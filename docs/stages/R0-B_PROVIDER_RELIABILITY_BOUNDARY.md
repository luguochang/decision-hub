# R0-B Provider Reliability Boundary Stage Charter

版本：`STAGE-R0-B-2026-08-26.v1`
状态：`done`（2026-08-26 已通过 Provider failure、retry、cost budget、Responses/Chat 和离线 core acceptance 证据）；`R0-B1` 至 `R0-B6` 已纳入 R0 总目标完成。
适用范围：外部 LLM Provider、Runtime adapter、结构化输出、失败降级、usage/cost metadata 和 canary。

## 1. 阶段价值假设

R0-A 已经证明文本可以进入核心链路并产出可审计的 Artifact、Forecast、Outcome 和 Evaluation。R0-B 的价值不是提升预测收益，也不是引入更多来源，而是把外部模型调用变成可替换、可限时、可降级、可观测、可测试的工程边界。

本阶段完成后，Decision Hub 应能回答：同一份 `AgentRequest` 在 Fake、Replay、Responses、Chat runtime 下是否遵守同一契约；Provider 超时、429、5xx、结构化输出失败和配置错误时系统是否 fail-closed；每次运行的 provider/model/schema/runtime 版本、usage 和成本是否可追溯。

## 2. 输入和输出

输入：

- 已冻结的 PIT Snapshot、TextEnvelope、evidence refs 和 strategy version。
- `AgentRequest` 契约和角色任务。
- 启动时读取的 Provider 配置、API mode、timeout、retry、budget 和 capability。
- 外部模型返回的严格结构化 `AgentPayload` 或框架异常。

输出：

- `AgentResult`，保持 Core 和 Gate 可消费的稳定契约。
- 运行级错误码：`provider_timeout`、`provider_rate_limited`、`provider_unavailable`、`structured_output_invalid`、`configuration_invalid`。
- provider/model/api_mode/schema/runtime/capability 版本信息。
- usage/cost metadata；缺失时显式 `unknown`，不能伪造为 0。
- live canary 摘要，只用于兼容性证据，不写入业务账本。

## 3. 必须复用的框架能力

- OpenAI-compatible 调用：`langchain-openai.ChatOpenAI` 和 OpenAI SDK transport。
- Agent loop 和结构化输出：LangChain `create_agent(response_format=...)`。
- 编排、node retry 和 checkpoint 边界：LangGraph `StateGraph`、`RetryPolicy` 和现有 checkpoint 机制。
- 运行时 schema：Pydantic v2；跨语言契约继续走 `contracts/` codegen。
- timeout：Provider client timeout、LangGraph node/run 生命周期和标准 `asyncio.timeout`。
- 观测：LangChain callbacks、metadata、`structlog` 或 OpenTelemetry；本阶段只投影必要字段。

## 4. 本项目只实现的薄层

- ProviderConfig 和版本化 capability manifest。
- Responses/Chat 模式选择的 adapter 配置，不让 Core、Graph、Gate 或前端感知协议细节。
- 有限错误分类和产品状态映射。
- Run deadline、retry budget 和 cost budget 的配置传递。
- usage/cost 的归一化 metadata 投影。
- mock transport、失败注入 fixture 和 live canary 记录规范。

## 5. 非目标

- 不做实时新闻、日历、行情、ASR、直播监听或通知。
- 不 clone DSH，不把 DSH session 当业务账本，不接入 Pi 作为生产主链。
- 不自写 HTTP client、Agent loop、JSON parser、retry framework、tracing SDK 或第二套 DTO。
- 不把一次 live canary 成功宣称为业务准确率、盈利能力或生产稳定性。
- 不把 API key 写入仓库、文档、数据库、测试日志或前端配置。
- 不新增 Redis、Temporal、DBOS、Kafka、Postgres 或微服务拆分。

## 6. BDD 验收场景

```text
Feature: Provider capability 可校验
Scenario: 非法 api_mode 在 adapter 边界失败
Given 一份冻结的 PIT Snapshot 和 ProviderConfig
When api_mode 不是 responses 或 chat
Then Runtime 不启动外部调用并返回 configuration_invalid
And Core、Graph、Gate 和账本不需要修改
```

```text
Feature: 外部 Provider 可控降级
Scenario: Responses Provider 超时不发布候选
Given 一个冻结的 PIT Snapshot 和有界 Run deadline
When Provider 在 deadline 内没有返回结构化 AgentPayload
Then Run 进入 failed/degraded 状态并记录 provider_timeout
And 不创建 publish Artifact，不写交易权限对象，Outbox 不发送决策通知
```

```text
Feature: 协议可替换
Scenario: 同一 AgentRequest 使用 Chat fallback
Given 相同文本、evidence、schema 和 strategy version
When Runtime 仅把 api_mode 从 responses 切换为 chat
Then 返回同一 AgentResult 契约
And Core、Graph、Gate 和账本代码无需修改
```

```text
Feature: 成本元数据可信
Scenario: Provider 不返回 usage 时成本未知
Given Provider 返回合法 AgentPayload 但没有 usage metadata
When Runtime 归一化 AgentResult
Then usage 和 estimated_cost 标记为 unknown
And 系统不能把未知成本记录为 0
```

## 7. TDD 测试矩阵

- 配置测试：合法/非法 provider_id、base_url、model、api_mode、timeout、max_retries、budget、capability。
- 契约测试：同一 `AgentRequest` 在 fake/replay/responses/chat adapter 下返回同一 `AgentResult` 形状。
- 结构化输出测试：缺 required 字段、未知字段、概率越界、非法 action 均失败并映射固定错误。
- 失败注入测试：timeout、429、5xx、解析失败、未知异常、缺 key。
- 重试测试：可重试错误不超过配置次数，总 deadline 不被单 node retry 突破。
- 成本测试：真实 usage、fake usage、usage 缺失三类路径。
- canary 测试：普通 CI 不触网；live canary 只在显式环境变量存在时运行。

## 8. 允许修改路径

- `packages/runtime_adapters/`
- `packages/orchestration/` 中 Runtime adapter 边界和 graph 调用点
- `contracts/` 和生成产物，仅当 `AgentResult` 或 provider metadata 契约需要变化
- `tests/runtime/`、`tests/orchestration/`、`tests/contracts/`
- `tools/canary/`
- 受影响模块 `README.md`、`docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`、`CHANGELOG.md`
- 必要时新增 ADR 到 `docs/decisions/`

## 9. 禁止修改路径和职责

- 不修改 Core 账本语义来适配某个 Provider。
- 不修改 Gate 规则来放行结构化输出失败或 no_trade。
- 不让前端、API route 或数据库保存 secret。
- 不在前端复制 DTO 或直读 LangGraph state。
- 不把 canary 结果写入业务事实表。
- 不为了 R0-B 修改 R1 来源、ASR、通知、Pi/DSH 或多领域模块。

## 10. R0-B1 任务卡

状态：`done`。ProviderConfig、capability manifest、Responses/Chat 配置读取、结构化输出能力校验、timeout/retry/error taxonomy、usage/cost 投影和预算 fail-closed 均已实现并通过测试。

Task ID：`R0-B1`
Title：ProviderConfig 和 capability manifest
Objective：让外部 Provider 的模型、协议、超时、重试、预算和结构化输出能力在启动时可校验、可版本化、可被 Runtime adapter 读取。
Preconditions：先阅读 `INDEX.md`、`DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md`、`docs/ROADMAP.md`、`docs/EXECUTION_PLAN.md`、本 Stage Charter、相关 runtime/orchestration README 和现有 Runtime tests。
Allowed paths：`packages/runtime_adapters/`、必要的 `contracts/`、`tests/runtime/`、`tools/canary/`、文档状态文件。
Forbidden paths：Core 账本、Gate 规则、R1 来源、ASR、通知、DSH/Pi 集成、前端 secret 配置。
Reuse：Pydantic v2、LangChain `ChatOpenAI` 配置、现有 `AgentRuntime` port。
Contract：`provider_id`、`base_url`、`model`、`api_mode`、`timeout_seconds`、`max_retries`、`token_budget`、`cost_budget`、`supports_structured_output`、`capability_version`。
Test first：先写配置校验、非法 api_mode、缺 key、unknown capability、responses/chat 双模式读取的 Red tests。
Implementation：只实现配置对象、manifest 读取和 adapter 边界接入；不改业务链路。
Acceptance：合法配置可启动 runtime；非法配置在外部调用前失败；Fake/Replay 不受影响；普通 CI 不触网。
Verification：`pytest` 相关 runtime/orchestration tests、`python -m tools.contract_codegen check`、`python tools/docs/check_module_docs.py`、ruff/pyright。
Docs：更新受影响模块 README、`docs/IMPLEMENTATION_STATUS.md`、`CHANGELOG.md`，如改变不可逆协议则补 ADR。
Stop conditions：发现需要自写 SDK、修改 Core/Gate、泄露 secret、或必须依赖真实网络才能通过测试时停止并报告。

## 11. 阶段退出门

R0-B 的历史退出门如下；本次已全部满足并标记为 `done`：

- Responses 和 Chat 两种模式在同一 `AgentResult` 契约下通过 fake/mock 测试。
- timeout、429、5xx、structured output invalid、configuration invalid 均有确定性失败语义和测试。
- retry 次数、run deadline 和 budget 不会互相绕过。
- usage/cost metadata 可查询，unknown 语义清楚。
- live canary 可独立运行、脱敏记录结果，普通 CI 不触网。
- Core、PIT、Gate、账本、Outbox 和前端 DTO 边界未被破坏。
- 模块 README、`docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md` 和 `CHANGELOG.md` 已同步。

## 12. Owner 确认边界

Owner 已确认本 Stage Charter；本阶段已完成。后续 R1/R2 能力仍必须通过独立任务卡和测试门，不得把实时来源、ASR、通知或 DSH/Pi 集成倒灌进 R0。
