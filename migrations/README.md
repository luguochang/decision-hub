# Database Migrations

Alembic 是数据库结构唯一真源。应用启动执行 `upgrade head`；测试可以使用空库迁移或显式的 `Base.metadata.create_all` 测试 fixture。生产代码不能自动调用 `create_all`。当前 head 为 `0015_evolution_provenance`；`0011` 增加 Core-owned ResearchMemo、Feedback 和 Capability intake，`0012` 增加不可变 Dataset/Experiment/Candidate、Promotion 决定和 CAS ActivePointer，`0013` 补齐 Promotion 幂等/评测族不变量，`0014` 增加实验 metadata 与 Experience 资产，`0015` 补齐 dataset/experiment/result 的 PIT provenance 与 candidate 归属。DSH session/插件状态仍不是业务账本。
