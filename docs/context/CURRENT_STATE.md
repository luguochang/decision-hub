# 当前状态短上下文

更新时间：2026-09-01（Asia/Shanghai）
用途：Agent 恢复任务时的第一读入口。本文只投影已验证事实，不替代 ADR、Stage Charter、schema 或实现。

## 当前状态：E2-L 已通过，进入 E3 观察

- 唯一执行事实源是 [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，最终证据是 [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。旧 Pilot Ready v2、E2L-01/02/03 和 C1-C7 任务书只保留历史证据，不能重新成为执行入口。
- 正式 Run `run_04dc1a46fd1e4c3b988750e18b0e9581` 从官方 DSH Web 建立，在同一受管 Session 完成 2 轮、12/12 durable Tool Call、15 条 Evidence、66.7% hard coverage，按代码 Gate 收敛为 `research_only / tool_budget`；DSH 与 Decision Desk 终态一致。
- 后台 scheduler/worker 自动创建 child Run `run_7df306876d114e09b8e83507d06f3ef0` 并再次完成 2 轮、12/12 Tool Call，证明持续复查不依赖 owner 再次输入。
- 两次 `web.search` timeout、FRED stale 和 unknown cost 被如实保留；不发布方向性交易结论，不把失败改写为成功或 `no_trade`。
- 当前产品状态：`pilot_ready=true / pilot_usable=research_only / automatic_trading=false / Fixed active / DSH candidate-shadow`。这只允许开始至少 14 天或 20 个高影响事件的 E3 前瞻观察，不等于预测、盈利或 Promotion 已证明。
- 最新完整质量门：Python `390 passed`、DSH Plugin `53 passed + build`、Decision Desk `10 passed + build`、Ruff、Pyright、codegen、module docs、Compose、fresh migration、recovery、三类 replay、callback/Web restart 和 rollback 均通过；桌面/窄屏截图与 SHA-256 已归档。

## 2026-09-01 E2L-03 历史收口复核

- Durable capability gateway 已在返回 DSH 前写入 `tool_started`、`tool_completed`/`tool_failed`，成功 Evidence 即时入账；取消保持为控制流，不包装成 Provider failure。
- DSH Host 的模型步 watchdog 使用官方 `session/event` 与 `sessionController.cancel()` seam；terminal callback 以 `run + generation + terminal_status` 互斥，事件/status/result 并发只发送一次，失败后可重放。
- Query/View 已修复“最终 Result 尚未生成时看不到进度”的缺口：从规范化 Trace 投影工具调用数、轮次和逐 capability ErrorProvenance；从 Hub Evidence 表投影已保留证据；失败/取消在无 Result 时显示保守 coverage/stop reason，不伪装为成功或空结果。
- Decision Desk 的 ToolActivity 已覆盖 `error_code`、`origin`、`cause_code` 和 retryability；对应部分成功/部分失败 fixture 已加入前端测试。
- `dsh_session_links.deadline_at` 已通过 migration `0026_dsh_session_deadline` 持久化；live Gateway 读取 durable deadline，effective cutoff 不得超过它，freshness 不再以未来 Run deadline 为锚点。历史 link 缺失 deadline 时 fail-closed，不猜测。
- DSH Web synthesis 的 attestation/structured-output 失败现在可返回 evidence-only `degraded` 结果：可信 Evidence、Tool Trace、coverage 保留，causal case/horizons 全部丢弃，记录 `synthesis_failure_code` 和 `synthesis_attestation` provenance；不发 repair turn、不消耗下一 generation。无可信 Evidence 仍直接失败。
- LangGraph 在 coverage 足够但 synthesis 无效时仍保持 `degraded/research_only`，不会把 Evidence-only 进度误标为 `completed` 或发布方向性 Forecast。
- 当前专项验证：E2L 相关 Python 联合测试 `72 passed`；全量离线 Python `373 passed`、DSH plugin `43 passed`、Decision Desk `10 passed`，Ruff/Pyright/codegen/module docs/frontend build/Compose config/diff check/core acceptance 均通过。官方 DSH replay success/partial/insufficient、跨进程恢复、callback gap、Web restart 和锁定版本回滚均通过。对真实失败 Run 的只读数据库副本复验为 `dsh / Round 1 / 10 tools / 47 Evidence / critical_data_unavailable`，剩余 hard gaps 为 `expectation_pricing`、`macro_transmission`，并保持 `dsh_evidence_unattested / synthesis_attestation`、无 causal/horizon/decision snapshot。最终 live 验收仍 pending。
- 当前阶段为 `E2L-03 offline quality gates passed; E2L-E live in-session acceptance pending`。真实 Web 已保留配置错误、模型步 timeout 和主动补证失败样本；最小 Official/Market 独立 canary 已通过，但 Search 历史 timeout、新正式 DSH Session 的 Evidence/Gate 结果、当前 revision 的长期截图/hash 与 console 归档仍不能用 adapter canary 或 replay 代替；`pilot_ready=false`、`pilot_usable=false`。
- Owner 已接受最小只读 Official/Market allowlist；独立 canary 的 official、cross-asset、crypto spot/derivatives 四例均通过且成本为 0。新隔离实例已加载 `web.search,official.macro,market.cross_asset,market.crypto_derivatives`；其中 FRED Evidence 约 4.42 天旧，正式 Gate 必须标 stale。Client Plugin 已通过官方 `conversation.view` 增加独立“研究报告”页签，完整报告不再占用 composer dock；固定上游无第三方 default-view seam，首次仍进入 Chat，不用 hack 覆盖。

## 2026-09-01 前端运行态历史根因复验

- 旧 `8010` API 返回 `research-run-view.v1`，当前前端严格要求 `v2`，因此页面显示“研究队列不可用”；这不是无数据，而是历史进程/契约不兼容。旧进程和历史数据未修改。
- 当前源码已在全新 `8970` 端口和临时数据库完成浏览器提交 -> durable worker -> 两轮 replay -> Evidence/Sufficiency/Gate/Trace 复验；结果为 `research_only`、hard coverage `16.7%`、`critical_data_unavailable`，证明离线 fail-closed 链路，不证明实时检索。
- 修复 replay fixture 的计数/轨迹矛盾：每轮现在有 canonical invocation/result，`total_tool_calls` 与 Round 明细一致；相关测试 `11 passed`，375px 无横向溢出。
- 详细记录见 [前端运行态验收与根因修复](../evaluations/FRONTEND_RUNTIME_ROOT_CAUSE_2026-09-01.md)。下一目标仍是 E2-L/G2-LIVE-01 的真实只读 capability 闭环；`pilot_ready=false`、`pilot_usable=false` 不变。

## 事实

- R0/R1/R2/R2-L 的离线/本机工程退出门已完成；当前工作树包含尚未提交的 R2-L、R2-R 和 DSH-NATIVE 文档/代码改动。
- legacy active 分析路径仍是固定 `policy_delta + counter_thesis + synthesis`；独立 `/v1/research/observations -> research.v1 durable worker -> agentic evidence-round graph` 候选链已完成，R2-R-06E 后仍不替换 active pointer。
- 旧 `DshAgentRuntime` 仍只是 callable wrapper；R2-R-01 已另行完成官方 DSH Python SDK `0.1.1rc1`、受限 `decision-research` profile、真实 Session/Tool/Subagent/Trace adapter 和 local/live canary。
- 当前账本、PIT、Gate、Forecast/Outcome、Evaluation、MCP、Capability、Evolution、Promotion 和 Decision Desk 可保留。
- 当前 Kernel/前端仍包含 `crypto_macro.v1`、Forecast 和 30m/24h/72h 等金融默认值；通用平台隔离尚未被第二领域证明。
- `crypto-macro-decision` Skill 中的根因链、事实门、来源回退、反方审查和鲜度规则已在 R2-R-00 拆成正式 `crypto_macro.v1` Domain Pack；Web/Official/Market capability、Evidence lineage 和双 Snapshot 已在 R2-R-02 以 deny-by-default/replay 方式实现。
- `research-mcp` 与 `hub-research-worker` 已加入 Compose；replay profile 默认只包含 `replay.research`，live profile 由显式配置选择，当前隔离产品实例只允许已审计的 `web.search`，不混入 `replay.research`。
- R2-R-06C 前三次真实 PIT Canary 均作为失败证据保留；第四次 `r2-r-06c-20260830-low-profile-canary` 已在 56.5 秒内完成，取得 2 条 attested Evidence，PIT/unattested 均为 0，三个 Horizon distinct。随后完成 `r2-r-06c-20260830-repair-full-12case`：Fixed 12/12，DSH 9/12；DSH 失败为 `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout`，PIT violations 为 0 但 unattested 为 1。ADR-0010 已将可信 Result 收回 adapter/确定性代码；replay Horizon 已相对 PIT cutoff 校验，Round 工具轨迹已从 canonical Trace/MCP Result 投影。当前低推理 candidate profile 为 `decision-research.v1:faf1b3115f7d339c`，12-case 门禁已放行但尚不能 Promotion。
- R2-R-06D 已完成：Warsh Jackson Hole 真实事件 Run `run_27acb7c425914bc7a69060637ea1feb3` 使用 DSH `gpt-5.5` 尝试 `web.search`，Search 在 20 秒 capability deadline 内失败；产品仍保留 Trace、双 Snapshot、Result 和 reject Artifact，Evidence 为 0，active pointer 未改变。另修复了长官方文本超过 4000 字符时 Request Factory 构造失败的输入投影缺口，原文和 hash 仍保留。
- R2-R-06E 已生成 Runtime 决策包，结论为 `retain_baseline / pending_owner_review`；DSH 继续 candidate/shadow。Owner usefulness 表单尚未填写，下一候选只能在新的 owner gate 下聚焦 Search reliability/error provenance。
- R2-R-06E 后的真实隔离 Run `run_488b389ad8674dcfb632d12ea7b2399c` 暴露了具体缺口：模型可自由填写 `observed_at`，晚到结果被正确 PIT 拒绝但被上层粗略归类为 `provider_timeout`；首个并行 MCP 失败会 abort 其余调用，失败 Run 的 stop/error 状态未完整投影到前端。G1-A/B/C/D 已修复这些边界；不得用手工时间戳或更多 Prompt 规避。
- 所有已知后续工作已汇总到 [产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)：G1 可靠性、G2 来源覆盖、G3 单机 prospective 价值验收、G4 ASR、G5 第二领域、G6 规模化部署；未通过对应 Gate 不自动开下一阶段。
- Owner 已授权 G1/G2；G1-A/B/C/D 与 G2-A/B 已完成实现和离线验证，详细契约投影和退出门见 [G1/G2 实施方案](../stages/R2_R_G1_G2_EXECUTION_PLAN.md)。G2-C 真实 Search canary 已在 owner 授权的隔离、只读、限时边界内执行，但因 `research_capability_timeout` 失败安全；G2-D 事实充分度验收仍 pending。
- Research Command Center 已成为默认工作区；durable Run 列表、Plan/Tool/Evidence/Sufficiency、主/反根因链、独立 Horizon、停止原因、规范化 Trace、SSE 和 owner command 已完成。默认不展示 DSH raw JSON 或 Provider payload。
- 2026-08-30 隔离浏览器 smoke 已完成：API `8030`、replay research worker、Vite `5175` 共享临时目录；页面提交 -> `202 accepted` -> durable worker -> `research_only`/16.7% hard coverage/5 个 hard gap，12 条 SSE trace，终态关闭；截图和脱敏证据见 [前端运行链记录](../evaluations/G1_G2_FRONTEND_RUNTIME_SMOKE_2026-08-30.md)。这证明本机离线链路，不证明实时网络检索。
- 最新复跑 Run 为 `run_9a1a75c170ce4da8978721bbd31794fb`、Artifact 为 `art_9232327c170ce4e45a0994bd8b4e18391`，同样得到 `research_only`/16.7% hard coverage/5 个 hard gap/12 条 SSE；未知 `/v1` 路由返回 JSON 404。首次并发初始化新 SQLite 时观察到 `table events already exists` 迁移竞态，顺序启动已验证，跨进程 migration lock 尚未实现。
- 本轮根因修复：前端 API target/base URL 可配置且 Inbox/Research 入口语义分离；研究图与 ledger commit 双层禁止 insufficient/non-sufficient 结果发布方向性 Forecast；replay worker 的注入时钟贯穿 commit，长任务 heartbeat 持续刷新，未知 `/v1` 路由返回 JSON 404。Compose 的默认研究 runtime 仍是 replay fixture；显式 DSH live 实例由 `run-product.sh` 注入 `dsh-web` 和已审计 capability。另记录并发首次迁移竞态：Compose 依赖顺序可规避，跨进程 migration lock/一次性 migrate job 尚未实现。
- 2026-08-30 产品复盘确认：当前实现按产品价值评价仍不可用；网页默认是 replay 诊断态，DSH Research Runtime 仍是 candidate/shadow。复盘结论和长期工程教训见 [产品失败复盘](../retrospectives/RETRO-2026-08-30-PRODUCT-FAILURE-AND-LESSONS.md)。
- [DSH-first 产品实现总方案](../product/DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)、[ADR-0012](../decisions/ADR-0012-dsh-first-product-rebaseline.md) 和 [ADR-0013](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md) 已于 2026-08-31 获 owner 接受：直接复用 DSH Web 作为交互主壳，Hub 以官方 Host/Client plugin 接入，Decision Desk 收敛为管理后台。当前 Python SDK 仍是已验证的 candidate/fallback；DSH-NATIVE-CORE 工程门和 `PRODUCT-CLOSEOUT-EXEC-02` 的 E1/E2-R 已完成，实时 capability 与产品价值仍未闭环。
- [DSH 与 Decision Hub 系统总装设计](../product/DSH_HUB_SYSTEM_ASSEMBLY.md) 是最新独立总装说明：只说明 DSH、Hub 外层、Domain Pack、两个前端、三类状态和当前/目标代码目录。新会话若不理解“DSH 外层为什么存在”，先读该文，不从历史总架构推断最新运行拓扑。
- [DSH Native Web Product Core Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 状态为 `engineering acceptance complete / product value pending`。固定上游 commit `0a53fb55bea101816fa226bb964ae2bed71c343b`、源码版本 `0.1.2-alpha.2` 和 tar SHA-256 保持不变；NATIVE-00 至 NATIVE-05、NC-01 至 NC-07 已完成官方 Web 三场景、业务状态投影、callback recovery、Web restart、版本 fail-closed、locked rollback 和浏览器验收。原 NATIVE-06 的 live/value 工作已由获授权的 `PRODUCT-CLOSEOUT-01` C3-C7 承接；这不授权扩大 capability、切 active pointer 或自动 Promotion。
- [最终产品交付与主线闭环方案](../product/FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md) 已被 owner 接受并形成 `PRODUCT-CLOSEOUT-01`：最终用户只进入 DSH Web，Hub 是后台控制面/资产库，Decision Desk 是管理后台。C1-C7 与 E2-L 的工程、replay 和真实 Web 证据已闭环；只剩 E3 前瞻价值观察未完成。
- 2026-08-31 交互审计确认：`tools/dsh_native_acceptance.py --scenario success --serve` 是一次性 `llm-replay` 验收页面，不是 live DSH。首轮 9 次模型调用耗尽后，继续输入会在第 10 次调用得到官方 `script exhausted`；这是 replay transport 的预期边界，不是用户输入或 Hub bridge 错误。详见 [DSH 交互运行态与插件生态审计](../evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md)。需要自由多轮输入时必须启动无 replay patch 的独立 live profile，并通过 owner gate 提供 Provider/capability 配置。
- 2026-08-31 replay 误用修复已通过真实浏览器复验：DSH Host 配置改为官方 `schemastery` 支持的 runtime union；只读守卫挂在 `conversation.input.dock`，因此新建空 Session 也会显示 replay 提示并锁定输入。新隔离实例 `64619` 的截图、hash 和 `327` Python / `18` DSH plugin / `9` Decision Desk 测试证据见上述审计文档；随后在全新标签和实例 `50212` 复验了新建会话锁定、输入拦截和零控制台错误。旧 `65349` 仍是旧 bundle，不能用于验证新行为。
- 2026-08-31 live Provider canary 已完成但不等于产品价值通过：`infra/dsh/run-live-web.sh --port 50220` 使用 gitignored `data/dsh-live/.env` 和 `settings.yaml`，真实网关 `/v1/models` 含 `gpt-5.5`，`/v1/responses` 可用而 `/v1/chat/completions` 返回 404，因此 route 锁定 `api: openai-responses`。首次请求因未被网关声明的 `reasoning: high` 以 `UNSUPPORTED_REASONING_EFFORT` fail-closed，修正为 `reasoning: off` 后真实 Session `session-da5b144a-7ac6-49a4-8ec9-ff4ec2eb9a48` 两轮返回 `LIVE_OK`/`LIVE_TURN_2` 并落盘 JSONL；新鲜标签页无控制台错误。实时 Search、主动补证、常驻 worker、事实覆盖和产品价值仍未证明，详见 live 审计。
- 同一真实网关已额外通过一次 Decision Hub 合成文本 canary：`tools.canary.run_live_text_canary` 返回 `admitted=true`、`completed`、`publish`、三个 Horizon，Run `run_13b0bb8a71b44fbd9db8f6d36d449c31`，结果 hash `3fee1d30ff543c6007e55e448d2fb42f39b6efba6cbcaaae43b756d8406c2600`。这是 R0 Provider/Graph/结构化输出/Artifact 兼容性证据，使用临时数据库，不证明实时事实、Search 补证或产品收益。
- 2026-08-31 新增 [产品执行总方案](../product/PRODUCT_EXECUTION_MASTER_PLAN.md) 和 [PRODUCT-CLOSEOUT-EXEC-01 执行记录](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-08-31.md)：锁定最终用户只进入官方 DSH Web、Hub 为控制面/资产库、C1-C7 任务、SDD/BDD/TDD/ADR 和三层验收门。该方案现为架构与执行总参考；2026-09-01 起不再承担唯一入口角色。该日的 Compose build/新实例证据曾受 Docker registry 超时阻塞，作为历史失败保留，不能与后续 2026-09-01 实例混用。
- 2026-08-31 新增 [产品实现与最终验收方案](../product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)：将 C1-C7 的代码落点、官方 DSH workspace 注册、两层循环、三份状态、插件/Domain Pack、错误语义、SDD/BDD/TDD/ADR、上下文压缩和最终产品/价值门收敛为详细执行 checklist。2026-09-01 起顶层交付控制以 [最终产品交付实施书](../product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md) 为准；宿主 replay 路径注入已修复，默认 Compose 仍为 replay，live 产品实例使用显式 `dsh-web` 配置。
- 2026-09-01 C1/C2 live 失败链路已在全新实例 `8140/8142/51890` 验证：官方 DSH Web 已加载 `Crypto Macro Trader` 和 `建立研究任务`，typed intake 创建 durable Run，并由 `dsh-web` Host 实际建立受管 DSH Session/Trajectory/JSONL link；真实 `web.search` 多次调用因中转站 Responses web-search 无响应而超时，Run 以 `provider_timeout/dsh_web_deadline_elapsed` fail-closed，未生成 Evidence/Artifact/Forecast。该失败事实继续保留，不因后续 replay 成功而改写。
- 2026-09-01 新增 [最终产品交付实施书](../product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)，将最终用户视图、DSH/Hub/LangGraph/Domain Pack 所有权、两层 loop、三份持久化状态、插件边界、C1-C7 实现 checklist、SDD/BDD/TDD/ADR 约束和工程/产品/价值验收门收敛为本轮交付控制书。该文档不把 replay 或 Provider 失败改写为产品可用；最新执行状态以本文件和收口总执行书为准。
- 2026-09-01 新增 [通用底座与首个产品最终实施章程](../product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)，锁定 DSH Web 唯一用户入口、Hub 控制面/资产库、LangGraph 外层生命周期、官方插件与 Domain Pack 边界、P1-P6 有界路线及 E1/E2/E3 验收门。其实施阶段已由当前控制书收口，不能再与控制书并列成为执行入口。
- 2026-09-01 最终收口修复了受管 Session、状态投影、Search attribution、Tool 身份与 durable 预算等根因，并完成官方 Web 真实 Run、后台 child recheck、双前端、四视口、console、完整测试和截图 hash 验收。早期 `343/34/9` 测试数字是历史快照，当前完整门为 `390/53/10`。

## 已接受决策

- 本次执行目标已登记在 [DSH-NATIVE-CORE 完成实施方案第 0 节](../stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)：只闭合官方 Web/Host/Client/耐久桥接的可复核工程证据。锁定源码闭包内含同版本 `@deepseek-ai/dsh-llm-replay` 测试支持包，可注入临时 profile 做 keyless replay；它不属于生产依赖。若该包在 fresh 闭包中不可用或需要真实网络，必须停在外部前置条件，不自制 transport、不伪造官方 Web 证据。

- Product Kernel 不依赖 Harness；Agent 只产生候选，确定性 Gate 唯一裁决。
- DSH 通过 Workbench/Capability/Runtime adapter 接入，Session/JSONL 不是业务账本。
- DSH 模型只拥有因果链/horizon 语义候选；Session/Tool/Evidence/Coverage/时间戳/计数由 adapter 与代码拥有。
- 插件 deny-by-default，必须经过许可证、安全、契约、回放和 owner enable。
- SDD -> ADR -> BDD -> TDD -> 回放/canary -> 文档/状态/commit。
- DSH-first 和原生 DSH Web/插件方向已接受；`DSH-NATIVE-CORE` 已完成，当前只按 `PRODUCT-CLOSEOUT-01` C1-C7 执行。不切 active pointer；live capability 只允许 C4 的隔离、只读、限时 canary；不扩 ASR/PPT/第二领域。

## 当前授权

- Owner 已于 2026-08-29 接受 [ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)、[ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md)、[R2-R Stage Charter](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) 与[研究智能体主体产品规格](../product/RESEARCH_AGENT_PRODUCT_SPEC.md)，并于 2026-08-30 授权 G1/G2 实施方案。
- Owner 已于 2026-08-31 接受 ADR-0012/0013 和系统总装设计，授权 `DSH-NATIVE-CORE` 的 NATIVE-00 至 NATIVE-05 实现和完整离线自测。
- Owner 已于 2026-08-31 额外授权一次使用本机持久化 Provider 配置的限时 live 多轮 canary；该授权不包含实时 Search/Official/Market 扩展、自动联网、active pointer 切换或产品价值结论。
- 授权覆盖 R2-R-00 至 R2-R-06，以及 G1/G2 可靠性与事实覆盖修复；不覆盖自动交易、任意社区插件、提前切 active pointer、ASR、PPT、多用户或第二领域。
- Owner 已于 2026-09-01 接受 `PRODUCT-CLOSEOUT-01` 的完整 C1-C7 方案和隔离只读 capability canary；只有扩大网络权限/费用、修改 Gate/账本/active pointer、自动交易、第二领域或第二套 Agent Loop 时重新设 Gate。

## 当前唯一目标

当前唯一目标是 `E3-PROSPECTIVE-OBSERVATION`：停止新增产品功能，在至少 14 天或 20 个
高影响事件中运行同一 DSH-first `research_only` 主线，记录事实覆盖、延迟、失败率、成本、
人工复核时间、usefulness、30m/24h/72h Outcome、Brier、方向准确率和净收益。窗口结束只
允许 `promote / retain_baseline / stop`；Fixed 保持 active，DSH 保持 candidate/shadow。

## 当前不做

- 不把旧 `DshAgentRuntime` callable seam 当成真实 DSH 集成，也不把已通过 canary 的 DSH candidate 当成已 promotion 的正式 Runtime；
- 不安装未经准入审计的社区插件；
- 不改 active pointer；
- 不迁移/删除现有金融表；
- 不开发 PPT、ASR、多用户、自动交易或公共插件市场；
- 不提交、不 push、不回退用户工作树，除非 owner 单独要求。
- 不把 `implemented`、`candidate` 或 `completed` 工程状态解释为 `product_ready`；产品可用必须有真实能力和 owner 价值证据。

## 最近验证证据

最新验证证据见 [E2-L 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)：正式 Run、完整 DSH Session、Plugin build hash、12/12 Tool Call、15 条 Evidence、Search timeout provenance、代码 Gate、后台 child recheck、双前端截图 SHA-256 和完整质量门均已记录。早期配置错误、模型 timeout、Search 4/8 timeout 和 attribution 缺陷 Run 均保留为历史失败样本，不被最终 Run 改写。
