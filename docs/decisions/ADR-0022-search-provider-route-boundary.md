# ADR-0022：DSH 原生 Search 与 Hub Search Provider 的路由边界

日期：2026-09-04
状态：accepted

## 决策

Decision Hub 采用单路、有优先级的 discovery provider ladder：

1. DSH 官方原生 `web_search` 是默认 discovery primary，继续由 DSH Harness 的 Agent Loop 调用。
2. Hub 的 `web.search.tavily` 只作为显式启用的 fallback/独立交叉索引；它不能与 DSH native
   Search 在同一 Run 无条件并发。
3. Hub `web.search` 不进入默认产品 allowlist。它若在实验或兼容场景启用，必须使用独立的
   capability id、预算和 canary，不能把同一 native route 重复暴露给模型。
4. 任一 Search provider 只产生 `search_derived` locator candidate。只有 `web.fetch` 或
   Official/Market typed provider 通过同一 Evidence Gateway 的 authority、PIT、freshness、hash
   和独立来源门后，才可进入 Evidence Ledger。

## 背景

DSH 上游确实提供 `web_search`，但它走独立 Anthropic-compatible Search route；Hub 也可以
提供 Search adapter。两者同时暴露时，真实 Run `run_e6812b573ca4448691109541cda2c737` 出现
重复 native Search 调用、超时和额外预算消耗，却没有增加可接受 Evidence。反过来，只暴露 Hub
typed capability 又会失去 DSH 的原生 discovery 能力。因此需要明确“谁发现、谁验证、谁入账”的
职责，而不是再创建第三套搜索或 Agent Loop。

## 候选与否决项

- 采用 DSH native primary + Tavily explicit fallback：复用官方 DSH loop 和现有
  `SearchCapabilityPort`，provider 可以替换，Core/Gate/账本不变。
- 否决“每轮同时调用 DSH native 与 Hub search”：重复费用、重复 locator 和不可解释的 timeout。
- 否决“Search 摘要直接入 Evidence”：摘要没有原文、PIT 或 authority 证明，不能满足金融 hard
  requirement。
- 否决“失败时静默改用另一 provider”：必须记录 provider、attempt、错误分类、成本和选择理由。

## 后果

- DSH 保留其官方搜索和 Agent Loop 能力，Hub 不复制 Harness；Hub 仍是 Evidence 唯一准入边界。
- 默认产品不读取 Tavily secret，不产生 Tavily 费用；启用前必须轮换已暴露 key，并通过独立只读
  canary 和预算门。
- Search provider 的延迟、费用和可归因率必须按 provider 分开观测；若 native 不稳定，可在不改
  Core、Gate、前端和账本的情况下调整 fallback 顺序，但需要新的 canary/ADR 证据。
- 当前真实运行仍可能以 `research_only` bounded failure 结束；这比把不充分资料包装成方向性
  结论更符合产品安全边界。

## 迁移/回滚

- 通过 `DECISION_HUB_RESEARCH_CAPABILITIES` 显式控制 fallback；回滚只需移除
  `web.search.tavily` 或恢复上一版 preset，不改历史 Run/Evidence。
- 新 provider 必须实现既有 `SearchCapabilityPort` 和 canonical result schema，通过 contract、
  replay、live canary 后才可加入 allowlist。

## 受影响契约与测试

- `research-capability-query.v1`、`research-capability-result.v1`、`EvidenceCandidate` 和
  `ErrorProvenance` 不变；新增 provider 只能适配，不得扩散 provider-specific 字段到 Core。
- 回归覆盖见 `tests/capabilities/test_search_capability.py`、
  `tests/orchestration/test_g2af_continuation.py`、`tests/research/test_document_adapter.py`。
- 真实证据与未完成门见 [G2-AF 实施执行记录](../evaluations/G2_AF_IMPLEMENTATION_EXECUTION_LOG_2026-09-04.md)。
