# 实施状态

日期：2026-08-28（Asia/Shanghai）
状态：`R0-CORE-COMPLETE`、`R1-REALTIME-EVENT-ENGINE`、`R1-L-SINGLE-OWNER-PILOT-READINESS` 离线代码门，以及 R2-00 至 R2-05 的离线 U2 工程退出门已完成。当前进入 R2 观察期，不自动开始 R3；Live Pilot Gate 仍未授权。

最后复核：2026-08-28。R2 的 canonical codegen、官方 MCP transport、Run Inspector、评测/经验资产、LangGraph Supervisor、候选 Runtime、Promotion/Rollback 和 Decision Desk 已通过离线退出门。这些是可重复的工程证据，不是生产稳定性、真实 shadow 优势、预测准确率或盈利能力证明。

## 治理状态

- 项目宪章：`accepted`，见 `docs/engineering/PROJECT_CHARTER.md`。
- 全局开发治理：`accepted`，见 `docs/engineering/DEVELOPMENT_GOVERNANCE.md`。
- 最近完成 Stage Charter：`R1 Realtime Event Engine`，`done`，见 `docs/stages/R1_REALTIME_EVENT_ENGINE.md`；可插拔边界见 `docs/decisions/ADR-0003-r1-realtime-plugin-boundary.md`。
- `R0-B1 ProviderConfig + capability manifest`：`done`；R0-B Provider Reliability Boundary 整体已完成。
- `R0-CORE-COMPLETE`：`done`；证据入口为 `tools/core_acceptance.py` 和 `docs/RELEASE_MANIFEST.json`。
- `R1-L-SINGLE-OWNER-PILOT-READINESS`：`done (offline)`；Stage Charter 见 `docs/stages/R1_L_SINGLE_OWNER_PILOT_READINESS.md`。readiness 契约、控制层、API 只读入口、worker 预检/启动门、通知组合根和离线 acceptance 均已通过；真实 Live Pilot Gate 仍未授权。
- Run/Step/Attempt/Call normalized projection + Run Inspector API：`done`，新 Run 可查询四个 Step、三角色 Call、错误/成本/重试和 `/v1/runs/{run_id}/inspector`。
- SQLite backup/integrity 和固定 PIT Replay：`done`，已验证固定 clock、future-information reject、baseline/candidate、Outcome/Brier/net return、restore replay smoke。

## R2 实施状态

- Stage Charter：[R2 Decision Workbench 与自主进化](stages/R2_DECISION_WORKBENCH_EVOLUTION.md)，状态 `done (offline U2) / observation`；当前停止扩张，不自动进入 R3。
- [ADR-0005 DSH Harness 与插件生态桥接边界](decisions/ADR-0005-dsh-harness-plugin-bridge.md) 和 [ADR-0006 Kernel/Orchestration 边界对齐](decisions/ADR-0006-kernel-orchestration-boundary-alignment.md) 已接受；尚未接入任意 DSH 社区插件。
- 已交付范围：Core MCP/DSH ResearchMemo、统一 Query/View、Evaluation Dataset/FailurePattern/Experience、baseline/candidate replay/holdout/离线 shadow、Pi/DSH candidate adapter、Version Registry、owner Promotion/Rollback 和资产目录。
- `R2-00 done`：新增 `DecisionWorkflowExecutor` Port 和 `LangGraphDecisionExecutor`/composition root；Kernel 不再直接导入 LangGraph/LangChain，PIT、Gate、账本、checkpoint/recovery 和 API 行为保持不变。
- `R2-01 done`：canonical YAML 通过真实 codegen 生成 Python/TypeScript/Zod；Core MCP 使用官方 SDK，stdio 与 streamable HTTP 客户端均通过真实握手、发现和结构化查询；Capability 默认拒绝并要求 owner audit、schema、timeout、权限和 executor。
- `R2-02 done`：Run Inspector 和 Decision Desk 展示 PIT 证据血缘、版本、Step/Call、Gate、实验、资产和 Promotion；375/768/1024/1440 视口均无横向溢出，默认不显示 raw provider/graph JSON。
- `R2-03 done`：不可变 Dataset manifest、fixture hash、PIT/未来标签泄漏拒绝、raw artifact 先保存后评分、FailurePattern 和 Experience 均可追溯。
- `R2-04 done`：Supervisor 复用 LangGraph `StateGraph`/`Send`/checkpointer，specialist 结构化失败、最多一次 replan 且只补缺失 capability；Pi/DSH candidate runtime 通过统一 contract suite，失败保持 fail-closed。
- `R2-05 done`：Promotion Gate、owner-only command、CAS active pointer、原子 Promotion/Rollback、并发单赢家、事务故障回滚和审计历史已通过测试与浏览器交互验收。

## R1 已交付与退出门

| 能力 | 当前实现 | 当前证据与边界 |
|---|---|---|
| 来源接入 | `SourceConnector`、`SourceManifest`、registry；RSS/Atom/JSON/iCalendar 和转写 fragment 统一产出 `TextEnvelope` | fixture 覆盖 cursor、duplicate、revision、429/解析失败与 PIT；真实来源仅 opt-in，不宣称稳定性 |
| Durable source state | Kernel 持有 `source_states` 的 cursor、health、backoff、`next_poll_at` | `0008`/`0010` 与 `tests/migrations/test_upgrade_paths.py` 覆盖历史升级；registry 不持有业务状态 |
| 市场与到期评估 | `MarketDataPort`/OKX public adapter、`DueOutcomeService` | bid/ask、VWAP fallback、unavailable/estimated 与 Outcome 幂等使用 fixture；不宣称实际执行质量 |
| 调度与恢复 | `RealtimeScheduler` 轮询来源并从 durable admitted Run 恢复 Graph 执行 | scheduler 不创建第二队列，不绕过 R0 LangGraph/Gate/账本 |
| 通知 | 已提交 outbox 的 local JSONL adapter 与有限重试 | 通知失败不重新分析；外部 Email/IM 仍未接入 |
| 产品可观测性 | `/v1/sources`、`/v1/health`、Decision Desk 来源健康摘要 | UI 只显示人可读状态，不展示原始 Provider/adapter JSON |

R1 退出门已完成：`R1-01` 至 `R1-07` 均有可回滚提交、固定 fixture 覆盖来源失败/游标/修订/PIT/到期/通知重试，且全量质量门通过。R0 `ReleaseManifest` 保持历史 R0 证据，不被 R1 工作树改写；fixture 成功不表述为真实数据或收益结论。

## 已完成并自测

| 能力 | 当前实现 | 证据 |
|---|---|---|
| 文本入口 | `ObservationCreate -> TextEnvelope`，content hash 去重 | `tests/contracts`, `tests/e2e` |
| 账本 | SQLite WAL + Alembic `0001` 至 `0015`，Event/Observation/Run/Snapshot/Artifact/Forecast/Outcome/Evaluation/Outbox/Step/Call 与 R2 Workbench/Evolution 资产；业务状态与 checkpoint 分离 | `tests/kernel`, `tests/e2e`, `tests/migrations`, `tests/workbench`, `tests/evolution`, fresh migration/SQLite PRAGMA |
| PIT | `SnapshotService.freeze()` 保存 cutoff、证据 hash 和不可变 snapshot | `tests/kernel/test_core_flow.py` |
| Agent 编排 | LangGraph decision/research graph；研究层 policy/counter 并行；Fake/Replay 和 LangGraph-native `create_agent` seam | graph integration in core flow |
| Gate | facts/citations/counter-thesis/action fields/probability cap 的确定性检查 | `test_gate_fails_closed_without_evidence` |
| Outcome/Evaluation | 手工结果录入、费用/滑点扣除、Brier score 和 evaluation query | `tests/e2e/test_api_flow.py` |
| API | `/v1` REST、异步 202、Idempotency-Key、Query/View DTO、timeline、health | live curl smoke + API tests |
| 前端 | React/Vite/TanStack Query/Lucide；Inbox/Health/Forecast coverage/Assets/Run drawer；真实文本提交对话框；桌面/移动响应式 | `pnpm build`, Vitest, browser DOM/screenshot/submit smoke |
| 前端契约 | `@decision-hub/contracts-ts` workspace 包提供 Zod 运行时校验，Decision Desk 只 re-export | TypeScript build + client fixture test |
| 契约同步 | canonical schema hash manifest 检查；schema 改动会使 codegen check 失败 | `tools.contract_codegen generate/check` |
| ASR 位置 | `TranscriptSourceAdapter` 和 Meeting Copilot fragment 边界，只接收/产生 `TextEnvelope` | `tests/sources/test_transcript_adapter.py` |
| Graph checkpoint | `langgraph-checkpoint-sqlite` 独立 checkpoint store + RecoveryWatchdog 恢复边界 | `tests/replay/test_checkpoint.py`, `tests/e2e/test_runtime_safety.py` |
| 发布 outbox | Artifact 与 `OutboxRecord` 同一事务提交；`hub-worker --once` 写本地 JSONL 并按 dedupe key 标记完成 | `test_outbox_worker.py` + fresh migration smoke |

## R1-L 进入 R2 前收口

| 能力 | 当前实现 | 证据与边界 |
|---|---|---|
| readiness | `packages/pilot_runtime` 聚合数据库、迁移、Provider、来源、市场、通知和自动交易禁用检查，输出 `pilot-readiness.v1` 脱敏报告 | `tests/pilot/test_readiness.py`；不触网、不调用 LLM/SMTP |
| worker 启动门 | `hub-worker --preflight` 输出报告并以非零退出表示未就绪；`--pilot` 在 scheduler 前 fail-closed | `apps/hub_worker/README.md`、`tools/pilot_acceptance.py`；`--once` fixture 兼容保留 |
| 通知组合根 | local JSONL 或显式 email SMTP adapter，均消费 committed outbox；复用现有 bounded retry/dedupe | `packages/pilot_runtime/bootstrap.py`、`tests/providers`；不重新分析、不改账本 |
| API readiness | 只读 `/v1/pilot/readiness`，仅返回 canonical Pydantic DTO，不返回密钥、密码、原始 provider JSON | `tests/pilot/test_entrypoints.py` |

以上条目是离线实现证据，不等于真实来源、真实 SMTP、长期运行、预测准确率或盈利验证；这些需要单独的 Live Pilot Gate。

## 尚未声称完成

- 外部 LLM live canary 已能通过 `gpt-5.5` Responses 路径完成三角色调用、结构化解析和 Artifact/Forecast 持久化；这只是 Provider 兼容性证据，不代表预测准确率或盈利能力。默认 CI 继续使用 fake/replay 保持确定性。
- 真实 Email/IM 通知、默认真实 Provider 运行、长期后台进程稳定性和真实来源的授权/限流协议仍未验收；本地 worker 与 local JSONL 只用于单机/fixture 证据。
- 直播音频 capture、ASR 推理、OCR、未授权新闻抓取、自动交易和第二领域仍未接入。
- 真实长期样本量、生产 shadow 优势和满足生产 Promotion 阈值的候选。
- 自动化 Playwright 视觉回归仍未建立；本轮已人工验证 375/768/1024/1440 四个视口及完整 Promote 交互。
这些是观察期或新 Stage Gate 的工作，不改变 R0 文本核心和 ASR 适配器边界；不能把有限 fixture 结果宣传为市场收益。

## 本地验证命令

```bash
./.venv/bin/python tools/core_acceptance.py
```

Inspector 证据归一化已由研究图汇合边界保证：Facts/Citations 取自 policy reviewer 的结构化输出，synthesis 上下文不会作为原始 JSON 写入 Artifact；`tests/e2e/test_api_flow.py` 对此有回归断言。

## TDD/SDD 与本次自测记录

长期规范见 [`docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md`](engineering/TDD_SDD_SELF_TEST_STANDARD.md)。当前测试已覆盖从文本输入到 Evaluation 的可执行链：文本哈希、Event/Observation/Snapshot、LangGraph research、Gate、Artifact、30m/24h/72h Forecast、Outcome、Brier/net return、Query View、Timeline、Step/Attempt/Call、Provider failure safety、checkpoint recovery、backup/restore 和 Outbox。

2026-08-28 当前离线质量门结果：

```text
Python pytest (not live): 128 passed
Ruff: passed
Pyright: 0 errors, 0 warnings
Canonical schema check: passed
Module documentation check: passed
Frontend Vitest: 5 passed
Frontend Vite build: passed
Migrations: 3 passed；fresh Alembic 到 `0015_evolution_provenance`
Core MCP: 官方 stdio 与 streamable HTTP client 验收通过
Browser: 375/768/1024/1440 无横向溢出；人工 Promote/审计历史通过
Core acceptance: passed（含 durability/replay、backup/restore/integrity、secret scan、前端 Vitest/Vite）
Pilot acceptance: passed（readiness、worker preflight fail-closed、notification composition、入口 API、PIT/recovery/backup 复用检查）
```

外部模型 canary 使用 `tools/canary/run_live_text_canary.py`，只读当前进程环境变量，使用临时 SQLite，输出脱敏 ID/状态/hash，不把密钥写入仓库或数据目录。本次使用 `https://codexai.club/v1` 与 `gpt-5.5` 做了分层探测：

- `/v1/models` 返回模型列表，包含 `gpt-5.5`。
- 直接 `/v1/chat/completions` 的普通文本请求和 JSON Schema 请求均返回 `200`。
- 直接 `/v1/responses` 的普通文本请求和 `text.format` JSON Schema 请求均返回 `200`。
- 之前强制 Chat + 非严格 Pydantic schema 时，LangGraph 结构化 Agent 出现超时/502；根因是协议选择和 schema 形状不匹配。Runtime 现默认使用 Responses，并提供 `DECISION_HUB_LLM_API_MODE=chat` 显式回退。
- 修正后完整 LangGraph canary 成功完成三次角色调用、结构化解析、Artifact 和三个 Forecast；本次结果为 `status=degraded`、`gate_status=research_only`，原因是模型选择了 `no_trade`，不是 Provider 协议失败。

因此当前结论是“Provider 的 Chat 和 Responses 基础接口均可用，Decision Hub 三角色结构化 Agent 的 Responses 路径已通过兼容性验证，但业务 Gate 仍正确拒绝无方向候选”。这不代表预测准确率、盈利能力或生产稳定性已证明；准确性仍需时间切分回放和 holdout 评测，正常 CI 仍禁止触网。
