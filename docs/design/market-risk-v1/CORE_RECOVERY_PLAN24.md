# 核心整改实现与验收计划24

更新时间：2026-10-09；当前正式31的真实BTC已保存artifact，但Run终态degraded。14证据/10工具、4主链/1反链/30m24h72h及1审查原件可见；六GET首验quality/context各45s超时，download200/26.782s/314402B，详情/过程/效果200。PC随后自然读出质量：事实/上下文complete，五挑战未解决、报告untrusted、用量与费用unknown。下载SHA等于PC可见发布报告hash；这不替代首超时或完整报告资格。24h建议复查早于研究、英文属性/长小数和重复缺口已记录。ETH未提交；完整产品和金融效果未通过。 本计划细化原33项；现行步骤只读一页纸和机器状态，历史原件及原验收门保留。

## 具体实现、依赖和完成定义

| 项 | 现状/原因 | 实现与源码入口 | 完成定义与证据 |
|---|---|---|---|
| K01认证终态一致性（MR15/17） | ETH旧seq/nohash、BTC取消降级掩盖原失败 | Host bridge.ts/status/inspect/ack + session-result.ts原snapshot cutoff/重启；Runtime web_runtime.py物理RO原callback、native元字段、late callback确认；严格complete不改 | Host33、Runtime18定向控制P；25已合生产源，标准构建/Ready已通过，真实报告待。错hash/gen/cursor/time/receipt/legacy无auth拒绝；不改旧Run，不伪造正文 |
| K02真实输入回调超时（MR15/17） | ETH source_capture8.358s+auth4.861s+再核2.787s>15s；HTTP409/lifetimeelapsed | provider_input_receipts.py→prepare_originals→_ReadSourceGuard→fresh完整源码/readset→writer；先按原phase定位重复纯构造/读取，复用仅限同物理snapshot且实际SHA/currentlease/auth重核 | 保15s原预算与不重放COMMIT；选定private并发实际source/readset负控和一个具名正控，保存各phase，不以提高timeout/省校验授P；新真实回调/完整报告必须通过。25 BTC首异常确指_record_transport→projection.write5s未确认；具体phase/BEGIN-operation-COMMIT卡点仍unknown |
| K03发布容量（MR02/28） | 21b余20,691B，下一V3约61,726B；旧格式必超限 | A stdlib product_release_provenance codec/PS v2 verifier+dispatch+producer；B 三Kernel current/history/archive；C 八启动/组件/retention/planner源 | actual21b全部9296 path/hash解析等同、v1原rawSHA不变、v2闭包transport+codec、错blob/leaf/path/大小拒绝；固定2MiB/32MiB与旧guard不变；封源后一次标准发布。17入口见consumer-map01 |
| K04历史训练（MR23/25） | 旧fit0/60冻结；8业务/0082与db/env接线仍未正式，command/独立worker三源DEV候选已封，4个接线控制已准备、未执行；未真实采集或训练 | history_observed_training/dataset/ledger/records/0082、acquisition23、model24；接db/migration/worker/CLI-PC；注册固定90UTC日→真实300分页/lease→source准入→freeze→fit实际保存P→未来cutoff当前31close | 两资产固定90原件、joint≥60/low-high各≥30；所有R/D/K/C/S/fit/P实际时钟、未来泄漏/篡改/重复事务拒绝；新模型只history_observed_at_fit，PIT/promotion仍false；正式自测后才Ready |
| K05用户可用报告（MR19/32） | 25普通BTC失败无artifact，ETH仅证据artifact/AI失败；27可读失败说明已实际PC验证；29b为历史正文/质量未过；31 BTC已存正文但degraded，quality/context首45s超时、24h复查建议早于研究，完整报告未过 | 已有首页/执行/失败/完整报告/质量/下载，补可读原因与恢复条件；保原任务只读和关联新Run | 每asset按钮一次真实请求，不默默加请求；报告正文、最强反方、三时段、数据时点/缺口、上下文/原件、实际用量、附件逐项GET和PC截图；部分材料不能授完整结论 |
| K06自动监控链（MR18） | 持久监控/告警工程已装，自然child未闭环 | 60s数据→条件→告警回执→DSH admitted child→报告→复查计划，既有额度/失败计数不放宽 | 同实际父/子身份和幂等原件、真实自然触发、完整子报告/回执/按钮；不以人工创建替代自然child |
| K07成绩和经验（MR24–27） | 完整分母实现，统计/经验作用未证 | 固定28日成熟路径/unknown分母/matching/成对CI/有无记忆对照 | 原固定截止不续开；100独立日/30事件不足明确insufficient，不授预测增益；记忆消费能追至原案例，不借相关BTC/ETH充独立样本 |
| K08宏观与黄金（MR29/30） | vintage/共识/仓位与黄金授权来源不足 | 独立版本候选与MarketSpec/可用性准入、许可/日历/时效/事实层；复用已验canary合同 | 真实供应商/权利/样本/时效和失败投影核实后准入；配置未接通仍unknown，不能把分钟行情或新闻摘要当预测优势 |

## 执行顺序与避免遗漏

1. K01/03候选已封并晋升；K02缓存实验无总体收益已撤回，真实阶段诊断继续。当前30三项修复已标准发布；本次BTC失败已记录，先修完整usage审计，再验真实核心，不重复提交旧失败任务。
2. 后续需发布时，审当前源码/依赖闭包与各seal，物理RO核无非终态任务后标准stop→捕原件→晋升→唯一标准build→标准start含SpotResearchV3→Ready。若具名门F，保原件/旧版本，修具体原因才重试。
3. K05两真实普通PC请求及完整六GET；对应原callback/provider原因验证。失败则明确F、诊断首原因，不循环重提。
4. K04正式接线/固定实际采集/fit/currentconsumer及PC；K06自然child；K07依固定期限和分母结项；K08真实源准入不足保明确边界。

每一关记录source before/after、版本/实际原件SHA、唯一控制/首F、PC动作与截图、剩余项。当前状态只写[一页纸](CURRENT_FOCUS.md)/[机器状态](execution-contract.json)，历史独立存档；原33项前五列和验收门不能因整改改宽。

## 历史28具名结果

正式28标准构建/启动，3Ready200/7服务在线。旧ETH质量GET200/29.984秒、上下文GET404/19.922秒；超时已消除，但质量为research_only/untrusted，旧上下文发布原件确实缺失。演进就绪GET由500修为200/3.062秒。完整AI报告与金融效果仍未通过。

K02审查原件：`B2/k02-capture-review28/REVIEW.md`。本批不直接作用于current callback capture；禁止把旧历史读取P记作15秒回调P。生产静态13类型/25行宽诊断是既有基线，新增控制诊断0；这些基线债务已在scope28/static-delta07.json留证，未夹带修改旧分支。

K04精确顺序：8业务/0082＋db/env→显式command/registration worker→真实采集→RO准备/fresh writer freeze/fit→独立不可变forecast索引0083→owner API/PC。旧RiskPrediction/trial或Run publication不能直接授无Run历史预测保存。默认七服务不自动注册、采集或训练。

## 历史状态25

Host33/Runtime18、容量A3/B16/C15、UI7定向控制P；25已合21生产/4控制源并标准构建/启动通过，BTC failed/no artifact；ETH degraded仅2证据、AI失败。六GET及原件验收F，质量/上下文各45s超时。K02 profiler2.944→2.998秒，减少JSON校验未产生总收益，已恢复原源；诊断和首失败保留。历史模型24已封11私控P，未正式接线及授数据/模型资格。具体计划和原验收门不变。

## 原始证据

- [终态字段认证核对](../../../artifacts/market-risk-implementation-20261007/B2/worker-terminal21b-btc-diagnosis/conflict-field-audit06.json)、[回调阶段](../../../artifacts/market-risk-implementation-20261007/B2/worker-terminal21b-provider-phases24/summary01.json)。无exactstack部分继续unknown，诊断日志不能授权限。
- [容量可行性](../../../artifacts/market-risk-implementation-20261007/B2/release-provenance-scaling23/FEASIBILITY01.md)、[17入口及精确source](../../../artifacts/market-risk-implementation-20261007/B2/release-provenance-scaling23/consumer-map01.json)。
- [实际失败页截图](../../../artifacts/mr-pc21b/eth-failed-reason11.png)：原任务只读/保留记录入口可用；当前准确人类原因F，未重试。

整产品可交付=false；金融效果合格=false。本页是实施合同，不是完成报告。

## 25真实验收新增缺陷及窄实施方案

- **K01补充：写入确认与首因保存。** `_record_transport`的同一次owned Future保提交/BEGIN/operation/commit阶段与最终结果；等待超时仅授pending，不重放写或HTTP。真正Host异常保原首因，未认证raw status原因单列；成功mapped仍须已确认精确原件。私控覆盖迟到commit/失败/取消/slot/原error不覆盖，真实PC再验。
- **K02实测：** BTC source_capture10.001s+original_auth4.938s，在15s回调断开；原件source recheck继续1.776s，未进writer。不将其归因为Docker/SDK缺失。仅优化实际重复纯工作，完整源码/鉴权/lease/readset仍fresh；缓存实验无收益已撤回。
- **K09（MR16/17/19）：质量和上下文读取超时。** ETH report200/.171s，quality/context两个GET各45.016s超时；本地原件读45.015s终止。逐层审API→publication_final_read_scope→retained recipe/完整原件，移除重复全证明须保持同物理snapshot、原SHA/row绑定、首读完整校验及退出重核。先定向对照/篡改拒绝，再标准发布和真实GET/PC；不能加45s等候替代性能修复。
- **K05细节：** 空采纳证据时不显示“来源可追溯”；0/非采纳/>0与刷新切换定向验收。25当时仅DEV候选，未发布。证据原文入口无HTTP关联会明确提示，ledger原件到用户可核对资料的接线仍需审查。

[严格六GET与只读原件结果](../../../artifacts/market-risk-implementation-20261007/B2/release25/strict-report01/receipt.json)、[BTC首因原件](../../../artifacts/market-risk-implementation-20261007/B2/worker-terminal25-btc-diagnosis/originals01.json)、[PC质量超时截图](../../../artifacts/mr-pc25/eth-quality-timeout09.png)。本批真实核心验收未过；不重复提交失败任务。

## 历史修复26（2026-10-09 08:54，当时已标准发布、真实报告待验）

| 阻塞点 | 本次具体改动 | 已有证据 | 仍需真实验收 |
|---|---|---|---|
| K01 执行记录确认/首因 | 跟踪同一次写入 Future；pending 不重放写入/RPC，迟到结果仅诊断；保原模型失败原因 | 2 Runtime 源、6个唯一控制 P，首次 F保留 | 实际 Run/原件/迟到写入与失败原因；不恢复旧失败任务 |
| K09 质量与上下文读取 | 同物理只读快照内共享 recipe 原件验证；SAVEPOINT 捕获原生事务切换；严格 authorizer 拒绝 marker 时走旧完整读 | 1 Kernel 源、3个唯一控制 P；单 fixture 验证次数12→4，4.070→1.360秒，完整 DTO 相同 | 旧ETH实际 GET 与新报告。旧严格观察器拒绝 SAVEPOINT，旧路径45秒限制仍可能失败 |
| K05 文案可信度 | 零采纳证据不显示来源可追溯；执行原件不可核验时说明本次未完成 | 2 UI 源，2+1个唯一控制 P | 正式 PC 刷新/失败页/证据查看截图 |

本批5个生产源/5个控制源已晋升，一次标准构建/启动通过，10066源/41产物，3Ready200/7online；精确哈希与首失败随封签保存。K02尚未修复：第二个快速拒绝候选2.944→3.022秒无收益，已恢复原源；纯文件 manifest warm .598/full .423秒仅定位，不授实际回调改善。保持5/15/45秒预算、完整源码/权限/原件门。

发布前发现25还有两条BTC同study任务，分别failed/research_method_binding_failed与failed/dsh_turn_error；起因尚待原件核实。四条实际25选择原件全量保留，非终态0；不只保存两条PC请求，也不把失败算自动研究通过。

26当时顺序：标准发布 → 旧ETH质量/context GET各一次 → 新BTC一次普通PC请求，终态后再ETH一次 → 正文/反方/三时段/质量/用量/下载与实际原件。当前沿29b已运行BTC等待终态，其后ETH一次；不并行CPU控制、不重提旧请求。若失败，记录具体首因和对应修复再发布。

本批候选与真实验收门独立记录，33项原五列已逐字核对未改。历史采集/训练、自然监控完整子报告、成绩与经验真实消费、宏观/黄金来源资格继续沿原清单执行；没有因本批维护任务删去。

具体历史训练正式接线、未来消费回执、结构化行情原件DTO/权限/SHA-time与必要验证见[实施合同26](IMPLEMENTATION_CONTRACTS26.md)。现有文档/HTTP原文入口保留；新接口仍proposed，不能把源码审查算接线完成。

## 26真实读取失败后的具体修复

旧ETH两个GET各一次45秒ReadTimeout，未发新模型请求。一次有界只读profile（mode=ro/query_only/physical BEGIN/deny data writes，允许仅本reader marker的SAVEPOINT/RELEASE）表明retained publication会重复resolve_product_receipt；relative_path459484次cum26.358秒，字符generator72406287次cum14.941秒。profile有额外开销，不是接口基准；纯Python区间延迟SQL进度deadline至60.56秒才中断，thread CPU59.98秒，原件源稳定/SQLchanges0。

最小候选：仅把product_release_provenance.relative_path的字符迭代拒绝检测换成等价C级compiled regex；类型/长度/路径parts/完整JSON/SHA/源/权限门不改，无authority或map缓存。有限等价/拒绝控制与同原v1/v2完整map对照后封源；实际GET及新完整报告仍需标准发布验收。K02输入回调是否改善另以实际阶段核实，不能由旧报告profile直接补认。

PC另一具名缺陷：ResearchPage“查看问题详情”的technicalSummaryName/failureExplanation未覆盖research_observation_unavailable；DesignState中文不等于报告页已覆盖。补统一标签/不猜写入丢失的解释，unknown原码仍折叠保留。26新源暂不混入未封候选；首次失败与截图保留。

## K10历史28缺陷：普通资产更新无法自动核验旧会话（MR15/18）

历史28普通PC BTC按钮已点一次：请求 riskresearch_5b4d39269555c19789c1966ad4324442cdf4ca3b 经3次准入核验后 failed/study_host_terminal_unknown，run=null、模型0；当时ETH尚未提交。当时自动Host活动核对没有把这类待准入请求列为需求（自动0、同Study显式1）；操作员指定一次原生产核对链实际保存1条活动快照。29b已完成自动活动需求接线并实际创建新BTC Run；旧失败请求不恢复，完整报告仍待。

- **原因与位置：** `risk_research_admission._admit` 被研究准入门拒绝时整个事务回滚，`_failure` 只保存请求失败。`dsh_activity.activity_targets_in_session` 原来只收集显式Study、到期复查和近期AdmissionDecision，不读取此待准入风险请求。
- **有限实现：** 只读加入当前BTC/ETH现货待准入请求的固定Study映射。核对原scope/Study身份、存储trigger哈希、manual owner-command、状态/租约/generation、精确host-unknown错误及60/120秒原backoff。活动原件仍由现有真实Host与认证API产生，当前30秒TTL、Run/Link/租约/非活跃门保持。
- **时间边界：** 自动需求必须覆盖120秒backoff，不能套用旧AdmissionDecision的60秒窗口；过期、已失败/queued/claiming或带Run请求不被此候选当成恢复许可。
- **必要控制：** 同一实际选择器核对两资产及attempt1/2时间窗、篡改/错scope/错Study/错owner/租约/错误/超期拒绝，显式/旧复查路径保持。只运行有限隔离组；不伪造实际Host通过或模型结果。
- **正式验收：** 标准发布后，同原Study关联新BTC一次；原失败请求/Run不复写、不删除。核对自动producer需求→真实活动回执→新准入→原模型→完整报告。其终态后ETH一次；正文/最强反方/三时段/质量/context/用量/下载/原件各留结果与PC截图。
- **独立未完成门：** `_claim` 对已有risk-request绑定旧Run仍保原host_work_unsettled硬门；尚未证明此候选解决该门或模型回调15秒问题。观察失败后只修实际首因，不循环提交。

[请求原件](../../../artifacts/market-risk-implementation-20261007/B2/release28/status02.json)、[实际生产核对记录](../../../artifacts/market-risk-implementation-20261007/B2/release28/activity-need01.json)、[PC失败截图](../../../artifacts/mr-pc28/btc-admission-failed02.png)。以上为28时待办；29b九项控制及正式自动活动/准入已通过，完整模型回调与报告仍待，旧失败请求不恢复。

## 29b本次实施结果与发布首失败修复

当时正式29b已标准构建/启动，以下保留历史真实验收。BTC一次PC请求经自动Host活动核对已准入，真实模型交付12条证据、主反链和30m/24h/72h正文，Run终态degraded，产品标为research_only。六GET首验中quality/context各45s超时，download200/44.125s；模型空闲后单独context200/25.344s，认证覆盖仍不完整。自动30m复查已触发但初始BEGIN IMMEDIATE锁冲突失败；ETH尚未提交。目标active，完整产品及金融效果未通过。

K10九项控制首轮通过、静态新增诊断0；正式自动producer保存5次完整census、真实inactive快照，再由原准入门创建新Run。最后快照03:17:59.232UTC，期限03:18:29.232UTC；新Run创建03:18:10.637UTC。未延长TTL或复写旧终态。完整模型回调与报告仍独立验收。

29首次标准构建在retention准备阶段失败，UI/Host构建均0。组件source-binding.json实际2,120,246B、仍存在且SHA未变；基础inventory不含AdditionalSources，比较时误当删除变化。仅在prepare-product-retention中限定原default-v2可观察的packages Python与contracts/schemas JSON/YAML输入；全局receipt/组件独立验证和原2MiB/32MiB门保持。3文件控制证实完整/过滤overlay产生相同真实manifest，新增import、删模块和schema效果保持，真正超大相关源仍拒绝。首次静态F保留；仅控制seam注释，AST相同，新增诊断0；原脚本7行宽/31类型债务保留。随后29b标准构建/启动通过。

[实际自动快照](../../../artifacts/market-risk-implementation-20261007/B2/release29/automatic-activity29b01.json)、[新BTC准入与运行](../../../artifacts/market-risk-implementation-20261007/B2/release29/status29b02.json)、[29首构建失败](../../../artifacts/mr-release-b2b4-29/build-execution.json)、[29b实际构建](../../../artifacts/mr-release-b2b4-29b/build-execution.json)、[PC运行截图](../../../artifacts/mr-pc29b/btc-progress02.png)。

## 29b真实终态与K11初始selection writer争用（本次记录）

BTC任务正文、12证据/10工具/1审查已真实保存；还存在质量/context并发45s超时、initial/review覆盖拒绝、5项review未解决和费用/完整用量权威不足。单独context200/25.344s表明不是单纯404；实际选中input已有完整DTO，不能归因成未发送正文。先做一次只读分阶段计时和精确拒绝点，再修对应代码，不反复提交同缺陷研究。

自然30m复查child失败事件4179：phase execute、OperationalError、原句database is locked、精确SQL BEGIN IMMEDIATE；尚无ContextSelect/Method/模型input。只在确认的ContextSelectionService.select初始writer入口讨论具名等待，要求原SQLite BUSY/LOCKED、无事务/changes0；worker重用现有官方checkpoint、原bundle、完整source、owner/cancel/deadline验证。四控制：①已知初始争用等待并恢复同checkpoint且不重复模型；②UPDATE/COMMIT/其他入口及未知提交不匹配；③错checkpoint/task/source或部分Method拒绝；④cancel/失租约/到期/pending再次busy无成功ACK。等待候选已完成唯一首轮4项私库控制，全部通过；定向Ruff/Pyright 0。当前仍待正式安装及实际复查，旧terminal不自动重开。

本次六GET原件btc-terminal29b01/receipt.json；认证context原件btc-context-idle29b01；窄存储诊断quality-metadata29b01/input-shape29b01/census-metadata29b01及recheck-failure29b01；PC原件artifacts/mr-pc29b/btc-report-quality-timeout03.png、btc-recheck-failed04.png。现有八层33项验收门与固定试验不变。

## 上下文数值比较和同快照重复读取修复

真实BTC的保护块包含完整4项初始资料和1项审查。两次有限只读诊断确认身份、时间、原生关联及12条证据一致；38条事实中8条的唯一差异为`attributes.window_sessions`的JSON数值`5`与类型化数据库DTO的`5.0`。原reader按Python JSON词法SHA比较，导致完整保护块被拒绝。并非模型没有接收资料，也不是发现了不同市场事实。

实现采用已有`provider_input_receipts.tool_arguments_json`的完整跨语言wire比较，仅用于持久DTO与实际保护DTO的一致性；原Provider输入字节/hash、native与实际body的SHA、行引用、accepted状态、身份、时点、事件和权限检查保留。新增控制应证明5与5.0合法一致，同时5与6、布尔/字符串替换、非法非有限数值及不安全整数不获通过；实际认证HTTP回读仍须单独验收。

同快照重复读取的最小修复复用`research_graph_binding._context_valid`现有局部原件机制，替代V3投影中无条件再次准备读取。仅在同物理快照、同源/祖先/原件有效时复用，保留base Run绑定和每次当前权限检查。隔离控制不得代替真实quality/context延迟指标。三处核心修复通过定向控制后合并一次标准发布，再测实际报告和自然复查。

等待修复证据为B2/context-writer-pending30/source-seal02.json及execution01/receipt01.json；数值差异补证为B2/release29/k09-protected-type29b01/receipt.json。首次失败与旧报告均保留。

## 30新核心阻断：完整usage审计（MR16/17）

真实BTC已产生38条内部usage快照，10次取证；checker把usage数量错误地限制为32，因此在第2轮前抛research_policy_trajectory_audit_limit、无artifact。原文422137B不是整组完整编码大小；不能据此跳过8MiB门。

实现范围：research_policy_checker.py保v1字段与规范编码、旧合法原件字节/hash；仅去usage的max32/limit33，按recorded_at+snapshot_id完整逐行读取，以空audit数组的其余snapshot原编码字节加每行精确JSON UTF-8/逗号计算，完整总量仍≤8MiB。保usage原SHA、全列row SHA、physical snapshot/source/readset/fresh writer/monotonic；commands32、round10、tool100及reference原门不改。verified_total_tokens/cost/cancel_drain仍None。旧归档按旧checker/recipe身份读取，旧terminal不重开。

必要控制：真实私库Factory/Graph/Method材料准备→完整38行快照→store/COMMIT→新连接saved reader；追加行保旧行；第33行坏SHA/保存body与hash篡改/旧行删除/8MiB超限拒绝；command33仍拒绝；旧v1字节往返相同。候选控制P不授真实报告P，随后标准发布与PC实测。实时通道realtime_channel_failed与PC自动更新停滞另按真实首原因记录，不能泛称SDK/Docker故障。

31标准发布结果：上述usage整改3控P、PC只读刷新1控P，2生产/2控制源已提升；实际3Ready/7新心跳在线。只解除usage数量误限，其他原门不改；私库typed Host仍degraded/not_applicable，不授真实报告。当前沿新BTC请求继续真实验收。30实时stderr实际21条具名OperationalError/TimeoutError首因尚未知，补安全诊断再修业务；不借7online授通道无异常。
本次31真实验收原件：B2/release31/btc-stage31_05.json、btc-terminal31_01/receipt.json、report-structure31_01.json、profile-preflight31_01.json；PC artifacts/mr-pc31/btc-report06.png及btc-publication07.txt。先做一次有限耗时分段，再修已证首因；原45s/完整原件/权限和时间门保留。

### 31实际质量读取成本与下一项修复

一次实际modeRO/BEGIN质量读取26.565s、threadCPU26.281s，SQL0、HTTP0、模型0、原source/事务门不变。六段排除嵌套后：执行/recipe10.244s、发布/source7.642s、usage6.339s；完整usage6次、verify5次、method/generation各10次，provider input231/row668。它证明本读CPU及重复链，不证明并发45s已修。原件B2/quality-census-cost31/actual02。候选仅复用同物理scope内完整认证的exact材料，每次fresh原件身份/SHA/分母，出口禁memo完整重算；permission/lease/decision不缓存、跨事务/未知marker不复用。先具名正负控、一次正式发布，再真实PC/GET。Host过去24h复查与中文/长精度/重复缺口另按独立门修；不得用降低report/财务门换P。
