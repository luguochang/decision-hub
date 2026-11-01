# BTC/ETH 4h 真实研究恢复与需求核对 Implementation Plan

> 执行方式：subagent-driven-development。先写排查记录，再复现、修复、审查、自测；进度追加到 CODEX_LOG.md。用户已确认本计划的目标和顺序，继续自主执行，不重复等待计划审批。

**Goal:** 在独立开发环境恢复 BTC/ETH 4h 研究，逐项完成 VERIFICATION.md §4 验收，查清实例退出；同时形成多事件需求、状态、测试基线与 PAPER 落地对照。

**Architecture:** 复用现有 API、worker、DSH 和原 Decision Desk；依据失败原件定位具体责任层，仅做有回归证明的必要修改。真实研究冻结源码，BTC 后 ETH 顺序执行。

**Tech Stack:** 现有 Python 3.12 / SQLAlchemy / TypeScript / Vite / PowerShell 7，不新增依赖。

**Spec:** HANDOFF_TO_CODEX_2026-10-28.md、DEVELOPMENT_WORKFLOW.md、VERIFICATION.md、PROBABILITY_BASE_RATE_PLAN.md，以及用户本次明确的“不打耗时发布包、全过程留文档、多事件需求核对”要求。

## Global Constraints

- 本目标不运行 build-product.ps1 或正式 run-product；不以完整发布打包作为功能验证前提。源码服务/Vite验证，必要局部插件编译须先说明范围并记录耗时。
- 不写 core-live-20260913、dsh-live、product-local，不冒充正式回执；不删历史、不 commit/push、不改 Git 配置。
- 使用当前 Main 的真实未提交源码。旧 HEAD / D 工作区不是完整当前源；复用本次明确指定的工作区，修改前保存精确前像，不回退无关变更。
- 不打印凭据、pairing token、DSH 认证 URL。DB只读取证使用只读连接，不初始化应用或迁移数据库。
- 研究期间不修改源码；新代码先测试复现，再最小修复和独立审查。付费失败不得无诊断重复运行。
- 不默认通过加大超时、删研究材料、削弱审核或提高资源阈值制造完成；具体裁决必须有证据和代价记录。
- 新 Python 文件 Ruff / strict 零新增；旧修改文件对任务前像比较，不虚称全仓零错误。
- 事实、推测、失败、SKIP、未执行分别记录。日志落盘，主记录仅关键摘录与路径。

## Review Focus

- 约 60 分钟是总时限、单次provider时限还是host读取问题，不能仅凭同长度推定原因。
- 模型/工具实际已经完成而host状态读取不一致；修复不能接受错run、错generation或过期原件。
- 实例退出与PID残留分开取证，不能因状态文件running认定进程在线，不能误杀复用PID。
- P3审核新原件必须经过真实独立审核；服务ready和隔离fixture不代替真实报告。
- 日历项存在不等于事前概率、独审、发布与事后核验完成；不能将CPI覆盖算成多事件全覆盖。

## Tasks

### R1：两个失败 Run 的完整时间线（A1）

**Files:** 只读开发库、`runtime/logs/20261028T041544656Z/`、相应DSH原件及责任源码；输出 `artifacts/incremental-delivery-20261023/d28-research-recovery/timeout/`。

- [ ] 保存只读取证方式、最后成功模型输出、失败阶段、错误链和超时定义文件:行号。
- [ ] 区分模型等待、工具等待、host读取、审核/终稿阶段；判断是否有概率或报告。
- [ ] 形成排查报告：原件、已证原因、推测、最小方案、未核对项。源码修改尚不授权给取证任务。

### R2：实例退出与陈旧状态（A2）

**Files:** 只读进程元数据、端口、Windows事件与现有观察器/启动停止日志；输出 `.../runtime/`。

- [ ] 复核PID+创建时间、端口、退出/休眠/重启/控制端结束证据。
- [ ] 确定退出原因能证到哪一步；缺失记录明确标未知，提出最小可测修正。
- [ ] 后续修复前另列精确生产文件和测试，不直接覆盖旧running证据。

### R3：需求和多事件覆盖（用户新增核对）

**Files:** 总控T01—T08、当前前端/事件登记与预测链；写 `EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md`。

- [ ] 列需求中的事件族及当前展示/采集/事前预测/独审/结果跟踪证据，覆盖CPI及其他明确事件需求。
- [ ] 分别记录已实现、仅日历、未接预测、未真实验收、已失败，不能用旧文档完成标记代替证据。
- [ ] 给出后续实现优先级与通用研究恢复的依赖；不据旧长期方案启动大规模重建。

### R4：状态、基线与 PAPER 对照（A3—A5）

**Files:** 三份状态文档与PAPER计划，写 `STATUS_RECONCILIATION.md`；已有pytest基线/新完整基线落 `.../baseline/`。

- [ ] 对照状态与PAPER期限/512代游标实际落地，附文件:行号，保留原状态文件。
- [ ] 先归档10-23既有基线；新全量测试分配独立时段，不与真实研究争用资源。
- [ ] 列明每个失败名/类型/摘要/源码mtime、是否已有证据；未完成全量不能声称全量绿。

### R5：按已证根因最小修复与必要自测（B）

- [ ] Root审R1/R2原件，先将精确修改文件、接口、负例与复现测试追加至本计划及诊断记录。
- [ ] 对失败做受控RED测试，保存前像，最小实现GREEN；旧来源、原件、独审、时限语义负例继续拒绝。
- [ ] 相关回归、scoped静态检查、非作者审查通过；新增失败如实列明，不扩大重构。
- [ ] 源码开发启动验证API/DSH/worker/owner与稳定运行/停止状态；无需正式发布包。

### R6：真实 BTC、ETH 验收及复盘（B）

- [ ] 源码冻结、记录身份与运行参数；先BTC，成功或完成具体归因后再ETH，不并发盲跑。
- [ ] 每run记录开始/结束、终态、基准率/最终概率、审核引用、basis关键原文和错误码。
- [ ] VERIFICATION §4逐项判定：1基准率输入；2研究走完；3独审引用；4概率偏离依据。旧两run第2为失败，第3—4缺证据。
- [ ] P4幅度来源、P5到期对照分分开记录，不混入四项标准；尚未到期不标已验证。
- [ ] 更新单一基准率进度源与追加执行日志，给出剩余C/D/E需求清单，目标仅实际达成才完成。

## 初始裁决和记录规则

| 项 | 裁决与原因 | 代价/边界 |
|---|---|---|
| 工作区 | 用户指定Main，已包含大量真实dirty源码，复用且逐文件前像 | 不新建缺源码的HEAD克隆，不自动提交 |
| 执行授权 | 用户本次已确认开始和定目标 | 正常可逆修复/测试/开发启动继续，不再逐项问；不可逆操作不在授权内 |
| 发布 | 本轮明确不完整打包 | 研发通过不代表正式发布通过，D留后续 |
| 验收矛盾 | 已知failed不能将“研究走完”写为无法判定 | 将旧run第2记不通过，旧文字保留追加纠正 |
| P4 | 原件分位数/幅度核对，不据末尾歧义改action_comparison公式 | C作为后续收尾，不扩本轮金融模型 |
| 需求 | 多事件覆盖核对纳入当前目标 | 没有对未接入事件承诺已有预测；实施范围依实际账本另列 |

原件主目录：`artifacts/incremental-delivery-20261023/d28-research-recovery/`。各诊断任务只写各自子目录，Root统一追加CODEX_LOG和进度，避免并发覆盖。

## R5 精确修复任务（已读原件后的裁决，UTC 2026-10-28）

### R5a：独审模型输入无损去重

**原件/原因：** `timeout/diagnosis.md`、其只读提取脚本与safe evidence。BTC已证独审及后续父模型context overflow；ETH无overflow原件，独审剩余仅约84秒。审核输入655949/674161字符；9条fact重复同一execution和strategy receipt，另有JSON字符串再编码。两run均有概率草稿及direction_base_rate独审输入，未形成正式报告。不是缺少全部模型输出，也不支持给两run都判同一overflow原因。

**Files:** Modify `extensions/dsh/decision-hub/src/perpetual-synthesis-protocol.ts` 的模型prompt消费点；Create 必要的单一模型视图helper `review-evidence-model-view.ts`；Test 对应`review-evidence-model-view.test.ts`，及原protocol/reviewer/gate相关必要回归。`final-review-gate.ts`的权威digest生成、所有Python/contracts、认证原件部分、时限与总量阈值保持不变。

**Interfaces:** 输入为现有已生成evidenceDigest字符串及当前review packet的run/generation身份；输出仅为模型可见结构化JSON视图。同一条相同receipt原文字节保存一次，以SHA256内容寻址表+逐fact引用表达；原fact字段、顺序、source/ref、完整原文及已存在omitted计数必须可精确重建。真实frozen request和authenticated originals段不变；prompt本身哈希绑定新实际bytes。

- [ ] 在真实旧输入及小合成fixture上RED：当前重复9次且二次编码；不运行模型。
- [ ] 变换不修改原输入；普通无重复/空digest有兼容行为；未知非JSON legacy digest不得猜造事实。
- [ ] 全fact+receipt往返精确、哈希/缺body/跨run代换拒绝；恶意字符串仍是DATA，不获得工具或发布权限。
- [ ] 真实旧输入本地只读取证，对比模型prompt视图大小，不把字符当token；保持每个selected original及direction_base_rate可见。
- [ ] 原protocol、gate、reviewer身份/负例测试、类型检查和独立审查；必要插件局部编译只做一次，无完整发布包。

**起改门：** 当前目标相关RED已在活动旧实现上捕获，前像保存。Root允许以下TS/PS窄改继续，Python/schema保持不变，不让1030项诊断扫测阻塞修复。全量扫测若跨TS/PS改动，按实际版本标记为跨版本诊断清单，不能用其声称稳定的改前/改后全套对比；改动验收靠冻结候选后的相关回归/静态/独立审查。修复成功不等同ETH时限问题已解决，真实验收继续按R6。

### R5b：开发实例状态与停止回执

**原件/原因：** `runtime/diagnosis.md`和events-bounded.json证明18:18附近新boot与异常关闭事件；6008时间与文件mtime有冲突，具体触发/精确退出仍未知，不能说已修系统关机。现代码finally/stop确实不落终态，running快照会残留。

**Files:** `infra/dsh/development-runtime.ps1`、`infra/dsh/stop-dev.ps1`、`infra/dsh/run-product.ps1`的DEV专用分支；新增只读`infra/dsh/status-dev.ps1`；tests/dsh_native中对应开发状态测试。默认正式分支、共享auth/进程实现不扩大修改。

**Interfaces:** status区分recorded_state与observed_state，按PID+创建时间和实际来源/实例归属判断；unknown不可当dead、其他进程占端口不当own。status只读不修改旧running证据。正常停止只有验证所有owned树结束后才能落stopped回执；无法核实保留unknown/残留明细，不伪称成功。保留launch_id、观测时间与最小生命周期记录。

- [ ] RED：陈旧running、PID复用、权限unknown、supervisor已死但owned child仍在、其他进程占端口、正常stop及强制清理失败。
- [ ] DEV专用finally/stop终态证据，不自动恢复/重跑付费研究；不做管理员系统服务/电源/开机自动化。
- [ ] 正式行为未变、只读status不改文件、正常owned进程stop受控验收。
- [ ] 源码改动须Root通知，当前已允TS/PS窄改且Python/schema不变；记录前像与相关RED/GREEN，现场不重启机器复现。

本次两任务共享的仅是开发启动入口与最终现场验证；TS helper与PS脚本可分别实现/审查，真实研究前统一冻结源码。若需突破文件范围，先给具体阻断并追加裁决，不顺手重构。

### 补充裁决

- generation在现prompt构造API没有字段；原final-review-gate已拒绝generation!=1。模型视图绑定现有run_id和完整reviewPacketRef，保留原上游generation门、跨packet拒绝和无跨调用缓存测试，不添加伪造的固定generation权威。
- supervisor自身finally无法证实自己已退出：只可确认children_stopped并记exiting；外部stop观察者确认全树消失后才记stopped。强制关机未写终态仍由只读status区分陈旧记录与现场事实。
- 全量诊断扫测仍属A4已授权核对，不当主线修复前置；会保留覆盖变更的时间/前像边界，不据跨版本扫测判旧失败已存在。只要需发真实研究，先安排扫测与模型资源独立，必要时分段记录后继续，不能把中断写全量完成。

### R5c：纠正开发模型容量声明，保留全部 provider/业务校验

**新原件：** `timeout/capacity-evidence.json`：BTC独审原生provider response是stop，input=214299、output=5040；pi-ai本地isContextOverflow因home声明128000将正常返回改判失败。不是远端明确报错。GET同route `/models`200只给模型ID，无capacity，不能据此虚构最大值。Root只读对照formal/dev：provider codexai、api openai-responses、baseURL和模型完全相同，formal已有声明262144/maxTokens8192，开发初始化硬编码128000/8192。formal声明本身也不等于远端最大值。

**裁决：** 修正我们初始化器引入的元数据偏差：只继承同route/model现有contextWindow声明，保持output=8192、provider错误分类、业务deadline、证据与所有资格门。已真实成功的214299输入证明旧128k导致假失败；不将262144称为已验证上限。BTC旧父轮342087仍有容量风险，只有新真实链能判定。停止进一步通用压缩平台探索；新originals结构化parser未采用，原R5a源码不再改。

**Files:** `infra/dsh/development-runtime.ps1` 初始化消费点与 `run-product.ps1` DEV传参（仅必要处）；必要新增单一 `infra/dsh/development-model-settings.mjs` 解析/窄改helper；必要`run-dev.ps1`参数透传；聚焦JS/PS回归。默认正式模型选择脚本与vendor/overflow判断不得修改。若可更少文件完成则选更小方案。

**Interfaces:** 源为已有同provider/API/baseURL/model的设置声明，只读；只提取contextWindow，不复制profile/credentials/policy。缺失、重复、错误模型/路由、坏数值明确处理，不继承别的模型默认值。对本次已知launcher生成旧128k设置迁移需要精确目标前像或生成模板证明、私有备份、源/目标SHA核对及幂等读回；只改目标模型contextWindow一个scalar，maxTokens与其余内容不变。手工改过的未知配置不可仅因128k而覆盖。

- [ ] 先真实旧源RED，验证初次生成和已有128k配置复现；同路由正确继承及第二次无变更。
- [ ] 反例：provider/API/baseURL/model错配，metadata缺失/重复/非法，目标前像漂移/手改，源或目标链接/异常，不复制secret/不写正式源。
- [ ] 停止实例迁移后读回semantic diff只有contextWindow；保留旧设置原件和实际副作用记录。
- [ ] 相关helper/launcher测试、必要静态和独立审查；不重新完整打包、不改已绿TS实现，无新TS改动则复用已编插件。
- [ ] 新开发启动、owner/服务/status验收，然后新研究按R6顺序执行。不得将配置纠正直接记作研究成功。

### R5d：FOMC 多阶段原件的根模型视图无损共享

**实际新Run：** BTC `run_ec03e5b9ff4f43b3b54cbd09a351dc47` 在DSH/model前失败，无模型input/finish/link。外层research_method_binding_failed的原始cause为perpetual_research_materials_oversized。只读重建 `_root_material_view` 为277174B，超过perpetual_profile.py原256000B门21174B。event_agenda从旧33738增长为87747B，新增FOMC statement/press conference两个必要阶段；不得删掉这些事件来过门。原件 `timeout/new-btc-method-binding-diagnosis.md`、safe JSON与owner-only private fixture已冻结。

**已证可行性：** 两stage的相同original_json共享一次，再将相同coverage.original_json以已包含calendar_coverage的原件共享，可277174→236389B，门内余19611B。所有stage/coverage/顺序/身份、原字符串与SHA逐一可恢复。此为内存prototype，不等实现或真实通过。

**Files:** 新单一 `packages/runtime_adapters/dsh_runtime/perpetual_materials_view.py`（若选择等义更清楚名称先记报告）；窄改 `perpetual_profile.py::_root_material_view` 与模型解释指令、`packages/kernel/decision_hub_kernel/application/perpetual_experience_use.py::_frozen_materials` 的解码接缝；聚焦新测试与原runtime/experience/materials相关必要回归。不得修改原catalog构造/validators、事件stage/coverage内容、256000门、deadline、provider上限、旧失败/历史原件。

**Interfaces:** 模型展示专用、有明确版本的共享表/lookup；完整真实原件只存一次仍inline可读，不base64/gzip隐藏。原authoritative materials、catalog、hash与source-ref都不改；只在渲染copy上操作。原生model input仍按真实收到bytes验证/绑定；经验消费者在现有native来源检查后严格展开展示编码，再交原validate_catalog/body_hash/原件链，不能以逻辑展开文本替换原生input签名。

- [ ] 用已冻结私有277174B素材离线RED复现当前builder拒绝；合成最小case覆盖重复stage+coverage，不让普通CI依赖私有数据。
- [ ] 表和lookup仅覆盖已证相同FOMC原件，完整展开精确还原旧rendered bytes/catalog及哈希；所有其他核心族/不同vintage/不同body/顺序不动。
- [ ] missing/hash错/跨run或parent ref代换/不支持版本/越界path/循环或膨胀/恶意source marker均拒绝；legacy无编码prompt保持旧读链，原生hash与catalog验证仍有效。
- [ ] 真实旧fixture编码小于原256000B，重建后通过原catalog/experience校验；在新源码下的当前材料还需重新真实准备验证。
- [ ] 新helper Ruff/strict0，旧改动文件对前像无新增；必要原runtime/experience/gateway/材料测试，独立审查后统一冻源。
- [ ] 停开发实例改Python，再源码重启；如果插件输入未变复用原产物，无完整发布包。新run使用新requestId且关联上次失败，先BTC后ETH，不改旧failed。

**边界：** 这是同一主链的下一处已证准备阻断，不是无证据扩大容量或增加新事件产品。R5a/R5b/R5c已通过门复用，不重复改它们；A4扫测已为真研究资源窗口记中断，后续按未完成/需错误明细节点续跑，不宣称完整基线完成。

R5d Ruling（16:47 UTC）：preflight指出helper放runtime会迫使kernel新增向外依赖。采用更窄且符合现有层次的 `packages/kernel/decision_hub_kernel/ports/perpetual_materials_view.py` 作为共享纯helper，runtime renderer与kernel consumer向内复用。原建议路径不再实施，不复制两套解码器；其余scope/阈值不变。代价：ports增加模型展示协议工具，必须保持无I/O、无运行器/数据库依赖并由测试锁定。

## 2026-10-29 16:50 UTC 续接快照（各项资格分开）

| 阶段 | 目前证据 | 状态/下一步 |
|---|---|---|
| R1/A1 | 两旧run时间线、原生214299输入正常stop被本地128k误判、ETH审核剩84秒；旧审核正文回放通过 | 排查已完成；原两run仍失败，不能升级 |
| R2/A2 | Windows真实重启事件；生命周期25定向PASS及实际启动/停止回执 | 状态残留已修并现场验证；重启具体触发未知 |
| R3需求 | EVENT_REQUIREMENTS_COVERAGE；CPI/PCE/就业/FOMC与扩展事件逐层对照 | 审计完成；多事件预测功能与真实闭环未全部完成 |
| R4/A3/A5 | STATUS_RECONCILIATION，PAPER期限/游标代码及原测试接线 | 静态核对完成；真实≥10笔费用验收未完成 |
| R4/A4 | 1030项扫测中断于约75%，原件保留；正在生成剩余/失败节点清单 | 不是完整基线、不是全绿；后续独立资源窗口续跑 |
| R5a/b/c | 无损审核视图、生命周期、模型声明对齐；各定向/相关检查与独审通过 | 候选已集成，不能代替真实业务通过 |
| R5d | 新BTC0模型调用时根view277174>256000；保留双FOMC+coverage的共享prototype236389 | 已先写scope/前像并停实例，单一实现者做RED/GREEN，独立审查跟进 |
| R6 | 新BTC run_ec03e...准备失败；还没有新ETH | 四项真实验收未闭合；离线修复通过后新attempt顺序执行 |
| C/D/E | P3/P4/P6收尾、正式发布、14天/多事件真实/PAPER/四周评分等 | 未计完成；本轮明确不运行完整发布包 |

start02观察器最终自然exit0（官方stop后收到DEV_TERMINAL native_exit=0），修正此前尚未收取终态的记录；此项不等于真实研究成功。新的attempt-aware提交/监视工具仅写artifact，旧脚本和旧回执保留；实际发起前仍需R5d独审与冻源。后续命令使用 submit-recovery-attempt.py <instrument> <btc/eth-attempt>，新标签/新UUID，记录prior_run_id。

时间标注勘误：上文手写的“2026-10-29 16:50 UTC”快照标题混用了中国日期和UTC，并非现场时间，应以本条系统时间及各原件at_utc为准；R5d Ruling的手写16:47也不作为取证时点。后续日志统一由系统生成带时区时间，避免手填。事件/错误码/退出结果不变。
本条实际UTC=2026-10-28T16:47:16.8794138+00:00
### R5e：V3 请求与已认证子研究的 protected-context 选择一致

**实证/诊断：** 新BTC run_855459f0c597467ca4ce5a09b6635cc4，root earliest UNKNOWN/research_provider_native_context_invalid @17:31:28.375Z，后有child回调拒绝及uncommitted终态。原保护message134仍与最后成功输入逐字一致，压缩未丢该消息；不得按猜测放宽marker/hash。实际output_schema_ref为 decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3。现synthesis-protocol.ts已将V2和V3统一判decision_v2，bridge加载childResearchSources；但host/retained-context.ts:25–27默认只对精确V2选v5，:76 eligibility也只接受精确V2，V3因而选v1/:79剔除已加载子来源。root工具仅交付9条，当前root-session accepted16条，7条真实child原件早于根错误。:100–107对v1的allowed+dynamic检查可拒绝这7条；child错误晚于根终止，不能作为第一原因。实际投影重放仍须完成并保留，不仅依赖码名。

**允许路径：** 仅 `extensions/dsh/decision-hub/src/host/retained-context.ts` 的版本选择与childWire eligibility两个接缝（可加一个纯共享predicate防两处再次漂移）；新增 `extensions/dsh/decision-hub/tests/retained-context-schema.spec.ts`，必要新测试support仅同目录且先记报告。旧测试断言不改，synthesis-protocol/bridge/child auth/validators/hash/lineage/额度/deadline/Provider容量/Python/schema/UI不改。源前像和HEAD在context-schema/before.json。

**接口/不变项：** 新鲜V3请求应与现已定义decision_v2协议一致，走既有v5或由实际重复属性池升级v6；显式历史v1–v6保持原样读，不把旧failed改成功或把v1原件伪改v5。默认选择和显式v5/v6 eligibility使用一致predicate；非V2/V3请求原有行为不变，不无意新增未知schema异常或接纳泛化未知schema。子研究只能来自既有已认证child source/proof；不得当root工具执行，不把pending当trusted/coverage；跨run/generation/parent/source hash/native lineage与消息完整性校验全部保留。不增加重试/时限、不放宽校验，七条存在于DB不等于自动授权为证据。

- [ ] 先纯local RED：真实V3形状+真实认证child source fixture，当前v1拒绝；V2对照已允许。以R1真实原件投影/安全清单复核具体guard。不得调用网络/API/model或启动服务来复现。
- [ ] 最小GREEN：V3新鲜选择v5/v6、显式childWire接受同一受支持V3，保持源身份/原文/引用与归因；V2/旧legacy/explicit历史wire行为一致。
- [ ] 负例：child原件缺失/篡改、跨run/parent/generation/模型身份/哈希、不同任务、重复/丢marker与未交付pending不得变成trusted；复用既有真实认证fixtures与测试，不用裸ID伪造授权。
- [ ] 只补必要retained-context/child/dispatch相关回归和完整插件tsc；不重跑Python全量或已绿R5a/b/c/d大套。新文件短函数，strict零新增。长日志落context-schema/。
- [ ] 独立spec+quality审查，最终源SHA冻结；当前开发已stop03正常stopped/absent/listeners0，可修源，禁止完整发布打包。
- [ ] Root官方源码重启，仅插件局部编译并记录真实耗时；实际新V3保护版本/子来源与原件校验单独核验。新BTC-r5e1新requestId关联failed855459，不覆盖旧原件；受控真实研究前必须离线重放GREEN。先BTC后ETH，仍不盲重复。

**裁决/代价：** 本轮不是压缩平台重写，也不改正确拒绝的child writer readset门。修正的是已存在两个受支持schema的选择器不一致；若真实投影重放否定该分支，则停止该实现并更新诊断，不能为了让真实运行过关扩大到任意schema/证据。
2026-10-28T18:56:30.4120246+00:00

R6 Ruling：允许新的事后普通4h验证，不重跑或修改已冻结V3。生产只读新状态18:46:34重算到23:46:34（1h发布预算+4h）当前已接纳日历无未来exact/nonmeeting条目，selector确为V2；NFP unavailable不能被解释为no-events，整体事件覆盖readiness仍UNPROVEN。/operations证BLS enabledtrue、source_access_denied且退避中，未强制重试/绕访问限制/伪造原件。公开正常GET证明OKX/Gate BTCETH响应shape可用，仅可用性，不授native新鲜度。

独立合同审计确认ordinary V2对coverage=unavailable不是硬veto：collector构造gap并continue，schema接受，历史重建/hash/父任务校验照常；无全coverage available门，V2不走V3强制event_plan。新Run只能具名NFP未知、不授事件资格，仍需原件、真实independentreview、两venue600s、全部原资格门。不是自动成功/degraded或V3强制降级；生产worker对新admission自行选择schema，若出现新未来event不得手工强制V2。旧V3失败与FOMC发布会功能缺口保留。

代价/边界：此新时点验证不能证明此前事前FOMC计划已交付；后续T03须解决发布会规则、事前freeze时序与缺资料完整报告路径。保留实际数据缺口，验证普通主链不能替代多事件/14天/正式发布。先新的BTC普通case，完成后再ETH，仍不完整打包，不改源码/模型。
2026-10-28T19:44:42.2134122Z
### R5f：审核原生仓库指令 baseline 的同源认证兼容

诊断已落盘live/btc-post1-reviewer-transcript-diagnosis.json、reviewer-instruction-provenance.json。实际parent call前唯一baseline与child seq8完整source+content EXACT；SDK当前继承preset/cwd，没有可用instruction-disable API。采用已有宿主读取的parent context.events作独立expected，producer与final consumer共用同一helper，以原reviewer call为cutoff；不用live文件复读/child自报digest/裸source.kind放行。child原件和hash完整保留，negative needs_revision仍negative，不授新审核/最终报告资格。

仅允许三处生产接缝：src/reviewer-system-snapshot.ts、reviewer-tool.ts、final-review-gate.ts；新专项测试，旧断言/支持代码只读。详见artifacts/incremental-delivery-20261023/d28-research-recovery/reviewer-instructions/task-brief.md。先真实形状RED，再最小GREEN与producer/consumer相关回归、完整插件tsc、独立审查。拒绝缺失/重复/变更baseline、额外productprompt、错误metadata/content/order/foreign source；无baseline旧路径保留。不要完整发布，不改时限、概率、原件/lineage、Provider/settings、市场数据或归档。

Ruling：这次修复认证SDK注入兼容，不绕过真实独审；实际独审唯一finding为三candidate共用basis缺继续空仓/考虑多/考虑空/失效条件，后续必须由模型修订候选再新独审，不能直接finalize。离线验证须覆盖negative review保留及已有修订链，减少只过第一guard就付费的诊断缺口。代价：parent baseline发生变更/歧义时保守拒绝，不能静默放行。

start04 stop第一次返回1 development_supervisor_identity_invalid，不能记正常stop通过；随后现场status observed absent/listeners0、recorded exiting，原observer自然exit0，时间19:42:39.3701793Z。已交独立只读生命周期排查，未重试/手改state/杀其他进程。当前无运行中研究，可开展此离线源修复；所有日志保留。
2026-10-28T20:51:23.3365737Z
R5f真实场景未闭合，自身设计/覆盖遗漏：新helper实际部署YES（dispatch/pluginbuild43feafb...、15compiled产物匹配），但SDK压缩保留旧baseline8并合法重发相同133，两帧source/content与child8 EXACT；历史count===1误判歧义。官方decodeStorageRecord无损展开父20703/子5432，真正reviewer seq18184==decoded index18184；packed物理outerindex1431分列，无重编号/补造。Prompt360083 exact/end1/tool0均过，首先helperFALSE；未知output equality未猜为首因。Root承担漏测SDK已有compaction重发场景的责任，非部署/provider问题。新Question与自主裁决reviewer-compaction/QUESTION_AND_RULING.md，R5g exact task-brief已在源修改前落盘；复用官方surface证明原call前唯一有效baseline和合法shadowing，保留全历史，不直接放宽count/取latest，不改原件、时限、配额或审核结论。

Root已用正常owner命令3fa347b8-a778-44af-a5b9-4b4c71c689e9取消46aec，accepted；实际cancelled/cancelled_by_owner，未改名成功或伪称自然failed。30modelcompleted、2reviewer意图/1finalize意图不授审核/发布成功。四项PASS/FAIL/PASS/UNPROVEN见btc-r5f1-terminal-acceptance.json，ETH未发。全部历史保留，无新付费请求。

start05官方stop exit0、recorded stopped/observed absent/listeners0，业务7failed+1cancelled。外层observer实际exit-1分别保留，不混成exit0。原件start05/stop-result.json/status-after-stop.json/terminal.json；现可在已停实例上受控R5g。下次付费须先实际SDK decoded compaction RED/GREEN+独审，不盲重复。首次写R5g artifact补丁格式无效，工具验证拒绝未落盘；已一次纠正模板格式成功，无源码影响。
2026-10-28T21:01:37.8693737Z
R5g最终局部ACCEPTED，review/r5g-final-review.md C0/I0：11文件117PASS（原99不改）、全插件tsc exit0/0诊断、69旧tests/support SHA全同。helper4f87eae2...f3bc28a，SDKpeer/lock importer固定现有0.1.2-alpha.2并仅新增本地junction，实际Host/plugin同realpath/SHA，无install/升级/SDK改动。实际完整双codec回放旧helper RED false→新GREEN true，合法surface replacement130 shadow8、active133、needs_revision3保留。原raw、decoded前后及官方encode回环各自SHA域不变；首actual-GREEN失败是离线存储range decoder漏一层，按SDK loader真实顺序decodeSeqRanges→decodeStorageRecord纠正，未为存储格式放宽生产。新single部分负例澄清为multi路径，旧69断言全保留；原foreground live equality仍UNPROVEN，生产pureselector/增量fold/lastmessage重建hash一致另见foreground-mapping.md。

Root已生成live/active-code-freeze-r5g.json（16项目源码/metadata+1既有SDK runtime只读hash，modelsettings c1...不变），plugin-before-r5g-restart.json。现服务stopped，所有源/tests/metadata保持冻结，先补A4原250确切未终态节点，独立资源窗口不发模型、不编辑源码；完成/分类后再start06，不跑完整发布。此接受不授BTC/ETH报告成功，旧7failed+1cancelled和C/D/E未完成仍保留。
