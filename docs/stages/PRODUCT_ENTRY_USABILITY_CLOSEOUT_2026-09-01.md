# 产品入口可用性收口任务卡

版本：`PRODUCT-ENTRY-CLOSEOUT-2026-09-01.v1`
状态：`in progress`
关联阶段：`E2-L backend/runtime acceptance`
目标：完成固定 DSH Web 地址到交易员产品工作台的真实用户闭环。

## 1. 为什么要单独收口

后端研究链、Decision Hub 账本、DSH Session/Trajectory/JSONL、后台 scheduler/worker
和研究报告投影已经有真实运行证据。但是上一轮验收只证明了“知道工作区和 Run ID 的工程人员”
可以打开结果，没有证明普通用户从固定入口打开后能自然看到交易员工作台和最近结果。

当前缺陷已经在官方 DSH Web 复现：

- 固定入口可以恢复到 `Codex` 或一个无 Run 关联的旧会话；
- 用户需要手工展开侧栏、选择 `Crypto Macro Trader`，再判断哪个同名会话有结果；
- 研究报告页在“无 Run”时会出现误导性的“研究报告生成中”；
- 终态 Run 的 status 已有失败、coverage、stop reason 和 capability failure 时，报告详情暂时不可用仍缺少可读降级投影；
- 侧栏中的后台复查会话和人工主线标题重复，不能快速区分。

因此本任务把状态拆成两道门：

```text
E2-L backend/runtime acceptance = 已通过
E2-L product-entry usability      = 本任务完成后才可通过
```

不把入口缺陷改写成“用户不会使用”，也不以测试通过替代页面验收。

## 2. 产品目标与非目标

### 2.1 目标

1. 打开本次产品启动器输出的固定 DSH URL 后，默认上下文落到 `Crypto Macro Trader` 工作区。
2. 若该工作区已有非空研究会话，自动打开最近的非空会话；若没有，则创建并打开一个空会话。
3. 用户在同一官方 DSH Web 内可以看到原生 Chat、Trajectory、研究报告三个视图；研究报告使用同一 `run_id <-> dsh_session_id` 关联。
4. 没有关联 Run 时不显示“报告生成中”；终态但报告详情暂时不可用时，显示 status 中已经确认的终态、coverage、停止原因和失败来源。
5. 后台复查继续由 scheduler/worker 自主执行，用户不需要手动轮询或输入 Run ID。

### 2.2 非目标

- 不 fork 或修改 DSH 上游 Web。
- 不替换官方 Chat/Trajectory renderer，不实现第二个 Agent Loop、账本、DTO 或前端。
- 不删除、重命名或改写历史 DSH Session/JSONL、Run、Evidence 和 Artifact。
- 不放宽 Evidence、PIT、authority、Sufficiency 或 Gate 规则。
- 不把 `no_trade` 研究性结果包装成交易建议，不引入自动交易、ASR、PPT 或第二领域。

## 3. 复用的官方能力与允许路径

本任务只使用固定上游已经公开的能力：

- Client `ctx.sessions.list`, `ctx.sessions.refresh`, `ctx.sessions.create`, `ctx.sessions.open`；
- Client `ctx.workspaces.list` 的官方 Workspace 快照；
- `ctx.slots.inject('conversation.input.dock', ...)` 和 `ctx.slots.inject('conversation.view', ...)`；
- Host bridge 已有的 `/api/decision-hub/status` 和 `/api/decision-hub/report` 同源投影。

客户端只保存短生命周期的视图状态；业务事实继续由 Hub Ledger 所有。不得通过假 URL、侧栏 DOM
点击、修改上游源码或复制 JSONL 达到默认视图效果。

## 4. BDD 验收场景

### Scenario A：固定入口默认进入交易员工作区

```text
Given 官方 DSH Web 已通过本次启动器输出的认证 URL 启动
And Workspace Controller 已注册标题为 Crypto Macro Trader 的工作区
When 用户首次打开页面，当前持久选择为空或属于其他工作区
Then Client 自动打开该工作区最近的非空会话
And 页面可见交易员研究上下文，不要求用户手工展开侧栏或输入 Session ID
```

### Scenario B：没有历史研究会话

```text
Given Crypto Macro Trader 工作区没有非空会话
When Workspace/Session 快照进入 ready
Then Client 通过官方 sessions.create({workspaceId}) 创建一个空会话并 sessions.open(id)
And 研究输入控件可见，用户只需输入研究目标并点击一次“建立研究任务”
```

### Scenario C：当前会话已有 Run

```text
Given status(session_id) 返回确定性的 run_id
When 研究报告页加载
Then 显示同一 Run 的状态、Gate、coverage、horizon、evidence 和 trace 入口
And 不显示第二个“建立研究任务”主按钮
```

### Scenario D：无 Run 的普通会话

```text
Given status 返回 run_id = null 且没有错误
When 研究报告页加载
Then 该页显示“当前会话尚未关联正式研究任务”或由输入 dock 提供入口
And 绝不显示“研究报告生成中”
```

### Scenario E：终态但详情投影暂不可用

```text
Given status 已返回 failed/rejected/research_only/completed 等终态
And report endpoint 暂时返回 409/503 或详情为空
When 研究报告页加载
Then 显示 status 已确认的终态、coverage、stop reason、failure code 和可重试性
And 不伪造 thesis、horizon、Evidence 或方向性结论
And 不再继续无限轮询
```

### Scenario F：刷新和后台复查

```text
Given 页面刷新或 DSH Web 重连
When 官方 Session/Workspace 快照恢复
Then 仍回到 Crypto Macro Trader 的最近有效研究上下文
And scheduler 创建的 child Run 在研究列表中可区分为 scheduled recheck
And 不重复创建主 Run，不改变历史记录
```

### Scenario G：响应式和错误可见性

```text
Given 1440px、1280px、768px 和 375px 视口
When 页面处于空会话、研究中、成功、失败和终态详情暂不可用
Then 无横向溢出、无 console error、错误来源和下一步清晰可见
```

## 5. TDD 测试计划

先在 `extensions/dsh/decision-hub/tests/client.spec.ts` 写纯函数测试，再实现：

- `productWorkspaceOf` 能从 Workspace 快照定位目标工作区；
- `latestProductSessionOf` 优先返回目标工作区最近非空、未归档会话；
- `reportStateOf({run_id:null}, null, null) === 'idle'`；
- 终态 status + 详情不可用返回 `terminal_pending`，不返回 `loading`；
- status fallback 保留 `coverage_status`、`hard_coverage_ratio`、`stop_reason_detail` 和 failures；
- 当前会话已受管时隐藏重复 intake，失败只允许幂等 retry；
- workspace/session ready 后只调用一次 `sessions.open` 或 `sessions.create`；
- session list 尚未 ready、目标工作区不存在、创建失败时保持可读的 bounded 状态，不循环创建。

专项命令：

```bash
pnpm --dir extensions/dsh/decision-hub test -- --run
pnpm --dir extensions/dsh/decision-hub build
```

回归命令：

```bash
pytest -m "not live"
python -m tools.docs.check_module_docs
python -m tools.contract_codegen check
git diff --check
```

最终浏览器验收必须从全新页面或清空页面状态开始，不能把已经手工切换到目标工作区的页面
当作默认入口证据。必须记录固定入口、目标工作区、实际 Session、Run、截图和 console 状态。

## 6. 完成门

只有以下条件全部满足，才把 `E2-L product-entry usability` 标为 `passed`：

- 固定 URL 首屏自动进入交易员工作区；
- 一步可开始研究；
- 最近有效 Run 一步可达且报告页不误报“生成中”；
- 终态失败和详情暂不可用均 fail-closed 且可读；
- DSH 原生 Chat/Trajectory/JSONL 仍是同一 Session；
- Python、插件测试/build、桌面/窄屏浏览器 E2E 和 console 检查通过；
- 本任务卡、模块 README、`IMPLEMENTATION_STATUS`、`CHANGELOG` 和验收记录同步。

在该门通过前，产品状态只能写：

```text
backend/runtime: passed
product-entry: in progress / blocked
pilot usability: not ready
```
