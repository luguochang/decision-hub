# 长任务恢复与交接协议

## 每次任务开始

按顺序只读：

1. 根目录 `INDEX.md`；
2. `docs/context/CURRENT_STATE.md`；
3. `docs/context/CURRENT_DECISIONS.md`；
4. 当前 Stage Charter 和 Task ID；
5. 相关 ADR、canonical schema、模块 README、代码和测试。

不要默认重读 2000 多行总架构或历史聊天。只有发生冲突、需要新架构决定或 Stage Charter 明确引用时才读取专项章节。

## 开始实现前

任务必须能写成：

```text
目标：一句话
价值：一个可观察结果
允许路径：明确目录
禁止路径：明确目录/行为
验收：最多三个核心断言 + 完整验证命令
停止条件：何时必须回到 owner/ADR
```

使用 `tools/context/build_task_context.py` 生成 `tmp/task-context.md`。临时 Manifest 不是长期真源。

## 必须停止的情况

- proposed ADR/Stage 尚未获得 owner gate；
- 需要新增第二套 Agent Loop、账本、DTO、状态机或 Provider client；
- 需要把金融字段加入 Platform Core；
- 需要读取未来信息才能让测试通过；
- 需要扩大插件权限、网络域、Secret 或自动交易能力；
- 同一缺陷连续补丁两次仍未解决；
- 实现与已接受 ADR/schema 冲突。

## 每次任务完成

同一任务内更新：

- 受影响模块 README；
- `docs/IMPLEMENTATION_STATUS.md` 和 `docs/ROADMAP.md`；
- 用户可见行为对应的 `CHANGELOG.md`；
- 新决策对应的 ADR；
- `docs/context/CURRENT_STATE.md` 的事实、目标、未决和最新验证；
- 必要时更新 `CURRENT_DECISIONS.md`，但不复制 ADR 正文。

完成后保留准确的测试结果、commit 和未完成项。未运行的检查不能写成 passed，离线 fixture 不能写成真实收益或生产稳定性。

## 文档寿命

| 文档 | 保存内容 | 禁止内容 |
|---|---|---|
| `INDEX.md` | 导航和当前一句话状态 | 长推理和实现细节 |
| `docs/context/` | 当前事实/决策投影/恢复协议 | 新架构事实和历史流水账 |
| `docs/platform/` | 跨领域长期所有权 | 单阶段 Task 进度 |
| `docs/domains/` | 领域 doctrine/evidence/gate/eval | Harness 内部实现 |
| `docs/stages/` | 当前阶段范围和验收 | 永久架构真源 |
| `docs/decisions/` | 跨模块决定及后果 | 每日进度 |
| 模块 README | 局部公开入口和约束 | 全局路线图 |
| `tmp/`/`research/` | 临时探索和外部调查 | 实现授权 |

## 当前交接（2026-09-01）

- `PRODUCT-CLOSEOUT-01 / E2-L` 已通过。当前唯一执行入口是
  [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，
  最终证据是
  [E2-L 官方 DSH Web 真实产品验收记录](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。
- 正式 Run `run_04dc1a46fd1e4c3b988750e18b0e9581` 与完整 DSH Session
  `dsh_c73364bc0fd7221f8102fc5181e8c4b17ab3736afc282379e311c67534d4c778` 完成 2 轮、
  12/12 Tool Call、15 条 Evidence、66.7% hard coverage 和 `research_only / tool_budget`；
  DSH 与 Decision Desk 终态一致。
- scheduler/worker 自动创建 child Run `run_7df306876d114e09b8e83507d06f3ef0` 并再次完成
  2 轮、12/12 Tool Call。Search timeout、FRED stale 和 unknown cost 被如实保留。
- 完整质量门为 Python `390 passed`、DSH Plugin `53 passed + build`、Decision Desk
  `10 passed + build`，Ruff、Pyright、codegen、module docs、Compose、migration、recovery、
  replay matrix、callback/Web restart、rollback、桌面/窄屏和截图 hash 均通过。
- 当前状态是 `pilot_ready=true / pilot_usable=research_only / Fixed active / DSH
  candidate-shadow / automatic_trading=false`。旧 `/v1/pilot/readiness` 是 R1-L Fixed 管线的
  legacy 状态，不是 E2-L 判定，不能为收口另建第二套 readiness。
- 下一次恢复不得继续 E2-L 功能开发，只执行 `E3-PROSPECTIVE-OBSERVATION`：至少 14 天或
  20 个高影响事件，记录覆盖、延迟、失败率、成本、人工复核时间、usefulness、Outcome、
  Brier、方向准确率和净收益，最终只作 `promote / retain_baseline / stop` 裁决。
- 不扩展 ASR、PPT、第二领域、多用户、自动交易、公共插件市场，不切 active pointer，
  不修改历史 Run/Evidence/Artifact/Forecast/Outcome，不提交 secret。

## 历史交接（以下条目只保留实施和失败证据）

### E2L-03 当前复核结果（2026-09-01）

- Durable Gateway、Host model-step watchdog、terminal callback 并发互斥、Query/View 部分进度和 Desk capability 错误展示已完成工程/replay 收口。
- 没有最终 `ResearchSessionResult` 时，Query/View 从 `research_trace_events`/`research_evidence` 投影已调用工具、轮次、已保留证据和全部 capability failures；空的刚入队 Run 不显示虚假的“证据不足”。真实失败 Run 的只读副本已复验为 `dsh / Round 1 / 10 tools / 47 Evidence / critical_data_unavailable`，剩余 hard gaps 与 `coverage.gaps` 一致，causal/horizon 不生成。
- 质量门：离线 Python `373 passed`、DSH plugin `43 passed`、Decision Desk `10 passed`、Pyright/Ruff/codegen/module docs/frontend build/Compose config/diff check/core acceptance 均通过；三类官方 replay、跨进程恢复、callback/Web restart 和 locked rollback 复验通过。
- E2L-03 离线质量门已完成；官方 DSH Web 已执行 live 失败/model-timeout/主动补证复验，但 live capability acceptance 和当前 revision 的长期 screenshot/hash 资产仍 blocked。保持 `pilot_ready=false`、`pilot_usable=false`，Fixed active、DSH candidate/shadow。

### E2L-E 真实复验追加（2026-09-01）

- 官方 DSH Web 新隔离实例 `52120` 已从页面建立研究任务并通过正式“重新研究”入口创建
  child Run；首次配置错误 Run `run_f202fb32e66c449aaf302bc8ec60e402`、模型步 timeout
  Run `run_37bcd4011a4098d2ffb65ef943dc3b63` 和完整补证 Run
  `run_fb3b54cf46efe2a03b13e160f190673b` 均保留，不改写历史。
- 正确本机 Provider 配置为 `codexai-gpt55/gpt-5.5` + `openai-completions`；非流式
  Responses/Chat 均 200，Chat 流式约 9 秒完成，Responses 流式 idle timeout。该修正只
  改本机 gitignored `data/dsh-live/settings.yaml` 的 adapter route，并非核心代码迁移。
- `run_fb3b54cf...` 完成 1 round、16 次 DSH capability 调用、20 条 Evidence、38 条
  Trace；8 次 `web.search` 中 4 次 timeout，之后按 deny-by-default 在
  `market.crypto_derivatives` 停止，未产生 causal/horizons/Artifact/Forecast。
- 当前结论：E2L-E 已执行但 `blocked by live capability acceptance`；`pilot_ready=false`、
  `pilot_usable=false`、Fixed active、DSH candidate/shadow。不得把本次 Evidence 数量解释
  为充分度或把失败改写成 `no_trade`。Owner 已接受控制书中的最小只读
  Official/Market 准入建议；下一步只按 manifest allowlist 重跑 Search/Market 主线，并
  补齐截图/hash 资产，不扩大到社区插件、active Promotion 或新领域。
- 最小 typed capability 独立 canary 已通过，新隔离实例 `8210/8212/52220` 已在 MCP/worker
  两侧加载 `web.search,official.macro,market.cross_asset,market.crypto_derivatives`。DSH
  Plugin 通过官方 `conversation.view` 新增“研究报告”页签，未覆盖 Chat/Trajectory；上游
  无公开 default-view seam，首次仍进入 Chat。剩余门是从该官方页面建立新的 live Run 并
  归档桌面/窄屏、console、Run/Gate 证据。

本次执行目标：按 [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) 只闭合 E2-L live capability 门；E1/E2-R 与 E2L-A/B/C/D 的离线实现已完成。固定源码闭包内的 `packages/test-support/llm-replay` 只是同版本 keyless acceptance transport，不是生产依赖。active Promotion、ASR/PPT/第二领域仍不在本目标内。

本轮新增的 [产品执行与最终验收总表](../product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)
是产品视图、两层循环、插件边界、阶段 checklist 和停止门的跨文档执行入口；若与
schema、ADR 或 Stage Charter 不一致，先修正事实源，不在聊天中临时改方向。

- 已完成：R2-R-06E 真实事件/12-case 验收；Runtime 结论为 `retain_baseline`，Fixed active，DSH candidate/shadow。
- R2-R-07 的 G1-A/B/C/D 与 G2-A/B 已实现并通过离线回归；授权的 live Search canary 以 `research_capability_timeout` 安全失败，E2-L 仍 pending。
- E2L-03-A/B/C/D 的代码已实现：`deadline_at` migration `0026`、live cutoff/freshness 分离、Evidence-only degraded fallback、失败 Run durable projection和 DSH Client 终态 fallback；E2L-03-E 官方 Web 已执行，当前因 live capability acceptance blocked。
- 本轮自动检查（历史 2026-08-31）：`git diff --check`、module docs、canonical contract、Python 328 tests、Ruff、Pyright、Decision Hub plugin 18 tests/build、Decision Desk 9 tests/build、Compose config、官方 `infra/dsh/acceptance.sh`、三场景 acceptance 和 recovery/rollback 均通过。新增 replay 只读守卫已用隔离端口 `64619` 的真实浏览器复验；截图和 hash 已写入验收记录。该日 PRODUCT-CLOSEOUT-EXEC-01 的 Compose 镜像 build 曾被 Docker Hub registry 超时阻塞，旧 `65349` 页面不能作为后续验证入口。
- ADR-0012/0013 与 DSH-first 总装设计已获 owner 接受；[DSH-NATIVE-CORE](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 现为 `engineering acceptance complete / product value pending`。NATIVE-00..05 和 NC-01..07 已闭环，Fixed 仍 active、DSH candidate/shadow、Replay 诊断态。2026-08-31 另完成一次仅限本机短文本的真实 `gpt-5.5` Responses 多轮 canary；`data/dsh-live/` 为 Git ignored 的本机状态，不等于 Search/Market live value。后续不得继续堆工程功能；G2-C/NATIVE-06 live value、active Promotion、ASR/PPT/第二领域仍需独立 Gate。
- 禁止：手工补可信时间戳、扩大 capability/network 权限、切 active pointer、修改历史 Run/Evidence/Artifact/Forecast/Outcome、引入 ASR/PPT/第二领域或新基础设施。

## PRODUCT-CLOSEOUT-EXEC-01（2026-08-31）

### 2026-09-01 C1/C2 真实入口与 Provider 失败收口

- 全新 Compose/官方 DSH Web 实例使用 `8140` Hub API、`8142` Research MCP、`51890` DSH Web；工作区 `Crypto Macro Trader` 通过官方 `workspace/create` 注册。
- 官方 DSH 页面已真实显示 `建立研究任务`。提交文本后创建 `run_d224133b625d4632b5e7e90c24ce94e7`，并由 `dsh-web` 建立受管 DSH Session；Session link、callback、Trajectory/JSONL 引用和 Hub Run 均存在。
- Host/Client 新增 canonical `research-run-queued.v1`、稳定幂等 intake、`run_id` 同源状态查询和页面轮询；DSH 原生 Chat 未被拦截或复制。
- DSH 实际调用 `web.search`，但当前 Provider 的 web-search 请求超时；Run 以 `provider_timeout/dsh_web_deadline_elapsed` fail-closed，未生成 Evidence/Artifact/Forecast。该失败不是产品可用证据，详细命令和结果见 [PRODUCT-CLOSEOUT-EXEC-01 2026-09-01](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md)。
- 最新收口为 Python `343 passed`、DSH plugin `34 passed`、Decision Desk `9 passed`，静态、契约、build、Compose、三场景和 recovery 全通过。E1/E2-R 已完成；`pilot_ready`、`pilot_usable` 继续为 false。
- 下一次恢复只做 E2-L/E2L-E 能力准入和质量门：先跑全量离线门，再用全新官方 DSH Web 实例完成 live Evidence/Gate/报告或记录真实失败；不得把 Provider 失败改写为 `no_trade`，不得修改 active pointer 或历史账本。

本轮已新增 [产品执行总方案](../product/PRODUCT_EXECUTION_MASTER_PLAN.md)，作为架构与执行总参考；2026-09-01 起唯一顶层交付合同是 [最终产品交付实施书](../product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)，C1-C7 详细 checklist 见 [产品实现与最终验收方案](../product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)。已完成 research MCP 宿主端口映射和启动器 URL 注入。离线工程门全部通过：328 Python tests、Ruff、Pyright、codegen、module docs、DSH plugin 18 tests/build、Decision Desk 9 tests/build、core acceptance、Compose config 和 bash syntax。该日 Compose build 的 registry 超时作为历史阻塞保留；随后 2026-09-01 已用全新端口完成官方 DSH Web、Host/Client、Research MCP 和 durable Run/Session bridge 运行验证，详见下方最新执行节。

## 产品交付收口（2026-08-31；历史方案，当前执行状态见 2026-09-01 节）

独立方案见 [最终产品交付与主线闭环方案](../product/FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)，现已由 owner 确认；详细实施约束和验收门见 [PRODUCT-CLOSEOUT-01 Stage Charter](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)。该阶段把未完成事项拆成 C1-C7，并锁定最终用户只进入 DSH Web、Hub 作为后台控制面/资产库、Decision Desk 作为管理后台；当前仍未达到实时产品交付，正式 Web Runtime、缺口驱动补证、真实来源 Search/Market、通知和 owner 价值观察尚未同时通过。

以上边界已经确认，第一张任务卡是 `C1`，不是重新设计架构；该任务已在 2026-09-01 的新实例完成工程验证。实施中仍不得扩大到自动交易、ASR/PPT、第二领域或未经审计社区插件。

## 最新执行复核（2026-08-31 23:38；历史）

新增 [产品实现与最终验收方案](../product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)，并完成启动器根因修复：不再向 Compose 容器传入宿主机绝对 replay 路径；产品启动器在官方 Web 发布认证 URL 后通过 `workspace/create` 注册默认 `Crypto Macro Trader` 工作区。修复后的质量门为 Python 329、DSH plugin 18、Decision Desk 9，契约、文档、Ruff、Pyright、前端 build、Compose config 均通过。官方 Web success/partial_failure/insufficient_or_stale 三场景新 Run 证据已写入执行记录；该时点 Compose 镜像 build 仍因 Docker registry 超时未取得容器产品证据，后续由 2026-09-01 新实例补齐 C1/C2。

## PRODUCT-CLOSEOUT-01 实施授权（2026-09-01）

Owner 已接受 [产品实现与最终验收方案](../product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md) 的全部建议，授权把 C1-C7 作为一个完整产品目标实施和自测，不再逐卡等待口头确认。`live` 模式不能开放 `replay.research`；2026-09-01 已在隔离实例验证仅允许已审计的 `web.search`，其请求超时被 fail-closed。Official/Market typed capability 仍必须逐项通过 canary 才能晋级。只有扩大权限/费用、修改 Gate/账本/active pointer、自动交易、第二领域或第二套 Agent Loop 时才停止并重新请求 owner Gate。
