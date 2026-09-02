# Decision Hub 产品交付控制书与最终验收包

版本：`PRODUCT-DELIVERY-CONTROL-2026-09-01.v1`
状态：`accepted / E2-L passed / E3 prospective observation`
产品：`Crypto Macro Trader Pilot`
Owner 范围：单 owner、单机、只读研究
上级事实源：[项目宪章](../engineering/PROJECT_CHARTER.md)、[最终实施章程](PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)、已接受 ADR、`contracts/schemas/`

## 0. 这份文档解决什么问题

本文件是当前交付周期的单一执行入口。它把产品定义、用户视图、DSH/Hub/LangGraph
边界、插件模型、持久化资产、阶段计划、SDD/BDD/TDD/ADR 约束和最终验收门放在同一
张可执行地图中，避免在实现过程中重新发明架构或把临时修复变成产品方向。

本文件不替代 canonical schema、ADR、模块 README 和历史验收记录。若发生冲突，必须
停止代码并修订事实源；不能通过局部补丁让两套规则同时存在。

## 1. 产品定义和成功标准

### 1.1 产品是什么

Decision Hub 是一个由官方 DSH Web 驱动的、持续运行的、证据约束的研究智能体产品。
它不是“用户问一句、模型答一段”的页面，也不是把 DSH 前端复制到 Hub 的项目。它的
最小智能行为是：在一个有边界的 durable Run 中读取目标，发现事实缺口，选择授权能力，
读取结构化结果，重新评估缺口，继续补证、降级或解释性停止。

```text
事件/文本/日历/新闻发现
  -> Hub admission：event_id + run_id
  -> 官方 DSH Web Session
  -> DSH Harness：计划 -> Tool/Skill/Subagent/MCP -> 结果 -> 下一轮
  -> Capability Gateway：权限/schema/PIT/鲜度/authority/hash/错误
  -> Sufficiency + 代码 Gate
  -> 人可读报告 + Forecast，或 research_only/解释性停止
  -> committed Artifact -> 本机 outbox 通知
  -> Outcome/Evaluation -> FailurePattern/Experience 个人资产
```

“一次 LLM 调用后直接结束”是问答；“固定节点串行执行”是 workflow；本产品的 Agent
行为来自 DSH 官方的有状态工具、子 Agent、Session 和继续循环，产品可靠性来自 Hub
的确定性事实边界和可恢复账本。

### 1.2 首期交付形态

- 唯一用户入口：官方 DSH Web，预装 Decision Hub Host/Client Plugin。
- 首个领域：`Crypto Macro Trader`，通过独立 Domain Pack 描述事实要求、来源梯度、
  根因链、Gate 和评测。
- 部署：单机、单 owner、SQLite/Alembic、只读研究；后台进程可常驻，默认不自动下单。
- 输入：文本、官方文档、新闻、日历和市场事实；未来 ASR 只需输出同一 `TextEnvelope`，
  本阶段不接 ASR 推理。
- 输出：事实引用、主/反方根因链、30m/24h/72h、Trigger、Invalidation、Gate、
  停止原因、复查时间和通知状态。
- 资产：Run、DSH Session/Trajectory/JSONL 引用、Evidence、PIT Snapshot、Artifact、
  Forecast、Outcome、Evaluation、FailurePattern、Experience。

### 1.3 成功标准

成功不是“报告更长”或“某次预测碰巧正确”，而是以下三件事同时成立：

1. 系统在没有 owner 逐轮提示的情况下能发现任务并在同一 DSH Session 内主动补证。
2. 每个事实、失败、停止和方向性输出都可由时间、来源、hash、Trace 和 Gate 复核。
3. 经过固定的前瞻数据和 Outcome 观察后，能够用证据选择 `promote`、`retain_baseline`
   或 `stop`，而不是无限堆功能。

## 2. 当前状态与交付边界

### 2.1 已验证事实

| 范围 | 状态 | 证据含义 |
|---|---|---|
| R0 文本核心 | `done` | TextEnvelope -> Snapshot -> Agent -> Gate -> Artifact/Forecast/Outcome 的离线链路已存在 |
| R1 实时事件 | `done (offline)` | 来源、行情、调度、outbox 的 fixture/replay 已通过；真实来源稳定性未证明 |
| R2 Workbench/Evolution | `done (offline)` | 运行检查、评测、候选/回滚和资产骨架已存在；不等于 candidate promotion |
| DSH Native Core | `engineering verified` | 官方 Web/Host/Client、Session/JSONL 关联、callback、恢复和版本锁定已验证 |
| R2-R Agentic Research | `candidate / retain_baseline` | DSH 内层循环和 Hub 外层生命周期已接通；Fixed 仍为 active |
| G1 失败边界 | `done (offline)` | PIT、失败 provenance、并行部分成功保留和终态投影已覆盖 |
| G2-A/B 事实包 | `done (offline/replay)` | 六类事实 requirement、manifest、fixture 和回放已覆盖 |
| G2-C live Search | `failed safely` | capability timeout 被记录；没有 Evidence/active pointer 变化 |
| E2-L 真实主线收口 | `passed / research_only` | 官方 Web 新实例已完成真实补证、可信账本、代码 Gate、双前端一致性、后台自动复查和完整质量门 |

### 2.2 当前不能宣称的事实

- `pilot_ready=true` 只表示 E2-L 的单 owner、单机、只读研究入口通过；
  `pilot_usable=research_only`，不等于自动交易或价值 Promotion。
- Fixed baseline 仍是 active；DSH 仍是 candidate/shadow，不得切换 active pointer。
- replay 是诊断和 CI 证据，不代表真实网络、搜索、行情、邮件长期稳定。
- 测试通过不代表预测准确率、盈利、低延迟或可替代人工决策。
- Search、Official、Market 任何一个真实能力缺失时，系统必须显示真实缺失/失败，不能改写
  成成功的 `no_trade`。

### 2.3 本轮明确不做

- 自动交易、自动 Promotion、自动修改 Gate、自动扣费或自动安装陌生插件。
- ASR/OCR/直播采集推理、PPT、A 股、美股、第二领域、多用户和公共插件市场。
- clone/fork DSH Web；复制 DSH Chat、Trajectory、JSONL、compaction 或插件安装器。
- 在 LangGraph 中重写第二套 ReAct/tool loop、Supervisor、Provider 协议、重试或账本。
- Redis、Kafka、Temporal、DBOS、Postgres、Kubernetes 或微服务拆分，除非新的真实需求
  先通过 ADR 和 Stage Gate。

## 3. 最终用户视图：一个入口，两个视图层

### 3.1 官方 DSH Web 是唯一主入口

启动器只输出一个官方 DSH Web URL，并默认打开 `Crypto Macro Trader` 工作区。用户不需要
知道 Hub API、MCP、worker、SQLite 或端口，也不需要手动为每个缺口输入提示。

```text
官方 DSH Web
  ├─ 原生 Chat / Session / History / Plan
  ├─ 原生 Tool / Skill / Subagent / MCP
  ├─ 原生 Trajectory / JSONL / compaction / live 状态
  ├─ Decision Hub 研究任务入口
  ├─ 当前 Run、证据覆盖、失败来源、Gate、报告和复查时间
  └─ 按需打开 Decision Desk 管理后台
```

普通 Chat 仍可用于探索，但标记为 `exploration`，不会自动成为正式 Evidence 或
Forecast。正式研究由 typed intake 创建唯一 `event_id + run_id`，再关联最多一个确定性
`dsh_session_id`。

### 3.2 DSH 业务工作区必须展示的内容

| 区域 | 用户能看到什么 | 数据所有者 |
|---|---|---|
| 工作区栏 | 领域、Live/Replay、Fixed/Candidate、runtime/provider/model/schema 版本 | Query/View DTO |
| 当前运行 | event、run、DSH session、状态、开始/更新时间、取消、重试/复查 | Hub Run + DSH link |
| 研究进度 | round、hard/soft coverage、开放 gap、成功/失败 capability、stop reason | Research Result/Trace |
| 证据链 | 来源、authority、published/observed/received 时间、PIT、hash、冲突 | Hub Evidence |
| 决策报告 | 主/反根因链、30m/24h/72h、Trigger、Invalidation、Gate、复查 | Artifact/Forecast |
| 运维 | worker、capability、通知、备份健康；打开 Desk | Operations View |

默认不显示 LangGraph state、Provider 原始 payload、密钥、SQL、MCP 原始协议或整段
DSH JSONL。原始轨迹仍由 DSH 原生 Trajectory/JSONL 保存，审计时按引用查看。

Client Plugin 必须通过官方 `conversation.view` 注册独立“研究报告”页签；完整报告不放在
composer dock，不覆盖 `chat` 或 `trajectory`。固定上游 `0.1.2-alpha.2` 在未持久选择
视图时硬编码 fallback 到 `chat`，且没有公开第三方 default-view API。因此当前产品在
Session 首次打开时仍进入官方 Chat，用户可切换到“研究报告”；不得用 fork、复用
`id=chat`、DOM/CSS hack 或删除 JSONL 强行隐藏原始轨迹。未来只有上游提供合法
per-workspace default-view seam 后，才可单独升级为受管研究 Session 默认打开报告页。

### 3.3 Decision Desk 是管理后台

Decision Desk 不是第二个聊天产品，提供以下管理和审计能力：

```text
Operations / Readiness / Worker heartbeat
Run Inspector / Timeline / Error Provenance
Evidence lineage / PIT / Source 与 Capability 健康
Artifact / Forecast / Outcome / Evaluation
FailurePattern / Experience / Dataset
Promotion / Rollback / Backup / Notification Outbox
```

前端只能消费 Query/View DTO 和 Zod schema，不直读数据库、Graph state 或 Provider raw
payload。success、partial failure、insufficient/stale、cancelled 四种终态必须有可读
页面；桌面和窄屏不能横向溢出，不能用一堆 JSON 代替信息层级。

## 4. 固定架构、所有权和数据流

```text
+---------------------------- DSH Web -----------------------------+
| Chat | Session | Trajectory | Tool | Skill | MCP | Subagent | JSONL |
| official Decision Hub Host + Client Plugin                         |
+-------------------------------+--------------------------------+
                                | official plugin seam
                                v
+----------------------+   +----+-----------------------------+
| Source adapters       |   | Decision Hub Host Plugin      |
| text/calendar/news    |   | intake/status/cancel/callback |
| future ASR -> text    |   | run <-> session correlation   |
+----------+-----------+   +----+-----------------------------+
           | TextEnvelope       |
           v                    v
  +--------+----------------------------------------------+
  | Hub API -> durable Run -> outer LangGraph lifecycle    |
  | Evidence Gateway -> PIT -> Sufficiency -> Gate -> Ledger|
  | Artifact/Forecast -> Outbox -> Outcome/Evaluation       |
  +--------+---------------------+-------------------------+
           | MCP capability       | Query/View
           v                      v
   Research MCP Gateway       Decision Desk Admin
           |
   Search / Official / Market adapters
```

| 组件 | 拥有 | 不拥有 |
|---|---|---|
| DSH Web/Harness | Agent Loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT、Gate、Forecast/Outcome、交易权限 |
| 官方 DSH Plugin | Host/Client seam、Run/Session 关联、状态/报告卡、命令桥 | 第二套 Chat/Loop、账本、Gate、自动安装器 |
| Hub Kernel | Event、Run、Snapshot、Evidence、Sufficiency、Gate、Artifact、Forecast、Outcome、Evaluation、Outbox | DSH 私有状态、模型推理、插件内部实现 |
| LangGraph | 外层生命周期、checkpoint、lease、Evidence Round、恢复和通知边界 | DSH tool loop、Supervisor、金融判断 |
| Capability Gateway | manifest、schema、权限、allowlist、timeout、retry、预算、PIT、provenance | 发布结论、修改 Gate、扩大权限 |
| Domain Pack | 事实要求、来源梯度、角色 profile、doctrine、Gate 参数、评测 | 通用账本、Harness 内部实现 |
| Decision Desk | Query、运维、审计、资产和 owner 命令 | 第二套聊天、直写数据库、改写历史 |

## 5. 两层循环：智能性和可靠性分工

### 5.1 DSH 内层 Agent Loop（唯一推理循环）

DSH 负责真正的自主研究行为，并复用官方 Harness：

```text
读取目标 + Domain Pack + 当前 hard gaps
  -> DSH Manager/Supervisor 选择 manifest 中授权的 Tool/Skill/Subagent/MCP
  -> Gateway 返回结构化结果
  -> DSH 更新计划和 gaps
  -> 关键 gap 仍存在且 deadline/预算允许？继续同一 Session
  -> 否则输出 candidate、research_only 或解释性停止
```

首期所有循环必须有界：`max_rounds`、`max_tool_calls`、`max_subagents`、总 deadline、
单能力 timeout、模型 step timeout、结构化修复次数和成本预算。一个 capability 失败不能
取消其它已成功结果；没有能力时必须记录稳定错误，不得伪装为成功结论。

本项目只提供 Role Profile、MCP binding、CapabilityManifest、结果 schema、权限和
确定性 Gate；不自写 ReAct、工具选择器、消息重试或平行 Supervisor。

### 5.2 Hub 外层 Product Loop

LangGraph 只负责产品生命周期和恢复：

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected
  -> outcome_due -> evaluated
```

它负责 Run 去重、lease、heartbeat、checkpoint、round 边界、重启恢复、取消、通知
outbox 和幂等提交；不替代 DSH 的模型/工具循环。

### 5.3 三份状态严格分离

| 状态 | 所有者/存储 | 用途 |
|---|---|---|
| DSH Session JSONL | DSH session root | 对话、工具、subagent、compaction、完整轨迹 |
| LangGraph checkpoint | Hub orchestration store | 外层生命周期位置、恢复信息、round 边界 |
| Hub Ledger | Kernel SQLite/Alembic | Event、Run、PIT、Evidence、Artifact、Forecast、Outcome、Evaluation、Outbox |

三者通过 `event_id/run_id/dsh_session_id/trace_ref/snapshot_id/artifact_id` 关联，只增不改
历史。DSH、模型或插件升级不得重写过去的 Forecast、Outcome 或 Evaluation。

## 6. 插件、领域和个人资产

### 6.1 插件不是 UI 装饰

官方 DSH Plugin 是能力安装运行单元，可以包含 Tool、Skill、MCP、Subagent、Hook、UI
和 Host/Client bridge；它不是只优化界面的卡片。正式影响 Evidence/Gate 的能力必须先
通过 `CapabilityManifest` 和 Gateway，完成 schema、权限、PIT、来源、timeout、retry、
预算、fixture、healthcheck 和审计。

```text
DSH Native Plugin
  = 官方 seam 上的运行能力：Tool/Skill/MCP/Subagent/Hook/UI

Product Extension / Domain Pack
  = 业务资产：目标、事实要求、来源、方法、Gate、结果契约、评测、历史
```

一个 Domain Pack 可以附带薄 DSH bundle，把 Role Profile、能力绑定、命令和视图接入
官方 DSH；Hub 仍拥有正式历史和 Gate。卸载插件、升级 DSH 或切换模型不能删除 Hub
历史。

### 6.2 新领域的隔离规则

新增 PPT、A 股、美股必须按以下顺序：

1. 新建独立 Extension/Domain Pack、canonical schema 和 ADR。
2. 只依赖公开 Platform Port（Event、Run、Evidence、Artifact、Asset、Evaluation、Trace ref）。
3. 为领域维护自己的事实来源、Gate、结果契约和评测；PPT 使用 `SlidePlan/RenderCheck`，
   不复用金融 Forecast 字段。
4. 用第二个真实调用方证明确实存在共享语义后，才抽取 Platform Core；不提前泛化。
5. ASR 先只实现 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope`，下游研究
   核心不感知音频来源。

### 6.3 每次 Run 必须沉淀的资产

- `DomainDoctrine`：根因链、事实要求、来源优先级、鲜度和 Gate。
- `EvidencePack`：来源 manifest、PIT fixture、hash、失败样本和回退关系。
- `RoleProfile`：Manager、反方、Data Quality、Specialist 的任务和权限契约。
- `RuntimeProfile`：DSH/Fixed、provider、model、api mode、预算、超时、schema 版本。
- `ResearchTrace`：Plan、Tool、Evidence、Sufficiency、Stop Reason 的规范化轨迹。
- `EvaluationDataset/Experiment`：Fixed、DSH、未来候选 runtime 的同条件比较。
- `FailurePattern/Experience`：根因、修复、适用条件、是否推广。
- `ProductArtifact`：报告、Forecast/Outcome、owner feedback、Promotion/Rollback 审计。

DSH JSONL 是运行证据，不是产品资产唯一真源；更换 DSH、Pi 或模型时，只替换 runtime
adapter，以上资产仍可读。

## 7. 代码地图和依赖规则

```text
apps/
  hub_api/                 REST command/query/callback；不执行长推理
  hub_worker/              realtime/research/evolution durable worker
  research_mcp/            唯一正式 capability gateway
  decision-desk/           Operations/Ledger/Evaluation 管理后台

packages/
  kernel/                  领域无关 Run/Evidence/PIT/Gate/Ledger/Outcome/Port
  orchestration/langgraph/ Hub lifecycle、checkpoint、recovery、Evidence Round
  runtime_adapters/dsh_runtime/ 官方 DSH SDK/Web Host adapter
  provider_adapters/       Search/Official/Market/Notification 实现
  workbench_adapters/      DSH/MCP/capability binding
  query_views/             人可读 DTO，不暴露 SQL/raw state
  evals/                   replay/holdout/shadow/Outcome/Promotion 证据

contracts/
  schemas/                 YAML canonical source，唯一跨模块事实源
  generated-*              codegen 镜像，禁止手改

packs/crypto_macro/
  doctrine/ evidence/ profiles/ tools/ gates/ evaluations/ fixtures/

extensions/dsh/decision-hub/
  src/host/                官方 Host seam：readiness/intake/status/cancel/callback
  src/client/              官方 Client seam：状态卡、报告、Desk 链接
```

依赖只能向内：`apps -> packages -> contracts`。Kernel 不导入 DSH、LangGraph、Provider
或前端；Domain Pack 通过 Port 接入；研究 graph 不直接 HTTP；前端只依赖 Query/View 与
Zod；跨边界只传 canonical Pydantic/Zod 模型，禁止裸 `dict/Any`。

DSH 上游由 `infra/dsh/upstream.lock.json` 锁定。升级流程：更新 lock/hash -> 安装官方
源码和插件依赖 -> 跑 NATIVE 三场景、版本 fail-closed 和完整质量门。禁止复制或修改
上游源码，禁止把 replay transport 当生产网络能力。

## 8. 已完成阶段：E2-L 真实主线收口

### 8.1 阶段目标

从官方 DSH Web 建立一次真实研究 Run，并在 DSH 业务卡和 Decision Desk 中看到同一 Run
的可信终态。成功、部分成功、证据不足、模型 synthesis 失败、取消和超时都必须真实
显示；任何没有通过认证或 Sufficiency 的结果都不能发布方向性 Forecast。

### 8.2 任务卡与实现边界

#### E2L-A：live effective cutoff

- 把 `deadline_at`（Run 最晚结束时间）、`query cutoff`（本次服务端接收时刻）和
  `decision_cutoff_at`（最后可信 Evidence 时刻）分开。
- live Gateway 从 durable Run/Session Link 读取 deadline，使用服务端当前时间生成
  effective cutoff，且不晚于 deadline。
- replay 保持 fixture cutoff，不读取当前时间。
- Evidence freshness 与 PIT 校验使用同一 effective cutoff；模型不能延长时间窗口。

#### E2L-B：Evidence attestation 安全降级

- 继续严格 exact-match Evidence ID；未知、改写、冲突 ID 仍 fail-closed。
- 若已有可信 Evidence，而 synthesis 只因 `dsh_evidence_unattested` 或
  `structured_output_invalid` 失败，返回 `degraded` 的 evidence-only 结果。
- 丢弃整个不可信 causal case/horizons，不产生方向性输出；保留可信 Evidence、Tool
  Invocation、Tool Result 和 coverage，并记录 `synthesis_failure_code`。
- 不在 DSH Web 里额外发 repair turn，不占用下一轮 generation；后续合法 generation
  可继续同一 Session。

#### E2L-C：失败 Run durable projection

- 没有最终 Result 时，Query/View 从 DSH Session Link、normalized Trace 和 Hub Evidence
  重建可信失败投影。
- runtime 不显示 `pending`，round/tool/evidence 使用 durable 统计；coverage 未完成时
  显示最后已知 gap，而不是“没有 gap”。
- failure 显示 `origin/cause_code/retryable/trace_ref`；不从 trace 摘要重建可发布因果链。

#### E2L-D：前端可读性

- failure 区显示“synthesis 未通过 Evidence 血缘认证”和已保留 Evidence 数量。
- 官方 HTML/XML 清理导航噪声并截断；market 数据使用少量字段摘要；原始 ID 只在诊断
  细节短形式出现。
- `run_id=null` 的正常空态不显示“研究生成中”；终态报告暂不可获取时显示终止和失败
  provenance，不无限 loading。
- Client 轮询生命周期以 Hub 业务终态为准，不能使用短于 Run `total_deadline_seconds` 的
  固定次数提前停止。网络暂时不可用时保留最后可信状态并继续低频恢复；组件卸载时必须
  abort/清理 timer。
- 一个官方 DSH Session 最多关联一条正式研究主线。只要按 `session_id` 能查到 `run_id`，
  composer dock 就不得再次显示“建立研究任务”；失败 Run 只允许显式、幂等的 child retry，
  完成/仅研究/拒绝/取消后必须新建 DSH Session 才能建立新的主线。
- 官方 DSH 的空白“新会话”在首条原生消息前可能没有 `session_id`，因此 intake 幂等身份
  不能退化成“空 session + 文本”。Client 必须为一次用户提交生成稳定 request key，并用
  标准 `Idempotency-Key` header 传给 Host；同一次网络重试复用该 key，不同新会话/新提交
  使用新 key。Host 只接受有界格式并把其 hash 传给 Hub，禁止用修改文本绕过重复识别。
- Event 与 Run 去重必须分层：相同规范化文本可复用同一 `event_id`，但不同用户 request
  key 必须创建独立 `run_id`，以保存各自的 PIT、Evidence、Runtime、预算、Gate 和反馈；
  只有同一 Idempotency-Key 的重试才返回原 Run。定时复查 child 不能吞掉新的 manual Run。
- 空白 intake 页只负责建立任务；当 status 返回该 Run 的确定性 `dsh_session_id` 后，Client
  必须使用官方 Client Session Controller 的 `sessions.refresh()` + `sessions.open(id)` 进入
  受管 Session，使用户在同一入口看到原生 Chat/Trajectory 与“研究报告”。固定上游
  `0.1.2-alpha.2` 不消费 `?session_id=`，因此禁止用假 URL 路由、侧栏 DOM 点击、修改上游
  router 或私写 selection store 实现关联。

#### E2L-E：Search citation binding 与来源独立性

- `web_search_call.action.sources` 只代表 Provider 浏览/发现过的 URL，不代表该 URL 支持
  最终摘要，不能直接转换为正式 Evidence。
- 每条 Search Evidence 必须来自 Responses `output[].content[].annotations[]` 中的结构化
  `url_citation`，且 citation URL 必须同时存在于本次 `action.sources`。Evidence excerpt
  只能使用 annotation offset 对应的原文精确 span，禁止把整段模型摘要复制到所有来源。
- 同一次调用按规范化 URL 去重；没有任何可绑定 citation 时返回稳定的
  `search_no_attributed_sources` 并 fail-closed，不保存“有 URL、无对应内容”的 Evidence。
- Sufficiency 的独立来源仍按 canonical `source_id` 计算；Search 摘要不能替代
  Official/Market 原生事实，也不能因多个无关 URL 虚增 authority/source coverage。
- 已存在的错误历史 Evidence、Run、Snapshot、Artifact 和 JSONL 保持不可变。修复只影响
  新调用；旧 Run 在验收记录中明确标记为“来源绑定缺陷样本”，不得重算为通过。

#### E2L-F：真实官方 Web 复验

使用全新隔离 Compose project、独立数据卷和新官方 DSH Web 端口：

```text
启动器
  -> 打开唯一官方 DSH Web URL
  -> 新建/选择当前 Session
  -> 页面 typed intake 提交真实文本
  -> DSH 主动调用授权 MCP capability
  -> 页面和 Decision Desk 轮询到同一 Run 终态
  -> 核对 Evidence、coverage、Trace、failure/report、桌面/窄屏截图
```

旧端口、旧 bundle、直接 curl 创建的另一个 Session、replay 页面或日志片段不能替代这
次验收证据。

#### E2L-G：受信 Tool Session 身份与 Prompt 耐久顺序

- `decision-research` Agent 只看到 DSH Native `decision_hub_research` Tool；原始
  `mcp__decision_research__research_capability_execute` 不得作为模型可见旁路。
- 模型参数中没有 `research_session_id`。Native Tool 只从官方
  `ToolDefinition.execute(args, exec)` 的 `exec.agent.id` 注入完整 Session 身份；无 Agent、
  未知/终态 Session、generation 不匹配均 fail-closed，禁止前缀、编辑距离或 request_id
  猜测。
- Native Tool 经独立 loopback bridge key 调用同一个 Research Gateway；两端继续校验
  canonical `ResearchCapabilityQuery/Result`，HTTP body、deadline 和错误 provenance 有界，
  不新增第二 DTO、第二 Gateway 或第二 Agent Loop。
- Host 提交顺序固定为“创建空官方 Session -> Hub accepted/link 耐久确认 -> queue Prompt”。
  accepted 失败时 Prompt 不入队；相同提交重试、Host 重启和后续 generation 均不得重复
  入队。
- Plugin build hash 必须覆盖 Native Tool 和 `decision-research` preset；实际 DSH JSONL、
  Hub Session Link 和 build identity 共同证明本轮没有继续走旧 MCP 旁路。

### 8.3 退出门

```text
[x] E2L-A live cutoff 和 durable deadline 测试通过
[x] E2L-B synthesis failure fallback/trace/下一 generation 测试通过
[x] E2L-C 无 Result 的失败 Run Query/View 投影测试通过
[x] E2L-D DSH 状态持续到业务终态，受管 Session 不出现重复 intake
[x] E2L-E Search citation/URL 绑定、无绑定 fail-closed、旧无关来源不入 Evidence
[x] E2L-F 新隔离实例官方 Web -> MCP -> Hub -> Desk 真实复验通过
[x] E2L-G 受信 Session 注入、accepted-before-prompt 和无 MCP 旁路通过 live 复验
[x] 真实失败不被改写为 no_trade，可信 Evidence 不丢失
[x] 桌面和窄屏截图、console 无未处理错误
[x] 最终 live revision 的完整质量门通过
[x] 最终状态文档、CHANGELOG 和验收记录同步
```

最终 live Run 为 `run_04dc1a46fd1e4c3b988750e18b0e9581`，对应完整 DSH Session
`dsh_c73364bc0fd7221f8102fc5181e8c4b17ab3736afc282379e311c67534d4c778`。它在同一
受管 Session 内完成 2 个 Evidence Round、12/12 个 durable Tool Call，保留 15 条
Evidence，hard coverage 为 `66.7%`，按代码 Gate 收敛为 `research_only / tool_budget`，
并提交 Artifact `art_815a237cee2c4aa6bee513c79c64741d`。两次 Search timeout 以
`research_capability_timeout / transport / retryable=true / deadline_ms=20000` 保留；
FRED 日频事实以 `evidence_stale` 保留，均未被伪装成成功或方向性交易结论。

运行达到复查时间后，后台 scheduler/worker 无需 owner 输入，自动创建 child Run
`run_7df306876d114e09b8e83507d06f3ef0`，再次完成 2 轮、12/12 Tool Call、7 条 Evidence
和 `research_only` Artifact。这证明后台 durable product loop 已真实运行；长期稳定性和
实际价值仍只能由 E3 前瞻观察证明。

官方 DSH Web 与 Decision Desk 对同一 Run 的 round、tool、Evidence、coverage、failure、
Gate 和 stop reason 一致。DSH 的 `375/768/1024/1440` 视口以及 Desk 的桌面/375 视口均无
横向溢出，本次 Run 期间 console 无 error。完整 Run、版本、插件 hash、截图 SHA-256、
失败 provenance 和测试命令见
[E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。

Owner 已在 2026-09-01 接受本控制书的最小只读 Official/Market 准入建议；这只授权使用
已通过独立 canary、license/audit 为 approved、域名和成本均受 manifest 约束的
`official.macro`、`market.cross_asset`、`market.crypto_derivatives` 重跑 E2L-F，不授权
任意社区插件、扩大域名/费用、切 active pointer 或放宽 Evidence/PIT/Gate。

本次 E2-L 只把产品状态提升为 `pilot_ready=true / research_only`；不会自动 Promotion，
也不会自动进入 ASR、PPT、第二领域或多用户。

## 9. 后续阶段和停止线

### 9.1 E3：价值观察（当前唯一下一阶段）

观察窗口为至少 14 天或 20 个高影响事件，使用同一 PIT 规则比较 Fixed 与 DSH candidate：

- 记录事件准时性、事实覆盖、端到端延迟、Provider 成本、人工复核时间和 owner usefulness。
- 到期才由 MarketDataPort 写 Outcome、Brier、方向正确性和净收益；未到期不能填标签。
- 每个事件保留原始输入、Evidence lineage、Gate、Forecast、Outcome 和评测版本。
- 观察结束只允许三种结论：`promote`、`retain_baseline`、`stop`。若不能证明价值，保留
  Fixed baseline，冻结 candidate，不继续堆功能。

### 9.2 P4：平台扩展（仅在真实需求出现后）

只有第二个真实领域调用 Platform Port 时，才评估共享 Core；只有 ASR 或远程部署成为
已验证需求时，才另立 Stage Charter 和 ADR。每个新领域必须有独立 schema、Domain Pack、
BDD/TDD、评测数据和回滚策略，不能把金融字段塞进通用 Core。

### 9.3 强制停止条件

出现以下任一情况，停止写代码，先回到 ADR/Owner 决策：

- 需要放宽 Evidence exact-match、PIT、Gate 或 authority floor。
- 需要新增第二账本、第二套 DTO、第二个 Agent Loop 或第二个正式生产入口。
- 需要扩大网络域名、费用、插件权限，或切换 active pointer。
- 同一根因两次最小修复仍不能通过测试。
- 只能依赖真实网络、真实密钥或事后信息让 CI 通过。
- 目标从“交付可审计研究试点”变成“继续打磨看起来更完整的页面”。

## 10. SDD/BDD/TDD/ADR 和上下文治理

### 10.1 固定顺序

```text
SDD 规格/契约/事件/Gate
  -> ADR（跨模块或不可逆时）
  -> BDD 用户场景
  -> TDD Red
  -> 最小实现 Green
  -> Refactor
  -> replay/integration/live canary
  -> README/状态/CHANGELOG/验收证据
```

### 10.2 每张任务卡必须写清

- 一个价值目标和三个以内的验收断言。
- 允许修改路径、禁止修改路径、复用的框架 API 和不做事项。
- canonical schema、事件、Gate、错误码和版本血缘。
- BDD 正常、重复、拒绝、失败、取消、恢复场景。
- TDD 测试文件、固定时间/输入/Provider 响应和完成命令。
- 完成后要同步的模块 README、`IMPLEMENTATION_STATUS`、`CURRENT_STATE`、`HANDOFF`、
  `CHANGELOG` 和必要 ADR。

### 10.3 上下文压缩规则

长任务只加载 `INDEX.md`、`PROJECT_CHARTER.md`、本文件、当前 Stage Charter、受影响
模块 README、canonical schema 和直接测试。出现目标矛盾、连续两次修补失败或需要读取
三个以上不相关模块时，先按“事实/决策/当前目标/不做/证据/未决”压缩到
`docs/context/`，再继续。临时推理放 `tmp/`，不能成为事实源。

## 11. 完整质量门和最终验收 checklist

### 11.1 文档与契约

```text
[x] git diff --check
[x] python -m tools.docs.check_module_docs
[x] python -m tools.contract_codegen check
[x] canonical schema 变更已 codegen，generated 镜像未手改
[x] 受影响模块 README 和阶段状态已同步
```

### 11.2 后端与编排

```text
[x] pytest -m "not live"（390 passed）
[x] ruff check .
[x] pyright（0 errors / 0 warnings）
[x] migration 0001 -> 0027、backup/restore/integrity
[x] LangGraph checkpoint、lease、cancel、resume、idempotent commit
[x] DSH Web Session、generation continuation、callback recovery
[x] capability timeout/429/5xx/schema/PIT/authority/conflict 错误分类
```

### 11.3 前端和运行态

```text
[x] pnpm --dir apps/decision-desk test（10 passed）
[x] pnpm --dir apps/decision-desk build
[x] pnpm --dir extensions/dsh/decision-hub test -- --run（53 passed）
[x] pnpm --dir extensions/dsh/decision-hub build
[x] docker compose config --quiet
[x] 新隔离实例 official DSH Web、Research MCP、Hub API/Desk 全部可达
[x] success/partial/insufficient/cancel/attestation failure 页面可读
[x] 前端持续轮询到 Hub 业务终态；长 Run 不冻结旧快照
[x] managed Session 不显示重复“建立研究任务”；失败只提供幂等 retry
[x] 桌面和 375/768/1024/1440 视口无溢出、覆盖或 console 未处理错误
```

### 11.4 产品最终可用门

```text
[x] 官方 DSH Web 是唯一主入口，用户不需要手动串联 API/MCP/worker
[x] DSH 在同一 Session 内完成“发现缺口 -> 调能力 -> 再评估”
[x] 每条 web.search Evidence 的 URL 与结构化 citation 对应；未绑定 source 不入账
[x] durable Ledger 保存 Run/Evidence/Trace/PIT/Gate/Artifact/Forecast
[x] 失败 Run 显示真实错误来源，可信 Evidence 保留，不能伪装 no_trade
[x] 通过 Gate 的报告才进入 outbox；通知失败不重跑、不改写历史
[x] 同一 Run 在 DSH 业务卡和 Decision Desk 的状态一致
[x] E2-L 真实证据记录 run id、版本、能力、错误、截图和 hash
[x] Owner 接受本控制书，下一阶段只进入 E3 前瞻观察
```

旧 `/v1/pilot/readiness` 仍是 R1-L Fixed 管线的 legacy readiness；它在未配置 Fixed
provider/market 时返回 `not_ready` 不影响上述 DSH-first E2-L 验收，也不能被改写为成功。
本轮不为文档收口新增第二套 readiness schema。

## 12. Codex 执行协议

每次实现只能绑定一个 Task ID，并按以下顺序执行：

1. 读取本文件、当前状态和受影响模块 README，确认没有事实冲突。
2. 先写/更新 SDD、BDD 和 TDD；需要不可逆决定时先写 ADR 并停在 Gate。
3. 使用现有 LangChain/LangGraph/Pydantic/SQLAlchemy/OpenAI/DSH API，禁止平行造轮子。
4. 只修改任务允许路径；发现需要扩大边界时停止并记录，不擅自扩展。
5. 跑最小专项测试，再跑完整质量门；真实 live 只在明确授权且有界时执行。
6. 把事实、失败、截图、命令、状态和文档同步写入仓库；每张任务卡一个可回滚 commit。
7. 未通过退出门时，状态只能写 `in progress`、`blocked` 或 `failed safely`，不能写
   `done`、`pilot_ready` 或“效果已证明”。

本控制书之后唯一允许的目标是：

> **E3-PROSPECTIVE-OBSERVATION：不新增产品功能，在至少 14 天或 20 个高影响事件中持续
> 运行同一 DSH-first research-only 主线，按 PIT 规则记录覆盖、延迟、失败率、成本、人工
> 复核时间、usefulness、30m/24h/72h Outcome、Brier、方向准确率和净收益；窗口结束只作
> `promote / retain_baseline / stop` 裁决。观察前不切 active pointer，不扩展 ASR、PPT、
> 第二领域、多用户、自动交易或公共插件市场。**

## 13. E2L-D/E/F/G 已完成任务卡

### 13.1 价值目标

用户在唯一官方 DSH Web 中建立一次研究后，无需继续人工提示或刷新页面，能看到同一个
durable Run 主动补证并到达可信终态；报告中的每条 Search 事实都能证明“这段内容由这个
URL 的结构化 citation 支持”，否则系统解释性停止。

### 13.2 允许与禁止路径

允许修改：

```text
packages/provider_adapters/search/openai_responses.py
tests/capabilities/test_search_capability.py
packages/kernel/decision_hub_kernel/application/analyze.py
tests/e2e/test_api_flow.py
extensions/dsh/decision-hub/src/client/index.js
extensions/dsh/decision-hub/src/research-tool.ts
extensions/dsh/decision-hub/src/host/bridge.ts
extensions/dsh/decision-hub/tests/client.spec.ts
extensions/dsh/decision-hub/tests/research-tool.spec.ts
extensions/dsh/decision-hub/tests/host.spec.ts
extensions/dsh/decision-hub/lib/client.js（只由 build 生成）
packages/workbench_adapters/research_mcp.py
packages/runtime_adapters/dsh_runtime/profile.py
packages/runtime_adapters/dsh_runtime/web_runtime.py
infra/dsh/presets/decision-research/agent.cordis.yml
相关模块 README、Stage/状态/验收/CHANGELOG 文档
```

禁止修改：DSH 上游源码、Kernel Gate、authority floor、历史数据库/JSONL、Provider secret、
active pointer、自动交易/通知发布权限、PPT/ASR/第二领域。

### 13.3 BDD/TDD

```text
Scenario A: action.sources 含 A/B/C，但 output annotation 只引用 A
  Given Provider 返回结构化 url_citation(A)
  When Search adapter 规范化结果
  Then 只生成 A 的 Evidence，excerpt 为 annotation offset 对应的原文精确 span，B/C 不入账

Scenario B: Provider 只有 action.sources，没有结构化 citation
  When Search adapter 规范化结果
  Then 返回 search_no_attributed_sources，Evidence/coverage/active pointer 均不变化

Scenario C: 研究耗时超过旧的 240 秒前端轮询窗口
  Given Hub business 仍为 researching
  Then Client 继续低频轮询，直到业务终态或组件卸载

Scenario D: 当前 DSH Session 已关联 Run
  Given status(session_id) 返回 run_id
  Then 不显示“建立研究任务”；失败可幂等 retry，其余终态要求新建 Session

Scenario E: 官方空白新会话尚无 session_id
  Given 用户用相同研究文本在两个新页面分别提交
  When Client 发送两个不同 Idempotency-Key
  Then Event 可以复用但产生两个独立 Run；单次响应丢失重试复用原 key，不重复 Run

Scenario F: Host 已为新 Run 建立确定性 DSH Session
  Given status(run_id) 返回 dsh_session_id
  When 当前页面仍是无 session_id 的 intake 页
  Then Client 先刷新官方 Session list，再通过 sessions.open 进入该 Session
  And Chat/Trajectory/研究报告切换到同一 Session scope，停止在空白提交页轮询

Scenario G: 模型调用研究 Tool
  Given model-visible schema 不含 research_session_id
  When decision_hub_research 在受管 Session 中执行
  Then canonical Query 的 Session ID 只等于 exec.agent.id
  And Agent 无法看到或调用原始 MCP capability tool

Scenario H: Hub accepted callback 暂时失败
  Given Host 已创建空的确定性 Session
  When durable accepted/link 未确认
  Then Prompt 不入队；相同提交重试确认成功后只入队一次

Scenario I: Native Tool HTTP bridge 收到无密钥、超大或非法 canonical body
  Then Gateway 不执行、Evidence 和工具预算不变化，并返回稳定 provenance
```

测试先 Red 后 Green；不引入新的前端状态库、Search client、schema 或数据库迁移。

### 13.4 最终验收 checklist

```text
[x] Search citation binding 专项测试通过
[x] DSH Client 长 Run/managed Session 专项测试通过并重新 build
[x] DSH Native Tool 身份注入、HTTP bridge 和 Host accepted 顺序专项测试通过
[x] decision-research 实际工具目录不含原始 MCP capability tool
[x] 空 session_id 的不同提交不再错误复用旧 Run，单次重试仍幂等
[x] typed intake 自动进入对应受管 DSH Session，Chat/Trajectory/研究报告属于同一 Run
[x] Python offline、Ruff、Pyright、contract、module docs 全通过
[x] Decision Desk 和 DSH Plugin test/build 全通过
[x] Compose/recovery/rollback/diff check 全通过
[x] 新 live Run 从官方页面建立并自主完成 2 个 Evidence Round 后解释性有界停止
[x] Hub API、DSH 报告页、Trajectory 的 Run/round/tool/evidence/Gate 终态一致
[x] 旧错误 Run 保留且明确标为来源绑定缺陷样本
[x] 桌面和 375px 窄屏截图、console、截图 hash 已归档
[x] E2-L 状态设置为 pilot_ready=true / research_only，并保留全部真实限制
```
