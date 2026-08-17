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

注入命令（**先武装定时还原，再注入**；到期自动还原原始镜像，补齐自恢复能力；PID 落盘供提前恢复时终止定时器）：
```bash
# 记录原始镜像并武装定时还原
ORIG_IMAGE=$(kubectl get deployment <deployment-name> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].image}')
( sleep <duration>; kubectl set image deployment/<deployment-name> -n <namespace> \
    <container-name>=$ORIG_IMAGE ) >/dev/null 2>&1 &
echo $! > /tmp/blade-restore-image.pid
# 再修改镜像为不存在的版本
kubectl set image deployment/<deployment-name> -n <namespace> <container-name>=<原始镜像>:non-existent-tag
```

恢复命令：
```bash
# 提前恢复时先终止武装的定时器
kill $(cat /tmp/blade-restore-image.pid) 2>/dev/null; rm -f /tmp/blade-restore-image.pid
# 恢复为原始镜像版本
kubectl set image deployment/<deployment-name> -n <namespace> <container-name>=<原始镜像>:<原始标签>
```

注意事项：
- ChaosBlade `pod-pod fail` 直接修改 Pod spec 中的镜像，kubectl-native 方式通过 Deployment 触发滚动更新
- 恢复时需记住原始镜像地址和标签
- 自恢复基于注入前武装的后台定时器（sleep <duration> + set image 还原），到期自动还原原始镜像；提前恢复仍用上方手动命令
