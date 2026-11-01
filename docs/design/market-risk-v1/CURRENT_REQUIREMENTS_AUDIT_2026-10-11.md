# 原需求完成情况与新核心目标对照

核对日期为2026年10月11日北京时间。**原 B0–B4 八层33项需求未全部完成。** 原清单明确标注工程范围已验收的为 MR03、04、06、07、08、09、11、14；其他项有局部通过或实现，但其完整验收门、真实交付或数据资格仍有缺口。下表只说明各项证据覆盖，不换算成产品完成百分比。

## 当前阅读入口

原33项不是全部完成：总判断仍为 **8限定 / 22部分 / 3未满足**，G1–G6未整体通过。[当前执行入口](CURRENT_FOCUS.md)持续记录最新实操与剩余项，下面的逐项表保留原验收基线。

- BTC65手动报告及30分钟自然复查已经有完整保存、独立确认、PC下载与刷新局部实证；仍须内容、监控触发与整门验收。
- 2026-10-13当前：83正常启动当次通过；原a748及真实ETH事件研究已有完整保存/确认，自然30分钟与原24小时复查恢复及PC读回有实证。完整金融资格与核心六门仍未整体通过，见[当前短入口](CURRENT_FOCUS.md)及[八层/多资产能力映射](CROSS_ASSET_CORE_PLAN_2026-10-13.md)。- release79历史首败：官方构建通过，但原a748预验128.852秒失败，首拒绝为构建注册凭据/共享ROOT history冻结读取Owner合同；当时Saved/ACK未完成。此失败保留，不能覆盖后续83真实成功。详见[79实际失败](../../../artifacts/core-goal-20261011/release79-preverify03-actual02.json)。
- 宏观数据资格（MR05/MR30）、黄金准入（MR29）、经验消费与预测增益继续未完成；本次核心修复不将它们记为通过。

## 历史局部差异（实证截至2026-10-11T04:43:16.686801Z）

本节保留当时局部差异；下方33项表及初始检查保留为原验收清单，历史“新BTC保存/ACK未过”不代表当前全部状态。原总判断仍为 **8限定 / 22部分 / 3未满足**，G1–G6未整体通过。

- **MR16/MR19**：BTC64完整确认和65原JSON/Markdown回读局部P、ETH63重启原件/反馈保持；新BTC65 native按原D完成、业务degraded/Artifact已生成，04:41:23 Job queued/gen1/state_version3、Saved/ACK0，新完整字节与内容待验，不用旧确认充数。
- **MR18/G1/G4**：原ETH64自然触发后失败保留；手动ETH65再failed，可信新cause `research_context_selection_permission_lost`，不套用64 wait15s旧因。BTC64当前PC登记72h今日05:21:20 UTC/24h14:40 UTC planned、无child；物理谱系、既有监控/事件报告链及自然Saved/ACK均待。
- **MR13/MR20/G5**：64真实publisher重叠summary-100 p95 BTC0.5329913/ETH0.5381418超过0.5秒F保持；65唯一observer已有限27m exit0、formal0/overlap UNKNOWN，无新成绩。summary/timeline原门分开，不复采换阈值。
- **65工程/PC**：14生产源/0tests、build/投影/原两RO及Ready02当次P保持，不授连续Ready。04:43:04 ETH失败/3证据/0review可见、未再次retry，3/3在线仅单点。BTC收口理由显示缺口的DEV两UI源candidate03已封未装；金融/来源/资料与全PC缺口未完成。

当前证据：[终态](../../../artifacts/core-goal-20261011/publication65_terminal01.json)、[计划当前PC](../../../artifacts/core-goal-20261011/pc65_01/btc64-natural-plan65-refresh01.json)、[ETH失败PC](../../../artifacts/core-goal-20261011/pc65_01/eth65-failure-terminal01.json)、[有限观察](../../../artifacts/core-goal-20261011/performance65-observation01/observation-summary.json)；可信cause见DEV `eth-binding65_01/physical-ro01.json`，短入口[CURRENT_FOCUS.md](CURRENT_FOCUS.md)。

## 初始检查（历史保留）

用户已结束旧目标并授权新目标，新目标为 active。今天初始检查的8047健康与原BTC报告状态均连接失败，8047/3097/8038无监听；昨天55 Ready是历史启动证据。服务退出原因由当前执行核查，不能推定为SDK、Docker或内存。当前检查见 [初始回执](E:/project/study/codex/dsh/decision-hub/artifacts/core-goal-20261011/initial-status01.json)。

## 33项交叉表

“已验收工程范围”保留具名历史工程结果，不授预测优势或当前在线资格。“待完整验收”包括已经局部验收但仍有失败的项目。G1数据/触发、G2真实分析/报告、G3保存/独立确认/读取、G4自动复查、G5 PC/性能、G6执行与恢复，定义见 [新核心目标](CORE_DELIVERY_GOAL_2026-10-11.md)。

| ID | 原需求简述 | 完成判断 | 确切剩余 | 新目标或后续路线 |
|---|---|---|---|---|
| MR01 | F1–F5探针与scope/time/label/matching冻结 | 待完整验收 | 有限探针已收口；F4真实质量门、F5数据不足仍保留 | G1、G2、G6；统计资格后续 |
| MR02 | 标准升级、备份、迁移副本与回滚 | 待完整验收 | 标准发布/迁移历史P；当前正常启动与实际回滚恢复仍待 | G6 |
| MR03 | BTC/ETH完整closed 1m路径、分页与原件 | 已验收工程范围 | 当前实时采集和PC数据重新接入闭环；罕见自然样本单列 | G1 |
| MR04 | candles追加版本、价格单位隔离 | 已验收工程范围 | 新闭环核对实际时点、版本与单位 | G1、G3 |
| MR05 | 宏观日期、公开时点与修订 | 未完成数据资格 | FRED描述性兼容已P；public/vintage/共识不足，BLS403与退避保留 | 宏观数据路线 |
| MR06 | 固定特征与不可变manifest | 已验收工程范围 | 当前数据缺口/freshness与报告实际引用 | G1、G2 |
| MR07 | 多空MAE、MDD与穿越区间 | 已验收工程范围 | PC展示一致；分钟内先后未知继续保留 | G2、G5 |
| MR08 | 受限DSL与active/paused/degraded/expired | 已验收工程范围 | 新主线实际操作与持久状态回读 | G1、G5 |
| MR09 | 监控租约、generation fencing与求值 | 已验收工程范围 | 当前受管恢复、阶段可见及新流程重启回读 | G6 |
| MR10 | episode告警、冷却、恢复与缺数保留 | 待完整验收 | 自然告警预警正例、实际触发研究及完整交付 | G1、G3；效果统计后续 |
| MR11 | 通知V2兼容、claim与重试 | 已验收工程范围 | 外部至少一次重复窗口保留；新触发实际送达另验 | G1、G6 |
| MR12 | 独立实时风险调度与健康 | 待完整验收 | 已有自然slot/ACK局部P；当前连续运行、完整性能与恢复待 | G1、G6；全固定试验后续 |
| MR13 | risk API、Py/TS合同与PC风险卡 | 待完整验收 | 首页/切换局部P；全核心PC、并发性能、权限恢复待 | G1、G5 |
| MR14 | display receipt/ack及刷新回执 | 已验收工程范围 | 新自然预警实际显示/ACK不由隔离样本替代 | G1、G5 |
| MR15 | BTC/ETH现货研究scope准入 | 待完整验收 | BTC真实DSH及摘要历史P；新ETH与双资产完整交付待 | G1、G2、G3 |
| MR16 | 统一QualityDecision与反证挑战 | 待完整验收 | 新BTC完整bundle保存/ACK未过；报告与质量一致性待 | G2、G3 |
| MR17 | 关键上下文、预算与机制取证 | 待完整验收 | 实际输入绑定局部P；usage为partial；真实Q05材料、两方法比较待 | G2；完整Q05后续 |
| MR18 | 事件/定时/告警研究与durable复查 | 待完整验收 | 新合法自动复查完整成功；旧callback异常原因未知 | G1、G4、G6 |
| MR19 | 摘要与完整报告投影、最强反方 | 待完整验收 | 旧ETH下载历史P；新BTC/ETH一致性、完整审计30.541秒性能F | G2、G3、G5 |
| MR20 | 价格/事实/事件/报告/中断时间线 | 待完整验收 | 实际分钟与缺口局部P；100请求p95=0.6453166秒超过0.5秒 | G5 |
| MR21 | 多空暴露情景与适用边界 | 待完整验收 | 情景实现与既有数值证据保留；当前PC操作验收，不报个人清算 | G2、G5 |
| MR22 | 预测/路径job、成熟观察与评价账本 | 待完整验收 | 成熟标签认证和完整分母、固定窗口自然观察 | 预测评价路线 |
| MR23 | 基线与单候选条件波动模型 | 待完整验收 | 两模型fit 0/60、条件组0/30；无合格概率，不以保存预测算校准 | 预测模型路线 |
| MR24 | matching、全分母、成绩与CI | 待完整验收 | 100独立时间block、30事件block、自然预警效果不足 | 预测评价路线 |
| MR25 | 固定28日shadow与有限结项 | 待完整验收 | 固定期限结项尚未到；训练不足不能靠等待补齐，不回填 | 固定试验路线 |
| MR26 | 方向/路径/反应/预警成绩分开展示 | 待完整验收 | 原48机会与UNKNOWN保留；成熟分母、正式成绩PC与金融资格待 | G5保留入口；效果评价后续 |
| MR27 | 类型化案例、失败路由与相似差异 | 待完整验收 | 已有case落库但eligible=false；真实消费与有/无记忆增益未证 | 经验能力路线 |
| MR28 | 来源/特征/模型/角色/规则独立升级回滚 | 待完整验收 | 跨进程机制fixture已P；正式独立组件升/回滚及资格待 | G6必要兼容；组件路线 |
| MR29 | 黄金MarketSpec、来源、日历与标签 | 未完成实际准入 | 实际供应商、权利、报价与独立准入未开通 | 黄金路线 |
| MR30 | 宏观状态、仓位、预期差增强 | 未完成数据资格 | 路线登记已做；仓位/共识/vintage及真实来源资格不足 | 宏观与拥挤度路线 |
| MR31 | 保留来源/资料/研究/反馈/历史主线 | 待完整验收 | 入口与资料局部PC P；受影响主线完整回归待 | G5 |
| MR32 | 自动化、PC截图、手册与交付说明 | 待完整验收 | 工具/截图/手册已有；真实双资产完整验收与可交付说明待 | G1–G6 |
| MR33 | 短接续、机器状态、批次结束与偏移测试 | 待完整验收 | 新范围接续/结束检查；清单和机器状态漂移需纠正 | G6 |

## 文档状态与证据边界

核对时ROOT和DEV原清单相同，但清单头仍写“目标active、54 STOP、55待启动”，末列又写“55 running、paused”；机器合同的current_goal_status/next_action仍引用旧46/旧待办。它们都不能覆盖今天的新目标和离线检查。原33项前五列与原协议必须保留，更新只作用于当前状态、待办与新目标入口。

主要依据为 [原清单](IMPLEMENTATION_CHECKLIST.md)、[55核心结果](CORE_ACCEPTANCE55_RESULT.md)、[10月10日复盘](PROGRESS_REVIEW_2026-10-10.md)、[原验收与指标](ACCEPTANCE_AND_METRICS.md)。另核对了 [B1正式工程验收](E:/project/study/codex/dsh/decision-hub/artifacts/market-risk-implementation-20261007/B1/formal-install-acceptance01.md)、[路径原件及数学](E:/project/study/codex/dsh/decision-hub/artifacts/market-risk-implementation-20261007/B1/market-path-receipt.md)、[历史ETH原下载](E:/project/study/codex/dsh/decision-hub/artifacts/mr-pc13/download-verified02.json)、[BTC55状态读取](E:/project/study/codex/dsh/decision-hub/artifacts/market-risk-implementation-20261007/B2/release50/btc-status55_01/receipt.json)及B4案例/组件有限控制回执。

工程P、HTTP200、Ready、摘要保存、异步受理及fixture成功均不替代完整报告交付或金融效果。新核心目标通过后，上表后续路线仍未自动完成。
