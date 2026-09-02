# 工程规范

本目录保存长期有效的开发、测试和发布规则。临时探索、一次性输出和未确认的推理放在 `tmp/`，不能作为实现依据。

## 文档入口

- [项目宪章](PROJECT_CHARTER.md)：短上下文锚点、当前大阶段、不变量和非目标。
- [全局开发治理规范](DEVELOPMENT_GOVERNANCE.md)：SDD/BDD/TDD/ADR、上下文压缩、变更记录和阶段门。
- [R1 Realtime Event Engine Stage Charter](../stages/R1_REALTIME_EVENT_ENGINE.md)：已完成的离线阶段，定义来源、市场、通知、调度与验收边界；真实网络能力另立 Stage Gate。
- [R0-B Stage Charter](../stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)：已完成的 Provider 边界历史记录。
- [TDD/SDD 与自测规范](TDD_SDD_SELF_TEST_STANDARD.md)：需求、契约、测试、外部模型 canary 和完成定义。
- [R2-R Agentic Research Runtime Stage Charter](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)：已完成的架构纠偏阶段；定义真正的 Agent、DSH/LangGraph/Core 边界、代码结构和退出门，当前结论为 `retain_baseline`。
- [通用产品平台基线](../platform/PLATFORM_BASELINE.md)：当前代码审计、DSH 执行主线与跨领域所有权提案。
- [当前状态短上下文](../context/CURRENT_STATE.md)：长任务恢复入口；只投影事实，不替代 ADR/schema。
- [本地运行手册](../runbooks/local-development.md)：本地 API、前端和数据目录。

## 维护约束

修改跨模块契约时，先改 `contracts/` canonical schema，再运行 codegen/check；修改模块行为时，同时更新受影响模块的 `README.md`、测试和 `docs/IMPLEMENTATION_STATUS.md`。不可逆或跨边界的选择必须新增 ADR，不依赖聊天上下文。
