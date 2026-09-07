# Decision Hub 执行路线图

版本：`ROADMAP-2026-08-26.v1`
用途：把 [产品架构基线](../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md) 中的 R0-R3 规划转换为 GitHub 可逐项追踪的执行清单。本文是里程碑状态入口；每个任务的实现边界、框架复用和 Codex 提示词见 [分阶段执行设计](EXECUTION_PLAN.md)，不替代架构基线、契约和 ADR。

当前执行路线已由 owner 收口到 `PD-00..07`。`PD-00..01` 已完成 requirement lineage、typed
`FactEnvelope`、crypto-macro 语义 Gate、durable EventWatch 和八个事件窗口；`PD-02A/B` 已完成
Provider 契约、失败审计和 Pack 驱动的 OKX/CoinEx stable Router composition；`PD-02C` 已完成
事件窗口 archive、sampler、Run/EventWatch lineage、archive route 和 FactStore/Gate 回放闭环；
`PD-02D` 已完成 order-book imbalance proxy、Pack route 与衍生品完整窗口语义回放；`PD-02E`
已完成 provider-neutral intraday macro/expectation seam 与离线完整语义回放，但真实供应商仍
blocked。`PD-02F` Pack/profile/composition closure、`PD-02G` public adapter canary 和 `PD-03`
Source Registry/官方 `event.identity` parser 已完成；PD-04..06 的主动调度、Inbox、报告、通知、
recheck、DSH 轨迹和 Decision Desk 运行收口已完成。下文 R0..G2-AF 保留为历史工程里程碑，不能覆盖当前 PD Stage，也
不能把历史 coverage 追认为金融语义充分。

R0/R1/R1-L、R2-00 至 R2-05、R2-L、R2-R-00 至 R2-R-06E、DSH-NATIVE-CORE 和
`PRODUCT-CLOSEOUT-01` 的 E1/E2-R/E2-L 已完成。官方 DSH Web 真实主线已完成受管 Session、
两轮主动补证、可信账本、代码 Gate、双前端一致性和后台自动复查，当前可作为单 owner、
单机、只读的 `research_only` 试点使用。Runtime 决策仍为 `retain_baseline`：Fixed active，
DSH candidate/shadow，不自动 Promotion。当前架构/交付基线仍以
[产品交付控制书与最终验收包](product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 为准；
Owner 于 2026-09-02 授权 [D2 官方 DeepSeek Live 主流程复验](stages/D2_OFFICIAL_DEEPSEEK_LIVE_FLOW_PLAN.md)
作为当前交付阻断修复。D2 已在 2026-09-03 真实跑通到安全终态：Provider、DSH Session、主动补证、
Evidence/PIT/Coverage/Gate 均有证据，但 synthesis 以 `structured_output_invalid` 安全失败。
D2 专属浏览器资产和最终质量门已通过：新 bundle 桌面/移动页面只有一个报告区域，移动视口无横向
溢出，console error 为 0；插件 57、Decision Desk 10、Python 395 项测试及静态/契约/文档门
通过。此前工作树复跑记录为 Python `446 passed`、DSH Plugin `60 passed + build`、Decision Desk `10 passed + build`；2026-09-04 最新全量 Python 复核为 `445 passed, 1 failed`，失败是 `10ms` DSH Web 超时用例在 Host `submit` 前已截止、远端没有可取消 Session，但旧断言仍无条件要求 cancel。该测试语义红灯必须在 PD 实现前收口，不能把历史绿灯冒充当前状态。D2 记录中的 Python `395` 保留为 D2 完成时点的历史证据，不与本次复跑混用。D2 不新增功能、不切 active pointer；为回应 hard gap 暴露的问题，`G2-AF-01..04` 技术门已通过，但最新交付阻断审计已提议暂停直接进入 G2-AF-05，等待 owner 决定是否以 `PD-00..07` 取代当前观察路线。
详见 [D2 真实验收记录](evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md) 和
[D2 执行清单](stages/D2_OFFICIAL_DEEPSEEK_LIVE_EXECUTION_CHECKLIST_2026-09-03.md)。

DSH-NATIVE-CORE 的工程验收已完成。详细任务卡、失败根因和退出证据见 [DSH-NATIVE-CORE 完成实施方案](stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md) 与 [Replay/恢复验收记录](evaluations/DSH_NATIVE_CORE_REPLAY_MATRIX_2026-08-31.md)。NATIVE-00 至 NATIVE-05、NC-01 至 NC-07 均有官方 Web/插件/桥接/replay/恢复/回滚/浏览器证据。Owner 已确认后续进入唯一的 [PRODUCT-CLOSEOUT-01](stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)，按 C1-C7 收口首个 Trader Pilot；不自动切 active pointer，不扩大到 ASR/PPT/第二领域。

## 使用规则

- 每个任务先锁契约、失败边界和验收测试，再实现。
- 完成任务必须同步模块 README、测试、实现状态和必要的 ADR。
- `done` 只表示本地测试和文档证据已经存在；外部 Provider 兼容性、业务准确率和生产可靠性分别记录。
- 不为未来场景提前创建无调用方目录、空服务或第二套业务账本。

状态标记：`done` 已验证；`partial` 已有边界或 scaffold，但未达生产验收；`next` 下一批执行；`blocked` 需要外部授权或真实数据。

## R0：Owner Production Core

R0-A 文本核心纵向链已完成。R0-B、R0-C、R0-D 已通过 `R0-CORE-COMPLETE` 总体验收；R1 的授权、契约和 adapter 边界已锁定并通过离线退出门。

### 已完成

- [x] `R0-01` 仓库工程治理：canonical schema、codegen check、ADR、模块 README、TDD/SDD、`.gitignore`。
- [x] `R0-02` 文本纵向链：`ObservationCreate -> TextEnvelope -> Event/Observation -> Snapshot -> Run`。
- [x] `R0-03` LangGraph fixed baseline decision/research graph；policy delta 与 counter-thesis 并行。该退出门只证明纵向链，不证明 Agentic Research。
- [x] `R0-04` AgentRuntime port：Fake、Replay、LangGraph-native Runtime。
- [x] `R0-05` 严格 `AgentPayload` 和 OpenAI-compatible Responses/Chat adapter 配置。
- [x] `R0-06` 确定性 Gate、Artifact、30m/24h/72h Forecast。
- [x] `R0-07` Outcome、Brier、net return、Evaluation Query View。
- [x] `R0-08` SQLite WAL、Alembic、业务账本与 LangGraph checkpoint 分离。
- [x] `R0-09` Idempotency-Key、Timeline、事务 outbox 和本地 worker。
- [x] `R0-10` Decision Desk 最小页面和文本提交入口。
- [x] `R0-11` 本地 TDD/E2E、Runtime adapter、契约、文档和前端构建检查。

### R0 核心已完成

- [x] `R0-12` Provider contract：配置、timeout、bounded retry、错误分类、usage/cost unknown/estimated 和预算 fail-closed。
- [x] `R0-13` Run/Step/Attempt/Lineage/Gate 的规范化 read model 和前端 Run Inspector。
- [x] `R0-14` 固定 PIT fixture、holdout replay、baseline/candidate 独立比较、Brier/net return。
- [x] `R0-15` SQLite backup、restore、integrity、retention 工具和升级迁移；自动 watchdog 保留为后续调度能力。
- [x] `R0-16` ReleaseManifest、failure injection、安装/升级/恢复 runbook 和离线 core acceptance。

### 历史阶段说明

R0/R1/R1-L 已完成既定工程退出门；其 fixed baseline 保留为回放和降级路径，不能因测试通过就把它解释为具备搜索、工具和 replan 的智能体。

### R1-L：Single-Owner Pilot Readiness（离线代码门已通过，等待 Live Pilot Gate）

详见 [R1-L Stage Charter](stages/R1_L_SINGLE_OWNER_PILOT_READINESS.md)。本阶段只收口本机人工决策辅助的启动、配置、安全、通知、恢复和验收边界：

- [x] `R1-L-01` readiness canonical DTO、Pydantic 服务和脱敏检查。
- [x] `R1-L-02` worker `--preflight` 与 `--pilot` 启动门，保留 `--once` 离线兼容。
- [x] `R1-L-03` local/email 通知组合根，复用事务 outbox 和有限重试。
- [x] `R1-L-04` 只读 `/v1/pilot/readiness` API、模块 README 和运维入口。
- [x] `R1-L-05` 离线 pilot acceptance、状态/路线图/CHANGELOG 收口；代码退出门已通过并形成独立提交但尚未推送，真实 Live Pilot Gate 仍需 owner 单独授权。

## R1：Realtime Event Engine

- [x] `R1-01` SourcePlugin registry、cursor、重连、去重、revision 和 source health。
- [x] `R1-02` 官方 Fed/BLS/BEA 日历、RSS/正文和授权范围内的事件来源；固定 parser/fixture 不触网。
- [x] `R1-03` Meeting Copilot/ASR adapter 的文本 fragment/revision 边界；不含音频采集或 ASR 推理。
- [x] `R1-04` OKX 公共行情和事件后执行基准；质量降级而非伪造执行结果。
- [x] `R1-05` scheduler、Outcome 到期标记、失败聚合和 outbox 通知 adapter。
- [x] `R1-06` local 通知 adapter 的有限重试；Email、桌面和 IM provider 仍是后续可替换 adapter。
- [x] `R1-07` `/v1/sources`、`/v1/health`、Decision Desk 来源摘要及来源到 Outcome/outbox 离线 E2E。

R1 开始真实直播监听或外部通知前，必须新增对应 ADR、Provider 授权说明、契约测试和回放样本。

## R2：Decision Workbench 与自主进化

详细边界、契约、DSH 插件桥接、任务依赖、BDD/TDD 验收门和 owner 决策项见 [R2 Stage Charter](stages/R2_DECISION_WORKBENCH_EVOLUTION.md) 及 [ADR-0005](decisions/ADR-0005-dsh-harness-plugin-bridge.md)。当前状态：Workbench/Evaluation/Promotion 工程底座 `done (offline U2)`；真实 DSH Research Runtime、工具循环和主动补证未完成。

- [x] `R2-00` Kernel/Orchestration 边界对齐：移除 Kernel application 对 LangGraph/LangChain 编排细节的直接依赖，保持 R0/R1 行为不变（见 [ADR-0006](decisions/ADR-0006-kernel-orchestration-boundary-alignment.md)）。
- [x] `R2-01` Core MCP、Workbench/Capability contract 与 DSH ResearchMemo adapter；DSH 只能提交研究候选，不能写业务账本或默认发布版本。
- [x] `R2-02` 完整 Run Inspector：证据血缘、步骤、调用、成本、Gate、版本和回放对比。
- [x] `R2-03` Evaluation 数据集、失败样本、反馈、Experience/FailurePattern 和策略实验登记。
- [x] `R2-04` LangGraph Supervisor/Evolution candidate：统一 Runtime contract、有限 replan、replay/holdout/离线 shadow 对照，不改变正式 pointer。
- [x] `R2-05` Promotion Gate、CAS active pointer、原子回滚、版本 registry 和人工 Promotion Desk。

## R2-L：Live Observation Pilot

详细运行拓扑、Job 状态机、触发器、Capability、前端和退出门见 [R2-L Stage Charter](stages/R2_L_LIVE_OBSERVATION_PILOT.md) 与 [ADR-0007](decisions/ADR-0007-live-observation-runtime.md)。这是 R2 的运行化收口，不是 R3。

- [x] `R2-L-00` canonical Evolution Job/heartbeat 契约、0016 migration、Repository/Service。
- [x] `R2-L-01` deterministic trigger、lease/CAS、retry/recovery EvolutionScheduler。
- [x] `R2-L-02` 复用 Supervisor/Evaluation/Evolution 服务生成候选、评测和 pending owner review。
- [x] `R2-L-03` API/realtime/evolution 三逻辑进程、heartbeat、Compose 配置和进程 smoke（本机通过，镜像 registry smoke blocked）。
- [x] `R2-L-04` 审计受控的 SearchCapabilityPort、manifest gate 和 live canary seam（默认关闭）。
- [x] `R2-L-05` Decision Desk Operations/Evolution 人可读视图。
- [x] `R2-L-06` 全量回归、故障恢复、本机 acceptance、runbook 和状态收口。

## R2-R：Agentic Research Runtime（completed / retain baseline）

详细定义、偏差证据、Runtime 选择、双层循环、双 Snapshot、代码结构、BDD/TDD 和退出门见 [R2-R Stage Charter](stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) 与 [ADR-0008](decisions/ADR-0008-agentic-research-runtime.md)。跨领域所有权和交易员 Skill 拆分见 [平台基线](platform/PLATFORM_BASELINE.md)、[ADR-0009](decisions/ADR-0009-product-platform-extension-boundary.md) 与 [Crypto Macro Domain Pack](domains/crypto_macro/README.md)。R2-R-00 至 R2-R-06E 已完成，Runtime 决策为 `retain_baseline`；不切 active pointer，不扩大范围。

- [x] `R2-R-00` canonical agentic research contract、Warsh failure fixture、Pack requirements。
- [x] `R2-R-01` 真实 DSH Python SDK + 受限 `decision-research` profile ResearchHarnessRuntime、Session/Tool/Subagent/Trace adapter。
- [x] `R2-R-02` Web/Official/Market Tool Gateway、EvidenceCandidate 和 Trigger/Decision 双 Snapshot（offline done）。
- [x] `R2-R-03` bounded evidence rounds、Sufficiency/Conflict Gate、CausalCase 与独立 HorizonDecision；已接入独立 `research.v1` candidate path，legacy Fixed baseline 保留。
- [x] `R2-R-04` durable research worker、Run lease/recovery、自动 discovery、scheduled recheck；真实子进程恢复和 single commit 通过。
- [x] `R2-R-05` Research Result/Trace Query/View/SSE、Plan/Evidence/Tool/Sufficiency/Causal/Horizon 人可读前端；四视口与控制台验收通过。
- [x] `R2-R-06` 12 个 PIT 事件 Fixed vs DSH 对比、一个真实事件、失败归档和 Runtime 决策包；结论 `retain_baseline`，owner usefulness 表单待填写，不自动 Promotion。

### R2-R-07：Search Reliability 与 Error Provenance（completed / E2-L passed）

R2-R-06E 的真实 Run 暴露了服务端时间戳、并行工具失败隔离、错误来源保真和失败状态前端投影缺口。G1-A/B/C/D 与 G2-A/B 已完成；详细目标、BDD/TDD 任务卡、停止条件和 G2-C live gate 见 [R2-R-07 阶段卡](stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。

- [x] `R2-R-07A` server-owned PIT time 与兼容投影（G1 offline）
- [x] `R2-R-07B` Provider/Search/MCP/DSH/outer timeout 错误分类和 provenance（G1 offline）
- [x] `R2-R-07C` 并行 capability 单任务隔离与部分结果保留（G1 offline）
- [x] `R2-R-07D` Research View/API/SSE/UI 失败状态投影和幂等 retry/recheck（G1 offline）
- [x] `R2-R-07E` 六类事实 manifest、replay success/stale/provider_failure 与离线回归（G2-A/B）
- [x] `R2-R-07E-live` 正式 DSH Session 已调用 Search/Official/Market；Search timeout 与 stale
  Evidence 按 provenance/Gate 保留，Official/Market 成功 Evidence 不丢失，E2-L 以
  `research_only` 解释性终态通过

G1/G2 代码、离线回归和 E2-L 真实 Web 验收已完成；这证明失败边界和有界补证可用，不
证明 Search 长期稳定或预测价值。E3 前不扩大 capability、不修改 active pointer、不进入 R3。

## G2-AF：主动事实获取与自主研究（G2-AF-01..04 technical gates passed / G2-AF-05 observation）

当前 hard gap 过早停止、Search 未形成可用 fallback、事件调度未与 DSH 主动研究贯通的问题，
统一记录在 [G2-AF 主动事实获取与自主研究阶段方案](stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)。
该阶段是 G2 事实覆盖收口与 G3 prospective value observation 的桥接执行书，不新增第二套
Agent loop：DSH 负责内层 Supervisor，LangGraph 只负责产品生命周期，Hub 负责 Capability
Catalog、PIT、Evidence、Gate、调度、通知和资产。官方 DSH 上游已核实包含原生
`web_search`，且 2026-09-03 native route probe 已通过；后续 DSH native 为 discovery primary，
Tavily Search/Extract 官方 MCP 仅作为按需 fallback/独立交叉索引，Brave/SearXNG 只作为后续回退候选。

- [x] `G2-AF-01` Capability Catalog、六类 requirement fallback、成本/超时/权限/失败码契约
- [x] `G2-AF-02` DSH 原生 Search -> Fetch/typed provider -> Evidence attribution 隔离 canary；Tavily live fallback 单独等待 secret 轮换
- [x] `G2-AF-03` DSH 同 Session 三轮 gap-driven continuation、部分失败保留、synthesis attestation
- [x] `G2-AF-04` 日历/feed admission -> durable Run -> 报告/Outbox/单次通知/复查前瞻链
- [ ] `G2-AF-05` 14 天或 20 个高影响事件 prospective observation，做 promote/retain/stop

最终隔离 canary 已由官方 feed 自动触发并在 DSH 完成 3 轮、20/24 calls、13 条 Evidence、
83.33% hard coverage、Artifact、通知和 child recheck；终态为 `degraded/research_only`。
Tavily 旧 key 未读取或持久化，正式 fallback canary 仍需轮换 secret；Fixed active/DSH
candidate-shadow 不变。G2-AF-05 已统一由
[PD-07 前瞻价值观察阶段卡](stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)承接；下一步只冻结
prospective cohort 并开始真实未来事件观察，不自动开启 R3。

## OBS-01：DSH 技术可观测插件（已通过隔离 canary）

以 DSH 官方 profile seam 接入 `@loongsuite/dsh-plugin@0.1.2`，只投影技术运行 Trace/Metric，
不复制 Hub 业务账本、不采集正文、不启用 LoongSuite Pilot。版本、integrity、运行脚本和
退出证据见 [ADR-0021](decisions/ADR-0021-dsh-observability-plugin.md) 与
[OBS 评估记录](evaluations/DSH_OBSERVABILITY_PLUGIN_ASSESSMENT_2026-09-02.md)。

- [x] `OBS-01` exact-version profile 安装、OTLP payload/Span 结构、`dsh.session.id` 和
  `captureContent=false` canary；exporter 不可用时 DSH/Hub 仍 fail-open。
- [ ] `OBS-02` 可选 TelemetryRef/Query View；必须在不复制 Span 的前提下另立 owner gate。
- [ ] `OBS-03` LoongSuite Pilot 多 Agent 采集；只有第二个真实 Agent 出现后评估，不能与独立
  插件同时作为同一 DSH Trace 的采集主责。

## R3：领域与部署扩展

状态：`blocked by owner gate and follow-up evidence`。R2-R 已完成但 DSH 未通过 Promotion 门；R3 不能在 owner 没有接受新候选前启动。

- [ ] `R3-01` A 股 Domain Pack，复用 Kernel/Run/Evidence/Evaluation，不把 BTC 字段扩散到 Core。
- [ ] `R3-02` 美股/宏观 Domain Pack，单独定义市场时段、执行基准和来源契约。
- [ ] `R3-03` PPT 等非市场产品使用独立 Domain Extension，验证 Kernel 的跨产品复用。
- [ ] `R3-04` 只有出现跨机器高可用、并发写入、远程只读或长期大规模 tick 数据需求时，才评估 PostgreSQL/远程部署。

## PRODUCT-CLOSEOUT-01：DSH Native Trader Pilot

状态：`E1/E2-R/E2-L passed / E3 prospective observation`。DSH Web、Hub 控制面和
`crypto_macro` Domain Pack 已收口为单 owner、单机、只读的 `research_only` 试点。
当前唯一执行入口和停止线见 [产品交付控制书](product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，
正式 Run、后台 child recheck、双前端、截图 hash 和完整质量门见
[E2-L 真实产品验收记录](evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。

- [x] `C1` 统一启动器、Trader 工作区和 DSH/Hub/Desk 双向链接（工程门；产品价值待验收）
- [x] `C2` 正式 `dsh-web` Runtime 选择、readiness、失败/重启语义（工程门；Provider 成功路径待验收）
- [x] `C3` 缺口驱动补证 loop、部分失败保留和人可读停止（工程/replay）
- [x] `C4` Search/Official/Market contract/canary 和正式 DSH Session 解释性闭环；真实 timeout/stale 保留
- [x] `C5` DSH 报告、outbox/dry-run 通知和失败可观测展示（工程/replay）
- [x] `C6` Outcome/Evaluation/Experience 工程资产；14 天/20 事件 E3 观察窗口是唯一下一阶段
- [x] `C7` 单机一键启动、恢复、备份、交付 runbook 和当前 UI 移动/console 资产

## 产品可用阶段

路线图的 `done` 不等于“模型已经赚钱”。当前产品形态按以下门槛解释：

| 状态 | 可交付产品 | 进入条件 | 结论边界 |
|---|---|---|---|
| `U0` | R0 本地文本决策核心 | 已通过 R0 离线验收 | 可用于人工提交文本和复盘，不代表实时来源或收益 |
| `U1` | R1/R1-L 单 owner 试运行 | R1-L 离线门已通过，且 owner 另行通过 Live Pilot Gate | 可有限运行真实来源/通知，仍需观察稳定性和效果 |
| `U2` | R2 Decision Workbench v1 | R2-00 至 R2-05 退出门全部通过，owner Promotion/Rollback 可审计 | Workbench/Evaluation/治理工程闭环；不代表研究智能体或预测优势 |
| `U2.1` | R2-R Research Agent Pilot | R2-R-00 至 R2-R-06 通过且 owner 接受 Runtime 决策；当前工程链已完成，但 DSH 结论为 `retain_baseline`、usefulness 待 owner | 只能继续 Fixed baseline + candidate/shadow 观察，不宣称 DSH active 或盈利 |
| `U2.2` | DSH Native Trader research-only Pilot | E2-L 官方 Web 真实主线、代码 Gate、双前端和后台复查通过 | 可由单 owner 试用并进入 E3；不自动交易，不代表预测或盈利优势 |
| `U3` | 多领域和远程部署 | R2 观察期证明新领域或规模需求，并另立 Charter/ADR | 仅按真实需求扩展，不预建泛化基础设施 |

R2 工程底座通过不等于研究智能体可用。R2-R 已完成验收但 DSH 未通过 Promotion，因此 R3 仍不启动；新领域、公共插件市场、多用户或远程高可用都必须有独立价值证据和新的 Stage Gate。

## 明确暂不做

- 不 clone DSH，不把 DSH session 当业务账本。
- 不把 Pi 作为首版必需运行时；先在 replay/holdout/shadow 中证明优势。
- 不自动交易、不让 Agent 修改 Gate 或自动晋级策略。
- 不在真实授权和数据价值尚未证明前引入 Redis、Kafka、Temporal、DBOS、Kubernetes 或微服务拆分。
- 不把 ASR 评测、新闻抓取或漂亮前端当成文本核心链路已验证的替代物。

## 里程碑验收

| 里程碑 | 必须具备 | 当前 |
|---|---|---|
| `R0-Core` | 文本到 Forecast/Outcome/Evaluation、Gate、账本、回放边界、TDD/SDD | `done` |
| `R0-Release` | Provider contract、backup/recovery、可观测 Run Inspector、ReleaseManifest | `done` |
| `R1-Realtime` | 授权来源、事件调度、行情基准、Outcome 到期和通知 | `done`（离线 fixture） |
| `R2-Workbench` | DSH MCP、完整观测、实验、候选晋级和回滚 | `done`（离线 U2；观察期，未证明真实收益） |
| `R2-R-Agentic` | DSH Harness tool/subagent loop、主动补证、双 Snapshot、充分度 Gate、自动触发和人可读轨迹 | `completed / retain_baseline / owner review pending` |
| `R3-Domains` | 第二领域真实复用和按需远程部署 | `planned` |
