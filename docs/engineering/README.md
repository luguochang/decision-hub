# 工程规范

本目录保存长期有效的开发、测试和发布规则。临时探索、一次性输出和未确认的推理放在 `tmp/`，不能作为实现依据。

## 文档入口

- [项目宪章](PROJECT_CHARTER.md)：短上下文锚点、当前大阶段、不变量和非目标。
- [全局开发治理规范](DEVELOPMENT_GOVERNANCE.md)：SDD/BDD/TDD/ADR、上下文压缩、变更记录和阶段门。
- [R0-B Stage Charter](../stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)：已完成阶段的价值、非目标、BDD/TDD 验收门和任务卡记录；当前没有执行中的 Stage Charter。
- [TDD/SDD 与自测规范](TDD_SDD_SELF_TEST_STANDARD.md)：需求、契约、测试、外部模型 canary 和完成定义。
- [本地运行手册](../runbooks/local-development.md)：本地 API、前端和数据目录。

## 维护约束

修改跨模块契约时，先改 `contracts/` canonical schema，再运行 codegen/check；修改模块行为时，同时更新受影响模块的 `README.md`、测试和 `docs/IMPLEMENTATION_STATUS.md`。不可逆或跨边界的选择必须新增 ADR，不依赖聊天上下文。
