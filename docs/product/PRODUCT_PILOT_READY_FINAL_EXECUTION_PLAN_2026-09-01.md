# Decision Hub Pilot Ready 最终执行与验收书

版本：`PILOT-READY-CLOSEOUT-2026-09-01.v2`
状态：`executed / historical taskbook / superseded by delivery control book`
Owner 决策：全部建议已接受，PR-00..08 已完成并验收
产品状态：`pilot_ready=true / research_only / no automatic trading`
最后更新：2026-09-01

> 本文保留 PR-00..08 的实施和反例证据，不再是当前执行入口。当前状态、E2-L 完成
> checklist 和 E3 停止线以
> [产品交付控制书与最终验收包](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准，
> 真实 Run/Session/页面/测试证据见
> [E2-L 官方 DSH Web 验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。
> [v1 最终产品收口实施任务书](PRODUCT_CLOSEOUT_FINAL_IMPLEMENTATION_TASKBOOK_2026-09-01.md)
> 保留为已完成 Search attribution 和安全降级工作的历史任务书，不再独立扩大任务。
> canonical schema、accepted ADR、当前 Stage Charter 和模块 README 仍是更高优先级事实源；
> 若冲突，先修正文档或补 ADR，禁止在代码中暗自改变架构。

## 0. 本轮唯一目标

把当前已存在的 DSH-first 工程闭合成一个真正可试用的单 owner 产品：

```text
用户只进入 Official DSH Web 的 Crypto Macro Trader 工作台，
手工提交文本或由后台事件触发研究后，DSH 能主动识别事实缺口、
在代码预算内调用已审计能力补证、重新评估并形成候选；
Decision Hub 持久保存事实、PIT、过程、失败和报告，由确定性 Gate 裁决；
Official DSH Web 显示日常研究结果，Decision Desk 显示同一 Run 的审计和资产。
```

最终成功不是“接口能返回”，而是以下结果同时成立：

1. Official DSH Web 是唯一日常入口，交易员业务身份、状态和报告可见；
2. 同一 DSH Session 至少完成一次“发现缺口 -> 调能力 -> 保存 Evidence -> 重新评估”；
3. 工具、轮次、成本和 deadline 全部由代码硬限制，模型不能越界；
4. Session 在第一条 Prompt/Tool Call 前已经与 Hub Run 建立耐久关联；
5. Search 没有精确 citation 就不产生 claim-bearing Evidence；
6. Evidence 或合成不足时可解释性停止，不伪造 `no_trade` 或方向性结论；
7. DSH Web 与 Decision Desk 对同一 Run 的状态、覆盖、失败和报告一致；
8. 代码 readiness 返回 `pilot_ready=true` 后立即停止功能扩张，进入 E3 价值观察。

## 1. 产品停止线

本轮交付的产品形态固定为：

```text
单 owner + 单机/Docker + 外部大模型 API + 只读研究 + Crypto Macro Trader
```

本轮明确不做：

- 自动交易、下单、扣费或写入交易账户；
- ASR、直播音频、PPT、A 股、美股第二 Domain Pack；
- 多用户、租户、计费、插件市场、社区插件自动安装；
- 切换历史 active runtime、删除或改写历史 Run/Evidence/Artifact/Forecast/Outcome；
- fork 或复制 DSH 上游前端；
- Redis/Kafka/Celery/Temporal/DBOS/Postgres/Kubernetes；
- 第二套聊天、第二套 ReAct/Supervisor/tool loop 或第二本业务账本。

ASR 只保留契约入口：

```text
Audio/Live Stream -> AsrProviderPort -> TextEnvelope -> 完全相同的研究主线
```

只有 Pilot Ready 通过并完成 E3 价值观察后，才讨论 ASR 或第二领域。避免用新功能掩盖
当前研究效果、事实质量或工程边界问题。

## 2. 当前代码和真实运行基线

### 2.1 已完成且必须复用

| 能力 | 当前事实 | 本轮处理 |
|---|---|---|
| Official DSH Web/Harness | 官方 Workspace、Session、Tool/Skill/Subagent/MCP、Trajectory、JSONL、compaction 已存在 | 复用，不 fork，不重写 Agent Loop |
| DSH Native Plugin | typed intake、Run/Session bridge、业务卡、命令和 Desk 链接已存在 | 在官方 seam 上修复和验收 |
| Hub Kernel | Event/Run/Evidence/PIT/Sufficiency/Gate/Artifact/Forecast/Outcome/Evaluation/Outbox 已存在 | 保持唯一可信账本 |
| LangGraph | checkpoint、lease、recovery、外层 evidence round、幂等提交已存在 | 只修 durable lifecycle，不加入第二 Supervisor |
| Capability Gateway | manifest、deny-by-default、timeout、错误 provenance、Evidence 即时入账已存在 | 增加原子预算边界 |
| Capability | Fed Official、FRED、CoinEx、Responses Search 已经真实触网 | 保守使用，Search 无 citation 时 fail-closed |
| Search attribution | `url_citation` exact span 才能生成 Search Evidence | 已完成，不回退到全局摘要 |
| Synthesis attestation | 无法证明的因果/预测语义会被丢弃，可信 Evidence 可降级保存 | 已完成，做回归保护 |

### 2.2 当前 live 反例，禁止掩盖

隔离 live Run：

```text
run_id:     run_773e50032b0f4c3d929f45f5028d53e1
session_id: dsh_f66aac8b72d82bc0de3b4940290d1d2607ac8cdc2db0c8a7ebacfc7686afb615
budget:     max_tool_calls=12
observed:   total_tool_calls=13
```

该 Run 已证明 DSH 会主动调用 Fed、FRED、CoinEx 和 Search，并把 hard coverage 从
`0% -> 33% -> 50% -> 66.7%`；也证明当前产品仍未通过：

1. **代码级工具预算越界**：Graph 在整个 Harness round 返回后才检查累计次数；下一轮又
   把完整预算传给 DSH，Gateway 在真实触网前没有原子 slot reservation；
2. **模型复制运行身份导致错误路由**：真实 JSONL 已证明失败调用把正确 Session ID
   `dsh_f66a...c8cdc2db0c8a7eb...afb615` 截断成
   `dsh_f66a...c8cdc2db0...afb615`；同一轮使用完整 ID 的 Official/Market/Search 调用均
   成功。因此直接根因不是已证实的数据库可见性竞态，而是系统错误地要求模型复制一段
   不应属于业务参数的长运行身份；
3. **Prompt 就绪顺序仍有独立缺陷**：Host 当前先 queue Prompt、后回调 Hub accepted。
   这次失败不是由它直接造成，但该顺序没有形成可证明的 fail-closed 就绪门，仍必须作为
   防御性边界修复；
4. **Readiness 语义过时**：旧 `/v1/pilot/readiness` 面向 Hub 自己的 LLM Provider，不能
   代表 DSH-first 产品；也不能通过重新启用 Hub 第二条 LLM 路径制造 ready；
5. **入口误操作风险**：用户可能在 typed intake 后误点 DSH 原生“发送消息”，进入普通
   Chat 而不创建 Hub Run。产品必须明确主业务 CTA 和 Run 关联状态，但不能破坏原生 Chat。

这些 Run 和 Trace 都是失败证据，必须保留，不修改、不删除、不把计数 clamp 成 12。

## 3. 最终用户视图

### 3.1 Official DSH Web：唯一日常入口

```text
┌ Official DSH Web / Crypto Macro Trader ────────────────────────────┐
│ DSH 原生：Workspace / Session / Chat / Plan / Tool / Trajectory   │
├ Decision Hub 业务入口 ─────────────────────────────────────────────┤
│ 研究目标输入                    [建立研究任务]                    │
│ 当前 Run / Session / 状态 / generation / deadline / 剩余预算     │
├ 主动研究进度 ──────────────────────────────────────────────────────┤
│ 已确认事实 | hard gaps | 正在调用 | 下一步 | StopReason           │
├ 证据覆盖 ──────────────────────────────────────────────────────────┤
│ requirement | source | authority | PIT | freshness | conflict     │
├ 人可读报告 ────────────────────────────────────────────────────────┤
│ 发生了什么 | 相对变化 | 根因链 | 最强反方 | 30m/24h/72h          │
│ Trigger | Invalidation | Gate | 缺失事实 | 下次复查              │
├ 失败与降级 ────────────────────────────────────────────────────────┤
│ error_code | origin | retryable | 已保留 Evidence | 可执行动作     │
├ 动作 ──────────────────────────────────────────────────────────────┤
│ 取消 | child Run 重试 | 复查 | 打开 Decision Desk                │
└────────────────────────────────────────────────────────────────────┘
```

交互约束：

- “建立研究任务”是创建有账本、有 Gate 研究的唯一主 CTA；
- 原生“发送消息”保留给当前 Session 的普通讨论，不伪装成正式研究；
- 提交成功后必须立即显示 Run/Session 关联，不能只显示“已接收”；
- `queued/running/partial/research_only/failed/cancelled/completed` 都有稳定可读状态；
- `insufficient` 显示缺什么、尝试了什么、为何停止，不复制相同模板到三个 horizon；
- Evidence-only 降级不显示未经 attestation 的因果链和预测；
- 页面不展示大段裸 JSON，原始 Trace 仅在审计详情按需展开。

### 3.2 Decision Desk：管理、审计和资产后台

Decision Desk 不是第二个聊天入口。它显示：

```text
Operations/Readiness/Worker/Capability/Outbox
Run Inspector/Evidence/PIT/Trace/Sufficiency/Gate
Artifact/Forecast/Outcome/Evaluation/Experience/FailurePattern
```

两处页面只通过 Hub typed Query/View 读取同一个 Run，不直读 DSH JSONL、LangGraph state
或数据库表。DSH JSONL 保留交互轨迹，Hub Ledger 保留产品事实，两者通过
`run_id <-> dsh_session_id` 互相定位但不互相替代。

### 3.3 后台主动运行

人工和后台触发必须汇合到同一主线：

```text
人工文本 / 日历 / 新闻发现
  -> TextEnvelope / Event admission / idempotency
  -> durable Hub Run
  -> 在 Hub 事务中 reserve DSH Session link + generation + budget
  -> Host 确认 accepted/link durable 可见
  -> 才允许 queue Prompt
  -> DSH 唯一 Agent Loop 规划并调用 Research MCP
  -> Gateway 原子占用剩余 tool slot，再触网
  -> Trace/Result/Evidence 即时入账
  -> deterministic Sufficiency 重新计算 gaps
  -> 有缺口且剩余预算允许：同一 Session 下一 generation
  -> 充分或有界停止：DSH 提交 synthesis candidate
  -> Evidence attestation + deterministic Gate
  -> Report/Outbox/Outcome/Evaluation/Experience
```

后台 worker 负责 schedule、lease、heartbeat、restart recovery 和通知；DSH 负责推理和
工具选择；Hub 负责可信边界和发布。所谓“自主”不是无限循环，而是在给定事件、权限、
事实需求和硬预算内主动选择下一步，并在信息不足时解释性停止和安排复查。

## 4. 最终组件所有权

| 组件 | 唯一职责 | 明确禁止 |
|---|---|---|
| Official DSH Web/Harness | 唯一内层 Agent Loop、Session、模型、Tool/Skill/Subagent/MCP、Trajectory、JSONL、compaction | Hub 账本、PIT、Gate、Forecast、交易权限 |
| DSH Native Plugin | typed intake、Run/Session link、业务状态/报告、命令桥、Desk deep link | fork 上游 UI、第二聊天、第二 loop、直接写账本 |
| Hub Kernel | Event、Run、Evidence、PIT、Sufficiency、Gate、Artifact、Forecast、Outcome、Evaluation、Outbox | import DSH/LangGraph/Provider/UI；运行模型 loop |
| LangGraph | 外层 checkpoint、lease、recovery、bounded evidence generation、幂等 commit | ReAct/Supervisor/tool selection、Provider client、发布决策 |
| Research MCP/Gateway | capability manifest、权限、schema、PIT、freshness、hash、原子预算、timeout/retry/error provenance | 修改 Gate、伪造 Evidence、自动安装插件 |
| Crypto Macro Pack | doctrine、requirements、source ladder、Profile/Role、Gate 参数、eval、fixtures | 通用账本、运行时、前端状态机 |
| Decision Desk | typed Query/View、运维、审计和资产 | 第二聊天、直读 SQL/JSONL/checkpoint |

正式只允许两层循环：

```text
DSH 内层：规划 -> 选能力 -> 看结果 -> 找缺口 -> 再行动 -> 候选/停止
Hub 外层：触发 -> durable Run -> bounded generation -> Gate -> 通知 -> Outcome/Evaluation
```

LangGraph 不需要成为第二个“领导者”。Supervisor 能力由 DSH Harness 负责；LangGraph
只是可恢复的产品生命周期编排器。

## 5. 插件和未来复用模型

交易员不是一份 fork 后的 DSH，也不是把所有金融逻辑塞进一个 UI 插件。它由四层组合：

| 层 | 当前落点 | 可复用资产 |
|---|---|---|
| DSH Native Plugin | `extensions/dsh/decision-hub/` | Official DSH 中的 intake、Run 状态、报告和命令 seam |
| Product Extension | `extensions/products/crypto_macro/` 目标边界；已有实现暂按任务迁移 | 领域业务输入/输出/命令/视图，不拥有通用账本 |
| Domain Pack | `packs/crypto_macro/` | doctrine、EvidenceRequirement、source ladder、Profile、Gate 参数、eval、fixture |
| Capability Plugin | `packages/provider_adapters/` + manifest | Search/Official/Market/Notification 的 typed、可审计、可替换能力 |

未来 PPT 等第二个真实产品出现时：

```text
Official DSH Web
  + 同一个 Decision Hub Native Plugin
  + PPT Product Extension
  + PPT Domain Pack/Profile/Capabilities
  + 同一 Hub Run/Artifact/Evaluation 基础资产
```

只有第二个真实调用方出现，才提取两域实际共享的 Platform Core 接口。禁止提前创建
空泛 `common/utils/helpers`。DSH 上游升级走 `infra/dsh/upstream.lock.json`、版本/hash
核对和官方插件 seam；不修改 node_modules，不复制 DSH 前端源码。

## 6. 本轮代码修改地图

```text
packages/orchestration/langgraph/graphs/agentic_research_graph.py
  - 每个 generation 只传 remaining tool/cost/round budget
  - 根据代码计数决定 next generation/StopReason

packages/workbench_adapters/durable_research_gateway.py
packages/kernel/decision_hub_kernel/application/research_observability.py
packages/kernel/decision_hub_kernel/persistence/db.py
  - 真实 capability 执行前原子 reserve tool slot
  - 并发和跨进程不能超额
  - idempotent request 不重复占用
  - 超额返回结构化 ErrorProvenance

extensions/dsh/decision-hub/src/host/
packages/kernel/decision_hub_kernel/application/dsh_sessions.py
  - accepted/link durable 可见后才 queue prompt
  - duplicate/retry/generation 保持幂等

extensions/dsh/decision-hub/src/research-tool.ts
apps/research_mcp/
  - 通过 DSH 官方 ToolDefinition.execute(args, exec) 注册受信 Research Tool
  - 从 exec.agent.id 注入 Session 身份，模型参数中彻底删除 research_session_id
  - 复用 canonical ResearchCapabilityQuery/Result 和既有 Durable Gateway
  - 原始 MCP capability tool 不再暴露给 decision-research Agent

apps/hub_api/ + packages/query_views/operations/
extensions/dsh/decision-hub/ + apps/decision-desk/
  - DSH-first product readiness
  - typed intake 主 CTA、剩余预算、StopReason 和同 Run 状态投影

tests/research/ + tests/orchestration/ + tests/dsh_native/
tests/operations/ + extensions/dsh/decision-hub/tests/
  - Red/Green、并发、恢复、前后端一致性和失败语义
```

只有失败测试证明需要时才改 canonical schema/migration。若新增持久字段，必须：

```text
schema -> codegen -> migration -> persistence -> Query/View -> tests -> module README -> ADR
```

不得为修复本轮问题改写历史数据或绕开公开契约。

## 7. 执行任务与 Checklist

### PR-00：文档和真实基线

- [x] Owner 接受 DSH-first、Hub 控制面、LangGraph 外层和四层插件模型；
- [x] 新建本文并声明为当前唯一执行入口；
- [x] 记录 Search attribution 已完成和 live Run 预算/身份截断反例；
- [x] 同步 `INDEX`、产品索引和当前决策索引；
- [x] 生成本任务 Task Context Manifest；
- [x] `git diff --check` 和 module docs check 通过。

退出门：现状、目标、非目标、视图、所有权、任务和停止线只有一个当前版本。

### PR-01：代码级工具预算边界

先写 Red 测试：

- [ ] 第 `max_tool_calls + 1` 个不同能力请求在 adapter/网络调用前被拒绝；
- [ ] 两个并发请求争夺最后一个 slot 时，最多一个进入 adapter；
- [ ] 相同 `request_id` replay 幂等，不重复占 slot，不重复触网；
- [ ] 被预算拒绝的请求生成 `research_tool_budget_exhausted` provenance；
- [ ] 实际 adapter invocation 数永远 `<= max_tool_calls`；
- [ ] Graph 最终 StopReason 为 `tool_budget`，不是模型自述。

实现：

- [ ] Gateway 在真实执行前用数据库事务原子 reserve；
- [ ] reservation 与 `run_id/request_id` 唯一，完成/失败保持可审计状态；
- [ ] 下一 generation 的 Request 只带 remaining budget；
- [ ] Graph 累计数来自耐久账本/结果，不 clamp、不重置；
- [ ] 取消、Provider failure、persistence failure 的计数语义固定并写 README/测试。

正式计数语义：

```text
一个新的、通过权限和 Session 校验、即将进入 capability adapter 的 request_id
占用一个 tool slot；slot 一经开始执行不退还。
相同 request_id 的幂等 replay 不再占 slot，也不重复触网。
被 Session/权限/schema/预算在 adapter 前拒绝的调用不算已执行 slot，
但必须记录独立的 rejected trace/provenance。
```

退出门：单线程、并发和跨进程测试证明实际执行不越界；旧 Run 保留 13/12 反例。

### PR-02：受信运行身份和 Prompt 就绪门

先写 Red 测试：

- [ ] 模型可见的 Research Tool schema 不包含 `research_session_id`；
- [ ] Tool body 只从 `exec.agent.id` 注入完整 Session 身份，拒绝 agentless 调用；
- [ ] 即使 Prompt 中没有 Session ID，新 Run 第一条 tool call 也不会错误路由；
- [ ] 原始 MCP capability tool 不暴露给 `decision-research` Agent，不能绕开受信 wrapper；
- [ ] 错误/截断/伪造 Session ID 无法通过任何模型参数进入 Gateway；
- [ ] Hub accepted callback 失败时 Host 不 queue prompt；
- [ ] duplicate submit/restart 只 queue 一次同 generation prompt；
- [ ] generation N+1 复用同 Session，但 round/link 已耐久更新后才 prompt；
- [ ] Host/Hub/MCP 跨进程可见性有集成测试。

身份和执行顺序固定为：

```text
Hub reserve link + generation
-> Host reserve official Session
-> Hub accepted/link durable acknowledgement
-> Host queue prompt
-> DSH Agent 调用 decision_hub_research（无 Session 参数）
-> DSH ToolRuntime 以 exec.agent.id 注入受信 Session 身份
-> canonical ResearchCapabilityQuery -> Durable Gateway -> capability adapter
```

正式实现只使用 DSH 官方 `ctx.tools.register(defineTool(...))` 和
`ToolDefinition.execute(args, exec)` seam；不修改 DSH 上游源码、不从 `request_id` 猜 Run、
不做 Session 前缀/模糊匹配。原始 MCP tool 可保留给独立 canary/非 DSH 客户端，但在
`decision-research` Agent 的可见工具面中必须被替换为受信 wrapper，不能同时暴露两条
等价执行路径。

若官方 DSH API 限制只能先建 Session 才 accepted，允许创建空 Session，但绝不允许把业务
Prompt 放入队列后再建立 Hub link。失败时返回明确 409/503 和 retryable provenance。

退出门：全新 Session 的第一代和第二代都由 `exec.agent.id` 完整注入运行身份，模型无法
选择或改写身份；accepted 失败不入队，恢复后不重复 Prompt。

### PR-03：DSH-first 产品 Readiness

新增或重构一个代码拥有的 product readiness，至少检查：

```text
Hub /health/ready
Official DSH Host /decision-hub/v1/readiness
research worker heartbeat/freshness
capability manifest enabled + adapter canary status
DB migration/current schema
DSH upstream/plugin revision/hash
最近一次正式 live Run 的 Evidence/Gate/terminal evidence（首次部署可为 not_observed）
```

- [ ] 旧 `/v1/pilot/readiness` 标为 legacy 或组合进新视图，不启用 Hub 第二条 LLM；
- [ ] 返回每个 component 的 typed 状态和原因；
- [ ] `pilot_ready` 只能由代码计算，不能由文档/env 手工写 true；
- [ ] DSH Web 与 Desk 显示相同 readiness；
- [ ] 密钥值永远不返回。

退出门：健康组件全部通过且正式 live Run 成功/可信 `research_only` 后，代码才返回 true。

### PR-04：单入口产品体验和状态一致性

- [ ] Crypto Macro Trader Workspace 明确显示业务入口；
- [ ] typed intake 提交后立即显示 Run/Session link；
- [ ] 页面显示 remaining tool/round/deadline budget 和 StopReason；
- [ ] Chat 与“正式研究”的语义清楚，不拦截 DSH 原生 Chat；
- [ ] DSH 与 Desk 对同一 Run 的 status/coverage/failures/report 一致；
- [ ] partial/insufficient/synthesis failure/cancelled 不显示空白或重复 horizon 模板；
- [ ] 原始 JSON 只在审计详情按需展开。

退出门：用户从唯一 URL 可完成研究、看进度、读报告、定位审计，不需要手工调用 API/MCP。

### PR-05：后台主动主线和恢复回归

- [ ] 人工文本和 admitted background event 创建相同类型的 Run；
- [ ] scheduler/worker lease、heartbeat、restart recovery 通过；
- [ ] 同一事件幂等，不产生重复 Run/Artifact/Outbox；
- [ ] background Run 完成后在 DSH Workspace 和 Desk 可见；
- [ ] 通知只引用已提交 Artifact；失败/insufficient 不伪造交易建议。

退出门：至少一个离线/replay 后台事件完成相同主线；live 验收仍以 Official DSH Web 为主。

### PR-06：全量工程质量门

- [ ] `git diff --check`；
- [ ] `python -m tools.docs.check_module_docs`；
- [ ] `python -m tools.contract_codegen check`；
- [ ] `ruff check .`；
- [ ] `pyright`；
- [ ] `pytest -m "not live"`；
- [ ] `pnpm --dir extensions/dsh/decision-hub test -- --run`；
- [ ] `pnpm --dir extensions/dsh/decision-hub build`；
- [ ] `pnpm --dir apps/decision-desk test`；
- [ ] `pnpm --dir apps/decision-desk build`；
- [ ] `docker compose config --quiet`；
- [ ] migration `0001 -> latest`、backup/restore/integrity；
- [ ] LangGraph checkpoint/lease/cancel/resume/idempotent commit；
- [ ] cross-process recovery smoke；
- [ ] Search/Evidence/Sufficiency/Gate 专项回归。

退出门：所有检查真实执行并记录精确结果；不得删测试、放宽 Gate 或忽略失败制造通过。

### PR-07：全新隔离真实产品验收

必须使用新的 Compose project、数据卷、Workspace 和 Session，不能复用失败 Run 作为成功证据：

```text
单命令启动
-> product readiness
-> 打开 Official DSH Web
-> Crypto Macro Trader typed intake
-> durable Run/Session link
-> DSH 主动调用 Official/Market/Search
-> 至少一次重新评估
-> Evidence/Sufficiency/Gate/Report
-> 同一 Run 在 DSH 与 Desk 一致
```

- [ ] 实际 capability executions `<= max_tool_calls`；
- [ ] 第一条 tool call 无 Session 关联失败；
- [ ] Search citation 和 URL exact binding，或 `search_no_attributed_sources`；
- [ ] 至少保留一个 Official 和一个 Market 的可信 Evidence；
- [ ] 达到 success 或可信 `research_only`，失败原因与事实一致；
- [ ] 没有未经 attestation 的 causal/horizon；
- [ ] 375/768/1024/1440 和 desktop 页面无遮挡、溢出、空白；
- [ ] 浏览器 console 无未处理错误；
- [ ] 保存非敏感截图、revision/build hash、Run/Session、模型/pack/schema/capability 版本、延迟和成本；
- [ ] readiness 由代码返回 `pilot_ready=true`。

退出门：上述全部通过；任何真实失败都先回到对应 PR 根因，不在页面或文档中掩盖。

### PR-08：收口和停止开发

- [ ] 回填本文全部 Checklist；
- [ ] 同步 `IMPLEMENTATION_STATUS`、`CURRENT_STATE`、`CURRENT_DECISIONS`、`HANDOFF`、
  `ROADMAP`、`CHANGELOG`、相关 README；
- [ ] 新建最终 evaluation 记录，列出通过、失败、残余风险和未做范围；
- [ ] 保持 `research_only`，不自动 Promotion，不切 active pointer；
- [ ] 宣布进入 E3，停止新增工程功能。

## 8. BDD/TDD 核心场景

| 场景 | Given / When | Then |
|---|---|---|
| 最后一个 tool slot | 剩余 1，两个并发 request | 仅一个 adapter 执行，另一个预算拒绝 |
| 幂等 replay | 相同 request_id 重试 | 不重复占 slot、不重复触网、返回一致结果或稳定已处理语义 |
| 下一 generation | 已用 7/12 | DSH 只能获得 remaining=5，不重新获得 12 |
| 受信 Session 身份 | Prompt 不提供 Session ID，模型调用 Research Tool | Tool 从 `exec.agent.id` 注入完整身份并命中唯一 link |
| 身份不可伪造 | 模型尝试传截断/其他 Session ID | schema 不接受该字段，Gateway 不收到模型选择的身份 |
| accepted 失败 | Hub callback 不可用 | Prompt 不入队，可重试且不重复 |
| 无 Search citation | 有 output_text/action.sources，无 annotation | 无 Evidence，`search_no_attributed_sources` |
| 部分成功 | Official/Market 成功，Search 失败 | Evidence 保留，失败可读，Gate 不误发布 |
| 合成无证明 | Evidence 合法，模型引用错误 ID | evidence-only；无 causal/horizon |
| 有界停止 | tool/round/deadline/cost 任一到达 | 稳定 StopReason，不无限 loop |
| 恢复 | worker/Host/Web 中断后重启 | 同 Run/Session 继续，不重复 Prompt/Artifact/Outbox |
| 页面一致 | 同一 Run 在 DSH/Desk 打开 | 状态、覆盖、失败、报告一致 |
| 后台事件 | scheduler admitted event | 使用与人工 typed intake 相同 product loop |

开发顺序强制为：

```text
Spec/ADR/Schema -> BDD -> Red -> Green -> Refactor
-> targeted tests -> full offline gates -> fresh live acceptance
-> docs/status/evaluation
```

普通测试不触网、不读取真实密钥；真实 Provider 只用于隔离 canary/live 验收。

## 9. 全局工程约束

1. 长期文档中文；草稿和临时输出进入 `tmp/`，不把聊天推理当事实源；
2. 跨模块/跨语言契约只有 canonical schema 一个来源，改 schema 后 codegen，禁止双写；
3. Agent 只产生候选，deterministic Gate 唯一裁决，无自动交易/扣费/账本写权限；
4. 三时间戳、cutoff 和 PIT 不得放宽，历史事实只增不改；
5. 跨边界 Pydantic/Zod 运行时校验，禁裸 `dict/Any` 和私有代码硬引用；
6. Core 不依赖 DSH/LangGraph/Provider/UI，Harness 和 Provider 只通过 adapter/port；
7. 优先复用 DSH、LangGraph、OpenAI SDK、Pydantic、SQLAlchemy/Alembic 已有能力；
8. 禁止第二 Agent Loop、第二账本、第二聊天、第二 DTO 和自建 Provider 协议；
9. 插件 deny-by-default，只有 manifest、许可、安全、契约、回放、canary、Owner enable 后可用；
10. 密钥只在 ignored env/secret store，不进入代码、文档、日志、JSONL、fixture 或截图；
11. DSH 上游只通过 lock/hash/官方 seam 升级，不 fork、不改 `node_modules`；
12. 不引入没有真实调用方的抽象和基础设施；不创建 `common/utils/helpers` 垃圾层；
13. 同一根因两次最小修复仍失败，或需要放宽 Gate/PIT/authority 时，停止编码并写复盘/ADR；
14. 每个 PR 任务完成时同步局部 README、状态、CHANGELOG 和测试证据；
15. 保留 dirty worktree和历史数据，不回退、不删除、不提交、不 push，除非 Owner 单独要求。

## 10. 最终可用性与价值边界

### 10.1 Pilot Ready

只有 PR-00 至 PR-08 全部完成，代码才允许返回：

```text
pilot_ready=true
mode=research_only
automatic_trading=false
```

这表示产品可以由 Owner 开始真实使用，不表示策略已盈利、预测准确或 Runtime 已 Promotion。

### 10.2 E3 真实价值观察

Pilot Ready 后进入：

```text
至少 14 天或 20 个高影响事件，以后到者为准
```

记录：首证据延迟、最终报告延迟、事实覆盖、失败率、调用次数、成本、人工复核时间、
usefulness、30m/24h/72h Outcome、Brier、方向准确率和净收益。标签必须在 horizon 到期后
填写，不能提前使用未来信息。

E3 最终只允许三个结论：

```text
promote
retain_baseline
stop
```

若结果没有比通用助手或当前 baseline 更可靠、更及时或更省人工，应停止或保留 baseline，
不能继续堆插件和页面来掩盖价值不足。

## 11. 当前无需 Owner 再确认的事项

Owner 已接受本文全部建议，本轮不存在架构决策阻塞。可以立即从 PR-00 文档同步和
PR-01 Red 测试开始连续实施。只有出现以下情况才重新请求 Owner：

- 扩大外部网络域、付费额度或 Secret 权限；
- 修改 deterministic Gate、PIT、账本所有权或 active pointer；
- 引入第二 Agent Loop、第二领域、自动交易、多用户或新基础设施；
- 官方 DSH seam 无法满足“accepted before prompt”，需要 fork 上游；
- 需要删除/迁移历史数据。

本轮最终停止句：

> 当且仅当代码级预算、受信 Session 身份与 Prompt 就绪门、DSH-first readiness、Official DSH Web
> 真实补证主线、DSH/Desk 同 Run 视图、多视口/console 和全量质量门全部通过，标记
> `pilot_ready=true / research_only`；随后立即停止功能开发并进入 E3，不以继续写代码
> 代替真实使用价值验证。
