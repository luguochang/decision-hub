# 当前执行入口：PC核心交付

状态更新 **2026-10-11 21:27（北京时间）**。替代目标 active，旧目标的未完成项保留，手机不纳入。**核心尚未交付；原33项仍为8限定／22部分／3未满足，G1–G6未整体通过。** 72实际Ready通过；原ETH仍为Saved0／ACK0，73联合13源已独审，正常停止旧服务、提升13源完成，唯一标准构建运行中；新服务尚未Ready。PC条件查询超时的原F保留。12:00既有自然计划已触发，模型完成，报告整理仍失败；计划没有被强制触发。

## 当前结果

| 主线 | 已经完成的实证 | 仍需完成 |
|---|---|---|
| 启动与DSH | 72正常构建、启动；12:11 UTC四接口200、七个Worker fresh，DSH Host ready/provider研究已真实执行 | 该次Ready不代表持续稳定或报告交付 |
| BTC手动研究 | 完整报告Saved+独立ACK；71 PC读取、完整JSON290220字节、中文阅读版72417字节、刷新同字节 | 宏观／跨资产缺口、量化触发与内容有效性 |
| 原ETH研究 | 原native gen1 completed；72独立API恢复一次、同请求回执回读一致；PC能读取最新取消状态 | PC条件查询超过30秒默认等待；整理仍超时取消，Saved0／ACK0，完整报告与下载未通过 |
| 自然复查 G4 | BTC首个30分钟复查Saved+ACK、PC原件回读；12UTC原24h计划自然产生child且真实模型按原D完成 | 30m窗口确认前已到期约6秒；新24h child整理unconfirmed/gen1/v5、Saved0ACK0，diagnostic unavailable；实际资源UNKNOWN，完整自动链仍待 |
| 监控事件 G1 | 一条ETH分钟行情与OKX原响应闭合；旧告警→trigger→request实际可追溯 | 该旧request准入预算耗尽，尚无event／Run／报告；不能拿人工研究充数 |
| 性能与PC G5 | timeline100独立分门P，BTC旧报告71回归P | summary100两资产p95仍超过原0.5秒门；剩余矩阵与内容质量 |

## 当前阻断：ETH报告整理

原任务 `run_a748b561e04166b41b3534b681137d9d` 保留，没有重新调用模型。

1. 72 PC核对旧 `7ca4` 已接受回执后，点击一次恢复条件查询；后台约44.4秒返回200，页面30秒默认等待先结束。一次独立诊断GET对同Run实际耗时33.283秒。PC没有提交新的恢复，PC条件分支仍为F。
2. 为取得新计时证据，独立公开API按当前gen8/v20仅提交一次 `report-recovery:85791f1f-afeb-4fe3-9da8-eb09092f9590`，12:30:53 UTC accepted/gen9/v21；GET原请求回执与POST逐字段相同。该API实测不替代PC验收，没有新模型或新研究。
3. 实际gen10：来源解析43.375秒、读取52.797秒并超时失败；来源作用域入口16.593秒、出口9.031秒。未进入材料收集、回放或保存。执行总计129.046秒，native线程12:33:27 UTC退出，finally/future完成已证。阶段包含关系不能盲目相加。
4. gen10退出时取消回写未确认，曾读到queued；后续gen11因续租写入忙停止，gen12读取成功后在回放超时，取消写入成功。12:44:43 UTC实际终态gen13/v32 cancelled／attempts7，Saved0／ACK0。正常队列允许再claim；逐次租期触发来源尚未唯一闭合，不能仅根据恢复回执的 `automatic_retry=false` 宣称没有后续尝试。
5. 资源解析同调用已认证字节复用、历史读取同Session/Run作用域复用均已独审；保留全部出口重新核验。告警请求展示与行情摘要V2候选合并了重叠的query改动，PC等待合同沿用已有45秒。联合候选13源／2新增／0生产测试已提升到ROOT、标准构建运行中，尚未启动；完整报告保存与性能收益仍待实测。

下一修复依据上述实际耗时：减少同一次读取中的重复来源核验，修复PC等待合同，闭合取消回写。合并独审通过的最小改造后正常发布，再按实际恢复条件验同Run的Saved+ACK、下载／刷新／重启。保留原计算预算与来源权限。

新增真实失败：24h自然child `run_da34ab255fd24741a6082f1795a78246` 于12:11:37 UTC创建，native12:32:21完成，早于原12:41:39截止；业务degraded于12:36:40完成，Job unconfirmed/gen1/v5，原因 `frozen_io_undeclared_authorization_resource`，Saved0／ACK0。13:17:16 UTC一次精确Job/最后Event只读核对身份与canonical hash匹配，diagnostic为unavailable／sha null，无声明原件；实际资源、producer frames仍UNKNOWN，不能套原ETH超时原因。证据：DEV `btc-natural-auth73_01/result01.json`，SHA `eaffbb2cedf03fa618b069501997e0b70809744f2d201f43bfcc107dec220f8b`。

故障整改条目：`research_publication_frozen_io48.py` 的authorization拒绝分支当前未记录io.denied；仅拟与runtime退出IO后的闭集安全日志配合记录首个缺项的已声明logical ref或未知路径hash。两文件观测方案**未实现、未安装**，不扩大allowlist或读取权限，不代表实际数据端口已修复；G4仍未完整通过。

[PC操作矩阵](../../../artifacts/core-goal-20261011/pc72_01/action-matrix72_01.json) · [实际子阶段](../../../artifacts/core-goal-20261011/publication72_subphase_observation02.json) · [独立API恢复回执](../../../artifacts/core-goal-20261011/recovery72_API_submit01.json) · [最新ETH终态](../../../artifacts/core-goal-20261011/publication72_terminal_state03.json)

![72 PC实测：报告整理仍因超时取消](E:/project/study/codex/dsh/decision-hub/artifacts/core-goal-20261011/pc72_01/eth67-current-cancelled12.png)

## 已核对的监控与来源边界

- PC中的ETH 10:54 UTC收盘2500.41，与同库candle／raw_capture及31621字节OKX history-candles HTTP200、confirm1原件闭合。仅这一分钟来源链P，不代表完整行情覆盖或G1整门。证据：DEV `eth-minute-origin71_01/result01.json`，SHA `5e9a6fac13a1bf41ebddae912327f42e217dd1754538485bda18f2c8b8512220`。
- PC71打开10月8日ETH旧告警详情：-2.01%／门槛2%，解除与知悉回执可读。实际同事务RO确认 `alert → risk_alert:{alert_id} → request`，request为failed／attempts3／`research_admission_budget_exhausted`；event_id与run_id均null，精确Run幂等键0行。没有关联报告可验。证据：DEV `g1-known-alert71_01/result01.json`，SHA `e6c1f6dfe7f7e8e023a85d8dc67f571184dbe81b14e3bd64175e06c59f304eba`。
- 当前监控条件未触发，未改规则或强制触发。旧告警详情没有研究失败／关联入口，首页最新人工ETH过程记录也容易被误解成完整报告；这两处交互缺口保留，不能用“已启用”代替成功自动研究。
- 静查已分清两个入口：自动卡片从本资产最新request读取其关联Run，request可以是人工触发；没有拿首页最新报告冒充这条旧告警。旧告警详情精确join原trigger／request，只返回report_run_id；无Run便隐藏入口，未展示准入失败原因。整改合同：卡片明确“最新资产请求”，告警详情增加该request的身份、状态、原因、次数及关联Run；调度开关、人工报告与告警链分别说明，不改准入或制造新事件。

## 下一步四项与结束条件

| 待办 | 实际结束条件 |
|---|---|
| ETH完整交付 | 定位并修复真实整理热点；同一Run Saved+独立ACK，PC正文／JSON／阅读版／刷新／重启一致 |
| 自动链与自然复查 | G1核告警→请求→event→automatic Run→报告；G4另外核父计划→自然child→报告及内容，不相互替代 |
| 原性能门 | summary的bar_load_verify实际主耗时须有算法修复；保持原首读、样本与阈值，不预热或重复采样抹失败 |
| 内容与剩余PC | 核时点、来源、触发阈值、反方、缺口及核心按钮；保存成功不等于预测有效 |

宏观数据资格、黄金准入、经验消费和预测增益仍是未完成路线，保留原协议。旧性能F、原失败、未知提交围栏及历史回执全部保留。

## 证据和文档

72 build SHA `ebd0d1797beaab885fb38dfcf67b1c72b7209a027654fd4593ff82864e461bad`；72监督session4435对应的旧服务已正常停止；73唯一构建session49698运行中，由发布操作agent接续。Ready72_02 SHA `3f78b9c3da6c0d9a7c3a98cb9f1e7b74bdcdfe3a70cb1c30ab338e55689ed123`。71 phase审计与安全诊断原件在DEV `publication-phase71_01/`，历史结果保留。

[机器状态](current-core-execution.json) · [六门与目标范围](CORE_DELIVERY_GOAL_2026-10-11.md) · [原33项对照](CURRENT_REQUIREMENTS_AUDIT_2026-10-11.md) · [PC用户手册](PC_USER_GUIDE.md) · [实测仍失败时的条件整改](CONDITIONAL_READ_QUALIFICATION_REMEDIATION_AFTER73.md) · [历史执行记录](EXECUTION_HISTORY_THROUGH_71_2026-10-11.md)
