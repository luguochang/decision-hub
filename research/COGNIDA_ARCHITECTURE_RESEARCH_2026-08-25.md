# Cognida 项目思想与架构调查

> 调查对象：[larry-zy/cognida](https://github.com/larry-zy/cognida)  
> 调查日期：2026-08-25（Asia/Shanghai）  
> 源码快照：`main@1fc211be8dab4635683e926768d9ad0363ff1087`  
> 文档性质：研究材料，不是已确认 ADR，不直接改变 Decision Hub 实施基线

## 1. 结论先行

Cognida 最值得研究的不是某个 Agent Loop，也不是它使用了 Go、Eino、MCP、Milvus 或 Neo4j，而是它尝试把以下几个通常分散的系统收敛为一个产品闭环：

```text
数据/知识接入
  -> 质量与权限治理
  -> 可复用资产沉淀
  -> Agent 分析和受控执行
  -> 结构化结果交付
  -> 轨迹与结果评测
  -> 经验沉淀
  -> 反哺后续运行
```

它的核心产品判断可以概括为：

> Agent 只是平台里的执行内核；真正的产品是数据、知识、治理、结果、评测和经验共同构成的生命周期系统。

这个判断与当前 Decision Hub 的长期方向高度相容，也进一步证明我们不应把 Pi、DSH、Codex 或 LangGraph 中任何一个 Harness 当成产品本身。真正需要由自己拥有的是：

```text
契约 + 业务账本 + 来源与证据 + Artifact + Gate
+ Outcome + Evaluation + Feedback + Domain Pack
```

但不建议把 Cognida 直接选为 Decision Hub 或未来个人 Agent 平台的底座。原因不是它没有价值，而是：

1. 它是企业数据与知识平台，不是实时事件决策内核；
2. 默认技术栈明显重于当前单 owner 场景；
3. 项目于 2026-08-09 才创建，无正式 Release；源码已经体现明显的生产工程取向，但公开信息尚不足以证明长期、大规模生产运行成熟度；
4. 主 ReAct 循环、工具门和评测已经有实质实现，但通用 Supervisor、Parallel 等编排原语仍比较基础；
5. 它没有证明 Agent 工作流具备 LangGraph/Temporal 类 durable checkpoint、进程重启恢复和长生命周期状态机语义；
6. 它的跨语言 Proto 单一来源只覆盖部分 gRPC 边界，不能直接等同于全平台 schema 单一来源。

因此，当前推荐是：

> 把 Cognida 当作“AI-Native 生命周期平台”的优秀参考实现和反例库，选择性吸收其模式；不 fork、不引入主系统、不因此更换当前 `Product Kernel + Decision Domain Extension + LangGraph + AgentRuntimeAdapter` 的边界。

## 2. 项目快照与成熟度

调查时的公开仓库快照：

| 项目 | 结果 |
|---|---|
| 仓库 | `larry-zy/cognida` |
| 创建时间 | 2026-08-09 |
| 最近推送 | 2026-08-24 |
| 主语言 | Go |
| 许可证 | MIT |
| Star / Fork | 约 173 / 15 |
| Release | 无 |
| 主要贡献者 | 作者约 145 次贡献；另有两名贡献者各约 4 次 |
| README 自述 | 企业级、受治理、可审计、可度量的“数据 + 知识”Agent 平台 |

这些数字不能单独证明已经有成熟生产部署，但源码中的版本化 migration、幂等、权限硬门、超时/预算、上下文治理、评测落库、Trace、恢复测试和 CI 表明它不是只写 README 的展示项目，而是按真实生产故障面建设的平台代码。

公开 issue 也能看到 Agent 主循环仍在快速修复：

- `#3`：流式输出可能混入中间/截断重试内容；
- `#5`：token budget 把 context size 当 cost，并可能重复计算历史；
- `#6`：Provider 不返回 usage 时 token budget 会被静默禁用；
- `#7`：流式路径丢失 `terminated_by / partial` 元数据；
- `#4/#8`：空答和畸形参数绕过自修复护栏的问题刚在当前 HEAD 修复。

所以本调查对它的定位是：

```text
生产工程取向明确、实现面广、多个核心模块有实质工程化
  + 公开的长期/规模化生产成熟度仍待时间和采用证据验证
```

## 3. 它到底在做什么

### 3.1 不是“问数 Bot”，而是生命周期平台

Cognida 明确反对只在数据平台外面套一层聊天入口。它将 Agent 放进数据和知识生命周期的各个环节：

| 生命周期 | 结构化数据 | 非结构化知识 |
|---|---|---|
| 接入 | 数据源、SQL Schema | 文档、OCR、URL、文件 |
| 治理 | 指标语义层、数据质量、权限 | 去重、质量、PII、知识库范围 |
| 沉淀 | Result Store、语义模型 | 分块、向量、知识图谱 |
| 分析 | Text2SQL、统计、归因、可视化 | RAG、多跳图谱、DeepResearch |
| 评测 | SQL execution accuracy、Agent 轨迹 | 检索、faithfulness、相关性 |
| 进化 | 会话经验、Skill、图谱 | 经验召回、策略改进 |

这里真正有价值的不是“功能多”，而是它要求一个能力必须能与前后工位交换产物。孤立的 Agent 功能如果不能进入接入、资产化、分析、评测、反馈链，就只是演示能力。

### 3.2 Agent 是执行面，不是业务事实源

它使用 Eino 和自研的 ReAct 执行层处理模型、工具、Hooks、上下文和多 Agent，但平台的用户、数据源、知识库、评测、Trace 和结果由其他模块管理。

这一思想对我们的对应关系是：

```text
Cognida Agent 内核          -> Decision Hub AgentRuntimeAdapter
Cognida 数据/知识平台       -> Product Kernel + Domain Extensions
Cognida Result Store/A2UI   -> Artifact Store + Renderer
Cognida Evaluation          -> Replay/Evaluation Subsystem
Cognida Experience          -> Feedback -> Review -> Versioned Pack
```

## 4. Agent 内核的真实实现

### 4.1 Eino 之上仍有大量自有 Runtime 逻辑

项目依赖 `github.com/cloudwego/eino v0.7.32`，但不是简单配置 Eino 就得到全部能力。源码自己实现或包装了：

- `Builder`：模型、工具、Hooks、中间件、上下文、协作、预算和护栏装配；
- 统一 `Chat/Stream` 主干；
- ReAct tool loop；
- 最大迭代、token、wall-clock 和单工具 timeout；
- wind-down 部分结果收尾；
- 上下文三级压缩和 reasoning 驱逐；
- ToolPolicy、审批、审计和自修复；
- 子 Agent 委派轨迹向父运行穿透；
- 流式事件到前端时间线的映射。

这证明一个现实：使用 Agent 框架不会消灭产品 Runtime 工程。框架负责通用 loop 和抽象，产品仍必须补预算、权限、上下文、错误、轨迹和结果契约。

对 Decision Hub 的启发不是“也写一套 Eino Loop”，而是：

> 通过 `AgentRuntime` 契约隔离 Harness，同时把预算、权限、Evidence/Gate 和 Run 状态留在自己的 Kernel/Workflow 层，不期待 Pi 或 DSH 自动解决全部产品问题。

### 4.2 上下文工程值得研究

Cognida 对上下文不是简单截断，而是分层处理：

1. 单条超长消息先压缩；
2. 全对话超过阈值后按完整轮次折叠，维持 tool-call/tool-result 配对；
3. 驱逐陈旧 reasoning，避免思考内容长期堆积；
4. 使用真实 BPE 计数器，减少中文夹数字场景下的预算误差；
5. 大数据不直接全部回灌模型，而是存 Result Store，只给模型 `result_id + schema + sample + aggregate`。

其中第 5 点尤其适合未来市场数据和 PPT/文档产品：

```text
大对象/行情序列/文档结构
  -> Artifact 或 Result Store
  -> 模型只获得有界 Envelope + object_id
  -> 需要时由工具按 ID 获取切片
```

这样可减少 token、避免上下文污染，也可以强制访问归属和审计。

### 4.3 自我修复不是裸重试

Text2SQL 发生错误后，Cognida 会做：

```text
错误分类
  -> schema/列/候选表/真实值提示
  -> 相同失败签名计数
  -> 达阈值后要求换路径
  -> 达上限后交付部分结果或受控失败
```

这个模式可以直接转译为 Decision Hub 的 Provider/Research 故障语义：

```text
失败
  -> typed error
  -> retryable / non-retryable
  -> 缺失证据、过期、冲突、权限或预算分类
  -> 补证或重新规划
  -> degraded / research_only / reject
```

应该吸收的是“类型化错误 + 有界修复 + 部分结果语义”，不是复制 SQL 领域代码。

## 5. 多 Agent 的真实水平

### 5.1 有两套不同层级的机制

Cognida 同时存在：

1. 通用组合原语：`Sequential / Parallel / Loop / Conditional / Supervisor`；
2. Data Agent 的治理型委派：`CollaborationRegistry + delegate_to_agent + delegate_parallel + DelegationEnvelope`。

第二套比第一套更值得研究。Data Agent 给子代理声明：

- `purpose`；
- `data_scope`；
- 最小工具集；
- `risk_class`；
- `isolated / summary` 上下文模式；
- 委派 goal、scope、max_rows 等约束；
- 子代理只回传紧凑摘要和 `result_id`。

这与我们讨论的 `SpecialistProfile` 很接近，但有一个关键区别：

> 角色不是一段 Prompt，而是“能力 + 工具权限 + 数据范围 + 上下文范围 + 预算 + 输出契约”的运行配置。

### 5.2 通用 Supervisor 仍很基础

源码中的通用 `Supervisor` 会让协调 Agent 返回 worker 序号，然后从文本里找到第一个数字；`NamedSupervisor` 则在模型文本中做名称子串匹配。它没有严格结构化路由结果，也没有展示可靠的任务图、证据覆盖或失败恢复语义。

`Parallel` 的基础实现则是给所有 Agent 同一输入，最后拼接各回答；它不是 Judge、冲突矩阵或共识协议。流式 Parallel 还会重建 chunk metadata，存在丢失子流原始结构化元数据的风险。

因此不能从 README 的“Supervisor/Parallel 已实现”推导出：

```text
它已经解决开放式多 Agent 根因链
或
它可以替换 LangGraph 的 durable workflow
```

### 5.3 对 Decision Hub 多 Agent 的正确借鉴

应吸收：

- Specialist 注册表；
- 每个 Specialist 的能力、工具、风险和上下文声明；
- Supervisor 动态选择受限能力；
- 委派 Envelope；
- 子任务和父任务的 Trace 穿透；
- 并行任务数量、深度、deadline 和成本预算；
- 大结果按引用回传。

不应吸收：

- 从自然语言中解析 worker 名称/序号；
- 仅拼接多 Agent 输出；
- 让 Agent 自己决定最终发布；
- 把一次进程内编排当作 durable workflow；
- 把“多 Agent”当作比单 Agent 更好的先验结论。

Decision Hub 仍应保持：

```text
LangGraph：运行生命周期、checkpoint、恢复、动态 fan-out
AgentRuntime：Supervisor/Specialist 的 agent loop
Domain Gate：唯一发布裁决
SQLite/Postgres：唯一业务账本
```

## 6. Skill、MCP、Hook 和插件思想

### 6.1 Skill 使用渐进式披露

Cognida 的 Skill 不是把所有 `SKILL.md` 全塞进 Prompt：

```text
Level 1：system prompt 常驻 name + description + when_to_use
Level 2：模型判断需要后调用 skill_invoke 加载正文
Level 3：按需使用 supporting files / tools
```

它还给目录加了上限：优先策展 Skill、限制条数和描述长度，自动蒸馏 Skill 靠后，避免目录无界增长。

这是值得直接吸收的 Skill 管理思想：Skill 是可发现、可版本、可评测的方法资产，不是把一堆 Prompt 文件放进目录就完成“可插拔”。

### 6.2 Skill 的工具权限由代码执行

`allowed_tools / disallowed_tools` 不只是提示词。显式 `skill_invoke` 后会激活 `ToolPolicy`，在工具执行前做硬拦截；会话还有 `read < write < etl` scope，危险工具可以进入人工审批。

这个方向正确，但源码目前仍有一个需要警惕的默认值：未登记在 `toolScopeRequirements` 的新工具默认按 `read` 处理。若未来新增写工具但忘记登记，它可能被最低权限会话放行。

我们的规则应更严格：

```text
工具必须显式注册 risk_class / capability / side_effect
未知工具默认 deny
写/外送/扣费/交易必须显式审批或永久禁用
```

### 6.3 MCP 是跨语言能力通道，不是业务边界

Cognida 通过 MCP 调 Python analytics，适合把统计、归因、异常检测做成 Agent 可调用工具。Go 仍持有业务编排和结果。

这说明 MCP 适合：

- 可复用能力服务；
- 跨 Harness 的工具接入；
- 低耦合实验能力；
- 对外暴露稳定 tool schema。

但 MCP 不应该替代：

- 业务账本；
- 领域事件协议；
- 事务和幂等；
- Gate；
- 长流程 checkpoint。

### 6.4 Hook 用于横切能力，但不能隐藏业务状态机

Cognida 用 Hook 做意图澄清、结论生成、压缩、反思和 guardrail。适合 Hook 的是运行前后横切逻辑；不适合藏在 Hook 里的，是事件状态变更、Evidence 冻结、发布和 Outcome 归档。

Decision Hub 可以借鉴 Hook seam，但所有会改变正式业务状态的操作必须走公开 use case 和账本事务。

## 7. 结果、评测和可观测

### 7.1 Result Store 和 A2UI

Cognida 不只返回 Markdown。完整数据集存进 Redis Result Store，模型得到紧凑 Envelope；A2UI 使用 `UISpec + DataModel` 生成表格、图表和结论，前端通过 SSE 渲染时间线与画布。

可借鉴的边界是：

```text
业务结果对象
  -> Renderer / UISpec
  -> Web/Markdown/JSON/Timeline
```

不能照搬的部分是：Cognida Result Store 默认 TTL 约 30 分钟，更像中间数据缓存，不是长期 Artifact 账本。Decision Hub 的 Forecast、DecisionArtifact、Outcome 和 Evaluation 必须长期持久化、版本化、可 PIT 回放，不能放进短期缓存当唯一事实源。

### 7.2 评测被提升为独立子系统

Cognida 的评测不是一个 Prompt 测试脚本，而有独立数据集、任务、Worker、结果表、指标注册表和前端。它区分：

- QA；
- RAG；
- Agent；
- Text2SQL。

Agent 评测记录答案、工具名称/顺序、步骤、时延、token、LLM 调用次数，并用 `request_id` 深链到完整 Trace。原始运行产物先落库，Python 评分失败也不丢昂贵运行结果，这一持久化顺序值得借鉴。

但它目前的“轨迹级评测”主要基于工具名称、顺序和步骤摘要，不等于已经严格评估：

- 工具参数是否正确；
- 证据是否满足 PIT；
- 引用是否真的支持结论；
- 多 Agent 冲突是否被正确处理；
- 决策概率是否校准；
- 结果是否产生真实收益。

Decision Hub 必须在通用轨迹指标之上增加领域指标：

```text
evidence coverage / citation entailment / freshness / PIT compliance
+ latency / cost / tool error / degraded path
+ forecast outcome / Brier / calibration
+ fee-aware and slippage-aware PnL
+ abstention quality / Gate false-positive rate
```

### 7.3 Trace 与业务 Run 必须区分

Cognida 使用 OpenTelemetry span 树记录 Agent、工具、委派等调用链，这是运行调试的正确做法。

但 Trace 不是业务账本：Trace 可以过期、采样或迁移，业务 `Run/Artifact/Forecast/Outcome` 必须有稳定 ID 和长期一致性。我们的设计应继续保持：

```text
AnalysisRun（业务事实） 1 ---- N Trace/Span（技术观测）
```

## 8. 经验沉淀与“自进化”

Cognida 的链路大致是：

```text
空闲会话
  -> 客观失败 pre-gate
  -> LLM 蒸馏 Experience
  -> 置信度 gate
  -> MySQL Experience
  -> 可选写知识图谱
  -> 可选生成 SKILL.md
  -> 可选后续召回
```

它做对了几件事：

- 写侧、读侧、Skill 落地分别开关，默认关闭；
- 明显失败会话先用廉价规则筛掉；
- 按 session 幂等，失败/跳过也落占位，避免无限重扫；
- 自动沉淀 Skill 标为实验性；
- 自动 Skill 有目录预算和 30 天加载 TTL；
- 图谱/Skill 写入失败不阻塞主对话。

但 Decision Hub 不能把这套机制原样用于 Doctrine、Gate 或交易策略。原因是一次会话“看起来成功”不等于它的市场判断后来正确；模型自评置信度也不是 Outcome 验证。

我们的正式进化链必须是：

```text
Run + Artifact + Forecast
  -> Outcome
  -> Evaluation
  -> 候选经验/规则/Prompt 变更
  -> 人工或严格规则 Review
  -> 版本化 Pack/Doctrine/Profile
  -> PIT Replay + Shadow
  -> 达门槛后提升为默认版本
```

自动蒸馏可以生成“候选研究记忆”，不能直接改变发布 Gate、风险参数或正式策略。

## 9. 技术架构的优点与代价

### 9.1 它的技术分工

```text
Go
  API / Agent Runtime / Data Agent / RAG / Evaluation orchestration

Python
  文档解析 / OCR / 质量计算 / Analytics / Evaluation metrics / MCP

Vue
  平台管理 / Agent 画布 / A2UI / Evaluation / Trace

Storage
  MySQL + Redis + Milvus + Neo4j
```

Go 和 Python 通过 gRPC、HTTP、MCP 通信；部分 gRPC schema 使用根目录 Proto 和 Buf 生成两端代码。

### 9.2 适合 Cognida，不适合直接照搬到当前项目

Cognida 的产品范围包含多租户、数据源、知识库、向量检索、图谱、质量中心、评测中心和管理 Web，所以重栈有其合理性。

当前 Decision Hub 是单 owner、文本核心优先、外部 LLM 为主。首版若照搬将引入：

- Go/Python 两套主业务运行时；
- MySQL、Redis、Milvus、Neo4j、etcd、MinIO；
- gRPC、HTTP、MCP 三套内部通信；
- 多租户、RBAC、知识库和 Data Agent 等非首要边界；
- 更高的部署、排障、备份和契约维护成本。

当前仍应采用：

```text
Python Product Kernel + Decision Extension
+ SQLite WAL
+ LangGraph Workflow Runtime
+ Pi/DSH/Codex adapters
+ 外部 LLM
```

只有真实需求出现后才按 ADR 引入 PostgreSQL、Redis 或独立向量/图谱设施。

### 9.3 “Proto 单一来源”需要准确理解

Cognida 的根 `proto/` 和 Buf 确实是 Go/Python 部分 gRPC 接口的单一来源，值得参考。但当前 Proto 主要覆盖 document/quality 等有限接口；Python Evaluation 使用 HTTP 数据模型，MCP 使用自己的 tool schema，Web 还有 TypeScript 类型。

因此它没有证明“全平台所有跨模块 schema 已经完全由一个源 codegen”。我们的契约治理仍需坚持：

```text
contracts/schemas/*.schema.yaml
  -> Pydantic
  -> Zod/TypeScript
  -> OpenAPI / fixtures validation
```

Proto 只在真正需要 gRPC 的边界作为该边界的 canonical source，不与 JSON Schema 双写同一契约。

## 10. 与 Pi、DSH、LangGraph 的区别

| 项目 | 本质定位 | 擅长 | 不应拥有的东西 |
|---|---|---|---|
| Cognida | 企业数据/知识产品平台 | 数据治理、问数、RAG、评测、画布和管理面 | 我们的市场领域账本与 Gate |
| Pi SDK | 可嵌入 Agent Runtime | agent loop、工具、上下文和角色执行 | 业务事实源、Outcome、发布权 |
| DSH | 本地优先 Agent Harness/Workbench | Skill、MCP、研究会话、Web、计划与人工深研 | 自动决策账本和最终发布权 |
| LangGraph | 状态图/Workflow Runtime | checkpoint、恢复、中断、动态 fan-out | 领域事实源和业务 Gate |
| Product Kernel | 自有产品内核 | Task/Run/Artifact/Evaluation/Version/Feedback | Harness 内部实现 |
| Decision Extension | 市场决策领域 | Event/Evidence/Forecast/Outcome/Gate | 通用办公/PPT 领域对象 |

Cognida 不是 Pi/DSH 的同类替代品。它更接近“已经把一套 Agent Runtime 嵌入企业数据产品后的完整应用平台”。

如果要研究其代码，应该带着两类问题：

1. 一个 Agent 产品如何把 Runtime、数据、评测、结果和治理连起来；
2. 当平台功能面过大时，哪些模块会变重、重复和难以验证。

## 11. 对 Decision Hub 的具体影响

### 11.1 建议纳入架构基线的思想

以下内容与现有方案兼容，建议在最终确认时提炼成 ADR/契约要求：

1. **生命周期闭环**：`Observation -> Evidence -> Run -> Artifact -> Outcome -> Evaluation -> Reviewed change`。
2. **运行产物先落库**：评分服务失败不能丢掉昂贵 Agent 运行。
3. **typed error + 有界自修复**：按错误类型决定补证、重试、重规划、降级或拒绝。
4. **data-by-reference**：大行情、大文档和分析结果使用 `object_id/result_id + envelope`。
5. **Specialist 治理描述**：能力、工具、数据范围、风险级、上下文模式、预算和输出 schema 一起注册。
6. **委派轨迹穿透**：父 Run 能关联所有子任务和工具调用，但不泄漏完整无关上下文。
7. **Skill 渐进披露和预算**：目录有界，正文按需，自动 Skill 默认实验性。
8. **经验多门控**：写、读、晋级分离；任何经验先是候选资产。
9. **Artifact 与 Renderer 分离**：UI/Markdown/JSON 只是正式 Artifact 的投影。
10. **Trace 与 Run 分离**：OpenTelemetry 用于排障，业务账本用于事实和回放。

### 11.2 不建议纳入的内容

1. 不引入 Cognida 作为主依赖或服务；
2. 不 fork 它的 Agent Runtime；
3. 不切换为 Go/Python 双主服务；
4. 不提前引入 MySQL、Redis、Milvus、Neo4j、etcd、MinIO；
5. 不采用文本解析式 Supervisor 路由；
6. 不把 Parallel 拼接当作多 Agent 共识；
7. 不让自动蒸馏 Skill 修改正式 Doctrine/Gate；
8. 不把短 TTL Result Store 当长期 Artifact Store；
9. 不把 README 的功能矩阵当作生产成熟度证明；
10. 不因为它“全平台”就扩大 Decision Hub R0 的产品边界。

### 11.3 对当前底座选择的结论

Cognida 调查不会改变当前推荐：

```text
自己的 Product Kernel                 长期资产
自己的 Decision Domain Extension      BTC/宏观业务资产
LangGraph                              durable workflow
Pi SDK Adapter                         生产 Agent Runtime 候选
DSH Adapter                            人工研究台/MCP/Decision Desk
Codex Adapter                          研发与代码生产能力
Alert/Meeting Copilot Adapter          复用既有领域/来源能力
```

它反而加强了一个判断：真正有长期价值的不是再选一个 Harness，而是把“输入、运行、结果、评测、反馈、版本”做成可积累的产品闭环。

## 12. 值得继续拆解研究的代码方向

如果后续进入实现前的针对性研究，最值得拆的不是整个仓库，而是以下小模式：

| 研究主题 | Cognida 参考位置 | 我们要验证的目标 |
|---|---|---|
| Result Envelope | `agent/resultstore` | Market/Document Artifact 的 data-by-reference 契约 |
| ToolPolicy | `agent/framework/tool_policy.go` | 默认拒绝、显式风险分类和审批契约 |
| Collaboration Envelope | `agent/framework/collab_*` | Specialist 委派、上下文防火墙和 Trace 穿透 |
| Context Budget | `agent/context`、`eino_toolloop.go` | 多模型 token/cost/deadline 的统一预算 |
| Evaluation Worker | `service/evaluation/worker.go` | 原始产物先持久化、评分可重入 |
| Experience gates | `agent/experience` | Candidate -> Review -> Replay -> Promote 生命周期 |
| Skill catalog | `agent/skills/catalog.go` | 有界目录和渐进式披露 |
| A2UI | `agent/genui` | ArtifactRenderer，不反向污染领域模型 |

研究方式应是抽取模式、写入自己的 ADR 和契约测试，不复制一批与领域不匹配的 Go 代码。

## 13. 最终判断

### 产品思想评分

| 维度 | 判断 |
|---|---|
| 生命周期产品思维 | 很值得借鉴 |
| Agent 与平台关系 | 定位正确 |
| 数据/知识治理 | 对企业场景有价值 |
| 评测与 Trace | 方向正确，部分实现有深度 |
| Skill/经验治理 | 有价值，但不能直接用于高风险决策自进化 |
| 通用多 Agent | 主执行与治理工程化较强；基础 Supervisor/Parallel 原语仍需继续成熟 |
| durable workflow | 未看到可替代 LangGraph/Temporal 的证据 |
| 当前部署复杂度 | 对 Decision Hub 过重 |
| 直接作为我们的底座 | 不推荐 |
| 作为架构参考库 | 推荐持续关注 |

一句话结论：

> Cognida 最值得带走的是“Agent 内生于产品生命周期，但不拥有产品事实”的思想；最不应该带走的是“为了覆盖完整平台而一次性引入全部基础设施和功能面”。

## 14. 主要一手来源

- [README：产品定位与能力矩阵](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/README.md)
- [Go Agent Builder](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/framework/eino_builder.go)
- [统一 Agent 执行主干](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/framework/eino_agent.go)
- [ReAct Tool Loop 与运行预算](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/framework/eino_toolloop.go)
- [通用 Supervisor](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/orchestration/supervisor.go)
- [通用 Parallel](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/orchestration/parallel.go)
- [子 Agent 治理与最小工具集](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/presets/data_agent/subagents.go)
- [工具权限硬门](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/framework/tool_policy.go)
- [Skill 渐进式目录](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/skills/catalog.go)
- [Skill 加载与自动 Skill TTL](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/skills/loader.go)
- [经验沉淀 Worker](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/experience/worker.go)
- [经验客观失败门](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/experience/pregate.go)
- [Agent 评测执行器](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/evaluation/executor/agent.go)
- [评测 Worker 与持久化顺序](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/evaluation/worker.go)
- [Result Store](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/resultstore/store.go)
- [Result Envelope](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/service/agent/resultstore/envelope.go)
- [Trace 领域实体](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/services/cognida-go/internal/model/trace/entity.go)
- [Docker Compose 全栈依赖](https://github.com/larry-zy/cognida/blob/1fc211be8dab4635683e926768d9ad0363ff1087/docker-compose.yml)
- [公开 Issues](https://github.com/larry-zy/cognida/issues)
