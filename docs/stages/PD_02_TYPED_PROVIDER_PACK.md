# PD-02 Typed Provider Pack 实施与验收方案

版本：`PD-02-2026-09-04.v1`  
状态：`completed / provider-neutral engineering; licensed live provider still blocked`  
父阶段：[PD 产品事实充分度与主动交付](PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)  
执行证据：[PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)

## 1. 要解决的问题

PD-00 已阻止错误指标、字段、单位或窗口关闭金融 requirement，PD-01 已建立可恢复的事件窗口。
当前缺口是 Provider 仍以单一当前快照接入：CoinEx/OKX 只能二选一，FRED 只有 delayed 日频值，
没有稳定的 capability router、事件窗口 query、供应商尝试记录和服务等级。因此 DSH 即使继续搜索，
也无法得到足以关闭 hard gap 的 typed facts。

本阶段把 Provider 做成可替换的数据能力包，而不是把供应商写进 Agent Prompt、Core 或页面。

## 2. 固定边界

```text
DSH Agent Loop
  -> 单一 research_capability_execute 工具
  -> Hub Capability Gateway（权限、PIT、预算、schema）
  -> Stable Capability Router
       -> primary Provider Adapter
       -> retryable failure 时 fallback Provider Adapter
  -> EvidenceCandidate + FactEnvelope + ProviderAttempt[]
  -> Hub Evidence/Fact/Attempt Ledger
  -> crypto_macro Semantic Gate
```

- DSH 仍是唯一 Agent Harness；本阶段不写 Agent Loop。
- LangGraph 不选择供应商，只消费 capability 成功/失败和 semantic gap。
- Hub 只认识 canonical capability/query/result/fact/attempt，不认识 CoinEx/OKX 私有字段。
- `crypto_macro` Pack 声明 capability、route、字段/单位/窗口和服务等级。
- adapter 负责 HTTP、供应商 schema、单位转换和 typed fact 映射。
- Search locator 不能替代行情；FRED delayed 值不能冒充 realtime。
- 普通测试只读 fixture；live canary 必须显式启用，结果单独记录。

## 3. 稳定能力与供应商路由

| Stable capability | 免费/默认 route | fallback/正式 route | 输出 |
|---|---|---|---|
| `market.crypto_spot` | OKX public | CoinEx public | price、volume、event_return、窗口 |
| `market.crypto_derivatives` | OKX public | CoinEx public | funding、OI、OI delta、mark/index、basis |
| `market.crypto_crowding` | OKX public order book/flow proxy | 后续 licensed aggregator | crowding_signal |
| `macro.cross_asset_intraday` | public/free proxy，明确 `free_proxy` | 后续 licensed live | rates、USD、equity/volatility/commodity 窗口 |
| `macro.expectation_pricing` | 无正式默认值；可用 proxy 只能 `research_only` | 后续 licensed Fed funds/SOFR/OIS | level、delta、窗口 |
| `market.cross_asset` | FRED delayed compatibility alias | 无 | 旧 payload 可读，不关闭 realtime gap |

稳定 capability ID 不含供应商名。供应商变更只改 `ProviderRoute` 和 adapter registration，不改
Research Plan、DSH tool、Graph、Core 或前端。

## 4. Canonical 契约

`agentic_research.schema.yaml` 以 additive 方式增加：

- `ResearchCapabilityQuery.event_id/event_at/window_start_at/window_end_at/requested_event_offsets`；
- `ProviderRoute`：`provider_id/adapter_ref/route_role/priority/service_tier/domains/timeout/license/audit`；
- `ProviderAttempt`：每次 route 的起止时间、延迟、状态、错误、retryable 和成本；
- `ResearchCapabilityManifest.provider_routes`；
- `ResearchCapabilityResult.provider_attempts`。

旧 query/result/manifest 均以默认空值继续可读。镜像只通过 codegen 生成，禁止 Python/TypeScript
双写。

## 5. 失败、回退与成本语义

- 只对 `timeout/429/5xx/transport_unavailable` 等 `retryable=true` 错误尝试下一个 route。
- contract、PIT、字段、单位、域名、license、audit 错误不得 fallback；它们代表配置或数据错误。
- primary 成功后不得调用 fallback。
- 所有 route 都失败时，抛出最后一个标准错误，但保留全部 ProviderAttempt。
- `cost_usd=0` 只表示已知免费调用；未知费用必须为 `null`，页面不得显示假零。
- `free_proxy` 可以提供研究参考，但不能通过改 `delay_class` 或 Gate 冒充 `licensed_live`。

## 6. 代码落点

```text
contracts/schemas/agentic_research.schema.yaml
packages/contracts_py/decision_hub_contracts/generated/
packages/contracts_ts/src/generated/r2.ts

packages/provider_adapters/
  routing.py                         # 通用有界 primary/fallback router
  http_errors.py                     # timeout/429/5xx/transport 分类
  market/crypto_multi_venue.py       # 稳定 spot/derivatives router
  market/crypto_crowding.py          # 独立 crowding capability
  macro_market/intraday.py           # stable macro intraday port/mapper
  macro_market/expectation_pricing.py

apps/research_mcp/main.py             # 按 Pack 注册稳定 capability router
packs/crypto_macro/pack.yaml
packs/crypto_macro/tools/bindings.yaml
packs/crypto_macro/evidence/source_manifest.yaml
packs/crypto_macro/profiles/*.yaml
```

Provider attempt 的 durable 投影复用现有 research observability/ledger；若现有表无法无损保存多
route 尝试，只允许新增 append-only migration，不把 attempts 塞进错误字符串或覆盖 tool call。

## 7. BDD/TDD

```gherkin
Scenario: primary 成功
  Given capability 有 primary 和 fallback
  When primary 返回契约有效的 typed facts
  Then fallback 不被调用
  And result 只包含一条 succeeded ProviderAttempt

Scenario: primary 429 后 fallback 成功
  Given primary 返回 retryable 429
  When fallback 返回有效事实
  Then result provider 是 fallback
  And 两次 attempt 的错误、延迟和成本都保留

Scenario: contract 错误不回退
  Given primary 返回字段或 PIT 错误
  When router 执行
  Then fallback 不被调用
  And Run 保持 fail-closed

Scenario: 当前快照不能伪造事件收益
  Given query 要求 t-5m 和 t+1m
  And Provider 只有当前 snapshot
  When adapter 映射 FactEnvelope
  Then 不生成 event_return 或指定 offset
  And semantic Gate 保持 window_missing

Scenario: 免费宏观 proxy 不冒充正式实时数据
  Given route service_tier 是 free_proxy
  And事实只有 delayed 数据
  When 评估 macro_transmission
  Then hard gap 保持 insufficient
  And result/页面显示 free_proxy/delayed
```

测试层级：schema/codegen、adapter fixture、router、Gateway、FactStore、semantic Gate、真实
composition、migration/restart、独立 live canary。普通 `pytest` 不触网。

## 8. 任务卡与退出门

- [x] `PD-02A`：canonical window/route/attempt contract 与兼容测试；失败的
  `ProviderAttempt[]` 也投影到 canonical `ErrorProvenance`，由现有 durable tool reservation
  的 `error_json` 无损保存和恢复，不新建重复账本。证据见
  [PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
- [x] `PD-02B`：完成通用 router 的 composition 接入、HTTP 错误分类与 attempt durable audit
  的真实 Gateway 覆盖；产品 Pack 现在通过 stable capability 选择 OKX primary/CoinEx fallback，
  旧环境变量二选一已删除。
  具体代码所有权、route/domain 安全语义、BDD/TDD、非目标和退出门固定在
  [PD-02B Stable Provider Router Composition](PD_02B_PROVIDER_ROUTER_COMPOSITION.md)。
- [x] `PD-02C`：OKX/CoinEx 多 venue spot/derivatives 和 event-window/delta（详见
  [PD-02C 事件窗口事实](PD_02C_CRYPTO_EVENT_WINDOW_FACTS.md)）；
- [x] `PD-02D`：crowding public proxy；订单簿 imbalance、Pack route 和衍生品完整窗口语义回放见
  [PD-02D Crypto Crowding Facts](PD_02D_CRYPTO_CROWDING_FACTS.md)；
- [x] `PD-02E`：intraday macro 与 expectation pricing 的 free/licensed seam；provider-neutral
  event-offset mapping、unknown cost 和完整 Gate 回放见 [PD-02E 阶段卡](PD_02E_INTRADAY_MACRO_EXPECTATION_PRICING.md)。
- [x] `PD-02F`：把 02C--02E 新增能力纳入 Pack/profile/composition，完成全 capability allowlist；
- [x] `PD-02G`：离线全链 replay、全量质量门和显式 public adapter canary 入口。该完成状态不代表
  正式分钟级 macro/expectation licensed Provider 已授权或通过生产 canary。

工程退出门：Provider 替换不修改 Core/Graph/UI；primary/fallback 有行为测试；所有 attempts、成本、
服务等级和错误可审计；每个 hard requirement 有可执行 primary route 或明确 `provider_unconfigured`
而不是被 Web 摘要伪装关闭。

产品退出门与工程退出门分开：需要正式分钟级 licensed 数据的 requirement，在真实 provider 未选定、
未授权、未通过 canary 前继续 `research_only`。不得为了宣布 PD-02 完成而降低语义 Gate。

## 9. 停止线

- 不安装新的 Agent 框架、队列、数据库或第二套插件系统。
- 不在此阶段实现前端大改、主动 Inbox、Outcome 或自动进化；它们分别属于 PD-04..06。
- 不在 fixture 中写 `sufficient` 自证；必须执行 adapter -> router -> Gateway -> FactStore -> Gate。
- 不把用户密钥写入仓库、日志、截图或测试。
- 不把离线 fixture 通过写成真实 Provider 稳定或交易有效。
