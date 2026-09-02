# DSH-first 产品实现总方案

日期：2026-08-30
状态：`accepted`（owner 于 2026-08-31 接受 DSH-first 与原生 Web/插件方向）
适用范围：Decision Hub 当前单 owner 事件驱动决策产品，以及未来可复用的非金融 Product Extension。
前置事实源：[产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)、[ADR-0012](../decisions/ADR-0012-dsh-first-product-rebaseline.md)、[产品失败复盘](../retrospectives/RETRO-2026-08-30-PRODUCT-FAILURE-AND-LESSONS.md)。

> 本文是完整设计边界；实际代码授权只以 [DSH Native Web Product Core Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 为准。不切换 active runtime、不启用 live capability、不修改历史账本。

## 1. 先给结论

### 1.1 最终产品形态

Decision Hub 不是一个聊天页面，也不是把 DSH 页面复制过来的网站。它是一个单 owner 的主动研究产品：系统在事件到达后自动启动一次有界研究任务，发现事实缺口，调用已审计能力，继续补证，形成带引用、反方、触发条件、失效条件和复核时间的决策支持结果，并把结果和后续 Outcome 记录下来。

```text
Decision Hub = 产品控制平面 + 可信账本 + 领域资产 + 人可读工作台
DSH          = 唯一研究执行 Harness（候选晋级后）
LangGraph    = 产品生命周期编排和恢复边界
Capability   = 可审计的工具/来源能力，不是第二套 Agent
```

当前真实状态仍是：`Fixed baseline active`、`DSH candidate/shadow`、`replay 页面默认运行`。这不是产品可用状态；本文的第一目标是闭合真实 DSH 研究价值，而不是继续增加页面或抽象。

### 1.2 前端决策：复用 DSH Web 主壳，Decision Desk 收敛为管理后台

**修订后的推荐方案：DSH Web 是用户与 Agent 交互的主界面；Decision Hub 以官方 DSH bundle/client plugin 形式贡献业务节点和命令；现有 Decision Desk 保留为运营、账本、评测和晋级后台。**

| 页面/界面 | 归属 | 用途 | 处置 |
|---|---|---|---|
| Chat / Session / Trajectory / Tool / Subagent / Skill | 上游 DSH Web | 人工对话、自动任务 Session、完整轨迹、会话历史和 JSONL 导出 | 直接复用上游，不复刻 |
| Decision Hub conversation nodes | 官方格式 `decision-hub` DSH client plugin | 在 DSH 对话中渲染 Run、Evidence、Gate、Horizon、报告和失败摘要 | 新建薄插件，不改 DSH 源码 |
| Decision Hub command/tool bridge | 官方格式 DSH host plugin + bundle | 提交事件、查询 Run、关联 `dsh_session_id`、反馈、复查和打开后台 | 新建薄插件，走 Hub API/MCP |
| Operations / Sources / Capabilities | Decision Desk (`apps/decision-desk`) | worker、runtime、来源、权限、错误和启动健康 | 保留为 `/admin` 或独立后台 |
| Assets / Evaluation / Promotion | Decision Desk | Pack/Profile/Runtime 版本、Outcome、评测、owner review 和回滚 | 保留为管理后台 |

DSH 官方仓库已经把 Web App、Session UI、Trajectory、Plan、Tool、Subagent、Skill、插件清单和设置卡拆成 Cordis client plugins；外部插件可通过 `dsh.client` 声明和 `dsh.bundle` patch 接入，不需要修改 Web 应用源码。因此不应该再自己复刻 DSH 的会话/轨迹前端。

Decision Hub 的业务事实仍通过 Hub Query/View/Command 契约取得。DSH plugin 只负责把这些事实渲染到 DSH Web 或把用户命令送到 Hub，不能直写 SQLite、Gate 或 active pointer。现有 Decision Desk 已有价值的运营和评测页面不删除，但不再重复实现聊天、Session、Trajectory 和插件设置。

### 1.3 后端决策：保留当前分层，DSH 走 adapter，插件走 Capability

不重写当前仓库，也不把所有代码搬进 DSH。保留已有模块，完成以下所有权调整：

```text
Source Adapter
  -> TextEnvelope / Observation
  -> Kernel admission + PIT Trigger Snapshot
  -> durable Run
  -> LangGraph lifecycle graph
  -> DSH Research Session
       Supervisor -> Capability Tool/Skill/MCP/Subagent loop -> replan/finish
  -> Evidence Gateway（服务端时间、来源、PIT、冲突和 hash）
  -> Decision Snapshot
  -> Deterministic Sufficiency/Publish Gate
  -> Artifact / Forecast / Outbox / Outcome / Evaluation
  -> DSH Web + Decision Hub client plugin / Decision Desk admin / notification
```

这里有两个循环，但不是两套 Agent：

1. **DSH 内层 Agent loop**：由 DSH Manager/Supervisor 根据当前缺口选择工具、调用 specialist/subagent、读取结果、调整下一步计划，直到证据充分或预算耗尽。
2. **Hub 外层产品 loop**：由 LangGraph 管理 durable Run、checkpoint、取消、恢复、Evidence Round 边界、最终 Gate、commit 和 Outcome recheck。外层只在需要下一轮或需要恢复时继续，不重新实现工具循环。

Agent 可以提出下一步和候选结论；代码拥有的 Evidence Gateway 和 Gate 才能确认事实和发布级别。

### 1.4 与现有 ADR 的关系

当前文档之间存在一个需要正式收口的术语差异：

- ADR-0005 将 DSH 描述为可替换的 Workbench/Harness，避免它成为整个产品的唯一运行时。
- ADR-0008 和 ADR-0012 将 DSH 描述为研究执行 Harness，LangGraph 负责 Hub 外层生命周期。

两者在所有权上并不矛盾，但“唯一”必须限定为**研究执行域**：DSH 是 Decision Hub 研究任务的唯一正式 Agent Harness（待 Promotion），并不是未来 PPT、渲染、批处理或企业平台所有任务的唯一 Runtime。未来其他 Product Extension 可以通过相同的公开 Port 选择 DSH、Pi、Codex 或专用 Runtime，不能因此改变 Kernel、账本和 Gate 的所有权。

在 owner 接受本蓝图后，必须新增或修订一个 ADR，明确 supersede ADR-0005 中关于“研究执行 Runtime”的旧措辞，同时保留其 Core/Harness 隔离、插件 deny-by-default、Session 不作账本和可替换 adapter 原则。在该 ADR 被接受前，代码和 active pointer 均保持现状。

## 2. 当前代码审计与处置

### 2.1 已有模块的真实职责

| 当前路径 | 现在有什么 | 处置 |
|---|---|---|
| `apps/decision-desk` | React 19/Vite/TanStack Query/Zod/ECharts/Lucide，已有 Research/Operations/Run Inspector | 收敛为运营/账本/评测后台；不再复制 DSH Chat/Session/Trajectory |
| `apps/hub_api` | admission、query/view、research command、SSE、health；不执行长任务 | 保留薄 API；不得在 route 中运行 Agent 或补写账本 |
| `apps/hub_worker/composition.py` | API/realtime/research/evolution 组合根，按环境选择 replay/DSH | 保留唯一组合根；默认 replay 只用于诊断；live 选择必须显式 preflight |
| `apps/hub_worker/research.py` | durable research worker、Run lease、Evidence Round、commit | 保留；只调用 `ResearchHarnessRuntime` port，不写第二套 loop |
| `apps/research_mcp` | DSH 到 Hub Capability Gateway 的 MCP transport | 保留为唯一研究工具入口；manifest deny-by-default |
| `packages/kernel` | Event/Evidence/PIT/Snapshot/Run/Gate/Ledger/Outcome/Evaluation | 冻结核心所有权；不 import DSH、LangGraph、Provider |
| `packages/orchestration/langgraph` | research graph、checkpoint、recovery、evidence round | 保留产品外层编排；不增加通用 Agent loop |
| `packages/runtime_adapters/dsh_runtime` | DSH SDK、restricted profile、Session/Tool/Subagent/Trace/result mapping | 作为唯一候选 Harness adapter；不把 SDK 内部状态暴露给 Core |
| `packages/runtime_adapters/fixed_research_runtime` | legacy fixed research baseline | 冻结为可回退 baseline；不继续扩展功能 |
| `packages/runtime_adapters/replay_runtime` | 固定离线 fixture | 仅诊断/回放；页面必须明确显示 replay，不得冒充实时 |
| `packages/provider_adapters` | Search、Official、Macro、Market、OKX 等实现 | 保留为 Capability 实现；不让领域策略直接 import 具体 provider |
| `packages/source_adapters` | 手工文本、官方 feed、transcript 边界 | 保留；未来 ASR 只接 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope` |
| `packages/query_views` | 面向前端的规范化 DTO | 保留；不将 raw DSH JSON、Graph state 或 SQL 暴露给前端 |
| `packages/workbench_adapters` | DSH/Codex/MCP 能力发现和准入 | 保留；只登记和执行经审计能力，不自动安装插件 |
| `packages/evals` | replay/holdout/shadow、比较和 Promotion 证据 | 保留为个人资产沉淀和晋级门，不用主观报告替代 |
| `packs/crypto_macro` | doctrine、evidence requirements、bindings、profiles、gates、eval fixtures | 作为首个 Domain Pack；金融默认值逐步留在领域边界 |

### 2.2 目前必须承认的缺口

- 默认页面是 replay，`LLM/sources/market` 为关闭状态，不能证明实时网络检索。
- DSH 真实 canary 曾出现 Search deadline、Session incomplete、unattested evidence 和错误归类；它仍是 candidate/shadow。
- 过去的“证据不足”是正确的安全停止，但曾经没有在预算内继续找证据；没有能力可调用时，系统只能停。
- 旧固定链和 DSH 候选链并存，但只有 Fixed 可以作为 active fallback；不得把两份结果同时当成生产答案。
- 第一个并发初始化 SQLite 的 migration race、真实 Search 授权和可靠性仍需单独证据。

这些是产品未完成的事实，不通过新增 Prompt 或更多前端卡片掩盖。

## 3. 所有权矩阵

| 能力 | DSH | LangGraph | Decision Hub Kernel | Frontend |
|---|---|---|---|---|
| Model/tool/skill/subagent/session loop | 拥有 | 调用/等待 | 不感知 | 只显示投影 |
| 动态研究计划 | 产生候选 `ResearchPlan` | 管理 round 边界 | 校验权限、预算和 Gate 输入 | 显示当前/下一动作 |
| Provider 协议、timeout、基础 retry | SDK/adapter 复用 | 生命周期传递 | 错误码和总预算语义 | 显示状态 |
| checkpoint/recovery/cancel | Session 自身状态 | 拥有产品 Run checkpoint/recovery | 账本状态裁决 | 发送命令 |
| Evidence provenance/PIT | 提供原始结果引用 | 传递不可变 refs | 唯一裁决和入账 | 显示来源/时间 |
| Gate/publish/active pointer | 无权限 | 路由到 Gate | 唯一裁决 | 无权限 |
| Forecast/Outcome/Evaluation/Ledger | 无权限 | 调度 | 唯一事实源 | 查询/人工反馈 |
| 原始会话 JSONL | 拥有 | 只保留 reference/hash | 不复制为业务账本 | 默认不展示 |

## 4. 插件/能力模型

### 4.1 不能再把“插件”混成一个词

```text
Platform Core
  -> Product Extension（产品结果和历史）
      -> Domain Pack（领域方法和事实要求）
          -> Role Profile（一次研究任务允许的角色/权限）
              -> Capability Plugin（工具/来源/模型能力）

DSH Native Plugin
  -> Tool / Skill / MCP / Subagent / Hook / UI
  -> 只有结果要进入正式 Evidence/Gate 时，才经 CapabilityManifest 准入
```

各层职责：

- **Product Extension**：定义产品对象、查询、命令、迁移和评测。例如当前 `decision.v1`；未来 PPT 应是独立 `presentation.v1`，不能把 PPT 字段塞进金融表。
- **Domain Pack**：定义事实要求、来源优先级、鲜度、根因链、领域 Gate、预算和评测。例如 `packs/crypto_macro`。
- **Role Profile**：一次任务的能力白名单、输入/输出 schema、预算、模型路由和允许的 DSH Skill/Subagent。它是配置资产，不是散落在 Prompt 里的角色代码。
- **Capability Plugin**：完成一个可验证动作，例如 `web.search`、`web.fetch`、`official.fed`、`market.cross_asset`、`market.crypto_derivatives`。它必须返回 canonical Result，经 Gateway 生成 EvidenceCandidate。
- **DSH Native Plugin**：DSH 生态里的安装单元。它可以实现 Capability，但不能直接写 Hub 账本、Gate 或 active pointer。

因此“一个交易员角色调用 ASR、金融分析、网络检索插件”是可实现的，但调用关系是：交易员的 Role Profile 由 DSH Supervisor 选择能力，能力经 Gateway 返回结构化结果，最终仍由 Hub Gate 处理。插件不是只优化页面，也不是任意代码直接互相 import。

### 4.2 CapabilityManifest 的准入字段

每个 capability 进入 enabled 前必须声明并测试：

```text
capability_id / version / provider_id
input_schema_ref / output_schema_ref
permissions / network_domains / data_classes
authority_level / supports_pit / freshness_policy
timeout / max_retries / cost_estimate
failure_codes / fallback_ids
license / audit_status / owner_enabled
replay_fixture_ref / healthcheck_ref
```

运行时按 `capability_id` 调用，不按 Python import 路径调用。未经 `owner_enabled` 的能力只能 `disabled` 或 `shadow`。Generic Web Search/Fetch 负责发现长尾资料；精确行情和官方数据仍必须优先走 typed capability，不能让搜索摘要代替收益率或 funding 的精确数值。

### 4.3 不要求 owner 手工封装所有接口

本方案不要求为每个网站写一个新 Agent。能力层提供少量可复用适配器：

1. 通用 `web.search`：发现未知事件和候选来源。
2. 通用 `web.fetch`：抓取允许域名的正文并保存 hash/时间。
3. 官方来源适配器：Fed、BLS、BEA、Treasury 等按 manifest 注册。
4. 宏观/跨资产适配器：统一输出 DXY、2Y/10Y、real yield、VIX 等 typed series。
5. 加密衍生品适配器：统一输出 spot、funding、OI、basis、liquidation 等字段。

DSH Supervisor 只看 capability catalog 和当前 gap，不需要预先知道每个 URL。它不能绕过 catalog 自动安装陌生插件或扩大网络权限；找不到可用能力时必须记录 `capability_unavailable`，而不是假装信息充分。

## 5. Agent 与 Workflow 的边界

### 5.1 为什么不是固定 Workflow

`policy_delta -> counter_thesis -> synthesis` 是旧 baseline，保留用于对照；正式 DSH 候选必须能根据实际 Evidence Gap 选择下一步。角色数量可以变化，Pack 的硬事实要求不能被模型删除。

Supervisor 的结构化输出是：

```text
ResearchPlan
  objective
  required_capabilities[]
  tasks[]: capability_id, question, input_refs, priority, success_condition
  dependencies
  stop_if
  budget_claim
```

代码对计划做 allowlist、PIT、权限、预算和 schema 校验，然后使用 LangGraph `Send` 或 DSH 原生 subagent 机制并发执行独立任务。结果合并后，Supervisor 可以产生下一轮计划；最多由产品 Run budget 控制轮次、工具数、时间、tokens 和 cost。

### 5.2 两层循环的时序

```text
事件入账
  -> 外层 LangGraph 创建 Run + Trigger Snapshot
  -> DSH Session Round 1：Supervisor 看 gap
      -> 并发调用 web/official/market capability
      -> Gateway 校验并入账 EvidenceCandidate
      -> Supervisor 读取成功、失败和冲突，决定 Round 2
  -> DSH Session 输出 synthesis candidate
  -> 外层 Evidence Gateway 冻结 Decision Snapshot
  -> deterministic Sufficiency/Publish Gate
  -> publish / research_only / degraded / reject
  -> 到期后 Outcome/Evaluation，必要时创建 recheck child Run
```

如果 Round 1 的一个工具失败，其他独立工具仍继续；如果所有可用能力失败，系统进入 `degraded` 或 `research_only`，并把下一步人工动作写入页面。停止必须有 `stop_reason`，不能只是“模型没有继续调用”。

## 6. 后端代码结构和允许变更

### 6.1 现有目录保持不变

```text
apps/
  decision-desk/       # 运营/账本/评测后台；不复制 DSH Web
  hub_api/             # REST/SSE/Query/Command，短请求
  hub_worker/          # realtime/research/evolution durable worker
  research_mcp/        # DSH -> Capability Gateway 唯一 MCP 入口

packages/
  kernel/              # 产品事实、PIT、Gate、账本、Outcome
  orchestration/langgraph/ # 外层图、checkpoint、恢复、Evidence Round
  runtime_adapters/    # DSH、Fixed、Replay、未来 Pi 的同一 Port 实现
  provider_adapters/   # Search/Official/Macro/Market 的 capability 实现
  source_adapters/     # 文本、Feed、Transcript、未来 ASR
  query_views/         # 前端/工作台的可读 DTO
  workbench_adapters/  # DSH/Codex/MCP 准入和查询桥接
  evals/               # replay/holdout/shadow/Promotion 评测

packs/
  crypto_macro/        # 首个领域 Pack，不扩散到 Kernel

contracts/
  schemas/             # YAML 单一来源；Python/TS 由 codegen 生成
  events/ policies/    # 事件和 Gate 契约
```

### 6.2 每个新增任务的唯一落点

| 需求 | 只能改哪里 | 禁止改哪里 |
|---|---|---|
| 新字段/事件/Gate | `contracts/` + codegen + 对应 schema test | 手写 Python/TS 镜像 |
| DSH SDK/Session 兼容 | `runtime_adapters/dsh_runtime` | Kernel、前端、业务 Prompt |
| 新工具/来源 | `provider_adapters` + manifest + replay | 研究 graph 中写 HTTP |
| 新研究策略 | `packs/<domain>` 或 Strategy adapter | 在 API route 分叉流程 |
| durable 生命周期 | `orchestration/langgraph` + worker | DSH plugin 自己建账本 |
| 账本、PIT、Gate | `kernel` | Agent/DSH/前端 |
| 页面显示 | `query_views` + `apps/decision-desk` | 直接读取 raw JSON/SQL |
| 经验进化 | `evals` + Evolution service | 在线改生产 Prompt/代码 |

不创建 `platform_core` 新目录，除非第二个真实 Product Extension 已经证明至少两个接口确实共享；先通过现有 Kernel/公开 Port 复用，避免空抽象。

## 7. 前端页面和用户可见行为

### 7.1 页面信息架构

```text
DSH Web（交互主壳）
├── Chat / Session history / JSONL export
├── Trajectory / Plan / Tool / Subagent / Skill
├── Decision Hub Run/Evidence/Gate/Horizon/Report nodes
└── Decision Hub commands：提交、取消、重试、复查、反馈、打开后台

Decision Desk（管理后台）
├── Operations / Sources / Capabilities / Preflight
├── Ledger Run Inspector / PIT / Evidence lineage
├── Assets / Dataset / Evaluation / Evolution
└── Promotion / Rollback / Backup / Notification
```

### 7.2 不显示无用 JSON

页面只显示四种人类问题：

1. DSH Web 中系统当前正在做什么、调用了什么、为什么停止？
2. Decision Hub 取得了哪些可信事实，哪些能力失败，为什么允许/不允许形成结论？
3. 这个 DSH Session 对应哪一个 durable Hub Run，是否已 commit？
4. 这个结果以后是否正确，哪个 Pack/Profile/Runtime 值得保留？

完整 DSH Session、工具参数和 Trajectory 由上游 DSH Web 展示；Decision Hub plugin 只新增业务节点，Decision Desk 只显示规范化摘要、hash、引用和可追溯链接。没有数据时显示真实空态，不能用 demo fallback 填满卡片。

### 7.3 Replay 和 Live 必须强区分

- Replay 页面顶部必须显示“离线回放，不代表实时网络事实”；工具次数来自真实 invocation，不读取 fixture 累计字段。
- Live 页面先显示 preflight：Provider、MCP、Capability、网络域、预算、SQLite、通知和 DSH profile 是否通过。
- Candidate/Shadow 结果必须显示“不会改变 active pointer”；只有 owner command 经 Core Gate 校验后才可晋级。

## 8. 数据、会话和个人资产

### 8.1 双真源

```text
DSH Session JSONL
  = 模型上下文、工具调用、subagent、turn、压缩和 harness trace

Decision Hub Ledger
  = Event、Evidence、PIT Snapshot、Run、Artifact、Forecast、Outcome、Evaluation、版本
```

Hub 只保存 DSH session id、trace reference、hash 和关键投影，不把全部 JSONL 复制成第二个账本；也不能只依赖 JSONL 计算 Brier、收益、PIT 或 Promotion。

### 8.2 个人资产沉淀

长期可迁移资产不是某一模型或某段 Prompt，而是：

- `crypto_macro` Domain Pack 的事实门、根因链、来源优先级、Gate 和评测；
- CapabilityManifest、Provider 兼容记录、失败模式和 fallback；
- PIT replay、holdout、prospective shadow 数据集；
- Evidence、Artifact、Forecast、Outcome、Evaluation 历史；
- 经结果验证的 Experience/FailurePattern；
- Pack、Role Profile、Skill、Runtime、模型和 active pointer 版本血缘。

Evolution 只能生成候选 Pack/Profile/Prompt/Capability 版本，执行 replay、holdout、shadow 和 owner review；不能在线自改 Gate、权限、账本或代码。所谓“自主进化”是可审计的候选生成和评测闭环，不是无限制自我修改。

## 9. 领域扩展和长期产品路线

### 9.1 其他领域如何接入

第二个真实领域出现之前，不提取更多共享接口。出现后按以下顺序：

```text
新 Product Extension
  -> extension schema / query / command / migration
  -> 独立 Domain Pack
  -> 独立 Role Profile / Capability binding / Gate / Eval
  -> 复用 Kernel 的公开 Port
  -> 只有两个领域都真实使用时，才提取 Platform Core 接口
```

未来 `presentation.v1`（PPT）可以拥有自己的 `Brief`、`SlidePlan`、`Asset`、`Render`、`Review`；它复用身份、版本、Run、Trace、Artifact 等确实通用的接口，但不读取或改写 `crypto_macro` 的 Forecast/Gate。A 股/美股同理，先建立自己的 Market Pack，不把金融默认值散落到通用 Kernel。

### 9.2 有界路线

| 阶段 | 目标 | 进入条件 | 退出/止损 |
|---|---|---|---|
| A：方案确认 | owner 确认本文、ADR-0012、前端和 runtime 所有权 | 本文无冲突，决策项全部有答案 | 有冲突则停止，不写代码 |
| B：DSH live baseline | 同一真实事件，DSH 能主动搜索、读取、继续补证并输出引用 | capability preflight 通过 | DSH 不比 baseline 更快/完整则保留 Fixed |
| C：Capability 闭合 | Search/Official/Market 三类能力真实返回并通过 Evidence Gateway | 许可证、网络和 live canary 授权 | 失败率/事实覆盖不达标则缩小能力，不扩范围 |
| D：Hub 薄集成 | DSH 结果进入现有 Evidence/Snapshot/Gate/Ledger/Desk | B/C 已有真实证据 | 任何重复 loop/账本/DTO 立即停止并修正架构 |
| E：Prospective 价值观察 | 7-14 天真实事件或明确样本，比较 DSH、Fixed、直接 DSH 使用体验 | owner 记录人工查证时间和 usefulness | 没有实际增值则收敛为 DSH 领域增强层 + 资产账本层 |
| F：个人可用 | 从页面自动触发、补证、出报告、可复查，失败可解释 | E 的价值和安全门通过 | 不承诺盈利；只标记研究支持/人工执行 |
| G：ASR | 本地 ASR/Meeting Copilot 转成 TextEnvelope | F 已可用，音频来源另有 charter | ASR 质量不达标则继续文本入口 |
| H：第二领域 | PPT/A 股/美股中的一个真实调用方 | F 已有可迁移契约和评测 | 不为想象中的领域预建空模块 |
| I：规模化部署 | 云端 worker、Postgres、队列/可观测扩展 | H 或真实负载需要 | 单机足够时不提前引入 Redis/Temporal/微服务 |

F 是当前产品的“可用”定义；G/H/I 是后续有条件路线，不是无限制 backlog。

## 10. 运行和部署方案

### 10.1 首期单机

首期推荐 Windows 主机 Docker Compose 或原生进程；Agent 交互入口和管理后台是两个明确界面：

```text
dsh-web       (固定上游 web profile + Decision Hub Host/Client plugin)
decision-desk (Vite build/静态管理后台，不复刻 DSH Session/Trajectory)
hub-api       (FastAPI，durable command/query/callback)
hub-worker    (realtime + research + evolution role)
research-mcp  (MCP，仅 capability gateway)
SQLite WAL + LangGraph checkpoints + DSH Session JSONL（分别持久化）
```

4060 Ti 8GB/32GB/1TB 足够运行上述编排、SQLite、本地小模型或 ASR 预留；决策模型默认调用外部 OpenAI-compatible Provider。2C/4G 海外服务器只能作为可选通知/反向代理或轻量 API，不承担本地 DSH/ASR 研究主链。当前不需要 Redis、Mongo、Postgres、Temporal 或 DBOS。

### 10.2 运行配置

- 固定 `DSH_UPSTREAM_REF`/package version/image digest；生产和升级候选不能跟随浮动 `master`。
- DSH Web 使用固定 profile、隔离 plugin directory 和 `@decision-hub/dsh-plugin`；Host bridge 只接受认证的 Hub command/callback。
- `DECISION_HUB_RESEARCH_RUNTIME=replay|dsh_sdk|dsh_web`；默认 replay 仅用于诊断，`dsh_sdk` 是 canary/fallback，`dsh_web` 通过 Stage Gate 后才可成为候选主线。
- `DECISION_HUB_RESEARCH_CAPABILITIES` 显式列出已审计能力；空/未知 capability fail-closed。
- Provider URL、模型、Chat/Responses、timeout、retry、budget 只在 adapter 配置；key 只进进程环境，不入文档、账本或 trace。
- DSH Web Host 与 SDK 子进程都使用固定 restricted profile；禁止 shell、filesystem、自动安装和宿主凭据读取。DSH 官方安全边界仍按 developer-preview 对待，不能把 sandbox 名称当作已证明隔离。
- Live 运行先 preflight，再允许单次事件/有界 deadline；失败状态、Trace 和下一步人工动作必须落账。

## 11. 可靠性、停止和安全规则

### 11.1 外层预算

每个 Run 固定 `deadline`、`max_rounds`、`max_tool_calls`、`max_subagents`、`max_tokens` 和 `max_cost`。单次工具有独立 timeout/retry；重试只对声明为 transient 的错误生效。禁止 `except Exception: retry` 和无限 continuation。

### 11.2 失败分类

至少区分：`configuration_invalid`、`capability_unavailable`、`provider_timeout`、`provider_rate_limited`、`provider_unavailable`、`structured_output_invalid`、`evidence_unattested`、`pit_violation`、`insufficient_evidence`、`run_cancelled`、`unknown_runtime_error`。错误必须有 origin、cause、capability、tool_call、attempt、deadline 和 retryable；不能把 PIT 拒绝粗略改名为 timeout。

### 11.3 Gate 规则

Agent、DSH、LangGraph、Judge 和前端都无发布权。Gate 代码检查：事件身份、PIT/三时间戳、最低事实包、来源权威和独立性、冲突、freshness、反方链、30m/24h/72h 独立性、trigger/invalidation、预算和权限。关键事实缺失时只能 `research_only/no_trade` 或 `degraded`，不能填 52% 看起来完整的方向性答案。

### 11.4 真实智能体最低验收

只有同时满足以下条件才允许称为 agentic：

1. 自动触发或用户提交后，worker 在后台持续运行，不需要用户逐轮提示。
2. 发现缺口后，实际调用授权 capability，并且下一轮计划与缺口相关。
3. 成功结果进入 Evidence Gateway；失败、冲突和不可用能力也进入 Trace/Failure Provenance。
4. 能在充分、预算耗尽或安全失败三种状态间明确结束。
5. 页面展示当前动作、证据增加、停止原因和下一步，而不是只展示最终文本。

没有以上事实时，只能称为问答/回放/候选运行，不能称为产品智能体。

## 12. SDD/BDD/TDD/ADR 执行门

每个后续阶段严格遵循：

```text
SDD 价值、契约、事件、Gate、非目标
  -> ADR 跨模块/不可逆决定
  -> BDD 正常、重复、失败、恢复、权限场景
  -> TDD Red
  -> 最小 Green
  -> 框架能力复用检查
  -> Refactor
  -> replay/holdout/live canary（按风险）
  -> README / INDEX / STATUS / CHANGELOG / ReleaseManifest
```

每一张任务卡必须写：允许修改路径、禁止修改路径、复用的 LangChain/LangGraph/Pydantic/SQLAlchemy API、测试命令、停止条件和预计产物。上下文过长时只更新 `CURRENT_STATE`、`CURRENT_DECISIONS` 和 `HANDOFF` 的事实投影，不把推理草稿当真源。

## 13. 当前大阶段任务卡

阶段名：`DSH-NATIVE-CORE`
状态：`accepted / implementation authorized`
唯一目标：先交付官方 DSH Web、原生 Host/Client plugin、durable bridge 和 replay E2E；完成后再进入独立真实价值 Gate。精确任务、路径和测试以 [Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 为唯一实施真源。

### 13.1 子任务顺序

| Task | 内容 | 不做 |
|---|---|---|
| `NATIVE-00` | 固定一致的 DSH 上游源码闭包，构建并验证官方 Web/Session/Trajectory/JSONL/plugin exports | 不 fork/复制前端，不接真实市场研究 |
| `NATIVE-01` | canonical bridge contract、additive session link persistence 和 Hub callback/query API | 不改历史账本或 active pointer |
| `NATIVE-02` | 官方 Host plugin：幂等 submit/status/cancel/completion、丢回调协调和重启恢复 | 不把 DSH Session 当 Hub durable queue/账本 |
| `NATIVE-03` | 官方 Client plugin：readiness、Run/Evidence/Gate 摘要和管理后台链接 | 不复制 DSH Session/Trajectory 或显示 raw JSON |
| `NATIVE-04` | Hub dispatch/reconcile、一个 replay Run、浏览器端到端和 single commit 恢复验收 | 不自写 Agent Loop，不冒充 live 事实 |
| `NATIVE-05` | 本机启动、升级、版本不匹配、回滚和完整离线质量门 | 不自动晋级、不直接进入 ASR/PPT |
| `NATIVE-06` | 完成离线核心后另行执行真实 canary 和 7-14 天价值观察 | 不由模型或脚本代填价值结论 |

### 13.2 BDD 退出场景

```text
Feature: 系统主动补齐事实
Given 一个真实高影响宏观事件和 crypto_macro.v1 的最低事实包
When 后台 DSH Session 发现 policy_delta、pricing、cross_asset 或 derivatives 缺失
Then 它在预算内调用已 enabled 的 capability
And 下一轮计划引用该 gap，而不是重复固定回放
And 每个返回结果都有 server-owned received_at、source provenance 和 hash
```

```text
Feature: 数据不足时不伪造结论
Given 某个关键 capability 超时或返回不可认证结果
When 研究 deadline 或 tool budget 到达
Then Run 进入 degraded/research_only，并显示具体缺口、错误和下一次复查时间
And 不生成方向性 Forecast，不发送交易通知，不修改 active pointer
```

```text
Feature: DSH Web 与管理后台能说明系统做了什么
Given 一个正在运行的 live Run
When owner 打开 DSH Session/Trajectory 或 Decision Desk Run Inspector
Then DSH Web 显示当前动作、工具和 Session 轨迹，Hub 视图显示 live/replay、证据增量、失败原因、Gate 和停止状态
And 页面不把 DSH raw JSON 或 fixture 计数显示成实时事实
```

### 13.3 TDD/质量门

必须至少通过：

- canonical contract/codegen 和 module docs check；
- capability manifest、PIT、attestation、错误分类、timeout/retry、部分成功和幂等单测；
- LangGraph checkpoint/recovery 和 worker restart 集成测试；
- Replay/holdout 对照：Fixed 与 DSH 使用同一 snapshot、schema 和评测指标；
- 显式 live canary：Provider、MCP、Search、Official、Market 各自记录成功/失败证据；
- API/SSE/前端浏览器验收：至少桌面和移动视口，无 raw JSON dump、无 replay 冒充 live；
- prospective 观察报告：事实覆盖、延迟、成本、失败率、人工查证时间和 Outcome。

### 13.4 可用性定义和止损

`product_ready` 不是所有代码都存在，而是 owner 可以在页面上提交/等待一个真实事件，系统能后台自主补证并给出可追溯结果，失败可解释，结果和 Outcome 可复盘，且在观察期内相较直接 DSH 或手工搜索有明确增值。

若 DSH 不能在授权能力下稳定补证、延迟没有优势、事实覆盖没有提升，或者 owner 认为查证时间没有减少，则结论必须是 `retain_fixed` 或 `stop_product`：保留 DSH 的 Skill/Workbench 体验和 Hub 的领域资产/账本，不继续叠加代码。

## 14. owner 确认清单

Owner 已于 2026-08-31 接受以下决策；实现仍受 Stage Charter 限制：

1. 接受 DSH Web 是交互主界面，Decision Hub 以官方 DSH bundle/client plugin 接入；Decision Desk 收敛为运营/账本/评测后台。
2. 接受 DSH 是唯一研究执行 Harness；LangGraph 只管理产品外层生命周期和恢复。
3. 接受 Product Extension/Domain Pack/Role Profile/Capability Plugin/DSH Native Plugin 的双层插件模型。
4. 接受当前 Fixed active、DSH candidate/shadow、Replay 仅诊断，不能把已有 `done` 当可用。
5. 同意首个 live 观察只使用 `crypto_macro` 和 Search/Official/Market 最小能力白名单。
6. 同意不引入第二套 Agent loop、账本、协议、前端或自研通用基础设施。
7. 同意以“比直接 DSH/手工检索更快、更完整、更可追溯”为价值门；不满足就停止产品化。
8. `DSH-NATIVE-CORE` 的 NATIVE-00 至 NATIVE-05 已完成；当前按 `PRODUCT-CLOSEOUT-01` C1-C7 执行。真实 capability 只允许 C4 的隔离、只读、限时 canary，Promotion 仍不自动授权。

当前开发授权来自 [PRODUCT-CLOSEOUT-01 Stage Charter](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md) 和 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)；本蓝图不单独扩大范围。

## 15. 官方 DSH 源码、前端和插件的复用/升级策略

### 15.1 不把 DSH 文件拷贝进业务源码

用户提出的“把 DSH 仓库放到某个文件夹，在其基础上二次开发”可以实现，但推荐使用**上游固定引用**，而不是把 DSH 文件复制进 `apps/` 或 `packages/`：

```text
decision-hub/
  vendor/deepseek-harness/       # 可选 git submodule，固定上游 commit
  overlays/dsh/                  # 本产品 profile、patch、配置和构建脚本
  infra/dsh/                     # DSH 独立容器/启动/健康检查
  packages/runtime_adapters/     # Hub -> DSH SDK/JSON-RPC/MCP adapter
  extensions/dsh/decision-hub/   # 官方 dsh.bundle/dsh.client 形式的 Hub 插件
  apps/decision-desk/            # Hub 运营/账本/评测后台
```

只有在需要本地构建或调试 DSH Web 前端时才引入 `vendor/deepseek-harness` submodule；生产环境可以直接使用固定 digest 的官方构建产物。业务代码不能 import `vendor` 内部模块，所有调用只能经过 DSH 官方 SDK、MCP/JSON-RPC 或明确的稳定 CLI seam。

这样做的好处是：上游 commit、许可证、构建产物和本产品 overlay 分开记录；升级可以快进 submodule/镜像而不混入业务 diff；回滚可以恢复上一个 commit。Git subtree 或复制源码会把上游历史和业务修改混在一起，后续升级冲突更难定位，因此不作为默认方案。

### 15.2 DSH 前端如何“复用”

正式方案是直接运行固定版本的 DSH `web` profile，并通过官方 client plugin 系统贡献 Decision Hub UI，而不是把 DSH React 组件拷进 Decision Desk：

```text
DSH web profile
  + upstream Chat/Session/Trajectory/Tool/Subagent/Skill UI
  + @decision-hub/dsh-plugin Host 半侧
  + @decision-hub/dsh-plugin Client 半侧（dsh.client / platform=web）
  + decision-hub bundle（dsh.bundle -> cordis.patch.yml）
```

Host 半侧接 Hub API/MCP、登记受控命令/工具、关联 Hub Run 与 DSH Session；Client 半侧通过 DSH slots、Conversation Node Definition 或 settings card 渲染业务状态。官方文档明确这种包可在不修改 Web App 的情况下由客户端模块系统动态提供；仓库外插件目前需要复刻官方 lazy-CJS client bundle 构建格式，这是升级 contract 的一部分，而不是 fork 整个前端。

现有 Decision Desk 不删除：它继续承担 DSH Web 不应拥有的账本/PIT、运行健康、来源权限、Evaluation、Promotion/Rollback 和备份管理。用户日常研究从 DSH Web 进入，深度运维才进入 Decision Desk。

### 15.3 当前 DSH 后端调用与目标调用方式

当前实现通过 `deepseek-harness-sdk==0.1.1rc1` 的 Python `DeepSeekHarness`：research worker 在进程内创建 SDK handle，SDK 启动 `sdk` profile 子进程，传入 model、workspace、session root、restricted Cordis profile 和 research MCP URL，再以确定性的 `session_id` 调用 `handle.run()`。返回的 Session events/notifications 经 adapter 映射为 Hub `ResearchTraceEvent`、EvidenceCandidate 和 ResearchSessionResult。

这条路径真实复用了 DSH Agent Loop、Tool/Subagent 和 Session JSONL，但它不是常驻 DSH Web Host，因此当前 Decision Desk 看不到上游 DSH Web 的实时 Session/Trajectory。目标正式路径调整为：

```text
Hub durable Run / outbox
  -> authenticated Decision Hub DSH Host plugin
  -> 常驻 DSH web profile 创建普通 Session
  -> DSH Agent Loop + official/native plugins + Hub MCP
  -> DSH Web 原生实时 Session/Trajectory
  -> Host plugin 监听 session/agent 事件并回报 Hub
  -> Hub Evidence/Gate/Ledger commit
```

DSH 官方 Webhook Runtime 已能把可信外部事件变成普通 Workspace Session，但它是 fire-and-forget、无内置去重、无完成结果，因此不能直接承担 Hub durable contract。Decision Hub Host plugin 必须在它之上或旁边补齐 `run_id/session_id` 幂等关联、接受回执、完成/失败回调和取消；耐久队列、重试和最终业务状态仍由 Hub 拥有。

现有 Python SDK 适配器不删除，降级为：DSH 版本 handshake、隔离 canary、replay/holdout、Web Host 不可用时的显式 fallback。只有常驻 Web Host bridge 通过 contract/recovery/live smoke 后，才讨论替换当前 candidate path。

### 15.4 官方插件如何进入本产品

DSH 官方插件体系仍以 DSH 自己的 manifest 和生命周期为准（例如 `dsh.bundle`、`dsh plugin add`、profile/plugin 配置）。本产品不重新发明一个安装器，也不把官方插件代码复制成 Python 插件：

```text
DSH 官方插件
  -> 安装在隔离的 DSH plugin directory
  -> 由 DSH profile/allowlist 启用
  -> DSH Tool/Skill/MCP/Session 执行
  -> Hub adapter 读取稳定结果/事件
  -> CapabilityManifest 只记录本产品的权限、PIT、成本和审计状态
```

`CapabilityManifest` 不是第二个插件实现，而是产品准入记录和安全边界：

- DSH-only 的 UI、临时 Skill 或人工工具可以只留在 DSH 工作台，不需要进入 Hub 正式链。
- 需要影响正式决策的插件，必须能经 SDK/MCP/Tool seam 返回可验证 schema；Hub 再补充 server-owned 时间、来源、PIT、hash、失败码和预算。
- 通用 DSH 插件如果输出已经符合 canonical capability schema，可直接由通用 adapter 接入；只有领域数据语义不同才增加薄 provider mapping，不复制 Agent loop。
- 插件永远不能直接写 Event、Evidence、Forecast、Gate、Outcome 或 active pointer。

因此“官方插件继续复用”和“Hub 有自己的 CapabilityManifest”是互补关系：前者管理 DSH 里的安装/执行，后者管理产品里的信任/审计/回滚。

### 15.5 上游升级不能承诺零适配，但可以承诺可控升级

DSH 官方当前是 developer preview，不能保证每次更新都二进制/协议兼容。可以保证的是升级不会变成业务重写：

```text
新 DSH commit/release
  -> license/hash/依赖检查
  -> SDK/profile/plugin discovery handshake
  -> adapter contract suite
  -> replay/holdout 对照
  -> DSH Web smoke + Session JSONL/Trace 检查
  -> owner review
  -> promotion 或保留旧版本回滚
```

升级门必须验证：Session 创建/恢复、Tool/Skill/Subagent 调用、MCP transport、结构化结果、Trace 事件、JSONL 落盘、profile 权限和 Web 工作台入口。失败时保留旧 DSH 版本和旧 active pointer，不能让依赖更新自动改变生产研究路径。

版本记录至少包含：上游仓库 URL、commit/tag、镜像 digest、SDK/runtime 版本、profile hash、插件 manifest hash、adapter contract 结果和回滚目标。任何直接修改 DSH 源码的 patch 必须位于 `overlays/dsh/` 并有单独 ADR；禁止在 vendor 目录内静默改文件。

### 15.6 这套方式对未来 DSH 更新的实际保证

能保证：

- DSH 上游可以单独升级、回滚和重新构建；
- Decision Hub 业务账本、契约和前端不会因为 DSH 内部目录变化而迁移；
- 官方插件仍按官方格式安装，产品只审计并映射需要的能力；
- 每次升级有自动 handshake、replay、contract 和 Web smoke 证据。

不能保证：

- 任意 DSH 内部 API 永远不变；
- 任意社区插件不需审计就能进入正式决策链；
- DSH Web UI 的内部组件可以稳定被第三方 React 页面直接复用。

这比复制一份源码后承诺“以后直接替换”更诚实，也更容易真正做到快速替换。
