# G1/G2 研究可靠性与事实覆盖实施方案

版本：`EXEC-R2-R-G1-G2-2026-08-30.v1`
状态：`G1/G2-A/B implemented offline / G2-C live Search failed safely / G2-D E2-L pending`（owner 已授权 G1、G2）
上级总计划：[产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)
G1 阶段卡：[R2-R-07 Search Reliability 与 Error Provenance](R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)
关联 ADR：[ADR-0011 研究失败语义与事实覆盖边界](../decisions/ADR-0011-research-reliability-fact-boundary.md)

> 本文是本阶段的唯一执行方案。G1 先解决“系统是否会错误处理失败”，G2 再解决“系统能否拿到足够可靠的事实”。两者共享现有 Product Kernel、DSH Runtime adapter、LangGraph 外层 Evidence Round、MCP Gateway、canonical schema 和 Research Command Center，不创建第二套 Agent Loop、账本、搜索协议或状态机。G1-A/B/C/D 与 G2-A/B 已实现并通过离线验证；G2-C 的真实 Search canary 已执行但 timeout，G2-D 需在可用且新鲜的 live capability 出现后完成 E2-L 事实充分度验收。

## 1. 授权与目标

### 1.1 已授权范围

Owner 已明确授权本目标：完成 G1 与 G2 的设计、实现和自动自测。授权范围包括：

- G1：PIT 时间所有权、错误 provenance、并行 capability 失败隔离、失败 Run 的 API/UI 投影；
- G2：`crypto_macro` 最小事实包、来源优先级/回退、replay fixture、受控 live canary 和事实充分度；
- 按 SDD -> BDD -> TDD -> 回放/canary -> 文档/状态/变更记录执行。

授权不包括自动交易、ASR、PPT、第二领域、公共插件安装、多用户、Postgres、队列、active pointer Promotion 或扩大默认网络权限。

### 1.2 不变产品目标

```text
输入 TextEnvelope / Event
  -> Trigger Snapshot（触发时事实）
  -> DSH tool/subagent loop（受限、可替换）
  -> Gateway 生成可信时间并归一化 capability 结果
  -> Evidence/PIT/authority/conflict/sufficiency 代码 Gate
  -> 必要时继续同一 Session，或有界停止
  -> Decision Snapshot + 根因链 + 30m/24h/72h
  -> deterministic Gate / Research View / Outcome
```

Agent 只能提出搜索和研究候选；可信时间、Evidence、Sufficiency、Gate、账本和发布权仍由代码持有。

## 2. 当前基线和已知失败

真实隔离 Run：`run_488b389ad8674dcfb632d12ea7b2399c`。

- DSH 主动规划 6 类缺口：事件身份、政策/数据变化、预期定价、宏观传导、BTC 现货、衍生品拥挤度；这证明内层 Agent Loop 已工作。
- 模型将 `observed_at` 填成 `cutoff_at`；网络响应晚到后被 PIT 正确拒绝，但上层被粗略映射为 `provider_timeout`。
- 首个并行 MCP 调用失败后，其余调用被 abort，已完成结果没有按 capability 独立投影。
- 研究详情可能把失败 Run 显示成运行中，Owner 看不到具体 stop code、失败 capability 和 retryability。

正确结果应是“失败可解释、无伪证据、无方向性发布”，不是让模型猜一个时间或让 Owner 手工补数据。

## 3. 技术边界和复用矩阵

| 能力 | 复用组件 | 本阶段薄层 | 禁止 |
|---|---|---|---|
| Agent loop | DSH Python SDK `0.1.1rc1`、受限 profile | 不改 loop，只读取可信通知/结果 | 自写 ReAct/Session 状态机 |
| 外层研究 | LangGraph StateGraph/checkpoint | Round 路由、恢复、Sufficiency | 在 node 中解析 Provider 协议 |
| capability transport | 官方 MCP SDK、OpenAI SDK Responses Search | canonical query/result、超时和 provenance | 自写 JSON-RPC/HTTP 搜索客户端 |
| 时间与 PIT | Kernel Evidence Gateway、Replay fixture clock | server-owned `observed_at`、`received_at`、cutoff 校验 | 接受模型时间戳作为事实 |
| 错误 | 现有 `ResearchCapabilityError`、`AgentExecutionError`、Trace error_code | 统一 `origin/cause/retryable` 投影 | `except Exception` 统一成 timeout |
| 事实覆盖 | `crypto_macro` pack requirements、source manifest | 来源优先级、回退、fixture/canary | 无限找站点或把搜索摘要当官方 |
| UI | Research Query/View/SSE、Zod/React | stop/error/failed capability 可读显示 | raw DSH JSON/Provider payload |

## 4. Canonical 契约变更原则

跨模块字段必须先修改 `contracts/schemas/agentic_research.schema.yaml`，再运行 codegen，不能手改生成文件。尽量复用现有 `error_code`，只在无法表达 provenance 时增加字段；历史 schema 和历史记录不可重写。

### 4.1 Query 时间投影

模型/DSH 请求仍可带兼容性的 `requested_observed_at`，但 Gateway 生成：

- `effective_observed_at`：网络 capability 由服务端/adapter 在 response 收到时生成；replay 来自 fixture manifest；
- `received_at`：Gateway 收到并解析结果的 UTC 时间；
- `cutoff_at`：Run/Decision Snapshot 代码生成并冻结的边界。

模型提供的时间仅作为诊断，不参与 PIT 通过判断。若来源自己提供了发布时间，保留为 `published_at`，不能冒充 `observed_at`。

### 4.2 错误 Provenance

每个工具调用和 Research Trace 至少能回答：

```text
error_code       最具体稳定码
origin           provider | transport | mcp | dsh | gateway | pit | orchestration
cause_code       下层原因（可空）
capability_id    哪个能力失败
tool_call_id     哪次调用
retryable        是否允许有界重试
deadline_ms      使用的能力预算
```

建议稳定码：

| 码 | 含义 | retry |
|---|---|---|
| `research_pit_violation` | 结果时间晚于 cutoff 或时间不可信 | 否（应改 query/等待新 Run） |
| `search_provider_failed` | 搜索 transport/provider 明确失败 | 是，受总预算限制 |
| `research_capability_timeout` | 单 capability 超过审计 timeout | 是 |
| `dsh_tool_failed` | DSH/MCP tool call 未完成或返回工具错误 | 视 cause |
| `provider_timeout` | 外层模型/Provider 总 deadline 超时 | 是，最多一次 |
| `research_capability_not_enabled` | capability 未通过 manifest/owner enable | 否 |
| `research_result_invalid` | canonical result/schema/hash 无效 | 否 |

## 5. G1 任务设计

### G1-A：时间所有权

实现位置：`research_evidence.py`、research adapter、MCP request projection、replay clock。

Red 测试：模型请求携带 `observed_at == cutoff_at`；网络 response 之后到达；旧 query 没有新诊断字段。

Green 行为：Gateway 忽略模型时间作为可信 PIT，写入服务端有效时间；晚到结果稳定报 `research_pit_violation`；replay 使用 fixture 时间。

退出断言：任何模型输出都不能把未来证据变成 PIT 内证据；历史数据和 schema 兼容；PIT 错误不再被压成 timeout。

### G1-B：错误归一化

实现位置：`ResearchCapabilityError`、runtime error mapper、MCP adapter、Trace/Result mapper。

Red 测试：分别注入 HTTP 5xx、429、连接超时、MCP `ToolError`、PIT 拒绝、结构化结果错误和 DSH Session 超时，检查 origin/cause/retryable。

Green 行为：保留最具体错误码，向上层只做有损级别提升，不覆盖具体 cause；日志/Trace 不含 secret/raw payload。

退出断言：同一失败在 fake/replay/live adapter 上拥有一致 canonical code；retry policy 只由代码执行。

### G1-C：并行失败隔离

实现位置：DSH/MCP capability dispatch adapter、outer round 汇合节点、trace projection。

Red 测试：A capability 成功、B capability 失败、C capability 超时；全失败；critical capability 失败；已完成结果必须保留。

Green 行为：每个调用有独立 attempt/timeout/result；失败不删除其他结果；只有 Sufficiency/critical policy 决定 round 是否停止；已完成证据先入 canonical candidate 再汇合。

退出断言：部分成功可见但不足时 `research_only/reject`；不因一个 MCP error 丢失全部 Trace；不允许通过 partial result 发布方向性结论。

### G1-D：失败 Run 人可读投影

实现位置：Research Query/View service、API/SSE、`apps/decision-desk/src/research`。

Red 测试：Run 已 failed/degraded/rejected 但无 Result；Result 有 stop reason；父 Run retry/recheck；SSE 断线续接。

Green 行为：`status` 根据 durable Run/Artifact/Result 终态计算；显示 stop code、error code、origin、failed capability、retryability、已保留证据数；retry/recheck 创建 child Run。

退出断言：页面永不把 durable failed Run 显示为 researching；默认不展示 raw JSON；命令幂等且不修改父 Run。

## 6. G2 最小可靠事实包

G2 不追求“抓遍互联网”，只为 `crypto_macro.v1` 的高影响事件锁定一组最小、可验证、可回放的事实需求：

| Requirement | 最小事实 | 首选 | 回退 | 失败语义 |
|---|---|---|---|---|
| `event.identity` | 讲话/会议/发布的官方身份、时间、原文 | Federal Reserve 官方 | 已审计新闻/搜索摘要 | 无身份则 stop |
| `policy.delta` | 新措辞、政策路径、与前次差异 | 官方 speech/statement | 官方 RSS + document fetch | 无原文则 stop |
| `expectation.pricing` | 利率隐含概率/预期变化 | 已审计市场/官方可引用数据 | 搜索仅作 lead | 缺失则不做方向性结论 |
| `macro.transmission` | DXY、2Y/10Y、实际利率/通胀链 | FRED/官方统计 | 审计 aggregator | 缺失则 research_only |
| `crypto.spot` | BTC 现货、关键区间、成交/波动 | OKX/受审计交易所 | 另一已审计交易所 | 缺失则 no_trade |
| `crypto.derivatives` | funding、OI、基差、清算 | 交易所衍生品 API | 受审计 aggregator | 缺失则 no_trade |

### G2-A：Manifest 与来源优先级

为每个 requirement 固定 `source_priority`、`authority`、`freshness_seconds`、`minimum_independent_sources`、`allowed_fallbacks` 和成本/timeout。manifest 不能由模型修改；新增来源先走许可证、安全、schema、PIT、replay 和 owner enable。

### G2-B：Replay fixture

至少为上述六类 requirement 各提供一组成功、一组 stale/缺失、一组 provider failure fixture。fixture 固定 `published_at/observed_at/received_at/cutoff_at`、source hash、authority 和内容引用；测试不得访问互联网。

### G2-C：受控真实 canary

G1 通过后只运行一次隔离真实事件 canary：

- 只读 HTTPS、显式 allowlist、20 秒 capability deadline、总 Run deadline 180 秒；
- 临时 SQLite/Session 目录，凭据只进当前进程；
- 记录成功/失败 capability、错误 provenance、响应数量、延迟、估算成本和 Evidence 数；
- 不写真实业务账本、不发送通知、不修改 active pointer；
- 成功与失败都保存脱敏报告，作为 G3 prospective pilot 的起点。

### G2-D：事实充分度

Sufficiency Gate 的结果必须是确定性的：

- 六类 requirement 中关键项达到 authority/freshness/独立来源要求，才能 `sufficient`；
- 任一 critical requirement 缺失、冲突或不可认证，最多输出 `research_only/no_trade`；
- Search 摘要不能单独满足官方或市场原生数值 requirement；
- 不允许为满足覆盖率而放宽 PIT、authority 或 hash。

## 7. 实现顺序与每卡停止条件

```text
R2-R-G1-A 契约/时间 Red
  -> G1-A Green + replay
  -> G1-B 错误 Red/Green
  -> G1-C 并行 Red/Green
  -> G1-D API/UI Red/Green
  -> G1 全量回归
  -> G2-A manifest
  -> G2-B fixture/replay
  -> G2-C 一次 live canary（待 owner 明确确认）
  -> G2-D sufficiency/报告（待 canary 证据）
  -> 文档/状态/owner 价值决策
```

每一张卡都必须满足：

1. 先写失败测试，再写最小实现；
2. 不改变历史数据和 active pointer；
3. 更新受影响模块 README、状态、路线图和 CHANGELOG；
4. 失败两次仍需补丁时停止，回到 ADR/架构评审；
5. 不顺手做 G3/G4/G5/G6。

### 7.1 当前执行收口

| 卡片 | 当前状态 | 证据边界 |
|---|---|---|
| G1-A/B/C/D | `implemented / offline exit passed` | PIT、provenance、并行部分成功和失败 Run API/UI 已有专项及全量回归；不代表真实 Search 长期稳定 |
| G2-A | `implemented / offline exit passed` | `source_manifest.yaml` 固定六类事实、优先级、权限、鲜度和回退；manifest 不由模型修改 |
| G2-B | `implemented / replay exit passed` | 六类事实各有 success/stale/provider_failure fixture；普通测试永不触网 |
| G2-C | `pending owner canary` | 只读、allowlist、临时目录、单次限时；未确认前不执行 |
| G2-D | `pending acceptance` | 需根据 canary 结果验证 authority/freshness/source-count，并保留缺口/冲突的 fail-closed 结论 |

## 8. 完整 BDD/TDD 退出门

### BDD

- 服务端生成可信时间，模型时间不能绕过 PIT；
- 单个 capability 失败不吞掉其他结果；
- 失败 Run 在 API、SSE 和 UI 都显示真实终态、错误来源和下一步；
- 最小事实包缺关键证据时安全停止，不编造方向；
- replay 与 live 使用同一 canonical contract，live 失败可重现为 fixture。

### TDD/静态/集成

```text
pytest -m "not live"
targeted tests: tests/research tests/capabilities tests/runtime tests/orchestration tests/operations
tools.contract_codegen check
tools/docs/check_module_docs.py
ruff check .
pyright
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
git diff --check
```

G2 额外执行 `tools/research_acceptance.py` 和显式授权的单一 live canary；普通 CI 永不触网。

## 9. 阶段完成后的决策

G1/G2 通过只说明失败处理和事实获取边界可靠，不代表 DSH Promotion 或交易收益。完成后必须在 [产品收口总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md) 的 G3 入口做一次选择：

- `continue prospective pilot`：进入固定观察窗口，收集 owner usefulness 和 Outcome；
- `retain baseline`：DSH 保持 shadow，不继续加功能；
- `stop candidate`：候选成本/延迟/覆盖/价值不足，冻结候选并保留证据。

没有该决策，不自动进入 ASR、第二领域、PPT、多用户或远程部署。

## 10. 当前需要 owner 做的事

Owner 已授权 G1/G2，技术实现和离线自测由 Agent 完成。当前不需要 owner 提供接口、手工时间戳或协助重试。只有以下两个产品边界需要在相应时点确认：

1. G2 live canary 前，确认允许一次限时、只读、临时目录的外部 Search 访问；
2. G1/G2 结束后，填写 owner usefulness 并选择 `continue / retain / stop`。

在 canary 之前，Agent 可以自行完成所有 fake/replay/静态/集成检查；凭据和真实数据不会写进文档或仓库。
