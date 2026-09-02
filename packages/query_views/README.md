# Query Views

## 目的
从 Kernel 只读表组装 Decision Desk 所需的 Overview、Inbox、Artifact、Health、Operations、Evolution Job 和 Research Run/Result/Trace DTO。

## 不负责
不保存第二份业务账本，不做 Gate、Outcome 计算或 Provider 调用。

## 公开契约
`DecisionDeskQueryService.summary()`、`OperationsQueryService.overview()`、`ResearchQueryService.list_runs()` 和 `ResearchQueryService.get()`。

Operations 只读取 durable heartbeat/source/capability/job 事实。三个预期服务从未产生 heartbeat 时使用 canonical nullable 字段显示 `offline`，不伪造实例、启动时间或最近心跳；runtime 的 fake/replay/provider、Provider 配置和 live canary 分开呈现。

Research 详情在最终 `ResearchSessionResult` 尚未产生时仍可从 durable
`research_trace_events` 和 `research_evidence` 投影进度：工具调用、轮次、已保留证据和
逐 capability 的 `error_code/origin/cause_code/retryable` 不会因为后续模型步失败而归零。
只有出现实际轨迹或终态失败时才显示保守的 `insufficient`/停止状态；刚入队且尚未开始的
Run 保持“等待研究”，避免把未开始误读为研究失败。

## 最近验证
`DecisionDeskQueryService.summary()`、Research Query/View 与 Kernel Inspector DTO 已通过 API/E2E/Research 测试；Research Trace 使用连续 sequence 支持 `after`/SSE 续接。没有 `ResearchSessionResult` 的 Run 顶层失败也会以 `research.runtime` 投影到 DSH business status，保留 error code、origin、cause 和 retryability，避免页面只显示空泛的 `failed`。Query View 不读取 Graph state、DSH raw JSON、Provider response 或前端私有 DTO。
