# Decision Hub 最终产品执行合同

版本：`PRODUCT-FINAL-EXECUTION-2026-09-01.v1`
状态：`executed / E2-L passed / historical contract`
执行目标：`PRODUCT-CLOSEOUT-01`
适用范围：单 owner、单机、只读研究的 `Crypto Macro Trader` 首个产品。

本文件保留 E2L-02 的执行合同和历史边界，不再描述当前待办。E2-L 已通过；当前状态和
E3 唯一目标以 [产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准。它不
替代 canonical schema、已接受 ADR、模块 README 和历史阶段记录；其他扩展仍必须另立
Stage Charter 和 ADR。

## 1. 产品定义与实际价值

Decision Hub 不是一次 LLM 问答，也不是把 DSH 页面复制一份。它是由官方 DSH Web 驱动的持续研究智能体：后台从事件/文本/日历/新闻发现任务，建立耐久 Run，DSH 在同一 Session 内主动发现事实缺口并调用已审计能力，Hub 再以 PIT、证据充分度和确定性 Gate 约束发布，最后把报告、通知和研究结果沉淀为可回放个人资产。

```text
来源/事件发现
  -> Hub admission + Event/Run
  -> 官方 DSH Web Session（唯一用户入口）
  -> DSH Harness Agent Loop：计划 -> Tool/Skill/Subagent/MCP -> 结果 -> 缺口 -> 下一轮
  -> Capability Gateway：权限、schema、authority、PIT、freshness、hash、预算、错误
  -> Sufficiency + 代码 Gate（唯一发布裁决）
  -> 报告、Forecast 或解释性 research_only
  -> Outbox 通知
  -> Outcome / Evaluation / FailurePattern / Experience
```

首期可验证价值只有两个：减少手工检索和复核时间；把每次研究的事实、方法、失败原因和结果标签积累成可回放资产。一次好看的报告、一次模型回答或静态 replay 不能证明盈利能力。

## 2. 最终产品形态

### 2.1 唯一入口和两个视图

用户只打开官方 DSH Web。启动器只打印一个 DSH URL，预装 Decision Hub 官方 Host/Client Plugin，并自动进入 `Crypto Macro Trader` 工作区。Hub API、Research MCP、worker、SQLite 和 LangGraph 都是后台实现，不是用户要操作的第二个产品。

DSH Web 日常视图复用官方 Chat、Session、History、Plan、Tool、Skill、Subagent、Trajectory、JSONL 和 compaction。插件只增加业务卡：建立研究任务、当前 Run、研究轮次、证据覆盖、能力成功/失败、Gate、报告、复查/取消/重试和打开管理后台。普通 Chat 标记为 `exploration`，不会自动成为正式 Forecast。

Decision Desk 是管理/审计视图，不是第二个聊天入口，提供 Operations、Run Inspector、Timeline、Evidence lineage、PIT、Capability/Source health、Artifact、Forecast、Outcome、Evaluation、FailurePattern、Experience、Promotion/Rollback、Backup 和 Outbox。

页面只显示人可读摘要：

- `研究中`、`部分完成`、`研究结论（不可交易）`、`失败`、`已取消`、`已提交`等状态；
- 每个 capability 的完成、失败原因、来源、是否可重试；
- 已保留的 Evidence 数量、hard/soft coverage、缺口和 stop reason；
- 30m/24h/72h、Trigger、Invalidation、PIT 和 Gate；
- 原始 DSH JSONL 只通过 DSH 原生 Trajectory 按需查看，不把 raw JSON、密钥、SQL 或 graph state 倾倒到页面。

### 2.2 单机后台形态

```text
官方 DSH Web + 官方 Plugin
        |
        +--> Hub API（短请求、命令/查询）
        +--> hub_worker（admission、research、realtime、outbox、evolution）
        +--> Research MCP（唯一 capability 入口）
                         |
                         +--> Search / Official / Market adapters
        +--> SQLite WAL + Alembic + DSH Session root + LangGraph checkpoint
        +--> Decision Desk（只读 Query/View + 明确命令）
```

首期不需要 Redis、Kafka、Celery、Temporal、DBOS、Postgres、Kubernetes 或多服务拆分。未来只有出现真实并发、远程高可用或第二领域复用证据时，才另立架构决策。

## 3. 所有权和可插拔边界

| 层 | 拥有 | 不拥有 |
|---|---|---|
| 官方 DSH Web/Harness | Agent Loop、Session、Tool/Skill/Subagent/MCP、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT、Gate、Artifact/Forecast/Outcome、自动交易 |
| 官方 DSH Plugin | Host/Client seam、Run/Session 关联、业务状态卡、报告卡、命令桥 | 第二套聊天、第二套 loop、账本、Gate、自动安装器 |
| Hub Kernel | Event、Run、Evidence、Snapshot、Sufficiency、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、Outbox | DSH 私有状态、模型推理和插件内部实现 |
| LangGraph | 外层生命周期、checkpoint、lease、恢复和有界 Evidence Round | ReAct/tool loop、Supervisor、Provider 协议和最终裁决 |
| Capability Gateway | manifest、权限、schema、来源、PIT、freshness、timeout、retry、cost、error provenance | 发布结论、修改 Gate、扩大权限 |
| Domain Pack | 领域事实要求、来源梯度、doctrine、Role/Profile、Gate 参数、评测规则 | 通用账本、Harness 内部实现 |
| Decision Desk | 查询、运维、审计、资产、Promotion/Rollback 命令 | 直写数据库、第二个聊天入口、改写历史 |

插件是正式能力安装单元，不是界面装饰。一个业务可以组合多个 DSH Tool/Skill/Subagent/MCP，但可信入账必须经过 Capability Gateway，发布必须经过代码 Gate。新增 PPT、A 股、美股时新建独立 Product Extension/Domain Pack 和结果契约；只有第二个真实调用方证明语义相同，才抽取共享 Platform Core。ASR 未来只需把音频转换为 `TextEnvelope`，研究核心不感知音频来源。

## 4. 唯一 Agent Loop 与外层生命周期

DSH 内层是唯一智能循环：读取目标和 Domain Pack，判断 hard gaps，选择授权能力，读取结构化结果，重新计算 gaps，继续补证或解释性停止。它必须受 `max_rounds`、`max_tool_calls`、单能力/模型步/总 deadline、结构化修复次数和成本预算约束。模型只能提出候选，不能写账本、Gate、Forecast 或交易权限。

Hub 外层只管理产品状态：

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected | failed | cancelled
  -> outcome_due -> evaluated
```

LangGraph 复用 `StateGraph`、checkpoint、lease、恢复和 RetryPolicy，不创建第二套 ReAct、Supervisor、工具选择器或 Provider client。三份状态互不冒充：DSH JSONL 保存会话轨迹，LangGraph checkpoint 保存恢复位置，Hub Ledger 保存业务事实和资产；历史 Run/Evidence/Forecast/Outcome 只增不改。

## 5. 代码结构和依赖规则

```text
apps/
  hub_api/                 REST command/query/callback；不执行长推理
  hub_worker/              durable research/realtime/evolution/outbox worker
  research_mcp/            唯一 capability gateway 进程
  decision-desk/           React 管理/审计后台
packages/
  kernel/                  领域无关业务账本和公开 Port
  orchestration/langgraph/ Hub 外层生命周期与恢复
  runtime_adapters/dsh_runtime/ 官方 DSH Web/SDK Host adapter
  provider_adapters/       Search/Official/Market/Notification 适配器
  workbench_adapters/      DSH/MCP/capability binding
  query_views/             人可读 DTO，不暴露 raw state
  evals/                   replay/holdout/shadow/outcome/promotion
contracts/schemas/         YAML canonical source；codegen 生成 Python/TypeScript
packs/crypto_macro/        doctrine/evidence/profiles/tools/gates/evaluations/fixtures
extensions/dsh/decision-hub/ 官方 Host/Client plugin seam
```

依赖方向固定为 `apps -> packages -> contracts`。Kernel 不导入 DSH、LangGraph、Provider 或前端；前端只消费 Query/View DTO 和 Zod；跨边界禁止裸 `dict/any`；generated 镜像禁止手改；Provider/协议/重试/观测优先复用 LangChain、LangGraph、OpenAI SDK、Pydantic、SQLAlchemy、Alembic 和 OpenTelemetry。

## 6. 阶段目标和当前授权任务

### 6.1 阶段状态

| 阶段 | 目标 | 当前状态 |
|---|---|---|
| R0 | 文本输入 -> PIT -> Agent -> Gate -> Artifact/Forecast/Outcome | 已完成并保留基线 |
| R1 | 来源适配、调度、行情基准、通知 | 工程/replay 已完成，live 需授权来源观察 |
| R2/R2-R | Workbench、评测、资产、自主补证、DSH candidate | 工程/replay 已完成，Fixed 仍 active，candidate 未 Promotion |
| E2L-01 | 误准入、fresh bootstrap backlog、优先级 | 工程/replay 已完成；live 复验并入 E2-L |
| E2L-02 | 能力即时入账、模型步 watchdog、部分结果恢复 | 工程/replay 已完成；live 证据 pending |
| E2-L | 正式 DSH Session 的真实能力闭环 | **当前唯一产品目标** |
| E3 | 14 天或 20 个高影响事件价值观察 | E2L-02 和真实能力门通过后开始 |
| R3 | 第二领域和远程部署 | E3 证明真实需求后另立 Charter |

### 6.2 E2L-02 可观察目标

```text
已成功 capability -> DSH 下一模型步失败
=> Tool Trace/Evidence 仍保留，Session/Run 准确显示失败来源，不发布错误 Artifact
```

本任务只修改与此目标直接相关的边界：

1. Research MCP Gateway 在返回 DSH 前写 `tool_started`、`tool_completed`/`tool_failed`，并即时入账 Evidence 或 Error Provenance；最终 SessionResult 重放幂等。
2. canonical DSH submit 增加 `model_step_timeout_ms`，Host 用官方 `session/event` 和 `sessionController.cancel()` seam 实现 watchdog，不改 DSH 上游源码。
3. 区分 capability timeout、model-step timeout、owner cancel、product total deadline；retry 只建 child Run。
4. DSH Web 与 Desk 显示“已完成能力、已保留证据、失败原因、是否可重试”，不显示整段错误 JSON。

首版 live 预算锁定：`per_tool=20s`、`per_model_step=150s`、`total=480s`。480 秒仍是硬上限；至少 20 个 live 高影响事件的 P95、成本和价值观察后，才可通过 ADR 调整。

### 6.3 E2L-02 非目标

不 fork/clone DSH；不复制其 Chat/Session/Trajectory/JSONL/compaction/Agent Loop；不在 LangGraph 重写 ReAct/Supervisor；不新增 capability 工具、消息队列、数据库、ASR、PPT、第二领域、自动交易或自动 Promotion；不把失败改写成 `no_trade`。

## 7. SDD/BDD/TDD/ADR 执行约束

所有新任务固定顺序：

```text
产品价值与非目标
  -> canonical schema/event/Gate 规则（SDD）
  -> ADR（跨模块/不可逆/数据和运行时决定）
  -> BDD success/partial/failure/cancel/recovery
  -> TDD Red -> Green -> Refactor
  -> 集成/E2E/replay -> live canary（若适用）
  -> README、状态、CHANGELOG、验收证据
```

BDD 必须描述用户可观察事实；TDD 必须固定时间、fixture 和 Provider 结果；普通 CI 不触网，live 只能显式 opt-in。异常必须保留 `error_code`、`origin`、`cause_code`、`retryable`、`deadline_ms`；`CancelledError` 是控制流，不能包装成 Provider failure；所有失败 fail-closed。

上下文过长或出现第二次补丁时先压缩为“事实/决策/当前目标/不做/证据/未决”，更新 `CURRENT_STATE`/`HANDOFF` 后再继续。文档职责分离：Platform/Domain/Stage/ADR/README/CHANGELOG 各写自己的事实，临时材料进 `tmp/`，禁止把聊天推理当事实源。

## 8. C1-C7 最终验收清单

### C1-C3：入口、运行和主动补证

- [x] 官方 DSH Web 是唯一入口，workspace、Host/Client Plugin、Hub Run 和 DSH Session 可互相定位。
- [x] 同一 Session 多轮、checkpoint、cancel、callback 和 JSONL link 有回归覆盖。
- [x] DSH Agent 能在授权 manifest 中选择能力并继续补证；hard gap 未闭合只能 research_only/no_trade。
- [ ] E2L-02 实现后的 live Session 能在能力成功后保留 progress。

### C4：能力和事实可靠性

- [x] manifest、schema、权限、来源、authority、freshness、PIT、hash、预算和错误码有契约/replay。
- [ ] Official、Market、Search 在正式 DSH Session 中完成成功/partial/insufficient live canary；Search 目前仍曾超时，不能假设通过。
- [ ] authority floor、stale、future timestamp、schema error、429/5xx/timeout 的拒绝在最终页面可见。

### C5：报告、通知、观测

- [x] Desk 有 Timeline、Evidence lineage、PIT、Gate、版本和基础错误投影。
- [ ] 每个 capability 的错误来源、可重试和部分成功数量在 DSH Web/Desk 都可读。
- [ ] 只有 committed Artifact 进入 Outbox；通知失败可重试且不重跑分析。

### C6：个人资产和价值

- [x] Run、Evidence、PIT Snapshot、Artifact、Forecast、Outcome、Evaluation、FailurePattern、Experience 均有契约/迁移/回放边界。
- [ ] E3 完成至少 14 天或 20 个事件的同 PIT 对照、Brier/方向、延迟、成本和 owner usefulness 记录。
- [ ] 价值门结束后只能选择 `promote`、`retain_baseline` 或 `stop`，不能无限加功能。

### C7：单机交付和可恢复

- [x] 一键启动/停止、readiness、SQLite WAL、备份/恢复、migration、heartbeat、lease 和 recovery smoke 有工程覆盖。
- [ ] 最终 revision 重新完成官方 DSH Web 成功、部分失败、模型步超时、恢复和桌面/移动截图/console/API/hash 证据。

### 阶段退出条件

E2L-02 的 Python/TypeScript/前端/契约/文档和 replay/recovery 工程检查已通过；只有官方 DSH Web 的 live 成功路径、部分失败、模型步超时安全失败和浏览器证据全部通过，E2-L 才能标 `done`。此时最多将产品标为 `pilot_ready / research_only`；`pilot_usable` 必须等待 E3，真实 Search/市场数据不足时不得给方向性交易结论。

## 9. 每张任务卡的 Definition of Done

```text
[ ] 价值、输入、输出、非目标写入 Stage Charter
[ ] schema/event/Gate 先锁定，必要时有 ADR，generated 由 codegen 生成
[ ] BDD 覆盖成功、部分失败、事实不足、取消、重复和恢复
[ ] TDD Red/Green/Refactor 完成，使用固定 fixture，不依赖真实网络
[ ] 复用框架已有 loop/重试/timeout/trace/transaction 能力
[ ] 无第二套 loop、Provider、账本、DTO、状态机或隐式权限
[ ] PIT、幂等、历史只增不改、secret 不落盘保持不变
[ ] 模块 README、INDEX、IMPLEMENTATION_STATUS、ROADMAP、CHANGELOG 同步
[ ] pytest、Ruff、Pyright、codegen、module docs、前端 test/build 通过
[ ] live 失败如实记录；截图/console/API/hash 有可复核非敏感路径
[ ] 一个任务一个可回滚 commit；未经 owner 另行授权不 push、不扩大范围
```

## 10. 当前执行顺序

1. 已完成：durable gateway、Host watchdog、UI partial-progress 的 TDD 和实现。
2. 已完成：canonical DSH submit schema/codegen、live budget、完整离线质量门和 replay/recovery。
3. 当前唯一动作：在全新隔离实例用官方 DSH Web 验收 live success、partial failure、model-timeout、owner cancel、恢复五类场景。
4. 保存非敏感证据，更新状态和交接文档；若 live capability 仍不可用，保持 `pilot_ready=false` 或 `research_only`，停止功能扩张并报告根因。

本合同的目标不是把代码做得更大，而是把一条可替换、可审计、可恢复、能真实发现信息不足并继续补证的产品主线交付出来。达到阶段退出门后进入价值观察，不再以新增框架和页面作为进度。
