# ADR-0020：DSH Research Tool 受信运行身份

日期：2026-09-01
状态：accepted
Owner：Decision Hub Owner

## 背景

live Run `run_773e50032b0f4c3d929f45f5028d53e1` 的真实 DSH JSONL 记录了一个
`research_session_not_found`。正确 Session ID 为：

```text
dsh_f66aac8b72d82bc0de3b4940290d1d2607ac8cdc2db0c8a7ebacfc7686afb615
```

失败工具调用缺少了中间的 `8cdc2db0c8a7eb`。同一轮携带正确 ID 的 Official、Market 和
Search 调用均成功。这证明直接根因是产品把运行时身份放进 Prompt，并要求模型逐字复制到
MCP 参数；模型输出被错误地当成了路由和授权事实。

Official DSH `ToolDefinition.execute(args, exec)` 的 `exec.agent.id` 是当前 Agent/Session 的
官方受信身份。该公开 seam 足以修复问题，不需要 fork DSH、不需要从其他字段猜 Session，
也不需要建立第二套 Agent Loop。

## 决策

1. `research_session_id` 从模型可见的 Decision Hub Research Tool 参数中删除；
2. `decision-research` Agent 只看到 DSH Native Plugin 注册的业务 Research Tool；原始
   `mcp__decision_research__research_capability_execute` 不得同时暴露给该 Agent；
3. Native Tool body 只从 `exec.agent.id` 注入 `ResearchCapabilityQuery.research_session_id`；
   `exec.agent` 缺失时 fail-closed，禁止使用默认值；
4. `request_id`、capability 业务参数仍由 Agent 提议；Hub Gateway 继续验证 Session link、
   generation、Run 状态、deadline、权限、PIT 和耐久预算；
5. Native Tool 与 Hub/Research service 之间继续使用 canonical
   `ResearchCapabilityQuery/ResearchCapabilityResult`，两端 Zod/Pydantic 校验，禁止第二 DTO；
6. Session 身份不得从 `request_id`、Run 前缀、Prompt 文本、当前最新 Run、Workspace 中的
   唯一候选或模糊字符串匹配推断；
7. Host 必须先创建空的官方 Session 并取得 Hub accepted/link 耐久确认，再 queue 业务
   Prompt；这是独立防御门，不代替受信身份注入；
8. 独立 MCP canary 和非 DSH 客户端可保留原始 canonical tool，但必须显式提供自己的
   受信 Session credential；不能成为 decision-research Agent 的旁路。

## 否决项

- 加强 Prompt，要求模型“仔细复制”：不能把概率行为变成身份边界；
- 对错误 Session ID 做前缀、编辑距离或唯一候选纠正：会产生跨 Run 错路由风险；
- 根据 `request_id` 反查当前 Run：模型同样可以抄错或伪造 request；
- 修改 DSH 上游 MCP client 或复制 DSH Agent Loop：破坏升级边界；
- 同时暴露原始 MCP tool 和受信 wrapper：模型仍可绕开 wrapper；
- 让 Hub 接受不存在的 Session 后补链：会污染历史账本。

## 实现约束

```text
DSH Agent
  -> decision_hub_research(model business args only)
  -> ToolDefinition.execute(args, exec)
  -> require exec.agent.id
  -> inject canonical research_session_id
  -> Zod ResearchCapabilityQuery validation
  -> Research service / Durable Gateway
  -> Pydantic validation + link/generation/PIT/budget checks
  -> canonical ResearchCapabilityResult
```

Red/Green 必须证明：

- 模型 schema 没有 `research_session_id`；
- 完整 Session ID 由 `exec.agent.id` 注入且原样到达 Gateway；
- agentless 调用、未知 Session、terminal Session 和 generation mismatch 都 fail-closed；
- 原始 MCP tool 不在 decision-research Agent 的模型可见工具集中；
- accepted callback 失败时 Prompt 不入队；
- duplicate/restart/generation continuation 不重复入队；
- 插件 build/test、官方 DSH Session JSONL 和 Hub link 共同证明实际运行使用的是 wrapper。

## 后果

- DSH Native Plugin 新增一个 Agent-plane Research Tool 模块；
- decision-research preset 从“直接暴露原始 MCP tool”改为“受信 wrapper 作为唯一业务能力口”；
- Prompt 删除 Runtime session id 和复制说明，降低 token 与错误面；
- Hub Ledger、MCP canary、Capability Gateway、Evidence/Gate 所有权不变；
- 后续 ASR、PPT 或第二领域复用同一原则：模型提交业务意图，运行身份和授权由 Harness
  execution context 与产品控制面注入。
