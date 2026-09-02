# ADR-0016 能力调用即时入账与 DSH 模型步截止

日期：2026-09-01
状态：accepted
关联任务：[E2L-02](../stages/E2L_02_DSH_DURABLE_PROGRESS_AND_DEADLINE.md)

## 决策

1. Research MCP 的每次已准入 capability 调用必须在返回 DSH 前，把规范化 Tool Trace、
   失败 provenance 和 EvidenceCandidate 写入由 DSH Session link 解析出的唯一 Hub Run。
2. 完整 DSH SessionResult 到达后的再次投影必须幂等，不能改写即时入账的事实。
3. canonical DSH submit 携带模型步时限；官方 Host 通过公开 Session event/cancel seam 执行
   watchdog，不修改或 fork DSH Agent Loop。
4. capability timeout、model-step timeout、owner cancel、product deadline 保持不同来源和终态。
5. `crypto_macro.v1` 首次 live budget 定为 tool 20 秒、model step 150 秒、total 480 秒；
   后续只能依据 live 延迟和价值评测通过 ADR 调整。

## 理由

真实 DSH Web 会话证明：能力成功后，下一模型步可能超时。如果只在最终综合后入账，系统会
把已取得的官方事实显示为零，损害恢复、审计和个人资产沉淀。DSH 应继续拥有 Agent Loop；
Hub 只在公开工具边界记录经过验证的业务事实。

模型步预算此前只存在于请求和提示词，没有执行者。Host 已订阅官方 Session events，并拥有
公开 cancel seam，因此 Host watchdog 是最小且可随上游升级的落点。

## 否决项

- 只扩大 180 秒：否决，仍丢部分成功事实，也不执行单步预算。
- 在 LangGraph 重写工具循环：否决，会形成第二套 Harness。
- 在线解析 DSH 私有 JSONL：否决，JSONL 只作审计证据。
- 让模型在参数中自报可信 `run_id`：否决，身份来自耐久 Session link。
- 为此引入消息队列或新数据库：否决，当前 SQLite WAL 和幂等服务足够。

## 后果

- Research MCP 挂载同一 Hub 数据卷，但仍不拥有 Gate、Artifact、Forecast 或发布权。
- 部分 Evidence 会早于最终 SessionResult 可见，UI 必须标识“研究进行中”。
- watchdog 后出现“已有 Evidence + Session failed”是预期可恢复状态。
- Provider 若持续超过 150 秒模型步 SLO，E2-L 必须失败，不能无限扩大时限。
