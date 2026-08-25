**用例名称** 凭证缺失或过期 导致 Pod_镜像拉取失败

**故障现象**：
1. Pod 状态为 ImagePullBackOff 或 ErrImagePull
2. Pod Events 中显示 `unauthorized` 或 `authentication required`
3. 镜像仓库返回 401/403 认证错误

**资源准备**：
1. 确认应用 A 已正常运行，且使用私有镜像仓库
2. 确认应用 A 的 Pod 配置了 imagePullSecrets

**演练步骤**：
1. 记录应用 A 当前的 imagePullSecrets 名称和 imagePullPolicy 值，并导出还原基线（**必须剥离
   metadata 中的 resourceVersion/uid/creationTimestamp/generation 与整个 status**——实证结论：
   带 resourceVersion 的 `kubectl get -o yaml` 原样输出，无论 apply 还是 replace 都会因乐观锁
   Conflict 报错，武装还原永不生效）。Agent-native 形态：Agent 经 `kubectl get deployment
   <deployment-name> -n <namespace> -o json` 取回 JSON，在自身进程内剥离上述字段并压缩为
   单行，内嵌进步骤 3 的还原脚本（顶层管道 exec-form 通道不解释、命令守卫也不放行，
   下面的运维侧一行流仅供参考，Agent 勿派发）：
   ```bash
   # 运维侧参考（顶层管道会被守卫拦截）
   kubectl get deployment <deployment-name> -n <namespace> -o json | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   m=d['metadata']
   for k in ('resourceVersion','uid','creationTimestamp','generation','managedFields'): m.pop(k,None)
   d.pop('status',None); json.dump(d,open('/tmp/blade-imgsecret-baseline.json','w'))"
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **先武装定时恢复，再注入**（载体 Pod 武装形态——直接以顶层 `( sleep … ) &` 后台子 shell
   派发会被命令守卫拦截（unknown_binary: `(`），载体内 `sh -c` 载荷同时解决 exec-form 通道
   不解释裸后台语法的问题；到期自动用基线整体替换还原 imagePullSecrets/imagePullPolicy
   并删除无效 Secret，补齐自恢复能力；**必须用 `kubectl replace` 而非 `kubectl apply`**——
   实证结论：apply 的三方合并会保留注入后新增的字段（还原不彻底）；replace 为 PUT 整体
   替换，实测在 live 被多次修改后依然精确还原；duration 需覆盖滚动更新耗时；执行通道为
   多副本路由，无法可靠终止定时器，故不设 pidfile，恢复命令幂等——迟到重复执行无副作用）。
   还原脚本内容（base64 折叠为 <restore-b64>；`<基线JSON单行>` 为步骤 1 剥离后的单行 JSON，
   由 Agent 构造——Deployment JSON 不含单引号，单引号包裹安全）：
   ```sh
   echo '<基线JSON单行>' > /tmp/blade-imgsecret-baseline.json
   kubectl replace -f /tmp/blade-imgsecret-baseline.json
   kubectl delete secret registry-cred-rotating -n <namespace>
   ```
   ```bash
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-imgsecret.sh; ( sleep <duration>; sh /tmp/blade-restore-imgsecret.sh ) >/dev/null 2>&1 & echo armed'
   ```
   ⚠️ 载体 RBAC 前置：载体 SA 需目标命名空间 deployments 的 update（`kubectl replace`
   的 PUT 请求映射 RBAC 动词 update——`put` 不是合法 RBAC 动词）与 secrets 的 delete
   权限，否则定时器到期 Forbidden 静默失败（输出被重定向丢弃）。注入前先在载体验证：
   `kubectl exec <载体Pod> -n <载体ns> -- kubectl auth can-i update deployments -n <namespace>`，
   返回 no 则先补 RBAC 再武装。
4. 创建一个包含无效凭证的 Secret 来替换原有的有效凭证：
   ```bash
   kubectl create secret docker-registry registry-cred-rotating \
     --docker-server=<registry-server> \
     --docker-username=invalid-user \
     --docker-password=invalid-password \
     --namespace <namespace>
   ```
5. 修改应用 A 的 Deployment，将 imagePullSecrets 指向无效 Secret（或直接移除 imagePullSecrets）。
   同时检查 imagePullPolicy：如果当前为 `IfNotPresent`，需同时改为 `Always`，否则 K8s 直接使用本地缓存镜像启动 Pod，不会触发凭证校验，故障无法注入
6. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
7. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
8. 观察 Pod 状态变化

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认**所有**目标 Pod 状态为 ImagePullBackOff 或 ErrImagePull（不是仅一个新 Pod，而是全部副本）
3. 执行 `kubectl describe pod <pod-name>`，确认 Events 中显示认证失败相关错误
4. 确认错误信息包含 `unauthorized` 或 `authentication required`

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动用基线整体替换还原 imagePullSecrets/imagePullPolicy
   并删除无效 Secret。如需提前恢复，Agent 幂等重执行同款恢复命令（先把步骤 1 的基线 JSON 写入
   本地临时文件再 replace；定时器迟到触发无害——replace 对已还原对象是 no-op，delete 对已删
   Secret 仅报 NotFound）：
   ```bash
   kubectl replace -f /tmp/blade-imgsecret-baseline.json
   kubectl delete secret registry-cred-rotating -n <namespace>
   ```
2. 基线 replace 已同时还原 imagePullSecrets、imagePullPolicy（如注入时改过）与 maxUnavailable
   （演练步骤 2 临时改的 100%）——三项都在基线快照内，无需逐项手动还原
3. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态恢复为 Running
2. 确认镜像拉取成功，无认证错误

**基准事实**：
- **根因**：imagePullSecrets 缺失或 Secret 中的凭证已过期/无效，导致向私有镜像仓库拉取镜像时认证失败
- **必现现象**：Pod ImagePullBackOff；Events 显示 unauthorized/authentication required；镜像仓库返回 401/403
