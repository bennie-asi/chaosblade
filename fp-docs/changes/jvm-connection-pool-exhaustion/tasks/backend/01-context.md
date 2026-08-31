# JVM 应用连接池耗尽故障能力 Backend Implementation Plan

> **For agentic workers:** REQUIRED FLOW: Use `fp-execute` to implement this plan task-by-task. Only task markers use checkbox (`- [ ] **Task backend-NNN: ...**`) syntax for tracking; substeps are plain ordered instructions.

**Goal:** 在主机 JVM 和 Kubernetes Pod 内，通过新命令 `datasource connectionpoolfull` 按 Spring Bean 名定位 HikariCP/Druid DataSource，按目标比例借出并持有连接，并以 UID、硬 TTL、主动 destroy 和 K8s 原子补偿保证可观测、可恢复、可快速验证。

**Architecture:** 在 `chaosblade-exec-jvm` 新增与旧 Druid 插件隔离的 datasource 插件，内部按 Spring Context 定位器、连接池适配器、UID 实验注册表和统一编排器分层；Agent service 仅为实现结果提供接口的模型返回结构化 JSON。`chaosblade-operator` 兼容旧 string UID 与新 `{uid,detail}` 结果，仅为新 Action 执行多资源失败补偿。根 `chaosblade` 负责参数透传、动态命令、结果展示、CRD 同步、三仓可复现构建和 Kubernetes E2E。

**Tech Stack:** Java 8 source compatibility、Maven、JUnit 4、Mockito、Java Reflection、Spring Boot 2.7/3.2 测试应用、HikariCP 4/5、Druid 1.2、Go 1.25、Kubernetes CRD/controller、Docker、shell、Kubernetes 单节点测试集群。

## Global Constraints

- D-001：新增 `datasource/connectionpoolfull`；旧 `druid/connectionpoolfull` 的名称、参数、全局 Holder 和恢复行为不得改变。
- D-002：正式验收覆盖新能力；现有 Druid/K8s P0 只作环境冒烟，不能代替新能力 E2E。
- D-003：仅支持 Spring Boot；按 canonical Bean 名精确定位，零候选、多个 Context 同名候选和反射不兼容均失败关闭，不遍历堆或猜测第一个对象。
- D-004：连接仅保存在 Agent 内存；默认 TTL 60 秒、不可续期；destroy、创建失败、TTL 和 unload 共用幂等释放入口。
- D-005：只有 `datasource/connectionpoolfull` 在多 Pod 任一失败时回滚所有已成功子 UID，其他 Action 保持现状。
- D-007：同一 DataSource 身份只允许一个活动 UID；同 UID create 幂等，不同 UID 返回 `RESOURCE_BUSY`；不同 DataSource 可并行。
- D-008：所有 Bean、Adapter、指标和探测连接校验均在 create 内完成，不新增 validate Action。
- D-009：恢复成功的硬判据为本 UID 拥有的连接全部关闭、Holder 清空且身份索引注销；池 active/idle 仅作观测。
- D-010：单元/集成矩阵覆盖 JDK 8/11 + Boot 2.7 + Hikari 4/Druid 1.2 和 JDK 17 + Boot 3.2 + Hikari 5/Druid 1.2；远程实时 E2E 至少覆盖 K8s 内 Hikari 与 Druid。
- Agent 不执行 SQL、不创建未提交事务、不保存数据库凭据；H2 只用于自包含 E2E 测试应用。
- `target-percent` 为 `1..100` 整数，默认 `100`；`data-source-name` 默认 `coreDataSource`；`timeout` 为正秒数，默认 `60`。
- 新 Action 成功结果使用 `{ "uid": "<child-uid>", "detail": <ConnectionPoolResult> }`；所有旧 Action 仍接受并返回 string UID。
- 三个仓库使用同名本地功能分支 `codex/jvm-connection-pool-exhaustion`，不推送远端；根 Makefile 默认上游地址/版本保持不变，只允许显式变量覆盖。
- 所有生产代码先由针对性失败测试约束，再完成最小实现；远程 E2E 证据写入变更目录，禁止以命令退出码代替故障效果与恢复证明。

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `../chaosblade-exec-jvm/chaosblade-exec-plugin/pom.xml` | modify | 注册新的 datasource Maven 子模块 |
| `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/pom.xml` | create | 定义无 Spring/Hikari/Druid 运行时依赖的插件构建与测试依赖 |
| `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/` | create | 模型、Spring 定位器、Adapter、Holder、TTL、执行器、结果与插件注册实现 |
| `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/` | test | 计算、反射适配、并发、幂等、TTL、定位与编排测试 |
| `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/resources/META-INF/services/com.alibaba.chaosblade.exec.common.aop.Plugin` | create | Java SPI 插件发现 |
| `../chaosblade-exec-jvm/chaosblade-exec-service/src/main/java/com/alibaba/chaosblade/exec/service/handler/` | modify | create/status/destroy 对可选结构化结果 provider 的兼容调用 |
| `../chaosblade-exec-jvm/chaosblade-exec-service/src/test/java/com/alibaba/chaosblade/exec/service/handler/` | test | 新旧模型响应兼容测试 |
| `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/types.go` | modify | ResourceStatus 新增可选原始 JSON result |
| `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/zz_generated.deepcopy.go` | modify | 深拷贝新 result 字段 |
| `../chaosblade-operator/deploy/crds/chaosblade.io_chaosblades_crd.yaml` | modify | canonical CRD schema 接受结构化 result |
| `../chaosblade-operator/deploy/helm/chaosblade-operator/crds/crd.yaml` | modify | 同步主 Helm CRD schema |
| `../chaosblade-operator/deploy/helm/chaosblade-operator-arm64/crds/crd.yaml` | modify | 同步 arm64 Helm CRD schema |
| `../chaosblade-operator/exec/model/executor.go` | modify | 兼容 legacy string 和新 `{uid,detail}` 子命令响应 |
| `../chaosblade-operator/exec/controller.go` | modify | 新 Action 创建部分失败时按成功子 UID原子补偿 |
| `../chaosblade-operator/exec/model/executor_test.go` | test | 响应解析、result 透传与旧格式回归 |
| `../chaosblade-operator/exec/controller_test.go` | test | 原子补偿成功、失败、幂等和非目标 Action 回归 |
| `exec/jvm/executor.go` | modify | 仅为新 datasource Action 转发 timeout |
| `exec/jvm/sandbox_test.go` | test | timeout 查询参数新旧行为回归 |
| `cli/cmd/create.go` | modify | 新 Action 保留 Agent detail 并仍提供父/子 UID |
| `cli/cmd/destroy.go` | modify | 新 Action destroy 展示实际释放 detail，旧 Action 不变 |
| `cli/cmd/command_test.go` | test | 主机与 container-datasource 动态命令/flags 注册测试 |
| `cli/cmd/destroy_test.go` | test | 新旧 destroy 输出契约测试 |
| `apis/chaosblade/v1alpha1/chaosblade_types.go` | modify | 根 CLI 的 ResourceStatus 同步可选 result |
| `Makefile` | modify | 外部 JVM/Operator 仓库地址和分支允许显式覆盖 |
| `test/e2e/jvm-connection-pool/` | create/test | Boot/H2 Hikari+Druid 应用、镜像、K8s manifests、自动部署和断言脚本 |
| `fp-docs/changes/jvm-connection-pool-exhaustion/e2e-evidence.md` | create | 记录远程集群、构建 SHA、命令、逐 Pod 效果、补偿和恢复证据 |
