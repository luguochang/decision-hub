# Decision Hub 最终产品收口实施任务书

版本：`PRODUCT-CLOSEOUT-FINAL-2026-09-01.v1`
状态：`superseded for remaining execution / historical v1 evidence retained`
当前阶段：`historical / E2-L passed`
适用范围：单 owner、单机、只读 `Crypto Macro Trader` 试点。
最后更新：2026-09-01

> Owner 已接受本文的产品、架构、视图、可信证据和实施建议。Search attribution 与
> synthesis 安全降级工作已经按本文完成；2026-09-01 的后续 live Run 又暴露代码级预算
> 越界和首次 Session 关联竞态，后续已由 Pilot Ready v2 执行并最终通过 E2-L。当前状态由
> [产品交付控制书](PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 接管。canonical schema、
> accepted ADR、当前 Stage Charter
> 和受影响模块 README 仍是更高优先级事实源；若发生冲突，停止编码并先修正事实源。
> 此前多份产品总方案保留为历史和设计参考，不再各自扩大当前开发范围。

## 0. 本轮最终目标和完成定义

一句话目标：

```text
用户只打开官方 DSH Web，或由后台事件自动触发，就能看到 DSH 主动发现事实缺口、
调用已审计能力补证、由 Hub 可信 Gate 裁决、输出人可读报告并沉淀可回放资产；
事实或合成不可信时，系统保留已取得的可信事实并解释性停止，绝不伪造方向性结论。
```

本轮不是继续搭框架，也不是再迁移一次架构。完成只包含以下结果：

1. 修复真实 Run 暴露的 Search 来源与摘要错误绑定问题；
2. 保持 Evidence attestation 失败时的可信 Evidence 安全降级，不隐藏进度；
3. 从全新隔离的官方 DSH Web 完成一次真实主动研究主线；
4. DSH 业务卡与 Decision Desk 对同一 Run 显示一致、可读、可追溯的状态；
5. 全量测试、浏览器、恢复、文档和非敏感验收证据全部闭合。

达到上述门后立即停止功能扩张，产品进入 `pilot_ready / research_only`。预测价值、
准确率和个人收益只能在后续 E3 的 14 天或 20 个高影响事件观察中证明；本轮不得宣称。

## 1. 当前真实状态

### 1.1 已完成且不重做

| 范围 | 当前事实 |
|---|---|
| R0/R1/R2/R2-L | 工程、回放、账本、Workbench、Outcome/Evaluation 和恢复基线已完成 |
| DSH Native Core | 官方 DSH Web、Host/Client Plugin、Session/Trajectory/JSONL 和 Hub bridge 已贯通 |
| 双层循环 | DSH 是唯一内层 Agent Loop；LangGraph 只管理外层 durable lifecycle/evidence round |
| Capability Gateway | MCP、manifest、权限、PIT、freshness、hash、成本、timeout 和错误 provenance 已存在 |
| Live capability | Official Feed、FRED、CoinEx spot/derivatives 和 Responses web search 独立 canary 已成功过 |
| 安全失败 | timeout、cancel、partial、insufficient、stale、Provider failure 不再伪装成 `no_trade` |
| 合成失败降级 | 已有可信 Evidence 时，unattested/invalid synthesis 可降级为 evidence-only 结果；丢弃因果链和 horizons |

这些能力只证明工程基础存在，不等于正式产品 live 主线已经通过。

### 1.2 当前真实缺陷

真实 Run：`run_ef2b6ab98ae34aa48d42195d49319303`，Hub API 位于隔离实例 `:8210`。
该 Run 证明 DSH 会在同一 Session 主动调用 Official、Market 和 Search 能力，并保留数十条
Evidence；但也暴露了一个必须从根修复的可信性缺陷：

```text
Responses action.sources 返回多个发现 URL
  + response.output_text 是一整段模型生成摘要
  -> 旧实现把同一整段摘要复制到每个 URL
  -> source_url 与 excerpt 中真正引用的页面不一致
  -> 不同 hostname 可能被 Sufficiency 错算成独立来源
```

实际样本中，关于 2026-08-28 Warsh 讲话的同一摘要被挂到
`warsh20260828a.htm`、`powell20190604a.htm`、旧 Bowman PDF 等不同 URL；这不是格式问题，
而是 Evidence lineage 错误。修复前，即使页面看起来“证据很多”，也不能把 Search 结果
作为可信的独立来源数量。

### 1.3 当前产品状态

```text
Fixed runtime          = active baseline
DSH runtime            = candidate / shadow
pilot_ready            = false
pilot_usable           = false
E3 value observation   = not started
automatic trading      = forbidden
```

## 2. 最终产品形态和用户视图

### 2.1 一个产品入口，两个视图层

```text
官方 DSH Web：唯一日常工作台
  ├─ Chat/Session/History/Plan/Tool/Skill/Subagent/Trajectory：完全复用 DSH
  ├─ Crypto Macro Trader：默认业务 Workspace/Profile
  └─ Decision Hub 业务卡：研究状态、事实覆盖、报告、失败、动作和 Desk 链接

Decision Desk：管理、审计和资产后台
  ├─ Operations/Readiness/Worker/Capability/Outbox
  ├─ Run Inspector/Evidence/PIT/Trace/Gate
  └─ Forecast/Outcome/Evaluation/Experience/FailurePattern/Promotion
```

用户不需要手工打开 MCP 页面、拼 API、运行 worker 或在两个聊天入口之间复制文本。
Decision Desk 不是第二个问答助手；DSH Web 也不复制 Hub 的业务账本。

### 2.2 DSH Web 必须呈现的业务视图

```text
┌ Crypto Macro Trader ───────────────────────────────────────────┐
│ 状态：排队 / 研究中 / 部分成功 / 仅研究 / 已提交 / 失败 / 取消 │
│ Run 与 Session：可互相定位；显示轮次、调用数、deadline、成本  │
├ 当前研究计划 ──────────────────────────────────────────────────┤
│ 已确认事实 | 正在调用的能力 | hard gaps | 下一步 | 停止原因     │
├ 证据覆盖 ──────────────────────────────────────────────────────┤
│ requirement / authority / source / PIT / freshness / conflict │
├ 人可读报告 ────────────────────────────────────────────────────┤
│ 事件变化 | 主因果链 | 最强反方 | 30m/24h/72h | Trigger         │
│ Invalidation | Gate | 缺失事实 | 下次复查时间                 │
├ 失败与降级 ────────────────────────────────────────────────────┤
│ error_code / origin / cause / retryable / 已保留 Evidence 数   │
├ 动作 ──────────────────────────────────────────────────────────┤
│ 取消 | 重试为 child Run | 复查 | 打开 Decision Desk           │
└────────────────────────────────────────────────────────────────┘
```

`insufficient`、`stale`、synthesis attestation failure 和 Provider failure 必须是可读终态，
不能无限 loading、显示空报告，或自动改写为 30m/24h/72h `no_trade`。

### 2.3 后台主动运行

人工输入和后台触发共用同一条产品主线：

```text
人工文本 / 新闻 / 日历 / 事件发现
  -> TextEnvelope/Event admission + idempotency
  -> durable Run + DSH Session link
  -> DSH Agent Loop 自主规划并调用 MCP capability
  -> Gateway 即时保存 Trace/Result/Evidence
  -> deterministic Sufficiency 重新计算 gaps
  -> 有缺口且预算允许：同一 Session 下一 evidence round
  -> 充分或有界停止：提交 synthesis candidate
  -> Evidence attestation + code Gate
  -> 人可读报告 / research_only / failed / cancelled
  -> Artifact/Outbox/Outcome/Evaluation/Experience
```

后台 worker 负责调度、lease、heartbeat、重启恢复和通知；DSH 负责推理和工具选择；
Hub 负责可信事实与发布。任何一层都不能替代另外两层。

## 3. 最终架构和不可越界的所有权

| 组件 | 正式职责 | 明确禁止 |
|---|---|---|
| 官方 DSH Web/Harness | 唯一内层 Agent Loop、模型、Session、Tool/Skill/Subagent/MCP、Trajectory、JSONL、compaction | Hub 账本、PIT、Gate、Forecast、交易权限 |
| DSH Native Plugin | typed intake、Run/Session link、业务状态/报告卡、命令桥和 Desk deep link | fork 上游 UI、第二套聊天、第二套 loop、写账本 |
| Hub Kernel | Event、Run、Evidence、PIT、Sufficiency、Gate、Artifact、Forecast、Outcome、Evaluation、Outbox | 导入 DSH/LangGraph/Provider/UI，调用模型 loop |
| LangGraph | Hub 外层 checkpoint、lease、recovery、bounded evidence round、幂等提交 | ReAct/Supervisor/tool selection、Provider client、发布裁决 |
| Research MCP/Gateway | capability manifest、权限、schema、来源、鲜度、PIT、hash、预算、timeout/retry 和错误分类 | 修改 Gate、伪造 Evidence、自动安装插件 |
| Crypto Macro Pack | doctrine、EvidenceRequirement、source ladder、Role/Profile、Gate 参数、评测和 fixture | 通用账本、运行时实现、前端状态机 |
| Decision Desk | typed Query/View、运维、审计、资产、owner 命令 | 第二聊天、直读 SQL/DSH JSONL/LangGraph state |

唯一允许的两层循环：

```text
DSH 内层智能循环：计划 -> 选能力 -> 看结果 -> 找缺口 -> 再行动 -> 候选/停止
Hub 外层产品循环：触发 -> Run -> durable round -> Gate -> 通知 -> Outcome -> Evaluation
```

不允许在 LangGraph、API、worker 或业务插件内再写一套 Supervisor/ReAct/tool loop。

## 4. 插件、业务隔离和未来复用

插件不是仅优化界面的组件。正式能力分四类：

| 类型 | 当前实现 | 未来扩展方式 |
|---|---|---|
| DSH Native Plugin | `extensions/dsh/decision-hub/` | 使用官方 Host/Client/Tool/Skill/MCP/Hook/UI seam；跟随 upstream lock 升级 |
| Product Extension | 当前交易研究输入、输出、命令、视图和资产 | PPT/A 股/美股出现真实需求时各建独立 Extension |
| Domain Pack | `packs/crypto_macro/` | 每个领域独立 doctrine/evidence/profile/gate/eval/fixture |
| Capability Plugin | Search/Official/Market/Notification adapter + manifest | 先审计、deny-by-default、typed schema、canary 后才启用 |

交易员角色不是改 DSH 源码得到的。它由 `Crypto Macro Domain Pack + DSH Profile/Skill +
已授权 Capability + Hub Extension View` 组合出来。ASR 后续只实现：

```text
Audio/Live Stream -> AsrProviderPort -> TextEnvelope -> 当前完全相同的研究主线
```

因此 ASR 可替换本地或云模型，不污染本轮文本核心。只有 PPT 等第二个真实调用方出现后，
才提取两个领域确实共享的 Platform Core 接口，禁止先建空泛 `common/utils`。

## 5. 代码结构和本轮修改地图

```text
apps/
  hub_api/                 command/query/callback；不执行长推理
  hub_worker/              research/realtime/evolution/outbox durable worker
  research_mcp/            DSH 到 Capability Gateway 的唯一正式入口
  decision-desk/           管理、审计和资产后台
packages/
  kernel/                  领域无关账本、Sufficiency、Gate 和公开 Port
  orchestration/langgraph/ 外层生命周期/checkpoint/recovery
  runtime_adapters/dsh_runtime/ 官方 DSH Web/SDK adapter 与结果认证
  provider_adapters/search/ Responses Search transport 和引用归因
  provider_adapters/research/ Search -> EvidenceCandidate 薄适配
  workbench_adapters/      MCP/capability/durable binding
  query_views/             DSH/Desk 人可读 DTO
  evals/                   replay/holdout/shadow/outcome/promotion
contracts/schemas/         canonical YAML；Python/TypeScript 只能 codegen
packs/crypto_macro/        首个领域资产
extensions/dsh/decision-hub/ 官方 DSH 插件
infra/dsh/                 upstream lock、安装和单命令启动
```

依赖只能向内：`apps -> packages -> contracts`。Kernel 不依赖 Harness；前端只消费
Query/View + Zod；模块间只走公开 Port/契约。当前 Search 修复优先不改 canonical schema：
`SearchEvidence.snippet` 改为明确 citation 绑定的原文片段即可。只有实现证明必须同时保存
模型摘要和逐源引文时，才另立 schema/ADR/migration，不能手改 generated 模型。

## 6. 可信 Evidence 和 Search 归因规则

以下规则是本轮强制产品合同：

1. `web_search_call.action.sources` 只是发现元数据，不能单独成为 claim-bearing Evidence；
2. 只有 Responses 输出中的显式 `url_citation` 才能把一个具体文本 span 绑定到 URL；
3. citation URL 必须同时存在于本次 tool `action.sources` 集合，否则 fail-closed；
4. citation offset 必须在同一 output text 边界内，span 不能为空，URL/offset 非法即
   `search_output_invalid`；
5. 没有可用 citation 时返回稳定的 `search_no_attributed_sources`，不能使用整段摘要兜底；
6. URL 必须 canonicalize：scheme/host 小写、移除 fragment 和常见追踪参数、规范默认端口；
7. 相同 canonical URL 只产生一条 SearchEvidence；同一 citation span 绑定多个 URL 时
   保守只保留一条，不能满足多个独立来源；
8. Search `authority` 始终是 `search_derived`，即使 hostname 是 `.gov` 也不能自动晋升；
9. 独立来源按保守 publisher/source identity 计数，同一 hostname 的不同页面不算多个来源；
10. Official/Market adapter 的精确事实优先于 Search，Search 主要用于发现、交叉核对和
    补充线索；精确收益率、价格、funding、OI、basis 不从摘要猜测。

这些规则不降低 exact Evidence ID attestation、authority floor、PIT、freshness 或 Gate。

## 7. 实施任务卡和顺序

### TASK-00：文档与任务基线

允许路径：本文件、`INDEX.md`、`docs/product/README.md`、`docs/context/`。
验收断言：只有一个当前任务入口；现状、目标、非目标、代码地图、视图和 Checklist 自洽。
状态：`in progress`。

### TASK-01：Search citation attribution（先 TDD）

允许路径：

```text
tests/capabilities/test_search_capability.py
tests/research/test_capability_gateway.py
packages/provider_adapters/search/openai_responses.py
packages/provider_adapters/README.md
docs/decisions/ADR-0017-search-evidence-citation-attribution.md
```

实现要求：解析 output message content annotations；按 exact span 和 URL 建 Evidence；
action.sources 无 annotation 时 fail-closed；canonical URL 与重复 span 保守去重；保持预算、
取消、domain allowlist 和 Provider error 语义。

禁止：抓取所有 URL 正文作为隐式补救、模糊匹配 URL、让模型重写 citation、引入第二
Search client、使用真实网络让普通单测通过。

### TASK-02：来源独立性和 Sufficiency 回归

允许路径：

```text
tests/kernel/test_sufficiency.py
tests/research/
packages/provider_adapters/research/web_search.py
packages/kernel/decision_hub_kernel/decision/sufficiency.py（只有测试证明需要时）
```

验收断言：同一站点/同一 citation/URL alias 不虚增独立来源；不同明确 citation 和不同
source identity 才能贡献多个来源；Search-looking-official 仍不能满足 official floor。

### TASK-03：安全降级与产品投影回归

复核已有实现，不默认重写：

```text
packages/runtime_adapters/dsh_runtime/result_mapper.py
packages/runtime_adapters/dsh_runtime/web_runtime.py
packages/orchestration/langgraph/graphs/agentic_research_graph.py
packages/query_views/research/
extensions/dsh/decision-hub/
apps/decision-desk/
```

验收断言：有效 Evidence + invalid/unattested synthesis 返回 `degraded evidence-only`；
`causal_case=None`、`horizons=[]`、`synthesis_failure_code` 可见；无可信 Evidence 时仍失败；
下一真实 evidence round 不被 repair 占用；DSH 与 Desk 显示一致。

### TASK-04：全量工程质量门

专项测试先通过，再运行第 9 节完整命令。任何失败必须区分本轮回归、既有脏工作树问题、
环境阻塞和真实 Provider 限制；禁止删测试、放宽断言或删除历史数据来制造通过。

### TASK-05：全新隔离 live 产品验收

使用新的 Compose project、独立数据卷和新的 DSH session root：

```text
单命令启动
  -> readiness/preflight
  -> 官方 DSH Web 创建/选择 Crypto Macro Trader Session
  -> 从页面 typed intake 提交真实文本
  -> DSH 主动调用 Official/Market/Search MCP
  -> Hub durable Evidence/Sufficiency/Gate
  -> DSH 业务卡与 Decision Desk 同一 Run 终态
  -> 桌面/375/768/1024/1440 截图与 console 检查
```

必须验收 success 或 `research_only` 的可信主线，以及 partial/insufficient、synthesis failure、
cancel、timeout/restart 中与本 revision 相关的安全路径。旧端口、旧 bundle、replay、直接
curl 创建的另一个 Session 或模型自述不能替代官方 Web 证据。

### TASK-06：最终收口和停止

同步 `IMPLEMENTATION_STATUS`、`CURRENT_STATE`、`CURRENT_DECISIONS`、`HANDOFF`、
`ROADMAP`、`CHANGELOG`、模块 README 和验收记录。只有代码 readiness 返回 true，才能
写 `pilot_ready=true`。完成后停止工程扩张并进入 E3 owner 使用观察，不自动 Promotion。

## 8. BDD/TDD 验收场景

| 场景 | Given / When | Then |
|---|---|---|
| 单一 citation | 两个 action.sources，仅一个 `url_citation` | 只产生被引用 URL 的一条 Evidence |
| 无 citation | 有全局 output_text 和多个 sources，无 annotation | `search_no_attributed_sources`，无 Evidence |
| 多 citation | 两个不同 span 分别绑定两个 source URL | 产生两条 source-specific Evidence |
| 重复 claim | 同一 span 绑定多个 URL | 保守去重，不能满足 `minimum_independent_sources > 1` |
| URL alias | tracking query/fragment/default port 变体 | canonical 后只保留一条 |
| 来源不一致 | annotation URL 不在 action.sources | `search_output_invalid`，无 Evidence |
| offset 非法 | 越界、倒序或空 span | `search_output_invalid` |
| authority | Search citation 指向 official-looking hostname | 仍为 `search_derived`，不能满足 official floor |
| 合成失败 | capability Evidence 合法，synthesis ID 不合法 | Evidence 保留；无 causal/horizon；degraded 可读 |
| 纯运行失败 | 无可信 Evidence，模型/Provider 失败 | failed；不能伪造 degraded report |
| 部分成功 | Official/Market 成功，Search timeout/429 | 成功 Evidence 保留，失败 provenance 可读，Gate 不误发布 |
| 继续补证 | hard gap + 预算/能力可用 | 同一 DSH Session 进入下一 evidence round |
| 有界停止 | deadline/tool/cost/round budget 到达 | research_only/failed 可解释终态，不无限循环 |
| 取消/恢复 | owner cancel 或 worker/Web 重启 | cancelled 或 lease/checkpoint 恢复；最多一个 Artifact/Outbox |
| 页面一致性 | 同一 Run 在 DSH 和 Desk 打开 | status、coverage、failure、report 和 trace link 一致 |

所有实现遵循 `Red -> Green -> Refactor`。固定时钟、输入、Provider payload 和随机性边界；
普通 CI 不触网、不读真实密钥。

## 9. 完整质量门

### 9.1 文档、契约和静态检查

```text
[ ] git diff --check
[ ] python -m tools.docs.check_module_docs
[ ] python -m tools.contract_codegen check
[ ] ruff check .
[ ] pyright
[ ] canonical schema 若有变更已 codegen，generated 镜像未手改
```

### 9.2 后端、编排和持久化

```text
[ ] pytest -m "not live"
[ ] Search attribution/URL canonicalization/independence 专项测试
[ ] Evidence attestation/evidence-only fallback/下一 generation 专项测试
[ ] LangGraph checkpoint/lease/cancel/resume/idempotent commit
[ ] migration upgrade/backup/restore/integrity
[ ] capability timeout/429/5xx/schema/PIT/authority/conflict 错误分类
```

### 9.3 前端、插件和构建

```text
[ ] pnpm --dir extensions/dsh/decision-hub test -- --run
[ ] pnpm --dir extensions/dsh/decision-hub build
[ ] pnpm --dir apps/decision-desk test
[ ] pnpm --dir apps/decision-desk build
[ ] docker compose config --quiet
[ ] DSH/Desk 空态、研究中、partial、insufficient、failed、cancelled 可读
```

### 9.4 真实产品验收

```text
[ ] 全新隔离实例单命令启动，唯一 DSH Web URL 可打开
[ ] typed intake -> Run -> 同一 DSH Session -> MCP -> Evidence -> Gate -> Report
[ ] Search Evidence 的 source_url 与 exact citation span 一致
[ ] DSH 至少完成一次“识别缺口 -> 调能力 -> 重新评估”
[ ] DSH 业务卡与 Decision Desk 同一 Run 状态一致
[ ] 可信失败保留 Evidence/provenance，不显示错误方向性 Forecast
[ ] 桌面和 375/768/1024/1440 页面无覆盖、溢出、空白或 console 未处理错误
[ ] 保存 revision/hash、run/session id、能力/模型/pack/schema 版本、延迟、成本和截图
[ ] readiness 由代码返回 `pilot_ready=true`
```

## 10. 全局开发约束

1. 中文长期文档；临时探索进入 `tmp/`，不把聊天或推理草稿当事实源；
2. canonical schema 单一来源 + codegen，禁止 Python/TypeScript 双写；
3. 新行为先写 schema/事件/Gate/BDD，再写失败测试，最后实现；
4. 跨模块、不可逆、协议或可信含义变化必须写 ADR；
5. Agent 只能提交候选，代码 Gate 唯一发布裁决，无自动交易/扣费/写账本权限；
6. 三时间戳和 cutoff 是 PIT 铁律，历史 Run/Evidence/Artifact/Forecast/Outcome 只增不改；
7. 跨边界 Pydantic/Zod 运行时校验，禁止裸 `dict/Any` 和代码硬引用；
8. 复用 DSH、LangGraph、OpenAI SDK、Pydantic、SQLAlchemy/Alembic 的现有能力；
9. 禁止第二 Agent Loop、第二账本、第二聊天入口、第二同名 DTO 或自建 Provider 协议；
10. 密钥只在 ignored 环境文件/secret store，不能进入代码、文档、日志、JSONL 或 fixture；
11. 不 fork/复制 DSH 上游；升级走 `infra/dsh/upstream.lock.json`、hash 和官方插件 seam；
12. 不新增 Redis/Kafka/Celery/Temporal/DBOS/Postgres/Kubernetes，除非真实需求和 ADR 出现；
13. 同一根因两次最小修复仍失败，或需要放宽 Evidence/PIT/Gate/authority，立即停止并复盘；
14. 每张任务卡完成后同步模块 README、状态、CHANGELOG 和测试证据；
15. 保留当前 dirty worktree，不回退、不覆盖、不提交、不 push，除非 owner 另行明确授权。

## 11. 最终 Checklist 和阶段停止线

### A. 文档和架构锁定

- [x] Owner 接受 DSH-first、Hub 控制面、LangGraph 外层生命周期和插件四层模型；
- [x] 最终用户入口、两个视图层、后台主动运行和个人资产模型已明确；
- [x] 本轮唯一任务、非目标、代码路径、BDD/TDD 和质量门已写入本文；
- [ ] Search Evidence 可信含义写入 ADR-0017 和模块 README；
- [ ] 所有当前状态文档消除“live 已通过/未通过”的矛盾。

### B. 可信事实与安全失败

- [ ] action.sources 不再直接生成 claim-bearing Evidence；
- [ ] citation span 与 source URL exact binding；
- [ ] URL alias/重复 span/同站点不虚增独立来源；
- [x] unattested synthesis 保留可信 Evidence 并丢弃所有模型语义；
- [ ] 上述语义有单元、集成、编排和 Query/View 回归证据。

### C. 产品主线

- [ ] 官方 DSH Web 是唯一入口，Crypto Macro Trader 业务身份可见；
- [ ] 人工文本和后台触发共用同一 durable product loop；
- [ ] DSH 会主动补证，而不是证据不足立即停止；
- [ ] Sufficiency/Gate fail-closed，partial/insufficient/failure 人可读；
- [ ] 报告、通知和 Outcome/Evaluation/Experience 资产链可追溯。

### D. 工程与真实验收

- [ ] 第 9 节全部质量门通过；
- [ ] 全新隔离实例官方 Web live 主线通过；
- [ ] 当前 revision 桌面/窄屏截图和 console 证据通过；
- [ ] 状态、CHANGELOG、HANDOFF 和验收记录同步；
- [ ] 代码 readiness 返回 `pilot_ready=true`，然后立即停止功能扩张。

### E. 下一阶段，不属于本轮代码交付

- [ ] Owner 明确进入 E3；
- [ ] 至少 14 天或 20 个高影响事件的同 PIT 观察；
- [ ] 记录首证据延迟、最终延迟、成本、覆盖、失败率、人工复核时间和 usefulness；
- [ ] 30m/24h/72h 到期后记录 Outcome/Brier/方向/净收益，不提前填标签；
- [ ] 最终只允许 `promote`、`retain_baseline` 或 `stop`；
- [ ] E3 前不进入 ASR、PPT、第二领域、多用户或自动交易。

## 12. 当前是否还有未决问题

没有阻止本轮实施的架构或产品决策。Owner 已接受本文建议，可以按 TASK-01 至 TASK-06
继续。剩余不确定性属于需要通过代码和真实验收验证的外部事实：中转是否完整返回
Responses `url_citation` annotations、实时来源是否在 deadline 内可用、当前页面在多视口
是否可读。它们不能靠文档假设通过；如果 Provider 不返回可归因 citation，正确结果是
`search_no_attributed_sources`，随后优先使用 Official/Market 能力或解释性停止，而不是
回退到不可信摘要。

本轮最终停止句：

> 当且仅当可信 Search 归因、Evidence attestation 安全降级、官方 DSH Web live 主线、
> DSH/Desk 一致视图、全量质量门和当前 revision 验收证据全部通过，标记
> `pilot_ready=true / research_only` 并停止开发；随后由 Owner 真实使用进入 E3，产品
> 价值未达门则 retain baseline 或 stop，不用继续堆代码掩盖。
