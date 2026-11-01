# Decision Hub 暂停交接：实际进度、执行错误与未完成项

交接日期：2026-10-27，北京时间。当前用户指令：**停止执行，汇报进度、核对总控、写交接。** 长期目标工具已返回 `paused`，没有标记完成。本文件交接项目事实，不授权下一位继续构建、恢复服务、调用模型或交易。

## 1. 接手者先看这几条

1. **产品没有完成交付，也不是只剩测评或自然时间等待。** 原总控 T01—T08 均未整体关闭。v3 主清单实际为 **56 项、9 阶段**，不是标题写的55项、8阶段。
2. 已有真实研究成功：原系统曾完成研究、独审、持久发布、原页面阅读、JSON/HTML下载和到期评分。它证明主链曾跑通，不能证明稳定六组合或预测胜率。
3. **当前原私有网页服务停止。** 浏览器里仍开着 `127.0.0.1:39142`，缓存页面不证明后台在线。14:03原生监听表确认39141—39144、8047、3097、8037、8038均无监听。
4. 唯一在跑的 normal27841 已按用户要求中止：北京时间 **13:58:47** 同进程句柄返回1，耗时3033.191秒。中止时仍在历史保留准备，UI/Host编译次数均0。原始日志的 `product_build_retention_prepare_failed` 是中止后退出结果，**不应记成一次新产品缺陷或盲重跑理由**。
5. **Main源码领先于最后一次成功构建。** 最新17个生产源码已采用并通过49项物理Main测试，但覆盖这17源的新normal被取消；没有新有效构建回执、postflight或恢复绑定。不能拿上一次成功normal冒充这17源已经发布。
6. 不要从T-000重做，不要重新搭前端，不要重新初始化WORK、账户或研究库，不要重投已经结束的Run。当前工作目录与历史原件都必须保留。

暂停实测见 [进程及端口状态](D:/DH-pause-handoff-20261027/native-stopped-state.json)、[中止构建事实](D:/DH-pause-handoff-20261027/cancelled-build-observation.json)、[有限字节核对](D:/DH-pause-handoff-20261027/finite-byte-checks.json)。本轮交接没有运行产品测试、新研究、SQL查询、模型或恢复服务。

## 2. 用户实际要什么，以及哪份计划有效

用户要的是保留原界面的事前决策产品：真实资料和当时可知信息 → 原因及反证 → 普通方向概率／事件情景p及条件方向q → 含成本多空方案、触发失效 → 持续监控与提醒 → PAPER退出结算 → 结果复盘与下一轮使用。用户已授权常规文档矛盾自主判断，后来明确要求沿原代码修复，不再用重建文档强制替换前端。

需求来源：

- [原总控](E:/project/study/codex/dsh/decision-hub/docs/design/decision-product-control-2026-10-17/00_MASTER_CONTROL.md)、[八工作包实施计划](E:/project/study/codex/dsh/decision-hub/docs/design/decision-product-control-2026-10-17/05_IMPLEMENTATION_PLAN.md)。
- [v3主清单](E:/project/study/codex/dsh/decision-hub/docs/v3/TASK_MASTER_LIST.md)、[最终需求](E:/project/study/codex/dsh/decision-hub/docs/v3/01_REQUIREMENTS.md)、[原诊断与方案](E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md)。
- [原系统增量覆盖账本](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_DELIVERY_COVERAGE.md)保留全部56 ID和八母工作包，不把局部测试换算为完成率。

**现有文档存在状态分叉。** 原总控、10月23日交接及 `execution-contract.json.latest_runtime_checkpoint` 仍留有49c在线、旧PID、25次命令等10月23日快照；其中“PCE/PAPER修复未采用”等描述已被后续增量部分更新。它们不能证明10月27日在线状态。Main `docs/v3/STATUS.md`仅有陈旧8行；formal worktree的STATUS展开27项。不能任选一份旧“唯一真相”继续执行，也不能把不同实例的历史统计相加。

## 3. 原总控 T01—T08 核对

八项均为部分完成，未整体验收；下表区分真实用户结果、局部软件成果和缺口。

| 工作包 | 已有证据 | 未完成 |
|---|---|---|
| T01 真实组件集成 | 原Host/Provider真实4h单例、独审、同Run报告、原PC双导出；最新17源49例 | 修复版稳定六组合、完整失败回放、整链重复可靠性 |
| T02 运行合同与恢复 | 并发/身份/上下文具名修复；失联过期Run沿原claim/checkpoint收尾；七角色候选已审 | 新七角色实际恢复，运行中重启、unknown ACK恰一次、截止内恢复全链 |
| T03 时点、重要事件、宏观 | 日历/议程、PIT基础；双币90闭日日线；PCE/NFP/FOMC部分规则和reader | 真实共识/锚点、完整阶段与提前预案，至少两类真实事件从预案到actual及更新 |
| T04 条件行动与概率 | 真实4h方向概率、明确假设的成本比较、保护和观望转操作条件、独审；六组合入口代码 | 六组合真实终稿和稳定性；事件p/q及预审动作；变化后快确认/重比较；first-touch策略收益比较 |
| T05 监控、修订、提醒 | 周期准入、到期提醒/暂停、受控条件与后继恢复 | 可用自动终稿、真实条件触发→实际送达→合格后继；PAPER生命周期 |
| T06 结果、经验、再次使用 | 旧BTC经验进入真实调用；WORK14一份ETH到期评分；跨期限评分软件 | 四周分层评分/周报、非seed学习完整循环、改善证明、PAPER结果消费 |
| T07 原PC与手册 | 原导航/身份/设置保留；同Run阅读、双导出、部分重启和历史兼容 | 全期限、事件、监控、复盘、PAPER完整用户验收；实测完整手册 |
| T08 正式交付 | 多批精确采用、成功构建、备份及部分历史回读 | 当前有效发布与全部最终指标、完整质量检查及可独立接手的运维交付 |

原计划条目见 `05_IMPLEMENTATION_PLAN.md` 的T01—T08（本次读取时约118—298行）；旧“执行合同尚未创建”文字与已经存在的checkpoint不一致，应视为历史遗留，不能据此再造控制体系。

## 4. v3任务登记与实际交付的区别

| 阶段 | 任务数量／ID | formal STATUS登记 | 当前原产品验收 |
|---|---|---|---|
| 0 基础设施 | 8：T-000—007 | 7 REVIEW；T-000进行中 | 完整存档和恢复清点未收口；部分技术资产可复用 |
| 1 主干 | 6：T-101—106 | 6 REVIEW | 原真实运行与恢复的整体证明不足 |
| 2 普通4h | 10：T-201—210 | 10 REVIEW | 原系统有真实单例；双币稳定完整三连等仍未验收 |
| 3 24h/72h | 3：T-301—303 | 2 REVIEW；T-303进行中 | 当前Main六组合实际稳定性未过门 |
| 4 事件 | 7：T-401—407 | 未展开／NOT_STARTED | 有增量软件，真实事件全链未过门 |
| 5 提醒 | 4：T-501—504 | 未展开／NOT_STARTED | 真实送达及合格后继未验收 |
| 6 PAPER | 6：T-601—606 | 未展开／NOT_STARTED | 可计入本次交付验收的真实成交0 |
| 7 评分学习 | 7：T-701—707 | 未展开／NOT_STARTED | 单例评分不等于四周；完整失败业务回放0/30 |
| 8 最终交付 | 5：T-801—805 | 未展开／NOT_STARTED | 最终稳定性、质量与交接未过门 |

合计56项：formal STATUS中 **25 REVIEW、2 IN_PROGRESS、29项未展开、0 ACCEPTED**。这只是该技术登记表的计数，既不能说“完成25项”，也不能说其余29项完全没有代码。隔离v3的真实/受控测试不能替代原Main产品验收。

T-303的“60例、≥80%、P50/P95 20/40分钟”是阶段标准；**最终仍是六组合连续14自然日≥90%、P50≤15分钟、P95≤30分钟**。80%不是项目完成率。

## 5. 已经完成并能追溯的成果

### 5.1 原报告与预测结果

WORK：`D:/DH-eth4h-live14`。真实ETH4h Run：`run_be0d276c61d5446b9527f145c0f46969`；artifact：`art_fa190171cd604b01be7f61a89c1456cb`。

- 独审approved，四项内容检查passed，报告交付complete；原UI真实展示并下载JSON/HTML。
- 方向概率40%／20%／40%，为**主观研究估计，未校准**。已到期窗口为UTC 2026-10-26 18:27—22:27（北京时间10月27日02:27—06:27），不能当当前建议。
- 同Run在恢复后报告GET200；18项不可变报告字段及source/artifact/publication保持，变化仅read_at。浏览器HTML自包含，内嵌JSON核对通过。
- 首份真实到期结果：下跌，终点close return约-0.52%，Brier 0.56、log loss约0.9162907。是预测结果，不是PAPER成交或校准效果。
- [离线HTML](D:/DH-work14-restored-readback01/reading.browser.html)、[离线JSON](D:/DH-work14-restored-readback01/reading.browser.json)可继续阅读。JSON SHA：`ff3458a3996b4aae8f27f89f848cfcc2f0c2af94201a9b7db989bc92a10e536a`。

本次独立只读复核认为，这份14报告具备文档六条**研究内容**条件：冻结资料、完整概率、6关键/3反证、含明确假设的多空/flat成本比较及保护/观望条件、独审、重启后可读。此意见未写回旧统计、未追认整个长期认证。它不证明first-touch EV、完整可执行比较、真实账户费率、未来资金费或执行许可。

`financial_qualified=false`、`execution_authorized=false`保持原样。不能改成true制造授权；也不能只凭这两个false否定原文允许的研究估计。

### 5.2 数据、代码、构建

- 原DataJob真实刷新BTC/ETH各90个连续已闭UTC日，范围[2026-07-29T00Z,2026-10-27T00Z)，末根10月26日。唯一job `datajob_2768b679e558463d9149e2cc23415d72` completed/attempt1，原件及分区回读通过。见 [实际刷新后验](D:/DH-daily-refresh-actual01/postflight.json)。
- 此前19源事件/PAPER期限等增量已接Main，并完成当时的成功normal；后续三族17源（5旧/12新）也已采用，旧5源前像保留，物理Main49例通过。见 [17源采用回执](D:/DH-event-three-family-adoption01/adoption-result.json)、[物理Main49例摘要](D:/DH-event-three-family-main02/summary.json)。这些都不是两类真实事件全链或真实成交证明。
- 新增范围有scoped strict/Ruff零诊断；旧4模块同一完整context仍有71项类型诊断、Ruff旧1项仍在，不能称整个实际交付代码strict/Ruff零错。
- 六组合在原UI和 `/v1/decision-settings/research` 均有代码接线。每次新研究仍须合法保存设置、对应合约元数据及现场采集的1441根已闭分钟线；90日日线不能替代。当前BTC合法设置是否存在，未读取当前API/SQL确认。
- 原六角色曾恢复，缺少priority研究worker导致侧栏2/3已有归因；r4-r2七角色候选已独审，**尚未绑定、启动或验得3/3**。

## 6. 最终交付还差什么

| 最终要求 | 当前证据边界／缺口 |
|---|---|
| 六组合14自然日≥90% | 当前Main未形成通过验收的完整连续序列；旧封存批次不能拼接 |
| P50≤15min、P95≤30min | 没有合格实测分布；已有约38分钟单例不能证明通过 |
| 至少两类真实事件完整流程 | 局部规则/parser/reader与受控流程不等于真实事前预案→actual→情景→更新，尚未验收 |
| 条件提醒与合格后继 | 真实条件、实际送达、正确授权及后继独审全链未验收 |
| PAPER≥10笔，含保护、退出、结算、重启 | 本次交付验收可计数0；非零funding正常落账仍缺接缝，不可未知当0 |
| 四周预测评分、分层与周报 | WORK14有1份；旧实例另有结果，不构成已验收四周序列或改善证明 |
| 30个真实失败完整回放 | 已验收完整业务回放0/30；transport/边界测试和数十纯测试不能计入 |
| 全交付范围ruff/strict零错 | 新模块局部结果不覆盖整个实际运行范围；旧类型债仍在 |
| 自包含报告、手册与独立交接 | 阅读导出单例已证；完整流程实测手册、正式升级/回滚/灾备交接仍未验收 |

未完成不是单纯“再等14天”或“测评卡住”。普通市场稳定运行、事件全链、通知后继、PAPER结算与完整回放仍有功能及集成缺口。

## 7. 我在执行中犯的错误

1. **路线纠偏不够早。** 先产生了v3骨架与新PC页，随后才按用户明确要求回到原界面/原系统。两套资产与实际交付路径分叉，增加了接线、验证和解释成本。接手者应保留可复用资产，不能继续以任务名称强制换前端。
2. **把局部证明置于可见交付之前。** 包装脚本、scope、复核、历史归档和完整构建反复成为主线，而用户没有获得持续在线的最新预测入口。上一成功normal实际4771.253秒；本次取消前3033.191秒且UI/Host尚未开始，说明维护链本身已成为明显成本。没有完整逐次耗时归因，不能虚报其中各项占比。
3. **私有验证工具屡次制造额外失败。** 包括导入装配、Windows命令行、loopback、`.venv`链接的路径判断等。最近49例的第一套harness在pytest前被合法链接拒绝；仅私有路径规范化后才实际49通过。工具失败不应混作业务失败，也不应无止境阻塞独立的产品流程。
4. **擅自强化过单次研究的验收口径。** 把未校准、financial/execution=false以及授时/0.25秒操作政策过度当作统一前置。原诊断§7明确效果评价“不作为单次阻断”。已在AGENTS§80纠偏；冻结信息集、真实原件、独审、资金边界仍必须保留，不能反向全部撤掉。
5. **并行支线没有及时收敛。** 在主流程未稳定交付时继续投入NFP格式、observer和非零funding等工作。它们属于完整目标的部分，但不应成为恢复页面和普通方向研究的新增依赖。用户追问后才停止扩展，优先级管理有误。
6. **进度表达和状态管理欠清楚。** 多次强调局部GREEN/“正在构建”，没有持续突出“最新源码尚未形成有效构建、服务已停、最新预测尚未生成”。Main STATUS、formal STATUS、旧总控和长AGENTS历史并存，接手成本高；不能让下一位重查所有旧PID和旧失败。
7. **源码与Git交付没有整体收口。** Main保存大量未提交/未跟踪内容，D增量目录又不是完整当前Main克隆。精确微切片提交有价值，但不能据此声称完整项目已可只从某个HEAD重建。
8. **自然时间验证与真实业务覆盖没有形成稳定产出。** 已有个别成功，仍未形成六组合、真实事件、PAPER和评分的持续流程。不能把剩余工作都解释成客观等待，也不能把50条局部测试当作50个任务完成。

这些是执行与交付管理责任，不能用“文档严格”或用户没有逐项确认来推卸。这里记录有原件支持的事实和判断，没有宣称分析过全部旧会话的逐条token、账单或全部失败栈。

## 8. 当前技术状态与保全位置

### 工作区与Git

| 位置 | 用途与注意 |
|---|---|
| `E:/project/study/codex/dsh/decision-hub` | 实际Main源码，分支codex/decision-product-core-20261018；HEAD `1f5a31da0da012c628737e5fa3bf8a3d04b344f1`不是完整当前文件状态 |
| `D:/DecisionHub-legacy-incremental` | 增量修复工作区，分支repair/incremental-delivery；后端基点不等于完整当前Main，不能整目录覆盖Main |
| `E:/project/study/codex/dsh/.worktrees/decision-hub-v3` | v3技术资产与formal STATUS，分支v3/T-303-six-combinations；不是当前原产品交付版 |
| `D:/DH-eth4h-live14` | 原WORK、owner、账户、设置、报告、SDK会话；不可重新初始化或删除 |

写本次交接前只读Git快照：Main porcelain条目1861（含1610未跟踪条目，含目录口径），D增量7个未跟踪条目，formal clean；三处暂存区均空。**这些不是本次新增文件计数，也不能全部归因本会话。** 详见 [Git快照](D:/DH-pause-handoff-20261027/git-before-handoff-docs.json)。本次交接文档写入后状态会另有文档差异。

D最近文档提交 `5ddd4cebb661e65e4e948de1c7c6c04ca30b6739`，此前17源镜像提交 `343f9f88f6a624fa1f929a432a45b4b36864e25e`；formal最近文档提交 `1411802feb1c2269c2d2ade45387f16f828753b1`。镜像提交不是独立完整产品测试。Main未进行本轮Git提交／push；正式installed指针未切换。

### 构建、原件和恢复候选

- **最后成功normal**：[event-paper-integration-build回执](E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261027/event-paper-integration-build/product-validated-builds.json)，SHA `1f1d2d3c1682ba6127115d8a955d9dda1f69da623e1c4997adfb1869c5023852`；native `f0025cc8a71d08ff8c460d5f8c563d17d42f9aea136509c9f6b3ad0bbf34a6d4`。只对应当时源码，不覆盖后来17源。
- **已取消normal**：Main `artifacts/incremental-delivery-20261027/event-three-family-integration-build/`。原日志及terminal在 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/event-three-family-normal-build01/`。没有有效product-validated-builds.json或final-receipt，不能直接绑定恢复。
- 取消后有限核对：161控制、72 native输入、17采用源码、672编译输出及57份关闭源/副本的114个pin均匹配，mismatch为0。没有执行完整源树扫描或新的fullguard；不能外推全部历史资源都已复核。
- 17源旧前像：Main `artifacts/incremental-delivery-20261027/event-three-family-integration-originals/`；当前17源保留，未因暂停回滚或删除12新增源。
- 关闭字节备份：`D:/DH-work14-priority-preimages02/`，57份包括DB4、两旧profile共40、raw session6、registry/projection7。凭据仍在原处，仅pin，没有复制进交接。这个范围不是全WORK灾备全集。
- 恢复前件：[transition02](D:/DH-work14-priority-transition02/transition.json)，SHA `69cdb54db8d65900e35407d3f2fb9d7906d3b4a0fe0d2ada93a717a978327a75`。旧r3已自然停止、7个owned Job关闭且errors为空。
- 已审七角色候选：`D:/DH-work14-priority-controller-r2/candidate/`。本次没有创建binding-final.json、preview-priority01 ready/ownership；**不能执行默认UNBOUND文件或旧r3启动脚本**。

### 暂存支线，当前均不执行

| 位置 | 实际边界 |
|---|---|
| `D:/DH-nfp-reference-period-format01/` | 新单parser候选，真实网页格式转录3 RED→44 PASS；未Main采用，native原件/历史PIT未因此证明 |
| `D:/DH-clock-observer01/` | 22纯测试、默认NO_ACTION；没有启动真实NTP观察循环 |
| `D:/DH-clock-persistence-ready01/` | 管理员Apply仅准备；本轮未取得实际Apply证据，也不再要求用户执行 |
| `D:/DH-paper-live-setup-diagnosis01/` | 原PAPER配置及非零funding缺口诊断；不是已启用/已成交证明 |

非零funding后续仅计划/测试草稿；私有测试中断exit1，没有完成RED/GREEN结论或新增产品实现。不要把早期计划文字当成该功能已落地。NFP/FOMC/PCE局部reader也不能冒完整事件生产、通知及后继链。

授时历史也须分开：用户此前管理员resync在09:21已由事件及毫秒级偏差实证；11:37再次只读采样约0.58—0.61秒。前者不能被抹掉，后者不能冒作当前采样或原需求规定的单次研究否决线。本轮没有再校时。Codex的fast配置偏好此前可见，但没有核实实际在途请求tier或据此证明主产品研究提速。

## 9. 下一位恢复工作时的最短顺序（当前不执行）

1. 先确认用户已经恢复执行。读取本文件、AGENTS最新暂停说明和相关源证据，核对实际进程/端口与Git状态；不根据旧RUNNING文字重启旧进程。
2. 决定如何让当前17源形成一致可运行版本。本次normal是用户中止，目录已使用；如继续当前源码，需要新的明确输出和构建前件，不能复用取消目录或伪造成功回执。也不要未审阅就回滚17源来追求页面上线。
3. 得到真实有效normal/postflight/完整graph后，复用已审r4-r2和原WORK恢复；确认真实心跳与同owner历史报告。优先研究通道是否达到3/3必须实证。
4. 从原UI读当前设置，若已有活动研究先回读原请求。没有活动研究时，先做一份最新ETH4h普通研究，取得新分钟原件、独审和同Run报告；保留全部失败。观望也要有具体理由及转操作条件，不强改成open。
5. 再覆盖BTC/ETH三个期限，并建立真实连续记录。普通研究不以NTP、未校准或未启PAPER作为额外前置；具体未来信息、原件缺失、费用比较缺失或审核拒绝仍如实失败。
6. 在主流程稳定可见后，回到完整目标中的真实事件、通知后继、PAPER退出结算、四周评分、30完整回放及质量/正式交接；不能把它们删成“未来增强”。

不要再次新增一套总控、前端、全API翻译层或大量同功能验证包装。复用已通过的直接证据；每次改动只做有必要的验证。不能缩短研究深度、删反证、改超时/门槛或编造校准/成交来获得绿色。

## 10. 本次交接核查边界

本次完整核对了原00总控、05实施计划、10月23日交接、v3主清单、formal/Main STATUS及增量覆盖账本；读取具名构建与数据/报告证据，有限核对指定文件字节和原生进程/端口。没有重新执行所有历史测试，没有读全量旧聊天记录，没有重新查询正式业务数据库，没有验证独立灾备恢复，也没有精确费用归因。

本文件保留未完成项和不确定性，不把暂停当交付。原目标保持paused，后续动作须等用户恢复。
