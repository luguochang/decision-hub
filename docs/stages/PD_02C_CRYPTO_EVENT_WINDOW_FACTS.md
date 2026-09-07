# PD-02C Crypto Event-Window Facts

版本：`PD-02C-2026-09-04.v1`  
状态：`completed / continue PD-02D`  
父阶段：[PD-02 Typed Provider Pack](PD_02_TYPED_PROVIDER_PACK.md)  
执行记录：[PD 实施与自测日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)

## 1. 唯一目标

把 PD-01 已持久化但尚未接入产品 composition 的 EventWatch 窗口，转换成 DSH 可以通过 stable
`market.crypto_derivatives` capability 获取、Hub 可以校验并落账的 crypto spot/derivatives
`FactEnvelope`。对一个未来事件，至少能够基于真实捕获的 `t-5m` 与 `t+1m` 样本生成 BTC spot
`event_return` 和 OI `open_interest_delta`，且任何缺失 baseline、过期样本或 payload 篡改均
fail-closed。

本卡不补 `crowding_signal`、宏观分钟数据或 expectation pricing；它们仍属于 PD-02D/02E。

## 2. 已确认的根因

1. `EventWatchService` 已有八个 durable slot，但 `build_realtime_worker()` 没有注入
   `EventWindowSamplerPort`，生产 composition 不会捕获任何窗口。
2. `EventWindowSample` 只保存 opaque payload ref/hash，这是正确 Kernel 边界；当前却没有受校验的
   payload archive 和把样本映射为 Run-scoped Evidence/Fact 的 adapter。
3. canonical `ResearchCapabilityQuery` 已有 `event_id/event_at/window/requested_event_offsets`，但
   DSH 原生 `decision_hub_research` 和 MCP tool 尚未暴露这些字段，Agent 无法请求事件窗口。
4. `ResearchSessionRequest.event_id` 是可信 Run lineage，但模型工具参数尚未由 durable Gateway 与
   Run/EventWatch 对账。只让模型填写 event time 会留下跨事件取数和未来泄漏风险。
5. OKX/CoinEx 当前 adapter 主要返回 current snapshot；把 `event_offset` 硬填到这些结果上会伪造
   历史事实，明确禁止。

## 3. 固定数据流

```text
future official Event
  -> EventWatchService (T-30m ... T+72h durable slots)
  -> RealtimeScheduler + CryptoEventWindowSampler
       -> OKX/CoinEx thin adapters
       -> immutable content-addressed capture blob
       -> EventWindowCapture(ref + sha256 only)
  -> EventWindowSampleRecord (Hub index, no vendor payload)

DSH decision_hub_research
  -> DurableResearchCapabilityGateway
       validates Session -> Run -> event_id
       projects server-owned EventWatch event_at/window
  -> ProviderCapabilityRouter market.crypto_derivatives
       -> local event-window archive route for event-relative fields
       -> OKX/CoinEx routes for current snapshot fields
  -> EvidenceCandidate + FactEnvelope
  -> existing Evidence/Fact ledger + semantic Gate
```

DSH 仍是唯一 Agent Loop；LangGraph 仍只负责 Run/round/checkpoint；Hub 仍是唯一业务账本。窗口
capture blob 是 Provider payload store，不是第二账本：唯一索引和状态在 EventWatch 表，进入研究
结论的事实仍必须经过 Gateway、EvidenceService 和 FactStore。

## 4. 代码结构与所有权

| 位置 | 职责 | 禁止 |
|---|---|---|
| `packages/provider_adapters/market/event_window.py` | provider-neutral crypto capture payload、content-addressed archive、sampler、Run-scoped window fact adapter | 写 Run/Artifact/Gate、修改 watch 状态机 |
| `packages/kernel/.../event_watch.py` | 增加按 event 读取 sample 的公开端口实现 | 解析交易所 payload、计算金融指标 |
| `apps/hub_worker/composition.py` | `market_enabled` 时注入 sampler | 自建 scheduler 或后台线程 |
| `apps/research_mcp/main.py` | 注册 local archive route adapter，共享 Hub DB/data dir | 读取模型 Prompt、复制 Gate |
| `packages/workbench_adapters/research_mcp.py` | 暴露 canonical event/window tool 参数 | 推断或伪造 event time |
| `extensions/dsh/decision-hub/src/research-tool.ts` | 复用 codegen Zod 校验并透传 event/window 参数 | 手写第二份 DTO、接受 session identity |
| `DurableResearchCapabilityGateway` | 将 model event_id 与 Run 对账，并从 EventWatch 投影可信时间边界 | 让模型延长 cutoff 或跨事件读取 |
| `packs/crypto_macro` | 声明 local archive route、canonical 字段与 offsets | 写 Provider HTTP 或计算逻辑 |

## 5. Capture blob 与计算规则

同一模块内使用 Pydantic 严格模型 `crypto-event-window-payload.v1`：

```text
event_id / offset / target_at / captured_at
observations[]:
  provider_id / source_id / venue / metric_family / field / value / unit
  source_url / published_at / provider_field
failures[]:
  provider_id / error_code / retryable
```

- 文件名为 canonical JSON 的 SHA-256，引用固定为 `crypto-window://<sha256>`；load 时同时校验 ref、
  文件名、内容 hash 和 `EventWindowSample.payload_hash`。
- 写入采用同目录临时文件 + atomic replace；相同 hash 幂等，不维护第二份可变索引。
- sampler 对 OKX/CoinEx 独立捕获；单个 venue 失败保留 failure，至少一个 venue 成功才提交 slot；
  全部失败由 PD-01 保留明确 error code。
- spot `event_return = (post_price / baseline_price - 1) * 100`，单位 `percent`。
- `open_interest_delta` 使用同 venue、同 instrument 的 baseline/post OI，公式同上；baseline 为 0、
  单位/venue/instrument 不同或任一 offset 缺失时不生成 delta。
- 原始 price/volume/funding/OI/basis facts 带各自真实 offset；derived fact 的 window 固定为 baseline
  target 至 post target。禁止使用 current snapshot 回填缺失 offset。

## 6. 路由与兼容

- Pack 在 OKX/CoinEx 前增加 `event-window-archive` primary route；它只在 query 有可信 `event_id`
  且请求 event offsets/derived fields 时参与，不能拦截普通 current snapshot query。
- stable capability 对外使用 canonical 字段 `price/volume/event_return/funding_rate/open_interest/
  open_interest_delta/mark_price/index_price/basis`；adapter 可以在内部兼容旧
  `spot_price/spot_volume` alias，但供应商私有名不得继续出现在新 Prompt。
- 已存在的 query/result 仍 additive 兼容；不改历史 Event、Sample、Evidence 或 Fact。
- DSH tool 增加的 event/window 参数全部来自 canonical codegen schema；Session identity 仍由 Host
  注入，模型无权传入。

## 7. BDD/TDD

```gherkin
Scenario: future event captures two venues exactly once
  Given a future EventWatch and injected OKX/CoinEx fixtures
  When scheduler reaches t-5m and t+1m across a process restart
  Then each slot has one immutable capture ref/hash
  And duplicate tick does not call providers again

Scenario: DSH obtains real event-relative spot facts
  Given the event has captured t-5m and t+1m payloads
  When the linked DSH Session requests price/volume/event_return
  Then Gateway binds query.event_id to the linked Run
  And output contains per-venue offset facts and derived event_return
  And semantic Gate may count only those validated facts

Scenario: model cannot read another event
  Given DSH Session belongs to event A
  When tool query declares event B or conflicting event_at
  Then execution fails with research_event_lineage_mismatch
  And no provider or archive read occurs

Scenario: missing baseline stays explicit
  Given only t+1m was captured
  When DSH requests t-5m and t+1m
  Then current quote is not substituted
  And no event_return/open_interest_delta is emitted
  And Gate remains no_baseline/window_missing

Scenario: capture blob tampering fails closed
  Given DB payload_hash differs from archive content
  When window adapter loads the sample
  Then capability fails with provider_window_payload_hash_mismatch
  And no Fact enters the Hub ledger
```

测试必须覆盖 contract/MCP/DSH plugin、archive、sampler、Router/Gateway、FactStore/Sufficiency、worker
composition、重启与重复 tick。普通 pytest 全部用 fake fetcher 和临时目录，不触网。

## 8. Checklist 与退出门

- [x] `C1`：补 event/window tool 参数和 Run/EventWatch server-owned lineage；
- [x] `C2`：实现 content-addressed payload archive 与篡改/幂等测试；
- [x] `C3`：实现 OKX/CoinEx 多 venue sampler 并接入 realtime worker；
- [x] `C4`：实现 archive -> Evidence/Fact adapter 与 price/OI delta；
- [x] `C5`：接入 Pack stable route，更新 DSH canonical playbook；
- [x] `C6`：完成 adapter -> Router -> Gateway -> FactStore -> semantic Gate 全链 replay；
- [x] `C7`：同步 README/状态/执行日志并运行全量 Python/TS/静态/契约/文档门。

工程退出门：离线 fixture 已证明未来事件的两个 capture slot 可跨重启恢复，并生成有
hash/lineage 的窗口事实；缺失与篡改不能被 current snapshot 或模型填补；Provider 变更不修改
Core/Graph/UI。真实 OKX/CoinEx 可用性仍需单独 live canary，不由本卡的 replay 证据替代。

产品边界：本卡完成后 `crypto_spot_confirmation` 可以具备窗口事实，但 derivatives 仍缺
`crowding_signal`，macro/expectation 仍缺正式分钟级数据。因此只能
`continue PD-02D / research_only`，不得宣称 PD-02、事实充分度或交易产品已完成。

## 9. 完成证据（2026-09-04）

- DSH/MCP 工具已透传事件字段；Durable Gateway 将 `event_id` 与 Run 对账，并从 EventWatch
  投影可信 `event_at/window_start_at/window_end_at`，冲突、未知事件和非法 offset 在 Provider
  调用前 fail-closed。
- EventWindow archive 使用 canonical JSON + SHA-256 content address + atomic replace；sampler
  复用既有 OKX/CoinEx typed adapter，单 provider 失败保留 failure，全部失败不提交 slot。
- Router 已按查询是否包含事件窗口严格分流：普通查询排除 archive，事件查询只走 archive，archive
  非可重试失败不能退回 current snapshot。
- 端到端离线回放已验证 `EventWatch -> archive -> stable Router -> Durable Gateway ->
  Evidence/FactStore -> semantic Gate`，并覆盖 `event_return`；新增 lineage/time-conflict/
  missing-baseline/hash-tamper 回归。

质量门：

```text
.venv/bin/pytest -q                                      503 passed
pnpm --dir extensions/dsh/decision-hub test               60 passed
pnpm --dir extensions/dsh/decision-hub build              passed
.venv/bin/pyright packages apps tests                     0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools             passed
.venv/bin/python -m tools.contract_codegen check           canonical schemas: ok
.venv/bin/python tools/docs/check_module_docs.py           module docs: ok (13 modules)
git diff --check                                           passed
```

上述证据全部使用离线 fixture/临时目录；没有证明分钟级宏观数据、expectation pricing、
`crowding_signal`、真实 Provider 长期稳定性、预测准确率、盈利或自动交易。
