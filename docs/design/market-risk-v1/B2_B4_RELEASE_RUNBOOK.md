# B2–B4 标准发布与 PC 验收执行单

> 正式15b标准构建/启动P：9434源/41产物/1982928B，UI/Host各1exit0，3health200/7online/schema0081；真实新完整报告门F。[15b标准构建](../../../artifacts/mr-release-b2b4-15b/product-validated-builds.json)、[15b实际启动](../../../artifacts/market-risk-implementation-20261007/B2/formal-start15b/receipt01.json)。 两普通PC请求已各一次保存，两请求已终态、完整报告门F；15首F永久保留。[15b的16工程门](../../../artifacts/market-risk-implementation-20261007/B2/release15b/ENGINEERING_ACCEPTANCE15B.md)及[唯一当前入口](CURRENT_FOCUS.md)。下文编号记录属于各次历史，不替当前新报告P。

> 当前唯一状态入口：[一页纸](CURRENT_FOCUS.md)、[交付状态](CURRENT_DELIVERY_STATUS.md)、[14具体修复与验收](14_REAL_ACCEPTANCE_FIXES.md)。以下编号发布记录是历史步骤；“当时运行/待执行”不得作为当前状态。旧14预检F14-03/14b清单兼容F已保原件；现行运行以顶部新启动原件为准，13已停止。

日期：2026-10-08。schema0081。本页保留历次实际发布顺序与失败原件；下文版本号属于各历史步骤。当前运行、唯一下一步及逐项通过范围统一见CURRENT_FOCUS.md与IMPLEMENTATION_CHECKLIST.md，不能由命令存在或旧READY推定。

## 入口与停止条件

- 候选目录：`E:/project/study/codex/dsh/.market-risk-b2-b4`；正式目录：`E:/project/study/codex/dsh/decision-hub`。
- 先冻结代理拥有的生产文件，核实所有封签。MR28跨重启旧代码加载需实际控制；同进程双版本与恢复旧数据库不能代替。
- 正式空闲核对已完成：2026-10-07T22:05Z API/DSH 200，无queued/researching；需在实际停机前再核对。
- 任一步失败保留该次日志和原件。仅在具体错误修复后重试；新文件或哈希不能覆盖旧失败。受管发布门禁止放宽。

## 02首次发布顺序与历史回执（现行状态见末节）

| 步骤 | 操作与通过条件 | 保存位置/当前状态 |
|---|---|---|
| 1 冻结 | 对全部候选SourceMap重新生成SHA清单；代码生成清单已核验，源改变后旧清单不得复用；逐件比对代理封签 | B2/candidate-source-inventory02.json；2142受管源冻结通过 |
| 2 预览 | promote_market_risk_source.py dry run，只读核实目标和变更清单；备份能力已具备 | B2/source-promotion-plan02；2524项核对通过，193项变更 |
| 3 停机晋升 | 正式stop-product.ps1；工具核对stop.request、进程原生命周期和8047/3097/8037/8038无监听，先完整保存受影响旧字节再apply | B2/source-promotion-apply02；标准停机门/原字节保留/193项晋升通过 |
| 4 标准构建 | 正式build-product.ps1，UI/Host各一次；绑定B1 receipt SHA及.b1-original-01，使用-InstallSpotResearchV3；得到实际新源/构建/Pack/recipe封签 | artifacts/mr-release-b2b4-01构建前拒外部原件目录，0 UI/Host；2234原件迁入artifacts/b1-src02后mr-release-b2b4-02通过：UI/Host各1次，2884源/3633产物、V3指针/组件闭包受管绑定 |
| 5 副本迁移 | 正式upgrade_market_risk_schema.py --probe，对唯一已命名DB一致备份；明确0076→0080，核升级前106表全部行/hash、ORM/index/FK及旧已送通知 | artifacts/mr-schema-probe02通过：106旧表行/hash保留、4迁移原子、ORM/index/FK通过 |
| 6 正式迁移 | --apply绑定通过probe及新build receipt的SHA，重新核停机和旧记录；不stamp、不downgrade、不丢后续数据 | artifacts/mr-schema-apply02；通过：0076→0080原子升级，106旧表/655已送通知保留，绑定probe/build实际SHA |
| 7 标准启动 | run-product.ps1 -ValidatedBuildReceipt新回执 -EnableSpotResearchV3；实际受管API/DSH/MCP/acquisition/Worker/监控在线 | data/product-local/logs/20261007T223345023Z；标准启动API/DSH ready，未作模型结果验收 |
| 8 只读检查 | verify_market_risk_installation.py --require-fresh，19 GET一次/并发3，保存原响应；两资产scope、V2 trial及三增强路线一致，坏原件明确拒绝 | artifacts/mr-read-census02；19 HTTP200，但首轮BTC fresh门失败；后续2摘要fresh，性能缺口已登记 |
| 9 真实AI | 正常初始触发BTC/ETH各一条现货Run：fresh原件准入→Pack1.2/V3→实际DSH工具/模型→V4质量/反证/审查→publication.v4完整报告；核预算退出与未解问题，没有结果则记录实际失败并修复 | B2/formal-real-dsh02；ETH实际失败：excerpt超4000，0工具/模型；修复中，不能以Host ready通过 |
| 10 PC | 正式8047切换两资产、自动/手动更新幂等、暂停恢复/刷新、报告摘要与完整质量一致、30m/24h/7d缺口、多空情景、原主线入口、成绩/记忆/增强及实验回执；实际截图、URL/时点/动作、控制台错误 | artifacts/mr-pc02；已实操ETH/路径/多空/原Run进度，发现范围文案问题；12组继续，手机不验 |
| 11 结项 | 每项MR更新源码/合同/真实证据/限制/回滚与PC手册；失败修复只重跑受影响门；工程与预测资格分开 | CURRENT_FOCUS/execution-contract；待正式结果 |

正式构建参数的既有基线：B1回执`artifacts/market-risk-release-b1-01/product-validated-builds.json`，SHA `ac4472964ffc8192a9bd757bc6520b0c4dbd5fdc1d8683bc13048f233e5bc43a`。执行前仍逐件核源/产物，不能只信本文字符串。源码原始保留位于`.b1-original-01`；标准生产者只接受正式artifacts内的原件目录，实际使用逐件校验的`artifacts/b1-src02`，不放宽验证门。

## 成功结果怎样裁定

PC可用、真实DSH终态和原件链通过后可交付受控研究试用。宏观缺精确发布时间/vintage、仓位/事前共识缺源、黄金未准入供应商均明确未知，不构造数据。预测频率保持未校准，实验警报没有避险收益承诺。

固定28日shadow自动到期输出一次不足或无增益结果，不补写历史、不自动开新周期。它不可能满足当前100独立日资格；足够长的后续独立观察需另登记。3名非开发用户理解门当前未执行，只能报告开发自测，不能改称用户验证。

## 本次必要修复发行（03）

发现F02-01输入长度、F02-02原件查询索引和F02-03范围文案后才安排03发行，不是重复未变构建。以当前build02真实回执929a4071…7631为基线，先完整保留当前2884源/3633产物在artifacts/mr2-src03；开发修复、相应有限控制和封签后，标准停机/晋升/构建，明确0080→0081副本及正式迁移。旧失败Run、初始fresh失败、首轮build目录范围拒绝全部保留。

03执行记录：当前build02的2884源/3633产物共6517原文件已逐件SHA保留于artifacts/mr2-src03。全部修复冻结SourceMap2143/SHAa0488fa1…f932b；dry2528核对/14变更。正式空闲active_count0核对后标准停机门通过，14源晋升通过；mr-release-b2b4-03标准构建执行中，使用build02实回执及受控artifacts内保留根。

03标准构建通过：UI/Host各1次、源码前后稳定、3350源/3651产物、2个组件历史闭包；实际回执SHA8c01c3c3f0dd61a7b534dae05715b6a5c311fc67a7b7c03d40e7d60ebb766b68。artifacts/mr-schema-probe03正在执行0080→0081完整原表保留门，不以索引私控代替正式迁移。

03副本迁移通过：0080→0081，124张升级前表完整行/列/hash保留、旧Run/报告/outbox与ORM/index/FK通过；probe SHA f4167c14db24ed43264ccaee335867da262fcb41e7ab0a88db7b726a9ef59af6。正式apply03执行中，重新绑定同probe/新build/源码和标准停机门，不修改旧Run或手工stamp。

03正式迁移通过：0080→0081，124旧表/旧Run/报告/outbox原件完全保留，apply SHA acf11603ba39ef082dcdb2932d6df951d44861aba1159f9cc123b1db3a1faa3c。标准run-product03已经READY；输出凭据行不入验收文档，Host ready不当真实报告成功。

## 03历史验收结果

| 门 | 实际结果与原件 | 判定 |
|---|---|---|
| 标准构建/迁移 | build03 SHA8c01c3c3…766b68，副本和正式0081通过，124旧表完整保留 | 通过 |
| 受管启动 | B2/formal-start03/receipt01.json：API8047/DSH3097/采集8038ready，7业务服务online；schema0081 | 启动通过，不是报告通过 |
| 有限真实读取 | B2/formal-index03-check01/actual01/receipt.json：6GET200，两摘要1440/fresh，索引SEARCH；2旧FRED accepted/90拒绝 | F02-02/04修复通过；public/vintage/PIT不足保留，不称O04 p95达标 |
| PC范围文案 | Root显式reload后index-BarhUTOe.js，原Run失败范围、global原任务和常规目录均正确；mr-pc03具名截图 | 三处通过；12组完整矩阵仍待 |
| 真实DSH报告 | 两真实新Run均failed/dsh_turn_error，native turn/end seq6首因research_catalog_http_409，0工具；B2/formal-real-dsh03/native-failure01.json | 未通过，Core最小修复catalog接线 |
| 交付 | MR表、当前一页纸、手册和机器状态03同步 | 完整研究试用尚未验收 |

03修复已发布后，旧tab只hash切换仍保留02脚本。正式验收必须显式刷新、等待加载、核实际脚本/发布index对应，再执行按钮和截图；本次旧/新截图均保留，不为缓存误读重新构建。金融资格、黄金/共识/仓位外部缺口另记，不以正常服务或空态消除。

## 04：正常 MCP 接线修复（2026-10-08）

03 两真实 Run 在 native turn/end seq6 以 `research_catalog_http_409` 停止，0工具/无报告。确认正常MCP未配置V3读取器；02 Factory输入长度缺陷已解决。本次仅MCP正常启动配置、Host安全目录状态码原因、PC保存请求等待时间3个生产源及1个Python回归文件；5项MCP/Host控制通过。

03全部7001个源/产物已逐个SHA保存在 `artifacts/mr3-src04`；标准停止守卫通过后，2529文件核对、4变更带旧字节备份正式安装。04标准UI/Host各构建1次，均退出0，3585源/3669产物，真实receipt SHA `9f7c24bfdd4a5907f51983bddc5d214005369403af7023b648008866885f01b0`。0081未改变、不重复迁移。Host回归 `.spec.ts`按SHA补交 `extensions/dsh/decision-hub/tests/catalog-terminal-cause.spec.ts`，属于非运行时测试，不改runtimeSourceMap。

当前标准启动04中；ready、真实MCP目录、两资产报告和PC等待说明仍需后续实际回执，不以控制/构建宣称通过。

### 04 READY及保存请求等待提示实际回执

前段“标准启动04中”为当时步骤记录，现由实际回执更新：2026-10-07T23:45:08.376566Z启动验收通过，schema0081未变；[formal-start04/receipt01.json](../../../artifacts/market-risk-implementation-20261007/B2/formal-start04/receipt01.json) SHA6db5ecb9e431784b89f233ba5587b03b7600c15a8d7877d5cf4578b8c7d37e9d。supervisor7944、launch20261007T234300694Z、MCP28276；API8047/DSH3097/采集8038 health200 ready，7业务服务online。该回执report_acceptance=false。

Root显式reload并核04入口index-CFkREcAf.js，各通过页面保存1个新请求：BTC riskresearch_062e09b50ba24d39897be29054ff0aab543c5d26、ETH riskresearch_757ea5f50e72c56dd891577581abb38ea1939962。future-pending显示请求已保存、北京时间最早2026-10-08 07:49:18/07:48:11、按当次检查约4/3分钟等待及届时仍须核验；30分钟准入规则、按钮与请求幂等未改。

F03-02标准编译/真实PC通过；Root已view_image确认[BTC等待截图](../../../artifacts/mr-pc04/btc-request-wait01.png)与[ETH等待截图](../../../artifacts/mr-pc04/eth-request-wait01.png)无溢出。F03-01的5项控制/04安装通过，真实新MCP目录、session、质量及完整报告仍待；完整PC矩阵和产品交付也未通过。此文档追加不重跑构建或调用UI/API，见[04有限文档回执](../../../artifacts/market-risk-implementation-20261007/B2/docs-update04/UPDATE04.md)。

04后续真实目录门通过：正常MCP scoped catalog以唯一合法header GET HTTP200，[catalog-read01](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/catalog-read01.json)SHA24da02e4ffb7160f6c929ef95df61826c24e35efda05e7b2e7c23dc5511d164c；ETH request→run_e6545db744fd46538ec870808461cd73→真实live DSH session/methodhash绑定，原409配置阻断已消除。BTC request→run_747187cd527140938901f70a01f25479等研究lane。request-003 SHA107fdfeb4cd11e6523eb40e57b71c3ee499481e9bd75f6972f70ce5d8874cea2及Core首读两path原hash核对通过；尚无终态，完整报告仍待，不把目录通过当报告通过。

## 04终态核对与05当前执行顺序

上面04等待/运行/lane语句为当时历史快照，已由[terminal-reconciliation02](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/terminal-reconciliation02/receipt02.json)补充：BTC/ETH均degraded/research_only，各9个完成工具，quality qualification=false；原观察器假设RunCall与不存在DTO字段导致两身份误判，原件保留，另版身份核对通过不等于业务通过。

05候选源已具名冻结，F04-01–05详见[修复任务](04_REAL_ACCEPTANCE_FIXES.md)；Root binding/view/sidecar实际SHA分别02aa48a8…f2a9195 / d09e160c…fc338 / b2cfb876…40d33。缓存4unit控制只验作用范围/失效记账，真实性能未通过；Host实际04候选认证通过，fidelity仍false；private历史桥4P不代替正式旧报告读取。

待执行门依次：核候选/保留04全部原件与182源闭包→实际空闲再标准stopguard/backup/apply→一次标准05 UI/Host构建和受管启动→读取旧04 BTC/ETH报告/质量/证据/完整下载→页面各一次05新请求及真实同资产/两venue DSH报告→[10项有限PC](../../../artifacts/market-risk-implementation-20261007/B4/remaining-pc04-review01.md)。正式当前仍04，无新构建/启动回执；合同新增ETH枚举不预设DB迁移，实际schema门以标准工具为准。

每步保存实际版本、命令结果、原件/截图、失败与修复；未变已P不重复。全产品/金融效果仍false，28日自然样本与外部来源资格独立。本轮只更新文档，见[同步证据](../../../artifacts/market-risk-implementation-20261007/B2/docs-update05/UPDATE01.md)。

05发布准备实证：正式04全部3585 source+3669 artifact=7254文件逐SHA保存于artifacts/mr4-src05；B2/release-preservation05.json SHA b89040a29cb7c2306abccfc45d01e2d599fc7be504616874149b07278f0d7400。原V3捕获artifacts/mr-v3-retention-05/prepare01.json实际236 files成功。标准builder遗漏新registry的候选修复仍进行，回执大小临界需实测；这两项准备通过不代表05构建/安装/报告已通过。

## 05失败 / 06构建通过但启动失败 / 07容量修复

- 05真实回执估计2182439字节超过2MiB，UI/Host尝试均0。06改为可还原每个source_before SHA的delta编码，保持原2MiB及标准源码守卫；实际回执1373618字节，标准UI/Host各1次退出0，SHA `cdd006b030ab6b34ebd37d0dca06556f392a7c694e24f40e592df48ec9ae1199`。旧05失败未覆盖。
- **F06-01：06实际API启动失败**。launch `20261008T010901436Z`，`assessment_basis_source_limit`：评估器扫描含历史归档的4113文件，原数量上限4096；实际81508288字节未超128MiB。不是SDK离线。原stderr和失败launch保留。
- 07容量修复：唯一生产源 `packages/evals/assessment_basis.py`，SHA `53d174cd…cece96d3`→`5a7b2e19…ae5a2d3b`，MAX_SOURCE_FILES=8192；不删除历史、不跳过SHA，16MiB单文件/128MiB总量保留。实际4113/边界8192接受、8193拒绝、同大小篡改改变manifest均通过私控；没有正式分析/模型/金融资格。
- 标准停止通过后，06全部8209源/产物按原SHA保存 `artifacts/mr6-src07`，只晋升一个源码并有原字节备份。[实际晋升](../../../artifacts/market-risk-implementation-20261007/B2/assessment-capacity-fix07/promotion07.json)、[有限控制](../../../artifacts/market-risk-implementation-20261007/B2/assessment-capacity-fix07/control01.json)。07标准构建进行中；ready、旧04回读、新BTC/ETH报告及PC是剩余门。
- 历史归档全量扫描的容量增长仍是维护约束：本修复只消除已发生的4113数量阻断，未来达到字节/数量门须明确失败；后续按稳定依赖集合优化，不能通过忽略原件或自动无限放宽门处理。


## 正式07实测与下一增量（当前覆盖历史状态）

07已正常运行：标准构建回执SHA `f4dfd067…6791d02e`，7服务在线，schema0081未变。[启动原件](../../../artifacts/market-risk-implementation-20261007/B2/formal-start07/receipt01.json)。[PC动作](../../../artifacts/mr-pc07/actions01.json)已有9组，涵盖MR19–21/24–27/31/32的受影响真实页面；不等于全矩阵通过。

F07-01旧04质量409/32.765秒；只读桥候选4P（22.469/13.391秒、原决策逐字一致），正式HTTP待下一发行。F07-02 ETH报告BTC标题/记忆内部ID、F07-03 ETH日线其他数据集显示均已DEV修，正式PC待回归。新的provider/ReviewV2闭环仍有最后正文保护有限控制和真实模型门；MR15–19/27/32仍待实际新报告，不标全产品完成。

| 下一增量 | 明确实现/已有证据 | 必须收口的门 |
|---|---|---|
| 每次模型输入及准确输出绑定 | 7 DTO、Hub受认证写读、Host真实generate-options与ToolCallID；三个source-seal具名保留 | 初始正文/根复核正文保护最后控制，标准安装及真实DSH |
| 独立复核V2 | 17源封签；跨语言11、TS gate4、真实私库写读1、旧V1 mapper1P；首身份哈希RED保留 | Root质量接线及正式真实审查，不制造缺失费用/金融资格 |
| 质量及上下文 | 41纯映射回归+6新增纯控；report影响独立于monitor，静态manifest和已验证动态正文分开；旧wire保留 | 真实provider正文证据、同原报告源hash以及当前Host终态 |
| 旧报告读取和PC | storage-only组合桥一次完整原Graph、原publication/政策原件重算；实际07报告200；9组PC实操有截图 | 08实际冷HTTP、下载及3显示问题PC回归 |

上述私库/纯控制与正式/金融资格分别记。最新范围及下一步见[一页纸](CURRENT_FOCUS.md)。


## 15b本轮实操补记

[唯一六 GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh15b/execution01/receipt.json)已一次执行，总体F、source稳定；BTC无报告、ETH仅degraded材料且context404。[PC15 五组/45 原件](../../../artifacts/mr-pc15/actions01.json)实际通过失败原因、保留证据/正文、原附件SHA、返回和事件类别筛选；综合因果论据与时间窗口输出缺失，完整报告仍F。

[15b实际验收与下一批核心修复](15B_REAL_ACCEPTANCE_AND_NEXT_FIX.md)给出四项具体候选、原件边界、正负控制和合批后同Run验收顺序。旧失败不重试，条件实际改变后再用普通按钮创建新研究。
