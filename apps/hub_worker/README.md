# Hub Worker

## 目的
R1 单进程 composition root：按 durable source state 轮询已启用来源，执行已 admission 的 R0 Run，扫描到期 Forecast，并消费已提交 Artifact 的 outbox。

## 不负责
不创建第二套业务执行链；必须复用 Kernel application 与 LangGraph graph。

## 运行

```bash
./.venv/bin/python -m apps.hub_worker.main --once
```

`--once` 执行一个重复安全 scheduler tick。默认只消费 local outbox；设置 `DECISION_HUB_SOURCES_ENABLED=1` 才注册官方 source presets，设置 `DECISION_HUB_MARKET_ENABLED=1` 才调用 OKX public market adapter。普通 CI 和本机 fixture 验收均不触网。

试运行前先执行本地预检：

```bash
./.venv/bin/python -m apps.hub_worker.main --preflight
```

输出只包含脱敏的 `PilotReadinessReport`。只有同时设置 `DECISION_HUB_PILOT_MODE=1` 并使用 `--pilot` 时，worker 才会把预检作为启动门；预检失败会在 scheduler、来源轮询和 outbox 之前退出。`--once` 不带 `--pilot` 继续保留 fixture/离线兼容行为。

来源只产出 `TextEnvelope`，worker 复用 Kernel admission、R0 LangGraph、Gate、账本和 outbox；它不拥有第二队列、第二账本或发布权。

## 最近验证
`tests/e2e/test_outbox_worker.py`、`tests/e2e/test_realtime_source_flow.py`、`tests/pilot`、`tests/scheduler`。真实来源/行情/邮件仍需单独 live canary，不能由 fixture 宣称稳定性或收益。
