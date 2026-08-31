# JVM DataSource 连接池耗尽 E2E 证据

## 1. 结论

**最终结果：PASS。** 2026-08-31 在用户指定的 `bennieliu-honor` 隔离 k3s 环境完成以下强制门禁：

- HikariCP 连接池达到 4/4，业务查询返回 HTTP 503，显式 `destroy` 后恢复 HTTP 200。
- Druid 连接池达到 4/4，业务查询返回 HTTP 503，60 秒硬 TTL 后无需 CLI 后台任务即可恢复 HTTP 200。
- 一个有效 JVM Pod 与一个无效 Pod 同时命中时，已成功子实验按精确 child UID 自动补偿，CR 状态为 `ROLLED_BACK`，有效 Pod 无连接残留。
- CR 保留逐资源结构化结果；Host 与 Kubernetes 动态命令均暴露新参数，旧 `druid connectionpoolfull` 命令仍存在。
- 最终无 ChaosBlade CR、无 mixed 测试 Pod、两个业务池 `active=0`，两个 `/query` 均为 HTTP 200。

## 2. 环境与构建身份

| 项目 | 实测值 |
|---|---|
| 主机 | `bennieliu-honor`，Linux `7.0.0-30-generic` x86_64 |
| Kubernetes | k3s `v1.35.7+k3s1` |
| Docker / Go / Maven | Docker `29.7.2`；Go `1.26.0`；Maven `3.9.12` |
| Java 矩阵 | OpenJDK `8u502`、`11.0.32`、`17.0.20` |
| Namespace | `chaosblade-e2e` |
| 根仓功能提交 | `b3e85917c696ba77b37a24d79ae8cdbc7c87a632` |
| JVM 仓功能提交 | `647e492a53d168ee4ff03f957c17351335dbffcc` |
| Operator 仓功能提交 | `41bab875e3c0957310e8afae8c6d2a262d98f0a1` |
| App 镜像 | `chaosblade-e2e/datasource-pool:local`，image id `99312921a80d` |
| Tool 镜像 | `chaosblade-e2e/chaosblade-tool:local`，image id `40cc7c7edc35` |
| Operator 镜像 | `chaosblade-e2e/chaosblade-operator:local`，image id `bdbd5e1601dd` |

测试包基于发布兼容线 `chaosblade 1.8.0` 组装，并覆盖本变更的 root/JVM/Operator 文件；完成验证后，相同内容分别固化为上表三个提交。这样既避免根仓当前主分支已有的 CRI 依赖漂移影响发布线验证，也保证运行产物包含本功能的 Agent、YAML、CLI、Operator 与 CRD。

## 3. 构建与回归门禁

| 门禁 | 结果 |
|---|---|
| `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am test` | PASS，7 tests，0 failure/error |
| `make build`（JVM Agent） | PASS；Agent JAR 与 `chaosblade-jvm-spec-1.8.0.yaml` 已生成，spec 含 datasource、新 Action 和三个默认参数 |
| `test/compatibility/datasource/run-matrix.sh` | PASS，6 组：JDK 8/11 + Boot 2.7、JDK 17 + Boot 3.2，Hikari/Druid 均完成热 attach、destroy、TTL |
| `go test -race ./exec/... ./pkg/apis/chaosblade/v1alpha1/... -count=1`（Operator） | PASS |
| `go test ./exec/jvm -count=1`（1.8 发布兼容线） | PASS |
| `go test ./apis/chaosblade/v1alpha1 -count=1`（根仓） | PASS |
| E2E App `mvn test` | PASS，Hikari/Druid 2 tests |
| `verify-make-overrides.sh` | PASS，默认值、四项 override、空格路径拒绝均通过 |
| `run-e2e.sh --self-test` | PASS |
| `shellcheck`（全部 E2E/matrix 脚本） | PASS，无 warning/error |

根仓主分支组合测试还有两个与本变更无关的既有门禁问题，已保留原始区分而未掩盖：

1. 当前根仓依赖 `chaosblade-exec-cri v1.8.0`，但解析到的新 Docker/containerd API 已删除 `types.ContainerListOptions` 等类型，导致 `go test ./cli/cmd ...` 在编译 CRI 依赖时失败；`exec/jvm` 与本地 API 聚焦测试均通过。
2. 在 1.8 发布线用 Go 1.26 单独启动 `cli/cmd` 测试时，旧包初始化提前解析参数，报 `flag provided but not defined: -test.paniconexit0`。真实 CLI 帮助、创建、销毁及 Kubernetes E2E 均已执行成功。

## 4. Kubernetes 强制场景

统一创建参数：

```text
blade create k8s container-datasource connectionpoolfull \
  --data-source-name coreDataSource --target-percent 100 --timeout 60 \
  --pid 1 --javaHome /opt/java --chaosblade-override
```

### 4.1 HikariCP：显式恢复

- 自动化父 UID：`03657c188a825d70`；子 UID：`ebb9aec785d5fde3`。
- 结构化结果：`poolType=HIKARI`、`maximumBefore=4`、`activeBefore=0`、`targetConnections=4`、`actualHold=4`、`activeAfter=4`、`state=ACTIVE`。
- 故障效果：`/pool` 为 `active=4,idle=0`；`/query` 返回 HTTP 503，手工定量复核约 `1.014s`，错误为连接获取超时。
- 恢复：按父 UID执行 Kubernetes destroy；随后 `/pool active=0`，`/query` HTTP 200，手工复核约 `0.03s`。

### 4.2 Druid：硬 TTL 恢复

- 自动化父 UID：`93801a856c47d29a`；子 UID：`c1c2f26ebd521a4c`。
- 结构化结果：`poolType=DRUID`、`maximumBefore=4`、`activeBefore=0`、`targetConnections=4`、`actualHold=4`、`activeAfter=4`、`state=ACTIVE`。
- 故障效果：`/pool` 为 `active=4,idle=0`；`/query` 返回 HTTP 503，手工定量复核约 `1.01s`，错误为 `GetConnectionTimeoutException`。
- 恢复：不调用 destroy，等待 60 秒 Agent 硬 TTL；随后 `/pool active=0`，`/query` HTTP 200，手工复核约 `0.026s`。

### 4.3 多 Pod 部分失败原子补偿

- 父 UID：`8ca812fbfb41a746`。
- 有效 Hikari Pod 子 UID：`285f1f19c42d4141`，创建先达到 `ACTIVE`、`actualHold=4`。
- 无效 Pod：`datasource-invalid`，不具备 Java/目标执行环境，返回部署失败。
- Operator 在 fan-out 收敛后执行 `blade destroy 285f1f19c42d4141`，有效资源最终状态精确为 `ROLLED_BACK`，并保留原始失败原因。
- 补偿后 Hikari `/pool active=0`、`/query` HTTP 200；手工复核约 `0.004s`。这证明不是只等待 TTL 被动恢复。

## 5. 动态命令与兼容性

- `blade create datasource connectionpoolfull --help`：PASS。
- `blade create k8s container-datasource connectionpoolfull --help`：PASS。
- 两者均显示：`--data-source-name` 默认 `coreDataSource`、`--target-percent` 默认 `100`、`--timeout` 默认 `60`。
- `blade create druid connectionpoolfull --help`：PASS，旧 target/action 未被替换。
- 兼容矩阵各组合均验证：应用先启动、再由 Agent 热 attach；Hikari/Druid 都经历 `active 0 -> 4 -> 0`，业务状态 `200 -> 503 -> 200`，且 3 秒矩阵 TTL 能独立释放。

## 6. 清理核验

最终只读核验输出：

```text
kubectl get chaosblade -A: no resources
kubectl -n chaosblade-e2e get pod -l scenario=pool-mixed: no resources
HIKARI active=0; /query HTTP 200
DRUID  active=0; /query HTTP 200
compatibility ports 18081-18086: no listener
compatibility JVM processes: none
```

连接池的 idle 数由池自身异步补齐，不作为恢复硬判据；本 UID持有连接全部关闭、Holder/identity 注销、`active=0` 和业务查询恢复共同构成已验证的无故障残留结论。
