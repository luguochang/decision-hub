# ADR-0001 R0 实现基线

日期：2026-08-26
状态：accepted

## 决策

R0 使用单仓库、Python 3.12、FastAPI、SQLite WAL、LangGraph、LangChain `create_agent` 的结构化核心链；前端使用 React、TypeScript、Vite、TanStack Query、Zod 生成类型和 Apache ECharts。所有跨边界 DTO 从 `contracts/schemas` 生成。ASR 只实现 `TranscriptSourcePlugin` 协议，不进入本次核心实现。

## 背景

产品必须先验证文本到决策的真实价值，同时保证后续可替换 Runtime、Provider、Pack 和来源适配器，不把 DSH 或某个模型供应商绑定进账本。

## 否决项

- 不 clone 或 fork DSH 作为产品前端或核心运行时。
- 不实现第二套正式 Agent runtime；Pi 保留未来 adapter 边界。
- 不用一次性脚本替代账本、PIT、Gate、Outcome 和评测。
- 不先投入 ASR、直播、新闻监听或通知。

## 后果

R0 的业务代码比一个 Prompt demo 多，但能够从第一天保存事件、证据、运行、Gate、预测与结果，并可用 fake/replay runtime 做确定性测试。首版不承担多用户、自动交易或公网安全边界。

## 迁移/回滚

策略、Pack、Runtime、契约和数据库 migration 都有版本；新实现通过 contract/replay/failure tests 后才可成为 active。失败时回滚 ComponentVersion，不覆盖历史 Artifact。

## 受影响契约

`contracts/schemas/*.schema.yaml`、`contracts/events/*.schema.yaml`、`contracts/policies/*.schema.yaml`

## 受影响测试

契约校验、PIT、Gate、幂等、checkpoint/recovery、replay、API 和 Decision Desk E2E。
