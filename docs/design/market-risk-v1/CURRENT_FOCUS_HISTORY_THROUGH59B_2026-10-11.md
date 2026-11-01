# 当前执行入口：PC核心交付

旧目标由用户结束，保留未完成记录；新目标active。原33项仍8项有限工程通过、22项部分/待验、3项资格未满足。手机不纳入。

## 当前动作：60标准发布中（2026-10-10T21:43:32.275948+00:00）

已正常受管停机；精确晋升9个生产文件：7个交接/展示修复及2个真实发布入出观测。43项不同聚焦纯控制通过（Runtime7、completed consumer10、展示18、发布观测8），独审通过；原65源和3配置未变。一次标准60构建正在执行，尚未Ready、尚无新研究或交付结果。59b的两条失败记录保持原终态。

## 实际状态：59b启动通过，两个新真实研究失败（2026-10-10 21:24UTC）

59b标准UI/Host、精确来源核对、原资格只读回读、正常受管Ready通过；服务当前仍运行。两任务均真实使用DSH、11次取证调用/15条材料，最终没有发布Job、Saved或独立ACK。**核心未交付，不能以Ready或模型完成代替。**

| 任务 | 实际结局 | 精确原因与阶段 |
|---|---|---|
| BTC手动合法恢复 `run_3091c7d79ab27ea433ade65861f51d3e` | failed21:19:40UTC，Host记录cancelled；原deadline21:21:45UTC | execute / `research_status_storage_pending`；精确异常“original Host status could not be durably confirmed”。本地BEGIN忙导致30秒确认片段耗尽后进入通用失败/收尾路径；占锁者和取消归因尚未证实 |
| ETH原计划自然到期子任务 `run_14d1d0eddb86406f93a42f50e29bd88e` | DSH completed21:16:57UTC，产品round2综合后review失败21:20:10UTC；原deadline21:18:32UTC | review / `research_worker_failed`；精确PermissionError `original_preparation_deadline_elapsed`，来自ExecutionBoundary当前时刻deadline门。已确定review与commit共同使用执行期限门，正在实现仅消费已认证completed原结果的边界 |

自然ETH计划身份及到期已通过物理只读核对，完整报告复查G4未过。两任务都已终态，修复经控制和独立审查后才发起下一轮合法研究；不回填或重开这两条失败记录。

## 紧接着做的核心修复

1. BTC：在Runtime内部保留原Host状态回执与submit，处理只在初始BEGIN可证未写入的确认pending；不把本地确认待办直接当业务失败，保持原deadline、owner/generation、未知提交围栏；不得重submit或扩预算。根因已定位，单Runtime修复正在DEV实现，随后做必要控制和独审。
2. ETH：精确核对review消费者对原completed结果的身份、期限、当前取消及lease要求；纯消费不能获得新模型/工具权限。修复真正的consumer边界，不能删除deadline检查或伪造新clock。
3. 错误展示：已核实RunView.run.failure有准确闭合原因，native_execution_failure=null符合“原生会话失败”语义。只修结束原因泛化和中文展示，复用可信failure，不把Worker收尾失败伪造成DSH失败。BTC为dsh_status_initial_writer_busy，ETH为original_preparation_deadline_elapsed。
4. 修复后一次标准发布和真实手动/自动/自然复查结果；Saved/独立ACK、摘要/正文/下载/刷新/正常重启及PC矩阵按六门验收。失败原件和原选择保留。

## 并行保留的未完成项

- 密集BTC时间线19标记p95 **0.853秒F**（门0.5），ETH15标记0.063秒、最近8项1.660秒P。一次局部RO0.143秒不复现HTTP峰值，根因仍UNKNOWN；先解决真实报告交接，不重复盲测或调门。
- publisher真实入口/finally观测的2文件候选静态独审P、8聚焦纯控制P，**尚未安装**；日志64观察+最多2区间行，不提供落盘/性能重叠授权。
- 旧ETH308477B在59b正常重启后同字节、快读/下载0.099/0.087秒P，属于旧报告兼容回归。
- 旧报告有SWAP/现货推断、OI错位、coverage与限制矛盾等10问题；[内容审查及3个最小修复点](REPORT_CONTENT_AUDIT59B_2026-10-11.md)。新报告逐份实证审查后才能授内容通过；不能靠关键词/空泛风险文字过门。
- 原宏观、黄金、经验消费和预测统计增益仍按独立协议待完成，不由报告产出或工程通过替代。

## 证据与接续

当前build `artifacts/mr-release-b2b4-59b/product-validated-builds.json` SHA `13f595e495ab107da4a3b286de8b6a5facfd82a0d3ec1c850a58edc6a9e7e55d`。以下位于`artifacts/core-goal-20261011/`：

- Ready `ready59b_04.json`、来源 `source-projection59b_01.json`、资格 `postbuild59b_01/`；此前warming和首次构建失败原记录保留。
- 实际终态 `btc-progress59b_04/result.json`、`eth-progress59b_06/result.json`；同快照物理RO `publication59b_02.json`：Job/Saved/ACK0。
- 精确异常 `failure-events59b_01/`，只读变更0；自然身份 `natural-review59b_01.json`；原SDK期限见`publication59b_01.json`。
- PC取证原文/返回4控制 `pc59b_01/evidence-controls01.json`，错误详情 `pc59b_01/eth-failed-detail.jpg`；旧报告回归 `snapshot-restart59b_01/result.json`。
- 性能 `performance59b_dense01/result.json`，局部分段 `profile59b_dense01/profile.json`；长writer及原件校验 `provider-phases59b_01.json`，不能据此断言占锁者。

[六门目标](CORE_DELIVERY_GOAL_2026-10-11.md) · [机器状态](current-core-execution.json) · [原33项](CURRENT_REQUIREMENTS_AUDIT_2026-10-11.md) · [用户手册](PC_USER_GUIDE.md) · [历史](CURRENT_FOCUS_HISTORY_THROUGH58_2026-10-11.md)
