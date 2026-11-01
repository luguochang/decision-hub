# 原产品增量交付需求覆盖账本（审阅草稿）

> **2026-10-27 用户暂停，构建已取消、服务已停止。** 当前状态与执行错误见[暂停交接](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/HANDOFF_PAUSED_2026-10-27.md)。下方13:24等旧“正在运行”表述保留为历史，不能继续执行。此次不改任务验收状态，不新增ACCEPTED。


2026-10-27 主流程优先纠偏：当前先完成唯一构建后的原系统恢复及最新普通市场预测验收。授时、NFP补充、非零funding支线停止新增，不阻塞这一链；全部最终范围和长期阈值保留。已确认原代码接通BTC/ETH×4h/24h/72h同一入口，但六组合真实运行验收尚未完成。

合格研究按[需求§4](../01_REQUIREMENTS.md)及[原诊断§6.1、§7](../../diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md)六条逐项取证。原文明确效果评价不阻断单次研究，未校准应如实披露；不能仅凭financial/execution=false、NTP状态或私有observer的0.25s操作政策否决普通研究，也不能因此授予操作权限。冻结信息集/known_at、期限概率、证据反证、含成本方案及观望条件、独审、重启后PC可读仍必须实证。旧14不自动改算合格，时钟异常/失败历史不删除或倒签。

2026-10-27 13:24 +0800 当前进展：17个PCE/NFP/FOMC事前规则与actual reader源已Main采用（5旧/12新），物理Main49例实际通过，原normal27841正在唯一运行；原预览已主动维护停止。BTC/ETH各90个连续已收盘日线已通过原DataJob真实采集和原件/分区回读，覆盖截至UTC10月27日零点；一份历史ETH4h报告/评分保留。七角色控制器r4-r2已C0/I0/M0但尚未实际绑定或启动。管理员持久授时命令已具名准备并交用户，未假设已执行；全部长期与真实金融门仍未完成。详见INCREMENTAL_THREE_EVENT_FAMILY_MAIN_INTEGRATION.md。

| 最终验收 | 当前真实证据 | 未完成部分 |
| --- | --- | --- |
| 六组合14自然日及延迟 | 一份ETH4h真实历史报告，单次发布约38分钟 | 连续资格、其余组合和P50/P95均未成立 |
| 至少两类真实事件全流程 | 三族部分事前规则/actual读取Main软件验证，真实全链未验 | 真实事前方案、首发actual、后继与通知送达 |
| 10笔PAPER成交及退出重启 | 4h旧语义保持，24h/72h私库完整目标退出验证 | 真实行情驱动成交及结算恢复，当前真实成交计数0 |
| 四周真实评分 | 一份真实到期结果，Brier0.56/log loss约0.9163 | 四周样本、校准与持续采集的长期验收 |
| 30个完整失败业务回放 | 真实13仅完成transport映射/边界回放 | 完整业务回放当前计数0，缺真实前执行快照和native seam |
| 最终质量与交接 | 新源strict/Ruff0，旧诊断有记录；报告阅读导出已验 | 全实际交付范围清债、补齐优先研究进程与正式部署交接 |

此更新保留全部56 ID/9阶段及原八工作包，不将上述局部成功标为全部ACCEPTED。以下旧日期段落保留为历史，旧“当前运行中/无canonical”等表述以具名时间为准。

**2026-10-25复核：主线和原总控T01—T08均未完成收口。** 本文末尾新增原总控八项对照及actual05失去执行进程后的真实状态，不能继续把旧“运行中”记录当作当前状态，也不能把局部修复等同整产品交付。

本草稿保留全部 **56 个任务 ID、Phase 0—8 共 9 个阶段**；按 Q-005，原逐行估时合计 308h，不是实际工时或交付承诺。当前路线以 Main `AGENTS.md` 的用户最新裁决为准：保留原 `apps/decision-desk` 界面、身份、设置、请求回执和业务流程，按实际缺陷增量修复；v3 与私有工作区成果保留为可选资产，不再据任务名要求整体替换原产品。

这是一份覆盖草稿，**不修改任何任务状态，也不新增任何 ACCEPTED 判定**。初版只读文本和已封运行收据，没有打开数据库、运行产品/测试、启动服务、联网或调用模型；后续具名复核另在对应日期段落说明，不能外推初版的观察范围。

## 阅读口径和当前缺口

- 正式 v3 `STATUS.md` 实际展开了 27 个任务行：25 行含技术 `REVIEW`，T-000 与 T-303 仍在进行；任务状态栏没有 `ACCEPTED`。Main 的另一份 STATUS 仅有陈旧的 T-000—007 八行，不能用作当前原产品完成度。其余任务必须逐项保留，不能按这两份状态表的已展开行数缩小范围。
- 表内“现有证据/资产”表示已阅读报告或既有只读审计所记录的资产，并非本轮重新执行或重新鉴真。`REVIEW`、私有提交、离线 GREEN、原实现存在、真实单例发布分别保留其边界；未知项写“待验证”，不推断未读代码不存在。
- 原 native 候选已记录完整 strict 零诊断、51 文件/556 项通过；这是候选离线结果，不能代替实际 Host/Provider、同 Run 报告、真实 PAPER 或长期指标。
- Q033 同 Run 历史实测：返回的是合成夹具 `filled`，原 `elapsed < 5` 门以 **6.0007603999983985s** 失败，pytest **1 failed / 73.69s**。65 个显式 pin 前后相同、453 个已报告仓库模块来源均为 Main；denied/child/plugin HTTP 为空，记录到 1 个 D 临时 SQLite 目标、5 个临时 loopback 事件。未进入后续研究/native/报告步骤。它不替代旧 5.626s 失败，也不能作实际金融性能或整链成功。
- 四周评分、14 自然日、两类真实事件、至少 10 笔有退出和恢复的 PAPER 等门槛仍保留。正式研究批次的旧失败/正常 revise/未受理项与授时异常封存均不能删除、拼接或改名为合格。

证据简称：**F**＝正式 v3 任务清单/状态及具名任务报告；**O**＝Main 增量报告和 AGENTS；**B**＝原后端只读审计；**P4/P5/P6**＝明确标记私有准备的报告；**R30**＝T-705 私有真实失败语义回放报告；**R033**＝Q033 历史实测只读收据。准确路径见文末。

## Phase 0：基线与基础设施（8 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-000 | 可恢复的源码/资料基线、分支与变更追溯 | F/Main T-000 保留八处旧存档读取受阻历史；后续 O 有逐源备份、精确提交与集成回执 | 完整旧存档及例外的最终清点、可恢复性、交接清单待验证；局部备份不等于完整存档完成 |
| T-001 | 清楚的模块职责、API/Worker/PC 接缝 | F/T-001 技术 REVIEW；原 UI 和后端保留，B 已定位原入口、执行器、提交与报告接缝 | 原入口到报告的真实同 Run 纵向交付待验证；不以新骨架存在代替原产品可用 |
| T-002 | 真实资料可按决策时点读取、来源可追溯 | F/T-002 技术 REVIEW；B 记原 FactStore/信息快照/原件 reader 已有时点约束 | 原产品各资料类型的时间边界、缺源/缺口、修订与恢复待验证；不得回填旧 known_at 或以新源补旧信息集 |
| T-003 | 完整录制和严格离线回放，无在线兜底 | F/T-003 技术 REVIEW；原 Capture/Replay 及 R30 资产 | 原 Host 实际请求、未知 ACK、超时/取消与恢复录制闭包待验证；旧 capture 的响应表示不冒称原始传输字节 |
| T-004 | 可复现的 lint/type/边界检查 | F/T-004 工具技术 REVIEW；O 有完整 native strict 和 UI 构建结果 | 当前实际交付后端及最终改动范围的检查待验证；Q031 仍有既存及新增/变形诊断，不声称全项目 strict 零错 |
| T-005 | 合格定义、状态与失败/取消/退回语义一致 | F/T-005 技术 REVIEW；B 记原 Run/task/checkpoint 和审核循环 | 原同 Run 的终态、恰一次提交、真实审核通过及 PC 可读联合判定待验证；revise/失败不能改算合格 |
| T-006 | 错误码、结构化安全日志可诊断 | F/T-006 的 13/CoreError 契约；O 保留配置失败、产品门失败和原始日志 | 原 API→Worker→Host→回执错误关联、凭据脱敏、unknown ACK 不误重试待验证；不强迫旧错误命名空间伪装新码 |
| T-007 | 隔离、可重复的测试与真实/合成夹具分界 | F/T-007 技术 REVIEW；O/Q032 两个原夹具定向 2PASS，保留原断言 | 最终交付相关回归和来源装配待验证；原 14 项批次仍是 12PASS/2FAIL 历史，不能用定向结果改写整批 |

## Phase 1：架构主干（6 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-101 | 研究主循环、DSH 上下文、断点续跑 | F/T-101 隔离 Host 技术 REVIEW；B 记原持久 checkpointer/execute/resume；O 有 591→592 完整重读修复与 native 回归 | 当前候选接实际官方 Host、live-root retained/admission、同请求重启恢复和 unknown ACK 恰一次待验证 |
| T-102 | 事前冻结真实信息集，恢复不吸收晚到资料 | F/T-102 技术 REVIEW；B 记原 Run snapshot 与来源时点边界 | 原真实请求的 cutoff/冻结清单、晚到补写、跨 Run/owner 拒绝和恢复同集待验证 |
| T-103 | 至少三条关键证据和一条反证，真实来源可追溯 | F/T-103 技术 REVIEW；原 native 材料目录/Provider proof 与封闭夹具可用 | 原产品实际检索到独审/报告全链引用、逐字原件、来源身份和未来资料拒绝待验证；无资料不能补假证据 |
| T-104 | 不可变原件归档、去重、损坏显错 | F/T-104 技术 REVIEW；B 记原 RawBodyArchive 按 SHA 存读及 capture 元数据 | 原研究引用到全部原件可回读/校验、备份重开、损坏与缺件失败语义待验证；不改写历史原件 |
| T-105 | 会话追加日志、前缀/序号/归属完整 | F/T-105 技术 REVIEW；原 session archive、Provider proof 和原上下文守门可复用 | 实际 Host 连续追加/compaction/恢复、历史改写/错会话拒绝与报告日志关联待验证 |
| T-106 | 自包含研究快照，可脱库离线读取 | F/T-106 技术 REVIEW；原通用报告已有 confirmed snapshot；原永续 V5 有 source/artifact 及受校验读链 | 实际待验是完整报告 JSON/HTML 的保真离线阅读：正文、必要定义、证据/反证、成本方案与审核可读，不能只留下需数据库解开的 ID。旧 generic bundle 不支持 V5 不等于阅读导出不可用；不强制每次下载全部后台源码、SQL、会话或密钥，也不把尚未实测改为通过 |

## Phase 2：普通市场 4h（10 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-201 | 真实历史同类基率、方向频率与波动分位数 | F/T-201 技术 REVIEW，报告记录双资产真实冻结/复算；B 可复用旧来源接缝 | 原 4h 产品的样本范围、成熟结果、PIT/缺口/分位数和样本不足行为待验证；不把回溯资料称当时可知 |
| T-202 | LLM 读真实基率作主观估计，概率合法 | F/T-202 单视角真实/离线技术证据；原 native 协议候选完整回归 | 原实际请求的基率提示、模型原答、三方向定义/加和、引用与费用边界待验证；主观估计不冒称校准概率 |
| T-203 | 三个独立视角、稳定聚合和分歧披露 | F/T-203 三独立会话技术 REVIEW；原 native 多角色相关测试资产 | 原产品真实三视角的会话独立性、同一冻结输入、聚合/分歧和坏答拒绝待验证；不得共享结论凑一致 |
| T-204 | 多空两侧入场/止损/止盈/触发失效，含费用/资金费 | F/T-204 及 v4 激活修订资产；B 记原合约/报价/执行链已存在 | 原方案真实费用政策、资金费投影、几何/时钟/选择理由、观望解除与实际入场重核待验证；无依据 EV/触价概率保 unknown，候选不等于下单许可 |
| T-205 | 独立审核、合理退回、有限修订保留原答 | F/T-205 记录真实 revise；O 修复候选已有完整独审容量/负反馈回归 | 原实际独审与父材料/Run/会话绑定、退回暂停/补证/再审、批准不可强改待验证；正常 revise 保留且不计合格 |
| T-206 | 真实审核批准后同 Run 持久发布，重开可读 | F/T-206 技术 REVIEW；B 记原 Graph/Kernel commit 与 saved-report reader | 原 PC 返回 run_id→native→Graph commit→saved report 同一 ID 的实际闭环待验证；R033 尚未走到这些步骤 |
| T-207 | 快照生成安全 HTML，首屏有方向/概率/方案 | F/T-207 脱库 HTML 技术证据；原 UI/纯 renderer 可复用；通用报告已有导出 | 保原页面信息架构验证完整报告 JSON+HTML 自包含导出、必要正文/定义的离线阅读和安全链接；保留 V5 现读链鉴真，不额外强制完整后台审计封包，不宣称已复现源码升级 409 |
| T-208 | 原 PC 发起研究、请求回执、刷新/丢 ACK 恢复 | F/T-208 为新 PC 技术资产；O 原 UI 98 项/构建通过；B 已定位原认证 POST 和 receipt | **在原 UI** 实际保存设置/提交/刷新/恢复，同请求不重复受理/付费，身份/owner 和状态清楚待验证；不能以新前端替换算完成 |
| T-209 | 原 PC 可读完整报告，来源/审核/未知明确 | F/T-209 新展示页技术证据；B 记原 saved-report API/DTO/页面 | 原真实 BTC/ETH 报告在原页可读、刷新/重启/历史兼容/导出待验证；来源未核、观望、未激活和未知成本不能伪装绿灯 |
| T-210 | 双资产 4h 完整链，真实录制且连续三次成功 | F/T-210 记录隔离 v3 修复后三连及全部六例分母；O 原 native 556 项通过 | 原产品实际 PC→官方 Host→独审→同 Run 报告三连、失败保留、重启/导出待验证；R033 前置五秒门仍失败，不能借隔离 v3 结果过门 |

## Phase 3：24h / 72h（3 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-301 | 24h 独立信息窗口、样本/目标与时钟一致 | F/T-301 记录双资产真实 24h 技术 REVIEW，4h 字节兼容 | 原 UI/原 runtime 24h 的真实同 Run、历史/当前资料、费用/审核/报告与旧 4h 兼容待验证；不把仅类型支持当端到端支持 |
| T-302 | 72h 独立长窗口，周末/事件/流动性未知如实披露 | F/T-302 记录双资产真实 72h、两年采集及旧报告兼容；B 记旧全期限 PAPER 包仍为候选 | 原产品 72h 研究与可读报告、长窗不足/资金费多期、真实周末和事件未知披露待验证；研究支持不等于 PAPER 已全期限认证 |
| T-303 | 六组各十例、固定配置合格率≥80%，P50≤20min/P95≤40min | F 明确保留旧 12 例与 v4 8/60 授时异常封存批次；统计/登记/审计工具保留 | 健康授时、事前登记的同配置完整新 60 例及原产品 PC/原件/重启验证待完成；失败/退回留分母、未受理保持 pending，不拼批、不重跑旧 ID 凑数 |

## Phase 4：事件研究（7 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-401 | 经济数据共识真实接入，字段/单位/时期可核 | F 未展开正式事件验收；B 记录 BEA/PCE 来源包和来源受限事实，不等于共识已交付 | CPI/PCE/FOMC/就业等共识来源、授权/配置、原件/收取时点及无 Forecast 行为待验证；无源必须 unknown，不补中性值 |
| T-402 | 真实事件日历、事前调度与到期身份 | B 记录官方日历包的有限测试/实际 ICS 观察；原事件 schema/界面资产可用 | 真实日历覆盖、UTC/修订/取消、提前 24—72h 冻结预案、幂等调度及恢复待验证；日历可读不等于 Actual/共识齐全 |
| T-403 | Actual 落地、惊喜/情景判定、预案匹配和更新 | 既有事件协议/私有设计与测试资产可供复核；本次未取得原产品完整事件验收记录 | 真 pre-cutoff 预案、同事件/指标/单位/时期 Actual、修订/缺失/冲突、事后新 Run 待验证；公布后≤5min 事实公报与完整研究延迟分别计量 |
| T-404 | CPI 因果链逐节点验证；覆盖需求中的事件模板族 | P4 仅 CPI 纯候选 A 部分，明确 research_only/watch/unactivated、无真实事件发布 | CPI→利率预期→美债→加密节点的真实事前/事后核对待验证；CPI/PCE/NFP/FOMC/GDP 模板覆盖不得漏，至少两族需真实完整事件链 |
| T-405 | Polymarket 事件概率锚点、精确目标/时间映射 | B 记 T405 私有 41 项合成/Mock 资产，无实际成功响应或原产品集成 | 实际合法来源、事件/结算规则与目标同一性、流动性/时效/原件和 unavailable 行为待验证；事件 p 与普通方向概率不得混用 |
| T-406 | Deribit ATM IV / 25-delta skew 可核原件 | F 仅需求条目；本次未取得该原产品输入和验收证据 | 实际期权合约/到期/报价/单位、选 ATM/25delta 方法、时效和缺源行为待验证；IV 不能冒充方向概率 |
| T-407 | 基率/市场锚点/LLM 差异同屏且无信号明确 | 普通概率报告与展示资产可复用；实际市场差异端到端证据待验证 | 同目标/期限的可比较锚点及差异，差异<5pp 无信号、方向方案门和 unknown 展示待验证；缺锚点不伪填 0 或声称显著优势 |

## Phase 5：监控与提醒（4 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-501 | 条件定义/版本/状态、追加检查记录、可追溯来源 | D/monitors 私有数据层保留；P5 明确未正式集成/迁移 | 原产品创建/修订/停用/到期、CAS/命令幂等、manual price 与报告条件来源、日志/触发绑定待验证；手工阈值不冒充已审核方案 |
| T-502 | 真一分钟检查，触发一次，后继研究不阻塞循环 | 私有独立 worker/分钟原件/稳定后继 UUID 资产；不计自然时间验收 | 原系统实际独立循环、完整分钟 bar/receipt、连续穿越两观察、gap/stale unknown、崩溃恢复/停用竞态、enqueue ACK 对账待验证；后继研究合格率≥90%另计 |
| T-503 | 邮件/Webhook/Telegram，安全和真实送达分级 | P5：35 路径私有提交，38 自有/5 外部专项；actual_channel_send_verified=false | 明确配置的实际渠道/收件目标验证、通知→后继研究链待完成；queued/armed/unknown/transport accepted/receiver confirmed 区分，未知 ACK 不自动重发、不保存明文凭据 |
| T-504 | 原 PC 列出/新建/编辑/停用监控与通知事实 | 私有后台/API 及设计成果保留；PC-B 未接管原 UI；P5 无正式集成 | 沿原导航增量接线，身份/CAS/来源/SHA、unknown/到期/启停和后继研究、送达层级真实显示待验证；PC 不存渠道秘密，不把读取成功当送达 |

## Phase 6：PAPER 成交（6 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-601 | 显式 PAPER 账户、10000USDT 初始余额、持仓可恢复 | B 记原 account/revision/CAS/事务与 PAPER UI 接缝已存在；私有 PAPER 资产可对照 | 原用户确认、同账户身份、余额/冻结/持仓/重开待验证；不新建第二默认账户或复制资金，合成账户不计实际成交 |
| T-602 | 市价/限价/止损止盈执行，真实合约/行情约束与流水 | B 记原 repository.apply_fill、合约/报价 reader 和 scheduler；R033 有合成 filled 但五秒门失败 | 原报价 receipt/时效、tick/lot/ctVal、容量、手续费/保证金/CAS/幂等/未知 ACK 全链待验证；至少十笔实际 PAPER 不能由单测 filled 代替 |
| T-603 | 已确认事件方案按真实情景触发 PAPER 执行 | 原事件/手动执行及私有候选资产可复用；P6 明确整段事件自动化未验收 | 真事件/已批准预案/账户确认/执行 claim、单向互斥、恰一次下单与恢复待验证；研究 selected_side、监控 hit 或未激活候选均不是权限 |
| T-604 | 止损/止盈/失效/到期平仓，异常下仍可安全退出 | 原保护实现/测试在 B；P6 仅负现金 reduce 前置，另纯条件层为未授权 candidate | 实际持久保护循环、双触保守仲裁、价格 unknown 不挡到期、资金亏损退出、停机恢复与 quote 原件待验证；纯命中不冒成交/授权 |
| T-605 | 费用/资金费/PnL 结算、账户持仓流水可对账 | B 记原仓库入场/退出费用和 PnL 写入；P6 不声称完整 funding/settlement | 真正入场→止损/止盈/到期退出→结算归档，多期资金费/负净值/重启/幂等对账待验证；未结算不伪补已实现 PnL |
| T-606 | 原 PC 展示余额/持仓/订单/PnL 与确认操作 | 原 UI 保留，B 记 PAPER 页面/服务相关资产；本轮未验全生命周期页面 | 原页面真实账户、≥10笔成交及退出覆盖、历史/重启和拒绝/unknown 状态待验证；不得把合成流水填进用户账本 |

## Phase 7：结果评分与学习（7 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-701 | Brier/log loss/方向准确率/PnL，预测与真值同目标 | F Phase7 未展开；本次没有取得原产品成熟真值评分验收记录，具体实现待验证 | 发布前冻结预测、到期真值/缺失/修订处理、可追溯评分与≥4周真实报表待完成；回放匹配率不是预测准确率 |
| T-702 | 可靠性/校准曲线，样本数和未成熟明确 | 本次未核到原产品校准曲线验收；不推断代码不存在 | 使用实际成熟样本的分桶/频率/置信限制、空桶/小样本、稳定重算待验证；不得用合成分布冒四周质量 |
| T-703 | 按资产/期限/事件族/市场 regime 分层 | 已保留六组合及事件族身份需求；实际分层统计验收待验证 | 同口径分母、失败/缺失/成熟性、各层样本与时间窗、四周趋势待验证；不能靠删难样本抬表现 |
| T-704 | 校准器训练、版本/数据谱系，Brier 改善≥5% | F 只有计划条目；实际训练/独立评估与改进记录待验证 | 时序划分、无未来泄漏、外样本对照、版本/回滚/适用域与≥5%改善待证明；经验归因和人工批准后输入下一轮也需覆盖，不自动宣称学习生效 |
| T-705 | ≥30真实失败历史录制的严格离线回放 | R30 私有登记30/30匹配、68项测试；29 request_correlated_unverified＋1实际四交换HTTP采集失败，原件不变 | 原产品集成/完整性及适用失败范围待验；旧内部 bug/PIT/原生 origin 未被这30例证明，不以语义回放凑预测评分或正式 Phase7 完成 |
| T-706 | 原 PC 复盘中心显示评分/校准/分层 | 原工作台可保留；本次没有核实完整复盘页及真实评分展示 | 原导航上的预测/结果/证据/交易/因果节点对齐、可筛选历史/未知与四周指标待验证；无评分不能默认0或伪造曲线 |
| T-707 | 汇总七天研究/评分/PAPER 的自动周报，可发送 | 本次未核到原产品真实周报验收；P5 有私有通知层可供选择复用 | 真七天资料、稳定周界/时区、缺失披露、重启不重复生成/发送、明确渠道送达事实待验证；周报不替代四周评分累计 |

## Phase 8：最终交付（5 项）

| ID | 业务要求 | 现有证据/资产 | 尚需原产品验收 |
| --- | --- | --- | --- |
| T-801 | 安装/配置/日常操作/限制的用户手册 | 2026-10-25原USER_MANUAL候选已按原PC源码整理并本地提交；本机登录及设置回读有实际证据，不等于完整手册验收 | 事件/监控/复盘及完整PAPER操作仍须随最终原功能补齐、实测；完整研究/独审/报告双导出链待验 |
| T-802 | 部署/监控/备份恢复/排障运维手册 | 2026-10-25原OPS_MANUAL候选已核现有启停/备份源码并本地提交，未执行所列运维命令；不是恢复演练通过 | 实际发布物/依赖/配置/权限/授时/资源/服务启停、数据备份及恢复演练、日志脱敏和故障回滚待验证；不以临时 launcher 当部署方案 |
| T-803 | 100次性能测试及优化；研究P50≤15min/P95≤30min | O 保留观测归因、撤回实验与 R033 原5s门失败；不能相减估计通过 | 原程序时效门及真实端到端性能均需验证；固定配置/完整分母/计时口径，100次记录与P50/P95待完成，不将6s局部窗口混作研究延迟 |
| T-804 | 六组合连续14自然日≥90%，全部长期硬门 | F/T303 未通过完整批次；O 明确目标ACTIVE，未给实际长期资格 | 自动＋手动六组合14日≥90%、条件后继≥90%、P50/P9515/30、历史报告/持仓重启正确；≥2真实事件族全链、≥10笔PAPER覆盖退出/恢复、≥4周评分均不可缩短/回填 |
| T-805 | 全覆盖交接/代码审查、可复现验收与残余清单 | 本56项草稿、局部提交/peer/原件资产可作交接输入，不是最终签收 | 汇齐56ID/9phase证据、最终实际源码/产物/接口/手册/覆盖率、ruff+pyright strict零错与30真实失败回放门，明确未达项；所有合格研究原 PC 一键JSON＋HTML自包含导出，全部硬门通过才最终交付 |

## 原报告导出的精确边界

原通用 V1 `research_quality_routes.py` 已有 confirmed snapshot/download，不能写成“原系统没有导出”。永续 V5 reader 的 `bundle_export_status='unsupported_perpetual_version'` 及原测试断言，描述的是旧 generic 审计 bundle 的兼容边界；`/report/source` 先走 `query.read` 是服务端身份/原件鉴真边界。它们不等于 JSON/HTML 自包含阅读报告不可用，也不支持“源码一变化必然409”或所有历史报告已坏的结论。

按 `AGENTS.md` 第19点及 `01_REQUIREMENTS.md` §5.4、T-106/T-207，最小交付是所有实际合格且已发布报告可在原 PC 一键保存完整、保真的 JSON 与 HTML，离线不依赖数据库、后端或外部资源，包含理解本报告所需的正文、目标/条件定义、证据与反证、成本/多空方案及审核。无需每次下载全部后台源码、SQL、会话或任何凭据/密钥；SHA 不冒充可信签名。Q034 阅读副本及 Q043 同 Run 历史 Action 比较投影是现成接缝，仍须真实报告的浏览器下载、脱库打开、双格式一致及重启读取验收；缺失资料保未知，不能用当前 Advice 重算旧报告或升级资格。

## 2026-10-24 接续登记（不改变验收状态）

本段按具名新收据纠正原草稿时点；历史 FAIL、技术 REVIEW/未 ACCEPTED、R30 来源边界及未达硬门全部保留。

- Main 已精确采纳 Q043/Q044 的8生产＋5测试/支持文件（含新测试 package import 修订），但不是部署。真实 Main 新helper/test/support在仅 Main 包根的 fullUnknown 检查为3files/0诊断；旧后端类型债仍在，不能称全后端 strict0。原正常构建已终态通过：2026-10-24T11:38:23.275123Z→11:56:34.955537Z，1091.6803226s/exit0；[正常构建收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q044-normal-build-final/final-receipt.json) SHA `35496acb…ed92e9`，实际 validated receipt SHA `936c0aa8…65f7c`。构建/新17产物 staged 不等于官方 Host、真实 Provider 或金融资格通过。
- 本轮原官方入口 `q043nh01` 已终态失败：12:16:30.946174Z→12:17:21.080424Z，50.1339501s/exit1；API 仍在原45s ready门超时，clone exit0，API log0B、具体原因未知，尚未进入 MCP/Host/Worker/admit。1927pins前后相同、Owned Job清理 errors=[]；[失败证明](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh01/failure-proof.json) SHA `c3cfddd8…a79c4`。不得将原 API-only AV、只读 import 成功或正常构建通过改写为此轮业务成功。
- 原生 `lf01` 单例已失败：Node internal/preload 的路径解析报 MODULE_NOT_FOUND，实际 security guard 尚未加载；Nodeguard日志0、view0、132pins相同、Python denied=[]，[原始终态](D:/DH-q043-lf01/result.json)。私有工具已用实际 launcher 表达式的普通/空格同路径 marker 完成 Node RED→GREEN，这仅证明路径编码，不代表原生研究成功。新的 `pr01` 自12:20:47.065498Z开始运行（Root登记 session50796/PID21472）；截至本段收到的登记尚无终态，不计成功或合格。
- Q033 原5秒合成manual前置仍为历史 FAIL，报价≤5秒新鲜度与原断言不改；其风险保留，但它不是研究P50/P95指标，也不作为独立官方 Host 功能验收的全局前置。
- 接续优先闭合现有原 PC→官方 Host→同 Run 报告/历史 Action→JSON/HTML 实际离线阅读；随后验证原 Worker 在运行中重启/未知 ACK 对账且不重复受理或付费、历史报告重开；再验证原 PAPER 显式确认→真实报价入场→止损/止盈/到期退出→结算与持仓重启对账。不新造界面、默认事实库或替代 Worker。
- 六组合自动＋手动连续14自然日≥90%、四周成熟评分、两类真实事件完整链、至少10笔含退出/恢复的 PAPER、30真实失败录制完整回放仍须分别取得实际证据；原R30的29 request_correlated_unverified＋1 HTTP失败语义匹配不升级为这些最终门已过。

## 来源与证据索引

- F：[完整任务清单](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/TASK_MASTER_LIST.md)、[正式状态](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/STATUS.md)、[Q-005](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/questions/Q-005-roadmap-consistency.md)、[需求与自然时间门槛](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/01_REQUIREMENTS.md)。具名技术报告在同根 `docs/v3/reports/T-001.md` 至 `T-302.md` 对应任务；引用其存在和报告结论，不将任务 REVIEW 升级为原产品验收。
- O：[Main AGENTS](E:/project/study/codex/dsh/decision-hub/AGENTS.md)、[旧 Main STATUS](E:/project/study/codex/dsh/decision-hub/docs/v3/STATUS.md)、[首个增量修复](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_REPAIR.md)、[类型修复](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_TYPE_REPAIR.md)、[构建候选](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_BUILD_CANDIDATE.md)、[native最终完整回归](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_NATIVE_FULL_REGRESSION.md)、[sameRun性能历史](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_SAME_RUN_PERFORMANCE.md)、[单RO聚合](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_MANUAL_READ_SNAPSHOT.md)、[Advice夹具](E:/project/study/codex/dsh/decision-hub/docs/v3/reports/INCREMENTAL_ADVICE_FIXTURE.md)。这些报告按各自时点读；R033 为原草稿采用的历史运行事实，最新接续另见上节，不改写旧报告。
- B：[原后端审计](D:/DecisionHub-v3-development/temp/legacy-reuse/backend-audit.md)、[现成同Run测试更正](D:/DecisionHub-v3-development/temp/legacy-reuse/backend-vertical-slice/README.md)。原后端审计是当时的 source-only 观察，不是当前重新执行或正式验收；其报告源码依赖的过宽推论以 O 和上文精确导出边界更正。
- P4：[CPI私有准备报告](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/reports/T-404-private-preparation.json)。仅 A 部分纯候选，不假称整段事件实现。
- P5：[通知私有准备报告](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/reports/T-503-private-preparation.json)。私有监控/通知资产位于 `D:/DecisionHub-v3-development/monitors`，本草稿不新增其正式集成或实际发送结论。
- P6：[保护前置私有报告](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/reports/T-604-private-prerequisite.json)。范围仅负现金 reduce 前置，不冒充完整保护/结算/事件执行。
- R30：[T-705私有报告](E:/project/study/codex/dsh/.worktrees/decision-hub-v3/docs/v3/reports/T-705.md)。真实30例匹配仅在其明确语义/来源范围内成立，不冒正式阶段过门。
- R033：[Q033运行只读收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/transport-runtime-review.json)，SHA `2cb8ed2c1dda882f1baaf1787482974e268dbedf853d584dee5f46b9e88c2e0c`；原件 `D:/DH-q033-same-run-20261024T0727/`，本轮只读日志/JSON，没有开库。

本表只建立需求到证据/未验项的对应关系。后续若精确复用私有资产，仍按原产品身份和协议选择最小接缝；不能因为有一份独立实现就再建一整套替代界面、事实库或默认 Worker。


## nh06真实终态：Host完成，业务提交失败

2026-10-25北京时间接续：fresh q043nh06唯一运行UTC2026-10-24T16:00:29.613102Z→16:10:00.882766Z，571.269687秒/exit1。原私有新库initialize70.531秒/head0103成功；同Run run_c5f94b84c83c431cb6df0385e0d43b84、session dsh_aa703d32ef76ecedb30714c68bf00d009d916cce7cbaf35c610e1ac5da324611已真实受理。只读冻结原件前置51.547秒，Task原截止16:32:50.908960Z；选定remaining1779.962642秒后未refresh。Worker实际观察460.641秒、admit至终态460.766秒、return1；未到Task截止，未触观察超时，Q046不作性能修复或本轮成功依据。

原collect保存Host finish_reason=completed/last_seq82、Link state=completed/error_code=null/tool_calls_started8及原生最终输出，但产品Run status=failed/research_worker_failed/cause=ValidationError、artifact=null；原assert_scenario:1188确已进入并因run_status非completed失败。coverage sufficient/hardratio1.0/evidence9/tool_failure1不是业务成功。worker日志另有dsh_session_links readset_conflict，仅作待核线索，不未经同录制复现断言它就是根因。下一步用本轮原件精确定位首个异常并做离线RED/GREEN，禁止盲重跑Host、放宽业务assert或拼接ls01报告。

[本轮失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh06/failure-proof.json) SHA 0efedd46a1456a4b5d87754a0d8973f984bab6be6d869192eafbb806c7067d37；1934来源前后相同，normal9fa/final314c保持；6个owned Jobs全部关闭、所属root退出已观测，cleanup errors=[]。这些不是全系统进程清查。nh06队列已释放，原件保全，不重试旧Run。

原PC→永续研究→官方Host主接线源码已存在；V5保存报告不要求新Task或重搭前端。后续沿现成接口修最小实际缺口，generic Host录制不能冒BTC/ETH domain或金融资格。原PAPER writer单例profile仅获一次隔离诊断授权，原五秒assert/报价新鲜度不变；尚未有诊断终态，不计实际PAPER。原正式服务/DB/profile/installed pointers无部署更改，目标ACTIVE，14自然日/四周评分/两事件族/10笔真实PAPER/30例完整失败回放等最终门未达。


2026-10-25最新接续：当前原Host兼容修复报告INCREMENTAL_HOST_COMPATIBILITY_REPAIR.md覆盖nh06真实generated DTO错误的同录制RED/GREEN及物理Main4PASS，另Host v3分支6新＋84相关PASS、局部TSstrict0。23源码已精确采用，旧9fa不能认证新源，新normal准备中。Q033实际5s门仍FAIL、suffix无稳定收益拒采；资源无损压缩只恢复构建空间。56ID/9phase与所有实际金融/自然时间/最终质量门继续保留，不以这两处局部修复升级ACCEPTED或最终交付。


## 2026-10-25 本次原产品新增实证（不改任务验收状态）

两处兼容修复经完整normal构建通过；nh07实际官方Host completed、Worker0并保存同Run研究Artifact，但因缺独审/校准为research_only/degraded，原success断言FAIL。原API与原PC页面已实际显示该Run正文/9证据和限制；完整snapshot409使导出仍不可用，未取得T-106/T-206/T-207/T-209或真实金融验收。新copy仅RO三表为空，当前先捕获读边界异常，不重跑研究或伪造归档。详见[原报告读取实证](INCREMENTAL_ORIGINAL_REPORT_READING.md)；56项/全部最终门保留。


### 原报告读取的最新归因与验收边界

上段409已定位为私有验证guard对规范只读SQLite URI的误拦截，限定修复经真实RED/GREEN与13拒绝负例，不改产品校验。原App uri01生命周期与原PC正文读取通过；实际snapshot404→legacy status200→quality200/unavailable，没有质量记录/导出按钮，未升级T-106/T-206/T-207/T-209。实际单Provider stream连通通过也不等于研究/独审通过。下一沿原签名PAPER command.v3的BTC4h入口准备，不改launcher或重新搭UI。完整RuleReader缺口与允许明确成本假设的单位研究区别对待，真实PAPER执行规则待验；56项/全部最终门与目标ACTIVE不变。完整凭据见[原报告最新实证](INCREMENTAL_ORIGINAL_REPORT_READING.md)。

## 2026-10-25 总控任务收口复核（UTC14:44）

用户询问“主线内容和当初总控的问题与任务是否完成”。结论为未完成，且不仅剩长期等待。原总控execution-contract的T01—T08八个工作包仍登记in_progress；其10/23 paused是历史交接状态，不覆盖用户后来继续执行的授权。新v3总表实际56项/9阶段，正式STATUS展开27项，其中25项技术REVIEW、T-000/T-303仍进行，不能将这些技术状态升级为原产品验收或完成百分比。

| 原总控 | 已有成果或局部证据 | 尚未完成的交付结果 |
| --- | --- | --- |
| T01 组件真实集成 | 原Host/Provider连通、部分真实报告、兼容修复与正常构建 | 修复版本稳定完成同Run研究、独审、发布、原PC及导出；未形成新稳定闭环 |
| T02 运行状态、计时、认证和终态合同 | 合法追加竞态及多个合同接缝已修，本机身份实际可用；原checkpoint/resume源码存在 | 活跃/失联准确区分，进程消失后同Run回收及终态、恢复/未知ACK不重复付费尚待实测 |
| T03 事前时点、关键事件与宏观输入 | 日历/议程、部分来源和候选资产存在 | 多事件族真实共识/Actual/阶段、独立提前准备窗口及统一事件包未完整交付 |
| T04 有依据的条件行动 | 历史BTC/ETH各一份真实独审4h报告、情景核心与受控全链证明保留 | 真实情景p/条件q/原因原件/事前多空方案，原产品24h/72h可用终稿与完整业务验证未闭合 |
| T05 监控、变化修订与可靠提醒 | 周期准入、到期提醒、部分条件监控及私有通知资产 | 可用自动报告、真实条件→通知送达→合格后继研究，以及PAPER完整生命周期未验收 |
| T06 结果、经验与下一次使用 | 历史成熟经验进入真实模型调用已有证据 | 因果使用、效果改善、结算PAPER结果消费、成熟评分/校准/四周报表未证明 |
| T07 原PC用户主线与手册 | 原导航/身份/设置保留，正文可读及阅读导出受控用例，手册候选已提交 | 实际合格报告/建议/PAPER/各期限/事件/监控/复盘及双导出的完整用户流程未验收 |
| T08 正式候选与完整交付 | 源码精确集成、完整normal构建和定向回归有证据 | 最终受核发布、真实整链、恢复/交接及全部硬门未通过 |

actual05新证据：14:43 UTC限定RO查同Run run_19001efca3fd49d5a62883d9770ebec6，业务status仍running，但更新停10:09:59、lease到10:13:59；artifact/error/finished皆NULL。八个owned身份及outer/supervisor进程均不在，旧exec43658也已不存在。DSH最后seen10:09:52、Task截止10:54已过，只有acquiring_evidence阶段及12次工具启动，没有报告/终态/可核退出码。只能记“进程不存在、业务状态滞留、未完成”，不能继续记活跃，也不能从工具错误推定最终cause。DB/WAL查询前后同、changes0/rollback/close；未改库、重启或新研究。[整合只读证据](E:/project/study/codex/dsh/decision-hub/evidence/legacy-sdk-review/btc4h-live-actual05/current-state-readonly-20261025T1444060949644Z.json) SHA `1aa1c961232eace8dcedfabf677c9055e8ac0fc939227680d9542ad406b5437d`。其observed_utc文本经PS序列化为+08，代表与原14:43:17Z相同瞬时，不当作新的UTC采样。

下一优先查原过期租约回收、checkpoint/resume及原deadline拒绝派发的接缝，先复现再依法收尾同Run；不人工SQL改成成功/失败、不续期、不重复提交。六组合14自然日、两类真实事件、条件后继、10笔含退出恢复PAPER、四周评分、30完整失败回放、最终性能/质量与交接均未达；目标保持ACTIVE。

### 过期恢复与历史资料的新增实证（UTC15:16以后）

actual05已从滞留running收尾：两一致副本先实测原Selection截止拒绝/原Worker终态，Root再核原进程/四SHA/Task并备份，通过原精确claim和Worker checkpoint恢复落failed/research_worker_failed，Run与Link终态UTC15:06:34.244982、lease清空。原Task/旧roles/Run集合不变，Host/outbound皆0，原deadline不刷新，备份quick_check通过。它关闭T02的这一例过期失联收尾，不能扩称在截止内恢复、Host重启、未知ACK和整条研究主线已通过，更不能算合格研究；最初进程消失原因未知。实际结果及诚实FAIL边界见[最新原链实证](INCREMENTAL_ORIGINAL_REPORT_READING.md)。

05私有库缺历史Job的前件已补实证：从原archive恢复精确85日BTC daily查询，原adapter先RED；单一原DataJob producer真实Gate HTTP200后保存85条完整日线/3分区/1rawcapture/0gap，原同窗口读回9facts/1evidence。新采集known-time为UTC15:16:18，未回填旧Run；只支持这个具名dataset/窗口的数据前件，不代表六组合、全来源PIT或金融基率已验收。私有Node guard的原子落盘冲突也已原body RED→9项GREEN，尚待新FRED公开采集验证；原PC、Main产品和全部最终门不变，56项/八个总控工作包仍未整体收口。

后续FRED原collector实际WRESBAL采集已HTTP200、20秒内保存成功DOM与receipt，exact Job清理完成；诊断入口首次CJS导入错误已单独保留，未归产品。直接collector尚无Hub received_at，原Python完整宏观解析/多事件/合格研究仍未证明。fresh06只准备新的独立历史前件阶段及原guard局部接线，未发新研究。详见原链实证，不能以这些资料和运行切片把T01—T08或全部56项标为完成。

actual06随后实际在原初始化后的历史准备遇ReadTimeout并于UTC15:55:37终态exit1，没有研究Run/模型；155输入同、四exact Jobs关闭。已有同窗口85日真实archive，因此选择只恢复原Job/RawCapture/五文件历史闭包，保源时间/ID，拒绝整库带入派生运行及待处理设置；新私有工具TDD中，未启动07。该工程资料前件不等于主线/预测质量过门，全部未达项保留。

2026-10-26北京时间再次核对：原execution-contract的T01—T08仍全部in_progress，没有整体完成验收；不仅剩长期观察，还缺当前版本稳定研究/独审/原PC双导出、完整提醒后继、PAPER与恢复整链。固定历史corpus真实RED→10项GREEN，只恢复2原行/5原文件，原reader严格读回85日，原时间不变/冲突和PIT反例拒绝/两处源DB-WAL同；不计业务交付。新07scope40fdb559…784d34已冻结、Root独立163针同，WORK07仍不存在，尚未启动模型。详见原报告读取实证最新节；未升级任何总控或v3验收状态，不给无依据完成百分比。

actual07于UTC16:31:52已由Root持同Handle唯一启动，原初始化及2行5文件历史恢复/85日原reader回读成功且没有历史重fetch。原API/acq/MCP/Host已就绪，真实Run run_3043def3338d461bb733a1c425fae7af于16:34:22受理；原截止17:34:22保持。原PC已正常认证并回读本轮模拟账户设置version1，显示研究进行中。UTC16:39仍活跃/未终态；尚无研究质量、独审、报告或双导出通过，不升级八个总控工作包/最终门。详情见原报告读取实证最新段。

## 2026-10-26北京时间新增：当前版本BTC4h单例闭环已实测

actual07已真实exit0，Root持同Handle观察至17:12:25Z，10exact Jobs清理errors[]/163pins同。Run run_3043def3338d461bb733a1c425fae7af由原Worker保存artifact，原native独审approved，action_publication available并选择维持空仓；原V5研究材料语义仍research_only/degraded、两资格标志false。Root原PC实际看见同Run正文与独审/发表，原JSON及HTML按钮真实下载完成，原Main decoder/组件/导出另对同200body验证通过。HTML内嵌JSON等实际JSON，0活动脚本/外部加载资源；未声称已打开离线浏览器或完整审计bundle。详见[原报告闭环实证](INCREMENTAL_ORIGINAL_REPORT_READING.md)。

这为T01、T07增加当前修复版本单个BTC4h的研究→独审→发表→原PC→双导出证据；此前“尚无当前完整单例”已被本次证据更新。它不升级为T01—T08整体完成、稳定六组合或金融合格。受理到artifact32分40.38秒，单样本不证明P50/P95；维持空仓使实际PAPER新增0。PAPER私有协议工具初版5个审查问题经真实RED/GREEN修复，Root33项离线PASS/peer C0I0M0，但实际UI确认、成交、退出结算与重启仍未验收。事件/后继/14自然日/四周评分/30完整失败回放/最终质量及发布交接等全部未达门保留，目标ACTIVE。Main生产源码、正式服务/profile/data/pointers无本轮部署改动，不重跑07。

### 后续推进：ETH前件、六组合原调用及§4内容缺口

新增ETH日线真实原Job已HTTP200/85日/0gap/3partition/1receipt严格回读，Root同Handle终态exit0，源时间真实不回填；这是数据前件，尚无ETH研究。六组合显式原caller Root28项PASS及非作者C0I0M0，但旧observer/执行包尚未拓展，不称六组合通过。

07尽管独审approved及发表available，原包仍缺§4.4明确保护与观望换选条件，重启阅读未实际验证；不直接加入研究合格分子。V5材料层degraded不能与旧quality path/execution判定硬混；最终六项实证及14日90%保持，阶段T303的80%/20-40不能代最终门。内容微切片D提交80e98ce，Python28/TS21及独审通过，Main未采用、真实模型未验证。原记录6次压缩380.517秒及重复大属性支持下一无损上下文修订，参考字节减少不充性能通过。详情见原报告读取实证及两份新微切片计划/报告。目标ACTIVE。


## 2026-10-26 即时复核：仍未完成总控及最终交付


2026-10-26北京时间即时复核（UTC18:36以后）：原总控T01—T08仍全部in_progress；v3正式STATUS展开27项，其中25项技术REVIEW、T-000/T-303进行中、任务ACCEPTED为0。总表实际56ID/9阶段，不把标称55项或局部测试换算成完成率。当前不是只剩自然时间等待；六组合稳定合格、两事件族、条件通知与后继、PAPER退出结算/恢复、评分消费、完整失败回放、最终质量/发布交接尚未验收。用户要求继续自主执行，目标保持ACTIVE，不从T000重启、不重新搭前端。

新增实际重启阅读：Root唯一report-restart01已同Handle terminal exit0/64.960秒。原07两库只读一致备份到新私有实例，原API单独启动、LLM及业务worker关闭；同owner/同Run唯一报告GET200，read_at之外整个响应全等，source/artifact hash不变，原业务文件和DB/WAL及65输入后验同。原件D:/DH-report-restart01/artifacts/restart-result.json。它证明保留原绝对archive根的同安装API重启阅读；不是可移植独立灾备、全部报告或PAPER恢复，也不补足07缺保护和观望换选条件，07仍不入合格分子。

内容/无损上下文两处微切片已完成D本地提交80e98ce及75d5a97，并按旧SHA/逐字备份/正式端口无监听核验，仅4生产源和4测试精确采用Main；receipt在Ddev/evidence/legacy-sdk-review/research-context-main-integration/adoption-receipt.json，未修改正式服务/profile/DB/installed指针。旧normal6dd不能认证新源，新完整normal尚未执行。Main必要11文件整合回归首次因旧测试beforeAll所需evidence/retained-context-race目录不存在而失败、7项跳过；保留cache及native-race-diagnostic.log。创建既定证据目录后，测试与生产源零改动，Root同Handle观察11文件96PASS/26.88秒/exit0，完整日志native-combined-after-evidence-setup.log。不能把测试目录前件错误当成产品竞态复发，亦不隐去失败。

Observer r2补齐既有context六项及Task三项身份联结，作者56PASS、非作者18定向负例PASS/C0I0M0；仍是离线六组合观察器候选，没有新的真实研究。ETH85日资料与显式六组合caller此前已验证；需要新normal、资料忠实恢复及真实内容验收后再进入六组合实际研究。状态取证及8采用文件逐SHA复核见research-context-main-integration/status-verification-20261026.json。以上不升级原八工作包/v3正式验收，不把单例32分40秒替代P50/P95，也不强迫空仓报告做PAPER成交。


## 2026-10-26 新构建与未完成协议的准确边界


2026-10-26北京时间最新接续：Root唯一content-context正常构建已持同Handle终态exit0。exec50172/outer4920/FT134374271910761229、supervisor28912，UTC18:39:50.9514079Z→19:01:38.6740159Z/1307.7227517秒；原producer内层1304.736秒。UI/Host各一次且exit0，原assessment/retention/完整临时与最终来源校验通过；migrations/process-starts/stops/pointer-writes/model-submissions均0。原installed49c仍为Prior，未部署。新normal=Main/artifacts/incremental-delivery-20261026/content-context-build/product-validated-builds.json，SHA982db6a45689b123a5a8f6cfff61497983af18dbb991235dccfb4989e6603886；native指纹5d4473b86ee871ac57a3b79a4251fd47cb5da81641c8604bcffd6cfa818cc4ff。有限postflight exit0/errors[]，final-receipt SHA578391e1…f027在Ddev/evidence/legacy-sdk-review/content-context-normal-build。31采用文件、43控制、71 native输入、30原源码及588个当前preimage备份逐SHA复核；新4生产源明确存在新receipt并相等。raw source1801/effective60910/artifact48与实际dist+lib586文件是不同范围，不能相加。完整说明见INCREMENTAL_CONTENT_CONTEXT_BUILD.md；旧6dd/a067不再充新源依据，也不冒新normal为完整研究/金融/运行验收。

ETH忠实历史包已冻结在Ddev/temp/legacy-reuse/eth4h-historical-corpus，helper b3d8a32e…bd125/corpus201ef7c1…536d4。真实新私有目标保存12项PASS：原reader先coverage_missing，后85日/原receipt；原2行5file全列/原时间相同，重复0/0、篡改/PK/文件冲突/过早PIT/跨asset拒绝。原测试过程exit未保留，不补造或重跑；Root独立纯验证exit0及非作者C0I0M0有据，不开DB/不再恢复，source DB/WAL当前同保存值，原BTC封包不变。observer r2 Root独立56PASS7.40秒/exit0，5files+13readonly pins同，root-verification-r2.json已保存。它们仍不是新研究或PAPER。

质量接缝已实际定位：V5固定material-only且reader硬校degraded，旧quality模型又是另一套路径/执行资格，不能只加bool统计或继续付费六组合就宣布合格。D计划INCREMENTAL_RESEARCH_COMPLETION_PLAN.md已本地提交8d0de17；TaskA显式typed findings.v2四项独审内容检查（actual_prompt版本选择、防降级、保留旧v1），TaskB新source.v6/完整报告提交状态与原阅读接缝正在隔离实施；金融gate/permission/financial/execution不扩权。Main新normal仅认证content/pooling，尚未认证这些未采用的候选。新completed也仍须实际PC/重启全部六项才计合格，旧07保持不计。没有新付费Run/cohort，保持旧05/06/07终态不重投。

30例回放实源审计也已保存：Ddev/evidence/legacy-sdk-review/replay-delivery-audit/audit.json SHA dfede3fb…65056、audit.md SHA ffc31ec8…a175。385个直接原件齐全，29例119observations仅关联错误语义，另1例4次HTTP交互；未跑原Main内部业务/完整native/PIT，旧30/30不能代最终门。可复用原ReplayResearchRuntime/严格传输及05终态前双库恢复，但新完整回放driver尚未实现，最多先证明一个真实失败边界，不能重复计次数或伪造缺失输入。全56项/八个总控工作包及全部最终门继续保留，目标ACTIVE。


## 2026-10-26 最新状态：总控未完成，当前历史回读缺口待修复

2026-10-26接续真实状态：原总控T01—T08及最终交付仍未完成，不是仅剩自然时间等待。正式STATUS的25项技术REVIEW不等同原产品25项已交付，ACCEPTED仍0；56ID/9阶段的全部业务范围保留。TaskA显式findings.v2已D提交327b6eb、非作者C0/I0/M0，TS97PASS/Python38PASS且保留1项已证旧Main失败deselect；旧Python strict76→70是零新增，不是整体strict0。TaskB新V6原native合成child两例均原提交/原reader成功，尚在最后类型/peer收口。Root原PC候选3files77PASS/12.82s与原UI tsc exit0/0诊断，6文件含两份原服务fixture逐字SHA相同，原组件/导出未改；scope4c5af4d2…373c。以上A/B/PC均未采用Main、未部署，无新真实业务/付费Run；不计六条件合格。

已实测暴露历史归档缺口：当前Main+982原SavedReportQuery在新私有一致副本读取旧07失败research_graph_recipe_current_implementation_unavailable，34条BEGIN/SELECT/changes0、原restart01三文件SHA前后同。05/07所需manifest原字节同7b7d19e3…921e，现有51e00f40…a433是不同实现（420源中6份不同），不得替换。真正481份旧源已从480份匹配Main和1份确切profile备份鉴真保留；将经合法原producer补归档并原reader回读列为采用前必须修复项。此前旧版本同安装重启成功与当前版本失败分别保留，下载JSON完好不能替代当前在线兼容。原证据在Ddev/temp/research-completion-legacy-readback-01。

新的05私有备份回放Root同Handle实际UTC19:16:47.6841239Z→19:19:22.9090941Z、155.2251399秒/exit1。虽然原Worker落failed/lease清空、源六文件SHA同且无业务外连，实际先被上述缺历史实现拒绝，未观测原deadline异常，因此replay-validation.passed=false，不算完成30例中的一例，不改预期、旧Task或原05。该FAIL及新归档修复并不暂停整个目标：继续隔离补正确归档、完整研究协议与原PC接线，六组合稳定性、事件/提醒后继、PAPER退出结算恢复、四周评分、完整失败回放、最终strict/发布交接仍须实际完成。目标ACTIVE。


## 2026-10-26 四切片已采用Main并通过必要联合验证

2026-10-26接续：四切片已非作者C0/I0/M0并D提交TaskA327b6eb、TaskB1caa6b3、原PCc9bbe15、归档66bad52；Root按19旧SHA与逐字备份/14新文件absence/正式端口空闲核验，精确采用Main33files，无Main Git、正式启动、DB/profile/installed pointer写或新模型。adoption-receipt SHAa704566f…ac3a在Ddev/evidence/legacy-sdk-review/research-completion-main-integration。真实Main联合验证三组终态exit0：Python70PASS25.25s、PC77PASS11.95s、native15files97PASS53.57s；33源后验同，main-focused-verification SHA cb30ce59…dc3。旧React act警告、最初repo根无Vitest的0test前件失败均保留，未安装依赖；这不是实际新研究或最终strict0。

真正7b7d已由原producer生成481原字节归档，并通过原175注册完整后验与现成union得到176；首次driver在producer成功后因普通Windows长路径读失败exit1，纯continuation复核现有产物exit0，未重复producer。原物理7b7d视图Query新一致副本已exit0/1113条仅BEGIN/SELECT/changes0，17原字段及source/artifact UTF8字节全等、原源文件SHA同，只有read_at新时间排除。仍须真正新normal后验证当前Main/archive分支，不能用原视图成功冒充当前兼容。542份真实6dd输入及48原artifact齐备，standard normal准备以6dd completed prior+显式opt-in原auth链生成新收据，installed49c不变；旧982不认证新33源，当前尚未开始该新build。

下一先完整normal及当前历史回读，再释放新真实研究。并行Phase7仅D原链增量：已定位原forecast/runtime/reader与PC硬4h、241分钟/6页限制及旧方法源码hash，保旧四software输入字节不变，新增显式v2跨期限truth/评分/read与原PC接线正在TDD；不得把纯32PASS、合成SQL或未来原件当四周实测。研究六条件/14自然日/事件/提醒后继/PAPER完整退出恢复/四周评分/30完整失败回放/最终strict和发布交接全保留，STATUS无新ACCEPTED，目标ACTIVE。详见INCREMENTAL_RESEARCH_COMPLETION_MAIN_INTEGRATION.md。


## 当前唯一正常构建已启动，尚未终态

覆盖点58的“新build尚未开始”：Root唯一完整normal已于UTC2026-10-25T20:37:22.7785193Z启动，exec47376，outer22160/FT134374342429091824、supervisor11764；inner原builder21100/FT134374342464127151。prepared8e7d232b…7d022，71controls/71native/61current adoption/542prior originals/588fresh当前产物备份已Root独立全SHA核，原10GiB内存与child8GiB门保留。UTC20:42:33.7318583Z再次轮询同handle仍RUNNING，两PID创建时间身份实核相同；没有终态或成功结论，禁止因无日志/观察超时重新启动。实际scope/started/root-live-observation在Ddev/evidence/legacy-sdk-review/research-completion-normal-build。后续必须继续47376，真实终态后才运行有限postflight.py并传实际47376；当前Main/archive旧07回读包另在准备，未执行。D新Phase7不采用Main；其首轮默认4GiB完整tsc已自然OOM/exit134，已保原日志，最终8GiB复核待本normal终态，不能冒通过。目标ACTIVE。


## 2026-10-26 新normal终态及当前历史归档回读通过

覆盖点59运行中状态：Root唯一normal47376已同Handle真实exit0，outer22160/FT134374342429091824，UTC20:37:22.7785193Z→20:59:32.1308297Z/1329.352451s；UI/Host各一次exit0，原assessment/retention与完整临时/最终guard通过，迁移/启停/pointer/model全0。真实新normal4511e6c6…4de4f，native d4b1c07c…e699；有限postflight exit0/errors[]，final-receipt57519b0c…a4fbd在Ddev/evidence/legacy-sdk-review/research-completion-normal-build。raw1807/effective61398/artifact48/dist-lib607是不同范围。installed仍49c，已完成6dd是显式合法prior；原standard producer已捕获真正7b7d并保175→176注册，未部署。

当前Main/archive兼容已实际闭合：最初79199 guard0、原Query已返回，但私有观察器将合法Windows长路径前缀误判，exit1原件及Target SHM变化保留。Root审新纯路径TDD/完整冻结后，仅新增observer85993实际exit0；不重build/guard/copy。当前Main manifest51718c50…5ae0不同7b，Query前无旧reader/后真实加载7b，255物理file逐SHA=payload=index，17原字段及两个JSON UTF8字节全等，1142条仅BEGIN/SELECT、session changes[0,0]，Source三文件/Target DB-WAL/normal前后同，financial/execution仍false。证据D:/DH-report-current-archive01/evidence/query-result-r2.json SHA6c86fcb7…09211、observer-r2-terminal7e549c79…8570。旧07不升合格，不推广全部历史/独立灾备/PAPER恢复。ETH08包可绑定4511/d4及此proof，但须Root最终scope审读后唯一释放；当前还无新付费Run。

后继维护链source-only已确认：当前H1 prior=6dd、installed49c，下一次不能再拿6dd重捕7b而漏真实51718。B仅D独立候选做bounded completed-chain完整逐跳鉴真，Main/66bad/新normal/Query证据均不改。Phase7仅D候选继续原链评分/PC，原native测试前三次分别为新harness类热装载、异步loop、strict JSON模式错误；第三次实际V6 native+Commit已完成，后续按原件新副本continuation继续，不冒原整测exit0或真实样本。所有业务/质量/自然时间硬门与全56任务范围保留，目标ACTIVE。详见INCREMENTAL_RESEARCH_COMPLETION_BUILD.md。


## 2026-10-26 ETH08 最新真实终态：业务未通过

本节覆盖早先“无新付费Run/ETH尚在准备或运行”的时点。原Main4511/51718/d4的唯一新ETH4h Run7093…6b6b已结束：Worker0/outer93659实际exit0，但业务degraded、DSH failed/native_context_invalid；source实际v3，未形成可读V5/V6永续报告。Root唯一报告GET409，无组件导出、实际下载或重启读验通过。5精确OwnedJobs闭/errors[]、151pins同；两库及4SDK文件关闭后原字节保全在D:/DH-eth4h-replay-source01。

原SDK370逻辑events、v6 marker与3child prefix窄门已纯回放；完整原生产verifier实际RED于substantive equality，14Evidence/31Facts与fresh15/35仅新增集合不同，正在核异步读取与冻结边界。诊断脚本exit0不等于业务校验PASS，不计30例、不重投。D截止恢复d32eb150另有mapped29PASS与库存10PASS，尚未采用Main，也不是本次native首错的修复。

[本次完整结果](INCREMENTAL_ETH4H_V6_LIVE_RESULT.md)保留实际身份、终态、原件SHA和证据边界。原八工作包/全56任务、全部金融、自然时间、质量及交接门仍未完成，目标ACTIVE。


## 2026-10-26 主线51文件已实际采用，完整构建运行中

此前Phase7/bounded/deadline/order“仅D未采用”的时点由本节更新：Root已精确采用51并完成必要Main联合验证，完整synthetic V6提交、发布、到期和后续读取1PASS；原PC108PASS保持原页面/原断言。原native首次164P9F及独立baseline9PASS分别保留，不能冒173单轮全绿；其他三个装配/等待失败的原件亦保留。当前仍无新的真实合格研究，ETH7093不升级。

真正H1 51718捕获66040实际0，原176+新1/union177已在隔离产物验证，未称Main完整177读取。新normal session73226已实际01:11:22Z启动，prior4511/H1、原8GiB/10GiB资源与完整source/artifact检查保留；preparedada10466…2b86。只能接续原handle，不因无输出重启；现无构建终态/新normal/部署结论，installed49c未改。控制书不属于本次源/120controls，因此只同步本说明、不动业务源/输出。

[本轮采用、真实摘要、失败与运行身份](INCREMENTAL_PHASE7_RECOVERY_MAIN_INTEGRATION.md)列出全部证据。PCE纯解析D提交及后续Actual候选不属于本次Main51，未取得真实事件闭环。全部56任务、原八工作包、14自然日90%、两族、提醒后继、PAPER退出恢复、四周评分、30完整失败回放、最终strict/Ruff和发布交接仍未完成；目标ACTIVE。
