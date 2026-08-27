# LangGraph Orchestration

## 目的
用 LangGraph 连接 admission 后的 snapshot、research subgraph、Gate 和 commit，提供可替换 Runtime 的正式图边界。R2 增加 bounded Supervisor、`Send` 动态 specialist fan-out、最多一次 replan 和 candidate-only synthesis；这些能力只用于候选/评测边界，不绕过 R0/R1 正式 Gate。

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

R2 Supervisor 使用 LangGraph `StateGraph`/`Send` 调度声明式 capability task，要求的 specialist coverage 由代码校验，最多只为缺失 capability replan 一次，最终只返回 candidate，不写账本、Gate 或 active pointer。`tests/orchestration` 和 `tests/runtime/test_candidate_runtime_contract.py` 覆盖恢复、有限 replan、统一失败分类及 replay/holdout/离线 shadow 候选边界。
