# 产品规格索引

本目录保存用户可感知的产品形态、工作方式和验收口径。它回答“用户最终看到什么、系统如何主动工作、什么状态才算可用”，不替代技术架构、ADR、canonical schema 或 Stage Charter。

当前规格：

- [DSH 插件底座、现有插件资产与平台解耦优化方案](DSH_PLUGIN_PLATFORM_DECOUPLING_PLAN_2026-09-06.md)：`proposed / architecture remediation pending owner confirmation`，盘点自研 DSH Host/Client/Research/Synthesis 插件、官方插件与 LoongSuite 的真实启用状态，并给出 Platform/crypto_macro 解耦结构、依赖约束、迁移任务和验收门。
- [PD-04～06 产品运行收口实施方案](../stages/PD_04_06_PRODUCT_RUNTIME_CLOSEOUT.md)：已完成，锁定当前代码构建、产品契约探针、单实例、历史状态语义、DSH/Decision Desk 浏览器验收与有限停止线。
- [产品事实充分度与主动交付修复方案](PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)：`accepted / PD-00..06 runtime closeout completed / PD-07 observation pending`，基于真实 G2-AF Run、PD public canary 和当前代码，锁定 requirement 语义 Gate、事件窗口、typed provider、主动报告、前端/成本/自进化和 PD-00..07 有限交付门。
- [PD-07 前瞻价值观察与产品停止线](../stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)：当前唯一下一阶段；不新增功能，以未来事件、PIT、事实充分度、延迟、成本和 owner usefulness 作 promote/retain/stop。
- [主动研究交付修复方案](PRODUCT_AGENTIC_FACT_SUFFICIENCY_REMEDIATION_2026-09-05.md)：`completed / PD-04..06 runtime closeout verified`，解释 Search locator 与 typed fact 的边界、replay continuation 回归、DSH/LangGraph/Hub 所有权、前端业务状态和本轮退出门。
- [产品交付控制书与最终验收包](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)：`accepted / E2-L passed / historical predecessor to PD-07`，保留产品视图、DSH/Hub/LangGraph/插件边界、E2-L checklist、治理约束和原始停止门；不再覆盖当前 PD-07 Stage Charter。
- [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)：`passed / research-only pilot entry`，记录正式 Run/Session、能力、错误 provenance、后台 child recheck、页面截图 hash 和完整质量门。
- [Pilot Ready 最终执行与验收书 v2](PRODUCT_PILOT_READY_FINAL_EXECUTION_PLAN_2026-09-01.md)：`executed / historical taskbook`，保留 live Run 的代码预算越界、首次 Session 竞态、PR-00..08 和 BDD/TDD 证据，不再作为执行入口。
- [最终产品收口实施任务书 v1](PRODUCT_CLOSEOUT_FINAL_IMPLEMENTATION_TASKBOOK_2026-09-01.md)：`superseded for remaining execution`，保留已完成 Search attribution 和 synthesis 安全降级的历史任务证据，不再独立扩大范围。
- [研究智能体主体产品规格](RESEARCH_AGENT_PRODUCT_SPEC.md)：`accepted`，是 R2-R 的产品体验和端到端行为真源。
- [产品收口与后续总计划](PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)：`accepted for G1/G2 execution / G3+ owner review pending`，列出当前已知问题、G1-G6 有界 Gate、停止开发条件和未来扩展准入规则。
- [G2-AF 主动事实获取与自主研究阶段方案](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)：`G2-AF-01..04 completed / G2-AF-05 superseded by PD-07`，保留 hard gap、Search provider、DSH 主动补证、自动调度、通知和资产沉淀的历史方案；Tavily 仍按需启用，不修改 active runtime。
- [G2-AF Tavily / DSH Search 核查与执行记录](../evaluations/G2_AF_TAVILY_EXECUTION_LOG_2026-09-03.md)：记录官方 DSH 原生 `web_search` 与当前产品 preset 的真实边界、Tavily key 安全处理、固定金融来源注册表和每轮执行记录规范；native route probe 已通过，Tavily 未调用。
- [G2-AF 实施执行记录](../evaluations/G2_AF_IMPLEMENTATION_EXECUTION_LOG_2026-09-04.md)：记录真实 DSH Web Run、Search/Fetch/attestation partial 结果、错误分类修复、质量门和下一张唯一任务卡。
- [ADR-0012 DSH-first 产品重新收口与实时研究闭环](../decisions/ADR-0012-dsh-first-product-rebaseline.md)：`accepted`，重新定义当前产品形态、DSH/LangGraph/Workbench 边界、可用性闭环和止损条件。
- [DSH-first 产品实现总方案](DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)：`accepted`，锁定前端主界面、DSH/LangGraph 所有权、Capability 插件模型、后端目录、运行模式、阶段任务和可用性定义。
- [ADR-0013 DSH Web 原生插件与上游升级集成策略](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)：`accepted`，把 DSH Web 调整为交互主壳，以官方 `dsh.bundle`/`dsh.client` 插件接入 Hub，Decision Desk 收敛为管理后台。
- [DSH 与 Decision Hub 系统总装设计](DSH_HUB_SYSTEM_ASSEMBLY.md)：`accepted`，单独解释 DSH、Hub 外层、Domain Pack、两个前端、三类状态数据和当前/目标代码目录，不混入历史复盘。
- [DSH 与 Decision Hub 边界和代码地图](DSH_AND_HUB_BOUNDARY_GUIDE.md)：独立解释 DSH 主壳、Hub 外层、官方插件、LangGraph、两个前端、三份状态数据和当前/目标代码落点。
- [最终产品交付与主线闭环方案](FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)：`accepted`，明确唯一用户入口、交易员业务呈现、主动补证、通知、资产沉淀、交付任务和停止条件。
- [PRODUCT-CLOSEOUT-01 DSH Native Trader Pilot](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)：`E1/E2-R/E2-L passed / E3 observation`，保留 C1-C7 代码落点、视图、约束和最终验收 checklist。
- [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)：`executed`，保留产品视图、所有权、C1-C7、全局约束和停止门，不再作为当前入口。
- [产品收口总执行书](PRODUCT_CLOSEOUT_MASTER_EXECUTION_2026-09-01.md)：`executed / historical taskbook`，保留 C3-C7、插件/loop/资产边界和实施证据。
- [通用底座与首个产品最终实施章程](PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)：`executed`，保留 DSH-first 产品形态、Hub/LangGraph/插件所有权、个人资产、P1-P6 和 E1-E3 设计。
- [产品执行总方案](PRODUCT_EXECUTION_MASTER_PLAN.md)：`accepted / implementation authorized`，保留为架构与执行总参考，不单独覆盖当前状态。
- [产品实现与最终验收方案](PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)：`accepted / implementation authorized`，作为 C1-C7 详细 checklist，记录代码落点、错误语义和工程/产品/价值验收门。
- [最终产品执行合同](PRODUCT_FINAL_EXECUTION_CONTRACT_2026-09-01.md)：`executed / historical contract`，保留 E2L-02 的所有权、用户视图、两层循环和治理约束。
- [产品执行与最终验收总表](PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)：`historical master`，保留跨产品视图、插件边界和阶段目标；当前状态以控制书为准。

维护规则：

1. 产品规格描述用户任务、页面、状态、行为和验收，不复制框架内部实现。
2. 跨模块或不可逆技术选择写 ADR；实现步骤写 Stage Charter；字段写 canonical schema。
3. 产品规格、ADR、Stage Charter 发生冲突时停止实现并先完成一致性修正。
4. 未通过相应 Stage Gate 和真实验收的能力必须标为 proposed、partial 或 not implemented。
