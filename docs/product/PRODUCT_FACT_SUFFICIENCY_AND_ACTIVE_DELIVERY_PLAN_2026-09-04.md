# 产品事实充分度与主动交付修复方案

版本：`PD-2026-09-04.v1`  
状态：`accepted / PD-00..06 engineering and runtime closeout completed / PD-07 observation pending`
适用产品：`decision.v1 + crypto_macro.v1`  
目标：在保留 DSH-first、可信 Evidence、PIT、确定性 Gate 和长期资产边界的前提下，把当前“能搜索、能循环、但事实仍不足”的工程试点收敛成真正有研究价值的单 owner 主动产品。  
非目标：自动交易、多用户、ASR、PPT、第二领域、公共插件市场、替换 DSH 或重写 Agent Loop。

本文是 2026-09-04 交付阻断审计后的独立产品方案。Owner 已于 2026-09-04 明确要求按本文和既有架构治理实施、自测；正式阶段门见 [PD Stage Charter](../stages/PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)，跨模块事实语义边界见 [ADR-0024](../decisions/ADR-0024-semantic-fact-and-event-window-boundary.md)。本文不替代 canonical schema、历史验收记录或逐任务执行证据。

## 1. 最终结论

### 1.1 能不能实现

能实现，但不能靠“再写一段 Prompt”或“把搜索轮次加大”实现。

现有代码已经证明以下工程能力可用：

- DSH 是真实 Agent Harness，同一 Session 可以进行三轮搜索、工具调用、补证和结构化提交；
- Hub 可以耐久记录 Run、Evidence、PIT Snapshot、Artifact、Trace、Outbox 和 child recheck；
- LangGraph 可以做外层 checkpoint、预算、恢复和确定性 Gate 路由；
- 官方 feed 可以无人值守地触发研究 Run；
- 失败、stale、域名拒绝和预算耗尽不会被伪装成成功。

当前没有实现的是“高影响金融事件所需的完整事实基础设施”：

- 分钟级利率、美元、波动率和跨资产数据；
- 事件前基线以及 `T-30m/T0/T+5m/T+30m` 可比较快照；
- 多交易所现货、衍生品和拥挤度窗口；
- 每个 requirement 的字段级、窗口级语义验证；
- 自动事件到主动报告、通知、Outcome 和评测的常驻产品体验；
- Provider 模型费用、工具费用和来源健康的完整归集。

所以问题不是“DSH 做不了”，而是上一阶段把“Search/Loop/安全停止已经工作”误当成“金融事实已经充分”。前者是必要的底座，后者才决定报告有没有价值。

### 1.2 当前能否交付

当前只能交付为：

```text
单 owner、单机、只读、research_only 的工程研究试点
```

当前不能交付为：

```text
能够对 Fed/宏观事件稳定给出 30m/24h/72h 可执行判断的主动交易研究产品
```

在第 15 节 `PD-00..07` 通过前，页面出现 `insufficient_sources` 是诚实结果，但不能把它算作产品成功，也不应直接开始 G2-AF-05/E3 的价值观察。用缺少关键事实的报告做 14 天观察，只会测量“缺数据时系统会安全停止”，无法测量交易研究价值。

### 1.3 WebSearch 到底有没有集成

已经真实集成并运行，不是占位实现。

2026-09-04 自动事件 canary Run：

```text
run_id: run_b89cb225177145cd9a0e7cd0b038e31c
admission: automatic
DSH Session: 1
research rounds: 3
audited tool calls: 20 / 24
retained Evidence: 13
normalized Trace events: 139
reported hard coverage: 83.33%
stop_reason: round_budget
gate_status: research_only
```

DSH 原生 `web_search` 找到了 Fed in Print、Reuters、财经媒体等 locator，也执行了 `web.fetch` 和 typed capability。问题不是“没有搜”，而是搜索结果没有自动变成可用于金融决策的事实。

### 1.4 历史质量门问题与当前状态

2026-09-04 的历史复核曾得到：

```text
445 passed, 1 failed
```

唯一失败是：

```text
tests/dsh_native/test_dsh_web_runtime.py::
test_web_runtime_timeout_cancels_and_fails_closed
```

该测试把整个 Run 截止时间设置为 `10ms`。当前工作树增加了 DSH readiness 和 Host 调用的
截止/重试边界后，数据库建链和 Prompt 持久化已经耗尽这 `10ms`，运行在 `submit` 前以
`dsh_web_submit_deadline_elapsed` 失败，`FakeHostClient.submit_calls == 0`。远端尚未接收 Session，
因此没有可取消的远端 Run，但旧断言无条件要求 `host.cancelled == [run_id]`。

该失败不是 WebSearch 缺证据的原因，也不是本文档造成的回归；它是当时工作树中“提交前超时是否应
调用远端 cancel”的测试语义与实现语义不一致。安全性仍是 fail-closed，但全量质量门当前必须标红，
不能继续沿用“最新 446 passed”的表述。该问题随后已按 accepted-before-cancel BDD 拆分并修复，
没有删除取消断言或扩大生产超时掩盖问题。2026-09-05 本轮复跑结果为：

```text
.venv/bin/pytest -q 533 passed
```

因此当前工程门是绿色；以上旧失败样本仅作为错误分类和回归证据保留，不再代表当前状态。

## 2. 为什么搜索后仍然显示证据不足

### 2.1 Search 是发现工具，不是精确行情数据库

搜索可以找到：

- 官方讲话原文和新闻页面；
- 对讲话的媒体解释；
- 某个数据或网页的入口；
- 可能需要进一步核实的来源。

搜索不能稳定替代：

- 事件前后美国 2Y/10Y 收益率分钟级变化；
- DXY、VIX、NQ/ES、油价的同窗口变化；
- FedWatch/OIS/SOFR futures 的精确定价变化；
- BTC mark/index/order book、OI 变化窗口、funding、basis；
- 多交易所 liquidation、long/short、taker flow/CVD；
- `T-30m` 基线不存在时的历史即时反应。

这类事实必须来自 typed provider 或系统事先保存的事件窗口快照。让 LLM 从网页摘要中猜数值，会破坏 PIT、准确性和可复盘性。

### 2.2 Search locator 还要经过 Evidence attestation

当前边界是正确的：

```text
DSH web_search
  -> locator/search_derived candidate
  -> web.fetch 或 typed provider
  -> domain/authority/time/hash/independence 校验
  -> accepted Evidence
```

搜索摘要不能直接满足 hard requirement。否则 SEO 页面、过期摘要或错误引用都可能被当成交易事实。

### 2.3 当前 Fetch allowlist 与真实搜索结果不匹配

`packs/crypto_macro/tools/bindings.yaml` 的 `web.fetch` 当前只允许有限官方机构、交易所和 Reuters 域名。真实 Run 搜到的 Fed in Print、CNBC TV18 等来源不在列表中，因而以：

```text
research_domain_denied
requested_domain_not_allowlisted
```

被拒绝。

不能简单把 allowlist 改成全网。正确做法是第 8 节的分级来源注册表：官方原文、一级新闻、市场数据、聚合器和未知网页分别处理；未知域名只能作为 locator，不能自动获得 Evidence 权限。

### 2.4 当前结构化 Provider 覆盖过窄

当前主要能力是：

| Capability | 当前实现 | 实际能力 | 不能满足的内容 |
|---|---|---|---|
| `official.macro` | 官方文档抓取 | 事件身份、官方文本 | 分钟级市场反应、共识定价 |
| `market.cross_asset` | FRED public | 日频 2Y/10Y/美元序列 | 5/15/30 分钟事件窗口 |
| `market.crypto_derivatives` | CoinEx public | 当前现货、funding、OI、mark/index、basis 快照 | OI 变化窗口、订单簿、清算、CVD、多 venue 独立确认 |
| `web.search` | DSH native | 当前网页 locator | 精确、可重复的市场遥测 |
| `web.fetch` | 受限网页正文 | 已批准域名的正文 | 未批准来源和结构化分钟行情 |
| `web.search.tavily` | adapter 已实现 | fallback locator | 尚未 live canary，也不能替代行情 API |

FRED 最新日频值相对 `macro_transmission=300s` 和 `expectation_pricing=900s` 的 freshness 规则必然是 stale。这个缺口不能通过多搜几轮修复。

### 2.5 事件发现太晚，无法重建 30 分钟反应

最近的 Waller 事件在研究开始时已经过去约 11 小时。系统没有提前保存事件前价格和宏观快照，因此不能诚实回答“讲话后 30 分钟发生了什么”。

即使现在取得最新 BTC 和收益率，也不能把“当前值”冒充“事件后 30 分钟值”。正确修复是第 9 节的事件窗口采集器；对于迟到事件，产品必须显示 `retrospective_only`，不允许补造 30m 结论。

### 2.6 83.33% 覆盖率目前也不能直接相信

当前 `assess_evidence_sufficiency()` 只确定性检查：

- `requirement_id`；
- `quality=accepted`；
- freshness/PIT；
- authority；
- 独立 `source_id` 数量；
- conflict group。

它没有验证某条 Evidence 的结构化字段是否真的满足该 requirement。例如，一条新鲜 BTC 衍生品快照如果被标为 `expectation_pricing`，当前通用 Gate 可能把它计入覆盖；但 BTC funding/OI 不是 FedWatch/OIS/前端利率定价。

此外，当前研究任务没有显式 `requirement_id`，`_attempts_from_result()` 通过 task 序号和 gap 序号推断归属。任务重排、并行或缺项时，可能把“尝试过哪个能力”记到错误 requirement。

因此 `83.33%` 只能解释为“通过当前通用 Gate 的覆盖”，不能解释为“83.33% 的金融语义已经正确”。`PD-00` 必须先阻止这种语义替代，再增加数据源。

### 2.7 既有问题、代码现状和最终修复矩阵

| 问题 | 当前状态 | 代码/运行证据 | 根因 | 最终修复 | 验收证据 |
|---|---|---|---|---|---|
| WebSearch | 已真实运行 | DSH JSONL/Run `run_b89...`；`search/dsh_native.py` | locator 不是精确事实 | 保留 DSH primary，Search -> Registry -> Fetch/typed fact | locator 不直接关闭 market gap |
| 搜索后停止 | 已有三轮有界 continuation | `agentic_research_graph.py::should_continue_after_round` | ladder 能继续，但可用能力不足 | 补齐 requirement 对应 primary/fallback，不增加无限 loop | 所有未尝试能力耗尽前不停止 |
| 分钟级宏观 | 未实现 | `macro_market/fred.py` 只有日频 FRED | 数据粒度与 300/900s Gate 矛盾 | `macro.cross_asset_intraday` + provider bake-off | 事件窗口 2Y/10Y/USD 等 typed facts |
| Fed 定价 | 未实现 | `expectation_pricing` 当前复用 `market.cross_asset` | 没有 Fed funds/SOFR/OIS/FedWatch 专用能力 | `macro.expectation_pricing` | BTC 数据不能满足该 requirement |
| Crypto 交易数据 | 部分实现 | `market/coinex_research.py` 当前快照 | 单 venue、没有 delta/book/liquidation/flow | 多 venue + event-window normalization | funding/OI/basis/delta + 独立 crowding |
| 事件前基线 | 未实现 | 当前 Snapshot 是 Run trigger/decision manifest | feed 到达后才研究，旧事件无法重建 | event watch + typed snapshot sampler | 未来事件具备 T-30m 到 T+30m |
| 语义 Gate | 未实现 | `decision/sufficiency.py` 只查 authority/freshness/count | requirement_id 可被错误内容占用 | FactEnvelope + Domain Semantic Gate | semantic-substitution fixtures 全拒绝 |
| task/gap lineage | 有结构性风险 | `_attempts_from_result()` 按数组序号映射 | contract 缺少显式 requirement_id | `research_task.requirement_id` | 重排/并行后归因仍正确 |
| 交易员 Role | 规则已用，产品投影不足 | `role_profile_ref=crypto_macro.manager.v1` | Role/Pack/DSH plugin 概念未在 UI 解释 | 通过现有 DSH extension 显示版本和能力 | 每个 Run 可见 Pack/Role/hash |
| 调度/主动报告 | 技术 canary 通过，常驻体验未验收 | official feeds、scheduler、research worker、Outbox | 无事件前采样，Inbox/通知未作为单一价值链验收 | 统一 event watch -> Run -> report -> recheck | 未来事件无需人工输入完整通过 |
| LoongSuite | 隔离技术 canary | `infra/dsh/observability`、ADR-0021 | 尚未关联用户 Run；它本身不补事实 | 只存 TelemetryRef 和聚合指标 | 可从 Run 打开 span，不复制账本 |
| 自进化 | 骨架存在，真实资产不足 | evolution worker、Outcome/Evaluation/Promotion | 缺有效 Forecast/Outcome/usefulness | 前瞻 Outcome + candidate/replay/holdout/shadow | 有 lineage 才允许 owner promotion |
| 前端 | 能显示 Trace/Gap，但不能表达事实准备度 | DSH client + Decision Desk Research | 工程状态多，用户价值状态不清晰 | DSH Inbox/Report/Data readiness；Desk 管理后台 | 不看日志可判断来源、缺口、成本、复查 |
| 成本 | 不完整 | DSH 有大量 Token，Hub 显示 known estimate 0 | 模型 usage 未映射到 Hub 总成本 | model/search/API/subscription 分项归集 | unknown 显示 partial，不显示假 0 |
| 交付门 | 判定过早 | 技术 Gate 通过但报告仍 research_only | 按组件验收而非按价值链验收 | PD-00..07 单一有限大目标 | 最终只有 promote/retain/stop |

## 3. 为什么改了多次仍然没有交付

这不是单一 bug，而是交付和验收方法发生了系统性偏差。

| 根因 | 过去的表现 | 造成的后果 | 本方案的纠正 |
|---|---|---|---|
| 把安全失败当产品完成 | PIT、Gate、错误码通过后写成 pilot ready | 用户看到的仍是大量 no_trade/不足 | 产品门必须包含真实事实充分度和用户价值 |
| 把搜索等同于事实覆盖 | 证明 web_search 被调用就继续下一阶段 | 精确行情和事件窗口仍不存在 | Search 只发现，typed provider/snapshot 才承载精确事实 |
| 只做通用质量校验 | authority/freshness/source count 通过 | 可能发生 requirement 语义替代 | 增加字段、单位、窗口、指标族语义校验 |
| 在事件后才开始采集 | 用最新快照分析旧事件 | 30m 反应不可重建 | 日历预热 + 事件窗口持久采样 |
| 按组件验收而非按价值链验收 | 调度、DSH、前端各自有测试 | 用户完整路径仍不可用 | 自动事件到报告、通知、Outcome 整链 BDD |
| “插件”概念混用 | DSH Plugin、Capability、Role、Pack、Telemetry 都叫插件 | 不清楚什么真正被安装和使用 | 继续执行第 5 节的明确分层 |
| 动态问题靠静态 Prompt 修复 | 每次异常后加限制或说明 | Prompt 膨胀，行为仍依赖缺失能力 | 契约、manifest、Provider、Gate 和评测承担确定性规则 |
| 状态文档过多且入口过时 | 多个“最终方案”并列 | 新会话容易读取旧目标 | 本文成为新的 proposed 交付阻断入口，接受后旧执行书转历史 |
| 未归集真实成本 | DSH 约 797K input/49.3K output，Hub 显示 `$0.0000 known estimate` | 无法判断单事件成本和可持续性 | Token/模型/工具/数据订阅统一投影 |

上一阶段不是毫无价值：它建立了 DSH Session、工具轨迹、可信 Evidence 和安全失败边界。但把“底座工程成功”写成“研究产品可以观察价值”过早，导致后续一直围绕显性错误打补丁，而不是补齐事实产品本身。

## 4. 最终产品形态

### 4.1 用户看到的产品

唯一日常入口仍是官方 DSH Web，不再新做第三个前端：

```text
DSH Web / Crypto Macro Trader Workspace
  -> 主动研究 Inbox
  -> 与某个自动 Run 关联的会话和轨迹
  -> 研究报告：事实、来源、根因链、反方、30m/24h/72h
  -> 数据充分度：已满足、待补、不可得、迟到事件
  -> 下一次复查和通知状态
  -> 需要时人工追问、补充或要求 recheck
```

Decision Desk 继续作为管理后台，不做第二个聊天产品：

```text
Decision Desk
  -> Sources/Capability/Provider health
  -> Run/Evidence/PIT/Cost/Ledger
  -> Outcome/Evaluation/Experience
  -> Promotion/Rollback/Backup
```

### 4.2 主动产品行为

```text
日历/官方 feed/新闻发现/人工文本
  -> Event admission + dedupe
  -> 事件前 baseline 采集（可预知事件）
  -> Durable research Run
  -> DSH Supervisor 读取 Pack、Role、当前事实和 gap
  -> 原生 web_search 发现文档/新闻 locator
  -> typed provider 获取精确市场事实
  -> web.fetch 验证被允许的正文
  -> requirement-specific semantic validator
  -> Gateway/PIT/independence
  -> gap-driven next round
  -> tradability/value Gate
  -> DSH Report + Inbox + notification
  -> T+30m/T+24h/T+72h recheck/outcome
  -> Evaluation/Experience/FailurePattern
```

用户可以继续一问一答，但人工提问只是入口之一，不再是产品运行的前提。

## 5. 架构边界：不重写 DSH，也不丢掉 Hub 资产

### 5.1 DSH 唯一拥有 Agent Harness

DSH 继续拥有：

- Agent Loop、Supervisor、Session、Trajectory、JSONL；
- Tool、Skill、Subagent、上下文压缩；
- 官方 Web 和 Native Plugin 生命周期；
- 当前步骤、工具调用和会话交互。

禁止在 LangGraph、Hub Worker 或 Domain Pack 中再写一套 ReAct/Supervisor/Reviewer loop。

### 5.2 LangGraph 只拥有产品外层生命周期

LangGraph 保留：

- durable Run/checkpoint/lease/recovery；
- 有界 round、deadline、成本和工具预算；
- DSH Session continuation；
- 确定性 Gate/commit/recheck 路由。

它不负责决定访问哪个网页、扮演交易员、调度多个金融角色或替代 DSH 的工具循环。

### 5.3 Hub 拥有可信业务资产

Hub 继续拥有：

- Event、Observation、Run；
- Fact/Evidence、PIT Snapshot、Artifact；
- Forecast、Outcome、Evaluation、Feedback；
- Outbox、通知、Promotion 和回滚。

DSH JSONL 记录“Agent 怎么运行”，Hub Ledger 记录“产品为什么运行、用了什么可信事实、产出了什么、后来是否正确”。两者通过 `event_id/run_id/dsh_session_id/trace_ref/artifact_id` 关联，互不替代。

### 5.4 五类扩展资产不能再混称插件

| 类型 | 当前例子 | 职责 | 是否 DSH Native Plugin |
|---|---|---|---|
| Product Extension | `decision.v1` | 结果契约、历史、Outcome/Evaluation | 可附带薄 bundle，但本体不是 |
| Domain Pack | `crypto_macro.v1` | 方法、requirement、Gate、评测 | 否 |
| Role Profile | `crypto_macro.manager.v1` | persona、工具白名单、预算、输出 | 作为 DSH preset/profile 被加载，不要求单独 npm 包 |
| Capability Plugin | `market.cross_asset` | 一个可替换的原子数据/工具能力 | 实现可来自 DSH plugin/MCP/Python/API |
| Telemetry Plugin | LoongSuite | LLM/Tool/Span/Token 技术观测 | 是，但不产生金融 Evidence |

当前 Decision Hub DSH extension 是连接官方 DSH Web 与 Hub 的薄 Host/Client plugin；金融 Provider 通过一个受控 Research MCP 工具暴露给 DSH，而不是每个 Python adapter 都复制成一个 npm 插件。

## 6. 事实契约：从“来源数量”升级为“语义正确”

### 6.1 新增通用 `FactEnvelope`

跨模块事实不能继续主要依靠 `excerpt` 中的一段 JSON。canonical schema 应增加通用、可 codegen 的结构化事实包，至少包含：

```text
fact_id
requirement_id
metric_family
instrument
venue
value + unit
window_start_at + window_end_at
event_offset
observed_at + received_at + published_at
source_id + independence_group
quality + delay_class
payload_schema_ref + payload_hash
```

原始 provider payload 仍不直接进入产品事实；adapter 生成规范化 `FactEnvelope`，Gateway 在入账前运行 Pydantic/Zod 和 manifest 规则。

### 6.2 每个 requirement 声明字段和窗口真值表

`source_manifest.yaml` 不再只声明 authority/freshness/source count，还必须声明：

- `accepted_metric_families`；
- `required_fields`；
- `required_event_offsets`；
- `unit_policy`；
- `minimum_venues` 或 `minimum_independence_groups`；
- `late_event_policy`；
- `primary/fallback capability`；
- `provider delay class`；
- `hard/soft` 和 confidence cap。

### 6.3 语义校验的确定性例子

| Requirement | 必须具备 | 不能替代 |
|---|---|---|
| `event_identity` | 官方 speaker/document/event time/revision | 新闻摘要中的模糊时间 |
| `policy_or_data_delta` | 当前原文/实际值 + prior/consensus，可计算 delta | 只说“偏鹰/偏鸽”的评论 |
| `expectation_pricing` | Fed funds/SOFR/OIS/FedWatch/2Y 等政策定价及事件窗口 delta | BTC funding/OI |
| `macro_transmission` | 至少 rates + USD 两类，含前后窗口 | 单条日频 FRED 最新值 |
| `cross_asset_confirmation` | VIX/NQ/ES/oil/credit 等独立风险资产窗口 | 同一来源重复 URL |
| `crypto_spot_confirmation` | BTC spot price、volume、event-window return；必要时 order book | 单个当前价 |
| `derivatives_crowding` | funding、OI 及 OI delta、basis；清算/long-short/taker flow 至少一项 | 只有 funding 当前值 |
| `counter_thesis` | 与主链相反且有独立 evidence refs | LLM 无证据的泛化风险提示 |

### 6.4 修复 task 与 gap 的显式关联

canonical `research_task` 必须增加 `requirement_id`。计划、Tool Call、Evidence、attempted capability 和 gap 只能通过明确 ID 关联，不再按数组序号猜测。

### 6.5 Gate 分为三层

```text
Generic Evidence Gate
  = schema/PIT/hash/authority/independence/conflict

Domain Semantic Gate
  = metric family/field/unit/window/venue/event age

Product Publish Gate
  = sufficient/research_only/reject + horizon/action/cost/value
```

通用 Kernel 不硬编码 BTC、FedWatch 或 DXY。Domain Pack 提供声明式规则，通用 validator engine 执行；只有真正无法声明的金融计算才放在 `crypto_macro` 的薄 evaluator 中。

## 7. 各 requirement 的最终数据能力

| Requirement | Primary | Fallback | 免费个人试点边界 | 正式交付边界 |
|---|---|---|---|---|
| Event identity | Fed/BLS/BEA/Treasury 官方 feed/document | DSH Search -> approved Fetch | 可用 | 可用 |
| Policy/data delta | 当前与前一官方文档/actual/prior/consensus | Reuters 等已批准正文 | 共识可能不完整 | 需稳定 consensus source |
| Expectation pricing | licensed Fed funds/SOFR/OIS/FedWatch adapter | 2Y/front-end futures proxy + verified report | 只能 research-only 或明确 proxy | 必须有事件窗口精确定价 |
| Macro transmission | intraday 2Y/10Y/DXY/VIX/NQ/ES/oil provider | 受标记 delayed proxy | 无法承诺 30m 可交易 | 必须 licensed/reliable intraday |
| Crypto spot | OKX primary + Binance/Bybit fallback | CoinEx/Kraken/Coinbase spot sanity | 公共 API 可用 | 至少双 venue + event window |
| Crypto derivatives | OKX/Binance/Bybit + Deribit options | CoinEx + audited aggregator | 可覆盖基础 funding/OI/basis | 需 OI delta、book、flow/crowding 独立源 |
| Breaking context | DSH native Search | Tavily rotated-key fallback | locator 可用 | 仍只负责 discovery |
| Official/news body | typed official document + approved web.fetch | owner-reviewed domain admission | 可用 | 需 license/retention 台账 |

关键商业结论：如果产品承诺 Fed 讲话后的可靠 30 分钟宏观传导分析，就需要一个合法、稳定、分钟级的宏观/利率数据源。零成本网页搜索不能保证这项承诺。免费路径可以做个人 `research_only` 试点，但必须在页面明确 `delayed/proxy/no_baseline`，不能通过降低 Gate 掩盖差距。

Provider 通过 `macro.expectation_pricing`、`macro.cross_asset_intraday` 等 capability contract 接入。未来更换 Databento、Trading Economics、CME 授权服务或其他供应商时只替换 adapter/manifest，不修改 DSH、Graph、Core 或前端业务契约。具体供应商必须在实施前用同一份 capability acceptance 做价格、授权、覆盖和延迟 bake-off，不能先把供应商私有字段写进 Core。

## 8. WebSearch 与来源注册表

### 8.1 最终路由

```text
DSH native web_search
  -> primary discovery

Tavily
  -> primary 无结果/失败/需要独立搜索索引时的显式 fallback

web.fetch
  -> 只抓 approved source class

typed provider
  -> 精确市场、日历和官方结构化事实
```

同一轮不无条件重复调用 DSH native Search 和 Hub native Search。Tavily key 曾在聊天暴露，必须轮换后才能 live canary；旧 key 不进入仓库、日志或文档。

### 8.2 来源分级

| 等级 | 例子 | 可否自动入 Evidence | 用途 |
|---|---|---|---|
| P0 Primary | Fed/BLS/BEA/Treasury/交易所官方 | schema/时间通过后可以 | 事件、actual、交易所事实 |
| P1 Reputable | Reuters/AP/CME 等已审计来源 | Fetch + license + timestamp 通过后可以 | 独立确认、共识、背景 |
| P2 Audited aggregator | 获批准的行情/衍生品聚合器 | typed adapter 通过后可以 | 跨 venue、清算、crowding |
| P3 Discovery only | 搜索结果和一般媒体 | 不可以 | 找 locator、发现事件 |
| P4 Untrusted | 社媒、论坛、未知转载 | 不可以 | 只作为未确认 scenario |

### 8.3 动态域名处理

不能继续在 Prompt 中手工塞域名，也不能把 `allowed_domains=[]` 解释成全网可信。实现一个受版本控制的 source registry：

- source class、authority、publisher group、license、retention、allowed paths；
- 是否允许 search/fetch/evidence；
- 时间戳提取和正文 parser；
- 健康、失败率、最后成功时间；
- owner approve/disable 记录。

搜索发现未知域名时，只创建 `source_candidate`。只有进入 registry 并通过 replay/live canary 后，才允许 Fetch 结果进入 Evidence。

## 9. 事件窗口与历史快照

### 9.1 可预知事件

日历中的 FOMC、CPI、PCE、NFP、Fed 官员讲话按以下计划采集：

```text
T-30m  baseline
T-5m   pre-event positioning
T0     event timestamp
T+1m   first reaction
T+5m   immediate confirmation
T+30m  short-horizon outcome
T+24h  persistence/outcome
T+72h  medium-horizon outcome
```

不是每个时间点都让 LLM 重跑。采集器保存 typed facts；只有事件发生、关键 delta 超阈值或 recheck 到期时才创建/继续 DSH Run，避免浪费模型 Token。

### 9.2 突发事件

战争、制裁、交易所事故等无法提前采集 T-30m：

- 以可信首次发现时间作为 `T_detected`；
- 尽可能从已持续采样的市场 ring buffer 获取前置基线；
- 如果没有基线，30m 状态为 `baseline_unavailable`；
- 允许生成“从发现时起”的研究报告，不允许声称事件发生前后的精确因果变化。

### 9.3 迟到事件

当 `research_started_at - event_at > horizon` 且没有对应快照：

- 该 horizon 不产生方向性判断；
- 页面显示 `retrospective_only/no_event_window`；
- 可继续做 24h/72h 或历史复盘；
- 不通过抓取当前值伪造过去窗口。

## 10. 调度、主动报告和通知

### 10.1 当前代码可复用的部分

- `official_source_presets()`：Fed/BLS/BEA feed 和 BLS calendar；
- `SourceIngestionService`：dedupe/revision/admission；
- `CryptoMacroDiscoveryPolicy`：高影响词准入；
- `RealtimeScheduler`：source poll、Outcome、notification；
- `DurableResearchWorker`：领取 `research.v1` Run；
- `Outbox`：幂等通知；
- child recheck：到期继续研究。

这些保留，不引入 Temporal/DBOS/Redis。

### 10.2 必须修正的调度行为

1. 日历默认在产品 profile 中显式启用并展示健康状态，不能只靠隐藏环境变量。
2. 可预知事件先创建 `event_watch` 和 snapshot schedule，不等官方 feed 到达后才采集。
3. 官方 feed、calendar 和新闻 discovery 汇入同一 Event identity/dedupe，不重复触发多个昂贵 Run。
4. 关键事件窗口内采用有界新闻 discovery；平时不做无差别全网轮询。
5. 数据采集和 Agent 研究分离：廉价 typed sampler 常驻，昂贵 DSH Run 按事件/阈值触发。
6. 报告生成后写 Inbox 和 Outbox；30m/24h/72h 到期自动 recheck/outcome。
7. 手工输入、ASR 文本和自动来源最终都只进入统一 `TextEnvelope/Event admission`，不复制研究链。

### 10.3 建议默认频率

| 状态 | 官方 feed/calendar | 新闻 discovery | 市场 snapshot |
|---|---:|---:|---:|
| 平时 | 60s / 15m | 不持续 broad search | 5m 轻量基线 |
| 已知事件 T-30m..T+30m | 30-60s | 2-5m 有界检索 | 1m，关键点额外采样 |
| 突发高影响事件 | 30-60s | 1-2m，达到预算即停止 | 1m |
| T+30m..T+72h | 正常 | recheck 时检索 | 5-15m 或到期采样 |

频率是 Pack policy，不写死在 Graph；费用/限流达到阈值时可降级，但必须可见。

## 11. 交易员 Role、前端和可观测性

### 11.1 交易员 Role 必须真实可见

当前 `crypto_macro.manager.v1` 进入了 Research Request，但没有被清晰展示为 DSH 工作区中的已加载业务角色。修复后每个正式 Run 必须显示：

```text
Product Extension: decision.v1
Domain Pack: crypto_macro.v1@<hash>
Role Profile: crypto_macro.manager.v1@<hash>
Harness: DSH <version/commit>
Runtime/Profile: <id/hash>
Capabilities: enabled + attempted + failed
Evidence Policy/Gate: <version/hash>
```

Role Profile 通过现有 DSH preset/plugin seam 投影，不复制一套角色后端。Manager 可以在 DSH 内调用 Search、Research MCP、Subagent/Skill；正式 Evidence 仍由 Hub Gateway 接受。

### 11.2 DSH 主页面

用户默认看人类可读信息，不看原始 JSON：

- `Inbox`：系统主动发现并完成/进行中的高影响事件；
- `Report`：事实摘要、变化量、主/反根因链、周期判断和下一复查；
- `Data readiness`：每个 requirement 的 `ready/delayed/stale/missing/denied/no_baseline`；
- `Sources`：标题、publisher、时间、权威性和可点击原文；
- `Run`：轮次、工具、失败、预算和停止原因；
- `Ask/recheck`：继续追问、人工补充、立即复查；
- `Notification`：是否已经发送、渠道和下一次计划。

原始 JSONL 仍由 DSH 提供下载/深度复盘，但不作为默认产品页面。

### 11.3 Decision Desk 管理后台

补齐但不重复 DSH 的内容：

- capability/provider/source health；
- 事件窗口 snapshot coverage；
- 各 requirement 的语义通过/拒绝原因；
- 单 Run Token、模型费、Search/API 费和总成本；
- Outcome/Brier/方向/人工 usefulness；
- Experience/FailurePattern/Candidate/Promotion。

### 11.4 LoongSuite 不重复 Hub Trace

LoongSuite 保持可选技术观测插件：

- 记录 DSH Session/Turn/Agent/LLM/Tool span、延迟、Token 和错误；
- Hub 只保存 `TelemetryRef(trace_id/backend)` 和聚合指标；
- DSH 页面或 Desk 提供“打开技术轨迹”链接；
- 不复制所有 span 到 Hub 表；
- 不把 span 当 Evidence，也不让 LoongSuite 决定 Gate。

## 12. 成本、密钥和外部依赖

### 12.1 成本必须如实归集

每个 Run 至少显示：

```text
model input/output/cache tokens
model provider billed/estimated cost
native search calls and estimate
Tavily/other search credits
market data subscription allocation
typed provider request count
total known + unknown cost
```

只要有一项未知，就不能显示 `$0.0000 total`，必须显示 `partial/unknown`。

### 12.2 免费与正式两条路线

**个人免费/低成本试点**：

- DSH native Search；
- 官方 feed/document；
- OKX/Binance/Bybit/Deribit/CoinEx 可用公共接口；
- Tavily 只在轮换 key 后按需 fallback；
- delayed/proxy 宏观数据明确降级；
- 不承诺 Fed 事件 30m 可交易结论。

**正式交付路线（推荐）**：

- 增加一个通过 bake-off 的合法 intraday macro/rates provider；
- 若要全市场清算/拥挤度，再增加一个 audited derivatives aggregator；
- Source/Provider license、数据保留和展示权限进入 manifest；
- 供应商不可用时按 capability contract 切 fallback，不能修改 Core。

### 12.3 owner 需要提供什么

开始实现前不需要再提供新的 LLM key。真正影响产品等级的是：

1. 是否接受“免费试点不承诺 30m 宏观可交易”的诚实边界；
2. 若要求正式 30m 交付，允许的数据订阅预算；
3. Tavily 旧 key 是否已轮换，只有需要 fallback live canary 时才使用新 key。

所有 secret 只进入 gitignored 本机 secret store，不能进入 Markdown、fixture、日志、截图或 Git。

## 13. 自进化如何变成真实资产

当前 Evolution 骨架保留，但没有 Outcome/Evaluation 的候选不能晋升。

### 13.1 每次 Run 沉淀的资产

- Event + event family；
- 事件窗口 Fact/Evidence/PIT Snapshot；
- DSH Session/Trajectory 引用和版本；
- CausalCase、Counter-thesis、Horizon；
- source/capability attempts、失败和成本；
- 30m/24h/72h Outcome；
- owner usefulness/人工修改；
- FailurePattern 和候选改进。

### 13.2 研究不足也要能评测

`research_only` 没有方向性 Forecast 时，仍应记录：

- 事实覆盖率和语义拒绝率；
- source/provider success/freshness；
- 从事件到第一份报告的延迟；
- 自动关闭 gap 的比例；
- owner 手工补证时间；
- 单事件成本。

不能因为 no_trade 没有 Brier 就让这次 Run 对改进毫无数据价值。

### 13.3 Promotion 边界

```text
Failure/Outcome/Feedback
  -> candidate policy/provider/profile/doctrine
  -> replay
  -> holdout
  -> prospective shadow
  -> owner review
  -> promote/retain/stop
```

Agent 可以提出来源、查询、角色或 policy candidate，但不能自动修改 Gate、安装未知插件、开通费用、写历史账本或切 active pointer。

## 14. 目标代码结构

以下是在现有仓库上增量收敛，不创建新微服务、不复制 DSH：

```text
contracts/schemas/
  agentic_research.schema.yaml       # research_task.requirement_id 等通用契约
  research_fact.schema.yaml          # FactEnvelope/metric/window/source lineage

packs/crypto_macro/
  evidence/
    source_manifest.yaml             # requirement 字段/窗口/来源真值表
    source_registry.yaml             # P0..P4、license、independence group
  tools/
    bindings.yaml                    # capability -> adapter，不含 secret
  profiles/
    manager.yaml                     # Role/预算/允许能力
  gates/
    fact_semantics.yaml              # 声明式语义门
  evaluations/
    fact_sufficiency/                # replay/holdout/late-event/semantic-substitution

packages/kernel/decision_hub_kernel/
  application/
    research_evidence.py             # 通用 attestation，不写金融字段
    fact_store.py                    # 通用 FactEnvelope 持久化/查询
    scheduler.py                     # durable schedule/outbox，保持通用
  decision/
    sufficiency.py                   # 通用 Gate + 注入声明式语义结果

packages/provider_adapters/
  market/
    crypto_multi_venue.py            # OKX/Binance/Bybit/CoinEx 规范化
    crypto_crowding.py               # 清算/long-short/flow，可选 aggregator
  macro_market/
    intraday.py                      # capability port，供应商私有实现隔离
    expectation_pricing.py           # Fed funds/SOFR/OIS/FedWatch 规范化
  research/
    semantic_validation.py           # 通用 manifest validator engine
  search/
    dsh_native.py                    # 保留 primary
    tavily.py                        # 保留显式 fallback

packages/source_adapters/
  official_feeds/                    # 保留
  event_calendar/                    # 可预知事件与 watch schedule
  news_discovery/                    # 有界 locator，不直接写金融 Evidence
  transcript_meeting_copilot/        # 只保留未来 TextEnvelope 入口

packages/orchestration/langgraph/
  graphs/agentic_research_graph.py   # 只接语义 Gate、budget、continuation

apps/hub_worker/
  composition.py                     # 注入 sampler/provider/policy
  market_sampler.py                  # 单机常驻、廉价 typed snapshot

extensions/dsh/decision-hub/
  src/client/                        # Inbox/Report/Data readiness/版本投影
  src/host/                          # 继续走公开 Hub/DSH 契约

apps/decision-desk/src/
  research/                          # 管理报告，收敛 raw trace
  features/sources/                  # source/provider health
  features/evaluation/               # Outcome/value/成本
```

约束：

1. 先改 canonical schema，再 codegen Python/TypeScript/Zod；禁止双写 DTO。
2. Kernel 不 import DSH/LangGraph/Provider/crypto 字段。
3. Provider 类型不泄漏到 Core、Graph、Prompt 或前端。
4. Domain Pack 不直接写数据库；Gateway/Store 是唯一写入边界。
5. DSH Native Search、Tavily、typed provider 都经 CapabilityManifest。
6. 不删除历史 migration、Run、Evidence、Artifact、Forecast 或 Outcome。
7. 不复制 DSH Web，不 fork 上游业务逻辑；继续使用锁定上游 + 本地 extension/overlay。
8. 每个受影响模块的 README、Stage 执行记录和 `CHANGELOG.md` 同步维护。

## 15. 有限实施阶段与 Checklist

整个修复只有一个大目标：

```text
PD：完成“自动高影响事件 -> 语义正确事实 -> DSH 主动研究 ->
人类可读报告/通知 -> 30m/24h/72h Outcome”的单 owner 产品闭环。
```

不得把下面每个小阶段单独宣传为产品完成。

### PD-00：事实真值表与语义 Gate（交付阻断，必须第一步）

- [x] 先以 accepted-before-cancel BDD 拆分 Host 提交前/接受后超时，修复当前唯一测试红灯并恢复全量绿色基线；
- [x] 给 `research_task` 增加显式 `requirement_id`；
- [x] 定义 `FactEnvelope` canonical schema 和 codegen；
- [x] 为八类 requirement 定义字段、单位、窗口和 independence group；
- [x] 增加 semantic substitution 失败样本；
- [x] BTC derivatives 不能满足 Fed expectation pricing；
- [x] 当前值不能满足事件窗口 delta；
- [x] `83.33%` 等 coverage 只由语义通过的 Evidence 计算；
- [x] 旧 Evidence/Run 可读，不回写历史。

退出门：任何错误 requirement、错误窗口或错误指标族都 fail-closed，且失败原因能在页面读懂。

### PD-01：事件窗口采集与迟到事件策略

- [x] 日历创建 durable event watch；
- [x] 持久化 `T-30m/T-5m/T0/T+1m/T+5m/T+30m/T+24h/T+72h`；
- [x] sampler 重启、重复 tick、跨时区和夏令时测试；
- [x] 突发事件使用 ring buffer 或明确 `baseline_unavailable`；
- [x] 迟到事件不能生成伪 30m 判断；
- [x] snapshot 与 Run/Event/Provider version 可追溯。

退出门：对一个未来测试事件，能在事件发生前后自动形成可比较、无未来泄漏的窗口。

### PD-02：Typed Provider Pack

- [x] 多 venue crypto spot/derivatives；
- [x] OI 1h/4h/24h delta、funding、basis、mark/index（离线事件窗口语义；实时 licensed provider 仍待）；
- [x] 订单簿/flow/crowding 至少一个可解释 proxy（独立 licensed source 仍待 bake-off）；
- [x] intraday rates/USD/cross-asset capability seam 与离线语义链（真实 provider blocked）；
- [x] expectation-pricing capability seam 与离线语义链（真实 provider blocked）；
- [x] primary/fallback、timeout、rate-limit、health、cost；
- [x] 免费 proxy 与 licensed live 明确区分；
- [x] Provider 替换不改 Core/Graph/UI contract。

退出门：每个 hard requirement 至少有一个经过真实 canary 的 primary；需要独立来源的 requirement 有可用 fallback。

### PD-03：Search -> Source Registry -> Attestation

- [x] DSH native Search primary 保留；
- [ ] Tavily rotated-key fallback 独立 canary（密钥未读取，仍是外部授权门）；
- [x] P0..P4 source registry；
- [x] 未知域名只能产生 source candidate；
- [x] Fetch redirect/body/timestamp/license/parser 测试；
- [x] locator、正文、精确 market fact 在 UI 中明确区分；
- [x] 搜索结果不能独自关闭结构化 market gap。

退出门：真实搜索可以找到并验证官方/已批准正文；未批准来源不会进入 hard Evidence。

### PD-04：主动调度、Inbox、报告和通知

- [x] calendar/feed/news/manual 汇入统一 Event；
- [x] 事件前 baseline 自动启动；
- [x] research worker 自动创建/恢复 DSH Session；
- [x] gap 有未尝试能力时继续，全部耗尽才 bounded stop；
- [x] DSH Inbox 显示主动报告，不要求用户先提问；
- [x] local/email 通知按 outbox dedupe key 实现 at-least-once delivery + effective-once；
- [x] 30m/24h/72h recheck 自动安排；
- [x] 重启/网络失败/重复事件全链恢复。

退出门：一个未来官方事件无需人工输入，从 watch 到报告、通知和 recheck 全自动完成。

### PD-05：Role、可观测性和成本产品化

- [x] DSH 页面显示 Pack/Role/Runtime/Capability/Gate 版本；
- [x] requirement readiness 是人类可读状态，不默认展开 JSON；
- [x] source attempts、拒绝、stale、no-baseline 可见；
- [x] DSH Token 与 Hub tool/provider cost 统一归集；
- [x] unknown cost 不显示为 0；
- [x] LoongSuite trace ref 可从 Run 追溯，缺失时显示 unavailable；
- [x] Decision Desk 不复制 DSH Chat/Trajectory。

退出门：owner 不看日志即可判断系统用了什么角色、搜了什么、为何仍缺、花了多少和何时复查。

### PD-06：Outcome、Evaluation 和受控进化

- [x] 自动记录 30m/24h/72h Outcome；
- [x] research_only 也记录 coverage/latency/cost/usefulness；
- [x] Brier、方向、MFE/MAE、费用后结果按有效 Forecast 计算；
- [x] 反复缺口/失败生成 candidate，而不是自动打补丁；
- [x] replay/holdout/shadow/owner review；
- [x] 任何自动 Promotion、自动 Gate 修改测试必须失败。

退出门：一次真实 Run 能从事件一直追溯到 Outcome/Evaluation/Experience candidate。

### PD-07：真实交付验收和停止线

- [ ] 至少覆盖 central-bank speech、macro release、breaking event 三类；
- [ ] 至少 14 天或 20 个前瞻高影响事件；
- [ ] PIT/future leakage 为 0；
- [ ] semantic substitution 为 0；
- [ ] 自动 event-to-report 成功率不低于 90%；
- [ ] 可预知事件的 baseline coverage 为 100%；
- [ ] 每个 Run 的真实成本为 known 或明确 partial，绝不假 0；
- [ ] 报告引用、反方、缺口、下一复查对 owner 可读；
- [ ] owner usefulness 与人工查证时间有记录；
- [ ] 产出 `promote / retain / stop` 唯一结论。

只有 PD-00..06 全部通过后，才开始 PD-07 的 14 天/20 事件观察。未通过时不能靠延长观察时间掩盖基础能力缺失。

## 16. BDD/TDD/验收矩阵

### 16.1 必须先写的 BDD

```gherkin
Scenario: WebSearch 找到网页但没有精确行情
  Given DSH 找到一条关于收益率的新闻 locator
  And 没有分钟级 rates FactEnvelope
  When 评估 macro_transmission
  Then locator 不能关闭该 hard gap
  And Agent 继续尝试 typed macro capability

Scenario: BTC 衍生品不能替代 Fed 定价
  Given 有新鲜 BTC funding/OI/basis
  And 没有 Fed funds/SOFR/OIS/2Y 定价窗口
  When 评估 expectation_pricing
  Then requirement 保持 insufficient
  And 页面显示 metric_family_mismatch

Scenario: 可预知讲话自动形成 30m 报告
  Given calendar 在 T-30m 创建 watch
  And sampler 保存 pre/post facts
  When 官方讲话到达
  Then 无需用户提问创建 DSH Run
  And 报告引用事件前后事实
  And 通知只发送一次

Scenario: 迟到事件没有历史基线
  Given 事件已过去 11 小时
  And 没有 T-30m/T+30m snapshot
  When 用户要求 30m 分析
  Then 系统标记 retrospective_only
  And 不使用当前值伪造事件窗口

Scenario: Provider 失败后使用未尝试 fallback
  Given primary provider timeout
  And manifest 有已审计 fallback
  When 当前预算仍可用
  Then 同一 DSH Session 继续
  And primary/fallback 的错误、成本和 Evidence 分别记录

Scenario: 成本只有部分已知
  Given DSH 有 token usage
  And data subscription allocation unknown
  When 页面显示 Run cost
  Then 状态是 partial
  And 不显示 total cost 为 0
```

### 16.2 TDD 层级

| 层级 | 测试内容 |
|---|---|
| Contract | schema/codegen、旧 payload compatibility、禁止裸 dict/any |
| Pack | requirement truth table、source registry、late-event policy |
| Provider | fixture parsing、单位/时间归一化、限流/timeout/partial failure |
| Gateway | PIT/hash/domain/authority/semantic/independence |
| Graph | continuation、budget、retry、checkpoint、no third Agent Loop |
| Worker | watch/sampler/restart/idempotency/outbox/recheck |
| DSH Plugin | Inbox/Role/readiness/report/terminal failure |
| Decision Desk | source health/cost/outcome/无 raw JSON 默认页 |
| Replay | stale、冲突、semantic substitution、missing baseline |
| Live canary | official + macro + crypto + Search + notification |
| Browser E2E | 桌面/移动、console、文本不溢出、状态一致 |

### 16.3 每张任务卡的完成证据

每张任务卡必须在 `docs/evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md` 追加：

- scope/非目标；
- Red test；
- 代码位置；
- 命令和结果；
- live/replay 样本 ID；
- 页面截图/hash（有 UI 时）；
- 新发现问题和根因；
- 未完成项；
- `continue / retain / stop` 结论。

禁止只写“tests passed”或只更新聊天。

## 17. 交付等级与有限终点

| 等级 | 条件 | 用户能做什么 |
|---|---|---|
| Engineering demo | DSH/Hub/Loop/Gate 可运行 | 查看技术链，不用于市场判断 |
| Research-only pilot | 搜索和部分 typed facts，可解释 bounded stop | 辅助查资料，owner 仍需大量核实 |
| Personal usable | 语义 Gate、事件窗口、核心 providers、主动报告通过 | 单 owner 日常研究，禁止自动交易 |
| Product deliverable | PD-07 前瞻指标、成本、Outcome、usefulness 通过 | 对外试点和稳定升级 |
| Trading automation | 另立合规、风控、执行和资金权限项目 | 本方案不授权 |

当前状态是 `Research-only pilot`，工程/运行闭环已通过，但用户价值仍未由前瞻样本证明。最近一次
页面“证据不足”不是新 bug，而是历史回溯窗口和正式分钟级 Provider 缺口的真实暴露。

本轮的有限终点不是“无限继续开发”，而是：

```text
PD-07 完成 -> owner 做 promote/retain/stop

promote: 进入个人正式使用/外部小范围试点
retain: 保留研究工具，不继续承诺交易判断
stop: 冻结产品，保留 DSH/Hub/Pack/Evidence/Eval 资产
```

ASR、PPT、第二领域和多用户都不能自动排在 PD 后面。只有当前产品达到 Personal usable 且出现第二个真实需求时，才单独立项。

## 18. Owner 决策门与当前执行入口

Owner 已确认：

1. 接受本文作为当前交付阻断方案，暂停把 G2-AF-05/E3 观察解释为产品价值验收；
2. 接受 DSH-first 边界不变：不重写 DSH、不新增第三套 Agent Loop；
3. 接受 `PD-00` 先修语义正确性，不能先放宽 Gate 获得好看报告；
4. 选择产品等级：先完成免费/低成本 Personal pilot，或同时批准 intraday macro/rates provider 的预算 bake-off；
5. Tavily fallback 如需 live 验收，先轮换已暴露 key。

PD-00..06 已完成后的唯一下一步是：

```text
PD-07：冻结首个 prospective cohort，在未来事件前创建 EventWatch，
开始至少 14 天或 20 个合格事件的只读观察；到期只做
promote / retain_baseline / stop 决策。
```

执行合同、样本准入、指标和停止线见
[PD-07 前瞻价值观察阶段卡](../stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)。观察未完成前不新增
第二领域、第二套 Agent Loop 或自动交易；真实 Provider bake-off 必须单独记录成本/授权和 cohort
版本边界。

## 19. 依据

- [Agentic 主动研究缺口复盘](../evaluations/PRODUCT_AGENTIC_GAP_REVIEW_2026-09-03.md)
- [G2-AF 主动事实获取与自主研究阶段方案](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)
- [G2-AF 实施执行记录](../evaluations/G2_AF_IMPLEMENTATION_EXECUTION_LOG_2026-09-04.md)
- [DSH 与 Decision Hub 系统总装设计](DSH_HUB_SYSTEM_ASSEMBLY.md)
- [研究智能体主体产品规格](RESEARCH_AGENT_PRODUCT_SPEC.md)
- [资产沉淀与可插拔扩展模型](../platform/ASSET_AND_EXTENSION_MODEL.md)
- [Crypto Macro Domain Pack 设计](../domains/crypto_macro/README.md)
- [ADR-0010 模型语义与可信运行账本边界](../decisions/ADR-0010-model-semantics-runtime-ledger-boundary.md)
- [ADR-0011 研究失败与事实覆盖边界](../decisions/ADR-0011-research-reliability-fact-boundary.md)
- [ADR-0012 DSH-first 产品重新收口](../decisions/ADR-0012-dsh-first-product-rebaseline.md)
- [ADR-0022 Search Provider 路由边界](../decisions/ADR-0022-search-provider-route-boundary.md)
- `packages/kernel/decision_hub_kernel/decision/sufficiency.py`
- `packages/orchestration/langgraph/graphs/agentic_research_graph.py`
- `packs/crypto_macro/evidence/source_manifest.yaml`
- `packs/crypto_macro/tools/bindings.yaml`
