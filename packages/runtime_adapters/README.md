# Runtime Adapters

## 目的
实现同一 `AgentRuntime` port。R0 提供 fake/replay 与 LangGraph-native seam；Pi 未来只作为独立 adapter。

## 不负责
不拥有账本、Gate、发布权或个人资产。

## 公开契约
`AgentRequest`、`AgentResult`、`AgentRuntime`。

## 降级
未配置外部模型时使用 fake/replay 路径，保证本地 replay 和 CI 确定性。

## 外部模型自测

真实 Provider 只能由 `tools/canary/run_live_text_canary.py` 显式触发。它要求 `DECISION_HUB_LLM_ENABLED=1`、`OPENAI_BASE_URL`、`DECISION_HUB_MODEL` 和进程内 API key；使用临时 SQLite，不把 key 写入日志、数据库或文件。canary 只验证 OpenAI-compatible 结构化响应能否走完 Runtime、LangGraph、Gate 和 Artifact 路径，不代表预测质量已证明。

`DECISION_HUB_LLM_API_MODE` 默认为 `responses`，适合 GPT-5/Codex 兼容的 `/v1/responses`；只支持 Chat Completions 的 Provider 显式设置为 `chat`。这只是 adapter 配置，Core 不感知协议。

## 最近验证
Fake/Replay 与 LangGraph Runtime 适配器测试通过；外部 endpoint 兼容性仍以 canary 结果为准，当前未标记为生产完成。
