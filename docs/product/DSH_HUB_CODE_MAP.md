# DSH 与 Decision Hub 代码地图

日期：2026-08-31
状态：accepted reference

本文是目录和调用方向导航，不替代 ADR、canonical schema 或阶段验收。它专门回答 DSH 本体、Decision Hub 外层、插件和两个前端分别在哪里、谁拥有哪份状态。

## 1. 一句话边界

    DSH Web/Harness       = Agent 执行、Session、Trajectory、Tool、Skill、Subagent、JSONL 和交互工作台
    Decision Hub          = 触发、Durable Run、PIT/Evidence、确定性 Gate、Ledger、Outcome/Evaluation 和运维
    Domain Pack           = crypto_macro 等业务方法、事实要求、角色配置和领域 Gate
    Decision Hub plugin   = 只把 Hub 能力接入 DSH 官方扩展面，不复制 DSH

Hub 不是第二个 Agent，也不是 DSH Web 的替代品。DSH 内层 loop 由官方 Harness 执行；Hub 外层只管理产品生命周期和业务事实。

## 2. 当前运行态与目标运行态

| 区域 | 当前实际状态 | 目标状态 |
|---|---|---|
| DSH Web | 官方上游源码已锁在 .cache/dsh-upstream/source；固定版本官方 Web 与 Hub plugin 已通过三场景 replay/recovery 验收；页面仍按 replay/live 明确区分 | 由 infra/dsh 固定版本构建官方 Web，并加载 Hub Host/Client plugin |
| DSH Python | packages/runtime_adapters/dsh_runtime 已有 SDK、Session/Tool/Subagent/Trace 适配器，仍是 candidate/shadow | 作为 SDK fallback/canary；Web Host path 通过同一 ResearchHarnessRuntime port 接入 |
| Host Plugin | `extensions/dsh/decision-hub` 已通过官方 Web readiness、submit/status/cancel、callback 幂等、重启/丢回调和版本 fail-closed 验收 | dsh.bundle Host 入口注入公开 webServer、sessionController 和事件监听 |
| Client Plugin | `extensions/dsh/decision-hub` 已通过 dsh.client 动态加载、状态投影、失败空态和官方 Web 三场景验收 | dsh.client、platform: web，只渲染 readiness、Run、Evidence/Gate 摘要和跳转 |
| Decision Desk | apps/decision-desk 是当前可见的 Hub 管理/研究页面 | 收敛为 Operations、Ledger、Evidence、Evaluation、Promotion/Rollback 管理后台 |

当前页面和 replay worker 已证明官方 DSH Web 加载插件并完成离线链路验收，不能证明真实搜索稳定、预测准确或盈利。

## 3. 代码目录所有权

    apps/
      hub_api/                  # REST command/query/callback；不执行长 Agent 任务
      hub_worker/               # realtime/research/evolution durable worker；不实现 Tool loop
      research_mcp/             # DSH -> Hub Capability Gateway 的唯一正式研究工具入口
      decision-desk/            # Hub 管理后台；不复制 DSH Chat/Session/Trajectory

    packages/
      kernel/                   # Event/Observation/Snapshot/Run/Evidence/Gate/Ledger/Outcome
      orchestration/langgraph/  # Hub 生命周期、checkpoint、恢复、Evidence Round 边界
      runtime_adapters/dsh_runtime/ # DSH Python SDK candidate 和未来 Web adapter
      source_adapters/          # manual/feed/transcript/未来 ASR -> TextEnvelope
      provider_adapters/        # Search/Official/Market 等 Capability 实现
      query_views/              # 面向两个前端的规范化 DTO
      evals/                    # replay/holdout/shadow/Outcome/Promotion 证据

    contracts/
      schemas/                  # 跨语言 canonical schema 单一来源
      generated-manifest.yaml   # codegen 结果清单，禁止手改镜像

    packs/crypto_macro/         # 首个领域包：doctrine/evidence/profile/tools/gates/evals
    extensions/dsh/decision-hub/ # 已实现并通过工程验收的官方 dsh.bundle + dsh.client 薄插件
    infra/dsh/                  # upstream lock、构建、启动、升级、回滚和验收脚本
    overlays/dsh/               # 仅允许的 profile/patch 层，不存放业务逻辑

## 4. 调用方向

    Source Adapter -> Hub admission/PIT -> durable Run
      -> Hub worker/LangGraph lifecycle
      -> DSH Host Plugin -> DSH SessionController
      -> DSH Agent loop -> DSH Tool/Skill/Subagent/MCP
      -> Hub Capability Gateway -> Evidence/PIT validation
      -> deterministic Gate -> Hub Ledger/Outcome/Evaluation
      -> DSH Client Plugin (交互投影) + Decision Desk (管理投影) + outbox

允许的反向调用只有：Hub worker 通过 Host bridge 提交/查询/取消 DSH Session，以及 DSH Host 将规范化状态回调 Hub。插件不能直接 import Kernel 私有实现或写 SQLite。

## 5. 三份状态，三份真相

| 状态 | 所有者 | 保存什么 | 不能替代什么 |
|---|---|---|---|
| DSH Session JSONL | DSH | Agent 对话、工具轨迹、Session history | 不能替代 Hub Run/Evidence/Gate |
| LangGraph checkpoint | Hub Orchestration | 生命周期恢复位置、外层 round 边界 | 不能替代业务账本 |
| Hub Ledger SQLite | Kernel | Event、Snapshot、Run、Evidence、Artifact、Forecast、Outcome、Evaluation | 不能复制完整 DSH raw JSON |

dsh_session_links 只保存 run_id 与 dsh_session_id、generation、状态、序号和结果引用；历史 migration、Run、Evidence、Artifact、Forecast、Outcome 不改写。

## 6. 插件边界

DSH 官方插件是安装/运行单元；CapabilityManifest 是 Hub 对可进入正式 Evidence/Gate 的能力的审计记录。两者不是两套 Agent 系统：

1. DSH-only Skill、工具和 UI 由 DSH 管理，不进入 Hub Ledger。
2. 会影响正式事实的插件必须经过权限、来源、PIT、鲜度、成本和回滚审计。
3. Host plugin 只使用官方公开 seam：webServer.register/registerFallback/registerUpgrade、sessionController.create/prompt/cancel/follow/control 和 ctx.on(session/event)、ctx.on(agent/status)。
4. Client plugin 只通过 slots、Conversation Node 和 Settings surface 注入，不复制 DSH Session/Trajectory 页面。
5. 任何插件不能拥有 Gate、active pointer、自动交易或业务账本写权限。

## 7. 扩展和替换规则

- 新领域先建独立 Product Extension/Domain Pack；不把金融字段塞进 Platform Core。
- 只有第二个真实领域证明确有相同调用方，才提取共享接口。
- 替换 DSH/Pi/Codex 时只替换 runtime_adapters 或官方插件 adapter；Kernel、canonical schema、PIT、Gate、Ledger 和评测不随 Harness 重写。
- 上游升级必须使用固定 commit/package 闭包，通过 Web/Session/JSONL/Plugin contract、Hub replay、回滚和 owner review；不能承诺 alpha 版本零适配替换。
- ASR 未来只实现 AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope，不进入决策核心。

## 8. 历史阶段映射与当前入口

以下是已完成的 DSH Native Web Product Core 历史映射：

    NATIVE-00  固定上游 Web 构建和 contract baseline       verified
    NATIVE-01  canonical bridge、link persistence、callback  verified
    NATIVE-02  最小官方 Host Plugin                      verified
    NATIVE-03  最小官方 Client Plugin                    verified
    NATIVE-04  durable dispatch/reconcile + replay E2E    verified
    NATIVE-05  单一启动、升级、回滚和浏览器退出门          verified
    NATIVE-06  真实 Search/Market 价值 Gate               独立授权

NATIVE-00 至 NATIVE-05 的工程退出门已经有可复核证据；这仍不宣称实时 Search、预测准确率或盈利。当前唯一顶层执行入口是 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)，原 NATIVE-06 的真实能力验收由 `PRODUCT-CLOSEOUT-01/C4` 承接，不能把 replay 作为 live 入口。
