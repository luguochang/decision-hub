# DSH Research Runtime

本模块是 DeepSeek Harness Python SDK 到 `ResearchHarnessRuntime` 的唯一
适配层。当前固定 Python SDK/Runtime 为 `0.1.1rc1`；官方 npm CLI 的
`0.1.1-rc.2` 不是同一个可直接替换的 Python 发布物。

## 职责

- `client.py`：持有官方 SDK 子进程、Session 和 bounded timeout；secret 只从
  进程环境读取，不进入配置、日志或账本。
- `profiles/` 与 `profile.py`：版本化 restricted Cordis 组合及安全检查。
- `event_mapper.py`：将 DSH Session/Turn/Tool/Subagent 通知转换为 canonical
  `ResearchTraceEvent`，不复制 raw event JSON；只从官方 tool-result 结构中解析并校验
  明确的 `provenance=` JSON，保留 capability/error/origin/retryable/deadline，解析失败才
  fail-closed 为 `dsh_tool_failed`。
- `result_mapper.py`：严格验证模型的 `research-synthesis-candidate.v1`，再从
  canonical MCP Tool Result、可信 Session/Trace 和确定性策略组装
  `research-session-result.v1`。模型不拥有 Round/Tool/Evidence/Coverage/时间戳。
  当前 SDK 未提供可信聚合 token/cost 时，这两个字段固定为 unknown，禁止接受模型自报。
- `tool_result_attestation.py`：从成功的 canonical MCP Tool Result 提取证据；最终
  `EvidenceCandidate` 缺失、篡改、跨 Session 或越权时以
  `dsh_evidence_unattested` fail-closed，且不持久化 raw Tool Result。
- `runtime.py`：实现 Kernel 的 `ResearchHarnessRuntime` port。
- `health.py`：本地 bundled runtime/profile handshake；不调用外部模型。

## Restricted profile

`decision-research.cordis.yml` 只装载 JSON-RPC、DeepSeek LLM、Session
JSONL/checkpoint、in-process subagent、todo、token meter 和 compaction。它不
包含 shell、terminal、filesystem、sandbox、插件市场、宿主凭据读取或自动
安装能力。Web/Official/Market 工具要在 R2-R-02 经 Capability Gateway 审计后
接入，不能临时把 shell 当搜索工具。
评测组合通过 `DshRuntimeConfig.research_mcp_url` 为每个 PIT case 注入独立 MCP；
该 URL 只进入 DSH 子进程环境，不依赖修改进程全局环境切换 case。

## 当前后续边界

G1-A/B/C 已完成：模型填写的 `observed_at` 仅作为诊断，PIT 时间由 Gateway/adapter 拥有；MCP/DSH 错误保留结构化 provenance；并行 capability 的部分成功不会被丢弃。失败 Run 的最终状态由 Query View/UI 投影。对应实现和测试见 [R2-R-07](../../../docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。本模块仍不新增 DSH loop、权限或网络 capability；G2-C 真实 Search canary 需单独 owner 确认。

## Provider 环境

Runtime 配置只记录 provider/model/base URL。API key 按顺序从
`DECISION_HUB_DSH_API_KEY`、`DEEPSEEK_API_KEY`、`SUB2API_API_KEY`、
`OPENAI_API_KEY` 读取，并仅注入 DSH 子进程。安装可选依赖：

```bash
uv sync --extra dsh
```

R2-R-01 已固定 `deepseek-harness-sdk==0.1.1rc1`、bundled runtime server
`0.0.1` 和 `decision-research.v1:1d4ce1f40ab265e4` profile。离线测试覆盖
Session identity、权限、trace/result、结构化失败、deadline/timeout 和本地
handshake；真实 gpt-5.5 canary 已完成 3 tool calls/results、1 subagent、2 turns、
4 steps 和 Session 落盘。该证据只证明 Harness 接通，不证明研究质量。

R2-R-06C 将模型输出边界收缩为 synthesis-only，并将无人值守评测 profile 固定为
`reasoningEffort=low`。当前 candidate hash 是
`decision-research.v1:faf1b3115f7d339c`；它不覆盖上述 R2-R-01 历史 hash，也不表示
已经 Promotion。`inspect_profile()` 会拒绝偏离 `low` 的 unattended profile。

普通 CI 不访问外部 Provider。真实 canary 必须显式授权，使用临时 Session
目录并只输出脱敏计数；从仓库根目录使用模块入口：

```bash
DECISION_HUB_DSH_LIVE_CANARY=1 \
DECISION_HUB_DSH_MODEL=gpt-5.5 \
./.venv/bin/python -m tools.canary.run_dsh_research_canary
```
