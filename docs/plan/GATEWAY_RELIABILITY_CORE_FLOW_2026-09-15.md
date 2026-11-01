# Decision Hub 网关核心链路目标（2026-09-15）

状态：`COMPLETE / GATEWAY_CORE_RELIABILITY_READY`

本目标只处理一条主链：**输入研究意图 → 网关校验/预算 → Provider 取证 → Evidence/Fact 持久化 → 可审计输出**。不以增加页面、角色入口、PPT、调度、WebSocket 平台或交易执行来替代核心链路验证。

## 1. 为什么现在做这个目标

上一轮价值验证已经得到 `STOP_PRODUCT_LAYER_INVESTMENT`：当前冻结样本只有 1/36 个方向判断，方向覆盖率 2.78%，概率全部未校准，尚不足以证明交易价值。网关可靠性仍然是后续任何信号验证的必要前提，但它不能改变这个价值结论。

因此本轮的交付不是“把产品做大”，而是把最小研究链路做成可重复、可拒绝、可审计的工程底座；完成后要么进入独立信号内核验证，要么明确停止，不再继续堆外围功能。

## 2. 当前核心链路

```text
研究输入
  -> Run / Session / Event lineage
  -> DurableResearchCapabilityGateway
       - 会话、轮次、事件、终态校验
       - 幂等 reservation
       - deadline / PIT / 字段契约
  -> ProviderCapabilityRouter
       - approved route / domain / field 匹配
       - 有限次 fallback，受同一 deadline 约束
  -> 原生 Provider adapter（当前 BTC/USDT REST）
  -> canonical result
  -> Evidence + typed Fact 原子落库
  -> trace / tool outcome / 查询投影
  -> DSH 或人工研究输出
```

本轮把 `run_id、session_id、request_id、round/generation、event_id、cutoff_at、deadline_at` 作为一组请求上下文贯穿链路。trace 的顺序由持久化观测层分配，调用方不再依赖固定 `sequence_no=0`。

## 3. 网关方案决策

### 3.1 当前快照继续使用原生 REST adapter

当前主流程是一次研究快照，需要明确的 cutoff、PIT 和证据引用。保留现有 Binance/OKX 原生 adapter；Provider 的 canonical 字段、时间语义和错误分类由 DSH 自己定义。

### 3.2 WebSocket 延后到连续行情采集目标

OKX 官方文档建议实时行情和订单簿使用 WebSocket，并定义快照、增量、序列和连接限制；这适合未来的连续采集器，不是当前一次性研究查询的必要依赖。本轮不引入连接管理、重连、checksum、订阅分片和新存储层，避免把网关可靠性目标扩大成行情平台目标。

### 3.3 CCXT 只作为可选统一公共数据 adapter

CCXT 的统一 API 是各交易所共同子集，公共数据不需要 key，并提供 rate limit；交易所特有字段和完整语义仍需交易所原生 API。本轮不把 CCXT 设为 canonical，也不做全量交易所接入；如果未来加入，只能通过已有 Provider capability contract，并保留原生 adapter 作为字段完整性和回退来源。

### 3.4 观测上下文先统一内部语义

OpenTelemetry 的 W3C Trace Context 说明了跨服务传播 trace/span 的通用方式。本轮先统一 DSH 内部的 request/lineage/deadline 字段和 trace 事件，不新增独立观测平台；后续接 OTel 时直接映射这些字段。

## 4. P0 开发任务

### P0.1 拒绝也必须可审计

现状：`DurableResearchCapabilityGateway.execute()` 在 `_validate_session()` 之前发生的未知 session、终态 Run、轮次或事件 lineage 错误可能没有 `tool_failed`/rejection trace。

改造：把已有 durable session 的拒绝写入统一的失败/拒绝事件，并按错误码区分重复 request 的不同拒绝；未知 session 返回明确错误但不伪造不存在的 run（当前没有独立 gateway rejection 表）。补齐 unknown session、terminal run、round mismatch、event mismatch 的测试，检查错误分类、引用 ID 和 sequence。

### P0.2 Evidence 与 Fact 原子提交

现状：Evidence 和 typed Fact 使用两个数据库 session 分别提交，Fact 失败会留下已提交 Evidence，重试又可能被永久失败 reservation 阻断。

改造：在一个数据库事务中完成 Evidence、Fact 和相关状态更新；故障注入要求事务回滚后不存在半提交，且同一 request 可以按幂等规则重试，或留下明确可恢复的 `pending_persistence` 状态。保留现有 Evidence/PIT/lineage 校验。

### P0.3 Provider fallback 继承同一剩余 deadline

现状：router 只按单 route timeout 顺序尝试，未使用整个 capability deadline，也没有明确 attempt 上限；连续超时可能超过上游允许的 deadline。

改造：为一次 capability 调用计算单一 `deadline_at` 和剩余预算；每次 route timeout 取剩余预算与 route 上限的较小值；超过预算立即停止；记录每次 attempt 的 provider、错误分类、开始/结束时间和剩余预算。补齐 retryable fallback、deadline exhaustion、non-retryable stop 测试。

### P0.4 收紧 route 配置和时间语义

改造：route allowlist 只接受规范 host/domain，不接受空值、URL、端口或隐式扩大范围；统一 domain matcher。统一 gateway/server/provider 的接收、完成和 cutoff 比较，断言 `published_at <= observed_at <= received_at <= cutoff_at`（按现有语义取反时在代码和测试中固定说明）。

## 5. 明确不做

- 不新增 WebSocket 采集平台、订单簿实时重建或全量交易所接入；
- 不引入 CCXT 全量替换现有 adapter；
- 不开发新的 DSH 页面、交易员角色、PPT、Agent、调度或交易执行；
- 不重新提示模型、不改历史评测输出、不用 UI 或网关指标宣称交易盈利价值；
- 不把一次真实回归包装成预测能力证明。

## 6. 验收门槛

全部满足才算 `GATEWAY_CORE_RELIABILITY_READY`：

1. 四类 lineage/session 拒绝均有正确、可查询的审计事件；
2. 注入 Fact 持久化失败时 Evidence 与 Fact 不出现半提交，重试行为可解释；
3. Provider fallback 总耗时不超过 capability deadline，attempt provenance 完整；
4. 非法 domain 配置被拒绝，PIT 和时间序列断言通过；
5. 相关单元/集成测试、静态检查和 `git diff --check` 通过；
6. 用现有真实 Provider 完成一次固定输入的核心链路回归，记录 run_id、trace、来源和失败原因，不泄露密钥。

任何一项不满足，状态为 `STOP / BLOCKED`，并记录具体失败证据；不能通过增加外围功能掩盖。

## 7. 参考资料

- [OKX V5 API 文档](https://app.okx.com/docs-v5/en/)：实时行情/订单簿和连接限制；
- [OKX 行情与订单簿说明](https://app.okx.com/docs-v5/trick_en/)：快照、增量和推送频率；
- [CCXT Manual](https://github.com/ccxt/ccxt/wiki/Manual)：统一 API 的共同子集、rate limit 和交易所特有方法；
- [Binance Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)：request weight、429/418 和退避要求；
- [OpenTelemetry Context Propagation](https://opentelemetry.io/docs/concepts/context-propagation/)：跨服务传播 trace/span 的上下文原则。

## 8. 执行记录

| 时间 | 动作 | 结果 |
|---|---|---|
| 2026-09-15 | 多 agent 代码审查、官方网关文档检索 | 确认 P0.1–P0.4；确认当前不引入 WebSocket/CCXT 全量 |
| 2026-09-15 | 开发与定向测试 | 已完成：网关/Provider/Fact Store `36 passed`，`tests/research` 全量 `199 passed`；Evidence/Fact 故障注入确认无半提交；codegen、Ruff、Pyright、diff check 通过 |
| 2026-09-15 | 真实核心链路回归 | 已完成：OKX public orderbook → `market.crypto_crowding` → Durable Gateway；Run `run_c845e7bea163481c9e338bd4f2bbab7b`；`tool_started/tool_completed`、reservation `completed`、1 Evidence、2 typed Facts、Provider attempt 1、约 378ms、成本 0 |
| 2026-09-15 | 最终门结论 | `GATEWAY_CORE_RELIABILITY_READY`：P0 网关链路已达到本轮工程验收；产品价值仍为 `STOP_PRODUCT_LAYER_INVESTMENT`，不进入交易或外围扩展 |
