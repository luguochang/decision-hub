# CORE59：一次真实研究批次的最小验收顺序

2026-10-11，DEV验收安排，**不是通过回执**。Root59已安装、59b正在标准构建；先以实际Ready/安装身份为前置。沿[六门定义](CORE_DELIVERY_GOAL_2026-10-11.md)，复用具名历史控制，保留58失败，不重复提交或制造数据。执行与读核均由Root操作。

## 一条顺序，复用同Run原件

| 顺序 / 门 | 最少PC动作 | 同批独立读核 / 通过依据 |
|---|---|---|
| 1 / G1、G6 | 首页分别选择BTC/ETH，核资产、spot/USDT、分钟截止和真实缺口。BTC正常手动入口只提交一次；ETH使用既有自动研究意图/监控路径，不重复点击。打开各自执行记录。 | 绑定59b build/launch、请求回执、Run/Study/Task/原origin/parent与DSH session；行情原bar/raw、scope、closed/freshness与UI一致。202、Ready或出现工具次数不等报告成功。用户保存自动意图与真正自动事件触发分别标注；缺一种证据不得混计。 |
| 2 / G2、G5 | 从最近研究打开两份原报告，读结论、风险窗口、数值单位/依据、最强反方和缺口，再展开执行记录；返回首页、BTC↔ETH切换，旧值不能串资产。 | 同Run原Host result→mapped→GraphFinal→Review→publication source→Result/Artifact/Revision相互绑定；首页摘要、正文、引用和报告hash一致。保留degraded/research_only、概率/执行支持未知和真实费用未知，不以身份核验授预测或交易资格。 |
| 3 / G3、G5 | 展开“本次读取核验”，点击完整原报告下载；刷新同页、返回再打开，原Run与状态不变。用已留旧BTC失败页核“重试读取/核对恢复条件”及无完整下载；原本机身份到期/恢复控制复用具名已验结果。 | **每个新Run**只读`research_publication_bundle_jobs/bundles/confirmations/state_events`、发布source和bundle文件：同request/generation/bundle_hash的Saved及独立ACK存在，head仍confirmed。下载原字节SHA/大小/ETag=保存report_hash；快照header的模式/版本与包络一致，读取未制造Saved/ACK。 |
| 4 / G4、G2、G3 | 打开该报告的复查计划，选**最早仍合法的真实计划**；让General正常循环自然到期，不强制tick。随后从计划打开child和parent，再核child完整报告、下载及确认。 | 原`review_schedules.trigger_at/trigger_hash/parent_run_id/child_run_id/status`与原Run `admission_origin=scheduled_recheck`、准入事件/原basis一致；自然时钟到期后才准入，正常预算内DSH终态及child Saved/ACK完成。计划completed只表示child执行状态映射，不代替完整报告确认。失败/跳过/延后保留原原因及关联；需合法恢复才能关闭失败分支。 |
| 5 / G5、G6 | 沿上面一次研究/发布期间的首页和报告操作取响应时间；确认后安排一次正常受管重启，再打开原BTC/ETH/child并下载。 | DSH执行与**真实publisher native执行**各自和读取采样有实际重叠，保存原phase/claim/generation/UTC日志及采样起止；queued/prepared或仅首末状态不是线程活动证明。BTC/ETH时间线沿原p95≤0.5s/原样本协议；最近8项和报告快读/下载≤2s，慢/失败全部保留。重启后同hash、出处、ACK/状态版本合法，未知提交不自动重试、无旧成功日志无限重放；lease、计算期限和native退出分别核对。 |

**尽量并入本批采样**：步骤2–3在publisher实际准备/保存/独立确认期间取得性能证据；若没有真实重叠，就记录该门未验，不能在发布结束后空闲快读补成“publisher并发通过”。原58 BTC p95 0.6470175s、最近8项2.1414478s失败保留；原[性能文件](../../../artifacts/core-goal-20261011/performance58_dsh01/result.json)明确`report_producer_active=null/full_interval_overlap_qualified=false`。

## 两个必须分清的边界

- **自然复查不能任意改成2分钟。** 现`GET /v1/research/runs/{run_id}/review-plans`只有读取，PC计划面板只有刷新/导航，无公开trigger_at创建/更新时间字段。正常计划来自已保存`horizon.next_review_at`，没有固定2分钟间隔；tracking可能保留合法较早future slot，迟到激活可冻结实际now。研究问题可以提出近期复查建议，但不保证模型或准入接受。若本批没有近期合法计划，G4保持等待/未验；不SQL改钟/改行，不拿manual child或daily root冒自然复查。
- **快读不是完整审计。** 默认`snapshot`及`snapshot/download`是`confirmed_report_snapshot`，`material_audit=not_checked_this_read`；独立一次PC“完整原库核对”走`POST .../report/audit`，核原quality与Saved包一致、头未变及`full_original_audit`回执，时间单列。它不会生成新确认，也不能将旧30.541s完整审计性能失败改写为快读通过。未确认、撤销或篡改保持拒绝。

## 已确认代码与原件入口

[报告API与两种读模式](../../../apps/hub_api/research_quality_routes.py) · [PC回执/原字节校验](../../../apps/decision-desk/src/api/researchQuality.ts) · [保存/独立确认表](../../../packages/kernel/decision_hub_kernel/persistence/research_publication_bundle_records.py) · [自然计划及writer准入](../../../packages/kernel/decision_hub_kernel/application/review_schedule.py) · [General发布循环](../../../apps/hub_worker/main.py) · [publisher原生阶段日志](../../../packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py) · [历史快读/下载](../../../artifacts/core-goal-20261011/snapshot-live56_01/result.json) · [历史重启同字节](../../../artifacts/core-goal-20261011/snapshot-restart57_01/result.json)。时间线为`GET /v1/risk/scopes/{scope_id}/timeline`，最近8项为`GET /v1/research/recent-runs?limit=8`；报告路由前缀`/v1/research/runs/{run_id}`。

最终按G1–G6逐项记P/F/未验；两资产一次成功仍不能代替自然复查、真实并发、失败恢复及重启门。开发PC自测不冒用户理解群体验证。手机、宏观/黄金、28日统计效果及经验增益保持原独立边界，本页没有新增可选门。
