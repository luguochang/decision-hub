# Forecast Research Loop 执行记录

日期：2026-09-19  
目标：完成 `FORECAST_RESEARCH_LOOP_AND_MACRO_CALENDAR_PLAN_2026-09-19.md` 的 M1–M5 核心闭环，并用可重复测试证明它能从预测写入走到复查、事件更新、结果标签和校准准入。

## 交付裁决

M1–M5 已实现并通过离线回归。产品现在能保存每条预测的前视输入、安排 durable recheck、接收官方宏观日历的发布/修订状态、记录到期结果，并拒绝未经样本外 receipt 的 `calibrated` 声明。

这不等于当前 BTC 策略已经达到 60% 胜率。仓库中没有为真实策略伪造通过的校准报告；没有通过 receipt 时，方向只能作为研究方向，概率保持 `unavailable/uncalibrated`，交易授权仍由 Gate 阻断。

## 已完成的核心改造

### M1：预测复盘 ledger

- Research 页面显示每个 horizon 的 forecast id、方向、预测时间、as-of、到期时间、标签状态、方向正确性、收益、净收益和 Brier。
- `ResearchValueEvaluation.outcomes` 的 `pending/unavailable/estimated/observed` 状态被明确区分。
- 复盘数据来自既有 `ForecastRecord`、`OutcomeRecord` 和 evaluation，不把缺失标签当成未命中。

### M2：durable scheduled recheck

- 新增 `ReviewScheduleRecord`、迁移 `0037_review_schedule_ledger` 和 `ReviewScheduleService`。
- 支持 `next_review`、`macro_event`、`invalidation_evidence`、`outcome_due` 四类触发器。
- `parent_run_id + horizon + trigger_hash` 幂等；重启后从数据库继续，子 Run 的 admission 仍走原有 Run ledger。
- 计划状态可见为 `planned/admitted/completed/skipped/failed`；worker tick 同步子 Run 状态。
- 历史预测只保留 outcome review，不会因为重启重新生成已经过期的 next-review/macro/invalidation 子 Run。

### M3：官方宏观事件日历

- 新增 `MacroEventRecord` 与 append-only `MacroEventVintageRecord`，迁移 `0034_macro_event_ledger`。
- 统一 Fed、ECB、BOJ、BLS 的事件实体，保存 UTC 时间、官方 URL、authority、prior、consensus、actual、vintage、revision、surprise 和 status。
- 官方日历适配器只负责事实发布；缺少 consensus 时保持 `null`，不从 prior 或模型猜测 consensus/actual。
- `/v1/macro-events` 和 Research 页面展示 scheduled/released/revised 以及 prior/consensus/actual/surprise。

### M4：前视审计

- `ForecastRecord` 通过迁移 `0035_forecast_point_in_time_audit` 固化 `forecasted_at`、`as_of`、`strategy_version`、`input_snapshot_id`、`input_fingerprint`、feature snapshot 和 evidence cutoff。
- `/v1/research/runs/{run_id}/forecast-audit` 提供预测是否早于 outcome、是否使用不可变 snapshot、是否跟随 trailing trend 的审计结果。
- 评估代码不回写历史输入，也不允许 outcome 之后的新数据改变已保存 fingerprint。

### M5：样本外校准和 Gate

- `packages/evals/forecast_calibration.py` 使用 chronological walk-forward split，并按 30m/24h/72h/7d 设置 horizon gap/embargo。
- 报告保存 accuracy、Wilson lower bound、Brier、log loss、成本后净收益、最大回撤、覆盖率、regime share 和 reliability bins。
- 新增 `StrategyCalibrationRecord`、迁移 `0036_strategy_calibration_receipts`、版本/horizon 精确匹配验证和 `/v1/strategy-calibrations`。
- 只有完整、不可变、版本与 horizon 匹配且状态为 passed 的 receipt，才允许方向性 forecast 进入 calibrated；模型自己填写的 `probability_status=calibrated` 会被降级。
- 页面展示评估时间、样本数、折数、版本和准入状态；当前没有真实策略通过 60% 门槛。

## 质量门证据

```text
uv run pytest -q                 767 passed, 4 skipped
uv run ruff check .              All checks passed
uv run pyright <changed modules> 0 errors, 0 warnings
pnpm --dir apps/decision-desk test -- --run  16 passed
pnpm --dir apps/decision-desk build            passed
git diff --check                 passed
```

新增/补强的关键回归覆盖：

- scheduled recheck 的时间触发、宏观事件/失效证据/到期触发和重启幂等；
- scheduled/released/revised 宏观事件 vintage，不伪造 actual/consensus；
- forecast PIT fingerprint、未来信息拒绝和反追涨审计；
- calibration split、horizon gap、校准准入和无 receipt fail-closed；
- Windows 下本地 JSONL 通知可跨系统编码读取；
- 候选产物使用与 hash 完全一致的 UTF-8 bytes，重启后不再因换行转换出现 `candidate_artifact_hash_mismatch`。

## 当前可交付边界

- 可以交付“有证据、可复查、可复盘、带阻断原因”的研究产品核心链路。
- 不能把当前结果包装成已经验证的 60% 胜率，也不能自动下单。
- 官方 Fed/ECB/BOJ 日历提供时间和发布状态；没有授权的 consensus provider 时，页面会明确显示 consensus 缺失。
- 真实 Provider 长期稳定性、足量历史样本和生产收益仍需要独立的 live/holdout 门禁，不能用本地 fixture 代替。

## 本机运行态验收

2026-09-19 重启了 `infra/dsh/run-product.ps1` 管理的 API、research MCP、DSH Web、realtime worker、research worker 和 historical worker。健康检查返回 `status=ready`，三个核心服务心跳为 `online`；`/v1/research/inbox`、`/v1/macro-events`、`/v1/strategy-calibrations`、`/v1/research/runs/{id}/forecast-audit` 和 `/review-plans` 均可访问。realtime worker 已实际写入官方 Fed/ECB/BOJ/BLS 日历事件；校准列表为空，没有把缺少的授权共识伪造成数据。

重启时 OKX 到期窗口曾出现连接失败。`DueOutcomeService` 现在把这类失败记录为可观察 warning、保留 forecast 未标记状态并继续下一 tick；不会让来源/日历 worker 退出。随后运行态检查显示 realtime worker 仍为 `online` 且无持续错误码。

提交了一次真实 BTC 当前研究 Run（run id 已留在本机 ledger，不在文档中传播运行凭据）。这次运行经历 2 个研究 round、13 条 trace 证据调用、7 条 accepted Evidence，遇到一个官方事件目标无法解析和若干 stale cross-asset 数据后，按 `no_actionable_path` 正常收敛为 `research_only`，没有卡死也没有发布交易授权。实际研究方向为：

- 30m：`no_trade`，等待 80890.40 上方回撤企稳或 82278.20 上方持续突破；
- 24h：`neutral`，要求现货保持在 80890.40 上方且衍生品不出现同步 OI 扩张与价格转弱；
- 72h：`long` 研究方向，要求日线保持 `close > SMA20 > SMA60` 并确认 82278.20 上方突破。

这三条方向的执行 Forecast 仍被确定性 Gate 保存为 `no_trade / 0.5 / uncalibrated`，`trade_authorized=false`。这是当前策略没有样本外校准 receipt 时的预期行为：报告给出可复核的研究方向，账本不给未经证明的概率和交易权限。三条 `next_review` 计划已落库，状态均为 `planned`，分别对应 30m、24h、72h。

## 下一步只允许做的事情

1. 重启本机 API/worker，应用 Alembic `0034`–`0037`，调用 health、macro-events、review-plans、forecast-audit 和 strategy-calibrations 做一次运行态 smoke。
2. 提交一个新的 BTC 研究 Run，确认页面能看到方向、证据、复查时间线和“未校准/无交易授权”的明确状态。
3. 收集真实前视样本后再运行 calibration CLI；在 receipt 通过前不改 Gate 以迎合单次报告。

## 2026-09-20 前后端验收记录

- 使用 `infra/dsh/run-product.ps1` 重新启动并验证：Decision Desk `8047`、DSH Web `3097`、Research MCP `8037` 可访问；Hub API、Realtime worker、Research worker 心跳为 `online`。Evolution worker 仍是 owner 触发范围，不作为当前核心主链路启动条件。
- 通过页面“获取最新操作建议”实际提交 BTC 研究 Run：`run_ab5aac8ac7db4623b835427086ac35f6`。本次取证保留 13 条 Evidence，hard/soft coverage 均为 100%，关键缺口为 0。
- DSH 模型步骤在 150 秒单步上限内超时，Host 返回 `dsh_model_step_timeout`；运行时保留已验证 Evidence，并按 evidence-only fallback 收敛为 `research_only`，没有把失败伪装成完成。
- 页面最终显示：7 日规则基线偏多，已有现货持有倾向、空仓多头候选优先；概率不可用、报告可靠性有限、交易权限未授权。独立 reviewer 指出低成交量、美元/10 年期收益率上行、基差为负等反方限制，因此这次结果不能包装为已验证交易结论。
- 这次验收暴露的实际交付限制是 DSH 单步超时后的“有证据但无完整综合链”降级路径，而不是 API 或页面不可用；页面会显示 Run、证据、研究过程、复核意见和执行权限状态。
+## 2026-09-21 方案补充与目标冻结

### 本次里程碑

- 重新审查 docs/plan/DECISION_HUB_CORE_RESEARCH_AND_EXPLAINABILITY_DESIGN_2026-09-21.md。
- 补充信息源获取工程：Source Registry、Route Policy、Acquisition Job、Raw Artifact Store、Normalizer、Acquisition Receipt、Evidence Admission。
- 明确信息获取路线：官方 API/JSON/CSV/ICS → HTTP/HTML → Playwright DOM/Network → Screenshot/OCR/视觉模型 → Computer Use/浏览器 Agent 兜底。
- 确认 Playwright + Crawlee 是确定性采集底座，Stagehand/browser-use 只作为动态页面发现或受控 fallback。
- 补充浏览器采集的 DOM、网络响应、截图、trace、hash、时间点和脱敏要求。
- 补充采集任务生命周期、artifact retention、来源健康检查、contract/migration/codegen、feature flag、fixture/live canary 和回滚要求。
- 明确当前不引入外部多 Agent handoff、自由 swarm、3D 图和自动交易执行。
- 创建本轮目标：实现方案中 P0/P1/P2 的核心内容，并通过构建、定向测试、真实 BTC Run 和页面回读验收。

### 目标状态

- 状态：active
- 目标范围：零输入最新建议幂等、Run 版本比较、Evidence-only/规则降级 Decision Graph、宏观日历历史与 vintage、Playwright/Crawlee 信息采集最小闭环。
- 完成标准：代码实现、契约/迁移、定向测试、前端构建、真实来源 canary、真实 BTC Run、页面回读全部有记录；任何未完成项必须保留具体阻塞原因。
- 本里程碑没有修改业务代码；只更新了方案和执行日志，等待进入 P0 开发。

### 方案审查结论

本轮没有发现会改变总体架构的遗漏，但补充了会影响可落地性的工程条件：

1. 新 schema 必须走 canonical schema → codegen → Python/TypeScript contract test；
2. 浏览器采集必须是独立 Job，不把浏览器进程塞进 Research Worker；
3. 原始 HTML/JSON/DOM/截图/trace/HAR 必须保存 hash 和脱敏后的 artifact ref；
4. 每个新来源必须先登记并通过 live canary；
5. fixture、local browser smoke、真实来源 canary 分层；
6. P0/P1/P2 通过 feature flag 和回滚路径发布；
7. 每个里程碑记录目标、代码、命令、测试、真实 Run、页面回读和限制。

## 2026-09-21 P0 Decision Graph、日历历史和采集回执

### 已实现

- 在 `agentic_research.schema.yaml` 增加 `DecisionGraph`、节点/边和 `ResearchRunComparisonView` 契约，完成 codegen；`ResearchRunDetailView` 现在带可选 `decision_graph`。
- 新增 `packages/query_views/research/decision_graph.py`：有完整 `causal_case` 时投影主链/反链；综合失败或证据不足时只投影 Evidence、coverage、unresolved stop 和执行质量门，不制造因果结论。
- API 增加 `/v1/research/runs/{run_id}/decision-graph` 和 `/comparison`；比较结果明确方向、证据数量、图状态和变化字段。
- 前端接入 `@xyflow/react` 只读图，节点固定宽度、摘要截断、颜色表达 supporting/opposing/blocked，技术详情显示上一版本差异。
- `/v1/macro-events` 增加 `scope=all|upcoming|history`、offset；新增 `/v1/macro-events/{id}/vintages`，前端日历增加全部/即将发生/历史发布切换。
- 新增 `apps/source_acquisition_worker`：Crawlee `RequestList` 管理单任务队列/重试，Playwright 采集 DOM、XHR/fetch 网络 JSONL、截图、trace，写入内容 hash 和 `receipt.json`；新增 `source_acquisition.schema.yaml`。
- 官方动态日历 SourceManifest 声明 `http`、`playwright_dom`、`playwright_network`、`screenshot_vision` 能力，不改变现有 adapter admission 路径。

### 自测结果

```text
uv run pytest -q tests/contracts/test_agentic_research_contract.py tests/research/test_research_api.py tests/research/test_macro_event_ledger.py tests/research/test_decision_graph.py tests/research/test_research_observability.py  35 passed
pnpm --dir apps/decision-desk test  16 passed
pnpm --dir apps/decision-desk build  passed
pnpm --filter @decision-hub/source-acquisition-worker typecheck  passed
pnpm --filter @decision-hub/source-acquisition-worker smoke  passed: 1 page, 4 artifacts, receipt readback
uv run python -m tools.contract_codegen check  passed
uv run ruff check ...  passed
git diff --check  passed
```

### 当前限制和下一步

- Crawlee 在当前依赖组合下会尝试写 SDK 统计并输出 `setValidationEnabled` warning，但请求、Playwright handler、artifact 和 receipt 均成功；需要在依赖升级或配置层再清理 warning，不能把 warning 当作来源成功凭证。
- 本次 browser smoke 使用本地 fixture；真实 BLS/Fed/ECB/BOJ 页面仍需单独 live canary，真实来源失败必须落 gap，不能阻断已有研究 Run。
- P0 代码已可用，下一阶段继续补 Run window/supersedes 的持久化字段、真实 BTC Run 回读和前端运行态截图；未完成前目标保持 active。

### 真实来源 browser canary

```text
pnpm --filter @decision-hub/source-acquisition-worker canary  passed
source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
status_code: 200
receipt_id: dc815900b84b3d3a57b91e5193bfc7b5
artifacts: DOM / network JSONL / screenshot / trace (4)
```

Canary 的本地 artifact 已清理，仓库只保留可重复脚本和 receipt contract；不会把页面内容或浏览器 trace 提交进 Git。

## 2026-09-21 运行态 BTC 验收

### 服务启动

```text
infra/dsh/run-product.ps1       passed
8047 / Hub API                  LISTEN
8037 / Research MCP             LISTEN
3097 / DSH Web                  LISTEN
hub-api                         online
hub-research-worker             online
hub-realtime-worker             online
model                           gpt-5.5 / provider
```

健康接口整体返回 `degraded`，原因是历史来源轮询中 BLS/BEA 等公开源出现 `source_unavailable`；Fed/BOJ 日历和 Fed speeches 在本次启动周期内有成功轮询。该降级被保留在 Operations 和 Source Health，不影响 API/研究 worker 启动，也没有把缺失来源伪造成 Evidence。

### 零输入研究 Run

使用固定入口文本 `获取最新操作建议` 和一次性 smoke Idempotency-Key 提交，得到：

```text
event_id: evt_7c5d381e34ff4e9b8801ce76a4eb308b
run_id: run_edf327bc678b45d6b7caaa857f4e24b5
terminal_status: research_only
stage: done
rounds: 1
accepted Evidence: 12
decision graph: causal / causal_case
graph nodes/edges: 13 / 13
```

本次 Run 的 stop reason 为 `sufficient`（方向性 hard evidence 已通过，执行层 gap 仍单独受 Gate 控制）。7 日规则展望为偏多，执行权限仍未授权；研究报告保留了证据、主链/反链和执行质量门。

### 版本比较和日历回读

最初比较接口发现 API 500，定位为服务层构造 generated `ResearchRunComparisonView` 时遗漏 `schema_version`；已修复、重启产品并重新回读。修复后的结果：

```text
GET /v1/research/runs/run_edf327bc678b45d6b7caaa857f4e24b5/comparison 200
previous_run_id: run_311d298782db438cb4c9d8b0cae1f27b
same_event: true (latest-advice source/event_hint 版本比较)
changed_fields: direction, graph_status, evidence_count, headline
GET /v1/research/runs/{run_id}/decision-graph 200
GET /v1/macro-events?scope=all 200
GET /v1/macro-events?scope=upcoming 200
GET /v1/macro-events?scope=history 200 (当前库没有已过期事件，返回空数组)
```

页面 bundle 已包含 Decision Graph 和日历范围切换；根 HTML 回读成功。该 Run 的模型综合由 DSH 运行时完成，没有把“研究方向”误写成“交易授权”。

### 最终自测汇总

```text
uv run pytest -q                                      770 passed, 4 skipped
pnpm --dir apps/decision-desk test                    16 passed
pnpm --dir apps/decision-desk build                   passed
source-acquisition-worker typecheck + smoke           passed
Fed browser canary                                    passed (HTTP 200, 4 artifacts)
contract_codegen check + ruff + git diff --check      passed
```

比较接口在最后一次 generated-model 修复后再次回归：

```text
uv run pytest -q tests/research/test_research_api.py tests/research/test_decision_graph.py tests/research/test_macro_event_ledger.py tests/sources/test_acquisition_receipt.py  17 passed
8047/8037/3097 仍为 LISTEN，GET /v1/health 返回 200
```
