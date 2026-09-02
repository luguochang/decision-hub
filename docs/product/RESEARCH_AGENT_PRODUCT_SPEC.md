# 研究智能体主体产品规格

版本：`PRODUCT-RESEARCH-AGENT-2026-08-29.v0.1`
状态：`accepted`（owner 于 2026-08-29 授权完整 R2-R 大阶段）
阶段边界：[R2-R Agentic Research Runtime](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)
架构决策：[ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)、[ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md)

> 本文锁定 R2-R 完成后用户真正得到的产品，而不是描述一个聊天 Demo。Owner 已授权按 R2-R-00 至 R2-R-06 实施；授权不包含自动交易、任意社区插件安装、提前修改 active pointer 或扩展到 ASR/PPT/多用户。

## 1. 产品结论

Decision Hub 的主体是一个**持续运行、任务驱动、证据驱动的研究智能体产品**，不是“用户输入一句，模型返回一段”的问答助手。

系统必须能够：

1. 从人工文本、日历、官方来源、新闻发现以及未来转写来源接收观察。
2. 依据领域 Pack 的 admission policy 判断事件是否值得研究，自动去重、合并修订并建立持久 Run。
3. 围绕研究目标识别关键事实缺口，自主选择授权工具、搜索、读取原文、查询行情或调用 Specialist。
4. 每次新证据都改变后续计划；证据不足时继续补证，而不是直接输出“数据不足”。
5. 在充分度、权限、时间、成本和工具预算内停止，生成带证据引用、反方链、触发/失效条件和后续复核时间的报告。
6. 不依赖用户停留在页面或持续追问；后台任务可恢复，结果主动进入工作台和通知 outbox。
7. 保存 Run、Evidence、Report、Forecast、Outcome、Evaluation 与版本血缘，让长期效果可以被证明或否定。

“主观能动性”在本产品中不是无限自治，而是：在用户授权的目标、工具、预算和安全边界内，系统能自行发现值得处理的问题、决定下一步取证动作并对结果负责地停止。

## 2. 什么算智能体，什么不算

| 行为 | 本项目判断 |
|---|---|
| 用户输入文本，模型一次生成报告 | LLM Call，不是智能体 |
| 固定 policy/counter/synthesis 三次调用 | LLM Workflow，不是完整智能体 |
| 模型发现缺口、选择工具、读取结果并动态继续 | Tool-using Agent |
| 上述 Agent 外还有持久任务、恢复、PIT、Gate、评测和产品页面 | Agentic Product，本项目目标 |

聊天入口可以保留，但只承担四类用途：

- 人工提交新事件；
- 对正在运行的任务补充来源或约束；
- 对已完成报告追问；
- 发出 cancel、retry、recheck、反馈等 owner 命令。

聊天记录不能代替 Event、Evidence、Run、Report 或产品账本。关闭聊天窗口不能终止后台研究。

## 3. 本阶段唯一产品形态

阶段产品名：`U2.1 Research Agent Pilot`。

R2-R 完成后，单 owner 在本机打开 Decision Desk，应直接看到：

- 系统当前发现了哪些事件；
- 哪些任务正在研究、缺什么证据、正在调用什么能力；
- 哪些报告刚完成、为什么可信或为什么只能 `research_only`；
- 30m、24h、72h 分别建议什么、何时触发、何时失效、何时复核；
- 哪些来源、工具、Runtime 或 Worker 异常；
- 哪些结论后来正确、错误或仍未到期；
- 哪些失败样本形成了候选经验，是否等待 owner 审核。

本阶段到此停止，不顺势实现 ASR、自动交易、PPT、多用户、公共插件市场或远程高可用。完成 R2-R-06 后进入真实观察期，不用继续堆功能来掩盖效果不足。

## 4. 端到端工作方式

```text
人工文本 / 日历 / 官方 Feed / 新闻发现 / future Transcript-ASR
                            |
                  ObservationSourcePort
                            |
                 TextEnvelope + provenance
                            |
        去重 / 修订 / 事件聚合 / admission / priority
                            |
                 Event + Trigger Snapshot
                            |
              durable Research Run queue
                            |
           LangGraph 产品生命周期与恢复循环
                            |
          DSH decision-research Agent Session
                  Manager / Tools / Subagents
                            |
      Web Search/Fetch / Official / Market / Specialists
                            |
     EvidenceCandidate -> PIT/来源/鲜度/冲突验证 -> Evidence
                            |
              Information Sufficiency Gate
                 | critical gap + budget
                 +-----------------------> 同一 Session 继续补证
                 | sufficient / bounded stop
                 v
          Decision Snapshot + Causal Case
                            |
             deterministic Publish Gate
                            |
       Report / Forecast / Follow-up / Outbox
                            |
             Outcome / Evaluation / Evolution
```

### 4.1 主动发现不是 LLM 24 小时空转

低成本来源 worker 按日历或 cursor 轮询官方 Feed、授权新闻来源和 broad discovery query。代码 policy 先做去重、来源优先级、事件影响等级和 admission；只有达到阈值的事件才唤醒 DSH 研究 Session。

研究完成后，`next_review_at`、缺口订阅或预定义 horizon 可创建耐久化 recheck 任务。系统到点重新获取指定证据并生成新的 Snapshot generation，不靠模型进程一直保持唤醒。

### 4.2 Agent 不能假装“完全知道”

Manager 根据 Domain Pack 的 Evidence Requirements 计算缺口，并在授权能力内动态取证：

```text
观察当前证据
  -> 选择最关键缺口
  -> 调用 Search/Fetch/Official/Market/Specialist
  -> 验证来源、时间、hash、鲜度和冲突
  -> 更新计划
  -> sufficient ? 结束 : 在预算内继续
```

未知网站先通过通用 Web Search/Fetch 发现，频繁使用且要求精确数值的来源再沉淀为 typed connector。Owner 不需要预先为互联网所有信息写接口；但精确行情、funding、OI、收益率等不能长期只依赖搜索摘要。

### 4.3 有界自主性

默认上限由 Pack/Profile 声明并由代码强制：Evidence round、Tool call、Subagent、总 deadline、单工具 timeout、模型 step timeout、成本预算和结构化修复次数。

Agent 可以选择下一步，不能：

- 自动安装插件或扩大网络/文件权限；
- 修改 Gate、Pack、Skill、active pointer 或生产代码；
- 自动交易、扣费或发布未过 Gate 的方向性结论；
- 无限重试或把缺失数据编造成事实；
- 将 DSH Session 当成业务账本。

## 5. 双层插件与 Runtime 边界

本产品使用两层插件模型：

```text
Product Extension（产品插件式模块）
  decision -> crypto_macro Domain Pack
  future presentation -> ppt Domain Pack

DSH Native Plugin（Harness 可装卸能力）
  Tool / Skill / MCP / Subagent / Memory / Hook / UI / Provider
```

- Product Extension 拥有产品结果、业务规则、历史迁移和评测闭环，可脱离 DSH 存在。
- DSH Plugin 提供 Agent 执行能力和工作台体验，必须经 CapabilityManifest 准入。
- 一个 Product Extension 可以附带薄 DSH bundle，把命令、查询、Skill、Role Profile 和 UI 接入 DSH；卸载 bundle 不能删除业务历史。
- Forecast、Outcome、Brier、Gate、PIT、Promotion 不进入 DSH Session 真源。

LangGraph 与 DSH 不是二选一：

| 组件 | 只负责 | 不负责 |
|---|---|---|
| LangGraph | Run 生命周期、checkpoint/recovery、Evidence round、双 Snapshot、确定性 Gate/commit | 通用 model-tool loop、DSH Session、网页解析 |
| DSH | Manager/Tool/Subagent loop、Skill/MCP、Session/Trace、上下文压缩 | 产品账本、PIT 裁决、Publish Gate、Promotion |
| Decision Hub Kernel/Extension | Event/Evidence/Run/Report/Forecast/Outcome/Evaluation 和权限规则 | Harness 内部状态 |

## 6. 后台组件和代码所有权

实现路径以 R2-R Charter 为技术真源，本节锁定产品职责：

| 组件 | 责任 | 目标位置 |
|---|---|---|
| Source Adapter | 把不同来源转换为统一文本观察 | `packages/source_adapters/<source>/` |
| Admission/Discovery | 去重、修订、事件聚合、影响等级、是否建 Run | Kernel application + Domain Pack policy |
| Research Queue | queued/claim/lease/heartbeat/retry/cancel/recovery | Kernel application/persistence |
| Research Lifecycle | Trigger Snapshot、bounded rounds、Decision Snapshot、commit | `packages/orchestration/langgraph/` |
| DSH Runtime | profile/session/tool/subagent/trace/result 映射 | `packages/runtime_adapters/dsh_runtime/` |
| Capability Gateway | 权限、域名、schema、timeout、成本、PIT 和插件映射 | Kernel Port + provider/workbench adapter |
| Domain Pack | 根因链、证据要求、来源优先级、预算、角色和评测 | `packs/crypto_macro/` |
| Query Projection | 将账本和 DSH trace ref 转成人可读页面模型 | `packages/query_views/research/` |
| API | admission、query、owner command、SSE；不执行长研究 | `apps/hub_api/` |
| Research Worker | claim Run 并执行完整研究生命周期 | `apps/hub_worker/research.py` + `composition.py` |
| Decision Desk | 任务、研究过程、报告、健康和评测 | `apps/decision-desk/src/research/` |

禁止复制 Graph、数据库、Provider client、DTO 或前端应用来新增角色。Role 多数通过 Profile/Pack 声明组合；新原子能力才增加 DSH/MCP/Python Capability Plugin。

## 7. 持久任务状态机

```text
discovered
  -> admitted
  -> queued
  -> researching
       -> acquiring_evidence
       -> assessing_sufficiency
       -> researching        # 有界下一轮
  -> decision_ready
  -> gated
       -> published
       -> research_only
       -> rejected
  -> monitoring / recheck_scheduled
  -> outcome_due
  -> evaluated

任何非终态 -> retry_wait / cancelled / failed
```

状态变化必须形成规范化 Run/ResearchTraceEvent。DSH 原始事件只保留 trace ref/hash；Query View 将它们投影成人类语言。API 重启、页面关闭或 Worker 崩溃不得丢 Run，不得重复提交 Evidence、Report 或通知。

## 8. 前端信息架构

### 8.1 首页：Research Command Center

首屏不是营销页，也不是空聊天框。默认展示：

- `正在研究`：事件标题、重要度、当前阶段、已用时间、证据覆盖、下一动作；
- `需要关注`：来源冲突、关键数据不可用、预算停止、待 owner 决策；
- `最新报告`：结论、Gate 状态、三个 horizon 摘要、完成时间；
- `即将发生`：高影响日历事件和已安排 recheck；
- `系统健康`：research/realtime/evolution worker、DSH profile、Provider、关键 Source/Capability。

页面加载失败必须显示真实错误，不回退 demo 数据。

### 8.2 研究任务详情

用户打开一个正在执行的 Run，应实时看到：

1. 顶部状态：事件、优先级、Runtime/Profile、round、deadline、cost/tool budget。
2. 当前动作：正在验证什么、调用哪个能力、为什么需要它。
3. Evidence Coverage：hard/soft requirements、已覆盖、缺口、鲜度和冲突。
4. Tool Activity：查询摘要、来源、耗时、失败和 fallback；默认不显示 raw JSON。
5. Research Plan：Manager 计划、Specialist 状态和有限 replan。
6. Stop Reason：为什么继续、为什么停止、是否需要 owner 介入。

API 使用基于 `ResearchTraceEvent` 投影的 SSE 推送单向进度；前端不连接 DSH 内部事件流，不读取 LangGraph state 或 SQL。断线后先读取快照，再从 sequence cursor 续接。

### 8.3 最终报告页

报告按阅读决策的顺序组织，不输出大段 JSON：

1. `发生了什么`：事件身份、原文要点、与预期差异。
2. `为什么影响市场`：可观测事实 -> surprise -> root driver -> 传导链。
3. `市场是否确认`：利率/美元/现货/衍生品/跨资产证据。
4. `最强反方解释`：什么证据支持相反结论。
5. `30m / 24h / 72h`：独立 action、证据、trigger、invalidation、expires、next review。
6. `仍然不知道什么`：关键缺口、confidence cap、停止原因。
7. `证据和引用`：Official/Market/Web 分组、发布时间/获取时间、鲜度和冲突。
8. `后续动作`：已计划 recheck、Outcome 到期时间、是否需要 owner 反馈。

未校准概率必须标记“模型主观、尚未校准”，不能只用醒目百分比制造确定性。`no_trade` 也必须解释何种实时条件会改变状态。

### 8.4 其他页面

- `Event Inbox`：新发现、去重合并、被 admission 拒绝及原因；
- `Calendar & Watch`：未来事件、关注人物/主题、recheck 计划；
- `Sources & Capabilities`：健康、最近成功、限流、权限、版本和 fallback；
- `Evaluation`：Outcome、Brier、成本后结果、覆盖率、失败原因和版本对比；
- `Assets & Evolution`：Dataset/Experience/FailurePattern/candidate 与 owner Promotion；
- `Ask/Steer`：报告侧栏中的追问和补充入口，不作为产品首页和唯一入口。

## 9. 前后端公开契约方向

R2-R-00 才能正式新增 canonical schema；禁止先在 Python/TypeScript 各写一份。产品需要的最小公开面为：

```text
POST /v1/research/observations        -> 202 + event_id + queued research.v1 run_id
GET  /v1/research/runs                -> task queue / filters
GET  /v1/research/runs/{run_id}       -> normalized research snapshot
GET  /v1/research/runs/{run_id}/events?after=<seq> -> SSE progress
POST /v1/research/runs/{run_id}/commands -> cancel/retry/recheck/feedback
GET  /v1/reports/{artifact_id}        -> human-readable report view
GET  /v1/calendar                     -> upcoming events/rechecks
GET  /v1/capabilities                 -> audited capability health
```

Query/View DTO 只包含页面需要的摘要、引用和状态。模型原始响应、完整网页、Provider payload、secret 和 DSH 内部 JSON 不进入默认 API。

## 10. 未来 ASR 接口

ASR 不是 R2-R 主体任务，也不能成为开始主体实现前的阻塞项。现有核心边界保持：

```text
AudioCapture / Stream / File
  -> future AsrProviderPort
  -> Transcript Fragment
  -> TranscriptSourceAdapter
  -> TextEnvelope(source_type=transcript)
  -> 与手工文本/Feed 完全相同的 admission 和研究链
```

需要提前保留但现在不实现的只有：

- 来源 ID、语言、segment 时间和 revision；
- 原始音频/转写产物的外部 ref/hash；
- partial/final transcript 与重复片段合并；
- speaker/置信度等可选 metadata；
- 本地或云 ASR 通过同一 Port 替换。

研究 Runtime 只消费经过来源适配的文本和 Evidence ref，不 import ASR SDK，不管理声卡、直播流或模型显存。等主体通过 R2-R-06 后，再独立制定 ASR Stage Charter 并复用已验证的 Meeting Copilot adapter。

## 11. “正式效果”的验收门

不能再以“接口存在、页面能开或单次模型返回”为完成。`U2.1 Research Agent Pilot` 至少满足：

### 工程行为

- 只有讲话文本且缺市场数据时，Agent 主动识别 hard gaps 并产生真实 Tool/Subagent loop。
- 新证据落账后至少发生一次可观察的 plan/sufficiency 变化。
- Search/Official/Market 失败时有 bounded fallback，无法取得时明确 confidence cap 和 stop reason。
- 三个 horizon 的 evidence、trigger、invalidation、expiry 和 next review 不完全重复。
- API/Worker/DSH 任一重启后 Run 可恢复且最多一次提交。
- DSH 不可用时 fail-closed 或按显式 policy 回退 fixed baseline，不生成伪 Agent 轨迹。

### 用户可见效果

- 用户不提交文字，授权的高影响来源事件也能自动进入任务中心并完成研究。
- 页面在任务运行期间显示至少 queued、researching、acquiring evidence、assessing、completed/degraded 等真实变化。
- 报告可从关键结论点击回具体 Evidence、来源和时间。
- 所有缺口、冲突、停止原因、未校准概率和 Runtime fallback 都能用人话看到。
- 默认页面没有大块 raw JSON，不需要用户打开终端理解系统是否工作。

### 真实价值证据

- 10 至 20 个 PIT replay 对比 fixed baseline 与 DSH candidate；
- 至少一个新发生的真实高影响事件完成端到端 live acceptance；
- 记录 hard coverage、引用有效率、PIT violation、时延、费用、工具失败率和 owner usefulness；
- Outcome 到期后再累计 Brier/成本后结果，不用 LLM Judge 或一次漂亮报告宣称盈利。

只有上述门全部通过，才可以称为“单 owner 可试运行的研究智能体”。它仍不等于盈利系统或自动交易系统。

## 12. 实施顺序与停止边界

本规格不增加新的无限阶段，直接由现有 R2-R-00 至 R2-R-06 落地：

| 工作卡 | 产品结果 |
|---|---|
| R2-R-00 | 锁 canonical contract、Pack policy、Warsh failure fixture 和页面 View contract |
| R2-R-01 | 真实 DSH Session、Tool/Subagent loop、受限 profile 和 trace mapping |
| R2-R-02 | Web/Official/Market 补证、Evidence lineage、双 Snapshot |
| R2-R-03 | bounded research rounds、充分度、根因链和独立 horizon |
| R2-R-04 | 自动发现、durable research worker、recheck 与恢复 |
| R2-R-05 | Command Center、研究详情、报告、健康和 SSE 前端 |
| R2-R-06 | replay/live/owner acceptance，决定是否 promotion |

R2-R-06 完成后必须停止开发并观察真实效果。ASR、PPT 或第二领域只能通过新的价值证据和独立 Stage Gate 开始。

## 13. 当前实现差距

当前仓库已经有可保留的账本、PIT、Gate、Forecast/Outcome/Evaluation、Source adapter、Worker、Operations 和前端骨架，但仍缺：

- 真实 DSH SDK/profile/Session/Tool/Subagent runtime；
- 正式研究主链的主动补证和 Evidence Sufficiency continuation；
- durable research worker；
- 主动 discovery 到研究报告的真实网络闭环；
- ResearchTraceEvent 的人可读投影和 SSE；
- 本规格定义的 Command Center、研究详情和最终报告页面；
- 独立 horizon 的真实数据和业务效果证据。

因此当前产品必须继续标记为 fixed baseline/工程骨架，不能称为正式研究智能体。

## 14. Owner Gate

Owner 已于 2026-08-29 接受本文、ADR-0008、ADR-0009 和 R2-R Charter，并授权完整 R2-R 大阶段。唯一第一步仍是 `R2-R-00`。不得跳到 ASR、UI 美化、第三方插件安装或真实 Provider 调参。R2-R-00 先用 SDD/BDD/TDD 锁住研究契约、失败样本、页面 View contract 和架构依赖测试，再进入 DSH adapter 实现。
