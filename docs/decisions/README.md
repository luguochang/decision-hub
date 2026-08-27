# ADR 目录

每项不可逆、跨模块或影响契约兼容性的决定建立一个独立 ADR。ADR 是接受后的工程事实，不用聊天记录替代。

## 当前 ADR

- [ADR-0001 R0 实现基线](ADR-0001-r0-implementation-baseline.md)
- [ADR-0002 R0 核心观测、回放与成本语义](ADR-0002-r0-core-observability-replay.md)
- [ADR-0003 R1 实时来源与可插拔适配器边界](ADR-0003-r1-realtime-plugin-boundary.md)
- [ADR-0004 冻结历史 Alembic schema snapshot](ADR-0004-freeze-alembic-schema-snapshots.md)
- [ADR-0005 DSH Harness 与插件生态桥接边界（accepted）](ADR-0005-dsh-harness-plugin-bridge.md)
- [ADR-0006 Kernel 与 Orchestration 边界对齐（accepted）](ADR-0006-kernel-orchestration-boundary-alignment.md)

## 模板

```text
# ADR-NNNN 标题
日期：YYYY-MM-DD
状态：proposed | accepted | superseded
决策：
背景：
候选方案：
否决项：
后果：
迁移/回滚：
受影响契约：
受影响测试：
```
