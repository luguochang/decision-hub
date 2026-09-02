# ADR-0019：耐久工具预算与 Session Prompt 就绪边界

日期：2026-09-01
状态：accepted
Owner：Decision Hub Owner

## 背景

Official DSH Web live Run `run_773e50032b0f4c3d929f45f5028d53e1` 声明
`max_tool_calls=12`，但实际记录 `total_tool_calls=13`。现有实现只在一个 Harness round
结束后检查工具累计数，并在下一 generation 重新把完整预算交给 DSH；Gateway 在触网前
没有代码级原子许可。

同一 Run 还出现过 `research_session_not_found`。真实 JSONL 后续证明，失败调用由模型把
完整 Session ID 中间一段字符抄漏造成；同一轮使用完整 ID 的调用成功。因此它不能继续
被表述为已经证实的“首次 Session 数据库竞态”。Host 先 queue Prompt、后回调 Hub
accepted 仍是独立的就绪顺序缺陷，但不再冒充这次错误路由的直接根因。受信运行身份的
正式决策见 ADR-0020。

## 决策

1. DSH 继续是唯一内层 Agent Loop；LangGraph 只计算剩余预算和外层 durable generation；
2. 原始 `max_tool_calls` 持久化到 `dsh_session_links`，同一 Run 不可扩大；历史缺失值
   fail-closed，可在相同 canonical request re-admission 时补齐；
3. 新 capability `request_id` 在进入 adapter/网络前，必须通过 Hub 数据库原子 reservation；
4. reservation 一旦开始不退还；相同 `request_id` replay 不重复占位或触网；
5. 每个 generation 的 DSH Prompt 只显示 `original max - durable started` 的剩余调用数；
6. 超额调用返回 `research_tool_budget_exhausted` 和结构化 ErrorProvenance，实际 adapter
   invocation 永远不超过总预算；禁止只 clamp 显示计数；
7. Session link/accepted 必须先耐久可见，再 queue 业务 Prompt；Gateway 对未 accepted 的
   Session fail-closed；
8. 模型不得再复制或选择 `research_session_id`；该身份由 DSH 官方 Tool execution context
   注入，具体边界由 ADR-0020 定义。

## 否决项

- 只靠 Prompt 要求模型守预算：模型输出不是执行权限；
- 每个 generation 重置完整预算：会把 round 数乘进成本和网络调用；
- 在前端或 Query/View clamp 计数：掩盖真实执行；
- 在 LangGraph 新建 tool router/Supervisor：形成第二 Agent Loop；
- 使用进程内 Lock 作为正式边界：无法约束 MCP/worker 多进程。

## 后果

- 新增 migration `0027_durable_tool_budget` 和内部 tool-call reservation 账本；
- DSH Host bridge contract 增加 immutable `max_tool_calls`；
- 旧 Run/Trace 不改写，迁移只从既有 capability trace 初始化计数投影；
- 并发、跨进程、幂等 replay、下一 generation remaining budget 和 Prompt ready
  必须有 Red/Green 测试；
- 本决策不授予新网络、费用、插件或自动交易权限。
