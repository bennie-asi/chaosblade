# ChaosBlade Root Integration Tasks

- [x] **Task backend-013: 仅为新 datasource Action 向 Agent 转发 timeout**

**Files:**
- Modify: `exec/jvm/executor.go`
- Test: `exec/jvm/sandbox_test.go`

**Reasoning:**
- 现有 JVM create URL 全局删除 timeout；硬 TTL 要求新 Action 收到该值，但直接取消过滤会改变所有旧 JVM Action。

**Depends on:** `backend-006`

**Interfaces:**
- Consumes: JVM executor target/action/flags。
- Produces: datasource/connectionpoolfull create URL 含 timeout，其他 target/action 仍无 timeout。
- Contract checks: 默认/显式 timeout 都透传；status/destroy 不伪造 timeout；旧 JVM URL 测试字节级保持。

**Step 1: Write the failing test**

在 sandbox URL 表驱动测试中分别构造 datasource、legacy Druid 和另一 JVM Action，断言 query 参数差异。

**Step 2: Run test to verify it fails**

Run: `go test ./exec/jvm -run TestCreateURLForwardsTimeoutOnlyForDatasource -count=1`
Expected: FAIL，datasource URL 中 timeout 被当前全局过滤逻辑删除。

**Step 3: Write minimal implementation**

将 timeout filter 改为精确 target/action 条件；不要修改共享 flags 或其他 executor。

**Step 4: Run test to verify it passes**

Run: `go test ./exec/jvm -count=1`
Expected: PASS。

**Step 5: Commit**

Run: `git add exec/jvm/executor.go exec/jvm/sandbox_test.go && git commit -m "feat: forward datasource hard timeout"`

- [x] **Task backend-014: 在根 CLI 与 Kubernetes状态中保留新 Action 详细结果**

**Files:**
- Modify: `cli/cmd/create.go`
- Modify: `cli/cmd/destroy.go`
- Modify: `apis/chaosblade/v1alpha1/chaosblade_types.go`
- Test: `cli/cmd/command_test.go`
- Test: `cli/cmd/destroy_test.go`
- Test: `apis/chaosblade/v1alpha1/chaosblade_types_test.go`

**Reasoning:**
- create 当前无条件把成功 response result 覆盖为 UID，destroy 丢弃 executor detail；必须精确 opt-in 新 Action，才能展示实际 hold/release 证据且不破坏脚本依赖的旧输出。

**Depends on:** `backend-009`, `backend-013`

**Interfaces:**
- Consumes: Agent JSON string detail、根 experiment UID 与 Operator ResourceStatus result。
- Produces: 新 Action `{uid,detail}` envelope 和 destroy detail；根 ResourceStatus 可反序列化 object result。
- Contract checks: 新 create result UID 可供 destroy；detail 是 object 非转义字符串；legacy create 仍为 string UID；legacy destroy 仍为现有 experiment result。

**Step 1: Write the failing test**

用 fake executor 返回 Agent detail，断言 datasource create/destroy 保留 detail；同一测试锁住非目标 Action 旧输出和 ResourceStatus JSON round-trip。

**Step 2: Run test to verify it fails**

Run: `go test ./cli/cmd ./apis/chaosblade/v1alpha1 -run 'TestDatasource|TestResourceStatusResult' -count=1`
Expected: FAIL，当前 create 覆盖 result、destroy 丢弃 response 或 type 缺字段。

**Step 3: Write minimal implementation**

增加精确 action predicate 与安全 JSON decode；仅新 Action组装 envelope/返回 destroy response，并同步可选 raw result type。

**Step 4: Run test to verify it passes**

Run: `go test ./cli/cmd ./apis/chaosblade/v1alpha1 -count=1`
Expected: PASS，legacy snapshots 无变化。

**Step 5: Commit**

Run: `git add cli/cmd apis/chaosblade/v1alpha1 && git commit -m "feat: preserve datasource experiment details"`

- [x] **Task backend-015: 支持三仓本地功能分支的可复现容器构建**

**Files:**
- Modify: `Makefile`
- Create: `test/e2e/jvm-connection-pool/verify-make-overrides.sh`

**Reasoning:**
- 根构建当前固定 clone 官方 v1.8.0，无法携带未发布的 Agent/Operator feature；覆盖项必须显式、可审计且不改变默认官方 build。

**Depends on:** `backend-008`, `backend-012`, `backend-014`

**Interfaces:**
- Consumes: `BLADE_EXEC_JVM_PROJECT/BRANCH`、`BLADE_OPERATOR_PROJECT/BRANCH` 环境/Make 变量。
- Produces: 默认值不变的 `?=` 覆盖契约和本地 `file://` repo build path。
- Contract checks: 无覆盖时仍指向官方 v1.8.0；覆盖后 clone 指令准确使用本地 repo/功能分支；路径含空格时明确拒绝并报错。

**Step 1: Write the failing test**

脚本使用 `make -n` 比较默认与 override 输出，要求四个变量均可覆盖且默认 URL/tag 未改变。

**Step 2: Run test to verify it fails**

Run: `test/e2e/jvm-connection-pool/verify-make-overrides.sh`
Expected: FAIL，当前 Makefile 的硬赋值覆盖调用方输入。

**Step 3: Write minimal implementation**

仅将四个外部组件变量改为条件赋值，保留原默认值、目标和打包目录；加入 dry-run checker。

**Step 4: Run test to verify it passes**

Run: `test/e2e/jvm-connection-pool/verify-make-overrides.sh`
Expected: PASS，默认/override 两组断言均通过。

**Step 5: Commit**

Run: `git add Makefile test/e2e/jvm-connection-pool/verify-make-overrides.sh && git commit -m "build: allow local chaosblade components"`

- [x] **Task backend-016: 验证动态主机/Kubernetes命令和根仓全量回归**

**Files:**
- Modify: `cli/cmd/command_test.go`
- Modify: `cli/cmd/prepare_jvm_test.go`
- Test: `exec/jvm/sandbox_test.go`

**Reasoning:**
- datasource 命令来自 JVM YAML 动态加载，K8s 又通过 scope 前缀转换；必须用实际生成 spec 验证 `datasource` 与 `container-datasource`，而不是新增硬编码命令。

**Depends on:** `backend-015`

**Interfaces:**
- Consumes: backend-007 生成的 JVM YAML 与根 command registration。
- Produces: host/K8s 命令树、flags/defaults 和回归门禁。
- Contract checks: host target=`datasource`；K8s target=`container-datasource`；三项新 flags 恰好出现；旧 Druid target/action 仍可执行。

**Step 1: Write the failing test**

将真实 feature JVM spec 作为测试输入，查找两种 command path 和 defaults，并保留 legacy Druid path 断言。

**Step 2: Run test to verify it fails**

Run: `go test ./cli/cmd ./exec/jvm -run 'TestDatasourceCommands|TestCreateURL' -count=1`
Expected: FAIL，根测试 fixture 尚无 datasource spec/结果契约。

**Step 3: Write minimal implementation**

只调整测试 fixture/spec 装载兼容点；若生产动态注册已满足契约，不增加任何 datasource 硬编码。

**Step 4: Run test to verify it passes**

Run: `go test ./cli/cmd ./exec/jvm ./exec/kubernetes ./apis/chaosblade/v1alpha1 -count=1`
Expected: PASS；随后容器内 `go test ./...` 通过或记录与变更无关的既有失败。

**Step 5: Commit**

Run: `git add cli/cmd exec/jvm test/e2e/jvm-connection-pool && git commit -m "test: verify datasource command integration"`
