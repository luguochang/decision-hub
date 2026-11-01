# 四项跨层实施与验收合同27

2026-10-09。本页补齐[实施合同26](IMPLEMENTATION_CONTRACTS26.md)之外的四处接口、依赖和验收说明，沿用[33项清单](IMPLEMENTATION_CHECKLIST.md)、[技术设计](TECHNICAL_DESIGN.md)及[原验收指标](ACCEPTANCE_AND_METRICS.md)。**已装**指本次只读核对的正式源码已有实现；**proposed**指尚须实现或完成真实验收的工作。代码、私控和服务Ready均不授真实报告、模型理解或金融效果通过；执行状态由CURRENT_FOCUS/execution-contract维护。

原权限、source/完整SHA/readset、物理事务、lease/generation、取消与冻结截止不变；各入口原5/15/45秒预算、Provider输入1,000,000B、census原8,388,608B门按原合同执行。未知COMMIT不重放，诊断不作ACK，旧原件/sidecar不回填；PIT、固定旧试验、100独立日/30事件及金融效果门不改。本页仅文档接线，不执行测试或实际请求。

## 1. G07：独立原生分母、实际用量及公共回读

**已装接口与原件。** Host的`extensions/dsh/decision-hub/src/host/native-conversation-census.ts`收官方生命周期；`host/bridge.ts`提交独立原件。`apps/hub_api/native_conversation_census_routes.py`提供bridge-auth的POST/GET `/v1/dsh/sessions/{run_id}/native-conversation-censuses/{generation}`，普通owner或模型不能声明complete。合同位于`packages/kernel/decision_hub_kernel/ports/native_conversation_census.py`，writer位于`application/native_conversation_census.py`；同Run/generation的`RunRoleOutputRecord`不可覆盖，不改root history或DshSessionResult的events/result hash。

- **数据身份与缺口：** `decision-hub-native-conversation-census.v1.census_sha256`是排除自身hash字段后的canonical UTF-8 SHA；ACK的`reference.payload_hash`是完整`research-native-conversation-census.v1`服务端wrapper的SHA，不能互换。wrapper保native_context/source_refs/model_binding_refs。现有说明未完整索引这条原件链、child/repair分母与公共usage的验收关系；并非缺少已有producer。
- **最小收口（proposed）：** 在正常流程核同一ACK/reference及重启GET同hash，联到`application/research_provider_usage.py:read_provider_run_usage_in_session/verify_provider_usage_census_in_session`和`research_quality_sidecar.py`。分母须来自root及全部descendant的注册/birth、各自first_live_seq/fork seed、完整step/turn/terminal及精确seq；同child修复第二turn也计入。缺birth/session/terminal、remote不可读、截短/超限或未settled保持incomplete/unknown，不以input行数或自报counter补齐。新sidecar的`conversation_usage_proof_version`要求独立census；旧冻结sidecar无marker沿原legacy验证，未知marker拒绝。
- **依赖：** 官方registry listener及raw native快照、完整ModelBinding/input/finish原件、authenticated terminal、同物理快照与fresh源门。公共`research-provider-conversation-usage.v1`仅`conversation_only`，排除compaction/title；只有独立分母完整且每项usage认证后才能给总token。部分known逐项不能变总量0；权威价格缺失保持`cost_status=unknown/cost_usd=null`，budget/understanding/financial标志仍false。
- **必要定向控制：** 复用已封root+repair-child、同hash新连接回读、权限/路径/冲突及缺件控制；只补实际新差异。核裸census SHA与wrapper ref SHA分别正确，源/原件/引用篡改拒绝，missing finish/子会话使总量null，旧sidecar不回填。
- **真实验收与完成指标：** 正常实际Run持久独立完整生命周期原件，重启可读同wrapper/hash，公共usage可从全部sourceRefs重算；expected=observed=covered>0且每项认证后才known，否则明确缺件/null。原始价格未取得时费用仍unknown。原件接线与可信token完成不代表全provider账单、预算资格或模型理解通过。

## 2. MR17：官方冻结ABI下的完整上下文保护与消费

**已装接口与原件。** `extensions/dsh/decision-hub/src/host/bridge.ts:retainedPreStep`挂官方`agent/pre-step`；`host/retained-context.ts`构造完整投影并在官方append→derive→deepFreeze之后只读核验；`host/model-dispatch.ts`归档实际GenerateOptions并把同一options交给next。`src/final-review-gate.ts`提供`trustedSynthesisEvidence/protectedRootReviewBodies`，Kernel `application/research_context_delivery.py`亲读ProviderInputOriginal、原prompt、native/plugin事件及review原件。

- **数据身份与缺口：** 首turn以实际user source的完整原prompt逐字匹配为初始DTO权威，canonical control与BEGIN_UNTRUSTED正文联合核验，不制造原生seq。后续plugin的第二text块是完整`decision-hub-protected-context.v1` JSON，含冻结初始DTO、accepted evidence/facts及matched完整IndependentReviewReceiptV2投影，绑定Run/generation/request/plugin和真实native范围。缺的是现有技术页对官方ABI、首turn及后续保护原件验收的完整接线说明，不应再修改冻结options或摘要替正文。
- **最小收口（proposed）：** 沿既有pre-step追加和只读dispatch门验实际消息；真实内容/保留状态变化时按现有机制保最新完整投影，不增加输入cap、编辑旧事件或无条件逐step累加。Kernel核actual append、last seq及authenticated census首端；官方prune display替换仅由`packages/runtime_adapters/dsh_runtime/tool_result_attestation.py`原身份/replace链认证，不能把两个不同真实结果合成一个合法review。
- **依赖：** 当前root binding/deadline/cancel、fresh DB accepted DTO全部共享字段、原request/prompt及Provider输入全body SHA、native范围、child原Provider与完整review receipt。仅managed live root conversation保护；child、unmanaged、title、compaction不注入。保护块可交付不等于模型理解或review效果。
- **必要定向控制：** 复用真实SDK append→derive→deepFreeze→archive/next ABI控及prune后完整投影控；只补受影响差异。wrong binding/gen、fresh DB DTO不同、缺正文/错hash/span/replace、非法第二真实body、超原cap、取消/到期必须拒绝，长期多step不靠扩大cap通过。
- **真实验收与完成指标：** 实际root所选Provider输入包含完整必需DTO与合法完整review正文，质量/context同Run回读能亲核原body/hash/native/child身份且无必需正文缺口。首turn原prompt与后续保护机制分别有原件证据；Q05模型理解、四独立Review实际效果和金融效果仍各按原门验收，不能由delivered徽标代替。

## 3. MR27：经验记忆的冻结成对消费与效果回执

**已装接口与原件。** `apps/hub_worker/market_case_context.py:MarketCaseContextProvider.__call__/for_research_mode`仅给current_decision的BTC/ETH 24h任务提供已认证当前method的案例上下文；`application/market_case.py:MarketCaseService.retrieve_in_session/context_in_session`核available≤cutoff、scope/method、成熟与assessment原件，最多4项并冻结CaseContextReference/DTO及required context refs。memory-off不读取案例；案例以document/unverified上下文提供，不升级市场事实权威。

- **缺口：** 现有“有/无记忆对照”未定义成对冻结输入与实际消费/效果回执；M02重启接续或出现case ID不证明市场经验有效。
- **最小实现（proposed）：** 新独立`MemoryAblationPairV1`先冻结pair_id、scope/cutoff、method/tier、相同market input refs、获准case refs/hash及既定metric protocol；两任务仅case context开关有差异，绑定各自实际Run/task/ProviderInputRef、完整body SHA、context delivery、result/review、usage和成熟标签/排除理由。保存独立不可变结果回执，不改旧案例或旧试验；具体command/producer接线须评审后实现，当前没有该新DTO/API。
- **依赖：** 合格成熟MarketCase、current-method witness与assessment basis、冻结task、原Provider输入和context reader、事前确定的成对指标/成本分母及原配额。候选case来自截止前实际原件；不得用未来label、current latest或相关两资产充独立样本。
- **必要定向控制：** future cutoff、wrong method/scope、未成熟/不可用案例、body/ref/source变更拒绝；on/off除案例上下文外冻结输入一致，memory-off案例读取为0；无合格案例记录insufficient，不以ID-only或摘要当完整消费。
- **真实验收与完成指标：** 在事前冻结的有限实际配对中，可由Provider完整body核案例是否真正交付，并按原指标保存差异、区间、实际用量/未知费用及排除分母。无改善或样本不足是合法结论；少数pair不授100日/30事件、PIT或预测promotion，也不续开/延长固定旧试验。

## 4. K08／MR29–30：宏观与黄金权威原件准入

**已装接口与原件。** `packages/kernel/decision_hub_kernel/ports/market_extension.py`已有`GoldMarketSpec/InputRouteManifest/ExtensionOriginals/ExtensionOriginalReader.verify`；`application/market_extension_registry.py:MarketExtensionRegistry.register/assess`执行准入。`application/market_enhancements.py`已有macro_state/crowding/event_surprise及ArchivedEnhancementInputs；`apps/hub_worker/market_enhancement_bootstrap.py`接当前原件。GoldMarketSpec已区分XAU-USD现货与GC合约的provider/venue、USD/troy_ounce、price basis、calendar、expiry/multiplier，不借BTC/ETH或新闻代理。

- **真实缺口：** 生产`EnhancementOriginalReader.verify`固定保留rights未认证、成熟label不可用、F5独立样本缺失、成本未核四项原因。质量/PIT成立可供描述性输入，但现有vintage/共识接线不能自行消除这些原因；权威producer/reader与实际release验收尚未闭合。
- **最小实现（proposed）：** 先选定真实获准供应商及不可变权利、时点、calendar/unit、成熟标签/样本和成本原件producer；通过既有`ExtensionOriginalReader.verify`端口亲读原ref/hash，与manifest/scope/provider逐项绑定，再交`assess`。需要的新成本/权利原件合同须单独评审，不能向旧strict DTO塞未知字段或由配置bool授资格。宏观共识须同event/unit且public前实际known；历史vintage不得由今日current值回填；黄金按独立spec实际采集。
- **依赖：** 实际许可与供应商、原published/received/known时间、vintage/revision与共识原件、黄金交易日历/价格定义、成熟路径、来源/模型版本及F5原独立样本评估。未获外部原件时明确not_admitted/descriptive_only和原因，不连续探测或用旧资格补认。
- **必要定向控制：** 复用已验canary合同，仅补真实producer/reader差异；future clock、错scope/event/unit/calendar、revision/原ref/hash变化、缺权利/成熟样本/成本时保持拒绝或原描述性状态。合法数据但缺资格原件不能research_qualified，更不能概率/交易promotion。
- **真实验收与完成指标：** 获准真实源的raw、时点与权利等原件可按same ref/hash回读，registry保存实际准入判断及缺项；取得数据和完成准入分别记录。高级模型/金融Ready仅在原F5与统计/效果门实际通过后判断；任何缺项仍unknown或明确不合格，不靠代码已装、单个200或一分钟行情授P。

## 执行证据与收口

每项只补确定缺口，复用未变已封控制，保存唯一版本/before-after/首F、实际Run/ref/body SHA、权限与时间分母及未完成原因。真实验收由Root安排，禁止为本页自动触发采集、研究、重试或发布。与合同26的K02/K09性能、K04历史训练和K05原件展示分开推进，原33项不删减。
