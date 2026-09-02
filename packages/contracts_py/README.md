# Python Contracts

本包提供 canonical schema 生成的 Pydantic 模型以及已有 R0/R1 稳定 DTO。
跨模块调用只能从 `packages.contracts_py.decision_hub_contracts` 公共入口导入，
不能 deep import `generated/`，也不能在 Kernel、Runtime、API 或测试中复制
同名模型。

## 所有权

- `contracts/schemas/*.schema.yaml` 是唯一真源。
- `decision_hub_contracts/generated/*.py` 由 codegen 生成，禁止手改。
- `models.py` 只负责稳定公共名和尚未迁入 canonical schema 的兼容 DTO。
- `__init__.py` 是跨模块稳定 import surface。

修改 schema 后必须运行：

```bash
./.venv/bin/python -m tools.contract_codegen generate
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/pyright
```

真实 Provider Experiment 使用 `provider_default` 随机性策略；只有 Runtime 明确
保证确定性或接受固定 seed 时才能写 `deterministic`/`seeded`。

`agentic_research.schema.yaml` 负责 Product Extension、Domain Pack、Role、
Capability、Evidence round、CausalCase、HorizonDecision、Trace 和前端 View
的跨语言语义。Harness 只能通过 adapter 实现这些契约，不能成为契约真源。
