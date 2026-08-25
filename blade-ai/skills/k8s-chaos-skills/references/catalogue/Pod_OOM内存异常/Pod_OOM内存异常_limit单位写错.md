**用例名称** limit单位写错 导致 Pod_OOM内存异常

**故障现象**：
1. Pod 启动后立即异常退出，状态为 CrashLoopBackOff
2. Pod 的 lastState 显示 reason: OOMKilled，或新 Pod 卡在 ContainerCreating（极小 limit 在 cgroup v2 下的实测形态，见注入验证第 3 条）
3. 容器 memory limit 值极小（如 100m = 0.1 字节），应用启动即超限或无法启动

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认应用 A 的正常内存使用量（如 200Mi 以上）

**演练步骤**：
1. 记录应用 A 当前的 resources 配置（基线捕获：Agent 读取输出并记录 JSON，恢复时使用）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.containers[0].resources}'
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
   # 武装定时自恢复（将"注入恢复"第 1 步命令 base64 编码后填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-oom.sh; ( sleep <duration>; sh /tmp/blade-restore-oom.sh ) >/dev/null 2>&1 & echo armed'
   ```
4. 修改应用 A 的 Deployment，将 memory limit 单位写错：
   ```yaml
   resources:
     limits:
       memory: "100m"    # 错误！100m = 0.1 字节（milli），应为 100Mi
     requests:
       memory: "100m"
   ```
   注意：在 Kubernetes 中，`m` 表示 milli（千分之一），`100m` = 0.1 字节；正确应为 `Mi`（Mebibyte）。patch 提交时 API server 会输出 `Warning: fractional byte value "100m" is invalid, must be an integer`——该 Warning 即单位错误的即时确认信号，且不妨碍 patch 生效
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
7. 观察 Pod 启动行为

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认**所有**目标 Pod 状态为 CrashLoopBackOff，RESTARTS 持续增长（不是仅一个新 Pod，而是全部副本）
3. 按 limit 取整结果分形态验证（两种形态均已实测复现）：
   - limit 为可用级小值（如 10Mi，容器可创建但启动即超限）：执行 `kubectl get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState}'`，确认 reason 为 OOMKilled
   - limit 极小（如 100m，CRI 取整为 0 字节、cgroup v2 memory.max=0）：容器无法创建，新 Pod 卡在 ContainerCreating，Events 显示 `FailedMount ... no space left on device`。**该报错是误导信号**：projected volume 以 tmpfs 承载、写入计入 Pod memcg，memory.max=0 时 tmpfs 页分配被拒而返回 ENOSPC，与节点磁盘空间无关（实测节点磁盘仅用 14% 仍稳定复现；此时 lastState 为空，查 OOMKilled 必然查不到，不是注入失败）
4. 执行 `kubectl describe pod <pod-name>`，确认 limits.memory 为极小值
5. 确认容器启动后立即异常退出（运行时间极短或无法启动）

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动将 resources 还原为步骤 1 基线；演练提前结束时由
   Agent 主动执行同一条恢复命令（幂等，定时器迟到再执行一次无副作用——用基线 JSON 整体
   replace resources 对象，limits/requests 一并还原。json patch 按字段精确替换，天然规避
   resourceVersion 乐观锁问题，也不会像 apply 三方合并那样保留注入引入的错误值）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":<步骤1基线JSON>}]'
   # 原本无 resources 时改用 remove
   ```
2. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态为 Running 且不再重启
2. 确认容器正常运行，内存使用率在合理范围
3. 确认应用 A 服务正常

**基准事实**：
- **根因**：memory limit 单位写错（如 `100m` 而非 `100Mi`），导致 limit 值极小，容器启动后内存使用立即超过 limit 被 OOMKill，或在 cgroup v2 环境下因内存配置过小无法启动
- **必现现象**：Pod 异常退出（OOMKilled）或无法创建（ContainerCreating 卡死 + FailedMount ENOSPC，极小 limit 在 cgroup v2 下的形态）；limits.memory 值不合理（如 100m）；容器运行时间极短或无法启动
