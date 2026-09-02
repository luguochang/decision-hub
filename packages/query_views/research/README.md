# Research Query View

## 目的

从 Run、Snapshot、Research Result、Evidence、Trace 和 scheduled child Run 组装规范化研究列表与详情，供 API、SSE 和 Decision Desk 使用。

## 边界

- 只读取业务账本，不读取 DSH JSONL、LangGraph checkpoint 或 Provider 原始响应。
- 不执行研究、不修改 Gate、不创建 Run，也不保存第二份领域事实。
- DTO 只使用 `agentic_research.schema.yaml` codegen 结果；前端不得再手写镜像。
- 有 DSH Session Link 时，工具进度使用 durable counter；无最终 Result 时只把
  `capability_call` 投影为业务工具，不能把对应的 `dsh_tool_call` 包装轨迹重复计数。
- DSH 包装轨迹继续保留在 Trace/Trajectory/JSONL；若 Gateway 已记录结构化 capability
  失败，通用 `decision_hub_research` 包装失败不再作为第二条用户可见失败。

## 公开接口

- `ResearchQueryService.list()`：Research Command Center 列表。
- `ResearchQueryService.get(run_id)`：Plan/Evidence/Tools/Sufficiency/Causal/Horizon 详情。
- `ResearchQueryService.trace(run_id, after=...)`：SSE 增量读取的规范化轨迹。
