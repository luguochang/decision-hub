# Workbench Adapters

## 目的

把 DSH、Codex、CLI 或未来 MCP transport 接到同一个 `ResearchWorkbenchPort` 和 `CapabilityManifest` 准入边界；不拥有业务事实。

## 公开入口

`DshCapabilityAdapter.discover(...)` 只登记待审计能力，不能执行插件或改变 enabled 状态。查询和受限命令由 Kernel `WorkbenchAssetService` 与 Hub API 提供。

## 禁止事项

不保存 DSH session/Cordis Context，不自写 MCP/JSON-RPC，不自动安装插件，不直读 SQL，不绕过 owner、PIT、schema 或 Gate。

## 测试

`tests/workbench` 覆盖 deny-by-default、引用校验、幂等和 DSH 未启动时 Core 可用。

R2-R-07 的 G1-B/C 已完成错误来源和并行调用失败隔离；本适配层不扩大 DSH 权限或
网络 capability。G2-C 真实 Search canary 仍需 owner 确认，详见
[阶段卡](../../docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。
