# Decision Hub 产品失败复盘与长期工程教训

日期：2026-08-30（Asia/Shanghai）
状态：`recorded / owner review`
适用范围：Decision Hub 从 R0 到 R2-R/G1/G2 的产品、架构、工程和验证过程
关联决策：[ADR-0012 DSH-first 产品重新收口](../decisions/ADR-0012-dsh-first-product-rebaseline.md)

## 1. 复盘结论

按“能否在真实事件中比直接使用 DSH 或通用 Chat 工具更快、更完整地获得可靠事实，并减少 owner 的人工查证”这个产品标准评价，当前 Decision Hub **不可用**。

当前工作树并不是一个已经发布的实时市场智能体，而是一套包含较完整工程底座、回放链路、候选 DSH 运行时和研究工作台的开发状态。当前页面默认使用 `replay`，不访问网络、不调用实时行情、不调用 DSH Agent Loop；因此页面展示的“证据不足”是安全 Gate 的正确结果，但不是产品完成的证明。

本次失败的根因不是某一个 Prompt、接口或前端组件，而是产品目标、运行时状态和阶段退出门没有被同一套“可用性事实”约束：

```text
架构文档：DSH Research Runtime + 主动补证 Agent
当前默认运行：Replay fixture + 无网络能力
用户期待：实时获取事实并给出有依据的研究结论
实际体验：固定材料重复回放后安全停止
```

工程上做了很多正确的边界建设，但核心用户价值没有先闭合，导致系统越来越像一个可观测的验证平台，而不是能帮助 owner 做判断的产品。

## 2. 最初产品目标

最初目标不是问答助手、固定报告生成器或自动交易机器人，而是一个单 owner、单机运行的事件驱动决策支持产品：

```text
日历/新闻/文本事件
  -> 事件身份与触发时点快照
  -> 主动发现事实缺口
  -> 搜索、官方来源、宏观数据、行情和衍生品能力
  -> 根据新证据继续研究或有限回退
  -> 主因果链 + 反方因果链
  -> 30m / 24h / 72h 独立决策
  -> 人工执行或 no_trade
  -> Outcome / Evaluation / 个人经验资产
```

“智能体”在这个产品中的最低含义是：系统在 owner 授权的目标、能力、预算和安全范围内，能够发现当前信息不足，主动选择下一步取证动作，读取工具结果，调整后续计划，并在事实充分或预算耗尽时诚实停止。

它不要求无限自治，但不能在发现缺口后立即把“证据不足”当作终点，也不能把固定回放结果包装成实时研究。

## 3. 实施时间线与真实产物

| 阶段 | 做了什么 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| R0 | 文本到事件、Evidence、PIT、Gate、Forecast、Outcome、Evaluation、Replay | 业务对象和失败边界可落账、可回放 | 实时搜索和研究价值 |
| R1/R1-L | Source、Scheduler、Market、Notification、durable worker、heartbeat、lease | 单机后台任务和来源边界存在 | 来源覆盖和长期运行稳定 |
| R2 | Workbench、MCP、Run Inspector、Evaluation、Evolution、Promotion/Rollback、Decision Desk | 可观测、可比较、可审计的工程资产 | 用户能否得到更好的市场判断 |
| R2-R-00/01 | `ResearchHarnessRuntime`、DSH SDK、受限 profile、Session/Tool/Subagent/Trace adapter | DSH 候选执行链和契约存在 | DSH 已成为 active、实时能力稳定 |
| R2-R-02/05 | Evidence Round、双 Snapshot、Research Worker、Query/View、SSE、失败投影 | 候选研究生命周期可恢复、可观察 | 真实数据能否被补齐 |
| R2-R-06C/E | DSH canary、12-case 对照、真实 Warsh 事件 | DSH 能尝试工具并在失败时 fail-closed；Fixed 暂时更可靠 | DSH 可以 Promotion 或带来收益 |
| G1/G2-A/B | PIT 所有权、Error Provenance、并行失败隔离、六类事实 manifest/replay | 失败和事实覆盖规则有确定性离线测试 | 真实 Search、Market 数据链稳定 |
| 前端 smoke | `5175 -> 8030 -> replay worker -> Research View/SSE` | 离线前端到后台链路可运行 | 实时 Agentic Product 可用 |

这些阶段的工程产物大多有效，但阶段名称中的 `done/completed` 没有始终附带“工程退出门”限定，导致它们被误解为“产品可以使用”。

## 4. 当前真实状态

### 4.1 当前页面运行链

```text
Decision Desk 5175
  -> Hub API 8030
  -> durable research worker
  -> ReplayResearchRuntime
  -> 固定 research-worker-replay.json
  -> LangGraph 外层 Evidence Round
  -> Sufficiency Gate
  -> research_only / no_trade
```

当前 Compose 默认配置为：

```text
DECISION_HUB_RESEARCH_RUNTIME=replay
DECISION_HUB_RESEARCH_CAPABILITIES=replay.research
DECISION_HUB_LLM_ENABLED=0
DECISION_HUB_SOURCES_ENABLED=0
DECISION_HUB_MARKET_ENABLED=0
```

因此系统没有真实获取 DXY、美国 2Y/10Y、FedWatch、BTC 现货、funding、OI、basis、清算或跨资产数据。Replay fixture 还配置了 `repeat_last_result=true`，第二轮不会根据缺口生成新的真实查询。

### 4.2 DSH 的真实状态

DSH 已作为 `ResearchHarnessRuntime` candidate 接入，承担 Session、Tool、Subagent、Skill、MCP 和内层 Agent Loop。它没有成为当前产品的 active runtime：

```text
Product Core/LangGraph：正式产品生命周期和账本边界
Fixed baseline：当前 active
DSH Research Runtime：candidate/shadow
Replay Runtime：当前隔离页面的实际运行模式
```

此前真实 DSH 运行出现过 `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout`；Warsh 事件的 `web.search` 在 capability deadline 内失败。因此当前正确结论是“候选链存在、失败可记录”，不是“实时 DSH 研究已经可用”。

### 4.3 用户看到的结果为什么没有价值

当前页面同时出现“`2/12 tools`”和“本轮未记录工具调用”。这是 replay 结果中的累计字段与真实 invocation 投影没有区分造成的展示缺陷。用户看到的因果链和事件身份也来自固定 fixture，不代表当前输入已经经过互联网检索。

## 5. 根因分析

| 症状 | 直接原因 | 架构根因 | 产品后果 |
|---|---|---|---|
| 页面没有实时分析 | 默认 runtime 是 replay | 安全测试模式和用户产品模式没有分离 | 用户误以为系统在线，实际只看演示 |
| 全部证据不足 | 只有一条固定事件身份证据 | 真实 capability closure 未完成 | Gate 正确停止，但没有完成主动补证职责 |
| 两轮没有新增信息 | `repeat_last_result=true` | 回放路径没有模拟“缺口驱动新查询” | 研究循环看起来存在，实际没有行动 |
| DSH 没有成为主线 | DSH 保持 candidate/shadow | “架构选择”与“active promotion”没有在 UI/状态中强区分 | 文档和体验互相矛盾 |
| 做了很多仍不可用 | 先做底座、可观测、演进，再做实时价值 | 工程退出门早于产品价值门 | 代码和文档膨胀，核心链路延后 |
| 启动体验混乱 | 多端口、多数据目录、旧进程并存 | 没有单一启动入口和运行态指纹 | 难以判断页面连的是哪套系统 |
| 失败信息不够具体 | Search/DSH 失败仍有粗粒度投影 | 真实失败分类和端到端页面验证晚于架构扩展 | 用户无法区分“没查到”“查失败”“未授权” |
| 阶段状态被误读 | `done` 只表示工程验收 | 缺少产品可用/active/未验证的独立状态 | owner 误以为 R0/R1/R2 已能使用 |

## 6. 对 SDD/BDD/TDD/ADR 的反思

### 6.1 SDD 有文档，但没有锁住唯一产品价值

我们写了大量架构、分层和契约，但没有在每个大阶段开头把以下句子作为不可绕过的退出门：

```text
用户相较于直接使用 DSH，实际节省了什么时间，获得了什么可靠事实？
```

结果是规格越来越完整，价值假设却没有被优先验证。

### 6.2 BDD 偏向工程可观察性，缺少用户价值场景

已有 BDD 很好地覆盖了 API 返回、SSE、失败状态、PIT 和 Gate，但缺少最重要的场景：

```text
Given 一个真实宏观事件
When 系统发现六类事实缺口
Then 它主动调用授权能力并改变下一轮研究计划
And owner 能看到新增事实，而不是只看到缺口列表
```

没有这个场景通过，页面能显示很多状态也不代表产品有效。

### 6.3 TDD 通过了大量离线契约，却没有足够早地要求 live canary

Replay、Fake、contract test 对安全边界是必要的，但不能替代外部能力验证。真实 Search canary 被放在 R2-R 后段，导致核心风险直到工程骨架已经很大时才暴露。

以后任何依赖外部能力的阶段，都必须同时拥有：

- 离线合同测试；
- 真实单次 canary；
- 失败/超时/降级证据；
- owner 价值判断。

### 6.4 ADR 记录了正确边界，但状态没有成为运行时真相

ADR-0008 已经选择 DSH 作为研究执行 Harness，ADR-0009 也已经定义 Product Extension 与 DSH Native Plugin 的双层模型。但 Compose 默认、active pointer、前端页面和状态文档没有形成一个醒目的运行态矩阵。

以后 ADR 必须同时规定：

```text
架构选择是什么
当前 active 是什么
候选如何运行
什么证据才能切换
用户如何看见当前状态
```

## 7. 未来新产品必须遵守的教训

### 7.1 价值优先于平台完整度

新产品先完成一个可真实使用的最小闭环，再建设跨领域平台。平台抽象只有在第二个真实调用方出现后才提取；没有调用方的目录、接口和服务不创建。

### 7.2 Harness 优先复用，产品资产独立保存

如果 DSH、Codex、Pi 或其他 Harness 已经拥有 Agent Loop、工具、Session、插件和上下文能力，不能重新实现一套同类 loop。自己的代码只保留产品不可替代资产：领域契约、证据、PIT、Gate、业务账本、Outcome 和评测。

### 7.3 一个产品只能有一个用户可见的 active 路径

Baseline、candidate、shadow、replay 可以并存于工程内部，但用户必须看到明确的 runtime 指纹。不能让 `replay` 看起来像 `live`，也不能同时维护多套“正式”研究路径。

### 7.4 工程完成和产品可用必须是不同状态

统一使用以下状态词，不再只写 `done`：

| 状态 | 含义 |
|---|---|
| `implemented` | 代码和离线测试存在 |
| `candidate` | 可以隔离运行，尚未成为 active |
| `active` | 当前用户路径正在使用 |
| `live_verified` | 真实能力通过单次或限定 canary |
| `useful` | owner 证明确实节省时间或提高质量 |
| `product_ready` | 完成规定观察期和发布门 |

任何文档出现 `completed` 都必须同时列出上述状态，不能让工程完成代替产品完成。

### 7.5 外部数据能力必须是一等产品依赖

外部 Search、Official、Market、ASR 或其他 Provider 不是“以后再接的接口”，而是产品价值链本身。阶段目标必须从第一天写出：授权、来源优先级、超时、重试、成本、PIT、fallback、失败展示和真实 canary。

### 7.6 默认模式必须诚实

Replay 是测试模式，Live 是产品模式。默认启动可以选择安全，但必须让页面明确显示“非实时”，并提供独立的 Live preflight，而不能用同一个首页承载两种含义。

### 7.7 不用更多功能掩盖核心失败

当核心价值不成立时，禁止转向 ASR、PPT、第二领域、Evolution、公共插件市场或更复杂的基础设施。应当暂停、复盘、缩小目标或停止产品。

### 7.8 一次只保留一个可验证的大目标

目标必须能写成：

```text
一个用户、一个领域、一个事件族、一个 active runtime、最多三个核心验收断言。
```

如果不能在一页 Stage Charter 中写清输入、输出、真实价值、失败和停止条件，就不能开始编码。

## 8. 对 Decision Hub 的修复原则

本复盘不授权立即重构代码，也不删除历史实现。后续只按关联的 ADR-0012 执行以下原则：

1. DSH 作为唯一研究执行 Harness；不再让 LangGraph 自建第二套 Agent Loop。
2. Decision Hub 保留 Evidence、PIT、Gate、Ledger、Outcome 和评测，这些是产品资产。
3. 先用 DSH 独立基线验证真实事件研究价值，再接回 Hub 的账本和可观测页面。
4. 只支持 `crypto_macro` 的一个高影响事件族，文本输入优先；ASR/PPT/第二领域冻结。
5. 只有真实 Search/Official/Market capability 能补齐关键事实，系统才允许进入方向性分析。
6. Replay 页面必须明确是非实时诊断模式；不得把 fixture 的工具统计渲染成真实调用。
7. 如果 Hub 在真实观察期不能比直接使用 DSH 更快、更完整或更可追溯，停止继续产品化，保留它作为 DSH 的领域资产/账本增强层。

## 9. 复盘后的停止点

当前产品停止在：

```text
可观察的离线/replay 研究工作台
```

当前不能宣传为：

```text
实时市场决策服务
DSH active Research Agent
预测准确或盈利系统
通用 Agent 平台
```

下一次开发必须先取得 ADR-0012 的 owner 确认；确认前不切 active pointer、不扩大网络能力、不开发新领域、不继续叠加外围功能。

## 10. 证据入口

- [当前状态短上下文](../context/CURRENT_STATE.md)
- [当前有效决策索引](../context/CURRENT_DECISIONS.md)
- [ADR-0008 Agentic Research Runtime](../decisions/ADR-0008-agentic-research-runtime.md)
- [R2-R-06E Runtime 决策包](../evaluations/R2-R-06E_RUNTIME_DECISION.md)
- [G1/G2 前端运行链记录](../evaluations/G1_G2_FRONTEND_RUNTIME_SMOKE_2026-08-30.md)
- [产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)
