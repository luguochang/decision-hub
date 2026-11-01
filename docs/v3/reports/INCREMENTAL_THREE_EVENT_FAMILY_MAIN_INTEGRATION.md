# 三事件族后端接回与新日线实测

更新：2026-10-27 13:24 +0800。17个后端源已实际接入原Main，49个物理Main用例通过。当前唯一原normal仍在运行，原网页维护停止；已保存报告可离线阅读。本记录不表示完整事件族、金融资格或整个项目已交付。

## 已实际完成

- 一个原DataJob POST202，真实任务datajob_2768b679e558463d9149e2cc23415d72终态completed/attempt1。BTC、ETH各90个连续UTC日线，窗口[2026-07-29T00Z,2026-10-27T00Z)，包含10月26日完整已收盘日。两条原Gate200 capture、8分区及manifest经过原严格reader和adapter回读；Root有限独立文件核验14唯一资源通过，旧ETH manifest及分区保持。
- 17生产Python路径：5旧文件完整前像先保存、12新增；原PCE/NFP/FOMC分派互斥，原CPI/numeric路径保持。事前原件规则经preparation→manifest→replay保留，actual读取保持原件/同poll/冻结选择器约束。未知项不补成已知，不猜未来URL、共识或概率。
- 组合独审C0/I0/M0。新12生产源及5测试/辅助文件在声明的Main继承Unknown配置下strict/Ruff零诊断；旧4源在同1725文件完整context的完整multiset为118→71新增0、Ruff1→1新增0，未宣称全Main清零。
- 物理Main实际49 PASS，pytest19.52秒、harness24.4773436秒，17模块真实路径及215依赖均来自Main；21 SQLite都位于新D测试目录，guard拒绝事件0，17源前后SHA保持。Main01初次在业务测试前被合法共享venv元数据路径拒，Main02只修两个私有canonicalPath表达式和输入manifest，原失败及原断言保留。

## 当前唯一构建

Root session27841，outer9208/creation FILETIME134375512944166982，supervisor11540；UTC2026-10-27T05:08:14.3868133Z启动。原builder14164/creation FILETIME134375512974959587。prepared SHA1ecb07ae25a46967278ac98f60e470511b4d7f239c2213ebf4e78b8f04bc8a02，runner SHA3261c37561e74b3a50696e5299a58d1c529178031e499906a0d4b154e57b0d9d。

161controls、72native输入、5旧源码及672现有输出前像已实核。native输入改动0只描述输入，尚不能据此宣布新的native或graph身份。以实际1f1d normal为CompletedMaintenancePrior，沿原producer及两原guard，保留8GiB child上限和10GiB可用物理内存前件；未另跑H1或standalone fullguard。空日志不代表失败，不重启同一构建。只有真实终态后才做一次有限postflight，再绑定实际normal/native/current并恢复七角色。

## 维护、备份与恢复边界

旧r3已在完整无active/queued研究与DSH会话的边界停止，同session86756/outer3108于UTC04:36:30.0012097Z实际0；7个所属Job全closed/errors[]，六actor原PID加创建时间均消失，八个私有及正式端口原生监听表为空。关闭后全记录query_only读回仍相同，再做最终57文件关闭字节备份：DB4、两个完整profile40、原raw session6、SDK registry/projection7。备份过程不打开SourceSQLite，三方SHA同，凭据仅就地hash，不复制。

r4-r2恢复器已独审C0/I0/M0，新增真实优先研究通道、三后台lane的独立instance/heartbeat ready、精确停止与失败清理；旧graph混搭与瞬时httpx超时门已补。它仍未实际绑定、启动或验证UI3/3。数据库、owner、账户、设置、旧报告、评分和新日线都沿用；不重跑空库initialize或研究POST代替恢复。

授时持久方案已独审并提供可回滚管理员命令，Root未假设用户已执行。之前09:21同步成功是真实历史，11:37偏差约0.6秒仍未构成持续健康。原工具0.25秒为未生产接入的prospective操作阈值，不冒金融门；后续连续观测单独接续。研究/评分时间不倒签。

## 尚未完成

真实事件族仍需完整p/q/共识及首发actual、原审核/watch、通知送达和后继研究；当前NFP真实整段正文兼容及PAPER非零funding结算另在新D修复。PAPER执行尚未配置、真实fill0；一份旧报告已过期且financial_qualified/execution_authorized仍false。六组合14自然日≥90%及P50/P95、四周评分、10成交退出结算重启、30完整失败回放、最终全范围质量和正式部署交接均未全部通过。

原manifest430表示source_files条目（包含文档），不是430个Python；旧口径校正在此，原件和SHA不改。全部56任务/9阶段与原八工作包保留，目标ACTIVE，无新增ACCEPTED。

## 具名证据

- D:/DH-daily-refresh-actual01/postflight.json，SHA8db1ee90bc0a581965f179653029791e0e45d2c81da42389f49cbf3b70b23e17。outer审计的connect事件0不是零HTTP：Windows async ConnectEx未被完整观测，网络事实以原native capture/HTTP回执为准。
- D:/DH-event-three-family-adoption01/adoption-result.json，SHAc8e09c0c93364e02b37fbe8f9895428195abf0abf9e0a67c862f7bdd08bc8a44。
- D:/DH-event-three-family-composite-peer01/review01.json，SHA98bf224005313ad9f47e919c649b7e5513b9dd0408e7af5d2657423081906e49。
- D:/DH-event-three-family-main02/summary.json，SHA2b27515d576c83f5c820c2086a4306d6b2256d2547ccae07e165253c4252edfe。
- D:/DH-work14-priority-transition02/transition.json，SHA69cdb54db8d65900e35407d3f2fb9d7906d3b4a0fe0d2ada93a717a978327a75。
- D:/DecisionHub-v3-development/evidence/legacy-sdk-review/event-three-family-normal-build01/：当前同句柄原构建与前像。
- D:/DH-work14-priority-controller-peer-r2/review.json，SHA696aeb64dd0b361fb9d5d9bd3ab3c5a32262750fe904c79db170a92c3583eecd。
