# Decision Hub TDD / SDD 与自测规范

版本：`TDD-SDD-2026-08-26.v1`  
适用范围：R0 文本输入到决策、预测、结果评估的纵向链；未来来源、领域 Pack、Runtime 和前端模块也必须遵守本规范。

## 1. 目的与边界

Decision Hub 的验收对象不是某个模型的漂亮回答，而是可复现、可审计、可评估的输入到输出链：

```text
文本输入
  -> ObservationCreate
  -> TextEnvelope / content_hash 去重
  -> Event + Observation admission
  -> PIT EvidenceSnapshot
  -> LangGraph decision/research graph
  -> StrategyCandidate（agent 只能提出候选）
  -> Deterministic Gate（唯一发布裁决者）
  -> Artifact + 30m/24h/72h Forecast
  -> Outcome
  -> Evaluation（Brier / net return）
  -> Query/View / Decision Desk / Outbox
```

本规范不把 ASR、新闻日历、实时行情、邮件或 IM 通知伪装成已完成能力。它们只能通过公开 Port/Schema 接入，且必须新增相应契约测试和 ADR。

## 2. SDD：先规格后实现

SDD（Specification-Driven Development）是本项目的入口顺序。新功能必须按以下顺序完成：

1. 写清用户价值、输入、输出、失败边界和不做什么。
2. 修改 `contracts/schemas/` 中的 canonical schema、事件 schema 或 Gate policy；跨语言类型只能由 codegen 生成，禁止手写镜像。
3. 写 ADR（编号、日期、决策、理由、否决项、后果）到 `docs/decisions/`。跨模块、不可逆、数据留存和 Runtime 选择都需要 ADR。
4. 画清 Port 边界：Core/领域契约不依赖 DSH、Pi 或具体 Provider；前端只消费 Query/View DTO；Agent 无账本、发布、交易和扣费权限。
5. 先写失败测试（Red），再实现最小行为（Green），最后只做有测试保护的重构（Refactor）。
6. 更新受影响模块 `README.md`、`INDEX.md`（若模块边界变化）和 `docs/IMPLEMENTATION_STATUS.md`。

禁止“先把 Python 流程写出来，再补字段和异常”的开发方式。框架可以替换，领域契约、三时间戳 PIT 规则、Gate 不越权和幂等语义不能靠框架隐式决定。

## 3. TDD：Red -> Green -> Refactor

每个行为至少包含一个可读的验收断言：

- **Red**：测试先表达契约，测试失败原因必须是功能尚未实现，而不是环境或网络不稳定。
- **Green**：只实现让测试通过的最小路径；不得借机加入未调用的基础设施、通用 `utils/` 或隐藏状态。
- **Refactor**：保持契约和测试不变，消除重复；重构后必须重新跑本模块及全量检查。

测试命名使用 `test_<行为>_<条件>_<结果>`，测试数据使用固定文本、固定时间和固定 Provider 结果。随机 ID 只允许出现在实现层，断言使用 schema、状态、数量和关系，不断言随机值本身。

## 4. 测试层次与责任

| 层次 | 目录/命令 | 允许的依赖 | 必须验证 |
|---|---|---|---|
| 单元测试 | `tests/kernel`、纯函数测试 | 内存对象、固定时钟/fixture | Gate fail-closed、概率上限、费用滑点、Brier、哈希和状态转换 |
| 契约测试 | `tests/contracts`、`tools.contract_codegen check` | canonical YAML、Pydantic/Zod/jsonschema | schema 版本、必填字段、extra forbid、Python/TS 镜像一致 |
| 适配器测试 | `tests/runtime`、`tests/contracts` | mock agent/假 transport | 环境开关、结构化响应、错误映射、密钥不落盘；禁止真实网络 |
| 图/集成测试 | `tests/kernel`、`tests/replay` | SQLite 临时库、Fake/Replay Runtime、LangGraph | snapshot -> research -> Gate -> commit、并行 reviewer、checkpoint 恢复 |
| API E2E | `tests/e2e` | FastAPI TestClient、临时 SQLite、Fake Runtime | HTTP 输入、`202`、Idempotency-Key、timeline、View DTO、Outcome/Evaluation |
| 回放/评测 | `tests/replay` 及后续 holdout | 固定快照、版本化 Runtime | 同一输入可回放、时间切分、策略/Runtime 版本和评估结果可追溯 |
| Live canary | `tools/canary/run_live_text_canary.py` | 显式外部 Provider、临时库 | 真实兼容性、结构化输出、Gate 仍裁决；不进入普通 CI |
| 浏览器验收 | `apps/decision-desk` | 构建产物、API smoke | Inbox、Run Inspector、文本提交、错误状态、移动端无横向溢出 |

普通 CI 必须无网络、无外部 API Key、无真实交易/通知副作用：

```bash
./.venv/bin/pytest -m "not live" -q
```

`live` 是显式 opt-in 标记。当前真实模型 canary 以脚本为主，不能因为 canary 成功就把外部模型变成默认 CI 依赖。

## 5. 输入到输出的验收合同

R0 的最小 E2E 必须证明以下事实，而不只是“返回 200”：

1. 合法文本生成 `TextEnvelope`，`content_hash` 为 SHA-256；相同文本不会生成第二个业务事件或第二个 run。
2. Event、Observation 和 received/observed/published 三类时间戳被持久化；Snapshot 保存 cutoff、证据 ID 和 hash，后续不会读取未来信息。
3. LangGraph 执行 policy_delta、counter_thesis 和 decision_synthesis；前两者并行，候选结果通过 `AgentResult` Port 返回。
4. 候选缺 facts、citations、counter-thesis、trigger/invalidation 或概率超过上限时，Gate 必须拒绝或降级；Agent 不能绕过 Gate 发布。
5. 通过 Gate 的 Artifact 必须关联 Run/Event，且有 `30m`、`24h`、`72h` 三个 Forecast；每个 Forecast 有方向、概率、触发条件、失效条件和过期时间。
6. Outcome 录入后，Evaluation 的 `net_return_pct = return_pct - fees - slippage`，Brier 使用该 Forecast 概率和方向标签计算。
7. API 只返回 Query/View DTO；Timeline 至少包含 `run.started`、`snapshot.frozen`、`research.completed`、`decision.committed`；Outbox 与 Artifact 同事务提交并可去重。

当前对应的可执行测试是：

```bash
./.venv/bin/pytest tests/contracts tests/kernel tests/e2e tests/replay -q
```

## 6. Fixture、PIT 和失败注入规则

- Fixture 必须说明来源、语言、观察时间和预期 Gate 状态；不得把未经授权的真实敏感文本提交进仓库。
- 测试使用临时 SQLite；不能依赖开发机默认数据库，也不能删除用户已有数据库来“清理”。
- PIT 测试固定 `observed_at`、`published_at`、`received_at` 的关系；禁止用当前时间替代历史 cutoff。
- 每个跨边界适配器至少注入：超时、空结构化响应、schema 校验失败、重复请求、Provider 5xx/429 和未知字段。
- Gate 失败必须 fail-closed；外部模型失败只能产生 `failed/degraded/research_only`，不能自动发布或写交易账本。
- 恢复测试要关闭/重开 checkpoint 或模拟中断，证明业务账本和 LangGraph checkpoint 相互独立。

## 7. Live Provider 自测规则

外部模型只用于人工触发的兼容性 canary。密钥不写入 Markdown、源码、`.env`、SQLite、trace、JSONL、测试 fixture 或 shell 历史；优先通过本地密钥管理器向当前进程注入环境变量。

本次自测配置（endpoint 和模型可以记录，密钥只放进临时环境）：

```bash
export OPENAI_BASE_URL="https://codexai.club/v1"
export DECISION_HUB_MODEL="gpt-5.5"
export DECISION_HUB_LLM_ENABLED="1"
export DECISION_HUB_LLM_API_MODE="responses"
export OPENAI_API_KEY="<从本地密钥管理器临时注入，不要写入文件>"
```

也支持 `DEEPSEEK_API_KEY` 或 `SUB2API_API_KEY`；`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`SUB2API_API_KEY` 只在当前进程内读取，优先级与 Runtime 实现一致。不要把真实 key 粘贴进命令、文档或 issue。

`DECISION_HUB_LLM_API_MODE=responses` 是默认值，会调用 OpenAI 原生 `/v1/responses` 并使用 `text.format` 结构化输出；如果某个 OpenAI-compatible Provider 只提供 `/v1/chat/completions`，改为 `DECISION_HUB_LLM_API_MODE=chat`，对应 `response_format`。不要只根据模型名猜协议，先用 canary 验证。

运行 canary：

```bash
./.venv/bin/python -m tools.canary.run_live_text_canary
```

脚本使用合成事件和临时 SQLite，复用正式 `LangGraphAgentRuntime -> LangGraph -> Gate -> Commit` 路径；输出仅包含 run/event/artifact 的脱敏 ID、Gate 状态、Forecast horizon 和结果 hash。异常输出会做 key、Authorization 和响应头脱敏。若 endpoint 不支持 `/v1` 或 `gpt-5.5`，只记录脱敏的 provider compatibility error，不把错误宣称为业务效果。

Live canary 的通过条件是：Provider 返回可解析的 `AgentPayload`，三次角色调用完成，结果能通过同一 Gate/Artifact/Forecast 持久化；它不代表预测准确率、盈利或生产可用性已证明。准确性必须由时间切分回放和 holdout 评测证明。

## 8. Definition of Done

一个功能只有同时满足以下条件才算完成：

- 规格、契约、失败边界和不做什么已写清；必要时有 ADR。
- Red/Green/Refactor 测试覆盖正常、拒绝、重复和失败路径。
- 跨模块数据经过 Pydantic/Zod 或等价运行时校验；无裸 `dict/Any` 穿越公开边界。
- 通过 lint、类型检查、契约 codegen check、Python/前端测试和文档检查。
- 账本、checkpoint、outbox、trace/metrics 的归属没有混淆；没有把临时产物加入长期事实源。
- `docs/IMPLEMENTATION_STATUS.md` 准确区分“已验证”“仅 scaffold”“未完成”。
- 修改后的模块 README、`INDEX.md` 地图和相关 runbook 已同步。
- 代码、日志、数据库和文档中没有凭据；外部 canary 结果没有被伪装成离线测试结果。

## 9. 当前基线与剩余缺口

截至 2026-08-28，R0/R1/R1-L 离线代码门和 R2-00 至 R2-05 的离线 U2 工程退出门已通过：文本纵向链、来源/调度/outbox、PIT replay/holdout/离线 shadow、Run Inspector、Workbench/Core MCP、评测资产、候选 Runtime、Supervisor 和人工 Promotion/Rollback 均有可重复测试证据。外部 Provider 的一次兼容性 canary 不能证明生产稳定性；真实来源/行情/通知、实时 ASR、真实 DSH/Pi 插件、长期 production shadow、预测准确率和盈利能力仍未完成验证。任何实现或报告必须保持这一边界。
