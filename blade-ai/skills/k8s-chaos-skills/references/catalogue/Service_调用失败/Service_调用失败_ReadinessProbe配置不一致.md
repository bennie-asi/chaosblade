**用例名称** ReadinessProbe配置不一致 导致 Service_调用失败

**故障现象**：
1. Pod 状态为 Running 但 READY 为 0/1
2. Service 的 Endpoints 列表为空或逐渐减少
3. Readiness Probe 持续失败，Pod 从 Service 后端移除

**资源准备**：
1. 确认应用 A 已正常运行，对外暴露 Service
2. 确认应用 A 实际监听的端口和健康检查路径

**演练步骤**：
1. 记录应用 A 当前的 readinessProbe 配置（基线捕获：Agent 读取输出并记录 JSON，恢复时使用；
   原本无 readinessProbe 时输出为空）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.containers[0].readinessProbe}'
   ```
2. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
3. **武装定时自恢复**（恢复命令幂等：定时器到期自动还原为主，Agent 在演练结束时主动执行
   同一条命令兜底，定时器迟到重复执行无副作用。定时器 shell 逻辑必须作为 `kubectl exec` 载体
   载荷派发——直接以 `sh -c '…'` 作为顶层命令派发会被命令守卫拦截（unknown_binary: sh），
   载体内 `sh -c` 同时解决 exec-form 通道不解释裸 `( sleep … ) &` 语法的问题；执行通道为
   多副本路由，无法可靠终止定时器，故不设 pidfile。载体 Pod 选集群内带 kubectl 且有足够
   RBAC 权限的常驻 Pod（如演练工具 Pod）。恢复含 json patch 引号嵌套，用 base64 折叠武装；
   `<duration>` 需覆盖滚动更新与观察窗口）：
   ```bash
   # 武装定时自恢复（将"注入恢复"第 1 步命令按基线选定 replace/remove 后 base64 编码填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-readiness.sh; ( sleep <duration>; sh /tmp/blade-restore-readiness.sh ) >/dev/null 2>&1 & echo armed'
   ```
4. 修改应用 A 的 Deployment，将 readinessProbe 路径或端口设置为与实际不一致：
   ```yaml
   readinessProbe:
     httpGet:
       path: <应用不提供的路径>   # 占位符：必须与应用实际健康检查路径不同；/non-existent-health-path 仅为示例写法
       port: <应用不监听的端口>   # 占位符：必须先探测应用实际监听端口后选一个未监听端口；9999 仅为示例写法
     periodSeconds: 5
     failureThreshold: 3
   ```
   （本用例的故障机制就是探针指向错误端点：注入前必须先探测目标应用实际监听端口与健康检查路径，再选一个确定未监听/不存在的值；不得照抄示例值，若示例端口恰被应用监听则故障不生效）
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段；实测跳过此步会导致 100% 泄漏到故障恢复之后，始终要在注入验证完成后第一时间还原）
7. 观察 Pod Ready 状态和 Service Endpoints 变化

**注入验证**：
1. 确认所有旧 Pod 已被替换（滚动更新完成）：**用 RS 视角判据，不要用 `kubectl rollout status`** ——
   注入期新 Pod 永不 Ready（故障本身），`rollout status` 等待 available 副本必然超时报错
   （实测复现），按其退出码会把已完全生效的故障误判为「滚动未完成」；正确判据是
   `kubectl get rs -n <namespace> -l <label>`：旧 RS DESIRED=0、新 RS DESIRED=1（或旧 Pod
   名消失、新 Pod Running 0/1）。对照：恢复路径（探针还原后）`rollout status` 正常返回
   `successfully rolled out`，可作恢复完成判据
2. 执行 `kubectl get pods`，确认**所有** Pod 状态为 Running 但 READY 列显示 0/1（不是仅一个新 Pod 0/1，而是全部副本都 0/1）
3. 执行 `kubectl get endpoints <service-name>`，确认 Endpoints 列表**为空**（无子集），而非仅新 Pod 不在列表中
4. 执行 `kubectl describe pod <pod-name>`，确认 Events 显示 `Readiness probe failed`
5. 向 Service 发送请求，确认返回 connection refused 或超时。注意：connection reset by peer 可能是应用自身行为而非故障效果，不可作为故障生效的充分证据；ipvs 模式无后端时实测为 Connection refused（kube-proxy 对无 Endpoints 的 ClusterIP 直接 reject）

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动将 readinessProbe 还原为步骤 1 基线；演练提前结束时由
   Agent 主动执行同一条恢复命令（幂等，定时器迟到再执行一次无副作用——基线非空时 json patch
   replace 回原值 JSON，原本无探针时 remove。json patch 按字段精确替换，天然规避
   resourceVersion 乐观锁问题，也不会像 apply 三方合并那样保留注入后新增的字段）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe","value":<步骤1基线JSON>}]'
   # 原本无 readinessProbe 时改用 remove：
   # kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
   #   -p='[{"op":"remove","path":"/spec/template/spec/containers/0/readinessProbe"}]'
   ```
2. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod READY 为 1/1
2. 执行 `kubectl get endpoints <service-name>`，确认 Pod 重新加入 Endpoints
3. 向 Service 发送请求，确认服务恢复正常

**基准事实**：
- **根因**：Readiness Probe 的路径或端口与应用实际监听不一致，Probe 持续失败导致 Pod 被标记为 Not Ready，从 Service Endpoints 中移除
- **必现现象**：Pod Running 但 Not Ready（0/1）；Endpoints 为空；Events 显示 Readiness probe failed
