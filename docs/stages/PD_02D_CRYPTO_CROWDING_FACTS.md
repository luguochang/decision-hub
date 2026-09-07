# PD-02D Crypto Crowding Facts

版本：`PD-02D-2026-09-04.v1`  
状态：`completed / continue PD-02E`  
父阶段：[PD-02 Typed Provider Pack](PD_02_TYPED_PROVIDER_PACK.md)  
执行证据：[PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)

## 1. 唯一目标

为 `derivatives_crowding` 增加一个可审计、可替换的订单簿拥挤度代理能力，并证明它能够沿
既有事件窗口链进入语义 Gate。这里的 `crowding_signal` 是同一订单簿的
`(bid_depth - ask_depth) / (bid_depth + ask_depth)`，只能解释为
`orderbook_imbalance` proxy；它不代表清算量、杠杆、多空比、主动成交量或完整的衍生品拥挤度。

本卡不承诺交易所长期可用性，也不把 proxy 当作独立于 venue 的第二来源。正式产品仍须在
真实 Provider canary 后保持 `research_only`，直到 funding、OI、OI delta、basis 和 crowding
均在同一事件窗口满足 Pack 的语义要求。

## 2. 固定边界

```text
DSH research_capability_execute
  -> Hub Gateway（权限、PIT、预算、Run/Session lineage）
  -> market.crypto_crowding Router
       -> OKX public order book (primary)
       -> CoinEx public depth (fallback)
  -> EvidenceCandidate + FactEnvelope(attributes.proxy_kind=orderbook_imbalance)
  -> EventWindow/FactStore/Semantic Gate
```

- DSH 仍是唯一 Agent Harness；本卡不新增 loop、Supervisor 或 MCP 工具。
- LangGraph 只负责 Run 生命周期和有界 continuation，不认识订单簿字段。
- Kernel 只接收 canonical `FactEnvelope`，不依赖 OKX/CoinEx SDK 或私有 payload。
- `CryptoCrowdingResearchAdapter` 只解析 HTTP payload 和计算 scalar proxy；它不写账本、不改 Gate。
- `CryptoEventWindowSampler` 复用该 adapter 的 typed 结果，保存 content-addressed capture；窗口
  读取仍必须经 archive route、Gateway 和 FactStore。

## 3. 代码结构

| 位置 | 职责 |
|---|---|
| `packages/provider_adapters/market/crowding.py` | OKX/CoinEx depth 形状归一化、正数深度求和、`book_imbalance` 计算、错误分类和 typed facts |
| `packages/provider_adapters/market/event_window.py` | 复用 crowding adapter 的 capture；为 `t-5m/t+1m` 事实建立窗口 lineage；不补造缺失 baseline |
| `packages/provider_adapters/routing.py` | 按 Pack route 执行 primary/fallback；只有 retryable transport/429/5xx 才回退 |
| `apps/research_mcp/main.py` | 将稳定 capability 注册为 Pack-driven Router；不读取供应商选择环境变量 |
| `packs/crypto_macro/tools/bindings.yaml` | 声明 capability、route、service tier、域名、预算和审批状态 |
| `packs/crypto_macro/evidence/source_manifest.yaml` | 只声明 `market.crypto_crowding` 为 derivatives fallback；不复制 Gate |
| `packages/kernel/.../sufficiency.py` | 校验 required fields、metric family、offset、venue、independence 和 freshness |

## 4. 语义与失败规则

- `crowding_signal` 和 `book_imbalance` 的单位是 `ratio`，`attributes.proxy_kind` 必须为
  `orderbook_imbalance`。
- 订单簿 proxy 的 `metric_family` 必须是 `crypto.derivatives`，venue 和 source/provider 必须
  保留，不能写成通用 `market.cross_asset`。
- 空 bid/ask、零总深度、非法数值、错误 payload 是 non-retryable provider failure；网络超时、
  429、5xx 才允许 Router 使用 fallback。
- 只有当前快照时，adapter 不填写 `event_offset`、`window_start_at/window_end_at`，也不生成
  `event_return` 或 `open_interest_delta`。
- 要关闭 `derivatives_crowding` hard requirement，必须在事件窗口中同时取得
  `funding_rate`、`open_interest`、`open_interest_delta`、`basis`、`crowding_signal`，并满足
  `t-5m/t+1m`、`realtime`、同一 venue 和 Pack 声明的来源独立性；crowding proxy 单独存在不能
  抬高 coverage。
- `market.crypto_crowding` 作为 derivatives fallback 时，只有它实际生成的 typed fact 才能
  计入语义 Gate；Search locator、网页摘要和 BTC 现货不能替代它。

## 5. BDD/TDD 验收

```gherkin
Scenario: OKX depth 生成可解释 proxy
  Given OKX 返回非空 bid/ask depth
  When 执行 market.crypto_crowding
  Then result 包含 crowding_signal 和 book_imbalance
  And FactEnvelope.metric_family 是 crypto.derivatives
  And attributes.proxy_kind 是 orderbook_imbalance

Scenario: primary 429 后使用 CoinEx fallback
  Given OKX route 返回 retryable provider_rate_limited
  And CoinEx route 返回契约有效的 typed facts
  When Router 执行 stable capability
  Then OKX failed 与 CoinEx succeeded ProviderAttempt 都被保留
  And fallback 只执行一次

Scenario: 订单簿 proxy 不能伪造事件窗口
  Given crowding capability 只有 current snapshot
  When query 要求 t-5m 和 t+1m
  Then 不生成 event_offset 或事件 delta
  And derivatives_crowding 保持 window_missing 或 semantic_mismatch

Scenario: 完整窗口才可关闭衍生品 hard gap
  Given 同一 venue 的 funding/OI/basis/crowding 在 t-5m 与 t+1m 均已捕获
  And open_interest_delta 由同 venue baseline/post 计算
  When Gateway -> FactStore -> Semantic Gate 回放
  Then coverage.status 是 sufficient
  And 去掉 crowding_signal 后 coverage 仍是 insufficient
```

测试层级：adapter fixture、错误分类、Router fallback、EventWindow archive、Gateway/Facts、语义
替代回归、全量 Python/TS/静态/契约/文档门。普通测试不触网；真实 OKX/CoinEx 只在显式 canary
中运行，且不把 canary 结果写入 fixture。

## 6. 完成清单

- [x] `D1` 新增 OKX/CoinEx order-book typed adapter，保留 proxy 语义和单位。
- [x] `D2` 将 capability、route、service tier 和允许工具加入 Pack、MCP composition 与三个角色。
- [x] `D3` 复用 EventWindow sampler/archive，允许 crowding observation 跨事件窗口保存。
- [x] `D4` 增加 adapter fixture 与空深度错误测试；保持错误不被 fallback 掩盖。
- [x] `D5` 增加事件窗口全链回放：archive -> Router -> Gateway -> Evidence/FactStore -> Gate，
  验证缺字段仍 fail-closed。
- [x] `D6` 同步模块 README、状态索引、执行日志和本阶段文档。

## 7. 退出门与保留项

工程退出门：稳定 capability 不含供应商名；Provider 替换只改 Pack route/adapter registration；
proxy 字段、单位、venue、attempt、成本和失败 provenance 可追溯；事件窗口缺失、篡改和语义替代
不能通过 Gate。

产品退出门：本卡只补一类衍生品 proxy。没有真实事件窗口、分钟级宏观和 expectation pricing
时，系统继续输出 `research_only`，不得宣称事实充分、预测准确、盈利或自动交易可用。下一步
是 `PD-02E` 的 intraday macro/expectation typed capability；真实 provider license/canary 仍单独
记录，不由离线回放代替。

## 8. 证据

本卡对应实现：

- `packages/provider_adapters/market/crowding.py`
- `packages/provider_adapters/market/event_window.py`
- `apps/research_mcp/main.py`
- `packs/crypto_macro/tools/bindings.yaml`
- `packs/crypto_macro/evidence/source_manifest.yaml`
- `tests/research/test_crypto_crowding.py`
- `tests/research/test_event_window_full_chain.py`

本卡的最终质量门和未完成项统一追加到
[PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
