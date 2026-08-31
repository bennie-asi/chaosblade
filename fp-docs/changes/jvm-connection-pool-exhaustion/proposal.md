# JVM 应用连接池耗尽故障能力

## Why

现有 ChaosBlade JVM 场景可以向目标 Java 进程注入 CPU、内存和方法级故障，但当前仓库及其已集成的 JVM 场景规格没有“按指定 DataSource 占用应用连接池”的能力。通过网络延迟、数据库长查询或外部客户端持续连接，只能间接增加连接占用，无法稳定满足以下要求：

- 按 Pod 和 DataSource 分别读取连接池最大、已占用和空闲连接数；
- 按目标占用率计算本次需要新增持有的连接数；
- 使用目标应用自身的 DataSource 借出连接，而不是绕过应用直接连接数据库；
- 只释放本次实验创建的连接，不影响正常业务连接；
- 在注入失败、主动销毁、超时或执行端失联时可靠恢复。

因此需要在 ChaosBlade 底层增加一项 JVM 应用连接池耗尽能力，以可控、可观测、可恢复的方式模拟核心数据源连接不足，并复用现有 JVM Agent 与 Kubernetes `container-jvm` 执行链。

## What Changes

### 1. 新增 JVM 连接池 `borrow-hold` 故障能力

在 `chaosblade-exec-jvm` 中增加应用连接池耗尽 Action。首版只实现 `borrow-hold` 模式：通过目标 DataSource 获取连接，将连接登记到当前实验 UID 并持续持有，直到实验销毁或到达自动恢复条件。首版不执行长查询、不创建未提交事务，也不允许注入任意 SQL。

该能力接受以下业务参数：

- DataSource Bean 名：可配置，默认 `coreDataSource`；
- 连接池类型：首版支持 HikariCP 与 Druid；
- 目标池占用率：1%–100%，默认 100%；
- 注入持续时间：默认 60 秒，沿用 ChaosBlade 通用超时语义；
- JVM 目标：沿用现有 `process`、`pid` 和 Java Agent attach 机制；
- Kubernetes 目标：沿用现有 Namespace、Pod、容器和资源选择参数。

### 2. 增加 DataSource 定位与能力校验

JVM Agent 增加 Spring Boot DataSource 定位能力，并为 HikariCP、Druid 提供独立适配器。校验阶段必须在目标 JVM 内完成下列检查：

- 能唯一定位指定名称的 DataSource Bean；
- DataSource 类型属于首版支持范围；
- 可以读取连接池最大连接数、当前已占用连接数和当前空闲连接数；
- 可以从该 DataSource 借出并归还一条探测连接；
- Agent、连接池适配器和目标 JVM 处于可执行状态；
- 目标 Pod、容器、JVM 进程和 DataSource 身份与后续注入目标一致。

找不到 DataSource、找到多个候选、池类型不受支持、指标不可读或探测借还失败时，校验必须明确失败，不得选择第一个候选或降级为直接连接数据库。

### 3. 按实时连接池状态计算并持有连接

每个目标 Pod 独立读取连接池状态并计算：

```text
targetConnections = ceil(maximumPoolSize * targetPercent / 100)
connectionsToHold = max(0, targetConnections - activeConnections)
```

Agent 为 `connectionsToHold` 创建受控 Holder，并记录以下实验状态：

- 实验 UID；
- Pod、容器、JVM 进程与进程启动身份；
- DataSource 名称与连接池类型；
- 注入前最大、活跃、空闲连接数；
- 计划持有、实际持有和失败连接数；
- 注入后的实际连接池占用率；
- 每条由本实验持有的连接引用和生命周期状态；
- 开始时间、过期时间、最后一次控制端心跳和恢复状态。

注入完成后必须重新读取池指标并确认实际占用率达到目标。未达到目标时，本 Pod 注入失败，并立即关闭该 UID 已持有的全部连接。多 Pod 请求中任一 Pod 失败时，Kubernetes 执行链需要向所有已成功注入的目标发起恢复，并返回逐 Pod 结果。

### 4. 提供幂等销毁与失联恢复

销毁逻辑按实验 UID 查找 Holder，只关闭本实验持有的连接。重复销毁、部分连接已经关闭、目标进程已经退出或 Pod 已经消失时，恢复操作保持幂等，并返回可区分的实际恢复结果。

恢复触发条件包括：

- 用户执行 `blade destroy <uid>`；
- 实验达到配置的持续时间；
- 创建阶段部分失败；
- JVM Agent 或控制链判断实验失联；
- Pod、容器或 JVM 身份发生变化。

恢复后必须重新读取连接池指标；仍存在本实验持有的连接时不得报告完全恢复。Pod 或 JVM 退出导致操作系统关闭连接时，应记录为目标消失后的被动释放，而不是伪装成 Agent 主动恢复成功。

### 5. 复用现有 JVM 与 Kubernetes 注册链

新 Action 继续由 `chaosblade-jvm-spec-<version>.yaml` 描述，并由现有 JVM 命令注册逻辑加载。Kubernetes 路径继续复用当前将 JVM 模型设置为 `container` scope 的机制，使同一能力暴露为 `k8s container-jvm` 场景，而不为 Kubernetes 复制一套连接池实现。

普通主机 JVM 命令可复用同一 Action；本次变更的主要验收目标是 Kubernetes Pod。`chaosblade-operator` 继续负责选择 Pod/容器、部署匹配版本的 ChaosBlade 工具、创建和销毁实验，不新增数据库凭证管理职责。

### 6. 保持现有能力兼容并建立验证矩阵

本变更以新增 Action 和新增适配器的方式交付，不改变现有 JVM Action 的名称、参数和执行语义。构建产物需要同时包含匹配版本的 JVM Agent、场景 YAML 与 Operator 分发内容。

验证范围包括：

- HikariCP 与 Druid 的 DataSource 定位、指标读取、借出和归还；
- 目标占用率计算的边界值与已有业务占用并发变化；
- 单 Pod、多 Pod、单容器和多容器选择；
- 部分连接借出失败、目标占用率未达到和多 Pod 部分失败；
- 主动 destroy、超时、失联、Pod 重建和 JVM 重启；
- 恢复只释放当前实验连接，且不破坏已有 JVM 故障场景；
- 不同支持版本的 Spring Boot、HikariCP、Druid、JDK 和容器运行环境。

## Capabilities

### New Capabilities

- `jvm-connection-pool-exhaustion`: 在目标 JVM 内定位指定 Spring DataSource，按目标占用率借出并持有 HikariCP/Druid 连接，并按实验 UID 精确恢复。
- `jvm-datasource-validation`: 校验目标 JVM、DataSource、连接池适配器、实时池指标和探测连接借还能力，为创建实验提供可验证的前置条件。

### Modified Capabilities

- `jvm-experiment-registration`: JVM 场景规格增加连接池耗尽 Action，继续通过现有 YAML 动态注册机制加载。
- `k8s-container-jvm`: Kubernetes 容器 JVM 场景自动继承新的连接池耗尽 Action，并返回逐 Pod/容器执行与恢复状态。
- `chaosblade-build-packaging`: 构建产物需要打包相互匹配的 JVM Agent、场景规格及 Operator 分发版本。

## Out of Scope

- `cw-chaos` 后端、前端、原子编排、校验页面、执行历史与业务指标展示；
- 数据库实例服务端连接耗尽以及修改 `max_connections`；
- Full GC、线程异常和其他 JVM 故障；
- 长查询、未提交事务、锁等待和任意 SQL 执行模式；
- 非 Spring Boot 应用；
- HikariCP、Druid 之外的连接池实现；
- 核心账务交易压测器、业务流量生成和外部可观测平台；
- 自动追踪 Deployment 滚动发布后的新 Pod 并无条件重新注入；
- 在 Agent 内保存或接收数据库账号、密码等凭证。

## Impact

- `Makefile:105-111` - 当前构建固定 `chaosblade-operator` 与 `chaosblade-exec-jvm` v1.8.0 分支；交付时需要同步匹配的组件版本与构建产物。
- `cli/cmd/exp.go:185-198` - 现有 JVM YAML 动态注册入口，新 Action 应通过场景规格进入，不在 Go CLI 中硬编码命令。
- `cli/cmd/exp.go:260-292` - 现有 Kubernetes JVM 模型映射入口，新 Action 将复用 `container` scope 与资源选择参数。
- `build/spec/spec.go:115-130` - 汇总规格时将 JVM 模型合并到 Kubernetes 模型，需要验证新 Action 的参数完整进入聚合规格。
- `exec/jvm/executor.go:127-203` - 现有 Java Agent 安装、create/destroy 请求转发与参数传递边界，需要验证新增参数和状态语义兼容。
- `exec/kubernetes/executor.go:130-223` - 现有 ChaosBlade CR 创建、状态等待与销毁入口，需要承载逐资源失败和恢复结果。
- `exec/kubernetes/executor.go:307-340` - 现有实验模型到 ChaosBlade CR 的转换入口，需要保持新 Action 参数完整传递。
- `chaosblade-exec-jvm`（外部构建依赖） - 新增 Spring DataSource Resolver、HikariCP/Druid Adapter、Connection Holder、校验、状态与恢复实现。
- `chaosblade-operator`（外部构建依赖） - 验证分发的 ChaosBlade 版本包含新 Agent 和场景规格，并保持现有 Pod/容器执行与清理链兼容。
- 测试与发布产物 - 增加适配器单元测试、JVM 集成测试、Kubernetes 单/多 Pod 冒烟、恢复验证和现有 JVM Action 回归。

该能力的主要技术风险是 Agent attach 后可靠定位已经初始化的 Spring DataSource，以及跨 Spring Boot、连接池、JDK 和容器环境的兼容性。设计阶段必须明确实例定位机制、Holder 并发模型、失联判定、状态机、兼容矩阵和熔断边界；运行时验收必须以实际池指标和连接释放结果为准，不能仅依据命令成功退出。

### Handoff Decision Ledger

| ID | Decision | Source | Blocking | Status | Evidence / explicit confirmation |
| --- | --- | --- | --- | --- | --- |
| P-001 | 交付范围仅覆盖 ChaosBlade 底层，包括 JVM Agent、连接 Holder 与 K8s `container-jvm` 接入 | 本会话用户范围选择 | yes | `user-confirmed` | P-001: selected 仅 ChaosBlade 底层方案；user message 回复 `B`，明确排除完整 `cw-chaos` 产品方案 |
| P-002 | 首版支持 Spring Boot + HikariCP/Druid，DataSource Bean 名可配置且默认 `coreDataSource` | 本会话用户兼容范围选择 | yes | `user-confirmed` | P-002: selected Spring Boot + HikariCP/Druid；user message 回复 `B` |
| P-003 | 首版只实现 `borrow-hold`，不包含长查询和未提交事务模式 | 本会话用户故障模式选择 | yes | `user-confirmed` | P-003: selected `borrow-hold` only；user message 回复 `A` |
| P-004 | FeaturePilot 产物归属当前 `chaosblade` 仓库，跨仓影响写入同一逻辑提案 | 本会话用户产物归属选择 | yes | `user-confirmed` | P-004: selected 当前 `chaosblade` 仓库；user message 回复 `A` |
| P-005 | change slug 使用 `jvm-connection-pool-exhaustion` | 本会话用户 slug 选择 | yes | `user-confirmed` | P-005: selected `jvm-connection-pool-exhaustion`；user message 回复 `A` |
| P-006 | Proposal 使用 small form，唯一入口为 `fp-docs/changes/jvm-connection-pool-exhaustion/proposal.md` | 本会话用户 form 与路径选择 | yes | `user-confirmed` | P-006: selected small form 与所列 exact path；user message 回复 `A` |
| P-007 | 保留需求中的逐 Pod 校验、实时计算、按 UID 持有、目标达成确认和精确恢复契约 | 用户指定的现行需求描述 | yes | `user-confirmed` | P-007: selected 按当前描述设计；user message `按照这里描述的做下技术设计吧`，对应 DevOps 工作项 p348_772 的原子1契约 |
| P-008 | 新能力必须保持现有 JVM Action 和调用方式向下兼容 | DevOps 工作项 p348_772 与用户指定的现行需求 | yes | `user-confirmed` | P-008: selected 向下兼容；p348_772 字段 `是否向下兼容` 显示为 `是`，用户要求按该描述推进 |
| P-009 | 新 Action 复用 JVM YAML 动态注册与 K8s `container` scope 映射，不在 Go CLI 复制实现 | 当前代码 | no | `code-verified` | P-009: `cli/cmd/exp.go:185-198` 动态加载 JVM YAML；`cli/cmd/exp.go:260-292` 将 JVM 模型注册到 K8s `container` scope |
| P-010 | `cw-chaos` 产品层与 UI 不属于本 proposal | P-001 已确认范围 | no | `not-applicable` | P-010: P-001 明确选择仅 ChaosBlade 底层，故产品层设计不适用于本提案 |

### Pre-write Confirmation Evidence

- Covered IDs: `P-001`, `P-002`, `P-003`, `P-004`, `P-005`, `P-006`, `P-007`, `P-008`, `P-009`, `P-010`
- Outstanding blocking decisions: `none`
- Explicit user authorization to write: 用户消息 ``确认并按 P-001 至 P-010 写入 proposal.md``，授权在当前 `chaosblade` 仓库创建 small form `fp-docs/changes/jvm-connection-pool-exhaustion/proposal.md`，且不存在覆盖、转换或 obsolete path 删除。
