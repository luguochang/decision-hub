# E2L-02 DSH 耐久进度、模型步时限与部分结果恢复

版本：`E2L-02.v1`
所属目标：`PRODUCT-CLOSEOUT-01`
状态：`engineering/replay complete / live capability pending`
产品状态：`pilot_ready=false / pilot_usable=false`

本阶段卡记录 E2-L 真链路暴露的根因，并锁定实现、测试和停止线。它不改变 DSH-first
产品架构，不增加第二套 Agent Loop、聊天入口、任务队列或插件协议。

## 1. 根因证据

2026-09-01 隔离 live Run `run_d8ae007466b242648d264a25728deae9` 的官方 DSH JSONL 显示：

- 第一模型步耗时约 126.9 秒，超过 Pack 声明的 60 秒模型步预算；
- DSH 真实调用 `official.macro`，Fed speeches feed HTTP 200 并返回一条 official EvidenceCandidate；
- 第二模型步耗时约 52 秒，产品总时限 180 秒到期后 DSH 写入 `turn/end=aborted/user`；
- Host 正确回写 Session `cancelled`，但 Hub 最终报告为 `tool_calls=0/evidence=0`。

因此问题不是“只要把 180 秒调大”，而是两个协议缺口：模型步预算没有运行时 watchdog；
能力结果要等完整 SessionResult 才入账，后续模型步失败会丢掉已经成功的事实。

## 2. 本阶段目标与非目标

### 2.1 目标

1. Research MCP 每次成功或失败的 capability 调用在返回 DSH 前写入对应 Run 的 Tool Trace、
   Evidence 或 Error Provenance；完整 SessionResult 到达时重复投影必须幂等。
2. DSH Host 执行 canonical model-step timeout；capability timeout、model-step timeout、owner
   cancel、product total deadline 保持不同错误来源和终态。
3. 超时或进程重启后保留已完成进度；retry 创建 child Run，不改写父 Run、Session、JSONL、
   Evidence、Forecast 或 Outcome。
4. DSH Web 与 Decision Desk 展示人可读的已完成能力、保留证据、停止原因和可重试状态，
   不要求用户阅读原始 JSON。

### 2.2 非目标

- 不 fork/clone DSH，不复制其 Chat、Session、Trajectory、JSONL、compaction 或 Agent Loop。
- 不在 Hub/LangGraph 重写 ReAct、Supervisor、角色调度或 Provider client。
- 不让模型、MCP 或插件发布 Artifact/Forecast；确定性 Gate 仍是唯一裁决者。
- 不引入 Redis、Celery、Kafka、Temporal、DBOS、Postgres 或新插件框架。
- 不把失败改写成 `no_trade`，不以静态 replay 或模型自述证明实时价值。

## 3. 运行与所有权

```text
官方 DSH Web（唯一入口）
 -> Hub manual Run + immutable Trigger Snapshot
 -> DSH Agent Loop
 -> Research MCP / Capability Gateway
      -> schema、authority、PIT、freshness、timeout、cost、permission
      -> Durable Progress Recorder（Session link 解析唯一 run_id）
           -> tool_started/tool_completed/tool_failed
           -> EvidenceCandidate 即时入账
      -> typed result 返回 DSH
 -> DSH 继续发现缺口、补证、综合
 -> 完整综合：Gate 决定 Artifact；不完整：安全失败但保留进度
```

DSH 继续拥有内层 Agent Loop 和 JSONL；Hub 继续拥有 Run/Evidence/PIT/Gate/Artifact/Outcome。
LangGraph 只拥有外层有界证据轮次、checkpoint、lease、recovery。MCP 仍是唯一能力入口，
新增 recorder 只是 Gateway 的组合装饰器，不是新的工具协议。

## 4. 截止与预算

| 截止 | 所有者 | 失败语义 |
|---|---|---|
| capability | Manifest/DSH MCP timeout policy | 当前 tool failed，其他能力可继续 |
| model step | canonical submit/Host watchdog | Session failed，`dsh_model_step_timeout` |
| product total | ResearchSessionRequest/worker | Run `provider_timeout`，`dsh_web_deadline_elapsed` |
| owner cancel | owner command/Host cancel | Session/Run cancelled，`owner_cancelled` |

首版 live Pack 预算固定为：

```text
per_tool_timeout_seconds       = 20
per_model_step_timeout_seconds = 150
total_deadline_seconds         = 480
```

480 秒仍是硬上限；后续只能依据至少 20 个 live 高影响事件的 P95、成本和价值评测，经过
ADR 调整，不能无限延长等待。

## 5. 失败后重试与证据规则

- retry 只创建新的 child Run 并记录 `parent_run_id`，父历史不变；
- 父 Run 的 accepted Evidence 只能在新 Run cutoff 下重新校验 freshness/PIT 后使用；
- stale Evidence 只作历史上下文，不能满足新 Run 的 hard coverage；
- 同一 Evidence ID/content hash/Trace 重放幂等，内容冲突 fail-closed；
- capability 已成功而 synthesis 失败时，页面必须显示“已保留 N 条证据”，不能显示 0。

## 6. 代码边界

```text
contracts/schemas/dsh_host_bridge.schema.yaml
  canonical model-step budget + codegen
packages/workbench_adapters/durable_research_gateway.py
  Session -> Run 校验、即时 Trace/Evidence
apps/research_mcp/main.py
  组合同一 Database、Pack requirements 和 recorder
extensions/dsh/decision-hub/src/host/bridge.ts
  官方 Session event/cancel watchdog
packages/runtime_adapters/dsh_runtime/web_runtime.py
  三类 deadline 映射和恢复
packs/crypto_macro/pack.yaml
  20/150/480 budget
apps/decision-desk/src/research/ 与 extensions/dsh/decision-hub/src/client/
  部分进度的人可读投影
```

依赖方向仍为 `apps -> packages -> contracts`；Kernel 不导入 DSH/MCP；前端只消费 Query/View DTO。

## 7. BDD 验收

### B1 成功能力后模型超时

Given official capability 已返回；When 下一模型步超时；Then Session failed，原因
`dsh_model_step_timeout`，Run 不发布 Artifact/Forecast，已成功 Tool/Evidence 保留且页面显示数量。

### B2 单能力失败不取消其他成功项

Given official/crypto 成功、cross_asset timeout；Then 成功 Evidence 保留，失败保存
`error_code/origin/cause_code/retryable`，不得显示 0 工具/0 证据。

### B3 取消语义

owner cancel -> `cancelled/owner_cancelled`；model watchdog -> `failed/dsh_model_step_timeout`；
total deadline -> `provider_timeout/dsh_web_deadline_elapsed`。

### B4 幂等恢复

同一 MCP 结果或 SessionResult 重放不增加计数；同 ID 不同内容冲突失败。

### B5 官方 DSH Web

fresh 隔离库从官方 DSH Web 提交 manual/100 Run，验证真实模型、真实 MCP、非零 Tool/Evidence、
完整报告或解释性停止、失败安全、截图/console/API 非敏感证据；不得伪造 `no_trade`。

## 8. TDD 和最终 Checklist

### 文档与契约

- [x] 记录真实 JSONL 时间线和根因；
- [x] 锁定 Gateway 即时入账、三类截止和停止线；
- [x] ADR-0016；
- [x] canonical DSH submit schema 和 Python/TS codegen；
- [x] 模块 README、runbook、CURRENT_STATE、CHANGELOG 同步。

### 实现

- [x] recorder 校验 Session -> Run、round 和状态；
- [x] 成功能力即时写 Tool Trace 和 Evidence；
- [x] 失败能力即时写完整 provenance；
- [x] replay/完整结果重放幂等；
- [x] submit 传递 model-step timeout；
- [x] Host watchdog 区分 model/owner/product deadline；
- [x] 页面显示部分成功和已保留 Evidence。

### 自动化和产品门

- [x] targeted Python/Host/UI TDD；
- [x] `pytest -m "not live"`、Ruff、Pyright；
- [x] contract codegen、module docs、`git diff --check`；
- [x] DSH plugin test/build、Decision Desk test/build；
- [x] compose config、fresh migration、recovery smoke；
- [ ] fresh 官方 DSH Web 成功路径；
- [ ] model-timeout 安全失败路径；
- [ ] 桌面/移动截图、console、API 摘要和 hash；
- [ ] 停止隔离实例并保留非敏感验收记录。

自动化门、契约和本地回放门已通过；官方 DSH Web 成功路径、model-timeout 安全失败路径和
当前 revision 的浏览器移动/console 资产仍 pending，因此 E2L-02 不能标记为最终完成。
完成 E2L-02 也只允许判定 `pilot_ready`；`pilot_usable` 仍需 14 天或 20 个高影响事件的
前瞻价值观察。关键 Search/实时能力未通过时，不得宣称可交易价值。

## 9. 本轮验证记录（2026-09-01）

工程/replay 收口验证已完成：

```text
.venv/bin/pytest -m 'not live' -q                 361 passed
.venv/bin/pyright                                0 errors / 0 warnings / 0 informations
.venv/bin/ruff check .                           passed
.venv/bin/python -m tools.contract_codegen check passed
.venv/bin/python tools/docs/check_module_docs.py passed (13 modules)
pnpm --dir extensions/dsh/decision-hub test       39 passed
pnpm --dir extensions/dsh/decision-hub build      passed
pnpm --dir apps/decision-desk test                9 passed
pnpm --dir apps/decision-desk build               passed
docker compose config --quiet                     passed
git diff --check                                  passed
```

本轮根因修复覆盖：Durable Gateway 即时 Evidence/Trace、`CancelledError` 控制流、Host
model-step watchdog、terminal callback 并发互斥、Query/View 部分进度和 Decision Desk
逐能力错误展示。测试结果只证明契约、回放和本机工程边界；此前真实 Search timeout、
官方 DSH Web live 成功/部分失败/model-timeout 资产和 14 天/20 事件价值观察仍 pending。
