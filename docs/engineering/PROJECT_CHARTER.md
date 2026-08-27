# Decision Hub 项目宪章

版本：`CHARTER-2026-08-26.v1`
状态：`accepted`（owner 已确认），与 [产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md) 一致。

## 产品目的

Decision Hub 持续把有时间边界的文本和事件证据，转换为可证伪、可回放、可量化评估、人工可控的决策支持结果。首期验证宏观事件对 BTC 与国际黄金的影响，后续用独立 Domain Pack 扩展到其他市场和非市场产品。

## 当前大阶段

**R0-CORE-COMPLETE（已完成）**

完整核心执行约束和验收证据见 [R0 Core Completion](../stages/R0_CORE_COMPLETION_PLAN.md)；Provider 子阶段记录见 [R0-B Stage Charter](../stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md)。本页只保留稳定的产品目的、阶段目标、不变量和非目标。

最近完成的总目标是 [R0 Core Completion](../stages/R0_CORE_COMPLETION_PLAN.md)：R0-B、R0-C、R0-D 已作为一个完整核心闭环通过验收。下一阶段必须另立 Stage Charter，先锁定 R1 实时事件来源的授权和契约边界。

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

- 不做实时新闻、日历、行情、ASR、直播监听或通知。
- 不接入 DSH/Pi 生产主链，不 clone DSH，不创建第二个业务账本。
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
