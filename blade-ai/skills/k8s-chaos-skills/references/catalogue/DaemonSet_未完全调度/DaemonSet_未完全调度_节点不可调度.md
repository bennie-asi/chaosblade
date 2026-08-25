**用例名称** 节点不可调度 导致 DaemonSet_未完全调度

**故障现象**：
1. DaemonSet 的 Ready 副本数小于期望副本数
2. 部分节点上没有运行 DaemonSet Pod
3. `kubectl get daemonset` 显示 DESIRED 与 READY 数量不一致

**资源准备**：
1. 确认 DaemonSet 应用 A 已正常运行，且所有节点均有副本
2. 确认监控系统可观测 DaemonSet 副本状态

**演练步骤**：
1. 选取一个运行 DaemonSet Pod 的节点
2. **先武装定时自恢复，再注入**（确认节点基线状态后武装定时器。必须用宿主机 systemd
   transient timer 武装（kubectl debug node + chroot /host + systemd-run）：`( sleep …; … ) &`
   后台子 shell 形态在 exec-form 通道不被解释，也不在 agent 守卫的载荷放行形态内；
   systemd-run 创建的 transient timer 由宿主机 systemd(PID 1) 管理，不依赖 debug Pod 存活。
   **timer 载荷只含 uncordon**——载荷 kubectl 以宿主机 kubelet.conf 为凭证，该凭证允许修改
   spec.unschedulable（uncordon 实测放行）但受 NodeRestriction 限制**不能修改 taints**，
   taint- 载荷到期只会得到 Forbidden、污点残留；污点摘除由 Agent 在演练结束时主动兜底）：
   ```bash
   # 基线确认：记录节点当前 unschedulable/taints 状态（恢复判据）
   kubectl get node <node> -o jsonpath='{.spec.unschedulable} {.spec.taints}'
   # 武装定时自恢复（宿主机 systemd timer；载荷仅 uncordon——taint- 受凭证限制不可自恢复）
   kubectl debug node/<node> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
     'systemd-run --on-active=<duration>s --unit=blade-restore-ds sh -c "kubectl --kubeconfig=/etc/kubernetes/kubelet.conf uncordon <node>"'
   ```
3. 使用 kubectl 将该节点标记为不可调度（cordon）：`kubectl cordon <node>`
4. 给该节点添加一个 DaemonSet 未配置容忍的自定义污点：`kubectl taint nodes <node> node.ops/maintenance=true:NoSchedule`
5. 删除该节点上的 DaemonSet Pod，观察 Pod 是否被重建
6. 观察 DaemonSet 副本数变化

**注入验证**：
1. 执行 `kubectl get nodes`，确认目标节点标记为 SchedulingDisabled
2. 执行 `kubectl describe node <node>`，确认自定义污点 `node.ops/maintenance=true:NoSchedule` 存在
3. 执行 `kubectl get daemonset`，确认 DESIRED 与 READY 数量不一致
4. 确认目标节点上的 DaemonSet Pod 删除后无法被重建

**注入恢复**：
1. 等待 `<duration>` 到期，systemd-run 定时器自动恢复节点可调度（**污点不会随 timer 摘除**
   ——kubelet.conf 凭证不能修改 taints，实测 Forbidden；污点残留期间 DaemonSet Pod 仍无法
   调度到该节点）。演练结束时由 Agent 主动执行同组恢复命令（幂等，定时器迟到再执行一次
   无副作用；两条命令独立执行），并停掉定时器避免迟到重放：
   ```bash
   kubectl uncordon <node>
   kubectl taint nodes <node> node.ops/maintenance=true:NoSchedule-
   kubectl debug node/<node> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host systemctl stop blade-restore-ds.timer
   ```
2. 等待 DaemonSet Pod 在该节点重建

**恢复验证**：
1. 执行 `kubectl get nodes`，确认目标节点恢复为可调度状态
2. 执行 `kubectl describe node <node>`，确认自定义污点已被移除
3. 执行 `kubectl get daemonset`，确认 DESIRED 与 READY 数量一致
4. 确认目标节点上 DaemonSet Pod 已正常运行

**注意事项**：
- Kubernetes ≥ 1.12 的 DaemonSet 默认容忍 `node.kubernetes.io/unschedulable` 污点，因此 `kubectl cordon` 不会阻止 DaemonSet Pod 调度
- 必须添加自定义污点（DaemonSet 未配置容忍的），才能有效阻止 Pod 调度
- 恢复时需一并移除自定义污点，否则 Pod 仍无法调度

**基准事实**：
- **根因**：节点被标记为不可调度（cordon）且存在 DaemonSet 未容忍的自定义污点，导致 DaemonSet 无法在该节点创建 Pod，副本数小于期望数
- **必现现象**：DaemonSet DESIRED 与 READY 不一致；节点 SchedulingDisabled；节点存在自定义污点；节点上缺少 DaemonSet Pod
