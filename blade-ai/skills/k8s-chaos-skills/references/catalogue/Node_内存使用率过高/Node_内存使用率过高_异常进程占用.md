**用例名称** 异常进程占用 导致 Node_内存使用率过高

**故障现象**：
1. 节点内存使用率持续超过注入的目标百分比
2. 节点上 Pod 出现 OOMKilled 或被驱逐
3. 节点 Status 出现 MemoryPressure 条件为 True

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认监控系统（如 Prometheus）已配置，可观测节点内存指标

**演练步骤**：
1. 定位运行应用 A 的节点
2. 使用 chaosblade 对该节点注入内存压力，模拟异常进程占用节点内存
3. 观察节点内存使用率及 Pod 状态变化

**注入命令**：
```bash
blade create k8s node-mem load --mode ram --mem-percent <percent> --names <节点名> --kubeconfig <path> --timeout <duration>
```
> **必须使用 `--mode ram`**。默认的 cache 模式在 cgroup v2 节点上不会增加实际物理内存占用（仅填充页缓存），kubectl top 观测不到变化。`--mode ram` 通过分配匿名内存直接占用物理 RAM。

**注入验证**：
1. `kubectl top node <node-name>` 确认节点内存使用率持续超过注入的目标百分比
2. 执行 `kubectl describe node <节点名>`，确认 MemoryPressure 条件为 True
3. 确认应用 A 的 Pod 出现 OOMKilled 或被驱逐

**注入恢复**：
1. 销毁 chaosblade 实验

**恢复验证**：
1. `kubectl top node <node-name>` 确认内存使用率恢复到正常水平
2. 执行 `kubectl describe node <节点名>`，确认 MemoryPressure 条件为 False
3. 确认应用 A 的 Pod 恢复正常运行

**基准事实**：
- **根因**：节点上存在异常进程大量占用内存，导致节点内存使用率过高，触发 MemoryPressure，Pod 被 OOMKilled 或驱逐
- **必现现象**：节点内存使用率持续超过注入的目标百分比；MemoryPressure 条件为 True；Pod 出现 OOMKilled 或被驱逐

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效故障注入。

前提条件：集群需支持 `kubectl debug node` 功能（K8s 1.18+）；选择当前集群已验证可拉取且**包含 `stress-ng`** 的镜像（如 `ghcr.io/colinianking/stress-ng`）；切勿使用不含 stress-ng 的 alpine/busybox 基础镜像（会报 `stress-ng: not found`）。

注入命令（`--vm-bytes` 必须按**增量**计算，不能拍脑袋给绝对值）：
```bash
# 1) 先测节点内存基线
kubectl top node <node-name>

# 2) 分配量 = 节点总内存 × 目标百分比 − 当前用量
#    例：节点 16Gi、当前已用 6Gi、目标 80% → 16×0.8 − 6 ≈ 6.8G

# 3) 使用 kubectl debug node 注入内存压力（非交互；镜像须含 stress-ng）
kubectl debug node/<node-name> --profile=sysadmin --image=<stress-ng-image> -- sh -c 'stress-ng --vm 1 --vm-bytes <算出的分配量> --timeout <duration>s'
# 示例：分配 6.8G，持续 600 秒
kubectl debug node/<node-name> --profile=sysadmin --image=<stress-ng-image> -- sh -c 'stress-ng --vm 1 --vm-bytes 6.8G --timeout 600s'
```

恢复命令：
```bash
# 退出 debug Pod（Ctrl+D），或直接删除 debug Pod：
kubectl delete pod <debug-pod-name> --force --grace-period=0
# stress-ng 会在 --timeout 到期后自动释放内存
```

注意事项：
- 必须使用 `--vm-bytes` 指定具体内存大小，而非百分比；大小按**增量**算（分配量 = 节点总内存 × 目标百分比 − 当前用量），按绝对值分配会超量触发 OOM killer
- debug 命令客户端会阻塞到 --timeout 到期或断连，但 debug Pod 服务端持续运行，客户端超时不代表注入失败（--timeout 到期后自动释放=自动恢复）
- 与 ChaosBlade `--mode ram` 相比，stress-ng 默认会不断分配/释放内存（malloc/free 循环），效果等价
- debug Pod 在节点 MemoryPressure 时可能被 OOM killer 终止，这本身就是预期行为
