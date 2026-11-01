# 原 Host 与冻结请求兼容修复

状态：两处兼容修复及完整标准构建已通过，未部署；nh07已保存报告，但缺独审/校准而未通过原发表验收。下一验证原App读取本次报告；无金融资格。北京时间2026-10-25记录。

## 两个具体问题与修改

nh06中原Host会话已completed，8次工具调用、9份Evidence与最终输出均有原件，但Worker的产品Run失败。实际Graph checkpoint保留的340B错误明确指向context_manifest的model_type：冻结ResearchExecutionRequestReceiptV3嵌入的generated ResearchContextManifest与公开ResearchSessionResult所接收的同名模型不是同一个Python类。首轮先将请求转为public DTO时mapper通过；用真实冻结generated request原实例后才精确复现，不能将先前dsh_session_links readset_conflict日志直接当根因。

修复在原公开mapper的消费边界将完整request.model_dump(mode="json")交原ResearchSessionRequest.model_validate严格重验。保留完整manifest、Run/request身份与原None语义；坏manifest仍拒绝，不捕获后丢字段、默认补值或改Task/原件哈希。生产变化仅此转换、输入注解及说明。

独立的条件性缺口发生在原永续agenda选择synthesis_v3时：原协议分类已将v2/v3都归为decision_v2，Host context reader的两处分支却只识别v2，导致缺失perpetualBinding/reviewOriginals及child来源核验。沿原分类器修这两处分支；保留v2、legacy、foreign child拒绝和unknown schema拒绝，不新造Task、协议、界面或翻译层。

原PC→永续研究→官方Host主接线已经存在。本修复继续沿Main代码和原apps/decision-desk界面工作；generic Host回放与真实BTC/ETH domain验收分别取证。

## 已实际验证

| 范围 | 实际结果 | 边界 |
| --- | --- | --- |
| 真实nh06冻结请求与Host原件 | 精确RED 2失败/2通过；候选永久回归4通过/5.15秒 | 非新的Host运行；原失败与340B错误保留 |
| 采用后的物理Main | 4通过/4.69秒，进程exit0；6项输入前后同，实际Main mapper来源，denied=[] | 1个故意坏revision的Pydantic序列化warning保留；local gethostname单列；无SQL/外连/子进程/模型 |
| Host context新用例 | 精确RED 3失败/3通过→同用例6通过/7.58秒 | 真实public resolve入口，受控session/transport边界，非官方Host金融链 |
| 相关原Host/child测试 | 84通过/9.80秒 | 有限2文件，不冒全suite通过 |
| 局部TypeScript strict | 2入口exit0/111.953秒、双log空 | 已审SDK映射及继承skipLibCheck；不是全项目strict0 |
| Python静态 | 新永久测试Ruff0；baseline/candidate均保留同一旧E501（419→422） | 未声称全mapper/全旧后端ruff或pyright0 |

Host context误选测试范围的一次进程已仅owned终止，日志记录337个PASS行/15文件，但没有完整suite终态，不纳入上述通过数。新用例装配中的两次夹具错误、Python首轮NUL日志配置和中间自有断言错误均保留原件，未当成产品RED或覆盖为成功。

## 采用、审查与源码保全

本次2生产、2测试、3原始JSON夹具及1来源说明共8文件，连同此前15项形成23项当前采用清单：[实际采用收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/host-compatibility-main-integration/current-source-adoption.json)，SHA f10cf925b8de6376f64dd5824f6ca248d489aa2c231e2d29b08771a62d0daab2。Main与D共46个实际路径逐个核无reparse/别名、字节等采用清单。D后端仍不是完整当前Main克隆，测试来源以物理Main及具名候选为准。

Root对Host分支非作者审查C0/I0（bcf90fdd…ff2619a），另一个agent对mapper及最终6文件审查C0/I0（8b7665f1…09a4d96、6edfff51…b508008）。审查不是最终产品验收。

两个Main生产preimage另存Main/artifacts/incremental-delivery-20261024/contract-compatibility-originals，原24份bank不动；新构建前的全部586个lib/dist＋2aux共588文件/128791804B已create-only备份，清单SHA f36b9734e9ab2ef3348138e15e1e6ba89e61d9e56a396bd12cb687cb4a74f070，原集合/字节/mtime不变。新源使旧normal9fa仅为旧源历史产物，不能用它认证新代码；installed Prior仍实际49c/2bc144fc…67e6，不能把未部署9fa作为Prior。

## 实际性能问题与资源恢复

原Q033 writer单例profile实际失败：外层87.266秒，原pytest1FAIL/81.90秒，带cProfile的filled execute10.781919秒触原<5秒断言。122输入SHA同、guard denied=[]、方法恢复、物理Main来源；这不是实际PAPER成交或无观察性能。7次完整库存遍历与6次source.matches_identity为此窗口主要CPU项，quote/SQL不是大头；嵌套累计不相加，也不相减推算未观测性能。

纯字符串suffix预筛候选虽保94357个完整名称逐字一致，热遍却0.447606→0.453037秒，未证稳定收益，已拒采；不删除fresh库存/源码鉴真或放宽五秒门。

E盘一度只余约1.2GB，存在后续构建空间不足风险。这里更正先前混淆：原控制器10GiB门检查的是可用物理内存，不是磁盘；空间恢复是本轮资源处理，没有新增生产磁盘硬门。只对16份具名旧测试SQLite快照做独占HANDLE下NTFS原地无损压缩，未打开SQL/改记录/移动删除文件或改ACL。每份前后SHA、大小、mtime、创建时间、fileID、单hardlink均同；两个scope物理allocated共减少13313175552B，E实际恢复约13.52GiB。原15白名单耗尽后另独立授权1文件；原始状态如实保留，独立seal标明最终容量已达。见[原15收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/ntfs-old-test-artifact-compression/final-receipt.json)与[单文件seal](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/ntfs-old-apply02-backup-compression/seal.json)。正式data/服务/profile/pointers未触碰。

## 下一实际动作与未达项

按当前23源、真实installed Prior和588输出备份准备新完整normal，原UI＋Host各一次build、资源/源码/registry守门保持。新normal成功后才按其真实native指纹、17stage字节和新来源封下一fresh Host，不重跑旧Run，不沿用a925或旧1934包。

原PC真实鉴权/浏览器下载和离线打开、真实永续官方Host全链、Worker进程恢复/未知ACK、PAPER入场到止损/止盈/到期退出/结算与重启均未完成。file://自动浏览器安全拒绝不绕过。六组合14自然日≥90%、P50≤15分钟/P95≤30分钟、两真实事件族、至少10笔实际PAPER、四周真实评分、30例完整真实失败回放和最终质量门均仍待。目标ACTIVE，不提前交付签收。

物理Main回归原件：[result.json](D:/DecisionHub-v3-development/temp/legacy-reuse/nh06-request-model-boundary/physical-main-verification/result.json)，SHA afeeea0bb736df34caa997f619a74a42e18e760ca44d901d215ebceb34f19a23。


## 新标准构建已实际启动

UTC2026-10-24T16:56:06.7529919Z，session51214/outer MainPython PID18300/FILETIME134373345668856959；实际原producer子进程于16:56:09.705905Z启动，pwsh PID4800/FILETIME134373345697081867，状态RUNNING而非PASS。启动时free physical12766502912B>=10737418240B，8GiB Node child选项只传本次子进程。scope97a19908…ad2ee46/prepared3c82f5e3…22eb6d/controller8a3c0496…6f75f9；Root非作者9ef5895f…92cf019/C0I0。控制器除两处literal外raw等先前成功00ba，私有薄PS仅将old24＋new2作为真数组传原producer，原消歧按prior源码SHA精确匹配；生产脚本未改。此段覆盖上文“尚未启动”，之后只监测同session，不重启/不并发其它业务，不以当前启动记录称新normal或Host成功。

D本地c98fa9e包含从旧D基点同步的Main mapper既有内容，不能把其较大Git diff称为本次Main新写逻辑；本次Main生产实际差异仅已保存4c514395→a4b77be3的严格DTO边界与f8d4c015→e106c3a6的Host两分支，完整preimage及精确逆差另存。Main没有执行Git操作，正式数据/profile/pointers未部署。


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
