 # Decision Hub 核心方向链路：根因修复与交付计划（2026-09-19）
 
 > 本文是当前代码审计后的执行计划，不是把“有方向”包装成“有胜率”。
 > 目标只有一个：用户输入“BTC 最新建议”后，系统能在可核验数据范围内给出当前主方向、行动倾向、最强反方和改判条件；证据不足只限制受影响的层级，不能把所有层级都变成一句“不能交易”。
 
 ## 1. 先给结论
 
 项目在技术上可以继续抢救。当前失败的本质不是“没有分钟数据”或“金融判断完全不可实现”，而是四个状态被压成了一个状态：
 
 1. 市场方向：未来 7 日偏多、偏空、区间或无法判断；
 2. 研究可信度：证据是否足够、来源是否独立、策略是否经过时间外验证；
 3. 入场/持仓倾向：已有仓位如何处理、空仓是否追价、是否偏好回撤；
 4. 执行权限：是否允许下单、杠杆、仓位和滑点检查是否通过。
 
 现有代码把第 2、4 层的失败写成第 1 层的 no_trade。因此真实运行中同时出现了“日线规则偏多”和“证据或复核未通过，不应据此交易”，但用户只能把后者理解成系统没有结论。
 
 第二个确定性问题是，固定入口的同一句请求在 AdmissionService 中按正文哈希去重。用户再次问“最新建议”时，可能拿到旧事件和旧触发快照；这会让结果看起来像系统没有刷新。
 
 本次修复先保证：
 
 - 研究方向可以保存和展示；
 - 执行权限仍然关闭；
 - 缺失衍生品只影响杠杆/拥挤与执行级判断；
 - 新的“最新建议”请求不会静默复用旧事件；
 - 任何未校准数值都不称为胜率。
 
 ## 2. 已经落到代码的根因
 
 | 用户症状 | 代码位置 | 实际行为 | 影响 |
 |---|---|---|---|
 | 方向被改成“不交易” | agentic_research_graph.py 的 finalize | stop_code 不是 sufficient 时把 long/short 改为 no_trade | 研究假设在保存前消失 |
 | 完整研究也不能发布多空 | commit.py 的 _research_result_is_publishable | 要求旧的 30m/24h/72h 全部存在，且排除 long/short | 方向观点和可执行 Forecast 被错误绑定 |
 | 页面主结论总是“证据未通过” | query_views/research/service.py 的 _action_summary | 数据不足、复核非 accepted、artifact limited 任一成立都选择同一 do_not_trade headline | 日线 ResearchOutlook 只能作为附属信息 |
 | 重复输入拿旧结果 | admission.py | 所有正文用同一 content hash 去重 | “最新”请求不一定是新的市场快照 |
 | 缺少衍生品拖垮所有方向 | source_manifest.yaml、fact_pack.py、sufficiency.py | derivatives_crowding 是硬缺口，硬缺口直接阻断 directional output | 缺口影响没有按决策层级声明 |
 | 宏观提示写了要搜，但主链未必取到正文 | dsh_runtime/profile.py、research graph | prompt 要求不等于 typed Evidence/Fact | 报告可能把“已要求”误写成“已核验” |
 | 0.5 被当成概率 | commit.py、旧 horizon contract | fail-closed sentinel 为 0.5 | 用户容易误以为这是统计中性概率 |
 
 ## 3. 本次已经实施的改变
 
 ### 3.1 查询层拆开方向和执行权限
 
 文件：packages/query_views/research/service.py
 
 当存在有效的日线策略收据时：
 
 - action_summary.recommendation = manual_review_required；
 - headline 直接写“7日价格规则倾向：偏多/偏空/区间；交易权限未授权”；
 - trade_authorized 继续是合同规定的 false；
 - rationale 明确这是可复算基线，不是样本外校准概率；
 - 衍生品、宏观、跨资产缺口转换成具体 missing_impact，例如“不能据此做杠杆或追涨判断”。
 
 这一步不放松交易安全门，只修复“有方向却显示成无方向”。
 
 ### 3.2 研究结果保留方向假设，提交 Forecast 仍 fail-closed
 
 文件：packages/orchestration/langgraph/graphs/agentic_research_graph.py
 
 bounded research 现在把 horizon 的方向保留为研究假设，只把概率降为未校准并附上执行抑制原因。CommitDecisionService 仍使用独立的 fail-closed 逻辑生成 no_trade Forecast。
 
 因此形成明确分层：
 
 - ResearchSessionResult.horizons：研究过程中的方向假设；
 - ForecastRecord：需要执行级证据和授权，未通过时保持 no_trade；
 - action_summary：同时展示方向和执行门状态。
 
 ### 3.3 最新建议按请求幂等键刷新
 
 文件：
 
 - packages/kernel/decision_hub_kernel/application/admission.py
 - packages/kernel/decision_hub_kernel/application/analyze.py
 
 固定入口 decision-desk-latest-advice + crypto_market_snapshot 使用 latest-advice:<Idempotency-Key> 作为哈希命名空间：
 
 - 同一幂等键重试：返回同一 event/run；
 - 新幂等键再次请求：创建新的 event/run/触发快照；
 - 普通文本和普通来源仍保持正文去重。
 
 ### 3.4 输入模板明确四层输出
 
 文件：packages/kernel/decision_hub_kernel/application/research_intake.py
 
 固定模板现在明确要求分别输出：
 
 - 市场方向；
 - 研究可信度；
 - 当前入场/持仓倾向；
 - 交易执行权限。
 
 并要求缺口说明限制哪一层，不能把缺口直接改写成方向不存在。
 
 ## 4. 目标架构
 
 ~~~mermaid
 flowchart TD
     U[最新 BTC 建议] --> I[固定入口 / 新鲜 Run]
     I --> D[数据编排与 PIT 快照]
     D --> P[完整日线 OHLCV]
     D --> Q[当前现货/成交量]
     D --> F[可用 funding/OI/basis]
     D --> M[官方宏观与事件正文]
     D --> S[情绪/叙事观察]
     P --> E[Evidence + Fact：来源/时点/单位/hash]
     Q --> E
     F --> E
     M --> E
     S --> E
     E --> R[日频策略基线]
     E --> H[主因/反方/鉴别证据]
     R --> O[Market View：7d 方向]
     H --> O
     O --> V[观点检查：证据相关性/反方/时效]
     O --> A[入场与持仓倾向]
     O --> G[Execution Gate：校准/流动性/拥挤/权限]
     V --> UI[Query View / Desk]
     A --> UI
     G --> UI
     G --> X[Forecast/下单权限：当前关闭]
 ~~~
 
 每层只做一件事：
 
 - 编排层：预算、超时、工具回执和多轮停止；
 - 领域层：指标语义、最小数据集、缺口影响；
 - Provider 层：读取原始数据，不补造缺失值；
 - 策略层：纯函数、可复算、可回放；
 - 研究层：解释机制、找反方、判断已定价风险；
 - 观点检查：确认 claim 与 Evidence 真正绑定；
 - Execution Gate：决定能否进入执行候选，不抹掉 Market View；
 - Query/UI：只投影保存结果，不从自然语言重新推断。
 
 ## 5. 接下来按 P0 顺序实施
 
 ### P0-A：把 7d Market View 变成原生合同
 
 状态：已实施并做过真实 Run 验证。7d Market View / Execution View 已成为原生合同；旧 horizons 保留历史兼容。整体稳定性仍按文末及开发日志验收，不因单项完成宣称整个产品交付。
 
 修改范围：
 
 - contracts/schemas/agentic_research.schema.yaml
 - packages/contracts_py/.../generated/agentic_research.py（通过 codegen 生成，不手改）
 - packages/contracts_ts/src/generated/r2.ts（通过 codegen 生成）
 - packages/runtime_adapters/dsh_runtime/result_mapper.py
 - packages/orchestration/langgraph/graphs/agentic_research_graph.py
 - packages/kernel/decision_hub_kernel/application/commit.py
 - packages/query_views/research/service.py
 
 新字段最小版本：
 
 ~~~yaml
 market_view:
   horizon: 7d
   direction: bullish | bearish | range | unresolved
   basis: rule_based | composite | calibrated
   rationale: string
   evidence_refs: []
   probability: null
   probability_status: unavailable | uncalibrated | calibrated
   action_candidate: string
   counter_case: string
   invalidation: string
   missing_impact: []
 execution_view:
   status: blocked | manual_review | authorized
   reasons: []
 ~~~
 
 兼容策略：
 
 - 旧 horizons 继续读取，供历史报告和审计使用；
 - 新报告以 market_view 为主；
 - Forecast 不再决定 market_view 是否存在；
 - 不允许模型只填 probability=0.5 就宣称已校准。
 
 验收：
 
 - 缺 derivatives 时仍可保存 market_view.direction=bullish/bearish/range；
 - execution_view.status=blocked 且原因明确；
 - 旧 API/历史数据解析不回归；
 - schema codegen、Python/TypeScript contract tests 全通过。
 
 ### P0-B：把 evidence requirement 从全局 hard/soft 改为决策层级
 
 状态：已实施 daily_direction / execution_candidate 分层；已收盘日线与双交易所现货对当前方向仍是 hard，衍生品、宏观/跨资产缺口按受影响层级披露。事件反应继续使用事件窗口合同，没有将所有 hard 改为 soft。
 
 在 pack 中增加三种模式：
 
 ~~~yaml
 daily_direction:
   hard: [market_daily_history, crypto_spot_confirmation]
   soft: [derivatives_crowding, macro_transmission, market_narrative]
 
 event_reaction:
   hard: [event_identity, event_window, crypto_spot_confirmation]
   soft: [derivatives_crowding]
 
 execution_candidate:
   hard: [current_spot, liquidity, derivatives_crowding, calibrated_strategy]
 ~~~
 
 代码位置：
 
 - packages/provider_adapters/research/fact_pack.py
 - packages/kernel/decision_hub_kernel/decision/sufficiency.py
 - packages/orchestration/langgraph/graphs/agentic_research_graph.py
 - packs/crypto_macro/evidence/source_manifest.yaml
 
 验收：
 
 - 日线完整、现货可用、衍生品不可用时：保留 7d 方向；
 - 同样缺口下：禁止杠杆/追涨/自动执行；
 - 没有日线基线时：direction=unresolved，而不是凭 LLM 补方向；
 - missing_impact 与受影响的决策层一一对应。
 
 ### P0-C：Provider 只做部分字段失败，不让一个端点拖垮 capability
 
 状态：适配器与路由补丁已完成；Gate/HTX 真实 smoke 可达，OKX/Binance/CoinEx 在当前环境仍有 transport/TLS 失败。重启后的固定入口 Run 仍需验证 provider attempts 和 coverage，不能把 smoke 当成交易有效性。
 
 修改范围：
 
 - packages/provider_adapters/market/gate_htx_research.py
 - packages/provider_adapters/market/crowding.py
 - packages/provider_adapters/market/okx_research.py
 - packages/provider_adapters/market/binance_research.py
 - packages/provider_adapters/routing.py
 - packages/kernel/.../research_evidence.py
 
 要求：
 
 - funding、OI、basis、crowding 分字段记录成功/失败；
 - 每次尝试保留 provider、route、错误码、TLS/DNS/timeout 分类；
 - 单一交易所指标标注局部证据，不冒充全市场；
 - 不关闭证书校验，不用无限重试掩盖出口问题；
 - Gate/HTX 可用时优先提供衍生品和盘口字段，现货和日线基线继续独立；单一 venue 不能代表全市场。
 
 外部官方接口可作为适配器依据：
 
 - Binance Funding Rate History：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
 - Binance Open Interest：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest
 - OKX API v5：https://www.okx.com/docs-v5
 - Gate Spot Candlesticks：https://www.gate.com/docs/developers/apiv4/en/spot/
 - Gate Futures API：https://www.gate.com/docs/developers/apiv4/en/futures/
 - HTX USDT swap API：https://huobiapi.github.io/docs/usdt_swap/v1/en/
 
 ### P0-D：宏观取证从提示词变成独立 typed Evidence
 
 状态：已接通 Yahoo 完整日频 US10Y/DXY/SPX/VIX typed Evidence，并在多次真实 Run 中取得；新鲜度按当前快照的 3 日窗口评估。它是延迟聚合市场数据，不是官方宏观发布值。下列官方正文差分、预期差和 vintage 能力仍未完成，不计入当前方向基线的已验证能力。
 
 每条宏观主张至少保存：
 
 - 官方原文 URL；
 - 发布时间和抓取时间；
 - 相对前次声明的变化；
 - 市场预期/预期差是否可观测；
 - 传导机制；
 - 支持与反方；
 - 适用周期。
 
 宏观缺失只写“未纳入综合判断”，不能写“宏观中性”。
 
 外部方法依据：
 
 - Federal Reserve monetary policy transmission：https://www.federalreserve.gov/monetarypolicy/monetary-policy-what-are-its-goals-how-does-it-work.htm
 - FRED API：https://fred.stlouisfed.org/docs/api/fred/
 - FRED real-time periods / vintage：https://fred.stlouisfed.org/docs/api/fred/realtime_period.html
 - Bitcoin–Macro Disconnect（NY Fed staff report）：https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr1052.pdf?sc_lang=en
 
 ### P0-E：策略和概率严格分开
 
 状态：日频规则基线已存在；概率校准未完成。
 
 V0 只允许：
 
 - 完整已收盘日线；
 - SMA20/SMA60；
 - 1/2/7 日收益；
 - 明确当前行动候选、反方和失效条件；
 - probability_status=uncalibrated 或 unavailable。
 
 不允许：
 
 - 把规则分数直接映射成 62% 胜率；
 - 用随机切分验证时间序列；
 - 用 90 根日线宣称交易优势；
 - 把多个转载观点计成多个独立样本。
 
 若以后增加概率：
 
 - 7 日标签先固定为 close[t+7] > close[t] 或预先声明的收益/成本目标；
 - 按时间顺序滚动训练/验证，留出 gap；
 - 同时报 Brier、log loss、可靠性图、覆盖率、成本后收益、回撤；
 - 只有存在独立 calibration receipt 时才将 probability_status=calibrated。
 
 依据：
 
 - scikit-learn TimeSeriesSplit：https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
 - scikit-learn probability calibration：https://scikit-learn.org/stable/modules/calibration.html
 - On Calibration of Modern Neural Networks：https://arxiv.org/abs/1706.04599
 
 ## 6. 真实运行的验收口径
 
 每次验收必须保存以下四份材料：
 
 1. API JSON：run、market view、execution view、Evidence、gap、review；
 2. UI 截图或可复现 URL：首屏先看到方向和行动倾向；
 3. provider attempt：成功、失败、错误分类；
 4. 计算收据：日线区间、截止时间、策略版本、输入 hash。
 
当前已核验的真实 run（以根因修复后的新快照为准）：

- run：`run_db8c5d68614c4f3dadf341a9a4a1ecb7`；Artifact：`art_1a290160052f44218c5b1641567ddf34`；完整回执：`data/product-local/core-direction-recheck-20260919/root-cause-live-run.json`；
- 状态：`research_only` / `done`，不是运行失败；
- 日线：Gate 原生完整日线，截至 2026-09-18，90 根，`derived_from_hourly=false`；
- 价格规则：未来 7 日 `bullish`；收盘 80890.40，高于 SMA20 78164.08，SMA20 高于 SMA60 71221.30，近 1/2/7 日收益为 +5.85%/+6.15%/+4.74%；
- 当前行动候选：已有现货偏持有；空仓多头候选优先，但入场风险单独评估；当前条件已成立，不要求先等一次突破；
- 交易权限：`false`；概率：`unavailable`，没有把规则方向冒充胜率；
- 衍生品：OKX/Binance 当前 transport/TLS/connect error，`derivatives_crowding` 仍是执行级阻断；宏观传导、跨资产确认也未完成语义核验；
- 该 run 证明“方向观点与执行门分离”已经贯通 API/UI，不证明综合交易策略、样本外胜率或自动交易已经交付。
 
 ## 7. 停止线与产品生死线
 
 继续开发的必要条件：
 
 - P0-A/B 完成后，真实 run 能同时给出方向、行动候选、反方、缺口影响和执行门；
 - 至少连续多次新幂等请求使用新快照；
 - provider 失败被定位为可见的运行限制；
 - 回放数据能复算同一方向结果。
 
 如果完成 P0-A/B 后，使用冻结的、时间隔离的数据仍然无法在成本和回撤约束下优于简单买入持有或明确的无交易基线，则应把产品定位为证据化研究/风险提示，停止包装成高胜率交易产品。此判断需要数据验证，不能靠界面或 LLM 叙述掩盖。
 
 ## 8. 本轮验收命令
 
 ~~~powershell
 uv run pytest tests/research/test_market_snapshot_admission.py -q
 uv run pytest tests/research/test_research_api.py tests/research/test_daily_outlook_projection.py -q
 uv run pytest tests/orchestration/test_agentic_research_graph.py::test_bounded_research_keeps_directional_hypothesis_but_caps_execution_value -q
 ~~~
 
 通过标准：
 
 - 最新建议新 key 不复用旧 event/run；
 - 日线方向存在时，action summary 不再用“证据或复核未通过”覆盖主方向；
 - trade_authorized 仍为 false；
 - 缺口影响清楚；
 - bounded research 保留研究方向，但执行 Forecast 仍 fail-closed。
 
这份计划的 P0-A+B vertical slice 已于 2026-09-19 完成并通过真实固定入口验收：方向/执行分离已写入原生 contract，`daily_direction` 与 `execution_candidate` 的证据门已分开，并已接通 mapper、graph、commit 和 UI。当前停止扩展外围功能，下一顺序只处理 P0-C provider 可靠性、P0-D 宏观/跨资产 typed evidence、P0-E 时间外校准；在这些完成前不宣称“交易可执行”或“概率已验证”。

P0-A+B 的完成不等于整个交易产品完成。最终验收 Run 见 `docs/plan/CORE_CHAIN_DEV_LOG.md`：方向层以日频规则基线输出偏多和行动倾向，执行层仍明确 blocked；这是当前正确的产品边界，而不是把缺口重新包装成无条件买卖建议。
 
