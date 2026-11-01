# 核心链路原因与修复记录

日期：2026-09-15  
范围：只处理“输入 → 证据 → Review → Gate → 用户结论”主链，不扩展 PPT、Evolution 或外围评测。

## 已确认的原因

1. Decision Desk 的手工输入没有发送 `event_hint`。Admission 把没有 hint 的 BTC 当前快照落成 `macro_event`，因此使用了宏观事件契约，要求事件身份、政策变化和预期定价等字段；OKX/Binance 已取得的行情事实因此被错误地显示为缺口。
2. 旧 Gate 把 `direction=no_trade` 一律降级为 `research_only`。这把“研究完成后安全地得出等待确认”误投影成了“报告没有完成”。
3. FRED 公开数据目前只有日频且滞后；当前没有已批准的免费分钟级 rates/USD 与标准 cross-asset event window。它们只能作为背景或软缺口，不能伪造成实时证据。

## 已实施

- Admission 增加有界的 BTC 快照自动分类，显式 hint 仍优先。
- `research_complete` 只由确定性条件生成：结果 completed、hard coverage=100%、无 hard gap/conflict、stop=sufficient、独立 Review accepted、方向概率已校准或为非方向情景。
- Gate 允许已完成的 `no_trade` 研究报告进入 `publish`，但 `trade_authorized` 固定为 `false`；未完成的 no-trade 仍然降级。
- DSH 原生 Review receipt 只能引用可信快照/结果 Evidence；receipt 无效时走确定性 bounded review，不能让 Worker 丢弃已有证据。

## 真实验收

Run `run_bd5f6a601ece403c87842e7aa3928cd2`  / Artifact `art_43904843edcb4c9782cde2556944ab25`：

- event family：`crypto_market_snapshot`；
- status：`completed`；artifact：`publish/saved`；failure：`null`；
- hard coverage：`100%`；soft coverage：`33%`；accepted Evidence：`15`；Review：`accepted`；
- 来源：`okx-public`、`binance-public`、`okx-orderbook-public`，另有本地 1h 历史 coverage；
- 用户结论：`wait_for_confirmation`，不是空结果；30m/24h 为中性条件情景，72h 为 `no_trade`；
- 剩余软缺口：`macro_transmission=stale`、`cross_asset_confirmation=semantic_mismatch`；两者均 `blocks_directional_output=false`；
- 交易权限：`trade_authorized=false`。

## 验收命令

```powershell
uv run pytest -q tests/kernel/test_core_flow.py tests/orchestration/test_supervisor_graph.py tests/research/test_market_snapshot_admission.py tests/research/test_context_and_review.py tests/evolution/test_research_worker.py tests/research/test_research_api.py tests/dsh_native/test_product_launcher.py
# 51 passed, 2 skipped

pnpm --dir apps/decision-desk test
# 12 passed

GET http://127.0.0.1:8047/health/ready       # 200
GET http://127.0.0.1:3097/decision-hub/v1/readiness  # 200（带 host key）
```

## 后续只允许做的核心工作

- 若要提高方向判断能力，接入经过 owner 批准的实时 rates/USD 与标准 cross-asset provider，并为其增加 PIT、来源独立性和 event_return 测试；不得用 Search 摘要或 FRED 日频冒充实时数据。
- 为 Binance/OKX 衍生品字段增加按字段、按 venue 的覆盖矩阵，明确 OI delta 和 crowding 是否需要每个字段都双 venue；通过后再重新跑同一主链。
- 收集足够历史 Run 后再做概率校准和方向价值验证；在此之前只交付有证据的研究报告，不生成自动交易权限。

## 为什么“质量门通过”仍然没有交易方向

这里原来混用了两个不同问题，导致页面和验收口径都产生了误导：

1. **Evidence/Report Gate（已通过）**检查必需 Evidence、PIT、引用、反向论点、触发/失效条件、独立 Review 和 hard coverage。它回答“这份研究是否完整、可追溯、没有把失败伪装成成功”。
2. **Directional Value Gate（未通过）**应该检查方向覆盖率、样本外命中、概率校准、扣成本收益、回撤和相对简单 baseline 的增益。当前运行时没有这个 Gate，也没有能生成 `calibrated` 概率的信号/校准组件。

当前 Run 的准确状态因此是：**证据审计通过，方向价值未验证**。`publish/saved` 只表示报告可保存和审阅，不表示 long/short 有经济价值，更不表示授权交易。

### 结构性阻断

- `packages/kernel/decision_hub_kernel/application/commit.py::_research_result_is_publishable` 要求 long/short 的 `probability_status` 必须是 `calibrated`；否则写入 Forecast 时会 fail closed 为 `no_trade/0.5`。
- 仓库没有训练、拟合或加载校准器的运行组件。现有 36 个冻结判断全部是 `uncalibrated`，DSH 只产生 1 个方向判断，因此没有任何路径能把模型概率升级为 `calibrated`。
- `packs/crypto_macro/gates/horizons.yaml` 声明了分周期规则和 `uncalibrated_probability_cap: 0.7`，但运行时没有加载它；实际 Gate 仍使用代码内固定的 `0.65`。配置文件目前不是生效策略。
- `HorizonDecision.missing_facts` 是自由字符串数组，没有被限制到 pack 的 canonical evidence requirements。模型可以持续新增系统无法满足的“缺失事实”，Review 也可能接受，造成报告永远显得还差一些数据。
- `probability_status` 目前只是 `uncalibrated/calibrated` 字符串，没有 `calibrator_id`、版本、训练/校准数据 hash、样本量或 holdout 证明。Commit 会相信该字符串；因此正常模型写 `uncalibrated` 时没有方向成功路径，未来若模型误写 `calibrated` 又可能绕过本意。校准证明必须由系统签发，不能由 LLM 自报。
- 旧 `build_decision_graph` 入口没有 ResearchResult，只要事实/引用/反方/操作字段齐全且概率不高于 0.65，未校准 long/short 也能得到 `publish`。这与 DSH 新路径的 fail-closed 语义冲突，是实际的方向发布漏洞。
- 当前方向和主观概率主要由 LLM 合成。Evidence 增多能够提高可审计性，不能自动形成可复算的交易信号或样本外概率。

### 数据是否是唯一原因

不是。当前 BTC 快照已经有 OKX/Binance 双交易所现货、资金费率、基差、OI/OI delta、OKX 订单簿和本地小时历史环境；这足以让本次 Evidence Gate 的 hard coverage 达到 100%。实时 rates/USD 与跨资产窗口仍是软缺口，补上后能改善解释，但不会凭空产生校准器。

免费数据可支持第一版核心验证：Binance Vision 的分钟 K 线已经用于 12 个历史事件的 30m/24h/72h outcome；公开交易所 API/归档也能提供部分衍生品和盘口特征。需要先用这些数据证明信号增益。只有产品明确要求宏观事件的分钟级 rates/USD、指数和可靠历史盘口时，才评估付费源；不能在信号尚未成立前用采购更多数据替代价值验证。

当前 hard 100% 还是 requirement 聚合级覆盖，并不表示每个字段都由两个 venue 确认。例如 OI delta 只有 Binance，crowding 只有 OKX 单一盘口代理，聚合后仍可能满足 `derivatives_crowding`。方向内核启用前必须增加 field × venue coverage matrix，不能把 requirement 级“两个来源”误读为每个关键特征已交叉验证。

## 本次立即修复

- 查询投影只把 `importance=hard` 或 `blocks_directional_output=true` 的缺口放入 `blocking_gap_ids`；软缺口保留为补充限制，不再显示成“被阻断”。
- 行动摘要新增 `directional_value_unvalidated`，并将成功但未校准的报告写成“研究报告已完成，当前未形成经过验证的方向优势”。
- Decision Desk 同时展示“证据审计”和“方向价值”，避免用“质量门通过”暗示交易有效。
- 同一真实 Run 回读结果：`artifact_status=saved`、`data_status=partial`、`blocking_gap_ids=[]`、`directional_value_unvalidated`，两个现有 gap 均为非阻断补充项。
- Windows 本机启动器原先让 Worker 继承调用 shell 中的空 `DECISION_HUB_SOURCES_ENABLED/DECISION_HUB_MARKET_ENABLED`，Pydantic 布尔解析失败；启动器又只等待约 0.5 秒，错误发生稍晚而被误报为启动成功。本次已为全部 Python 子进程显式设置有效布尔值，并等待 Research worker 写出 `online` heartbeat 后才返回成功。最终 Hub API、DSH、MCP、Research worker 与 Historical worker 均存活，核心页面显示 `2/2`。
- 所有 Gate 入口现在都要求由调用方显式传入受信的 `directional_value_validated`；默认 false。旧路径的未验证 long/short 只能得到 `research_only`，正式 Forecast 固定写为 `no_trade/0.5`。DSH 路径也不再信任模型自报的 `calibrated` 字符串；在校准凭证落地前，方向只能作为 ResearchResult 内的研究假设保留。

## 下一阶段唯一核心目标

在继续角色、PPT、调度和页面功能前，单独实现并验证可复算的 BTC 方向信号内核：

1. 按时间冻结 30m/24h/72h outcome、交易成本口径、Dev/Calibration/Holdout 分割和 baseline。
2. 从已接入的价格收益、成交量、资金费率、基差、OI delta、订单簿失衡生成确定性特征；缺失值作为显式特征，LLM 不参与方向和概率计算。
3. 在 Dev 拟合信号，在独立 Calibration 段使用 Platt 或 isotonic calibration，将概率状态升级为 `calibrated`；Holdout 全程不可调参。
4. Directional Value Gate 至少检查：每个周期足够的独立方向样本、方向覆盖率、Brier/ECE 相对基准概率改善、扣 10 bps 成本后的收益、最大回撤和相对 always-long/always-neutral/简单动量的增益。
5. 只有 Directional Value Gate 通过，才允许 calibrated long/short 进入正式 Forecast；LLM 只生成基于 Evidence 的解释、反例、触发和失效条件。

该目标失败时，产品应明确定位为“可审计研究助理”；通过后才重新定位为“交易决策产品”。
