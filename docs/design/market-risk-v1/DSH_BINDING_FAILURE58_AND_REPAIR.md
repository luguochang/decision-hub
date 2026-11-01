# 真实研究58失败与最小修复

## 1. 已证实的用户结果

| 任务 | 实際结果 | 内部闭合原因 |
|---|---|---|
| BTC关联复查 `run_dbce769c27973d121244b5ee4859535d` | 19:30:47→20:00:52UTC，11次工具、15条证据，第一轮没有可提交结果 | `research_method_binding_failed / cancellation_projection_wait_elapsed` |
| ETH现有研究入口 `run_5085d5c2879340e793cf25672c66c1ba` | 19:35:45→19:58:02UTC，11次工具、15条证据，第一轮没有可提交结果 | `research_method_binding_failed / cancellation_projection_busy` |

两条真实Run的独立只读快照均是发布Job0、Saved0、ACK0。没有完整交付，也未运行到新SourceSnapshot V2的实际报告发布验收。服务Ready、工具成功和API的legacy读取模式不能替代报告保存。

证据：`artifacts/core-goal-20261011/btc-linked58_14/result.json`、`eth-auto58_04/result.json`、`failed-binding58_01.json`、`failed-publication58_01.json`、`pc58_01/eth-research-failure-ready.jpg`。

## 2. 精确首因与尚不确定的部分

原始终止事件两条都为 `phase=execute`，异常消息精确匹配 `Original research Task changed or could not be verified`，对应 `web_runtime._assert_task_binding` 的任务/选择绑定读取。`cancellation_projection_busy` 表示未能进入两个真实线程槽位，或等待预算已耗尽；不是已确认SQLite锁异常，SQLite忙另有 `cancellation_projection_db_busy`。目前不能断言两个占用线程当时正在做什么。

当前调用把短暂pending直接作为研究终止。不能忽略pending继续执行Host，也不能重放不确定写事务。正确处理是：在原核验和绝对期限内，未提交的纯读取等待槽位；已提交的Future只观察原操作；确认核验成功且仍在两个期限内，才继续原Host操作。

`current_round=1` 表示尚无完整已验证研究轮次，不能据此判定SDK内部只跑一个模型步骤。600秒是模型步骤的原监控上限，30分钟是研究原总期限；有证据不保证能产出合格报告。第一轮没有可信结果不能伪造降级报告。

## 3. 有限只读计时与方案选择

按正式58 receipt和原纯schema初始化，在真实只读事务分别测历史准备与绑定核心。初始化8.8—9.1秒单列，不混入核验耗时。

| 操作 | 实际核验耗时 | SELECT | 结论 |
|---|---:|---:|---|
| ETH prepared | 1.651秒 | 63 | 原件读通过，不授新执行许可 |
| ETH完整 `_v2_original` | 1.853秒 | 93 | 历史核验核心小于15秒；不证明当时并发/当前选择许可全部通过 |
| 同入口加现成recipe scope，含uncached出口 | 2.677秒 | 118 | 该样本没有提速，本次不晋升该接线 |

首份prepared诊断未初始化正式recipe，得到 `research_graph_recipe_v3_not_installed`；保留该诊断失败，后按产品的原初始化补齐。它不是产品的新故障。

复用方案虽有静态重复来源读取的依据，本次实证不支持把它作为根因解决方案。Publisher的SourceSnapshot许可也不能直接移给DSH。现有线程槽位数、15秒核验上限、模型600秒和研究总期限均保持。

证据：`binding-prepared58_01/result.json`（诊断初始化失败）、`binding-prepared58_02/result.json`、`binding-runtime58_01/result.json`、`binding-runtime-scope58_01/result.json`。

## 4. 59最小实现与验收

1. `_assert_task_binding`的可信纯读取采用有界等待；slot尚未取得可再尝试，原pending Future只poll，所有权、取消、选择、原件和哈希继续完整验证。
2. 只对已结束操作中明确SQLITE_BUSY/LOCKED分类重新读取；其他数据库异常、invalid、closed和未知写状态保留原拒绝。Projector底层连接是读写连接，不能宣传成所有任意回调都物理只读。
3. 成功返回同时检查monotonic核验预算和原absolute deadline，防止事件循环延迟导致超期限放行。
4. 等待结束时留一条有限诊断：预算、实际耗时、入场拒绝/原Future观察计数及最终状态；不逐poll刷日志、不记录SQL、模型内容或私密参数。
5. 与已独审的读取性能及复查时间修复合并一次受管59发布。保留Root58真实停止闭合实现，不用漂移DEV旧源覆盖。
6. 验收仍需真实新current59研究→完整Saved/独立ACK→原字节页面/下载/刷新/重启，以及真实自然自动复查。失败Run58保留原记录，不能用它们的旧授权覆盖新报告。

RO等待纯控制和有限历史核验只是修复前证据，不能宣称故障已实际解决。新模型仅在最小候选审查、正常构建/资格回读/启动全部通过后提交，禁止盲目重复模型和扩大预算。

## 5. 当前状态

2026-10-10 20:16:40UTC原stop-product正常停机，supervisor stopped、进程状态已移除，数据和日志保留。目标active；59候选待最终封印和受管发布。原33项8/22/3及六门未全部通过保持。
