# PC 使用指南

本指南覆盖本机 Chrome 的 BTC、ETH 研究与行动查看。**核心新流程尚未交付；请以实际保存的报告、方案和回执分别判断完成情况。** 手机、真实下单及黄金/纳指/恒生科技的完整分析不在本轮交付范围。

## 本轮状态：normal26 BTC 已超时失败，尚不可交付

normal25 这次 BTC 研究取得了真实模型和工具执行记录，但未形成可提交的研究结果或新报告，**新增报告为0**。模型步骤 watchdog 在异步工具等待期间结束本次执行，父任务仍显示运行；随后验收时经 Chrome 点击停止（2026-10-16 06:44:08 UTC），父任务以“用户停止”保存。阶段错误和最终用户停止各自保留，不能把其中一个覆盖另一个。ETH 只切换查看，**没有发起本轮新研究**。

normal25 已官方安全停服；统一19份业务文件修复已应用。normal26 已完成构建和启动，08:16:27 UTC通过启动就绪门，本机 Chrome 仅一次新BTC提交后，原请求、执行页、取证与资产切换恢复已有实际记录。**本次BTC已于2026-10-16 09:20:53.019394 UTC自动保存超时失败，新增报告为0，独立复核未通过，ETH没有提交，产品尚不可交付。工程门和局部交互通过不能代替核心分析与报告交付。** 详见[真实失败页截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/chrome-btc-real-failure.png)、[失败基础状态DOM](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/btc-failure-basic-settled-dom.txt)。[PC验收记录](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/acceptance-progress.json)已更新本次失败终态；原中间DOM及逐次观测另存，不改写历史。

normal26 已查看9条材料、4个来源标识，并打开币安捕获响应/匹配持仓量原始JSON、关闭详情、展开及收起技术身份，再经ETH查看返回BTC和刷新恢复同一请求。这些是资料与交互证据。基线宏观跨资产取证仍有一项超时；技能 `list` 只是列目录，`load` 只是返回方法指令，**不能计为分析、方法节点执行、独立审核或报告完成**。09:19:19 UTC的实际阶段中，spot_evidence、features、hypothesis三个节点均为0；第4次回调仍是list，09:19:24完成也不表示分析已发生。此前[功能阶段核查](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/functional-stage-review.md)按其原快照时间保留。

本次父任务错误保存为 `provider_timeout`，不是权限阻塞判定；**错误名不能直接诊断为纯Provider慢**。前四个真实dispatch均finish=completed/errornull；第五次仅binding，进入时到原TaskD只剩约29秒，binding回执后剩约2.38秒，尚无第五input/finish原件。watchdog受原Task总期限clamp，不能称第五模型推理达到600秒。详见[终态核查](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/normal26-terminal-review.md)。原Task截止为09:20:46.724141 UTC，native于09:20:46.728显示idle/`dsh_model_step_timeout`，但 `terminal_at` 仍为空，不能宣称物理执行已获终态释放认证。父任务09:20:53自动失败和物理释放分别判断；原observer34739在09:21:15的第126次观测读到失败后正常退出。Chrome失败基础页与完整记录均已实际读成功；完整说明为“原研究执行时限已到”，保存了原任务标题、9条材料和5次工具记录。[完整原因截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/chrome-btc-deadline-explained.png)和[完整DOM](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/btc-failure-full-read-settled-dom.txt)可核对。

## PC 最短操作路径

1. 正常服务就绪后，用本机 Chrome 打开[今日研究](http://127.0.0.1:8047/#/home)，选择 BTC 或 ETH，先看行情时点和资料缺口。
2. 点一次“更新当前资产研究”，在“我本次的研究”查看受理、等待或执行状态。“下次自动研究计划”单独查看，不替代本次操作；等待期间无需重复提交。
3. 点“查看研究执行记录”查看同一任务。基础进度会定时读取，也可点“刷新进度”；可以离开、刷新或切到另一资产后再返回。任务更新时间、进程在线和产生新结论分别判断。
4. 需要材料时点“展开后台工作与材料记录”。这是按需读取的保存快照；后续变化点“重新读取后台工作与材料记录”。“未读取”不是零材料，快照中的工具数也不是当前在途调用数。
5. 点“查看已收集的证据”核对来源，再打开材料详情和捕获响应；看完关闭详情并返回首页。研究中的证据页不等于已经发布完整报告。
6. 如需停止，点“停止本次研究”并等待确认。停止/失败后从“查看原因与恢复”核对基础终态，再按需“读取完整任务与停止依据”。“重新尝试研究”会创建关联的新任务，不是刷新按钮；normal25 未点击该重试入口。
7. **实际出现新报告和行动方案后**，才继续阅读完整报告、核对资产/期限/持仓情景和行动条件；未保存报告、尚缺依据或未形成方案时，不把研究受理或材料列表当作可操作结论。

## 本轮按钮与交付缺口

“25已实测”保留历史证据；第三栏记录normal26已实际操作的范围和仍缺项，不把每个可见按钮都写成已点击。PC交互保留08:56:47 UTC及此前原证；当前终态更新至09:21:15 UTC原observer126，旧运行快照不覆盖失败。

| 操作或功能 | normal25 历史结果（保留） | normal26 实测与剩余项 |
|---|---|---|
| 更新当前资产研究（BTC） | 仅点一次，形成原 owner-command、request、Run；[原点击](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-actual-click.json) | 已一次真实BTC提交，原件期限已核对；[新回执](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/btc-initial-request.json)、[实际预算](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/actual-frozen-budget.json) |
| 查看执行记录、刷新进度 | 原 Run 可读；手动刷新保留基础状态，[任务身份](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-original-identity-dom.txt)、[刷新结果](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-manual-refresh-basic-dom.txt) | 新原Run执行页与基础进度可读，现已显示真实失败；无新报告，整条仍未通过 |
| 展开后台工作与材料记录 | 一次完整读取有4条材料/2次工具的保存快照；[原DOM](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-full-work-record-first-read-dom.txt) | 具体中文缺口、运行态说明已复验；[实际截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/chrome-btc-readable-work-gaps.png)。完整记录仍是按需快照 |
| 查看证据→材料详情→捕获响应 | 同一新 Run 的证据页、3条关联捕获、Binance原始响应可读；[原文截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/chrome-binance-original-response.jpg) | 9条材料/4来源标识、币安原始JSON、技术身份折叠/展开/收起已实测；[原始响应截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/chrome-binance-original-response.png)。不代表宏观材料齐全或报告完成 |
| 返回首页、切ETH再回BTC、浏览器刷新 | 恢复同一BTC原请求；ETH未提交；[刷新后原回执](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-reload-original-command-restored-dom.txt)、[切换记录](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/eth-view-switch-no-submit-dom.txt) | 证据返回、ETH只查看后回BTC及刷新恢复原Run已通过；[刷新后回执](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/btc-reload-original-command-restored-dom.txt)。报告/行动发布后的往返仍待 |
| 停止本次研究 | 单次停止已确认，父任务取消，报告未保存；[回执截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/chrome-btc-owner-stop-receipt.jpg) | 新26未靠验收停止收尾，父任务09:20:53自动保存失败；native终态认证/物理释放仍未证 |
| 查看原因与恢复、读取完整依据 | 基础终态、任务标题和完整记录可读；[停止后完整DOM](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/btc-stopped-full-cause-settled-dom.txt)曾暴露英文阶段码 | 新26恢复旧停止任务并分开中文显示原因；新BTC基础及完整失败记录已读，明确原Task期限已到、保留标题/9材料/5工具；[旧任务截图](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal26/chrome-restored-stop-chinese-cause.png) |
| 重新尝试研究 | 仅可见，**未点击**；未产生关联新任务 | 只在真实需要且服务端允许时验证，不为补截图重复提交 |
| 新报告、行动条件、方案知悉/暂停/恢复、模拟验证 | 此次BTC无新报告/方案，**未沿新结果到达这些按钮**；旧历史证据单列于后文 | **新增BTC报告0，独立复核未通过**；list/load不算分析。完整报告、行动及其按钮/回执仍待实际产物 |
| ETH 新研究与报告 | **未提交、未验收**；资产查看切换不算研究成功 | 单次合法ETH请求、真实报告和相应行动主线 |
| CPI预案、事件提醒与回看 | 可复用既有版本的九格/固定链接和normal21自然失效通知确认记录；25页面可见不等于重新点击验收 | 公布后真实触发/结果回看仍缺PC证据；8074拒绝记录保留，不绕过 |
| 安全重启恢复 | normal25曾恢复旧停止回执；本轮已真实安全停服，[停服回执](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-post25-fix/maintenance-current-cancelled01/maintenance-stop-result.json) | 新26启动门与旧停止请求/操作回执恢复已有记录；新报告/行动发布后的安全恢复仍待。事件历史恢复证据不扩写成本次全通过 |

### 已发现缺陷与本次修复状态

normal25 的84.764秒准备写事务与心跳停更重合、旧步骤计时器中止异步工具、失败未自动收尾等问题均保留原证。normal26 已实际记录同一准备操作BEGIN至COMMIT为3.380秒，完整RO原件认证位于BEGIN前；[实际阶段证据](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/prepare-stage-actual-01.json)。这只是本次准备阶段观察，不能推断全部心跳/CPU原因。中文缺口、来源展示、技术折叠和旧停止原因分离已有PC复验；**本次父任务已自动失败，但模型/技能分析/独立复核到报告的完整主线仍未通过**，不把工程门或准备性工具调用计成交付或金融效果。

还保留两项界面/能力缺口：首页多版本CPI失效提醒堆叠、小时风险能力服务尚未配置；本片未整改，不以研究进程3/3证明小时预测能力可用。另有一项 `market.cross_asset` 取证超时的实际缺口，[有限诊断](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal26-research/baseline-single-failure-review.md)没有把持久化间隔冒充网络耗时。

保留[normal25完整验收记录](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/acceptance-progress.json)、[watchdog原始阶段证据](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-normal25-research/model-step-watchdog-actual-01.json)及[修复应用收据](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/M5-post25-fix/applied.json)。`acceptance-progress.json`里的“候选未应用”是当时记录，当前以本段应用收据为准；旧失败不改写。

<details>
<summary>本轮原任务身份（审计核对）</summary>

- command：`05196bb1-6d1d-401d-a97f-fd9acf0521bf`
- request：`riskresearch_d20585410de5197917cd4e6c957e49883b7627fe`
- Run：`run_d87c961253484d63a67015887c85f308`
- normal26 command：`9bef7e8b-d499-4b28-81c6-052b1ca3b64c`；request：`riskresearch_f8996a3d0df9487a00d1b0086a6b428ec3d53b4a`；Run：`run_3a9eb4be73754f508e999ec0ce116ff6`。
- 旧25截图：[新原任务](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/chrome-btc-new-original-run.jpg)、[运行中材料](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/chrome-btc-live-materials-returned.jpg)、[watchdog后父任务仍运行](E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-v1/core-main-flow-20261015/pc-normal25/chrome-btc-timeout-parent-still-running.jpg)。它们分别是原受理、材料和失败证据，不是报告完成证明。

</details>

以下保留完整功能参考与历史验收。历史功能说明、旧启动成功或旧截图不能替代上述当前交付缺口。

<details>
<summary>历史状态原文：normal21及更早（不表示当前状态）</summary>

本指南覆盖 BTC、ETH 的研究、行动方案和模拟验证。**核心新流程正在验收，整个产品尚未交付。** 当前运行状态及未完成项见[当前进展](CURRENT_FOCUS.md)；服务在线、报告保存、方案发布和效果验证分别判断。

按用户明确指定，PC 验收使用本机 Chrome，不重复询问浏览器授权偏好。本轮已用 Chrome 真实打开正常版8047，并关闭旧内置浏览器页，仅保留 Chrome 验收；这不代表8074的安全拦截已经解除。

**当前为 normal21 验收后停服状态：2026-10-16 00:08:16 UTC（北京时间08:08）在全局空闲后官方安全停止，真实研究主链仍未通过，产品尚未交付。** 本轮自动 BTC、ETH 请求各尝试3次后均失败，未创建 Run、DSH 会话或报告；BTC 原因为 `study_host_activity_refresh_required`，ETH 终态为 `risk_research_claim_lease_expired`。见[真实自动请求终态](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal21-research/automatic-observation-005.json)、[空闲原件](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal21-research/preparation-20261016T000656.json)及[官方停服回执](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal21-research/stop.json)。

**本轮已通过一条正常8047自然通知的 PC 展示、单次确认和刷新保留。** 本机 Chrome 显示 CPI 2026-10 的旧预案失效通知；指定 ETH 多仓通知于北京时间08:02确认，刷新后仍显示同一通知的原确认时间。完整通知内容、ID、创建时间、outbox 和 display 回执未变，owner=`owner` 仅一条 ACK。见[点击前](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal21-research/natural-notice-before.json)、[点击后落库核对](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal21-research/natural-notice-after.json)及下方截图。该提醒由新版本替代旧预案产生，CPI 计划11月10日公布，**不代表公布后条件触发、实际结果激活或研究报告成功**；8074独立工程页仍保留原安全拒绝记录。

[normal21 正常8047截图：指定ETH多仓旧预案通知，刷新后保留08:02确认时间](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal21/chrome-natural-notice-confirmed-after-refresh.jpg)

**历史失败保留：normal20 ETH 于2026-10-15 17:26:45 UTC真实失败，错误为 `research_method_binding_failed`，没有已确认报告；17:45:47 UTC正式安全停服。** 更早的 Host native 记录于17:26:35出现 `baseline_transport_unavailable`，先于本地 `original_preparation_readset_changed`；本地停止依据不能称为整轮唯一首因。17:27:22的 `research_run_terminal` 是后续拒绝，分别保留审计。本版曾于17:00:02.868 UTC构建成功、17:09:51.894 UTC通过启动门，DSH于17:20:28.186 UTC受理；这些和[当时运行截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal20/chrome-ETH-live-progress.jpg)均不代表报告完成。见[构建记录](../../../artifacts/decision-product-v1/core-main-flow-20261015/release20/build-end.json)、[启动门](../../../artifacts/decision-product-v1/core-main-flow-20261015/normal20-http/accept-20261015T170138955082.json)及[本地停止依据审查](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal20-research/failure-message-review.md)。

normal20 Chrome 于17:12:55.161 UTC仅点击一次 ETH空仓24小时研究，命令 `103e9b94-7223-4c0c-8e52-4cda04dfefb3`，对应请求 `riskresearch_6e5fb2113f4ba7ee87d4a35676333779473e6d02` 与 `run_66c00399d223417cb1cf50a6c7bfc3e4`；[原操作回执](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal20-research/initial-request.json)保留身份。执行准备沿同一 Future 在91.688秒完成提交，[准备操作记录](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal20-research/preparation-lifecycle-owner-log.json)可核对；长写事务风险仍保留。新20打开旧19失败 Run 的[基础进度刷新为342毫秒](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal20/old-ETH-basic-progress-refresh.json)，只证明这次旧任务读取，不能当作当前完整报告或全部网络门通过。当时8074仍被工具策略拒绝、display/ack未验收；后续normal21正常8047的自然通知通路结果单列于上方。

**历史失败保留：normal19 原 ETH 于16:22:43 UTC failed，原因 `context_selection_deadline_elapsed`，DSH link为0。** 原请求 `riskresearch_7b02984e6c808dd8eb743a4c4695cf572b64d7ec` 与 `run_dab7b00ea26540a4a34ef41cf8006569` 不改写；16:25:55 UTC确认空闲后，于16:26:15 UTC正式安全停止，再留存原字节实施修复。[旧Chrome提交截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal19/chrome-ETH-receipt.jpg)只证明当时回执，旧进度加载截图不能标为报告通过。

</details>

## 0. 核心事件：先看自动生成的事前预判

本节保留 normal18retry01 的事件使用步骤与历史实测。**旧版 Chrome 的 ETH多仓当前规则与单份概率表已有通过记录；normal21 正常8047另已通过一条自然失效通知的显示、知悉和刷新恢复。真实研究、公布后触发的PC展示以及新回执安全重启后的恢复尚未通过。** 正常服务就绪后进入[市场与事件](http://127.0.0.1:8047/#/market)，查看“核心事件事前预判”；无需先手写研究题目或创建研究任务。normal17 已保存9项事件读取门以及九种情景、ETH/多仓切换、历史规则折叠、覆盖与缺资料事件、固定链接及两次刷新的实测证据，均保持单份显示。固定链接重载最初出现定位等待超时，随后内容自然可见；保留该首次等待问题，不将其写成首次即成功，也不把这组历史交互算成本次全部重新操作。详见本指南末尾的历史验收记录。

1. 选择未来的 CPI 事件，核对计划公布时间、参考月份和估计时间。九格比较的是上月前值，暂时不是市场调查共识；点击一格查看对应机会。
2. 切换 BTC 或 ETH，先看“已使用的宏观背景”：名义利率、实际利率、广义美元指数、观察日期和入库确认时间。入库确认时间表示系统何时可以使用该值，不等于数据对外发布时刻；广义美元指数也不是 DXY。
3. 阅读根因链与反向机制，区分事件结果概率、资产反应估计和扣费后策略估计。它们的目标和样本不同，不能互相代替。
4. 根据自身情景选择“当前空仓”或“已持多仓”，查看具体确认条件、减险/退出、失效条件及有效窗口。新版各情景规则有实际差异；旧版未保存宏观背景时会明确显示缺失。
5. “刷新预判”只重新读取已保存结果。原件无法核对、需要更新或尚缺输入时，保留页面的具体状态；反复刷新不会补齐数据或启动模型。使用“该事件的固定回看链接”返回同一事件。

主区“当前自动确认与入场条件／当前持仓减险条件”采用公告价格锚和公告前20分钟量基准。需要核对旧快照时展开“保存的原版规则（历史对照）”；其中的滚动6小时收益、UTC小时量及原版盈利估计不代表当前自动确认规则。

本轮已接通官方实际结果、事件监控、站内提醒与结果回看的源码，并完成独立工程演练。正常产品已读到两个未来事件的等待状态和六个待到期提醒窗口；normal21 也真实呈现并确认了旧预案被替代的通知，但这些均不证明官方结果已激活。8074 独立工程页的 origin deny 拒绝保持不变；未换地址或代理绕过，其公布后触发夹具的 PC 展示仍未验收。FOMC 的利率/指引概率尚未接入。本轮进展见[主流程计划](executable-product-v1/CORE_MAIN_FLOW_EXECUTION_PLAN_2026-10-15.md)。

### 公布与后续跟踪

1. 查看“公布前覆盖与数据状态”：分别记录公布前24小时、2小时、30分钟应有的预案和提醒。输入未齐会显示缺口，晚生成不会补记成按时交付。
2. 官方数据公布后，系统核对参考月份、指标、原件与实际获知时间，再对应唯一情景。页面显示总体/核心实际值和激活时间；公布前的概率版本仍保留。
3. “事件条件跟踪”分别显示 BTC、ETH 的空仓和已有多仓状态。展开“查看确认条件”核对阈值、公告后变动与量能。价格以公告前最后完整分钟收盘为锚；量能基准固定为公告前20分钟的分钟成交量中位数。
4. 已有多仓在短暂上涨时仍继续等待减险条件，在预案有效窗口内随后下跌仍可触发提醒。空仓与多仓分别判断，缺数据不会当成条件已满足。
5. 提醒进入可见区域后记录显示回执；点击“我已了解这条提醒”记录知悉。回执失败时点击“重试同一回执”，不会新建一条提醒。刷新后核对原通知创建时间与“已确认”时间；新版本可能生成同标题的另一条提醒，不能混作刚才确认的版本。这些回执均不代表账户成交。
6. “结果回看”区分事件概率评分、24小时价格变化和规则纸面结果。窗口未结束、缺连续行情、缺公布前预案分别显示。可展开具体缺口和计算假设；纸面减险结果按一单位持仓相对持续持有计算。

新公告锚定规则尚无校准后的策略胜率，不能沿用旧 V2 规则的历史概率。实际值获知较晚会影响可操作时点；系统不把晚到的数据倒填成公布当时的提示。独立演练中的触发和回放不能作为真实前瞻命中的证明。

## 1. 从资产研究开始

打开[今日研究](http://127.0.0.1:8047/#/home)，选择 BTC 或 ETH，核对行情时点和数据缺口。点击“更新当前资产研究”使用当前资产的研究模板，无需自己编写完整题目。提交一次后沿同一请求看进度；页面等待、失败或取消都不能视为研究完成。

查看已有研究时，保留它的资产、分析时间和期限。旧报告可以复盘，到期后不会因重新打开而成为当前建议。

### 跟踪“我本次的研究”

以下回执功能的源码和有限工程控制已通过；完整真实研究回执与报告链仍待通过。normal21 的自动请求未创建 Run，不能以自然通知确认验收代替研究主链成功。

1. 点击一次“更新当前资产研究”后，主卡“我本次的研究”持续显示这次操作关联的请求与执行进度。提交已保存、等待核验、进入执行和报告可用分别判断。
2. 出现“下次自动研究计划”时，单独查看它的状态与最早检查时间。它是另一项自动请求，不会取代上方这次手动请求的当前或失败状态；计划时间不保证研究已开始。
3. 本次执行失败时，点击主卡“查看研究执行记录”打开原 Run，核对失败原因与允许的恢复操作。较新自动请求仍在等待，不表示本次失败已恢复，也不表示已经有报告。
4. 刷新页面只通过 GET 重新核对原操作回执，不重新提交研究。浏览器保存的跟踪线索仍须服务端核验；读取失败时保留原操作身份，使用“重新读取研究状态”，不会静默换成最新自动队列。
5. BTC、ETH 分别恢复各自的本次操作。切到 ETH 再返回 BTC，应继续看到原 BTC 请求；没有本次操作线索的资产，才显示该资产最新研究请求。清除浏览器存储后，不保证恢复此前本次操作卡。
6. 提交结果尚未确认、且服务端明确未查到原操作（HTTP404）时，页面才提供“重试保存本次请求”。明确点击后沿原 command 和幂等标识重试保存，不新建另一项操作。普通断网、权限错误或其他读取异常不开放这个入口；刷新不会自动重发 POST。

工程证据见[本次研究回执实施状态](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-unified-fix/receipt/followup-status.md)：正式 router 私库回归1通过、React主线与断网恢复2通过，类型检查与Ruff通过。这些结果不证明真实模型已经产出报告。

## 2. 阅读当前行动方案

本节对应当前代码中的行动入口；新研究的完整真实路径仍在验收。出现方案后：

1. 选择 30 分钟、24 小时或 72 小时窗口，再选“我目前空仓”或“我已持有多仓”。不同窗口、仓位分支分别阅读。已实测当前浏览器刷新保留所选窗口，BTC与ETH各自保存；切换资产不会混用时间窗口。
2. 先看建议、理由、有效期，再看“开始行动的条件”“退出条件”“判断失效条件”。“条件未满足”和“尚无法判断”含义不同，缺数据不能当作条件已满足。
3. 在“为什么这样判断 · 主反依据”核对传导关系、支持、反方和仍需核对项。概率尚未校准时，页面不能据此宣称某个胜率。
4. “查看版本与监测记录”可查看上一版、返回当前版本和读取条件检查历史。“阅读原研究报告”进入完整依据。
5. “我已了解此方案”记录知悉；“暂停此方案监控”和“恢复此方案监控”影响这份共享方案的条件检查。页面显示和知悉回执均不代表成交。

若最新研究没有形成方案，先查看具体缺口和研究状态。过期、已被替代或读取失败的版本不能继续当作当前有效方案。

## 3. 查看方法是否真的被采用

“本次采用的方法”分别显示选择、读取、执行和采用四个阶段。仅“选择已核实”表示选中了方法；后续各阶段须有对应回执。“当前行动方案没有匹配的已核验采用引用”不能解释成当前方案已经用了该方法。

“查看方法版本与原始依据”可展开版本与引用。现有预置方法的金融效果仍未验证。真实结果自动产生新技能、后续研究消费及有无技能的效果对照，仍属于待完成能力。

## 4. 用后续行情验证行动

在“用后续行情验证行动”中选择“空仓开始”或“已有持仓情景”，再点“开始模拟跟踪”。这只登记模拟验证，不连接交易账户。当前固定情景本金为 10,000 USDT，执行延迟 60 秒，每侧费用假设 0.1%、滑点假设 0.05%。

- 登记回执显示实际起止时间；只跟踪登记范围内后续机会。资产、窗口、方法及起始情景各自保留本金和结果。
- 同一方法和情景已有活动轮次时，查看原轮次。刷新页面不能另算一份本金。
- “查看跟踪结果”读取现金、模拟持仓、执行结果及机会记录。“查看全部执行记录”“查看全部机会记录”用于核对成交、未成交、未知和未形成方案的情况。
- “本轮无法开始，结果未知”表示未建立模拟持仓，不计算收益。系统会重新核对登记条件；恢复后可以登记新的未来轮次，旧失败记录保留。
- 已有模拟持仓仍未退出时，继续看持仓与保护状态。登记窗口结束不等于持仓已平仓。

若提交结果待确认，使用“恢复模拟登记回执”；只有确认原回执不存在时，才通过“重试原模拟登记”复用原请求。不要用不断新建轮次处理未知提交。

已实际验证：空仓轮次登记、刷新恢复同一回执和自动开户。旧报价时钟异常轮次仍保留未知；修复后的已有持仓轮次已按未来时点真实报价开户，05刷新可恢复原登记和持仓。开户不算系统买入成交，目前尚无正式Action执行结果。收益优势和长期胜率尚无结论。

## 5. 原文、报告与后续复查

来源原文保留原捕获记录。正文预览可能截取显示，完整原文入口用于核对全部材料；展开来源、追加下一批和收起不会替换原研究的冻结依据。外部文章进入资料库或模型输入，仍须独立核验后才能支持具体行动。

报告显示“已读取确认保存的报告”后，可下载完整 JSON 或 Markdown 阅读版。摘要生成与完整报告保存确认分别处理。“本报告的后续复查”查看真实计划、子任务和失败；刷新只读取当前记录。

原 30 分钟与 24 小时复查的全文下载、刷新及父子报告往返已有实测证据，见[历史验收](../../../artifacts/core-goal-20261011/pc83_01/pc-recovery-acceptance01.json)。这些历史报告的窗口已经结束，不作为本次新行动流程通过的依据。

## 6. 失败与恢复

| 页面状态 | 下一步 |
|---|---|
| 研究正在进行 | 查看原请求进度；需要停止时点“停止本次研究”并等待回执 |
| 研究已停止或失败 | 阅读原因与已取得证据；重试是新的关联任务 |
| 本次执行失败，另有自动请求等待 | 从“我本次的研究”打开原执行记录；“下次自动研究计划”单独跟踪 |
| 研究提交结果尚未确认 | 先重新读取原回执；仅页面确认原操作不存在并开放入口时，点“重试保存本次请求”沿原标识保存 |
| 完整报告未确认 | 使用页面的恢复条件检查及原请求回执入口；超时不等于未受理 |
| 方法原始记录暂时无法核对 | 点“重新读取方法记录”；不能沿用隐藏前的采用状态 |
| 模拟登记结果待确认 | 先恢复原回执，再按页面允许的原请求重试 |
| 起始报价缺失 | 保留该轮未知结果；新轮次使用新的未来开始时间 |

## 7. 当前范围和证据

当前优先完成 BTC/ETH 的事前行动、条件监控、结果结算和学习闭环。黄金、纳指、恒生科技的完整分析尚未接通；手机和真实下单不在本次范围。

Kronos 已完成本地独立候选试跑与简单基线对照，但存在无效价格柱和事件日尾部漏判，未接入正式动作。其离线试跑不能作为历史上已发布预警的证明，见[试跑结果](../../../artifacts/decision-product-v1/core-main-flow-20261015/K1/STATUS.md)。

### 研究回执的精简 PC 验收清单（剩余主线）

下一正常修复版构建、服务与 Worker 就绪后，仅用本机 Chrome 补齐以下主线；每步记录当前 request/Run 身份与截图，不能用旧18的截图或normal21自然通知确认代替研究验收。

1. **一次提交与身份：** 选 BTC，只点一次更新；记录原 command、服务端 request A、Run A（若已创建）。主卡显示“我本次的研究”，不把提交回执写成完成。
2. **原请求的真实结果：** 等待当前或终态，核对主卡与原 request/Run 一致；有报告再核对已保存报告。若真实失败，保留原因与原 Run 入口，不为验收伪造失败或成功。
3. **刷新与跨资产恢复：** 刷新，再切 ETH 后返回 BTC；核对仍是 A、入口仍指向 Run A，浏览器读取过程没有新增 POST。ETH已有自己的回执时分别恢复。
4. **自动计划共存：** 若后台自然产生另一自动请求 B，核对 B只出现在自动计划次区，A的执行或失败及入口仍保留。没有自然出现B时记“正常环境待观察”，沿用明确标注的工程A/B控制证据，不改正式库造队列。
5. **不确定保存的恢复：** 仅实际出现未确认提交且GET404时，核对显式重试沿原command/Idempotency-Key；刷新无自动POST。正常环境没有该分支时记“工程控制通过，正常场景未触发”，不额外制造故障或重复研究。

旧版 [BTC终态截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/chrome-BTC-terminal.jpg) 实际展示的是新自动队列B，原A失败与入口被遮住；它属于缺陷证据，**不是本次回执修复已通过的截图**。

### normal18retry01 与 normal17 历史验收（2026-10-15）

| 部分 | 已核实的范围与尚缺项 |
|---|---|
| 正常启动与事件 API | normal18retry01 于14:07:26 UTC构建退出0，14:08:06 UTC启动（launcher PID9716）；API于14:11:51 UTC可用，14:16:37 UTC两项新研究 Worker 就绪，启动门3/3通过。后续普通 Worker 于14:25:55 UTC仍有新鲜心跳，priority心跳却停在14:23:58 UTC、到14:27:10 UTC才恢复，因此 PC 曾显示健康2/3有实际依据，不能写两者始终新鲜。未证明是 watchdog 终结研究。早期16次连接错误和 normal17 历史读取证据分别保留。 |
| 正常 PC 事件页 | 本轮用本机 Chrome 真实打开8047，ETH/已有多仓的当前公告锚定规则和单份概率表通过，已有截图。normal17 的九格、旧规则、覆盖、缺资料、固定链接和两刷新仍保留原实测；固定链接首次定位等待超时后自然恢复的过程未掩盖。 |
| 真实研究与完整报告 | 旧 BTC 任务 `run_c12461bfa1cd43a184c110def59d757b` 的RPC前15秒等待失败原件保留。normal18修复版上线后，Chrome仅点击一次新请求 `riskresearch_6d94695c7e842c738833ce3efa739bff879b922b`，生成 `run_175dd4ae90b643e0af041727d70eb868`。该 Run 已于14:27:12.99 UTC failed：`research_method_binding_failed`，精确消息为 `Original research Task changed or could not be verified`，cause=`cancellation_projection_wait_elapsed`；DSH link、研究结果和报告均为0。14:23:13 UTC的 running及等待核验截图只保留为当时状态，不能代替真实终态。normal18随后于14:46:15 UTC正式停止，后续认证/心跳与本次回执统一修复已通过工程控制；正常19真实报告、行动及 ETH研究仍待验收。 |
| 提醒显示与确认（本历史版本） | 8074 独立工程私库已有原生条件触发和通知交付。首次浏览器访问因保存的 origin deny 被安全审批拒绝；用户明确指定本机 Chrome 后，第二次真实 Chrome 访问也在窗口打开前被同一策略拒绝，未创建浏览器窗口。未换地址、代理或其他浏览器绕过。当时真实组件的 display、ack 及刷新恢复同一回执未验收；后续 normal21 正常8047自然失效通知的同回执展示/确认/刷新已通过，见本指南顶部。8074触发夹具仍不宣称PC通过。 |
| 安全重启恢复 | normal17 停止后 normal18retry01 启动门3/3通过；已逐项核对4个事件监控 head ID、对应 evaluation ID/认证hash及6个coverage ID与 normal17 一致，未重复或丢失，仍为 waiting/pending。这项事件状态恢复门已通过；研究、行动及提醒回执的完整恢复仍不能据此判通过。 |

可复查证据：

- [normal18 启动状态](../../../artifacts/decision-product-v1/core-main-flow-20261015/normal18-http/STATUS.md)及[3/3就绪汇总](../../../artifacts/decision-product-v1/core-main-flow-20261015/normal18-http/SUMMARY.json)；[独立重试构建退出0](../../../artifacts/decision-product-v1/core-main-flow-20261015/release18/build-end-retry01.json)、[正常启动回执](../../../artifacts/decision-product-v1/core-main-flow-20261015/release18/launch-retry01.json)。
- [本轮 Chrome 操作记录](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/checks.json)、[Chrome 市场页可见区域](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/chrome-market.jpg)及[完整市场页截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/chrome-market-full.jpg)。
- [点击新 BTC 请求后的等待保存/核验截图（当时未完成）](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/chrome-BTC-request.jpg)与[当时的 running 页面文字（随后已failed）](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal18/BTC-running.txt)，不把旧截图当作当前运行状态或完成证明。
- [本次新 BTC 真实研究未通过说明](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal18-research/STATUS.md)、[failed 终态与零结果汇总](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal18-research/SUMMARY.json)及[精确失败来源](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal18-research/terminal-failure-provenance.json)。
- [事件重启恢复原件](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal18-research/event-restart-restoration.json)：4个head/evaluation及hash、6个coverage身份与 normal17 一致；并非新触发或研究成功。
- [normal17 启动与事件读取状态](../../../artifacts/decision-product-v1/core-main-flow-20261015/normal17-http/STATUS.md)及[9项读取门汇总](../../../artifacts/decision-product-v1/core-main-flow-20261015/normal17-http/SUMMARY.json)。
- [PC 实际操作记录](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal17/checks.json)：包含九格切换及固定链接首次等待超时/恢复。
- [市场与事件页完整截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal17/normal17-market-full.jpg)、[当前可见区域截图](../../../artifacts/decision-product-v1/core-main-flow-20261015/pc-normal17/normal17-market-view.jpg)。
- [BTC 真实研究初始观测](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal17-research/initial-request.json)曾为 running；后续以[已保存的 failed 原因](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal17-research/db-failure-details.json)及[修复前失败记录](../../../artifacts/decision-product-v1/core-main-flow-20261015/M5-normal17-research/pre-host-read-fix/normal17-failure-message.json)为准，不以初始受理状态代替完成。
- [normal18 首次构建失败回执](../../../artifacts/decision-product-v1/w5w6-release-18/build-execution.json)及[磁盘空间失败日志](../../../artifacts/decision-product-v1/core-main-flow-20261015/release18/build.log)保留；后续 retry01 已成功构建与启动，未覆盖首次失败。
- 为解除磁盘不足，两组旧测试 SQLite 及其 sidecar 约8.57GB已逐文件迁至D盘，并核对前后 hash一致，正常数据库未迁移；[第一组迁移索引](../../../artifacts/decision-product-v1/core-main-flow-20261015/release18/cold-test-relocation.json)与[第二组迁移索引](../../../artifacts/decision-product-v1/core-main-flow-20261015/release18/cold-test-relocation-02.json)保留原位置和字节证据。
- [8074 工程私库只读就绪回执](../../../artifacts/decision-product-v1/pc16-event-01/server-ready.json)：仅证明独立服务与通知数据可读，不证明浏览器回执交互通过。

- [本次实施计划](executable-product-v1/EXECUTION_PLAN.md)
- [工作包与未完成项](executable-product-v1/tasks.json)
- [逐批操作和首败记录](executable-product-v1/EXECUTION_LOG.md)
- [本次 PC 验收计划与证据目录](../../../artifacts/decision-product-v1/w5w6-live-05/PLAN.md)
- [本次修订前的手册原文](../../../artifacts/decision-product-v1/w5w6-core-repair-03/PC_USER_GUIDE.before-op039.md)
