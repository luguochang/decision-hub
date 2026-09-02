# Research MCP

本服务是 DSH Research Harness 到 Decision Hub `ResearchCapabilityGateway` 的唯一
模型工具入口。它只暴露一个 canonical `research_capability_execute` 工具，不包含
Memo/Feedback、Gate、Artifact、Forecast、ActivePointer 或任何 owner 写命令。

默认使用 streamable HTTP 并监听 `127.0.0.1:8002`：

```bash
./.venv/bin/python -m apps.research_mcp.main
```

DSH restricted profile 通过官方 `@deepseek-ai/dsh-mcp-client` 连接
`http://127.0.0.1:8002/mcp`，模型看到
`mcp__decision_research__research_capability_execute`。MCP 只负责协议和工具发现；
license/audit、显式 enable、域名、timeout、费用、PIT、hash 和 Evidence 入账仍由
Kernel Gateway 裁决。

网络能力默认不启用。`DECISION_HUB_RESEARCH_CAPABILITIES` 只能选择 Pack 中已
`license_status=approved` 且 `audit_status=approved` 的能力；candidate 即使写入环境
也会 fail-closed。Replay 还必须通过 `DECISION_HUB_RESEARCH_REPLAY_FIXTURES` 显式
指定版本化归档文件；缺失查询直接失败，绝不回退 live network。DSH profile 设置
`failOnStartupError=true`，Research MCP 未就绪时 Harness readiness 必须失败。普通 CI
使用注入的 fake/replay Gateway，不触网。

`web.search` 只在 `DECISION_HUB_RESEARCH_CAPABILITIES` 显式包含该 capability 时
创建官方 OpenAI SDK 客户端。配置读取
`DECISION_HUB_SEARCH_{API_KEY,BASE_URL,MODEL}`，并兼容现有
`OPENAI_*` / `SUB2API_*`；密钥只进入 adapter 进程，不进入 MCP 参数、Trace、Evidence
或账本。搜索来源必须来自 Responses `web_search_call.action.sources`，搜索摘要固定为
`search_derived`，不能替代 Official/Market 能力对精确事实的复核。

显式 live canary（会产生中转费用，输出只含脱敏计数）：

```bash
DECISION_HUB_SEARCH_LIVE_CANARY=1 \
./.venv/bin/python -m tools.canary.run_responses_web_search_canary
```
