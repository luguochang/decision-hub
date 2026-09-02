# Decision Hub 产品实现与最终验收方案

版本：`PRODUCT-IMPLEMENTATION-2026-08-31.v1`
状态：`executed / historical C1-C7 implementation plan`
Owner 范围：单 owner、单机、`Crypto Macro Trader Pilot`。
执行目标：收口 `PRODUCT-CLOSEOUT-01` 的 C1-C7 主线，形成可启动、可恢复、可观察、可审计的 DSH 原生研究试点；通过产品验收前，不扩展 ASR、PPT、第二领域、多用户或自动交易。

> C1-C7 与 E2-L 已执行并通过。本文件只保留详细实施说明和历史证据；当前产品状态、
> 完成 checklist 和 E3 停止线以
> [产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准。

Owner Gate：2026-09-01，owner 已接受本文的产品形态、DSH-first 边界、两层插件模型、C1-C7 顺序、工程/产品/价值三层验收门和停止条件，并授权按本文实施与自测。实现不再等待逐卡口头确认；只有扩大网络权限、产生未声明费用、修改 Gate/账本/active pointer、自动交易、引入第二领域或需要第二套 Agent Loop 时才必须停下重新确认。

> 本文件是 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md) 下的详细实现与验收 checklist，不替代架构、契约或顶层交付合同。它回答“具体改哪里、如何验收、什么时候停止”。若与 accepted ADR、canonical schema、阶段 Charter 或顶层交付合同冲突，立即停止实现，先修订决策；不能用局部补丁同时维持两套事实。

## 0. 事实源和阅读顺序

长期真源按以下优先级解释：

1. Owner 已接受的 ADR 和 `contracts/schemas/` canonical schema；
2. [产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)；
3. [产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，作为当前唯一执行/收口入口；
4. [PRODUCT-CLOSEOUT-01 Stage Charter](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md) 和本文件的 C1-C7 详细 checklist；
5. [DSH 与 Hub 系统总装设计](DSH_HUB_SYSTEM_ASSEMBLY.md) 与 [产品执行总方案](PRODUCT_EXECUTION_MASTER_PLAN.md)，作为架构和执行总参考；
6. 受影响模块 `README.md`、代码、测试和 runbook；
7. `docs/context/`、`IMPLEMENTATION_STATUS.md`、`ROADMAP.md` 和 `CHANGELOG.md` 的当前投影。

新会话先读 `INDEX.md`、`docs/context/CURRENT_STATE.md`、`docs/context/CURRENT_DECISIONS.md`、[最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)、本文件和当前任务卡，再读取直接相关代码。聊天内容、临时草稿和旧研究只能提供线索，不能授权实现。

## 1. 产品目标

### 1.1 交付对象

交付对象不是一个“可以问答的网页”，而是以下闭环：

```text
事件/文本/日历/新闻发现
  -> Hub admission（event_id + run_id）
  -> DSH Web Session（官方 Agent Harness）
  -> Agent 自主识别 hard gap、调用授权能力、继续补证
  -> Evidence Gateway（来源/权限/时间/hash/PIT/冲突）
  -> 充分度与确定性 Gate
  -> 人可读报告 + 30m/24h/72h Forecast 或 research_only
  -> 通知 outbox
  -> Outcome/Evaluation/FailurePattern/Experience 资产
```

只有“读取目标、发现缺口、调用工具、读取结果、继续计划、在充分/预算/安全边界内停止”才算 Agent 行为。一次 LLM 调用是问答；固定调用顺序是 workflow；本产品需要 DSH 的有状态 tool/subagent/session loop 加 Hub 的 durable 产品循环。

### 1.2 首期价值假设

- 对 owner：减少手动搜集宏观事件、来源、跨资产和加密衍生品证据的时间；
- 对决策：把事实、因果链、反方链、触发/失效条件和停止原因放在同一份可追溯报告中；
- 对长期资产：把失败、Outcome、评测、领域 doctrine、来源 manifest、能力版本和 owner 反馈沉淀为可迁移资产。

这三个假设必须用前瞻观察验证。代码通过、报告变长或 JSONL 增多，都不等于预测准确、盈利或生产可用。

### 1.3 非目标（本阶段禁止）

- 自动下单、自动修改策略、自动 Promotion、自动安装陌生插件或自动扣费；
- 复制 DSH Web 的 Chat、Session、Trajectory、JSONL 和插件安装能力；
- 在 LangGraph 中重写第二套 ReAct/tool loop、Supervisor 或角色调度器；
- 预先建设多用户、租户、Postgres、Redis、Temporal、Kubernetes、公共插件市场；
- 在文本主链价值尚未证明前接 ASR、PPT、A 股、美股等第二领域；
- 用 replay、fake、静态截图或旧进程证明真实网络覆盖、实时稳定性、预测优势或收益。

## 2. 最终产品形态和用户视图

### 2.1 唯一用户入口

交付启动器只打印一个官方 DSH Web URL。用户不需要理解 Hub API、MCP、worker 或数据库端口：

```text
DSH Web
  -> 预装 Decision Hub 官方 Host/Client Plugin
  -> 默认工作区 Crypto Macro Trader
  -> 自动 Run、Agent 轨迹、证据缺口、报告和复查
  -> 需要运维/审计时才打开 Decision Desk
```

Hub API、research MCP 和 worker 是后台组件；Decision Desk 是 Operations/Ledger/Evaluation 管理后台，不是第二个聊天产品。

### 2.2 DSH Web 应看到的内容

直接复用官方 DSH：Chat、Session history、Plan、Tool、Skill、Subagent、Trajectory、上下文压缩和 JSONL。Decision Hub client plugin 只增加业务投影：

| 区域 | 人可读内容 |
|---|---|
| 工作区 | `Crypto Macro Trader`、Live/Replay、Fixed/Candidate、runtime/provider/version |
| 当前 Run | event、run、DSH session、状态、开始/完成时间、取消/重试 |
| 研究进度 | 当前 round、hard/soft coverage、成功/失败 capability、unresolved gap、stop reason |
| 报告 | 事实引用、主/反因果链、30m/24h/72h、触发条件、失效条件、Gate |
| 运维动作 | 取消、重试、复查、反馈、打开 Decision Desk |

默认不展示 LangGraph state、Provider 原始 payload、密钥、完整 SQL 或无意义 raw JSON。原始 DSH JSONL 通过官方轨迹按需查看。

### 2.3 Decision Desk 应看到的内容

Operations/Readiness、Run Inspector/Timeline、Evidence lineage/PIT、Source/Capability 健康、Artifact/Forecast/Outcome/Evaluation、FailurePattern/Experience、Dataset、Promotion/Rollback 和 Outbox。它只读 Query/View DTO，不直读数据库，不拥有 Agent Loop。

## 3. 架构和所有权

```text
+-------------------------- DSH Web ---------------------------+
| Chat | Session | Trajectory | Tool | Skill | Subagent | JSONL |
| official Decision Hub Host/Client Plugin                    |
+-----------------------------+-------------------------------+
                              | official DSH seam
                              v
     +------------------------+------------------------+
     | Decision Hub Product Control Plane          |
     | API -> durable Run -> LangGraph lifecycle   |
     | Evidence/PIT -> Sufficiency -> Gate -> Ledger|
     | Artifact/Forecast -> Outbox -> Outcome/Eval |
     +------------------+-------------------------+
                        | MCP Capability Gateway
                        v
              Search / Official / Market adapters
```

| 组件 | 拥有 | 明确不拥有 |
|---|---|---|
| DSH Web/Harness | Agent loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL | Hub 账本、PIT、Gate、Forecast/Outcome、Promotion |
| 官方 DSH Plugin | Host/Client route、Session 关联、状态/报告卡、Hub command bridge | 第二套 loop、业务账本、Gate |
| Decision Hub Kernel | Event、Run、Evidence、PIT、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation | DSH 私有状态、模型 loop、插件安装器 |
| LangGraph | durable lifecycle、checkpoint、lease、recovery、Evidence Round | 通用 Agent loop、网页搜索策略、金融角色 |
| Domain Pack | doctrine、事实要求、来源优先级、能力绑定、领域 Gate、评测 | 通用账本、Harness 内部实现 |
| Capability Gateway | schema、权限、域名、timeout、retry、预算、provenance | 自行发布结论、修改 Gate、扩大权限 |
| Decision Desk | 运维、审计、Query/View、资产和 Promotion/Rollback 命令 | 第二套聊天、直写 SQL、改写历史 |

硬规则：Agent 只能提交候选；代码 Gate 是唯一裁决。任何 DSH、插件、模型或 worker 都不能写交易权限对象、修改 active pointer、自动交易、自动安装插件或覆盖历史账本。

## 4. 两层循环和三份持久化状态

### 4.1 DSH 内层 Agent Loop

```text
读取目标 + Domain Pack + 当前 hard gaps
  -> Manager/Supervisor 选择 manifest 中的 Tool/Skill/Subagent/MCP
  -> 取得结构化结果
  -> Gateway 做权限、来源、server-owned 时间、hash、PIT 和 schema 校验
  -> 更新 evidence/gap/plan
  -> 有授权能力且仍有关键 gap？继续下一轮；否则有界停止
```

DSH Agent 必须在 `max_rounds`、`max_tool_calls`、`max_subagents`、总 deadline、单工具 timeout、模型 step timeout、修复次数和成本预算内行动。一项 capability 失败不能吞掉其他成功结果；没有合法能力必须产生 `research_capability_unavailable` 或 `critical_data_unavailable`，不能把 `no_trade` 伪装成证据充分。

### 4.2 Hub 外层 Product Loop

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected
  -> outcome_due -> evaluated
```

LangGraph 只管理这条产品生命周期的 checkpoint、lease、恢复、取消和 Evidence Round；不再新增一个 Supervisor 或 reviewer/judge agent 层。业务状态和 Gate 由 Kernel 代码裁决。

### 4.3 状态分离

| 状态 | 所有者 | 用途 | 不能替代 |
|---|---|---|---|
| DSH Session JSONL | DSH | 对话、Tool/Subagent、compaction、完整轨迹 | Hub Evidence/Gate/Ledger |
| LangGraph checkpoint | Hub Orchestration | 外层执行位置、恢复信息、round 边界 | 业务事实和结果 |
| Hub Ledger SQLite/Alembic | Kernel | Event、Run、PIT、Evidence、Artifact、Forecast、Outcome、Evaluation、Outbox | 完整 raw trace |

三者通过 `event_id/run_id/dsh_session_id/trace_ref/snapshot_id/artifact_id` 关联，只增不改历史。DSH 升级、模型更换或插件替换不得改写过去的 Forecast、Outcome 或 Evaluation。

## 5. 插件、Domain Pack 和未来扩展

### 5.1 两层插件模型

```text
DSH Native Plugin
  = Tool / Skill / MCP / Subagent / Hook / UI / Provider 的安装运行单元

Product Extension / Domain Pack
  = 目标、事实要求、方法、Gate、结果契约、评测、历史资产
```

`crypto_macro` 是当前唯一真实 Domain Pack。它可以附带一个薄 DSH bundle，把 Role Profile、MCP binding、命令和视图接入官方 DSH。DSH-only 工具可以留在 DSH；凡会进入正式 Evidence/Gate 的能力必须通过 `CapabilityManifest` 和 Gateway 审计。插件卸载不能删除 Hub 历史。

### 5.2 CapabilityManifest 最低契约

每个能力必须声明：

```text
capability_id/version/provider_id
input_schema_ref/output_schema_ref
permissions/network_domains/data_classes
authority_level/supports_pit/freshness_policy
timeout/max_retries/cost_estimate
failure_codes/fallback_ids/license/audit_status/owner_enabled
replay_fixture_ref/healthcheck_ref
```

能力按 `capability_id` 调用，而不是按 Python import 路径调用。Generic Search/Fetch 用于发现和正文；Fed、BLS、BEA、Treasury、DXY、2Y/10Y、BTC spot、funding/OI/basis/liquidation 等精确事实优先使用 typed capability。未通过授权和 canary 的能力只能是 `disabled` 或 `shadow`。

### 5.3 新领域口子

新增 PPT、A 股或美股时，先创建独立 Product Extension/Domain Pack 和 ADR；只复用公开 Platform Port（Event、Run、Evidence、Artifact、Asset、Evaluation、Trace reference）；领域自己定义输入、结果、Gate、来源和评测。出现第二个真实调用方前不提取共享 Core，不能把金融字段塞入 Platform Core。ASR 未来只走 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope`。

## 6. 目标代码结构和依赖方向

```text
apps/
  hub_api/                 # REST command/query/callback；不执行长 Agent 任务
  hub_worker/              # realtime/research/evolution durable worker
  research_mcp/            # 唯一正式研究能力网关
  decision-desk/           # Operations/Ledger/Evaluation 管理后台

packages/
  kernel/                  # 领域无关 Run/Evidence/PIT/Gate/Ledger/Outcome
  orchestration/langgraph/ # 外层 lifecycle/checkpoint/recovery
  runtime_adapters/dsh_runtime/ # DSH SDK/Web Host adapter
  provider_adapters/       # Search/Official/Market/Notification
  workbench_adapters/      # DSH/MCP capability binding
  query_views/             # 人可读 DTO；不暴露 SQL/raw state
  evals/                   # replay/holdout/shadow/Outcome/Promotion

contracts/
  schemas/                 # YAML canonical single source
  generated-*              # codegen 镜像，禁止手改

packs/crypto_macro/        # doctrine/evidence/profiles/tools/gates/evaluations
extensions/dsh/decision-hub/ # 官方 dsh.bundle + dsh.client 薄插件
infra/dsh/                 # upstream lock、启动、升级、回滚、验收
```

依赖只能向内：`apps -> packages -> contracts`。Kernel 不导入 DSH、LangGraph、Provider 或前端；Domain Pack 通过公开 Port；前端通过 Query/View 和 Zod；研究 graph 不能直接 HTTP 或直写 SQLite。

## 7. 可靠性、错误和信息不足语义

### 7.1 三时间戳 PIT 铁律

`published_at` 是来源声称发布时间，`received_at` 是本系统收到时间，`observed_at` 是服务端确认观察时间。模型、客户端或 Provider 返回的时间只能作为 payload 字段，不能覆盖服务端 `observed_at`。`observed_at > cutoff_at`、来源权威性低于 requirement floor、hash/schema 不合法或跨事件污染时，Evidence 必须拒绝并保留 provenance。

### 7.2 错误分类

| 类别 | 例子 | 处理 |
|---|---|---|
| `research_capability_timeout` | 工具在 deadline 内未返回 | 可按 policy 重试；不发布方向性结果 |
| `search_provider_failed` | 上游 5xx/429/协议失败 | 保留失败 capability 和 provenance；不吞掉其他结果 |
| `research_capability_unavailable` | manifest 无合法 capability 交集 | fail-closed，停止并列出 gap |
| `evidence_pit_violation` | 观察时间越过 cutoff | 拒绝该 Evidence；不能压成 provider timeout |
| `evidence_low_authority` | 低于 authority floor | 不能覆盖 hard requirement |
| `permission_denied` | 域名、secret、数据类不允许 | 不重试，不扩大权限 |
| `cancelled` | owner cancel/worker shutdown | 保持取消语义，不伪装 Provider 失败 |
| `budget_exhausted` | round/tool/cost/deadline 用尽 | 解释性停止，结果只能 research_only/no_trade |

所有错误都必须有 `error_code/origin/cause_code/retryable/capability_id`，并同时出现在 DSH 轨迹、Hub Run 视图和 Decision Desk 人可读摘要中。失败 Run 和已完成 Evidence 永不删除或覆盖。

### 7.3 信息不足时 Agent 的行为

第一轮发现 hard gap 不能直接停止。只要 manifest 存在授权能力且预算未用尽，DSH 必须继续下一轮；并行调用的一项失败不能取消其他调用。没有合法能力、连续无进展、预算耗尽或 PIT/权限拒绝时才停止，并报告：已覆盖事实、失败能力、未解决 gap、停止原因、复查条件和当前可发布等级。

### 7.4 Live 能力准入与启动语义

`live` 描述的是事实获取和 PIT 语义，不是页面是否允许输入。产品启动器必须同时满足以下约束：

1. `DECISION_HUB_RESEARCH_EXECUTION_MODE=live` 时禁止把 `replay.research` 放入 allowlist；replay 只属于固定回放和验收命令；
2. 至少存在一个 `license_status=approved`、`audit_status=approved`、具有 live adapter 且凭据/健康检查可用的 capability，否则启动失败并显示 `research_capability_unavailable`；
3. 首个允许进入 live 产品的能力是已审计的 `web.search`；它负责发现和来源线索，但 `search_derived` 证据不能冒充 Official/Exchange 事实；
4. `official.macro`、`market.cross_asset`、`market.crypto_derivatives` 和 `web.fetch` 在各自 contract/replay/live canary、PIT、authority、成本和失败注入验收前保持 `candidate/disabled`；
5. 每项 typed capability 晋级必须单独留下 manifest 版本、来源域名、测试、canary、成本和 ADR/状态记录，不能通过扩大 Prompt 或把所有网络访问交给通用搜索来绕过；
6. 页面必须明确显示当前 enabled/disabled/candidate capability、Provider、版本、最近健康状态和本 Run 的成功/失败能力，不能只显示笼统的“证据不足”。

因此，产品允许在只有 `web.search` 的初始 live 形态下运行和主动发现来源，但 hard requirement 仍可能因缺少 Official/Market attestation 而 `research_only`。C4 的职责就是逐项把真实 typed capability 从 candidate 晋级到 approved；C4 未通过前不能把报告不足误判为 Agent Loop 已完成。

## 8. 本轮唯一大目标：PRODUCT-CLOSEOUT-01 / C1-C7

### 8.1 进入条件

- ADR-0012/0013、产品执行总方案和本文件已被 owner 接受；
- R0/R1/R2/R2-L、R2-R-00..06E 的离线工程基线保留；
- Fixed baseline 仍为 active，DSH 仍为 candidate/shadow；
- `contracts/schemas/`、PIT、Gate、Ledger 和历史 migration 不迁移、不重写。

### 8.2 任务卡

| 卡片 | 实现范围 | 允许修改 | 关键 BDD/TDD 断言 | 退出证据 |
|---|---|---|---|---|
| C1 启动/工作区 | 单一启动器、通过官方 `workspace/create` 注册 `Crypto Macro Trader`、Run/Session 双向链接 | `infra/dsh`、Host/Client、runbook | 启动器只输出 DSH URL；Trader workspace 可发现；同一 run/session 可互查 | 新实例 API/MCP/DSH readiness、页面证据；详见 [2026-09-01 执行记录](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md) |
| C2 正式 DSH Runtime | 一个 runtime 选择点、live/replay 明示、版本和回调 | `runtime_adapters/dsh_runtime`、`extensions/dsh`、worker composition | Web 失败/重启/重复 callback 不丢 Run、不重复 commit | Web Host + Session/Trajectory + Hub Ledger 对照；真实 Provider 失败已留存，成功产品门仍待 |
| C3 主动补证 | six requirement、bounded rounds、parallel isolation、sufficiency | `packs/crypto_macro`、research graph、MCP gateway | 有授权能力且有 hard gap 时进入下一 round；部分失败保留成功证据；不足只能 research_only | success/partial/insufficient 三场景 |
| C4 能力准入 | Search/Official/Market manifest、PIT、authority、freshness、cost、canary | `provider_adapters`、`contracts`、`apps/research_mcp` | 未授权/低权威/超时/错误能力不能进入正式 Gate | replay + 一次隔离只读 canary；失败有 provenance |
| C5 报告/通知/观测 | DSH 业务卡、Desk Query/View、local outbox/dry-run | `query_views`、前端、notification adapter | 报告包含事实/主反链/三 horizon/gap/stop；通知幂等、失败可重试 | DSH/Desk 同一 Run 一致；截图和 console |
| C6 结果/资产 | Outcome、Brier/net return、FailurePattern、Experience、owner feedback | `kernel`、`evals`、Desk | 到期结果只追加；失败先入资产；历史 Forecast 不改写 | fixture + 真实观察记录、资产 lineage |
| C7 单机交付 | Compose/启动停止/备份恢复/heartbeat/磁盘检查 | `compose.yaml`、`infra`、runbook | 进程退出可恢复；旧实例不冒充新版本；数据目录可备份恢复 | 新镜像或明确外部阻塞；recovery smoke |

允许的实现顺序：`C1 -> C2 -> C3 -> C4 -> C5 -> C6 -> C7`。如果某卡暴露跨模块架构冲突，停止在该卡，不进入下一卡，不用 UI 或 Prompt 掩盖。

## 9. SDD/BDD/TDD/ADR 和上下文治理

### 9.1 每张任务卡开始前

- [ ] 写一句价值目标、输入、输出、非目标和停止条件；
- [ ] 检查 canonical schema/event/Gate 是否变化；变化先改 YAML，再运行 codegen；
- [ ] 明确 DSH、Hub、LangGraph、Domain Pack、Plugin 的所有权；
- [ ] 固化 timeout、retry、cost、error、permission、PIT、幂等语义；
- [ ] 标出允许修改和禁止修改目录；
- [ ] 生成 `tmp/task-context.md`；
- [ ] 跨模块、不可逆、数据留存或 Runtime 决策写 ADR，未接受不写代码。

### 9.2 实现中

- [ ] 先写 Given/When/Then 场景和失败测试（Red）；
- [ ] 复用 DSH 官方 SDK、LangGraph checkpoint/RetryPolicy、Pydantic/Zod、SQLAlchemy/Alembic、OpenTelemetry/structlog；
- [ ] capability 只经 Manifest/Gateway；禁止 graph node 直接 HTTP；
- [ ] Agent 候选与代码 Gate 分离；
- [ ] 失败保留已完成证据和完整 provenance；
- [ ] 不手改生成镜像、不改写历史、不把 secret 写入代码/文档/数据库/trace。

### 9.3 完成前

- [ ] Red -> Green -> Refactor 后跑受影响和全量测试；
- [ ] 通过契约、单元、集成、回放和必要的隔离 canary；
- [ ] 官方 DSH Web 和 Decision Desk 均检查桌面/移动视口，无横向溢出和 console error；
- [ ] 更新模块 README、`INDEX.md`（边界变更时）、`IMPLEMENTATION_STATUS.md`、`ROADMAP.md`、`CURRENT_STATE.md`、`HANDOFF.md` 和 `CHANGELOG.md`；
- [ ] 未运行或被外部环境阻塞的命令必须写成 `pending/blocked`，不能写成 passed；
- [ ] 一个任务一个可回滚 commit；未经 owner 明确要求不 push。

### 9.4 上下文压缩格式

当任务涉及三个以上无关模块、出现互相矛盾的方案、同一根因连续两次修补仍失败，或目标不能用一句话和三个断言表达时，先停止追加上下文，写下：

```text
事实：已验证的代码、测试、外部结果
决策：accepted ADR/canonical schema
当前目标：本任务唯一目标
不做：本任务排除项
证据：文件、命令、commit
未决：需要 owner 决策的事项
```

## 10. 最终验收 checklist

### 10.1 工程质量门

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

### 10.2 产品主线门

- [ ] 新实例由单一启动器启动，API、Worker、MCP、DSH 全部 ready；
- [ ] Trader workspace 在官方 DSH Web 可发现，文本/事件可提交；
- [ ] 正式 Run 真实关联 DSH Session/Trajectory/JSONL，重启和重复请求不重复创建/commit；
- [ ] Agent 在有授权能力时至少完成一次 gap-driven continuation；无能力时显示明确 `research_capability_unavailable`；
- [ ] success、partial capability failure、low-authority/stale/insufficient 四类场景均有 DSH、Hub、Desk 一致证据；
- [ ] 报告包含事实引用、主链/反链、成功/失败 capability、hard gap、三 horizon、Gate、触发/失效和复查；
- [ ] local outbox/dry-run 通知可读、可去重、失败可重试；
- [ ] backup/restore、worker lease、checkpoint recovery 和 callback gap 可复现；
- [ ] 真实 Search/Official/Market 至少有一次隔离、只读、限时 canary；若失败，失败本身有完整 provenance 且产品仍安全降级。

### 10.3 价值门（与工程门分开）

- [ ] 至少 14 天或 20 个高影响事件（先达到者）；
- [ ] Fixed 与 DSH candidate 在相同 PIT、同一输出契约下比较；
- [ ] 记录证据首达延迟、完成率、失败率、成本、通知延迟、30m/24h/72h Outcome、方向/Brier/net return；
- [ ] owner 记录是否节省查证时间、是否理解停止原因、是否愿意继续使用；
- [ ] 根据证据作一次且仅一次 `promote / retain_baseline / stop` 决策。

价值门未通过时，产品可以作为 `research_only` 个人工具保留，但不能宣称交易优势或继续无限加功能。

## 11. 当前状态和本轮执行记录

截至 2026-09-01（历史 2026-08-31 阻塞记录仍保留在执行日志）：

| 范围 | 状态 | 解释 |
|---|---|---|
| R0/R1/R2/R2-L | 离线/本机工程门通过 | 不等于实时价值或盈利 |
| R2-R-00..06E | candidate 完成，`retain_baseline / owner review pending` | Fixed active；DSH shadow |
| G1 | offline complete | 取消、错误 provenance、PIT、并行隔离和失败投影已回归 |
| G2-A/B | manifest/replay complete | 六类事实固定；live Search canary 已 timeout，完整 live 来源门待闭合 |
| C1-C2 | `engineering verified / live provider failed safely` | 官方 DSH Web 入口、typed intake、durable Run、受管 Session/Trajectory/JSONL link、run_id 状态轮询已实测；详见 [2026-09-01 执行记录](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md) |
| C3/C5/C6/C7 | `engineering/replay verified` | 同 Session 多轮、三类报告、资产契约、恢复/备份和质量门已通过；prospective 价值未开始 |
| C4/E2-L | `blocked by live capability` | Search Provider timeout；需已审计且新鲜的 live capability 完成正式 DSH Session 成功闭环 |
| Compose build/up | `verified for latest instance` | 新实例 `8140/8142/51890` 已启动并完成官方 DSH Web/Host/Client/Research MCP 运行验证；此前 registry timeout 仅作为历史失败记录 |
| Search live canary | `failed safely` | provider deadline；无 Evidence、无 Ledger commit、无 active pointer 变化 |
| Live product allowlist | `verified for isolated run` | 本次 live 实例显式只允许 `web.search`，未混入 `replay.research`；Official/Market 仍保持 candidate/disabled |

本轮每次实际执行的命令、端口、版本、截图、console、外部阻塞和失败样本必须记录在最新 [PRODUCT-CLOSEOUT 执行记录](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md)；2026-08-31 记录只保留历史阻塞，不能覆盖后续事实。

## 12. 何时算“可用”和何时停止

### 12.1 个人可用试点

只有以下条件同时满足，才能把第一产品标为 `pilot_usable`：

1. 工程质量门和 C1-C7 主线门通过；
2. 真实来源失败时能准确降级，成功时确实有主动补证；
3. DSH Web、Hub Ledger、Decision Desk 和通知对同一 Run 一致；
4. 至少一段前瞻观察数据支持 owner 节省时间和愿意继续使用。

这仍是只读研究辅助，不是自动交易系统。

### 12.2 停止开发

达到 `pilot_usable` 后停止继续堆基础设施，先观察和评估。若真实来源授权/成本/延迟不可接受、DSH 没有优于 Fixed 的证据、owner 不愿使用、同一根因连续两次修补仍失败，或需求需要第二套 Agent Loop/账本/权限模型，则冻结候选并作 `retain_baseline / stop` 决策。

只有在真实第二领域出现、文本主线价值成立或单机容量证据成立后，才分别开启 G4 ASR、G5 第二领域和 G6 规模化部署；每次都要新建 Stage Charter/ADR，不自动开启下一阶段。

## 13. 文档同步清单

完成任一 C 卡后必须同步：

```text
受影响模块 README
contracts/generated-manifest.yaml（若契约变化）
docs/IMPLEMENTATION_STATUS.md
docs/ROADMAP.md
docs/context/CURRENT_STATE.md
docs/context/HANDOFF.md
CHANGELOG.md
对应 docs/decisions/ADR-xxxx（若有架构决定）
本文件的任务/证据状态
```

本文件是 C1-C7 的详细实现与验收 checklist；顶层执行入口是 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)。总装、架构、契约、ADR 和模块 README 仍各自拥有自己的事实，不把实现细节全部复制到一个超长文档中。
