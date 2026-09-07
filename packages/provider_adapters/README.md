# Provider Adapters

## 目的

把第三方 HTTP、交易所和通知 SDK 适配为 Kernel 的 `MarketDataPort`、`MarketWindowPort` 和 `NotificationPort`。adapter 只返回 canonical Pydantic DTO，不拥有 Gate、账本或发布权限。

`routing.ProviderCapabilityRouter` 在稳定 capability ID 后执行 Pack 声明的 approved provider
routes。它只对 retryable transport/429/5xx 错误执行有界 fallback，并把每次 route 的服务等级、
耗时、成本和错误写入 canonical `ProviderAttempt`；contract/PIT/字段错误不得通过换供应商掩盖。

## 当前实现

- `market/okx.py`：OKX 公共 ticker 和 1m candle 适配；bid/ask 优先，只有 last 时显式 `estimated`。
- `research/`：Web Search/Fetch 与 deterministic replay 到 canonical `EvidenceCandidate` 的薄适配。
- `official_sources/`：受域名白名单保护的官方文档能力；不复制 Gateway 权限逻辑。
- `market/okx_research.py`：OKX ticker/funding/OI/mark 的研究证据适配。
- `market/coinex_research.py`：CoinEx spot/funding/OI/mark/index/basis 当前快照适配。
- `market/event_window.py`：复用 typed market adapter 捕获 EventWatch 窗口，使用内容寻址 archive，
  并将已捕获的 `t-5m`/`t+1m` 投影为 event-relative Evidence/Fact；缺失 baseline 不回填 current snapshot。
- `market/crowding.py`：OKX/CoinEx order-book depth 到 `crowding_signal/book_imbalance` typed
  proxy；`attributes.proxy_kind=orderbook_imbalance`，不代表清算、杠杆或完整衍生品拥挤度。
- `macro_market/fred.py`：FRED 公开序列的 PIT 观测适配，保留数据日期用于鲜度判断。
- `macro_market/intraday.py`：provider-neutral intraday rates/USD seam；未配置 endpoint 时
  `provider_unconfigured`，delayed proxy 不通过 realtime semantic Gate。
- `macro_market/expectation_pricing.py`：Fed funds/SOFR/OIS 等政策预期定价的 licensed adapter seam；
  没有 approved provider 时保持 candidate/unconfigured，不用 crypto 或网页摘要替代。
- `routing.py`：stable capability 的 primary/fallback 路由；供应商名不得进入 Core/Graph/UI。
- `http_errors.py`：把 timeout、429、5xx、4xx 和 transport 错误映射为稳定 retry 语义。
- `notifications/local.py`：本地 JSONL 通知 adapter，便于单机验证；Email/IM 继续作为可替换 adapter。
- `search/fake.py`：普通测试使用的确定性 canonical Search transport。
- `search/openai_compatible.py`：接收已配置客户端调用的强类型 seam；不持有 key、不猜测某个中转站的私有响应格式，所有输出仍由 Kernel Search Gate 校验。
- `search/openai_responses.py`：使用官方 `AsyncOpenAI` Responses `web_search`；`web_search_call.action.sources` 只作为发现集合，只有 message `output_text.annotations[].url_citation` 的 exact text span 与该集合中的 canonical URL 明确绑定后才生成 `SearchEvidence`。同一 claim/URL alias 保守去重，缺少可归因 citation 时 fail-closed；默认不启用。中转不支持官方 `max_tool_calls` 时，内部调用成本按 `$0.01/call` 保守估算并在返回后执行预算 Gate，不能宣称为 Provider 硬预授权。
- `search/dsh_native.py`：DeepSeek Anthropic-compatible 原生 `web_search` route 的 typed transport；结构化 `web_search_tool_result` 只生成 discovery candidate，必须经 Gateway/Fetch 才能入账。
- `search/tavily.py`：Tavily Search fallback transport；只读、显式成本策略、无 key 日志，按 capability allowlist 启用，不与 DSH native 无条件并发。

## 失败与测试

HTTP 429、5xx、超时、空响应和字段缺失必须映射为可观测失败或 `unavailable`，不能伪造高置信 PnL。普通测试使用注入的 fake fetcher，不触网；真实 endpoint 只能在显式 live canary 中使用。

每个 provider adapter 可声明 `supported_fields`。Router 只用它做执行前的保守筛选：缺少声明的旧
adapter 仍由结果契约校验，明确不支持所请求字段的 route 必须跳过。当前 OKX 不声称支持 CoinEx
已有的 `spot_price/spot_volume/index_price/basis`；当前 snapshot 也不得冒充事件窗口或 delta。

Search adapter 只负责把外部响应转换成 `SearchResult`。是否允许执行、允许访问哪些域、预算和三时间戳顺序由 `SearchCapabilityService` 判定；不得在 adapter 内复制 Capability 生命周期或写业务账本。

Search 进入研究 Evidence 后始终保持 `authority=search_derived`。`www.` 与 apex host 使用同一
保守 publisher `source_id`，同站不同页面不能满足多个独立来源；官方域名也不能由 Search
adapter 自行晋升为 `official`。完整可信边界见
[ADR-0017](../../docs/decisions/ADR-0017-search-evidence-citation-attribution.md)。

## Source Registry 边界

`research/source_registry.py` 读取 Domain Pack 的 `evidence/source_registry.yaml`，只负责将
Search locator 或 Fetch 最终 URL 解析为 `approved_locator`、`discovery_only` 或 `unknown`。
它不是第二个插件系统，不执行 HTTP、不写 Evidence/Fact/账本，也不替代 Gateway 的权限、PIT 和
语义 Gate。新增来源必须先改 Pack canonical policy，并通过 license/audit、domain/path、
requirement 和 redirect 测试；未知或未审计来源只能留在 DSH trajectory/source candidate。

`official_sources/` 的 parser 只提取可确定的字段（当前为 `event.identity`），不让 LLM 从正文
猜测 actor、时间或政策 delta。`policy.delta`、分钟级宏观传导和政策预期定价必须由专用 typed
provider 提供，不能用 Search 摘要、FRED 日频或 current snapshot 替代。

事件窗口的 `crypto-window://<sha256>` payload 只属于 Provider archive；EventWatch 表保存唯一
slot 状态和 hash，Evidence/Fact 仍只能由 Gateway 入账。Router 对包含 `event_id` +
`requested_event_offsets` 的查询只选择 `requires_event_window=true` route，普通 current snapshot
查询排除该 route。

Crypto spot/derivatives 的事件窗口 freshness 当前为 600 秒，用于覆盖合法的 `t-5m -> t+1m`
比较；这不是 current snapshot 的宽松历史容忍。缺失 event offsets 时 current snapshot 仍不能
关闭窗口 requirement。
