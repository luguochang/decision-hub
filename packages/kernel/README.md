# Kernel

## 目的
保存 Event、Observation、PIT Snapshot、Run、Artifact、Forecast、Gate 和 Outcome，并通过 application service 保持状态、幂等和事务边界。

## 不负责
不负责 LangGraph topology、模型调用、HTTP DTO、ASR、DSH session 或前端状态。

## 公开契约
`packages/contracts_py/decision_hub_contracts`、`AdmissionService`、`SnapshotService`、`AnalyzeTextService`、`CommitDecisionService`、`SourceIngestionService`、`DueOutcomeService`，以及 `ports.sources` 中的 Source/Market/Notification Protocol 和 `ports.workflow.DecisionWorkflowExecutor`。

## 依赖与禁止依赖
Kernel domain/application/ports 不依赖 Harness、FastAPI、LangGraph/LangChain 或 Provider；persistence 只在 Kernel 内部使用 SQLAlchemy。`AnalyzeTextService` 只调用 `DecisionWorkflowExecutor`，编排框架和 checkpoint 由 composition root 注入。

## 状态/错误/权限
Agent 只能返回 candidate；`evaluate_gate` 和 `CommitDecisionService` 控制发布。重复文本按 content hash 降级为 duplicate observation。

## 数据与 Query View 所有权
Kernel 拥有 SQLite 业务账本，包括 Event/Observation/Snapshot/Run/Artifact/Forecast/Outcome/Evaluation/Outbox 和 R1 `source_states`。Query service 只读规范化表并生成 View DTO；registry、DSH session、Graph state 和 adapter 内部状态都不是业务事实。

## 测试与回放
`tests/kernel`、`tests/contracts`、`tests/replay`。`tests/contracts/test_architecture_boundaries.py` 会拒绝 Kernel 对 orchestration、LangGraph 或 LangChain 的反向依赖。

## 最近验证
`tests/kernel`、`tests/e2e`、`tests/replay`、`tests/sources`、`tests/scheduler`、`tests/migrations`、`tests/workbench` 和 `tests/evolution` 覆盖账本、PIT、Gate、幂等、Outcome/Evaluation、source cursor/health、到期扫描、Step/Call 投影、恢复、Promotion/Rollback 原子性和升级路径；数据库结构由冻结 schema 的 Alembic `0001` 至 `0015` 管理。R2 离线 U2 工程退出门已完成，真实生产 shadow 和效果仍未证明。
