# Evaluation Runner

## 目的

`EvaluationRunner` 复用 `tools.replay.run_fixture`、正式 `AgentRuntime` 和 PIT Snapshot，执行
baseline/candidate 的公平 replay、holdout 或 prospective shadow 比较。每个候选使用独立
SQLite，原始 report 先写入工作目录，聚合后的 `ExperimentResultView` 才进入 Evolution 资产。

## 边界

- 不训练、调参、修改 Gate 或改变 active pointer。
- 不把 holdout 标签传给 Runtime；fixture outcome 只在正式运行完成后由 replay 工具写入。
- 不访问网络；真实 shadow 需要另一个明确授权的 SourceAdapter 和 Stage Gate。
- 不创建第二套 workflow、账本或评测 DTO。

## 验证

```bash
./.venv/bin/pytest tests/evals -q
```
