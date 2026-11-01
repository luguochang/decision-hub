# 事前决策主流程实施与验收计划

2026年10月15日。承接[旧总计划](EXECUTION_PLAN.md)、[核心事件清单](CORE_EVENT_EXECUTION_PLAN.md)与[最新对抗审查](../KRONOS_CPI_ADVERSARIAL_REVIEW_2026-10-15.md)。本次先交付 PC 上可以持续使用的 BTC/ETH 主流程，工作记录保存到 `artifacts/decision-product-v1/core-main-flow-20261015`。原 W0–W9、33 项及 G/E/V/I 未完成范围继续保留。

## 旧目标完成情况

| 范围 | 已有成果 | 仍未完成 |
|---|---|---|
| 正常启动和输入 | normal15 已正式安装、构建并启动；CPI/nowcast/history 与名义利率、实际利率、广义美元有真实采集原件 | 旧 BLS 源长期失败后的事件覆盖兜底；两后台评估通道超时 |
| 事前事件预案 | 下一期 CPI 九格、BTC/ETH 各九份 V2 条件预案、宏观依据；PC 情景/资产/持仓切换已测 | 对应公布前提醒和风险处理；共识差模型；公告锚定特征 |
| 发布和交互 | 原件与版本回读、固定链接、页面内刷新恢复 | 首次读取偶发超过客户端30秒；完整PC门与重启恢复 |
| 公布后主流程 | 已有真实BLS四指标读取、价量特征、DSL、outbox与其他主体的回执能力 | 事件实际值激活、事件所属监控、站内提醒/确认和结果回看尚未接通 |
| 真实研究与学习 | 有DSH执行、输入/角色/审核/发布等工程资产 | 新真实研究完整完成仍未验收；结果生成技能、后续实际消费和增量评价未完成 |
| 扩展 | 有跨资产分层方案；Kronos K1 已实际运行并裁决为不准入 | Kronos K2 前瞻 Shadow；FOMC、黄金、纳指、恒科独立主线尚未完成 |

**旧目标整体未完成。** 本计划把能直接让主流程工作的单元排在前面，工程交付与长期预测效果各有状态。

## 2026-10-17 最新检查点：release33

- K1 已完成有限实际 shadow，`engineering_trial_complete=true`、`financial_admission=false`。ETH 尾部漏判、非法 OHLC 与 CPU fixture 失败原样保留；K1 不重跑、不写成正式预测能力。
- release32 本机 Chrome 单次 BTC Run `run_5bdc18a17d174045846b8f0a323d5909` 真实失败：原生首因 `dsh_model_step_timeout`，公开 `host_terminal_result_binding_mismatch` 为二次遮蔽；报告、独立复核、行动均为 0，ETH 未提交。release32 已官方停机。
- release33 是当前唯一主线：拆分失败终态 binding 与 result unavailable；把 model watchdog 从准备阶段移到真实 Provider delegate；增加无敏感数据的分段诊断；模型保持 `gpt-6.1-sol/medium/max_tokens=8192`，不删研究上下文、不把异步研究硬切为 60 秒。
- release33 全新 BTC 必须通过既有 11 项 core gate 才允许 ETH。详细计划见 `artifacts/decision-product-v1/core-main-flow-20261015/release33/PLAN.md`。
- K2 作为独立 `planned_non_blocking` 里程碑，`required_for_release33=false`，激活门为 release33 全新 BTC core 通过。K2A 新协议/隔离 Worker 接线和 K2B 28 天 BTC 前瞻窗口见 [KRONOS_K2_PROSPECTIVE_SHADOW_PLAN_2026-10-17.md](KRONOS_K2_PROSPECTIVE_SHADOW_PLAN_2026-10-17.md)。

## 本目标的用户结果

用户打开正常PC产品，能查看当前资产的方案，以及未来核心事件的事前预案、依据、缺口和有效期；无需先手写研究题目。事件公布前有风险提示，公布后认证实际值并激活唯一情景，随后显示等待确认、触发、失效或到期、提醒和确认回执，最终回看原预测与后续结果。普通日保留已有24h研究/条件方案入口。

## 执行顺序

| 单元 | 具体实现和代码边界 | 本单元完成条件 |
|---|---|---|
| M1 公布结果激活 | 新 `ports/event_realization.py`、`application/event_realization.py`；复用 `bls_cpi_original`、schedule、event-owned forecast和既有event持久化；接BLS公告窗口采集 | 同统计期/指标匹配，保留真实known时间，只激活一格；旧预案不修改；正常未来事件显示等待公布；受控集成推进后可读取实际激活原件 |
| M2 监控与提醒 | 新event-owned lifecycle/monitor；复用合法Action DSL、MarketPathStore、确认计算及原子outbox；在composition/scheduler接有限tick；通知增加显式事件主体 | 空仓/已有多仓分别求值；显示等待/触发/失效/到期/缺数据；同bar及重复tick不重复提醒；PC能显示和确认同一通知 |
| M3 事前覆盖与风险 | 复用日历及来源台账建立应交付slot，T−24h/−2h/−30min统一配置；源失败仍保事件/缺口；增加公布前风险状态，公布后特征拆趋势与公告锚点 | 没有输入时能看见具体缺口和漏报；公布前风险处理与公布后预案明确；修复“公告后跌而滚动6h仍为正、小时量来自公告前”的规则问题 |
| M4 结果回看 | 新事件outcome投影，复用现有评分与路径/成本计算 | 分别展示事件判断、资产反应、空仓/减险方案结果；未成熟保持等待，缺失/晚报可查，不伪造收益 |
| M5 真实研究与PC交付 | 沿现有DSH→审核→报告/行动入口跑真实研究；修当前详情重复重放造成首开失败的具体断点；一次正常构建/启动验收 | BTC/ETH真实研究各有可核对结果或定位并修复具体失败；正常PC主线、刷新返回、回执和安全重启通过；更新用户手册和截图 |

M1/M2是当前最明确的producer/consumer接线缺口，先实现。M3并行准备，M4消费前两项产物，M5贯穿集成收口。每次选择能推进用户流程的具体失败修复，不追加全仓性能专项。

## K1 Kronos 候选验证纳入本次目标

K1与主流程使用独立运行环境和新trial身份。先完成M1–M5的可用主链，再完成下列有限验证；不用模型训练或长期效果研究拖住主流程上线。本次K1须给出实际运行结果与对照结论，不能以已有调研文档代替。

| 步骤 | 实现内容 | 可核对交付 |
|---|---|---|
| K1.1 隔离准备 | 锁定Kronos源码提交、small权重和tokenizer revision/hash；独立Python环境，依赖及模型文件放项目指定目录 | 版本/依赖清单、健康检查；停模型不影响API和监控 |
| K1.2 原始路径 | 对薄推理内核适配原始采样路径，保留默认均值兼容；显式输入时间、已收盘bar、单位、seed与错误 | 一个可执行离线命令和不可变输入/输出/推理回执；不从均值反推概率 |
| K1.3 真实数据试跑 | BTC、ETH既有24h数据/标签语义，独立新trial；先16条路径核格式/资源，再完成冻结的小规模配对对照 | 两资产各有实际结果或具名失败，资源/时延、简单基线与Kronos同原点报告；不修改旧COND_VOL协议 |
| K1.4 接入裁决 | 记录相对基线的分布/下行风险、可用性与成本；只做候选级小实验，长期前瞻另列 | 明确可继续候选/停止/证据不足及原因；未准入产物不进入正式动作，不能宣称已提高胜率 |

K1不含首轮微调、新team/Harness迁移、图数据库或多期限全展开。若当前硬件/权重确有阻塞，须先完成实际尝试、保存错误和可复现命令，形成具体替代方案；不能只写“未验证”后把K1标完成。

## 接口与分层

- Kernel ports/decision：事件实际值、激活、状态、条件和结果定义；不导入Torch或新agent框架。
- Kernel application/persistence：认证读取、事件owner、幂等键、短事务提交、通知与回执；复用现有账本，迁移采用增量和旧版兼容。
- source/worker：官方输入与有限调度；HTTP GET只读已保存结果，不临时采集或启动模型。
- query_views/API/React：同一事件/版本贯穿事前、激活、提醒和结果；后端状态决定页面，不由前端猜测已完成。
- DSH与现有编排：负责真实研究；有进展的异步研究允许超过60秒，通过原阶段、取消、租约和deadline管理。

第一批按已发表的prior-month目标完成激活，页面继续明示其比较基准；共识差使用独立target后续接入，不能偷换旧概率。新事件主体不得伪造Run或借原生研究Action发布权限。

## 只做主流程需要的验证

1. 每单元先跑最小主路径集成，再针对会直接造成错事件、重复触发/提醒、旧结果覆盖新结果的问题保留必要负控。
2. 不开展全仓覆盖率、参数搜索或长期收益测评；已有通过项只在受影响时复验。首次失败保留，修正后一次定向复验。
3. 公布/触发/到期需要推进时钟，使用独立测试库和已认证样本执行；明确标为工程演练。正常库保留真实日期、真实known，不倒填昨晚预测。
4. 正常PC分别验证当前真实未来事件、已有研究入口、通知回执、返回刷新和错误恢复；保存截图、API结果与少量DB原件。
5. 功能闭环具备后只构建一次并正常启动，安全空闲检查后重启一次验证恢复；有新失败才追加构建或测试。

## 暂后推进但保留的内容

调查共识/市场定价增强、FOMC插件、真实结果驱动技能及后续消费、黄金/纳指/恒科仍在旧范围。Kronos的K1候选实际运行属于本次目标；正式金融准入、微调、团队迁移和图数据库仍是后续范围。

金融效果持续积累真实前瞻记录。主流程完成不等于70%胜率；长期样本观察不阻塞已经可用的工程交付。

## 操作记录与目标完成定义

每步记录：改了什么、对应主流程哪一段、验证命令、首次失败/修正、当前运行身份、下一步。主台账继续连续OP号；每个单元保留一份简短状态，不反复新增同内容长文档。

主流程在M1–M5均有实现和相应自测、正常PC截图/用户手册/重启记录后交付；本次目标完成还需K1实际运行和有限对照结论。若真实事件尚未公布，工程激活演练与正常等待状态分别交付，实际前瞻结果另列观察；不得以需要等待一个月为由留下未实现的consumer，也不得把演练当真实预测命中。


## M5 正常18真实失败后的统一修复（OP126，实施前冻结）

正常18新Run在首次Host调用前的后续Task认证失败，DSH调用/结果/报告为0；192秒priority心跳空档与新的自动请求覆盖本次手动回执均为真实缺口，当前M5未通过。正常18于14:46:15 UTC在新鲜只读空闲检查后正式停止，原数据与失败保留，14个候选源码原字节备份于`M5-unified-fix/before`。

本轮按三份只读对抗结论形成一个实施单元：

|责任边界|具体实现|必须验收|
|---|---|---|
|executor/graph及worker薄接线|owned native callable执行freeze、原graph/recipe准备、quality/selection；复用取消drain admission，Session不跨await/线程|真实同步慢段期间心跳/续租/取消调度仍推进；取消后等待线程关闭才释放|
|Selection及original_preparation|完整RO原件/readset/source准备，独立短writer核全query/absence/raw bytes及当前权限，再按原格式幂等插入；新增显式fresh RO verifier供binding复用|慢RO不占writer；另一连接可写心跳；原件/absence改变或伪造prepared拒绝；原selector身份历史保留|
|runtime binding/web_runtime|原完整慢认证同一Future等待；同一owned线程关闭长Session再独立fresh事务复核readset/owner/lease/cancel/deadline/代际；Host就绪宽限从首次真实RPC起算且不重置|超过观察窗不误判；期间取消/原件变更拒绝；未知submit/回执投影等待不二次提交；不延长原Task截止|
|API/DTO/PC本次回执|按owner+scope+原command查已保存canonical请求；localStorage仅保存定位；主卡追踪手动A，自动B独立次栏|A运行→B新排队→A失败→刷新仍指向A的Run，POST仅1次；未知owner/scope不泄漏|

具体接口、身份字段、读写边界和有限门禁以三份实施前审查为准：`M5-normal18-research/binding-design-review.md`、`heartbeat-and-binding-review.md`、`request-followup-review.md`。不新增编排框架、全局已认证缓存或模型时长限制。保存必要phase开始/结束及安全耗时，禁止记录prompt/密钥。

顺序为备份→分工实现→只跑直接保护上述缺口的有限门禁→一个新发布身份的正式构建→新服务启动门→本机Chrome一次BTC真实DSH/审核/报告/行动后ETH→更新截图/手册/台账。不得再逐条英文错误放宽超时后反复部署。旧失败不改成成功；真实报告门通过前不宣布可交付。M1–M4、K1和旧W0–W9范围及原验收定义保持。

实施接线补充：同一既有DshCancellationProjector增加`read_owned`薄入口，完整慢准备及新fresh Session在同真实thread内关闭；该入口取消等待真实Future drain，不新增executor或改变普通read/write语义。额外源改前已完整备份。Graph共用runner沉淀到`packages/orchestration/owned_work.py`，worker旧导入兼容re-export。

统一修复复审补充：readset复验结束后另开第三短物理事务核当前权限与Link完整身份/absence；真实Host terminal异步推进可在同原native Future内、旧session/lease先关闭后，凭具名Link差异+同代immutable身份+认证terminal metadata最多重读一次全原件。不是屏蔽terminal字段或重放RPC。41项分项工程门通过，完整Runtime工程主路径1通过仍使用受控Host；真实BTC/ETH报告和PC门等待normal19，不提前宣称可交付。


## M5 normal19 本机Chrome发现的执行准备与进度读取问题

本机Chrome已真正打开8047，并单次提交ETH原请求。normal19启动门通过，但完整真实研究未通过：原Run已三次进入planning/coverage，尚无DSH Link/结果/报告；这不是模型在持续研究。首次执行prepare的30秒未确认被恢复分支接纳，代码中执行准备及准备恢复均存在完整原件解析位于写事务内的问题；停写约6分钟的具体阶段仍缺原生栈，不把字体警告当唯一根因。进度页44–59秒完整认证请求超过30秒客户端等待，随后每3秒重复，是单独可复现的PC缺陷。

当前Run仍活动，正式源保持封存。先在artifact内准备可审查候选，原Run终态/原Future退出后才修改并发布：

1. 复用已有`GET /v1/runs/{id}`供PC基础进度轮询；重详情/原件材料改为用户显式读取，不自动轮询重认证。已有报告认证与停止命令的owner/原回执服务端权限不变；未读取的材料数量保持未知。
2. 执行准备在30秒观察窗后继续等待同一原写Future，不重交prepare、不立即转恢复再执行。取消和截止后的清理必须等待原线程关闭，原Task截止与Host许可不延长。此次仅修Future生命周期，不宣称长writer已经全部拆分。
3. 针对已证实进入原件验证链的CPI PDF纯解析，最多复用4份实际body（合计≤20MiB）对应的不可变解析tuple，key包括body及解析器/依赖身份。SQL、raw bytes/hash、provenance、PIT、权限和新鲜度每次重新核验。此为减少重复CPU的候选；不能以纯解析复用代替原件认证，也不能预先认定解决了全部写锁问题。
4. 正式结构缺口继续按`runtime-preparation-blocker-review.md`处理RO认证候选→完整readset短writer→原子全件及恢复分支；若上述最小切片后真实Run仍阻塞心跳/续租或prepare未确认，必须完成该结构拆分，禁止再递增超时循环发布。
5. Host活动快照连续性与暂缺时不耗尽真实尝试有独立最小方案；未采用任意created_at+1h限制，适用期应来自事件/请求或用户取消。不能放宽快照认证或仅刷新scope冒充完整活动证明。

有限验收：两项原Future生命周期门、两项PC基础进度/显式重详情门、两项纯解析身份/原件仍重验门，以及受影响的原完整Runtime工程门；随后一个新正常发布身份，在Chrome同一次真实研究从准备进入DSH→审核→报告/行动，核对心跳与原请求跨刷新/资产返回。没有真实结果前保持未通过，不扩大为全仓测试。

实施前审查与草稿仅在`M5-normal19-research`；正式源码/正常库未因草稿改动。若真实原Run在原截止后结束，则保留其实际错误和原始时间，不改写成成功。
