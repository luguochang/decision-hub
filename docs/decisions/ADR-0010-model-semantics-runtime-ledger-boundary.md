# ADR-0010 模型研究语义与可信运行账本边界

日期：2026-08-30
状态：accepted

## 决策

DSH 模型只输出 canonical `research-synthesis-candidate.v1`，内容限定为
`request_id + CausalCase + HorizonDecision[]`。最终
`research-session-result.v1` 由 DSH adapter 从可信 Session 通知、MCP Tool Result、
canonical Request 和确定性策略组装。

以下字段禁止由模型作为事实源：

- Session、Runtime、Profile 和 Trace identity；
- Round、Plan、Tool invocation/result 和时间戳；
- Evidence payload、lineage、hash、三时间戳和 capability identity；
- Coverage、Stop Reason、tool/subagent/token/cost 计数。

模型可以决定研究问题、选择授权工具、根据结果调整研究方向，并形成因果链和独立
horizon 候选；但所有 `evidence_refs` 必须指向输入 Evidence 或同一 Session 中成功的、
允许的 canonical MCP Tool Result。未知引用以 `dsh_evidence_unattested` fail-closed。

## 背景

`R2-R-06C` 首次真实 PIT Canary 证明 DSH 能调用 Research MCP 并取得归档 Evidence，
但模型随后花费大量时间猜测完整 `ResearchSessionResult` 的 runtime metadata、Round、
Tool 和时间戳字段：

- `r2-r-06c-20260829T183320Z` 在约 102 秒后返回错误 shape，触发
  `structured_output_invalid`；
- 增加完整 Result schema 后，`r2-r-06c-20260829T184016Z` 已完成 8 次 MCP 调用并
  取得 2 条归档 Evidence，但在 180 秒硬上限前仍停留在运行账本组装，最终 timeout；
- 第二次失败曾被内层 `BaseException` 错误映射为 `dsh_runtime_failed`，现已保持
  cooperative cancellation，由外层记录为 `provider_timeout`。

这不是单纯 Prompt 长度问题，而是所有权错误：模型不应生成可由运行时直接观测的
事实。继续增加 schema、示例或超时会扩大 token、延迟和伪造面。

## 候选方案

### A. 继续要求模型输出完整 ResearchSessionResult

否决。运行元数据不是研究推理，模型生成既慢又不可信，并与 DSH trace、MCP Result
形成双写。

### B. 增加 timeout、max tokens 或结构化重试

否决作为根因修复。它只增加成本并延后相同失败；结构化 repair 不能把模型输出变成
可信运行账本。

### C. 模型输出语义候选，adapter 组装可信 Result

选择。它保留 DSH 的 Agent/Tool/Subagent/Session loop，同时遵守 Core/Harness、
candidate/Gate 和单一事实源边界。

## 后果

正面：

- 模型专注研究语义，不再猜测可观测运行数据；
- Evidence 直接来自 canonical MCP Result，字段无需模型复制，减少篡改和丢失；
- Session/Trace/计数/时间戳只有一个可信来源；
- DSH、Pi 或其他 Harness 都可复用相同“语义候选 -> adapter 结果”边界。

约束与代价：

- 新增一个 canonical `ResearchSynthesisCandidate` 契约及 Python/TypeScript codegen；
- adapter 必须校验 synthesis 的全部 Evidence 引用；
- Coverage、Gate 和 Stop Reason 仍由代码重算，不能接受模型自评；
- 当前 SDK 无可信聚合 token/cost 时继续记为 unknown。

## 迁移与回滚

- `ResearchSessionResult`、数据库、历史 Run、Artifact、Forecast 和 Evaluation 不迁移；
- 只改变 DSH adapter 的模型输出边界，Fixed/Replay Runtime 公共端口不变；
- 若新 Canary 失败，保留 Fixed baseline，不切 active pointer；
- 不通过放宽 Evidence attestation 或 PIT 规则回滚。

## 受影响契约与模块

- `contracts/schemas/agentic_research.schema.yaml`
- `packages/runtime_adapters/dsh_runtime/profile.py`
- `packages/runtime_adapters/dsh_runtime/result_mapper.py`
- `packages/runtime_adapters/dsh_runtime/runtime.py`
- Python/TypeScript generated contract mirrors

## 必须通过的验证

- canonical codegen 无漂移；
- synthesis 额外字段拒绝；
- request identity 不一致拒绝；
- 未知、越权或错误 lineage Evidence ref 触发 `dsh_evidence_unattested`；
- Evidence payload 只来自允许的同 Session MCP Tool Result；
- 外层超时记录为 `provider_timeout`，不误报 runtime crash；
- 同一 PIT case Canary 完成后才允许 12-case。
