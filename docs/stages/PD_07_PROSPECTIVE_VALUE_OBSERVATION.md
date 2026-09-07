# PD-07 前瞻价值观察与产品停止线

版本：`PD-07-2026-09-05.v1`  
状态：`entry ready / observation not started`  
前置：`PD-00..06 engineering complete`  
上级方案：[产品事实充分度与主动交付修复方案](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)  
执行日志：[PD-07 前瞻观察日志](../evaluations/PD_07_PROSPECTIVE_OBSERVATION_LOG_2026-09.md)

## 1. 阶段结论先行

PD-07 不再开发一个新 Agent、插件系统、账本或页面。它使用已经完成的 DSH -> LangGraph -> Hub ->
Pack 主线，观察未来真实事件，回答唯一产品问题：这套系统是否比 owner 直接使用通用聊天产品更快、
更可信、更少人工查证，并值得进入个人正式使用或小范围外部试点。

当前可以开始的是单 owner、单机、只读 `research_only` 观察。当前不能宣称的是预测准确、盈利、
分钟级宏观事实充分、自动交易或 DSH candidate 已 Promotion。真实分钟级
`macro.cross_asset_intraday` 和 `macro.expectation_pricing` 仍缺正式 Provider；这可能使最终结论成为
`retain_baseline` 或 `stop`，但不是放宽 Gate 的理由。

## 2. 不可变架构边界

| 所有者 | PD-07 中的唯一职责 | 禁止 |
|---|---|---|
| DSH | 唯一 Supervisor、Agent Loop、Tool/Skill、Session、Trajectory、官方 Web 主入口 | 直接写 Fact/Forecast/Outcome 或获得发布权 |
| LangGraph | Run 生命周期、checkpoint、恢复、轮次、预算与确定性路由 | 新建第二个智能体循环或金融语义 |
| Hub | Event/Evidence/Fact/PIT/Artifact/Forecast/Outcome/Evaluation/Outbox 唯一账本和 Gate | 复制 DSH JSONL 或根据 UI 状态补写事实 |
| `crypto_macro` Pack | requirement、字段、单位、窗口、来源、Role 与 Gate policy | 把搜索摘要当精确行情或跨领域公共协议 |
| Provider/Search adapter | 定位来源或产生经过验证的 typed fact | 修改 Core、Gate、active pointer 或历史记录 |

PD-07 期间只允许修复阻断观察的根因缺陷。任何新 Provider、付费能力、字段、Gate、Role 或 UI 功能都
必须独立立卡并说明是否中止/重开观察窗口；不得在观察中悄悄改变候选版本。

## 3. 观察对象与样本准入

### 3.1 观察时钟

- 时钟从第一条合格未来事件的 `EventWatch.created_at` 开始，不从本文件日期或历史 Run 开始。
- 既有授权门为“至少 14 个自然日或 20 个合格未来高影响事件”。达到任一条件只允许做结项审查；
  为降低小样本误判，`promote` 默认要求两项都满足。`retain_baseline/stop` 可在明确失败时提前决定。
- 观察时间不能压缩、回填或用 replay/历史事件替代；代码测试、fixture 和昨天的讲话不计入样本数。

### 3.2 合格事件

观察必须覆盖三类事件：

1. `central_bank_speech`：央行主席/委员讲话、议息声明或记者会；
2. `macro_release`：CPI/PCE/就业/GDP 等有官方发布日期和 actual/prior/consensus 语义的数据；
3. `breaking_event`：突发政策、地缘或系统性市场事件。

合格样本必须有 canonical `event_id/run_id`，并保留来源、发现时间、事件时间和 Run cutoff。可预知事件
还必须在事件前创建 EventWatch；突发事件允许 `baseline_unavailable`，但不能计入“可预知事件 baseline
coverage”的分母。人工事后粘贴的旧新闻只能作为回溯研究，不计入 PD-07 前瞻样本。

### 3.3 固定候选与对照

- 观察开始时冻结代码 commit/worktree identity、DSH upstream/version、Extension build hash、Pack/Role、
  capability manifest、source registry、Gate policy 和 Provider route。
- Fixed baseline 保持 active；DSH candidate/shadow 只产生候选结果，不自动切 active pointer。
- 同一 Event/Snapshot 用相同 PIT cutoff 形成可比较输入。缺失对照时记录 `baseline_unavailable`，不补造。
- 修复安全或运行阻断 bug 后必须记录版本分界；影响事实、规划或 Gate 的变更从下一事件开始新 cohort，
  不能把变更前后样本混成同一候选。

## 4. 每个事件必须沉淀的资产

Hub 账本保存机器事实，本文档日志只追加引用和人工结论，不复制大段 JSON：

```text
Event + EventWatch + window samples
  -> Run + DSH session/trajectory ref
  -> tool/provider attempts + Evidence + Fact + PIT
  -> Coverage/Gate + Artifact/Forecast + notification/recheck
  -> 30m/24h/72h Outcome (到期后)
  -> ResearchValueEvaluation + owner usefulness/manual verification time
```

每个样本至少记录：

- `event_id/watch_id/run_id/dsh_session_id/artifact_id`；
- event family、scheduled/observed/received/cutoff/finished 时间；
- baseline status、required/available offsets、PIT/future leakage 检查；
- Search/Fetch/typed provider 的 attempts、错误、延迟、费用和来源等级；
- hard/soft coverage、semantic rejection、stop reason、报告/通知/recheck 状态；
- owner `useful/partial/not_useful`、人工复核分钟数和一句具体原因；
- 有效方向 Forecast 到期后的 Brier/方向/净收益；`research_only/no_trade` 的这些字段必须为 null。

## 5. 指标与计算口径

| 指标 | 口径 | 通过线 |
|---|---|---|
| PIT/future leakage | 任何事实超过 Run cutoff 或用未来值补历史窗口 | 必须为 0 |
| semantic substitution | locator/错误指标/错误窗口关闭 hard requirement | 必须为 0 |
| event-to-report 成功率 | 合格事件中在预算内得到终态可读报告的比例；`research_only` 可算报告成功，但不能算事实充分 | `>= 90%` |
| 可预知事件 baseline coverage | 事件前建 Watch 且所需 pre-event offsets 成功捕获 | `100%`，否则解释并重新评估运行可靠性 |
| 事实充分度 | hard requirements 通过 Pack 语义 Gate 的比例 | 按事件/类别报告，不设伪平均 |
| Provider 可靠性 | primary/fallback 尝试成功率、超时、限流、拒绝、stale | 每 route 独立报告 |
| 首达/完成延迟 | Event received -> 首条可信 Fact；Event received -> 报告终态 | p50/p90，失败不剔除 |
| 成本 | model/search/provider/subscription | `known/partial/unknown`，unknown 不得写 0 |
| owner usefulness | `useful/partial/not_useful/unlabeled` | 每个样本必须在结项前标注 |
| 人工复核时间 | owner 从打开报告到完成核验的分钟数 | 与现有人工流程同口径比较 |
| Forecast 指标 | 仅有效方向 Forecast 到期后计算 | 样本数不足时只报告，不外推 |

分钟级宏观 Provider 缺失造成的 hard gap必须进入 Provider 可靠性和 usefulness 统计，不能从分母删除。
`research_only` 是合法安全终态，但如果绝大多数事件都需要 owner 重新查完所有核心事实，产品价值门应失败。

## 6. 运行入口与日常流程

唯一产品启动入口：

```bash
./infra/dsh/run-product.sh
```

启动器必须完成当前镜像构建、Hub `/health/ready`、canonical Inbox 契约、Research MCP TCP/MCP
业务探针和带 build identity 的 DSH Host readiness，再输出带认证 token 的唯一 DSH URL。Research MCP
当前不单独提供 HTTP `/health/ready`；部署平台若强制要求该端点，必须另立运维兼容任务，不能把 404
误判为 MCP 工具不可用。

日常流程：

1. realtime worker 轮询已批准日历/feed，并在未来事件前建立 Watch；
2. sampler 保存到期窗口，事件正文到达后自动创建 durable Run；
3. DSH 在同一 Session 内有界 Search/Fetch/typed-provider 补证；
4. Hub Gate 产出报告或 `research_only`，Outbox 通知并安排 recheck；
5. owner 从 DSH Inbox 阅读报告/轨迹，在 Decision Desk 查看跨 Run 评测；
6. horizon 到期后 worker 追加 Outcome/Evaluation，owner 补 usefulness 与复核时间；
7. 每日只把稳定 IDs、指标和人工结论追加到执行日志，原始事实继续留在 Hub。

裸 DSH URL 返回 401 是预期认证行为；只能使用启动器输出的 token URL。旧端口和旧 Compose volume
不是当前观察实例。密钥只放 gitignored 环境文件，不写日志、截图、文档或命令历史。

## 7. BDD/TDD 与运行验收

```gherkin
Scenario: 未来可预知事件自动进入报告
  Given EventWatch 在事件前创建且 pre-event samples 已捕获
  When 官方正文到达
  Then 系统无需用户提问创建同一 lineage 的 DSH Run
  And 报告、通知、recheck 和后续 Evaluation 都能追溯到该 EventWatch

Scenario: 突发事件没有历史基线
  Given breaking event 发生前没有 Watch 或 ring-buffer sample
  When 系统生成研究报告
  Then baseline_status 是 unavailable
  And current snapshot 不得冒充事件前窗口

Scenario: Search 找到报道但 typed macro fact 缺失
  Given DSH Search 找到多个可信 locator 和正文
  But 没有合格的分钟级 macro/expectation FactEnvelope
  When semantic Gate 运行
  Then hard gap 保持未关闭并继续尝试尚未耗尽的能力
  And 能力耗尽后以可解释 research_only 终止

Scenario: 未经 owner 决策不能晋级
  Given 观察窗口或候选评测已完成
  But 没有 owner promote decision
  When agent、scheduler 或 worker 尝试切 active pointer
  Then 请求失败且 active generation 不变
```

PD-07 不新增普通单元测试来伪装时间观察。进入观察前必须保持 PD-00..06 全量质量门绿色；每次
影响 cohort 的 bug 修复后重跑对应 Red/Green、全量回归、前端 build，并在日志记录版本边界。

## 8. 决策与有限终点

观察结束只允许一次结论：

| 决策 | 条件 | 后续 |
|---|---|---|
| `promote` | 观察门、真实性门、运行可靠性和 owner usefulness 通过；无未解释的关键风险 | 进入个人正式使用/小范围外部试点；仍不自动交易 |
| `retain_baseline` | 工程可信，但 DSH/Provider 的事实充分度、成本或人工节省没有证明优势 | 保留 DSH/Hub/Pack/账本资产，Fixed 继续 active，不继续堆功能 |
| `stop` | 可信性、可维护性、成本或实际价值明确不可接受 | 冻结产品，保留历史资产和复盘，不以新功能延长项目 |

任何结果都必须写 ADR，包含样本 manifest、指标、失败模式、成本和 owner 反馈。PD-07 未结项前不进入
ASR、PPT、第二领域、多用户、公共插件市场、远程高可用或自动交易。

## 9. Checklist

### 入口准备

- [x] PD-00..06 工程与隔离运行门通过；
- [x] DSH/LangGraph/Hub/Pack 所有权固定且无第二套 loop/账本；
- [x] EventWatch、Inbox、Report/Trace、Outbox/recheck、Outcome/Evaluation 入口存在；
- [x] 观察指标、样本准入、cohort 版本和停止线写入本 Charter；
- [x] 追加式执行日志已建立，未伪造未来样本；
- [ ] 冻结首个真实 cohort manifest，并记录当前 commit/build/provider/pack/gate identity；
- [ ] 第一条合格未来事件在事件前创建 EventWatch，PD-07 时钟开始。

### 观察与结项

- [ ] 覆盖 central-bank speech、macro release、breaking event 三类；
- [ ] 达到至少 14 天或 20 个合格事件的审查门；`promote` 默认同时满足两项；
- [ ] PIT/future leakage 为 0；
- [ ] semantic substitution 为 0；
- [ ] event-to-report 成功率不低于 90%；
- [ ] 可预知事件 baseline coverage 为 100%；
- [ ] 每个 Run 成本为 known 或明确 partial/unknown，不存在假 0；
- [ ] 每个样本有 owner usefulness 和人工复核时间；
- [ ] 到期 Outcome/Evaluation 已追加，research-only 未伪造交易指标；
- [ ] 形成 `promote / retain_baseline / stop` ADR，且 owner 显式确认。

