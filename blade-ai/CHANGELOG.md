# Changelog

English · [简体中文](CHANGELOG.zh-CN.md)

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.6.5] - 2026-08-04

### Changed
- Tool schema context slimming: schemas for 22 tools reduced from 12,696 to 9,354 tokens (-26%), lowering per-turn token consumption
- Improved debug Pod recognition and injection carrier management logic

### Added
- Skill library: added application main-process failure modes and sustained-injection-mode recipes
- Documentation: added Transport Channel connection methods

## [0.6.4] - 2026-08-03

### Added
- New Pod network bandwidth throttling fault use case; added kubectl fallback recipes for container anomalies and disk fill

### Fixed
- LLM retry-history memory prevents re-sending commands that already failed
- Fixed an exception in `blade-ai update` caused by concurrent deletion during version directory scans
- Fixed an event-loop close error in the TaskStore shutdown flow
- Shell execution no longer sends kill signals to special process groups

### Changed
- Verifier now recognizes the experiment setup phase as a warning state, reducing false positives
- Improved skill catalog fingerprint computation for more accurate use-case syncing

## [0.6.3] - 2026-08-03

Pure release packaging version; no functional changes.

## [0.6.2] - 2026-08-03

### Added
- Progress ledger: end-to-end execution state tracking with truncation policies that protect key milestones
- Host read-only probe commands and parameter-level change detection
- Auxiliary LLM call audit records (session store), with a fixed model-name field in session records
- Support for Pod-level ephemeral debug containers
- Uninstall supports per-version removal and preserves the config directory by default
- Added qwen3.7-max / qwen3.7-plus model window configuration; updated model window sizes and compaction budgets
- Optimized context compaction logic; added a stagnation frequency ceiling setting

### Changed
- **Full internationalization**: built-in prompts, log messages, architecture diagrams, and config hints switched from Chinese to English; both READMEs fully restructured; safety-layer diagram updated
- Detailed carrier rejection reporting: every rejection now carries an explicit reason and remediation suggestion
- Fault injection confirmation steps merged for a simpler submission flow; unified intent-clarification rejection handling with better reason visibility
- Finer-grained kubectl subcommand and argument validation, with Chinese action-word mapping
- Tool visibility filtered by a single profile
- Context token counting anchored to provider usage; removed fixed message-count truncation to preserve full history
- Prompt escalation control switched from `stagnation_frequency_ceiling` to `hint_escalate_after`
- Preflight diagnostics now emitted in English

### Fixed
- Hardened `blade-ai update` / `uninstall` against real failure modes; `update --check` works from any install type
- Fixed read-only host-escape probes being falsely rejected
- Fixed empty AI replies being misinterpreted as conversation end
- Corrected execute-loop termination: execution completion must always pass through verification
- Fixed routing of replica Pods created by `kubectl --copy-to`
- Fixed intent-clarification rejection reasons not being persisted; node messages are now guaranteed to persist to TUI session files
- Fixed fault-binary classification to support Pod-level operations
- Handled truncated-output tool calls to avoid execution errors; added explicit unique identifiers to HumanMessage
- Fixed read/write device detection and signal handling; clarified missing parameters to improve unknown-confidence feedback
- Corrected loop-detection thresholds; filtered backend CLI capability probes from the intent tool list
- L4 routes chaos skill selection by runtime channel
- Updated ChaosBlade installer checksums

## [0.6.1] - 2026-07-28

### Added
- **Python application-layer fault drills**: new python_agent and python-app-chaos-skills skill library, extending drill scope from K8s/host to Python application processes
- New `blade-ai serve` command to start the HTTP API server
- Cross-fault-family scope change protection (approving a network fault but executing a process fault is now blocked)
- New `injection_start_time` field for accurate detection of whether an injection command was issued
- Improved injection-step semantic detection and kubectl patch recognition

### Changed
- Unified tool-call permission checks and filtering based on environment capability context
- CLI output formatting unified via the OutputFormat enum
- Unified task ID retrieval and task identity recognition; status tracker supports event channel IDs and bounded event history

### Fixed
- Corrected active-task query criteria to support the fault_spec canonical form
- Corrected channel capability gating for Python application injection tools

## [0.6.0] - 2026-07-27

### Added
- **Host chaos skill library (host-chaos-skills) debut**: host fault drill and chaos engineering skill documentation with command references
- **Environment capability context introduced** and applied across execution nodes
- New node-level network fault use cases; availability-zone network partition injection strategy standardized and hardened (including concurrent node creation flow)
- Documentation for ChaosBlade-free native-command fallback recipes; kubectl debug simulated fault injection guide
- Baseline command data and execution layers; multi-target baseline command expansion; baseline capture supports multiple profiles and host commands
- Dynamic discovery of unregistered carriers; structured tracking and cleanup of debug Pods
- New `request_replan` tool; structured replan requests (invalid plan / needs investigation)
- Deterministic host evidence supplementation for the Host profile; host-channel verification no longer relies on K8s-specific prompts
- ResilientChatOpenAI: automatic retry on LLM transport failures
- Automatic cleanup of capability-probe debug Pods (with faster concurrent deletion)
- Dynamic `--wait-timeout` for wiz commands; independent timeout configuration for wiz tasks
- Auto-approval event stream for a smoother streaming confirmation experience; config display now shows full content (including sensitive fields)
- Injection tool calls bound to task_id with multi-target coverage detection
- Target classification for kubectl and blade_create commands (new classifier)
- Improved usability and self-recovery of kubectl-based chaos injection commands

### Changed
- **Environment-agnostic refactor**: prompts no longer carry a profile parameter; the Agent moves from K8s-specific to a general-purpose execution engine
- Unified transport layer: local kubectl calls replaced by unified transport execution; host-local injection tool support removed
- Removed the legacy injection graph in favor of the new Pipeline graph; removed the legacy classifier module
- Soft self-check mechanism for multi-step injections; snapshot scope decisions use `can_capture` instead of static `requires_namespace`
- Optimized plan-change interfaces and routing state management; restructured the nodes subpackage

### Fixed
- Fixed event-loop hang on task cancellation; fixed tool-call event unique identification
- Fixed container command passthrough in command parsing under kubewiz mode
- Fixed node and availability-zone network isolation recipes; strengthened systemd-run timed recovery
- Fixed debug profile passing and read-only probe determination
- Better error messages and retries for failed baseline commands; longer stderr previews
- Fixed batch intent auto-confirmation and state reset; fixed TUI streaming scrollbar artifacts
- Prevented trailing Markdown newlines from affecting safe split-point calculation

## [0.5.8] - 2026-07-06

### Added
- Multi-tenancy: `tenant_id` context propagation; removed the obsolete experiment storage module
- Event-stream handling and phase-step tracking for the recovery flow
- SSE heartbeat to prevent long-lived connections from dropping

### Fixed
- Avoided thread-safety issues caused by Linux fork
- Disabled backend SSE batching to reduce streaming latency
- kubectl and wiz paths now resolved via absolute paths to avoid fork crashes

### Changed
- Experience file renamed from AGENT.md to EXPERIENCE.md; optimized TaskStore initialization and switching logic

## [0.5.7] - 2026-06-30

### Added
- Replan mechanism after verification failure

### Fixed
- Corrected verification state determination logic
- Fixed blade path resolution errors; hardcoded paths replaced with a unified resolver
- No-skill-active situations are now recoverable and retryable; corrected skill activation invocation rules
- Fixed logging failures after the log directory was deleted
- PostgreSQL timestamp field serialization/deserialization handling
- Extended the `layer2_details` field length limit to 500 characters

## [0.5.4] - 2026-06-25

### Added
- PostgreSQL checkpoint persistence backend support

### Changed
- fault_spec unified as the single source of truth for fault injection intent
- Baseline capture adds policy-hit message dispatch; execution steps explicitly limited to the injection phase

### Fixed
- Fixed partial label-spec overrides and adjusted namespace defaults
- Baseline capture messages now end with a newline
- Standardized disk IO anomaly documentation with unified root path usage

## [0.5.3] - 2026-06-24

### Added
- safety_check introduces topology signals and node message dispatch during status checks
- Injection task status updates supported during the recovery flow (task_store)
- New Sidecar container fault typical use-case documentation

### Fixed
- Fixed result restoration when Layer 1 cached an in_progress state
- Improved error handling so the verifier checks fault states reliably

## [0.5.2] - 2026-06-23

### Added
- Unified `fault_type` field for fault type identification

### Fixed
- Corrupted config file detection with number-to-string type coercion
- Paths uniformly handled via `os.path.expanduser` for better cross-environment compatibility

## [0.5.1] - 2026-06-23

### Added
- Task snapshot restore and merge
- Recovery delay awareness prompts
- Intent graph checkpoint rollback on request cancellation
- `llm_start` event for improved thinking-duration metrics
- Unified AgentState lifecycle management

### Changed
- classify_intent replaced by the recover_task recovery flow
- Restructured operation result handling and session finalization; unified persistence of operation summaries
- Refactored intent clarification prompts; simplified loop detection prompts

### Fixed
- Fixed verification data isolation errors in recovery state
- Corrected step validation and text-exit handling in multi-step injection flows
- Fixed jsonl log reading during task snapshot restore
- Corrected the label_selector parameter for Service endpoints commands in baseline_capture
- Preflight timeout budget corrected to match server-side settings

## [0.5.0] - 2026-06-18

### Added
- End-to-end streaming for the recovery flow (UI and API both supported); session_store management for recovery tasks
- Cluster-wide tool Pod discovery with node-scoped improvements
- Task IDs now support the UUID format

### Changed
- agent_loop supports a no-LLM path; improved plan rejection reason extraction and display
- Optimized the LLM rejection flow with task state cleanup and batch fault reporting

### Fixed
- Fixed tui_session_id retrieval in the recovery flow
- Improved use-case matching and plan alternative handling; async initialization and task restore logic

## [0.4.9] - 2026-06-17

### Added
- Streaming token events for per-token frontend rendering
- Feasible alternatives displayed when a plan is rejected
- Structured output events for injection and recovery conclusions

### Fixed
- Fixed postmortem event emission and phase event handling
- Improved model thinking and phase event message handling

## [0.4.6] - 2026-06-16

Maintenance release: event handling refactor preparing for streaming token display.

## [0.4.5] - 2026-06-15

### Added
- Skill-type-based filtering for fault injection use-case sync
- Cross-namespace automatic discovery and cleanup of Debug Pods
- Explicit fault recovery and progress event forwarding
- Optimized chaos engineering capability definitions and baseline capture strategy

### Fixed
- Non-ChaosBlade faults continue to Layer 2 verification after Layer 1 failure
- Corrected success determination for baseline capture commands
- Tool Pod discovery supports multiple namespaces and labels
- Fixed intent graph logic and state management; improved tool-call event input/output display

## [0.4.2] - 2026-06-11

### Added
- SSE real-time streaming recovery endpoint with shared recovery session management
- Smart recommendation module to improve fault injection decisions
- Settings management improvements for multi-tenant context; extended K8s connection mode configuration

### Fixed
- Improved recovery judgment when blade destroy fails
- Fixed ChaosBlade Operator checks across mixed namespaces
- Fixed stream processing and timeout confirmation logic

### Changed
- Large-scale internal refactor: split giant functions such as turn.py (1,957 lines → 4 files) and _recover_verifier_with_llm (1,036 lines → 3 functions + a 92-line orchestrator); unified kubectl command building; removed deprecated compatibility layers (no external behavior change)

## [0.4.1] - 2026-06-08

### Added
- **Batch fault injection**, with node memory usage detection and real-time event streaming
- CLI auto-downloads the Node.js runtime to support the TypeScript TUI
- TUI event audit trail and display-store profiling; improved task list display with verification detail output

### Changed
- Injection graphs switched to the Pipeline graph architecture
- Optimized intent clarification and conversation-mode prompt structure; improved task summary building and message pruning

### Fixed
- Improved message compaction and tool-call message grouping
- Adjusted kubectl self-check timeout limits to avoid blocking startup
- Unified timeout parameter handling and tool-node error handling

## [0.3.0] - 2026-06-03

### Added
- `time_wait` intelligent waiting tool
- Blast radius scope declaration with safety score adjustment
- `kubectl apply` supports safely creating resources via stdin_data
- Extract and apply verified kubectl label selectors
- Optimized executor role definitions with enhanced injection monitoring and UID extraction
- CNI IP exhaustion injection script optimized with automatic replica count calculation

### Fixed
- Fixed target_guard classification and freezing logic; added persistentvolume to allowed manifest kinds
- Tool stagnation detection now distinguishes subcommand levels
- Fixed injection start time not being set correctly
- Improved label selector formatting and variable mapping display; kubectl query and label sync logic
- Fixed kubectl stdin parameter passing and consecutive tool-call blocking
- Adjusted empty-blade_uid handling; improved injection method detection and namespace resolution

## [0.2.2] - 2026-06-02

Maintenance release: uv dependency added to build commands, kubectl change policy and target health detection fixes, release process documentation.

## [0.2.1] - 2026-06-02

Major hardening release for the safety system, observability, and TUI experience (152 commits).

### Added — Safety & planning guards
- **Multi-dimensional safety scoring system with UI display** (blast radius / frequency / time / topology)
- Injection feasibility assessment with safety check reports; improved loop detection
- Target guard and intent confirmation guarding with message management
- Phase 1 read-only safety enforcement (kubectl_ro)
- Removed the blade_create tool from the planning phase to close a security hole
- Plan rejection mechanism and termination detection; directory browsing detection with planning rejection protection
- owner_scope cross-level validation to permit legitimate owner operations
- Conflict detection triggering the confirmation gate
- Tool error interface mismatch detection with runtime verification hints

### Added — Verification & recovery
- **Cross-session automatic experiment recovery**
- Verifier supports multi-candidate skill case verification options; multiple matching use-case paths offered for selection
- Self-destructing fault detection and skip mechanism
- Disk feasibility checker (disk usage assessment)
- kubectl_native injection step completeness check and guidance
- Plan change confirmation during replanning
- Automatic completion of key parameters for burn operations
- Feasibility checks strengthened by resolving real Pod names from labels
- Pod network fault feasibility checker; improved iptables availability checks and blade command classification
- Recovery verification Scheme B finalize step

### Added — Observability & platform integration
- **MCP client integration** for enhanced model context tooling
- Automatic postmortem generation and display
- Generic file watcher with hot reload for skills and knowledge
- Prometheus metrics export with multi-model context budgets
- OpenTelemetry GenAI export and task tracing
- blade-ai L4 adapter and related modules
- Server-side SSE event batching

### Added — TUI experience
- plan_builder guided planning node with TUI components
- Expanded slash commands: `/session` (with permission-mode subcommands), `/mode`, new `/help` card, `/tasks`, `/config`, `/memory`, `/compact`
- Model selection API and commands with model listing and dynamic hot-switching
- Skill management subcommands
- context_size events with context window display; manual session compaction indicator with interrupt support
- SSE streaming endpoint for forced context compaction
- Terminal background color detection and adaptation
- Confirmation message components redesigned (Forge × Operator design language), showing fault classification, complexity markers, agent rationale, and target-change confirmation content
- Fault experiment catalog cards with backend management
- Embedded configuration wizard with wizard-flow state management
- Boot flow management with progress display, async self-check loading, and PendingTasksCard
- Two visual forms for tool messages; visible-line budgets for agent messages
- Command parsing and registration refactor with subcommand support

### Added — Installation & packaging
- PyInstaller packaging optimized with multi-platform support
- Cross-platform blade-ai install scripts and uninstall commands (including a Windows uninstall script)
- ChaosBlade auto-download on first use with multi-platform support
- Fault capability sync and listing (case_sync)
- New Pod fault scenario chaos experiment use cases (including service dependency disruption via Pod network loss)

### Changed
- Removed the container-restart fast path in favor of the snapshot-diff detection framework
- FaultSpec used centrally instead of scattered field access
- Token estimation replaced by model-aware counting
- Unified fail_state and structured failure reason support
- Baseline metric extraction simplified to shared module calls
- Phase round limits moved into settings for unified management

### Fixed
- Server-side HTML injection protection
- Fixed Pod status parsing for complex RESTARTS field formats
- Improved network interface existence checks and exception handling
- Fixed the finish_planning tool message contract; planning termination signals and action stagnation detection
- Prevented duplicate token streaming from the save_memory node
- Fixed state blockage caused by stuck running tools
- Fixed embedded server startup in PyInstaller mode; fixed version command process hangs
- Fixed /compact endpoint thread ID parsing
- Baseline capture LLM policy timeout handling and configuration

## [0.1.0] - 2026-05-18

First release (331 commits; the complete outcome of the 2026-04-20 to 2026-05-18 development cycle).

### Added — Core execution engine
- Chaos Agent Server core application framework
- Three-graph state machine execution engine: Intent Graph (intent clarification), Pipeline Graph (injection pipeline), Recover Graph (recovery verification)
- agent_loop with LLM reasoning and two-phase ReAct loops; LLM thinking mode support (preserving reasoning_content)
- Two-layer verification (L1 deterministic + L2 LLM semantic) and two-layer recovery verification
- Replan support with lifecycle handling; complex task plan persistence
- Failure reason classification with LLM diagnostic extraction

### Added — Fault injection capabilities
- ChaosBlade integration: blade_create / blade_destroy / blade_status / blade_query_k8s tools with automatic blade_uid extraction and recording
- Dual-mode injection: natural language mode + structured inject parameter system (scope/target/action/params) + direct fast path
- Dry-Run preview and landing execution
- kubectl-native injection detection with alternative recipe support
- Unified kubectl toolset: generic kubectl subcommand execution, kubectl_get / kubectl_set / kubectl_patch / kubectl_delete
- Original replica count tracking for kubectl scale operations (for recovery)
- Automatic rollback on injection failure, destroying orphaned blade experiments
- kubeconfig and context parameter support; automatic kubeconfig injection
- Pre-injection baseline capture, post-disk-fill validation, post-injection verification for node-disk-burn, automatic parameter augmentation for pod-disk-burn
- Automatic injection timeout extension to guarantee experiment duration (default 600s); TUI interactive mode with timeout auto-recovery
- Chat mode for non-injection conversations; web_search tool for extended queries
- Safe local file read, write, and search tools

### Added — Safety mechanisms
- Pre-injection conflict checks (including target resource overlap analysis); conflict checks filter destroyed and cancelled experiments
- Duplicate tool-call detection with loop interruption; consecutive idle-round detection to prevent LLM loop stalls
- Iteration convergence hints with tuned default loop limits
- Safety checks with feasibility reporting
- Safe parameter computation with enhanced skill tool descriptions
- FCAT (Fault Context Adaptation Table) rule integration with multi-level adaptation and safety hardening

### Added — Conversational TUI
- React Ink-based TUI framework (later refactored to an async PromptSession architecture)
- First-run configuration wizard (single-panel step-based UI) with config validation commands
- Custom slash command menu and status bar rendering; command help panel and input interactions
- Welcome card, farewell panel with session statistics; ThinkingPrinter streaming render of thinking states
- `/expand` command to show full tool-call output
- Unified theme colors and refined palette
- Real-time streaming output for the inject command

### Added — Memory & sessions
- Claude Code-aligned hierarchical memory system with environment information injection
- JSONL-based session storage with incremental snapshots; SessionStore refactor
- PreReasoningHook for memory compaction and context management
- Automatic task experience append to AGENT.md for self-evolution
- Session persistence: command execution info, system prompts, Layer 1 results, compaction events

### Added — Persistence & observability
- SQLite-unified TaskStore (merged task state and metrics); async persistence backend migration
- JSONL persistence of trace metrics with cross-process queries
- Multi-node execution state tracking; task state inference with lifecycle timestamp management
- LLM call tracing; debug-mode LLM response summary logging

### Added — Skills & knowledge
- Skill catalog parsing with dynamic fault use-case caching (directory fingerprint based)
- LLM-based skill injection use-case generation and caching; cache bypass when listing failure cases
- K8s chaos skill use cases: Pod network DNS faults (resolution anomalies), network loss, storage class misconfiguration, node and resource saturation scenarios, and more
- Section-based knowledge document loading with index display
- Chaos engineering principles and verification strategy knowledge docs

### Added — CLI & engineering
- Natural language fault injection CLI; status / list / config command suite
- Unified configuration management (config management replacing mode management)
- Default skills directory moved to `~/.chaos-agent/skills` with resolution priority and config file support
- Unified API response format via the JSONEnvelope wrapper
- blade binary path resolution with packaging integration
- Project renamed from chaos-agent to Blade AI

### Changed
- PromptMode-driven unified system prompt assembly; execution instructions and verification sub-modularized
- Timestamps unified to Beijing-timezone ISO format (now_iso / parse_iso_timestamp)
- API key config `dashscope_api_key` renamed to `llm_api_key`
- kubectl tools merged (kubectl_get_pods/nodes → generic kubectl_get)
- Token estimation supports CJK characters

### Fixed
- Fixed injection failures being falsely reported as success
- Fixed recovery verification routing for Kubernetes exec injections
- Fixed blade status command usage; Blade command parameter handling under node scope
- Fixed AgentState message persistence to avoid duplicate injection messages
- Fixed synthetic message state persistence across multiple iterations
- Fixed kubectl exec blade path parameter write-back and metadata updates
- Fixed flow control across multi-layer verification phases
- Fixed task_store initial state writes and state inference on reads
- Fixed database connection exception handling to avoid resource leaks
- Backward compatibility with legacy blade_create commands
