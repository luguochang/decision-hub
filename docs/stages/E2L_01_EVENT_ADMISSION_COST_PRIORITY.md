# E2L-01 事件准入、成本保护与任务优先级实施书

版本：`E2L-01.1`
日期：`2026-09-01`
状态：`engineering/replay complete / live product revalidation folded into E2-L`
所属目标：`PRODUCT-CLOSEOUT-01`
当前产品状态：`E1 passed / E2-R passed / E2-L blocked / E3 not started`

本文曾是进入 E2-L live 产品验收前的根因修复任务卡；代码、迁移和工程/replay 门已完成，
全新 live 产品复验统一并入当前 E2-L 门。它不改变
[通用底座与首个产品最终实施章程](../product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)
确定的产品形态：官方 DSH Web 仍是唯一用户入口，DSH 仍拥有唯一 Agent Loop，Hub
仍拥有 durable Run、Evidence/PIT、Gate、Ledger 和资产，LangGraph 仍只负责外层
生命周期。本文完成后不得借机新增 Harness、聊天入口、Provider 协议或领域功能。

## 1. 为什么必须先做本任务

当前 revision 的全新 live 实例已经证明 DSH、Hub、MCP 和 worker 能启动，但也暴露
了一个会直接破坏真实产品的队列准入错误：

1. `CryptoMacroDiscoveryPolicy` 使用完整网页正文匹配关键词；Fed 每个页面都含有
   `Federal Reserve / Board of Governors` 固定站点文案，因此银行审批、员工执法等
   普通页面被误判为高影响宏观事件。
2. 官方 Feed 在 fresh install 时会取每个来源最近 10 条并建立昂贵 `research.v1`
   Run；这不是实时发现，而是隐式历史回填。
3. research worker 当前严格按 `created_at` FIFO 领取 Run；自动历史 backlog 会让从
   DSH 页面提交的 owner 任务长时间得不到执行。
4. 页面虽然能显示 Run，却无法明确解释该任务来自用户手动提交、自动发现还是系统
   复查，用户会误以为后台正在处理自己的输入。

真实错误样本 `run_f7dfd75ed788400fb1b888b16ee4a800` 的标题是银行员工执法行动，
却被送入 Crypto Macro Trader。该 Run 和对应历史数据保留，不修改、不删除；它是本
任务的回归证据。

## 2. 本任务完成后的产品视图

```text
官方 DSH Web（唯一用户入口）
  |
  +-- owner 输入“建立研究任务”
  |     -> origin=manual / priority=100
  |     -> durable Run 立即进入高优先队列
  |
  +-- 后台 Source Worker
        -> fresh bootstrap：只建立 cursor，不做历史昂贵研究
        -> 后续新增 Feed：先做标题级低成本准入
        -> 高影响标题：origin=automatic / priority=50
        -> 普通条目：只保留 baseline/Observation，不进入 research
        -> Calendar：默认不激活；显式启用后也不因标题直接启动 research

durable Run queue
  -> priority DESC, available_at, created_at ASC
  -> 官方 DSH Session + typed capability
  -> Evidence/PIT -> Sufficiency -> code Gate -> 人可读报告
```

DSH 工作区中每个正式研究任务必须展示：

- `任务来源`：`用户提交 / 自动发现 / 定时复查 / 历史兼容`；
- `队列优先级`：`高 / 普通 / 低`，不展示内部整数；
- `run_id / session_id / 当前状态 / 当前证据轮次 / 停止原因`；
- 自动发现的事件标题和来源；
- 手动任务在自动来源繁忙时仍能优先开始；
- fresh bootstrap 只显示来源同步成功，不伪装成“发现了 40 个重要事件”。

Decision Desk 继续作为管理后台，可按任务来源查看队列和历史；不得建立第二个聊天
入口。原始 JSON、模型 payload 和 DSH JSONL 继续只在审计入口查看。

## 3. 锁定设计

### 3.1 标题级事件准入

低成本 discovery 只允许使用可信结构化标题，不允许使用下载后的完整网页正文。当前
`TextEnvelope` 的官方 Feed 约定为首个非空文本行是 Feed item title；后续若引入正式
`title` 字段，应先改 canonical schema，再替换这个 adapter 内部约定。

Crypto Macro Pack 的自动研究词只保留明确事件语义，例如：

```text
fed chair / fomc / powell / warsh / monetary policy / economic outlook
interest rate / inflation / cpi / employment / payroll / tariff / sanctions / war
```

删除过宽的 `federal reserve`。不得通过持续增加银行审批、执法、任命等排除词维护
黑名单；那会重新形成补丁式架构。

`bls-calendar` 是计划信息，不是已发生的市场冲击。产品默认不激活 calendar discovery，
即使显式启用也不直接建立昂贵 `research.v1`；未来在真实到期触发需求出现时另立任务卡，使用
`available_at` 或调度事件，不在本任务内临时实现。

### 3.2 fresh bootstrap 成本保护

官方实时 Feed 的 `bootstrap_latest=true` 语义固定为：

```text
cursor is None
  -> 拉取并解析 Feed
  -> 将 cursor 移到当前最新 item
  -> envelopes=[]
  -> Observation/Run count 均不增加
  -> 后续 poll 只处理 cursor 之后的新 item
```

它不是“取最近 N 条并自动研究”。如 owner 以后确实需要历史回填，必须使用显式
backfill/recheck 命令、单独预算和可见确认；不能复用启动路径隐式产生费用。

### 3.3 canonical Run admission

Run 新增持久化字段：

| 字段 | 值 | 含义 |
|---|---|---|
| `admission_origin` | `manual` | DSH/API owner 主动提交 |
|  | `automatic` | 来源发现自动建立 |
|  | `scheduled_recheck` | 系统到期复查 |
|  | `legacy` | 迁移前历史 Run |
| `priority` | `100` | manual |
|  | `50` | automatic/default |
|  | `10` | scheduled recheck |
|  | `0` | legacy migration |

领取顺序固定为：

```text
priority DESC
-> available_at 已到期
-> created_at ASC
-> run_id ASC（稳定 tie-break）
```

同一 Run 的 origin/priority 创建后不可由 Agent、模型、插件或 Provider 修改。owner
重试视为显式手动操作，使用 `manual/100`；自动生成的定时复查使用
`scheduled_recheck/10`。历史 migration 只增加字段并填充 `legacy/0`，不改历史状态、
时间、事件、Evidence、Artifact、Forecast 或 Outcome。

### 3.4 失败和费用边界

- discovery 命中只代表可建立 research candidate，不代表方向、置信度或 Gate 通过；
- bootstrap、准入和 queue claim 都不得调用模型；
- 无合法 live Evidence 时必须保持 failed/degraded/research_only，不能输出方向性
  `no_trade` 伪装成功；
- 本任务不扩大 capability allowlist、网络域名或费用预算；
- 任何自动研究仍受既有 `ExecutionBudget`、typed capability 和代码 Gate 限制。

## 4. 代码结构与修改边界

```text
packs/crypto_macro/discovery.yaml
  自动研究标题词与允许来源；Domain Pack 拥有

packages/provider_adapters/research/discovery.py
  标题提取和确定性准入；不访问网络、不调用模型

packages/source_adapters/official_feeds/adapter.py
  fresh bootstrap cursor-only 语义

packages/kernel/decision_hub_kernel/application/run.py
  Run 创建不变量与 priority claim

packages/kernel/decision_hub_kernel/application/{analyze,source_ingest,commit,research_observability}.py
  在唯一 RunService/事务边界声明 admission origin

packages/kernel/decision_hub_kernel/persistence/db.py
migrations/versions/0025_run_admission_priority.py
  durable 字段与历史兼容迁移

contracts/schemas/agentic_research.schema.yaml
packages/query_views/research/service.py
extensions/dsh/decision-hub/
  canonical origin/priority 人可读投影；生成代码只由 codegen 更新
```

禁止新增 `queue_service.py`、第二张任务表、Redis、Celery、另一套 scheduler 或根据
`source_id == dsh-web` 在 SQL 中硬编码优先级。SQLite durable Run 仍是唯一任务事实源。

## 5. SDD / BDD / TDD

### 5.1 SDD 成功定义

一个 fresh product 实例启动后不产生历史 research backlog；一个新高影响讲话能自动
入队；一个普通 Fed 银行监管页面不能入研究队列；一个 DSH 手动任务能越过已有自动
任务优先被 research worker 领取；页面能解释任务来源，且历史数据完整保留。

### 5.2 BDD 场景

```text
Given Fed 银行执法或收购审批标题，正文含 Federal Reserve 固定文案
When discovery policy 评估事件
Then 只建立 baseline，不建立 research.v1
```

```text
Given Powell/Warsh/FOMC/monetary policy/inflation 等明确高影响标题
When 新 Feed item 在 bootstrap 之后到达
Then 建立 automatic/50 research Run，重复 poll 不重复 Run
```

```text
Given fresh 数据库和 bootstrap_latest 官方 Feed
When 第一次 poll
Then cursor 移到最新 item，Observation=0，Run=0，模型调用=0
And 第二次 poll 没有新 item 时仍为 0
And 第三个 poll 出现新 item 时只处理该新增 item
```

```text
Given 多个更早的 automatic research Run 和一个后创建的 manual Run
When worker claim_next
Then 先领取 manual Run
And 同优先级仍按 created_at/run_id 稳定 FIFO
```

```text
Given 0024 历史数据库
When upgrade 到 0025
Then 所有历史 Run 为 legacy/0
And 原有 Run/Evidence/Artifact/Forecast/Outcome 数量与业务字段不变
```

```text
Given DSH 手动、自动发现和定时复查三类 Run
When 用户查看 DSH 业务卡和 Decision Desk
Then 页面显示人可读任务来源与优先级
And 不暴露 raw SQL、内部整数或未脱敏凭据
```

### 5.3 TDD 顺序

1. 在 `tests/research/test_discovery_policy.py` 先写普通 Fed 页面和高影响标题的失败测试；
2. 在 `tests/sources/test_official_feeds.py` 写 cursor-only bootstrap 与下一条新增 item；
3. 在 `tests/evolution/test_research_worker.py` 写 manual/automatic/recheck 排序与稳定 tie-break；
4. 在 `tests/migrations/test_upgrade_paths.py` 写 0024 -> 0025 历史保留；
5. 在 query/API/plugin 测试中写 origin/priority 投影；
6. 只做使测试通过的最小实现，再运行全量质量门。

## 6. 实施 Checklist

### A. 文档和契约

- [x] 锁定产品视图、根因、非目标、代码地图和停止线；
- [x] 新增 ADR-0015，确定 bootstrap 与 Run priority；
- [x] canonical schema 增加 `admission_origin`，由 codegen 生成 Python/TS；
- [x] 模块 README、migration README 和用户变更记录同步。

### B. 根因实现

- [x] discovery 只匹配首个非空标题行；
- [x] 删除过宽 `federal reserve`，日历不自动启动 research；
- [x] fresh Feed bootstrap 只建立 cursor；
- [x] Run 持久化 origin/priority，旧数据迁移为 legacy/0；
- [x] manual/automatic/recheck 创建路径全部显式声明来源；
- [x] `claim_next` 按 priority/available_at/created_at/run_id；
- [x] DSH/Desk 显示人可读来源和优先级。

### C. 自动化门

- [x] targeted TDD 全部通过；
- [x] `pytest -m "not live"`；
- [x] Ruff / Pyright；
- [x] canonical codegen / module docs / `git diff --check`；
- [x] DSH plugin test/build；
- [x] Decision Desk test/build；
- [x] Compose config、fresh migration、backup/restore/recovery。

### D. 全新 live 产品门

- [ ] 使用全新 Compose project、数据库、DSH_HOME 和端口，不复用旧进程；
- [ ] 启动后没有历史 research backlog；
- [ ] 页面提交手动任务得到明确 `run_id`，并优先进入受管 DSH Session；
- [ ] 新高影响 Feed 才能自动入队，普通 Fed 页面不入研究队列；
- [ ] DSH 调用既有 `official.macro / market.cross_asset / market.crypto_derivatives`；
- [ ] success / partial / insufficient 语义准确，失败不伪装 `no_trade`；
- [ ] 桌面与移动截图、console、API、revision、hash 证据可复核；
- [ ] 只有代码 readiness 通过才设置 `pilot_ready=true`。

### E. 阶段停止

- [ ] E2-L 通过后停止新增工程功能，进入 E3（14 天或 20 个事件）；或
- [x] E2-L 仍失败时记录真实 blocker，保持 `pilot_ready=false` 和 Fixed active；
- [ ] E3 最终只允许 `promote / retain_baseline / stop`，不无限开发。

## 7. 验收证据与回滚

每次验收必须记录 revision、启动命令、隔离端口、Run/Session 关联、脱敏日志、页面
截图和文件 hash 到 `docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md`。凭据、
DSH token 和 `.env` 不进入文档或 Git。

若 0025 或新 claim 顺序造成回归，停止新 Run，回滚应用到锁定版本；已创建 Run 和
迁移列保留，不删除数据。迁移 downgrade 只用于空测试库验证，真实 owner 数据不做
破坏式回退。若修复需要新增网络、费用、第二个队列或修改 Gate/active pointer，立即
停止并新建 ADR，不在本任务内扩张。
