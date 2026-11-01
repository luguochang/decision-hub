# 预测复盘、宏观事件日历与持续研究改造方案

日期：2026-09-19  
范围：`decision-hub` 的 BTC/加密宏观研究固定入口  
目标：让每一次输出都能回答“依据是什么、预测是什么、何时复核、复核结果是什么”，并在证据不足时明确限制，而不是用一个未经验证的概率替代事实。

## 1. 先给结论

当前产品有一条可工作的研究链，但还不是一个已经证明胜率超过 60% 的预测系统。

- 当前研究会保存 Evidence、事实、因果链、分周期候选和审查轨迹；7 日方向是 `daily_strategy` 的规则基线，当前运行的概率状态是 `unavailable` 或 `uncalibrated`，不能把它解释成胜率。
- 预测结果已经有数据库表：`forecasts`、`outcomes`、`evaluations` 和 `research_value_evaluations`。到期结果由 `DueOutcomeService` 在 realtime tick 中处理，Brier、净收益和方向正确性可以被计算。
- 页面现在把研究结论、证据轨迹、预测 ledger、前视审计和复查计划分开显示，使用者可以区分预测是否早于结果、标签是否可用以及是否只是跟随 trailing trend。
- `next_review_at` 仍是报告里的建议时间，但 durable `review_schedules` 才是调度权威；worker tick 会领取到期计划并幂等创建 `scheduled_recheck` 子 Run。
- 当前 `packs/crypto_macro/pack.yaml` 只允许 3 个 Evidence round、24 次工具调用、480 秒总时限。把模型循环直接扩大到 25 或 50 轮会增加重复证据、相关来源重复计数、成本和超时，不能直接提高胜率。

因此本次改造的目标不是“让模型永远搜索”，而是建立一条可验证的前视预测闭环：

> 固定时间截面 → 取点时可用证据和事件日历 → 形成主链与反链 → 在结果时间之前写入不可变预测 → 到期自动取标签 → 计算命中率、Brier、净收益和延迟 → 只有经过时间切分验证并校准的模型才允许显示概率。

60% 只能是经过样本外验证后的准入门槛，不能是单次报告的承诺。

## 本轮已完成与边界

- [x] M1 页面展示已有 `ResearchValueEvaluation.outcomes`，逐条区分待到期、标签不可用、估计标签和已复核，并显示方向、净收益、Brier。
- [x] 前端测试 4 个文件、16 个测试通过；生产构建和 `git diff --check` 通过。
- [x] 产品启动脚本已默认打开官方 BLS 日历发现开关；需下次重启产品进程后生效。该开关只提供事件时间，不会产生 consensus 或实际值。
- [x] M2 durable scheduled recheck 已实现：`review_schedule` 持久化 planned/admitted/completed/skipped/failed，四类触发器幂等创建子 Run，并由 worker tick 领取。
- [x] M3 Fed/ECB/BOJ/BLS 统一事件实体已实现；scheduled/released/revised 与 prior/consensus/actual/vintage 分离，官方来源缺失的 consensus/actual 保持为空。
- [x] M4 前视输入快照与反追涨审计已实现：Forecast 固化 `as_of`、snapshot/fingerprint、strategy version、feature snapshot 和 evidence cutoff，并提供 audit API/UI。
- [x] M5 样本外校准准入已实现：按时间切分并设置 horizon gap，固化 Brier/log loss、Wilson 下界、净收益、回撤、覆盖率和可靠性分箱；只有持久化且版本/周期精确匹配的 passed receipt 才能标记 calibrated。

本轮完成的是可复核的产品闭环和安全 Gate，不是对当前策略胜率的证明。当前没有为真实策略伪造一个通过 60% 门槛的 receipt；没有 receipt 的方向仍显示为研究方向或 `no_trade`，概率状态保持 `unavailable/uncalibrated`。

## 2. 现状到代码的对应关系

| 能力 | 当前实现 | 当前边界 |
| --- | --- | --- |
| 证据与根因链 | `agentic_research_graph.py`、`ResearchEvidenceService`、`ResearchFactStore`、`ResearchSynthesisCandidate` | 已保留证据、事实、主链/反链和复核轨迹；实时来源质量和共识值仍取决于授权 provider |
| 预测持久化 | `ForecastRecord`（`forecasts`） | 已保存规则/模型版本、输入 snapshot/fingerprint、as-of、feature snapshot 和 evidence cutoff，并由 audit API/UI 展示 |
| 到期标签 | `OutcomeRecord`、`EvaluationRecord`、`DueOutcomeService` | 依赖可用的市场窗口；缺标签时必须显示 pending/unavailable，不能当作未命中 |
| 研究价值 | `ResearchValueEvaluationService`、`GET /v1/research/runs/{id}/value-evaluation` | 已能算证据覆盖、引用追溯和 outcomes；缺标签时仍只能显示 pending/unavailable |
| 调度 | `ReviewScheduleService`、`RealtimeScheduler.tick()`、`apps/hub_worker/main.py` | 已生成 durable 计划并幂等创建子 Run；实际触发仍受 worker 在线、租约和预算约束 |
| 宏观事件 | `official_feeds/calendar.py`、BLS/Fed/ECB/BOJ presets、`MacroEventRecord` | 已统一事件实体和 append-only vintage；缺失的 consensus/actual 保持为空 |
| 概率 | `forecast_calibration.py`、`StrategyCalibrationRecord`、Gate | 已有时间序列样本外评估、校准曲线分箱和版本准入；仍禁止把 LLM 主观数值直接升级为 calibrated |
| 研究轮次 | pack 配置 8 rounds/48 calls；策略 policy 仍有硬上限 | 采用新颖性、coverage、冲突、成本和 deadline 约束的自适应循环，不承诺无上限搜索 |

## 3. 可交付目标与验收口径

### 目标 A：每次预测可复盘

对每个 `run_id + horizon` 必须能看到：

1. 预测写入时间和 `as_of` 截止时间；
2. 当时的方向（long/short/neutral/no-trade）和概率状态；
3. 规则或模型版本、输入数据指纹、Evidence 引用；
4. 到期时间、真实标签的来源和质量状态；
5. 方向是否正确、收益、费用/滑点、Brier；
6. 尚未到期、数据不可用、标签估计和正式标签必须区分。

验收：页面不能只显示“方向未验证”，而要显示“预测记录已写入/尚未到期/已复核/标签不可用”的明确状态。

### 目标 B：复查真的会触发

为每个已保存的 horizon 创建幂等的 `scheduled_recheck` 计划。触发条件至少包括：

- `next_review_at` 到点；
- 关联宏观事件进入预告、发布、修订三个状态；
- 关键失效条件被新 Evidence 命中；
- 预测到期并完成 outcome 后的复盘任务。

同一个 `parent_run_id + horizon + trigger_window` 只能创建一个子 Run。调度器每个 tick 只负责领取已到期计划，研究执行仍受预算、租约和失败重试约束。复查计划的状态必须可见：planned、admitted、completed、skipped、failed。

### 目标 C：事件日历成为输入而不是装饰

建立统一事件实体：

```text
macro_event
  event_id
  family                  # FOMC / ECB / BOJ / CPI / NFP / PPI / PCE / GDP ...
  scheduled_at_utc
  source_url
  source_authority        # official
  prior_value
  consensus_value         # 可为空
  actual_value             # 发布后才有
  vintage_at               # 当时可见的版本
  revision_flag
  surprise                 # actual - consensus；无 consensus 时为空
  surprise_z               # 按历史发布波动标准化；不足时为空
  status                   # scheduled / released / revised / cancelled
```

事件时间和实际发布正文优先使用官方来源：Federal Reserve 的 FOMC 日历、ECB Governing Council 日程、BOJ MPM 日程和 BLS 发布日历。预期值是另一类来源，不能把官方发布页的 prior 当成 consensus。利率路径可使用 CME FedWatch 的期货隐含概率，并记录工具假设与时间戳；历史宏观数据使用 FRED/ALFRED 的 initial vintage，避免回放时偷看修订值。

### 目标 D：概率有证据才显示

三个 horizon 的标签要在预测写入前固定定义：

- 30m：未来 30 分钟中点价相对 `as_of` 的收益超过成本缓冲；
- 24h：未来 24 小时收盘收益超过成本缓冲；
- 72h/7d：同理，使用明确的交易日历和收盘定义。

训练和评估采用按时间顺序的 expanding/rolling split，并设置不小于预测 horizon 的 gap，避免训练样本看到测试期信息。概率只在以下条件同时满足时标记 `calibrated`：

- 预测样本达到预先规定的最低数量，且至少有三个独立的样本外时间折；
- 方向准确率、成本后净收益、最大不利路径和覆盖率达到策略门槛；
- Brier/log loss 与基准比较通过，可靠性曲线没有明显偏差；
- 结果不是由单一行情 regime 或一小段事件窗口贡献；
- 模型版本、特征版本、数据快照和评估报告已固化。

未达到条件时，产品仍可给出“偏多/偏空/区间/不交易”的研究方向，但概率状态必须是 `unavailable` 或 `uncalibrated`，不能显示 60%、70% 之类的伪精确数字。

## 4. 目标架构

```text
官方日历/发布页 ─┐
FRED/ALFRED vintage ─┼─> MacroEventAdapter ─> macro_event ledger
CME FedWatch/调查预期 ─┘                               │
                                                       v
市场现货/衍生品/跨资产/叙事 ─> PointInTimeSnapshot ─> Evidence & Fact Store
                                                       │
                                                       v
                         自适应研究循环（新颖性、冲突、缺口、成本、截止时间）
                                                       │
                                                       v
                 主因果链 + 反因果链 + 规则/模型候选 + 前视预测快照
                                                       │
                ┌──────────────────────────────────────┴──────────────────┐
                v                                                         v
        Artifact/ForecastRecord                              ReviewSchedulePlanner
                │                                                         │
                v                                                         v
        OutcomeDueProcessor <────── market window ────── RealtimeScheduler
                │                                                         │
                └──────────────> Evaluation/Brier/return ─────────────────┘
                                                       │
                                                       v
                         Research Page：结论 + 依据 + 预测 ledger + 复查时间线
```

“模型”只负责在固定快照上产生候选预测；LLM 负责研究问题分解、证据比较和因果链表述。数值概率、是否 calibrated 和是否有交易权限由确定性评估器和 Gate 决定。

## 5. 研究循环设计：25/50 次不是默认答案

把循环拆成两个预算：

1. **证据扇出**：同一轮可以并行查询多个互不依赖的来源，例如官方事件、跨资产、衍生品、现货、历史标签、反方叙事。每次查询必须绑定 requirement、截止时间和去重键。
2. **串行推理**：只有发现未覆盖的 hard gap、来源冲突、事件 surprise 未解析或反方链缺失时才新增一轮。每轮结束记录新增 Evidence 数、覆盖变化、冲突解决和下一步问题。

建议第一版预算：`max_evidence_rounds=8`、`max_tool_calls=48`、总时限 8 分钟，但要求连续两轮无 coverage/novelty 增长就停止。25–50 可以作为低成本检索调用的总扇出上限，不能作为 50 次模型推理承诺。每次停止必须写入 `stop_reason`：sufficient、no_progress、deadline、cost_budget、tool_budget 或 critical_data_unavailable。

禁止以下循环：重复访问同一 URL、把同一新闻转载当成多个独立证据、在看到未来价格后重写历史预测、为了满足轮次而生成没有新事实的段落。

## 6. 实施顺序

### M1：预测 ledger 可见（先交付）

文件范围：

- `apps/decision-desk/src/research/ResearchPage.tsx`
- `apps/decision-desk/src/styles.css`
- `apps/decision-desk/src/research/ResearchPage.test.tsx`
- 已有 `GET /v1/research/runs/{run_id}/value-evaluation`

工作：把 `ResearchValueEvaluation.outcomes` 按 horizon 显示；明确 pending/unavailable/estimated/observed；显示 forecast id、到期时间、direction_correct、return、net return、Brier 和 observed_at。此阶段不改变模型，不伪造准确率。

验收：提交一个新的 BTC 研究 run，页面能看到三条预测的记录状态；等待一个短 horizon 到期后，能看到 outcome 或明确的市场窗口不可用原因。

### M2：复查调度真正落库（已完成）

文件范围：

- `packages/kernel/decision_hub_kernel/application/research_observability.py`
- 新增 `packages/kernel/decision_hub_kernel/application/review_schedule.py`
- `packages/kernel/decision_hub_kernel/persistence/db.py` 与迁移
- `packages/kernel/decision_hub_kernel/application/scheduler.py`
- `apps/hub_api/main.py`、`packages/query_views/research/service.py`

工作：从已提交 artifact 的 horizon 生成幂等计划；scheduler tick 领取到期计划并 admission 为 `scheduled_recheck`；把触发原因、父 Run、horizon、trigger hash 写入 trace 和 detail。`next_review_at` 仍可展示，但只有 durable review plan 才能声称“会自动复查”。

验收：测试时钟推进到复查时间，第一次 tick 只创建一个子 Run；重复 tick 不重复创建；子 Run 能在列表和父子链中看到。

### M3：官方宏观事件统一接入（已完成）

文件范围：

- `packages/source_adapters/official_feeds/calendar.py` 与 `presets.py`
- 新增 `packages/provider_adapters/macro_market/event_calendar.py`
- `packs/crypto_macro/evidence/source_manifest.yaml`
- `apps/hub_worker/composition.py`、`apps/hub_worker/main.py`
- 迁移和事件实体 schema

工作：先接官方时间与发布状态，再接预期值；Fed/ECB/BOJ/BLS 的来源适配器共用事件 schema。所有时间转换为 UTC，并保留原始 source URL、抓取时间和内容 hash。若 consensus 不可用，事件仍可触发研究，但 surprise 不得被推断。

验收：未来事件能在 Inbox 中以 scheduled 状态出现；到发布后变成 released；一个事件只生成一个预告和一个发布触发窗口。

### M4：前视数据快照和反追涨验证（已完成）

文件范围：

- `packages/kernel/decision_hub_kernel/decision/daily_strategy.py`
- `packages/kernel/decision_hub_kernel/application/commit.py`
- `packages/provider_adapters/historical/`
- `tools/research_evaluation/build_splits.py`、`audit_directional_value.py`
- 现有 replay/evals 测试

工作：将 `as_of`、data hash、strategy version、feature snapshot、evidence cutoff 固化到 strategy receipt/forecast ledger；评估器只读取当时已发布的数据。增加与“当前趋势跟随”基线的对比，报告提前量、方向翻转、事件前后命中率和成本后收益。

验收：任何 outcome 之后加入的价格或修订数据不能改变已保存的输入 hash；评估报告能回答“预测是在价格变化前还是变化后写入”。

### M5：样本外校准和策略准入（已完成）

文件范围：

- 新增 `packages/evals/forecast_calibration.py`
- `packs/crypto_macro/evaluations/`
- `packages/kernel/decision_hub_kernel/decision/gate.py`
- Research 页面概率状态和评估面板

工作：按 horizon 生成时间切分报告、Brier/log loss、可靠性曲线、基准比较、置信区间、净收益和最大不利路径。只有通过准入的 `strategy_version` 才能返回 `probability_status=calibrated`。其余保持方向输出但概率为空或未校准。

验收：没有通过评估的模型无法把状态写成 calibrated；有通过证据的版本能在页面看到评估时间、样本数、折数和版本号。

## 7. 事件影响与因果链的固定模板

每个宏观事件研究必须按同一结构落盘：

```text
事件：是什么、何时发布、官方来源是什么
预期：市场在 as_of 时的 consensus / rate-implied path
前值：上一期 initial vintage；是否存在 revision
当前状态：美元、实际利率、收益率曲线、风险资产、BTC 现货/衍生品
传导链：事件 -> 利率/美元/流动性 -> 风险偏好 -> BTC
主场景：actual 相对 consensus 的区间及方向
反场景：爆冷、修订、流动性冲击、拥挤反转
验证：发布前后的具体指标和时间窗
失效：哪个事实出现后必须撤销方向
```

该模板不把“事件发生”直接等价于“BTC 上涨/下跌”。方向必须由事件 surprise、当时 regime、跨资产确认和拥挤度共同决定；如果 consensus 缺失，输出只能是情景分析，不得伪造 surprise 概率。

## 8. 当前输出如何解释

现阶段可以给出有依据的研究方向，但不能声称已达到 60%：

- 依据来自可追溯 Evidence、日线规则 receipt、现货/跨资产/衍生品等输入以及主链和反链；
- 7 日方向目前是规则基线，存在滞后风险；不能把它当成提前预测能力；
- 24h/72h 若没有校准模型只能是条件情景，不能包装成概率；
- “不交易”目前是执行 Gate 的保护状态，不等于市场方向一定不明；页面应同时显示研究方向、行动候选、阻断原因和复查计划；
- M1/M2 已完成，用户现在能看到预测复盘和 durable 复查计划；当前版本仍不是自动交易产品，因为真实策略尚无通过 60% 门槛的样本外 receipt。

## 9. 外部资料与实现依据

- Federal Reserve 官方 FOMC 日历：<https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>
- ECB Governing Council 官方日程：<https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html>
- BOJ 官方 MPM 日程：<https://www.boj.or.jp/en/mopo/mpmsche_minu/>
- BLS 官方 2026 发布日历：<https://www.bls.gov/schedule/2026/>
- FRED/ALFRED observations 与 vintage API：<https://fred.stlouisfed.org/docs/api/fred/series_observations.html>
- CME FedWatch 方法说明与 API：<https://www.cmegroup.com/articles/2023/understanding-the-cme-fedwatch-tool-methodology.html>、<https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html>
- scikit-learn 时间序列切分：<https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html>
- scikit-learn 概率校准：<https://scikit-learn.org/stable/modules/calibration.html>

这些资料只支持数据时点、事件日程、隐含概率方法和评估方法；它们不构成 BTC 买卖信号。任何交易方向仍必须经过本项目自己的前视快照、标签和样本外评估。

## 10. 禁止事项

- 不把单次 LLM 主观概率写为 calibrated；
- 不用修订后的宏观数据回放历史预测；
- 不把同一新闻的多个转载计为独立证据；
- 不把 `next_review_at` 的展示当成已接入自动调度；
- 不以增加模型轮数替代缺少的市场数据、共识值或标签；
- 不在没有样本外统计和成本后结果时承诺胜率超过 60%；
- 不因为产品需要一个结论，就删除反方链、数据缺口或“无法评估”的状态。

## 11. 本轮实现与自测记录

实现记录、文件边界、失败根因和验收证据见 [`FORECAST_RESEARCH_LOOP_EXECUTION_LOG_2026-09-19.md`](FORECAST_RESEARCH_LOOP_EXECUTION_LOG_2026-09-19.md)。本轮质量门：

- Python：`767 passed, 4 skipped`（`uv run pytest -q`）；
- Ruff：`uv run ruff check .` 通过；
- Pyright：变更核心模块 `0 errors, 0 warnings`；
- 前端：Vitest `16 passed`，TypeScript/Vite production build 通过；
- `git diff --check` 通过。

这些是离线和本机可复现证据。它们证明闭环、持久化和 fail-closed 约束可运行，不证明当前 BTC 策略已经达到 60% 胜率或可以自动下单。
