# 已过期早期研究 checkpoint 的恢复顺序

当前已精确 D-only 采用；Main、4511 构建、live08、原 05/备份和既有失败原件均未修改。
不计入 30 例业务回放验收，不证明原 05 进程消失首因。精确生产/测试范围仅以下五路径：

- `packages/kernel/decision_hub_kernel/application/research_graph_binding.py`
- `tests/runtime/test_research_resume_deadline.py`
- `tests/runtime/test_research_finished_recovery.py`
- `tests/runtime/test_research_admitted_round_recovery.py`
- 本报告。

原恢复链在 saved Task 与 checkpoint 相等后，先核当前 recipe manifest，稍后才到原 context
selection deadline fence，导致已过期的 pre-admission checkpoint 先报当前实现不可用。
修订仅对没有 round/final locator 的早期 checkpoint，在原 writer permission 后鉴真原 Task、
比较 checkpoint，再以 `utcnow() >= original.deadline_at` 拒绝，沿用
`PermissionError("context_selection_deadline_elapsed")`，随后才检查当前 recipe。
已有 round/final locator 的分支保持原 recipe、Task、原件 reader、缓存比较及 lease 检查顺序。
locator 不是授权：伪 ref、reader 拒绝或缓存篡改仍失败。不新增 UPDATE、续租、超时、重试、
execution grant 或旧图执行；后续原 deadline fence 全部保留。

不能按 runtime deadline 一律丢弃已完成成果。原 executor 支持 finished checkpoint 无 pending
node 的恢复；既存 `test_graph_observed_result_v2.py:507–548` 要求读回同一原投影且无新 RPC。
已 admitted 永续 round 可以仅待确定性 finalize 存储。原 Commit 的 candidate 有效期与研究
runtime 分开（3600 秒 runtime、1800 秒 operation margin），原 late-persistence 路径保留。
`terminal_storage_pending` 是失败状态更新尚未落库，不能概括为所有 final 过期作废。

两个真实基线分别保留，不混 SHA：

- Main 原件 SHA256 `2844facd75ddc73b05bac680b6fd65af1d7b190b731d6f307ce00e0bd00e94ca`；
  已审 Main 候选 `99996243ce7e3f5ba8effde963bc9b229c08b5f35380d223f2ebcc929a4bb565`。
- D 原件 SHA256 `8352ed0c1495de4bf14a77080ee5bf80d87bb17d7d0d9838b1ef1bf91132f591`。
  `_original_inventory` 的 17 行差来自已提交
  `3621347ece5a5f32edf2479787dd5d90bcc0a8f3`：保留遍历/后缀/排序语义，先筛后缀并延后拼接。
  D 候选 SHA256 `ae8621f2488e59d145f04b23bdc396f173dbf07a220d51ec9a1042da35c99618`。
  只叠已审 deadline 片段，D 库存函数文本/AST 原样；prepare 函数与已审 Main 候选 AST 相同；
  除库存函数外，D 候选与 Main 候选逐字相同。两个 before 快照及确切 diff 保留在
  `temp/replay-deadline-fence/`，未撤销 D 既有优化。

实际定向证据：原 Main 早期分支 RED 为 7 FAIL/8 PASS；最初无条件候选虽 15 PASS，却被
finished 回归实证 7 FAIL/2 PASS；仅 exempt final 的第二候选又被 admitted-round 回归实证
5 FAIL。原 Main 对应 finished 9 PASS、round 5 PASS。中间 reader seam 漏 `reference` 的
fixture 错误单独保留，不冒产品 RED。最终 Main 窄候选 29 PASS，Root 独立 29 PASS 4.68s、
39 pins 逐字匹配、C0/I0/M0；旧两候选不得采用。

三份正式映射测试默认按自身仓库路径加载真实 D 生产源，不依赖 temp 文件。仅候选验证可以
显式设置测试用 `RESEARCH_GRAPH_BINDING_SOURCE` 指向物理候选；不伪造 `__file__`。
真实 D8352 默认来源得到 7 FAIL/22 PASS，6.80s/exit1；显式新 D 候选得到 29 PASS，
6.30s/exit0。库存既存完整专项 10 PASS，0.22s/exit0：原测试/oracle/断言不改，只在私有
runner 中将其 target 函数绑定到新 D 候选真实物理函数。新映射三测试 Ruff0/strict0；
原 D 源与新 D 候选 Main 配置 strict 均20条、消息相同，既存诊断不在本切片修复。

这些测试是纯内存入口顺序验证，显式替代 native dispatch、原件 reader 与当前 recipe 边界；
原 permission、Pydantic request/ref 及投影比较真实执行。验证依赖物理 Main 当前 Python
模块（D 并非完整 Main 克隆），生产待测函数来自真实 D 或显式物理候选。未运行 Worker、
checkpoint store、图、模型、HTTP、服务、新 Run 或业务回放；不是完整 SQL 鉴真证明。

Root 单独授权的只读观察仅打开既有失败回放私有副本 checkpoint 库：原 saver 默认 namespace
的最新 `run_19001efca3fd49d5a62883d9770ebec6` checkpoint 两个 locator key 均不存在，raw
payload SHA256 `b7c08e84ff38c8e0b21003afb5f670a483bcdaffa58cf34e5a94e506e8bd3786`。
WAL/SHM 前后不存在，DB SHA256 前后均
`aa0b2e97b6c9e5d63dfd6870ef47201ba21e332f348f14b5c23469788ba05e55`；query_only、只读
事务/finally close，ormsgpack opaque Ext 保留，无业务对象实例化。不据此声称 Task/lease
已鉴真或回放成功。30585/41794 仍 FAIL；将来 Main 精确采用及正常构建后，才有资格在当前
新私有副本进行另行授权的实际业务回放。

Root 对新 D scope `2da4299345a6efe3d6dc839486db3a8c4b8b46c1b2c90f15cfc3f8bba4c66d97`
的19 pins及文本/AST独立核对通过，C0/I0/M0；peer收据 SHA256
`206acde15a93322eb68bf4bddc50a74db6ed90bb8edc0bd5629beeaefc05c146`。
经其明确授权，物理 D 生产已精确采用 `ae8621…99618`，保留库存快路径；unset
`RESEARCH_GRAPH_BINDING_SOURCE` 后三份映射测试实际 29 PASS、4.80s/exit0，日志
`temp/replay-deadline-fence/adopted-D-green.log`。新三测试在实际采用位置仍 strict0/Ruff0。
实际 D 生产路径 strict21：相比临时目录候选20，多出未改行 `recipe._ROOT` 的
`reportPrivateUsage`（原D917→现D923）；报告此位置差异，不声称物理整源 strict0。
实际 D 生产 Ruff 为原8条E501，临时目录的额外I001不在规范包路径出现；未修既有诊断。
精确提交仅列上述5路径；提交身份及索引检查由提交后另存私有收据。
