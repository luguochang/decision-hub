# Evaluation Runner

## 目的

`EvaluationRunner` 复用 `tools.replay.run_fixture`、正式 `AgentRuntime` 和 PIT Snapshot，执行
baseline/candidate 的公平 replay、holdout 或 prospective shadow 比较。每个候选使用独立
SQLite，原始 report 先写入工作目录，聚合后的 `ExperimentResultView` 才进入 Evolution 资产。

`research_dataset.py` 加载 R2-R 的 canonical `ResearchEvaluationCase`，复用现有
`EvaluationDatasetManifest`、fixture hash 和 PIT 规则。它不生成标签、不运行模型，
只负责在评测前拒绝篡改、future leakage、来源/内容 hash 错误和事件族漂移。
`research_assets.py` 将完整 Research Runtime 对照投影到已有
Dataset/Candidate/Experiment/Result 账本；Outcome 未到期时 Brier 和方向准确率保持
`null`，真实 Provider 使用 `provider_default`，不会伪装成确定性实验。
`research_runtime.py` 为每个 case 启停独立归档 Research MCP，并通过显式 URL
注入 DSH 子进程；case 之间不共享 fixture，也不回退 live network。

## 边界

- 不训练、调参、修改 Gate 或改变 active pointer。
- 不把 holdout 标签传给 Runtime；fixture outcome 只在正式运行完成后由 replay 工具写入。
- 不访问网络；真实 shadow 需要另一个明确授权的 SourceAdapter 和 Stage Gate。
- 不创建第二套 workflow、账本或评测 DTO。

## 验证

```bash
./.venv/bin/pytest tests/evals -q
./.venv/bin/python -m tools.research_evaluation.build_pit_dataset
```
