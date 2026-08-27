# Decision Hub 工程入口

项目简介见 [README](README.md)，阶段执行状态见 [ROADMAP](docs/ROADMAP.md)，逐任务实施约束见 [EXECUTION_PLAN](docs/EXECUTION_PLAN.md)。

## 当前状态

Decision Hub 已完成 R0 Owner Production Core：文本已经可以可靠地转换为可回放、可审计、可评估的市场决策支持结果。当前没有正在执行的 R0 代码任务；ASR、直播监听、新闻日历和通知必须作为后续阶段的适配器接入，不改变文本之后的核心链路。

## 唯一有效事实源

1. [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
2. [执行路线图](docs/ROADMAP.md)
3. [分阶段执行设计](docs/EXECUTION_PLAN.md)
4. [ADR 目录](docs/decisions/README.md)
5. [模块地图](docs/modules/README.md)
6. [项目宪章](docs/engineering/PROJECT_CHARTER.md)
7. [全局开发治理规范](docs/engineering/DEVELOPMENT_GOVERNANCE.md)
8. [R0-B Stage Charter](docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)
9. [R0 Core Completion 实现方案](docs/stages/R0_CORE_COMPLETION_PLAN.md)
10. [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
11. `contracts/schemas/` 中的 canonical schema
12. 受影响模块的 `README.md`、测试和 ReleaseManifest

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
