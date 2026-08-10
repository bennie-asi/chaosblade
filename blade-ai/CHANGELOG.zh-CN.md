# 更新日志

[English](CHANGELOG.md) · 简体中文

本项目所有重要变更均记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.6.5] - 2026-08-04

### Changed
- 工具 schema 上下文瘦身：22 个工具的 schema 从 12696 token 降至 9354 token（-26%），降低每轮对话 token 消耗
- 优化调试 Pod 识别与注入载体（carrier）管理逻辑

### Added
- 技能库补充应用主进程异常故障模式及注入持续模式方案
- 文档补充 Transport Channel 连接方式说明

## [0.6.4] - 2026-08-03

### Added
- 新增 Pod 网络带宽受限故障用例；补充容器异常、磁盘填满的 kubectl 降级方案

### Fixed
- LLM 增加重试历史记忆，防止重复发送已失败的命令
- 修复 `blade-ai update` 版本目录扫描时并发删除导致的异常
- 修复 TaskStore 关闭流程中的事件循环关闭错误
- shell 执行避免向特殊进程组发送杀死信号

### Changed
- 验证器将实验设置阶段识别为 warning 状态，减少误报
- 改进技能目录指纹计算逻辑，提高用例同步准确性

## [0.6.3] - 2026-08-03

纯发布打包版本，无功能变更。

## [0.6.2] - 2026-08-03

### Added
- 进度账本（progress ledger）：全程跟踪执行状态，截取策略保护关键里程碑
- 主机只读探针命令及参数级变更判定
- 辅助 LLM 调用审核记录（session store），固定会话记录中的模型名称字段
- 支持 Pod 级 ephemeral debug 容器
- 卸载支持按版本卸载，默认保留配置目录
- 新增 qwen3.7-max / qwen3.7-plus 模型窗口配置；更新模型窗口大小与压缩预算
- 上下文压缩逻辑优化，新增停滞频次上限配置

### Changed
- **全面国际化**：内置提示词、日志消息、架构图、配置提示由中文替换为英文；中英文 README 完整重构，安全层图示更新
- 执行载体（carrier）拒绝原因详尽报告：每个拒绝都有明确拒因与修复建议
- 故障注入确认步骤合并，提交流程简化；意图澄清拒绝处理统一，拒因可见性提升
- 细化 kubectl 子命令及参数校验，支持中文动作词映射
- 工具可见性基于单一 profile 的筛选逻辑
- 上下文令牌计数改用提供者用量锚定；避免固定消息数量截断，确保完整历史传递
- 提示升级控制改用 `hint_escalate_after` 替代 `stagnation_frequency_ceiling`
- 预检（preflight）诊断信息改为英文输出

### Fixed
- 加固 `blade-ai update` / `uninstall` 对真实失败模式的处理；`update --check` 支持任意安装类型
- 修正只读 host-escape 探针被误判拒绝的问题
- 修正空 AI 回复被误判为对话结束的问题
- 纠正执行循环终止流程：执行完毕必须经过验证
- 修复 `kubectl --copy-to` 创建的副本 Pod 路由处理问题
- 修复意图澄清拒绝原因未正确持久化的问题；节点消息保证持久化到 TUI 会话文件
- 修正故障二进制分类以支持 Pod 级操作
- 处理输出截断的工具调用，避免执行错误；为 HumanMessage 添加显式唯一标识符
- 修复读写设备检测和信号处理逻辑；明确缺失参数以改进未知置信度反馈
- 修正环路检测阈值；从意图工具列表中过滤后端 CLI 能力探针
- L4 按运行时通道路由混沌技能选择
- 更新 ChaosBlade 安装包校验和

## [0.6.1] - 2026-07-28

### Added
- **Python 应用层故障演练支持**：新增 python_agent 与 python-app-chaos-skills 技能库，演练范围从 K8s/主机扩展到 Python 应用进程
- 新增 `blade-ai serve` 命令，启动 HTTP API 服务器
- 跨故障族作用域变更防护（批准网络故障但执行进程故障会被拦截）
- 新增 `injection_start_time` 字段，准确判定注入命令是否已发出
- 注入步骤语义检测优化与 kubectl patch 识别

### Changed
- 基于环境能力（capability context）统一工具调用权限校验与筛选
- CLI 输出格式统一为 OutputFormat 枚举处理
- 统一任务 ID 获取逻辑，优化任务身份识别；状态追踪器支持事件通道 ID 及历史事件长度限制

### Fixed
- 修正活跃任务查询判据，支持 fault_spec 规范形态
- 修正 Python 应用注入工具的通道能力门控逻辑

## [0.6.0] - 2026-07-27

### Added
- **主机故障演练技能库（host-chaos-skills）首发**：主机故障演练与混沌工程技能文档及命令参考
- **环境能力上下文（capability context）引入**并应用于各执行节点
- 新增节点级网络故障实验用例；可用区网络分区注入策略规范与强化（含节点并发创建流程）
- ChaosBlade 替代原生命令的降级方案文档；kubectl debug 模拟故障注入操作说明
- 基线命令数据层及执行层实现；多目标基线命令扩展；基线采集支持多配置文件与主机命令
- 未注册载体（unregistered carrier）动态发现；调试 Pod 结构化跟踪和清理机制
- 新增 `request_replan` 工具；结构化重规划请求（计划无效 / 需调查两种形态）
- Host profile 确定性主机证据补充逻辑，主机通道验证不再依赖 K8s 特定提示
- ResilientChatOpenAI：LLM 传输失败自动重试
- 能力探测 debug pod 自动清理（含并发删除提速）
- wiz 命令 `--wait-timeout` 参数动态配置；wiz 任务超时时间独立配置
- 自动批准事件流，提升流式确认体验；配置展示完整内容（含敏感字段）
- 注入工具调用 task_id 绑定与多目标覆盖检测
- kubectl 及 blade_create 命令目标分类功能（新分类器）
- kubectl 相关 chaos 注入命令易用性与自恢复能力增强

### Changed
- **环境无关化重构**：提示词统一移除 profile 参数，Agent 从 K8s 专用走向通用执行引擎
- 传输层统一：本地 kubectl 调用替换为统一传输执行；移除宿主机本地注入工具支持
- 移除旧版注入图，全面切换新 Pipeline 图；删除旧分类器模块
- 多步注入操作引入软自检机制；快照范围判断改用 `can_capture` 替代静态 `requires_namespace`
- 计划变更相关接口及路由状态管理优化；nodes 子包结构重构

### Fixed
- 修复取消任务时事件循环挂起的问题；工具调用事件唯一标识问题
- 修复 kubewiz 模式下命令解析的容器命令穿透问题
- 修复节点和可用区网络隔离方案，增强系统定时恢复（systemd-run）机制
- 修复调试配置文件传递和只读探测判定逻辑
- 基线采集失败命令错误信息与重试优化；扩大 stderr 预览长度
- 修复批量意图自动确认及状态重置问题；TUI 流式响应滚动条重影问题
- 防止 Markdown 尾部换行符影响安全分割点计算

## [0.5.8] - 2026-07-06

### Added
- 多租户支持：新增 `tenant_id` 上下文传递，删除过时实验存储模块
- 恢复流程事件流处理与阶段步骤追踪
- SSE 心跳维护机制，防止长连接意外断开

### Fixed
- 避免 Linux fork 引起的线程安全问题
- 禁用后端 SSE 批处理以减少流式延迟
- kubectl 和 wiz 路径改用绝对路径解析，避免 fork 崩溃

### Changed
- 经验文件由 AGENT.md 更名为 EXPERIENCE.md；TaskStore 初始化和切换逻辑优化

## [0.5.7] - 2026-06-30

### Added
- 验证失败后的重新规划（replan）机制

### Fixed
- 修正验证状态判定逻辑
- 修复 blade 路径解析错误，硬编码路径替换为统一解析方法
- 无技能激活的情况改为可恢复并重试；修正技能激活调用规则说明
- 修复日志目录被删除后无法写入日志的问题
- PostgreSQL 时间戳字段序列化/反序列化处理
- `layer2_details` 字段长度限制扩展至 500 字符

## [0.5.4] - 2026-06-25

### Added
- 支持 PostgreSQL checkpoint 持久化后端

### Changed
- fault_spec 统一为故障注入意图的单一数据源
- 基线采集添加策略命中消息分发；明确执行步骤仅限注入阶段

### Fixed
- 修复标签规格部分覆盖问题并调整命名空间默认值
- 基线采集消息补齐行尾换行符
- 磁盘 IO 异常文档规范化并统一根路径使用

## [0.5.3] - 2026-06-24

### Added
- safety_check 引入拓扑信号与状态检查中的节点消息分发
- 恢复流程中支持注入任务状态更新（task_store）
- 新增 Sidecar 容器故障各类典型用例文档

### Fixed
- 修复 Layer 1 缓存 in_progress 状态下的结果恢复问题
- 改进错误处理逻辑，确保验证器检查故障状态

## [0.5.2] - 2026-06-23

### Added
- 统一 `fault_type` 字段用于故障类型标识

### Fixed
- 配置文件损坏检测，兼容数字到字符串的类型转换
- 路径统一使用 `os.path.expanduser` 处理，增强跨环境兼容性

## [0.5.1] - 2026-06-23

### Added
- 任务快照恢复与合并功能
- 恢复延迟意识提示（recovery delay awareness）
- 取消请求时回滚意图图检查点
- `llm_start` 事件以改进思考时长统计
- AgentState 生命周期统一管理

### Changed
- classify_intent 替换为 recover_task 恢复流程
- 操作结果处理与会话终结重构；操作概要生成逻辑统一持久化
- 意图澄清提示词模块重构；循环检测提示简化

### Fixed
- 修复恢复状态中验证数据隔离错误
- 修正多步注入流程的步骤校验和文本退出处理
- 修复任务快照恢复时读取 jsonl 日志的问题
- 修正 baseline_capture 中 Service endpoints 命令的 label_selector 参数
- 预检超时预算修正以匹配服务器端设置

## [0.5.0] - 2026-06-18

### Added
- 恢复流程全链路流式化（UI 与 API 同步支持）；恢复任务 session_store 管理
- cluster-wide 工具 Pod 发现与 node-scoped 改进
- 任务 ID 支持 UUID 格式

### Changed
- agent_loop 支持无 LLM 路径；规划拒绝原因提取与展示优化
- LLM 拒绝流程优化，添加任务状态清理及批量故障报告支持

### Fixed
- 修复恢复流程中 tui_session_id 的获取逻辑
- 优化用例匹配与规划替代方案处理；异步初始化和任务恢复逻辑

## [0.4.9] - 2026-06-17

### Added
- 流式 token 事件，前端支持逐 token 渲染
- 规划被拒绝时展示可行的替代方案
- 注入与恢复结论的结构化输出事件

### Fixed
- 修复 postmortem 事件发射及阶段事件处理
- 优化模型思考与阶段事件消息处理逻辑

## [0.4.6] - 2026-06-16

维护版本：事件处理逻辑重构，为流式 token 展示做准备。

## [0.4.5] - 2026-06-15

### Added
- 基于技能类型的故障注入用例同步过滤
- Debug Pod 跨命名空间自动发现与清理
- 显式故障恢复及进度事件转发
- 混沌工程能力定义与基线采集策略优化

### Fixed
- 非 ChaosBlade 故障 Layer 1 失败后继续执行 Layer 2 验证
- 修正基线采集命令的成功判定逻辑
- 工具 Pod 发现支持多命名空间与标签
- 修正意图图相关逻辑和状态管理问题；工具调用事件输入输出展示优化

## [0.4.2] - 2026-06-11

### Added
- SSE 实时流式恢复端点，恢复会话共享管理
- 智能推荐模块，提升故障注入决策能力
- Settings 管理改进以支持多租户上下文；K8s 连接模式配置扩展

### Fixed
- 优化 blade destroy 失败时的恢复判断逻辑
- 修复混合命名空间的 ChaosBlade Operator 检查逻辑
- 修复流处理和超时确认逻辑

### Changed
- 大规模内部重构：拆分 turn.py（1957 行→4 文件）、_recover_verifier_with_llm（1036 行→3 函数+编排器）等巨型函数；统一 kubectl 命令构建；删除废弃兼容层（不影响对外行为）

## [0.4.1] - 2026-06-08

### Added
- **批量故障注入支持**，含节点内存使用率检测与实时事件推送
- CLI 自动下载 Node.js 运行时以支持 TypeScript TUI
- TUI 事件审计轨迹与显示存储侧写；任务列表显示优化与验证详情输出

### Changed
- 注入相关图切换为 Pipeline 图架构
- 意图澄清与对话模式提示结构优化；任务摘要构建及消息修剪逻辑优化

### Fixed
- 改进消息压缩逻辑与工具调用消息分组
- 调整 kubectl 自检超时限制，避免阻塞启动
- 统一超时参数处理及工具节点错误处理

## [0.3.0] - 2026-06-03

### Added
- `time_wait` 智能等待工具
- 爆炸半径（blast radius）范围声明及安全评分调整
- `kubectl apply` 支持通过 stdin_data 安全创建资源
- 提取并应用经过验证的 kubectl 标签选择器
- 执行角色定义优化，增强注入过程监控与 UID 提取逻辑
- CNI IP 耗尽注入脚本优化，支持自动计算副本数

### Fixed
- target_guard 分类与冻结逻辑修复；persistentvolume 加入允许的清单类型
- 工具停滞检测支持子命令层级区分
- 解决注入开始时间未正确设置的问题
- 优化标签选择器格式及变量映射显示；kubectl 查询和标签同步逻辑
- 修复 kubectl 工具调用 stdin 参数传递及阻止工具连续调用逻辑
- 调整 blade_uid 为空时的处理逻辑；注入方法检测与命名空间解析优化

## [0.2.2] - 2026-06-02

维护版本：构建命令补充 uv 依赖、kubectl 变更策略与目标健康检测逻辑修正、发布规范文档。

## [0.2.1] - 2026-06-02

本版本为安全体系、可观测性与 TUI 体验的大规模强化版本（152 commits）。

### Added — 安全与规划守卫
- **多维安全评分体系及界面展示**（爆炸半径/频率/时间/拓扑四维评分）
- 注入可行性评估功能与安全检查报告；循环检测优化
- 目标防护（target guard）和意图确认的守护与消息管理
- Phase 1 只读安全强制（kubectl_ro）
- 移除规划阶段中的 blade_create 工具以防止安全漏洞
- 计划拒绝机制和终止检测；目录浏览检测及拒绝规划保护
- owner_scope 跨级校验以允许合法的所有者操作
- 冲突检测触发确认门控
- 工具错误接口不匹配检测与运行时验证提示

### Added — 验证与恢复
- **跨会话的实验自动恢复功能**
- 验证器支持多候选技能案例的验证选项；多匹配用例路径供选择
- 自毁故障检测与跳过机制
- 磁盘可行性检查器（评估磁盘使用情况）
- kubectl_native 注入步骤完整性检查与引导
- 重新规划时的计划变更确认机制
- 燃烧（burn）操作关键参数自动补全
- 通过标签解析真实 Pod 名称增强可行性检查
- Pod 网络故障可行性检查器；iptables 可用性检查与 blade 命令分类改进
- 恢复验证 Scheme B finalize 步骤

### Added — 可观测性与平台集成
- **MCP 客户端集成**，增强模型上下文工具支持
- 任务事后分析（postmortem）自动生成与展示
- 通用文件监控器，支持技能与知识热重载
- Prometheus 指标导出与多模型上下文预算
- OpenTelemetry GenAI 导出与任务追踪
- blade-ai L4 适配器及相关功能模块
- 服务器端 SSE 事件批处理

### Added — TUI 体验
- plan_builder 方案引导节点与 TUI 组件
- 斜杠命令体系扩展：`/session`（含权限模式子命令）、`/mode`、`/help` 全新卡片、`/tasks`、`/config`、`/memory`、`/compact`
- 模型选择 API 及命令，支持模型列表与动态热切换
- 技能管理子命令
- context_size 事件及上下文窗口显示；手动会话压缩指示器及中断支持
- 会话上下文强制压缩的 SSE 流式接口
- 终端背景色检测及适配
- 确认消息组件重构（Forge × Operator 设计风格），显示故障分类和复杂度标记、Agent 理由及目标变更确认内容
- 故障实验目录卡片及后端管理
- 内嵌配置向导与向导流程状态管理
- 启动流程管理和进度显示组件、异步加载自检结果、PendingTasksCard
- 工具消息两种视觉形式；Agent 消息可见行数预算
- 命令解析与注册体系重构，新增子命令支持

### Added — 安装与打包
- PyInstaller 打包配置优化并添加多平台支持
- blade-ai 跨平台安装脚本与卸载命令（含 Windows 卸载脚本）
- ChaosBlade 首次使用自动下载及多平台支持
- 故障能力同步与列表展示功能（case_sync）
- 新增多种 Pod 故障场景混沌实验用例文档（含 Pod 网络丢包服务依赖中断）

### Changed
- 容器重启快速路径逻辑移除，改用快照差异检测框架
- FaultSpec 集中使用，替代散落字段访问
- token 估算替换为模型感知计数模块
- 统一 fail_state 和结构化失败原因支持
- 基线指标提取简化为共享模块调用
- 环节轮数配置移入 settings 统一管理

### Fixed
- 服务端防范 HTML 注入风险
- 修正 Pod 状态解析对复杂 RESTARTS 字段格式的处理
- 网络接口存在性检查逻辑与异常处理完善
- finish_planning 工具消息合约修复；规划终结信号和动作停滞检测
- 阻止 save_memory 节点重复流式输出 token 内容
- 修复卡住的运行中工具导致的状态阻塞
- 修复 PyInstaller 模式下内嵌服务器启动问题；version 命令进程挂起问题
- 修复 /compact 接口线程 ID 解析逻辑
- 基线采集 LLM 策略超时处理及配置项

## [0.1.0] - 2026-05-18

首次发布（331 commits，2026-04-20 至 2026-05-18 开发周期的完整成果）。

### Added — 核心执行引擎
- Chaos Agent Server 核心应用框架
- 三图状态机执行引擎：Intent Graph（意图澄清）、Pipeline Graph（注入流水线）、Recover Graph（恢复验证）
- agent_loop 集成 LLM 推理与两阶段 ReAct 循环；LLM 思考模式支持（保留 reasoning_content）
- 双层验证（L1 确定性 + L2 LLM 语义）与两层恢复验证
- 重规划支持及生命周期处理；复杂任务计划保存
- 失败原因分类支持与 LLM 诊断提取

### Added — 故障注入能力
- ChaosBlade 集成：blade_create / blade_destroy / blade_status / blade_query_k8s 工具，blade_uid 自动提取与记录
- 双模式注入：自然语言模式 + 结构化 inject 参数体系（scope/target/action/params）+ direct 快速通道
- Dry-Run 模式预览和落地执行
- kubectl-native 注入检测及替代方案支持
- kubectl 统一工具集：通用 kubectl 子命令执行、kubectl_get / kubectl_set / kubectl_patch / kubectl_delete
- kubectl scale 操作原始副本数追踪（用于恢复）
- 注入失败自动回滚，销毁遗留的 blade 实验
- kubeconfig 及上下文参数支持；Kubeconfig 自动注入
- 预注入基线采集、磁盘填充后效验、node-disk-burn 注入后验证、pod-disk-burn 参数自动增强
- 注入超时时间自动提升保证实验时长（默认 600 秒）；TUI 交互模式和超时自动恢复机制
- 非故障注入的聊天模式；web_search 工具扩展查询能力
- 安全的本地文件读取、文件写入和搜索工具

### Added — 安全机制
- 预注入冲突检查（含目标资源重叠分析）；冲突检查过滤已销毁和撤销的实验
- 重复工具调用检测与循环打断机制；连续空闲轮次检测防止 LLM 循环卡死
- 迭代次数收敛提示与默认循环上限调整
- 安全检查与可行性报告支持
- 参数安全计算与技能工具说明增强
- FCAT（故障上下文适应表）规则集成，多级适配与安全增强

### Added — 对话式 TUI
- 基于 React Ink 的 TUI 框架（后重构为异步 PromptSession 架构）
- 首次启动配置向导（单面板步骤式界面）与配置校验命令
- 自定义斜杠命令菜单和状态栏渲染；命令帮助面板和输入框交互
- 欢迎卡片、告别面板与会话统计；ThinkingPrinter 思考状态流水式渲染
- `/expand` 命令支持工具调用完整输出展开
- 主题颜色统一与配色方案优化
- inject 命令实时流式输出

### Added — 记忆与会话
- Claude Code 对齐的分层记忆系统与环境信息注入
- 基于 JSONL 的会话存储和增量快照机制；SessionStore 重构
- PreReasoningHook 支持内存压缩和上下文管理
- 任务经验自动追加至 AGENT.md 支持自我进化
- 会话持久化：命令执行信息、系统提示信息、层 1 结果、压缩事件

### Added — 持久化与可观测性
- SQLite 统一持久化 TaskStore（合并任务状态与指标）；异步持久化后端迁移
- trace 指标的 JSONL 持久化与跨进程查询
- 多节点执行环节状态跟踪；任务状态推断与生命周期时间戳管理
- LLM 调用追踪能力；调试模式 LLM 响应摘要日志

### Added — 技能与知识
- 技能目录解析与故障用例动态缓存（基于目录指纹）
- 基于 LLM 的技能注入用例生成与缓存机制；列出失败案例时绕过缓存
- K8s 混沌技能用例：Pod 网络 DNS 故障（域名解析异常）、网络丢包、存储类配置错误、节点与资源饱和场景等
- 知识文档按节加载与索引展示
- 混沌工程原理与验证策略知识文档

### Added — CLI 与工程化
- 自然语言模式故障注入 CLI；status / list / config 命令体系
- 统一配置管理（模式管理替换为 config 管理）
- skills 目录默认路径迁移至 `~/.chaos-agent/skills`，解析优先级与配置文件支持
- API 响应格式统一为 JSONEnvelope 封装
- blade 二进制路径解析与打包集成
- 项目命名从 chaos-agent 统一切换为 Blade AI

### Changed
- PromptMode 驱动系统提示构建逻辑统一；执行指令和验证子模块化
- 时间戳统一使用北京时区 ISO 格式（now_iso / parse_iso_timestamp）
- API 密钥配置 `dashscope_api_key` 重命名为 `llm_api_key`
- kubectl 工具合并统一（kubectl_get_pods/nodes → 通用 kubectl_get）
- 令牌估算支持中日韩文字

### Fixed
- 修复故障注入失败误报成功的问题
- 修复 Kubernetes exec 注入的恢复验证路由逻辑
- 修复 blade status 命令使用问题；节点作用域下 Blade 命令参数处理
- 修复 AgentState 消息持久化问题，避免注入消息重复
- 修复合成消息在多次迭代中的状态持久化问题
- 修复 kubectl exec blade 路径参数回写及元数据更新
- 修复多层验证阶段的流程控制问题
- 修复 task_store 初始状态写入与读取时状态推断逻辑错误
- 修复数据库连接异常处理，避免资源泄露
- 兼容旧版本 blade_create 命令
