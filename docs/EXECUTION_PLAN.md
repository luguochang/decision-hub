# Decision Hub 分阶段执行设计

版本：`EXECUTION-2026-08-26.v1`
状态：`accepted`（owner 已确认）；按本文件和当前 Stage Charter 串行执行任务。
适用范围：R0 文本核心、R1 实时事件、R2 Workbench/自主进化、R3 多领域与部署扩展。

## 1. 这份文档解决什么问题

`DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md` 是架构、契约和边界的唯一基线；`docs/ROADMAP.md` 是里程碑清单。本文件是两者之间的执行层，解决每次交给 Codex 一个小目标时容易出现的四类问题：

本文件必须与 [项目宪章](engineering/PROJECT_CHARTER.md)、[当前 R1 Stage Charter](stages/R1_REALTIME_EVENT_ENGINE.md)、[R1 adapter 边界 ADR](decisions/ADR-0003-r1-realtime-plugin-boundary.md)、[R0 Core Completion 实现方案](stages/R0_CORE_COMPLETION_PLAN.md) 和 [全局开发治理规范](engineering/DEVELOPMENT_GOVERNANCE.md) 一起使用：宪章负责保持产品目的短而稳定，Stage Charter 负责工作包边界和验收门，ADR 锁定可插拔边界，Core Completion 方案负责已完成的核心闭环，治理规范负责 SDD/BDD/TDD、上下文压缩和变更留痕，本文件负责任务拆分。

1. 没有先定义任务边界，开发过程中不断添加无调用方的抽象。
2. 把 LangChain、LangGraph、Pydantic、OpenTelemetry 等已有能力重新实现一遍。
3. 为了修当前错误修改架构，造成协议、账本和运行时后续无法迁移。
4. 阶段目标从“验证决策价值”漂移成“打磨看起来复杂的基础设施”。

本文件不创建第二套架构，不覆盖 V1 中已经确认的 OD-01～OD-16；如果本文件与 V1、ADR、canonical schema 或模块 README 冲突，必须停止实现并先修订 ADR。

## 2. 总体实施原则

### 2.1 先锁价值，再锁技术

每个阶段必须先回答：

- 这一阶段给 owner 增加什么可验证价值？
- 输入和输出是什么？
- 哪一个现有框架能力可以直接复用？
- 哪些行为由代码 Gate 或账本负责，不能交给模型？
- 什么结果说明这一阶段没有价值，可以停止或回滚？

没有可测价值的基础设施不进入当前阶段。未来能力只保留契约出口，不提前创建空模块。

### 2.2 复用矩阵：什么由框架负责，什么由本项目负责

| 能力 | 直接复用 | 本项目只实现的薄层 | 明确禁止 |
|---|---|---|---|
| OpenAI-compatible 协议 | `langchain-openai.ChatOpenAI`、OpenAI SDK transport | `ProviderConfig`、能力声明、版本和错误映射 | 自写 HTTP client、协议解析或重复 SDK |
| Chat/Responses 选择 | `ChatOpenAI(use_responses_api=...)` | Runtime adapter 的配置和 canary | 在 Core、Graph、前端各自判断协议 |
| 结构化输出 | LangChain `create_agent(response_format=...)`、Pydantic v2 | 领域 schema、空字段语义和 Gate 输入归一化 | 手写 JSON parser、正则修复模型输出 |
| Agent loop | LangChain `create_agent` | Role prompt、RolePlugin 任务契约和权限 | 自写 ReAct、工具循环、会话状态机 |
| 图编排 | LangGraph `StateGraph`、子图、条件边、fan-out | decision/research/evolution 图的领域节点 | 在 Python 中另写一套 workflow engine |
| 重试 | LangGraph `RetryPolicy`、LangChain `max_retries` | 每类错误是否允许重试、总 deadline/cost budget | `except Exception: retry`、各模块自定义重试 |
| 超时/取消 | Provider client timeout、LangGraph task 生命周期、标准 `asyncio.timeout` | Run 级 deadline 传递与降级状态 | 每个 Provider 自己写一套超时线程 |
| checkpoint/恢复 | `langgraph-checkpoint-sqlite`、LangGraph recovery 机制 | checkpoint 与业务 Run 的引用关系、RecoveryWatchdog | 用业务表复制一套 graph state |
| 观测 | LangChain callback/metadata、OpenTelemetry、`structlog` | 将关键 step/call/gate 元数据投影为 Query View | 手写 tracing SDK、把全部原始 JSON 放前端 |
| 账本/迁移 | SQLAlchemy 2、Alembic、SQLite WAL | Event、Snapshot、Run、Artifact、Forecast、Outcome | 用 DSH session、Graph state 或日志当账本 |
| 评测 | 现有 Evaluation service、pytest、固定 replay fixture | 领域标签、执行基准、Promotion 规则 | 用主观报告代替可计算指标 |
| 前端数据 | 现有 React、TanStack Query、Zod、ECharts | Query/View DTO、Run Inspector 视图 | 前端直查 SQL、复制 DTO、显示无用原始 JSON |

判断标准不是“能不能自己写”，而是“是否属于 Decision Hub 的不可替换产品资产”。协议、loop、图运行、checkpoint 和 tracing 不属于产品资产；契约、PIT、Gate、Forecast/Outcome、评测和版本血缘属于产品资产。

### 2.3 一条正式生产链，多个可替换候选

R0 只保留一条正式链：

```text
TextEnvelope
  -> Product Kernel admission/PIT
  -> LangGraph decision/research graph
  -> LangChain create_agent Specialist
  -> StrategyCandidate
  -> deterministic Gate
  -> Artifact/Forecast
  -> Outcome/Evaluation
```

`FakeAgentRuntime` 和 `ReplayAgentRuntime` 用于测试；`LangGraphAgentRuntime` 是 R0 正式外部模型适配；Pi、DSH 只能通过同一个 Runtime 或 ResearchMemo port 做候选/研究对照。候选 Runtime 必须在相同 PIT fixture、相同输出契约和相同评测上证明质量、成本或可靠性优势后，才可能改变 active pointer。

## 3. 阶段总览和阶段门

| 阶段 | 目标 | 主要复用框架 | 当前状态 | 退出门槛 |
|---|---|---|---|---|
| P0 | 仓库、契约、文档和测试纪律 | Git、Pydantic、codegen、pytest | `done` | 可追溯提交、无 secret、规则可执行 |
| R0-A | 文本输入到可审计决策纵向链 | FastAPI、SQLAlchemy、Alembic、LangGraph | `done` | Fake/Replay E2E 通过 |
| R0-B | Provider 兼容和失败语义 | LangChain OpenAI、LangGraph RetryPolicy、OpenTelemetry | `done` | Chat/Responses canary、错误和预算测试通过 |
| R0-C | 观测、恢复和可回放评测 | LangGraph checkpoint、SQLite、pytest | `done` | Run Inspector、backup/recovery、PIT replay 通过 |
| R0-D | R0 发布基线 | ReleaseManifest、runbook、CI | `done` | R0 Definition of Done 全部满足 |
| R1 | 真实事件来源和按需触发 | SourcePlugin、scheduler、outbox | `done`（离线 fixture） | 授权来源、事件游标、行情基准和通知测试通过 |
| R2 | Workbench 与自主进化 | DSH MCP、replay/shadow、Promotion | `done`（离线 U2；观察期） | 候选可比较、人工晋级、可回滚 |
| R3 | 第二领域和按需部署扩展 | Domain Extension、PostgreSQL 迁移出口 | `planned` | 第二领域复用 Kernel，不复制主链 |

### 阶段门的固定顺序

每个阶段都按同一顺序执行：

```text
价值假设
  -> 契约/事件/Gate 规则
  -> ADR（如有跨边界决策）
  -> 失败测试 Red
  -> 最小实现 Green
  -> 框架能力验证
  -> 集成/E2E/回放
  -> 文档、状态和 ReleaseManifest
  -> owner 验收后进入下一阶段
```

未通过阶段门时，不扩大范围，不通过新增基础设施掩盖问题；先定位是契约、适配器、编排、数据、模型质量还是评测不足。

## 4. P0：仓库与工程纪律

状态：`done`。

已交付：Git 基线、README、架构基线、ADR、模块地图、TDD/SDD、`.gitignore`、ROADMAP、测试和本地运行手册。

后续任何任务都必须从以下事实源开始阅读：

1. `INDEX.md`
2. `DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md`
3. `docs/ROADMAP.md`
4. 本文件
5. 受影响模块的 `README.md`
6. `contracts/` 和对应测试

## 5. R0-A：文本核心纵向链

状态：`done`，后续只允许修复明确失败，不重新设计主链。

### R0-A1：输入与 admission

实现方案：使用 FastAPI/Pydantic `ObservationCreate`，通过 `AdmissionService` 生成 `TextEnvelope`、SHA-256 content hash、Event 和 Observation；重复文本和 `Idempotency-Key` 沿用现有服务语义。

不实现：ASR、网页抓取、实时监听、用户系统、自动交易。

验收：合法文本可入账；重复输入不生成重复 Event/Run；非法字段被 Pydantic 拒绝。

### R0-A2：PIT Snapshot

实现方案：使用现有 `SnapshotService.freeze()`，在研究图之前冻结 cutoff、证据 ID 和 hash；只把不可变引用放入 LangGraph state。

不实现：将未来新闻、后验行情或模型会话写入历史 Snapshot。

验收：`observed_at`、`published_at`、`received_at` 可追溯；相同 Snapshot 可回放；新事实创建 revision/generation，不覆盖旧记录。

### R0-A3：研究图和 Agent Runtime

实现方案：使用 LangGraph `StateGraph` 建立 decision/research graph；使用 `asyncio.gather` 执行独立 policy/counter 研究；每个角色调用 LangChain `create_agent`，通过 `AgentRuntime` port 返回 `AgentResult`。

不实现：自写 ReAct、把 DSH session 当 checkpoint、让 Agent 直接写账本或 Gate。

验收：Fake/Replay 结果结构稳定；并行研究失败可定位；checkpoint 不替代业务账本。

### R0-A4：Gate、Artifact、Forecast、Outcome

实现方案：保留纯代码 `evaluate_gate`；通过 `CommitDecisionService` 事务写 Artifact、GateDecision、Forecast 和 Outbox；通过 `OutcomeService` 计算 net return/Brier。

不实现：让模型自己决定发布、自动下单、用报告文本替代 Forecast。

验收：缺 facts/citations/counter-thesis/action fields 或 no_trade 时 fail-closed；发布结果具备 `30m/24h/72h` Forecast；Outcome 可回填 Evaluation。

## 6. R0-B：Provider 与 Runtime 兼容性

目标：证明外部模型可以被替换、可限时、可失败、可评估，但不自建模型网关。

### R0-B1：ProviderConfig 和能力声明

实现方案：新增一个经过 Pydantic 校验的配置对象，字段只描述 `provider_id`、`base_url`、`model`、`api_mode`、timeout、max_retries、token/cost budget 和结构化输出能力；由 Runtime adapter 读取。协议实现仍由 `ChatOpenAI` 和 OpenAI SDK 完成。

不实现：自定义 Provider SDK、在数据库保存 API key、让前端配置 secret。

验收：`responses`/`chat`、模型、超时和预算均可在启动时校验；缺 key、非法协议和未知模型在 adapter 边界失败。

### R0-B2：协议选择

实现方案：`DECISION_HUB_LLM_API_MODE=responses|chat` 只在 LangGraph Runtime adapter 解析；Responses 使用 `use_responses_api=True`、`text.format`，Chat 使用 `False`、`response_format`。Core 和 Graph 不感知 URL 或协议。

不实现：根据模型名称在多个模块隐式猜协议；同时维护两套领域调用链。

验收：同一 `AgentRequest` 可通过两个 adapter 模式产生同一 `AgentResult` 契约；正常 CI 不触网；live canary 单独记录协议和模型。

### R0-B3：结构化输出

实现方案：Pydantic v2 `AgentPayload` 作为 schema，`extra="forbid"`，所有字段 required；`create_agent(response_format=AgentPayload)` 负责 ProviderStrategy/解析。角色不适用字段返回空值，由研究图做组合。

不实现：手写 `json.loads` 修复、正则提取方向、在每个角色写独立 JSON parser。

验收：mock transport 返回合法/非法 schema 时分别成功/失败；未知字段、缺 required 字段和超范围 probability 可被测试捕获。

### R0-B4：超时、重试和错误分类

实现方案：

1. Provider 请求 timeout 使用 `ChatOpenAI(timeout=...)`。
2. 单次瞬时错误重试使用 LangChain `max_retries` 或 LangGraph node `RetryPolicy`。
3. Run 总 deadline 使用标准 `asyncio.timeout` 包住图级调用，deadline 通过 `AgentRequest` 传递。
4. Adapter 将框架异常映射为有限的产品错误码：`provider_timeout`、`provider_rate_limited`、`provider_unavailable`、`structured_output_invalid`、`configuration_invalid`。
5. `RunService` 只负责 `failed/degraded/research_only` 状态，不在每个节点另写重试。

不实现：`except Exception: retry`、无上限重试、业务层重复实现 Tenacity/HTTP client、把 429 当作 Gate 通过。

验收：timeout/429/5xx/parse failure/unknown error 都有固定测试；重试次数和总 deadline 不超过配置；失败不发布、不写交易权限对象。

### R0-B5：成本与 Provider 可观测性

实现方案：从 LangChain callback/响应 usage metadata 读取 token usage；价格使用版本化配置，不把价格逻辑复制到每个角色；Run/Step/Call 只存 usage、估算成本、provider/model/version 和错误摘要。

不实现：根据字符串长度猜 token、把原始 prompt/secret 写入 trace、为成本单独引入计费服务。

验收：Fake usage、真实 usage 和 usage 缺失分别有测试；成本缺失时标为 unknown，不伪造 0；Query View 能按 Run 汇总。

### R0-B6：Provider live canary

实现方案：沿用 `tools/canary/run_live_text_canary.py`，使用临时库和合成文本，复用正式 Runtime/Graph/Gate 路径；只输出脱敏 ID、协议、模型、Gate 状态和 hash。

不实现：把 live canary 放入普通 pytest、提交 key、使用敏感生产文本、把一次成功当成准确率证明。

验收：`gpt-5.5 + responses` 已通过兼容性；其他 Provider 必须用相同脚本验证；canary 结果写入状态文档，不写入业务事实源。

## 7. R0-C：观测、恢复和可回放评测

目标：从“能跑”提升到“可以解释哪一步、恢复哪一步、比较哪个版本”。不引入第二套业务数据库或自研 tracing 系统。

### R0-C1：Run/Step/Attempt/Call 投影

实现方案：在 LangGraph node 和 LangChain callback 边界记录最小 metadata，投影到 SQLite read model；原始模型响应默认不进前端，必要时保存脱敏内容 hash/reference。

验收：Run Inspector 能看到阶段、开始/结束时间、runtime/model/version、attempt、latency、cost、错误和 Gate 结果；同一调用具备 correlation/run ID。

### R0-C2：checkpoint 和启动恢复

实现方案：继续使用 `langgraph-checkpoint-sqlite`；RecoveryWatchdog 只从业务 Run 找非终态候选，再调用 LangGraph recovery；业务状态由 Kernel 账本裁决。

验收：模拟进程中断、关闭重开 checkpoint、重复恢复；不能重复提交 Artifact 或 Outbox；失败进入可查询状态。

### R0-C3：备份、恢复和保留

实现方案：使用 SQLite 官方 backup/integrity 能力和小型运维脚本；保留策略由配置决定；恢复到临时目录后运行 schema、ledger 和 replay smoke。

验收：日备份、恢复、integrity check、过期备份清理和失败告警都有 runbook/测试；不手工删除数据库作为清理方案。

### R0-C4：PIT fixture 和 replay

实现方案：固定文本、时间戳、证据、Provider 结果和预期 Gate；使用 Replay Runtime 和同一 LangGraph 图执行；评测按时间切分，不读取未来信息。

验收：同一 fixture 可重复运行；baseline/candidate 可以比较延迟、成本、Gate 状态、概率校准和 net return；所有结果带 strategy/runtime/pack version。

### R0-C5：Frontend Run Inspector

实现方案：扩展 Query/View DTO、FastAPI query service、TanStack Query 页面和 ECharts；前端只渲染归一化阶段、错误、证据、Gate、Forecast 和评测摘要。

不实现：前端直接展示整个 LangGraph state、原始 callback JSON 或模型 secret；不为了展示而改变账本 schema。

验收：桌面/移动端可查看运行时间线、Gate 原因和 Forecast；running/failed/degraded/publish 状态清晰；无横向溢出。

## 8. R0-D：Release 基线

目标：能把 R0 安装、运行、升级、恢复和审计，而不是只在开发机成功一次。

任务：

- [x] 生成 `ReleaseManifest`：代码、schema、pack、strategy、runtime、provider policy、migration、测试摘要。
- [x] 完成本地 Docker/原生运行手册，明确数据目录、备份、API、前端和 canary 配置。
- [x] 运行 Python、契约、类型、前端、E2E、replay、文档和安全扫描。
- [x] 记录已完成、partial、blocked 和明确不能宣称的能力。
- [x] R0 gate 通过后，冻结 R1 的契约范围。

R0 退出条件：文本输入到 Outcome/Evaluation 可回放；Provider 失败可降级；账本和 checkpoint 可恢复；Run Inspector 能解释；没有 secret；没有未记录的临时架构。

## 9. R1：Realtime Event Engine

R1 已完成离线退出门。它只把来源、市场和通知 adapter 接入 R0 文本主链，不改变既有 LangGraph、确定性 Gate、业务账本或 Product Kernel；完整 SDD/BDD/TDD 边界以 [R1 Stage Charter](stages/R1_REALTIME_EVENT_ENGINE.md) 为准。

| Task ID | 已交付的最小产品资产 | 复用能力 | 离线验收证据 |
|---|---|---|---|
| `R1-01` | `SourceManifest`、registry、Kernel-owned cursor/health/backoff、admission 后游标提交 | Pydantic、SQLAlchemy/Alembic、既有 `TextEnvelope`/Admission | 重复、非法 payload、失败保留 cursor、revision、upgrade-path 测试 |
| `R1-02` | RSS/Atom/JSON/iCalendar parser 与 Fed/BLS/BEA preset | 注入式 HTTP fetcher、标准 XML/日历库 | 固定 feed/calendar fixture、malformed/429/批次 cursor 测试 |
| `R1-03` | Meeting Copilot fragment/revision 到 `TextEnvelope` 的文本边界 | Pydantic、既有 transcript 契约 | 时间戳/revision/hash 保留；拒绝非 transcript；无音频/ASR 路径 |
| `R1-04` | `MarketDataPort`、OKX public quote/window、quality downgrade、Due Outcome | 既有 Outcome/Evaluation、HTTP adapter | bid/ask、VWAP fallback、unavailable、不伪造收益、幂等 fixture |
| `R1-05` | 单进程 `RealtimeScheduler` 与 durable source/due-outcome tick | asyncio、SQLite durable state、既有 R0 graph | 重复 tick、重启/失败恢复、到期只处理一次且不重新分析 |
| `R1-06` | committed outbox 的 local JSONL / SMTP notification adapter 和有限 retry | 既有事务 outbox、Pydantic Protocol | dedupe、retry/permanent failure、失败不改 Artifact/Forecast/Gate |
| `R1-07` | Source/Product health API、Decision Desk 摘要、完整 E2E | FastAPI、React/TanStack Query、Zod | source -> Run -> Forecast -> Outcome -> outbox fixture E2E |

R1 不包含：音频采集、ASR 推理、OCR、未授权网页抓取、搜索摘要 canonical source、自动交易、DSH/Pi 自动热路径、Redis/Kafka/Temporal/DBOS/微服务。真实来源、行情、邮件和 ASR 的授权、网络稳定性、低延迟、预测准确率与盈利能力必须在后续 Stage Charter 中单独定义可验证目标，不能由 R1 fixture 代替。

## 10. R1-L：Single-Owner Pilot Readiness（进入 R2 前）

R1-L 的完整规格、BDD 场景、TDD 故障矩阵和退出门见 [R1-L Stage Charter](stages/R1_L_SINGLE_OWNER_PILOT_READINESS.md)。它是 R1 和 R2 之间的运行边界，不增加研究能力，不接入 DSH/Pi，不改变 R0/R1 正式链。

### 10.1 价值目标

在单机、单 owner、人工决策辅助的前提下，能够在启动前判断配置/数据库/来源/行情/通知是否安全，失败时不启动长期 worker，成功后通过既有 LangGraph、Gate、账本和 outbox 运行，并留下可脱敏、可恢复、可回放的证据。它不能证明真实网络稳定、预测准确或盈利。

### 10.2 已实现的薄层

| Task ID | 实现边界 | 固定验收 |
|---|---|---|
| `R1-L-01` | `pilot-readiness.v1` canonical DTO、`PilotReadinessService`、共享 bootstrap | `tests/pilot/test_readiness.py` |
| `R1-L-02` | worker `--preflight`/`--pilot`，预检失败不进入 scheduler；保留 `--once` | `tools/pilot_acceptance.py` worker smoke |
| `R1-L-03` | local JSONL 或 SMTP adapter 组合根，复用 committed outbox/bounded retry | `tests/pilot`、`tests/providers` |
| `R1-L-04` | `/v1/pilot/readiness` 只读 API 与模块/runbook 文档 | `tests/pilot/test_entrypoints.py` |
| `R1-L-05` | 离线 acceptance、文档/状态收口；真实 Live Pilot Gate 另行授权 | `tools/pilot_acceptance.py`、`core_acceptance.py` |

### 10.3 强制约束

- readiness 不调用外部 LLM、来源、行情或 SMTP；实时连通性只能由显式 canary 证明。
- worker/API 不复制 readiness、Provider、重试或账本逻辑；统一调用 `packages/pilot_runtime` 和现有 Kernel/adapter。
- `--pilot` 要求 `DECISION_HUB_PILOT_MODE=1`，自动交易和已知私钥变量一律 fail-closed。
- 不引入新队列、数据库、workflow engine、DSH/Pi runtime 或领域对象；R2 仍需新的 Stage Charter 和 owner Stage Gate。

### 10.4 固定执行顺序

```text
R1-L-01 readiness contract/service
  -> R1-L-02 worker gate
  -> R1-L-03 notification composition
  -> R1-L-04 API/runbook
  -> R1-L-05 offline acceptance + status closeout
  -> owner Live Pilot Gate
  -> new R2 Stage Charter (not automatic)
```

## 11. R2：Decision Workbench 与自主进化

R2 的目标不是“让 Agent 自己改代码”，而是把失败样本、反馈和候选版本沉淀成可比较的个人资产。完整边界见 [R2 Stage Charter](stages/R2_DECISION_WORKBENCH_EVOLUTION.md)。R2-00 至 R2-05 的离线 U2 工程退出门已完成且未改变 R0/R1 行为；当前进入观察期，不自动执行 R3。

### R2-00：Kernel/Orchestration Boundary Alignment

复用：现有 `AgentRuntime`、LangGraph graph、checkpoint 和 R0/R1 测试；新增最小 workflow executor Port，把 LangGraph 类型和 graph factory 的组装移到 orchestration/composition root。

不实现：新的业务对象、canonical schema、迁移、MCP、DSH/Pi adapter 或第二个 workflow engine。

验收：Kernel application 不再直接导入 LangGraph/LangChain 编排类型；LangGraph contract、PIT、Gate、账本、checkpoint/recovery、API E2E 和全量测试与基线一致；模块 README、状态和 ADR-0006 同步。

### R2-A：DSH MCP / ResearchMemo

复用：DSH 的 MCP、Skill、会话和研究能力；Decision Hub 提供只读 Snapshot/Run/Artifact query 和受限 ResearchMemo command。ResearchMemo、Experiment、Candidate、Promotion 和 Rollback 的拟议语义以 R2 Stage Charter 为准，不能在本节或任务实现中自行扩展。R2-01 只能在 R2-00 通过后开始。

边界：DSH 不能写业务账本、修改 Gate、设置默认策略或触发自动交易；研究结果必须通过 schema、来源和 owner review。

验收：DSH 研究结果能引用已有 Snapshot；离线研究不重复创建同一事件分析；Memo 可追溯至 Run 和版本。

### R2-B：实验和 Shadow Runtime

复用：LangGraph configurable subgraph、Replay fixture、Evaluation service；Pi 作为 `AgentRuntime` candidate，不创建第二条正式生产链。

任务：

- [ ] baseline、LangGraph candidate、Pi candidate、DSH memo 的同契约对比。
- [ ] time-split replay、holdout、shadow 和成本/延迟统计。
- [ ] experiment registry、candidate version 和结果摘要。
- [ ] active pointer 变更前的 owner approval。

验收：候选不能绕过 Gate；不同 Runtime 可公平比较；实验结果不可被后续运行覆盖。

### R2-C：Evolution Engine 和 Promotion

复用：现有 Evaluation、版本 registry、LangGraph evolution graph；不让模型在线直接修改生产配置。

任务：

- [ ] 从失败样本/反馈生成 candidate prompt、pack、strategy 或 provider policy。
- [ ] replay -> holdout -> shadow -> owner promotion 流程。
- [ ] 回滚和 active pointer 审计。
- [ ] 失败原因分类和资产质量评分。

验收：自主进化只能产生候选；所有晋级可解释、可回滚、可重放；Gate 规则不能由 Agent 修改。

## 12. R3：多领域与部署扩展

### R3-A：A 股、美股和宏观

复用：Product Kernel 的 Task/Run/Source/Evidence/Snapshot/Artifact/Review/Evaluation；每个领域单独实现 Domain Pack。

任务：

- [ ] A 股交易时段、执行基准、数据授权和 Forecast 语义。
- [ ] 美股交易时段、盘前盘后和来源契约。
- [ ] 供应链/地缘事件传导 Pack。
- [ ] 领域之间不共享隐含的 nullable BTC 字段。

验收：第二领域使用同一 Run/Evidence/Gate/Evaluation 基础设施，但有独立契约和评测；没有复制主链。

### R3-B：PPT 等非市场产品

复用：Product Kernel 的任务、运行、来源、证据、版本、评测和观测；PPT 使用独立 `SlidePlan`、`RenderCheck` 和输出契约。

不做：为了 PPT 把市场 Forecast/Outcome 塞进通用 nullable schema；不创建另一个 Agent 平台。

验收：第二类产品证明 Kernel 可复用，同时保持领域边界。

### R3-C：存储和远程部署

复用：SQLAlchemy/Alembic repository contract、现有 API/View DTO、Docker。

只有出现跨机器高可用、多 worker 高并发写入、远程只读服务或大规模 tick 数据时，才评估 PostgreSQL；迁移存储驱动，不迁移 Event/Evidence/Snapshot/Forecast/Outcome 契约。

## 13. Codex 任务卡：每次只交付一个小目标

### 12.1 任务卡固定格式

每次给 Codex 的目标必须包含以下字段：

```text
Task ID: R0-B4
Title: Provider timeout/retry/error taxonomy
Objective: 一句话说明给 owner 增加的可验证价值
Preconditions: 必须先读的文档、ADR、现有测试和前置任务
Allowed paths: 允许修改的目录/文件
Forbidden paths: 禁止触碰的目录/职责
Reuse: 必须调用的现有 LangChain/LangGraph/Pydantic/SQLAlchemy 能力
Contract: schema、事件、错误码、版本和不变量
Test first: 先添加哪些 Red tests
Implementation: 只实现哪些最小行为
Acceptance: 可观察的通过条件
Verification: 固定命令和预期结果
Docs: 必须更新的 README、状态、路线图或 ADR
Stop conditions: 遇到什么情况停止并报告，不自行扩大范围
```

### 12.2 可直接复制给 Codex 的模板

```text
你正在实现 Decision Hub 的 <Task ID>：<Title>。

先阅读：INDEX.md、DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md、
docs/ROADMAP.md、docs/EXECUTION_PLAN.md、<受影响模块 README>、
<相关 canonical schema> 和 <相关测试>。

目标：<单一可验证价值>。
前置条件：<已完成任务/ADR>。
允许修改：<精确路径>。
禁止修改：<其他模块/禁止职责>。
必须复用：<LangChain/LangGraph/Pydantic/SQLAlchemy 的具体 API>。
禁止重复造轮子：不得自写 <协议/Agent loop/重试/trace/账本等>。

先写 Red 测试，测试必须覆盖正常、重复、拒绝和至少一个失败路径；
再做最小 Green 实现；最后只做有测试保护的 Refactor。

完成条件：<契约/状态/可观测/性能/安全验收>。
验证命令：<固定命令>。
完成后更新：<模块 README>、docs/IMPLEMENTATION_STATUS.md、
docs/ROADMAP.md 或 ADR（按需）。

如果发现现有架构、契约或框架能力不足，先停止并报告证据；
不要临时新建通用框架、第二套 DTO、第二套账本、第二套重试或空目录。
不要 push，不要写入任何 secret。
```

### 12.3 R0 已完成任务卡顺序（历史记录）

以下记录 R0 的实际执行顺序，全部已完成，不是当前待办；后续任务必须先建立并确认新的 Stage Charter：

1. `R0-B1` ProviderConfig 和 capability manifest（`done`）。
2. `R0-B4` timeout/retry/error taxonomy（`done`）。
3. `R0-B5` token usage/cost metadata 投影（`done`）。
4. `R0-C4` PIT fixture 和 replay/holdout（`done`）。
5. `R0-C1` Run/Step/Attempt/Call Query View（`done`）。
6. `R0-C2` checkpoint 中断恢复和幂等提交（`done`）。
7. `R0-C3` backup/restore/integrity runbook 与 smoke（`done`）。
8. `R0-C5` Run Inspector 前端（`done`）。
9. `R0-D` ReleaseManifest 和 R0 release gate（`done`）。

当前目标：在既有 R1 Stage Charter 内完成来源到 Outcome/outbox 的总验收和文档收口；不得顺势启动 R2 DSH/Pi Workbench 或第二领域。

每个任务结束时必须形成一个独立 commit；commit message 使用 `<type>: <single outcome>`，例如：

```text
feat: add provider capability manifest
test: cover provider timeout and retry taxonomy
feat: project run call usage metadata
test: add PIT replay fixtures
feat: expose run inspector timeline view
chore: publish R0 release manifest
```

## 14. 任务完成检查表

```text
[ ] 任务只解决一个目标，没有顺手增加未来基础设施
[ ] 先读了架构、路线图、执行设计、模块 README 和契约
[ ] 契约/事件/Gate 规则先于实现确定
[ ] 已有框架能力优先使用，没有自写替代品
[ ] Red -> Green -> Refactor 完成
[ ] 正常、重复、拒绝、超时/失败路径有测试
[ ] PIT、幂等、Gate、权限和 secret 边界没有被破坏
[ ] Query/View 没暴露 SQL、Graph state 或原始敏感 JSON
[ ] lint、type、pytest、contract check、前端测试和文档检查通过
[ ] 模块 README、状态、路线图和 ADR 已同步
[ ] commit 独立、可回滚，没有自动 push
```

## 15. Owner 确认记录

Owner 已确认以下执行纪律，不重新讨论已冻结的产品架构：

- 是否接受“架构基线不变，执行设计作为任务拆分层”的方式。
- 是否接受 R0-B -> R0-C -> R0-D 的顺序，不先做 R1 实时来源或 R2 DSH 集成。
- 是否接受 Responses 默认、Chat 显式 fallback 的 Provider adapter 方案。
- 是否接受所有超时/重试/结构化输出优先使用 LangChain/LangGraph 能力，本项目只做策略配置、错误映射和产品状态投影。
- 是否接受每次 Codex 只执行一个 Task ID，并以独立 commit、测试和文档作为完成单位。

本文件、R0-B 历史记录和 R1 Stage Charter 均已进入 `accepted`；`R0-CORE-COMPLETE` 已完成。R1 的实时来源契约和授权边界已锁定，任务仍必须重新生成 Task Context Manifest，不能把 R1 授权扩展到 R2/第二领域。
