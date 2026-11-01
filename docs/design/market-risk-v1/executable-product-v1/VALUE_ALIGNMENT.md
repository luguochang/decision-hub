# 两项核心意图：产品行为与不得省略的验收

日期：2026-10-13。本附件明确事前行动、自我改进与全球观点利用的交付含义；生产尚未实现。它补充原27项验收，新增I01–I12，均绑定原W工作包，不新增另一套长期目标。新增候选与数据接入仍遵守原评价协议，不能改已开始试验。

## 1. 用户要的结果

**主动获取可能改变判断的信息，形成有时点的操作方案，跟踪其成立/退出条件，根据结果找可纠正的问题，形成可再次执行的方法，并检验下一次是否改善了操作。**

用户默认只需关注资产、选择适用仓位分支；研究问题、搜证、监测、复查及学习由系统生成。动作可为现在减仓/退出、满足条件后建仓、持有或有明确转变条件的等待。没有概率校准不自动抹掉有依据的研究试用建议；概率未知保持null。当前首版为现货多头/现金的研究建议，不产生真实交易订单。

产品必须同时评价机会捕获和风险降低。只会提示风险、永远等待、只在涨跌后写解释，都不能获得产品价值通过；也不能用强迫建仓数量来制造积极性。70%是具体定义、含成本、带样本与不确定性的额外成绩目标，不能由模型自信分数代替。

## 2. 第一项：如何真正形成自反思、自改进和长期记忆

```mermaid
flowchart LR
  S[新事实/外部观点/事件预期] --> H[主反机制与待核问题]
  H --> R[定向搜证/计算/案例比较]
  R --> A[事前动作/触发/退出/失效]
  A --> M[持续监测与新版本]
  M --> O[独立结果结算]
  O --> F[具体错因与修正候选]
  F --> K[自动生成版本化技能候选]
  K --> T[合同/反例/有无技能对照]
  T --> U[试用/采用/拒绝/回滚]
  U --> R
```

### 2.1 每轮必须留下的业务对象

| 对象 | 必须回答的问题 | 拟实现归属 |
|---|---|---|
| MechanismReview | 哪个事件/预期变化→哪个可观测中间变量→哪种资产影响？时滞、支持、反证、替代机制、已计价证据和未知分别是什么？ | `ports/research_signal.py`新增DTO；DSH synthesis输出；`application/research_signal.py`校验并用既有不可变role原件存储 |
| ResearchQuestion | 哪个缺口可能改变当前动作、概率目标或退出条件？检索什么来源/变量，什么证据可以推翻主假说？ | 既有DSH研究工具/Gateway；记录问题与native调用原件，不建第二个loop |
| ActionPlanRevision | 现在/何时做什么、适用仓位、期限、触发、退出、失效与理由；为何从上一版改变？ | W2/W3现有Action规格；可操作条件由已注册feature计算 |
| ReflectionDelta | 结果与当时预期差在哪里？缺数据、机制错、状态不适用、时机/路径错、执行/交付失败、运气或仍未知？拟改哪个节点，预期改善哪个指标？ | `application/action_outcome.py`触发既有学习通道；不可变role原件，绑定原Plan和Outcome hash |
| SkillVersion | 可重复的核验/搜证/计算/反证/条件生成步骤、触发和排除范围、依赖、原案例与测试 | W5–W7；既有research_recipe组件/archive，加技能回执和经验桥 |

MechanismReview最低合同：`review_id, run_id, cutoff, scope, horizon, nodes[{id, kind:event/expectation/observable/asset_effect, refs}], edges[{from,to, hypothesis, lag, support_refs, counter_refs, testable_feature_refs}], alternative_refs, priced_in_status, unresolved, affected_plan_refs`。所有边是待证传导假说，统计相关或文本解释不自动成为已识别因果。

ReflectionDelta最低合同：`reflection_id, outcome_ref/hash, original_plan_ref/hash, cutoff, error_categories, supporting_refs, counterexample_refs, unresolved, proposed_node_changes, expected_behavior_change, evaluation_target, candidate_version_ref`。没有可复现修正时结论为`no_candidate/needs_data/unknown`，不要求每次盈亏都编一个skill。

### 2.2 自动化的准确边界

1. 结算到期、原机制失效、漏接已登记机会、有效建议未及时交付，均可自动发起反思；成功和失败均进入样本。
2. 学习通道从ReflectionDelta生成候选SKILL/workflow/schema/测试清单，绑定父版本与原件。首交付必须包含**一个由结果自动生成的候选**；手写种子只能证明接线，不能替代这一门。
3. 候选通过合同与针对性正反例后，可在已登记范围进入研究试用和独立对照，下一次正常DSH研究按适用条件选择/读取/执行/引用。候选不自改权限、评分、资产准入或风险政策。
4. 方法技能试用与金融策略认证分别记录。候选可以被拒绝或结果不足；不要求等到金融显著才允许合法研究试用，也不把试用自动升级成已证明收益。
5. 用户看见“本次采用哪条经验、改变了哪个检索/机制/条件、为何适用、结果是否改善”。只显示“已学习”或skill数量不能通过。

### 2.3 大涨大跌回溯怎样服务于下一次

对已讨论的下跌和上涨案例，先核品种、计价、UTC日/滚动24h等窗口与原件，再固定事件前cutoff，对照当时可获得的信息、原观点/动作和真实送达时间。定位漏源、漏机制、延迟、过于保守、错误适用或证据不足；没有旧Run不能补造当时已预测。

回放必须同时含下跌、上涨、假突破、横盘和相反证据，避免学成单向看空。历史模型回放可能已知结局，属于诊断，不能直接授样本外预测成绩；用于形成候选后，效果仍按独立前瞻协议评价。

示例为合成说明：旧判断长期偏多；新增信息提出短期去杠杆风险。系统定向核验资金流、持仓、利率预期与价格结构，给已有多仓分支减险方案，给空仓分支确认后入场方案，持续跟踪反证。结果后发现曾忽略“价格涨而新增资金不足”的机制，自动提出核验该分歧的技能；下一次需在强资金流上涨案例上证明不会机械减仓。示例不证明过去那次涨跌可被准确预测。

### 2.4 长期不遗忘与性能

原件、结果、Reflection、技能版本与使用回执持久化，不依赖聊天上下文。新Run先按资产/期限/时点/适用状态筛选元数据，再按需加载方法、案例和原件；缓存以内容hash/依赖版本为键，不能长期复用旧行情或latest指针。

快速监测继续执行已武装条件，深研究与候选评价走现有独立通道；模型/网络不占SQLite写事务，不为每个技能开进程。记录检索耗时、加载字节、缓存命中、实际调用与stop reason。上下文不足要保留缺口和后续任务，不能静默丢反证或仅返回保守模板；不以新增统一低token限制代替实现。

## 3. 第二项：全球分析帖子怎样真正进入决策

### 3.1 来源分工与本次核实

- 已找到[TradingNEWS](https://www.tradingnews.com/)候选站，栏目含股票、加密、外汇、大宗商品。作为具名观点来源登记；本次只确认公开页面，不声称已验证批量API、持续采集或用户指的唯一服务。
- [TradingView Ideas](https://www.tradingview.com/support/folders/43000547307/)用于市场观点/分歧；[News Flow](https://www.tradingview.com/support/folders/43000594706/)用于新闻来源发现；[官方MCP文档](https://www.tradingview.com/mcp/docs)具体能力按实际账户核验，不能把新闻接入当Ideas接入完成。
- [TradingView社区脚本](https://www.tradingview.com/support/solutions/43000558522-what-are-community-scripts/)包括指标、策略和库。它们可产生方法候选，不能直接视为本产品已验证技能；开源/受保护/仅邀请脚本的可用范围分别处理。
- [Trading Economics日历接口](https://docs.tradingeconomics.com/economic_calendar/streaming/)文档区分actual、previous、revised和forecast，可作为预期差数据候选；本次未实际接入。官方原始发布仍用于核对事实和公开时点。

### 3.2 从帖子到动作的处理合同

```text
来源原件/版本/公开与收到时间
→ 转载聚类（十篇转载不算十份独立证据）
→ 拆出事实引用、推断、预测、情景、推广
→ ClaimSignal：观点主张、条件/期限、反证、影响资产
→ 与现有机制比较：新变量/分歧/催化/条件失效
→ DSH自主提出核验问题，找原始数据及独立反方
→ MechanismReview、ActionPlan/Condition新增或修订
→ 实际结果、观点可用性与方法候选
```

ClaimSignal最低合同：`id, source_id, canonical_url, author, language, content_ref/hash, published_at或区间, first_seen_at, version, supersedes, syndication_cluster, scopes, claim_type, proposition, horizon?, claimed_conditions?, quoted_evidence_refs, rights_ref, extraction_version`。缺原作者期限/价位保持null，不替其补成可评分承诺。

拟新增`packages/provider_adapters/research/opinion_parser.py`（文本→类型化主张）和`packages/kernel/decision_hub_kernel/application/research_signal.py`（资格、去重、问题/机制绑定）；DTO由`contracts/schemas/research_signal.schema.yaml`生成。W0已核实`ResearchDocumentVersionRecord/AnalystClaimRecord`存在，DC002明确复用其version_id/claim_id、立场、条件、期限及独立取证要求；ClaimSignal是这些对象与来源/时间/转载信息的适配视图，不另建同义观点库。原字节复用RawBodyArchive，冻结到具体Run的ClaimSignal/MechanismReview引用快照使用既有不可变RunRoleOutputRecord（`claim_signal.v1/mechanism_review.v1`），不能把现有可更新review状态当历史原件。全局文章身份与转载索引仍缺，按既有source/document接入索引增量补齐，不复制原文库或把观点直接写成认证Fact。SourceIntake触发使用已有事件/研究任务，持久化后可启动Run，无手填研究题目前置。

单条观点引发研究的理由固定记录为：`new_factor / contradictory_evidence / upcoming_catalyst / condition_change / source_revision`；相同hash/转载簇无新信息不重启研究。研究可有两类合法结论：改变具体动作/条件，或有证据地维持并记录忽略原因。没有独立事实支撑时可保留低资格情景和需核条件；不能因帖子多或作者受欢迎直接上调概率。

### 3.3 怎样衡量信息全面且有价值

每资产/期限维护覆盖矩阵：政策/增长/通胀、利率/美元/融资、仓位/资金、供需/盈利、技术/波动/流动性、事件预期/分歧；按DomainPack加资产特有项。每格显示适用性、来源、最后有效时点、独立源数量、缺口和受影响机制。黄金、纳指、恒科的预留矩阵不能冒充已接通。

分别记录：发现与收到延迟、原件核验率、转载重复率、新机制/反证贡献、进入事前动作/条件的引用、可评分率、错误适用、成本后增量和机会代价。作者表现仅按本系统连续收录样本及可评分覆盖统计，不称全网真实胜率。新增观点源以同一信息截止的有/无该源对照诊断；正式信息增量实验另预登记，不与首轮memory消融混为同一次效果证明。

PC在当前方案下面展示“本次新信息与分歧”“支持/反方/未核”“因此改变什么”和来源链接；资料页保留原件、版本与历史结算。单纯增加新闻列表不能完成本要求。

## 4. 12项硬验收与工作包绑定

以下均为**待实现/待验收**；之前51项合成检查不覆盖这些真实产品门。

| ID | 工作包 | 必须提交的证据 |
|---|---|---|
| I01 | W2/W3/W4 | 用户仅关注资产即可由新信息生成问题→事前Action→武装条件→PC可见；没有手写题目依赖 |
| I02 | W2/W4 | 每个当前建议有适用仓位/期限/改变条件/退出；有合格证据的未校准方案不会被全局no_trade吞掉；wait附理由/条件/最迟复查 |
| I03 | W2/W5 | 一条主反机制→具名核验问题→实际工具原件→采纳/拒绝→动作或条件引用；根因不只是长段解释 |
| I04 | W6/W7 | 一个真实到期结果自动生成ReflectionDelta和新SkillVersion候选，含可复现修正/反例；手写种子不替代 |
| I05 | W5/W7 | 下一正常Run实际选择/读取/执行/消费一个由结果生成且通过方法试用准入的新版本，展示影响，不能用原种子替代；候选拒绝记录合法，但不能代替此消费门通过，也不强制晋升 |
| I06 | W7/W8 | 上涨/下跌/假突破/横盘诊断及有无技能配对，覆盖漏掉上涨、错误建仓、下跌减险和错误卖飞；长期偏多不吞盘中风险，更多等待不自动算改善；重启后经验身份不丢 |
| I07 | W0/W2/W8 | 至少一个真实外部分析/社区观点来源→版本/时间/作者/主张原件；仅官方数值源通过不能替代这项 |
| I08 | W2/W5/W8 | 一条真实观点→独立原始数据或反证核验→研究机制→新Action/条件或具证维持理由，全链可读 |
| I09 | W1/W2 | 转载不重复增权、编辑追加版本、过时观点不回填、未来修订不进入旧cutoff、推广不作事实的正负例 |
| I10 | W0/W4/W9 | BTC/ETH机制覆盖矩阵与真实缺口可见；扩展资产逐个核验，文章总数不能当全面性 |
| I11 | W6/W7 | 源/技能增量的机制/行动/成本/风险/覆盖评价可导出；不以报告字数、反思次数或skill数量评分 |
| I12 | W4/W8 | PC当前动作、变更依据、外部观点与反方、采用经验、监控/退出与结果均可操作回链；快速路径不等后台学习完成 |

W0中外部源不可用可以保留缺口并推进其他独立工作；I07/I08仍保持未通过，不能宣布全球观点能力完成。涉及已开始协议的变更另登记版本；本补充没有修改旧试验成绩和门槛。
