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
1. 备份当前 CoreDNS ConfigMap：
   ```bash
   kubectl get configmap coredns -n kube-system -o yaml > /tmp/coredns-backup.yaml
   ```
2. 查看当前 Corefile 配置，确认插入位置：
   ```bash
   kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}'
   ```
3. **先武装定时恢复，再注入**（在运行 kubectl 的机器上后台武装，到期自动将 Corefile
   还原为原始值，补齐自恢复能力；PID 落盘供提前恢复时终止定时器）。
   注意：**不要用备份文件 apply 还原** —— 备份中的 resourceVersion 会与集群现状冲突
   （实测 `error when applying patch: the object has been modified`），必须用
   注入前提取的原始 Corefile 文本做 json patch 还原：
   ```bash
   ORIG_COREFILE=$(kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}')
   ( sleep <duration>; \
     printf '%s' "$ORIG_COREFILE" | jq -Rs '{data:{Corefile:.}}' > /tmp/coredns-restore.json && \
     kubectl patch configmap coredns -n kube-system --type=merge \
       --patch-file=/tmp/coredns-restore.json && \
     kubectl rollout restart deployment coredns -n kube-system ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-nxdomain.pid
   ```
4. 使用 kubectl patch 向 Corefile 中添加 template 插件，对目标域名返回 NXDOMAIN：
   ```bash
   kubectl get configmap coredns -n kube-system -o json | \
     jq '.data.Corefile |= sub("ready"; "template IN A <target-domain> {\n    rcode NXDOMAIN\n  }\n  ready")' | \
     kubectl apply -f -
   ```
   说明：在 `ready` 插件前插入 template 块，使 CoreDNS 对 `<target-domain>` 的 A 记录查询返回 NXDOMAIN
5. 重启 CoreDNS 使配置生效：
   ```bash
   kubectl rollout restart deployment coredns -n kube-system
   ```
6. 等待 CoreDNS Pod 重新就绪

**注入验证**：
1. 确认 CoreDNS Pod 已重启且 Running：
   ```bash
   kubectl get pods -n kube-system -l k8s-app=kube-dns
   ```
2. 在应用 Pod 内验证目标域名解析失败：
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
1. 等待 `<duration>` 到期后武装的定时器自动还原 Corefile 并重启 CoreDNS；如需提前恢复，
   先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-nxdomain.pid) 2>/dev/null; rm -f /tmp/blade-restore-nxdomain.pid
   ```
2. 用注入前提取的原始 Corefile 做 json patch 还原（**不要 `kubectl apply` 备份文件**，
   resourceVersion 冲突会直接失败）：
   ```bash
   printf '%s' "$ORIG_COREFILE" | jq -Rs '{data:{Corefile:.}}' > /tmp/coredns-restore.json
   kubectl patch configmap coredns -n kube-system --type=merge \
     --patch-file=/tmp/coredns-restore.json
   ```
   若 `$ORIG_COREFILE` 变量已丢失（如换了 shell），从备份文件中提取 data 字段重建：
   ```bash
   yq '.data' /tmp/coredns-backup.yaml -o=json > /tmp/coredns-restore.json   # 无 yq 时手工取 Corefile 文本
   ```
3. 重启 CoreDNS 使恢复的配置生效：
   ```bash
   kubectl rollout restart deployment coredns -n kube-system
   ```
4. 等待 CoreDNS Pod 重新就绪

**恢复验证**：
1. 在应用 Pod 内验证目标域名恢复解析：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- nslookup <target-domain>
   ```
   确认返回正常 IP 地址
2. 确认应用日志不再出现 DNS 解析失败错误
3. 确认 CoreDNS Pod 全部 Running 且 Ready

**基准事实**：
- **根因**：CoreDNS Corefile 被注入 template 规则，对特定域名强制返回 NXDOMAIN，模拟域名不存在/DNS 记录缺失场景
- **必现现象**：目标域名 nslookup/dig 返回 NXDOMAIN；其他域名解析正常；应用日志出现 `Name or service not known`
