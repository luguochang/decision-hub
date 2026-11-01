# 原 perpetual Graph 最终化：同次原件准备与短写事务

本片仅 D 隔离候选，未采用 Main、未运行新研究、未改变 actual12。冻结入口：`D:/DH-perpetual-finalization01/scope-final.json`，SHA `1d8313e0a78d063501f590f7e2117b04d8914a5fdbfe1bf5d3c5e77f0bb958e4`。非作者审查及物理 Main 验证由 Root 后续进行。

## 真实故障与限度

actual12 的原 native terminal 已在 11:40:02.739284 同步；20MB transport result、4.8MB mapped、graph_round:1 随后保存。`synthesis_started.created_at=11:41:27.533810Z`，距原 Task 截止 `11:41:48.074051Z` 仅 **20.540241 秒**。关闭副本中 graph_final/Artifact 均不存在，Run 仍 running；未执行报告 GET 或导出。11:41:14.305468 的 heartbeat_storage_busy 早于 synthesis_started，不能据此认定最终化已持锁。原失败和第三审 approved/frozen_information incomplete 保持原样。

物理 Main 原 `read_round(active=False)` 在关闭副本进行过一次只读测量：50.9024555 秒；`SQL_reads=406, SQL_mutations=0, HTTP=0, model=0, process=0, sys.setprofile=true`。调用计数包括一次完整 perpetual mapping、四次 prepared execution、18 次 attestation `_binding`。DB/WAL 前后同。此数值含 profile 开销，仅证明原读路径工作量，不是 actual12 未插桩耗时，也不证明全部末尾等待都发生在此处。

## 最小变化

只修改三条生产逻辑路径：`research_perpetual_graph.py`、`agentic_research_graph.py`，新增 `perpetual_finalization.py`。完整 diff 位于 `D:/DH-perpetual-finalization01/changes-final.diff`。

原 Graph route 对 `decision_v2` 与原 perpetual round schema 同时匹配的 locator 只选择 finalize，不授予权限。一个同步 owned callback 使用原 `prepare_graph_state_in_session` 完整核对真实原件和 checkpoint；原 `prepare_originals` 记录同一次只读快照的行、范围、缺失项及来源。回执、投影、locator 转为 callback 内部私有 JSON，不能跨请求作为鉴真或权限缓存。

原 CallbackSourceLease 在同一线程持有至 COMMIT/rollback 和 Session 退出。writer 在 BEGIN IMMEDIATE 后核 issued 身份、原 readset/source、fresh owner/lease/generation/deadline，复用原 snapshot/final receipt body；最后再核取消、来源、权限 fence 与截止。没有重映射、金融结果重算、全局缓存、时间回填、权限或预算扩张。原 snapshot/final body AST 除已绑定变量 `last_admission→frozen.reference` 外保持相同；原六个 perpetual 函数 AST 不变。原 legacy/V3 路由保留。

异步取消只通知本次 callback 的线程 checkpoint，仍等原 owned 工作、Session 和文件 HANDLE 真正结束再返回取消。没有释放尚在运行的 writer。

## 实际验证

- 原 Graph.finalize→原 freeze_final 的真实 SQLite 锁边界 RED：第二物理连接 heartbeat 被锁，1 FAILED/4.13 秒。不是 actual12 原 writer 的重运行。
- 首次候选验证是私有 loader 循环导入 ImportError，原件保留；只修 loader 为先初始化真实 Main 父包再加载真实候选文件。后续同入口 1 PASS/3.67 秒。
- 新最终 fence 跨截止、异步取消未通知 checkpoint 两例分别取得真实 RED，再最小修正。
- 最终定向 **22 PASS/9.77 秒、exit0**：20 个锁/issued/OCC/owner/lease/deadline/cancel/路由/locator 例，加两项真实 helper 字节来源与导入闭包检查。业务鉴真使用受控边界 fixture，SQLite/原 PreparedOriginals/原权限与读集机制是真实组件；不冒真实完整研究通过。
- 同 owner 续租/updated_at 变化沿原 PreparedOriginals 活动字段规则通过；owner 变化、到期、取消、generation/原始 payload/范围新增、source 改动均拒绝。调用方在 checkpoint 改 locator 不改变 writer 的已鉴真 locator。该例最初已因入口私有 canonical copy 通过，未伪造 RED。
- helper 的真实字节复制到专用 D 来源 fixture，原 `_ConsumerSourceGuard` 确实列入该路径，原 Windows HANDLE lease 真实拒绝修改，关闭 lease 后修改被原 source.matches 检出。这不等于 D overlay 已纳入当前 Main inventory。Main 采用后，新 helper 位于原 application/*.py inventory 内；原 import parser 的候选合并源码遍历也到达该 helper，不改 recipe_hash 或旧 receipt。
- 新 helper 与四个私有测试文件 Ruff 0。沿 Main 原 strict 模式及原 per-rule 配置：旧两源 71→候选三源 33 诊断，按 file/rule/message 多集合新增 0，新 helper 0。保留旧类型债，不称全局完整 strict/Ruff 已清零。新 helper 对内部 producer body 的跨模块私有调用使用一条明确的 reportPrivateUsage 注记。

真实文件路径、SHA、失败原件及终态均在 scope；`loaded-sources-final.json` 保存实际候选三个 `__file__` 及三个物理 Main 依赖来源。切到物理 Main 的同 22 例命令已写 scope，仅在 Root 采用后设置 `FINALIZATION_PHYSICAL_MAIN=1`，不伪造 `__file__`、不复制依赖树、不改断言。当前未执行该 Main 模式。

减少重复 replay 和 writer 持锁不保证下一轮赶上最终截止；原 60 分钟、600/120 秒等预算未增加。完整真实报告、PC/双导出和最终业务验收仍需后续新 Run 原证据，actual12 不升级为成功。

封存后 Root 非作者逐 SHA 复核通过：`D:/DH-perpetual-finalization01/Root-nonauthor-review.json`，SHA `b00aca66a7cfc32a69d0a05e07f06db837086d1a3a280e2cb741c6d70b661a15`。scope 与三个生产源保持不变；Main 采用和同 22 例物理 Main 验证仍未执行。
