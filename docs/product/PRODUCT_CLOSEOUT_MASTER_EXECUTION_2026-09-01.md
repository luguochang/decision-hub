# Decision Hub 产品收口总执行书

版本：`PRODUCT-CLOSEOUT-MASTER-2026-09-01.v1`
状态：`executed / E2-L passed / historical taskbook`
执行目标：`PRODUCT-CLOSEOUT-01`
产品形态：单机、单 owner、`Crypto Macro Trader` 研究试点
本文件性质：保留收口实施任务和历史验收清单；当前入口是
[产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)。

> 本文件把已经接受的产品架构、DSH-first 决策、平台扩展边界、SDD/BDD/TDD/ADR 规范和 C1-C7 阶段拆成可执行的工作包。它不是新的 Agent 框架，也不替代 ADR、canonical schema、模块 README 或历史执行记录。若本文件与已接受 ADR、`contracts/schemas/` 或 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md) 冲突，立即停止实现，先修订事实源。

## 1. 一句话产品定义

Decision Hub 是一个由官方 DSH Web 驱动的、持续运行的、证据约束的研究智能体产品：它能在没有用户逐次提示的情况下从事件/日历/新闻发现任务，在同一 DSH Session 内主动发现事实缺口、调用已审计能力、继续补证或解释性停止；Hub 负责可信时间、PIT、Gate、业务账本、通知、结果评估和个人资产沉淀。

```text
事件/文本/日历/新闻
  -> admission + durable Run
  -> 官方 DSH Web Session
  -> DSH Agent Loop：计划 -> Tool/Skill/Subagent/MCP -> 结果 -> 发现缺口 -> 下一轮
  -> Research MCP / Capability Gateway：权限、schema、来源、PIT、鲜度、hash、错误
  -> Sufficiency + Deterministic Gate
  -> 人可读报告 + 30m/24h/72h Forecast 或 research_only
  -> committed Artifact -> Outbox 通知
  -> Outcome -> Evaluation -> FailurePattern / Experience 资产
```

“一次 LLM 调用后返回一段文字”是问答，不是本产品的完整形态；“固定调用顺序”是 workflow，也不是本产品的智能性。智能性来自 DSH 的有状态 tool/subagent/session loop，产品可靠性来自 Hub 的确定性可信边界和耐久生命周期。

## 2. 本轮最终产品边界

### 2.1 交付内容

- 唯一用户入口：官方 DSH Web；预装 Decision Hub 官方 Host/Client Plugin，默认工作区为 `Crypto Macro Trader`。
- 后台控制面：Hub API、Research MCP、realtime/research/evolution worker、SQLite/Alembic、LangGraph 生命周期图。
- 正式研究：文本输入已经贯通；音频/ASR 只保留 `TextEnvelope` 适配器口，不在本轮接入推理。
- 研究能力：Official、Market、Search 只有经过 `CapabilityManifest`、契约测试、回放、隔离 live canary 和审计后才可启用。
- 输出：事实引用、主因果链、反方链、30m/24h/72h、Trigger、Invalidation、Gate、停止原因、复查时间。
- 资产：Run、DSH Session/Trajectory/JSONL link、Evidence、PIT Snapshot、Artifact、Forecast、Outcome、Evaluation、FailurePattern、Experience。
- 个人价值：减少手工查找和复核时间，积累可回放的研究方法、失败样本、来源版本和结果标签；不以“报告更长”或一次正确判断作为价值证明。

### 2.2 明确不做

- 自动下单、自动修改 Gate、自动 Promotion、自动扣费、自动安装陌生插件。
- ASR 采集/推理、OCR、PPT、A 股、美股、第二领域、多用户、公共插件市场。
- clone 或 fork DSH Web；不复制 DSH Chat、Session、Trajectory、JSONL、compaction 或插件安装器。
- 在 LangGraph 中重写第二套 ReAct/tool loop、Supervisor、角色调度器或 Provider client。
- 因为一次网络失败引入 Redis、Kafka、Temporal、DBOS、Kubernetes 或微服务拆分。
- 把 replay、fake、旧进程、静态截图或模型自述当成真实事实、预测准确率、盈利或长期稳定性证据。

### 2.3 可用性定义

| 状态 | 必须满足 | 允许的使用方式 |
|---|---|---|
| `engineering_verified` | 契约、测试、静态检查、恢复和失败安全通过 | 工程验证，不代表实时业务可用 |
| `pilot_ready` | E1 + E2-R + E2-L 全部通过，且 readiness 由代码返回 true | 单 owner `research_only` 试运行 |
| `pilot_usable` | `pilot_ready` + 14 天或 20 个高影响事件价值观察 | 可继续使用并决定是否保留 candidate |
| `promoted` | Fixed 与 candidate 同 PIT 对照、owner review、Promotion Gate 通过 | 仅人工批准后切 active；不承诺收益 |

当前事实：R0/R1/R2/R2-L、R2-R-00..06E 和 DSH-NATIVE-CORE 的工程成果保留；
`PRODUCT-CLOSEOUT-EXEC-02` 已通过 E1 和 E2-R replay 主线。Fixed 为 active baseline，
DSH 为 candidate/shadow；Search live transport 仍超时，故 `pilot_ready`、
`pilot_usable` 和 `promoted` 均未达到。

### 2.4 当前唯一根因修复任务（已收口，后续转 E2L-02/live 门）

全新 live 实例发现官方 Feed 的站点固定文案会触发误准入，fresh bootstrap 会建立
历史 research backlog，FIFO 又会饿死 DSH 手动任务。当前唯一允许实施的任务卡是
[E2L-01 事件准入、成本保护与任务优先级实施书](../stages/E2L_01_EVENT_ADMISSION_COST_PRIORITY.md) 的工程/replay 部分已完成，
其设计由 [ADR-0015](../decisions/ADR-0015-event-admission-cost-and-priority.md) 锁定。
其 live 产品复验已并入 E2-L；不得把误准入样本删除后宣称问题消失。当前唯一允许继续的是 E2L-02 的 live capability
验收，具体以 [产品执行与最终验收总表](PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)
和 [E2L-02 阶段卡](../stages/E2L_02_DSH_DURABLE_PROGRESS_AND_DEADLINE.md) 为准。

## 3. 所有权和唯一入口

### 3.1 DSH、Hub 和 Domain Pack

| 层 | 拥有 | 不拥有 |
|---|---|---|
| 官方 DSH Web/Harness | Agent Loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT、Gate、Forecast/Outcome、自动交易 |
| 官方 Host/Client Plugin | DSH 原生扩展 seam、typed intake、Run/Session 关联、状态/报告卡 | 第二套聊天、第二套 loop、账本、Gate |
| Decision Hub Kernel | Event、Run、Evidence、PIT、Sufficiency、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、Outbox | DSH 私有状态、模型 loop、插件安装 |
| LangGraph 外层 | 生命周期、checkpoint、lease、recovery、Evidence Round 边界 | ReAct/tool loop、角色选择、网页搜索策略 |
| Capability Gateway | manifest、schema、权限、域名、timeout、retry、预算、PIT/provenance | 发布结论、修改 Gate、扩大权限 |
| Domain Pack | 事实要求、来源优先级、doctrine、Role/Profile、领域 Gate、评测和 fixture | 通用账本、DSH 内部实现 |
| Decision Desk | 运维、审计、资产、Promotion/Rollback、备份 | 第二个聊天入口、直写 SQL、原始 Provider payload |

用户只需要打开 DSH Web；Hub API、MCP、worker、SQLite 是后台实现。Decision Desk 是运维/审计后台，不是第二个产品入口。

### 3.2 两份前端视图

**DSH Web（日常研究主壳）**

- 复用官方 Chat、Session、History、Plan、Tool、Skill、Subagent、Trajectory、JSONL 和模型设置。
- 插件增加：`建立研究任务`、当前 `run_id`/状态、Evidence Gap、能力成功/失败、Gate、报告、复查/取消/重试、打开 Desk。
- 状态必须区分：`researching`、`partial`、`research_only`、`failed`、`cancelled`、`committed`；失败不可伪装成 `no_trade`。
- 默认只显示人可读摘要；原始 DSH JSONL 通过官方轨迹按需查看。

**Decision Desk（管理和审计后台）**

- Operations：进程、readiness、worker heartbeat、source/capability health、通知 outbox。
- Run Inspector：版本、PIT、timeline、Step/Attempt/Call、Agent 轨迹引用、总失败与 per-capability provenance。
- Evidence：来源、authority、observed/published/received/cutoff、hash、freshness、冲突和覆盖度。
- Decision/Forecast：Artifact、30m/24h/72h、Trigger、Invalidation、Gate、复查时间。
- Assets/Evaluation：Dataset、FailurePattern、Experience、Outcome、Brier、延迟、成本、owner feedback、Promotion/Rollback。
- 页面只消费 Query/View DTO 和 Zod schema，不直读 SQL、LangGraph state 或 Provider raw JSON；桌面/移动视口无横向溢出。

## 4. 两层循环和三份状态

### 4.1 DSH 内层 Agent Loop

```text
读取目标 + Domain Pack + hard gaps
  -> Manager/Supervisor 在授权 manifest 中选择 Tool/Skill/Subagent/MCP
  -> 读取结构化结果
  -> Gateway 校验 authority、PIT、鲜度、hash、schema、错误
  -> DSH 更新 gaps 和下一步计划
  -> gap 未闭合且预算/deadline 允许？在同一 Session 继续
  -> gap 已闭合：输出候选；否则输出 research_only 或解释性停止
```

必须有界：`max_rounds`、`max_tool_calls`、`max_subagents`、总 deadline、单能力 timeout、模型 step timeout、结构化修复次数、成本预算。能力 A 失败不能取消 B/C 已成功结果；取消必须保留为取消；Provider 失败必须保留错误来源。

### 4.2 Hub 外层 Product Loop

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected | failed | cancelled
  -> outcome_due -> evaluated
```

LangGraph 只负责这条耐久生命周期和恢复位置。业务账本、Gate、Evidence 和 DSH Session 不互相冒充。

### 4.3 三份持久化事实

| 状态 | 所有者 | 关联键 | 规则 |
|---|---|---|---|
| DSH Session JSONL | DSH session root | `dsh_session_id`, `trace_ref` | 保留完整对话/工具/子 Agent/compaction 轨迹 |
| LangGraph checkpoint | Hub orchestration store | `run_id`, checkpoint ref | 只保存外层恢复位置，不当业务结论 |
| Hub Ledger | Kernel SQLite/Alembic | `event_id`, `run_id`, `snapshot_id`, `artifact_id` | 只增不改，拥有 PIT/Gate/资产和审计 |

DSH/模型/插件升级不能改写历史 Forecast、Outcome 或 Evaluation。

## 5. 代码结构和可插拔方式

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
  workbench_adapters/      DSH/MCP/capability binding
  query_views/             人可读 DTO，不暴露 SQL/raw state
  evals/                   replay/holdout/shadow/Outcome/Promotion 证据

contracts/
  schemas/                 YAML canonical source；跨语言唯一来源
  generated-*              codegen 镜像，禁止手改

packs/crypto_macro/
  doctrine/ evidence/ profiles/ tools/ gates/ evaluations/ fixtures/

extensions/dsh/decision-hub/
  src/host/                官方 Host seam：readiness/submit/status/cancel/callback
  src/client/              官方 Client seam：状态卡、报告、Desk 链接
```

依赖方向固定为 `apps -> packages -> contracts`。Kernel 不导入 DSH、LangGraph、Provider 或前端；Domain Pack 通过公开 Port 接入；前端通过 Query/View 和 Zod；研究 graph 不直接 HTTP；能力只能经过 Manifest/Gateway。

### 5.1 插件分类

1. **DSH Native Plugin**：官方 DSH 能力单元，可包含 Tool、Skill、Subagent、MCP、Hook、UI、Provider binding。
2. **Product Extension**：产品功能包，定义输入输出、结果视图、命令、资产和权限。
3. **Domain Pack**：领域方法包，定义事实要求、来源 ladder、doctrine、Gate 和评测。
4. **Capability Plugin**：经 manifest 审计的外部能力适配器，不能自行发布结论。

一个业务功能可组合多个插件，但组合关系由 DSH Agent Loop 发现和调用，可信入账和发布由 Hub Gateway/Gate 决定。新增 PPT、A 股、美股时先建独立 Extension/Pack，不把字段塞进 Platform Core；只有第二个真实调用方证明语义相同，才抽取共享接口。

## 6. C1-C7 实施总清单

每张卡必须按 `SDD -> ADR（如需） -> BDD -> TDD Red/Green/Refactor -> 集成/回放 -> live canary -> 文档/状态/CHANGELOG` 执行。没有证据不能勾选。

### C1 唯一入口与关联（已验证）

- [x] 启动器启动 Hub API、三个 worker、Research MCP 和官方 DSH Web。
- [x] 只打印一个 DSH URL，自动注册 `Crypto Macro Trader` workspace。
- [x] typed intake 通过稳定幂等键创建唯一 durable Run 和 DSH Session link。
- [x] DSH Web、Hub Run、Decision Desk 能互相定位；readiness 可读。

**保留回归**：重复提交、旧 session、callback 丢失、同一 Run 多次查询不得重复建 Event/Session。

### C2 正式 DSH Runtime（工程/replay 验证，live 成功路径待 C4）

- [x] 正式 live profile 拒绝隐式 `replay.research`，candidate/fallback 不成为第二 active chain。
- [x] submit/status/cancel/reconcile/callback 具备幂等、恢复和错误 provenance。
- [x] DSH Session/Trajectory/JSONL 只保存 link/reference，Hub 账本独立。
- [ ] 至少一条已审计且新鲜的 Official/Market/可用 Search 能力在同一 Session 中完成 live 结构化结果回传。

**当前阻塞**：`codexai.club` 的 `/v1/responses`/web search transport 在 DSH deadline 内不返回，不能把 Provider 失败改写为业务 `no_trade`。先验证 transport 或使用已通过独立 canary 的 typed capability；不重写 DSH/LangChain/OpenAI SDK。

### C3 主动补证闭环

- [x] Run 启动加载 `crypto_macro` 六类事实要求及 capability ladder（离线/replay 已验证）。
- [x] hard gap + 可用能力时，DSH 在同一 Session 继续至少第二轮调用；generation 2 的 user message/turn 已在固定 replay 验证，live continuation 仍由 C4/E2-L 管理。
- [x] 每轮保存 Plan、ToolInvocation、EvidenceCandidate、Sufficiency、失败 provenance、stop reason（离线/replay 已验证）。
- [x] 一项 capability 失败不取消其他成功项；取消、超时、Provider failure 分类准确（离线/replay 已验证）。
- [x] hard gap 未闭合只能 `research_only/no_trade`，不生成方向性 Artifact/Forecast（离线/replay 已验证）。

**BDD 主线**：Given 一个 Warsh/Fed 事件和六类 gap，When Official + Market 能力返回，Then 页面显示至少两轮工具轨迹、Evidence coverage 和最终报告；When Search 超时，Then 已成功能力保留、Run 显示可重试错误、不发布错误方向。

### C4 事实能力准入

- [x] 六类事实均有 manifest、输入/输出 schema、license/audit、authority floor、freshness、PIT、hash、timeout、retry、cost、error codes、fixture、healthcheck（工程/离线门）。
- [ ] Official、Market、Search 分项通过 contract/replay/live canary；默认 deny-by-default（Official/Market 已通过一次只读 canary，Search 仍超时）。
- [x] 低 authority、stale、future timestamp、unknown authority、schema error、429/5xx/timeout 都 fail-closed（离线/专项回归）。
- [x] canary 记录 evidence count、authority、freshness、PIT、latency、cost、error provenance；不切 active pointer（已记录，Search 失败也保留 provenance）。

**当前已知证据**：Official 宏观、FRED 跨资产、CoinEx 现货/衍生品只读 canary 已能返回结构化 Evidence；FRED 约 10.8 天旧，freshness Gate 必须显示 stale，不能冒充实时确认。`web.search` 仍被 Provider transport 阻塞。

### C5 报告、通知、观测

- [x] DSH 业务卡显示 Run、round、coverage、缺口、成功/失败 capability、Gate、报告和 Desk 链接（工程/fixture 视图）。
- [x] Desk 显示 Timeline、Evidence lineage、PIT、per-capability `error_code/origin/cause/retryable`、stop reason（工程/fixture 视图）。
- [x] committed Artifact 才进入 outbox；通知失败可重试，不重新分析、不改写账本（离线回归）。
- [x] success、partial、insufficient/stale、provider failure 四种页面和 API fixture 通过；官方 DSH Web 三类 replay 报告已真实验收，live 页面仍待 C4/E2-L。

### C6 资产和评测

- [x] 每个 Forecast 固化 horizon、probability、trigger、invalidation、Snapshot、Runtime/Provider/Pack/schema 版本（工程/契约测试）。
- [x] 到期由 MarketDataPort 计算 Outcome、Brier、方向和净收益；未到期不写猜测标签（离线回归）。
- [x] Fixed 与 DSH candidate 使用同 PIT 数据集完成 replay/holdout/shadow 对照（离线回归；prospective 价值未开始）。
- [x] 失败样本、owner feedback、manifest、Role/Profile、Runtime/Provider、FailurePattern/Experience 可导出（工程/契约测试）。
- [x] 新 Pack/Plugin 只能经回放、holdout、shadow、人工 review 后进入 candidate（Promotion Gate 回归）。

### C7 单机运维和停止门

- [x] 一键启动/停止、readiness、heartbeat、日志、SQLite WAL、checkpoint、Session root、备份/恢复、integrity check 有 runbook（工程/离线验收）。
- [x] restart、lease 过期、callback 丢失、重复提交、部分 worker 失败、磁盘不足均有测试或安全状态（工程/离线验收；最终 live 实例仍待验证）。
- [ ] 每次验收保存命令、端口、runtime mode、版本、hash、截图、console、API 摘要（本轮已有桌面截图/hash、API 和恢复证据；当前 revision 的真实移动 viewport 与 console 导出仍 pending）。
- [ ] C1-C7 通过后停止工程功能扩张，进入 14 天/20 事件价值观察。

## 7. 下一张任务卡和执行顺序

工程/replay 任务 `C3-A/C3-B/C5/C6/C7` 已闭环。当前唯一允许继续的产品目标是：

```text
目标：不新增 Agent/工作流/页面框架，只解决 E2-L 的真实能力准入，使正式 DSH Session 用已审计且新鲜的 live capability 完成一次成功闭环，并保留 partial/insufficient 安全失败。
价值：owner 看到的报告来自实时可核验事实，而不是 replay；一旦通过立即停止功能扩张并进入价值观察。
```

按以下顺序执行，前一张卡未达退出门不得跳到下一张：

| 卡片 | 允许路径 | 主要复用 | 退出证据 |
|---|---|---|---|
| `C3-A` capability request hardening | `done (replay)` | Pydantic/codegen、现有 Gateway、LangGraph evidence round | 六类 typed requirement、admission 错误和 authority floor 回归通过 |
| `C3-B` session continuation | `done (replay)` | DSH Agent Loop、官方 Session、LangGraph checkpoint/lease | 同 Session generation 2、partial failure、cancel provenance 已验证 |
| `C4-A` capability canary | `partial / only active task` | 只允许现有 Provider adapters、canary 和 manifest | Official/Market 通过；Search timeout；须取得一条新鲜 live 成功闭环 |
| `C5` report/notify/observe | `done (engineering/replay)` | canonical Query/View、React/Zod、outbox/retry | DSH 人可读报告、Desk、失败 provenance 和三类页面通过 |
| `C6` asset/eval | `done (engineering/replay)` | 既有 Outcome/Brier/Evaluation、固定 PIT 数据集 | Forecast/Outcome/FailurePattern/Experience 契约和回放通过；prospective 未开始 |
| `C7` delivery | `done (engineering/recovery)` | Compose、SQLite WAL/Alembic、LangGraph checkpoint | fresh migration、restart、backup/restore/integrity/recovery 通过；当前 UI 的移动/console 资产 pending |

### 7.1 每张任务卡固定模板

```text
目标：一句话，可在一个 Run/页面观察
价值：一个 owner 可感知结果
允许路径：明确目录
禁止路径：不新增第二套 loop/账本/DTO/Provider client
契约：schema、event、query view、错误码
BDD：success、partial failure、insufficient/stale、cancel、recovery
TDD：先写失败测试，固定时间/Provider fixture/随机性边界
停止条件：需要 ADR、扩大权限/费用、改 Gate/账本/active pointer 时停下
验收：实际命令、运行端口、日志/截图/API/hash
收尾：README、CURRENT_STATE、IMPLEMENTATION_STATUS、ROADMAP、CHANGELOG
```

## 8. SDD/BDD/TDD/ADR 和上下文约束

### 8.1 强制顺序

```text
SDD（价值/边界/契约/非目标）
  -> ADR（跨模块/不可逆/部署/数据/运行时决定）
  -> BDD（用户可观察行为）
  -> TDD Red -> Green -> Refactor
  -> 集成/回放/隔离 live canary
  -> 文档、状态、CHANGELOG、可复核证据
```

### 8.2 不可漂移不变量

1. Core/领域契约不依赖 DSH、Pi、Codex、LangGraph 或 Provider；它们只能是 adapter。
2. 所有跨模块 schema 只改 `contracts/schemas/`，再运行 codegen；生成镜像禁止手改。
3. DSH 只有一套 Agent Loop；Hub/LangGraph 不复制 ReAct、Supervisor、Session、Trajectory 或插件安装器。
4. Agent、插件、模型和 worker 没有 Gate、Ledger、交易、扣费、Promotion 或扩大网络权限。
5. 公开边界必须经过 Pydantic/Zod/canonical schema，禁止裸 `dict/Any` 穿过模块。
6. `observed_at/published_at/received_at/cutoff_at` 由可信代码拥有；模型不能填写可信时间。
7. 每个失败保留 `error_code/origin/cause/retryable` 和已完成证据；取消不能伪装 Provider 失败。
8. Event、Run、Evidence、Artifact、Forecast、Outcome、Evaluation、migration 只增不改。
9. replay/fake/截图不能证明真实网络、实时稳定、预测准确或盈利。
10. 未执行的检查保持 `pending`；没有截图、console、API 和 hash 资产不能写浏览器验收通过。

### 8.3 上下文压缩

每次新任务只读：`INDEX.md`、`docs/context/CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、本文件、当前任务卡、相关 ADR/schema/README/测试。使用：

```bash
./.venv/bin/python tools/context/build_task_context.py \
  --objective "<单一任务目标>" \
  --paths <允许修改的目录>
```

出现三个以上不相关模块、架构事实冲突、同一缺陷两次补丁仍失败或目标不能写成一句话加三个断言时，停止编码，先把事实/决策/目标/不做/证据/未决提炼到 `docs/context/` 或 ADR。

## 9. 最终验收 checklist

### 9.1 工程门

```text
[x] git diff --check
[x] ./.venv/bin/python -m tools.contract_codegen check
[x] ./.venv/bin/python tools/docs/check_module_docs.py
[x] ./.venv/bin/pytest -m "not live" -q
[x] ./.venv/bin/ruff check packages apps migrations tests tools
[x] ./.venv/bin/pyright
[x] pnpm --dir extensions/dsh/decision-hub test
[x] pnpm --dir extensions/dsh/decision-hub build
[x] pnpm --dir apps/decision-desk test
[x] pnpm --dir apps/decision-desk build
[x] docker compose config --quiet
[x] fresh migration + integrity + backup/restore
```

### 9.2 E2-R replay 产品主线门

```text
[x] 新实例启动全部组件并 ready，未引用旧端口/旧进程
[x] DSH Web 发现 Trader workspace，受管 Session 出现在官方工作区树
[x] 同一 Run 只有一个 DSH Session，callback/reconcile 幂等
[x] success、partial capability failure、low-authority/stale/insufficient 场景贯通
[x] hard gap + 可用能力触发同 Session generation 2 Tool/Evidence 动作
[x] 页面显示 per-capability error code、origin、cause、retryability
[x] Provider failure 不生成 Evidence/Artifact/Forecast，不伪装 no_trade
[x] sufficient 只在 authority/PIT/freshness/coverage 通过后发布 Artifact/Forecast
[x] restart、callback 丢失、lease 过期后可恢复且不重复提交
[ ] 当前 revision 的桌面/移动截图、console、API、版本和 hash 全部可复核（桌面截图/hash/API 已完成；真实移动 viewport 和 console 导出 pending）
```

### 9.2.1 E2-L live 产品主线门

```text
[ ] Search 或等价新鲜 live capability 完整通过 contract/canary
[ ] 正式 DSH Session 完成 live Evidence -> Sufficiency -> Gate -> Report
[ ] live success、partial failure、insufficient 三场景保持同一失败语义
[ ] pilot_ready 由代码/readiness 置为 true；不得靠文档手工宣称
```

### 9.3 价值门

```text
[ ] 14 天或 20 个高影响事件
[ ] Fixed 与 DSH candidate 使用同一 PIT 数据集对照
[ ] 30m/24h/72h Outcome 到期并计算 Brier/方向/覆盖率
[ ] 记录首证据延迟、最终延迟、工具调用数、成本、通知延迟、失败率
[ ] owner 确认是否减少人工查证、是否理解停止原因、是否愿意继续使用
[ ] 形成 promote / retain_baseline / stop ADR 或决策包
```

## 10. 真实阻塞和停止规则

当前最大阻塞是外部 Provider transport，不是继续增加 Python 逻辑即可解决的问题：

- `/v1/models` 可见 `gpt-5.5`，但 Responses web-search 请求在 deadline 内不返回；Chat 路径曾返回非 JSON/不稳定响应。
- DSH 已进入 MCP 并尝试多个 capability；`official.macro` 缺少 `target_url` 时 Gateway 能返回 `research_target_required`，之后仍可继续其他能力。
- Provider 无结构化结果时，Run 必须 `failed/degraded/research_only`，不得写 Evidence、Artifact、Forecast，不得显示方向性 `no_trade` 作为结论。
- 不能通过延长超时、复制一套 HTTP client、切换未审计协议、把 replay 当 live 或把错误改名来“修复”阻塞。

允许的解决路径只有：

1. 用最小隔离 canary 确认 codexai.club 的实际 Responses、Chat 或 WebSocket 协议，并记录脱敏结果；或
2. 启用已通过独立 contract/replay/live canary 的 Official/Market typed capability，先证明成功 Evidence/Artifact/Forecast 主线。

若需要新网络域名、额外费用、修改 Gate/账本/active pointer、自动交易、第二领域或第二套 Agent Loop，必须新增 ADR 并重新取得 owner Gate。

## 11. 文档和变更同步

本任务每次代码变更必须同步：

- 本文件对应任务卡的 checklist 和证据；
- 受影响模块 `README.md`；
- `docs/context/CURRENT_STATE.md`、必要时 `CURRENT_DECISIONS.md`；
- `docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`；
- 用户可见行为写入 `CHANGELOG.md`；
- 新的不可逆决定写入 `docs/decisions/ADR-NNNN-*.md`；
- 真实运行命令、端口、revision、错误、截图、console、API 摘要写入 `docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_YYYY-MM-DD.md`。

长期文档和中间产物分离：临时脚本、日志、截图和 context manifest 进入 `tmp/` 或 ignored acceptance 目录；不能把推理草稿当事实源。未通过的 checklist 保持 `[ ]`，不能为了视觉完整提前勾选。

## 12. 交付终止条件和未来路线

E1 与 E2-R 已通过。现在停止新增工程功能，只允许闭合 E2-L 的真实 capability
准入和缺失的验收资产；E2-L 通过后，`PRODUCT-CLOSEOUT-01` 立即进入 G3/P4
价值观察。后续只有在价值证据支持时才开新 Stage：

```text
C1-C7 DSH Native Trader Pilot
  -> G3 单机 prospective 价值观察
  -> G4 ASR（文本链已有价值后）
  -> G5 第二领域（出现真实调用方后）
  -> G6 多用户/远程 HA（真实容量、权限或协作需求出现后）
```

本文件的最终目标不是把系统做得更大，而是交付一个可以真实运行、失败可解释、证据可追溯、资产可复用、后续可替换的最小成熟产品；达到停止门后默认观察和评估，不再无限增加角色、插件或基础设施。
