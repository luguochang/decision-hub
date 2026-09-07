# G2-AF 主动事实获取与自主研究阶段方案

版本：`G2-AF-2026-09-04.v2`  
状态：`G2-AF-01..04 technical gates passed / personal research-only pilot / G2-AF-05 prospective observation pending`  
适用范围：`crypto_macro.v1` 的真实来源补证、DSH 主动研究循环、单机调度与个人研究资产沉淀。  
不适用范围：自动交易、多用户、第二领域、ASR、公共插件市场和远程高可用。

> 这是一份阶段 Charter，不是代码任务清单。它先解决当前产品“发现 hard gap 后很快停止、没有主动搜索、看起来像问答助手”的根因，再进入有限的真实观察。Tavily key 已由 owner 提供，但在本文件和预算门确认前只登记为“已收到、未读取、未持久化、未启用”；不扩大 live allowlist，不改变 active runtime pointer。

`G2-AF` 是既有 G2“来源与能力覆盖”到既有 G3“前瞻价值观察”的桥接阶段；它不是另一个 G3，
也不改变 `PRODUCT_COMPLETION_AND_FUTURE_PLAN.md` 中 G3 的含义。G2-AF-05 完成后才进入既有
E3/G3 观察窗口。

2026-09-03 已完成一次授权的 DSH 原生 Search route 最小诊断：HTTP 200，返回结构化
`web_search_tool_result` 和 10 个来源域名。该探针仅证明官方 Search route 基本可达；没有把结果
写入 Hub Ledger，也没有证明 DSH Web preset 到 Hub 的 attestation bridge、P0 来源质量或实时
金融事实覆盖。Tavily key 已收到但仍未读取/持久化，免费额度只作为成本优先的后备选项。

## 1. 决策摘要

### 1.1 产品必须达到的行为

产品不是“一次 LLM 调用后给一段 no_trade”，而是：

```text
事件/日历/人工文本到达
  -> 创建 durable research Run
  -> DSH Supervisor 读取领域 hard requirements 和能力目录
  -> 发现缺口，选择尚未尝试的 primary/fallback capability
  -> Search 发现候选来源，Fetch/Typed Provider 验证原文和数值
  -> Gateway 做权限、PIT、hash、freshness、独立来源校验
  -> 新证据进入 Hub Ledger，重新计算 Coverage
  -> sufficient：生成带引用的报告并通知
  -> 仍不足：继续下一轮；无可用能力/预算/时间耗尽：解释性 bounded failure
  -> 到期记录 Outcome/Evaluation，形成可复盘资产
```

“信息不足”不再是默认停止条件，而是一个需要继续处理的 hard gap 状态。只有以下任一条件成立才允许停止：

1. 关键 requirement 已满足 authority、freshness、PIT 和独立来源门；
2. 所有批准的 primary/fallback capability 均已尝试且没有可用替代；
3. Run deadline、工具调用预算或费用预算耗尽；
4. 权限、PIT、来源冲突或安全策略明确拒绝继续。

无论哪一种停止，报告都必须列出已尝试的能力、失败原因、已保留证据、未关闭的 hard gap 和复查条件。不得用模型记忆或搜索摘要填充缺口。

### 1.2 本轮执行目标（G2-AF-03 收口到 G2-AF-04 的可验证闭环）

本轮只推进一条用户可观察主线：

```text
官方 DSH Web 会话
  -> DSH 原生 web_search（发现 locator）
  -> decision_hub_research(web.fetch / typed provider)
  -> Hub Gateway（域名、PIT、authority、hash、独立来源）
  -> Coverage 重新评估
  -> 同一 DSH Session 的下一轮或有界停止
  -> 轨迹/报告/失败原因可见
```

实现约束：

- `tool-web` 由官方 DSH base bundle 提供，本项目 preset 只覆盖 `fetch=true`、查询/输出上限和超时；不复制 DSH 的搜索、抓取或 Agent Loop。
- DSH 原生搜索和抓取结果只留在 DSH JSONL/Trajectory，不能直接满足金融 hard requirement；只有 Hub `decision_hub_research` 返回并被 Gateway 接受的 `EvidenceCandidate` 才可进入业务账本。
- `web.fetch` 是受审计的只读 HTTP adapter，只允许 `bindings.yaml` 的官方机构/交易所域名；跨域页面、登录页、重定向或无正文都产生结构化失败，不回退成模型摘要。
- Search/Fetch 的 provider 失败、无来源、预算耗尽和权限拒绝必须保留在 Trace，并由 LangGraph 的 capability ladder 决定继续、回退或 bounded stop。
- 本轮不启用聊天中暴露的 Tavily key；Tavily 只有在轮换 secret、显式 allowlist 和只读预算通过后才进入独立 canary。

### 1.3 最终架构立场

- **DSH 是主要 Harness/runtime**：继续复用官方 Web、Session、Trajectory、JSONL、Tool、Skill、Subagent 和 Agent Loop；不复制 DSH 源码。
- **Decision Hub 是产品控制面**：拥有 Event、PIT Snapshot、Run、Evidence、Gate、Artifact、Forecast/Outcome、Evaluation、通知和个人资产；不把 DSH JSONL 当业务账本。
- **LangGraph 只管理产品生命周期**：Admission、Round、checkpoint、lease、恢复和确定性 Gate；不再实现第二套 ReAct/Agent loop。
- **Capability Plugin 是可替换能力**：搜索、抓取、官方文件、行情和衍生品都通过公开的 Capability contract 暴露给 DSH；供应商可替换，Hub 契约不变。
- **Domain Pack 是业务方法**：`crypto_macro` 保留根因链、事实要求、来源优先级、角色和金融 Gate；以后 PPT/A 股/美股使用独立 Pack，不把 BTC 字段扩散到 Core。
- **LoongSuite 是技术观测插件**：只记录 Session/Turn/Step/LLM/Tool span、耗时、Token、错误和重试；不负责搜索、金融事实、角色或自进化。

这不是“DSH 或 LangGraph 二选一”：DSH 执行 agentic loop，LangGraph 保护产品生命周期，Hub 保护事实和发布边界。任何实现如果需要第三套 loop、第二套账本或前端直读 DSH raw JSON，必须停止并回到 ADR。

## 2. 当前问题与根因

| 问题 | 事实 | 根因 | 本阶段修复 |
|---|---|---|---|
| hard gap 出现后立即停止 | 既有 live profile 只把 Hub 的三个 typed capability 暴露给研究工具 | allowlist 没有可用的 discovery fallback，也没有 gap-to-capability continuation | 能力目录 + 未尝试能力选择 + bounded continuation |
| DSH key 看似有搜索但产品没有搜索 | 锁定的官方 DSH base 确实包含 `dsh-web`、`dsh-web-search-deepseek` 和原生 `web_search`；当前 `decision-research` preset 已开启官方 `tool-web`，并保留 Hub `decision-hub-research-tool` 作为业务能力入口 | DSH 原生工具结果不能绕过 Hub Evidence Ledger；模型 key、DSH tool、Hub capability 是三个边界 | 先做 DSH 原生搜索 capability probe；只有通过结果 attestation 的薄桥接才可进入主链，Tavily 作为独立 Hub provider fallback |
| 搜索摘要不能满足金融事实 | Search 结果没有可证明的官方数值、历史截面或交易所时间 | 搜索被误当成权威数据源 | Search 只发现；`web.fetch`/typed provider 验证后才入账 |
| 用户看到的是 no_trade | 失败安全链路已存在，但没有把“继续补证”作为产品价值门 | 安全失败完成了，事实获取能力未完成 | 继续轮次、来源回退、失败原因和下一步在 DSH/Desk 可见 |
| 调度不是全网实时 | 当前 worker 主要轮询 Fed/BLS/BEA feed，未形成新闻搜索触发闭环 | 事件 admission、Search 和研究 worker 没按一条链验收 | feed/calendar 发现事件，触发一次有界研究；高影响窗口可配置新闻轮询 |
| 交易员角色看不见 | `crypto_macro.manager.v1` 参与请求和 Gate，但不是 DSH 可选 Native Role Plugin | Domain Pack、Role Profile、DSH preset 概念混淆 | 将 Role Profile 正式加载为可审计 preset，不复制业务逻辑 |
| 自进化没有明显产物 | Failure/Outcome/Evaluation -> candidate/replay/holdout/shadow 已有 | 没有真实 Outcome/owner feedback，不能安全 Promotion | 把搜索失败、来源质量、成本和 usefulness 纳入 Evolution 数据集；仍需人工晋级 |

## 3. 外部 Web Search 方案核查

核查日期：2026-09-03。价格和额度是供应商动态信息，正式采购前必须在控制台再次确认；本表只作为阶段选型依据，不把网页价格写入代码常量。

| 方案 | 已核查能力 | 费用/授权 | 与 DSH/Hub 的适配 | 结论 |
|---|---|---|---|---|
| **Tavily** | Search、Extract、Map、Crawl；官方 `tavily-mcp`，MIT；远程 MCP 或本地 npm；支持 basic/advanced、domain/time 过滤 | 免费 1,000 credits/月、无需信用卡；按量 `$0.008/credit`；文档列出月包 `$0.0075-$0.005/credit`；basic search 1 credit、advanced 2 credits | 有成熟 MCP 和 API，能把发现与原文抽取放在同一供应商；仍需 Hub 做 PIT/authority 验证 | **按需 fallback/独立交叉索引**。不与 DSH native 每次并发；首个外部 key 只保留 Tavily，成本和接入风险最低 |
| **Brave Search** | 官方 `brave-search-mcp-server` v2.1.3（MIT）；Web/News/Image/Video/LLM Context/Summarizer；freshness 过滤；stdio/HTTP | 需要独立 Brave API key；价格页面是动态控制台，本次无法稳定取得可引用的当前单价，采购前必须核价 | 适合作为第二索引和 News fallback；需要自己做内容 Fetch/来源验证 | **第二阶段回退**，不阻塞首个 canary |
| **Exa** | `exa-mcp-server`（MIT）；语义 Search、Crawl/Contents | 当前定价页显示注册 `$20` credits、每月 `$10` credits；Search 约 `$7/1k requests`，Contents 按页计费，需以控制台为准 | 研究/语义检索强，但金融实时新闻和官方原文仍需另做验证 | 质量候选，不作为首个低成本方案 |
| **Serper** | Google SERP API；页面显示 50k credits `$1/1k`，500k `$0.75/1k`，更大包最低约 `$0.30/1k`，credits 有效期 6 个月 | 需要独立 key；低单价，但需自写 API 适配和抓取/引用绑定；不要与 SerpApi MCP 混称 | 便宜但增加维护边界，没有本项目现成的官方 DSH 集成 | 只有搜索量很大时再评估 |
| **SearXNG 自建** | AGPL-3.0；聚合多个上游；`/search?format=json` API；可自建 | 无搜索供应商 key；成本是机器、上游限流/CAPTCHA、升级和运维；公共实例不可信 | 可做开发、灾备或低优先级 discovery；不能单独提供权威金融事实 | 作为可选灾备，不做唯一关键来源 |
| **OpenAI Responses web_search** | Responses `tools: [{type: web_search}]`；当前 adapter 已实现来源提取和引用绑定 | 需要真正支持该工具的 OpenAI-compatible Provider；不能把 DeepSeek 原生 Anthropic Search 与 Responses route 混用 | 当前中转站曾出现 `web.search` timeout；保持 candidate，不把它当默认搜索 | 不作为本阶段唯一依赖 |

### 3.1 DSH 原生 Web Search 核查（2026-09-03）

这项核查修正了此前“DeepSeek 文本 API 不等于 DSH Web Search”的过度简化。准确结论是：

1. **官方 DSH 有原生搜索能力。** 锁定上游源码的 `packages/web/web-search-deepseek` 注册
   `deepseek-official` provider，`packages/bundle/base/cordis.patch.yml` 将它与
   `dsh-web`、`dsh-tool-web` 一起挂载；官方 Web/标准 preset 的 tool catalog 包含
   `web_search` 和 `web_fetch`。
2. **DSH key 与聊天路由不是同一个 HTTP 契约。** 原生搜索复用 `DEEPSEEK_API_KEY`，但默认走
   `https://api.deepseek.com/anthropic/v1/messages`，模型默认为 `deepseek-v4-flash`，而聊天
   adapter 使用另一套 `DEEPSEEK_BASE_URL`/Chat Completions 配置。中转站只有聊天兼容并不证明它
   支持该 Anthropic-compatible Search route。
3. **当前 Decision Hub 没有“意外丢掉 DSH 能力”，而是显式收窄了 preset。**
   `infra/dsh/presets/decision-research/agent.cordis.yml` 只把 Hub 研究 capability tool、
   delegation、todo 和 compaction 暴露给该 agent；它没有把官方 `tool-web` 作为产品研究工具
   入口。这样做是为了不让原生搜索结果绕过 Hub 的 authority、PIT、hash、独立来源和 Gate。
4. **“接入 DSH 原生搜索”不能等于直接让模型自由搜索。** 后续必须用官方 DSH tool/Hook seam
   把结构化搜索结果映射为 `SearchResult.v1` 的 `search_derived` candidate，带上 DSH session、
   tool call、query hash 和三时间戳，再经 Hub Gateway 验证；无法完成 attestation 时只能保留在
   DSH Trajectory，不能满足金融 hard requirement。
5. **没有额外 DSH 许可证。** 原生搜索是否产生额外费用取决于 DeepSeek Search route 的模型
   调用和中转站计费；Tavily 则是独立的按 credit 计费 provider。两者的成本、延迟和失败码必须
   分开记录，不能把“同一个 key”误写成“同一种服务”。

因此本阶段的 provider 顺序不是“每次二选一并发”，而是有优先级的单路尝试：DSH 原生搜索作为
discovery primary；只有它超时、无来源、来源独立性不足或 route 不兼容时，才按预算调用 Tavily
作为独立、可替换的 discovery/fetch fallback。任一路径都不能绕过同一个 Evidence Gateway。

### 3.2 选型结论

首个真实 provider 验证按两个隔离阶段执行：**DSH 原生 Search probe/attestation** 已在
2026-09-03 通过最小 route probe；**Tavily API/MCP probe** 只在 G2-AF-01 契约门通过后作为
fallback canary 执行。不把其中一个失败改写成另一个成功，也不在同一 Run 无条件重复调用两家。

DSH 原生 probe 优先验证：

- 当前 DeepSeek key 是否能访问 Anthropic-compatible Search route；
- 官方 DSH `web_search` 是否真实出现在该 preset 的 tool catalog；
- 结果是否带结构化 URL/source/citation，并能通过 Hub attestation；
- 单次搜索的延迟、模型 token 和 provider 错误是否可控。

Tavily fallback probe 仍然保留，理由是：

1. 现有 DSH 通过 MCP 暴露工具，Tavily 有成熟官方 MCP，不需要自写搜索引擎或第二套 agent loop；
2. Search 和 Extract/Crawl 可以在同一权限边界内完成“发现 -> 读取原文”；
3. 单个事件的预算可控。即使每个 hard gap 做 1 次 basic search、6 个 gap、每月 20 个事件，约 120 credits，通常落在免费额度内；advanced 查询和 extract 另计，必须通过 Run budget 控制；
4. 它不是权威事实源，正因为这一点，架构会强制 `search_derived -> verified_web/official/market` 晋级，避免供应商摘要变成结论；
5. 以后可以在同一 `SearchCapabilityPort` 下替换 Brave、Exa、Serper 或 SearXNG，不改变 Core、DSH prompt、Gate 或账本。

当前 route probe 已通过，但 attestation bridge、来源质量和业务 Gate 仍未通过，因此 DSH native
只在 discovery 层作为 primary candidate；Tavily 在 native timeout、来源不足或需独立复核时作为
fallback。若未来实测 native 的延迟/费用/可归因率不适合，仍可在不改 Hub contract、Gate、前端和
账本的情况下调整 provider 顺序；这需要新的 ADR 和 canary 证据，不在运行中动态切换。

Brave 作为第二个独立索引只在首个 DSH/Tavily canary 证明“搜索成功但来源覆盖不足或单一供应商
不稳”后引入。这样既满足信息尽可能充分，又不在没有质量证据时堆付费 key。

### 3.3 固定金融来源注册表（设计，不是无脑轮询清单）

“固定二十几个页面”应实现为 Domain Pack 的**受审计来源注册表**，而不是让模型每轮重复
搜索二十几次。注册表是来源策略资产，不是新的跨模块 DTO；它由 `packs/crypto_macro/`
维护，provider adapter 只读取公开的 `SourceRef` 语义。每个事件按 requirement 选择相关的
条目，默认最多并行读取一组，避免费用、限流和重复转载。

首版建议冻结 27 个来源引用（实际 URL/API 路径在实现时逐项做可达性和字段审计）：

| 级别 | source_ref | 载体 | 主要事实 | 历史能力/限制 |
|---|---|---|---|---|
| P0 | `fed.press_releases` | Federal Reserve 官方发布页 | 当前政策/官方措辞 | 有发布时间；保留原文 hash |
| P0 | `fed.speeches` | Federal Reserve 官方讲话页 | 官员讲话全文 | 需按 speaker/date 过滤 |
| P0 | `fed.calendar` | Federal Reserve 日历 | 事件 identity、预定时间 | 事件修订需保留 revision |
| P0 | `fed.fomc_statement` | FOMC 声明页 | 当前/历史政策文本 | 可按会议日期回溯 |
| P0 | `fed.minutes` | FOMC 会议纪要页 | 事后政策细节 | 发布滞后，不能冒充实时 |
| P0 | `treasury.par_yield_curve` | U.S. Treasury 数据页/API | 2Y/10Y 名义收益率 | 日频历史；记录 observed_at |
| P0 | `treasury.bill_rates` | U.S. Treasury 数据页/API | 短端利率 | 日频历史；不能替代实时盘口 |
| P0 | `fred.dff` | FRED `DFF` | 有效联邦基金利率 | 日频历史；事件窗口可能 stale |
| P0 | `fred.dgs2` | FRED `DGS2` | 2Y 收益率 | 日频历史；不能满足分钟级 gate |
| P0 | `fred.dgs10` | FRED `DGS10` | 10Y 收益率 | 日频历史；不能满足分钟级 gate |
| P0 | `fred.dtwexbgs` | FRED `DTWEXBGS` | 美元指数代理 | 日频历史；需 freshness 检查 |
| P0 | `bls.cpi_release` | BLS 官方 CPI 发布页 | CPI/核心 CPI | 历史可回溯；发布时间优先 |
| P0 | `bls.release_calendar` | BLS 发布日历 | 数据事件 identity | 只作日历，不作数值 |
| P0 | `bea.pce_release` | BEA 官方 PCE 发布页 | PCE/核心 PCE | 历史可回溯；发布时间优先 |
| P0 | `bea.release_calendar` | BEA 发布日历 | 数据事件 identity | 修订需新 revision |
| P0 | `cme.fedwatch` | CME FedWatch/利率期货说明 | 会议隐含概率 | 必须核对 snapshot 时间和方法 |
| P0 | `okx.spot` | OKX public market API | BTC spot/volume | 分钟级/逐笔取决于 endpoint |
| P0 | `okx.funding` | OKX public derivatives API | funding | 需记录合约、结算周期 |
| P0 | `okx.open_interest` | OKX public derivatives API | OI | 需记录合约和时间窗 |
| P0 | `okx.liquidations` | OKX public derivatives API | 清算/流量 | 若接口不提供则明确缺失 |
| P0 | `deribit.spot` | Deribit public API | BTC spot/volume | 交易所口径独立 |
| P0 | `deribit.funding` | Deribit public API | funding/basis 辅助 | 不与 OKX 数值混写 |
| P0 | `deribit.open_interest` | Deribit public API | OI | 记录 instrument 与时间 |
| P0 | `bybit.spot` | Bybit public API | BTC spot/volume | 交易所口径独立 |
| P0 | `bybit.funding` | Bybit public derivatives API | funding | 记录结算周期 |
| P0 | `bybit.open_interest` | Bybit public derivatives API | OI | 记录 instrument 与时间 |
| P0 | `bybit.liquidations` | Bybit public derivatives API | 清算/流量 | 若接口不提供则明确缺失 |
| P1 | `reuters.markets_macro` | Reuters 受限新闻页/RSS | 独立事件解释 | 受版权/登录/访问策略限制，不能替代 P0 数值 |

注册表每条记录至少包含：`source_ref`、canonical locator、source class、publisher、authority、
支持的 `requirement_id`、字段映射、历史查询方式、最大允许 age、`published_at`/`observed_at`/
`received_at`、独立性分组、转载关系、robots/登录/付费标记、内容 hash、许可证和失败码。
同一发布者的转载、搜索摘要和模型记忆不能计为独立来源。固定来源只声明“优先读哪里”，
不保证页面永远可达；不可达时必须生成结构化 failure provenance 并选择注册表中已批准的
fallback。

### 3.4 来源权重、垃圾过滤和历史数据规则

来源不压成一个“可信度分数”，而是按硬门逐项验证：

| 维度 | P0 通过条件 | P1/P2 用途 |
|---|---|---|
| authority | 机构/交易所与 requirement 的 authority floor 匹配 | 解释或发现，不替代关键事实 |
| freshness | 在 requirement 的最大 age 内，且三时间戳顺序合法 | 过期内容只能标 stale |
| identity | publisher、canonical URL、事件/合约/序列号明确 | 摘要或无上下文页面只能 candidate |
| independence | 与已有证据不属于同 publisher/转载链 | 只增加覆盖，不虚增独立来源数 |
| reproducibility | 可按 locator + query + cutoff 重读或使用历史快照 | 无历史截面时不得回填过去状态 |
| attribution | 原文/结构化字段有精确引用和 content hash | 无引用的自然语言不入 Evidence |

历史数据优先从 typed provider 或官方历史序列取得；网页当前值不能倒推事件时点值。
所有查询携带 PIT cutoff，结果按 `published_at <= observed_at <= received_at <= cutoff` 校验，
并保存请求参数和内容 hash。Tavily/DSH 搜索只发现候选，不能直接满足 `expectation.pricing`、
`macro.transmission`、`crypto.spot` 或 `crypto.derivatives` 的 hard requirement。

### 3.5 需要 owner 提供的外部前置

在本文件确认后，owner 只需要：

1. Tavily key 已收到；正式执行前只需确认将它放入本机 gitignored `.env` 或 secret mount（不进文档/仓库/日志）。由于 key 已出现在聊天内容，正式使用前建议在 Tavily 控制台轮换一次；
2. 确认单次 Run 的最高 Search 预算（建议初始 `$0.50`）和每小时最大事件数；
3. 选择是否在首个 canary 后追加 Brave key。未选择时系统仍可使用 typed provider 和 Tavily，缺失时诚实停止。

不需要为 Tavily MCP 再购买 DSH、LangGraph、LoongSuite 或其他 Harness 许可。DSH、LangGraph、Pydantic、OpenTelemetry 是现有工程依赖；外部供应商只对其 Search/Extract 用量计费。SearXNG 不需 key，但不等于没有运维成本。

## 4. 能力目录与插件边界

### 4.1 四类可插拔资产

| 名称 | 放置/载入方式 | 负责什么 | 不负责什么 |
|---|---|---|---|
| DSH Native Plugin | 官方 DSH profile manifest；Host/Client/Telemetry seam | 工作台、工具暴露、会话、轨迹、UI 投影 | 不拥有 Hub 账本和金融 Gate |
| Capability Adapter | Hub `SearchCapabilityPort`/MCP Gateway；provider 可替换 | 调用一个外部能力，返回 canonical result | 不生成方向性决策，不写 raw provider payload |
| Domain Pack/Role Profile | `packs/<domain>/` manifest、doctrine、profile、evidence、gates | hard requirements、根因链、角色任务和权限 | 不改 Platform Core，不安装未知插件 |
| Observability Plugin | DSH profile opt-in（LoongSuite） | OTel 技术 trace、token、耗时、重试、错误 | 不增加搜索或证据可信度 |

因此“把业务做成插件”是合理方向，但不能把所有层都叫插件：搜索是 Capability Adapter/DSH Tool，交易员是 Domain Role Profile，LoongSuite 是 Telemetry Plugin，Hub 是产品控制面。四者通过公开契约组合，而不是互相硬引用。

### 4.2 Capability Catalog 最小契约

后续实现只增加一个只读、版本化 Catalog，不允许模型改写。仓库当前已经存在
`ResearchCapabilityManifest.v1`、`ResearchCapabilityQuery.v1`、`ResearchCapabilityResult.v1`
和 `SearchCapabilityPort`/Gateway；G2-AF-01 必须先复用这些 canonical contract。若现有字段不足，
只能在 `contracts/schemas/` 增加版本化字段并重新 codegen，不能另建一份旁路 provider YAML 与
canonical manifest 双写。以下字段是目标语义，具体是扩展 v1 还是新建 v2 必须由 Red test 和 ADR 决定：

```yaml
capability_id: web.search
provider_id: tavily
adapter_version: web-search.tavily.v1
mode: live
authority: search_derived
supports: [source_discovery, news, time_filter, allowed_domains]
input_schema: SearchQuery.v1
output_schema: SearchResult.v1
freshness_seconds: 300
timeout_seconds: 20
max_results: 10
cost:
  unit: credit
  estimated_usd: 0.008
  budget_required: true
scopes: [internet_read]
fallback_rank: 3
verification: web.fetch | official.* | market.*
replay_fixture_ref: fixtures/search/tavily.v1
license: MIT
enabled: false
```

每个 `requirement_id` 还要声明：

- `primary_capabilities`：首选 typed provider；
- `fallback_capabilities`：第二 typed provider、Search discovery、Fetch；
- `minimum_independent_sources`；
- `authority_allowlist`；
- `freshness_seconds` 和 `published_at/observed_at/received_at` 使用方式；
- `verification_rule`、`cost_ceiling`、`timeout` 和固定失败码。

### 4.3 `crypto_macro.v1` 的能力梯度

| Hard requirement | Primary | Fallback | Search 的作用 | 不能满足时 |
|---|---|---|---|---|
| `event.identity` | Fed/官方日历和原文 | 已审计官方 feed、Tavily discovery + fetch | 找到事件页/讲话稿 | 无身份则停止方向性结论 |
| `policy.delta` | 当前/前次官方 statement/speech | 官方 RSS + fetch | 找到前次措辞和独立报道 | 只有摘要不能入账 |
| `expectation.pricing` | 受审计利率/期货数据 | 第二市场 provider | 找到数据说明或事件解读 | 缺失则 `research_only` |
| `macro.transmission` | FRED/Treasury/官方统计 | 第二 typed macro provider | 找到具体数据页 | 缺失则 `research_only` |
| `crypto.spot` | 交易所现货 ticker/volume | 第二交易所 | 仅找事件窗口说明 | 缺失则 `no_trade` |
| `crypto.derivatives` | 交易所 funding/OI/basis/liq | 第二交易所/审计聚合器 | 仅找数据解释 | 缺失则 `no_trade` |

Search 不能凭自身补出 OI、funding、清算或历史隐含概率。找不到 typed provider 时，系统必须明确说“关键数据不可用”，而不是把网页文字当数值。

## 5. 主动研究循环的实现方案

### 5.1 两层循环，不造第三套

**DSH 内层 Supervisor Loop**：在同一 DSH Session 中读取 `role_profile_ref`、hard gap、已尝试 capability、剩余预算和允许权限，选择下一步 Tool/Skill/Subagent。它可以并行发起互不依赖的 capability 调用，要求每个调用返回 canonical result。

**Hub 外层 LangGraph Lifecycle**：负责 Run admission、PIT cutoff、round budget、checkpoint、lease、失败隔离、Evidence commit、Coverage recompute、Gate 和恢复。它只在“有预算且产生了新的 accepted Evidence 或存在未尝试 fallback”时进入下一轮。

外层决策伪代码：

```text
while round < max_rounds and deadline_not_expired and cost < budget:
    gaps = deterministic_sufficiency(requirements, evidence, cutoff)
    if gaps.empty: finalize_sufficient()
    candidates = catalog.resolve(gaps, attempted, permissions)
    if candidates.empty: finalize_bounded_failure(no_eligible_capability)
    results = DSH.supervisor.run_parallel(candidates)
    accepted = gateway.validate_and_commit(results)
    if accepted or candidates.have_unattempted_retryable:
        continue
    finalize_bounded_failure(capabilities_exhausted)
```

模型只能提出“下一步调用哪个 capability、为什么需要它、预期关闭哪个 gap”；代码负责是否允许调用、是否重试、是否接受 Evidence、是否继续和是否生成报告。

### 5.2 搜索和验证步骤

1. Supervisor 为每个 hard gap 生成窄查询：事件名、官方主体、日期、需要的具体事实；禁止只生成泛化“市场怎么看”。
2. `web.search` 返回候选 URL、标题、摘要、provider timestamp、query hash 和成本，authority 固定为 `search_derived`。
3. Resolver 按域名/robots/权限/重复度选择候选；优先官方域名和独立来源，不把同一新闻转载算作独立来源。
4. `web.fetch` 读取原文，记录 `published_at`、`observed_at`、`received_at`、内容 hash、HTTP 状态和来源关系；必要时分块读取。
5. 对数字类事实调用 typed Official/Market capability；原文只用于解释和交叉验证。
6. Gateway 进行 schema、PIT、freshness、authority、独立来源和冲突检查；通过后写入 Evidence Ledger。
7. Sufficiency 重新计算。若仍有 hard gap，继续下一轮；若已经满足，DSH 生成候选报告，代码 Gate 决定 `publish/degraded/research_only/reject`。

### 5.3 结构化 synthesis 的唯一回传边界

真实运行已经证明 DSH 会主动搜索、调用 typed provider 并在同一 Session 继续下一轮；当前剩余的
协议问题是模型可能在合法 JSON 前增加解释文字。Web runtime 不新增宽松文本解析器，也不把修复
迁出 DSH Agent Loop，固定使用以下路径：

```text
DSH manager 完成获批能力调用
  -> decision_hub_synthesis_submit(candidate)
  -> DSH Tool 使用 codegen Zod schema 校验
  -> schema 错误作为 Tool error 回到同一 DSH Agent Loop 修复
  -> 成功 tool/result 写入 DSH Trajectory/JSONL
  -> Hub 证明同 session_id + tool_call_id 的 tool/call/result 配对
  -> Hub 再校验 request_id 和 Evidence ID 白名单
  -> Gate 决定报告终态
```

约束：

- submit tool 只捕获候选对象，不写 Hub 账本、不发布、不通知、不交易；
- 参数 schema 直接复用 canonical YAML 生成的 `researchSynthesisCandidateSchema`，禁止 Python/TypeScript 双写；
- Hub 只接受明确名为 `decision_hub_synthesis_submit` 的成功 tool result，禁止从 payload 内嵌 `name` 猜测工具名；
- 多次成功提交时只取同一 Session 最后一次合法结果；失败 result、跨 Session result、未配对 result 均拒绝；
- Web runtime 优先使用 attested submit；SDK/replay 保留原有完整严格 JSON 兼容路径；
- 最终自然语言响应不再是 Web synthesis 的事实源，可被安全忽略。

### 5.4 预算、超时、重试和错误分类

建议首个 live profile（可配置，不写死在 Core）：

| 约束 | 初始值 | 说明 |
|---|---:|---|
| 单 Run deadline | 480 秒 | 已按真实三轮 DSH 延迟复验；超过即 `research_deadline_exceeded` |
| 最大 research round | 3 | 防止无限循环；每轮有 checkpoint |
| 最大 capability calls | 24 | Search/Fetch/typed 合计 |
| Search 单 Run 预算 | `$0.50`（总 capability 上限 `$1.00`） | provider usage 缺失时记 `unknown`，不伪造 0 |
| Search 单次 timeout | 20 秒 | 可 retry 一次，受总 deadline 约束 |
| 可重试次数 | 1-2 | 仅 timeout/429/5xx/temporary DNS；PIT/schema/auth 不重试 |
| 事件触发频率 | feed 优先；高影响窗口 1-5 分钟可配 | 不默认全网每秒轮询，避免费用和重复事件 |

错误必须保留 origin/cause/retryable：

`search_configuration_invalid`、`search_provider_timeout`、`search_rate_limited`、`search_no_sources`、`search_no_attributed_sources`、`fetch_forbidden`、`fetch_timeout`、`source_pit_violation`、`source_stale`、`source_conflict`、`critical_data_unavailable`、`research_deadline_exceeded`。

一次 capability 失败不能 abort 其他并行结果；成功 Evidence 先提交，再汇合。所有失败都可在 Trace、Run Inspector 和报告停止区看到。

### 5.5 DSH 运行态单写者约束

DSH Session/Trajectory 继续使用官方 JSONL persistence，不复制存储实现。产品启动器必须保证
同一规范化 `DSH_WEB_HOME` 只有一个存活的官方 DSH Web 写进程；不同端口不构成隔离。锁使用
PID、进程启动标识和 Home 三元组识别 owner，崩溃遗留或 PID 复用可安全接管，存活 owner 必须
fail-fast。replay、canary 和开发实例需要并行时各用独立 Home。详细决策见
[ADR-0023](../decisions/ADR-0023-single-writer-dsh-home.md)。

## 6. 自动调度、报告和通知

### 6.1 调度路径

```text
Official calendar/feed + approved news source
  -> SourceAdapter cursor/revision/dedupe
  -> Event admission（影响等级、时间窗、来源权限）
  -> durable research.v1 Run
  -> research worker lease/checkpoint
  -> DSH Web/Session Supervisor Loop
  -> Hub Evidence/Gate/Artifact
  -> Outbox notification + next recheck
```

调度规则：

- Fed/BLS/BEA 等官方 feed 是低成本首选；日历 discovery 必须显式开启并记录来源授权。
- Tavily News/Search 不作为每秒全网监听器；只在新事件 admission、临近高影响窗口和复查时调用。
- 事件以 `source_id + external_event_id + revision` 去重；修订产生新 revision，不改写旧 Run。
- research worker 重启后从 lease/checkpoint 恢复；同一事件不会重复发送同一通知。
- owner 手工输入仍保留为补充入口，但后台事件触发是产品主路径。

### 6.2 前端呈现

用户只进入官方 DSH Web。DSH Client Plugin 增加可读的 Research Panel，不显示无用 raw JSON：

- 当前自动运行、事件时间、Role/Domain Pack、Runtime/Provider；
- 研究进度：`发现事件 -> 补证第 N 轮 -> 已尝试/成功/失败 capability`；
- 六类 hard requirement 覆盖表，显示 authority、freshness、独立来源和缺口；
- 证据时间线：来源标题、域名、发布时间、观察时间、hash、验证状态和冲突；
- 报告卡：30m/24h/72h、触发条件、失效条件、证据引用、Gate 状态；
- 停止/复查卡：为什么停止、已尝试什么、何时自动复查、需要 owner 做什么；
- 通知状态和 LoongSuite `trace_ref` 链接；
- 原始 DSH JSONL 只在诊断抽屉按权限查看，不作为默认用户界面。

Decision Desk 保留为管理后台：来源/能力目录、Run/Trace 查询、Evaluation、FailurePattern、候选 Promotion 和 owner feedback。它不是第二个用户入口。

### 6.3 通知

报告完成或发生重要失败都写入 Outbox：

- `publish`：发送可行动研究报告；
- `degraded/research_only`：发送“信息不足/不可交易”的事实清单和自动复查时间；
- `reject/failed`：发送失败 origin/cause、已保留证据和重试状态。

邮件/IM/桌面通知都是可替换 delivery adapter，通知去重使用 `artifact_id + channel + revision`，不能由模型直接发送。

## 7. 资产沉淀与自进化

每个 Run 都保留并关联：

1. `Domain Doctrine`：根因链、事实要求、来源优先级、freshness 和 Gate；
2. `Capability Catalog`：provider、版本、权限、成本、失败和 replay fixture；
3. `Role Profile`：Manager、Counter-thesis、Data Quality 和 Specialist 的任务/权限；
4. `Research Trace`：Plan、Tool Attempt、Evidence、Sufficiency、Stop Reason、DSH Session/JSONL ref、LoongSuite trace ref；
5. `Evidence Pack`：来源 URL、原文 hash、三时间戳、authority、冲突和独立性；
6. `Evaluation Dataset`：Fixed 与 DSH candidate 的同条件输入、Coverage、延迟、成本、30m/24h/72h Outcome、Brier 和 owner usefulness；
7. `FailurePattern/Experience`：失败根因、修复、适用范围、是否可推广；
8. `Product Artifact`：报告、预测、通知、owner review 和 Promotion/Rollback 审计。

自进化闭环为：

```text
失败/结果/owner feedback
  -> FailurePattern/Experience
  -> candidate（prompt、来源优先级、查询模板或 capability 配置）
  -> replay
  -> holdout
  -> shadow
  -> owner review
  -> promote / retain / stop
```

禁止“失败一次就自动改 Prompt/Gate、自动安装插件、自动切 active pointer”。自进化的资产是可比较、可回放、可回滚的候选，不是未经验证的自我修改。

## 8. 代码落点与依赖方向

以下是实现时允许的目标结构；本阶段文档确认前不创建空目录：

```text
packages/provider_adapters/search/
  openai_responses.py       # 已有，可保留 candidate
  tavily.py                 # 新增：Tavily API/MCP canonical adapter
  brave.py                  # 后续独立 fallback，不与 Tavily 混写
  searxng.py                # 可选自建 fallback

packages/kernel/.../ports/
  search.py                 # SearchCapabilityPort，保持 provider-neutral
  capabilities.py           # 只读 Catalog/Resolver 的公开契约

packages/orchestration/langgraph/graphs/
  agentic_research_graph.py # 扩展 round continuation，不新写 Agent loop

apps/research_mcp/
  main.py                   # 仅注册 approved capability；deny-by-default

packs/crypto_macro/
  tools/bindings.yaml       # capability binding
  sources.yaml              # requirement/source/fallback/freshness
  profiles/manager.yaml     # 正式 Role Profile preset

extensions/dsh/decision-hub/
  src/client/                # Research Panel、状态/Trace/报告投影

apps/hub_worker/
  realtime/                  # calendar/feed admission
  research/                  # durable lease/checkpoint/recheck
  outbox/                    # email/IM adapter dispatch

tests/capabilities/ tests/provider_adapters/ tests/orchestration/
tests/evolution/ tests/dsh_native/ tests/frontend/
```

依赖方向必须保持：

```text
DSH Plugin/Tool -> Public MCP/Capability Gateway -> Hub Port
Hub Port -> Provider Adapter -> External API
Domain Pack -> Canonical requirement/Gate
LangGraph -> lifecycle/checkpoint only
Frontend -> Query/View DTO only
```

禁止：Domain Pack 直接 import 某供应商 SDK；前端直读 provider payload；DSH 写 Hub 账本；Search adapter 自己实现重试/账本/Trace；新增第二套 DTO、Agent loop 或 workflow engine。

## 9. SDD/BDD/TDD/ADR 执行约束

### 9.1 SDD 和 ADR

实现前必须冻结：

- `CapabilityDescriptor.v1`、`CapabilityAttempt.v1`、`SearchResult.v1`、`SourceVerification.v1`；
- 六类 requirement 的 primary/fallback、authority、freshness、cost 和 failure code；
- DSH tool 权限和 live allowlist；
- Run/round/deadline/retry/cost 的配置；
- 前端 Query/View 字段及通知事件。

如果要新增跨模块字段、替换 Tavily、引入 Brave、修改 DSH profile、改变主动调度默认值或改变 Gate，先新增 ADR，记录候选、否决项、费用、后果和回滚方式。

### 9.2 BDD 验收场景

```text
Feature: 系统主动补齐 hard gap
Scenario: Search 发现来源后继续验证
Given 一个有 policy.delta 和 macro_transmission gap 的新事件
And web.search 已批准且仍有预算
When DSH Supervisor 执行第一轮
Then 产生 search_derived candidates
And 系统自动选择 web.fetch/typed provider 验证
And 只要产生新的 accepted Evidence 就进入下一轮
```

```text
Feature: 信息不足不被掩盖
Scenario: 所有回退能力失败
Given Search、Fetch 和 typed provider 均有明确失败 provenance
When deadline 或能力目录耗尽
Then Run 进入 research_only/degraded/reject
And 报告列出已尝试能力、失败原因、未关闭 hard gaps 和复查时间
And 不产生方向性 publish Artifact 或交易权限
```

```text
Feature: 搜索摘要不是金融事实
Scenario: 搜索结果只有摘要没有原文验证
Given web.search 返回一个候选 URL
When fetch 被拒绝或原文不含可验证数据
Then evidence authority 保持 search_derived/candidate
And expectation_pricing 或 derivatives requirement 仍为 insufficient
And 系统继续尝试下一个 approved fallback 或安全停止
```

```text
Feature: 后台主动触发
Scenario: 高影响日历事件自动创建研究任务
Given 一个新的 approved calendar event 且不重复
When realtime worker 到达 admission window
Then 创建 durable research.v1 Run
And 无需 owner 再次输入即可进入 DSH Session
And 报告完成/失败均进入 Outbox，重复事件不重复通知
```

```text
Feature: 结构化报告在 DSH 内闭环修复
Scenario: 模型在最终文字中加入说明
Given DSH 已获得一组经过 Hub Gateway 证明的 Evidence
When 模型通过 decision_hub_synthesis_submit 提交候选报告
Then DSH Tool 使用 codegen schema 校验并在错误时把反馈返回同一 Agent Loop
And Hub 只接受配对成功的 tool/call 与 tool/result
And 未证明的 Evidence 引用仍触发 dsh_evidence_unattested
And Hub 不从自然语言中截取 JSON
```

```text
Feature: Provider 可替换
Scenario: Tavily 暂时失败时切换 Brave 或 SearXNG
Given 相同 SearchQuery、PIT cutoff 和 contract
When Tavily 返回 timeout/429
Then 只按 Catalog 允许的 fallback 顺序尝试
And Core、Gate、前端 DTO、账本和 DSH loop 不变
And 每次尝试保留 provider_id、错误 provenance、成本和耗时
```

### 9.3 TDD 矩阵

- Catalog：非法 provider、权限域、缺 fallback、成本/timeout 越界、manifest hash 变化；
- Tavily/Brave/SearXNG contract：成功、空结果、重复 URL、引用缺失、限流、超时、无 key；
- 验证：Search candidate 不可直接入账，Fetch/typed source 的三时间戳、hash、authority 和 PIT；
- Loop：hard gap 有未尝试 fallback 时必继续；无新 Evidence 但有 retryable candidate 时按预算重试；预算/期限耗尽时确定性停止；
- 并行：一个 capability 失败不丢其他成功；全部失败和 critical failure 都有稳定终态；
- 调度：cursor、revision、dedupe、重启、lease、checkpoint、child recheck 和通知幂等；
- 前端：progress/coverage/stop/trace/report 投影完整，失败不显示为 researching，默认不显示 raw JSON；
- 自进化：失败样本、Outcome、owner feedback、candidate/replay/holdout/shadow 可查询，禁止自动 Promotion；
- Live canary：普通 CI 永不触网；显式 `LIVE_SEARCH_CANARY=1` 才读本机 secret，临时目录、只读来源、限时、脱敏报告。

## 10. 阶段任务卡与验收门

### G2-AF-01：契约和能力目录

目标：冻结 provider-neutral Catalog、requirement fallback、失败码和预算配置。  
验收：Pydantic/TypeScript codegen、manifest check、权限 deny-by-default、无业务代码改动。  
停止：需要改写 Core 账本或新增第二套 DTO 时回到 ADR。

### G2-AF-02：Search discovery/fetch canary

目标：在临时隔离目录验证 DSH native Search -> Fetch/Official -> Evidence attribution，并在
native 不可用时用单个轮换后的 Tavily key 做同一契约的独立 fallback。  
验收：native canary 至少一个 Fed/宏观原文已通过；Tavily canary 仍需 key 轮换后再执行；每次记录 URL、hash、三时间戳、延迟、credits、失败 provenance；Search 摘要不能直接满足 critical requirement。  
停止：Provider 不支持结构化来源、无法满足权限/PIT、费用不可控或需要把 key 写进仓库时停止。

### G2-AF-03：DSH gap-driven continuation

目标：让 DSH 在同一 Session 中根据 Catalog 选择未尝试能力，Hub 继续 round，不再因第一组工具不足立即 finalize。  
验收：success/partial/insufficient/timeout 四个 replay 场景；新 Evidence 进入下一轮；失败保留成功结果；无能力时解释性 bounded failure；Web synthesis 必须经过 attested submit tool，且最终自然语言不能绕过 schema/Evidence 白名单。  
停止：需要自写 ReAct loop、修改 Gate 放宽 authority 或复制 DSH session state 时停止。

### G2-AF-04：自动事件到报告/通知

目标：官方 feed/calendar admission -> durable Run -> DSH Session -> Report/Outbox/复查形成一条前瞻链。  
验收：无 owner 逐轮输入；重启恢复；重复事件不重复 Run/通知；同一 `DSH_WEB_HOME` 的第二个存活写进程被启动门拒绝；前端 Research Panel 显示进度、缺口、报告和停止原因。  
停止：真实来源授权不清、通知无法审计或需要多用户/远程队列时停止在单机版本。

### G2-AF-05：观察与产品价值门

目标：在 G2-AF-01 至 G2-AF-04 通过后进行至少 14 天或 20 个高影响事件的单 owner prospective observation。  
验收：记录 coverage、首证延迟、最终延迟、Search 成本、失败率、30m/24h/72h Outcome、Brier、通知延迟和 owner usefulness；做 `promote / retain / stop`。  
停止：不以报告长度或测试数量替代真实价值；若没有增量价值，保留资产并停止继续堆功能。

### 阶段退出条件

只有同时满足以下条件，才把产品称为“个人可用试点”：

```text
[x] G2-AF-01 Catalog/契约/权限/预算通过
[x] G2-AF-02 至少一个真实 Search -> Fetch -> Evidence canary 成功并有完整 provenance
[x] G2-AF-03 DSH 同一 Session 三轮主动补证、attested synthesis submit 和有界停止已真实复验
[x] G2-AF-04 事件自动触发、报告、Outbox 单次通知、复查和重复轮询幂等已贯通
[x] 关键 requirement 缺失时仍 fail-closed，不伪造、不放宽 PIT/authority
[x] DSH JSONL、LangGraph checkpoint、Hub Ledger、OTel/业务 Trace 可关联但所有权不混淆
[ ] 至少一段 prospective observation 有真实 Outcome 和 owner usefulness
[x] 文档、README、schema/codegen、状态、CHANGELOG、ADR 和测试证据同步
[ ] owner 记录 promote / retain / stop 决策
```

这套门通过后才是“个人研究辅助试点可用”，不是自动交易、盈利保证或多用户 SaaS。

## 11. 允许修改与明确不做

允许修改：

- `packages/provider_adapters/search/`、Capability Port/Catalog、研究图继续条件、Research MCP 注册；
- `packs/crypto_macro/` 的来源和 Role Profile manifest；
- `apps/hub_worker/` 的事件 admission、research lease、recheck、outbox；
- DSH Client Plugin 的 Research Panel/TraceRef Query View；
- 相关契约、测试、模块 README、状态和 ADR。

明确不做：

- 不 clone/重写 DSH，不替代官方 DSH Web；
- 不自写第二套 Agent loop、workflow engine、retry、tracing、JSON parser 或账本；
- 不自动搜索 GitHub、安装未知插件或把任意网页变成 approved capability；
- 不把 Search 摘要、模型记忆、未来时间数据当 canonical financial Evidence；
- 不自动交易、改 Gate、切 active pointer、自动 Promotion；
- 不在本阶段引入 Brave/Exa/Serper 全部供应商、Redis/Postgres/Temporal/DBOS、多用户或第二领域；
- 不重写历史 Event/Run/Evidence/Artifact/Forecast/Outcome。

## 12. Owner 确认项与确认后的第一步

G2-AF-04 自动事件整链真实验收已通过。当前状态保持：`Fixed active`、DSH research candidate
path、Tavily Search 不进入默认 Hub allowlist；产品可称为“单 owner 个人研究辅助技术试点”，
但在 G2-AF-05 前瞻观察完成前，不宣称事实长期充分、预测准确、盈利或可自动交易。

Owner 已接受本阶段推荐默认值；以下项目已锁定：

1. Native route probe 与 Search -> Gateway canary 已通过；Tavily fallback 真实 canary 仍要求轮换已暴露的旧 key；
2. 首个 profile 使用 Search `$0.50/Run`、总 capability `$1.00/Run`、`480s`、`3 rounds`、`24 calls`，只通过 Domain Pack 配置调整，不进入 Core；
3. feed/calendar 自动 admission，高影响窗口按 1-5 分钟可配置调用 Search，不做全网每秒轮询；
4. Brave 只作为后续独立 fallback，不在首期同时接入全部供应商；
5. G2-AF-01 至 G2-AF-05 是一个有限阶段，结束时必须做 `promote / retain / stop`，不自动开启新领域。

本轮已授权并完成 **G2-AF-01..04**：Capability Catalog、DSH native Search -> Gateway
attestation、同 Session gap continuation、结构化 synthesis、自动 feed admission、Artifact、Outbox
单次通知和 child recheck。真实验收 Run `run_b89cb225177145cd9a0e7cd0b038e31c` 完成 3 轮、
20/24 个受审计 capability call、13 条保留 Evidence 和 83.33% hard coverage，因 round budget
收敛为 `degraded/research_only`。Tavily fallback 仍受 secret 轮换、PIT、预算和只读门约束；
G2-AF-05 的 14 天或 20 个事件价值观察尚未完成。

## 13. 外部资料与核查链接

- DSH 官方上游锁定提交：[deepseek-harness](https://github.com/deepseek-ai/deepseek-harness/tree/0a53fb55bea101816fa226bb964ae2bed71c343b)；原生 Search provider：[dsh-web-search-deepseek](https://github.com/deepseek-ai/deepseek-harness/tree/0a53fb55bea101816fa226bb964ae2bed71c343b/packages/web/web-search-deepseek)；
- Tavily 价格与额度：[Credits & Pricing](https://docs.tavily.com/documentation/api-credits)；[官方 Tavily MCP](https://github.com/tavily-ai/tavily-mcp)；
- Brave Search MCP：[官方仓库](https://github.com/brave/brave-search-mcp-server)；[API 文档](https://api.search.brave.com/app/documentation/web-search/get-started)；
- Exa：[价格](https://exa.ai/pricing)；[官方 MCP](https://github.com/exa-labs/exa-mcp-server)；
- Serper：[价格页](https://serper.dev/)；
- SearXNG：[仓库](https://github.com/searxng/searxng)；[JSON Search API](https://docs.searxng.org/dev/search_api.html)；
- MCP Fetch 参考实现（明确提示需自行评估生产安全）：[官方说明](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)；
- DSH 原生 DeepSeek Web Search：上游 `packages/web/web-search-deepseek` README 与实现（锁定源码目录 `.cache/dsh-upstream/source/`）说明了 `DEEPSEEK_API_KEY`、独立 Anthropic-compatible `/messages` 路由和 `web_search_tool_result` 结构化结果；它与本项目的 OpenAI Responses adapter 是两条不同契约。

## 14. 结论

本阶段不是把产品再迁移到另一套框架，而是把已经存在的 DSH Harness、Hub 账本、LangGraph 生命周期和 Domain Pack 连接成一条真正主动的事实获取链。Tavily 只是第一个可替换的 Search capability；它不能替代官方/市场数据，也不能替代 Gate。完成阶段后，用户看到的是 DSH Web 中持续更新的研究进度、证据覆盖、报告、停止原因和复查通知，而不是一问一答后静默返回 `insufficient_sources`。
