# R0 实施状态

日期：2026-08-26（Asia/Shanghai）
状态：R0-A 文本核心纵向链已完成；`R0-CORE-COMPLETE` 已通过离线总体验收。R0-B/R0-C/R0-D 已完成；R1 实时来源、ASR、通知和第二领域仍未开始。

最后复核：2026-08-27。产品架构总表已与当前实际路径、ReleaseManifest 和 R0/R1/R2 范围重新对齐；本次仅为文档治理修正，无运行时行为变化。

## 治理状态

- 项目宪章：`accepted`，见 `docs/engineering/PROJECT_CHARTER.md`。
- 全局开发治理：`accepted`，见 `docs/engineering/DEVELOPMENT_GOVERNANCE.md`。
- 最近完成 Stage Charter：`R0-B Provider Reliability Boundary`，`accepted`，见 `docs/stages/R0-B_PROVIDER_RELIABILITY_BOUNDARY.md`；当前没有执行中的 Stage Charter。
- `R0-B1 ProviderConfig + capability manifest`：`done`；R0-B Provider Reliability Boundary 整体已完成。
- `R0-CORE-COMPLETE`：`done`；证据入口为 `tools/core_acceptance.py` 和 `docs/RELEASE_MANIFEST.json`。
- Run/Step/Attempt/Call normalized projection + Run Inspector API：`done`，新 Run 可查询四个 Step、三角色 Call、错误/成本/重试和 `/v1/runs/{run_id}/inspector`。
- SQLite backup/integrity 和固定 PIT Replay：`done`，已验证固定 clock、future-information reject、baseline/candidate、Outcome/Brier/net return、restore replay smoke。

## 已完成并自测

| 能力 | 当前实现 | 证据 |
|---|---|---|
| 文本入口 | `ObservationCreate -> TextEnvelope`，content hash 去重 | `tests/contracts`, `tests/e2e` |
| 账本 | SQLite WAL + Alembic `0001` 至 `0007`，Event/Observation/Run/Snapshot/Artifact/Forecast/Outcome/Evaluation/Outbox/Step/Call；业务状态与 checkpoint 分离 | `tests/kernel`, `tests/e2e`, fresh migration/SQLite PRAGMA |
| PIT | `SnapshotService.freeze()` 保存 cutoff、证据 hash 和不可变 snapshot | `tests/kernel/test_core_flow.py` |
| Agent 编排 | LangGraph decision/research graph；研究层 policy/counter 并行；Fake/Replay 和 LangGraph-native `create_agent` seam | graph integration in core flow |
| Gate | facts/citations/counter-thesis/action fields/probability cap 的确定性检查 | `test_gate_fails_closed_without_evidence` |
| Outcome/Evaluation | 手工结果录入、费用/滑点扣除、Brier score 和 evaluation query | `tests/e2e/test_api_flow.py` |
| API | `/v1` REST、异步 202、Idempotency-Key、Query/View DTO、timeline、health | live curl smoke + API tests |
| 前端 | React/Vite/TanStack Query/Lucide；Inbox/Health/Forecast coverage/Assets/Run drawer；真实文本提交对话框；桌面/移动响应式 | `pnpm build`, Vitest, browser DOM/screenshot/submit smoke |
| 前端契约 | `@decision-hub/contracts-ts` workspace 包提供 Zod 运行时校验，Decision Desk 只 re-export | TypeScript build + client fixture test |
| 契约同步 | canonical schema hash manifest 检查；schema 改动会使 codegen check 失败 | `tools.contract_codegen generate/check` |
| ASR 位置 | `TranscriptSourcePlugin` 和 Meeting Copilot adapter，只接收 TextEnvelope | `test_transcript_adapter_is_text_only` |
| Graph checkpoint | `langgraph-checkpoint-sqlite` 独立 checkpoint store + RecoveryWatchdog 恢复边界 | `tests/replay/test_checkpoint.py`, `tests/e2e/test_runtime_safety.py` |
| 发布 outbox | Artifact 与 `OutboxRecord` 同一事务提交；`hub-worker --once` 写本地 JSONL 并按 dedupe key 标记完成 | `test_outbox_worker.py` + fresh migration smoke |

## 尚未声称完成

- 外部 LLM live canary 已能通过 `gpt-5.5` Responses 路径完成三角色调用、结构化解析和 Artifact/Forecast 持久化；这只是 Provider 兼容性证据，不代表预测准确率或盈利能力。默认 CI 继续使用 fake/replay 保持确定性。
- Email/IM 外部通知 adapter、scheduler/recovery watchdog 自动循环和默认真实 Provider 运行仍未接入；本地 outbox worker 已可运行且不触发新分析。
- Outbox/通知 adapter、官方日历/新闻源、实时市场 Provider、直播音频 capture。
- 完整六层评测、长期样本量和 Asset Promotion。
- 完整 Playwright 375/768/1024/1440 视觉回归仍未建立；当前已完成浏览器 DOM、提交文本、桌面截图和 375px Inspector 无横向溢出 smoke。
这些是后续 R1/R2 工作项，不改变 R0 文本核心和 ASR 适配器边界；R0 的工程闭环已完成，但不能把有限 fixture 结果宣传为市场收益。

## 本地验证命令

```bash
./.venv/bin/python tools/core_acceptance.py
```

Inspector 证据归一化已由研究图汇合边界保证：Facts/Citations 取自 policy reviewer 的结构化输出，synthesis 上下文不会作为原始 JSON 写入 Artifact；`tests/e2e/test_api_flow.py` 对此有回归断言。

## TDD/SDD 与本次自测记录

长期规范见 [`docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md`](engineering/TDD_SDD_SELF_TEST_STANDARD.md)。当前测试已覆盖从文本输入到 Evaluation 的可执行链：文本哈希、Event/Observation/Snapshot、LangGraph research、Gate、Artifact、30m/24h/72h Forecast、Outcome、Brier/net return、Query View、Timeline、Step/Attempt/Call、Provider failure safety、checkpoint recovery、backup/restore 和 Outbox。

2026-08-26 本地结果：

```text
Python pytest: 44 passed, 1 warning
Ruff: passed
Pyright: 0 errors, 0 warnings
Canonical schema check: passed
Module documentation check: passed
Frontend Vitest: 1 passed
Frontend Vite build: passed
Core acceptance: passed（fresh Alembic `0007_run_cost_nullable`、PIT compare、backup/restore/integrity、secret scan）
```

外部模型 canary 使用 `tools/canary/run_live_text_canary.py`，只读当前进程环境变量，使用临时 SQLite，输出脱敏 ID/状态/hash，不把密钥写入仓库或数据目录。本次使用 `https://codexai.club/v1` 与 `gpt-5.5` 做了分层探测：

- `/v1/models` 返回模型列表，包含 `gpt-5.5`。
- 直接 `/v1/chat/completions` 的普通文本请求和 JSON Schema 请求均返回 `200`。
- 直接 `/v1/responses` 的普通文本请求和 `text.format` JSON Schema 请求均返回 `200`。
- 之前强制 Chat + 非严格 Pydantic schema 时，LangGraph 结构化 Agent 出现超时/502；根因是协议选择和 schema 形状不匹配。Runtime 现默认使用 Responses，并提供 `DECISION_HUB_LLM_API_MODE=chat` 显式回退。
- 修正后完整 LangGraph canary 成功完成三次角色调用、结构化解析、Artifact 和三个 Forecast；本次结果为 `status=degraded`、`gate_status=research_only`，原因是模型选择了 `no_trade`，不是 Provider 协议失败。

因此当前结论是“Provider 的 Chat 和 Responses 基础接口均可用，Decision Hub 三角色结构化 Agent 的 Responses 路径已通过兼容性验证，但业务 Gate 仍正确拒绝无方向候选”。这不代表预测准确率、盈利能力或生产稳定性已证明；准确性仍需时间切分回放和 holdout 评测，正常 CI 仍禁止触网。
