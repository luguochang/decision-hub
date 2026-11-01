# Decision Hub V3.1：核心价值优先、任务全程可见、调度分阶段落地

日期：2026-09-08。已核查仓库主分支基线：`1f5a31da0da012c628737e5fa3bf8a3d04b344f1`。
状态：**实施计划与验收契约；未修改目标仓库，未运行真实模型、数据供应商、DSH 或调度服务。**

本版替代 V3 的实施顺序与“将来调度只预留接口”的不足。原有安全和数据正确性约束继续适用，但不再要求先完成整个控制平台、观测平台和数据库重构，才交付第一份真实研究。本包中 `archive/` 不作为并行开发指令，`reference/` 是原有控制语义参考，不是第四个平台。

## 0. 当前决策，一次说清

目标是一个**能得到可信材料、主动补证、实际独立复核、给出可核验主判断，并让用户看到任务进展的决策研究工具**。不是只做新闻摘要，也不是先造一个通用任务平台。

采用：现有 DSH + Hub + LangGraph + Provider/Gateway；一份中文业务工作区；SQLite 现有持久队列；必要范围内的真实子任务；LoongSuite/Langfuse 保持诊断路线。保持单 owner。

第一批就交付“手动提交 → 真实取证 → 判断/限制 → 可读结果 → 可见过程”。必要安全修复随这条链落地；不会把数据和 Agent 的改造排到第五、第六个纯基础设施阶段后。

调度不是永久停用、不是随手重启旧定时器：它是后续明确的产品里程碑，包含任务模板、规则、未来排期、实际触发、跳过原因、关联执行与结果。**核心验证通过后，先上线可视化排期预演，再由 owner 开启实际调度。**

## 1. 三类工作与边界

| 类型 | 现在做什么 | 暂不做什么 |
|---|---|---|
| 直接构成核心效果 | 任务策略、真实取数、正文与基线、历史背景、检索回读、计算、上下文、独立复核、可读报告 | 不能以基础设施更重要为由长期推迟 |
| 核心必需的可靠性 | owner 认证、单主任务、幂等、既有租约续期与旧执行隔离、原子提交、受限工具与预算、取消边界 | 不将其扩大为通用身份系统、分布式调度平台或完整计费产品 |
| 核心通过后扩展 | 完整排期日历、定时执行、复杂日历触发、容量优化、更多品种/资料 | 暂不多租户、Redis/Celery/Kafka、自动交易、插件市场、复杂可视化编排器 |

必需安全约束不能取消；但通用化、自动修复、无中断升级和全供应商预算中心不是首份报告的先决条件。

### 1.1 两个阶段门，而不是两套系统

- **G0：允许受控试跑。** 只有 owner 可以发起；不自动派生新 Run；一个活跃 root；用量/次数/时限有界；旧执行不能覆盖；提交终态一致；实际动作有记录。未知远端状态可暂停并要求人工处理，不强求先实现所有自动恢复。
- **G1：值得自动化。** 代表任务和留出任务的研究质量、人工返工、时间正确性与可复查性达到预先冻结的要求。通过后才实施/启用周期调度。不能只凭演示顺利或 33 个参考测试通过。

## 2. 产品范围与验收任务

首期用 BTC 宏观研究作为一条纵向主链，不把 BTC 写进通用核心。ETH/SOL、PPT 等先保留接口扩展可能性，不增加本轮产品范围。

### 2.1 第一份真实研究要回答什么

代表输入：用户给出一个宏观事件或当前市场问题，要求结合实际数据与历史背景形成 BTC 研究判断。

交付必须包括：

1. 当前最有依据的判断；无法支持方向时说明具体缺口与条件，不硬编概率。
2. 发生了什么、与基线相比有什么变化，原文或数值引用在哪里。
3. 实际市场与相关指标如何变化，时点/单位/计算口径是什么。
4. 支持与反对材料，各自支持到什么程度；不把同时变化直接说成因果。
5. 什么新信息会改变判断、哪些条件失效、建议何时/因何复查。
6. 本次哪些工作实际做了、哪些失败/跳过；每项关键结论可回读证据。

“当前研究”与“历史事件复盘”必须使用不同任务策略，不能把当前快照放进过去的事件窗口。

### 2.2 预先固定三种代表情况

| 情况 | 核心价值测试 |
|---|---|
| 正常资料较齐 | 能整合原文、历史/当前数据，形成有支持的主判断 |
| 首轮信息不足 | 同一工具可针对新目标继续查，最终补上关键资料或准确说明不可得 |
| 证据矛盾或旧信息混入 | 真正复核、纠正时间/单位/归因，保留分歧而不多数投票 |

首次开发选择一个真实问题，再用三类案例拓展；不是每次选一条最容易输出漂亮报告的新闻。

## 3. 两类可视化：当前执行必须现在做，未来排期后续做

### 3.1 第一阶段就有任务列表与任务详情

在 DSH 的自有业务视图中，保留当前已有页面能完成的部分，只补这条主链所需组件。默认展示中文。

任务列表：问题/数据作业名称、类型、状态、提交时间、触发者、当前阶段、是否阻塞、更新时间、结果入口。
任务详情：工作计划、实际执行、关键发现、资料缺口、复核、结果。每一项计划都显示 `planned/ready/running/partial/completed/failed/skipped/cancelled` 等适用状态的中文投影。

这些是拟定业务投影，不要求立即改掉所有旧 RunStatus 枚举；底层沿 canonical schema 增量映射。

必须区分：计划要做、实际已启动、实际完成、因条件不满足未执行。计划节点显示为待办，不能画成真实 Agent。

独立子任务、手动历史回填、长解析等有用户价值的异步作业都进入任务投影；底层可以保留不同表，不强求一开始建立统一万能 Job 继承树。细粒度网络重试折叠在该任务内，不将每条心跳/token 都变成任务卡。

同一页面能查看结果、任务进度、依据和关键失败，不要求用户先打开 Langfuse 才知道“有没有在做”。

### 3.2 调度阶段再增加任务面板和排期日历

| 区域 | 内容 |
|---|---|
| 任务模板 | 用于哪类研究/数据更新、输入、默认任务策略、成本与有效期约束 |
| 调度规则 | 单次/每天/工作日/固定间隔、时区、启停、owner 授权、重叠/漏跑策略 |
| 未来排期 | 日/周/月或列表，预览后续触发，显示预计时间，不承诺准确开始/结束 |
| 触发记录 | 到期、已入队、跳过、超期、权限/预算阻断及原因 |
| 实际执行 | 关联 Run/DataJob，沿当前同一任务详情查看真实动作与结果 |

调度规则和未来预览不是已经发生的任务。UI 必须明确标“预计触发”；实际触发时间、入队时间、开始时间和完成时间分别记录。

不要先造空日历、虚构排期、不可点按钮。当前不开放排期路由；进入对应里程碑后才露出完整可用入口。

## 4. 总体架构：调度以后接入，不改变核心研究

```text
当前：固定 owner → DSH 自有业务输入 → Hub Admission → 同一持久队列
未来：owner 批准的 Schedule → TriggerFire → 同一 Hub Admission → 同一队列
                                                  ↓
                                       Research Worker / 数据 Job
                                                  ↓
                               固定任务依赖 + DSH 实际研究与子任务
                                                  ↓
                 本地资料 / 实时 Provider / 搜索与回读 / 确定性计算
                                                  ↓
                                    复核 → 综合 → Hub 原子提交
                                                  ↓
                                 Task/Report Query → 同一业务视图

LoongSuite + 少量 Hub spans → Langfuse（诊断旁路）
```

禁止 scheduler 直接调用 DSH 或另外复制研究图。暂停调度仅停止新触发，不应使已有报告不可读，也不默认取消运行中的研究。

### 4.1 当前就保留的扩展接缝

- 沿既有 `admission_origin`/Run 关联添加服务端可信触发说明，不在各层散落永久 `origin == manual` 断言。
- `TriggerPolicy` 现在只允许 owner 手动命令；未来允许被 owner 批准的 schedule service acting-on-behalf-of-owner。
- 同一 admission 负责权限、去重、参数校验、容量和预先批准的预算。调用方 body 不授予自己的来源或权限。
- 当前只需要 task kind、parent/root、attempt、状态/原因、输入/结果引用；完整 Schedule/Fire 表放到调度阶段迁移。
- `next_review_at` 现在是建议信息；不能自动建立定时 Run。将来 owner 从建议创建/批准规则。

“可扩展”不是现在给未实现的调度开一个接口，也不是把未实现未来 schema 都加 nullable 字段。

## 5. 第一条链具体如何改

### 5.1 M01：最短的真实纵向闭环

第一批合并请求可以按小 PR 顺序提交，但里程碑不是“控制层完成”，而是第一份有证据的研究已完成并可见。

| 切片 | 必须改 | 不做 |
|---|---|---|
| M01-A 最小受控入口 | owner/service auth；手动模式；修 post-commit 改失败；接既有续租/epoch；工具权限/提交验证当前执行代次；DB 序号 | 新账户平台、消息 broker、全量自动对账框架 |
| M01-B 首个真实研究 | 任务要求按问题选择；实际官方原文/基线 + 现有行情；正文解析；相关片段/引用；至少一次真实缺口补查；干净综合 | 全市场数据平台、全部三个月指标作为首份输出前提 |
| M01-C 同步呈现 | 独立研究输入、任务/步骤状态、证据卡、结果、失败/取消 | 重排所有导航、复杂图编辑器、日历、重型观测平台部署 |

M01-A 保留 G0 不变量，限定单机、单活跃 root，主动隔离不确定远端执行。不要为了赶 demo 删除认证/时间真实性/预算与终态保护。

真实独立复核从 M01 就做一项最小任务：在干净子上下文中核对关键结论及引用。先用已注册、确实可执行的两个角色职责即可；独立不要求并行。广度并行和三个月完整背景在 M02。

若 DSH 独立调用、工具身份或取消能力不满足，记录具体公开接口缺口；可以完成单 Agent 输出用于基线，但不得把 M01 多 Agent 验收标通过，也不得先去扩调度/换整个系统。

### 5.2 M02：补齐能改善结果的数据、上下文和独立研究

按一个明确缺口增加一个能力：

- 本地历史查询、实时刷新、原文回读统一接到既有 `ResearchCapabilityGateway`。
- 修 `document.text[:4000]`、发布时间、目标 RSS 匹配与当前/基线对比。
- 替换 `_input_evidence()[:50]`，保留新证据、基线、反证的预算；所有引用可继续回读。
- ReAct 尝试以 query/target/fields/window/provider 区分，不因工具试过就禁止新查询。
- 原文研究与市场数据研究最多并行 2 个，提交独立结果；复核读固定结果版本；最后干净综合。
- 修关系 Gate：同一事件/品种/来源/单位的基线与后值配对；不同指标不能拼凑成完整事实。
- 手动回填任务实现并展示进度；加载三个自然月的核心数据集与明确覆盖。指标预热窗口单独取，不把三个月材料全塞 prompt。

M02 结束应已有：历史背景、实时缺口补查、真实任务回执与复核、可读主判断。不是只有 Provider 类、schema 和工具列表。

### 5.3 M03：质量裁决、唯一工作台与必要运行整理

用固定开发案例与独立留出案例比较；修主要差错；迁移有用 Desk 功能后退役重复前端；把现成 LoongSuite/Langfuse Trace 与 Task 对齐。

Langfuse 安装或漂亮调用图不阻塞 M01/M02 的业务任务可见性，但在 M03 完成已选诊断路线；不同时选第二套平台。

补齐真实失败所暴露的恢复、取消、版本兼容、备份。重型压测、分布式扩容仍不做。

只有 G1 通过，才进入可视化调度的 M04/M05。由此保证“调度”是在自动执行已证明有用的研究，而不是批量产生低价值报告。

## 6. 核心数据选型与处理

| 信息能力 | 先用什么 | 必须验证 |
|---|---|---|
| BTC 价格/成交量/资金费/OI | 现有 OKX/CoinEx 稳定端点；CCXT 只补已缺历史/多所能力 | 合约/现货、币数量/USD名义量、费率周期、历史最早可读日期 |
| 官方宏观背景 | 现有 FRED/ALFRED 扩历史/版本；必要时选择性 OpenBB | 单位、频率、修订、日级可知性，不当分钟事实 |
| 事件原文与基线 | 官方档案+已有搜索；HTML 主体解析优先复用 Trafilatura | 事件匹配、发布时间、正文定位、基线版本 |
| 复杂文档 | 真正阻塞任务时接 Docling 受限进程 | 文字层优先、表格/页定位、页数与内存限制 |
| 新信息发现 | 已有 DSH 原生搜索；已配置且许可通过的 fallback | 原文获取与来源身份；工具可用不等于信息充分 |
| 精确计算 | 领域实现包的纯函数 | 公式版本、输入引用、单位/时间窗口、缺值不补零 |

不得为了接更多项目重写已可靠 adapter；不整体嵌入 TradingAgents/FinRobot/另一个 Harness。开源库的接口能力、代码许可、数据访问和用户所在环境的可达性分别验证。[S-DATA]

### 6.1 三个月加载的范围与用途

以 2026-09-08 为例，展示窗口从 2026-06-08 到同步截止时点。首批列出每个数据集 actual coverage，而不是默认全齐。

价格与成交量以小时背景为先，需要事件分析时再取具体分钟窗口；funding/OI按接口实际覆盖；宏观按原频率/版本。事前一致预期、完整期权历史、订单簿等缺乏权限或可回填来源时，记录缺口，不用近似指标冒充。

数据在一个手动 DataJob 中分区回填，可见 queued/running/partial/failed/completed 与行数/时间覆盖。分区不可变，manifest 原子发布；运行中 Run 固定版本。历史回填不能触发一批“新事件研究”。

区分 current_research / observed_replay / reconstructed_history。`received_at` 永远真实；历史重建不算前瞻试验。三个月分位与案例不是经过校准的收益概率。

## 7. 任务研究协议与实现接缝

沿当前 contracts/schema codegen 增量修改，不把本节 DTO 同名复制成第二套正式类型。

### 7.1 主路径伪代码（不是已存在的 DSH API）

```python
async def execute_research(claim):
    # claim 携带真实执行权；runtime 方法是项目端口，需绑定公开 DSH API
    request = load_and_validate_request(claim)
    policy = product.select_task_policy(request)
    snapshot = await prepare_relevant_data(request, policy)
    tasks = persist_actual_plan(claim, policy, snapshot)
    # G0：生命周期处理必须随本函数存在；不把网络工作放进事务
    async with lease_guard(claim):
        findings = await execute_required_tasks(tasks, snapshot)
        # 预期来源失败返回 partial，不把所有 sibling 取消；程序错误传播
        review = await execute_independent_review_if_required(findings, policy)
        if review.needs_new_evidence:
            findings = await bounded_gap_followup(findings, review)
        report = await synthesize_in_fresh_context(findings, review)
        validate_claims_time_and_permissions(report, policy)
        return commit_atomically(claim, report)
```

`execute_required_tasks` 必须生成实际 receipt。伪代码不规定每次恰好搜索几轮或启动四个 Agent。一个明确足够的问题不必强行全团队。

### 7.2 最小持久字段

| 对象 | 当前需要的字段 |
|---|---|
| Run | 请求hash、可信owner/origin、任务策略/版本、状态/phase、时限、执行epoch、产物引用 |
| Task | root/parent、kind、question、required、dependencies、状态/原因、input revision、result refs |
| Attempt | task/attempt、operation_id、原生Session/Turn、submit状态、真实receipt与结果hash |
| ContextManifest | 选材/规则/工具版本、时点、token预留、输入指纹、selected/effective保证级别 |
| Claim/Review | 主张/目标主张、支持/反证、提出者、处理结果及限制 |
| Event | 稳定id、Run/Task/Attempt、DB序号、状态变化、简短业务说明、资料/产物引用 |

计划和实际执行是两个事实。若动态补查新增任务，先持久计划revision/新增原因，再显示；不得事后填一个“早就规划好”的历史。

### 7.3 UI/API最小改造

保留已存在提交、查询、commands、events URL；新 task/data 查询沿现有 QueryService 扩展。Root/owner身份由服务器注入，前端只能请求允许操作。

详情以轻量 snapshot+cursor获取，任务详情/原文按需读取；不要每250ms读取整份报告。状态事件可使用已存在SSE；没有必要在首个切片换传输协议。

即使 Langfuse 缺失，也显示真实任务状态和结果。技术Trace多棵可按Task关联，不要求拼超级Trace。

## 8. 必须保留的可靠性，以及明确推迟的性能工作

### 8.1 现在只保留 G0 的小范围实现

| 条件 | 本轮处理 |
|---|---|
| 非owner能触发 | 固定owner可信认证；公开/旧绕行入口禁用；内部callback不同scope |
| 提交后被日志错误改成失败 | 结果/终态/完成事件/必要Outbox同事务；派生错误不改终态 |
| 超过租约发生重复执行 | 接已有续租，增量epoch检查；单root、无不明状态自动重派 |
| 取消后迟到结果发布 | DB撤销执行授权；旧epoch不能提交；远端仍执行显示unknown而非假停 |
| 重复请求/不同payload复用key | 原请求hash比对；相同请求返回原Run，不重复计费派发 |
| 事件序号竞争 | 已有研究路径用DB原子序号，不能仅实例Lock |
| 预算/重试失控 | 沿已有限制执行；次数/token/deadline/已授权费用路径有界；未知不记0 |
| 失去远端回执 | 已有Session/op查证；unknown暂停处理，不盲目重派；全自动恢复后续完善 |

不把这些修复扩大成通用授权服务器、分布式事务协调器、调度引擎或全公司账单中心。

### 8.2 资源模型

建议初始仍 root=1、等待最多5、研究leaf并行≤2、Gateway HTTP≤4/单provider≤2、解析进程≤1。用现有配置承载并实测，不为此新造一个资源调度服务。DSH原生调用的限制要单独核实，不能宣称受Python Semaphore自动控制。

网络用异步连接池；同步DB用短事务+受控线程，每调用独立Session；不可控CPU解析用受限进程。取消await不等于停止运行线程。父协调等待不占leaf permit。

优化只针对测得瓶颈：模型/网络延迟、同步阻塞、正文丢失、等待重复、查询放大、压缩后信息损失。暂不制定“达到多少QPS”作为核心验收前提。

### 8.3 本轮不做的性能/平台项目

Redis/Celery/Kafka；多机worker自动扩缩；复杂统一资源调度器；全量数据库迁移；高级优先队列；拖拽工作流编辑；向量/知识图谱先行；一口气接齐几十个Provider；多个观测后端；零停机热换插件。

通过当前限定配置解决的问题，不得为了“未来”扩大实现；不能用一个未知风险为所有外围工程打开无限范围。

## 9. 后续调度：M04/M05是正式功能，而不是重启旧定时器

### 9.1 明确选型

**默认路线：APScheduler 的 trigger/时间计算能力 + FullCalendar Standard React组件 + Hub自己的调度政策和业务回执。**[W1][W2][W3][W4]

APScheduler负责时间表达式、日期/间隔/cron计算等库能力；Hub负责owner授权、业务任务去重、是否允许派发及关联结果。FullCalendar只呈现计划/实际记录，不执行任务。

进入 M04 时固定经过验证的稳定版本，文档/接口/前端预览使用同一语义。3.x示例不得混用master/4.x配置。特别验证星期数字、时区、DST；默认界面用星期名称而非裸数字。

Prefect 的 schedule/Upcoming交互和Kestra的任务时长/触发/执行详情用作产品参考。[W5][W6] 不整体嵌入它们，不新增第二套Run和登录/执行后台。

FullCalendar Standard的基础日/周/月/列表与React集成可用；资源时间轴等属于Premium范围，不能默认其也属于MIT标准部分。[W3][W4]

### 9.2 调度模块边界（M04才实现）

建议落点 `packages/scheduling/`，同代码仓、先同机。提供内部 `ScheduleService`、`TriggerCalculator`、`DueDispatcher`，沿已有应用层和DB迁移；不是对外插件市场。

```text
ScheduleDefinition（owner授权、revision、规则、任务模板、预算）
   → PreviewOccurrence（只读计算，无副作用，无Run）
   → TriggerFire（实际到期、接收/跳过原因）
   → 同一 admission transaction
   → ResearchRun / DataJob
```

APScheduler在这里不再次持久化整套研究任务。采用库的trigger计算/单例唤醒能力，业务Schedule/Fire为唯一权威；不在每个FastAPI副本各开一个scheduler，不用APScheduler回调阻塞等待LLM完成。

`max_instances=1`最多约束该短callback，并不等于某业务研究只有一个活跃Run。业务重叠检查仍在Hub。

### 9.3 拟新增接口与数据

| 接口（新增设计） | 作用 |
|---|---|
| POST /v1/schedules/preview | owner输入规则，返回后续时间、时区/语义说明，不入研究队列 |
| POST /v1/schedules | owner保存draft，明确任务模板和版本 |
| POST /v1/schedules/{id}/commands | approve/enable/pause/resume/run_once，作用范围明确 |
| GET /v1/schedules | 规则和真实启用状态 |
| GET /v1/schedules/calendar | 预计触发和实际Fire投影，类型不可混淆 |
| GET /v1/schedules/{id}/fires | 到期/跳过/阻塞记录与关联Run/DataJob |

Schedule字段：id、revision、owner_subject、approval_ref、enabled、task_template_ref、parameters、trigger_spec/version、timezone、misfire_policy、overlap_policy、budget_policy_ref、next_due、last_fire。
Fire字段：schedule_id、schedule_revision、scheduled_for_utc、observed_at、admission_state、reason、run_id/data_job_id、唯一键。

唯一键推荐 `(schedule_id, scheduled_for_utc)`，revision记录在payload中；同一逻辑时间被配置更新不能暗中再派发一次。明确需要新计划时新建schedule或由owner显式run_once。run_once使用独立命令幂等key。

Schedule修改只改变未来未触发预览，不改已经admit的Run；暂停阻止后续新的admission，已接受任务继续，取消另走Run command。

### 9.4 触发规则与显示规则

| 情况 | 初始政策 | 用户必须能看见 |
|---|---|---|
| 规则尚未批准/未开启 | 不产生Run | draft/paused 与预览状态 |
| 正常到期 | 经同一入口建立任务 | nominal time、实际触发、Run链接 |
| 服务离线错过多次 | 默认仅合并最新一次且在grace内，否则跳过 | missed/coalesced/expired与原时间范围 |
| 同schedule已有活跃研究 | 默认跳过本次，不并行挤占 | skipped_overlap，关联活跃Run |
| 队列已满 | 默认记录skipped_capacity或在明确grace内有限待接收；首版选择skip | 不能显示成功、不能静默丢弃 |
| 当天预算耗尽 | 不派发 | blocked_budget，恢复条件 |
| owner撤销授权 | 不再派发 | disabled_authorization |
| 时区规则更新 | 后端计算预览，保存规则版本 | 预览改变，不伪造历史 |
| 提交回执丢失 | 用Fire幂等key查相同Run | pending_reconciliation，不重复派发 |

M05首版选择：日期/每日/工作日/固定间隔；复杂交易日排除和事件依赖排期后续再按需要做。cron自由输入可作高级入口，但不能让前端用不同解析器自行计算真实未来时间。

触发事务中同时插入Fire和Run/首事件，或复用既有outbox实现等价原子桥；网络/模型不在事务里。执行可靠性沿既有Runner，scheduler不复制。

### 9.5 主流程和调度权限的关系

现在仅owner手动提交有效；未来scheduler是受owner批准的服务身份，不能从请求body自报scheduler获得权限。Schedule只可使用批准的任务模板/参数/数据和预算，不保存或执行任意Python函数/shell字符串。

开启定时数据更新不等于开启定时研究。两类任务在面板中明确分开，分别授权。

## 10. 代码修改责任地图

| 部分 | 现有位置/新增落点 | 里程碑 | 交付行为 |
|---|---|---|---|
| owner与任务入口 | apps/hub_api/main.py、DSH Host、现admission | M01 | 同一句请求经可信入口持久接受，无legacy绕行 |
| 终态与lease | application/run.py、commit.py、worker/research.py | M01 | 不因派生记录失败改终态；当前执行者唯一有权提交 |
| 任务要求 | ResearchRequestFactory、packs、产品实现包 | M01–M02 | 按问题选证据合同，不全量要求 |
| 文档/数值 | provider_adapters、research/documents.py | M01–M02 | 实际读到相关原文和正确时间/单位的数据 |
| 检索/历史 | 新knowledge能力，沿ports/research.py/Gateway | M02 | 三个月可查询、实时补查、明确覆盖 |
| 上下文 | graph._input_evidence、runtime.profile、ContextAssembler | M01–M02 | 不机械前50；每Task可回读的必要材料 |
| 实际任务执行 | DSH plugin/runtime adapter、LangGraph外层 | M01–M02 | receipt/独立复核；M02增加两项取证并行 |
| 业务事件和查询 | research_observability、query_views/research、API events | M01起 | 已计划/已执行/失败/待补可见，刷新可恢复 |
| UI | extensions/dsh/decision-hub/src/client/ | M01起 | 自有输入＋任务列表/结果；无DOM拦截、无JSON默认墙 |
| 指标/关系Gate | product_extensions/crypto_macro、sufficiency | M02 | 计算可复算、错误基线拼凑不能过门 |
| 产物/Desk退役 | commit、query、Dockerfile、workspace/scripts | M03 | 普通产物不生成固定BTC Forecast；迁移后删除重复UI |
| 技术观测 | infra/dsh/observability、TelemetryRef adapter | M03 | LoongSuite自动采集、按实际任务Trace链接；不是新用户后台 |
| 模型质量评测 | evals/research_comparison与案例 | M01起/M03裁决 | 修facts口径；每阶段已有真实报告对照 |
| 调度预览 | 新scheduling模块、后端preview API、UI标准日历 | M04 | 无副作用的真实排期、规则/授权表单 |
| 调度执行 | 同模块DueDispatcher、同admission、Fire记录 | M05 | 计划→触发→Run完整可见，可停可查可去重 |

新增名字是实施落点建议，不是已经存在的接口。接入时先核对基线和未提交修改，不重建同义模块。

## 11. 成品验收：用少量真实任务及早判价值

### 11.1 M01就留基线，不能等M03才比较

在任何改动前保存首个问题的旧链路输出、实际资料、模型/provider配置、工具记录和人工缺陷。M01结束使用相同问题对比，明确改变在哪，不只给新截图。

M02用六个开发案例（覆盖三类代表情况）反复定位；M03使用至少一组未参与提示调优的留出案例，起点可为六个。数量是工程起点，不是统计显著或收益验证声明。

固定证据的实验用于定位上下文/分析效果；在线端到端对照用于资料获取效果，记录模型/预算/时间差异，不能把变化都归功于某个框架。

### 11.2 必查的六项

| 维度 | 检查 |
|---|---|
| 关键事实 | 是否覆盖问题必需信息，缺失是否被明确而正确地标注 |
| 时间/数字 | 是否使用对应时点、单位、基线，派生值是否可复算 |
| 引用支持 | 原文是否真支持主张，而不是只存在一个网址 |
| 独立复核 | 是否有真实回执，是否发现实际问题，是否能如实无异议 |
| 可使用性 | 能否减少用户二次搜索和改写，是否给出适用的主判断与变化条件 |
| 成本与可靠性 | 失败也计入，对比实际人工返工、时长与可得成本 |

通过条件：选定案例无伪造事实/历史、无未授权触发、无假独立任务；关键事实/引用不低于基线；至少一个重要的质量或返工维度有逐案例证据的改善；成本在owner批准范围。用户确认代表任务已可用于日常研究后开启后续调度。

不能把弃答数量增多本身视为质量提升；也不能为更明确建议而编造概率。不可回答案例的正确处理同样有价值，但不会替代正常案例的研究交付。

### 11.3 必要故障验收随主链做

同key提交、非owner拒绝、运行超过原租约、取消/完成竞争、提交后派生失败、第一次查不到但第二次新目标查到、长文后半段关键事实、旧观点污染、子任务失败、页面刷新、Langfuse离线。

大规模压测、全场景高可用不放到M01前；已知会破坏当前链的错误也不能留到“以后性能优化”。

## 12. 里程碑顺序与停止扩张条件

| 阶段 | 可见交付 | 进入下一阶段的依据 | 明确不做 |
|---|---|---|---|
| M01 | 一个真实研究问题贯通，含最小独立核验与可见任务 | G0 + 真数据、真实工具/任务、可回读报告 | 日历、消息broker、全量历史/观测部署 |
| M02 | 三个月核心背景、实时补证、两项独立取证＋复核 | 六个开发案例的资料/结果对照，覆盖缺口有界 | 更多产品、假统计预测优势 |
| M03 | 日常可用主链、唯一工作区、必要Trace与恢复、留出裁决 | G1；真实返工/关键遗漏改善 | 全面架构重写、多平台并存 |
| M04 | 可视化任务模板、规则、未来排期、历史关联 | preview无副作用、时区/版本/权限正确 | 真实自动研究、任意脚本调度 |
| M05 | owner启用的一条定时任务可见触发和执行 | 幂等、漏跑/重叠/队列/预算/暂停、结果仍达质量门 | 批量开启全部规则、自动交易、多租户 |

安全修复和首份报告的实现可以拆小PR，但M01内部不得扩展成新的横向平台阶段。

停止条件：若连续PR只交付接口/状态/图，没有真实报告，下一步回到当前案例；若报告质量无改善，不进入调度；若新增组件需要替换核心接口/库才能带来一个小能力，先评估薄适配而非继续全量迁移。

## 13. 维护与AI编码上下文控制

唯一入口：`AGENTS.md` → 本文 → `CURRENT_IMPLEMENTATION.json` → 当前 `work_orders/M0x.md` → 该卡精确源码。

当前任务只读所需章节，不把所有旧方案塞上下文。`archive/`只作解释性证据；旧T01–T09保留的技术步骤是参考食谱，不再是必须串行完成的项目路线。

每PR同时交付：改变的真实行为；精确diff；实际测试结果；当前研究报告/或明确失败原因；剩余风险。不得用“框架完成，价值以后再测”结项。

Core写路径/contract一次一个负责人；独立正文解析与UI可以按已冻结DTO并行开发。需要新增依赖要列出它关闭哪个具体缺口、增加什么运行负担、没有它能否先验证。

保留版本manifest、接口测试和一次恢复演练；升级不静默续旧checkpoint。依赖锁定不意味着永不升级，更不允许活跃Run内热替换代码。

## 14. 本包是什么、不是什么

本次更新计划、工作卡与当前状态；没有改目标仓库或真实部署。原有reference控制层/旧风险测试保留可运行，不新增一套调度运行程序，不安装APScheduler/FullCalendar或后台启动任务。

独立多Agent审查仍未执行；`reviews/`继续标NOT_RUN。本次开放源码核查与人工式设计分析不冒充独立团队结果。

reference是单元和局部故障语义，不能替代原仓库测试、DSH真实回执、真实数据和产品质量对照。旧测试通过不证明scheduler或未来产品界面可用。

## 15. 核查来源

核查日期2026-09-08。仓库main再次确认仍为`1f5a31d`。外部组件的当前文档不代表已经完成目标环境接入。

- [S-PLAN] 已提供的V3实施契约与源代码审查，特别是当前任务卡顺序、运行风险、未来调度接缝。
- [S-DATA] 已提供V2/V3的数据、检索、事实、上下文与成品要求；本版按核心价值重排。
- [S-GRAPH] `https://github.com/luguochang/decision-hub/blob/1f5a31da0da012c628737e5fa3bf8a3d04b344f1/packages/orchestration/langgraph/graphs/agentic_research_graph.py`：已有外层round/coverage/重规划和runtime调用。
- [S-WORKER] 同commit的`apps/hub_worker/research.py`、`application/run.py`、`application/commit.py`：既有执行与提交边界。
- [S-UI] 同commit的`apps/decision-desk/src/research/ResearchPage.tsx`、`extensions/dsh/decision-hub/src/client/index.js`：已有任务/事件/UI资产，不从空白重新做。
- [W1] APScheduler 3.x用户指南：`https://apscheduler.readthedocs.io/en/3.x/userguide.html`。
- [W2] CronTrigger及星期语义：`https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html`。
- [W3] FullCalendar React官方接入：`https://fullcalendar.io/docs/react`。
- [W4] FullCalendar标准/高级许可区别：`https://fullcalendar.io/pricing`、`https://fullcalendar.io/docs/premium`。
- [W5] Prefect排期/Upcoming与调度不执行任务：`https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules`、`https://docs.prefect.io/v3/concepts/schedules`。
- [W6] Kestra任务执行与Gantt/拓扑：`https://kestra.io/docs/ui/executions`。

**最终裁决：主流程价值第一，当前任务可视化同期交付，可靠性按本链必要范围修复；调度作为已定义的后续可视化产品，先预演再授权执行，复用同一核心而非另造平台。**
