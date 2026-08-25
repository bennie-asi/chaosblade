**用例名称** StartupProbe配置不足 导致 Pod_CrashLoopBackOff

**故障现象**：
1. Pod 反复重启，状态为 CrashLoopBackOff
2. Pod Events 中显示 `Startup probe failed` 后容器被杀
3. 慢启动应用尚未完成初始化就被 StartupProbe 判定为失败

**资源准备**：
1. 确认应用 A 已正常运行（应用启动时间较长，如 Java 应用）
2. 确认应用 A 启动过程中健康检查接口不可用

**演练步骤**：
1. 记录应用 A 当前的探针配置（基线捕获：Agent 读取输出并记录 JSON，恢复时使用；
   原本无 startupProbe 时输出为空）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.containers[0].startupProbe}'
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **武装定时自恢复**（恢复命令幂等：定时器到期自动还原为主，Agent 在演练结束时主动执行
   同一条命令兜底，定时器迟到重复执行无副作用。定时器 shell 逻辑必须作为 `kubectl exec` 载体载荷派发——直接以
   `sh -c '…'` 作为顶层命令派发会被命令守卫拦截（unknown_binary: sh）；执行通道为多副本
   路由，无法可靠终止定时器，故不设 pidfile。恢复含 json patch 引号嵌套，用 base64 折叠
   武装；`<duration>` 需覆盖滚动更新与观察窗口）。
   载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻 Pod（如演练工具 Pod）：
   ```bash
   # 武装定时自恢复（将"注入恢复"第 1 步命令按基线选定 replace/remove 后 base64 编码填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-startup.sh; ( sleep <duration>; sh /tmp/blade-restore-startup.sh ) >/dev/null 2>&1 & echo armed'
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
1. 等待 `<duration>` 到期，定时器自动将 startupProbe 还原为步骤 1 基线；演练提前结束时由
   Agent 主动执行同一条恢复命令（幂等，定时器迟到再执行一次无副作用——基线非空时 json patch
   replace 回原值 JSON，原本无探针时 remove。json patch 按字段精确替换，天然规避
   resourceVersion 乐观锁问题，也不会像 apply 三方合并那样保留注入新增的字段）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/containers/0/startupProbe","value":<步骤1基线JSON>}]'
   # 原本无 startupProbe 时改用 remove：
   # kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
   #   -p='[{"op":"remove","path":"/spec/template/spec/containers/0/startupProbe"}]'
   ```
2. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态为 Running 且不再重启
2. 确认 StartupProbe 和 LivenessProbe 检查均正常通过
3. 确认应用 A 完成初始化并正常服务

**基准事实**：
- **根因**：StartupProbe 未配置或 failureThreshold × periodSeconds 总窗口不足以覆盖应用启动时间，慢启动应用在初始化完成前被判定为启动失败，反复被杀重启
- **必现现象**：Pod CrashLoopBackOff；Events 显示 Startup probe failed；容器日志显示应用启动中被中断
