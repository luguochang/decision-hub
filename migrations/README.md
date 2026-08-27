# Database Migrations

Alembic 是数据库结构唯一真源。应用启动执行 `upgrade head`；测试可以使用空库迁移或显式的 `Base.metadata.create_all` 测试 fixture。生产代码不能自动调用 `create_all`。当前 head 为 `0010_source_poll_schedule`；`0008` 增加 SourceConnector 的 cursor/健康状态，`0009` 增加通知的下次重试、终态失败和有限错误码，`0010` 前向增加来源的 `next_poll_at` 调度时间。来源原文仍由 Observation/Event 保存，通知状态不成为第二业务账本。
