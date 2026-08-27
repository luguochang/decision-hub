# Decision Hub

Decision Hub 是一个可插拔、可审计的事件驱动决策智能平台。

它将文本、会议转写、官方信息、新闻和其他事件来源统一转换为结构化证据，再通过可替换的 Runtime、LangGraph 研究图、确定性 Gate、Forecast、Outcome 和 Evaluation，生成可回放、可解释、可评估、人工可控的决策支持结果。

首期聚焦宏观事件对 BTC 及其他市场资产的影响，但核心架构不绑定加密货币。后续可以增加 A 股、美股、供应链、行业研究或 PPT 等独立 Domain Pack，而不复制 Product Kernel，也不把某个 Agent Harness 作为业务账本。

## 当前状态

`R0 Owner Production Core` 已完成：文本输入到决策、预测、结果评估、观测、恢复、回放和备份自测的纵向链已能在本地运行；外部 GPT-5.5 Responses 兼容性 canary 已通过，业务 Gate 仍保持独立裁决。ASR、实时新闻/日历、实时行情、通知和长期样本评测尚未标记为生产完成。

## 文档入口

- [工程索引](INDEX.md)
- [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
- [执行路线图](docs/ROADMAP.md)
- [分阶段执行设计](docs/EXECUTION_PLAN.md)
- [实现状态](docs/IMPLEMENTATION_STATUS.md)
- [项目宪章](docs/engineering/PROJECT_CHARTER.md)
- [全局开发治理规范](docs/engineering/DEVELOPMENT_GOVERNANCE.md)
- [R0-B Stage Charter](docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)
- [R0 Core Completion 实现方案](docs/stages/R0_CORE_COMPLETION_PLAN.md)
- [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
- [ADR 目录](docs/decisions/README.md)
- [模块地图](docs/modules/README.md)
- [本地运行手册](docs/runbooks/local-development.md)

## 本地开发

Python 依赖使用项目中的 `.venv`，前端使用 pnpm：

```bash
./.venv/bin/pytest -q
./.venv/bin/ruff check packages apps migrations tests tools
./.venv/bin/pyright packages apps tests tools/canary
./.venv/bin/python -m tools.contract_codegen check
./.venv/bin/python tools/docs/check_module_docs.py
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
```

启动本地 API 和 Decision Desk：

```bash
./.venv/bin/uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000
```

默认路径使用 Fake/Replay Runtime，不触发外部模型。真实 Provider 只能通过 [live text canary](tools/canary/run_live_text_canary.py) 显式执行；密钥必须通过当前进程环境或本机密钥管理器注入，不能写入仓库、数据库或日志。

## 设计边界

- Core/领域契约不依赖 DSH、Pi 或具体 Provider。
- Agent 只能提出候选，代码 Gate 是唯一发布裁决者。
- LangGraph checkpoint 不替代 SQLite 业务账本。
- 前端只消费 Query/View DTO，不读取 SQL、原始 JSON 或 Harness session。
- ASR、网页、新闻和日历只作为来源适配器，统一产出 `TextEnvelope`。
