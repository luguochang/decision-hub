# Decision Hub 工程入口

项目简介见 [README](README.md)，阶段执行状态见 [ROADMAP](docs/ROADMAP.md)，逐任务实施约束见 [EXECUTION_PLAN](docs/EXECUTION_PLAN.md)。

## 当前状态

Decision Hub 已完成 R0 Owner Production Core（提交 `2ee2f8d`）和 [R1 Realtime Event Engine](docs/stages/R1_REALTIME_EVENT_ENGINE.md) 的离线验收，当前正在完成 [R1-L 单 owner 试运行就绪](docs/stages/R1_L_SINGLE_OWNER_PILOT_READINESS.md)。R1 在不改变文本之后核心链路的前提下，以 adapter 接入官方来源、人工转写文本、市场基准、到期评估和通知；R1-L 只补启动预检、组合根和脱敏 API。fixture 证据不等于真实网络稳定性、预测准确率或收益证明；R2 需要新的 owner Stage Gate。

## 唯一有效事实源

1. [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
2. [执行路线图](docs/ROADMAP.md)
3. [分阶段执行设计](docs/EXECUTION_PLAN.md)
4. [ADR 目录](docs/decisions/README.md)
5. [模块地图](docs/modules/README.md)
6. [项目宪章](docs/engineering/PROJECT_CHARTER.md)
7. [全局开发治理规范](docs/engineering/DEVELOPMENT_GOVERNANCE.md)
8. [当前 R1 Stage Charter](docs/stages/R1_REALTIME_EVENT_ENGINE.md)
9. [R1 adapter 边界 ADR](docs/decisions/ADR-0003-r1-realtime-plugin-boundary.md)
10. [R0 Core Completion 实现方案](docs/stages/R0_CORE_COMPLETION_PLAN.md) 和 [R0-B 历史阶段记录](docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)
11. [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
12. `contracts/schemas/` 中的 canonical schema
13. 受影响模块的 `README.md`、测试和 ReleaseManifest

`DECISION_HUB_FINAL_ARCHITECTURE.md` 和 `DSH_RESEARCH_DECISION_LOG.md` 是历史研究/审查材料；若与 V1 基线冲突，以 V1、ADR、canonical schema 和受影响模块文档为准。

聊天记录、临时草稿和历史研究不能单独授权代码变更。架构与实现冲突时必须停下并补 ADR。

## 首批纵向链

```text
TextEnvelope
  -> Observation/Event admission
  -> EvidenceSnapshot(PIT)
  -> LangGraph decision/research graph
  -> StrategyCandidate
  -> deterministic Gate
  -> Artifact + Forecast + Outcome/Evaluation
  -> Query View -> Decision Desk
```

## 开发入口

```text
uv run pytest
uv run python -m tools.contract_codegen check
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
```

每项任务开始前先生成 `Task Context Manifest`：

```bash
python tools/context/build_task_context.py --objective "..." --paths packages/kernel apps/hub_api
```

每项任务结束前运行：

```bash
python tools/docs/check_module_docs.py
```

## 禁止事项

- 不 clone DSH，不把 DSH session 当业务账本。
- 不在前端手写同名 DTO，不直接读取 SQL、LangGraph state 或原始 JSON。
- 不在 Graph node、API route、Provider adapter 中重复实现 Gate、状态机、重试或事务提交。
- 不新增无调用方的基础设施或 `common/`、`utils/`、`helpers/` 垃圾目录。
- Agent 只能提交候选，代码 Gate 才能发布。
