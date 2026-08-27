# R1 实时事件引擎 Stage Charter

版本：`STAGE-R1-2026-08-27.v1`
状态：`done`（owner 已授权实现；离线退出门已通过）
前置：`R0-CORE-COMPLETE`，提交 `2ee2f8d`
适用范围：来源插件、实时事件触发、市场基准、Outcome 到期扫描、通知适配器。

## 1. 阶段目标

R1 的目标不是再写一套决策流程，而是把真实事件安全地送入已经通过 R0 验收的文本主链：

```text
SourceConnector
  -> TextEnvelope / Observation
  -> Admission + PIT Snapshot
  -> 现有 LangGraph research/decision
  -> Deterministic Gate
  -> Artifact / Forecast / Outbox
  -> Outcome 到期扫描与评估
```

阶段完成后，owner 可以在本机运行一个轻量 worker：来源插件按游标轮询或接收人工转写，重复事件不会重复分析；来源失败可观测且不会破坏已存在的账本；Forecast 到期后可以使用可替换的市场数据适配器生成 Outcome；通知通过 outbox 发送一次且失败不会重新触发分析。

R1 仍然不承诺盈利、实时交易或未授权数据的稳定性。真实来源只用于验证低延迟、PIT、去重和可回放边界。

## 2. 范围与非目标

### 2.1 本阶段交付

- `SourceConnector`/`SourcePlugin` 注册表：manifest、权限、游标、revision、去重、重试、退避和健康状态。
- 官方公开 Feed/Calendar 适配器：通用 RSS/Atom/JSON parser，加上 Fed/BLS/BEA 配置入口；测试使用固定 fixture，运行时可注入 HTTP client。
- 转写文本适配器：接受 `TextEnvelope` fragment/provisional/revision，不接管音频捕获和 ASR 模型。
- 市场数据 Port 和 OKX 公共行情适配器：首个可执行 bid/ask、1m VWAP fallback，缺失时显式 `estimated/unavailable`。
- 轻量 scheduler：单进程、可取消、基于 SQLite state 的重复安全 tick；不引入 Redis/Temporal/DBOS。
- Forecast 到期扫描：只读取已过期 Forecast，采集市场结果并调用现有 `OutcomeService`；不重复分析。
- 通知 adapter：保留现有事务 outbox，增加 email/桌面/local 抽象和有限重试；通知失败不改变 Artifact/Forecast。
- `/v1/sources`、`/v1/health` 的来源健康和手动 poll/ingest 只读/受限入口，供 Decision Desk 与未来 DSH/MCP 复用。
- 离线 BDD/TDD、固定来源 fixture、失败注入、PIT/revision/replay 和 worker E2E。

### 2.2 明确不做

- 不修改 R0 的 Event、Snapshot、Gate、Artifact、Forecast、Outcome 语义。
- 不让来源插件直接给出交易方向，不绕过 Admission/PIT/Gate。
- 不把金十、未授权网页抓取或搜索摘要作为唯一 canonical source；只提供显式配置和降级语义。
- 不实现音频采集、ASR 推理、OCR 或云端转码；只保留转写文本边界。
- 不自动下单、不保存交易所私钥、不接入真实交易执行。
- 不引入 DSH/Pi 作为正式自动链；DSH/MCP 只在后续 Workbench 阶段调用 R1 公共 Port。
- 不引入第二个账本、第二套 DTO、第二套重试/队列/调度框架或微服务拆分。

## 3. 公开契约

### 3.1 SourceConnector

每个来源插件必须实现稳定的 Port，而不是向 Core 暴露 SDK 类型：

```text
SourceManifest
  source_id, source_type, version, capabilities,
  authority_level, poll_interval_seconds, max_batch,
  allowed_domains, enabled

SourcePollResult
  source_id, cursor_before, cursor_after,
  envelopes[], fetched_at, next_poll_at,
  duplicate_count, revision_count

SourceHealth
  source_id, status, last_success_at, last_error_at,
  cursor, consecutive_failures, latency_ms, error_code
```

`envelopes[]` 只能是经过 Pydantic 校验的 `TextEnvelope`。`cursor_after` 只有在成功解析并完成 admission 后才提交；失败时保持旧游标。相同 `content_hash` 走现有 Admission 去重；同一事件的修订必须通过 `revision_of` 形成新 Observation，不覆盖旧文本。

### 3.2 MarketDataPort

```text
MarketQuote
  instrument, observed_at, received_at,
  bid, ask, last, volume, source_id,
  quality_status (observed|estimated|unavailable),
  benchmark (first_executable|vwap_1m|last)
```

Market adapter 只能返回事实快照，不能修改 Forecast 或 Gate。Outcome service 负责把事实映射为 `OutcomeCreate`；缺数据时写入 `quality_status=unavailable` 并拒绝伪造高置信收益。

### 3.3 NotificationPort

```text
NotificationMessage
  artifact_id, channel, dedupe_key, subject, body, created_at

NotificationResult
  delivered, retryable, error_code, provider_message_id?
```

消息只从已提交 outbox 读取。channel adapter 不重新调用分析，不写 Artifact/Forecast，不拥有 Gate 权限。

## 4. 组件与职责

```text
packages/source_adapters/
  registry.py              # manifest、状态、poll 编排
  official_feeds/          # RSS/Atom/JSON parser 与 Fed/BLS/BEA 配置
  transcript_meeting_copilot/ # fragment/revision TextEnvelope

packages/provider_adapters/
  market/                  # MarketDataPort、OKX public adapter、benchmark
  notifications/           # local/email adapter

packages/kernel/decision_hub_kernel/
  application/source_ingest.py   # admission + cursor commit 边界
  application/outcome_due.py     # 到期 Forecast 扫描
  ports/sources.py               # Source/Market/Notification Protocol
  persistence/db.py              # source state、health、market snapshot

apps/hub_worker/main.py     # scheduler tick、source poll、outbox/outcome drain
apps/hub_api/main.py        # source health、manual ingest/poll、产品 DTO
```

所有第三方 HTTP/XML/交易所类型都停留在 adapter；Kernel 只依赖 Protocol 和 canonical DTO。来源 worker 先执行 `poll -> validate -> admission -> cursor commit`，只有 admission 成功的 envelope 才能推进游标。

## 5. 任务卡与顺序

每张任务卡必须先写 Red 测试再实现，并单独形成可回滚 commit。

| Task ID | 目标 | 依赖 | 退出证据 |
|---|---|---|---|
| `R1-01` | Source manifest/registry、cursor、dedupe、revision、health | R0 contracts/DB | registry contract、失败不推进游标、revision replay、source health 测试 |
| `R1-02` | RSS/Atom/JSON 官方 Feed adapter 与 Fed/BLS/BEA 配置 | R1-01 | 固定 fixture 解析、PIT 时间、429/断网降级、HTTP 不进普通 CI |
| `R1-03` | Transcript fragment/provisional/revision adapter | R1-01 | 文本-only、revision 关联、重复片段和回放测试 |
| `R1-04` | MarketDataPort、OKX public adapter、执行基准 | R0 Outcome | bid/ask、VWAP fallback、unavailable 不伪造收益、固定行情 fixture |
| `R1-05` | scheduler、Forecast 到期扫描、失败聚合 | R1-01、R1-04 | tick 幂等、到期只处理一次、失败可恢复、不触发新分析 |
| `R1-06` | local/email notification adapter、outbox retry | R0 outbox | dedupe、有限重试、失败不改决策、不重复发送 |
| `R1-07` | API/Decision Desk health 与完整离线 E2E | R1-01..06 | source->run->forecast->outcome->notify 全链通过；文档/契约/类型/前端检查通过 |

## 6. BDD 验收场景

```text
Feature: 来源可插拔且重复安全
Scenario: 成功轮询推进游标
Given 一个已注册的官方 Feed 和 cursor=10
When adapter 返回 item=11、item=12 且两个文本均通过 TextEnvelope 校验
Then 系统为新内容执行 admission，并把 cursor 提交为 12
And 再次轮询不会创建第二个 Event 或 Run
```

```text
Feature: 来源失败不污染账本
Scenario: 429/断网保持旧游标
Given cursor=12 的来源
When HTTP 返回 429 或连接超时
Then SourceHealth 记录有限错误码和连续失败次数
And cursor 仍为 12，已有 Event/Run/Artifact 不被修改
```

```text
Feature: 修订可回放
Scenario: provisional transcript 被完整 revision 替换为新版本
Given 一个 revision_of=obs_old 的转写片段
When 来源提交更完整的文本
Then 系统创建新的 Observation/Event generation
And 旧文本仍可查询，PIT replay 不读取未来 revision
```

```text
Feature: Forecast 到期可评估
Scenario: 只处理已过期 Forecast
Given 一个已过期 Forecast 和固定市场 fixture
When scheduler 执行 outcome tick 两次
Then 只创建一个 Outcome/Evaluation
And 第二次 tick 不触发分析、不创建第二条 outbox
```

```text
Feature: 通知与决策解耦
Scenario: Email provider 失败
Given 一个已提交且未发送的 outbox row
When notification adapter 返回 retryable error
Then outbox attempts 增加并保留 pending 状态
And Artifact、Forecast、Gate 状态不改变，系统不重新运行 Agent
```

## 7. TDD、故障和安全矩阵

- 契约：manifest、TextEnvelope、MarketQuote、NotificationMessage 的 extra-forbid、版本和时间戳关系。
- Registry：未注册 source、禁用 source、非法 cursor、重复 item、admission 失败不推进 cursor。
- Feed：RSS/Atom/JSON 正常、空 feed、 malformed XML、429、5xx、超时、重试上限和退避。
- Transcript：fragment 顺序、revision、语言、时间戳、重复 hash、音频不进入账本。
- Market：bid/ask 缺失、VWAP fallback、过期 quote、跨资产 unavailable、未来时间戳拒绝。
- Scheduler：固定 clock、并发 tick、进程重启、同一 Forecast 幂等、source 失败聚合。
- Notification：local 成功、email mock 成功、429/5xx、永久失败、dedupe、secret redaction。
- E2E：source -> admission -> R0 graph -> Gate -> Forecast -> due outcome -> outbox；DSH 未启动时仍可完成。

普通 CI 只使用固定 fixture 和 mock transport：

```bash
./.venv/bin/pytest -m "not live" -q
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/docs/check_module_docs.py
```

真实 Feed/OKX/邮件只允许显式 `live` canary；密钥不写入代码、SQLite、日志、fixture 或前端。

## 8. 阶段退出门

R1 只有同时满足以下条件才可标记 `done`：

- 所有 R1-01 至 R1-07 任务卡均有独立提交和测试证据。
- 至少一个官方 Feed 配置、一个转写入口、一个市场适配器和一个通知适配器可在本机组合运行。
- 来源失败、游标回滚、重复/修订、PIT、Forecast 到期、通知重试均有自动化测试。
- DSH 未启动时自动来源到 Outcome/outbox 链仍可运行；DSH/MCP 只作为后续入口，不成为依赖。
- R0 全量测试、契约、pyright、ruff、前端构建/测试和文档检查继续通过。
- `docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`、受影响模块 README 和 `CHANGELOG.md` 已同步，准确区分 fixture 证据与真实网络效果。
- 不新增第二账本、第二 DTO、第二 Agent runtime 或未授权外部依赖。

## 9. Owner 决策边界

本 Stage Charter 已由 owner 确认并授权实现。若实现过程中需要修改 R0 契约、引入新的基础设施、依赖未授权数据或改变自动交易边界，必须停止当前任务，新增 ADR 并重新请求 owner 确认。
