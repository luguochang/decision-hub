# 前端运行态验收与根因修复记录

日期：2026-09-01（Asia/Shanghai）
状态：`recorded / owner review`
范围：Decision Desk Research 页面、Hub API、durable research worker、Replay Runtime。
本记录不把 replay 验收解释为实时联网产品验收。

## 1. 本次目标

从用户可见的前端入口触发一条研究任务，核对从提交到后台 Run、研究计划、工具活动、Evidence、Sufficiency、Gate 和 Trace 的完整链路；对页面截图和 API 返回做事实记录；发现问题后只修复根因，不通过文案掩盖失败。

## 2. 运行矩阵

| 项目 | 旧浏览器实例（8010） | 本次干净实例（8970） |
|---|---|---|
| 数据库 | 历史 `/tmp/decision-hub-r2r-ui...` | 新建 `/tmp/decision-hub-ui-current2.mYL4hg` |
| API | 旧进程，返回 `research-run-view.v1` | 当前源码，返回 `research-run-view.v2` |
| Research worker | `replay`，历史实例 | `current-ui-research`，`replay` |
| Runtime | `fake` | `fake`（Research Runtime 为 `research-replay`） |
| 网络 sources | 关闭 | 关闭 |
| Research capabilities | 空 | 仅 `replay.research` |
| Provider/LLM | 未配置 | 未启用 |
| 产品含义 | 不能作为当前源码验收入口 | 离线回放验收，不是实时联网 |

8010 页面出现“研究队列不可用”的直接原因是前端 Zod 契约要求 `research-run-view.v2`，而旧 API 仍返回 `v1`。其根因是机器上并存多个旧 API、worker、前端和数据库；仅刷新浏览器不会更新服务进程或数据目录。历史进程和数据未修改。

## 3. 前端实测链路

在 `http://127.0.0.1:8970/` 的 Research 页面提交：

```text
Fed speech replay acceptance: verify official policy change and cross-market transmission,
then produce independent 30m/24h/72h bounded decisions.
```

得到：

- `202`，创建 durable Run `run_afb98ca385e84467b10db21d148bb520`；
- worker heartbeat 为 `online`，模式为 `replay`；
- Run 进入 `research_only`，`hard coverage=16.7%`；
- `2` 个有界证据轮次，保留 `critical_data_unavailable`；
- 1 条 official event identity Evidence；
- 2 个 replay capability invocation 和 2 个对应 tool result；
- 12 条规范化 Trace；
- 没有生成方向性 30m/24h/72h 决策，因为关键事实缺失。

API 事实（提交后查询）：

```text
status=research_only
runtime_id=research-replay
current_round=2
total_tool_calls=2
rounds=[(1, 1 invocation, 1 result), (2, 1 invocation, 1 result)]
hard_coverage_ratio=0.16666666666666666
stop_reason=critical_data_unavailable
```

这条结果证明离线 durable 链路和 fail-closed Gate 正常；它不能证明联网搜索、实时行情、主动补证或预测价值。

## 4. 发现与根因

### 4.1 已修复：工具计数与工具轨迹不一致

原始 replay fixture 的 `ResearchSessionResult.total_tool_calls=1`，但每个 Round 的 `tool_invocations=[]`、`tool_results=[]`。因为 `repeat_last_result=true`，第二轮重复同一结果后，页面显示 `2/12 tools`，却显示“本轮未记录工具调用”。这会让用户误判系统曾经真实调用工具。

根因在回放资产契约，而不是 React 渲染：fixture 声称有一次调用，却没有可投影的调用记录；第二轮复用该资产又把累计计数放大。

修复：

1. 在 `packs/crypto_macro/fixtures/research-worker-replay.json` 为归档结果补齐一个明确的 `tool_invocations` 和 `tool_results`，并与 Evidence 的 `tool_call_id` 对齐；
2. 在 `tests/runtime/test_replay_research_runtime.py` 增加资产一致性断言：每个 replay result 的 `total_tool_calls` 必须等于所有 Round invocation 数之和；
3. 重新启动 worker 后复验，页面现在能显示每轮 `research_capability_execute`、attempt、latency、cost 和成功状态。

### 4.2 仍需根治：单一启动入口和运行态指纹

当前机器仍有 8000、8010、8020、8030、8031 以及多个 Vite/DSH 进程。用户打开错误端口时，页面可能显示旧 schema、旧数据库或旧 runtime。这个问题不能靠前端 fallback 解决，否则会把不兼容状态伪装成空页面。

后续产品启动必须只使用 `infra/dsh/run-product.sh`（live）或明确标注的隔离 replay 命令，并在前端显著显示：API revision、data directory 标识、runtime mode、worker instance、enabled capabilities、source enablement。该项属于后续启动/产品收口任务，本记录未擅自扩展实现范围。

### 4.3 产品能力仍未完成

本次实例的 sources、market 和 LLM 均关闭，Research Runtime 是回放；因此“发现信息不足后主动联网补齐”没有发生。页面正确地 fail-closed，但仍然不是用户期待的实时智能体。`pilot_ready=false`、`pilot_usable=false` 保持不变。需要在 owner 授权的隔离 live canary 中完成真实 Search/Official/Market capability 闭环，不能用本记录的 replay 证据替代。

## 5. 截图与证据资产

本次干净实例截图（Git 忽略的中间产物）：

- `tmp/evaluations/frontend-runtime-20260901/desktop-full.png`
- `tmp/evaluations/frontend-runtime-20260901/mobile-375x812.png`

移动端检查结果：`document.documentElement.scrollWidth == window.innerWidth == 375`，未发现横向溢出。截图只用于展示页面状态，不是实时能力证据。

## 6. 测试证据

本次变更后执行：

```text
git diff --check                                      passed
pytest tests/runtime/test_replay_research_runtime.py \
       tests/research/test_research_observability.py 11 passed
```

此前工程门仍以 [当前状态短上下文](../context/CURRENT_STATE.md) 和 [产品收口执行记录](PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md) 为准；本记录没有重写历史 Run、Artifact 或 Forecast。

## 7. 下一张有界任务卡（需 owner 确认后执行）

**目标：G2-LIVE-01：在唯一 live 启动入口完成一次真实、只读、限时的 Search -> Evidence -> Sufficiency -> Gate -> 人可读报告闭环。**

非目标：不切换 active runtime，不安装社区插件，不做 ASR/PPT/第二领域，不自动交易，不把 Search 失败改写为成功。

BDD 退出门：

```text
Given 一个新的高影响宏观事件和唯一 live 运行态指纹
When DSH Research Session 发现关键事实缺口
Then 它至少发起一个已审计 capability 调用，并在页面显示真实成功或结构化失败
And 结果进入 Hub Evidence/PIT/Gate 账本，或明确 fail-closed
And 固定 baseline 与候选结果可区分，pilot_usable 仍由 owner 价值观察决定
```

开始实现前必须先完成 live preflight（Provider route、capability allowlist、数据目录、费用和截止时间）并将结果追加到独立执行记录；若 preflight 失败，停止在能力准入边界，不转向外围功能。
