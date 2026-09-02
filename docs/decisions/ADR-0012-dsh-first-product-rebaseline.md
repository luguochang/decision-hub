# ADR-0012 DSH-first 产品重新收口与实时研究闭环

日期：2026-08-30
状态：`accepted`（owner 于 2026-08-31 授权按方案建立阶段目标、实现并自测）
关联复盘：[Decision Hub 产品失败复盘与长期工程教训](../retrospectives/RETRO-2026-08-30-PRODUCT-FAILURE-AND-LESSONS.md)
关联架构：[ADR-0008 Agentic Research Runtime](ADR-0008-agentic-research-runtime.md)、[ADR-0009 产品平台与扩展边界](ADR-0009-product-platform-extension-boundary.md)

> 2026-08-31 补充：DSH Web、官方 Host/Client plugin、常驻 Host bridge 和上游升级的具体提案以 [ADR-0013](ADR-0013-dsh-web-native-plugin-upstream-integration.md) 为准。本文中的 SDK 运行顺序是当时基线，不再代表目标前端/运行拓扑。

## 1. 决策摘要

本提案将 Decision Hub 后续产品重新收口为一个 **DSH-first 的单 owner 研究产品**：

```text
DSH
  = 唯一研究执行 Harness
    （Agent Loop / Tool / Subagent / Skill / MCP / Session）

Decision Hub
  = 产品资产与可信边界
    （Event / Evidence / PIT / Gate / Ledger / Forecast / Outcome / Evaluation）

Decision Desk / DSH Workbench / Core MCP
  = 同一产品事实的查看、人工补充、反馈和运维入口
```

不再让 LangGraph 自己实现第二套通用 Agent Loop，也不再把固定 replay 工作流当成实时产品。LangGraph 如继续存在，只负责产品级 durable Run、checkpoint/recovery、Evidence Round、权限和确定性 Gate。

本 ADR 已确认产品方向，但不授权立即修改 active pointer。进入新阶段时当前状态保持：

```text
Fixed baseline：active
DSH Research Runtime：candidate/shadow
Replay：离线诊断模式
实时产品：未达到可用
```

## 2. 背景与问题

当前已经有 DSH SDK adapter、受限 profile、Research Worker、Evidence Round、MCP 和前端页面，但用户实际打开的是 replay 实例：

```text
runtime=replay
capabilities=replay.research
LLM/sources/market=off
```

结果是：系统能够识别缺口并安全停止，却没有真正搜索网络、读取官方来源或查询行情。此前 DSH 真实 canary 还出现过 Search deadline、Session incomplete 和 Evidence unattested，R2-R-06E 因此结论为 `retain_baseline`。

这说明当前问题不是“再添加一个 Reviewer”或“再增加一个 Prompt”，而是必须先把 DSH 真实执行路径、外部能力和产品价值闭合。

## 3. 产品定义

本阶段产品不是泛用 Agent 平台、聊天助手或自动交易系统，而是：

> 对单 owner 关注的高影响宏观事件，使用 DSH 主动取得和验证关键事实，给出带证据、反方、触发、失效和复核时间的人工决策支持，并保存后续结果以评估是否有实际价值。

### 3.1 允许的主动性

系统可以在以下边界内自主行动：

- 根据 `crypto_macro` Domain Pack 识别证据缺口；
- 选择已通过 CapabilityManifest 的 Search、Official、Market 和 Specialist 能力；
- 读取工具结果、比较来源、处理冲突和重新规划；
- 在总 deadline、tool budget、cost budget 和权限范围内继续研究；
- 将新证据和失败原因写入产品账本并安排 recheck。

系统不能：

- 安装未经审计的插件或扩大网络权限；
- 修改 Gate、Pack、active pointer、代码或账本规则；
- 自动下单、扣费或发送未经 Gate 的交易通知；
- 把模型提供的时间戳、搜索摘要或未认证内容当作事实；
- 无限重试，或在关键数据缺失时编造完整结论。

## 4. 最终运行边界

```text
Source Adapter
  -> TextEnvelope / Observation
  -> Product Kernel admission + Trigger Snapshot
  -> durable Research Run
  -> LangGraph lifecycle/checkpoint/recovery
  -> DSH Research Session
       Manager -> Tool/Skill/MCP/Subagent loop -> replan/finish
  -> Evidence Gateway
       provenance / PIT / authority / freshness / conflict
  -> Decision Snapshot
  -> deterministic Sufficiency + Publish Gate
  -> Report / Forecast / Outbox / Outcome
  -> Decision Desk / Workbench / Evaluation
```

### 4.1 DSH 的所有权

DSH 拥有：

- 一个研究 Session 内的 model/tool/result/next-step 循环；
- Manager、Subagent、Skill、MCP、Web Search/Fetch 和 Session Trace；
- Harness 内部的上下文压缩、工具编排和会话日志。

DSH 不拥有：

- Event、Evidence、Snapshot、PIT、Forecast、Outcome、Gate 和 Ledger；
- 产品的发布权、Promotion/Rollback 或交易权限；
- 业务结果的唯一事实。

### 4.2 LangGraph 的所有权

LangGraph 只保留产品生命周期职责：

- durable Run 的状态、checkpoint、恢复和取消；
- Trigger/Decision Snapshot；
- Evidence Round 的边界和外层 continuation；
- budget/deadline 路由；
- 确定性 Gate、commit、Outbox 和 Outcome 调度。

LangGraph 不重新实现 DSH 已有的通用 Agent Loop、Session、插件系统或网页解析。

### 4.3 工作台的所有权

Decision Desk、DSH Workbench、Core MCP 和 Codex 只访问同一 Query/View/Command 契约：

- 查看当前运行、证据、工具状态、失败和报告；
- 人工提交文本、来源、Memo 和 Feedback；
- 取消、重试、复查和查看历史；
- 查看能力健康、版本和评测结果。

它们不能各自创建第二套研究逻辑、账本或 Agent Loop。

## 5. 插件与能力模型

插件分成两层：

```text
Product Extension
  -> decision Extension
      -> crypto_macro Domain Pack
          -> Role Profile
              -> Capability Plugin

DSH Native Plugin
  -> Tool / Skill / MCP / Subagent / Provider / UI
```

当前阶段只实现和验证 `crypto_macro` 一个真实调用方：

- `web.search` / `web.fetch`：发现未知新闻、讲话和长尾来源；
- `official.macro`：Fed、BLS、BEA、Treasury 等权威来源；
- `market.cross_asset`：DXY、收益率、实际利率和风险资产；
- `market.crypto_derivatives`：BTC 现货、funding、OI、basis、清算等。

所有能力必须经 Manifest 声明并经过许可证、安全、输入/输出 schema、PIT、timeout、retry、cost、replay 和 owner enable。DSH 只按 capability ID 调用，不直接依赖其他插件的私有代码。

## 6. 本次产品收口范围

### 6.1 MVP 只支持

- 单 owner、单机本地运行；
- `crypto_macro`；
- Fed/央行讲话、宏观数据和高影响事件；
- 文本输入；
- DSH 研究 Session；
- Search、Official、Market 三类已审计能力；
- 30m、24h、72h 决策支持；
- 人工执行或 `research_only/no_trade`；
- Report、Forecast、Outcome 和 Evaluation 记录。

### 6.2 本阶段明确不做

- ASR、直播音频和 OCR；
- PPT、A 股、美股或第二 Domain Pack；
- 自动交易、自动 Promotion、自动扣费；
- 多用户 SaaS、公共插件市场；
- Pi、OpenAI Agents SDK 等第二套正式 Runtime；
- 新数据库、消息队列或微服务拆分；
- 用更多前端页面掩盖实时能力未闭合。

## 7. 运行模式

用户入口必须明确区分两种模式：

### 7.1 Replay / Diagnostic

- 使用固定 fixture；
- 禁止外部网络；
- 不产生实时事实或收益结论；
- 页面必须醒目标注“离线回放”；
- 工具数量以真实 invocation 记录为准，不得使用 fixture 累计值冒充实时调用。

### 7.2 Live Research Pilot

- 显式运行 preflight；
- 检查 DSH SDK/profile、Provider、MCP、CapabilityManifest、网络 allowlist、预算和临时数据目录；
- 只读网络、单次事件、单次 deadline；
- 所有成功和失败都写入脱敏 Trace；
- 未通过充分度只能 `research_only/no_trade`；
- 未完成观察期前不修改 active pointer。

## 8. 下一阶段唯一目标

阶段名称：`DSH-NATIVE-CORE`，随后才是独立真实价值 Gate
状态：`accepted / implementation authorized`

目标不是增加平台能力，而是证明以下闭环真实可用：

```text
真实宏观事件
  -> DSH Session
  -> 发现六类事实缺口
  -> Search/Official/Market 调用
  -> Evidence Gateway 认证
  -> 根据结果改变第二轮研究计划
  -> Sufficiency Gate
  -> 证据充分的报告，或可解释的失败
```

### 8.1 执行顺序

1. **DSH 独立基线**：用同一个事件目标验证 DSH 是否能主动搜索、读取来源、继续研究并产出带引用的结果。
2. **能力闭合**：只接入能满足六类事实要求的最小 Search、Official、Market capability；修复 timeout、fallback、错误 provenance 和结果认证。
3. **Hub 薄集成**：将 DSH 结果映射到 Evidence、Decision Snapshot、Gate、Report、Forecast、Outcome 和 Query/View；不复制 DSH loop。
4. **Prospective 观察**：用 7-14 天真实事件记录延迟、事实覆盖、人工查证时间、失败率和 Outcome。
5. **产品决策**：只有 owner 认为 Hub 比直接使用 DSH 有明显增值，并且安全/可靠门通过，才讨论 DSH promotion；否则收敛为 DSH 的领域增强和资产账本层。

## 9. BDD 验收门

### 场景 A：主动补证

```text
Given 一个真实 Fed/宏观事件和六类事实要求
When DSH Session 发现 event.identity 已有但 policy/market/derivatives 缺失
Then 它调用至少一个授权 capability
And 下一轮 query 与已发现的 gap 相关，不重复 replay 结果
```

### 场景 B：真实证据入账

```text
Given Search、Official、Market capability 返回结果
When Evidence Gateway 收到结果
Then 服务端生成 effective_observed_at/received_at
And 通过 authority、freshness、hash、PIT 和冲突检查后才能成为 Evidence
```

### 场景 C：失败诚实可见

```text
Given 一个 capability timeout、429、MCP error 或 PIT violation
When 研究任务结束
Then 页面显示具体 error_code、origin、capability、retryable 和已保留证据数
And 不把失败伪装成 researching，也不发布未经证据支持的方向性 Forecast
```

### 场景 D：充分时才发布

```text
Given 六类关键事实达到 authority/freshness/source-count 要求
When DSH 返回结构化研究候选
Then Gate 允许生成独立的 30m/24h/72h 结果
And 每个结果都有证据、trigger、invalidation、expires_at 和 next_review_at
```

### 场景 E：产品价值比较

```text
Given 同一事件分别由直接 DSH 和 Decision Hub 运行
When owner 完成真实观察期
Then 记录人工查证时间、事实覆盖、延迟、失败率和结果可解释性
And 若 Hub 没有可观察增值，阶段输出 stop/retain，而不是继续扩展功能
```

## 10. TDD/SDD 要求

代码开始前必须先完成：

- 本 ADR owner acceptance；
- 一个 Stage Charter 和 Task Context Manifest；
- capability 输入/输出、事件、错误和 Gate 契约；
- 真实 canary 的权限、数据目录、成本和停止条件。

实现必须优先复用：

- DSH Python SDK 的 Agent Loop、Session、Tool、Subagent、MCP 和 Trace；
- LangGraph 的 checkpoint、interrupt、恢复和外层路由；
- 官方 MCP SDK、OpenAI-compatible SDK、Pydantic、SQLAlchemy/Alembic、Zod 和现有 Query/View。

禁止：

- 新增第二套 Agent Loop、第二个账本或第二个状态机；
- 在 Prompt 中伪造时间、来源或工具结果；
- 为了让测试通过而放宽 PIT、authority、freshness 或 Gate；
- 在没有真实调用方时抽象第二领域或公共插件市场。

## 11. Promotion、回滚和停止

DSH 从 candidate 到 active 必须同时满足：

1. replay contract、权限和 PIT 测试全部通过；
2. 真实 capability canary 有成功和失败证据；
3. 研究结果不会出现未认证 Evidence、未来信息或错误 Horizon；
4. prospective 观察期内没有不可接受的失败率、延迟或成本；
5. owner 证明比直接 DSH 更节省人工查证时间或更可靠；
6. owner 通过受控 Promotion command 明确批准。

不满足时：

- 保持 Fixed 或 DSH candidate；
- 不删除历史 Run/Evidence/Artifact/Forecast/Outcome；
- 不重写历史 schema；
- 通过 active pointer/CAS 回滚；
- 将失败样本记录为 FailurePattern/Evaluation asset；
- 若连续两个有界修复仍无法达到价值门，停止该方向，而不是增加更多外围功能。

## 12. Owner Gate 结果

Owner 于 2026-08-31 接受以下结论并要求建立阶段目标、实现和自测：

1. DSH-first：DSH 是唯一研究执行 Harness，Hub 是产品资产和可信边界。
2. 先做官方 DSH Web/原生插件/耐久桥接，再做能力闭合和真实价值验收，不继续扩写 replay Agent 页面。
3. MVP 暂时只做 `crypto_macro` 高影响宏观事件和文本输入。
4. 如果 Hub 比直接 DSH 没有实际增值，则停止继续产品化。

实现授权以 [DSH Native Web Product Core Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 为边界。该授权不包含切 active pointer、扩大 live capability/network、引入 ASR/PPT/第二领域或自动交易。
