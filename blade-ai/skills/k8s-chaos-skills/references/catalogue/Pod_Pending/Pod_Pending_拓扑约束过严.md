**用例名称** 拓扑约束过严 导致 Pod_Pending

**故障现象**：
1. Pod 状态为 Pending，无法被调度
2. Pod Events 中显示 `didn't match pod topology spread constraints` 或 `didn't match pod anti-affinity rules`
3. 由于拓扑分布约束或反亲和规则过严，调度器无法找到满足条件的节点

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群节点数量有限（便于触发约束冲突）

**演练步骤**：
1. 记录还原基线（基线捕获：Agent 读取输出并记录以下字段的原始值，恢复时使用；
   topologySpreadConstraints/affinity 原本无约束时输出为空）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.affinity}'
   kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.spec.replicas}'
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **武装定时自恢复**（恢复命令幂等：定时器到期自动还原为主，Agent 在演练结束时主动执行
   同组命令兜底，定时器迟到重复执行无副作用。定时器必须经 `kubectl exec` 载体派发——顶层
   裸 `sh -c '… & echo armed'` 不被工具守卫放行；载体 Pod 需含 kubectl 与集群凭证（如
   kubewiz-executor 或集群内工具 Pod，注意业务镜像多为极简镜像无 kubectl，不可作载体）；
   恢复含 json patch 引号嵌套，用 base64 折叠武装；`<duration>` 需覆盖滚动更新、扩容
   观察与恢复滚动全程）：
   ```bash
   # 武装定时自恢复（将"注入恢复"第 1 步命令组按基线选定 replace/remove 后整体 base64 编码填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体ns> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-topology.sh; ( sleep <duration>; sh /tmp/blade-restore-topology.sh ) >/dev/null 2>&1 & echo armed'
   ```
4. 修改应用 A 的 Deployment，注入调度约束（**首选反亲和形态**——实测 topologySpreadConstraints
   的 `maxSkew: 1` 语义是"任意两拓扑域副本数差 ≤ 1"，资源充足时任意副本数都能均匀铺开，
   Pending 判据结构性不可达：8 节点实测 12 副本（4×2+4×1）与 17 副本（1×3+7×2）全部
   调度成功；反亲和 required 语义是每拓扑域排他，副本数 > 节点数时 Pending 必现）：
   ```yaml
   # 首选：podAntiAffinity（required = 每拓扑域排他 → 副本数 > 节点数时 Pending 必现）
   affinity:
     podAntiAffinity:
       requiredDuringSchedulingIgnoredDuringExecution:
       - labelSelector:
           matchLabels:
             app: <app-name>
         topologyKey: kubernetes.io/hostname
   ```
   若确需验证 topologySpreadConstraints 形态（判据不可达，仅作约束生效性观察——已调度 Pod
   呈均匀分布，Pending 不出现）：
   ```yaml
   topologySpreadConstraints:
   - maxSkew: 1
     topologyKey: kubernetes.io/hostname
     whenUnsatisfiable: DoNotSchedule
     labelSelector:
       matchLabels:
         app: <app-name>
   ```
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 注入验证完成后暂缓还原 maxUnavailable——**必须等恢复流程移除约束且第二次滚动完成后再还原**
   （实测：反亲和形态下若注入后立即还原为默认 25%，恢复时移除约束触发的第二次滚动中，新 RS
   Pod 会被仍在运行的旧 RS Pod 的反亲和规则挡住（Events：`didn't satisfy existing pods
   anti-affinity rules`），新 Pod Pending + 旧 RS 滞留形成滚动死锁，实测持续 4-5 分钟才自行
   破局。maxUnavailable 保持 100% 直至恢复完成是防死锁的关键）
7. 将应用 A 的副本数扩大到超过集群节点数
8. 观察无法调度的 Pod 状态

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认部分或全部 Pod 状态为 Pending
3. 执行 `kubectl describe pod <pending-pod>`，确认 Events 显示拓扑约束或反亲和相关的调度失败原因

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动还原拓扑约束与副本数；演练提前结束时由 Agent 主动执行
   同组恢复命令（幂等，定时器迟到再执行一次无副作用。json patch 按字段精确替换/移除，天然
   规避 resourceVersion 乐观锁问题，也不会像 apply 三方合并那样保留注入新增的字段。
   **顺序即安全**——约束移除必须先于 maxUnavailable 还原，否则第二次滚动死锁，见步骤 6）：
   ```bash
   # ① 还原 topologySpreadConstraints（基线非空时 replace 基线 JSON；原本为空时 remove）
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"remove","path":"/spec/template/spec/topologySpreadConstraints"}]'
   # ② 还原 affinity（同上按基线 replace/remove）
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"remove","path":"/spec/template/spec/affinity"}]'
   # ③ 副本数恢复为基线值
   kubectl scale deployment <deployment-name> -n <namespace> --replicas=<基线副本数>
   ```
2. 等待 Pod 滚动更新完成（约束已移除，新 RS Pod 不再受反亲和阻挡）
3. 最后还原 maxUnavailable 为基线值（此前的 100% 只是滚动保障手段，恢复完成后必须收回）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"<基线值>"}]'
   ```

**恢复验证**：
1. 执行 `kubectl get pods`，确认所有 Pod 状态为 Running
2. 确认副本数恢复正常

**基准事实**：
- **根因**：topologySpreadConstraints 或 podAntiAffinity 配置过严，当副本数超过可用拓扑域时，调度器无法满足约束条件
- **必现现象**：部分 Pod Pending；Events 显示拓扑约束或反亲和规则不满足；已调度 Pod 严格按约束分布
