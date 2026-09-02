# Live Observation 本机运行手册

适用阶段：`R2-L Live Observation Pilot`

本手册只覆盖单 owner、单机四个逻辑进程（API、realtime、research、evolution；Compose 另有受控 research-mcp）。默认配置完全离线，不调用外部 LLM、来源或行情；真实能力必须逐项显式启用并单独运行 canary。

## 1. 启动前检查

```bash
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/live_observation_acceptance.py
docker compose config --quiet
```

不要把 API key 写入仓库、Compose 文件、SQLite 或前端。需要真实 Provider 时只在当前运行环境或受控 `.env` 注入；`.env` 不得提交。

## 2. 默认离线启动

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/v1/operations
```

浏览器入口：`http://127.0.0.1:8000/`

默认应看到：

- `hub-api`、`hub-realtime-worker`、`hub-research-worker`、`hub-evolution-worker` 四个独立服务，以及 `research-mcp` 受控出口；
- runtime 是 `fake`，`provider_configured=false`，`live_canary_status=not_run`；
- research runtime 默认是 `replay`，使用仓库 fixture；回放不是实时网络事实；
- 来源和行情关闭；
- evolution worker 安装 release baseline，但不会自动 Promotion；
- 没有输入时两个 worker tick 均为 no-op。

## 3. 本机进程模式

不使用 Docker 时，四个终端共享同一个 `DECISION_HUB_DATA_DIR`。必须先启动 API 并等待健康检查成功，再启动三个 worker；不要让多个进程同时对全新目录执行首次 Alembic 迁移。

```bash
export DECISION_HUB_DATA_DIR="$PWD/data/decision-hub"
export DECISION_HUB_LLM_ENABLED=0
export DECISION_HUB_SOURCES_ENABLED=0
export DECISION_HUB_MARKET_ENABLED=0

./.venv/bin/uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000
# 另一个终端确认 API 已完成迁移：
curl -fsS http://127.0.0.1:8000/health/ready
./.venv/bin/hub-worker --role realtime --interval 5
DECISION_HUB_RESEARCH_RUNTIME=replay \
DECISION_HUB_RESEARCH_RUNTIME_FIXTURE="$PWD/packs/crypto_macro/fixtures/research-worker-replay.json" \
./.venv/bin/hub-worker --role research --interval 5
./.venv/bin/hub-worker --role evolution --interval 5
```

SQLite WAL 只允许这四个逻辑进程位于同一台机器并共享同一文件系统。跨主机部署前必须新立 PostgreSQL ADR。
研究 worker 必须与 API 共享同一 `DECISION_HUB_DATA_DIR`；没有它，Research Run 会保持 queued，不应把 queued 解释为已执行。
首次并发启动可能出现 `table events already exists` 的迁移竞态；当前 Compose 通过 API health dependency 规避，跨进程 migration lock/一次性 migrate job 仍是后续启动可靠性任务。

## 4. 显式启用真实能力

示例只表示配置入口，不代表 canary 已通过：

```bash
export DECISION_HUB_LLM_ENABLED=1
export DECISION_HUB_LLM_API_MODE=responses
export DECISION_HUB_PROVIDER_ID=openai-compatible
export DECISION_HUB_MODEL=gpt-5.5
export OPENAI_BASE_URL=https://your-approved-relay.example/v1
export OPENAI_API_KEY='set-in-current-shell-only'

export DECISION_HUB_SOURCES_ENABLED=1
export DECISION_HUB_MARKET_ENABLED=1

docker compose up -d --build
```

只有真实 Provider canary 成功且证据已记录时，才可把 `DECISION_HUB_LLM_CANARY_STATUS` 设为 `passed`。Search 还必须登记 canonical `CapabilityManifest`，由 owner 置为 `enabled` 或 `shadow`；未审计能力不会调用 transport。

## 5. 中断、恢复与隔离检查

停止 evolution worker：

```bash
docker compose stop hub-evolution-worker
```

等待超过五个 heartbeat interval 后，Operations 应显示 evolution worker 为 `offline`；API 和 realtime worker 继续在线。恢复：

```bash
docker compose start hub-evolution-worker
docker compose logs --tail=100 hub-evolution-worker
curl -fsS http://127.0.0.1:8000/v1/evolution/jobs
```

恢复后 Job 从 durable lease/state 继续。已经提交的 Candidate、Experiment、Result 和 raw report 必须复用，不能因重启重复创建；active pointer 仍只能由 owner Promotion/Rollback command 改变。

## 6. 故障定位

1. 先看 Decision Desk 的 Operations：区分进程、runtime、source、capability 和 job。
2. Job 的 `last_error_code`、`attempt/max_attempts`、`next_attempt_at` 判断是否等待重试。
3. `configuration_invalid`、PIT、schema、permission、budget 和 Gate 错误按永久失败处理，不应无限重试。
4. Provider timeout/rate limit/unavailable 只走有限重试；不要在业务步骤外再套第二层无限循环。
5. 日志不是事实源。最终以 SQLite 账本、Job、heartbeat、Candidate/Experiment/Result 和 owner audit 为准。

## 7. 停止与回滚

```bash
docker compose down
```

默认命令保留 named volume。不要使用 `docker compose down -v`，除非已经备份且明确要删除本机业务数据。功能回滚优先停止 evolution worker 或禁用 Search Capability；不要删除追加的 Candidate/Job/Experiment 历史。
