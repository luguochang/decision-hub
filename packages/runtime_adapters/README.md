# Runtime Adapters

## 目的
实现同一 `AgentRuntime` port。R0 提供 fake/replay 与 LangGraph-native seam；Pi 未来只作为独立 adapter。

## 不负责
不拥有账本、Gate、发布权或个人资产。

## 公开契约
`AgentRequest`、`AgentResult`、`AgentRuntime`。

## Provider 配置

`langgraph_agent.provider_config.ProviderConfig` 是 Runtime 唯一的 Provider 配置入口。它使用 Pydantic Settings 读取 `DECISION_HUB_PROVIDER_ID`、`OPENAI_BASE_URL`/`SUB2API_BASE_URL`、`DECISION_HUB_MODEL`、`DECISION_HUB_LLM_API_MODE`、timeout、retry、token budget 和 capability 字段，并在构造时拒绝非法协议、预算或不支持的模式。

`ProviderCapabilityManifest` 只保存 provider/model、支持的 API mode、结构化输出能力和 capability version。API key 不属于配置对象或 manifest，只在构建 `ChatOpenAI` 时从当前进程环境读取，不写入日志、账本、manifest 或前端。

## 降级
未配置外部模型时使用 fake/replay 路径，保证本地 replay 和 CI 确定性。

Evolution candidate runtime 通过同一 `AgentRuntime` contract 注入，不把候选配置写回 active pointer。`CandidateArtifactStore` 保存不可变候选配置和 hash；Evolution worker 只读取它用于 replay/holdout/shadow，owner-only Promotion 才能改变正式 baseline。

## 外部模型自测

真实 Provider 只能由 `tools/canary/run_live_text_canary.py` 显式触发。它要求 `DECISION_HUB_LLM_ENABLED=1`、`OPENAI_BASE_URL`、`DECISION_HUB_MODEL` 和进程内 API key；使用临时 SQLite，不把 key 写入日志、数据库或文件。canary 只验证 OpenAI-compatible 结构化响应能否走完 Runtime、LangGraph、Gate 和 Artifact 路径，不代表预测质量已证明。

`DECISION_HUB_LLM_API_MODE` 默认为 `responses`，适合 GPT-5/Codex 兼容的 `/v1/responses`；只支持 Chat Completions 的 Provider 显式设置为 `chat`。这只是 adapter 配置，Core 不感知协议。R0-B Provider 边界已完成 timeout/retry/error taxonomy、usage/cost unknown/estimated 投影和可选 cost budget fail-closed 语义；真实 endpoint 仍只能由显式 canary 证明兼容性。

## 最近验证
Fake/Replay 与 LangGraph Runtime 适配器测试通过；三角色调用、Provider timeout/429/5xx/structured-output failure、成本估算和预算耗尽均已投影到 Run Inspector 并有测试。外部 endpoint 兼容性仍以 canary 结果为准，当前未宣称预测质量或生产稳定性。

R2-R 新增具体 [`dsh_runtime/`](dsh_runtime/README.md)：它使用官方 Python
SDK/bundled runtime、restricted Cordis profile、Session/Tool/Subagent trace
mapper 和严格 ResearchSession result mapper。旧 `DshAgentRuntime` 保留为历史
candidate callable seam，不得继续向其中添加 SDK 或业务逻辑。

R1 的 Source/Market/Notification adapter 不属于 AgentRuntime；它们通过 `kernel.ports.sources` 的 Protocol 接入，不能把 HTTP、交易所或邮件 SDK 类型泄漏到 Kernel 或 LangGraph。
