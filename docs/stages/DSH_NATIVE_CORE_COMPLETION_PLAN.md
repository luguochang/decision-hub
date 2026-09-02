# DSH-NATIVE-CORE 完成实施方案

日期：2026-08-31
版本：`NATIVE-CORE-EXECUTION-2026-08-31.v1`
状态：`accepted / engineering acceptance complete / product value pending`
唯一阶段：`DSH-NATIVE-CORE`

本文是 [DSH Native Web Product Core Stage Charter](DSH_NATIVE_WEB_PRODUCT_CORE.md) 的执行任务卡。Stage Charter 负责范围和退出门；本文负责把当前代码事实、已发现的根因缺口、实施顺序和证据位置固定下来。若与 ADR、canonical schema 或 Stage Charter 冲突，必须停止并修订更高优先级事实源。

## 0. 本次执行目标登记（2026-08-31）

### 目标

在不重写 DSH Agent Loop、不开启实时市场能力、不切换 active pointer 的前提下，完成
`DSH-NATIVE-CORE` 的官方 Web 产品闭环验收：固定官方上游、官方
`dsh.bundle`/`dsh.client` 插件、`run_id <-> dsh_session_id` 耐久关联、成功/部分失败/事实不足三类
replay、重启/丢 callback/版本回滚恢复，以及人可读浏览器证据。

### 产品价值断言

Owner 打开官方 DSH Web 时，能够看到 DSH 原生 Session/Trajectory/Tool/Subagent/JSONL；同一正式
Decision Run 能在 DSH Web 和 Decision Hub Ledger 之间互相定位；任务失败、证据不足或版本不兼容时，
页面和账本给出可解释的状态，且不会发布未经 Gate 允许的方向性 Forecast。该断言只证明工程链路和失败安全，
不证明实时来源稳定、预测准确率、盈利或自动交易。

### 允许路径

- `infra/dsh/`：使用 `upstream.lock.json` 固定、校验、构建和启动官方 Web。
- `extensions/dsh/decision-hub/`：仅实现官方 Host/Client plugin 和 bridge，不复制上游 Web/Session/Agent Loop。
- `packages/runtime_adapters/dsh_runtime/`、`apps/hub_api/`、`apps/hub_worker/`、`packages/kernel/`：仅按
  `dsh_host_bridge.v1` 做耐久关联、恢复、callback 和 Ledger 投影。
- `tests/dsh_native/` 与 `docs/evaluations/`：保存 contract、replay、恢复和浏览器证据。
- 官方源码闭包中同版本的 `@deepseek-ai/dsh-llm-replay` 只能作为 keyless acceptance transport；它属于
  测试支持包，不得成为生产网络能力或新的 Provider 抽象。

### 明确禁止

- 不 fork/clone 或修改固定上游源码，不把上游测试插件复制到业务目录。
- 不在 Hub、LangGraph 或插件内新增第二套 Agent Loop、Supervisor、Provider client、trace、账本或重试体系。
- 不扩大 live Search/Official/Market capability，不写入真实密钥，不自动交易，不修改历史账本，不切 active pointer。
- 不把 contract-level fake Host、离线 replay 或单条官方 Web smoke 写成产品可用/收益证明。

### 三个核心验收断言

1. 一个正式 Run 在官方 Web 中最多创建一个确定性 Session；重复提交、进程重启和 terminal callback 重放不产生重复
   Session、Evidence、Artifact 或 Ledger commit。
2. 官方 Web success、partial failure、insufficient/stale 三类 replay 均能在 DSH JSONL、Hub callback/SSE、
   Evidence/Sufficiency/Gate/Artifact/Ledger 和浏览器视图中对齐；失败必须保留 error provenance 并 fail-closed。
3. 上游/plugin/schema 版本不兼容或 recovery 证据不完整时，readiness/Run 状态明确为失败或 pending，
   Fixed active、历史数据和 active pointer 保持不变。

### 停止条件与外部前置

如果同版本 replay package 无法从固定上游闭包构建，或者要取得真实网络证据，则停止在官方 Web 证据边界，
记录 `pending_external_prerequisite`，请求 owner 提供固定 replay transport 或一次隔离、只读、限时的 Provider
canary 授权；禁止自制 transport 冒充官方 Web 证据。完成本地可复现证据后，阶段仍需独立 owner review 才能
标记 `complete`，NATIVE-06 live/value Gate 另行授权。

## 1. 当前产品结论

当前产品不是“可直接用于实时市场决策”的完成品。已经证明的是：固定的官方 DSH Web 可以构建和启动，Hub 与官方 DSH Session、MCP capability、DSH JSONL、callback 和 Hub Ledger 存在一条真实隔离 replay 链；success、partial failure、insufficient/stale 三类结果都能被正确投影并由 Gate fail-closed。Fixed baseline 仍是 active，DSH 仍是 candidate/shadow，Replay 只用于工程诊断。

当前阶段完成后的产品形态是：

```text
官方 DSH Web
  -> 官方 dsh.bundle / dsh.client Decision Hub 插件
  -> 确定性 run_id <-> dsh_session_id bridge
  -> DSH Agent Loop / Tool / Subagent / JSONL
  -> Hub Evidence / PIT / Sufficiency / Gate / Ledger
  -> DSH 原生轨迹 + Hub 人可读结果 + 可恢复运行
```

这仍不等于真实网络来源稳定、预测准确率已证明、自动交易已授权或 DSH 已 Promotion。

## 2. 本轮审计得到的必须修复项

以下问题在退出阶段前必须以测试和运行证据关闭，不能用提示词、手工时间戳或 callback 端幂等掩盖：

| 编号 | 根因 | 影响 | 关闭证据 |
|---|---|---|---|
| `NC-F1` | Host 的事件协调和结果查询可并发越过 terminal sent 检查 | 同一 Session 可能发送两次终态 callback | 并发 event/status/result 测试只收到一次 callback，失败可重放 |
| `NC-F2` | Capability Gateway 捕获 `BaseException` | owner cancel 或 worker shutdown 可能被伪装成可重试 Provider 失败 | `CancelledError` 保持取消语义；只有真实 timeout/provider 错误进入错误分类 |
| `NC-F3` | Domain source manifest 的 `authority_floor` 未投影到 canonical requirement/Gate | 低权威资料可能错误覆盖 hard requirement | 六类事实低权威 fixture 均被拒绝；合格 authority 才能覆盖 |
| `NC-F4` | Tool Activity 只显示顶层 Run 状态，没有渲染 per-capability provenance | 部分成功/部分失败无法被用户解释 | 前端展示 capability、error code、origin、cause 和 retryable |
| `NC-F5` | NATIVE-04 只跑过单条 replay，未形成成功/部分失败/关键事实不足矩阵 | 无法证明真实产品边界和失败可见性 | 三类 fixture 同时有 DSH JSONL、Hub API/ledger、SSE/UI 证据 |
| `NC-F6` | 总装代码地图保留已被 Stage Charter supersede 的旧 NATIVE 编号 | 新任务容易目标漂移 | 代码地图只引用当前 `NATIVE-00..06` 编号，历史编号明确标记 superseded |
| `NC-F7` | 官方 DSH 页面只显示 Session `completed`，没有区分 Hub 业务 Gate | 用户会把执行终态误读为可发布结果，partial failure 也不可解释 | canonical 业务摘要同时显示 Gate、hard coverage、Stop Reason 和 capability failure；Host/Client 不推断 Gate |

## 3. 实施任务卡

任务严格按顺序执行；每张卡必须先写/更新 BDD 和 TDD，再实现最小改动，再运行卡片级检查。

### `NC-01` 文档和契约一致性

目标：让所有长期文档指向同一阶段编号、状态和退出门。

范围：`docs/stages/**`、`docs/product/DSH_HUB_CODE_MAP.md`、`INDEX.md`、`docs/context/**`、`CHANGELOG.md`。

动作：

- 以 Stage Charter 的 `NATIVE-00..06` 为唯一任务编号；旧总装编号只保留历史说明并显式标记 superseded。
- 把 `NC-F1..F6` 和当前三类 replay 矩阵写入状态/交接文档。
- 不把 `partial`、`scaffold`、`replay` 或 `candidate` 写成 `complete`/`product_ready`。

验收：文档检查通过；任意当前入口都能定位本方案、Stage Charter、ADR-0012/0013 和最新状态。

### `NC-02` Host terminal 幂等

目标：并发协调最多产生一个确定性的 terminal callback；callback 失败后仍可重放。

范围：`extensions/dsh/decision-hub/src/host/**`、对应 Vitest。

复用：官方 DSH `sessionController`/事件 seam、现有 Hub callback 客户端和 canonical bridge schema。

不做：不改 DSH 上游、不在插件内创建队列或业务账本。

BDD：

```text
Given 同一 completed Session 同时触发 event、status 和 result 协调
When 三路都尝试发送 terminal callback
Then Hub 只收到一个 terminal callback
And 首次 callback 失败时，后续协调可以重放同一个 payload
```

TDD：覆盖 in-flight 互斥、成功后 sent、失败清除、generation/last_seq 保持不变。

### `NC-03` 取消与错误 provenance

目标：取消、超时、Provider、MCP、DSH 和外层错误在来源上可区分。

范围：`packages/kernel/decision_hub_kernel/application/research_evidence.py`、错误映射、相关测试。

复用：标准 `asyncio.CancelledError`、现有 `ErrorProvenance` 和框架 timeout；不自写异常体系。

BDD：

```text
Given owner 取消正在运行的 capability
When adapter 抛出 asyncio.CancelledError
Then 取消向上保留，不写成 search_provider_failed
And Run 进入 cancelled 或由上层明确协调的终态
```

TDD：分别注入 owner cancel、worker shutdown、bounded timeout 和真实 Provider exception；断言 `error_code/origin/cause_code/retryable`。

### `NC-04` authority floor canonical 化

目标：来源权威下限成为跨语言 canonical contract 和确定性 Sufficiency Gate 的一部分。

范围：`contracts/schemas/**`、codegen、`packages/provider_adapters/research/**`、`packages/kernel/decision_hub_kernel/decision/sufficiency.py`、fixtures/tests。

约束：禁止继续双写 `source_manifest.yaml` 与运行时 `requirements.yaml`；若两者职责暂时不同，必须在文档中说明生成方向和 hash。

BDD：

```text
Given requirement 声明 authority_floor=official
When 只有 unverified/web 证据覆盖该 requirement
Then requirement 仍为 uncovered，Gate 为 research_only/reject
When 存在满足 authority floor 且 PIT/freshness 合格的证据
Then 才允许进入 covered 集合
```

TDD：六类事实各一条低权威拒绝和合格权威通过 fixture；codegen、Python、TypeScript/Zod 镜像一致。

### `NC-05` 三类 replay 产品验收矩阵

目标：同一条官方 DSH Web 路径可解释成功、部分 capability 失败和事实不足三种结果。

固定场景：

1. `success`：所有 hard facts 由新鲜、满足 authority floor 的 replay capability 返回，Gate 结果可接受。
2. `partial_failure`：至少一个 capability 失败或超时，其他成功 Evidence 保留，失败带完整 provenance；Gate 仍按关键缺口 fail-closed。
3. `insufficient_or_stale`：资料 stale/低权威/冲突，最终 `research_only/reject`，不产生方向性发布。

contract-level matrix 与官方 DSH Web 三场景均已完成，证据见
[`DSH_NATIVE_CORE_REPLAY_MATRIX_2026-08-31.md`](../evaluations/DSH_NATIVE_CORE_REPLAY_MATRIX_2026-08-31.md)。
官方 Web 验收使用固定上游闭包内同版本的 keyless replay transport；它不能替代真实网络或授权 Provider 的价值证据。

每个场景必须同时保存：

- 官方 DSH Session/Trajectory/JSONL；
- Hub `run_id`、`dsh_session_id`、timeline/SSE 和 callback；
- Evidence、Sufficiency、Gate、Artifact/Ledger 查询结果；
- 浏览器桌面/移动截图和 console 结果（截图路径、hash、commit、端口写入验收报告）。

#### `NC-05B` 官方工作台业务裁决投影

实施细节、代码所有权、BDD/TDD 和停止条件见
[`DSH 原生工作台业务裁决投影实施卡`](DSH_NATIVE_CORE_BUSINESS_STATUS_PROJECTION.md)。
该任务明确分离 DSH Session 执行态与 Hub 业务 Gate；`completed + reject` 是必须支持并醒目展示的正常组合。
只有官方 Client Plugin 能显示人可读 Gate/Coverage/Stop/Failure 摘要且不输出 raw JSON，`NATIVE-03/04`
才可完成。

### `NC-06` 重启、丢回调、升级和回滚

目标：DSH Web 或 Hub worker 在中断后不重复创建 Session、不重复 commit，版本不兼容时 fail-closed，并可回到锁定上游。

范围：`infra/dsh/**`、`packages/runtime_adapters/dsh_runtime/**`、`packages/kernel/**`、`tests/dsh_native/**`、runbook。

Host/Hub contract-level recovery 已有测试证据，官方 Web restart/rollback 仍是阶段退出门。

最小场景：

- create/accepted 之间进程退出，重启后按确定性 ID 恢复；
- Session 已完成但 terminal callback 丢失，reconciliation 从公开 status/result/history 补回；
- Hub commit 完成前/之后 worker 退出，重启只产生一份业务提交；
- upstream/plugin/schema 版本不兼容时 readiness 返回 `version_incompatible`；
- 切换到旧 lock 文件运行 replay smoke，历史账本和 active pointer 不变。

### `NC-07` 完整离线质量门

只有 `NC-01..06` 都有证据后运行完整质量门。任何未实际执行的命令都必须标记 `pending`，不能写成 passed。

```bash
git diff --check
./.venv/bin/python tools/docs/check_module_docs.py
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/pytest -m "not live" -q
./.venv/bin/ruff check .
./.venv/bin/pyright
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
docker compose config --quiet
./infra/dsh/acceptance.sh
```

浏览器验收还必须核对官方 DSH Web 原生 Session/Trajectory 非空、Hub 插件可见、三类结果的人可读状态、无 console error 和无横向溢出。

本轮实际结果（2026-08-31）：

```text
pytest -m "not live": 327 passed
research_acceptance.py: 28 passed；跨进程 recovery smoke passed
live_observation_acceptance.py: 43 passed
ruff: passed；pyright: 0 errors
contract_codegen check: passed；module docs: 13 modules ok
Decision Hub plugin: 15 passed；build passed
Decision Desk: 9 passed；build passed
docker compose config --quiet: passed
infra/dsh/acceptance.sh: NATIVE-00 source/build/authenticated Web passed
git diff --check: passed
```

因此 `NC-01..NC-04`、`NC-05/NC-06` 的 contract 证据、`NC-07` 离线质量门，以及官方 Web 三场景、进程重启、旧 lock 回滚和浏览器资产均已记录；工程验收完成，但不代表产品价值或 live 能力完成。

官方 Web 三场景、官方 Web restart、locked rollback 和浏览器证据已执行。固定上游源码闭包本身包含同版本的
`packages/test-support/llm-replay`（包名 `@deepseek-ai/dsh-llm-replay`），可通过
`DSH_REPLAY_PLUGIN=<locked-source>/packages/test-support/llm-replay` 注入临时 profile，作为
keyless acceptance transport；它是上游测试支持包，不是生产网络能力，也不进入业务目录。若 fresh
闭包构建时该包不可用，或要取得真实网络证据，则停止在官方 Web 证据边界，请 owner 提供固定
replay transport 或一次隔离、只读、限时 Provider canary 授权。禁止自制 transport 冒充官方 Web
证据，也不把 contract-level fake Host 证据升级为官方 Web 产品证据。

## 4. 阶段退出与产品可用边界

`DSH-NATIVE-CORE engineering acceptance complete` 的必要条件是：`NATIVE-00..05` 和本方案 `NC-01..NC-07` 全部有可复核证据。下一步仅允许进入 `NATIVE-06` 真实价值 Gate 或 `retain_sdk_or_stop` 评估；不能自动切 active pointer。

阶段完成不代表：

- 真实网络 Search/Official/Market 长期稳定；
- Forecast 准确率、收益或盈利；
- 自动交易、ASR、PPT、第二领域或社区插件市场已实现；
- DSH candidate 已 Promotion。

## 5. 文档同步清单

每张任务卡完成后，同一提交中更新：

- 受影响模块 `README.md`；
- `docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`；
- `docs/context/CURRENT_STATE.md`、`docs/context/HANDOFF.md`；
- `CHANGELOG.md` 和必要的验收报告；
- 新架构决定才新增 ADR，不把日常进度塞入 ADR。

`DSH-NATIVE-CORE` 工程目标已完成。2026-09-01 owner 已另行授权 `PRODUCT-CLOSEOUT-01` C1-C7；当前工作不再从本历史完成方案开新任务。原 NATIVE-06 的真实能力/value 证据由 `PRODUCT-CLOSEOUT-01/C4` 和后续价值观察承接，active pointer 仍不得自动切换。
