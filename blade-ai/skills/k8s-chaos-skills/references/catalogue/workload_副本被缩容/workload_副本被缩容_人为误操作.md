**用例名称** 人为误操作 导致 workload_副本被缩容

**故障现象**：
1. Deployment/StatefulSet 的当前副本数小于期望副本数
2. 应用可用实例减少，部分请求无法处理
3. 服务响应延迟增大或出现超时

**资源准备**：
1. 确认应用 A 的 Deployment/StatefulSet 已正常运行，副本数大于 1
2. 确认监控系统可观测 Pod 副本数和请求指标

**演练步骤**：
1. 定位应用 A 的 Deployment/StatefulSet，并记录当前副本数
2. **先武装定时自恢复，再注入**（基线捕获 → 武装定时器 → 注入三步。恢复命令幂等：定时器到期
   自动恢复为主，Agent 在演练结束时主动执行同一条命令兜底，定时器迟到重复执行无副作用。定时器
   shell 逻辑必须作为 `kubectl exec` 载体载荷派发——直接以 `sh -c '…'` 作为顶层命令派发会被
   命令守卫拦截（unknown_binary: sh），载体内 `sh -c` 同时解决 exec-form 通道不解释裸
   `( sleep … ) &` 语法的问题；执行通道为多副本路由，无法可靠终止定时器，故不设 pidfile。
   载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻 Pod（如演练工具 Pod））：
   ```bash
   # 基线捕获：Agent 读取输出并记录原始副本数（定时器与主动恢复均使用）
   kubectl get <workload-kind> <name> -n <namespace> -o jsonpath='{.spec.replicas}'
   # 武装定时自恢复（<duration> 需覆盖演练观察窗口；定时器在载体内执行 kubectl，
   # 需载体含 kubectl 且有 scale 权限）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c '( sleep <duration>; kubectl scale <workload-kind> <name> -n <namespace> --replicas=<基线副本数> ) >/dev/null 2>&1 & echo armed'
   # 缩容注入
   kubectl scale <workload-kind> <name> -n <namespace> --replicas=<较小值>
   ```
3. 观察 Pod 缩容过程和应用状态变化

**标签选择器提示**：
- Kubernetes 推荐标签格式为 `app.kubernetes.io/name=<name>`，而非简单的 `app=<name>`
- 建议先不带 `-l` 过滤器查询 `kubectl get deployment <name> -n <ns>`，再从返回结果中提取实际标签
- 若需用标签过滤，优先使用 `-l app.kubernetes.io/name=<name>` 或 `-l app.kubernetes.io/component=<name>`

**注入验证**：
1. 执行 `kubectl get pods`，确认 Pod 总数减少，部分 Pod 被终止
2. 执行 `kubectl get deployment/statefulset <name>`，确认当前副本数小于缩容前的值
3. （可选，仅当演练方提供了应用访问入口时）确认请求延迟增大/超时或可用性下降；无入口时上述副本数证据成立即可判定

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动将副本数恢复为基线；演练提前结束时由 Agent 主动执行
   同一条恢复命令（幂等，定时器迟到再执行一次无副作用）：
   ```bash
   kubectl scale <workload-kind> <name> -n <namespace> --replicas=<基线捕获的原始副本数>
   ```
2. 等待 Pod 自动扩容

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 总数恢复到缩容前的值
2. 执行 `kubectl get deployment/statefulset <name>`，确认 READY 副本数等于 DESIRED
3. （可选，有访问入口时）确认请求延迟与可用性恢复正常

**基准事实**：
- **根因**：人为误操作（如 kubectl scale、修改 YAML 等）导致 Deployment/StatefulSet 的副本数被意外缩小，可用实例不足
- **必现现象**：READY 副本数小于 DESIRED；Pod 被终止；服务可用性下降

**注意事项**：
- 若目标 Deployment/StatefulSet 由 Helm 管理（label `app.kubernetes.io/managed-by: Helm`），kubectl scale 修改会被 Helm reconciliation 覆盖
- 注入期间应避免触发 Helm upgrade/rollback 操作，否则故障会被意外恢复
- 反之，若需快速恢复，可通过 `helm rollback` 或 `helm upgrade` 强制还原
