# Fixed Research Runtime

本模块只为 R2-R runtime comparison 包装现有 legacy fixed workflow。它原样执行
`policy_delta + counter_thesis + decision_synthesis + deterministic Gate`，再把已有
Artifact/Forecast 映射为 canonical `ResearchSessionResult`。

它不增加工具、不补证、不改 legacy API/账本，也不作为新 Harness。固定流程引用的
Trigger Evidence 不能因此升级为 verified Evidence；确定性 scorer 仍按 Pack
requirements 计算实际 coverage。该 adapter 让 Fixed 与 DSH 使用同一 case/report
契约，不把 Replay stub 冒充 baseline。

验证：

```bash
./.venv/bin/pytest tests/runtime/test_fixed_research_runtime.py -q
```
