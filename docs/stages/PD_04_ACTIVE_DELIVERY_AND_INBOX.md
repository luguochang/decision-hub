# PD-04 主动交付与 DSH Inbox

状态：`completed / engineering and runtime evidence verified`  
日期：2026-09-05（Asia/Shanghai）  
上级方案：[`PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md`](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)

## 1. 阶段目标

在不新增 Agent Loop、业务账本或第二个用户入口的前提下，把现有的 Source、EventWatch、
DurableResearchWorker、DSH Session、Artifact、Outbox 和 recheck 连接成一条可恢复的主动价值链：

```text
calendar/feed/news/manual TextEnvelope
  -> 统一 Event identity + dedupe/revision
  -> EventWatch + future baseline
  -> durable research.v1 Run
  -> DSH 唯一 Supervisor/Agent Loop/Session
  -> typed facts + approved documents + semantic Gate
  -> Artifact/Forecast + DSH Inbox/Report
  -> Outbox notification + scheduled recheck
```

用户不需要先在聊天框提问，打开 DSH 工作台即可看到系统主动发现、运行、完成或失败的研究。
人工提问仍是同一条链的入口之一，不成为主动运行的前提。

## 2. 非目标与硬边界

- 不实现新的 Supervisor、ReAct loop、计划器或多 Agent 框架；这些只由 DSH 拥有。
- 不让 LangGraph、DSH 或前端写业务账本；Hub 仍是唯一写入者与 Gate。
- 不新建消息队列、Redis、Temporal、第二套调度器或第二个聊天前端。
- 不接 ASR、PPT、第二领域、多用户、自动交易或自动 Promotion。
- 不因分钟级宏观 Provider 尚未授权而降低 Gate、伪造 baseline 或把 current 值标成事件窗口。
- 不承诺数据库层面的分布式 exactly-once；本阶段承诺单 owner 部署下可审计的
  `at-least-once delivery + dedupe_key effective-once`。

## 3. 现有复用点

| 能力 | 现有唯一实现 | 本阶段处理 |
|---|---|---|
| 来源汇入与 Event admission | `SourceIngestionService` | 复用，不建新 ingestion |
| 未来事件窗口 | `EventWatchService` | 复用 durable slot、baseline 和 late-event 语义 |
| 常驻轮询 | `RealtimeScheduler` | 复用单进程 coordinator，补产品状态投影 |
| Run 生命周期/恢复 | `DurableResearchWorker` + LangGraph checkpoint | 复用 lease、deadline、resume |
| Agent Loop/Session/Trajectory | DSH | 唯一 Harness，不在 Hub 复制 |
| 事务提交 | `CommitDecisionService` | 复用 Artifact/Forecast/Outbox/recheck 同事务 |
| 通知 | `NotificationDispatcher` | 复用 dedupe/retry/terminal failure |
| 查询 | `ResearchQueryService` | 新增 Inbox 聚合投影，不直接查 SQL 的 UI |
| 用户入口 | 官方 DSH Web + decision-hub extension | 新增 Inbox/主动报告导航；不复制 DSH Web |

## 4. 必须补齐的真实缺口

1. 当前 DSH 页面主要围绕“本会话关联的一个 Run”，没有一个 canonical 主动研究 Inbox。
2. Event、Run、DSH Session、Artifact、通知和 child recheck 虽各自可查询，但没有统一产品投影。
3. feed revision、重复 scheduler tick、worker 重启和重复通知需要在一条 E2E 中共同证明幂等。
4. 产品状态必须区分 `watching / queued / researching / report_ready / research_only / failed`，
   不能把“无 Run”显示成加载中，也不能把失败显示成报告成功。
5. 未来 calendar 预热与突发/迟到事件必须在 Inbox 中显式展示 `baseline_status`，避免伪 30m。

## 5. 契约与所有权

新增一个 canonical `ResearchInboxItem` / `ResearchInboxView` 产品投影，字段只引用既有资产：

- `event_id/watch_id/run_id/dsh_session_id/artifact_id/parent_run_id`；
- `admission_origin/event_family/scheduled_at/created_at/updated_at`；
- `status/baseline_status/gate_status/report_available`；
- `headline/summary/next_recheck_at/notification_status`；
- `domain_pack_ref/role_profile_ref` 只作版本引用，不复制 Pack 内容。

契约先写入 canonical YAML，再 codegen Python/TypeScript/Zod，禁止前后端双写。Inbox 是只读
Query View，不拥有新的状态机或表；状态由 EventWatch、Run、Artifact、DSH link、Outbox 聚合得到。

统一 Event identity 继续使用 `SourceIngestionService` 已有 content hash、source cursor、revision 和
Event idempotency 规则；不得在 calendar、news、manual adapter 分别生成不同身份算法。

## 6. 后端实现落点

```text
contracts/schemas/research_product_view.schema.yaml  # Inbox/PD-05/PD-06 单一投影契约
packages/query_views/research/service.py              # Inbox 聚合，只读
apps/hub_api/main.py                                  # /v1/research/inbox
apps/research_mcp/main.py                             # 同一查询能力给 DSH extension
packages/kernel/.../scheduler.py                      # report counters，不复制状态机
packages/kernel/.../commit.py                         # 保持单事务与 dedupe
extensions/dsh/decision-hub/src/client/index.js       # Inbox/报告入口
```

若现有表已能导出字段，不新增 migration。只有无法由现有可信资产推导且必须耐久的数据，才能先立
ADR 再新增表；本阶段默认不新增表。

## 7. 前端产品形态

DSH Web 仍是唯一日常入口：

- 顶部/侧边 Extension 区显示“主动研究”；
- 默认按更新时间列出进行中、报告就绪、research-only、失败和待复查项目；
- 点击项目进入同一 DSH Session 的 Report/Trajectory，不建立第二套 chat；
- 列表只显示人类可读状态、事件、时间、baseline、Gate、下一复查；raw JSON 默认隐藏；
- 无项目时显示“暂无主动研究”，不显示“报告生成中”。

Decision Desk 只做管理后台和跨 Run 查询，不复制 Inbox 聊天体验。

## 8. 失败与恢复语义

- scheduler 重复 tick：同一 source item/Event/Run/Watch 不重复创建。
- worker 在 DSH accepted 前崩溃：按 Run lease 重领，不取消不存在的远端 Session。
- DSH accepted 后超时：best-effort cancel，保留 Session/Trace/error provenance。
- Artifact 已提交后重试：返回原 artifact，不重复 Forecast/Outbox/recheck。
- 通知 adapter 暂时失败：有界退避；同一 dedupe key 最多有效投递一次。
- report detail 暂不可得但 Run 已终态：展示 canonical status fallback，不回到无限 loading。
- baseline 不可得：Inbox 标记 `retrospective_only/baseline_unavailable`，方向 Gate 保持关闭。

## 9. BDD

```gherkin
Scenario: 未来官方事件主动交付
  Given calendar 在 T-30m 前创建统一 Event 和 EventWatch
  And sampler 保存到期窗口事实
  When official feed 到达并触发 durable Run
  Then worker 创建或恢复一个 DSH Session
  And Artifact、Inbox、通知和 recheck 可由同一 Run 追溯
  And 用户无需先提问即可在 DSH 看见报告

Scenario: 重启和重复事件不产生重复交付
  Given scheduler 已 admission 该 source item
  And worker 在提交前后分别发生一次重启
  When scheduler 和 worker 再次运行
  Then Event、Run、Artifact、Outbox、child recheck 各只有一个逻辑实例

Scenario: 迟到事件没有 baseline
  Given 事件发生 11 小时后才被发现
  When 系统生成 research-only 报告
  Then Inbox 显示 retrospective_only 与 baseline_unavailable
  And current snapshot 不得冒充 30m event window

Scenario: 终态失败不伪装成报告
  Given DSH Run 已失败且没有 Artifact
  When 用户打开 Inbox
  Then 项目显示 failed 和稳定错误分类
  And 不显示 report_ready 或无限加载
```

## 10. TDD 与验收

- Contract：codegen、旧数据库空字段兼容、未知枚举 fail-closed。
- Query：watch-only、queued、running、artifact ready、failed、recheck child、notification 状态。
- Worker：重复 tick、lease recovery、commit recovery、Outbox retry/dedupe。
- DSH Plugin：Inbox 空态/运行/终态/失败、点击 Session、无 raw JSON。
- E2E：future fixture 从 watch -> facts -> DSH replay -> report -> notification -> recheck。
- Browser：桌面/移动、console=0、状态和 Hub canonical view 一致。

## 11. 退出门

- [x] calendar/feed/news/manual 使用同一 admission 入口；
- [x] future baseline、late-event 状态在 Inbox 可读；
- [x] 自动 Run 能创建/恢复 DSH Session；
- [x] DSH Inbox 无需人工提问即可发现主动研究；
- [x] report/notification/recheck 具备同一 lineage；
- [x] 重启/重复 tick/通知失败行为测试全绿；
- [x] 不新增第二个 Agent Loop、账本、调度器或前端框架；
- [x] 普通测试离线，全量质量门和执行证据落日志。

完成证据统一见 [PD 实施与自测执行日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)：
当前工作树全量 Python `551 passed`，DSH Extension `69 passed + build`，Decision Desk
`10 passed + build`；隔离实例已验证自动 Run、Inbox、同一 Session 的 Report/Trace、通知和
scheduled recheck。真实历史回溯样本仍为 `research_only/baseline_unavailable`，没有被改写为
具备事前窗口。

通过本卡只证明主动交付工程闭环，不证明宏观事实充分、预测准确或可交易。下一阶段为 PD-05。
