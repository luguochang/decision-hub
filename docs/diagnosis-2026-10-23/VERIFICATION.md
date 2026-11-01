# 基准率改动验证记录（P1/P2/P3 部分/P5）

日期：2026-10-23。进度来源：`PROBABILITY_BASE_RATE_PLAN.md` 的"进度"一节。

## 1. 改动文件（Codex 只读，不得修改）

新增：
- `packages/kernel/decision_hub_kernel/ports/perpetual_base_rate.py`（P1）
- `packages/kernel/decision_hub_kernel/ports/perpetual_hourly_history.py`
- `packages/kernel/decision_hub_kernel/application/perpetual_base_rate_source.py`
- `tests/decision_product/test_perpetual_base_rate.py`、`tests/decision_product/test_perpetual_base_rate_source.py`

修改：
- `packages/kernel/decision_hub_kernel/application/perpetual_research_entry.py`：新增 `collect_history`，`prepare(..., history=)` 写入 `admission.direction_base_rate`
- `.../application/perpetual_research_schedule.py`：自动排程同样采集
- `.../application/perpetual_research_binding.py`：`_authenticate_admission` 重算比对；materials 新增 `direction_base_rate`
- `.../application/perpetual_review_materials.py`：新增可选审核原件 `direction_base_rate`
- `.../application/perpetual_forecast_scoring_view.py`：新增读取时的 `base_rate_comparison`
- `packages/runtime_adapters/dsh_runtime/perpetual_profile.py`：提示词新增基准率规则
- `extensions/dsh/decision-hub/src/perpetual-synthesis-protocol.ts`：角色白名单
- `apps/decision-desk/src/api/perpetualForecastResult.ts`、`apps/decision-desk/src/product/PerpetualAdvicePanel.tsx`

## 2. 已验证

| 项 | 命令 | 结果 |
|---|---|---|
| 新单测 | `PYTHONPATH=tests/decision_product .venv/Scripts/python.exe -m pytest tests/decision_product/test_perpetual_base_rate.py tests/decision_product/test_perpetual_base_rate_source.py -q -p no:cacheprovider` | 全过（8 + 13） |
| 相关集合 | entry/binding/schedule/review_materials/close_source/deadline_separation/base_rate + `tests/runtime/test_perpetual_research_content.py` | 改动前 2 failed / 52 passed，改动后 54 passed |
| 评分 | `test_perpetual_forecast_experience.py`、`test_perpetual_forecast_result_view.py` | 11 passed |
| 前端类型 | `apps/decision-desk` 下 `NODE_OPTIONS=--max-old-space-size=8192 npx tsc -b` | 通过 |
| 真实 OKX 冒烟 | 临时库，脚本在 `_shared/temp/claude/selftest/live_hourly_smoke.py` | BTC、ETH 各 22 页，2157 样本，缺口 0，两次重算一致；90d 横盘约 34% |

## 3. 未验证 / 已知风险

- `direction_base_rate` 审核原件只在单测里构造过，没有经过真实独立审核链路。如果真实研究在审核原件校验处报错（角色或 schema 不被接受），这就是首要嫌疑。
- vitest `src/api/perpetualManualPaper.test.ts` 有 6 个失败（`localStorage is not defined`）。这个文件不引用改动模块，判断是已有问题，但没有改动前的对照。
- 全量 pytest 基线没有跑完（43% 时超时，已见约 19 个失败），未归因。
- 没有构建，没有启动，没有真实研究。

## 4. 真实验收标准（BTC、ETH 各一次 4h 研究）

必须全部满足：
1. 研究的 admission 含 `direction_base_rate`，`status=available`，`window_90d.samples ≥ 2000`，`threshold_fraction` 等于 close_window 里 4h 的阈值。
2. 研究正常走完，最终状态与改动前同类研究一致（completed 或有理由的 degraded），没有出现 `perpetual_research_base_rate_original_changed` 或审核原件相关的新错误码。
3. 独立审核的原件列表包含 `direction_base_rate`。
4. 最终方向概率：如果仍是 40/20/40，或与 window_90d 相差超过 15 个百分点，basis 里必须写明引用的证据；如果没写，记为"未达标"（不算阻塞，交给 Claude 做 P3 的确定性审核）。

记录项：run_id、开始与结束时间、最终状态、admission 中的 window_90d / window_30d 概率、最终方向概率、basis 中与基准率相关的原文（≤5 行）、错误码。
