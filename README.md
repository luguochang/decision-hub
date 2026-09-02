# Decision Hub

Decision Hub 是一个可插拔、可审计的事件驱动决策智能平台。

它将文本、会议转写、官方信息、新闻和其他事件来源统一转换为结构化证据，再通过可替换的 Runtime、LangGraph 研究图、确定性 Gate、Forecast、Outcome 和 Evaluation，生成可回放、可解释、可评估、人工可控的决策支持结果。

首期聚焦宏观事件对 BTC 及其他市场资产的影响，但核心架构不绑定加密货币。后续可以增加 A 股、美股、供应链、行业研究或 PPT 等独立 Domain Pack，而不复制 Product Kernel，也不把某个 Agent Harness 作为业务账本。

## 当前状态

`R0 Owner Production Core` 已完成（提交 `2ee2f8d`）：文本输入到决策、预测、结果评估、观测、恢复、回放和备份自测的纵向链已能在本地运行；外部 GPT-5.5 Responses 兼容性 canary 已通过，业务 Gate 仍保持独立裁决。

`R1 Realtime Event Engine` 已完成离线验收并提交：来源、市场和通知以可替换 adapter 接入既有 Kernel，固定 fixture 覆盖来源到 Outcome/outbox 的完整链路、游标/PIT、失败恢复和升级路径。真实来源、行情、邮件和 ASR 的网络稳定性、授权范围、预测准确率及盈利能力均未声称完成；`R2` 必须获得新的 owner Stage Gate。

进入 R2 前的 `R1-L Single-Owner Pilot Readiness` 离线代码门也已完成：readiness、worker 预检/启动门、local/email outbox 组合根和只读 readiness API 已实现并通过 `tools/pilot_acceptance.py`。真实连续运行仍需 owner 单独授权的 Live Pilot Gate。

`R2 Decision Workbench v1` 已完成 R2-00 至 R2-05 的离线 U2 工程退出门：Core MCP、Workbench/Capability 边界、Run Inspector、评测资产、Evolution Supervisor、候选 Runtime seam、人工 Promotion/Rollback 和可观测前端已形成工程闭环。当前进入观察期，不自动扩大到 R3；真实 DSH/Pi/Provider、长期 shadow 和收益优势仍需独立授权与证据。

2026-08-30 R2-R-06E 已完成：12-case Fixed vs DSH、Warsh 真实事件 durable Run 和 Runtime 决策包均已保留。结论为 `retain_baseline / pending_owner_review`：Fixed 继续 active，DSH candidate/shadow 已能主动尝试补证并在工具失败时 fail-closed，但尚未达到 Promotion 门；不宣称盈利或长期实时稳定性。

2026-08-31 `DSH-NATIVE-CORE` 工程验收完成：固定官方 DSH Web、Host/Client plugin、durable Run/Session bridge、官方三场景 replay、Web 重启、版本 fail-closed、locked rollback 和桌面/移动浏览器证据均已通过。当前仍保持 Fixed active、DSH candidate/shadow、Replay 诊断态；真实网络价值、预测准确率和盈利未证明，详见 [阶段实施方案](docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)。

2026-09-01 `PRODUCT-CLOSEOUT-01 / E2-L` 已通过：官方 DSH Web 的 `Crypto Macro Trader`
工作区从真实页面建立受管 Session，DSH 在同一 Session 内完成两轮主动补证，Official、
Market、Search 结果经 Research Gateway、PIT、Sufficiency 和代码 Gate 写入 Hub 账本；
DSH 报告与 Decision Desk 对同一 Run 的状态一致，后台还自动创建了定时复查 child Run。
当前状态为 `pilot_ready=true / pilot_usable=research_only`，Fixed 仍 active，DSH 仍是
candidate/shadow；Search timeout、FRED stale 和未知成本被如实保留，不代表预测准确、盈利、
自动交易或 Promotion。下一阶段仅做 E3 前瞻价值观察。详见
[E2-L 真实产品验收记录](docs/evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)。

## 文档入口

- [工程索引](INDEX.md)
- [当前状态短上下文](docs/context/CURRENT_STATE.md) 与 [当前有效决策索引](docs/context/CURRENT_DECISIONS.md)
- [产品交付控制书与最终验收包](docs/product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)（当前唯一执行/收口入口）
- [E2-L 官方 DSH Web 真实产品验收记录](docs/evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)
- [2026-09-02 DSH Live Attestation 修复与真实运行记录](docs/evaluations/DSH_LIVE_RESEARCH_ATTESTATION_FIX_2026-09-02.md)
- [产品架构基线](DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)
- [最终产品交付实施书](docs/product/FINAL_PRODUCT_DELIVERY_EXECUTION_2026-09-01.md)（已执行的顶层交付合同）
- [产品执行总方案](docs/product/PRODUCT_EXECUTION_MASTER_PLAN.md)（架构与执行总参考）
- [产品实现与最终验收方案](docs/product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)（C1-C7 详细任务、代码结构和最终验收 checklist）
- [通用产品平台基线](docs/platform/PLATFORM_BASELINE.md) 与 [资产/扩展模型](docs/platform/ASSET_AND_EXTENSION_MODEL.md)（accepted）
- [Crypto Macro Domain Pack 设计](docs/domains/crypto_macro/README.md)（accepted，R2-R candidate path 已完成，Fixed active）
- [执行路线图](docs/ROADMAP.md)
- [分阶段执行设计](docs/EXECUTION_PLAN.md)
- [实现状态](docs/IMPLEMENTATION_STATUS.md)
- [项目宪章](docs/engineering/PROJECT_CHARTER.md)
- [全局开发治理规范](docs/engineering/DEVELOPMENT_GOVERNANCE.md)
- [R0-B Stage Charter](docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)
- [R0 Core Completion 实现方案](docs/stages/R0_CORE_COMPLETION_PLAN.md)
- [R1 Realtime Event Engine Stage Charter](docs/stages/R1_REALTIME_EVENT_ENGINE.md)
- [R2 Decision Workbench 与自主进化 Stage Charter（离线 U2 工程退出门已完成，观察期）](docs/stages/R2_DECISION_WORKBENCH_EVOLUTION.md)
- [R2-R Agentic Research Runtime Stage Charter（completed / retain baseline）](docs/stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)
- [R2-R-06E Runtime 决策包（retain baseline / owner review pending）](docs/evaluations/R2-R-06E_RUNTIME_DECISION.md)
- [DSH Native Web Product Core Stage Charter（engineering acceptance complete / product value pending）](docs/stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)
- [DSH-NATIVE-CORE 完成实施方案与验收记录](docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)
- [产品收口与后续总计划（G1-G6 有界路线）](docs/product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)
- [TDD/SDD 与自测规范](docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md)
- [ADR 目录](docs/decisions/README.md)，包括 [R1 adapter 边界](docs/decisions/ADR-0003-r1-realtime-plugin-boundary.md)
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

### 运行真实 DSH 产品入口

```bash
./infra/dsh/run-product.sh
```

打开启动器输出的完整 `DSH_URL`，进入 `Crypto Macro Trader` 后点击 `新建会话`。在新会话
输入研究目标并点击插件提供的 `建立研究任务`，等待 DSH Agent Loop 完成，再查看 `对话`、
`轨迹`、`研究报告`。旧会话里的普通聊天是续聊，不会新建 Hub Run；报告中的
`research_only/no_trade` 是证据 Gate 的安全结果，不是静态演示或方向性交易结论。

启动本地 API 和 Decision Desk：

```bash
./.venv/bin/uvicorn apps.hub_api.main:app --host 127.0.0.1 --port 8000
```

默认路径使用 Fake/Replay Runtime，不触发外部模型。Decision Hub API 的真实 Provider 只能通过 [live text canary](tools/canary/run_live_text_canary.py) 显式执行，密钥通过当前进程环境或本机密钥管理器注入。DSH 原生 live Web 的个人本机入口可使用 Git ignored 的 `data/dsh-live/.env` 持久化凭据（权限 `600`）；它不进入仓库、数据库、日志或最终报告，生产部署应使用 Secret Manager。

## 设计边界

- Core/领域契约不依赖 DSH、Pi 或具体 Provider。
- Agent 只能提出候选，代码 Gate 是唯一发布裁决者。
- LangGraph checkpoint 不替代 SQLite 业务账本。
- 前端只消费 Query/View DTO，不读取 SQL、原始 JSON 或 Harness session。
- ASR、网页、新闻和日历只作为来源适配器，统一产出 `TextEnvelope`。
