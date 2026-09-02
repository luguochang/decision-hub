# Decision Desk

## 目的
提供 Research、Inbox、Decision、Forecast、Health、Assets、Evolution 和 Operations 的本地可观测工作台。Research Command Center 是 R2-R 的默认入口，展示 durable Run、证据补齐过程、根因链、分周期结论、规范化轨迹和 owner command；Operations 用于区分 API/realtime/research/evolution 四个进程、runtime/provider/canary、来源和 capability 权限以及 durable 队列。

## UI 边界
React + TypeScript + Vite + TanStack Query + Zod generated contracts + Lucide。页面只消费 API View DTO，不读 SQL、LangGraph state、DSH session 或原始 provider JSON；API 失败或没有真实数据时显示明确不可用/空状态，不使用 demo fallback 伪装在线。

## 视觉基线
采用 data-dense operations dashboard：中性浅色画布、专业深蓝导航、状态色、紧凑表格、可见 focus、响应式 375/768/1024/1440；不使用紫粉渐变、HUD 装饰或 JSON dump。

## 最近验证
Decision Desk 已消费真实 `/v1/runs/{run_id}/inspector`、`/v1/operations`、`/v1/evolution/jobs` 和 `/v1/research/runs/**` Query/View，显示 Run/Step/Attempt/Call、证据、Gate、Forecast、Evaluation、四进程状态、研究/演进队列和 Research Result/Trace。Vitest 9 passed 与 Vite build 通过；人工浏览器检查 375/768/1024/1440 视口无横向溢出且控制台无错误，真实长期运行仍以 Operations heartbeat 和独立 canary 为准。

## 本地 API 连接

生产静态页面使用同源 `/v1` 与 `/health`。开发时可用环境变量把 Vite 和前端请求指向同一隔离 API，避免误连旧进程：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8030 \
pnpm --dir apps/decision-desk dev -- --port 5175
```

本地开发默认使用同源相对路径，由 `VITE_API_PROXY_TARGET` 控制 Vite 代理；这样不会触发跨源请求。只有部署环境已明确配置 CORS 时，才设置 `VITE_API_BASE_URL=http://...`，并让它与 API target 指向同一实例。Research 页面会显示 `fake/replay/provider` runtime 和 worker heartbeat；`replay` 结果只代表 fixture，不是实时网络事实。
