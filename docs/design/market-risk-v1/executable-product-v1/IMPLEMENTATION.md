# 开发任务、首轮参数、运行约束与交付顺序

日期：2026-10-13。这里规定工作包与结束门，**实施状态单独以tasks.json和EXECUTION_LOG.md为准**。设计参考和定向测试不能写入生产功能通过率。本文与[架构](ARCHITECTURE.md)、[评价协议](EVALUATION.md)、[技能和复用](SKILLS_AND_REUSE.md)共同组成一个规格包。

本版结束门同时包括[核心意图I01–I12](VALUE_ALIGNMENT.md)：W2必须实现外部观点/主反机制/搜证到Action的接线；W7必须由真实结果自动生成非种子技能候选并证明后续真实消费。I07/I08外部观点门不能由官方数值源替代。下面W表与tasks.json合并读取，任何一组门未过均保留未完成状态。

## 1. 不再留给开发临时决定的事项

| 决策ID | 本版决定 | 变更条件 |
|---|---|---|
| ADR01 | 继续Python/FastAPI/SQLAlchemy/SQLite、现有DSH与Graph、React；一个Run/持久任务终态 | 有具体测量证明现有结构无法满足门槛，才提出替代，不先换框架 |
| ADR02 | 先完成BTC-USDT/ETH-USDT现货纵向切片，主要行动窗口24h；30m/72h研究保留、7d仅独立参考 | 新市场按绑定准入；没有复用BTC阈值或冒充已开通 |
| ADR03 | 首版动作enter_long/reduce_long/exit_long/hold/wait；仓位flat/long/unknown；模拟与用户采纳独立 | 做空、杠杆、真实订单须新scope/账户合同，当前规格不隐式开放 |
| ADR04 | DSH产生假说/研究与候选动作；纯ActionPolicy验证可发布性和风险约束；数值模型与结算由确定性组件承担 | 不用LLM口述校准，不因未校准自动抹掉研究试用建议 |
| ADR05 | 每UTC整点两资产各一条计划机会；事件另入事件队列与单独统计，去重不按最终盈亏决定 | 变更形成新protocol；不修改已开始窗口的机会分母 |
| ADR06 | reference DSL仅all/any/compare，最多深8/32叶/每组16子项；三值真值、结构错误独立 | 新运算符先补schema/解释器/正负样例，再由版本启用 |
| ADR07 | 快速监测每60秒；新鲜输入被Hub接受到PC可见p95≤120秒；深研究异步修订，不承诺120秒完成全部深研究 | 上游source延迟单列；不能把旧原件标新鲜来达标 |
| ADR08 | 最小反思/技能使用闭环纳入首交付；批量自动优化以后再接 | 不以缺向量库/GEPA/所有全球数据为阻断 |
| ADR09 | 能力和门槛固定在有版本manifest；新增范围先列影响的需求、合同、迁移和评价项 | 开发细节可自主选择，但不得静默改变动作、分母、资产、成本或完成口径 |

## 2. 首轮策略与风险参数封存提案

以下是**用于公平比较的研究试用与模拟参数**，没有盈利验证，也不代替用户自身仓位设置。实施探针只允许修正不可实现的单位/来源问题；改参数须新版本并重新登记未开始的协议，不按已经看到的行情选赢家。

### 2.1 三种比较对象

- B0：空仓分支保持现金；已有多仓分支持有原单位现货。计同一价格/成本/期限；二者分别是建仓和风险降低的参照。
- B1：上一稿的固定v0上破/下破配方，参数完整保留在[业务主方案](../DECISION_FIRST_PRODUCT_PLAN_2026-10-13.md)；输入不足单列，不当失败后删除。它只是技术基线，不能代表整个智能体。
- A1：DSH当前证据+主反机制+适用技能产生一条首选行动及替代动作；使用与基线相同的报价、费用、风险预算和到期规则。它可以提出B1之外的机制，不受B1的M/C逻辑限制。反证、改变理由与新因子都落入DecisionSnapshot。

主比较预登记为A1对B1；B0为必须同时报告的经济参照。首个记忆增量实验单独比较A1有/无一个固定种子技能，其余数据/模型/风险政策相同。两组同时改变数据、prompt、模型和成本的结果不用于声称技能增益。

### 2.2 数值/行动政策

1. 价格尺度U=截止时点之前20根完整1h bar的true range中位数；首根需前一close。缺bar/U≤0则相应价位方案不可评分，不能填0。数据由已有闭合1m聚合，时间与版本冻结。
2. 首个标准建仓试用：初始风险距离1U，目标距离2U，最长24h；失效或研究撤销可以提前退出。新版本可收紧风险/减仓/退出，不能静默扩大已开纸面仓位的最大损失。更复杂动态仓位另立策略版本。
3. 每资产每scenario独立模拟账户E=10000 USDT，两资产结果等权汇总；新建仓单次风险预算0.5%E、名义暴露上限25%E，禁止杠杆。数量按`min(0.005E/(风险距离+每单位费用滑点估计), 0.25E/入场价)`计算，不能用未来收益确定仓位。只为比较，不当用户真实资产数量。
4. 标准reduce_long模拟减少当前单位的一半；exit_long退出余量；hold/wait不新增。existing_long以T0合格报价初始化100%E已有现货，各对照相同，初始不补造成交；这是继承仓位情景，不为满足新建仓25%上限自动减仓。其后新增风险适用第3条。用户未知仓位显示分支说明，不能把模拟账户当其实际持仓。
5. 首轮费用假设按每侧10bp、额外滑点每侧5bp，价差另用真实bid/ask；压力情境为费用/滑点各翻倍。明确显示“假设”，不称该账户真实费率。取得可核费率后作为新费用版本；不能在结果出来后选更低费率。
6. 纸面可执行时点=max(服务端可获取方案时点、触发已被系统知悉时点、准入生效时点)+反应延迟；主情境60秒，0/300秒作敏感性。真实用户可见另按display回执计，无display不能声称已提醒该用户。首报价须observed_at不早于该时点且收到时年龄≤10秒，buy用ask、sell用bid；最多等60秒，取得不到bid/ask则不做可执行收益评价，close-return仅作另列方向标签。
7. 概率字段分subjective/calibrated/unavailable；只有相同scope/期限/目标/模型版本的校准映射可赋calibrated。无映射仍可给有条件的research_trial动作。未来收益正概率不能拿来表示先止盈概率。
8. 若有合格目标概率与盈亏分布，可计算成本后效用；未知时不编数值EV，展示定性选择与压力损失。建议资格检查引用/时点/价格/条件/风险预算及关键反证，不能用统一“资料不全”隐藏所有动作。

### 2.3 研究编排算法

```python
opportunity = register_opportunity(key, scope, target, deadline)  # 先入全分母
snapshot = freeze_accepted_facts(cutoff, required_for_action)
context = select_cases_and_skill_versions(scope, horizon, regime, cutoff)
question = make_question(current_plan, new_events, snapshot.gaps)
candidate = dsh.research(question, snapshot, context)  # 唯一模型/工具循环
# candidate含主/反机制、可核feature引用、新因素需求、首选/替代动作、条件AST。
checked = action_policy.validate(candidate, snapshot, registered_risk_policy)
publish_atomic(checked.decision, checked.plan, monitor_registration, outbox)
# 快速worker只做纯条件求值；实质新证据才触发DSH研究修订。
# 结算worker读取实际到期路径/成本；学习worker只读取已成熟结果。
```

DSH可按问题选择专科；新增风险的实质反证审查必需，不能用所有角色都重复搜索来替代独立性。工具/数据缺口生成具名请求，已有可发布风险方案继续交付。新信息需要新cutoff/revision；超时机会如实记missed，不用后到长报告覆盖当时记录。

## 3. 源可行性先验收什么

不把真实接入写成设计已完成。每项能力发现/失败重试最多3次；明确拒绝则停止同样重试，最多验证1个已登记替代源。执行澄清DC001：成功发现后的必要数据分页另定预算，BTC/ETH各最多5页×300条closed 1m，覆盖21个完整UTC小时及前收盘；不能因每页300条把原本可得的U计算窗口人为截短，也不能无界回填历史。

| 数据/能力 | 现有基础 | 探针必须产出 | 不可用时处理 |
|---|---|---|---|
| BTC/ETH closed 1m | market_path/market_candles/现有provider | 两资产单位/时点/缺bar/版本原件，至少覆盖U计算区间 | 不发布依赖该价位的方案；已有告警保留缺口 |
| bid/ask及费用 | 现有行情能力需核完整原件和实时续取 | 交易场所、scope、observed/known、价差、可追踪原件；记录费用假设 | close-only可做方向诊断，金融可执行收益门不通过 |
| 宏观/仓位 | 已有DTO/公式，部分normal bootstrap输入为空 | 真实actual/consensus/prior时间版本；多所OI/funding单位与原件 | 给受影响能力标unknown；允许其余有依据的研究试用，不伪称宏观增强合格 |
| 外部观点/新闻 | search/fetch/官方feeds；TradingView MCP为未接候选 | 一条授权新材料→独立原件/反证→假说→Plan的血缘 | 先用已有官方源；账号缺失单列，不反复阻塞整条研究 |
| DSH技能加载 | 有skill工具调用，缺导出版本/消费链 | 真实tool result/加载hash、Run binding、消费引用 | 单独标学习未实现；不得用模型自述补加载成功 |

探针结果只有available/partial/unavailable，附首因与证据。研究质量受数据能力影响的范围须逐动作列出，不能用一次HTTP200把整源标合格。

## 4. 工作包及依赖

以下为拟实施任务；每个工作包交付一个可审查变更，不要求按文件数拆PR。所有完成项有测试原件和真实接线证明。

| ID / 依赖 | 精确改造与文件 | 必交付物 | 结束门 |
|---|---|---|---|
| W0 / 无 | 注册首轮协议/策略/数据能力probe；复用现有manifest哈希，不改旧协议 | sources-capability.json、baseline/candidate/risk/cost hashes、原需求映射 | 数据缺口逐项定性；无需全球源全部齐全；所有金融数值的来源/假设确定 |
| W1 / W0 | 新decision_product_v1.schema.yaml；更新codegen Py/TS显式清单；7表和outbox subject迁移 | 生成Py/TS、migration副本报告、历史读回、contract fixtures | schema/check/compatibility；旧数据不改；新旧通知不重发；非法scope/单位/版本被拒 |
| W2 / W1 | decision/action_policy.py、application/action_plan.py、action_repository.py；接可信Graph结果 | 同期限Decision/Plan、原概率与资格分离、owner幂等/CAS/outbox | 重复提交一次发布；并发旧版本409；失败无半条计划；真实BTC/ETH研究产生有依据分支 |
| W3 / W1,W2 | action_monitor/action_tick、scheduler/composition、条件DSL V2与通知新subject | 快速求值、修订、到期与通知；保持原risk V1 | 重启/重复bar/断源/晚到/暂停/恢复/失效优先级；两资产真Plan已武装，自然未触发不伪造 |
| W4 / W2,W3 | decision_routes/query_views/ActionPlanPanel与旧报告入口 | PC当前方案、条件进度、更新原因、来源/技能、结果；恢复GET原回执 | 同期限不混；unknown不填0.5；刷新一致；display不当成交；按钮/错误/超时/重复点击实操 |
| W5 / W1,W2 | Skills文档中的绑定/回执/Experience桥；已有4静态技能先版本化 | 种子方法包、真实skill id/hash、选中/加载/执行/消费四阶段、实体案例引用 | 实际新Run消费、过期/跨资产/缺依赖负例；冻结反例上不强迫观望/建仓；本步不声称金融增益 |
| W6 / W3,W4 | action_outcome、独立Action evaluation CLI/协议、成本与路径算子 | 等待/漏接/未成交/费用/同bar歧义完整分母；到期结算原件 | 至少一个真实到期结果闭环；合成控制单列；下述效果窗口不作为工程无限等待理由 |
| W7 / W5,W6 | Reflection→research_recipe候选编译→配对评价→下次DSH消费；组件晋升/回滚 | 一个已结算案例产生候选、真实后续调用、动作或推理差异及评价 | 可通过也可拒绝/不足；不以写lesson算学会；方法资格与金融资格分开 |
| W8 / W1–W7 | 独立前瞻运行、现PC阻断/实际重叠性能修复、交付包 | 正常启动、真实主链截图/回执、用户手册、首次失败清单、有限效果报告 | 功能、可用试用、金融资格分别判定；任何一门未过明确列出，不用工程分数覆盖金融缺口 |
| W9 / W1接口稳定后，按资产推进 | Calendar/Horizon/Instrument binding；黄金→纳指→恒科领域包 | 每市场provider、机制、指标/条件、研究/行动、标签/费用与独立验证 | 每资产同套接口兼容测试+真实链；未通过不得仅因有行情就显示“分析已支持” |

W5可与W3/W4并行；W6不依赖批量自优化工具；W9接口可以在W1时预留，但不延后BTC/ETH首交付。工程P0等待/技术词/SQLite诊断只处理阻断上述主链的项；没有具体剩余风险不重复全量打包/压测。

## 5. 开发命令与回执

**现在可运行**的设计参考：

```powershell
# cwd = E:/project/study/codex/dsh/decision-hub
& ./.venv/Scripts/python.exe -X utf8 -B docs/design/market-risk-v1/executable-product-v1/spec/verify.py --output artifacts/core-goal-20261011/executable-spec-local-new.json
```

输出路径必须不存在，保留第一次失败。它只接触合成样例和内存SQLite。

**既有命令，用于正式新增合同**（实际运行回执见EXECUTION_LOG）：

```powershell
& ./.venv/Scripts/python.exe -m tools.contract_codegen generate
& ./.venv/Scripts/python.exe -m tools.contract_codegen check
& ./.venv/Scripts/python.exe -m tools.contract_codegen compatibility
```

**拟建，当前不存在**：`tools/acceptance/decision_product_v1.py`统一collect/verify命令；评价统一在`tools/action_evaluation/`，提供validate_protocol/run_golden/verify_export/score模块。正式登记通过产品service，评估器不写产品库。单命令只能生成所用mode的回执；fixture模式永远不能写production_pass/financial_pass。确切参数见评价协议；脚本交付前不能在手册中写成可执行命令。

每项receipt至少保存`requirement_id, build/source_hash, scope, mode, input_refs, command, started_at, ended_at, expected, actual, verdict, first_failure_ref, evidence_paths`。PC还需截图/请求返回/实际可见记录；模型研究需模型/技能/数据版本和actual stop reason。状态只能planned/running/passed/failed/insufficient，不能以“写完代码”直接passed。

## 6. 实现过程防止偏移

- 开工顺序只取W表第一项未通过且依赖已满足的任务；无法继续时给具体接口/数据缺件并做独立可执行项，不重新泛化设计全部系统。
- 每次变更关联业务验收ID及一个可观察行为。性能改动须指出阻断的动作或门槛；单纯减少内存/增加日志不是产品完成项。
- 新能力先通过合同/负例，再真实接入，再评价增量；三者不混用。Agent不能修改已经开始的评分协议，也不能把未触发/弃权从分母移除。
- 每个工作包结束更新机器任务表和一段变更记录；完整历史按引用读取。下一次执行只需README、tasks.json和当前工作包相关规格，避免重读长台账造成目标漂移。
- 若接口/数据探针否定已选技术，记录“原决定、失败证据、替代、影响合同与验收”后修订一个版本；保留已完成工作，不为换框架重新从零开始。
