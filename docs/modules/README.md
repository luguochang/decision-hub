# 模块地图

| 模块 | 目的 | 公开入口 | 上游/下游 | 契约 | 测试 |
|---|---|---|---|---|---|
| `packages/kernel` | 账本、PIT、Gate、应用用例 | `packages.kernel` public API | API/Worker -> Ports/DB | `v1` | `tests/kernel` |
| `packages/orchestration/langgraph` | 可恢复决策与研究图 | graph builders | Worker -> Kernel/Runtime | `v1` | `tests/replay` |
| `packages/runtime_adapters` | AgentRuntime 实现 | `AgentRuntime` | Graph -> adapter | `v1` | `tests/contracts` |
| `packages/source_adapters` | 文本来源统一适配 | `SourcePlugin` | API/未来监听 -> Kernel | `v1` | `tests/contracts` |
| `packages/provider_adapters` | 市场、通知等第三方适配 | `MarketDataPort`、`NotificationPort` | Worker -> Kernel ports | `v1` | `tests/providers` |
| `packages/query_views` | 前端只读 View DTO | Query services | API -> repository | `v1` | `tests/e2e` |
| `packages/contracts_ts` | TypeScript/Zod 公开契约镜像 | `@decision-hub/contracts-ts` | canonical schema -> Web/DSH adapters | `v1` | `pnpm build/test` |
| `apps/hub_api` | REST API 和静态前端 | `/v1` | Browser/CLI -> Kernel | `v1` | `tests/e2e` |
| `apps/decision-desk` | 可观测决策工作台 | React routes | API -> View DTO | `v1` | Playwright/Vitest |

每个模块下的 README 是局部边界事实源。模块新增前先更新本表和任务上下文。
