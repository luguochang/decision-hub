# R0 实施状态

日期：2026-08-26（Asia/Shanghai）
状态：核心文本纵向链已可运行，继续补齐生产级恢复、评测和来源扩展。

## 已完成并自测

| 能力 | 当前实现 | 证据 |
|---|---|---|
| 文本入口 | `ObservationCreate -> TextEnvelope`，content hash 去重 | `tests/contracts`, `tests/e2e` |
| 账本 | SQLite WAL + Alembic `0001_initial`/`0002_outbox`，Event/Observation/Run/Snapshot/Artifact/Forecast/Outcome/Evaluation/Outbox；业务状态与 checkpoint 分离 | `tests/kernel`, API smoke, SQLite PRAGMA |
| PIT | `SnapshotService.freeze()` 保存 cutoff、证据 hash 和不可变 snapshot | `tests/kernel/test_core_flow.py` |
| Agent 编排 | LangGraph decision/research graph；研究层 policy/counter 并行；Fake/Replay 和 LangGraph-native `create_agent` seam | graph integration in core flow |
| Gate | facts/citations/counter-thesis/action fields/probability cap 的确定性检查 | `test_gate_fails_closed_without_evidence` |
| Outcome/Evaluation | 手工结果录入、费用/滑点扣除、Brier score 和 evaluation query | `tests/e2e/test_api_flow.py` |
| API | `/v1` REST、异步 202、Idempotency-Key、Query/View DTO、timeline、health | live curl smoke + API tests |
| 前端 | React/Vite/TanStack Query/Lucide；Inbox/Health/Forecast coverage/Assets/Run drawer；真实文本提交对话框；桌面/移动响应式 | `pnpm build`, Vitest, browser DOM/screenshot/submit smoke |
| 前端契约 | `@decision-hub/contracts-ts` workspace 包提供 Zod 运行时校验，Decision Desk 只 re-export | TypeScript build + client fixture test |
| 契约同步 | canonical schema hash manifest 检查；schema 改动会使 codegen check 失败 | `tools.contract_codegen generate/check` |
| ASR 位置 | `TranscriptSourcePlugin` 和 Meeting Copilot adapter，只接收 TextEnvelope | `test_transcript_adapter_is_text_only` |
| Graph checkpoint | `langgraph-checkpoint-sqlite` 独立 checkpoint store + RecoveryWatchdog 边界 | `tests/replay/test_checkpoint.py` |
| 发布 outbox | Artifact 与 `OutboxRecord` 同一事务提交；`hub-worker --once` 写本地 JSONL 并按 dedupe key 标记完成 | `test_outbox_worker.py` + fresh migration smoke |

## 尚未声称完成

- 外部 LLM live canary 已能通过 `gpt-5.5` Responses 路径完成三角色调用、结构化解析和 Artifact/Forecast 持久化；成本表、provider contract 和业务准确率仍未完成。默认 CI 继续使用 fake/replay 保持确定性。
- Email/IM 外部通知 adapter、scheduler/recovery watchdog 自动循环和真实 Provider 仍未接入；本地 outbox worker 已可运行且不触发新分析。
- Outbox/通知 adapter、官方日历/新闻源、实时市场 Provider、直播音频 capture。
- 完整六层评测、时间切分 replay/holdout/shadow 和 Asset Promotion。
- Playwright 浏览器交互和 375/768/1024/1440 视觉回归。
这些是后续 R0/R1 工作项，不改变文本核心和 ASR 适配器边界；在相应能力未通过测试前，不标记为生产完成。

## 本地验证命令

```bash
./.venv/bin/ruff check packages apps migrations tests tools
./.venv/bin/pytest -q
pnpm --dir apps/decision-desk build
pnpm --dir apps/decision-desk test
./.venv/bin/python -m tools.contract_codegen check
```

## TDD/SDD 与本次自测记录

长期规范见 [`docs/engineering/TDD_SDD_SELF_TEST_STANDARD.md`](engineering/TDD_SDD_SELF_TEST_STANDARD.md)。当前测试已覆盖从文本输入到 Evaluation 的可执行链：文本哈希、Event/Observation/Snapshot、LangGraph research、Gate、Artifact、30m/24h/72h Forecast、Outcome、Brier/net return、Query View、Timeline 和 Outbox。

2026-08-26 本地结果：

```text
Python pytest: 16 passed, 1 warning
Ruff: passed
Pyright: 0 errors, 0 warnings
Canonical schema check: passed
Module documentation check: passed
Frontend Vitest: 1 passed
Frontend Vite build: passed
```

外部模型 canary 使用 `tools/canary/run_live_text_canary.py`，只读当前进程环境变量，使用临时 SQLite，输出脱敏 ID/状态/hash，不把密钥写入仓库或数据目录。本次使用 `https://codexai.club/v1` 与 `gpt-5.5` 做了分层探测：

- `/v1/models` 返回模型列表，包含 `gpt-5.5`。
- 直接 `/v1/chat/completions` 的普通文本请求和 JSON Schema 请求均返回 `200`。
- 直接 `/v1/responses` 的普通文本请求和 `text.format` JSON Schema 请求均返回 `200`。
- 之前强制 Chat + 非严格 Pydantic schema 时，LangGraph 结构化 Agent 出现超时/502；根因是协议选择和 schema 形状不匹配。Runtime 现默认使用 Responses，并提供 `DECISION_HUB_LLM_API_MODE=chat` 显式回退。
- 修正后完整 LangGraph canary 成功完成三次角色调用、结构化解析、Artifact 和三个 Forecast；本次结果为 `status=degraded`、`gate_status=research_only`，原因是模型选择了 `no_trade`，不是 Provider 协议失败。

因此当前结论是“Provider 的 Chat 和 Responses 基础接口均可用，Decision Hub 三角色结构化 Agent 的 Responses 路径已通过兼容性验证，但业务 Gate 仍正确拒绝无方向候选”。这不代表预测准确率、盈利能力或生产稳定性已证明；准确性仍需时间切分回放和 holdout 评测，正常 CI 仍禁止触网。
