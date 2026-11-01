# 现行合同映射与 Q05 有限执行入口

状态：DEV有限工程控制已执行，20个唯一case最终20P/0F/0 unavailable，93份actual调用输入在函数执行前封存。首轮工具setup profiler超时18P+2 unavailable永久保留；工具适配后仅补2项，未重跑18P。未调用Provider、正式API/DB、浏览器或标准构建；未改产品/测试源或正式文档。完整Q05市场/两方法验收仍未执行，model_understanding_verified=false、two_method_real_comparison_executed=false、financial_qualified=false。

## 1. 设计别名如何对应现行合同

TECHNICAL_DESIGN第303行的七个名称是设计口径，不要求再创建七套同名对象。应按下面实际class/schema_version/protocol_version/report_version消费，区分kernel内部合同、不可变存储原件与对外生成DTO。源码或GET存在不等于正式consumer/真实报告通过。

| 设计别名 | 当前实际名称/版本 | 实现与合同入口 | 兼容/升级边界 |
|---|---|---|---|
| RiskPredictionV1 | RiskPathForecast / risk-path-forecast.v1；FrozenPathForecastModel / frozen-path-forecast-model.v1 | [ports/path_forecast.py:178](../../../packages/kernel/decision_hub_kernel/ports/path_forecast.py)、decision/path_forecast.py、RiskPathLedger.publish_pair | forecast外层仍V1，模型/试验参数版本独立；不把V2试验名改写成旧forecast V2，不授实验频率calibrated资格。 |
| RiskPathObservationV1 | PathObservationReceipt / path-observation-receipt.v1，内嵌PathRiskResult / path-risk-result.v1 | [ports/path_forecast.py:271](../../../packages/kernel/decision_hub_kernel/ports/path_forecast.py)、[decision/path_risk.py:29](../../../packages/kernel/decision_hub_kernel/decision/path_risk.py)、risk_path_ledger标签保存/原件重读 | 固定cutoff+1min、24h、label_known_at/originals；缺首bar不移窗口，不回填预测。 |
| QualityDecisionV1 | 同名 / quality-decision.v1，kernel frozen Pydantic合同 | [decision/research_quality.py:110](../../../packages/kernel/decision_hub_kernel/decision/research_quality.py)、quality_input_from_state、research_quality_sidecar/view | 不另造ports/research_quality.py。ResearchQualitySidecarV1/research-quality-sidecar.v1与ResearchQualityViewV1/research-quality-view.v1分别是存储/查询合同；publication V4不重标旧decision。 |
| ResearchChallengeV1 | 同名 / research-challenge.v1，kernel合同 | [decision/research_quality.py:28](../../../packages/kernel/decision_hub_kernel/decision/research_quality.py)、research_quality_projection.py | 旧context/challenge未提供的新认证/effect字段不凭默认值注入重签；test_old_context_and_challenge_payloads_keep_their_wire_hash是兼容控制。 |
| RiskConditionV1 | 同名 / risk-condition.v1；RiskComparisonV1/RiskMetricV1受限DSL | [ports/risk_monitor.py:27](../../../packages/kernel/decision_hub_kernel/ports/risk_monitor.py)、application/risk_monitor.py | fraction是内部单位，PC转百分比；rule/plan scope保留。拒任意表达式，unknown不等false，不覆盖旧episode。 |
| RiskSummaryV1 | RiskSummaryView / market-risk-summary.v1；TS riskSummaryViewSchema/RiskSummaryView | [market_risk_view.schema.yaml:96](../../../contracts/schemas/market_risk_view.schema.yaml)、[generated/market_risk_view.py:116](../../../packages/contracts_py/decision_hub_contracts/generated/market_risk_view.py)、[api/risk.ts:44](../../../apps/decision-desk/src/api/risk.ts)、query_views/market_risk.py | 新scope独立；旧研究API不塞风险字段，无risk对象/资格保持unknown。摘要不能代完整质量核验。 |
| RiskEvaluationReportV1 | ledger不可变报告dict：path-shadow-evaluation.v1/v2，内含path-shadow-score.v1/v2；API ScoreReportV1/ScoreReportV2 union | [risk_path_ledger.py:56](../../../packages/kernel/decision_hub_kernel/application/risk_path_ledger.py)、[path_forecast_score.py:259](../../../packages/kernel/decision_hub_kernel/decision/path_forecast_score.py)、[risk_paths.py:422](../../../apps/hub_api/risk_paths.py)、api/riskPaths.ts | report_version与score版本必须匹配。旧V1不注入V2 CI/alarm字段；V2 alarm_originals/alarm_score_original读exact原件，不以latest替旧侧车。 |

### 按实际版本轴读取

| 版本轴 | 当前入口 | 保留规则 |
|---|---|---|
| 试验/模型参数 | PathForecastParametersV1/path-shadow-24h.v1；PathForecastParameters/path-shadow-24h.v2；parse_path_parameters、RiskPathLedger.protocol | 从stored版本核exact fieldset/hash。旧V1 CI not_registered，新V2固定paired UTC-day bootstrap/0.95/seed271828/2000次。 |
| 试验登记 | risk-path-shadow-trial.v1/v2 | 与parameters版本匹配，registered_at<first_cutoff。特征latest closed≤cutoff、actual known≤cutoff、delay≤180s、31连续closed bars；不回写known_at。 |
| 成绩 | path-shadow-evaluation.v1/v2 + path-shadow-score.v1/v2 | V1原JSON不改；V2不足CI null/insufficient，100独立UTC日/30事件不能由28天强行满足。固定28日不足或无增益有限结项，不续开。 |
| review | 历史independent-review-receipt.v1保旧Host/Harness；live IndependentReviewReceiptV2/independent-review-receipt.v2 | [independent_review.schema.yaml](../../../contracts/schemas/independent_review.schema.yaml)是当前V2生成合同；ResearchReviewEffectOriginal/research-review-effect.v1是另一原件版本轴。缺真正effect原件不提升旧公开review标签。 |
| 实际输入/递交 | ProviderInputOriginal/research-provider-input.v1；ProviderToolEmissionOriginal/research-provider-tool-emission.v1；ProviderFinishOriginal/research-provider-finish.v1；ResearchContextDelivery/research-context-delivery.v1 | [ports/provider_input.py](../../../packages/kernel/decision_hub_kernel/ports/provider_input.py)：exact input→call/emission→finish→delivery reader。proof_scope=dsh_llm_generate_options，只证明SDK边界；不补造模型理解或美元费用。 |

Task/Method/recipe/install身份及旧reader保留。源码更改不等于Host生效。MR18真实automatic/事件/定时子Run及幂等不能由manual按钮替代；MR28五类actual writer消费+scope/cutoff+input/output hash+audit/pointer+回滚原件不能由注册/GET替代。

## 2. Q05现有可复用控制与尚缺原件

固定20个具名工程控制，输入全部为constructed_control；D类亲写读私库，但Host/native正文仍为合成。实际冻结索引/expected断言/protected字段原值/source SHA/返回值见[执行说明](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/README.md)、[20输入索引](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/frozen-input-index.json)、[完整分母回执](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/execution02/receipt.json)。93次调用是20case的子输入，不能变成93个独立样本。

表内“工程P”只指这批具名控制+actual输入捕获通过；不表示20个真实市场争议、模型理解、20个真实分析结果或两方法对照通过。每项原expected与断言源码都在执行前冻结；实际函数参数/DTO正文在call前以独立JSON保存，source/hash与必保槽原值同封。没有用actual输出反填expected。

| 候选 | 输入/变体 | expected | protected field/语义 | 精确当前控制 | 证据/待执行 |
|---|---|---|---|---|---|
| Q05-C01 | coverage齐全、critical liquidity仍open | continue/blocked/research_only，不能sufficient | severity/blocking_scope/evidence/status | [Q:111](../../../tests/market_risk/test_research_quality.py) :: test_complete_coverage_does_not_override_open_critical_challenge | 工程P；市场/方法未执行 |
| Q05-C02 | 仅path_risk被反证阻塞 | direction eligible、path blocked、partial | blocking_scope；方向与路径资格分开 | [Q:137](../../../tests/market_risk/test_research_quality.py) :: test_tail_only_challenge_does_not_erase_supported_direction | 工程P；市场/方法未执行 |
| Q05-C03 | 预算unknown且coverage齐全 | unresolved，不继续，不把未知费用填0；旧pure控制不替新阶段权限裁决 | budget_state/reason_codes/blocks_completion | [Q:146](../../../tests/market_risk/test_research_quality.py) :: test_budget_and_runtime_end_never_become_sufficient_even_with_full_coverage[unknown] | 工程P；市场/方法未执行 |
| Q05-C04 | 预算exhausted；同函数另有timeout/failed变体 | 不充分、不继续、research_only；变体不增加独立输入数 | budget_state/deadline/publication_mode | [Q:146](../../../tests/market_risk/test_research_quality.py) :: test_budget_and_runtime_end_never_become_sufficient_even_with_full_coverage[exhausted] | 工程P；市场/方法未执行 |
| Q05-C05 | retained反方缺失或fidelity=false | context incomplete、unresolved；全文保留 | strongest-counter/required/retained refs；数字与时点 | [Q:153](../../../tests/market_risk/test_research_quality.py) :: test_context_must_retain_counter_and_critical_semantics | 工程P；市场/方法未执行 |
| Q05-C06 | 本批精确deferred/accepted，无resolution basis | 缺resolution basis仍open，不充分 | disposition/resolution_evidence_refs/result hash/effects | [Q:167](../../../tests/market_risk/test_research_quality.py) :: test_relabelled_challenge_without_resolution_basis_stays_open[deferred-accepted] | 工程P；市场/方法未执行 |
| Q05-C07 | 有效resolution vs旧hash/外Run引用 | 仅当前result+accepted refs+effects允许解决 | result hash/accepted refs/direction与tail effects | [Q:175](../../../tests/market_risk/test_research_quality.py) :: test_answered_challenge_requires_current_result_hash_accepted_refs_and_effects | 工程P；市场/方法未执行 |
| Q05-C08 | 原生review要求补证但无结构化映射 | unmapped review保留，direction blocked/unresolved | review_id/challenge/unresolved_challenge_ids | [Q:202](../../../tests/market_risk/test_research_quality.py) :: test_unmapped_native_reviewer_cannot_disappear_behind_coverage | 工程P；市场/方法未执行 |
| Q05-C09 | 外引用、反方为空或key risk缺失 | report incomplete、不sufficient | evidence/opposite_chain/key_risks | [Q:217](../../../tests/market_risk/test_research_quality.py) :: test_untrusted_citations_missing_risk_or_counter_do_not_publish_complete | 工程P；市场/方法未执行 |
| Q05-C10 | 长全文、paused、账户/场外未知及未批准来源 | 8000字片段/hash不变，paused/unknown保留；长度不证明质量 | unit=USDT/BTC/timestamp/full bytes/unknowns/monitor | [Q:250](../../../tests/market_risk/test_research_quality.py) :: test_full_report_refs_unknowns_and_monitor_state_are_preserved_without_truncation | 工程P；市场/方法未执行 |
| Q05-C11 | 模型自造取证路线或重绑report hash | undeclared_route/hash_mismatch拒绝，不扩权限 | declared/executable requirements/source_result_hash | [Q:266](../../../tests/market_risk/test_research_quality.py) :: test_new_free_form_need_cannot_grant_route_and_hash_cannot_be_rebound | 工程P；市场/方法未执行 |
| Q05-C12 | 可执行需求存在但no_progress | 不重开无进展循环，partial | no_progress/should_continue/bounded状态 | [Q:273](../../../tests/market_risk/test_research_quality.py) :: test_no_progress_does_not_reopen_a_bounded_loop | 工程P；市场/方法未执行 |
| Q05-C13 | max round或真实deadline到达 | round_budget/deadline，不能充分 | current_round/max_evidence_rounds/assessed_at/deadline | [P:204](../../../tests/market_risk/test_quality_projection_v3.py) :: test_actual_round_limit_and_deadline_never_report_sufficient | 工程P；市场/方法未执行 |
| Q05-C14 | accepted证据实际received晚于cutoff | 该引用排除，unresolved/research_only | received_at/pit_cutoff_at/retained refs | [P:258](../../../tests/market_risk/test_quality_projection_v3.py) :: test_accepted_label_does_not_allow_evidence_received_after_decision_cutoff | 工程P；市场/方法未执行 |
| Q05-C15 | 本批精确执行前自报正文已送达 | preexecution_claim_forbidden | authenticated_review_ids/authenticated_retained_context_refs | [A:41](../../../tests/market_risk/test_authenticated_quality_context.py) :: test_creation_cannot_claim_future_review_or_delivered_body[authenticated_retained_context_refs] | 工程P；市场/方法未执行 |
| Q05-C16 | report挑战只有monitor effect | report仍阻塞；真实report effect是独立门 | blocking_scope=report/effect_on_report/monitor/result hash | [A:46](../../../tests/market_risk/test_authenticated_quality_context.py) :: test_monitor_effect_does_not_resolve_a_report_challenge | 工程P；市场/方法未执行 |
| Q05-C17 | 动态正文proof与result/manifest/fidelity错配 | 只exact绑定可增加retained；错hash不complete | bound_result_hash/selection_manifest_hash/authenticated refs | [A:65](../../../tests/market_risk/test_authenticated_quality_context.py) :: test_dynamic_body_proof_counts_only_for_the_exact_bound_manifest_and_result | 工程P；市场/方法未执行 |
| Q05-C18 | 无新增问题review的原件有/无/旧binding | 仅真实authenticated empty review可无挑战 | authenticated_review_ids/review created_at/result hash | [A:98](../../../tests/market_risk/test_authenticated_quality_context.py) :: test_authenticated_empty_issue_review_and_missing_original_are_distinct | 工程P；市场/方法未执行 |
| Q05-C19 | 本批full root wrapper：缺effect/合法effect/篡改effect | 仅effect原件+exact root完整wrapper覆盖；model_understanding=false | root/execution/dispatch/call/input ref+hash/review effect ref+hash | [D:126](../../../tests/dsh_native/test_provider_review_delivery.py) :: test_exact_root_review_wrapper_delivery_or_missing[full] | 工程P；市场/方法未执行 |
| Q05-C20 | 初始prompt只有控制前缀、正文或source被改 | missing/changed拒绝；ref-only不能完整送达 | initial sections/excerpt/source_id/UTF-8 bytes | [D:234](../../../tests/dsh_native/test_provider_review_delivery.py) :: test_actual_initial_prompt_codec_rejects_missing_or_changed_bodies | 工程P；市场/方法未执行 |

压缩附加控制（仅静态引用，本批未重跑、不增加20输入数）：[test_spot_context_excerpt.py:103](../../../tests/market_risk/test_spot_context_excerpt.py)::test_large_eth_context_has_complete_bounded_summary_and_immutable_originals。216个capture hash原件不改，excerpt≤4000，summary保scope/count/manifest_hash/semantic_hash/capture_hashes_hash/original_ref，qualification=unknown。它证明摘要/归档边界，不证明Provider实际看见或理解所有原件；不能替C20的actual initial section检查。

fixture边界：Q的report/quality_input/full_input/challenge是人工pure合同，attestation:test和fidelity=True不授权生产；P的material是固定请求/result/coverage/预算投影；A的认证标志是pure输入，真实认证由D的Server writer/reader另证。D(inputs/active)亲写读临时SQLite和同physical只读Session，但native notification/模型输入是合成，不冒正常DSH；S(prepared/_ingest_216_originals)是受控采集归档，不授金融P。

## 3. 单命令工程入口与真实Q05边界

开发根执行下列单命令。输出必须选不存在的新attempt；工具拒绝正式根执行和覆盖输出。选择固定20items；总门240s、无自动重试，不扩套件、不调用Provider。

~~~powershell
Set-Location -LiteralPath 'E:/project/study/codex/dsh/.market-risk-b2-b4'
& .venv/Scripts/python.exe artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/run_q05.py --output artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/execution-new01
~~~

这批实际首轮20items全部收集：18P后fixture准备阶段的工具profiler导致240.122s上限，2项unavailable；execution01及其SHA核对重建的原工具bytes不覆盖。唯一工具适配把捕获移到test call期并先过滤函数名，execution02只补C19/C20：2P/36.08s，总进程38.263s，source pins未变；18P带原回执/原输入引用复用。完整20分母XML为[collated JUnit](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/junit-20-collated.xml)，明确由实际pytest phase reports合并；execution02/junit.xml是原生2项XML。不得写成一次原生20项JUnit或隐去首轮RED。

仍未完成的真实Q05：20条真实冻结争议材料、同输入/cutoff/权限的两方法真实对照、模型理解验证。后续只在真实报告稳定后按20材料固定分母登记，保actual known_at、Task/Method/selector/profile/预算身份、主反/数字/单位/时点/挑战/effect原件。未知费用不填0；引用或SDK实际递交不升格模型理解。无材料或方法时有限关闭为未执行，不以重复工程P代替。

## 4. 原目标的关闭边界保持

- MR16/17、A11–14/Q05：本文补映射，主要产品新报告优先；pure/历史P不替实际动态正文、ReviewV2或真实同Run报告。
- MR18/A19：自动触发、报告建议→durable复查→同scope子Run及幂等仍需实际正控；manual不充数。
- MR28：原件/成本/样本insufficient与无handler未实现分开；实际5 consumer/CAS/rollback仍需对应原件。
- MR31/32、A26/G14：保留来源/资料/历史/反馈/Inbox/Operations/Evolution/delivery；已验nonce/普通恢复复用，剩余具名PC独立；本文不是浏览器P。
- U01–03：3名非开发人员/5任务≥80%/关键误解0；无人时明确未执行，不把开发截图授用户验证。
- Q02–04及MR05/24/25/29/30：28日有限close、100独立UTC日/30事件、public/vintage/crowding/consensus/gold外部不足保持，不回填/续开；不足不伪晋级。

## 5. 冻结来源与回执

执行前source pins与每份调用input的callable source SHA见[preregistered-manifest](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/execution02/preregistered-manifest.json)。expected含具名断言原文/source hash；frozen-input-index含20唯一ID、每次调用原件/hash、protected field原值与字段缺失，未截断raw input_json/full_report_json。两次执行source pins相等，工具版本V1/V1.1及首次超时永久保留。

输入/输出资料、工具和本文冻结副本由[交付seal](../../../artifacts/market-risk-implementation-20261007/B2/q05-frozen-controls01/source-seal01.json)绑定。private-tmp临时SQLite不纳入正式镜像；正式镜像只按public文件清单复制，不执行工具、不改正式库。

独立DEV日历select候选仍只增加aria-label=事件类别：before19511fcd47ebdfd1a80809d4f01c680c899dd7ba19e19b2cfc2e39ad143a486f，afterd369a3eb6d7944d8bb33118eab038fc623303beb0649462a3b6ff583e4c60312。本轮不晋升该源、不新增或运行产品测试。
