# Decision Hub 核心决策价值验证（2026-09-15）

状态：`COMPLETE / STOP_PRODUCT_LAYER_INVESTMENT`

## 1. 要回答的问题

本轮只回答一个问题：当前 Decision Hub 在 BTC/USDT 上是否已经产生了可验证的方向判断价值，而不只是完成取证、审计和安全拒绝。

验证对象是仓库已经真实运行并冻结的 12 个历史事件案例，以及各自的 Fixed baseline 与 DSH 输出。不得重新提示模型、删掉失败样本、修改历史输出、把未校准概率写成已校准概率，或用新增界面代替结果验证。

## 2. 上一目标实际完成与未证明的内容

上一目标完成了：

- OKX 与 Binance 双交易所现货、资金费率、OI 及 OI 变化、基差、订单簿和小时历史取证；
- Evidence、PIT、来源独立性、充分度、Review 和 Artifact 主链；
- DSH 与 Decision Desk 的行动摘要、三个周期、八步过程和技术详情；
- 未校准、证据不足或 Review 未通过时的 fail-closed；
- 三次真实当前快照运行和自动化回归。

上一目标没有证明：

- 方向命中率；
- 概率校准；
- 相比简单基线有预测增益；
- 计入成本后有正收益；
- 减少 owner 人工时间。

所以 `CORE_VALUE_DELIVERY_EXECUTION_2026-09-14.md` 证明的是“可审计且不会伪装成交易许可”，不是“可以赚钱”或“能够给出稳定方向”。

## 3. 冻结输入

- Dev：`packs/crypto_macro/evaluations/r2r_dev_core_v3`，6 个案例；
- Holdout：`packs/crypto_macro/evaluations/r2r_holdout_core_v3`，6 个案例；
- 已冻结输出：
  - `data/decision-hub/evaluations/g1-c3-dev-20260913/comparison.json`；
  - `data/decision-hub/evaluations/g1-c3-holdout-20260913/comparison.json`；
- Outcome：从 Binance Vision 官方 BTCUSDT 1 分钟 K 线取得 cutoff、cutoff+30m、+24h、+72h 的已实现价格；只用于运行完成后的评分；
- 成本敏感度：主口径按每次方向行动 10 bps 往返摩擦，同时报告 0/10/25 bps 三档方向标签敏感度。

本轮不调用模型，不产生模型费用，不改变 canonical case 和 comparison 文件。

## 4. 必须输出的统计

- Fixed baseline 与 DSH 各自的 `long/short/neutral/no_trade` 数量；
- 方向覆盖率和弃权率；
- 已实现 30m/24h/72h 收益；
- 有方向判断时的命中率、平均有符号收益和扣除 10 bps 后收益；
- 简单事件后动量基线与 always-long 参照；
- 概率是否具备 Brier/校准评分资格；
- Dev/Holdout 分开结果；
- 数据缺失、时间错配和 hash 记录。

## 5. 预先冻结的判断门

### 可以继续产品层验证

只有同时满足：

1. Holdout 至少产生 3 个独立方向判断，不能用全量中性规避错误；
2. Holdout 方向命中率高于 always-long 和简单事件后动量中的较优者；
3. 扣除 10 bps 后的平均有符号收益为正；
4. 所有用于概率评分的输出均为 `calibrated`；
5. 无 PIT、引用或 outcome 时间错配。

这只是允许继续验证，不构成交易或盈利证明。12 个事件不足以支持上线。

### 停止产品层投入

满足任一项即停止：

- 方向覆盖率接近零，无法计算可靠命中率；
- 所有概率仍为 `uncalibrated`；
- Holdout 不优于简单基线；
- 结果依赖未来数据、缺失数据或不可复现来源；
- 需要通过修改历史输出或放宽评分规则才能通过。

## 6. 执行 Checklist

- [x] 校验两个 comparison 与 canonical manifest/case hash；
- [x] 获取并 hash Binance Vision outcome 文件；
- [x] 生成 12 案例 × 3 周期的已实现收益；
- [x] 评分 Fixed baseline、DSH、always-long 和简单事件后动量；
- [x] 分离 Dev 与 Holdout，检查方向覆盖和概率状态；
- [x] 写入原始 JSON 和本文结果表；
- [x] 运行脚本自检、相关评测测试和 `git diff --check`；
- [x] 给出继续或停止决定。

## 7. 实际结果

### 7.1 数据完整性

- 两个 canonical dataset 均由现有 loader 重新验证 case hash、manifest hash 和 PIT；12 个案例全部通过；
- 两个冻结 comparison 的 manifest hash 与各自 dataset 一致；
- 下载 48 个 Binance Vision 官方日档，共 69,120 根 BTCUSDT 1 分钟 K 线、3,432,238 bytes；
- 每个 zip 的本地 SHA-256 均与同 URL 的官方 `.CHECKSUM` 一致；
- outcome 覆盖从 `2024-04-03` 到 `2025-03-10`，36 个 cutoff/horizon 均成功找到不晚于目标时间 61 秒的 close；
- 评分原始文件：`data/decision-hub/evaluations/directional-value-audit-20260915/directional-value-audit.json`；SHA-256 为 `8b193cc4b8b1bba0a3c40f872bd76b65dceec8f682ee77a86a3badfaaa152321`。

### 7.2 方向覆盖

| Split | 系统 | 方向判断数/总数 | 弃权率 | 动作分布 |
| --- | --- | ---: | ---: | --- |
| Dev | Fixed baseline | 3/18 | 83.33% | long 3、neutral 6、no_trade 9 |
| Dev | DSH | **0/18** | **100%** | neutral 1、no_trade 17 |
| Holdout | Fixed baseline | 0/18 | 100% | neutral 9、no_trade 9 |
| Holdout | DSH | **1/18** | **94.44%** | long 1、neutral 1、no_trade 16 |
| 全部 | DSH | **1/36** | **97.22%** | long 1、neutral 2、no_trade 33 |

DSH 唯一的方向判断是 Holdout 的 `fomc_cut_50bp_20240918 / 30m long`，主观概率 0.56、状态 `uncalibrated`；实际 30 分钟收益为 `+1.3797%`。1 个样本即使命中，也无法估计稳定命中率或证明预测能力。

### 7.3 与简单参照比较

主统计按 10 bps 方向阈值和每次方向动作 10 bps 往返摩擦：

| Split | 系统 | 方向覆盖 | 方向命中率 | 平均有符号毛收益 | 扣 10 bps 后 |
| --- | --- | ---: | ---: | ---: | ---: |
| Dev | DSH | 0.00% | N/A | N/A | N/A |
| Dev | Always-long | 100% | 72.22% | +0.88897% | +0.78897% |
| Dev | 事件后动量符号 | 100% | 55.56% | +0.14686% | +0.04686% |
| Holdout | DSH | 5.56% | 100%（**n=1**） | +1.37971% | +1.27971% |
| Holdout | Always-long | 100% | 83.33%（n=18） | +2.08014% | +1.98014% |
| Holdout | 事件后动量符号 | 100% | 33.33%（n=18） | +0.08261% | -0.01739% |

Holdout 的已实现方向本身高度偏多：18 个 case×horizon 中，按 10 bps 阈值有 15 个上涨、3 个下跌。因此 always-long 的高命中率主要反映这个小样本的市场状态，不能当策略证明；DSH 只选择其中一个上涨样本，也没有足够覆盖率与它比较。

### 7.4 概率校准

- Fixed baseline：36/36 概率为 `uncalibrated`；
- DSH：36/36 概率为 `uncalibrated`；
- DSH 方向判断中 calibrated 数量为 `0/1`；
- 按预先规则，Brier score 为 `null`，不能把模型写出的 0.56、0.65 等数字当频率概率。

### 7.5 判断门

| 条件 | 结果 |
| --- | --- |
| Holdout 至少 3 个方向判断 | **失败：只有 1 个** |
| Holdout 命中率高于简单参照 | 表面通过，但 n=1，无统计意义，且受上一条阻断 |
| Holdout 扣 10 bps 后平均有符号收益为正 | 单样本为正，受覆盖门阻断 |
| 所有方向概率 calibrated | **失败：0/1** |
| manifest、case、outcome 完整性 | 通过 |

### 7.6 与最新构建的交叉证据

冻结历史 comparison 生成于 2026-09-13。2026-09-14 最新构建的三次同题真实当前快照进一步确认了当前产品行为：Run 1 为 `do_not_trade`，Run 2 为 `unavailable`，Run 3 为 `do_not_trade`，其中 Run 3 的 30m/24h/72h 全部为 `no_trade/uncalibrated`。当前实现新增的是更严格的双 venue、OI delta、Review 和未校准 Gate，没有新增任何历史训练或概率校准器，因此没有证据表明最新构建的方向覆盖高于本次冻结历史评分。

### 7.7 可复现命令

```powershell
uv run python -m tools.research_evaluation.audit_directional_value
uv run ruff check tools/research_evaluation/audit_directional_value.py
uv run pyright tools/research_evaluation/audit_directional_value.py
uv run pytest tests/evals/test_research_dataset.py tests/evals/test_research_comparison.py -q
git diff --check
```

第二次从本地缓存完整复跑用时 1.591 秒，统计断言通过；Ruff 通过、Pyright 0 errors、相关测试 `7 passed`、`git diff --check` 通过。本轮没有调用模型。

## 8. 产品决定

**决定：`STOP_PRODUCT_LAYER_INVESTMENT`。**

当前证据不能支持“Decision Hub 已经能给方向决策”：DSH 在 36 个冻结判断中只给出 1 个方向，覆盖率 2.78%，其余均弃权；唯一方向虽然事后命中，但概率未校准、样本量为 1，不能与简单基线公平比较。上一目标的安全和审计改造有效，方向价值没有成立。

因此停止继续改 DSH 页面、角色、PPT、调度和自动交易。可以保留双交易所 Provider、Evidence/PIT、Review、Gate、账本和查询投影作为基础组件。只有先建立带真实 outcome 的大样本 walk-forward 校准器，并在未看 Holdout 的情况下产生足够方向覆盖，才允许重新打开产品层开发。

## 9. 上一目标改造的验证证据

| 改造 | 已有验证 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| 恢复 OI delta 和双 venue Gate；新增 Binance adapter | FactPack、Binance、Fact Store、API 等核心 Python `70 passed, 2 skipped`；真实 Run 1 因只有 OKX 被 hard Gate 拒绝，Run 3 使用 OKX+Binance 达到 hard 100% | 数据要求没有再被单一来源伪装成满足 | 数据与方向之间存在预测关系 |
| 未校准和 bounded Review fail-closed | commit/query 测试；三次真实 Run 均 `trade_authorized=false` | 不会把研究报告误写成自动交易许可 | 能给出有效 long/short |
| DSH 行动摘要、三个周期、八步过程、折叠技术错误 | DSH `87 passed + build`；真实页面和同 Run API 截图回读 | 普通用户和开发人员能看到结论、证据、缺口与过程 | 用户会因此获得经济收益 |
| Decision Desk 用户/技术视图分离 | Decision Desk `12 passed + build`；最终 Run 页面回读 | 交付信息可读，原始错误没有隐藏 | 结论准确或优于直接模型 |
| 三次同题真实端到端 | Run 1 hard 失败、Run 2 synthesis 失败、Run 3 仅研究成功，均保留 | 主链失败能够暴露并安全收口；最终链路可运行 | 稳定性达到生产门；三次中有一次合成失败 |
| 12 案例 Fixed/DSH G1 对照 | Dev/Holdout 均 6 案例，PIT 违规和未验真 Evidence 为 0；两边 quality success 0/6、manual rework 6/6 | DSH 增加了可审计证据，没有破坏 PIT | 相比 baseline 提高可用性；G1 实际为 `retain_baseline` |

所以，上一目标投入并非完全没有验证：它修复并验证了安全、证据和可读性；它没有越过“方向价值”这一关。本轮补上的 outcome 评分给出了明确停止依据。

## 10. 根因与禁止复发约束

### 10.1 根因

1. **目标定义错位**：开发目标把“证据可审计、失败时不误导”当成交付终点，没有把“能否产生有效方向并优于简单基线”设为第一验收门。
2. **成功路径在结构上不存在**：Gate 规定只有 `calibrated` 才能放行方向，但系统只有模型填写的 `subjective_probability`，没有训练、校准、样本外验证或从 `uncalibrated` 升级的组件。因此继续运行只会稳定得到 `no_trade/research_only`。
3. **历史评测没有 outcome**：12 个 canonical case 的 `outcome_available_at=null`、`outcome_labels=[]`。旧 G1 能检查 Evidence、PIT、引用和返工，不能计算方向准确率、Brier 或净收益。
4. **代理指标替代产品指标**：hard coverage、引用率、测试数量、页面完整度和 Trace 可读性持续改善，但这些指标不证明方向正确，也不证明节省人工时间。
5. **baseline 回答了错误的问题**：Fixed baseline 与 DSH 比较的是证据覆盖和人工返工，两者 quality success 都是 0；没有在开发前加入 always-long、always-neutral、简单动量及已实现收益，所以无法及早发现“系统几乎全部弃权”。
6. **实现顺序颠倒**：先扩展 Provider、合同、Gate 和两个 UI，最后才补 outcome scorer。正确顺序应先冻结 outcome、baseline 和停止门，再决定是否值得写产品代码。

### 10.2 明确结论

当前问题不是一个 UI bug，也不能通过修改 prompt 或再增加数据字段解决。现有项目是可审计研究基础设施；它不是已经成立的交易决策产品。继续沿当前结构增加角色、页面、调度或更多 Agent，只会放大成本，不会自动产生校准方向。

### 10.3 后续任何重新开发必须遵守

1. 在写模型、Agent 或 UI 前，先准备含真实 outcome 的时间序列数据和可独立运行的 scorer；缺一项就不开工。
2. Dev/Holdout 按时间冻结；所有门槛在读取 Holdout 结果前写入同一实施文档，失败后不得调整门槛、删案例或重新挑时间段。
3. 第一版只做信号内核：方向、概率校准、成本、失效条件和 outcome；不得同时开发 DSH 页面、角色、PPT、调度或自动交易。
4. 必须同时比较 always-long、always-neutral、简单动量和现有 Decision Hub；只增加 Evidence 数量不能算改善。
5. 必须报告方向覆盖率。高命中率但只有极少方向判断视为样本不足，不能通过。
6. 概率来自可复算校准器；LLM 只解释证据，不能生成用于评分或仓位的概率。
7. 先通过样本外方向、校准和成本门，再接回 Evidence/Review 和界面。任一核心门失败，立即停止当轮，不用外围工作补交付感。
8. 产品层重新打开前，验收报告必须能用一页回答：产生了多少方向、命中多少、相对哪个 baseline 改善、扣成本后如何、置信区间多宽、哪里会失效。

若产品定位保持“研究助理”，当前基础设施可以继续保留，但必须删除交易决策叙事。若定位要求给出方向，下一项工作必须是独立的可校准信号内核验证，不能继续修当前 UI 来假装接近目标。
