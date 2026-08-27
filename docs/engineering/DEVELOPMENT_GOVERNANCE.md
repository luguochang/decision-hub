# Decision Hub 全局开发治理规范

版本：`GOVERNANCE-2026-08-26.v1`
状态：`accepted`（owner 已确认），作为所有后续阶段的强制约束。
适用范围：代码、契约、Agent/Workflow、前端、数据、测试、文档、评测和发布。

## 1. 规范组合：SDD + BDD + TDD + ADR

本项目不把一种方法当成万能流程，而是给每种规范固定唯一职责：

| 规范 | 解决的问题 | 产物 | 何时必须使用 |
|---|---|---|---|
| SDD（Specification-Driven Development） | 做什么、输入输出和边界是什么 | 规格、契约、事件、Gate 规则、阶段目标 | 所有新能力和跨模块变更 |
| BDD（Behavior-Driven Development） | 用户能观察到什么行为 | Given/When/Then 场景和验收测试 | API、前端、来源、通知和决策结果 |
| TDD（Test-Driven Development） | 如何以最小实现可靠完成 | Red -> Green -> Refactor 测试 | 所有代码行为、修复和重构 |
| ADR（Architecture Decision Record） | 为什么选这个方案、放弃什么 | 编号决策、后果、回滚 | 不可逆、跨模块、数据/协议/部署决定 |
| Changelog/Release | 改了什么、哪个版本可用 | `CHANGELOG.md`、ReleaseManifest | 用户可见行为、阶段完成和发布 |

执行顺序固定为：

```text
SDD 规格/契约
  -> ADR（需要时）
  -> BDD 场景
  -> TDD Red
  -> 最小实现 Green
  -> Refactor
  -> 集成/回放/Live canary（按风险）
  -> 文档、状态、变更记录和独立 commit
```

不能用一份“先写代码再补测试”的 Python 草稿代替 SDD；不能用“pytest 通过”代替 BDD 的用户可观察验收；不能用聊天中的解释代替 ADR。

## 2. 文档事实源与冲突优先级

每次任务开始只加载与目标有关的最小上下文。事实源按下列优先级解释：

1. Owner 已确认的 ADR 和 canonical schema（针对具体决策/契约时优先）。
2. [产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)（全局产品、分层和边界）。
3. [项目宪章](PROJECT_CHARTER.md)（目的、当前大阶段和不可漂移不变量）。
4. 本规范与 [分阶段执行设计](../EXECUTION_PLAN.md)（开发流程和任务拆分）。
5. 受影响模块的 `README.md`、实现、测试和 runbook。
6. `docs/IMPLEMENTATION_STATUS.md`、`docs/ROADMAP.md`、`CHANGELOG.md`（进度和历史证据）。
7. 研究文档、临时文件和聊天记录（只能提供线索，不能单独授权实现）。

如果发现冲突：停止代码修改，记录冲突位置和影响，新增/修订 ADR，等待 owner 确认。禁止用局部补丁让两个冲突事实同时存在。

## 3. 阶段治理：先定义大目标，再开小任务

每个大阶段必须有一页 Stage Charter，至少包含：

- 价值假设：给 owner 增加什么可测价值。
- 输入/输出：涉及的公开契约、事件和 Query/View。
- 复用清单：必须调用哪些 LangChain/LangGraph/Pydantic/SQLAlchemy 能力。
- 自研边界：本项目只补哪些产品资产。
- 非目标：明确本阶段不做什么。
- 风险和失败语义：超时、拒绝、降级、恢复、权限和数据边界。
- BDD 场景、TDD 测试矩阵和阶段退出门。
- 允许修改路径、禁止修改路径和文档同步清单。

大阶段未通过 owner 的 Stage Gate 前，不得开始其中的代码任务。通过后再按 Task ID 串行执行，每个任务一个可回滚 commit。

最近完成的 Stage Charter 是 [R0-B Stage Charter](../stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md) 中的 `R0-B Provider Reliability Boundary`；当前没有执行中的 Stage Charter。R1 开始前必须另立并经 owner 确认新的 Stage Charter；项目宪章只作为短上下文锚点，本文件负责全局治理。

## 4. BDD 规范：把价值写成可观察场景

BDD 场景使用业务语言，不描述内部函数名；每个场景必须能映射到一个自动化测试或明确的人工验收。

模板：

```text
Feature: <用户价值>
Scenario: <一个结果>
Given <固定前置数据、时间和版本>
When <用户/来源/调度器执行一个动作>
Then <可观察状态或输出>
And <安全、审计、幂等或降级约束>
```

R0-B 示例：

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

BDD 场景必须覆盖：正常路径、重复请求、拒绝/降级、超时/Provider 失败、权限边界和恢复路径；只有“模型输出看起来合理”不算场景通过。

## 5. TDD 规范：测试先于实现

### Red

- 先写能表达契约和失败边界的测试。
- 测试失败必须说明缺少的行为，不得因为网络、当前数据库或未清理的缓存失败。
- 固定时间、文本、Provider 响应和随机性；随机 ID 只检查格式和关系。

### Green

- 只写让当前测试通过的最小实现。
- 优先调用框架已有 API：`create_agent`、`StateGraph`、`RetryPolicy`、`ChatOpenAI`、Pydantic、SQLAlchemy/Alembic。
- 不顺手创建未来阶段的服务、抽象、第二套 DTO 或第二套状态机。

### Refactor

- 只重构已有测试保护的代码。
- 重构不能改变公开契约、状态语义、PIT、Gate 和权限边界。
- 重构后重新跑受影响测试和全量检查，并更新模块 README。

测试层次和命令以 [TDD/SDD 与自测规范](TDD_SDD_SELF_TEST_STANDARD.md) 为准；普通 CI 必须使用 `pytest -m "not live"`，禁止外部 API。

## 6. 复用框架的强制规则

在引入新代码前先查官方 API 和本地依赖版本：

- Provider/协议：`langchain-openai.ChatOpenAI` 和 OpenAI SDK。
- Agent loop/结构化输出：LangChain `create_agent(response_format=...)`。
- 图、子图、checkpoint、恢复、RetryPolicy：LangGraph。
- Runtime schema：Pydantic v2；前端 schema：Zod；跨语言源：`contracts/` codegen。
- 数据迁移：SQLAlchemy 2 + Alembic；观测：LangChain callbacks/OpenTelemetry/`structlog`。

本项目的薄层仅负责领域 schema、版本、权限、PIT、Gate、业务状态、评测和 Query/View。若框架能力不足，先写证据和 ADR；不得在 Provider、Graph node 或 API route 中复制一套协议、重试、trace 或事务机制。

## 7. 上下文长度与目的漂移控制

### 7.1 两层上下文

每次 Codex 任务只需要：

1. 短上下文：`INDEX.md`、`PROJECT_CHARTER.md`、当前 Stage Charter、任务卡。
2. 专项上下文：受影响模块 README、相关 canonical schema、直接依赖代码和测试。

完整总架构和历史研究只在需要解释冲突或做架构决策时读取，不默认复制到每次任务提示中。

### 7.2 Task Context Manifest

使用现有工具生成任务上下文：

```bash
./.venv/bin/python tools/context/build_task_context.py \
  --objective "<单一任务目标>" \
  --paths packages/runtime_adapters packages/orchestration tests/runtime
```

Manifest 必须记录：当前目标、产品价值、前置 ADR/契约、允许/禁止路径、相关测试、已知限制、停止条件和验证命令。`tmp/task-context.md` 是中间产物，不能成为长期事实源；任务完成后把结论提炼到模块 README、状态或 ADR。

### 7.3 何时压缩上下文

出现以下任一情况时必须先压缩，不继续向 Prompt 追加历史：

- 任务需要同时读取三个以上不相关模块。
- 对话出现多个互相矛盾的“当前方案”。
- 同一问题连续修补两次仍未通过。
- 任务目标无法用一句话和三个验收断言表达。

压缩格式固定为：

```text
事实：已经验证的代码、测试和外部结果
决策：已接受的架构/契约/ADR
当前目标：本任务唯一目标
不做：本任务明确排除项
证据：文件、测试命令、commit
未决：必须由 owner 决定的问题
```

压缩只删除重复叙述和未验证推理，不能删除不变量、失败边界、版本、授权、已知缺口或 owner 决策。压缩后先更新 `docs/IMPLEMENTATION_STATUS.md` 或 ADR，再继续开发。

### 7.4 防止目标漂移的停止句

Codex 在以下情况必须停止实现并报告，而不是自动扩展：

- 发现任务需要修改 `Forbidden paths`。
- 发现框架已提供的能力却想自写替代品。
- 发现契约需要增加跨领域 nullable 字段。
- 发现测试只能靠真实网络、真实密钥或事后信息通过。
- 发现当前实现与已接受 ADR/V1 不一致。
- 发现“修复”需要新增第二个账本、第二套 DTO、第二个状态机或第二条正式生产链。

## 8. 变更记录与文档同步矩阵

禁止静默变更。每类变化必须写入对应事实源：

| 变化 | 必须更新 | 证据 |
|---|---|---|
| 产品目标/阶段范围 | `PROJECT_CHARTER.md`、`ROADMAP.md`、必要时 ADR | owner 确认和 Stage Gate |
| 架构/协议/部署/数据不可逆决定 | `docs/decisions/ADR-NNNN.md` | accepted ADR |
| 跨模块 schema/事件/Gate | `contracts/`、codegen manifest、相关测试 | codegen/check + contract tests |
| 模块职责或公开入口 | 模块 `README.md`、`docs/modules/README.md` | module docs check |
| 用户可见行为/API/UI | `CHANGELOG.md`、Query/View、BDD/E2E 测试 | API/UI test |
| 运行状态或完成度 | `docs/IMPLEMENTATION_STATUS.md`、`ROADMAP.md` | 固定验证命令 |
| 临时探索和实验 | `tmp/` 或 `research/` | 提炼后删除/归档 |
| 每个可回滚小任务 | 一个独立 Git commit | commit message + tests |

文档更新和代码更新必须在同一个任务提交中完成；不能先改行为，之后靠聊天“以后补文档”。

## 9. Review 和 Definition of Done

任务完成前逐项确认：

```text
[ ] 产品价值、输入输出和非目标已写清
[ ] SDD 契约/事件/Gate 规则先于实现
[ ] 需要时有 ADR，且没有和已接受 ADR 冲突
[ ] BDD 正常/重复/拒绝/失败/恢复场景存在
[ ] TDD Red -> Green -> Refactor 完成
[ ] 优先复用 LangChain/LangGraph/Pydantic/SQLAlchemy 能力
[ ] 没有第二套协议、Agent loop、重试、trace、账本或 DTO
[ ] PIT、幂等、Gate、权限和 secret 规则保持不变
[ ] 受影响模块 README、状态、路线图、CHANGELOG 已同步
[ ] pytest、lint、type、contract、前端和文档检查通过
[ ] 任务只有一个目标，一个独立 commit，不自动 push
```

只有阶段所有 Task ID 都通过并且 owner 通过 Stage Gate，才能把阶段标记为 `done`，再开启下一个大阶段。

## 10. Owner 确认后的任务执行规则

本规范已被 owner 接受；`R0-B1 ProviderConfig 和 capability manifest` 以及 R0 核心总目标均已完成。下一张任务卡必须隶属于新的 R1/R2 Stage Charter，单独定义并通过范围确认，不得以“顺手修复”为理由绕过 Stage Charter。
