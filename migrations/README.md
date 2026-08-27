# Database Migrations

Alembic 是数据库结构唯一真源。应用启动执行 `upgrade head`；测试可以使用空库迁移或显式的 `Base.metadata.create_all` 测试 fixture。生产代码不能自动调用 `create_all`。当前 head 为 `0007_run_cost_nullable`；该迁移兼容旧库的 `runs.cost_usd NOT NULL`，使未知 Provider 成本可以保持为 `NULL`。
