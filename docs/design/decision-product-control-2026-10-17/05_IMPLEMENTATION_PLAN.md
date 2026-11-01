# 事前决策产品分阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. **2026-10-18用户已批准review-5并恢复实施，采用OKX永续建议/纸面方案。** 当前子任务以execution-contract.json为准，过程证据见EXECUTION_LOG.md；不得把全部T任务并行扩成无限重构。

**Goal:** 接通并验证事前判断、情景概率、条件行动、持续提醒、结果复盘和下一次经验消费，交付可正常使用的BTC/ETH PC产品。

**Architecture:** 复用Hub领域服务、Graph持久化和DSH原生循环；按07先抽取来源验证port并拆开运行阶段，修订预算/成果与按版本复核规则。真实组件验证随这段主流程改造完成，不单独扩成验收平台；不新建Agent平台。

**Tech Stack:** Python 3.12、FastAPI/SQLAlchemy/Alembic、现有锁定LangGraph/DSH、React 19/TypeScript、pnpm 11.19.0；版本以仓库锁文件为准，本计划不升级依赖。

**Spec:** [00总控](00_MASTER_CONTROL.md)、[01模块映射](01_CAPABILITY_TRACEABILITY.md)、[11全链合同](11_END_TO_END_DESIGN_CONTRACT.md)、[08 Agent/根因/上下文/学习](08_AGENT_RESEARCH_AND_LEARNING_SPEC.md)、[02方法](02_DECISION_METHOD_AND_EVALUATION.md)、[03运行合同](03_RUNTIME_AND_DATA_CONTRACTS.md)、[04PC合同](04_PC_PRODUCT_CONTRACT.md)、[07代码层架构修订](07_ARCHITECTURE_REPAIR_BLUEPRINT.md)、[10技术债](10_TECHNICAL_DEBT_REGISTER.md)。原33项及G/E/V/I验收继承见01；新增双向F33仍映射本T任务，不开第二条执行队列。

## Global Constraints

- 状态：review-5已批准，实施进行中；文档齐备与技术核查均不是产品完成证明。
- review-4首交付为BTC/ETH双向建议/纸面、普通市场与多事件入口、PC、持续跟踪和结果学习。推荐OKX USDT线性永续；旧现货保留。CPI/PCE/Employment/FOMC核心事件族和加密原生事件接线在T03–T08；全球其他重要事件按02覆盖/来源资格进入同一研究链。黄金/纳指/恒科与后续方法增强在T09，Kronos在T10。不能把CPI完整统计模型或等下一次公布作为普通加密主线前置。
- 新主期限4h/24h/72h、旧30m与独立7d分别保留；必须贯通schema/profile/绑定/标签/学习与UI，不能只改展示名称。
- DSH负责模型和工具循环；Graph负责研究业务流程；Hub负责持久事实、行动、通知、结算。
- 研究有真实进展可超过60秒；页面、监控tick和研究的预算分开。
- 不以省token删除反证、出处或关键输入；保留已要求的gpt-6.1-sol。
- 旧协议/旧判断/失败原件不得回写成通过；真实数据、回放夹具、纸面模拟分别标识。
- 做多/做空都是首版操作候选；paper采用03 §10.5的逐仓单向基准。真实自动下单不在本包，未知账户不给虚构数量/杠杆；移动端暂缓。
- 产品代码、schema、生成类型、领域规则和UI职责分别落在已有模块，不新增混合大utils。

## Review Focus

1. 受理后HTTP连接结束：T01/T02必须证明后台任务身份、取消和终态不随客户端等待误变化。
2. 真实Host返回失败双空结果：T01/T02必须保留原始失败，不能由替身掩盖。
3. CPI实际值迟到/修订或共识缺失：T03/T04必须保留事前版本和未知项，不能用后来数据补事前成绩。
4. 提醒已投递但响应丢失、服务重启：T05/T08必须证明幂等、原回执和有效方案恢复。
5. 同一事件多资产/多期限、技能自我评价：T06/T09必须按事件相关性分组，不能重复计样本或让候选自批晋升。

## 1. 任务顺序与状态

2026-10-18激活D1/T02+T01；后续T任务尚未签收。01记录的已有实现需要复用，不代表必须重写。

```text
用户结束review-5文档补审暂停（review-4及OKX范围已批准）
→ T02运行/分层改造 + T01随行的接缝证明
→ T04前置：预算/成果分离、版本化复核
→ T03/T04/T05/T07：先一个合约一个目标的设置→双向建议→监控→PC切片
→ 同组扩到BTC/ETH的4h/24h/72h、核心多事件/可能事件及纸面结果
→ T06结果/候选/后续实际消费，T07同步展示变化与能力状态
→ T08正式候选整链与交付
T09新资产/后续专用方法、T10 Kronos：独立增强，保留在总需求中
```

这是唯一执行顺序；03/07/10不另设可竞争的任务序列。T01嵌入T02及所改边界；每个切片先证明具体断点，修改后用同一正常路径与直接反例复验，足够后向主线推进。T04分前置与主体但ID不变。T07必须随切片完成：不允许先做完所有后台再发现用户主线缺字段。自动晋升尚未接好时用明确baseline方法交付研究切片，T06仍是整包完成门。

### 1.1 四个可审阅里程碑及停止扩围规则

| 里程碑 | 用户能看到什么 | 必须证据 / 何时继续 |
|---|---|---|
| D1 运行恢复 | 一个正常研究命令真实成功，经审核形成报告/Action并在PC回读、恢复原身份；另核失败首因 | 必须有同候选API→真实native→审核→报告/Action→PC成功证据；准确失败只是必要负例，不能替成功签收。只证明当前合同恢复，不宣布新多空完成 |
| D2 双向可用切片 | 配置合约/仓位/风险→三类概率→明确首选/备选→触发或失效→纸面结果；先BTC永续4h跑通再两资产三期限 | 11三条用户流、02手算净PNL/三分类、04页面同版本；实时lane不被深研究占用。子切片完成即演示，不等所有方法自动学习 |
| D3 结果改善闭环 | 解释哪条机制/操作出了问题、下一次用了哪条非seed经验、何时撤回 | 真结果→候选→新Run实际消费/可回滚，目标scope/期限独立；已有spot结果不可替perp资格。缺新scope成熟真实样本保留该门未验证，调度结项且不空等或伪造完成 |
| D4 原扩展范围 | 黄金/纳指/恒科及Kronos各显示实际支持程度 | T09/T10各自数据/方法/行为/金融资格，不能只接报价或运行模型就标全支持 |

开发记录按D与T双索引。一个任务的验证已经覆盖其明确风险时停止添加可选测试；出现新问题先写10的具体差异和用户影响，只在阻断该切片时当前修复。公开建议缺失、多空方向错误、错误时点/价格/账户绑定均属主线阻断，不能列作以后美化。

T01/T02仅是D1工程切片，不能结束整个产品目标。T03–T08是提议首交付的必要范围。T09/T10按scope与候选逐项验收；它们未完成时，整体需求必须继续显示未完成。数周金融观察由持久调度与协议结果负责，不把开发会话挂在那里反复测试或等待。

| 工作包 | 01实际模块设计 | 必须形成的可见代码结果 | 主要合同 |
|---|---|---|---|
| T02+T01 | §1A依赖图、§1B运行与研究模块 | Kernel移出DSH私有解析；API/Worker共同装配；模型结束/原件/发布分段 | 07来源port、03生命周期/迁移 |
| T03 | §1B采集、事实、事件/宏观 | 多事件族/阶段/议程冻结及PIT来源、主期限新版本；保留独立CPI旧数值分支 | 02 EventFamily/Target/Probability，03对应接线 |
| T04 | §1B研究、情景、行动 | 独立角色实跑；新问题修订；根因图与概率/净效用产生可跟踪建议 | 08角色/上下文/Inquiry；02数学与行动 |
| T05 | §1B持续跟踪和通知 | 新证据改变版本，触发条件可求值，离开PC仍工作 | 既有Action/Event monitor、outbox |
| T06 | §1B结果、经验/技能 | 成熟结果归因→候选→受控试用/晋升→下一Run真实消费 | 08 ReflectionDelta/激活指针；02评分 |
| T07/T08 | §1B查询、PC、运维 | 当前动作/场景/依据/变化/经验可读，整链可复现并有手册 | 04七个内容块与U01–U10 |
| T09/T10 | F29–F31及对应模块 | 各资产能力包与独立PathForecastRuntime接缝 | 02多资产与Kronos角色 |

### 1.2 每次只执行一个有结束条件的子任务

以下任务章程在获批后写入当前执行合同的`current_slice`，不把T02/T04整个工作包当一次无限任务。工作树包含历史未提交修改，baseline必须记录HEAD和本任务范围内的实际文件hash；不得把别的会话原有修改算成本次越界或随意回滚。

```yaml
slice_id: D1-T02-runtime-recovery-01  # 示例，不表示已激活
baseline: decision-product-control-2026-10-17.review-4
owner: implementer
user_outcome: 正常研究完成审核、同Run报告和行动，PC可读回
allowed_paths: []                   # 开工按真实阻断填写，禁止全仓通配
contract_refs: []                   # 只列本次涉及的schema/方法/ADR真实版本
dependencies_and_evidence: []
hypothesis_and_blocker_id: null
direct_checks: []                   # 正常路径+与修改有关的必要反例
uncovered_boundaries: []
attempts: []                        # 输入/代码/配置hash、假设、结果、证据
same_blocker_failed_attempts: 0
cost: {tokens: null, token_source: unknown, command_count: 0, elapsed_seconds: null}
close_when: 成功路径及必要反例通过，用户结果与未覆盖范围已保存
escalate_when: 同一阻断连续两次有效修复验证失败或发现必须改变产品语义
next_slice: null
```

第一子任务只处理有证据阻断正常链的阶段时钟/身份/回执或接线；API/Worker全部构造搬迁、全仓纯化、模型增强不自动进入。若具体依赖确实阻断，先更新章程说明为什么，再纳入必要子范围。已有R33候选先读其实际差异，不重写一遍作为新产出。

每次检查只产生一个明确结果：通过并关闭、获得新的定位证据并修复、或触发一次范围受限的跨层复核。相同输入的已通过门不反复运行；真实provider失败需证据区分外部瞬态和本地合同，不能靠同payload无限重试。代码/输入改变或旧证据不可信时才重复相关门。已经成功的用户链和无关模块不因新增一个事件族反复全部构建。

交付小结固定回答：用户现在多完成了什么、对应哪个对象/版本、还有哪个主线阻断、下一步是什么。测试数、文件数、hash数、构建成功均不能代替这四项。子agent只服务当前切片的可独立子范围；完成后返回短结论和引用，追加审查只针对具体问题。

### 1.3 review-5补审项如何并入原任务

以下按同一子任务接线，不另起治理/安全/测试平台。T02仍先解决有证据的运行阻断；新私有命令或能力在开放前满足其身份/数据门，不能把全部后续运维工作设为D1前置。所有新数字是具名设计profile，待测，不是已达标承诺。

| 原任务 | 必须同行的补充 | 唯一规范与直接门 |
|---|---|---|
| T02/T07 | 受信单owner会话、来源/CSRF与服务secret分用途；逐能力readiness、冷启动/交互测量 | 03 §10.9–11；伪owner/过期/跨owner拒绝且无写入，阅读可用不冒充研究/保护可用 |
| T03/T05 | source用途/新鲜度/缺口、校时和休眠恢复；perp quote大小/单位和纸面时钟 | 02成交profile、03质量门；旧数据不刷新成新事实，无容量不能模拟可执行数量，缺数据不追造成交 |
| T04/T05 | 成交条件概率、共享账户的联合压力、同窗口/多期限风险；按协议部分成交与退出保护 | 02新增算术；独立手算、两资产同时不利和部分fill反例；PC说明导致首选变化的原因 |
| T04/T06 | 角色冲突、候选状态投影、缓存修订/权限失效、外部文本信任、学习撤权 | 07/08具名消费者及直接反例；不新增review真相库、记忆权限或第二Agent循环 |
| T06 | 主观预测只按实际分支评分、尾部/期限一致性、事件簇/重叠窗口不重复授分 | 02评价合同；保持原协议分母和未来观察截止，不拿一个结果同时给所有假设格授分 |
| T07/T08 | 首次使用与日常恢复、监控健康和旧保护；准确入口与用户手册 | 04/12；本机Chrome同对象回读，停机错过窗口可见；观察页不是收益完成证明 |
| T08 | 配置/迁移owner、一次隔离一致恢复与兼容回滚、运行交接 | 03 §10.11、12；Hub/checkpoint/archive/selected版本/DSH原件同恢复集合，旧回执不重发；7日可用率固定到期报告，开发不空等 |

## 2. 工作包

### T01：让真实组件集成提前发现接线错误

**Files:** 修改 `tools/dsh_native_acceptance.py`（复用 `ScenarioRuntime`），必要适配 `infra/dsh/presets/decision-research/replay.patch.yml`；拟新增 `tests/integration/decision_product/test_native_main_flow.py`、`tests/fixtures/decision_product/native_main_flow_v1.json`。既有 `tests/decision_product/test_action_native_vertical.py` 保留其工程fixture标签，不冒充实装全链。

**Interfaces:** 消费现有API受理→Worker→Graph→Python runtime→TS Host→原生session；外部模型/行情才使用受控边界。输出同一owner/request/run/session/generation的真实证据、复核、报告、行动及终态回执。拟新增测试函数 `test_native_main_flow_publishes_same_run_action`、`test_native_failed_null_result_preserves_first_cause`，fixture带显式 `engineering_fixture=true`。

- [ ] 先核查现有replay模型适配是否经过当前 `model-dispatch`、`provider-input` 和prepared/selected-skill链；保存入口图与回执，缺少任一链明确写出。旧replay成功不自动合格。
- [ ] 添加上述集成断言：真实Host/DB/archive不得替换；正常路径要求同Run行动，失败路径要求原始错误和无伪报告。运行 `uv run pytest tests/integration/decision_product/test_native_main_flow.py -q`，先保存真实失败。
- [ ] 只适配ScenarioRuntime与fixture到当前合同，不为测试放宽生产HTTPS/身份/原件认证。新增CLI场景名 `decision_main_flow` 标为拟实现，其他参数复用真实argparse定义。
- [ ] 同命令复验；若replay确实无法经过当前Provider中间件，输出“部分组件门”的具体未覆盖边界，将该边界交给有限真实Provider门验证，禁止标全链通过。若正常本地接线仍失败，不开始正式发布。
- [ ] 保存可复用命令、fixture身份、通过/失败与未覆盖清单；独立提交本任务。

**完成门:** 能重复运行真实本地组件链并准确暴露当前接缝；清楚知道仍需真实外部服务验证什么。无需新任务平台、无需每个fixture完整构建前端。

### T02：收敛运行状态、计时、认证和终态合同

**范围控制：**以下是T02总归属，不要求全部改完才展示D1。当前子任务按§1.2冻结必要文件和阻断；没有影响当前成功路径的搬迁留待相关切片完成，仍保留原债项。失败两次的计数跨发布号持续，不以新增release清零。

**新增明确改造范围（07第2–3节）：**拟建`packages/kernel/decision_hub_kernel/ports/research_source.py`、`packages/runtime_adapters/dsh_runtime/action_source.py`、`runtime_preparation.py`、`runtime_lifecycle.py`及`extensions/dsh/decision-hub/src/host/run-lifecycle.ts`；修改`application/action_research_reader.py`、`apps/hub_worker/composition.py`。先将DSH特有解码/原件映射移出Kernel并注入验证port，保留现有行为；再分离Provider finish与receipt finalization。prepared-context/skill-delivery接线使用能力port，不继续依赖具体runtime类判断。

- [ ] 按01/03将API↔Worker互引的构造逻辑移至拟建`packages/pilot_runtime/bootstrap/{documents,research_skills,frozen_harness,history}.py`；保持配置语义，两个app都不能成为另一个app的公共库。`market_risk_data.py`采集依赖改为既有provider端口注入，业务域不新建OKX client。

- [ ] 将现有`_native_action_from_originals`迁至DSH来源适配器；已有身份、证据、review绑定断言随迁移保留，确保Kernel不再导入adapter私有实现。
- [ ] 按07规范化来源骨架补全生成合同及composition注入，保留历史DSH验证版本；新runtime不足能力时在准入处明确失败，不能先运行到发布阶段才发现不兼容。
- [ ] 拆开准备/Provider/回执/终态阶段；用明确Provider已结束但回执延迟的场景，证明原始模型终态不会被存储等待改写。

**Files:** 现有 `packages/runtime_adapters/dsh_runtime/web_runtime.py`、`extensions/dsh/decision-hub/src/host/bridge.ts`、`model-dispatch.ts`、`provider-input.ts`、`infra/dsh/provider-transport.mjs`；现有回调/原件服务的具体责任见03，只有证据指向时才修改。测试复用 `tests/research/test_terminal_error_provenance32.py`、`extensions/dsh/decision-hub/tests/model-invocation-watchdog.spec.ts` 和T01。

**Interfaces:** 复用 `DshWebResearchRuntime`、`DecisionHubHostBridge`；现有R33 `beginProviderInvocation`在真实delegate前创建唯一Provider计时，保持Task deadline约束。native状态、认证receipt、业务Run和结果存在性分开；原始失败不被关闭/读取次生错误覆盖。准确DTO与新增候选接口以03为准。

- [ ] 按03时序给T01添加三组断言：准备阶段不冒充模型耗时；受理后断开HTTP不取消后台；native失败到认证receipt再到公开Run可定位各段时间。
- [ ] 定向执行 `uv run pytest tests/research/test_terminal_error_provenance32.py -q` 与 `pnpm --dir extensions/dsh/decision-hub exec vitest run tests/model-invocation-watchdog.spec.ts`，记录现有候选修复是否仍通过。
- [ ] 在对应责任组件修复失败；不删除权限/fence、不靠延长固定宽限掩盖传播；不变证据复用依赖部署闭包和原件身份，实时权限/取消继续新鲜校验。
- [ ] 若修改认证闭包，增加拟建 `tests/dsh_native/test_bound_source_scope.py`：相同mtime但内容不同、文件替换、取消/generation/lease变化仍拒绝；加入未选中的历史文件不扩大本次readset；记录文件数/字节数/writer_hold而不虚构阈值。执行 `uv run pytest tests/dsh_native/test_bound_source_scope.py tests/dsh_native/test_provider_input_receipts.py tests/dsh_native/test_original_research_proof_lifetime.py -q`。未修改该边界不为测试而扩大重构。
- [ ] 再执行同一组及T01门；记录准备、连接、首字节、有效进展、终态传播分段耗时。没有实际性能样本不得声称性能达标。
- [ ] 保存当前SDK/构建/配置和恢复合同；独立提交。R33原件保留为历史，不改成当前全链通过。

**完成门:** 本地组合成功、失败归因及取消恢复正确；未来真实Provider失败时能区分外部失败和本地实现故障。

### T03：统一事前时点、关键事件和宏观输入

**Files:** 复用 `application/event_scenario_snapshot.py`、`event_macro_context.py`、`event_lifecycle.py`、`event_outcome.py`（均在 `packages/kernel/decision_hub_kernel/`）；新增共识/时点类型与适配文件遵守02的拟新增接口，schema从现有contracts目录生成，不手改生成文件。

**Interfaces:** `freeze_event_scenario_snapshot_in_session(session, run_id, *, cutoff_at, archive=None)`、`collect_event_macro_context_in_session(session, *, archive, cutoff_at)`为已有入口。新增输入必须携带目标定义、单位、参考期、发布/采集/首次可用时间、原件版本与来源资格；共识缺失保持typed unavailable。事件注册/日历/截止复用已有ledger。

**新增明确接线：**按03 §10.5在既有OKX adapter及quote port增加合约元数据、mark/index、bid/ask、funding/结算时间和保证金阶梯原件；新增`decision_product_v2.schema.yaml`及生成类型，旧v1不改含义。`InstrumentContractV1`与SourceAvailability区分已安装/当前可取/历史PIT可用/合法使用，不能把API目录当可用数据。可能事件由02具名发生条件/观察窗口建目标，不能给无限“黑天鹅”统一伪概率。

**多事件族为当前主线：**按02/03新增`EventFamilyPackV1/EventFamilyBindingV1`及议程冻结合同、`ports/event_family.py`、静态`application/event_family_registry.py`与`packs/crypto_macro/events/<family_id>.yaml`。沿既有pack manifest/research binding、事件ledger和source adapter接入，保留旧CPI original reader。事件ID、发生簇、phase/发布时间和reference period分别建模；schema enum含fomc不能当作FOMC全链已实现。

- [ ] 核对并接通CPI/PCE/Employment/FOMC核心族的schedule/actual/原文、单位/precision/first release/revisions、时区、阶段、情景、方法和labels；官方日历有记录而无可执行方案必须显式pending，不得静默漏掉。
- [ ] 现有BLS/Fed/ECB/BOJ目录逐项说明真实精度与parser；BEA源未接则新增对应adapter/config，不把识别PCE标题等同收到PCE数据。第三方共识和资金流源须单独权利/历史资格。
- [ ] 既有CPI确定算法只用于其family。PCE/就业/FOMC先实现02具名结构化概率/条件预案，实际发布后按各自规则认证；不能一律用headline/core CPI系数或“偏热/偏冷”替代其语义。
- [ ] 新增拟`tests/decision_product/test_event_family_binding.py`的有限合同反例：至少CPI、PCE、就业、FOMC各一具名输入；同日多事件不碰撞、日期精度未知不补8:30、阶段后到不回填、就业修订与首次值分开。命令为`uv run pytest tests/decision_product/test_event_family_binding.py -q`（planned，本轮不执行）。
- [ ] 按03 §10.7接入薄适配器`official_feeds/{bea_release,bls_employment,fed_policy}.py`及`market/okx_event_context.py`，复用原件、采集和生命周期服务。数值actual按原字段精度认证；声明/发布会的语义解读保留主观方法及证据，不能包装成同等精度的官方数值。有限案例补FOMC当次无SEP、公布延期、声明与发布会先后、同窗口两个重大事件；联合情景必须留下依赖，不能直接相乘各事件边际概率。

- [ ] 增加 `tests/decision_product/test_event_information_set.py`（拟新增）：公布前不能见实际值/后来修订；共识不能用前值代替；同一事前输入身份稳定；无共识仍能给相对前值的明确标识方案。
- [ ] 运行 `uv run pytest tests/decision_product/test_event_information_set.py tests/decision_product/test_event_coverage_and_poll.py -q`，保存失败。
- [ ] 接入02冻结的数据合同和来源优先级；保留新闻观点作为待核假设，复制新闻去重；未经许可不接收费源。缺失来源影响相应目标资格，不把整个已有市场能力全部禁用。
- [ ] 按02/03新增4h/24h/72h主profile版本，保留旧30m/24h/72h合同读取；同时覆盖`agentic_research.schema.yaml`新组件、`decision_product_v2.schema.yaml`及`research_probability_binding.py`版本路由/固定集合校验，不能只加enum或改UI。为4h生成独立PIT目标和标签，不复用旧30m分数。
- [ ] 普通市场按02的publication_bound规则冻结发布时钟/下一分钟起点，机会slot先登记、错过/迟到仍在分母；旧600秒固定预留仅作历史协议读取。事件预案按T0及固定目标窗，不因迟发布平移。target_family与实例hash分开，不在发布后回填新信息重新估计概率。
- [ ] scheduled主目标采用02的T0−60s闭合trade锚点，包含公布跳动；T0+60s独立post_release_response。两个family分别建标签/模型/资格，反例覆盖公布首分钟大跳动不被排除、提前公布不能拿计划T0签事前。
- [ ] 验证trade/mark/index三种价格不可互换、合约面值/lot变化和funding时间原件；缺历史/报价明确影响哪些动作，旧spot报价不得给新perp填空。
- [ ] 同命令通过后核对CPI headline/core、美元/名义和实际利率、仓位/流动性输入各自的时点及缺口；逐资产映射，不能把日线背景当分钟反应。
- [ ] 保存source capability矩阵与采用/不采用原因；独立提交。

**完成门:** 系统能够重建“当时知道什么”；相对前值、相对共识和市场反应分别有目标，未知项可追踪。

### T04：把事前研究变成有依据的条件行动

**前置代码修订（07第3–4节）：**`decision/research_quality.py`和`application/research_quality_projection.py`分开内容资格/续研许可；`extensions/dsh/decision-hub/src/final-review-gate.ts`与`dsh_runtime/profile.py`按candidate revision/hash授权复核；拟建`application/research_inquiry.py::admit_inquiry`，在原capability范围内接纳新根因问题。`research_context_selection_v4.py/research_execution_binding.py`冻结RolePlan和RequirementRevision引用，`research_graph_binding.py/research_graph_projection_v3.py`接纳真实回执，`decision/research_loop.py`安排原有下一轮。不修改原Task/hash，不再套第二个supervisor循环。

- [ ] 钉住“最后一次调用已得完整成果仍被预算降级”的具体反例，修订判定顺序；权限、取消和事前截止约束继续独立有效。
- [ ] 实现v1→挑战→补证→v2→新复核的合法路径，同候选重放幂等，旧review不能批准v2。传输重试、内容修订、generation/lease变更使用不同字段，不能共用一个计数器。
- [ ] 增加RoleTask/RoleResult/ChallengeResolution与InquiryProposal/RequirementRevision合同；在已授权能力范围内允许新问题成为下一轮任务，无法执行的关键问题保留在结论缺口中。
- [ ] 上下文按问题加载，关键反证/时点/单位/条件保全且可回读；记录实际省略原因，不直接把扩大字符上限作为完成标准。
- [ ] 落地08动态RolePlan及每个实际child session/attempt原件；按问题派工和复用经核适用结果，不能每tick启动全套角色。同一会话改称谓不算多Agent。角色失败按受影响目标处理，反方与独立review必须有真实证据；公开依据摘要与内部模型思维过程区分。
- [ ] 按08根因节点/边合同及02确定性估计接口实现输入→条件概率→行动比较；模型不得通过写入`calibrated`或`verified`自授资格。CPI九格数值分支独立保留，不能强迫每次事件预案都先跑一次DSH。

**Files:** 复用 `application/action_research_integration.py`、`action_research_reader.py`、`action_publication.py`、现有research synthesis/claim/mechanism合同与纯decision模块；具体因果/概率接口见02。测试复用 `tests/decision_product/test_native_action_synthesis.py`、`test_action_research_integration.py`，拟新增 `test_exante_decision_policy.py`。

**Interfaces:** `ActionResearchIntegration.publish_committed(...)` 和 `reconcile_missed_hooks(...)`继续唯一发布接线；既有Opportunity→DecisionSnapshot→ActionPlanRevision引用保持。02定义的三类概率、收益/风险效用、反证/缺口请求进入结构化合成，UI不再自行推断。

**双向计算与设置：**新增拟`decision/perpetual_accounting.py`纯函数和`application/decision_settings.py`owner命令；沿现有Action/publication/query写入`AccountSnapshotV2/PositionContextV2/UtilityPolicyV2/ActionCandidateV2/ActionComparisonV2`。账户未知给单位名义三分支，neutral仅市场结果；设置修改产生新revision并使旧比较stale，市场概率不随用户仓位改写。新版本基线ComponentManifest与实际perp数据操作在T03/T04安装，先静态SkillSelectionSet消费，再由T06接自动候选，不能用旧spot DSL伪装perp能力。

- [ ] 定义并运行反例：最可能情景不一定对应最大净效用动作；缺策略经验胜率仍可发布标明资格的条件方案；反证改变情景/动作但不能回写旧版；每个主张能定位证据或明确标为假设。
- [ ] 按02固定首版`event_conditional_multinomial_ridge.v1`拟合/预测接口和结构化判断降级，完整保存训练PIT/参数/支持度。三类概率、long/short/flat比较及尾部成本分别输出；九格缺样本不得拼接基线伪造条件概率，也不要求每格20才允许有条件建议。
- [ ] CPI专用ridge与通用事件执行接口分开；FOMC声明/SEP/发布会按阶段冻结，非农/失业/工资与前值修订保留同包关联，PCE按本身指标定义。多事件议程进入研究上下文，同源/同窗冲击不独立相乘；尚无联合模型则发布明确假设的具名联合情景或区间，不伪造完整精确联合概率。
- [ ] 新增拟`test_perpetual_accounting.py`及`test_decision_settings.py`，用02合成手算验证多空符号、双向费用/资金费、强平风险、反手两个intent、未知余额不生成数量；settings变更不改历史概率、同键重试返回原revision。命令：`uv run pytest tests/decision_product/test_perpetual_accounting.py tests/decision_product/test_decision_settings.py -q`（尚未执行）。
- [ ] 执行 `uv run pytest tests/decision_product/test_exante_decision_policy.py tests/decision_product/test_native_action_synthesis.py tests/decision_product/test_action_research_integration.py -q`，记录失败。
- [ ] 按02补证循环与合成合同落地到现有DSH工具/领域节点；新信息有实质变化才触发再研究；重复搜索无新证据不能冒充推进。模型可继续深研，已有合格的条件方案独立保存。
- [ ] 按03 §3.1实现“当前Run完成并发布→ContinuationProposal登记→既有ActionReviewService/RiskResearchAdmission建立关联复查Run→Action revision”，保留同Run内部多轮候选但不新增活跃Run多次Action发布。补证与普通到期复查同键合并，恢复补意图不能重跑原模型。
- [ ] 按03 §10.6新增内部`action_revision.py::apply_comparison_revision`与`ActionComparisonDerivationV1`：v2 review显式授权候选集合/确定性方法/变量域，纯设置或成本变化可以重评并CAS新revision，不重复调用同Run publish。旧v1或越域拒绝并安排新复核；原概率不改。复用T04设置/算账直接测试覆盖幂等、跨域拒绝、成本翻转和并发设置，不新建通用规则平台。
- [ ] 复验同命令及T01正常路径：报告摘要、详情与行动绑定同一截止、同一Run及有效版本。
- [ ] 独立提交并记录用户能看到的具体动作、条件、反证和缺口。

**完成门:** BTC和ETH都有可解释、可求值、可追踪的建议；不能仅填一段报告摘要或把未知概率默认0.5。

### T05：接通监控、变化修订与可靠提醒

**Files:** 复用 `application/action_monitor.py::ActionMonitorService`、`event_lifecycle.py::EventLifecycleProcessor`、事件机会通知和既有outbox服务；API复用 `apps/hub_api/action_routes.py`、`event_lifecycle_routes.py`。测试复用 `test_action_monitor.py`、`test_action_notifications.py`、`test_event_opportunity_monitor.py`、`test_event_lifecycle_main_flow.py`。

**Interfaces:** `ActionMonitorService.claim/process_due`、`EventLifecycleProcessor.tick`；条件三值true/false/unknown；head CAS+不可变revision+outbox同事务；通知subject已存在，不重新创建平行类型。事件激活时间、价格可用时间和提醒可见时间分别记录。

- [ ] 补充“同一真实方案→新事实→状态/版本变化→通知→原回执”的集成场景；旧bar不能多次计连续确认，未知不解除原风险。
- [ ] 执行 `uv run pytest tests/decision_product/test_action_monitor.py tests/decision_product/test_action_notifications.py tests/decision_product/test_event_opportunity_monitor.py tests/decision_product/test_event_lifecycle_main_flow.py -q`。
- [ ] 只修缺失接线；保持25秒有限监控tick的既有shield/join语义与深研究分离。结果变更生成新版本，重放outbox不重复业务动作。
- [ ] 在既有realtime调度明确市场条件/事件actual/资金费与合约变更的有界批次和公平性；积压显示last_success/oldest_pending/next_due，不让深研究或历史回填占唯一监听者。事前预案快求值可先输出变化，深研究随后创建新revision。
- [ ] 监控绑定正确instrument/price basis/settings；做空止损和止盈方向、mark风险、funding跳变与事件提前/不发生分别直接验算，未知行情不当false或已成交。纸面执行必须存在明确paper账户，费用/资金费/保证金流水及结果沿03同链恢复。
- [ ] 设置以account_kind+account_id隔离；旧比较stale只停止新增暴露，已触发paper episode继续保护/退出监控。用户停止研究或新设置待生效不能撤掉旧保护，退出缺价明确告警而非伪平仓。
- [ ] 实际情景认证后选择事前已审核的条件q及操作分支，禁止继续拿边际m交易或借另一个时钟family的q。用02降温后long优于short的合成反例直接验证；未预审情景触发复查而非临时编造动作。
- [ ] 非CPI族沿同一monitor/realization链消费绑定；FOMC后续讲话、同窗口新事件、资金费/到期规则变化只复查受影响计划，合并重复来源和关联复查，不因每个标题再开完整深研究。
- [ ] 加入重启和响应丢失的重放，验证原revision/receipt不变；同命令复验。
- [ ] 独立提交并保存触发→发布→送达/可见/知悉各时间证据。

**完成门:** 系统持续跟踪自己给出的条件；用户离开页面也能更新，不能只在报告中写“建议关注”。

**双来源接线：**按04 §2.2，由事实接收/monitor记录event-owned预案与Run-owned行动的具名冲突或失效，并经已有admission去重安排复查；保留两个身份，不能在GET或前端发起隐式研究。

### T06：从真实结果生成候选经验并在下一次使用

**能力边界补正：**按07第6节分别实现检索、因果问题、上下文、概率/行动方法的版本化候选；优先在已有DSL与已安装组件范围内完成闭环。需要新增parser/feature/model/monitor时建立具名组件开发任务，不能声称SKILL文本已赋予系统未安装的执行能力，也不能由学习任务直接改生产源码。

**Files:** 复用 `application/action_execution.py::ActionExecutionService`、`research_learning_registration.py::LearningRegistrationService`、`research_learning_scanner.py::TerminalOutcomeScanner`、`research_learning_jobs.py`、`research_learning_packages.py::LearningPackageBuilder`、`research_skill_package.py::SkillPackageService`、`research_skill_use.py::SkillUseService`、`research_skill_consumption.py::SkillConsumptionService`与现有component registry（均在Kernel下）。`event_outcome.py::EventOutcomeService`继续负责独立事件评分。拟新增 `tests/integration/decision_product/test_outcome_to_skill_use.py`、`tools/action_evaluation/scoring.py`、`tests/decision_product/test_target_scoring.py`；已有事件测试 `tests/decision_product/test_event_outcome.py`。

**Interfaces:** 首闭环明确为 `ActionExecutionService.observe_evaluation/execute_intent/observe_episode/read_outcome` → `LearningRegistrationService.register_for_portfolio` → `TerminalOutcomeScanner.scan(protocol_id, portfolio_id, limit=4)` → learning job → `LearningPackageBuilder.build` →方法trial→ `SkillConsumptionService.prepare_in_session/freeze_in_session/read_consumed_in_session`。当前learning source只接 `action_outcome/execution_observation`，不能把EventOutcomeRecord直接塞入；事件结果作为可验证证据引用关联到行动反思，若将来单独作为触发source必须增加版本化适配及测试。`EventOutcomeService.process_forecast/process_due`与02 `score_frozen_forecast`负责事件和目标计分，不冒充行动学习输入。候选、试用、晋升/回滚以及selected/loaded/executed/consumed沿已有合同。

- [ ] 构造事前冻结→到期结果→候选技能→下一Run消费的失败测试；不把预置seed或只load计为结果驱动学会。
- [ ] 执行 `uv run pytest tests/integration/decision_product/test_outcome_to_skill_use.py tests/decision_product/test_event_outcome.py -q`。
- [ ] 接通缺失的结果触发和后续选用，独立记录适用/拒绝原因；禁止候选自评自晋升。后补结果建新版本，历史首次分数保持。
- [ ] 修正08现状边界：当前仅BTC/ETH现货24h且模板不能当实际消费；新增perp的4h/24h/72h来源/结果/方法协议，资格键包含market_type+venue+instrument+target_family+horizon。原spot协议不扩写、不授新scope资格。补方法试用结果读取器、SkillReleaseReceiptV1及selection接线，复用ComponentRegistry已有activate/rollback；下一Run读取新版本，当前Run保持冻结。
- [ ] 按08 §11.1实现SkillSelectionSetV1，每目标/期限成员独立资格与指针、Provider载入/节点执行/真实consumption证明；组件ref用稳定target family分区而非本次event hash，4h回滚不影响24h/72h。保留旧单24h选择与原件reader。
- [ ] 实现02评分接口及 `test_target_scoring.py`：事件二元/九格、市场三分类Brier及操作净PNL分别与手算相符；neutral阈值从事前冻结，不同目标拒混算，缺标签不填0，相关事件不按资产重复计独立样本。检查首个发布版本及独立slot分母，延迟和缺预测不能从结果统计消失。执行 `uv run pytest tests/decision_product/test_target_scoring.py -q`；不由单测授金融资格。
- [ ] 复验命令，并检查失败/错过/等待机会仍在分母；没有足够样本输出候选/试用，不能伪称增益或自动广泛推广。
- [ ] 独立提交。保存两次Run、原结果、技能版本、选择/执行/消费和回滚原件。

**完成门:** 至少能证明完整学习机制真实工作；“有效提升”的结论另按独立前瞻协议判断。

### T07：整合PC用户主线与手册

**当前建议选择：**按04 §2.2拟增`DecisionUiCurrentViewV3`与`DecisionProductQuery`聚合投影。Action优先、合法事件预案补位、实质冲突降为待复核；仓位/期限分别显示。前端不得按响应顺序、报告新旧或自由文本自行挑建议。

**Files:** `apps/decision-desk/src/product/ProductPages.tsx`、`ActionPlanPanel.tsx`、`EventPrevisionPanel.tsx`、`EventLifecyclePanel.tsx`、`ResearchProgressPage.tsx`、`ActionReviewPanel.tsx`及关联api/测试；更新现有 `docs/user-guides/DECISION_HUB_CUSTOMER_USER_MANUAL_2026-09-22.md`。不重写整个App或另起网站。

**Interfaces:** `/v2/actions/current`、`/v1/actions/*`、`/v1/event-previsions`、`/v1/event-lifecycles`及owner回执；准确字段走生成schema。04 U01–U10为用户验收定义。

**强制迁移同组：**`apps/hub_api/action_routes.py`、`DecisionProductQuery._scope`的期限/市场校验、`src/api/actionPlans.ts::checkBranch`和缓存键都须版本化；旧spot不能补位perp页面。RiskExposure本地假设不是真实设置；新设置页写03具名命令，持久回读后才显示已保存。当前页相同资产不同账户/风险/标的不能串显示回执。

- [ ] 为U01/U02/U03/U04加入代表性组件/API合同断言：事前时间、概率含义、仓位/期限、变化原因、原回执和过期状态。
- [ ] 同一切片展示明确首选long/short/保持或退出及备选；展开能看event/result、market up/neutral/down、action净盈亏三种含义。账户未知仍给方向/条件但不假数量；风险设置改变后显示比较待更新，保留旧历史。
- [ ] 运行 `pnpm --dir apps/decision-desk exec vitest run src/product/ActionPlanPanel.test.tsx src/product/EventPrevisionPanel.test.tsx src/product/ResearchProgressPage.test.tsx src/api/actionPlans.test.ts`。
- [ ] 按04整合首页阅读顺序、中文说明和展开详情；保留来源/资料/报告/历史/管理入口。内部属性值不占主阅读区。
- [ ] 定向复验；浏览器U05–U10留到T08整链，不为每个样式增加镜像测试。
- [ ] 更新手册任务步骤、真实截图占位要求和已知能力边界；独立提交。

**完成门:** 用户无需写prompt即可知道当前判断、行动、条件和变化；技术原件可展开，既有主线可达。

### T08：正式候选、真实Provider与PC完整交付

**Files:** 复用正式构建/启动/Supervisor入口，具体入口引用03；保存本次候选版本的独立验收目录，不能覆盖release32/33历史。使用04场景与现有11项核心证据标准。

**Interfaces:** 同一候选版本、部署身份、数据目录和端口集合；真实模型采用现有已配置gpt-6.1-sol。需要外部连接的步骤不混入默认单元套件。

- [ ] T01–T07相关门通过且本地接线无已知阻断，再冻结候选与一次正式构建；不为只改文档/界面无关部分重复构建全部DSH。
- [ ] 正常启动，确认必要服务和能力可用；在本机Chrome执行一个新BTC完整研究，再验证ETH同主流程。实际外部失败记原因，不用fixture签发真实通过。
- [ ] 用受控事件完成可重复的工程注入与U01–U10交互门，再分别核对真实事前方案、真实通知与真实学习证据。受控U06不能替代I04/I05：必须有真实结果自动生成的非种子候选，并由后续正常Run实际消费。优先使用已存在、合格且成熟的真实行动结果完成该链；旧spot结果只签原scope，不替perp新目标，不能伪造结果或空等下一次CPI。不存在所需证据时对应真实门保持未验证。截图与对应数据/Run/方案/回执成组保存。
- [ ] 官方安全停止/恢复一次，核当前方案、监控、回执和历史仍可查，不能产生重复任务/提醒；不扫描或清理无关进程。
- [ ] 发布能力矩阵、用户手册、运行入口、已知限制和各状态轴。把长期价值观察交给现有调度与固定终点报告，不让开发目标持续空等。
- [ ] 完成下方协议状态表的只读盘点与归属：已登记协议按原到期时间结项，未登记的设计不能写running；缺样本到期输出insufficient，附完整分母。任务负责人为T06评价实现者、T08交付复核者；运行责任为既有shadow/评价调度和结果查询，不能只写“以后观察”。

**完成门:** 当前版本真实主链和PC任务完整通过，能供用户正常使用；金融效果尚未满足的项目如实标注，不改变原验收协议。

**必须随交付更新的协议状态表（本轮未查询运行库，不能推断已登记）：**

| 路线 | 既有身份/定义 | 本轮能确认的状态 | T06/T08必须产出的有限结果 |
|---|---|---|---|
| 原MR23–25 path协议 | `ports/path_forecast.py`的V1/V2、原28日及100独立UTC日/30事件block门 | 原合同存在；当前实例ID/T0/T1/到期结果未在本轮读取 | 导出实际protocol_id/hash/T0/T1/expected/observed/scorable/missing/terminal；28日不能凑成100独立日，不足明确结项 |
| A28 | `action-forward-pilot.v1`；旧EVALUATION §A28，28日、T1+28h结算 | 设计存在，未证明当前实际注册 | 若有注册，保留原窗口输出工程状态与insufficient/no_observed_gain/descriptive_gain_unconfirmed；若无，先标not_registered，待T06合格后另登记，不倒签 |
| A180 | `action-confirmatory.v1`；独立180日、T1+28h结算 | 后续确认设计，未证明当前注册 | 冻结候选/成本/评价代码及窗口后登记；到期qualified/no_gain/insufficient；未到期输出running及due_at，开发目标不空等 |
| K1/K2 | K1具名原件与K2计划 | K1有限试验完成且未准入；K2未开始 | T10独立处理；不并入A28分母或给现有Action授分 |

协议状态导出仅核定身份和进度，不扩成新评价平台；任何原协议到期但尚未结项都必须列为具名未完成项，不能用新目标遮蔽。

### T09：新资产与后续方法增强（独立子包）

**Files/Interfaces:** 复用02资产能力接口及现有provider/domain pack；新增文件必须位于具体资产/事件适配器，不能复制BTC核心服务。每个子包在01关联MR29/MR30等原要求。

- [ ] 分别登记黄金、纳指、恒科：交易日历/时区、报价标的、数据权利、宏观机制、传导时滞、成本和结果目标；标的不能用指数和ETF价格混算。FOMC/就业/PCE等影响加密的核心事件执行能力已归T03–T08，不能再推到本包之后。
- [ ] 新的专用统计估计器/更多事件特征按EventFamilyPack方法版本增加；已有合法结构化情景方案继续工作，不因等待统计样本暂停加密主线。参数/样本资格按事件族、资产、期限隔离。
- [ ] 为每个子包建立同一输入→情景→行动→监控→结果的合同夹具，复用T03–T08的验收形态。市场休市不能补造分钟bar。
- [ ] 接通已有适配器和领域规则；关键数据权利/历史可用性未核实则保持该scope未准入，不影响已合格BTC/ETH。
- [ ] 每个资产分别输出完整链结果和PC能力状态；不能只接到价格就标“支持黄金”。

**完成门:** 每个scope独立通过同一业务合同；域模型、参数和数据资格独立版本。具体供应商费用/权利需获得事实后定，不在本轮虚构已可用来源。

### T10：Kronos与后续模型候选（独立子包）

**Files/Interfaces:** 沿用已登记K1/K2原件、path forecast适配器和冻结协议；模型输出只通过候选接口进入比较，不直接改Action或事实层。

- [ ] 保留K1未准入事实、非法OHLC、尾部漏判和CPU失败；按原K2窗口与目标登记输入/输出、硬件、样本和模型版本。
- [ ] 在模型适配层修复可用性/输出合法性并复验对应样本，不借修复重写原K1成绩。
- [ ] 按02既定基线、时间分割和前瞻资格比较，输出有增益/无增益/不足；未准入时作为研究实验显示，不进入推荐依据。
- [ ] 只有独立合格后在T04融合策略注册新版本，并检验有/无候选的实际行动变化；保持可回滚。

**完成门:** 明确知道该模型增加了什么价值或为什么拒绝；运行成功不等于市场预测有效。

## 3. 开工和结束时怎样记录

00是设计和批准入口，05是任务定义，06是裁决/操作历史，01是需求及证据投影；不允许它们分别维护互相矛盾的“当前任务”。获批后在本目录建立唯一`execution-contract.json`（**planned，本轮未创建/激活**），保存`baseline/approval_ref/phase/current_slice/task_states/evidence_refs`；`current_slice`按§1.2，含跨尝试失败计数与关闭记录。旧`market-risk-v1/execution-contract.json`保留为历史，不同时作为当前执行权威。`CURRENT_FOCUS.md`只投影当前合同和短接续点；每次状态变化先更新合同，再核对00/01/05的摘要一致。日志只在artifact保存，正文只留结论和定位引用。

**先修旧工具的具体冲突，不新建治理平台。** 已读`tools/acceptance/validate_market_risk_plan.py`硬编码旧目录、`market=spot`和active goal；`tools/context/build_task_context.py`硬编码旧合同/ADR且默认禁止DSH改动，与本次范围冲突。实施获批后，首子任务只做必要的配置化读取：validator接受显式当前合同及输出位置，依据其市场/批准状态校验，保留旧profile兼容；context builder从同合同的任务引用/允许路径生成有界上下文，禁止区仍按任务声明。目标卡片状态单独记录，不当作产品成功或用户批准的替代，也不让无关旧目标阻断已获批工作。新增字段须有schema/version和必要元数据正反例；不用运行真实Provider验证这些元数据。`check_module_docs.py`仅检查README存在，不能签技术架构正确。

这些控制当前只是设计，不能声称脚本已经强制执行。首子任务完成工具最小迁移后才记录其真实验证；迁移只控制当前任务状态/边界，不铺设另一个执行平台。一次任务结束必须记录：实现变化、执行命令、真正验证到的层、未覆盖层、用户结果和剩余依赖。每次重跑标明相对上次的输入/代码/配置变化或旧证据失效原因，连续两次有效修复验证失败触发00 §7的有界复核。

整包获批后，开发者可独立选择内部变量和不改变责任的内部拆分、测试组织。改变批准的产品范围、用户行为、概率含义、主体身份/时钟规则或金融协议，必须先更新受影响设计、说明用户可见差异并提交用户确认；不能只在长日志留一句理由后继续。普通实现选择不逐项打扰用户。

每次接续只加载00、当前T任务、对应模块设计与必要源码；历史原件按引用检索，不将完整执行日志和全体skills塞入工作上下文。任务结束写短记录`需求ID→修改文件/责任→用户结果→直接证据→未完成/下一步`。实际代码与设计不一致时先记具名差异并修设计/实现，不能宣称通过后再补写含义。

## 4. 本计划自审

- 业务闭环分别由T03/T04/T05/T06/T07覆盖，T08验证完整交付；T01/T02仅承担当前工程阻断。
- 原MR/G/E/V/I覆盖由01映射；现有方法训练、历史协议和多资产不得因本轮优先级删除。
- 上述新测试/CLI场景均明确拟新增；当前不存在不等于文档断言已实现。已有函数签名与03/02冲突时先校正文档，不允许实现者随意选一版。
- T09/T10为独立扩展合同，供应商资格和模型实证不能靠文档评审消除；相关未知项留有具体验证任务与失败结果。
- 本轮未执行上述命令，无新测试、构建或产品可用结论。
