# Decision Hub 最终产品交付实施书

版本：`PRODUCT-DELIVERY-2026-09-01.v1`
状态：`executed / E2-L passed / historical delivery contract`
执行目标：`PRODUCT-CLOSEOUT-01`
Owner 范围：单 owner、单机、`Crypto Macro Trader` 个人研究试点。
本文件性质：保留已执行的顶层交付合同；当前状态和停止裁决以
[产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准。

> 本文件把已接受的架构、ADR、canonical schema 和阶段 Charter 组合成一份可执行的交付合同。它不替代这些事实源；若与 accepted ADR 或 `contracts/schemas/` 冲突，必须先停止实现并修订决策，不允许靠补丁维持两套事实。

## 1. 先锁定产品结论

### 1.1 产品是什么

Decision Hub 不是一次 LLM 调用，也不是必须由 owner 每次提示的问答页面。它是一个持续运行、证据驱动、可恢复、可审计的研究智能体产品：

```text
事件/文本/日历/新闻发现
  -> Hub admission（event_id + run_id）
  -> 官方 DSH Web Session
  -> DSH Agent 自主识别事实缺口并调用授权能力
  -> Evidence Gateway（权限、来源、PIT、鲜度、冲突、hash）
  -> Sufficiency / Gate（代码唯一裁决）
  -> 人可读报告 + Forecast 或 research_only
  -> 通知 outbox
  -> Outcome / Evaluation / FailurePattern / Experience 资产
```

只有“读取目标、发现缺口、选择下一步能力、读取结果、继续或解释性停止”才算本产品的 Agent 行为。普通聊天仍保留，但它是探索入口；正式研究必须创建 durable Run，并通过 DSH Agent Loop 完成。

### 1.2 首期交付对象

首期只交付 `Crypto Macro Trader Pilot`：

- 输入：文本或未来由适配器转换成的 `TextEnvelope`；本阶段不接入 ASR 推理。
- 运行：单机、单 owner、只读研究、默认不下单。
- 入口：官方 DSH Web，用户不需要打开 Hub API、MCP 或 worker 端口。
- 输出：事实引用、主因果链、反方链、30m/24h/72h、触发/失效条件、Gate、失败和复查时间。
- 资产：Run、DSH Session/Trajectory/JSONL 引用、Evidence、PIT Snapshot、Artifact、Forecast、Outcome、Evaluation、FailurePattern、Experience。

以下内容不进入本次交付：自动交易、自动 Promotion、自动安装陌生插件、ASR、PPT、A 股、美股、多用户、公共插件市场、Postgres/Redis/Temporal/Kubernetes、第二套 Agent Loop。

### 1.3 什么时候算可用

本阶段不以“页面能打开”作为可用。必须通过三层门：

| 门 | 必须证明 | 通过后的含义 |
|---|---|---|
| 工程门 | 进程、会话、Run、失败、恢复、备份、契约、测试都通过 | 系统可运行、可审计、不会用错误结论掩盖失败 |
| 产品主线门 | DSH Web -> MCP -> Hub -> Ledger -> Desk 三场景贯通，主动补证真正发生 | 单 owner 可以把它作为研究辅助试点使用 |
| 价值门 | 14 天或 20 个高影响事件的同 PIT 对照、Outcome、成本/延迟和 owner 反馈 | 决定 `promote / retain_baseline / stop`，不由代码测试替代 |

工程门和产品主线门通过后，产品状态为 `pilot_ready / research_only`，不是盈利承诺。价值门未通过时停止堆功能，保留 Fixed baseline 或停止 DSH candidate。

## 2. 用户视图与入口

### 2.1 唯一主入口

启动器只打印一个官方 DSH Web URL，并预装 Decision Hub Host/Client Plugin。用户默认进入 `Crypto Macro Trader` 工作区：

```text
官方 DSH Web（唯一用户入口）
  ├─ 原生 Chat / Session / History / Plan / Tool / Skill / Subagent
  ├─ 原生 Trajectory / JSONL / compaction / live 状态
  ├─ Decision Hub 研究任务入口
  ├─ 当前 Run、证据覆盖、失败来源、Gate、报告和复查
  └─ 按需打开 Decision Desk（运维/审计后台）
```

Hub API、Research MCP、realtime/research/evolution worker 和 SQLite 是后台实现，不是用户需要理解的第二个产品。

### 2.2 DSH Web 必须显示的业务信息

官方 DSH 的 Chat、Session、Trajectory、JSONL 和工具轨迹继续由上游负责。Decision Hub Client Plugin 只投影以下业务信息：

| 区域 | 页面内容 |
|---|---|
| 工作区 | `Crypto Macro Trader`、Live/Replay、Fixed/Candidate、runtime/provider/version |
| 当前运行 | event、run、DSH session、状态、时间、取消、重试/复查 |
| 研究进度 | round、hard/soft coverage、未解决 gap、成功/失败 capability、stop reason |
| 结果 | 事实引用、主/反因果链、30m/24h/72h、触发、失效、Gate、复查时间 |
| 运维 | readiness、worker 健康、通知状态、打开 Decision Desk |

默认不把 LangGraph state、Provider 原始 payload、密钥、完整 SQL 或无意义 raw JSON 倾倒给用户。原始 DSH JSONL 仍保留，并通过官方轨迹按需查看。

### 2.3 Decision Desk

Decision Desk 是后台管理和审计视图，不是第二个聊天入口：

```text
Operations / Readiness / Worker 心跳
Run Inspector / Timeline / Error Provenance
Evidence lineage / PIT / Source 与 Capability 健康
Artifact / Forecast / Outcome / Evaluation
FailurePattern / Experience / Dataset
Promotion / Rollback / Backup / Notification Outbox
```

前端只能消费 Query/View DTO 和 Zod schema，不直读 SQL、LangGraph state 或 Provider 原始 JSON。桌面和移动视口必须无横向溢出；失败、部分成功和停止原因必须在人可读层可见。

## 3. 固定架构与所有权

```text
+---------------------------- DSH Web -----------------------------+
| Chat | Session | Trajectory | Tool | Skill | MCP | Subagent | JSONL |
| official Decision Hub Host/Client Plugin                         |
+-------------------------------+--------------------------------+
                                | official DSH plugin seam
                                v
+----------------------+   +----+-----------------------------+
| Source adapters       |   | Decision Hub Host Plugin      |
| text/calendar/news   |   | submit/status/cancel/callback |
| future ASR -> text   |   | session correlation           |
+----------+-----------+   +----+-----------------------------+
           | TextEnvelope       |
           v                    v
  +--------+----------------------------------------------+
  | Decision Hub: API -> durable Run -> LangGraph lifecycle |
  | Evidence Gateway -> PIT -> Sufficiency -> Gate -> Ledger |
  | Artifact/Forecast -> Outbox -> Outcome/Evaluation       |
  +--------+---------------------+-------------------------+
           | MCP capability       | Query/View
           v                      v
   Research MCP Gateway       Decision Desk Admin
           |
   Search / Official / Market adapters
```

| 组件 | 拥有 | 明确不拥有 |
|---|---|---|
| DSH Web/Harness | Agent Loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT 裁决、Gate、Forecast/Outcome、自动交易 |
| 官方 DSH Plugin | Host/Client seam、Run/Session 关联、状态/报告卡、业务命令桥 | 第二套聊天、第二套 Agent Loop、账本、Gate |
| Decision Hub Kernel | Event、Run、Evidence、PIT、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、权限 | DSH 私有状态、模型 loop、插件安装器 |
| LangGraph | Hub 外层生命周期、checkpoint、lease、recovery、Evidence Round 边界 | ReAct/tool loop、Supervisor、网页搜索策略、金融角色 |
| Domain Pack | 事实要求、来源优先级、能力绑定、领域 doctrine、Gate、评测 | 通用账本、Harness 内部实现 |
| Capability Gateway | schema、域名、权限、timeout、retry、预算、PIT/provenance | 发布结论、修改 Gate、扩大权限 |
| Decision Desk | 运维、审计、查询、资产、Promotion/Rollback 命令 | 第二套聊天、直写数据库、改写历史 |

硬规则：Agent 只能提交候选；代码 Gate 是唯一发布裁决。任何 Agent、插件、模型或 worker 都不能写交易权限对象、修改 active pointer、自动交易、自动扣费、自动安装插件或覆盖历史账本。

## 4. 两层 loop、插件和持久化

### 4.1 DSH 内层 Agent Loop

这是产品智能性的唯一来源，必须复用官方 DSH Harness：

```text
读取目标 + Domain Pack + 当前 hard gaps
  -> DSH Manager/Supervisor 选择 manifest 中授权的 Tool/Skill/Subagent/MCP
  -> 获得结构化结果
  -> Gateway 校验来源、权限、可信时间、hash、PIT、schema
  -> DSH 更新 gap 和下一步计划
  -> 关键 gap 仍存在且能力/预算/deadline 允许？继续同一 Session
  -> 否则输出候选、research_only 或解释性停止
```

首期必须有界：`max_rounds`、`max_tool_calls`、`max_subagents`、总 deadline、单能力 timeout、模型 step timeout、结构化修复次数和成本预算。能力失败不能取消其它已成功结果；没有能力时必须记录 `research_capability_unavailable` 或 `critical_data_unavailable`，不能把 Provider 失败伪装成 `no_trade`。

### 4.2 Hub 外层 Product Loop

LangGraph 只管理产品生命周期和恢复，不造第二套 Agent Loop：

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected
  -> outcome_due -> evaluated
```

它负责事件去重、Run lease、heartbeat、checkpoint 引用、重启恢复、取消、通知 outbox 和幂等提交。关闭任意进程后，业务 Run + lease + checkpoint 能恢复；不能依赖内存里的 DSH 进程作为事实源。

### 4.3 三份状态严格分离

| 状态 | 所有者/存储 | 用途 |
|---|---|---|
| DSH Session JSONL | DSH session root | 对话、工具、subagent、compaction、完整轨迹 |
| LangGraph checkpoint | Hub orchestration store | 外层生命周期位置、恢复信息、round 边界 |
| Hub Ledger | Kernel SQLite/Alembic | Event、Run、PIT、Evidence、Artifact、Forecast、Outcome、Evaluation、Outbox |

三者通过 `event_id/run_id/dsh_session_id/trace_ref/snapshot_id/artifact_id` 关联，只增不改历史。DSH、模型或插件升级不得改写过去的 Forecast、Outcome 或 Evaluation。

### 4.4 插件模型

插件不是只优化页面，而是能力安装运行单元；但业务事实和产品资产仍属于 Hub：

```text
DSH Native Plugin
  = Tool / Skill / MCP / Subagent / Hook / UI / Provider 运行单元

Product Extension / Domain Pack
  = 目标、事实要求、方法、Gate、结果契约、评测和历史资产
```

一个 Domain Pack 可以附带薄 DSH bundle，把 Role Profile、MCP binding、命令和视图接入官方 DSH。影响正式 Evidence/Gate 的 DSH 插件必须先通过 `CapabilityManifest` 和 Gateway；只影响 DSH UI 的工具不自动进入正式结论。插件卸载不能删除 Hub 历史。

未来新增 PPT、A 股、美股：先新建独立 Product Extension/Domain Pack 和 ADR，只复用公开 Platform Port（Event、Run、Evidence、Artifact、Asset、Evaluation、Trace reference）。只有第二个真实领域证明语义相同后，才抽取共享 Core；不能把金融字段扩散到 Platform Core。

## 5. 代码结构与依赖方向

```text
apps/
  hub_api/                 REST command/query/callback；不执行长 Agent 任务
  hub_worker/              realtime/research/evolution durable worker
  research_mcp/            唯一正式 capability gateway
  decision-desk/           Operations/Ledger/Evaluation 管理后台

packages/
  kernel/                  领域无关 Run/Evidence/PIT/Gate/Ledger/Outcome/Port
  orchestration/langgraph/ Hub 生命周期、checkpoint、recovery、Evidence Round
  runtime_adapters/dsh_runtime/ 官方 DSH SDK/Web Host adapter
  provider_adapters/       Search/Official/Market/Notification 具体实现
  workbench_adapters/      DSH/MCP/Codex capability binding
  query_views/             人可读 DTO，不暴露 SQL/raw state
  evals/                   replay/holdout/shadow/Outcome/Promotion 证据

contracts/
  schemas/                 YAML canonical source，唯一跨模块事实源
  generated-*              codegen 镜像，禁止手改

packs/crypto_macro/
  doctrine/ evidence/ profiles/ tools/ gates/ evaluations/ fixtures/

extensions/dsh/decision-hub/
  src/host/                官方 Host seam：readiness/submit/status/cancel/callback
  src/client/              官方 Client seam：状态卡、报告、Desk 链接
```

依赖只能向内：`apps -> packages -> contracts`。Kernel 不导入 DSH、LangGraph、Provider 或前端；Domain Pack 通过公开 Port 接入；前端通过 Query/View 和 Zod；研究 graph 不能直接 HTTP；能力只能通过 Manifest/Gateway。

## 6. C1-C7 最终实施清单

每个 C 阶段先完成契约/测试/实现，再完成页面和运行证据。`[ ]` 只能在证据存在后改为 `[x]`。

### C1：唯一入口与双向关联（工程门已验证）

- [x] 一个启动器启动 Hub API、realtime/research/evolution worker、Research MCP 和官方 DSH Web。
- [x] 启动器只输出一个 DSH URL；默认工作区为 `Crypto Macro Trader`。
- [x] typed intake 创建唯一 durable Run，并通过稳定幂等键和唯一 `dsh_session_id` 关联；重复语义由 contract/test 覆盖。
- [x] DSH -> Hub 状态/报告链接和 Hub Run -> DSH Session 关联存在。
- [x] 宿主机 DSH 能经 loopback 端口访问 Research MCP，readiness 可读。

证据：[2026-09-01 执行记录](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md)。这里的 `[x]` 仅表示 C1 工程门，不表示 C3-C7 或产品价值通过。

### C2：正式 DSH Runtime（工程门已验证，成功研究路径待 C3/C4）

- [x] 正式模式拒绝隐式 replay；隔离实例只允许 `web.search`，未混入 `replay.research`。
- [x] submit/status/cancel/reconcile/callback 的幂等与恢复边界已有 contract/replay/运行证据。
- [x] DSH Session、Trajectory、JSONL 引用和 Hub Run 关联存在；Provider 失败仍保留终态和 provenance。
- [x] Python SDK 仅保留 candidate/fallback，不形成第二条 active 生产链。

本次真实 Provider 的 `web.search` 在 capability deadline 内未返回，因此 C2 只证明正式 Runtime 和失败安全；成功的 Evidence/Artifact/Forecast 路径必须由 C3/C4 的可用能力验收证明。

### C3：主动补证闭环

- [ ] Run 启动加载 `crypto_macro` 六类事实要求及 capability ladder。
- [ ] hard gap 存在且有授权能力时，DSH 在同一 Session 内继续调用并进入下一轮。
- [ ] 每轮保存计划、ToolInvocation、EvidenceCandidate、Sufficiency、失败 provenance 和 stop reason。
- [ ] 并行能力一项失败不取消其它成功结果；取消与 Provider 失败严格区分。
- [ ] hard gap 未闭合只能 `research_only/no_trade`，不能生成方向性 Artifact/Forecast。

### C4：事实能力准入

- [ ] 六类事实均有 `CapabilityManifest`、输入/输出 schema、权限、域名、authority floor、freshness、PIT、hash、timeout、retry、cost、error codes、fixture 和 healthcheck。
- [ ] Hub 的 `web.search.tavily`、Official、Market 默认 deny-by-default；DSH 官方原生 `web_search`
  只作为 discovery primary，且其结果仍必须经 Hub attestation；每个外部 live canary 单独授权、
  只读、限时、临时目录。
- [ ] 低权威、过期、未来时间、未知 authority、schema 错误和 provider failure 都 fail-closed。
- [ ] canary 记录结果数量、authority、freshness、PIT、latency、cost、错误 provenance；不切 active pointer。

### C5：报告、通知、可观测

- [ ] DSH 业务卡展示 Run、round、coverage、成功/失败 capability、Gate、报告和 Desk 链接。
- [ ] Desk 展示 Timeline、Evidence lineage、PIT、per-capability error code/origin/cause/retryable、stop reason。
- [ ] committed Artifact 才进入本机 outbox；通知失败可重试，不重新分析、不改写账本。
- [ ] 前端对 success、partial failure、insufficient/stale 三种状态都有可读页面和测试。

### C6：个人资产与评测

- [ ] 每个正式 Forecast 固化 horizon、probability、trigger、invalidation、Snapshot 和版本血缘。
- [ ] 到期由 MarketDataPort 计算 Outcome、Brier、方向和净收益；未到期不填猜测标签。
- [ ] Fixed 与 DSH candidate 在同 PIT 数据集进行 replay/holdout/shadow 对照。
- [ ] 失败样本、owner 反馈、来源 manifest、Role/Profile、Runtime/Provider Profile、FailurePattern/Experience 可导出。
- [ ] 新 Pack/Profile/Capability 只能经过回放、holdout、shadow、人工 review 后进入 candidate，不在线修改历史。

### C7：单机运维与停止

- [ ] 一键启动/停止、readiness、heartbeat、日志目录、SQLite WAL、checkpoint、Session root、备份/恢复和 integrity check 有 runbook。
- [ ] 重启、租约过期、callback 丢失、重复提交、部分 worker 失败和磁盘不足均有测试或明确安全状态。
- [ ] 每次产品验收保存命令、端口、runtime mode、版本、hash、截图、console 摘要和 API 摘要。
- [ ] C1-C7 通过后停止工程功能扩张，进入 14 天/20 事件价值观察。

## 7. SDD/BDD/TDD/ADR 全局执行约束

### 7.1 固定顺序

```text
SDD（目标/价值/输入/输出/契约/Gate/非目标）
  -> ADR（跨模块、不可逆、部署、数据和运行时决定）
  -> BDD（成功、拒绝、失败、取消、恢复、权限）
  -> TDD Red -> Green -> Refactor
  -> 集成/E2E/回放/隔离 live canary
  -> README、状态、CHANGELOG、验收记录
```

### 7.2 强制不变量

1. 跨模块 schema 只改 `contracts/schemas/`，再运行 codegen；禁止手改生成镜像和双写字段。
2. Core/Domain 契约不依赖 DSH、Pi、Codex、LangGraph 或具体 Provider；这些只能是 adapter。
3. DSH 只有一套 Agent Loop；Hub/LangGraph 不复制 ReAct、Supervisor、Session、Trajectory、插件安装器或 Provider client。
4. Agent 无 Gate、Ledger、交易、扣费、Promotion、插件安装和扩大网络权限。
5. 跨边界数据必须 Pydantic/Zod 运行时校验；禁止裸 `dict/Any` 穿越公开边界。
6. `observed_at/published_at/received_at/cutoff_at` 由可信代码拥有；模型不能填写可信时间。
7. 每个失败保留 `error_code/origin/cause/retryable` 和已完成证据；取消不能伪装成 Provider 失败。
8. 历史 Run、Evidence、Artifact、Forecast、Outcome、Evaluation 和 migration 只增不改。
9. Replay、fake、静态截图和旧进程不能证明真实事实、预测准确率、收益或生产稳定性。
10. 未运行的检查必须写 `pending`；没有截图/console/API/hash 资产不能写“浏览器验收通过”。

### 7.3 上下文压缩与文档纪律

每张任务卡开始前读取 `INDEX.md`、`docs/context/CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、当前 Stage Charter 和直接相关模块 README，并运行：

```bash
./.venv/bin/python tools/context/build_task_context.py \
  --objective "<单一任务目标>" --paths <允许修改的目录>
```

上下文超过三个不相关模块、出现决策冲突或同一缺陷两次补丁仍失败时，停止继续写代码，先把“事实/决策/目标/不做/证据/未决”提炼到 `docs/context/` 或 ADR。长期文档、阶段文档、模块 README、状态和 CHANGELOG 必须在同一任务同步；临时日志、截图和探索只进入 `tmp/` 或 ignored 验收目录。

### 7.4 每张任务卡模板

```text
目标：一句话
价值：一个用户可观察结果
允许路径：目录和文件
禁止路径：目录、权限和行为
契约：schema/event/query view
BDD：成功/失败/恢复三场景
TDD：测试文件、失败注入、Red/Green/Refactor
停止条件：需 ADR/owner 的变化
验收命令：实际命令和结果
```

## 8. 最终验收 Checklist

### 8.1 工程门

```text
[ ] git diff --check
[ ] ./.venv/bin/python -m tools.contract_codegen check
[ ] ./.venv/bin/python tools/docs/check_module_docs.py
[ ] ./.venv/bin/pytest -m "not live" -q
[ ] ./.venv/bin/ruff check packages apps migrations tests tools
[ ] ./.venv/bin/pyright
[ ] pnpm --dir extensions/dsh/decision-hub test
[ ] pnpm --dir extensions/dsh/decision-hub build
[ ] pnpm --dir apps/decision-desk test
[ ] pnpm --dir apps/decision-desk build
[ ] docker compose config --quiet
[ ] fresh migration + integrity + backup/restore
```

### 8.2 产品主线门

```text
[ ] 新实例而非旧端口/旧进程启动全部组件并 ready
[ ] DSH Web 发现 Trader workspace，提交后创建 durable Run/Session
[ ] 同一 Run 只有一个 DSH Session，callback/reconcile 幂等
[ ] success、partial capability failure、low-authority/stale/insufficient 三场景贯通
[ ] hard gap + 可用能力触发至少第二轮 Tool/Evidence 动作
[ ] per-capability failure 展示 error code、origin、cause、retryability
[ ] Provider failure 不生成 Evidence/Artifact/Forecast，不伪装成 no_trade
[ ] sufficient 只在 authority/PIT/freshness/coverage 通过后发布 Artifact/Forecast
[ ] 重启、callback 丢失、worker lease 过期后可恢复且不重复提交
[ ] 桌面/移动截图、console、API、版本和 hash 可复核
```

### 8.3 价值门

```text
[ ] 至少 14 天或 20 个高影响事件
[ ] Fixed 与 DSH candidate 使用同一 PIT 数据集对照
[ ] 30m/24h/72h Outcome 到期并计算 Brier/方向/覆盖率
[ ] 记录首证据延迟、最终延迟、工具调用数、成本、通知延迟、失败率
[ ] owner 确认是否减少人工查证、是否理解停止原因、是否愿意继续使用
[ ] 形成 promote / retain_baseline / stop ADR 或决策包
```

## 9. 交付状态和后续路线

### 9.1 本轮目标

```text
PRODUCT-CLOSEOUT-01
  = 按 C1-C7 收口单机 DSH Native Trader Pilot
  = 补齐真实可观察主线与失败安全语义
  = 完成工程门和产品主线门后停止继续堆功能
```

当前基线中，R0/R1/R2/R2-L、R2-R-00..06E、DSH-NATIVE-CORE 的离线/工程成果保留；Fixed 仍为 active baseline，DSH 为 candidate/shadow。真实 Search/Official/Market、通知和价值观察没有被 replay 或一次 Provider canary 冒充完成。

### 9.2 当前执行投影（2026-09-01）

| 卡片 | 当前状态 | 下一退出证据 |
|---|---|---|
| C1 | `engineering verified` | 保持新实例启动、workspace、typed intake 和双向关联回归 |
| C2 | `engineering verified / provider failed safely` | 在 C3/C4 成功场景确认结果回传，不重开第二 Runtime |
| C3 | `partial` | 至少一次合法 Evidence 入账后，同一 Session 按 gap 继续下一轮 |
| C4 | `failed safely / acceptance pending` | Search transport 可用，或逐项通过 Official/Market contract、replay、live canary 和审计 |
| C5 | `partial` | 成功报告、通知 outbox 和 DSH/Desk 同 Run 一致验收 |
| C6 | `pending` | 新 Forecast 到期后追加 Outcome/Evaluation/Experience；历史数据不改写 |
| C7 | `pending final live revision` | 最终 revision 上完成 restart、lease、checkpoint、backup/restore 和磁盘检查 |

当前不是 `pilot_ready` 或 `pilot_usable`。Provider 失败已被正确保留为失败，不得把它改写成 `no_trade`、证据不足或产品完成。

### 9.3 交付后有界路线

```text
C1-C7 产品主线
  -> G3 单机 prospective 价值观察（promote / retain / stop）
  -> G4 ASR（只有文本链产生价值后）
  -> G5 第二领域（出现真实调用方后）
  -> G6 规模化/多用户/远程 HA（真实容量或权限需求出现后）
```

任何新需求必须先声明属于 C1-C7、G3、G4、G5 或 G6，并给出用户价值和退出门。无法归类的需求只进入产品评审，不创建代码目录。达到产品主线门后，默认动作是观察和评估，不是无限新增角色、插件或基础设施。

## 10. 本轮实现记录入口

本文件只规定目标和验收，实际命令、端口、版本、截图、日志、失败原因和未通过项写入：

- `docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_*.md`
- `docs/context/CURRENT_STATE.md`
- `docs/context/HANDOFF.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/ROADMAP.md`
- `CHANGELOG.md`

本轮结束时，只有同时满足工程门和产品主线门才把 `PRODUCT-CLOSEOUT-01` 标记为工程交付；价值门继续独立观察。没有证据的 checklist 保持 `[ ]`，不得为“看起来完整”而提前勾选。
