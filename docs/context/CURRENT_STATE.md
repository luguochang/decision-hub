# 当前状态短上下文

更新时间：2026-09-05（Asia/Shanghai）
用途：Agent 恢复任务时的第一读入口。本文只投影已验证事实，不替代 ADR、Stage Charter、schema 或实现。

## 2026-09-05 PD 当前执行状态

- PD-04..06 runtime closeout 已完成：官方 DSH Web、同一 Session 多轮补证、Report/Trace/Inbox/
  Decision Desk 和 Hub durable ledger 已在隔离实例复验；当前仍为 `research_only`，不代表金融事实
  充分、预测准确、盈利或自动交易。

- `PD-00` 已完成：显式 `ResearchTask.requirement_id`、canonical `FactEnvelope`、migration
  `0028`、Fact Store、八类 crypto-macro 语义真值表、Gateway/DSH/LangGraph/Query View lineage
  和 semantic-substitution Gate 已接通；历史 Evidence/Run 不回写。
- 错误 metric family、字段、单位、窗口、venue、delay class 或 independence group 现在
  fail-closed；Web locator、BTC derivatives 和当前 snapshot 不能替代 Fed pricing 或事件窗口。
- success replay 已使用语义完整 Facts 恢复为同一 DSH Session 两轮充分；partial failure、stale
  和 `no_baseline/window_missing` 继续保守终止。
- `PD-01` 已完成：未来日历事件创建 durable EventWatch，窗口固定为 `T-30m/T-5m/T0/T+1m/
  T+5m/T+30m/T+24h/T+72h`；迟到事件明确为 `retrospective_only`，采样失败和五分钟宽限过期
  保留独立错误，服务重建和重复 tick 不重复 capture。
- 最新已复跑质量门：Python `552 passed`；Ruff、Pyright、canonical codegen、module docs、前端
  test/build 和 `git diff --check` 均通过。DSH Plugin `69 passed + build`、Decision Desk `10 passed + build`。
- 运行收口后的 `host_hub_unreachable` 已由 Docker event 定位为旧测试栈挤压 Docker VM 后的 Hub
  OOM/`exitCode=137`；旧栈已停止且未删除历史数据。产品启动器现为单次 build，并在 Hub/Inbox/MCP、
  DSH Host 双向 readiness 后才启动 research worker。失败 Run 通过既有 owner retry 建立 child
  `run_2c06947df692424e326161d83f488368`，DSH/Decision Desk 同链复验通过；终态仍诚实为
  `research_only/provider_timeout`，不是 PD-07 样本。
- `PD-02A` 已完成 canonical ProviderRoute/ProviderAttempt、事件窗口 query 与失败 attempt 的
  durable `ErrorProvenance` 投影；`PD-02B` 已完成 Pack 驱动的 stable derivatives Router，OKX
  primary/CoinEx fallback、域/字段筛选、retryable-only fallback 和 Gateway composition 已通过。
  `PD-02C` 已完成 EventWatch/Sampler、内容寻址 archive、Run/EventWatch lineage、archive-only
  event routing、event return/OI delta 以及 FactStore/Gate 回放闭环；`PD-02D` 已完成订单簿
  imbalance proxy、Pack route 和衍生品完整窗口语义回放；`PD-02E`、`PD-02F` 的
  provider-neutral seam 与 Pack/profile/composition closure 已完成；`PD-02G` public adapter
  canary 已通过；`PD-03` 已完成 Source Registry、官方 `event.identity` parser 与 live Fed feed
  canary；PD-04..06 runtime closeout 已完成并有真实页面证据。真实分钟级 Provider 仍 blocked，
  下一阶段只能是 PD-07 前瞻观察或明确 Provider bake-off，不创建第二套 Provider DTO、Agent Loop
  或账本。

## 2026-09-04 交付阻断历史复核

- G2-AF 自动事件 Run `run_b89cb225177145cd9a0e7cd0b038e31c` 已证明 DSH 原生 Search、三轮补证、20/24 audited calls、13 条 Evidence、Artifact、通知和 child recheck 技术链；终态仍为 `degraded/research_only/round_budget`。
- WebSearch 已真实集成。证据不足并非 DSH 没有 loop，而是 Search locator 不能替代分钟级利率/美元/波动率、事件窗口、Fed 定价和多 venue 衍生品 typed facts。
- 当时通用 Sufficiency Gate 只校验 quality/freshness/PIT/authority/source count/conflict，尚未校验 requirement 所需的 metric family、字段、单位和事件窗口；该缺口现已由 PD-00 修复，历史 83.33% 仍不可追认成金融语义充分。
- 当时 `research_task` 没有显式 `requirement_id`；该结构性风险现已由 PD-00 修复。
- 当前只能称为单 owner `research_only` 工程试点，不能称为可交付的 30m/24h/72h 主动交易研究产品。G2-AF-05/E3 前瞻观察在事实语义和事件窗口完成前不能证明产品价值。
- Owner 已接受[产品事实充分度与主动交付修复方案](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)和[PD Stage Charter](../stages/PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)；PD-00..06 runtime closeout 已完成，当前进入 PD-07 前瞻观察准备。
- 当时全量 Python 为 `445 passed, 1 failed`，唯一失败是 submit 前 deadline 的旧 cancel 断言；该问题已按 accepted-before-cancel BDD 修复。此处数字属于历史复核，不是当前质量门；当前全量结果见本文件顶部 `552 passed`。

## 最新审计入口

六项产品现状、DSH/Hub/Loop、调度、自进化、代码冗余和 `.gitignore` 的独立回答见
[产品现状、架构与交付缺口审计](../evaluations/PRODUCT_STATE_ARCHITECTURE_AUDIT_2026-09-02.md)。
本次对“为何没有自动搜索、交易员角色/LoongSuite 是否进入主链、调度和自进化是否真实运行”的
根因复核见 [Agentic 主动研究缺口复盘](../evaluations/PRODUCT_AGENTIC_GAP_REVIEW_2026-09-03.md)。
该审计不新增实现授权；当前仍保持 `research_only`、Fixed active、DSH candidate/shadow。
Owner 于 2026-09-02 额外授权 [D2 官方 DeepSeek Live 主流程复验](../stages/D2_OFFICIAL_DEEPSEEK_LIVE_FLOW_PLAN.md)：
它只修复交付前暴露的 Provider/模型路由阻断并复验现有主线，不新增功能、不切 active pointer；
D2 完成后恢复 E3 前瞻价值观察。

2026-09-03 D2 真实验收已完成到诚实终态：官方 `deepseek-v4-flash` 探针通过；DSH Web 在
隔离 Compose 内真实建立受管 Session，完成 3 轮、12 次 capability 调用和 12 条 Evidence；
Run `run_6ea4b5b7c20140e2b4c0109d36b0179a` 最终因 FRED stale/事件窗口缺失和
`structured_output_invalid` 进入 `degraded/reject`。Evidence-only 降级、Coverage、失败
provenance 和审计入口保留，causal/horizon 方向语义为空。本次运行专属浏览器截图/console
归档和最终质量门复跑已完成：新 bundle 桌面/移动页面只有一个报告区域，移动视口无横向溢出，
console error 为 0；插件 57、Decision Desk 10、Python 395 项测试及静态/契约/文档门通过。
D2 因此完成到 `truthful terminal`，但不能写成方向分析成功或产品正式可用。完整证据见
[D2 官方 DeepSeek Live 主流程真实验收记录](../evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md)。

关于微信文章所述 LoongSuite DSH 可观测插件、Pilot、多层 Trace 所有权和 OBS-01..03
提案，见 [DSH 可观测插件接入评估与实施建议](../evaluations/DSH_OBSERVABILITY_PLUGIN_ASSESSMENT_2026-09-02.md)。
OBS-01 已在隔离 profile 通过 exact-version canary；普通 active product profile 仍默认不安装，
OBS-02/OBS-03 不得越过新的 owner gate。

本轮针对“hard gap 出现后没有继续搜索、产品像问答助手”的方案已单独记录在
[G2-AF 主动事实获取与自主研究阶段方案](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)。
当前状态为 `G2-AF-01..04 technical gates passed / G2-AF-05 observation pending`：DSH 原生
`web_search` 作为 discovery primary，Tavily（Search/Extract/官方 MCP）作为按需 fallback 和独立
交叉索引，Brave 作为后续独立索引回退，SearXNG 仅作可选自建灾备；DSH 继续负责内层 Supervisor
loop，LangGraph 只负责产品生命周期，Hub 负责 Catalog、PIT、Evidence、Gate、调度、通知和资产。
DSH 原生结果和 Tavily 结果都必须先经 Hub attestation，不能直接入账。最终隔离 canary 已由
官方 feed 自动触发，Run `run_b89cb225177145cd9a0e7cd0b038e31c` 在同一个 DSH Session
完成 3 轮、20/24 calls、13 条 Evidence、83.33% hard coverage、Artifact、Outbox 单次通知和
child recheck；终态为 `degraded/research_only/round_budget`。不切 active pointer。

2026-09-03/04 Search 能力核查补充：官方 DSH 上游锁定源码确实包含
`dsh-web-search-deepseek`、`dsh-tool-web` 和原生 `web_search`。它复用 `DEEPSEEK_API_KEY`，但默认
走独立的 Anthropic-compatible `/anthropic/v1/messages` Search route，不等于聊天
`DEEPSEEK_BASE_URL` 自动具备搜索。当前 Decision Hub `decision-research` preset 已开启官方
`tool-web` 的 `web_search`/`web_fetch`；为了保证所有业务证据进入 Hub Gateway，原生 Search 结果
只能作为 discovery candidate，不能绕过 `decision-hub-research-tool` 直接入账。
本轮已收到 Tavily key，但没有读取、持久化或联网调用；key 已出现在聊天内容，正式使用前应轮换，
只允许进入 gitignored `data/dsh-live/.env` 或 Secret Manager。完整核查和固定 27 条金融来源注册表
设计见 [G2-AF Tavily / DSH Search 执行记录](../evaluations/G2_AF_TAVILY_EXECUTION_LOG_2026-09-03.md)
和 [G2-AF 阶段方案第 3 节](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)。
当前仍不运行 Tavily、不扩大默认 allowlist；DSH native route、原生 locator -> Hub Gateway ->
Fetch/Official attestation、官方 DSH Web 同 Session continuation、结构化 synthesis 和自动事件链
均已通过。一次额外暴露 Hub `web.search` 的实验产生重复 native Search timeout，已固定为默认禁用；
详见 [ADR-0022](../decisions/ADR-0022-search-provider-route-boundary.md) 和
[G2-AF 实施执行记录](../evaluations/G2_AF_IMPLEMENTATION_EXECUTION_LOG_2026-09-04.md)。
下一步只按 [PD-07 前瞻价值观察阶段卡](../stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)
冻结 cohort，并以第一条合格未来 EventWatch 开始 14 天/20 事件观察。轮换旧 Tavily key 后的独立
fallback canary 不是启动观察的默认前置条件；只有真实主路事实发现失败且成本/授权获批时才按需执行。
不得以继续增加框架或页面替代价值验证。

## 历史状态：E2-L 已通过，进入 E3 观察（已由 PD runtime closeout 更新）

- G2-AF-01..04 已进一步证明主动搜索、同 Session 多轮补证、自动 feed admission、Artifact、通知和
  复查技术链；当前仍是 `research_only`，分钟级 macro transmission、Provider 模型费用归集和
  prospective Outcome/usefulness 尚未闭合。
- 唯一执行事实源是 [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，最终证据是 [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。旧 Pilot Ready v2、E2L-01/02/03 和 C1-C7 任务书只保留历史证据，不能重新成为执行入口。
- 正式 Run `run_04dc1a46fd1e4c3b988750e18b0e9581` 从官方 DSH Web 建立，在同一受管 Session 完成 2 轮、12/12 durable Tool Call、15 条 Evidence、66.7% hard coverage，按代码 Gate 收敛为 `research_only / tool_budget`；DSH 与 Decision Desk 终态一致。
- 后台 scheduler/worker 自动创建 child Run `run_7df306876d114e09b8e83507d06f3ef0` 并再次完成 2 轮、12/12 Tool Call，证明持续复查不依赖 owner 再次输入。
- 两次 `web.search` timeout、FRED stale 和 unknown cost 被如实保留；不发布方向性交易结论，不把失败改写为成功或 `no_trade`。
- 当前产品状态：`pilot_ready=true / pilot_usable=research_only / automatic_trading=false / Fixed active / DSH candidate-shadow`。E2-L 原结论允许开始至少 14 天或 20 个高影响事件的 E3 观察；2026-09-04 交付阻断复核已提议在事实语义和事件窗口补齐前暂停把该观察解释为产品价值验收，等待 owner 决策。这不等于预测、盈利或 Promotion 已证明。
- 2026-09-04 历史质量复核：Python `445 passed, 1 failed`；DSH Plugin `60 passed + build`、Decision Desk `10 passed + build`、Ruff、Pyright、codegen、module docs 和 `git diff --check` 通过。该数字属于历史复核；2026-09-05 当前全量结果为 Python `552 passed`、DSH Plugin `69 passed + build`、Decision Desk `10 passed + build`，详见顶部和 PD 执行日志。此前 E2-L/D2/G2-AF 记录中的 `390/395/397/404/416/446` 是对应历史时点的验收数字，保留作历史证据，不与本次复跑混用。Compose、fresh migration、recovery、三类 replay、callback/Web restart 和 rollback 的专项证据仍以各自验收记录为准；G2-AF 最终 Desk/DSH 报告/轨迹截图已归档。

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
- `research-mcp` 与 `hub-research-worker` 已加入 Compose；replay profile 默认只包含 `replay.research`，live profile 由显式配置选择，当前隔离产品实例允许官方 DSH native `web_search` discovery 和已审计的 Hub typed capabilities（含 `web.fetch`），不混入 `replay.research`；Hub `web.search` 默认禁用。
- R2-R-06C 前三次真实 PIT Canary 均作为失败证据保留；第四次 `r2-r-06c-20260830-low-profile-canary` 已在 56.5 秒内完成，取得 2 条 attested Evidence，PIT/unattested 均为 0，三个 Horizon distinct。随后完成 `r2-r-06c-20260830-repair-full-12case`：Fixed 12/12，DSH 9/12；DSH 失败为 `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout`，PIT violations 为 0 但 unattested 为 1。ADR-0010 已将可信 Result 收回 adapter/确定性代码；replay Horizon 已相对 PIT cutoff 校验，Round 工具轨迹已从 canonical Trace/MCP Result 投影。当前低推理 candidate profile 为 `decision-research.v1:faf1b3115f7d339c`，12-case 门禁已放行但尚不能 Promotion。
- R2-R-06D 已完成：Warsh Jackson Hole 真实事件 Run `run_27acb7c425914bc7a69060637ea1feb3` 使用 DSH `gpt-5.5` 尝试 `web.search`，Search 在 20 秒 capability deadline 内失败；产品仍保留 Trace、双 Snapshot、Result 和 reject Artifact，Evidence 为 0，active pointer 未改变。另修复了长官方文本超过 4000 字符时 Request Factory 构造失败的输入投影缺口，原文和 hash 仍保留。
- R2-R-06E 已生成 Runtime 决策包，结论为 `retain_baseline / pending_owner_review`；DSH 继续 candidate/shadow。Owner usefulness 表单尚未填写，下一候选只能在新的 owner gate 下聚焦 Search reliability/error provenance。
- R2-R-06E 后的真实隔离 Run `run_488b389ad8674dcfb632d12ea7b2399c` 暴露了具体缺口：模型可自由填写 `observed_at`，晚到结果被正确 PIT 拒绝但被上层粗略归类为 `provider_timeout`；首个并行 MCP 失败会 abort 其余调用，失败 Run 的 stop/error 状态未完整投影到前端。G1-A/B/C/D 已修复这些边界；不得用手工时间戳或更多 Prompt 规避。
- 所有已知后续工作已汇总到 [产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)：G1 可靠性、G2 来源覆盖、G3 单机 prospective 价值验收、G4 ASR、G5 第二领域、G6 规模化部署；主动事实获取的独立 G3 阶段方案目前仍为 proposed，未通过对应 Gate 不自动开下一阶段。
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

当前唯一执行目标 `D2-OFFICIAL-DEEPSEEK-LIVE` 已完成真实主流程复验到诚实终态：官方
`deepseek-v4-flash` 探针通过，DSH Web 在隔离 Compose 中完成 3 轮、12 次 capability
调用和 12 条 Evidence；Run `run_6ea4b5b7c20140e2b4c0109d36b0179a` 因 stale/缺失事件窗口
数据及 `structured_output_invalid` 进入 `degraded/reject`，Evidence-only 降级保留可信事实，
不发布 causal/horizon 方向语义。完整证据见 [D2 真实验收记录](../evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md)。
本次运行专属浏览器资产和最终质量门复跑已完成；D2 不改变 `research_only`、Fixed active、
DSH candidate/shadow。下一阶段只能另立 `E3-PROSPECTIVE-OBSERVATION`，观察至少 14 天或 20 个
高影响事件，不能由 D2 自动 Promotion。

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
