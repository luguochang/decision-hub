# 产品规格索引

本目录保存用户可感知的产品形态、工作方式和验收口径。它回答“用户最终看到什么、系统如何主动工作、什么状态才算可用”，不替代技术架构、ADR、canonical schema 或 Stage Charter。

当前规格：

- [产品交付控制书与最终验收包](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)：`accepted / E2-L passed / E3 prospective observation`，当前唯一执行和收口入口；锁定产品视图、DSH/Hub/LangGraph/插件边界、完成 checklist、治理约束和停止门。
- [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)：`passed / research-only pilot entry`，记录正式 Run/Session、能力、错误 provenance、后台 child recheck、页面截图 hash 和完整质量门。
- [Pilot Ready 最终执行与验收书 v2](PRODUCT_PILOT_READY_FINAL_EXECUTION_PLAN_2026-09-01.md)：`executed / historical taskbook`，保留 live Run 的代码预算越界、首次 Session 竞态、PR-00..08 和 BDD/TDD 证据，不再作为执行入口。
- [最终产品收口实施任务书 v1](PRODUCT_CLOSEOUT_FINAL_IMPLEMENTATION_TASKBOOK_2026-09-01.md)：`superseded for remaining execution`，保留已完成 Search attribution 和 synthesis 安全降级的历史任务证据，不再独立扩大范围。
- [研究智能体主体产品规格](RESEARCH_AGENT_PRODUCT_SPEC.md)：`accepted`，是 R2-R 的产品体验和端到端行为真源。
- [产品收口与后续总计划](PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)：`accepted for G1/G2 execution / G3+ owner review pending`，列出当前已知问题、G1-G6 有界 Gate、停止开发条件和未来扩展准入规则。
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
