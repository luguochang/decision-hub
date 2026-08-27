# ADR-0003：R1 实时来源与可插拔适配器边界

日期：2026-08-27
状态：accepted（owner 已授权 R1 实现）

## 决策

R1 采用“Source/Market/Notification Port + adapter registry”的可插拔边界：

```text
第三方来源/HTTP/交易所/邮件 SDK
  -> Adapter
  -> Pydantic canonical DTO / Protocol
  -> Kernel admission/PIT/Outcome/outbox
  -> 现有 R0 LangGraph + Gate
```

来源插件只负责拉取、解析、游标、revision、健康和 `TextEnvelope` 产出；市场插件只负责事实行情快照；通知插件只消费已提交 outbox。所有正式业务状态仍归 Decision Hub Kernel，DSH/Pi 不进入 R1 自动链。

## 理由

R0 已经证明文本之后的决策闭环可回放、可审计和可评估。把实时来源直接写进 Graph 或 DSH 会复制 admission、PIT、Gate、重试和账本，之后更换来源或入口必须迁移历史状态。Port/adapter 让来源、市场 Provider、通知渠道和 DSH/MCP 都能在不改变 R0 契约的前提下替换。

## 否决项

- 不把来源插件做成直接返回 `long/short` 的“信号插件”。
- 不让 cursor 在 admission 失败、解析失败或部分批次失败时推进。
- 不把未授权网页或搜索摘要作为唯一事实源。
- 不让通知失败重新触发分析。
- 不在 R1 引入 Redis、Kafka、Temporal、DBOS、Postgres 或微服务拆分。
- 不为插件重写 HTTP client、XML parser、retry 或 Agent loop；优先复用 `httpx`、标准库 XML、LangGraph/R0 application service。
- 不让 DSH session、Graph state 或原始 Provider JSON 成为业务事实。

## 后果

R1 会增加少量 registry/state 表和 adapter 代码，但自动链、人工文本和未来 DSH/MCP 使用同一条 admission/PIT/Gate/账本路径。来源健康和游标可独立恢复，市场数据缺失能显式降级，通知可替换且可幂等。真实 Feed、行情和邮件仍需单独 live canary，不能用 fixture 结果宣称实时效果或盈利。

## 受影响范围

- `packages/kernel/decision_hub_kernel/ports/`：Source、Market、Notification Protocol。
- `packages/source_adapters/`：registry、official feed、transcript。
- `packages/provider_adapters/`：market、notification。
- `apps/hub_worker/`、`apps/hub_api/`：组合根和受限健康/手动入口。
- `contracts/`、迁移、受影响模块 README、R1 Stage Charter 和测试。

## 回滚

每张 R1 任务卡独立提交；active source/market/notification 通过 manifest 配置切换。回滚 adapter 不删除已经入账的 Event、Observation、Snapshot、Forecast、Outcome 或 Outbox；失败状态保留并可用 fixture/replay 重放。
