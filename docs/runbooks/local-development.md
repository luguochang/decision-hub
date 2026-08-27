# 本机运行手册

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

真实 OpenAI-compatible Provider 只通过 opt-in canary，先按 [`TDD/SDD 与自测规范`](../engineering/TDD_SDD_SELF_TEST_STANDARD.md) 注入临时环境变量，再运行：

```bash
./.venv/bin/python -m tools.canary.run_live_text_canary
```

不要将 API key 写入本文件、`.env`、shell 脚本、数据库或日志；canary 失败时只按 Provider 兼容性问题处理。

## 数据与备份

默认数据目录是 `data/decision-hub/`。迁移由 Alembic 管理：

```bash
./.venv/bin/alembic upgrade head
```

SQLite backup、restore、integrity check 和 retention command 在 R0 运维补齐前，不得把手工删除数据库文件当清理方式。

升级已有本地数据库时始终先执行 `./.venv/bin/alembic upgrade head`。`0007_run_cost_nullable` 会把旧版 `runs.cost_usd` 的 `NOT NULL` 约束迁移为可空，以准确表示 Provider 未返回 usage 的 unknown 成本；迁移前应按下方命令做一次 backup。

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
