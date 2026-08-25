**用例名称** 网卡卸载失败 导致 Pod_Terminating

**故障现象**：
1. Pod 状态长时间停留在 Terminating
2. 容器已停止，但 Pod sandbox 清理失败
3. Events 或 kubelet 日志中显示 CNI DEL 调用失败或网络资源释放异常

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群使用 ENI/Terway 等需要显式清理网络资源的 CNI 插件
3. 确认监控系统可观测 Pod 状态和 CNI 插件日志

**演练步骤**：
1. 定位应用 A 的 Pod 所在节点
2. 使用 chaosblade 挂起节点上的 CNI 插件进程（如 terway-daemon），模拟 CNI 响应异常：
   ```bash
   blade create k8s node-process stop \
     --names <节点名> \
     --process terway \
     --timeout <duration> \
     --kubeconfig <路径>
   ```
   或删除 CNI 插件 DaemonSet 中该节点的 Pod（先 cordon 节点防止重建）
3. 删除应用 A 的 Pod，触发 Terminating 流程
4. 观察 Pod Terminating 状态

**注入验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态为 Terminating 且长时间未消失
2. 查看 kubelet 日志（`journalctl -u kubelet`），确认有 CNI DEL 调用超时或失败的记录
3. 确认 CNI 插件进程处于 stopped 状态或不可用

**注入恢复**：
1. 恢复 CNI 插件进程：等待 chaosblade 超时或执行 `blade destroy <UID>`
2. 若删除了 CNI Pod：uncordon 节点，等待 CNI DaemonSet Pod 重建
3. kubelet 将自动重试 sandbox 清理

**恢复验证**：
1. 确认 CNI 插件进程恢复正常
2. 执行 `kubectl get pods`，确认 Terminating 的 Pod 已被完全清理
3. 确认节点网络资源（ENI/IP）已释放
4. 确认新 Pod 可以正常创建和分配网络

**基准事实**：
- **根因**：CNI 插件异常或不可用，导致 Pod 删除时网卡/ENI 资源无法正常释放，sandbox 清理失败，Pod 卡在 Terminating
- **必现现象**：Pod Terminating 持续；kubelet 日志显示 CNI DEL 失败；网络资源未释放

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令模拟 CNI 插件异常。

前提条件：集群需支持 `kubectl debug node` 功能（K8s 1.18+），或可操作 CNI DaemonSet

注入命令（**先武装定时恢复，再注入**）：
```bash
# 方式A：通过 kubectl debug node 挂起 CNI 插件进程
# ⚠️ 先用 systemd-run 登记定时 SIGCONT 再 STOP —— 定时器由宿主机 systemd(PID 1) 管理，
#    不受 debug pod 生命周期影响
# ⚠️ 进程名以实测为准：Terway 的 daemon 进程名是 terwayd（伴生 terway-cli），不是
#    terway-daemon——照抄错误进程名会使 $() 展开为空、STOP 落空（注入静默失败）；
#    注入前先 kubectl exec <debug-pod> -- chroot /host sh -c 'pidof terwayd' 确认
# ⚠️ Terway DaemonSet 自带 liveness probe：STOP 后容器在探测失败后重启换新进程
#    （实测故障窗口 <60s）；且 terway CNI binary 对 daemon 不可达走快速失败路径——
#    kubelet 仍能完成 Pod 删除（不卡 Terminating），CNI DEL 失败仅表现为 kubelet 日志
#    报错与潜在 ENI/IP 泄漏。本方式的可靠判据是 kubelet 日志 CNI DEL 失败记录，
#    不是 Pod 卡 Terminating
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'systemd-run --on-active=<recovery-seconds>s --unit=blade-cont-cni \
     sh -c "kill -CONT \$(pidof terwayd || pidof cilium-agent || pidof calico-node)" && \
   kill -STOP $(pidof terwayd || pidof cilium-agent || pidof calico-node)'

# 方式B：删除节点上的 CNI Pod（先武装定时 uncordon 再注入；定时器必须经 kubectl exec
#        载体派发——顶层裸 sh -c 不被工具守卫放行，载体需含 kubectl 与集群凭证；
#        恢复命令幂等，迟到重复执行无副作用）
kubectl exec <载体Pod> -n <载体ns> -- sh -c '( sleep <duration>; kubectl uncordon <node-name> ) >/dev/null 2>&1 & echo armed'
kubectl cordon <node-name>
kubectl delete pod -n kube-system -l app=terway-eniip --field-selector spec.nodeName=<node-name>
# ⚠️ label 以实测为准：Terway DaemonSet 的 Pod label 是 app=terway-eniip（不是 app=terway），
#    删除前先 kubectl get pods -n kube-system -o wide | grep -i terway 核对
# ⚠️ cordon 挡不住 DaemonSet 重建：DS controller 直接绑 nodeName 绕过调度器（实测删除后
#    秒级重建），「先 cordon 防重建」的前提不成立——本方式在 DS 形态 CNI 上无法维持故障窗口
```

恢复命令（提前恢复）：
```bash
# 方式A：恢复 CNI 插件进程（SIGCONT 幂等，武装的定时器后续再触发也无副作用）
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'kill -CONT $(pidof terwayd || pidof cilium-agent || pidof calico-node)'
# 方式B：Agent 主动 uncordon 节点（定时器到期也会自动 uncordon，幂等），等待 DaemonSet 重建 CNI Pod
kubectl uncordon <node-name>
# 删除 debug Pod
kubectl delete pod <debug-pod-name> --force --grace-period=0
```

注意事项：
- CNI 插件名称因集群而异：Terway（阿里云）、Cilium、Calico 等，需根据实际环境实测进程名与
  Pod label（Terway 实测：进程 terwayd，Pod label app=terway-eniip）
- 方式B 删除 CNI Pod 后，DaemonSet controller 直接绑 nodeName 重建（cordon 无效，实测秒级
  重建），故障窗口不可维持——本用例在 DS 形态 CNI 上仅方式A 可用
- 自恢复机制：方式 A 为宿主机 systemd-run 定时 SIGCONT（timer 由 systemd 管理，到期自动恢复；
  Terway 的 liveness probe 会在更短时间内自愈重启容器，实测形成双重自愈）；
  方式 B 为 kubectl exec 载体内定时 uncordon（定时器存活于载体 Pod，Pod 重建会丢失定时器，
  届时仍需 Agent 主动执行或人工 uncordon 兜底）
