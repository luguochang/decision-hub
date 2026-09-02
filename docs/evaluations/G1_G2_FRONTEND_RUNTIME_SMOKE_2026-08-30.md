# G1/G2 前端运行链 Smoke 与根因修复记录

日期：2026-08-30（Asia/Shanghai）
任务：`R2-R-G1-G2-FRONTEND-SMOKE-20260830`
状态：`verified / historical smoke closed; live Search remains pending owner gate`

## 1. 目标

从 Decision Desk 前端实际提交一段文本，验证同一版本的完整本机链路：

```text
浏览器 -> Vite/API target -> Hub API -> durable Research Run
  -> research worker -> bounded evidence-round -> Result/Trace
  -> API Query/View/SSE -> 前端可读详情
```

本次验收必须回答：

1. 页面连接的是当前源码和预期数据目录，而不是遗留进程或 replay 演示库；
2. API 返回、worker 状态、Run 终态、SSE 事件和页面展示相互一致；
3. `fake`、`replay`、`provider`、`live search` 的运行模式被明确标识，不能把 fixture 当网络事实；
4. 缺证据、失败 capability、停止原因和 retryability 能被人理解；
5. 发现问题时记录现象、根因、修复和回归证据，不通过静默 fallback 掩盖。

## 2. 授权与边界

本轮已授权的是本机、隔离、可回放的前端到后台研究链验收及其启动配置修复。普通验证不访问外部网络，不执行真实 Search canary，不发送通知，不修改 active pointer，不改写历史 Run/Evidence/Artifact/Forecast/Outcome。

不在本轮范围内：

- 外部 Provider 或搜索服务的稳定性、事实充分度和预测收益；
- ASR、PPT、第二领域、自动交易、多用户和公共插件安装；
- 重写 DSH、LangGraph、账本、canonical 状态机或另造 Agent Loop；
- 终止或覆盖用户已有的 8000/8010/8020 等进程和数据目录。

## 3. 当前环境核对（修复前）

| 入口 | 观察结果 | 结论 |
|---|---|---|
| Vite `5174` | proxy 固定指向 `127.0.0.1:8000` | 当前前端可能连到旧 API |
| API `8000` | Provider 配置的旧进程；`/v1/research/runs` 返回 SPA HTML | 不是当前研究 API |
| API/worker `8010` | 临时 SQLite；`hub-api=fake`、`research-worker=replay` | 能跑链路，但只是 replay，不是实时检索 |
| API/worker `8020` | Provider/DSH candidate 环境，使用另一临时库 | 不能未经 owner gate 当作 live canary |
| Research MCP | 进程存在，但是否与 8020 URL 一致需单独探测 | 不作为离线验收依赖 |

已观察到的 replay 提交结果：任意输入都绑定同一 fixture，`runtime_id=research-replay`、hard coverage `1/6`、stop `critical_data_unavailable`、1 条 evidence、12 条 trace。这是可解释的离线结果，但页面若不显示 runtime mode，容易被误读为实时研究。

## 4. 根因与修复目标

### 4.1 本轮必须修复

**R-FE-01：API 地址没有统一配置入口**
根因：Vite proxy 和 App 提交逻辑硬编码 `127.0.0.1:8000`/相对路径，隔离实例无法安全验证，旧进程会被误用。
修复：Vite 使用 `VITE_API_PROXY_TARGET`；前端 API client 使用统一 `VITE_API_BASE_URL`/`apiUrl()`；所有请求包括文本提交都走同一入口；同源生产默认保持相对路径。

**R-FE-02：运行模式和数据边界不够醒目**
根因：Research 页面显示 worker heartbeat，但没有把 `runtime mode`、数据目录边界和“非实时”状态作为首屏可读事实。
修复：复用 Operations canonical DTO，在 Research 首屏明确显示 worker 状态、`fake/replay/provider` 模式和 canary 状态；错误时不使用缓存或 demo 数据。

### 4.2 记录为下一张修复卡（不在本轮扩大实现）

**R-RUN-01：Compose 的 DSH 默认与镜像依赖不一致**
`Dockerfile` 使用 `uv sync --no-dev --no-install-project`，但 `deepseek-harness-sdk` 位于可选 `[dsh]` extra；Compose 默认 `DECISION_HUB_RESEARCH_RUNTIME=dsh`，真实入队时可能得到 `dsh_sdk_unavailable`。下一张任务卡必须在启动 preflight 中显式失败，或在经批准的镜像中安装 extra，且补 Docker build 证据。

**R-RUN-02：research worker lease identity 固定**
`build_research_worker` 没有传入实例 identity，多个 worker 会共用 `research-worker` lease owner，而 heartbeat instance 不一致。单机首版可暂时保持单实例约束；多实例前必须修复并增加恢复测试。

**R-RUN-03：没有 worker 时 Run 无限 queued**
研究 API 只负责 durable enqueue；没有 worker 时 admitted Run 没有 queue age/stuck/expired 终态。下一张任务卡必须定义 queue timeout/stuck 投影和启动门，前端不能把 queued 伪装成 researching。

## 5. BDD/TDD 验收门

### BDD 场景

- **配置隔离**：给定 `VITE_API_PROXY_TARGET=http://127.0.0.1:8030`，前端所有 `/v1` 与 `/health` 请求都到 8030；不得命中 8000 遗留进程。
- **离线研究**：给定 replay runtime 和隔离 SQLite，提交文本返回 `202 + run_id`，Run 经 `queued -> terminal`，终态、stop reason、coverage、evidence、trace 均能从 Query/View 读取。
- **运行模式诚实**：给定 `replay`/`fake`，页面显示模式和非 live 状态；不得把 fixture 文本渲染为外部事实或收益结论。
- **失败可解释**：给定失败 capability，页面显示 canonical error code、origin、capability、retryability 和保留证据数，不显示 raw DSH/Provider JSON。
- **前端 API 异常**：给定上游返回 HTML 或非 canonical JSON，页面显示 API 不兼容/不可用，而不是空白或 demo 数据。

### TDD 顺序

1. 先补 `apiUrl()` 与 Vite env 的失败测试；
2. 实现统一 URL 入口和测试；
3. 运行前端 Vitest/build；
4. 启动隔离 API/worker/Vite，浏览器真实提交；
5. 记录 POST、轮询、SSE、截图、console；
6. 运行 Python、契约、文档、静态和迁移检查。

## 6. 本轮执行计划

| 步骤 | 产物 | 状态 |
|---|---|---|
| P0 核对进程、配置、文档 | 本文第 3 节和命令证据 | `done` |
| P1 统一 API target/base URL | Vite/client/App + 前端回归测试 | `done` |
| P2 隔离启动当前版本 | 临时数据目录、API/worker/Vite 端口矩阵 | `done` |
| P3 浏览器端到端提交 | API 原始结构、Run 终态、SSE、截图和 console | `done` |
| P4 根因回归与完整检查 | pytest/ruff/pyright/contract/docs/frontend | `done` |
| P5 收口 | 状态、CHANGELOG、HANDOFF 和下一张任务卡 | `done` |

## 6.1 先前失败的质量门（已修复）

在本轮首次重跑全仓离线测试时得到 `293 passed, 1 failed`：
`tests/evolution/test_research_worker.py::test_research_worker_projects_agentic_result_into_existing_ledger` 没有产生 `research.recheck.scheduled`。根因是测试 fixture 固定在 `2026-08-29`，而 `DurableResearchWorker` 的 `CommitDecisionService` 使用真实墙上时钟 `2026-08-30`，历史 `next_review_at` 被错误判为过期。

修复不是修改断言或历史数据，而是把 worker 的注入时钟贯穿 `ResearchRequestFactory`、`CommitDecisionService` 和完成记录，并让回放测试显式使用 fixture clock。目标是让“是否创建 scheduled child”只由回放时间决定，不随今天日期漂移。定向回归随后为 `11 passed`，本轮最终全仓离线结果为 `296 passed`。

## 6.2 已完成的隔离浏览器执行

P2/P3 已在当前源码上完成：

| 组件 | 配置 |
|---|---|
| Hub API | `127.0.0.1:8030`，临时数据目录 `/tmp/decision-hub-ui-smoke.cja8Vc` |
| Research worker | `--role research --interval 1`，`DECISION_HUB_RESEARCH_RUNTIME=replay`，仓库 `research-worker-replay.json` |
| Decision Desk | `127.0.0.1:5175`，Vite proxy 指向 8030；浏览器请求保持同源相对路径 |
| 外部网络 | 未访问；Provider/LLM/source/market 均关闭 |

浏览器通过 Research Command Center 提交：`测试事件：请核验一条宏观事件，并在事实不足时明确列出缺口和停止原因。`。POST 返回 `202`、`status=queued` 和唯一 `run_id`；约 1 秒内由 worker 领取并完成两轮 replay。最终 API `/v1/research/runs/{run_id}` 摘要：

```text
status=research_only
runtime_id=research-replay
artifact_id=art_271b67e9adc2427bbc37f654918afab2
hard_coverage=16.7% (event_identity only)
remaining_hard_gaps=policy_or_data_delta, expectation_pricing,
  macro_transmission, crypto_spot_confirmation, derivatives_crowding
stop_reason=critical_data_unavailable
evidence=1, rounds=2, horizons=0, trace=12
```

SSE `GET /v1/research/runs/{run_id}/events?after=0` 返回 12 条规范化事件，最后是 `session_stopped`，终态后连接关闭。页面截图已通过浏览器验收输出捕获；首屏可见“后台研究循环在线 · replay”、`research_only`、17% hard coverage、停止原因和缺口列表，未出现 raw DSH JSON 或 Provider payload。浏览器没有产生应用级 error；早期一次跨源直连因 CORS 被阻断，改为只使用 Vite proxy 后复测通过。

这次执行同时证明了 fail-closed 修复：即使 replay 模板带有 `short` horizon，最终 Research View 不展示方向性 horizon，Artifact 的三条 Forecast 均为 `no_trade`，Gate 不是 `publish`。因此页面显示的是“证据不足，安全停止”，而不是看起来完整但未经事实支持的交易结论。

P2/P3 结论：`offline_frontend_chain_validated`。这不是 G2-C live canary，也不是实时事实、预测准确率或收益证明。

## 6.3 P4 最终质量门

2026-08-30 在修复后的当前工作树上重新执行，结果如下。所有命令均未访问外部 Provider/Search：

```text
pytest -m "not live" -q                         296 passed
tools/research_acceptance.py                    26 passed
tools/pilot_acceptance.py                       15 passed
tools/live_observation_acceptance.py            42 passed
tools/core_acceptance.py                        passed (296 tests + durability/replay)
ruff check .                                    passed
pyright                                         0 errors, 0 warnings
tools.contract_codegen check                    passed
tools/docs/check_module_docs.py                 passed (13 modules)
pnpm --dir apps/decision-desk test -- --run     9 passed
pnpm --dir apps/decision-desk build              passed
docker compose config --quiet                   passed
alembic heads                                    0021_research_error_provenance (head)
git diff --check                                 passed
```

附加 API 检查：未知 `/v1/not-a-real-route` 返回 HTTP `404` 和 JSON `{"detail":"api_route_not_found"}`，不会再把 SPA `index.html` 当作 API 响应。隔离 API、Research worker 和 Vite 重新启动后，浏览器提交得到 `202`，Run `run_9a1a75c170ce4da8978721bbd31794fb` 进入终态，Artifact `art_9232327c170ce4e45a0994bd8b4e18391`，SSE 返回 12 条规范化事件并在 `session_stopped` 后关闭。

## 6.4 启动竞态记录

本轮第一次同时启动全新数据目录的 API 与 Research worker 时，worker 失败并报告 SQLite `table events already exists`。根因是两个进程同时执行 Alembic 首次迁移：一个进程已创建表但尚未提交版本号，另一个进程重复执行 `0001_initial`。

随后按生产 Compose 的依赖顺序（API 健康后再启动 worker）重新启动，worker 正常在线并完成上述 smoke。该现象不影响已存在数据库的业务结果，但说明“并发首次迁移”不是可靠的人工启动方式。当前约束与处理如下：

- Compose 通过 `depends_on: condition: service_healthy` 保证 API 先完成迁移；
- 本地手工启动必须先等待 `/health/ready`，再启动任意 worker；
- 不删除数据库、不重写历史 Run；
- 将跨进程 migration lock/独立一次性 migrate job 作为后续启动可靠性任务，未在本轮引入新的锁实现，避免在没有跨平台验收时增加补丁。

这次错误已保留在本记录中，不能把“并发启动失败后重试成功”解释为一次性启动可靠性已证明。

## 6.5 最终 smoke 结论

当前可证明：同一源码、同一隔离数据目录下，Decision Desk -> Hub API -> durable Research Run -> replay worker -> Research View/SSE 的离线链路可运行；运行模式、证据覆盖、硬缺口、停止原因和 `no_trade` Gate 投影一致，页面没有展示未经证据支持的方向性 Forecast。

当前不能证明：真实 Search/Provider 长期稳定、实时事实充分度、预测准确率、盈利能力或 DSH Promotion。G2-C live Search canary 仍需 owner 单独授权；在授权前保持 `Fixed active`、`DSH candidate/shadow`。

## 6.6 后续有界任务

本轮不继续堆叠功能。后续只保留以下两张任务卡，均需单独验收：

| 任务卡 | 目标 | 进入条件 | 禁止事项 |
|---|---|---|---|
| `R2-R-G2-C/D-LIVE-SEARCH` | 在临时目录、只读 allowlist 和单次 deadline 下验证真实 Search；用六类事实包检查 authority/freshness/source-count，缺失时保持 `research_only/no_trade` | Owner 明确授权 live canary | 不切 active pointer、不接未经审计插件、不宣传实时稳定或收益 |
| `R2-R-G0-STARTUP-MIGRATION-BOUNDARY` | 为 API/worker 首次启动提供跨进程 migration lock 或一次性 migrate job，并补并发启动、崩溃恢复和 Windows/Docker 证据 | 形成独立 SDD/ADR，明确锁的所有权和 stale recovery | 不在业务 worker 内静默重试迁移、不删除数据库、不改写历史 Run |

在上述任务完成前，当前产品形态的停止点是“可观察的离线/replay 研究工作台”：可以人工提交文本并看到证据缺口和安全停止，但不能把它当作自动实时市场决策服务。

## 7. 证据记录模板

执行完成后必须填写：

- API/worker/frontend 端口和源码 revision；
- 数据目录、runtime mode、是否触网；
- 输入摘要（不写密钥和敏感正文）；
- POST 返回的 `event_id/run_id/status/status_url`；
- Run 状态转移、`stop_reason`、coverage、evidence、trace 数；
- SSE 事件数、最后 sequence 和终态；
- 页面截图绝对路径、主要可见字段、浏览器 console；
- 发现的问题、根因、修复文件、回归测试；
- 未完成事项及是否影响 G1/G2 Gate。

## 8. 阶段结论规则

本轮只能得出“本机离线前端链路可运行”或“链路存在明确缺口”。它不能证明真实网络检索、事实充分度、预测准确率、盈利或生产稳定性。若 API/worker/frontend 不是同一源码和数据目录，验收结论为 `invalid_environment`，不得用页面截图宣称完成。
