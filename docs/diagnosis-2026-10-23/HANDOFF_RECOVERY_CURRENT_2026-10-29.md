# 当前研究恢复交接（2026-10-29）

## 本轮最终续接：主流程恢复与需求核对已完成

更新时间：2026-10-29T05:32:08.279422+00:00。本轮目标的主流程、自测、文档与需求核对完成；全项目/55任务、正式发布及长期资格没有完成。完整可复核结果见[结果报告](E:/project/study/codex/dsh/decision-hub/docs/diagnosis-2026-10-23/RESEARCH_RECOVERY_RESULT_2026-10-29.md)。下面历史续接中的running、待执行、API409状态均为旧时点，不再作为现场操作依据。

## 现场状态与操作

- Main工作目录 `E:/project/study/codex/dsh/decision-hub`，沿原系统修复；原前端 `http://localhost:5173`，API18047，DSH18097。
- start10运行，05:21:03.866178UTC首次观察全7服务online/API+DSH ready；05:17:45.6778799启动到该观察198.188秒，为采样上界。正式/金融/执行false。
- observer session25112、启动器PID5316只作本次身份索引；下次现场status检查，不拿旧PID判断活跃。不重复启动、不恢复旧Run/observer。
- 使用已存在PowerShell7 `C:/Users/90603/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe`；运行 `infra/dsh/status-dev.ps1` / `run-dev.ps1 -DevelopmentRoot data/development/default` / `stop-dev.ps1`。停止前确认研究空闲。
- 日常前端Vite，Python/插件在研究空闲时局部验证、源码重启，官方缓存决定局部插件编译。开发验证不调用完整build-product；start09局部6.35秒，本start10两compiler log/stamp字节未变，未观察到新局部编译。
- 真实研究期间冻结源码/SDK/AGENTS/模型设置。当前模型rawSHA仍c1a32f…66905；不输出设置/凭据/认证URL，不commit/push或删历史数据。

## 两份真实原报告现在可读

| 原研究 | 终态/原artifact | 当前认证读回 |
|---|---|---|
| BTC09fc0ef0… | degraded / art_aac19b93f010489db1377121a2c90134 | live/btc-r5k-final-authenticated-report.json；13checks全true |
| ETHdff19351… | completed / art_12bfd8ce4a2a4ad1ac30026cfe84158a | live/eth-r5k-final-authenticated-report.json；13checks全true |

证据根 `artifacts/incremental-delivery-20261023/d28-research-recovery/`。publication/source/artifact/概率与R5i原证据精确相等。概率仍冻结90d prior，未证明预测增益，不授金融/执行权限；原BTC旧degraded与ETH6soft gaps保留。业务库存仍9failed+1cancelled+1degraded+1completed，R5j/R5k收尾未新发付费研究。

## 当前代码和验证

- R5i完成Host→Python最终来源认证，两真实研究各约41分钟；所有旧失败/负评保留。
- R5j root/child accepted数据最终交付修复：42TS+127Python通过，actual-source派生链BTC37facts/18evidence、ETH40/18、hardgaps空。只是新离线v5fixture，旧真实v4不改；livewriter/lease/插入幂等/同v5延迟取消未验。
- R5k当前执行manifest门保留；DEV显式scope用精确旧消费者认证历史报告。只六生产文件，SourceDelta/架构说明与前像均在coverage-semantics/r5k-historical-reader/。未改g/perp/recipe/worker/PS/前端/概率阈值/依赖/正式registry。
- 新49cases=48PASS1symlink平台SKIP，inner0/140.004秒；Ruff5files0，新helpers strict实际2files0/0/0。96选定source/test/SDK+模型与冻结同；六份具名正式保护文件SHA同，非全目录盘点。final-integrity.json全PASS。
- ACTUAL2真实prod factory/currentQuery两Run读回成功，各7/总14对照通过；6非空事务BEGIN/query_only/同DB/changes0，额外selection快照同；原builder从native物理旧源码自然ac519，两current obsolete selector仍拒。证据复制至actual2-evidence/，实际命令原件D:/DH-r5k-actual2/。
- 最终独立审查review-final-live.md C0/I0；正常API各13checks通过才关闭原409，没有用prototype替代上线读回。

## 快照、空间与后续限制

- 固定DEV store `data/development/default/runtime/history-snapshots/`，current512files identity4b9b9f5b95996c632d9d65444a74832e3dcd554770b46f10ff3eb5f7f7b19b68；旧两报告509files/9735354B identityac519bc186586fa81652f8b5b2dc54cf1108626535bc5378492f1cc76bcae183。
- 启动只capture/核当前身份一次，读请求只按保存manifest核对应小闭包，不扫全仓/189归档，不把current冒旧。Python改后必须官方源码重启再发研究，不支持不重启热改。旧报告深度验证仍约一分钟，读取性能未优化。
- Windows native IO修Win206但逻辑路径/manifest/hash不改；只有repository regular-source leaf可按SHA读取硬链接，所有snapshot/index/祖先/DB及symlink/reparse仍strict。源文件链接未拆改。
- E曾0空间，现仍约450MB。临时测试/长日志放D短新目录，禁止复用会清理的旧basetemp。仅本轮失败writer副本原样迁D:/DH-r5k-space-20261029T043927Z/preserved-fixtures，先backup后Move三SHA同；原E/private/btc-derived-source.sqlite3.location.json指向新址。另两本轮readerfixture透明压缩bytes/SHA同。未迁真库、未删历史内容。

## 未完成需求与下一步

P3确定性base_rate_only/>15pp审核、P4完整价格带/锚价、P5基准log-loss/明确无对照及真实评分、P6基准/模型并列和理由面板未齐。专用事件预测主要CPI九格；PCE/就业/FOMC等有部分接缝，各族完整事前→独审→发布→结果闭环未验。其他期限/六组合、14自然日≥90%、四周评分、PAPER≥10笔费用/资金费、正式发布及retention等未完成。

A4原1030跨版本944PASS/81FAIL/3ERROR/2SKIP，0未观察；核对完成不等失败修复，不再重复原250长测，也不冒当前全仓strict/测试绿。81测试FAIL与旧两源81类型诊断是两回事。

后续优先P3/P6→P4/P5→逐族事件→其他期限/PAPER/长期→正式发布；先写精确任务范围/本地复现再改，常规裁决自主记录，不从T-000或旧D盘克隆重建。删除数据/git等不可逆操作需用户确认。本轮未授新的金融或正式发布资格。

## 自身问题与记录入口

完整列表/首次错误、退出码、last40、前像和修订命令见CODEX_LOG与PROBABILITY_BASE_RATE_PLAN最新追加。重点：日常误用完整存证；漏跨Python消费者与公共Query；Windows长路径/真实hardlink metadata未覆盖；fixture假设258>260；AST脚本自遮蔽；格式检查过度全局串行。都保留原失败，没有归咎provider/降低阈值/改旧Run。

## 以下原样保留历史续接

以下所有“当前”“下一步”只代表其写入时刻，以本页以上与具名结果报告为准。

---

# 研究恢复交接摘要（2026-10-29，北京时间）

> 最新续接：R5k candidate4已冻结96文件（只fixture root加长，生产同candidate3）；Ruff五新文件0、新helper strict实际2file0/0/0。原新快照生成及复用已PASS；48完整新用例在D:/DH-r5k-u4执行中，尚未结论。此前E满已恢复约559MB：仅本轮失败writer隔离copy迁D:/DH-r5k-space-20261029T043927Z/preserved-fixtures，先独立backup再Move、三SHA全同，E原路径旁.location.json可定位；两本轮readerfixture透明压缩逻辑bytes/SHA同。真DEV/formal数据库未迁/改。native extended IO修复Win206、不改逻辑manifest；oldbuilderac519与两实际完整Query、start10/normalowner接口仍待。start09已stopped/absent，勿重复start09或paid/完整打包。下方Stage7/旧running/早期授权均历史，当前以本段及CODEX_LOG最新条目为准；目标active。

> 当前实现阶段（2026-10-29T04:23:54.509317+00:00）：R5k两真实完整Query的private-ORM prototype与原source/artifact/概率/状态对照通过，R2独审认可限定组合。start09已官方空闲停止04:19:52UTC，stopped/absent/listeners0，外层observer退出-1另列。task-brief及before-production先保存4前像、87/model旧同与7protected；R1仅六源实现、A仅两newtest获写入，先停写freeze后检查。当前正常owner接口409仍未由生产候选闭合；不恢复旧Run、不新发付费、不完整打包。最新GO只覆盖本段/具名brief，下面较早状态作历史。

> 最新状态（2026-10-29T04:17:46.243672+00:00）：start09局部编译6.35秒且7服务ready；R5j离线派生链已通过，但正常owner旧报告两个GET均409/perpetual_report_original_unavailable，已证首因保存v2 recipe与当前Python闭包不匹配。先前低层Source兼容控制漏覆盖公共publication graph，是执行者验证遗漏。R5k精确509原码可用；current PublicQuery+私有原publication consumer的隔离prototype由R1报告两Run通过，最终文件/独审待收尾，生产接线尚未实施。API兼容仍未修好，目标active，无新付费请求/完整打包。以本段及CODEX_LOG最新条目为准；下方较早Stage7、running及停止状态均为历史。

> 最新续接点（2026-10-29 01:56 UTC）：BTC-r5i1 run_09fc0ef0a2fc4444a812b8df05167fef 已degraded并保存，ETH-r5i1 run_dff19351d7b84c6e8291199c96a0a377 已于01:52:58.855342UTC completed/error null，artifact art_12bfd8ce4a2a4ad1ac30026cfe84158a。两份正常owner API报告认证读回均全过，各技术四项PASS；BTC概率35.74/31.81/32.45%、ETH34.86/33.70/31.44%，均精确保留90d prior，不是模型预测增益。BTC已证漏纳child daily9/1与macro/cross各4/2，R5j待修；ETH daily9facts实际来自ROOT call361/result363，row_count91，hardcoverage1.0但仍6soft gaps，不反证child缺口。官方stop08 exit0，stopped/absent/listeners0；业务9failed+1cancelled+1degraded+1completed原件保留。r5j/pre-stage1-freeze-check.json核79旧源/SDK+model及6前像全同。现仅A/B新增R5j测试获Stage1 GO，未修改生产；wire-spec d1d84fc…为child-only新v5，旧v4/hash不变，统一freeze后才RED。无完整发布、服务或付费请求；C/D/E未完成。下表旧running及早期续记均历史，以本段及CODEX_LOG最新条目为准。

> 当前续接（Stage7）：R5j TS42/Python新78+旧49运行PASS，最终fullplugin tsc0、新helper Ruff0/strict实际1file0/0；之前所有setup/类型/风格失败均保留。最终helperSHA2f66c20d…b418，stage7-actual-freeze.json87 selected及model同，所有源码/tests停写。R1已获离线actual原件整链EXEC GO：BTC旧v4经DTO→SDK→event88traces→resultmapper exact9c1b…/degraded通过；Source读回先发生artifact strict日期JSON边界错误（已按原strict API纠正），后因进程缺development路径env在raw archive处FileNotFound而停，未判原件丢失。Root只准按官方Layout/实际launcher补齐离线子进程path-env、单文件核原raw_ref后一次旧Source纠正；不改生产/历史/校验，不扫formal。BTC v5、ETH、容量/停止、最终独审及源码重启旧报告真实读回仍待执行。服务stopped，无新paid/完整发布；旧81类型诊断与项目其他未完需求保留。

入口：继续当前 Main 原系统渐进修复，保留原界面。不要从 T-000 或旧 D 盘实验分支重新开始。用户已授权必要代码修复、开发启动、真实研究和自主常规裁决；本轮明确不运行完整发布打包、不删历史、不改正式数据、不 commit/push。

本页初始快照为 2026-10-28 19:59 UTC；新增终态见页尾续记与 `CODEX_LOG.md`。目标仍 active，项目未整体交付。

## 可复用的开发方式

- 工作目录：`E:/project/study/codex/dsh/decision-hub`。
- PowerShell 7：`C:/Users/90603/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe`。所有官方 PowerShell 脚本使用它，不使用 Windows PowerShell 5.1。
- 启动：`infra/dsh/run-dev.ps1 -DevelopmentRoot data/development/default`；状态：`infra/dsh/status-dev.ps1`；停止：`infra/dsh/stop-dev.ps1`。后台 Start-Process 使用 Hidden。现有实例先检查身份与状态，不重复启动。
- 日常改前端由 Vite 开发入口处理；改后端或插件在研究队列空闲时停止、修改/测试、源码重启。插件有变化时局部编译，不运行 build-product.ps1。
- start07 Python局部改动复用已验插件15产物，无新插件编译；98.153238秒首次ready。此前start04/05 Vite报告6.46/6.34秒只是编译时长，完整启动另计。默认不强制BuildPlugin，让官方身份检查决定缓存；没有新的完整发布构建。
- 开发地址：前端 `http://localhost:5173`，API `127.0.0.1:18047`，DSH `127.0.0.1:18097`。开发模式的正式发布、金融资格、执行授权均 false。
- 设置配对使用开发实例私有快捷方式，凭据/认证 URL 不得打印。现有仓库外 Client 可复用，但不要调用重置仓位和风险的便利函数；研究提交工具按保存的 revision/state_version 发新 request UUID/Idempotency-Key。
- 每个真实研究期间冻结全部源码和模型设置；可追加文档、只读查询及观察。失败后先真实原件离线 RED/GREEN、独立审查，再决策新请求；不能盲重试、延长截止或改失败状态。

正式目录 `data/core-live-20260913`、`data/dsh-live`、`data/product-local` 保持保护。最近一次在start06停止后由 `live/protected-after-start06.json` 核六份既定正式文件SHA全未变；这是该时点/该六文件的证据，不代表全目录盘点。

## 已完成的局部修复

证据统一位于 `artifacts/incremental-delivery-20261023/d28-research-recovery/`。

| 项 | 已证原因与改动 | 局部验证与证据 |
|---|---|---|
| R5a 审核视图 | 无损共享重复原件字符串，完整审计原件保留 | compaction/；相关95项及定向8项通过，完整插件 tsc、独审通过 |
| R5b 生命周期 | recorded 与 observed 分开，PID出生身份及所属子树验证，退出不假记 stopped | lifecycle/；25项通过，实际启动/停止/重启；start04 另有退出竞态，见下 |
| R5c 模型声明 | 开发初始化硬编码128k，曾把214299输入正常 stop误判 overflow；同路由来源声明为262144 | capacity/；21定向、111相关通过3SKIP；仅contextWindow受控迁移，maxTokens8192不变；262144不是远端能力实测 |
| R5d 材料视图 | 双FOMC阶段重复原文277174B超过256000B | materials/；无损共享233060B并精确展开，独审通过；实际新材料233046B；部分相关旧失败保留 |
| R5e V3保护版本 | 协议/bridge支持V3，两个retained selector却仅认V2，导致真实child来源被剔除 | context-schema/；RED9→GREEN24，最终focused+admission38通过、完整tsc/独审；真实V6/4调用原件/5子证据已验证 |
| R5f 审核指令 | SDK继承preset自动注入AGENTS baseline，被误判第二条产品提示 | reviewer-instructions/、review/r5f-final-review.md；真实父子baseline精确同源，99项通过、完整tsc0、独审C0/I0；真实负评仍保留 |
| R5g 压缩后指令 | SDK压缩后重发相同baseline，R5f误把历史两帧当歧义 | reviewer-compaction/、review/r5g-final-review.md；SDK合法替换/唯一有效baseline，真实完整解码RED→GREEN、117项/完整tsc/独审通过 |
| R5h 历史资料局部容量 | 已有完整视图被128KiB最后资源门回退，BTC177046B/旧ETH176584B源均完整 | history-originals/、review/r5h-final-review.md；128→192KiB单行，global/auth/PIT不变；18new/旧1PASS、strict0/原Ruff I001保留。新f89d真实history175917B/90rows/13partitions与v4校验全过；独审需修3项内容，尚无最终报告 |
| R5i Python最终来源认证 | TS native已经APPROVED/finalize匹配，Python旧checker拒SDK agentinstructions；149投影无完整prefix | python-final-attestation/、review/r5i-final-review.md；Host完整prefix官方fold生成typed独立binding，Python仅顶层源消费；actual原件双mapper/witness GREEN、原序列/hash/概率不改，C0/I0接受。旧literal失败保留，新真实09fc待终态 |

R5f 使用宿主独立完整父日志与原call cutoff；R5g以官方SDK surface证明合法替换，保留全历史，不仅凭kind放行。当前冻结 `live/active-code-freeze-r5h.json`：原R5g17文件全同，加历史模块6272bd…，共18源/SDK，model不变；新tests/fixture也已冻结。

## 真实研究结果（按 Run，模型完成不等报告交付）

| Run / attempt | 已观察结果 | 首因或限制 |
|---|---|---|
| 旧BTC 82740daa… / 旧ETH e8c4952c… | 均约60分钟失败，无审核通过报告 | BTC本地容量误判；ETH独审启动仅剩约84秒。旧稿内实际已有方向概率，交接的“没有任何概率”已纠正 |
| ec03e5b9… | 准备失败，0模型调用 | 材料277174>256000，R5d已处理 |
| 855459f0… / btc-r5d1 | failed / research_perpetual_synthesis_uncommitted | 最早 native_context_invalid 为V3 child wire选择失配；后续childwriter错误不是首因，R5e已处理 |
| b6a9b2cd… / btc-r5e1 | 20模型完成，产品仍failed无报告 | FOMC事前freeze已错过、模型自报blocked；现货两venue新鲜度不足。不能倒填时间或把blocked改为成功 |
| ab9b2a7f… / btc-post1 | 27模型完成，产品failed无报告 | prepare成功；真实独审为needs_revision1条，但transcript guard误拒绝SDK baseline。R5f已处理兼容；负评仍须模型修订 |
| 46aec510… / btc-r5f1 | 初始running，后由owner取消，cancelled_by_owner；request bfe3f5a5-6349-43b5-b580-8d0728d353be | 实际guard因SDK压缩后重发相同baseline而再次拒绝；R5g已离线修复。本Run无最终报告，不恢复或重复同请求 |
| 6d5022a9… / btc-r5g1 | failed/uncommitted，22:26:23UTC；request 5c1dba3c-9241-4b41-8523-5fc73c70ab7b | 真v4及output/SDK证明通过；needs_revision1缺可读history fullview，model补读被slot拒绝后停止；R5h离线修，旧失败不改 |
| f89d6251… / btc-r5h1 | failed/dsh_protocol_invalid，23:34:15UTC；request 8d611296-9a8b-42fd-8c4b-94c4488434d7 | 真fullview1、复审APPROVED0、native finalize matched全过；Python最终消费者review_child_transcript拒SDK指令。R5i离线修，不改旧失败，无保存报告 |
| 09fc0ef0… / btc-r5i1 | 当前running；request dff2f01d-05b2-4d44-b527-5da46e49d941 | start08/newbuild4ae02f…/79选定hash+model冻结；actualinitialV2/221580B通过。Rootwatch38573，R1 observer27339持续每轮review/terminal，不重发 |

本轮尚未提交新ETH。旧两Run、每次失败原件、请求与日志均保留。R5f离线实际capture证明helper和负评parser通过；旧失败未曾发出成功wrapper，其实际run.result/output相等性仍UNPROVEN，不能由合成集成测试补授真实资格。

`VERIFICATION.md §4`逐Run验收：基准率冻结输入、研究终态、独审原件含direction_base_rate、最终概率及偏离依据。post1为PASS/FAIL/PASS/UNPROVEN；P4幅度和P5到期评分另外记录，草稿不替代最终报告。

## 测试基线与需求缺口

- A1超时归因、A2旧实例停止原因、A3状态命名空间核对、A5 PAPER期限/游标静态核对已完成。旧实例有Windows实际重启证据，具体触发未知。
- A4清单已全部观察，**不要再跑remaining**：原250补测232PASS/16FAIL/1SKIP/1ERROR。原1030跨版本最近结果944PASS/81FAIL/3ERROR/2SKIP，0未观察；不是同一当前源码全绿/全repo全绿。F01重复67不加分母、新R5d28另列；原10-23日志无nodeID，不能称全部10-23旧失败。见baseline/r01/r01-terminal-causal-review.md。
- F01的49条publication/views共同阻断已静态定位至真实run precheck早于fixture mock，夹具未建RunRecord；具体运行分支/修后结果待判别，未修未通过。5条性能、4条缺原件、9条其他单列。见baseline/causality/。
- P3确定性基准率审核仍缺；P6基准率/模型并列与理由/仅基准率标记未接齐；P5 baseline log-loss与明确“无对照”文案未齐。P4分位数原件和提示存在，实际最终报告幅度尚待证明；不改action_comparison公式。
- 多事件需求未全完成。日历默认可显示多族；独立事件预测面板的producer/query/render目前只覆盖CPI九格。PCE/就业/FOMC有部分解析、选择与共享链路，未取得各族真实事前→独审→发布→到期闭环。FOMC发布会自动source_rule缺口与NFP来源拒绝访问保留；ordinary V2可带明确NFP未知继续，不授事件资格。
- 正式发布未通过；本轮不做完整打包。14自然日六组合、四周评分、多事件真实资格、≥10笔PAPER及费用、30次失败回放、retention等未完成。静态存在和局部GREEN不能替代这些结果。

## 执行者的问题与防复发

1. 开发模型容量声明遗漏，导致正常provider stop误分类；已精确对齐同路由声明，仍如实保留远端上限未实测。
2. 曾在真实来源身份测试进行时修改源码，触发正确guard；已要求任何源码身份检查/真实研究时不编辑，失败原件保留。
3. 曾把CPU基线与其他测试并行，性能归因受到干扰；又在最终review未结束时先开长测试拖延修订。后续固定独立窗口，不把负载当已证唯一原因。
4. 两次向completed agent发send_message，任务未启动却等待；后续必须followup_task并核agent状态。
5. GBK→Node诊断管道异常回显不完整原文；已用ASCII安全JSON+code-only catch。重建回显前缀六类已知secret/authURL模式均0，但实际显示子集与未知模式未证明，不宣称绝对无暴露。
6. 手填UTC标题混本地日期、猜脚本路径及一次[datetime]本地转换造成诊断误判；改系统带时区时间、rg先定位、aware UTC比较。未以此重构建或改时钟。
7. start04第一次官方stop在cleanup退出窗口报identity_invalid，保留exit1。现场全部absent后仅一次官方终态分支重试exit0，状态stopped/absent/listeners0；竞态本身尚未修改源码，见runtime/start04-stop-diagnosis.md。
8. R5f漏测SDK已有压缩重发baseline场景，导致真实付费又卡；R5g已以真实完整双codec/合法surface修，不能归咎provider。R5h1新运行仍需实际资格。
9. start07缓存检查将Python小写SHA与PowerShell大写hex区分大小写，误报15产物不匹配；规范大小写后全同。v1错误与v2修正各保留，未因此重新编译/改产物。
10. R5f/g只补TS两端，跨接口review遗漏Python最终事件消费者，导致APPROVED/native finalize已成功却报告仍未落库。R5i必须完整actual Host→eventmapper→resultmapper/witness GREEN，不能只helper/producer通过后再付费。
11. R5i codegen未前置所有传递输出的即时before；有界较早前像/AST审计确认6传递文件只加DTO/optional字段，仍保留即时前像缺证。GBK codegen失败改进程-Xutf8、TS2352一行类型边界修正均原log保留；未隐作首跑全绿。

## 下一步

1. 当前BTC和ETH均已自然终态、保存并正常owner认证读回，start08已官方停止。不要恢复旧watch/Run/observer或重复付费。R5j Stage1仅A/B写新增测试，生产源/model保持原冻结；统一写入结束后再运行行为RED。
2. 正常终态必须同Run artifact/publication源/owner API报告认证读回及VERIFICATION四项，实际新Host topfield的canonical transport保存/Python消费另核；native/离线成功不能代产品报告。失败先实际原件离线复现再精确裁决。
3. R5j按coverage-semantics/QUESTION_AND_RULING.md、task-brief.md、r5j/wire-spec.md推进：行为RED后才分阶段授权精确源修改，最终生产认证constructor→Host DTO→双mapper/Source reader actual BTC与ETH回放、容量/停止/篡改负例和独审完成后再决定启动。carrier只承载完整认证child accepted数据，Python完整finalize鉴真后合并，旧v4/hash不变；不得裸扩V6白名单或放宽阈值。C项P3/P6、多事件及长期门仍未完成。
4. 更新本页、CODEX_LOG、PROBABILITY_BASE_RATE_PLAN及验收报告，保留全部失败、负评与自身工具问题。A4原250已完成分类，不重跑长测；旧8failed+1cancelled不恢复、不改名成功。

关键索引：`RESEARCH_RECOVERY_PLAN_2026-10-28.md`、`CODEX_LOG.md`、`PROBABILITY_BASE_RATE_PLAN.md`、`STATUS_RECONCILIATION.md`、`EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md`、`DEVELOPMENT_WORKFLOW.md`。不要将历史ACTIVE/PID/曾通过构建当作现场状态。
2026-10-28T20:10:37.4612307Z
R5a–R5f当前整体候选的独立cross-interface只读审查完成：review/whole-candidate/integration-review-20261029.md（44行），未发现新的Critical/Important集成缺陷，支持继续受控开发验证。审查结束再次核14生产源及modelsettings与freeze一致。审查保留start04 stop race待办、相关失败/250未跑、actual旧wrapper equality UNPROVEN；不是BTC/ETH成功、金融资格、正式发布或全绿批准。无新test/service/API/model调用/源码修改。
2026-10-28T20:51:23.3365737Z
R5f真实场景未闭合，自身设计/覆盖遗漏：新helper实际部署YES（dispatch/pluginbuild43feafb...、15compiled产物匹配），但SDK压缩保留旧baseline8并合法重发相同133，两帧source/content与child8 EXACT；历史count===1误判歧义。官方decodeStorageRecord无损展开父20703/子5432，真正reviewer seq18184==decoded index18184；packed物理outerindex1431分列，无重编号/补造。Prompt360083 exact/end1/tool0均过，首先helperFALSE；未知output equality未猜为首因。Root承担漏测SDK已有compaction重发场景的责任，非部署/provider问题。新Question与自主裁决reviewer-compaction/QUESTION_AND_RULING.md，R5g exact task-brief已在源修改前落盘；复用官方surface证明原call前唯一有效baseline和合法shadowing，保留全历史，不直接放宽count/取latest，不改原件、时限、配额或审核结论。

Root已用正常owner命令3fa347b8-a778-44af-a5b9-4b4c71c689e9取消46aec，accepted；实际cancelled/cancelled_by_owner，未改名成功或伪称自然failed。30modelcompleted、2reviewer意图/1finalize意图不授审核/发布成功。四项PASS/FAIL/PASS/UNPROVEN见btc-r5f1-terminal-acceptance.json，ETH未发。全部历史保留，无新付费请求。

start05官方stop exit0、recorded stopped/observed absent/listeners0，业务7failed+1cancelled。外层observer实际exit-1分别保留，不混成exit0。原件start05/stop-result.json/status-after-stop.json/terminal.json；现可在已停实例上受控R5g。下次付费须先实际SDK decoded compaction RED/GREEN+独审，不盲重复。首次写R5g artifact补丁格式无效，工具验证拒绝未落盘；已一次纠正模板格式成功，无源码影响。
2026-10-28T21:01:37.8693737Z
R5g最终局部ACCEPTED，review/r5g-final-review.md C0/I0：11文件117PASS（原99不改）、全插件tsc exit0/0诊断、69旧tests/support SHA全同。helper4f87eae2...f3bc28a，SDKpeer/lock importer固定现有0.1.2-alpha.2并仅新增本地junction，实际Host/plugin同realpath/SHA，无install/升级/SDK改动。实际完整双codec回放旧helper RED false→新GREEN true，合法surface replacement130 shadow8、active133、needs_revision3保留。原raw、decoded前后及官方encode回环各自SHA域不变；首actual-GREEN失败是离线存储range decoder漏一层，按SDK loader真实顺序decodeSeqRanges→decodeStorageRecord纠正，未为存储格式放宽生产。新single部分负例澄清为multi路径，旧69断言全保留；原foreground live equality仍UNPROVEN，生产pureselector/增量fold/lastmessage重建hash一致另见foreground-mapping.md。

Root已生成live/active-code-freeze-r5g.json（16项目源码/metadata+1既有SDK runtime只读hash，modelsettings c1...不变），plugin-before-r5g-restart.json。现服务stopped，所有源/tests/metadata保持冻结，先补A4原250确切未终态节点，独立资源窗口不发模型、不编辑源码；完成/分类后再start06，不跑完整发布。此接受不授BTC/ETH报告成功，旧7failed+1cancelled和C/D/E未完成仍保留。
2026-10-28T21:24:25.5368377Z
R01取证补记：截至21:23UTC附近40条状态（32PASS/7FAIL/1ERROR），非终态、未作失败归因。test_actual_24h_signed_entry_to_native_graph_unique_action_and_saved_report已PASS，明确synthetic child backend+真实归档源/服务写/完整读路径，不授真实Provider/24h金融资格。

执行遗漏与保全：开R01前未先查旧测试的固定示例输出路径，在运行中才发现multi_horizon case写Main artifacts/d2-multi-horizon/actual-24h-{advice,saved-report}.json。Root21:18:50已先逐字备份当时Oct19旧文件并核双SHA相同；官方测试21:18:57确实刷新两目标，旧字节完整保存在r01/preserved-generated-examples。未手动改旧文件/恢复覆盖新结果。把既有官方测试正常生成样例归于已授权正常产物写入，不能扩大到正式数据/历史失败伪改；仍须如实列副作用，不宣称全部artifact未变。

43个选定测试源的有界AST输出核对另见r01/fixed-output-side-effects.json、fixed-output-root-addendum.md。确认5个固定sink，ETH及默认概率输出预启动mtime已保留副本；review-revision目标原不存在；没有全库遍历/改测试重定向/权限变更/删除，旧两备份未动。首个PowerShell Select-Object摘要对OrderedDictionary显示null字段，真实before.json已核包含完整正确path/size/mtime/SHA，摘要错误不当作备份失败或成功凭证。今后长测前先做所选源固定写出清单与前像，而非运行到时才发现。
2026-10-28T21:52:00.4941403Z
R01补测真实终态exit1：250节点，232PASS/16FAIL/1SKIP/1ERROR，pytest2850.79秒（wrapper2857.277秒），未中断/重跑。r01/terminal.json、results.xml/stdout/stderr/last40全保留；3monitoredPy源未变。Root另外核candidate-source-after.json全部17候选/SDK文件及modelsettings未变。初始原1030库存现250补段已有终态；原712PASS等历史与F01 67重复不得混加为当前全repo全绿，新R5d28另列。

解析器已一次成功：250XML一一映射，无missing/extra/重复/F01重叠/stdout冲突。ERROR actual_factory_native_carrier...的original_preparation_deadline_elapsed栈与保留R5d固定clock/未patch utcnow旧fixture问题同节点同边界，未据此证明新真实BTCETH阻断。16FAIL初分8个manual_execution:254 timing、2不同AttributeError、2UnicodeDecodeError fixture读取、1生成输出父目录缺失、1continuous command invalid、1observation locator invalid、1prepared implementation change；详细源位置/安全摘录由r01报告补齐，不猜CPU因果，不将它们标通过。

当前没有长测或研究，source仍按R5g冻结。下一start06官方源码模式，核现有SDK公共surface模块加载、局部插件编译与全部ready后，再新BTC-r5g1关联已取消46aec；先BTC后ETH，不改旧回执/不完整发布。辅助build recorder只新增可选before-manifest参数用于对应R5g前像，旧默认行为与旧报告保留，不影响产品源。

### R5g1 首个真实 v4 独审回执：阻断修复已在真实链路验证，负评保留
时间：2026-10-28T22:26:22.8513712+00:00
Run：run_6d5022a9417b4db29d32aaae153cc6dd。实际 native call7514 22:20:38.829UTC → result7515 22:22:23.860UTC，isError=false，唯一decision-hub-frozen-review-result.v4。
证据：artifacts/incremental-delivery-20261023/d28-research-recovery/live/btc-r5g1-reviewer-proof.json/.md；JSON SHA256 36bfc7c5bf807aa1bec38ab7378fac0e7c15a21a20b8c6d81328a6a1f85488a9。
事实：完整官方双codec与foldSurface证明父历史2条相同baseline中合法shadow旧8、唯一active7344，R5g helper真实返回true；全producer原件/身份/packet/prompt/child-events checks TRUE。实际持久化v4.original_output与两个独立SDK投影完全相等，output canonical SHA fad5d910dec0b2d4590608e172923e4a390a05a1a103cea2d3f483a1d985902d；这次是实际原件相等，不是旧Run的重建猜测。
事实：独审 verdict=needs_revision，1条finding，涉及3candidates/5fields；要求daily-history摘要补充认证coverage/computation原件。direction_base_rate原件1，body/source_ref hash同a091e2e307623409996abd6ab86e297da9d6adb60dfe027e900aec27f2c8779b。负评原样保留，让模型按既有流程修订，未手工approve。consumer/finalize/保存报告/金融资格尚未授予，Run仍running。
自身工具问题：只读observer的离线verifier首次漏第二ref codec，exit1/108stderrbytes（原stderr未保留）；一次复用官方完整decodeSeqRanges→decodeStorageRecord后核验通过。产品callback在此之前已成功，未因此重启/打包/付费重试，不能把工具失败算成产品失败。observer43862已自行退出，Root watch51529和开发50510继续，源码/model继续冻结。

### start06 已停止、冻结复核与自身记录错误更正
时间：2026-10-28T22:29:41.8692393+00:00
官方stop exit0、status exit0、recorded stopped/observed absent/listeners0；开发observer50510实际exit0。8failed+1cancelled均为历史真实终态。证据live/start06/stop-result.json与status-after-stop.json。
自身记录错误：复用artifact stop-observed-idle.py时仍含上次cancelled专用reason。本次btc-r5g1实际自然failed，未执行cancel。原receipt不改，另存live/start06/stop-reason-correction.json明确更正；helper未来reason改为所有研究已terminal的通用事实，不涉及产品源码或官方停止行为。
正式保护：live/protected-after-start06.json六个既定受保护文件hash全同（不是全目录盘点）；live/btc-r5g1-source-after.json全部17候选/SDK与modelsettings全同。没有手动变更历史/正式数据、没有git提交、没有完整发布构建。当前服务停止，候选仍冻结，待实际新因果和精确任务卡再允许源码编辑。

### start07就绪、缓存复用与新BTC-r5h1唯一请求
时间：2026-10-28T22:54:17.1169360+00:00
实际启动22:48:55.2052186UTC，首次ready观测22:50:33.358456UTC，98.153238秒；7服务online/APIready/DSHtrue。正式发布/金融/执行权限false，未执行完整发布。
插件缓存实证：start07/plugin-cache-proof-v2.json全部checks TRUE，15compiled artifacts与stamp/新生成identity一致；build log/stamp字节及mtime仍旧，新编译未发生。Python局部修改不影响插件TS身份，因此复用原已验产物。status-running.json官方recorded/observed均running；pre-research-verified.json全部18源/SDK和model不变。
自身工具错误：初次plugin-cache-proof.json exit1/plugin_cache_checks_failed，由Python算小写SHA与PowerShell大写十六进制作区分大小写比较导致15/15假不匹配。一次仅规范SHA大小写更正后15/15匹配；原错误proof保留，v2另存，未因此改产物/源码/重启/重新编译。不把该工具错误当产品失败。
唯一新付费请求：btc-r5h1 / request8d611296-9a8b-42fd-8c4b-94c4488434d7 / run_f89d625138e74336899b5f47beeb4aea，22:52:38提交回执queued，22:52:43观察running，savedsettings preserved。prior为failed6d5022…，新UUID非重投旧请求。submit stdout/stderr/terminal全保留，stderr0，无自动retry。
当前observer66872/PID8548、watch92448；只读R1观察本Run实际新full history视图/审核/终态，18源/model/tests冻结，无长测/源码编辑。ETH尚未新发，旧8failed+1cancelled不变，当前BTC尚无报告，真实四项待后续核对。

### R5h1 native最终已批准/提交成功，产品Python消费者漏兼容导致失败
时间：2026-10-28T23:42:29.3203918+00:00
首needs_revision3和第二APPROVED0原件均保留。第二proof live/btc-r5h1-review2-reviewer-proof.json/.md，JSONfbed77418023cf3ec576030c09dac6b885c5d9195e90fa8971a1960dae275a2a。实际prepare19576与新candidate/packet一致；reviewer result19788 23:31:02.100UTC、v4/全部原件通过；actual prompt601410B，input238375/output5322/context262144，正常完成。
实际finalize SDK19973→19974 isError=false，decision-hub-synthesis-tool-result.v4 / final-review-gate.v3 status matched / synthesis.v2成功。DSH completed23:33:40.194UTC。产品Run于23:34:15.247UTC failed/dsh_protocol_invalid，artifactnull，不能以native成功代产品交付。
确切首因已用实际149 Hostnotifications、原finished_at、actualexecutionrequest完整production入口纯回放RED：web_runtime937 map_notifications→event_mapper66→tool_result_attestation1513→perpetual_attestation1063/_review886 ValueError review_child_transcript→1162包dsh_protocol_invalid/cause perpetual_synthesis_attestation_invalid。Python旧_review_child_input_matches在childseq8 agent-instructions source119→128拒绝；真实end1completed/answer==persistedoriginaloutput均TRUE。不是V2/V3推测或最终297B非JSON prose的已证原因。
自身漏项：此前R5f/g仅补TS producer/consumer，cross-interface审查没覆盖最终Python event mapper，导致又一次付费报告未落库。必须补完整actual Host→eventmapper→resultmapper/witness回放，不只单helper GREEN，之后才允许新付费。原Run不改，四项PASS/FAIL/PASS/UNPROVEN，ETH仍未新发。
证据live/btc-r5h1-terminal-causality.json/.md（JSONbf71bdb00ebc7ced05bdf0f10a7bbc5e1e73b7abe40b7a4d801ccf80f290d4b2），review/final-result-protocol-static-trace.md、review/python-baseline-parity-design.md。149是过滤投影，不能假称完整parentprefix/重编号/填造，不能sourcekind白名单或读liveAGENTS放权。正在评估完整原件所在Host先官方SDK fold→独立顶层native baseline binding投影的明确契约，尚未新改源。
start07官方stop exit0/stopped/absent/listeners0，observer66872 exit0；9failed+1cancelled。live/btc-r5h1-source-after.json全部18源/SDK和model仍同，未完整打包/改正式数据/git。停止空闲服务后继续离线取证和新精确范围裁决。
派生记录错误另存：live/btc-r5h1-review2-template-correction.json/.md更正首proof的$.diagnostic_initial_failure旧R5g模板残留；首R5h实际verifier exit0/no codec failure。首freeze8175c45...不改，不为模板错误补跑模型或证明。

### R5i actual完整双mapper GREEN、最终独审接受、start08启动
时间：2026-10-29T00:19:35.3318461+00:00
actual-replay/report.md SHA0289f78498ddb1715c7f5f569341b1d520f1e695587f12d7b61b797351e92c41；result.json c2a93817788c53d31c7d8d808f9a517354fa244f38b1696b2a43ccfbb6fa0022；postcheck d00393ac0bddd1722a241c6eff95cb1a551ce0df4cd48b760e56800008552b12。Root已读回报告及review/r5i-final-review.md，C0/I0 ACCEPTED范围仅修复与受控新真实验证。
完整19977实际native seq0..19976→productionHost两call10079/19787的binding（active9890、禁止后19797）→native ack/MATCH与3种tamper拒绝→generatedDTO→production_sdk_run原149索引不变+尾1meta→map_notifications81traces→map_session_result_with_witness completed、perpetual非null、approved、tool_capture19973/19974全过，第一负评/修订/最终审批历史重验保留。oldresult_hash、events_json、297Bfinaltext、概率/时间/SQL/原native/proofs均未改；这是新离线DTO，不是旧wire/Run翻案。
artifact驱动自身纠正均保留：猜DBrequest_id列、zstd单帧短读、schema对象键顺序比较、request row缺runtime/profile标签。只改artifact，从实际frozen research_execution_method/上游identity原生产格式取最终标签，不编造/替换时间，不修改生产门。没有更后产品predicate失败。
独立peer将唯一旧literal失败判本scope非阻塞但未闭合：41PASS1FAIL保留/pre-R5i未执行，不能称全绿或已证旧；新36与A27/150/tsc/UTF8codegen证据保持。不要求新套件、旧assert改写或profile文字补丁。
Root存live/active-code-freeze-r5i.json：80核验条目合并后79唯一选定文件（含1只读SDK；原80计数不是80个不同文件），model c1…同。plugin-before-r5i-restart.json前像已存。现官方observe-development start08启动outerPID30404/exec61871，无完整发布/新付费；预计仅变更TS的局部编译，但必须另核新stamp/log/部署/ready，不以预计算成功。下一新BTC-r5i1需新UUID prior failed f89d，再ETH；当前尚未发请求。

### start08就绪/新插件身份与BTC-r5i1真实验证
时间：2026-10-29T00:26:15.0321480+00:00
start08实际启动00:18:32.006484UTC，首次ready观测00:21:06.051947UTC，154.045463秒；7服务online/APIready/DSHtrue。局部Vite报告6.3秒（非完整启动时间）；local-plugin-build-proof.json新log/stamp通过，未跑完整发布。
pre-research-verified.json官方status exit0、recorded/observed running，79选定文件hash与model c1…全同，15compiled artifacts及生成identity全匹配；新plugin build4ae02f07d591994780dc82eaa4c9d9e54767989b386bec987e6f7547a7d52f5a。旧业务仍9failed+1cancelled；旧f89d保持failed/dsh_protocol_invalid/artifactnull，无自动翻案。formal/financial/execution权限false。
唯一新请求btc-r5i1：requestdff2f01d-05b2-4d44-b527-5da46e49d941，Runrun_09fc0ef0a2fc4444a812b8df05167fef；00:23:24UTC回queued，00:23:56观察running，savedsettings preserved，stderr0/no retry，prior failed f89d。watch38573、开发observer61871/PID30404。R1仅只读实际admission/reviews/terminal Host新topfield与Python消费；79选定源/tests/生成件及model保持冻结，无长测/编辑/新ETH/完整打包。
下一步必须真实completed/degraded+artifact并owner API认证报告读回，才算本次BTC；offline GREEN及native APPROVED不替代产品保存。其后同候选直接顺序ETH，无需再编译重启。当前仍无本轮BTC/ETH保存报告，目标active。
