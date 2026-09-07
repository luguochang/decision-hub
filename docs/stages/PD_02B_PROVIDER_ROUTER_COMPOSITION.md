# PD-02B Stable Provider Router Composition

版本：`PD-02B-2026-09-04.v1`  
状态：`completed / continue PD-02C`  
父阶段：[PD-02 Typed Provider Pack](PD_02_TYPED_PROVIDER_PACK.md)  
执行记录：[PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)

## 1. 本任务的唯一目标

把已经通过离线行为测试的 `ProviderCapabilityRouter` 接入真实 `crypto_macro` Pack
composition。对稳定能力 `market.crypto_derivatives`，由 Pack 的
`manifest.provider_routes` 声明 OKX public 为 primary、CoinEx public 为 fallback；运行时不再
通过环境变量二选一供应商。

本任务只解决“稳定能力如何由 Pack 路由至已审计 Provider”的工程边界。它不声称当前快照已经
能够满足事件窗口、OI delta、分钟级 macro 或 Fed expectation requirement，更不改变
`research_only`、Fixed active / DSH candidate 的产品状态。

## 2. 已验证的根因

当前代码已经有 `ProviderRoute`、`ProviderAttempt`、HTTP 错误分类、durable error lineage 和
`ProviderCapabilityRouter`，但产品 composition 仍存在两处旧实现：

1. `apps/research_mcp/main.py` 使用
   `DECISION_HUB_CRYPTO_DERIVATIVES_PROVIDER=coinex|okx`，一次只构造一个 adapter；
2. `packs/crypto_macro/tools/bindings.yaml` 的同一 stable capability 固定声明 CoinEx
   implementation 与 `api.coinex.com` allowlist。

`ResearchCapabilityGatewayService` 以 `capability_id` 唯一索引 adapter，因此两个 vendor adapter
不能直接并列注册。把二者直接塞入 Gateway 既会触发重复 ID，也会让环境变量、manifest 与
实际 URL 彼此脱节。

这不是 Agent Loop、DSH 或 LangGraph 的缺口：DSH 继续只选择稳定 capability；LangGraph
继续只消费成功/失败与 semantic gap；供应商选择必须在 Provider Adapter 层完成。

## 3. 固定设计

```text
DSH research_capability_execute
  -> ResearchCapabilityGatewayService
       (enable / audit / request domain / PIT / result schema / budget)
  -> ProviderCapabilityRouter("market.crypto_derivatives")
       (approved route order / bounded retryable fallback / ProviderAttempt[])
       -> OKXDerivativesResearchAdapter     provider_id=okx-public, primary
       -> CoinExMarketResearchAdapter       provider_id=coinex-public, fallback
  -> EvidenceCandidate + FactEnvelope
  -> existing durable Gateway / Evidence-Fact ledger / crypto_macro semantic Gate
```

### 3.1 代码所有权

| 位置 | 本任务职责 | 不得承担的职责 |
|---|---|---|
| `packs/crypto_macro/tools/bindings.yaml` | 声明 stable capability 的合并 allowlist、route、优先级、timeout、tier、license/audit | provider HTTP 逻辑、Prompt、Gate 逻辑 |
| `apps/research_mcp/main.py` | composition root；从 manifest 读取 route 并将已知 provider adapter 映射装配成一个 Router | env 分支选择供应商、复制 retry/PIT/Gate |
| `packages/provider_adapters/routing.py` | 按 approved route 执行、仅对 retryable failure fallback、写 `ProviderAttempt[]` | 持久化、账本、事实充分度判断、动态 import |
| `packages/provider_adapters/market/*` | 各供应商 HTTP payload 到 canonical Evidence/Fact 的薄映射 | 选择 fallback、篡改 window/delay 语义 |
| `ResearchCapabilityGatewayService` | 继续作为唯一 enable/domain/PIT/schema/budget Gate | 认识 OKX/CoinEx 私有字段或路由顺序 |

### 3.2 Pack 配置

`market.crypto_derivatives` 改为 provider-kind stable capability：

```yaml
implementation_ref: adapter://provider/market/crypto-multi-venue
allowed_domains: [okx.com, api.coinex.com]
provider_routes:
  - provider_id: okx-public
    adapter_ref: adapter://provider/market/okx-public
    route_role: primary
    priority: 10
    service_tier: free_proxy
    allowed_domains: [okx.com]
  - provider_id: coinex-public
    adapter_ref: adapter://provider/market/coinex-public
    route_role: fallback
    priority: 20
    service_tier: free_proxy
    allowed_domains: [api.coinex.com]
```

`free_proxy` 表示公共端点而非正式分钟级授权行情。任何 Provider 成功都不能让
`FactEnvelope` 凭当前 snapshot 冒充 `t-5m/t+1m`、`event_return` 或 `open_interest_delta`。

### 3.3 Route 选择与安全约束

- 没有 `query.allowed_domains` 时，按 Pack 的 approved primary/fallback 顺序执行。
- 请求显式限制 domain 时，只选择 route allowlist 覆盖该限制的 route；例如
  `api.coinex.com` 只会选 CoinEx。无兼容 route 时以 non-retryable
  `provider_route_domain_denied` fail-closed，不能扩张用户请求的域权限。
- Router 只接受 `capability_id` 等于 stable capability 的 adapter。缺失 mapping、错误 adapter
  capability、未 approved route、mode 不支持均为 composition/configuration failure，不做 fallback。
- Adapter 可以声明可选 `supported_fields`。当 query 请求某 Provider 不支持的字段时，该 route
  在执行前跳过，继续检查同一 stable capability 的其他 approved route；没有可用 route 时以
  `provider_route_unavailable` fail-closed。这样不会把一个供应商的较窄字段面伪装成完整能力。
- 只有 timeout、429、5xx、transport unavailable 等既有 `retryable=true` failure 才尝试下一个
  route；字段、PIT、license、contract、语义错误立即终止。
- Router 不信任 Provider 输出的域名：Gateway 仍用 stable manifest 的合并 allowlist 校验每条
  Evidence URL。Route 层的 domain 只用于选择 Provider，不能绕过 Gateway。
- `ProviderAttempt[]` 继续通过既有 `ResearchCapabilityResult` 或 durable `ErrorProvenance`
  投影保存；不增加第二张 attempt 表，不把字段拼进错误消息。

## 4. 明确非目标

- 不新增 `market.crypto_spot`、crowding、macro intraday、Fed expectation provider；这些分别属于
  PD-02C/02D/02E。
- 不实现 event-window return、OI delta 或用 mock 数据关闭 hard gap。
- 不新增 Agent framework、DSH plugin、database、队列、Provider DTO 或通用插件系统。
- 不调用 live API、不读取聊天中出现过的密钥；普通 pytest 一律使用 fake adapter/fixture。
- 不改变前端、主动报告、调度、Outcome 或自进化工作。

## 5. BDD / TDD 验收

先写 Red，再写最小 Green。所有 case 经真实 Gateway composition，而非只调用 Router unit。

```gherkin
Scenario: Pack primary 成功且不依赖环境变量
  Given crypto_macro Pack 以 stable market.crypto_derivatives 声明 OKX primary 和 CoinEx fallback
  And composition 注入两个 provider-specific adapter
  When Gateway 执行没有 domain 限制的 live query
  Then 只调用 OKX
  And result provider 为 okx-public
  And result 含一条 primary succeeded ProviderAttempt
  And 不读取或不需要 DECISION_HUB_CRYPTO_DERIVATIVES_PROVIDER

Scenario: retryable primary failure 使用 Pack fallback
  Given OKX 返回 retryable provider_rate_limited
  And CoinEx 返回 contract-valid canonical result
  When Gateway 执行 stable capability
  Then CoinEx 仅调用一次
  And result 保存 failed OKX 与 succeeded CoinEx 两条 attempts

Scenario: request domain 不能被 route 扩权
  Given query.allowed_domains 仅为 api.coinex.com
  When Gateway 执行 stable capability
  Then 不调用 OKX，直接选择 CoinEx route
  And query 限制与所有 approved route 都不兼容时 fail closed

Scenario: non-retryable provider failure 不被 fallback 掩盖
  Given OKX 返回 research_market_output_invalid
  When Gateway 执行 stable capability
  Then CoinEx 不调用
  And durable error recovery 保留 OKX failed ProviderAttempt
```

测试层级：

1. Pack contract：stable implementation、合并 allowlist、route 版本、优先级和批准状态；
2. Router：route/domain/filter/fallback/misconfiguration；
3. Gateway composition：manifest -> Router -> provider fake -> Gateway result，以及 error durable
   recovery；
4. 全量 regression：pytest、pyright、ruff、canonical codegen、module-docs、`git diff --check`。

## 6. 实施清单与退出门

- [x] `B1`：补 Pack route 声明并调整契约测试，不改变 semantic Gate；
- [x] `B2`：在 composition root 构造稳定 Router，删除 derivatives provider 环境变量选择；
- [x] `B3`：补 Router 的 request-domain、field capability 和 adapter-composition fail-closed 保护；
- [x] `B4`：新增 Gateway composition 测试，并复用 PD-02A 的 durable failure/recovery 测试；
- [x] `B5`：更新 module README、stage/执行日志/状态索引，运行全部离线质量门；

工程退出条件：Pack 内只出现 stable capability，调用方不出现 CoinEx/OKX 分支；Provider 切换只改
Pack route 与 provider adapter registration；成功与失败皆保留 `ProviderAttempt[]`；Core、Graph、UI
不因本任务改动。

产品停止条件：即使 B1--B5 完成，PD-02C/02E 的 event-window 与宏观正式数据仍缺失。因此最终状态
只能是 `continue PD-02C / research_only`，不能写为事实充分、方向有效、交易可用或正式交付。

## 7. 完成证据

- `market.crypto_derivatives` 现在由 Pack 声明 OKX primary、CoinEx fallback；composition root
  对 DSH/Gateway 只注册一个 stable capability Router，旧环境变量二选一已删除。
- Router 在执行前按请求域和 adapter 声明的字段能力收窄 route；无兼容 route、未知 adapter
  ref、capability mismatch 和重复 provider ID 全部 fail-closed。
- primary 成功不调用 fallback；仅 retryable 429/timeout/5xx/transport failure 才尝试下一 route；
  成功和失败均保留 canonical `ProviderAttempt[]`。durable error recovery 继续由既有 reservation
  `error_json` 承担，没有新增第二账本。

2026-09-04 最终离线质量门：

```text
.venv/bin/pytest -q                                      486 passed
.venv/bin/pyright packages apps tests                    0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools           passed
.venv/bin/python -m tools.contract_codegen check         canonical schemas: ok
```

以上只证明 provider 路由组合和失败边界。OKX/CoinEx 当前仍主要提供 current snapshot，尚未产生
可关闭 `t-5m/t+1m`、`event_return`、OI delta 的事件窗口事实；未执行 live canary，也未证明
分钟级宏观事实、预测准确率或盈利。
