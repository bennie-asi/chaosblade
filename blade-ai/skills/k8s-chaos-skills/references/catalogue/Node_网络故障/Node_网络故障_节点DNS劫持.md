**用例名称** 节点DNS劫持 导致 Node_网络故障

**故障现象**：
1. 节点宿主机与 hostNetwork Pod 对特定域名的 DNS 解析被劫持到错误 IP
2. 多个应用同时出现对同一域名的连接异常
3. 与 Pod 级 DNS 劫持不同：作用点为宿主机 /etc/hosts；但非 hostNetwork Pod（绝大多数业务 Pod）的 /etc/hosts 由 kubelet 独立管理，宿主机劫持对其零传导（实测，见注意事项）
4. 模拟节点级 DNS 污染或中间人攻击场景

**资源准备**：
1. 确认目标节点名称及其上运行的依赖特定域名的工作负载
2. 确认目标域名当前可正常解析
3. 确认 ChaosBlade Operator 已部署（DaemonSet 通道）或具备节点 SSH 访问权限（SSH 通道）

**演练步骤**：
1. 确认目标节点名称和上面运行的 Pod：
   ```bash
   kubectl get pods -o wide --field-selector spec.nodeName=<node-name>
   ```
2. 确认目标域名当前解析正常：
   ```bash
   kubectl exec <该节点上的pod> -n <namespace> -- nslookup <target-domain>
   ```
3. 选择执行通道并注入节点级 DNS 劫持：

   **方式一：DaemonSet 通道**
   ```bash
   blade create k8s node-network dns \
     --names <node-name> \
     --domain <target-domain> \
     --ip <错误IP地址> \
     --timeout <duration> \
     --kubeconfig <kubeconfig-path>
   ```

   **方式二：SSH 通道**
   ```bash
   blade create k8s node-network dns \
     --domain <target-domain> \
     --ip <错误IP地址> \
     --channel ssh \
     --ssh-host <node-ip> \
     --ssh-user root \
     --timeout <duration>
   ```
   - `--domain`：要劫持的域名（必填）
   - `--ip`：劫持后指向的错误 IP（必填）
4. 记录返回的 experiment_uid，用于后续恢复

**注入验证**：
1. 在目标节点宿主机侧验证劫持生效（hosts 修改路径的正证据）：
   ```bash
   kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host getent hosts <target-domain>
   ```
   确认解析到注入的错误 IP。**不要用 nslookup 验证 hosts 劫持**——nslookup 是纯 DNS 查询、不读 /etc/hosts，在宿主机与非 hostNetwork Pod 内均验不出劫持；getent 走完整 resolver（/etc/hosts 优先）才是正证
2. 验证其他域名解析不受影响
3. 验证其他节点解析正常（确认故障限定在目标节点）：
   ```bash
   kubectl debug node/<其他节点名> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host getent hosts <target-domain>
   ```
4. 查看应用日志确认出现连接异常（受影响的是宿主机进程与 hostNetwork Pod 上的应用；非 hostNetwork Pod 的应用不受影响）

**注入恢复**：
1. 销毁 ChaosBlade 实验：
   ```bash
   blade destroy <experiment_uid>
   ```
2. 或等待 `--timeout` 到期自动恢复

**恢复验证**：
1. 在目标节点上的 Pod 内验证域名解析恢复正确：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- nslookup <target-domain>
   ```
2. 确认应用日志不再出现连接错误
3. 确认业务调用恢复正常

**基准事实**：
- **根因**：节点宿主机级别 DNS 解析被劫持（/etc/hosts 修改），特定域名被解析到错误 IP；影响宿主机进程与 hostNetwork Pod（其 /etc/hosts 为宿主机 hosts 的 kubelet 管理副本）；非 hostNetwork Pod 的容器内解析不受影响（独立 /etc/hosts + 集群 DNS）
- **必现现象**：该节点宿主机与 hostNetwork Pod 对目标域名解析结果为错误 IP；非 hostNetwork Pod 解析不受影响（实测零传导）；其他节点不受影响

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效节点级 DNS 劫持。

前提条件：集群需支持 `kubectl debug node` 功能（K8s 1.18+）；选择已验证可拉取且含 `chroot`/`sh` 的镜像；宿主机变更必须 `--profile=sysadmin`；禁用 `-it`

注入命令（**先备份、武装定时还原，再注入劫持记录**）：
```bash
# 通过 kubectl debug node 修改宿主机 /etc/hosts 注入 DNS 劫持。
# timer 由宿主机 systemd(PID 1) 管理，到期自动用备份还原 hosts；
# `&&` 串联保证武装失败时不会执行篡改
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'cp /etc/hosts /etc/hosts.bak &&
   systemd-run --on-active=<recovery-seconds>s --unit=blade-restore-hosts \
     sh -c "cp /etc/hosts.bak /etc/hosts && rm -f /etc/hosts.bak" &&
   echo "<错误IP> <target-domain>" >> /etc/hosts'
```

恢复命令（timer 到期前可提前手动恢复）：
```bash
# 提前恢复：还原宿主机 /etc/hosts（同时停掉已武装的 timer）
kubectl debug node/<node-name> --profile=sysadmin --image=<verified-cluster-image> -- chroot /host sh -c \
  'systemctl stop blade-restore-hosts.timer 2>/dev/null; cp /etc/hosts.bak /etc/hosts && rm -f /etc/hosts.bak'
# 删除 debug Pod
kubectl delete pod <debug-pod-name> --force --grace-period=0
```

注意事项：
- 修改宿主机 /etc/hosts 对宿主机上所有使用 glibc 的进程立即生效；对节点上容器的传导取决于 Pod 网络模式（实测）：**非 hostNetwork Pod（绝大多数业务 Pod）零传导**——其 /etc/hosts 由 kubelet 独立管理（仅 localhost 与 Pod IP 条目），宿主机劫持期间容器内解析仍返回真实 IP；**hostNetwork Pod 会传导**——其 /etc/hosts 为 kubelet 维护的宿主机 hosts 内容副本（文件头标注 "Kubernetes-managed hosts file (host network)"），宿主机条目变化会同步其中
- 部分应用有 DNS 缓存（如 JVM），修改 hosts 后可能需等待缓存过期
- 自恢复基于 systemd-run transient timer 到期自动用备份还原 hosts，补齐了 ChaosBlade `--timeout` 的自恢复能力；备份文件 `/etc/hosts.bak` 是还原的唯一依据，注入前必须确认备份成功（`&&` 串联已保证）
- 同名 transient timer 重复武装会报 `Unit blade-restore-hosts.service was already loaded`（上次武装命令执行失败时 unit 以 failed 状态残留所致）；重武装前先按本文件注入命令的同等 chroot /host 通道形态清理残留：`systemctl stop blade-restore-hosts.service; systemctl reset-failed blade-restore-hosts.service`（武装命令成功执行过的 unit 无残留，可直接重武装）
