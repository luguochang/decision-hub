# 原PC预测到期评分V2消费

当前仅D隔离候选，未采用Main、未部署、未形成真实成熟样本或金融资格。八份原Query完整wire定向回归及原UI完整8GB类型检查已通过；独立复审尚待收口。

## 范围与原件

仅修改原`api/perpetualForecastResult.ts`和`product/PerpetualAdvicePanel.tsx`，新增两份定向测试及八份原Query完整fixture。原V1测试断言未改。两生产源起点逐字等于物理Main：API `d934dd39f7a55f35a5a1c705f5ff5486a8570bdef0fb41d6d9c861c3986c70ad`，Panel `27b0c65a6b3653ea676c4461189d22ce50d121f7ce089c055f9d4022f3cd7877`；四文件基线见`temp/perpetual-forecast-scoring-pc/baseline.json`。

首份fixture是原pure Action builder/publisher → ETH24h的1441分钟边界真值 → V2评分持久化 → 原Query分派的完整`{advice,result}`，不是手写缩减wire。源SHA `0f782118f7037e240fb7fe4d34cf6811b92ddb350c127d0d6fd7c3623bac5a04`保存在`temp/perpetual-forecast-scoring-pc/initial-query.v2.json`，不充最终原件。

当前八份逐字原Query fixture位于`apps/decision-desk/src/api/fixtures/perpetualForecastResult.v2/`：六资产/期限组合finite，以及原author在发表前给合法零概率和未知概率的infinite/unavailable两例。来源是`temp/perpetual-forecast-scoring/wire-matrix-01/`和`wire-special-02/`，逐份路径与SHA见`temp/perpetual-forecast-scoring-pc/fixture-provenance.json`。全部使用合成输入，不是真实模型研究或实际成熟预测；没有事后改旧概率/原件来制造正例。

## 行为

- V1保持原严格形状、四状态与4h/unsupported语义；V2单独解析not_due/pending/observed，接受4h/24h/72h。
- 保留同owner/Run/target/hash/发表绑定和时点检查、90秒独立GET时限；不新增POST、采真值或下单。
- V2分层须匹配原资产/期限；三项可靠性观察须逐项等于原冻结概率和实际分类onehot。方向概率未知时只能保留空观察和unavailable。
- Log loss有限值按原十进制字符串显示；infinite明确显示无穷；unavailable显示未评分。只对原概率字符串精确判零核对无穷状态，不截断概率、不重算log loss或Brier。
- 事件族/市场状态原件缺失保留null并显示未知。这里只展示单个样本记录，不生成校准曲线，不填成熟样本为0，不翻转financial/skill/trade标记。

## 已保存验证与待办

- 首轮真实RED：3FAIL/1PASS，两个API正例在旧V1-only解析处拒绝，UI无新增评分展示；见`temp/perpetual-forecast-scoring-pc/red.log`。
- 首轮旧2+新2文件19PASS/11.40秒；原V1的15项断言未改。见`green-first.log`。
- 追加真实协议RED：原实际结果概率为0.565却声称infinite仍被接受；精确零状态核对后新API四项4PASS/9.95秒。见`red-infinite-binding.log`和`green-infinite-binding.log`。
- Root只读指出的负数下溢经完整wire验证为真实RED：只改`log_loss.value=-1E-1000`仍被接受。仅V2新字段改用原字符串精确符号/零判断，保留-0/0e合法，旧V1 decimal规则未改。随后新API5PASS/10.48秒，原件为`red-negative-underflow.log`与`green-negative-underflow.log`。
- 当前完整八wire矩阵及原V1回归共4文件30PASS/11.69秒，见`green-matrix.log`；原15项断言未改。新增15项覆盖六组合和三个评分状态、原件绑定、概率/onehot联结、越域及UI忠实展示，不声称通过六组合真实研究。
- Root最终独审发现的unknown/Brier联结已真实复现：原unavailable完整wire只将Brier改为`0`仍被接受，`red-unknown-brier.log`保存1FAIL/12deselected。只在V2原概率为null时要求Brier也为null，未改V1或重算评分。修后完整4文件31PASS/10.71秒，`green-matrix-r2.log`；八份原wire及旧15项断言不变。
- 首轮完整原UI类型检查未显式设8GB，Node22900自然退出134，V8约4GB heap OOM；原`tsc-first.log`和`tsc-first-terminal.json`保留，不计类型通过，未人工终止。按Root调度等待正在运行的完整normal终态，再用原8GB预算最终检查，避免并行大编译。
- Root normal真实终态后，唯一最终原UI全范围`tsc --noEmit --incremental false --pretty false`以8GB运行，原handle66367实际exit0/147.783秒/0诊断；原tsconfig与Main逐字同SHA `b940cdd21a76f49229374813d5e6127e99c1565c9ed3c2df6e025fef01630710`，strict未放宽。保存`tsc-final.log`与自动生成的`tsc-final-terminal.json`。已绿30项按Root指示未重复运行；之后只补擦除后的测试tuple类型标注，最终完整tsc包含该最终源。
- 上述147.783秒对应unknown/Brier修复前版本。最新修复后另一次必要的同原配置8GB全UI类型检查，原handle26990实际exit0/145.898秒/0诊断；见`tsc-final-r2.log`和`tsc-final-r2-terminal.json`，不沿用旧通过充当新源证据。
- 独立复审待补；当前不提交或计验收。

所有实施与测试写入仅D候选/私有临时目录；Main/正式库/07/冻结包未改，无真实HTTP、模型、服务、真值抓取或PAPER执行。
