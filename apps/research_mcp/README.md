# Research MCP

本服务是 DSH Research Harness 到 Decision Hub `ResearchCapabilityGateway` 的唯一
模型工具入口。它只暴露一个 canonical `research_capability_execute` 工具，不包含
Memo/Feedback、Gate、Artifact、Forecast、ActivePointer 或任何 owner 写命令。

默认使用 streamable HTTP 并监听 `127.0.0.1:8002`：

```bash
./.venv/bin/python -m apps.research_mcp.main
```

DSH restricted profile 通过官方 `@deepseek-ai/dsh-mcp-client` 连接
`http://127.0.0.1:8002/mcp`，模型看到
`mcp__decision_research__research_capability_execute`。MCP 只负责协议和工具发现；
license/audit、显式 enable、域名、timeout、费用、PIT、hash 和 Evidence 入账仍由
Kernel Gateway 裁决。

网络能力默认不启用。`DECISION_HUB_RESEARCH_CAPABILITIES` 只能选择 Pack 中已
`license_status=approved` 且 `audit_status=approved` 的能力；candidate 即使写入环境
也会 fail-closed。Replay 还必须通过 `DECISION_HUB_RESEARCH_REPLAY_FIXTURES` 显式
指定版本化归档文件；缺失查询直接失败，绝不回退 live network。DSH profile 设置
`failOnStartupError=true`，Research MCP 未就绪时 Harness readiness 必须失败。普通 CI
使用注入的 fake/replay Gateway，不触网。

`market.crypto_derivatives` 不再通过环境变量二选一交易所。composition root 读取
`packs/crypto_macro/tools/bindings.yaml` 的 `provider_routes`，在 Gateway 后装配一个 stable
`ProviderCapabilityRouter`：当前 OKX public 为 primary、CoinEx public 为 fallback。请求域和字段
能力会在执行前收窄 route；只有 retryable transport/429/5xx 才 fallback。新增或替换供应商只允许
修改 Pack route 与本模块的 adapter-ref registry，不得把供应商分支写入 DSH、Graph、Core 或页面。

事件窗口请求必须携带由 DSH/Run 关联的 `event_id` 和 `requested_event_offsets`。Durable Gateway
从 EventWatch 投影 `event_at/window_start_at/window_end_at`，Router 只把这类请求交给本地
`event-window-archive` route；archive 缺失、篡改或非可重试失败不会回退到 current snapshot。

`market.crypto_crowding` 由 Pack 注册 OKX order book primary 与 CoinEx depth fallback，输出的
`crowding_signal` 明确是 `orderbook_imbalance` proxy。它可以被 EventWindow sampler 捕获，但只有
与 funding/OI/OI delta/basis 及完整 event offsets 一起通过 Domain Semantic Gate 时才关闭
`derivatives_crowding`；单个 current proxy 不构成充分证据。`macro.cross_asset_intraday` 和
`macro.expectation_pricing` 已有 provider-neutral adapter seam，前者默认 delayed、后者默认
candidate/review-required；没有配置和批准真实 endpoint 时都必须 fail-closed。

`web.search` 只在 `DECISION_HUB_RESEARCH_CAPABILITIES` 显式包含该 capability 时
接入 DeepSeek Anthropic-compatible native `web_search` route（`DEEPSEEK_API_KEY`）；
配置读取 `DECISION_HUB_DSH_SEARCH_{ENDPOINT,MODEL,ESTIMATED_COST_USD}`。其结构化
`web_search_tool_result` 只生成 `search_derived` candidate。`web.search.tavily` 是独立的
按需 fallback，只有显式加入 allowlist 且设置 `TAVILY_API_KEY` 时才读取 secret；两者都不能
替代 Official/Market 能力对精确事实的复核。密钥只进入 adapter 进程，不进入 MCP 参数、Trace、
Evidence 或账本。
TLS 校验默认开启；只有本机代理诊断 canary 才可显式设置
`DECISION_HUB_DSH_SEARCH_VERIFY_TLS=0`，该设置不得进入生产或长期运行配置。

来源注册由 `ResearchSourceRegistry` 在 composition root 装配，策略唯一来源是
`packs/crypto_macro/evidence/source_registry.yaml`。MCP 不提供第二套插件协议：它只暴露
`research_capability_execute`，Search locator 仍是 `search_derived`，approved Fetch/typed
provider 的 Evidence/Fact 仍走同一个 Gateway。未知域名、错误 requirement、未审计来源和越权
redirect 在进入 adapter 或账本前 fail-closed。MCP 不拥有 DSH Session、账本、Gate 或发布权。

`web.fetch` 与 `official.macro` 复用同一 Registry；前者必须同时满足 capability 网络域名上界和
Registry 的 fetch/evidence 业务准入，并以 Registry authority 入账。Registry requirement alias
来自 `source_manifest.yaml` 的 `requirement_id/canonical_requirement_id`，不在 composition 或
adapter 中双写。`official.macro` 当前只生成 event identity，政策 delta 不能由当前正文伪造。

显式 live canary（会产生中转费用，输出只含脱敏计数）：

```bash
DECISION_HUB_SEARCH_LIVE_CANARY=1 \
./.venv/bin/python -m tools.canary.run_responses_web_search_canary
```
