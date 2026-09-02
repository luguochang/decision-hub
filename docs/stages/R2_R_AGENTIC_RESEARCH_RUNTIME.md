# R2-R Agentic Research Runtime Stage Charter

版本：`STAGE-R2-R-2026-08-29.v0.1`
状态：`completed / retain_baseline`（owner 于 2026-08-29 授权 R2-R-00 至 R2-R-06；R2-R-00 至 R2-R-06E 已完成，DSH 未通过 Promotion 门）
前置：R0、R1、R1-L、R2-00 至 R2-05、R2-L 离线/本机工程退出门
关联决策：[ADR-0008 Agentic Research Runtime 与双层循环边界](../decisions/ADR-0008-agentic-research-runtime.md)
产品体验真源：[研究智能体主体产品规格](../product/RESEARCH_AGENT_PRODUCT_SPEC.md)

> 本阶段先纠正“产品目标是研究智能体，正式主链却是固定问答工作流”的偏差。Owner 已授权完整 R2-R 大阶段；仍不得提前修改正式 active pointer、自动安装社区插件、开放无限网络权限或越界实现 ASR/PPT/自动交易。

平台所有权基线见 [通用产品平台基线](../platform/PLATFORM_BASELINE.md)、[资产与扩展模型](../platform/ASSET_AND_EXTENSION_MODEL.md) 和 [ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md)。本阶段只建立这些边界在 `crypto_macro` 的第一个真实调用方，不进行全仓平台化重构，也不实现 PPT；交易员方法拆分见 [Crypto Macro Domain Pack](../domains/crypto_macro/README.md)。

## 0. 先定义什么是智能体

本项目使用以下四级定义，禁止把一次 LLM 调用或固定工作流宣传成智能体：

| 等级 | 定义 | 是否属于智能体 |
|---|---|---|
| LLM Call | 输入一段上下文，模型一次返回结构化结果 | 否 |
| LLM Workflow | 代码预先写死调用顺序，模型只填写各节点结果 | 否；只是含 LLM 的工作流 |
| Tool-using Agent | 模型围绕目标观察当前状态、识别缺口、选择工具、读取结果并动态决定下一步 | 是，但可能只是单会话 Agent |
| Agentic Product | Agent loop 外还有持久任务、证据/PIT、预算、恢复、Gate、评测、人工权限和可观测产品面 | 是；这是 Decision Hub 的目标 |

Decision Hub 中的研究智能体必须同时满足：

1. 有持续目标和显式停止条件，而不是只回答一轮 Prompt。
2. 能从 Evidence Requirements 识别当前关键事实缺口。
3. 能在权限、时间和成本范围内自主选择 Search、Fetch、Official、Market 或 Specialist 能力。
4. 每次工具结果都会改变后续计划；失败时能选择授权的 fallback，而不是把“数据不足”直接写进结论。
5. 能进行有限 replan、反方审查、来源冲突处理和按周期独立决策。
6. 终止由证据充分度、deadline、预算或安全 Gate 决定；不能无限循环。
7. 所有事实、工具调用、停止原因和结果都可追溯、可回放、可评测。

工作流与智能体不是二选一。正确关系是：固定工作流管理产品生命周期和安全边界，智能体在其中承担开放式研究。Agent 不能取代业务账本和确定性 Gate，工作流也不能把 Agent 的每一步预先写死。

## 1. 发现的问题与设计偏差

### 1.1 真实失败样本

2026-08-29 使用真实 Provider 对 Kevin Warsh 讲话运行：

```text
event_id: evt_cf111a1bfc414abbbf7503262235dbd5
run_id:   run_78b7664c884e4dc6a36fc77454a6e47c
status:   degraded
gate:     research_only
```

`policy_delta`、`counter_thesis` 和 `decision_synthesis` 共消耗约 93.7 秒，但 30m、24h、72h 全部输出相同的 `no_trade / 52% / Trigger / Invalidation`。结果主动列出 DXY、2Y/10Y、BTC 基差、OI、funding、清算和 FedWatch 等缺口，却没有调用任何工具补证。

该结果只能算有限输入下的研究草稿，不能算合格决策。`52%` 是未校准的模型主观概率，不是历史统计概率。

### 1.2 当前实现为什么不是 Agent

| 当前实现 | 实际行为 | 产品影响 |
|---|---|---|
| `research_graph.py` | 固定并行一次 policy/counter，再固定合成一次 | 没有工具选择、缺口补证或动态停止 |
| `decision_graph.py` | `freeze_snapshot -> research -> gate_and_commit` | 研究后取得的新证据没有合法入口 |
| `supervisor_graph.py` | 只处理候选 Evolution，specialist 被要求只读 frozen evidence | 正式分析主链没有 Supervisor；覆盖能力不等于事实充分 |
| `SearchCapabilityPort` | 只有权限/PIT/预算外壳和 callable seam | 没有真实 Web Search/Fetch 客户端，也没有接入正式 decision graph |
| `DshAgentRuntime` | 只有接收 `execute_fn` 的通用包装器 | 类名存在不等于 DSH SDK、Session、插件、Web Tool、Subagent 已集成 |
| `/v1/observations` | FastAPI `BackgroundTasks` 仅执行兼容的 fixed baseline；`/v1/research/observations` 只入 durable queue | 研究主链不能依赖请求生命周期；legacy baseline 的兼容语义暂不迁移 |
| Horizon 输出 | 一次 synthesis 同时生成三个窗口 | 没有分窗口证据要求、鲜度、到期和独立触发规则 |

### 1.3 与最初设计的偏差

原始产品架构已经要求：

- `enrich_evidence` 在正式 `freeze_snapshot` 之前发生；
- Specialist 使用成熟 Agent/tool loop，不手写 ReAct；
- Supervisor 动态规划，工具结果和缺口决定是否 replan；
- 新证据必须形成 Evidence revision 和新的 Snapshot generation；
- DSH/Pi/其他 Harness 通过可替换 Runtime/Workbench adapter 接入；
- 30m/24h/72h 使用不同的市场鲜度和决策约束。

后续实现只完成了固定 Graph、接口 seam 和候选评测骨架，却在状态文档中把 R2 工程退出门写得过于接近“智能体已完成”。这是阶段验收口径错误，不应继续用局部 Prompt、更多 reviewer 或新增 JSON 字段掩盖。

## 2. 阶段目标、产品形态与停止条件

本 Charter 负责技术实施和退出证据；用户看到的任务中心、研究详情、报告、主动发现、聊天定位和未来 ASR 边界，以《研究智能体主体产品规格》为准。两份文档发生冲突时不得开始代码，必须先完成 owner 评审和一致性修正。

### 2.1 唯一大目标

> 把已经完成的 Decision Hub 账本、PIT、Gate、Evaluation、Promotion 和 Decision Desk，与一个真实可替换的 Harness Agent Loop 组合起来，使系统能够从人工文本、官方来源或新闻发现自动建立研究任务，主动补齐证据，形成可解释的根因链和分周期结果，并把完整研究过程展示给 owner。

### 2.2 阶段完成后的产品形态

阶段产品名：`U2.1 Research Agent Pilot`。

Owner 可以：

- 手工输入事件文本，也可以由日历/官方 feed/新闻发现触发；
- 立即获得 Run ID，并在页面看到研究实时推进，而不是等待一个同步回答；
- 看到 Agent 当前计划、证据缺口、工具调用、来源、失败/fallback、充分度和停止原因；
- 得到 30m、24h、72h 各自的行动、主观置信度、触发、失效、到期、下一复核和引用；
- 对比 Fixed Baseline 与 DSH Research Runtime，同一输入不改变 Core 账本/Gate；
- 在 DSH 失败时明确降级或回滚，不产生静默假结果。

Decision Desk 首屏必须是任务驱动的 Research Command Center，而不是空聊天框。聊天只作为人工提交、补充证据、追问和 owner command 入口；关闭页面不影响 durable research worker。

### 2.3 阶段停止条件

R2-R-00 至 R2-R-06 全部通过后停止扩张，进入真实观察期。不得顺势实现 ASR、自动交易、A 股/美股/PPT、多用户、公共插件市场或 PostgreSQL。只有真实观察数据证明需要时，才单独开启下一阶段。

## 3. 最终职责分层

```text
Manual Text / Calendar / Official Feed / News Discovery / future ASR
                              |
                    Admission + Event/Run
                              |
                       Trigger Snapshot
                 记录触发时已经知道的事实
                              |
              LangGraph Product Lifecycle Graph
             admission / rounds / recovery / Gate
                              |
                  ResearchHarnessRuntime Port
                              |
            +-----------------+-----------------+
            |                                   |
  DSH decision-research profile            Fixed baseline
     primary R2-R candidate               replay/fallback
            |
    Manager Agent + Tool Loop + Subagents + Session/Trace
            |
    Audited Tool Gateway / Core MCP
            |
    +-------+----------+-----------+----------------+
    |                  |           |                |
 Web Search/Fetch   Official    Market Tools    Pack Specialists
 long-tail facts    sources     exact numbers   causal/adversarial
    |                  |           |                |
    +---------------- EvidenceCandidate ------------+
                              |
             Normalize + provenance + PIT validation
                              |
                 Information Sufficiency Gate
                    | critical gap + budget
                    +-------------------------> resume same session
                    |
                    | sufficient or bounded stop
                    v
                       Decision Snapshot
                冻结最终实际用于决策的证据
                              |
             Causal Case + 30m/24h/72h decisions
                              |
                    Deterministic Publish Gate
                              |
       Artifact / Forecast / Outcome / Evaluation / Owner Review
```

### 3.1 LangGraph 保留的职责

- Run 生命周期、checkpoint、恢复、轮次和 deadline；
- Trigger/Decision 双 Snapshot generation；
- 调用一个可替换 `ResearchHarnessRuntime`；
- 每轮完成后的 Evidence 落账、Coverage/Conflict/Sufficiency 路由；
- 确定性 Gate、commit、Outbox 和 Outcome 调度。

LangGraph 不实现通用模型工具循环，不解析搜索网页，不维护 DSH Session，不决定发布。

### 3.2 DSH 保留的职责

- 单会话内的 model -> tool calls -> tool results -> next step 循环；
- Manager/Supervisor 与 Subagent 调用；
- Skill、插件、MCP、Web Search/Fetch、Session event log 和 telemetry；
- 在同一 Research Session 内根据工具结果动态继续、转向或结束。

DSH 不保存业务唯一事实，不写 Artifact/Forecast，不修改 Gate/active pointer，不拥有自动交易权限。

DSH 在本阶段是 Agent 执行主线，不是整个产品主线。跨领域的 Task/Run/Evidence/Artifact/Asset/Evaluation/Version/Promotion 属于 Platform Core；Forecast/Outcome/方向/周期属于 `decision` Product Extension；`crypto_macro` 的根因链、证据和鲜度规则属于 Domain Pack。Role Profile 组合这些规则与工具，Capability Plugin 提供原子能力。禁止把 PPT 字段加入 Forecast，或把交易规则写进通用 DSH profile。

### 3.3 Core 与 Decision Extension 保留的职责

Event、Observation、Evidence、Snapshot、Run、通用 Artifact identity、Evaluation/Asset/Version、Capability、Dataset、Candidate、Promotion 和 Rollback 继续属于 Platform Core。Forecast、Outcome、方向、周期和市场 Gate 属于当前 `decision` Product Extension。两者都不属于 Harness；即使未来把 DSH 换成 OpenAI Agents SDK、Pi 或其他 Harness，这些资产也不迁移。R2-R 只先锁边界和新增研究契约，不授权一次性搬迁历史表。

## 4. Runtime 决策

| 方案 | 优点 | 风险 | R2-R 决策 |
|---|---|---|---|
| 继续扩写当前 Fixed Graph | 改动小、已有测试 | 必须自写工具循环/会话/继续执行，继续偏离产品目标 | 只保留 baseline，不再扩成 Harness |
| LangChain `create_agent` 自建完整研究运行时 | Python 同栈、可用 tools/middleware | 仍需自己组合会话、插件生态、subagent 和持续研究产品面 | 不作为 R2-R 主实现 |
| DSH Python SDK + 受限 `decision-research` profile | 可复用完整 Agent Loop、Web tools、Subagent、Skill/MCP、Session、插件生态 | developer preview；需 profile allowlist、严格 adapter 与版本契约 | 选择为首个真实 candidate |
| OpenAI Agents SDK | Python、成熟 Runner/tools/handoffs/tracing | 可能绑定 OpenAI hosted tool/Provider 行为；与现有 sub2api 兼容性需实测 | 保留第二候选接口，不在本阶段同时实现 |
| Pi SDK | 轻量 stateful tool loop、事件流 | TypeScript sidecar、权限需外置、形成第二 telemetry/session 面 | 保留未来 candidate，不在本阶段实现 |
| Codex | 强工具、权限和长任务设计 | 面向代码/工作区，不是金融研究运行时 | 只借鉴，不作为业务 Runtime |

DSH 使用 Python SDK 启动 `decision-research` profile。该 profile 基于完整 `sdk`/base 能力集，但默认禁用 shell、文件写入、任意插件安装和宿主凭据访问，只开放经 manifest 审计的 Web/Official/Market/MCP/Skill/Subagent 能力；禁止用缺少 Web tools、subagents、jobs/telemetry 的 minimal profile 冒充完整验收。具体版本与最终 dump-config 必须锁定；升级先跑 contract、permission、replay 和 live canary。

## 5. 两层循环，不重复造轮子

### 5.1 内层：Harness Step Loop

DSH 自己处理：

```text
model request
  -> zero or more tool calls
  -> tool results
  -> model observes results
  -> next step / subagent / final output
```

Decision Hub 不复制这套循环，只通过 adapter 配置 Profile、MCP、权限、预算和输出 schema。

### 5.2 外层：Product Evidence Round

LangGraph 只处理产品级充分度：

```text
round 1: DSH research session
  -> persist evidence
  -> code coverage/freshness/conflict checks
  -> semantic gap assessment candidate
  -> critical gaps remain?

yes + budget -> continue same DSH session with exact gap list
no/bounded   -> freeze Decision Snapshot and synthesize
```

默认硬限制：

```text
max evidence rounds:       3
max total tool calls:      12
max subagents:             6
total deadline:            180s
per tool timeout:          20s
per model step timeout:    60s
max structured repair:     1
max estimated cost:        Pack policy
```

超出限制后必须停止并展示原因。不得用递归 Agent、模型自批预算或“直到完全充分”为条件。

## 6. 双 Snapshot 与 PIT

### 6.1 Trigger Snapshot

在 Event/Run 建立时冻结：触发文本、事件身份、发布时间、收到时间、已有来源和触发时市场事实。它用于证明“为什么当时启动研究”，后续不能修改。

### 6.2 Evidence Acquisition

每个工具结果先进入 `EvidenceCandidate`：

```text
evidence_id / kind / authority / source_url
published_at / observed_at / received_at
content_hash / excerpt / structured_payload_ref
tool_call_id / research_session_id / round
quality / freshness / conflict_group
```

PIT、域名、权限、hash 和 schema 通过后才成为 Evidence revision。搜索摘要只能是 `search-derived`，不能伪装成官方原文或交易所原生数值。

### 6.3 Decision Snapshot

研究达到充分度或预算停止后，再冻结真正用于判断的 Evidence generation。最终 Candidate、Gate 和 Forecast 只能引用 Decision Snapshot。Replay 模式把 DSH Tool Gateway 替换成 archived/replay gateway，禁止访问事件 cutoff 后的网页或行情。

## 7. Capability 与数据获取策略

Owner 不需要预先封装所有未知网站。能力分三类：

| 能力 | 实现 | 适用 |
|---|---|---|
| `web.search` / `web.fetch` | DSH Web tool 或审计后的通用搜索 Provider | 未知新闻、讲话、长尾来源、fallback |
| Official Source Tool | Fed/BLS/BEA/Treasury/Cboe 等 typed connector | 高频权威事实、日历、原文和 actual |
| Market Tool | OKX/Binance/Bybit/Deribit 与宏观行情 adapter | mark/index、K线、funding、OI、基差、清算、收益率、DXY |

工具必须由 `CapabilityManifest` 提供：版本、输入/输出 schema、权限、允许域、timeout、费用、鲜度、license、audit status 和 replay policy。插件互相不硬引用；Manager 只按 capability ID 调用 Tool Gateway。

### 7.1 crypto_macro Evidence Requirements

首个 Pack 至少定义：

```text
event_identity_and_primary_text      hard
expectation_and_consensus            hard for scheduled policy/data events
us_2y_10y_and_dxy                    hard for Fed/macro direction
btc_last_mark_index_and_candles      hard
funding_and_oi                       hard for leveraged direction
basis_or_spot_perp_quality           soft, hard when thesis depends on leverage quality
liquidation_or_crowding_source       soft; absence caps confidence
cross_asset_risk                     soft/hard by event type
opposite_case                        hard
source_quality_and_freshness         hard
```

每个 requirement 指定 source priority、freshness、minimum independent sources、hard/soft、允许 fallback 和 confidence cap。代码检查结构与时效，模型只能提出语义覆盖候选，不能自行宣布 hard requirement 通过。

## 8. 多 Agent 与根因链

DSH Manager 不直接“凭感觉叫几个角色”，而是读取 Pack 的 required capability 和当前 gap：

```text
manager
  -> event/policy delta specialist
  -> expectation/pricing specialist
  -> macro transmission specialist
  -> market/derivatives specialist
  -> counter-thesis specialist
  -> data-quality specialist
  -> lead synthesis
```

角色数量可以随事件变化，但 `counter_thesis` 与 `data_quality` 对高影响事件不可删除。所有角色返回同一结构化 `SpecialistResult`，关键结论必须引用 Evidence ID。

最终根因链使用 `CausalCase`：

```text
observable facts
  -> prior expectation/positioning
  -> surprise or immediate cause
  -> durable root driver
  -> rates/USD/liquidity/risk/forced-position transmission
  -> first confirmations
  -> market implication
  -> opposite chain
  -> invalidations
```

Manager/Lead 只能产生 Candidate。Information Sufficiency Gate 和 Publish Gate 都是代码服务，不能由 DSH 或 LLM Judge 取代。

## 9. 30m、24h、72h 独立决策

三个窗口共享 Event 与 CausalCase，但各自有独立的 Evidence Policy 和 `HorizonDecision`：

| 窗口 | 重点 | 关键约束 |
|---|---|---|
| 30m | 即时价格发现、收益率/DXY、mark/index、funding/OI、清算和成交结构 | 最终合成前刷新市场快照；不允许使用过期 24h 背景代替实时确认 |
| 24h | 政策重定价是否持续、区间突破、资金/拥挤延续、下一事件 | 明确到期和下一复核；区分事件冲击与均值回归 |
| 72h | 后续数据/官员、利率与美元趋势、跨资产和流动性验证 | 不把 30m 噪声扩写成长线结论 |

每个窗口必须单独包含：`action`、`subjective_probability`、`evidence_refs`、`trigger`、`invalidation`、`expires_at`、`next_review_at`、`missing_facts` 和 `confidence_cap_reason`。

窗口可以同为 `no_trade`，但不得复用完全相同的 trigger/invalidation/expiry/evidence 集。精确重复视为 `horizon_output_not_distinct`，执行一次结构化修复，仍失败则 `research_only`。主观概率在得到足够前瞻样本校准前必须显示“未回测/未校准”，不能只展示一个醒目的百分比。

## 10. 自动触发与持久运行

LLM/DSH 不做 24x7 空转监听。现有 realtime worker 负责低成本发现和 admission：

```text
known calendar/feed poll
  or scheduled broad discovery query
  or manual observation
        -> Event/Observation/queued Run
        -> research worker durable claim
        -> DSH research session
```

为避免长研究绑定 FastAPI `BackgroundTasks` 或阻塞来源轮询，R2-R 增加同一 `hub-worker` CLI 的 `--role research` composition。它不是新仓库或微服务框架，只是第四个逻辑进程；研究工具由同一镜像中的独立 `research-mcp` 进程提供受控 MCP 出口：

| 进程 | 职责 |
|---|---|
| hub-api | admission、Query/View、owner 命令；不执行长研究 |
| realtime worker | 日历/feed/新闻发现、Outcome/outbox、Run enqueue |
| research worker | claim queued Run、LangGraph lifecycle、DSH session、checkpoint/recovery |
| evolution worker | 离线候选/eval/review；不执行正式研究 |

`research-mcp` 只暴露 `research_capability_execute`，默认仅启用 `replay.research`；网络能力必须通过 Pack manifest、显式环境 allowlist 和 audit/readiness gate 才能打开。Compose 中该服务不暴露宿主端口，只供 research worker 的 DSH profile 访问。

Run 使用 durable status + CAS/lease；API 返回 `202 + run_id`。进程崩溃后 research worker 从账本和 checkpoint 恢复，不能重复 Evidence、Artifact 或通知。

研究完成后的 `next_review_at`、关键缺口订阅和 horizon 到期使用 durable recheck job 唤醒，不让 DSH/LLM 24x7 空转。未来 ASR 只通过 `TranscriptSourceAdapter -> TextEnvelope` 进入同一 admission；R2-R 不实现音频 capture 或 ASR 推理。

### 10.1 Durable recheck 的唯一实现

R2-R 不新增第二张任务队列表或第二套 scheduler。Recheck 复用既有 `Run`
账本和 research worker，仅对 Run 增加两个通用调度字段：

```text
available_at   # 到该时间后才允许 claim；历史/立即 Run 为 null 或当前时间
parent_run_id  # 指向产生本次复核的上一条 Run；不改写父 Run
```

研究结果 commit 时，代码从通过校验的 30m/24h/72h 中选择最早的未来
`next_review_at`，在同一事务内创建最多一个 `research.v1` 子 Run：

- idempotency key 固定为 `research-recheck:<parent_run_id>`，重试不能重复建任务；
- `next_review_at` 必须晚于本轮完成时间且不晚于对应 `expires_at`；非法值不能唤醒；
- `claim_next` 和兼容 worker 统一忽略尚未到 `available_at` 的 Run；
- 子 Run 到期后走同一 `ResearchRequestFactory -> LangGraph -> DSH -> Gate` 链，
  不复用旧 Decision Snapshot，不原地修改旧 Artifact/Forecast；
- 每个父 Run 最多产生一个自动 recheck，新的子 Run完成后可根据自己的合法
  horizon 再安排下一次；不得创建“立即再运行”的自激循环；
- owner 手工 `recheck` 也只创建带 request-id 幂等键的新子 Run；API/UI 命令面在
  R2-R-05 暴露，底层调度语义在 R2-R-04 锁定。

这样 Run、Snapshot、Artifact、Outcome 和 Evaluation 历史均保持追加式；未来第二个
领域若也需要定时任务，再用真实调用方验证是否需要抽取更通用的 Schedule 接口。

## 11. 前端产品面

Decision Desk 的 Run Inspector 增加人可读 Research 面，不展示无意义的大块 JSON：

- 顶部：运行状态、实际总时长、当前 round、预算/工具调用进度、active runtime/profile；
- Plan：Manager 计划与 required capability 覆盖；
- Evidence：按 Official/Market/Web 分类，显示来源、鲜度、quality 和冲突；
- Tool activity：查询/工具/结果/失败/fallback/耗时，不展示 secret 或完整 provider payload；
- Sufficiency：hard/soft requirement、缺口、为什么继续或停止；
- Causal chain：主链与最强反链，事实/推论/情景明确分色；
- Horizons：30m/24h/72h 并排比较独立 action、trigger、invalidation、expiry 和 next review；
- Stop reason：`sufficient`、`deadline`、`cost_budget`、`tool_budget`、`permission_denied`、`critical_data_unavailable`；
- Runtime compare：Fixed baseline 与 DSH candidate 的覆盖、引用、时延、成本和 Gate 结果。

同时修复现有观测错误：Run latency 必须覆盖完整生命周期而不是只统计 `gate_and_commit`；所有 API 时间必须带 timezone，前端不能把 UTC 当本地时间。

前端信息架构和报告顺序见《研究智能体主体产品规格》。R2-R-05 必须同时交付 Command Center、运行中 Research Detail、最终 Report、Sources/Capabilities Health 和基于规范化 `ResearchTraceEvent` 的 SSE 续接；默认不得直接展示 DSH raw JSON、LangGraph state 或 Provider payload。

## 12. Canonical 契约

R2-R-00 只新增一个 canonical 文件：

```text
contracts/schemas/agentic_research.schema.yaml
```

内部 `$defs` 包含：

```text
ResearchSessionRequest / ResearchSessionResult
ResearchPlan / ResearchTask / ResearchRound
ToolInvocation / ToolResultSummary
EvidenceRequirement / EvidenceCandidate
CoverageAssessment / EvidenceGap / ConflictItem
CausalCase / HorizonDecision
ResearchTraceEvent / ResearchStopReason
```

Python/TypeScript/Zod 均从该文件 codegen；禁止在 API、DSH adapter、Graph 和前端分别手写镜像。大文本和 raw tool/provider payload 不进 Graph state，只保存内容地址/ref/hash。

## 13. 目标代码结构

以下是 owner 接受后才允许创建/修改的准确落点；不创建空目录凑架构：

```text
contracts/schemas/
└── agentic_research.schema.yaml

packs/crypto_macro/
├── pack.yaml                    # event class、required evidence、budgets、horizons
├── doctrine/                    # 根因链、来源优先级、行动语义
├── profiles/                    # manager/specialist 声明，不写业务 Python
├── tools/                       # capability binding，不含 secret
├── gates/                       # freshness/confidence/horizon policy
└── fixtures/                    # PIT replay、Warsh failure fixture、golden assertions

packages/kernel/decision_hub_kernel/
├── ports/research.py            # ResearchHarnessRuntime/ResearchTraceSink Protocol
├── application/research.py      # round、evidence persist、sufficiency use case
├── application/research_queue.py# Run claim/lease/recovery；不做 harness loop
├── decision/sufficiency.py      # hard/soft coverage、freshness、conflict code gate
└── persistence/db.py            # additive Run lease/evidence lineage repository

packages/orchestration/langgraph/
├── graphs/agentic_decision_graph.py # lifecycle + bounded evidence rounds
├── state/research.py                # 只存 ID/ref/round/budget/stop reason
├── routing/research.py              # continue/freeze/degrade/fail 条件
└── composition.py                   # active runtime 组合；不含领域判断

packages/runtime_adapters/
├── dsh_runtime/
│   ├── client.py                 # DSH Python SDK/restricted profile lifecycle
│   ├── runtime.py                # ResearchHarnessRuntime 实现
│   ├── profile.py                # Pack -> DSH profile/skill/tool binding
│   ├── event_mapper.py           # DSH session events -> ResearchTraceEvent
│   ├── result_mapper.py          # structured result/evidence mapping
│   └── health.py                 # version/capability/readiness canary
└── fixed_research_runtime/       # 现有 fixed graph 的明确 baseline wrapper

packages/provider_adapters/
├── search/                       # real web search/fetch + existing audited gate
├── market/                       # OKX first；Binance/Bybit/Deribit fallback
├── macro_market/                 # 2Y/10Y/DXY/VIX/oil 等标准化 snapshot
└── official_sources/             # Fed/BLS/BEA/Treasury/Cboe typed connector

packages/query_views/research/
├── service.py
└── view.py

apps/hub_worker/
├── research_composition.py       # --role research
└── main.py

apps/decision-desk/src/research/
├── ResearchRunPage.tsx
├── ResearchPlanView.tsx
├── EvidenceCoverageView.tsx
├── ToolActivityView.tsx
├── CausalChainView.tsx
└── HorizonDecisionView.tsx

tests/
├── contracts/test_agentic_research_contract.py
├── runtime/test_dsh_research_runtime.py
├── orchestration/test_agentic_research_graph.py
├── research/test_sufficiency.py
├── research/test_dual_snapshot.py
├── research/test_research_worker.py
├── replay/test_agentic_research_replay.py
├── e2e/test_agentic_research_flow.py
└── failure_modes/test_agentic_research_failures.py
```

现有 `packages/runtime_adapters/candidate_runtime.py::DshAgentRuntime` 明确标记为 incomplete seam。实现完成后由具体 `dsh_runtime.DshResearchRuntime` 取代，不在原类中继续堆 SDK、session、tool 和 telemetry 特判。

## 14. 工作包与任务卡

每张卡固定遵循 SDD -> BDD -> TDD Red/Green/Refactor -> 文档 -> 独立 commit；不得多卡一起提交。

| Task | 单一目标 | 主要实现 | 退出证据 |
|---|---|---|---|
| `R2-R-00` | 锁定契约和漂移基线 | canonical ResearchHarness/ProductExtension/DomainPack/RoleProfile/Capability schema、ADR、Warsh failure fixture、Pack policy | codegen/check、架构依赖测试、fixture 可重复证明当前缺陷 |
| `R2-R-01` | 接入真实 DSH Harness | `decision-research` profile、session lifecycle、MCP/tool binding、trace/result mapper、readiness | DSH contract/permission tests；真实 session 至少两轮 tool call；DSH 停止时 fail-closed |
| `R2-R-02` | 建立证据获取与双 Snapshot | EvidenceCandidate、Tool Gateway、Web/Official/Market adapter、Trigger/Decision Snapshot | PIT、future leakage、authority/freshness/fallback、replay adapter tests |
| `R2-R-03` | 完成 bounded agentic research graph | 外层 evidence rounds、sufficiency/conflict gate、required subagents、CausalCase、独立 HorizonDecision | 缺口触发继续；上限停止；窗口防复制；checkpoint recovery |
| `R2-R-04` | 完成自动触发和 durable research worker | Run queue/lease、`--role research`、Compose research-mcp、研究 API 无长 BackgroundTask、discovery policy、offline acceptance command | API restart 不丢 Run；source polling 不被长研究阻塞；重复 claim 单赢家；lease/checkpoint recovery acceptance |
| `R2-R-05` | 完成人可读研究观测面 | Research Query/View、Plan/Evidence/Tools/Sufficiency/Causal/Horizon UI、延迟/时区修复 | API/Vitest/build/browser 375/768/1024/1440；无 raw JSON 默认面 |
| `R2-R-06` | 证明产品价值并决定 Runtime | 12 个 PIT replay、Fixed vs DSH、至少一个真实事件、失败归档和 owner usefulness；具体任务卡见 [R2-R-06 价值验收执行方案](R2_R_VALUE_ACCEPTANCE.md) | 覆盖/引用/PIT/成本/延迟/Gate/预测报告；本次结论 `retain_baseline`，usefulness 仍由 owner 复核 |

### 14.1 当前执行状态

- `R2-R-00 done`：canonical contract/codegen、稳定公共导出、真实
  `crypto_macro` Pack、Warsh failure fixture、契约/边界/失败测试已完成；
  退出证据为 Python 170 passed、Ruff/Pyright/codegen/module docs、前端
  7 passed/build 和 `git diff --check` 全通过。
- `R2-R-01 done`：固定官方 Python SDK `0.1.1rc1`、bundled runtime server
  `0.0.1` 与 restricted `decision-research.v1:1d4ce1f40ab265e4` profile；本地
  handshake 和真实 gpt-5.5 canary 已通过 3 tool calls/results、1 subagent、
  2 turns、4 steps 与 Session 落盘。全仓 187 passed、Ruff/Pyright/codegen/docs、
  前端 7 passed/build 和 `git diff --check` 通过。
- `R2-R-02 done (offline)`；`R2-R-03 done (candidate path)`：bounded graph、sufficiency、continuation/stop/horizon、Decision Snapshot 和 commit projection 已接入独立 `research.v1` 候选链。legacy `/v1/observations` 继续保留 Fixed baseline 兼容语义，R2-R-06 前不得把“未切 active pointer”误写为图未接入。
- `R2-R-04 done (offline/local process)`：独立 durable research worker、`--role research`、Compose research-mcp、自动 discovery、API 研究入队、Run lease/CAS、Run-scoped Snapshot/Evidence identity、Trigger 后证据 cutoff、scheduled recheck、运行时 fail-closed 和真实 Python 子进程 checkpoint recovery 均已通过；第二次恢复为 `no_run`，Artifact/Evidence/Outbox 各仅一份。退出门为 Python 243 passed、`tools/research_acceptance.py` 24 passed、迁移 head `0019_scheduled_research_runs`。
- `R2-R-05 done`：新增规范化 Research Result/Trace/Command 持久化和 migration
  `0020_research_observability`；Query/View、增量 SSE、cancel/retry/recheck/feedback、
  Command Center、Plan/Tool/Evidence/Sufficiency/Causal/Horizon/Trace 人可读界面均已接通。
  Result 与 Artifact 同事务回滚，Trace 幂等且不保存 DSH raw JSON；浏览器在
  375/768/1024/1440 视口无横向溢出且控制台无错误。退出证据为 Python
  任务完成当时 Python 251 passed（当前总回归 285 passed）、research acceptance 24 passed、前端 8 passed/build、Ruff、Pyright、
  codegen、module docs、跨进程 recovery、Compose config 和 `git diff --check` 全通过。
- `R2-R-06 done`：完成 12 个 PIT Fixed vs DSH 对照、Warsh 真实事件 durable Run、
  失败归档和 `retain_baseline` Runtime 决策包。Owner usefulness 尚待填写；不自动切
  active pointer、不宣称 DSH active、预测优势或盈利；后续候选必须另立 owner gate。

## 15. BDD 验收场景

### Scenario A：发现缺口后主动补证

```text
Given 一条只包含 Fed 讲话原文、没有收益率和 BTC 衍生品数据的 Observation
When R2-R Research Runtime 执行
Then Manager 识别宏观与衍生品 hard gaps
And 调用授权的 Official/Market/Web tools
And 工具结果进入 Evidence lineage 后重新评估充分度
And 不能把“数据不足”直接复制成三个窗口的最终答案
```

### Scenario B：未知来源不要求 owner 先写接口

```text
Given 一个 Pack 未预先配置专用 connector 的突发事件
When Agent 需要验证事件身份和原始表述
Then 它可以使用通用 Web Search/Fetch 找到候选来源
And 对官方/可信来源进行交叉验证
And 搜索摘要保持 search-derived，不能冒充 canonical official fact
```

### Scenario C：关键数值不能只靠网页摘要

```text
Given 一个需要 BTC 杠杆方向的研究
When Agent 获取 funding/OI/mark/index
Then 优先使用 exchange-native Market Tool
And API 失败后才走 aggregator/web fallback
And 精确执行数据仍缺失时应用 confidence cap 或 trigger/no_trade
```

### Scenario D：三个窗口不复制

```text
Given 同一个 Decision Snapshot
When 生成 30m、24h、72h HorizonDecision
Then 每个窗口有自己的 evidence、expiry、next review、trigger 和 invalidation
And 完全重复输出触发一次 repair
And repair 后仍重复则降级为 research_only
```

### Scenario E：研究进程崩溃可恢复

```text
Given DSH 已完成若干 tool calls 且 Graph 已 checkpoint
When research worker 在合成前退出并重新启动
Then Run lease 到期后被重新 claim
And 已提交 Evidence/tool trace 不重复写入
And Artifact/通知最多一次
```

### Scenario F：DSH 不可用不污染 Core

```text
Given active candidate 指向 DSH 且 DSH readiness 失败
When 新 Run 到达
Then Run 明确 failed/degraded 或按已配置 policy 使用 fixed baseline
And 页面展示 runtime_unavailable/fallback 原因
And Core 账本、Gate、历史 Evidence 和 active pointer 不损坏
```

### Scenario G：自动发现并触发

```text
Given realtime worker 发现新的高影响官方讲话或新闻
When discovery policy 判断达到事件阈值
Then 只创建一个 Event/Observation/Run
And research worker 自动执行研究
And owner 无需再次手工粘贴文字才能看到结果
```

### Scenario H：到期复核不靠常驻 Agent

```text
Given 一条完成的 research.v1 Run 给出合法的未来 next_review_at
When 决策与 Artifact 在同一事务提交
Then 系统幂等创建一个 parent_run_id 指向原 Run 的 scheduled research Run
And available_at 之前任何 worker 都不能 claim
And 到期后现有 research worker 执行它并生成新的 Snapshot/Artifact
And commit 重试或 worker 重启不会重复建立 recheck
```

## 16. TDD 与故障矩阵

| 故障 | 预期行为 |
|---|---|
| Search 429/timeout | DSH/Tool policy 走允许的 fallback；记录 attempt；不得无限重试 |
| Official source 与新闻冲突 | ConflictItem 保留双方；hard conflict 阻止方向发布 |
| Market API stale | 标记 stale，尝试 fallback；仍 stale 则周期 confidence cap |
| DSH structured output invalid | 原始结果留 trace ref；最多一次 repair；仍失败则 typed failure |
| 未授权 tool/域名 | Tool Gate deny；Agent 不能自行扩权 |
| tool result 无 timestamp/hash | 拒绝成为 Evidence，不进入 Decision Snapshot |
| deadline/cost/tool budget 到达 | 取消未开始任务，明确 stop reason，已有结果仅允许显式降级 |
| future evidence in replay | PIT reject，实验失败，不能悄悄删除该证据后继续 |
| duplicate discovery/Run claim | idempotency/CAS 单赢家 |
| API/research worker restart | durable Run + checkpoint 恢复；无重复发布 |
| DSH version/capability drift | readiness/contract fail，candidate 不可 active |
| exact duplicate horizons | repair once，然后 `research_only` |

普通 CI 使用 Fake/Replay Harness 和 archived tools，不访问外网。DSH SDK integration、真实搜索和真实 Provider 只能由显式 canary/acceptance 触发，密钥不写仓库、账本、日志或前端。

## 17. 评测与 Promotion

R2-R 不凭一次漂亮答案切换正式 Runtime。固定 10-20 个事件样本比较：

- current fixed baseline；
- DSH agentic candidate；
- 后续可选 OpenAI Agents/Pi candidate。

指标分四组：

| 组 | 指标 |
|---|---|
| Evidence | hard coverage、freshness、official source ratio、citation validity、conflict disclosure、PIT violations |
| Agent trajectory | gap detection、tool success/fallback、round/tool budget、stop reason、recovery、重复调用 |
| Decision quality | causal completeness、counter-thesis、horizon distinctness、Gate status、主观概率校准标记 |
| Product/runtime | latency、cost、failure rate、owner usefulness；Outcome 到期后再加 Brier/net return |

Promotion 仍由 owner-only command 和现有 CAS/rollback 完成。R2-R 阶段完成不自动把 DSH 设为正式 active。真实收益需要前瞻观察，不能用历史回放或 LLM Judge 宣称。

## 18. 兼容迁移与回滚

1. 保持当前 Fixed Graph 为 `fixed-baseline.v1`，不删除、不重写历史 Run。
2. R2-R 新契约、Evidence lineage 和 Run lease 使用 additive migration。
3. DSH 先以 shadow/candidate 运行，同一 Event 可在 UI 对比但只有 baseline 发布。
4. 达到退出门后，由 owner 决定是否把 `crypto_macro` Research Runtime pointer 切到 DSH。
5. 回滚只切 pointer、停止 research worker/DSH profile；不删除 Evidence、Session trace ref、Artifact 或 Evaluation。
6. DSH 升级失败时保留旧版本 adapter/profile；Core、API 和历史查询仍可工作。

## 19. 允许和禁止修改路径

Owner 接受后允许：

```text
contracts/schemas/agentic_research.schema.yaml
packs/crypto_macro/**
packages/kernel/**/ports/research.py
packages/kernel/**/application/research*.py
packages/kernel/**/decision/sufficiency.py
packages/orchestration/langgraph/**
packages/runtime_adapters/dsh_runtime/**
packages/provider_adapters/{search,market,macro_market,official_sources}/**
packages/query_views/research/**
apps/hub_worker/**
apps/hub_api/** 仅公开契约/移除长 BackgroundTask
apps/decision-desk/src/research/** 和必要导航/API schema
migrations/versions/0017_*.py
migrations/versions/0018_*.py
migrations/versions/0019_*.py  # scheduled research child Run
后续 R2-R additive migration（必须新编号，禁止改写历史 migration）
tests/{contracts,runtime,orchestration,research,replay,e2e,failure_modes}/**
相关 README、runbook、状态、CHANGELOG
```

禁止：

```text
修改历史 migration snapshot
把 DSH session/raw JSON 作为业务事实
让 DSH/LLM 写 active pointer、Gate 或交易命令
为 R2-R 引入 Redis/Kafka/Temporal/DBOS/PostgreSQL
同时实现 Pi/OpenAI Agents 第二套真实 Runtime
实现 ASR、第二领域、多用户或公共插件市场
自动安装未经 owner 审计的社区插件
```

## 20. Owner Stage Gate

Owner 已于 2026-08-29 明确确认：

1. 接受本文对“智能体”的定义和当前实现仅为 LLM Workflow 的判断；
2. 接受 DSH Python SDK + 受限 `decision-research` profile 作为首个真实 Research Runtime candidate；
3. 接受 LangGraph 只管理产品生命周期、DSH 管理内层工具/子 Agent 循环；
4. 接受 ADR-0009 的 Platform Core/Product Extension/Domain Pack/Role Profile/Capability Plugin 所有权，且本阶段不进行全仓重构或 PPT 实现；
5. 接受 Trigger Snapshot + Decision Snapshot；
6. 接受新增 `hub-worker --role research`，研究 API 不使用长 BackgroundTask；legacy fixed baseline 路径在兼容迁移前保留原语义；
7. 接受 R2-R-00 至 R2-R-06 为一个完整大阶段，R2-R-06 前不宣称可用或收益；
8. 接受本阶段不同时实现 OpenAI Agents SDK、Pi、ASR、自动交易或第二领域。

授权后的唯一第一步是 `R2-R-00`：先写 canonical contract、Warsh 失败 fixture 和失败测试，证明旧链的缺陷，再开始 DSH 集成。不能从 UI、市场接口或 Prompt 调优开始。
