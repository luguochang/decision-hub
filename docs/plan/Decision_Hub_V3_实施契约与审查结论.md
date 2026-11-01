# Decision Hub V3：实施契约、并发安全与质量验收

日期：2026-09-08。源码基线：`1f5a31da0da012c628737e5fa3bf8a3d04b344f1`。
状态：**实施规格和可运行参考，不是已修改仓库或已上线报告。**
本文件替代 V2 的执行安排。V2 保留为历史来源，不再作为第二份开发指令。

## 0. 审查真实性与本次实际完成

本会话没有可调用的独立子 Agent 调度工具。已检查连接器与插件目录，未获得可直接执行的独立审查接口。**没有启动后台多 Agent，没有把同一模型的不同视角写成独立审查结果。**

本次完成：V2 全文读取；基线源码的 API、RunService、worker、提交、事件与额度记录、数据库模型、执行器、Compose、领域预算核查；三个隔离风险复现；参考控制层和并发测试。完整仓库归档下载因容器 DNS 失败，未运行原仓库完整测试，未调用真实 DSH、LLM、数据供应商或 Langfuse。

`tests/test_current_probes.py` 中两个探针执行复制的原方法，依赖由测试替身提供；第三个复现原序号分配算法的跨实例竞争。它们不是完整系统复现。`reference/` 是可执行语义规格，不是另造一套正式业务账本，实施时把行为移入既有 RunService、CommitDecisionService 和 Gateway。

真正独立审查的分工和结果格式见 `reviews/`，状态为 NOT_RUN。不能把这些文件的存在当作审查已执行。

## 1. 本轮裁决：V2 不可原样交给编码 Agent

V2 选型方向保留，但缺少可执行的事务、状态、权限和恢复契约。并发前提没确定就先做 Team/可视化，会放大现有缺陷。

**本轮只允许固定 owner 的显式命令触发研究。定时研究、来源自动触发、自动复查、自主演化关闭。** 已接受任务的恢复、在任务内补查、额度核对和技术观测重试，不属于新的用户触发；但不能借它们新建无限研究任务。

保留：DSH 执行与公开 Client 工作区、Hub 权威账本、LangGraph 外层检查点、已有 Provider、LoongSuite→Langfuse 观测路线。
不新增：Redis/Celery/Kafka/Temporal、多租户、公开插件市场、第四个平台、第二个自由规划 Supervisor、自动交易。

初期参数是待测默认值，不是性能保证：

| 参数 | 初始值与作用域 |
|---|---|
| 活跃研究 root Run | 1，全服务部署范围；不是一个浏览器标签一个 |
| 待开始研究 | 最多 5；相同幂等请求不重复占位 |
| 独立研究 leaf Task | 最多 2；综合/协调等待时不占 leaf 许可 |
| Gateway 外部 HTTP | 最多 4；单 Provider 最多 2，供应商限制更小则取更小值 |
| DSH 原生搜索 | 另一个明确的上限 1；不能声称受 Python Semaphore 控制 |
| 重解析作业 | 最多 1 个受限子进程；超时可终止，不能无限起线程 |
| 手动数据导入 | 最多 1 个低优先级作业；不触发研究或通知 |
| 研究租约 | 建议 60 秒，每 15 秒续约；上线前验证事件循环延迟和 DB 争用 |
| 任务执行总预算 | 先保留现有 480 秒作为实验初值；排队过期时间独立，恢复不能重置 |
| 模型 token/费用 | 保留显式上限，最终数值按实际模型与数据源授权；没有可界定费用的路线不默认启用 |

单用户 ≠ 无并发。重复点击、两个标签页、进程重启、子任务和迟到回调仍然并发。

## 2. 新发现与反驳：先改真正会破坏结果的部分

| ID | 级别 | 依据 | 裁决与动作 |
|---|---|---|---|
| A01 | 阻断 | `apps/hub_api/main.py` 的研究提交没有 owner 鉴权；`require_owner` 只检查非空及可选字符串相等 | 不是密码/会话认证。所有读写与事件流必须走可信身份，废弃信任 X-Owner-Id。[S1] |
| A02 | 阻断 | `commit.py` 会依据 horizons 创建 `scheduled_recheck` Run；claim 不按手动模式隔离 | 只停 scheduler 不够。提交层不建复查作业，admission 与 claim 都校验 origin，已有自动队列进入暂停/隔离。[S2][S3] |
| A03 | 阻断 | worker 默认 lease=240；pack deadline=480；worker/执行器路径没有续租调用 | 存在活跃任务变成可回收任务的窗口。保留已有 renew，接入 lease supervisor，加 epoch fencing。[S3][S4][S5] |
| A04 | 阻断 | worker 在 commit 后调用 `_record_completed`，仍在同一宽泛 try 内；异常时 set_status(failed) | 已提交产物可能与失败状态并存。原方法隔离测试复现。业务提交和派生记录错误分离。[S4][S6] |
| A05 | 高 | `ResearchObservabilityService` 实例 Lock + MAX(sequence)+1；有独立 API/Gateway/worker 写者 | 进程内/实例内锁不保护多个写者。唯一约束会阻止重复号，却可能使合法事件失败。改成同事务分配 DB 序号。[S7] |
| A06 | 高 | `RunService.set_status` 无 epoch/owner/expected-state 条件；commit 无租约 fence | 旧 worker 能改新执行状态。所有权威写入接受 fence；终态只能合法转移。[S2][S3] |
| A07 | 高 | 旧 `/v1/observations` 仍通过 FastAPI BackgroundTasks 执行，与 durable research 并存 | 关闭产品 legacy 执行入口，所有正式研究只入库排队；删除 UI 不等于删除绕行 API。[S1] |
| A08 | 高 | async API/SSE 调用同步 query；SSE 约每250ms读取完整详情 | 存在事件循环阻塞和读放大风险，未做生产压测。短事务线程边界、轻量事件读取、分页和固定连接。[S1] |
| A09 | 设计阻断 | V2 没定义原生提交超时后，究竟已运行还是未运行的恢复行为 | 增加 dispatch intent + 三态 lookup + receipt；unknown 不盲目重派，恢复不等于重跑。 |
| A10 | 设计阻断 | V2 全局预算没有落到 DSH 原生模型/搜索的实际拦截点 | Gateway 额度已有实现但不代表覆盖所有请求。明确每条计费路径的预留/结算/未知状态；无法拦截则停用该路径。 |
| A11 | 高 | V2 任务、claim、review都有描述，但没有权威状态与合并时点 | 冻结任务输入快照；叶任务并发结果持久后再建立 review 快照；不直接并发修改共享字典。 |
| A12 | 高 | V2 说版本锁定，但没有旧 checkpoint 与新代码兼容矩阵 | bundle_manifest 固定 graph/contract/tool/parser/model/规则；不匹配不可直接 resume。 |

不能夸大的点：
- RunService 已有条件 UPDATE 领取和 renew_lease，并非从零写消息队列。[S3]
- 工具额度 reserve 已有原子更新与唯一键冲突处理，不能删除后重写成内存计数。[S7]
- Compose 端口绑定 loopback，不是已确认公网未授权访问；但本机接口与进程仍需要身份边界。[S8]
- `_evaluate_terminal_safely` 有派生评价隔离，主要遗漏是 `_record_completed` 等仍可进入统一失败分支。[S4]
- SQLite 不是必须立即替换；它要求同机文件系统、短写事务和可测的写争用，WAL 仍仅一个写者。[W2]

## 3. 核心不变量：编码必须以测试保护

| 不变量 | 内容 |
|---|---|
| INV01 | 仅认证后的固定 owner 能创建、取消、重试、反馈、读取私有报告/资料/事件流。 |
| INV02 | 来源配置、模型输出和调用方 body 不能开启新的触发类型、权限或费用路线。 |
| INV03 | 同 owner+幂等键+相同请求返回原 Run；相同键不同请求 409；部署后重试不切新配置。 |
| INV04 | 领取、续租、完成、失败、取消、工具权限签发都验证 Run/Task/attempt/epoch。 |
| INV05 | Artifact、业务终态、完成事件、必要 Outbox 在一个 DB 事务提交。 |
| INV06 | 已完成/失败/取消的 Run 不被迟到回调重新变为 running；改版/手工重试是新 Run。 |
| INV07 | 发往外部的操作有持久 intent 与 idempotency identity；不明结果先对账。 |
| INV08 | 队列、并行、内存、文档大小、页数、token、费用、磁盘均有独立边界。 |
| INV09 | 原始资料、发布时间、接收时间、版本不得为了通过 Gate 被改写。 |
| INV10 | 独立复核必须有实际执行 receipt，不以角色扮演或图节点替代。 |
| INV11 | 业务记录权威且可恢复；Langfuse/Collector 故障不影响业务提交和历史可读。 |
| INV12 | 只有完整验证过的输入/有效运行时输入才能标 effective_inputs_verified；仅选材清单不等于证明全部上下文。 |
| INV13 | 没有质量增量不扩大角色、数据范围或平台模块；安全正确性不能被好看的报告抵消。 |

## 4. 部署和所有权：减少组件，但不是把所有工作塞进 API

```text
固定 owner 浏览器
  → DSH 公共工作区（已有认证 + 自有业务输入）
  → Host/BFF（可信转发；不接受浏览器自报 owner）
  → Hub API（admit/query/command）
      → SQLite Run/Task/Attempt/预算/资产目录/业务事件

单个 Research Worker
  → 有续租与 fence 的 Run lease
  → LangGraph 固定任务依赖/恢复（不是另一个自由规划器）
  → DSH 公共执行原语（每项真实 Task、允许的工具与独立上下文）
      → Research Gateway（唯一数据/证据/权限入口）
      → 原生模型与搜索（受明确的运行期预算钩子控制）

手动启动的 Data Job Worker / 受限 Parser subprocess
  → 持久游标、不可变分区/文档、manifest
  → 不创建 research Run

LoongSuite + Hub 关键 span → Collector → Langfuse
```

保留现有单机、loopback/内部网络边界；模型和数据密钥仅放到需要它的服务，不继续将全部密钥通过共享 environment 注入所有容器。数据 parser 无模型密钥、无业务库写权限，使用只读输入和自己的输出目录。

默认不启动 realtime/evolution/scheduler；保留必要 outbox/reconciliation 处理，它们只能处理已有命令。用户显式请求长期数据采样时，才能创建有边界的数据作业；这不意味着授权其自动发起研究。

API 不直接执行研究。不要用 FastAPI BackgroundTasks 代替持久队列。参考实现使用 SQLite 展示事务语义，生产必须复用现有 Run 表/服务，不新建并行“正式队列”。

## 5. 单 owner 安全设计与 API 行为

### 5.1 身份与权限

1. DSH Web 继续使用其固定版本已验证的登录/会话机制；匿名请求不能触发业务。
2. Host 从服务端认证上下文构建固定 principal；浏览器传来的 owner/origin/role/能力列表一律无权决定身份。
3. Host→Hub 使用单独的随机内部 credential。Hub 将该 credential 映射为既定 owner 和限定 scope，不把 X-Owner-Id 当认证。工具回调使用另一个 credential，不能凭工具 credential 调 Intake。
4. 所有正式读接口、SSE、原文下载同样授权；不是只保护“提交”按钮。
5. Cookie 写请求做 Origin/CSRF 检查，WebSocket 验证 Origin，CORS 不是身份认证。公开 listener 使用 TLS；本轮不开放公网。
6. task capability 在服务端绑定 Run/Task/attempt/epoch；子任务不能创建用户级研究任务，不能获取其他任务的证据权限。
7. 禁止固定开发 secret 的生产 fallback；未配置时拒绝启动写入口。不要造完整账户系统，也不自己实现密码学。

本轮不承诺抵抗同 OS owner 的任意恶意代码或 root；这是信任边界，不是允许其他应用随便使用 HTTP owner 字符串。

### 5.2 API 契约（保留已存在的 URL，添加行为）

| 路由 | 行为 |
|---|---|
| POST `/v1/research/observations` | owner auth → 校验 → 幂等查找 → 容量检查 → 同事务 Run/初始事件 → 202。不可在事务里探测 Provider/调用模型。 |
| POST `/v1/research/runs/{id}/commands` | 所有 command 经 owner auth；取消幂等；已完成 run 不再转 cancelled；重试由 owner 显式创建新 Run 并继承 lineage。 |
| GET `/v1/research/runs/{id}` | 返回有版本 snapshot、cursor、必要任务摘要；大正文/Trace 不全嵌入。 |
| GET `/v1/research/runs/{id}/events` | 授权，使用 after/Last-Event-ID；稳定事件 ID 与 seq；有断连和慢消费者策略。 |
| GET 对应 evidence/task 详情（新增） | 按需读取、分页、限长、授权；不允许任意文件路径读取。 |
| POST 手动数据导入（新增，沿现有 query/command 层） | owner only，排一个 data job；不创建 research Run，不自动升级 source 许可。 |
| legacy `/v1/observations`、source poll、automatic intake | 本轮默认禁用或返回明确 410/403，不允许第二套执行路径。 |

状态码：未登录401；无scope403；相同幂等键不同请求409；队列满429（明确 reason 与重试提示）；暂不可写503；返回202代表已持久接受，不代表执行成功。

幂等比较用户的稳定原请求，第一次 admission 固定 server 解析的 bundle。部署后原请求重试仍回到原 Run，不能因为新默认模型/策略而错判请求冲突。

## 6. 队列、Run、Task、Attempt：明确不是同一个对象

Run = 一次用户授权的研究。Task = Run 内逻辑工作。Attempt = Task 一次真实执行。Lease epoch = 执行所有权代次。原生 Session/Turn = 运行时身份。Trace = 诊断身份。

### 6.1 增量数据库设计

沿 `persistence/db.py` 的现有表新增/拆出 ORM 模块；不要直接复制 reference 的独立测试 DDL。

| 对象 | 字段/约束 |
|---|---|
| RunRecord 增量 | owner_subject、trigger_origin、request_hash、bundle_manifest_ref、phase、fence_epoch、queue_deadline、execution_deadline、cancel_requested_at、event_seq、root预算引用 |
| ResearchTaskRecord 新增 | task_id、run_id、kind、depends_on、required、state、input_revision、max_attempts、result_ref；同Run+logical_key唯一 |
| ExecutionAttemptRecord 新增/复用已有原生关联 | attempt_id、task_id、epoch、operation_id、submit_state、native_session、native_turn、receipt_ref、result_hash、started/finished；operation_id唯一 |
| 既有 reservation 扩展 | operation_id、request_hash、类别、approved_upper_bound、reserved/settled/unknown、实际费用与来源；不重复造工具次数账本 |
| RunEvent / ResearchTrace | 事件ID、DB分配seq、Task/attempt关联；同事务分配，不用进程Lock作为全局保证 |
| Artifact | 同一Run正式提交唯一；hash/版本不可变；派生修订新Run/显式新version |
| Outbox | dedupe_key唯一，kind/available_at/attempt/status；仅提交后派生工作，不负责再次调用整条研究 |
| 数据 job/目录 | 持久游标与不可变资产，和研究任务分类型；无root时也能导入，不产生“历史新事件” |

新列优先 additive migration。现有 RunStatus 保留兼容映射，新增细 phase/attempt 状态不强迫旧UI理解新枚举。若确需修改 canonical enum，必须发布 schema版本和旧记录reader，不能口头承诺“所有修改都天然向后兼容”。

### 6.2 状态与取消

```text
admitted/queued → running（phase=preparing/researching/reviewing/synthesizing）
                 → completed / degraded（研究产物可用但有边界）
                 → failed / cancelled / expired
running lease失效 → reconciling → 恢复原执行 或 明确失败/需人工处理
```

取消的线性化点是 DB 撤销执行授权：epoch递增/取消标记原子写入并写 cancel-runtime outbox。此后旧 fence 不再签发新工具/模型执行许可，也不能提交结果。

已经发出的网络请求可能无法撤回；报告业务状态可立即 cancelled，runtime_stop_status 仍可能 pending/unknown。未确认结束的远端工作仍占用对应 native capacity，不立刻放开无限新工作。迟到usage只影响成本记录，迟到结果可隔离归档，不能恢复已取消结果。

取消与完成同时到达：先成功的 DB 事务决定结果。完成已提交则取消返回 already_terminal，不撤回既有产物；取消先提交则完成被 fence拒绝。

### 6.3 原子领取与续租

保留 `claim_next` 现有条件UPDATE逻辑，扩展 epoch/version/origin/容量检查。在短事务中领取，实际网络执行在事务外。

```sql
-- 示意：参数、表名沿现有ORM迁移，不是可直接对生产执行的SQL
UPDATE runs SET lease_owner=:worker,
                fence_epoch=fence_epoch+1,
                lease_expires_at=:expires
WHERE run_id=:id AND status='running'
  AND lease_expires_at<=:now
  AND trigger_origin='manual'
  AND bundle_manifest_ref=:compatible;
```

RunClaim返回epoch。renew按 `(run_id, owner, epoch, status, lease still valid)` 条件更新。旧 worker 即使使用相同 worker_id 也不能通过新 epoch。

所有修改结果、失败、事件、Task、工具许可的路径验证 fence。**只在 claim 处加锁，不在 commit 处检查，是不完整的修复。**

初始根并发1通过DB容量约束/部署唯一worker验证。即便未来误启动第二worker，必须拒绝两个不同root同时执行；进程内Semaphore不等于部署级限制。

### 6.4 三种时钟

queue_deadline：接受后多久还值得开始。
execution_deadline：第一次领取时确定，恢复不延长。
lease_expires_at：执行所有权的短期租约，持续续租。

持久时间用UTC；进程内超时使用单调时钟。长时间休眠、时钟跳变后先reconcile并刷新适用的实时资料，不能无条件使用旧行情继续“当前判断”。

### 6.5 提交原子性

一个业务事务中：验证fence和输入/结果版本 → 保存研究结果/Artifact/适用Forecast → 更新终态 → 分配完成事件 → 插入必要Outbox。

提交成功后不再因为 `_record_completed`、评价、Trace查询或通知失败而 `set_status(failed)`。将runtime/version/latency等必须字段放入提交事务，派生工作用Outbox。

同Run同hash重复提交返回原artifact；不同hash冲突，不最后写入者覆盖。旧worker的提交即使payload有效，也必须被fence拒绝；只读返回既有相同artifact不改变状态。

## 7. 外部执行：checkpoint不等于精确恢复

当前LangGraph节点内会调用完整runtime.execute。进程可能在DSH已经执行、但graph结果还未checkpoint时崩溃。[S5] 恢复节点可能重执行，因此必须先用持久原生关联对账。[W3]

每次外部工作遵循：

```text
事务A：建立 Task/Attempt/operation_id + 预算预留 + dispatch intent
网络外：submit(operation_id, task envelope)
事务B：保存原生ack/receipt、状态和result ref
```

Intent先落库。submit请求超时后的 lookup：

| lookup结果 | 行为 |
|---|---|
| running | 重新附着、等待；不第二次submit |
| completed | 取得并验证已有receipt/result，推进本地状态 |
| failed/cancelled | 根据失败类别及本次剩余预算判断，不泛化重跑 |
| absent_confirmed | 只有运行时能确证“没有接收过”才允许原operation幂等提交 |
| unknown/unreachable | 有界重查或暂停，保留费用预留，不能当未执行/零费用 |

operation_id绑定逻辑Task/attempt，不能仅因worker epoch变化就生成新operation并再次扣费。新Task attempt才创建新operation；业务授权仍验证新的epoch。

RuntimePort规格在 `reference/ports.py`。`submit/lookup/cancel` 是**本项目拟定义的接口**，不是声称DSH有同名方法。第一张宿主实施卡必须列出固定版本DSH真实公开接口、源码位置、参数、返回值和对应测试；不能通过猜API名实现。

同一Run的DB、LangGraph checkpoint、DSH原生存储不是一个分布式事务。采用可对账关联，不承诺外部LLM/网络请求全局 exactly-once。能保证的是本地正式产物唯一、旧执行无权覆盖、未知状态不被掩盖。

## 8. 并行、线程和背压：分清工作类型

| 工作 | 执行方式 | 必须处理 |
|---|---|---|
| HTTP、LLM等待、子任务等待 | async I/O、连接池、明确超时 | 有界并行与全局预算；不要一口气gather全历史 |
| 现有同步SQLAlchemy事务 | 短线程边界；每次调用在该线程创建/关闭自己的Session | 不跨Task/线程共享Session，不在事件循环直接等待DB busy |
| 只提供同步接口的SDK | 有界线程池临时适配 | await取消不代表底层线程停了，许可要到实际完成才释放 |
| CPU重解析、不可控第三方解析 | 隔离子进程/worker，最多1 | 内存/页数/响应体/运行时间限制，可终止进程 |
| 大型历史数据 | 分页/分区流式写文件 | 游标持久化；磁盘水位/配额；不一次全读入RAM |
| UI事件 | 持久事件+分页增量 | 不每token写SQLite，不每250ms读取全报告 |

Python TaskGroup 对未处理子任务异常会取消其他任务；符合预期的来源失败要转为 typed Outcome，程序错误/根取消则传播。不要 `except BaseException: return success`。[W1]

协调者不能拿着唯一LLM许可等待子任务，而子任务又需要同一许可。许可属于正在执行的模型请求/leaf，不属于整段协调会话。参考 `async_execution.py` 显式展示这个边界。

Python限流器只能约束它管的调用。DSH原生模型、原生搜索与GatewayHTTP分别有可验证的上限；统一费用来自共享ledger，不靠跨语言共享一个Semaphore的想象。

线程取消：future正在运行时不能保证取消；超时后不得立即把该线程槽当空闲继续塞无限任务。数据库同步事务尽量小，线程结束后检查fence；有副作用的同步SDK必须有独立幂等。[W4][W5]

**不引入 Redis/Celery 的理由**：已有数据库队列可复用；当前root单并发，无跨机负载需求。新broker会增加持久化、ack、重试、监控与第二份状态。不因“未来可能多用户”现在就引入。

迁移触发：真的跨主机运行；写锁等待持续超标；数据量/采集负载无法通过缩短事务与索引改善。先独立评估PostgreSQL或消息broker，不与研究规则重写一起迁移。SQLite WAL只适用同机、正确文件系统并仍然单写者；不是无限并发能力。[W2]

## 9. 全局预算与故障分类

额度沿现有 reserve_tool_call 扩，不替换为内存计数。每次LLM/付费搜索/Provider实际发出前预留可说明的上界；收据到达结算；未知保留reserved，不返还。

预算分层：root总量 → 最终综合/必要核验预留 → 研究可支配额度。具体比例由任务配置和测量决定；不能先把全部预算用于搜索，然后没有预算生成可读结果。

金额使用整数micro-USD或Decimal，不用浮点累计作为严格额度判断。供应商实际费用超出预留必须如实入账、标overrun并阻止新请求；不能为了满足“预算内”隐藏真实账单。

| 错误 | 重试责任 |
|---|---|
| 429/503/短暂连接问题 | Provider adapter 一层负责、指数退避+jitter、尊重Retry-After，预算和总deadline约束 |
| 401/403/未配置/许可禁止 | 不自动重试同路径，返回可执行配置缺口 |
| 解析失败 | 明确一次受控parser fallback；不是重复搜索原URL |
| 结构化输出错误 | 仅在相同事实输入上有限修复，不重新获取所有数据 |
| 原生submit结果未知 | reconciliation，不泛化为网络重试 |
| 业务约束/伪证/PIT违规 | 不重试到通过；保存失败证据并停止对应方向发布 |
| DB临时busy | 短事务重试一次责任层，不能重做已经结束的LLM请求 |
| Artifact后评价/观测失败 | Outbox单独重试；Run终态不变 |

所有嵌套层默认SDK retry需明确关闭或协调，避免工具×网关×SDK各重试三次。

## 10. 数据、检索和计算：保留V2路线，但收紧当前触发范围

首批：现有OKX/CoinEx稳定接口；CCXT补缺失历史；现有FRED/ALFRED补版本与metadata；OpenBB仅在现有adapter确实缺能力时按需接入；HTML主体提取和复杂文档隔离解析。不是一次安装全部数据平台。

本轮回填由owner点击/明确命令触发。运行中按缺口获取资料属于该Run的已授权工作。定时研究/自动来源触发/自动复查均关闭；无主任务或owner数据作业时不擅自启动采集任务。

三个月窗口使用自然月；指标预热和评测样本窗口独立。数据集元数据必须列：指标/来源/单位/频率/合约类型/最早可读日期/发布时间精度/修订性/授权/实际覆盖。不能把日频美元代理称作分钟DXY，不把当前OI变成历史OI变化。[V2]

结构：原始资产→不可变文档/Parquet→版本manifest→按Run引用。无Run可存资料，但资料不自动成为某个Run已核验的Fact。改manifest默认指针不改已完成Run引用。

时间模式：当前研究、当时实际观测回放、今天重建历史。保留received_at真值，标available_at依据/精度；只知道日期不能伪装当天任意分钟已可知。历史重建不计前瞻样本。

检索复用 `ResearchCapabilityAdapter`/Gateway，新增capability但不另建协议：search、read、query_series、compute_features、coverage。返回结构化资料refs与缺口，不只返回搜索服务的自然语言答案。

缓存按query/目标/字段/时间/版本/权限区分；首次成功不能让所有不同查询返回同批结果。新鲜度按字段定义：不可变旧声明正文不会只因“旧”而无效，当前报价则需严格时效。

文档大小、压缩后膨胀、页数、重定向、DNS/IP、最终URL均检查。外部文字是数据，不能变成工具指令。已批准来源仍可能被注入恶意内容，模型拒绝指令不能替代权限控制。

计算由代码完成，输出携带输入引用/公式版本/单位/窗口/缺失规则。实际值-前值不等于意外程度；分位不等于上涨概率；相似性检索不能使用未来标签选事件。

## 11. Context、研究分工和质量：不是多Agent就更聪明

### 11.1 ContextManifest精确定义

包含：root/task/attempt、问题、适用截止、数据revision、选中的证据refs、规则与工具schema版本、模型能力档、token预算、摘要版本、被排除原因、输入指纹。

区分 `selected_inputs` 与 `effective_inputs_verified`。DSH可能另注入系统指令、历史或压缩内容；仅保存自己选择的材料不能宣称它就是完整运行期输入。适配器要记录可获得的实际输入版本/继承关系；没有权限看到的部分明确未知，不伪造全透明。

不默认传父会话全部历史；不每次读所有Skill。当前价格/持仓刷新按任务类型和队列等待时间执行。长期记忆分为用户稳定偏好、方法经验、历史研究与当前事实，来源和置信状态分开，不让错误结论自动成为记忆真值。

### 11.2 任务与并行合并

TaskPolicy先声明必要任务。典型复杂研究：原文任务与数据任务可并行；二者提交有版本的结果后建立review快照；独立核验针对真实主张回读；最后干净上下文综合。简单解释任务允许少任务，不强迫所有请求四个Agent。

叶任务内部保留受限ReAct，以缺口选择新query/target/window。外层图只维护固定依赖、预算、等待和状态，不再运行竞争的自由规划Supervisor。

共享资料仓可读，不共享可任意改写的报告对象。Task输入固定revision；新事实持久后显式建立下一revision。核验者不会读到“写了一半”的结果，也不会因完成顺序不同覆盖其他人的主张。

Review指向claim+证据；反方不强制否定，意见一致也不是校准。独立上下文不等于统计独立；相同模型/同新闻源的错误高度可能相关，质量评测要检查新增证据与错误率，不按人数加信心。

### 11.3 发布前的时间有效性

明确记录研究start/cutoff/finish/publish。排队久了不能用入队时价格冒充当前价格。提交前检查关键实时事实是否仍满足任务TTL；不满足则预算内刷新并重算，或发布“截至某时”的历史研究，不偷偷改变结论时点。

### 11.4 质量验收与模型适配

真实endpoint必须先证明工具调用、结构化输出、超时/取消、usage语义；兼容OpenAI API不等于行为完全相同。记录服务端实际返回的model/usage及缺失状态，不靠请求字符串推定模型身份。

用固定证据比较上下文/方法，用在线任务比较取证能力；保留失败样本和不可回答任务。历史题可能受模型已知后果影响，不能把历史好答案当真实前瞻预测。

代码Gate负责身份、来源、时间、单位、关系；语义复核/人工负责引用支持程度、反证和因果越界。不能用同一个LLM同时生成并无条件裁定自己正确。人审rubric与留出案例不能在每次失败后悄悄改成通过。

## 12. UI与可观测：不推翻V2，但先保护事务与权限

保留DSH公开slot中的自有研究输入，移除DOM抓取/拦截原生发送。一个业务工作台：研究列表、研究详情、数据与设置。必要Desk功能迁移并通过用例后，移除重复前端构建/静态挂载/跳转；不先删正在用于验收的页面。

保留：任务/证据/产物/Outcome/Evaluation底层服务。退出默认：Evolution/Promotion产品面、无用重复导航、未接动作按钮、独立研究入口。隐藏UI不是关闭API权限。

任务树由实际receipt生成；质疑链由真实claim/review/revision生成；技术Trace由LoongSuite和少量Hub边界span生成。Langfuse不是业务账本，也不决定是否提交。

保留多Trace关联，不修改上游ROOT_CONTEXT拼超级树。TelemetryRef按native session/turn查找真实trace；缺少trace就是pending/missing，不能猜。

Langfuse离线时不阻塞报告；重型观测部署资源独立、有界队列。低价值token级事件不写业务DB。默认不采prompt全文，诊断开关有权限、到期、脱敏和留存；工具/body里的恶意文档不能进入可执行UI，Markdown禁原始脚本、下载路径受控。

SSE：snapshot和last_seq同一短读事务，随后消费seq>cursor。事件ID用于去重；cursor过旧返回重取snapshot提示。客户端一条稳定连接，退避重连；终态取最终快照；慢消费者有界缓冲。SSE断开不取消Run，关闭浏览器不停止worker。

## 13. 可维护、扩展、升级与退出

### 13.1 模块边界

- Core定义Run/Task状态、权限端口、预算与提交事务；不能导入DSH实现、OpenBB、CCXT或金融产品实现。
- knowledge定义资料索引/查询/版本，不判断交易方向；重库在provider/parser adapters后。
- crypto_macro实现任务策略、指标、关系Gate、Forecast投影；可以含代码，不能因pack data-only限制把代码挤回Core。
- runtime adapter负责真实执行与receipt；core不解析DSH JSONL私有格式来补写事实。
- composition root允许导入具体实现并注入；不能为了禁所有import造复杂字符串反射。
- app/UI只消费query DTO，不读SQL或checkpoint内部状态。

提取共享能力至少有两个真实消费者或稳定外部协议依据。不要为未来PPT建立整个插件市场。本轮用一个非金融dummy extension测试：不改Core即可创建普通产物，不自动生成BTC Forecast。

### 13.2 bundle与升级

每个Run固定：代码commit/build、contract版本、graph版本、DSH上游/插件版本、TaskPolicy、Gate、Provider route、parser/特征公式、模型/能力配置、数据snapshot。

新二进制仅恢复明确兼容的bundle/checkpoint。未知版本进入blocked_upgrade/人工裁决，不静默用新代码续旧图。部署：停止接新任务→drain或持久暂停→备份并验证→迁移→canary→恢复接收。

数据manifest可以热发布，运行中的代码/插件不随HMR热换。取消边界、fence、工具认证与事实安全修复不随效果回滚被关闭。

备份包括业务DB的正确WAL快照、checkpoint、DSH持久目录和被引用的原始资产manifest；仅复制SQLite主文件可能不是有效在线备份。[W2] 定期恢复演练；GC不能删除仍被Artifact或评测引用的文档。

### 13.3 将来调度层

只预留 `TriggerCommand`/admission adapter契约，不现在实现cron。未来scheduler通过同一授权入口发布任务，不直接调用Agent。

届时明确：owner的Schedule权限、timezone、fire_time、misfire/coalesce策略、幂等键(schedule_id,fire_time)、重叠禁止、优先级、配额、停止开关。现在public origin只允许manual；已有自动pending作业需migration报告后暂停，不能一启动新版就恢复执行。

## 14. 性能目标与回归（目标，不是已测生产指标）

| 目标 | 初始验收建议 |
|---|---|
| 本地admission P95 | <500ms，不等待外部Provider；测时记录机器/DB规模 |
| cancel授权撤销P95 | <500ms；远端真正停止单独测，不做不可能保证 |
| 事件可见 | 正常网络下2秒内；明确端到端延迟拆分 |
| 根并发/leaf并发 | 1/2有运行记录证明；误启动第二worker不突破 |
| 队列上限 | 第6个等待请求429，无隐藏无限list |
| 写争用/事件循环 | 记录busy等待、event loop lag、p95/p99；持续超标必须先定位 |
| 成品质量 | 关键事实遗漏/无支持主张/人工返工相较基线改善；不得只看耗时 |

测试矩阵分三层：单元/故障注入；真实DSH协议探针；真实研究任务留出对照。reference测试只属于第一层的部分。

并发/故障必须测试：同key并发提交；多worker领取；240秒以上活跃Run（时间可控测试）；同worker_id旧epoch；commit之后事件失败；取消/完成竞争；submit ack丢失；进程在intent后/remote后/checkpoint前退出；未知计费；optional子任务失败；DB busy；解析器OOM/超时；事件乱序/重连；版本不兼容；禁止自动触发；Langfuse离线。

## 15. 实施卡顺序：每卡小范围、先红测试

| 卡 | 修改范围 | 必须先出现的失败测试 | 验收，不允许替代 |
|---|---|---|---|
| T01 owner与触发隔离 | hub_api、Host auth、commit复查、claim origin、Compose | 非owner/任意header/legacy直调/自动recheck均被拒 | 真HTTP路由用例；DSH默认UI认证不代替Hub鉴权 |
| T02 lease/fence与终态事务 | run.py、commit.py、worker、相关schema/migration | 旧epoch写入/已完成改失败/取消晚提交 | 真实SQLite竞争+事务回滚；已有业务测试保持 |
| T03 eventseq与幂等 | research_observability、RunEvent、admission | 两进程同序号/同key不同payload/重连游标 | 单事务序号和唯一产物；不只用thread Lock |
| T04 runtime协议与恢复 | DSH adapter/Host/attempt表/外层executor | ack丢失/lookupunknown/旧checkpoint | 固定DSH公开接口与真实receipt；无能力不得用模拟绿灯交付 |
| T05 并发与预算 | worker/gateway/DSH受控钩子/parser worker | leaf超限、父子许可死锁、未知费用退款、重试放大 | 全部计费路径有可验证责任；无hook则明确不启用 |
| T06 数据与检索 | provider/knowledge/parser/contracts | 后半段原文丢失、历史时间造假、缓存误命中 | 实际数据覆盖、可回读与可复算；所有自动触发仍关闭 |
| T07 Context/TaskPolicy/Review | context assembler、任务表、领域实现 | 第51条关键新证据、历史污染、假复核、并发半成品 | 真实任务与固定输入对照；不按Agent数量算质量 |
| T08 UI/观测/Desk退役 | DSH公开slot、telemetry refs、query、Dockerfile | 未认证事件流、刷新过程丢失、观测离线、DOM依赖 | 有用视图迁移后删旧前端；业务结果不依赖Langfuse |
| T09 升级/留出裁决 | bundle manifest、迁移、评测、部署 | 旧checkpoint新代码、回滚历史丢失、成品无增量 | 恢复演练＋真实研究对照后才扩大范围 |

同一时刻最多一张涉及Core写路径的卡在实现。独立前端/解析器卡可以并行，但不能同时改同一schema和提交状态。schema变更由一个负责人合并后其他卡再基于新版本开发。

## 16. 防止开发上下文再次漂移

仓库落地时建立：
- `AGENTS.md`：不超过必要长度的全局约束，指向唯一当前规格和禁止项。
- `CURRENT_IMPLEMENTATION.json`：当前任务、基线commit/worktree hash、已通过测试、剩余失败、下一动作。
- `work_orders/Txx.md`：该卡需要的代码、契约、失败测试、允许修改文件、明确不做什么。
- `DECISIONS.md`：只追加变更裁决与原因，不另写“最终方案最终版”。

每次新会话读AGENTS+当前卡+精确代码，不把三个项目全部历史和所有旧方案灌入prompt。开工前检查当前commit与卡上基线是否匹配；不匹配先重新核查，不覆盖用户变更。

完成报告固定五项：改变的行为、改动文件、运行过的测试及真实输出、没验证的部分、剩余风险。没有真实DSH任务回执不能说“多Agent已实现”；没有质量对照不能说“效果提升”。

独立审查在能调度子Agent的环境执行：五路独立first-pass只看共同基线和各自题目，不看主审结论；第二轮反驳者核对证据并尝试推翻；最后合并确证问题。不得伪造运行编号、耗时或投票。

## 17. 本次附件与仍未完成的验证

`reference/`：运行控制、接口和有界独立任务示例。`tests/`：旧风险探针＋新不变量测试。`test-results.txt/xml`：本容器真实执行输出。`reviews/`：已核查发现及未执行的独立审查任务。

没有实现：真实Web认证桥、DSH公开API绑定、实际provider、生产SQLAlchemy迁移、真实队列worker集成、Langfuse部署、真实模型研究质量与性能压测。参考包不能直接替换生产代码，也不应被当成第四个平台。

## 18. 来源索引

固定仓库前缀：`https://github.com/luguochang/decision-hub/blob/1f5a31da0da012c628737e5fa3bf8a3d04b344f1/`

- [S1] `apps/hub_api/main.py`：require_owner、research_observations、legacy BackgroundTasks、SSE。
- [S2] `packages/kernel/decision_hub_kernel/application/commit.py`：提交事务、固定事件号、自动复查。
- [S3] `packages/kernel/decision_hub_kernel/application/run.py`：claim/renew/set_status。
- [S4] `apps/hub_worker/research.py`：240秒租约、tick、postcommit记录与失败分支。
- [S5] `packages/orchestration/langgraph/executor.py`：checkpoint调用与resume。
- [S6] `source_extracts/current_control_flow.py` 与 `tests/test_current_probes.py`：局部控制流复现。
- [S7] `packages/kernel/decision_hub_kernel/application/research_observability.py`：原子工具额度与实例锁/MAX序号。
- [S8] `compose.yaml`：loopback、角色和共享凭据配置。
- [S9] `packages/kernel/decision_hub_kernel/persistence/db.py`：ORM模型与约束。
- [S10] `packs/crypto_macro/pack.yaml`：480秒总预算、工具/子Agent上限。
- [V2] `Decision_Hub_整体系统方案_V2_2026-09-08.md`：本次完整读到的历史方案，非新实现证明。
- [W1] `https://docs.python.org/3.12/library/asyncio-task.html`：TaskGroup、CancelledError、timeout与线程边界。
- [W2] `https://sqlite.org/wal.html`：单写者、同机约束、WAL与备份。
- [W3] `https://docs.langchain.com/oss/python/langgraph/persistence`：checkpoint与store的不同职责，不代替应用事务。
- [W4] `https://docs.sqlalchemy.org/en/20/orm/session_basics.html`：Session并发与生命周期。
- [W5] `https://docs.python.org/3.12/library/concurrent.futures.html`：运行中的future取消边界。

最终裁决：**先把权限、触发、租约、事务、外部执行对账和预算做成可测试契约，再建设并行研究与可视化。保留已证明有用的架构，不为“更全面”引入更多运行基础设施。**
