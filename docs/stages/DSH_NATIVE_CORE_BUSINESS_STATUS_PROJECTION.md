# DSH 原生工作台业务裁决投影实施卡

日期：2026-08-31
任务：`NATIVE-03/04-BUSINESS-STATUS`
所属阶段：`DSH-NATIVE-CORE`
状态：`accepted / verified`

本文只解决官方 DSH Web 中“Agent 执行完成，但用户看不见 Decision Hub 业务裁决”的产品缺口。
它不新建前端、不复制 DSH Session/Trajectory、不修改 Agent Loop，也不扩大 live capability。

## 1. 当前产品事实

官方 DSH Web 的三场景 replay 与进程级恢复已经走通。浏览器可看到原生 Session、轨迹、工具调用、
Agent preset 和 Decision Hub Client Plugin；Hub 同时保存 Evidence、Sufficiency、Gate、Artifact 和 Ledger。

本任务修复前的不可验收根因是状态语义混在一起：

```text
DSH Session state = completed
Hub business Gate = publish | degraded | research_only | reject
```

`completed` 只表示本次 DSH turn 有可验证终态，不表示事实充分，更不表示可以发布或交易。
在 `partial_failure` 场景里，DSH turn 正常 completed，但 Hub 实际为 `reject`、hard coverage 16.7%，
且存在 capability failure。现已通过 canonical 业务摘要和官方 Client slot 分离显示；页面不再把 `Run completed` 误读为业务成功。

## 2. 本任务可观察目标

Owner 在官方 DSH Web 选择一个 Decision Hub Session 后，无需阅读 raw JSON，即可同时看到：

1. DSH 执行状态；
2. Hub 业务状态与确定性 Gate；
3. Evidence coverage 与 hard coverage；
4. Stop Reason；
5. 失败 capability 的 `error_code/origin/cause_code/retryable` 摘要；
6. 指向 Decision Desk 完整业务详情的链接。

成功、部分失败、事实不足三种 replay 必须在人类可读状态上明显不同。

## 3. 所有权与数据流

```text
Hub Ledger / Research Query View
  -> canonical dsh-business-status.v1
  -> Hub public read-only endpoint
  -> official DSH Host Plugin validates and forwards
  -> official DSH Client Plugin renders in a native slot
```

强制边界：

- Gate 只能读取 Hub 已提交 Artifact 的 `gate_status`，Host/Client 不得根据 `completed`、coverage 或模型文本推断；
- Hub 只返回浏览器安全摘要，不返回 prompt、request hash、Provider payload、完整 Evidence 或 raw JSONL；
- Host 只调用 Hub 公开 Query View，不读 Hub SQLite；
- Client 只使用官方 `dsh.client`/slot，不复刻 Session、Trajectory 或 Decision Desk；
- 跨 Python/TypeScript 数据只来自 `contracts/schemas/dsh_host_bridge.schema.yaml` codegen；
- Hub 不可达、schema 不兼容或摘要校验失败时明确 degraded/fail-closed，不伪装为业务成功。

## 4. 代码结构

```text
contracts/schemas/dsh_host_bridge.schema.yaml
  dsh_business_status / dsh_business_failure

packages/query_views/research/service.py
  从 Research Result、Artifact 和 tool invocation 组装安全业务摘要

apps/hub_api/main.py
  GET /v1/dsh/sessions/{run_id}/business-status

extensions/dsh/decision-hub/src/host/hub-client.ts
  获取并校验 canonical 摘要

extensions/dsh/decision-hub/src/host/bridge.ts
  将摘要加入现有 /api/decision-hub/status 浏览器投影

extensions/dsh/decision-hub/src/client/index.js
  在官方 Session header slot 渲染 Gate/Coverage/Stop/Failure 摘要
```

不新增数据库表、migration、队列、状态机、Provider client、Agent Loop 或第二个前端。

## 5. BDD 验收

### 场景 A：执行完成且业务通过

```text
Given DSH Session state=completed 且 Hub Artifact gate_status=publish
When Client Plugin 读取浏览器投影
Then 页面分别显示 Run completed、Gate 可发布、hard coverage 100%
And 不显示 raw synthesis JSON
```

### 场景 B：执行完成但业务拒绝

```text
Given DSH Session state=completed 且 Hub Artifact gate_status=reject
When hard coverage 不足或 capability 部分失败
Then 页面醒目显示 Gate 拒绝、实际 coverage、Stop Reason 和失败 capability
And 不把 completed 显示为业务成功
```

### 场景 C：Hub 摘要不可用

```text
Given DSH 自身仍可运行但 Hub Query View 不可达或返回不兼容 schema
When Client Plugin 请求状态
Then 状态为 degraded，error_code 可读，Decision Desk 链接保留
And 官方 DSH 页面不白屏、不展示缓存伪结果
```

### 场景 D：职责不重复

```text
Given 用户打开同一个 Run 的 DSH Web 与 Decision Desk
Then DSH Web 只展示执行轨迹和最小业务摘要
And Decision Desk 继续拥有 Evidence/PIT/Gate/Ledger/Evaluation 完整详情
```

## 6. TDD 与自测门

先写失败测试，再实现最小改动：

- Python：`publish`、`completed + reject`、partial failure、无 Artifact/运行中、Run 不存在；
- Contract：未知字段拒绝、coverage 范围、failure provenance、Python/TypeScript codegen 一致；
- Host：摘要成功、404 尚未物化、Hub 503、schema mismatch、version mismatch；
- Client：success/reject/degraded/empty、无 raw JSON、长 Stop Reason 不撑破容器；
- Client 生命周期：覆盖 Session 已 completed 但 Hub Gate 尚未提交的窗口；最多 80 次、每 3 秒一次有界轮询（与 240 秒 worker 上限一致），业务终态后立即停止；
- Browser：官方 DSH 桌面与移动视口、轨迹/工具展开、console、横向溢出和截图 hash；
- 回归：三场景 replay、recovery、全仓离线质量门。

阶段完成前必须实际运行：

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
./.venv/bin/python tools/dsh_native_acceptance.py
./.venv/bin/python tools/dsh_native_acceptance.py --recovery
```

## 7. 退出与停止条件

本任务完成只关闭 `NATIVE-03/04` 的业务状态可读性，不代表 live 市场可用、预测准确、盈利、
自动交易或 DSH Promotion。Fixed 仍是 active，DSH 仍是 candidate/shadow，Replay 仍只用于诊断与验收。

若实现需要修改官方 DSH 上游、读取其私有数据库/JSONL、复制官方页面、让 Host 自行裁决 Gate，
或引入新的 durable 状态，则立即停止并回到 ADR/owner，不以局部补丁继续。
