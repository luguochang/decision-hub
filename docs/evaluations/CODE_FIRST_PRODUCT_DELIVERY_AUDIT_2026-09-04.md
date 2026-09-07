# 代码优先产品交付审计报告

审计日期：2026-09-04  
审计对象：`dsh-mult` 当前工作树、已提交 `HEAD`、本机运行实例和 DSH 上游锁定源码  
审计原则：代码、测试、运行日志和可复现命令优先；文档只作为“规划要求”的证据，不作为“已完成”的证据。

## 1. 执行摘要

结论先行：当前项目有相当多真实实现，DSH 官方插件接入也确实成立，但还不是可以对外或稳定日常使用的产品版本。准确的产品等级是：

> **单机、单 owner、research-only 的工程试点/内部技术预览。**

目前不能称为稳定的主动研究产品，不能称为可靠的交易决策产品，更不能称为自动交易或已经证明盈利的系统。

造成长期不可交付的主因不是“没有写代码”，而是交付控制失效：

1. 新实现没有形成可复现的发布提交。当前 `HEAD` 与 `origin/main` 都是 `9b17a6b`；审计期间 `git status --short` 在约 180 行附近持续变化，tracked diff 约为新增 4.8K 行、删除 3.3K 行。这个工作树不能作为单一 release artifact。
2. 当前本机同时运行至少 5 个 DSH Web 实例和多组 Hub/MCP 实例；同一路径的 readiness 都返回 ready，但构建哈希不同。用户看到的页面无法可靠归属到当前源码。
3. `/health/ready` 只是数据库/运行统计健康，不是产品可用性检查。Provider、MCP 业务握手、Worker、能力目录、迁移版本和 DSH 制品身份未纳入该门禁。
4. DSH 内层 Agent Loop、LangGraph evidence-round、durable Worker、Gateway 和结果映射共同拥有部分规划/预算/重试语义，形成了双控制面及过多中间转换。
5. `web_search` 已安装且暴露，但是否调用主要由模型遵循 Prompt 决定。产品又把 native search 与 Hub `web.fetch`/typed capability 分成不同信任路径，默认 allowlist 还不包含 `web.search`，因此搜索“存在”不等于搜索“会被稳定触发”。
6. 当前前端契约测试仍有红灯：生成的 `research_task` 要求 `requirement_id`，`ResearchPage.test.tsx` 夹具未同步。
7. 产品真正需要的 typed 事实和事件窗口仍不完整：多 venue crypto/OI delta、分钟级宏观和 expectation pricing、自动事件到报告通知的稳定闭环、真实 Outcome/Evaluation 观察都未完成。
8. 产品启动器只执行 `docker compose up --force-recreate`，不会重建镜像；当前源码的 `max_tool_calls` 是 24，而运行中主 API 容器内仍是 12，已形成可复现的源码/镜像行为漂移。

因此，建议立即停止新增平行文档和大范围架构扩张，先做一次集中收口。以 2 名全职工程师和 1 名产品/领域 owner 为前提，技术 RC 约需 4 至 6 周；包含至少 14 天自然观察和 20 个前瞻事件的稳定个人研究试用版约需 6 至 9 周。这个估算不承诺预测准确率或盈利，交易自动化不在当前交付范围内。

## 2. 审计范围和证据等级

本报告使用了以下证据：

| 证据 | 结果 | 解释 |
|---|---|---|
| 当前工作树静态代码 | 38K 行左右的应用/包/扩展/工具源代码 | 证明实现存在，不证明端到端可用 |
| Python 离线回归 | `492 passed in 23.52s`（`./.venv/bin/pytest -q`，17:53 左右） | 当前工作树在该时间点的 Python 离线回归通过；这只证明离线测试，不证明 live/provider/制品一致性 |
| Ruff/Pyright/codegen/module docs | Ruff 2 项、Pyright 7 项失败；codegen/module docs 通过 | Ruff 是 `apps/research_mcp/main.py` 和 `contracts_py/__init__.py` 两处 import order；Pyright 涉及 MCP query 类型、event-window 可空时间和测试 Protocol/参数类型；canonical schemas 和 13 个 module docs 均通过 |
| DSH plugin tests | `58 passed, 2 failed`（共 60） | 两个 Research Tool 用例在 DSH schema 编译阶段失败：可选 `event_id` 生成了 `required: false`，而锁定的 DSH compiler 要求出现的字段必须为必填 |
| Decision Desk build | 通过；JS chunk 530.53 kB 警告 | 能构建，不代表页面链路验收通过 |
| Decision Desk tests | 失败：1 suite，8 tests 在收集前通过 | 当前质量门不是全绿 |
| 本机 `/v1/pilot/readiness` | 多实例均 `not_ready` | 与浅层 `/health/ready` 的 200 形成反差 |
| 本机 DSH Host readiness | 多实例 `ready=true`，构建 hash 不同 | ready 只证明实例内部自洽 |
| 源码/运行镜像配置 | `max_tool_calls: 24` / `12` | `run-product.sh` 没有 `--build`，当前容器不是当前源码制品 |
| 真实 DSH Session JSONL | 9 个近期会话中 7 个调用 native `web_search`，2 个未调用；均大量调用 `decision_hub_research` | 证明触发不确定，不是工具未安装 |
| 当前 live Hub run | 4 条可见 run：`rejected`、`failed`、`research_only`、`failed`，没有 `completed/published` | 不支持“产品已交付” |

其中离线测试和静态门是工程证据；真实事件价值、长期稳定性、预测效果和成本只能由独立 live canary/前瞻观察证明，不能由文档或 replay 代替。

## 3. 实际代码架构

### 3.1 当前运行拓扑

`infra/dsh/run-product.sh` 实际启动 Hub API、realtime worker、evolution worker、research MCP、research worker，然后在宿主机启动官方 DSH Web。`apps/hub_worker/composition.py` 再把这些进程装配到同一个 Python 业务系统中。

实际链路可以概括为：

```text
Browser
  -> 官方 DSH Web / Session / Agent Loop
       -> Decision Hub Host + Client plugin
       -> DSH native tools + decision_hub_research + synthesis tool
  -> Hub API / durable Run / SQLite
       -> research worker
            -> LangGraph evidence-round graph
                 -> DSH Web runtime
                      -> MCP/Gateway/provider adapters
       -> Evidence / Fact / PIT / Gate / Artifact / Outbox / Outcome
  -> Decision Desk（管理和审计视图）
```

这不是“几个插件组合后自然得到产品”，而是一个多进程、双状态源、双 UI、内外两层编排的系统。DSH JSONL/Session 是 Harness 事实，Hub SQLite 是业务事实；职责分离本身合理，但当前转换层过多，失败时很难判断是模型、Prompt、DSH、Graph、Gateway、Provider 还是旧实例造成的。

### 3.2 已经实现的部分

- Hub API、durable Run、Event/Evidence/PIT/Gate、Outbox、Outcome/Evaluation 和迁移体系。
- DSH Web Host bridge：readiness、submit/status/cancel、callback、session correlation、版本 hash。
- DSH Client plugin：把 Run/Evidence/Gate 等业务摘要投影到官方 Web。
- 受限 research/synthesis Tool、MCP/Gateway、provider adapter、replay/fake 运行时。
- LangGraph checkpoint、bounded evidence rounds、失败安全和部分 continuation。
- Decision Desk、Operations、Research detail、SSE/Query view 以及较完整的离线测试资产。

这些实现足以称为一个认真搭建的工程骨架，也足以支持受限内部试用；它们还不能替代产品价值验收。

### 3.3 架构中的主要过度复杂点

`packages/orchestration/langgraph/graphs/agentic_research_graph.py:60-72,186-218,368-415` 明确把 DSH 作为内层模型/工具循环，同时 LangGraph 决定下一 evidence round、预算、停止和最终快照。`apps/hub_worker/composition.py:254-347` 又在 runtime、execution mode、capability allowlist 之间做第三次选择。

这带来四类重复：

- DSH 和 LangGraph 都拥有“是否继续研究”的判断。
- Prompt、Graph、Gateway 都描述工具预算、失败和 fallback。
- DSH JSONL、runtime adapter、trace mapper、Gateway 和 Query view 逐层重建同一运行事实。
- SDK、Web、Replay、Fake 三套 runtime 仍同时出现在组合根，用户难以确认正在跑哪条主线。

保留 Hub 的 durable lifecycle、checkpoint 和确定性 Gate 是合理的；具体研究策略、工具选择、角色和 continuation 应尽量回收到 DSH profile/plugin 组合。LangGraph 应收敛成生命周期、恢复和确定性业务 Gate，而不是第二个研究 Supervisor。

## 4. DSH 原生插件集成判断

### 4.1 结论：官方形式接入成立

`extensions/dsh/decision-hub/package.json:30-43` 声明了 `dsh.bundle.patch` 和 `dsh.client.platform=web`；`src/index.ts:8-10` 使用 Cordis Host plugin 的 `name/inject/apply` 入口；`infra/dsh/run-web.sh:106-113` 使用官方命令：

```bash
dsh plugin --profile web add /.../extensions/dsh/decision-hub
```

上游 DSH 由 `infra/dsh/upstream.lock.json:2-16` 锁定为 `0a53fb55... / 0.1.2-alpha.2`，并由 `verify-upstream.mjs` 检查公开 Session/Web/bundle/client seam。因此不能说“完全没有按 DSH 原生插件做”。

当前一个包内包含：

- Host export：`@decision-hub/dsh-plugin`
- Research Tool export：`@decision-hub/dsh-plugin/research-tool`
- Synthesis Tool export：`@decision-hub/dsh-plugin/synthesis-tool`
- Web Client export：`@decision-hub/dsh-plugin/client`

这符合 DSH 官方插件安装和 profile 组合方式。

### 4.2 但“产品已经拆成多个可组合插件”不成立

当前只有一个 npm 安装单元、一个泛化的 `decision_hub_research` facade 和一个 synthesis Tool。`extensions/dsh/decision-hub/src/research-tool.ts:30-45` 的 Tool schema 要求模型一次处理约 14 类参数；实际 official macro、market、search、fetch、provider route、账本写入都在 `apps/research_mcp/main.py:73-179` 与 Python Gateway/adapters 内部再分发。

因此准确说法是：

> **官方 DSH 插件边界已建立，但窄能力插件的组合产品化尚未完成。**

原始规划并没有要求“所有业务都必须做成 DSH npm plugin”。`ADR-0005`、`ADR-0009`、`ADR-0013` 和 `docs/platform/ASSET_AND_EXTENSION_MODEL.md` 明确采用双层模型：Product Extension/Domain Pack 负责产品事实、Gate、Outcome 和资产；DSH Native Plugin 负责 Harness 内的 Tool、Skill、MCP、Subagent、Hook、UI、Provider 等能力。Role Profile 主要是声明式组合，不默认是代码插件。这个边界与用户提出的“model 和 harness 可替换，长期资产应是插件/领域能力”的方向一致。

建议的插件拆分应针对独立变化和独立验收单元，而不是把账本拆进 DSH：

```text
@decision-hub/dsh-bridge
@decision-hub/dsh-research-tools
@decision-hub/dsh-crypto-macro（profile/bundle，不持有账本）
provider/search/telemetry plugins
        -> 一个总 bundle/profile 组合安装
```

其中 `official-document`、`verified-fetch`、`macro-market`、`crypto-spot`、`crypto-derivatives` 可以是窄 facade；底层 Gateway 可共享。用户不应该手动安装十几个包，产品仍应提供一个审核过的总 bundle。

### 4.3 安全隔离边界

锁定的 DSH Web bundle 会禁用 base 中的 bash、filesystem、skill、tool-web 等 agent-plane rows；产品 preset 再按需启用 research、synthesis、web、subagent、todo 和 compaction。当前 Web preset 没有发现 shell/filesystem 暴露，这是正确的安全方向。但隔离依赖 bundle 的加载顺序和具体 profile；不能把同一 preset 复制到 headless/SDK 就假设仍然安全，必须对每个 profile 做 tool catalog acceptance。

另外，`compose.yaml:3-17` 把 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`SUB2API_API_KEY` 放在公共环境锚点中，`hub-api`、realtime、evolution、MCP 和 research worker 全部继承。现场 `hub-api` 容器也确认能看到这些变量。Provider 密钥应只注入真正发起 Provider 请求的进程；API、realtime 和 evolution 不应获得模型密钥，并应增加 secret-scope contract test。

## 5. `web_search` 未稳定触发的根因

### 5.1 工具确实注册了

锁定上游 `packages/bundle/base/cordis.patch.yml:440-468` 已注册：

- `@deepseek-ai/dsh-web`
- `@deepseek-ai/dsh-web-search-deepseek`
- `@deepseek-ai/dsh-tool-web`

产品 preset `infra/dsh/presets/decision-research/agent.cordis.yml:42-56` 又显式设置 `search: true`、`fetch: true`。近期 DSH Session 的 `request/header` 也能看到 `web_search`、`web_fetch`、`decision_hub_research` 和 synthesis Tool。因此问题不是“DSH 自带能力不存在”。

### 5.2 触发主要依赖 Prompt 和模型选择

`packages/runtime_adapters/dsh_runtime/profile.py:222-245` 只是要求模型在 hard gap 前使用 native `web_search`，没有 deterministic router、`tool_choice`、缺调用补偿或模型不可用时的明确调度。实际日志显示：

- 近期 9 个会话中 7 个调用了 native `web_search`，2 个完全没有调用；
- `dsh_15f196...`：`11 decision_hub_research`、`4 decision_hub_synthesis_submit`、`0 web_search`、`0 web_fetch`；
- `dsh_175919...`：`12 decision_hub_research`、`2 decision_hub_synthesis_submit`、`1 web_search`。

也就是说，工具注册和工具触发是两个不同问题。模型可能直接调用 Hub research facade、因为它看起来更像“正式任务工具”，从而跳过 discovery search；也可能在一次失败后直接进入 bounded stop。

### 5.3 双搜索面和 allowlist 冲突

`infra/dsh/run-product.sh:61-75` 默认的 live `DECISION_HUB_RESEARCH_CAPABILITIES` 是：

```text
official.macro,market.cross_asset,market.crypto_derivatives,web.fetch
```

其中没有 `web.search`。`result_mapper.py:504-529` 又把 native 搜索标为 `dsh.native.web_search`，并明确它不能直接进入 Hub Evidence；模型必须先调用 native search 拿 locator，再调用 Hub `web.fetch` 或 typed capability 才能获得可入账 Evidence。

这条信任链本身有理由，但现在让模型同时看到：

- DSH `web_search`
- DSH `web_fetch`
- `decision_hub_research(capability_id=web.search)`（视配置）
- `decision_hub_research(capability_id=web.fetch)`

这形成了两个命名、两个预算、两个失败语义和两个搜索面。应统一为一个 model-facing `discovery_search`：

```text
discovery_search
  -> 确定性 provider router（DSH native primary / 明确 fallback）
  -> locator
  -> verified fetch / typed fact
  -> Hub Evidence
```

搜索 locator、网页正文和精确 market fact 必须在契约和 UI 中明确区分。若模型没有完成必需 discovery，系统应自动继续到未尝试路径或返回可解释的失败，而不是静默 finalize。

### 5.4 Native Search 还有独立的 Provider 前提

上游 `@deepseek-ai/dsh-web-search-deepseek` 使用独立的 Anthropic-compatible `/messages` endpoint（默认 `https://api.deepseek.com/anthropic/v1/messages`），共享 `DEEPSEEK_API_KEY` 但不共享聊天模型的 base URL；一次 search 是一次完整辅助模型请求，有独立延迟、token、成本和失败语义。聊天模型能用不代表 Search provider 能用。当前 `DshNativeWebSearchTransport` 这个 Python 名称还容易让人误以为它就是 DSH Session Tool，telemetry 和文档应改成明确的 provider route 名称。

## 6. 规划要求与真实实现矩阵

| 规划/产品要求 | 当前真实状态 | 判断 |
|---|---|---|
| Core/Harness/Domain Pack 分层 | Hub、DSH adapter、Pack 和 Provider 目录存在 | 方向正确，但边界仍有多层映射和重复运行时 |
| 官方 DSH Web 原生 Host/Client/bundle | 已实现且 build 通过；插件测试当前为 `58 passed, 2 failed` | 接入方向成立，但当前制品质量门未通过，仍需 fresh-start/单实例发布门 |
| DSH Session/Agent Loop/Tool/Subagent/compaction 复用 | 已复用 | 正确，不应再自建第三套 ReAct |
| `crypto_macro` requirement/Gate/Role/Pack | 声明式 Pack 和 Gate 存在，Role 主要由 preset/persona 组合 | 部分完成；Pack 与 DSH Role 的可见组合还不清晰 |
| Search -> Source Registry -> Fetch/typed attestation | 有 adapter/Gateway 和 canary 资产；默认触发不确定，完整产品链未证明 | 未完成 |
| 多 venue crypto、OI delta、event return | 当前计划 PD-02C；现有能力不足以满足全部窗口和独立性要求 | 未完成 |
| 分钟级 macro/cross-asset、expectation pricing | FRED 主要是日频；规划中的 intraday/expectation provider 尚未完成 | 交付阻断 |
| 事件前采样和 T-30m/T0/T+窗口 | 有 EventWatch 代码和 schema，但 Stage Charter checklist/未来事件端到端证据仍不足 | 未完成 |
| 自动 Event -> Run -> 报告 -> 通知 -> recheck | 有 scheduler/worker/outbox/recheck 组件和 canary | 组件存在，真实主动链路未验收 |
| 人类可读 Inbox/Report/Readiness/Cost | Decision Desk 和 DSH Client 有骨架 | 页面契约红灯，成本/事实 readiness 尚未形成可交付体验 |
| Outcome/Evaluation/Experience | Kernel/evolution 资产存在 | 工程资产有，真实前瞻闭环没有 |
| 至少 14 天或 20 个前瞻事件 | 尚未完成 | 价值交付阻断 |
| 预测/盈利/自动交易 | 规划明确禁止自动交易，当前 live 没有成功发布结果 | 不能对外宣传 |

## 7. 当前缺口及优先级

### P0：必须先解决，否则不能称为版本

1. **单一可复现发布物**：把当前选定实现收敛成 release branch/commit；锁定 DSH source、plugin build hash、Docker image digest、Compose project、DSH_HOME、migration head 和配置来源。报告、截图和运行日志必须携带同一 build identity。
2. **单实例 owner**：启动前锁定 API/MCP/DSH Web/DSH_HOME；拒绝旧实例和多个 compose project；提供显式 stop/cleanup 和唯一 URL。当前至少 5 个 DSH Web 实例的 hash 为 `af90a4...`、`fdf17a...`、`ad2913...`、`9a2408...` 等，ready 但互不一致。
3. **统一 readiness**：产品入口必须检查迁移 head、Hub API、MCP 业务握手、Provider/Search 配置、capability catalog、worker heartbeat、DSH Host、preset/tool catalog、plugin hash 和 build identity。现有 `/health/ready` 仅调用 `HealthService.status()`，而它主要做 `SELECT 1`、运行数和 source 统计（`apps/hub_api/main.py:238-248`、`packages/kernel/.../health.py:11-26`）。
4. **恢复全量红线**：修正 `ResearchPage.test.tsx:17-23` 的 `requirement_id` 夹具；加入列表到详情、report/artifact 和失效实例的 E2E。当前 Decision Desk test 不能作为绿门。
5. **搜索确定性路由**：只保留一个 model-facing discovery 入口，明确 native primary、fallback、预算、credential 和 Evidence 归属；未调用必需搜索时自动补偿或显式失败。
6. **镜像和密钥边界**：产品启动必须 `build` 或核验 image digest，source/image/plugin hash 不一致时 fail closed；把 Provider secret 从公共 Compose 环境中移出，按进程最小权限注入。

### P1：完成个人可用的 research-only 产品

1. 收敛生产路径为 DSH Web；SDK/Replay/Fake 仅保留测试、回放和 canary，不让用户在运行时猜测模式。
2. 将 DSH 研究 facade 拆成窄、可测试的 capability plugins/facades；默认参数和 Session/PIT/time/cost 由受信上下文填充，减少模型填写 14 类字段的机会。
3. 完成 PD-02C/02D：多 venue crypto、OI delta、事件窗口、intraday macro 和 expectation pricing；免费 delayed proxy 必须与 licensed live 明确区分。
4. 完成一个未来官方事件的 watch -> pre/post snapshot -> DSH Run -> report -> notification -> recheck 真实闭环。
5. 页面默认展示 requirement readiness、source attempt、stale/no-baseline、provider route、成本 partial/unknown、Role/Pack/Runtime/Gate 版本，不要求用户读 raw JSON 或终端。

### P2：价值和长期质量

1. 建立 14 天或 20 个事件观察，记录 hard coverage、引用有效率、PIT violation、p95、成本、失败率和 owner usefulness。
2. 自动 Outcome/Evaluation/Experience candidate 只能由真实结果和 owner review 驱动；禁止自动修改 Gate、自动 Promotion 或自动生成补丁。
3. 再考虑第二领域、ASR、PPT、多用户或公共插件市场。当前没有第二真实调用方，不应继续扩平台抽象。

## 8. 交付时间判断

以下估算以“立即冻结范围、停止新增平行架构、2 名全职工程师 + 1 名产品/领域 owner、外部 Provider 凭据和预算及时可用”为前提：

| 目标 | 预计时间 | 必要条件 |
|---|---:|---|
| 工程 RC | 4-6 周（约 2026-10-02 至 2026-10-16） | clean checkout、单实例、readiness、红测修复、search route、fresh browser E2E、迁移/build 全绿 |
| Personal usable research-only pilot | 6-9 周（约 2026-10-16 至 2026-11-06） | RC 之上完成 typed facts、事件窗口、主动闭环，并自然观察至少 14 天 |
| 对外小范围稳定试点 | 不早于上述 pilot 结果 | 20 个前瞻事件或 14 天门槛、成功率/成本/可读性/owner usefulness 达标 |
| 可靠交易决策/盈利结论 | 不能按开发工期承诺 | 需要真实数据许可、长期 Outcome/Brier/成本后结果和独立风险评估 |
| 自动交易 | 当前不在交付计划 | 需另立合规、风控、执行和资金权限项目 |

若只有 1 名兼职开发者，上述工程区间至少乘以 1.5 至 2。日期不是承诺；任何一个 P0 或 Provider 许可阻塞都会顺延。特别是 14 天观察是自然时间，不能用 replay 或文档压缩。

## 9. 建议的后续研发路线

### 第 0 周：冻结和恢复可信绿线

- 建立唯一 release branch 和 clean checkout 验收目录。
- 清理/停止旧 DSH、Hub、MCP 实例；每次启动只允许一个 compose project 和一个 `DSH_HOME`。
- 生成唯一 `release identity`：Git commit、DSH upstream commit、plugin hash、image digest、migration head、preset hash。
- 修复前端红测；加入 `release-check`，一次执行 Python/TS tests、lint/type/codegen/docs、plugin build、Desk build、migration、compose config。

### 第 1-2 周：收敛能力边界

- 设计并测试 `discovery_search -> verified_fetch/typed_fact` 单一路由。
- 把模型不应填写的 request/session/time/budget 字段移到 Host/Gateway 上下文。
- 生产只保留 DSH Web runtime；SDK/replay/fake 改为显式 acceptance 参数。
- readiness 增加真实 MCP/capability/provider/worker/tool catalog 检查，并在页面显示失败原因。

### 第 2-4 周：补齐事实和主动链路

- 完成 PD-02C/02D 的 typed providers、事件窗口、baseline/no-baseline/迟到事件语义。
- 用一个未来官方事件跑通 watch、采样、Run、Session、Evidence、报告、通知和 recheck。
- 做浏览器桌面/移动 E2E：状态变化、列表/detail、报告引用、console 无异常、页面无 raw JSON/溢出。

### 第 4-6 周：RC 和小规模 pilot

- 固定 5-10 个真实任务作为 release canary，比较 hard coverage、freshness、p95、cost、artifact publication 和失败原因。
- 所有 run 可从报告追溯到 Evidence、source、provider attempt、DSH trace ref 和 build identity。
- 只有 P0/P1 通过，才开始 PD-07 的自然观察；不在此之前宣称产品价值。

## 10. 研发质量控制方案

每张任务卡必须有以下证据，缺一项不得标记完成：

- canonical schema/迁移变化及 codegen diff；
- 一个先失败后通过的 BDD/TDD；
- 离线命令和精确结果；
- live/replay 样本 ID、runtime/profile/provider/build identity；
- 有 UI 时的桌面和移动截图、console 检查和页面 hash；
- 失败、未完成项、`continue/retain/stop` 结论。

建议把下面的门禁设成唯一 release gate：

```text
clean checkout
  -> install from lockfile
  -> migration upgrade/head check
  -> Python + TypeScript + plugin + browser tests
  -> compose build/config
  -> fresh single-instance boot
  -> API/MCP/DSH/tool-catalog/readiness checks
  -> 5-10 fixed live canaries
  -> release identity and artifact archive
```

必须持续执行的专项回归：

- Contract/codegen/旧 payload compatibility；
- provider timeout/429/5xx、redirect/domain/license、partial cost；
- semantic substitution（BTC derivatives 不能满足 Fed expectation pricing）；
- event window/PIT/future leakage/no-baseline/late event；
- search required-but-not-called、native search unavailable、fallback exhaustion；
- DSH Host accepted-before-cancel、重启、callback 丢失和最多一次提交；
- worker/watch/sampler/outbox/recheck 幂等；
- 列表/detail/report 一致性；
- 一个 profile 的安全 tool catalog，而不是只测 preset 文本；
- browser E2E 和移动 viewport。

## 11. 明确禁止事项

- 不再通过新增文档、Stage 名称或“completed”标记替代代码和运行证据。
- 不在 P0 绿线、语义 Gate 和事件窗口完成前继续新增 Provider、领域或 UI 功能。
- 不同时向模型暴露多个竞争的 search/fetch/research 入口。
- 不把 `/health/ready=200` 当成产品可用；不把 DSH Session/JSONL 当业务账本。
- 不用 replay、单次漂亮报告或 14 天缺数据观察替代真实价值验收。
- 不自动 Promotion、自动修改 Gate、自动交易或把未经授权的网页摘要当 hard Evidence。

## 12. 最终裁决

当前代码值得保留，DSH 原生接入方向也无需推倒重来；问题在于边界和交付控制没有收敛。当前版本可以继续作为内部工程试点，让单 owner 观察失败安全、Session/Trace 和受限 Evidence 链；不能作为“已经可交付的主动交易研究产品”发布。

下一步唯一正确动作是：**冻结当前范围，建立单一可复现版本，修复红线和 readiness，统一搜索路由，补齐事实/事件窗口，再用真实前瞻事件决定 promote、retain 或 stop。**
