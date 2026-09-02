# PRODUCT-CLOSEOUT-01：DSH Native Trader Pilot

版本：`PRODUCT-CLOSEOUT-2026-08-31.v1`
状态：`E1/E2-R/E2-L passed / E3 prospective observation`
确认日期：2026-08-31
适用范围：单 owner、单机部署、crypto macro 交易研究首个可交付试点

关联事实源：

- [最终产品交付与主线闭环方案](../product/FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)
- [DSH 与 Decision Hub 系统总装设计](../product/DSH_HUB_SYSTEM_ASSEMBLY.md)
- [DSH 与 Decision Hub 边界和代码地图](../product/DSH_AND_HUB_BOUNDARY_GUIDE.md)
- [ADR-0012 DSH-first 产品重新收口](../decisions/ADR-0012-dsh-first-product-rebaseline.md)
- [ADR-0013 DSH Web 原生插件与上游升级集成](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)
- [开发治理规范](../engineering/DEVELOPMENT_GOVERNANCE.md)
- [SDD/BDD/TDD 自测规范](../engineering/TDD_SDD_SELF_TEST_STANDARD.md)
- [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)
- [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)

## 1. Owner 决策和目标

Owner 已确认：

- DSH Web 是唯一用户主入口；
- Decision Hub 是后台控制面、可信账本和个人资产库；
- Decision Desk 只作为管理/审计后台；
- 允许一次隔离的真实 Search/Official/Market 只读 canary；
- 首版通知先使用本机 outbox/dry-run，真实投递必须单独 canary；
- 首版定位为个人研究试点，不承诺盈利、不自动交易；
- Prospective 观察窗口为至少 14 天或 20 个高影响事件，以先达到者为准；
- 不提前扩展 ASR、PPT、A 股、美股、多用户或公共插件市场。

本阶段唯一目标：把一条可理解、可恢复、可审计的产品主线交付出来：

```text
事件/人工输入
  -> Hub admission + durable Run
  -> DSH Web Host Session
  -> DSH Agent 根据证据缺口主动调用已授权能力
  -> Evidence Gateway / PIT / provenance
  -> 确定性 Sufficiency + Publish Gate
  -> DSH 业务报告和 Agent 轨迹
  -> 本机通知 outbox
  -> Outcome / Evaluation / Experience 资产
```

达到本阶段退出门后，必须停止继续堆功能，进入观察窗口并作出
`promote / retain / stop` 决策。

## 2. 产品形态和入口

### 2.1 用户只面对一个入口

交付启动器只输出一个 DSH Web URL。用户不需要知道 Hub API、Worker、MCP 或
Decision Desk 的端口。

```text
DSH Web
  -> 预装 Decision Hub plugin
  -> 默认工作区：Crypto Macro Trader
  -> 自动 Run 列表、过程轨迹、报告和复查
  -> 管理/审计链接 -> Decision Desk Admin
```

普通 DSH 会话仍可用于自由探索，但标记为 `exploration`，不会自动写入正式
Evidence、Forecast 或 Outcome。正式任务必须通过 Hub admission 创建唯一
`event_id + run_id`，再由 Host Plugin 关联唯一 `dsh_session_id`。

### 2.2 页面视图

DSH Web 页面至少提供：

```text
工作区：Crypto Macro Trader
运行模式：Live / Replay（醒目标识）
后台健康：Hub、Worker、DSH Host、Capability、通知
最近事件：事件时间、来源、Run 状态
当前研究：轮次、已覆盖事实、未解决 hard/soft gap、停止原因
报告：事实、主因果链、反方链、30m/24h/72h、Gate、触发/失效条件
操作：取消、重试、复查、反馈、打开管理后台
```

默认显示人可读 DTO，不把 DSH raw JSON、LangGraph state、Provider payload 或
secret 倾倒给用户。原始 DSH JSONL 只在 DSH 原生轨迹和审计目录保留。

Decision Desk Admin 负责：Operations、来源和 capability 健康、Run Inspector、
Evidence lineage、PIT、错误 provenance、Outcome/Evaluation、Experience、
Promotion/Rollback、备份和通知 outbox。

## 3. 所有权和代码边界

```text
DSH Web / official plugin
  Chat、Session、Trajectory、Tool、Skill、MCP、Subagent、Agent Loop、JSONL、UI

Decision Hub Kernel
  Event、Evidence、PIT、Run、Gate、Ledger、Artifact、Forecast、Outcome、Evaluation

Hub LangGraph
  durable lifecycle、checkpoint、lease、recovery、Evidence Round 边界

crypto_macro Domain Pack
  Doctrine、事实 requirements、来源优先级、Role Profile、Capability binding、Gate、Eval

Provider/Capability Adapter
  Search、Official、Market、Notification 的具体外部接口实现
```

必须遵守：

1. 不复制或 fork DSH Chat、Session、Trajectory、JSONL、插件安装器和 Agent Loop。
2. 不在 Kernel、API route 或 Decision Desk 中实现第二套 Supervisor/Tool Loop。
3. DSH 官方插件只通过公开 `dsh.bundle`、`dsh.client`、Host route、MCP 或 SDK seam 接入。
4. 影响正式 Evidence/Gate 的能力必须经过 CapabilityManifest、PIT、权限、预算和 provenance。
5. Agent 只能产生计划和候选；代码 Gate 才能发布或拒绝。
6. DSH Session JSONL、LangGraph checkpoint、Hub Ledger 三份状态分开保留。
7. 新字段先改 `contracts/schemas/`，再 codegen；禁止手改生成镜像。
8. 历史 Run、Evidence、Artifact、Forecast、Outcome 和 migration 只增不改。
9. 未授权能力保持 `disabled`/`shadow`；不能由 Agent 自动安装陌生插件或扩展网络域。
10. 需要长期 fork DSH、私有 API、第二套账本或第二套 Agent Loop 时立即停止并回到 ADR。

## 4. 两层循环和后台持久化

### 4.1 DSH 内层 Agent Loop

DSH 是研究执行 Harness。Manager/Supervisor 读取当前 Domain Pack 和 Evidence
Gap，选择允许的 Tool/Skill/MCP/Subagent，读取结构化结果，再决定继续补证或
输出候选。缺口存在且有可用能力时，不能在首轮直接写 `no_trade`。

每个 Run 的边界来自 Domain Pack：

```text
max_evidence_rounds
max_tool_calls
max_subagents
total_deadline_seconds
per_tool_timeout_seconds
max_estimated_cost_usd
permission / capability allowlist
```

### 4.2 Hub 外层 Product Loop

LangGraph 只管理产品生命周期，不重新执行 DSH 工具循环：

```text
admitted -> dispatched -> researching
  -> completed | failed | cancelled
  -> evidence_attested -> gate_evaluated -> committed
  -> outcome_due -> evaluated
```

Hub Worker 通过 SQLite WAL、Run lease、checkpoint、outbox 和 callback 实现单机
持久化。进程重启时从 Run/lease/checkpoint 恢复，不依赖 DSH 进程内状态作为业务真相。

### 4.3 信息不足和失败语义

每轮执行顺序固定为：

1. 从 Pack 读取 hard/soft requirement；
2. 将 gap 分类为可并行、依赖前置事实、不可用/权限拒绝；
3. 并行执行独立 capability，部分成功必须保留；
4. 对失败写结构化 `ErrorProvenance`，不能改写成“没有数据”；
5. 成功 Evidence 进入下一轮并重新做 Sufficiency；
6. 只有无可用能力、权限不足、数据过期、预算或 deadline 到达时才停止；
7. 输出 `publish`、`research_only`、`degraded` 或 `reject`，并展示已查、失败、缺口和下次复查条件。

## 5. 插件和领域扩展

插件分为两层，不混用：

| 层级 | 作用 | 示例 |
|---|---|---|
| DSH Native Plugin | DSH 的 Tool/Skill/MCP/Subagent/Hook/UI/Host 安装单元 | `extensions/dsh/decision-hub` |
| Product Extension/Domain Pack | 产品对象、事实、Gate、评测和历史资产 | `decision.v1` + `packs/crypto_macro` |

交易员角色由声明式资产组合而成：

```text
Crypto Macro Trader
  = decision.v1
  + crypto_macro.v1
  + crypto_macro.manager.v1
  + decision-research preset
  + audited CapabilityManifest
  + deterministic Gate
```

新增能力的判断规则：

```text
只是 DSH UI/临时工具       -> DSH Native Plugin
影响正式事实链             -> DSH Plugin + CapabilityManifest + Gateway
新增领域方法/事实/Gate      -> 新 Domain Pack/Product Extension
只有两个真实领域共享语义   -> 才提取 Platform Core 接口
```

未来 ASR 只实现 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope`，
不侵入研究主链；PPT 应创建独立 `presentation.v1`，不得把金融字段加入 Core。

## 6. C1-C7 实施任务

### C1：统一启动器、Trader 工作区和双向链接

落点：`infra/dsh/run-product.sh`、`extensions/dsh/decision-hub/src/client/`、
`extensions/dsh/decision-hub/src/host/`、`apps/hub_api/`。

要求：一个 URL 启动 API/Worker/MCP/DSH；DSH 显示 Trader 工作区；提交事件只
创建一个 Run/Session；刷新和重启可恢复；未 ready 时显示原因且不静默切 fake。

BDD：提交同一 `request_hash` 两次只产生一个 Run 和一个 DSH Session，页面显示
两者关联、模式、状态和报告链接。

### C2：统一正式 `dsh-web` Runtime

落点：`packages/runtime_adapters/dsh_runtime/`、`apps/hub_worker/composition.py`、
`contracts/schemas/dsh_host_bridge.schema.yaml`。

要求：组合根只有一个 runtime 选择点；状态显示 runtime/provider/version；正式
试点显式选择 `dsh-web`；Web 失败只能进入 failed/degraded/research_only；SDK
保留为 candidate/fallback；三份持久化状态不混写。

BDD：同一任务在 live、Web 失败、Web 重启三场景中状态准确且不重复提交。

### C3：缺口驱动多轮补证

落点：`packs/crypto_macro/`、`infra/dsh/presets/`、`apps/research_mcp/`、
`packages/kernel/.../research_evidence.py`、`packages/query_views/research/`。

要求：Manager 首轮读取六类事实要求；调用可并行能力；一项失败不取消其他；
成功证据进入下一轮；达到 Sufficiency 才允许方向性 Forecast；停止必须有
`stop_reason`、已完成证据、未完成 gap 和复查条件。

BDD：至少一个 Search/Official/Market 能力成功时，Agent 必须继续下一轮，而不是
立即输出 no_trade；部分失败结果可见；硬缺口未闭合时只能 research_only。

### C4：真实能力准入和 canary

首批能力：`event.identity`、`policy_or_data_delta`、`expectation_pricing`、
`macro_transmission`、`crypto_spot_confirmation`、`derivatives_crowding`。

每个能力必须具备 endpoint、schema、权限、server-owned 时间戳、authority、
freshness、PIT、hash、revision、timeout、retry、cost、error code、fixture 和
真实 canary。一次只读、限时、临时目录 canary 通过后才进入 enabled；失败保留
provenance，不扩大范围。

### C5：报告、通知和可观测视图

DSH 展示过程和业务摘要；Desk 展示审计细节。报告必须包含事实引用、预期变化、
主/反因果链、成功/失败能力、未解决 gap、三个 Horizon、Gate、触发/失效条件和
复查时间。首版通知写本机 outbox/dry-run，内容包含 Run、报告、Session、失败和
复查链接；真实邮件/IM 另行 canary。

### C6：Outcome、Evaluation 和个人资产

每个正式 Run 保留 DSH JSONL、Hub Snapshot、Artifact/Forecast、Outcome/Evaluation、
Experience/FailurePattern 和版本 Promotion 记录。失败样本先入库，不能在线改
Prompt；新 Pack/Profile/Capability 需 replay/holdout/shadow 和 owner review 后才可
生成新版本，历史结果不可改写。

### C7：单机启动、恢复和交付运维

提供产品启动/停止脚本、API/Worker/MCP/DSH readiness、数据目录、备份/恢复、
重启/回调丢失/重复提交/磁盘检查。Live 与 replay 使用不同目录和命令；首期不引入
Postgres、Redis、Temporal、Kubernetes 或多用户设施。

## 7. SDD/BDD/TDD 执行 checklist

### 7.1 每张任务卡开始前

- [ ] 写清用户价值、输入、输出和非目标。
- [ ] 确认 canonical schema/event/Gate 是否变化。
- [ ] 标明 DSH、Hub、Domain Pack、Plugin 的所有权。
- [ ] 固化 timeout、retry、cost、error、permission、PIT 规则。
- [ ] 列出允许修改和禁止修改的目录。
- [ ] 生成 `tmp/task-context.md`，防止上下文压缩导致目标漂移。
- [ ] 跨模块不可逆决定写 ADR，未接受前不实现。

### 7.2 每张任务卡实现中

- [ ] 先写 BDD 场景和失败测试（Red）。
- [ ] 优先调用已有 LangGraph/DSH/LangChain/OpenAI/Pydantic/SQLite/Alembic API。
- [ ] capability 只通过 Manifest/Gateway，禁止研究 graph 直接 HTTP。
- [ ] Agent 候选和代码 Gate 分离。
- [ ] 失败保留 ErrorProvenance 和已完成证据。
- [ ] 不改写历史数据，不手改 codegen 镜像，不提交 secret。

### 7.3 每张任务卡完成前

- [ ] 通过 Red -> Green -> Refactor。
- [ ] 通过契约、单元、集成、回放和必要的真实 canary。
- [ ] 通过浏览器页面和移动视口检查；默认不展示 raw JSON。
- [ ] 更新模块 README、`IMPLEMENTATION_STATUS.md`、`ROADMAP.md`、`CURRENT_STATE.md`、`HANDOFF.md`、`CHANGELOG.md`。
- [ ] 记录未运行的检查和外部阻塞，不能写成 passed。
- [ ] 一个任务一个独立 commit；未经明确要求不 push。

## 8. 最终验收矩阵

### 必须通过的本地门

```bash
git diff --check
./.venv/bin/python tools/docs/check_module_docs.py
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/pytest -m "not live" -q
./.venv/bin/ruff check packages apps migrations tests tools
./.venv/bin/pyright
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
docker compose config --quiet
```

### 必须留存的产品证据

- [x] 单一启动器输出唯一 DSH URL，API/Worker/MCP/DSH 全部 ready。
- [x] Trader workspace 可发现、可提交、可查看 Run/Session/Report。
- [x] 正式 Run 真实走 DSH Web Host，重复提交和重启不重复创建/commit。
- [x] Search/Official/Market 真实能力经准入边界执行，成功与 timeout/stale 均保留。
- [x] 成功补证、部分失败、低权威/过期和无能力停止四类场景均有证据。
- [x] DSH 轨迹、Hub Ledger、Decision Desk 视图对同一 Run 一致。
- [x] 本机 outbox/dry-run 可产生可读通知，失败可重试。
- [ ] 30m/24h/72h Outcome/Evaluation 开始产生真实标签。
- [ ] 14 天或 20 个高影响事件观察数据已归档。
- [ ] owner 填写节省时间、可解释性和继续使用意愿。

### 交付判定

```text
E2-L 产品入口证据通过 -> research_only 进入观察窗口
E3 价值证据通过 -> promote / retain_baseline / stop
关键事实能力长期不可用 -> 只能交付 research_only 研究辅助
预测或收益无优势 -> retain/stop，不继续无限开发
需要 fork DSH/私有 API/第二套 Loop -> stop，回到 ADR
```

## 9. 版本升级和未来扩展

DSH 仍处于 alpha，不能承诺零修改替换。升级必须锁定上游 commit/package/image
digest，运行 Host/Client、Session、Trajectory、Tool、MCP、JSONL、Hub replay/PIT/Gate
回归，owner 审核后才 promote；失败继续旧版本并可 rollback。

第二领域只有在真实调用方出现后新增 Product Extension/Domain Pack。新领域必须
通过相同 Kernel、公开 Port、CapabilityManifest 和 Evaluation 套件验证复用，不能
提前复制金融 graph、表或前端。平台 Core 只抽取两个真实领域已经证明相同的语义。

## 10. 本阶段停止条件

出现以下任一情况立即停止写新功能并做阶段裁决：

- 已经达到主线目标且 owner 不节省人工查证时间；
- 真实来源成本、延迟、授权不可接受；
- DSH candidate 没有优于 Fixed 的证据；
- 同一根因连续两次补丁仍未解决；
- 需求不能落入现有 Product Extension、Domain Pack、Capability 或 Platform Port；
- 需要扩展到自动交易、多用户、ASR、PPT 或公共插件市场。

## 11. 本阶段交付物

```text
代码：C1-C7 任务提交和受控启动组合
契约：canonical schema/codegen 镜像和 manifest
文档：本 Stage Charter、模块 README、ADR、runbook、状态和 CHANGELOG
证据：本地质量门、浏览器截图/console、真实 canary、观察窗口和 owner review
资产：Run/Evidence/Artifact/Forecast/Outcome/Evaluation/Experience/版本血缘
```

本文件是本阶段实施约束。若它与旧聊天、临时草稿或中间产物冲突，以本文件、
accepted ADR、canonical schema 和受影响模块 README 为准；若与 accepted ADR 或
schema 冲突，停止实现并先修订决策。
