**用例名称** 端口被占用 导致 Pod_网络故障

**故障现象**：
1. Pod 内服务端口被强制占用，应用无法监听预期端口
2. 应用启动失败或现有连接中断，日志报 `Address already in use`
3. 健康检查可能失败（如探针使用被占端口），导致 Pod 被重启
4. Service 端点无法正常接收流量

**资源准备**：
1. 确认目标应用已正常运行，且在特定端口提供服务
2. 确认目标 Pod 的标签选择器和命名空间
3. 确认需要占用的端口号（应用监听的端口）

**演练步骤**：
1. 确认目标 Pod 的标签选择器和命名空间：
   ```bash
   kubectl get pods -n <namespace> -l <label-selector> -o wide
   ```
2. 确认目标端口当前正在被应用正常使用：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- netstat -tlnp
   ```
3. 使用 ChaosBlade 对目标 Pod 注入端口占用：
   ```bash
   blade create k8s pod-network occupy \
     --namespace <namespace> \
     --labels "<label-key>=<label-value>" \
     --port <port> \
     --force \
     --timeout <duration> \
     --kubeconfig <kubeconfig-path>
   ```
   - `--port`：要占用的端口（必填）
   - `--force`：强制杀死当前使用该端口的进程后占用（不加则端口被占时注入失败）
4. 记录返回的 experiment_uid，用于后续恢复

**注入验证**：
1. 确认端口已被 ChaosBlade 进程占用：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- netstat -tlnp
   ```
   确认监听进程已变更为 ChaosBlade 注入的进程
2. 确认原应用进程已停止监听该端口（使用 `--force` 时原进程被杀）
3. 验证服务不可达：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- wget -qO- --timeout=5 http://localhost:<port>
   ```
   确认连接失败或返回非预期响应
4. 查看应用日志确认进程被杀/端口冲突相关错误

**注入恢复**：
1. 销毁 ChaosBlade 实验：
   ```bash
   blade destroy <experiment_uid>
   ```
2. 被杀的应用进程通常由容器 init 系统或 K8s 探针重启机制自动恢复
3. 若应用未自动恢复，可重启 Pod：`kubectl delete pod <pod-name> -n <namespace>`

**恢复验证**：
1. 确认应用重新监听目标端口：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- netstat -tlnp
   ```
2. 确认服务恢复可达：
   ```bash
   kubectl exec <pod-name> -n <namespace> -- wget -qO- --timeout=5 http://localhost:<port>
   ```
3. 确认 Pod 状态为 Running 且 Ready
4. 确认 Service Endpoints 包含该 Pod

**基准事实**：
- **根因**：Pod 内目标端口被 ChaosBlade 强制占用，原应用进程被杀死（`--force`），模拟端口冲突或进程异常退出场景
- **必现现象**：应用进程停止监听目标端口；服务请求失败；健康检查可能失败触发 Pod 重启

---

**降级方案（kubectl-native）**

> 当 ChaosBlade 不可用时，可使用以下 kubectl 原生命令实现等效 Pod 内端口占用。

前提条件：容器内需有 `nc`（netcat）或 `socat` 工具。两个实测陷阱（busybox:1.33 实测确证）：
1. **杀原进程不能用裸 `fuser <port>/tcp`**：busybox fuser 按 `/proc/net/tcp`（IPv4）与
   `/proc/net/tcp6`（IPv6）分表查询，监听形态决定查哪张表（实测：IPv4-only 监听
   `127.0.0.1:port` 时 `fuser <port>/tcp` 正常输出 PID；IPv6 双栈 `:::port` 时
   `fuser <port>/tcp` 返回空 RC=1，须用 `fuser <port>/tcp6`）。原应用监听形态不确定时
   裸 tcp 查询可能静默不杀原进程 → 占用进程 bind 失败退出 → 注入静默失败且无任何报错。
   改用 `pkill -x <原应用进程名>` 按进程名精确匹配（不依赖监听形态）；**不要用
   pkill -f**——载体 sh -c 命令行含模式串时会把自己杀掉（实测 RC=143）
2. **busybox nc 持久监听必须配 -e，且 -k 不可移植**：无 -e 的监听形态（`nc -l -p <port>`）
   在首个连接处理后即退出，占用不持久；必须配 `-e cat` 才能持续抢住端口。`-k` 选项
   仅部分 busybox 编译版支持（nc110 选项集定制版实测 `-lk -e cat` 持久；官方
   busybox.net 1.33.1 源码无 -k，遇 -k 直接报 usage 退出）——可移植形态是重复 -l：
   `nc -l -l -p <port> -e cat`（官方源码语义：多次 -l 时父进程 fork 循环 accept；
   定制编译版实测同样跨连接持久，echo 正常完成）

注入命令：
```bash
# 通过 kubectl exec 在 Pod 内占用端口（先杀原进程 → nc 抢占 → PID 落盘 → 定时自恢复；
# 后台进程必须重定向，否则 exec 会挂到超时）
kubectl exec <pod-name> -n <namespace> -- sh -c '
  pkill -x <原应用进程名> 2>/dev/null
  ( nc -l -l -p <port> -e cat ) >/dev/null 2>&1 &
  echo $! > /tmp/portbind-agent.pid
  ( sleep <duration>; kill $(cat /tmp/portbind-agent.pid) 2>/dev/null; rm -f /tmp/portbind-agent.pid ) >/dev/null 2>&1 &
'
# 如容器无 nc，可用 socat（socat fork 模式本身就是持久服务；同样重定向 + PID 落盘 + 定时自恢复）：
kubectl exec <pod-name> -n <namespace> -- sh -c '
  pkill -x <原应用进程名> 2>/dev/null
  ( socat TCP-LISTEN:<port>,fork,reuseaddr /dev/null ) >/dev/null 2>&1 &
  echo $! > /tmp/portbind-agent.pid
  ( sleep <duration>; kill $(cat /tmp/portbind-agent.pid) 2>/dev/null; rm -f /tmp/portbind-agent.pid ) >/dev/null 2>&1 &
'
```

恢复命令：
```bash
# 首选：按 PID 文件精确终止占用进程（实测 timer 到期自愈与手动恢复均验证通过）
kubectl exec <pod-name> -n <namespace> -- sh -c 'kill $(cat /tmp/portbind-agent.pid) 2>/dev/null; rm -f /tmp/portbind-agent.pid'
# 兜底：PID 文件丢失时，用 netstat -tlnp 定位当前监听 <port> 的 PID 后 kill
# （注意 ps+grep 定位有自匹配陷阱：载体 sh -c 行的命令行含 "nc -l" 关键词，会被
# grep 命中——[n]c 技巧只能排除 grep 自身，排除不了载体行；netstat 看监听 socket
# 最准）
kubectl exec <pod-name> -n <namespace> -- netstat -tlnp
# 应用进程通常由容器 init 系统或 K8s 探针重启机制自动恢复；若未恢复，重启 Pod：
kubectl delete pod <pod-name> -n <namespace>
```

注意事项：
- 无 nc/socat 时本降级方案不可用：busybox ash **不支持 /dev/tcp**（实测报 nonexistent
  directory），shell 内置重定向占位的思路在 busybox 行不通；只能走 ChaosBlade 主方案
  或镜像内自带的长驻占位程序
- 后台占用进程必须 `>/dev/null 2>&1 &`，否则继承 exec 管道会导致 `kubectl exec` 挂起到超时
  （进程其实已启动；实测占用进程跨 exec 会话存活，stdin EOF 不会杀掉它）
- 自动恢复基于 PID 文件（`/tmp/portbind-agent.pid`）+ 定时 kill 补齐了 ChaosBlade
  `--timeout` 的自恢复能力；恢复兜底用 netstat -tlnp 定位 PID（fuser 须按监听形态选
  tcp/tcp6 表，见前提条件陷阱 1；netstat 不受监听形态限制最通用）
- `--force` 效果通过先 `pkill -x <原应用进程名>` 实现，会杀死当前监听该端口的进程
- 验证时 ps 里可能残留已 kill 进程的 zombie（PID 1 为 sleep 不收尸，busybox 经典
  现象）：zombie 不持有 socket、不占用端口，netstat 里无监听即为已恢复，无需处理
  （随 Pod 重建消失）
