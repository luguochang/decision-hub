# Pilot Runtime

## 目的

R1-L 单 owner 试运行控制层。聚合现有数据库、Provider、来源、市场和通知配置，生成脱敏 `PilotReadinessReport`，并供 worker/API composition root 使用。

## 边界

本模块可以依赖 Kernel 的公开 Database/Port 和 adapter 配置；Kernel 不依赖本模块。它不执行研究、不写 Gate、不创建第二账本、不持有外部 session，也不调用 DSH/Pi。readiness 只检查本地配置与数据库状态，不访问网络。API 和 worker 通过 `build_readiness_service` 共享同一套检查，通过 `build_notification_adapters` 选择 local 或 email outbox adapter。

## 安全

试运行必须明确禁用自动交易且不能携带交易所私钥。API/CLI 报告只包含固定错误码、版本和 capability 摘要，不包含 API key、SMTP password、Authorization header 或原始 Provider 响应。真实 Provider/source/market/notification 连通性由 opt-in canary 单独证明。

## 测试

`tests/pilot` 覆盖完整配置、缺配置、危险交易变量、邮件配置、迁移与脱敏；`tools/pilot_acceptance.py` 还验证 worker fail-closed、outbox/recovery、PIT replay 和 backup/restore，并由 `tools/core_acceptance.py` 复核全量离线质量门。
