**用例名称** 内存压力过大 导致 Pod_OOM内存异常

**故障现象**：
1. Pod 内存使用率接近 Limit 上限
2. 应用响应变慢，出现延迟
3. 存在被 OOMKill 的风险

**资源准备**：
1. 确认应用 A 已正常运行
2. 确认应用 A 的 Pod 已配置 resources.limits.memory

**演练步骤**：
1. 定位应用 A 的 Pod
2. 使用 chaosblade 对应用 A 的 Pod 注入内存压力，模拟内存占用增长接近 Limit 的场景
3. 观察 Pod 内存使用率变化

**注入命令**：
```bash
blade create k8s pod-mem load --mode ram --mem-percent 80 --names <Pod名> --namespace <命名空间> --kubeconfig <path> --timeout 300
```
> **必须使用 `--mode ram`**。默认的 cache 模式在 cgroup v2 环境下不会增加 Pod 的 RSS 内存占用，kubectl top 观测不到变化。`--mode ram` 直接分配匿名内存，确保 Pod 内存使用率真实上升。

**注入验证**：
1. `kubectl top pod <pod-name> -n <namespace>` 确认内存用量接近 Limit 上限（对比 resources.limits.memory）
2. 仅当已触发 OOMKill 时，`kubectl describe pod` 才会在 Events 中见到 OOMKilled；只接近 Limit 而未 OOM 时**没有任何内存相关 Event，查不到是必然，不要反复找**
3. （可选，仅当演练方提供了应用访问入口时）向入口发请求确认延迟增大；无入口时上述内存级证据成立即可判定

**注入恢复**：
1. 销毁 chaosblade 内存压力注入实验

**恢复验证**：
1. `kubectl top pod <pod-name> -n <namespace>` 确认内存用量回落到注入前基线
2. （可选，有访问入口时）确认应用响应恢复正常

**基准事实**：
- **根因**：应用内存使用增长或注入内存压力，导致 Pod 内存使用率接近 Limit，存在被 OOMKill 的风险
- **必现现象**：Pod 内存使用率接近 Limit；应用响应变慢；存在 OOMKill 风险

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效内存压力注入。

前提条件：容器内有 `stress-ng`（首选）；否则有 `python`/`perl`（多数业务容器自带）；最低保底用 `dd`（见方案 3 的驻留陷阱）

**先测基线、算增量（必须，防超量 OOMKill）**：
```bash
# 当前 Pod 内存用量
kubectl top pod <pod-name> -n <namespace> --no-headers
# Pod 内存 limit
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[0].resources.limits.memory}'
```
**分配量 = limit × 目标百分比 − 当前用量**。例：limit=8Gi、当前 3.3Gi、目标 80% → 8×0.8 − 3.3 ≈ 3.1G。
**严禁直接按「limit × 目标百分比」的绝对值分配**——Pod 已有基础用量，超量会越过剩余余量直接触发 OOMKill，注入进程被杀、效果不出现。

注入命令：
```bash
# 方案1：指定绝对大小（推荐，精确控制；后台+重定向让 exec 立即返回，--timeout 自带自动恢复）
kubectl exec <pod-name> -n <namespace> -- \
  sh -c 'stress-ng --vm 1 --vm-bytes <按上式算出的分配量，如 3G> --timeout <duration>s >/dev/null 2>&1 &'
# 方案2：无 stress-ng 时用分块分配（推荐，python/perl 容器普遍自带）。
# 关键：必须分块逐段分配——一次性构造全量（如 bytearray(N) 或 perl "x"xN）有 ~2 倍瞬时峰值，
# 会越过余量直接 OOMKill。分块后瞬时峰值只有一个块，且 python 不可用时可换 perl 同构写法：
kubectl exec <pod-name> -n <namespace> -- sh -c 'cat > /tmp/mem_stress.py << "EOF"
import time
chunks = []
for _ in range(<MB> // 100):        # 每块 100MB，块数 = 分配量(MB)/100
    chunks.append(bytearray(100 * 1024 * 1024))
    time.sleep(0.2)
with open("/tmp/memcache-warmup.pid", "w") as f:
    f.write(str(__import__("os").getpid()))
time.sleep(<duration>)              # 到期进程退出即自动释放
EOF
nohup python /tmp/mem_stress.py >/dev/null 2>&1 &'
# 方案3：仅有 dd 时，必须用 sleep 挂住管道保持驻留——`( dd … | tail )` 单独用有驻留缺陷：
# dd 拷贝一结束 tail 即退出、内存立即释放，实测内存冲高后 30 秒内塌回基线；
# sleep 让管道 EOF 延迟到 <duration> 后，tail 的缓冲才能撑住全程。
# 另注意：dd 是逐块拷贝，速度慢于方案 2 的匿名内存直接分配：
kubectl exec <pod-name> -n <namespace> -- sh -c '
  ( ( dd if=/dev/zero bs=1M count=<MB> 2>/dev/null; sleep <duration> ) | tail ) >/dev/null 2>&1 &
  echo $! > /tmp/memcache-warmup.pid
'
```
> **不要走 tmpfs 文件写路线**（`dd of=/dev/shm/…`）：部分环境对页缓存写入路径限速，实测可低至 ~0.5MB/s（3.5G 需 1 小时以上）；匿名内存分配（方案 2）同环境实测 <30 秒完成同量级分配。

恢复命令（从精确到兜底）：
```bash
# stress-ng：kill 进程
kubectl exec <pod-name> -n <namespace> -- sh -c 'pkill -f stress-ng 2>/dev/null'
# 分块分配：按落盘 PID kill（python 方案）
kubectl exec <pod-name> -n <namespace> -- \
  sh -c 'kill $(cat /tmp/memcache-warmup.pid) 2>/dev/null; rm -f /tmp/memcache-warmup.pid /tmp/mem_stress.py'
# dd 管道方案同上式（kill 管道进程即可释放）
# 兜底：ps+kill（比 pkill 通用）
kubectl exec <pod-name> -n <namespace> -- \
  sh -c "ps -o pid,args 2>/dev/null | grep -E '[s]tress-ng|[m]em_stress|[d]d if=/dev/zero' | awk '{print \$1}' | xargs -r kill -9"
```

注意事项：
- stress-ng `--vm-bytes` 按系统内存百分比计算，非 Pod cgroup 百分比，需手动转算绝对值
- **严禁一次性构造全量分配**（`bytearray(总量)`、`perl "x"x总量`）：瞬时峰值约为目标量 2 倍，即使增量算对了也会越过余量被 OOMKill——分块（每块 100MB + 短间隔）是唯一稳妥写法
- dd 管道方案驻留时长 = sleep 挂管道的时长（到期 EOF → tail 退出 → 释放），且逐块拷贝速度慢于分块分配，仅作无 python/perl 时的保底
- 无 cgroup 感知能力，可能直接触发 OOMKill 而非停留在“接近 Limit”状态
- **量必须按增量算**（分配量 = limit × 目标百分比 − 当前用量）。按目标百分比的绝对值直接分配会越过剩余余量触发 OOMKill——注入进程被杀、效果不出现，表现为「执行了但 top 无变化」
- **注入后 top 冲高又塌回基线时，按顺序排查**：① `kubectl describe pod` 查 Events 是否有 OOMKill——有则是分配过量被杀，按增量重算后重试同一方法；② 无 OOMKill 则是驻留载体已退出（典型如 dd|tail 未挂 sleep，dd 结束 tail 即退出），换持续驻留的载体（方案 2 分块分配），**不要**误判为「该容器无法驻留内存」
