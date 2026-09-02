# DSH 与 Decision Hub 边界和代码地图

日期：2026-08-31
状态：accepted orientation；实现授权仍以 DSH-NATIVE-CORE Stage Charter 为准
用途：解释为什么同时存在 DSH 和 Decision Hub、两者如何组合、当前代码在哪里、后续代码应落在哪里。本文只做边界导航，不复制 DSH 源码，不替代 ADR、canonical schema 或 Stage Charter。

权威入口：

- DSH 与 Decision Hub 系统总装设计：完整拓扑、迁移和升级策略。
- DSH Native Web Product Core Stage Charter：当前唯一获授权的实现阶段和验收门。
- ADR-0012：DSH-first 产品边界。
- ADR-0013：官方 DSH Web 与原生插件集成策略。

## 1. 一句话结论

    DSH = Agent 的执行器和日常工作台
    Decision Hub = 产品的控制面、可信边界和长期资产库
    Domain Pack = 某个业务领域的方法、事实要求和发布规则

这不是两个 Agent 叠在一起，也不是 Decision Hub 再造一个 DSH。DSH 负责会话内的观察、工具调用、Skill/Subagent、继续推理和轨迹展示；Hub 负责会话外的事件、耐久任务、事实可信度、确定性 Gate、结果账本和后验评测。

如果只需要人工聊天和查看工具轨迹，直接使用 DSH 就够了。只有当产品还要求无人提问时自动触发、重启不丢任务、事实可审计、结果可回测和版本可回滚时，才需要 Hub 外层。

## 2. 两个系统各自拥有的东西

| 问题 | DSH | Decision Hub |
|---|---|---|
| 人如何和 Agent 对话 | 唯一拥有 Chat、Session、Prompt、History | 只保存关联引用 |
| Agent 如何行动 | 唯一拥有 Agent Loop、Tool、Skill、Subagent、MCP、Trajectory、Compaction | 不实现第二套 Loop |
| 什么时候启动任务 | 用户在 DSH 中主动开始 | 事件、日历、新闻、定时器自动 admission |
| 任务重启、幂等、取消 | 提供 Session 能力 | 以 run_id、lease、outbox 和恢复协调为业务真相 |
| 事实是否可信 | 返回工具结果候选 | 负责来源、PIT、三时间戳、hash、冲突和 Evidence 入账 |
| 能否发布结论 | 只能生成候选报告 | 代码 Gate 唯一决定 publish、research_only 或 reject |
| 后验是否正确 | 不拥有业务结果 | 保存 Forecast、Outcome、Evaluation、反馈和版本比较 |
| 日常前端 | 官方 DSH Web | 不复制 Chat/Trajectory |
| 运维和产品后台 | 提供跳转 | Decision Desk 管理状态、来源、评测、Promotion、Rollback |

硬规则：Agent 可以提出候选，但不能写业务账本、修改 Gate、切换 active pointer、自动交易或自动 Promotion。

## 3. 为什么 DSH 外还要有 Hub

DSH Session JSONL 是 Agent 工作过程的事实源，不是产品业务事实源。Hub 补齐六类 DSH 会话之外的产品责任：

1. 日历、新闻或定时器到点后自动创建一次有确定身份的任务。
2. 进程重启、重复事件和网络失败后不丢任务、不重复发布。
3. 每个事实拥有来源、三时间戳、PIT、hash、权威性和冲突状态。
4. Agent 只能提交候选，代码 Gate 决定能否发布。
5. Forecast 到期后保存 Outcome、误差和人工反馈，形成可评测资产。
6. DSH、模型、插件或领域规则升级前可以 replay、holdout、shadow、Promotion 和 rollback。

这些是产品控制面职责，不是第二个 Agent。Hub 不重新实现 Chat、Session、Trajectory、Tool Loop、Subagent、Skill、JSONL 或插件安装器。

## 4. 完整运行流程

### 4.1 普通 DSH 会话

    用户打开 DSH Web
      -> DSH 创建 Session
      -> DSH Agent Loop 选择 Tool / Skill / Subagent
      -> DSH 保存 Session JSONL 和 Trajectory
      -> 用户查看、继续或导出会话

普通会话默认是探索，不会因为聊天内容自动写入 Hub 的 Evidence、Forecast 或 Outcome。只有显式创建正式 Decision Run，才进入产品账本。

### 4.2 正式 Decision Run

    来源文本 / 日历 / 新闻 / 定时器
      -> Hub admission：event_id + run_id + request_hash
      -> Hub outbox 幂等提交给 DSH Host Plugin
      -> Host Plugin 创建或恢复确定性的 dsh_session_id
      -> DSH Agent Loop 主动发现证据缺口并继续调用工具
      -> Capability Gateway 校验来源、时间、权限、PIT、hash 和预算
      -> DSH 输出研究候选并回传完成状态
      -> Hub 冻结 Decision Snapshot，执行确定性 Gate
      -> Artifact / Forecast / Outbox；到期后 Outcome / Evaluation
      -> DSH Web 展示轨迹，Decision Desk 展示业务报告和审计结果

智能体的主动性来自 DSH 内层 Agent Loop：它发现缺口、选择下一步工具并继续执行。Hub 外层只负责触发、边界、恢复、审计和结果提交。

### 4.3 失败流程

    Provider / Tool / Hub / DSH 失败
      -> 保留已完成证据和结构化 error provenance
      -> 按错误类别执行有限重试或恢复
      -> 事实门未满足时停止为 research_only 或 reject
      -> 不伪造证据、不发布方向性 Forecast、不覆盖历史 Run

## 5. DSH、LangGraph 和 Hub 的关系

    DSH 内层：Agent Loop
      目标 -> 选工具/Skill/Subagent -> 读结果 -> 发现缺口 -> 继续或输出候选

    Hub 外层：产品生命周期
      admitted -> dispatched -> running -> completed/failed/cancelled
               -> evidence_attested -> gate_evaluated -> committed
               -> outcome_due -> evaluated

    LangGraph：Hub 外层的可恢复编排
      checkpoint、生命周期节点、Evidence Round 边界、恢复和 commit 路由

LangGraph 不负责网页搜索策略、金融角色扮演、Session 历史或第二个 Tool Loop。当前 agentic research graph 是迁移期 SDK candidate 的回放、恢复和对照路径；官方 DSH Web Host 通过验收后，它应收敛为生命周期图，而不是继续扩成另一套 Harness。

## 6. 插件到底指什么

### 6.1 DSH 官方插件

DSH 官方插件是 DSH 的安装和运行单元，使用 DSH 自己的格式和生命周期，可以提供 Tool、Skill、Subagent、MCP、Hook、Host route、Session 事件、Web Client UI、Conversation Node、Settings 卡片、dsh.bundle 组合和 dsh.client 动态加载。

本项目的 extensions/dsh/decision-hub 属于这一层。它把 Hub readiness、Run 状态、Evidence/Gate 摘要和跳转入口挂入官方 DSH Web，但不复制 DSH 页面和内部状态机。

### 6.2 Decision Hub CapabilityManifest

CapabilityManifest 不是第二个插件安装器，而是 Hub 对“这个插件或工具的结果能否进入正式事实链”的准入记录。它记录 capability、schema、provider、permission、allowed domains、timeout、retry、cost budget、PIT、freshness、authority、provenance、audit 和 rollback。

只改变 DSH UI 或个人临时体验的插件由 DSH 管理；任何会影响正式 Evidence、Gate、Forecast 或 Outcome 的结果，必须通过 Hub Capability Gateway 和 manifest 审计。官方插件已有稳定 schema 时只做 binding；领域格式不同才加薄 result mapper。插件不能直接写 Hub Ledger、Gate 或 active pointer。

## 7. 前端不是两套重复产品

### 7.1 DSH Web：Agent 主界面

直接复用固定版本上游 DSH Web，保留原生 Chat、Session、History、Plan、Tool、Skill、Subagent、Trajectory、插件清单、设置和实时状态。

Decision Hub Client Plugin 只新增当前 run_id、dsh_session_id、运行模式、Evidence 缺口、Gate 状态、Forecast horizon、报告链接、取消/重试/复查和跳转 Decision Desk。它不实现第二套 Chat 或 Trajectory。

### 7.2 Decision Desk：产品管理后台

现有 apps/decision-desk 不删除，职责收敛为 Operations、worker、队列、readiness、来源健康、Capability、Provider、错误 provenance、成本、Ledger、PIT、Evidence lineage、Run Inspector、Forecast、Outcome、Evaluation、Dataset、Experience、Promotion、Rollback、Backup、通知和审计。

它可以提供“在 DSH 中打开此 Session”的链接，但不继续复制 DSH 的 Agent 对话体验。迁移期的 Research Command Center 保留为业务报告和诊断入口；原生 DSH Web 通过验收后，不再扩张其聊天功能。

## 8. 当前代码结构

    apps/
      hub_api/                    # REST、Query/View、命令；不执行长 Agent 任务
      hub_worker/                 # realtime/research/evolution durable worker
      research_mcp/               # DSH -> 正式 capability gateway
      hub_mcp/                    # 人工 DSH/Codex 工作台入口
      decision-desk/              # 当前前端，逐步收敛为管理后台

    packages/
      kernel/                     # Event/Evidence/PIT/Gate/Ledger/Outcome
      orchestration/langgraph/    # Hub 生命周期、checkpoint、恢复
      runtime_adapters/dsh_runtime/ # DSH Python SDK candidate/fallback
      source_adapters/            # manual/feed/transcript -> TextEnvelope
      provider_adapters/          # Search/Official/Market/Notification
      workbench_adapters/         # DSH/Codex/MCP capability binding
      query_views/                # 前端人可读 DTO
      evals/                      # replay/holdout/shadow/Promotion 证据

    contracts/                    # YAML canonical schema + codegen
    packs/crypto_macro/           # 当前唯一领域包
    infra/dsh/                    # 上游 pin、构建、启动、校验和回滚

当前真实 DSH 调用链：hub_worker -> runtime_adapters/dsh_runtime -> deepseek_harness SDK profile 子进程 -> DSH Session/Tool/Subagent/JSONL。

因此，“代码已经调用 DSH”和“当前网页已经是官方 DSH Web”可以同时成立：SDK adapter 仍保留为 candidate/fallback，官方 Web Host/Client plugin 与 durable bridge 已完成工程验收；当前 replay 页面仍是诊断路径，live Provider/长期 worker 尚未启用。

## 9. 目标代码结构

    extensions/dsh/decision-hub/
      package.json                 # 官方 dsh.bundle + dsh.client 声明
      cordis.patch.yml             # 官方组合 patch
      src/host/                    # readiness、submit/status/cancel、事件回调
      src/client/                  # Run/Evidence/Gate/Report 节点和设置
      tests/                       # Host/Client contract 与加载测试

    packages/runtime_adapters/dsh_runtime/
      client.py / runtime.py       # 现有 SDK canary/replay/fallback，保留
      web_host_client.py            # 目标：调用 Host bridge 公开契约
      web_runtime.py               # 目标：同一 ResearchHarnessRuntime Port 的 Web 实现

    apps/hub_api/                  # DSH callback/query 公开端点
    apps/hub_worker/               # outbox dispatch、status reconcile、恢复
    packages/kernel/               # dsh_session_links 关联和确定性业务状态
    packages/query_views/          # Session link 和业务报告 DTO
    migrations/versions/           # 只做 additive migration
    tests/dsh_native/              # 契约、幂等、恢复和 replay E2E

不把 DSH 源码复制到业务目录。vendor/deepseek-harness 如有需要只能作为固定 commit 的只读 submodule 或 checkout；业务代码只能依赖官方导出的 package、SDK、MCP 或公开 Web seam。

## 10. 三份状态必须分开

| 状态 | 存放位置 | 记录什么 | 不能替代什么 |
|---|---|---|---|
| DSH Session JSONL | DSH session root | turn、tool、subagent、compaction、trajectory | 不能替代 Hub Ledger |
| LangGraph checkpoint | checkpoint store | Hub 生命周期执行到哪一步、崩溃恢复 | 不能替代业务事实 |
| Decision Hub Ledger | SQLite/Alembic 业务表 | Event、Evidence、PIT、Run、Artifact、Forecast、Outcome、Evaluation | 不能复制完整 DSH raw JSON |

三者只通过 event_id、run_id、dsh_session_id、trace_ref、snapshot_id、artifact_id 关联。删除一个 DSH Session 不能删除历史业务结果；升级 DSH 也不能改写历史 Forecast/Outcome。

## 11. 领域和未来产品如何扩展

当前只有 packs/crypto_macro，里面放根因链、最低事实包、来源优先级、角色 profile、Capability binding、金融 Gate 和评测 fixture。

新增 PPT、A 股、美股、ASR 或其他智能体时按以下顺序判断：

    只是 DSH 的工具、Skill 或 UI？ -> DSH official plugin
    是产品自己的触发、账本、权限或结果评测？ -> 复用 Hub Platform Core
    是领域方法、事实要求、输出和 Gate？ -> 新建独立 Product Extension / Domain Pack
    是否真的有两个领域以相同语义调用？ -> 才提取共享 Platform Core 接口

ASR 只需实现 AsrProviderPort 到 TranscriptSourceAdapter，再产出 TextEnvelope；它不进入 DSH/Hub 的研究主链。PPT 不能修改 crypto_macro，也不能把金融字段扩散到 Core。

## 12. 历史集成阶段和当前可用性边界

DSH-NATIVE-CORE 是已完成的历史集成阶段；当前实施阶段是 `PRODUCT-CLOSEOUT-01`。下表保留原生 Web 核心的工程边界，不再作为当前任务编号：

| 子阶段 | 目标 | 当前含义 |
|---|---|---|
| NATIVE-00 | 固定一致的官方 DSH Web 上游闭包并验证公开 exports | 已完成工程验收 |
| NATIVE-01 | canonical bridge contract、Session link、回调/query | 已完成工程验收 |
| NATIVE-02 | 最小官方 Host Plugin | 已完成工程验收 |
| NATIVE-03 | 最小官方 Client Plugin | 已完成工程验收 |
| NATIVE-04 | dispatch/reconcile、replay E2E、重启恢复 | 已完成工程验收 |
| NATIVE-05 | 本地运行、升级、回滚和离线回归 | 已完成工程验收 |
| NATIVE-06 | 真实 Search/Official/Market 价值 Gate | 不属于离线完成声明，需单独授权 |

R0、R1、R2、R2-L 的工程退出门完成，不等于实时市场产品可用；Fixed 仍是 active baseline，DSH 仍是 candidate/shadow，Replay 仍是诊断态。NATIVE-00 至 NATIVE-05 已完成只表示 DSH 原生集成核心完成，不表示预测准确率、盈利或 DSH Promotion 已证明。当前后续统一以 `PRODUCT-CLOSEOUT-01` 的 C1-C7 和 [最终产品交付实施书](FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md) 为准；C4 承接原 NATIVE-06 的隔离真实能力证据，不另开并行阶段。

## 13. 开发和维护硬约束

1. 先写 SDD、契约和 BDD，再做 TDD Red/Green；跨模块 schema 只改 contracts/schemas，禁止手改生成镜像。
2. Kernel 不依赖 DSH、LangGraph、Provider；Agent 只能提交候选，代码 Gate 唯一裁决。
3. 不新增第二套 Agent Loop、Supervisor、Session、Trajectory、插件安装器、队列或业务账本。
4. DSH 官方插件采用 dsh.bundle 和 dsh.client seam；不得 import 上游私有 src 路径。
5. 插件结果进入正式 Evidence/Gate 前，必须经 CapabilityManifest、运行时校验、PIT、权限和 provenance。
6. 失败必须保留具体错误和已完成证据；不能把失败统一改写为成功或 provider_timeout。
7. 不改写历史 migration、Run、Evidence、Artifact、Forecast、Outcome；新增表只能使用 additive migration。
8. 每项任务开始生成 tmp/task-context.md，结束同步模块 README、状态、路线图、CHANGELOG 和测试证据。
9. 未运行的检查不得写成 passed；Replay fixture 不得宣传为实时事实、预测优势或盈利。
10. 一旦需要长期 fork DSH、依赖私有 API、扩大 secret/network/交易权限，或连续两次补丁仍失败，停止并回到 ADR/owner 决策。

## 14. 新会话如何恢复

    INDEX.md
      -> docs/context/CURRENT_STATE.md
      -> docs/context/CURRENT_DECISIONS.md
      -> 本文（边界和代码地图）
      -> 当前 Stage Charter
      -> 对应 ADR、canonical schema、模块 README 和测试

本文件只回答谁负责什么、代码在哪里、如何组合。需要改变职责、协议或阶段范围时，必须更新对应 ADR 或 Stage Charter，而不是在本文末尾追加临时讨论。
