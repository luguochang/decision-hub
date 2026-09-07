# 持久化研究资料目录与 DSH 适配器转换器方案

日期：2026-09-04  
状态：proposal，待产品 owner 和架构 owner 评审  
范围：Decision Hub、DSH 官方插件、来源采集、90 天历史回填、跨 Run 研究检索、GitHub/网络项目转换  
本文性质：方案设计，不代表已经完成实现、实时网络验收或生产能力承诺。

## 1. 摘要与最终建议

本方案建议建设一套由 Decision Hub 所有的、按来源和时间可追溯的持久化研究资料目录（Research Catalog），并在 DSH 中提供薄的查询和运维入口。同时建设一个离线开发工具 `dsh-adapter-forge`，把常见形态的开源项目转换为 **候选** DSH Skill、MCP 包装、SourceConnector 或 CapabilityAdapter。

核心边界如下：

```text
DSH
  = Agent Loop、Session、Skill、Tool、MCP、交互、查询与状态展示

Decision Hub
  = 耐久采集、原始归档、文档/事实版本、PIT、冲突、Gate、长期账本

Hub worker / SourceConnector
  = 定时增量、90 天回填、repair、确定性解析、浏览器 fallback

第三方项目
  = SourceConnector、ResearchCapabilityAdapter、MCP 服务或 parser
```

不要把持续爬取、长期游标、事实账本和私有数据库塞进 DSH 插件。DSH 插件的进程和升级生命周期不适合承担可靠采集，也不应形成第二套事实源。

推荐按以下顺序推进：

1. 先修好跨 Run 资料目录所需的身份、版本、游标并发和迁移纪律。
2. 只接官方 API、RSS 和普通 HTTP，完成无 LLM 的最近 90 天回填。
3. 增加增量同步、修订、quarantine/promotion 和可重放解析。
4. 增加 SQLite FTS5/BM25、结构化 `fact.lookup`、PIT/as-of 查询和 Research MCP。
5. 仅对确有需要的站点引入隔离的 Playwright 浏览器采集器。
6. 用固定问题集比较 cold fetch 和 warm catalog，再决定是否扩大采集范围。
7. 最后开发 `dsh-adapter-forge`，首版只覆盖确定性较高的 Skill、MCP/OpenAPI、SourceConnector 和受控 CLI 路径。

## 2. 要解决的问题与不解决的问题

### 2.1 目标问题

- 同一个公告、指标、网页或文档在不同 Run 中被重复获取和重复解析。
- 新 Run 无法直接按实体、指标、来源和时间窗口查询历史资料。
- 模型需要反复读取长网页，导致输入 token、工具轮次和延迟增加。
- 搜索摘要、转载内容、模型摘要和旧数据容易混在一起，形成不可追溯的“事实”。
- 首次上线没有可恢复、幂等、可审计的最近三个月历史初始化流程。
- DSH/Hub 外部项目接入目前依赖手工重构，缺少统一的分类、脚手架、权限和测试流程。

### 2.2 明确不做

- 不做“任意 GitHub 仓库一键生成生产级 DSH 插件”的承诺。
- 不把模型摘要、搜索 snippet 或浏览器页面上的自然语言直接作为 canonical fact。
- 不在 DSH 内再造一套队列、Agent Loop、Session、事实账本或长期 cursor。
- 不在第一阶段引入 Qdrant、Milvus、MemOS、OpenViking 等第二事实存储。
- 不把 LLM 浏览器代理作为三个月回填的默认路径。
- 不在 Alembic migration 或应用启动逻辑中隐式联网并执行历史回填。
- 不通过 CAPTCHA 绕过、未授权登录态或违反 robots/ToS 的方式采集。
- 不把“缓存命中”当成“事实可信”；可缓存的数据仍必须经过来源、PIT、hash、权限和冲突校验。

## 3. 当前仓库现状与依据

### 3.1 已有的正确基础

当前架构已经明确了 DSH 和 Hub 的边界：[DSH 与 Decision Hub 边界指南](DSH_AND_HUB_BOUNDARY_GUIDE.md)。其中的核心结论是：DSH 是 Agent 的执行器和日常工作台，Decision Hub 是产品控制面、可信边界和长期资产库。

仓库已经具备以下可复用能力：

| 现有能力 | 代码/文档位置 | 对本方案的意义 |
|---|---|---|
| `SourceConnector.poll(cursor)`、SourceRegistry 和 source health | [`packages/kernel/decision_hub_kernel/ports/sources.py`](../../packages/kernel/decision_hub_kernel/ports/sources.py)、[`source_ingest.py`](../../packages/kernel/decision_hub_kernel/application/source_ingest.py) | 作为增量采集协议的基础 |
| TextEnvelope、内容 hash、revision 和失败不推进 cursor | [`packages/source_adapters/README.md`](../../packages/source_adapters/README.md) | 保留现有 admission 语义并扩展跨 Run 目录 |
| Evidence、Fact、PIT、authority、freshness、conflict 和语义 Gate | [`research_evidence.py`](../../packages/kernel/decision_hub_kernel/application/research_evidence.py)、[`fact_store.py`](../../packages/kernel/decision_hub_kernel/application/fact_store.py)、[`sufficiency.py`](../../packages/kernel/decision_hub_kernel/decision/sufficiency.py) | 作为 canonical catalog 晋级和 Run 投影的验证边界 |
| `FactEnvelope` 和事件窗口 | [`ADR-0024`](../decisions/ADR-0024-semantic-fact-and-event-window-boundary.md)、[`research_fact.schema.yaml`](../../contracts/schemas/research_fact.schema.yaml) | 避免把金融语义通过自由文本传递 |
| 搜索 provider 路由边界 | [`ADR-0022`](../decisions/ADR-0022-search-provider-route-boundary.md) | Search 只做 discovery，精确事实由 typed provider 或归档事实提供 |
| 薄 DSH Host/Client 插件 | [`extensions/dsh/decision-hub/README.md`](../../extensions/dsh/decision-hub/README.md) | 只增加触发、查询、状态和审批，不增加第二账本 |
| 固定 DSH 上游版本与公开 seam | [`infra/dsh/upstream.lock.json`](../../infra/dsh/upstream.lock.json)、插件 README | 适配器生成物必须记录兼容版本并跑契约测试 |

### 3.2 当前阻碍复用的缺口

当前的 Research Evidence 和 Fact 主要按 `research_session_id`、`run_id` 隔离。Evidence ID 由 session 和 content hash 派生，查询也以 `run_id` 为主；因此它们是单次 Run 的审计投影，不是跨 Run 的资料目录。仅把已有表继续写满，不会让下一个 Run 自动少联网。

还存在以下上线前问题：

1. 没有跨 Run 的不可变 raw archive、source document、document revision、chunk 和 FTS5/BM25。
2. 官方 feed 的首次启动逻辑默认推进到最新 cursor，不导入历史，当前没有 90 天 backfill job。
3. `observations.content_hash` 按正文全局去重，可能吞掉不同来源的 provenance 和独立来源信息。
4. `revision_of` 缺少父记录存在、同来源/同 URL、环检测等约束。
5. source cursor 没有 lease/CAS/generation，并发 worker 可能覆盖或回退 cursor。
6. conflict 主要依赖 provider 主动填写，缺少同实体、指标、窗口的自动冲突发现。
7. 没有 staging/quarantine/promotion 分层，批量导入容易绕过现有验证边界。
8. ORM 与 migration 存在潜在漂移，且 migration README 与实际 Alembic head 不一致。
9. live Research MCP 仍有 `create_all()` 路径，与生产只跑 Alembic 的纪律冲突。

本地库当前只有少量手工 observation，`research_evidence`、`research_facts` 和 `event_watches` 尚没有可复用的历史事实缓存。因此本方案应被视为新增产品能力，而不是简单启用已有缓存开关。

## 4. 产品价值与 token 经济性

### 4.1 价值链

持久化目录的价值不是“数据库里多了一份网页”，而是下面这条链路：

```text
同一内容只 fetch/parse/extract 一次
    -> 事实和原文 span 可按时间/来源/实体查询
    -> 新 Run 只注入与 requirement 相关的少量结果
    -> 少一次搜索、补证或整页阅读
    -> 更低延迟、更少 token、更稳定的 citation/PIT
```

### 4.2 可能节省什么

- HTTP/RSS/API 请求数：相同 URL、ETag、外部 ID 或 content hash 的重复请求可减少。
- 浏览器时间：确定性 HTTP 和本地归档命中后无需打开动态页面。
- 模型输入 token：不再把完整长文档反复传入，只传 typed fact、短 span 和引用。
- 模型输出 token：减少“补证说明”和重复总结轮次。
- 端到端延迟：本地 FTS/结构化查询通常比跨网搜索和浏览器交互快。

### 4.3 不应假设的收益

- 数据库本身不保证 token 下降。
- 如果检索仍返回整页正文，输入 token 可能反而上升。
- 如果由模型逐步点击浏览器，浏览器采集可能比普通 HTTP 更贵、更慢。
- 对已经稳定命中 prompt/KV cache 的固定前缀，gross token 降幅不等于同等费用降幅。
- 历史数据越多，陈旧事实和错误抽取的放大风险越高。

已有真实 Run 约为 `797K input / 49.3K output / 93% cache hit`。这只能说明仍值得测量跨 Run 复用，不能直接推导节省比例。若把 93% 理解为 token 级 cache hit，未命中输入约为 `797K * 7% = 55.8K`，其余输入仍可能按 provider 缓存价格计费。

### 4.4 验证方法

上线前建立固定评测集，分别跑以下两条路径：

```text
cold/live：本地没有可用 revision，允许 discovery + fetch + parse
warm/catalog：存在符合 as_of/freshness/authority 的本地 revision
```

必须同时记录 gross/cached/uncached token、模型步数、搜索和补证轮次、HTTP 请求、浏览器分钟、provider 费用、p50/p95 延迟、citation coverage、stale rate、conflict rate、Gate 结果和最终答案准确率。

建议的阶段性 go/no-go 门槛是：网络请求减少至少 50%、gross input token 减少至少 30%、本地检索 p95 小于 500ms，且 hard-fact coverage、引用覆盖率、固定集正确率和错误召回率不得退化。这些是待验证的验收标准，不是当前已经实现的事实。

## 5. 目标架构

### 5.1 端到端拓扑

```text
官方 API / RSS / GitHub API / 普通 HTTP / BrowserCollector
             |
             v
      CollectionJob + FetchAttempt
             |
             v
      staging / quarantine
             |
             v
      immutable RawBlob（原始响应，不原地覆盖）
             |
             v
      SourceDocument + append-only DocumentVersion
             |
             v
      deterministic parser / normalizer / validator
             |
             +--> DocumentChunk -> FTS5/BM25
             |
             +--> FactVersion -> canonical FactCatalog
             |
             v
      knowledge.search / fact.lookup（PIT/as_of/freshness 过滤）
             |
             v
      Run 固定引用具体 revision/fact version
             |
             v
      现有 per-Run Evidence / Fact / Snapshot / Gate
```

### 5.2 所有权矩阵

| 能力 | DSH | Hub API/Worker | Kernel/DB | 第三方 adapter |
|---|---|---|---|---|
| Chat、Session、Trajectory | 唯一所有 | 只存关联 ID | 不复制 | 不拥有 |
| 调度、重试、lease、backfill | 展示控制入口 | 调度和执行 | 持久状态与 CAS | 提供 poll/fetch |
| Raw、Document、Fact 版本 | 查询 | 写入命令/查询 | 唯一账本 | 输出标准 DTO |
| PIT、authority、冲突、Gate | 展示结果 | 调用服务 | 唯一裁决 | 不能自行晋级 |
| 浏览器登录态和网络权限 | 不持有长期密钥 | 隔离 worker 管理 | 记录审计引用 | 受 manifest 限制 |
| 适配器转换 | 调用候选能力 | 审核/安装状态 | manifest 和审计 | 生成候选 |

### 5.3 与现有架构的关系

`research_evidence`、`research_facts`、`snapshot` 和 `Gate` 不应被改造成共享可变缓存。它们继续保存某个 Run 在某个时点实际使用的证据投影；新目录提供跨 Run 的可复用 source revision 和 fact version，Run 通过引用把它们固定下来。

## 6. 数据存储设计

### 6.1 分层原则

存储必须明确分成四层：

1. **Raw layer**：不可变、可校验、可重放的原始响应或文件。
2. **Document layer**：按来源和稳定文档身份组织的版本化文本与 chunk。
3. **Canonical fact layer**：经过 schema、单位、窗口、PIT、来源和冲突校验的结构化事实。
4. **Run projection layer**：某次研究实际使用的 Evidence、Fact、Snapshot、Gate 和报告。

模型摘要、embedding、排序特征和搜索索引属于 derived layer，可以重建，不能作为原始或事实真源。

### 6.2 推荐最小模型

下面是逻辑模型，不要求一次性按此表名全部实现；第一阶段可以从 additive migration 开始逐步落地。

#### `collection_sources`

保存来源契约和安全边界：

- `source_id`、`source_group_id`、`name`、`kind`（api/rss/http/browser/file）。
- `authority_level`、`publisher`、`license_status`、`robots_status`、`tos_review_ref`。
- `allowed_domains`、`allowed_paths`、`auth_profile_ref`、`max_response_bytes`、`rate_limit`。
- `poll_interval`、`retention_policy`、`parser_version`、`manifest_hash`。
- `status`：`candidate|approved|paused|revoked`。

同一发布者的官网、转载站和聚合站应通过 `source_group_id` 区分“来源身份”和“独立来源计数”。

#### `collection_jobs`

保存一次采集作业：

- `job_id`、`kind`：`backfill|incremental|repair|reparse`。
- `source_id`、`range_start`、`range_end`、`as_of`、`manifest_hash`。
- `status`：`queued|running|paused|completed|failed|cancelled`。
- `checkpoint`、`lease_owner`、`lease_expires_at`、`attempt_count`。
- `request_budget`、`browser_budget`、`llm_budget`。
- `fetched_count`、`promoted_count`、`quarantined_count`、`failed_count`。
- `started_at`、`completed_at`、`error_summary`。

回填 job 与 live source cursor 必须分开。回填完成后用 watermark 交接，不直接覆盖 live cursor。

#### `fetch_attempts`

一条网络或文件读取尝试对应一条记录：

- `attempt_id`、`job_id`、`source_id`、`requested_url`、`resolved_url`。
- `started_at`、`completed_at`、`status_code`、`mime_type`、`etag`、`last_modified`。
- `provider`、`collector_version`、`browser_session_ref`。
- `response_hash`、`response_bytes`、`error_code`、`redirect_chain`。
- `robots/ToS` 检查结果和 `security_verdict`。

失败尝试必须保留错误 provenance；失败不能推进 cursor。

#### `raw_blobs`

内容寻址的不可变原始内容：

- `raw_blob_id`、`sha256`、`byte_size`、`mime_type`、`compression`。
- 对象存储或文件系统中的 `storage_ref`。
- `received_at`、`retention_until`、`encryption_key_ref`。
- `content_encoding`、`source_response_metadata`。

同一 raw blob 可以被多个来源 revision 引用；内容相同不等于来源身份相同。

#### `source_documents`

稳定的文档身份：

- `document_id`、`source_id`、`external_id`、`canonical_url`、`url_fingerprint`。
- `publisher`、`document_kind`、`first_seen_at`、`last_seen_at`。
- 唯一键优先使用 `source_id + external_id`；无 external ID 时使用 `source_id + normalized_url`。

#### `document_versions`

append-only 的文档版本：

- `document_version_id`、`document_id`、`raw_blob_id`、`content_hash`。
- `published_at`、`observed_at`、`received_at`、`valid_from`、`valid_to`。
- `supersedes_version_id`、`revision_reason`、`canonical_url`。
- `parser_version`、`normalizer_version`、`schema_version`。
- `quality_status`：`staged|quarantined|promoted|deprecated|rejected`。
- `license_status`、`prompt_injection_flag`、`manual_review_ref`。

禁止原地更新已晋级版本的正文、时间、来源或 hash。修订必须新增版本并建立父子关系。

#### `document_chunks`

由确定性规则生成的可定位文本片段：

- `chunk_id`、`document_version_id`、`ordinal`。
- `start_offset`、`end_offset`、`text_hash`、`token_count`。
- `heading_path`、`page_no`、`table_ref`、`locator`。
- `fts_rowid` 或 FTS5 虚表关联。

chunk 是检索定位单位，不是事实本身。chunk 变化时可以重建索引，不应改写旧 Run 引用的定位信息。

#### `fact_catalog` 与 `fact_versions`

`fact_catalog` 表示自然键，`fact_versions` 保存该事实随时间变化的版本：

- 自然键：`metric_family`、`entity/instrument`、`field`、`venue`、`unit`、`window_kind`。
- 数值：`value_numeric`、`value_text`、`value_type`、`scale`。
- 时间：`event_time`、`valid_from`、`valid_to`、`published_at`、`observed_at`、`known_at`。
- 来源：`document_version_id`、`raw_blob_id`、`source_group_id`、`authority_level`。
- 版本：`fact_version_id`、`supersedes_fact_version_id`、`schema_version`、`transform_version`。
- 质量：`quality_status`、`confidence`、`conflict_group`、`independence_group`。
- 审计：`promotion_actor`、`promotion_reason`、`created_at`、`retracted_at`。

同一自然键出现不同值时必须并存并标记冲突，不能用“最后写入”静默覆盖。

#### `assertion_source_links`

把一个断言与多个来源版本关联，记录：

- `fact_version_id`、`document_version_id`、`support_type`（primary/confirming/contradicting）。
- `independence_group`、`locator`、`source_weight`。
- `observed_at` 和链接建立时间。

这能保留“两个独立来源发布相同事实”和“一个转载链上的多个页面”之间的区别。

#### `run_evidence_links`

把 Run 固定到目录版本：

- `run_id`、`requirement_id`、`document_version_id` 或 `fact_version_id`。
- `retrieved_at`、`as_of`、`selection_policy_version`、`rank`。
- `quoted_span_hash`、`citation_locator`、`reason_selected`。

旧 Run 的引用不会因为新 revision 到来而变化；新的 Run 必须重新按策略选择版本。

#### `retrieval_events`

为价值评估建立事实数据：

- `retrieval_event_id`、`run_id`、`query_hash`、`route`、`cache_state`。
- `source_ids`、`revision_ids`、`result_count`、`selected_count`。
- `network_requests`、`browser_seconds`、`provider_calls`。
- `input_tokens`、`cached_input_tokens`、`output_tokens`。
- `latency_ms`、`stale_result`、`conflict_seen`、`gate_effect`。

没有这类记录，就无法证明“落库节省了 token”或识别本地目录是否增加了错误召回。

#### `quarantine_records`

未晋级数据的隔离原因：

- `quarantine_id`、`raw_blob_id`/`document_version_id`、`reason_code`。
- `detected_at`、`detector_version`、`severity`、`review_status`。
- `reviewer`、`decision`、`decision_reason`、`expires_at`。

浏览器抓取、未知 schema、可疑 prompt injection、许可不明确、超大响应和冲突未裁决内容默认进入 quarantine。

### 6.3 身份、去重与修订规则

必须同时维护三种不同概念：

1. **内容身份**：`sha256(raw bytes)` 或规范化文本 hash，用于避免重复解析。
2. **文档身份**：`source_id + external_id/normalized_url`，用于识别同一来源的后续修订。
3. **来源身份**：`source_id/source_group_id`，用于 provenance、authority 和独立来源计数。

相同内容 hash 不能直接返回旧 event 并丢掉第二来源；应复用 raw blob，但创建新的 source-document link 或 revision link。`revision_of` 必须验证父记录存在、同一文档身份、无环、不能指向未来版本，并在 source/parser 变化时保留原因。

### 6.4 三时间戳与 PIT

每条文档和事实至少保存：

- `published_at`：来源声明的发布时间。
- `observed_at`：事件或数据实际观察时间。
- `received_at/ingested_at`：Hub 收到并登记的时间。

对回测或历史决策，查询必须显式带 `as_of`，只返回在该时点已经 known 的版本。当前值不能倒填历史窗口；迟到事件必须标记 `retrospective_only` 或 `no_baseline`。

## 7. 采集与解析策略

### 7.1 Provider ladder

固定优先级：

```text
官方 API / RSS / GitHub REST 或 GraphQL
    -> 普通 HTTP + 确定性 selector/parser
    -> Scrapy 或 Crawlee 队列
    -> 有界 Playwright/Crawl4AI 浏览器 fallback
    -> LLM 浏览器代理，仅处理极少数不可确定的交互
```

对 GitHub 资料优先使用官方 REST/GraphQL 或 GitHub MCP，不爬 GitHub 页面；对金融硬事实优先使用 typed provider 或已验证的官方文档，不把搜索摘要当精确指标。

### 7.2 采集器的统一接口

每个来源适配器应实现类似以下端口：

```python
class SourceConnector(Protocol):
    source_id: str

    def poll(self, cursor: SourceCursor, *, window: TimeWindow | None = None) -> PollResult:
        ...

    def fetch(self, locator: Locator) -> FetchResult:
        ...

    def normalize(self, raw: RawBlob) -> NormalizedDocument:
        ...

    def extract_facts(self, document: NormalizedDocument) -> list[FactCandidate]:
        ...
```

实际实现可拆为多个类，但必须让网络获取、规范化、事实抽取和 promotion 边界可独立测试、重放和计量。适配器不能直接写事实账本。

### 7.3 浏览器 fallback 原则

- 浏览器是采集能力，不是事实裁决器。
- 默认只允许固定域名、固定路径和固定动作，禁止任意导航。
- 每个重定向都重新做域名和协议检查，禁止 SSRF 到内网地址。
- 限制页面大小、下载 MIME、脚本运行时间、并发数、浏览器分钟和文件写入目录。
- 登录态按域隔离、短期使用、不得写日志；cookie 和 storage state 不进 raw blob。
- 页面正文按不可信数据处理，先做 prompt injection 检测，再交给确定性抽取或人工复核。
- 记录浏览器版本、脚本版本、页面 URL、动作序列 hash 和最终响应 hash。
- CAPTCHA、强制登录或 ToS 不允许自动化时，进入人工补缺或放弃，不尝试绕过。

LLM 浏览器代理只有在结构化接口和确定性 selector 均不可用时才启用，且每次调用必须记录模型、步数、token、费用和结果审计状态。

### 7.4 解析器与模型提取

优先使用确定性 parser：JSON schema、CSS/XPath selector、日期/单位规范化、PDF 表格解析和文档结构解析。LLM 只做：

- 结构变化后的规则候选生成。
- 无法用确定性规则处理的低频非结构化段落抽取。
- 生成供人工审核的摘要或标签。

LLM 产物必须带 `extractor_model`、`prompt_version`、`input_revision_id` 和 `candidate` 状态，不得自动升级为 canonical fact。

## 8. 检索与上下文组装

### 8.1 查询类型

第一阶段提供三类查询：

1. `knowledge.search`：全文检索文档 chunk，支持关键词、来源、时间、authority、文档类型和 `as_of`。
2. `fact.lookup`：按实体、指标族、字段、venue、单位、窗口和有效时间查询 typed fact。
3. `source.status`：查看来源健康、最后成功 cursor、stale 状态、失败统计和回填覆盖率。

### 8.2 查询返回结构

返回应紧凑且可引用：

```json
{
  "query_id": "q_...",
  "as_of": "2026-09-04T00:00:00Z",
  "items": [
    {
      "kind": "fact",
      "fact_version_id": "fv_...",
      "fact_key": "btc.funding_rate.binance.8h",
      "value": 0.012,
      "unit": "percent",
      "valid_from": "2026-09-03T16:00:00Z",
      "known_at": "2026-09-03T16:02:10Z",
      "authority": "typed_provider",
      "citation": {
        "document_version_id": "dv_...",
        "locator": "table.row[3].funding_rate",
        "span_hash": "sha256:..."
      }
    }
  ],
  "omitted": {"stale": 2, "conflict": 1, "quarantined": 3}
}
```

`knowledge.search` 返回的 snippet 只能作为 locator；若用户要精确指标，必须通过 `fact.lookup` 或 `web.fetch` 进入 Evidence Gateway。

### 8.3 排序与过滤

首期用 SQLite FTS5/BM25，加上确定性过滤和排序：

1. `as_of` 可见性和 freshness。
2. authority 和 source allowlist。
3. requirement 的实体、指标、时间窗口和单位匹配。
4. 来源独立性和冲突状态。
5. BM25 相关性、发布时间和内容完整性。

向量 embedding 仅在固定评测集证明 FTS 和结构化过滤召回不足后加入，并作为可重建的 derived index。向量相似度不能覆盖 PIT、单位或 authority 过滤，也不能直接提升事实可信度。

### 8.4 Context budget

每个 requirement 应有独立的上下文预算：

- 先返回少量 typed facts。
- 只有字段缺口或冲突时，追加对应原文 span。
- 只有 span 不足以验证时，才请求整篇 fetch。
- 不把同一 revision 的摘要、全文和多个重复 snippet 同时注入。
- 对同一 Run 的重复查询使用 `query_hash + selection_policy_version` 复用结果，但仍记录引用。

## 9. 污染、幻觉与安全控制

### 9.1 Promotion 状态机

```text
fetched
  -> staged
  -> validated
  -> quarantined  --人工/规则通过--> validated
  -> promoted
  -> deprecated / retracted
```

任何一项关键校验失败都只能进入 quarantine 或 rejected，不能为了提高 coverage 自动放行。promotion 必须记录 actor、规则版本、schema 版本和原因。

### 9.2 必须防止的污染路径

| 风险 | 控制 |
|---|---|
| 旧事实被当成当前事实 | 强制 `as_of`、freshness、validity 和 stale 标记 |
| 模型摘要循环成为事实 | derived asset 与 canonical fact 分离，摘要不得自证 |
| 转载被算作独立来源 | `source_group_id`、independence group 和 publisher lineage |
| 抽取器升级静默改历史 | raw immutable、parser version、reparse 新版本 |
| 单位/venue/窗口混用 | FactEnvelope 和 Domain Semantic Gate |
| 冲突被最后写入覆盖 | 冲突并存、显式 conflict_group、Gate fail-closed |
| 网页 prompt injection | 内容区与指令区分离、检测、quarantine、禁止执行页面命令 |
| 恶意响应/SSRF/超大文件 | allowlist、逐跳重定向校验、大小/MIME/超时限制 |
| 凭据泄露 | 域隔离 secret、短期 token、不记录 cookie/body |
| 许可和 ToS 违规 | source manifest、robots/ToS 审核、保留期限和人工批准 |

### 9.3 错误语义

错误必须细分为 `source_timeout`、`source_rate_limited`、`parse_schema_error`、`security_rejected`、`license_unknown`、`quarantine_conflict` 等稳定 code，不能把失败统一改写为“没有数据”或“成功但低置信度”。

## 10. 最近 90 天初始化方案

### 10.1 作业定义

初始化是一次上线前显式、可暂停、可恢复、幂等的 `backfill` job，不是 migration，也不是 worker 启动副作用。当前日期下默认范围约为 `2026-06-04` 至 `2026-09-04`，但实现必须通过参数固定 `range_start`、`range_end` 和 `as_of`，不能硬编码。

建议命令形态：

```bash
python -m decision_hub backfill start \
  --pack crypto_macro \
  --range-start 2026-06-04T00:00:00Z \
  --range-end 2026-09-04T00:00:00Z \
  --as-of 2026-09-04T00:00:00Z \
  --manifest packs/crypto_macro/evidence/source_manifest.yaml \
  --mode quarantine-first
```

### 10.2 执行步骤

1. 读取并冻结 Pack、source manifest、parser/normalizer 版本和 manifest hash。
2. 为每个来源创建独立 job 分片、lease、预算和 checkpoint。
3. API/RSS/结构化 provider 优先分页回填；普通 HTTP 使用 ETag/Last-Modified；浏览器只补 gap。
4. 所有原始响应先写 raw/staging，不直接进 canonical fact。
5. 确定性解析、schema 验证、时间规范化、来源和许可检查。
6. 对失败、未知字段、冲突和可疑内容写 quarantine；不推进该来源 checkpoint。
7. 通过 promotion 的文档生成 chunk、FTS 索引和 typed fact candidate。
8. 事实通过 Domain Semantic Gate 后才写 `fact_versions` 的 promoted 状态。
9. 保存覆盖率、缺失来源、失败原因、watermark、耗时和 manifest hash。
10. 由增量调度从固定 watermark 接棒；回填 cursor 与 live cursor 不混用。

### 10.3 幂等与恢复

- job 以 `source_id + range + manifest_hash + parser_version` 作为逻辑身份。
- 相同 raw hash 不重复存储或解析；不同 source identity 仍保留链接。
- 进程崩溃后从最近 checkpoint 恢复，未完成 attempt 可以重试。
- lease 过期后由新 worker 接管；提交 cursor 使用 generation/CAS。
- 重新运行只补缺失 revision，不删除或覆盖已晋级历史。
- 回填完成后必须生成机器可读 manifest 和人工可读质量报告。

### 10.4 初始化边界

默认不为最近三个月的每条页面创建完整 Research Run，也不默认调用 LLM。只有高影响事件、评测集样本或事实抽取确实需要时，才异步生成少量 Run。这样可以避免把初始化成本误当成产品日常成本，也避免一次性把错误模型抽取写入可信账本。

### 10.5 覆盖率定义

“完成三个月初始化”不能只看抓了多少 URL，应按以下维度统计：

- source coverage：目标来源中成功、失败、未授权和不可访问的比例。
- event coverage：重要事件类别和日期窗口的覆盖率。
- metric coverage：Pack 定义的指标序列、实体、venue、单位和时间窗覆盖率。
- fact quality：promoted、quarantined、conflict、stale、retracted 比例。
- replay coverage：固定问题集能否在对应 `as_of` 下复现相同引用。

## 11. DSH 插件设计

### 11.1 插件职责

在现有 `extensions/dsh/decision-hub` 上增量增加薄能力：

- `backfill.start`、`backfill.status`、`backfill.pause/resume/cancel`。
- `source.health`、`source.coverage`、`source.repair`。
- `knowledge.search`、`fact.lookup` 的受限查询入口。
- quarantine 列表、人工批准、拒绝和 reprocess。
- 展示当前 Run 使用的 catalog revision、PIT、引用和 stale/conflict 警告。
- 从 DSH Session 跳转到 Decision Desk 的报告、审计和 job 详情。

插件不负责：

- 自己维护 SQLite/向量库、Source cursor、Fact ledger 或第二套 scheduler。
- 直接执行任意网页 URL、shell 命令或未经 manifest 审计的 MCP。
- 把 DSH Session JSONL 复制进 Hub，或绕过 Hub Gateway 写 Evidence/Gate。

### 11.2 公开调用边界

建议通过现有 Host/Hub bridge 暴露稳定的 command/query contract：

```text
POST /decision-hub/v1/backfills
GET  /decision-hub/v1/backfills/{job_id}
POST /decision-hub/v1/backfills/{job_id}/pause
POST /decision-hub/v1/backfills/{job_id}/resume
POST /decision-hub/v1/backfills/{job_id}/cancel
GET  /decision-hub/v1/sources/{source_id}/health
POST /decision-hub/v1/knowledge/search
POST /decision-hub/v1/facts/lookup
GET  /decision-hub/v1/quarantine
POST /decision-hub/v1/quarantine/{id}/decision
```

所有请求都必须经过 host key、bridge key、capability manifest、query budget、PIT/as_of 和审计校验。结果只返回最小必要字段和 citation locator。

### 11.3 何时用 MCP

跨进程的查询或第三方服务适合通过 DSH 官方 MCP client 接入；DSH 不应因此接管 MCP 服务的数据库迁移、监督运行或凭据管理。Hub 的 Research MCP 应调用 canonical query service，而不是重新实现一套搜索和缓存。

## 12. 开源采集和文档处理选型

### 12.1 推荐组合

| 组件 | 适合位置 | 结论 |
|---|---|---|
| Scrapy | Python HTTP/RSS/API 主路径 | MVP 首选，成熟、速度和可观测性好 |
| Crawl4AI | Python/Playwright 浏览器 fallback | 适合快速 PoC；必须锁定修复版本并隔离网络/文件权限 |
| Crawlee | 独立 Node browser collector sidecar | 真实站点 bake-off 证明需要队列/session/proxy 时再引入 |
| MarkItDown | 多格式文档转 Markdown | normalizer，不负责事实可信性 |
| Docling | PDF/Office/表格/OCR | 复杂官方报告的解析候选 |
| Browser4 + dsh-browser4 | DSH 原生浏览器实验 | 只做隔离 canary，需审计安装和生命周期代码 |
| Tencent BrowserSkill | 少量登录态、人工接管 | 不适合无人值守批量回填 |
| Firecrawl | 外部 provider/独立服务评估 | AGPL/服务体量和数据合规需单独评估，不做默认内核 |

在 5～10 个真实目标站上使用相同 fixture、预算和指标做 bake-off 后，才决定引入 Crawl4AI 还是 Crawlee。不能根据 README 中“零 token”“每机高访问量”等作者宣传作上线依据。

### 12.2 不建议直接引入的类别

MemOS、OpenViking、Qdrant 等 agent memory/RAG 或向量产品不能替代 Hub 事实账本。向量索引可以后置作为可重建召回层；把模型历史回答直接存为 memory 会显著增加污染风险。

## 13. 通用项目转换器：`dsh-adapter-forge`

### 13.1 产品定位

它应是离线开发工具或 CI action，而不是常驻运行时 DSH 插件。输入一个固定 commit 的 GitHub/网络项目，输出一个带权限、schema、测试和 upstream lock 的候选适配器包。输出状态只能是 `candidate`，人工审核后才允许 `approved`。

### 13.2 分类与目标映射

| 输入形态 | 输出形态 | 自动化程度 | 备注 |
|---|---|---:|---|
| `SKILL.md`、方法论、模板 | DSH Skill | 高 | 校验名称、字段、渐进披露和引用资源 |
| 已有 MCP server | DSH MCP 配置/薄插件 | 高 | 通常无需改写服务代码 |
| OpenAPI/FastAPI | 白名单化 MCP adapter | 中高 | 只暴露 GET/只读和必要 endpoint |
| RSS/API crawler | Hub `SourceConnector` | 中高 | 生成 cursor、DTO、fixture 和 parser 骨架 |
| 稳定 CLI/库 | MCP tool 或 CapabilityAdapter | 中 | 手工确认参数、超时、幂等和副作用 |
| 浏览器工作流 | Skill + 确定性 BrowserCollector | 中 | 动作和权限必须人工审核 |
| 完整 UI、状态型 Agent 框架 | 人工重构 | 低 | 无法从 README 推断生命周期和权限 |
| 任意普通仓库 | 分析报告 + 空脚手架 | 低 | 不自动执行、不自动接入生产 |

### 13.3 转换流水线

```text
固定 upstream commit
  -> 读取真实 LICENSE、SBOM、依赖和 secret scan
  -> Repomix/Tree-sitter 生成受预算约束的代码输入
  -> 分类 input_kind 与副作用等级
  -> 选择唯一 target_kind
  -> 生成 scaffold、manifest、schema、权限 allowlist
  -> 生成 contract/replay/异常/副作用测试
  -> 隔离环境 build + canary
  -> 人工审核
  -> candidate -> approved -> versioned promotion/rollback
```

自动生成器必须默认拒绝：任意 shell、任意网络域名、长期 secret、写数据库、交易动作、安装脚本和动态代码下载。所有生成物记录上游 commit、许可证、依赖锁、生成器版本、DSH 兼容 commit 和审核人。

### 13.4 可复用项目

- [Repomix](https://github.com/yamadashy/repomix)：仓库压缩、Tree-sitter 分析、token budget、secret scan；适合作为理解输入，不是插件生成真值。
- [FastMCP](https://github.com/PrefectHQ/fastmcp)：OpenAPI 到 MCP 的 bootstrap；必须显式排除复杂或有副作用 endpoint。
- [FastAPI-MCP](https://github.com/tadata-org/fastapi_mcp)：已有 FastAPI 服务的 schema/auth 包装。
- [dsh-plugin-kit](https://github.com/hyzyn/dsh-plugin-kit)：借用官方 bundle 脚手架，不当作语义转换器。
- [dsh-codex-port](https://github.com/STARDUSTLC666/dsh-codex-port)：只参考 Skill 搬运；其字段和命名规则需按 DSH 官方解析器重新校验。
- `github-to-mcp` 类项目：必须读取仓库真实 LICENSE；README badge 与实际许可证冲突的项目一律拒绝。

## 14. 实施分期与任务拆解

### K0：契约和迁移纪律

- 定义 catalog、document revision、fact version、query result 和 retrieval event schema。
- 修正 `content_hash` 与 source identity 的去重边界。
- 为 revision 增加父记录、同文档、环检测和 supersedes 校验。
- 为 source cursor 增加 lease、generation/CAS 和恢复测试。
- 统一 Alembic head、README、ORM 和生产启动路径；移除生产 `create_all()` 依赖。
- 增加 schema contract、migration upgrade/downgrade 和 replay fixtures。

退出门：可以在不改变旧 Run 语义的前提下，创建、重放和审计一个新的 source revision/fact version。

### K1：官方源 90 天回填

- 实现 `collection_jobs`、`fetch_attempts`、raw blob 和 quarantine 最小模型。
- 为官方 API/RSS/HTTP 适配器增加时间窗口分页和 backfill cursor。
- 完成无 LLM 的解析、规范化和 source/document revision promotion。
- 生成覆盖率、失败清单、watermark 和 manifest 报告。

退出门：至少一个 Pack 能从固定 manifest 幂等恢复最近 90 天数据，失败不会推进 cursor。

### K2：增量、修订与 repair

- 将 live poll 与 backfill watermark 串接。
- 支持 ETag/Last-Modified、迟到记录、修订和 reparse。
- 自动发现同自然键冲突并进入 quarantine/Gate。
- 增加 raw 重放和 parser 升级回归。

退出门：源内容更新、重复运行、worker 崩溃和解析器升级都不会改写旧版本。

### K3：跨 Run 检索和 MCP

- SQLite FTS5/BM25 与 document chunk。
- `knowledge.search`、`fact.lookup`、`source.status`。
- PIT/as_of/freshness/authority/source-group 过滤。
- Research MCP 和 DSH 插件查询入口。
- `retrieval_events` 和 token/延迟指标接通。

退出门：固定评测集能复现引用，旧 Run 引用不会被新 revision 改变。

### K4：浏览器 fallback

- 选 5～10 个真实目标站做 Scrapy/Crawl4AI/Crawlee 对比。
- 只对 API/HTTP 无法覆盖的来源引入有界 Playwright collector。
- 接入 allowlist、SSRF、MIME/大小、凭据隔离、浏览器预算和 quarantine。
- 浏览器 collector 输出统一 DTO，不直接写 canonical fact。

退出门：浏览器路径的可靠性、成本、延迟和安全指标达到与 HTTP 路径相同的审计要求。

### K5：收益验证

- 建立 cold/live 与 warm/catalog A/B。
- 比较 token、请求、延迟、质量、错误召回、stale/conflict、Gate 和用户有效性。
- 若 warm 路径没有稳定收益，则限制目录范围或回滚高成本来源。

退出门：达到第 4 节的阶段性门槛，且没有 hard-fact coverage 退化。

### K6：`dsh-adapter-forge`

- 首先支持 Skill、MCP/OpenAPI、SourceConnector 和受控 CLI。
- 生成 manifest、权限、schema、replay fixture、contract tests、SBOM 和 upstream lock。
- 生成结果默认 candidate，提供人工审核和 rollback。
- 暂不支持任意 UI、任意 Agent 框架和无人审查的浏览器工作流。

退出门：每种 target_kind 都有可运行的最小示例、拒绝策略和失败回滚，不以“能生成文件”作为成功标准。

## 15. 验收与观测指标

### 15.1 质量指标

- hard-fact coverage：固定 requirement 所需字段的覆盖率。
- citation coverage：最终事实是否都有可定位 source revision。
- stale-result rate：返回结果超过 freshness policy 的比例。
- conflict recall/precision：应发现的冲突是否进入 conflict/quarantine。
- wrong-source rate：事实是否来自错误 authority、venue、单位或时间窗口。
- replay stability：相同 `as_of + policy + fixture` 是否产生相同引用和 Gate 结果。
- promotion error rate：错误内容被晋级的比例。

### 15.2 成本与性能指标

- catalog hit、stale hit、quarantine hit、cold miss 比例。
- HTTP 请求数、浏览器秒数、provider 调用数。
- p50/p95 `knowledge.search` 和 `fact.lookup` 延迟。
- 每 Run gross/cached/uncached input token 和 output token。
- 每个 promoted fact 的采集、解析和存储成本。
- 回填吞吐、checkpoint 恢复时间和失败重试次数。

### 15.3 安全与合规指标

- allowlist 拒绝数、SSRF 拦截数、超大响应拒绝数。
- prompt injection 检测和 quarantine 数。
- 凭据使用域、泄露扫描结果和日志脱敏检查。
- robots/ToS/license 未确认的来源数量。
- raw retention 到期清理和 retraction 完成率。

## 16. 故障、回滚与运维

### 16.1 失败处理

- 网络失败：保留 attempt，指数退避，不推进 cursor。
- 解析失败：raw 保留，document/fact 进入 quarantine，可在 parser 升级后 reparse。
- schema 不兼容：拒绝 promotion，保留 raw 和失败版本。
- Provider 返回冲突：并存事实，暂停自动晋级，等待规则或人工决策。
- 浏览器崩溃/登录失效：停止该 source，避免无限重试和凭据污染。
- 索引损坏：从 document chunk 和 fact version 重建，不影响 canonical raw/fact。

### 16.2 回滚原则

- 回滚 parser、collector、adapter 或 DSH plugin 版本，不删除 raw、document 或 fact 历史。
- 将受影响 source/capability 标记为 `paused`，禁止新 promotion。
- 旧 Run 继续读取其固定 revision；新 Run 可以退回上一版 selection policy。
- 若发现批量污染，使用 `retracted`/`deprecated` 和审计事件，不做静默 delete。
- DSH 插件只回滚 UI/command binding，不修改 Hub ledger。

## 17. 关键风险与取舍

| 风险 | 影响 | 缓解 | 是否当前阶段接受 |
|---|---|---|---|
| 目录把一次错误永久化 | 高 | raw/revision/quarantine/Gate/replay | 必须控制后再上线 |
| 浏览器站点频繁变化 | 高 | HTTP 优先、selector 版本、失败隔离、人工补缺 | 仅作为 fallback |
| 数据许可和 ToS 不明确 | 高 | source manifest、审查、retention、撤回 | 未确认不采集 |
| 第二套数据库/账本 | 高 | Hub 单一 owner，插件只做薄桥 | 不接受 |
| 向量召回相关但不正确 | 中高 | 结构化/PIT/authority 先过滤，向量只是 derived | 后置 |
| 回填耗时和成本失控 | 中高 | source budget、分片、checkpoint、无默认 LLM | 可控后试点 |
| 转换器生成任意权限代码 | 高 | candidate、默认拒绝、SBOM、人工审核 | 不自动批准 |
| DSH 上游 developer preview 变化 | 中高 | upstream lock、公开 seam、契约测试 | 持续监控 |
| 迁移/ORM 漂移 | 中 | additive migration、升级测试、禁止 create_all | K0 必须修复 |

## 18. 待评审决策

以下项目需要 owner 在实现前明确：

1. 第一批回填的 Pack、来源清单和指标/事件优先级是什么？
2. raw blob 是存本地文件、对象存储还是 SQLite BLOB？保留期限和加密要求是什么？
3. 是否允许任何需要登录态的来源？若允许，哪些域、谁审批、凭据由哪个 secret manager 管理？
4. 90 天窗口按自然日、UTC 还是领域时区计算？历史 `as_of` 的默认语义是什么？
5. 首期只使用 SQLite FTS5，还是现有部署已经有可复用的 Postgres/对象存储？
6. 哪些来源的事实可以自动 promotion，哪些必须人工抽样或逐条审核？
7. `dsh-adapter-forge` 的第一目标是 Skill/MCP 迁移还是 SourceConnector 生成？
8. 何时允许把 `candidate` adapter 加入 Pack allowlist？审核人和 rollback owner 是谁？

## 19. 最终决策建议

建议批准以下最小范围：

```text
批准：
  Hub-owned persistent catalog
  官方 API/RSS/HTTP 的 90 天回填
  append-only raw/document/fact revision
  quarantine/promotion、PIT/as_of 和 FTS5/BM25
  DSH 薄插件的 backfill/search/status/approval 入口
  离线 candidate-only 的 dsh-adapter-forge 设计

暂缓：
  默认浏览器批量爬取
  LLM browser agent 作为主路径
  向量库替代事实账本
  任意 GitHub 项目一键生成生产插件
  把第三方项目自带 SQLite/memory/cache 当作 Hub 真源
```

这条路线能直接回应节省 token、降低检索延迟、改善时点一致性和减少幻觉的目标，同时保留现有 DSH/Hub 边界，不引入第二个 Agent Loop 或第二套业务账本。能否继续扩大采集范围，应由 K5 的真实 A/B 数据决定，而不是由项目 README、模型直觉或一次成功演示决定。

