# 批次17候选与已确认的后续缺口

更新时间：2026-10-08T14:33:55.154839+00:00。正式17标准构建/Ready P，真实BTC首次研究F无报告；官方深冻结ABI不兼容已确认，ETH同条件未提交。O04唯一100完整投影P、p95=0.609s F。目标 active，PC限定；18修复与能力说明进行中，整产品/金融效果未通过。

## 已完成的候选修复

| 修复 | 代码验证 | 尚需真实验收 |
|---|---|---|
| 源码重复AST纯计算复用 | 4个有限控制及静态检查通过，保持每次新鲜源码/SHA/安全门 | 新版本真实callback在原15秒内完成 |
| 首页约700次元数据查询 | 同事务批量元数据；每原件仍新鲜读取/hash；4MiB有界纯解析行 | 修复部署后唯一100次完整摘要，p95<0.5s |
| 根审核格式错误 | 同一个审稿child最多一次格式反馈，原错误与合法修正均留原件；旧兼容首件不能绕过V2链 | 实际根审核被综合消费及四项影响 |
| 综合前证据正文被prune | 17有限5控制/TS通过，但真实SDK深冻结options必拒；控遗漏官方冻结ABI。18迁移到官方pre-step，原控P不覆盖实际F | Kernel原件反核3有限控制已通过，正式接线已部署；新真实Run待验证 |
| 第二现货能力被策略拒绝 | 现货需求显式fallback，非空spot-only字段；真实Factory/Preparation→Gateway→Router私控通过，篡改/异资产/衍生字段拒绝 | 真正研究取得OKX+第二现货并形成独立覆盖 |
| 全Run用量与PC误述 | 可信coverage DTO与未知总量；完整报告/保存阶段/首页未读反方文案候选 | 实际child分母还缺，不能称全量已对账；UI编译已通过；受影响PC待完整验收 |

真实供应商可用性：Gate ETH一次公共GET为200且品种/价格/量可解析；Huobi一次ConnectTimeout，无重试。见[实际探测](../../../artifacts/market-risk-implementation-20261007/B2/spot-independent-route17/public-probe01/receipt01.json)。私控运输不是供应商全正常证明。

16b两真实Run的251份原recipe材料已文件式捕获保存，2个实际selection保持，数据库写入/模型调用均0；见[保留回执](../../../artifacts/market-risk-implementation-20261007/B2/release17/capture-execution01.json)。24生产+6控制合批晋升、标准发布和Ready已执行通过；新双Run仍待完整结果。原件见[promotion01](../../../artifacts/market-risk-implementation-20261007/B2/release17/promotion01.json)与[Ready](../../../artifacts/market-risk-implementation-20261007/B2/formal-start17/receipt01.json)。

## 新确认的产品能力缺口

### 冻结训练0样本

[真实只读原件](../../../artifacts/market-risk-implementation-20261007/B3/training-feasibility17/actual01.json)确认两模型有效训练窗口均0；90历史窗口没有完整标签。当前每资产2556真实分钟，远少于最低60日，条件组要求30。fit job succeeded仅说明冻结原件保存，不表示模型ready。固定fit不在28日内重训，因此等待28日不能修复这项能力；100独立日资格也不能由相关资产或重复窗口补齐。

当前试验与已发预测保留，不回填、不续开。下一步首先给用户显示training_unavailable、0/60与组0/30，未知概率不得变0。下一协议须明确historical observed训练的information_asof在当前时刻，历史PIT验证仍按当时信息；只有真实来源、完整连续历史、有限探针与ready门通过后，才能开始另外的未来试验。详细有限方案见[训练可行性和复查方案](../../../artifacts/market-risk-implementation-20261007/B3/training-feasibility17/REVIEW01.md)。这部分尚未实现，不能用台账条目数宣称有预测作用。

### 自动复查容量

实际rolling24h为automatic10+scheduled2=12；其中8条旧manual触发历史被记录为automatic，旧费用/分类不回写。当前服务器owner命令已有可信manual路线，固定source_id名称不能证明Run的admission_origin。manual另9不占这12。

候选18方案在原总12内为复查保留容量，普通自动最多6、scheduled继续总12；hour2/并发/窗口/幂等不改，失败仍计数。review caller必须传scheduled_recheck。普通自动吞吐由12降6是明确代价，保留容量不保证每个资产或每个窗口成功；已满12不能即时绕过。原30m过期child=null保留。候选18未写正式/未部署，真实自然父计划→child→完整报告仍待通过。

## 执行顺序

1. 已完成17 Kernel保护正文反核3有限控制和完整封签列表。
2. 已标准停止/保存原件/24+6合批晋升，标准构建及Ready各一次通过，原guard与2MiB不变。
3. 17实际BTC已一次F；ETH不重复同一SDK坏条件。18官方pre-step候选控制/标准发布完成后，BTC/ETH各一个真实Run；终态report/quality/context各一次，核同Run附件、正文、质量、反方、PC。
4. 修复后正式O04有限100次；未变具名PC范围复用，未测按钮不称全验。
5. 18优先实际复查容量、全child独立分母与训练ready说明；再按新历史训练可行性方案推进，固定旧试验不私改。

全部33项仍见[改造清单](IMPLEMENTATION_CHECKLIST.md)。每一项以源码/原件/真实状态为准，控制通过不是金融有效证明。

## 17本次实际失败与18方案

正式100次O04全200/完整投影P，p95=0.6090408s F，SQL已降16；不重复同条件性能验收。17 BTC研究在provider-input持久化前失败，原native17行/seq15和完整SDK ABI原件保留；真实SDK深冻结请求，原新增mutable options假设错误。18官方pre-step消息接入与冻结请求只读guard开发中，不声明已修复。训练就绪3生产候选/4有限控制P，复查容量2生产候选/8控制P；全child独立census与新版缺census负门候选进行中。下一次真实运行以合批后SDK门通过为前提。全部精确证据见[当前一页纸](CURRENT_FOCUS.md)。
