# LangGraph Orchestration

## 目的
用 LangGraph 连接 admission 后的 snapshot、research subgraph、Gate 和 commit，提供可替换 Runtime 的正式图边界。R2 增加 bounded Supervisor、`Send` 动态 specialist fan-out、最多一次 replan 和 candidate-only synthesis；这些能力只用于候选/评测边界，不绕过 R0/R1 正式 Gate。

## 不负责
不写 SQL、不定义业务 DTO、不修改 Gate、不直接调用 Provider。

## 公开契约
`build_decision_graph(database, runtime)`、`DecisionState`、`LangGraphDecisionExecutor`、
`LangGraphResearchExecutor` 与 composition root `build_analyze_text_service(...)`。

R2-R 的 `build_agentic_research_graph(...)` 是独立的研究入口：每轮调用同一个
`ResearchHarnessRuntime`，再由 Kernel 的确定性 sufficiency/conflict Gate 决定是否继续。
图状态只保存 JSON-compatible contract projections；DSH session、账本和最终发布权不在
LangGraph state 中。

## 依赖与禁止依赖
依赖 Kernel application/ports 和 LangGraph；不依赖 DSH/Pi 私有状态。Kernel 不反向导入本模块；API、worker、测试和工具必须通过 composition root 组装 `AnalyzeTextService`。

## 测试与回放
使用 `FakeAgentRuntime` 做确定性 graph/recovery 测试。

LangGraph checkpoint 使用 `langgraph-checkpoint-sqlite` 写入独立 checkpoint SQLite；它只保存 graph 恢复状态，不替代 Kernel 账本。`RecoveryWatchdog` 依据 Kernel 中非终态 Run 选择恢复候选。

## 最近验证
Decision Graph 已使用 LangGraph `RetryPolicy`、SQLite checkpoint 和四个 Step 边界；研究节点的 policy/counter reviewer 并行，失败和重试 attempt 投影到 Kernel Inspector。R2-00 已把 graph/checkpoint/config 组装移出 Kernel，`tests/contracts/test_architecture_boundaries.py`、`tests/replay/test_checkpoint.py` 与 `tests/e2e/test_runtime_safety.py` 覆盖依赖方向、恢复、幂等和失败安全。

R2 Supervisor 使用 LangGraph `StateGraph`/`Send` 调度声明式 capability task，要求的 specialist coverage 由代码校验，最多只为缺失 capability replan 一次，最终只返回 candidate，不写账本、Gate 或 active pointer。`tests/orchestration` 和 `tests/runtime/test_candidate_runtime_contract.py` 覆盖恢复、有限 replan、统一失败分类及 replay/holdout/离线 shadow 候选边界。

R2-R-03 增加 `decision/sufficiency.py` 的 freshness、authority、独立来源和 conflict
判定。模型自报 coverage 只作为候选，不能绕过该 Gate；研究图在 round budget、tool
budget 或 no-progress 时 bounded stop，并始终通过双 Snapshot 服务冻结触发证据与研究证据。

R2-R-05 在 executor 边界把产品生命周期事件和 DSH tool/subagent/model 事件统一映射为
规范化 `ResearchTraceEvent`；LangGraph state 和 DSH raw payload 仍不进入业务 Trace。
checkpoint 恢复、Result/Artifact 原子提交和 Trace 幂等由 Kernel/worker 测试覆盖。

R2-L Evolution executor 每次建立评测计划时动态读取当前 active candidate：release baseline 使用内置 baseline runtime，已由 owner 晋级且具有 immutable artifact 的 candidate 从 artifact store 恢复配置。Candidate/Experiment/Result/raw report 已存在时按 job/stage/input hash 复用，worker 重启不会重新注册资产，也不会自动 Promotion。

G2-AF 在 `AgenticResearchState` 中记录每个 hard gap 的已尝试 capability 和 retryable failure。
`route_after_round` 只有在 sufficient、预算耗尽或所有可执行能力均已尝试时才停止；无新增证据
但存在未尝试 fallback 时继续。每轮 capability ladder 是 Pack 声明与当前 Run
`allowed_capabilities` 的交集：优先尚未尝试 route；只有本轮新增了可信 Evidence、需要再次综合时，
才在总预算内复用已启用 route。Pack 中存在但部署未启用的 fallback 不会进入 DSH 计划，也不能在
结果映射阶段把已成功提交 synthesis 的回合推翻为 Worker failure。无剩余可执行 route 时进入确定性
bounded finalize。该逻辑仍不执行 provider，所有调用经 DSH runtime 和 Hub Gateway。

如果至少一个完整可信 round 已落账，而后续 round 因 retryable Provider/transport timeout 失败，
图会保留上一轮 attested synthesis 和当前已持久化 Evidence，把失败 provenance 写入状态，并强制
以 `degraded/research_only` 进入 Artifact、Outbox 和复查链。首轮失败、schema/PIT/权限或代码错误
仍 hard fail；禁止为了生成报告吞掉这些错误。真实工具计数从 Hub durable reservation 读取，
`crypto_macro` 当前总预算为 24 次且跨 generation 不重置。
