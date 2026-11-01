# 原系统同Run前置PAPER性能诊断

> **最新实际结果（Q033，2026-10-24；优先于下文历史WIP）：** 本地源码提交`e6ab691`及Windows生命周期局部验证完成，三源受控接回Main但未部署。新65pin原sameRun唯一运行仍在原5秒门失败：**6.0007603999983985s，pytest1failed73.69s/exit1**，尚未到研究/native/报告。三源暂保已审本地候选，不作为性能采用/成功；Main三源strict1129→1130及旧Q03119项类型债务均保留。[本轮完整边界](D:/DecisionHub-legacy-incremental/docs/v3/reports/INCREMENTAL_MANUAL_SOURCE_LEASE.md) / [独立实际收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/transport-runtime-review.json)。以下旧状态/下一步按历史原文保留；本页末节给出最新闭合，目标ACTIVE。


## 任务信息

- 任务：Q-027原系统渐进交付的性能微切片，不冒认T-803或实际PAPER验收。
- 执行者：Codex根智能体；独立实现和只读复核见具名收据。
- 日期：2026-10-24，时间输出为本机记录，不作真实UTC健康认证。
- 状态：Q031单RO聚合已受控接Main，原sameRun仍5.6261252秒失败5秒门；旧回归12PASS/2fixture FAIL，Q032有界修复已决定。详见INCREMENTAL_MANUAL_READ_SNAPSHOT.md。
- 前一目标轮分类：PROGRESS。原plugin51文件/556项、完整102入口strict均通过并本地提交，随后现成同Run测试产生了改变下一动作的真实失败证据。

## 执行摘要

保留原界面和现成业务测试，没有另建研究主线。原同Run用例在前置受控PAPER fill返回后触发原5秒墙钟门，尚未到研究POST/native/Graph/报告。首次实测54.865721秒的原因未定，因此本轮只增加透明诊断，不改业务时钟、时限、原参数/返回、权限或任何断言。诊断确认来源目录重复清点与临时观测都是热点；SQL不是当前几十秒的主要证据。

本轮没有启动真实Host、付费Provider或正式PAPER，未改原服务8047/3097和正式数据库。长期六组合/14天/四周等验收保留，目标ACTIVE。

## 交付物清单

- 临时新诊断包：`D:/DecisionHub-v3-development/temp/legacy-reuse/same-run-diagnostic/`。原7文件复制后只增加观察者；原Isolation、Node守卫、native config逐字不变。8文件scope SHA `0bc08becf0d41b0d6b2cec4619a8e263dd9852e117c9de0f14778db8804a5be0`，49项来源pin执行前匹配。
- 新`diagnostics.py`190行：同步透明包装真实`PerpetualManualPaperService.execute_intent`，只在调用线程的最外窗口计数，cProfile与统计在sessionfinish输出，恢复原class method。
- 独立D-only探针：`evidence/same-run-diagnostic-probe/`，合同RED5失败/0错误，最终5通过/0错误；真实合成class的同self/参数/checkpoint、返回对象、原异常对象和失败会话恢复均核验。无业务导入或DB。
- 实际诊断原件：`D:/DecisionHub-v3-development/evidence/legacy-same-run/diagnostic-20261024T0416-root/`，保留原失败log、pstats、统计、来源读集和退出结果。
- 单点缓存资格诊断：`evidence/legacy-sdk-review/inventory-exit-diagnostic/result.json`。只抽取已pin原函数AST执行一次有界枚举，非live memo或业务测试。
- 原当前recipe模块逐字基线到隔离D，提交`ae7bfaa`：`packages/kernel/decision_hub_kernel/application/research_graph_recipe.py`，原Main SHA `721e2d8d79560a6f43b554710dedee01569c0ddf5bc92363b85ea708b275bc87`，864行。D此前不存在此模块；其余Python仍旧基点，不冒称完整生产克隆。`manifest-memo-repair/baseline.json`保存该事实及原件。

上述相对evidence路径均位于`D:/DecisionHub-v3-development/`。

## 验收命令执行结果

```text
<Main .venv>/python.exe -B <same-run-diagnostic>/run.py --execute
  --source-root E:/project/study/codex/dsh/decision-hub
  --candidate-root D:/DecisionHub-legacy-incremental
  --sdk-root <existing pinned SDK> --node <existing pinned Node>
  --work-root <legacy-same-run>/diagnostic-20261024T0416-root

MANUAL_EXECUTE_WALL elapsed_seconds=78.28000489999249 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 204.17s (0:03:24)
actual exit 1; guard denied=[]; native children=[]; research HTTP receipts=[]
```

这次是明确有观测开销的归因诊断，无论结果是否GREEN都不算5秒时效验收。原5秒helper SHA与两次执行前来源一致。49个pin执行后当前仍匹配；1774文件/323253141B完整进程读集与0357逐项相同，guard finish无源变错误。未伪造缺失的`after.json`。

| 具名窗口的实际统计 | 次数 | 时间/体积 | 含义 |
|---|---:|---:|---|
| collector writer窗口 | 1 | 78.2799933秒 | 与原helper计时边界略有不同，均包含观测 |
| 最外audit合计 | 425572 | 42.9926543秒 | 以下audit分类和path/read嵌套，不可相加 |
| audit os.scandir | 240587 | 35.8316332秒 | 临时观测的显著贡献 |
| audit open | 20343 | 6.995342秒 | 全部open，不全是Main源码读取 |
| 完成并验证的Main读取 | 20064，652路径 | 394708798字节 | 包含重复读取，只计该窗口 |
| `_original_inventory` | 22 | profile累计50.0210814秒 | 包含原枚举与其观测；不是额外50秒 |
| manifest builder | 5 | profile累计11.0004917秒 | 每次都重新构建 |

`nt.scandir`原profile caller进一步区分：完整inventory219032次，即22×9956目录；纯memo资格枚举20130次，即5×4026；其他bounded865与pathlib560。窗口中`market_component_versions/`的完成源码hash读取路径/字节为0，不能再把全进程323MB的大JSON读集当该writer的根因。profile显示SQL执行、query/row digest为毫秒量级，当前不支持归因为几十秒SQL成本。

原pstats SHA `629de2ee`前缀，完整值见独立结果收据；统计JSON SHA `dc223824`前缀。审查者已逐项比较marshal原统计与JSON top100，不能以cProfile缺少audit Python帧断言没有观测开销。

独立单函数AST诊断保原4096/65536/cycle分支：当前`directories=4026`、`pending=70`、`entries_seen=45204`，返回None；目录与pending合计4096，符合原pending cap出口。return trace行414属于with清理，不伪称直接观测到421。原函数源前后SHA相同。它解释当前树为何不能获得memo资格，不支持提高金融时限或删历史目录。

## 已知问题 / 偏离任务卡

- 同Run未通过，未到native/Graph/保存与重开。filled只是受控返回状态，不代表整链或实际成交合格。
- 原helper同时使用每次调用递增1ms的合成业务clock；因此filled和真实78秒不矛盾，不据此认定生产放过真实过期行情。
- path26.595265秒、read6.860107秒与audit/profile时间互含；不能相加，也不能用78减audit估计无观测时通过5秒。
- 诊断会增加开销；不能把本次78秒外推为旧54秒的精确分摊，亦不作真实延迟统计样本。
- 模块原864行属于当前原件基线，尚未大拆旧模块；新增观察者190行，最大函数21行。仅临时AST检查与具名探针，未冒称全v3 Ruff/Pyright本轮重验。
- D-only源码复制第一次因目标原先不存在而在复制前停止；空目录证据保留，无产品修改或提交。随后明确记录缺失再保全Main原字节，不从旧D模块猜基线。

## 技术决策记录

1. 不裁剪完整`_original_inventory`、不缓存权限/Run/SQL结果、不使用TTL或stat代替源码鉴真。
2. 当前最小产品修复范围仅纯manifest memo：完整复核六个initial roots、所有import候选（含不存在的高优先级候选）、路径解析、原文、schema、runtime和helper身份。不同输入必须失效并回原builder，canonical manifest与原builder等价。
3. 并行只做临时守卫的等价包含判断优化，保每次resolve、junction/正式数据/写入拒绝及每次Main原文hash；不据此替代真实产品修复或承诺5秒。
4. 既有callback source lease只能pin一次，selection extension/sealed与publication source scope各有强绑定，不能直接套进manual多RO和writer来省校验。

## 测试覆盖说明

诊断探针覆盖正常/异常/嵌套、同对象和原方法恢复；不证明业务性能。真实用例完整保留并失败。下一纯memo回归必须先RED大树重复构建，再验证优先候选新增、别名重定向、同名原文覆写、schema/runtime/helper漂移失效，不能只测一份复制算法。

## Git与独立复核

- 已提交`ae7bfaa`只保全原模块基线，不是性能修复。
- 诊断包非作者复核：`evidence/legacy-sdk-review/same-run-diagnostic-peer.json`，C0/I0。
- 实际诊断结果非作者复核：`same-run-diagnostic-result-peer.json`，SHA `555b204bbb27ff27e19b135c6471e7918b5d1bca7543771af111831ff61f7abe`，C0/I0；不冒性能通过。
- 独立解读：`evidence/same-run-overhead-probe/diagnostic-0416-interpretation.{md,json}`，不把观测与产品累计相加。

## 下一步

完成并复核上述两条最小修复，再按真实源码身份组装原业务验证；未经原5秒门和后续闭环通过，不标同Run已完成。原官方Host接缝与真实长期指标仍继续待验收。

## 已核验修复与本地集成（续记）

纯memo产品修复已提交`f23ef9d`，只包含原recipe模块和新增回归测试两文件。改为逐次核对实际manifest闭包的六个入口、全部解析候选（含缺失及高优先候选）、原文字节、路径身份、schema与runtime。原完整`_original_inventory`及4096/65536/cycle函数AST不变，不缓存权限、Run或数据库结论。入口tuple漂移的cache-hit与构建返回后seed两个独立反例也已复现并修复。

```text
<Main .venv>/python.exe -X utf8 <manifest-memo-repair>/run_tests.py -q
26 passed in 3.01s
ruff: All checks passed!
owned two-file strict: 0 errors, 0 warnings, 0 informations
```

最终模块SHA`5ce02ead1bf1531077383c45b3da33f9e6789368e7ad0175a1330323ad0f9d16`，883行CRLF；测试SHA`8a181ce28730d2c9b8288429fdc5f72268d13072f3d7b481a072f75e7b08937d`，377行LF。owned strict使用逐字候选快照及仅为静态解析定向候选import的测试快照；运行测试实际装载D模块并只读Main依赖，不能冒认完整Main后端回归。最终作者收据`manifest-memo-repair/scope-final.json` SHA`b4116024b4a63afdfcc1728037eade4502e2c6ddb4904d2f8f486d89c27d3c0d`；非作者RO关闭`manifest-memo-peer-final.json` SHA`760faeb2acef0ff4de67e2f0c978552fd6e25abb700bf8bb9f7272ce666f4d56`，C0/I0。

按用户自主裁决与Q-028，已先逐字备份、再核验原文件SHA及8047/3097无监听，只把该已验证模块接回Main本地源码。原文件为未跟踪的当前工作文件，不能从旧Git版本推测它。未改其它Main源码、profile或服务；前后字节、端口快照见`manifest-memo-main-integration/receipt.json`。D仍不冒称完整当前Python克隆。

临时fast-guard保每次resolve和每次Main原文hash，只优化等价Windows包含判断；其27路径/13操作及真实D-only junction拒绝证据通过，不能外推业务性能。随后另建timing-control包，明确取消临时重复文件hash及目录枚举观测；实际file open仍resolve和拒绝Main/data、env和未登记artifact，副作用限制及产品自身原件/完整库存/权限/CAS/5秒保持。目录名可能被枚举，所以弱于strict观察配置；三份输出明确`full_readset_observed=false`和`directory_enumeration_observed=false`。6项D-only合同GREEN和installed四拒绝仅证明具名策略，不是业务或真实数据验收。

timing-control原7文件scope SHA`47ffe443d8c5167610d615e179328e485986fc218f7286725c9573c2468e0040`、probe收据SHA`19e4c641f129d3a84dcbb5cf671a8764da4403563cc34b5938c3377da1468ba0`及原非作者peer均保留。Main集成后只更新49项中的recipe一项，其余48项不变；新inputs SHA`efc256775d4e64b569830344a89dfef338d5a8e36f3462a78caa66f9c4711ac3`，新scope SHA`426cf4f2a6a3d93292f9431ac4f9a02cb2a7acbd054c6c9e5b21815b5239946c`。原peer只证明集成前包，集成后来源另核验。

## 已执行的低观测计时结果（续记）

集成后独立RO收据`manifest-memo-main-integration/transport-readonly-verification.json` SHA`d93437ffe8540f091f42989a029f32e626ab515b7b3f33db02eb825641e87937`，C0/I0：实际Main与D逐字一致，旧scope与备份不变，更新后的7文件及全部49pin实际匹配。只变一个显式来源pin，未冒称核查Main所有脏文件。

随后根仅执行一次既有同Run用例，原件位于`D:/DecisionHub-v3-development/evidence/legacy-same-run/timing-control-20261024T0456-root/`：

```text
<Main .venv>/python.exe -B <same-run-timing-control>/run.py --execute
  --source-root E:/project/study/codex/dsh/decision-hub
  --candidate-root D:/DecisionHub-legacy-incremental
  --sdk-root <pinned SDK> --node <pinned Node>
  --work-root <legacy-same-run>/timing-control-20261024T0456-root

MANUAL_EXECUTE_WALL elapsed_seconds=21.52716650000366 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 88.94s (0:01:28)
actual exit 1; source_pins_unchanged=true; denied=[]; children=[]; HTTP=[]
```

原门槛继续失败，未到研究/native/报告。`preflight.json`与`after.json`均实际存在并相等；所有仓库模块来源仍为Main。`native_validation_not_reached`仅说明非零pytest导致后置native检查跳过；此例无实际child另由observations的空children确认，不由该flag推断。进程文件读取集为空是显式关闭临时全读集观测的结果，不表示产品未读取或全部运行闭包不变。

这个21.53秒和前两次54.87/78.28秒使用不同源码和临时观测配置，不能把差额全归因于memo，不能当作真实PAPER性能统计。剩余阻塞已实际确认；下一步低开销profile只定位剩余产品热点，不作为5秒合格结果。没有同配置盲重跑，也没有修改原helper、clock或断言。

## 剩余热点的实际profile（续记）

根随后单次执行新`same-run-control-diagnostic`包，原control的Isolation逐字不变，仅挂原字节`WriterDiagnostic`透明包装真实unbound方法；没有实例化带逐audit计时的DiagnosticIsolation。8文件scope SHA`f22c694c8e9f9a0a4cf8fb69824b0ff76d5b1c8a00e2ad00524cccf27f5db020`；保原49来源身份、更新两个装配pin并加collector第50项；非作者RO关闭。原件位于`D:/DecisionHub-v3-development/evidence/legacy-same-run/control-diagnostic-20261024T0504-root/`。

```text
<Main .venv>/python.exe -B <same-run-control-diagnostic>/run.py --execute
  <同上固定source/candidate/SDK/Node，新独立work-root>
MANUAL_EXECUTE_WALL elapsed_seconds=35.99690810000175 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 102.75s (0:01:42)
actual exit 1; 50pin preflight==after; denied=[]; children=[]; HTTP=[]
```

窗口35.9968951秒、进程CPU35.5秒；pstats SHA`e83a6759836037b52c4d2e2e6bae5ab9b558935bd83005208a28827990dc9f6c`。完整1618个函数记录中builder确无entry，而manifest入口5次、指纹10次；结合真实命中分支，说明本窗口5次warm memo实际命中，不只是在top100里未找到builder。冷启动填充不在此结论范围。

剩余具名累计热点：完整库存22次/15.6185731秒；manifest入口5次/13.0540446秒，其中指纹10次/13.0434591秒及来源解析27310次/9.2177053秒；splitext2376178次/4.5492267秒与库存互含。SQLcontext/querydigest/rowdigest约0.0195/0.0286/0.0223秒。这些累计时间互含且有profile开销，不能相加、不能从35.99秒扣差推算21.53秒或5秒。临时audit/path/read计数和hash体积本次均未采集，collector空字段不表示无IO；原collector中关于独立audit计时的文字属于本次未启用能力。

结果独立RO短收据`D:/DecisionHub-v3-development/evidence/same-run-control-diagnostic/profile-0504-readonly.json` SHA`9edd2cd2049abbb778f6f268eb8c1daa71171b1c45b80ee0129037940fa567ec`，核原pstats、top100、原失败和50pin前后相等；未打开正式DB。

基于上述真实热点，下一微切片Q-029仅优化完整库存循环的无关后缀处理，完整遍历/原检查点不变；Q-030仅优化来源路径的纯词法运算，解析和每次fresh resolve不变。D此前没有binding模块，已从当前Main逐字保全3039行/128404B/SHA`2844facd75ddc73b05bac680b6fd65af1d7b190b731d6f307ce00e0bd00e94ca`，单独基线提交`74ed38b`。这不是产品修复，也不说明其余Python已同步。

## Host装配的独立进展（续记）

原ScenarioRuntime接缝已定位。D插件现有node_modules会把Cordis解析到Main npm版本，而官方CLI使用锁定SDK vendor，故没有复用该依赖目录。已审`host-candidate-staging/stage.mjs`后，根实际执行prepare、阅读计划，再用精确plan SHA`170b269187ab2471d13cd921f9cd779975307113103630fe410530fa866dfc05`执行stage。仅新UUID`67ebed59-6400-40dd-81b2-f2839f83356e`内物理复制15个已封lib和原package/patch，建7个新SDK junction；没有替换或删除现有链接、安装或重建。

实际收据状态`STAGED_RESOLVER_CHECKED_NO_HOST`：四个公共入口及各SDK external解析同一锁定SDK，tools/subagent/replay的Cordis亦落同一vendor入口。原件在`D:/DecisionHub-v3-development/temp/legacy-reuse/host-candidate-staging/staged/67ebed59-6400-40dd-81b2-f2839f83356e/staging-receipt.json`。这些只证明实际文件与resolver装配，未evaluate插件/SDK、未调用apply或启动Host/DB/网络，不替代admission/retained/Provider或真实研究验收。原Main profile和服务仍未切换。

## 两项等价循环修复的完成与接回（续记）

Q-029已提交`3621347`。只改完整库存循环：继续原顺序调用目录/文件元数据，匹配候选仍用原splitext处理点名，只减少不可能后缀的分拆和无关文件词法路径拼接。新增10项真实D模块测试通过0.07秒，原函数oracle AST逐项一致；整树保留目录、新增/删除/改名、真实硬链接/junction、元数据和scandir原异常均覆盖。owned函数及测试strict/Ruff0。整binding模块比较时baseline/candidate均20条strict，固定cwd的8条E501同原；外部备份另有位置相关I001，不冒称修复或整模块0。只有该28行函数AST改变，其余模块AST原样。非作者RO收据`complete-inventory-peer-final.json` SHA`9eea7b410d832f0337c766f14f3c7fff46e7423ec50fa05a3bc5e532b439e2f6`，C0/I0。

Q-030已提交`3b07b97`。只改纯词法相对路径及其loaded-helper身份绑定，保持原`_module_file`和每次`resolve(strict=True)`，不加路径/文件缓存。Windows原生绝对路径使用lower组件语义；外来类型、方法漂移或不适合输入回原relative_to/as_posix。根审发现is_relative_to动态替换的具体反例，真实RED后补原方法/代码绑定再GREEN。原26项memo+新增38项共64项通过2.66秒；Ruff/strict0。最终仅tuple纯布局调整后全模块AST与已测代码相等，未冒称重新运行了该格式字节。非作者新改动RO收据`source-path-peer-final.json` SHA`d63d1fc7028a234b3da7f2257e35859cf885fc3a90b5d378919d8bf8e952df3b`，C0/I0。

两份已核验源在逐字备份、旧SHA及8047/3097无监听前置核对后接回Main本地：binding `2844facd…e94ca`→`8352ed0c1495de4bf14a77080ee5bf80d87bb17d7d0d9838b1ef1bf91132f591`（3044行LF）；recipe `5ce02ead…0f9d16`→`1641b1ca003f919a6223aca88b4fa78a4b7b636aba24b02ef14a59d68a662091`（931行CRLF）。原件及端口快照见`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/performance-loop-main-integration/receipt.json`。未启动服务、未改Main其他源码或正式数据库。

新`same-run-loop-control`仅复制原已核验7文件control装配，不改其实际代码，49来源pin中只更新这两个Main源，其余47项原样；完整读集/目录观测仍明确false。原clock、5秒、原件、完整库存、SQL/身份/权限/CAS不变。根只启动新attempt `loop-control-20261024T0534-root`，其最终结论必须待实际终态，不以局部74项测试替代。

后续业务方案已只读核清：`manual-single-ro-bundle/decision.md`建议用现成prepare_originals的一次真实RO事务收联合entry/account/observations和实际检查的quote前缀，并保真issued parent/选中quote hash与仓库前后验证；报价读取无网络，但Advice.current目前另开session，不能嵌套包装后冒充同一快照。尚未实施/测试，若本轮仍失败将优先验证此业务方案，不继续搭全局文件缓存或抬时限。

## 组合实测失败、撤回及业务决策（最终续记）

上述唯一无profile用例已终态，原件`D:/DecisionHub-v3-development/evidence/legacy-same-run/loop-control-20261024T0534-root/`：

```text
<Main .venv>/python.exe -B <same-run-loop-control>/run.py --execute
  <原source/candidate/SDK/Node，work-root=loop-control-20261024T0534-root>
MANUAL_EXECUTE_WALL elapsed_seconds=27.232045099997777 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 94.43s (0:01:34)
actual exit 1; source_pins_unchanged=true; denied=[]; children=[]; HTTP=[]
```

49个显式pin执行前后相同，after/result/observations均已落盘。新运行仍未进入研究/native/报告。该配置没有profiler；临时file-open策略仍有开销，未观测完整读集和目录，不是纯产品基准或真实金融延迟样本。与21.53秒的单样本比较不足以推断两项改动各自的因果性能，但当前结果不能支持采用它们来解决5秒门。

因此根自主决定撤回这两项Main集成：核对实际新SHA、逐字备份及8047/3097无监听后，Main binding恢复`2844facd75ddc73b05bac680b6fd65af1d7b190b731d6f307ce00e0bd00e94ca`，recipe恢复`5ce02ead1bf1531077383c45b3da33f9e6789368e7ad0175a1330323ad0f9d16`。原源码备份和`rollback-receipt.json`在`performance-loop-main-integration/`。D的`3621347`/`3b07b97`保留为未采用的隔离实验，不改Git历史、不删除失败材料。没有再跑回退后业务，不据此声称已恢复21.53秒或通过5秒。

Q-031已明确允许路径、金融/签发/时点和必要负例。当前Main manual/advice/paper三份原件已逐字保全到D，基线提交`73c544d`，不是实现：SHA分别`8fb0b14b…13717`、`875f78f3…b6fff`、`8e278bdc…805ec`。C实现一次真实RO聚合与session helpers，B只读审真issued parent/quote成员边界；不改原original_preparation和repository SQL/CAS/既有5秒断言，不跑真实模型或正式库。尤其禁止仅凭“任意真parent+裸book”签发，必须证明选中quote确由受限真实reader在同session读取并收集，再绑定其身份及hash。当前尚无Q031 GREEN或Main集成声明。

最终决策保持原产品路线：保原界面，问题逐个复现、原代码修复和实测；文档作为需求/验收清单，不能把全部重写或55个状态当作可用交付。真实六组合/14天/四周/实际PAPER等目标仍未达，目标ACTIVE。下一步优先完成Q031真实负例与原5秒链，再接已经完成resolver staging的官方Host，而非继续堆叠局部路径优化。


## Q031实际结果续接

单RO六文件提交71225da并精确接Main；38单元GREEN/真实Main新helper strict0/六文件Ruff0不替代整链。原12定义展开14项为12PASS2fixture FAIL260.55s；唯一新63pin无profile sameRun仍5.626125200011302s失败原5s门（pytest1failed74.78s），63pin前后同、denied/child/HTTP空，未到研究/native/报告。Q031原件、非作者收据、19项新增/变形静态提示及当前Main四SHA详见INCREMENTAL_MANUAL_READ_SNAPSHOT.md。Q032仅修fixture真实Task/catalog；不盲重跑或降低门槛。


Q032后续：仅旧Advice fixture材料接线修复4d9966e并精确集成测试，定向2PASS79.77s；不是整批14或5s通过。旧63pin历史化，不重跑。下步按Q033现成default full-pin接线TDD/生命周期验证；真实长期目标ACTIVE。


## Q033默认full-pin接线与唯一实际失败（最新）

单parent接线本地提交`e6ab691`，只改manual/helper/paper三生产源，精确接Main本地工作副本；未部署原profile/服务/正式库。默认完整pin显式传给唯一preparer，scope跨writer物理COMMIT/rollback/ACK未知退出，原terminal/idempotence、entry时钟、SQL/CAS/原件hash及5秒门保留；None保原2args，不使用defer/extend/seal或权限缓存。

局部RED9FAIL/3PASS→12PASS；新15＋原38共53PASS42.59s，测试辅助拆分后仅新15再PASS6.80s。真实Windows测试仅小D私有源/HANDLE/临时SQLite＋合成no-fill业务，不替代完整Main/native/实际PAPER。新test/support2文件strict0；虚拟Main filename＋Main配置五文件Ruff0，裸D新两测试仍2I001（生产三源0），[上下文已封存](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/ruff-context-addendum.json)。Main真实同配置三源strict1129→1130，唯一新增是旧checkpoint在新lease调用点暴露Unknown，暂不改冻结源；旧Q03119项未清，不能称whole0/新增0。

```text
attempt: D:/DH-q033-same-run-20261024T0727
MANUAL_EXECUTE_WALL elapsed_seconds=6.0007603999983985 status=filled
assert elapsed < 5  # 原门保持
1 failed in 73.69s (0:01:13)
actual exit_code=1; denied=[]; children=[]; HTTP=[]
```

[preflight](D:/DH-q033-same-run-20261024T0727/preflight.json)与[after](D:/DH-q033-same-run-20261024T0727/after.json)逐字同，含65个显式来源pin；453个已报告模块origin均在Main。guard只观测1个D临时SQLite目标、5个临时loopback事件（3bind/2connect）；`full_readset_observed=false / directory_enumeration_observed=false`，空读集不是零I/O。原失败位于normal_queue_lifecycle前置manual fill之后的helper:254，外层test:51因此中止，研究/native/报告未执行；没有真实Host/Provider/实际金融成交。[stdout](D:/DH-q033-same-run-20261024T0727/pytest.stdout.log)、[result](D:/DH-q033-same-run-20261024T0727/result.json)、[observations](D:/DH-q033-same-run-20261024T0727/observations.json)、[独立RO runtime review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/transport-runtime-review.json)（SHA`2cb8ed2c…e0c`）均保留。

5.6261252s与6.0007604s都是不同运行的失败单样本，不能据差额归因或宣称性能采用；filled不代表时效或金融合格。三源保为已审本地候选而非性能交付，旧63pin包保持历史、不改写重跑。官方Host独立原入口临时执行器另行准备，本次无Host成功结果；后续实测须根另定有证据的动作，不盲重试。真实长期验收未达，项目目标ACTIVE。
