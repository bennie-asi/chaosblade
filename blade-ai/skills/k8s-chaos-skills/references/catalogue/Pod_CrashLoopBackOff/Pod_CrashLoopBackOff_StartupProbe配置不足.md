**用例名称** StartupProbe配置不足 导致 Pod_CrashLoopBackOff

**故障现象**：
1. Pod 反复重启，状态为 CrashLoopBackOff
2. Pod Events 中显示 `Startup probe failed` 后容器被杀
3. 慢启动应用尚未完成初始化就被 StartupProbe 判定为失败

**资源准备**：
1. 确认应用 A 已正常运行（应用启动时间较长，如 Java 应用）
2. 确认应用 A 启动过程中健康检查接口不可用

**演练步骤**：
1. 记录应用 A 当前的探针配置，并导出还原基线（**必须剥离 metadata 中的
   resourceVersion/uid/creationTimestamp/generation 与整个 status**——实证结论：带
   resourceVersion 的 `kubectl get -o yaml` 原样输出，无论 apply 还是 replace 都会因乐观锁
   Conflict 报错，武装还原永不生效）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> -o json | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   m=d['metadata']
   for k in ('resourceVersion','uid','creationTimestamp','generation','managedFields'): m.pop(k,None)
   d.pop('status',None); json.dump(d,open('/tmp/blade-startup-baseline.json','w'))"
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **先武装定时恢复，再修改探针**（在运行 kubectl 的机器上后台武装，到期自动用基线整体替换还原
   startupProbe，补齐自恢复能力；**必须用 `kubectl replace` 而非 `kubectl apply`**——实证结论：
   apply 的三方合并会保留注入后新增的字段（还原不彻底）；replace 为 PUT 整体替换，实测在 live
   被多次修改后依然精确还原；duration 需覆盖滚动更新耗时；PID 落盘供提前恢复时终止定时器）：
   ```bash
   ( sleep <duration>; kubectl replace -f /tmp/blade-startup-baseline.json ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-startup.pid
   ```
4. 修改应用 A 的 Deployment，添加或修改 startupProbe 使其窗口不足以覆盖应用启动时间：
   ```yaml
   startupProbe:
     httpGet:
       path: <应用实际健康检查路径>   # 占位符：必须按目标应用实际探针配置替换，/healthz 仅为示例写法
       port: <应用实际健康检查端口>   # 占位符：必须按目标应用实际探针配置替换，8080 仅为示例写法
     failureThreshold: 3
     periodSeconds: 5
   ```
   （总等待时间 = failureThreshold × periodSeconds = 15 秒，远小于应用实际启动时间。窗口参数为示例默认值，可按应用启动时长调整；**path/port 必须取自目标应用的真实探针配置，探针本身应指向可达端点，直接照抄示例值会把故障变成端口错配，偏离用例语义**）
5. 同时确保 livenessProbe 存在，使得 startupProbe 失败后触发容器重启
6. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
7. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
8. 观察 Pod 启动行为

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认**所有**目标 Pod 的 RESTARTS 持续增长，状态为 CrashLoopBackOff（不是仅一个新 Pod，而是全部副本）
3. 执行 `kubectl describe pod <pod-name>`，确认 Events 显示 `Startup probe failed`
4. 查看容器日志，确认应用正在启动但未完成初始化就被杀

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动用基线整体替换还原 startupProbe；如需提前恢复，先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-startup.pid) 2>/dev/null; rm -f /tmp/blade-restore-startup.pid
   ```
2. 恢复应用 A 的 Deployment，增大 startupProbe 的窗口时间（若原本无 startupProbe，则移除）
3. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态为 Running 且不再重启
2. 确认 StartupProbe 和 LivenessProbe 检查均正常通过
3. 确认应用 A 完成初始化并正常服务

**基准事实**：
- **根因**：StartupProbe 未配置或 failureThreshold × periodSeconds 总窗口不足以覆盖应用启动时间，慢启动应用在初始化完成前被判定为启动失败，反复被杀重启
- **必现现象**：Pod CrashLoopBackOff；Events 显示 Startup probe failed；容器日志显示应用启动中被中断
