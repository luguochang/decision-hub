# Decision Hub 主动研究交付修复方案

版本：`PD-DELIVERY-2026-09-05.v1`  
状态：`completed / PD-04..06 runtime closeout verified; PD-07 observation not started`  
适用范围：`DSH-first + crypto_macro.v1 + 单 owner 单机 research-only`  
上游约束：[产品事实充分度与主动交付修复方案](PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)、[PD-04～06 产品运行收口](../stages/PD_04_06_PRODUCT_RUNTIME_CLOSEOUT.md)、[ADR-0022 Search Provider 路由边界](../decisions/ADR-0022-search-provider-route-boundary.md)、[ADR-0024 语义事实与事件窗口边界](../decisions/ADR-0024-semantic-fact-and-event-window-boundary.md)。

本文是本轮执行的唯一新增问题收口方案，解决“搜索已经调用但报告仍反复证据不足、循环过早停止、用户看不出智能体主动工作、改动多次仍难交付”的工程和产品问题。本文不替换 canonical schema、既有 Stage Charter 或历史验收记录；发生冲突时停止编码，以 schema、ADR 和当前状态页为准。

## 1. 先回答：现在到底是什么产品

### 1.1 产品定义

Decision Hub 不是一次问答接口，也不是自动交易系统。它是一个由 DSH 驱动的、可审计的单 owner 主动研究工作台：事件或文本进入后，系统在预算和权限内自主发现缺口、调用已注册能力、补充事实、保存每轮轨迹，最终给出带来源和停止原因的研究报告；没有足够可信事实时必须明确停止并安排复查。

```text
事件/文本/日历
  -> Event admission + 去重
  -> 事件前后窗口采集（可预知事件）
  -> DSH 官方 Agent Loop / Supervisor
  -> DSH web_search 发现 locator
  -> Hub capability gateway 调用 Fetch/typed provider
  -> PIT、authority、hash、freshness、独立性、语义校验
  -> 新 Evidence/Fact 入 Hub 账本
  -> 同一 DSH Session 有界继续或解释性停止
  -> Artifact / Report / Trace / Outbox
  -> recheck -> Outcome / Evaluation -> 人工 Promotion
```

用户每天只需要进入官方 DSH Web 的交易员工作区；Decision Desk 是管理、复盘和运维视图，不是第二个聊天入口。

### 1.2 四个组件的不可变边界

| 组件 | 所有权 | 允许做什么 | 禁止做什么 |
|---|---|---|---|
| DSH | 官方唯一 Agent Harness、Supervisor、Agent Loop、Session、Trajectory、Web 主壳 | 搜索、调用 Hub 工具、规划下一轮、输出结构化候选 | 写业务账本、改 Gate、发布方向性结果 |
| LangGraph | 外层生命周期和恢复 | checkpoint、轮次、deadline、预算、确定性路由 | 再造一个模型 loop、直接写事实或账本 |
| Decision Hub Kernel | 可信业务账本 | Evidence/Fact/PIT/Gate/Artifact/Forecast/Outcome/Outbox | 依赖 DSH 私有状态、猜测网页事实 |
| Domain Pack / Plugin | 领域和能力声明 | requirements、source registry、provider route、语义规则、视图扩展 | 绕过 Gateway、直接获得发布权 |

“插件”在本项目中有明确含义：DSH 官方插件负责 UI/工具注册；Capability Adapter 负责一个可替换外部能力；Domain Pack 负责领域配置和语义规则；Role Profile 负责角色任务约束。它们都通过公开契约接入，不新造第二套插件协议。

## 2. 为什么过去改了很多次仍不可交付

根因不是 DSH 或 LangGraph 不能做 Agent Loop，而是验收对象错位：过去证明了工具调用、Evidence 账本和 fail-closed，就把它当成金融研究产品完成。

1. `web_search` 只证明搜索发现能力。它返回 locator/摘要，不能替代分钟级利率、DXY、FedWatch/OIS、BTC OI/funding/basis 或事件窗口。
2. Search 结果只有经过 Source Registry、Fetch/typed provider、PIT、authority、hash 和语义 Gate 才能进入业务 Evidence；因此“搜索到了网页”不等于“关闭了 hard gap”。
3. 旧事件没有事前 EventWatch 和 `t-30m/t0/t+5m/t+30m` 快照，无法诚实重建讲话后的 30 分钟反应；当前值不能冒充历史窗口。
4. 现有通用 Gate 曾主要检查 freshness、authority 和来源数，未必检查字段、单位、指标族是否真的符合 requirement，存在语义替代风险。
5. 外层循环对 live route 有能力梯子，但离线 replay 只有聚合能力 `replay.research`，上一轮把合法的下一 generation 当成“无路可走”。这属于代码回归，不能靠改 Prompt 掩盖。
6. 前端同时展示工程轨迹和业务结论，却没有把“已满足、待补、不可得、迟到事件、下一次复查”作为一等状态，用户因此只看到重复的 `insufficient_sources`。
7. 状态文档并列存在多个历史“最终方案”，导致新会话读取旧入口，形成目标漂移和重复造轮子。

结论：之前的底座建设不是无效；但只能证明 DSH-first 研究链和安全失败边界，不能证明实时金融事实充分、预测准确或盈利。新的完成条件必须按“事件到报告到复查”的价值链验收。

## 3. 本轮具体实现方案

### 3.1 循环和工具路由

DSH 仍是唯一 Agent Loop。LangGraph 只包住一次研究 Run 的生命周期：

```text
round_started
  -> DSH generation
  -> tool result attestation
  -> Gateway durable commit
  -> semantic coverage
  -> sufficient ? finalize : select next declared route
```

继续条件必须同时满足：

- hard coverage 未达标；
- Run 未超过 `max_evidence_rounds`、`max_tool_calls`、费用和 deadline；
- 至少存在一个未尝试的 declared capability，或存在 retryable provider failure；
- replay fixture 若只有 `replay.research`，仅在 `execution_mode=replay` 下允许有界重复 generation；
- live Run 不得因为“有新证据”而无限重复同一能力。

停止条件必须由代码 Gate 产生：`sufficient`、`tool_budget`、`round_budget`、`critical_data_unavailable`、`permission_denied` 或 `runtime_unavailable`。每个停止都要保留 attempts、error provenance、未关闭的 hard gap 和下一次复查条件。

### 3.2 Search 与 typed fact 的分层

固定为一条路由，避免重复搜索和重复造协议：

```text
DSH web_search (primary discovery)
  -> locator candidate
  -> Source Registry 分级
  -> web.fetch / official parser / typed market adapter
  -> EvidenceCandidate + FactEnvelope
  -> ResearchCapabilityGateway
  -> ResearchFactStore + deterministic semantic Gate
```

Tavily 是显式 fallback，不是必需依赖，也不与 DSH native search 在同一 Run 无条件并发。没有 Tavily 时，DSH native 仍可运行；两者都只能发现来源，不能直接关闭金融 hard requirement。若未来购买实时行情或定价数据，接入 `ProviderRoute`，不改 Core、Graph、UI 或业务账本。

### 3.3 六类事实能力的稳定接口

继续复用现有 `ResearchCapabilityAdapter.execute(ResearchCapabilityQuery) -> ResearchCapabilityResult`、`FactEnvelope` 和 Gateway，不增加第二套 DTO。

| 能力 | 现有状态 | 本轮的可执行收口 |
|---|---|---|
| `official.macro` | 可抓正文，事件身份部分可用 | typed parser 输出 event identity、actual/prior、policy delta，字段和来源可验证 |
| `macro.cross_asset_intraday` | FRED 日频延迟 | Provider-neutral router；免费 proxy 明确 delayed，不能伪装 realtime；正式分钟数据由 licensed route 接入 |
| `macro.expectation_pricing` | 专用能力缺失或仅声明 | 单独的字段/单位/时间窗契约；没有 Fed funds/SOFR/OIS/FedWatch 数据时保持 hard gap |
| `market.crypto_event_window` | 部分采样能力 | 持久保存事件前基线和事件后 offset，缺 baseline 时标记 `retrospective_only` |
| `market.crypto_derivatives` | CoinEx/OKX 快照 | 多 venue route、OI 1h/4h/24h delta、funding、basis、mark/index、窗口状态 |
| `market.crypto_crowding` | 基础 order-book proxy | 明确 book imbalance/taker/liquidation proxy 的字段和独立性，不把同一 venue 重复计数 |

Provider 每次尝试必须记录 `provider_id、route_role、service_tier、started_at、finished_at、cost、error_code、retryable、fallback_of`。429、timeout、5xx、domain denied、malformed payload、stale 和 PIT 违规必须可区分，且仅 retryable 错误允许切换 fallback。

### 3.4 事件调度与主动交付

调度不是新的 Agent。现有 feed/calendar -> admission -> durable worker -> DSH Run -> Artifact/Outbox 链继续复用，补齐以下业务语义：

1. 日历事件提前创建 `EventWatch`，在 `t-30m`、`t-5m`、`t0`、`t+1m`、`t+5m`、`t+30m` 采集允许的 typed snapshot。
2. 迟到或历史事件只能标 `retrospective_only`，缺失 baseline 不得补造反应结论。
3. Run 完成后写入 Inbox/Report，并由 Outbox 做一次通知；recheck 是同一事件的 child Run，不能创建重复报告。
4. 页面主动展示“新报告、待复查、数据健康、失败原因”，用户不需要先提问才能启动系统。

### 3.5 自进化和个人资产

自进化不是让模型自行改代码或改 Gate。可沉淀的个人资产是：

- 不可改写的 Run/Trajectory/Evidence/Fact/PIT/Artifact/Forecast/Outcome；
- 每个 provider、来源、字段、窗口和错误的长期健康统计；
- 有时间切分的 replay、holdout、Brier/coverage/usefulness 评测；
- 版本化 Role/Pack/Capability candidate 及其 provenance；
- owner-only promotion/rollback 记录。

Evolution worker 只能根据 Outcome/Evaluation 生成 candidate，不能自动发布；没有前瞻 Outcome 就不能声称“自进化有效”。

### 3.6 前端呈现

继续使用官方 DSH Web 主壳和当前 Decision Hub 插件，不 clone 或重做 DSH 前端。页面至少有以下业务区：

- `Research Inbox`：新事件、状态、hard coverage、下一次复查、通知状态；
- `Research Report`：事实卡片、来源等级、根因链、反方链、窗口、缺口、停止原因；
- `Trace`：DSH 每轮、工具、provider attempt、retry/fallback、Gateway 接受/拒绝；
- `Data Readiness`：每个 requirement 的 semantic status，而不是只显示一个百分比；
- `Decision Desk`：Run、成本、版本、Outcome/Evaluation、provider health 和 promotion 管理。

没有证据时页面要显示“缺什么、试过什么、为什么不能继续、何时复查”，不显示空的 JSON 或看似精确的方向性结论。

## 4. 代码落点和维护规则

### 4.1 只允许复用的公共边界

| 边界 | 代码位置 | 维护规则 |
|---|---|---|
| Contract | `contracts/schemas/` -> codegen | 只改 YAML，再生成 Python/TS；禁止镜像手改 |
| 事实账本/Gate | `packages/kernel/decision_hub_kernel/` | 不在 Graph、API、Provider 重写 |
| 生命周期 | `packages/orchestration/langgraph/` | 只做 checkpoint、预算、恢复、确定性路由 |
| DSH 接入 | `packages/runtime_adapters/dsh_runtime/`、`extensions/dsh/decision-hub/` | 使用官方 Host/Plugin 接口，保留 upstream build identity |
| Provider | `packages/provider_adapters/` | 每项能力一个 adapter/router，不能把网页摘要当 typed fact |
| Domain | `packs/crypto_macro/` | requirement/source/semantic policy 只在 Pack 定义 |
| 用户视图 | DSH extension + `apps/decision-desk/` | 只消费 query view/contract，不读 SQL 或 LangGraph state |

### 4.2 本轮必须先写测试的场景

BDD 场景：

```text
Given 一个 hard requirement 尚未满足
And DSH 已尝试 primary route
When 仍存在 declared fallback 且预算未耗尽
Then 同一 Session 必须进入下一轮并记录 route lineage

Given 只有 replay.research 聚合能力
And execution_mode 是 replay
When 第一轮 coverage 仍 insufficient
Then 允许有限 generation continuation

Given execution_mode 是 live
And allowed_capabilities 只有 replay.research
When coverage 仍 insufficient
Then 不得重复该能力，必须 bounded stop

Given Search 只返回 locator
When 没有通过 Fetch/typed semantic validation
Then locator 不能进入 accepted Evidence，也不能关闭 hard gap

Given 事件没有事前 baseline
When 用户查看 30m 结果
Then 页面显示 retrospective_only/baseline_unavailable，不得伪造事件窗口
```

TDD 顺序：Red 测试 -> 最小实现 -> Green 全量回归 -> Refactor -> 更新模块 README、状态页和执行日志。每个跨边界字段必须经过 Pydantic/Zod 运行时校验。

## 5. 本轮执行目标和任务卡

### 目标

在不改变 DSH/LangGraph/Hub 所有权、不新建第二套 Agent Loop、不引入新前端框架的前提下，恢复工程绿线并把当前真实运行状态收口为可复盘的 `research_only` 产品试点：

```text
DSH Web -> 同 Session 有界补证 -> Gateway/Fact/Gate -> Report/Trace/Desk
```

### 任务清单

- [x] 修复 replay-only continuation 回归：只允许 replay mode 的聚合能力有界续轮。
- [x] 为 replay continuation 和 live 防重复增加 BDD/TDD 覆盖。
- [x] 修复 Pyright 私有 helper 和测试 adapter 类型赋值问题。
- [x] 重跑 Python 全量测试、Pyright、Ruff、contract codegen、module docs、git diff check。
- [x] 重跑 DSH plugin 和 Decision Desk test/build。
- [x] build identity 变化后重建 DSH Extension，并确认 Host readiness 与插件 hash 一致。
- [x] 对同一真实 Run 复验 Report、Trace、Inbox、Decision Desk 的 session/run 关联和失败 provenance。
- [x] 更新 `IMPLEMENTATION_STATUS.md`、`CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、`ROADMAP.md`、`EXECUTION_PLAN.md`、`INDEX.md` 和 `PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md`。
- [x] 将本轮结果与截图、命令、测试数量写入执行日志；不把历史旧 hash 或旧测试数字改写成当前事实。

### 退出门

只有以下全部满足，才可把本轮标为 `PD-04..06 runtime closeout complete`：

1. Python 全量测试和 Pyright 通过；
2. 契约、Ruff、模块文档和 `git diff --check` 通过；
3. DSH 插件和 Decision Desk 测试/build 通过；
4. 隔离实例的 Hub、worker、MCP、DSH Host readiness 全部通过；
5. 浏览器可从 DSH Inbox 打开同一个 Run 的报告、轨迹和 Decision Desk；
6. 页面能显示 accepted/rejected Evidence、provider attempts、未关闭 hard gap、停止原因和复查时间；
7. 任何 `research_only` 或 `insufficient_sources` 都有可核验的事实原因，不用模型文字掩盖；
8. 文档状态统一为“工程收口完成，个人 research-only 观察待进行”，不得写成预测准确、盈利、自动交易或实时数据充分。

## 6. 交付边界和下一阶段

本轮完成后，产品可以交付为单 owner、单机、只读的研究试用入口，用户能够看到 DSH 的多轮轨迹、来源和失败原因；这仍不是交易信号产品。下一阶段只有在运行收口绿线后才能开始：

1. `PD-07/G2-AF-05`：至少 14 天或 20 个未来高影响事件的前瞻观察；
2. 真实分钟级宏观/预期定价 Provider bake-off，明确免费 proxy 与 licensed live 的成本和授权；
3. 基于 Outcome/Evaluation 的 usefulness、coverage 和 calibration 评测；
4. 只有数据和价值门通过后，才讨论第二领域（PPT 等）和新的通用 Platform Core 接口。

任何新 Provider、Role、Pack 或 UI 能力都必须先新增 schema/事件/Gate 规则和 ADR，再写代码；不能因一条失败报告继续追加 Prompt 或复制 adapter。

## 7. 当前事实声明

- DSH 原生 `web_search` 已集成并真实运行，多轮补证和轨迹不是占位；
- 当前报告仍可能 `research_only`，因为 typed market facts、事件窗口和语义 Gate 仍有真实缺口；
- Search 不能凭自身补齐分钟级金融数据；Tavily 不是必需依赖，只是显式 fallback locator；
- 当前任务的代码回归已定位为 replay continuation，已修复并由测试保护；
- 本轮退出门已通过，交付名称固定为“单 owner、单机、只读的 research-only 试点”；PD-07
  观察仍未开始，不能称预测准确、盈利、自动交易或正式分钟级事实充分。PD-07 的唯一入口见
  [前瞻价值观察阶段卡](../stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)。

## 8. 运行收口后故障：OOM 根因与研究消费屏障

### 8.1 已观察事实与根因

隔离实例中的 scheduled recheck `run_8886586f38a24fe4949a1da0e741c98c` 在第 0 轮以
`host_hub_unreachable` 失败，`retryable=true`，没有 DSH Session、Tool Call 或 Artifact。该失败
没有被页面掩盖。现场检查确认 DSH 进程继承的 `DECISION_HUB_API_URL` 是正确的 `18300`；Hub 恢复
后，同一 DSH 进程的 Host readiness 也从 `false` 自动恢复为 `true`，无需重启 DSH。因此不是
Cordis `!!js` 地址解析、DSH Agent Loop 或 Node 连接池永久失效。

Docker 事件给出的直接基础设施根因是 Hub API 容器发生 OOM 并以 `exitCode=137` 退出。事发时
Docker Desktop 只有约 7.75 GiB 内存，同时运行 68 个容器，其中 12 套旧 Decision Hub
acceptance/test Compose 栈各占用约 450～600 MiB；真实 Provider 执行与重复镜像构建叠加后，Hub
在 DSH 探针期间重启，于是表现为随机的 `host_hub_unreachable`。受控重启证明同一 DSH 进程会在
Hub 恢复后自动回到 `ready=true / hub_reachable=true`，因此不能把故障归因于 Cordis 配置、DSH
Agent Loop 或永久失效的 Node 连接。

同时审计发现了一个独立的启动竞态：旧 `run-product.sh` 在 DSH Host 双向就绪前就可能启动
`hub-research-worker`，到期 Run 会过早被领取。该竞态没有造成这次 OOM，但会放大短暂基础设施
故障，因此一并以消费屏障修复。既有 owner `retry`、scheduled recheck、lease 和不可改写 child
Run 继续复用，不新增第二套自动重试队列。

### 8.2 最小实现

先停止 12 套旧测试 Compose 项目和 3 个旧 DSH launcher，只保留当前产品栈；没有删除任何 Docker
volume、数据库、Session、历史 Run、镜像或其他项目数据。清理后运行容器由 68 个降至 8 个，当前
Decision Hub 五个服务约占 606 MiB。

启动器分成三个明确阶段，仍由一个脚本拥有生命周期；当前工作树镜像只构建一次，后续启动全部
使用 `--no-build`，避免 DSH 已在线后再次构建挤压 Docker VM：

```text
build all current worktree product images once
  -> start Hub API + realtime/evolution workers + Research MCP with --no-build
  -> verify Hub ready + Inbox contract + MCP transport
  -> start official DSH Web and verify Host readiness (including hub_reachable)
  -> start hub-research-worker with --no-deps --no-build
  -> verify research worker container is running
  -> publish the one authenticated DSH URL
```

`hub-realtime-worker` 可以在屏障前接收入站事件和创建 durable admitted Run，但只有
`hub-research-worker` 能领取研究任务。这样不需要修改 Kernel、Run schema、LangGraph、DSH 插件
或历史数据，也不把 transient failure 隐藏为成功。

### 8.3 BDD/TDD 与验收门

```gherkin
Scenario: 到期研究不会在 DSH Host 就绪前被消费
  Given 当前工作树镜像已构建
  And Hub API、Inbox contract 与 Research MCP 已就绪
  When official DSH Web 尚未通过带 build identity 的 Host readiness
  Then hub-research-worker 不得启动

Scenario: DSH Host 就绪后研究消费开始
  Given DSH Host readiness.ready=true and hub_reachable=true
  When 产品启动器越过消费屏障
  Then hub-research-worker 才以当前镜像启动
  And 启动器确认该容器处于 running 后才发布产品 URL
```

退出门：launcher Red/Green 测试、Shell 语法、DSH Extension 测试/build、Python 全量质量门均通过；
用隔离实例确认 Host readiness 和研究 worker running；对失败 Run 使用既有正式 `retry` 命令创建
不可改写 child Run，确认能建立 DSH Session 并进入 Tool Loop。若 Provider、预算或 typed fact 仍
阻断，必须保留真实终态，不得把它写成事实充分或 PD-07 样本。

### 8.4 实现与最终验收证据

- TDD Red 首先证明 research worker 会在 DSH Host readiness 之前启动；Green 后聚焦 launcher/
  runtime recovery 共 `14 passed`。
- Python 全量 `552 passed`；Ruff、Pyright、canonical codegen、module docs 和
  `git diff --check` 通过；DSH Extension `69 passed + build`，Decision Desk
  `10 passed + build`。
- 失败 Run `run_8886586f38a24fe4949a1da0e741c98c` 通过既有 owner command 创建不可改写 child
  `run_2c06947df692424e326161d83f488368`，建立 DSH Session
  `dsh_07e5a2012555a0ac614c8e0b1a749f08ab270a12afa8fd4884a4670fe235e2d1`，完成 3 次 generation、
  2 个已提交研究轮次和 16 次工具调用，并生成 Artifact
  `art_a5a513d76cb145299cb412454f21fa0f`。
- 浏览器从官方 DSH Web 的 `Crypto Macro Trader` 工作区打开该 Session；报告显示 8 条有效证据、
  3 轮轨迹、`web_search`/Fed/CoinEx/FRED/Fetch 调用、失败 provenance 和下一复查时间。主动研究
  Inbox 显示用户提交、scheduled recheck、通知与排队/失败/仅研究状态；Decision Desk 对同一 Run
  显示 102 条归一化事件、Evidence 接受/拒绝、Gate、三周期输出和 owner commands。
- 该 child Run 最终为 `research_only/provider_timeout`：480 秒 deadline 在先前 OOM 波动期间耗尽，
  系统保留最后一次已认证 synthesis 和 durable Evidence，并抑制方向性发布。这是诚实的安全终态，
  不是事实充分、预测成功或 PD-07 前瞻样本。

本方案工程目标已经完成。产品当前可交付边界仍是单 owner、单机、只读 `research_only` 试点；
PD-07 的 `started_at=null`、前瞻样本数为 0，下一步只能先冻结第一批未来事件 cohort，再在事件发生
前创建 EventWatch，不能用本轮回溯运行补记观察结果。
