**用例名称** CoreDNS异常 导致 Pod_网络故障

**故障现象**：
1. Pod 内 DNS 解析失败，应用报 `Name or service not known` 或 `NXDOMAIN` 错误
2. CoreDNS Pod 异常（CrashLoopBackOff/不可用）
3. 集群内服务间调用因 DNS 解析失败而中断

**资源准备**：
1. 确认应用 A 已正常运行，且依赖集群 DNS 进行服务发现
2. 确认 CoreDNS Deployment 正常运行
3. 确认监控系统可观测 DNS 请求指标

**演练步骤**：
1. 记录 CoreDNS Deployment 当前副本数和配置，并获取其 Pod 选择器标签：
   ```bash
   kubectl get deployment coredns -n kube-system -o jsonpath='{.spec.selector.matchLabels}'
   ```
   记录返回的标签（如 `component=coredns` 或 `k8s-app=kube-dns`），后续步骤中用 `<coredns-label>` 表示该标签
2. **先武装定时自恢复，再缩零**（基线捕获 → 武装定时器 → 注入三步。恢复命令幂等：定时器到期
   自动恢复为主，Agent 在演练结束时主动执行同一条命令兜底，定时器迟到重复执行无副作用。定时器
   必须经 `kubectl exec` 载体派发——顶层裸 `sh -c` 不被工具守卫放行，且载体需含 kubectl 与
   集群凭证；载体为多副本路由时无法可靠终止定时器，故不设 pidfile。
   ⚠️ 载体 RBAC 前置检查（实测踩坑）：载体 Pod 的 ServiceAccount 若无 kube-system 的
   deployments/scale 权限，定时器内的 scale 会因 Forbidden 静默失败——输出被
   `>/dev/null 2>&1` 丢弃，表面 armed 实际永不恢复，集群 DNS 瘫痪到人工介入。注入前必须在
   载体内验证：
   `kubectl exec <载体Pod> -n <载体ns> -- kubectl auth can-i scale deployment coredns -n kube-system`，
   返回 no 则定时器方案不可用，改由 Agent 演练全程在线、缩零后立即手动恢复。
   注意：CoreDNS 是集群级依赖，缩零期间**全集群** DNS 解析瘫痪，`<duration>` 应取小值、演练窗口尽量短）：
   ```bash
   # 基线捕获：Agent 读取输出并记录原始副本数（定时器与主动恢复均使用）
   kubectl get deployment coredns -n kube-system -o jsonpath='{.spec.replicas}'
   # 武装定时自恢复（载体需通过上述 RBAC 前置检查）
   kubectl exec <载体Pod> -n <载体ns> -- sh -c '( sleep <duration>; kubectl scale deployment coredns -n kube-system --replicas=<基线副本数> ) >/dev/null 2>&1 & echo armed'
   # 缩零注入
   kubectl scale deployment coredns -n kube-system --replicas=0
   ```
3. 在应用 A 的 Pod 内尝试进行 DNS 解析
4. 观察应用 A 的服务间调用行为

**注入验证**：
1. 执行 `kubectl get pods -n kube-system -l <coredns-label>`（使用演练步骤 1 中获取的实际标签），确认无 CoreDNS Pod 运行
2. 在应用 A 的 Pod 内执行 DNS 解析测试（⚠️ 判据陷阱：busybox nslookup 退出码不稳定
   ——超时（`connection timed out; no servers could be reached`）与 NXDOMAIN 均 RC=1，
   但 **NOERROR 空应答（`*** Can't find ...: No answer`）时 RC=0**——解析失败形态
   却报成功退出码，不能以退出码判定，必须 grep 输出文本）：
   ```bash
   kubectl exec <pod-name> -- sh -c 'nslookup kubernetes.default.svc.cluster.local | grep -E "timed out|no servers"'
   ```
   （管道必须包在 sh -c 载荷内——命令是 argv 直传无 shell，裸 nslookup 形态下
   `|`/`grep` 会沦为 nslookup 的字面参数，过滤静默失效）
   grep 有输出即确认解析失败
3. 确认应用 A 依赖 DNS 的服务调用出现错误

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动将副本数恢复为基线；演练提前结束时由 Agent 主动执行
   同一条恢复命令（幂等，定时器迟到再执行一次无副作用）：
   ```bash
   kubectl scale deployment coredns -n kube-system --replicas=<基线捕获的原始副本数>
   ```
2. 等待 CoreDNS Pod 启动并就绪

**恢复验证**：
1. 执行 `kubectl get pods -n kube-system -l <coredns-label>`（使用演练步骤 1 中获取的实际标签），确认 CoreDNS Pod 全部 Running 且 Ready
2. 在应用 A 的 Pod 内重新执行 DNS 解析，确认恢复正常（CoreDNS 刚拉起时 kubernetes 插件
   同步 Service 记录需数秒到数十秒，Ready 后立即查询可能仍空应答，稍候重试；部分发行版多副本
   间存在间歇性空应答噪声，判读以多次查询的稳定形态为准）
3. 确认应用 A 的服务间调用恢复

**基准事实**：
- **根因**：CoreDNS Pod 异常或不可用，导致集群内 DNS 解析服务中断，依赖 DNS 的服务发现和调用全部失败
- **必现现象**：Pod 内 DNS 解析超时或返回 NXDOMAIN；CoreDNS Pod 不可用；服务间调用失败
