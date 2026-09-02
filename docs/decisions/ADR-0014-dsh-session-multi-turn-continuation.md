# ADR-0014 DSH Session 多轮补证与消息幂等边界

日期：2026-09-01
状态：`accepted`（PRODUCT-CLOSEOUT-EXEC-02 已获 owner 整体实施授权）
关联目标：[通用底座与首个产品最终实施章程](../product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md) P1 / C3

## 决策

一个 durable Hub `run_id` 只关联一个确定性的 DSH `session_id`；每个外层
Evidence Round 必须关联一个不同、确定性的 DSH turn/request：

```text
run_id + immutable request_hash
  -> one deterministic dsh_session_id

run_id + immutable request_hash + generation(current_round)
  -> one deterministic request_id
  -> one immutable prompt projection
  -> one DSH turn result/callback
```

`generation` 是同一 DSH Session 内的 Hub evidence-round generation，不是新的
Run、Session 或重试随机数。`generation=1` 保持现有 request id 算法兼容；后续
generation 使用带轮次的确定性摘要。相同 generation 重放必须幂等；只有当前
generation 已完成后才能进入 `generation + 1`，跳号、回退或并发覆盖一律拒绝。

DSH 仍拥有每个 turn 内的 Agent/Tool/Subagent 循环；LangGraph 只在确定性
Sufficiency Gate 仍有 hard gap 时发起下一 evidence round。它不会创建第二套
ReAct 或 Supervisor。

## 背景

现有实现把 `current_round/evidence/target_gaps` 从 Run 的 `request_hash` 排除，以保证
同一 Run 不创建多个 DSH Session；但 `deterministic_request_id` 也只由
`run_id + request_hash` 生成。结果是第二轮虽然再次调用 Host `submit`，官方 Host
通过 `hasAcceptedPrompt(request_id)` 判断首轮提示已经存在，不会把包含新 gap 和新
Evidence 的 continuation prompt 再送给 DSH。

已有测试只断言两次提交拥有同一 Session/Request ID，因此证明了幂等，却没有证明
第二轮真实发生。这会把重复读取首轮结果误写成主动补证，违反 C3/E2 产品门。

## 候选方案

### A. 每轮创建新的 DSH Session

否决。会丢失 DSH 原生对话、Tool、Subagent 和 compaction 上下文，也让用户看到
一个 Hub Run 对应多个分散 Session。

### B. 沿用同一个 request id，让 Prompt 自己要求模型内部完成所有轮次

否决为唯一机制。DSH 内层应尽量完成自主工具循环，但 Provider/能力部分失败、Hub
Sufficiency 重新计算和进程恢复后仍需要受控 continuation；同 request id 会被 Host
正确去重，无法表达第二个 turn。

### C. 一个 Session、每轮一个确定性 request id 和不可变 Prompt

选择。它复用官方 DSH Session/Trajectory，同时让 Hub 的 bounded Evidence Round、
checkpoint 和恢复语义可验证。

## 持久化和回调规则

- `dsh_session_links` 保留一个 Run/Session 的最新协调投影；进入下一 generation 时
  只推进最新协调状态，不改写 Hub Research Round、Evidence 或 DSH JSONL 历史。
- `dsh_session_prompts` 改为 `(run_id, generation)` 复合主键；Prompt 只增不改，
  `request_id` 全局唯一，同一 `dsh_session_id` 可有多轮 Prompt。
- accepted/status/terminal callback 必须携带 generation。旧 generation 的迟到回调
  只作为 stale callback 忽略，不能覆盖当前 generation；未来 generation 拒绝。
- Host 的 terminal in-flight key 已包含 generation；同一轮并发 status/result/event
  只发送一次 terminal callback，失败后允许协调重放。
- Hub Prompt API 必须按 `run_id + generation` 精确读取，禁止“取最新 Prompt”导致
  竞态或错轮。

## 受影响契约和代码

- `contracts/schemas/dsh_host_bridge.schema.yaml`
- `DshSessionPrompt.generation`
- `DshSessionLinkService.deterministic_ids(..., generation)`
- `DshWebResearchRuntime` submission/prompt projection
- `extensions/dsh/decision-hub` Host correlation、Prompt fetch 和 result projection
- Alembic `0024_dsh_session_prompt_generations`

## BDD/TDD 验收

```text
Given 一个 Run 的第一轮 DSH turn 已完成但仍有 hard gap
When LangGraph 发起 current_round=2
Then run_id 和 dsh_session_id 与第一轮相同
And generation/request_id/prompt 与第一轮不同
And DSH Session 中确实出现第二条 user/message
And 第二轮只投影第二个 turn 的 Tool/Evidence/Result
```

```text
Given 同一个 generation 的 submit/status/result/callback 被重复或并发发送
When Host/Hub 协调
Then Prompt 只入账一次、terminal callback 只生效一次
And 不重复创建 Run、Session、Evidence、Artifact 或 Outbox
```

```text
Given generation=2 已开始
When generation=1 的迟到 callback 到达
Then callback 不覆盖当前 generation
And generation=3 在 generation=2 未完成前被拒绝
```

必须覆盖：generation 1 向后兼容、两轮成功补证、部分失败保留、事实不足停止、Host
重启恢复、callback 迟到/重复、migration upgrade 和旧 prompt 数据迁移。

## 后果

正面：真实第二轮与 DSH 原生 Session 历史一致；幂等边界从“一个 Run 一个 Prompt”
修正为“一个 Run 一个 Session、每轮一个 Prompt”，主动补证可以被回放和审计。

负面：Host bridge 和 Prompt 表需要一次兼容迁移；最新 link 是协调投影而不是每轮历史
表，完整 turn 历史仍由不可变 Prompt、Research Round/Trace 和 DSH JSONL 共同保存。

## 迁移与回滚

旧 `dsh_session_prompts` 全部迁为 `generation=1`，原 request id 不变。若新版本验收
失败，回滚到锁定的旧 DSH/Hub 版本并停止新 Run；不得删除已经写入的 generation>1
Prompt、Research Round 或 Evidence。active pointer 在本 ADR 实施期间保持 Fixed。
