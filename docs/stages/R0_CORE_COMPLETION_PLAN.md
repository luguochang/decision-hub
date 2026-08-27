# R0 Core Completion 实现方案

版本：`CORE-R0-2026-08-26.v1`
状态：`done`（2026-08-26 离线 core acceptance、fresh migration、回放/恢复/备份和前端构建通过）；不代表预测准确率、盈利能力或 R1 实时能力。
对应架构：`DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md`
对应治理：[全局开发治理规范](../engineering/DEVELOPMENT_GOVERNANCE.md)

## 1. 为什么要有这个总目标

仓库已完成一条可运行的文本纵向链，并补齐 Provider、观测、恢复、回放和发布能力。该总目标把这些能力作为一个整体验收，避免只交付无法解释、恢复或评估的局部 scaffold。

因此本方案把 R0 的核心完成定义为一个整体目标：从一段人工输入文本开始，到可查询的决策结果、Forecast、Outcome/Evaluation、运行时间线、失败降级、进程恢复、PIT 回放、备份恢复和本地发布自测全部闭环。所有工作包都必须完成，才允许把 R0 标记为 `done`。

## 2. 总目标

目标 ID：`R0-CORE-COMPLETE`

目标名称：`Decision Hub Core Production Baseline`

目标陈述：在单机本地环境中，用一条正式的 Decision Hub 主链，把文本证据稳定转换为可审计、可解释、可回放、可量化评估的决策支持结果，并且能在 Provider 失败或进程中断时安全降级和恢复。

完成后 owner 必须能完成以下演示，而不依赖聊天解释：

1. 提交一段中文或英文文本和 `Idempotency-Key`。
2. 在 Decision Desk 看到该 Run 的状态、阶段时间线、模型/Runtime/策略版本、Provider 调用次数、耗时、错误/降级原因、Gate 结果和 Forecast。
3. 展开结果只看到归一化的事实、推断、反方、传导链、引用、触发条件和失效条件，不看到无用的原始 LangGraph state 或 secret。
4. 注入 Provider timeout、429、5xx、结构化输出失败或配置错误时，Run 可查询、可解释、fail-closed，不创建错误的发布结果，不重复发送 Outbox。
5. 模拟进程中断后重新启动，能够从 checkpoint/业务 Run 的边界恢复或明确进入 failed/degraded，不能重复提交 Artifact 或 Outbox。
6. 用固定 PIT fixture 离线重复运行，并比较 baseline/candidate 的状态、延迟、成本、校准和 net return；结果带版本且不读取未来信息。
7. 备份数据库到临时目录、运行 integrity check、恢复后重新执行 replay smoke。
8. 用一条 release/runbook 命令完成本地安装、自测和能力声明；明确哪些是已验证、未验证和不能宣称的。

## 3. 核心范围

### 3.1 必须完成

- 文本 admission、content hash、幂等和 PIT Snapshot。
- LangGraph decision/research graph 与 LangChain structured agent runtime。
- Responses/Chat Provider adapter、配置能力声明、timeout、bounded retry 和产品错误分类。
- `AgentResult` 统一契约和结构化输出 fail-closed。
- Run/Step/Attempt/Call 最小可观测 read model，以及 Query/View API。
- Gate、Artifact、30m/24h/72h Forecast、Outbox、Outcome、Brier/net return Evaluation。
- LangGraph checkpoint 与业务 Run 分离但可关联；进程中断、重启、重复恢复和幂等提交测试。
- SQLite backup、restore、integrity check、retention smoke 和本地 runbook。
- PIT fixture、Replay Runtime、时间切分 replay/holdout 对比和结果版本血缘。
- Decision Desk 的 Run Inspector：状态、时间线、调用摘要、错误、证据、Gate、Forecast、评测摘要。
- ReleaseManifest、完整离线测试、外部 Provider opt-in canary 和 secret 扫描。

### 3.2 明确不属于本目标

- 实时新闻、官方日历、行情 Provider、直播监听和 ASR。
- Email、IM、桌面通知和自动调度。
- DSH/Pi 生产集成、MCP、Skill marketplace 和自主晋级。
- 自动交易、扣费、用户系统和公网多租户。
- A 股、美股、PPT 等第二领域 Domain Pack。
- Redis、Kafka、Temporal、DBOS、Postgres、Kubernetes 或微服务拆分。

这些能力只保留已有 Port/Schema 出口，必须在 R0 完成后另立阶段和 ADR，不能为了“看起来完整”混入核心目标。

## 4. 固定架构和复用边界

正式主链保持一条：

```text
TextEnvelope
  -> Admission / Event / Observation
  -> PIT EvidenceSnapshot
  -> LangGraph decision/research graph
  -> LangChain create_agent structured roles
  -> AgentResult / StrategyCandidate
  -> deterministic Gate
  -> Artifact + Forecast + Outbox
  -> Outcome + Evaluation
  -> Query/View + Decision Desk Run Inspector
```

运行与恢复边界：

```text
ProviderConfig / Capability
  -> Runtime Adapter
  -> LangChain ChatOpenAI
  -> LangGraph node / RetryPolicy / checkpoint
  -> Run/Step/Attempt/Call projection
  -> Kernel business ledger
```

必须复用：LangChain `ChatOpenAI`、`create_agent`、structured response、LangGraph `StateGraph`、`RetryPolicy`、checkpoint、Pydantic v2、SQLAlchemy/Alembic、SQLite WAL、React/TanStack Query/Zod、现有 Evaluation 和 Outbox。

禁止新增：自写 HTTP client、JSON parser、ReAct loop、workflow engine、retry framework、tracing SDK、第二套 DTO、第二个账本或前端原始 JSON 浏览器。

## 5. 当前基线和真实缺口

| 能力 | 当前事实 | R0 完成前必须补齐 |
|---|---|---|
| 文本主链 | Fake/Replay E2E 已能从文本到 Evaluation | 保持契约，增加完整集成验收 |
| ProviderConfig | Pydantic 配置、Responses/Chat 和 capability manifest | 已完成 timeout/retry/error taxonomy、usage/cost 和统一失败投影 |
| AgentResult | 严格结构化 payload 和 unknown/estimated cost 语义 | 已完成错误和版本元数据 |
| Run 记录 | Run、Step/Attempt、Timeline 和三角色 Call projection | 已完成 correlation、Gate/provider 摘要和恢复语义 |
| Graph checkpoint | 独立 SQLite checkpoint 与业务 Run 关联 | 已完成正式 graph 接入、恢复/重复提交测试 |
| RecoveryWatchdog | 从业务 Run 查询非终态候选 | 已完成恢复策略、失败状态和幂等边界 |
| Replay | 版本化 PIT fixture、固定 clock 和三 horizon Outcome | 已完成 holdout、baseline/candidate 对比与未来信息拒绝 |
| Backup | SQLite backup/integrity/restore/retention 工具 | 已完成恢复后 schema、账本和 replay smoke |
| Frontend | Decision Desk 真实 Inspector | 已完成 Step/Call、证据/Gate/Forecast/Evaluation 交互；视觉回归仍可在 R1 维护 |
| Release | 一键 core acceptance 和本地运行手册 | 已完成 ReleaseManifest、离线 gate、升级/恢复记录 |

## 6. 工作包和依赖

这些是一个总目标内的实施工作包，不是互相独立的产品阶段。所有工作包完成后才通过 `R0-CORE-COMPLETE`。

### WP-1：Provider Reliability Boundary

范围：补齐 R0-B2～R0-B6。

输入：`ProviderConfig`、`AgentRequest`、PIT Snapshot。

输出：统一 `AgentResult`、有限错误码、Run deadline、bounded retry、usage/cost metadata、canary 摘要。

必须覆盖：Responses/Chat、timeout、429、5xx、结构化输出失败、未知异常、缺 key、预算耗尽、usage 缺失。

退出门：普通 CI 不触网；每个失败码都有确定性状态；失败不能发布、不能写交易权限、不能重复 outbox；成本未知不是 0。

### WP-2：Run Observability and Query View

范围：Run/Step/Attempt/Call 的业务 read model 和 API。

输入：LangGraph node、LangChain callback、Gate、Outbox 的最小 metadata。

输出：Run Inspector DTO，包含阶段、开始/结束时间、attempt、latency、runtime/model/schema version、cost、错误摘要、Gate 原因、correlation/run ID。

必须覆盖：正常、并行 reviewer、重试、降级、失败和重复请求。

退出门：前端不读取 SQL、Graph state 或原始 response；同一个 run 可以解释“在哪一步、调用了什么、为什么降级”。

### WP-3：Checkpoint Recovery and Durability

范围：正式 graph checkpoint 关联、启动恢复、业务幂等、SQLite 备份恢复。

输入：业务 Run 状态和 LangGraph thread/checkpoint。

输出：可恢复或可解释失败的 Run；backup、restore、integrity、retention smoke。

必须覆盖：中断在 admission、snapshot、research、gate/commit 前后；重启、重复恢复、已提交 Artifact 不重复提交。

退出门：业务账本仍是事实源；checkpoint 只保存编排状态；Outbox 只发送一次。

### WP-4：PIT Replay and Evaluation Baseline

范围：固定 fixture、Replay Runtime、时间切分、holdout、baseline/candidate 对比。

输入：固定文本、三时间戳、证据、Provider 结果、策略/Pack/Runtime 版本。

输出：可重复的 ReplayReport，至少包含 Gate status、延迟、cost/unknown、Brier、net return、失败分类和版本血缘。

必须覆盖：相同 fixture 重复运行、未来信息拒绝、candidate 不覆盖 baseline、失败样本可定位。

退出门：没有 replay/holdout 证据时，不宣称模型或策略有效。

### WP-5：Decision Desk Run Inspector

范围：前端 Query/View 和真实后端数据绑定。

输入：WP-2 的 normalized Query/View DTO。

输出：可扫描的运行列表、阶段时间线、调用摘要、错误/降级、证据和 Gate、Forecast、Outcome/Evaluation。

必须覆盖：`running`、`completed/publish`、`degraded`、`failed`、无 artifact、无 cost、移动端窄屏。

退出门：不展示无用 JSON，不泄露 secret，用户能从页面判断结果是否可发布、哪里失败、如何回放。

### WP-6：Release and Core Acceptance

范围：ReleaseManifest、runbook、离线 gate、canary 记录和总体验收。

输入：WP-1～WP-5 的代码、测试、文档和版本。

输出：本地一键自测/安装/备份/恢复说明，能力矩阵和不可宣称项。

退出门：从干净临时目录可启动；所有离线检查通过；canary 明确 opt-in；没有 secret；R0 总体验收场景通过。

## 7. BDD 总体验收场景

```text
Feature: Core decision loop
Scenario: 文本进入并产生可审计结果
Given 一个固定文本、PIT 时间戳和 Fake/Replay Runtime
When owner 通过 API 提交文本和 Idempotency-Key
Then 系统创建唯一 Event、Observation、Snapshot 和 Run
And Decision Desk 显示时间线、候选、Gate、三个 Forecast 和版本血缘
And 结果可录入 Outcome 并生成 Evaluation
```

```text
Feature: Provider failure safety
Scenario: 外部模型失败时不发布错误决策
Given 一个冻结 Snapshot 和有界 Run deadline
When Provider 返回 timeout、429、5xx 或非法 structured output
Then Run 记录有限错误码并进入 failed/degraded/research_only
And 不创建错误 publish Artifact，不发送重复 Outbox
```

```text
Feature: Recovery
Scenario: 进程中断后恢复不重复提交
Given 一个处于 running 的业务 Run 和对应 LangGraph checkpoint
When 进程在 research 或 commit 边界中断后重启
Then 系统只恢复可恢复步骤或明确进入 failed/degraded
And 已存在的 Artifact、Forecast 和 Outbox 不重复创建
```

```text
Feature: Replay evaluation
Scenario: 同一 PIT fixture 可重复比较
Given 相同文本、三时间戳、evidence、strategy、pack 和 runtime version
When baseline 与 candidate 分别离线回放
Then 两者都有独立 ReplayReport 和可比较的 Gate、延迟、成本、Brier、net return
And candidate 不能覆盖 baseline 或读取 cutoff 之后的信息
```

```text
Feature: Operable release
Scenario: 从临时目录恢复核心产品
Given 一份 ReleaseManifest 和 SQLite backup
When 在干净数据目录执行安装、自测、restore 和 replay smoke
Then API、Decision Desk、账本、checkpoint 和 Query/View 可用
And 报告明确已验证、未验证和不能宣称的能力
```

## 8. TDD / SDD / ADR 交付顺序

总目标按以下顺序推进，但每个工作包必须在同一个总目标下完成：

```text
总目标 SDD + 本文契约
  -> 必要 ADR
  -> WP-1~WP-5 的 BDD 场景
  -> 失败注入和契约测试 Red
  -> 最小实现 Green
  -> Refactor
  -> Core E2E / replay / recovery / backup smoke
  -> Run Inspector 人工验收
  -> ReleaseManifest + 文档状态
  -> R0-CORE-COMPLETE Gate
```

每个工作包仍然形成独立 commit，方便回滚；但任何单个工作包完成都不能被描述为核心产品完成。

## 9. 完成定义（Definition of Done）

`R0-CORE-COMPLETE` 只有同时满足以下条件才算完成：

- 文本到 Outcome/Evaluation 的正式链通过 Fake、Replay 和显式 live canary。
- Provider timeout、429、5xx、结构化失败、配置错误和未知异常均有确定性错误语义。
- Run/Step/Attempt/Call、PIT、Gate、版本、幂等、权限和 secret 边界可查询且有测试。
- checkpoint、业务账本、Outbox 的恢复和重复提交边界通过测试。
- PIT replay/holdout 报告可重复，baseline/candidate 可比较，不能宣称准确率或盈利超出证据。
- backup/restore/integrity/retention smoke 通过。
- Decision Desk Run Inspector 展示归一化信息，无原始 JSON 垃圾和 secret。
- ReleaseManifest、runbook、模块 README、IMPLEMENTATION_STATUS、ROADMAP、CHANGELOG 已同步。
- `git diff --check`、module docs、contract check、pytest、ruff、pyright、frontend test/build 通过。
- 核心 BDD 总体验收场景通过；R0 仍未包含 R1/R2/R3 能力。

## 10. 总目标完成后的下一步

只有 `R0-CORE-COMPLETE` 通过后，才允许另立 `R1 Realtime Event Engine` 目标。R1 再接入官方日历、新闻、行情、ASR、scheduler 和通知；R2 再接入 DSH/Pi ResearchMemo、Workbench 和自主进化。不得在 R0 未完成时用这些扩展掩盖核心链路缺口。
