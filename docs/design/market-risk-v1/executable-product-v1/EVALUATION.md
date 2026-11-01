# BTC/ETH 自主决策切片：评估协议与验收矩阵

日期：2026-10-13。状态：**拟实现、未登记、未执行**。本文件提出新 Action 协议，不修改旧试验身份、窗口、门槛或成绩。首个交付对象是自主搜证、条件行动、持续修订、真实行情结算及经验再次消费；固定规则只是对照。行情触发尚未出现可以交付可运行计划，但不能计为操作命中。

## 1. 范围与不同价值目标

验收还包含[核心意图I01–I12](VALUE_ALIGNMENT.md)。这些补充真实业务门不修改本文件金融协议：外部观点增量与技能增量分别记录，正式新实验须事前登记；不能用更多skill/帖子/观望次数来证明收益改善。

首版为两个精确 scope：`crypto:spot:okx:BTC-USDT:trade:1m`、`crypto:spot:okx:ETH-USDT:trade:1m`。报价使用同场所 bid/ask，USDT 单位不与 USD 混用。仅纸面现货多头、现金和减少多头风险；无做空、杠杆、订单提交。实际空仓的风险解释不计为本协议交易成绩。

每资产、每scenario、每method建立独立连续纸面组合，初始名义权益均为 10,000 USDT。BTC/ETH 先分别结算，综合结果按两个相同本金账户等权汇总；没有共同50%资本池，不在资产之间借用现金或重复占用同一本金。统计上两资产仍共用同一冲击分组。

cash分支新建仓：每资产目标名义暴露上限25%当时权益、初始计划风险预算0.5%当时权益；权益10,000时分别为2,500和50 USDT。数量取 `min(0.005E/(风险距离+每单位预计往返费用滑点), 0.25E/入场价, 可用现金/(入场价+每单位入场费用))`，均使用当时可知价格和已登记成本；跳空可使实际损失超过计划预算，预算不是保证止损金额。新增风险和恢复仓位必须重新核限额，不能用未来盈利确定仓位。

existing_long初始100%现货是历史继承情境，限额仅约束新增风险，不为达到25%假设自动减仓。减仓50%后残余继承仓位也不因仍大于25%被强制卖出。退出后新建、或主动加回须按25%目标暴露上限和0.5%计划风险核验；若继承仓位已大于25%，不允许追加风险，仍可持有、减仓或退出。各方法必须从同一初始持仓出发，不能先替候选减仓再比较。

| scenario_id | 起始状态/主目标 | 基线与结果 |
|---|---|---|
| cash | 100%现金；捕获扣成本后上行机会 | B0现金参照；B1固定条件配方v0；A1自主增强候选。评价净收益、漏接基线机会、回撤和覆盖 |
| existing_long | T0 已有满额现货多仓；降低下行路径损失 | B0同初始数量/估值的持有参照；B1固定减险配方v0；A1自主增强候选。评价净效用、最大回撤、最大不利偏移及卖出后的上行损失 |

existing_long 初始持仓是对照设定，不补造入场成交；各方法初始数量完全相同。其后卖出、买回才计费用。两个 scenario 分别报告，不将减仓避损成功与建仓交易胜率相加。未知真实用户仓位，不把纸面比例投射成其真实金额。

**主比较固定为A1（AI增强候选）对B1（固定v0）。** B0在cash分支持现金、existing_long分支持有原单位现货，只是次级经济参照，不是可事后切换的主比较。候选可以提出新机制和条件，但必须在当时冻结配方、来源、理由、参数、反证与输出；改动形成新 revision，原版不可覆盖。

## 2. 旧协议不能直接使用的边界

1. `ports/path_forecast.py:23–61` 的 path V1/V2 固定 28 日，又要求 100 独立 UTC 日、30 事件 block；28 日内不可能满足 100 日。它可以结束为不足，不能用 1m 数据点或两资产翻倍凑样本。本协议不放宽其门。
2. `packages/evals/prospective_trials.py:24–50` 固定 BTC-USDT-SWAP、first-occurrence-per-study、close_1m 方向配对，且至少 30 日；不是 BTC/ETH 现货 Action 注册器。
3. `packages/evals/prospective_labels.py:28–99` 标签明确为 `estimated`、`minute_close_not_execution_pnl`，`promotion_eligible=False`。不能给该输出换标题叫成交净利润。
4. `decision/path_score_inference.py:17–29,68–99` 认证 UTC 零点日预测、固定 24h+1m 窗口和 Brier loss；不能直接传入小时行动、交易收益或任意新统计参数。
5. 校准 CLI 默认 policy 的样本/折数/间隔是原方向概率协议，不能给 Action 稳定 70% 胜率背书。新 scorer/CLI 需要新 schema/version，而不是修改旧 Literal。

## 3. 新协议登记、时限与机会分母

### A28：28日工程及前瞻描述试运行

`action-forward-pilot.v1`：T0 为登记完成后的首个 UTC 零点，T1=T0+28 日。每个整点、每资产一个机会，固定 `N_expected=28×24×2=1344`。scenario、策略、配方、revision 是该机会的维度，**不重复扩充机会数**。

最迟结算：T1+28 小时。新建仓窗口最长 24 小时；机会+120秒的方案只能前瞻生效；到期后最多4小时完成原件认证，迟于此点为 `label_missing_at_deadline`。未发表、停机也预先有 opportunity_id，不是只有有报告才有机会。

初始发表 SLA 为 slot+120秒：当前可验证研究/事实可以形成第一版方案，深度 DSH 还未完成时保留未决状态。后到的研究以自己的 as_of/revision 生效，**不倒填原整点，不能强迫真实深研究两分钟结束**。迟到仍可用于新的事件后响应，但不计原机会按时成功。

### 状态分母

每方法、scenario、机会必须有且仅有一项主处置结果；该项可引用多个合法 revision：

- `actionable`: 有条件方案；动作枚举 `enter_long / hold / reduce_long / exit_long / wait`（买入/持有/减仓/退出/等待）。
- `no_edge`: 已比较候选而无优势；保留比较依据。
- `unknown`: 价格/宏观/仓位/成本/权限或关键反证未解决，保存具体缺项。
- `late`、`failed`、`missed`: 原机会未能按时形成有效结果。

条件计划另记 `armed / triggered / invalidated / expired_untriggered / suspended_unknown`；成交另记 `filled / unfilled / unpriced / ambiguous`。`wait` 不是 unknown、未触发不是盈利、未成交不是亏损、到期不触发也不算交易胜利。一个机会内两配方为替代，净持仓不得重复叠加；revision 不算另一笔入场。

以上为评价视图标签，不新增另一套生产状态机。动作映射为buy→enter_long、reduce→reduce_long、exit→exit_long、wait/hold原样；expired_untriggered由建议expired且无trigger派生，suspended_unknown由监控degraded和truth=unknown派生。生产建议终态仍按ARCHITECTURE，不能将这些视图标签反写head。

计数恒等式：`N_expected = N_actionable + N_no_edge + N_unknown + N_late + N_failed + N_missed`。报告 N_due、N_label_verified、N_on_time、N_filled、N_scored_trades、N_event_clusters；所有比例为零分母时返回 null。

连续组合主评价包含等待和实际未触发时段。无仓等待保持现金；已有仓无有效新方案保留原持仓/原已武装保护条件，**不能事后默认为成功减仓**。数据缺失使组合估值未知时不补收益0；单列未评分时段、上下界（若能证明）及原失败分母。

## 4. 输入截止、动作时点与成本

### 时间链

每条输入保存 `observed_at / source_public_at或上下界 / received_at / accepted_at / known_at / revision_at / body_hash`。算法使用 `known_at<=decision_as_of` 且 source版本满足可得时点的原件；新闻事件日期不得代替公开日期。宏观日精度发布不能冒分钟级已知，当前历史 CSV 不能回填 vintage。

对于 primary scheduled cohort，A1/B1冻结同一个 as_of 和可用事实集合；B1只使用其事前冻结的规则与特征（含所需M/C，缺源保持unknown），A1可提出M/C之外的机制。比较新增来源时显式标成信息增量实验，不混称同信息方法更优。后续新事实使双方各自形成有时点的新 revision，不能让一方看未来数据后重算旧动作。

`trigger_true_at` 采用形成条件所需最后输入的 known_at，不能将迟到 bar 的历史 close_at 记成当时已经报警。

两条成交观察通道分开：

1. **shadow_engine**：`base_action_at=max(plan_confirmed_at, trigger_true_at, admission_valid_at)`，`eligible_action_at=base_action_at+reaction_delay`；主反应延迟60秒，0/300秒只作敏感性。这是纸面系统动作，不是人看见后实际成交。
2. **pc_visible**：`eligible_action_at=max(base_action_at, server_received_display_receipt_at)+reaction_delay`，同样主延迟60秒、0/300敏感性；不在shadow已经加60秒后再加一次。页面关闭/未确认可见保持 unknown，不伪造送达。

主策略成绩使用 shadow_engine；pc_visible 为产品可用性及响应敏感性，不能把0秒模拟当用户实绩。每个方法的真实发布时间/计算延迟是产品效用的一部分，不等候慢候选再倒移基线时间。

### 价格和成交

- 买入选 eligible_action_at 后第一个合格 ask，卖出选 bid；same scope、observed_at>=eligible_action_at，报价收到时年龄<=10秒，接收不得早于观测，原件/hash完整。
- 等待合格价格最多60秒，否则 unpriced/unfilled；不选择后来最有利价格。实际需求将必要报价按计划捕获；一分钟 close 只能产生 `close_proxy` 诊断，不授 execution_price_verified。
- 成交价：`buy_fill=ask×(1+s_buy)`，`sell_fill=bid×(1−s_sell)`；spread 已含在 bid/ask 中，不能又扣一遍 spread。
- 试运行默认 fee=每侧10bps、滑点=每侧5bps；压力费用每侧20bps、压力滑点每侧10bps，分别为两倍。它们是明确模拟假设，未必等于账户费率/真实可成交冲击。正式确认需登记账户等级对应的可核费率和所需报价权利，否则 cost/rights 不合格。
- 逐笔 fee=`abs(quantity×fill_price)×fee_rate`，换手全部计费；现货持仓不伪造 funding。现金收益初值0，借币/融资不在首版范围。其他方法采用完全相同成本、报价和延迟口径。
- 部分减仓按实际数量结算；最多一个净现货多仓，0<=exposure<=1；不允许虚构资金、无限补仓或重复触发同订单。
- Stop/take-profit 先后依据实际可得报价序列。只有OHLC而同bar两者皆触及时，主标签 ambiguous；可输出“先止损/先止盈”收益上下界，不选择有利路径。
- 跳空穿过止损，按后续首个合格报价减仓/退出，不在止损阈值虚构成交。中断时禁止新增风险，不假定已有仓已经平掉；原保护仍保留并等待新有效报价。

## 5. 初值、基线与自主候选

基线沿用主方案 v0 的**试用初值**，不是验证过的市场建议：此前6h箱体；连续2根 closed 1m 突破；完整1h量能/此前20完整1h中位量>=1.2；U为此前20完整1h true range 中位数；入场后1U风险距离、2U目标、24h到期。任何用于边界的窗口排除当前触发bar/未来bar，U<=0/缺路径返回unknown。

已有多仓固定减险基线：已验证的宏观逆风M及双场所拥挤C同时成立，减少50%暴露；下破此前6h下沿的两根bar退出剩余。M/C unknown不当false。M为广义美元与2年收益率最近5个有效观测期变化均>0；C为两场所6h OI增加、资金费>0且现货6h收益<=0，单位/合约匹配。恢复只生成新计划，不自动加倍买回。

AI候选与v0分离：DSH可提出不同因果机制和具体动作，但每条ActionPlan须有可求值的条件、当前动作、适用仓位、入场/退出/失效、期限、主反原件、成本和修改理由。只允许受限DSL调用已注册feature。模型生成任意Python、仓位翻倍、保本止损保证或未核概率EV属于G门拒绝。

未知的概率维持null；未校准主观概率原样保存。可对合格的规则/研究纸面方案进行观察，不能将未知写0.5后执行。`no_edge` 必须表明与现金/持有/v0哪些方案比较，不能用一条全局资格失败吞掉全部方向和动作。

### 消融

首个正式主比较只检验 `A1` 对 `B1(v0)`，`B0` 为次级经济参照。固定输入的无宏观、无拥挤、无技能/案例记忆、无反方消融可在离线诊断阶段执行，全部保留且不按两已知行情选优。前瞻只允许额外一个事前指定消融（优先 full vs memory_off），其统计结论为次要；不得在28日反复更换正式候选。

技能刚加载不证明使用。真实消费必须记录 skill_id/version/hash、触发问题、调用结果、采纳/拒绝依据、对哪条claim/condition/revision产生影响；“未改变方案”允许，但需要可审查原因。前一真实已结算失败形成候选经验后，在下一次独立研究实际检索/读取/判断适用性，才计学习链功能完成；不强制晋升无增益技能。

## 6. 价值公式与评分目标

组合使用保守可变现bid估值：`equity_t=cash_t+quantity_t×bid_t`；费用已从现金扣除。日收益 `r_d=equity_end/equity_start−1`，只有完整可估值时段才评分，缺失保持未知。

- 净组合收益：`equity_T/equity_0−1`。不能把每小时重叠24h虚拟收益相加代替连续组合。
- MDD：`max_t(1−equity_t/max_{s<=t}equity_s)`。1m收盘口径只能标 `MDD_close`，不冒连续极值。
- 交易净收益：出售收入−买入支出−全部费用；胜率=`N(net_pnl>0)/N(真实触发且完整结算的交易episode)`，零利润单列、不计win；止盈触发率另列。
- R单位：入场初始风险 `R=quantity×U`，`net_R=net_pnl/R`；部分减仓按原R累计，R不能事后换大以美化亏损。
- 简单二结果EV：`p×G−(1−p)×L−C`；多状态按完整分布求和。未核p/G/L时不输出精确EV，70%且赢1R亏3R成本0.05R仍为−0.25R。
- 下行避损：已有多仓对同初始持有组合的 `MAE_hold−MAE_candidate`、MDD差、净收益差；同时报卖出后持有基线的上行收益，不能只看跌时减少了多少损失。
- 上行机会：对事前登记的v0触发事件，比较candidate实际响应、成交价、净R；漏接=`有完整基线事件却无有效candidate动作`。这不是事后找低点/高点的oracle机会。
- 覆盖：按时发布/N_expected、可评分标签/N_due、可评分动作/N_due、有效监控秒数/计划秒数、未触发/等待/unknown/失败分解；低频出手可能有价值，但必须公开机会代价。
- 机制与概率：有事前数值概率才计算Brier=`mean((p−y)^2)`；y为冻结定义事件。校准的方向正确率、暴跌概率、交易成功率是不同目标。

每个scenario的主净效用为 `J=net_return−0.5×MDD_1m_bid`，主配对差 `ΔJ=J_A1−J_B1`。`MDD_1m_bid`是固定一分钟可变现估值网格的回撤；缺合格bid时标partial，不与close_proxy混用。另展示底层价格完整OHLC路径的MAE/区间极值，不称网格MDD为连续最坏损失。0.5为试用效用参数，不是所有用户统一风险偏好；不得试后改lambda挑赢家。B0现金/持有参照始终显示。金融门还检查风险增益和严重损失，不能只有平均净效用。

## 7. 正式确认窗、相关性与70%陈述

### A28 终点

28日是交付/数据质量/前瞻描述试运行，**不授稳定收益、70%或统计晋升资格**。终点输出：工程passed/failed；观察已登记已结束；金融 `insufficient` / `no_observed_gain` / `descriptive_gain_unconfirmed`。没有自然触发可以证明armed与监控正常，但金融为事件不足。不得延长同一次窗口、改候选或用旧历史补样本。

### A180 独立事前确认协议（后续资格任务）

在选定候选和成本来源后另登记 `action-confirmatory.v1`，T0/T1、候选hash和评价代码hash均固定；观察180日，T1+28h最终结算。不能倒签或把A28已用于挑选的结果作为确认测试集。确有问题时安全停止候选、保留分母；方法修改另协议，不恢复到原协议凑结果。

初始样本底线：180日期待范围中至少162个完整、成对、不同UTC日（覆盖>=90%）；按2日固定块至少81个完整块；每scenario有>=100个完整结算交易/调整episode并报告共同事件簇。要作“70%策略成功率”陈述，所指建仓策略还需>=160个非重复交易episode，全部处于确认集；极少交易仍可交付策略功能与事实成绩，但金融结论为不足。这些是预登记底线，不是独立性或功效保证。完整日必须包括两资产及全部被比较方法；缺失日不得假装收益0。完整块不足、净资产端点未知或缺失路径使尾部风险无法认证时，资格为insufficient而不是删缺失后qualified。

置信区间：alpha=0.05；固定种子271828、10,000次配对 moving-block bootstrap，块长度48h；同块同时保留两资产、全部方法、scenario、原机会/episode成员及费用。具体算法如下：

1. 从原完整连续分钟估值网格构建所有合法48h候选块，不跨缺失日/路径；每块包含各method/scenario的归一化权益轨迹 `g(t)=equity_t/equity_block_start`，开始g=1。真实动作/仓位/费用已体现在该轨迹，不另重跑策略或在块界虚构平仓。
2. 对每次bootstrap均匀有放回抽块，所有方法使用同一组索引；将下一块轨迹乘上前一块终点权益后拼接，直到达到原评价时长，最后块按时长截断。这是统计重采样的收益轨迹，不是新的市场/订单成交证据。
3. 从拼接后的共同长度分钟权益路径重算各J和ΔJ。依次报告点估计、nearest-rank的2.5%/97.5%分位95%区间；不对小时重叠收益或逐交易独立抽样。
4. 试验正优势的单侧bootstrap p值取 `p=(1+count(ΔJ_boot−ΔJ_hat>=ΔJ_hat))/(10000+1)`，后按Holm判定；原点估计<=0必不通过。实现保存每次样本索引及代码hash，可确定性复算。
5. 固定7日块敏感性复算相同方法（不足20个完整非重叠7日块只报不足）。在48h结论正而7日证据不支持时，标dependence_sensitive，不授稳定优势。该块假设仍不保证所有市场依赖已消除。

建仓成功率的区间使用同一共同时间块，把完整episode唯一分配到首次入场所在UTC日，重采相应wins/total，不把跨日持仓重复计数；收益仍包含所有退出与费用。触发事件簇相连的成员不得单独拆出参与win率，须另做共同事件簇重采敏感性；若簇太少无法得出有效下界，stable_70pct=false。独立伯努利Wilson区间可以辅助展示，但不是通过门。

两个scenario是两个主检验，使用Holm控制family-wise alpha=0.05：将两个p升序排列，较小者须<=0.025才继续检验另一项<=0.05；同时提供未经调整的95%CI及调整判定。70%只对事前指定建仓策略作下文门控的次级陈述，不在资产/scenario/参数之间挑最好胜率。宏观/技能消融和压力费用为次要分析，不宣称发现了新赢家。期间只看数据质量/安全停机，不按每天p值决定成功或提前加样本。

通过一般正效用建议资格：调整后A1对B1的ΔJ正优势成立、成本/价格/时点可认证、覆盖>=90%、候选MDD不比B1恶化超过绝对2个百分点、两倍费用与滑点压力下净效用不为负。cash还要求扣成本净收益>0；existing_long还要求MDD或MAE较B0持有至少改善绝对0.5个百分点，且净收益比持有落后不超过2个百分点。全部是需在事前登记并通过数值控制的初始产品门，不是金融规律；失败/无增益是合法终态。

70%是额外成绩陈述档位，**不是research_trial可用、工程交付或一般正效用建议资格的前提**；stable_70pct=false不能把有依据的研究试用Action全部改成unknown/no_trade。70%展示分两档：

1. 原始事实可显示“X/Y个完整episode净利润>0，样本成功率Z%，区间/覆盖如下”，说明相关性、真实报价+模拟成交及费用口径；不宣传稳定70%。
2. “该冻结策略在本确认集成功率至少70%”只有按共同事件/时间块校正的一侧97.5%下界>=0.70、样本/覆盖/成本/净效用和风险门全部满足才能使用。不能用普通逐交易Wilson区间忽略BTC/ETH同冲击。作为样本规模参考，检测0.80对0.70、双侧5%/80%功效的独立伯努利近似需约153样本，相关性会增加需求；所以160只是最低起点，不保证通过。

事件簇单独保存：重叠交易窗口、同资产同一计划、关联同宏观事件或两资产首次触发相距<=30m的事件连成共同簇。长链可能只有少量簇，不能拆短凑样本。统计以时间块为主，事件簇作相关性与稀缺事件的独立报告；收到新revision不是新样本。训练/测试按标签终点purge，24h horizon至少24h embargo；所有调参仅训练段，历史LLM回放只诊断。

## 8. 自动化验收矩阵

G为合同/隔离金样例，E为真实工程链，V为产品/金融价值。每项保存输入、代码版本、原件hash、输出、首次失败和退出状态；`not_run`不能写passed。G/E功能门通过不自动使V金融门通过。

| ID | 场景 | 通过条件 |
|---|---|---|
| G01 | schema、scope、币种、spot/swap误混 | 拒绝错scope/单位/非有限数字；旧报告原字节仍读 |
| G02 | source_public/known迟到、修订/未来新闻 | 旧cutoff排除未来版本；保持unknown，不补日期零点 |
| G03 | 四个研究状态及buy/hold/reduce/exit/wait | unknown不造50%，no_edge有比较，wait有转变条件及最迟复查 |
| G04 | 多空/仓位/重复请求/两worker | spot不隐性做空；CAS拒旧generation；一个净持仓、一次合法fill |
| G05 | box/U/volume边界 | 手算相同；触发bar不进入自身边界；缺历史/U0不产价位 |
| G06 | false→armed→两bar确认→trigger→持续true→恢复 | 合法transition一次；unknown不解除；退出优先新增风险 |
| G07 | bid/ask、费用/滑点、部分减仓、跳空 | 固定数值与手算一致；不双扣spread、不保证stop价 |
| G08 | 同bar stop与target/成交缺报价 | ambiguous上下界正确；未触发/未成交不算成功 |
| G09 | 1344机会与不同scenario/revision | 原机会分母守恒；缺报告、故障及late全部保留 |
| G10 | 端点上涨但途中跌5%、先减仓后涨18% | 分开方向、路径、避损和错过上行，不靠终点抹风险 |
| G11 | 完整技能/案例vs仅加载/不适用 | 三种使用状态准确；注入越权指令不执行；保留输出影响链 |
| G12 | 旧协议/fixture/未来标签当成绩 | schema/version拒错用；synthetic永远financial_qualified=false |
| G13 | 费用高而70%win、全等待、缺失样本 | EV/连续组合/覆盖不被胜率覆盖；分母0返回null |
| G14 | 重叠/两资产同冲击、重采样/多检验 | 共同block不翻倍；固定seed重算一致；不足无CI/不晋级 |
| E01 | BTC/ETH各一次真实DSH主动问题/技能/主反搜证 | 正常准入、原工具结果、机制/Plan绑定；不能用fixture代替 |
| E02 | each asset可用Action→持久armed→连续求值 | 当前价格/成本/时间可核；自然未触发如实记录，不改行情制造盈利 |
| E03 | 新反证/严重风险→revision→退出/暂停新增风险 | 原版保留；快速调整不等待完整长报告；来源到可见各段计时 |
| E04 | restart/未知提交/双worker/旧报告回链 | 原键GET恢复、无重复模型/成交；Saved和独立ACK及页面身份一致 |
| E05 | 页面已打开的方案/警报可见与0/60/300s价格 | server display receipt原件；页面关闭不称送达；模拟通道单列 |
| E06 | 到期真实原件→标签→错因→候选经验→下一Run读取 | 至少一项真实到期且合法结算；经验实际消费，效果不足不强制晋升 |
| E07 | 按时机会、源断线、恢复及报价来源探针 | 探针每源<=3次；确定拒绝停止；源失败不绿灯，不无界重试 |
| V01 | 用户找到动作/前提/反方/当前监控/退出 | 五PC任务人工记录；fixture不能充真人理解验证 |
| V02 | A28终点 | 全机会台账、真实价格/模拟成本、覆盖、waiting/missed和有限终态完整 |
| V03 | cash策略 | 成本后净组合、ΔJ、漏接基线机会、压力费用及MDD分开报告 |
| V04 | existing_long策略 | 对持有的避损和卖后上行损失同时报告；不计减仓为盈利交易胜率 |
| V05 | 技能/记忆消融 | 同输入/预算/窗口方法配对；无增益也结束，不能以自评分授资格 |
| V06 | A180正式确认/70%陈述 | 全部资格、相关性、覆盖、调整alpha门通过；不足/失败终态原件可复核 |

立即离线可验证G01–G14：单调上涨/下跌、V形先跌后涨、上涨后跳空、两bar突破后回落、同bar双触、触发前低点、未来宏观修订、spread突然扩大、分批减仓、缺价/断网、重复worker、同冲击双资产、全等待、高胜率负EV、技能不适用/真实新增反证。合成可用于恢复/时点/算术/状态机，**合成收益永远不是金融效果**。旧两个用户案例只作历史诊断；缺PIT/原Run时不补认证。

## 9. 可复用入口与拟建命令

### 已有（本轮未执行）

| 入口 | 可复用 | 不能直接承担 |
|---|---|---|
| `tools/research_evaluation/build_pit_dataset.py`、`build_splits.py`、`audit_core_replay_dataset.py` | 原件manifest、hash、case/window、replay取证 | 历史LLM知识泄漏免疫、现货行动收益 |
| `packages/evals/research_comparison.py:43–109`、`run_runtime_comparison.py` | 同不可变case/预算、两runtime结果原件、不覆盖输出 | Action成交/学习增益；该runner可能调用真实模型，不能称纯离线脚本 |
| `tools/ops/export_prospective_observations.py` | RO导出原观点及标签、自包含原件验证 | 自动回填Action字段/旧成绩变新资格 |
| `tools/ops/prospective_trials.py`、`prospective_labels.py` | 登记/幂等/hash/版本与label任务模式 | 直接运行28日ETH现货Action（旧合同固定swap/30日） |
| `decision/path_risk.py`、`forecast_alarm_matching.py` | 长短路径、完整标签、保守一对一/共同事件概念 | fill或trade胜率；旧matching方法/门槛需新Action版本 |
| `tools/research_evaluation/evaluate_forecast_calibration.py` | 明确概率目标的离线数学和walk-forward | 行动胜率/执行PnL认证；加database-url会写库，不是RO |

已有命令示例：`python -m tools.ops.export_prospective_observations --verify <已有自包含export>`；`python -m tools.research_evaluation.evaluate_forecast_calibration --input <合法旧协议样本.json> --output <新输出.json> --strategy-version <版本> --horizon 24h`。后者只写指定输出，不能当Action验证。

### 拟建、尚不可运行

以下均须实现后才可执行，不能把命令写入文档就登记通过：

```text
python -m tools.action_evaluation.validate_protocol --protocol <A28/A180.json>
python -m tools.action_evaluation.run_golden --suite <fixtures/action-v1/manifest.json> --output <new_dir>
python -m tools.action_evaluation.verify_export --manifest <真实Action自包含manifest.json>
python -m tools.action_evaluation.score --protocol <冻结protocol.json> --manifest <真实export.json> --as-of <UTC> --output <new_dir>
```

`tools.action_evaluation`为唯一新Action评价CLI命名空间，不再另设平行评价入口。validate/golden/score不调用网络、模型、启动服务或写产品库；默认output必须新建，不能覆盖旧成绩。数据采集、正式登记、影子Worker和DSH是正常产品入口；评估器不自行补模型结果或选择最有利报价。返回码：0=工具正常完成并生成完整裁决（包括insufficient/no_gain/failed资格），2=输入/schema/原件验证错误，3=程序或算法执行错误。资格不足不是程序异常；消费方读取各qualification和terminal_status，不能只据exit0标金融通过。

## 10. 机器原件示例（字段合同示意，未实现）

```json
{
  "schema_version": "action-evaluation-bundle.v1",
  "trial": {
    "protocol_id": "action-forward-pilot.v1",
    "trial_id": "action_trial_example",
    "registered_at": "2030-01-01T12:00:00Z",
    "starts_at": "2030-01-02T00:00:00Z",
    "ends_at": "2030-01-30T00:00:00Z",
    "settlement_deadline": "2030-01-31T04:00:00Z",
    "expected_opportunities": 1344,
    "source_mode": "synthetic_example",
    "protocol_hash": "<sha256>",
    "primary_baseline_id": "B1",
    "primary_candidate_id": "A1",
    "secondary_reference_id": "B0",
    "baseline_recipe_hash": "<sha256>",
    "candidate_recipe_hash": "<sha256>",
    "scorer_hash": "<sha256>",
    "capital_model": {"initial_equity_usdt_per_asset_scenario_method": 10000, "asset_aggregation": "equal_weight", "shared_capital_pool": false, "new_exposure_cap": 0.25, "new_plan_risk_fraction": 0.005, "inherited_long_start_exposure": 1.0},
    "cost_model": {"fee_bps_per_side": 10, "slippage_bps_per_side": 5, "stress_fee_bps_per_side": 20, "stress_slippage_bps_per_side": 10, "basis": "assumed", "original_refs": []}
  },
  "opportunity": {
    "opportunity_id": "<hash(trial,scope,slot)>",
    "scope_id": "crypto:spot:okx:BTC-USDT:trade:1m",
    "slot_at": "2030-01-02T01:00:00Z",
    "decision_as_of": "2030-01-02T01:00:00Z",
    "scenario_id": "cash",
    "method": "A1",
    "disposition": "actionable",
    "action": "wait",
    "reason": "已登记上破条件，尚未触发",
    "input_manifest_ref": "<immutable_ref>",
    "input_manifest_hash": "<sha256>",
    "known_at_max": "2030-01-02T01:00:00Z",
    "subjective_probability": null,
    "calibrated_probability": null,
    "strategy_success_probability": null
  },
  "plan": {
    "plan_id": "<id>", "revision": 1, "parent_revision_hash": null,
    "published_confirmed_at": "2030-01-02T01:01:00Z",
    "display_receipt_ref": null,
    "state": "armed", "condition_dsl_ref": "<immutable_ref>",
    "expires_at": "2030-01-03T01:01:00Z", "next_review_at": "2030-01-02T01:30:00Z",
    "trigger_true_at": null, "action_eligible_at": null,
    "thesis_refs": ["<claim_ref>"], "counter_refs": ["<claim_ref>"],
    "skill_uses": [{"skill_id": "<skill>", "version_hash": "<sha256>", "loaded": true, "consumption": "not_applicable", "reason": "事件时段不匹配", "changed_claim_refs": []}]
  },
  "execution": {"channel": "shadow_engine", "reaction_delay_seconds": 60, "sensitivity_delays_seconds": [0, 300], "quote_max_age_seconds": 10, "state": "unfilled", "fills": [], "orders_to_broker": 0},
  "outcome": {"state": "pending", "label_known_at": null, "original_path_ref": null, "original_quote_refs": [], "net_pnl": null, "net_R": null, "win": null, "mae": null, "event_cluster_id": null},
  "qualification": {"contract": "not_run", "real_dsh": "not_run", "execution_price_verified": false, "cost_verified": false, "functional": "not_run", "financial": "insufficient", "stable_70pct": false, "promotion_allowed": false},
  "evidence": {"input_hash": "<sha256>", "result_hash": "<sha256>", "source_refs": [], "first_failure_ref": null}
}
```

最终score artifact另包含完整slot census、各状态计数、排除逐项原因、原始逐笔fills/组合路径、全部固定对照、成对覆盖、事件成员、CI参数/结果、资格逐门结果和有限终态。示例时间不表示正式登记；占位hash不能被真实reader认证。

## 11. 明确结束规则

- G/E失败：保留首失败、定位并只重验受影响门；没有代码/输入变化不反复构建同一范围。
- 工程通过：交付真实Plan、持续监控、结算和经验消费及PC说明，不等待统计资格变好。
- A28到期：出有限报告并封存；金融不足不是产品功能未实现，也不能包装成预测成功。
- A180到期：样本/成本/资格不足即insufficient；可靠无增益即no_gain；达到全部门才qualified。候选不晋升仍是完整实验结果。
- 追加市场、来源、费用、DSL、技能或参数：新的版本及必要合同/效果验证，复用未变核心；不得让黄金继承BTC阈值、成交口径或70%成绩。

本轮仅完成评估设计及已有入口核对；未登记试验、运行测试、产生真实成绩或修改旧协议。

## 12. 方法依据与本项目自定门槛

概率评分采用明确事件标签与proper scoring思路，参照[Gneiting/Raftery原论文](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf)；保存所有候选与独立确认集是为控制反复挑选历史赢家，参照[回测过拟合原研究](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。文中A28/A180、成本、覆盖、效用系数和风险边界是本项目拟登记的工程/产品协议选择，不是这些论文证明的最优交易参数。市场非平稳和未观察混杂仍可能影响结论，区间不能保证未来收益。
