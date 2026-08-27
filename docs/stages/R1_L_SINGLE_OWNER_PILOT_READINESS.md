# R1-L 单 Owner 试运行就绪 Stage Charter

版本：`STAGE-R1-L-2026-08-27.v1`
状态：`offline_complete`（离线代码门已通过；owner 尚未授权 Live Pilot Gate）
前置：`R0-CORE-COMPLETE`、`R1-REALTIME-EVENT-ENGINE`

## 1. 阶段目的

R1-L 是进入 R2 前的运行就绪阶段。它不增加新的研究方法，也不接入 DSH/Pi；只把已经通过离线验收的 R0/R1 链路收口成一个**单 owner、单机、人工决策辅助**的受控试运行入口。

本阶段要回答一个可验证问题：

> 在不自动交易、不重复分析、不丢失业务事实的前提下，owner 能否在本机明确检查配置、启动 worker、接收已 Gate 的结果、恢复中断、完成备份，并留下可审计的运行证据？

正式链仍然只有一条：

```text
授权来源/人工文本
  -> R1 SourceConnector / TextEnvelope
  -> Kernel admission + PIT
  -> R0 LangGraph research/decision
  -> deterministic Gate
  -> Artifact / Forecast / Outbox
  -> Outcome / Evaluation
```

## 2. 范围与非目标

### 2.1 本阶段交付

- 一个启动前 `pilot readiness` 预检：数据库迁移、数据目录、Provider 配置、来源开关、市场开关、通知配置和自动交易禁用状态均有明确结果。
- 一个显式的 `pilot` 运行模式：未通过预检时不启动长期 worker；`--once` 仍可用于离线/fixture 调试。
- 通知 channel 的组合根配置：默认 local JSONL；显式配置后可使用现有 SMTP adapter；通知仍只消费 committed outbox。
- 一个只读 `/v1/pilot/readiness` API 和 CLI JSON/人类可读输出，前端或未来 DSH 只能消费结果，不读取环境变量、SQL 或原始 provider 响应。
- 单 owner 运维证据：启动、停止、备份、恢复、故障降级、成本上限和日志脱敏的 runbook。
- BDD/TDD 测试：预检失败关闭、危险配置拒绝、配置完整通过、worker 不重复分析、通知失败不改账本、离线回归不触网。

### 2.2 明确不做

- 不进入 R2：不实现 DSH MCP、ResearchMemo、Pi shadow、实验 registry、Evolution、Promotion 或 Rollback pointer。
- 不实现音频采集、ASR 推理、OCR、网页搜索或新的数据授权。
- 不实现自动交易、交易所私钥、私有行情接口或模型修改 Gate/权限。
- 不把一次 readiness 通过宣称为真实来源稳定、预测准确、盈利或生产 SLA。
- 不引入 Redis、Kafka、Temporal、DBOS、Kubernetes、微服务或第二个队列/账本/运行时。

## 3. 架构边界与复用

| 能力 | 复用 | 本阶段薄层 |
|---|---|---|
| 配置校验 | Pydantic Settings、现有 `ProviderConfig` | 独立 `packages/pilot_runtime` 聚合检查并脱敏输出 |
| 数据库/迁移 | SQLAlchemy、Alembic、现有 `Database.initialize()` | 检查 head、目录和备份路径，不复制 schema |
| Agent/图 | 现有 LangGraph + LangChain `create_agent` | 只在 pilot 模式启用既有 Runtime，不新增 graph |
| 来源/行情 | R1 SourceRegistry、OKX public adapter | 检查显式开关和 manifest，不在预检中伪造网络成功 |
| 通知 | 现有事务 outbox、Local/SMTP adapter、bounded retry | 组合根按 channel 注册 adapter；不在通知层重新分析 |
| 可观测性 | 既有 Run/Step/Attempt/Call、Health API | readiness 只读摘要，不暴露 secret/raw JSON |

`packages/pilot_runtime` 是 composition/application 控制层，可以依赖 Kernel Port 和 adapter 配置；Kernel 不能反向依赖它。Core、canonical contract、PIT、Gate、账本和权限不依赖本阶段的 CLI、API、DSH 或具体 Provider。

## 4. 配置与安全不变量

试运行必须显式设置：

```text
DECISION_HUB_PILOT_MODE=1
DECISION_HUB_LLM_ENABLED=1
DECISION_HUB_SOURCES_ENABLED=1
DECISION_HUB_MARKET_ENABLED=1
DECISION_HUB_NOTIFICATION_CHANNEL=local | email
```

以下任一条件使 readiness `fail`，worker 不得以 pilot 模式启动：

- Provider key 缺失、API mode 未声明、结构化输出不支持、timeout/retry/budget 非法。
- 数据库无法连接、Alembic head 不是 `0010_source_poll_schedule`、数据目录不可写。
- 没有启用且具备 authority/domain 的来源 manifest，或市场能力未显式启用。
- email channel 缺 SMTP host/sender/recipient，或配置含明文密码而未通过 SecretStr 环境注入。
- `DECISION_HUB_AUTO_TRADE` 为真，或检测到已知交易私钥环境变量。

预检不主动调用外部来源、行情、LLM 或 SMTP；真实连通性只能由单独 opt-in canary 和 Live Pilot Gate 证明。

## 5. Task Cards

每张任务卡先写失败测试，再写最小实现，并单独提交。R1-L 任务顺序如下：

| Task ID | 交付 | 主要文件 | 退出证据 |
|---|---|---|---|
| `R1-L-01` | readiness 控制层与脱敏报告 | `packages/pilot_runtime/readiness.py`、canonical DTO、tests | 完整/缺配置/危险配置三类预检测试 |
| `R1-L-02` | CLI `hub-worker --preflight` 与 pilot 启动门 | `apps/hub_worker/main.py`、README | fail-closed；非 pilot 行为兼容；输出无 secret |
| `R1-L-03` | notification channel 组合根 | `apps/hub_worker/main.py`、SMTP adapter | local 默认、email 配置校验、outbox 失败隔离 |
| `R1-L-04` | API readiness 与运维 runbook | `apps/hub_api/main.py`、runbook、模块 README | 只读 API DTO、迁移/备份/恢复步骤可执行 |
| `R1-L-05` | 离线 pilot acceptance 与文档收口 | `tests/e2e`、状态/路线图/CHANGELOG | 全量质量门、真实 Live Pilot Gate 清单、阶段审计 |

当前实现状态：`R1-L-01` 至 `R1-L-05` 的离线代码、测试和文档证据已完成；本次变更形成独立提交但尚未推送，也未授权真实 Live Pilot Gate。只有 owner 明确授权来源、Provider 和通知通道后，才能进行真实连续运行验收。

## 6. BDD 验收场景

```text
Feature: 单 owner 启动安全
Scenario: 缺 Provider key 时 pilot fail-closed
Given pilot mode、来源和市场都已打开但 Provider key 缺失
When owner 执行 hub-worker --preflight
Then 返回 fail，列出 configuration_invalid
And 不启动 scheduler、不轮询来源、不写 Event/Run/Outbox
```

```text
Feature: 危险边界
Scenario: 自动交易开关或私钥出现
Given DECISION_HUB_AUTO_TRADE=1 或已知交易私钥环境变量存在
When readiness 检查运行
Then 状态为 fail，错误为 auto_trade_forbidden
And 任何 Agent、worker 或通知 adapter 都不获得执行权限
```

```text
Feature: 可恢复试运行
Scenario: 进程在 cursor commit 后退出
Given source admission 已提交但 Graph 尚未完成
When pilot worker 重启并执行 tick
Then scheduler 只恢复同一个 admitted Run
And 不创建第二个 Event、Artifact 或通知
```

```text
Feature: 通知解耦
Scenario: SMTP 暂时失败
Given Artifact 与 Outbox 已在同一事务提交
When SMTP adapter 返回 retryable error
Then outbox attempts/backoff 更新
And Artifact、Forecast、Gate 和 Run 不改变，Agent 不重新执行
```

```text
Feature: 离线回归
Scenario: 普通 CI 执行 pilot acceptance
Given 没有外部 key、网络和交易凭据
When 运行 pytest -m "not live"
Then 使用 Fake/Replay/fixture 完成核心链
And 不发起外部 HTTP、SMTP、交易或 LLM 请求
```

## 7. TDD/故障矩阵

- `PilotReadinessService`：全部通过、每个必需项缺失、未知环境值、SecretStr 不泄漏。
- Database：临时目录、只读目录、迁移 head 过旧、SQLite integrity 失败。
- Provider：LLM disabled、缺 key、非法 mode、预算不完整、结构化输出能力 false。
- Source/Market：未显式启用、manifest authority/domain 缺失、adapter 仍可替换；预检不触网。
- Notification：local 默认、SMTP 字段缺失、可重试/永久失败、dedupe 和 bounded retry。
- Worker：`--preflight`、pilot fail-closed、`--once` 兼容、重启恢复、SIGINT 可停止。
- API：readiness 只读、失败细节脱敏、不能通过 API 改环境或启用自动交易。
- 回归：R0/R1 全量 pytest、ruff、pyright、codegen、module docs、前端测试/build、migration、PIT/backup/restore。

## 8. 阶段退出门

R1-L 代码阶段只有同时满足以下条件才能标记 `done`：

1. `R1-L-01` 至 `R1-L-05` 均有独立提交、测试和模块文档。
2. pilot readiness 对缺 key、过期迁移、来源/市场关闭、危险交易变量均 fail-closed；完整配置只产生脱敏 `ready` 报告。
3. pilot worker 启动前运行 readiness；非 pilot 的离线/fixture `--once` 仍保持兼容。
4. 重启恢复、outbox retry/dedupe、PIT、Gate、业务账本和 checkpoint 语义未改变。
5. `./.venv/bin/python tools/core_acceptance.py` 与 `git diff --check` 通过。
6. 文档准确区分：离线就绪证据、Provider/source/SMTP canary、真实业务效果和盈利验证。

代码阶段完成后仍需单独的 `Live Pilot Gate`：由 owner 明确授权至少一个有来源授权的真实 source、一个真实 Provider、一个真实通知通道，完成连续运行和人工决策样本记录。Live Pilot Gate 未通过前，不得把系统称为生产运行或可靠交易系统。

## 9. 与 R2 的关系

R1-L 完成只表示可以安全积累真实人工运行样本，不代表 R2 已授权或自动进入 R2。R2 仍必须另立 Stage Charter，锁定 ResearchMemo、实验/holdout/shadow、Version Registry、Promotion/Rollback 和 DSH/Pi adapter 契约，并再次取得 owner Stage Gate。

R1-L 不得修改 R0/R1 的正式 active pointer，也不得把 DSH/Pi 会话、Prompt、原始模型 JSON 或外部来源状态写入业务账本。

## 10. Owner 决策边界

本 Charter 的实现范围由本次目标授权。若需要新增 canonical 领域对象、修改 Gate/PIT/权限、引入新的基础设施、访问未授权数据或扩大到自动交易/R2，必须停止当前任务，新增 ADR 和 Stage Charter，等待 owner 确认。
