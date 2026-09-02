# ADR-0018 DSH Client 受管 Session 选择边界

日期：2026-09-01
状态：accepted
决策：Decision Hub Client Plugin 必须通过官方 Client Session Controller 的
`sessions.refresh()` 和 `sessions.open(sessionId)` 进入 Host 已创建的受管 Session；不得把
`?session_id=` 当成固定上游 `0.1.2-alpha.2` 的路由契约。

## 背景

E2L-F 真实浏览器复验中，typed intake 能建立 durable Run，Host 也能创建并关联确定性的
DSH Session，但 Client 使用 `location.assign('/?session_id=...')` 后，地址栏虽然变化，
Conversation 仍停留在原来的 blank Session。固定上游源码没有读取 `session_id` query 的
客户端路由；因此 URL 变化不是 Session Controller 状态变化，导致页面继续显示第二个
“建立研究任务”，Chat/Trajectory 也不属于目标 Run。

上游已经公开 `ISessions` 服务：`refresh()` 拉取 Host-authoritative Session list，
`open(id)` 选择已存在 Session，`clear()` 进入 no-session 状态。`ui-workspace` 官方实现也用
`sessions.open(sessionId)` 打开侧栏 Session。这是可以随上游版本校验的正式 seam。

## 候选方案

1. **继续使用 `?session_id=`。** 当前上游不消费该参数，地址栏和 UI 状态会分裂，否决。
2. **点击侧栏、修改 DOM 或写 localStorage selection。** 依赖 UI 结构或私有持久化，升级
   不可控，否决。
3. **fork/patch DSH router。** 违反官方上游不修改约束，否决。
4. **注入官方 `sessions` 服务并调用 `refresh/open`。** 与上游自身 Workspace UI 使用相同
   契约，可测试、可 fail-closed，接受。

## 决策

1. Client Plugin 声明 Cordis `sessions` 服务依赖，并在 client module graph 中声明
   `@deepseek-ai/dsh-api-session-controller` 注入关系。
2. status 返回目标 `dsh_session_id` 且不同于当前 slot-owned `sessionId` 时，先
   `await sessions.refresh()`，再确认 `sessions.binding(id)` 可解析，最后 `sessions.open(id)`。
3. refresh、binding 或 open 失败时不改 URL、不点击 DOM、不清本地选择；保留当前 durable
   Run 状态并继续有界轮询，UI 显示稳定错误来源。
4. 当前 Session 身份只来自 DSH slot 的标准 `sessionId`/`SessionSnapshot.sessionId`；不从
   `location`、DOM、JSONL 或 Hub 文本反推。
5. `sessions.clear()` 仍由官方“新建会话”拥有。Client Plugin 不截获该按钮；新的 blank
   view 可以建立一条新正式研究主线。
6. Host、Hub 和 Ledger 的 `run_id <-> dsh_session_id` 关联不变，历史 Session/JSONL 不迁移。

## 后果

- 受管 Session 选择由 DSH 自己的 current-selection store 驱动，Chat、Trajectory、报告和
  composer slot 会共享同一 Session scope。
- Client Plugin 增加一个官方 DSH service dependency，但不复制 Session Controller 或路由。
- Host-created Session 可能晚于 Client list baseline 出现，因此 refresh 是必要同步边界；
  未发现目标 Session 时必须 fail-closed，而不是回到伪路由。
- DSH 上游升级若移除/变更 `ISessions.refresh/open/binding`，版本验收必须失败并阻止启动。

## 迁移与回滚

- 删除 `managedSessionUrlOf()` 和所有 `location.assign('?session_id=...')` 逻辑。
- 无数据库 migration，不改写历史 Run、Session 或 JSONL。
- 回滚到 URL 方案会重新引入已复现的状态分裂，因此不允许作为降级路径；失败时保持当前
  Session 并显示“受管 Session 暂不可打开”。

## 受影响实现

- `extensions/dsh/decision-hub/src/client/index.js`
- `extensions/dsh/decision-hub/package.json`
- `extensions/dsh/decision-hub/tests/client.spec.ts`
- `infra/dsh/upstream.lock.json`（只作为兼容性事实源，不修改）

## 受影响测试

- 目标 Session 尚未在本地 list：先 refresh，binding 成功后 open；
- 目标已是当前 Session：不 refresh、不重复 open；
- refresh 后仍无 binding：稳定失败，不改 URL、不打开其它 Session；
- 官方“新建会话”仍显示 typed intake；
- 受管 Session 不再显示第二个 intake，Chat/Trajectory/报告使用同一 Session scope。
