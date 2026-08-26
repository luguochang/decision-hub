# 工程规范

本目录保存长期有效的开发、测试和发布规则。临时探索、一次性输出和未确认的推理放在 `tmp/`，不能作为实现依据。

## 文档入口

- [TDD/SDD 与自测规范](TDD_SDD_SELF_TEST_STANDARD.md)：需求、契约、测试、外部模型 canary 和完成定义。
- [本地运行手册](../runbooks/local-development.md)：本地 API、前端和数据目录。

## 维护约束

修改跨模块契约时，先改 `contracts/` canonical schema，再运行 codegen/check；修改模块行为时，同时更新受影响模块的 `README.md`、测试和 `docs/IMPLEMENTATION_STATUS.md`。不可逆或跨边界的选择必须新增 ADR，不依赖聊天上下文。
