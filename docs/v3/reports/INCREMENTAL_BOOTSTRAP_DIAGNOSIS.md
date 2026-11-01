# 原启动链：直接导入与真实回执前提

2026-10-24。继续保留原UI和业务流程；本报告只记已运行诊断与source-only前置事实，不证明API/Host或金融验收。

## 已执行的有限导入

新私有目录D:/DH-fact-im1024a，真实Main Python，完全原写/网络防护，无sys.setprofile/sys.settrace/faulthandler；子脚本再拒SQLite/network/subprocess，仅import apps.hub_api.fact_routes。命令为Main python `-X importtime -B <fact-import-probe/child.py>`，由既有OwnedProcess启动并归属Windows Job。

实际import6.165805299999192s，外层7.063s、child0；完成后226个apps/packages模块origin均指真Main并逐SHA重核一致，direct具名源前后pin相同。创建应用/DB/API/Host/Worker/admission均未执行。PID21072/FILETIME134373060083251154，Jobclosed/rootexitobserved。来源清单是post-import观察，不冒完整transitive before/after audit。

[导入收据](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/fact-import-probe/receipt.json)与[D原件](D:/DH-fact-im1024a/)。importtime累计值嵌套，不能相加或扣旧45s；这个成功不证明完整API ready。fact_assets包init会额外加载workbench/mcp/runtime等，但尚未证明它们是故障根因，未重排import。

## source-only确认的正常入口缺口

正常API配置函数在无两项DECISION_HUB_RETAINED_RECIPE_RECEIPT及其SHA时直接return；fresh history registry初始化None，factory在Root/registry前置检查拒绝。原ScenarioRuntime只继承ambient env，不创建该来源；sterile执行器也没有传入。必须走原正常guard/producer的真实receipt，不用fixture/replay或动态赋值伪造binding；此缺口不是此前超时/AV的已证原因。

真实installed locator指49c，receipt SHA2bc144fc…567e6、registry SHA9e2c…2ff27b与文件匹配。旧receipt的Main源与Q031/Q033/Q034/Q035/Q036等当前源不符，不能冒当前安装通过。原producer保留历史并生成含retention证据的新回执，不运行会配置凭据和启动服务的完整run-product。

[有界正常build准备清单](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-next-step/normal-build-readiness-review.md)是较早观察，里面manual1ac已由Q036 d83取代；最新可获性为[1790项清单](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/prior49c-source-availability/availability-final.json)，SHA82578b00…8788d0。exact旧字节1772当前Main+12旧mirror+3Q037备份+3具名修复备份，缺件/读取错误0，recipe inputs914可获。未展开63499历史shards或执行完整normalguard，不夸大材料齐备结论。

## 当前接点

Q036已完成纯注解修复与实际strict差量；[Q037](INCREMENTAL_REVIEWED_SOURCE_ADOPTION.md)已精确采纳32源并由标准generator得到a925指纹。现8051进程CIM实证是pc_v3独立预览，不能代表原Main产品。

Q038卡范围f14f827e…0857已三处记录：18旧源只按真实hash补入全新Main/artifacts树；bce为已封合成测试材料，非实际金融数据；完整备份Main既有lib/dist/编译cache后，原build-product只各一次UI/host构建，真实source-stability、retention union、产物guard和2MiB边界不变，不安装/更新依赖，不指定Spot/capture/DB。成功仍需实际API/Host等验证；失败保新stage/log后定位，不自动再跑。

完整V5审计封包仍缺typed envelope、正常issuer内全物料捕获及可信离线验证；[有限方案](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-next-step/perpetual-full-bundle-plan.md)已明确generic下载/reading-only不是替代品。实际PAPER/六组合14天/四周等长期指标未完成；没有新增ACCEPTED，目标ACTIVE。

## 后续实证与需求范围纠偏

Q038实际原build-only在retention失败：archive要求builder内唯一literal initial，Q028缓存把它移到global。UI/Host各attempt0，旧输出未变；详见 [正常构建失败原件](INCREMENTAL_NORMAL_BUILD.md)。Q039恢复声明兼容性，同时保留已有memo动态root失效/cold fallback契约，不放松归档器或旧断言。

上文“完整V5审计封包仍缺”是额外方案状态，不能作为01_REQUIREMENTS §5.4/T106/T207的强制推论。需求要求所有合格研究PC一键自包含JSON+HTML、快照脱DB可读；不等于每次下载必须全部后台Python/SQL/session。Q034已具阅读副本，但仍需实际脱库浏览器和真实合格报告路径验收，不能用方案复杂度代替验收。

旧sameRun的5秒失败发生在合成manual helper前置（tests/decision_product/test_perpetual_manual_execution.py:241–254），未到研究POST；它不是需求的P50≤15分钟/P95≤30分钟。保持失败/风险记录及旧断言，独立官方Host功能验证继续。生产报价新鲜度≤5秒是另一真实业务守门，不改它，也不据此称PAPER/性能通过。


## 正常构建后的原官方入口真实结果

正常构建936c0aa8…65f7c通过后，q043nh01按新实际Main17候选和1927来源运行，12:16:30.946174Z→12:17:21.080424Z/50.1339501s/exit1：API在原45秒门未就绪，log0B，未到MCP/Host/Worker/admission。clone0、两Owned Job已关闭、cleanup errors=[]、1927来源前后同；[失败证明](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh01/failure-proof.json) SHA `c3cfddd89b01d9ede84483fd4fb04dd7b5a42ca16c17ed9c7acd035284ea79c4`。不由空日志或清理后exit1推定根因。

唯一新API-only q043api01增加importtime及10秒faulthandler，保45秒门/原guard/私有数据/Owned finally；Root在新副本先修PowerShell7 Junction.Target string的准备脚本兼容，旧包未改。实际observer于12:31:58.540282Z→12:32:50.622638Z/52.0818967s退出1，真实API在cleanup前0xC0000005；1931来源前后同/cleanup[]。[实际结果](D:/DH-host-q043api01/diagnostic-result.json)仍ready=false；importtime159056B，stack仅169B不完整pathlib filename。这个观察不能认定应用热点、死锁或采样导致崩溃；旧19.922s API-only AV也保留。

不再重复同类C级采样。下一新Python线程栈观察仅准备，仍保全部权限/45s/清理；诊断不授予性能、Host、研究或金融资格。未改Main API/Worker代码、未延长预算或盲重跑Host。


## Python栈定位与真实首次数据库初始化

q043api02以Python线程栈替代不完整的C级采样，取得四个完整样本：10秒文件身份检查、20秒pypdf导入、30秒迁移0014、40秒迁移0035。API停前仍活、1931来源同、清理无错误；原45秒ready仍失败。[实际证据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/api-readiness-q043api02/diagnostic-proof.json) SHA `8d7f8dc58242db5e7d3d73f957f8951fd9df43e259787450657691db2f8e06aa`。不能从这些样本精分耗时或把全部窗口归因rglob/迁移，不实施该扫描优化。

按[Q045](E:/project/study/codex/dsh/decision-hub/docs/v3/questions/Q-045-first-database-initialization.md)，新nh02先调用真实Main Database.initialize完成全部迁移、核实际head，随后原API仍自行检查head；新安装300秒与原API45秒分开记录，所有业务/权限/金融门不变。实际初始化72.391秒，expected/current均0103_perp_funding_reconcile、engine已dispose；随后API及MCP确实通过。整次109.562秒仍exit1：Node --import使用裸Windows路径报d:协议错误，guard与CLI未加载，未到Hostready/worker/admit。

[nh02失败证明](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh02/failure-proof.json) SHA `b86af70d21b67d1bd36dbe27204f1f6866faac860265597e46605331cb1c01ce`；1928来源同、五Owned Jobs清理、errors=[]。下一仅私有guard路径as_uri文件URL修订、新小探针与新包，不改Main、不恢复旧半迁移库或延长原业务预算。原normal936c通过仍不授予实际Host/金融验收。


## 官方 CLI 已加载，私有 SDK junction 路径冲突

nh03（13:27:11.872712Z→13:29:00.188239Z）108.313秒/exit1。实际新库安装70.875秒、actual/expected=0103_perp_funding_reconcile、engine disposed，原API45秒及MCP通过。仅fileURL新包scope f08d7342…9a58f使原Node guard和官方CLI真正加载；随后官方app-boot ensureSymlink:422在unlinkSync私有已有junction处触发controlled_host_external_operation_denied。1928来源前后同，五owned Jobs清理且summary errors=[]；[失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh03/failure-proof.json) SHA 532970ba404aa4de65cbbd45a6d7f19ea9f1c6ce9030050ccb03069a4a334f6d。Host未就绪、worker/admit未开始。

[RO路径分析](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh03/guard-path-readonly-final.json) SHA 7d1a105487477a6764a421a70e498dca80a0e16e994092ad0075ec58f4efcda1：clone种子的@deepseek-ai/cordis指SDK/vendor/cordis，官方想用SDK/apps/cli/node_modules下alias；realpath相同但官方canonicalLinkPath不跟最后分量，故想替换D私有link。通用writable跟随final leaf落SDK才拒，不能称目标正文写入。原guard没有denied参数日志，具名路径是确定循环顺序、已heal首项和现有metadata推导，不冒直接参数观测。

下一最小候选优先调整clone种子的已核lexical aliases，以同realpath/entry/manifest绑定官方目标；不修改SDK，不放宽通用unlink，不抬安装300/API45/Host20/worker240预算或原断言。新包/最小原SDK heal探针/限定peer和fresh nh04后才可再做一次真实Host。当前Main正常构建936c与已构建产物不变，profile/正式服务未部署。新test schema修复不在normal sources，已经独立采纳；不据此重建整个产品。


## nh04真实Host受理后，研究scope接线失败

nh04唯一运行13:45:38.498754Z→13:49:00.141447Z，201.641秒/exit1。新库原initialize71.437秒/head0103通过；原Hostready身份、SDK0a53…c343b、plugin a925、Run受理及owned Worker已实际发生。完整session8records最后turn-end error={message:research_catalog_http_503,code:UNKNOWN}，没有LLM step/token使用。MCP已有唯一catalog GET503；cancel不授权和GETresult409是后果，不能当根因。[终态proof](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh04/failure-proof.json)SHAe7648f886fb21419d68567abda7246eaff6ff4c79e392cecee93537521dcb427，[RO原因](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh04/cause-readonly.json)SHA9920c7631ce016e45c3dc234528e2a71e1ee769bb9516d123a701bedca8a616b。1928前后同、6owned Jobs清理无错误。

冻结env only replay.research、无显式DECISION_HUB_RESEARCH_DURABLE_PROGRESS；原main.py477–483因此False，构建无scoped_query_catalog的thin gateway，query_catalog本身是dict。SDK对真实session请求触route281–284的scope_reader_unavailable503；body未录，精确branch属冻结env+源逻辑推导。现成显式值1会启原Durable gateway，可保持真实Run/Session冻结权限；不能删session参数、给thin假scope或冒研究成功。

下一私有工具只接此既有开关，先核原Scenario Run是否满足真实Durable scope前提并做原route/真实SQLite正负例离线RED/GREEN。完成后才新scope/pins/freshnh05；当前未执行、不重复nh04。原normal936c/Main13 b86仍有效，前端真实整数合同候选暂仅D，不把未知变更套旧来源。

PAPER另一条独立功能实证：一次匿名3GET均200/4captures，原bracket通过，原decoder按5秒最坏age5.542991秒正确拒绝；一次严格离线回放1casePASS2.115秒、21项输入SHA同，无新网络/SQL/child。原捕获FAILED保留，不能计订单、金融资格或全30例研究验收。


## Durable接线技术验证及构建等待边界

仅新私有run显式DECISION_HUB_RESEARCH_DURABLE_PROGRESS=1，原字节aadc→bab2cc90…372cb，逆去一行全raw同、其余5runtime同。真实create_server+真实V3 SQLite+ASGI catalog从旧thin503(scope_reader_unavailable)→原Durable200，unknownsession409/noauth401/tamperedLink409仍拒；1PASS32.85秒，40旧async warnings及1 socket.bind拒保留。[技术scope](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/host-durable-route/source-scope.json)SHA530eb7380fbe660002ffbb93b3dfb6bf6d5e110a3bf627d52d9238b4c9f9639d。A另对实际nh04库mode=ro核36个V3身份/hash全真且DB SHA同，原Scenario无需换输入；RO实际前提与fixture技术200不是新完整Host成功。

前端整数修复已Root精确接Main，当前13聚合298131b4…0c4ed，normal936c已仅历史。完整产物565+2aux=567文件/123890719B已独立保全，备份1091781a…a669，Main集合/字节/mtime不变。新正常producer q043-outbox-normal-build只prepared cfdc/尚未启动；ui-ls04又暴露binding_hash合同误读，故先等待D最小修复+完整真实UI，再重封最终来源，不执行过时prepared、不拼normalreceipt或套旧1928。

下一仍是实际同Run读/导出与浏览器、最终完整normalbuild、新receipt绑定Durable工具freshnh05；原300安装/API45/Host HTTP20/原URL等候120/Worker240和原assert保持，SDK/权限/正式数据不改。尚无完整Host研究成功或真实PAPER/14天/四周资格。


## 当前运行基线参数已纠正，新标准构建进行中

上一reading-build唯一运行14:35:50.488924Z→14:35:54.622667Z，4.133689s/exit1，原retention_maintenance_producer_changed。Root误把未部署的936c本地候选当Prior；实际installed pointer仍指49c/2bc144fc4bdc3dcaa11766aea1927d71e9d8dfe32753baaad9a71d7138d567e6，和原成功guard-build参数相同。[失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q043-reading-normal-build/failure-proof.json) acaed01c…ca5d和[B只读原因](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q043-reading-normal-build/retention-prior-readonly-review.json) d250aa2c…02c4a保留。此为调用选择错误，不修改原producer、硬编码维护allowlist、任何pointer或旧receipt；正确直接分支仍执行后续完整registry/source认证。

前端真实DTO已闭合整数和完整snapshot哈希两处合同缺口，当前15聚合a9f256a0…1cac3；原页ui-ls05真实1PASS，原JSON/HTML按钮Blob保真，但controlled transport不是实际HTTP/鉴权/浏览器下载，file://自动浏览器目检因安全策略拒绝仍待人工，不能绕过策略或冒通过。详见保存报告报告最新段。

最终匹配当前15源的新包scope184c86da…c1dc/preparedb9282fcb…19b2e/controller00ba372a…1e55，独立peercb4020ba…7cd3/C0I0。全新runtime-baseline标准build14:46:11.380640Z已启动，session97474/PID21472/FILETIME134373267713829543，free14962855936B、原8GiB child/10GiBfree策略，27控制/71native/24bank/567backup。当前RUNNING而非PASS，不套用历史936c；安装300/API45/HostHTTP20/URL120/Worker240不变，未启动新Host。

成功后仅以实际新normal receipt、当前15及17stage逐字绑定freshnh05；旧1928/936c/b86 manifest是历史，不能直接运行。Durable env1技术RED/GREEN保持，原权限/SDK/Run session不放宽。真正Worker进程恢复、PAPER入场至退出/结算/重启、全局UTC/PIT、14自然日/四周等实测仍待，目标ACTIVE。原profile、正式服务及正式库未部署更改。


## 标准构建终态与240秒观察窗裁决（Q046）

runtime-baseline标准构建已实际PASS：14:46:11.380640Z→15:04:49.221471Z/1117.840781秒/exit0，UI和Host各1次exit0。当前normal9fa3ff2700e7f320b103554ea83fc956053921f674bd0c7b601926cc25391f2b，正式final314c3741ac9b4237d06a81163c80e54e818fd9d5db5ba3aa69f48275dc8ed522。raw1801/effective60910/48receipt artifacts/586lib-dist+2aux分开计量；旧备份567和源码原件24保全，当前15/71native/27控制及installed pointers同，未部署。

新q043nh05 15:17:12.733754Z→15:23:02.305319Z/349.5714秒/exit1，真实初装69.610秒/head0103，Host身份就绪、Run受理且catalog200+4execute200；外层原worker.wait240 TimeoutExpired，assert/collect未到。1933pins前后同，6owned Jobs完整清理/errors=[]。[失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh05/failure-proof.json) SHA1a1c01836276df7038dcafa7e82b645a162c095424ed5285544ffcae0838c2d9。

原session只读45records证明4step已完成，第5步15:23:01.831开始；实际4tool error=null，总5evidence16facts。Task截止15:49:34.097134Z，modelstep600000ms，Run/Link仍running无结果；API至停止前仍推进。未证产品死锁/最终必能完成。[只读核验](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh05/session-readonly-review.json) SHAa83b41bffb6d8abea21171e92a5de7136dced45514ee7d64330d8a2b796ac4ac，Root另实际跨frame解码核b1cfba86…d754、45/219495一致。

执行者依据用户已授权的证据裁决见Q046：240来自父验收脚本wait，非30m产品deadline或最终15/30统计阈值。下一新私有控制器按原accepted60就绪后读本Run已冻结Task的scope.deadline_at，固定原剩余<=1800s的monotonic观察端点；不改任何产品timeout、租约、模型步限、Task截止、原业务assert。前置与业务分别计时，保完整admit至终态实际耗时，超阈值如实失败/未知，长等本身不是修复。此刻freshnh06尚未执行，Worker恢复候选仅静态；没有合格Host/实际Provider/PAPER或长期资格。原完整App可由API同源serve既有dist，原run-product的固定正式目录/pointer写入仍不可用于隔离体验。目标ACTIVE。


执行前细化覆盖上文accepted60：该回执actual nh05约59秒才出现，不应将其当只读提取Task截止的强前提。仅父observer在原240秒前置观察上限内每0.5秒mode=ro等同Run冻结Task/Method/Exec/Link齐备，暂缺以外全部异常立即失败；提取后仍固定真实Task原deadline与remaining<=1800单调端点。产品自身accepted权限和所有业务断言不改，无新增HTTP轮询/重试。详见Q046执行前细化；未实际运行。


## 最新实际运行登记（北京时间2026-10-25）

fresh q043nh06已UTC2026-10-24T16:00:29.613102Z启动，session57095/PID27436/FILETIME134373312297221821，D:/DH-host-q043nh06；当前仅RUNNING/新库初始化，无业务结论。采用Q046执行前细化的最终scope0dabe967…1676047、1934pins c427d1fd…4345cfe、helper e2d1aba5…3cb200b，独立peer981577f5…435bae/C0I0；Root实际旧冻结原件RO parser probe03387fd5…b28d通过，原DB不变、不执行旧Run。当前normal9fa/final314c/15a9/17stage保持；原产品时限、权限与业务assert不变，只父观察到真Task截止，完整计时不冒性能修复。旧nh05FAIL保留，不拼其它Run结果。只继续监测本轮，不重启/不并发其它业务；最终Host、Worker/PAPER恢复、原页浏览器及全部长期硬门仍待，目标ACTIVE。


## nh06真实终态：Host完成，业务提交失败

2026-10-25北京时间接续：fresh q043nh06唯一运行UTC2026-10-24T16:00:29.613102Z→16:10:00.882766Z，571.269687秒/exit1。原私有新库initialize70.531秒/head0103成功；同Run run_c5f94b84c83c431cb6df0385e0d43b84、session dsh_aa703d32ef76ecedb30714c68bf00d009d916cce7cbaf35c610e1ac5da324611已真实受理。只读冻结原件前置51.547秒，Task原截止16:32:50.908960Z；选定remaining1779.962642秒后未refresh。Worker实际观察460.641秒、admit至终态460.766秒、return1；未到Task截止，未触观察超时，Q046不作性能修复或本轮成功依据。

原collect保存Host finish_reason=completed/last_seq82、Link state=completed/error_code=null/tool_calls_started8及原生最终输出，但产品Run status=failed/research_worker_failed/cause=ValidationError、artifact=null；原assert_scenario:1188确已进入并因run_status非completed失败。coverage sufficient/hardratio1.0/evidence9/tool_failure1不是业务成功。worker日志另有dsh_session_links readset_conflict，仅作待核线索，不未经同录制复现断言它就是根因。下一步用本轮原件精确定位首个异常并做离线RED/GREEN，禁止盲重跑Host、放宽业务assert或拼接ls01报告。

[本轮失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh06/failure-proof.json) SHA 0efedd46a1456a4b5d87754a0d8973f984bab6be6d869192eafbb806c7067d37；1934来源前后相同，normal9fa/final314c保持；6个owned Jobs全部关闭、所属root退出已观测，cleanup errors=[]。这些不是全系统进程清查。nh06队列已释放，原件保全，不重试旧Run。

原PC→永续研究→官方Host主接线源码已存在；V5保存报告不要求新Task或重搭前端。后续沿现成接口修最小实际缺口，generic Host录制不能冒BTC/ETH domain或金融资格。原PAPER writer单例profile仅获一次隔离诊断授权，原五秒assert/报价新鲜度不变；尚未有诊断终态，不计实际PAPER。原正式服务/DB/profile/installed pointers无部署更改，目标ACTIVE，14自然日/四周评分/两事件族/10笔真实PAPER/30例完整失败回放等最终门未达。


## nh06首个异常已同录制复现并修复

实际checkpoint340B错误为generated与public同名ResearchContextManifest类不相容。保原失败，精确generated请求RED后，在公开mapper严格完整wire重验；原候选4PASS，采用后的物理Main4PASS4.69s。另修Host v3条件分支漏binding/child鉴真，6新＋84相关PASS及局部TSstrict0。当前23源f10cf925…0daab2已Main+D采用；旧normal9fa只历史，新完整build仅prepare未启动。E经16具名旧快照无损压缩恢复13.52GiB，588当前输出已备份。完整证据/静态遗留/误选边界/下一动作见INCREMENTAL_HOST_COMPATIBILITY_REPAIR.md；原金融/性能/恢复及长期门未达，目标ACTIVE。


## 新完整标准构建真实PASS，尚未部署

当前已核终态：唯一contract-compatibility完整标准producer UTC2026-10-24T16:56:09.705905Z→17:18:08.197906Z，1318.491944秒/exit0；outer同handle1321.483549秒终态。原UI/Host各一次exit0、builds2，原assessment/retention及temporary/final guards均通过；chunk-size警告保留。新normal Main/artifacts/incremental-delivery-20261024/contract-compatibility-build/product-validated-builds.json SHA6dd435853656710aa85748dad31def411c2ea94ba8e53c7cee407e2b02a02513（246189B），实际native fingerprint a067bcfad97769c525e3e4e4e8be37cd212fedebbecc7229481bdb963a4cbc41，不能沿用a925。

[实际final收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/contract-compatibility-normal-build/final-receipt.json) SHA3d51c9ad0be84620ebd735d76cc0d6dc2a6043e74a0df46b5c080bc7a4b4103e；35controls/23adoption/71native/26原件/588备份与两installed pointers后验无漂移。raw sources1801、guard effective60910、receipt artifacts48与完整lib/dist586files128775456B＋2aux是不同口径；不相互替代。process starts/stops、migrations、pointerwrites、modelsubmissions均0，native_execution_qualified与whole_product_accepted仍false。构建通过不是部署、Host业务成功或金融验收。

UTC2026-10-25接续已实际重新读取Main normal并核23当前采用源SHA，无漂移。下一仅以此新normal/fingerprint和真实新17stage字节封一次fresh q043nh07，原nh06失败/原件不改，不重跑旧Run/旧包，不拼ls01结果。原完整App历史读取入口另只准备隔离API与ls01旧私有库的无损副本；不改旧库/正式数据/凭据，不以该历史Run证明新研究或PAPER。原浏览器file://策略拒绝不绕过，完整App原路由读取不是静态HTML代理。

目标ACTIVE。原PAPER五秒仍FAIL、真实金融domain/Worker恢复/浏览器实际下载和所有14自然日/四周/两事件族/十笔PAPER/30完整回放等最终门仍待，未改变56项覆盖要求。


## nh07同Run真实链路正在执行

UTC2026-10-25T06:13:07.2234542Z启动唯一fresh nh07（session41720/PID12392/FILETIME134373823872595484），使用上述新normal6dd、actualnative a067和23源f10c。1940 finite pins与17stage已独立核验。原initialize86.875秒、真实0103_perp_funding_reconcile/exit0/engine disposed，原API/MCP/Host ready；Run run_f8b79d4daec342b9b27745fc3fb35641已admit。真实Task created06:16:08.933080Z、deadline06:46:08.933080Z，观察器在06:16:32.305505Z固定remaining1776.627575秒，未刷新原截止。此时RUNNING，未有终态、报告或性能/金融成功；继续同一session，不重启/不并行业务。

原App历史HTTP包已完成限定peer C0/I0（352e51aa370ed1986cef56f5193418b39a0050492e195ef44b7ff55f66664313），PS parser0，仅准备；C终态后才一次原迁移作用于字节核对的新副本、启动原API/原dist/原配对流程。旧ls01库不打开SQL、不改动；真实读取/浏览器导出仍待。

当前授时3请求仅1有效+4.8534191秒/2超时，Local CMOS Clock/Leap3/stratum0，严格采集未通过；token无SeSystemtimePrivilege，W32Time Running。本轮未校时/提权/偏移补偿，需有权限操作者修全局授时。其他功能继续，长期认证不据局部功能结果冒称通过。


## nh07终态与下一原报告读取裁决

最新原件覆盖上文RUNNING：nh07在UTC06:26:19.4318531Z终态exit1/792.208539秒；Worker exit0、Host completed/seq82、9证据/0工具失败，同Run实际保存artifact art_5cc89552320244c6b4df15366e85f755。business research_only/product degraded/errornull，原success的completed/publish断言1188仍失败，1940来源同/6owned Jobs清理errors=[]。[failure-proof](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh07/failure-proof.json) SHA3db51ef876cd0c861fe06f41c09a76ba80fe61c2f401781bdfa8064e0906577a。

实际报告只有bounded自动检查、无独审回执，方向候选未校准。原可发表门明确保留这些要求；不将充分度1.0或Artifact当作合格，不改状态或放松断言，不再重复旧generic候选。nh06的model_type异常本轮未再阻止产物保存；这项事实不等于全部研究验收通过。

原App历史r01在原migration0008 source_states表已存在失败，4.8580173秒/exit1，原init3.61秒、engine disposed、owned Job关闭；无API/pair/HTTP，旧ls01 SHA709d同。create_all无迁移头fixture不作为真实安装库，失败原件e3e6a4a4…bdcfc2保留。改用nh07已真实正常迁移/head103且保存本次报告的私有库做RO一致性backup（源有WAL，不能只拷main/immutable漏WAL），新副本沿原API/原dist/原读取鉴权验证，不修假安装、不加代理。当前新包仅准备，未HTTP/浏览器通过。

具体判断及授权边界见[Q-047](../questions/Q-047-research-gate-and-report-database-source.md)。当前产品与原UI源码未因本裁决改动，全部长期/金融门保留，目标ACTIVE。


原App当前实际读取结果已记录于[INCREMENTAL_ORIGINAL_REPORT_READING.md](INCREMENTAL_ORIGINAL_REPORT_READING.md)：原API窗口正常结束、原UI正文可读；完整snapshot实际409、导出未通过，新copy三表均无本Run行。底层异常尚待同请求透明诊断；不改门或强制fallback，目标ACTIVE。


最新读取实证覆盖上一段“异常尚待捕获”：私有SQLite URI guard误拦截已用真实RED/GREEN修正，产品代码未改；原App uri01生命周期正常结束、原页面同Run正文可读，snapshot404→status200 legacy_full_original→quality200 unavailable，仍无完整质量记录/导出。真实单Provider canary亦已PASS，仅连通性，不是研究成功。详见[原报告最新实证](INCREMENTAL_ORIGINAL_REPORT_READING.md)；下一准备原PAPER command.v3的BTC4h完整独审链，全部最终验收门保留。
