# ADR-0004：冻结历史 Alembic schema snapshot

日期：2026-08-27
状态：accepted

## 决策

`0001_initial` 改为自包含的 R0 schema snapshot，不再导入当前 ORM 的 `Base.metadata`。后续表和列只能在对应的前向 Alembic revision 中创建。`tests/migrations/test_upgrade_paths.py` 作为永久回归证据，覆盖 `0007 -> 0010` 和 `0009 -> 0010`。

## 背景

R1 新增 `SourceStateRecord` 后，`0001_initial` 若调用当前 `Base.metadata.create_all()`，升级到历史 `0007` 时就会提前创建 `source_states`。随后 `0008` 会重复建表而失败。历史迁移随当前 ORM 改变，违反 schema 单一来源、可回放和可恢复要求。

## 否决项

- 不在 `0008` 或后续迁移中吞掉“表已存在”错误来掩盖初始 migration 污染。
- 不让 `0001` 继续依赖当前应用模型，或把历史 schema 放入运行时 Core。
- 不用手工修改 `alembic_version`、删除用户数据库或 `create_all` 修复升级失败。

## 后果

新安装按准确的历史顺序创建 R0，再前向升级到 R1。已处于 `0007` 或 `0009` 的数据库可升级至 `0010_source_poll_schedule`；正式数据和业务账本不需要重建。今后 ORM 新增表/列必须同时有新的前向迁移和升级路径测试。

## 回滚

该修复不改变已应用 revision 的数据库内容。若需回退 R1，仅按 Alembic downgrade 或 adapter 配置停用，不能删除已入账的业务事实。

## 受影响范围

`migrations/versions/0001_initial.py`、`migrations/versions/0008` 至 `0010`、`tests/migrations/`、迁移运行手册和 R1 阶段验收。
