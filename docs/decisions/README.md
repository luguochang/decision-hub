# ADR 目录

- [ADR-0019：耐久工具预算与 Session Prompt 就绪边界](ADR-0019-durable-tool-budget-and-session-readiness.md)：`accepted`，把原始工具预算持久化到 Session link，以数据库 reservation 在 adapter 前原子限额，并要求 Hub accepted/link 先于业务 Prompt。
- [ADR-0020：DSH Research Tool 受信运行身份](ADR-0020-trusted-dsh-tool-session-context.md)：`accepted`，模型不再填写 Session ID；DSH Native Tool 只从 `exec.agent.id` 注入身份，原始 MCP tool 不作为 decision-research Agent 的旁路。

每项不可逆、跨模块或影响契约兼容性的决定建立一个独立 ADR。ADR 是接受后的工程事实，不用聊天记录替代。

## 当前 ADR

- [ADR-0001 R0 实现基线](ADR-0001-r0-implementation-baseline.md)
- [ADR-0002 R0 核心观测、回放与成本语义](ADR-0002-r0-core-observability-replay.md)
- [ADR-0003 R1 实时来源与可插拔适配器边界](ADR-0003-r1-realtime-plugin-boundary.md)
- [ADR-0004 冻结历史 Alembic schema snapshot](ADR-0004-freeze-alembic-schema-snapshots.md)
- [ADR-0005 DSH Harness 与插件生态桥接边界（accepted）](ADR-0005-dsh-harness-plugin-bridge.md)
- [ADR-0006 Kernel 与 Orchestration 边界对齐（accepted）](ADR-0006-kernel-orchestration-boundary-alignment.md)
- [ADR-0007 Live Observation 运行时、耐久化演进任务与人工晋级边界（accepted）](ADR-0007-live-observation-runtime.md)
- [ADR-0008 Agentic Research Runtime 与双层循环边界（accepted）](ADR-0008-agentic-research-runtime.md)
- [ADR-0009 通用产品平台、领域扩展与角色/能力插件边界（accepted）](ADR-0009-product-platform-extension-boundary.md)
- [ADR-0010 模型研究语义与可信运行账本边界（accepted）](ADR-0010-model-semantics-runtime-ledger-boundary.md)
- [ADR-0011 研究失败语义与事实覆盖边界（accepted）](ADR-0011-research-reliability-fact-boundary.md)
- [ADR-0012 DSH-first 产品重新收口与实时研究闭环（accepted）](ADR-0012-dsh-first-product-rebaseline.md)
- [ADR-0013 DSH Web 原生插件与上游升级集成策略（accepted）](ADR-0013-dsh-web-native-plugin-upstream-integration.md)
- [ADR-0014 DSH Session 多轮补证与消息幂等边界（accepted）](ADR-0014-dsh-session-multi-turn-continuation.md)
- [ADR-0015 事件准入、首次同步成本保护与 Run 优先级（accepted）](ADR-0015-event-admission-cost-and-priority.md)
- [ADR-0016 能力调用即时入账与 DSH 模型步截止（accepted）](ADR-0016-durable-capability-progress-and-model-step-deadline.md)
- [ADR-0017 Search Evidence 引用归因与独立来源边界（accepted）](ADR-0017-search-evidence-citation-attribution.md)
- [ADR-0018 DSH Client 受管 Session 选择边界（accepted）](ADR-0018-dsh-client-session-selection.md)
- [ADR-0019 耐久工具预算与 Session Prompt 就绪边界（accepted）](ADR-0019-durable-tool-budget-and-session-readiness.md)
- [ADR-0020 DSH Research Tool 受信运行身份（accepted）](ADR-0020-trusted-dsh-tool-session-context.md)

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
