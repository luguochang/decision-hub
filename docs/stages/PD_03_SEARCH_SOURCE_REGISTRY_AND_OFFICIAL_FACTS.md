# PD-03 Search、来源注册与官方事件事实

状态：`completed / engineering exit passed / official feed canary passed / market gaps remain`  
日期：2026-09-05（Asia/Shanghai）  
上级方案：[`PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md`](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)

## 1. 阶段目的

PD-02 已证明公开 Crypto/FRED adapter 能返回 typed Facts，也证明了两个不能隐藏的事实：

1. Search 返回的是 locator，不是金融事实；
2. `official.macro` 当前只返回正文 Evidence，没有 `event.identity` typed Facts，真实 canary 必然失败。

本阶段只建立下面一条可审计链：

```text
DSH native Search（发现）
  -> ResearchSourceRegistry（版本化准入）
  -> approved Fetch（正文与三时间戳）
  -> official parser（确定性字段提取）
  -> EvidenceCandidate + FactEnvelope
  -> 既有 Gateway / Durable Gateway / FactStore / Semantic Gate
```

它不扩展 DSH 的 Agent Loop，不新增 Supervisor，不建立第二套账本，也不让 LLM 直接写入事实。

## 2. 所有权和复用边界

| 能力 | 唯一所有者 | 本阶段做什么 |
|---|---|---|
| Search/Agent Loop/Session/Trajectory | DSH | 保留原生 `web_search`，结果只作 locator |
| 运行生命周期/checkpoint/deadline | LangGraph | 不修改 |
| Evidence/Fact/PIT/Gate/Ledger | Hub | 复用既有 Gateway、Store 和 Gate |
| 来源等级、允许用途、parser、license | Crypto Macro Pack | 新增版本化 `source_registry.yaml` |
| HTTP 与 provider 私有格式 | Provider adapter | 复用 `HttpDocumentResearchAdapter`，增加受控 parser hook |
| 事件字段和窗口语义 | Domain Pack | 继续使用 `event.identity` 真值表 |

`packages/source_adapters/registry.py` 仍是“可轮询 SourceConnector 注册表”；本阶段的
`ResearchSourceRegistry` 是 Domain Pack 的网页/Provider 来源策略。两者职责不同，禁止混合成
一个同时轮询、授权、抓取和写账本的对象。

## 3. 来源注册表

注册表位于 `packs/crypto_macro/evidence/source_registry.yaml`，由 Pydantic 在运行时 fail-closed
校验。每条来源至少声明：

- `source_ref`、`tier=P0..P4`、publisher、canonical domains/paths；
- authority、independence group、支持的 requirement；
- `search/fetch/evidence` 三项独立许可；
- parser、license、retention、audit status；
- 是否允许 redirect 到同一已批准来源。

准入规则：

- P0/P1/P2 也不是天然可信，只有 `license_status=approved`、`audit_status=approved` 且对应
  use flag 开启，才能 Fetch 或入 Evidence；
- P3 只允许 discovery；P4 和未知域名只能停留在 source candidate/DSH trajectory；
- 域名批准不等于所有路径批准；redirect 后的最终 URL 必须再次匹配；
- Search adapter 永远保留 `authority=search_derived`，即使命中 Fed 官方域名也不能自行晋级；
- 精确 market gap 仍只能由 typed provider facts 关闭。

## 4. 官方事件 parser

首批只支持已有真实入口：Federal Reserve speeches/press RSS 与对应官方讲话页面。parser 输出：

| field | metric family | unit | event offset | 来源 |
|---|---|---|---|---|
| `event_actor` | `event.identity` | `text` | `t0` | Feed 标题/官方页面元数据 |
| `event_time` | `event.identity` | `datetime` | `t0` | feed published/updated 或可信页面时间 |
| `revision_status` | `event.identity` | `text` | `t0` | `original` / `updated` |

三项 Fact 必须引用同一条已经返回的官方 Evidence，`source_id` 和
`independence_group` 来自注册表，`published_at <= observed_at <= received_at <= cutoff_at`。
无法确定 actor 或时间时返回明确的 `official_event_identity_incomplete`，不得让模型补字段。

`policy.delta` 不在本卡伪实现。它需要当前/前一文本或 actual/prior/consensus 的双版本语义，后续
必须复用相同 Registry/Fetch/Fact 边界另立 parser；本阶段不得把单篇正文冒充 delta。

## 5. BDD

```gherkin
Scenario: Search 命中未知域名
  Given DSH Search 返回未知网站 locator
  When 系统评估来源准入
  Then locator 保持 search_derived candidate
  And 不调用 approved Fetch
  And 不生成 FactEnvelope

Scenario: 官方 Feed 生成事件身份事实
  Given Fed speeches feed 在 registry 中为 P0 approved
  And feed 条目有 actor/title 与发布时间
  When official.macro 经 Gateway 执行
  Then 返回官方 Evidence
  And 返回 event_actor/event_time/revision_status 三项 typed Facts
  And event.identity Semantic Gate 通过

Scenario: Redirect 越过注册表
  Given 请求 URL 在 approved path
  But HTTP 最终 URL 跳转到未知域名或未批准路径
  When adapter 校验抓取结果
  Then fail closed 为 research_source_not_approved
  And 不产生 Evidence 或 Fact

Scenario: Search 摘要包含事件时间
  Given Search locator 摘要包含讲话人和时间
  But 没有 approved Fetch/official parser
  When 评估 event.identity
  Then requirement 保持 semantic_mismatch
```

## 6. TDD 与文件落点

| 测试/实现 | 责任 |
|---|---|
| `tests/research/test_source_registry.py` | schema、P0..P4、域名/路径/use/license/audit、unknown candidate |
| `tests/research/test_official_event_facts.py` | feed/page parser、三时间戳、Fact fields、redirect、缺字段 |
| `tests/research/test_capability_gateway.py`、`tests/research/test_durable_research_gateway.py` | Official adapter 在 Gateway/Durable Gateway 边界的集成回归 |
| `tests/contracts/test_pd02f_pack_profile_composition.py` | Pack、默认 allowlist 与 timeout composition 不变量 |
| `packages/provider_adapters/research/source_registry.py` | Pack 策略读取与 fail-closed resolution |
| `packages/provider_adapters/research/documents.py` | 单次 Fetch 与 parser hook，不写账本 |
| `packages/provider_adapters/official_sources/documents.py` | 官方 event identity parser |
| `packs/crypto_macro/evidence/source_registry.yaml` | 来源策略唯一事实源 |

测试顺序：先提交失败的 BDD 测试，再写最小实现；普通 pytest 禁止联网，真实网络只通过显式
canary。任何失败均记录 provider、错误码和语义范围，不把旧 fixture 改成看似通过。

## 7. 本阶段退出门

- [x] 注册表覆盖 P0..P4，非法、重复、越权和未知来源 fail-closed；
- [x] DSH Search locator 与 approved body/typed fact 可机器区分；
- [x] Fed official feed/page 能产生合法 `event.identity` typed Facts；
- [x] 真实 `official_feed` canary 返回 Evidence + Facts；
- [x] 未批准来源不能进入 hard Evidence；
- [x] Search 单独不能关闭 structured market 或 event identity gap；
- [x] Python、Ruff、Pyright、codegen、module docs、DSH/Desk 前端、diff check 全绿（以本轮最终命令证据为准）；
- [x] 实际 canary 的成功、失败、时效、成本和 semantic scope 写入执行日志。

工程退出门已通过，下一唯一工程入口为 PD-04。若 minute macro/expectation providers 仍未授权，产品继续明确显示
`research_only / provider_blocked`；PD-03 成功不等于 30 分钟宏观交易分析已经可交付。

## 8. 非目标与停止线

- 不实现新的通用爬虫、向量库、RAG 或浏览器自动化；
- 不把所有互联网域名默认批准；
- 不存储受限正文，只按 retention policy 保存允许的摘要/hash/ref；
- 不让 Search/LLM 决定 authority、license、revision 或 event time；
- 不修改 DSH 上游源码、LangGraph 生命周期或 Hub 发布 Gate；
- 不处理 ASR、PPT、第二领域、多用户或自动交易。
