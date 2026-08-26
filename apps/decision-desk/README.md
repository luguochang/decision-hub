# Decision Desk

## 目的
提供 Inbox、Decision、Forecast、Health、Assets 和 Evolution 的本地可观测工作台。

## UI 边界
React + TypeScript + Vite + TanStack Query + Lucide。页面只消费 API View DTO，不读 SQL、LangGraph state 或 DSH UI 状态。

## 视觉基线
采用 data-dense operations dashboard：中性浅色画布、专业深蓝导航、状态色、紧凑表格、可见 focus、响应式 375/768/1024/1440；不使用紫粉渐变、HUD 装饰或 JSON dump。

## 最近验证
R0 scaffold；使用 `pnpm build` 和 Playwright 视觉 smoke 验证。
