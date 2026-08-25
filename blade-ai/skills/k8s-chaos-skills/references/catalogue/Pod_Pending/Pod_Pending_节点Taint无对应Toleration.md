**用例名称** 节点Taint无对应Toleration 导致 Pod_Pending

**故障现象**：
1. Pod 状态为 Pending，无法被调度到任何节点
2. Pod Events 中显示 `N node(s) had untolerated taint {node.ops/pending-reboot: true}`
3. 目标 Pod 可调度的所有节点均带有 Pod 无法容忍的污点

**RCA症状**：
1. Pod 状态为 Pending，无法被调度到任何节点
2. Pod Events 中显示 `had untolerated taint`
（以上为kubectl直接可观测的现象，不包含诊断结论）

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群中有多个可调度节点
3. 记录应用 A 的 Pod 当前运行在哪些节点上（这些节点即为"目标节点"）

**演练步骤**：

> **爆炸半径控制**：本用例仅 taint 目标 Pod 所在的节点（而非全部集群节点），
> 通过 nodeSelector 约束目标 Pod 只能调度到这些节点，从而在保证故障复现的同时
> 避免影响集群中其他工作负载的调度。

1. 记录目标节点的当前 taint 信息，以及 Deployment 的当前 nodeSelector 原值 JSON（用于恢复；
   无 nodeSelector 时该命令输出为空字符串）：
   ```bash
   kubectl get deployment <name> -n <ns> -o jsonpath='{.spec.template.spec.nodeSelector}'
   ```
2. **武装定时自恢复**（恢复命令幂等：定时器到期自动恢复为主，Agent 在演练结束时主动执行
   同组命令兜底，定时器迟到重复执行无副作用。定时器必须经 `kubectl exec` 载体派发——顶层
   裸 `sh -c '… & echo armed'` 不被工具守卫放行；载体 Pod 需含 kubectl 与集群凭证（如
   kubewiz-executor 或集群内工具 Pod，业务镜像多为极简镜像无 kubectl，不可作载体）。恢复含
   json patch 引号嵌套，用 base64 折叠武装——nodeSelector 还原形态按步骤 1 基线确定：为空
   用 remove，非空用 replace 基线原值）：
   ```bash
   # 武装定时自恢复（将"注入恢复"第 1 步三连命令整体 base64 编码后填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体ns> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-tainttol.sh; ( sleep <duration>; sh /tmp/blade-restore-tainttol.sh ) >/dev/null 2>&1 & echo armed'
   ```
3. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
4. 给目标节点添加标签：`kubectl label node <node> workload-affinity=<app-name>`（仅目标 Pod 所在节点）
5. 给应用 A 的 Deployment 添加 nodeSelector，约束 Pod 只能调度到目标节点：
   `kubectl patch deployment <name> -n <ns> -p '{"spec":{"template":{"spec":{"nodeSelector":{"workload-affinity":"<app-name>"}}}}}'`
6. 等待 rollout 完成（Pod 仍在原节点上运行，因为只有目标节点有此标签）
7. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
8. 给目标节点添加污点：`kubectl taint node <node> node.ops/pending-reboot=true:NoSchedule`（仅目标节点）
9. 删除应用 A 的一个 Pod，触发重建调度
10. 观察新 Pod 的调度状态

**注入验证**：
1. 执行 `kubectl get pods`，确认新 Pod 状态为 Pending
2. 执行 `kubectl describe pod <pod-name>`，确认 Events 中显示 untolerated taint 相关的调度失败原因。
   消息形态随 K8s 版本而异：老版本显示明细形态 `N node(s) had untolerated taint
   {node.ops/pending-reboot: true}`；K8s 1.35 实测为**合并形态**（与其他不满足条件合并为一行、
   不带 taint key 明细）：`0/8 nodes are available: 1 node(s) had untolerated taint(s),
   7 node(s) didn't match Pod's node affinity/selector`。判定要点是 `untolerated taint`
   关键字，不要依赖 taint key 明细
3. 确认目标节点均有 node.ops/pending-reboot taint

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动执行三连还原；演练提前结束时由 Agent 主动执行同组
   恢复命令（幂等，定时器迟到再执行一次无副作用；多条命令独立执行，多个目标节点时对每个
   节点各执行一遍污点与标签还原。nodeSelector 按步骤 1 基线还原——为空则整体移除、
   非空则用基线原值精确替换，避免无条件 remove 丢失原有键值）：
   ```bash
   # ① 摘除目标节点污点
   kubectl taint node <node> node.ops/pending-reboot=true:NoSchedule-
   # ② 还原 nodeSelector（基线为空时 remove；非空时 replace 为基线原值 JSON）
   kubectl patch deployment <name> -n <ns> --type='json' \
     -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'
   # ③ 移除目标节点标签
   kubectl label node <node> workload-affinity-
   ```
2. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态变为 Running
2. 确认目标节点 taint 已恢复到演练前状态
3. 确认 Deployment spec 已恢复到演练前状态

**基准事实**：
- **根因**：目标 Pod 可调度的所有节点被标记了 Taint，而 Pod 未配置对应的 Toleration，导致调度器无法找到合适节点
- **必现现象**：Pod Pending；Events 显示 untolerated taint；目标节点带有不可容忍的污点
