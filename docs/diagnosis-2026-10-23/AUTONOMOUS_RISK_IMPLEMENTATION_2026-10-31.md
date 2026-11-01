# 全天候风险链第一轮实施计划

> 执行者：使用 subagent-driven-development 分工实施；每项先失败测试、实现、必要回归、独立审核。用户已授权常规决策自主推进；不再请求例行审批，不commit/push。

**Goal:** 修复可重现的通知通道/反向告警、DEV supervisor恢复、概率来源透明展示三处真实主线缺口。
**Architecture:** 复用现有行情、RiskMonitor、Outbox、永续研究与原前端；OS任务只负责恢复官方DEV生命周期。历史报告、原件哈希、现有风险阈值与金融权限保持原语义。
**Tech Stack:** 现有Python/Pydantic/SQLAlchemy/pytest，TypeScript/React/Zod/Vitest，PowerShell7/Windows当前用户任务。
**Spec:** E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/AUTONOMOUS_RISK_DESIGN_2026-10-31.md

## Global Constraints

- 原Main目录 E:/project/study/codex/dsh/decision-hub 实施；当前未提交成果大量存在，新建基于旧HEAD的worktree会丢上下文，沿用用户指定目录并逐文件留前像。
- 不完整发布打包、不改正式库/历史原件、不删除数据、不commit/push、不真实交易；不新开付费循环，不伪造owner consent。
- 测试/日志在D盘全新短目录；不得复用会清理已有内容的basetemp。CODEX_LOG只追加。源码改前记录根因/范围；研究运行时冻结源码。
- 新Python模块≤400行、函数≤60行，新增helper Ruff/strict零错误。老模块既有诊断独立报告，不冒全仓绿。
- 失败日志落盘，记录exit和最后40行；保留首次失败。测试按实际改动选择，不重复原1030/250跨版本大回归。

## Review Focus

- 暴跌后反向暴涨、反向镜像、零/未知/陈旧数据及阈值震荡；不把每tick变成新告警。
- 邮件配置下不得再入local通道；只验适配器接受，display/ack仍是独立送达层。
- PID复用、身份未知、孤儿、并发、用户停止、故障循环不能误杀/抢占或无限重启。
- 同Run基准缺失、旧响应、未知概率、错horizon/引用和篡改不得静默补当前数据。
- 概率接近或等于基准不自动代表有/无专业调整，不能冒充预测增益或做空胜率。

## Task 1：风险通知通道及剧烈反向再提醒

**Files（以实际依赖闭包记录必要调整）:**

- Modify: E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/risk_monitor.py
- Modify: E:/project/study/codex/dsh/decision-hub/apps/hub_worker/composition.py
- Modify: E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/perpetual_research_drivers.py（若实际位置不同，先记录真实路径）
- 必要传播点：既有perpetual_plan_followup/condition构造器；不改变授权或研究策略。
- Create tests: E:/project/study/codex/dsh/decision-hub/tests/market_risk/test_risk_notification_configuration.py、test_risk_direction_reversal.py。

**Interfaces:** RiskMonitorService新增兼容默认local的channel参数；从运行配置贯通现有watch/driver通知通道。默认abs且gte/gt正阈值规则中，已确认、fresh、超阈值的相反方向变化形成新事件；关闭旧episode，复用新evaluation唯一ID及事务Outbox去重。其他规则/未知/陈旧不得触发反转。

- [ ] 写并实际运行RED：选择email仍收到local；-2%→+2%及镜像只生成第一次提醒；同向持续不重复。
- [ ] 实现最小变化；若反转前提未被现有模型支持，停在已证通道修复，不编造错误行为。
- [ ] GREEN并验Outbox失败回滚、重投去重、原确认根数、原风险快通道；不实际发邮件。
- [ ] 独立审查与前后差异记录，不将provider接受当用户已收到。

## Task 2：官方DEV supervisor外部恢复

**Files:** 新增 E:/project/study/codex/dsh/decision-hub/infra/dsh/ensure-dev-running.ps1、install-dev-watchdog.ps1；新增 E:/project/study/codex/dsh/decision-hub/tests/dsh_native/test_development_watchdog.py；仅必要时改现有status/helper，先记录理由。

**Interfaces:** ensure入口使用官方observed状态和进程出生身份；仅本人服务树确证全absent且无监听冲突时隐藏启动官方run-dev。未知/孤儿/活实例不抢占，不按裸PID杀进程。每root跨进程互斥；失败留下状态、有限次数/退避并终止，显式停止可暂停恢复。

- [ ] 先写RED：旧running但全absent、在线不重复、身份未知/PID复用、并发只启动一次、用户暂停、达到失败上限。
- [ ] 实现与干跑任务定义；当前用户、有限权限，不收集密码，不提升权限。任务登录触发和周期检查，不能冒称未登录启动或睡眠期间运行。
- [ ] 独立审核后注册并验证一次官方DEV恢复/在线不重复；权限失败如实记录，不换高权限绕过。
- [ ] 启动后现场验证worker/API/风险数据状态；24自然小时、重启/睡眠恢复另列待验，不冒已完成。

## Task 3：同Run基准率与模型概率透明展示（P6局部）

**Files:** 既有 E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/perpetual_advice_query.py；新增同目录perpetual_forecast_basis.py；既有 E:/project/study/codex/dsh/decision-hub/apps/decision-desk/src/api/perpetualAdvice.ts、src/product/PerpetualAdvicePanel.tsx；必要新小组件/测试按准确路径记录，不重做前端。

**Interfaces:** 在原Advice已认证的同Run research_materials及方向分布上生成可选forecast_basis只读投影，保留旧记录兼容；模型/基准原概率、最大绝对差（百分点）、同一窗口/来源说明；精确相等只显示“与历史基准率相同”。缺基准保持null/明确未知，不重算历史、不推断概率获利优势。

- [ ] RED覆盖有/无冻结基准、相等/接近/明显偏离、未知方向、旧响应、错Run/horizon/引用拒绝。
- [ ] Python→API→strict Zod→语义校验→原Panel完整消费；所有owner/settings/target/hash校验保留。
- [ ] 局部Python/TS测试、类型与必要原建议回归；无需完整发布构建。
- [ ] 独立审查；P3精确调整声明及双端审核仍未完成，不通过自由文本猜测冒作完成。

## Task 4（用户补充“不空转/不重复抓取”）：同小时历史采集复用

**授权补充:** BTC/ETH、模型费用不设上限、效果优先；仍须避免无意义空转/大量重复获取。费用无上限不自动取消现有并发/次数/失败保护，也不授真实交易权限。

**Files:** E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/perpetual_research_entry.py 的collect_history及构造器小接线；新增同目录perpetual_history_cache.py；新增 E:/project/study/codex/dsh/decision-hub/tests/decision_product/test_perpetual_history_cache.py。

**Interfaces:** 每Entry/绑定source隔离的、有界成功缓存；键含历史adapter版本、instrument及原始小时cutoff。单进程并发single-flight只发一次真实采集，原capture_ids/as_of逐值复用，仍由原reader核档/校验；小时边界失效，跨边界完成的采集不memo。None/异常/取消不伪成功；注入/回放collect_window仍完全不联网。不能直接拼接不同时刻旧90日页，也不伪造新的采入/观察时间。

- [ ] RED：同小时顺序/并发产生重复采集；不同币种/Entry不混；小时变化重新采、原时钟/IDs不改；失败与取消不留成功memo。
- [ ] 最小实现，保护跨event-loop调用（现有worker含线程/asyncio.run），一个等待者取消不取消其它等待者；生命周期不保无限pending。
- [ ] 原件损坏继续被原reader拒绝；已有注入collector测试不触网。单元及必要history原件回归/新helper严格静态，最终来源身份测试在全局冻结后执行。
- [ ] 独立审查，再统一源码启动；只声明同进程同小时减少采集调用，跨进程/重启及跨小时增量未做，不宣称整轮研究已缩短到某分钟。

**自动业务启用预检后的裁决:** 原有V3 continuous watch虽支持typed条件/失效与expiry复查、pending successor合并，但当前不能直接启用。真实两settings是user_declared且仓位unknown、无paper账本；V3只允许CurrentPaperPartition，不能改字段假装paper。两旧plan无任何typed entry_condition且已过期，开启也会slot_missed。更关键的是expiry使用revision.valid_until（服务器准备/发布许可期限），并非专业市场判断寿命：旧41分钟研究发布后只余约18分钟，直接开启会再入小时级深研空转。先设计新版本化research_only政策，将许可过期一次提醒、forecast/独立reestimate due、有意义新条件分别授权和消费；不延长旧许可、不改旧V1-3含义。宏观event-only仍依赖active schedule且仅CPI/NFP/FOMC，安全/RiskAlert/仓位变化尚未接入。

## Task 5（用户选择邮件）：本机安全持久配置与官方启动传播

用户已选择邮件预警，通过本机配置邮箱；不在聊天收密码。现有SMTP适配器与Task1通道贯通继续复用。当前源码仅有环境变量配置、未找到前端SMTP表单；不能把一次shell临时设置当作重启后仍有效。

**Files:** 新增 E:/project/study/codex/dsh/decision-hub/infra/dsh/development-email.ps1、configure-dev-email.ps1；infra/dsh/run-product.ps1仅DEV构造child env的小接线；新增 E:/project/study/codex/dsh/decision-hub/tests/dsh_native/test_development_email.py。不得改Task2交接/锁、真实SMTP适配器、全局正式配置或旧凭据。

**Interfaces:** 配置保存在验证过的DEV runtime内email-settings.json，owner-only ACL，密码采用本机当前用户DPAPI/PowerShell SecureString加密。交互式本地输入，不接受明文密码命令行；读取后仅在内存/子进程环境向原PilotSettings传原有SMTP变量，日志只输出配置存在/验证状态，不打印secret。缺文件保留旧默认；文件存在但损坏/不可解密/不安全路径不得静默降级local。禁用须明确为local，不改变原Llm credential文件。

- [ ] RED后实现：真实DPAPI往返、文件不含明文、配置只投射固定SMTP/通道key、CLI检查不回显secret、跨用户解密失败/无效文件失败关闭、禁用不误启邮件；注入/linked路径拒绝。
- [ ] 官方DEV child env真实受控执行验收，不只检查源码字符串；Task2新handoff/非DEV原路径保持。使用dummy凭据，禁止实际发邮件。
- [ ] 本地配置命令/安全验证与再启动步骤成文；实际收件地址/SMTP由用户本机填写。需要真正发测试邮件时先核指定唯一收件人已配置，区分SMTP接受、退信与用户确认，不宣称模拟已送达用户。
- [ ] 独立审查新增secret/path边界后再合并源码启动。实际配置前保持“邮件待配置”，不把local日志当完成邮件接通。

## 后续业务阶段（未实现）

1. P3：明确调整声明、有效非baseline证据、双端/最终消费者同规审核；2pp/15pp按原计划边界验收。
2. 市场首轮专业盲判，再与仓位结合；已有主反方/独审继续保留，不把角色投票当概率。
3. 分钟跨资产/衍生品/安全事件输入和既有RiskAlert→永续再审桥接；隔离永续条件观察的准入prepare等待，保授权、预算、幂等。
4. 真实时序与反事实回放分开，上涨/下跌/反转/安静窗对照，记录时延、漏报/误报、实际送达及含费用纸交易结果；未来自然时间/金融验收不得预填。

## 执行记录

各任务进度与实际文件/命令/首次失败/最终结果追加CODEX_LOG及独立任务报告。完成三个局部切片仍不代表完整产品、全天候、事前预测或长期盈利已经验收。
