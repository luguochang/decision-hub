# LangGraph Orchestration

## 目的
用 LangGraph 连接 admission 后的 snapshot、research subgraph、Gate 和 commit，提供可替换 Runtime 的正式图边界。

## 不负责
不写 SQL、不定义业务 DTO、不修改 Gate、不直接调用 Provider。

## 公开契约
`build_decision_graph(database, runtime)`、`DecisionState`、`LangGraphDecisionExecutor` 与 composition root `build_analyze_text_service(...)`。

## 依赖与禁止依赖
依赖 Kernel application/ports 和 LangGraph；不依赖 DSH/Pi 私有状态。Kernel 不反向导入本模块；API、worker、测试和工具必须通过 composition root 组装 `AnalyzeTextService`。

## 测试与回放
使用 `FakeAgentRuntime` 做确定性 graph/recovery 测试。

LangGraph checkpoint 使用 `langgraph-checkpoint-sqlite` 写入独立 checkpoint SQLite；它只保存 graph 恢复状态，不替代 Kernel 账本。`RecoveryWatchdog` 依据 Kernel 中非终态 Run 选择恢复候选。

## 最近验证
Decision Graph 已使用 LangGraph `RetryPolicy`、SQLite checkpoint 和四个 Step 边界；研究节点的 policy/counter reviewer 并行，失败和重试 attempt 投影到 Kernel Inspector。R2-00 已把 graph/checkpoint/config 组装移出 Kernel，`tests/contracts/test_architecture_boundaries.py`、`tests/replay/test_checkpoint.py` 与 `tests/e2e/test_runtime_safety.py` 覆盖依赖方向、恢复、幂等和失败安全。
