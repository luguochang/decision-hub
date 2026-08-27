# Hub MCP

## 目的

使用官方 Python MCP SDK 把 Decision Hub 的 Workbench Query 和 owner 受限 Memo/Feedback command 暴露给 DSH、Codex 或其他 MCP 客户端。MCP 是 transport adapter，不是第二套业务 API、账本或 Agent loop。

## 运行

默认使用 stdio：

```bash
./.venv/bin/python -m apps.hub_mcp.main
```

需要 streamable HTTP 时显式设置 `DECISION_HUB_MCP_TRANSPORT=streamable-http`，默认监听 `127.0.0.1:8001`；`DECISION_HUB_OWNER_ID` 只用于单 owner 写入身份绑定。

## 边界

工具由 `packages/workbench_adapters/mcp.py` 注册，参数和返回值使用 Core Pydantic DTO。Memo/Feedback 只能引用已有 Run/Snapshot/资产，不能写 Artifact、Forecast、Gate、Outbox 或 ActivePointer。MCP session/raw JSON 不写入业务账本；官方 SDK 负责 JSON-RPC、stdio、SSE 和 streamable HTTP 生命周期。

## 验证

`tests/workbench/test_mcp_server.py` 覆盖工具发现、结构化 Query、owner deny、引用校验、官方 stdio subprocess client 和官方 streamable HTTP client 握手，以及 DSH 未启动时 Core 仍可查询。
