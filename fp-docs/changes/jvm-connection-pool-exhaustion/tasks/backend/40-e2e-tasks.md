# Kubernetes E2E Tasks

- [ ] **Task backend-017: 创建 Hikari/Druid 自包含 Kubernetes 测试应用**

**Files:**
- Create: `test/e2e/jvm-connection-pool/app/pom.xml`
- Create: `test/e2e/jvm-connection-pool/app/src/main/java/io/chaosblade/e2e/ConnectionPoolApplication.java`
- Create: `test/e2e/jvm-connection-pool/app/src/main/java/io/chaosblade/e2e/DataSourceConfiguration.java`
- Create: `test/e2e/jvm-connection-pool/app/src/main/java/io/chaosblade/e2e/QueryController.java`
- Create: `test/e2e/jvm-connection-pool/app/src/test/java/io/chaosblade/e2e/ConnectionPoolApplicationTest.java`
- Create: `test/e2e/jvm-connection-pool/app/Dockerfile`
- Create: `test/e2e/jvm-connection-pool/k8s/namespace.yaml`
- Create: `test/e2e/jvm-connection-pool/k8s/apps.yaml`

**Reasoning:**
- 真实效果必须由业务请求证明；H2 提供无外部凭据的数据库，两个 profile 暴露同名 `coreDataSource`，小池和短获取超时使耗尽/恢复在测试机上快速可判定。

**Depends on:** `backend-008`

**Interfaces:**
- Consumes: Spring Boot、H2、Hikari/Druid 和 Kubernetes Deployment/Service。
- Produces: `/query`、`/pool`、`/actuator/health`；hikari/druid 两个 workload，最大连接数固定为 4。
- Contract checks: 无故障时 query 200；池满时并发 query 在配置时限内超时/失败；恢复后连续 query 200；`coreDataSource` canonical name 一致。

**Step 1: Write the failing test**

为两个 profile 参数化启动 Context，断言 bean 类型、max=4、query 成功与占满四条外部连接后的第五条请求超时。

**Step 2: Run test to verify it fails**

Run: `mvn -f test/e2e/jvm-connection-pool/app/pom.xml test`
Expected: FAIL，测试应用和 profile 尚不存在。

**Step 3: Write minimal implementation**

实现只执行 `SELECT 1` 的 controller、安全池指标 endpoint、两个 profile 和非 root 容器；K8s manifest 固定 namespace/labels/readiness/resources。

**Step 4: Run test to verify it passes**

Run: `mvn -f test/e2e/jvm-connection-pool/app/pom.xml test`
Expected: PASS；两个 profile 均完成测试且镜像构建成功。

**Step 5: Commit**

Run: `git add test/e2e/jvm-connection-pool/app test/e2e/jvm-connection-pool/k8s && git commit -m "test: add datasource kubernetes fixtures"`

- [ ] **Task backend-018: 自动化三仓构建、集群部署与效果/恢复断言**

**Files:**
- Create: `test/e2e/jvm-connection-pool/lib.sh`
- Create: `test/e2e/jvm-connection-pool/build-and-deploy.sh`
- Create: `test/e2e/jvm-connection-pool/run-e2e.sh`
- Create: `test/e2e/jvm-connection-pool/k8s/mixed-compensation.yaml`

**Reasoning:**
- E2E 不能依赖手工观察；脚本要输出版本、等待条件、保存 API/CR 状态，且每个场景都有定量失败和恢复断言，才能在一次远程运行中重复验证。

**Depends on:** `backend-016`, `backend-017`

**Interfaces:**
- Consumes: 三个同名 feature 分支、本地 Docker/Kubernetes、根 build image、`kubectl`、新 CLI。
- Produces: versioned blade/operator/app images；限定 namespace 部署；create/destroy/TTL/mixed-failure scenario runner。
- Contract checks: 任何 build/deploy/create/result/effect/recovery/compensation gate 缺失即非零退出；trap 只清理本测试 namespace；日志不含 kubeconfig/token/凭据。

**Step 1: Write the failing test**

先实现 shell 自检和场景 gate 定义，使用假 `blade/kubectl/curl` 证明缺少 structured result、业务超时、恢复或 rollback 状态都会失败。

**Step 2: Run test to verify it fails**

Run: `test/e2e/jvm-connection-pool/run-e2e.sh --self-test`
Expected: FAIL，构建/部署 helper 与所有 gate 尚未实现。

**Step 3: Write minimal implementation**

实现可重入脚本：记录三仓 SHA，容器构建并导入节点 runtime，apply CRD/operator/tool/apps，等待 ready，再依次验证 Hikari explicit destroy、Druid TTL 和 mixed valid/invalid Bean 原子补偿。

**Step 4: Run test to verify it passes**

Run: `test/e2e/jvm-connection-pool/run-e2e.sh --self-test`
Expected: PASS；`bash -n` 和可用时 `shellcheck` 无错误。

**Step 5: Commit**

Run: `git add test/e2e/jvm-connection-pool && git commit -m "test: automate datasource kubernetes e2e"`

- [ ] **Task backend-019: 在 bennieliu-honor 执行完整 E2E 并固化证据**

**Files:**
- Create: `fp-docs/changes/jvm-connection-pool-exhaustion/e2e-evidence.md`
- Modify: `fp-docs/changes/jvm-connection-pool-exhaustion/tasks/backend/10-jvm-agent-tasks.md`
- Modify: `fp-docs/changes/jvm-connection-pool-exhaustion/tasks/backend/20-operator-tasks.md`
- Modify: `fp-docs/changes/jvm-connection-pool-exhaustion/tasks/backend/30-chaosblade-integration-tasks.md`
- Modify: `fp-docs/changes/jvm-connection-pool-exhaustion/tasks/backend/40-e2e-tasks.md`

**Reasoning:**
- 用户指定测试机为最终验收环境；只有远程 Kubernetes 内 attach、业务阻塞、结构化逐 Pod 结果、主动/TTL恢复和部分失败补偿全部有实测证据，需求才完成。

**Depends on:** `backend-018`

**Interfaces:**
- Consumes: SSH alias `bennieliu-honor`、远程 Docker/Kubernetes、E2E runner。
- Produces: 可审查的 `e2e-evidence.md` 和按实际验证结果勾选的唯一 task markers。
- Contract checks: 证据含时间、环境、三仓 SHA、镜像、命令、父/子 UID、每 Pod result、故障期 query 结果、destroy/TTL 恢复、mixed compensation、清理结果和最终 PASS/FAIL。

**Step 1: Write the failing test**

在证据模板中先列全部 mandatory gates，并运行 preflight；任一 gate 尚无远程输出时整体状态保持 FAIL/未完成。

**Step 2: Run test to verify it fails**

Run: `ssh bennieliu-honor 'test -x /root/chaosblade-jvm-pool-e2e/chaosblade/test/e2e/jvm-connection-pool/run-e2e.sh'`
Expected: FAIL，远程功能源码/runner 尚未同步或尚未执行。

**Step 3: Write minimal implementation**

同步三个仓库到独立远程目录，执行 preflight、构建部署和全场景 runner；将脱敏后的原始关键信息、断言结果和清理状态写回证据文件，失败则修复对应 owner task 并重跑相关测试/E2E。

**Step 4: Run test to verify it passes**

Run: `ssh bennieliu-honor 'cd /root/chaosblade-jvm-pool-e2e/chaosblade && test/e2e/jvm-connection-pool/run-e2e.sh'`
Expected: PASS：Hikari explicit destroy、Druid hard TTL、multi-Pod partial-failure compensation 与 post-recovery queries 全部通过，test namespace 无活动故障残留。

**Step 5: Commit**

Run: `git add fp-docs/changes/jvm-connection-pool-exhaustion test/e2e/jvm-connection-pool && git commit -m "test: verify datasource exhaustion on kubernetes"`
