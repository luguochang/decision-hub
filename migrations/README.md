# Database Migrations

Alembic 是数据库结构唯一真源。应用启动执行 `upgrade head`；测试可以使用空库迁移或显式的 `Base.metadata.create_all` 测试 fixture。生产代码不能自动调用 `create_all`。
