# DSH 与 Decision Hub 系统总装设计

日期：2026-08-31
状态：`accepted`（owner 于 2026-08-31 授权按该边界建立阶段目标、实现并自测）
用途：只解释 DSH、Decision Hub 外层、领域插件、两个前端和代码目录如何组合。产品范围、历史复盘和阶段验收分别引用其他文档，不在本文追加讨论记录。

关联决策：

- [ADR-0012 DSH-first 产品重新收口](../decisions/ADR-0012-dsh-first-product-rebaseline.md)
- [ADR-0013 DSH Web 原生插件与上游升级集成](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)
- [DSH-first 产品实现总方案](DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)

## 1. 一句话结论

整个产品不是“DSH 外面再造一个 Agent”，而是：

```text
DSH
  = Agent 执行器和交互工作台

Decision Hub
  = 自动触发、可信边界、业务账本和结果评测

crypto_macro
  = 第一个可插拔领域包
```

DSH 负责把研究做出来；Decision Hub 负责决定为什么启动、事实是否可信、结果能否发布、后来是否正确。外层禁止重新实现 DSH 已经拥有的 Chat、Session、Agent Loop、Tool Loop、Subagent、Skill、Trajectory 和 JSONL。

## 2. 为什么不能只剩 DSH

如果需求只是“人工打开页面，和 Agent 聊天并查看工具轨迹”，直接使用 DSH 就够了，不需要 Decision Hub 外层。

本产品保留外层，是因为还要求以下 DSH 会话之外的产品能力：

1. 没有人提问时，也能根据日历、新闻、来源状态或定时规则创建任务。
2. 进程重启、重复事件和网络失败后，任务不会丢失或重复发布。
3. 每个事实都要记录来源、三时间戳、PIT、hash、权威性和冲突状态。
4. Agent 只能提出候选，确定性 Gate 决定 `publish`、`research_only` 或 `reject`。
5. Forecast 到期后要保存 Outcome、误差和人工反馈，形成可评测资产。
6. 模型、Pack、插件或 DSH 版本升级前，要 replay/holdout/shadow、人工 Promotion 和可回滚。

这些能力不是第二套 Agent，而是产品控制面。如果以后确认不需要这些价值，正确做法是删减 Hub，而不是继续扩写外层。

## 3. 三层系统

### 3.1 DSH：Agent 执行层

DSH 拥有：

- 官方 Web、Chat、Session history 和 JSONL export；
- Session 内的 model -> tool -> result -> next action 循环；
- Supervisor、Subagent、Skill、MCP、上下文压缩和 Trajectory；
- DSH 官方插件的安装、启用、生命周期和 UI；
- Agent 当前正在做什么的实时展示。

DSH 不拥有：

- Hub Event、Evidence、PIT Snapshot、Forecast、Outcome 和 Evaluation；
- 产品 Run 的最终业务状态；
- Publish Gate、active pointer、Promotion 或自动交易权限；
- Decision Hub 的 SQLite 业务账本。

### 3.2 Decision Hub：产品控制与资产层

Decision Hub 外层只保留四类职责：

```text
A. Trigger + Durable Run
   自动发现、定时、入队、幂等、lease、恢复、取消、重试

B. Trust Boundary
   Evidence Gateway、PIT、来源、时间、hash、冲突、Sufficiency/Publish Gate

C. Product Ledger
   Event、Run、Artifact、Forecast、Outcome、Evaluation、Outbox、版本

D. Operations
   来源健康、能力权限、成本、错误、Promotion、Rollback、Backup
```

外层不允许加入通用 Agent Loop、角色调度器、聊天历史、工具轨迹 UI 或 DSH 插件安装器。

### 3.3 Domain Pack：业务能力层

当前唯一真实领域是 `packs/crypto_macro`：

```text
crypto_macro
  doctrine/      # 根因链和领域方法
  evidence/      # 最低事实要求、鲜度和来源优先级
  profiles/      # Manager/角色允许的能力和预算
  tools/         # capability binding，不实现 DSH 生命周期
  gates/         # 金融领域发布规则
  evaluations/   # replay/holdout/结果指标
  fixtures/      # 固定失败和回归样本
```

未来 PPT、A 股或美股不会改写 DSH，也不会把字段塞进 `crypto_macro`。它们以独立 Product Extension/Domain Pack 接入；只有第二个真实调用方出现后，才提取两个领域确实共享的接口。

## 4. 完整系统拓扑

```text
                         +------------------------------+
                         |          DSH Web             |
                         | Chat / Session / Trajectory  |
                         | Tool / Skill / Subagent      |
                         | Decision Hub Client Plugin   |
                         +---------------+--------------+
                                         |
                                         | DSH native plugin protocol
                                         v
+------------------+       +-------------+--------------+
| Source Adapters  |       | Decision Hub DSH Host      |
| manual/news/feed |       | Plugin / Session Bridge    |
| calendar/ASR (*) |       +-------------+--------------+
+--------+---------+                     |
         | TextEnvelope                  | create/resume/cancel/callback
         v                               v
+--------+------------------------------------------------+
|                  Decision Hub                          |
| Hub API -> Durable Run/Outbox -> Hub Worker/LangGraph  |
|                     |                                  |
|                     v                                  |
| Evidence Gateway -> PIT -> Gate -> Ledger -> Outcome   |
+----------+---------------------------+-----------------+
           |                           |
           | MCP capability call       | Query/View
           v                           v
+----------+-------------+   +---------+------------------+
| Research MCP Gateway   |   | Decision Desk Admin       |
| Search/Official/Market |   | Ops/Ledger/Eval/Promotion |
+----------+-------------+   +----------------------------+
           |
           v
 Provider adapters / DSH official plugins / external APIs

(*) ASR 以后只负责把音频转换成 TextEnvelope，不进入研究主链。
```

## 5. 两种入口，不混为一种

### 5.1 普通 DSH 会话

```text
用户打开 DSH Web
  -> 普通聊天或临时使用 DSH 插件
  -> Session/Trajectory/JSONL 只保存在 DSH
```

这适合探索和人工研究。除非用户显式执行“创建正式 Decision Run”，普通会话结果不会自动进入 Hub Evidence、Gate 或 Forecast。

### 5.2 正式产品任务

```text
人工提交 / 日历 / 新闻 / 定时扫描
  -> Hub admission，生成 event_id + run_id
  -> Hub outbox 幂等提交到 DSH Host plugin
  -> Host plugin 创建或恢复 dsh_session_id
  -> DSH Agent 在预算内调用 Tool/Skill/MCP/Subagent
  -> Research MCP 把正式事实请求送入 Capability Gateway
  -> DSH 根据成功、失败和证据缺口继续规划
  -> Host plugin 回报完成、失败或取消
  -> Hub 冻结 Decision Snapshot
  -> 确定性 Gate
  -> Artifact/Forecast/Outbox/Outcome/Evaluation
  -> DSH Web 显示 Agent 轨迹，Decision Desk 显示业务结果
```

正式任务的关键不是多一层 Prompt，而是有 durable identity、可信事实、Gate 和后验结果。

## 6. DSH 内层循环与 Hub 外层流程

### 6.1 DSH 内层是 Agent Loop

```text
读取目标和当前 evidence gaps
  -> 选择工具、Skill 或 Subagent
  -> 读取结果
  -> 比较来源和冲突
  -> 继续补证或输出候选报告
```

这部分必须复用 DSH，不在 LangGraph 中再写一个 Supervisor/Reviewer/Judge Agent 系统。

### 6.2 Hub 外层是产品生命周期

```text
admitted
  -> dispatched
  -> running
  -> completed | failed | cancelled
  -> evidence_attested
  -> gate_evaluated
  -> committed
  -> outcome_due
  -> evaluated
```

LangGraph 只负责这条流程的 checkpoint、恢复、Evidence Round 边界和 commit 路由。它不能选择具体网页、扮演金融角色或重新调度 DSH 内部 Tool/Subagent。

现有 `agentic_research_graph.py` 在迁移期保留，用于当前 SDK candidate、回放和恢复。常驻 DSH Web Host 通过验收后，它必须收敛为生命周期图；不得继续扩展成第二套 Agent Harness。

## 7. 两个前端分别显示什么

### 7.1 DSH Web：日常 Agent 主界面

直接复用上游：

- Chat、Session 列表、历史和 JSONL；
- Plan、Tool、Skill、Subagent 和 Trajectory；
- DSH 插件清单和设置。

Decision Hub Client Plugin 只增加：

- 当前 `run_id`、状态和对应 `dsh_session_id`；
- Evidence、Gap、Gate、30m/24h/72h 和 Report 节点；
- 提交、取消、重试、复查、反馈和打开管理后台；
- Replay/Live、Candidate/Active 的醒目标识。

### 7.2 Decision Desk：产品管理后台

保留现有 `apps/decision-desk`，但不继续复刻 DSH：

- Operations、worker、queue 和 readiness；
- Sources、Capability 权限、Provider 和错误；
- Ledger、PIT、Evidence lineage 和 Run Inspector；
- Dataset、Evaluation、Experience 和 Outcome；
- Promotion、Rollback、Backup 和通知。

现有 Research Command Center 在迁移期保留；DSH Web 原生路径通过验收后，它应收敛为只读业务报告/管理入口或跳转到对应 DSH Session，而不是继续增加聊天和 Trajectory 功能。

## 8. DSH 官方插件与 CapabilityManifest

它们不是两套插件系统：

```text
DSH official plugin
  = 安装和运行单元
  = Tool / Skill / MCP / Subagent / Hook / Client UI

CapabilityManifest
  = Decision Hub 对正式证据的准入记录
  = permissions / PIT / freshness / cost / timeout / audit / rollback
```

规则：

1. DSH-only UI、临时 Skill 和人工工具只由 DSH 管理，不需要 Hub manifest。
2. 插件结果要影响正式 Evidence/Gate 时，才需要 CapabilityManifest。
3. 官方插件已经输出 canonical schema 时，只加通用 binding。
4. 只有领域数据结构不同，才加薄 result mapper。
5. 不复制官方插件逻辑，不复制 DSH Agent Loop，不实现第二个插件安装器。
6. 任何插件都不能直接写 Hub Ledger、Gate 或 active pointer。

## 9. 当前代码与目标代码

### 9.1 当前已经存在

```text
apps/
  hub_api/                    # REST、Query/View、命令；不执行长 Agent 任务
  hub_worker/                 # realtime/research/evolution durable worker
  research_mcp/               # DSH 到正式 capability gateway 的 MCP 入口
  hub_mcp/                    # DSH/Codex 人工工作台入口
  decision-desk/              # 当前前端，目标收敛为管理后台

packages/
  kernel/                     # Event/Evidence/PIT/Gate/Ledger/Outcome
  orchestration/langgraph/    # 外层生命周期、checkpoint、恢复
  runtime_adapters/dsh_runtime/ # 当前 Python SDK DSH candidate
  source_adapters/            # manual/feed/transcript -> TextEnvelope
  provider_adapters/          # Search/Official/Market/Notification
  workbench_adapters/         # 工作台发现、准入和公开 Port
  query_views/                # 前端人可读 DTO
  evals/                      # replay/holdout/shadow/Promotion 证据

contracts/                    # canonical schema 单一来源 + codegen
packs/crypto_macro/           # 第一个领域包
```

当前 DSH 调用方式是：

```text
hub_worker
  -> packages/runtime_adapters/dsh_runtime/runtime.py
  -> client.py
  -> deepseek_harness.DeepSeekHarness
  -> sdk profile 子进程
  -> handle.run(prompt, session_id)
```

它复用了 DSH Agent Loop、Tool、Subagent、Session 和 JSONL，但没有运行上游 DSH Web，所以当前页面看不到官方 Session/Trajectory。

### 9.2 目标新增目录

```text
extensions/
  dsh/
    decision-hub/
      README.md
      package.json             # 官方 dsh.bundle + dsh.client 声明
      cordis.patch.yml         # 挂载 Host/Client plugin
      src/
        host/
          index.ts             # Cordis Host plugin 入口
          hub-api-client.ts    # 只调用 Hub 公开 API/MCP
          run-bridge.ts        # submit/status/cancel/completion
          session-map.ts       # run_id <-> dsh_session_id
        client/
          index.tsx            # DSH Client plugin 入口
          nodes/               # Run/Evidence/Gap/Gate/Horizon/Report
          settings/            # Hub readiness 和管理后台链接
      tests/                   # Host contract + Client load/render

overlays/
  dsh/
    profiles/                  # Decision Hub web profile/allowlist
    patches/                   # 仅经 ADR 允许的临时上游 patch

infra/
  dsh/
    README.md
    compose/                   # DSH Web 服务组合
    scripts/                   # pin、build、handshake、upgrade、rollback

vendor/
  deepseek-harness/            # 可选只读 Git submodule；默认可不存在
```

现有 Python 侧增加目标，不另建新框架：

```text
packages/runtime_adapters/dsh_runtime/
  client.py                    # 保留当前 SDK canary/fallback
  runtime.py                   # 保留当前 SDK ResearchHarnessRuntime
  web_host_client.py           # 计划：调用 Host bridge 公开契约
  web_runtime.py               # 计划：同一 ResearchHarnessRuntime 的 Web 实现
  host_contract.py             # 计划：typed submit/status/cancel/result
```

最终文件名须在 `NATIVE-00` 固定官方 DSH 版本并验证真实 exports 后锁定，不能根据猜测 import 上游私有模块。

## 10. 每个现有模块为什么保留

| 模块 | 保留原因 | 禁止继续做什么 |
|---|---|---|
| `hub_api` | DSH、后台和自动来源共用稳定命令/查询协议 | 不执行 Agent，不写前端专用业务分支 |
| `hub_worker` | 无人值守任务、lease、恢复、到期复查 | 不实现模型工具循环 |
| `kernel` | PIT、Gate、Ledger、Outcome 是长期产品资产 | 不 import DSH/LangGraph/Provider |
| `langgraph` | durable checkpoint 和恢复 | 不实现第二套 Supervisor/Tool Loop |
| `research_mcp` | 正式事实的权限和 schema 边界 | 不变成插件市场或 Agent |
| `provider_adapters` | 精确官方/市场数据和可替换 API | 不在 graph/Prompt 中硬编码调用 |
| `dsh_runtime` | 当前可工作的 SDK canary、replay 对照和 fallback | 不复刻 DSH 内部状态机 |
| `query_views` | 两个前端读取同一业务事实 | 不暴露 raw SQL/Graph/DSH JSON |
| `evals` | 判断版本是否真的更好 | 不自动 Promotion 或修改生产配置 |
| `decision-desk` | 运维、账本、评测和回滚 | 不复制 DSH Chat/Trajectory/Plugin UI |

## 11. 三份状态数据不能混

```text
DSH Session JSONL
  -> 模型上下文、turn、tool、subagent、compaction、trajectory

LangGraph checkpoint
  -> 产品 Run 当前执行到哪个生命周期节点，供崩溃恢复

Decision Hub Ledger
  -> Event、Evidence、PIT、Artifact、Forecast、Outcome、Evaluation
```

关联字段固定为：

```text
event_id
run_id
dsh_session_id
trace_ref + trace_hash
snapshot_id
artifact_id
```

Hub 不复制完整 DSH JSONL；DSH JSONL 也不能替代业务账本。删除一个 DSH Session 不能删除历史 Forecast/Outcome，回滚 Hub candidate 也不能静默改写 Session 历史。

## 12. 上游 DSH 如何复用和升级

默认不复制源码，不长期 fork：

```text
固定 DSH tag/commit/package/image digest
  + extensions/dsh/decision-hub
  + overlays/dsh
```

只有需要构建或调试上游时，才把 DSH 作为只读 Git submodule 放到 `vendor/deepseek-harness`。业务代码不能 import `vendor` 私有路径；产品修改不能直接写进 submodule。

DSH 当前仍快速演进，因此不能承诺零适配升级。必须保证的是：

- 旧固定版本随时可运行和回滚；
- Hub Ledger、Pack、Gate 和前端管理数据不随 DSH 升级迁移；
- Host bridge 和 Client plugin 有独立 contract/smoke tests；
- 新版本未通过 Session/Trajectory/Tool/Subagent/JSONL/Hub replay 门时不 Promotion。

## 13. 迁移时哪些代码不动、哪些收敛

第一阶段不删除现有代码：

- 历史 migration、Run、Evidence、Artifact、Forecast、Outcome 全部保留；
- Python SDK DSH adapter 保留为 canary/fallback；
- Fixed/replay 保留为确定性测试和比较基线；
- Decision Desk 保留管理页面；
- 当前 LangGraph research graph 保留到 Web Host bridge 完成 shadow 对照。

只有满足以下证据后才收敛旧路径：

1. 官方 DSH Web 能稳定创建、恢复和展示 Hub 关联 Session。
2. Host bridge 重复提交、丢回调、重启和取消测试通过。
3. 同一 replay/live case 的 Evidence、Gate 和账本结果不回归。
4. DSH Web 已覆盖日常 Agent 轨迹，不再需要 Decision Desk 复制展示。
5. owner 明确 Promotion。

收敛也不是删除业务资产，而是停止扩展旧 Research UI 和 SDK-only candidate，把默认入口切到经过验证的 DSH Web。

## 14. 下一阶段唯一实现顺序（历史编号映射）

本节保留总装文档最初的编号，避免删除历史决策；它不是当前阶段的独立任务编号，已被当前 Stage Charter supersede。当前唯一有效的编号和退出门以 [DSH Native Web Product Core Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 的 `NATIVE-00` 至 `NATIVE-06` 以及 [完成实施方案](../stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md) 的 `NC-01..NC-07` 为准。旧编号 `NATIVE-01` 至 `NATIVE-06` 不能用于创建新任务。

本文确认后才创建 Stage Charter，顺序不能交换：

```text
NATIVE-00
  固定 DSH 上游版本
  启动官方 Web
  验证 Session/Trajectory/Tool/Subagent/JSONL/plugin exports

NATIVE-01
  最小官方 Host + Client plugin
  只显示 readiness、只读 Run node 和 session correlation

NATIVE-02
  durable submit/status/cancel/completion bridge
  fake/replay 下验证幂等、重启、丢回调和恢复

NATIVE-03
  一个真实事件和最小 Search/Official/Market allowlist
  比较直接 DSH、SDK candidate 和 Web Host path

NATIVE-04
  Evidence/PIT/Gate/Ledger 与两个前端端到端

NATIVE-05/06
  7-14 天观察
  promote_dsh | retain_fixed | stop_product
```

在 `NATIVE-00` 至 `NATIVE-04` 通过前，不写真实市场插件、不扩 LangGraph、不继续改 Research Command Center。

## 15. 最终边界检查

以后任何设计先回答以下问题：

1. 这是 Session/Agent/Tool/Trajectory 能力吗？是则优先放 DSH 官方插件。
2. 这是触发、幂等、恢复、PIT、Gate、账本或 Outcome 吗？是则放 Decision Hub。
3. 这是金融事实要求、根因链或策略吗？是则放 `packs/crypto_macro`。
4. 这是前端 Agent 交互吗？放 DSH Client Plugin。
5. 这是运维、评测或版本管理吗？放 Decision Desk Admin。
6. 是否在重复 DSH 已有功能？是则停止实现并补 ADR。
7. 是否只有一个想象中的未来调用方？是则不提前抽象平台接口。

## 16. Owner Gate

本文与 ADR-0013 已于 2026-08-31 获 owner 接受。实现只能按 [DSH Native Web Product Core Stage Charter](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 执行；阶段 Gate 继续要求：

- 不废弃当前 SDK、Fixed/replay 或 Decision Desk；
- 不切 active pointer；
- 不执行新的 live 市场 canary。

`DSH-NATIVE-CORE` 的 NATIVE-00 至 NATIVE-05 已完成；当前唯一执行目标已转为获授权的 `PRODUCT-CLOSEOUT-01` C1-C7。NATIVE-06/实时价值仍需独立证据；C4 仅允许一次隔离、只读、限时 capability canary，不能由离线实现或 canary 自动切 active pointer。
