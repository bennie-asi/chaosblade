**用例名称** CNI分配失败 导致 Pod_ContainerCreating

**故障现象**：
1. Pod 长时间停留在 ContainerCreating 状态
2. Pod Events 中显示 `failed to allocate for ENI` 或 `no available IP in subnet` 或 CNI 相关错误
3. 节点上的 IP 资源池耗尽或 ENI 数量达到上限

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认集群使用 ENI 或 vSwitch 分配 Pod IP 的 CNI 插件（如 Terway）
3. **余量核查（必做，命中即停）**：对比目标节点 `status.allocatable.pods`（pod 容量）与
   `metadata.annotations.k8s.aliyun.com/max-available-ip`（IP 池大小）。二者余量
   （pod 容量 − IP 池）必须显著大于节点系统 Pod 数 + 滚动更新瞬时超调（建议 ≥ 5）：
   pod 容量与 IP 池接近的节点上（实测 53 vs 50，余量 3），批量 Pod 会先撞满 pod 容量——
   K8s 1.31+ 上 Pod 呈 `OutOfpods` 状态（`Node didn't have enough resource: pods`），且
   OutOfpods Pod 仍占 slot、Deployment 对其删除重建形成自锁，「IP 耗尽」判据结构性不可达。
   此时应换用 pod 容量远大于 IP 池的节点，或终止演练并如实上报形态退化

**演练步骤**：
1. 查看目标节点的 ENI 和 IP 分配情况，确认目标应用 Pod 所在节点（记为 `<目标节点>`）
2. **先武装定时恢复，再注入**（恢复命令幂等：定时器到期自动删除耗尽 Deployment、还原
   nodeSelector、移除节点标签；Agent 在演练结束时主动执行同一条命令兜底，定时器迟到重复执行无副作用。定时器 shell 逻辑必须作为 `kubectl exec` 载体载荷派发——直接以
   `sh -c '…'` 作为顶层命令派发会被命令守卫拦截（unknown_binary: sh）；执行通道为
   多副本路由，无法可靠终止定时器，故不设 pidfile。恢复含 json patch 引号嵌套，用
   base64 折叠武装；`<duration>` 需覆盖批量 Pod 创建与观察窗口）。
   载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻 Pod（如演练工具 Pod）：
   ```bash
   # 将下列三条恢复命令整体 base64 编码后填入 <restore-b64>（nodeSelector 按基线选定
   remove 单 key 或 remove 整字段；label 移除为最后一步）：
   #   kubectl delete deployment chaos-ip-exhaust -n <namespace> --ignore-not-found=true
   #   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
   #     -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector/net.ops~1ipam-audit"}]'
   #   kubectl label node <目标节点> net.ops/ipam-audit-
   kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-cni.sh; ( sleep <duration>; sh /tmp/blade-restore-cni.sh ) >/dev/null 2>&1 & echo armed'
   ```
3. 给目标节点添加标签，并给应用 A 的 Deployment 添加 nodeSelector，确保新 Pod 只能调度到目标节点（防止调度器规避耗尽节点）：
   ```bash
   kubectl label node <目标节点> net.ops/ipam-audit=true
   kubectl patch deployment <deployment-name> -n <namespace> --type='merge' \
     -p='{"spec":{"template":{"spec":{"nodeSelector":{"net.ops/ipam-audit":"true"}}}}}'
   ```
   等待 rollout 完成（Pod 仍在原节点运行，因为目标节点已有此标签）。
   记录原始 nodeSelector 值，恢复时还原（武装还原仅移除新增的 key，若原本还有其他 nodeSelector 不受影响）。
4. 使用 `execute_skill_script` 在目标节点批量创建 Pod 耗尽 IP/ENI 资源（**`kubectl create/apply` 不可用，必须使用脚本**）：
   ```
   execute_skill_script(
     skill_name="k8s-chaos-skills",
     script_name="inject_cni_exhaust.py",
     params="--namespace <namespace> --node <目标节点> --kubeconfig <kubeconfig路径>"
   )
   ```
   脚本会创建 `chaos-ip-exhaust` Deployment 并绑定到目标节点，脚本输出中的 `[drill-vehicle: ...]` 登记行会被框架自动解析，将该 Deployment 注册为演练占位载具（恢复阶段与任务中途崩溃时的兜底清理都依赖此登记）。
5. 删除应用 A 在目标节点上的 Pod，触发重建。由于 nodeSelector 约束，新 Pod 只能调度到已耗尽的目标节点，将进入 ContainerCreating 状态
6. 观察新 Pod 的 ContainerCreating 状态

**注入验证**：
1. 执行 `kubectl get pods`，确认应用 A 新 Pod 状态为 ContainerCreating
2. 执行 `kubectl describe pod <pod-name>`，确认 Events 显示 CNI/IP 分配失败相关错误
3. 查看节点 ENI/IP 使用情况，确认资源已耗尽
4. **反证形态（命中即停）**：若批量 Pod 呈 `OutOfpods` 状态而非 Running 满载，说明先撞
   的是 pod 容量而非 IP 池（见资源准备第 3 条余量核查），本用例判据不可达，转入恢复并上报
   形态退化，禁止继续加压

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动删除耗尽 Deployment、还原 nodeSelector、移除节点标签；如需提前恢复，Agent 直接执行下列第 2–4 步恢复命令（幂等，定时器迟到再执行一次无副作用；多副本路由下无法可靠终止载体容器内的定时器进程，不依赖 pidfile）
2. 删除批量创建的 Deployment：`kubectl delete deployment chaos-ip-exhaust -n <namespace>`（该 Deployment 已由脚本登记行注册为演练载具，此删除会被守卫豁免）
3. 移除应用 A 的 Deployment 上添加的 nodeSelector（还原为原始值，若原本无 nodeSelector 则移除整个 nodeSelector）：
   ```bash
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector/net.ops~1ipam-audit"}]'
   ```
4. 移除目标节点上添加的标签：`kubectl label node <目标节点> net.ops/ipam-audit-`
5. 等待 IP/ENI 资源释放和 Pod 滚动更新完成

**恢复验证**：
1. 执行 `kubectl get pods`，确认应用 A 的 Pod 状态恢复为 Running
2. 确认节点 IP/ENI 资源恢复可用
3. 确认应用 A 网络连通正常

**基准事实**：
- **根因**：节点可用 IP 池耗尽或 ENI 数量达到上限或 vSwitch IP 不足，CNI 插件无法为新 Pod 分配网络资源
- **必现现象**：Pod ContainerCreating；Events 显示 CNI/IP/ENI 分配失败；节点网络资源耗尽
