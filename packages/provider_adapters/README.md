# Provider Adapters

## 目的

把第三方 HTTP、交易所和通知 SDK 适配为 Kernel 的 `MarketDataPort`、`MarketWindowPort` 和 `NotificationPort`。adapter 只返回 canonical Pydantic DTO，不拥有 Gate、账本或发布权限。

## 当前实现

- `market/okx.py`：OKX 公共 ticker 和 1m candle 适配；bid/ask 优先，只有 last 时显式 `estimated`。
- `research/`：Web Search/Fetch 与 deterministic replay 到 canonical `EvidenceCandidate` 的薄适配。
- `official_sources/`：受域名白名单保护的官方文档能力；不复制 Gateway 权限逻辑。
- `market/okx_research.py`：OKX ticker/funding/OI/mark 的研究证据适配。
- `macro_market/fred.py`：FRED 公开序列的 PIT 观测适配，保留数据日期用于鲜度判断。
- `notifications/local.py`：本地 JSONL 通知 adapter，便于单机验证；Email/IM 继续作为可替换 adapter。
- `search/fake.py`：普通测试使用的确定性 canonical Search transport。
- `search/openai_compatible.py`：接收已配置客户端调用的强类型 seam；不持有 key、不猜测某个中转站的私有响应格式，所有输出仍由 Kernel Search Gate 校验。
- `search/openai_responses.py`：使用官方 `AsyncOpenAI` Responses `web_search`；`web_search_call.action.sources` 只作为发现集合，只有 message `output_text.annotations[].url_citation` 的 exact text span 与该集合中的 canonical URL 明确绑定后才生成 `SearchEvidence`。同一 claim/URL alias 保守去重，缺少可归因 citation 时 fail-closed；默认不启用。中转不支持官方 `max_tool_calls` 时，内部调用成本按 `$0.01/call` 保守估算并在返回后执行预算 Gate，不能宣称为 Provider 硬预授权。

## 失败与测试

HTTP 429、5xx、超时、空响应和字段缺失必须映射为可观测失败或 `unavailable`，不能伪造高置信 PnL。普通测试使用注入的 fake fetcher，不触网；真实 endpoint 只能在显式 live canary 中使用。

Search adapter 只负责把外部响应转换成 `SearchResult`。是否允许执行、允许访问哪些域、预算和三时间戳顺序由 `SearchCapabilityService` 判定；不得在 adapter 内复制 Capability 生命周期或写业务账本。

Search 进入研究 Evidence 后始终保持 `authority=search_derived`。`www.` 与 apex host 使用同一
保守 publisher `source_id`，同站不同页面不能满足多个独立来源；官方域名也不能由 Search
adapter 自行晋升为 `official`。完整可信边界见
[ADR-0017](../../docs/decisions/ADR-0017-search-evidence-citation-attribution.md)。
