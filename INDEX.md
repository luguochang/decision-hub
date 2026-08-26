# Decision Hub 工程入口

项目简介见 [README](README.md)，阶段执行状态见 [ROADMAP](docs/ROADMAP.md)。

## 当前状态

Decision Hub 正在实现 R0 Owner Production。当前首要目标是把已经存在的文本可靠地转换为可回放、可审计、可评估的市场决策支持结果。ASR、直播监听、新闻日历和通知只通过适配器接入，不改变文本之后的核心链路。

## 唯一有效事实源

1. [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
2. [执行路线图](docs/ROADMAP.md)
3. [ADR 目录](docs/decisions/README.md)
4. [模块地图](docs/modules/README.md)
5. [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
6. `contracts/schemas/` 中的 canonical schema
7. 受影响模块的 `README.md`、测试和 ReleaseManifest

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
