# Decision Hub 通用底座与首个产品最终实施章程

版本：`PRODUCT-PLATFORM-2026-09-01.v1`
状态：`executed / E2-L passed / E3 prospective observation`
执行对象：`PRODUCT-CLOSEOUT-EXEC-02 / Crypto Macro Trader Pilot`
适用范围：首期单 owner、单机、只读研究；后续领域必须通过新的 Stage Gate。
本文件用途：保留通用底座和首个产品的长期设计；当前执行/收口入口是
[产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)。

> 本文不再是当前执行入口，也不替代 canonical schema、已接受 ADR 和模块 README。若本文与
> `contracts/`、ADR 或 Stage Charter 冲突，立即停止代码，先修订更高优先级事实源。
> 不把聊天内容、临时脚本或模型输出当作架构授权。

## 0. 执行结论

Decision Hub 的首个产品不是“本地 DSH 的复制品”，也不是一次 LLM 问答。它是：

```text
来源/事件持续发现
  -> durable Run admission
  -> 官方 DSH Web Session
  -> DSH Harness 在同一 Session 内自主补证
  -> Capability Gateway 校验权限、PIT、鲜度、来源和 hash
  -> 代码 Sufficiency/Gate 裁决
  -> 人可读报告、研究停止或方向性候选
  -> 本机 outbox 通知
  -> Outcome/Evaluation/FailurePattern/Experience 资产
```

本轮统一采用以下决策：

1. **DSH 是用户交互和 Agent 执行主壳。** 直接复用锁定版本的官方 DSH Web、Session、Trajectory、Tool、Skill、Subagent、MCP、JSONL 和 compaction；不复制上游前端，不在 Hub 重新实现 DSH Agent Loop。
2. **Decision Hub 是产品控制面和长期资产库。** 它拥有 Event、Run、Evidence、PIT、Sufficiency、Gate、Artifact、Forecast、Outcome、Evaluation、Outbox 和个人资产；不是另一个聊天入口。
3. **LangGraph 只负责 Hub 外层产品生命周期。** 它用于 durable checkpoint、lease、恢复、Evidence Round 边界和通知生命周期；不创建第二个 ReAct/tool loop，不与 DSH 争夺 Supervisor。
4. **插件是正式能力安装单元，不只是界面组件。** DSH 官方 Plugin seam 负责 Host/Client、Tool/Skill/MCP/Subagent/Hook/UI；Domain Pack 负责业务方法和事实要求。两者通过版本化 `CapabilityManifest` 和公开 Port 连接。
5. **Agent 只能提出候选，代码 Gate 唯一裁决。** Provider、DSH、LangGraph、插件、worker 都不能改写历史账本、active pointer、Gate、交易权限或自动下单。
6. **首期先把文本主线做成可用试点。** ASR 只保留 `TextEnvelope` 入口，PPT、A 股、美股、多用户和远程高可用不进入本次交付。

## 1. 当前事实，不混淆“工程完成”和“产品可用”

### 1.1 已经存在的能力

| 范围 | 当前事实 | 证据/边界 |
|---|---|---|
| R0 文本核心 | TextEnvelope -> Snapshot -> Agent -> Gate -> Artifact/Forecast | 离线质量门和历史 ReleaseManifest；不是实时来源证明 |
| R1 来源/调度 | SourceAdapter、calendar/news 入口、market adapter、durable scheduler、outbox | fixture/replay 已覆盖；真实授权来源和长期稳定性待观察 |
| R2 Workbench/Evolution | Query/View、Run Inspector、Evaluation、FailurePattern、Experience、candidate/rollback | 离线和本机 acceptance；不代表 DSH candidate 已 Promotion |
| DSH Native Core | 官方 Web/Host/Client plugin、Session/Trajectory/JSONL link、callback、restart/recovery | `DSH-NATIVE-CORE` engineering acceptance complete |
| Agentic Research | bounded evidence-round graph、研究 worker、MCP capability gateway、DSH runtime candidate | `R2-R-06E retain_baseline`；默认 active 仍是 Fixed |
| G1/G2-A/B | PIT server-owned、错误 provenance、并行隔离、UI 失败投影、六类事实 manifest/replay | 离线通过；没有替代真实 Search 证据 |

### 1.2 当前未完成的产品事实

- `Fixed` 仍是 active；DSH Research Runtime 仍是 `candidate/shadow`。
- `web.search` 真实 canary 曾在 20 秒 capability deadline 内超时，错误为
  `research_capability_timeout`；不能把失败伪装成 `no_trade`。
- `official.macro`、`market.cross_asset`、`market.crypto_derivatives` 模块级只读 canary 已通过，但 `market.cross_asset` 证据约 3.85 天，只能作为历史/低频参考，不能当作实时确认。
- C1-C3、C5-C7 的工程/replay 闭环已经通过：官方 Workspace 可见受管 Session，
  同一 Session 可进入第二轮 Evidence Round，三类 replay、报告投影、恢复、备份和
  版本 fail-closed 均有证据。C4 的完整 live capability acceptance 仍未通过。
- 默认 Compose/replay 是诊断路径；live 产品实例必须显式启用 provider、capability allowlist 和 `dsh-web` profile，不能让 replay 隐式混入。

因此当前状态是：**可审计的工程候选，不是实时交易决策产品，不是盈利承诺。**

### 1.3 “可用”的严格定义

| 门 | 必须满足 | 通过后的含义 |
|---|---|---|
| E1 工程门 | 启动、契约、Run、失败、恢复、备份、静态检查和测试通过 | 不丢状态、不伪造结论、可审计 |
| E2-R replay 产品主线门 | DSH Web -> Host/Client -> Hub Run -> MCP -> Evidence/Gate -> 报告贯通 success/partial/insufficient 三类固定场景 | 证明产品组合、失败语义和页面可验收；不证明实时事实能力 |
| E2-L live 产品主线门 | 同一正式路径至少以已审计且新鲜的 live capability 完成成功和安全失败场景 | 单 owner 可进入 `research_only` 试运行 |
| E3 价值门 | 至少 14 天或 20 个高影响事件，PIT 对照、Outcome、Brier/方向、成本/延迟和 owner usefulness 有证据 | 决定 `promote / retain_baseline / stop` |

E1、E2-R 已通过，但只有 E2-L 也通过后才叫 `pilot_ready / research_only`；因此
当前 `pilot_ready=false`、`pilot_usable=false`。E3 未通过不得宣称预测优势或盈利。
E3 结束必须停止堆功能，写下明确的 `promote`、`retain_baseline` 或 `stop` 决策。

## 2. 最终用户视图：只有一个主入口

### 2.1 官方 DSH Web 是唯一用户入口

启动器只打印一个官方 DSH URL，并预装 `Decision Hub` Host/Client plugin。用户不需要知道 API、MCP、worker、SQLite 或端口：

```text
官方 DSH Web
  ├─ Crypto Macro Trader 工作区
  ├─ 原生 Chat / Session / History / Plan / Tool / Skill / Subagent
  ├─ 原生 Trajectory / JSONL / compaction / live 状态
  ├─ Decision Hub 研究任务入口
  ├─ 当前 Run、证据覆盖、失败来源、Gate、报告和复查时间
  └─ 按需链接到 Decision Desk 管理后台
```

普通 Chat 仍可用于探索，但标记为 `exploration`，不会自动成为正式 Evidence/Forecast。正式研究由 typed intake 创建 `event_id + run_id`，再关联最多一个确定性的 `dsh_session_id`。

### 2.2 DSH Web 中的人可读业务区

插件只增加业务投影，不重画 DSH 原生界面，也不暴露无用 JSON：

| 视图 | 展示内容 | 数据来源 |
|---|---|---|
| 工作区栏 | 领域、Live/Replay、Fixed/Candidate、runtime/provider/model/schema 版本 | Query/View DTO |
| 当前运行卡 | event、run、DSH session、状态、开始/更新时间、取消/重试/复查 | Run View + DSH link |
| 研究进度 | round、hard/soft coverage、缺口、成功/失败 capability、stop reason | Research Result/Trace |
| 证据链 | 来源、authority、published/observed/received 时间、PIT、hash、冲突 | Evidence lineage |
| 决策报告 | 主/反根因链、30m/24h/72h、触发、失效、Gate、复查时间 | Artifact/Forecast |
| 健康和操作 | worker、capability、通知、备份健康；取消、retry/recheck、打开 Desk | Operations View |

默认隐藏：LangGraph state、Provider 原始 payload、密钥、SQL、MCP 原始协议和完整 DSH JSONL。原始轨迹仍由 DSH 原生 Trajectory/JSONL 保存，审计时按引用查看。

### 2.3 Decision Desk 是管理后台

Decision Desk 不与 DSH 并列为第二个聊天产品，只提供：

```text
Operations / Readiness / Worker heartbeat
Run Inspector / Timeline / Error Provenance
Source 与 Capability 健康 / Evidence lineage / PIT
Artifact / Forecast / Outcome / Evaluation
FailurePattern / Experience / Dataset
Promotion / Rollback / Backup / Notification Outbox
```

前端只能消费 Query/View DTO 和 Zod schema；不直读数据库、graph state 或 provider payload。桌面和 375/768/1024/1440 视口均须无横向溢出，错误、部分成功和停止原因必须人可读。

## 3. 固定架构和所有权

```text
+----------------------------- DSH Web ----------------------------+
| official Chat/Session/Trajectory/Tool/Skill/MCP/Subagent/JSONL  |
| Decision Hub Host + Client Plugin                                |
+-------------------------------+---------------------------------+
                                | official plugin seam
                                v
+----------------------+   +----+-------------------------------+
| Source adapters       |   | Host Plugin                      |
| text/calendar/news    |   | intake/status/cancel/callback    |
| future ASR -> text    |   | run <-> session correlation       |
+----------+-----------+   +----+-------------------------------+
           | TextEnvelope       |
           v                    v
  +--------+----------------------------------------------+
  | Hub API -> durable Run -> outer LangGraph lifecycle    |
  | Evidence Gateway -> PIT -> Sufficiency -> Gate -> Ledger|
  | Artifact/Forecast -> Outbox -> Outcome/Evaluation     |
  +--------+---------------------+-------------------------+
           | MCP capability       | Query/View
           v                      v
   Research MCP Gateway       Decision Desk Admin
           |
   Search / Official / Market adapters
```

| 组件 | 拥有 | 明确不拥有 |
|---|---|---|
| DSH Web/Harness | Agent Loop、Tool/Skill/Subagent/MCP、Session、Trajectory、JSONL、compaction、原生 UI | Hub 账本、PIT、Gate、Forecast/Outcome、交易权限 |
| 官方 DSH Plugin | Host/Client seam、Run/Session 关联、状态卡、报告卡、命令桥 | 第二套 Chat、Agent Loop、账本、Gate、自动安装器 |
| Hub Kernel | Event、Run、Evidence、Snapshot、Sufficiency、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation、Outbox | DSH 私有状态、模型推理、插件内部实现 |
| LangGraph orchestration | 生命周期、checkpoint、lease、Evidence Round、恢复和外层通知 | DSH tool loop、业务裁决、Provider 协议 |
| Capability Gateway | schema、domain、权限、allowlist、timeout、retry、预算、PIT/provenance | 结论发布、Gate 修改、权限扩大 |
| Domain Pack | 事实要求、来源梯度、doctrine、角色 profile、Gate 参数、评测 | 通用账本、Harness 内部实现 |
| Decision Desk | 运维、审计、查询、资产、Promotion/Rollback 命令 | 第二套聊天、直写数据库、改写历史 |

## 4. Agent、Supervisor 与插件的真实关系

### 4.1 不是“纯 LLM + Prompt”

Agent 的最低定义是：在一个 durable Run 内，读取目标和当前证据缺口，选择授权能力，读取结构化结果，重新计算缺口，继续、降级或解释性停止。只有首轮 Prompt 后直接结束，不算本产品的 Agent。

### 4.2 两层循环，不重复造轮子

**DSH 内层智能循环（唯一推理循环）**复用官方 DSH Harness：

```text
读取目标 + Domain Pack + 当前 hard gaps
  -> DSH 内建 Manager/Supervisor 选择 manifest 中的 Tool/Skill/Subagent/MCP
  -> Gateway 返回结构化 capability result
  -> DSH 更新计划/gaps，决定是否继续同一 Session
  -> 仍有关键 gap 且 deadline/预算允许？下一 round
  -> 否则输出 candidate、research_only 或解释性停止
```

本项目只提供：Role Profile、MCP binding、CapabilityManifest、结构化 schema、权限和确定性 Gate；不自写 ReAct、工具选择器、消息重试或新的 Supervisor。Manager、counter-thesis、data-quality 等角色以 DSH 官方可加载的 profile/subagent 配置存在，不在 Python 中散落一套平行角色。

**Hub 外层产品循环**由现有 LangGraph lifecycle 管理：

```text
discovered -> admitted -> queued -> dispatched -> researching
  -> evidence_attested -> gate_evaluated
  -> committed | research_only | rejected
  -> outcome_due -> evaluated
```

它只负责 Run 去重、lease、heartbeat、checkpoint、重启恢复、取消、通知 outbox 和幂等提交；不替代 DSH 的模型/工具循环。这样 DSH 可升级，Hub 仍可保留产品资产；LangGraph 可升级，DSH 仍是唯一 Agent 主壳。

### 4.3 插件与 Domain Pack 的边界

```text
DSH Native Plugin
  = 官方 seam 上的 Host/Client/Tool/Skill/MCP/Subagent/Hook/UI 运行单元

Product Extension / Domain Pack
  = 目标、事实要求、来源梯度、方法、Gate、结果契约、评测、资产
```

一个 Domain Pack 可以带一个薄 DSH bundle：加载角色、绑定能力、提供入口和显示卡；正式 Evidence/Gate 影响必须经过 `CapabilityManifest` 和 Gateway。插件卸载、DSH 升级或模型切换不得删除 Hub 历史。

未来新增 PPT、A 股、美股的固定顺序：

1. 新建独立 Extension/Domain Pack、canonical schema 和 ADR；
2. 证明它只依赖公开 Platform Port（Event/Run/Evidence/Artifact/Asset/Evaluation/Trace ref）；
3. 为领域维护自己的结果契约、来源、Gate 和评测；
4. 用一个最小真实调用方证明两个领域确实共享接口；
5. 只有重复出现的共享语义才提取 Core，不提前泛化。

PPT 的结果应是 `SlidePlan`/`RenderCheck`/导出 Artifact，不得复用金融 Forecast 字段。ASR 的结果先是 `TextEnvelope`，下游研究核心不感知音频来源。

## 5. 代码结构和依赖规则

```text
apps/
  hub_api/                 REST command/query/callback；不执行长推理
  hub_worker/              realtime/research/evolution durable worker
  research_mcp/            唯一正式 capability gateway
  decision-desk/           Operations/Ledger/Evaluation 管理后台

packages/
  kernel/                  领域无关 Run/Evidence/PIT/Gate/Ledger/Outcome/Port
  orchestration/langgraph/ Hub 生命周期、checkpoint、recovery、Evidence Round
  runtime_adapters/dsh_runtime/ 官方 DSH SDK/Web Host adapter
  provider_adapters/       Search/Official/Market/Notification 具体实现
  workbench_adapters/      DSH/MCP/capability binding
  query_views/             人可读 DTO，不暴露 SQL/raw state
  evals/                   replay/holdout/shadow/Outcome/Promotion 证据

contracts/
  schemas/                 YAML canonical source，唯一跨模块事实源
  generated-*              codegen 镜像，禁止手改

packs/crypto_macro/
  doctrine/ evidence/ profiles/ tools/ gates/ evaluations/ fixtures/

extensions/dsh/decision-hub/
  src/host/                官方 Host seam：readiness/intake/status/cancel/callback
  src/client/              官方 Client seam：状态卡、报告、Desk 链接
```

依赖只能向内：`apps -> packages -> contracts`。Kernel 不导入 DSH/LangGraph/Provider/前端；Domain Pack 通过 Port 接入；研究 graph 不直接 HTTP；前端只依赖 Query/View 与 Zod；跨边界只传 canonical Pydantic/Zod 模型，禁止裸 `dict/Any`。

DSH 上游由 `infra/dsh/upstream.lock.json` 锁定。升级流程是：更新 lock 和 hash -> 安装官方源码/插件依赖 -> 跑 NATIVE 三场景与版本 fail-closed -> 再跑完整质量门。禁止把上游源码复制到业务目录、禁止修改上游源码、禁止把测试 replay transport 当生产网络能力。可替换的是 lock/runtime adapter；不可替换的是 Hub canonical 资产和契约。

## 6. 来源、后台运行和信息充分度

### 6.1 来源适配统一为文本/事实契约

```text
人工文本 / 日历 / 新闻 / 官方文档 / 市场行情 / 未来 ASR
  -> SourceAdapter
  -> TextEnvelope 或 typed EvidenceCandidate
  -> Evidence Gateway
  -> PIT + authority + freshness + conflict + hash
```

`web.search` 是发现和补充线索，不自动等同于权威事实；精确政策、利率、价格和衍生品事实优先使用 manifest 指定的 Official/Market adapter。未经授权的爬取、搜索摘要或模型记忆不能成为唯一 canonical source。

### 6.2 常驻后台不是“用户每次提问才触发”

单机启动器负责 API、realtime worker、research worker、evolution worker、Research MCP 和 DSH Web 的受控启动。Realtime worker 根据来源 manifest 轮询日历/新闻，使用 cursor、revision、dedupe key 和 `next_poll_at`；发现高影响事件后创建 durable Run。Research worker 从 Run lease/checkpoint 恢复，在同一 DSH Session 内触发主动补证；无需 owner 逐轮输入。

当前默认 replay 是为了 CI 可复现。要进入 live，必须显式提供 provider/capability 配置、allowlist、预算和 deadline；没有能力时应进入 `research_capability_unavailable`/`critical_data_unavailable`，而不是假装完成。

### 6.3 停止和错误语义

| 情形 | Run 语义 | 是否发布方向性 Forecast |
|---|---|---|
| 证据满足 authority/PIT/freshness/independence | `completed` + Gate 结果 | 仅 Gate 允许时 |
| 非关键 capability 失败、其它证据保留 | `degraded`，保留成功和失败 provenance | 由 Gate 按缺口决定，关键缺口时否 |
| Provider/MCP/DSH/外层 deadline 超时 | `failed` 或 `degraded` + 稳定 error code | 否 |
| 证据 stale/低权威/冲突/未来信息 | `research_only` 或 `rejected` + stop reason | 否 |
| owner cancel / worker shutdown | `cancelled`，保留已完成结果 | 否 |

`no_trade` 只能表示 Gate 在充分事实下裁决不交易；不能用来掩盖 Search 超时、来源缺失或 Provider 不兼容。每个错误保留 `error_code/origin/cause_code/retryable/attempt/deadline/trace_ref`。

## 7. 三份状态和个人资产

### 7.1 状态分离

| 状态 | 所有者/存储 | 允许做什么 |
|---|---|---|
| DSH Session JSONL | DSH session root | 对话、工具、subagent、compaction、完整轨迹 |
| LangGraph checkpoint | Hub orchestration store | 外层 lifecycle、round 边界、恢复位置 |
| Hub Ledger | Kernel SQLite/Alembic | Event、Run、Snapshot、Evidence、Artifact、Forecast、Outcome、Evaluation、Outbox |

三者通过 `event_id/run_id/dsh_session_id/trace_ref/snapshot_id/artifact_id` 关联，只增不改历史。模型、DSH、插件或 Domain Pack 升级不得重写历史 Forecast、Outcome、Evaluation。

### 7.2 个人资产目录

每次正式 Run 都要沉淀可迁移资产：

- `DomainDoctrine`：根因链、事实要求、来源优先级、鲜度和 Gate；
- `EvidencePack`：来源 manifest、PIT fixture、内容 hash、失败样本、回退关系；
- `RoleProfile`：Manager、反方、Data Quality、Specialist 的任务和权限契约；
- `RuntimeProfile`：DSH/固定 runtime、provider、model、api mode、预算、超时、schema 版本；
- `ResearchTrace`：Plan、Tool、Evidence、Sufficiency、Stop Reason 的规范化轨迹；
- `EvaluationDataset/Experiment`：Fixed、DSH、未来 Pi/OpenAI candidate 的同条件比较；
- `FailurePattern/Experience`：根因、修复、适用条件、是否推广；
- `ProductArtifact`：报告、Forecast/Outcome、owner feedback、Promotion/Rollback 审计。

DSH JSONL 是运行证据，不是产品资产的唯一事实源；更换 DSH、Pi 或模型时只替换 runtime adapter，以上资产仍可读。

## 8. 接下来唯一允许的阶段和任务

### 阶段 P0：文档/契约锁定（本次）

- [x] 新建本章，锁定产品定义、DSH-first 架构、插件/Domain Pack 边界、代码地图和停止线。
- [x] 修正历史状态措辞，不把模块 canary 或 replay 写成产品完成。
- [x] 将本章链接加入 `INDEX.md`、`docs/context/CURRENT_STATE.md`、`docs/IMPLEMENTATION_STATUS.md` 和 `CHANGELOG.md`。
- [x] 完成本章引用的文档检查、codegen check、pytest 和前端构建证据。

### 阶段 P1：C3 主动补证主线（工程/replay 已完成）

目标：同一官方 DSH Session 在 hard gap 存在时会自主调用已授权 capability；一个能力失败不取消其它成功结果；达到充分度、预算或 deadline 才停止。

- [x] `P1-01` 用现有 `CapabilityManifest`/Domain Pack 加载六类 `crypto_macro` requirements，禁止 Python/Prompt 双写。
- [x] `P1-02` 将 DSH manager profile 绑定到官方 MCP `research_capability_execute`，每轮保存 typed Plan/Tool/Evidence/Sufficiency。
- [x] `P1-03` 复用 LangGraph bounded evidence-round/checkpoint，不新增第二个 Agent Loop 或 Supervisor。
- [x] `P1-04` 成功、部分失败、事实不足三场景同一 `run_id <-> dsh_session_id` 回放，并将报告卡投影到 DSH Web。
- [x] `P1-05` 确认失败状态、stop reason、retry/recheck child Run、outbox 不发送伪成功通知。

P1 根因审计补充：旧 bridge 的 `deterministic_request_id` 未区分 evidence round，
第二轮 submit 会被 Host 当作首轮 Prompt 的重复提交，无法证明 DSH 真正收到 continuation。
[ADR-0014](../decisions/ADR-0014-dsh-session-multi-turn-continuation.md) 已锁定
“一个 Run/Session、每轮一个确定性 request/prompt generation”的修复边界；P1-04
验收必须检查第二条 DSH `user/message`，不能只检查 `submit` 调用次数。

退出门：DSH JSONL、Hub Trace、Evidence、Gate 和前端状态一致；关键缺口时无方向性发布；同一 Session 不创建重复 Run/Artifact。

### 阶段 P2：G2 真实能力准入和事实充分度

目标：让来源能力能区分“成功、stale、低权威、Provider 失败、PIT 拒绝”，而不是只返回“证据不足”。

- [x] `P2-01` 只启用已审计的 Official/Market capability；`web.search` transport 未通过时保持失败可见，不冒充业务成功。
- [x] `P2-02` 为每类 requirement 保存 authority floor、freshness、时间字段、allowed domains、预算和回退来源。
- [x] `P2-03` 在隔离临时目录完成一次只读 live canary，记录脱敏 latency、cost、source count、错误 provenance；未修改 active pointer。
- [x] `P2-04` 对过期 `market.cross_asset` 触发 stale Gate；Search timeout 保留失败 Run，不伪装实时确认。
- [x] `P2-05` 在固定 replay 中完成同一 DSH Web success/partial/insufficient 三场景。
- [ ] `P2-06` 让 Search 或等价的新鲜 live capability 通过完整 contract/canary，并从正式 DSH Session 完成至少一次 live Evidence/Gate/报告闭环。

退出门：每个 hard requirement 至少有一个可验证来源或明确 `critical_data_unavailable`；失败时 fail-closed；没有 PIT 未来信息泄漏。

### 阶段 P3：C5-C7 产品主线收口

目标：报告、通知、Outcome、个人资产和单机恢复真正形成闭环。

- [x] `P3-01` DSH Web 报告卡显示事实、主/反因果链、30m/24h/72h、触发/失效、Gate、复查时间。
- [x] `P3-02` committed Artifact 和 local outbox 同一事务；通知失败只重试投递，不重新分析、不改账本。
- [x] `P3-03` 运行 3 个 replay 结果场景：成功、部分失败、事实不足；验证报告和停止原因可理解。
- [x] `P3-04` 验证 worker/DSH/Web 重启、丢 callback、lease 过期、备份恢复、版本不兼容；重复提交为零或可解释。
- [x] `P3-05` Decision Desk 展示资产目录、FailurePattern、Experience 和运行健康，不展示无用 raw JSON。

退出门：C5/C6/C7 证据齐全；DSH 仍 candidate/shadow，直到价值门完成。

### 阶段 P4：个人价值观察和停止决策

- [ ] 固定观察窗口：至少 14 天或 20 个高影响事件，以先达到者为准。
- [ ] 每个事件同时记录 Fixed baseline、DSH candidate、PIT、调用次数、首证据延迟、终态、成本和通知延迟。
- [ ] 到期补记 30m/24h/72h Outcome，计算 Brier、方向、coverage、延迟和成本。
- [ ] Owner 只填写：是否减少手工检索、是否理解停止原因、是否愿意继续使用。
- [ ] 生成一次不可逆的 `promote / retain_baseline / stop` ADR；没有证据就保持 baseline，不继续堆功能。

### 阶段 P5/P6：条件触发，不提前实现

- [ ] P5 ASR：仅当文本主线有价值后接入 Meeting Copilot 的采集/partial/final/revision，统一产出 `TextEnvelope`。
- [ ] P5 第二领域：出现真实 PPT/A 股/美股调用方后新建 Extension/Domain Pack、schema、ADR 和独立评测。
- [ ] P6 规模化：只有跨主机并发、单机资源不足、远程用户或高可用需求真实出现后，才评估 Postgres、对象存储、队列、身份、租户和灾备。

## 9. SDD/BDD/TDD/ADR 全局执行约束

每张任务卡都必须按此顺序：

```text
SDD：规格/契约/事件/Gate/非目标
  -> ADR：跨模块、不可逆、部署或协议决策
  -> BDD：正常、重复、失败、权限、恢复场景
  -> TDD Red：先写可复现失败测试
  -> Green：调用框架已有 API 的最小实现
  -> Refactor：只重构有测试保护的代码
  -> 集成/回放/Live canary（按风险）
  -> README、状态、CHANGELOG、Handoff、独立 commit
```

强制约束：

1. 文档中文；长期文档与 `tmp/` 中间产物分离。
2. Core/Domain 契约不依赖 DSH、Pi、Codex 或任意 Provider；这些只能是 adapter。
3. DSH 不写业务账本、不拥有发布权；Agent 只能提候选，代码 Gate 唯一裁决。
4. 跨边界使用 Pydantic/Zod/canonical schema，禁止裸 `dict/Any`。
5. 所有数据使用三时间戳 PIT 铁律：`published_at <= observed_at <= received_at`，未来标签只能在 `available_at > received_at` 后进入 Outcome。
6. canonical schema 只改 YAML，再运行 codegen；禁止手改 generated mirror。
7. 不引入 Redis/Mongo/Postgres/Temporal/Kubernetes 或第二套队列，除非 P6 有真实调用方并有 ADR。
8. 同一问题两次局部补丁仍未解决，停止修补，先做根因 ADR。
9. 上下文压缩时只从 `docs/context/CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、本章和目标模块 README 恢复；事实必须回链到 ADR/schema/测试证据。
10. 每次行为改变同步模块 README、状态、CHANGELOG、测试和 `docs/evaluations/`；不把推理草稿当事实源。

## 10. 最终验收 Checklist

### E1 工程门

- [x] `git diff --check`
- [x] `./.venv/bin/python tools/docs/check_module_docs.py`
- [x] `./.venv/bin/python -m tools.contract_codegen check`
- [x] `./.venv/bin/pytest -m "not live" -q`
- [x] `./.venv/bin/ruff check .`
- [x] `./.venv/bin/pyright`
- [x] `pnpm --dir extensions/dsh/decision-hub test`
- [x] `pnpm --dir extensions/dsh/decision-hub build`
- [x] `pnpm --dir apps/decision-desk test`
- [x] `pnpm --dir apps/decision-desk build`
- [x] `docker compose config --quiet`
- [x] fresh migration、backup/restore、integrity 和 recovery smoke

### E2-R replay 产品主线门

- [x] 启动器只打印一个官方 DSH URL；默认工作区可见 `Crypto Macro Trader`。
- [x] typed intake -> `202 accepted` -> durable Run -> 同源 DSH Session/Trajectory/JSONL。
- [x] DSH 在同一 Session 内至少进行两轮；generation 2 有真实 user message/turn 证据，不以 submit 次数替代。
- [x] success、partial failure、insufficient/stale 三场景在 DSH Web、Hub Query/View、Ledger 和桌面截图中一致。
- [x] 一个 capability 失败不抛弃其它成功证据；Provider/PIT/DSH/outer timeout 错误来源可区分。
- [x] 关键事实缺失时 UI 显示 `research_only`/stop reason，不输出伪 `no_trade` 或方向性 Forecast。
- [x] Report/Artifact/Outbox/Outcome/Experience 的 `run_id`、`snapshot_id`、`dsh_session_id` 可互相定位。
- [x] retry/recheck 产生 child Run，不改写 parent Run、历史资产或 active pointer。
- [x] DSH/Hub/worker 重启、callback 丢失、lease 恢复和版本 fail-closed 有证据。

### E2-L live 产品主线门

- [ ] Search 或等价的新鲜 live capability 通过完整 contract/canary。
- [ ] 从正式 DSH Web/Session 取得 live Evidence，并通过 authority、freshness、PIT 和 Sufficiency。
- [ ] live success、partial failure 和 insufficient 场景的页面、Ledger、报告/通知一致。
- [ ] 当前 revision 的移动端真实 viewport 和浏览器 console 验收资产可复核。

### E3 价值门

- [ ] 至少 14 天或 20 个高影响事件完成 prospective 观察。
- [ ] 每个 horizon 都有真实到期 Outcome 或明确 unavailable，不用模型补标签。
- [ ] 有 Fixed 与 DSH candidate 的同 PIT 对照、Brier/方向/coverage/成本/延迟统计。
- [ ] Owner usefulness 已填写且引用具体 Run/Artifact。
- [ ] 新 ADR 写明 `promote`、`retain_baseline` 或 `stop`；没有自动 Promotion。

## 11. 停止线和本轮目标

以下任一情况发生就停止当前代码任务，回到架构/契约评审：

- 需要重写 DSH Agent Loop、自己实现 Provider 协议或再建 Supervisor；
- 需要把模型提供的时间、Gate、active pointer 或账本写入当作可信事实；
- 需要扩大网络域名、Secret、插件权限，或把未审计 capability 改为 approved；
- 需要把 replay、一次 canary 或“报告看起来完整”当作实时价值证明；
- 需要为没有真实调用方的领域、服务、数据库或 UI 提前建抽象。

本次执行目标登记为：

> **`PRODUCT-CLOSEOUT-EXEC-02`：依据本章，在不破坏 R0/R1/R2 历史契约、不复制 DSH、不新增第二 Agent Loop 的前提下，完成 P1-P3 的工程/replay 主线并通过 E1/E2-R；随后只闭合 E2-L 的真实能力门，成功后立即停止代码扩张并进入 P4 价值观察。**

目标完成的唯一判定不是测试数量，而是：用户只打开 DSH Web，就能看到后台自动创建的 Run、同一 Session 的主动补证轨迹、可解释的成功/失败/事实不足报告、通知状态和可追溯个人资产；事实不足时系统明确停止，而不是快速返回一个没有依据的答案。

## 12. 变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-09-01 | 新建本章，统一 DSH-first 产品形态、Hub 控制面、LangGraph 外层、插件/Domain Pack、P1-P6 路线和 E1-E3 验收 | 解决多份阶段文档分散、入口不清、产品可用边界不清和目标漂移 |
| 2026-09-01 | 澄清 Search 首轮超时与 Official/Market 后续模块级 canary 通过是两类不同事实 | 保留历史证据，避免把部分能力误写为完整产品准入 |
| 2026-09-01 | 增加 ADR-0014 的同 Session 多 turn 幂等边界 | 修复重复 request id 让第二轮 Prompt 被 Host 去重、测试误报 continuation 的根因 |
| 2026-09-01 | P1/P3 与 E1/E2-R 收口；新增 E2-L 分层并保留 Search timeout、移动端/console pending | 防止把 replay 工程验收误写成实时产品可用 |
