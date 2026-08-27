# Kernel

## 目的
保存 Event、Observation、PIT Snapshot、Run、Artifact、Forecast、Gate 和 Outcome，并通过 application service 保持状态、幂等和事务边界。

## 不负责
不负责 LangGraph topology、模型调用、HTTP DTO、ASR、DSH session 或前端状态。

## 公开契约
`packages/contracts_py/decision_hub_contracts`、`AdmissionService`、`SnapshotService`、`AnalyzeTextService`、`CommitDecisionService`、`SourceIngestionService`、`DueOutcomeService`，以及 `ports.sources` 中的 Source/Market/Notification Protocol。

## 依赖与禁止依赖
Kernel domain/ports 不依赖 Harness、FastAPI 或 Provider；persistence 只在 Kernel 内部使用 SQLAlchemy。

## 状态/错误/权限
Agent 只能返回 candidate；`evaluate_gate` 和 `CommitDecisionService` 控制发布。重复文本按 content hash 降级为 duplicate observation。

## 数据与 Query View 所有权
Kernel 拥有 SQLite 业务账本，包括 Event/Observation/Snapshot/Run/Artifact/Forecast/Outcome/Evaluation/Outbox 和 R1 `source_states`。Query service 只读规范化表并生成 View DTO；registry、DSH session、Graph state 和 adapter 内部状态都不是业务事实。

## 测试与回放
`tests/kernel`、`tests/contracts`、`tests/replay`。

## 最近验证
`tests/kernel`、`tests/e2e`、`tests/replay`、`tests/sources`、`tests/scheduler` 和 `tests/migrations` 覆盖账本、PIT、Gate、幂等、Outcome/Evaluation、source cursor/health、到期扫描、Step/Call 投影、恢复和升级路径；数据库结构由冻结 schema 的 Alembic `0001` 至 `0010` 管理。
