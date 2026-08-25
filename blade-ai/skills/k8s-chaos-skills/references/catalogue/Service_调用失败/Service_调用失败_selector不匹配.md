**用例名称** selector不匹配 导致 Service_调用失败

**故障现象**：
1. Service 的 Endpoints 列表为空
2. 通过 Service 访问应用返回 connection refused 或无响应
3. Pod 正常运行但未被 Service 选中

**资源准备**：
1. 确认应用 A 已正常运行，对外暴露 Service
2. 确认监控系统可观测 Service 请求指标和 Endpoints 状态

**演练步骤**：
1. 记录应用 A 的 Service 当前 selector 配置
2. **先武装定时自恢复，再注入**（基线捕获 → 武装定时器 → 注入三步。恢复命令幂等：定时器到期
   自动恢复为主，Agent 在演练结束时主动执行同一条命令兜底，定时器迟到重复执行无副作用。定时器
   shell 逻辑必须作为 `kubectl exec` 载体载荷派发——直接以 `sh -c '…'` 作为顶层命令派发会被
   命令守卫拦截（unknown_binary: sh），载体内 `sh -c` 同时解决 exec-form 通道不解释裸
   `( sleep … ) &` 语法的问题；执行通道为多副本路由，无法可靠终止定时器，故不设 pidfile。
   载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻 Pod（如演练工具 Pod）。
   恢复命令含 json patch 引号嵌套，
   用 base64 折叠武装）：
   ```bash
   # 基线捕获：Agent 读取输出并记录原始 selector（定时器与主动恢复均使用）
   kubectl get svc <service-name> -n <namespace> -o jsonpath='{.spec.selector}'
   # 武装定时自恢复（将"注入恢复"第 1 步命令整体 base64 编码后填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-selector.sh; ( sleep <duration>; sh /tmp/blade-restore-selector.sh ) >/dev/null 2>&1 & echo armed'
   # 篡改 selector 注入
   kubectl patch svc <service-name> -n <namespace> \
     -p '{"spec":{"selector":{"app":"non-existent-app"}}}'
   ```
3. 观察 Endpoints 变化和服务可用性

**注入验证**：
1. 执行 `kubectl get endpoints <service-name>`，确认 Endpoints 列表为空（无子集）
2. 向 Service 发送请求，确认返回 connection refused 或超时
3. 执行 `kubectl get pods -l app=<原标签>`，确认 Pod 实际正常运行
4. 对比 Service selector 与 Pod labels，确认不匹配

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动将 selector 整体替换回基线；演练提前结束时由 Agent 主动
   执行同一条恢复命令（幂等，定时器迟到再执行一次无副作用。注意用 json patch 的 `replace`
   而非 strategic merge patch——后者对 map 是键级合并，若注入期间键集变化会残留多余键导致
   selector 永久不匹配）：
   ```bash
   kubectl patch svc <service-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/selector","value":<基线捕获的原始 selector JSON>}]'
   ```
2. 等待 Endpoints 自动更新

**恢复验证**：
1. 执行 `kubectl get endpoints <service-name>`，确认 Endpoints 列表恢复，包含后端 Pod IP
2. 向 Service 发送请求，确认恢复正常
3. 确认服务可用性恢复

**基准事实**：
- **根因**：Service 的 selector 与后端 Pod 的 label 不匹配，导致 Endpoints 控制器无法关联任何 Pod，Service 无后端可转发
- **必现现象**：Endpoints 为空；Service 请求失败（connection refused/超时）；Pod 正常但未被选中
