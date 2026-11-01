# D0-7D-DIRECTIONAL-CORE 自测报告（2026-09-18）

## 验收范围

本次只验收一条纵向链路：

`简单 BTC 输入 → 固定 7 日模板 → 已收盘日线 → daily-trend-v0 → strategy receipt → research_outlook → 首屏观点`

它验证研究观点能否复算和解释，不验证盈利、胜率，也不打开交易授权。

## 实现结果

- `research_intake.py` 将“现在怎么操作/最新操作建议”等短输入固定展开为已收盘日线、未来 7 个自然日的研究任务。
- `OKXHistoricalDataLoader` 支持 `crypto.btc_usdt.ohlcv_1d`，请求 `bar=1D`；历史适配器校验至少 60 根已闭合 candle，计算 SMA20、SMA60、20 日收益、波动、成交量比和 20 日高低点。
- 历史 worker 增加独立 `tick_daily()`，生产 `historical` role 会按 UTC 日边界幂等刷新 1D DataJob；原有 1h 刷新仍保留。
- `daily-trend-v0` 是无模型依赖的纯函数，保存输入 hash、参数、指标、方向、行动候选、触发条件、最强反方、失效条件和概率状态。
- `research_outlook` 与 `trade_authorized` 分离；未校准时概率为 `null`，前端显示“规则基线，概率未校准”。
- 有 7 日观点时，首屏显示方向、行动、触发、反方、失效和 Evidence，并隐藏旧的 30m/24h/72h 卡片，避免两套周期同时作为最新建议。

## 四类 replay 结果

这些是冻结的合成日线回放，用于验证分支语义，不冒充真实市场收益回测。

| 案例 | 输入 | 结果 | 行动候选 | receipt data hash |
|---|---|---|---|---|
| `bullish_trend` | 80 根单调上行日线 | `bullish` | `wait_pullback_or_breakout_confirmation` | `c028ae20aed256cdc62c7fef400dc0a65b26b82f54b1df3fd18465119fda1a69` |
| `bearish_trend` | 80 根单调下行日线 | `bearish` | `reduce_long_exposure_and_wait_for_reclaim` | `0349a8789f3ff260b2cdbd66ebab3e8c16b1f4a26b05bbad5b671097e506ab38` |
| `range_no_edge` | 80 根交替窄幅日线 | `range` | `wait_for_range_break_or_trend_alignment` | `4ebe8c3b9d8744a48283773741271d05af3fd9bd94e60285669dd3df3218be3f` |
| `insufficient_history` | 40 根日线 | `unresolved/data unavailable` | 不生成方向 | 无 receipt |

Replay 的纵向验证由 `tests/historical/test_data_jobs.py::test_daily_history_projects_a_replayable_strategy_receipt` 完成：历史分区 → Gateway adapter → Fact attributes 中的 JSON receipt → `ResearchStrategyReceipt` → `ResearchOutlook` 投影。离线验收标识为 `daily-strategy-session`，不是生产 Run。

## 实时源结果

使用当前环境直接请求 OKX 公开历史接口：

```text
uv run python -  # OKXHistoricalDataLoader, crypto.btc_usdt.ohlcv_1d, 70 days
httpx.ConnectError
```

失败发生在网络连接层，未产生数据行，系统不会用空值或模型文本补齐。因此本次不把 replay 结果冒充 live 结果。恢复网络后可直接复用同一 `DataJob → HistoricalDataResearchAdapter` 路径，无需改策略合同。

## 验证命令

```text
uv run pytest -q tests/kernel/test_daily_strategy.py tests/research/test_daily_outlook_projection.py tests/research/test_daily_strategy_replay.py tests/historical/test_daily_loader.py tests/historical/test_data_jobs.py tests/kernel/test_core_flow.py tests/research/test_market_snapshot_admission.py tests/capabilities/test_fact_pack.py tests/runtime/test_dsh_research_runtime.py --disable-warnings
104 passed, 1 skipped

uv run python -m tools.contract_codegen check
canonical schemas: ok

uv run ruff check <本次核心 Python 文件与测试>
All checks passed!

cd apps/decision-desk && pnpm test -- ResearchPage.test.tsx
14 passed

cd apps/decision-desk && pnpm build
built successfully（仅有既有 chunk size warning）
```

## 结论和遗留项

D0 的工程闭环通过：输入、日线 Fact、可复算策略回执、观点投影、首屏展示、四类 replay 和交易权限边界均已验证。当前不能说“BTC 可以交易”或“策略胜率已证明”，因为实时行情在本机不可达，且 V0 还没有时间隔离的概率校准数据集。下一目标只能在补齐可达日线后进入历史校准；不能先增加更多 Agent、论坛来源或自动下单。
