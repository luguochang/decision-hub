# 原系统主线集成：评分、恢复与上下文读取顺序

2026-10-26，北京时间。原界面和入口保留。12源后继修复已采用Main，完整normal与旧07当前版本回读均实际通过，未部署。随后fresh10真实研究正在执行；概率与保护预案尚未完成本次真实报告验收。原09 needs_revision与失败保持，最新状态见末节。

## 实际采用

采用范围为Phase7后端23文件、原PC评分展示13文件、完成维护链5文件、过期恢复5文件、native读取顺序5文件。前像10份逐字保存，41个新目标采用前不存在；全部51份实际Main结果与已审候选一致。保留607个dist/lib产物及2个辅助产物的前像，两个原native测试固定输出另有前像。Main没有Git操作，正式服务、库、profile及installed指针未切换。

- 准备：D:/DecisionHub-v3-development/evidence/legacy-sdk-review/phase7-main-integration-prepared01/prepared.json，SHA `e0d91ed66a32b31c81da60c0eaf6adef33525b7a149fb07e343299aee2322d9f`。
- 实际采用：同目录adoption-result.json，51项均记录原/新SHA；并非用D提交代替Main采用。
- Main截止恢复源为`99996243…b565`，保留Main基线，不以D带额外库存优化的整个文件覆盖。
- native修复仅将已有detail读取放在已鉴真的子前缀/Provider proof读取之后；没有新增缓存、真相库、重试、超时或放宽校验。D提交`ca1993a89698417690e102976ac6eceee79d5efe`，Main bridge为`8fa8a6b1…c2cda`。

## 当前版本的历史保留

Root唯一capture session66040已实际exit0，Root观察终态为UTC2026-10-26T00:37:12.6376554Z。原物理producer在D:/DH-H1-retain01生成真正H1 `51718c50…5ae0`，原176注册在原校验器前后完整验证，新1条通过原校验，合并177条逐项保留旧176。Root另外核484个新归档资源及union内容。仅为隔离捕获，尚未安装、未执行当前Main对完整union177的读取。

捕获结果：D:/DecisionHub-v3-development/temp/legacy-reuse/h1-retention-capture-prepared/capture-r2-result.json，SHA `3e0df4f64f9427022c6172e241938160bfb1fda21e1ec96a1d275dfab14910a8`。未来normal使用真实H1 receipt `4511e6c6…4de4f`与CompletedMaintenancePrior；未重拿6dd漏掉当前实现。10旧source+48旧artifact全部匹配H1映射，按原入口要求复制到Main/artifacts/incremental-delivery-20261026/phase7-h1-current-source-originals；不冒499源的producer闭包为完整输入根。

## Main联合验证

各组独立进程，采用文件前后SHA一致，Python产品模块来源均为Main。以下是各实际命令的完整摘要，不换算成业务合格率：

| 实际组 | 实际摘要 |
| --- | --- |
| 原assessment/delivery/单跳兼容 | 69 passed, 1 deselected in 23.43s |
| Phase7七文件 | 61 passed in 382.46s |
| 截止/已完成/已准入恢复 | 29 passed in 1.12s |
| 有界维护链 | 39 passed in 1.99s |
| 原mapper/完整proof | 25 passed, 1 deselected in 10.89s |
| 原PC七文件，串行文件 | 108 passed；Vitest duration29.22s |
| 原native20文件首次 | 164 passed, 9 failed；74.51s，exit1保留 |
| 上述baseline九项的独立装配复核 | 9 passed in9.61s，exit0；不能称首次173项单轮全绿 |
| 新V6→Commit→publisher→due→后续读取 | 1 passed in282.54s，exit0 |

两个deselected分别是重复的旧6dd真实全registry正例（当前实际H1链捕获和后继normal提供新证据），以及此前已证明的旧prompt字面量失败；原断言未改。最终原UI完整类型检查复用接下来同源码normal，不再无变化重复执行。

失败原件均保留在D:/DH51v：deadline01为15PASS/14个缺共享fixture错误，私有入口显式注册原fixture后deadline02为29PASS；pc01为107PASS/1FAIL，原报告case只在第二次异步读取后固定等30ms，失败时未保存DOM，不能断定当时状态。相同源/原30ms/原断言仅串行文件后的pc02为108PASS，不宣称修改产品治好了该等待风险。native01九项是私有guard误拦截原测试临时loopback服务，修正只允许本进程登记的临时端口后单独9PASS，其余164未重复运行。

flow01实际1FAIL/72.16s，内层原Vitest CLI未带私有host配置，被localhost DNS拒绝；flow02仅给精确原argv/cwd追加import原Main配置的D wrapper，业务文件、发现范围、断言与预算不变。flow02原生子过程exit0，整个Python测试exit0。Python guard保留一次被拒绝的urllib3导入期IPv6可用性bind探测，静态栈已定位；并非业务外连，也未放宽权限。新测试使用私有D SQLite，所谓零DB动作仅指正式/Main数据库，不能解释为测试没有SQL。

联合证据：D:/DecisionHub-v3-development/evidence/legacy-sdk-review/phase7-main-integration-prepared01/main-joint-verification.json，SHA `b03e4dc6c9d0399fafdf3f12e004b1b211b6b7d788b3afc4fb2d175cd7bb6f66`，包含8个成功终态和4个保留失败终态的完整日志引用。

## 原完整构建正在执行

Root唯一session73226，outer27572/FILETIME134374506792401557、supervisor12232；原builder17800/FILETIME134374506821932380。UTC2026-10-26T01:11:22.191368Z真实启动。prepared SHA `ada1046603edb324f6a534f9796ce490c46b583418e4a2af45bce845fbe22b86`；120controls、71native输入、51采用、58旧原件、609产物前像已验证。启动前free15297990656B≥10GiB，原child8GiB及全部检查保留。

输出为Main/artifacts/incremental-delivery-20261026/phase7-recovery-build；原Prior明确4511/H1，不是6dd。控制和日志在D:/DecisionHub-v3-development/evidence/legacy-sdk-review/phase7-recovery-normal-build。必须接续同一73226，不因无日志或观察超时重新启动；终态后才运行一次有限postflight并传实际handle。当前未有新normal成功结论，installed仍旧49c。

## 保留范围与后续

PCE纯官方原件解析另已D提交`d00d3cd6174d8d05e7f2da096415c8f353858948`，41PASS/两个新文件strict0/Ruff0/非作者C0I0M0；没有采用Main，不属于本51或本次构建。PCE Actual原入口接线正在D独立验证，不能算真实SourcePoll、首次发布/PIT认证或两类事件交付。

旧ETH7093仍失败，原marker348仍按完整校验拒绝；新生产顺序的受控录制数据回归通过不改写旧失败，也不计30例完整回放或新真实研究成功。新normal通过后再核实际来源与适当历史读取、完整真实研究。全56任务/9阶段及T01—T08尚未整体验收；六组合14自然日90%、P50/P95、两真实事件族、条件送达及合格后继、10笔PAPER退出结算恢复、四周真实评分、30完整历史失败回放、最终strict/Ruff与发布交接均保留。新源局部strict0不能冒Main旧债已清零。目标ACTIVE。


## 2026-10-26 北京时间接续：构建终态与ETH09实际失败

- 原normal73226实际exit0，有限postflight通过；真实receipt `7b2bc2a530042dfd0a12861702fba45491f2deadb728aeaf3f4c47825a0d691a`、native `6fa2750ca0974ae5e7e22df308016b7efe3fbf2272eb0d6fee8a7eca80fcadb3`、当前graph `3a33ae5585b741e65d8885e1e07b72cbdb659b67fdb6f49b5a67e08577a64cf5`。当前Main旧07回读23511也actual0，17字段及source/artifact字节同。未部署或改installed pointer。
- ETH09唯一新实例实际98569/7964，同Handle UTC02:26:09.0040025Z至03:24:38.1605296Z、3509.156752秒、outer0。原90日ETH数据Job真实completed/90连续日/4分区/1capture；一次POST，未知回执后仅同command4次GET确认。Run `run_9e9694224b41495784f43e8bc99677b9`，accepted02:29:04.730946Z，原Task截止03:29:04.730946Z未改。
- Worker0/research_only不代表业务成功。唯一报告GET于03:13:52实际409/perpetual_report_original_unavailable；没有report.raw.json、render或下载。本轮9组exact-owned均已关闭，cleanup.errors=[]；原26文件一致副本在D:/DH-eth4h-replay09-source01，Source SQLite未打开。
- 本轮首个后段断点不同于旧08：step8 prepare成功；独审原答为needs_revision/1issue/cost_protection_and_conditions incomplete，指出多空缺有依据的d_SL/d_TP与按tick舍入的保护价格。概率完整性check引用的probability_source声明与本packet方向分布source_ref、真实14声明中的派生ref完全一致，却被catalog-only检查拒绝。随后review重试、finalize均未成功；不得把旧失败或needs_revision改成approved。真实case：D:/DecisionHub-v3-development/temp/legacy-reuse/eth4h-live09-review-binding/reviewer-case.original.json，SHA `3fceb90229e66c4be6a84583bdb735d517feaf617870ded82cc2f70a2bf02d40`。
- 已自主裁定修复方向：V1保持原语义；V2按检查目的区分真实资料与提案内容。冻结信息/证据平衡仍只认原资料catalog；概率完整性可引用同frozen prepare中按真实body、role和packet用途精确绑定的概率声明；保护条件可引用同prepare中精确候选的条件/退出/依据声明。这些只是被审内容，不获得市场事实、校准、成交或执行权限。Host、Python鉴真、服务端delivery与PC必须同步，真实cost incomplete和needs_revision保持。实际旧对象TS/Python原guard均已RED；候选尚未Main采用。
- 原页面候选条件/退出正文的5源候选已24Python/62PC、新增4文件strict/Ruff零错、UI8GiB类型检查零错及非作者C0/I0/M0；仍是隔离候选，不冒真实09报告可用。与引用域修复合并收口，不再扩大PCE/PAPER等前置。
- 下一次正常构建可由原producer直接使用真实7b2 prior、Main/artifacts内真实变更源前像与CompletedMaintenancePrior，在同一次normal中保存当前3a33并保全部177旧注册；不要求另跑一次H1捕获。原件已按实际SHA保全至probability-review-repair-originals及D:/DH-probability-review-preimages01；尚未执行新producer或采用业务源。

原总控T01—T08、全56任务/9阶段及全部长期/质量/部署交接门仍未完成。当前不重投09或新付费研究；先用本次真实旧对象修复反馈链并验证负面审核后可继续修改。

## 后继12源修复：实际采用、构建与fresh10实际失败

两候选finalscope(a5a67f…a778、284308…c25a)已Main精确采用12源（10旧/2新），实际回执Ddev/evidence/legacy-sdk-review/probability-review-main-integration/adoption-result.json；D提交f48e412另含通用PC19测试。物理Main的真实09两端parser保持needs_revision/1issue/cost incomplete、原续修单例通过。未改写历史为成功。

原standard normal51019实际exit0：UTC05:41:10.7282998至06:30:20.8250765、2950.0969259秒；UI/Host各一轮，finite postflight0/errors[]。receipt b98640c1ecf7b187f4959de26ae46c844a85a1dc68c3f7ec1b453efe5c462537，native62dfb09ed9c557685d2a41d76ccc5062d743474019e55a373c949e06955aefe0，currentgraph936fcc7913d1263368e1449975bacb802813f121114ed7cb71d2b1bd7e78d03f；final-receipt SHA9a8ec5dd7bab15efd079b4b86e5f9e5d93d371ec85da13850ed60e348c7df3bd。原producer同次真实保留3a33、177至178，旧注册全保；未另H1捕获、未部署或切installed49c。

当前Main旧07复验58870 actual0：17字段及source/artifact UTF8字节同，1274只读语句、Source三文件同，financial/execution仍false。query SHA0997ae206e00e2c95d27dae028538599360200ec54c1ca9ff3a8d20476ce7015，terminal5c236b9f66f0ed49b0789d613a608c7a968fbf6161039de8fe0652df52e73c59；证据D:/DH-report-probability-review01/evidence。

fresh10已自然终态：76404/outer13524/FT134374707859726649，同Handle UTC06:46:25.9455613至07:56:11.8615139Z，4185.9160949秒、outer0。真实90日数据由2原行6文件恢复并严格回读，不搬旧Run/账户或回填known_at、不重复GET；Run run_32303478bf3340399bbdc66fb4a62030于06:49:20.588705Z受理，原Task截止07:49:20.588Z，一POST＋同command三GET确认。真实两轮独审均needs_revision；负评后changed prepare和第二审通路已走通，但第二审cost failed/2issues：匹配SWAP成交P_ref、明确有依据的距离/数值舍入保护及风险检查仍缺；提案自写spot600s停用条件已触发。末端07:38:02另出现native_context_invalid，尚未认定原因，不沿用旧08结论。唯一报告GET于07:43:24.838Z返回409/perpetual_report_original_unavailable，未render或导出。Root实际终态凭据12689762a50d28d1d0ee8bc007c3335476b142549ebc368c3c37cc0396347f30；10owned Jobs全部关闭且不运行，39原文件闭源一致副本D:/DH-eth4h-replay10-source01，proof ed8b1c265efe28fe99ac48e1c572884dfb7da46b1caa2174772997ba7375349a。SourceSQLite未打开；私有copy首guard误把已退出内核对象视为运行，firstFAIL保留，r02只修该判断。

当前只在D补原研究工具缺失的显式SWAP trade price与已鉴真close数据的波动依据，并在一致副本复现新context首错；旧SPOT ticker语义、mark/trade区分、原5秒执行检查、600秒已有声明、负面review与所有金融权限均保留。未新建前端、未再POST或启动11；离线修复及必要合并验证通过后才安排后继实跑。尚不能称事前操作预测概率可交付，所有总控与长期验收门仍未完成。


## 事前概率输入与会话换代：8源Main采用及唯一正常构建
2026-10-26 事前概率输入与原会话换代修复已实际Main采用8生产源（5旧/3新），仅原后端与Host，无新前端。依据复合scope7b0c1352…7458和两个候选finalscope c15bf77a…c164/ab1fb746…79f6，原子采用回执02d2508e…49df；原Main5前像逐SHA保存于Main/artifacts/incremental-delivery-20261026/probability-inputs-repair-originals，匹配真实b986 prior。B5源新增显式SWAP trade字段、保旧SPOT八字段默认与mark独立，真实原collector/strict decoder→RawCapture/parsed原链复用；稳定Evidence去重与完整独立Fact trace并存，原生raw ref不冒catalog引用。close波动/区间仅原catalog核对后的display_only派生view，旧binding/catalog正文逐字保留，不挑默认d_SL/d_TP/不造ATR或first-touch概率。C3源以实际10原件在旧313长度门复现gen2两protected；仅官方prune/replace将已鉴真前代control退役成无权限历史说明，原events保留，新完整snapshot仍由SDK在真实step/prompt后append并重读真实prefix。原verifier逐字不改，合法0marker压缩/恢复兼容，截止/预算不续期。

B25离线及新7文件strict/Ruff0、旧三源Ruff24→24新增0；C原相关116PASS、新23PASS、8GiB focused types实际0。两方独立peer C0/I0/M0，c7314295…9510/0029366a…5497；Root独立源码review6978236c…96f1。采用后物理Main真实5Python import/25PASS（D:/DH-pi-main01/summary.json f9c28a3e…83ab）、3TS import/23PASS（D:/DH-probability-inputs-main01/native-main-terminal.json），8源前后同。不是新真实ETH研究、durableSQL验收或全Main最终strict/Ruff清零；旧10两负评/报告409保留。

Root唯一原standard normal82814已真实终态：outer16636/FT134374791253882512，同Handle UTC09:05:25.3440193Z→10:01:09.6261023Z/3344.2822694秒、exit0；finite postflight actual0/errors[]，final29dc973e…a3d3。UI/Host各一轮且exit0，migrations/process starts/stops/pointers/model submissions全部0。实际normal6059af63013f78af55294847bcef09125946c0cacccb94ac1e2609cf0ed9978a/250162B、native85ac99ac3b1d86a6a43650c1f69ad018b699c35bf9b8effd9dcf8c9ae7ce9d19，原manifest builder实际current855650c1…9ac3。原producer同次保留旧current936fcc79…d03f，真实retention passed/178→179，原验证全部通过；未另H1捕获或独立fullguard。Source1829、实际guard有效62871、artifact48、full lib/dist649是不同范围，不可混称。证据Ddev/evidence/legacy-sdk-review/probability-inputs-normal-build；82814已闭，禁止再poll或重启。installed49c/pointer b057仍未切换，无正式服务/data/profile部署。

fresh11仅D pending包/NO_ACTION：Ddev/temp/legacy-reuse/eth4h-live11-v6-prepared，WORK11未创建，无HTTP/SQL/model/Run；身份必须等本次actual normal终态后绑定，不能用旧b986冒新身份，不预设native仍17产物。所有原14天/六组合/两个真实事件族/通知与后继/10笔PAPER结算恢复/四周评分/30完整回放/最终strict与部署交接门、全56任务9阶段及原T01—T08均未全部验收，不新增ACCEPTED。


## SDK准备修正与实际12受理
实际11仅启动前件失败，未形成新研究：outer15748/FT134374839628634799同Handle UTC10:26:02.8194285→10:26:06.3710262Z/exit1，唯一clone8396已exact Job closed/errors[]，initialize/API/POST/Run/model未到。原Node import.meta.resolve严格校验指出schemastery实际ESM index.mjs，而准备元数据误用了require.resolve的index.cjs。Root同原解析器只读实际11克隆目录确认，C新12四importers×七SDK RED4失配→GREEN28全同，原clone校验与产品/normal6059均未改，不重build或旧11。

Root唯一actual12已10:38:18.9540625Z启动：exec18164/outer4780/FT134374846989970257、supervisor8672；WORK D:/DH-eth4h-live12，ports39121/39122/39123/39124，scope9e8978c5…1da5e，733inputs。Clone0、initialize70.594s/原head103、owner/真实90日两行六文件恢复和严格回读已过（producer_invoked=false），API/acq/MCP/Host ready。一次原POST首次logical10:40:33.464130Z，未知ACK后同command八GET确认，实际accepted10:41:48.074051Z；Run5863e36c…7844/request4103b136…184d，原Task截止11:41:48.074051Z未延长。command00b6687b…df12/608B，receipt7ee9ee55…2293。实际12已在原截止时间结束：同Handle exec18164/outer4780于11:41:48.7907147Z退出1，elapsed3809.8368841秒，原perpetual_observer_original_deadline_elapsed；worker-exit未写，10个Owned Jobs均关闭且cleanup errors[]，禁止重启旧12。第三原独审authorization=approved，概率/证据/成本保护三个assessment=passed、冻结信息=incomplete；原third-review-final-submit.original.json 9f764066…3d6f不改判。native Link已completed/gen1，authenticated terminal实际11:40:02.739284落库，20MB transport result/4.8MB mapped/graph_round:1依次已存；关闭副本直接RO证Run仍running、Artifact本Run零行，没有canonical报告或graph_final，不能称可交付。关闭字节副本D:/DH-eth4h-replay12-source01 provenance161ffd58…fe85，原SourceSQLite未开，副本初查1a458eaa…16b0；未做报告GET/render/JSON HTML导出。当前收敛修复为历史日线原件的只读材料接缝及Graph最终化重复验证，先D-only复现/验证，不盲启新模型、不回填旧Run或续预算。Root-delivery-status.json 0cf38296…d105保存本次真实状态。installed49c和正式服务/data/profile/pointers未切换，所有长期与全56任务/9阶段/T01—T08门仍未全部验收，不新增ACCEPTED。


67. 本段仅记本次执行状态，不新增审批门：Root已将7个经过独立复核的修复源精确采用Main（5旧/2新），adoption e3e1d5e7…3275、composite0e0295d0…19a7，旧5源码保存在Main/artifacts/incremental-delivery-20261026/probability-final-mile-originals。修复为普通V2完整历史原件只读投影、单次Graph最终化锁外鉴真/短writer，以及信息快照与真实writer时间、仓位time exit与proposal expiry的提示澄清；原UI源未改、未部署。物理Main同scope实际67 Python/2 Host/2原event byte guard全部过，summary D:/DH-final-mile-main01/summary.json 93117da4…f5f8；初次保护补测因cwd取错导入D不完整依赖的4个collection errors保留，改明确Main/importlib后同25例通过，未改业务修错。原normal唯一exec22904/supervisor22144/outer13040/FT134374928702892300，UTC12:54:30.2465288Z启动，builder24196/FT134374928732997524，prepared4d9cc036…6629，133 controls/72 native/5 originals/651 output preimages；以已完成6059为显式prior、同原producer，已于14:04:07.0340598Z同Handle退出0，outer4176.7876549秒。有限原postflight actual0/errors[]，final d608eb8f…0dfd；CURRENT normal6909bc70…932e/native7d921119…1db3，raw1831/effective63359/artifacts48/full lib-dist649，UI及Host各1次0，migrations/process starts-stops/pointers/model均0，未部署。不猜新闭包count；已生成输出供一次新实际报告验收。事件full raw-history view尚未交付；旧类型诊断基线71→33、新helper0/新增诊断0，不是全Main strict0。旧12无canonical报告的失败及全部自然时间/总控验收门不改，尚不能称事前概率前端已可用。详见docs/v3/reports/INCREMENTAL_PROBABILITY_DELIVERY_FINAL_MILE.md。
