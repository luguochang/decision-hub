# Decision Hub 最终产品交付与主线闭环方案

版本：`PRODUCT-DELIVERY-2026-08-31.v1`
状态：`accepted / implementation authorized`
日期：2026-08-31
适用范围：Decision Hub 首个可交付产品（单 owner、crypto macro 交易研究）

## 0. 本文要解决的问题

当前仓库已经有 DSH 上游构建、官方 Host/Client Plugin、Hub 账本、研究
Worker、Decision Desk 和 `crypto_macro` Domain Pack，但这些能力还没有完全
收口成一个用户可以直接理解和使用的产品入口。

本文是独立的交付收口文档，回答以下问题：

1. DSH 和 Decision Hub 为什么同时存在，最终用户从哪里进入；
2. 交易员角色、领域规则和插件如何真正体现在产品中；
3. 后台智能体如何在没有用户持续输入时主动工作；
4. 检索、补证、报告、通知、Outcome 和个人资产如何形成闭环；
5. 当前哪些能力已经存在，哪些只是 scaffold/candidate，哪些尚未实现；
6. 交付前每个缺口如何实现、如何自测、什么条件下停止；
7. 第一产品到什么程度算交付，避免无限增加阶段和重复造轮子。

本文不替代以下事实源：

- 产品架构与边界：[DSH_HUB_SYSTEM_ASSEMBLY.md](DSH_HUB_SYSTEM_ASSEMBLY.md)；
- DSH 上游接入阶段：[DSH_NATIVE_WEB_PRODUCT_CORE.md](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)；
- 研究智能体规格：[RESEARCH_AGENT_PRODUCT_SPEC.md](RESEARCH_AGENT_PRODUCT_SPEC.md)；
- 有界后续路线：[PRODUCT_COMPLETION_AND_FUTURE_PLAN.md](PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)；
- 技术决策：[ADR 目录](../decisions/README.md)；
- 开发约束：[TDD_SDD_SELF_TEST_STANDARD.md](../engineering/TDD_SDD_SELF_TEST_STANDARD.md)。

## 0.1 当前交付判定（2026-08-31）

先给出不含糊的结论：当前仓库已经具备可复用的工程底座和离线主线，
但还没有交付“会自动检索、主动补证、生成报告并通知”的实时交易研究产品。
现在可以交付的是开发/诊断包，不能把它宣传为实时决策产品或盈利系统。

| 能力 | 当前事实 | 产品交付含义 |
|---|---|---|
| DSH 上游 Web、Session、Trajectory、JSONL、官方 Host/Client plugin | 已完成工程验收；live 多轮 Provider canary 已通过 | DSH 可以作为主壳，但还不是已晋级的正式研究 Runtime |
| Hub durable Run、PIT、Evidence、确定性 Gate、Ledger、Outcome/Evaluation | 已有实现和离线回归 | 这是产品控制面和个人资产底座，不是用户需要单独操作的第二个 Agent |
| `crypto_macro` Domain Pack、manager/counter-thesis/data-quality Profile | 已有声明式资产 | 交易员方法已经有落点，但尚未完整投影为 DSH 首页工作区 |
| 后台 Worker 和 Agentic Research graph | 有 durable worker、checkpoint 和 bounded graph | 默认 capability 是 `replay.research`；没有真实授权时不会自动联网 |
| Search/Official/Market 真实补证 | 只有 adapter/manifest/canary seam，真实价值 Gate 未通过 | 当前出现“证据不足”是安全降级，不是信息已经查全 |
| 自动事件发现、报告通知、Outcome 观察 | 有骨架、outbox 和测试；真实来源/通知/长期观察未验收 | 不能声称无人值守闭环已经可用 |

最终用户路径必须只有一条：

```text
用户打开唯一 DSH Web 产品 URL
  -> 进入预装的 Crypto Macro Trader 工作区
  -> 查看自动创建的 Run、Agent 轨迹、证据缺口和报告
  -> 需要运维/审计时才从链接打开 Decision Desk Admin
```

Hub 是后台服务和资产库，不是第三个用户入口；Decision Desk 是管理后台，
也不是第二个聊天产品。开发期间出现的 `50220/65349/5175/8000/8002` 等端口
是验收实例，交付时由一个受控启动器统一启动并只打印一个 DSH URL。

因此，当前阻塞点不是“能不能把 DSH 接进来”，而是以下四件事实尚未同时成立：

1. 正式 Run 已统一由 DSH Web Host Session 执行，而不是 SDK/replay candidate；
2. DSH Agent 在能力可用时会根据 hard gap 继续补证，而不是首轮就停止；
3. 至少一条真实来源和一条真实 Search/Market 路径通过隔离 canary；
4. 报告、通知、Outcome 和 owner 的实际使用价值有可追溯证据。

在这四件事通过前，`Fixed baseline = active`、`DSH = candidate/shadow`、
`Replay = 诊断专用` 的状态不能改变。

## 1. 最终产品的一句话定义

> Decision Hub 是一个以 DSH 为研究执行和交互主壳、以 Hub 为产品控制面和
> 长期资产库、以 Domain Pack 为业务方法的持续运行研究智能体。它会在事件
> 到达后自动创建有边界的研究任务，主动补齐授权事实，生成可追溯的报告，
> 在事实不足时明确停止并通知 owner，并把后续结果沉淀为可评估资产。

它不是：

- 一次 LLM 调用后的漂亮回答；
- 只能由用户手动逐句提示的问答助手；
- 一套复制 DSH Chat/Trajectory 的前端；
- 只返回 `no_trade` 而不尝试补证的固定工作流；
- 自动下单、自动扣费或自动修改生产策略的机器人；
- 一开始就覆盖所有行业的泛化 Agent 平台。

### 1.1 什么才算“智能体”

本产品的智能体行为必须同时满足四个条件：

```text
事件/目标进入
  -> 系统自主识别证据缺口
  -> DSH Agent Loop 自主选择授权能力并继续取证
  -> 新证据改变计划，直到充分、预算耗尽或安全停止
  -> Hub Gate 裁决并持久化结果
```

单次回答属于 LLM Call；固定三次调用属于 Workflow；只有“发现缺口、
调用工具、读取结果、继续规划、最终有界停止”才是 Tool-using Agent。再加上
durable Run、恢复、PIT、Gate、Outcome 和评测，才是本产品的 Agentic Product。

## 2. 最终产品形态和唯一用户入口

### 2.1 用户不应该面对两个需要理解的产品

最终交付时，用户只需要知道一个入口：

```text
DSH Web 工作台
  -> Decision Hub 业务插件已经预装
  -> 默认进入“决策研究”工作区
  -> 可选择“Crypto Macro Trader”
  -> 可查看自动运行、报告、轨迹和结果
```

Decision Desk 仍然存在，但被明确标为“管理后台”，只在需要时打开。它不是
第二个聊天产品，也不是用户必须理解的运行入口。

### 2.2 两个页面的固定分工

| 页面 | 用户看到什么 | 最终职责 |
|---|---|---|
| DSH Web | Chat、Session、Trajectory、Plan、Tool、Subagent、Skill、业务节点、报告摘要 | 日常 Agent 工作台和研究过程 |
| Decision Desk Admin | 运行队列、来源健康、Evidence lineage、Gate、Outcome、评测、资产、Promotion、备份 | 运维、审计和产品管理 |

DSH Web 必须提供从业务节点跳转到对应 Hub Run 和后台的入口；Decision Desk
必须提供“在 DSH 中打开此 Session”。二者通过 `run_id`、`dsh_session_id`、
`artifact_id` 和 `trace_ref` 关联，不复制彼此的事实。

### 2.3 最终 DSH 首页必须显示的业务信息

打开 DSH 后，用户至少应看到：

```text
当前工作区：Crypto Macro Trader
运行模式：Live / Replay（必须醒目标识）
后台状态：事件监听、研究 Worker、通知状态
最近事件：事件时间、来源、Run 状态
当前研究：补证轮次、证据覆盖、缺口和停止原因
决策结果：30m / 24h / 72h、Gate、触发条件、失效条件
报告入口：事实、主因果链、反方链、引用和风险提示
```

DSH 原生轨迹仍由 DSH 自己渲染。Hub Client Plugin 只增加业务节点和状态，
不重写 DSH 的消息、轨迹或会话页面。

## 3. 三个组件到底如何关联

### 3.1 DSH：唯一的研究执行 Harness

DSH 负责已经成熟且会快速演进的 Agent 能力：

- Session、Chat、History 和 JSONL；
- Agent Loop、模型调用、Tool Loop；
- Tool、Skill、MCP、Subagent 和 Compaction；
- Plan、Trajectory、实时事件和 Web UI；
- 官方插件发现、安装和 Client/Host 生命周期。

DSH 不负责：

- 产品事件、Evidence、PIT Snapshot 和业务账本；
- 最终 Gate、Forecast、Outcome、Evaluation 和 Promotion；
- 自动调度、幂等 Run、Lease、恢复和通知 outbox；
- 交易权限、自动下单和业务资产版本。

### 3.2 Decision Hub：产品控制面和资产库

Hub 负责四类 DSH 会话之外的产品职责：

```text
Trigger + Durable Run
  日历/新闻/来源/人工输入 -> admission -> Run -> lease -> recovery

Trust Boundary
  Evidence Gateway -> 三时间戳 -> PIT -> authority/freshness/conflict -> Gate

Product Ledger
  Event / Evidence / Snapshot / Run / Artifact / Forecast / Outcome / Evaluation

Operations + Evolution
  health / cost / error provenance / backup / replay / shadow / owner promotion
```

Hub 不实现第二个 Chat、第二个 Session、第二个 Tool Loop 或第二个插件安装器。

### 3.3 Domain Pack：交易员业务方法

`packs/crypto_macro` 是第一个真正的业务包，声明：

- 宏观事件的根因链和事实/推论/情景分类；
- 六类关键证据要求及来源优先级；
- 30m、24h、72h 的 Gate 和复核规则；
- Manager、Counter-thesis、Data-quality 等角色 Profile；
- Search、Official、Market、Replay Capability 的绑定；
- 评测 rubric、PIT fixture 和失败样本。

交易员不是一段隐藏 Prompt，而是以下对象的组合：

```text
Crypto Macro Trader
  = decision.v1 Product Extension
  + crypto_macro.v1 Domain Pack
  + crypto_macro.manager.v1 Role Profile
  + DSH decision-research preset
  + audited Capability bindings
  + deterministic Gate
```

## 4. 最终主线：从事件到报告和资产

### 4.1 后台主动路径

```text
来源监听/人工提交/日历/新闻发现
  -> Source Adapter 生成 TextEnvelope/Observation
  -> Hub admission 去重、修订合并、影响等级判断
  -> 创建 durable Run 和 Trigger Snapshot
  -> Outbox 幂等派发给 DSH Host Plugin
  -> DSH 创建确定性的 Session
  -> DSH Manager 读取 Domain Pack 和当前 evidence gaps
  -> DSH Agent Loop 并行调用授权 Search/Official/Market/MCP/Skill/Subagent
  -> Capability Gateway 服务器校验来源、时间、权限、hash、成本和 PIT
  -> 已接受 Evidence 回到 DSH，Agent 重新规划
  -> 充分或有界停止后输出结构化候选
  -> Hub 冻结 Decision Snapshot
  -> 确定性 Sufficiency/Publish Gate
  -> Artifact + Forecast + Outbox
  -> 通知 owner
  -> 30m/24h/72h 到期后 Outcome/Evaluation
  -> Experience/FailurePattern 候选，等待 owner review
```

### 4.2 两个循环，不是两个 Agent

#### DSH 内层 Agent Loop

由 DSH 官方 Harness 拥有：

```text
读目标和缺口
  -> 选择 Tool/Skill/Subagent
  -> 读取结果
  -> 发现新缺口/冲突
  -> 继续取证或结构化输出
```

这一层解决“为什么当前数据不足就直接停”的问题。只要还有硬缺口，且有
授权能力、时间和成本预算，Manager 必须生成下一步 bounded action；不能由
一个固定节点直接输出“信息不足”。

#### Hub 外层 Product Loop

由 Hub Worker 和 LangGraph 生命周期图拥有：

```text
admitted -> dispatched -> researching -> completed/failed/cancelled
          -> evidence_attested -> gate_evaluated -> committed
          -> outcome_due -> evaluated
```

它负责重启、租约、回调、PIT、Gate 和业务提交，不重新实现工具循环。

### 4.3 信息不足时的正确行为

当证据不足时，系统必须按以下顺序处理：

1. 从 Pack 读取仍未满足的 hard/soft requirement；
2. 为每个缺口选择首选能力和允许的回退能力；
3. 并行执行互不依赖的能力，保留成功结果；
4. 对失败记录 `ErrorProvenance`，不能把失败写成“没有数据”；
5. 将新结果重新送入 Sufficiency 检查；
6. 只有在没有可用能力、权限不足、关键数据过期、预算或 deadline 到达时
   才有界停止；
7. 输出 `research_only` 或 `reject`，并向用户展示“已经查过什么、失败在哪、
   下一次何时复查”，不能只显示一长串 `insufficient_sources`。

“继续检索”不是无限循环。循环的边界来自 Pack 的 `max_evidence_rounds`、
`max_tool_calls`、`total_deadline_seconds`、`max_estimated_cost_usd` 和
Capability permission。每次继续都必须留下可回放的计划和原因。

## 5. 业务插件和角色如何真正落地

### 5.1 双层插件模型

本项目中的“插件”有两个层级，不能混称：

| 层级 | 负责什么 | 典型位置 |
|---|---|---|
| Product Extension | 产品任务、结果、历史、迁移、评测和业务页面 | `decision.v1`、未来 `presentation.v1` |
| DSH Native Plugin | DSH 内的 Tool、Skill、MCP、Subagent、Host route、Client UI | `extensions/dsh/decision-hub` |

`crypto_macro` 是 Domain Pack，不是直接把所有逻辑塞进 DSH 插件。它可以附带
一个 DSH bundle/client plugin，把角色、命令和业务节点装入 DSH；被卸载时，
Hub 的 Run、Artifact、Forecast、Outcome 和 Evaluation 仍必须可读。

### 5.2 当前已经有的插件接入

当前官方插件包位于：

```text
extensions/dsh/decision-hub/
  package.json          # dsh.bundle + dsh.client 声明
  cordis.patch.yml      # 官方 Host 组合 patch
  src/host/             # readiness、submit/status/cancel、callback
  src/client/           # Session header/status/replay notice
  tests/                # Host/Client contract
```

当前插件已经能：

- 将 Hub readiness 和 Run 状态显示到 DSH Session header；
- 区分 live/replay，防止把 replay 当成正式交互；
- 显示 Gate、Coverage、Stop Reason 和失败摘要；
- 通过 Host route 创建/恢复 DSH Session；
- 通过 callback 将 DSH 结果回写 Hub；
- 通过确定性 ID 避免同一 Run 重复创建 Session。

当前插件还没有完成：

- DSH 首页可见的“Crypto Macro Trader”工作区入口；
- 从 DSH 直接提交正式 Decision Run 的业务命令；
- 在 DSH 对话流中展示完整 Evidence/Gap/Horizon/Report 卡片；
- 让所有正式后台 Research Run 统一使用 Web Host Session；
- 将通知、Outcome 和个人资产入口收口到同一个用户路径。

### 5.3 Role Profile 不是每个角色一套代码

当前 `crypto_macro.manager.v1`、`counter_thesis`、`data_quality` 是声明式
Profile。新增交易角色时，优先修改 Profile/Pack：

```text
只有人格、能力白名单或输出 schema 不同 -> 新 Role Profile
新增金融事实或 Gate -> 新/改 Domain Pack
新增原子工具 -> 新 Capability Plugin + Manifest
新增产品结果闭环 -> 新 Product Extension
```

禁止为每个角色复制 Graph、数据库表、前端应用、Provider client 或 DSH Loop。

## 6. 当前真实状态：什么已完成，什么还没有

### 6.1 已经存在且可复用

| 能力 | 当前状态 | 代码/文档 |
|---|---|---|
| 固定官方 DSH 上游构建 | 已验证 | `infra/dsh/`、`upstream.lock.json` |
| DSH 原生 Session/Trajectory/JSONL | live/replay 已验证 | `.cache/dsh-upstream`（ignored） |
| DSH 官方 Host/Client Plugin | 基础桥接已验证 | `extensions/dsh/decision-hub/` |
| Hub durable Run、lease、checkpoint、callback | 已有并通过离线测试 | `apps/hub_worker/`、`packages/kernel/` |
| PIT、Evidence、Sufficiency、确定性 Gate | 已有 | `packages/kernel/`、`packs/crypto_macro/` |
| 交易领域 Doctrine、Profile、Capability Manifest | 已有 candidate 资产 | `packs/crypto_macro/` |
| Decision Desk 业务查询和研究页 | 已有管理/诊断页 | `apps/decision-desk/` |
| Outcome、Evaluation、Experience、Promotion/Rollback | 已有结构和测试 | `packages/kernel/`、`packages/evals/` |
| Provider Responses canary | GPT-5.5 Responses 已验证 | `tools/canary/`、live audit |

### 6.2 当前没有完成的交付能力

| 缺口 | 用户影响 | 当前事实 | 交付动作 |
|---|---|---|---|
| 统一主入口 | 用户需在 DSH 和 Desk 之间猜用途 | DSH 是通用工作台，Desk 才看得到研究业务 | 在 DSH Client Plugin 增加 Trader workspace、正式 Run 命令和业务卡片 |
| Web 主运行时统一 | SDK candidate 与 Web live 并存，语义容易混淆 | Hub worker 可选 `dsh`/`dsh-web`，默认环境可能仍是 SDK/replay | 先做 runtime readiness/选择显示，再让正式试点显式使用 `dsh-web`；失败不回退伪造轨迹 |
| 后台主动触发 | 当前主要靠人工提交/已有 worker | Source 监听和 scheduler 骨架已有，真实来源未证明 | 完成受控来源、日历/新闻 discovery canary、去重/修订/游标和 durable trigger |
| 主动补证闭环 | 目前某些运行会在不足后停止 | Profile/Graph/Capability 资产已有，真实 Web research loop 未达产品门 | 正式 DSH prompt/profile 要求缺口驱动的 bounded continuation，并验证多轮轨迹 |
| 实时事实覆盖 | 报告出现大量证据不足 | 六类 manifest/replay 已有，Search/Official/Market live 尚未完成 | 逐能力 live canary，记录成功率、延迟、成本、权威和 PIT；失败保留 provenance |
| 人可读失败 | 目前页面仍可能列出技术 gap 原文 | DTO 和部分摘要已有，per-tool provenance/UI 仍需补齐 | 报告按“已查/成功/失败/待复查”展示，raw JSON 仅进审计详情 |
| 报告和通知 | 不能保证结果到达 owner | local outbox 有，外部通知未验证 | 先做本机通知/邮件 dry-run，再做显式 live delivery canary；通知含 Run/报告链接和状态 |
| 资产入口 | 资产尚未形成用户可操作的积累 | Ledger/Evaluation/Experience 有骨架 | 在 Desk 展示版本化 Doctrine/Source/Failure/Experience，DSH 只展示链接和摘要 |
| Prospective 价值 | 不能证明比手工查证更有用 | 目前主要是 replay 和短 canary | 固定观察窗口，比较 Fixed/DSH 的准确、延迟、成本和 owner 时间 |

### 6.3 当前运行实例的解释

当前本机存在多个进程和入口，这是开发验收遗留，不是最终用户部署形态：

```text
50220  官方 DSH Web live 交互入口（临时 canary）
65349  官方 DSH Web replay 验收入口（固定脚本，不能继续自由对话）
5175   Decision Desk Vite 开发前端
8000   Hub API，并可托管 Decision Desk 构建产物
8002   Research MCP Gateway
若干   Hub API/Worker 的历史验收实例
```

交付时必须提供一个受控启动组合，而不是要求用户记住这些端口。默认启动
命令应一次启动 API、Worker、Research MCP 和 DSH Web，并打印唯一的 DSH URL；
replay 只能由验收命令显式启动。

## 7. 交付阶段：只保留一个有限目标

本阶段名称：`PRODUCT-CLOSEOUT-01 / DSH Native Trader Pilot`。

目标不是继续做更多底层框架，而是把首个交易研究产品主线打通到可交付试点：

```text
事件自动进入
  -> DSH 统一执行
  -> 主动补证
  -> 报告和 Gate 投影到 DSH
  -> 通知 owner
  -> Outcome/Evaluation/资产可追溯
```

### 7.1 Task C1：统一启动与用户入口

实现位置：

```text
infra/dsh/run-product.sh                         # 新的受控产品启动器
extensions/dsh/decision-hub/src/client/          # Trader workspace/业务卡片
extensions/dsh/decision-hub/src/host/            # submit/query/command bridge
contracts/schemas/dsh_host_bridge.schema.yaml    # 若新增字段先改 canonical
apps/hub_api/main.py                              # 只增加公开 Command/Query route
```

实现要求：

1. 启动器只暴露一个用户入口 URL；live/replay 参数不能混用；
2. DSH Client Plugin 提供 `Crypto Macro Trader` 工作区或明确入口；
3. 新建正式 Run 必须创建 Hub `event_id/run_id`，再关联 DSH Session；
4. 普通 DSH 对话仍可存在，但明确标为“探索会话”，不自动写正式账本；
5. Run 页面必须能双向打开 DSH Session 和 Decision Desk；
6. 没有 Hub/Plugin readiness 时，入口显示不可用原因，不静默切到 fake。

BDD 退出门：

```text
Given 用户打开产品 URL
When 选择 Crypto Macro Trader 并提交事件文本
Then 只产生一个 Hub Run 和一个 DSH Session
And 页面显示 Run/Session 关联、运行模式和当前状态
And 刷新或重启后仍能打开同一个 Run
```

### 7.2 Task C2：统一正式 Research Runtime

当前必须明确两条路径的处置：

```text
DshResearchRuntime       = Python SDK candidate/fallback，保留用于对照和故障回退评估
DshWebResearchRuntime    = 官方 Web Host 路径，作为本阶段产品试点候选
```

实现要求：

1. 组合根只有一个 runtime 选择点；
2. 运行状态 API 显示 `runtime_id`、版本、模式、Provider 和 canary 状态；
3. 真实产品试点显式选择 `dsh-web`，不通过环境默认值猜测；
4. DSH Web 不可用时只能进入 `failed/degraded/research_only`，不能伪造 DSH
   轨迹或把 Fixed 报告冒充 DSH；
5. SDK candidate 与 Web candidate 使用同一 `ResearchHarnessRuntime` port，
   不改变 Event/Evidence/Gate/Ledger 契约；
6. DSH Web 的 Session JSONL、Hub checkpoint、Hub Ledger 三份状态继续分离。

BDD 退出门：同一个固定任务分别走 live Web、失败 Web、重启 Web 三个场景，
都能得到准确状态；重启不重复创建 Session 或业务提交。

### 7.3 Task C3：真正的缺口驱动补证 loop

实现原则：复用 DSH Agent Loop，不在 LangGraph 里重写第二套 Supervisor/Tool
Loop。需要修改的是 Pack preset、Capability Gateway 和结果投影，而不是再造
一个 Python Agent。

实现位置：

```text
packs/crypto_macro/doctrine/causal-chain.md
packs/crypto_macro/profiles/manager.yaml
infra/dsh/presets/decision-research/agent.cordis.yml
apps/research_mcp/
packages/kernel/decision_hub_kernel/application/research_evidence.py
packages/query_views/research/
extensions/dsh/decision-hub/src/client/
```

实现要求：

1. Manager 首先读取六类 requirement 的当前状态；
2. 将缺口分为可立即并行、依赖前置事实、不可用/权限拒绝三类；
3. 每轮最多调用 Pack 预算内的能力；
4. 每个 capability 必须返回结构化成功或 ErrorProvenance；
5. 一项能力失败不能取消其他独立能力；
6. 成功证据进入下一轮，不能停留在模型上下文而不进 Hub；
7. 达到 sufficiency 后才允许合成方向性 Forecast；否则明确 `research_only`；
8. 所有停止都记录 `stop_reason`、已完成证据、未完成缺口和下次复查条件。

BDD 退出门：

```text
Given 事件身份已知但宏观传导和 BTC 衍生品缺失
When Search、Official、Market 能力至少有一个成功
Then Agent 继续下一轮而不是立即输出 no_trade
And 成功证据保留，失败能力单独显示
And 只有硬缺口仍无法关闭时才输出 research_only
```

### 7.4 Task C4：实时来源和 Search/Market 能力准入

不承诺“自动搜索整个互联网”。只允许 Domain Pack 中经过 Manifest 审计的
能力。首批能力：

```text
event.identity          -> 官方来源/受审计 Search
policy_or_data_delta    -> 官方讲话/公告 + 独立来源
expectation_pricing     -> 利率/政策预期来源
macro_transmission      -> DXY/收益率等授权市场来源
crypto_spot_confirmation -> BTC 现货和事件窗口
derivatives_crowding    -> funding/OI/basis/liquidation 等来源
```

每个能力必须有：

- 明确 endpoint/domain 和授权状态；
- canonical input/output schema；
- server-owned `observed_at/received_at` 和来源 `published_at`；
- authority、freshness、PIT、hash、revision 和冲突字段；
- timeout、retry、cost budget、error code 和回放 fixture；
- 真实 canary 证据，不能由 fixture 代替。

G2-C live canary 仍是独立 owner gate。未授权前可以继续离线回放，但不能让
产品默认声称实时事实完整。

### 7.5 Task C5：报告、通知和可观测展示

DSH Web 展示 Agent 过程和业务摘要；Decision Desk 展示详细审计视图。默认
不向用户倾倒 raw JSON。

报告必须分成：

```text
事件事实（带引用）
预期/变化（带独立来源）
主因果链
反方因果链
已确认证据 / 失败能力 / 未解决缺口
30m / 24h / 72h 独立结论
Gate 和置信度来源
触发条件 / 失效条件 / 下一次复核
```

通知最小内容：

```text
事件标题、Run 状态、Gate 状态、报告链接、DSH Session 链接、
关键缺口/失败、下一次复核时间、是否需要 owner 处理
```

先交付本机 outbox 和 dry-run；真实邮件/IM 必须是显式 canary，不能因为
outbox 写入成功就声称已送达。

### 7.6 Task C6：个人资产和后验闭环

每个正式 Run 结束后必须保留：

```text
DSH JSONL/Trajectory       -> Agent 过程证据
Hub Run/Evidence/Snapshot   -> 产品事实与 PIT
Artifact/Forecast          -> 当时的可证伪输出
Outcome/Evaluation         -> 后验事实与评分
Experience/FailurePattern   -> 候选资产
Version/Promotion          -> 哪个版本被 owner 采用
```

资产沉淀规则：

1. 失败样本先入库，不直接改 Prompt；
2. 经验候选必须引用 Run、Outcome、Evaluation 和适用范围；
3. replay/holdout/shadow 通过后才允许 owner review；
4. owner review 通过后形成新 Pack/Profile/Capability/Policy 版本；
5. 版本变更可以回滚，历史 Run 不改写；
6. 未来更换 DSH/Pi/模型时，只替换 adapter，不迁移这些资产。

### 7.7 Task C7：产品启动、恢复和交付运维

首期单机交付，不引入 Postgres、Redis、Mongo 或 Temporal。启动组合使用现有
Python Worker、FastAPI、SQLite WAL、官方 DSH Web 和受控 MCP。

必须提供：

- 一个产品启动脚本和一个停止脚本；
- API、Worker、MCP、DSH Web readiness；
- 运行模式、版本、Provider、来源和通知健康；
- 数据目录、DSH JSONL、Hub SQLite、checkpoint 和备份位置；
- 重启、回调丢失、重复提交和磁盘空间检查；
- replay 与 live 完全分开的命令和数据目录。

## 8. 交付前 SDD/BDD/TDD 验收矩阵

### 8.1 SDD 规格门

每个 C Task 开始前必须存在：

```text
[ ] 用户价值、输入、输出、非目标
[ ] canonical schema/event/Gate 变化或明确“不变”
[ ] DSH/Hub/Domain/Plugin 所有权
[ ] timeout/retry/cost/error/permission/PIT 规则
[ ] owner 是否需要提供外部授权
[ ] 受影响模块和禁止触碰的模块
```

### 8.2 BDD 场景门

最低必须覆盖：

| 场景 | 必须证明 |
|---|---|
| 新事件自动进入 | 只有一个 Event/Run，能关联 DSH Session |
| 普通聊天 | 不误写业务账本，明确是探索会话 |
| 缺口可补 | 新证据触发下一轮，不能一次停机 |
| 部分能力失败 | 成功证据保留，失败带 provenance |
| 关键数据不足 | `research_only/reject`，无方向性伪 Forecast |
| 证据充分 | 三 Horizon 独立、Gate 可解释、Artifact 可读 |
| Provider 超时/429/403 | 有界重试或明确失败，不伪装成功 |
| Worker/DSH 重启 | Run、Session、checkpoint 可恢复且不重复提交 |
| 回调丢失 | reconciliation 可补回终态 |
| 通知失败 | Outbox 可重试，报告仍在 Hub |
| 结果到期 | Outcome/Evaluation 记录且不改写 Forecast |
| 插件卸载/升级 | Hub 历史仍可读，版本不兼容 fail-closed |

### 8.3 TDD 和质量门

每个 C Task 使用 Red -> Green -> Refactor；至少运行：

```bash
git diff --check
./.venv/bin/python tools/docs/check_module_docs.py
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/pytest -m "not live" -q
./.venv/bin/ruff check packages apps migrations tests tools
./.venv/bin/pyright
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
docker compose config --quiet
```

真实 Provider、Search、通知和浏览器验收必须作为单独证据记录，不能混入普通
CI，也不能把 live canary 写成产品价值通过。

## 9. 最终产品交付门：什么时候停止继续开发

本项目首个产品的交付目标是“个人可用试点”，不是保证盈利或立即成为多用户
SaaS。满足以下条件后，首版可以交付并进入观察期：

```text
[ ] 只有一个用户入口：DSH Web；Decision Desk 明确是 Admin
[ ] Crypto Macro Trader 在 DSH 中可发现、可启动、可查看结果
[ ] 事件 -> Run -> DSH Session -> Agent Loop -> Evidence -> Gate -> Report 已贯通
[ ] Agent 会在有授权能力和预算时继续补证，不因第一处缺口立即停止
[ ] 成功/失败/权限/超时/PIT 结果均可解释且可回放
[ ] 报告、通知、DSH 轨迹、Hub Ledger 和资产入口可互相跳转
[ ] 至少一个真实来源能力和一个真实市场/搜索路径通过受控 canary
[ ] 固定观察窗口内 Run 能持续运行、恢复、不重复提交
[ ] 30m/24h/72h Outcome/Evaluation 开始产生真实标签
[ ] owner 能确认报告比手工查证节省时间或提供了可验证增量
[ ] 无自动交易、无自动 Promotion、无未审计社区插件
[ ] 运行手册、备份、恢复、文档、测试和 CHANGELOG 同步
```

这里的“至少一个真实来源能力和一个真实市场/搜索路径”不是说六类事实
全部已经稳定，而是首版必须能够证明一条真实可用主线；其余缺口必须在页面
上透明显示并安全降级。若关键交易事实仍长期缺失，则产品只能交付为
`research_only` 研究辅助，不能命名为实时交易决策产品。

### 9.1 明确停止条件

以下任一情况发生，停止继续堆代码，做 `promote / retain / stop` 决策：

- 主线已经能稳定运行，但 owner 不节省查证时间；
- 真实来源成本、延迟或授权不可接受；
- DSH candidate 在同条件下没有优于 Fixed 的证据；
- 需要 fork DSH、依赖私有 API 或新增第二套 Agent Loop；
- 同一问题两次局部修复仍未解决；
- 新需求不能归入现有 Product Extension、Domain Pack 或 Capability 边界。

## 10. 已确认的交付授权

Owner 已确认以下交付授权和验收口径：

1. 批准 `PRODUCT-CLOSEOUT-01`，把 DSH Web 作为唯一用户主入口；
2. 批准一次限时、只读、隔离的 live Search/Official/Market canary；
3. 首版通知先使用本机 outbox/dry-run，真实投递另行 canary；
4. 接受“首版是个人可用试点，不承诺盈利、不做自动交易”；
5. Prospective 观察窗口采用“至少 14 天或 20 个高影响事件，以先到者为准”；
6. 观察窗口结束后由 owner 填写节省时间、可解释性和继续使用意愿。

实施期间仍保持以下安全边界：

```text
Fixed baseline                    = active
DSH Web Research Runtime          = candidate
Replay                            = 诊断专用
实时 Search/Official/Market       = 仅在 C4 隔离 canary 中显式启用
通知外部投递                      = 不自动启用
ASR/PPT/第二领域/多用户            = 不进入本阶段
```

## 11. 已确认后的唯一执行顺序

确认后不再重新设计架构，按以下顺序执行：

```text
C1 统一启动器、DSH Trader workspace 和双向链接
  -> C2 dsh-web runtime readiness/正式试点装配
  -> C3 缺口驱动多轮补证和人可读失败投影
  -> C4 一条真实 Search/Market 主线 canary
  -> C5 报告、通知和资产入口收口
  -> C6 固定观察窗口和 Outcome/Evaluation
  -> promote / retain / stop
```

每个 Task 只能有一个独立目标和一个独立提交；每个 Task 完成后更新受影响
模块 README、`IMPLEMENTATION_STATUS.md`、`ROADMAP.md`、`CURRENT_STATE.md`、
`HANDOFF.md` 和 `CHANGELOG.md`。不允许以“顺手”扩大到 ASR、PPT、第二领域、
公共插件市场或多用户。

## 12. 最终决策摘要

```text
DSH Web                  = 用户唯一主工作台和唯一 Agent Harness
Decision Hub             = 触发、可信边界、业务账本、Gate、Outcome 和资产库
crypto_macro             = 第一个交易领域插件包
Decision Desk            = 管理/审计后台，不是第二聊天入口
LangGraph                = Hub 生命周期、恢复和 Evidence Round 边界
Role Profile             = 声明式业务角色组合，不为每个角色复制代码
Capability Plugin        = 可审计的原子能力，进入正式链前过 Manifest
普通 DSH 会话            = 探索，不自动写正式账本
正式 Decision Run         = 从 Hub admission 创建并关联 DSH Session
产品可用                  = 主线贯通 + 真实能力证据 + owner 价值确认
```

因此，当前不是“做不了”，也不是“必须把 DSH 重写成 Hub”。当前问题是已经
完成了很多基础工程证据，却没有把业务入口和正式 Web Runtime 收口。本文的
交付阶段只修这条主线，并在有限 Gate 后停止或转向，而不是继续无限迁移。
