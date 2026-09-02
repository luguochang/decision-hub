# Kernel

## 目的
保存 Event、Observation、PIT Snapshot、Run、Artifact、Forecast、Gate、Outcome 和规范化 Research Result/Trace/Command，并通过 application service 保持状态、幂等和事务边界。

## 不负责
不负责 LangGraph topology、模型调用、HTTP DTO、ASR、DSH session 或前端状态。

## 公开契约
`packages/contracts_py/decision_hub_contracts`、`AdmissionService`、`SnapshotService`、`AnalyzeTextService`、`CommitDecisionService`、`ResearchObservabilityService`、`ResearchCommandService`、`SourceIngestionService`、`DueOutcomeService`，以及 `ports.sources` 中的 Source/Market/Notification Protocol、`ports.search.SearchCapabilityPort`、`ports.research.ResearchHarnessRuntime` 和 `ports.workflow.DecisionWorkflowExecutor`。

## 依赖与禁止依赖
Kernel domain/application/ports 不依赖 Harness、FastAPI、LangGraph/LangChain 或 Provider；persistence 只在 Kernel 内部使用 SQLAlchemy。`AnalyzeTextService` 只调用 `DecisionWorkflowExecutor`，编排框架和 checkpoint 由 composition root 注入。

## 状态/错误/权限
Agent 只能返回 candidate；`evaluate_gate` 和 `CommitDecisionService` 控制发布。重复文本按 content hash 降级为 duplicate observation。

`SearchCapabilityService` 复用 `CapabilityManifest` 的 owner 准入状态，在 transport 调用前后强制检查只读 HTTPS 权限、canonical schema、域名、timeout、成本、PIT 时间和内容 hash。真实检索默认关闭；Provider adapter 不能绕过这个执行边界。

## 数据与 Query View 所有权
Kernel 拥有 SQLite 业务账本，包括 Event/Observation/Snapshot/Run/Artifact/Forecast/Outcome/Evaluation/Outbox 和 R1 `source_states`。Query service 只读规范化表并生成 View DTO；registry、DSH session、Graph state 和 adapter 内部状态都不是业务事实。

## 测试与回放
`tests/kernel`、`tests/contracts`、`tests/replay`。`tests/contracts/test_architecture_boundaries.py` 会拒绝 Kernel 对 orchestration、LangGraph 或 LangChain 的反向依赖。

## 最近验证
`tests/kernel`、`tests/e2e`、`tests/replay`、`tests/sources`、`tests/scheduler`、`tests/migrations`、`tests/workbench`、`tests/evolution` 和 `tests/research` 覆盖账本、PIT、Gate、幂等、Outcome/Evaluation、source cursor/health、到期扫描、Step/Call/Research Trace 投影、恢复、事务回滚、Promotion/Rollback 原子性和升级路径；数据库结构由冻结 schema 的 Alembic `0001` 至 `0020` 管理。R2-R-00 至 R2-R-06E 的 candidate path 和 Runtime 价值验收已完成；当前 Fixed active、DSH candidate/shadow，真实 Search reliability 和 owner usefulness 仍待新 gate。
