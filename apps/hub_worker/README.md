# Hub Worker

## 目的

`hub-worker` 是三个可独立运行的 composition root，共享 Product Kernel、canonical contracts 和 SQLite WAL：

- `--role realtime`：按 durable source state 轮询来源，推进并采样 EventWatch 窗口，执行已 admission 的 R0 Run，扫描到期 Forecast，并消费 committed outbox。
- `--role research`：领取 `strategy_version=research.v1` 的 admitted Run，执行 R2-R bounded evidence-round graph，并将结果投影到既有 Snapshot/Artifact/Forecast/Outbox 账本；默认使用受限 DSH Research Runtime，测试/replay 可注入替代 runtime。
- `--role evolution`：扫描 feedback/failure/evaluation/schedule trigger，领取 durable Evolution Job lease，复用 LangGraph Supervisor 和 EvaluationRunner 生成候选，停在 `pending_owner_review`。

三个 role 都写入 `service_heartbeats`；heartbeat 只表示进程存活，Run claim/Job lease 才表示任务所有权。进程管理器或 Compose 负责重启，应用不创建自守护线程。

Research worker 构造 canonical request 时只读同一 Hub 数据库中的 `EventWatch` 与 sample 状态。
无 Watch 的人工回溯事件显式投影 `event_watch=null`；只有 `captured` sample 可进入 DSH 的窗口
规划。worker 不补造 Watch、不伪造 baseline，也不放宽 Gateway 的 event/PIT 校验。

## 不负责

不创建第二套业务执行链、账本、队列、Agent loop、Gate 或发布权。来源仍只产出 `TextEnvelope`，worker 复用 Kernel admission/EventWatch、R0 LangGraph、R2-R bounded graph、确定性 Gate、账本和 outbox。EventWindowSampler 通过 composition root 注入，不能让 Provider 私有字段泄漏到 scheduler。Evolution/research worker 不得修改 source cursor、Gate、active pointer 或交易权限；DSH Session/JSONL 只作为运行轨迹。

## 运行

单 tick（离线/fixture）：

```bash
./.venv/bin/python -m apps.hub_worker.main --role realtime --once
./.venv/bin/python -m apps.hub_worker.main --role research --once
./.venv/bin/python -m apps.hub_worker.main --role evolution --once
```

常驻进程：

```bash
./.venv/bin/python -m apps.hub_worker.main --role realtime --interval 5
./.venv/bin/python -m apps.hub_worker.main --role research --interval 5
./.venv/bin/python -m apps.hub_worker.main --role evolution --interval 5
```

默认只消费 local outbox，且 `DECISION_HUB_SOURCES_ENABLED=0`、`DECISION_HUB_MARKET_ENABLED=0`、`DECISION_HUB_LLM_ENABLED=0`，因此普通 CI 和 fixture 验收不触网。显式启用来源、行情或 Provider 前必须经过 readiness/canary；密钥只从进程环境注入。

试运行前先执行本地预检：

```bash
./.venv/bin/python -m apps.hub_worker.main --preflight
```

输出只包含脱敏的 `PilotReadinessReport`。只有同时设置 `DECISION_HUB_PILOT_MODE=1` 并使用 `--pilot` 时，worker 才会把预检作为启动门；预检失败会在 scheduler、来源轮询和 outbox 之前退出。`--once` 不带 `--pilot` 继续保留 fixture/离线兼容行为。

本机 Compose（API、三个 worker role 和受控 research-mcp）：

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/v1/operations
```

Compose 的 API 和三个 worker 共享一个 `decision-hub-data` 卷，research worker 另使用 `decision-hub-dsh-sessions` 保存 DSH 会话轨迹；所有服务使用 `restart: unless-stopped`。SQLite WAL 只适用于该单机共享卷。跨主机部署必须另立存储 ADR。

Compose 默认 `DECISION_HUB_RESEARCH_RUNTIME=replay` 并绑定仓库 replay fixture，保证默认启动完全离线；镜像仍包含经过审计的 DSH extra，只有显式选择 `dsh` 并通过 Provider/capability canary 后才会调用外部模型。Research Run 没有 research worker 时会保持 queued，运维必须从 Operations heartbeat 判断是否具备执行条件。

EventWindow sampler 在 realtime composition 中复用 OKX/CoinEx typed adapter；默认关闭。只有
`DECISION_HUB_MARKET_ENABLED=1` 且显式传入测试 provider 或设置
`DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED=1` 才会写入
`$DECISION_HUB_DATA_DIR/event-windows`。该目录是内容寻址 payload archive，不是业务账本；真实
Provider 采样前仍需单独 canary 和授权。

默认 live sampler 还包含 OKX order-book crowding capture，保存 `crowding_signal` 和
`book_imbalance` 的 scalar proxy。该 proxy 只用于 Domain Gate 声明的衍生品窗口组合，不能被
解释为清算/杠杆数据；单个 provider 失败会保留在 capture failure 中，不阻断其他 provider 的
合法观察。

## 最近验证

`tests/e2e/test_outbox_worker.py`、`tests/e2e/test_realtime_source_flow.py`、`tests/pilot`、`tests/scheduler`、`tests/evolution`、`tests/operations` 和 `tools/live_observation_acceptance.py` 覆盖 scheduler、EventWatch capture、Job 幂等/lease/recovery、heartbeat、三 role 隔离、Operations API 和重启边界。Research Run 还支持 `available_at` + `parent_run_id` scheduled recheck；到期前不可 claim，完成 Run 不被原地改写。真实来源/行情/邮件/搜索/Provider 仍需分别显式 live canary，fixture 不能宣称稳定性或收益。

R2-R durable research 的离线退出门使用：

```bash
./.venv/bin/python tools/research_acceptance.py
```

该命令运行 fake/replay 测试、真实 Python 子进程 checkpoint recovery 和 Compose 配置检查；不会启动 DSH、访问网络或启用 `candidate` capability。恢复验收会证明 single Artifact/Evidence/Outbox 和第二次 worker `no_run`。真实 DSH/MCP/search/market canary 必须单独授权。

G1/G2-A/B 的 Search reliability/error provenance 和事实 manifest/replay 已实现，见
[R2-R-07 阶段卡](../../docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。worker 会保留
失败 Run 的具体来源和已完成 Evidence；不把失败重试解释为需要 owner 手工补数据。真实
Search canary（G2-C）仍须 owner 明确确认。
