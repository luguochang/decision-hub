# LangGraph Orchestration

## 目的
用 LangGraph 连接 admission 后的 snapshot、research subgraph、Gate 和 commit，提供可替换 Runtime 的正式图边界。

## 不负责
不写 SQL、不定义业务 DTO、不修改 Gate、不直接调用 Provider。

## 公开契约
`build_decision_graph(database, runtime)` 与 `DecisionState`。

## 依赖与禁止依赖
依赖 Kernel application/ports 和 LangGraph；不依赖 DSH/Pi 私有状态。

## 测试与回放
使用 `FakeAgentRuntime` 做确定性 graph/recovery 测试。

LangGraph checkpoint 使用 `langgraph-checkpoint-sqlite` 写入独立 checkpoint SQLite；它只保存 graph 恢复状态，不替代 Kernel 账本。`RecoveryWatchdog` 依据 Kernel 中非终态 Run 选择恢复候选。

## 最近验证
R0 scaffold。
