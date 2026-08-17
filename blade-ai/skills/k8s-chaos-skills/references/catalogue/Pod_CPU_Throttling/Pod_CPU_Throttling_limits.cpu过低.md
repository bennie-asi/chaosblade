**用例名称** limits.cpu过低 导致 Pod_CPU_Throttling

**故障现象**：
1. Pod 内进程频繁被内核 CPU 节流（throttle），`cpu.stat` 中 `nr_throttled` 持续增长
2. 应用请求延迟显著增大，P99 延迟飙升
3. `kubectl top pod` 显示 CPU 使用率接近 limits 但实际未满载

**资源准备**：
1. 确认应用 A 已正常运行，且 Pod 配置了 resources.limits.cpu
2. 确认监控系统可观测 Pod CPU 使用率及 throttle 指标

**演练步骤**：
1. 定位应用 A 的 Deployment，并记录其 CPU limits 原始值
2. **先武装定时恢复，再注入**（在运行 kubectl 的机器上后台武装，到期自动将 CPU limits 还原为
   原始值，补齐自恢复能力；PID 落盘供提前恢复时终止定时器）：
   ```bash
   # 记录原始 limits（多容器 Pod 请调整 containers 索引至目标容器）
   ORIG_CPU=$(kubectl get deployment <deployment-name> -n <namespace> \
     -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}')
   # 武装定时还原
   ( sleep <duration>; kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
       -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"${ORIG_CPU}\"}]" ) >/dev/null 2>&1 &
   echo $! > /tmp/blade-restore-cpulimit.pid
   # 再调低 limits 注入
   kubectl patch deployment <deployment-name> -n <namespace> --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/cpu","value":"<调低后的limits值>"}]'
   ```
3. 使用 chaosblade 对应用 A 的 Pod 注入 CPU 负载，确保实际 CPU 需求超过 limits，触发内核 throttle
4. 观察 Pod CPU throttle 指标变化及应用响应延迟

**注入验证**：
1. 进入容器查看 `/sys/fs/cgroup/cpu/cpu.stat`，确认 `nr_throttled` 和 `throttled_time` 持续增长
2. `kubectl top pod <pod-name> -n <namespace>` 确认 Pod CPU 使用率接近 limits
3. （可选，仅当演练方提供了应用访问入口时）确认请求延迟显著增大；无入口时上述 throttling 与 CPU 证据成立即可判定

**注入恢复**：
1. 等待 `<duration>` 到期后武装的定时器自动将 CPU limits 还原为原始值；如需提前恢复，先终止定时器：
   ```bash
   kill $(cat /tmp/blade-restore-cpulimit.pid) 2>/dev/null; rm -f /tmp/blade-restore-cpulimit.pid
   ```
2. 销毁 chaosblade CPU 负载实验
3. 使用 kubectl patch 将应用 A 的 CPU limits 恢复为原始合理值

**恢复验证**：
1. 查看 `cpu.stat`，确认 `nr_throttled` 停止增长
2. （可选，有访问入口时）确认请求延迟恢复正常

**基准事实**：
- **根因**：容器 limits.cpu 设置过低，实际 CPU 需求超过 limit，内核对容器 CPU 时间片进行 throttle，导致应用性能下降
- **必现现象**：cpu.stat 中 nr_throttled 持续增长；应用延迟飙升；CPU 使用率接近 limits 上限

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效 CPU 负载注入。

前提条件：容器内有 `stress-ng` 时优先使用；否则用 shell 循环替代。多容器 Pod 请用 `-c <container>` 指定目标容器。

注入命令：
```bash
# 方式一：容器内有 stress-ng（后台+重定向让 exec 立即返回，--timeout 自带自动恢复）
kubectl exec <pod-name> -n <namespace> -c <container> -- \
  sh -c 'stress-ng --cpu 0 --cpu-load <percent> --timeout <duration>s >/dev/null 2>&1 &'
# 方式二：容器无 stress-ng，用 shell 循环（重定向避免 exec 挂起；PID 落盘定时自动 kill）
kubectl exec <pod-name> -n <namespace> -c <container> -- sh -c '
  : > /tmp/loadgen-worker.pids
  for i in $(seq 1 <N>); do
    ( while :; do :; done ) >/dev/null 2>&1 &
    echo $! >> /tmp/loadgen-worker.pids
  done
  ( sleep <duration>; kill $(cat /tmp/loadgen-worker.pids) 2>/dev/null; rm -f /tmp/loadgen-worker.pids ) >/dev/null 2>&1 &
'
```

恢复命令（从精确到兜底）：
```bash
# stress-ng：kill 进程
kubectl exec <pod-name> -n <namespace> -c <container> -- pkill -f stress-ng
# shell 循环 首选：按注入落盘的 PID 精确 kill
kubectl exec <pod-name> -n <namespace> -c <container> -- \
  sh -c 'kill $(cat /tmp/loadgen-worker.pids) 2>/dev/null; rm -f /tmp/loadgen-worker.pids'
# 兜底：ps+kill（比 pkill 通用，精简镜像常无 pkill）
kubectl exec <pod-name> -n <namespace> -c <container> -- \
  sh -c "ps -o pid,args 2>/dev/null | grep '[w]hile :' | awk '{print \$1}' | xargs -r kill -9"
```

注意事项：
- shell 循环单个只能打满单核，需按 CPU 上限起 N 个循环逼近目标百分比；精细百分比应优先 stress-ng
- shell 循环命令必须重定向 `>/dev/null 2>&1`，否则占住 exec 输出管道导致 `kubectl exec` 挂起到 10s 超时
- 自动恢复基于 PID 文件 + 定时 kill，可靠；切勿用 `$(jobs -p)` 定时自杀（脱离子 shell 取不到 PID）
- 精度不如 ChaosBlade 的 cgroup 级 CPU 控制
