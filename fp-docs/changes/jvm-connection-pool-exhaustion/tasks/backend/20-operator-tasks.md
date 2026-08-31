# Operator Tasks

- [ ] **Task backend-009: 扩展逐资源状态以承载可选结构化 result**

**Files:**
- Modify: `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/types.go`
- Modify: `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/zz_generated.deepcopy.go`
- Modify: `../chaosblade-operator/deploy/crds/chaosblade.io_chaosblades_crd.yaml`
- Modify: `../chaosblade-operator/deploy/helm/chaosblade-operator/crds/crd.yaml`
- Modify: `../chaosblade-operator/deploy/helm/chaosblade-operator-arm64/crds/crd.yaml`
- Test: `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/types_test.go`

**Reasoning:**
- Agent detail 必须按 Pod/容器归属持久化；使用可选 Raw JSON 可避免 Operator 重复定义 Java result schema，并让旧 CR 实例省略字段。

**Depends on:** None

**Interfaces:**
- Consumes: Kubernetes JSON serialization/deepcopy 与现有 `ResourceStatus`。
- Produces: `ResourceStatus.Result` 可选结构化 JSON 和一致的 served CRD schema。
- Contract checks: object round-trip 不双重转义；nil 省略；deepcopy 不共享底层 bytes；canonical 与两个 Helm CRD 均接受 object。

**Step 1: Write the failing test**

新增 round-trip/deepcopy 测试，断言 `result.state` 可作为 JSON 对象读取且旧 status JSON 无 result。

**Step 2: Run test to verify it fails**

Run: `go test ./pkg/apis/chaosblade/v1alpha1/...`
Expected: FAIL，原因是 `ResourceStatus.Result` 尚不存在。

**Step 3: Write minimal implementation**

增加带 `omitempty` 的 raw extension/result 字段，更新 deepcopy，并只同步当前发行链使用的 canonical/Helm CRD schema。

**Step 4: Run test to verify it passes**

Run: `go test ./pkg/apis/chaosblade/v1alpha1/...`
Expected: PASS，三个 CRD 的 schema 检查脚本通过。

**Step 5: Commit**

Run: `git add pkg/apis/chaosblade/v1alpha1 deploy/crds deploy/helm && git commit -m "feat: retain per-resource experiment results"`

- [ ] **Task backend-010: 兼容解析 legacy UID 与 datasource 详细子响应**

**Files:**
- Modify: `../chaosblade-operator/exec/model/executor.go`
- Modify: `../chaosblade-operator/exec/model/executor_copy.go`
- Modify: `../chaosblade-operator/exec/model/executor_nsexec.go`
- Test: `../chaosblade-operator/exec/model/executor_test.go`

**Reasoning:**
- 当前 create 强制把 `response.Result` 断言为 string；新 envelope 必须提取真实子 UID 并保留 detail，且不能让错误格式被当作成功 UID。

**Depends on:** `backend-009`

**Interfaces:**
- Consumes: child blade JSON `result` 为 string 或 `{uid,detail}`。
- Produces: `ResourceStatus.Id` 始终为真实 child UID，`Result` 仅在 detail 存在时赋值。
- Contract checks: legacy string 行为不变；新 map 成功；空 uid、非 object detail、未知类型或 JSON 损坏返回资源失败并保留安全错误。

**Step 1: Write the failing test**

对共享 decode helper 建表测试，再覆盖 copy/nsexec 两条执行路径均写入相同 `Id/Result`。

**Step 2: Run test to verify it fails**

Run: `go test ./exec/model/... -run 'Test.*(Legacy|Detailed|Malformed).*' -count=1`
Expected: FAIL，新对象结果触发 string assertion 或丢失 detail。

**Step 3: Write minimal implementation**

抽取单一 decoder，显式类型分支并验证非空 UID；两个并行 executor 复用 helper，不复制解析逻辑。

**Step 4: Run test to verify it passes**

Run: `go test ./exec/model/... -count=1`
Expected: PASS，旧 string 与新 envelope 全部通过。

**Step 5: Commit**

Run: `git add exec/model && git commit -m "feat: decode detailed child experiment results"`

- [ ] **Task backend-011: 为 datasource 多资源创建实现 action-specific 原子补偿**

**Files:**
- Modify: `../chaosblade-operator/exec/controller.go`
- Test: `../chaosblade-operator/exec/controller_test.go`

**Reasoning:**
- 新故障在部分 Pod 成功时会留下真实连接占用；必须等待创建 fan-out 收敛后，使用每个成功结果的 child UID调用现有 Destroy 链补偿，再返回整体失败。

**Depends on:** `backend-010`

**Interfaces:**
- Consumes: `ExperimentContext` target/action、create `ResourceStatuses`、controller `Destroy`。
- Produces: datasource-only `rollbackAtomicCreate`，状态为 `ROLLED_BACK`、`ROLLBACK_FAILED` 或 `PASSIVE_RELEASED`。
- Contract checks: 任一 create 失败/未达标/结果不可解析都会补偿全部成功资源；只按 child UID；保留原错误和补偿错误；所有成功时不补偿。

**Step 1: Write the failing test**

用 fake resource controller 覆盖两成功一失败、补偿一处失败、目标消失、全部成功和非 datasource Action。

**Step 2: Run test to verify it fails**

Run: `go test ./exec/... -run 'TestDatasourceCreateCompensatesSuccessfulResources' -count=1`
Expected: FAIL，当前 controller 在部分失败后直接返回且没有 Destroy 调用。

**Step 3: Write minimal implementation**

在 `ResourceDispatchedController.Create` 成功收集后加入精确 target/action guard，构造仅含成功 child UID 的 destroy context，执行现有 controller Destroy 并合并状态。

**Step 4: Run test to verify it passes**

Run: `go test ./exec/... -run 'TestDatasourceCreate|Test.*Dispatched.*' -count=1`
Expected: PASS，fake 记录每个成功 UID 恰好一次 destroy。

**Step 5: Commit**

Run: `git add exec/controller.go exec/controller_test.go && git commit -m "feat: compensate partial datasource experiments"`

- [ ] **Task backend-012: 完成 Operator 全量回归、CRD 一致性与竞态验证**

**Files:**
- Modify: `../chaosblade-operator/exec/controller_test.go`
- Modify: `../chaosblade-operator/exec/model/executor_test.go`
- Test: `../chaosblade-operator/pkg/apis/chaosblade/v1alpha1/types_test.go`

**Reasoning:**
- ResourceStatus 与 dispatch 是共享基础设施；除目标 Action外任何状态机、并发 fan-out、destroy 筛选和旧结果行为都不能漂移。

**Depends on:** `backend-011`

**Interfaces:**
- Consumes: backend-009 至 backend-011 的 CRD、decoder 和 compensation 合约。
- Produces: Operator 发布级测试门禁和可重复 race 证据。
- Contract checks: 非目标 Action 零补偿；重复 destroy 幂等；资源顺序不影响结果归属；CRD schema 与 Go type 一致。

**Step 1: Write the failing test**

补齐 table tests：legacy 全成功/部分失败、新 Action 全成功/部分失败/rollback failure、并行结果乱序与 destroy 重入。

**Step 2: Run test to verify it fails**

Run: `go test -race ./exec/... ./pkg/apis/chaosblade/v1alpha1/... -count=1`
Expected: FAIL，未覆盖的边界至少暴露一项 contract 差异或 race。

**Step 3: Write minimal implementation**

只修正共享 helper 的归属/同步问题和 schema 生成差异，不扩大到其他 Action 的行为重构。

**Step 4: Run test to verify it passes**

Run: `go test -race ./exec/... ./pkg/apis/chaosblade/v1alpha1/... -count=1`
Expected: PASS；`go test ./...` 通过或只记录与变更无关且有证据的既有失败。

**Step 5: Commit**

Run: `git add exec pkg/apis/chaosblade/v1alpha1 deploy && git commit -m "test: verify datasource operator orchestration"`
