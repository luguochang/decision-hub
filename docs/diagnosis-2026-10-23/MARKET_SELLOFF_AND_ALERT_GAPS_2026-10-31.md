# 10月29–31日市场下跌与产品预警缺口核查

日期：2026-10-31，北京时间。范围：公开行情/新闻检索、有限业务代码只读核查、记录；没有修改业务源码、启停服务、构建、提交Git、调用付费研究或执行交易。

## 1. 本次直接读取的行情

北京时间13:00左右，Coinbase公开接口单次读取。交易所、窗口及时间不同会产生差异；滚动24小时变化不是两天累计跌幅，也不表示持仓期间没有触及强平线。

| Coinbase Exchange | 查询UTC | 最新价USD | 滚动24h开价 | 滚动24h变化 | 滚动24h高/低 |
|---|---|---:|---:|---:|---|
| BTC-USD | 2026-10-31T05:00:19.6501317Z | 77023.46 | 78069.08 | -1.34% | 78634.80 / 76141.02 |
| ETH-USD | 2026-10-31T05:00:20.0828261Z | 2235.89 | 2238.33 | -0.11% | 2258.64 / 2203.72 |

源：[BTC stats](https://api.exchange.coinbase.com/products/BTC-USD/stats)、[ETH stats](https://api.exchange.coinbase.com/products/ETH-USD/stats)。涨跌由last/open计算，非模型预测。另在05:00:08Z读取Coinbase v2 spot，BTC77027.515、ETH2235.595；不将两次报价混成同一盘口。

## 2. 已证事实与时间线

1. 北京时间10月29日02:00，Fed维持3.75%–4.00%，两位异议者主张加息25bp。高利率/通胀是背景，本次没有突然加息。[Fed原声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm)
2. 10月29日亚洲时段，科技股风险偏好下降、BTC已跌破8万美元；这一段先于币安攻击。[当时报导](https://www.coindesk.com/markets/2026/10/29/bitcoin-dips-below-usd80-000-as-samsung-led-risk-aversion-in-tech-and-a-hawkish-fed-weigh-on-crypto)
3. 北京时间10月30日04:05（29日20:05UTC），币安热钱包受攻击。官方估计失窃约63亿美元，暂停提现并承诺以SAFU/公司资金覆盖失窃资产；截至Update #2，提现恢复仍是附条件计划。冻结不等于已追回，承诺补失窃资产不能推定涵盖市场交易亏损。[官方Update #2](https://www.binance.com/en/support/announcement/detail/d3306d9dca5243548c813b605e1d1751)
4. 攻击范围是交易所托管/授权基础设施及多条链上的资产；Elliptic估算64.3亿美元，时间/定价/归集口径与官方约数不同。幕后归因仍应标明分析机构判断，不能说比特币协议被破解或据此认定交易所破产。[Elliptic链上分析](https://www.elliptic.co/insights/binance-largest-hack-in-history/)
5. 10月30日美股收盘，纳指-2.86%、标普-1.82%、Intel-28.6%、半导体指数-7.2%。财报引发对AI投入回报、现金流及部分业务指引的重新定价。[Reuters美股](https://www.aol.co.uk/articles/nasdaq-futures-tumble-amazon-intel-084615000.html)
6. 同日韩国KOSPI收-3.48%。欧洲STOXX600收-0.36%，MSCI全球-1.51%；各地跌幅并不相同。全球Reuters还记录美债10年收益率当日下降5.8bp、美元指数仅+0.04%，而Brent涨2.4%。不能编成当日美元/债息暴涨主导一切。[Yonhap韩国](https://www.koreajoongangdaily.com/business/kospi-plunges-35-percent-as-ai-doubts-hammer-korean-chip-auto-stocks/12947570)、[Reuters全球](https://new.streetinsider.com/Reuters/Stocks%2Bslump,%2Bbonds%2Bleap%2Bas%2BAI%2Bgiants%2Bspending%2Bspooks%2Binvestors/27259748.html)
7. 10月30日早间报导的加密清算窗口约15.7亿美元，期权保护需求上升。统计是当时窗口，不是截至本报告的实时总额。[清算/衍生品报导](https://www.coindesk.com/markets/2026/10/30/crypto-liquidations-jump-to-usd1-57-billion-as-binance-hack-sends-bitcoin-ether-lower)
8. Farside本次读取：BTC ETF 29日净流入1720万美元，30日净流出6.432亿美元；ETH分别净流出1.055亿、1.557亿美元。不能把30日完整收盘流量倒置成29日事前可知事实。[BTC表](https://farside.co.uk/btc/)、[ETH表](https://farside.co.uk/eth/)

推测/解释：美国科技盈利及AI投入回报预期冲击，经供应链和共同资金风险暴露传至亚洲/欧洲；高利率与能源通胀约束估值，币安安全冲击叠加信任/流动性风险，杠杆清算再放大价格变化。同步下跌不单独证明统一操纵，以上资料也不能量化每个因素的独立贡献。

## 3. 产品核查：代码存在、运行、送达分开

证据根为 E:/project/study/codex/dsh/decision-hub/。下列是本次有限核查，不能外推全仓能力不存在。

- 两份旧4h研究于10月29日北京时间09:04/09:52发布，BTC下跌32.4537%、ETH31.4352%。数值精确保留冻结90日基准率，未证明模型增益，不能当10月30日实时做空胜率。结果见 E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/RESEARCH_RECOVERY_RESULT_2026-10-29.md:11、:12、:14；P3/P6仍未齐见:71、:73。
- 有获授权定时/事件研究接缝：E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/perpetual_research_schedule.py:1，及 ports/perpetual_research_inputs.py:59。未核实事发时用户授权、调度成功、模型结果和通知送达。
- 有风险监测，不能说完全没有：E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/risk_monitor.py:259，条件确认后创建RiskAlertRecord及local Outbox，alarm_kind=fact_change、prediction_hit=False，表示已观察变化。不是已校准的事前暴跌概率。
- 有持续方案观察：E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/perpetual_plan_watch_continuous.py:579起，依赖有效active watch、观察条件及策略，remind_and_research才触发再研究。未查明本次是否有有效watch及用户收到通知。
- 常规输入不只CPI：E:/project/study/codex/dsh/decision-hub/packages/provider_adapters/research/fact_pack.py:323起有跨资产/美元/美债/波动率，:370起有funding/OI；market/okx_research.py:54支持资金费/OI。market/crowding.py:93的盘口不平衡代理不能当全市场杠杆/清算数据。字段存在不证明持续盘中风险重算。
- 专用事件生产仍偏CPI：E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/application/event_scenario_autoproduction.py:90。安全/监管/故障类事前概率与观察闭环未验收，见 E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md:39。ETF/专门清算链在有限入口未核实，不宣称全仓绝无。
- 研究账户输入是纸交易账本：E:/project/study/codex/dsh/decision-hub/packages/kernel/decision_hub_kernel/ports/perpetual_research_inputs.py:16；application/perpetual_paper.py:645的纸仓投影liquidation_estimate=None。未证明已接用户真实交易所仓位、保证金模式、标记价或强平距离。
- 本次单次读取127.0.0.1:18047/health/ready连接被拒。仅代表本机此刻该端口未响应，不能反推10月30日离线，亦不能归因本次损失。

## 4. 责任边界与需补证的业务闭环

已证交付问题：以前的阶段验收重点是研究流程恢复、来源认证、接口读回；完整实盘预警及金融资格未通过。执行沟通应更突出这一点，不能让“接口自测通过/能出概率”被理解为“能保护真实杠杆仓位”。目前仍有研究/记录价值，实盘预警效果未经证明。

用户本次未提供币种、持仓方向、杠杆、开仓/强平时间与价格、实际通知及系统事发日志。因此不能认定某条告警已失败、如果做空必赚钱、某个缺口必然导致全部损失，或承诺可以提前知道黑客行为。

需要分别验收：公开风险信号出现时间→系统实际摄取时间→触发/重算→带期限/证据的决策→通知生成与送达→实际仓位风险变化。服务/数据/通知失败本身也须可见。日历事件与突发安全/脱锚/交易所异常应各有覆盖；必须用当时已可见数据回放，禁止用事后消息回填成预测。

还须区分：期限末下跌概率、期限内触及强平线概率、指定进出场/费用/资金费后的做空盈利概率。三者不等价；期限末反弹也不能挽回中途强平。没有相应校准和路径/仓位数据，本次不编造70%或80%等可交易胜率。

当前建议：优先保护剩余本金、重新核对真实仓位/杠杆/强平距离与平台提现状态，避免为追回损失高杠杆追空或盲目加仓。方向交易应有退出条件及最大可承受损失；止损可能滑点。平台补失窃资产承诺不能替代账户风险管理。本轮只做研究和记录，尚未修复或认证上述缺口。
