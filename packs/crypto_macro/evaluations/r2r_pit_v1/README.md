# R2-R PIT Evaluation Dataset v1

本目录是 `R2-R-06B` 的 12-case 不可变 replay 数据集。`catalog.yaml` 是人工维护
的事件目录；`cases/*.json` 和 `manifest.json` 由生成器产生，不手改。

事件分布固定为：央行讲话 2、货币政策决议 3、通胀发布 3、就业发布 2、地缘冲击
2。每个 case 保存 Trigger Evidence、完整 Pack Evidence Requirements、预期 hard
requirements、事件/变化的归档证据、三时间戳、cutoff 和来源。当前 Outcome 为
`pending`，所以 `outcome_available_at=null` 且 labels 为空；不能用猜测结果填充 Brier、
方向准确率或收益。

生成与校验：

```bash
./.venv/bin/python -m tools.research_evaluation.build_pit_dataset
./.venv/bin/pytest tests/evals/test_research_dataset.py -q
```

`manifest.json` 使用现有 `EvaluationDatasetManifest`，逐 case 保存 SHA-256。Loader
还会独立检查 canonical schema、事件族、source ref、内容 hash、hard requirement
集合和 `published_at <= observed_at <= received_at <= cutoff_at`。只修 manifest hash
不能绕过 PIT 检查。

归档 replay 允许模型自由生成 query，但只按 case 内已有的 `requirement_id` 返回证据；
没有匹配项时 fail-closed，不回退 live network。该行为只属于 evaluation/replay，
不改变正式 live capability 的权限。
