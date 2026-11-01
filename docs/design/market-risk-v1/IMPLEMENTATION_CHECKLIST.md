# 改造清单与现行交付状态

原B0–B4、八层33项与原验收门保留。2026-10-13最新状态：83当次正常启动与多条BTC/ETH保存/独立确认/PC读回通过；[PC工程目标](PC_CORE_FINISH_2026-10-13.md)active，完整产品按[八层与多资产P0–P5](CROSS_ASSET_CORE_PLAN_2026-10-13.md)追踪。黄金/纳指/恒科、宏观真实输入、记忆增益和预测评价未完成。下表末列保留原历史快照，最新回执优先读[当前入口](CURRENT_FOCUS.md)。

历史检查（2026-10-11）：当时8047健康/原BTC状态GET连接失败，8047/3097/8038无监听、supervisor为startup_failed；当时新BTC Saved/ACK与完整PC闭环未过。该失败与55历史证据保留，不能当作83当前检查。末列涉及55 running/paused按2026-10-10检查点理解。

原需求、源码入口及原验收门在前五列保持；末列为历史逐项状态。[当前步骤](CURRENT_FOCUS.md) · [技术方案](TECHNICAL_DESIGN.md)。

| ID | 批次/层 | 原需求 | 当前源码/合同 | 原验收门与已有证据 | 现行状态 |

|---|---|---|---|---|---|

| MR01 | B0 | F1–F5可行性探针、scope/time/label/matching协议冻结 | `K/ports/market_path.py`<br>`K/ports/path_forecast.py` | 原始数据、实际版本、pass/fail/unknown、有限尝试；[路径原件][data]、[通知][f3]、[监控][f2] | 55构建/读回/正常START/Ready P，服务运行、schema0091；目标paused，计时66.9h。同原BTCqueued/SavedACK0、完整报告未过；原33/48/协议及所有F保持。 |

| MR02 | B0 | 标准受管升级、备份、迁移副本、回滚方案 | `infra/dsh/run-product.ps1`<br>`infra/dsh/build-product.ps1`<br>`packages/evals/assessment_basis.py` | 正式与候选身份区分，无旁路发布；[11晋升][promotion11]、[11构建][build11]、[11启动][ready11]；[13晋升][promotion13]、[13构建][build13]、[13启动][ready13] | 55唯一runtime晋升、标准UI/Host各1、完整读回/CheckOnly/START/Ready P。三200/七fresh、0091；原65/3configs/权限/协议保持，0092未装。历史54STOP/Ready及早期F不改。 |

| MR03 | B1/L1 | BTC/ETH完整closed 1m路径接口、分页与原件 | `P/market/okx_path.py` | A01/A03/A05，重复/冲突/游标不推进/缺口；[B1正式][b1]、[路径原件][data] | 已验收：分钟路径正式工程范围；罕见自然样本继续单列。 |

| MR04 | B1/L2 | market_candles及追加版本、规范价格单位 | `K/application/market_path_store.py` | A02–06；scope/venue/price_type隔离；[B1正式][b1]、[数据控制][data] | 已验收：原件/追加版本/单位隔离工程范围。 |

| MR05 | B1/L2 | 宏观日期语义、known/public时点与修订 | `P/macro_market/fred_time.py` | FRED观测期不冒充公开时点；旧Run保持；[时点控制][macro]、[03实测][read03] | 旧FRED描述性兼容P；public/vintage/共识不足。46 BLS日历/发布历史HTTP403及至少24h退避实际PC已见，PIT资格未过。 |

| MR06 | B1/L3 | 固定特征与不可变feature manifest | `K/application/market_features.py` | 连续收益/波动/成交/价差，缺数unknown；[B1正式][b1]、[数据控制][data] | 已验收：固定特征/manifest工程范围，不授未来预测资格。 |

| MR07 | B1/L3 | long/short MAE、MDD_close、穿越区间 | `K/decision/path_risk.py` | A07–10；首锚点固定、同分钟顺序未知；[B1正式][b1]、[路径数学][math] | 已验收：已观察路径计算，分钟内穿越先后保持未知。 |

| MR08 | B1/L5 | 受限条件DSL及active/paused/degraded/expired | `K/application/risk_monitor.py` | A15/A18/A20，不执行任意表达式；[B1正式][b1]、[监控控制][f2] | 已验收：受限DSL与持久状态工程范围。 |

| MR09 | B1/L5 | 持久监控租约、generation fencing、evaluation | `K/persistence/risk_records.py` | F2/A09；并发、崩溃和旧owner验证；[B1正式][b1]、[监控控制][f2] | 已验收：租约/fencing/崩溃隔离工程范围。 |

| MR10 | B1/L5 | episode告警、冷却、恢复、缺数保留风险 | `K/application/risk_research_admission.py` | 重复触发不重复告警；unknown不解除；[B1正式][b1]、[10终态][terminal10] | 原48三次失败/no Run保持；49新意图正常准入，同一DSH Run自然完成并保存degraded摘要，10工具结束；告警自然预警正例与完整交付仍待。 |

| MR11 | B1/L5 | V2通知subject兼容、dispatch claim与重试 | `K/application/outbox.py` | F3/A16–19；旧行不重发，外部未知可见；[通知控制][f3]、[B1正式][b1] | 已验收：V2兼容与claim工程范围；外部至少一次重复窗口仍存在。 |

| MR12 | B1/L5 | RealtimeScheduler独立风险通道与健康 | `W/market_risk_loops.py` | O01/O02/O05；不等待DSH深研究；[B1正式][b1]、[PC10][pc10] | UTC04双资产slot/ACK/publication按时P；05双forecast ACK P。BTC05已合法隔离、原提交UNKNOWN及围栏保留，不冒同机会恢复；ETH05正常publication ready/on_time。后续自然调度/全性能待；维护缺失保留。 |

| MR13 | B1/L6 | risk API/生成Py-TS合同/PC最小风险卡 | `A/risk_routes.py` | O04/A21–23；strict DTO兼容；[B1正式][b1]、[PC10][pc10] | 48首页实际风险入口已可见，ETH/BTC切换先清空旧资产P；原身份恢复/返回P。全PC与并发性能未过，验证短期失效复杂度已列改造。 |

| MR14 | B1/L6 | 告警display receipt/ack及刷新回执 | `A/risk_routes.py` | O03/A22；列表发布与实际显示分开；[B1 PC][b1pc]、[PC10][pc10] | 已验收：既有display/ack工程范围；新自然预警正例仍待。 |

| MR15 | B2/L4 | BTC/ETH现货研究scope正式准入 | `K/application/spot_scope_admission.py`<br>`packages/workbench_adapters/durable_research_gateway.py`<br>`K/application/risk_research_admission.py`<br>`K/application/research_tracking.py` | 新品种不能借永续准入；真实回执；[10终态][terminal10]、[11启动][ready11]；[13真实请求][requests13] | 49同一合法BTC意图完成真实DSH并保存摘要；12真实输入/结束回执及上下文绑定P，10工具完成。完整报告仍待，usage partial。ETH完整新研究与普通/自然交付仍待。 |

| MR16 | B2/L4 | 统一QualityDecision和challenge处理 | `K/application/research_quality_projection.py` | A11–14；阻塞反证不能充分；[10终态][terminal10]、[PC10][pc10] | 旧ETHSaved/ACK/原下载历史P；同原BTC真实degraded摘要14/8/4反方1review/10tools，55完整bundlequeued/gen21/v24→HTTPv26，SavedACK0、CAS0。实际初采重复fullmaps，scope55只后半段；核心门未过。 |

| MR17 | B2/L4 | 关键上下文保护、预算未解决、机制取证提案 | `K/application/research_context_delivery.py`<br>`K/application/provider_input_receipts.py` | Q05；不私改冻结需求，实际tier可核；[11来源修复][callback11]、[10终态][terminal10] | 49新BTC原生12输入/12结束/18emit及完整绑定核P；usage字段仅partial，不等于子会话/费用/drain完整分母。原完整上下文、预算、认证与Q05/Gate门保持；Q05两方法及理解/复核仍待。 |

| MR18 | B2/L5 | 事件/定时/告警自动研究与durable复查 | `K/application/risk_research_admission.py` | 原报告建议落成实际计划，子Run幂等；[10终态][terminal10]、[等待提示][wait04]；[13人工入Run][requests13] | 人工BTC真实Run和摘要P；原ETH30m/24h失败原因PC可读。53原租约内收尾已安装，但新合法复查未过；另一Run callback内部异常仍未知，不称已修。 |

| MR19 | B2/L6 | 首屏/完整报告统一投影与最强反方 | `UI/product/ResearchQuality.tsx`<br>`A/research_quality_routes.py` | A25/U01–03；数值、风险资格和报告一致；[UI11][ui11]、[PC10][pc10]；[13实际附件][download13]、[原报告][original13] | 旧ETH快读/PC原下载P，完整audit30.541s性能F。54测试质量用途、选中加载、原749段title URI与末页返回具名PC P，对应53三个F保留；全PC待。 |

| MR20 | B2/L6 | 价格/事实/事件/报告/告警/中断时间线 | `packages/query_views/market_risk.py` | A24；2000点聚合不丢极值/告警/缺口；[PC10][pc10]、[03实测][read03] | 原100 HTTP p95=.6453166s F>.5保留；53 PC实际29原分钟/1缺口正确显示本次时点与缺口P，不改网格/数值。不重复未变压测。 |

| MR21 | B2/L6 | 暴露敏感性情景与适用边界 | `UI/product/RiskDashboard.tsx` | long/short分开；无账户规则不报个人清算；[B1 PC][b1pc]、[PC10][pc10] | 已实现待验收：多空线性情景实现、既有10倍±200证据复用；不报个人清算/预测。 |

| MR22 | B3/L7 | 预测/路径job/成熟观察/评价持久合同 | `K/application/risk_path_ledger.py` | 固定cutoff/window/anchor，原件认证；[原清单证据快照][oldcheck] | 已实现待验收：持久合同/PIT控制已装；成熟原件与完整分母还须自然窗口/有限核对。 |

| MR23 | B3/L3 | BASE_EMPIRICAL_V1与单候选COND_VOL_V1 | `K/decision/path_forecast.py` | 参数manifest，样本不足未知，不伪校准；[原清单证据快照][oldcheck] | UTC04原slot/ACK/publication按时P；05 BTC已合法隔离且原提交UNKNOWN，ETH05 publication ready/on_time。原两模型fit0/60、分组0/30不重训、不冒充校准；维护缺失与统计不足保留。 |

| MR24 | B3/L7 | matching.v1、成绩分母、CI及全量覆盖 | `K/decision/path_forecast_score.py`<br>`K/decision/path_score_inference.py` | A27/Q01–04；相关资产不充独立样本；[原清单证据快照][oldcheck]、[PC10][pc10] | 已实现待验收：V2 CI/matching/全分母实现；真实100独立日/30事件与自然预警效果不足。 |

| MR25 | B3/L7 | 固定28日shadow注册/自动观察/有限结项 | `K/application/path_shadow_jobs.py` | 实际前瞻不可回填，no_gain/insufficient保留；[原清单证据快照][oldcheck] | 已实现待验收：28日有限试验已登记，不回填/不续开；两个fit冻结0N且不重训，等待28日不能补训练，须明确不足，旧试验依固定期限有限结项。 |

| MR26 | B3/L6 | 成绩页与方向/路径/反应/预警分别展示 | `UI/product/RiskPathScore.tsx` | 展示误报漏报缺数及相对基线增益；[PC10][pc10]、[原清单证据快照][oldcheck] | 原48机会分母保持；04双publication按时P，05 BTC隔离后原提交UNKNOWN、ETH05 publication ready/on_time；06维护事实保留，不回填伪按时。真实成熟、全分母、统计/金融待。 |

| MR27 | B4/L8 | 类型化历史案例/失败路由/相似与差异 | `W/market_case_context.py`<br>`K/application/market_case.py` | known_at检索、有/无记忆对照、原件可追；[案例控制][cases]、[PC10][pc10] | 已实现待验收：16b ETH同Run诊断case mcase_a812b2b0d505ff6de933446386417309969d307a已落，eligiblefalse；缺成熟标签及方向方法，actualconsumer作用和增益仍未证。 |

| MR28 | B4/L8 | 来源/特征/模型/角色/规则独立升级和回滚 | `W/market_component_bootstrap.py`<br>`W/market_component_versions.py` | 每种稳定合同、shadow→审核→pointer；[跨进程组件][components]、[11晋升][promotion11] | 55scope已正常安装且保原uncached入口/成功出口及54十DTO namespace。实际初采未覆盖，完整报告仍F；65/3configs/2MiB32MiB/CAS/unknown/预算保持。audit11+0092桥、diag02仅准备/独审P未装。 |

| MR29 | B4/L1–3 | 黄金MarketSpec、来源、日历、标签准入 | `K/ports/market_path.py`<br>`K/application/market_scope.py` | 单独真实报价和期限，不借GC宣布XAU可用；[技术边界](TECHNICAL_DESIGN.md)、[原清单][oldcheck] | 外部待验证：MarketSpec/准入准备已实现；黄金实际供应商/权利/报价未开通，不能借其他资产准入。 |

| MR30 | B4/L1–3 | 宏观/仓位/预期差增强路线与数据资格 | `W/market_enhancement_bootstrap.py`<br>`K/application/market_enhancements.py` | MACRO_STATE/CROWDING/EVENT_SURPRISE各候选注册；F5决定研究资格，缺源显式待验证；[03实测][read03]、[原清单][oldcheck] | 6路线注册、两旧FRED描述性level/90拒绝P；BLS403/24h退避真实可见，仓位/共识/vintage不足，不晋级模型。 |

| MR31 | 全批/L6 | 保留已有来源、资料、研究、反馈、历史主线 | `UI/App.tsx` | 每批受影响回归；不隐藏高级入口；[PC10][pc10]、[11已读恢复][readpc11]；[13实际附件][download13]、[13普通请求][requests13] | 原来源/资料/研究/反馈/历史主线保留。54默认排除测试资料、质量与用途禁新研究、加载、原749段title URI及751–770末页返回PC P；完整BTC/自然复查/全PC待。 |

| MR32 | 全批 | 自动化工具、PC截图、用户手册与交付说明 | `docs/design/market-risk-v1/PC_USER_GUIDE.md` | 实际运行/版本/步骤/错误/修复均有证据；fixture不冒充真实AI；[11构建][build11]、[11启动][ready11]、[10终态][terminal10]；[13构建][build13]、[13启动][ready13]、[13下载][download13]、[BTC首页][btcpc13]、[ETH首页][ethpc13] | 55build/读回/START/Ready P与PC排队无下载已记，目标paused/66.9h。7pureP不冒真实报告；单次无locals栈证初采fullmap重复，未证无限loop。180仅lease非deadline/heartbeat；SavedACK/全PC/金融待，所有F保留。 |

| MR33 | 全批/L8 | 接续一页纸、机器状态、批次结束及偏移测试 | `docs/design/market-risk-v1/CURRENT_FOCUS.md`<br>`docs/design/market-risk-v1/execution-contract.json` | M01–03；唯一当前batch，新指令优先；[接续实测][continuation] | 目标active，原33前5列/机器JSON/28日合同不变；一页任务只列当前结果与待办，历史原件按问题读取。实际效果未宣称完成。 |

## 状态边界与历史原件

工程实现、具名有限验收、正式用户动作和金融效果分别判断。自然28日/成熟标签、独立样本及外部来源不伪造通过，也不要求当前目标无限重构等待；范围与固定结束条件见[当前交付状态](CURRENT_DELIVERY_STATUS.md)。

本次删去的05–10长段未丢弃：完整原字节及哈希保存在[清单原件][oldcheck]和[交付状态原件](../../../artifacts/market-risk-implementation-20261007/B2/docs-current11/before/docs/design/market-risk-v1/CURRENT_DELIVERY_STATUS.md)。这些是历史，不能覆盖本页现行状态。[首原因与unknown边界](../../../artifacts/market-risk-implementation-20261007/B2/docs-terminal20-cause14/cause-facts14.json)仅授已保存execute错误；官方根会话turn aborted无terminal SDKerror，不证明Docker/SDK故障。每个新失败只补对应有限门，未变已P不重测；M01–03隔离样本不证明自然生产恢复。

[b1]: ../../../artifacts/market-risk-implementation-20261007/B1/formal-install-acceptance01.md

[b1pc]: ../../../artifacts/market-risk-implementation-20261007/B1/pc-browser-acceptance01.md

[data]: ../../../artifacts/market-risk-implementation-20261007/B1/market-data-receipt.md

[math]: ../../../artifacts/market-risk-implementation-20261007/B1/market-path-receipt.md

[macro]: ../../../artifacts/market-risk-implementation-20261007/B1/macro-time-receipt.md

[f2]: ../../../artifacts/market-risk-implementation-20261007/B0/F2-monitor-junit01.xml

[f3]: ../../../artifacts/market-risk-implementation-20261007/B0/runtime-F3-notification-acceptance.md

[read03]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-index03-check01/actual01/ACTUAL01.md

[promotion11]: ../../../artifacts/market-risk-implementation-20261007/B2/release11/promotion11.json

[build11]: ../../../artifacts/mr-release-b2b4-11/build-execution.json

[ready11]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-start11/receipt01.json

[terminal10]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh10/execution01/receipt.json

[pc10]: ../../../artifacts/mr-pc10/actions01.json

[callback11]: ../../../artifacts/market-risk-implementation-20261007/B2/provider-input-callback11/receipt01.json

[ui11]: ../../../artifacts/market-risk-implementation-20261007/B2/quality-readable-download11/DELIVERY11.md

[readpc11]: ../../../artifacts/mr-pc11/read-recovery01.txt

[continuation]: ../../../artifacts/market-risk-implementation-20261007/B2/continuation-eval10/review01.json

[wait04]: ../../../artifacts/market-risk-implementation-20261007/B2/research-wait-copy04/DELIVERY04.md

[cases]: ../../../artifacts/market-risk-implementation-20261007/B4/case-context-receipt01.md

[components]: ../../../artifacts/market-risk-implementation-20261007/B4/component-versions-delivery06.md

[oldcheck]: ../../../artifacts/market-risk-implementation-20261007/B2/docs-current11/before/docs/design/market-risk-v1/IMPLEMENTATION_CHECKLIST.md

[promotion13]: ../../../artifacts/market-risk-implementation-20261007/B2/release13/promotion13.json

[build13]: ../../../artifacts/mr-release-b2b4-13/build-execution.json

[ready13]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-start13/receipt01.json

[requests13]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh13/request-observation01.json

[download13]: ../../../artifacts/mr-pc13/download-verified02.json

[original13]: ../../../artifacts/mr-pc13/original-eth-report01.json

[btcpc13]: ../../../artifacts/mr-pc13/btc-request01.png

[ethpc13]: ../../../artifacts/mr-pc13/eth-request-saved02.png

[build17]: ../../../artifacts/mr-release-b2b4-17/product-validated-builds.json

[ready17]: ../../../artifacts/market-risk-implementation-20261007/B2/formal-start17/receipt01.json

17–21历史阶段与完整旧状态见[清单历史原件](../../../artifacts/market-risk-implementation-20261007/B2/docs-running21b01/before/docs/design/market-risk-v1/IMPLEMENTATION_CHECKLIST.md)，33项原门不变。

历史21b终态核对 2026-10-09 03:48（北京时间）：[双失败原件](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh21b/status04.json)、[execute首异常](../../../artifacts/market-risk-implementation-20261007/B2/worker-terminal21b-btc-diagnosis/safe-summary02.json)；原始异常未保存exact stack，终态冲突字段后来已核对，见CORE_RECOVERY_PLAN24；当时exact stack仍未知。该历史两Run均无artifact，未执行无效的完整报告六GET验收。PC旧同页脚本在一次reload后已载入21b；同一原请求持续执行，不新增提交。

本次31真实验收原件：B2/release31/btc-stage31_05.json、btc-terminal31_01/receipt.json、report-structure31_01.json、profile-preflight31_01.json；PC artifacts/mr-pc31/btc-report06.png及btc-publication07.txt。先做一次有限耗时分段，再修已证首因；原45s/完整原件/权限和时间门保留。
