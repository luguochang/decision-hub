# Decision Hub 产品执行总方案

版本：`PRODUCT-EXECUTION-2026-08-31.v1`
状态：`executed / historical execution reference`
确认范围：单 owner、单机、`crypto_macro` 交易研究试点；DSH Web 唯一用户入口。
本文件不是新的架构发明，而是把已经接受的架构、ADR、契约和阶段卡收敛成一份可执行的产品交付清单。

## 0. 本文件的作用

本项目之前出现过“代码能运行，但用户看不到真正的智能体；信息不足时直接停止；DSH、Hub 和
自建流程边界不清；阶段不断迁移”的问题。本文件用四个规则结束这种漂移：

1. 只保留一个用户入口：官方 DSH Web。
2. 只保留一套 Agent Loop：DSH 官方 Harness；Hub 不再实现第二套。
3. 只保留一套产品事实账本：Decision Hub Kernel；DSH JSONL 和 LangGraph checkpoint 只作运行证据。
4. 只按本文件的阶段门前进；完成交付门后进入观察期，不因“还可以增加功能”无限开发。

本文件保留为产品执行总参考；C1-C7 与 E2-L 已执行完成。当前状态、完成 checklist 和 E3
停止线以 [产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准，本文件不再
承担执行入口角色。

权威优先级仍然是：accepted ADR/canonical schema > 产品架构基线 > 本文件 > Stage Charter > 模块 README >
状态投影和临时材料。若本文件与 accepted ADR 或 schema 冲突，必须停止实现并修订 ADR，不能用补丁让两套事实同时存在。

关联事实源：

- [DSH 与 Decision Hub 系统总装设计](DSH_HUB_SYSTEM_ASSEMBLY.md)
- [最终产品交付与主线闭环方案](FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)
- [DSH-first 产品实现总方案](DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)
- [PRODUCT-CLOSEOUT-01 Stage Charter](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)
- [项目宪章](../engineering/PROJECT_CHARTER.md)
- [全局开发治理](../engineering/DEVELOPMENT_GOVERNANCE.md)
- [SDD/BDD/TDD 自测规范](../engineering/TDD_SDD_SELF_TEST_STANDARD.md)
- [当前状态](../context/CURRENT_STATE.md)

## 1. 产品定义和价值边界

### 1.1 产品是什么

Decision Hub 是一个持续运行、证据驱动、可恢复、可审计的研究智能体产品。它不是一次 LLM 调用，
也不是要求 owner 每次手动提示的问答助手。其最小闭环是：

```text
事件/文本/日历/新闻发现
  -> durable Run
  -> DSH Agent 在授权范围内发现缺口并主动补证
  -> PIT、来源权威性、鲜度、冲突和充分度校验
  -> 确定性 Gate
  -> 报告、Forecast、通知
  -> Outcome、Evaluation、FailurePattern、Experience 资产
```

“智能体”在本产品中的可验证定义是：在 owner 预先授权的目标、能力、网络域、时间和成本预算内，
它能自己决定下一步取证动作，读取工具结果，发现仍未覆盖的 hard requirement，并在预算内继续或解释性停止。
无限自治、自动交易和未经授权的能力扩张不属于产品目标。

### 1.2 首个可交付产品

首个产品为 `Crypto Macro Trader Pilot`：单机运行、单 owner、只读研究、默认不下单；输入是文本或已转换的
`TextEnvelope`，输出是带证据和时间范围的研究报告。ASR、PPT、A 股、美股、多用户和公共插件市场均不是首个交付物。

产品交付必须同时满足两类条件：

| 类型 | 判定 | 当前状态 |
|---|---|---|
| 工程可运行 | 进程启动、会话可用、Run 可持久化、失败不伪造结论、可恢复 | 已有离线/本机证据，Compose 正式启动仍需本轮复核 |
| 值得依赖 | 真实来源覆盖、延迟/成本可接受、主动补证确实工作、owner 愿意使用 | 尚未完成，不能用 replay 或一次 canary 代替 |

达到工程门并不自动等于盈利、预测准确或可以自动交易。价值门必须由前瞻观察和 owner 反馈决定。

## 2. 最终用户视图和入口

### 2.1 唯一主入口

交付启动器只输出一个 DSH Web URL。用户不需要知道 Hub API、MCP、worker 或数据库端口：

```text
DSH Web（唯一主入口）
  -> 预装 Decision Hub 官方插件
  -> 默认工作区：Crypto Macro Trader
  -> 自动发现/运行列表、Agent 轨迹、证据缺口、报告和复查
  -> 需要运维或审计时，点击链接打开 Decision Desk
```

普通 DSH 对话仍然可用，但标记为 `exploration`；只有正式 admission 创建的 `event_id + run_id` 才写入 Hub
Evidence、Gate、Forecast 和 Outcome。关闭聊天窗口不应终止后台 Run。

### 2.2 DSH Web 页面

DSH 原生页面继续负责 Chat、Session、History、Plan、Tool、Skill、Subagent、MCP、Trajectory、JSONL 和实时 Agent
状态。Decision Hub 的官方 Client Plugin 只增加产品上下文卡片，不复制 DSH 页面：

```text
工作区标题：Crypto Macro Trader
运行标识：Live / Replay（醒目标记）
健康状态：Hub、Worker、DSH Host、Capability、通知
最近事件：事件时间、来源、优先级、Run 状态
研究进度：当前轮次、已覆盖 hard/soft requirement、未解决 gap、停止原因
报告摘要：事实、主因果链、反方链、30m/24h/72h、Gate、触发/失效条件、复查时间
操作：取消、重试、复查、反馈、打开 Decision Desk
```

页面默认展示 Query/View 的人可读 DTO，不展示整个 LangGraph state、Provider payload、secret 或无意义的原始 JSON。
原始 DSH JSONL 通过 DSH 原生轨迹和审计引用保留，只有在需要诊断时才打开。

### 2.3 Decision Desk

Decision Desk 是管理和审计后台，不是第二个聊天产品：

```text
Operations / Readiness / Worker 心跳
Run Inspector / Timeline / Error Provenance
Evidence lineage / PIT / Source 和 Capability 健康
Artifact / Forecast / Outcome / Evaluation
FailurePattern / Experience / Dataset
Promotion / Rollback / Backup / Notification Outbox
```

## 3. 系统总装和责任边界

```text
+---------------------------- DSH Web -----------------------------+
| Chat | Session | Trajectory | Tool | Skill | MCP | Subagent | JSONL |
| official Decision Hub Client Plugin: run/evidence/gate/report card |
+-------------------------------+--------------------------------+
                                | DSH native plugin / host seam
                                v
+----------------------+   +----+-----------------------------+
| Source adapters      |   | Decision Hub Host Plugin      |
| text/calendar/news   |   | session link/submit/status   |
| future ASR -> text   |   | cancel/reconcile/callback    |
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
   Search / Official / Market / Notification adapters
```

| 层 | 拥有的能力 | 明确不拥有 |
|---|---|---|
| DSH Web/Harness | Agent Loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL、上下文压缩、原生 UI | Hub 账本、PIT 裁决、Publish Gate、Forecast/Outcome、自动交易 |
| DSH Official Plugin | Host/Client route、Hub 状态卡、Session 关联、结果回调、可见性增强 | 第二套聊天、第二套工具循环、业务账本 |
| Decision Hub Kernel | Event、Evidence、PIT、Run、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、权限 | DSH 私有状态、Agent Loop、插件安装器 |
| LangGraph 外层 | durable 生命周期、checkpoint、lease、recovery、Evidence Round 边界 | 通用 ReAct/tool loop、网页搜索策略、DSH Session |
| Domain Pack | doctrine、事实要求、来源优先级、能力绑定、领域 Gate 和评测 | 通用账本、Harness 内部实现 |
| Capability Gateway | schema、权限、域名、timeout、retry、预算、PIT/provenance | 自行发布结论、修改 Gate、扩大权限 |
| Decision Desk | 运维、审计、报表、资产和 Promotion/Rollback 操作 | 第二套 Agent 产品、直读数据库、改写历史 |

硬规则：Agent 只能提交候选；代码 Gate 是唯一发布裁决者。任何 Agent、插件、DSH Session 或模型都不能写交易权限对象、
修改 active pointer、自动安装新插件、扣费或覆盖历史账本。

## 4. 两层循环和持久化

### 4.1 DSH 内层 Agent Loop

这是产品智能性的来源，必须复用 DSH 官方 Harness：

```text
读取目标 + Domain Pack + 当前 Evidence gap
  -> Manager/Supervisor 选择授权 Tool/Skill/Subagent/MCP
  -> 取得结构化结果
  -> Gateway 做来源、时间、权限、hash、PIT 校验
  -> 更新 gap 和计划
  -> 仍有关键 gap 且预算足够？继续同一 Session；否则输出候选/解释性停止
```

首版只允许有限边界：最大 evidence round、tool call、subagent 数、总 deadline、单工具 timeout、模型 step timeout、
结构化修复次数和估算成本。缺口存在且存在授权能力时，不能在第一轮直接把结果伪装成完整结论；没有合法能力时必须记录
`research_capability_unavailable` 或 `critical_data_unavailable`，安全降级为 `research_only/no_trade`。

### 4.2 Hub 外层 Product Loop

LangGraph 只编排产品生命周期和恢复，不再造一个 Supervisor：

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected
  -> outcome_due -> evaluated
```

外层负责事件去重、Run lease、heartbeat、checkpoint 引用、重启恢复、取消、通知 outbox 和幂等提交。进程停止后从业务
Run + lease + checkpoint 恢复，而不是依赖内存中的 DSH 进程。

### 4.3 三份状态分离

| 状态 | 存储 | 内容 | 不能替代 |
|---|---|---|---|
| DSH Session JSONL | DSH session root | 对话、工具调用、subagent、compaction、轨迹 | Hub 业务事实 |
| LangGraph checkpoint | checkpoint store | 外层生命周期执行位置、恢复信息 | Evidence、Gate、Outcome |
| Hub Ledger | SQLite/Alembic | Event、Run、Evidence、PIT、Artifact、Forecast、Outcome、Evaluation、资产 | 完整 DSH raw trace |

三者通过 `event_id/run_id/dsh_session_id/trace_ref/snapshot_id/artifact_id` 关联；只增不改历史。升级 DSH 或模型不能改写
过去的 Forecast、Outcome 或 Evaluation。

## 5. 插件和扩展模型

### 5.1 两层插件不是两套产品

```text
DSH Native Plugin
  = Harness 能力：Tool / Skill / MCP / Subagent / Hook / UI / Provider

Product Extension / Domain Pack
  = 产品资产：目标、事实要求、领域方法、Gate、结果契约、评测和历史
```

一个 Product Extension 可以附带一个薄 DSH bundle，把 Role Profile、MCP binding、命令和视图接入 DSH；卸载 bundle
不能删除 Hub 历史。影响正式 Evidence/Gate 的 DSH 插件必须经过 CapabilityManifest、权限、schema、PIT、预算和审计。
DSH-only 的临时工具或 UI 可由 DSH 管理，不得绕过 Hub 进入正式结论。

### 5.2 新领域的可插拔口子

当前只有 `packs/crypto_macro`。未来新增 PPT、A 股或美股时：

1. 先新建独立 Product Extension/Domain Pack 规格和 ADR。
2. 复用 Platform Core 的公开 Port：Event、Run、Evidence、Artifact、Asset、Evaluation、Trace reference。
3. 为领域自己定义输入、结果、Gate、来源和评测；不得把金融字段塞进 Core。
4. 用第二个真实调用方证明两个领域确实共享后，才提取共享接口。
5. 新能力优先作为 DSH official plugin/MCP capability，禁止复制 DSH 源码或 fork 私有实现。

ASR 以后只实现 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope`；PPT 只实现自己的 `SlidePlan/RenderCheck/Export`
资产。两者都不改变现有研究核心。

## 6. 目标代码结构和依赖方向

```text
apps/
  hub_api/                 REST、Query/View、owner command；不执行长 Agent 任务
  hub_worker/              realtime/research/evolution durable worker
  research_mcp/            受控 capability gateway
  decision-desk/           运维、审计和研究产品后台

packages/
  kernel/                  领域无关账本、PIT、Gate、权限和公开 Port
  orchestration/langgraph/ Hub 生命周期、checkpoint、recovery、Evidence Round
  runtime_adapters/dsh_runtime/ DSH SDK/Web Host adapter；不拥有业务账本
  provider_adapters/       Search、Official、Market、Notification 的具体实现
  workbench_adapters/      MCP/DSH/Codex capability binding
  query_views/             人可读 DTO；不暴露 SQL/raw state
  evals/                   replay、holdout、shadow、Outcome、Promotion 证据

contracts/
  schemas/                 YAML canonical source
  generated-*              codegen 镜像，禁止手改

packs/crypto_macro/
  doctrine/ evidence/ profiles/ tools/ gates/ evaluations/ fixtures/

extensions/dsh/decision-hub/
  src/host/                官方 Host seam：readiness、submit/status/cancel、callback
  src/client/              官方 Client seam：状态卡、报告和管理后台链接
```

依赖只能向内：`apps -> packages -> contracts`；Kernel 不导入 DSH、LangGraph、Provider 或前端；Domain Pack 通过公开 Port
接入；前端通过 Query/View 和 Zod，不直读 SQL、Graph state 或原始 Provider JSON。

## 7. SDD/BDD/TDD/ADR 全局执行规范

### 7.1 每个任务的固定顺序

```text
SDD：目标、价值、输入/输出、契约、事件、Gate、非目标
  -> ADR：存在不可逆/跨模块/部署/数据决策时记录
  -> BDD：正常、失败、取消、恢复、权限和用户可观察场景
  -> TDD Red：先写失败测试
  -> Green：调用框架已有能力的最小实现
  -> Refactor：只在测试保护下重构
  -> 集成/E2E/回放/隔离 canary
  -> README、状态、CHANGELOG、验收证据
```

### 7.2 每张任务卡必须包含

```text
目标：一句话
价值：一个用户可观察结果
允许路径：明确目录
禁止路径：明确目录/行为
契约：canonical schema/event/query view
BDD：最多三个核心场景
TDD：对应测试文件和失败注入
停止条件：遇到什么必须回到 ADR/owner
验收命令：实际运行命令和结果
```

任务开始先运行 `tools/context/build_task_context.py` 生成 `tmp/task-context.md`；完成后将事实提炼到长期文档，临时
文件不作为架构真源。上下文超过三个不相关模块、发现决策冲突或同一缺陷两次补丁仍失败时，先压缩事实/决策/目标/不做/证据/未决，停止继续堆代码。

### 7.3 强制边界

1. 跨模块 schema 只改 `contracts/schemas/`，然后运行 codegen；禁止双写生成镜像。
2. Core/Domain 契约不依赖 DSH、Pi、Codex、LangGraph 或 Provider；它们只能是 adapter。
3. 不复制 Agent Loop、Supervisor、Session、Trajectory、插件安装器、Provider client、账本或前端聊天。
4. Agent 无 Gate、Ledger、交易、扣费、Promotion、安装插件和扩大网络权限。
5. 运行时跨边界数据必须用 Pydantic/Zod 校验；禁止裸 `dict/Any` 穿越公开边界。
6. PIT 的 `observed_at/published_at/received_at/cutoff_at` 必须由可信代码拥有；不能让模型填写可信时间。
7. 失败保留具体 error code、origin、cause、retryability 和已完成证据；取消不能伪装成 Provider 失败。
8. 历史 Run、Evidence、Artifact、Forecast、Outcome、Evaluation 和 migration 只增不改。
9. replay 只用于诊断和回归，不能宣传为实时事实、准确率或收益。
10. 未运行的检查必须标记 `pending`；旧进程、旧端口和旧截图不能作为新版本验收证据。

## 8. 有界实施计划

本轮目标不是继续增加角色或基础设施，而是完成 `PRODUCT-CLOSEOUT-01` 的工程主线，并在可用边界处停止。

### C1：统一启动器、工作区和双向链接

- [ ] 一个启动器启动 API、realtime worker、research worker、evolution worker、research MCP 和官方 DSH Web。
- [ ] 只输出一个 DSH URL；DSH 页面能打开对应 Run 的 Hub/Desk 链接。
- [ ] 反向从 Hub Run 打开确定的 DSH Session；不产生第二个 Session。
- [ ] 宿主机 DSH 能访问容器 research MCP；端口映射和健康检查可验证。

### C2：正式 DSH Web Runtime

- [ ] 正式启动模式拒绝 replay 配置，显示 `live`/Provider/version/source commit/plugin hash。
- [ ] Web Host submit/status/cancel/reconcile/callback 幂等且可恢复。
- [ ] Web 重启、Hub 重启、callback 丢失后不重复提交 Artifact/Outbox。
- [ ] Python SDK 仅作 candidate/fallback，不创建第二条 active 生产链。

### C3：缺口驱动主动补证

- [ ] Run 启动后加载 Domain Pack 的 requirement/capability ladder。
- [ ] DSH Agent 在同一 Session 内根据 gap 继续调用授权能力，而非首轮发现不足就结束。
- [ ] 每轮保存计划、ToolInvocation、EvidenceCandidate、Sufficiency 和 stop reason。
- [ ] 没有合法能力、超时或预算耗尽时安全降级并保留 provenance。

### C4：真实能力准入和隔离 canary

- [ ] Search/Official/Market capability 均有 manifest、schema、权限、timeout、cost、PIT 和 audit。
- [ ] 默认 deny-by-default；只允许临时目录、只读、域名白名单和一次限时 canary。
- [ ] 记录来源数量、authority、freshness、PIT、latency、cost、错误分类和结果 hash。
- [ ] canary 不切 active pointer、不写生产交易对象、不替代回放测试。

### C5：报告、通知和可观测视图

- [ ] DSH Client Plugin 显示 Run、Evidence gap、Gate、Forecast、报告和 Desk 链接。
- [ ] Decision Desk 显示可读时间线、成功/失败 ToolInvocation、error provenance、PIT 和 stop reason。
- [ ] committed Artifact 才能进入 local outbox；通知失败不重新分析、不改写账本。
- [ ] 页面桌面/移动视图无横向溢出，不把无用 raw JSON 倾倒给用户。

### C6：Outcome、Evaluation 和个人资产

- [ ] 每个 Forecast 记录 horizon、probability、trigger、invalidation、snapshot 和版本。
- [ ] 到期由 MarketDataPort 计算 Outcome/Brier/net return；未到期保持 pending，不填猜测值。
- [ ] Fixed 与 DSH candidate 在同一 PIT 数据集上可 replay/holdout/shadow 比较。
- [ ] 失败样本、反馈、来源 manifest、Role/Profile、Runtime/Provider Profile 和版本血缘可导出。

### C7：单机交付和停止规则

- [ ] 单机启动、停止、重启、备份、恢复、integrity check 和磁盘告警有 runbook。
- [ ] SQLite WAL、lease、checkpoint、outbox 和 Session root 数据目录明确且互不混淆。
- [ ] 所有本地/集成/浏览器/隔离 canary 证据存有命令、端口、runtime mode、版本和 hash。
- [ ] C1-C7 通过后进入至少 14 天或 20 个高影响事件观察期，执行 `promote/retain/stop`，停止无价值功能扩张。

## 9. 最终验收门

### 9.1 工程门（必须全部通过）

```text
[ ] git diff --check
[ ] ./.venv/bin/python -m tools.contract_codegen check
[ ] ./.venv/bin/python tools/docs/check_module_docs.py
[ ] ./.venv/bin/pytest -m "not live" -q
[ ] ruff check packages apps migrations tests tools
[ ] pyright（若环境可用）
[ ] pnpm --dir extensions/dsh/decision-hub test
[ ] pnpm --dir extensions/dsh/decision-hub build
[ ] pnpm --dir apps/decision-desk test
[ ] pnpm --dir apps/decision-desk build
[ ] docker compose config --quiet
[ ] 迁移 fresh database 到当前 head 并执行 integrity check
```

### 9.2 产品主线门（必须有运行证据）

```text
[ ] 新实例（非旧端口/旧进程）启动 Hub + worker + research MCP + 官方 DSH Web
[ ] readiness/heartbeat 全部可读，启动日志显示 live 或明确 replay，不混淆
[ ] DSH Web 创建/恢复 Session，原生 Trajectory/JSONL 可见
[ ] 同一 Run 只关联一个 dsh_session_id，callback/reconcile 幂等
[ ] 成功、部分失败、低权威/事实不足三个场景贯通 DSH -> MCP -> Hub -> Ledger -> Desk
[ ] 缺口存在且能力可用时出现多轮 Tool/证据动作；不能首轮直接结束
[ ] 能力失败显示 capability、origin、cause、error code、retryability 和已完成结果
[ ] insufficient/stale/authority failure 不发布方向性 Forecast，Gate 为 research_only/no_trade
[ ] Artifact、Forecast、Outbox 只在确定性 Gate 后提交；失败不产生伪资产
[ ] DSH/Hub/Web 重启和 callback 丢失后可恢复且不重复提交
[ ] 桌面和移动截图、console 摘要、API 摘要、版本和 hash 可复核
```

### 9.3 价值门（工程通过后单独观察）

```text
[ ] 至少 14 天或 20 个高影响事件
[ ] 每个事件保留 Fixed、DSH candidate、失败样本和 owner feedback
[ ] 首个可用证据延迟、最终延迟、工具调用、成本、通知延迟均有数据
[ ] 30m/24h/72h Outcome 到期后计算 Brier/方向/覆盖率，不填未到期标签
[ ] owner 判断是否减少人工查证时间、是否理解停止原因、是否愿意继续使用
[ ] 形成 promote / retain_baseline / stop 的 ADR/决策包
```

价值门不通过时，产品可以保留为 `research_only` 个人工具或停止 candidate；不能通过继续增加页面、角色、插件或数据库来掩盖没有价值。

## 10. 当前状态、未完成项和本轮执行目标

截至 2026-09-01（2026-08-31 的 registry 阻塞作为历史证据保留）：

- R0/R1/R2/R2-L、R2-R-00..06E 和 DSH-NATIVE-CORE NATIVE-00..05 的离线/工程证据已存在；Fixed 仍为 active baseline，DSH 为 candidate/shadow。
- canonical contract、PIT、authority floor、capability ladder、取消语义、Host terminal callback 并发和前端 error provenance 的根因修复已进入工作树并有测试。
- `pytest -m "not live"`、Ruff、Pyright、codegen、module docs、DSH plugin test/build、Decision Desk test/build 和 Compose config 既有结果通过。
- 2026-08-31 的 Compose 镜像 build 曾受 Docker registry 超时阻塞；该历史失败不再代表最新实例状态，也不能把旧进程或旧镜像当作新产品验收。
- 2026-09-01 最新收口已验证官方 Workspace/Session、同 Session generation 2、三类人可读报告、恢复/备份和完整离线质量门，即 E1/E2-R 通过；该证据不代表实时研究价值。
- 真实 Search、当前 revision 的移动/console 资产和预测价值仍需 E2-L/E3，不用 replay 冒充。

本轮已完成的执行目标：

```text
PRODUCT-CLOSEOUT-EXEC-02
在不改变 DSH/Hub/Domain 边界的前提下，完成 Workspace/Session、同 Session 多轮、
三类报告和恢复质量门，E1/E2-R 已通过；当前只允许闭合 E2-L，随后进入 E3。
```

允许修改：`compose.yaml`、`infra/dsh/`、本执行记录、受影响模块 README/状态投影和对应测试。
禁止修改：DSH 上游缓存、历史 migration/账本、active pointer、领域 Gate、自动交易权限、ASR/PPT/第二领域和公共插件市场。

## 11. 变更与交付记录

每个 C 子任务完成时同步：

```text
代码/测试 -> 受影响模块 README
契约       -> contracts schema + codegen manifest + contract tests
用户行为   -> CHANGELOG + BDD/E2E
阶段状态   -> docs/IMPLEMENTATION_STATUS.md + docs/ROADMAP.md
当前事实   -> docs/context/CURRENT_STATE.md + HANDOFF.md
架构/不可逆 -> docs/decisions/ADR-NNNN.md
运行证据   -> docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_*.md
```

临时日志、截图和外部调查放在 ignored 的验收目录或 `tmp/`；长期文档只保留结论、路径、命令、版本、hash 和失败原因。
一次可回滚小任务对应一个 Git commit；当前用户未明确要求时不自动提交或 push。

## 12. 交付后的有限路线

```text
C1-C7 产品主线交付
  -> G3 单机 prospective 价值观察（promote/retain/stop）
  -> G4 ASR（仅当文本链有价值）
  -> G5 第二领域（出现真实调用方后）
  -> G6 规模化/多用户/远程高可用（真实容量或权限需求出现后）
```

任何后续工作必须先回答“它属于 C1-C7、G3、G4、G5 还是 G6，哪个用户价值和退出门证明它值得做”。
不能回答的需求停在产品评审，不直接创建代码目录。达到 C1-C7 以后，默认工作是观察和评估，而不是继续堆功能。
