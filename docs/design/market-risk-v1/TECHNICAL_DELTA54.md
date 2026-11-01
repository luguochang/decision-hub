# 54技术增量与文档状态边界

1–4节是原54编写时点及未安装候选的历史说明；现行55状态以第5节和[55结果](CORE_ACCEPTANCE55_RESULT.md)为准，历史P/F不改。


文档准备时：53已标准停止，54三个必要生产源已合并并完成唯一标准构建P，服务正在正常启动；首次过早Ready守门拒绝、未进入HTTP/DB的原F保留。**本次54 Ready与同原BTC Saved/独立ACK尚未通过。** 本文不代替[当前任务](CURRENT_FOCUS.md)、[33项清单](IMPLEMENTATION_CHECKLIST.md)或[53实际结果](CORE_ACCEPTANCE53_RESULT.md)。以下实现、候选、测试等级与独立发布范围分开说明。

## 1. 文档入口和小时状态一致性（仅文档增量）

- **实现入口**：README、REPORT_SERVING_REDESIGN、RESEARCH_RUNTIME_RELIABILITY_PLAN和33清单MR12/MR23/MR26。历史46/47/48首段明确标为历史，不删除当时的失败、数据和判断；现行状态仍读一页纸及具名批次结果。
- **接口/数据**：清单只更新末列。原48机会、forecast ACK、outcome publication、到期标签是不同对象；UTC04双publication按时P，05的BTC已合法隔离且原提交UNKNOWN保留，ETH05 ready/on_time。隔离不冒同机会恢复，06维护缺失保留。
- **失败策略**：未知提交/fence、原缺失/迟到和成熟不足都保真；不把新文档当业务写入或重签旧机会，不改原33前五列、机器contract、28日或模型参数。
- **验收**：文档更新先逐字核原文件SHA，检查只指定三行末列改变、机器contract前后字节相等；实际自然publisher/全分母与金融门仍按原协议，不能由文字一致性判P。
- **独立升级范围**：仅文档，ROOT写入由Root显式执行已交接脚本。八层正文和统一指标不另复制；历史主题只加现行入口标识。

## 2. 54已合并：Frozen CurrentLoader十合同模块的最窄身份闭包

- **实现入口**：`packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py::_current_loader_type`。53实际gen11制作因normal Reader返回DTO与private usage引用类不同，被`research_usage_transport_original_invalid`拒绝；此次仅修outer retained-installation CurrentLoader。
- **接口/数据**：固定十个模块为合同root、models、generated容器、binding_contract_base、policy_contract_base，以及generated中的provider_input/research_context_execution/research_context_selection_v3/research_context_selection_v4/research_policy。每个normal导出必须在本次captured paths/payloads内且真实源码逐字相等；直接generated导入同样闭合。共享父通过本loader的闭合分支装载，保持normal `contracts.generated`原namespace；其它父仍原private路径。没有prefix fallback。
- **失败策略**：缺源/源码变化/未声明依赖仍拒绝。inner retained archive、Frozen授权/nonce/IO、原七transport条件、原65/预算、2MiB/32MiB和180秒lease/CAS不变；不能删`isinstance`、ducktype接纳或用当前合同替掉未认证旧合同。
- **验收**：七项私有机制控制和独审P，证明具体类身份/namespace、逐源pin及七transport拒绝仍生效；不是真实完整材料replay。最终门仍是同原BTC正常Worker制作→Saved→独立ACK→状态/质量/完整下载与PC原字节一致；启动P也不能代替此门。
- **独立升级范围**：当前54必要三源批次中只有此一个backend生产源，其余两源是已发现资料用途/加载/定位UI修补。任何未来共享十合同源码变更须另核captured map；本次不重写旧Run和旧归档。

原件：[54出版候选及机制结果](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-frozen-dispatch53/candidate54/candidate-seal54_01.json) · [作者外独审](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-frozen-dispatch53/candidate54/independent-review54_01.json)。实际53首因与唯一正常CAS见[批次结果](CORE_ACCEPTANCE53_RESULT.md)第13行，不是重新发研究许可。

## 3. 诊断02未安装：错误传播与安全元数据分级

- **实现入口**：独立候选`web_runtime.py::_ObservedCancellationProjector._call`及`_projection_exception_diagnostic`，包住完整原projector operation后仍委托原`super()._call`；受保护`cancellation_projection.py`原read/write/Session/BEGIN/commit/两槽/deadline均未改。
- **接口/数据**：私有logger记录`dsh-projection-exception-diagnostic.v1`，只含phase、read/write/unknown、合法原operation/run/generation、已有stage、固定35个(module,class)安全名称或unlisted、可信源frame及观察时间。上限4KiB/8frame/64扫描；可信frame要求实际loaded模块globals、module文件及code文件一致。没有异常消息、args、locals、SQL、prompt或raw traceback。read未传ID/stage和初次identity缺件仍明确null。
- **失败策略**：诊断logger或序列化抛Exception/BaseException均不能遮盖原operation错误，原同一异常对象裸`raise`；取消、KeyboardInterrupt和SystemExit仍传播。记录只是诊断，不授权限、不重投、不改terminal、不据旧050Run字段推断零provider或授权retry。
- **验收**：真实执行七组纯AST机制控制P/0.126378秒，覆盖原返回/同异常、logger失败、取消、名单/未知/敏感内容、4K/8/64界限及委托不变；父projector、loaded frame和临界serializer使用私有替身。**实际Session/BEGIN/native/durable、原线程资源、handler延迟、新DSH canary和内部首因仍未验证。** 候选未安装，不混当前54。
- **独立升级范围**：仅一个非65保护的web_runtime文件及未来正常canary诊断；不改原期限/槽数/授权/Host。记录失败不产生新的业务恢复资格，历史未知保持未知。

原件：[后继02封签/实际纯控](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/cancellation-projection-diagnostics54/candidate02/candidate-seal02.json) · [独审](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/cancellation-projection-diagnostics54/candidate02/independent-review02.json)。原R1–R3方案仍保留在[可靠性历史专题](RESEARCH_RUNTIME_RELIABILITY_PLAN.md)。

## 4. 异步完整audit11未安装：202受理与独立完成

- **实现入口**：`ReportAuditService`/`research_report_audit_jobs.py`、独立`ports/research_report_audit.py`与`research_report_audit_routes.py`，Managed publication原lane空余才调用`process_one()`；完整`audit_original`在writer外执行。UI使用独立`reportAudit.ts`，未把audit合同塞入上述十共享模块。
- **接口/数据**：`POST /v1/research/runs/{run_id}/report/audits`接32位request_key并返回202；GET同prefix的`/{audit_id}`、`/{audit_id}/result`、`/latest`；POST `/{audit_id}/cancel`必须带expected_version。typed state包括queued/running/prepared/completed/failed/cancelled/obsolete/unconfirmed，绑定run/audit/version/原identity/结果SHA/真实时点。202只表示已有受理状态，只有完整结果保存和独立原件确认ACK后completed才表示本次完整audit完成；它仍不授预测资格。
- **失败策略**：原读权/撤销头、generation/owner/180秒lease、完整原库/材料和result内容寻址前后核验；未知COMMIT fence保留，不盲重领。busy/未知/原件漂移/取消保持各自状态或原拒绝。正常接口仍可能读取已有材料状态，≤2秒必须真实测；不能以POST快或缓存quality冒完整原库核验。原同步完整audit继续可显式调用。
- **验收**：原audit正确但30.541秒性能F保留。重基后只有八Python AST/逆向恢复当前54全字节等静态证明，未重跑旧同矩阵、未做正常consumer/ACK/≤2秒。下一批仅选一个已confirmed旧ETH原报告，正常受理→Worker完整核验→独立ACK→status/result与PC回链；受理、结果GET及总后台耗时分别记录，原report/download SHA不变。
- **独立升级范围**：仍11生产源；唯一重叠runtime只含三个旧audit hunk，逆向恢复当前54完整字节，十合同/CurrentLoader/原七transport不动。新增唯一`0092_research_report_audits`，前件`0091_research_publication_bundles`，仅追加`research_report_audit_jobs`和`research_report_audit_confirmations`两表；不改旧Run/Fact/Saved/ACK/grant/协议/模型。

**正常迁移缺口尚未实现**：现`upgrade_market_risk_schema.py`的EXPECTED_HEADS不接0091，STOPPED_GUARD仍V1-only；不能直接当0091→0092可执行入口。下一批需单独封正常operator，精确expected0091/target0092，停机桥用官方`Test-ValidatedProductBuildsV2`且保stop.request、supervisor stopped/PID birth、roster/端口和三检查时点；先一致私备份probe完整旧表/行/原件，再Root显式apply，不能stamp/create_all、patch globals或伪V1。随后新schema/Ready明确0092，原资格/协议/65/配置只读重核。候选未包含此operator，当前正式head仍0091。

原件：[audit54重基候选](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-audit-feasibility52/candidate54/candidate-seal54_01.json) · [完整接口和未实现迁移交接](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-audit-feasibility52/candidate54/HANDOFF54.md)。该页为核心通过后的备用独立批次，不是54的新业务门。

统计不足与外部准入已有[统一指标](ACCEPTANCE_AND_METRICS.md)和[八层/33项](IMPLEMENTATION_CHECKLIST.md)合同：原48机会全分母、28日固定到期、100独立日/30事件、Q05及黄金权利/报价均不因本技术增量改为通过。本轮只补文档索引和必要状态一致性，不以文档量计交付。

## 5. 55连续只读原件复用：已安装，核心报告仍未过

- **实现入口**：`research_publication_runtime48.py::frozen_replay`，仅两处增量及`nullcontext`导入；54十个合同模块的精确normal DTO/namespace闭包完整保留。retained分支从本次原native记录读取注册，强等selected directory/index，再调用原host的`_verified_release_uncached`和既有`_verified_release_read_scope`；current-installation仍走原路径。
- **接口与数据**：仅在既有`authenticated_material_session`、`FrozenConsumptionIO`、`NativeCollector`的同一干净physical只读事务中使用原host的不可变原件。范围绑定Run、注册、thread/task、Session、native连接/事务/savepoint、source/contract/private身份；不跨请求、版本或事务缓存授权pass。入口完整uncached验证，成功出口由原builder重新核全部原件及复用描述；异常出口清空范围、不发布结果。
- **失败策略**：selected/source/archive/transport/内存身份或物理读连续性改变仍拒绝。完整typed native查询、范围/absence、所有结果字段与原材料比较，以及`replayed==expected`保留；重复query只由原collector按query_id去重，未删任一比较字段。原Frozen授权、nonce、2MiB/32MiB资源门、180秒lease/CAS、未知COMMIT、Saved及独立ACK均不变。严格authorizer不允许savepoint时原scope保留完整慢核验，不能降门。
- **时限真相**：180秒是任务所有权lease，不是执行deadline，也不是heartbeat或续租；本次未实现阶段进度、执行期限、续租或提高预算。单次旧堆栈见重复原件核验，不足以证明无限loop；scope也尚不构成已解决高CPU或性能的证据。
- **验收**：7项纯机制控制P，使用真实候选与既有scope算法、synthetic Session/native/resources；无实际DB、整份冻结材料、正常issuer、DSH或BTC replay。实际55构建/完整读回/正常START/Ready已P；同原BTC仍queued、Saved/ACK0，完整报告验收F。单次原无locals栈证明当前在`current_capture_before_frozen_replay`，初采仍重复完整source maps，后半段scope没有覆盖这一步。真实性能/RSS与已完成报告未证，不能宣称scope已解决重复开销或无限loop。
- **独立升级范围**：仅一个runtime生产文件；原65、三配置、六flags、原授权/expiry/模型预算、48机会/28日协议、旧Run/facts/捕获12成员及历史F保持。diag02和audit11/0092未合入；不重新授retry或创建研究。

原件：[55单源封签](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-frozen-dispatch53/candidate55/candidate-seal55_01.json)、[55源独审](E:/project/study/codex/dsh/.market-risk-b2-b4/artifacts/market-risk-implementation-20261007/B2/report-frozen-dispatch53/candidate55/independent-review55_01.json)、[55实际晋升](../../../artifacts/market-risk-implementation-20261007/B2/release55/root-source-seal55_01.json)。audit11+0092桥和diag02仍未安装；新完整边界方案仅static。最新运行结论见[55结果](CORE_ACCEPTANCE55_RESULT.md)。
