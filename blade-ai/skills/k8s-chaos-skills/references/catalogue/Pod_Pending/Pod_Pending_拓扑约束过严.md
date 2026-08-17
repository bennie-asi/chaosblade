**用例名称** 拓扑约束过严 导致 Pod_Pending

**故障现象**：
1. Pod 状态为 Pending，无法被调度
2. Pod Events 中显示 `didn't match pod topology spread constraints` 或 `didn't match pod anti-affinity rules`
3. 由于拓扑分布约束或反亲和规则过严，调度器无法找到满足条件的节点

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群节点数量有限（便于触发约束冲突）

**演练步骤**：
1. 导出还原基线（含拓扑约束与副本数；**必须剥离 metadata 中的
   resourceVersion/uid/creationTimestamp/generation 与整个 status**——实证结论：带
   resourceVersion 的 `kubectl get -o yaml` 原样输出，无论 apply 还是 replace 都会因乐观锁
   Conflict 报错，武装还原永不生效）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> -o json | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   m=d['metadata']
   for k in ('resourceVersion','uid','creationTimestamp','generation','managedFields'): m.pop(k,None)
   d.pop('status',None); json.dump(d,open('/tmp/blade-topology-baseline.json','w'))"
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **先武装定时恢复，再修改约束**（在运行 kubectl 的机器上后台武装，到期自动用基线整体替换还原
   拓扑约束与副本数，补齐自恢复能力；**必须用 `kubectl replace` 而非 `kubectl apply`**——实证
   结论：apply 的三方合并会保留注入后新增的字段（还原不彻底）；replace 为 PUT 整体替换，实测在
   live 被多次修改后依然精确还原；duration 需覆盖滚动更新耗时；PID 落盘供提前恢复时终止定时器）：
   ```bash
   ( sleep <duration>; kubectl replace -f /tmp/blade-topology-baseline.json ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-topology.pid
   ```
4. 修改应用 A 的 Deployment，添加过严的拓扑约束：
   ```yaml
   topologySpreadConstraints:
   - maxSkew: 1
     topologyKey: kubernetes.io/hostname
     whenUnsatisfiable: DoNotSchedule
     labelSelector:
       matchLabels:
         app: <app-name>
   ```
   或添加过严的反亲和规则：
   ```yaml
   affinity:
     podAntiAffinity:
       requiredDuringSchedulingIgnoredDuringExecution:
       - labelSelector:
           matchLabels:
             app: <app-name>
         topologyKey: kubernetes.io/hostname
   ```
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
7. 将应用 A 的副本数扩大到超过集群节点数
8. 观察无法调度的 Pod 状态

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认部分或全部 Pod 状态为 Pending
3. 执行 `kubectl describe pod <pending-pod>`，确认 Events 显示拓扑约束或反亲和相关的调度失败原因

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动用基线整体替换还原拓扑约束与副本数；如需提前恢复，先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-topology.pid) 2>/dev/null; rm -f /tmp/blade-restore-topology.pid
   ```
2. 恢复应用 A 的 Deployment 定义，移除或放宽拓扑约束/反亲和规则
3. 将副本数恢复为原始值
4. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认所有 Pod 状态为 Running
2. 确认副本数恢复正常

**基准事实**：
- **根因**：topologySpreadConstraints 或 podAntiAffinity 配置过严，当副本数超过可用拓扑域时，调度器无法满足约束条件
- **必现现象**：部分 Pod Pending；Events 显示拓扑约束或反亲和规则不满足；已调度 Pod 严格按约束分布
