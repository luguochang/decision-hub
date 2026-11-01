# 原 V2 历史实现最小保留计划

状态：D 隔离候选；Root 已授权本切片；Main、installed pointer、原05/07数据库只读。

## 已观测问题与目标

05、07实际选择的原 manifest 均为 `7b7d19e3b08c2939695b9ab2d70b35215651383515cb608f34a1f26b1838921e`。当前 Main 原 SavedReportQuery 使用982 normal，在忠实 restart01 三文件副本上实际报 `research_graph_recipe_current_implementation_unavailable`。51e archive 已注册，但六份源文件与7b7d不同，不能替代。481份真正旧源、manifest和6dd原receipt已逐字鉴真保全；它们不是archive或注册。

本切片只补合法历史实现保留和原报告读取兼容；不重试业务，不改变金融资格、权限或旧Run状态。最终验证目标是原SavedReportQuery在新的私有一致副本读到原07，旧source_json、artifact_json及发布/独审身份与已存原件逐项相同。

## 允许路径和真实来源

- `infra/dsh/prepare-product-retention.py`：D原先缺失，先逐字保存Main SHA `01c02643d59f4641022b55b207d3fd2820142690b0367a08a53234e2719b2ecf`，仅新增显式 completed maintenance prior 分支；旧unstarted分支保留。
- `infra/dsh/build-product.ps1`：Root已明确扩白名单；逐字Main基线SHA `8e97aa83487938a0f6b6369b737115dbccd5f32328098702ba97eaab442dcfd8`，只新增显式Switch和向原prepare CLI转发，默认不变。
- `tests/runtime/test_retained_v2_maintenance_prior.py`：新纯文件测试，实际Main原链正例加完整合成协议负例，不执行服务、模型或SQL业务。
- 本计划及其验证记录。
- 独立私有driver、scope和证据限 `Ddev/temp/research-completion-legacy-retention-*`。如物理原入口需要小helper或独立测试，将先在本文件记录职责和理由。

不stage或commit，直至C释放D index。其他A/B/PC文件不在本切片范围。

## 最小实现

1. 用真正Main代码和原件复现：6dd已完成标准build、build-execution.pointer_writes=0；正式installed仍为49c。原特殊unstarted验证只认另一对producer pins且要求pointer_writes=1，当前合法链被拒。
2. 增加明确opt-in分支，认证现有49c pointer/receipt，6dd原receipt、标准build终态、原retention反向绑定、两代producer绑定、transport与registry包含关系，并在准备前后复核全部witness。只支持已审的producer时代关系，不仅白名单6dd hash或放宽pointer数字。非零process/migration/model、伪build、外来或篡改prior、producer或transport变化均拒绝。
3. 验证分支后，以6dd原sources鉴真的481源、必要真实package initializer和原producer物理文件构造隔离原view；额外文件逐份记录来源，不改manifest。复用原relocated preparation receipt语义，完整sources/artifacts map保持，明确回指原receipt，绝不冒充installed。
4. 仅原V2 producer在旧view上，对真实profile旧→当前这一source change产生7b7d archive；registry合并只用现成冲突拒绝union。保留49c/6dd原registry每行，原producer及bridge重验原件，输出只入fresh私有staging。
5. capture前封存scope/输入SHA；capture后先查原producer输出、481字节和registry完整性。再用真正物理源码的新私有一致副本与原Query验证旧07，不改Main，不使用假__file__、重封native proof或人工registry行。

## TDD与验收限制

先记录真实拒绝和新增正例RED，再做最小代码，运行定向GREEN及原retention相关回归；不跑付费或全产品build。输出范围、来源变更、未完成build、外来prior、pointer篡改、业务副作用及旧分支均须覆盖。必要strict/Ruff按真实物理Main依赖与D候选明确区分。

只有原Query实际成功且与已存07原件一致后，才报告此兼容切片成功；结构测试或archive产物本身不代表研究/金融/产品验收。现存155秒05回放失败始终保留，不改计数。

## 执行记录

- 已保存Main prepare脚本逐字基线，Ddev私有baseline.json记录来源、大小和SHA；未碰index。
- 首轮真实RED 2F/18P，包含真实Main6dd原链拒绝；显式分支后扩到24P。原标准入口转发和真实relocation补测RED 3F；修复后27P/3.32秒/exit0。PowerShell测试只执行原参数声明与参数构造AST，未执行build。
- 新分支以原completed build作为源prior，明确保留49c runtime locator；物理视图relocation复用纯helper，sources/artifacts map不变，原legacy字段不重解释。没有将6dd写成installed。

## 实际终态与交接

原producer只执行一次。冻结scope `7d9ade8496636bf5282f339c5cf5bbed805e159db94a213fe6fbcafbac6b02d9` 后，唯一capture handle30585父driver实际exit1：原物理producer子进程已成功，父driver用普通Path.read_bytes读Windows长路径失败。保留原stdout/stderr及FAIL；随后冻结只校既有产物的continuation scope `39c4e4c0d543f4ebf1d5c8ddf585fa6eaafd0284153a1dd29425f3f05d3ff2c9`，handle36156真实exit0，使用原m.read/io_path逐份验证，未重跑producer。

- 真正7b7d index SHA `25a6b544d9fc892016acb1b5660595aa943a2f230795bdc5736c64d970773420`，原481字节全等；原producer输出483文件。现成冲突拒绝union保留175原注册并新增7b7d，共176，registry SHA `486b535821f733996287e6fed0b42b3a7a0852328d2b973bd250415142edef05`。`capture-original-result.json` SHA `d14786e9c6297a77400d4ccda6a516388cd57850759b653664e761f7a760e9aa`，明确只是文件捕获后验。
- 原物理视图Query独立scope `84a92b339fbd10f639551826697ef0726804c7f2a821903563c1d02b15c6400a`：529份源码均由6dd.sources鉴真，真实__file__指向新物理视图，重算manifest7b7d。handle75716真实exit0，原SavedReportQuery执行1113条BEGIN/SELECT、total_changes=0，来源三文件SHA不变。与原实际200body SHA `e5c0f39af71c7f61df0fac75584b9440eb4469a3ae4c06c1dbd6c4403d96ff17` 的17个共同字段全部相等，仅实际新read_at不作旧时钟相等断言；source_json/artifact_json UTF8字节全等，financial_qualified/execution_authorized仍false。首原视图包漏了parent initializer的隐式import，导入阶段0SQL失败保留；r02补齐真实依赖后成功。
- 新27项测试最终按文件实际运行；曾2F18P与新增入口3F的真实RED保留。相关原4套在物理Main基线39F5P，对候选覆加载加新27为同39F32P，失败nodeid逐字一致、0新增。首因旧V3/capture fixture缺现行codec原文件及两个plan29旧fixture缺现行有限方法声明；未修改旧tests或放宽validator。
- 最后只作Namespace/JSON边界注解、clock字符串类型窄化与换行。新tests strict零错误；原整脚本strict基线716并非零，候选以最终静态记录对照；Ruff基线57项保留，新增行与新tests不留新诊断。错误绝对include曾导致pyright忽略目标并扫描临时视图，记录为无效配置运行，最终使用相对include与显式单文件，不冒其结果为本切片检查。
- 最终新27项：27PASS/3.63秒/exit0；新test单文件strict0、Ruff0。原prepare整脚本strict716→601，规则/消息多集合对照无新增；整脚本Ruff57→57，既有诊断保留，不能称整体strict/Ruff0。

所有运行证据均在 `Ddev/temp/research-completion-legacy-retention-01/`。最终scope补列四文件最终SHA、两个真实Main基线、RED/GREEN/静态日志和三个真实handle终态引用；先前capture输入scope保留为执行时记录，最后注解/测试格式不冒其旧SHA仍是当前文件。

当前只完成D候选、合法原producer捕获和原7b7d视图回读。不能用尚无新archive refs的旧6dd/982 receipt伪造新normal授权。Root已准备Main新artifacts隔离输入root `artifacts/incremental-delivery-20261026/research-completion-prior-6dd`，540份真实旧源/旧输出每份对6dd.sources或artifacts鉴真，48原artifact齐全；证据SHA `dc20e5cdef8a19b5fa156375f0135c828d08982902650356252ed63fdddc8ac4`。

接下来Root经非作者review采用精确源码，沿原build-product显式 `-CompletedMaintenancePrior`、prior6dd和上述OriginalSourceRoot生成真正新normal，保留正式49c locator真实性。新normal必须真正包含7b7d registry，随后实际当前Main/新V6经真正archive分支读取旧07并核原件相等，才释放新研究/运行/部署。该后验尚未完成，本切片不冒当前Main兼容已恢复，也不改变原05失败、07degraded或任何最终金融/14天目标。

Root后续又将同一OriginalSourceRoot补齐两个原retention producer preimage，现542份，全部仍按6dd.sources/artifacts真实SHA；补充证据 `prior-6dd-input-view-with-retention.json` SHA `7260dae649a63f740d8a86a7ce03b0753be8f045dbf38d74c13feef69eaffb43`。本agent不重复造root或执行normal。

最终自查闭合一个显式opt-in边界：若completed flag为true但prior恰是installed本身，原prepare的身份相同短路曾跳过认证。新文件负例真实1FAIL/27 deselected，现仅让opt-in始终进入原认证，由既有hop_identity条件拒同身份，默认分支不变；未执行producer。最终28PASS/3.45秒/exit0，新tests strict/Ruff仍0。旧finalscope保留并由final-r2 scope明确取代；无需重复archive/Query。新增一例只验证该实际绕路，测试文件由398增至418行，保留单一夹具与4文件交付范围，未为行数拆出第二套夹具。
