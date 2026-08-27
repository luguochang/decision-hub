# ADR-0006：Kernel 与 Orchestration 边界对齐

- 日期：2026-08-27
- 状态：`accepted`（owner 于 2026-08-27 随 R2 Stage Gate 接受）
- 范围：R0/R1 代码边界、R2 进入前的架构卫生
- 相关：`DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md`、`docs/stages/R2_DECISION_WORKBENCH_EVOLUTION.md`

## 背景

Decision Hub 的长期边界约定为：Kernel application 只依赖领域契约和公开 Port，LangGraph topology、checkpoint 和框架类型属于 orchestration。R0/R1 的行为测试已经通过，但当前 `AnalyzeTextService` 直接导入 `RunnableConfig`、`CheckpointStore`、`build_decision_graph` 和 `DecisionState`。这不会立即造成运行错误，却会让替换 LangGraph、DSH、Pi 或另一种编排器时，需要修改 Kernel application。

## 决策

在 R2 业务能力开始前执行有界的 `R2-00 Kernel/Orchestration Boundary Alignment`：

1. 在 Kernel 公开一个最小的 workflow/decision executor Port，参数和结果只使用 canonical/domain 类型。
2. 将 LangGraph graph factory、checkpoint store、`RunnableConfig` 和 LangGraph state 的组装移到 orchestration 或 composition root。
3. 保持 `AnalyzeTextService` 的用例语义、PIT、Gate、账本、超时、错误码、幂等和恢复行为不变。
4. 用契约测试证明现有 LangGraph 实现仍满足该 Port；Fake/Replay 测试继续作为默认 CI 路径。
5. 不在这张卡中引入 DSH/Pi、MCP、数据库迁移、新 DTO 或第二个 workflow engine。

## 否决项

- 不为了“抽象”复制一套 AgentRuntime、checkpoint 或 retry 语义。
- 不把 LangGraph state 重新包装成业务账本。
- 不直接重写 R0/R1 历史账本、Gate 或 schema。
- 不以本 ADR 作为 R2 业务功能授权；R2 仍需通过 Stage Charter owner gate。

## 后果

Kernel application 可以保持对编排框架无感，DSH/Pi/未来编排器只需实现公开 Port；LangGraph 仍是默认正式实现。短期会增加一个很薄的 composition seam 和回归测试，但不改变现有运行结果。若后续没有第二个编排实现，Port 也必须保持最小，不能扩张成通用 workflow 平台。

## 验收证据

- R0/R1 全量测试结果与 R2-00 前后对比一致。
- Kernel import 检查不再出现 LangGraph/LangChain 编排类型。
- LangGraph orchestration contract test、checkpoint recovery、PIT replay、Gate 和 API E2E 继续通过。
- 变更记录、受影响模块 README 和实现状态同步。
