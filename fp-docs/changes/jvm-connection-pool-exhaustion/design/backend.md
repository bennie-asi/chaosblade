# JVM 应用连接池耗尽故障能力 — 后端技术方案设计

## 第一部分：架构决策

### Decision Ledger

| ID | Decision | Source | Blocking | Status | Evidence / explicit confirmation |
| --- | --- | --- | --- | --- | --- |
| D-001 | 新增通用 JVM target `datasource` 和 Action `connectionpoolfull`，保留旧 `druid connectionpoolfull` 行为 | 本轮 D-001 方案选择 | yes | `user-confirmed` | D-001: selected A（新增通用 target/action，旧命令不变）；user message `A` immediately after the D-001 prompt |
| D-002 | 保留 proposal 全部正式范围，并先用现有 K8s Druid 能力完成 P0 快速效果验证 | 本轮 D-002 交付路径选择 | yes | `user-confirmed` | D-002: selected A（P0 快速验证 + P1 正式能力）；user message `A` immediately after the D-002 prompt |
| D-003 | 通过 Spring Boot Context/BeanFactory 捕获与精确 Bean 名查找定位 DataSource，多上下文同名时失败关闭 | 本轮 D-003 DataSource 定位选择 | yes | `user-confirmed` | D-003: selected A（BeanFactory 捕获、精确 Bean 名、歧义失败）；user message `A` immediately after the D-003 prompt |
| D-004 | Agent 使用 UID 隔离的内存 Holder 注册表和默认 60 秒不可续期硬 TTL | 本轮 D-004 生命周期选择 | yes | `user-confirmed` | D-004: selected A（UID Holder、硬 TTL、幂等释放、Agent 卸载清理）；user message `A` immediately after the D-004 prompt |
| D-005 | 仅为新 Action 增加 Operator 侧多 Pod 原子补偿，任一目标失败即回滚全部成功目标 | 本轮 D-005 多目标恢复选择 | yes | `user-confirmed` | D-005: selected A（新 Action 专用原子补偿）；user message `A` immediately after the D-005 prompt |
| D-006 | 整体采用定位器、连接池适配器、实验注册表和 Operator 协调器组成的分层插件架构 | 本轮 D-006 整体方案选择 | yes | `user-confirmed` | D-006: selected A（分层插件与连接池适配器）；user message `A` immediately after the D-006 prompt |
| D-007 | 同一 DataSource 同时只允许一个活动实验；相同 UID 创建幂等，不同 UID 冲突时拒绝 | 本轮 D-007 并发模型选择 | yes | `user-confirmed` | D-007: selected A（DataSource 级互斥，不同 DataSource 可并行）；user message `A` immediately after the D-007 prompt |
| D-008 | `create` 内同步完成前置校验，不新增 `validate` Action 或 `validate-only` 模式 | 本轮 D-008 命令契约选择 | yes | `user-confirmed` | D-008: selected A（创建前内部校验、最小命令面）；user message `A` immediately after the D-008 prompt |
| D-009 | 完全恢复以本 UID 连接全部关闭、Holder 清空并注销为硬判据；池指标只作为观测证据 | 本轮 D-009 恢复判据选择 | yes | `user-confirmed` | D-009: selected A（所有权判据 + 恢复后指标）；user message `A` immediately after the D-009 prompt |
| D-010 | 首版采用 JDK 8/11 + Spring Boot 2.7 与 JDK 17 + Spring Boot 3.2 的最小代表性兼容矩阵 | 本轮 D-010 验证范围选择 | yes | `user-confirmed` | D-010: selected A（代表性矩阵，其他版本运行时能力探测）；user message `A` immediately after the D-010 prompt |
| D-011 | 设计采用 backend-only small form，入口为 `design/00-index.md` 和 `design/backend.md`，无迁移或删除 | 本轮 D-011 产物结构选择 | yes | `user-confirmed` | D-011: selected A（small form、仅后端、所列 exact paths、无 obsolete paths）；user message `A` immediately after the D-011 prompt |

### 已确认决策说明

#### 能力边界与交付顺序

- 正式能力是新的 `datasource connectionpoolfull`，语义固定为从应用自身 DataSource `borrow-hold`；不执行长查询、未提交事务或任意 SQL。
- 旧 `druid connectionpoolfull` 保持原名称、参数、全局 Holder 和恢复语义，不迁入新注册表，避免扩大回归面。
- P0 使用现有 Druid/K8s 场景快速证明 Pod 选择、Agent attach、连接耗尽效果和 destroy 恢复；P0 不能替代新能力验收。

#### 定位、隔离与生命周期

- Spring Boot 上下文按应用 ClassLoader 隔离，Agent 仅保存弱引用；按 Bean 名精确查找，默认 `coreDataSource`。
- DataSource 身份键为 `ClassLoader identity + ApplicationContext identity + canonical Bean name`。同一身份一次只允许一个活动 UID，不同 DataSource 可并行。
- 实验连接只存在于 Agent 内存中，硬 TTL 默认 60 秒且任何状态查询或控制请求都不能续期；主动 destroy、创建回滚、TTL 和 Agent 卸载共用同一幂等释放入口。

#### 命令、恢复与兼容性

- `create` 原子地完成 Bean 定位、适配器识别、指标读取、探测借还、正式借出和达标确认，不增加独立校验命令。
- 恢复成功不以连接池整体活跃数回落为条件，因为正常业务可以并发借用其他连接；恢复前后指标必须随结果返回。
- 新 Action 在 Operator 中启用专用原子补偿；其他 Action 沿用现有多资源行为。
- 首版保证 HikariCP 与 Druid 的代表性版本组合，未列版本按运行时能力探测，探测失败时明确拒绝而非猜测兼容。

### Pre-write Confirmation Evidence

- Covered IDs: `D-001`, `D-002`, `D-003`, `D-004`, `D-005`, `D-006`, `D-007`, `D-008`, `D-009`, `D-010`, `D-011`
- Outstanding blocking decisions: `none`
- Explicit user authorization to write: user message ``确认并按 D-001 至 D-011 写入 small form 设计文档``，确认采用 backend-only small form，创建 `fp-docs/changes/jvm-connection-pool-exhaustion/design/00-index.md` 与 `fp-docs/changes/jvm-connection-pool-exhaustion/design/backend.md`，且不覆盖、迁移或删除现有设计文件。

## 第二部分：技术方案详述

### 1. 目标与非目标

#### 1.1 设计目标

- 在已经运行的 Spring Boot JVM 内按 Bean 名定位 HikariCP 或 Druid DataSource。
- 根据每个 JVM 的实时连接池状态计算并持有连接，使实际占用率达到目标值。
- 使用实验 UID 精确登记、查询和释放本次实验创建的连接。
- 主机 JVM 和 Kubernetes Pod 复用同一个 Agent 实现；K8s 返回逐 Pod/容器状态并对部分失败自动补偿。
- 默认参数可直接用于快速验证，同时保持现有 JVM Action 和旧 Druid 命令兼容。

#### 1.2 非目标

- 不实现 SQL、长查询、事务不提交、数据库锁、数据库服务端连接耗尽或业务压测流量。
- 不支持非 Spring Boot 应用、非 HikariCP/Druid 连接池或跨 Pod 自动追踪新副本。
- 不接收、保存或输出数据库账号密码，不修改 `cw-chaos` 产品层、页面或编排原子。

### 2. 当前集成基线与变更边界

| 所有者 | 当前事实 | 本设计变更 |
| --- | --- | --- |
| `chaosblade` | `cli/cmd/exp.go` 从 JVM YAML 动态注册模型，并将 K8s JVM 模型映射为 `container` scope | 不硬编码新命令；验证 `datasource` 自动注册为主机 target，并在 K8s 下显示为 `container-datasource` |
| `chaosblade` | `exec/jvm/executor.go` 将 create 请求 POST 到本机 Sandbox，但当前过滤 `timeout` | 仅对 `datasource/connectionpoolfull` 把 `timeout` 转发给 Agent；其他 Action 继续过滤，保持兼容 |
| `chaosblade` | `exec/kubernetes/executor.go` 创建 ChaosBlade CR 并透传 `ResourceStatus` | 支持新 Action 的可选结构化结果并保留逐资源回滚状态 |
| `chaosblade` | `apis/chaosblade/v1alpha1.ResourceStatus` 当前只有身份、状态、错误和成功标志 | 增加可选 `result` JSON 对象；旧 Action 省略该字段，CRD schema 与 deepcopy 同步更新 |
| `chaosblade-exec-jvm` | v1.8.0 构建依赖已有 Druid 专用 `connectionpoolfull`，但无 Bean 名、目标比例和 UID 隔离 | 新增通用 `datasource` 模型、Spring 定位器、Hikari/Druid Adapter、Holder 注册表与状态接口；旧模块不改语义 |
| `chaosblade-operator` | v1.8.0 构建依赖按资源执行并记录状态，创建部分失败不会立即补偿成功目标 | 对新 Action 收集成功子 UID 并立即执行补偿，结果写回每个资源状态 |
| 构建产物 | `Makefile` 固定拉取 JVM 与 Operator v1.8.0 分支 | 外部组件形成匹配提交后统一锁定分支或 tag，保证 CLI、YAML、Agent、Operator 和 CRD 同版交付 |

### 3. 总体架构

```text
blade create / blade create k8s
              │
              ▼
      JVM YAML 动态注册层
              │
       host ──┴── container scope
              │
              ▼
       JVM Executor / Operator
              │  localhost Sandbox HTTP
              ▼
  DataSourceConnectionPoolFullExecutor
       │          │             │
       ▼          ▼             ▼
 SpringContext  PoolAdapter  ExperimentRegistry
 Resolver       Hikari/Druid   UID Holder + TTL
       │          │             │
       └──────────┴──────┬──────┘
                         ▼
                目标应用 DataSource
```

#### 3.1 JVM Agent 组件

| 组件 | 职责 |
| --- | --- |
| `DataSourceConnectionPoolFullExecutor` | 解析请求、编排校验/借出/确认/恢复并生成统一结果 |
| `SpringContextResolver` | 在应用 ClassLoader 中发现活动 Spring Boot Context，返回候选 BeanFactory 弱引用 |
| `DataSourceLocator` | 对所有活动 Context 执行精确 Bean 名查找；零个或多个候选均失败 |
| `PoolAdapterFactory` | 通过类层级与方法能力选择 Hikari 或 Druid Adapter，不按类名模糊猜测 |
| `HikariPoolAdapter` | 读取 maximum/active/idle，使用 DataSource 借还连接 |
| `DruidPoolAdapter` | 读取 maxActive/activeCount/poolingCount，使用 DataSource 借还连接 |
| `ExperimentRegistry` | 维护 UID 到 Holder、DataSource 身份到活动 UID 的双向索引和并发互斥 |
| `ConnectionHolder` | 保存本 UID 的连接引用、快照、状态、到期时间和释放结果 |
| `TtlReaper` | 到期后调用统一释放入口；状态查询不改变到期时间 |

#### 3.2 Spring Boot 热 attach 定位

Agent attach 时应用通常已经完成初始化，不能只依赖未来的 Spring refresh 事件。定位链按以下顺序执行：

1. 在每个包含 Spring Boot 的应用 ClassLoader 中，通过版本适配器读取 Spring Boot shutdown hook 维护的活动 `ApplicationContext` 集合。
2. 同时安装轻量生命周期观察点，捕获 attach 后新建、刷新或关闭的 Context，并更新按 ClassLoader 分区的弱引用集合。
3. 过滤未激活、已关闭或不属于目标 ClassLoader 的 Context；对存活 Context 获取 BeanFactory。
4. 对 canonical Bean name 执行精确查找并验证返回对象实现 `javax.sql.DataSource`。
5. 找不到、内部字段/方法不兼容或多个 Context 返回同名 DataSource 时失败关闭，不遍历堆、不选择第一个候选，也不退化为连接池名匹配。

Spring Boot 内部 Context 注册结构可能随版本变化，因此 Boot 2.7 与 3.2 使用独立反射适配器，并由热 attach 集成测试约束。任何反射访问失败都属于 `DATASOURCE_CONTEXT_UNAVAILABLE`，不得继续注入。

### 4. 命令与参数契约

#### 4.1 主机 JVM

```bash
blade create datasource connectionpoolfull \
  --pid <PID> \
  --data-source-name coreDataSource \
  --target-percent 100 \
  --timeout 60
```

`--pid` 与现有 `--process` 定位方式二选一，Agent attach 与 destroy 继续复用现有 JVM executor。

#### 4.2 Kubernetes

```bash
blade create k8s container-datasource connectionpoolfull \
  --namespace <NAMESPACE> \
  <现有 Pod/容器选择参数> \
  --data-source-name coreDataSource \
  --target-percent 100 \
  --timeout 60
```

`container-datasource` 来自现有 `container` scope 前缀规则；Namespace、Pod、label、container 与进程选择继续使用现有 K8s/JVM flags，不复制新选择器。

#### 4.3 Action 参数

| 参数 | 类型 | 默认值 | 校验与语义 |
| --- | --- | --- | --- |
| `data-source-name` | string | `coreDataSource` | trim 后非空；Spring canonical Bean name 精确匹配 |
| `target-percent` | integer | `100` | 范围 `1`–`100`；不接受小数、0 或越界值 |
| `timeout` | duration/seconds | `60` 秒 | create 时换算为正秒数并传给 Agent；形成不可续期 `expiresAt` |

不增加 `pool-type` 参数：连接池类型必须由实际 DataSource 能力自动识别。也不增加 `validate`、`validate-only`、SQL 或凭证参数。

#### 4.4 timeout 双层保护

- Agent 收到 create 后立即计算 `expiresAt = createStartedAt + timeout`，这是释放连接的主保护。
- 现有 CLI `actionPostRunEFunc` 仍可在 timeout 后发起 `blade destroy`；K8s scope 现有的额外等待不改变 Agent TTL。
- `exec/jvm/executor.go` 只为 `target=datasource && action=connectionpoolfull` 保留 `timeout` 请求字段，其他 JVM Action 继续沿用当前过滤行为。
- create、status、Operator 轮询和重复 create 均不得延长 `expiresAt`。

### 5. Agent 内存数据模型

#### 5.1 DataSourceIdentity

| 字段 | 用途 |
| --- | --- |
| `classLoaderId` | 应用 ClassLoader 运行时身份，不使用类名代替实例身份 |
| `contextId` | `ApplicationContext` 运行时身份 |
| `beanName` | canonical Spring Bean name |
| `poolType` | `HIKARI` 或 `DRUID` |

该对象只用于内存索引，不序列化对象引用。Context 与 ClassLoader 使用弱引用，Context 关闭时主动移除索引。

#### 5.2 ConnectionHolder

| 字段 | 类型/语义 |
| --- | --- |
| `uid` | 当前实验子 UID |
| `identity` | 目标 DataSource 身份 |
| `state` | 本文第 7 节定义的状态 |
| `startedAt` / `expiresAt` | 创建时间和不可续期到期时间 |
| `maximumBefore` / `activeBefore` / `idleBefore` | 注入前快照 |
| `targetPercent` / `targetConnections` | 目标参数和换算后的连接数 |
| `plannedHold` / `actualHold` / `failedHold` | 计划、实际和失败借出数量 |
| `connections` | 仅本 UID 成功借出的 `Connection` 引用集合 |
| `activeAfter` / `idleAfter` / `actualPercent` | 达标校验快照 |
| `releaseReason` / `closedCount` / `closeFailedCount` | 恢复原因与逐连接关闭结果 |
| `activeAfterRelease` / `idleAfterRelease` | 恢复后的观测指标 |

`connections` 不持久化、不写日志、不进入 CLI/CRD 响应。注册表写操作使用 DataSource 身份粒度的原子操作，避免检查与登记之间的竞态。

### 6. 前置校验和连接池适配

#### 6.1 原子前置校验

`create` 在登记活动 Holder 前依次完成：

1. 校验 UID、Bean 名、目标百分比和 timeout。
2. 定位唯一活动 Context 与唯一 DataSource Bean。
3. 识别 Adapter，并读取 maximum、active、idle；maximum 必须大于 0，指标不得为负。
4. 借出一条探测连接并立即关闭，确认连接有效且归还后 Adapter 仍可读取指标。
5. 原子检查 DataSource 身份占用情况：相同 UID 返回已有结果，不重复借出；不同活动 UID 返回 `RESOURCE_BUSY`。
6. 固化进程启动身份、Context 身份与 `expiresAt`，再进入正式借出。

探测连接不计入 Holder。探测借出或关闭失败时返回 `PROBE_FAILED`，不产生实验连接。

#### 6.2 Adapter 能力

| 能力 | HikariCP | Druid |
| --- | --- | --- |
| 最大连接数 | `maximumPoolSize` | `maxActive` |
| 活跃连接数 | pool MXBean active | `activeCount` |
| 空闲连接数 | pool MXBean idle | `poolingCount` |
| 借出/归还 | `DataSource.getConnection()` / `Connection.close()` | 同左 |

Adapter 通过反射隔离具体连接池依赖，并在创建前一次性绑定经过验证的方法句柄。方法缺失、返回类型不符或指标不可读时返回 `UNSUPPORTED_POOL` 或 `METRICS_UNAVAILABLE`。

### 7. 注入算法与状态机

#### 7.1 目标计算

```text
targetConnections = ceil(maximumPoolSize * targetPercent / 100)
connectionsToHold = max(0, targetConnections - activeConnections)
actualPercent = activeAfter * 100 / maximumPoolSize
```

如果创建前已经达到目标，返回终态 `ALREADY_AT_TARGET`，`actualHold=0`，不创建活动 Holder，并明确标识此次没有新增故障影响。

#### 7.2 正式借出

1. 创建 `ACQUIRING` Holder 并原子占用 DataSource 身份。
2. 从目标 DataSource 借出连接；每成功一条先登记到 Holder，再进行下一条。
3. 每次借出均受连接池自身获取超时和剩余硬 TTL 共同约束。异步借出在 TTL 后返回连接时必须立即关闭，不能加入已过期 Holder。
4. 初轮完成后重新读取 active；若业务并发归还导致未达标，可在剩余 TTL 内按最新 active 用同一公式补齐。
5. 最终 `activeAfter >= targetConnections` 才进入 `ACTIVE`；否则返回 `TARGET_NOT_REACHED` 并同步回滚本 UID。

任何借出、登记或指标读取异常都停止补齐并进入 `ROLLING_BACK`。Holder 不执行 SQL，不调用 `setAutoCommit(false)`，也不改变事务隔离级别。

#### 7.3 状态机

```text
VALIDATING ──失败──────────────> FAILED_NO_EFFECT
    │
    ▼
ACQUIRING ──异常──────────────> ROLLING_BACK
    │                              │
    ▼                              ├──> ROLLED_BACK
VERIFYING ──未达标─────────────>   └──> ROLLBACK_FAILED
    │
    ▼
ACTIVE ──destroy/TTL/unload────> RELEASING ──> RELEASED
    │
    └──Pod/JVM 消失────────────> PASSIVE_RELEASED
```

状态只能单向推进；`RELEASING` 使用一次性原子门闩，确保主动 destroy、TTL 和 Agent unload 并发触发时只执行一次连接关闭循环。

### 8. 恢复与幂等语义

统一 `release(uid, reason)` 流程：

1. UID 不存在且有已完成墓碑记录时返回原终态；完全未知 UID 返回 `NOT_FOUND`，不按 target/action 批量释放。
2. 将 Holder 原子切换到 `RELEASING`；重复调用等待或读取同一个释放结果。
3. 对 Holder 快照中的每条连接独立调用 `close()`，某条失败不阻断后续连接。
4. 再次检查连接关闭状态，清空已确认关闭的引用；对未关闭项记录错误。
5. 重新读取池指标，填充恢复后快照。
6. 仅当 Holder 中不存在本 UID 仍持有的连接时，注销双向索引并返回 `RELEASED`；否则返回 `RELEASE_INCOMPLETE` 并保留记录供后续 destroy 重试或 Agent 卸载清理。

完全恢复的硬判据是“本 UID 所有连接已关闭、Holder 为空且身份索引已注销”。连接池整体 active 可能被业务立即占用，因此恢复后 active/idle 是观测字段，不作为成功门槛。

触发来源：

- `blade destroy <uid>`：主动恢复，幂等返回实际结果。
- 硬 TTL：Agent 自主恢复，不依赖控制端在线。
- 创建阶段失败：同请求内同步回滚。
- Agent unload：遍历活动 UID 调用同一 release 流程。
- Pod/JVM 退出：操作系统被动关闭连接，Operator 标记 `PASSIVE_RELEASED`，不得伪装成 Agent 主动成功。

为限制墓碑内存，已完成记录只保存不含连接引用的最小结果，并在一个额外 TTL 周期后清理；该清理不影响实验连接生命周期。

### 9. Kubernetes 多目标编排

#### 9.1 UID 层次

- ChaosBlade CR 名称/UID 是父实验 UID。
- Operator 为每个 Pod/容器执行生成或记录子 UID；Agent Holder 只使用子 UID。
- `ResourceStatus.Id` 保存子 UID，`Identifier` 保存 Pod/容器身份，新增可选 `result` 保存连接池结果。

#### 9.2 新 Action 专用原子补偿

Operator 仅在 `target=datasource && action=connectionpoolfull` 时启用：

1. 按现有方式并行执行所有已选资源，并保留每个成功资源返回的子 UID。
2. 所有 create 结束后，只要存在一个失败、未达标或结果不可解析的资源，整体进入补偿阶段。
3. 对每个成功资源按其子 UID 发起 destroy；不得使用 target/action 无 UID 的批量销毁。
4. 每个资源记录 `ROLLED_BACK`、`ROLLBACK_FAILED` 或 `PASSIVE_RELEASED`，并保留原始注入错误与补偿错误。
5. 所有补偿结束后才把 CR 标记为 Error 并返回逐资源结果；补偿失败的 Agent Holder仍受硬 TTL 保护。

其他 JVM、OS 和 K8s Action 不启用该策略。后续用户删除 CR 或执行父 UID destroy 时，现有销毁链对已经回滚的子 UID执行幂等清理。

### 10. 响应与错误契约

#### 10.1 ConnectionPoolResult

Agent create/status/destroy 返回相同结构，Operator 将它放入 `ResourceStatus.result`：

| 字段 | 说明 |
| --- | --- |
| `uid` / `state` | 子实验 UID 与状态机状态 |
| `dataSourceName` / `poolType` | Bean 名与识别出的连接池类型 |
| `maximumBefore` / `activeBefore` / `idleBefore` | 注入前快照 |
| `targetPercent` / `targetConnections` | 请求目标与换算连接数 |
| `plannedHold` / `actualHold` / `failedHold` | 借出统计 |
| `activeAfter` / `idleAfter` / `actualPercent` | 注入后达标证据 |
| `startedAt` / `expiresAt` | 生命周期时间戳 |
| `releaseReason` / `closedCount` / `closeFailedCount` | 恢复统计 |
| `activeAfterRelease` / `idleAfterRelease` | 恢复后观测指标 |
| `reason` / `message` | 稳定原因枚举与安全错误说明 |

`result` 是可选的新增 JSON 对象，不改变现有 `ResourceStatus` 字段。旧客户端忽略未知字段，旧 Action 不生成该对象。

#### 10.2 稳定失败原因

| Reason | 含义 | 是否可能产生影响 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | Bean 名、百分比或 timeout 非法 | 否 |
| `DATASOURCE_CONTEXT_UNAVAILABLE` | 无法安全取得活动 Spring Context | 否 |
| `DATASOURCE_NOT_FOUND` / `DATASOURCE_AMBIGUOUS` | 没有唯一目标 Bean | 否 |
| `UNSUPPORTED_POOL` / `METRICS_UNAVAILABLE` | Adapter 或指标能力不满足 | 否 |
| `PROBE_FAILED` | 探测借还失败 | 否 |
| `RESOURCE_BUSY` | DataSource 已有其他活动 UID | 否 |
| `ACQUIRE_FAILED` / `TARGET_NOT_REACHED` | 正式借出失败或最终未达标 | 是，响应前必须回滚 |
| `RELEASE_INCOMPLETE` | 仍有本 UID 连接无法确认关闭 | 是，保留 Holder 并由后续恢复重试 |
| `TARGET_GONE` | Pod、JVM 或进程启动身份已变化 | 被动释放 |

### 11. 并发、性能与安全

- 注册表使用 DataSource 身份级锁，不使用全 JVM 大锁；不同 DataSource 的实验互不阻塞。
- 连接借出运行在 Agent 专用的有界执行器中，不占用应用请求线程；任务数受计划持有数和剩余 TTL 约束。
- 关闭操作逐连接容错；生命周期线程、TTL 线程和 destroy 请求通过 Holder 原子状态协作。
- 不记录 `Connection` 对象、JDBC URL、用户名、密码、SQL 或 Spring Bean 内容。日志只输出 UID、目标身份摘要、池类型、计数、状态和安全错误码。
- Sandbox HTTP 继续绑定目标容器内 localhost；不新增对外端口、数据库凭证或 Operator RBAC 权限。
- create 前后校验进程启动身份与 ClassLoader/Context 身份，避免 PID 复用或 Pod 重建后操作新进程。
- `target-percent=100` 是默认的强故障参数，只允许在已有 ChaosBlade 权限与目标选择边界内执行；自动恢复不能被续期覆盖。

### 12. 兼容矩阵与构建交付

#### 12.1 首版验证矩阵

| JDK | Spring Boot | HikariCP | Druid | 用途 |
| --- | --- | --- | --- | --- |
| 8 | 2.7.x | 4.x | 1.2.x | 老运行时与两类连接池基线 |
| 11 | 2.7.x | 4.x | 1.2.x | 中间 JDK、attach 与模块回归 |
| 17 | 3.2.x | 5.x | 1.2.x | Boot 3 与较新 JDK 基线 |

任务计划从上述版本族中锁定可重复构建的小版本。矩阵外版本不会仅因版本号被拒绝，但必须完整通过 Context、Adapter、指标和探测能力检查；没有验证证据的组合不进入首版兼容承诺。

#### 12.2 版本一致性

交付流水线必须同时验证：

- `chaosblade-jvm-spec-<version>.yaml` 包含 `datasource/connectionpoolfull` 及三个参数默认值。
- 主机帮助中出现 `datasource connectionpoolfull`，K8s 帮助中出现 `container-datasource connectionpoolfull`。
- CLI 对新 Action 转发 timeout，Agent 能识别请求和状态结构。
- Operator 镜像内分发的 blade、Agent、YAML 与 CRD 类型来自同一发布组合。
- 旧 `druid connectionpoolfull` 和已有 JVM Action 的命令帮助、请求参数及执行结果保持不变。

### 13. 测试与验收

#### 13.1 P0 快速效果验证

在隔离的测试 Namespace 部署一个小连接池 Druid 示例应用和非生产数据库，使用现有 `k8s container-druid connectionpoolfull`：

1. 记录 Pod、容器、JVM、Druid 最大/活跃/空闲连接数和业务探针基线。
2. 使用现有资源选择参数创建故障并保存 UID。
3. 持续调用只读测试接口，确认连接获取出现预期阻塞或错误，同时 Pod/JVM 保持存活。
4. 执行 `blade destroy <uid>`，确认连接释放且测试接口恢复。
5. 明确记录 P0 仅证明现有 Druid 链路，未覆盖 Bean 定位、Hikari、目标比例、UID 隔离或多 Pod 原子补偿。

#### 13.2 单元测试

- 百分比换算：1、99、100、已有 active 大于等于目标、非整除向上取整。
- Context/Bean：无 Context、无 Bean、同名多 Context、非 DataSource、Context 关闭和弱引用清理。
- Adapter：方法能力缺失、指标非法、探测借还、Hikari/Druid 指标映射。
- Registry：相同 UID 幂等、不同 UID 冲突、不同 DataSource 并行、三种恢复触发并发竞争。
- Holder：部分借出失败、晚返回连接、单条 close 失败、重复 destroy、墓碑过期。

#### 13.3 JVM 集成测试

- 对兼容矩阵中每种池分别执行热 attach、精确 Bean 定位、25%/100% 目标、达标确认和 destroy。
- 验证 Agent 在应用已经完成启动后仍能取得 Context；该项失败即视为对应版本组合不兼容。
- 制造业务并发归还，验证重新计算补齐或未达标回滚；回滚只关闭实验连接。
- 验证 60 秒 TTL、Agent unload 和 JVM 重启，不依赖 CLI 后台 destroy 才恢复。

#### 13.4 Kubernetes 集成测试

- 单 Pod/单容器成功、单 Pod 多容器精确选择、多 Pod 全成功。
- 一个 Pod Bean 缺失、一个 Pod Adapter 不支持、一个 Pod 借出失败时，成功 Pod 全部补偿。
- 补偿 destroy 失败时返回 `ROLLBACK_FAILED`，随后由硬 TTL 清理。
- Pod 在 ACTIVE 期间删除或重建时返回 `PASSIVE_RELEASED/TARGET_GONE`，不得把新 JVM 当成旧目标。
- CR create/destroy 结果包含逐资源子 UID、状态、原始错误、补偿结果和可选连接池结果。

#### 13.5 验收门槛

- create 成功必须有真实池指标证明 `activeAfter >= targetConnections`；命令退出码不是充分证据。
- create 失败返回前，本 UID 已借出的连接必须已回滚，或明确返回 `ROLLBACK_FAILED/RELEASE_INCOMPLETE`。
- destroy/TTL 后，本 UID Holder 必须清空并注销；恢复后指标必须可读取并返回。
- 多 Pod 请求任一目标失败时，不得留下状态为 ACTIVE 的成功目标。
- 旧 Action 回归通过，发布物版本一致，日志与响应不包含凭证、SQL 或连接对象信息。

### 14. 发布、回滚与风险控制

#### 14.1 发布顺序

1. 完成 P0，只形成现有能力效果证据，不修改正式能力结论。
2. 在 `chaosblade-exec-jvm` 完成新模型、Agent 组件和 JVM 测试。
3. 在 `chaosblade-operator` 完成结果透传、原子补偿和 K8s 测试。
4. 在本仓库完成 timeout 条件转发、CRD 状态扩展、动态规格/命令测试和版本锁定。
5. 构建一套匹配产物，通过兼容矩阵、K8s 冒烟和旧 Action 回归后发布。

#### 14.2 回滚路径

- 新能力是独立 target/action；停止暴露新 YAML 或回退匹配版本产物即可撤销入口，不迁移任何数据。
- 发布回滚前先 destroy 所有已知新 Action UID；无法联系的 Agent 最迟在硬 TTL 到期时释放连接。
- CRD 新增字段为可选字段，回退客户端会忽略；回退 Operator/CRD 前确认集群中没有活动的新 Action CR。
- 不删除或转换旧 Druid 场景，因此旧能力可独立继续使用。

#### 14.3 主要风险

| 风险 | 控制与验收 |
| --- | --- |
| 热 attach 后无法取得已初始化 Context | Boot 2/3 独立 Context 适配器；每个矩阵组合必须做启动后 attach；失败关闭 |
| JDK 17 反射或模块边界变化 | 不依赖 `--add-opens` 作为默认前提；能力探测失败明确返回，不进入借出 |
| 业务并发造成达标抖动 | 实时计算、剩余 TTL 内重新补齐、最终指标硬校验，失败立即回滚 |
| 获取连接阻塞超过实验生命周期 | 借出任务受剩余 TTL 约束；晚返回连接立即关闭 |
| close 返回但连接仍被 Holder 引用 | 逐连接状态检查；Holder 未清空则 `RELEASE_INCOMPLETE`，不得报告完全恢复 |
| 多 Pod 部分失败形成不一致故障 | 新 Action 专用原子补偿；子 UID 精确 destroy；TTL 作为最后保护 |
| CLI、Agent、Operator、YAML 版本错配 | 单一发布组合和构建期命令/规格校验，不混用独立产物 |
