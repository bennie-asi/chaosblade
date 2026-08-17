**用例名称** kube-proxy异常 导致 Service_调用失败

**故障现象**：
1. 通过 ClusterIP/NodePort 访问 Service 失败，连接超时
2. 节点上 iptables/ipvs 规则未更新或被清空
3. kube-proxy Pod 异常，无法维护 Service 转发规则

**资源准备**：
1. 确认应用 A 已正常运行，对外暴露 Service
2. 确认 kube-proxy DaemonSet 正常运行
3. 确认监控系统可观测 Service 请求指标

**演练步骤**：
1. 记录 kube-proxy DaemonSet 当前状态
2. 选择目标节点，删除该节点上的 kube-proxy Pod 并临时阻止重建（通过 cordon 节点或修改 DaemonSet nodeSelector）：
   ```bash
   # 方式A：给目标节点添加标签排除 kube-proxy 调度
   kubectl label node <目标节点> net.ops/proxy-degraded=true
   kubectl patch ds kube-proxy -n kube-system \
     -p '{"spec":{"template":{"spec":{"affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[{"matchExpressions":[{"key":"net.ops/proxy-degraded","operator":"DoesNotExist"}]}]}}}}}}}'
   ```
   或者直接使用 chaosblade 杀掉 kube-proxy 进程：
   ```bash
   blade create k8s node-process kill \
     --names <目标节点> \
     --process kube-proxy \
     --timeout <duration> \
     --kubeconfig <路径>
   ```
3. 在目标节点上的 Pod 内通过 ClusterIP 访问 Service
4. 观察 Service 访问结果

**注入验证**：
1. 确认目标节点上 kube-proxy 进程不存在或 Pod 处于异常状态
2. 在目标节点的 Pod 内访问 Service ClusterIP，确认连接超时或失败
3. 检查节点 iptables/ipvs 规则，确认 Service 相关转发规则缺失或过期

**注入恢复**：
1. 若使用标签方式：移除节点标签并恢复 DaemonSet 配置（若原 DaemonSet 本就有 affinity，
   须用注入前记录的原值还原，而非直接 remove）：
   ```bash
   kubectl label node <目标节点> net.ops/proxy-degraded-
   kubectl patch ds kube-proxy -n kube-system --type=json \
     -p='[{"op":"remove","path":"/spec/template/spec/affinity"}]'
   ```
2. 若使用 chaosblade：等待实验超时或执行 `blade destroy <UID>`
3. 等待 kube-proxy Pod 在目标节点重建

**恢复验证**：
1. 确认目标节点上 kube-proxy Pod 恢复 Running 且 Ready
2. 在目标节点的 Pod 内重新访问 Service，确认恢复正常
3. 检查 iptables/ipvs 规则已重新同步

**基准事实**：
- **根因**：kube-proxy Pod 异常或进程被杀，无法维护节点上的 iptables/ipvs 转发规则，导致 Service ClusterIP 流量无法被正确转发
- **必现现象**：Service ClusterIP 访问超时；kube-proxy 不可用；节点 iptables/ipvs 规则缺失

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效 kube-proxy 异常。

前提条件：具备修改 kube-proxy DaemonSet 的权限

注入命令（**先武装定时恢复，再注入**）：
```bash
# 方式A：通过标签排除目标节点上的 kube-proxy 调度。
# 先取证原 affinity（可能为空），武装定时还原按基线分支：为空则 remove，非空则还原原值——
# 无条件 remove 会在原 DaemonSet 本就有 affinity 时把它删丢，属于错误恢复
ORIG_AFFINITY=$(kubectl get ds kube-proxy -n kube-system -o jsonpath='{.spec.template.spec.affinity}')
( sleep <duration>; kubectl label node <目标节点> net.ops/proxy-degraded-; \
  if [ -z "$ORIG_AFFINITY" ]; then \
    kubectl patch ds kube-proxy -n kube-system --type=json \
      -p='[{"op":"remove","path":"/spec/template/spec/affinity"}]'; \
  else \
    kubectl patch ds kube-proxy -n kube-system --type=json \
      -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/affinity\",\"value\":$ORIG_AFFINITY}]"; \
  fi ) >/dev/null 2>&1 &
echo $! > /tmp/blade-restore-proxy.pid
# 再注入
kubectl label node <目标节点> net.ops/proxy-degraded=true
kubectl patch ds kube-proxy -n kube-system \
  -p '{"spec":{"template":{"spec":{"affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[{"matchExpressions":[{"key":"net.ops/proxy-degraded","operator":"DoesNotExist"}]}]}}}}}}}'
# 方式B：通过 kubectl debug node 挂起 kube-proxy 进程。
# 先武装定时 CONT（timer 由宿主机 systemd(PID 1) 管理），再 STOP；`&&` 串联保证武装失败不冻结
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'systemd-run --on-active=<recovery-seconds>s --unit=blade-restore-proxy sh -c "kill -CONT \$(pidof kube-proxy)" &&
   kill -STOP $(pidof kube-proxy)'
```

恢复命令（timer 到期前可提前手动恢复）：
```bash
# 方式A：终止定时器，移除标签并按注入前取证的基线还原 affinity
kill $(cat /tmp/blade-restore-proxy.pid) 2>/dev/null; rm -f /tmp/blade-restore-proxy.pid
kubectl label node <目标节点> net.ops/proxy-degraded-
# 基线为空 → remove；基线非空 → 用注入前记录的 $ORIG_AFFINITY 原值 replace（与武装块同构）
kubectl patch ds kube-proxy -n kube-system --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/affinity"}]'
# 方式B：停掉 timer，恢复 kube-proxy 进程
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'systemctl stop blade-restore-proxy 2>/dev/null; kill -CONT $(pidof kube-proxy)'
```

注意事项：
- 方式A 效果更彻底（kube-proxy Pod 被完全移除），但修改了 DaemonSet 配置，需注意恢复
- 方式B 更轻量，但 kube-proxy 被 systemd 或 kubelet 管理时可能自动重启
- 自恢复机制：方式A 依赖客户端武装的后台定时器（到期去标签+按基线分支还原 affinity），
  方式B 依赖宿主机 systemd-run transient timer（到期自动 SIGCONT）；方式A 的还原已在武装块
  内按注入前取证的 $ORIG_AFFINITY 分支处理（为空 remove、非空 replace 原值），不会删丢
  原有 affinity
