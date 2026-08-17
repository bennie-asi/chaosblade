**用例名称** Volume挂载超时CSI异常 导致 Pod_ContainerCreating

**故障现象**：
1. Pod 长时间停留在 ContainerCreating 状态
2. Pod Events 中显示 `FailedMount` 或 `FailedAttachVolume`，提示 CSI driver 超时或 attach 失败
3. PV 指向的云盘不存在或不可用，CSI 驱动无法完成 attach/mount

**机制警示（真实演练教训，违反则故障现象必然退化）**：
- **严禁给 PV 添加 nodeAffinity**：一旦 PV 带 `nodeAffinity`，调度器的 VolumeBinding 过滤器会在**调度阶段**前置过滤节点（Events 显示 `volume node affinity conflict`，Pod 呈 `Pending` + `FailedScheduling`）。CSI 驱动根本不会被调用，本用例承诺的 ContainerCreating 现象永远无法复现
- 正确机制：PV **不带任何拓扑约束**让调度正常通过，`volumeHandle` 指向不存在/不可用的云盘，使 CSI 驱动在 **attach 阶段**失败，Pod 才会卡在 ContainerCreating

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群 CSI 驱动（如 `diskplugin.csi.alibabacloud.com`）正常工作

**演练步骤**：
1. （仅 Deployment 目标）记录 Deployment 当前 maxUnavailable 值，并临时设为 100%（确保滚动更新能完成，故障注入的新 Pod 不会 Ready，默认策略下 K8s 不会终止旧 Pod，导致滚动更新死锁）：
   ```bash
   kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.strategy.rollingUpdate.maxUnavailable}'
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/strategy/rollingUpdate/maxUnavailable","value":"100%"}]'
   ```
   StatefulSet 目标跳过此步，见「StatefulSet 目标工作负载」一节。
2. 使用 `kubectl(subcommand="apply", v_args="-f -", stdin_data="...")` 创建 PV 和 PVC（**必须使用 `stdin_data` 参数传入 YAML 且必须带 `-f -`，不要用 exec heredoc 或其他方式**）：
   PV YAML（**无 nodeAffinity、无 storageClassName、volumeHandle 为不存在的云盘 ID**）：
   ```yaml
   apiVersion: v1
   kind: PersistentVolume
   metadata:
     name: archive-vol-chaos
   spec:
     capacity:
       storage: 20Gi
     accessModes: ["ReadWriteOnce"]
     csi:
       driver: diskplugin.csi.alibabacloud.com
       volumeHandle: <不存在的云盘ID，如 d-fake-chaos-vol-001>
     fsType: ext4
   ```
   PVC YAML（**storageClassName 必须为空字符串**，静态绑定无 SC 的 PV，避免动态供给或 WaitForFirstConsumer 干扰）：
   ```yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: archive-vol-claim
     namespace: <namespace>
   spec:
     accessModes: ["ReadWriteOnce"]
     storageClassName: ""
     resources:
       requests:
         storage: 20Gi
     volumeName: archive-vol-chaos
   ```
3. **先武装定时恢复，再修改模板**（在运行 kubectl 的机器上后台武装，到期自动移除注入的
   volumes/volumeMounts 并清理 PV/PVC，补齐自恢复能力；`<volume-index>`/`<mount-index>` 为注入时
   新增项在数组中的索引，添加前先记录；PID 落盘供提前恢复时终止定时器）：
   ```bash
   ( sleep <duration>; \
     kubectl patch <workload-kind>/<name> -n <namespace> --type='json' \
       -p='[{"op":"remove","path":"/spec/template/spec/containers/<container-index>/volumeMounts/<mount-index>"},{"op":"remove","path":"/spec/template/spec/volumes/<volume-index>"}]'; \
     kubectl delete pvc archive-vol-claim -n <namespace>; \
     kubectl delete pv archive-vol-chaos ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-csi.pid
   ```
4. 修改应用 A 的工作负载模板，添加引用该 PVC 的 volume 和 volumeMount
5. （Deployment）等待滚动更新完成，确认所有旧 Pod 已被替换；（StatefulSet）删除目标 Pod 触发重建
6. （仅 Deployment）滚动更新完成后，立即还原 maxUnavailable 为原始值（maxUnavailable 只是使滚动更新完成的手段，不是故障本身，不应泄漏到恢复阶段）
7. 观察 Pod 的 ContainerCreating 状态

**StatefulSet 目标工作负载**：
- StatefulSet 没有 maxUnavailable；滚动更新控制参数是 `spec.updateStrategy.rollingUpdate.partition`
- partition 语义：**ordinal >= partition 的 Pod 使用新模板，ordinal < partition 的保持旧模板；丢失 Pod 的重建同样遵循此规则**
- 由此推出硬约束：**无法隔离"仅中间单个 Pod 受影响"**——patch 模板后，目标 Pod 与所有更高 ordinal 的 Pod 都会用新模板重建（从高 ordinal 向低 ordinal 依次进行）
- 爆炸半径声明必须按实际受影响的 ordinal 区间如实告知用户，严禁声称 target-only
- 新 Pod 永不 Ready 时 StatefulSet 滚动更新同样会卡住（控制器等待新 Pod Ready 才继续），这是本故障的预期现象，无需也不可用 maxUnavailable 类技巧绕过

**注入验证**：
1. （Deployment）执行 `kubectl rollout status deployment <deployment-name>`，确认滚动更新已完成（所有旧 Pod 已被替换）。如果滚动更新未完成（卡死），则故障未完全生效，不可判定为 verified
2. 执行 `kubectl get pods`，确认目标 Pod 状态为 ContainerCreating
3. 执行 `kubectl describe pod <pod-name>`，确认 Events 显示 FailedMount 或 FailedAttachVolume 或 CSI attach 超时
4. **如果观察到 Pending + FailedScheduling（事件含 `volume node affinity conflict`），说明 PV 带了 nodeAffinity，机制错误——不可判定为 verified，必须删除 PV/PVC 并按本用例模板（无 nodeAffinity）重新注入**

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动移除注入的 volumes/volumeMounts 并清理 PV/PVC；如需提前恢复，先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-csi.pid) 2>/dev/null; rm -f /tmp/blade-restore-csi.pid
   ```
2. 恢复应用 A 的工作负载模板，移除注入时添加的 volumes 和 volumeMounts（两者都需移除，只移除其中一个会导致配置错误）
3. 等待 Pod 滚动更新/重建完成，确认 Pod 恢复 Running
4. （Deployment）还原 maxUnavailable 为演练前记录的原始值
5. 清理测试 PVC：`kubectl delete pvc archive-vol-claim -n <namespace>`
6. 清理测试 PV：`kubectl delete pv archive-vol-chaos`

**恢复验证**：
1. 执行 `kubectl get pods`，确认 Pod 状态恢复为 Running
2. 确认测试资源已清理
3. 确认应用 A 存储功能正常

**基准事实**：
- **根因**：CSI 驱动 attach/mount 失败（云盘不存在或不可用），Pod 无法完成存储卷挂载
- **必现现象**：Pod ContainerCreating；Events 显示 FailedMount/FailedAttachVolume/CSI 超时
- **必不出现**：若 PV 带 nodeAffinity，现象退化为 Pending + FailedScheduling（调度器前置过滤），CSI 流程未被触发——这不是本用例的合格形态
