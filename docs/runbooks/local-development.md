# 本机运行手册

容器的 Web 构建阶段使用 AWS Public ECR 上的 Docker Library 官方 Node 镜像，
Python 阶段使用 GHCR 官方 uv 镜像；两者都固定不可变 digest。这样不依赖本机当前
不可达的 Docker Hub，同时不改变上游镜像内容。离线或企业镜像环境可以通过 build
arg `NODE_IMAGE` / `UV_IMAGE` 覆盖为内部等价镜像，但 release 验收必须固定
digest，不能使用浮动 tag。

前端依赖仍由 `pnpm-lock.yaml` 和 Corepack 固定；默认 `NPM_REGISTRY` 使用本机网络
可达的 `https://registry.npmmirror.com`，并启用 BuildKit pnpm store cache。企业环境
可覆盖为内部 registry；registry 只改变下载位置，不改变 lockfile 中的包版本和完整性
校验。

## API 与 Decision Desk

```bash
./.venv/bin/uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000
```

生产静态前端来自 `apps/decision-desk/dist`，由 `hub-api` 同源提供；开发时另开 Vite：

```bash
pnpm --dir apps/decision-desk dev
```

## 文本分析

```bash
curl -X POST http://127.0.0.1:8000/v1/observations \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -d '{"text":"Powell says rates may stay higher for longer.","source_id":"manual","language":"en"}'
```

返回 `run_id` 后查询 `/v1/runs/{run_id}/view`。不要直接读取 SQLite 或 Graph state。

完整文本链验收（不触发外部模型）使用 Fake Runtime：

```bash
./.venv/bin/pytest tests/e2e/test_api_flow.py -q
```

真实 OpenAI-compatible Provider 只通过 opt-in canary，先按 [`TDD/SDD 与自测规范`](../engineering/TDD_SDD_SELF_TEST_STANDARD.md) 注入环境变量，再运行：

```bash
./.venv/bin/python -m tools.canary.run_live_text_canary
```

仓库内的通用 canary 不会替你持久化密钥；不要把 key 写入本文件、shell 脚本、数据库或日志。DSH 原生 live Web 的个人本机配置例外使用 `data/dsh-live/.env`，该目录已整体 Git ignored，文件权限必须为 `600`，生产环境应使用 Secret Manager。canary 失败时只按 Provider 兼容性问题处理。

### DSH 原生 live Web（本机）

启动器会复用本机持久化的 `data/dsh-live/.env` 和 `settings.yaml`，不需要每次重复输入 key；它使用官方 DSH Web/Session/Trajectory/JSONL，不使用 replay fixture：

```bash
./infra/dsh/run-live-web.sh --port 50220
```

首次打开必须使用启动日志打印的带 `?token=...` 的完整 URL；裸 `/` 没有本地认证 cookie 时返回 401。当前默认 route 是 `codexai-gpt55/gpt-5.5` 的 `openai-responses`，未探测到推理能力时保持 `reasoning: off`，不要擅自声明 `high`/`max`。一次短文本多轮成功只证明 Provider 与 Session 基础兼容，不证明实时 Search、事实覆盖、预测准确率或盈利。

## 数据与备份

默认数据目录是 `data/decision-hub/`。迁移由 Alembic 管理：

```bash
./.venv/bin/alembic upgrade head
```

SQLite backup、restore、integrity check 和 retention command 在 R0 运维补齐前，不得把手工删除数据库文件当清理方式。

升级已有本地数据库时始终先执行 `./.venv/bin/alembic upgrade head`。`0007_run_cost_nullable` 会把旧版 `runs.cost_usd` 的 `NOT NULL` 约束迁移为可空，`0008` 至 `0010` 增加 durable source cursor/health、通知重试状态和 `next_poll_at`，`0011` 至 `0015` 增加 Workbench/Evolution 资产、metadata 和 PIT provenance，`0016` 增加 durable Evolution Job/lease 和 service heartbeat，`0017` 增加研究 Evidence/Snapshot 字段，`0018` 增加 research Run lease，`0019` 增加 scheduled recheck 的可领取时间与父 Run lineage，`0020` 增加 Research Result/Trace/Command；迁移前应按下方命令做一次 backup。`0001_initial` 是冻结的 R0 schema snapshot，不能再导入当前 ORM metadata。

## R1 来源、调度与通知

R1 的来源只接受已授权的 source preset 或人工转写文本。普通开发/测试使用 fixture，不触网。需要手工验证 source worker 时显式开启来源；需要行情评估时再显式开启市场 adapter：

```bash
DECISION_HUB_SOURCES_ENABLED=1 ./.venv/bin/python -m apps.hub_worker.main --once
DECISION_HUB_SOURCES_ENABLED=1 DECISION_HUB_MARKET_ENABLED=1 \
  ./.venv/bin/python -m apps.hub_worker.main --once
```

本地通知输出到 `data/decision-hub/exports/notifications.jsonl`。来源/行情/通知失败只更新健康或 outbox 重试状态，不能重新触发分析或改变已提交 Artifact/Forecast。真实 endpoint、邮件和市场执行质量必须分别以 opt-in canary 验证。

## R1-L 单 owner 试运行预检

进入长期 worker 前先执行只读预检。它不会访问外部 Provider、来源、行情或 SMTP：

```bash
./.venv/bin/python -m apps.hub_worker.main --preflight
```

只有明确设置 `DECISION_HUB_PILOT_MODE=1` 并增加 `--pilot`，worker 才会把相同报告作为启动门；失败时不会创建 scheduler、轮询来源或消费 outbox。local 通知默认写入 `data/decision-hub/exports/notifications.jsonl`，Email 必须显式设置 SMTP host/sender/recipient，密码只从当前进程环境注入。

运行不触网的 R1-L acceptance（含 worker fail-closed、入口测试、outbox/recovery、PIT replay 和 backup/restore）：

```bash
./.venv/bin/python tools/pilot_acceptance.py
```

readiness 通过只表示本地配置和数据边界满足要求；真实来源授权、Provider/SMTP 连通性、长期运行和业务效果仍需 owner 单独授权的 Live Pilot Gate。

### 单 owner 启停与故障处理

启动长期 worker 前使用 `--preflight`，确认报告为 `ready` 后再使用 `--pilot`：

```bash
DECISION_HUB_PILOT_MODE=1 \
DECISION_HUB_LLM_ENABLED=1 \
DECISION_HUB_SOURCES_ENABLED=1 \
DECISION_HUB_MARKET_ENABLED=1 \
./.venv/bin/python -m apps.hub_worker.main --pilot
```

停止时向前台进程发送 `Ctrl-C`（SIGINT），或由进程管理器发送 SIGTERM；不要删除数据库文件。worker 只在 tick 边界停止，已提交的 Event/Run/Artifact/Outbox 会保留，下一次启动会由 durable source state、Run 和 checkpoint 恢复。来源、行情、Provider 或通知失败只进入 health/error/outbox retry 状态，不重新执行已经提交的决策。

Provider 的总超时、重试次数、token budget 和可选 cost budget 只在 `ProviderConfig` 配置；成本未知时记录 `unknown`，预算不足时 fail-closed。不要在 worker、Graph 或通知 adapter 内另写重试/计费逻辑。日志和 readiness 输出只允许固定错误码、版本、状态和 hash，禁止记录 API key、SMTP password、Authorization 或原始 Provider JSON。

## Core durability and replay

SQLite backup and integrity checks use the checked-in operations tool:

```bash
./.venv/bin/python -m tools.ops.database backup \
  data/decision-hub/db/decision_hub.sqlite3 \
  tmp/backups/decision-hub.sqlite3
./.venv/bin/python -m tools.ops.database integrity tmp/backups/decision-hub.sqlite3
```

Run the fixed PIT fixture without a live Provider:

```bash
./.venv/bin/python -m tools.replay.run_fixture \
  fixtures/replay/powell-higher-for-longer.json \
  --database tmp/replay.sqlite3
```

The replay output is compatibility and reproducibility evidence. It is not a claim of forecast accuracy or profitability.

## R2-R Research Runtime 对照

R2-R-06C 只允许对不可变 PIT 数据集运行 Fixed 与 DSH 对照。先执行单 case canary；
它会写 raw report 和临时 DSH Session，但不会写 Evolution 账本或修改 active pointer：

```bash
DECISION_HUB_R2R_LIVE_EVAL=1 \
DECISION_HUB_LLM_ENABLED=1 \
DECISION_HUB_MODEL=gpt-5.5 \
DECISION_HUB_DSH_MODEL=gpt-5.5 \
./.venv/bin/python -m tools.research_evaluation.run_runtime_comparison \
  --case-id powell_stanford_20240403
```

只有 canary 的 DSH MCP、结构化输出、Evidence attestation、PIT、时延和失败记录均可
解释时，才运行 `--all`。全量完成后才将 Dataset/Candidate/Experiment/Result 登记到
现有 Evolution 账本；脚本始终返回 `active_pointer_changed=false`，Promotion 仍是 owner-only。
真实 Provider 不承诺 seed 复现，因此 Experiment 固定记录
`randomness_policy=provider_default`；SDK 未提供可信 token/cost 时保持 unknown。
