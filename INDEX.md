# Decision Hub 工程入口

项目简介见 [README](README.md)，阶段执行状态见 [ROADMAP](docs/ROADMAP.md)，逐任务实施约束见 [EXECUTION_PLAN](docs/EXECUTION_PLAN.md)。

## 当前状态

Decision Hub 已完成 R0/R1/R2/R2-L、R2-R-00 至 R2-R-06E、[DSH-NATIVE-CORE](docs/stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 和 [PRODUCT-CLOSEOUT-01](docs/stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md) 的 E1/E2-R/E2-L 验收。官方 DSH Web 已从真实页面完成两轮主动补证、可信账本、代码 Gate、双前端一致性和后台自动复查；当前可作为单 owner、单机、只读的 `research_only` 试用入口。Fixed 仍 active，DSH 仍是 candidate/shadow；这不代表预测准确、盈利、自动交易或 Promotion。下一阶段只做 E3 前瞻价值观察。

当前唯一执行和收口入口是 [产品交付控制书与最终验收包](docs/product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，真实证据见 [E2-L 官方 DSH Web 验收记录](docs/evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。[Pilot Ready 最终执行与验收书 v2](docs/product/PRODUCT_PILOT_READY_FINAL_EXECUTION_PLAN_2026-09-01.md) 与 [v1 最终产品收口实施任务书](docs/product/PRODUCT_CLOSEOUT_FINAL_IMPLEMENTATION_TASKBOOK_2026-09-01.md) 已完成其实施用途，只保留为历史任务和反例证据，不能再作为新开发入口。

## 长任务快速恢复

开发者若不清楚 DSH 与外层 Decision Hub 的边界，先读 [DSH 与 Decision Hub 边界和代码地图](docs/product/DSH_AND_HUB_BOUNDARY_GUIDE.md)，再按下方事实源继续恢复。

Agent 或新会话先读 [当前状态短上下文](docs/context/CURRENT_STATE.md) 和 [当前有效决策索引](docs/context/CURRENT_DECISIONS.md)，再读当前 Stage Charter、相关 ADR/schema、模块 README 和测试。[交接协议](docs/context/HANDOFF.md) 规定开始、停止和收尾动作。`docs/context/` 只是当前事实投影，不替代下面的权威真源。

## 唯一有效事实源

1. [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
2. [执行路线图](docs/ROADMAP.md)
3. [分阶段执行设计](docs/EXECUTION_PLAN.md)
4. [ADR 目录](docs/decisions/README.md)
5. [模块地图](docs/modules/README.md)
6. [项目宪章](docs/engineering/PROJECT_CHARTER.md)
7. [全局开发治理规范](docs/engineering/DEVELOPMENT_GOVERNANCE.md)
8. [已完成阶段记录](docs/stages/) 与对应 accepted ADR
9. [ADR-0005 DSH Harness 与插件生态桥接边界](docs/decisions/ADR-0005-dsh-harness-plugin-bridge.md)（已接受）
10. [R0 Core Completion 实现方案](docs/stages/R0_CORE_COMPLETION_PLAN.md) 和 [R0-B 历史阶段记录](docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)
11. [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
12. [R2 Decision Workbench 与自主进化 Stage Charter](docs/stages/R2_DECISION_WORKBENCH_EVOLUTION.md)（R2-00 至 R2-05 离线 U2 退出门已完成，观察期）
13. [R2-L Live Observation Pilot Stage Charter](docs/stages/R2_L_LIVE_OBSERVATION_PILOT.md) 与 [ADR-0007](docs/decisions/ADR-0007-live-observation-runtime.md)
14. [R2-R Agentic Research Runtime Stage Charter](docs/stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) 与 [ADR-0008](docs/decisions/ADR-0008-agentic-research-runtime.md)（`completed / retain baseline`）
15. [R2-R-06 研究智能体价值验收执行方案](docs/stages/R2_R_VALUE_ACCEPTANCE.md) 与 [06E Runtime 决策包](docs/evaluations/R2-R-06E_RUNTIME_DECISION.md)（`retain_baseline / owner review pending`）
16. [通用产品平台基线](docs/platform/PLATFORM_BASELINE.md)、[资产与扩展模型](docs/platform/ASSET_AND_EXTENSION_MODEL.md) 与 [ADR-0009](docs/decisions/ADR-0009-product-platform-extension-boundary.md)（`accepted`）
17. [产品规格索引](docs/product/README.md) 与 [研究智能体主体产品规格](docs/product/RESEARCH_AGENT_PRODUCT_SPEC.md)（`accepted`）
18. [领域文档索引](docs/domains/README.md) 与 [Crypto Macro Domain Pack 设计](docs/domains/crypto_macro/README.md)（accepted，R2-R-06E 已完成）
19. [ADR-0010 模型研究语义与可信运行账本边界](docs/decisions/ADR-0010-model-semantics-runtime-ledger-boundary.md)（`accepted`）
20. [ADR-0011 研究失败语义与事实覆盖边界](docs/decisions/ADR-0011-research-reliability-fact-boundary.md)（`accepted`）
21. [产品收口与后续总计划](docs/product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)（G1-G6 有界路线）
22. [G1/G2 研究可靠性与事实覆盖实施方案](docs/stages/R2_R_G1_G2_EXECUTION_PLAN.md)（`G1/G2-A/B offline complete / live Search failed safely`）
23. [R2-R-07 Search Reliability 与 Error Provenance 阶段卡](docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)（`completed / E2-L passed`）
24. `contracts/schemas/` 中的 canonical schema
25. 受影响模块的 `README.md`、测试和 ReleaseManifest
26. [产品失败复盘与长期工程教训](docs/retrospectives/RETRO-2026-08-30-PRODUCT-FAILURE-AND-LESSONS.md)
27. [ADR-0012 DSH-first 产品重新收口](docs/decisions/ADR-0012-dsh-first-product-rebaseline.md)（`accepted`）
28. [DSH-first 产品实现总方案](docs/product/DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)（`accepted`）
29. [ADR-0013 DSH Web 原生插件与上游升级集成策略](docs/decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)（`accepted`）
30. [DSH 与 Decision Hub 系统总装设计](docs/product/DSH_HUB_SYSTEM_ASSEMBLY.md)（`accepted`；理解整体方案和代码结构先读此文）
31. [DSH Native Web Product Core Stage Charter](docs/stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)（`engineering acceptance complete / product value pending`）
32. [DSH-NATIVE-CORE 完成实施方案](docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)（NATIVE-00..05、NC-01..07 任务与统一退出证据）
33. [DSH 交互运行态与插件生态审计](docs/evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md)（replay 输入报错根因、官方插件边界、生态候选与 LIVE-00..03 有界计划）
34. [最终产品交付与主线闭环方案](docs/product/FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)（`accepted`；当前交付缺口、唯一入口、交易员工作区、主动补证、通知、资产与有限执行顺序）
35. [PRODUCT-CLOSEOUT-01 DSH Native Trader Pilot](docs/stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)（`E1/E2-R/E2-L passed / E3 observation`；C1-C7、视图、约束和最终验收 checklist）
36. [产品执行总方案](docs/product/PRODUCT_EXECUTION_MASTER_PLAN.md)（`accepted / implementation authorized`；架构与执行总参考）
37. [PRODUCT-CLOSEOUT 执行与自测记录](docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md)（E1/E2-R 收口证据、官方 DSH 三类报告、恢复/截图 hash、真实 Search 失败和 E2-L/E3 边界）
38. [产品实现与最终验收方案](docs/product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)（C1-C7 详细实现/checklist、SDD/BDD/TDD/ADR 和产品/价值验收门）
39. [最终产品交付实施书](docs/product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)（已执行的顶层交付合同：最终视图、架构边界、插件/loop/资产、C1-C7、全局约束和停止门）
40. [产品收口总执行书](docs/product/PRODUCT_CLOSEOUT_MASTER_EXECUTION_2026-09-01.md)（已执行的 E1/E2-R/E2-L 历史任务书；当前状态以控制书为准）
41. [通用底座与首个产品最终实施章程](docs/product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)（已执行的 DSH-first 实施章程；保留 P1-P6 与 E1-E3 设计）
42. [E2L-01 事件准入、成本保护与任务优先级实施书](docs/stages/E2L_01_EVENT_ADMISSION_COST_PRIORITY.md)（工程/replay 已完成：修复误准入、fresh bootstrap backlog 和手动任务饥饿；live 产品复验并入 E2-L）
43. [ADR-0015 事件准入、首次同步成本保护与 Run 优先级](docs/decisions/ADR-0015-event-admission-cost-and-priority.md)（accepted）
44. [E2L-02 DSH 耐久进度、模型步时限与部分结果恢复](docs/stages/E2L_02_DSH_DURABLE_PROGRESS_AND_DEADLINE.md)（`completed`：能力即时入账、模型步 watchdog、部分进度恢复）
45. [ADR-0016 能力调用即时入账与 DSH 模型步截止](docs/decisions/ADR-0016-durable-capability-progress-and-model-step-deadline.md)（accepted）
46. [最终产品执行合同](docs/product/PRODUCT_FINAL_EXECUTION_CONTRACT_2026-09-01.md)（已执行的 E2L-02 合同；保留统一视图、所有权和可插拔边界）
47. [产品执行与最终验收总表](docs/product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)（历史跨文档总表；当前事实以控制书和 E2-L 验收记录为准）
48. [产品交付控制书与最终验收包](docs/product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)（`accepted / E2-L passed / E3 observation`，当前唯一执行/收口入口：产品视图、DSH/Hub/LangGraph/插件边界、完成 checklist、治理约束和停止门）
49. [最终产品收口实施任务书 v1](docs/product/PRODUCT_CLOSEOUT_FINAL_IMPLEMENTATION_TASKBOOK_2026-09-01.md)（`superseded for remaining execution`，保留 Search attribution 和安全降级的历史任务证据）
50. [Pilot Ready 最终执行与验收书 v2](docs/product/PRODUCT_PILOT_READY_FINAL_EXECUTION_PLAN_2026-09-01.md)（`executed / historical taskbook`，保留 live 预算/Session 反例、PR-00..08 和 BDD/TDD 证据，不再作为执行入口）
51. [E2-L 官方 DSH Web 真实产品验收记录](docs/evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)（`passed / research-only pilot entry`，Run/Session/能力/错误/后台复查/截图 hash/完整质量门）

`DECISION_HUB_FINAL_ARCHITECTURE.md` 和 `DSH_RESEARCH_DECISION_LOG.md` 是历史研究/审查材料；若与 V1 基线冲突，以 V1、ADR、canonical schema 和受影响模块文档为准。

聊天记录、临时草稿和历史研究不能单独授权代码变更。架构与实现冲突时必须停下并补 ADR。

新结论进入对应 Platform/Domain/Stage/ADR 文档，不在旧总文档末尾连续追加讨论记录；旧结论被替代时标记 `superseded` 并保留历史链接。

## 首批纵向链

```text
TextEnvelope
  -> Observation/Event admission
  -> EvidenceSnapshot(PIT)
  -> LangGraph fixed decision/research graph
  -> StrategyCandidate
  -> deterministic Gate
  -> Artifact + Forecast + Outcome/Evaluation
  -> Query View -> Decision Desk
```

这条正式链是 `fixed baseline`，不是完整 Agent。R2-R candidate path 已在不迁移账本/Gate 的前提下加入 `Trigger Snapshot -> DSH tool/subagent loop -> Evidence Sufficiency -> Decision Snapshot`；具体边界只以 R2-R Charter 为准。该 candidate 尚未通过 Promotion，Fixed 继续 active。

## 开发入口

```text
uv run pytest
uv run python -m tools.contract_codegen check
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
```

每项任务开始前先生成 `Task Context Manifest`：

```bash
python tools/context/build_task_context.py --objective "..." --paths packages/kernel apps/hub_api
```

每项任务结束前运行：

```bash
python tools/docs/check_module_docs.py
```

## 禁止事项

- 不 clone DSH，不把 DSH session 当业务账本。
- 不在前端手写同名 DTO，不直接读取 SQL、LangGraph state 或原始 JSON。
- 不在 Graph node、API route、Provider adapter 中重复实现 Gate、状态机、重试或事务提交。
- 不新增无调用方的基础设施或 `common/`、`utils/`、`helpers/` 垃圾目录。
- Agent 只能提交候选，代码 Gate 才能发布。
