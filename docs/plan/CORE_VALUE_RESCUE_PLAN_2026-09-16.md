# Decision Hub 核心价值抢救方案

> 日期：2026-09-16  
> 状态：基于当前代码、两轮真实运行和既有三份设计文档形成的实施计划；本文件先冻结方案，不启动外围开发。  
> 唯一目标：让系统稳定给出“当前最有证据的根因判断、反向解释、分周期市场展望和明确的可信边界”，再用样本外数据决定它是否有资格升级为交易信号。

## 0. 结论先说清

项目现在不是“模型永远分析不出来”，而是三个不同问题叠在一起：

1. **研究结论被交易 Gate 遮住了。** 真实运行已经生成并修订了根因 thesis，但用户首先看到的是“不能交易”，因此感知上等于没有结论。
2. **当前代码从制度上禁止方向结果晋级。** `research_action_summary.trade_authorized` 在 schema 中是常量 `false`；`_research_result_is_publishable` 又明确要求 30m/24h/72h 都不能是 `long/short`；`evaluate_gate(... directional_value_validated=False)` 没有接入任何真实的校准凭证生成链。按现状继续调 prompt，最终仍会停在 `no_trade`。
3. **资料数量不少，但缺的是能区分机制的数据语义。** 当前运行已有现货、资金费率、持仓量、盘口和历史窗口；问题是很多数据是一个时点的 level，不能区分“多头去杠杆”“现货主动卖压”“宏观 risk-off”“技术性破位”。根因需要同一窗口的 delta、flow、venue 对照和反事实，而不是再多抓十条快照。

所以核心抢救路线不是继续加外围工程，也不是简单换成一个 Deep Research 产品。正确顺序是：

```text
先让研究结论可见且不被 no_trade 覆盖
→ 把 reviewer/chain break 变成结构化可执行目标
→ 补能区分根因机制的同步窗口数据
→ 形成可版本化的根因图与证据强度
→ 最后做独立的方向信号与校准
```

产品是否继续投入分两条生死线：

- **研究价值生死线**：代表案例能否给出具体、可核验、能被反证的主根因，而不是泛泛的“可能受多因素影响”。
- **方向价值生死线**：冻结的信号在留出数据上能否稳定胜过简单基线并覆盖成本。未通过时产品可以保留为研究助理，但不能宣传交易预测；研究价值也未通过时，停止自研核心并评估接入现成研究产品。

## 1. 当前真实状态，而不是计划想象

最新真实运行 `run_74992bc99715451897801ca5210e5a62`：

- 真实 DSH + Research MCP + Hub + research worker；
- 2 轮研究、3 个 DSH subagents；
- 16 条 accepted Evidence、3 条 stale Evidence；
- hard coverage 100%，soft coverage 33%；
- 第一版 30m 为 `short` 候选；reviewer 要求验证同窗持仓、盘口、宏观与跨市场机制；
- 第二轮新增证据后修改 thesis，并把 30m 收敛为 `no_trade`；
- 主判断是“短线风险释放/去杠杆解释较强，但不足以排除趋势反转”；
- 由于 reviewer 仍有保留且剩余问题没有合法 capability route，停止为 `no_actionable_path`。

这证明以下能力已经存在：真实工具调用、真实子 Agent、证据入账、两轮补证、thesis revision、反证审查和有界停止。问题在于它们还没有组合成用户能直接消费的产品结论。

### 1.1 当前存在的逻辑死锁

| 位置 | 当前规则 | 实际后果 |
|---|---|---|
| `agentic_research.schema.yaml` | `trade_authorized` 恒为 `false` | 产品永远无法表达任何授权态，即使未来信号通过 |
| `commit.py::_research_result_is_publishable` | 所有 horizon action 必须不是 long/short | 有方向的结果永远不能成为 publishable research |
| `gate.py::evaluate_gate` | directional 需要 `directional_value_validated=True` | 规则合理，但当前没有生产该凭证的实现 |
| Commit fail-close | 未 publishable 时 Forecast 被覆写为 no_trade/0.5 | 安全，但把“研究方向候选”和“可执行动作”混成一个字段 |
| Action summary | 顶部优先展示 do_not_trade | 已经形成的根因 thesis、反链和改变过程不成为第一结论 |

因此目前不能用“再优化一下 prompt”解释问题。必须先修状态模型：**研究判断、方向候选、信号验证、交易授权是四种不同对象。**

### 1.2 当前根因链为什么仍不够强

现在的 `CausalCase` 是带 Evidence 引用的线性 link 列表，能描述一条链，但还不能可靠回答：

- 哪个根因在候选集合中排名第一；
- 每条边是被什么事实支持、被什么事实反对；
- 哪个新证据真正改变了根因排序；
- reviewer 的限制属于哪个 claim/horizon；
- 同一观察是否同时被多个假设解释；
- 结论是“证据缺失”还是“现有证据更支持反链”。

当前 reviewer 在真实运行里又生成了 `REQ-30M-DIRECTIONAL-CONFIRMATION` 一类未在 Pack 注册的 ID。kernel 正确拒绝执行，但这说明 review contract 仍是自然语言愿望，不是可执行的 chain break。

### 1.3 不是没有使用 Deep Agent

当前 DSH 已经使用 lead model、工具循环和独立 reviewer subagent，真实运行启动了 3 个 subagents。它属于**有界 Deep Research**，只是没有采用某个名叫 Deep Agents 的框架。

瓶颈不在 Agent 数量：

- 更多 Agent 不能绕过永远禁止 long/short 的 Gate；
- 更多搜索不能把单点 OI 自动变成 OI delta；
- 更多 reviewer 不能把自由文本 limitation 自动变成已注册 requirement；
- 更多模型自信不能替代样本外校准。

后续仍保留 DSH 作为模型/工具/subagent harness，LangGraph 作为持久化和有界循环，Hub 作为 Evidence、Gate 和权限内核。需要吸收 Deep Research 的 planner/worker/verifier 模式，不需要重写一套 Agent 平台。

## 2. 目标输出重新定义

一次研究必须同时产生四层输出，UI 和 contract 不得再复用一个 `action` 字段表达四种含义。

### 2.1 Research Verdict：用户首先看到的结论

```text
dominant_hypothesis
confidence_grade: strong | moderate | weak | unresolved
why_now
decisive_evidence_refs
contradicting_evidence_refs
alternative_hypotheses[]
chain_breaks[]
what_would_change_the_verdict
```

即使最终不能交易，也必须明确回答：“目前最有证据的是哪一个解释、为什么、相反解释是什么、最薄弱的一环是什么。”

### 2.2 Research Outlook：模型研究得到的市场情景

30m/24h/72h 分别保存：

- `research_bias`: bullish / bearish / neutral / unresolved；
- `raw_confidence`，明确标注未校准；
- trigger、invalidation、expiry；
- evidence refs 和 blocking chain breaks。

这个对象不能被 Commit 覆写成 no_trade。它是研究产物，不是交易许可。

### 2.3 Signal Verdict：确定性信号引擎的判断

```text
eligible | not_eligible | unavailable
signal_version
feature_snapshot_hash
calibration_receipt_ref
expected_edge_after_cost
validation_metrics
```

没有凭证时显示 `not_eligible`，但不能删除 Research Outlook。

### 2.4 Trade Authorization：执行权限

它由仓位、风险限额、账户、人工/策略授权决定。首期仍保持 `false` 完全合理；但这是执行边界，不是研究结论。未来即使改为可授权，也必须作为独立 contract 和 service，不由 LLM 决定。

## 3. 分层精修方案

### L0：问题冻结与研究任务类型

当前同一套 Pack 同时承载宏观事件研究和“BTC 当前为何下跌”问题，语义不匹配。宏观事件适合 `t-5m/t+1m`；当前市场快照需要 5m/30m/4h/24h 的同步窗口。

新增 `ResearchQuestion`：

```text
question_type: event_impact | current_move | historical_analogue
subject
as_of
observation_windows
baseline_windows
requested_horizons
candidate_mechanisms
```

实施要求：

- 自动从用户输入和当前行情确定问题类型；
- `current_move` 生成明确的价格变化区间，不再研究一个没有起止时间的“当前回落”；
- 同一 Run 内 `as_of`、窗口和标的冻结；后续对话产生 revision；
- 不要求用户手工提供技术参数。

### L1：机制型事实与数据

为 `current_move` 建立独立 requirement profile，按“能否区分机制”设计数据，而不是按 endpoint 设计：

| 候选机制 | 必需事实 | 当前缺口 | 首选补法 |
|---|---|---|---|
| 多头去杠杆/清算 | price return、OI delta、funding/basis、liquidation 或 taker flow 同窗变化 | 有 OI level，时间差分和主动流不稳定 | Binance/OKX 5m OI history、funding history、taker buy/sell；统一计算 delta |
| 现货主动卖压 | 现货回报、成交量异常、主动买卖差、跨 venue 一致性 | 有 ticker/volume，缺主动流和异常基线 | trade/klines 聚合；至少 OKX+Binance 两所 |
| 盘口流动性真空 | 多档 book imbalance、spread、depth change、恢复速度 | 主要是单次盘口 | 采样两次以上并计算时间窗变化；不把单帧深度当根因 |
| 宏观 risk-off | DXY/利率/股指/波动率与 BTC 同窗变化 | FRED 日频过期，不适合分钟级 | 当前市场问题先降为背景；需要时接有许可的 intraday provider |
| 技术/市场结构破位 | 多周期收益、波动、成交量、关键区间和后续跟随 | 已有 1h 历史，但缺对齐的前后窗口 | 先使用免费交易所 1m/5m/1h Kline，离线计算 |

第一阶段不购买分钟数据。Binance 官方市场接口提供 OI history、taker buy/sell、long/short ratio 等有限近期窗口；OKX 官方接口提供订单簿和 Kline，可先验证根因区分度。只有当免费数据无法覆盖足够的历史留出样本时再决定是否购买。

所有事实统一为：

```text
metric, value, unit, venue, window_start, window_end,
baseline_value, delta, zscore_or_percentile,
observed_at, available_at, evidence_ref, calculation_version
```

Gate 不再允许“一个最新值”满足需要 delta/window 的 requirement。

### L2：检索与补证

把下一轮从自然语言“再查一下”变成结构化 `RetrievalAttempt`：

```text
attempt_id, round, target_claim_id, requirement_id,
query_or_endpoint, entity, fields, window, provider,
expected_disambiguation, result_refs, outcome
```

精修规则：

- 相同 requirement 可以重试，但 query/fields/window/provider 必须至少一项变化；
- 每次检索必须声明它要区分哪两个假设；
- search snippet 只能发现来源，正文/数据读取后才能支持 claim；
- 没有 route 时记录 `unavailable`，不能由 reviewer 发明新 ID；
- capability map 由 kernel 提供给 reviewer，reviewer 只能从枚举中选择；
- `no_actionable_path` 必须列出每个 chain break、尝试过的 route 和为什么不可得。

### L3：根因 Claim DAG

将当前线性 `CausalLink[]` 增量升级为：

```text
ClaimNode:
  claim_id, type, statement, mechanism_id, horizons,
  support_refs, contradict_refs, evidence_strength,
  status, revision, invalidation

ClaimEdge:
  from_claim, to_claim, relation,
  support_refs, contradict_refs, status

Hypothesis:
  hypothesis_id, mechanism_id, claim_path,
  support_score, contradiction_score,
  missing_requirement_ids, rank, status
```

候选机制集合由领域 Pack 定义，模型可以组合和新增候选，但必须映射到可读的 mechanism family。综合器必须输出 ranked hypotheses，而不是只写一段 thesis。

Evidence strength 由 kernel 根据来源、窗口、独立性、冲突和语义完整性计算；LLM 只负责解释，不自行给自己打高分。

每轮必须记录：

- 哪个 hypothesis 上升或下降；
- 哪个 claim/edge 被确认、否定或仍未知；
- 哪条新增 Evidence 导致变化；
- 哪个 horizon 因此改变；
- 没变化时明确记录 unchanged。

### L4：有界 Deep Research 工作流

每次 current-move 研究采用以下角色，按问题需要创建，不固定全部并行：

1. **Planner/Lead**：冻结候选机制和最小区分问题。
2. **Microstructure worker**：现货主动流、盘口、成交量和跨所一致性。
3. **Derivatives worker**：OI delta、funding、basis、long/short、taker flow。
4. **Macro/regime worker**：只在当前问题确实需要时执行宏观/历史环境。
5. **Falsifier**：寻找最强反例，输出结构化 target claim 和合法 requirement IDs。
6. **Synthesizer**：只读取固定 Evidence snapshot 和 worker receipts，产生新 hypothesis revision。

外层由 LangGraph 保持确定性：

```text
freeze → plan hypotheses → parallel acquire
→ deterministic fact/coverage assessment
→ synthesize ranked hypotheses
→ falsify
→ kernel chooses one chain break
→ targeted acquire → revise
→ stop or finalize
```

最多两类独立 worker 并行，默认 2 轮、最多 3 轮。增加 Agent 的条件是子问题可独立、返回结构化结果且能影响一个明确的 claim；否则不创建。

### L5：Review 契约

Reviewer 输出改为：

```text
target_claim_ids[]
resolution: accepted | revised | bounded | rejected
blocking_findings[]:
  finding_id, target_claim_id, requirement_id,
  horizon_scope, severity, required_evidence_kind
non_blocking_limitations[]
```

`accepted` 不再要求世界上不存在任何 limitation；要求的是没有未解决的 blocking finding。开放世界研究永远可能有局限，当前“limitations 必须为空”的条件会让 accepted 极难出现。

review 必须绑定被审版本 hash。综合修订后，旧 review 作为历史保留，新版本需要新 receipt 或明确的增量复核。

### L6：三个 Gate，不再互相覆盖

1. **Evidence/Research Gate**：事实与根因判断是否足以形成可读研究结论。
2. **Directional Value Gate**：冻结信号是否通过样本外验证和成本测试。
3. **Trade Authorization Gate**：账户和风险策略是否允许执行。

Research Gate 可以发布 bearish/bullish 的“研究展望”，同时 Directional Gate 为 not_eligible、Trade Authorization 为 false。这样既不欺骗用户，也不会把所有有价值的研究都显示成一句 no_trade。

### L7：结果页面

页面第一屏顺序固定为：

1. 当前最有证据的根因；
2. confidence grade 和一句原因；
3. 两个主要反向解释；
4. 30m/24h/72h research outlook；
5. Signal Verdict；
6. Trade Authorization；
7. 关键证据和两轮变化；
8. 剩余 chain breaks 与下一次复查条件。

开发者错误、provider 次数、schema 名称下沉到诊断区。当前 DSH wrapper 调用数与 capability invocation 数必须分别命名，不能在两个位置显示不同数字而不解释。

## 4. 严格实施顺序

### R0：解除“永远 no_trade”的状态死锁

改动范围：contract、Commit、Gate、Query、ResearchPage。

- 增加 Research Verdict、Research Outlook、Signal Verdict、Trade Authorization 四个对象；
- 保留模型的研究方向候选，不再因 signal 未校准而覆写研究产物；
- `trade_authorized` 继续 false，但从研究结论中分离；
- 顶部 headline 使用 dominant hypothesis，不再默认使用“证据或复核未通过”；
- directional Gate 仍 fail-close。

验收：复跑现有 live case，用户第一屏能看到具体根因与反链；同时清楚显示“方向候选未校准、不能执行”。

### R1：结构化根因与 Reviewer 闭环

改动范围：agentic research contract、DSH prompt/result mapper、research loop、review service。

- 引入 ClaimNode/Edge/Hypothesis/Revision；
- reviewer 只能引用 claim ID 和 Pack requirement ID；
- limitation 分类 blocking/non-blocking；
- chain break 精确映射 capability route；
- 每轮记录 hypothesis rank delta。

验收：不能再出现 `REQ-*` 幻想 ID；新增证据必须能解释为何改变或不改变根因排序。

### R2A：金融知识与信息智能闭环

详细设计见 [FINANCIAL_INTELLIGENCE_PROBABILITY_UPGRADE_2026-09-17.md](./FINANCIAL_INTELLIGENCE_PROBABILITY_UPGRADE_2026-09-17.md)。

- 将旧 `crypto-macro-decision` 中的宏观、跨资产、拥挤度、priced-in 和 EV/R 知识迁入 Domain Pack，但不迁移未经校准的固定分数；
- 将输入明确分为事实、观点、情绪、可疑传播和市场反应；
- 增加上游来源追溯和同源叙事聚类，转载量不能虚增独立证据数；
- 增加博主/机构观点 PIT 台账，只有事前、明确、可证伪的预测才能积累历史 skill weight；
- 增加 macro regime、cross-asset、crypto flow、narrative forensics、event scenario 和 market-pricing falsifier 六类专业视角；
- 分开根因概率、收益方向概率和行动成功概率；未校准阶段只能显示相对支持度。

验收：高热度同源消息不会提高事实权重；主机制、次机制和最强反方均可追溯；系统能明确说明信息是否已定价；任何显示为概率的方向输出必须有内核签发的 calibration receipt。

### R2B：current-move 机制数据闭环

改动范围：crypto market Pack、Binance/OKX adapters、fact semantics、sufficiency。

- 建立 current-move 专用窗口和 requirement profile；
- 补 OI delta、taker buy/sell、multi-level book imbalance、volume anomaly；
- 对齐 5m/30m/4h/24h 窗口和 venue；
- 同一指标生成 level/baseline/delta；
- 用两所交易所验证方向一致性。

验收：在 BTC 回落案例中，至少能在“去杠杆、现货卖压、流动性真空、宏观 risk-off、技术破位”之间给出可核验的首要机制与次要机制；无法区分时要指出缺的具体 metric/window，而不是泛称信息不足。

### R3：代表案例价值验收

只使用 5 个冻结案例，不先建设大评测平台：

1. 去杠杆占优；
2. 现货主动卖压占优；
3. 宏观 risk-off 占优；
4. 证据矛盾；
5. 数据确实不足。

通过条件：

- 至少 4/5 给出具体 dominant hypothesis 或有事实依据的 unresolved；
- 所有 material claim 可追溯到 Evidence；
- 时间窗口、单位和 venue 无错配；
- reviewer 能真实改变至少一个错误/过强结论；
- 用户不看 trace 也能理解“结论、原因、反例、还缺什么”；
- 不要求产生交易动作。

若 R3 仍大量输出泛泛而谈，停止继续做 Agent 平台，评估将产品收缩为 Evidence 工作台或接入成熟 Deep Research 服务。

### R4：方向信号与准确率

只有 R3 通过才开始。

- 30m 优先使用免费 5m 数据构造标签；24h/72h 先用现有 1h 数据；
- 固定 feature schema，不把 LLM 文本直接当 feature；
- Dev/Calibration/Holdout 时间隔离，使用 rolling-origin；
- 基线包括 always-neutral、momentum、简单 funding/OI rule；
- 指标使用 Brier、ECE、directional accuracy、coverage、扣成本收益、最大回撤和 CI；
- 生成 kernel calibration receipt；
- Commit 只有在 receipt 匹配 signal/data/version 时才传入 `directional_value_validated=True`。

“最大准确率”不是可验收指标。低覆盖率系统可以通过只挑容易样本获得高命中率；因此必须同时冻结准确率、覆盖率、概率校准和成本后收益。

若三个 horizon 中只有一个胜过基线，只开放该 horizon；不能把 30m 成果外推到 24h/72h。

### R5：交易候选，仍不自动交易

只有 R4 的留出结果通过后，增加 `trade_candidate`，仍需人工/风险策略批准。自动下单、账户接入和仓位控制不在本轮核心抢救范围。

## 5. 第一阶段明确不做

- PPT、角色入口扩展；
- 调度日历和自动触发；
- 更多微服务、消息队列或通用 Agent 平台；
- 漂亮但不改变结论的管理看板；
- 大规模评测框架；
- 在免费机制数据尚未用尽前购买分钟数据；
- 自动交易。

## 6. 推荐的下一个开发目标

前一版把 R0-R2 同时列入一个目标，范围仍然过大，会再次让状态模型、搜索、市场数据、信息聚类和 UI 并行展开。代码复核后，当前唯一目标收窄为 `R3-core-intelligence`，详细定义、八个顺序任务和退出门见 [金融信息智能与概率决策升级方案第 32 节](./FINANCIAL_INTELLIGENCE_PROBABILITY_UPGRADE_2026-09-17.md#32-现在应该锁定的唯一开发目标)。

该目标只完成以下一条纵向链：

```text
真实用户输入
  -> 确定性 gap planning
  -> 单一 web.search + typed market data
  -> verified fetch / Evidence
  -> 最小 origin cluster 去重
  -> 主假设与反方假设 revision
  -> 用户首屏结论与技术回溯
```

同步 OI delta、taker flow 和盘口窗口只补当前案例所需的最小字段；大规模历史数据、社交/KOL、复杂近重复模型和校准概率均留在当前目标退出门之后。未完成一次真实 live Run 的上述纵向验收前，不开始后续阶段。

## 7. 外部方案为什么只吸收模式，不更换底座

- OpenAI Deep Research 的核心价值是复杂多步搜索、互联网与 MCP 私有数据联合、引用和工具轨迹；它不能代替本项目的 Evidence admission、PIT、信号校准和交易权限。
- LangGraph 官方区分 workflow 与 agent，并推荐用 orchestrator-worker 和 evaluator-optimizer 处理可拆分研究与有验收标准的迭代；与当前“确定性外图 + DSH Agent 节点”兼容。
- Deep Agents 提供 planning、subagents、filesystem 和 context management，是 harness，不会自动解决领域事实语义和业务 Gate。
- 交易所官方免费接口已经能提供近期 OI、funding、taker volume、Kline 和 order book；应先验证这些机制数据能否提升根因区分度，再决定购买数据。

因此不建议现在迁移到新框架。先把状态模型、领域事实和结果契约修对；若 R3 仍失败，再比较现成 Deep Research/交易研究方案的实际输出，而不是先做一次高成本框架迁移。
