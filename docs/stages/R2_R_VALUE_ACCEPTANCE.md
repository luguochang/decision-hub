# R2-R-06 研究智能体价值验收执行方案

版本：`R2-R-06-2026-08-30.v0.1`
状态：`completed / retain_baseline / pending_owner_review`
上级阶段：[R2-R Agentic Research Runtime](R2_R_AGENTIC_RESEARCH_RUNTIME.md)
产品规格：[研究智能体主体产品规格](../product/RESEARCH_AGENT_PRODUCT_SPEC.md)

> 本文只负责 R2-R-06 的可执行任务卡和决策证据。它不新增产品范围，不授权自动交易、ASR、PPT、多用户、任意社区插件或自动 Promotion。

## 1. 唯一目标

用可复现证据回答一个问题：受限 DSH Research Harness 相比 Fixed baseline，是否已经给单 owner 带来足够的研究价值，可以晋级为 `crypto_macro` 默认 Research Runtime。

R2-R-06 结束时只能出现两种诚实结果：

1. `promotion_recommended`：工程、安全和真实价值门都通过，提交 owner 决策；
2. `retain_baseline`：报告明确列出未通过项、失败样本和下一次最小候选，不继续堆功能掩盖问题。

无论哪种结果，都不得宣称盈利。Brier、方向准确率和净收益只有 Outcome 到期后才能补记。

## 2. 进入阶段时的实测事实

- R2-R-00 至 R2-R-05 已完成，Research Command Center、durable worker、双 Snapshot、Gate、Result/Trace/SSE 和 owner command 已接通。
- legacy `/v1/observations` 仍是 Fixed baseline；`/v1/research/observations` 是 candidate path，active pointer 未切换。
- DSH SDK `0.1.1rc1` 的基础 Tool/Subagent canary 曾通过，但 2026-08-30 的 DSH + MCP 复跑在 125 秒外层上限超时。一次历史成功不能替代时延和失败率评测。
- OpenAI-compatible 中转已实测支持 Responses `web_search` 和 `web_search_call.action.sources`；当前 `research-mcp` 却没有组合真实 `web.search` adapter。
- `web.search`、`web.fetch`、Official/Market manifest 仍是 candidate/review-required，默认 Gateway 会拒绝；R2-R-06 必须只晋级通过合同、安全和 live canary 的具体 capability。
- DSH 最终 `EvidenceCandidate` 当前只做 schema/session 校验，尚未逐条证明来自 MCP Tool Result。未完成 attestation 前，模型可能提交未被工具结果支持的候选，不能进入价值评测。
- 当前只有一个 research replay fixture，不能满足 10-20 个事件的产品价值门。

## 3. 不新增第二套平台

```text
DSH Manager / Subagents
        |
        | canonical MCP tool call
        v
Research Capability Gateway
        |
        +-- OpenAI Responses Web Search transport (opt-in)
        +-- Official document adapter
        +-- OKX derivatives adapter
        +-- FRED cross-asset adapter
        +-- archived replay adapter
        |
        v
ResearchCapabilityResult / EvidenceCandidate
        |
        | tool-result attestation
        v
DSH ResearchSessionResult candidate
        |
        v
LangGraph rounds -> Sufficiency -> Decision Snapshot -> deterministic Gate
        |
        v
existing Evaluation / Asset / Version / Promotion ledger
```

复用边界：

- DSH 继续拥有 model/tool/subagent/session loop；不自写 ReAct。
- OpenAI 官方 SDK 只实现可替换 Search transport；不自写 HTTP/Responses 协议。
- MCP 和 `ResearchCapabilityGatewayService` 继续拥有 capability schema、权限、域名、timeout、成本和 PIT。
- LangGraph 继续拥有外层轮次和恢复；不解析网页或 DSH 私有 Session。
- 评测复用现有 Dataset/Experiment/Result/Promotion 资产；只新增 Research Runtime scorer/runner，不建第二套账本。

## 4. 任务卡

| Task | 目标 | 实现 | 退出证据 |
|---|---|---|---|
| `R2-R-06A` | 关闭真实证据可信性缺口 | 组合 opt-in Responses Search；MCP Tool Result 与最终 Evidence 逐条 attestation；只批准实测 capability | fake contract、tamper/unknown evidence、domain/cost/timeout/PIT、真实 search source canary |
| `R2-R-06B` | 建立公平 PIT 数据集 | 12 个固定事件，覆盖 central-bank speech、policy decision、inflation、labor、geopolitical；冻结触发事实、当时可用证据、来源 hash 和 cutoff | manifest hash、时间顺序、future leakage 和事件族数量检查 |
| `R2-R-06C` | Fixed vs DSH 对照 | 同一 Event/cutoff/budget 分别运行 Fixed 和 DSH candidate；先写 raw report，再聚合指标 | 12 个样本全部产生可追溯 report；失败也计入，不删除 |
| `R2-R-06D` | 新真实事件验收 | 从 2026-08-30 以后首次选中的高影响事件建立 durable Run，使用显式启用的 audited capability | 真实 Run/Trace/Evidence/Result、成本/延迟/失败、owner usefulness 表单 |
| `R2-R-06E` | 形成 Runtime 决策包 | 生成机器可读 JSON + 中文 Markdown，对照所有 blocking/non-blocking 指标 | `promotion_recommended` 或 `retain_baseline`；最终 pointer 仍由 owner-only command 决定 |

任务顺序固定为 `06A -> 06B -> 06C -> 06D -> 06E`。06A 未通过时禁止运行批量 live DSH，避免用不可信证据花费成本。

### 4.1 执行状态

- `R2-R-06A done (2026-08-30)`：官方 `AsyncOpenAI` Responses Search 已通过
  domain/source/cost/PIT/timeout 边界和真实 MCP canary；DSH 真实 MCP Tool Result 已被
  生产 attestation 解析为 1 个 canonical Result/1 条 Evidence。缺失、篡改和越权证据
  均以 `dsh_evidence_unattested` 关闭。
- `R2-R-06B done (2026-08-30)`：`crypto_macro.r2r_pit.v1` 固定 12 个 case 和
  `5fb57edc255d34d6a4011e7c15a3563f03e41e852d6a9d0a1460dface3348cde`
  manifest hash；canonical Case、逐文件 hash、PIT、来源、hard requirement、事件族和
  future leakage 检查均通过。Outcome 全部保持 pending，未伪造标签。
- `R2-R-06C done (2026-08-30)`：已完成首轮 12-case 与 structured-repair 后的追加
  12-case 对照；原始报告和失败样本均已登记，active pointer 未改变。
- `R2-R-06D done (2026-08-30)`：Warsh Jackson Hole 讲话建立 durable Run
  `run_27acb7c425914bc7a69060637ea1feb3`。DSH `gpt-5.5` 成功启动 Session 并尝试
  `web.search`，但 capability 在 20 秒 deadline 内失败；Trace、Decision Snapshot、
  Research Result 和 reject Artifact 均保留，未产生未认证 Evidence，未修改 active pointer。
- `R2-R-06E done (2026-08-30)`：已生成 [Runtime 决策包](../evaluations/R2-R-06E_RUNTIME_DECISION.md)。
  结论为 `retain_baseline / pending_owner_review`；DSH 继续 candidate/shadow，不晋级 active。

### 4.2 06C Canary 发现与架构修正

同一 `powell_stanford_20240403` case 的三次真实 DSH Canary 均作为失败样本保留：

| Experiment | DSH 行为 | 结果 |
|---|---|---|
| `r2-r-06c-20260829T183320Z` | MCP 取得 2 条 Evidence；模型猜测错误的完整 Result shape，并改写一条 Evidence 时间戳 | `structured_output_invalid`，约 102 秒 |
| `r2-r-06c-20260829T184016Z` | 8 次 MCP 调用、2 条归档 Evidence；模型耗时组装 Session/Round/Tool/时间戳等运行账本 | 180 秒 timeout；旧错误映射曾记为 `dsh_runtime_failed` |
| `r2-r-06c-20260829T190104Z` | synthesis-only 契约已生效；130 个 Session event、3 个 step、2 个 tool call；模型给 replay 请求添加未授权域名，随后调用 `todo_write` 并在高推理档耗尽预算 | `provider_timeout`，约 180 秒；未登记资产、未修改 active pointer |

根因和修正由 [ADR-0010](../decisions/ADR-0010-model-semantics-runtime-ledger-boundary.md)
锁定：模型改为输出 `research-synthesis-candidate.v1`；adapter 从真实 DSH 通知和
canonical MCP Result 组装 `research-session-result.v1`，并验证全部 Evidence 引用。
该修正不改变 Dataset、评分、PIT、预算或 Promotion 门。

第三次 Canary 后的最小运行修正不放宽任何产品 Gate：unattended profile 固定
`reasoningEffort=low`；replay 调用固定 `target_url=null`、`allowed_domains=[]` 和
`<event_id>:<requirement_id>` 归档别名；禁止在 bounded session 中调用
`todo_write`，工具完成后立即输出 synthesis。Role Profile 的 `output_schema_ref`
同步指向模型实际负责的 `research_synthesis_candidate`。新 06C candidate profile 为
`decision-research.v1:faf1b3115f7d339c`；R2-R-01 的
`decision-research.v1:1d4ce1f40ab265e4` 仍作为历史接通证据保留。上述修正已通过
32 个 DSH/contract/eval 专项测试、Ruff、Pyright、canonical codegen、13 个模块文档
和 `git diff --check`。

第四次同 case Canary `r2-r-06c-20260830-low-profile-canary` 已通过：DSH 在
56.5 秒内完成，取得 2 条同 Session attested Evidence，hard coverage 为 33.3%，
PIT violation 与 unattested Evidence 均为 0，三个 Horizon 语义互不重复；Fixed
baseline 用时 29.2 秒、Evidence 为 0、hard coverage 为 0、Horizon distinctness
未通过。该 Canary 未登记资产、未修改 active pointer。

Canary 后的 raw Result 审计又发现并关闭两个产品缺口：replay Horizon 改为相对
历史 PIT cutoff 校验，不再相对 2026 实际执行完成时间误判过期；Research Round 的
tool invocation/result 只从 canonical Trace 和同 Session MCP Result 投影，不接受模型
自报，也不复制 raw 参数或 Provider payload。新增 TDD 先得到 3 个预期失败，修正后
相关 DSH/Graph/contract/eval 专项为 39 passed，Pyright 0 errors，Ruff、codegen、
module docs 和 diff check 均通过。因此 12-case 门禁已放行。

### 4.3 12-case 对照结果（必须保留失败）

首轮实验 `r2-r-06c-20260830-full-12case` 与 structured-repair 后的追加实验
`r2-r-06c-20260830-repair-full-12case` 都已写入
`data/decision-hub/evaluations/`，失败 case 不删除、不重跑覆盖：

| 实验 | Runtime | 完成/样本 | hard coverage mean | Horizon distinct | Tool calls | Evidence | p95 latency | 失败 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `full-12case` | Fixed | 11/12 | 0 | 0/12 | 0 | 0 | 115480ms | 1 `unknown_runtime_error` |
| `full-12case` | DSH | 9/12 | 0.2222 | 7/12 | 72 | 16 | 115480ms | 3 `structured_output_invalid` |
| `repair-full-12case` | Fixed | 12/12 | 0 | 0/12 | 0 | 0 | 121336ms | 0 |
| `repair-full-12case` | DSH | 9/12 | 0.1667 | 8/12 | 43 | 12 | 180177ms | `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout` |

追加实验的 PIT violations 为 `0`，但 DSH 有 `1` 条 unattested Evidence；因此
DSH 目前证明了真实 Tool/Session/Evidence 链路和相对 Fixed 更高的证据覆盖，尚未证明
可靠性、时延或产品价值达到 Promotion 门。三类失败分别归档为：

- `fomc_cut_50bp_20240918`：Evidence 未能由同一已完成 MCP Result 证明，必须
  `dsh_evidence_unattested` fail-closed；不能通过放宽校验解决。
- `us_cpi_20250212`：DSH Session 未以可接受完成原因结束，记录
  `dsh_session_incomplete`；不能把部分输出当成结果。
- `fomc_cut_25bp_20241218`：在有界 deadline 内超时，记录 `provider_timeout`；
  不能无限延长预算或重试掩盖尾延迟。

当前结论：`R2-R-06C` 允许进入 06D，但 `promotion_recommended` 仍不可报告；
默认 active runtime 继续保持 Fixed，DSH 保持 candidate/shadow。

### 4.4 06D 真实事件结果

真实事件是 2026-08-28 Kevin Warsh 在 Jackson Hole 的 `In Our Time` 演讲，官方来源为
<https://www.federalreserve.gov/newsevents/speech/warsh20260828a.htm>。Run
`run_27acb7c425914bc7a69060637ea1feb3` 使用 DSH `0.1.1rc1`、profile
`decision-research.v1:faf1b3115f7d339c` 和 `gpt-5.5`，运行 `100646ms`。

DSH 识别出证据缺口并调用一次 canonical MCP `web.search`，但 Search provider 在
capability 的 20 秒 deadline 内失败。产品层正确收口为：`evidence_count=0`、
`stop_code=critical_data_unavailable`、Gate=`reject`，并保留 14 条规范化 Trace、
Trigger/Decision Snapshot、Research Result 和 Artifact。该结果证明了缺口发现、工具调用、
失败降级和 Gate 的智能体生命周期，但没有证明实时搜索稳定或结果具备交易价值。

此前因全文超过 4000 字符导致 Request Factory 在输入投影阶段失败的问题已按 TDD 修正：
只截断送入 ResearchSessionRequest 的 `ResearchInputEvidence.excerpt`，完整原文仍保留在
Observation/Trigger Snapshot，content hash 不改变。

### 4.5 06E Runtime 决策

`R2-R-06E` 的 blocking 检查中，PIT violations 为 0，但追加 12-case 仍有 1 条
unattested Evidence、1 次 Session incomplete 和 1 次 Provider timeout；DSH Horizon
distinct 为 8/12，p95 为 180177ms，真实事件 Search 失败且成本为 unknown。故只能报告
`retain_baseline`，不能报告 `promotion_recommended`。

Owner usefulness 尚未填写。Owner 需要在决策包中回答是否减少人工查证时间、报告是否可解释、
当前失败/延迟下是否值得继续 candidate，以及最值得保留或修正的能力。表单完成前不允许
Promotion；下一候选只应聚焦 Search reliability/error provenance，并重新通过单事件 canary
和 owner review，不扩大到 ASR、PPT、第二领域或自动交易。

## 5. Search 决策

首个 Search transport 使用现有 OpenAI-compatible endpoint 的 Responses `web_search`：

- 官方 SDK `AsyncOpenAI` 负责协议、timeout 和响应模型；
- 请求必须包含 `include=["web_search_call.action.sources"]`；
- 来源 URL 从实际 `action.sources` 读取，不能只从模型文本提取；
- Search 输出固定为 `authority=search_derived`，只用于发现，不能伪装成官方原文；
- 精确事实继续用 Official/Market tool，搜索后应 `official.macro`/`web.fetch` 读取原文；
- domain filter、结果上限和 broad search 权限继续由 capability manifest/Gateway 控制；
- 默认关闭，只有 `DECISION_HUB_RESEARCH_CAPABILITIES` 显式包含 `web.search` 才运行；
- 成本以可配置的保守估算入账。OpenAI 官方基价是每次 Web Search 工具调用 0.01 美元，另计搜索内容 token；中转实际计费可能不同，报告必须标记 estimated。

当前中转实测不支持官方 `max_tool_calls` 参数。适配器使用低 reasoning、有限输出和
20 秒 Gateway timeout，按实际 `web_search_call` 数量执行调用后成本 Gate。该 Gate 能
阻止超预算结果进入产品链，但不能在请求前阻止中转内部已发生的搜索费用；这是
06E 必须披露的 Provider 限制，不得写成硬预授权成本控制。

DSH 官方 `dsh-tool-web` 保留为以后可替换 provider，不在同一阶段并行接入。当前没有 DeepSeek Search、Exa 或 Perplexity 凭据，同时接两套 Search 会扩大权限和评测面。

## 6. Tool Result Attestation

最终 `ResearchSessionResult.evidence_candidates` 的每一项必须满足：

1. 同一 DSH Session 中存在已完成的 `research_capability_execute` Tool Result；
2. Tool Result 可按 canonical `ResearchCapabilityResult` 解析；
3. `evidence_id`、`content_hash`、lineage、三时间戳、URL 和 excerpt 与 Tool Result 完全一致；
4. capability 在当前请求 allowlist 中，且 Tool Result 不是 error；
5. 找不到、篡改或跨 Session 的 Evidence 使整个 DSH round typed fail-closed，错误码为 `dsh_evidence_unattested`。

产品 Trace 只保存规范化调用/结果状态和引用，不保存 raw Tool Result；业务 Evidence 仍从已验证 canonical candidate 落账。

## 7. PIT 数据集和指标

固定样本数为 12，不为追求漂亮数字删除失败事件。事件族最低分布：

| 事件族 | 最少数量 |
|---|---:|
| central-bank speech | 2 |
| monetary-policy decision | 3 |
| inflation release | 3 |
| labor release | 2 |
| geopolitical shock | 2 |

每个 case 固定：`case_id`、事件族、输入文本、published/observed/received/cutoff、trigger evidence、archived capability results、source hash、预期 hard requirements、Outcome 可用时间和可选标签。Runtime 看不到 cutoff 后标签。

聚合指标：

| 组 | blocking 指标 |
|---|---|
| Evidence | PIT violations 必须为 0；citation URL 必须来自 tool result；hard coverage 不得低于 Fixed |
| Trajectory | 发现 hard gap 后必须有 tool/fallback 或明确 permission/budget stop；无限循环为 0 |
| Decision | horizon exact duplicate 为 0；未校准概率有 provenance/cap；开放 hard gap 只能 research_only |
| Runtime | 失败/timeout 全计入；p95 latency、tool/subagent 数、tokens、estimated cost 可见 |
| Product | owner 对“是否减少手工查证、是否能解释、是否值得复用”逐项评分，不用 LLM Judge 代替 |

方向准确率、Brier、MFE/MAE 和净收益作为 `pending_outcome` 单独列出，不阻塞首日工程报告，也不能被填成 0 或猜测值。

## 8. Promotion 规则

只有以下条件同时成立才允许报告 `promotion_recommended`：

- 06A 至 06D 全部有证据；
- 12 个 PIT case 无 future leakage、无 unattested evidence；
- DSH hard coverage、引用有效性和 horizon distinctness 不劣于 Fixed，且至少一项核心指标有实际提升；
- 新真实事件不是静默失败，完整过程可在 Command Center 复查；
- 成本和 p95 延迟在 Pack budget 内，失败/timeout 有明确降级；
- owner usefulness 接受。

即使报告推荐晋级，也只生成 `pending_owner_review` 决策包。Agent、脚本、DSH 和前端都不能自动修改 active pointer。

## 9. 代码和文档落点

```text
packages/provider_adapters/search/          Responses web-search transport
apps/research_mcp/                          capability composition only
packages/runtime_adapters/dsh_runtime/      MCP evidence attestation only
packages/evals/                             research comparison runner/scorer
packs/crypto_macro/evaluations/             12-case manifest and archived inputs
tools/                                      R2-R-06 acceptance/report command
tests/{capabilities,runtime,evals,research}/ failure-first coverage
docs/stages/                                this plan and final decision report
```

不新增 migration，除非已有 Evaluation/Asset 表无法表达最终证据；遇到这种情况先写 ADR，不用 JSON 文件冒充长期账本。

## 10. 当前唯一保留给 Owner 的决定

工程实现和评测范围已经确定。Owner 只在 06E 查看完整报告后决定：

- 将 DSH candidate 晋级为 `crypto_macro` 默认 Research Runtime；或
- 保留 Fixed baseline，继续让 DSH 作为 candidate/shadow。

在此之前不再请求技术路线选择，也不把一次 canary 成功写成产品可用。
