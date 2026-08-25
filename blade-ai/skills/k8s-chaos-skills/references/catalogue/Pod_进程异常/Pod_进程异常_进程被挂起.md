**用例名称** 进程被挂起 导致 Pod_进程异常

**故障现象**：
1. 应用完全无响应但进程仍存在（与 kill 不同，进程不会退出）
2. Pod 状态仍为 Running（因为进程 PID 存在，容器未退出）
3. 所有入站请求超时，服务完全不可用
4. 若配置了 Liveness 探针，探针超时后 kubelet 会重启容器；若无 Liveness 探针则 Pod 持续 Running 但服务不可用（模拟应用死锁场景）

**资源准备**：
1. 确认目标应用已正常运行
2. 确认目标 Pod 所在 namespace 和 labels
3. 确认容器内实际进程名（不可凭服务名猜测）

**演练步骤**：
1. 记录应用当前 Pod 状态：
   ```bash
   kubectl get pods -l <labels> -n <namespace> -o wide
   ```
2. **验证实际进程名（必须）** — 进入容器确认目标进程的二进制名称：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- ps aux
   ```
   注意：实际进程名可能与服务名不同，必须以 ps 输出为准
3. 使用 ChaosBlade 注入进程挂起故障：
   ```bash
   blade create k8s pod-process stop \
     --namespace <namespace> \
     --labels "<label-key>=<label-value>" \
     --process <实际进程名> \
     --timeout <duration> \
     --kubeconfig <kubeconfig-path>
   ```
   - `--process`：目标进程名，必须与 ps aux 输出一致
   - 原理：向目标进程发送 SIGSTOP 信号，进程被内核挂起，无法处理任何请求但不会退出
4. 记录返回的 experiment_uid，用于后续恢复

**注入验证**：
1. 在目标 Pod 内确认进程状态为 T（Stopped；管道必须包在 sh -c 载荷内——命令是 argv 直传
   无 shell，裸 ps 形态下 `|`/`grep` 会沦为 ps 的字面参数，过滤静默失效）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- sh -c 'ps aux | grep <进程名>'
   ```
   procps 镜像确认 STAT 列显示 T（表示进程被 SIGSTOP 挂起）；**busybox 镜像 ps 无 STAT 列**
   （输出仅 PID/USER/TIME/COMMAND，实测 busybox:1.33），改用 /proc 判据：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- sh -c 'grep State /proc/$(pgrep -x <进程名>)/status'
   ```
   输出 `State:\tT (stopped)` 表示挂起（正常运行为 `State:\tS (sleeping)`）
2. 验证应用端口无响应：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- wget -qO- --timeout=5 localhost:<port>
   ```
   确认请求超时
3. 查看 Pod 事件确认 Liveness 探针状态：
   ```bash
   kubectl describe pod <pod-name> -n <namespace>
   ```
   若配置了 Liveness 探针，应观察到探针超时失败事件
4. 确认 Pod 状态仍为 Running（进程未退出，容器未重启——除非 Liveness 触发重启）

**注入恢复**：
1. 销毁 ChaosBlade 实验（会向进程发送 SIGCONT 恢复执行）：
   ```bash
   blade destroy <experiment_uid>
   ```
2. 若 Liveness 探针已触发容器重启，等待新 Pod Ready 即可
3. 或等待 `--timeout`（`<duration>`）到期后 ChaosBlade 自动发送 SIGCONT 恢复

**恢复验证**：
1. 确认进程恢复正常运行状态（同样包在 sh -c 载荷内，理由同注入验证；busybox 镜像用
   注入验证第 1 条的 /proc State 判据，恢复后为 `S (sleeping)`）：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- sh -c 'ps aux | grep <进程名>'
   ```
   确认 STAT 列不再显示 T
2. 验证应用端口恢复响应：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- wget -qO- --timeout=5 localhost:<port>
   ```
3. 确认服务正常处理请求，业务恢复可用

**基准事实**：
- **根因**：容器内应用主进程收到 SIGSTOP 信号被内核挂起，进程不退出但完全停止执行，无法处理任何请求
- **必现现象**：进程状态变为 T（Stopped）；应用端口请求全部超时；Pod 状态保持 Running（进程 PID 仍存在）；Liveness 探针超时失败（若已配置）

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效进程挂起注入。
> 两条路径按容器内是否有 `kill`/`pgrep` 二选一。

---

**路径 A —— 容器内有 `kill` 和 `pgrep`**

前提条件：容器内需有 `kill` 和 `pgrep` 工具。先确认：
```bash
kubectl exec <pod-name> -n <namespace> -- sh -c 'command -v kill; command -v pgrep'
```
两者都有输出才走本路径；任一缺失（distroless / scratch 等极简镜像的常态）走路径 B。

注入命令（**先武装定时恢复，再注入**；到期自动发送 SIGCONT，补齐自恢复能力）：
```bash
# 武装定时恢复（容器内后台定时器，必须重定向后台化，否则 exec 挂住）
# 两条命令分两次独立执行——不能用 && 串联：第二段 kubectl 会沦为第一条 exec
# 载荷（sh -c）的死参数，挂起静默丢失
kubectl exec <pod-name> -n <namespace> -- sh -c \
  '( sleep <duration>; kill -CONT $(pgrep -x <process-name>) ) >/dev/null 2>&1 &'
# 挂起目标进程（发送 SIGSTOP）
kubectl exec <pod-name> -n <namespace> -- sh -c 'kill -STOP $(pgrep -x <process-name>)'
```
**必须用 `pgrep -x`（按进程名精确匹配），严禁 `pgrep -f`（按 cmdline 匹配）+ `grep -vw $$` 排除的老写法**——实测后者有两处误匹配（busybox:1.33 复现）：
- ① 上面武装的定时器后台 sh 的 cmdline 含进程名字面量，注入时会被一起 SIGSTOP 冻结——定时器被冻结则到期不再触发，**自恢复链断裂**
- ② `$()`/管道的瞬时子进程在 fork 后 exec 前 cmdline 是 sh 副本（同样含进程名字面量），pgrep 扫描命中、kill 执行时已退出——报 `can't kill pid <N>: No such process`（rc=1）
- 实测对照：`pgrep -f httpd` 输出目标 PID + 瞬时 PID 两个值，`pgrep -x httpd` 只输出目标 PID
- `-x` 按进程名（comm）匹配，定时器/瞬时进程的进程名是 sh/pgrep/grep 不会命中；<process-name> 须与 `ps aux` 输出 COMMAND 列首词一致（进程名超 15 字符会被内核截断，截断名即 comm）

恢复命令：
```bash
# 恢复目标进程（发送 SIGCONT；SIGCONT 幂等，武装的定时器后续再触发也无副作用）
kubectl exec <pod-name> -n <namespace> -- sh -c 'kill -CONT $(pgrep -x <process-name>)'
```

注意事项：
- 必须确认实际进程名（通过 `ps aux` 确认），不可凭服务名猜测；确认的进程名同时就是 `pgrep -x` 的匹配词，两者必须一致
- 自恢复基于注入前武装的容器内后台定时器（sleep <duration> + SIGCONT），到期自动恢复；提前恢复仍用上方手动命令（容器内无 systemd，定时器无法像节点侧用例那样先 stop——后台 sleep 定时器不可取消，只能等它到期空触发，SIGCONT 幂等无害）
- 若 Liveness 探针已触发容器重启，进程会自动恢复（新容器中进程正常启动）
- 效果与 ChaosBlade 完全等价，ChaosBlade 内部也是发送 SIGSTOP/SIGCONT

---

**路径 B —— 容器内没有 `kill`（极简镜像）：节点侧 cgroup freezer**

从节点侧冻结容器的整个 cgroup，**不需要容器内有任何二进制**。冻结范围是该容器的
**全部进程**（而非路径 A 的单个进程），语义上更彻底。

> **仅适用 cgroup v1。** 先判定版本，v2 的路径与写法完全不同（`cgroup.freeze`，写 `1`/`0`），
> 本用例未覆盖 v2，判定为 v2 时应停止并报告不支持：
> ```bash
> kubectl debug node/<node-name> --image=<verified-cluster-image> --profile=sysadmin --quiet \
>   -- chroot /host stat -fc %T /sys/fs/cgroup
> ```
> 输出 `tmpfs` → cgroup v1，继续；输出 `cgroup2fs` → v2，**停止**。

1. 取目标容器的 Pod UID、containerID 与 QoS class（三者都是拼路径的必需项）：
   ```bash
   kubectl get pod <pod-name> -n <namespace> -o jsonpath={.metadata.uid}
   kubectl get pod <pod-name> -n <namespace> -o jsonpath={.status.containerStatuses[0].containerID}
   kubectl get pod <pod-name> -n <namespace> -o jsonpath={.status.qosClass}
   ```

2. 拼出 cgroup 路径（以下转换规则仅适用 **systemd cgroup driver** 的 `.slice`/`.scope` 布局；cgroupfs driver 布局不同，这些转换拼不出正确路径——**推荐所有场景都直接用下方 find 实测定位**）。
   systemd driver 的三处转换：
   - Pod UID 里的 `-` 全部换成 `_`（`314aae5f-2566-…` → `pod314aae5f_2566_…`）
   - containerID 去掉 `containerd://` 前缀，包成 `cri-containerd-<id>.scope`
   - **按 QoS 插入中间层**（这是最容易漏的一层）：

   | qosClass | 路径形态 |
   | --- | --- |
   | `BestEffort` | `kubepods.slice/kubepods-besteffort.slice/kubepods-besteffort-pod<UID>.slice/` |
   | `Burstable` | `kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod<UID>.slice/` |
   | `Guaranteed` | `kubepods.slice/kubepods-pod<UID>.slice/` — **没有中间层** |

   完整形态（BestEffort 示例）：
   ```
   /sys/fs/cgroup/freezer/kubepods.slice/kubepods-besteffort.slice/\
   kubepods-besteffort-pod<UID_下划线>.slice/cri-containerd-<containerID>.scope/freezer.state
   ```

   拼不出来时用 containerID 直接搜（更稳，推荐先用这个拿到真实路径）：
   ```bash
   kubectl debug node/<node-name> --image=<verified-cluster-image> --profile=sysadmin --quiet \
     -- chroot /host find /sys/fs/cgroup/freezer -type d -name *<containerID前12位>*
   ```

3. 注入 —— **必须先武装定时解冻，再冻结**。顺序错了会导致容器永久冻结：
   ```bash
   # ⚠️ 关键顺序：先用 systemd-run 登记定时 THAWED，再写 FROZEN。
   #    冻结后该容器无法 exec 进入（进程被冻结无法响应），且 debug pod 可能先于
   #    解冻被删除 —— 定时器由宿主机 systemd(PID 1) 管理，不受 debug pod 生命周期影响。
   kubectl debug node/<node-name> --image=<verified-cluster-image> --profile=sysadmin --quiet \
     -- chroot /host sh -c '
       systemd-run --on-active=<recovery-seconds>s --unit=blade-thaw-<containerID前12位> \
         sh -c "echo THAWED > <freezer.state 路径>" &&
       echo FROZEN > <freezer.state 路径>
     '
   ```

验证（读 `freezer.state`，不要试图 exec 进被冻结的容器）：
```bash
kubectl debug node/<node-name> --image=<verified-cluster-image> --profile=sysadmin --quiet \
  -- chroot /host cat <freezer.state 路径>
```
应输出 `FROZEN`。服务不可用要从**旁路**验证 —— 被冻结的容器无法 `exec` 进入，
所以借同节点上另一个正常 Pod 去访问目标 Pod IP：
```bash
# 先取目标 Pod IP，再挑一个同集群的正常 Pod 作为探测源
kubectl get pod <pod-name> -n <namespace> -o jsonpath={.status.podIP}
kubectl exec <另一个正常Pod> -n <namespace> -- wget -qO- --timeout=5 http://<目标PodIP>:<port>
```
应超时或连接失败。另可从 Endpoints 侧确认它已被摘除：
```bash
kubectl get endpoints <service-name> -n <namespace> -o wide
```

恢复：
```bash
kubectl debug node/<node-name> --image=<verified-cluster-image> --profile=sysadmin --quiet \
  -- chroot /host sh -c 'echo THAWED > <freezer.state 路径>'
```

注意事项：
- **冻结的是整个容器的所有进程**（实测一个容器的 cgroup 内有 3 个进程），不是单个进程；
  这与路径 A 的语义差异要在演练报告里说明
- **Liveness/Readiness 探针会失败** → 容器可能被 kubelet 重启 → cgroup 目录消失、故障自动结束。
  此时应记录为「故障导致重启」，且**不要再写 THAWED**（路径已不存在，写入会报错）
- 冻结期间 `kubectl exec <pod>` 会挂住 —— 所有验证都从节点侧或外部 Pod 做
- `freezer.state` 权限实测为 `-rw-r--r-- root root`，`chroot /host` 后可写
- cgroup 路径形态随节点 cgroup driver 而变：systemd driver 为 `.slice`/`.scope` 命名（上面拼路径规则），cgroupfs driver 为 `/sys/fs/cgroup/freezer/kubepods/<qos小写>/pod<UID>/<containerID>/`（实测 ACK 集群即此形态，且 Pod UID 保留连字符不转下划线、containerID 为裸 ID 不包 scope）；两种形态均以 find 实测定位为准
- 手动提前恢复后，此前武装的 timer 仍存活（pending 状态），立即重武装同名 unit 会报
  `Unit blade-thaw-<containerID前12位>.timer already exists`（实测复现；注入命令的
  `&&` 串联保证此时 FROZEN 不会写入）；先 `systemctl stop blade-thaw-<containerID前12位>.timer`
  清掉 pending timer 再重武装，或等它到期触发（transient timer 触发后自动清理，
  THAWED 幂等无害）

