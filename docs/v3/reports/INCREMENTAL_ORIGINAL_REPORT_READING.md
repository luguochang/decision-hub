# 原界面读取本次研究报告：实际结果与当前缺口

状态：原API生命周期、原页面正文已验证；409已归因并修复私有guard，正常读取确认本Run质量记录缺失，完整导出未通过。目标ACTIVE，未交付签收。UTC2026-10-25。

## 实际运行与原件

沿Q-047，以nh07正常迁移至0103_perp_funding_reconcile的私有库做mode=ro/query_only的一致性SQLite backup，包含已提交WAL，不改源库。固定本次Run run_f8b79d4daec342b9b27745fc3fb35641 / artifact art_5cc89552320244c6b4df15366e85f755；仍research_only/degraded、无金融/执行资格，不拼旧ls01结果。

冻结包scope545a12db4913505156b3fcc097f2ccc4be3cc258a50cf9da7f6398e13f8ae04a，Root完整非作者读审和8文件逐字核同。真实Main Database.initialize在新副本3.141秒、expected/current head均0103、engine disposed；原Main API通过原45秒就绪门，提供原Main dist。没有改前端、API协议、产品门、正式服务/profile/data/pointers，也没有Worker/Host/模型或新Run。

唯一App outer session84231/PID25408/FILETIME134373839544418944，同Process.Handle：06:39:14.4009848Z→06:54:47.8500464Z，933.4491936秒/exit0（含备份/初始化/就绪/原900秒hold）。两个exact owned Jobs均closed/root exit observed；源DB SHA3d308bc425f509232bd6597fac051beec713c86b8d277e35f444873cc3191cd4、WAL SHA36cc31fc41b73263c929f5df073658cabf42577317f3c803b192be41c3405bdf前后同；SHM只观察，不宣字节不变。

[terminal-proof](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-r01/terminal-proof.json) SHAc17d9df435b9007ea399330da76db9cd249ff8c7a4b3796a8a70411877ae38c4。outer0只代表此API验证窗口正常结束，不代表完整报告验收通过。

## 浏览器实际结果

CUA Tab10实际打开原同源 `http://127.0.0.1:18057/#/report?run=run_f8b79d4daec342b9b27745fc3fb35641`。原导航今日研究/市场与事件/研究/资料/管理与审计保留；本次报告正文、9份证据和自动检查记录可读，明确显示“仅研究”“未授权交易”“仅自动检查，独立复核未完成”。原页面同时显示质量原件不可读取，导出按钮不可用。实际截图已在本会话工具输出展示，没有声称本地截图文件已归档。验证结束已关闭临时Tab；API按原hold自然结束，不是持续用户服务。

[浏览器观察](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-r01/browser-observation.json) SHA7412ccd2acd4b8ccc94467c7738344e0270c356da459189053e457591afc31a1。只是本次受控generic报告，不等于BTC/ETH真实合格研究或完整永续HTML导出。原generic仅JSON+Markdown；原perpetual页面JSON/HTML仍需真实链路实测，不临时扩写generic凑HTML。

## 409与最短后续

除浏览器请求外，仅增加一次精确snapshot GET，实际409/detail research_report_snapshot_verification_failed，57B/SHA b05a33e7f61a96324a1efe7a222044a82e5b4f1f81d86c473822164cec25fcc2。不是可legacy fallback的指定404，所以没有强行status/quality fallback、点击重复读取或下载。

仅对新副本做一次query_only/BEGIN-Rollback三表诊断，本Run的research_publication_bundle_jobs / research_publication_bundles / research_publication_bundle_confirmations均无行，total_changes0/rollback/close。[RO原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-r01/publication-state-readonly.json) SHA844e51abcceb798851256c3e27ad08e7967f86aaaa93aaedc3b36c7cdf3b62dd。原research --once本来包含一次publication tick，不能因无独立进程就断言少跑归档；也不能根据空表认定pending Job。

原reader无Job/无fence通常返回None→404，而本次409可能在factory/更早读边界抛出。当前尚未捕获底层异常，不能先归缺bundle、profile或源码损坏。已安排最小独立诊断候选：仍原物理Main API，仅私有childguard追加限定文件的透明exception trace，未来单次重放同snapshot GET，原300/45及清理保持；不替换函数/Database/__file__、不改409、Job/状态或造bundle/ACK。该候选本记录时仅准备，未执行。

真实Provider单stream canary另仅D私有实现/mock准备，未发真实模型请求。最终仍保独立复核/校准、六组合14自然日≥90%、P50/P95、两真实事件、十笔实际PAPER及恢复、四周评分、30完整真实失败回放、最终质量与手册等全部门。


## 最新实证：409来自私有guard，正常读取确认质量记录缺失

本段覆盖上文“尚未捕获异常/Provider尚未实际调用”的当前状态，原失败记录保留。diagnostic01全局trace增加启动开销，原45秒ready失败（exit1/59.6847454秒），未发snapshot GET；未将此失败当产品缺陷。diagnostic02改为原API ready后开启原线程透明trace，实际单GET重现409，捕获先行PermissionError/controlled_host_write_outside_attempt，从confirmed_snapshot→read_confirmed_report_snapshot→lifecycle._ro建RO连接时抛出。[实际异常凭据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-diagnostic02/terminal-proof.json) SHA7bb8e8b53113cc9ade1035abf5afc41ce8210930dc4ac51b460c64bc8e8fceb2。诊断只有截断20帧，不称完整底层stack。

根因在隔离脚本：原guard将合法Path.as_uri()+?mode=ro当普通Windows文件路径解析，误判为外部写入。只在私有guard新增规范file URI只读分支，解码后仍执行原WORK内resolved-path检查；产品校验、读链与Main源码未改。真实小SQLite同probe旧guard RED→候选GREEN/0.281秒，query_only/changes0/DB哈希同，普通私有RW保持，13个越界/非规范URI实际connect均PermissionError；socket/file/process原分支不变。冻结scope fd314367…e60583，非作者peer C0/I0，凭据77792e7c…059e7e。

随后唯一uri01原App读窗已完成：session55415/outer26944/FILETIME134373865139619910，UTC07:21:53.9150315Z→07:27:27.7404685Z/333.8255736秒/exit0，同Process.Handle；原initialize3.203秒/head0103、ready45与300秒人工读窗不变，2个exact Owned Jobs closed/root退出已观测。源nh07 DB3d308/WAL36cc前后同，8冻结工具同，SHM仅观察。没有新研究、Worker/Host/模型/产品代码或正式服务变更。[终态凭据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-uri01/terminal-proof.json) SHAff04aaed5dcd7dd8f7be583ea650e5ce863acac9c5c9ebe95385c2c11ef30112。

CUA Tab11实际显示原导航、同Run正文、9证据和“这份历史研究没有发布新版质量记录，暂不能补认当前资格。”没有导出按钮，已关闭临时Tab。Root另3个精确GET/无重试：snapshot404/research_report_snapshot_not_available，status200/legacy_full_original，quality200/unavailable/research_quality_legacy_unavailable；原页面按原协议正常fallback，没有强改409或开启导出。[页面与HTTP实证](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/private-original-app-nh07-uri01-root-reading/observation.json) SHAf7c2cd33fb9298cf355dff5b77ddbf44196436102ffed0b5fd0b366cfdde2600。原正文可读已验证；完整质量报告与导出仍未通过，不能补造本Run质量记录或冒称历史兼容缺陷已全部修好。

真实Provider连通性已另经一次原SDK直接stream通过：gpt-6.1-sol、合成pong提示、max50、1 request/1 fetch/HTTP200，Node5.4636875秒/exit0、Context disposed；无agent/tool/研究/DB/新Run。[单次连通实证](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/provider-canary-actual01/success-proof.json) SHA8c9b858d372b24ad6b9c3cab786aa7f81672c5fc7f73c0e42615331cd5aff15b。凭据只原loader到子进程环境，不复制store或输出key；原usage保留，不称账单封顶或金融/研究认证，不重复canary。

下一步复用原PC的PAPER签名设置、command.v3、独立reviewer与finalize做单项BTC4h链路；不是重跑本generic受控案例。源码证据表明完整RuleReader未实现，但原单位研究明确使用server cost assumption并披露实际账户费率/未来funding未知，不以这些执行依赖先行阻断研究；真实PAPER规则仍必须另补。优先复用现有私有角色启动，不为验证修改产品launcher。当前该新链仅源码方案，尚未发真实市场或研究请求。normal6dd/a067/23f10c保持；原PAPER5秒、授时、独审/校准、六组合14自然日、四周评分及全部最终门保持，目标ACTIVE。


## 原BTC4h首次live启动：运行时前件失败，未调用模型

新私有原角色包scope208831cc51f336a1e7a4463530a4fcd4a324d9e56844160a26d0701f0023f7c8已由Root读审并核17+101输入。它沿原签名PAPER设置→command.v3→原Worker/官方CLI/真实独审，不复用nh07模拟材料。PAPER2500USDT是明确虚拟测试选择，不是原UI默认或真实资产，不提交订单；API协议验证不能计GUI提交验收。Root helper8纯测试、C deadline42纯测试及限定静态/非作者复核只是前件。

实际唯一启动08:11:38.3573326Z至08:12:49.7685154Z，71.4113561秒/exit1，session13855/PID9716/同Handle。正常安装69.313秒/head0103/engine disposed通过；隔离启动器选择Windows PowerShell5，执行策略在owner脚本加载时拒绝，未执行其主体。API/MCP/Host/Worker/command/Run/Provider均未到。[失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual01/failure-proof.json) SHA62ad4e897e587ee05f660dce6ede0352680bad720c1fba126efc79ecfd06914b。3exact Jobs清理/errors[]，118输入前后同；不重跑或改写旧包/WORK01。

Root核对既往成功canary的真实Core7.6.5，并在sterile环境只读查询两运行时：Core现有effective RemoteSigned，WindowsPS5现有effective Restricted；两者无Process policy环境值，无GroupPolicy设置。本轮未改策略/注册表/UAC，也未加Bypass。[运行时查询](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/powershell-runtime-review/review.json) SHA5abb76e4e2eddb246f098b92df3d63dce5b7134ec5c888c2dc61c4a441046d9c。下一仅独立私有包修正到已正常的原Core运行时，用原owner helper同字节小fixture验证后冻结新包/fresh WORK02；此记录时尚未启动02。Main产品/原UI/normal6dd/正式服务、profile、数据与pointers不变，完整研究/独审/报告/导出与全部长期门仍未通过，目标ACTIVE。

## 第二次启动：原初始化通过，Host 未就绪

新Core包scope `0f271f37f451b126af533c4175ab94fd820ee516b0d5a4c358a7d0927614a5e3` 的17私有和110只读输入经Root逐项核对。唯一actual02，session71994/outer19500/FILETIME134373900762148582，同Process.Handle：08:21:16.1843371Z至08:23:47.4447124Z，151.2605022秒/exit1。原initialize75.125秒/head0103、owner、API与采集服务ready、MCP socket通过；Host原20秒未得HTTP200，ready/command/Run/Worker未生成。7个owned Jobs关闭/errors[]、127输入后验相同。[第二次失败原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual02/failure-proof.json) SHA `f77678a0615bcf4f8850db151e06a912cdd7dfb223ab30c7d1b77adfa776cf4b`。

Host原日志只有脱敏的官方启动URL；本次旧观察器未保存HTTP失败status/body，因此实际失败分支仍未知。独立源码复核发现验证启动器省略原 `tools/dsh_native_acceptance.py::_register_workspace`，而原Host `resolveWorkspace` 只lookup，未登记时必503；创建工作目录不代替原workspace/create。源码审查收据 SHA `54d8cb7400be0d5c33eebdf324723f552858f75f5148e53ae3cbf160fb323444`，现有测试分支仅阅读、未执行，不能冒本次真实响应。

接续仅在D私有工具补原工作区认证登记及安全失败诊断，不新增产品插件/界面、不写registry或重建cookie、不延长Host20秒。旧02包与WORK保全，不盲重跑。新helper尚在离线准备，未释放actual03；Main产品、normal和正式服务/数据不变，全部最终门仍保留，目标ACTIVE。

## 第三次启动：工作区与 Host 通过，合约资料连接失败

实际scope `f234b45abb55ed43c6333b4626b932ef26c006cb85e6e40571ecc8e73f8a6cb5` 经Root核20+115输入。原工作区登记经真实官方303认证、一次RPC完成（2.859秒）；Host同一20秒截止内首次GET返回200，ready/hub/version及a067构建身份匹配。原Host两fixture实际2PASS/58按pattern跳过，注册helper9Python+3Node纯检查和非作者复核只是对应前件。

唯一actual03：08:52:39.3455723Z至08:54:42.8787552Z，123.5333265秒/exit1，session16305/PID21260/同Handle。Host ready后，首个 `/v1/decision-settings/instruments/prepare` 返回本地503；未到研究command/receipt/Run/Worker。[终态原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual03/failure-proof.json) SHA `54332e2f2cbc4399da425fbf9adfcbba41b85eb6e8f9e15bbb5e94aa18e410af`。135pins前后相同、7exact Jobs关闭/errors[]，原包与WORK保留不重跑。

一次限定RO查询fresh03原录制：唯一BTC metadata请求耗69.636ms，error_code=ConnectError，上游status/body_hash/body_ref皆null，并未收到上游HTTP503。[只读归因](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual03/metadata-capture-readonly.json) SHA `b282853f97aff4493a1d227e7d9cdfa4a1b0fbc1b07db785da8c35e2dc367665`；query_only、短事务回滚/关闭、changes0，DB/WAL字节相同，不冒SHM全同。原捕获表没有更细cause/message/errno，原本地503 wirebody也未保存，故不能认定代理、TLS、DNS或guard根因。

下一先做一次隔离、单公开GET的原HTTPX传输诊断，不重复数据库/Host/整链、不带模型key、不改TLS/20秒/原guard。私有API代理选择仍只是源码候选，未先改代理或扩大权限。新签名Client非200安全诊断副本已4MockTransport GREEN但尚未集成/实请求；旧6698/原8测试和report readback保持原样。本段尚无该单GET实际结果；全部研究、独审、完整报告/JSON/HTML及长期门仍未通过，目标ACTIVE。

## 单次传输诊断：当前直接 DNS 失败

没有再跑整条服务链。单次诊断使用原Main HTTPX 0.28.1/Proactor、同字节Python guard、原20秒/TLS及NO_PROXY，明确只许可该公开请求必要的www.okx.com:443，不声称重建03全部环境。单GET在0.156秒失败，异常链为ConnectError→ConnectError→gaierror(errno11004)，无上游HTTP响应。只追踪真实guard限定frame，未见其静态拒码，不能冒完整socket审计；03原录制仅ConnectError仍保留原边界，不回填成DNS。

[单次诊断终态](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-market-transport-diagnostic/terminal-proof.json) SHA `3b7253f002e32f269a1c510cc36ede2e46a41e005b597bde40709b1fe3c0bb94`：Owned child29488/FILETIME134373929964992444，工具exit0/Job关闭，10具名输入前后同；exit0不等于请求成功。未读模型keys、未读registry/SQL、未API/Host/Run/模型或第二请求。

下一先核原有公开代理选择，离线验证仅私有API的合法HTTPS_PROXY接线仍保NO_PROXY/TLS/时限/端点；不盲用模型专用代理、不改系统DNS。尚未选择或执行修复，没有新整链。安全非200观察副本63944另经非作者C0/I0，未集成或实请求。当前目标ACTIVE，最终验收状态不变。

公开代理选择补充：C以原loader仅允许PUBLIC设置的读取和原launcher同源OS静态配置核实，PUBLIC env未配置；现有OS general/HTTPS代理已启用，对OKX不bypass，回环端口7892/无userinfo。已明确选它作为私有公开请求接线候选，先真实HTTPX离线proxy-map检查，再唯一单GET验证；不借模型专用配置，不改系统网络。此处尚无该候选实际结果，不能称连接已修复；A有一次单项验证授权，整链04未放行。

## 公开连接通过；首个真实BTC4h Run失败于私有验证器

原OS general代理经同源picker实际选中。原HTTPX离线映射确认公开HTTPS走该代理、回环仍direct；唯一OKX GET返回200/code0/BTC-USDT-SWAP，耗0.75秒。Owned child9920/FILETIME134373934458928795，exit0/Job关闭，14输入前后同。[单GET终态](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-market-proxy-diagnostic/terminal-proof.json) SHA `04f8b315e3f1617c360c7ba59c085b422daeebaf1f51e4e5935ff1c799d16475`。这项连通成功不计研究资格，不回填03未保存的cause。

actual04采用scope `f1d37e92dd9a94f3c7c4fb0d30a0405d1ede0c11422aba708fd5c2a415214a66`，21私有+120只读输入。只在私有API/MCP接原选中公开代理；MCP另保原PUBLIC字段给原trust_env=False文档获取。集成63944安全HTTP错误诊断，NO_PROXY/TLS/Host和Task期限、端点权限均保留。没有产品或前端重写。

唯一actual04 UTC09:27:52.2684537Z至09:31:49.8646257Z，237.5963373秒/exit1，session85143/PID27744/FILETIME134373940722941848，同Process.Handle。原初始化、owner、API/acquisition/MCP/Host及真实合约metadata通过；原签名PAPER设置与command.v3得到真实queued Run `run_a93e89b82c724893b3b4d9a6ee32f8a7`，随后Worker failed。8exact Jobs关闭/errors[]，141pins前后相同。[失败终态](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual04/failure-proof.json) SHA `1bf575b1985c0bfe66ceefc7b92580b12030f287831c71f2f06f2283d1114402`。没有报告GET、重新受理或金融合格声明；原04包与WORK保留。

两次限定mode=ro/query_only事务只读该Run元数据和trace，rollback/close/changes0，DB及WAL前后同，SHM不宣称同。实际run_calls/run_steps/session_links皆0；失败在原_execute_entry的schema probe、Host提交之前。trace为orchestration/cancellation_projection_failed/retryable=true，39字符消息仅以相等判断确认为原“Research schema probe was not confirmed”。不是Task3600秒或ready240秒耗尽。[Run元数据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual04/run-failure-readonly.json) SHA `51c333308c7a6dde1e22b697c1b8ddc856179fe0faa6db2346f4c2e8bca75e8f`；[trace归因](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual04/failure-trace-readonly.json) SHA `03af79d4b61db3e6acf4cd0486ad27f1ceef2c51b0882120534d77a768cf84e6`。原11项输出元数据已含必需Pack v2/Task v2/compiled-policy v1；原首轮允许尚无Method/Exec/Link，不能把research_method_binding_failed标签等同资产缺失。

实际不兼容在私有childguard：它只接受canonical mode=ro，而原DshProjectionSessions以原quote公式生成mode=rw并拒绝创建缺失库。原creator在完整旧guard下的小SQLite复现确实抛PermissionError；仅给默认WORK/hub-data/db/decision_hub.sqlite3增加精确原producer RW URI分支后，真实Main物理session SELECT1/changes0通过。18项包含原RO/plainRW、14项越界或非规范拒绝及missing DB仍拒创建；两私有DB/WAL同、Jobs关闭。[GREEN原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-runtime-projection-uri/green-result.json) SHA `c5b8be8685a782b80ebe63c52fdf1bfbfa05a4ee459d40c8d56430523299571c`；guard SHA `d3829fe47704335e1cdeb005bca63d027c5e9a4c1dd7287224ab9b977616e864`。没有改产品session/50ms/Task权限或期限；技术fixture不冒研究验收。

下一仅冻结新05包并独立核读后运行新私有Run，不重跑04或补造其材料。本记录时05尚未释放。normal6dd/a067、原UI、正式服务/profile/data/pointers保持；完整真实研究、独审、报告和JSON/HTML以及全部最终时间与金融门仍未达，目标ACTIVE。

## actual05运行接续与手册候选（截至UTC10:07，未终态）

新scope `370c58951a35dea9ca4d53aa0f2f53d374e37023c61f0f560f8a47b5660a1548` 由Root独立核21私有+125只读实物，唯一差异为精确RW guard与fresh WORK05等路径；产品与预算不变，非作者guard审查C0/I0。唯一actual05 UTC09:51:49.9079832Z启动，session43658/PID3728/FILETIME134373955099443919，supervisor15636持同Handle，A正在监测。不得在续接时再启动此包或重复创建Run。

原initialize72.375秒/head0103，原Host/真实metadata/PAPER设置通过；Run `run_19001efca3fd49d5a62883d9770ebec6` 于09:54:18.439742Z真实queued。已跨过04失败点，原Task/Method/Exec/Link齐备，原Task deadline实际为10:54:18.439742Z。观察器09:55:33.200106Z一次冻结，remaining3525.239636秒，mono210661.843→214187.082636；不刷新/增加预算。当前只有MCP活动和运行状态，尚无Worker终态或报告成功。

Root实际在IAB Tab12原settings页以原表单完成本机登录，页面回读同owner保存账户、BTC合约和原导航。未再次读取metadata、提交研究或更改设置。B已核本次held command/receipt SHA与旧原校验函数PASS，44源针相同；该结果仅证queued和同Run，live05-report-r01仍未创建/GET。待Worker0后在原300秒hold内读回同Run报告、原页面与JSON/HTML；失败则保留原件，不盲重跑。

并行整理的[用户手册候选](E:/project/study/codex/dsh/decision-hub/docs/v3/USER_MANUAL.md)与[运维手册候选](E:/project/study/codex/dsh/decision-hub/docs/v3/OPS_MANUAL.md)已按原页面、启动器和备份源码核对，D本地提交 `dc24c4cd1db98371c8756b3ddb562c204052baf6`。只写文档，没有执行所列启停/备份/恢复命令，不计T-801/T-802最终验收。Main同字节采用，formal缺原launcher/backup源，未复制这些相对源码链接依赖的手册。全部真实长期/金融门与目标ACTIVE保留。

## actual05当前进程已不存在、业务状态滞留（UTC14:44）

本段覆盖上段“仍运行”，不是补造本次终态。Root实查旧exec43658已不存在，outer3728/supervisor15636均不在；限定审计确认八个owned身份也均不存在。原私有DB同Run仍running，updated停10:09:59、lease10:13:59，artifact/error/finished全NULL。DSH最后seen10:09:52/last_seq973、Task原截止10:54已过，没有trace/result ref；26条trace最后为10:08:35 acquiring_evidence/tool_completed，共12次工具启动。没有报告、可核退出码或原因证据，不能称活跃/成功或给终止归因。

[只读整合证据](E:/project/study/codex/dsh/decision-hub/evidence/legacy-sdk-review/btc4h-live-actual05/current-state-readonly-20261025T1444060949644Z.json) SHA `1aa1c961232eace8dcedfabf677c9055e8ac0fc939227680d9542ad406b5437d`。DB/WAL前后同，changes0/rollback/close，SHM未宣同；observed_utc被PS写为+08，与原14:43:17Z同一时刻。工具级错误capability_deadline、acquisition_fred_browser_failed、historical_dataset_coverage_missing保留为线索，不当作Run最终cause。没有再GET、模型、新Run、业务写入或调整期限。

下一查原过期租约回收、checkpoint/resume和截止拒派发的现成路径，先离线复现，再决定同Run合法收尾；不重跑05或人工改库。原总控八项及v3全部需求仍未交付，现状对照已补[覆盖账本](INCREMENTAL_DELIVERY_COVERAGE.md)。目标ACTIVE。

## 原05已由原Worker落真实失败终态；资料前件开始实证补齐

fresh recovery01/02以原两库及WAL一致副本，调用物理Main build_research_worker、原精确claim和checkpoint恢复；原Selection抛PermissionError/context_selection_deadline_elapsed，原Worker写failed/research_worker_failed。两次旧脚本整体FAIL保留：唯一socket事件是urllib3导入时被拒的本机IPv6探测bind，计数错误地要求所有bind为0；没有Host或外连，未为此改产品/开放bind。该固定码与当前schema对应原research_context_selection.py截止检查；未保存真实throw-stack，不冒称已录实际throw-frame。

Root完整读审最小finalizer，核原八个owned及两supervisor不在、原四SHA与Task哈希；两库先backup到fresh D:/DH-btc4h-recovery-backup01。唯一exec6115已exit0，原Run及Link由原Worker落failed/research_worker_failed，finished/terminal UTC15:06:34.244982，lease清空、artifactNULL。原Task、旧role payload哈希及Run集合不变；Host0、outbound0、被拒bind1、越界写0。原截止10:54:18.439742Z保持，两备份quick_check均ok。[实际收尾结果](D:/DH-btc4h-live05/artifacts/expired-recovery-result.json) SHA `d893684f500d1ddfb6947b778f6362b2c19410bcccd941f5e6359b7c1a375166`。这是过期收尾通过，不是研究成功，也不证明最初进程消失原因；禁止再恢复或重跑05。

取数缺口也从原件核实：05私有data_jobs=0；原主session压缩archive SHA `4c98ddb79b73a9ac1ba27a0c98e1c1d83e77e435d3af8e44c378aadf657134b3` 的seq834直接记录BTC daily窗口Aug01T00Z→Oct25T00Z。新history01副本以原adapter实测coverage_missing后，唯一原DataJobService.create→HistoricalDataJobRunner.run(queued_only=True)已完成，job `datajob_5b2a6c0ac4f2492c8ce366a0cd0ab1ad` /attempt1。Gate HTTP200原件8342B/SHA `270f120523268839a8ea35d9537cca0a3a5e977ada23a281b2be3dd014438aa7`；manifest/三partition/capture关联及SHA已核，31+30+24=85条日线、UTC Aug01–Oct24每日唯一、缺失/额外/gap皆0。原adapter同窗口实际回读9facts/1evidence。[数据Job结果](D:/DH-btc4h-history01/online-one.result.json)。采集和记录UTC15:16:18，保持新known-time，不回填05或冒PIT合格研究。未来fresh06在原初始化成功后、模型dispatch前独立准备资料并回读；本段未新Run/模型。

FRED另有确定的私有验证器冲突：原createArtifactOnce使用异步hardlink原子落盘，旧Node guard全部拒绝link，失败receipt写异常又被吞。候选仅让原fs.promises.link在两端原私有真实路径校验后调用；原函数AST片段逐字提取，旧guard实际RED→候选9项GREEN，包括内容/hash、幂等不覆盖、两端越界及junction拒绝，linkSync仍拒。候选02325054…78029及原件在btc4h-acquisition-atomic-link；旧包/Main同，未反填05原因。下一新WRESBAL原collector20秒公开诊断仅准备，尚未发HTTP/browser。全部独审/报告双导出/金融/长期门仍未达，目标ACTIVE。

## 原FRED采集器已实际保存成功DOM与收据

首个fred01诊断在模块装配处失败：TypeError/chromium undefined，原collector和browser/public HTTP尚未调用。原因已核为诊断入口对CJS package require入口再dynamic import后错误解构，不是Main collector缺陷。Node14664/FILETIME134374158373948690，15:30:37.391148Z→37.902972Z/exit1，exact Job关闭、输入同，旧原件保留。新共享module-entry改用原包require('playwright').chromium；实际离线preflight 3.84秒/exit0，公共函数形状正确，collector/browser/HTTP皆0、15输入同/Job关闭，两个被拒tsx named-pipe事件未被放行。

Root读审后唯一新fred02于UTC15:36:14.439866Z启动，15:36:20.439601Z终态，exec6.599秒/exit0，Node16464/FILETIME134374161744418769。物理Main acquireWithPlaywright、20秒/DOM/TLS/原route与已审私有link分支：launch/newPage/route/goto/content/close全部返回，WRESBAL主响应HTTP200/79156B，原DOM SHA `7649d5a75ee5156401133d07e69bc84a4565c0da30287a4b74227da9bee241b7`，原成功receipt文件SHA `6de729f6f8f0d2cc487b51e7424cae31462b9fe0a30f7e9b88779a1a8e0b5f13`。15输入前后同，日志失败0，exact Job关闭/root退出已观测，无Hub/SQL/模型/新Run。[采集结果](D:/DH-btc4h-fred02/artifacts/diagnostic-result.json) SHA `2fd98b94e2b2bf434c79dcc1b4c08248b605b2193c9403d48b37f91fe4dcc9c9`；[外层与清理原件索引](D:/DH-btc4h-fred02/artifacts/outer-result.json)。

直接collector的received_at为null，原Python browser_fetcher还要求原server/Hub真实签收时间及sourceRegistry/DOM核验。未补造received_at，未把本次成功反填05原因或称完整宏观/金融验收。下一fresh06包正在准备，在保原initializer与所有期限的基础上先做历史资料准备，后续同包可用原acq API/Hub真实签收做无模型功能预检；本记录时06未创建/未派模型。全项目及原总控仍未交付，目标ACTIVE。

## actual06在资料准备阶段终态失败，尚未派模型

Root再次逐物核155输入、WORK06不存在及私有四端口空闲后唯一启动。exec96247/outer17716/FILETIME134374172483313557/supervisor21688，同Process.Handle：UTC15:54:08.2922616Z至15:55:37.2396091Z，88.9474773秒/exit1。原initialize66.937秒、head0103、engine disposed；历史准备唯一job `datajob_03c44ae2c74a47ba849ac223473c8c43`/attempt1失败。API/acquisition/MCP/Host、研究command/Run/模型均未启动；四Owned Jobs关闭/root退出已观测，cleanup errors[]，155输入后验相同。[实际终态](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual06/outer-terminal.json) SHA `b5b11f9233dfc6b2516a9fcf8e8ed728e582c0f3aeba5e70cc9e000f6b691a75`。

限定RO核原Job/manifest/raw：status failed、0行/0分区，manifest固定gap readtimeout；唯一capture `raw_cb7d4803ebe3442e983192e861d6c940` 15:55:20.228945Z→15:55:36.623568Z/16.394623秒，没有status或body。原固定错误精确比对为ReadTimeout。两路径同原Gate loader、15秒timeout、proxy/NO_PROXY/guard授权；环境继承范围不同，但没有实际证据证明造成失败，不能推TLS/隧道/上游具体阶段。[只读证据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual06/history-failure-readonly.json) SHA `24288f6c9183b3e18fbacb5e50dd77023dc40e9a176652b3cdd78f7c4d150959`，DB/WAL同、changes0/rollback/close。未重取、改超时或重跑06。

已有history01同窗口真实85日原件，因此下一采用数据闭包忠实恢复，消除重复公网取同历史的依赖。整库seed会带入派生旧Run、PAPER/settings待处理状态，且旧非历史body闭包不完整，已拒绝。私有小corpus只带已验真的原completed Job、原RawCapture全列/原ID/context/原时间，以及1manifest/3partition/1raw原字节；新库原reader仍须验证原件，禁止改known_at或把旧响应冒新fetch。该数据恢复经原ORM实现并明确记录来源，其他业务行和06失败不搬；实现/测试正在新D工具里，未启动07或模型。原系统与所有最终门保持，目标ACTIVE。

## 固定历史闭包验证通过；07准备已冻结，尚未启动

私有corpus工具真实RED后10项GREEN：[结果](D:/DH-history-corpus-test01/artifacts/green-result.json)。原reader/capture_scope严格读回85日/原raw receipt，原两行全列与源时间一致；五文件逐SHA相同，重复恢复零写，篡改、PK冲突、目标文件冲突和早于原recorded_at的PIT读取均拒绝。原06失败Job及其他表不变，两处源DB/WAL不变，HTTP/model0。这是资料恢复验证，不是新研究或完整PIT/金融验收。Root完整读审并独立验证工具11e4619b…f6b71、manifest1d656e76…f04fa5。

fresh07包[scope](D:/DecisionHub-v3-development/temp/legacy-reuse/btc4h-live-corpus-prepared/scope-prepared.json) SHA `40fdb559109bc911ee4c39dbbf9690a569a29b851b064acfbed8256350784d34`，22私有+141只读共163针经Root独立逐件核字节/SHA相同。tsx loader为Main原pnpm链接，物理指向tsx@4.23.13。原initializer、signed63944、Host/deadline/guard023字节保持；限定差异为07实例路径及初始化后、Provider配置前的corpus-restore角色，无Provider/服务key、LLM0、端点空/公共网络0。恢复后原历史reader应直接复用，不再取同85日。WORK07仍不存在，私有39071—74空闲；尚无07服务/Run/模型或报告。不得把准备完成写为启动成功。05/06封存不重跑；后续只允许Root同Handle持续观测新07实际终态。全部最终门与原总控未完成边界保持。

## actual07已实际受理（本节尚未终态）

Root唯一启动UTC2026-10-25T16:31:52.4169791Z，exec28550/outer20636/FILETIME134374195124551385/supervisor29212、scope40fdb559…784d34，同Handle。初始化71.937秒/head0103通过，原历史corpus恢复2行5文件后，原reader85日/原receipt核验、producer_invoked=False，历史没有重复HTTP。原API/acq/MCP/Host就绪，原签名请求真实受理Run `run_3043def3338d461bb733a1c425fae7af`，admitted16:34:22.025381Z、原Task及runtime截止17:34:22.025381Z，观察预算选定后未续期。原command SHA780e3025…2fc5、receipt1a798cd2…d342均保存在07/artifacts。Root已在IAB原设置页正常认证，实际回读owner、2500USDT模拟账户及保存版本1，页面显示研究进行中，未另行研究或修改设置。截图[原设置页](D:/DH-btc4h-live07/artifacts/original-settings.png)。UTC16:39仍由Root持exec live handle；没有Worker终态、报告读取或新金融验收，不能写成功或重启。

## actual07：原独审、发表、原PC与浏览器双导出已形成单例闭环

Root持同Process.Handle至真实exit0：UTC16:31:52.4169791Z→17:12:25.9252458Z，2433.5083933秒；exec28550/outer20636/FT134374195124551385/supervisor29212。10个exact Jobs清理errors[]，163pins前后相同。Run `run_3043def3338d461bb733a1c425fae7af`、artifact `art_821212911a254059aa2ef72a61988d9d`；Run degraded、errorNULL、lease清空、finished17:07:02.409865，Link completed/lastseq6742/17工具。Root限定RO变化0、rollback/close、DB/WAL前后同。[终态及原业务证明](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual07/completion-proof.json) SHA `48b344e0b6ad2555bee8d52896f1cdbea33c24a77861704da32a0a8802382dcf`。原日志binding_readset_conflict warning未删除，本轮最终成功保留原件，未根据warning推定失败。

当前永续V5 renderer固定research_only/research_complete=False，是原分层语义；本次source/payload的native_review_verdict=approved，原action_publication.status=available、accepted17:07:23.880548，选择no_change/flat。ACTION三个flag0不必然跳过永续publisher（原Worker fallback仍调用），冷启动无校准也不是新增全局拦截；独审与金融资格、实际执行分别保持原含义。金融/执行两个标志仍false，未经校准research_trial不冒金融合格。

A用本次原command/receipt绑定，唯一鉴权GET返回200/full_saved_perpetual_original，787459B/body SHA `e5c0f39af71c7f61df0fac75584b9440eb4469a3ae4c06c1dbd6c4403d96ff17`，24.1864秒/44pins同。唯一同body受控原Main decoder/组件/Blob导出11.8143秒/exit0，原文、artifact、definitions、action_publication及各hash保留；该受控证据不冒真实浏览器下载。[受控render原件](D:/DecisionHub-v3-development/temp/legacy-reuse/btc4h-live-report-readback/attempts/live07-report-r01)。

Root另在原IAB同Run页面实际读到正文、独审通过及发表时空仓选择，并点击原JSON/HTML两个按钮完成真实下载。原Downloads文件保留，字节副本：

- [浏览器实际JSON](D:/DH-btc4h-live07/artifacts/browser-reading.json)：1457939B，SHA `ae9a27d54684013424b4995abf4390c3002083d9c127833136c284fd5cc96555`。
- [浏览器实际HTML](D:/DH-btc4h-live07/artifacts/browser-reading.html)：2249748B，SHA `5853b89a3e896d8a0cc14c5d93146bcdd0fbf4b9c22bc5751b3841772c605734`。
- [实际浏览器下载核对](D:/DH-btc4h-live07/artifacts/browser-download-result.json)：hidden pre内JSON等于JSON文件，0活动脚本/0外部加载资源，原CSP default-none。Root初检错误假定JSON放script，实际源码用hidden pre；已改核对方式，未改产品。未执行离线浏览器打开，不绕过旧file://阻止。
- [原PC布局截图](D:/DH-btc4h-live07/artifacts/original-report-desktop.png)：1280×900实际原界面，临时视口已恢复。截图证明原页面，未重新搭UI。

受理→artifact实测1960.384484秒（32分40.38秒）；原DB latency_ms1755262与outer2433.508秒各有不同范围，全部保留，不混算性能。单例未证明稳定性或P50/P95达标。原发表选择是维持空仓，因此本轮实际PAPER成交增加0，未为10笔目标改动作。完整六组合、事件、后继、10笔及退出恢复、四周评分、14自然日、30完整失败回放及最终质量/交接仍未验收。[单例边界摘要](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual07/functional-chain-summary.json)。原candidate unknown_reasons有一条乱码尚待独立定位，原件保留。

## PAPER工具独立复审后离线准备

新增私有工具[paper-original-api-lifecycle](D:/DecisionHub-v3-development/temp/legacy-reuse/paper-original-api-lifecycle)复用原Client及原typed/PC schema。初18GREEN经非作者发现5个Important，随后完整原模型/AST正例和真实反例RED后修复：原display→intent恢复、未知ACK只查不重投、execution串单联结、v2及protocol绑定、全部输入先检、业务错误原body/status关联保存且配对凭据排除。两个真实边界（旧同账户episode、合法结算推进ledger）按原source保留。Root独立unittest discover实际33PASS/31.242秒/exit0，非作者C0/I0/M0；[验证原件](D:/DecisionHub-v3-development/temp/legacy-reuse/paper-original-api-lifecycle/verification-result.json) SHAa11d945b…4666c。该工具未实际--execute/HTTP/PAPER或修改Main/07；不计10笔，不取代真实UI确认与原报价/退出/结算组件。

## ETH真实资料、六组合caller与研究内容修订（尚非新研究验收）

Root唯一ETH资料准备已同Handle终态exit0：UTC17:34:15.0430978Z→17:35:30.2926568Z，75.2496688秒，exec99897/outer8132/FT134374232550804875。原initializer70.359秒/head0103；job datajob_b01c5f5366a6464ab7e0758010d37129经原runner完成，HTTP200/8434B/body121ee597…fccc，真实recorded_at17:35:29。原adapter严格回读85日/3partition/1capture/0gap/原receipt verified，27pins同/两exact Jobs关闭，无模型/研究Run。[原读回结果](D:/DH-eth-daily-history01/artifacts/historical-preparation.json)。Root首次mode=ro的前后sidecar存在性比较未通过且未保留before哈希；不推断变更原因，不声明首次同字节。闭库且WAL无非空内容后的独立immutable RO核job/capture为changes0/DB-WAL相同：[当前只读证明](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/eth-daily-history01/source-readonly-proof.json)。已采时间不得回填，BTC archive不改名当ETH。

[六组合caller](D:/DecisionHub-v3-development/temp/legacy-reuse/perpetual-signed-entry/README.md)仅原API参数化，Selection显式且所有asset/horizon/owner/hash/ref绑定保留；Root28PASS2.50秒、非作者C0I0M0。未HTTP或新Run；ETH协议fixtures不充行情。旧observer仍BTC4h限定，后续包装须单独核验；实际预算3600/5400不随预测期放大。

原内容缺口已在D做[最小修订](INCREMENTAL_RESEARCH_CONTENT_REPAIR.md)，提交80e98ce：用原exit_policy/basis/analysis及monitor codec补保护/到期和hold换选说明，原独审保留具体负面结论；Python28PASS、TS21PASS、原相关静态边界与非作者C0I0M0有据。Main未采用，未再实际研究；这些离线提示/协议测试不证明真实模型必然发现遗漏，也不补造07内容、校准或资格。

[实际SDK时段证据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual07/timing-recorded-metadata.json)显示6次compaction共380.517秒。step-start到message包含多阶段，未误作Provider独占时延；root first input usage141966、contextWindow262144。原seq179背书29facts/13evidence包含9份重复大attributes；[Python参考共享检验](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/btc4h-live-actual07/pooling-reference-feasibility.json)244381B→74341B且全字段往返相等，不是TS实现或实际性能证明。现正按[无损上下文计划](INCREMENTAL_LOSSLESS_CONTEXT_PLAN.md)做隔离候选，原授权/预算/证据及旧wire保持。全部最终门不变。


## 总控即时复核、同安装重启阅读及新源采用


2026-10-26北京时间即时复核（UTC18:36以后）：原总控T01—T08仍全部in_progress；v3正式STATUS展开27项，其中25项技术REVIEW、T-000/T-303进行中、任务ACCEPTED为0。总表实际56ID/9阶段，不把标称55项或局部测试换算成完成率。当前不是只剩自然时间等待；六组合稳定合格、两事件族、条件通知与后继、PAPER退出结算/恢复、评分消费、完整失败回放、最终质量/发布交接尚未验收。用户要求继续自主执行，目标保持ACTIVE，不从T000重启、不重新搭前端。

新增实际重启阅读：Root唯一report-restart01已同Handle terminal exit0/64.960秒。原07两库只读一致备份到新私有实例，原API单独启动、LLM及业务worker关闭；同owner/同Run唯一报告GET200，read_at之外整个响应全等，source/artifact hash不变，原业务文件和DB/WAL及65输入后验同。原件D:/DH-report-restart01/artifacts/restart-result.json。它证明保留原绝对archive根的同安装API重启阅读；不是可移植独立灾备、全部报告或PAPER恢复，也不补足07缺保护和观望换选条件，07仍不入合格分子。

内容/无损上下文两处微切片已完成D本地提交80e98ce及75d5a97，并按旧SHA/逐字备份/正式端口无监听核验，仅4生产源和4测试精确采用Main；receipt在Ddev/evidence/legacy-sdk-review/research-context-main-integration/adoption-receipt.json，未修改正式服务/profile/DB/installed指针。旧normal6dd不能认证新源，新完整normal尚未执行。Main必要11文件整合回归首次因旧测试beforeAll所需evidence/retained-context-race目录不存在而失败、7项跳过；保留cache及native-race-diagnostic.log。创建既定证据目录后，测试与生产源零改动，Root同Handle观察11文件96PASS/26.88秒/exit0，完整日志native-combined-after-evidence-setup.log。不能把测试目录前件错误当成产品竞态复发，亦不隐去失败。

Observer r2补齐既有context六项及Task三项身份联结，作者56PASS、非作者18定向负例PASS/C0I0M0；仍是离线六组合观察器候选，没有新的真实研究。ETH85日资料与显式六组合caller此前已验证；需要新normal、资料忠实恢复及真实内容验收后再进入六组合实际研究。状态取证及8采用文件逐SHA复核见research-context-main-integration/status-verification-20261026.json。以上不升级原八工作包/v3正式验收，不把单例32分40秒替代P50/P95，也不强迫空仓报告做PAPER成交。


## 新normal已终态；完整研究协议进入隔离实现


2026-10-26北京时间最新接续：Root唯一content-context正常构建已持同Handle终态exit0。exec50172/outer4920/FT134374271910761229、supervisor28912，UTC18:39:50.9514079Z→19:01:38.6740159Z/1307.7227517秒；原producer内层1304.736秒。UI/Host各一次且exit0，原assessment/retention/完整临时与最终来源校验通过；migrations/process-starts/stops/pointer-writes/model-submissions均0。原installed49c仍为Prior，未部署。新normal=Main/artifacts/incremental-delivery-20261026/content-context-build/product-validated-builds.json，SHA982db6a45689b123a5a8f6cfff61497983af18dbb991235dccfb4989e6603886；native指纹5d4473b86ee871ac57a3b79a4251fd47cb5da81641c8604bcffd6cfa818cc4ff。有限postflight exit0/errors[]，final-receipt SHA578391e1…f027在Ddev/evidence/legacy-sdk-review/content-context-normal-build。31采用文件、43控制、71 native输入、30原源码及588个当前preimage备份逐SHA复核；新4生产源明确存在新receipt并相等。raw source1801/effective60910/artifact48与实际dist+lib586文件是不同范围，不能相加。完整说明见INCREMENTAL_CONTENT_CONTEXT_BUILD.md；旧6dd/a067不再充新源依据，也不冒新normal为完整研究/金融/运行验收。

ETH忠实历史包已冻结在Ddev/temp/legacy-reuse/eth4h-historical-corpus，helper b3d8a32e…bd125/corpus201ef7c1…536d4。真实新私有目标保存12项PASS：原reader先coverage_missing，后85日/原receipt；原2行5file全列/原时间相同，重复0/0、篡改/PK/文件冲突/过早PIT/跨asset拒绝。原测试过程exit未保留，不补造或重跑；Root独立纯验证exit0及非作者C0I0M0有据，不开DB/不再恢复，source DB/WAL当前同保存值，原BTC封包不变。observer r2 Root独立56PASS7.40秒/exit0，5files+13readonly pins同，root-verification-r2.json已保存。它们仍不是新研究或PAPER。

质量接缝已实际定位：V5固定material-only且reader硬校degraded，旧quality模型又是另一套路径/执行资格，不能只加bool统计或继续付费六组合就宣布合格。D计划INCREMENTAL_RESEARCH_COMPLETION_PLAN.md已本地提交8d0de17；TaskA显式typed findings.v2四项独审内容检查（actual_prompt版本选择、防降级、保留旧v1），TaskB新source.v6/完整报告提交状态与原阅读接缝正在隔离实施；金融gate/permission/financial/execution不扩权。Main新normal仅认证content/pooling，尚未认证这些未采用的候选。新completed也仍须实际PC/重启全部六项才计合格，旧07保持不计。没有新付费Run/cohort，保持旧05/06/07终态不重投。

30例回放实源审计也已保存：Ddev/evidence/legacy-sdk-review/replay-delivery-audit/audit.json SHA dfede3fb…65056、audit.md SHA ffc31ec8…a175。385个直接原件齐全，29例119observations仅关联错误语义，另1例4次HTTP交互；未跑原Main内部业务/完整native/PIT，旧30/30不能代最终门。可复用原ReplayResearchRuntime/严格传输及05终态前双库恢复，但新完整回放driver尚未实现，最多先证明一个真实失败边界，不能重复计次数或伪造缺失输入。全56项/八个总控工作包及全部最终门继续保留，目标ACTIVE。


## 2026-10-26 最新状态：总控未完成，当前历史回读缺口待修复

2026-10-26接续真实状态：原总控T01—T08及最终交付仍未完成，不是仅剩自然时间等待。正式STATUS的25项技术REVIEW不等同原产品25项已交付，ACCEPTED仍0；56ID/9阶段的全部业务范围保留。TaskA显式findings.v2已D提交327b6eb、非作者C0/I0/M0，TS97PASS/Python38PASS且保留1项已证旧Main失败deselect；旧Python strict76→70是零新增，不是整体strict0。TaskB新V6原native合成child两例均原提交/原reader成功，尚在最后类型/peer收口。Root原PC候选3files77PASS/12.82s与原UI tsc exit0/0诊断，6文件含两份原服务fixture逐字SHA相同，原组件/导出未改；scope4c5af4d2…373c。以上A/B/PC均未采用Main、未部署，无新真实业务/付费Run；不计六条件合格。

已实测暴露历史归档缺口：当前Main+982原SavedReportQuery在新私有一致副本读取旧07失败research_graph_recipe_current_implementation_unavailable，34条BEGIN/SELECT/changes0、原restart01三文件SHA前后同。05/07所需manifest原字节同7b7d19e3…921e，现有51e00f40…a433是不同实现（420源中6份不同），不得替换。真正481份旧源已从480份匹配Main和1份确切profile备份鉴真保留；将经合法原producer补归档并原reader回读列为采用前必须修复项。此前旧版本同安装重启成功与当前版本失败分别保留，下载JSON完好不能替代当前在线兼容。原证据在Ddev/temp/research-completion-legacy-readback-01。

新的05私有备份回放Root同Handle实际UTC19:16:47.6841239Z→19:19:22.9090941Z、155.2251399秒/exit1。虽然原Worker落failed/lease清空、源六文件SHA同且无业务外连，实际先被上述缺历史实现拒绝，未观测原deadline异常，因此replay-validation.passed=false，不算完成30例中的一例，不改预期、旧Task或原05。该FAIL及新归档修复并不暂停整个目标：继续隔离补正确归档、完整研究协议与原PC接线，六组合稳定性、事件/提醒后继、PAPER退出结算恢复、四周评分、完整失败回放、最终strict/发布交接仍须实际完成。目标ACTIVE。


## 2026-10-26 四切片已采用Main并通过必要联合验证

2026-10-26接续：四切片已非作者C0/I0/M0并D提交TaskA327b6eb、TaskB1caa6b3、原PCc9bbe15、归档66bad52；Root按19旧SHA与逐字备份/14新文件absence/正式端口空闲核验，精确采用Main33files，无Main Git、正式启动、DB/profile/installed pointer写或新模型。adoption-receipt SHAa704566f…ac3a在Ddev/evidence/legacy-sdk-review/research-completion-main-integration。真实Main联合验证三组终态exit0：Python70PASS25.25s、PC77PASS11.95s、native15files97PASS53.57s；33源后验同，main-focused-verification SHA cb30ce59…dc3。旧React act警告、最初repo根无Vitest的0test前件失败均保留，未安装依赖；这不是实际新研究或最终strict0。

真正7b7d已由原producer生成481原字节归档，并通过原175注册完整后验与现成union得到176；首次driver在producer成功后因普通Windows长路径读失败exit1，纯continuation复核现有产物exit0，未重复producer。原物理7b7d视图Query新一致副本已exit0/1113条仅BEGIN/SELECT/changes0，17原字段及source/artifact UTF8字节全等、原源文件SHA同，只有read_at新时间排除。仍须真正新normal后验证当前Main/archive分支，不能用原视图成功冒充当前兼容。542份真实6dd输入及48原artifact齐备，standard normal准备以6dd completed prior+显式opt-in原auth链生成新收据，installed49c不变；旧982不认证新33源，当前尚未开始该新build。

下一先完整normal及当前历史回读，再释放新真实研究。并行Phase7仅D原链增量：已定位原forecast/runtime/reader与PC硬4h、241分钟/6页限制及旧方法源码hash，保旧四software输入字节不变，新增显式v2跨期限truth/评分/read与原PC接线正在TDD；不得把纯32PASS、合成SQL或未来原件当四周实测。研究六条件/14自然日/事件/提醒后继/PAPER完整退出恢复/四周评分/30完整失败回放/最终strict和发布交接全保留，STATUS无新ACCEPTED，目标ACTIVE。详见INCREMENTAL_RESEARCH_COMPLETION_MAIN_INTEGRATION.md。


## 当前唯一正常构建已启动，尚未终态

覆盖点58的“新build尚未开始”：Root唯一完整normal已于UTC2026-10-25T20:37:22.7785193Z启动，exec47376，outer22160/FT134374342429091824、supervisor11764；inner原builder21100/FT134374342464127151。prepared8e7d232b…7d022，71controls/71native/61current adoption/542prior originals/588fresh当前产物备份已Root独立全SHA核，原10GiB内存与child8GiB门保留。UTC20:42:33.7318583Z再次轮询同handle仍RUNNING，两PID创建时间身份实核相同；没有终态或成功结论，禁止因无日志/观察超时重新启动。实际scope/started/root-live-observation在Ddev/evidence/legacy-sdk-review/research-completion-normal-build。后续必须继续47376，真实终态后才运行有限postflight.py并传实际47376；当前Main/archive旧07回读包另在准备，未执行。D新Phase7不采用Main；其首轮默认4GiB完整tsc已自然OOM/exit134，已保原日志，最终8GiB复核待本normal终态，不能冒通过。目标ACTIVE。


## 2026-10-26 新normal终态及当前历史归档回读通过

覆盖点59运行中状态：Root唯一normal47376已同Handle真实exit0，outer22160/FT134374342429091824，UTC20:37:22.7785193Z→20:59:32.1308297Z/1329.352451s；UI/Host各一次exit0，原assessment/retention与完整临时/最终guard通过，迁移/启停/pointer/model全0。真实新normal4511e6c6…4de4f，native d4b1c07c…e699；有限postflight exit0/errors[]，final-receipt57519b0c…a4fbd在Ddev/evidence/legacy-sdk-review/research-completion-normal-build。raw1807/effective61398/artifact48/dist-lib607是不同范围。installed仍49c，已完成6dd是显式合法prior；原standard producer已捕获真正7b7d并保175→176注册，未部署。

当前Main/archive兼容已实际闭合：最初79199 guard0、原Query已返回，但私有观察器将合法Windows长路径前缀误判，exit1原件及Target SHM变化保留。Root审新纯路径TDD/完整冻结后，仅新增observer85993实际exit0；不重build/guard/copy。当前Main manifest51718c50…5ae0不同7b，Query前无旧reader/后真实加载7b，255物理file逐SHA=payload=index，17原字段及两个JSON UTF8字节全等，1142条仅BEGIN/SELECT、session changes[0,0]，Source三文件/Target DB-WAL/normal前后同，financial/execution仍false。证据D:/DH-report-current-archive01/evidence/query-result-r2.json SHA6c86fcb7…09211、observer-r2-terminal7e549c79…8570。旧07不升合格，不推广全部历史/独立灾备/PAPER恢复。ETH08包可绑定4511/d4及此proof，但须Root最终scope审读后唯一释放；当前还无新付费Run。

后继维护链source-only已确认：当前H1 prior=6dd、installed49c，下一次不能再拿6dd重捕7b而漏真实51718。B仅D独立候选做bounded completed-chain完整逐跳鉴真，Main/66bad/新normal/Query证据均不改。Phase7仅D候选继续原链评分/PC，原native测试前三次分别为新harness类热装载、异步loop、strict JSON模式错误；第三次实际V6 native+Commit已完成，后续按原件新副本continuation继续，不冒原整测exit0或真实样本。所有业务/质量/自然时间硬门与全56任务范围保留，目标ACTIVE。详见INCREMENTAL_RESEARCH_COMPLETION_BUILD.md。


## 2026-10-26 ETH08 单次真实报告读取：409，未导出

当前Main4511/51718、真实Run7093f1de…6b6b终态degraded/native failed。Root同owner和原command/receipt唯一GET在UTC23:10:17返回409/perpetual_report_original_unavailable；实际source.v3不满足现成V5/V6永续报告reader。响应原件在D:/DH-eth4h-live08/artifacts/report-readback/terminal-r01；没有report.raw.json，未执行render、实际浏览器下载或重启报告读验。不能用前述旧07成功代替本次读取，也不能用本次失败改写旧07已证实的阅读兼容。

900hold自然结束、outer93659 exit0/5jobs闭/errors[]及151pins同另有88584fe1…23388证据，但业务仍未通过。离线原校验RED、原件与后续界限见[ETH08真实结果](INCREMENTAL_ETH4H_V6_LIVE_RESULT.md)。


## 2026-10-26 主线51文件已实际采用，完整构建运行中

此前Phase7/bounded/deadline/order“仅D未采用”的时点由本节更新：Root已精确采用51并完成必要Main联合验证，完整synthetic V6提交、发布、到期和后续读取1PASS；原PC108PASS保持原页面/原断言。原native首次164P9F及独立baseline9PASS分别保留，不能冒173单轮全绿；其他三个装配/等待失败的原件亦保留。当前仍无新的真实合格研究，ETH7093不升级。

真正H1 51718捕获66040实际0，原176+新1/union177已在隔离产物验证，未称Main完整177读取。新normal session73226已实际01:11:22Z启动，prior4511/H1、原8GiB/10GiB资源与完整source/artifact检查保留；preparedada10466…2b86。只能接续原handle，不因无输出重启；现无构建终态/新normal/部署结论，installed49c未改。控制书不属于本次源/120controls，因此只同步本说明、不动业务源/输出。

[本轮采用、真实摘要、失败与运行身份](INCREMENTAL_PHASE7_RECOVERY_MAIN_INTEGRATION.md)列出全部证据。PCE纯解析D提交及后续Actual候选不属于本次Main51，未取得真实事件闭环。全部56任务、原八工作包、14自然日90%、两族、提醒后继、PAPER退出恢复、四周评分、30完整失败回放、最终strict/Ruff和发布交接仍未完成；目标ACTIVE。
