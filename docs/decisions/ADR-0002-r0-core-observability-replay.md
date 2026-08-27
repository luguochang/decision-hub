# ADR-0002 R0 核心观测、回放与成本语义

日期：2026-08-26  
状态：accepted

## 决策

R0 将 `Run/Step/Attempt/Call` 作为同一业务运行的最小规范化观测投影：LangGraph 节点边界写入 `RunStepRecord`，每次 Provider 调用写入 `RunCallRecord`，重试保留新的 attempt，不覆盖历史。业务账本仍由 Kernel 拥有，LangGraph checkpoint 只保存恢复所需引用。

Provider usage/cost 采用显式 unknown 语义。只有 Provider 返回 token usage 且存在版本化价格配置时，Call 才记录 `estimated` 成本；启用 cost budget 但无法计费时确定性返回 `budget_accounting_unavailable`，累计成本超过预算时返回 `budget_exhausted`。任何失败都不能创建 Artifact、Forecast 或 Outbox。

固定 PIT replay 使用注入的 `received_at` clock 和语义内容 hash，baseline/candidate 使用独立 SQLite 数据库；Outcome/Evaluation 通过现有 `OutcomeService` 写入，不创建第二套评测实现。旧数据库通过 Alembic `0007_run_cost_nullable` 迁移到可空 Run cost。

## 背景

产品需要能解释运行在哪一步、调用了哪个 Provider、重试了几次、成本是否可信，并且能在升级已有本地数据库后准确表示 Provider 未提供成本的情况。一次性 timeline 或 `$0` 默认值会掩盖失败和成本未知，无法支撑回放与长期产品维护。

## 候选方案

- 继续把节点事件 timeline 当作全部观测：实现少，但无法表达 Step attempt 与模型 Call 的独立生命周期。
- 引入独立 tracing/计费平台：信息更丰富，但增加第二事实源、部署和维护边界。
- 在现有 LangGraph、Kernel SQLite 和 Query/View 上做最小投影：复用框架能力，保留业务事实源和可迁移边界。

## 否决项

- 不把原始 Graph state、prompt、secret 或完整 Provider response 写入前端或 checkpoint。
- 不以随机重跑覆盖失败 attempt，不把 timeline 冒充 Step。
- 不把未知成本写成 `0`，不在每个角色复制计费逻辑。
- 不为回放另建账本、另建 Outcome/Evaluation 或依赖实时网络。

## 后果

R0 多维护两张轻量 read-model 表和一个迁移，但 Run Inspector 能显示完整执行证据，失败和恢复可查询，PIT replay 的版本比较具备可重复基础。历史 Run 没有 retroactive Step/Call；它们会保留原 timeline，升级后新 Run 使用完整投影。

## 迁移/回滚

`0005_run_steps`、`0006_call_pricing` 和 `0007_run_cost_nullable` 按 Alembic 顺序升级；回滚前必须保留 SQLite backup。若新观测投影出现问题，可回滚 Runtime/Graph component pointer，但不能删除已写入的业务 Run、Artifact 或 Evaluation。

## 受影响契约

`RunInspectorView.steps`、`StepView`、`CallView.pricing_version`、`AgentUsage.pricing_version`、`ReplayReport` 的 Brier/net return 摘要。

## 受影响测试

`tests/e2e/test_runtime_safety.py`、`tests/e2e/test_api_flow.py`、`tests/replay/test_checkpoint.py`、`tests/replay/test_run_fixture.py`、`tests/runtime/test_langgraph_agent_runtime.py`、fresh Alembic/core acceptance smoke。
