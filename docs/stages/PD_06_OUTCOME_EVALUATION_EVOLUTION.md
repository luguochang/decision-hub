# PD-06 Outcome、Evaluation 与受控进化

状态：`completed / engineering evidence verified; prospective value pending PD-07`  
日期：2026-09-05（Asia/Shanghai）  
上级方案：[`PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md`](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)

## 1. 阶段目标

把每次研究从一次性报告变成可积累的产品资产：

```text
Run/Artifact/Forecast
  -> T+30m/T+24h/T+72h Outcome
  -> forecast metrics + research usefulness/coverage/latency/cost
  -> FailurePattern/Experience candidate
  -> replay -> holdout -> shadow
  -> owner review
  -> promote / retain / stop
```

方向性 Forecast 只在事实充分且 Gate 允许时计算 Brier/方向/MFE/MAE/费用后结果；
`research_only/no_trade` 也必须记录事实覆盖、耗时、成本、引用质量、人工 usefulness 和失败模式，
不能因为没有交易方向就完全跳过评测。

## 2. 非目标与硬边界

- 不自动 Promotion、不在线改 Gate、不让 Agent 写 active pointer。
- 不自动交易、不以回测收益代替 owner review 或合规/风险控制。
- 不用未来数据回写历史 Snapshot，不覆盖历史 Run/Artifact/Forecast/Outcome。
- 不让模型自评成为唯一标签；确定性指标和 owner feedback 分开记录。
- 不为第二领域提前抽象 Platform Core；复用现有 Extension/Pack 契约即可。
- 不把 PD-06 完成宣传为预测准确或盈利通过；真实价值仍需 PD-07 前瞻观察。

## 3. 现有复用点

| 能力 | 现有唯一实现 | 本阶段处理 |
|---|---|---|
| Forecast/Outcome/Evaluation | `CommitDecisionService`、`DueOutcomeService`、`OutcomeService` | 扩充研究级评测，不重写交易指标 |
| Failure/Feedback/Experience | `EvolutionAssetService` | 复用 lineage 与去重 |
| candidate/eval | `EvolutionJobService`、`EvolutionExecutor`、`EvaluationRunner` | 复用 replay/holdout/shadow |
| Promotion | `EvolutionAssetService.review_promotion/promote` | 保留 owner-only + CAS |
| 页面 | Decision Desk | 增加价值/Outcome/候选投影，不放进 DSH Chat 主流程 |

## 4. 必须补齐的缺口

1. 当前 `DueOutcomeService` 对 `no_trade` 直接跳过，因此 research-only Run 没有产品价值记录。
2. 当前交易 Evaluation 只有 Brier、方向和 net return，缺 MFE/MAE（数据可得时）及数据质量说明。
3. coverage/latency/cost/usefulness 没有形成 canonical run-level Evaluation。
4. 重复发生的 semantic gap、Provider failure、baseline miss 尚未稳定转成 candidate input。
5. 必须用测试证明 agent 或 scheduler 无法绕过 owner review 自动 Promotion/改 Gate。

## 5. Canonical 契约与持久化

在 `research_product_view.schema.yaml` 增加：

- `ResearchValueEvaluation`：run/artifact、mode、coverage、latency、cost status、citation quality、
  usefulness（nullable）、outcome coverage、created/evaluated timestamps；
- `HorizonOutcomeView`：forecast、horizon、quality、return/Brier/direction/MFE/MAE（均可 nullable）；
- `EvolutionCandidateSummary`：candidate kind、input refs、repeated pattern count、stage、owner status。

若 run-level Evaluation 不能由既有表无损推导，新增 append-only 表和 migration；唯一键为
`(run_id, evaluation_version)`，重跑必须幂等。Owner usefulness 使用现有 Feedback/Experience lineage，
不覆盖机器指标。历史 Forecast/Outcome/Evaluation 表保持不变。

## 6. 研究级指标

### 6.1 所有 Run

- hard/soft semantic coverage；
- accepted/rejected/stale Evidence 与 typed Fact 数；
- baseline status 与 event-window coverage；
- end-to-end latency、round/tool/provider attempts；
- cost status/known subtotal；
- citation traceability；
- terminal reason 与 error provenance；
- owner usefulness：`useful / partial / not_useful / unlabeled`。

### 6.2 有效方向 Forecast

- 30m/24h/72h return、direction correct、Brier；
- fees/slippage 后 net return；
- Provider 窗口支持时记录 MFE/MAE，否则为 null + unavailable reason；
- `no_trade/research_only` 不伪造方向正确率或 Brier。

## 7. Candidate 与 Promotion

候选来源仅限可追溯模式：

- 同一 requirement 多次 `provider_timeout/rate_limited` -> provider route candidate；
- 多次 `semantic_mismatch` -> source/mapper/eval fixture candidate；
- 多次 `baseline_unavailable` -> calendar/sampler policy candidate；
- owner `not_useful` 且有具体 feedback -> role/doctrine candidate。

Agent 只能提交 candidate；candidate 必须经过 replay、holdout、prospective shadow，且三个阶段都有
独立 evaluation refs，才可进入 owner review。Promotion 必须由显式 owner request 调用，active pointer
通过 generation CAS 更新。任何无 owner decision 的自动 Promotion 都应抛出稳定错误并留审计。

## 8. 代码落点

```text
contracts/schemas/research_product_view.schema.yaml
migrations/versions/0030_research_value_evaluations.py   # 仅在需要耐久字段时
packages/kernel/.../outcome.py
packages/kernel/.../outcome_due.py
packages/kernel/.../evolution.py
packages/query_views/research/service.py
packages/orchestration/langgraph/evolution_executor.py
apps/hub_worker/composition.py
apps/hub_api/main.py
apps/decision-desk/src/research/ResearchPage.tsx
```

## 9. 失败与恢复语义

- Outcome 行情暂不可得：保持 due/pending，不生成伪 0 return；有界重试并记录数据质量。
- 同一 Forecast/Run 重复评测：返回原记录，不重复 candidate。
- research-only 没有方向：只生成研究价值 Evaluation，不生成交易指标。
- evolution worker 崩溃：按 durable job checkpoint 恢复；candidate/input refs 保持幂等。
- replay/holdout/shadow 任一缺失或泄漏：owner review `eligible=false`。
- owner review 并发：只有一个 active pointer CAS 胜者，其他请求明确失败。

## 10. BDD

```gherkin
Scenario: research-only 仍被评测
  Given Run 因 macro provider blocked 以 research_only 完成
  When Evaluation worker 运行
  Then 记录 coverage/latency/cost/citation/baseline/usefulness 状态
  And 不生成伪方向、伪 Brier 或伪收益

Scenario: 有效方向 Forecast 到期
  Given publishable Forecast 有 30m horizon
  And PIT 合法的 market window 已可用
  When 30m 到期
  Then 记录 return、direction、Brier、fees/slippage
  And MFE/MAE 只有在 window 支持时记录

Scenario: 重复失败产生候选但不自动修复
  Given 相同 requirement 的 provider timeout 达到版本化阈值
  When evolution worker 聚合 FailurePattern
  Then 创建有 input refs 的 provider-route candidate
  And 不修改 active route 或 Gate

Scenario: 自动 Promotion 被拒绝
  Given candidate 已有 replay/holdout/shadow
  But 没有显式 owner decision
  When agent 或 scheduler 请求切 active pointer
  Then 请求失败且 active generation 不变
```

## 11. TDD 与验收

- Contract/migration：历史库升级、append-only、重复计算幂等、nullable 指标。
- Outcome：30m/24h/72h、无行情、no_trade、fees/slippage、MFE/MAE 可用/不可用。
- Evaluation：research-only coverage/latency/cost/usefulness，PIT leakage 拒绝。
- Evolution：重复 FailurePattern -> candidate，缺 lineage 不建 Experience。
- Promotion negative tests：agent/scheduler/无 owner decision/缺 stage 均不能 promote。
- E2E：Event -> Run -> Report -> recheck -> Outcome/Evaluation -> candidate/owner queue。
- UI：管理页能看价值、数据质量、候选阶段和 owner 状态，无 raw JSON 默认页。

## 12. 退出门

- [x] 30m/24h/72h 到期 Outcome 可自动处理且幂等；
- [x] research-only 生成研究价值 Evaluation，不伪造交易指标；
- [x] coverage/latency/cost/citation/usefulness 可追溯；
- [x] 反复失败可形成有 lineage 的 candidate；
- [x] replay/holdout/shadow 与 owner review 边界保持；
- [x] 自动 Promotion、自动 Gate 修改和自动交易的负向测试通过；
- [x] 历史数据不改写，升级/回滚/重启测试通过；
- [x] 全量质量门和执行证据落盘。

完成证据统一见 [PD 实施与自测执行日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
`research_only` Run 已验证只生成 coverage/latency/cost/citation/usefulness 研究价值快照，方向、
Brier、MFE/MAE 和收益保持 `null`；有效 Forecast 的交易指标仍只在 horizon 到期且行情质量合法时
计算。代码与迁移完成不等于已观察到预测价值，后者只能由 PD-07 的未来事件样本验证。

PD-06 通过后，`PD-00..06` 才构成 Personal usable 的工程候选。随后只能建立 PD-07 的
14 天或 20 个前瞻事件观察入口；在观察门通过前，不得声称预测准确、盈利或正式产品交付。
