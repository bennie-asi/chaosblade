# Coverage Matrix

| Source | Requirement / Boundary | Tasks | Verification |
| --- | --- | --- | --- |
| proposal.md P-001/P-002 | 新通用 datasource Action，主机与 K8s 复用，旧 Druid 不变 | `backend-001`, `backend-007`, `backend-016` | JVM spec tests；`go test ./cli/cmd/...`；legacy Druid command snapshot |
| proposal.md P-003 | 精确 Spring Bean 定位，支持 Hikari/Druid | `backend-002`, `backend-003`, `backend-008` | resolver/adapter tests；JDK/Boot/pool matrix |
| proposal.md P-004 | 按目标比例借出并持有，不执行 SQL/事务 | `backend-002`, `backend-005` | calculation and executor tests assert only `DataSource.getConnection/Connection.close` |
| proposal.md P-005 | UID 隔离、幂等 destroy、60 秒 TTL、Agent unload | `backend-004`, `backend-005`, `backend-006` | concurrency/race/TTL/unload tests and live TTL E2E |
| proposal.md P-006 | 可观测结果与稳定失败原因 | `backend-005`, `backend-006`, `backend-009`, `backend-010`, `backend-014` | Agent handler, Go decoder and CLI output contract tests |
| proposal.md P-007 | K8s 多 Pod 逐资源结果与部分失败原子补偿 | `backend-009`, `backend-010`, `backend-011`, `backend-012`, `backend-019` | Operator controller tests plus mixed-target live E2E |
| proposal.md P-008 | 快速默认值和可重复 E2E | `backend-017`, `backend-018`, `backend-019` | one-command E2E using default bean/percent/timeout |
| design D-003/D-006 | Boot 2.7/3.2 热 attach 定位器与分层适配器 | `backend-002`, `backend-003`, `backend-008` | attach-after-start matrix; ambiguous context negative test |
| design D-007/D-008 | DataSource 粒度互斥；create 内同步探测和原子回滚 | `backend-004`, `backend-005` | simultaneous create and probe/partial-acquire failure tests |
| design D-009 | owned connection、Holder 与 identity index 形成恢复硬判据 | `backend-004`, `backend-005`, `backend-019` | release-incomplete unit test and destroy/TTL live recovery |
| design D-010 | JDK/Boot/Hikari/Druid 代表性兼容矩阵 | `backend-008`, `backend-017`, `backend-019` | matrix runner plus live Hikari and Druid workloads |
| Backend boundary | JVM Agent handler 对不实现 provider 的旧模型完全兼容 | `backend-006` | legacy create/status/destroy response tests |
| Backend boundary | Operator 只对目标 Action补偿，旧 string UID 和非目标 Action不变 | `backend-010`, `backend-011`, `backend-012` | table-driven Go regression suite |
| Backend boundary | 根 JVM executor 只为目标 Action放行 timeout | `backend-013` | focused URL query tests |
| Build boundary | 三仓功能分支可由固定容器构建复现，默认上游版本不漂移 | `backend-015`, `backend-018` | dry-run override test; Docker build logs with git SHAs |
| Live acceptance | 连接耗尽期间核心查询出现阻塞/超时，destroy 与 TTL 后恢复 | `backend-017`, `backend-018`, `backend-019` | Kubernetes endpoint latency/error assertions and post-recovery probes |
| Live acceptance | 多 Pod 一处失败时已成功 Pod 自动回滚，无残留 Holder | `backend-018`, `backend-019` | mixed valid/invalid Bean experiment and follow-up query/status evidence |
