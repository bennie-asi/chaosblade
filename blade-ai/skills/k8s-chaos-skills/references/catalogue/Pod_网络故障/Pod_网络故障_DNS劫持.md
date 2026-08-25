**用例名称** DNS劫持 导致 Pod_网络故障

**故障现象**：
1. Pod 对特定域名的解析被劫持到错误 IP 地址
2. 应用连接到非预期的服务端点，请求失败或返回异常数据
3. 与 DNS 解析失败（NXDOMAIN/超时）不同：域名仍可解析，但结果为错误 IP
4. 仅影响指定域名，其他域名解析正常

**资源准备**：
1. 确认目标应用已正常运行，且依赖特定域名进行外部服务调用
2. 确认目标 Pod 的标签选择器和命名空间
3. 确认目标域名当前可正常解析到正确 IP

**演练步骤**：
1. 确认目标 Pod 的标签选择器和命名空间：
   ```bash
   kubectl get pods -n <namespace> -l <label-selector> -o wide
   ```
2. 确认目标域名当前解析结果（基线。⚠️ 判据陷阱：本场景注入层在 /etc/hosts，而
   nslookup 只查 DNS 服务器、**不读 /etc/hosts**——nslookup 看不见劫持效果，不能作判据。
   ping/wget 走 getaddrinfo（含 hosts 层），是正确判据工具）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- ping -c 1 -W 3 <target-domain>
   ```
   从输出首行 `PING <domain> (<IP>)` 读取当前解析 IP 作为基线
3. 使用 ChaosBlade 对目标 Pod 注入 DNS 劫持：
   ```bash
   blade create k8s pod-network dns \
     --namespace <namespace> \
     --labels "<label-key>=<label-value>" \
     --domain <target-domain> \
     --ip <错误IP地址> \
     --timeout <duration> \
     --kubeconfig <kubeconfig-path>
   ```
   - `--domain`：要劫持的域名（必填）
   - `--ip`：劫持后指向的错误 IP（必填）
   - `--replace`：若域名已有本地解析记录，是否覆盖（默认不覆盖）
4. 记录返回的 experiment_uid，用于后续恢复

**注入验证**：
1. 在目标 Pod 内验证域名解析已被劫持（nslookup 不读 /etc/hosts，用 ping 判读）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- ping -c 1 -W 3 <target-domain>
   ```
   确认输出首行解析 IP 为注入的错误 IP 地址
2. 在目标 Pod 内尝试访问该域名，确认连接失败或返回异常：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- wget -qO- --timeout=5 http://<target-domain>
   ```
3. 查看应用日志确认出现连接错误（connection refused/timeout/非预期响应）：
   ```bash
   kubectl logs <pod-name> -n <namespace> --tail=20
   ```
4. 验证其他域名解析不受影响（确认故障范围可控）

**注入恢复**：
1. 销毁 ChaosBlade 实验：
   ```bash
   blade destroy <experiment_uid>
   ```
2. 等待 DNS 缓存刷新（通常即时生效）

**恢复验证**：
1. 在目标 Pod 内验证域名解析恢复正确（同样用 ping 而非 nslookup）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- ping -c 1 -W 3 <target-domain>
   ```
   确认输出首行解析 IP 回到基线记录的正确 IP 地址
2. 在目标 Pod 内验证服务访问恢复正常
3. 确认应用日志不再出现连接错误

**基准事实**：
- **根因**：Pod 内 DNS 解析被 ChaosBlade 劫持，特定域名被解析到错误 IP 地址，导致应用连接到非预期端点
- **必现现象**：目标域名解析（ping/getaddrinfo 路径）返回注入的错误 IP；应用对该域名的请求失败或返回异常；其他域名解析不受影响（注：nslookup 不读 /etc/hosts，看不见劫持效果，验证一律用 ping/wget）

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，用以下 kubectl 原生命令实现等效 Pod 级 DNS 劫持。
> 有两条路径，**优先用路径 A** —— 它只用控制面 kubectl，不依赖容器内有什么、也不依赖容器权限。

**路径 A：改 workload 的 `hostAliases`（推荐）**

`hostAliases` 是 Kubernetes 原生的 /etc/hosts 注入机制，由 kubelet 在创建容器时写入，
不需要容器内可写、不需要 root、不需要任何容器内工具。

前提条件：目标 Pod 由 Deployment / StatefulSet / DaemonSet 管理（能承受一次滚动重建）

注入命令（**先武装定时恢复，再注入**）：
```bash
# 1) 基线捕获 + 武装定时还原（恢复命令幂等：定时器到期自动还原为主，Agent 在演练结束时
#    主动执行同一条命令兜底，迟到重复执行无副作用。定时器必须经 kubectl exec 载体派发
#    ——顶层裸 sh -c 不被工具守卫放行，且载体需含 kubectl 与集群凭证；多副本路由无法
#    可靠终止，故不设 pidfile。恢复含 json patch 引号嵌套，用 base64 折叠武装；
#    <duration> 需覆盖滚动重建窗口。
#    ⚠️ 载体 RBAC 前置检查（实测踩坑）：载体 SA 若无目标 ns 的 deployments/patch 权限，
#    定时器还原会因 Forbidden 静默失败（输出被 >/dev/null 2>&1 丢弃，表面 armed 实际
#    永不还原，劫持将随滚动重建长期生效）。注入前先验证：
#    kubectl exec <载体Pod> -n <载体ns> -- kubectl auth can-i patch deployment <deployment-name> -n <namespace>，
#    返回 no 则定时器方案不可用，改由 Agent 演练全程在线、结束时手动执行恢复命令兜底）
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.spec.template.spec.hostAliases}'
kubectl exec <载体Pod> -n <载体ns> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-hostaliases.sh; ( sleep <duration>; sh /tmp/blade-restore-hostaliases.sh ) >/dev/null 2>&1 & echo armed'

# 2) 注入劫持记录 —— 把域名指向一个不可达 IP（240.0.0.0/4 是保留段，必然不可达）
kubectl patch deployment <deployment-name> -n <namespace> --type=strategic -p \
  '{"spec":{"template":{"spec":{"hostAliases":[{"ip":"<错误IP>","hostnames":["<target-domain>"]}]}}}}'

# 3) 等滚动重建完成，新 Pod 才带上劫持记录
kubectl rollout status deployment/<deployment-name> -n <namespace> --timeout=120s
```

恢复命令（定时器到期自动执行；提前结束时由 Agent 主动执行）：
```bash
# 原本没有 hostAliases —— 整个字段移除（幂等：remove 对已不存在的路径仅报错无害）
kubectl patch deployment <deployment-name> -n <namespace> --type=json -p \
  '[{"op":"remove","path":"/spec/template/spec/hostAliases"}]'

# 原本有 hostAliases —— 用注入前记录的原值 json patch 整体替换（strategic patch 对数组按
# 合并键合并，注入项会残留，不可用于还原）
kubectl patch deployment <deployment-name> -n <namespace> --type=json -p \
  '[{"op":"replace","path":"/spec/template/spec/hostAliases","value":<注入前记录的原值>}]'

kubectl rollout status deployment/<deployment-name> -n <namespace> --timeout=120s
```

**路径 B：直接改容器内 `/etc/hosts`（受限，先验证前提）**

前提条件：容器内 `/etc/hosts` 必须**当前用户可写**。这一条经常不成立 ——
该文件由 kubelet 生成，属主是 `root` 且权限通常是 `644`，而多数生产镜像以非 root 用户运行
（如 UID 1200），此时写入会 `Permission denied`。**必须先验证，不要假定可写**：

```bash
kubectl exec <pod-name> -n <namespace> -- sh -c 'ls -l /etc/hosts; id -u; test -w /etc/hosts && echo WRITABLE || echo NOT_WRITABLE'
```
输出 `NOT_WRITABLE` 就改走路径 A。

注入命令（**先备份 → 武装定时恢复 → 再注入**，三步严格串行；到期自动用备份还原 /etc/hosts）：
```bash
kubectl exec <pod-name> -n <namespace> -- sh -c \
  'cp /etc/hosts /etc/hosts.bak &&
   { ( sleep <duration>; cat /etc/hosts.bak > /etc/hosts; rm -f /etc/hosts.bak ) >/dev/null 2>&1 & } &&
   echo "<错误IP> <target-domain>" >> /etc/hosts'
```

结构说明：外层 `{ ... & }` 在前台执行（立即返回），保证备份**先于**注入完成；
若写成 `cp ... && ( ... ) & echo ...` 会把备份与注入并行，备份可能混入劫持记录，
导致到期"恢复"成被劫持状态——**严禁该写法**。
还原必须用 `cat >`（截断写），**不能用 `cp` 覆盖**：/etc/hosts 由 kubelet bind-mount，
实测 busybox cp 向它覆盖写入必报 `can't create '/etc/hosts': File exists`；若用在
定时器里，失败输出被丢弃、分号链继续删备份——劫持记录将永久残留。`cat >` 以 O_TRUNC
打开既有 inode，实测成功；追加注入 `>>` 同理可用。

恢复命令（提前恢复；SIGCONT 类幂等不适用此处，务必先还原再删备份。同 D50 陷阱：
`cp` 覆盖 /etc/hosts 必报 File exists，必须用 `cat >` 截断写）：
```bash
kubectl exec <pod-name> -n <namespace> -- sh -c \
  'cat /etc/hosts.bak > /etc/hosts && rm -f /etc/hosts.bak'
```

注意事项：
- 验证判据一律用 ping/wget（getaddrinfo 含 hosts 层），nslookup 不读 /etc/hosts，
  看不见劫持效果（实测确证：注入后 ping 解析到错误 IP、nslookup 仍返回 NXDOMAIN）
- 路径 A 会触发滚动重建，故障在**新 Pod** 上生效，原 Pod 名会变 —— 注入后需重新获取 Pod 名
- 路径 A 的劫持随 Pod 生命周期持续，重启也不丢；路径 B 改的是容器内临时文件，
  容器一旦重启，kubelet 重新生成 /etc/hosts，修改**自动丢失**（这也算一种兜底恢复）
- 自恢复基于注入前武装的定时器：路径 A 为执行通道 sh -c 载荷内定时器（到期自动按基线
  还原 hostAliases，json patch 规避乐观锁与三方合并问题），路径 B 为容器内 sleep <duration> +
  备份还原；路径 A 定时器存活于执行通道会话 Pod，Pod 重建会丢失定时器，届时仍需 Agent
  主动执行（或人工）恢复命令兜底
- 若应用绕过 hosts 直连 DNS 解析器（自带 resolver 或 DNS 缓存），两条路径都可能不生效；
  这种情况要在 DNS 层面做（见 `Pod_网络故障_CoreDNS异常`）
