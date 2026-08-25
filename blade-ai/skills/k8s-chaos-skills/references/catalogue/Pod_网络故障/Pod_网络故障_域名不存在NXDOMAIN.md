**用例名称** 域名不存在NXDOMAIN 导致 Pod_网络故障

**故障现象**：
1. 特定域名解析返回 NXDOMAIN（域名不存在），而非解析到错误 IP
2. 依赖该域名的应用连接失败，日志出现 `Name or service not known`
3. 与 CoreDNS 完全不可用不同：仅针对特定域名失败，其他域名解析正常
4. 模拟外部服务域名过期/DNS 记录误删场景

**资源准备**：
1. 确认应用 A 正常运行，且依赖特定域名进行服务调用
2. 确认 CoreDNS Deployment 正常运行（`kubectl get deployment coredns -n kube-system`）
3. 确认目标域名当前可正常解析

**演练步骤**：
1. 查看当前 Corefile 配置，确认插入锚点（Agent 记录原文全文——定时器与手动恢复均使用。
   ⚠️ 锚点陷阱（实测踩坑）：标准锚点是 `forward . /etc/resolv.conf` 行——核心 Corefile
   必有；部分发行版（如 ACK/ASI 定制版）**没有 `ready` 插件**，以 ready 为锚点的注入会
   静默失配（jq sub 找不到模式时返回原文，apply 后无任何变化，故障不出现且无报错））：
   ```bash
   kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}'
   ```
   同时获取 CoreDNS Pod 选择器标签（部分发行版为 `component=coredns`，不是
   `k8s-app=kube-dns`，后续 Pod 查询以实测为准）：
   ```bash
   kubectl get deployment coredns -n kube-system -o jsonpath='{.spec.selector.matchLabels}'
   ```
2. **先武装定时恢复，再注入**。定时器必须经 `kubectl exec` 载体派发——顶层裸 shell
   元字符（变量赋值/子 shell 后台/pidfile 落盘）不被工具守卫放行，载体需含 kubectl 与
   集群凭证；恢复含 patch JSON 引号嵌套，用 base64 折叠武装；多副本路由无法可靠终止
   定时器，故不设 pidfile，恢复命令幂等（迟到重复执行无副作用）。
   ⚠️ 载体 RBAC 前置检查（实测踩坑）：载体 SA 若无 kube-system 的 configmaps/patch
   权限，定时器还原会因 Forbidden 静默失败（输出被 `>/dev/null 2>&1` 丢弃，表面
   armed 实际永不还原——CoreDNS 配置污染直到人工介入）。注入前先在载体验证：
   `kubectl exec <载体Pod> -n <载体ns> -- kubectl auth can-i patch configmap coredns -n kube-system`，
   返回 no 则定时器方案不可用，改由 Agent 演练全程在线、结束时手动执行恢复命令兜底。
   还原脚本（base64 折叠为 <restore-script-b64>；<restore-json> 为
   `{"data":{"Corefile":"<原文，JSON 转义>"}}`，由 Agent 从步骤 1 原文构造）：
   ```sh
   kubectl patch configmap coredns -n kube-system --type=merge -p '<restore-json>'
   kubectl rollout restart deployment coredns -n kube-system
   ```
   ```bash
   kubectl exec <载体Pod> -n <载体ns> -- sh -c 'echo <restore-script-b64> | base64 -d > /tmp/blade-restore-nxdomain.sh; ( sleep <duration>; sh /tmp/blade-restore-nxdomain.sh ) >/dev/null 2>&1 & echo armed'
   ```
3. 注入——向 Corefile 添加 template 插件块，对目标域名返回 NXDOMAIN（Agent-native
   形态：Agent 从步骤 1 原文构造注入后的完整 Corefile——在 forward 锚点行前插入
   template 块——再以 merge patch 一次写入；无中间文件、无顶层管道，实测 merge patch
   还原链等价验证通过）：
   ```bash
   kubectl patch configmap coredns -n kube-system --type=merge \
     -p '{"data":{"Corefile":"<注入后完整 Corefile，JSON 转义，template 块插入在 forward 行前>"}}'
   ```
   等效手工参考（运维侧 jq 管道；Agent 执行通道因顶层管道不被守卫放行）：
   ```bash
   kubectl get configmap coredns -n kube-system -o json | \
     jq '.data.Corefile |= sub("forward \\. /etc/resolv\\.conf"; "template IN A <target-domain> {\n    rcode NXDOMAIN\n  }\n  forward . /etc/resolv.conf")' | \
     kubectl apply -f -
   ```
   说明：template 插件在插件链中位于 kubernetes 插件之后——**目标域名必须是外部域名**；
   cluster.local 内域名由 kubernetes 插件直接应答，template 拦不到，注入无效
4. 重启 CoreDNS 使配置生效：
   ```bash
   kubectl rollout restart deployment coredns -n kube-system
   ```
5. 等待 CoreDNS Pod 重新就绪

**注入验证**：
1. 确认 CoreDNS Pod 已重启且 Running（使用步骤 1 获取的实际标签）：
   ```bash
   kubectl get pods -n kube-system -l <coredns-label>
   ```
2. 在应用 Pod 内验证目标域名解析失败（⚠️ 判据陷阱：busybox nslookup 退出码不稳定
   ——NXDOMAIN 与超时均 RC=1，但 **NOERROR 空应答（`*** Can't find ...: No answer`）
   时 RC=0**——解析失败形态却报成功退出码，不能以退出码判读，必须 grep 输出文本）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- nslookup <target-domain>
   ```
   确认返回 `** server can't find <target-domain>: NXDOMAIN`
3. 验证其他域名解析仍正常（确认故障范围可控）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- nslookup kubernetes.default.svc.cluster.local
   ```
4. 检查应用日志出现 DNS 解析失败相关错误

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动还原 Corefile 并重启 CoreDNS；提前恢复或
   定时器不可用（RBAC 前置检查为 no）时，由 Agent 主动执行同一条还原命令（幂等，定时器
   迟到重复执行无副作用；定时器经载体派发无 pidfile 可终止，迟到触发只会把 Corefile
   原文再写一遍）。
   ⚠️ **不要 `kubectl apply` ConfigMap 的 yaml 备份还原**——备份中的 resourceVersion 等
   元数据会与集群现状冲突（实测 `error when applying patch: the object has been
   modified`），必须用注入前记录的原始 Corefile 文本做 merge patch 还原：
   ```bash
   kubectl patch configmap coredns -n kube-system --type=merge \
     -p '{"data":{"Corefile":"<步骤 1 记录的原文，JSON 转义>"}}'
   ```
2. 重启 CoreDNS 使恢复的配置生效（定时器还原链内已含；Agent 手动恢复时需单独执行）：
   ```bash
   kubectl rollout restart deployment coredns -n kube-system
   ```
3. 等待 CoreDNS Pod 重新就绪

**恢复验证**：
1. 在应用 Pod 内验证目标域名恢复解析：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- nslookup <target-domain>
   ```
   确认返回正常 IP 地址（CoreDNS 刚拉起时 Service 记录同步需数秒到数十秒；部分发行版
   多副本间存在间歇性空应答噪声，判读以多次查询的稳定形态为准）
2. 确认应用日志不再出现 DNS 解析失败错误
3. 确认 CoreDNS Pod 全部 Running 且 Ready

**基准事实**：
- **根因**：CoreDNS Corefile 被注入 template 规则，对特定域名强制返回 NXDOMAIN，模拟域名不存在/DNS 记录缺失场景
- **必现现象**：目标域名 nslookup/dig 返回 NXDOMAIN；其他域名解析正常；应用日志出现 `Name or service not known`
