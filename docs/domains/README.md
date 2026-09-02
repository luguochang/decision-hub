# 领域文档索引

本目录保存各 Product Extension/Domain Pack 自己拥有的方法、证据、Gate 和评测边界。跨领域能力写入 `docs/platform/`，阶段任务写入 `docs/stages/`，不可逆决定写入 `docs/decisions/`。

## 当前领域

- [Crypto Macro Domain Pack](crypto_macro/README.md)：`decision.v1` 的首个领域，状态 `accepted / R2-R-00..06E candidate path implemented / Fixed active`。

## 新增规则

- 新领域不得修改另一个领域的 schema、Gate 或 fixture。
- 新 Product Extension 必须定义自己的 Task/Artifact/Evaluation 闭环；不能为了复用而继承不适用的 Forecast、Outcome 或 Brier。
- Domain 文档必须引用 canonical schema/ADR，不复制跨模块契约。
- 只有真实调用方和 Stage Gate 出现后才创建实现目录，不提前搭空骨架。
