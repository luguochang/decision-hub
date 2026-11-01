# Kronos K2 前瞻路径 Shadow 计划

日期：2026-10-17。K2 是 release33 全新 BTC 核心门通过后启动的独立长期里程碑，不属于 release33 完成条件，也不参与正式 Action 或自动 Promotion。

## 现状与裁决

- K1 已实际运行 BTC/ETH 两资产、两个原点、每例 16 路径，共 64 条路径；`engineering_trial_complete=true`。
- K1 `financial_admission=false`。ETH 2026-10-14 尾部下跌约 3.054%，16 条路径给出的 `P(跌>=3%)=0`；34/1536 OHLC 非法；CPU fixture 失败。
- K1 结论是保留 GPU 研究候选，不是正式预测能力；不重跑、不把旧结果写成通过。
- `apps/hub_worker/prospective.py` 是 direction/probability/citations 的 AgentRuntime，不适合保存 Kronos 原始路径、尾部分布和 OHLC 合法性。
- 可复用的是 `PathShadowTick -> PathShadowJobService -> RiskPathLedger` 的固定 cutoff、fit/forecast/label/finalize、lease/restart、完整分母和有限结项能力。现有 `path-shadow-24h.v1/v2` 只允许既有基线，不能偷换为 Kronos。

## K2 任务对象

```json
{
  "id": "K2_KRONOS_PROSPECTIVE_SHADOW",
  "status": "planned_non_blocking",
  "required_for_release33": false,
  "activation_gate": "release33_fresh_btc_core_passed",
  "financial_admission": false
}
```

## K2A 工程接线

1. 新建独立协议和 trial 身份，例如 `kronos-path-shadow.v1`；旧 `path-shadow-24h.v1/v2` 原件保持不变。
2. 新增薄 `PathForecastRuntime` 接口和 Kronos 外部进程适配器；Kronos/Torch 继续在 D 盘隔离 GPU 环境，主 Hub Python 不安装 Torch。
3. 新增显式 opt-in 的 `kronos-shadow` Worker/heartbeat；默认 release33 与 `run-product.ps1` 不启动。Worker 失败不影响 API、研究 Worker 或 ETH。
4. 冻结源码 commit、模型、tokenizer、adapter、配置和输入 hash；每个 trial、cutoff、seed、路径数和资源预算均可审计。
5. GPU 不可用时记录 missed/failed，不静默 CPU 降级，不事后补跑回填。
6. 重启、续租和幂等门保证同一 slot 不重复预测、标签或结项。
7. K2 只写 shadow ledger，不写 Action，不自动 Promotion。

### K2A 完成门

- 新协议、新 trial、新 Worker 与隔离 Runtime 接通。
- 旧协议和旧 trial hash 不变。
- 同 slot 重启不重复；失败/迟到/缺失进入账本。
- 没有正式 Action、没有自动 Promotion、没有对主产品可用性的影响。

## K2B 真实前瞻窗口

首轮仅 BTC 24h，固定 28 天：

1. 每个应发布 slot 先登记，再以同一 cutoff、同一输入运行冻结基线与 Kronos。
2. 保存完整原始路径、有效/非法 OHLC、输入/模型身份、GPU/CPU/内存、排队和推理时延。
3. 路径数在隔离资源探测后预登记；16 路径只用于格式核验，不能作为尾部概率准入样本。
4. 迟到、失败、缺失、GPU 不可用和非法 OHLC 全部进入分母；不得删除失败、静默修复或事后回填。
5. 成熟标签后计算 CRPS、区间覆盖、方向、尾部漏报、有效覆盖率、迟到率与资源成本；报告可从 ledger 独立复算。
6. 结论仅可为 `retain`、`stop` 或 `insufficient`。ETH 是第二个显式 scope，另建版本和门；黄金、纳指、恒科不混入首轮。

### K2B 完成门

- 28 天窗口自然结束且所有 slot 有状态。
- 基线与 Kronos 按同输入配对，标签成熟且无时间穿越。
- 分布、尾部、覆盖和成本可复算，late/missed/failed 未被排除。
- 输出具名裁决及原因；仍不等于金融准入。

## 与主线的关系

- release33 的完成定义只有全新 BTC 11 项 core gate；K2 不阻塞。
- K2A 仅在 release33 BTC 通过并冻结后启动；K2B 需要真实时间积累，不拿历史回放冒充前瞻命中。
- 黄金、纳指、恒科在各自资产准入计划中复用分层架构与宏观事件层，不能仅把 BTC ticker 换名。
