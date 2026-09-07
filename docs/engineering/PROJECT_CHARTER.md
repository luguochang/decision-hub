# Decision Hub 项目宪章

版本：`CHARTER-2026-08-26.v1`
状态：`accepted`（owner 已确认），与 [产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md) 一致。

## 产品目的

Decision Hub 持续把有时间边界的文本和事件证据，转换为可证伪、可回放、可量化评估、人工可控的决策支持结果。首期验证宏观事件对 BTC 与国际黄金的影响，后续用独立 Domain Pack 扩展到其他市场和非市场产品。

## 当前大阶段

**PD Product Fact Sufficiency and Active Delivery（implementation in progress）**

Owner 于 2026-09-04 接受 [产品事实充分度与主动交付修复方案](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md) 和 [PD Stage Charter](../stages/PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)，授权按 `PD-00..06` 完成事实语义、事件窗口、typed provider、主动交付、可观测成本和受控评测闭环，并为 `PD-07` 建立真实前瞻观察入口。不得把 14 天/20 个事件的时间窗伪造为本轮代码完成，也不得在 PD-00 正确性门前先放宽 Gate 或扩大产品承诺。

R0/R1/R2/R2-L 工程底座退出门已经完成。Owner 已接受 [R2-R Stage Charter](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) 与 [ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)，并授权 R2-R-00 至 R2-R-06；R2-R-00 至 R2-R-06E 已完成，Runtime 决策为 `retain_baseline / pending_owner_review`。Fixed 继续 active，DSH 保持 candidate/shadow。R2-R-07 的 G1-A/B/C/D 与 G2-A/B 已完成离线实现，G2-C live canary 与 G2-D 事实充分度验收仍需独立确认，不进入 R3。所有更长期工作以[产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)的 G1-G6 为准，不再按临时问题无限扩展。

2026-08-30 产品失败复盘后，产品方向按已接受的 [ADR-0012 DSH-first 产品重新收口与实时研究闭环](../decisions/ADR-0012-dsh-first-product-rebaseline.md) 和 [ADR-0013 DSH Web 原生插件集成](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md) 收口。DSH-NATIVE-CORE 的官方 Web/Host/Client/耐久桥接工程门已完成；当前唯一执行阶段是 [PRODUCT-CLOSEOUT-01](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md)，按 [产品执行总方案](../product/PRODUCT_EXECUTION_MASTER_PLAN.md) 收口 C1-C7 的单 owner Trader Pilot。真实 Search/Official/Market、G3 价值观察、active Promotion、ASR/PPT/第二领域仍需独立 Gate。Fixed 保持 active、DSH candidate/shadow、Replay 诊断态。工程 `done` 不等于 `product_ready`。

最近完成的总目标是 [R1 Realtime Event Engine](../stages/R1_REALTIME_EVENT_ENGINE.md)：来源文本、市场事实和已提交 outbox 已复用 R0 主链，并未创建第二个 Agent runtime、账本或 Gate。它证明离线工程闭环，不证明真实网络稳定性、预测准确率或盈利能力。

目标：在单机本地环境完成文本到决策、预测、评测、观测、恢复、回放和发布自测闭环；外部 LLM Runtime 可以在不绑定 Core、不自写协议栈的前提下，被配置、限时、重试、观测和安全降级；同一 `AgentRequest -> AgentResult` 契约支持 Responses 和 Chat 两种 OpenAI-compatible 模式。

核心交付物：

- Provider 能力配置和版本化 capability manifest
- 严格 Pydantic structured output
- LangChain/LangGraph 原生 timeout、bounded retry 和错误映射
- usage/cost metadata 投影
- 外部 gpt-5.5 Responses canary 与 Chat fallback canary
- 不触网的 mock transport、失败注入和契约测试
- `Run/Step/Attempt/Call` Inspector、checkpoint recovery、PIT replay/holdout、SQLite backup/restore 和 ReleaseManifest

## 成功标准

不是“模型回答更长”，而是：

1. 同一输入可以在 Fake、Replay 和外部 Runtime 上按相同契约运行。
2. Provider 超时、429、5xx、结构化输出失败和配置错误都能确定性降级，不发布错误决策。
3. Agent 只能提交候选，代码 Gate 仍是唯一发布裁决者。
4. 所有运行带 strategy/runtime/provider/model/schema 版本，成本未知时明确标记 unknown。
5. 普通 CI 不访问外部网络，不需要任何 API Key。

## 当前明确不做

- 不做音频捕获、ASR 推理、OCR、自动交易或直播自动执行；R1 只接收已经转写的文本。
- 不把未经授权的网页抓取、搜索摘要或模型总结作为唯一 canonical source。
- R2-R-06 通过前不把 DSH candidate 提升为正式 active runtime；不 clone DSH，不创建第二个业务账本。
- 不做自动交易、自动晋级、在线修改 Gate 或用户系统。
- 不因为 Provider 不兼容而重写 LangChain/LangGraph/OpenAI SDK；先在 Adapter 边界记录证据。
- 不新增没有调用方的基础设施、服务、目录或通用 `utils/`。

## 六条不可漂移的不变量

1. Core/领域契约不依赖任何 Harness 或 Provider。
2. PIT 的 `observed_at`、`published_at`、`received_at` 和 `cutoff_at` 不能被未来信息覆盖。
3. Agent 只产出候选，Deterministic Gate 唯一裁决发布级别。
4. Event、Snapshot、Run、Artifact、Forecast、Outcome 和 Evaluation 是业务事实；日志、DSH session、Graph state 不是。
5. 公开边界只使用 Pydantic/Zod/canonical schema，禁止裸 `dict/Any` 穿越模块。
6. 任何不可逆或跨模块改变先记录 ADR，任何行为改变必须有 BDD/TDD 证据。

## 任务决策句

如果一个改动不能回答“它如何帮助验证或交付上述产品价值”，或者只能靠新增自研基础设施实现，任务应暂停并回到架构/契约评审。
