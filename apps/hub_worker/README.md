# Hub Worker

## 目的
R0 worker 进程入口，消费已发布 Artifact 的本地 outbox；未来在同一进程加入 scheduler、recovery watchdog 和 outcome jobs。

## 不负责
不创建第二套业务执行链；必须复用 Kernel application 与 LangGraph graph。

## 运行

```bash
./.venv/bin/python -m apps.hub_worker.main --once
```

`--once` 将本地通知写入 `data/decision-hub/exports/notifications.jsonl` 并按 `dedupe_key` 标记完成。它不触发分析、不调用外部渠道。

## 最近验证
`tests/e2e/test_outbox_worker.py`。
