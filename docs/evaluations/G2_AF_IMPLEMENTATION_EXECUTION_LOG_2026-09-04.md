# G2-AF 实施执行记录（2026-09-04）

状态：`G2-AF-01..04 technical gates passed / G2-AF-05 prospective observation pending`  
目标：在不复制 DSH Agent Loop、账本或搜索引擎的前提下，补齐“hard gap 出现后主动补证”的产品闭环。

## 授权与边界

- Owner 已授权按 G2-AF-01..05 目标执行。
- DSH 仍是主要 Harness/runtime；LangGraph 只编排产品生命周期；Hub Gateway 是 Evidence 唯一准入边界。
- Provider 顺序固定为：DSH 原生 `web_search` discovery primary；Tavily 按需 fallback/独立交叉索引。
- Search 结果只能先产生 `search_derived` candidate，必须经过 `web.fetch` 或 typed Official/Market provider 才能满足金融 hard requirement。
- 不自动交易、不修改 Fixed active/DSH candidate-shadow、不改写历史账本、不把密钥写进仓库。
- 聊天中暴露的 Tavily key 未读取、未持久化、未联网调用；正式使用前必须轮换，新 key 只允许进入 gitignored secret store。

## G2-AF-01 首批实现

### 任务

- [x] Capability Catalog：只返回 `enabled/shadow` 且不超过运行成本上限的 capability。
- [x] Capability ladder：按 preferred/fallback 顺序去重，跳过已尝试 capability。
- [x] LangGraph state：记录 `attempted_capabilities_by_requirement` 与 `retryable_failures`。
- [x] Gap-driven continuation：无新增 Evidence 时，只要仍有未尝试 fallback 或可重试失败且预算未耗尽，继续下一轮。
- [x] Round ladder：下一轮动态移除已经尝试的 capability，使 DSH 真实选择 fallback，而不是重复 primary。

### 代码落点

- `packages/kernel/decision_hub_kernel/application/research_planning.py`
- `packages/orchestration/langgraph/state/research.py`
- `packages/orchestration/langgraph/graphs/agentic_research_graph.py`
- `tests/orchestration/test_g2af_continuation.py`

## G2-AF-02 provider adapter 首批实现

- [x] `DshNativeWebSearchTransport`：DeepSeek Anthropic-compatible native route 的结构化结果映射。
- [x] `TavilySearchTransport`：Tavily Search API 的只读 discovery 映射，成本通过显式估算策略记录，不静默写 0。
- [x] 两个 transport 都复用现有 `SearchCapabilityPort` -> `WebSearchResearchAdapter` -> `ResearchCapabilityGatewayService`。
- [x] `web.search.tavily` 已加入 `crypto_macro` Capability Catalog；没有显式 enable 时仍 deny-by-default。
- [x] `apps/research_mcp` 根据 allowlist 选择 DSH native primary 或 Tavily fallback；未启用 Tavily 时不会读取其 secret。

## TDD/BDD 证据

离线 Red tests 先行覆盖：

- capability 状态、成本门和 fallback 顺序；
- primary 已尝试后选择 Tavily fallback；
- 无新 Evidence 但存在未尝试能力时继续；
- 下一轮 requirement ladder 移除已尝试 primary；
- Tavily 结构化结果、时间戳、hash 和非零成本；
- DSH native `web_search_tool_result` 结构化结果映射。

本轮专项结果：

```text
.venv/bin/pytest -q \
  tests/contracts/test_agentic_research_contract.py \
  tests/capabilities/test_search_capability.py \
  tests/orchestration/test_g2af_continuation.py \
  tests/orchestration/test_agentic_research_graph.py \
  tests/research/test_capability_gateway.py \
  tests/runtime/test_dsh_research_runtime.py
37 passed（契约/能力/计划专项）
111 passed（图、Gateway、Runtime 联合）
全量 Python：404 passed

.venv/bin/ruff check <本轮改动文件>
All checks passed

.venv/bin/pyright <本轮改动文件>
0 errors / 0 warnings
```

## 未宣称完成的门

- [x] DSH native Search 真实结果进入 Hub attestation/Evidence Ledger 的隔离 canary。
- [ ] Tavily fallback 真实只读 Search -> Fetch -> Gateway canary。由于旧 key 已暴露，需 owner 轮换后再执行。
- [x] 自动日历/feed admission -> durable Run -> 报告/通知/复查的整链路。
- [x] 同一 DSH Session 的真实 gap continuation、结构化 synthesis attestation 和有界停止。
- [ ] 14 天或 20 个高影响事件 prospective observation。

## 2026-09-04 配置根因修复（G2-AF-02 前置）

此前官方 DSH base bundle 已挂载 `web_search`，但 `decision-research` preset 将 `tool-web.fetch`
关闭，且研究提示词把“hard evidence unavailable”写成了立即停止；这会让产品看起来只有问答，
也会在发现缺口后过早输出 `no_trade`。本轮修复如下：

- `infra/dsh/presets/decision-research/agent.cordis.yml` 显式覆盖官方 `@deepseek-ai/dsh-tool-web`，开启 `web_search` 与 `web_fetch`，保留官方 DSH 的 provider、timeout policy、Trajectory 和 JSONL。
- `packages/runtime_adapters/dsh_runtime/profile.py` 改为要求：hard gap 时先用 DSH 原生 `web_search` 发现 locator，再用 Hub `decision_hub_research` 的 `web.fetch`/typed provider 验证；只有 ladder、轮次、工具、deadline、权限或成本预算耗尽才 bounded stop。
- `web.fetch` 绑定改为受审计的 `HttpDocumentResearchAdapter`，只允许金融官方机构/交易所域名；Search locator 仍是 `search_derived`，不能跳过 Fetch/typed provider。
- 泛网页 Fetch 的 `source_id` 改为发布者域名，避免不同独立来源被错误合并为一个 `web-fetch` 来源，从而错误触发独立来源不足。
- `run-product.sh` 默认将 `web.fetch` 加入 live allowlist；Tavily 仍不默认读取或调用。

这组修改只修配置/提示词/适配器边界，没有新增第二套 Agent Loop、第二本账本或前端协议。
离线回归新增了 DSH preset 启用检查、Fetch publisher identity 和“搜索后继续补证”提示词断言。

## 2026-09-04 DSH native provider probe

在已有 gitignored `data/dsh-live/.env` 的 DeepSeek 凭据下执行了一次只读、域名限定为
`federalreserve.gov` 的 provider probe。为诊断本机代理 CA，仅本次命令显式使用
`DECISION_HUB_DSH_SEARCH_VERIFY_TLS=0`；生产默认仍为 TLS 校验开启，不能复制该诊断设置。

```text
status: passed (provider adapter)
provider: dsh-native-web-search
model: deepseek-v4-flash
route: https://api.deepseek.com/anthropic/v1/messages
tool_type: web_search_20260209
source_count: 3
sources:
  https://www.federalreserve.gov/newsevents/speech/barr20260901a.htm
  https://www.federalreserve.gov/newsevents/speech/warsh20260828a.htm
  https://www.federalreserve.gov/newsevents/speech/waller20260130a.htm?... (tracking query retained by provider)
estimated_cost_usd: 0.01
```

此前以旧工具类型 `web_search` 得到 HTTP 400；DeepSeek 明确要求
`web_search_20250305` 或 `web_search_20260209`。adapter 已修正为 `web_search_20260209`，
并新增回归测试。此次调用没有创建 Run、没有写 Evidence Ledger、没有写 JSONL/Trace，也没有调用
Tavily；因此只证明 native route 和结构化 locator 可达，不证明 DSH Session attestation、Fetch
原文、PIT/authority Gate 或报告闭环。

## 2026-09-04 真实 DSH Web 闭环执行

本节记录本轮已完成的真实、只读、隔离 Compose 运行。运行没有改写历史 Run，也没有切换
Fixed active/DSH candidate-shadow。

### Run `run_7fa4d33b35c44ddeb8ce1cfa8cf13904`：旧能力配置失败样本

- DSH 原生 `web_search` 连续执行，随后 Hub `web.fetch` 多次尝试；这证明 DSH 没有在一次搜索
  后立即停止。
- 多个 locator 不在受审计 allowlist，`federalreserve.gov` 也出现 HTTP 失败；最终没有
  accepted Evidence，Run 以 `structured_output_invalid`/失败终态结束。
- 失败 provenance 保留为 `search_provider_failed`（`cause_code=httpstatuserror`）和
  `research_domain_denied`，该 Run 作为回归失败样本，不改写成成功。

### Run `run_703028dd36924c6ebe5a008b95740d35`：typed capability 配置

- 允许能力为 `official.macro`、`market.cross_asset`、`market.crypto_derivatives` 和
  `web.fetch`；同一 DSH Session 完成第二轮补证。
- 共保留 12 条 Evidence，hard coverage `66.7%`；官方宏观、跨资产和衍生品能力成功，
  FRED 日频数据相对 5 分钟 freshness 要求被正确标记为 stale。
- `expectation_pricing`、`macro_transmission` 仍未闭合；工具预算耗尽后终态为
  `research_only`，`gate_status=research_only`，`stop_reason=tool_budget`。这不是方向性交易
  结论，也不能称为预测成功。
- 关键证据：继续轮次、accepted Evidence、stale 标记、剩余 hard gap 和预算停止原因均在
  Run/Trace/Report 中可复核。

### Run `run_e6812b573ca4448691109541cda2c737`：重复暴露 Hub `web.search` 的实验

- 在上一配置基础上额外暴露 Hub `web.search`，DSH 原生 `web_search` 本身成功，但模型又
  通过 Hub capability 重复调用同一类 native Search route。
- 重复调用造成多次 `research_capability_timeout`，最终仍为
  `research_only/tool_budget`，共保留 12 条 Evidence，hard coverage `66.7%`。
- 结论：DSH 原生 `web_search` 与 Hub `web.search` 不能默认同时暴露；Hub `web.search` 不再
  进入默认 allowlist。Tavily 仅作为显式 `web.search.tavily` fallback，必须单独执行 canary。

### 本轮代码修复与自测

- `packages/provider_adapters/research/documents.py` 统一 HTTP 404/401/403/429/5xx、timeout
  和 connection error 的稳定错误码、`origin`、`cause_code` 与 retryability；注入式 fake
  fetcher 与默认 HTTP fetcher 使用同一分类。
- `packages/kernel/decision_hub_kernel/application/research_evidence.py` 为 capability
  deadline、请求域名拒绝和 target URL 拒绝补充稳定 provenance code。
- 泛网页 Fetch 使用发布者域名作为 `source_id`，避免不同独立来源被错误合并。
- 新增/更新文档适配器、DSH native/Tavily adapter 和 gap continuation 回归测试；没有读取或
  持久化聊天中暴露的 Tavily key。

### 2026-09-04 Official/Market capability canary 复跑

在不启用 Tavily、不写 durable Run/Evidence Ledger 的只读 canary 中，四个已准入 typed
capability 均返回结构化结果：

| case | provider | Evidence | latency | 关键限制 |
|---|---|---:|---:|---|
| `official_feed` | `official-macro-document` | 1 | 1016 ms | Fed 官方来源，可作事件 identity |
| `cross_asset` | `fred-public` | 3 | 1765 ms | 最大年龄约 587445 秒（约 6.8 天），5 分钟 freshness Gate 仍判 stale |
| `crypto_spot` | `coinex-public` | 1 | 982 ms | 交易所公开 BTC 现货/成交量 |
| `crypto_derivatives` | `coinex-public` | 1 | 3216 ms | 交易所公开 funding/OI/mark/index/basis |

canary 输出 `research-fact-capability-canary.v1/status=passed`。该结果只证明 provider adapter
可达、schema 可解析、来源 authority 可识别；FRED 日频数据不能满足分钟级事件窗口，不能被
报告当成实时利率确认。它也不替代 DSH Session attestation、Search -> Fetch 归因或 E3 价值观察。

本轮质量门结果（2026-09-04，当前工作树）：

```text
.venv/bin/pytest -q                                      416 passed
.venv/bin/ruff check packages apps tests tools             passed
.venv/bin/pyright packages apps tests tools/canary         0 errors / 0 warnings
.venv/bin/python -m tools.contract_codegen check           passed
.venv/bin/python tools/docs/check_module_docs.py           passed (13 modules)
pnpm --dir extensions/dsh/decision-hub test -- --run       57 passed
pnpm --dir extensions/dsh/decision-hub build               passed
pnpm --dir apps/decision-desk test -- --run                 10 passed
pnpm --dir apps/decision-desk build                         passed
git diff --check                                           passed
```

### 阶段门结论与下一张任务卡

- [x] G2-AF-01 Capability Catalog、fallback ladder、预算/超时/权限/失败码契约及离线回归。
- [x] G2-AF-02 provider adapter、DSH preset、原生 Search locator -> Hub attestation -> Ledger canary。
- [x] G2-AF-03 同一 DSH Session 三轮 gap continuation、部分失败保留与 synthesis attestation。
- [x] G2-AF-04 日历/feed admission -> durable Run -> 报告/通知/复查整链路。
- [ ] G2-AF-05 至少 14 天或 20 个高影响事件 prospective observation。

下一步不是继续堆功能，而是 **G2-AF-05 前瞻观察**。轮换 Tavily secret 后可执行独立 fallback
canary，但它不阻塞 DSH native 技术链试点，也不能绕过 Search candidate -> Fetch/typed provider
-> Gateway 的 Evidence 边界。

## 当前诚实结论

G2-AF-01..04 的代码、离线门、真实官方 DSH Web、自动事件、Evidence、Artifact、Outbox、通知、
复查和前端投影已经通过；产品可进入单 owner `research_only` 技术试点。真实运行仍有
`macro_transmission` stale、第三方 locator 不在 allowlist、模型费用未归集到 Hub 等限制，
因此不能称为实时金融决策、预测准确或盈利系统。Tavily adapter 已接入公开端口但未读取或调用
已暴露的旧 key；正式 fallback canary 仍要求轮换 secret。

## 2026-09-04 同 Session continuation 与 synthesis 根因记录

以下三个真实 Run 固定保留为回归样本，不把失败改写成成功：

| Run | 已证明事实 | 根因/结论 |
|---|---|---|
| `run_09cc88c898764627af1ab62da41299f4` | DSH Host 后来完成了执行 | 旧 Worker 在临时 `RemoteProtocolError` 时提前失败；Host status/result transport 需要受总 deadline 约束的有界重试 |
| `run_8afe37551e1a4ad4b6b712b0fa8c2ff9` | 同一 DSH Session 完成两轮并保留 13 条 Evidence，hard coverage 66.7% | 旧 mapper 把 discovery-only 原生 `web_search` 错误当成未批准的 Hub capability，属于计划投影错误 |
| `run_51b5a3305d2b4fba8f8579c9074a09d1` | 修复后同一 Session 两轮、10 条 Evidence、hard coverage 66.7%，生成 Artifact `art_0eeb66534dc54df4b61e4ed2257ce2ad` | DeepSeek 在合法 JSON 前增加说明文字，严格解析触发 `structured_output_invalid`；Evidence 正确保留、方向语义被 fail-closed 丢弃 |

最后一个样本证明“信息不足后继续补证”的 DSH loop 已真实存在，也证明不能把结构化协议错误伪装
成成功。根因修复锁定为 `decision_hub_synthesis_submit`：由 DSH Tool 使用 codegen Zod schema 校验，
模型在同一 Agent Loop 根据 Tool error 修复；Hub 只接受同 Session、同 `tool_call_id` 配对的成功
`tool/call -> tool/result`，然后再次校验 `request_id` 和 Evidence ID 白名单。禁止寻找第一个 `{`、
去除前后自然语言或调用 Hub 侧第二个 LLM 修复。

下一张代码任务卡为 **G2-AF-03-SYNTHESIS-ATTESTATION**。完成后必须真实复跑官方 DSH Web；只有
随后 **G2-AF-04** 的 official feed/calendar -> Run -> DSH -> Artifact -> Outbox -> child recheck
隔离真实链通过，才可宣称“个人研究辅助试点技术链可用”。

## 2026-09-04 G2-AF-02 DSH native Search -> Gateway attestation canary

执行命令（凭据仅由本机环境提供，未进入日志）：

```text
set -a; source data/dsh-live/.env; set +a
DECISION_HUB_G2AF_CANARY=1
DECISION_HUB_DSH_SEARCH_VERIFY_TLS=0
.venv/bin/python -m tools.canary.run_g2af_search_attestation_canary
```

结果：`status=passed`，Run/Session 在临时 SQLite 中创建并在命令结束后销毁，正式数据库、
active pointer、DSH 工作区和长期 JSONL 均未修改。

```text
run_id: run_f1d0819198ce4d16bc303143daccca03
dsh_session_id: dsh_1287ace1b91f2a42780a639b1ee02e3c1a097781fd5072de2be353858a75cbf0
provider/model: dsh-native-web-search / deepseek-v4-flash
Search candidates: 3（全部 federalreserve.gov locator）
web.fetch evidence: 1（verified_web, fresh）
official.macro evidence: 1（official, fresh）
ledger evidence: 5（含 3 个 search_derived candidate）
event_identity Gate: covered
trace events: 6（每次 capability started/completed）
estimated cost: $0.01
latency: 16000 ms
remaining hard gaps: policy_or_data_delta, expectation_pricing, macro_transmission,
  crypto_spot_confirmation, derivatives_crowding
```

本次探针还暴露并修复一个可重复的供应商行为：同一 Search 响应可能重复相同 URL/内容，
导致 canonical evidence identity 冲突并丢弃整批候选。`WebSearchResearchAdapter` 现在按内容
hash 首次出现保留、重复项确定性丢弃；回归测试覆盖该情形。

这张 canary 证明“DSH 原生 Search 结构化 locator -> Hub durable Gateway -> Fetch/Official
attestation -> 临时 Ledger/Trace”可用，并证明搜索结果不能单独关闭金融 hard gap。它**不**证明
DSH Web 官方 Agent Loop 在同一真实用户 Session 中自动完成上述桥接，也不证明 `crypto_macro`
全量事实充分、报告可交易或盈利。G2-AF-03 仍需独立的同 Session continuation 证据；Tavily
仍未启用或调用，聊天中暴露的 key 仍须轮换后再做 fallback canary。

新增可重复命令：`tools/canary/run_g2af_search_attestation_canary.py`。它强制临时数据库、
只读 Search、明确外部调用开关、脱敏输出和最低 event identity 验收条件，普通 CI 不触网。

## 2026-09-04 G2-AF-03/04 最终真实闭环

### 验收中发现并从根因修复的问题

1. **Host readiness 假阳性**：隔离 DSH Web 启动后虽有 Session Controller/Client Plugin，
   产品工作区尚未通过官方 `workspace/create` 注册，Host submit 会持续 503。Host readiness
   现将工作区注册纳入必要条件：注册前返回 `503/host_workspace_not_registered`，注册后才
   `ready=true`；BDD/TDD 已覆盖。
2. **隔离脚本变量错误**：一次手工验收把 Gateway 写成不存在的
   `DECISION_HUB_DSH_RESEARCH_TOOL_URL`，导致 DSH 调用旧的 `8002` 实例并返回
   `research_session_not_found`。正式入口继续只使用
   `DECISION_HUB_RESEARCH_TOOL_URL`；该失败属于验收环境，不改写为产品成功。
3. **原生 Search capability ID 不统一**：DSH JSONL 已有 `web_search`，但 Hub mapper 记录为
   `dsh.internal.web_search`，会让 canary 错判“未搜索”。现统一为
   `dsh.native.web_search`/`dsh.native.web_fetch`，并有 mapper 回归测试。
4. **后续轮次 timeout 丢失已成功成果**：真实 Run
   `run_4aeadf9cbaef44fb8138f385cfeba64d` 已完成 3 轮、22 次 reservation、16 条 Evidence 和
   121 条 Trace，却在总 deadline 尾部被整体标记为 `failed/provider_timeout`，没有 Artifact、
   Outbox 或复查。研究图现在仅在“至少已有一个完整可信 round，后续发生 retryable
   AgentExecutionError”时保留上一轮 attested synthesis 和所有已持久化 Evidence，记录完整
   provenance 并强制 `degraded/research_only` 收口；首轮失败和 schema/PIT/权限/代码错误仍
   hard fail。真实工具计数从 durable reservation 读取，不用模型自报值。
5. **Domain Pack 与契约测试预算漂移**：G2-AF 阶段已接受 3 rounds/24 calls，但全量测试仍
   断言旧值 12。测试已同步到 canonical `pack.yaml` 的 24，Gateway 的跨 generation 原子
   reservation 仍保证实际调用不能超额。

### 最终隔离环境与 fail-closed readiness

```text
data_dir: /tmp/decision-hub-g2af-accept.bCBDGM
Hub API: 127.0.0.1:18260
Research MCP: 127.0.0.1:18262
Official DSH Web: 127.0.0.1:3195
plugin build: ad2913e101cd65b2a6dacdbdc8722afa3627812836b7bb674a271fe7b835d7e1
workspace before registration: 503 / host_workspace_not_registered
workspace after official workspace/create: 200 / ready=true
```

### 最终自动事件 canary

命令：`.venv/bin/python -m tools.canary.run_g2af_autonomous_flow_canary`，凭据仅由本机
gitignored live 环境提供，canary 使用临时账本且不修改历史数据或 active pointer。

```text
status: passed
event_id: evt_3c9085f7b404456a81b6bd05b3a2dc97
run_id: run_b89cb225177145cd9a0e7cd0b038e31c
dsh_session_id: dsh_d65c746d69529710aa822c6f5409e0d4393783f90d5c05d0ce59e21734ed801f
artifact_id: art_20c52f7c35f247c7bb43673c5433d249
run_status: degraded
gate_status: research_only
stop_reason: round_budget
rounds: 3
tool calls: 20 / 24
retained Evidence: 13
hard coverage: 83.33%
Trace events: 139
Outbox: local / attempts=1 / sent once
child recheck: admitted exactly once
```

以下断言全部为 `true`：

```text
automatic_admission
artifact_committed
canonical_result_committed
synthesis_attested
dsh_native_search_used
evidence_retained
trace_retained
outbox_created
notification_delivered_once
child_recheck_scheduled
duplicate_poll_idempotent
```

这次运行从 Fed 官方 speech feed 自动发现 Waller 讲话，无需 owner 逐轮输入。DSH 在同一个
Session 中执行三轮，真实调用 typed official/market capability、原生 `web_search`、受审计
`web.fetch` 和 `decision_hub_synthesis_submit`。FRED 日频数据相对分钟级要求被正确标为 stale；
Search 找到的 Fed in Print、Reuters/财经媒体 locator 不在当前 Fetch allowlist 时以
`research_domain_denied` 保留，没有把搜索摘要冒充金融事实。

### 浏览器验收与资产

- Decision Desk 能看到自动发现父 Run、scheduled recheck child、3/3 round、20/24 tools、
  13 条 Evidence、83% hard coverage、失败 provenance、Gate、stop reason 和完整 Trace。
- DSH 官方页面能看到受管 Workspace/Session、原生 `web_search`、成功/失败 Tool Call、三轮导航、
  独立“研究报告”和“轨迹”页签；终态为“仅研究完成”，没有停在“生成中”。
- DSH 与 Desk 新页面控制台均为 0 warn/error。
- DSH 报告与 Decision Desk 在 `390x844` 复验时均满足 `document.scrollWidth == innerWidth == 390`，
  无页面级横向溢出；验收后恢复默认视口。
- 截图及 SHA-256：
  - `assets/g2af-final-hub-20260904.jpg`：`198cc93504fa9e1413400487db84eccffa3c5575781e808758a07e99e7b58368`
  - `assets/g2af-final-dsh-report-20260904.jpg`：`cd18f8743ec81f18839ea110baf01663f7c2abfb8a292cb292ec80a038eada9b`
  - `assets/g2af-final-dsh-trace-20260904.jpg`：`70dd72e5824291fed07be37c562533149e78c544beccfe8e55816a80ccb2f15e`

隔离 canary 没有常驻 research worker，因此 Desk 的 service heartbeat 显示 offline；这不是
正式 `run-product.sh` 的运行方式，也不影响本次自动链断言。正式试点必须由产品启动器同时托管
API/MCP/worker/DSH，不能把 canary 页面当常驻部署。

### 仍未完成且不得掩盖的价值门

- `macro_transmission` 仍因缺少分钟级收益率/美元事实而 stale；soft cross-asset 与 counter-thesis
  也缺独立受审计来源。技术闭环通过不等于报告已达到可交易充分度。
- DSH 页面记录本次约 797K 输入 token、49.3K 输出 token、93% cache hit；Hub 仍只显示
  `$0.0000 known estimate`，Provider 模型费用尚未归集到 Hub Run cost。G2-AF-05 必须把该项
  纳入单位事件成本，不能把 0 当真实费用。
- Tavily fallback adapter/离线测试已完成，但聊天中旧 key 已暴露，未做 live 调用。轮换 key 后
  才能执行独立只读 canary；不得将旧 key 写入仓库或日志。
- G2-AF-05 仍需 14 天或 20 个真实高影响事件，记录 Coverage、时延、费用、Outcome/Brier 和
  owner usefulness 后做 `promote / retain / stop`。当前只能称为“个人研究辅助技术试点”。
- 空 SQLite 上 API/MCP 完全并发首次启动仍可能触发 Alembic 建表竞态；正式启动器以 API ready
  后再启动 MCP 的顺序规避。没有真实并发部署需求前不引入额外分布式锁。

### 最终质量门

```text
.venv/bin/pytest -q                                      446 passed
.venv/bin/ruff check packages apps tests tools             passed
.venv/bin/pyright packages apps tests tools/canary         0 errors / 0 warnings
.venv/bin/python -m tools.contract_codegen check           passed
.venv/bin/python tools/docs/check_module_docs.py           passed (13 modules)
pnpm --dir extensions/dsh/decision-hub test -- --run       60 passed
pnpm --dir extensions/dsh/decision-hub build               passed
pnpm --dir apps/decision-desk test -- --run                 10 passed
pnpm --dir apps/decision-desk build                         passed（保留 >500 kB chunk warning）
git diff --check                                           passed
```
