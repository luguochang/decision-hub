# Decision Hub 产品执行与最终验收总表

版本：`PRODUCT-EXECUTION-ACCEPTANCE-MASTER-2026-09-01.v1`
状态：`executed / E2-L passed / historical master`
适用范围：单机、单 owner、`Crypto Macro Trader` 首个产品；后续领域必须另立 Extension、Domain Pack、Stage Charter 和必要的 ADR。
文档性质：保留跨文档阶段清单；当前执行和最终验收以
[产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准。

> 本文把已经接受的产品、架构、插件和治理决定汇总成可执行入口，不替代
> `contracts/schemas/`、accepted ADR、模块 README、Stage Charter 和历史验收记录。
> 若本文与这些事实源冲突，先停止代码，修正文档/ADR，再继续实现。本文不授权
> 自动交易、自动 Promotion、第二领域、ASR、未经审计的社区插件或新的基础设施。

## 1. 产品定义和交付边界

Decision Hub 是一个由官方 DSH Web 驱动的持续研究智能体。它不是一次 LLM 问答，
也不是复制一个新的聊天页面。系统应能从事件、日历、新闻或人工文本发现研究任务，
在同一个 DSH Session 中主动识别事实缺口、调用已审计能力、继续补证或解释性停止，
最后由 Hub 的确定性充分度和发布 Gate 决定是否形成报告。

```text
事件/文本/日历/新闻
  -> Hub admission + durable Run
  -> 官方 DSH Web Session
  -> DSH Agent Loop：计划 -> Tool/Skill/Subagent/MCP -> 结果 -> 缺口 -> 下一轮
  -> Capability Gateway：权限、schema、来源、PIT、freshness、hash、预算、错误
  -> Sufficiency + 代码 Gate（唯一发布裁决）
  -> 人可读报告 / research_only / failed / cancelled
  -> committed Artifact -> Outbox 通知
  -> Outcome -> Evaluation -> FailurePattern / Experience 资产
```

首期可验证价值只有两项：

1. 减少 owner 手工检索、交叉核对和复盘的时间；
2. 把事实、来源版本、研究轨迹、失败原因、预测结果和反馈沉淀为可回放资产。

报告更长、单次模型回答漂亮或 replay 通过都不能证明预测准确率、盈利或实时可用。

### 1.1 本阶段交付

- 唯一用户入口是官方 DSH Web，默认工作区为 `Crypto Macro Trader`。
- DSH Native Host/Client Plugin 负责把 Hub 任务、状态和人可读报告投影回 DSH。
- Hub Kernel 负责 Run、Evidence、PIT、Sufficiency、Gate、Artifact、Forecast、
  Outcome、Evaluation、Outbox 和个人资产账本。
- DSH 内层 Harness Agent Loop 负责主动选择已授权 Tool/Skill/Subagent/MCP 并继续补证。
- LangGraph 只负责 Hub 外层生命周期、checkpoint、lease、recovery 和有界 evidence round。
- 文本输入已贯通；ASR 只保留未来 `AsrProviderPort -> TextEnvelope` 适配器口。
- 默认输出是人可读状态、证据覆盖、主/反因果链、30m/24h/72h、Trigger、Invalidation、
  Gate、停止原因和复查时间；原始 JSONL 通过 DSH 原生 Trajectory 按需查看。

### 1.2 明确不做

- 自动下单、自动交易、自动改 Gate、自动 Promotion、自动扣费或自动安装插件；
- ASR/OCR、PPT、A 股、美股、第二领域、多用户、公共插件市场；
- clone/fork DSH Web，或复制 DSH Chat、Session、Trajectory、JSONL、compaction、安装器；
- 在 LangGraph 自建第二套 ReAct、Supervisor、Tool Loop 或 Provider client；
- 因一次网络失败引入 Redis、Kafka、Celery、Temporal、DBOS、Postgres、Kubernetes；
- 用 `no_trade` 掩盖 `failed`、`insufficient`、`stale`、`cancelled` 或 Provider 失败。

## 2. 用户视图和后台视图

### 2.1 DSH Web：唯一日常入口

用户只需要打开 DSH Web。官方 Chat、Session、History、Plan、Tool、Skill、Subagent、
Trajectory、JSONL 和 compaction 全部复用。Decision Hub 插件只增加以下业务卡：

- `建立研究任务`：从当前 composer 文本创建 typed intake；
- `研究状态`：Run、Session、来源、优先级、轮次、已完成能力、当前缺口和 deadline；
- `结果卡`：Evidence coverage、Gate、报告、30m/24h/72h、Trigger、Invalidation；
- `动作`：取消、重试、复查、打开 Decision Desk；
- `失败卡`：逐 capability 显示 `error_code`、来源、cause、是否可重试和已保留证据。

页面状态必须区分：`queued`、`researching`、`partial`、`research_only`、`committed`、
`failed`、`cancelled`。`insufficient` 不是空白页，必须显示缺口、停止原因和下一次
可执行动作；失败不能伪装成方向性结论。

### 2.2 Decision Desk：管理和审计后台

Decision Desk 不是第二个聊天入口，只消费 Query/View DTO 和 Zod schema：

- Operations：API、worker、MCP、readiness、heartbeat、source/capability health、Outbox；
- Run Inspector：版本、PIT、timeline、Step/Attempt/Call、DSH trace link、错误总览；
- Evidence：authority、来源、`observed_at/published_at/received_at/cutoff_at`、hash、freshness、冲突；
- Decision：Artifact、Forecast、Trigger、Invalidation、Gate、复查时间和停止原因；
- Assets/Evaluation：Dataset、Outcome、Brier、延迟、成本、FailurePattern、Experience、owner feedback；
- Promotion/Rollback：仅 owner 命令，确定性 Gate、generation/CAS 和审计事件；
- Backup/Recovery：SQLite、checkpoint、Session root 和恢复结果。

页面不能直读 SQL、LangGraph state、DSH raw JSON 或 Provider payload，也不能把大量内部
JSON 倾倒给用户。原始轨迹只在审计动作中按需打开。

## 3. 责任边界和两层循环

### 3.1 所有权矩阵

| 层 | 拥有 | 不拥有 |
|---|---|---|
| 官方 DSH Web/Harness | Agent Loop、Session、Tool/Skill/Subagent/MCP、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT、Gate、Artifact/Forecast/Outcome、交易权限 |
| DSH Native Plugin | 官方 Host/Client seam、typed intake、Run/Session 关联、状态/报告卡、命令桥 | 第二套聊天、第二套 loop、账本、Gate、自动安装 |
| Hub Kernel | Event、Run、Evidence、Snapshot、Sufficiency、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、Outbox | DSH 私有状态、模型 loop、插件安装 |
| LangGraph 外层 | 生命周期、checkpoint、lease、recovery、有界 Evidence Round | ReAct/tool loop、Supervisor、Provider 协议、发布裁决 |
| Capability Gateway | manifest、权限、schema、来源、authority、freshness、PIT、timeout、retry、cost、error provenance | 发布结论、修改 Gate、扩大权限 |
| Domain Pack | 领域事实要求、来源 ladder、doctrine、Role/Profile、Gate 参数、评测和 fixture | 通用账本、Harness 实现 |
| Decision Desk | Query/View、运维、审计、资产和 owner 命令 | 第二聊天、直写 SQL、改写历史 |

### 3.2 DSH 内层 Agent Loop

```text
目标 + Domain Pack + hard gaps
  -> DSH 判断下一步并选择授权能力
  -> Gateway 校验并返回结构化结果或错误 provenance
  -> DSH 更新缺口和计划
  -> 未闭合且预算允许：同一 Session 继续下一轮
  -> 已闭合：提交候选；否则 research_only / 解释性停止
```

循环必须受 `max_rounds`、`max_tool_calls`、`max_subagents`、单能力 timeout、模型步
timeout、总 deadline、结构化修复次数和成本预算约束。模型只能提交候选，不能写账本、
修改 Gate、生成未经验证的 Evidence 或获得交易权限。

### 3.3 Hub 外层生命周期

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected | failed | cancelled
  -> outcome_due -> evaluated
```

LangGraph 只管理该生命周期和恢复位置；三份状态必须保持分离：

| 状态 | 所有者 | 事实用途 |
|---|---|---|
| DSH Session JSONL | DSH | 会话、工具、子 Agent、compaction 轨迹 |
| LangGraph checkpoint | Hub orchestration | 外层恢复位置，不是业务结论 |
| Hub Ledger | Kernel SQLite/Alembic | PIT、Evidence、Gate、资产、审计和通知事实 |

三者通过 `run_id`、`dsh_session_id`、`trace_ref` 和 checkpoint ref 关联，互不冒充。
历史 Run、Evidence、Artifact、Forecast、Outcome 和 Evaluation 只增不改。

## 4. 插件和代码结构

### 4.1 插件不是界面装饰

一个业务功能可以组合多个 DSH Tool、Skill、Subagent 和 MCP；DSH 负责选择和循环，
Capability Gateway 负责审计入账，Hub Gate 负责是否发布。插件分四类：

1. **DSH Native Plugin**：官方 DSH 可加载的 Tool/Skill/Subagent/MCP/Hook/UI bundle；
2. **Product Extension**：产品输入输出、命令、视图、权限、历史和资产契约；
3. **Domain Pack**：领域 doctrine、事实要求、来源优先级、Gate、Role/Profile 和评测；
4. **Capability Plugin**：外部 Search/Official/Market/Notification 等已审计适配器。

新增 PPT、A 股、美股时先建独立 Extension/Pack；只有第二个真实调用方证明语义相同，
才抽取 Platform Core。ASR 只把音频产出转换成同一 `TextEnvelope`，不进入研究核心。

### 4.2 目录和依赖方向

```text
apps/
  hub_api/                 REST command/query/callback；不执行长推理
  hub_worker/              durable research/realtime/evolution/outbox worker
  research_mcp/            唯一 capability gateway
  decision-desk/           React 管理/审计后台
packages/
  kernel/                  领域无关账本、Gate 和公开 Port
  orchestration/langgraph/ 外层生命周期、checkpoint、recovery
  runtime_adapters/dsh_runtime/ 官方 DSH Web/SDK Host adapter
  provider_adapters/       Search/Official/Market/Notification
  workbench_adapters/      DSH/MCP/capability binding
  query_views/             人可读 DTO
  evals/                   replay/holdout/shadow/outcome/promotion
contracts/schemas/         YAML canonical source；codegen Python/TypeScript
packs/crypto_macro/        doctrine/evidence/profiles/tools/gates/evaluations/fixtures
extensions/dsh/decision-hub/ 官方 Host/Client Plugin seam
```

依赖方向固定为 `apps -> packages -> contracts`。Kernel 不依赖 DSH、LangGraph、Provider
或前端；前端只消费 Query/View + Zod；generated 镜像禁止手改；公开边界禁止裸 `dict/any`。
协议、重试、结构化输出、checkpoint、迁移和观测优先复用已选框架能力。

## 5. 阶段路线、目标和停止线

| 阶段 | 目标 | 当前状态 | 退出后动作 |
|---|---|---|---|
| R0 | 文本 -> PIT -> Agent -> Gate -> Artifact/Forecast/Outcome | 工程/回放完成 | 保留 Fixed baseline |
| R1 | 来源、调度、市场基准、通知 | 工程/回放完成 | 只读 live 观察另立门 |
| R2/R2-R | Workbench、评测、资产、DSH candidate、主动补证 | 工程/回放完成 | DSH 仍 candidate/shadow |
| E2L-01 | 误准入、bootstrap backlog、Run 优先级 | 代码/回放完成 | 不再隐式历史研究 |
| E2L-02 | 能力即时入账、模型步 watchdog、部分结果恢复 | 工程/回放完成；live pending | 只闭合 E2-L，不加新功能 |
| E2-L | 正式 DSH Session 的真实能力闭环 | **当前唯一产品目标** | 通过即停止工程扩张 |
| E3/P4 | 14 天或 20 个高影响事件价值观察 | 未开始 | 形成 promote/retain/stop |
| G4/G5/G6 | ASR、第二领域、规模化部署 | 未授权 | 必须新 Stage Charter/ADR |

### 5.1 当前执行目标

```text
在不新增 Agent、workflow、页面框架、Provider 协议或数据库的前提下，
让 Search 或等价的已审计、新鲜 live capability 在正式 DSH Session 中完成：
Evidence -> Sufficiency -> Gate -> 人可读报告；同时保留 partial/insufficient 安全失败。
```

价值断言：owner 能看到来自实时可核验事实的报告，而不是 replay 或模型自述。真实
能力通过后立即进入 E3/P4，不继续堆功能。`Fixed` 保持 active，`DSH` 保持 candidate/shadow。

### 5.2 E2-L 退出门

- [ ] Official、Market、Search 至少一项新鲜 live capability 通过 manifest、contract、
  canary、authority、freshness、PIT、hash、timeout、retry、cost 和 error provenance；
- [ ] 正式 DSH Session 完成 live Evidence -> Sufficiency -> Gate -> Report；
- [ ] success、partial failure、insufficient/stale、model-step timeout、owner cancel、
  restart/recovery 均能在 DSH Web/Desk 人可读显示；
- [ ] 失败保留 `error_code/origin/cause_code/retryable`，不产生错误 Artifact/Forecast；
- [ ] readiness 由代码返回 `pilot_ready=true`，不能靠文档手写；
- [ ] 保留命令、端口、runtime/provider/pack/schema 版本、API 摘要、截图、console、hash；
- [ ] 若 live capability 继续失败，记录真实 blocker，保持 `pilot_ready=false`，停止扩张。

### 5.3 E3/P4 价值门

E2-L 通过后才开始，至少持续 14 天或覆盖 20 个高影响事件；每个事件记录：

- Fixed 与 DSH candidate 的同 PIT 输入、来源和版本；
- 首条证据延迟、最终延迟、工具调用数、失败率、成本和通知延迟；
- 30m/24h/72h Outcome、方向标签、Brier、覆盖率和净收益（未到期不写标签）；
- owner 是否减少检索时间、是否理解停止原因、是否愿意继续使用；
- 最终只产生 `promote`、`retain_baseline` 或 `stop`，不自动 Promotion。

## 6. SDD / BDD / TDD / ADR 全局约束

所有新任务严格执行：

```text
产品价值/非目标
  -> canonical schema + event + Gate 规则（SDD）
  -> ADR（跨模块、不可逆、协议、部署、数据决定）
  -> BDD success/partial/insufficient/failure/cancel/recovery
  -> TDD Red -> Green -> Refactor
  -> 集成/E2E/replay -> live canary（按风险）
  -> README、状态、路线图、CHANGELOG、验收证据
```

强制规则：

1. schema 只在 `contracts/schemas/*.yaml` 修改，随后运行 codegen；禁止手改镜像；
2. Agent 只能提候选，代码 Gate 唯一裁决；无合法 Evidence 不得发布方向性结果；
3. 三时间戳 `observed_at/published_at/received_at` 和 cutoff 必须可审计，禁止未来泄漏；
4. `CancelledError` 是控制流；timeout、429、5xx、schema、权限和 Provider 错误分类不得混淆；
5. 跨边界只走公开 Port/契约，Pydantic/Zod 运行时校验，禁止裸 dict/any；
6. 失败必须保留错误 provenance 和已经取得的成功事实；不删除历史 Run 制造“干净”结果；
7. DSH JSONL、LangGraph checkpoint 和 Hub Ledger 分离；任何历史事实只增不改；
8. 先复用 LangGraph、LangChain/OpenAI SDK、Pydantic、SQLAlchemy/Alembic 和现有适配器，
   不重复实现 loop、重试、provider client、trace、账本或状态机；
9. 普通 CI 不触网、不使用真实密钥；live 仅显式 opt-in，密钥不能进入代码、日志、数据库、
   JSONL、fixture、文档或 shell history；
10. 不引入没有真实调用方的新基础设施；发现第二次补丁或 ADR/schema 冲突时立即停止编码。

### 6.1 上下文压缩和文档纪律

长任务只读 `INDEX.md`、`docs/context/CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、
当前 Stage Charter、相关 ADR/schema、模块 README 和测试。出现三个以上不相关模块、
重复修补、目标不能写成一句话加三个断言或事实冲突时，先压缩为：

```text
事实 / 决策 / 当前目标 / 不做 / 证据 / 未决
```

结论写入对应 Platform、Domain、Stage、ADR、README、Status 或 CHANGELOG；临时推理进
`tmp/`，不把聊天记录当事实源。模块职责变化必须同步模块 README 和 `docs/modules/README.md`。

## 7. BDD / TDD 最终验收矩阵

| 场景 | Given/When | 必须观察到 |
|---|---|---|
| Success | 新鲜能力全部返回合法 Evidence | 同一 DSH Session 多轮、充分度通过、三 Horizon、Artifact/Outbox 提交 |
| Partial failure | A 成功、B timeout/429/5xx | A 的 Trace/Evidence 保留；B 有 provenance；Gate 不误发布 |
| Insufficient/stale | 缺 hard fact 或证据过期 | `research_only`/`reject`、缺口和 stop reason 可读；无方向性 Forecast |
| Model-step timeout | capability 成功后模型步超时 | Host cancel；Run/Session failed；已入账 Evidence 不丢；可恢复 |
| Owner cancel | owner 在运行中取消 | `cancelled`，不被归类 Provider failure；不再提交 Artifact |
| Duplicate/retry | 相同 idempotency key 或 retry | 一个 Run/Session；retry 产生 child Run；历史父 Run 不改写 |
| Restart/recovery | worker、Web 或 callback 中断 | lease/checkpoint 恢复；最多一个 Artifact/Outbox；Session hash 保持 |
| Permission/schema | 未授权能力、低 authority、未来时间、坏 schema | fail-closed；明确权限/时间/schema 错误；不写业务事实 |

TDD 必须先写失败测试，再写最小实现，最后只做有保护的重构。测试数据固定时间、
文本、Provider 响应和随机性边界；不以真实网络不稳定作为普通测试前置。

## 8. 工程和产品 Definition of Done

### 8.1 每张任务卡

```text
[ ] 目标、价值、输入、输出和非目标已写入 Stage Charter
[ ] schema/event/Gate 已锁定，必要时有 ADR，generated 由 codegen 产生
[ ] BDD 覆盖成功、重复、部分失败、事实不足、取消和恢复
[ ] TDD Red/Green/Refactor 完成，固定 fixture 和时钟
[ ] 复用已有 Harness/框架能力，没有第二套 loop、账本、DTO、状态机或 Provider
[ ] PIT、幂等、权限、secret、历史只增不改保持不变
[ ] 模块 README、INDEX、IMPLEMENTATION_STATUS、ROADMAP、CHANGELOG 同步
[ ] Python/前端/契约/静态/文档/恢复检查通过
[ ] live 证据包含运行版本、hash、API 摘要和失败 provenance
[ ] 一个任务一个可回滚 commit；未经 owner 另行授权不 push
```

### 8.2 全局验收命令

```bash
./.venv/bin/pytest -m 'not live' -q
./.venv/bin/ruff check .
./.venv/bin/pyright
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/docs/check_module_docs.py
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
docker compose config --quiet
git diff --check
```

浏览器验收使用全新隔离实例，必须保存 success、partial、insufficient/stale、model-step
timeout、cancel、restart 的页面/API/console/hash 证据。旧进程、旧端口和 replay 页面不能
替代当前 revision 的 live 证据。

## 9. 当前事实、阻塞和停止句

截至 2026-09-01：

- R0/R1/R2/R2-L、R2-R-00..06E、DSH-NATIVE-CORE 和 E1/E2-R 工程/replay 门已通过；
- E2L-02 的 durable progress、capability 即时入账、Host watchdog、部分进度 Query/View
  和失败 provenance 已通过离线/回放门；
- 最新复核为 Python `373 passed`、DSH plugin `43 passed`、Decision Desk `10 passed`，
  Ruff、Pyright、codegen、module docs、前端 test/build、Compose config、core acceptance 和 diff check 通过；
- 真实 Search 历史样本仍有 timeout；最小 Official/Market capability 独立 canary 已通过，
  但新的正式 DSH Session live acceptance 和当前 revision 的移动 viewport/console/hash 资产仍待完成；
- `pilot_ready=false`、`pilot_usable=false`；Fixed active，DSH candidate/shadow；E3 未开始。

因此当前唯一允许的开发目标是 E2-L live capability 收口。若需要新增角色、工作流、
领域、数据库、Provider 协议、权限或前端入口，必须停在这里，先新增 Stage Charter/ADR
并取得 owner gate。E2-L 通过后必须停止工程功能扩张，进入 E3/P4 价值观察。

## 10. 文档维护和交接

每次任务结束必须更新：

1. 受影响模块 README；
2. `docs/IMPLEMENTATION_STATUS.md` 和 `docs/ROADMAP.md`；
3. 相关 Stage Charter、ADR/schema（若发生改变）；
4. `CHANGELOG.md`；
5. `docs/context/CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、`HANDOFF.md`；
6. 本文 checklist 和验收证据索引。

下一次恢复时的唯一第一步：读取本文件第 9 节和 [E2L-02 阶段卡](../stages/E2L_02_DSH_DURABLE_PROGRESS_AND_DEADLINE.md)，
确认 live Search/官方 DSH Web 的阻塞仍未改变，再决定是否执行隔离 canary；不得从聊天记录推断新目标。
