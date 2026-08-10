**用例名称** 无效挂载选项注入 导致 Pod_ContainerCreating

**故障现象**：
1. Pod 长时间停留在 ContainerCreating 状态
2. Pod Events 中显示 `FailedMount`，mount 命令报 `bad option` / `wrong fs type, bad option, bad superblock`
3. mount 命令参数中可见非预期的挂载选项（如 `chaos-invalid-mntopt`）

**机制特点**：
本用例通过无效挂载选项在 mount 阶段阻断 Pod 启动：不创建任何占用者 Pod，不触发 Multi-Attach 竞争，故障根因直接落在 PV 挂载参数上。注意本用例同样需要删除目标 Pod 触发重建（见资源准备第 4 条），因此删除动作伴随的环境反应（如级联清理策略）同样须在资源准备阶段评估。

**资源准备**：
1. 确认目标 Pod 挂载了云盘类型 PVC（accessMode ReadWriteOnce），定位其 PV：`kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.volumes[*].persistentVolumeClaim.claimName}'`，再查 PV 名
2. **PV 当前 mountOptions 基线取证（必做）**：`kubectl get pv <pv> -o jsonpath='{.spec.mountOptions}'`，记录原值（可能为空）。恢复时必须还原为此基线，而不是无条件置空
3. **PV 生命周期风险核查（必做）**：确认该 PV 的 `persistentVolumeReclaimPolicy`，并确认没有外部控制器会周期性刷新 PV spec（若管控面会覆写 mountOptions，注入会被自动修复或产生竞争，需重新评估）。PV 是集群级资源，修改影响面大于命名空间内资源，确认它只被目标 PVC 绑定（RWO 一对一）
4. 确认删除目标 Pod 后控制器（StatefulSet/Deployment）会自动重建——重建是触发重新 mount 的必要条件

**演练步骤**：
1. 向 PV 注入无效挂载选项（追加，不覆盖既有选项）：
   ```
   kubectl patch pv <pv> --type json -p '[{"op":"add","path":"/spec/mountOptions","value":["chaos-invalid-mntopt"]}]'
   ```
   若 PV 已有 mountOptions，改用 `add` 到 `/spec/mountOptions/-` 追加。注入选项命名使用 `chaos-` 前缀标识，便于恢复审计
2. 删除目标 Pod 触发控制器重建：`kubectl delete pod <pod> -n <ns>`（用普通删除即可，本机制不依赖强制删除）
3. 新 Pod 调度后 kubelet 执行 mount 时携带无效选项失败，进入 ContainerCreating 并周期性重试（默认分钟级，可通过 Events 观察重试计数）

**机制反证条件（命中即停）**：
删除目标 Pod 后，若观察到以下任一现象，说明机制不可达，**立即停止一切尝试，上报偏离并转入恢复**：
1. PV 或 PVC 进入 Terminating 状态或被删除（存在级联删除/清理策略，继续操作会扩大破坏）
2. 新 Pod 未进入 ContainerCreating 而是直接 Running（说明 mount 未经过该 PV 或选项被旁路，注入无效）
3. mount 报错与注入选项无关（如 attach 失败、磁盘损坏），说明命中的是其他故障，须按真实故障处理

**注入验证**：
1. `kubectl get pods -n <ns>`：目标新 Pod 状态为 ContainerCreating
2. `kubectl describe pod <pod> -n <ns>`：Events 显示 `FailedMount`，报错信息包含注入的选项名（机制归因——症状相同但由其他原因产生的不算注入成功）
3. `kubectl get pv <pv> -o jsonpath='{.spec.mountOptions}'`：确认注入选项仍在 PV 上（故障持续的原因）

**注入恢复**：
1. 按基线还原 PV mountOptions：基线为空时 `kubectl patch pv <pv> --type json -p '[{"op":"remove","path":"/spec/mountOptions"}]'`；基线非空时 patch 回原值数组
2. kubelet 下一轮 mount 重试（分钟级）自动成功，Pod 原地转为 Running——**无需删除或重建 Pod**，恢复动作只有一个 patch

**恢复验证**：
1. `kubectl get pod <pod> -n <ns>`：状态恢复 Running，READY 1/1，且 Pod 对象未变（AGE 与注入前一致，证明原地恢复而非重建）
2. `kubectl get pv <pv> -o jsonpath='{.spec.mountOptions}'`：与注入前基线完全一致
3. Events 中不再出现新的 FailedMount

**基准事实**：
- **根因**：PV mountOptions 中的非法选项使 mount 命令失败，kubelet 在 volume mount 阶段阻塞并周期性重试
- **必现现象**：Pod ContainerCreating；Events 显示 FailedMount 且报错包含注入选项名；PV mountOptions 含注入标记
- **恢复特征**：单一 patch 还原选项后 kubelet 自动重试成功，零爆炸半径
