# Decision Hub 产品现状、架构与交付缺口审计

版本：`PRODUCT-STATE-AUDIT-2026-09-02.v1`
状态：`事实审计 / 不新增实现授权`
审计日期：2026-09-02（Asia/Shanghai）
适用产品：`Decision Hub + 官方 DSH Web + Crypto Macro Trader`

> 本文是独立的现状审计和问题回答，不是新的开发入口。它不替代
> [产品交付控制书](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)、canonical
> schema、ADR 或模块 README。若本文与更高优先级事实源冲突，先停止实现、修订事实源，
> 不用局部代码补丁掩盖冲突。

## 0. 先给结论

### 0.1 最初产品不是问答助手

最初锁定的产品是一个由官方 DSH Harness 驱动的、持续发现事件并主动补证的研究智能体：

```text
事件/日历/新闻/人工文本
  -> durable Run admission
  -> 官方 DSH Web Session
  -> DSH Agent Loop：发现缺口 -> 选择授权能力 -> 读取结果 -> 继续补证
  -> Hub Evidence/PIT/Sufficiency
  -> 代码 Gate
  -> 人可读报告、research_only 或解释性停止
  -> outbox 通知
  -> Outcome/Evaluation/FailurePattern/Experience
```

它与一次 LLM 调用的区别是：任务可以由后台发现和调度；DSH 在同一有状态 Session 中自主
选择已经授权的 Tool/Skill/Subagent/MCP；缺口、失败和停止理由被持久化；结果可以回放和
评测。它也不是“无限浏览互联网直到模型满意”：所有外部能力都必须先进入
`CapabilityManifest`，并受 deadline、调用次数、轮数、子 Agent 数和成本预算约束。

### 0.2 当前不是正式交易产品

当前可准确称为：**单 owner、单机、只读、research-only 的工程试点入口**。

已经通过的是工程组合和主线可审计性：官方 DSH Web、插件、Hub Run、MCP、证据、Gate、
报告、重启恢复、后台复查和质量门。尚未被证明的是实时市场事实的长期稳定供给、分钟级
事件窗口覆盖、预测准确率、盈利、低延迟和自动交易。因此：

- `pilot_ready=true / pilot_usable=research_only` 只表示允许进入观察期；
- `Fixed` baseline 仍是 active；DSH Research Runtime 仍是 `candidate/shadow`；
- 真实缺口会显示为 `insufficient_sources`、`research_only`、`degraded` 或 `rejected`；
- 这不是系统失去智能，而是证据 Gate 正确拒绝了不可审计的方向性结论。

### 0.3 目前唯一合理的后续阶段是 E3

E2-L 主线已经通过，下一阶段不是继续堆页面、换框架或无限增加插件，而是至少 14 天或
20 个高影响事件的前瞻观察。E3 只回答三个问题：

1. 真实来源是否在事件截止时间内稳定返回足够新鲜、独立、可追溯的事实；
2. DSH candidate 相对于 Fixed baseline 是否降低人工查证时间并改善可评测指标；
3. 运行成本、延迟、失败率和 owner usefulness 是否值得继续投入。

窗口结束必须写出 `promote`、`retain_baseline` 或 `stop`，不能因为“还可以继续优化”而
无限延期。

## 1. 初始设计、当前实现和后续计划

### 1.1 初始设计的长期资产

最初方案希望沉淀的不是某一次 BTC 报告，而是可以迁移到第二个领域的产品资产：

| 资产 | 作用 | 当前落点 |
|---|---|---|
| Canonical Contract | 跨 Python/TypeScript/DSH 的稳定边界 | `contracts/schemas/` + codegen |
| Event/Run/Ledger | 自动触发、幂等、重启恢复、审计 | `packages/kernel`、SQLite/Alembic |
| Evidence/PIT | 防止未来信息泄漏和无来源结论 | Kernel Evidence/Gateway |
| Capability Manifest | 外部搜索、官方资料、行情能力的准入与权限 | `packs/crypto_macro/evidence`、MCP |
| Domain Pack | 领域事实要求、根因链、Gate、评测和角色 | `packs/crypto_macro/` |
| DSH Session/Trajectory/JSONL | Agent 的会话过程、工具调用和轨迹 | 官方 DSH Web/Session |
| Artifact/Forecast/Outcome | 可读报告、到期结果和后验评测 | Kernel/Query View |
| FailurePattern/Experience | 失败模式、经验候选、回放和 shadow | `packages/evals`、`evolution.py` |
| Trace/Provenance | 可观察性、逐能力失败和恢复定位 | Research trace/query views |

这些资产被设计成“换模型、换 DSH 版本、加 Domain Pack 后仍保留历史”，而不是把业务逻辑
写死在某个 Prompt 或某个前端页面里。

### 1.2 当前已经存在的实现

| 范围 | 真实状态 | 不应误解为 |
|---|---|---|
| R0 文本核心 | `done`，文本到 Snapshot、Agent、Gate、Artifact/Forecast/Outcome 的离线链路 | 实时网络已可靠 |
| R1 来源/调度 | `done (offline/replay)`，来源适配、事件准入、scheduler、worker、outbox 有骨架和测试 | 金十等商业实时数据授权已获得 |
| R2 Workbench/Evolution | `done (offline/local)`，审计页面、评测、候选、回滚和资产接口存在 | 自动进化或自动 Promotion 已开启 |
| DSH Native Core | `engineering verified`，官方 Web、Host/Client Plugin、Session/JSONL link、callback、恢复已验证 | 已 fork 上游或承诺 alpha 版本零适配替换 |
| Agentic Research | `candidate / retain_baseline`，DSH 内层研究循环接入 Hub 外层生命周期 | DSH 已成为 active runtime |
| E2-L | `passed / research_only`，真实主线完成过补证、Gate、报告和后台复查 | 研究结论已经足够交易 |
| E3 价值观察 | `not completed` | 预测准确率或盈利已证明 |

### 1.3 后续计划边界

```text
E3 前瞻观察（当前唯一下一阶段）
  -> 真实事件/来源质量
  -> 完成/失败/缺口/延迟/成本/人工时间记录
  -> PIT Outcome + Brier/方向/净收益等评测
  -> owner usefulness 复核
  -> promote | retain_baseline | stop
```

E3 期间禁止新增 ASR、PPT、A 股、美股、自动交易、公共插件市场、第二套 Agent Loop 或
Redis/Temporal/微服务。只有真实数据证明某个具体缺口，才通过 ADR 新增一个 capability
或修复一个现有边界。

## 2. DSH Harness、WebSearch 和 Loop 的真实情况

### 2.1 目前是否真正借助 DSH

是。官方 DSH Web 是唯一用户交互主壳，官方 DSH Agent Loop 是唯一内层推理循环；Hub
通过官方 Host/Client Plugin 和 MCP seam 接入，不复制 DSH Web，不在 LangGraph 里重写第二
套 ReAct。当前固定上游为 `0.1.2-alpha.2`，并由 `infra/dsh/upstream.lock.json` 锁定。

DSH 负责：

- Chat、Session、History、Trajectory、JSONL、compaction；
- Manager/Supervisor 对 Tool、Skill、Subagent、MCP 的会话内调度；
- 在同一 Session 内根据结果决定继续、修复、降级或停止；
- 向 Hub adapter 提供结构化 trace、tool result 和 session identity。

Hub 负责：

- 事件发现、任务 admission、幂等、lease、deadline、重启恢复和通知；
- Evidence、PIT、来源权威性、三时间戳、hash、冲突和 Sufficiency；
- 代码 Gate、Artifact/Forecast/Outcome、评测和个人资产；
- 外部 capability 的 allowlist、schema、预算、超时、错误分类和 provenance。

### 2.2 WebSearch 是否会自动无限补证

目前不会，也不应该无限制地做。实际循环是：

```text
DSH 读取目标和当前 hard gaps
  -> 选择 CapabilityManifest 中允许的能力
  -> Gateway 校验权限/schema/PIT/鲜度/预算
  -> 成功结果写入 Evidence，失败写入 ErrorProvenance
  -> 重新计算 coverage/gaps
  -> 仍有关键 gap 且预算允许：继续同一 Session
  -> deadline/预算/无进展：保留真实缺口并安全停止
```

当前有界参数包括：`max_evidence_rounds`、`max_tool_calls`、`max_subagents`、总 deadline、
单工具 timeout、模型 step timeout、structured repair 次数和估算成本。这样做的原因是：

1. 任意网络搜索不能保证来源权威、时间可追溯或结果可复现；
2. 无界循环会在 Provider 异常时消耗费用、重复证据或让模型自行扩大权限；
3. 交易研究必须能解释“为什么停止”，不能把找不到数据偷偷改写成 `no_trade`。

所以用户看到 `insufficient_sources` 并不代表 DSH 没有 loop，而代表 loop 在既定边界内
做过补证后仍缺数据。当前系统必须把“已尝试哪些能力、哪些超时、还差哪些事实”显示给用户。

### 2.3 当前接入了哪些检索/事实能力

当前代码中有以下 typed adapter 和 replay/canary：

| 能力 | 当前定位 | 主要限制 |
|---|---|---|
| `web.search` | OpenAI-compatible Search/Responses adapter | 曾发生 `research_capability_timeout`；不是专门的实时行情数据库 |
| `official.macro` | FRED/官方宏观资料 typed capability | FRED 主要日频，不能替代分钟级事件窗口 |
| `market.cross_asset` | 交叉资产只读研究 adapter | 数据新鲜度和事件前后窗口仍需 E3 证明 |
| `market.crypto_derivatives` | BTC spot/衍生品研究 adapter | 只有快照时不能证明历史基差、OI、funding、清算序列 |
| `replay.research` | 固定 fixture transport | 仅用于回放、CI 和诊断，禁止混入 live 产品 |

目前没有把陌生 GitHub 插件自动装入生产。成熟的开源工具、数据 SDK 或 MCP Server 可以
作为候选实现，但必须经过：

```text
候选插件/SDK
  -> typed CapabilityAdapter
  -> manifest/权限/成本/来源声明
  -> 独立 canary（live + failure）
  -> PIT、鲜度、hash、attribution 和 replay
  -> E3 价值证据
  -> owner review 后才允许进入 allowlist
```

不能仅因为某个项目在 GitHub 上存在，就让 DSH 自行安装、执行任意网络请求或把其输出当成
可信 Evidence。当前缺的不是再写一个搜索 Prompt，而是经过授权且能提供分钟级历史窗口、
期货隐含概率、清算/资金流和可靠时间戳的具体数据能力；这些可能涉及第三方额度或收费，
必须单独确认，不在本审计中默认采购。

## 3. 自进化目前如何体现

当前“自进化”是受控的工程与评测闭环，不是模型自动修改自身，也不是失败后自动改 Prompt：

```text
Failure / Outcome / Owner Feedback
  -> FailurePattern / Experience
  -> immutable Candidate Artifact
  -> replay
  -> holdout
  -> shadow comparison
  -> Experiment Result
  -> owner review
  -> promote | retain_baseline | stop
```

当前已有：

- `FailurePattern`、`Experience`、dataset 和 candidate artifact 的持久化接口；
- replay、holdout、12-case 对照、Outcome/Brier/方向/净收益比较；
- DSH candidate 与 Fixed baseline 的 shadow 记录；
- locked rollback 和 active pointer 的保护；
- Agent/worker 无权自动 Promotion 或修改历史账本。

当前没有：

- 根据一次失败自动改写 Domain Pack、Gate 或系统 Prompt；
- 没有 Outcome 就宣称“策略学会了”；
- 没有 owner review 就把 candidate 切为 active；
- 预测准确率、盈利能力或长期 usefulness 的 E3 证据。

这套设计的个人资产价值在于：每个事件的事实、决策、失败、结果和评测都能沉淀为可回放
数据集；以后增加 PPT 或其他领域时，复用的是 Run/Evidence/Trace/Evaluation/Asset Port，
而不是复制一套金融 Prompt。

## 4. 调度、日历、实时触发和主动页面

### 4.1 调度层是否存在

存在，且是 Hub 外层产品职责，不由 DSH Chat 临时触发：

| 进程/模块 | 职责 | 当前证据 |
|---|---|---|
| `hub-realtime-worker` | 轮询授权来源、日历/新闻/行情，维护 cursor、去重、revision、健康 | fixture/replay 和本机进程测试 |
| `hub-research-worker` | 领取 durable Run，调用 DSH candidate/replay runtime，写入 Trace/Evidence/Result | worker/recovery 测试和真实 Run |
| `hub-evolution-worker` | 处理候选、评测和 Evolution Job | eval/evolution 测试 |
| scheduler | 到期复查、Run admission、lease、retry、child Run | 自动 child recheck Run 证据 |
| outbox | 通知记录、重试和本机 channel adapter | outbox 测试；远程通知未承诺 |
| LangGraph checkpoint | 外层生命周期的 checkpoint 和跨进程恢复 | checkpoint/recovery 测试 |
| DSH Session JSONL | 内层 Agent 会话、tool/subagent/compaction 轨迹 | 官方 DSH Web 证据 |

后台可以在没有 owner 再次输入的情况下，根据日历或到期时间创建 child Run 进行复查。它
不是“每秒搜索全网”，而是只轮询已授权、可观测、有 cursor 的来源。真实商业日历、新闻
推送和分钟级行情长期稳定性尚未通过 E3，因此当前只能把它称为可运行的调度骨架和只读试点。

### 4.2 主动报告和页面在哪里

用户只有一个主入口：官方 DSH Web。插件在官方 `conversation.view` seam 增加独立的
“研究报告”视图，不覆盖 DSH 原生 Chat、Trajectory、Session 或 JSONL。页面应展示：

```text
工作区/领域/运行模式/Runtime 与 Provider
当前 Run：状态、时间、Session、重试/复查
研究进度：round、hard/soft coverage、开放 gap、能力成功/失败
证据链：来源、authority、published/observed/received、PIT、hash、冲突
报告：主因/反因、30m/24h/72h、Trigger、Invalidation、Gate、停止原因
主动事项：scheduled recheck、通知状态、待 owner 复核
```

Decision Desk 是运维和资产后台，不是第二个聊天入口。它展示 Run Inspector、Trace、来源
健康、Outcome、Evaluation、FailurePattern、Experience、备份和通知状态。前端消费 typed
Query/View DTO，不直读 SQL、LangGraph state、Provider raw payload 或整段 JSONL。

当前体验限制必须如实保留：固定 DSH `0.1.2-alpha.2` 没有公开的第三方
per-workspace default-view API，因此新 Session 首次仍可能进入官方 Chat，用户切换到
“研究报告”页签即可；禁止 fork、DOM/CSS hack 或复制一套 DSH UI 来伪造默认视图。

## 5. 当前代码结构和冗余审计

### 5.1 结构不是两套产品代码

```text
apps/
  hub_api/                 命令/查询/callback；不执行长推理
  hub_worker/              realtime/research/evolution durable worker
  research_mcp/            正式 capability gateway
  decision-desk/           运维/审计/资产后台

packages/
  kernel/                  领域无关 Run/Evidence/PIT/Gate/Ledger/Port
  orchestration/langgraph/ Hub 外层生命周期、checkpoint、recovery
  runtime_adapters/        Fixed、Replay、LangGraph、DSH runtime adapter
  provider_adapters/       Search、Official、Market、Notification 具体实现
  source_adapters/         文本、日历、新闻、未来 ASR -> TextEnvelope
  query_views/             人可读 DTO 和投影
  evals/                   replay/holdout/shadow/Outcome/Promotion 证据

packs/crypto_macro/        首个领域的 doctrine、profiles、tools、gates、fixtures、evals
extensions/dsh/decision-hub/
  src/host/                官方 Host seam：intake/status/cancel/callback
  src/client/              官方 Client seam：状态卡、报告、Desk 链接
  lib/                     npm package 发布入口（当前有意跟踪）
```

依赖方向是 `apps -> packages -> contracts`。Kernel 不依赖 DSH、LangGraph、Provider 或
前端；Domain Pack 通过公开 Port 接入；前端不直连数据库。该边界才是以后替换 DSH、模型或
新增 PPT/A 股领域时不重写 Hub 账本的关键。

### 5.2 不能直接删除的并存模块

以下模块看起来“重复”，但有明确的 compatibility、replay 或 active/candidate 职责：

| 模块 | 当前分类 | 删除风险 |
|---|---|---|
| `fixed_research_runtime` | `active compatibility baseline` | 会破坏 Fixed 回滚和对照 |
| `dsh_runtime` | `candidate/live adapter` | DSH Web/SDK 主线依赖 |
| `replay_runtime`、`fake_runtime` | `replay/evaluation/CI only` | 会失去确定性回放和故障测试 |
| `langgraph_agent` | `legacy compatibility/replay` | 可能仍被历史测试、迁移或 rollback 引用 |
| `executor.py` 的 fixed/research executor | `两层生命周期边界` | 删除会把 DSH loop 和 Hub loop 混在一起 |
| `search/openai_compatible.py`、`openai_responses.py` | `不同协议 adapter` | 中转站兼容性不能假设一致 |
| `apps/decision-desk` 与 DSH Web | `主壳 + 管理后台` | 两者职责不同，不是重复前端 |
| `extensions/.../lib/*` | `发布构建物` | `package.json main` 指向 `lib/index.js` |

### 5.3 需要后续单独做的静态清理

当前没有证据可以安全地把某个业务模块标成 dead code。仍需独立任务完成：

1. Python/TypeScript import、测试、入口脚本和 migration 引用图；
2. 旧文档执行入口的 superseded 标记；
3. `langgraph_agent`、旧 Fixed API 和历史 replay 的最小保留清单；
4. npm `lib` 是否改为 release pipeline 生成的决策；
5. 只有被证明无入口、无回放、无回滚和无发布用途的文件，才建立 ADR 后移入 `legacy/`
   或删除。

本次不直接删除代码。把“没有被主入口 import”当作删除依据，会破坏恢复、回放或回滚，
正是此前反复迁移和打补丁的根因。

## 6. `.gitignore` 与远程仓库核对

### 6.1 当前远程事实

本地 `HEAD` 与 `origin/main` 均为 commit `9b17a6bf6f3592eb44b44ee3337ce1c3f0126159`，
当前工作树无未提交改动。对 `git ls-tree -r --name-only HEAD` 的扫描结果：

- 没有 `data/`、`tmp/`、`.cache/`、`.env`、SQLite、JSONL、credentials、`.pem` 或 `.key`；
- 没有 `__pycache__`、`*.pyc`、`*.tsbuildinfo`、`.DS_Store` 或 `apps/decision-desk/dist/`；
- `extensions/dsh/decision-hub/lib/*` 有 7 个发布构建文件，属于有意跟踪的 npm package 输出；
- `docs/evaluations/assets/*.jpg` 是验收截图资产，属于文档证据，不是运行数据。

因此当前“被提到仓库”的主要误会是：本机确实存在 ignored 的 `data/`、`tmp/`、缓存和
构建目录，但它们没有进入远程 Git 树；另一个是插件 `lib/` 与临时 `dist/` 的语义不同。

### 6.2 规则处理结论

当前 `.gitignore` 已经能拦截运行数据、凭据、缓存和构建目录，且 `git check-ignore -v`
已验证以下路径会被忽略：

```text
apps/__pycache__/__init__.cpython-312.pyc
apps/decision-desk/dist/index.html
apps/decision-desk/tsconfig.tsbuildinfo
infra/.DS_Store
data/dsh-live/.env
```

本次只做最小清理：删除 `data/decision-hub/` 这一条与 `data/*` 重复的规则，并保留
`data/dsh-web/`、`data/dsh-live/` 的显式注释作为产品运行数据意图说明。没有执行
`git rm --cached`，因为远程树中不存在需要从索引移除的敏感/临时文件；没有删除本机运行
数据，因为那会影响当前实例恢复。

### 6.3 后续提交纪律

- 运行数据、密钥、数据库、Session JSONL、缓存和临时截图默认不提交；
- 发布构建物只有在 package 发布入口需要时才提交，并在模块 README 说明；
- 验收截图只有在它们被文档引用、可复现且不含敏感信息时提交；
- 每次 push 前运行 `git ls-tree` 敏感路径扫描、`git diff --check` 和 secret scan；
- 若已跟踪文件后来新增 ignore 规则，必须先确认目标并单独记录 `git rm --cached` 影响，
  不用“重写历史”解决普通误提交。

## 7. 交付判断和后续停止线

### 7.1 现在能否交付

可以交付为：**研究性试用/工程验证产品**。用户可以启动官方 DSH Web、进入
`Crypto Macro Trader`、新建会话、建立研究任务，等待同一 Session 的补证、查看轨迹和
研究报告；后台可以按授权来源和复查时间继续运行。不能交付为：正式交易信号、自动下单、
实时市场全覆盖或盈利系统。

### 7.2 进入 E3 前必须保持的约束

```text
[ ] Fixed active、DSH candidate/shadow 状态不变
[ ] 只读 capability allowlist，不自动安装未知插件
[ ] 每个 Run 有 deadline、预算、PIT、Trace、Evidence 和 failure provenance
[ ] 数据不足显示缺口，不降级伪装成成功/no_trade
[ ] 后台调度和 child Run 可在无人工提示下运行
[ ] 报告展示人可读摘要，JSONL/原始 payload 只按轨迹引用查看
[ ] outcome 到期后进入评测，不以模型自评代替真实结果
```

### 7.3 E3 的退出门

E3 结束时必须归档至少 14 天或 20 个高影响事件的：来源成功率、证据鲜度、完整 hard
coverage、延迟、成本、失败分类、人工查证时间、Outcome、Brier/方向/净收益和 owner
usefulness。之后只允许三种结果：

| 决定 | 含义 |
|---|---|
| `promote` | 证据和价值均超过 Fixed，经过 owner review 才能切换 active |
| `retain_baseline` | DSH 有工程价值但未证明优于 Fixed，继续候选或停止扩展 |
| `stop` | 真实价值不足或成本/数据不可接受，保留资产和失败证据，停止该方向 |

## 8. 需要继续讨论但当前不能擅自决定的事项

下列事项不是代码遗漏，而是需要真实数据或 owner 决策的产品边界：

1. 是否购买或授权分钟级宏观/期货隐含概率、清算和历史行情数据；
2. `web.search` 超时后是否采用具体的第二 Provider，以及其费用、隐私和合规条件；
3. DSH 上游升级到哪个版本，是否保持当前官方 plugin seam；
4. E3 观察结束后是否允许 candidate Promotion；
5. 在第二个真实领域出现前，是否仍保持当前最小 Platform Port，不提前泛化；
6. 对外开源时是否发布 DSH overlay、Domain Pack 示例和本地部署手册，哪些内部数据适配器
   需要闭源。

这些决策如果改变 DSH/Hub 边界、Gate、账本、权限、active pointer 或数据成本，必须新建
ADR 并更新 `docs/context/CURRENT_DECISIONS.md`；不能在聊天或旧总文档末尾临时追加。

## 9. 审计证据和复核命令

本次审计使用的关键事实检查：

```bash
git status --short
git log -1 --format=fuller
git ls-tree -r --name-only HEAD
git ls-files
git check-ignore -v <path>
git diff --check
```

产品质量门仍以历史验收记录为准，最近一次记录为 Python `392 passed`、DSH Runtime 回归
`41 passed`、Decision Desk `10 passed`、Ruff、Pyright、contract codegen、module docs 和
frontend build 均通过。该证据证明代码边界和失败语义的工程质量，不证明实时数据充分度、
预测准确率、盈利或自动交易能力。

