# 可执行产品规格 V1：架构、合同、持久化与执行时序

日期：2026-10-13。**本文定义目标架构；各对象、表、路由与接线的实际完成状态见tasks.json及EXECUTION_LOG.md，不以本文出现名称即视为已实现。** ROOT = `E:/project/study/codex/dsh/decision-hub`。本文与同目录参考规格配合使用，参考实现通过不能记成生产或金融验收。

## 1. 范围与唯一主链

首个纵向切片使用已准入的 BTC/ETH 现货，读取真实冻结研究和现有价格事实；保留30m/24h/72h研究与独立7d基线。首版动作统一enter_long/reduce_long/exit_long/hold/wait，仓位上下文flat/long/unknown；enter_short/hedge及已有空仓分支留给后续独立合约scope，不列为首版可用。首轮只登记一个主要增强候选，具体参数、成本与评价窗口在实施计划中冻结。

`Opportunity → DecisionSnapshot → ActionPlan不可变版本 → 持续条件求值/DecisionRevision → ActionOutcome → Reflection → SkillVersion → 后续SkillUseReceipt`。
Opportunity先登记进入全分母；没有研究、无优势、资料不足、过期和漏接均留账。人工建议、纸面模拟、用户自报采纳、真实交易订单是四个对象；本方案不接交易所下单。

## 2. 最小模块结构与依赖

以下是新增模块的职责边界；现有项目使用 `decision/` 存纯决策函数，不新增另一个domain目录。
```text
contracts/schemas/decision_product_v1.schema.yaml         # DTO单一来源
packages/kernel/decision_hub_kernel/
  ports/action_plan.py                                   # Generated DTO导出、Reader/Clock端口
  decision/action_policy.py                              # 建议等级、条件真值、转换与费用计算
  application/action_plan.py                            # 发布/修订/命令/幂等，调用纯决策
  application/action_monitor.py                         # claim→快照→求值→fenced提交
  application/action_outcome.py                         # 到期标签/动作结算/反思候选
  persistence/action_records.py                         # 7张行动表、2张技能索引表
  persistence/action_repository.py                      # SQL读写；调用者拥有事务
packages/query_views/decision_product.py                 # 有界用户投影
apps/hub_api/decision_routes.py                          # GET和owner命令
apps/hub_worker/action_tick.py                           # 既有RealtimeWorker内两个tick
apps/decision-desk/src/api/decisionProduct.ts             # 生成Zod校验
apps/decision-desk/src/product/ActionPlanPanel.tsx         # 当前方案/条件/更新/结果
migrations/versions/0093_decision_product_v1.py           # 拟编号，实施前核head未被其他改动占用
```
依赖方向：API/UI → query或application → ports/decision + persistence；decision不得导入SQLAlchemy、DSH、HTTP或第三方数据SDK。Provider将结果变成既有Fact/Price DTO；composition注入reader/clock/dispatcher。Repository不commit、不隐式打开第二个session。DSH负责模型/工具/技能加载；Graph负责研究生命周期；Hub负责事实、行动状态和结算。不得新增第二套Run、模型循环或任务平台。

## 3. DTO：最小字段与精确语义

研究侧新增ClaimSignal、MechanismReview和ReflectionDelta的合同、文件与现有原件存储接线见[VALUE_ALIGNMENT.md](VALUE_ALIGNMENT.md)§2–3；这三类采用现有role原件，不新增并行记忆数据库。它们分别绑定外部观点、可核主反机制及结果驱动的候选修正，Action和Skill必须保留引用。

所有DTO `additionalProperties=false`；时间为带时区RFC3339并规范UTC；金额/价格/收益使用规范十进制字符串，概率value为有限JSON number[0,1]或null，禁止NaN/Infinity；ID非空且有长度上限；hash为64位小写SHA256。V1先消费既有UTC现货日历，扩展市场引用版本化Calendar/Horizon，不复用全天1440根假设。
| 对象/版本 | 必需字段与类型 |
|---|---|
| Opportunity V1 | id:str；key:str；scope_id:str；horizon:enum[30m,24h,72h]；kind:enum[scheduled,event,condition]；event_ref:str?；registered_at/time_start/time_end:datetime；target_spec_hash/recipe_hash:str；cohort:enum[prospective,diagnostic]；status:enum[open,decided,missed,expired]；reason:str? |
| DecisionSnapshot V1 | id/opportunity_id/run_id:str；source_result_ref/hash:str；decision_cutoff_at/known_at:datetime；scope/horizon:str；hypotheses:list{direction:long/short/range, evidence_refs:list[str], counter_refs:list[str], rationale:str}；factor_refs:list{ref,hash,unit,known_at}；direction_status:eligible/unknown/blocked；path_status:qualified/experimental/unknown；probability:{target_id:str, value:number[0,1]或null, kind:subjective/calibrated/unavailable, provenance_ref:str?}；gaps:list{scope,reason}；skill_refs:list{version_id,hash}；snapshot_hash:str |
| ActionPlanRevision V1 | revision_id/plan_id/decision_id/opportunity_id:str；revision_no:int>=1；previous_id:str?；scope/horizon:str；as_of/published_at/valid_until:datetime；action:enter_long/reduce_long/exit_long/hold/wait；position_context:flat/long/unknown；advice_qualification:research_trial/qualified/blocked；decision_status:actionable/no_edge/unknown/blocked；entry/exit/invalidation:list[Condition]；size_policy:{kind:unspecified/fixed_scenario/user_budget, amount:decimal?, unit:str?}；cost_policy_ref/hash:str；target_spec_ref/hash:str；basis_refs:list{ref,hash}；reason:str；payload_hash:str |
| Condition V1 | id:str；purpose:entry/exit/invalidation/risk_upgrade；expression:bounded AST；min_fresh_samples:int；max_age_seconds:int；evaluation_seconds:int；calendar_ref:str；valid_until:datetime；original_text:str；input_requirements:list{metric,scope,source,unit} |
| Condition AST V1 | {op:compare,feature:str,scope_id:str,unit:str,cmp:gt/gte/lt/lte,threshold:decimal-string,max_age_seconds:int}；或{op:all/any,args:list[ConditionAST]}；每节点最多16args、全树深度最多8、叶子最多32；白名单feature及计算lookback由recipe manifest声明；没有eq/eval/python/shell或自由函数 |
| Evaluation V1 | id/plan_id/revision_id:str；tick_at/as_of/evaluated_at:datetime；lease_generation:int；input_ref/hash:str；truth: true/false/unknown；status:ok/error；per_condition:list{id,truth,status,reason,input_refs}；state_before/state_after:str；evaluation_hash:str |
| ActionOutcome V1 | id/opportunity_id/plan_id/revision_id:str；version:int≥1；due_at:datetime；mode:paper/user_reported；status:pending/scorable/unscorable/settled；protocol_ref/hash:str；visible_at/trigger_at/entry_at/exit_at:datetime?；price_basis:trade/bid_ask/mark；entry/exit/gross_pnl/cost/net_pnl/mae:decimal?；coverage:decimal；price_refs:list{ref,hash}；baseline_outcome_ref:str?；exclusions:list[str]；settled_at:datetime?；payload_hash:str |

DecisionSnapshot记录研究原假说，不使用commit生成的no_trade/0.5冒充市场判断；只有通过引用、来源、时点、scope与可信合成检查的部分才可成为人工方案。缺概率资格不自动抹去规则或条件建议；关键价格不可用阻断相关价格动作；no_edge是实际判断，unknown是证据状态。
Condition结果使用三值：AND只要false即false，否则有unknown则不可确认为true；OR只要true即true，否则有unknown则不可确认为false；scope/单位错误同时记录status=error且truth=unknown，stale/future/缺失记录truth=unknown及原因。旧risk evaluator的error桥接为unknown+error状态；每个缺口保留。同一旧bar不能累计为多个连续确认样本。
Reflection原件使用既有不可变 `RunRoleOutputRecord` 独立role及ref/hash；SkillVersion使用既有research_recipe组件和原件archive。技能使用事件与经验桥另加两张小索引表`research_skill_use_events`、`experience_case_skill_links`，具体合同见SKILLS_AND_REUSE；不复制另一份经验正文库。DSH实际安装/加载由其适配器完成，Hub只登记版本、资格与真实加载回执，不写全局活跃技能目录。

### 参考规格子集与生产适配映射

`spec/action.schema.json`与`spec/reference.py`只实现Action DTO和机械规则子集，版本为`decision-action.spec.v1`；不包含整个Opportunity/Source资格/DSH/Alembic/owner身份/长期经验流程，不能直接作为已安装API合同。生产映射显式生成，禁止对additionalProperties=false对象塞入扩展字段。
| 参考字段/行为 | 拟生产适配 |
|---|---|
| plan_id/revision/scope_id/horizon_seconds | plan_id/revision_no/scope_id；1800/86400/259200分别映射30m/24h/72h；revision_id由原身份生成 |
| action/position_context/advice_qualification | 枚举保持；enter_long要求flat，reduce_long/exit_long要求long；unknown只展示兼容的hold/wait及仓位缺口，不猜持仓 |
| probability.kind/target/value/calibration_ref | target=return_positive或target_before_stop对应明确target_spec；unavailable→value/ref均null，subjective→有限value/ref=null，calibrated→有限value/具名ref；不把策略成功率混入；qualified不能只由模型自称 |
| condition/evidence_refs/policy_hash | condition对应本revision的一个具名purpose；其余entry/exit/invalidation逐项用相同AST；evidence_refs只作引用ID，生产补取ref/hash与时点资格；policy_hash绑定冻结配方 |
| 内存SQLite publish/CAS/幂等/outbox | 证明单事务机械语义；生产增加owner、租约generation、真实原件复核及上述迁移/通知第三subject |
| transition/evaluate | proposed→armed→triggered→completed；armed到期expired、triggered到期completed；true/false/unknown三值，scope/unit错配抛ContractError，生产捕获为truth=unknown+status=error |

参考与生产共享AST边界、动作枚举及状态语义。只有动作/条件子集可按适配器往返；尚无生产字段的参考对象不能证明真实source验证、发布/可见或金融资格。

## 4. 状态机：建议、监控、模拟、采纳与结算

| 对象 | 合法转换/原因 | 绝不能推断 |
|---|---|---|
| 建议head | proposed→armed：必需证据有效且条件计划已保存；proposed→invalidated：发布前拒绝；armed→triggered：真实新输入满足entry或即时风险动作有效；armed到期→expired；triggered结算或到期→completed；armed/triggered→invalidated或superseded：失效或新版本替换 | triggered/completed不等于成交或盈利；告警sent不等于用户看到 |
| 监控运行状态 | active↔paused；active→degraded：关键输入过期或连续错误；degraded→active：新合格输入恢复；任意→stopped：建议终止 | degraded不解除原风险、不变no_edge、不自动恢复过期计划 |
| 纸面执行 | not_entered→open：方案已可见、条件成立后首个合格可执行报价，按冻结fill policy；open→closed：退出/目标/风险/期限规则；missing→unscorable | 不使用触发前最低价；不宣称真实账户盈亏 |
| 用户自报采纳 | not_reported→reported_open→reported_closed，由owner具名命令；允许corrected追加版本 | 页面点击/条件满足不写actual_fill；自报未经券商认证 |
| 结算 | pending→settled或unscorable；不足保留具体缺口；后补结果创建新版本并保留首个结果 | later bar/修订不覆盖首次截止和输入，不回填事前成绩 |

每个计划分支只对应一种适用仓位；同Decision可发flat/long/unknown三个分支但共享Opportunity，不能当三个独立预测样本；unknown不是空仓。严重风险可立即向已知long上下文发布reduce_long/exit_long人工建议，不等待入场；实际执行仍分别记录。建议终态不可原地重启；新信息产生新revision，已有模拟持仓退出规则不能被新建议静默重写。

## 5. 表、索引与唯一键

拟新增7表；不可变JSON原件随ref/hash校验，小型可查询字段单独索引。原报告、Forecast、Outcome和既有市场风险表不改历史语义。
| 表 | 核心列及约束/索引 |
|---|---|
| decision_opportunities | id PK；key UNIQUE；scope/horizon/recipe_hash/target_hash；registered/start/end/status；INDEX(scope,horizon,start_at)；预登记不因研究失败删除 |
| decision_snapshots | id PK；opportunity_id/run_id/source_hash/payload_json/hash/known_at；UNIQUE(opportunity_id,source_hash,recipe_hash)；INDEX(run_id,known_at) |
| action_plan_revisions | revision_id PK；plan_id/revision_no/decision_id/previous_id/position_context/payload_json/hash/published/valid_until；UNIQUE(plan_id,revision_no)；INDEX(decision_id,position_context) |
| action_plan_heads | plan_id PK；current_revision_id/state_version/state/monitor_state/next_due/valid_until；lease_owner/generation/expires；last_input_watermark；enter_streak；INDEX(monitor_state,next_due,lease_expires_at) |
| action_condition_evaluations | evaluation_id PK；plan_id/revision_id/tick_at/input_hash/payload_json/hash；UNIQUE(plan_id,revision_id,tick_at)；INDEX(plan_id,tick_at) |
| action_outcomes | outcome_id PK；opportunity_id/plan_id/revision_id/mode/protocol_hash/version/status/due_at/payload_json/hash；UNIQUE(plan_id,revision_id,mode,protocol_hash,version)；INDEX(status,due_at) |
| action_command_receipts | (owner_id,request_id) PK；operation/payload_hash/response_json/accepted_at；相同键同内容返回原回执，内容冲突409 |

W1存储对齐：内部实体ID最长128、scope最长192；原件定位符独立限制。Head.last_input_watermark为最后处理的独立闭合bar的UTC时点（date-time|null），不是哈希或收到时点。Outcome的version/due_at为必填并进入原件hash，后补结果追加版本。每Condition的连续确认状态由W3逐项维护，单个head.enter_streak不能代表不同条件的确认阈值。

显示/已读事件首轮复用action_command_receipts的具名operation，服务器保存接收时刻；客户端时间只能作诊断。两次不同display命令不得改变首次实际可见时间。
即时修订通知须新增明确的 `action_revision` subject：现行 `notification_envelope.schema.yaml:14` 与 `OutboxRecord db.py:1300–1309`仅支持report/risk_alert，必须同时扩展schema、sources构造器、outbox dispatcher和CHECK约束；不能伪装risk_alert或report。原subject行不重发；dedupe_key=`action-revision:{revision_id}:{transition}:{channel}`。
迁移拟接0092之后，用Alembic显式建表/索引；SQLite修改outbox CHECK用受控batch迁移，副本迁移保留row/attempt/claim identity。有历史行动/通知时downgrade拒绝销毁，运行时回滚使用旧组件指针而非删除证据。

## 6. API与PC交互

拟新增路由；GET均有界、无模型/写入/完整历史材料审计。命令复用现有owner验证；未开启产品能力时返回明确资格。
| 请求 | 输入/输出 | 错误 |
|---|---|---|
| GET /v1/decision/scopes/{scope_id}/current?horizon=24h | CurrentDecisionView：scope/as_of/opportunity/decision/各position分支head+revision/数据时点/监控/最新调整；最多1当前Decision、3分支 | 404 scope_unknown；409 current_basis_unverifiable；无Decision返回typed unavailable，不用500 |
| GET /v1/decision/plans/{plan_id}?revision_id=… | 精确原revision、当前head及最多20次evaluation/调整，next_cursor | 404 plan/revision_not_found；409 hash_mismatch |
| GET /v1/decision/opportunities/{id}/outcomes | 全部分支纸面/自报结果及排除原因、基线ref；typed pending | 404 opportunity_not_found |
| POST /v1/decision/plans/{id}/commands | {request_id,expected_revision_id,expected_state_version,operation:pause/resume/display/ack/report_adoption/report_exit,payload}→{request_id,status:recorded/current_conflict,server_time,head,original_receipt_ref} | 401 owner_required；409 command_key_conflict/revision_superseded/state_version_conflict/plan_expired；422字段/单位/时间非法 |
| GET /v1/decision/commands/{request_id} | 当前owner原回执或typed not_received；提交未知时只读核对 | 404 typed not_received；不据此自动换键重POST |

系统生成初始计划/修订由Worker内部service调用，API不接受用户或LLM任意整份Plan上架。PC先显示动作/适用期限/条件/当前资格及最近改变，按需展开主反根因/因子/来源/原件；概率不可用显式未知。新Action视图是增量，不覆盖现有确认报告或7d基线。

## 7. Worker时序、事务、取消与版本

1. 预登记Opportunity：按固定机会协议生成key与窗口；同键重放只核原件，不移动deadline。所有到期未决机会写missed/expired并进入分母。
2. 深研究使用现有DSH/Graph冻结结果；ActionPlanService读取可信Graph/quality/source refs，在事务外构造Decision与Plan候选，再开短写事务核原件版本/资格/当前head并原子保存。原报告发布/ACK继续由既有publication执行，不冒充新Action资格。
3. ActionMonitor claim短事务：领取一个due head，generation+1并写租约；事务外读固定as_of快照、验证Source/单位/freshness并运行纯函数；不调用模型。
4. complete短事务：核owner/generation/lease/revision/state_version/watermark；插evaluation→CAS head→必要不可变revision→outbox同事务提交。CAS rowcount不为1则旧owner退出，不再发通知。
5. 不同revision/invalidation优先级写死：无数据=unknown；scope/单位错配=unknown+status:error；已到期未触发先expired，已触发到期按协议completed；有合格失效证据先invalidated；再检查exit/risk_upgrade/entry。每个分支保留原因，不以未知输入解除风险。unknown只属于求值/资格，不是建议终态。
6. Outcome tick以固定due_at取到期机会；事务外读取完整价格/成本原件，计算各模式标签；短事务核protocol/ref/hash和唯一键写结果+Reflection候选。缺价格/成本/同bar先后不明=unscorable，不强造赢亏。
7. 在现有RealtimeScheduler追加action_monitor(60s cadence、拟30s有界tick)和action_outcome(60s、拟120s)通道；每tick最多2个scope/4个due结果，正常composition注入。不得每个技能新启进程。
8. cancel不能杀掉to_thread的SQLite工作；参照PathShadowTick现有shield/join，在所属有界任务真正退出前不发下一次tick。捕获真实SQLITE_BUSY使用既有sqlite_busy分类；失败保留，不能无界重试或提高原研究预算。
9. API命令采用owner+request_id+payload_hash；修订采用plan_id+expected_revision_id+state_version；tick采用plan+revision+slot；结算采用plan+revision+mode+protocol+version。所有重放取原结果，内容不同明确冲突。
10. scope、horizon、skill、recipe、condition、fill/cost、outcome protocol均独立版本/hash；当前指针不重算旧报告/模拟成交/标签。任何跨版本迁移生成显式新对象及supersedes关系。

## 8. 与现行源码的具体接线

| 现行文件/行 | 已有职责 | 拟改造 |
|---|---|---|
| tools/contract_codegen/__main__.py:19、:740；generate_ts.mjs:12 | 显式Py schema→output map、TS sources及generate/check/compatibility | 登记decision_product_v1 schema；生成Py/TS与manifest，禁止手改generated |
| persistence/db.py:1703–1724；migrations/env.py:8 | ORM模块登记；正常启动Alembic升级head | import action_records，新增迁移；不是API请求里create_all |
| persistence/risk_records.py:36–154；application/risk_monitor.py:179–330 | 风险claim、generation、四值、evaluation+alert+outbox | 复用模式并显式桥接三值Action结果，V1旧close_return规则原样；Action表接自己的revision和组合条件，不让旧DTO吞新字段 |
| application/commit.py:290–325、:2184；query_views/research/service.py:1809–1856 | 研究/Forecast发布及7d市场投影 | 新人工Action由可信原Graph假说独立派生，保留各资格；不删除原Forecast保护、不把0.5当原p |
| apps/hub_worker/research.py:1915；composition.py:748–780 | 研究完成观察钩子与案例输入 | 增量调用action service并保存失败回执；Action失败不伪造成报告失败或再跑模型 |
| application/scheduler.py:385–390；composition.py:455–567 | 现有风险/记忆/增强独立通道 | 注入两个Action tick，快速风险不等待全部DSH专科 |
| apps/hub_api/risk_routes.py:125–185；main.py:494–539 | Router注入、GET投影、owner命令/回执 | 新decision_router单独include；404/409/422按具名错误映射 |
| ports/market_path.py:42–79；decision/path_risk.py:1 | scope/价格原件与确定性路径计算 | 首轮复用现货Reader；新交易日历先独立合同，不混USD/USDT/mark/trade |
| application/outbox.py；notification_envelope.schema.yaml:14；db.py:1300 | 已有claim/投递与两subject身份 | 增量action_revision第三subject，明确actual_visible/ack独立 |
| apps/hub_worker/market_case_context.py:535；application/evolution_context.py:53 | 现有案例选择、候选上下文 | Reflection/Skill bridge先接有真实结算的一例；选中/加载/消费分别记录，不把0 Fact判失败 |

## 9. 本规格的可验对象与开放问题

规格校验只证明DTO、状态机、幂等、时间及结算例子一致；应含：当前满足/未满足/反证/缺数/过期、重复命令、旧owner、旧revision、同bar双触、费用缺失、用户自报与纸面分开、后发方案不能获先前价格。
生产门另验：一个真实Opportunity→可信Decision→可读Plan→连续求值→真实新信息修订→实际可见→到期结果→后续真实技能消费；BTC/ETH均武装，真实未触发不伪造触发。效用比较按独立Action协议，全部机会分母和未知保留。
首个ActionPolicy参数/基线与候选、全量机会日界、fill/cost可得来源、Action协议有限终点、DSH确切skill加载ID/hash回执接口由同目录总IMPLEMENTATION统一封定；架构与参考适配必须引用该版本，开发者不能另猜参数或用fixture报金融效果。

