# 原预测评分与学习链增量候选（D隔离，未采用Main）

计划见 INCREMENTAL_FORECAST_SCORING_PLAN.md。本切片补原BTC/ETH × 4h/24h/72h发表预测的真值、持久评分、原读端与下一Task/ProviderInput数据链；不是T701–T703、四周评分、金融或总体交付完成。Main、正式DB、四个v1 software输入未写；没有真实HTTP行情、模型调用或成熟真实样本。

## 行为与版本

- 原normal composition一个构造点显式选择scoring_version=v2，直接构造runtime仍默认v1。两版本均跳过已有任一版本评分，不写双版本标签。
- 新truth codec okx-perp-forecast-truth.v1独立拥有0–14页身份，复用原capture/archive、OHLC与parse-cache原语。4/24/72h分别要求241/1441/4321个完整冻结边界分钟，最多1/5/15页。未到期零请求；缺页、重复、错资产、游标断链、未来或未确认分钟均不能填标签；重试继续原RETRY_SECONDS。
- experience.v2存原概率、Brier、logloss及各类probability/onehot。零概率为显式infinite，不clip；原概率未知为unavailable、Brier=null、可靠性=[]。strata保真实BTC/ETH/期限，event_family/market_regime=null并列缺口，不造分类统计。
- 原owner/publication鉴真后投影result-view.v2，仅not_due/pending/observed。新V6或显式v2 due claim决定新版本；原v1原件优先，由原物理reader精确读取，不补算logloss。新v2 reader按原created_at重建publication、capture/archive、method及真实software SHA，外层重封不能改分数。
- 新经验由原Worker prepare/freeze入口进入Task；含新来源的selection/provider-use显式v3。Task选择不等于模型采用。原ProviderInput确含完整原prompt且作者因果链引用原目录才记referenced；skill_consumed、financial_qualified、trade_outcome仍false。
- v3旧receipt的初查和并发old均经过原prepare_originals物理快照，核row身份/时点/outer，按原recorded_at重派完整Task/Input/score/causal并全body相等，返回真正存量A；并发发现存量时先无写退出writer事务再重派。旧v1/v2路径不变，不放松provider snapshot/auth门。

## 验证终态与限制

所有证据在 temp/perpetual-forecast-scoring，以下独立执行不能相加冒充一个全仓suite：

| 边界 | 日志/结果 | 实际终态 |
|---|---|---|
| 七focused文件联合：数学/truth/collector/六组合SQL/legacy/normal/学习队列 | final-focused-01.log | 55 passed in 349.45s，handle30516 exit0；在后来snapshot修复前 |
| 两资产三期限完整原发表→SQL评分/read/Query及v1原件保真 | wire-matrix-01.log | 8 passed, 2 deselected in 260.51s，exit0 |
| 原builder发表zero/unknown→SQL→Query | wire-special-02.log | 2 passed, 6 deselected in 99.20s，exit0 |
| normal 72h未到期/15页/幂等与v2 pending版本 | green-v2-pending.log | 2 passed in 74.54s，exit0 |
| 实际v2学习输入跨owner/asset/horizon/未来cutoff拒绝 | learning-scope-01.log | 1 passed, 7 deselected in 57.10s，exit0 |
| 最终snapshot契约/重封/原时点/并发存量及原纯边界 | green-pure-snapshot-final.log | 49 passed in 4.81s，exit0 |
| 实际已存两v3原件，原时点重派并校输入联结 | actual-v3-prior-ro.log/result.json | handle95190 exit0；255条仅BEGIN/SELECT，SOURCE101/TARGET198全文件SHA同 |
| 实际not_referenced原件只在内存backup改为referenced并重封 | actual-v3-prior-selfseal.log/json | handle6848 exit0；拒绝v3_prior_original_facts_changed，磁盘两目录全SHA同 |

新六源与新十测试按物理Main配置严格类型均0；五原接线源基线140→119、零新增，不是整仓strict0。静态source-only overlay的其余application/ports为物理Main逐字拷贝，其他依赖为只读junction，动态测试保实际D.__file__。types-main-snapshot.json/types-tests-snapshot.json有据。Ruff新源/新测试0；五旧源Main已有18 I001/25 E501，候选保同数量。未跑无关整仓suite/build。

## native与同Run继续的真实边界

Root原normal47376 exit0/postflight errors[]后，下面三次新测试均真实exit1，不能写成单次native测试exit0：

1. fresh-native-01，83552，23.33s：新test在fixture构造后重装同字节Pydantic schema，类型身份不同触settings_head_changed，Graph前停止。native-loader-class-identity-red.json纯内存证明source SHA/model_dump同但class/==不同；仅修collection装载顺序。
2. fresh-native-02，64877，104.29s：原同步builder调用asyncio.run，被新test外层async pytest loop冲突；改新test同步、只异步段asyncio.run，原builder/门不变。
3. fresh-native-03，22063，214.02s：原synthetic native、V6 Commit和完整SavedReport已成功，交接新test用model_validate(dict JSON)被严格datetime拒绝；原source的model_validate_json精确roundtrip通过，证据native-graph-ref-json-mode-proof.json。没有再次运行producer。

新test已修JSON交接并提取postCommit helper。Root与C独立窄审允许后，只将03静止无WAL的101份原件逐字copy到native-continuation-01，旧Run/Task/ref/archive_root不改。原SOURCE/raw只历史读，新DB/captures/history只写TARGET；prepare SHA b639c769…555f，driver797da502…1128及handoff06c798e…894e在执行前已peer C0/I0/M0。

唯一continuation handle99962实际exit1并保原log：原V6→原Action publisher→due observed→下一Task/selector/原ProviderInput两次真假v3 emission的全部helper断言已经过，最后实际prior回读触research_provider_physical_snapshot_required。它暴露新v3 reader直接live-session derive违反原合法快照门，不是缺原件；未重跑publisher/due/Run/model。修复后只做上表真实RO与内存反例，均exit0。不能把这些分段证据改称完整native test单次GREEN。

原SOURCE101 SHA前后同，DB为a160f01f17c870221dc57daf14d43a7e65de7c32b00e513eeb86796251056386；TARGET闭环后198文件亦在最终RO审计全SHA同，DB633acf0302e7f7dc34d7e18692682de70dc34d65809c0717b61a7d46039c0667。原sourceJSON在copy中也逐字核同。

## 实际联结原件

native Run run_55518c63d9f0489aafbed42ddeb22b6e，artifact art_027ebdf4766b4571b76b4e16a5414e8a；committed-source.v6.json为complete。发表后原Query产物在native-continuation-01/native-scoring-result.v2.json：

- experience_ref perp_experience_8fd68debba87290f3bb23505c3b1127cbafc4620b7155de08e23b427087411fb。
- final_target publication_target_e70f578ef544ded7912fbd82cd58ca1760a1489b6a4de407fda285dbb1457e6e。
- frozen_forecast perp_frozen_forecast_61e435d89e4dff28e25f6768198ba02bd0816b7bfa033f78f2c2eb7b59bac411。
- 下一Run run_0c1acef305d34f0cacf84a0a7514e6ca的同份Input model_input_b99acdda8a0ce4a74072d9366e0df8b97dc01ab5，两个emission model_emit_0d4fca4f7f001fc1c6c89686094a791e09201dac / model_emit_9882f56f63b34df6c7f12f62b7731bc9f8fd7aee，分别referenced/not_referenced。

actual-v3-prior-ro-result.json（SHA7ce271969cf6fed171346189a4d553780e7af6d404c19bd13fa5f037a48e6a83）保完整refs/hash：raw input_json/input_hash、完整prompt_hash、Task hash、experience_ref与emission.input_ref实际闭包。没有新增人工nonce，也不以nonce文本出现推定使用。原服务确实接收/保存这些synthetic authored ProviderInput原件，但没有真实LLM调用或认知/效果证明；两笔skill/financial为false，评分trade_outcome亦false。

## 非作者问题与保留失败

首次peer指出I1：新v3沿旧prior外层快捷路，能自重封uses/skill；并发可返回未存B。red-v3-prior-proof五FAIL及red-v3-prior-race一FAIL→green-v3-prior-proof十二PASS。真实continuation又发现该修复缺物理snapshot契约，red-v3-physical-snapshot六FAIL→green-v3-physical-snapshot十二PASS4.01s；最终真实RO与内存重封已证明合法门保持。最终use.py SHA1397854ac2f319979e0612080529d20044d30b60a0fe037898f027e2d43efd24已非作者C窄复核C0/I0/M0，其他10生产源亦完整独立C0/I0/M0；旧direct-live版本的peer不用于证明本修复。

其余业务RED保red-metrics/truth-codec/collection/runtime/learning/software/use-queue/v2-pending/mixed-version-queue等日志。first-store的ETH position仍BTC与wire-special-01的作者概率分组/unknown path输入不合法均为新fixture错误，修测试作者输入，不冒生产数学算法RED。首次静态测试namespace配置错误已停止，types-tests-01不是有效结果。所有FAIL不覆盖。

## 范围与交付状态

后端白名单11生产（6新+5原接线）+10新focused测试/支持+本报告/计划，最终23pin scope另存；C的PC13文件已独立提交c65914167dfcaf75cfd5753b0422f641bd1168c5，本文作者未改其两生产路径。无stage/Main写或新真实Run。

新模块均小于400行；新scoring鉴真/事务投影存在79/112/68行函数，inputs/view完整投影68/73行，原接线更大，属于已明确记录的60行工程偏离：保原鉴真/事务可核结构，不挪旧hash输入伪装兼容。局部通过不冒全规则PASS。

此切片持久化可靠性曲线所需逐样本事实；没有四周成熟样本、校准曲线、事件族/regime统计或PAPER收益，也未改变14自然日、四周、金融/PAPER与最终质量门。最终23pin scope/report封存，本地提交以temp/perpetual-forecast-scoring/commit-receipt.json为准；Main精确采用/采用后验证另由Root协调，ETH08运行时不得采用或构建，不自行标正式ACCEPTED或总体完成。
