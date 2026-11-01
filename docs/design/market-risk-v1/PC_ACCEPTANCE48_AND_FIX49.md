# PC48实测缺陷与核心修复计划

更新：2026-10-10。49必要修复已经一次标准构建并正常安装；下表原48失败保留，49实际结果和新发现追加在末节。工程门与产品门分别记录，未宣称完整交付。原八层33项的完整实现方案、指标及验收在[技术设计](TECHNICAL_DESIGN.md)、[验收指标](ACCEPTANCE_AND_METRICS.md)和[原清单](IMPLEMENTATION_CHECKLIST.md)，本页维护本次最短核心闭环。

## 1. 有证据的缺陷与实现方案

| 编号/优先级 | 实际问题与证据 | 具体实现位置/方法 | 通过门 |
|---|---|---|---|
| C49-01/P0 | 新BTC第三次_admit首次BEGIN出现SQLITE_BUSY，未捕获，claim残留60秒后才失败；无Run | `risk_research_admission.py::_admit/process_due`：仅识别尚未首读/业务写入的原生SQLITE_BUSY和精确BEGIN IMMEDIATE，给封闭writer_busy原因并走原failure CAS；其它业务SQL/COMMIT/IO不吞并 | 私库真实锁控：不创建Run/事件；释放后按原次数/退避成功；并发旧generation不改新owner；业务/COMMIT未知原样保留。正式新合法意图最终完成或明确可定位原因 |
| C49-02/P0 | 旧39正常worker通用失败；RO制作通过，正式保存未验证 | `research_publication_jobs48.py`、lifecycle/runtime：有限phase与私有安全异常回执，区分produce、verify、material、bundle、prepared CAS、独立readback、confirm CAS；持久未知提交继续fenced。依据同原publication/generation/state版本设计正常显式受管恢复 | 一组控制覆盖制作/保存失败、旧owner、重复指令、取消、未知COMMIT；未知不重投。正常旧39同原字节保存、独立ACK后再验GET≤2秒、下载和完整审计；不直接SQL重置 |
| C49-03/P0 | 旧47 Host500与真实两投影槽满，48新请求尚未进DSH | 按[DSH可靠性方案](RESEARCH_RUNTIME_RELIABILITY_PLAN.md)做封闭阶段诊断、只接受原生明确not-found；保留原首错与终错、两槽/预算/完整输入守卫 | 有限定向控制后，一次正常真实研究从提交ACK到终态/报告；输入原30秒与writer≤.5秒；未知活动不放行新生产 |
| C49-04/P1 | report unconfirmed通用失败页面仍说稍后读取，暗示后台继续工作；followup原错误缺中文 | `ResearchQuality.tsx`、中文错误映射：queued/prepared/已停止/提交未知分别解释；完整原件不确认时不开放下载或研究资格，不显示内部属性替代说明 | 实际错误页面能说明“后台整理失败/未知提交”，保留原记录；只提供真正可执行且有回执的恢复入口；未知码不猜原因 |
| C49-05/P1 | 首页本机验证短期失效，流程复杂 | 先记录同一授权的失效/返回过程，优化恢复后的同资产返回及解释；不扩大权限或TTL，不把验证当授权 | 原身份过期立即隐藏数值；一次恢复返回原资产；无新增grant、无权限扩大；正式有权限与无权限分别反馈 |
| C49-06/P0验证 | 48独立小时调度实际UTC04双资产按时通过，真实AI共存仍未证 | 保持`history_cadence48.py`原UTC自然机会/115秒批次/90秒动作/60秒原分类；只读一次新机会全原生与phase，不回填03 | BTC/ETH下一实际小时原时限内完整保存/独立确认；研究/报告工作不阻塞原监测；迟到/缺失真实计入原分母 |
| C49-07/P0 | 49旧39正常恢复后仍失败，produce→frozen resource stage：目标路径260字符；对应受信源存在且全字节符合49声明 | `research_publication_resource_stage.py::__enter__`：缩短独占stage前缀，先核完整已捕获资源的路径上界；保留目录/链接/硬链、源身份、原字节与资源上限 | 实际相同logical_ref与最长资源正常创建、完整读回与清理；链接/改字节/超限仍拒绝。一次必要正常版本修复后，同原job最终保存/ACK及读取通过；不重抓历史新闻 |
| C49-08/P0环境 | 49首次Ready F，analytical_assessments原生SQLITE_FULL；当时C盘剩余0，系统TEMP在C盘 | 仅本项目启动进程TMP/TEMP指向E盘普通目录，委托原受保护标准启动；详见[运行方法](OPERATIONS_TEMP_STORAGE.md) | 新Ready实际P已取得；研究、报告最终写入仍需验收。原失败保留，不删除用户文件、不改永久环境或权限，不宣称原事务已回滚 |
| C49-09/P0验证 | UTC05双资产原槽和独立ACK按时，但后续原publication机会pending/attempts0，next_due05:05已到 | 核实际受管publisher是否运行、当前正常阶段/心跳/错误与认领条件；按实际首因定向修复 | 原机会最终分类由原forecast/ACK判断，延迟和缺失保留；不能因slot ACK通过直接标完整发布通过，不回填或改分母 |
| C49-10/P0 | 05:35研究Worker退出：报告stage错误后，fail_claim首BEGIN锁竞争异常未处理，监督程序重启一次 | `research_publication_bundle_lifecycle.py::_attempt`仅精确原生首BEGIN SQLITE_BUSY分类；`research_publication_jobs48.py::process_one`有限处理可重试分支，不创建循环或新模型调用；其它业务SQL/COMMIT保持原行为 | 私库真实首BEGIN锁控制不退出Worker、未写Job或解除未知COMMIT；释放锁后原正常流程通过；业务SQL及未知COMMIT门原样保留。正常发布后同原报告最终保存/ACK，监督重启不算成功 |

首次BEGIN锁失败的时间线：03:39:31.148 UTC claim；03:39:47.153 UTC durable_admission BEGIN忙；03:40:47.177 UTC最终lease过期。第一次业务写入前获取锁失败可明确“尚未写入”；业务写入后或COMMIT错误不能沿用这个判断。持锁写者当前未知，不能用加长lease/降低新鲜度门伪解决。

报告时间线：03:33:35.306 UTC正常enqueue；03:34:50.343 UTC unconfirmed/version4；reason为`publication_bundle_preparation_failed`。正常Worker catch覆盖producer和save_verified；一次纯只读制作106.875秒通过只排除稳定制作故障，不证明正式保存或ACK，也不允许把其临时产物直接发行。

## 2. 固定推进顺序和停止条件

1. 两条P0修复并行，先满足源封存与最少有意义控制；Root负责一次组合晋升/标准构建，不重跑未变矩阵。
2. 正常停机时确认实际在途任务与未知状态，不强清Link、取消或修改历史失败。保持旧版本原件/source归属守卫及正式发布门。
3. 正常启动后验真实主线：首页→新合法研究意图→真实DSH终态→完整同原报告→正常下载/核对→监測共存。原失败三次结束不自动第四次。
4. 每个失败记录实际输入、版本、阶段、原错误、耗时、结果、截图及必要修复；源码控制P、实际P/F、未执行、外部待资格分开。不能以日志多/构建多替代用户结果。
5. 真实主线通过后补回剩余PC按钮与原高级主线，并更新手册；手机仍排除。统计有效性与黄金/宏观供应商资格按原清单判断；不重训冻结模型、不延期原28日、不宣称预测优势。

## 3. 当前实际证据

[PC步骤/截图/原件索引](../../../artifacts/mr-pc48/pc-acceptance48_01.json) · [48 Ready](../../../artifacts/market-risk-implementation-20261007/B2/release48/ready48_01.json) · [正式迁移](../../../artifacts/mr-schema-publication48-apply02/receipt.json) · [报告单次RO诊断](../../../artifacts/market-risk-implementation-20261007/B2/release48/old39-prepare-diagnostic48_01/receipt.private.json)。源码候选/控制与正式49结果在产生后追加具名证据，不能提前改成已完成。

## 4. 49候选与发布准备

11个生产文件和1个UI测试已由Root组合封存；保持原源码守卫、预算、时限和授权。报告恢复25项、首页授权恢复2项与UI错误提示4项控制通过；R2末版的lineage fence与4种已知HTTP错误分支也有定向控制。均为源码或隔离证据，不代表真实产品已恢复。

两次组合工具的singleton-list形状错误仅发生在artifact读取阶段，未改Root产品源或DB；F已保留，修正读取结构后组合封存通过。正常49发布前只做一次停机后原生selection/coverage；若没有新增安装身份，复用原10成员capture-set，不重复capture或0091迁移。

[组合封存](../../../artifacts/market-risk-implementation-20261007/B2/release49/root-source-seal49_01.json)。R1/R3及真实主线、完整PC按钮仍为待完成项。

## 5. 正式49实际结果与下一步

- 一次标准UI/Host构建通过，沿用0091及原授权；首次Ready因SQLITE_FULL失败，项目E盘临时目录后新Ready通过，两个原件都保留。
- 恢复现有本机身份后返回BTC，原失效提示清除、当前小时记录可读；没有扩权或延长TTL。
- 只提交一次新的BTC意图，进入`run_4061aaa1cd134013abfc60f7f6c698de`。真实DSH于05:29:50 UTC完成，Run于05:33:41保存degraded报告；PC已见14证据、8来源、10次调用、反方及审查。12输入/结束回执及绑定通过；usage为partial，完整报告及费用/子会话/drain资格仍待。
- 旧39正常恢复后仍unconfirmed/gen3/v7、Saved0/ACK0；新私有诊断与原FailureEvent哈希相符，定位C49-07，正在准备最小源码修复。没有再次重试或伪确认。
- UTC05 BTC/ETH原独立ACK分别05:00:06、05:00:14，原生只读核对通过；唯一后续publication快照仍pending，C49-09正在定向核查。新BTC在05:04以后才提交，不能宣称与05小时槽同步。

每个新发现必须先有本表的实现位置、通过门与原证据，再改代码；完成后按同一真实主线复验。当前状态见[一页纸](CURRENT_FOCUS.md)，原33项前五列、机器协议、原48机会和固定28日不改。

### C49-07的50修复（已正式部署，同原ETH实际通过）

仅临时stage固定前缀及对应清理归属检查由`publication-frozen-`缩短为`pf-`，随机独占机制不变。实际旧路径260字符失败复现；新路径244字符、原547资源中最长249字符通过。9项定向控制通过，原报告308477字节和完整哈希保持；资源每file2MiB、总32MiB、180秒租约及来源守卫保持。

Root独立核1260个当前49源码完整文件和候选两处字面量差异，通过并只暂存artifact。两次审查工具对候选JSON形状的错误均发生在源暂存前、产品/DB零写入，失败回执保留，正确解析单成员列表及控制数量后通过。新49实际selected覆盖仍需正常停机后一次核对，不能从旧547资源推断。

[Root独立审查](../../../artifacts/market-risk-implementation-20261007/B2/release50/root-source-review50_03.json) · [PC49实际结果](../../../artifacts/mr-pc49/pc-acceptance49_01.json) · [报告错误页补充](../../../artifacts/mr-pc49/pc-acceptance49_02.json) · [BTC真实终态](../../../artifacts/mr-release49-btc-live-read01/terminal-summary03.json)。当前BTC已自然终态；正常停机前另核在途范围，不把本Run终态当全Host停止。后续完成一次必要正常发布，再走同原报告正常恢复、保存/ACK与用户读取。

### C49-09与C49-10的新增实际证据

05:36:53的唯一02只读诊断通过，两个配置工厂、原协议、独立ACK及两机会全部可读，总7.468秒、原生changes0；两机会仍pending、attempts0/generation0。首份诊断的SQLITE_AUTH是工具误拒只读table_xinfo，F原件保留，不能作为在线首因。需要正常运行时的配置/进入/原last_receipt有限阶段证据，不能由纯只读通过推定正常publisher已经调用。

05:40受管监督程序、10根和31登记子进程的PID与birth全部实际匹配。旧研究Worker实际退出链包含stage创建失败及fail_claim首BEGIN锁竞争，当前Worker重启一次；页面已恢复3/3。完整报告、长期心跳健康及UTC05发布仍未通过。两个源码候选与publisher诊断分别验收，避免把监督重启或泛化异常吞掉当产品修复。

## 6. 正式50实际结果与新增核心修复

50已一次标准构建、原授权读回和正常启动/Ready通过。旧ETH正常Worker于07:35:45 UTCconfirmed/v11；status .036s、quality .525s、download .527s通过；PC真实完整下载308477B与原SHA一致。C49-07及同原报告交付门实际关闭；旧失败不删除。显式audit200、正确性P，但30.541s超过原2s门，性能F保留。

| 编号/优先级 | 原问题与实现位置 | 具体实现/限制 | 通过门 |
|---|---|---|---|
| C50-01/P0 | `research_publication_jobs48.py::_retry_archive_proof49` legacy fallback要求全局归档为空；旧ETH合法保存后，新BTCgen2/v4正常恢复拒绝ownership_unknown | 保留trusted all-generations路径；legacy改为完整归档inventory的逐Run归属证明：每个bundle全字节canonical SHA与原生Saved整行及原16MiB/192MiB材料验证相符；全部材料有明确归属；目标Run任意generation、orphan/未知/部分/链接拒绝；writer仍精确CAS及入口/出口核对 | 双Run“另一Run合法保存、目标尚无归档”可正常恢复；target/orphan/未知提交拒绝；并发变更/旧owner拒绝；Root正式同一BTC经正常Worker Saved+ACK+完整快读和PC原下载 |
| C50-02/P0 | 50真实publisher bound/enter/exit返回`history_outcome_action_requires_readonly_reconciliation`；实际队首仍pending/gen0/attempt0/leaseNULL，认领前原fence和receipt已经存在 | 已封新增typed reconciliation＋正常停机维护CLI。仅对完整证明的认领前未知机会：全部原生行、原件及subject/score围栏末核后，短writer只将state改为unconfirmed；原NULL lease、gen/attempt/error/nonce和原fence/receipt不变。14项隔离控制和独立源码审查P，51正常工厂一次应用、提交及独立读回P。隔离使其他机会可继续，同一机会恢复需要另有合法executor，非本修复已完成 | 矛盾/缺件/已有lease或subject零写；旧pending claim完整行/CAS失配拒绝；隔离原机会仍UNKNOWN/非completed/非qualified；正常Worker推进其他自然机会。不能SQL清围栏、重造旧claim或把私控当实机通过 |
| C50-03/P1 | 旧ETH显式全历史audit正确但30.541s性能F | 核实际完整材料IO/认证/query阶段；若采用持久异步完整audit，必须独立受理回执、完整后台结果和原身份回链，原2s同步门F不改写成P；快读不冒完整审计 | 原全材料认证不减少；PC清楚显示受理/核对中/最终P或F，刷新恢复原键、重复幂等；错误/撤销/源改动拒绝；快读/下载原≤2s继续保持 |
| C50-04/P0主线 | PC旧ETH30m/24h复查实际失败“运行身份核验未完成”，72h仅计划 | 复用durable研究准入与原计划/子Run幂等；原head已核：父子身份和独立spot准入匹配，30m等待超时、24h通道busy，Host终态未确认；52说明实际阶段/原因/回链，不授未知安全重试，不回填原触发或延期原deadline | 原失败保持；新的自然合法复查独立意图→真实DSH→完整报告及父子回链；普通/定时/告警不重复；原时间和研究资格分别展示 |
| C50-05/P1资料与体验 | 正式资料列表直接展示英文“Decision Hub automated acceptance fixture”，未明确测试属性；官方资料原文含大量网页导航，用户默认需阅读770段 | 以可信原件/导入记录确认测试样本身份，明确测试标识和默认筛选，保留原件/审计入口，禁止凭标题自动认定或删数据；官方资料默认提供结构化要点与定位，原英文正文可展开核对 | 正式资料与测试样本可区分；测试材料不能未经明确选择和资格核对进入真实研究依据；默认要点可回到原文位置；原版本/hash与资料主线保持 |
| C50-06/P1待核对 | 同一首页风险摘要1440/1440，而30m曲线显示1段缺口；可能是尚未闭合分钟与实际缺数的边界差异，尚未定位 | 核`RiskTimelineView`及query_views时间网格、closed bar与窗口边界；区分尚未闭合/超出窗口和真实缺失，不能为了图满而补值 | 同一cutoff实际1440路径与图缺数解释一致；未closed不误计已缺失；真实断点不插值、时点/极值/告警保持；用原实际截图和对应闭合分钟验证 |
| C50-07/P0数据含义 | PC打开FRED 10年实际收益率2.87详情，页面“实际观察时间”为2026-10-10 13:21:04；已核验留存原文写明Observations 2026-10-08、Updated Oct 9 2026 3:16 PM CDT。采集时间被当作经济数据所属日期展示 | 核provider解析与fact版本模型的period/published/observed/known字段，保存来源原文的所属日期、发布时间与时区，页面分别展示“数据所属日期/来源发布时间/本次采集/系统已知/版本登记”；历史缺字段明确未知，不重写历史采集为发布时间。PIT使用系统当时已知与当时版本，不能把新抓取视为新经济观测 | 相同原文能验证10月8日所属日期与10月9日发布；10月10日仅为抓取/已知；缺期/缺发布时间明确缺失；修订可回读原版本；不存在未来泄漏或把旧日频值包装成实时新值。留存原文入口已实际P |
| C50-08/P1指标可读性 | PC事实筛选暴露path、realized_vol_30m、return_24h、return_30m等内部属性名，列表数值显示过长小数 | 在事实展示层使用稳定指标目录映射中文名称、单位、窗口和精度；正常视图按金融含义格式化，完整原数值/标识在审计展开保留。未知字段显示可理解的类别与“未定义口径”，不猜测为价格或收益 | PC正常筛选与表格无内部键替代指标名；百分比/收益率/价格单位正确，精度不改变计算/阈值；原数值可审计并与原件一致 |

所有新增项先有具体源码、隔离控制及独立审查，再沿原标准发布一次；不重复未改验收。当前正式51运行，C50-02合法隔离实机P但原机会仍UNKNOWN；C50-01实际误拒因runs列序已定位，52单点候选进行中。完整八层/33项覆盖见[架构执行表](CURRENT_ARCHITECTURE_AND_DELIVERY.md)。

C50-02一次实际完整RO通过，changes0，原件SHA `70af19f246c66ca34e1e8b911107476f82a53f9b7352f35bdd9b57cfc9bd9447`。BTC05原队首为pending/gen0/attempt0/leaseNULL，认领前fence与receipt全字节相同、预期原native hash及source vector匹配；publication、4次observation/独立ACK及subject commit-fence均无。ETH05为pending/gen0，无job fence。该证据只解释队首阻塞，不授权重放；隔离后仍保留UNKNOWN，真正同机会恢复须另有合法executor。[具名RO回执](../../../artifacts/market-risk-implementation-20261007/B2/release50/outcome-head-native50_01/receipt.json)。

## 7. 51实际复验与52可执行修复

- 标准51构建、完整raw/resolved/transport projection、原授权读回、正常启动及Ready P（3健康200/7新鲜在线服务）。
- C50-02正常停机工厂先RO计划，再一次CAS仅改state为unconfirmed；提交及独立读回P，其余原列、原fence/receipt全部保持。后续原机会真实推进仍待验。
- C50-01首次验收脚本cwd错误F未到Job，正确Root cwd后inspect P但eligible=false。一次完整六表/原归档RO诊断P/.828s/changes0：两条归档、已confirmed旧ETH唯一owner、所有字段native类型匹配；仅runs物理列序不同导致jobs51:269误拒。52保字段唯一/完整集合，按列名核原cell类型/nullable，proof和writer/exit仍保原生列序和值。缺/多/重复/变更均拒绝；同一BTC Saved/ACK/完整下载正式待验。
- C50-07已在PC51审计读到指定原版本observation_date/published_at/source_updated_at，无需新表/端点或回填；页面候选与CSV未来freshness规则已有限控制P，正式PC AFTER待验。
- C50-04/08及已批准fetch路径候选已封；私库/类型控制分别记录，不称真实provider/完整资讯链或全PC通过。

[51实际隔离](../../../artifacts/market-risk-implementation-20261007/B2/release51/outcome-quarantine51_01/receipt.json) · [归档首拒原件](../../../artifacts/market-risk-implementation-20261007/B2/release51/archive-diagnosis51_01/receipt.json) · [PC实际日期字段](../../../artifacts/mr-pc51/facts-audit51_01.dom.txt)。

## 8. 52实际结果与下一批必要核心修复

1. 52精确10源组合标准发布P；原65守卫/配置/协议保持，UI/Host各一次构建；正式启动Ready02 P。Ready01在业务入口前F：继承的两个固定原协议在data目录被artifact限定误拒。后继仅允许原path/bytes/SHA，8文件控制及独审P，不扩大业务权限。
2. C50-01归档列序：正式52正常inspect eligible=true，原gen2/v4一次正常CAS→queued/gen3/v5 P。后台claim gen4/v6真实生成F，code=frozen_io_root_data_fallback，phase=produce，诊断SHA a750a3e561374035e3c1765dc64b5501aa2cfdc309f11623a08209f39a7c1054。已确认retained CurrentLoader让当前graph_binding绑定普通normal_history，跳过独占冻结host。不能扩大IO allowlist，须让已完整授权的依赖叶绑定原host；同原报告Saved/ACK/字节仍待。
3. C50-02有限只读见原ETH05正常ready/gen1/on_time；BTC05仍unconfirmed，成熟观察/接受仍待。未重建机会、补预测、延长观察或改旧fence。
4. C50-07真实PC：FRED原版本观测期2026-10-08，页面更新时间、采集、已知/登记各字段可见；return_30m原fraction=0.000300008588955570905457374显示0.03%，正确转换。老记录缺来源观测期或可回读原文继续显示缺失。
5. C50-04真实PC：父页主动核对分别显示30m核验等待超时/曾接收、24h核验暂不可用/接收和结束未确认，原失败时间与计划时间分开；父子跳转/返回保留原报告。子页仍泛称“研究准备”，须更正措辞。Host maybeTerminal已有before_failed_terminal调用；实际缺口是Worker清lease后核验拒绝，未来候选在原错误出口/原租约有效时强核完整authority及同命令native tuple后接既有drain。旧失败不追溯授权。
6. C50-03异步完整audit候选、C50-05精确可信101B测试原件排除与770段原文定位候选在独立DEV实现/控制；未正式安装。候选不得计为真实核验或新研究成功。
7. PC还发现研究列表加载时没有提示、子失败通用阶段措辞不准确；列入剩余核心PC修复。当前曲线缺口/新资讯真实canary/用量/性能/自然自动及复查/全按钮矩阵仍待。

[52启动](../../../artifacts/market-risk-implementation-20261007/B2/release52/ready52_02.json) · [正常BTC重试](../../../artifacts/market-risk-implementation-20261007/B2/release50/btc-retry52_01/receipt.private.json) · [BTC只读进度](../../../artifacts/market-risk-implementation-20261007/B2/release52/btc-progress52_01/receipt.json) · [FRED截图](../../../artifacts/mr-pc52/facts-date52_01.png) · [百分比原值](../../../artifacts/mr-pc52/facts-return-audit52_01.dom.txt) · [复查实际原因](../../../artifacts/mr-pc52/eth-followup-causes52_01.png)。

## 9. 53实际验收及必要54修复

正式53已标准构建/完整读回/START/Ready P；同原BTC正常CAS后produce因完整用量DTO身份F，Saved/ACK仍无。53曲线缺口、默认测试筛选/禁新研究、原文搜索定位749段实际PC P；质量用途矛盾、选中加载及URI显示实际F，54候选未安装。具名动作、截图、正常发布及首因见[53结果](CORE_ACCEPTANCE53_RESULT.md)，接续见[当前任务](CURRENT_FOCUS.md)。上述历史版本的“当前”只描述其当时状态。
