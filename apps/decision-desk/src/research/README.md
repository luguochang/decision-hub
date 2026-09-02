# Research UI

## 目的

提供 R2-R Research Command Center、Run Detail、SSE 轨迹续接和 owner command。默认首屏是 durable research task，而不是空聊天框。

## 数据边界

- 只消费 `agentic_research.schema.yaml` 生成的 TypeScript/Zod DTO。
- 不读取 DSH JSONL、LangGraph state、SQL 或 Provider 原始响应。
- `EventSource` 只接收规范化 `ResearchTraceEvent`；断线后通过 sequence 续接。
- owner 命令必须携带 request ID、owner identity 和 reason；页面不能直接修改 Artifact、Gate 或 active pointer。

## 视图

- Research Run 列表：自动触发、人工提交、scheduled recheck。
- Run Detail：Plan、Tool、Evidence、Sufficiency、Causal Case、30m/24h/72h、Stop Reason、Trace。
- Operations 状态：明确显示 research worker heartbeat，不用页面加载状态冒充后台在线。
- 运行模式：Research 首屏必须显示 worker mode（`fake`/`replay`/`provider`）和 canary 状态；`replay` 只代表离线 fixture，不得写成实时网络事实。
- 本地 API：开发时通过 Vite `VITE_API_PROXY_TARGET` 指向与 worker 相同的当前 API；浏览器默认走同源相对路径，避免把跨源 `VITE_API_BASE_URL` 当作本地代理配置。

## 维护约束

修改字段先改 canonical schema 并 codegen；新增 UI 行为同步更新本目录测试和本文档。不得添加 raw JSON 默认面或前端私有 DTO 镜像。

失败 Run 的 stop code、error provenance、failed capability 和 retryability 已由
[R2-R-07](../../../../docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md) 的 G1-D
实现并覆盖测试。页面不使用 loading 状态代替 durable 终态，也不展示 raw DSH/Provider
JSON；G2-C 只影响真实来源 canary，不改变 UI 契约。
