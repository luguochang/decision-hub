# 多空持仓信号与有方向概率改造方案（交 Codex 执行）

日期：2026-11-01。作者：Claude（方案与复核），执行：Codex。
来源：会话 50c76024 中 10-31 用户两次要求（大跌复盘、"概率多空持平、建议太保守、多空持仓数据是公开的"），以及同目录 10-31 的 `AUTONOMOUS_RISK_DESIGN`、`AUTONOMOUS_RISK_IMPLEMENTATION`、`CORE_PROBABILITY_VALIDATION`。本文件不替代它们的约束，只补上它们没写的部分：用哪些数据、怎么算出有方向的概率、怎么映射成操作、怎么验证、怎么接进现有代码。

执行前必须先读本文件第 9 节（工作规则）和第 10 节（日志格式）。

---

## 1. 用户要的是什么

1. 每次判断要给出**偏多还是偏空**的概率，而且这个概率要来自当时的市场数据，不能总是等于历史基准率。
2. 建议不能永远是"持有/不变"。保护性操作（降杠杆、止损、减仓）在风险升高时要主动提出；方向性操作（开多、开空）在有验证过的优势时要提出。
3. 必须用上公开的衍生品持仓数据：持仓量（OI）、多空账户比、大户多空比、主动买卖比、资金费率、基差、爆仓、期权隐含波动率。用户的判断是"双向收割"就发生在这些数据极端的时候。
4. 方案要评估技术、数据、业务三方面是否可行，并且落到本项目现有代码上。
5. 写给 Codex 的步骤要具体到文件、接口、测试命令和验收标准；Codex 每一步写改动日志，Claude 复核。

## 2. 为什么现在多空概率总是持平（已查证）

| 原因 | 证据 |
|---|---|
| 提示词要求"从 90 天基准率出发，没有引用证据就保持基准率" | `packages/runtime_adapters/dsh_runtime/perpetual_profile.py:276-286` |
| 研究输入里没有任何经过验证的条件信号，模型找不到可以引用的调整依据，于是每次都保持基准率 | `CORE_PROBABILITY_VALIDATION_2026-10-31.md` 第"数值来源"节：BTC/ETH 最终 probability_source 的唯一 evidence_ref 是 `direction_base_rate` |
| 现有"拥挤度"适配器其实只读 OKX 盘口深度，没有 OI、多空比、主动买卖、爆仓 | `packages/provider_adapters/market/crowding.py`（只有 `_depths` / `_levels`） |
| 风险哨兵只看 30 分钟价格涨跌幅 | `packages/kernel/decision_hub_kernel/ports/risk_monitor.py:41` `RiskMonitorInput` 只有 `close_return` |
| 基准率 35.7/31.8/32.5 本身接近三等分，所以"保持基准率"在界面上看起来就是多空持平 | 10-29 BTC 报告 |

结论：问题不在模型"性格保守"。系统没有一个能把市场数据变成条件概率的确定性模块，大模型也没有可引用的量化依据。改提示词让模型"大胆一点"只会产生没有根据的数字，不能这样修。

## 3. 可行性评估

### 3.1 技术可行性：数据能拿到（2026-11-01 本机实测，全部 HTTP 200，无需 API key）

| 数据 | 端点 | 粒度 | 免费历史深度 |
|---|---|---|---|
| Binance OI 历史 | `fapi.binance.com/futures/data/openInterestHist` | 5m–1d | 仅最近 30 天 |
| Binance 全体账户多空比 | `/futures/data/globalLongShortAccountRatio` | 5m–1d | 30 天 |
| Binance 大户持仓多空比 | `/futures/data/topLongShortPositionRatio` | 5m–1d | 30 天 |
| Binance 主动买卖量比 | `/futures/data/takerlongshortRatio` | 5m–1d | 30 天 |
| Binance 标记价/指数价/资金费率 | `/fapi/v1/premiumIndex`，历史 `/fapi/v1/fundingRate` | 实时 / 每 8h | 资金费率全量历史 |
| **Binance Vision 日度 metrics 文件** | `data.binance.vision/data/futures/um/daily/metrics/{SYM}/{SYM}-metrics-{date}.zip` | 5m | **2022-01-01 起可下载（实测 200）**，列：`sum_open_interest, sum_open_interest_value, count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio` |
| Binance Vision K 线 | `data.binance.vision/data/futures/um/daily/klines/...` | 1m 起 | 2020 起 |
| OKX OI | `okx.com/api/v5/public/open-interest` | 实时 | 实时 |
| OKX 多空账户比、主动买卖量 | `/api/v5/rubik/stat/contracts/long-short-account-ratio`、`/rubik/stat/taker-volume` | 5m 起 | 有限（约数天到数月，按粒度） |
| OKX 爆仓单 | `/api/v5/public/liquidation-orders` | 逐笔 | 仅最近 |
| Bybit 账户多空比 | `api.bybit.com/v5/market/account-ratio` | 5min 起 | 有限 |
| Deribit DVOL（BTC/ETH 30 天隐含波动率指数） | `deribit.com/api/v2/public/get_volatility_index_data` | 1h 起 | 2021 起 |

结论：
- 训练和回测用 Binance Vision metrics + K 线 + 资金费率 + Deribit DVOL，有约 4 年 5 分钟级历史，足够做滚动验证。
- 实时监控用上表的实时端点，多交易所交叉核对。
- **爆仓数据没有免费历史**，第一版只用于实时哨兵，不进概率模型。用户 11-01 决定不接入付费源（第 11 节）。

### 3.2 业务可行性：公开数据能不能带来优势

- 这些数据人人可见，单个指标的"看到就赚钱"优势大多已被套利掉。可能还剩的优势在**极端状态**：资金费率、多空比、OI 同时处在历史高分位时，后续更容易出现反向挤压（多头爆仓或空头爆仓）。这就是用户说的"双向收割"。
- 极端状态出现的频率低（预计 5%–15% 的时间），所以模型大部分时候仍会接近基准率，**只在极端状态明显偏离**。这是正确行为，不是保守。界面要显示"当前拥挤度分位"，让用户看到为什么这次偏离、为什么那次不偏离。
- 风险预警（波动会不会放大、哪一边更容易被挤）比方向预测更容易做准。拥挤度高时，即使方向不确定，也可以确定"两边都可能剧烈波动，应降杠杆"。
- **结论**：方向优势是否存在，必须在第 6 节的回测里先证明，不能预设。风险等级部分在回测前就可以上线，因为它只给保护性建议。

### 3.3 方案可行性：能不能接进现有项目

- 数据采集：现有 `packages/provider_adapters/market/` 已有 OKX/Binance 适配器和统一的错误码、原件捕获（`CapturingAsyncClient`），新适配器按同一模式写。
- 历史数据：现有 `packages/provider_adapters/historical/binance_vision.py` 已在下载 Binance Vision，可以加 metrics 数据集。
- 概率注入点：现有 `perpetual_base_rate_source.py` → `perpetual_research_binding.py` → `perpetual_profile.py` 这条链把"冻结基准率"作为研究材料交给模型。新条件概率按同样方式作为第二份冻结材料注入，不需要重做研究流程。
- 展示：10-31 已做的 `perpetual_forecast_basis.py` + `PerpetualAdvicePanel.tsx` 已能并列展示基准率和模型概率，加一列"条件模型概率"即可。
- 哨兵：现有 `risk_monitor.py` 规则只认 `close_return`，需要新增一个版本化的规则族，不改旧规则含义。
- 主要风险：仓库禁止改旧报告哈希和旧原件读取，所有新字段必须是新版本、可选，旧报告读取不受影响（见第 9 节）。

## 4. 总体设计

```
层1 数据采集   多交易所持仓/资金费/基差/爆仓/DVOL，5 分钟一次，存原件和时间戳
      │
层2 特征计算   纯函数，算分位数、z 分数、OI-价格背离、拥挤度评分
      │
层3 条件概率   确定性模型：给定当前特征 → 4h 涨/横/跌概率 + 尾部概率
      │        （先离线回测证明有用，才允许进入研究）
      ├──────────► 层5 风险哨兵：拥挤度/尾部风险超阈值 → 告警 + 保护性建议（不依赖层3回测结果）
      │
层4 研究与决策 大模型读层3结果作为冻结证据，负责解释和找反证；
               偏离层3超过 10 个百分点必须引用新证据
      │
      ▼
   操作映射     概率 + 尾部风险 + 持仓 → 开多/开空/减仓/降杠杆/不动，每条带有效期和退出条件
```

分工原则：**数字由程序算，大模型解释和挑错**。这样概率可以复算、可以回测，也不会每次都退回基准率。

## 5. 特征定义（层2，全部是纯函数）

所有特征在决策时刻 `t` 只能使用 `t` 之前已经公开的数据。Binance Vision 5m 行的 `create_time` 是该 5 分钟结束时刻，计算时只取 `create_time <= t`。分位数和 z 分数一律用**滚动 90 天窗口**，窗口只含 `t` 之前的数据。

| 代号 | 名称 | 公式 | 含义 |
|---|---|---|---|
| F1 | OI 变化率 | `oi_value(t) / oi_value(t-4h) - 1`，另算 24h | 杠杆在加还是在减 |
| F2 | 价格-OI 四象限 | 4h 价格涨跌符号 × F1 符号 | 涨+OI涨=新多入场；跌+OI涨=新空入场或多头死扛；涨+OI跌=空头回补；跌+OI跌=多头平仓 |
| F3 | 资金费率 z 分数 | `(funding(t) - mean_90d) / std_90d`，Binance 与 OKX 各算，取均值 | 多头还是空头在付费，是否极端 |
| F4 | 全体多空账户比分位 | `globalLongShortAccountRatio` 在 90 天中的分位 | 散户朝哪边挤 |
| F5 | 大户多空比分位 | `topLongShortPositionRatio` 分位 | 大户朝哪边 |
| F6 | 散户-大户背离 | `F4 - F5` | 散户和大户方向相反时，散户一方更容易被收割 |
| F7 | 主动买卖比 | `takerlongshortRatio` 的 1h 均值，取对数 | 短线主动资金方向 |
| F8 | 基差 | `(mark - index) / index`，以及与 90 天均值之差 | 合约相对现货的溢价 |
| F9 | DVOL 水平与变化 | DVOL 分位；`DVOL(t) - DVOL(t-24h)` | 期权市场预期的波动 |
| F10 | 已实现波动率 | 1h 收益率的 24h 标准差，年化 | 与 F9 比较：隐含远高于已实现=市场在为大事件付费 |
| F11 | 爆仓（仅实时） | 最近 1h 多单/空单爆仓名义金额 | 只用于哨兵，不进模型 |

### 5.1 拥挤度评分（用于哨兵和展示）

```
long_crowding  = mean( pct(F3), F4, pct(F1_24h) )   当 F2 为"跌+OI涨"时再 +0.1
short_crowding = mean( 1-pct(F3), 1-F4, pct(F1_24h) ) 当 F2 为"涨+OI涨"时再 +0.1
```
`pct(x)` 为 90 天分位（0–1）。结果截断到 [0, 1]。阈值初值 0.85 为"高"、0.95 为"极高"，这些只是**候选值**，要在第 6 节回测里定，回测后写入配置文件，不硬编码。

### 5.2 需要能识别的四种状态（回测必须分别统计）

| 状态 | 判定（候选） | 历史上的典型结果 | 对应建议方向 |
|---|---|---|---|
| A 多头拥挤 | long_crowding ≥ 0.85，资金费率为正 | 下跌时容易引发多头连环爆仓。10-12 至 10-27 BTC 跌 4.3% 而 OI 涨 9.2%、资金费仍为正，属于此类 | 持多：降杠杆、收紧止损；空仓：条件开空候选 |
| B 空头拥挤 | short_crowding ≥ 0.85，资金费率为负 | 上涨时容易引发空头挤压 | A 的镜像 |
| C 去杠杆完成 | 24h OI 下降 ≥ 8%，同期爆仓放大，资金费转负 | 短期反弹概率上升 | 空仓：条件开多候选；持空：止盈 |
| D 波动压缩 | DVOL 与已实现波动都在 20 分位以下，OI 在 80 分位以上 | 方向不明，但大波动概率上升 | 两边都降杠杆，不给方向建议 |

状态 D 很重要：它对应用户说的"下次可能暴涨也可能暴跌"。这时正确的建议是"风险高、方向不明、降杠杆"，而不是猜一个方向。

## 6. 条件概率模型与验证（层3）

### 6.1 预测目标

与现有基准率完全同口径，否则无法对比：
- 方向：4h 后收盘相对起点的收益 `r`，分类 up（`r > θ`）/ neutral（`|r| ≤ θ`）/ down（`r < -θ`）。`θ` 取研究 target 的 `threshold_fraction`，与 `perpetual_base_rate_source.py` 使用的同一值。
- 尾部（新增）：4h 内最高价涨幅 ≥ 3% 的概率、最低价跌幅 ≥ 3% 的概率，分开估计。3% 是候选值，同时输出 5%。尾部用路径最高/最低，不只看收盘。

### 6.2 模型（按顺序做，先简单后复杂）

**M0 基准**：现有 90 天 Laplace 频率 `(count+1)/(n+3)`。所有模型都要和它比。

**M1 分桶频率**：
1. 每个特征按 90 天分位切成 5 档。
2. 只用三个最可能有用的组合维度：F3（资金费 z）、F4（散户多空比分位）、F2（四象限）。共 5×5×4=100 格，太多会过拟合，所以先单维、再两两组合，最多用到两维。
3. 每格算历史上 up/neutral/down 的频率，向基准率收缩：
   `p = (n_cell × p_cell + k × p_base) / (n_cell + k)`，`k` 初值 50，回测里选。
4. 样本数 `n_cell < 30` 的格直接用基准率，并标 `insufficient_sample`。

**M2 多项逻辑回归**（只有 M1 证明有效后才做）：输入 F1–F10 标准化值，L2 正则，输出三分类概率；再做一次温度缩放校准。

### 6.3 回测方法（walk-forward，防止偷看未来）

- 数据：BTCUSDT、ETHUSDT，2022-01-01 至最近一个完整月。
- 每个月月初用此前全部数据（至少 12 个月）训练，预测这个月；下个月再滚动。训练时的分位和 z 分数也只用训练集内的数据。
- 预测点：每 4 小时一个（00/04/08/12/16/20 UTC），相邻样本不重叠。
- 2026-10 整月**留作最终留出集**：调参期间一律不看，只在最后跑一次，结果原样记录。
- 指标：log-loss、Brier、各状态（第 5.2 节 A–D）的条件命中率、可靠性曲线（10 档），尾部概率的 Brier。

### 6.4 验收门槛（写进代码配置，不由模型或执行者临场改）

M1 或 M2 只有**同时**满足以下条件，才允许把它的概率作为研究证据注入：
1. 全部 walk-forward 月份合计，log-loss 比 M0 低，且 1000 次按月块 bootstrap 的 95% 区间上界 < 0。
2. BTC 和 ETH 分别满足第 1 条。
3. 至少 60% 的月份 log-loss 优于 M0。
4. 可靠性曲线每档实际频率与预测频率之差 ≤ 8 个百分点（样本 ≥ 50 的档）。
5. 留出集 2026-10 上 log-loss 不劣于 M0（允许持平）。

不满足时：模型状态记为 `not_qualified`，研究里只把它当"参考信息"展示，概率仍以基准率为准，界面明确写"条件模型未通过验证"。**不得为了通过而反复换特征挑留出集**。每次实验都在实验登记表里留一行（第 10.2 节），失败也记。

### 6.5 第 5.2 节四种状态的单独检查

即使 M1 整体没通过，也要输出每种状态出现次数和之后 4h/24h 的实际分布、尾部频率。如果某状态（例如 A 多头拥挤）样本 ≥ 100，且其后"跌超 3%"的频率比基准高出 ≥ 1.5 倍、bootstrap 区间不含 1，就允许哨兵在该状态下发**保护性**提醒（不发开仓建议）。

## 7. 操作映射（解决"永远持有"）

记 `edge = p_up - p_down`（条件模型概率，未通过验证时为基准率），`tail_dn` / `tail_up` 为 4h 跌/涨超 3% 的概率，`tail_base` 为基准尾部频率。

| 条件 | 空仓 | 持多 | 持空 |
|---|---|---|---|
| 模型已通过验证且 `edge ≥ +0.10` | 条件开多候选（附入场、止损、有效期） | 持有，给出退出条件 | 减仓或平仓候选 |
| 模型已通过验证且 `edge ≤ -0.10` | 条件开空候选 | 减仓或平仓候选 | 持有，给出退出条件 |
| `tail_dn ≥ 1.5 × tail_base` 或状态 A | 不开多 | **降杠杆/收紧止损**（保护性，不需要模型通过） | 持有 |
| `tail_up ≥ 1.5 × tail_base` 或状态 B | 不开空 | 持有 | **降杠杆/收紧止损** |
| 状态 D | 不开仓 | 降杠杆 | 降杠杆 |
| 以上都不满足 | 不动，**说明理由**："拥挤度 xx 分位，正常；条件概率与基准差 x 个百分点，无优势" | 同左 | 同左 |

规则：
- `0.10` 是候选阈值，回测时同时报告 0.05/0.10/0.15 三档下的纸交易结果（含手续费 0.05%、资金费、滑点 0.02%），选定后写配置。
- 方向性建议（开仓）要求模型已通过验证；保护性建议（降杠杆、止损）只要求第 6.5 节的状态检查通过。两类门槛分开，这就是"该保守的地方保守，不该保守的地方不保守"。
- "不动"也要输出理由和下一次复查时间，不能只写 no_change。
- 每条建议都带：有效期（默认 4h）、什么情况下作废、和基准率相比偏离多少。
- 不自动下单，不做反手。持仓先平再判断是否反向开仓（与现有 `ActionCandidateSetV2` 规则一致，见 `perpetual_profile.py` 第 289 行附近）。

## 8. 任务分解（Codex 按顺序做）

执行顺序（11-01 用户决定后修订）：**T0 先做** → T1、T2、T9 数据采集并行 → T3 → T4（决定方向优势是否存在）→ T5、T8 → T6 → T7。T6 的保护性告警只依赖 T3 和 T4 的第 6.5 节状态检查，不等 M1 通过。T0–T9 的说明在本节下文，T0/T8/T9 写在 T7 之后。

不使用任何付费数据源（用户 11-01 决定）。

通用命令（以下 `<id>` 用任务号加 UTC 时间戳，例如 `T1-20261101T1200Z`，每次新建目录）：
```
# 工作目录
cd E:/project/study/codex/dsh/decision-hub
# 单测
.venv/Scripts/python.exe -X utf8 -m pytest -p no:cacheprovider --basetemp D:/DH-pos-<id>/tmp <测试文件> 2>&1 | tee D:/DH-pos-<id>/pytest.log
# 格式与静态检查（只查本任务新建/修改的文件）
.venv/Scripts/python.exe -m ruff check <文件...>
# 类型检查：用 DEVELOPMENT_WORKFLOW.md 中现有的命令，只对本任务文件
```

### T1 实时持仓数据适配器

- 新建 `packages/provider_adapters/market/positioning.py`（≤400 行）。
- 照 `crowding.py` 的写法：`CapturingAsyncClient`、同样的错误码映射（timeout→`provider_timeout`，429→`provider_rate_limited` 等）。
- 新建端口 `packages/kernel/decision_hub_kernel/ports/positioning.py`，定义冻结的 Pydantic 模型 `PositioningSnapshotV1`：
  - `symbol`、`venue`（binance/okx/bybit/deribit）、`metric`（oi_value / global_ls_ratio / top_ls_ratio / taker_ratio / funding_rate / basis / dvol / liq_long_1h / liq_short_1h）、`value: Decimal`
  - `period_end`（数据所属时段结束时刻）、`fetched_at`（系统收到时刻）、`source_url`、`raw_sha256`
  - 任何一个交易所失败，只让该条 `status="error"` 并带错误码，其余照常返回，不整体失败。
- 端点见第 3.1 节表。Bybit 与 OKX 的多空比是"账户比例"，换算成 `long/short` 后再存，原值另存。
- 测试 `tests/market_risk/test_positioning_adapter.py`：用录制好的 JSON（放 `tests/fixtures/positioning/`，从第 3.1 节实测响应保存）覆盖：正常解析、某交易所 500、429、超时、字段缺失、数值为字符串 "NaN"、时间戳毫秒转换。**测试不得联网**。
- 验收：测试全过；Ruff 0；手动跑一次真实请求（只读，不入库），把输出存 `D:/DH-pos-<id>/live-sample.json`。

### T2 历史数据集构建

- 在 `packages/provider_adapters/historical/binance_vision.py` 旁新建 `binance_vision_metrics.py`，下载 `futures/um/daily/metrics`、`futures/um/daily/klines`（1m 和 1h）、资金费率历史；新建 `deribit_dvol.py` 下载 DVOL 1h。
- 输出到 **D 盘** `D:/DH-positioning-data/<symbol>/`，每天一个文件，另写 `manifest.json`（文件名、SHA256、行数、下载时间、源 URL）。不写入 E 盘，不写入任何正式库。
- 已存在且 SHA 一致的文件跳过，不重复下载。Binance Vision 每个 zip 有对应 `.CHECKSUM` 文件，下载后必须校验。
- 范围：BTCUSDT、ETHUSDT，2022-01-01 至昨天。
- 测试 `tests/historical/test_binance_vision_metrics.py`：用本地小 zip fixture 测解析、校验和不符拒绝、缺天记录为缺口不补造、重复运行不重下。
- 验收：测试全过；真实下载完成后在日志记录总天数、缺失天列表、总大小。缺口原样记录，不插值。

### T3 特征计算（纯函数）

- 新建 `packages/kernel/decision_hub_kernel/application/positioning_features.py`。
- 入口 `compute_features(rows: Sequence[PositioningRow], *, as_of: datetime) -> PositioningFeaturesV1`。只读 `period_end <= as_of` 的行；函数内部第一步就过滤并断言，防止未来数据泄漏。
- 输出 F1–F10、`long_crowding`、`short_crowding`、`state`（A/B/C/D/none）、每个特征的 `sample_count`、`missing` 列表。
- 数据不够 90 天、某指标缺失：该特征为 `None`，`state` 只在所需特征都在时判定，否则为 `unknown`。不得用 0 填。
- 阈值放 `packs/positioning/thresholds.v1.yaml`（新建），代码只读配置。
- 测试 `tests/market_risk/test_positioning_features.py`，必须包括：
  1. **防泄漏**：在 `as_of` 之后插入极端数据，结果不变。
  2. **镜像对称**：把价格收益、资金费率、主动买卖比取反，多空比取倒数，`long_crowding` 与 `short_crowding` 互换，状态 A↔B 互换。
  3. 缺失和不足 90 天返回 `None`/`unknown`。
  4. 手算的小样本（10 行）与函数结果逐项相等。
- 验收：测试全过；对 2026-10-01 至 10-31 每 4h 计算一次，输出 CSV 到 `D:/DH-pos-<id>/oct-features.csv`，并在日志里写出 10-12 至 10-29 期间 BTC 的 `long_crowding` 最大值和出现时刻（这是第 6.5 节回放的预检）。

### T4 条件概率模型与回测（决定性步骤）

- 新建 `research/positioning_model/`（离线研究目录，不是产品代码）：`dataset.py`（从 T2 数据和 T3 函数生成样本）、`models.py`（M0/M1/M2）、`walk_forward.py`、`report.py`。
- 产品侧只新建一个小模块 `packages/kernel/decision_hub_kernel/application/positioning_probability.py`：读取冻结的模型参数文件（JSON，含版本号、训练截止日、SHA256、验收结果），给定特征输出概率。产品代码里**不训练**。
- 回测步骤按第 6.3 节。输出：
  - `D:/DH-pos-<id>/walk_forward.json`：每月每个模型的 log-loss、Brier、样本数。
  - `D:/DH-pos-<id>/states.json`：第 6.5 节四种状态统计。
  - `D:/DH-pos-<id>/paper.json`：第 7 节三档阈值的纸交易结果（含费用）。
  - `D:/DH-pos-<id>/REPORT.md`：结论，是否通过第 6.4 节门槛，逐条写实际数值。
- 测试 `tests/research/test_positioning_walk_forward.py`：用合成数据（人为让"资金费 z > 2 后下跌概率 60%"）验证模型能学到；用纯随机数据验证模型**不会**通过门槛（防止流程本身有泄漏）。
- 验收：两个合成测试都按预期；真实回测跑完；报告写清通过或没通过。**没通过也是有效结果**，照实写，然后 T5 只注入"参考信息"。

### T5 接入研究流程

- 在 `perpetual_base_rate_source.py` 同目录新建 `perpetual_positioning_source.py`：研究开始时读取实时快照（T1）→ 计算特征（T3）→ 计算概率（T4 冻结参数），作为一份新的冻结研究材料 `positioning_conditional`，结构：
  `status`（qualified / not_qualified / unavailable）、`model_version`、`probabilities{up,neutral,down}`、`tail{up_3pct,down_3pct,up_5pct,down_5pct}`、`features`、`state`、`crowding{long,short}`、`as_of`、`input_sha256`。
- 在 `perpetual_research_binding.py` 中按 `direction_base_rate` 的同样方式挂入。新材料是**可选**字段，旧报告没有该字段时读取逻辑不变。
- 修改 `perpetual_profile.py:276-286` 的提示词段落，**新增**一段（不删旧段，旧版本 recipe 要能复现）：
  - `status=qualified` 时："从 positioning_conditional.probabilities 出发，而不是从 window_90d 出发。偏离超过 10 个百分点时，必须逐项引用新证据。"
  - `status=not_qualified` 时："仍从基准率出发；positioning_conditional 可作为证据引用，但必须说明它未通过验证。"
  - 两种情况都要求模型在 basis 里写出当前拥挤度分位和状态。
- 这会产生新的 recipe 版本。按 `AGENTS.md` 的规定：研究空闲 → 官方停止 → 修改 → 源码重启，不在运行中热改。
- 测试：扩展现有 `tests/decision_product/` 中基准率相关测试（先 `rg direction_base_rate tests/` 找到对应文件），新增：qualified/not_qualified/unavailable 三种绑定；旧报告（无新字段）读取通过；材料哈希被篡改时拒绝。
- 验收：测试全过；10-31 已有的 BTC/ETH 旧报告用 owner API 读取仍成功（13 项检查不变）。

### T6 风险哨兵：拥挤度规则族

- 不改 `RiskMonitorInput` 和现有 `spot.move30.v1` 规则。新建 `packages/kernel/decision_hub_kernel/ports/positioning_risk.py`，定义 `PositioningRiskConditionV1`（规则 id、阈值、确认次数、冷却时间、过期秒数）和 `PositioningRiskInputV1`（T3 的特征 + 数据新鲜度）。
- 新建 `packages/kernel/decision_hub_kernel/application/positioning_risk_monitor.py`，复用 `risk_monitor.py` 的 plan/claim/complete 和 Outbox 去重机制（读该文件第 86–330 行，照搬模式，不复制粘贴整段）。
- 规则（阈值从 `packs/positioning/thresholds.v1.yaml` 读）：
  1. `crowding.long.high`：long_crowding ≥ 阈值，连续 2 次（10 分钟）确认。
  2. `crowding.short.high`：镜像。
  3. `deleverage.done`：状态 C。
  4. `vol.compression`：状态 D。
  5. `liquidation.spike`：1h 单边爆仓 ≥ 过去 30 天 1h 爆仓的 99 分位（实时累积，冷启动不足 7 天时不触发）。
  6. `data.stale`：任一主交易所数据超过 15 分钟未更新，单独告警，**不能**静默当作"无风险"。
- 同一规则触发后进入冷却（初值 2h），状态退出（低于阈值 0.05 以下）后才允许再次触发，避免每 5 分钟重复提醒。方向反转（A 变 B）立即作为新事件。
- 每条告警带第 7 节对应的保护性建议文字和"为什么"（哪几个特征、各自分位）。
- 接入 `apps/hub_worker/main.py` 中现有 realtime scheduler（第 232 行 `risk_enhancement_tick` 附近），5 分钟一次。
- 测试 `tests/market_risk/test_positioning_risk_monitor.py`：阈值附近来回震荡不重复告警；A→B 反转触发新事件；数据过期发 stale 而不是 none；镜像输入产生镜像告警；Outbox 去重。
- 验收：测试全过；启动开发实例后观察 1 小时，日志里每 5 分钟有一次评估记录，`data.stale` 能在断网 15 分钟后触发（可用拦截 fixture 模拟，不用真断网）。

### T7 回放验收 + 前端展示

**回放**（核心验收，证明"事前能不能看到"）：
- 新建 `research/positioning_model/replay.py`，按时间逐 5 分钟推进，只用当时已有的数据，跑 T3 特征 + T6 规则 + T5 概率。
- 回放窗口（全部要跑）：
  1. 2026-10-12 至 2026-10-31（本次大跌，Binance 被盗 10-29 20:05 UTC）
  2. 2025-10-10 前后 ±5 天（大规模爆仓）
  3. 选 2 段 2024–2026 的大涨窗口（空头被挤），由 T4 状态统计里挑 `crowding.short.high` 后涨幅最大的两次
  4. 选 2 段平静窗口（4h 波动都 < 1.5% 连续 7 天），用来量误报
- 每个窗口输出：首次告警时刻、相对大波动开始的提前量、告警次数、误报次数、当时的概率和建议。写入 `D:/DH-pos-<id>/replay/<window>.json` 和汇总 `REPLAY.md`。
- **如实记录**：被盗事件本身不可预测；要看的是 10-29 20:05 之前风险等级有没有已经升到"高"，以及事发后几分钟内是否告警。没有提前告警就写没有。

**前端**：
- 在 `perpetual_forecast_basis.py` 的投影中加可选字段 `positioning`（条件概率、状态、拥挤度、是否通过验证）。
- `apps/decision-desk/src/api/perpetualAdvice.ts` 的 Zod schema 加对应可选字段。
- `apps/decision-desk/src/product/PerpetualAdvicePanel.tsx` 中加一块：拥挤度条（多/空两条，显示分位）、三列概率对比（基准率 / 条件模型 / 最终），状态标签，未通过验证时灰色并写原因。
- Vitest：有/无新字段、qualified/not_qualified、旧响应不报错。
- 验收：Vitest 全过；开发实例中打开 `http://localhost:5173` 截图存证据目录。

### T0 页面先展示每次分析（最先做，用户 11-01 指定）

用户原话："先在页面上把你的每次分析还有建议概率先写出来"。邮件等个人渠道延后。

- 页面 `apps/decision-desk/src/product/` 下新增"分析记录"列表：每次研究（含失败、降级）一行，显示时间、标的、期限、涨/横/跌概率（基准率 / 条件模型 / 最终三列）、建议操作和理由、有效期、到期后的实际结果和评分。
- 数据来源复用现有报告读取和 `perpetual_forecast_scoring_view.py` 的到期评分，不新建存储。
- 失败、降级的研究也要显示，并写出失败原因，不能隐藏。
- 测试：Vitest 覆盖完成、降级、失败、无评分、已评分五种行。
- 验收：开发实例页面上能看到 10-28 以来全部 12 次研究，截图存证据目录。

### T8 事件目录：全部重要事件都要有预测（用户 11-01 指定）

现状（见 `EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md`）：首页"核心事件事前预判"面板写死了 CPI 九格（`EventPrevisionPanel` 第 80 行），标题也写死为"美国 CPI"（`query_views/event_prevision.py:160`）。PCE、非农、FOMC 只有日历和部分接线，没有逐个事件的预测。

1. 新建 `packs/events/catalog.v1.yaml`，每类事件一条：名称、发布机构、频率、北京时间、重要性等级、历史上对 BTC 的平均影响、数据源。重要性分三级：
   - **S 级**（BTC 历史 4h 波动显著放大）：FOMC 决议与发布会、CPI、非农就业报告、PCE
   - **A 级**：PPI、零售销售、GDP、JOLTS、ISM 制造业/服务业、美债季度再融资公告、NVDA 等巨头财报、BTC/ETH 期权季度到期
   - **B 级**：初请失业金、密歇根消费者信心、联储官员讲话、FOMC 纪要、ECB/BOJ 决议
   - 等级不靠拍脑袋定：用 T2 的 1m K 线，算 2022 年以来每类事件发布前后 4h 的已实现波动与平常同时段之比，按比值排序后定级，结果写进 yaml 的 `measured_vol_ratio` 字段。
2. 2026 年 11–12 月已确认日期（来源见第 13 节，执行时以发布机构官网为准）：11-02 美债借款预估，11-04 季度再融资公告，11-06 非农（10 月），11-10 CPI（10 月），11-13 PPI，11-17 零售销售，11-17 盘后 NVDA 财报（预计），11-25 GDP 修正值和 PCE（10 月），12-04 非农，12-09 FOMC（含点阵图），12-10 CPI。11 月没有 FOMC 会议。
3. 每个 S/A 级事件，发布前 72h 自动生成一张事件卡片，内容包括：市场预期值（一致预期）、超预期/符合/低于预期三种情景下 BTC 4h 的涨跌概率（由历史同类事件的条件频率算出，样本少时向基准率收缩，方法同第 6.2 节）、期权隐含波动（DVOL）、当前拥挤度、各情景对应的操作。
4. 修改 `event_prevision.py`：标题按事件类型取值，不再写死"美国 CPI"；`uncovered_families` 由目录自动计算。
5. 一致预期数据不付费：优先用公开日历页抓取，抓不到就标"预期未知"，此时只给波动幅度预测，不给方向。
6. 测试：目录 yaml 校验；每个 S 级族都能生成卡片；预期缺失时不给方向；标题不再出现写死的"CPI"。

### T9 宏观流动性层（用户 11-01 指定：要像资深交易员一样看全局）

最近一次下跌是多个因素叠加的结果（第 13 节）。系统要能同时看到这些因素，而不是只看币价。

- 数据全部来自 FRED，免费，现有 `packages/provider_adapters/macro_market/fred.py` 可复用：
  - 净流动性：`WALCL - WTREGEN - RRPONTSYD`（周度）
  - 银行准备金 `WRESBAL`
  - 资金面：`SOFR` 与 IORB 利差
  - 美元指数 `DTWEXBGS`
  - 10 年期名义收益率 `DGS10`，实际收益率 `DFII10`
  - 高收益债利差 `BAMLH0A0HYM2`
  - VIX `VIXCLS`
- 用途：
  1. 作为 M2 模型的候选特征（日度，严格按发布日期对齐，周度 H.4.1 数据以周四发布时刻为可见时间，不能按周三数据日期对齐）。
  2. 新增"宏观流动性"研究角色，只读这一组数据和事件目录，首轮独立判断，再与持仓角色互查。
  3. 页面显示"流动性仪表"：净流动性周变化、TGA、准备金距 2.8 万亿下限的距离、信用利差、美元、实际利率，每项标出近 1 年分位。
- 测试：发布时刻对齐（周四之前看不到本周数据）；缺数据显示未知。

### 本方案之后

- 交易所安全/提现暂停/大额异常转出监控，新闻关键词流（免费来源优先）。
- 多角色研究的其余角色：美股财报、链上安全、地缘商品。
- 邮件等个人告警渠道：主流程效果验证之后再做（用户 11-01 决定）。
- 爆仓距离：用户 11-01 决定不做。

## 9. 工作规则（每一条都必须遵守）

1. 先读 `AGENTS.md`，其约束优先于本文件。特别是：不改正式数据目录、不删历史、不改旧 Run 状态/时间/哈希、不调用 `infra/dsh/build-product.ps1`、真实研究运行期间不改源码。
2. **改代码前先写排查记录**：在 `CODEX_LOG.md` 追加一条，写清要改的文件、原因、方案、还没核对的事。
3. 先写失败的测试（RED），再写实现（GREEN）。日志里要有 RED 的实际输出。
4. 不为了让测试通过而改测试断言；不为了让回测通过而改验收门槛。门槛只能由用户或 Claude 改，改动要记录原因。
5. 新 Python 模块 ≤400 行、函数 ≤60 行。
6. 数据、日志、回测输出放 D 盘新目录 `D:/DH-pos-<id>/`，不复用旧目录。E 盘只放源码和小索引。E 盘剩余约 82GB（2026-11-01 实测），T2 历史数据预计数 GB，一律放 D 盘。
7. 推测要标"推测"。未执行不算通过，SKIP 不算 PASS。
8. 不能把"代码写好了"写成"功能完成了"。每个任务分三档记录：代码完成 / 测试通过 / 真实数据验证通过。
9. 不提交 git（由用户决定提交时机），不推送。
10. 卡住时（同一错误重试 2 次仍失败），停下来，在日志写清楚卡在哪、试过什么，等 Claude 复核，不要绕开。

## 10. 日志格式

### 10.1 改动日志（追加到 `docs/diagnosis-2026-10-23/CODEX_LOG.md`）

每完成一步追加一条，格式固定：

```
### POS-<任务号>-<序号> <UTC 时间> <一句话标题>
- 目的：
- 改动文件：<绝对路径>（新建/修改，修改前 SHA256 → 修改后 SHA256）
- 命令：<原样贴出>
- 退出码：
- 结果：PASS x / FAIL y / SKIP z；失败时贴最后 40 行
- 证据：<D 盘路径>
- 偏离本方案之处及原因：（没有写"无"）
- 下一步：
```

### 10.2 实验登记表（`D:/DH-positioning-experiments/registry.csv`，只追加）

每次跑回测一行：`时间, 实验id, 模型, 特征列表, k值, 阈值, 训练区间, 是否碰了留出集, log-loss差, 是否通过, 报告路径`。没通过的实验也必须登记。Claude 复核时会比对登记表与报告，发现有未登记的实验结果，该任务视为未完成。

## 11. 用户决定（2026-11-01）

1. **不接入付费数据源**。只用第 3.1 节免费端点和 FRED。爆仓数据只做实时、不进模型。
2. **不做爆仓距离功能**。系统的重点是事前预测，不收集用户仓位和杠杆。
3. **暂不做邮件等个人告警**。先把每次分析、建议和概率显示在页面上（T0）。10-31 的邮件代码保留，不再继续。
4. **事件要覆盖全部重要事件并分级**，不能只有 CPI（T8）。
5. **要从全局看多个因素**：流动性、美债、美元、信用、持仓结构一起看（T9）。
6. git 提交：待用户确认（见对话记录）。

## 12. Claude 复核清单

Codex 每完成一个任务，Claude 按以下检查，结果追加到 `CODEX_LOG.md`，标 `REVIEW-POS-<任务号>`：

- [ ] 日志条目齐全，命令可复跑，证据路径存在。
- [ ] 改动文件的 SHA 与日志一致，没有日志外的改动（对比 `git status` 和日志列出的文件）。
- [ ] 抽查 1 个测试：亲自运行，结果与日志一致。
- [ ] T3/T4：防泄漏测试和镜像测试真实存在且断言有效（故意改坏一处，测试应失败）。
- [ ] T4：实验登记表与报告一致，留出集只用了一次，门槛没被改过。
- [ ] T5：旧报告读取仍通过；提示词旧段落未删除。
- [ ] T6：冷却和反转逻辑有测试；`data.stale` 不被当成无风险。
- [ ] T7：回放结果原样记录，没有用事后数据。
- [ ] 结论措辞：没有把"技术通过"写成"预测有效"或"能盈利"。

## 13. 10-29 至 10-31 下跌的多因素事实（T9 的设计依据，2026-11-01 检索）

| 因素 | 事实 | 来源 |
|---|---|---|
| 美元流动性被抽走 | 10-28 当周净流动性 5.73 万亿美元，周降 784 亿；TGA 升 705 亿至 1.016 万亿；逆回购只剩约 1.8 亿，已无缓冲；准备金周降 688 亿至 2.894 万亿，距 FOMC 的 2.8 万亿下限约 940 亿 | [Au79 10-31](https://martyau79.substack.com/p/31oct2026-traditional-markets-and)、[TFTC](https://www.tftc.io/us-liquidity/net-liquidity) |
| 月末资金紧张 | SOFR 比 IORB 高 6–8bp，贴现窗口借款升至 95 亿 | 同上 |
| 信用利差走阔 | 高收益债利差 +12bp 至 326bp | 同上 |
| 利率与美联储 | 10 年期一度 5.05%，后回落至 4.94%；联储官员仍在讨论加息 | [investingLive](https://investinglive.com/news/investinglive-americas-fx-news-wrap-30-oct-it-s-a-wrap-nasdaq-tumbles-as-ai-costs-worry-investors-fed-speakers-keep-hikes-in-play/) |
| 科技股 | 纳指 100 跌 3.46%，费城半导体跌 7.2%，起因是 AI 资本开支和现金消耗 | [Morningstar](https://www.morningstar.com/news/dow-jones/202610303491/ai-selloff-deepens-oil-jumps-and-crypto-falls) |
| 黄金同跌 | 金价跌 3.3% 至约 3,899，被解读为基金为补保证金卖出流动性最好的盈利资产，非基本面破位 | [Au79](https://martyau79.substack.com/p/31oct2026-traditional-markets-and)、[Cointelegraph](https://cointelegraph.com/markets/bitcoin-risks-worst-october-in-12-years-as-tech-sell-off-spreads-to-gold) |
| 加密自身 | Binance 被盗约 63 亿，约 15 亿爆仓；此前 OI 上升而价格下跌，多头拥挤 | [FXStreet](https://www.fxstreet.com/analysis/gold-on-a-free-fall-to-3-900-bitcoin-slides-to-76-500-after-the-largest-hack-in-crypto-history-202610301536) |

近期日期：11-02 美债借款预估、11-04 再融资公告（[Treasury](https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents)）；11-06 非农、11-10 CPI、11-13 PPI、11-17 零售、11-25 GDP/PCE、12-09 FOMC（[SpicyFutures](https://spicyfutures.com/economic-calendar/)、[CPI 日程](https://www.usinflationcalculator.com/inflation/consumer-price-index-release-schedule/)）；NVDA 财报预计 11-17 盘后（[Seeking Alpha](https://seekingalpha.com/symbol/NVDA/earnings)）。

## 14. 监督流程：Claude 规划和复核，Codex 执行

1. Claude 每次只派一个任务（T0、T1……），用 `codex exec -C E:/project/study/codex/dsh/decision-hub -s workspace-write --add-dir D:/ -o D:/DH-pos-<id>/codex-last.md "<任务说明>"` 启动。任务说明写明：读本文件哪一节、只做哪个任务、完成后按第 10 节写日志。
2. 不使用 `--dangerously-bypass-approvals-and-sandbox`。Codex 不提交 git。
3. Codex 完成后，Claude 按第 12 节复核：读 `codex-last.md` 和 `CODEX_LOG.md` 新增条目，对比 `git status`，亲自重跑一个测试。偏差写入 `REVIEW-POS-<任务号>`，不合格就带着偏差清单把同一任务重新派给 Codex。
4. 当前阻塞（2026-11-01 实测）：`codex exec` 在本机返回 `"model is not supported when using Codex with a ChatGPT account"`。配置的 `gpt-6.1-sol`，以及 gpt-6.1、gpt-6、gpt-5.5、gpt-5 都失败；`codex login status` 显示已登录。同日 16:45 的 Codex 会话用同一模型能正常回复，原因未查明。需要用户在 Codex 客户端确认可用模型或重新登录后，才能开始派任务。
