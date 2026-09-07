# PD-02E Intraday Macro / Expectation Pricing

版本：`PD-02E-2026-09-04.v1`  
状态：`completed / engineering exit passed / live provider blocked`  
上游阶段：`PD-00..02D completed`  
下游入口：`PD-02F Pack/Runtime capability closure`

## 1. 本卡解决什么

当前系统已经有 DSH 原生 Search、三轮补证、typed crypto event-window facts 和语义 Gate，
但 `macro_transmission` 与 `expectation_pricing` 仍不能被真实关闭。根因不是 Agent Loop 缺失，
而是缺少具备事件相对时间、数据授权和明确语义的宏观事实。

本卡只完成两项稳定能力的工程闭环：

```text
macro.cross_asset_intraday
  -> rates + USD
  -> level + event_return
  -> t-5m + t+1m

macro.expectation_pricing
  -> policy expectation
  -> level + delta
  -> t-5m + t+1m
```

这两个 capability 保持 provider-neutral；替换数据供应商只修改 Domain Pack route 和 adapter
registration，不修改 DSH、LangGraph、Hub Core、FactEnvelope、Gate 或前端。

## 2. 不解决什么

- 不新增第二套 Agent Loop、Supervisor、MCP 工具或插件体系；DSH 仍是唯一 Harness。
- 不让 LangGraph 做开放式搜索或模型调度；它仍只负责 durable Run 生命周期。
- 不新增行情账本；事实继续进入 Hub 的 `Evidence/Fact/PIT` 单一账本。
- 不把 FRED 日频、WebSearch 摘要、BTC 衍生品或 current snapshot 当作分钟级宏观事实。
- 不把未授权、未 canary 的免费代理标成 `licensed_live/realtime`。
- 不在本卡承诺真实 Provider 稳定性、预测准确率、收益或交易可用性。
- 不把供应商密钥、私有响应或完整原始 payload 写入 Trace、Evidence、文档或仓库。

## 3. 固定架构边界

```text
DSH Supervisor / Agent Loop
  -> research_capability_execute（唯一稳定 MCP 工具）
  -> ResearchCapabilityGateway（权限、域名、预算、PIT）
  -> ProviderCapabilityRouter（approved route、字段、窗口、fallback）
  -> IntradayMacro / ExpectationPricing adapter
  -> EvidenceCandidate + canonical FactEnvelope
  -> DurableResearchGateway
  -> Hub Evidence/Fact ledger
  -> crypto_macro Domain Semantic Gate
```

职责分配：

| 层 | 本卡职责 | 禁止事项 |
|---|---|---|
| DSH | 根据 requirement/gap 调用稳定 capability | 直接裁决发布、另存业务账本 |
| LangGraph | Run、deadline、checkpoint、恢复、确定性 continuation | 自建搜索循环或 Provider DTO |
| Hub | capability 审计、PIT、Evidence/Fact 持久化、Gate | 理解供应商私有 payload |
| Domain Pack | capability、route、字段、单位、offset、delay/license 规则 | 在 Python 中复制一套规则 |
| Adapter | 请求供应商、解析 payload、映射 canonical Facts | 放宽 Gate、伪造 offset 或授权 |

## 4. 当前 Red 基线

1. `macro.cross_asset_intraday` 在 Pack 中标为 approved，但 capability 和 routes 的
   `allowed_domains` 均为空。真实 Gateway 会先报 `research_broad_permission_required`，与文档声称的
   `provider_unconfigured` 不一致。
2. Intraday adapter 只选择 cutoff 前最新 point，丢失 `event_offset`，不能证明
   `t-5m/t+1m`，也不能关闭 `level + event_return`。
3. Intraday route 未声明 `requires_event_window`，Router 会从事件相对查询中排除该 route。
4. Expectation adapter 能保留 offset，但生产 capability/route 未获 license/audit；没有可声明为
   `licensed_live` 的 endpoint。
5. 当前只有 adapter 单测，没有执行
   `adapter -> Router -> Gateway -> FactStore -> Semantic Gate` 的 macro/expectation 行为链。
6. delayed/free proxy、缺 baseline、错误指标族和正确 licensed fixture 的 Gate 行为尚未锁定。
7. `macro_transmission/cross_asset_confirmation` 请求 `t-5m/t+1m`，但 freshness 只有 300 秒；
   合法基线到最早可判定时已相隔 360 秒，会被错误标为 stale。

进入本卡前的可信基线：

```text
.venv/bin/pytest -q                                  511 passed
pnpm --dir apps/decision-desk test                    10 passed
pnpm --dir apps/decision-desk build                   passed（仅 chunk size warning）
pnpm --dir extensions/dsh/decision-hub test           60 passed
pnpm --dir extensions/dsh/decision-hub build          passed
```

## 5. 具体实现

### E1. Pack 审计状态根因修正

- 在未选定真实供应商前，把 `macro.cross_asset_intraday` capability 和 route 改为
  `review_required/candidate`，与 expectation pricing 保持同一 fail-closed 原则。
- 两类事件事实 route 均声明 `requires_event_window: true`。
- 不用 `search:broad` 绕过空域名，也不随意加入假域名。
- 未来批准真实供应商时，必须在 Pack 中同时写入固定 `allowed_domains`、`service_tier`、
  `cost_policy_ref`、license/audit；环境变量只保存 endpoint/secret 值，不拥有授权语义。

### E2. Intraday event-relative mapping

- Provider-neutral observation 支持 `timestamp/observed_at/time`、`event_offset`、`values`。
- 事件查询只选择明确命中的 requested offsets；缺 offset 返回已有事实，由 Gate 产生
  `no_baseline/window_missing`，不得回退为 latest point。
- 普通非事件查询仍只返回 cutoff 前 latest point，保持兼容。
- Fact 必须保留 `event_offset`，`macro.rates` 与 `macro.usd` 的 unit 分别为：
  `yield_percent/bps` 与 `index/percent`。
- 两个 requirement 的 freshness 调整为严格的 600 秒事件信封，只覆盖合法
  `t-5m -> t+1m` 比较；无 offset 的 current snapshot 仍无法通过。
- 可选的 `source_id`、`independence_group`、`source_url` 只能来自已审计 provider payload；
  未提供时回退到 adapter provider identity，不能按 symbol 人为伪造独立来源。

### E3. Expectation mapping hardening

- 保留现有 offset 选择；事件查询缺某个 offset 时不补造。
- `level` 只允许 probability/percent 语义，`delta` 只允许 percentage point/bps 语义；
  最终由 Domain Gate 再校验。
- route 强制事件窗口；未获审批时 Gateway 必须在调用 adapter 前拒绝。

### E4. 完整行为回放

新增单一全链测试，真实执行：

```text
EventWatch
  -> Router
  -> ResearchCapabilityGateway
  -> DurableResearchCapabilityGateway
  -> EvidenceService / FactStore
  -> assess_evidence_sufficiency
```

测试只使用进程内 fake fetcher 和临时 SQLite，不联网、不读取 secret。测试 manifest 显式批准
fixture route，仅用于证明工程行为；不得修改生产 Pack 的 candidate 状态。

### E5. 模块文档和状态真源

- 更新 `packages/provider_adapters/README.md`、`apps/research_mcp/README.md` 和相关模块 README。
- 在本卡结束时同步 `INDEX.md`、`CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、
  `IMPLEMENTATION_STATUS.md`、`ROADMAP.md` 和 PD 执行日志。
- 历史测试数字保留为历史时点；只在本卡末尾追加本轮真实证据。

## 6. BDD 验收场景

### BDD-E1：生产 route 未审批时 fail-closed

```gherkin
Given 宏观 capability 没有已审批供应商和固定 allowlist
When DSH 请求事件相对宏观或预期定价事实
Then Gateway 在调用 adapter 前返回 research_capability_not_audited
And 不触网、不生成 Evidence/Fact、不显示为 provider_unconfigured 假象
```

### BDD-E2：delayed proxy 不可关闭 realtime requirement

```gherkin
Given adapter 返回字段和 offsets 完整但 delay_class=delayed
When Fact 进入 macro_transmission 或 expectation_pricing Gate
Then requirement 保持 semantic_mismatch/research_only
And 不因来源数量或内容完整而提升为 sufficient
```

### BDD-E3：licensed realtime fixture 可以关闭正确 requirement

```gherkin
Given 已审批 fixture route 返回 PIT 合法、realtime、字段/单位/offset 正确的 Facts
When 完整链持久化并运行 Domain Gate
Then expectation_pricing 为 sufficient
And rates + USD 具有两个真实 provenance groups 时 macro_transmission 为 sufficient
```

### BDD-E4：缺 baseline 不回退 current snapshot

```gherkin
Given provider 只有 t+1m 或无 event_offset 的 current point
When requirement 请求 t-5m 与 t+1m
Then Gate 返回 no_baseline
And Router 不选择 requires_event_window=false 的 current route
```

### BDD-E5：语义替代必须失败

```gherkin
Given BTC funding/OI/basis Facts 被提交给 expectation_pricing
When Domain Gate 校验 metric family、field、unit、offset、delay class
Then 返回 semantic_mismatch
And 该 Evidence 不计入 expectation pricing coverage
```

## 7. TDD 文件和断言

| 测试 | 必须锁定的行为 |
|---|---|
| `tests/research/test_macro_typed_adapters.py` | offset 选择、PIT、numeric validation、缺 endpoint、provenance |
| `tests/research/test_macro_event_window_full_chain.py` | 两类 capability 的完整持久化与 Gate 行为 |
| `tests/research/test_provider_router.py` | event query 只选 window route、candidate route 不执行 |
| `tests/research/test_capability_gateway.py` | production manifest 未审批时在 adapter 前拒绝 |
| `tests/kernel/test_sufficiency.py` | delayed、missing offset、wrong metric family 均 fail-closed |
| `tests/contracts/test_agentic_research_contract.py` | Pack capability/route schema 和状态一致 |

禁止只断言“有 Evidence”或读取自证字符串 `status=sufficient`。退出证据必须来自实际对象通过
Router、Gateway、FactStore 和 Gate。

## 8. 退出门

- [ ] `macro.cross_asset_intraday` 不再存在 approved + empty domains 的矛盾。
- [ ] 两类生产 capability 无供应商审批时均在 adapter 前 fail-closed。
- [ ] Intraday Facts 保留请求的 event offsets 和可审计 provenance。
- [ ] expectation Facts 保留 offsets，缺 baseline 不补造。
- [ ] delayed/free fixture 不能关闭 realtime hard requirement。
- [ ] 正确 realtime fixture 通过完整持久化/Gate 行为链。
- [ ] BTC derivatives 替代 expectation pricing 的测试失败。
- [ ] 全量 Python、两个前端 test/build、Ruff、Pyright、codegen、module docs、diff check 通过。
- [ ] 文档明确真实 macro/expectation Provider canary 仍 blocked，除非有真实授权与 endpoint。

## 9. 阻断和决策边界

工程闭环可以在本卡内完成；以下事项不能由代码擅自决定：

| 决策 | 当前状态 | 需要的 owner 输入 |
|---|---|---|
| 分钟级 rates/USD 正式供应商 | blocked | 候选供应商、授权、费用、域名、endpoint/key |
| Fed funds/SOFR/OIS expectation pricing | blocked | 候选供应商、授权、字段定义、费用、endpoint/key |
| 免费 proxy 是否用于背景研究 | 可保留 candidate | 只能显示 degraded/research_only，不能关闭 hard Gate |

因此本卡有两个独立结论：

```text
engineering_exit = pass/fail
live_provider_exit = pass/blocked/fail
```

只有 `engineering_exit=pass` 可以进入 `PD-02F`；只有真实 canary 通过后，产品 readiness 才能把
对应 requirement 从 `provider_blocked` 改为 `ready`。两者不得合并成一个好看的“已完成”。

## 10. 本卡完成后的唯一下一步

`PD-02F` 只核验 `PD-02C..02E` 的 capability 是否全部进入 Pack/profile/composition、DSH tool
allowlist、readiness 和可观测投影；不新增新 Provider，不开始 PD-07 价值观察。若真实 macro/
expectation Provider 仍未选择，产品继续明确显示 `research_only/provider_blocked`。
