**用例名称** 节点排空Drain 导致 Node_维护

**故障现象**：
1. 节点被标记为 SchedulingDisabled，不再接受新 Pod 调度
2. 节点上所有非 DaemonSet Pod 被安全驱逐
3. 被驱逐 Pod 在其他节点重建；如集群资源不足，部分 Pod 进入 Pending
4. 模拟节点维护/升级场景下的工作负载迁移

**资源准备**：
1. 确认目标节点上有业务 Pod 运行（非仅 DaemonSet Pod）
2. 确认集群中其他节点有足够资源接纳被驱逐的 Pod
3. 确认目标节点名称（通过 `kubectl get nodes` 获取）

**演练步骤**：
1. 确认目标节点当前运行的 Pod：
   ```bash
   kubectl get pods --all-namespaces --field-selector spec.nodeName=<node-name> -o wide
   ```
2. **先武装定时自恢复，再排空**（定时器到期自动 uncordon 为主，Agent 在演练结束时主动执行
   同一条命令兜底，迟到重复执行无副作用。timer 必须以宿主机 systemd transient unit 武装
   （kubectl debug node + chroot /host + systemd-run）——裸 `( sleep … ) &` 后台子 shell
   形态在 exec-form 通道不被解释，也不在 agent 守卫的载荷放行形态内；systemd-run 创建的
   transient timer 由宿主机 systemd(PID 1) 管理，不依赖 debug Pod 存活（drain 驱逐 debug
   Pod 后恢复仍生效）。timer 载荷中的 kubectl 以宿主机 kubelet.conf 为凭证，该凭证允许修改
   spec.unschedulable（uncordon 实测放行；但受 NodeRestriction 限制不能修改 taints，见
   "节点污点注入Taint"用例）。`<duration>` 需覆盖 drain 与观察窗口）：
   ```bash
   # 武装定时自恢复（宿主机 systemd timer；先武装再 cordon/drain，保证闹钟先于故障上膛）
   kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
     'systemd-run --on-active=<duration>s --unit=blade-restore-drain sh -c "kubectl --kubeconfig=/etc/kubernetes/kubelet.conf uncordon <node-name>"'
   # 标记不可调度
   kubectl cordon <node-name>
   ```
3. 排空节点上所有 Pod（安全驱逐）：
   ```bash
   kubectl drain <node-name> \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --grace-period=30 \
     --timeout=120s
   ```
4. 观察被驱逐 Pod 的重建情况

**注入验证**：
1. 执行 `kubectl get nodes`，确认目标节点状态为 `Ready,SchedulingDisabled`
2. 执行 `kubectl get pods --field-selector spec.nodeName=<node-name> --all-namespaces`，确认仅剩 DaemonSet Pod
3. 执行 `kubectl get pods -n <namespace> -l <label-selector> -o wide`，确认业务 Pod 已迁移到其他节点
4. 检查是否有 Pod 因资源不足进入 Pending：
   ```bash
   kubectl get pods --all-namespaces --field-selector status.phase=Pending
   ```

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动恢复节点为可调度状态；演练提前结束时由 Agent 主动执行
   同一条恢复命令（幂等，定时器迟到再执行一次无副作用），并停掉已武装的 timer 避免迟到重放：
   ```bash
   kubectl uncordon <node-name>
   kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host systemctl stop blade-restore-drain.timer
   ```
2. 等待调度器将 Pending Pod（如有）重新调度

> ⚠️ `kubectl cordon/drain` 本身**没有自动恢复机制**，自恢复依赖注入前武装的宿主机 systemd
> transient timer（由 PID 1 管理，debug Pod 被驱逐/删除不影响；timer 载荷凭证为宿主机
> kubelet.conf——对 uncordon 实测放行）。Agent 主动 uncordon 兜底始终有效。被驱逐的 Pod
> 不会自动迁回本节点（uncordon 后仅恢复可调度性，新 Pod 与再平衡由调度器决定）。

**恢复验证**：
1. 执行 `kubectl get nodes`，确认目标节点状态恢复为 `Ready`（无 SchedulingDisabled）
2. 执行 `kubectl get pods --all-namespaces --field-selector status.phase=Pending`，确认无 Pending Pod
3. 确认业务 Pod 全部 Running 且 Ready

**基准事实**：
- **根因**：节点被 cordon + drain 标记为不可调度并驱逐所有工作负载，模拟节点维护场景下的 Pod 迁移行为
- **必现现象**：节点状态为 SchedulingDisabled；非 DaemonSet Pod 被驱逐并在其他节点重建；drain 命令输出 evicting/evicted 信息
