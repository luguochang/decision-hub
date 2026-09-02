# PRODUCT-CLOSEOUT-EXEC-01 执行与自测记录

日期：2026-08-31（Asia/Shanghai）
状态：`engineering gates passed / product acceptance blocked`
对应方案：[产品执行总方案](../product/PRODUCT_EXECUTION_MASTER_PLAN.md)
对应阶段：[PRODUCT-CLOSEOUT-01](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)

## 1. 本轮目标

在不改变 DSH、Decision Hub、LangGraph 和 Domain Pack 所有权边界的前提下，完成产品启动链的最小修复，
运行可复核的工程质量门，并记录 C1-C7 的真实完成度。不得用旧进程、旧镜像、replay 或静态文件替代新产品运行证据。

## 2. 本轮变更

### 2.1 启动链

- `compose.yaml` 为 `research-mcp` 增加宿主机 loopback 端口映射：`${DECISION_HUB_RESEARCH_MCP_PORT:-8002}:8002`。
- `infra/dsh/run-product.sh` 增加 `DECISION_HUB_RESEARCH_MCP_PORT` 和宿主机 `DECISION_HUB_RESEARCH_MCP_URL` 导出。
- 修复 Compose replay 夹具格式错配：MCP 默认使用现有 `research-evaluation-case.v1` PIT case；research worker 继续使用独立的 `research-runtime-replay.v1` fixture。
- 修复启动器向容器注入宿主机绝对 replay 路径的问题；启动器现在不覆盖 Compose 的容器内默认路径，只有显式传入的路径才会被转发。
- 启动器补充官方 `workspace/create` 注册：默认将 `data/dsh-live/Crypto Macro Trader` 注册为 DSH Workspace，并把认证 URL 作为唯一用户入口输出；该路径和注册动作尚待 Compose 新实例通过后复核。
- 宿主机 DSH 使用 `http://127.0.0.1:<port>/mcp`；容器 research worker 继续使用 `http://research-mcp:8002/mcp`。
- 不改变 DSH 上游源码、不复制 Agent Loop、不增加账本或 provider client。

### 2.2 长期执行入口

新增 [PRODUCT_EXECUTION_MASTER_PLAN.md](../product/PRODUCT_EXECUTION_MASTER_PLAN.md)，收敛：

- 最终产品定义和唯一用户入口；
- DSH Web、官方插件、Hub、LangGraph、Domain Pack、Capability Gateway 和 Decision Desk 的责任；
- 两层循环、三份持久化状态和双层插件模型；
- 目标代码结构、依赖方向和禁止重复造轮子规则；
- SDD/BDD/TDD/ADR、上下文压缩、变更留痕和文档同步；
- C1-C7 任务、工程/产品/价值三层验收门和停止开发规则；
- 当前执行目标与 Compose/浏览器产品验收的未决条件。

## 3. 工程质量门

以下命令均在本轮实际运行：

| 检查 | 结果 |
|---|---|
| `bash -n infra/dsh/run-product.sh infra/dsh/run-live-web.sh infra/dsh/run-web.sh infra/dsh/stop-product.sh` | passed |
| `docker compose config --quiet` | passed |
| `git diff --check` | passed |
| `./.venv/bin/python tools/docs/check_module_docs.py` | passed（13 modules） |
| `./.venv/bin/python -m tools.contract_codegen check` | passed |
| `./.venv/bin/pytest -m "not live" -q` | passed（328 tests） |
| `./.venv/bin/ruff check packages apps migrations tests tools` | passed |
| `./.venv/bin/pyright packages apps tests tools/canary` | passed（0 errors/warnings/informations） |
| `pnpm --dir extensions/dsh/decision-hub test` | passed（18 tests） |
| `pnpm --dir extensions/dsh/decision-hub build` | passed |
| `pnpm --dir apps/decision-desk test` | passed（9 tests） |
| `pnpm --dir apps/decision-desk build` | passed |
| `./.venv/bin/python tools/core_acceptance.py` | passed（328 Python tests + core checks） |

## 4. Compose 构建结果

实际执行：

```bash
DECISION_HUB_API_PORT=8030 \
DECISION_HUB_RESEARCH_MCP_PORT=8003 \
DSH_PRODUCT_PORT=50880 \
docker compose -f compose.yaml build
```

结果：`blocked`。Dockerfile 依赖的 `docker.io/library/node:22-bookworm-slim` manifest 请求在 Docker Hub 超时：

```text
failed to do request: Head "https://registry-1.docker.io/v2/library/node/manifests/22-bookworm-slim": context deadline exceeded
```

这是外部 registry 网络阻塞，不是代码质量门失败。由于没有得到新镜像的构建证据，本轮不启动 Compose，不把旧的
`8000/8002` 进程或旧镜像当成 `PRODUCT-CLOSEOUT-EXEC-01` 通过证据。网络可用后必须用同一端口组合重试 build，再执行
`up`、readiness、heartbeat、MCP 宿主可达性和官方 DSH Web 验收。

## 4.1 隔离 Search canary 结果

已按脚本要求显式设置 `DECISION_HUB_SEARCH_LIVE_CANARY=1` 执行一次只读、域名白名单为
`federalreserve.gov`、超时 25 秒、预算上限 `$0.10` 的 `web.search` canary。结果未取得
可用 Search 证据：上游 Responses 请求在约 20 秒 capability deadline 内被取消，Gateway
准确记录为：

```text
error_code=search_provider_failed
origin=provider
cause_code=searchcapabilityerror
retryable=true
capability_id=web.search
```

脚本以非零退出。未生成证据、未写生产账本、未切换 active pointer。这个失败说明当前
中转站/Responses Search 路径不能作为已验证的实时事实来源；不能把 Search 摘要当作官方
事实，也不能因此让报告绕过 `research_only/no_trade`。后续若重试，必须保持同样的隔离、
只读、预算和 provenance 约束。

## 5. C1-C7 当前状态

| 阶段 | 状态 | 结论 |
|---|---|---|
| C1 启动器/Trader workspace/双向链接 | `partial` | 启动器、官方 workspace 注册和链接代码存在；新 Compose + 官方 Web 运行证据待 build/up |
| C2 正式 DSH Web Runtime | `engineering verified / live product pending` | 上游/Host/Client/recovery 工程证据存在；新实例正式运行仍待 Compose/浏览器验收 |
| C3 缺口驱动多轮补证 | `candidate verified / live capability pending` | replay/失败语义有证据；真实 Search/Official/Market 未证明 |
| C4 真实能力准入/canary | `pending` | 默认 deny-by-default；真实 canary 需要独立 owner 授权和临时目录 |
| C5 报告/通知/观测 | `partial` | Query/View、Desk、local outbox 和插件视图存在；新实例端到端页面/通知待验收 |
| C6 Outcome/Evaluation/个人资产 | `offline verified / prospective pending` | 账本和回放已通过；14 天或 20 个事件观察尚未开始 |
| C7 单机运维交付 | `partial` | stop/backup/recovery/runbook 存在；Compose 实际运行和长时心跳待验证 |

因此本轮不能把 `PRODUCT-CLOSEOUT-01` 标记为完成，也不能宣称实时市场可用、预测准确、盈利或 DSH 已 Promotion。

## 6. 下一步唯一执行顺序

1. 在 Docker registry 可用时重试隔离 Compose build（API `8030`、MCP `8003`、DSH Web `50880`）。
2. 启动 `hub-api`、realtime/evolution/research worker 和 `research-mcp`，验证 `/health/ready`、heartbeat 与宿主 MCP URL。
3. 启动无 replay patch 的官方 DSH Web，验证页面、Session、Trajectory、插件状态和无 console error。
4. 用统一三场景矩阵验证 success、partial failure、low-authority/insufficient：同时核对 DSH JSONL、Host callback、Hub Ledger、Desk Query/View、截图和版本/hash。
5. 把真实 Search/Official/Market canary 和 C1-C7 证据补回本文件；未通过门保持 `pending`。
6. C1-C7 全部通过后进入 14 天/20 事件观察，形成 `promote / retain_baseline / stop` 决策包；不自动扩展 ASR、PPT、第二领域、多用户或自动交易。

## 7. 事实边界

- 离线测试通过只证明契约、错误语义、账本、恢复和前端构建在固定输入上可靠。
- Replay fixture 只用于回归和诊断，不能证明实时来源、信息充分度、预测优势或收益。
- 一次 Provider canary 只证明协议/结构化输出兼容，不证明 Search 质量或长期稳定性。
- 历史 Run、Evidence、Artifact、Forecast、Outcome、Evaluation 和 migration 均保持只增不改。

## 8. 修复后复核（2026-08-31 23:38 Asia/Shanghai）

本节是对前述执行记录的追加证据，不改写历史结果。完成启动器 replay 路径修复和官方 workspace 注册改动后，重新运行：

| 检查 | 结果 |
|---|---|
| `bash -n infra/dsh/run-product.sh` | passed |
| `git diff --check` | passed |
| `./.venv/bin/python tools/docs/check_module_docs.py` | passed（13 modules） |
| `./.venv/bin/python -m tools.contract_codegen check` | passed |
| `./.venv/bin/pytest -m "not live" -q` | passed（329 tests） |
| `./.venv/bin/ruff check packages apps migrations tests tools` | passed |
| `./.venv/bin/pyright` | passed（0 errors/warnings/informations） |
| `pnpm --dir extensions/dsh/decision-hub test` | passed（18 tests） |
| `pnpm --dir extensions/dsh/decision-hub build` | passed |
| `pnpm --dir apps/decision-desk test` | passed（9 tests） |
| `pnpm --dir apps/decision-desk build` | passed（仅 bundle size warning） |
| `docker compose config --quiet` | passed |

使用全新临时目录和随机端口重新运行官方 DSH Web 三场景：

| 场景 | Run | 结果 | 证据边界 |
|---|---|---|---|
| `success` | `run_7fbc4d0f07474ec79952b1dec077af74` | `completed / publish / sufficient`，9 Evidence，0 failure | 固定 replay 的成功链 |
| `partial_failure` | `run_27209494f0844effb7912ed7c03e2221` | `rejected / insufficient`，1 Evidence，1 capability failure | 失败 provenance 保留，Gate fail-closed |
| `insufficient_or_stale` | `run_617e3cffb5bf4f38893740256e952135` | `rejected / insufficient`，1 Evidence，1 capability failure | 低覆盖/缺口不发布方向性 Forecast |

三场景均生成新的 DSH Session JSONL 和独立 Hub Run；进程已在证据收集后停止。官方 Web 页面标题为 `DeepSeek Harness`，无控制台 error/warn；replay 页面明确显示只读提示并禁用输入。该页面证明官方 Web/插件/Session 的工程行为，不证明实时网络或预测价值。Compose build 仍需 Docker registry 可用后重试。
