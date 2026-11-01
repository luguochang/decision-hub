# C1 核心可交付自测记录（2026-09-19）

> 更正：本记录的工程回读结果仍保留，但“可交付”判断撤回。旧日线聚合未检查每日24小时完整性，且当前查询使用旧入站时间；旧收盘/触发价不得直接作为当前建议。单调行情测试仅验证代码行为，不能声称五案例价值或基线增益通过。修复与真实复测见 CURRENT_DIRECTION_CORRECTION_2026-09-19.md。

## 验收口径

本轮只验收 BTC 的核心研究链路：固定入口、日线策略、Evidence/Fact 绑定、根因链、行动边界和 UI 回读。实时交易权限仍然关闭；实时数据不可达时必须保留失败原因，不能用 replay 冒充 live，也不能用模型文本补齐。

## 真实 Run

- 输入：`获取最新操作建议`
- Run：`run_e863345fcc254c28962f0baf7ac198d2`
- 最终状态：`research_only`
- 报告状态：`limited`
- 真实 Evidence：`ev_c588806e84cbf8c93af273125321803e`
- Evidence 来源：`okx-public`，数据集 `crypto.btc_usdt.ohlcv_1d`
- 观测窗口：`2026-06-20T00:00:00Z` 至 `2026-09-18T00:00:00Z`，实际最新闭合数据至 `2026-09-16T14:00:00Z`
- Fact：9 条，策略版本 `daily-trend-v0`
- 策略 hash：`ad47c2304ebeeb09f0dca66f44e9f7534d58face68edaa6b5456a900a697d22d`
- 研究结论：`range`
- 当前行动候选：`wait_for_range_break_or_trend_alignment`
- 触发/失效：收盘有效突破 `82285.00` 或跌破 `74955.50` 后重算
- 概率：`null`，状态 `unavailable`
- 交易授权：`false`

UI 通过 `http://127.0.0.1:8047/?run_id=run_e863345fcc254c28962f0baf7ac198d2` 回读确认：首屏显示“7日主要方向：区间”、行动候选、触发条件、最强反方、判断失效、Evidence 链接、数据限制和未授权交易；技术详情显示 8 步过程、19 次工具调用及失败原因。

### 真实数据失败边界

本次实时现货、资金费率/OI/基差、盘口拥挤度和搜索补证均失败，主要错误是 `provider_transport_unavailable`、`research_market_unavailable`、`research_domain_denied`、`search_provider_failed`。因此最终仍为研究态，不生成可交易方向。日线是归档小时线聚合，UI 明确标注“最新覆盖可能滞后；只作为日频背景”。

## 五个核心案例

| 案例 | 输入/验证 | 结果 | 价值判定 |
|---|---|---|---|
| 上涨趋势 | 80 根单调上升闭合日线 | `bullish`；等待回踩或突破确认 | 有方向、条件和失效，不强行 no-trade |
| 下跌趋势 | 80 根单调下降闭合日线 | `bearish`；降低多头暴露并等待收复 | 有方向，但没有自动做空权限 |
| 区间震荡 | 80 根交替收盘日线 | `range`；等待区间突破/趋势对齐 | 概率为空，避免伪造优势 |
| 冲突/不充分 | `test_event_window_crowding_chain.py` 的事件窗口拥挤度与语义 Gate | 证据关系或字段不足时保留 gap/冲突，不升级为方向 | 失败原因可追踪，不能拿局部代理冒充全市场 |
| 当前数据不足 | 上述真实 Run | 7 日 `range` 仅作为日线背景；实时硬缺口导致 `research_only`、`trade_authorized=false` | 仍给出可执行的复查触发，同时明确不能交易 |

## 自动化检查

- `tests/research/test_daily_strategy_replay.py`：4 passed
- 日线 loader、DataJob、outlook projection、Research API：20 passed
- 研究比较和 runtime：7 passed
- 事件窗口拥挤度、上下文审查：4 passed
- 前端 ResearchPage/API/App/Operations：14 passed
- `pnpm --dir apps/decision-desk build`：通过；仅有 chunk size warning
- `ruff`：通过
- `tools.contract_codegen check`：`canonical schemas: ok`

## 交付判定

核心研究产品链路已经可以交付为“有边界的研究建议”：简单输入能固定进入 7 日模式，报告能给出方向、行动候选、触发、反方、失效、Evidence、数据缺口和交易权限边界，真实 Run 和 UI 均可追溯。

实时交易研究尚未达到交付线：当前网络环境下公开交易所和搜索网关不可达；五案例还没有样本外命中率或校准概率，因此不能把本轮 `range` 称为胜率，也不能把 `research_only` 改成可交易报告。下一步唯一核心任务是恢复至少一条当前市场数据路线，然后重新跑同一个固定入口；在此之前不继续增加外围功能。

## 增补：双交易所现货路线接入后的真实 Run（2026-09-19）

前一版记录之后，已把两条无需密钥的公开现货路线接入真实主链，并重新触发同一固定入口：

- Run：`run_afd12c295ab841d7a6bf220b0479b00a`
- Artifact：`art_7295fbb6ad9d45358d12a7012a545b20`
- 输入：`现在怎么操作`（自动展开为 BTC 7 日日频研究模板）
- 真实 accepted Evidence：Gate.io 现货、Huobi 现货、Alternative.me 情绪、OKX 归档日线，共 4 条
- 覆盖：hard `50%`、soft `25%`；唯一剩余 hard gap 为 `derivatives_crowding`
- 日频策略：`daily-trend-v0`，89 根闭合日线，最新覆盖至 `2026-09-16`；`range`
- 当前行动：`wait_for_range_break_or_trend_alignment`
- 触发：收盘有效突破 `82285.00` 或跌破 `74955.50` 后重新计算
- 反方：均线未形成同向趋势，追涨和杀跌两侧都缺少已验证优势
- 概率：`null`，`unavailable`；`trade_authorized=false`

这次主链证明当前市场现货已经能从 Gate.io/Huobi 进入 Evidence/Fact/策略/研究循环/UI；衍生品资金费率、未平仓量、基差和拥挤度的公开路线仍连续失败，所以结果仍是受限研究建议，不能升级为交易建议。此前 API 还把已形成的 7 日日频观点显示为“综合失败”，原因是旧版 30m/24h/72h 合同未填充；现已修正为“7 日日频观点已形成，旧版周期未发布”，避免把周期不匹配误报为整条链失败。

本次回归检查：定向后端 `106 passed, 1 skipped`；`ruff`、`contract_codegen check` 通过；服务 readiness 为 `ready`。产品交付状态因此定为“核心研究建议可交付，交易授权和概率预测未交付”。
