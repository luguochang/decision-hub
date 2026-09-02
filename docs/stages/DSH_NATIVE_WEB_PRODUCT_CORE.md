# DSH Native Web Product Core Stage Charter

日期：2026-08-31
状态：`accepted / engineering acceptance complete / product value pending`
阶段 ID：`DSH-NATIVE-CORE`
Owner Gate：2026-08-31，owner 明确要求根据产品现状完成方案、制定目标、实现并自测。

关联事实源：

- [DSH 与 Decision Hub 系统总装设计](../product/DSH_HUB_SYSTEM_ASSEMBLY.md)
- [ADR-0012 DSH-first 产品重新收口](../decisions/ADR-0012-dsh-first-product-rebaseline.md)
- [ADR-0013 DSH Web 原生插件与上游升级集成](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)

## 1. 阶段目标

交付一个可见、可恢复、可测试的 DSH 原生产品核心：owner 打开官方 DSH Web，能看到原生 Session/Trajectory/Tool/Subagent/JSONL，并看到 Decision Hub 插件提供的 Hub readiness、Run 状态和业务结果入口；同一个 Hub Run 只关联一个确定性的 DSH Session，重复提交、Host 重启、回调丢失和取消不会造成重复业务提交。

本阶段完成的是 DSH 原生 Web 与 Hub 的产品级集成核心，不把离线 replay 说成实时市场可用，也不把一次 Web smoke 说成预测有效。

## 2. 可观察产品结果

阶段退出时必须同时看到以下结果：

1. 官方 DSH Web 由固定上游版本启动，不是 Decision Desk 复刻页面。
2. Decision Hub 以官方 `dsh.bundle` + `dsh.client` Host/Client plugin 形式加载。
3. DSH 页面显示 Hub readiness、当前 Run、运行模式和管理后台入口。
4. 一个固定 replay Run 能创建普通 DSH Session，并通过 `run_id <-> dsh_session_id` 双向跳转。
5. DSH 原生页面显示 Session 历史和 Trajectory；Hub Ledger 仍显示 Evidence/Gate/Artifact。
6. 重复 submit 不创建第二个 Session；重启后可恢复或明确协调到终态。
7. 插件、Host bridge 或版本不兼容时 fail-closed，Fixed/replay/SDK fallback 和历史账本不被破坏。

## 3. 当前代码基线

当前实际状态：

```text
Decision Desk                      = 管理后台（DSH Web 为研究交互主壳）
deepseek-harness-sdk 0.1.1rc1     = 已验证的 Python SDK candidate/fallback
hub research worker               = durable Run/lease/commit
research-mcp                      = DSH capability 工具入口（默认 replay）
LangGraph                         = 外层 lifecycle/evidence-round/recovery
Fixed                             = active baseline
Replay                            = 默认离线诊断模式
DSH Host plugin                   = official Web verified；Host-Key、callback、readiness、submit/status/cancel 和业务摘要已验收
DSH Client plugin                 = official Web verified；native slot status projection、失败态和有界轮询已验收
decision-research Web preset      = repository-controlled official user preset；已通过 `DSH_HOME/.agent-presets` 安装并在隔离 Web Session 物化
official DSH Web default assembly = 隔离实例可启动并报告 readiness；生产 live 装配仍需独立价值 Gate
replay Session/Trajectory E2E     = verified；success/partial_failure/insufficient_or_stale 三场景均已通过
restart/upgrade/rollback smoke    = verified；callback gap、官方 Web restart 和 locked rollback 均已通过
```

历史 Run、Evidence、Artifact、Forecast、Outcome、Evaluation 和 migration 全部保留。

### 3.1 当前实施账本（2026-08-31）

这张表是本阶段的执行事实，不把“有文件”或“专项测试通过”解释成阶段完成：

| 任务 | 当前状态 | 已证明 | 尚未证明 |
|---|---|---|---|
| NATIVE-00 上游 pin/build | `verified` | 固定 commit、版本、tar hash、构建脚本、隔离 Web 启动、readiness 和 authenticated boot 已验证 | 后续上游版本升级门 |
| NATIVE-01 contract/link | `verified` | `dsh_host_bridge.v1`、codegen、`dsh_session_links` migration、Hub query/callback、幂等和恢复已验证 | 后续 schema/上游升级需重新验收 |
| NATIVE-02 Host plugin | `verified` | Host-Key 入站、Bridge-Key callback、readiness、幂等/取消、官方 Web submit/status 和业务摘要已验证 | live capability 仍需独立授权 |
| NATIVE-03 Client plugin | `verified` | package manifest、官方 `ModuleLoader`/native slot、status projection、失败态和有界轮询已验证 | 上游 UI 改版需通过插件契约回归 |
| NATIVE-04 dispatch/replay | `verified` | 官方 Web success/partial_failure/insufficient_or_stale 三场景均走通 Hub -> Session -> MCP -> JSONL -> callback -> Ledger；截图/console 已归档 | 实时来源质量和预测价值 |
| NATIVE-05 upgrade/recovery | `verified` | callback gap、官方 Web restart、multi-round continuation、version mismatch、locked rollback 已通过 | 生产 HA 与 live provider |

当前本阶段唯一允许的工程结论是：`DSH-NATIVE-CORE engineering acceptance complete`。NATIVE-06 的真实网络/价值 Gate 另行授权，不能据此宣称产品已可盈利或 DSH 已 Promotion。

### 3.2 最新隔离 replay 证据（2026-08-31）

| Run | 结果 | 证明边界 |
|---|---|---|
| `run_66b2fab16f23409184f089e007bbaa23` | `dsh_tool_failed`，`research_capability_mode_denied` | replay capability 被错误请求为 `mode=live` 时，网关 deny-by-default；说明权限错误没有被伪装成证据 |
| `run_f4dbcf557f9345a2ac6cc205f34e1340` | `failed / dsh_turn_error`，DSH JSONL 保留 `MISSING_CREDENTIAL` | 隔离启动遗漏官方 replay patch 时，DSH provider fail-closed；该失败保留用于启动配置回归 |
| `run_7e39f721253b44adb4f2cc479dae24a1` | `rejected / research_only`，1 次 MCP tool success，1 条 Evidence 因历史鲜度为 `stale` | 正确启动 `replay.patch.yml` 且使用 `mode=replay` 后，Hub -> DSH Web -> MCP -> JSONL -> callback -> Ledger 真实闭环；PIT/鲜度 Gate 阻止方向性 Forecast，`horizons=[]` |

本节只记录离线 replay/诊断证据，不代表实时网络检索、预测准确率、盈利或 DSH active Promotion。sidecar 的 `mode=replay` 是验收配置；live capability 仍须独立 owner gate。

## 4. 官方上游锁定事实

2026-08-31 只读核对：

```text
repository       https://github.com/deepseek-ai/deepseek-harness
source commit    0a53fb55bea101816fa226bb964ae2bed71c343b
source version   0.1.2-alpha.2
source tar sha   935574f69c8bb10b697cf8abe8c0449dab783e9f73dc3f224629458b6f65b980
npm CLI latest   @deepseek-ai/dsh@0.1.1-rc.2
license          MIT
```

`master` 的 Session Controller 已公开 `create/prompt/cancel/follow/control`，WebServer 已公开 route registration，Web 通过 `dsh.client` 动态加载 Client plugin。与此同时，npm 完整 CLI 尚停留在 `0.1.1-rc.2`，不能把 CLI rc2 与 alpha2 Session/Client packages 混装后宣称兼容。

因此 `NATIVE-00` 使用上述不可变 commit 的完整源码闭包完成构建和 contract baseline；不跟随浮动 `master`，也不把未通过测试的混装依赖作为生产路径。后续若官方发布一致的 `0.1.2` CLI/package set，可通过升级门替换源码构建。

## 5. 最终职责边界

| 责任 | DSH | Decision Hub |
|---|---|---|
| Chat/Session/Trajectory/Tool/Skill/Subagent | 唯一拥有 | 只保存引用和规范化业务投影 |
| Agent 内层工具循环 | 唯一拥有 | 不实现第二套 loop |
| 自动触发、Run、lease、恢复 | 不拥有业务真相 | 唯一拥有 |
| Evidence/PIT/freshness/authority | 提供候选结果 | 唯一校验和入账 |
| Publish Gate/active pointer | 无权限 | 唯一裁决 |
| Forecast/Outcome/Evaluation | 无权限 | 唯一事实源 |
| Agent 交互前端 | DSH Web | Client plugin 贡献业务节点 |
| 运维/账本/评测前端 | 提供跳转 | Decision Desk Admin |

## 6. 运行拓扑

```text
Browser
  -> pinned DSH Web
       -> Decision Hub Client Plugin
       -> native Session/Trajectory/Tool/Subagent UI

Hub Worker / Outbox
  -> authenticated Decision Hub Host Plugin route
       -> SessionController.create(deterministic session_id)
       -> SessionController.prompt(deterministic request_id)
       -> DSH Agent Loop + Hub research MCP
       -> agent/session events
  <- accepted/status/completion/failure/cancel
  -> Evidence Gateway / PIT / Gate / Ledger

Decision Desk Admin
  -> Operations / Sources / Capabilities / Ledger / Evaluation / Rollback
```

## 7. 公开契约

跨 Python/TypeScript 的 Host bridge 契约必须先加入 `contracts/schemas/` 并 codegen，禁止两边手写镜像。最低对象：

```text
DshUpstreamIdentity
  source_commit / source_version / package_versions / plugin_build_hash

DshHostReadiness
  ready / version_compatible / session_controller / client_plugin / hub_reachable

DshSessionSubmit
  run_id / request_hash / deterministic_session_id / deterministic_request_id
  workspace_ref / prompt_ref / agent_preset / permission_ref / deadline_at

DshSessionAccepted
  run_id / dsh_session_id / accepted_at / generation

DshSessionStatus
  admitted | running | idle | completed | failed | cancelled | unknown

DshSessionCompletion
  run_id / dsh_session_id / terminal_status / last_seq
  trace_ref / result_ref / result_hash / completed_at / error

DshRunSessionLinkView
  run_id / dsh_session_id / state / upstream_identity / timestamps
```

正式 HTTP 路径：

```text
DSH Host plugin
  GET  /decision-hub/v1/readiness
  PUT  /decision-hub/v1/runs/{run_id}
  GET  /decision-hub/v1/runs/{run_id}
  POST /decision-hub/v1/runs/{run_id}/cancel

Hub API callback/query
  PUT  /v1/dsh/sessions/{run_id}/accepted
  PUT  /v1/dsh/sessions/{run_id}/terminal
  GET  /v1/dsh/sessions/{run_id}
```

Hub -> DSH 与 DSH -> Hub 使用两个独立环境 secret；只记录配置是否存在和 key id/hash，不记录 secret。所有写请求包含 `request_hash`、幂等语义和 schema version。

## 8. 持久化

新增 Hub-owned `dsh_session_links`，只保存关联和协调状态：

```text
run_id unique
dsh_session_id unique
request_hash
state / generation
upstream_ref / plugin_build_hash
accepted_at / last_seen_at / terminal_at
last_seq / trace_ref / result_ref / result_hash
error_code / updated_at
```

不把完整 DSH JSONL 复制进 SQLite。三份状态继续分离：

- DSH JSONL：Agent 会话和轨迹；
- LangGraph checkpoint：外层生命周期恢复；
- Hub Ledger：业务事实和结果。

确定性的 `dsh_session_id` 和 `request_id` 由 `run_id + request_hash + contract_version` 生成，解决 create/accepted 回调之间崩溃导致的重复 Session。

## 9. 目标代码结构

```text
extensions/dsh/decision-hub/
  README.md
  package.json
  cordis.patch.yml
  src/host/
    index.ts
    bridge.ts
    auth.ts
    hub-client.ts
    session-correlation.ts
  src/client/
    index.tsx
    status/
    run/
    locales.ts
  tests/

infra/dsh/
  README.md
  upstream.lock.json
  fetch-upstream.sh
  build-upstream.sh
  run-web.sh
  verify-upstream.mjs

overlays/dsh/
  decision-hub-web.patch.yml

contracts/schemas/
  dsh_host_bridge.schema.yaml

packages/runtime_adapters/dsh_runtime/
  client.py                    # 现有 SDK，保留
  runtime.py                   # 现有 SDK，保留
  web_host_client.py           # 新 Host bridge client
  web_runtime.py               # 同一 ResearchHarnessRuntime port

apps/hub_api/                  # callback/query endpoints
apps/hub_worker/               # dispatch/reconcile composition
packages/kernel/               # link persistence/application service only
packages/query_views/          # 管理后台 session link view
migrations/versions/           # additive migration
tests/dsh_native/              # contract/integration/recovery
```

不将上游源码提交进业务目录。`infra/dsh` 根据 lock 文件下载到 ignored cache；可选 submodule 只能用于开发调试，不是实现前提。

## 10. 任务顺序

### NATIVE-00：上游构建与 Web 契约基线

- 写 `upstream.lock.json`、下载/hash 校验、构建和启动脚本；
- 从不可变 commit 构建官方 Web；
- 验证 Web、Session create/history/follow、Trajectory、JSONL 和 plugin discovery；
- 固化脱敏 build/handshake 证据。

退出门：官方 Web 能在隔离 `DSH_HOME` 启动，版本/hash 可读，未加载 Hub 插件时基础功能不被修改。

### NATIVE-01：canonical bridge contract 与持久关联

- 先写 schema/codegen/test；
- additive migration 和 Link service；
- Hub callback/query API；
- 重复 accepted/terminal、乱序回调、generation conflict 和历史升级测试。

退出门：完全不启动 DSH 也能用 fake callback 证明幂等、CAS、重启和单一关联。

### NATIVE-02：最小官方 Host Plugin

- 官方 `dsh.bundle` package；
- 注入 `webServer` 和 `sessionController`；
- readiness、submit/status/cancel route；
- 确定性 create/prompt；
- agent idle/error/session event -> Hub callback；
- auth、body limit、deadline、错误和日志脱敏。

退出门：Host contract tests 覆盖重复 submit、取消、丢回调重放、Hub 不可达和重启协调。

### NATIVE-03：最小官方 Client Plugin

- 官方 `dsh.client / platform=web`；
- 使用 DSH slot/Conversation Node/Settings surface；
- 显示 Hub readiness、Run 状态、模式、Evidence/Gate 摘要和管理后台链接；
- 不复制 Session/Trajectory，不显示 raw JSON；
- 中文/英文 locale、空态、错误态和响应式测试。

退出门：Client bundle 可由官方 Web 动态加载；插件失败时 DSH boot page 明确报错，不白屏；`decision-research` preset 能被官方 roster 发现并在新 Session 物化。

### NATIVE-04：Hub dispatch/reconcile 与 replay E2E

- Web runtime adapter 实现现有 `ResearchHarnessRuntime` port；
- worker/outbox dispatch、status reconciliation、cancel 和 terminal result；
- 一个 replay Run 从 Hub 创建普通 DSH Session；
- DSH Web 与 Decision Desk 显示同一关联，Hub 只 commit 一次；
- 重启 DSH/Hub worker 后完成恢复演练。

退出门：浏览器截图、API 摘要、DSH Session/Trajectory/JSONL、Hub Ledger 和 single-commit 证据同时存在；固定 replay 至少覆盖成功、部分 capability 失败、低权威/关键事实不足三种结果，且三种结果都能在 DSH 原生轨迹与 Hub 规范化视图中解释。

### NATIVE-05：升级、回滚和本地运行

- Compose/本机运行文档；
- version mismatch fail-closed；
- 新旧 upstream replay/contract smoke；
- rollback 到已固定 DSH 版本；
- Decision Desk Research 入口明确指向 DSH Web，不删除管理功能。

退出门：一条命令启动核心服务，一条命令执行离线验收，一条命令回滚上游 pin。

### NATIVE-06：真实价值 Gate，不属于离线完成声明

完成 NATIVE-00 至 NATIVE-05 后，另行执行只读、限时、计费可见的真实 Search/Official/Market canary 和 7-14 天观察。没有真实证据时不能标记 `product_ready` 或 Promotion。

## 11. BDD 验收场景

### 场景 A：复用官方工作台

```text
Given 固定版本的官方 DSH Web 和已安装的 Decision Hub bundle
When owner 打开 DSH Web
Then 页面保留官方 Session/Trajectory/Tool/Subagent/JSONL 功能
And Decision Hub 只贡献 readiness、Run 和业务结果节点
And Decision Desk 不再充当复制的 Agent 主界面
```

### 场景 B：重复提交只创建一个 Session

```text
Given Hub 已 durable admission 一个 run_id 和 request_hash
When outbox 因超时重复提交三次
Then DSH 只存在一个确定性 dsh_session_id
And Hub 只保存一个 active link
And 最终只 commit 一份 Artifact/Evidence/Outbox
```

### 场景 C：回调丢失后恢复

```text
Given DSH Session 已完成但 terminal callback 丢失
When Hub worker 或 DSH Host 重启并执行 reconciliation
Then 系统从 status/session history 恢复终态
And 不重新提示模型、不重复 commit
```

### 场景 D：版本或插件不兼容

```text
Given DSH upstream 与 Decision Hub plugin contract 不匹配
When readiness 执行
Then dsh_web runtime fail-closed 并显示 version_incompatible
And SDK/Fixed/replay 仍可显式使用
And active pointer 和历史账本不改变
```

### 场景 E：两个前端没有重复所有权

```text
Given 一个已完成的 replay Run
When owner 打开对应 DSH Session 和 Decision Desk
Then DSH Web 展示完整 Agent 轨迹
And Decision Desk 展示 PIT/Gate/Ledger/Evaluation
And 两端以 run_id/dsh_session_id 互相跳转
And 任一端都不展示伪造 demo 数据或 raw JSON dump
```

### 场景 F：研究结果不足时继续补证，而不是立即结束

```text
Given replay Run 的 hard Evidence requirement 未覆盖
When DSH Agent Loop 收到 Evidence Gap
Then 通过允许的 Research MCP capability 继续下一轮查询
And 每一轮都有 bounded round/deadline/attempt 记录
And 达到 no-progress、deadline 或预算上限时才停止
And Hub Gate 将结果标为 research_only/no_trade，不生成方向性发布
```

### 场景 G：失败来源和部分成功可见

```text
Given 多个 capability 并行执行且其中一个超时或权限拒绝
When 研究轮次完成
Then 其他成功结果仍保留并带有 source/authority/PIT lineage
And 失败项带 error_code/origin/retryable/cause_code
And DSH 轨迹、Hub API 和前端显示同一失败事实
And 不把失败伪装成 provider_timeout 或完整成功
```

### 场景 H：执行终态与业务裁决分离

```text
Given 官方 DSH Session 已 completed
When Hub Gate 为 publish、research_only 或 reject 中任一状态
Then Client Plugin 同时显示 DSH 执行态与 Hub 业务 Gate
And completed + reject 不得显示成业务成功
And Gate 只来自 Hub canonical Query View，不由 Host、Client 或模型文本推断
```

本场景的独立实现卡为
[`DSH 原生工作台业务裁决投影实施卡`](DSH_NATIVE_CORE_BUSINESS_STATUS_PROJECTION.md)。

## 12. TDD 与测试矩阵

| 层 | 必测行为 | 工具 |
|---|---|---|
| Contract | Python/TS schema 一致、未知字段拒绝、版本不匹配 | codegen/Pydantic/Zod/Vitest |
| Link service | 幂等、CAS、乱序、终态不可逆、重复 callback | pytest + SQLite |
| Host plugin | auth、body limit、create/prompt/cancel、事件回调 | Vitest + fake Cordis services |
| Client plugin | load、readiness、空/错/运行/终态、locale | Vitest/jsdom |
| Upstream | pin/hash/build/Web/profile/client discovery | scripts + official smoke |
| Worker | dispatch/reconcile/restart/single commit | pytest integration/subprocess |
| Browser | DSH Web 原生 UI、插件节点、跳转、无白屏/重叠 | Playwright screenshot/console |
| Regression | 现有 318 Python、Decision Desk、contracts、Ruff/Pyright | 全量离线门 |

所有网络 Provider 测试默认关闭。上游源码下载和 npm 安装属于依赖构建，不等于 live 市场 canary。

## 13. 允许修改路径

```text
extensions/dsh/**
infra/dsh/**
overlays/dsh/**
contracts/**
packages/contracts_py/**
packages/contracts_ts/**
packages/runtime_adapters/dsh_runtime/**
packages/kernel/**              # 仅 link persistence/application/port
packages/query_views/**
apps/hub_api/**
apps/hub_worker/**
apps/decision-desk/**           # 仅 admin link/status 收敛
migrations/versions/**
tests/dsh_native/**
相关 README/docs/status/changelog
```

## 14. 禁止修改和非目标

- 不复制或 fork DSH Web 组件到 Decision Desk；
- 不 import 上游 `src/*` 私有模块作为产品契约；
- 不创建第二套 Agent Loop、Session、Trajectory、插件安装器或业务账本；
- 不删除/改写历史 migration、Run、Evidence、Artifact、Forecast、Outcome；
- 不切 active pointer，不自动 Promotion；
- 不安装未经审计社区插件；
- 不扩大 live network、Provider secret 或自动交易权限；
- 不实现 ASR、PPT、多用户或第二领域；
- 不以 replay E2E 宣称实时事实、预测准确率或盈利。

## 15. 停止条件

出现以下任一情况必须停止当前实现并回到 ADR/owner：

1. 必须长期修改 DSH 上游源码才能加载插件或创建 Session。
2. 必须依赖未导出的私有 API，且没有稳定公开 seam。
3. 上游源码闭包不能在固定 commit 可重复构建。
4. Host bridge 需要成为第二个 durable queue/账本才能工作。
5. 需要扩大网络、secret、shell/filesystem 或交易权限。
6. 同一架构缺陷连续两次局部补丁仍失败。
7. 实现要求迁移或删除现有历史数据。

## 16. 完整自测门

阶段完成至少运行：

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

浏览器验收必须检查桌面和移动视口、控制台错误、DSH 原生 Session/Trajectory 非空、Decision Hub plugin 可见、两个前端无职责重复。未实际执行的命令不能写成 passed。

## 17. 阶段完成定义

`DSH-NATIVE-CORE complete` 只表示：官方 DSH Web、原生插件、durable bridge、replay E2E、恢复、升级和离线回归全部通过。它不表示 DSH 已 Promotion，不表示实时市场数据已经稳定，也不表示产品能够盈利。

下一阶段只有两个允许结论：

- 进入 `NATIVE-06` 真实价值 Gate；
- 因官方 seam、可靠性或维护成本不达标而 `retain_sdk_or_stop`。
