# Provider Adapters

## 目的

把第三方 HTTP、交易所和通知 SDK 适配为 Kernel 的 `MarketDataPort`、`MarketWindowPort` 和 `NotificationPort`。adapter 只返回 canonical Pydantic DTO，不拥有 Gate、账本或发布权限。

## 当前实现

- `market/okx.py`：OKX 公共 ticker 和 1m candle 适配；bid/ask 优先，只有 last 时显式 `estimated`。
- `notifications/local.py`：本地 JSONL 通知 adapter，便于单机验证；Email/IM 继续作为可替换 adapter。

## 失败与测试

HTTP 429、5xx、超时、空响应和字段缺失必须映射为可观测失败或 `unavailable`，不能伪造高置信 PnL。普通测试使用注入的 fake fetcher，不触网；真实 endpoint 只能在显式 live canary 中使用。
