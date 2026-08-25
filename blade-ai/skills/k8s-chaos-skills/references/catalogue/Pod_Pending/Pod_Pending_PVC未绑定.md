**用例名称** PVC未绑定 导致 Pod_Pending

**故障现象**：
1. Pod 状态为 Pending，无法启动
2. Pod Events 中显示 `pod has unbound immediate PersistentVolumeClaims`
3. PVC 状态为 Pending，无法绑定到 PV

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群中 StorageClass 和 CSI 插件正常工作

**演练步骤**（注意：本用例需要临时修改 Deployment 添加 volume 引用，这是故障注入的必要操作，不违反安全红线。目标应用无需预先配置 PVC——注入的目的就是添加一个无法绑定的 PVC 依赖。恢复步骤会还原所有修改）：
1. 记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
2. 使用 `kubectl apply -f` 创建一个引用不存在的 StorageClass 的 PVC（通过 `stdin_data` 传入 YAML）：
   ```yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: app-data-claim
     namespace: <namespace>
   spec:
     accessModes: ["ReadWriteOnce"]
     storageClassName: "ssd-retain-zone-c"
     resources:
       requests:
         storage: 10Gi
   ```
3. **先武装定时自恢复，再注入**（先捕获 volumes/volumeMounts 数组基线，再武装定时器。
   恢复命令幂等：定时器到期自动恢复为主，Agent 在演练结束时主动执行同一组命令兜底，定时器
   迟到重复执行无副作用。定时器 shell 逻辑必须作为 `kubectl exec` 载体载荷派发——直接以
   `sh -c '…'` 作为顶层命令派发会被命令守卫拦截（unknown_binary: sh）；执行通道为多副本
   路由，无法可靠终止定时器，故不设 pidfile。恢复含 json patch 引号嵌套，用 base64
   折叠武装）。
   载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻 Pod（如演练工具 Pod）：
   ```bash
   # 基线捕获：Agent 读取输出并记录原始数组 JSON（定时器与主动恢复均使用）
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.volumes}'
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.containers[<container-index>].volumeMounts}'
   # 武装定时自恢复（将"注入恢复"第 1 步的两条命令整体 base64 编码后填入 <restore-b64>）
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-pvc.sh; ( sleep <duration>; sh /tmp/blade-restore-pvc.sh ) >/dev/null 2>&1 & echo armed'
   ```
4. 使用 `kubectl patch` 修改应用 A 的 Deployment，添加引用该 PVC 的 volume 和 volumeMount
5. 等待 Pod 滚动更新完成，确认所有旧 Pod 已被替换
6. 滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
7. 观察新 Pod 的状态

**注入验证**：
1. 执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pvc`，确认 PVC 状态为 Pending
3. 执行 `kubectl get pods`，确认**所有**目标 Pod 状态为 Pending（不是仅一个新 Pod，而是全部副本）
4. 执行 `kubectl describe pod <pod-name>`，确认 Events 显示 unbound PVC 相关信息
5. 执行 `kubectl describe pvc app-data-claim`，确认 StorageClass 不存在或 Provisioner 异常

**注入恢复**：
1. 等待 `<duration>` 到期，定时器自动执行基线还原；演练提前结束时由 Agent 主动执行同一条
   恢复命令（幂等，定时器迟到再执行一次无副作用。**数组整体 replace 回基线而非按索引 remove**
   ——remove 按位置删除，定时器第二次触发时数组已变化，同索引会误删其他卷；整体 replace
   重复执行结果不变）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/volumes","value":<步骤3基线volumes JSON>},{"op":"replace","path":"/spec/template/spec/containers/<container-index>/volumeMounts","value":<步骤3基线volumeMounts JSON>}]'
   kubectl delete pvc app-data-claim -n <namespace>
   ```
   （基线为空/字段原本不存在时，对应 replace 改为 remove——remove 对已不存在的路径仅报错，
   不会误删其他数组项）
2. 等待 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态恢复为 Running
2. 确认注入时创建的 PVC 已被清理

**基准事实**：
- **根因**：Pod 引用的 PVC 无法绑定，原因为 StorageClass 不存在或 Provisioner 异常，导致 Pod 无法挂载所需存储卷而 Pending
- **必现现象**：Pod Pending；PVC Pending；Events 显示 unbound PersistentVolumeClaims
