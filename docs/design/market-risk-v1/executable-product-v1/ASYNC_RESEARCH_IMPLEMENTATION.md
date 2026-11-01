# 长研究执行：按有效进展运行，按原任务回看

登记：2026-10-14，OP071；OP072补入独立源码审计。本文落实用户最新要求：后台研究超过60秒可以正常运行；依据不足时继续追踪根因，阶段成果与最终方案持久保存。不是把一分钟作为研究质量门槛。

**当前状态（OP083）：core09已正常构建并启动；唯一正常ETH研究越过旧baseline整批120秒边界，保存真实模型回执及技能阶段原件，但最终失败，未生成完整报告或Action。认证首因是技能回调传输超时，后续报告原件核验又超过任务期限，8条证据仍可回看。core09已安装结果摘要投影；Q1持久callback为候选，完整Q2阶段归并和Q3跨Run续研未安装。** 这些工程成果不等于行动、独立Outcome、非种子Skill消费或金融效果验收通过。以[执行台账](EXECUTION_LOG.md)、[任务](tasks.json)及[当前状态](../current-core-execution.json)为准。历史运行证据保留在[后台设计](BACKGROUND_RESEARCH_EXECUTION.md)。

## 1. 当前代码与问题

| 真实接线 | 现状 | 应有行为 |
|---|---|---|
| `apps/decision-desk/src/api/client.ts:18,31–57` | 页面fetch默认30秒，超时为结果未确认 | 只结束本次等待；同请求查回执，不判研究失败、不创建第二次研究 |
| `extensions/dsh/decision-hub/src/host/hub-client.ts:135–140` | 技能callback传输固定60秒 | 根据原操作身份读取持久回执；等待策略与操作总期限分开 |
| `apps/hub_api/research_skill_routes.py:31–53` | callback固定60秒；断连和到期都设置同一cancelled标志 | 页面/传输离开与业务取消分开；未提交准备也必须有持久任务 |
| `extensions/dsh/decision-hub/src/host/bridge.ts:1343–1375` | step watchdog可以取消session | 单步期限必须解释其实际覆盖范围，不能用“无新证据60秒”取消合法Provider请求 |
| `decision/research_loop.py::research_acquisition_close_at` | 正式V2按冻结的独立reserve计算，旧无版本Task保留deadline减2倍model step | 当前正常新Run已冻结V2/900；分配仍为试行，尚未证明最佳 |
| `application/research_skill_service.py` | 能认证同key已提交loaded/consumed回执 | 不等于未提交长准备可跨进程恢复；此缺口单列实施 |

实际ETH07运行超过一分钟，最终因`CONTEXT_WINDOW_EXCEEDED`降级，报告仍可回看。不能把这次失败归因成60秒空转，也不能把报告保存等同于有效行动方案完成。

### 独立审计补充：已有功能与真实新增范围

- 页面SSE离开不会取消研究；显式Stop才落业务取消。`apps/hub_api/main.py:1278`、`application/research_observability.py:647`及`apps/hub_worker/research.py:1265`为现有接线。Host→Hub回调断连是另一条路径，当前可间接产生模型error finish，不是仅页面慢。
- Worker已有独立lease/续租，trace/query过程视图已经读取真实工具、证据、覆盖和审查事件。Q2复用这些原件，不声称现在只有心跳或没有阶段数据。
- Graph `packages/orchestration/langgraph/graphs/agentic_research_graph.py:640–662`只按新accepted Evidence或确定性coverage变化重置连续两轮门；无增益则停止继续轮次并进入`finalize`，不是60秒抛异常。原生Graph还经认证的projection/receipt决定转移；实施必须同时检查该权威路径，不只修改示例分支。
- 改措辞不应重置此门；但同一证据下关闭真实审查问题、核验主反机制可能有价值，需要具证阶段结果计量。增加此类进展须绑定真实审查/机制原件，不能信任模型自报“取得进展”。单个DSH轮内重复generic调用也须观测，现两轮门不覆盖它。
- `research_results`/`research_trace_events`和Artifact已保存完整结果/引用/主反链，GET详情与报告下载可读取。`application/commit.py:1709`却将`Artifact.summary`设为`result.stop_reason.detail`；Q2要将用户摘要改为论证与行动摘要，停止原因独立展示，不能把这个投影缺陷描述成没有落库。

## 2. 产品行为与时间政策

用户发起后拿到一个稳定研究主题/请求回执。关闭页面不终止研究。回来看到“在核验哪条机制、已确认哪些依据、尚缺什么、接下来做什么”和最终方案版本。

时间分为四类：

1. **页面等待**：短请求返回回执或状态；到期显示“后台仍在处理/结果待核对”。
2. **单项操作**：资料获取、模型请求、本地认证各自从冻结政策取预算。合法在途请求可以超过60秒，不因一分钟没有新材料被终止。
3. **单次Run**：有明确费用、调用数、绝对期限与收尾预算。需要更长研究时，按任务类型预先选较长政策或创建具名续研版本；不暗改原Run。
4. **研究主题**：跨Run保留因果假设、反方、缺口与修订关系。新数据、反证、时效到期可触发续研，不能靠心跳无期限重跑。

当前1800秒总期限、600秒step、900秒当前试行收尾只属于本轮试行。验收用实际取证/综合/复核/提交耗时评价；不能永久规定只允许15分钟取证。增加限制或缩短正文不得替代缺口核验。

## 3. 有效工作、研究进展与空转

这三个信号分开记录，不能只看“最后更新时间”。

| 情形 | 状态与处理 |
|---|---|
| 有真实Provider/tool请求在途，未到该请求期限 | `waiting_provider`；展示开始时间和等待对象，继续等；暂时无证据不算空转 |
| 新原件、已完成认证阶段、独立证据或可核不可得结果 | 更新工作进展，保存产物引用/hash |
| 缺口补齐、机制被支持/推翻、反方改变结论、独立审查完成 | 更新研究进展，保存依据和版本变化 |
| 同hash材料/同失败路线反复调用，没有新目标或输入 | `stalled_suspected`；检查在途请求和已提交回执，再换合法路线或结束本轮并保留缺口 |
| 只有心跳、轮询、“正在思考”文本 | 只代表进程存活，不增加研究进展或续期 |
| Provider明确失败/业务取消/原期限届满 | 保存已完成产物和原因；是否走后续版本由协调器明确决定 |

**不是每60秒必须产生一个结论。** 不按固定文本长度、token数量或相同心跳误判；停滞阈值按阶段和历史耗时设定，先诊断再处置。所用阈值随政策版本保存，可以根据实测调整。

## 4. 实施片Q1：持久callback与原回执恢复

复用现有Run、ProviderInput、原stage key与writer。新增窄`ResearchCallbackJob`存储/服务，由现有worker调度，不新增一个研究引擎。

持久binding最少包含：`job_id, operation, run_id, generation, execution_session_id, original_occurrence_ref, stage_request_id, request_body_hash, method/selection/compiled_plan_ref, policy_ref, deadline_at`。身份来自已认证原件，不由调用方补出新权限。

持久执行字段：`state, owner, attempt, lease_until, cancel_version, checkpoint_ref, result_receipt_ref, error_code, created_at, updated_at`。唯一性使用原操作身份与原stage key；同key不同body拒绝。

```text
Host先保存实际ProviderInput/工具occurrence
  → API认证原binding并持久接收，返回job_id
  → worker领取lease：原件核验→阶段checkpoint→原writer提交
  → 同job_id保存完成结果/第一份真实回执
  → Host读取真实完成结果后，SDK才继续该次工具/dispatch
```

`accepted → preparing → committing → completed`；另有`failed/cancelled/expired`。提交是否确认单独记录，断连不是`failed`。worker重启后先读取已有原回执，未提交步骤重新认证；不把SQLite快照或内存PreparedOriginals作为跨重启权限。

POST短返回202是内部接收状态，Host必须理解它；不能把pending当工具成功内容发给模型。GET只查持久状态，不同步重跑全部认证。显式用户取消、失效lease/旧attempt、换generation仍拒绝晚提交；已经提交的报告/回执仍可查看。

接线范围：`research_skill_routes.py`、`research_skill_service.py`、新增窄repository/service与additive migration、`apps/hub_worker`既有调度、`hub-client.ts`/`skill-host.ts`。具体新表和合同先与现有迁移head核对；本次core08不包含此迁移。

## 5. 实施片Q2：阶段成果持久化与PC投影

复用现有trace、query过程视图和结果原件，新增有效工作/研究/提交状态的版本化归并记录，最低字段：

```text
run_id, generation, sequence, phase, status,
completed_work, findings_summary, unresolved_gap_refs,
artifact_refs, in_flight_operation_refs,
last_work_progress_at, last_research_progress_at, next_step,
evidence_as_of, policy_ref
```

从实际Gateway/原件认证/机制/独立审查/报告提交事件写入；不要靠前端计时伪造步骤。持久状态作权威，实时推送作更新提示。摘要展示依据、假设、反证、概率判断及尚未解决问题，不需要暴露模型私有思维链。

`application/commit.py`摘要投影先从实际causal_case/行动/主反链/coverage引用生成结论＋关键依据＋反证＋触发/失效＋未解问题；停止原因由原Result单独展示。缺少论证时保持明确缺失，不把兜底措辞当有效方案。详情投影接入`packages/query_views/research/service.py`既有读取及确认快照。

PC默认展示中文阶段和可读成果；审计展开区才显示技术ID/hash。页面离开、刷新、网络超时后按原request/Run恢复，不重复POST；终态区给出报告、行动、失效条件与修订入口。尚未形成有效行动时明确当前缺口。

## 6. 实施片Q3：未解决根因续研

在现有持久研究任务上登记`topic_id, parent_run_id, revision, trigger_ref, unresolved_inquiry_refs`。每条缺口记录已有证据、支持/反对机制、下一合法数据路线与停止条件；已知不可得也保存为结果。

新问题先映射已许可来源/能力；确需扩展由服务生成新的冻结请求，不能让自由文本直接授权任意工具。每次续研重新冻结as-of、行情与预算；旧报告和预测不覆盖。旧材料可以复用，但新的行动发布必须核验当前有效报价、仓位/风险与反证。

“先提供可用暂定方案，再持续修订”必须以本次确有证据的行动为前提；不伪造胜率或把待核研究显示成已完成。后续Outcome和非种子Skill学习仍走原独立流程，不因后台化跳过。

## 7. 验收门与实施顺序

先完成现有core08正常核心链，再按Q1→Q2→Q3接线；每片先记录具体改动、合同、原件边界和失败样本。已有通过的组件不反复重测；只验证新增风险及真实联调。

| 门 | 必须实际发生的结果 |
|---|---|
| Q1-01 真正超过60秒 | 实际后台准备/取证完成可核阶段且墙钟>60秒，Host最终收到真实结果；不能用sleep/pending冒充 |
| Q1-02 断连与未知提交 | writer前/后断连分别恢复同一job/第一回执，改body拒绝，无第二次模型执行 |
| Q1-03 重启/取消/lease | 未完成步骤可恢复；旧attempt/过期lease/旧generation不能晚提交，显式取消有效 |
| Q2-01 返回页面 | 关闭并重开PC仍显示同Run阶段成果及最终报告，无重复POST |
| Q2-02 长推理/空转区别 | 合法请求在途>60秒继续；只有重复hash/同错误时诊断并留原因，心跳不续命 |
| Q3-01 续研及行情时效 | 新数据/反证触发具名版本，旧报告不改；过期报价不能直接武装当前行动 |
| 核心价值门 | 实际观点核验、主/反机制、操作/触发/退出、独立复核与Action仍各自有原件 |

记录实际成果丢失数、重复请求/模型数、恢复成功率、各阶段耗时、进展类型、漏预警/误动作和事前方案质量。预算策略依据这些数据调整；运行更久、文章更长或工程门通过均不等于预测效果已通过。


## 8. OP077实测约束修订与具体接线

### 8.1 先修真正已发生的整批传输截止

2026-10-14正常PC只提交一次ETH24h空仓研究，Run `run_e157b7f79e7c497598cb2057acd8bc30`。任务原件已冻结V2：total1800/step600/tool120/reserve900，原deadline09:28:53.919502Z。它不是一分钟研究能力不够：正常5次Gateway中4次完成、1次跨资产provider_timeout，baseline batch于09:02:00.885938Z真正提交，仍在原期限内；Host整批POST固定120秒却返回`research_baseline_transport_timeout`，Run09:02:30失败、模型输入0。第一原件/失败/截图均保存，不重复研究覆盖失败。

`host/baseline.ts:31,78–95`把整个五路取证、原件核验和最终delivery当成单项120秒传输。Q0先将这一整批pre-step等待与原冻结政策对齐，覆盖合法准备及传输，显式cancel与原绝对deadline继续有效；不能从失败推断空转，不先加新的短阈值。具体候选须检验旧协议行为、剩余原期限、更短冻结政策和显式取消。不修改已失败Task/Run，也不把batch已提交当模型已分析。

原件证据：[首次真实失败回执](../../../..//artifacts/decision-product-v1/w5w6-live-08/first-failure/receipt.json)；[独立边界审查](../../../..//artifacts/decision-product-v1/async-review-02/REPORT.md)。跨资产provider_timeout是另一条数据缺口；延长Host整批等待不能补齐宏观或跨资产证据。

### 8.2 Q1持久任务的两个身份和独立调度

持久原业务stage key和精确proof提交key分开：同stage可以在后来真实ProviderInput内重新认证，但同proof key改正文拒绝。记录`semantic_operation_key`、`proof_request_hash`、`original_occurrence_ref`、`provider_input_ref`及`stage_request_id`，重启仍消费第一真实业务回执；不能用全payload hash一概否决合法的后来原件。域stage冻结与job完成回执尽量在原writer同事务完成；若跨事务，恢复必须先认证已存在第一业务回执，禁止重复副作用。

不能排在等待DSH工具的同一个串行ResearchWorker tick之后。候选实现复用已有进程管理及repository方式，独立callback执行lane、有界领取；当前研究worker继续等原工具结果。每次领取有owner/attempt/lease/cancel_version fence，重启重新亲核未提交步骤，不把内存PreparedOriginals当权限。不借用EvolutionJob任意解释新工作。

Host接到202后只能轮询同job，不能交给模型当成功。SDK的600秒step目前包含其中工具/回调墙钟，Q1验收先覆盖真实超过60秒且在原step期限内的工作；更长操作需要原合同中的独立阶段期限或具名续接协议，不按heartbeat暗延长step。最终业务结果、模型可见证据和报告保存分别认证。

### 8.3 进展定义及用户可回看内容

三类时间分开：`last_activity_at`表示存活；`last_work_progress_at`表示真实操作完成；`last_research_progress_at`表示缺口/机制/反方/独立审查有具证变化。停止判定从已结束的工作回合和重复输入/路线判断，不要求每60秒产出结论。

新增研究增益必须来自：新独立Evidence、要求coverage变化、真实challenge状态关闭、主/反机制判断有新认证原件或独立审查完成。为每个增益存原件ref/hash与旧新状态；只有表述变化、同hash转载、重复同错、心跳不增加研究增益。合法请求在途按其冻结阶段期限等候；无进展且无在途请求时先定位，再换有依据的数据路线或收尾保存缺口。

PC返回同request/Run展示：当前阶段与在途工作、已完成取证和可读发现、支持/反对依据、未解问题、下一步、结果版本及证据时点。失败时也可回看已经完成的真实取证，明确模型是否实际读过；不能因为尚无报告就把原batch成果全藏起来。展示论证摘要与依据，不暴露模型私有思维链。轻量进度GET不做整套重型原件认证；完整方法核验和下载保留显式入口。

### 8.4 实施与验收次序

1. Q0当前baseline整批截止：候选边界门→保留旧原件→正常新Run实际进入Provider，避免再次只改总预算。
2. Q1持久callback：迁移线先与W7尚未安装的0096候选统一，不平行造两个同编号head；短接收、独立执行、第一回执恢复、显式取消及旧代晚提交拒绝必须同批贯通。
3. Q2用户成果：已封两源summary候选接线加旧投影保留；阶段归并和轻量状态另外验收。页面离开/重开读取同Run，0重复POST；当前PC进度读取失败原因正在独立定位，不假称已解决。
4. Q3主题续研：新数据/反证/行情时效触发新具名Run，保留旧方案；已有1800秒只是单次Run政策，主题研究可跨Run延续。长研究结束后仍检查当前Action的有效时点和报价。

评估记录有效工作耗时、模型/数据/本地认证分别耗时、超时后产物丢失、重复执行、断线恢复、用户回看成功及主线行动质量。门槛依据这些实测调整；无新限制可以用来替代证据、根因追踪或操作方案质量。完整原W0–W9/Outcome/非种子Skill与其它资产范围保持，整体未通过。
## 9. OP082：时间约束必须服务于研究结果用户再次确认：有用的思考、取证和根因追踪超过60秒正常；用户后续打开应看到保存的发现和方案。以下是本产品执行规则，不新增“每60秒产出一次结论”等质量限制。### 9.1 三类不同处理| 条件 | 行为 | 禁止混用 ||---|---|---|| 页面等待或单HTTP到期/断线 | 显示提交或结果待确认，按同request/job查原回执；后台按原期限继续 | 不能据此标业务失败、重复研究或撤销未提交准备 || 合法Provider/工具/本地原件认证仍在途 | 显示等待对象、开始时间和最近真实阶段；没有新证据不直接算停滞 | 心跳不证明研究进展；模型完成也不证明行动结论有效 || 已结束工作反复使用同输入/hash/同失败路线，没有新目标和有效产物 | 先排查传输、权限、来源和已提交回执；有依据才换路线，否则保留缺口并收尾 | 不用固定一分钟或措辞变化代替空转诊断；不删除已完成成果 |时间期限、费用和调用数继续冻结在每次Task政策中。当前1800/600/120/reserve900是试行值，不能作为所有未来资产/深度研究的固定最优值。取证、综合、独立审查和提交分别记录实耗；需要较长任务时在开始前选合适政策，或用具名续研版本，旧Run不暗延长。研究主题可跨版本持续，发布当前行动须重新核验报价、证据时效和失效条件。### 9.2 真实代码边界与候选接线- 正式同步`research_skill_routes.py`的60秒checker及断线Event仍可能拒绝有效未提交准备；`hub-client.ts`仍有对应skill60秒传输。Q1已封11源候选，将短POST接收、专用持久job、独立worker lane、GET首结果与原writer同事务贯通，HTTP结束不取消job。**未安装，不宣称已修复。**- Q1已有24项Python机械控制、另1项lane异常恢复控制、32项Host控制；真实原件、正常SDK超过60秒、重启和PC仍未通过。严格TSC的现有DSH peer类型缺失保留。详细边界见[候选交接](../../../../artifacts/decision-product-v1/durable-callback-01/HANDOFF.md)和[具体时限风险](../../../../artifacts/decision-product-v1/durable-callback-01/TIME_POLICY_READBACK.md)。- Q1新lease120秒是所有权维护，不是研究120秒截止；续租20秒独立。仍有真实写事务持SQLite锁超过lease导致续租无法提交的风险，W7/Q1联合原件门必须测准备时间、持写锁时间及owner fence。不得删fence掩盖；先缩短实际写事务，仍需更长时冻结合理lease。- 现600秒step watchdog覆盖DSH step墙钟，含工具/回调等待。不能将它描述成纯思考时间；Q1后台化不会自动绕过原step期限。独立阶段预算/续接若需要须有合同和实测，再改变新任务政策。- 连续两轮增益门仅认accepted Evidence和coverage；同证据下的真实challenge闭合、主反机制核验可能未计入。Q2以真实事件原件ref/hash、旧新状态和独立完成身份扩展计量；模型自报进展、同hash转载或改措辞无效。需同时覆盖正常Graph权威投影，不能只改示例分支。- PC已能离开后按原Run回看证据，现进度的证据数/工具数/心跳不足以解释分析阶段。Q2展示“当前核验对象、已确认发现、反证、缺口、下一步和方案版本”，技术ID/hash放审计区，用户正文不展示私有思维链。### 9.3 当前唯一正常Run的实际证据`run_397fb39114704bf5bf4825ca5db3508e`仅PC提交一次，原deadline为2026-10-14T10:51:09.243530Z。10:38:38只读快照显示8个模型完成原件，其中7个submit completed、1个`dsh_provider_failed`；真实baseline批次、后续Gateway、loaded与两个node proof都已保存。完成原件不能直接证明最终结论；Provider失败尚未核到具体根因，不能仅按约60秒时间关联归因于callback。未形成可验收Action，不把运行更久算业务通过。验收按三个结果分开：**有效工作能持续、成果不丢且可回看、形成具证事前行动并可独立结算**。分别记录真实在途/准备/写入耗时、传输超时恢复、重复模型或stage、成果丢失、未解缺口和行动质量；不以等待更久、文本更长或机械控制数量代替产品价值。### 9.4 OP083：本次真实终止，不能靠增加等待掩盖后续认证原件已明确本次阻断：`dsh_authenticated_terminal:1`的error message为`DSH turn failed (SKILL_CALLBACK_TRANSPORT_TIMEOUT)`，原SHA为`8f06e3b4bae744e0ee5ca9f651397035ca80700bae6d4547d5b795ac8df7d1e9`。这是本次SDK终止的已认证技能传输边界；先前10:34:12的独立Provider错误仍不能据此补断为同一原因。15个首次原件保存在[first-dsh-terminal](../../../../artifacts/decision-product-v1/w5w6-live-09/first-dsh-terminal/receipt.json)。本次共11个模型输入、10个finish，其中9 completed/1 error；selected、loaded及两项executed保存。DSH于10:43:46结束；Graph final在10:47:20保存，独立reviewer角色原件10:49:09保存，但不代表审查有效。原Task期限10:51:09未暗延长，最终Run10:51:48以`research_worker_failed`失败，`artifact_id=null`，没有完整报告或Action，30m/24h/72h action hooks均exhausted。8条证据仍能在PC回看。PC最终主原因显示“报告原件核验超过任务时限”，而首次认证阻断是技能传输超时。**两个阶段分别整改**：Q1持久callback解决有效准备被短HTTP中断；W7/Q1合并应消除重复认证并量测原件准备/持写锁/收尾时间；Q2必须同时显示首次阻断和后续收尾错误及原引用，不能让最后错误覆盖首因。技术详情中的“必需覆盖100%”不能掩盖7项补充宏观/跨资产/公开信息缺口，也不能代表因果、反方或行动已经完成。Q1 Host两源已另外用实际当前Host及保留的官方SDK声明严格核验：263源，0诊断，候选/正式before/SDK原字节前后一致，无emit或正式安装。原私有环境缺peer失败保留，新增[类型收据](../../../../artifacts/decision-product-v1/q1-host-current-types-01/receipt.json)SHA为`71c3532cfc1f1e0a1c53f75615a4356ad83bc358558bc172f4f57f8d652fcf7a`。此门仍不能替代真实后台任务、重启、完整原件和PC验收。独立审查另确认：Q1候选续租遇临时SQLite锁/IO时立即lost，也可能误杀尚有效的准备。补修原则是只能在最后真实确认的lease未到期期间做可重做准备，绝不假续期；确认owner/epoch/generation/cancel变化或原期限/lease到期立即停止，写入仍亲核完整fence。新候选控制尚未完成，以后续封存和联合验收为准。## 10. OP085：有效长研究、结果回看与真实执行边界### 10.1 采用的行为规则用户2026-10-14再次强调：后台研究超过60秒正常，只有无有效追踪和产物的空转才值得诊断。**不新增每60秒交结论、按心跳续研究期限或按字数判断质量的规则。** 并非现有每个环节都已异步：正式技能路由仍有同步等待，Q1持久任务候选尚未安装；必须完成API、Host、SDK工具、worker、数据库和PC的同批接线。| 实际状态 | 服务端处理 | 用户后续打开看到什么 ||---|---|---|| HTTP观察到期/页面离开，但原job有效 | 观察结束不cancel job；按同request/job查询首回执，未知POST只重发同字节 | 结果待确认、原任务及已保存材料；不显示研究失败、不重复启动 || 合法模型/工具/原件核验仍在途 | 记录真实开始/完成事件，沿本次已冻结任务及阶段期限继续 | 等待对象、开始时间、最近确认的成果；不把“暂时无新证据”当空转 || 已完成工作有新反证闭合/独立复核 | 原件认证后计研究增益，即使证据条数没变 | 支持与反对依据、已解问题、剩余缺口及方案版本 || 已完成回合重复同输入/同失败路线且无新目标 | 排查已提交结果、来源、传输与能力；有依据才换路线，无法推进则保存具名缺口 | 明确卡点、已完成发现和下一合法步骤；不靠轮询/改措辞伪造进展 || 显式取消/冻结期限耗尽/真实失权 | 拒绝新副作用及旧attempt晚提交；保留之前真实成果 | 原生首因与收尾错误分列，已有证据仍可回看；需要继续则新具名研究版本 |用户回看的内容是**可核实的论证、依据、假设、反证、概率判断、操作触发/退出及未解问题**。保存私有模型思维链不是验收要求；尚未形成行动时不能把证据列表包装成操作方案。主题可跨Run持续，发布新行动须重新确认报价/证据时点。### 10.2 本轮代码审查又找出的真实65秒限制`w7-integration-02`中的`extensions/dsh/decision-hub/src/skill-tool.ts`仍注册`timeoutMs: 65_000`。当前SDK的`core/tools/schema.ts`仅接收static number，正常加载的`guard/timeout-policy/src/index.ts`会据工具配置改变执行signal并产生`TOOL_TIMEOUT`。因此只改HTTP为202+poll仍不够：合法job超过65秒时，后台可以继续，但模型观察者可能已被SDK截断，不能算主线修复成功。这与live09已认证的`SKILL_CALLBACK_TRANSPORT_TIMEOUT`是不同边界，不反推旧失败来自SDK65秒。新增`skill-tool-time-policy-01`必须先核SDK默认/fallback和实际owned correlation，保留旧同步行为；durable工具等待沿真实冻结Task/step及显式取消结束，不另加65秒上限，也不无界脱离Run。若SDK不支持动态期限，使用其真实支持的机制与Host既有冻结期限组合，禁止臆造续期接口。窄控制与之后正常Provider实测分开记录。### 10.3 当前已封候选与未通过门- W7/Q1母02的108target及03的6target已封，合110unique；完整Host实际443源严格检查0诊断。03的外层source HANDLE延续到真正COMMIT后关闭，RO作用域先成功结束再进writer，内嵌trial仍完整认证。实际source-only准备5.400秒、私有SQLite持写锁978.065ms仅说明该源接口；producer及nested认证成本未被这些数字覆盖，不冒称完整回调已快到这个程度。- Q2新02九源已封，真实native变化的review/challenge IDs通过server lineage对应；具证challenge关闭和首次独立复核可计增益，同issue重开/重复及资格丢失后恢复不刷增益。31项控制与独立源审查通过，**正常SQL/Provider/最终行动仍未通过**。旧01仅采用两份PC候选，旧01两份Python语义实现不装入本批。- Root装配器已实际核122unique候选目标及formal before零漂移，保持03叠加的母hash与原正式before分离；尚缺SDK65秒修片和原生首因DTO，不冻结为完整可安装发布。正式安装、正常build、0096迁移、新Run及PC均未执行。- 首因投影正补`first_native_failure`、`current_authenticated_native_failure`与coverage。当前Link不能认证旧generation；历史绑定不齐则明确earliest unknown/coverage incomplete，不能把当前错误冒称所有generation最早错误。轻量poll不塞全Graph核验，历史展示不授执行权。### 10.4 具体验收及下一步1. 完整候选同批安装前，先合SDK工具时限、原首因安全DTO/PC与既有W7/Q1/Q2，正规生成合同并保存所有将覆盖的old09原字节。只核新增冲突和必要发布门，不重跑无关宽套件。2. 正常环境记录HTTP observer、SDK工具、Provider、prepare、写锁、提交/独立审查/投影分别的实际耗时及截止原因。必须真的完成有用工作跨60/65秒并让Host收到原结果；sleep、pending或机械clock不能冒充此门。3. 关闭再打开同一PC Run可读已完成成果与最后方案，零重复POST；失败时首因/收尾分别出现，数据不丢，未知历史范围明确。未知提交/重启恢复同一首结果，旧attempt不能再写。4. 依据实际各阶段时间配置**下一次任务**的预算或具名续研；不修改已失败Run的冻结期限。600step/1800total/900reserve/120lease是当前试行参数，不作为所有研究的固定质量准则。5. 主线仍须真实观点与事实/反方核验→机制→操作/触发/退出→Action→监控/修订→独立Outcome→非种子Skill→未来正常消费。长研究、回看或工程通过均不能替代这些价值门；原W0–W9范围保持，整体未通过。## 11. OP089后的真实安装反馈与最小修复正常10已安装133源、正规构建并升级原库0096；API/DSH已就绪，未提交新研究。前节的“候选尚未安装”属于当时记录，不代表当前安装状态。真实Worker启动新发现 `apps/hub_worker/installed_harness_bootstrap.py::_official_install_guard` 对完整33,344源安装校验设置了固定 `subprocess.run(timeout=90)`；两Worker校验未结束就被终止，随后重复重启，尚未到研究或技能DI。### 11.1 各种期限必须分别说明| 边界 | 含义及本次处理 | 不可当作什么 ||---|---|---|| 页面/HTTP约60秒观察 | 返回等待状态；继续查询同任务/首结果 | 不可当研究失败或重新POST理由 || durable技能原65秒SDK工具限制 | 已安装修片：沿真实Task/step/取消；正常Provider仍待验 | 机械时钟测试不可证明真实长任务成功 || 安装认证固定90秒 | 本次最小候选移除错误墙钟上限；同子进程完整返回、proof和前后源SHA全部保留；正常shutdown停止实际子树 | pending/启动日志不可当通过 || Worker lease120秒、续租20秒 | 所有权机制；必须是真续租，失权不再提交 | 心跳不可延长研究期限或刷质量进展 || 当前Task1800秒、model step600秒、trial reserve900秒 | 试行冻结预算；记录各阶段实际耗时后评估下一次Task配置，不能中途改已签发预算 | 不可声称所有硬限制都已取消、也不可把所有研究一律限60秒 || 取消/原任务截止/失权 | 停止新副作用、保留既有材料与当前缺口，具名续研是新版本 | 不可无限续同一旧任务或丢弃已提交成果 |### 11.2 有效进展与空转的判断存在已开始的合法模型、检索或原件核验时，暂时没有新证据只表示“工作在途”。已经完成的回合，才比较真实来源/问题/证据/反证和方案变化。新反证、被排除的解释、独立复核、行动触发变化都能构成增益；重复同来源同输入同失败路线且无新目标才进入诊断。只改变时间戳、心跳、字数、措辞均不算增益。当前Q2有限固定问题谱系已安装，任意长主题及真实最终行动仍需单独验收。### 11.3 用户回看及验收同Run展示已保存的事实/来源时点、支持与反对机制、已解与未解问题、概率依据、方案版本、触发/退出及具体失败原因；页面离开和HTTP观察结束不重复启动。旧09正常API已返回认证子因 `skill_callback_transport_timeout`，但真实Report仍只读旧 `dsh_turn_error`，已保存PC差异截图，本次补两文件最小展示候选。实际正常新Run须跨60/65秒完成有用工作、返回同首结果并能再次打开回看，才能通过这一验收。执行见 [最小11计划](../../../../artifacts/decision-product-v1/core-release-11/PLAN.md)，首败原件和逐步操作继续追加原执行台账。整体及金融效果仍未通过；独立Outcome/自动非种子技能/未来消费与其他资产准入保持原目标。