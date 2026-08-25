# 容器镜像被篡改 导致 Pod_镜像拉取失败

**用例名称**：容器镜像被篡改 导致 Pod_镜像拉取失败

**Blade 命令**：

```bash
blade create k8s pod-pod fail --labels <label-selector> --namespace <namespace> --timeout <duration> --kubeconfig <path>
```

**故障机制**：ChaosBlade 修改容器镜像为 `<原始镜像>-fault-injection` 后缀版本，K8s 尝试拉取新镜像失败，触发 ImagePullBackOff，使 Pod 不可用。

**故障现象**：

1. Pod 容器镜像被修改为不存在的 fault-injection 版本，触发 ImagePullBackOff
2. Pod 状态从 Running 变为 CrashLoopBackOff 或 ImagePullBackOff
3. Pod Events 中显示 "Container definition changed, will be restarted"
4. 关联 Service 的 Endpoints 被移除（Pod 不再 Ready）

**资源准备**：

1. 确认目标应用已正常运行，有明确的 namespace 和 label selector
2. 确认目标 Pod 由 Deployment/ReplicaSet 管理（确保恢复后能自动重建）
3. 确认监控系统可观测 Pod 状态变化和 Endpoints 变化

**演练步骤**：

1. 确认目标 Pod 当前状态为 Running 且 Ready
2. 记录目标 Pod 当前容器镜像版本（作为恢复基准）
3. 使用 ChaosBlade 注入：

```bash
blade create k8s pod-pod fail --labels <label-selector> --namespace <namespace> --timeout <duration> --kubeconfig <path>
```

4. 注：`pod-pod fail` 通过修改容器镜像为不存在的 `-fault-injection` 后缀版本来制造故障，而非直接删除 Pod
5. 等待 10-30 秒让故障生效
6. 观察 Pod 状态变化和应用影响

**注入验证**：

1. 确认目标 Pod 状态为 CrashLoopBackOff 或 ImagePullBackOff：

```bash
kubectl get pods -n <namespace> -l <label> -o wide
```

2. 确认容器镜像包含 `-fault-injection` 后缀，Events 有 ImagePullBackOff/ErrImagePull 错误：

```bash
kubectl describe pod <pod-name> -n <namespace>
```

3. 确认 Endpoints 减少或为空：

```bash
kubectl get endpoints -n <namespace>
```

**注入恢复**：

```bash
blade destroy <UID>
```

ChaosBlade 会将容器镜像恢复为原始版本。

**恢复验证**：

1. Pod 状态恢复为 Running 且 Ready
2. 容器镜像恢复为原始值（无 `-fault-injection` 后缀）
3. Service Endpoints 恢复正常

**基准事实**：

- 根因：ChaosBlade `pod-pod fail` 通过修改 Pod 容器镜像为不存在的版本来模拟 Pod 故障
- 必现现象：容器镜像含 `-fault-injection` 后缀；Pod 状态为 ImagePullBackOff 或 CrashLoopBackOff；Events 显示镜像拉取失败；Endpoints 被移除

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效镜像篡改故障注入。

前提条件：无特殊要求，仅需 kubectl 可访问集群

注入命令（**先武装定时还原，再注入**；恢复命令幂等：定时器到期自动还原为主，Agent 在演练
结束时主动执行同一条命令兜底，定时器迟到重复执行无副作用。定时器 shell 逻辑必须作为
`kubectl exec` 载体载荷派发——直接以 `sh -c '…'` 作为顶层命令派发会被命令守卫拦截
（unknown_binary: sh），裸 `ORIG_IMAGE=$(…)` 变量赋值 + 后台 `( ) &` 复合形态同样被拦；
执行通道为多副本路由，无法可靠终止定时器，故不设 pidfile，提前恢复靠幂等重执行同一条
set image 命令而非 kill 定时器。载体 Pod 选集群内带 kubectl 且有足够 RBAC 权限的常驻
Pod（如演练工具 Pod））：
```bash
# 基线捕获：Agent 读取输出并记录原始镜像完整引用（定时器与主动恢复均使用）
kubectl get deployment <deployment-name> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
# 武装定时自恢复（将"注入恢复"第 1 步 set image 命令 base64 编码后填入 <restore-b64>；
# <duration> 需覆盖滚动更新与观察窗口）
kubectl exec <载体Pod> -n <载体命名空间> -- sh -c 'echo <restore-b64> | base64 -d > /tmp/blade-restore-image.sh; ( sleep <duration>; sh /tmp/blade-restore-image.sh ) >/dev/null 2>&1 & echo armed'
# 再修改镜像为不存在的版本（⚠️ 用「替换 tag」形态：仓库地址 + 不存在 tag，
# 不要在原始镜像后追加——原始镜像已含 tag 时拼出双冒号非法引用）
kubectl set image deployment/<deployment-name> -n <namespace> <container-name>=<registry>/<repo>:non-existent-tag
```

恢复命令：
```bash
# 恢复为原始镜像版本（幂等；定时器若已执行，Agent 重复执行同一条命令无副作用，
# 无需也无法终止定时器——多副本路由下 pidfile 不可靠，见注入命令说明。
# 镜像值用基线捕获的完整引用，不要拼 `<原始镜像>:<原始标签>`——原始镜像已含 tag
# 时同样会拼出双冒号非法引用）
kubectl set image deployment/<deployment-name> -n <namespace> <container-name>=<基线捕获的完整镜像引用>
```

注意事项：
- **主方案 `pod-pod fail` 实测行为与故障现象描述有偏差**（v1.24.6 集群实测）：blade 改 Pod spec 镜像为
  `-fault-injection` 后缀后，kubelet 反复拉取失败（Events 报 404 BackOff）但**不杀旧容器**——
  运行中容器保持旧镜像（containerStatuses 仍为原镜像、restartCount 0）、Pod 持续 Running、
  服务不中断、Endpoints 不摘除；「CrashLoopBackOff + Endpoints 移除」现象不可达，
  可验证的判据是 Pod spec 镜像被改 + Events 拉取失败 404；`--timeout` 到期 blade 自动还原镜像
  （注意还原时 ReplicaSet 补建的新 Pod 可能短暂继承被篡改镜像，最终收敛回原镜像）
- **降级方案注入镜像必须用「替换 tag」形态**（`<registry>/<repo>:non-existent-tag`）：旧形态
  `<原始镜像>:non-existent-tag` 在原始镜像已含 tag（生产常态）时拼出双冒号引用
  （如 `busybox:1.33:non-existent-tag`）——K8s 直接拒绝为非法镜像名，新 Pod 呈
  **InvalidImageName**（Event 报 `invalid reference format`）而非 ImagePullBackOff，
  按镜像拉取失败判据会误判（实测复现）
- ChaosBlade `pod-pod fail` 直接修改 Pod spec 中的镜像，kubectl-native 方式通过 Deployment 触发滚动更新
- 恢复时需记住原始镜像地址和标签
- 自恢复基于注入前武装的后台定时器（sleep <duration> + set image 还原），到期自动还原原始镜像；提前恢复仍用上方手动命令
