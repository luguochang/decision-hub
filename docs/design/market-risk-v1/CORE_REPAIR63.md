# 核心修复 63：受理回执确认与行情采集

范围：PC BTC/ETH主线。新目标active，原33项和G1–G6判断不变。64六源/0tests标准build、精确投影、原读回及正常Start/Ready64_01当次P；63 ETH重启后同报告307831字节/hash与原反馈保持，首页保守材料标签实读P。新BTC合法一次child已确认，当前检查证据缺口/0工具0证据；02:26:41快照Run/DSH running、Job/SavedACK0，尚无报告。四源peer与Root审两提示源分开，提示产出未验；旧63 BTC失败、ETH内容/金融缺口、原性能F保持。

## 两个实际问题

| 问题 | 已确认原因 | 尚未确认 |
|---|---|---|
| 62b新BTC受理后立刻失败 | accepted之后，本地两个projection槽忙，回执writer尚未入队；编排以 `research_observation_unavailable / cancellation_projection_busy` 终止。原期限未到，取证0、Job/Saved/ACK0 | 哪两个操作占槽。不能归因为SQLite、SDK、provider或30分钟到期 |
| 61正式性能样本ETH过期 | 当时ETH采集claim未结束、后续分钟无新claim，50样本均超原180秒时效。已知另有初始采集写锁BUSY | 该claim早期失败的确切共享原件写入阶段；不宣称修掉所有采集异常 |

原证据：DEV `observation-failure62b_01/summary01.json` SHA `0da9e74b…cc55e`；`market-freshness61_01/`。原失败和旧ETH降级完整字节保留。

## 实施合同

### 1. 同一次回执确认

单文件 `packages/runtime_adapters/dsh_runtime/web_runtime.py`，收敛为 `_record_transport` 与 `_retry_host_call` 两函数窄接线。

- 原typed reply、context、recorded_at在等待前冻结。
- 正常RPC的accepted/status/result：由调用显式传入实际期限；槽忙且未分配/排队操作、无Future时，按25ms异步间隔等待本地准入；截止点为原调用/binding/proof最早绝对期限。确认ready之后仍复核截止点，晚确认只保真实审计，不推进Latest。原研究deadline不延长。
- writer已接纳后，只poll原 `ProjectionPending`；不会第二次write或Host submit/status RPC。原记录仍须实际提交并核验后才被消费。
- 每次writer等待保持原≤5秒，线程槽仍为2；业务拒绝、已完成但未知结果、closed/unsupported不重放。
- 原Task取消、owner权限、租约和清理保持；显式取消审计0.75秒尾段、error和直接调用的原单次路径不变；原到期后的3秒短恢复路径不套用新增等待门。审计记录不授予模型、报告发布或交易权限。

初版 `observation-confirmation63_01/source-seal01.json` SHA `891a8059…0441d`，源码SHA `a676ca0b…24297`。该测时源码一次8项有限纯控制P，4.581秒；原版本同accepted槽忙1项F保留（5.446秒）。独审发现late-ready及旧短恢复边界，初版不晋升。最终02源码SHA `40780199…86995`、seal `8619916e…fddcc`、独审 `bc6b3299…5c69f` 通过。最终源3项必要控制一次P（4.929秒）及normal未知Future到期/重入1项P（4.869秒），覆盖正常result、晚确认拒绝和旧恢复兼容；不把初版8P算作最终源码重测。不是实际线程槽竞争或真实DSH验收。

### 2. 采集的初始锁准入

两个文件 `market_risk_data.py`、`scheduler.py`。沿原risk_acquisition 90秒绝对截止点继续已知初始BEGIN未写入的有限等待；不增加90秒、原lease或180秒行情时效。

- 同物理连接/native事务/Session/UOW/total_changes无变化，且真实SQLite初始BEGIN/BUSY，才允许最多16个配置100ms短段；BUSY退出事务后才异步让出。
- 成功BEGIN后，原业务/HTTP/INSERT/COMMIT顺序保持，不重放；不同scope的已知准入等待可隔离，但最终仍保留真实失败，不制造全部成功。
- 未知finish/COMMIT直接退出，原异常和此前primary保留；genuineBUSY投影已修正cause自引用。
- 100ms配置不代表操作系统硬取消保证。原2个fixture失败和所有修订保留。

最终 `market-admission63_03/source-seal03.json` SHA `a4064b1d…aa9da`，独审P；market源 `527feeda…bb90e`、scheduler `4b9f0d4e…e6239`。有限9项分三段P，后续异常缝6项及cause环1项各一次P，未重复原9/6。已实际安装；尚无该版实盘采集验收，不宣称所有采集故障已消除。

## 发布及真实验收记录（63历史实测）

| 步骤 | 当前状态 | 必须记录 |
|---|---|---|
| 1. 主因及候选 | 主因确证，采集03及回执02独审P，三源已安装 | 三个Root前像、最终源码及控制/独审；原65源/3配置保持 |
| 2. 原62b捕获与正常停止 | 实际P，已正常Stop一次 | 精确BTC62b capture、275源9536091字节，query-only关闭0变化；官方producer4705 exit0；preparation `0b52485c…22e0a`，singleton `0827561d…eb5b0`。prior62b继承旧registry，不累计历史capture。01:00:40 UTC正常Stop exit0，owned tree停止、原PID/roster不存在、目标listeners0；后置条件 `pre-stop63_01/result.json` P |
| 3. 三源安装与标准发布 | build/投影/两份读回/Start及当次Ready实际P | 三源promotion `87c113aa…6fbaa`、0tests、原65源/3配置保持。唯一标准build session `22850` exit0，receipt `1e92256b…4d502`；精确投影 `7ac7cb16…45ba1` P（3源+278 capture，无unexpected）。serving只读P 2.704秒、outcome只读P 0.157秒，均0changes。原Start56已由session `89445` 启动，当时前台循环运行，64正常Stop后已退出；不能以等待exit作为就绪门。早期Ready01旧stopped、02 starting/1 Worker、03 running/5 Worker均false保留。`ready63_04.json`于01:11:46 UTC P，四个GET200、七个fresh Worker/no errors，launch `20261011T010827097Z`；仅当次快照，不宣称连续Ready。工程P不计核心交付 |
| 4. BTC/ETH真实综合研究 | BTC失败；ETH completed/业务degraded、完整确认及原字节局部P | BTC native14 tools、research_status_storage_pending，无Job/SavedACK，HTTP snapshot404；ETH completed11 tools/error null，confirmed03于01:54:12UTC核Job confirmed/gen2、Saved1/ACK1身份一致；HTTP与浏览器307831字节/SHA0c52f2c8…fd2265匹配。原gen1 queued及404历史保留，不是模型重新执行 |
| 5. 内容及PC | 有界综合内容、浏览器下载与刷新局部P；整体内容/PC未过 | audit06有5环主因果/3反方/3窗口/1有界审查，14有效证据/8来源；仍有公开叙事、概率/预算未知、窗口锚点、阈值及事实/推论混写缺口，无量化风险资格。反馈09:53:37刷新后正文/当时评估保留；证据14查找无关联HTTP原响应/发布时间，重启未验；当前只下载原字节JSON，中文报告导出缺失、重复边界/小字拥挤，G5 UX未P。data failed106/completed332及0分区覆盖缺口、日历BLS403/SOURCEcatcherror保留；63当次UI FINAL02未安装，64已晋升但尚未Ready |
| 6. 自动闭环和性能 | 最早02:30UTC自然复查待验；性能观察自然结束且0正式批次 | observer01:43:15UTC结束/formal0/overlap UNKNOWN，不restart；ETH gen1 entry/PID/job已匹配，收集后余44.682秒不足原170秒采样前置，合法0批次；原61性能F保持，不以HTTP200/辅助profile授P。只在真正自然触发及真实publisher重叠中核验 |

当前阶段更新：2026-10-11T02:30:30.931957+00:00。发布helper独审P `c3a3688d…03071`，并行三源/构建依赖独审P `d66e8a78…0a636`；仅静态范围通过，不能代替实际构建、Ready或报告验收。

## 64 当前六步

| 步骤 | 当前事实 | 下一实际条件 |
|---|---|---|
| 1. 正常停止 | 02:11:57 UTC实际P，原tree/3listeners0/无activeRuns | 不重停；Start63已退出 |
| 2. 六源安装 | promotion exit0，receipt `ad170dac…bf875`/manifest `a6672940…31a00`，0tests、原65/3保持 | commit1+UI3 peerP；profile/frozen-review提示2源仅Root实际diff接受，不外延独审 |
| 3. 唯一标准build | session71216实际exit0，02:18:04UTC UI1/Host1 | receipt `84f9514b…78f17`；retention7/264/52650B P，工程P不授六门 |
| 4. 投影/读回/启动 | projection `19126fa1…abe7` P6production+278capture/815rawchanges/unexpected0；RO2.563/.157秒P，Start27168前台正常运行 | Ready64_01 02:22:28当次四GET200/7fresh/noerror P，launch20261011T021944082Z/PID15188；仅快照，不连续 |
| 5. 新BTC/内容 | 一次原failed父重试确认child `run_7f72aca1a449cba9dcbd7a8c0a05e2c1`，10:25:26CST检查证据缺口/0工具0证据；02:26:41UTC Run/DSH running/gen1，accepted02:25:51.228、原deadline02:55:26.320306/max100/modelstep600000ms，Job/SavedACK0 | 真实报告/Job/Saved/ACK待；新提示锚点、阈值、事实/推论产出未验，prompt不当validation |
| 6. ETH重启/自然复查/PC性能 | 02:23:24.818UTC同ETH63 Run307831字节/hash与重启前一致、原反馈保留；首页材料标签、创建/最近更新及BTC模板只读入口范围P，未另提交模板 | 自然复查最早02:30UTC未验，observer尚未启动；保27分钟/105GET阈值，首页旧缓存无active→响应后BTC的过渡待优化，中文导出/完整UX与金融资格未过 |

提示候选 `synthesis-guidance64_01/candidate.json` SHA `48f59435…0f31`、diff `de43f53e…d8d6`。原63持锁者未确证；源码改善不代表根因消失或交易/预测资格通过。

实际结果继续更新本表及 `current-core-execution.json`；历史原失败和G1–G6未过保持。
