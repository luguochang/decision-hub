# 多事件需求与真实覆盖核对（2026-10-28）

## 结论与证据边界

**需求没有全部完成。CPI不是全部范围；当前原UI的“核心事件事前预判”读链确实以CPI为中心，但其他事件已有部分接线，不能笼统说完全没有。** PCE、就业、FOMC已有实际值原件认证分派，且接入通用永续情景监控；尚无本次证据证明它们逐族完成真实事前概率→独立审核→可读发布→实现选择→结果回看。CPI本身也不能因九格代码或日历存在就计作真实全链验收。

本次仅静态读取当前Main源码与需求，未启动服务、调用模型/API、读写数据库、执行测试或构建。表中“有实现”只指找到当前源码；“未验证”指本次未取得真实run/审核/发布/结果原件；“未发现”限定于下述当前生产入口，不能推导为全仓绝对不存在。没有以旧截图断言当前屏幕状态，也没有用旧文档的完成标记代替运行证据。失败4h run的结论引用交接，不冒充本次重查数据库结果。

## 1. 有效需求与范围

- `docs/design/decision-product-control-2026-10-17/00_MASTER_CONTROL.md:177`明确BTC/ETH、多事件双向闭环；CPI是例子，PCE/就业/FOMC属于当前T03–T08，不能整体推到T09。`:185`要求每族官方输入、事前概率/多空分支、更新和结果，日历不等于方案。
- `05_IMPLEMENTATION_PLAN.md:157`起的T03要求四核心族schedule/actual/原文、精度、首次值/修订、阶段与标签；`:165`要求EventFamilyPack/Binding、registry及agenda；`:167`起要求核心族不静默遗漏，缺可执行方案应显式pending。
- `02_DECISION_METHOD_AND_EVALUATION.md:145`–`:154`列出四核心族、扩展宏观、crypto_funding、crypto_expiry、crypto_operations与具名可能事件。`:143`为重大性准入规则，不能任意追加所有新闻类别。
- `11_END_TO_END_DESIGN_CONTRACT.md:183`–`:189`要求PCE九格、就业firm/near/soft/mixed完整向量、FOMC利率×声明/SEP及发布会阶段、其他宏观经有界准入进入消费者；`:216`要求重叠事件不独立乘概率或重复归因。
- 当前AGENTS明确保留原Decision Desk渐进修复；v3历史重建状态只作覆盖账本，不据其另建前端或重新开项目。

## 2. CPI-only外观的具体原因

这里有两条不同读链，应先辨认用户实际看的页面：

1. **宏观日历本身没有默认锁CPI。** `apps/decision-desk/src/DesignStatePage.tsx:374`的family默认`all`，`:376`组装q/family筛选，`:379`请求`macroEventsPage`，`:380`请求可用family，`:397`的全部/即将/历史页继续携带当前筛选。URL存在`family=cpi`、q、scope、分页，或后端当前仅有CPI数据，都可能影响可见列表；本次没有浏览器/数据库现场证据来区分这些可能性。
2. **首页的“核心事件事前预判”不是完整宏观日历。** `apps/decision-desk/src/product/ProductPages.tsx:95`挂载`EventPrevisionPanel`；后者`:121`请求专用列表，`:122`默认选列表第一项，`:80`硬编码CPI九格。`packages/query_views/event_prevision.py:253`调用`read_event_scenario_events_in_session`，该函数只列event-owned forecast job（`application/event_scenario_autoproduction.py:160`–`:170`），其自动生产上下文`:90`仅从`CPI_SCHEDULE_SOURCE_ID`采集。**这条专用预判链的CPI覆盖限制是源码事实，不是单纯界面筛选问题。**
3. 列表的`query_views/event_prevision.py:160`直接把每项标题写成`美国 CPI`；未来仅扩producer而不修query，也会错误标名。覆盖提示也不完整：该文件`:28`默认只声明`uncovered_families=('fomc',)`，UI`:140`只显示FOMC尚未接入；不能据此推断PCE/就业已覆盖。该提示没有反映所有当前需求缺口。此处是后续最小接线/提示修复候选，不是本次改前端授权。
4. `event_scenario_autoproduction.py:160`说明过去30天及未来job可发现，列表上限12；即使扩多族，也需独立验证排序/分页不会掩盖未来事件。此处仅提出有限核对项，未认定当前已出现该缺陷。

## 3. 分事件族覆盖矩阵

符号：**代码**=存在当前实现/接缝；**未证**=缺本次真实验收原件；**缺口**=当前被核入口没有该覆盖。以下路径`K/`代表`packages/kernel/decision_hub_kernel/`，`S/`代表`packages/source_adapters/official_feeds/`。

| 需求事件族 | 日历/页面与采集来源 | 事前概率、情景与分支 | 独审及可读报告 | 实现后跟踪/结果 | 当前诚实状态 |
|---|---|---|---|---|---|
| CPI | **代码**：`S/presets.py:73`–`:95`装BLS、Cleveland nowcast/history、CPI schedule/history；专用预判面板九格可展示。实时入口`apps/hub_worker/main.py:247`–`:248`装producer | **代码**：`K/application/event_scenario_autoproduction.py:17,90`与`K/decision/cpi_event_scenario.py`计算专用九格；不能把历史CPI日价格基准当当前情景q（UI`:35,89`有区分） | 通用情景独审/发布接缝存在，见§4；本次未证真实事前Run/独审/发布链 | **代码**：`K/application/event_lifecycle.py:53`–`:96`筛CPI实际值并认证；`:101`起coverage/monitor/outcome。新perp CPI parser也有dispatch（`event_scenario_realization.py:239`） | 有较多代码；**未真实验收**。不标CPI全链完成 |
| PCE | **代码**：`S/presets.py:112`装`bea-calendar`；`S/bea_release.py:1,22`仅采national Personal Income and Outlays日历，明确不是actual，也会排除GDP；agenda支持`bea-calendar→pce`（`perpetual_event_agenda.py:22`） | `K/ports/event_scenario_preparation.py:218`–`:226`可绑定PCE selector；通用情景plan可承载主观p/q，但CPI专用自动producer/list无PCE handler。未证真实PCE九格/条件多空研究产物 | 通用链可用，未证PCE专属真实独审和可读报告；现专用预判页不读此plan | **代码**：`event_scenario_realization.py:202`–`:213`分派BEA RSS selector/精确文档认证；`event_scenario_pce_prospective.py:238`入口。首次版本冲突在`:266`–`:269`拒绝。到期结果未证 | 已有采集/认证接线，**不是仅名称；但事前生产/页面/真实闭环未证** |
| Employment / nfp（就业报告） | **代码**：`S/presets.py:129`装BLS ICS；`S/calendar.py:182`映射Employment Situation→nfp；agenda同族。`K/ports/employment_source_rules.py:24`–`:27`冻结BLS RSS/日历 | `event_scenario_preparation.py:228`有族分派；`employment_source_rules.py:27`具名非农变动/失业率/时薪。不能据三指标推断“前月修订+mixed四类”已全部满足。CPI自动producer与专用页不承载就业 | 通用链可用；未证就业四类主观p/q、独审原件、报告与分支齐备 | **代码**：`event_scenario_realization.py:215`–`:229`分派selector/document；`event_scenario_employment_prospective.py:175`认证入口，`:205`拒绝edition conflict；实际发布前/修订隔离及结果需真实验证 | 有专用actual认证代码；完整向量/四类/修订/真实闭环**未证** |
| FOMC | **代码**：`S/presets.py:105`政策规则、`:145`Fed会议日历；`K/application/perpetual_event_agenda.py:12`引入阶段原件；`K/ports/fomc_stage.py:249`有阶段推导。日期与精确时间必须照原件 | `event_scenario_preparation.py:245`–`:253`绑定FOMC statement selector；不是CPI九格。未证rate×statement/SEP联合p/q及发布会连续预案；UI`:140`仍明确未接专用预判 | 通用链可用；未证FOMC真实独审发布。enum的`fomc_rate_discrete`（`K/ports/event_scenario.py:11`）本身不构成producer | **代码**：`event_scenario_realization.py:231`–`:237`分派Fed reader；`event_scenario_fomc_realization.py:53`–`:55`只接受`policy_statement`。不能据此声称SEP/发布会所有actual阶段均自动核验。结果未证 | statement具名支持；**完整会议生命周期未证，专用预判覆盖缺口** |
| 扩展宏观：PPI/GDP/零售/JOLTS/初请、讲话/纪要、ECB/BOJ | **代码**：`S/calendar.py:183`识别PPI，`:185`识别GDP，`:188`ECB/BOJ，未匹配退`scheduled_macro_event`（`:193`）；`S/presets.py:151`–`:164`装ECB/BOJ日期源。BEA adapter排除GDP，不能以PCE来源证明GDP采集；零售/初请/JOLTS专门来源与identity未证 | 需求是经重大性准入的通用scalar/政策文本方案；当前`perpetual_event_agenda.py:20`–`:23`只枚举四核心来源，未见扩展族进入该议程映射。未证具名pack、主观p/q与实际消费者 | 通用发布基础可复用；各扩展族预审报告未证 | 日历不认证actual。未证逐类精确解析/文档认证/结果，不能把unknown补成0或自动标签 | **日历部分有代码；预测集成缺口/未证**。不承诺穷尽所有宏观事件 |
| crypto_funding（OKX资金费/结算） | `packages/provider_adapters/market/okx_research.py:54,258`有当前funding-rate查询；**不等于未来逐次结算agenda或实际费用证据** | 三期限成本敏感性/未来未知费率范围及每合约版本为需求；当前四核心agenda未列此族，拟`okx_event_context.py`不存在 | 普通perp报告成本链不等于该事件族独立p/q、预审发布已完成；未证 | 不将本次审计扩成PAPER资金链审计；真实结算与费用/结果未证 | **数据查询已有；事件pack/agenda闭环未证** |
| crypto_expiry（相关合约到期） | 需求仅OKX公开可得BTC/ETH相关到期上下文，永续本身不设固定到期；当前四核心agenda无此族，拟适配器不存在 | 未证到期/OI具名上下文和条件方案；不得由OI虚构方向概率 | 未证 | 未证 | **覆盖缺口/未证**，不能随意拓至其他交易所 |
| crypto_operations与具名可能事件（维护/规则/故障、已公告升级、监管/安全） | 需求按所选交易所官方状态/公告、项目/监管原件登记定义/期限/观察覆盖；已有通用材料采集不证明逐源在线覆盖 | 未证该族具名发生/不发生概率、两侧分支、观察窗口和pack；真实意外消息只可算事后响应，不能倒补事前预测 | 通用研究基础存在；族级真实审核/发布未证 | 未确认传闻不成为actual；缺观察保持unknown；具体集成未证 | **有界能力需求尚未验收**，不要求“所有黑天鹅”概率 |

## 4. 可复用接线与不能跨越的证据层级

- **共用研究动机**：`K/application/perpetual_event_research.py:191`–`:212`把agenda每项family/stage/source写入`PerpetualEventMotiveV1`。这证明有“日历/议程→研究请求”的接缝，未证明每族实际产生合格plan。
- **共用情景合同**：`K/decision/event_scenario_action_plan.py:73`校验数值分区，`:257`校验情景predicate，`:277`–`:283`校验发生/结果概率分区。数学完整性不是概率有效性的证明，也不是独立审核。
- **独审/发布**：`K/application/event_scenario_publication.py:67`–`:69`解析并校验真实trusted审核绑定，`:107`–`:109`保存review request/authorization；`:71`起从server material读取完整manifest。存在严格接缝，不可拿hash或测试fixture替代独审原件。
- **可读报告**：`K/application/event_scenario_report.py:44`投影真实plan，`:68`–`:75`输出分布与逐情景概率；但当前`EventPrevisionPanel`仍走旧CPI专用query。`K/application/perpetual_advice_query.py:216`–`:224`已有从永续建议页读取已发布情景plan的另一路，不应误判所有报告页面都CPI-only。
- **监控接入**：`K/application/perpetual_plan_followup.py:235`–`:237`创建EventScenarioWatch；`perpetual_plan_watch_continuous.py:606,672,818,840`准备/观察同一已发布plan；`event_scenario_watch.py:298`调用actual reader。PCE/就业/FOMC认证不是孤立死文件；是否有真实同事件plan进入该消费者仍未证。
- **静态registry缺口**：本次精确`Test-Path`结果：T03拟定的`K/application/event_family_registry.py`、`K/ports/event_family.py`、`K/decision/event_family_scenarios.py`、`packs/crypto_macro/events/`与`packages/provider_adapters/market/okx_event_context.py`均不存在；拟`official_feeds/bls_employment.py`/`fed_policy.py`也不存在，但其部分职责已由ports/application的selector和reader实现，**不能按文件名缺失直接判功能全无**。应按实际职责裁决复用与缺口，不机械照旧拟文件清单重建。
- **真实证据**：本次没有取得每族run_id、cutoff、预审authorization_ref、publication receipt、readable report、realization与outcome组合原件，因此没有任何一族可被本次审计升级成“真实闭环通过”。两次普通4h run为failed且没有最终审核通过的概率/报告，也不能拿来授多事件资格。Root后续R1取证已发现未发布概率草稿，纠正旧交接“完全没有概率”的措辞；草稿不升级资格，原件见 `d28-research-recovery/timeout/diagnosis.md`。

## 5. 后续优先级（建议，非本次新增实现）

| 优先级 | 具体有限工作与交付判据 | 与4h链的依赖 |
|---|---|---|
| P0 | 先完成当前BTC/ETH4h失败归因和真实研究恢复；分别记录基准率、终态、概率、独审引用、报告及偏离依据 | 已批准当前主目标；通用DSH研究/审核/发布失败会阻塞主观多事件plan的真实验收 |
| P1 | 在原UI和原API分别核对宏观日历筛选/数据与预判面板job列表；覆盖账本明确四核心族pending/缺项，不能只提示FOMC。先读现场数据再修最小责任层 | UI覆盖文字和日历取证可独立开展；不得占用付费研究或重建前端 |
| P1 | 按实际selector/reader复用四核心族职责，先找当前真实agenda→motive→plan→review→report断点；冻结同事件/阶段/合约/期限身份和输入缺口，保证PCE/就业/FOMC有研究消费者与可读分支 | 结构化合同/来源核对不必等待CPI统计训练；真实主观p/q与独审验收依赖P0可运行 |
| P2 | 分别验PCE九格；就业完整向量、mixed及修订；FOMC statement/SEP/发布会阶段。每族最少一条真实事前发布与实现后回看，BTC/ETH及三期限资格分别记账 | 不借CPI样本或其他期限资格；需等真实事件和成熟结果，不回填事前cutoff |
| P2 | 扩展宏观按重大性规则逐个登记；funding/expiry/operations补真实来源覆盖与消费者。未认证actual可先有具证文档研究与价格保护，必须显示缺口 | 依赖通用plan/审核/监控可用；无需先等多年统计样本，也不在本轮直接扩全部适配器 |
| P3 | 同窗事件/连续阶段依赖、延期/提前发布、无SEP、修订、缺观察负例和结果分账；随后14天稳定性、PAPER真实费用/成交及评分资格 | 不能用离线合成或本次静态审计替代真实长期验收 |

## 6. 本次输出与未执行

只新增本文件；未修改源码、UI、总控、Root计划/日志、数据库、历史原件或正式数据。未运行测试/构建/服务/模型。没有联网核对实时官方日历，因为本次问题是仓库需求与已实现接线，不是推荐日期；官方URL仅作为源码内采集配置证据，不声称远端当前可用。后续需由Root在独立运行窗口补逐族真实原件，不能据此文将T03或整个目标标完成。
