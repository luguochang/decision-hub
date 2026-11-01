# PC产品流程、输出与验收合同

设计基线日期：2026-10-17。基线：`decision-product-control-2026-10-17.review-5`。状态：**已于2026-10-18获准实施，PC优先**；实时进度与证据见[执行合同](execution-contract.json)和[执行日志](EXECUTION_LOG.md)，设计获准不表示界面或功能已通过验收。总控见[00](00_MASTER_CONTROL.md)，整体闭环见[11](11_END_TO_END_DESIGN_CONTRACT.md)。

用户已选定BTC/ETH的OKX USDT线性永续合约双向研究建议与纸面验证。多头、空头、减仓、平仓均可成为首选。建议、用户自报和纸面成交分别记录，自动下单不在本包内。原现货报告、行动、纸面账本和旧协议按原身份保留，不转换成永续成绩。

## 1. 用户怎样完成一次决策

1. **确定判断对象。** 关注BTC/ETH，选择市场/合约及4h/24h/72h，填写当前空仓/多仓/空头仓位/未知，以及允许暴露、计划损失等约束。正常使用不要求编写prompt。缺账户时仍给单位名义建议和适用分支，余额、张数、保证金、强平位置保持未知。
2. **在变化前看到计划。** 首页列未来事件和普通市场下仍有效的方案：现在首选什么、关键情景发生时做什么、主要反证、触发/失效条件。系统登记机会并自动研究；暂无结论要说明缺什么及下一次补查条件。
3. **区分三类概率。** 事件先区分按定义发生/未发生及条件于发生的结果，再说明结果相对前值/合格共识/自有估计；市场显示同事件条件下的涨/震荡/跌及边际分布；动作显示扣除费用、滑点、资金费后的盈利/持平/亏损分布。最可能上涨不等于首选做多，震荡不等于空仓。
4. **获得明确建议。** 显示适用仓位下的首选多/空/减仓/平仓/不变或条件方案，附次选、依据、风险门及换选边界。未知仓位同时显示flat/long/short三支。看空观点、开空建议与卖出平多不能混写。
5. **及时知道变化。** 事实/价格满足已发布条件时先收到快提醒，指出触发哪版方案、原建议是否失效、当前分支。深研究后台更新机制与概率；后台服务健康时关闭页面不停止监控、不丢成果，主机休眠/服务中断须明确显示覆盖缺口。
6. **比较修订。** 显示旧→新概率/动作、决定性新证据、设置变化及数据截止/可见时间。旧计划已失效须立即标明；新建议经过正式审核发布才能成为首选。
7. **看结果和经验。** 到期分别查看事件、市场路径和行动结果、未触发/不可评分原因。知悉不是成交，paper不是实际收益；下一次实际采用的经验须有版本与消费证明。

4h是新增目标，不是旧30m改名；缺4h原件显示尚未形成方案。旧30m、事件窗口和7d基线保留原名。预测期限与最大持仓期限分列，旧72h预测/最多持仓24h策略不能显示成72h持仓策略。

CPI等具备精确公布时点的主卡使用02按事件阶段定义的`scheduled_pre_release`，以T0−60s闭合的合约trade分钟close为起点，包含公布跳动；T0+60s的`post_release_response`独立显示目标、q、样本和成绩。FOMC声明/当次SEP、发布会、纪要分阶段，不能把“声明已有结果”显示为整场会议完成。只有日期精度时显示“时间待核实”，不能补造倒计时；普通市场和发布会live更新用各自`publication_bound`目标，不能混算事前成绩。

**首页事件议程**消费02/03的`EventAgendaSnapshotV1`与family binding：默认展示未来24小时、可展开7天，显示用户时区和原公布时区；研究按所选4h/24h/72h冻结自己的议程，展示窗口不能改变预测目标。通胀、就业、央行和加密原生事件同列，CPI不设专属入口门槛。每项显示影响理由、阶段、事前方案状态、缺口、下次检查与关联操作；“已发现事件/正在研究/预案已发布/条件触发/结果待确认”分别呈现。无方案或缺数据的重大事件仍留在列表，不能只展示已成功的条目。重叠事件展示组合影响和同一当前建议；详情说明条件依赖，不能把两条矛盾建议都标为首选。普通市场方案仍持续更新，不要求用户手动挑事件或写研究题。

### 1.1 首次使用与每天返回

首次进入先选合约/期限与关注范围，保存后显示服务端回读的设置和跟踪状态；缺账户可以直接看三种持仓分支。页面说明建议是否研究假设、适用何种风险输入，并提供下一步：查看当前计划、补齐具名输入或开启跟踪。启用paper是独立步骤，需确认模拟账户、模式及02 §5.7的成交协议；开启跟踪不自动建账或自动启用paper。

每天返回首页先呈现“当前可做什么/等待哪条条件”，再列自上次查看以来失效或新生效的方案、未完成保护/退出、待结算结果与下一事件。所有方案都须有可检查触发量、来源、有效期、失效/退出及下次检查；“看空但尚无可执行条件”和“已触发但错过报价”分别显示。研究报告进入依据详情，不能替代行动卡或让用户读完报告才能找到操作条件。

首页常驻监控健康：行情/事件采集最近成功时间、monitor最近完成时间、通知通道状态和覆盖中断区间；显示“正常/数据延迟/监控中断/恢复中”，不以页面在线推断后台正常。恢复先对账原revision、未结episode与回执，补记漏报/缺数据；不能把离线期间历史价补成当时模拟成交或已送达提醒。归属原T05/T07/T08，无需新运维平台。

“暂停跟踪”停止新研究/新增风险准入，界面明确已有paper仓位及保护仍在；它不等于平仓。停止新paper、显式平仓与关闭提醒分别有命令和回执，关闭提醒不清除风险状态。服务无法持续保护时显示尚未完成的暴露与操作入口；恢复或切换模式不复制旧ack、不重复执行旧intent。具体保护规则沿03 §10.5，成交规则沿02 §5.7。

## 2. 首页与方案详情

### 2.1 文字线框

```text
关注：BTC / ETH   市场：USDT线性永续   场所：OKX（设计候选）
期限：4h / 24h / 72h   [我的仓位与风险设置]
约束：单位名义基准 / 用户保存设置   数据截至 …

当前推荐 [适用仓位] [有效至] [研究估计/模型估计/已校准]
首选：做多/做空/减仓/平仓/不变/条件方案   次选：……
为什么：两条主因 + 主要反证
触发：……  失效/退出：……  换选条件：……
风险约束：……  主要损失来源：……
[完整方案] [概率与依据] [变化记录] [纸面验证]

事前情景：事件结果概率（比较基准）
本合约反应：上涨 / 震荡 / 下跌（窗口与阈值）
本动作结果：盈利 / 持平 / 亏损（成本与资格）

接下来事件：时间 / 是否有事前方案 / 待触发分支
最近变化：发生什么 / 原方案如何变化 / [查看] [已知悉]
研究进展：已解决问题 / 关键缺口 / 等待的依赖
结果与经验：原判断 / 结果 / 漏报或错过 / 下次采用
```

技术ID/hash/Run/receipt放在数据与执行详情。来源、资料、研究、历史、方法和管理仍可直达；首页的“当前建议”不能用最近报告摘要代替。

### 2.2 当前行动与事件预案的选择规则

拟扩展`packages/query_views/decision_product.py::DecisionProductQuery`，金融类型从新`contracts/schemas/decision_product_v2.schema.yaml`生成，查询schema新增`DecisionUiCurrentViewV3`及客户端校验。旧V1/V2投影继续按旧合同读取。该投影只读，不生成Action、修改head或启动研究。最小字段：

```text
schema_version, primary_kind=action|event_contingency|none, primary_ref,
owner_scope, account_id/account_kind, instrument_ref/hash, horizon, target_bundle_ref/hash,
position_context_ref/hash, account_snapshot_ref/hash|null, utility_policy_ref/hash,
settings_revision, applicability, recommendation_basis,
event_probability_ref|null, market_distribution_refs[], action_distribution_refs[],
reason_codes[], conflicts[], supporting_refs[], current_revision_ref,
published_at, visible_as_of, data_cutoff_at, valid_until
```

review-4新增`event_agenda_ref/hash`及关联`event_family_binding_refs`到新V3投影；议程entry的stage、coverage和next_check沿03合同投影。完整议程列表复用事件查询路由与分页，UI不能自己从新闻标题生成事件或概率。

1. 同owner、instrument/venue/market_type/price basis、期限、仓位和风险版本下，正式发布且有效的Action优先，读取权威head；不能按HTTP返回顺序或报告时间选建议。
2. 无匹配Action时，合格event-owned预案可补位，明确标为事件条件预案，保留event/forecast/output身份。现货预案不能补永续卡；预案不能冒充Action或成交。
3. 监控确认失效或认证事实实质推翻方案时，显示旧方案、冲突与`needs_review/invalidated`。后台持久冲突并沿既有admission复查，UI不按新闻标题判断，GET不创建Run。
4. 风险设置变化使旧版标`settings_stale`，历史可读，但不能当已适用新约束的首选或再增加暴露。重评复用仍有效事实/概率原件，不强迫全部重新检索；未完成时显示适用性待重评。已经成交的paper episode继续按其冻结保护/退出规则监控，主卡下架不能撤掉止损；改变存量保护须具名新修订与执行回执。
5. unknown仓位显示flat/long/short三支；浏览其他分支不等于提交账户变化。跨资产、期限、现货/永续不补位。账户改为short后旧long显示回执不能算新建议已展示。
6. 缺可信收益分布时仍可给明确`evidence_rule`建议、条件、替代与证据；EV和概率空值保留。缺统计胜率资格不自动降为等待，不用默认50%填缺口。

T05持久化冲突/失效/复查，T07实现投影，T08验证同机会的发布→冲突→修订→旧版回读。

### 2.3 详情内容与对象

| 区块 | 必须有的内容 | 冻结对象 |
|---|---|---|
| 当前选择 | 首选/次选、方向、适用持仓、规模单位、触发/失效/退出 | ActionPlanRevision、ActionCandidateV2、ActionComparisonV2 |
| 三类概率 | 发生/未发生与发生后的结果/基准；市场三分类阈值/条件/边际；动作净结果/成本及条件于成交 | EventProbabilityV1、ConditionalMarketDistributionV1、ActionOutcomeDistributionV1、TargetSpec |
| 风险适用性 | 账户/设置版本、暴露/计划损失、资金费/保证金、换选边界 | PositionContextV2、AccountSnapshotV2、UtilityPolicyV2、sensitivity |
| 判断依据 | 主链/反链、预期差/已定价、确认与反证量 | MechanismGraphV1、证据引用 |
| 正在补查 | 必要角色与未启动理由、复用原件、问题与挑战 | RolePlan/RoleTask/RoleResult/ChallengeResolution |
| 本次变化 | 旧→新概率/动作/设置，触发/送达/可见/知悉时点 | revision lineage、evaluation、outbox、receipt |
| 结果与经验 | 原目标、结果资格、经验版本、下次实际影响 | outcome、SkillConsumption、case/skill |

研究过程展示任务、公开证据和可检查的论证摘要。网页、帖子和历史观点保留来源与时间，不变成新事实。数字统一引用02 §9，页面不另编概率或收益。

## 3. 风险输入、重评与纸面验证

### 3.1 最小输入

| 输入 | 保存与用途 | 未设置时 |
|---|---|---|
| 关注资产/市场/合约与期限 | InstrumentContractV1及目标profile，spot/perp和BTC/ETH独立 | 展示范围与资料，不默选真实持仓 |
| flat/long/short/unknown、规模 | PositionContextV2含signed_contracts、来源/时点；position不等于订单side | 三分支；不猜flat或虚构规模 |
| 权益/余额/保证金模式/持仓模式/杠杆 | AccountSnapshotV2区分自报与模拟 | 单位名义研究；金额/保证金占用/强平值未知 |
| 允许方向/最大暴露/计划损失/尾部限制 | UtilityPolicyV2保存币种、金额/比例、来源与版本；比例需权益 | 显示单位名义基准，不声称符合个人承受能力 |
| 跟踪开关与期限 | 既有owner命令、回执、tracking policy | 页面关闭不停止；无授权不无限续研 |

设置命令携带owner/account_id/account_kind/request_id/expected_settings_revision，保存新revision并登记受影响重评，返回原回执。用户自报账户和paper账户不能共享一个设置head。后端校验单位、时间、合约与权限；持久位置和唯一写者以03为准。它是拟建功能，不能把现有`RiskExposure.tsx`的本地useState假设值当已保存偏好。

同合约4h/24h/72h是不同方案，不能默认三笔独立加仓。首版无组合策略时显示互斥备选；同paper账户不得重复开满。共用BTC/ETH账户沿02 §5.6显示总暴露、已预留保证金和同时不利压力占用，不给未经验证的分散抵扣；单资产建议成立不自动表示账户可同时执行，组合输入缺失时显示unknown及具体缺项。

### 3.2 设置变化

`设置命令 → 新Position/Account/Utility原件 → 受影响ActionComparison重评 → 正式新revision或不可行原因 → outbox → 新旧对照`。

域内确定性重评沿03 §10.6的`apply_comparison_revision/ActionComparisonDerivationV1`，只能重选v2 review已授权的候选与变量域，不能再次调用同Run的首次publish。旧v1或越域变化进入新复核/后继Run；前端不提交自算金融载荷。

只改计划损失不改变事件概率和公共宏观事实；改合约/期限/持仓模式须重新绑定相应目标、成本和规则。旧comparison不覆盖。进行中的Run保留冻结输入，发布前核新设置版本，可保存其研究，但旧约束行动不能成为当前首选。风险收紧导致不可行的提示由确定性检查完成，不等待深研究。

### 3.3 建议、paper和实际行为

- 做空建议指定合约与仓位分支；卖出平多减少原多仓。反手是`close → 平仓回执 → 重核条件/quote/risk → open short`，两个候选、intent和费用，不能一个sell穿仓。
- 已知悉只写通知回执；自报操作独立保存；两者都不伪造成交。
- 首版perp paper采用每合约净仓(one-way/net)、逐仓(isolated)、新仓名义/初始保证金基准为1。此为模拟协议，不代表真实账户杠杆；仍需维持保证金、资金费和强平规则。hedge双腿分别保留，首版paper暂不准入，不能净额抹掉双腿。
- 用户建立paper前必须看到并明确模拟起始保证金、合约规则、成本、资金费、成交/强平假设。账户未知时单位名义比较不等于已建paper账户，不能默填用户余额。
- paper模式按02 §5.7显式启用：服务端自动模拟与看见后手动模拟分别显示账户/协议、反应延迟和启用状态；知悉按钮不触发执行。部分成交、剩余暴露/保护、容量不足、超时错过及bar顺序歧义都有状态；盘口快照模拟不能显示为交易所真实成交。
- paper成交用可执行报价；mark/index用于各自定义的估值/保证金，不冒充成交价。缺价、跳空、资金费未知有具名状态，不填0收益，也不把止损当损失保证。
- `RiskExposure.tsx`保留假设计算器用途，其1000等示例不写入账户/风险设置。自动下单未包含在本设计；不能出现暗示真实订单成功的按钮状态。

## 4. 既有入口与拟补接线

路径相对仓库根，application为`packages/kernel/decision_hub_kernel/application/`。代码存在不代表本轮验证通过。

| 用户任务 | 当前真实入口 | 拟补接线 |
|---|---|---|
| 首页/当前方案 | ProductPages.tsx→RiskDashboard.tsx→ActionPlanPanel.tsx；api/actionPlans.ts→action_routes.py→DecisionProductQuery | current V3；instrument/target/settings贯穿API、缓存键、身份校验，保留V1/V2 |
| 期限/仓位 | ActionPlanPanel.tsx::HorizonPanel/ScopedActionPlanPanel | 旧30m/24h/72h与flat/long/unknown保留；新4h/short及三分支同profile接线 |
| 风险设置 | RiskExposure.tsx仅本地假设；既有owner/product_commands模式 | 独立设置用例持久写入，命令/重评责任见03 |
| 事件预判/实际值 | EventPrevisionPanel.tsx/EventLifecyclePanel.tsx、eventPrevisions/eventLifecycle API | 前值/共识/自有估计区分；perp目标、事件身份、延迟/提前/取消 |
| 进展/恢复 | ResearchProgressPage.tsx、既有research/Run API | 阶段成果、关键缺口、首因与后台恢复 |
| 依据/报告 | research/ResearchPage.tsx、DecisionGraphCanvas.tsx | 主反节点原件；摘要/概率/动作同冻结版本 |
| 暂停/恢复/知悉 | ActionPlanPanel.tsx::SelectedPlan/HorizonPanel、actionPlansApi | owner/request/expected_revision/state_version；浏览与写账户分开 |
| 提醒 | DeliveryPage.tsx、delivery API、notification dispatcher | 发出/可见/知悉分开；轮询周期不冒充实时SLA |
| paper/结果 | ActionPaperPanel.tsx/ActionReviewPanel.tsx/HistoryOutcomeResults.tsx；action_paper_product.py/action_execution.py | 现spot_scope和示例账户路径不能直接套perp；新规则/协议与旧账本独立评分 |
| 学习 | ActionMethodPanel.tsx/MarketMemory.tsx/演进页面 | 技能版本、采用/拒绝、真实consumed回执 |
| 来源/资料/管理 | SourceAuditPage.tsx、FactsPage.tsx、ResearchLibraryPanel.tsx及旧历史/管理入口 | 保留上传/解析/范围/实际引用、报告反馈回读、来源许可与失败、管理配置 |

具体迁移断点：`action_routes.py`和`DecisionProductQuery._scope`硬限旧三期限；`api/actionPlans.ts::checkBranch`只识别enter_long/reduce_long/exit_long，`actionPresentation.ts`也是long动作；monitor不能在UI增加short后静默忽略。新action+target_side须schema/codegen/API/query/monitor/UI同组启用。旧spot和新perp的scope、缓存、命令恢复键不能碰撞。

## 5. 输出与状态规则

每个有效方案包含资产/市场/合约/venue、事件/机会、截止/发布/有效期、版本、仓位、预测及持仓期限；明确首选/次选、触发/失效/退出、风险/换选边界；三概率各自定义、资格、支持度/区间与成本；主因/反证/缺口、来源时点、监控和下次检查、旧版与结果入口。

`unavailable/null`保留并说明缺什么。非穷尽情景保留其他/未覆盖质量信息，不能强制归一化隐藏遗漏。摘要、提醒、报告从同一冻结对象投影。

| 状态/操作 | 用户行为与持久语义 |
|---|---|
| 尚无方案 | 显示事件/普通市场安排与缺口，一键复查无需prompt；GET不创建Run |
| 正在研究 | 显示真实发现或等待依赖/持续时长；不造百分比；关闭页面不丢成果 |
| 快条件已触发 | 指出原revision哪条条件成立、失效、后继研究状态；不发布未审核新建议 |
| 事件提前/延迟/取消 | 显示原计划与实际差异；不覆盖schedule；晚于真实公布的判断不算事前 |
| 新版/设置变化 | 新旧对照和适用性；原知悉不复制；旧链接可回读 |
| 来源过期/冲突 | 显示受限范围；unknown不是false，新闻数量不作废全部方案 |
| 暂停/恢复/知悉 | 服务端回执确认对象/时间；同键重放，跨owner拒绝 |
| 提交响应丢失 | 查询原request，不自动换键重发 |
| 无成交/不可评分 | 未触发、不可成交、缺价/资金费等原因保留机会分母，不填0盈利 |

## 6. PC完整任务验收

本轮只定义，不运行或生成伪截图。实施获准后使用本机Google Chrome，真实数据/受控样本标识清晰；截图关联Run/对象/回执。

| ID | 完整任务 | 防止的用户失败 |
|---|---|---|
| U01 | BTC永续/4h普通方案→事件议程→具名事件事前卡→三概率→首选/次选→证据；受控数据覆盖CPI/PCE/就业/FOMC，至少一组同窗口多事件 | 只有报告无操作、只识别CPI、无方案事件被隐藏、前值冒充共识、概率混写 |
| U02 | 研究中离开→返回→进展/成果 | 丢Run、刷新重复创建 |
| U03 | flat/long/short/unknown、spot/perp、期限切换 | 未知只等待、空仓减仓、平多误开空、缓存串用 |
| U04 | 新事实/资金费跳变或FOMC后继阶段→快提醒→失效/合法分支→正式修订→知悉→旧版 | 旧建议持续有效、声明后提前结项、告警算预测、回执算成交 |
| U05 | 首次保存/启用→离开页面→服务中断→返回恢复；暂停/恢复、响应丢失→回查 | 假保存、暗启paper、重复动作、跨owner写入、旧版接新命令、离线补造成交、暂停误撤保护 |
| U06 | 到期→结果→经验→下一Run实际消费/拒绝 | 改原概率、无成交算盈利、总结算学会 |
| U07 | 来源/假消息冲突/失败/事件延迟→恢复 | 隐藏首因、重复消息累计证据、伪造成功 |
| U08 | 来源/资料/报告/历史/管理往返；上传→解析→范围→实际引用；反馈→回读 | 只保菜单丢业务、旧现货协议不可读 |
| U09 | 保存设置→收紧尾损→同事实重评→首选翻转→旧版 | 假设当偏好、风险未重评、覆盖原事实概率 |
| U10 | 明确模拟账户/执行模式→long/short协议→报价size/延迟/部分成交→资金费/保证金→结果；BTC/ETH并发占用同预算 | 伪余额、同bar追溯成交、忽略余仓、双占资金、未经验证分散抵扣、多期限重复占用、paper变实盘 |

用11的计划事件（含核心四族与FOMC阶段）、意外事件、普通市场三类时序串联验收，不新建测评平台；受控多事件检查证明工程合同，不能签为真实事前表现，不空等下次CPI才交付普通主线。U08可复用仍适用的旧验证证据并列明范围。手册沿用`docs/user-guides/DECISION_HUB_CUSTOMER_USER_MANUAL_2026-09-22.md`，移动端暂缓。

## 7. 性能与完成定义

GET有界读取，不启动模型、写任务或全历史认证。页面立即显示保存方案和后台状态；研究时间和页面响应分开。快提醒分别计received→evaluation→outbox→送达→可见时间，现有30秒/60秒轮询不证明即时送达。

PC关键任务须可用键盘完成，焦点与控件标签可识别；保存失败/无报价/方案失效给可读原因和下一步，输入错误关联字段，风险状态同时用文字/图标表达，不只依赖颜色。纳入既有U01/U05/U10，不另建UI项目。

PC可用须U01–U10及后端证据通过；预测与行动价值按02完整机会分母、时间隔离、成本和资格另评。页面可开、HTTP 200、概率数字齐全、paper盈利都不能独立证明产品完成。当前文档复核暂停状态及恢复入口以00为准，既有整包批准不被改写为未批准。
