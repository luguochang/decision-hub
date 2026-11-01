# Report content audit 59B — 2026-10-11

## 审查范围与原件绑定

本次是人工静态报告内容审查及当前 ROOT 源码定位。仅写入 DEV 文档，未改 ROOT、生产代码或报告原件；未运行 SDK、API、DB、模型、浏览器、构建或测试。结论不包含当前行情或投资建议，也不证明预测正确、风险提前捕获或预测能力已经具备。

| 项目 | 绑定值 |
| --- | --- |
| 审查日期 | 2026-10-11，Asia/Shanghai；下文报告原始时间均为 UTC |
| ROOT | `E:/project/study/codex/dsh/decision-hub` |
| DEV | `E:/project/study/codex/dsh/.market-risk-b2-b4` |
| 当前 ROOT / DEV Git HEAD | `1f5a31da0da012c628737e5fa3bf8a3d04b344f1`；源码含工作区修改，不能仅用 HEAD 代表当前文件字节 |
| 旧 ETH 原件 | ROOT 的 `artifacts/core-goal-20261011/snapshot-restart59b_01/eth-download.private.body` |
| 旧 ETH 原件 SHA256 | `f3c1629fd171f4b21e03afcfb6829515325d91cb4c051dfdb1644bff6211c523` |
| 原件 schema | `research-session-result.v1` |
| request_id | `research-request:run_dbce68caf06e41f799c5a2b5a6d1c8ce` |
| research_session_id | `dsh_72f0095388e4f9e8952c19b49b7a56cd9e56ae83b32758e36d849ccb4b9e7920` |
| runtime / profile | `dsh-web-0.1.2-alpha.2+78e1b06d6652` / `decision-research.web.v1` |
| 原件 started_at | `2026-10-09T19:57:41.924Z` |
| 原件 finished_at | `2026-10-09T20:20:00.372Z` |
| trace_hash | `855b87c647e95a1fcd3ba7af2c1477cfe57d1673a7ba87368c75e6be58a448c5` |

已完整读取 UTF-8 JSON：它有结论、主因果链、反向因果链、独立审查、46 条事实和 14 条证据，并非只有摘要。没有证据表明本次存在正文权限缺失。

报告有用的部分是承认技术与衍生品信号冲突，提供正反因果链、触发与失效条件，保留来源 URL、时间戳和未校准概率标记。以下问题限制其实际使用价值。新 BTC / ETH 报告在本次文档形成时仍待完成及人工实证审查，不能继承旧报告审查结论，也不能以简单关键词脚本判定财务质量通过。

## 十项内容问题

JSON 路径以旧下载原件为准。`excerpt` 和部分 `value` 是 JSON 字符串，涉及内部字段时需先解码；区分真实内容缺陷、覆盖缺口和尚无预测资格。

| 编号 | 分类 | 具体问题、正文路径及短证据 |
| --- | --- | --- |
| A01 | 实际报告缺陷 | **覆盖状态与正文资格未对齐。** `$.final_coverage` 为 `sufficient`、hard/soft 比率均 `1.0`、`gaps=[]`；`$.horizons[1].missing_facts` 却包含 `macro_transmission`、`derivatives_crowding`、`market_narrative`，`$.causal_case.unresolved_questions` 承认四类不足。不能将满足冻结任务的最低采集覆盖解释为全部判断依据充分；仅据原件不能断言 coverage 算法违反了当时冻结规则，确定的是报告表达没有解释两种范围。 |
| A02 | 实际报告缺陷 | **永续盘口被推成现货缓冲。** `$.causal_case.main_chain[1].statement` 写“订单簿快照显示下方挂单缓冲”，并影响“现货回撤幅度”；`$.evidence_candidates[6].excerpt` 的 instrument 是 `ETH-USDT-SWAP`，URL 是 futures order_book。`$.reviews[0].limitations[3]` 明确指出这一边界，最终链仍保留跨市场推断。 |
| A03 | 实际报告缺陷 | **OI 跨场所比较错开窗口。** `$.facts[24]` 是 Gate.io `19:55—20:00 UTC`，`$.facts[30]` 是 HTX `20:00—20:05 UTC`；`$.causal_case.main_chain[2].statement` 使用“OI delta 一增一减”，`$.horizons[2].trigger` 使用“跨场所不再分化”。不同窗口不能直接证明场所分化。旧 review 只说场所和多周期覆盖不足，没有明确识别这两个窗口错位；错位是本次人工审查发现。 |
| A04 | 实际报告缺陷 | **方向、触发状态和概率对象不明确。** `$.horizons[1]` / `[2]` 给 long、概率 `0.58/0.56`，却未定义是终点上涨、达到幅度、先触及上界或其他事件；也未标明触发条件是否已满足。`invalidation` 的“有效跌破”“明显走强”未定义收盘周期或阈值，难以复核。`uncalibrated` 标记是正确边界，但不能替代概率对象定义。 |
| A05 | 实际报告缺陷 | **已有反方，未说明最强反方如何影响最终方向。** `$.causal_case.opposite_chain[0]` 包含低于20日均线、20日负回报、负 basis；`$.reviews[0].challenge` 只接受“有边界的判断”。最终 `$.horizons[1]` / `[2]` 为 long，没有指出首要反方、为何未推翻偏多及提前转为中性的可观测条件。这是判断解释不足，不能证明 long 错误。 |
| A06 | 实际报告缺陷 | **正文未解释异步数据的影响。** `$.causal_case.main_chain[0].statement` 并列 SPX、VIX、US10Y；`$.evidence_candidates[9].excerpt.latest_date` 为 `10/09`，`[10]` 的 VIX 为 `10/08`，`$.facts[44].attributes.observation_date` 的实际收益率为 `10/07`。原始 URL 和时间戳存在，但正文未说明非同时点拼接及其对 horizon 的限制。 |
| A07 | 尚无预测资格 | **无法从现有内容取得未来波动幅度或阈值窗口。** `$.facts[1]` 为历史20日已实现波动，`$.facts[10]` 为历史30分钟波动；`$.horizons[*]` 未定义未来波动区间或 ±2% / ±5% 事件，`$.facts[9].attributes.forecast_qualified=false`。应明示能力边界，不能以历史波动补造未来概率。 |
| A08 | 尚未证明提前性 | **已发生路径风险不能证明提前预警。** `$.facts[9].value` 解码后，下行2% crossing 为 `10/09 11:46—11:47 UTC`，早于 `$.finished_at` 的 `20:20 UTC`；`$.reviews[0].limitations[4]` 引用这一历史风险。正文没有未来风险时点、事前信号和预警有效窗口，不能声称这次风险被提前捕获或未来预警有保证。 |
| A09 | 表达缺陷及数据资格不足 | **杠杆边界不够明确。** `$.reviews[0].limitations[2]` 承认全市场杠杆拥挤 / 去杠杆方向不确定，`$.causal_case.unresolved_questions[2]` 承认缺少主要场所及多周期 OI。最终 long 未清楚交代市场与用途边界，也未说明现货失效价不能代表杠杆仓位的强平风险。现有内容不足以推出安全杠杆结论。 |
| A10 | 覆盖缺口 | **宏观是美国背景快照，全球及未来事件覆盖不足。** `$.facts[35]` 至 `[45]` 的宏观 / 跨资产标的是 US10Y、DXY、SPX、VIX、WRESBAL、DFII10、T10YIE；未给出非美政策、地缘或全球流动性事件链。`$.causal_case.unresolved_questions[1]` 承认缺少预期、实际、修订、发布时间。应明确美国宏观背景范围，不能作为全球宏观风险已充分覆盖的证据。 |

时间边界：这是 10/09 完成的旧快照。截至 10/11，30m / 24h 的 `expires_at` 已过；72h 的到期时间为 10/12，但 `next_review_at` 是 10/10。本文件不能证明复核已发生，也不能作为 10/11 最新市场判断。

## 新 BTC / ETH 报告内容验收的五项必要检查

以下每项需阅读对应报告原件、关键事实和独立审查后作出人工结论，并记录新原件 SHA、分析时点及 JSON 路径。元数据、结构检查和关键词出现均不能替代这一步；未知必须保持未知。

1. **时点与来源可追溯。** 每个核心数值追到来源 URL、标的及现货 / 永续类型、单位、数据窗口和实际观测日期；正文展示分析时点、滞后数据、复核与失效时间。通过依据是核心陈述能按原件复核且没有用采集时间刷新旧观测，过期结论不能呈现为当前判断。
2. **各 horizon 可验证。** 30m / 24h / 72h 分别定义方向含义、触发是否满足及可测量的失效标准；概率有明确事件对象和校准状态。无资格时明确无法提供未来波动 / 阈值概率；不要求为了通过而生成不受支持的数值。
3. **因果链与最强反方闭合。** 标清事实、推断、场景；指出最强反方及其如何改变结论。覆盖范围、缺口、review 限制与 action 一致；永续盘口不直接证明现货买盘，跨场所变化比较有相同窗口与一致口径。通过依据是每个核心推断的证据范围和反证处理能被逐条解释。
4. **风险提前性与宏观范围明确。** 已发生价格路径与未来预警分别表述；存在未来窗口时列出事件时点、证据和事前信号。检查美国、全球宏观及资产特定事件的真实覆盖；缺失就明示。通过不能依赖风险词汇数量，也不能以未来已发生结果倒推事前能力。
5. **技术面与杠杆边界完整。** 技术判断涵盖趋势、动量、量能、历史波动及冲突；衍生品明确场所、funding 周期、basis 幅度、OI 单位与窗口。正文交代拥挤度证据是否足够，以及方向 / 现货失效条件不能充当杠杆安全或强平风险保证。缺乏数据时可以通过诚实限定用途，不能通过伪造覆盖。

## 当前 ROOT 的直接生产入口与校验边界

以下仅定位当前 ROOT 的四个直接源码入口；旧报告运行于 10/09，当前工作区源码不等于其历史生成版本的字节证明。本次未重放或执行模块。

| 环节 | 当前 ROOT 相对路径 / 符号 / 行号 | 静态行为及限制 |
| --- | --- | --- |
| 生产综合及 review 指令 | `packages/runtime_adapters/dsh_runtime/profile.py`，`build_trusted_web_research_prompt` 第239行；review / revision 第280行；`_capability_playbook` 第854、885行 | 要求完整因果链与三 horizon、唯一 frozen reviewer；明确 bounded / rejected 可以形成有限报告。盘口是 SWAP proxy，OI 是短历史窗口；未明确要求两 venue OI 同时窗或禁止由 SWAP 盘口直接证明现货缓冲。 |
| 最终报告映射 | `packages/runtime_adapters/dsh_runtime/result_mapper.py`，`map_session_result_with_witness` 第130、176行；`_attach_attested_review` 第418行 | 独立计算 coverage；causal_case / horizons 直接取 synthesis 原件；随后附加真实 review。审查对象身份匹配不等于 accepted，也不会据 review 自动纠正正文。 |
| coverage 计算 | `packages/kernel/decision_hub_kernel/decision/sufficiency.py`，`assess_evidence_sufficiency` 第46行、`_semantic_assessment` 第248行、`_relationship_assessment` 第324行 | 验冻结 requirements 的 freshness、authority、sources、metric family、fields、units、venue 等；没有 hard gap 即 sufficient。无 baseline / after offsets 时第340行直接返回，非 event-window 多场所 OI 不自动获得共同窗口验证；自然语言限制不直接变成 gap。 |
| horizon / opposite chain 的继续研究校验 | `packages/kernel/decision_hub_kernel/decision/research_loop.py`，`_synthesis_problem` 第225行、`_unhandled_review` 第257行、`assess_research_loop` 第49行 | 检查三 horizon 及 main / opposite chain 引用是否属于 accepted 输入；不证明因果含义或最强反方质量。只有 `requires_new_evidence` review 会作为强制继续的 reviewer challenge；free-form inquiry 保留而不创建新需求。 |

为什么旧意见仍进入 final：旧 review 的 `resolution=bounded`，SWAP 局限被保留为报告审查意见；上述 mapper 保留 synthesis 内容并附加 review，loop 不把 bounded 自动转成重新采集或改写要求。coverage 是最低冻结需求的采集覆盖，不是全部推断充分的证明。因此报告可以同时存在 sufficient、long 研究假说及未解决的正文限制；仅凭本次文件不能声明其正式预测或交易资格已通过。

必须纠正措辞：旧 reviewer 明确指出了 SWAP / 现货边界，但没有明确指出 Gate.io `19:55—20:00` 与 HTX `20:00—20:05` 错位。旧 review 只指出场所与多周期覆盖不足；OI 时窗问题是本次人工读取 facts[24] / [30] 发现，不能编造“review 已发现但被忽略”的历史。

## 三个最小修复点（待新实际结果决定，未实施）

1. **`profile.py` 的综合及 capability 指令。** 明确 SWAP 盘口只说明该永续场所快照；不同 OI 起止窗口不能称跨场所分化。要求报告按五项验收检查解释概率事件、触发状态、最强反方、异步日期、全球宏观缺口及杠杆边界。先用既有 statement / trigger / invalidation / confidence_cap_reason 表达，不新增采集权限、不补造预测或证据。
2. **`sufficiency.py` 的比较资格。** 对确实需要跨场所变化确认的冻结 requirement，使用真实 typed instrument、field、unit、window_start_at / window_end_at 验同一窗口；不匹配时保留事实并标 comparison 不足，不能改写原始时间或补值。只在该 requirement 明确要求比较时影响其 coverage，不能把任意 free-form 不确定性擅自扩成全局 hard gap。
3. **`research_loop.py` + `result_mapper.py` 的内容限制落地。** 把 reviewer 要求补证据的事项绑定精确 requirement ID；bounded 仍保留有限研究原件，但最终报告必须解释 coverage 的范围以及受影响 horizon。修正文案属于新 synthesis revision，不能默认为唯一旧 review 已批准；不能通过关键词判定推断正确或自动改写旧原件。变更是否必要及边界，按新 BTC / ETH 的实际原件人工审查结果决定。

当前新 BTC / ETH 仍待完成及逐份人工实证审查，需保存各自 SHA、分析时点、JSON 路径与 unresolved 判定。生成成功、引用存在、时间合法、审查对象匹配或文字含“风险”都不能单独成为金融内容质量通过。

## 当前 ROOT 四个直接源码字节记录

这些 SHA 是本次从 ROOT 读取的当前文件，防止与 DEV、历史 retained recipe 或后续变化混淆；不是旧 ETH 报告历史生成源码的证明。

| 当前 ROOT 相对路径 | SHA256 |
| --- | --- |
| `packages/runtime_adapters/dsh_runtime/profile.py` | `d35fbfe54d10867b0f2b0f7d0fc640121f6b0b440801fd2f8cceb53b05f9fd42` |
| `packages/runtime_adapters/dsh_runtime/result_mapper.py` | `a465202f163d1188128a510668673e8c4bb10775c0509342070bd219bce5e3af` |
| `packages/kernel/decision_hub_kernel/decision/sufficiency.py` | `925b71dde2f6465a2923d12e18a8716a6f832a4055085f4929d8da789e34ab1f` |
| `packages/kernel/decision_hub_kernel/decision/research_loop.py` | `cc6fd1c01b5ceb5f2312974f4b759254915669956311b01e2fdd30d63d1710a9` |