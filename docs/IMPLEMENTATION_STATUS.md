# 实施状态

日期：2026-09-02（Asia/Shanghai）
状态：R0/R1/R2/R2-L、R2-R-00 至 R2-R-06E、DSH-NATIVE-CORE 和
`PRODUCT-CLOSEOUT-01` 的 E1/E2-R/E2-L 已完成工程、replay 和真实官方 Web 验收。当前为
`pilot_ready=true / pilot_usable=research_only`：Fixed 仍 active，DSH 仍是
candidate/shadow，不切 active pointer，不自动交易。该状态只允许进入 E3 前瞻价值观察，
不等于预测准确率、盈利、Search 长期稳定性或 Promotion 已证明。

## 2026-09-02 真实 DSH 运行与 attestation 修复

真实官方 DSH Web Run `run_3c0c64d966aa431da7aecce642694fcf` 已完成 2 轮、12 次工具调用和
10 条有效证据，页面可见原生对话、轨迹和研究报告。此前由工具参数声明 request ID 与底层
call ID 不一致引起的 `dsh_evidence_unattested` 映射缺陷已修复并通过回归；修复详情和真实
运行证据见 [DSH Live Research Attestation 修复记录](evaluations/DSH_LIVE_RESEARCH_ATTESTATION_FIX_2026-09-02.md)。

该 Run 最终因 FRED 日频、事件窗口数据缺失和 Search timeout 被代码 Gate 正确拒绝，状态为
`research_only/degraded`，三个 horizon 为研究性 `no_trade`。这证明“提交任务 -> DSH Agent Loop
主动调用多项研究能力 -> Evidence 落账 -> Gate 裁决 -> 报告投影”链路，不证明方向预测、盈利
或正式交易可用性。全量 Python `392 passed`，Ruff、Pyright、契约、文档和前端检查均已复核。

## E2-L 最终产品验收（2026-09-01）

- 正式 Run `run_04dc1a46fd1e4c3b988750e18b0e9581` 与完整 DSH Session
  `dsh_c73364bc0fd7221f8102fc5181e8c4b17ab3736afc282379e311c67534d4c778` 从官方
  `Crypto Macro Trader` 页面建立并完成 2 轮、12/12 Tool Call、15 条 Evidence、66.7%
  hard coverage 和 `research_only / tool_budget` 代码 Gate。
- `official.macro`、`market.cross_asset`、`market.crypto_derivatives`、`web.search` 经同一个
  Research Gateway 调用；两次 Search timeout 和 FRED stale 如实保留，未变成 `no_trade`
  成功或方向性 Forecast。
- scheduler/worker 自动创建 child Run `run_7df306876d114e09b8e83507d06f3ef0` 并再次完成
  2 轮、12/12 Tool Call，证明后台 durable recheck 不依赖 owner 再次输入。
- DSH 与 Decision Desk 对同一 Run 的 round/tool/Evidence/coverage/failure/Gate 一致；
  375/768/1024/1440 视口无横向溢出，本次 Run 期间 console 无 error。
- 完整工程门：Python `390 passed`、DSH Plugin `53 passed + build`、Decision Desk
  `10 passed + build`、Ruff、Pyright、codegen、module docs、Compose、fresh migration、
  recovery、三类 replay 和 rollback 均通过。证据见
  [E2-L 真实产品验收记录](evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。

## E2L-03 历史实现复核（已由 E2-L 最终验收取代）

本轮只收口 live 时间边界、Evidence-only 安全降级、失败 Run 投影和人可读终态，没有新增 Agent Loop、Provider、数据库或业务能力。

| 边界 | 状态 | 证据 |
|---|---|---|
| MCP durable progress | `done` | `tests/research/test_durable_research_gateway.py`；成功 Evidence/Trace 在返回 DSH 前落库，失败 provenance 保留，取消原样传播 |
| Host model-step watchdog | `done` | DSH plugin 当前 `43 passed`；官方 `step/start`/`step/end`/`turn/end` seam、watchdog cancel、owner cancel 区分 |
| Host terminal callback | `done` | 并发 event/status/result 单 callback、失败后重试、稳定 terminal identity 回归 |
| Query/View partial progress | `done` | 无最终 Result 时从 Trace/Evidence 投影工具数、轮次、证据和逐 capability failure；`tests/research/test_research_observability.py` |
| Desk per-capability error UI | `done` | `ResearchPage.test.tsx` 覆盖 timeout、origin 和 retryability；不展示 raw JSON |
| Live Search/value gate | `pending` | 既有真实 Search timeout 继续保留；typed capability 独立 canary 已通过，仍需正式 DSH Session Evidence/Gate 与浏览器资产后才可进入 E3 |
| Durable DSH deadline/cutoff | `done (offline)` | migration `0026_dsh_session_deadline`；Gateway 读取持久 deadline，模型不能延长 live cutoff，freshness 使用可信 completion/decision cutoff |
| Evidence-only synthesis fallback | `done (offline)` | 可信 Evidence 保留，causal/horizon 全部丢弃，状态 `degraded/research_only`，失败 provenance 持久化，不增加 repair turn |
| Failure Run projection | `done (offline + live failure)` | 无 Result 时从 link/Trace/Evidence 投影 runtime、round、tool count、coverage、failure；真实 Run 副本已复验为 `dsh/R1/10 tools/47 Evidence/critical_data_unavailable`，新正式 Session 的 capability acceptance 仍 pending |

本轮专项质量门：E2L live/cutoff/research/DSH Web/graph/migration 联合测试 `72 passed`；全量离线门 Python `373 passed`、DSH plugin `43 passed`、Decision Desk `10 passed`，Ruff/Pyright/codegen/module docs/frontend build/Compose config/diff check 均通过；三类官方 replay、跨进程恢复、callback/Web restart 与 locked rollback 复验通过。另以真实失败 Run 的只读数据库副本复验了 Query/View 投影一致性。该结果不证明真实 Search 稳定性、事实充分度、预测准确率或盈利。

当前唯一任务入口为 [产品交付控制书与最终验收包](product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)。E2-L 已通过，旧 E2L-A..G 实施任务全部转为历史证据；后续只允许执行 E3 前瞻观察，不得新增第二套 Agent Loop、Provider 协议栈、页面框架或领域功能。

跨文档执行总表见 [产品执行与最终验收总表](product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)。它只汇总已接受事实、阶段 checklist 和停止门，不新增运行时或改变任何历史账本。

2026-08-31 交互误用修复已完成并通过真实浏览器复验：replay 页面显示只读提示并锁定新建/继续输入；`run-live-web.sh` 提供无 replay patch 的 Provider 预检入口。新实例端口 `64619` 的截图和 hash、测试结果见 [DSH 交互运行态与插件生态审计](evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md)。旧 `65349` 进程不代表新 bundle；实时 Search 仍未验证。

2026-08-31 新增 [产品实现与最终验收方案](product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)，把 `PRODUCT-CLOSEOUT-01` 的 C1-C7、目标代码结构、DSH/Hub/插件边界、SDD/BDD/TDD/ADR、上下文压缩和最终工程/产品/价值验收门收敛为详细执行 checklist。2026-09-01 起 [最终产品交付实施书](product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md) 是唯一顶层交付控制书。`run-product.sh` 不再把宿主机绝对 replay 路径传入容器；Compose 容器使用自身默认 fixture，重新 build 仍需外部 registry 可用后取得证据。

最后复核：2026-09-01。R2 的 canonical codegen、官方 MCP transport、Run Inspector、评测/经验资产、候选 Runtime seam、Promotion/Rollback 和 Decision Desk 已通过离线退出门；最新又验证了官方 Workspace 可见受管 Session、同 Session generation 2、canonical 人可读报告、三场景 replay、恢复/回滚和桌面浏览器证据。上述证据不证明 DSH 已晋级 active、真实网络长期稳定、预测准确率或盈利能力。

## 治理状态

- 项目宪章：`accepted`，见 `docs/engineering/PROJECT_CHARTER.md`。
- 全局开发治理：`accepted`，见 `docs/engineering/DEVELOPMENT_GOVERNANCE.md`。
- 最近完成 Stage Charter：`R1 Realtime Event Engine`，`done`，见 `docs/stages/R1_REALTIME_EVENT_ENGINE.md`；可插拔边界见 `docs/decisions/ADR-0003-r1-realtime-plugin-boundary.md`。
- `R0-B1 ProviderConfig + capability manifest`：`done`；R0-B Provider Reliability Boundary 整体已完成。
- `R0-CORE-COMPLETE`：`done`；证据入口为 `tools/core_acceptance.py` 和 `docs/RELEASE_MANIFEST.json`。
- `R1-L-SINGLE-OWNER-PILOT-READINESS`：`done (offline)`；Stage Charter 见 `docs/stages/R1_L_SINGLE_OWNER_PILOT_READINESS.md`。readiness 契约、控制层、API 只读入口、worker 预检/启动门、通知组合根和离线 acceptance 均已通过；真实 Live Pilot Gate 仍未授权。
- Run/Step/Attempt/Call normalized projection + Run Inspector API：`done`，新 Run 可查询四个 Step、三角色 Call、错误/成本/重试和 `/v1/runs/{run_id}/inspector`。
- SQLite backup/integrity 和固定 PIT Replay：`done`，已验证固定 clock、future-information reject、baseline/candidate、Outcome/Brier/net return、restore replay smoke。
- 当前阶段：[R2-R Agentic Research Runtime](stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) 与 [ADR-0008](decisions/ADR-0008-agentic-research-runtime.md) 已完成；`R2-R-00` 至 `R2-R-06E done`，Runtime 决策为 `retain_baseline / pending_owner_review`，Fixed active、DSH candidate/shadow。
- 通用平台边界：[平台基线](platform/PLATFORM_BASELINE.md)、[资产与扩展模型](platform/ASSET_AND_EXTENSION_MODEL.md)、[Crypto Macro Domain Pack](domains/crypto_macro/README.md) 与 [ADR-0009](decisions/ADR-0009-product-platform-extension-boundary.md) 已接受；它们授权渐进边界落地，不授权全仓重构或第二领域。
- 主体产品规格：[研究智能体主体产品规格](product/RESEARCH_AGENT_PRODUCT_SPEC.md) 已接受；durable research、补证循环、Command Center/报告/SSE 和自动 discovery/admission 已完成 candidate 路径，真实事件 Search reliability 仍未达到 Promotion 门；ASR 输入仍只保留适配器边界。
- 产品重新收口：`DSH-first / accepted`；DSH 负责唯一研究执行 Harness，Decision Hub 保留 Trigger/Durable Run、Evidence/PIT/Gate/Ledger/Outcome，当前先完成原生 Web/插件/耐久桥接和 replay E2E。详见 [DSH-NATIVE-CORE](stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)。
- DSH-NATIVE-CORE 为 `engineering acceptance complete / product value pending`：NATIVE-00 至 NATIVE-05、NC-01 至 NC-07 均有固定上游、官方插件、durable bridge、三场景官方 Web、callback recovery、Web restart、版本 fail-closed、locked rollback 和浏览器证据。失败样本继续保留；完整证据见 [Replay 与恢复验收记录](evaluations/DSH_NATIVE_CORE_REPLAY_MATRIX_2026-08-31.md)。不得把工程完成解释为 `product_ready`、live 稳定、预测准确或盈利。
- `PRODUCT-CLOSEOUT-01`：`E1/E2-R/E2-L passed / E3 prospective observation`，Stage Charter 见 [DSH Native Trader Pilot](stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)，当前执行入口见 [产品交付控制书](product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)。官方 DSH Workspace/Session/Trajectory、同 Session 主动补证、真实能力、代码 Gate、双前端一致性、后台自动复查、恢复和完整质量门已完成；Search timeout 与 stale Evidence 作为真实限制保留。不得宣称预测准确率、盈利、自动交易或 DSH active Promotion。证据见 [E2-L 真实产品验收记录](evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。

## R2-R 前历史基线问题（已由 candidate 路径覆盖，仍保留作回归证据）

2026-08-29 Warsh 讲话 Run `run_78b7664c884e4dc6a36fc77454a6e47c` 使用真实 Provider 运行约 93.7 秒，最终 30m/24h/72h 产生完全相同的 `no_trade / 52% / Trigger / Invalidation`。模型列出了 DXY、2Y/10Y、FedWatch、BTC funding/OI/基差/清算等缺口，但正式 graph 没有工具节点、真实 Search/Market binding、Evidence Sufficiency 路由或 continuation，因此无法补证。

当前能力必须准确解释为：

- `done`：账本、PIT、固定 Graph、Provider contract、Gate、Forecast/Outcome/Evaluation、Workbench/Evolution、Operations 骨架。
- `candidate / not active`：`/v1/research/observations -> durable research worker -> bounded LangGraph -> DSH/replay ResearchHarnessRuntime -> Gate/账本` 已接通；legacy `/v1/observations` 仍是 Fixed baseline，active pointer 未切换。
- `not implemented / not verified`：默认真实网络长期稳定性、预测准确率、盈利能力和 DSH Promotion。Research Query/View/SSE、人可读前端、12-case 对照和一个真实事件 Runtime 决策包已完成；真实事件 Search 失败和追加对照失败样本均保留，DSH 仍不等于 active runtime。

因此当前结果可作为单 owner、单机、只读的 `research_only` 市场研究试点使用，但不能
宣传为已证明预测优势、盈利能力或自动交易能力的正式交易产品。

## DSH-NATIVE-CORE 工程完成记录

本阶段唯一目标是固定官方 DSH Web 主壳，完成官方 Host/Client plugin、durable Run/Session bridge、三场景 replay、重启恢复、升级回滚和浏览器验收。Hub 不重新实现 Agent Loop；DSH Session/JSONL 不代替 Hub 业务账本；Fixed baseline 继续 active，DSH 保持 candidate/shadow。

| 卡片 | 状态 | 当前证据边界 |
|---|---|---|
| `NC-01` 文档/契约一致性 | `verified` | 本状态、交接、路线图、代码地图和模块 README 已同步本方案与当前编号；未授权切 active pointer |
| `NC-02` Host terminal 幂等 | `verified` | in-flight 协调、并发单 callback 和失败后重放测试通过 |
| `NC-03` 取消/错误 provenance | `verified` | 取消、timeout、Provider 错误分类回归通过；继续纳入全量门 |
| `NC-04` authority floor canonical 化 | `verified` | canonical schema、codegen、Sufficiency 和 authority floor 回归通过 |
| `NC-05` 三类 replay 产品验收 | `verified` | success/partial_failure/insufficient_or_stale 均通过官方 Web -> MCP -> JSONL -> Hub Gate/Ledger；历史 NC revision 的桌面/移动/console 已归档，最新报告 revision 另有桌面截图/hash，移动/console pending |
| `NC-06` 重启/丢回调/升级/回滚 | `verified` | callback gap、Web restart、multi-round continuation、version mismatch 和 locked rollback 已通过 |
| `NC-07` 完整质量门 | `verified` | 最新为 343 Python tests、34 DSH plugin tests、9 Decision Desk tests、静态、契约、Compose 配置、三场景和 recovery 均通过 |

本轮执行顺序已完成 `NC-01 -> NC-05 -> NC-06 -> NC-07`、E2-R 和 E2-L 官方 Web 产品验收。下一步不得继续追加工程功能，只能进入 E3 前瞻观察，或保持 `retain_baseline`/停止。

## R2-R Research Agent Pilot

| Task | 状态 | 已交付与证据 |
|---|---|---|
| `R2-R-00` | done | `agentic_research.schema.yaml` canonical contract 与 Python/TS/Zod 公共导出；`packs/crypto_macro/` 的 Doctrine/Evidence/Profile/Tool/Gate/Eval；Warsh failure fixture；契约与架构测试；全仓 170 passed、Ruff/Pyright/codegen/docs/前端 test+build/diff check 通过 |
| `R2-R-01` | done | 官方 Python SDK `0.1.1rc1` + bundled runtime `0.0.1`、restricted `decision-research.v1:1d4ce1f40ab265e4` profile、Session/Tool/Subagent/Trace/result adapter；local handshake 与真实 gpt-5.5 canary 通过（3 tool calls/results、1 subagent、2 turns、4 steps、Session 落盘）；全仓 187 passed 与全部静态/前端门通过 |
| `R2-R-02` | done (offline) | Web/Official/Market Tool Gateway、EvidenceCandidate lineage、Trigger/Decision 双 Snapshot；外部能力继续 deny-by-default，PIT/鲜度/authority/hash/fallback/replay 专项通过；Decision Snapshot 现在保留 parent Trigger evidence |
| `R2-R-03` | done (candidate path) | `decision/sufficiency.py` 确定性 freshness/authority/independence/conflict Gate、LangGraph bounded evidence rounds、同一 session continuation、no-progress/budget stop、JSON checkpoint state、horizon distinctness/cutoff fail-closed；通过独立 `research.v1` API/worker 候选链进入既有 Gate/账本，未替换 legacy Fixed baseline |
| `R2-R-04` | done (offline/local process) | `DurableResearchWorker`、`--role research`、Compose research-mcp、DSH/replay runtime、Run lease/CAS、自动 discovery、Run-scoped Snapshot/Evidence identity、Trigger 后证据、scheduled child Run recheck、真实子进程 checkpoint recovery 和幂等 commit；`tools/research_acceptance.py` 24 passed，全量 Python 243 passed，Alembic head `0019` |
| `R2-R-05` | done | migration `0020`；规范化 ResearchResult/ResearchTrace、Query/View/增量 SSE、owner command、Command Center、详情与报告；Result/Artifact 事务一致性和取消后禁止提交；前端 8 passed/build，浏览器 375/768/1024/1440 无横向溢出且控制台无错误 |
| `R2-R-06` | done (`06A-06E`, retain baseline) | 12-case Fixed vs DSH、Warsh 真实事件 durable Run 和 Runtime 决策包均已完成；DSH 追加批次仍有 `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout`，真实 Search 以 `dsh_tool_failed/research_capability_failed` fail-closed。Owner usefulness 待填写；active pointer 保持 Fixed，不宣称盈利 |

### R2-R-07（completed / E2-L passed）

R2-R-06E 后的隔离 live Run `run_488b389ad8674dcfb632d12ea7b2399c` 曾复现：模型提交 `observed_at == cutoff_at`，晚到结果被 PIT 正确拒绝但上层粗略归类为 `provider_timeout`；首个并行 MCP 失败会 abort 其他调用；前端可能无法显示终态 stop/error。G1-A/B/C/D 已修复这些边界，详见 [R2-R-07 阶段卡](stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。

当前状态：`G1/G2 engineering complete / E2-L research_only passed / E3 observation next`。
真实 Search timeout 仍作为失败样本保留；不扩大 capability/network，不切 active pointer，
不改写历史数据。

G1 可靠性和 G2-A/B 事实包/回放已经是当前代码事实，不再标记为 implementation pending：

| 目标 | 状态 | 说明 |
|---|---|---|
| G1-A/B/C/D | `done (offline)` | Server-owned PIT、错误 provenance、并行部分成功保留、失败 Run API/UI 投影均已实现并回归 |
| G2-A | `done (offline)` | `CryptoMacroFactPack` 与 source manifest 固定六类事实、优先级、鲜度和回退 |
| G2-B | `done (replay)` | 六类事实各有 success/stale/provider_failure fixture，统一 canonical contract |
| G2-C | `failed safely` | 只读、allowlist、临时目录、单次限时真实 Search 已执行；`research_capability_timeout`，无 Evidence/active pointer 变化 |
| G2-D | `E2-L passed / research_only` | 正式 DSH Session 完成 Search/Official/Market 主线；timeout/stale 与 66.7% hard coverage 由代码 Gate 解释性停止，不伪装 no_trade |

`R2-R-06C` 前三次同 case 真实 Canary 作为失败证据保留；第四次
`r2-r-06c-20260830-low-profile-canary` 已完成：DSH 56.5 秒、2 条 attested Evidence、
hard coverage 33.3%、PIT/unattested 均为 0、三个 Horizon distinct，且未登记资产或
修改 active pointer。`r2-r-06c-20260830-repair-full-12case` 追加批次中 Fixed 为
12/12，DSH 为 9/12；三项失败分别为 `dsh_evidence_unattested`、
`dsh_session_incomplete`、`provider_timeout`，PIT violations 为 0 但 unattested 为 1。
replay Horizon 已相对 PIT cutoff 校验，Round 工具轨迹已从可信 Trace/MCP Result
投影。unattended profile 为 `decision-research.v1:faf1b3115f7d339c`；最新 39 个
DSH/Graph/contract/eval 专项测试、codegen、13 个模块文档、Ruff、Pyright 0 errors
和 diff check 通过，12-case 门禁已放行但尚不能 Promotion。06D/06E 已完成；下一步不是继续堆功能，而是等待 owner usefulness 复核，并由 owner 决定是否授权只聚焦 Search reliability/error provenance 的新候选。

## R2-L Live Observation Pilot

Stage Charter：[`R2_L_LIVE_OBSERVATION_PILOT.md`](stages/R2_L_LIVE_OBSERVATION_PILOT.md)，ADR：[`ADR-0007`](decisions/ADR-0007-live-observation-runtime.md)。当前状态：`done (offline + local process acceptance) / observation`。

| Task | 状态 | 已交付与证据 |
|---|---|---|
| `R2-L-00` | done | `evolution-job.v1` canonical schema、Python/TS/Zod codegen、Alembic `0016`、Evolution Job/heartbeat service |
| `R2-L-01` | done | trigger scanner、幂等 `trigger_key`、CAS claim、lease renew/recovery、retry/permanent error policy；`tests/evolution/test_live_observation_jobs.py` |
| `R2-L-02` | done | LangGraph Supervisor/Evaluation/EvolutionAsset 组装、immutable candidate artifact、replay/holdout/shadow、重启复用资产；`tests/evolution/test_evolution_executor.py` |
| `R2-L-03` | done (local) | realtime/evolution role、API/worker heartbeat、独立 tick、Compose 配置；`tests/evolution/test_worker_processes.py` 与 acceptance |
| `R2-L-04` | done (offline seam) | SearchCapabilityPort、manifest/权限/域/预算/PIT Gate、fake 和 OpenAI-compatible canonical adapter；`tests/capabilities` |
| `R2-L-05` | done | `/v1/operations`、`/v1/evolution/jobs`、Operations/Evolution 人可读页面、无 fallback；`tests/operations`、Vitest、Vite build、浏览器检查 |
| `R2-L-06` | done (offline/local) | `tools/live_observation_acceptance.py` 34 passed、runbook、状态文档和全量回归；Compose 真实镜像运行受外部 registry timeout 阻塞 |

本阶段能证明：单机可持续运行的三逻辑进程边界、耐久化演进任务、可恢复 lease、受控搜索出口和人可读观测已经组成一个可观察产品骨架。仍不能证明真实 Provider/source/search/market/notification 长期稳定、预测准确率、盈利、生产高可用、自动交易或自动 Promotion。

## R2 实施状态

- Stage Charter：[R2 Decision Workbench 与自主进化](stages/R2_DECISION_WORKBENCH_EVOLUTION.md)，状态 `done (offline U2) / observation`；当前停止扩张，不自动进入 R3。
- [ADR-0005 DSH Harness 与插件生态桥接边界](decisions/ADR-0005-dsh-harness-plugin-bridge.md) 和 [ADR-0006 Kernel/Orchestration 边界对齐](decisions/ADR-0006-kernel-orchestration-boundary-alignment.md) 已接受；尚未接入任意 DSH 社区插件。
- 已交付范围：Core MCP/DSH ResearchMemo、统一 Query/View、Evaluation Dataset/FailurePattern/Experience、baseline/candidate replay/holdout/离线 shadow、Pi/DSH candidate adapter seam、Version Registry、owner Promotion/Rollback 和资产目录；adapter seam 不等于真实 Harness 集成。
- `R2-00 done`：新增 `DecisionWorkflowExecutor` Port 和 `LangGraphDecisionExecutor`/composition root；Kernel 不再直接导入 LangGraph/LangChain，PIT、Gate、账本、checkpoint/recovery 和 API 行为保持不变。
- `R2-01 done`：canonical YAML 通过真实 codegen 生成 Python/TypeScript/Zod；Core MCP 使用官方 SDK，stdio 与 streamable HTTP 客户端均通过真实握手、发现和结构化查询；Capability 默认拒绝并要求 owner audit、schema、timeout、权限和 executor。
- `R2-02 done`：Run Inspector 和 Decision Desk 展示 PIT 证据血缘、版本、Step/Call、Gate、实验、资产和 Promotion；375/768/1024/1440 视口均无横向溢出，默认不显示 raw provider/graph JSON。
- `R2-03 done`：不可变 Dataset manifest、fixture hash、PIT/未来标签泄漏拒绝、raw artifact 先保存后评分、FailurePattern 和 Experience 均可追溯。
- `R2-04 done`：Evolution candidate Supervisor 复用 LangGraph `StateGraph`/`Send`/checkpointer，specialist 结构化失败、最多一次 replan 且只补缺失 capability；Pi/DSH 通用 callable seam 通过统一 contract suite。它没有接入正式 decision graph，也没有证明 DSH SDK、工具或 subagent 可运行。
- `R2-05 done`：Promotion Gate、owner-only command、CAS active pointer、原子 Promotion/Rollback、并发单赢家、事务故障回滚和审计历史已通过测试与浏览器交互验收。

## R1 已交付与退出门

| 能力 | 当前实现 | 当前证据与边界 |
|---|---|---|
| 来源接入 | `SourceConnector`、`SourceManifest`、registry；RSS/Atom/JSON/iCalendar 和转写 fragment 统一产出 `TextEnvelope` | fixture 覆盖 cursor、duplicate、revision、429/解析失败与 PIT；真实来源仅 opt-in，不宣称稳定性 |
| Durable source state | Kernel 持有 `source_states` 的 cursor、health、backoff、`next_poll_at` | `0008`/`0010` 与 `tests/migrations/test_upgrade_paths.py` 覆盖历史升级；registry 不持有业务状态 |
| 市场与到期评估 | `MarketDataPort`/OKX public adapter、`DueOutcomeService` | bid/ask、VWAP fallback、unavailable/estimated 与 Outcome 幂等使用 fixture；不宣称实际执行质量 |
| 调度与恢复 | `RealtimeScheduler` 轮询来源并从 durable admitted Run 恢复 Graph 执行 | scheduler 不创建第二队列，不绕过 R0 LangGraph/Gate/账本 |
| 通知 | 已提交 outbox 的 local JSONL adapter 与有限重试 | 通知失败不重新分析；外部 Email/IM 仍未接入 |
| 产品可观测性 | `/v1/sources`、`/v1/health`、Decision Desk 来源健康摘要 | UI 只显示人可读状态，不展示原始 Provider/adapter JSON |

R1 退出门已完成：`R1-01` 至 `R1-07` 均有可回滚提交、固定 fixture 覆盖来源失败/游标/修订/PIT/到期/通知重试，且全量质量门通过。R0 `ReleaseManifest` 保持历史 R0 证据，不被 R1 工作树改写；fixture 成功不表述为真实数据或收益结论。

## 已完成并自测

| 能力 | 当前实现 | 证据 |
|---|---|---|
| 文本入口 | `ObservationCreate -> TextEnvelope`，content hash 去重 | `tests/contracts`, `tests/e2e` |
| 账本 | SQLite WAL + Alembic `0001` 至 `0023`，Event/Observation/Run/Snapshot/Artifact/Forecast/Outcome/Evaluation/Outbox/Step/Call、R2 Workbench/Evolution 资产、R2-L Job/heartbeat、R2-R Run lease、scheduled child Run、Research Result/Trace/Command、DSH Session link/prompt；业务状态与 checkpoint 分离 | `tests/kernel`, `tests/e2e`, `tests/migrations`, `tests/workbench`, `tests/evolution`, `tests/research`, `tests/dsh_native`, fresh migration/SQLite PRAGMA |
| PIT | `SnapshotService.freeze()` 保存 cutoff、证据 hash 和不可变 snapshot | `tests/kernel/test_core_flow.py` |
| Agent 编排 | 已实现 LangGraph fixed baseline、独立 DSH `ResearchHarnessRuntime` candidate、bounded evidence-round graph 和 durable research worker；API 默认仍使用 fixed baseline，外部 Web/Official/Market 仍 deny-by-default | R2-R-03/04 离线测试证明边界与投影，不证明默认主链、真实搜索稳定性、预测质量或收益 |
| Gate | facts/citations/counter-thesis/action fields/probability cap 的确定性检查 | `test_gate_fails_closed_without_evidence` |
| Outcome/Evaluation | 手工结果录入、费用/滑点扣除、Brier score 和 evaluation query | `tests/e2e/test_api_flow.py` |
| API | `/v1` REST、异步 202、Idempotency-Key、Query/View DTO、timeline、health | live curl smoke + API tests |
| 前端 | React/Vite/TanStack Query/Lucide；Inbox/Health/Forecast coverage/Assets/Run drawer；真实文本提交对话框；桌面/移动响应式 | `pnpm build`, Vitest, browser DOM/screenshot/submit smoke |
| 前端契约 | `@decision-hub/contracts-ts` workspace 包提供 Zod 运行时校验，Decision Desk 只 re-export | TypeScript build + client fixture test |
| 契约同步 | canonical schema hash manifest 检查；schema 改动会使 codegen check 失败 | `tools.contract_codegen generate/check` |
| ASR 位置 | `TranscriptSourceAdapter` 和 Meeting Copilot fragment 边界，只接收/产生 `TextEnvelope` | `tests/sources/test_transcript_adapter.py` |
| Graph checkpoint | `langgraph-checkpoint-sqlite` 独立 checkpoint store + RecoveryWatchdog 恢复边界 | `tests/replay/test_checkpoint.py`, `tests/e2e/test_runtime_safety.py` |
| 发布 outbox | Artifact 与 `OutboxRecord` 同一事务提交；`hub-worker --once` 写本地 JSONL 并按 dedupe key 标记完成 | `test_outbox_worker.py` + fresh migration smoke |

## R1-L 进入 R2 前收口

| 能力 | 当前实现 | 证据与边界 |
|---|---|---|
| readiness | `packages/pilot_runtime` 聚合数据库、迁移、Provider、来源、市场、通知和自动交易禁用检查，输出 `pilot-readiness.v1` 脱敏报告 | `tests/pilot/test_readiness.py`；不触网、不调用 LLM/SMTP |
| worker 启动门 | `hub-worker --preflight` 输出报告并以非零退出表示未就绪；`--pilot` 在 scheduler 前 fail-closed | `apps/hub_worker/README.md`、`tools/pilot_acceptance.py`；`--once` fixture 兼容保留 |
| 通知组合根 | local JSONL 或显式 email SMTP adapter，均消费 committed outbox；复用现有 bounded retry/dedupe | `packages/pilot_runtime/bootstrap.py`、`tests/providers`；不重新分析、不改账本 |
| API readiness | 只读 `/v1/pilot/readiness`，仅返回 canonical Pydantic DTO，不返回密钥、密码、原始 provider JSON | `tests/pilot/test_entrypoints.py` |

以上条目是离线实现证据，不等于真实来源、真实 SMTP、长期运行、预测准确率或盈利验证；这些需要单独的 Live Pilot Gate。

## 尚未声称完成

- 外部 LLM live canary 已能通过 `gpt-5.5` Responses 路径完成三角色调用、结构化解析和 Artifact/Forecast 持久化；这只是 Provider 兼容性证据，不代表预测准确率或盈利能力。默认 CI 继续使用 fake/replay 保持确定性。
- 真实 Email/IM 通知、默认真实 Provider 运行、长期后台进程稳定性和真实来源的授权/限流协议仍未验收；本地 worker 与 local JSONL 只用于单机/fixture 证据。
- 直播音频 capture、ASR 推理、OCR、未授权新闻抓取、自动交易和第二领域仍未接入。
- 真实长期样本量、生产 shadow 优势和满足生产 Promotion 阈值的候选。
- 自动化 Playwright 视觉回归仍未建立；本轮已人工验证 375/768/1024/1440 四个视口及完整 Promote 交互。
- R2-L 已完成离线和本机三进程 acceptance；R2-R 新增 `tools/research_acceptance.py` 离线边界。2026-09-01 的 DSH-native Compose 实例已完成运行态验证；生产多主机部署仍需独立 ADR。
这些是观察期或新 Stage Gate 的工作，不改变 R0 文本核心和 ASR 适配器边界；不能把有限 fixture 结果宣传为市场收益。

## 本地验证命令

```bash
./.venv/bin/python tools/core_acceptance.py
```

Inspector 证据归一化已由研究图汇合边界保证：Facts/Citations 取自 policy reviewer 的结构化输出，synthesis 上下文不会作为原始 JSON 写入 Artifact；`tests/e2e/test_api_flow.py` 对此有回归断言。

## TDD/SDD 与本次自测记录

长期规范见 [`docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md`](engineering/TDD_SDD_SELF_TEST_STANDARD.md)。当前测试已覆盖从文本输入到 Evaluation 的可执行链：文本哈希、Event/Observation/Snapshot、LangGraph research、Gate、Artifact、30m/24h/72h Forecast、Outcome、Brier/net return、Query View、Timeline、Step/Attempt/Call、Provider failure safety、checkpoint recovery、backup/restore 和 Outbox。

2026-08-31 DSH-NATIVE-CORE contract/offline 收口后的质量门结果：

```text
Python pytest (not live): 329 passed
Ruff: passed
Pyright: 0 errors, 0 warnings
Canonical schema check: passed
Module documentation check: passed
Frontend Vitest: 9 passed
Frontend Vite build: passed
Research acceptance: 28 passed；真实 Python 子进程 checkpoint recovery/second no_run 通过
G1/G2 targeted tests: research/capabilities/runtime/API/observability/migrations 全部通过
Migrations: fresh Alembic 到 `0023_dsh_session_prompts`
Core MCP: 官方 stdio 与 streamable HTTP client 验收通过
Browser: 375/768/1024/1440 无横向溢出；人工 Promote/审计历史通过
Core acceptance: passed（含 durability/replay、backup/restore/integrity、secret scan、前端 Vitest/Vite）
Pilot acceptance: passed（readiness、worker preflight fail-closed、notification composition、入口 API、PIT/recovery/backup 复用检查）
Live observation acceptance: passed（43 tests；Evolution Job/lease、四逻辑进程 heartbeat、Search Gate、Operations API/UI、本机恢复）
Compose: `docker compose config --quiet` passed（含 `research-mcp` 与 `hub-research-worker`）；`docker compose build` blocked by external registry timeout，未取得镜像运行/heartbeat 证据
G2-C live Search canary: pending owner confirmation；普通 CI 未访问网络
DSH-NATIVE-CORE: NATIVE-00..05、NC-01..07 engineering acceptance complete；NATIVE-06 live/value Gate pending owner authorization
```

前端到后台 smoke 证据见 [`G1/G2 前端运行链记录`](evaluations/G1_G2_FRONTEND_RUNTIME_SMOKE_2026-08-30.md)：隔离 API `8030`、replay Research worker 和 Vite `5175` 共用同一临时目录，页面提交后得到 `research_only`、17% hard coverage、5 个 hard gap、12 条 SSE；页面明确显示 `replay`，Artifact 三个 Forecast 均为 `no_trade`。本轮曾观察到并发首次 Alembic 迁移竞态，已记录启动顺序约束和后续 migration lock 任务；不把顺序重试当成并发启动可靠性证明。

外部模型 canary 使用 `tools/canary/run_live_text_canary.py`，只读当前进程环境变量，使用临时 SQLite，输出脱敏 ID/状态/hash，不把密钥写入仓库或数据目录。本次使用 `https://codexai.club/v1` 与 `gpt-5.5` 做了分层探测：

- `/v1/models` 返回模型列表，包含 `gpt-5.5`。
- 直接 `/v1/chat/completions` 的普通文本请求和 JSON Schema 请求均返回 `200`。
- 直接 `/v1/responses` 的普通文本请求和 `text.format` JSON Schema 请求均返回 `200`。
- 之前强制 Chat + 非严格 Pydantic schema 时，LangGraph 结构化 Agent 出现超时/502；根因是协议选择和 schema 形状不匹配。Runtime 现默认使用 Responses，并提供 `DECISION_HUB_LLM_API_MODE=chat` 显式回退。
- 修正后完整 LangGraph canary 成功完成三次角色调用、结构化解析、Artifact 和三个 Forecast；本次结果为 `status=degraded`、`gate_status=research_only`，原因是模型选择了 `no_trade`，不是 Provider 协议失败。

因此当前结论是“Provider 的 Chat 和 Responses 基础接口均可用，Decision Hub 三角色结构化 Agent 的 Responses 路径已通过兼容性验证，但业务 Gate 仍正确拒绝无方向候选”。这不代表预测准确率、盈利能力或生产稳定性已证明；准确性仍需时间切分回放和 holdout 评测，正常 CI 仍禁止触网。
