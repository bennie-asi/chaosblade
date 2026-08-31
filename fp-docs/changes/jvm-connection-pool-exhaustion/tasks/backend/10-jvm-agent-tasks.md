# JVM Agent Tasks

- [ ] **Task backend-001: 建立 datasource 插件模块、模型与请求校验契约**

**Files:**
- Modify: `../chaosblade-exec-jvm/chaosblade-exec-plugin/pom.xml`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/pom.xml`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourceModelSpec.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourceConnectionPoolFullActionSpec.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/ConnectionPoolRequest.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/ConnectionPoolRequestTest.java`

**Reasoning:**
- 先冻结 target/action/flags 和失败原因，后续 Agent、CLI 与 Operator 才能共享同一命令契约；新 Maven 模块确保旧 Druid 插件不被改造。

**Depends on:** None

**Interfaces:**
- Consumes: `ModelSpec`、`ActionSpec`、`FlagSpec`、`ActionModel.getFlag` 现有 JVM SPI。
- Produces: `datasource/connectionpoolfull` spec；`ConnectionPoolRequest.from(ActionModel)`。
- Contract checks: 默认 bean=`coreDataSource`、percent=`100`、timeout=`60`；空 Bean、非整数/越界 percent、非正 timeout 返回稳定参数错误。

**Step 1: Write the failing test**

新增 `shouldApplyDefaults`、`shouldRejectInvalidFlags` 与 action spec 快照断言，证明模块和请求解析尚不存在。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=ConnectionPoolRequestTest test`
Expected: FAIL，原因是 datasource module/classes 尚不存在。

**Step 3: Write minimal implementation**

创建独立 module、model/action/flag spec 和不可变请求对象，仅实现默认值、trim、范围检查与稳定错误映射。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=ConnectionPoolRequestTest test`
Expected: PASS。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin/pom.xml chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: add datasource connection pool model"`

- [ ] **Task backend-002: 实现连接池能力适配器与目标占用计算**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/PoolAdapter.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/PoolAdapterFactory.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/HikariPoolAdapter.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/DruidPoolAdapter.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/ConnectionTarget.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/PoolAdapterFactoryTest.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/pool/ConnectionTargetTest.java`

**Reasoning:**
- 使用能力反射隔离具体池依赖，并把数学计算做成纯函数；生产 classpath 不引入 Hikari/Druid，缺失方法时明确拒绝。

**Depends on:** `backend-001`

**Interfaces:**
- Consumes: `javax.sql.DataSource` 与连接池运行时公开 getter/MXBean 能力。
- Produces: `PoolAdapter`、`PoolSnapshot`、`ConnectionTarget.calculate(maximum,active,percent)`。
- Contract checks: 最大值正数、指标非负；ceil 计算准确；不支持对象返回 `UNSUPPORTED_POOL`，不可读指标返回 `METRICS_UNAVAILABLE`。

**Step 1: Write the failing test**

使用测试 double 和测试 scope 的 Hikari/Druid 实例覆盖 Adapter 选择、指标、borrow/close 与 `maximum=5, percent=81 => target=5`。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=PoolAdapterFactoryTest,ConnectionTargetTest test`
Expected: FAIL，原因是 Adapter 与 target calculator 尚不存在。

**Step 3: Write minimal implementation**

以已验证的反射 Method 封装 Hikari/Druid 指标和 `DataSource.getConnection()`，实现纯整数向上取整与负值防护。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=PoolAdapterFactoryTest,ConnectionTargetTest test`
Expected: PASS。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: adapt hikari and druid pools"`

- [ ] **Task backend-003: 实现 Spring Boot 活动 Context 与唯一 DataSource Bean 定位**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/spring/SpringContextResolver.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/spring/Boot2ContextAdapter.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/spring/Boot3ContextAdapter.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/spring/DataSourceIdentity.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/spring/SpringContextResolverTest.java`

**Reasoning:**
- Agent 热 attach 时应用已启动，定位必须从目标 ClassLoader 的 Boot 活动 Context 注册处恢复现状；弱引用和歧义失败可避免类加载器泄漏与误注入。

**Depends on:** `backend-001`

**Interfaces:**
- Consumes: 目标应用 ClassLoader、Boot 2.7/3.2 shutdown hook 活动 Context 反射结构、canonical bean name。
- Produces: `ResolvedDataSource` 和身份键 `ClassLoader identity + Context identity + bean name`。
- Contract checks: 唯一活跃 DataSource 成功；零候选、多候选、关闭 Context、非 DataSource Bean 和反射不兼容分别失败关闭。

**Step 1: Write the failing test**

构造 Boot2/Boot3 Context 测试夹具，覆盖 attach-after-start、关闭 Context 过滤、同名多 Context 歧义和弱引用清理。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=SpringContextResolverTest test`
Expected: FAIL，原因是 resolver/adapters 尚不存在。

**Step 3: Write minimal implementation**

按版本选择反射适配器，读取活动 Context，按目标 ClassLoader 分区并精确调用 BeanFactory 查找；不遍历堆、不退化猜测。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=SpringContextResolverTest test`
Expected: PASS。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: resolve spring datasource beans"`

- [ ] **Task backend-004: 实现 UID Holder 注册表、互斥、硬 TTL 与幂等释放**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/registry/ConnectionHolder.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/registry/ExperimentRegistry.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/registry/TtlReaper.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/registry/ExperimentRegistryTest.java`

**Reasoning:**
- 所有连接所有权和恢复入口必须先独立收敛，否则 create/destroy/TTL 竞态会导致重复关闭、泄漏或错误释放其他实验连接。

**Depends on:** `backend-001`

**Interfaces:**
- Consumes: UID、`DataSourceIdentity`、不可续期 `expiresAt` 与 Agent scheduler。
- Produces: 原子 `create/find/addConnection/release/closeAll`、活动双向索引和无连接引用的短期 tombstone。
- Contract checks: 同 UID 幂等、异 UID 同身份冲突、不同身份并行、destroy/TTL/unload race 只关闭一次、部分 close 失败保留可重试 Holder。

**Step 1: Write the failing test**

用 `CountDownLatch` 与可计数 Connection doubles 覆盖并发 create、三路 release race、不可续期时间和 `RELEASE_INCOMPLETE` 重试。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=ExperimentRegistryTest test`
Expected: FAIL，原因是 registry/holder/reaper 尚不存在。

**Step 3: Write minimal implementation**

以 ConcurrentMap 原子计算维护 UID 与 identity 索引，Holder 内一次性 release 门闩逐连接关闭；scheduler 仅读取固化 expiresAt。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=ExperimentRegistryTest test`
Expected: PASS，重复运行 20 次无竞态失败。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: manage datasource experiment lifecycle"`

- [ ] **Task backend-005: 编排探测、借出、达标确认、回滚与恢复结果**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourceConnectionPoolFullExecutor.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/ConnectionPoolResult.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/FailureReason.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourceConnectionPoolFullExecutorTest.java`

**Reasoning:**
- 该任务是分层组件的唯一编排点，必须先探测借还，再登记 Holder，逐条先登记后继续借出，并在未达标/异常时同步释放本 UID。

**Depends on:** `backend-002`, `backend-003`, `backend-004`

**Interfaces:**
- Consumes: `ConnectionPoolRequest`、resolver、adapter、target calculator 和 registry。
- Produces: create/status/destroy 一致的 `ConnectionPoolResult` 与稳定 state/reason。
- Contract checks: already-at-target 无 Holder；成功达到 ceil 目标；晚返回连接在 TTL 后立即关闭；任一步失败仅回滚本 UID；结果不含 Connection/SQL/凭据。

**Step 1: Write the failing test**

覆盖 probe 成功/失败、100% 借满、已有 active 扣减、业务归还后二次补齐、未达标回滚、TTL 晚返回和恢复硬判据。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=DataSourceConnectionPoolFullExecutorTest test`
Expected: FAIL，原因是 orchestrator/result 尚不存在。

**Step 3: Write minimal implementation**

实现 create 内全量前置校验、探测连接、原子 Holder、有限借出/复核和统一 release；序列化前只复制安全统计字段。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=DataSourceConnectionPoolFullExecutorTest test`
Expected: PASS。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: exhaust and recover datasource pools"`

- [ ] **Task backend-006: 让 Agent handlers 可选返回结构化实验结果并保持旧模型兼容**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-service/src/main/java/com/alibaba/chaosblade/exec/service/handler/InjectionResultProvider.java`
- Modify: `../chaosblade-exec-jvm/chaosblade-exec-service/src/main/java/com/alibaba/chaosblade/exec/service/handler/CreateHandler.java`
- Modify: `../chaosblade-exec-jvm/chaosblade-exec-service/src/main/java/com/alibaba/chaosblade/exec/service/handler/StatusHandler.java`
- Modify: `../chaosblade-exec-jvm/chaosblade-exec-service/src/main/java/com/alibaba/chaosblade/exec/service/handler/DestroyHandler.java`
- Modify: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourceModelSpec.java`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-service/src/test/java/com/alibaba/chaosblade/exec/service/handler/StructuredResultHandlerTest.java`

**Reasoning:**
- Agent `Response.result` 仍是 String；通过可选 provider 返回 JSON 字符串可最小扩展新模型，同时不改变所有旧模型的 `model.toString()`/`success` 响应。

**Depends on:** `backend-005`

**Interfaces:**
- Consumes: preCreate/preDestroy handlers 和 datasource registry tombstone。
- Produces: `InjectionResultProvider.getInjectionResult(uid)` 及 handler 的 opt-in 分支。
- Contract checks: datasource create/status/destroy 返回同 schema JSON；destroy 即使先移除 StatusManager model 仍可从 tombstone取结果；非 provider byte-for-byte 保持旧响应。

**Step 1: Write the failing test**

新增 provider model 与 legacy model doubles，分别断言三个 handler 的结构化/旧响应以及 destroy 后 tombstone 查询。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-service -am -Dtest=StructuredResultHandlerTest test`
Expected: FAIL，原因是 provider 与 handler opt-in 分支不存在。

**Step 3: Write minimal implementation**

增加无侵入 provider 接口；handler 仅在 model 实现该接口时返回其 JSON，否则执行现有代码路径。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-service -am -Dtest=StructuredResultHandlerTest test`
Expected: PASS，并执行现有 service tests 通过。

**Step 5: Commit**

Run: `git add chaosblade-exec-service chaosblade-exec-plugin/chaosblade-exec-plugin-datasource && git commit -m "feat: expose datasource experiment results"`

- [ ] **Task backend-007: 注册插件 SPI、生成 JVM spec 并完成模块回归**

**Files:**
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourcePlugin.java`
- Create: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/main/resources/META-INF/services/com.alibaba.chaosblade.exec.common.aop.Plugin`
- Test: `../chaosblade-exec-jvm/chaosblade-exec-plugin/chaosblade-exec-plugin-datasource/src/test/java/com/alibaba/chaosblade/exec/plugin/datasource/DataSourcePluginTest.java`

**Reasoning:**
- 代码存在不代表 Agent 能加载；必须验证 Java SPI、direct injection 模型、打包清单和 YAML spec，同时用旧 Druid tests 锁住回归边界。

**Depends on:** `backend-006`

**Interfaces:**
- Consumes: `Plugin`、direct injection handler、Maven spec builder。
- Produces: 可加载 datasource plugin JAR 和包含准确 flags 的 JVM spec YAML。
- Contract checks: SPI 只注册新插件；旧 Druid module bytecode/测试行为不变；新 plugin 无强制 Spring/Hikari/Druid runtime 依赖。

**Step 1: Write the failing test**

断言 ServiceLoader 发现 datasource plugin、pointcut/direct-injection 组合合法、model/action/flags 可由 build service序列化。

**Step 2: Run test to verify it fails**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am -Dtest=DataSourcePluginTest test`
Expected: FAIL，原因是 plugin/SPI 资源不存在。

**Step 3: Write minimal implementation**

创建插件入口与 SPI 文件，连接 model executor 和 unload cleanup；修正 module assembly/spec build 使 JAR 被整体发行物收集。

**Step 4: Run test to verify it passes**

Run: `mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-datasource -am test && mvn -pl chaosblade-exec-plugin/chaosblade-exec-plugin-druid -am test`
Expected: PASS，生成 spec 含 datasource 且旧 Druid suite 不变。

**Step 5: Commit**

Run: `git add chaosblade-exec-plugin && git commit -m "feat: package datasource chaos plugin"`

- [ ] **Task backend-008: 建立 JDK、Spring Boot 与连接池兼容矩阵**

**Files:**
- Create: `../chaosblade-exec-jvm/test/compatibility/datasource/pom.xml`
- Create: `../chaosblade-exec-jvm/test/compatibility/datasource/src/test/java/com/alibaba/chaosblade/exec/compatibility/DatasourceAttachCompatibilityTest.java`
- Create: `../chaosblade-exec-jvm/test/compatibility/datasource/run-matrix.sh`

**Reasoning:**
- Spring Boot 内部 Context 注册与 Hikari 公开能力跨版本变化，必须以真实依赖组合验证热 attach 后发现、借满和恢复，而不是只依赖 mocks。

**Depends on:** `backend-007`

**Interfaces:**
- Consumes: 已打包 Agent/plugin、Boot 2.7/3.2、Hikari 4/5、Druid 1.2 与 JDK 8/11/17 容器。
- Produces: 固定四类组合的 matrix runner 和逐组合测试报告。
- Contract checks: 应用先启动再 attach；create 达标、explicit destroy 恢复、TTL 恢复；不支持反射结构明确失败而非误选。

**Step 1: Write the failing test**

建立参数化 fixture 和 runner，先要求 datasource plugin artifact 与 attach result；当前 feature 缺失时矩阵失败。

**Step 2: Run test to verify it fails**

Run: `test/compatibility/datasource/run-matrix.sh`
Expected: FAIL，原因是矩阵 fixture/目标 artifact 尚未接通。

**Step 3: Write minimal implementation**

用固定 Maven/JDK 容器启动代表性 Boot 应用，attach 已构建 Agent，断言 Hikari/Druid 的 create/destroy/TTL；保存 surefire 报告。

**Step 4: Run test to verify it passes**

Run: `test/compatibility/datasource/run-matrix.sh`
Expected: PASS，所有声明组合输出独立 PASS 与报告路径。

**Step 5: Commit**

Run: `git add test/compatibility/datasource && git commit -m "test: cover datasource compatibility matrix"`
