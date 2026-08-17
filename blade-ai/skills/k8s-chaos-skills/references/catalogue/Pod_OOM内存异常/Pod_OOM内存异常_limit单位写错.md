**用例名称** limit单位写错 导致 Pod_OOM内存异常

**故障现象**：
1. Pod 启动后立即异常退出，状态为 CrashLoopBackOff
2. Pod 的 lastState 显示 reason: OOMKilled 或 StartError（cgroup v2 环境下容器可能因内存配置过小无法启动）
3. 容器 memory limit 值极小（如 100m = 0.1 字节），应用启动即超限或无法启动

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认应用 A 的正常内存使用量（如 200Mi 以上）

**演练步骤**：
1. 记录应用 A 当前的 resources.limits.memory 配置，并导出还原基线（**必须剥离 metadata 中的
   resourceVersion/uid/creationTimestamp/generation 与整个 status**——实证结论：带
   resourceVersion 的 `kubectl get -o yaml` 原样输出，无论 apply 还是 replace 都会因乐观锁
   Conflict 报错，武装还原永不生效）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> -o json | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   m=d['metadata']
   for k in ('resourceVersion','uid','creationTimestamp','generation','managedFields'): m.pop(k,None)
   d.pop('status',None); json.dump(d,open('/tmp/blade-memlimit-baseline.json','w'))"
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **先武装定时恢复，再修改 limit**（在运行 kubectl 的机器上后台武装，到期自动用基线整体替换还原
   memory limit，补齐自恢复能力；**必须用 `kubectl replace` 而非 `kubectl apply`**——实证结论：
   带 resourceVersion 的基线 apply 会 Conflict 报错，剥离后 apply 三方合并虽成功但对新增字段
   还原不彻底；replace 为 PUT 整体替换，实测在 live 被多次修改后依然精确还原；duration 需覆盖
   滚动更新耗时；PID 落盘供提前恢复时终止定时器）：
   ```bash
   ( sleep <duration>; kubectl replace -f /tmp/blade-memlimit-baseline.json ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-memlimit.pid
   ```
4. 修改应用 A 的 Deployment，将 memory limit 单位写错：
   ```yaml
   resources:
     limits:
       memory: "100m"    # 错误！100m = 0.1 字节（milli），应为 100Mi
     requests:
       memory: "100m"
   ```
   注意：在 Kubernetes 中，`m` 表示 milli（千分之一），`100m` = 0.1 字节；正确应为 `Mi`（Mebibyte）
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
7. 观察 Pod 启动行为

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认**所有**目标 Pod 状态为 CrashLoopBackOff，RESTARTS 持续增长（不是仅一个新 Pod，而是全部副本）
3. 执行 `kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState}'`，确认 reason 为 OOMKilled 或 StartError（cgroup v2 环境下极小 limit 可能导致容器无法启动而非 OOMKill）
4. 执行 `kubectl describe pod <pod-name>`，确认 limits.memory 为极小值
5. 确认容器启动后立即异常退出（运行时间极短或无法启动）

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动用基线整体替换还原 memory limit；如需提前恢复，先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-memlimit.pid) 2>/dev/null; rm -f /tmp/blade-restore-memlimit.pid
   ```
2. 恢复应用 A 的 Deployment，将 memory limit 修正为正确单位（如 `256Mi`）
3. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态为 Running 且不再重启
2. 确认容器正常运行，内存使用率在合理范围
3. 确认应用 A 服务正常

**基准事实**：
- **根因**：memory limit 单位写错（如 `100m` 而非 `100Mi`），导致 limit 值极小，容器启动后内存使用立即超过 limit 被 OOMKill，或在 cgroup v2 环境下因内存配置过小无法启动
- **必现现象**：Pod 异常退出（OOMKilled 或 StartError）；CrashLoopBackOff；limits.memory 值不合理（如 100m）；容器运行时间极短或无法启动
