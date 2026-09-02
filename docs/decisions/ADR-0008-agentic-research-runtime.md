# ADR-0008 Agentic Research Runtime 与双层循环边界

日期：2026-08-29
状态：`accepted`（owner 于 2026-08-29 授权 R2-R）
关联阶段：[R2-R Agentic Research Runtime](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)
修订关系：补充 ADR-0005，不废弃其 Core/Harness 隔离原则；纠正 ADR-0007 之后正式研究主链仍停留在 fixed workflow 的实现偏差。若 owner 接受，本 ADR 只在“研究 Harness candidate”范围内修订产品架构基线 3.1/3.2 和治理规范第 6 节中“DSH 仅 Workbench、LangChain 是唯一正式 Agent loop”的旧选择；Core、PIT、Gate、候选先评测后 Promotion 等不变量不变。

## 决策摘要

Decision Hub 把正式“研究执行能力”从单次角色调用 `AgentRuntime` 中分离，新增可替换的 `ResearchHarnessRuntime` 产品端口：

- R2-R 首个真实 candidate 使用 DeepSeek Harness Python SDK 启动受限 `decision-research` profile；该 profile 基于完整 `sdk`/base 能力集，保留 Agent Loop、Web Search/Fetch、Subagent、Skill/MCP、Session log 和 telemetry，默认移除 shell、文件写入、任意插件安装和宿主凭据访问。
- 当前 fixed `policy_delta + counter_thesis + synthesis` graph 明确降级为 baseline/replay/fallback，不能继续被称为 agentic research。
- LangGraph 保留 Run 生命周期、证据轮次、双 Snapshot、checkpoint/recovery、充分度路由、确定性 Gate 和 commit；不自研通用 tool loop。
- DSH 管理一次 Research Session 内的模型/工具/子 Agent Step Loop；不能拥有 Product Kernel 账本、PIT、发布、Promotion 或交易权限。
- Evidence acquisition 采用 `Trigger Snapshot -> bounded research -> Decision Snapshot`；搜索或市场工具取得的新材料必须先规范化为 Evidence revision，再进入新的 Snapshot generation。
- 长研究从 FastAPI `BackgroundTasks` 移到 durable research worker；API 只 admission/查询，realtime worker 只发现/enqueue，research worker 执行/恢复。
- OpenAI Agents SDK 与 Pi 只保留未来 adapter 位置，本阶段不并行实现第二套真实 Harness。

## 背景

真实 Warsh 讲话 Run 已证明当前系统会识别 DXY、收益率、FedWatch 和 BTC 衍生品数据缺口，但由于正式 graph 没有 Tool node、Search/Market binding、充分度循环或真实 DSH Runtime，模型只能输出三个完全相同的 `no_trade / 52%`。这不是 Provider 模型能力问题，而是拓扑没有授权模型采取行动。

原产品架构和 ADR-0005 已要求避免重写 Harness 能力，但实现只交付了：

- 一个固定研究 graph；
- 一个只接受注入 callable 的 `DshAgentRuntime` 类；
- 一个未接入正式 graph 的 Search Capability seam；
- 一个只用于 Evolution candidate 且只读 frozen evidence 的 Supervisor。

因此 R2/R2-L 的“done”只能解释为工程底座退出门，不能解释为研究智能体完成。继续添加 Prompt、Reviewer 或单个接口会把架构缺陷变成更多补丁。

## 术语决策

- `AgentRuntime`：现有的单角色/单结构化调用契约，可继续服务 fixed baseline、简单 specialist 和 provider contract。
- `ResearchHarnessRuntime`：完成一次有状态研究会话的契约，允许 Harness 自己执行多 Step 工具循环、Subagent 和 continuation，并返回结构化结果和 trace references。
- `Product Evidence Round`：LangGraph 在一次 Harness Session 完成后执行的证据落账、充分度判断和有限 continuation；它不是另一套 tool loop。
- `Trigger Snapshot`：事件触发时已知事实的不可变快照。
- `Decision Snapshot`：研究结束时实际用于 Candidate/Gate 的不可变证据快照。

## 候选方案

### A. 在当前 research graph 中继续增加固定 Search/Market 节点

否决。它只能覆盖预先想到的数据，未知事件仍要求改 graph；模型依旧不能根据结果动态决定下一步，并会逐渐形成手写 Harness。

### B. 用 LangGraph/LangChain 从零补齐通用 Harness

否决作为 R2-R 主方案。LangGraph 适合生命周期、状态和路由，LangChain `create_agent` 可以提供单 Agent tool loop，但完整实现插件生态、Session、Subagent、Web tools、telemetry 和工作台仍会重复建设。现有 fixed baseline可以保留，不继续扩张成第二个平台。

### C. 让 DSH 直接成为 Product Core 和调度器

否决。DSH 处于 developer preview，内部 Session/插件 schema 可能变化；若它拥有 Event/Evidence/Gate/Artifact/Promotion，未来升级或替换需要迁移业务事实，也违反 ADR-0005。

### D. DSH 作为可替换 Research Harness Runtime

选择。它复用现成 Agent Loop 和插件生态，同时通过 `ResearchHarnessRuntime`、MCP/Tool Gateway、canonical Evidence 和 Gate 隔离产品资产。DSH 失败可以切回 baseline，账本与历史评测不迁移。

### E. 直接选择 OpenAI Agents SDK 或 Pi

暂不选择为 R2-R 首实现。OpenAI Agents SDK 是可靠的 Python 对照候选，Pi 是轻量 TypeScript 候选；但同时接入三套 Runtime 会扩大测试/部署/telemetry 面，延迟真实产品验证。统一端口保留后续替换能力。

## 运行边界

```text
Product Kernel / LangGraph
  owns: Run, Evidence, snapshots, budgets, sufficiency, Gate, commit, recovery
                |
                | ResearchSessionRequest / Result / Trace refs
                v
DSH adapter (restricted decision-research profile)
  owns: session, steps, manager, subagents, model/tool loop, harness telemetry
                |
                | audited capability calls
                v
Tool Gateway / Core MCP
  owns: permissions, schema, timeout, source class, PIT metadata, result mapping
```

DSH native Web tools的结果必须经过 adapter/event mapper 转换为 `EvidenceCandidate`；不能仅保存在 DSH Session 中。对高频精确数据使用 typed Market/Official tools；通用 Web Search/Fetch 处理长尾和 fallback。

## 两层停止策略

DSH 内层 Step Loop 按 Harness 的模型/工具终止语义执行；外层 Product Evidence Round 只在代码充分度检查发现关键 gap 时 continuation。同一阶段默认最多 3 rounds、12 tool calls、6 subagents、180 秒和 Pack 成本上限。

模型可以提出“证据已经足够”或“需要某数据”，但只有代码 `SufficiencyPolicy` 可以确认 hard requirements、freshness、source quality、conflict 和预算状态。达到上限仍有 gap 时结果必须显式 `research_only`，同时保留搜索轨迹和停止原因。

## 多 Agent 决策

Manager/Supervisor 动态选择任务，但 Pack 对高影响事件强制 `counter_thesis` 和 `data_quality`，并按事件要求 policy/data delta、expectation/pricing、macro transmission、market/derivatives 等 capability。Subagent 是研究角色，不拥有发布权。

Lead synthesis 产生 `CausalCase + HorizonDecision[]`。30m、24h、72h 可以同方向，但必须引用各自证据、鲜度、expiry、trigger、invalidation 和 next review；完全复制会触发 schema/quality failure。

## 持久运行决策

FastAPI `BackgroundTasks` 不再承担长研究。复用现有 `hub-worker` 可执行入口，新增 `--role research`：

- API admission 后只产生 durable queued Run；
- realtime worker 发现事件并 enqueue，不等待 180 秒研究；
- research worker 通过 CAS/lease claim，执行 LangGraph + DSH，定期 heartbeat；
- checkpoint 和已落账 Evidence 支持进程恢复；
- evolution worker 继续只做 candidate/evaluation，不与正式研究混用。

该选择增加一个逻辑进程，但不增加仓库、数据库产品、消息队列或微服务框架。单 owner 本机继续使用 SQLite WAL；跨主机需求出现前不引入 Redis/Temporal/Kafka/PostgreSQL。

## 后果

正面：

- 系统首次获得真实的 observe -> plan -> act -> observe -> replan 能力；
- 复用 DSH 的 Harness/插件生态，不重写通用 Agent Loop；
- 未知长尾事实可由 Web Search/Fetch 发现，高频精确事实仍由 typed tools保证；
- Core、PIT、Gate、评测和个人资产继续独立于 DSH；
- 研究过程可观测、可恢复、可比较和可回滚。

负面：

- 需要锁定并维护 DSH SDK/CLI 兼容层与 capability canary；
- 本机增加 research worker/DSH 进程资源和部署面；
- 双 Snapshot 与 Evidence lineage 需要 additive schema/migration；
- Agentic research 延迟会从数秒增加到约 1-3 分钟，因此必须异步展示进度；
- DSH developer-preview 升级可能破坏 adapter，必须 contract/replay 后才能升级。

## 迁移与回滚

- fixed graph 注册为稳定 baseline；历史 Run 不迁移、不重算。
- DSH 首先以 shadow/candidate 运行；只有 owner Promotion 才改变 Pack runtime pointer。
- adapter/readiness 失败时按显式 policy fail-closed 或标明 fallback，禁止静默切换。
- 回滚通过 active pointer/CAS 退回 fixed baseline，停止 DSH/research worker不删除任何业务事实。
- Replay 使用 archived Tool Gateway；不允许 DSH 实时联网取得未来信息。

## 受影响契约与模块

- 新增 `agentic_research.schema.yaml` 及 codegen 镜像。
- 新增 Kernel `ResearchHarnessRuntime` Port、SufficiencyPolicy、Research Queue/lease。
- 新增具体 `dsh_runtime` adapter，不继续膨胀通用 `candidate_runtime.py`。
- 新增 agentic decision graph、research worker role、Research Query/View 和前端页面。
- 更新 crypto_macro Pack、CapabilityManifest、Run Inspector、Operations 和评测数据集。

## 必须通过的验证

- 当前 Warsh fixture 能稳定重现“fixed baseline 发现缺口但不会补证”。
- DSH contract fixture 至少发生两次 model/tool step，并根据第一次结果改变后续调用。
- 未知来源可经 Web Search/Fetch 补齐；精确市场数据优先 typed tool。
- hard gap、stale、conflict、tool deny、429/timeout、DSH crash、worker restart、PIT future leakage 均有失败测试。
- 三个 horizon 不得完全复制；概率必须标记 subjective/uncalibrated。
- 10-20 个 PIT 事件上比较 fixed 与 DSH 的证据覆盖、引用、PIT、成本、时延、Gate 和到期 Outcome。
- Decision Desk 可查看计划、工具、证据、充分度、停止原因和 runtime compare，默认不展示 raw JSON。

## Owner 确认

Owner 已于 2026-08-29 接受本 ADR，并授权从 `R2-R-00` 开始实施。接受 ADR 不等于授权任意社区插件、自动 Promotion、自动交易、第二领域或公开网络无限访问；DSH 精确版本和 profile 必须在 R2-R-01 canary 后锁定。
