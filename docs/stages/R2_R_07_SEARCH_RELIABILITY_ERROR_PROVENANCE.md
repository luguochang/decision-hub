# R2-R-07 Search Reliability 与 Error Provenance 修复阶段卡

版本：`STAGE-R2-R-07-2026-08-30.v0.1`
状态：`accepted / G1 complete / G2-A/B complete / G2-C live Search failed safely / G2-D E2-L pending`（owner 已授权 G1、G2）
前置：[R2-R-06E Runtime 决策包](../evaluations/R2-R-06E_RUNTIME_DECISION.md)
上级阶段：[R2-R Agentic Research Runtime](R2_R_AGENTIC_RESEARCH_RUNTIME.md)
关联 ADR：[ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)、[ADR-0010](../decisions/ADR-0010-model-semantics-runtime-ledger-boundary.md)、[ADR-0011](../decisions/ADR-0011-research-reliability-fact-boundary.md)

> 本阶段不是 R3，也不是重新实现 Agent Loop。它只处理 R2-R-06E 真实事件验收暴露的 Search 时间边界、错误来源和失败状态投影问题。G1-A/B/C/D 与 G2-A/B 已完成实现和离线验证；G2-C 真实 Search canary 已执行但以 `research_capability_timeout` 安全失败，G2-D/E2-L 事实充分度验收仍是未完成阶段门。具体代码和验证必须遵循 [G1/G2 实施方案](R2_R_G1_G2_EXECUTION_PLAN.md) 的契约、BDD/TDD 和顺序。

## 1. 为什么需要本阶段

R2-R-06E 已证明 DSH candidate 能够围绕目标主动规划搜索任务，但真实 durable Run 没有得到可认证证据。隔离 live Run `run_488b389ad8674dcfb632d12ea7b2399c` 的实际链路为：

1. DSH 规划 6 类证据缺口：事件身份、政策/数据变化、预期定价、宏观传导、BTC 现货、衍生品拥挤度。
2. 模型自由填写 `observed_at`，把它设置为与 `cutoff_at` 相同。
3. Gateway 收到网络结果时已晚于该时间点，按 PIT 铁律拒绝证据。
4. 首个 MCP 工具失败后，其余并行调用被 abort；上层将错误粗略归类为 `provider_timeout`。
5. 前端没有把完整失败码和停止原因投影到研究详情，失败 Run 可能仍显示为运行中。

这是产品边界和错误语义问题，不是要求 owner 手工提供收益率、DXY 或行情接口，也不是用更多 Prompt 掩盖数据不足。Fail-closed 是正确安全结果，但必须让系统说明“哪个 capability、哪一个时间边界、哪一层失败”。

## 2. 唯一目标与可观察价值

**唯一目标：** 在不改变 Core 账本、Deterministic Gate、active pointer 或 DSH loop 所有权的前提下，使一次真实研究 Run 能够可靠地区分“服务端时间边界拒绝”“搜索提供方失败”“单个并行工具失败”“整个 Provider 超时”，并在页面显示可操作的停止原因。

完成后的可观察价值：

- Owner 不需要手工填写或猜测 `observed_at`；服务端生成可信接收时间，模型只能提出查询语义。
- 一个搜索任务失败不会静默吞掉其他已完成的任务；Run 会保留每个 capability 的结果/失败。
- `research_pit_violation`、`search_provider_failed`、`dsh_tool_failed`、`provider_timeout` 等错误码有稳定层级和 provenance。
- Research Command Center 能显示 `failed/degraded/research_only`、停止码、失败 capability、是否可 retry/recheck；不再把失败状态伪装成运行中。
- 失败仍然 fail-closed：没有认证 Evidence 就不生成方向性发布，不修改 active pointer，不发送决策通知。

## 3. 范围

### 3.1 允许做

- 在 Research Capability Gateway/adapter 由服务端生成或校正 `observed_at`，保留原始请求值用于审计但不作为可信 PIT 时间。
- 统一 DSH/MCP/Provider/Capability 的结构化错误模型和 provenance 链。
- 为并行 capability 调用建立单任务隔离、部分结果收集和确定性 round stop 语义。
- 把失败 Run 的 stop code、error code、failed capability、retryability 投影到 canonical Research View/API/SSE/UI。
- 增加 fake/replay、失败注入、单一真实事件 canary 和对应 BDD/TDD 证据。
- 更新受影响模块 README、Stage/Status/Roadmap/Changelog 和必要 ADR。

### 3.2 明确不做

- 不重新实现 DSH/模型 Agent Loop、MCP 协议、LangGraph checkpoint 或搜索 HTTP 协议。
- 不同时引入 ASR、PPT、第二领域、自动交易、多用户、公共插件市场或新数据库/队列。
- 不扩大 live capability allowlist，不把 candidate capability 改为 approved；每个来源仍需单独审计和 canary。
- 不迁移、删除或改写历史 Run、Evidence、Artifact、Forecast、Outcome、Evaluation 或 active pointer。
- 不把一次成功 canary 宣称为实时稳定、预测准确或盈利证明。

## 4. 复用与代码边界

```text
DSH SDK loop / MCP transport / OpenAI SDK
             |
             v
ResearchHarnessRuntime adapter
             |
             v
Research Capability Gateway  <-- server-owned time + error provenance
             |
             v
LangGraph outer evidence round / checkpoint / recovery
             |
             v
Kernel Evidence + Sufficiency + Gate + Ledger
             |
             v
Research Query/View/SSE -> Decision Desk
```

复用现有能力：DSH SDK 的 session/tool/subagent loop、官方 MCP transport、OpenAI SDK 的 Responses Search transport、LangGraph 的 bounded round/checkpoint、Pydantic/Zod/canonical codegen、SQLAlchemy/Alembic 账本、现有 Research Trace/View 和 owner command。

只允许在以下边界补薄层：

- `packages/kernel/.../application/research_evidence.py`：可信时间和错误归一化入口；不在 route 或 graph node 复制。
- `packages/provider_adapters/research/` 与 `packages/provider_adapters/search/`：transport/provider provenance；不拥有 Gate/账本。
- `packages/workbench_adapters/research_mcp.py`：canonical MCP 错误映射和请求字段裁决；不让模型拥有 PIT。
- `packages/runtime_adapters/dsh_runtime/`：DSH tool/session 错误保真投影；不把模型自报错误当事实。
- `packages/query_views/research/`、`apps/hub_api`、`apps/decision-desk/src/research/`：失败状态的人可读投影；不展示 raw JSON。

禁止新建第二个 Agent Loop、第二套错误 DTO、第二套 Run 状态机或通用 `utils/` 目录。跨模块字段必须先改 `contracts/schemas/` 再 codegen。

## 5. 目标契约与错误语义

### 5.1 时间所有权

`cutoff_at` 由 Run/Trigger/Decision Snapshot 的代码边界拥有；`received_at` 由服务端在收到 capability response 时生成；`observed_at` 对网络来源由 adapter/Gateway 生成，不能接受模型自由填写的可信值。对于 replay，`observed_at` 必须来自 fixture manifest，并按 cutoff 校验。

保存原始请求时间只用于诊断：`requested_observed_at`。它不能进入 Evidence 的可信 PIT 判定。历史数据保持不变，新字段必须向后兼容或通过迁移增加。

### 5.2 错误层级

```text
provider_timeout                 Provider/model 总 deadline 超时
  search_provider_failed         搜索 transport/provider 返回失败
    dsh_tool_failed              DSH tool call 未完成或 MCP 返回工具级错误
      research_pit_violation     结果时间晚于 cutoff 或时间字段不可信
```

实际错误只保留最具体的稳定码，同时记录：`origin`、`capability_id`、`request_id`、`tool_call_id`、`research_session_id`、`retryable`、`deadline_ms`、`cause_code`。不得把 PIT 拒绝压成 provider timeout，也不得把模型文本中的错误描述当作 provenance。

### 5.3 并行失败语义

- 每个 capability 拥有独立 timeout、attempt 和终态。
- 一个调用失败时，其余未完成调用按 bounded cancellation 处理；已完成结果仍可入账。
- round 只有在满足充分度、deadline、预算或显式 critical failure 时才停止。
- 关键证据缺失时进入 `research_only`/`reject`，并列出缺口；不得用 partial result 生成方向性发布。

## 6. BDD 验收场景

```text
Feature: PIT 时间由服务端拥有
Scenario: 模型提交与 cutoff 相同的 observed_at
Given 一个真实模式 Research Run 和冻结 cutoff_at
When DSH tool 请求包含模型填写的 observed_at
Then Gateway 使用服务端 received_at/来源时间生成可信 observed_at
And 原始请求值只作为诊断字段保存，不能绕过 PIT 校验
```

```text
Feature: 错误来源可解释
Scenario: 搜索结果晚于 cutoff
Given Search provider 返回 HTTP 成功但来源时间晚于 cutoff
When Evidence Gateway 校验结果
Then Run 保留 capability 失败记录，错误码为 research_pit_violation
And 页面显示 stop code、capability 和 retryability，不显示 provider_timeout
```

```text
Feature: 并行 capability 隔离
Scenario: 一个搜索失败而另一个官方来源成功
Given 两个独立 capability 调用
When Search 调用失败、Official 调用成功
Then Official 结果继续进入 canonical Evidence candidate
And Search 失败单独记录，Run 不因泛化 MCP error 丢失已完成结果
```

```text
Feature: 失败 Run 可操作
Scenario: 关键证据缺失
Given 没有足够认证 Evidence 通过 Sufficiency Gate
When Research Run 终止
Then UI 显示 failed/degraded、stop code、错误来源和缺口
And retry/recheck 只创建新的 child Run，不改写父 Run 或 active pointer
```

## 7. TDD 任务卡与退出门

本阶段按 owner 已授权范围执行，每张卡先 Red、再最小 Green、最后 Refactor；当前 G1 与 G2-A/B 已完成，只有 G2-C 仍有独立 live canary gate：

| Task | 实现边界 | 必须先写的测试 | 退出证据 |
|---|---|---|---|
| `R2-R-07A` | server-owned PIT time + request/response projection | 时间漂移、replay cutoff、旧请求兼容、tamper reject | `done`：contract/kernel/replay tests 通过；历史数据未改写 |
| `R2-R-07B` | error taxonomy/provenance | PIT/provider/MCP/DSH/outer timeout 分类、retryability、cause chain | `done`：稳定错误码跨 adapter 一致；无泛化覆盖 |
| `R2-R-07C` | parallel capability isolation | 一个失败/一个成功、全失败、deadline、partial evidence | `done`：已完成结果不丢，critical gap 仍 fail-closed |
| `R2-R-07D` | Research View/API/SSE/UI failure projection | failed Run 状态、stop reason、retry/recheck child、断线续接 | `done`：前端人可读、无 raw JSON、无“运行中”假状态 |
| `R2-R-07E` | manifest/replay regression | 六类事实 success/stale/provider_failure 与回放 smoke | `done`：离线回归通过；不触网 |
| `R2-R-07E-live` | isolated real-event canary | 单一真实事件、来源错误分类、G2-D 充分度 | `pending`：需 owner 确认；不改变 active pointer |

阶段退出门：

1. `pytest -m "not live"`、受影响模块测试、contract/codegen/module-docs、Ruff、Pyright 和前端 test/build 全部通过。
2. 至少一个真实事件 Search canary 能把成功、PIT 拒绝、provider failure、timeout 分开记录；失败时 fail-closed。
3. Research Command Center 可显示停止原因和失败 capability，retry/recheck 幂等且不改写父 Run。
4. Fixed baseline 继续 active；DSH 仍 candidate/shadow。不得以本阶段结果直接 Promotion。

## 8. 停止条件与 owner gate

以下任一情况立即停止并回到 ADR/owner：

- 需要让模型决定可信时间、Gate、active pointer 或业务账本；
- 需要增加新的 Agent Loop、队列、数据库、搜索协议实现或无调用方基础设施；
- 同一错误经过两次局部补丁仍无法保留 provenance；
- 需要扩大网络域名、Secret、插件权限或把 candidate capability 改为 approved；
- 真实 canary 结果不足以区分失败来源，或出现未来信息泄漏。

Owner 只需确认以下两项，其他验证由 Agent 自动完成：

1. G1/G2-A/B 的实现授权已完成，不需要再次提供数据接口或手工时间戳。
2. 是否允许一次隔离的外部 Search/真实事件 canary（只读、限时、临时数据目录、密钥不落盘）。

G1/G2 的代码实现已获授权并完成；在 owner 确认 live canary 前，当前唯一动作是离线回归、报告整理和 canary 准备。不切 active pointer，不进入 R3。

## 9. 文档与记录要求

每张任务卡完成时同步受影响模块 README、`docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`、`CHANGELOG.md` 和 `docs/context/`。若修改跨模块契约或错误语义，先新增 ADR，再改 schema/codegen 和实现。所有真实 canary 记录脱敏计数、时间、错误码和 commit，不记录 API key、raw Provider payload 或用户凭据。

## 10. 离线实现与 canary 前检查证据

2026-08-30（Asia/Shanghai）完成 G1/G2-A/B 实现后：

```text
git diff --check                         passed
tools/docs/check_module_docs.py          passed (13 modules)
tools.contract_codegen check              passed
pytest -m "not live" -q                   296 passed
ruff check .                             passed
pyright                                  0 errors, 0 warnings
pnpm --dir apps/decision-desk test       9 passed
pnpm --dir apps/decision-desk build      passed
docker compose config --quiet            passed
```

前端隔离 smoke 随后在当前源码上完成：页面提交返回 `202`，replay worker 完成后得到 `research_only`、16.7% hard coverage、5 个 hard gap、12 条规范化 SSE；截图和完整 API 摘要见 [`G1/G2 前端运行链记录`](../evaluations/G1_G2_FRONTEND_RUNTIME_SMOKE_2026-08-30.md)。首次并发启动全新数据库时曾观察到 Alembic `table events already exists` 竞态，已改用 API 健康后的顺序启动并记录为后续 migration lock 任务；这不构成并发首次迁移已解决的证明。

这些结果证明 G1 失败处理和 G2-A/B 事实包/回放实现通过离线门；它们不代表 Search 已稳定、事实覆盖已完成真实验收或 DSH 已 Promotion。G2-C 真实外部 canary 仍须 owner 明确确认后单独执行并记录，G2-D 在其后完成。
