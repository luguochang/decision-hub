# PD-04～06 产品运行收口实施方案

状态：`completed / engineering and isolated runtime closeout verified`  
日期：2026-09-05（Asia/Shanghai）  
上级方案：[`PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md`](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)  
阶段 Charter：[`PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md`](PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)

## 1. 目标与产品形态

本卡不再扩展领域能力，而是把已经实现的 PD-04～06 代码收敛成一个可验证的单 owner 产品实例：

```text
官方日历/feed/人工 TextEnvelope
  -> Hub EventWatch + durable Run
  -> LangGraph 生命周期/checkpoint/预算
  -> DSH 唯一 Supervisor/Agent Loop/Session/Trajectory
  -> Search locator + approved Fetch/typed provider + PIT Fact
  -> Hub semantic Gate + Artifact/Forecast/Outbox
  -> DSH 主动研究 Inbox/报告 + Decision Desk 管理投影
  -> 30m/24h/72h Outcome/Evaluation -> owner-gated candidate
```

唯一日常用户入口是官方 DSH Web。Decision Desk 是运维、跨 Run 查询、评测和 Promotion 管理面，
不是第二个聊天产品。DSH Session/JSONL 保存交互和轨迹；Hub 保存唯一业务账本；两者通过
`run_id/dsh_session_id/trace_ref` 关联，互不替代。

## 2. 本轮根因

2026-09-05 浏览器验收发现页面显示 `Services 0/3`、`degraded` 和长期“处理中”。代码质量门全绿，
但当前 `127.0.0.1:8000` 的 OpenAPI 不包含工作树已有的 `/v1/research/inbox`。进一步对账发现：

1. `run-product.sh` 使用 `docker compose up --force-recreate`，没有 `--build`；旧镜像可被重建为新容器。
2. 启动器只探测通用 `/health/ready`；旧版本同样返回 200，无法证明 PD 产品契约已加载。
3. 本机保留多套历史验收 Compose 项目和 DSH Web，每套都有独立卷；打开旧端口会看到旧账本。
4. 历史失败总数会让 `/v1/health` 长期显示 `degraded`，它表达“账本里曾有失败”，不等于当前服务离线。
5. 第一条真实人工回溯 Run 暴露出规划信息缺口：Hub 已用 `EventWatch` 严格区分未来窗口和
   `retrospective_only`，但 `ResearchSessionRequest` 没有把这项可信状态投影给 DSH。模型只能从通用
   playbook 猜测是否可请求 `t-5m/t+1m`，导致无 Watch 的人工事件触发
   `research_event_watch_not_found`。
6. Gateway 的拒绝是正确的 fail-closed 行为；若靠放宽 PIT/EventWatch、伪造 baseline 或只改页面提示，
   会破坏事实可信边界。根因必须在 canonical request 和 DSH 规划输入层修复。
7. 同一 Run 第二轮重复读取语义相同的 FRED observation 时出现 `research_fact_identity_conflict`。
   `payload_hash/fact_id` 已按设计排除易变的 Gateway 接收时间，但 FactStore 的幂等比较又包含
   `observed_at/received_at`，导致同一语义事实因二次读取时间不同被误判为身份冲突。
8. 第二条真实 Run 已进入 DSH 第 2 轮并执行 Search/Fetch/typed-provider，但 `official.macro` 被
   `research_source_requirement_denied` 拒绝。根因不是搜索未启动，而是 `source_registry.yaml` 使用
   Pack 内部 dotted ID，真实 `ResearchCapabilityQuery` 使用 canonical snake ID；Registry 做了裸字符串比较。
9. `official.macro` 当前只能确定性解析 `event.identity`，却被 playbook 引导为 `policy_or_data_delta`
   产生 typed Fact。即使修正 ID 映射，它也不能凭一篇当前正文计算“相对前值/前次措辞”的 delta。
10. 通用 Fetch 把 `application/pdf` 响应按 `response.text` 解码，导致 PDF 二进制头曾被保留为 Evidence。
    未安装可信 PDF 文本解析器时，这类内容必须结构化拒绝，不能用乱码抬高证据数量。
11. DSH 产品工作区同时显示官方“发送消息”和扩展“建立研究任务”。前者会运行 DSH Web Search，
    但不创建 Hub Run、Fact、Gate 或 Artifact；这是真实入口歧义，不能归咎于用户操作错误。
12. 第二条真实 Run 的第 3 轮已成功提交结构化 synthesis，却被 `_trusted_plan()` 以
    `research_capability_unavailable/market.crypto_crowding` 推翻为 Worker failure。根因是跨轮
    `requirements_for_round()` 会把 Pack 中未启用的 fallback 移到首位，而计划投影只接受当前
    Run 的 allowed capability；一个不可执行的 fallback 因此在结果映射阶段抹掉了已完成的 DSH 回合。
13. `create_server()` 给 `official.macro` 注入了 Source Registry，却让通用 `web.fetch` 只经过 capability
    域名白名单。它会把 Federal Reserve 正文固定标为 `verified_web`，并可能让 Registry 中仅允许 fetch、
    不允许 Evidence 的来源入账。这既造成官方正文 `low_authority`，也绕过了既定来源审计链。
14. 最新真实 Run 的顶部 Hub 状态已经按当前 Session 正确显示 `research_only` 和 hard coverage，但切换到
    “研究报告”视图后却显示“当前会话尚未关联正式研究任务”。官方 `conversation.view` 是 session-scope，
    标准 props 已提供 `sessionId`；插件包装器再次使用 `{ ...props }` 传给嵌套组件。如果 Host 用非枚举
    属性或特殊运行时对象暴露标准 props，对象展开会静默丢失 Session identity。Inbox 和 Intake 的注册
    包装器使用了同一模式，因此必须统一修复，不能用 URL、localStorage 或第二份关联状态绕过。

因此这是部署身份与状态语义问题，不是放宽事实 Gate、重写 Agent Loop 或清空历史数据可以解决的。

## 3. 架构与所有权约束

- DSH 继续是唯一 Agent Harness、Supervisor、工具循环、Session 和 Trajectory 所有者。
- LangGraph 继续只负责外层 Run 生命周期、checkpoint、恢复、预算和确定性路由。
- Hub 继续是 Event/Evidence/Fact/Artifact/Forecast/Outcome/Evaluation/Outbox 唯一账本和 Gate。
- `crypto_macro` Pack 继续拥有 requirement、字段、窗口、来源和语义政策。
- 启动器只负责构建、装配、探针和进程所有权，不新增业务状态机。
- 不删除历史卷，不把历史失败改写成成功，不把 Search 摘要冒充 typed Fact。
- 不新增第二套 Agent Loop、账本、scheduler、Provider DTO、前端框架或插件概念。

## 4. 实现方案与代码落点

### 4.1 当前代码构建与产品契约探针

修改 `infra/dsh/run-product.sh`：

1. 每次产品启动都以 `docker compose up --build --force-recreate` 构建当前工作树。
2. 通用 DB readiness 通过后，再请求 `/v1/research/inbox?limit=1`。
3. 只有响应可解析且 `schema_version=research-inbox-view.v1` 才允许启动 DSH Web。
4. 探针失败时明确报 `product API contract did not become ready`，不得继续打开旧 UI。
5. DSH Host 仍使用已有 upstream commit/version/plugin hash readiness，不复制版本协议。

这项改动不增加 API DTO。它复用 canonical `ResearchInboxView` 作为产品能力探针，避免新增
只为启动器服务的第二份 build schema。

### 4.2 单实例与历史实例

- 正式默认实例使用稳定 Compose project `decision-hub-product-<api_port>`。
- 并行验收必须显式使用独立 project、API/MCP/DSH 端口和卷。
- 启动器不自动删除其它项目或卷；清理是独立、显式、可恢复性较差的运维动作。
- 用户只使用本次启动日志输出、带 token 的 `DSH_URL`；裸 URL 和旧端口不是当前产品入口。

### 4.3 健康、历史和处理中状态

页面必须区分三类事实：

| 事实 | 权威来源 | 语义 |
|---|---|---|
| 当前进程在线 | `/v1/operations` heartbeat | API/worker 是否活跃 |
| 当前 Run 状态 | Run lease、DSH link、Artifact | queued/running/terminal |
| 历史失败 | Hub 账本计数 | 审计历史，不等于当前服务离线 |

本卡先在全新隔离卷验证当前投影。若长期 Run 在新实例复现，按 lease/DSH link 恢复语义修 Kernel；
若只存在于旧卷，保留历史并在执行日志标为旧实例资产，不通过改 UI 隐藏。

### 4.4 DSH 和 Decision Desk 验收

官方 DSH Web 必须验证：

- `decision-hub-inbox` 可见，空态不是“生成中”；
- watching、运行、`research_only`、失败和 report-ready 状态不互相伪装；
- 点击有 Session 的项目进入官方 Session/Trajectory；
- 终态 report detail 暂缺时显示 canonical fallback，不无限 loading；
- 默认页面不倾倒 raw JSON。

Decision Desk 必须验证：Inbox、Recent events、System health、Role/Pack/Runtime/Gate、来源尝试、
成本、Forecast coverage、Personal assets；桌面和移动无横向溢出，控制台 error 为 0。

### 4.5 回溯事件与事件窗口的协议化规划

不能再用 Prompt 猜测 EventWatch。按“协议先行、单一事实源”做以下增量实现：

1. canonical `ResearchSessionRequest` 增加可选 `event_watch` 和 `event_window_samples` 投影，直接复用
   已有 `event-watch.v1`、`event-window-sample.v1`，不新建第二套 DTO。
2. `ResearchRequestFactory` 只读 `EventWatchService`，把当前 Run 的 Watch 和 sample 状态写入请求；
   LangGraph checkpoint、DSH prompt 与回放因此保存相同的确定性规划上下文。
3. DSH capability playbook 只在请求中存在匹配 `event_id` 的 Watch，且所需 offset 已有
   `captured` sample 时，建议携带 `requested_event_offsets`。具体 `event_at/window_start/window_end`
   继续由 Gateway 从 durable Watch 推导，模型无权填写。
4. 无 Watch 或 `baseline_status=unavailable` 时明确进入 `retrospective_only` 语义：先取官方正文、
   当前/背景 typed facts 和独立来源；不得重复请求不存在的历史窗口，不得用 current snapshot
   冒充事件前 baseline。
5. `required_event_offsets` 仍由 `crypto_macro` Pack 拥有。缺失历史窗口时语义 Gate 必须保留
   `no_baseline/window_missing`，最终只能 `research_only`；这不是运行失败，也不能伪装成事实充分。
6. Gateway 的 EventWatch、PIT、offset 与 event lineage 校验保持不变。此次不新增工具、不新增 loop、
   不改变 DSH/LangGraph/Hub 所有权。

代码落点仅限 canonical schema/codegen、`ResearchRequestFactory`、DSH profile 及对应测试和模块文档。

### 4.6 跨轮事实幂等

- `fact_id` 相同时，FactStore 必须继续校验 evidence、requirement、来源、字段、值、单位、窗口、
  `payload_hash` 和 attributes；任何语义差异仍报 `research_fact_identity_conflict`。
- 若唯一差异只是 `observed_at/received_at`，且相同 `payload_hash` 已在同一 Run 接受，则返回首次
  持久化 Fact，不覆盖时间、不新增记录。这与 `research_fact_payload_hash()` 排除易变接收时间的
  既有定义一致。
- 不允许按 provider 名称特判 FRED；规则属于 Hub FactStore，所有 typed provider 共用同一幂等语义。
- 增加“相同语义、不同接收时间幂等”和“相同 fact_id、不同值仍冲突”测试，防止把冲突检查放宽成
  静默覆盖。

### 4.7 Source Registry requirement 命名空间

- `source_manifest.yaml` 继续是 `requirement_id -> canonical_requirement_id` 唯一映射源；
  `source_registry.yaml` 只保存 Pack 内部 dotted ID，不双写 canonical ID。
- `ResearchSourceRegistry.from_pack()` 必须复用 `CryptoMacroFactPack` 解析结果建立双向别名索引；
  Registry `require()` 同时接受内部 ID 和 canonical ID，并规范化后再与来源政策比较。
- 映射缺失、重复或歧义必须在启动/测试时 fail-closed，不能在运行时猜测下划线与点号转换。
- `create_server()` 真实装配路径必须有组合测试，证明 `official.macro + event_identity` 能通过 Registry；
  不能只测试内部 dotted ID。

### 4.8 `official.macro` 能力边界

- 当前 `official-event.v1` parser 只产生 `event.identity` 三个 typed Fact：actor、event time、revision status。
- 对 `policy_or_data_delta`、`counter_thesis` 等其他 requirement，`official.macro` 必须返回稳定的
  `research_capability_requirement_unsupported`，不得把 event identity Fact 改标签冒充 delta。
- 官方讲话正文仍可由 `web.fetch` 作为可审计 Evidence 提供给 DSH 推理，但不能关闭要求
  `current/baseline/delta` 的 typed semantic gap。真正关闭该 gap 需要以后新增 Pack 声明的
  baseline-aware parser/provider，不在本卡暗中扩张能力。
- DSH playbook 只把 `official.macro` 用于 `event_identity`；policy delta 走 approved Fetch/Search
  收集正文与前次官方文本，并诚实保留 typed gap，直至有合法 parser。

### 4.9 文档内容类型边界

- 通用 HTTP Fetch 仅接受可解释的 HTML/XML/JSON/纯文本类型；未知或二进制响应返回稳定错误。
- PDF 在没有显式、可测试的解析 adapter 时返回 `research_document_media_type_unsupported`。
- 不根据 URL 后缀假定内容类型，不把 replacement character/二进制头截断后当正文 Evidence。
- 未来 PDF/OCR/ASR 必须作为独立 capability adapter 接入，输入仍归一为可审计 Text/Evidence 契约。

### 4.10 DSH 单一研究入口

- 在产品研究工作区、尚未关联 Hub Run 的 live Session 中，点击官方发送按钮或按 Enter 必须进入同一
  Hub intake；不得先发送普通 DSH 消息形成无账本研究。
- 扩展必须在捕获阶段阻止原始发送，并复用现有 `submit()`、idempotency key 和受管 Session 打开逻辑；
  不 fork DSH 前端、不修改 upstream 源码、不新增 intake API。
- Shift+Enter 仍只换行；已关联的 managed Session 恢复官方消息发送，用于围绕已有报告追问。
- 显式“建立研究任务”按钮保留为无障碍/回退入口，但两个动作必须产生同一个 durable 语义。

### 4.11 跨轮 capability ladder 可执行性

- Pack 可以声明比当前部署更多的 provider/fallback，但每轮投影给 DSH 的 ladder 只能包含本 Run
  `allowed_capabilities` 中可执行的项；优先未尝试 route，确有新 Evidence 需要下一轮综合时才允许在总预算内
  复用已启用 route。DSH 原生 `web.search` 继续只作为已有 discovery marker。
- `requirements_for_round()` 负责交集和顺序，`_trusted_plan()` 只投影真实可执行计划；不得在 DSH
  完成以后才因禁用 fallback 抛异常。
- 没有剩余可执行 capability 时，外层 graph 必须有界 finalize 已保存的 synthesis/Evidence，交给 Hub
  Gate 输出 `research_only/degraded`；不能把“事实不足”转换成 `failed`。
- 真实 provider、PIT、合同或 attestation 错误仍按既有规则 fail-closed，不能用本修复吞掉异常。

### 4.12 通用 Fetch 的 Source Registry 准入

- `create_server()` 必须把同一个只读 `ResearchSourceRegistry` 注入 `web.fetch` 和 `official.macro`，
  不得在 composition 层形成两套来源判断。
- `web.fetch` 在创建 Evidence 前必须同时通过 `allow_fetch` 与 `allow_evidence`；只允许 discovery/fetch 的
  locator 不得因 capability 域名白名单而升级成 Evidence。
- Candidate 的 authority 来自已批准 source policy；publisher domain 仍作为 `source_id`，Registry ref 写入
  `structured_payload_ref`，以保留独立来源与审计 lineage。
- capability manifest 仍是网络访问上界，Source Registry 是业务证据准入上界；两者取交集，不互相替代。

### 4.13 DSH session-scope identity 投影

- 官方 slot 回调 props 是当前视图的 Session identity 唯一来源；插件不得从 URL、DOM、localStorage、
  Workspace 当前项或 Hub 再反查一份“当前 Session”。
- `conversationSessionIdOf(props)` 继续兼容官方直接 `sessionId` 与已存在的嵌套 `session.sessionId`，并负责
  trim/空值归一。任何需要包装后再渲染子组件的注册函数，都必须先显式读取一次，再传入普通可枚举
  `{ sessionId, ...pluginOwnedProps }`；不得直接 `{ ...props }` 假设 Host props 可枚举。
- `ResearchReportView`、`ResearchInboxView` 和 `ResearchIntakeControl` 只消费显式投影后的 Session；
  顶部 `HubStatusUtility` 仍直接消费官方 props。四个表面必须对同一官方 Session 查询同一 Hub Run。
- 只在 DSH Client plugin adapter 层修复，不 fork/修改 DSH upstream，不新增 schema、API、账本或状态存储。
- 刷新、切换“主动研究/研究报告/轨迹”后，Session identity 必须保持；同一真实 Run 的 header、报告、
  Inbox 打开动作和 Intake managed 状态不得出现互相矛盾的关联结果。

## 5. BDD

```gherkin
Scenario: 旧镜像不能冒充当前产品
  Given Compose 已存在一个只实现通用 health 的旧 Hub 镜像
  When owner 运行产品启动器
  Then Compose 必须构建当前工作树
  And 启动器必须验证 research-inbox-view.v1
  And 契约不匹配时不得启动 DSH Web

Scenario: 新实例没有幽灵运行任务
  Given 使用新的隔离 project 和持久卷
  When API 和三个 worker 完成 heartbeat
  Then Operations 显示预期服务在线
  And Inbox 空态不得显示研究生成中

Scenario: 主动研究可从 DSH 发现
  Given scheduler admission 一个未来或迟到事件
  When worker 创建或恢复 DSH Session 并提交终态
  Then DSH Inbox 无需用户先提问即可看到该研究
  And Hub、Session、Artifact、通知和复查具有同一 lineage

Scenario: 事实不足诚实停止
  Given 缺少授权分钟级宏观或 expectation pricing Fact
  When DSH 已穷尽允许的 Search/Fetch/typed provider 路由和预算
  Then 结果为 research_only/provider_blocked/insufficient_sources
  And 页面展示具体 requirement 与 provider attempt
  And 不用 Web Search 摘要或当前快照伪造事件窗口事实

Scenario: 人工回溯事件不请求不存在的窗口
  Given 人工 TextEnvelope 已创建 Run 但没有 durable EventWatch
  When Hub 构建 ResearchSessionRequest 并交给 DSH
  Then 请求必须显式投影 event_watch=null 和空 sample 集合
  And DSH 必须使用非窗口官方/当前/背景事实继续有界检索
  And 不得反复提交 requested_event_offsets
  And 最终必须标记 retrospective_only/baseline_unavailable 或对应事实缺口

Scenario: 已捕获的未来事件窗口可被 DSH 使用
  Given EventWatch 属于同一 event_id 且所需 sample 均为 captured
  When Hub 构建请求和 capability playbook
  Then DSH 只可请求这些已捕获 offset
  And Gateway 从 durable Watch 推导可信事件时间和窗口边界
  And 模型不得自造 event_at 或 window bound

Scenario: 同一 typed fact 跨轮重读保持幂等
  Given 同一 Run 已持久化一个 payload_hash 相同的 Fact
  When 下一轮在更晚 observed_at/received_at 再次读取相同语义值
  Then FactStore 必须返回首次持久化 Fact 且不新增记录
  But 字段、值、来源、窗口或 payload_hash 变化时仍必须拒绝身份冲突

Scenario: Registry 接受 canonical requirement 且不双写配置
  Given Source Registry 使用 Pack 内部 event.identity
  And source manifest 映射 event.identity 到 event_identity
  When 真实 MCP composition 以 event_identity 调用 official.macro
  Then Registry 必须批准匹配的 Federal Reserve URL
  And 未声明的 requirement 仍必须拒绝

Scenario: 官方事件 parser 不冒充政策变化 parser
  Given official.macro 当前只实现 official-event.v1
  When DSH 以 policy_or_data_delta 请求该 capability
  Then capability 返回 requirement unsupported
  And 不得生成 current/baseline/delta 或错误标记的 event identity Fact

Scenario: 二进制文档不进入 Evidence
  Given Web Fetch 收到 application/pdf 且没有 PDF parser
  When adapter 检查响应媒体类型
  Then 返回 research_document_media_type_unsupported
  And Hub 不得持久化 PDF 二进制乱码 excerpt

Scenario: DSH 产品入口不能绕过 Hub
  Given live 产品研究工作区的 Session 尚未关联 Hub Run
  When 用户点击官方发送或按 Enter
  Then 扩展必须创建同一 canonical Hub intake
  And 不得先触发普通 DSH 对话回合
  But 已关联受管 Session 的后续追问仍由官方 DSH 发送处理

Scenario: 禁用 fallback 不得推翻已完成研究回合
  Given Pack 声明 market.crypto_crowding 但当前 Run 未启用它
  And 前一轮已尝试 market.crypto_derivatives
  When graph 为下一轮重排 capability ladder 并映射 DSH 结果
  Then 未启用的 fallback 不得进入该轮可执行计划
  And 成功提交的 synthesis 与耐久 Evidence 必须进入有界 finalize
  And 最终可为 research_only/degraded 但不得变成无可提交结果的 failed

Scenario: 通用 Fetch 不能绕过来源审计
  Given web.fetch 的 capability manifest 允许访问某域名
  When Source Registry 只允许该来源 fetch 而不允许 Evidence
  Then adapter 必须在 Evidence 创建前拒绝
  And 已批准的 Federal Reserve 页面必须保留 official authority 与 Registry lineage

Scenario: 嵌套视图不能丢失官方 Session identity
  Given DSH 以 session-scope slot props 提供一个非枚举 sessionId
  And 顶部 Hub 状态已按该 Session 关联正式 Run
  When 用户切换到研究报告、主动研究或输入区插件表面
  Then 包装器必须显式投影同一个 sessionId 给嵌套组件
  And 研究报告必须读取该 Run 的 canonical Artifact，而不是显示未关联空态
  And 不得创建 URL、localStorage 或第二份 Session 映射作为补丁
```

## 6. TDD、自测和证据

执行顺序固定为：

1. launcher Red：断言 Compose 带 `--build`，脚本包含 canonical Inbox 契约探针。
2. launcher Green：最小修改 `run-product.sh` 和模块文档。
3. EventWatch Red：无 Watch/回溯 Watch 时 playbook 不得建议窗口查询；已捕获 Watch 只暴露可信 offset。
4. EventWatch Green：schema codegen、RequestFactory 投影和 profile 条件提示最小实现；Gateway 不改。
5. FactStore Red/Green：重现跨轮相同事实的时间差冲突；仅忽略易变接收时间，语义差异仍拒绝。
6. Registry/official/PDF Red：真实 canonical ID 组合失败、parser 越权和二进制误收必须先被测试复现。
7. Registry/official/PDF Green：只改 Pack 解析边界与 adapter 能力检查，不放宽 Gate/Registry。
8. Ladder Red/Green：禁用 fallback 不进入新一轮；成功 DSH synthesis 不被结果映射阶段推翻。
9. Fetch Registry Red/Green：通用 Fetch 与 official adapter 复用同一 Registry，并校验 Evidence 许可。
10. DSH 入口 Red/Green：普通发送和 Enter 归一到现有 Hub intake；managed Session 后续消息不拦截。
11. Session identity Red：用非枚举 `sessionId` 证明对象展开会丢失官方 session-scope identity，三个包装器
    都必须失败；直接读取标准 props 的 header 对照保持通过。
12. Session identity Green：新增纯投影 helper，报告、Inbox 和 Intake 注册包装器显式传递规范化 Session；
    不增加第二份状态或修改 upstream。
13. 工程门：全量 pytest、Ruff、Pyright、contract codegen、module docs、`git diff --check`。
14. 前端门：Decision Desk test/build；DSH extension test/build。
15. 隔离运行门：新 project、新端口、新卷启动当前代码；API/worker heartbeat、Inbox 和 DSH readiness 全绿。
16. 真实文本 Run：DSH 继续有界 Search/Fetch/typed-provider loop；无历史 baseline 时诚实
   `research_only`，Run/Session/Inbox/Artifact 全部进入一致终态。
17. 浏览器门：刷新同一真实 Session 后，顶部状态、研究报告、主动研究、轨迹和 Decision Desk 必须关联
    同一 `run_id/dsh_session_id`；桌面与移动截图、console=0、无横向溢出、终态/空态语义正确。
18. 证据写入 `docs/evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md`，不覆盖历史数字。

普通 pytest 必须离线。Live Provider/Search canary 单独记录，禁止输出或提交密钥。

## 7. Checklist 与停止线

- [x] 启动器强制构建当前工作树；
- [x] 通用 health 与 PD 产品契约双探针通过；
- [x] 隔离实例 API、三个 worker、research MCP 和 DSH Host 同属一套配置；
- [x] 新实例无长期幽灵 Run；旧实例历史不删除、不改写；
- [x] canonical request 显式携带可选 EventWatch/sample 上下文，DSH 不再猜测窗口可用性；
- [x] 无 Watch 的人工回溯 Run 不重复触发 `research_event_watch_not_found`；
- [x] 跨轮相同 typed fact 不再因易变接收时间触发 identity conflict；语义冲突仍 fail-closed；
- [x] Source Registry 从 Pack 单一来源接受 canonical ID，真实 MCP composition 不再错误拒绝 event identity；
- [x] `official.macro` 不再被引导或允许冒充 policy delta parser；
- [x] 未解析 PDF/二进制正文不再进入 Evidence；
- [x] 未启用的 Pack fallback 不进入跨轮可执行计划，也不再抹掉已提交 synthesis；
- [x] `web.fetch` 与 `official.macro` 共用 Source Registry，authority/准入不再绕过来源政策；
- [x] DSH live 研究工作区的官方发送/Enter 与显式按钮都进入同一 Hub intake；
- [x] session-scope props 即使为非枚举属性，报告/Inbox/Intake 包装器也不会丢失官方 Session identity；
- [x] 刷新并切换报告/轨迹后，同一真实 Run 的 `run_id/dsh_session_id` 关联保持一致；
- [x] DSH Inbox 空态、运行、终态和失败投影通过；
- [x] Decision Desk 桌面/移动、console 和 overflow 通过；
- [x] 全量自动化质量门通过并记录；
- [x] PD-04～06 状态文档与实现证据一致；
- [x] `PD-00..06 engineering complete` 与 `PD-07 observation pending` 明确分开；
- [x] 不宣称预测准确、盈利、自动交易或正式分钟级金融事实充分。

运行探针口径：Hub 使用 `/health/ready` 加 canonical Inbox 契约探针；DSH Host 使用带 build identity
的 readiness；Research MCP 使用 TCP 就绪并由官方 MCP `initialize/list_tools/call_tool` 业务探针验证。
FastMCP 当前没有额外提供 HTTP `/health/ready`，对该路径返回 404 不应写成业务不可用，也不能写成
标准 HTTP readiness 已实现。若未来部署平台强制要求 HTTP probe，须作为独立运维兼容任务处理，
不得在这里复制一套 MCP Server。

若真实授权的 `macro.cross_asset_intraday` 与 `macro.expectation_pricing` 仍缺失，PD-04～06 可以完成
工程退出，但产品只能标记为 `personal research-only candidate`。PD-07 至少 14 天或 20 个未来高影响
事件的观察时间不可压缩、回填或伪造。
