# 技术债台账：跨层业务阻断与关闭证据

## 2026-10-20 BTC16运行中的明确缺口（未关闭）

- **BTC16保存错误已定位（修复未上线）：** native completed且V4真实approved/finalize后，commit的review reader用通用1MB JSON parser读2.59MB审核原件，触发research_provider_bytes_exceeded。这是parser类别错配，不是模型报告正文/Task token预算超限；既有审核总封套64MiB。ISO修复通过actual reader/严格JSON直接门，完整真实commit副本门未完成，Main失败记录不改。

- **经验跨升级重放缺口：** ISO新paper经验reader重建时读取当前3个源码hash并比较完整body；升级任一文件会使旧经验不可读，而版本键当前仅由outcome+语义METHOD组成，可能同ID冲突。该行为不是获批升级策略；当前片仅证明同代码版本消费。需把语义方法版本与创建时软件审计身份分离、保持历史原件/净额重放和明确迁移，未关闭T06跨版本持久性门。

- **后台统计重复与取消/租约故障：** actual analytical_assessments 90s预算耗188353ms、calibrations 180s耗260013ms。不是已证实的长写锁；多次源码全量扫描缺checkpoint，shield后等待线程、assessment超2min lease和calibration取消未回收可致重做。ISO批准memo+最终fresh drift验证+cooperative deadline+exact owner/attempt CAS retry，未合入Main40，当前整体degraded。不得称所有后台正常。
- **持续循环账户快照耦合：** perpetual_research_binding严格比较settings账户original与当前纸面ledger；settlement改变ledger后旧settings阻止下轮，自动schedule只取现settings，无合法refresh。fresh save可跑下一轮但不能完成自动循环，且watch绑定settings变更需处理。必须设计稳定风险policy与动态账户观察的清晰关系，不放宽hash/ownership校验。
- **真实PC易用性未通过：** BTC16进度详情仍出现英文内部任务标题；详细材料记录为手动读取旧快照，上方基础状态才独立刷新。记录真实截图，Main运行源码冻结，后续按实际可用性修。
- **当前交付事实：** 16次真实受理含1运行，报告0、纸面成交0；event/watch/paper experience只在ISO准备。完整真实报告→建议→PAPER→ETH及全自动循环仍未验收。


## 2026-10-20 当前纠偏：PAPER窗口与发布基线（merge26）

- PAPER入场有效期与持仓截止混用、持仓采样游标超过512代不推进：已按 `PAPER_HORIZON_ALIGNMENT_FIX.md` 完成4后端源+3PC生产源及聚焦测试，后端3P、PC24P后精确修正原失败用例1P、类型检查0；待真正发布及BTC/ETH PC成交结算，不算产品验收关闭。
- 当前manual持仓链没有显式撤回/设置变化自动退出watchdog；不再把该能力误写为已实现。自定义SL/TP/提前退出的独立计划概率仍未建模，参考预测终点概率必须明确适用条件。
- 发布流程债：root把构建完成误当已安装基线。38只build-only，未正常start登记locator；39以未安装38为prior在保留原件阶段3秒失败，UI0/Host0/model0，`retention_maintenance_producer_changed`。该未安装prior的维护分支仍含旧producer硬pin及pointer_writes=1要求，不能靠伪造receipt或改locator绕过。当前补救是精确恢复38字节→标准start38正常登记→正常stop→恢复12修复→新40标准发布，39失败原件保留。不补做业务测试，不声称登记即完整启动验收。
- 根本工程改进待核心通过后处理：发布计划须先核实installed locator及真正适用的prior分支；超大历史版本图导致每次标准构建/启动多轮全量核验，需后续在保持原件可复核的条件下设计增量机制，当前不扩大为性能项目。
- 最新交付状态仍为15个真实准入：12failed+2degraded+1cancelled；真正报告0、纸面成交0。旧BTC11等段落为历史记录，当前恢复入口以D2/merge-26/CURRENT.md为准。

**最新BTC11终态/硬阻：** Root在原progress stop一次并正常确认，Run cancelled/`cancelled_by_owner`、finished2026-10-19T22:41:55.341677Z/artifact=null，sole66885终态22:42:18.257445Z exit0，native cancelled/gen1/seq17898、20gateway completed、38input/37finish；`D2/BTC-11/observation-current.json` SHA=`e43b22e5db5b0b1eed9f0a59b04ae2d24123739bd8a5a201e9524e5f526a4b6d`，`pc-stopped-proof-size-block.png`保留。11accepted=8failed+2degraded+1cancelled+0running，报告0/成交0，cancelled不混failed。

第二prepare成功后首次review16613→16614因`too_big child_events_json`未获有效鉴真wrapper/金融批准，`native-first-review-result.json` SHA=`77426b0f33c1c6716ec032bb3f40eef05bea941460a063ed4e2656fa6600848c`；rootpre已静态确认当前源同prepare已reviewed不可恢复、改draft须有效负审授权、gen2不允许review。完整proof 1MB门TS+Python修复在ISO进行中，Main32/350未动；与已就绪提交恢复必要整合一次发版，完整实际child跨层离线门通过前不新研究。不截原件、不改金融门/研究时限，不扩首prepare提示优化，整项未关闭、目标active。

**当前Main/BTC11状态：** Main32/306controlled/350frozen未动；BTC11现已owner正常取消，11accepted=8failed+2degraded+1cancelled+0running、报告0/成交0。完整proof大小门TS+Python在ISO进行中，将与已就绪提交恢复必要整合一次发版；完整实际child跨层离线门通过前不新研究，未预称修复通过。

**BTC10终态原证据（历史）：** `run_4cabed994c4d487692f32c7740034132`于2026-10-19T21:08:16.987865Z failed/`dsh_host_unavailable`、artifact=null；`D2/BTC-10/observation-current.json` SHA=`d16656d6c60afc1f1bcbd174a9f00a5d697af85cc73d682ff05a0f960cf35d11`于21:08:38.455832Z确认，observer88528已exit0。native failed/generation2/seq16468同码、28input/28finish、Gateway13=12completed+1failed（cross_asset已恢复）。该终态时10accepted=8failed+2degraded，报告0/成交0，终态截图`D2/BTC-10/pc-terminal-failed-31.png`保留。

**最新记录更正：** 安全注记`D2/BTC-09/identity-correction.json` SHA=`1e8cf1968be38576b4da3435bd04dcdbd599a7757ddc91c1d03cbedb8bd522c1`只读DB确认，BTC09 `run_524667d79f784d888932a95d38c7af46`真实accepted_command_key为`8f48cd1d-222b-4e69-9a0d-e067b20cf2c5`；旧run-identity误录`7c459b63-e8e7-4bbc-beb3-ab8e182edb63`实际属BTC08 `run_9eece502010349f6b04974a7195df19b`，抄录来源未知。9accepted的Run/回执request_id/内部PK各9唯一，原回执request_id不同于内部PK；无重投/重复业务写证据，不需再GET/POST。保留旧identity与17attempts，不将记录错误写成产品重复受理。

**31既有启动证据（历史）：** 31正式构建后于2026-10-19T20:31:30.750993Z ready，API200/3新worker全fresh/error=null；`D1/candidate-31-startup-current.json` SHA=`09417703de0dbfaab55ce3df6df14c04d8eb21327ee70f149aceb0df159910d1`，launch20:22:03.9196322Z、observer84851已exit0。现31已正常停，不据旧ready推定32或产品可用。

**当前检查点（2026-10-20北京时间）：既有目标active，Main32/306controlled/350frozen及02方法未动。BTC11 owner正常取消，11accepted=8failed+2degraded+1cancelled+0running，可用V2报告0/paper fills0，不混cancelled与failed。完整proof大小门ISO TS+Python修复进行中，与提交恢复必要整合一次；完整实际child跨层离线门通过前不新研究。旧17attempts/identity及失败原件保持。**

### merge18当前源码与未关闭债务

- **BTC11终态前PC状态抖动待诊断：** sole66885@22:18:17Z native gen1 seq256、model_input19/finish17、13gateway completed；PC同Run sources页16accepted/fresh、6sources、12gateway view、3call failure保留、0review，管理7个实际进程在线，Back报告再Back原progress成功且未提交。badge曾2/3随后3/3，不据此断言离线或已修；`D2/BTC-11/pc-evidence-in-progress.png`、`pc-management-processes.png`留证，完整报告/成交仍0。
- **提交恢复ISO候选就绪，Main未修：** manifest SHA缩写=`60a6cdcc…573ff`，3paths/product仅API，timeout/network仅一次recovery GET/no retry POST，定向17P，Root产品before/after差异已读无阻断；不计Main或实际自动恢复已修。候选留ISO，不因BTC11终态立即重建；若正常发布先以32跑BTC纸面→同账户ETH核心，真实核心后与必要阻断修复整合一次；若BTC11阻断须改源再连同整合。Main350/候选32不变，不复制/运行/扫描或增加任务。

- **BTC11提交确认窗口超时（未修，有界证据）：** `D2/BTC-11/submission-receipt-recovery-diagnosis.json` SHA=`05f17de8ff1256777c5b17fe8abf86d73784a94e04c6e5459622381edf46ef80`：服务器HTTP200/firstbyte31.8904s超过UI默认30s，本command无override；强支持admission窗口超时，但浏览器精确错误分支及内部耗时首因未知。此等待与后台研究deadline独立，不通过缩短研究处理。BTC11同一Run现已owner取消，一次POST+一次原回执GET，无重复；只记录，不改源/运行测试或observer。

- **定向通过不等于全套绿/真实成果：** TS28P、Python19P+negative revision1P、prompt3P；独审SHA=`a88cf4a4e5f6df83357aaec9f33e60ca2b01cece5a7c877079ced181523d1117`、增量SHA=`ca246af4e33ff1509125051290dc736d38709b25fff4c66fa1ecdb9223ed5d61`无P0/P1。旧prompt Main同baseline1F/2E501保留；P2 wrapped-null-source既有行为差异记债，本轮不扩修。
- **审核requiredfields与Host运输分开：** precheck SHA=`25459e0ec16a0dcc726a328948bbad2c31e85ff4aa1af335ba4931336e5c84c0`的snapshot/auth/refs/time真、2issue缺id与prompt漏列已做canonical requiredfields源码修复，不自动补ID/不改negative/schema/方法。最终Host诊断SHA=`f155d13826fb78f49bad1b40966b4bf6aa7db262c29d1a064392dd83bf154fed`只定位gen1 completed、gen2修复accepted后HTTP502无native事件，具体stage未知，单列且不混同已修问题。BTC11须待32 buildpass+正常startup ready后一次原研究。

- **prepare停止修复真实窄验证保留：** `D2/BTC-10/native-prepare-stage-snapshot.json` SHA=`535d44232c48f357297c566417d9d11974961ab56b176191556cca00502ed23d`中typed prepare后真实step/start继续，未重现旧25ms立即abort；该较早快照review/finalize0不代表后续无调用。现BTC10已失败，observer88528完成；进行中PC16accepted/fresh、7source、失败1及Back原progress证据仍有效，不关闭完整产品债。
- **审核协议源码已修、真实成果未验：** `D2/BTC-10/native-review-stage-snapshot.json` SHA=`5d31684782026f92c6bb8968dce23d45b0181e25ab208cae57252c02bd236bac`保留review两次协议错误及金融授权0/finalize0。SDK snapshot第二user投影已随merge18独审合Main；不是金融审核拒绝，也不把最终Host错误预称同因。32 ready、BTC11已恢复accepted/running，真实review/完整报告/成交待。
- **UI结果恢复成功不等于原因已知：** `D2/BTC-11/pc-submission-unconfirmed.png`到`pc-recovered-original-receipt.png`保留一次POST未确认→一次原回执GET accepted、同原Run progress成功；精确原回执RunID join length1，非latest。无重投/closure，首次未确认具体原因另诊断，不能据此猜未受理，也不计报告或审核授权通过。

- **merge17既有证据保留：** manifest SHA=`0f488f35c5e329fe948ad0f363103c786b1628dff0c40aa69b1c76ad5e452087`、merged-source SHA=`adf1be0186231f77403d970d4fc725f10413367492691dbb99bc3bcab29e6151`；prepare独审`D2/merge-17/preparation-review.json` SHA=`afa86c9d45af4e7ea542e220fa853f8bcbe0926bf0c9ee1b0fa13417b753a38b`。5P/11P/6P不等于真实review、报告或成交成功；30失败保留，31构建通过与startup ready仅签原工程范围。
- **旧suite未绿：**baseline17F/76P、after17F/81P，17个失败名字完全相同。本轮不扩修旧fixture；部分`host_hub_http_404`已知缺terminal-quiescence-authority mock，其余502不全归因，不以单一mock问题解释全部失败。
- **环境回归与容量事实分开：**30于2026-10-19T19:14:23.9157579Z失败，UI1/exit1、Host0、retention passed=true；`D1/candidate-30/build-execution.json` SHA=`b670abda935b90f46fbe75a45f70043bb2008e7ca2485a05aa1fd5ee92cea5ed`。Root迁移node_modules引入`ERR_PNPM_UNSAFE_MODULES_DIR`，basic checks未覆盖自动install安全；真实E依赖/正式安装已恢复，31构建通过但不改写30失败。26scope fresh hash/OwnerGroup/AllowSubset/SourceProtected门过：权限相同或更严格，不是raw ACL相同；D3357次Access-only收敛，无SourceACL/owner/SACL/privilege改。DATSO/DATS ERROR5保留，普通DAT+精确.NET Access-only DACL是正确修复，非guard放宽，仅删核验E重复backup。启动前6GiB门实过，但空间增量不可全归因迁移，先前差额未知；24/25 stage2未执行，无core-live DB/source/02/锁改。真实review、sameRun报告+建议、paper、ETH仍未完成。
- **startup观察边界：** 初次旧28心跳和31于20:31:30.750993Z API200/3fresh/null errors的ready均按原时点保留，旧`realtime_channel_failed`不是31错误。BTC10 observer88528已终态exit0，当前健康不重测，不另开observer/POST/构建；ready及prepare继续执行均不计核心产品验收。
- **共享依赖安装状态债（开放，执行约束立即生效）：** Root Get-Item实核ISO `.worktrees/decision-hub-decision-settings-d2/node_modules` junction→Main/node_modules。迁移前Main `.pnpm-workspace-state-v1.json`写ISO4项目而Main实际5，Main自动install后恢复正确Main状态。Main冻结/构建/真实研究期间禁止从此ISO执行会改共享state的`pnpm install/exec`；测试使用既有固定CLI或隔离依赖。根本修复是依赖隔离，后续处理，不在本轮改ISO链接/伪写state/产品源码。

### BTC09终态与历史补采（merge-17合入前记录，整项未关闭）

- **BTC09成功预备被Host误判为最终成功：**`D2/BTC-09/actual-prepare-premature-stop-proof.json` SHA=`c7ebbcbc11369ef18280e412bb3afd98e9673b0f6b1e2e17942834a10fbb7523`确认17897/17:16:42.192Z第4次prepare，17898/17:16:44.681Z完整typed preparation成功；25ms后17900/17:16:44.706Z aborted.user。Root核源确认`hasAcceptedSynthesis`不区分phase触发`bridge.stopAfterAcceptedSynthesis.cancel`，是内部Host终止，不是用户UI取消。reviewer0/finalize0，预备成功不是评审报告。
- **修复状态与未覆盖：**Root ISO正修prepare/final停止条件，不改candidate accepted-only资格。前3次conditioning、quantity、候选引用错误被模型继续修复，第3次错误并非最终阻塞；opaque反馈可用性仍开放且改进未实施。独立fallback DTO/context_manifest候选已GREEN6P但未合并/未上线。candidate29 capture已完成，不计新构建或研究成功。
- **历史补采与PC覆盖：**`D2/BTC-09/history-backfill-result.json`首job partial（ETH110/BTC0 connecttimeout），`history-btc-recovery-result.json`第二job completed/BTC110；`history-backfill-read-verification.json`核两资产真实gateio-public现货日线来源与hash、各110连续已收盘UTC日（2026-07-01至10-18）。纯读adapter结果为本地candidate，未转accepted研究证据，不回填known_at或修改BTC09失败。PC覆盖验收截图`pc-history-eth-original-coverage-28.png`、`pc-history-btc-coverage-28.png`保留。
- **队列自动消费仍未上线：**ISO队列消费+daily370 GREEN尚未合并，真实两job均automatic_queue_consumption=false；不能称候选28已自动补采或以110天现货资料覆盖关闭永续研究/报告/纸面债务。

- **Unicode query hash与prepare反馈：**两修复均已Root独立review后merge-16合入，状态source_fixed_pending_live。manifest SHA=`704ec464d7c557be832057224f93775820aae2e6c7672357e098f125437eb415`，原件`D2/merge-16/manifest.json`及`apply-result.json`；不得以局部门或源码合入关闭产品债务。
- **产品structured_output_invalid已精准定位阶段：**`D2/BTC-08/terminal-failure-diagnosis.json` SHA=`2fa1a6ab377d4b7ee2514d520d5b9c2411ec5d7a3855c56e8f3e325f13ced5d0`确认两次prepare同样缺少own-event conditioningRef，independent review0、finalize0，不能称为审核失败。`run_9eece502010349f6b04974a7195df19b`的native completed/seq10246、35model_input/35model_finish、Gateway6（4completed/2failed）、callbacks0不证明报告、审核或行动发布成功。
- **实际完整草稿回放：**`D2/BTC-08/full-prepare-replay.json` SHA=`3fb84501922c9aefb6e435d4bd6851ca2e245aa0f6ba3f4a721e9dddc981ecf8`使用两份完整actual draft、实际绑定及18原件，仅测试副本补missing ownref后，两份完整prepare/fullV2 semantics/review packet均通过；不是fixture，也未完成真实独立review/发布，不关闭产品债务。28构建回执`D1/candidate-28/product-validated-builds.json` SHA=`948d328fb75e7f41b0d20245c930d6b92424229610b0c75a74e6523373b19eda`，构建时间16:11:24.3276825Z→16:29:02.8395140Z；当前已进入BTC09实际验证。
- **P2中文错误映射缺失（未修UX）：**Root同Run完整失败页仍显示“原因尚无中文说明，请展开原始任务记录核对”；材料6、tool6，返回按已保存设置研究链接正确，截图`D2/BTC-08/pc-failure-reason-27.png`。`structured_output_invalid`尚无对应中文说明，通用失败页不算核心验收成功；本次只登记，不改UI源码。
- **证据与边界：**BTC08/09真实终态均保留，候选28既有启动ready记录不改写为新健康测评。构建前仅NTFS压缩本目标既有证据目录，以满足既有5GiB启动门，未删/移文件，不计性能任务或验收。真实独立review、sameRun报告+建议、paper及同账户ETH均未完成；本次只更新文档，296/340冻结输入不再变更。

**2026-10-18执行状态：用户已批准review-5并启动实施。当前D1及证据见[实施记录](EXECUTION_LOG.md)和[执行合同](execution-contract.json)；下文2026-10-17的暂停描述属于设计审查时点，不代表当前目标暂停。**

日期：2026-10-17。状态：**静态审查与设计修订；实施暂停。** 本轮只读必要源码、原件和设计并修改文档，没有运行产品、研究、测试、构建、安装或提交。总控与唯一实施顺序分别为[00](00_MASTER_CONTROL.md)和[05](05_IMPLEMENTATION_PLAN.md)；架构边界见[01](01_CAPABILITY_TRACEABILITY.md)/[07](07_ARCHITECTURE_REPAIR_BLUEPRINT.md)，对抗反例归[11](11_END_TO_END_DESIGN_CONTRACT.md)。本台账不创建新目标、不另起工单系统、不删除F/MR/G/E/V/I或历史分母。

## 2026-10-18 D1实施增量（整项均未关闭）

### BTC-05 实测补充（2026-10-18 13:10 UTC）

- **TD-02/14：基础采集与研究可恢复性未贯通。** 13:08:09调用可见分钟线年龄189秒，超过180秒门；此前采集ReadTimeout，下一批8.7秒后才入库。拒绝过期数据本身正确。baseline按代码应汇总真实失败给模型，但本次HTTP503在模型前终止，底层异常未记录；不能把503单归因过期或推定锁故障。真实临时SQLite两distinct请求并发反例已取得单次RED：完整认证在BEGIN IMMEDIATE之后，第二请求实际SQLITE_BUSY。该反例证明机制，不能冒充取得本次503原异常栈。修复方向为锁外完整认证、writer精确复核与新鲜权限，不放宽freshness、不回填未来材料、不全局串行模型。证据：`D1/BTC-05/stale-source-review.md`、`parallel-admission-red.txt`、`terminal-cause-metadata.json`。
- **TD-01/12：PC失败说明丢失。** 完整失败页显示英文系统兜底和“执行原因：失败”，未优先解释结构化`research_baseline_http_refused`。窄修复复用现有中文映射，未知原因不猜SDK/网络/数据过期；cancelled终态优先。原文仍在原始记录，需实际页面回读验证。

| 归属 | 实测问题与用户影响 | 当前修复及证据 | 剩余门 |
|---|---|---|---|
| TD-01／首次模型上下文 | BTC-01真实基线含行情过期错误后，首次上下文漏恢复说明而校验器要求它，1500/2302bytes不一致；模型提交被本地guard拒绝 | `BTC-01/native-context-failure.json`封存真实原件；candidate-03统一共用说明生成函数，认证baseline→SDK事件形态→验证的7项回归先红后绿，独立静态审查通过；BTC-02真实SDK guard通过且Provider HTTP200/首块通过，后续工具交付超时仍使整链失败 | 修复前红/后绿、篡改仍拒绝；新固定候选真实模型→独立审核→报告/Action→PC。此前没有覆盖此Host roundtrip，不能拿旧V1读取用例代替 |
| TD-01／原生工具交付期限 | BTC-02显式65s原生包装先超时；同call后端138s后成功入库，但native只有错误回执，下一pre-step因可信交付差集被拒绝；不是远端模型故障 | 真实首因见`BTC-02/native-idle-first-cause.json`；两路交叉审查后，正补认证catalog原Task deadline与managed工具观察期；官方SDK65s超时→两次压缩→真实guard拒绝已复现RED | managed长工具真实成功回执→下一protected输入确含原件，负控晚成功仍拒；再用新固定候选取得报告/Action/PC整链成功。不得放宽证据guard或禁用压缩 |
| TD-02／启动注册 | 两个研究Worker在已有组件注册的`BEGIN IMMEDIATE`因SQLite争用退出，heartbeat离线；同窗口其他写路径也锁失败 | candidate-02先在独立读取会话认证已有相同版本；缺失仍原写锁内重查。真实竞争测试先红后5 passed，父版本/CAS关联2 passed | 固定候选正常启动及真实研究；全局长锁持有者尚未证明，不能宣称共享争用整体解决 |
| TD-12／请求恢复 | 请求仍为queued、关联Run已failed/cancelled时，PC显示失败却永久禁用更新 | candidate-02按Run终态解锁新命令；pending/claiming/running仍锁定；同文件27 passed | Chrome实际点击、同command回执和新Run最终回读；不等于新current view已实现 |
| TD-02／历史安装全库覆盖 | 可选全库审计发现118条V3选择/75个唯一安装身份中19个未被当前retained registry精确覆盖；不等于原报告数据丢失 | `candidate-04-v3-capture/selected-installation-missing-audit.json`保存真实差集；当前03 exact installation capture已封存。原candidate03正式构建未启用该额外全库审计，root误扩当前前置后已纠偏 | T08按实际需要保留/恢复这些历史安装并分别验收；未覆盖保持明确。不得声称当前候选已签全部历史重建；不扩当前D1工具时钟修复 |
| TD-02／准备成本 | 首次正式构建14分31秒，UI/Host各仅构建一次；source_count=47504。准备栈还显示同一CPI原件在单次validate可能完整HTML解析8次 | 构建和准备栈存于`artifacts/decision-product-core-20261018/D1`；只读定位，不把这次启动失败归因于未经证明的某个长事务 | 仅在直接阻断当前切片时改造；原件身份及PIT校验保留，不加全局认证缓存或降低数据能力 |
| TD-02／BTC04核心准备阻断（当前P0） | candidate05仍在原Task截止11:42:44失败，报告/Action均无；首model-binding RO认证27.486s而post rows0.311s，首技能callback434.23s。单次真实owner栈处于CPI历史解析/情景重建；每row checkpoint另重复DB fence/解码，实际次数未测 | `BTC-04/terminal`、`terminal-cause-metadata.json`、`critical-path-review.md`、`callback-preparation-review.md`与`execute-callback-owner-stack.txt`。post批读仅证明局部边界，不能声称整条准备改善。当前实施RO快照内复用既有事件原件读法、分开cheap abort与必要完整fence；源码改前已严格capture当前05 | 实际skill原准备路径证明缓存仅限物理snapshot、原件篡改仍拒、取消仍不能commit；再取得真实报告/Action/PC。不删历史数据/研究轮次，不放宽Task期限，不以直接单元通过替代用户链 |
| TD-02／Gateway阻塞异步循环 | 当前真实execute路径的临时SQLite直接反例中，同步认证阻止独立async timer运行；基线间隙共373.361s是已测时序，不是已测持锁总时长 | `BTC-04/admission-preparation/red-execution.json`干净预期RED；正将完整同步准入/预留放入同线程拥有会话的工作块，保留adapter原loop与精确取消归属 | timer/另一声明wave可前进；取消、截止、旧幂等行及hash/gen守卫仍正确；不能只包to_thread留下迟到reservation或误取消别人的行 |


## 1. 如何使用和关闭

台账只收录会阻断明确用户流程、造成跨层语义错误或重复工作的16项。既有代码不等于全链已验；候选补丁不等于当前正式部署。债项状态采用四列，允许“已发现、已设计、有部分候选、整项未验证”同时成立。静态事实是发现证据，不能升级成运行验证。

- **P0**：真实主线无法交付，或新增核心需求根本不能由现合同表达。P0必须随第一项相关切片解决，不代表可以扩大实施授权。
- **P1**：会使建议、监控、评价、学习或恢复失真，相关能力开放前必须解决。
- **P2**：可在明确受限能力内延期的扩展债，不成为早期可见建议的前置。

每项关闭须绑定同一候选版本/合同版本、直接场景的输入与失败原件、迁移/回滚回执及05任务记录。依赖项可以先完成其必要子范围；不得因为本台账互相引用而把全仓重构变成一个不可交付的大任务。数值SLA、金融资格、方法有效性分别按02/03验收；本文没有新运行成绩。

路径缩写：`K=packages/kernel/decision_hub_kernel`，`A=K/application`，`D=K/decision`，`P=K/persistence`，`R=packages/runtime_adapters/dsh_runtime`，`H=extensions/dsh/decision-hub/src/host`，`UI=apps/decision-desk/src/product`。位置基于本轮当前工作树；符号是长期定位依据，行号仅辅助。核源排除了`*_versions`、依赖与产物扫描，历史release只读取具名原件。

| ID | 问题与严重度 | 已发现 | 已设计 | 候选实现 | 已验证 |
|---|---|---|---|---|---|
| TD-01 | 运行身份、阶段时钟与首因混合，P0 | 代码＋release32失败原件 | 03/07 | R33有局部修复；整项未完 | R33窄门历史证据；当前正式全链未验 |
| TD-02 | 不可变原件重复认证拖入交互路径，P1 | 代码结构；增量耗时待测 | 03/07 | 未登记整项候选 | 未验证 |
| TD-03 | API/Worker/Kernel逆向耦合，P1 | 静态import和类判断 | 01/03/07 | 未登记 | 未验证 |
| TD-04 | 预算耗尽压掉已有合格成果，P0 | 判定顺序可直接构造 | 02/07/08 | 未登记 | 未验证 |
| TD-05 | 全局一次复核挡住修订重审，P1 | TS gate＋DSH profile | 07/08 | 未登记 | 未验证 |
| TD-06 | 角色、追问与上下文没有完整可恢复闭环，P1 | 现有路线/限额与映射 | 07/08 | 已有部分基础；新合同未登记 | 新合同未验证 |
| TD-07 | 事件调度共享lane，积压/恢复责任不够显式，P1 | 包装顺序；全局饥饿未实测 | 03/07 | 既有独立channel/join保留；新切口未实现 | 新切口未验证 |
| TD-08 | SourceAvailability未把真实覆盖与市场能力闭合，P1 | registry/health/market目录职责分散 | 02/03/08 | 原服务已有；统一合同未登记 | 新市场能力未验证 |
| TD-09 | CPI假设侵入通用事件，事件概率尚未走到资产/行动目标，P1 | CPI专用读法、表约束、显式null与宏观缺口 | 02/03/07/11 | CPI基线已有；family binding与条件传导未登记 | 新传导/其他事件全链未验证 |
| TD-10 | 事件/阶段、三期限及市场的学习选择/资格身份不完整，P1 | 24h硬编码和pointer键 | 02/03/08 | 24h基础已有；新选择集未实现 | 新选择集未验证 |
| TD-11 | 学习DSL能力与宣称的进化范围不一致，P1 | 固定三节点校验 | 08 | 有限候选/消费基础已有 | 历史局部；当前正常闭环未验 |
| TD-12 | 当前建议缺统一适用性/冲突投影，P1 | 多入口及spot/期限过滤 | 03/04 | 新current view未登记 | 未验证 |
| TD-13 | 现货long合同无法表达双向建议，P0 | schema＋policy＋query＋UI | 01/02/03/04/07 | v2未实现 | 未验证 |
| TD-14 | 永续短仓报价/成本/纸面风险缺专用实现，P0 | spot-only quote/accounting | 02/03/07 | perp会计未实现 | 未验证 |
| TD-15 | Run、发布、派送与用户可见仍可能被混为完成，P1 | 完成后hook及独立回执 | 03/04/07 | 既有补偿已有；全状态投影未完 | 历史受控范围；当前整链未验 |
| TD-16 | 事实告警/迟到建议与事前机会分母未统一交代，P1 | fact_change与终态后发布 | 02/03/04 | 原记录已有；全分母/时效投影未完 | 新协议未验证 |

## 2. 具体债项

### TD-01 运行身份、阶段时钟与首因混合（P0）

**事实与位置：**`R/web_runtime.py::DshWebResearchRuntime`经`web_host_client`与TS Host通信；`H/model-dispatch.ts:76–99`已在delegate处启动模型窗口，但`H/provider-input.ts::recordedProviderStream`仍在yield finish前await `saveFinish`，其finally才观察stream_finished。`H/bridge.ts`负责watchdog释放。release32的[BTC_FAILURE_ROOT_CAUSE](../../../artifacts/decision-product-v1/core-main-flow-20261015/release32/BTC_FAILURE_ROOT_CAUSE.md)记录准备约428秒挤占原600秒窗口及首因被次生读取错误遮蔽；R33已有起点/双空分类候选，不能重报为完全未修。

**用户影响与根因：**用户看到模型超时却无法知道是准备慢、Provider慢还是落盘慢；恢复可能重做已结束研究。request/run/generation/session/native终态和业务终态跨进程交错，计时边界与身份所有权没有完整闭合。

**修复边界：**保留现有runtime port和持久身份，按prepare、Provider、receipt、publication分段；Provider finish有证据即停止Provider计时，回执pending保持独立。首因追加保存；恢复使用原request/generation，禁止新建模型调用掩盖落盘失败。**依赖/归属：**T01/T02，随行TD-02/03与03生命周期合同。

**直接验收：**一条真实组件成功、一条Provider原始失败、一条Provider完成后回执超时；三者公开stage/首因正确，第三条恢复不增加Provider调用。**迁移/回滚：**新增阶段投影可回退读取旧状态；不得改写旧native原件或从时间戳猜阶段。候选R33原件单独保留，新adapter回退仍拒绝身份漂移。**状态：**见总表，整项未验证。

### TD-02 不可变原件重复认证拖入交互路径（P1）

**事实与位置：**`A/provider_input_receipts.py::_record_in_source_scope:790–813`每次回调经`prepare_originals`和`_prepare`认证，input/emission/finish均调用这条路径；`apps/hub_api/research_proof_request.py:91`注明轻量检查不替代完整Task/source/PIT/permission proof。`H/provider-input.ts`对emission/finish同步等待。现结构能证明重复准备路径，不能未经分段测量给出加速倍数。

**用户影响与根因：**一次取证可能在后续读回执时反复消耗准备成本，挤压事前发布窗口。不可变内容验证和随时变化的权限/租约检查共用过宽路径。

**修复边界：**按build/closure hash、validator/schema、原件ref/hash与选中输入冻结最小读集；只复用不可变解析/认证。权限、cancel、owner、lease/generation、有效时点仍在副作用前新鲜检查；Windows source lease/句柄不能跨操作证明时仅操作内复用。**依赖/归属：**T01/T02，TD-01；03封存闭包合同。

**直接验收：**同原件重复回调复用内容认证，篡改hash或撤销权限仍拒绝；记录writer_wait/original_read/source_capture/commit及读文件数，证明没有扫描无关历史目录。**迁移/回滚：**先同输入核对新旧认证等价，再启用缓存；不等价即关闭复用退回原路径，已认证回执不授予新授权。**状态：**已发现/已设计，候选未登记、未验证。

**维护边界补充：**仓库`artifacts/`与`packages/kernel/*_versions/`及嵌套版本目录包含大量代码副本；活动代码审查/封存不能默认从仓库根递归发现全部副本，亦不能删除归档来掩盖成本。按03 §10.8使用明确活动根与选中版本闭包，archive按精确ref、索引/分页、引用与保留政策读取。验收增加“活动输入相同、无关归档增加，认证读集不增长”的反例；实际被选择的历史版本仍须认证。磁盘/WAL/文件数增长有owner和指标，超限时停止新增工作并给可恢复原因，不能静默丢失失败原件。

### TD-03 API/Worker/Kernel逆向耦合（P1）

**事实与位置：**`A/action_research_reader.py:221–224`导入DSH mapper及私有`_sdk_run`；`A/market_risk_data.py:17–18`导入OKX和RawBodyArchive；`apps/hub_api/main.py:45,1989`导入Worker装配；`apps/hub_worker/composition.py:866`及`main.py:222`反向导入API bootstrap。composition的846/889/915行按`DshWebResearchRuntime`具体类授予能力。

**用户影响与根因：**改一处runtime或来源要同步改行动/启动层，局部可用难以证明组合可用。配置装配、供应商解析、业务事务三个职责交错；现有execute Protocol不能描述prepared-context/skill-delivery能力。

**修复边界：**中立`packages/pilot_runtime/bootstrap/`共享构造；Kernel `ResearchSourceVerifier/CanonicalDecisionSource`规范来源；adapter封装DSH原件，市场采集/存储由composition注入。保留受控Session与既有事务owner，不作全仓仓储化。**依赖/归属：**T02＋必要T01，后续T03/T04复用。

**直接验收：**API/Worker各自装配同一冻结配置，无互引启动副作用；真实DSH输入的旧/新规范化结果一致；不满足能力的runtime在composition具名拒绝，不到发布才失败。**迁移/回滚：**先搬行为保持签名，再切import；旧runtime/version reader保留；撤回新装配不迁移或重算旧Action。**状态：**已设计，未实现/验证。

**维护边界补充：**03 §10.8固定Session、HTTP客户端、Graph saver、native stream、watchdog和同步线程的创建/关闭owner；多worker不能共享Session或把取消await当writer已停止。07 §2.3规定更换runtime只影响adapter/组合和规范来源，新增事件不改Graph/DSH核心循环。重复出现相同跨层失效时按00停止发布并修订owner/合同，不以继续增加包装层作为关闭证据。

### TD-04 预算耗尽压掉已有合格成果（P0）

**事实与位置：**`D/research_quality.py:319–334`先判`not budget_available`再判`full`；`A/research_quality_projection.py:91–106`对工具、角色、轮次、成本使用`>=`。故刚好最后一次调用得到完整内容时也会进入unresolved/research_only，属于静态可推导逻辑，不是本轮测试结果。

**用户影响与根因：**系统已获得有用研究却不给相应方案，表象像判断不足。继续工作的资源资格与已有成果的内容/真实性资格共用一个状态。

**修复边界：**质量合同分别输出内容判定、发布资格、可否继续；预算只阻止新增工作。取消、截止与真实性仍独立限制发布；未知费用不变成中性市场方向。**依赖/归属：**T04＋T01直接回归，TD-01有效时点和TD-05复核绑定。

**直接验收：**相同完整候选只改变remaining budget至零，内容判断不降级；过截止、取消、无复核仍按各自规则阻止新发布，不能用“内容完整”绕过。**迁移/回滚：**新quality schema/reader并行，旧结果原样读回；关闭新判定只影响新Run，不覆盖已有报告概率。**状态：**已设计，未实现/验证。

### TD-05 全局一次复核挡住修订重审（P1）

**事实与位置：**`extensions/dsh/decision-hub/src/final-review-gate.ts:781–785`要求generation=1并拒绝已花费reviewer attempt；`R/profile.py:352–368`将失败/拒绝/未完成都算一次，修订后只能research-only。已有独立reviewer真实能力需要保留。

**用户影响与根因：**反证推动候选改好后无法得到新独审；若绕过则旧回执可能错误批准新方案。复核次数按整个执行窗口记账，没有按候选hash、attempt、证据集区分。

**修复边界：**候选v1→challenge→补证→v2→独立review-v2；同候选重复回调幂等，新尝试仍受总预算与期限约束。reviewer只读，新增采集交回合法任务。**依赖/归属：**T04，TD-04/06及08角色合同。

**直接验收：**v1被反证拒绝，v2可新审；替换candidate hash、移用旧证据/回执均拒绝；超预算后只保留实际资格。**迁移/回滚：**新版profile与gate同组启用、冻结在新Run；旧Run继续其旧语义；回退新准入不重审/改写旧版本。**状态：**已设计，未实现/验证。

### TD-06 角色、追问与上下文没有完整可恢复闭环（P1）

**事实与位置：**`D/research_loop.py:174–230`将无法映射旧requirement的free-form limitations保留却可能sufficient；`R/profile.py:306,321`已有并行上限和specialist；`A/research_context_selector_v3.py:153–175`裁剪非protected项，protected超限报错。不能据外图只有research_round就说没有多Agent，也不能把全部输入都描述成静默丢弃。

**用户影响与根因：**影响动作的根因问题停在文字里，后轮既不知道缺什么，也可能重复已有研究；必要反证与上下文裁剪的优先级未形成可回读合同。

**修复边界：**08 RolePlan/RoleTask/RoleResult、InquiryProposal与不可变RequirementRevision，以及每轮context manifest；按必要能力启用角色，合法范围内补证，保存选择/遗漏/复用理由。Graph负责续轮，DSH负责轮内执行，不建第二主管。**依赖/归属：**T04，TD-05/08；学习选择按TD-10逐能力启用。

**直接验收：**新问题改变方案时生成具名修订并实际取证；无capability返回具体缺口且不假称解决；恢复复用已完成角色，核心反证可回原件。**迁移/回滚：**原Task/request hash不改，新增revision在后继envelope引用；旧reader继续可读。关闭新角色规划仅拒绝新profile，不把尚未执行任务写为已完成。**状态：**基础已有，新合同未实现/验证。

### TD-07 事件调度共享lane，积压/恢复责任不够显式（P1）

**事实与位置：**`A/scheduler.py:363–433`已有独立channel；`apps/hub_worker/event_scenario_tick.py:14,51`包装risk_enhancement_tick，内部先forecast/lifecycle再await original；同步任务取消时join已存在。这里发现的是共享批次/诊断的结构耦合，没有新证据声称整个系统全局串行或已经发生长期饥饿。

**用户影响与根因：**新研究/历史回填积压时，关键条件多久复查、谁负责过期任务不够明确；配置cadence不等于实际完成间隔。任务种类隐藏在包装函数，有限批次、排队年龄、优先级和恢复身份未统一表达。

**修复边界：**显式登记事件forecast与monitor/outcome工作，保留scheduler/持久claim；设置批次和并发上限、合并键、过期/重试策略、普通队列公平性。保护既有仓位/已发布条件优先；长深研异步继续。**依赖/归属：**T02/T05，TD-01/15；不创建新消息平台。

**直接验收：**慢采集＋SQLite竞争＋重启的一次场景中，快监控持续推进，队列年龄可解释；旧writer未结束不得接替，不出现第二模型调用/重复触发。**迁移/回滚：**先影子登记任务身份，单owner切换lane；回滚必须停新准入并等当前owner退出，不让旧包装与新lane双跑。**状态：**已有恢复基础；新切口未实现/验证。

**多事件补充：**03 EventFamilyBindingV1的occurrence/stage与job identity共同决定到期、重试和停止。FOMC声明完成不能取消发布会阶段；同日CPI/PCE/就业事件不能以日期或family覆盖彼此。业务stage时钟与模型deadline分开，晚到/改期追加原件；过期任务记录missed/unknown并交结果责任，不无限轮询。慢事件不得阻断已发布行动保护；这些反例归T03/T05/TD-09，仍复用既有scheduler。

### TD-08 SourceAvailability未把真实覆盖与市场能力闭合（P1）

**2026-10-21 ETH72h实际补充（未关闭）：**真实独审退回两项：精确1/2/7日收益在选中原件只有20日汇总，无法重算；Gate OI五分钟区间/单位未由选中原件端点和基准证明。原件见`artifacts/decision-product-core-20261018/D2/ETH-72H-01/actual-phase-results-12961-23781.json`，SHA=`325285fa06e4d78d2a4b910cb1fb479b3720aae0530b42275133ea09c18348a6`。负审后prepare成功，但08:18:43Z最终provider_timeout、无报告，未证二审通过。此问题应修来源计算/投影与可恢复修订；不能删除出处要求或把digest数字当原始事实以通过审核。

**同日窄修进度（仅ISO）：**`artifacts/d2-review-evidence-closure/packet-v2.json` SHA=`c60a67593fbe4ef2acd22e23736b55acf77e124f3f362f73787d89af4d3bf4d2`保存两适配器精确源码与7P3.34s直接证据。OI端点/实际窗口/单位和BTC/ETH短窗close-to-close输入现在进入同一EvidenceCandidate；HTX按官方支持60min取数，不冒充五分钟。单次审查发现replay越过请求收盘截止，已用真实adapter RED复现并改为min(now,window_end,cutoff)。尚未上线/真实独审消费，TD08不关闭。

**事实与位置：**`packages/provider_adapters/research/source_registry.py::query_sources:138`过滤license/audit/allow标志；类文档明确registry不fetch、不决定证据质量。`A/source_ingest.py::health_all`与采集回执属于另一来源；`A/market_scope.py`同时有perp研究目录与受限现货准入。源码未显示一份已落地的SourceAvailability可将声明能力、真实时效和目标用途一并冻结。

**用户影响与根因：**“源被允许”容易被解释成“当前有适合此合约/时点的可用数据”；现货价、perp标记价、日频宏观、单点OI可能被错误互替。权限、连接状态、内容覆盖、市场类型和PIT分散，没有按决策用途形成快照。

**修复边界：**03 SourceAvailability按source/capability/instrument/venue/price basis/时间精度/覆盖窗口保存expected、observed、freshness、rights、缺口影响与原件refs；08角色按它规划，不把声明健康当新证据。缺共识不冒充共识，缺OI历史只允许水平描述。**依赖/归属：**T03，支撑T04/T05/TD-14。

**直接验收：**同一授权源返回空数据/过期现货/日频背景时仍分别标明限制；perp mark需求不被spot trade满足，PIT禁止未来修订；缺关键输入只降低受影响用途。**迁移/回滚：**旧source/health数据原样保留，新快照追加；没有可证明known时间的旧数据留unknown；回退新能力只关闭相关目标，不删除原件。**状态：**已有分散基础；新合同未实现/验证。

### TD-09 CPI假设侵入通用事件，事件概率尚未走到资产/行动目标（P1）

**事实与位置：**`A/event_scenario_snapshot.py:86–87`的asset_reaction_probability和strategy_net_profit_probability明确为None；`A/event_macro_context.py:82`保留日频背景与调查共识/事件定价未绑定缺口。现CPI基线回答相对前值的事件结果，不能当资产涨跌或策略胜率。

`ports/event_scenario.py::EventDistributionSpec`只允许cpi/fomc，`FrozenEventScenarioBase`只允许公布前发布；`A/event_lifecycle.py::_realize_due`直接过滤BLS源，`A/event_realization.py`直接使用CPI band。`P/event_scenario_records.py`、`event_realization_records.py`限定非空月度reference_period，日slot不能表达通用阶段。因此其他事件的目录/日历存在不证明完整支持，也不能只给enum加pce/nfp就完成扩展。

**用户影响与根因：**用户可能读到精确事件概率，却得不到为何做多/做空及何时改变。事件结果、市场预期差、资产响应、净行动结果缺少可估计、可检验的目标传导。

**修复边界：**按02分TargetSpec、ProbabilityEstimate/ModelBinding与ActionComparison；透明频率基线保留，稀疏样本显示支持度并给具证研究估计/规则方案。本次02选定的`event_conditional_multinomial_ridge.v1`单独版本化并公开加性/收缩假设，不用基线填空伪称格内有样本；未来候选独立比较。**依赖/归属：**T03/T04，TD-08/13/14，02为数学唯一规范。

沿`packs/crypto_macro`新增版本化EventFamilyPackV1、EventFamilyBindingV1、EventAgendaSnapshotV1，具体字段/port/adapter/表迁移由03 §10.7固定，07 §2.3约束变更范围；不新增插件框架或活动指针。首产品的CPI/PCE/完整就业/FOMC登记、阶段研究、修订、建议、监控与结果，以及02定义的加密原生事件和有界扩展宏观入口在T03–T08内贯通，各自专用统计资格如实标记；T09保留新资产与更多专用模型。共享事实/原件，参数/标签/资格按事件族及阶段、资产/目标/期限/conditioning隔离；多事件agenda使用明确联合/条件语义。

**直接验收：**同事件概率、不同共识/状态或资金费假设，能指出资产/动作排序为何变；仅p_down不足以输出short净盈利概率；缺样本不是默认50%或全体等待。**迁移/回滚：**新target/method hash与旧CPI原件并存，历史不重标；撤回候选方法退回本目标合格baseline或具名缺口。**状态：**事件基线已有，新传导未实现/验证。

补充直接门：CPI旧原件仍能精确读回；PCE不调用CPI专用算法；就业首次发布/修订可区分；FOMC阶段各有时钟且发布会未到不假称完成；两个重叠事件不覆盖目标、不直接相乘概率。新family binding/表索引迁移只能从可证明原件映射，未知保留unknown，旧hash不改；注册与结构校验本身不授金融资格。关闭须记录各族支持范围，不能用一个CPI成功结清整项。

### TD-10 事件/阶段、三期限及市场的学习选择/资格身份不完整（P1）

**事实与位置：**`A/research_skill_selection.py:197–207`用run/role建key、body固定24h；`research_skill_trial_selection.py:135–136,217–218`固定24h；`component_registry.py:236,290`pointer按kind/component_ref/scope_id。`D/action_research_policy.py:78`仍是30m/24h/72h。仅增加4h枚举不足以改变这些消费者。

**用户影响与根因：**三期限页面可能共享不适用的方法或回滚互相覆盖；现货经验也可能错误获得永续资格。目标实例、稳定目标family、运行选择和发布pointer的粒度不一致。

**修复边界：**08 SelectionSet按scope、target family、horizon冻结成员；family包含市场/品种/场所/价格基准等定义，排除本次事件日期；本次完整target hash另绑结果。4h/24h/72h分别选择/消费/资格/回滚。研究可先使用各自合法baseline，自动学习逐期限开放。**依赖/归属：**T06＋T04消费者＋T03目标；TD-09/13。

**直接验收：**4h晋升/回滚不改变24h/72h；spot包不能凭资产名授权perp；一个成员实际使用只计该成员consumed，同次采集可以共享原件。**迁移/回滚：**保留旧24h selection reader，新增schema；回滚只移动后继Run的对应pointer，不热换在途Run、不覆盖历史选择。**状态：**24h基础已有；新选择集未实现/验证。

family/stage/conditioning也是资格身份的一部分：CPI经验不能靠共同“宏观”标签自动准入FOMC，事前目标不能借用post_release结果。共同事实、特征实现和经验可检索，但参数共享须由具名方法显式声明并评价；family pack变化生成新binding，后继Run使用新版本，在途选择不热换。该范围归原T03/T06，不新增学习平台。

### TD-11 学习DSL能力与宣称的进化范围不一致（P1）

**事实与位置：**`A/research_learning_packages.py::validate_supported_workflow_layout:10–42`只允许spot_evidence/features/hypothesis三节点，主要可改spot_evidence literal输入。已有learning queue、trial、use/consumption及W7历史局部证据，不证明任意新节点或当前正常完整学习链。

**用户影响与根因：**反思写出“以后检查OI变化”却没有历史OI采集/执行节点，下一次仍做不了；下载或读取经验容易冒充行为改变。模型候选表达范围与真正执行器能力未逐类对应。

**修复边界：**首条经验在已支持literal修改内真实改变一次合法采集，再由后继Run实际引用。新检索/因果/上下文/模型节点需要ComponentChangeProposal与具名开发任务，不能靠SKILL文字自动增加能力。运行错误与金融错误分别归因。**依赖/归属：**T06，TD-06/08/10；08唯一学习规范。

**直接验收：**同一缺证据问题产生非seed候选，下次执行确实改变查询、取得证据或正确缺口，并进入机制/候选；无新节点能力时明确拒绝。consumed只证明使用，金融改进另对照。**迁移/回滚：**候选不可变、trial受限、旧parent可读；回退未来selection/pointer，不删失败单位或改原结果。**状态：**历史局部，当前正常全链和扩展类型未验证。

### TD-12 当前建议缺统一适用性/冲突投影（P1）

**事实与位置：**`UI/ActionPlanPanel.tsx`、EventPrevisionPanel、EventLifecyclePanel为独立读取入口；`packages/query_views/decision_product.py:70–74`只允许spot scope与旧期限。当前04规划的DecisionUiCurrentViewV3尚未实现。多个页面存在本身不是bug，缺少统一主建议适用性才是本项。

**用户影响与根因：**较新的事件预案可能盖过仍有效Action；或已被反证的旧建议继续占首位；切换到空头账户后仍显示现货多头分支。来源时间、业务有效性和账户适用性没有单一确定性读取规则。

**修复边界：**03/04当前视图只读已持久化facts/heads/conflicts，按instrument、venue、account/risk snapshot、target、horizon过滤，再按发布/冲突规则选主项；GET不启动研究，UI不自判机制冲突。未知仓位展示各分支与限制；paper账户和用户声明账户具有不同身份。settings_changed使旧比较stale并阻止新增暴露，不撤销已触发episode的保护监控和退出责任。**依赖/归属：**T07＋T05冲突事实＋T04发布，TD-13/15。

**直接验收：**有效Action＋更新事件预案＋真实反证能按规则变更主项；现货旧long不得填perp short空缺，4h缺项不得拿24h补；旧版仍能读回。**迁移/回滚：**新view并行旧detail接口；关闭聚合首页不改head或冲突事实；客户端版本不支持时明确提示，不能默认wait。**状态：**已设计，未实现/验证。

### TD-13 现货long合同无法表达双向建议（P0）

**事实与位置：**`contracts/schemas/decision_product_v1.schema.yaml:1097–1113`、`D/action_policy.py:257`只支持enter/reduce/exit_long、hold、wait及flat/long/unknown；`UI/actionPresentation.ts:3`及ActionPlanPanel:21同义。BTC perp研究目录不能证明short建议、short仓位管理已经实现。

**用户影响与根因：**新增明确需求“多空双向事前建议”会被旧schema拒绝，或被迫退化成看跌却只能等待/卖出现货。动作方向、仓位状态与市场类型在现货设计中被隐含合并。

**修复边界：**新`decision_product_v2.schema.yaml`的ActionCandidateV2用`action=open/add/reduce/close/conditional/no_change`与`target_side=flat/long/short`；不扩写v1语义。InstrumentContractV1、AccountSnapshotV2、PositionContextV2、UtilityPolicyV2及设置revision同绑。建议首版OKX BTC/ETH USDT线性永续、单向净仓；反向先平再准入。保留独立现货。**依赖/归属：**T03/T04/T05/T07/T08；TD-08/12/14；F33追加、不替代F15。

**直接验收：**flat可比较long/short/no新增风险，已有short可减/平/持有；未知账户不能编数量；复核candidate hash、monitor版本、UI合同一致。只改提示词或标签失败。**迁移/回滚：**v1读回不变，新scope/profile显式准入；不将旧sell解释为空头开仓；退回旧版本前停止新perp意向并处理在途保护。**状态：**已设计，未实现/验证。

**执行工具同属迁移：**`tools/acceptance/validate_market_risk_plan.py:14,33–34`固定旧目录与spot，`tools/context/build_task_context.py:28–35`固定contracts.v1/ADR-0001并排除DSH/Pi内部；旧工具会误拒本次授权范围内的v2/perp/runtime改动。按05在获准首切片中修改原工具读取唯一当前`execution-contract.json`的合同版本、真实refs和增量paths，旧机器状态仅保留历史。直接门为旧合同仍可核查、新合同明确分派、缺批准引用拒绝启动；不把更新设计文档当自动批准，也不建立另一套当前目标。

### TD-14 永续短仓报价/成本/纸面风险缺专用实现（P0）

**事实与位置：**`ports/action_quote.py:30–32,219`只接受spot_scope/SPOT；`D/action_paper_accounting.py:22–26`只有两个spot scope，Account数量非负，`size_entry:134`要求多头止损低于ask，`sell:193`限制不超过持有量；`A/action_execution.py:174`还原spot_scope。现有会计没有宣称处理永续保证金，不能通过传负数量绕过。

**用户影响与根因：**有正确看跌方向也可能无法给可执行纸面方案；若复用现货收益，会遗漏资金费/强平和空头报价方向，净盈利标签失真。品种特有风险被埋在现货算术和scope parser里。

**修复边界：**03拟`perpetual_accounting`纯计算层，接独立InstrumentContract、资金费、mark/index、盘口、保证金模式/杠杆/维持保证金与强平规则版本。卖出开空用bid、买入平空用ask；费用/资金费符号与结算时点独立。研究允许缺口，纸面执行/净收益资格不允许伪造缺项。**依赖/归属：**T03来源＋T04计算/协议＋T05监控＋T08直接场景，TD-08/13。

**直接验收：**一条short获利路径、一条short止损路径、一条跨资金费结算/强平或缺mark路径；验证gross/net分离、资金费符号、保证金与不可评价原因。无需真实下单授权。**迁移/回滚：**perp独立账本协议与episode，旧spot记录不迁移解释；撤回新准入仍完成已有episode保护/结算，无法可靠处理时冻结并保留未结算原因，不删仓伪平。**状态：**未实现/验证；第一纸面切片开放前必解。

### TD-15 Run、发布、派送与用户可见仍可能被混为完成（P1）

**事实与位置：**`apps/hub_worker/research.py:1587–1589`先_record_completed再_publish_actions_safely；`A/action_research_integration.py:542`已有reconcile_missed_hooks。UI ActionPlanPanel:134明确display/ack不代表成交。独立事务与补偿本身合理，不能误写成需要一大事务；待补的是跨层公开状态与恢复证明。

**用户影响与根因：**研究completed可能还没有Action，通知sent可能用户尚未看到；若界面只给一个成功标记会误导，也会促使用户重复发起研究。

**修复边界：**03按报告/Action publication pending/published/blocked/failed独立投影，保留原hook identity幂等；outbox/provider accepted/inbox available/display/ack分开，重复派送用dedupe key处理。重放原发布不重做模型。**依赖/归属：**T02/T05/T07/T08，TD-01/07/12。

**直接验收：**Run提交后杀进程，恢复只补一次Action/outbox；再在发送后回执前中断，不能重复展示或声称用户已看；离线PC显示unobserved而非虚假达标。**迁移/回滚：**新投影可回退，业务不可变原件、hook失败与通知重试记录保留；不把旧sent记录补成display。**状态：**已有补偿与历史局部门；当前整链未验证。

### TD-16 事实告警/迟到建议与事前机会分母未统一交代（P1）

**事实与位置：**01 §1A.4核到risk alert明确`alarm_kind=fact_change/prediction_hit=false`，`apps/hub_worker/research.py:1589`在终态后发布Action；`D/action_research_policy.py`已有固定target。本轮02将普通市场改为publication_bound、事件目标仍按预登记窗口；任一目标的数学评分都不能自动证明此前风险已覆盖，也不能把错过的机会排除。

**用户影响与根因：**系统事后解释正确，却让用户误以为提前预测；只统计成功发出的方案会掩盖等待、超时和未形成建议。事件时点、候选cutoff、实际发布/展示、评分起点与原机会登记缺少同一产品读法。

**修复边界：**沿原机会登记记录全部候选/无建议/迟到/失效/等待，单列发布提前量、首次可见等待、漏报；固定目标结果与服务时效两账关联。快监控执行已发布条件，深研更新后继版，不能事后回填published。**依赖/归属：**T03/T04/T05/T07/T08，02评价与TD-15时间回执。

**直接验收：**同一事件包括准时建议、迟到建议、失败无建议、用户离线四例；全分母不漏，迟到不能算事前命中，离线展示延迟不混入在线SLA。**迁移/回滚：**新协议追加版本，不移动旧窗口/删除旧失败；旧缺display/known时间保持unknown。**状态：**已有相关记录；新全分母与时效投影未验证。

多事件与多阶段分母也按原occurrence/stage/目标登记；漏掉FOMC发布会或重叠就业事件不能因CPI主卡已成功而消失。预案、后继阶段修订、迟到实际值和结果沿同一agenda/binding引用解释，stage更名/pack升级不重置历史失败。对应03 §10.7与11完整链，归原T03–T08。

### 2.1 review-5交付与运行缺口归属（不增债项）

以下直接门补入原16项的相关关闭证据；数值及行为以03 §10.9–10.11的**单owner本机工程提案**为准，当前均未实测，不形成第二执行队列。身份与恢复缺口在相关能力开放前解决；7日运行统计在交付后有固定终点，不阻止已验证的受限能力先交付。

| 现有债项/任务 | 当前证据与剩余缺口 | 关闭时必须补的直接证据 |
|---|---|---|
| TD-01/07/16；T02/T03/T05 | `P/db.py::utcnow/as_utc`有UTC归一化，未证明本机校时/休眠恢复；03原先只有业务时点与局部时延 | UTC/IANA展示、时钟偏移/回拨反例不放行时点敏感新行为；当前页/受理时延、启用分钟分母、依赖失败与不可观测数；长期可用率独立待观察 |
| TD-08/14；T03/T05 | `pilot_runtime/readiness.py:198–221`主要核来源配置/市场flag；quote port现有10s校验仍为spot；来源在线不证明某用途覆盖 | 每用途的age/max_age、缺bar/乱序/修订和失权负例，OKX断流仅降受影响用途；不补造事实/fill，保护未完成可见；paper算术沿02 |
| TD-03/12；T02/T07 | `apps/hub_api/main.py:492–497`接收owner标签；`local_owner_session.py:58`签名会话仅harness路径，launcher仍有固定本地服务key | 本机业务principal/服务凭据分离，伪造/过期/跨owner访问拒绝且无写；secret不出现在普通证据，配置清单可复现，账户/资料/备份访问受控 |
| TD-01/15；T02/T08 | `main.py:730–751`有live/ready/pilot层次，`run-product.ps1:832,902`允许来源/Host Optional；原“ready”不能证明用户能力 | 同一build按阅读/研究/保护/paper/通知/学习导出能力与原因；Host或来源失败时阅读可用且相关新行为明确不准入，真实依赖证据不被本地健康替代 |
| TD-02/03/15；T08 | `Database.initialize:1731–1738`启动升head，历史runbook有单库备份；本次Hub/checkpoint/archive/DSH集合恢复未证 | 单owner迁移、旧对象兼容、一次隔离一致恢复/回滚证明；schema/原件hash、RPO/RTO实际值、恢复点后未决对象与外部回执核对；不重复Provider/通知/成交 |
| TD-07/15/16；T08 | 有官方启停/Supervisor与历史长日志，未形成当前候选的持续使用交接证据 | 有界manifest/启停/故障处置/备份恢复/责任与限制交接；7日观察ID、启用窗口、终点和报告归属明确，缺样本insufficient，不重复构建或全仓测试 |

## 3. 最小交付与更新纪律

### D2接线实测增量（2026-10-19北京时间；隔离实现未合入）

- **TD-13/15：报告writer强制V1审核。** 真实Factory、Provider输入/提交原件和完整Graph已到达 `CommitDecisionService`，原先返回 `research_graph_publication_review_required`。已在同writer补显式V2报告/source.v5，停止产生伪旧期限Forecast。新的本地集成4项133.18s通过，覆盖同报告原回读、Action事务失败全回滚、唯一发布/原回执及真实Advice查询。模型/child输出在此测试中是明确的合成边界，真实模型、主产品合入及Chrome仍未通过；相关债项不关闭。
- **TD-06/13：V2缺可读根因分析与真实证据解析。** 当前synthesis仅 `reasoning_refs`，report helper的facts/transmission_chain为空。交付前必须在同候选/child审核包中保留正反机制、证据、确认/失效及未解决问题；ref实际解析冻结来源。旧CausalLink只含30m/24h/72h，不能冒用4h。该项为主线阻断，不是以后文字美化。
- **TD-04/06：实际输出包超过8192空间。** 合法完整Tool参数34857字节，离线o200k_base估计12926 token，尚不含因果解释；非供应商精确计费。新V2专用输出预算沿SDK公开agent/request在header/Provider原件前设置，旧协议不变；不能删分析或晚改stream来凑通过。直接SDK证明与正式真实调用分别记录。
- **TD-12/14/15：页面及纸面范围不得夸大。** 当前只发布已审空仓三候选，unknown是显式假设空仓；已持多/空还缺size规则。初始监控paused、执行授权未开。新Advice已沿真实Snapshot/Revision原件读取，旧 `/v2/actions/current` 不能当作新perp4h。旧quality/frozenbundle仅支持V1/V4链，不能给V5伪造quality.available；先接PC可读原报告，未实现导出单列。

07 §8.1的切片对应05的D1/D2/D3及原T任务：先使一条真实研究可交付，再一个事件/合约/目标的双向事前建议，再完成其纸面与变化闭环，随后覆盖BTC/ETH、4h/24h/72h、核心事件族/阶段、02加密原生/扩展事件入口和真实经验复用；05 D4保留原资产/模型扩展。建议可以先于完整纸面切片可见；其缺项和资格必须如实表达。不得用早期切片结清F33全部能力，亦不得等全仓纯化完才让用户看到第一条建议。

债项更新只追加证据及明确子范围：记录修改的合同/owner、实际用户结果、直接失败/成功回执、迁移回滚和未证实项。完成某文件搬迁、增加enum、写出设计、测试文件存在、旧release局部门，都不能单独把“已验证”设为是。技术债并不代表已有真实性/权限/审计控制都应删除；优先删除重复责任与隐含耦合，保留可检验的保护条件。

### D1/BTC06 实测补充：重复事件日历认证与整Task截止（未解决）

- 用户影响：真实研究9轮模型返回后仍未形成报告/Action，原3600秒Task截止失败；不能交付。当前6次真实失败，不因候选号变化清零。
- 事实：实际callback-owner22064在原件准备中执行parse_fred_cpi_calendar，经先前技能步骤read_gateway/_deadline再进入完整prepared/event认证；model绑定、input与finish收据也进入完整认证。单栈不能证明全部耗时归因；API一次采样无匹配认证帧。
- 证据：D1/BTC-06/runtime-phase-timings.json、execute-step-owner-stack-metadata.json、terminal-cause-metadata.json、terminal/gate.json、pc-failure.jpg。
- 关闭条件：具体重复解析直接反例通过，原bytes/hash/PIT/权限与依赖失效仍正确，再真实同Run审核/报告/Action/PC成功；缓存或构建通过不能关闭此项。D2/D3仍未完成。

2026-10-18 17:33 UTC追加：candidate08/BTC07 loaded100.490263s、两个execute167.383642s/254.109023s，整链仍未通过。纯解析warm0.681s不代表完整认证。静态固定路径至少17次bundle/source复核，且stage证明重读继续放大；已用真实临时SQLite Factory/scope取得3次完整source认证对期望1次的RED。下一窄修复仅同physical RO的Frozen bundle复用、clone/cutoff/退出原reader复核；不缓存执行权限、不触碰generation/cycle和writer，当前尚未落主源。本项保持未解决，不因局部门通过关闭。

2026-10-18 18:52 UTC追加：candidate09已应用bundle窄修复，真实旧Run只读首读/复用/退出原读19.658/0.308/9.521s、认证1/0/1；BTC08 loaded callback实际36.000s，但新Run仍未完成。本项仍未关闭，七次既往失败保留。

### D1/BTC08 当前原生上下文压缩开销（已定位现象，未判根因/未修）

- 当前同Run原生记录两次连续compaction：18:44:48.130→18:46:26.266、18:46:26.268→18:47:48.438 UTC，随后prune一段23794 tokens；不是由该现象直接证明结论失真。request/context声明contextWindow=128000，尚未核对实际模型/Provider的有效能力与配置来源。
- 仅计大小、不保存提示/模型正文：首组user content分别129137/574/8125/105268/6061 JSON字符；后继上下文114847字符；14个工具schema合计71487 JSON字符、system3432。字符数不是token估计，不能相加后冒充实际模型使用量。证据BTC-08/native-context-metadata-01.json和native-context-sizes-01.json。
- 本次不在运行中改提示/删证据/扩大窗口或重新提交。后续若阻断主链，应先区分重复固定说明/重复来源、真实必要证据与SDK压缩策略，以有出处的上下文包/原件检索保留信息可达性；不得以任意摘要截断、少做必要反方研究或伪造模型窗口来“提速”。原生压缩是否影响输出及本轮能否正常发布仍待同Run终态，不为该项重复跑测评。

### D1/BTC08 金融会话误载开发技能（根因配置已修，端到端未证）

实际全局技能资源路径/调用和锁定SDK配置组合证实：自定义目录不覆盖默认roots。候选10只将provider includeDefaultRoots=false，显式禁用金融研究会话goal/todo及子角色todo。四个领域技能、必要数据/检索、specialist及独立审核、compaction/上下文/模型/Task预算保持。原RED/改GREEN11项证据见D1/BTC-08/native-context-audit/README.md，源SHA f6de0c3d64b75517aff8beaffb8496ed0a48ef10c5b0498a60369cd602395da8。八次失败原件保留；配置证明不替代一次真实研究审核/报告/行动/PC成功，此债项暂不关闭。上一条compaction现象不据此被当作全部耗时根因。

### D2真实模型可操作性审查：确定性装配与返修缺口（P1，2026-10-19）

- **已修但整链待验：**实际V2独立审核沿用V1系统persona，工具要求新授权JSON却系统要求旧receipt。现仅V2 Tool选用独立金融persona，Main直接真实Tool启动参数检查通过；旧V1/preset不变。
- **当前阻断首次真实V2：**模型需自行产生多层canonical SHA256，并重复提交完整candidate/review_request；合成fixture由Python代算，使接口不可操作性未被发现。实施中的修复是既有synthesis Tool显式prepare/finalize：模型只提交一次完整判断草稿，产品确定性派生内部引用和原审核包，finalize用同Session/Run/gen原prepare句柄回读。外部来源/冻结server refs不替换；raw Provider draft与normalized DTO分别留存并可重放，不更改概率/因果结论来通过。
- **仍未实现：**needs_revision/rejected后同Run有效返修。当前V2 native one-shot且Graph只round1。必须保留反对意见，后继切片支持按新版本/新证据返修和真实独立复核；不能将negative改approved、复用旧授权覆盖新候选或因未校准而强制no_change。
- **关闭证据：**模型不计算hash/不复制审核嵌套JSON的prepare→真实子审核→短finalize→Provider原件→Graph/报告/Action→PC正向；原草稿改内容必须破坏原引用证明。合成门单独标识，最终仍需真实模型。


### TD-03/13 增量：V2历史Graph跨代码升级读取（P1，待明确保留实现入口）

- 同一24h工程Run的真实Native/Provider/Graph已保留，修复report后继续读取时在任何业务写入前被research_graph_recipe_current_implementation_unavailable拒绝；持久状态仍report0/Action0。七个selected recipe源码与当前不同，包含本次必要reader/report修复及并行auto/paper源，schema/runtime一致。证据隔离artifacts/d2-native-preparation/same-db-resume/recipe-difference.json。
- 这不是模型失败或归档丢失。V2当前reader要求保存manifest与当前实现一致；正式保留实现能否dispatch仍在只读核查。不得改旧selection/原件、削弱当前源核对或绕reader来伪造继续成功。当前工程门改为最终源固定后一次完整集成；跨升级历史V2读取作为本债项明确子范围，未通过前不声称已支持。

### 2026-10-19 本轮已修复的真实接线问题（不代表完整交付）

- 实际Provider emission → Tool → HTTP目录的真实先后被错误HTTP now打乱：改用服务端已认证prepare emission作为目录cutoff，review/finalize同源，当前租约/截止仍按wall now；非固定时钟直接门1通过40.69s，最终主源24h门通过。证据D2/merge-03与隔离d2-perpetual-live-clock。
- V5发布被旧V4-only bundle worker误接收：正常producer先验证原文hash，再仅把实际V4送旧消费者；V5走自己的签名JSON报告读者，不制造失败的legacyjob。旧V4 v2/v3幂等门通过，最终主源24h报告/Action门通过；历史unconfirmed不重写，V2 PDF未完成。证据隔离d2-bundle-v5-schema-gate及主源D2/merge-03。
- 工程fixture临时库表注册过晚、输出目录缺失：均仅修测试初始化/证据写出，未动生产授权与资金逻辑；最终固定主源门1通过183.68s，不把旧两次pytest失败抹去。

仍开放的主线债务：实际模型与PC签收、negative review修订再审、V2持续监控/提醒、多事件议程、跨源升级的V2历史读取、真实结果与下一Run经验消费。它们不因上述问题关闭而自动完成。


### BUILD-RETENTION-DISK-20261019（P1，核心验收后处理）

- 事实：官方prepare-product-retention.py::prepare逐build将已验证transport与source复制到新的original-view；多轮候选留下重复历史副本。候选13因此Errno28，尚未进入UI/Host。
- 本轮恢复：仅对D1精确生成目录做NTFS透明无损压缩；内容/路径不变、未删除原件，receipt11 SHA复核保持；D1新输出继承压缩。candidate14 wrapper加入2GB可用空间检查。记录D1/retention-compression-record.json。
- 后续范围：核心PC交付后评审历史副本生命周期/可重建中间产物回收及容量上界；必须根据实际receipt引用区分权威原件与可重建副本，不得直接递归删历史目录，也不得为存储省略审计原件。当前不扩成重构build/provenance框架。

## 2026-10-19T02:36:15.555849+00:00 — 核心集中交付检查点（不替代原债务）

| 项目 | 具体事实/影响 | 当前处置与关闭证据 |
|---|---|---|
| 请求身份与可见时间混用 | 首真实V2在模型前失败；后续轮合法新证据也会被初始admission时间误拒 | 候选15已合精确Selection/生产代次身份与effective cutoff；工程绿，须真实完整报告后关闭 |
| 同设置无法再研究 | 已确认queued原回执让按钮永久隐藏；刷新不能解除 | 候选15显式新研究+历史回执；未知请求继续核对。须实际PC关闭 |
| 历史依赖漏捕获 | 已存V2原报告在旧代码读文件型方法依赖时失效 | 有限3函数完整回算及未来有限资源闭包已合；禁止改旧哈希/原件。正式同旧库回验待完成 |
| 模拟资金费覆盖与账户复用 | 已成交账户资金费不完整时，权益/净胜负不能证明，下一笔准入可能拒绝 | 保留未知；现可明确创建独立模拟账户。完整资金费覆盖及已有账户连续模拟仍是缺口，不能称已解决 |
| 条件监控范围 | 当前新片为到期提醒与另行同意的下一整点复查；不是完整价格/资金费/OI/新闻即时监控 | 首报告优先，后续按已有事件/机会队列接入，未授权不能自动加付费研究 |
| 真实效果验收 | 工程链为合成HTTP/模型输入；暂无成熟真实V2目标，更没有70%统计胜率证据 | 保存事前概率/条件/时点，成熟后按冻结4h规则记结果，再验证实际下一模型因果引用 |
| 构建依赖保留成本 | 候选13磁盘满；候选14保留/构建约21分钟。重复不可变文件副本放大空间与升级耗时 | 已无损压缩、不删金融原件；本轮不转做构建优化。首核心交付后评估去重及仅清可重建未引用副本，必须保留原件读取 |


### LEGACY-DAILY-OPERATOR｜旧每日研究缺独立暂停入口

- 观察：legacy risk_research_plans支持active/paused，discover仅active，但应用服务/PC/API没有对应操作；spot flag同时控制Action/quote/execution，不能用它作单计划暂停。
- 当前：在16启动前、无在途Run时，有界ORM配置维护仅暂停BTC/ETH两个旧daily计划，next_due等其他字段不变；完整审计与反向恢复影响见 `D2/operations-legacy-daily-pause/receipt.json`。
- 后续：补真实独立运维命令/原回执及PC入口，保留旧研究；不得把风险价格监控另一张表的暂停冒充每日研究暂停。当前核心新V2与完整Action旗标保持开启。


### HARNESS-PRODUCTION-BRIDGE｜并行准备截止与完整Provider轨迹

- 实证：BTC-02根+三个child并发准备被旧27秒剩余预算截断，实际模型/Task窗口并未耗尽；旧V4分支还跳过完整Provider路径，V2同call emission缺失会阻断报告。
- 已合17：生产准备沿用Task绝对截止；根见证与ProviderInput/Emission/Finish并存；root/child执行链、selected skills与实际delegate watchdog贯通。完整接口与代码落点见03的实施裁决。
- 验证：focused1P45sfakeprep+Node真实header+V2篡改负例、sourceTS0、独立复审0P0/P1。实际Native与报告/纸面仍待17后验，不能标已交付。


容量复用记录补充（18构建中）：NTFS compressed属性不足以证明实际空间收益，零空间转换可留部分状态；需读取实际分配与完整内容hash。仅在产品全停时完成DB存储转换，最终SHA不变、SQL写0，记录 `D1/live-db-compression-completion-record.json`。可逆转存仅允许从未安装的失败retention副本，保留构建记录/数量/字节核验；验证版本、来源捕获与业务数据留原位。构建保留复制的体积/耗时仍是后续infra债务，核心报告验收优先。

## PAPER-REUSE：平仓后的资金费核对与账户连续使用（2026-10-19）

- 只读代码审计发现：fill将funding_complete置false；平仓后episode不再进入active执行tick，尚无正常补齐通路；账户可用保证金因未知而拒绝下一笔prepare。不能用新建账户规避产品缺口。
- 当前仅审计发现，真实首笔纸面尚待验收。资金费历史现有单页/coverage_unproven不能当成完整区间覆盖，不得伪造0或净收益。
- 处理顺序：先取得真实BTC同Run报告与首笔纸面；依据原件验证，再增加持久补齐和明确覆盖证据。审计与准确文件落点：`artifacts/decision-product-core-20261018/D2/paper-account-reuse-audit/README.md`。
- 完成门：旧unknown原件保留、合法补齐后同账户可再次研究/模拟、重启后补齐幂等；未覆盖结算仍明确unknown。未完成，不将连续使用宣称已交付。

### PAPER-REUSE复审拦截的3个主链P1（2026-10-19，修复中）

| 缺陷 | 必需合同 | 验收 |
|---|---|---|
| closed source失败退出tick/每5秒重打 | 保护与正常intent先行；单episode source异常隔离与head CAS退避 | 一路source失败不挡另一路正常执行 |
| legacy closed缺initial_ledger_revision | 从已认证旧entry fill精确恢复，否则typedpark未知；不改旧金融原件 | 旧shape不KeyError、不污染账本 |
| 新entry继承旧episode/entry/保护 | 新logical episode、新授权/保护/funding字段；head version单调防ABA | 正常第二笔新保护；第二笔后旧回执仍回原episode/outcome |

首片2P和UI1P不足以覆盖上述生命周期，不作为交付证据。修复收敛在既有manual execution/head/tick/PC路径，不改变预测概率/研究时长。

### 2026-10-19 核心阻断：手动模拟risk limit与发布容量

- MANUAL-LOSS25：现有已保存max_planned_loss25曾被manual _prepare无条件拒绝，正常纸面主流程不可用。merge10修复到真实首quote的stop交易情景门，工程7P/独立review通过；实际21paper待验。funding/gap/liquidation未形成未来全净损失上界，须继续明确scope，不能清空风险偏好取巧。
- RETENTION-CAPACITY：20在retention publish Errno28前未编译；失败43454文件/1266166678逻辑bytes已可逆移C保留并核对，E恢复约4GB，21前3GB门。D1本已NTFS压缩，不再重复容量巡视。后续重复保留副本的存储生命周期可单独优化；当前只完成真实BTC/ETH产品，不扩大为存储重构。


### TD-V2-SKILL：永续技能选择和实际消费（开放，核心学习链缺口）

现有SkillPreparation、SkillUse load directive及SkillHost节点只支持V1 spot；V2曾因错误复用资格而在首model前失败。merge-11只修正能力路由，普通V2研究继续。后续需按V2目标/合约/方法编译选择与节点，Provider实际载入、执行/消费原件和下一研究引用应使用V2身份，不得复用spot回执或将经验记录存在等同消费。属D3核心闭环未完成；优先先取得真实BTC报告/纸面，再接回该缺口。

### CHILD-RESEARCH-DELIVERY-20261019｜子研究证据落库与主会话交付不一致（P1，开放）

- 实证：候选24/BTC-05的主会话成功返回9份证据，另有两份official.macro证据来自有真实父子身份、Provider emission与完整成功工具返回的子会话。两份证据以root research_session_id落库，却不在root直接工具原件集合；retained-context projection将这种合法状态当成致命上下文异常。无事实ID内容冲突；上下文整理的时间邻近不等于根因。
- 原件：D2/BTC-05/actual-turn-error-diagnosis/diagnosis.json，SHA256=4daf6adc564d63036ef69618902d515b869a967c24d4fbed10bd95fe8f8e5007。产品最终failed/research_worker_failed，报告0、模拟成交0。
- 已决定的窄修复：复用已登记子会话身份及官方inspect/Provider emission读取，保留firstLiveSeq和完整原始前缀；通过V2新保护投影v5向root实际交付。前缀哈希固定，后续子会话追加不改变已交付模型输入；root与child事件不混写。未交付原件显示缺口，不冒充已消费；同ID原件被改仍拒绝。V1-v4旧投影不重解释。
- 关闭条件：实际子原件回放或有原件边界的直接验证通过，再取得真实BTC研究、独立审核、同Run报告/建议的PC结果。仅修代码或合成测试通过不关闭本债务。后端V2发布/材料/上下文消费接缝须同时核查，不只修改Host局部。

## BTC06主流程实际阻断（2026-10-19，源修复待真实验证）

| 编号 | 已确认原因与处理 | 状态/直接证据 |
|---|---|---|
| CHILD-COLD-RESUME-20261019 | 合法disposed child续聊被单residency诊断误杀；独立连续性证明保留原出生身份/完整prefix，异常重入仍拒绝 | source_fixed_pending_live；actual codec RED→GREEN、32P、独立review，merge-14/cold-resume |
| ORDINARY-PREPARE-CONSISTENCY-20261019 | 非事件null引用和短candidate/长policy组合被TS/Python矛盾拒绝；两语言校正，冻结policy与实际到期守卫保留 | source_fixed_pending_live；同实际草稿完整prepare/review-request通过、portable TS3P/Python1P/Ruff0、独立review，merge-14/market-consistency |

下一验收仅26真实BTC→独立审核→同Run可读报告/建议→纸面→ETH；未获真实结果不关闭为产品已交付。模型首次flat而非neutral属于正确被拦截的输出错误，应走原工具反馈修复，不能放松概率分组或回写旧原件。


## TD-PROVIDER-INPUT-BYTE-CAP-20261019 — BTC07主线阻断

- 状态：根因已复现，最小修复实施中；未通过真实报告，不标关闭。
- 真实首因：Root330，本地完整GenerateOptions为1,010,431 UTF8B，旧cap1,000,000B；发生在记录/Provider delegate之前。不是远端token拒绝；摘要超时与字节门分开记录。
- 决策：单独的完整Provider输入工程cap为8MiB，联动Host、保护上下文、canonical+正规生成DTO、kernel写入和原件回读。现有tool arguments/finish/response及金融方法/真实原件/模型窗口/研究时限保持各自合同。8MiB是产品传输和存储接纳范围，不保证模型上下文或金融正确率。
- 验证：受控>1MB输入完整记录/回读、超过8MiB拒绝、旧工具参数边界不扩；真实327离线原文重放只作私有证据。先独立审查和原件保护，再一次必要部署，下一次只BTC产品完整主线。

## 2026-10-20 核心验收新增确证项

| 编号 | 确证问题与最小处理 | 状态与关闭条件 |
|---|---|---|
| CALLBACK-WRITER-41 | BTC17 callback finish 实际持写19.8486秒，两次source校验合计约19.59秒，commit 0.258毫秒。与事件/通知BUSY时段精确重合；不能泛化所有episode。只接现有authenticated-selection/prewriter seal，完整pre-pin真实SHA及OS拒写HANDLE、writer/postwrite当前校验和CAS保持。 | ISO单产品文件+真实callback/SQLite竞争门实施中；Main41冻结。工程门通过仍须正常后台实际恢复，无现成全健康宣称。诊断：D2/merge-27/REALTIME_MAIN41_DIAGNOSIS.md；ISO artifacts/d2-main41-sqlite-contention/DIAGNOSIS_AND_PLAN.md。 |
| OKX-ETH-OI-UNIT | `packages/provider_adapters/market/okx_research.py::_okx_fact`把`open_interest/oiCcy`单位写死btc。OKX官方`oiCcy`为base currency amount，ETH应为eth；现仅spot_volume已有币种校验。按已认证请求合约币种生成unit并核row.instId匹配，不由响应选择资产。 | 尚未修改；须在ETH核心验收前完成。BTC unchanged/ETH eth/跨资产响应拒绝，并保留原URL、capture、PIT、payload hash直接门。与已封OKX结果预算delta分开标识，不冒充已部署。 |

OI primary source: [OKX official agent-skills derivatives fields](https://github.com/okx/agent-skills/blob/github-main/skills/okx-cex-market/references/derivatives-commands.md#open-interest--open-interest)；只核字段语义，未下载或运行第三方代码。

## BTC17/C 实际主链暴露的合同接缝（2026-10-20，未完成不关闭）

| 编号 | 实证与当前最小改造 | 验收与后续约束 |
|---|---|---|
| LIVE-METHOD-PREFLIGHT | BTC17第3次公开审核指出：通用candidate合同允许cost_ref null+unknown_reasons，但当前method选中branch全候选必须绑定同一cost_policy.original_ref。只有flat/no_change实际交易成本0，held/no_change仍有退出/资金费。实施计划仅2TS：perpetual-preparation.ts新增纯已认证catalog预检；final-review-gate.ts只在实时preparePerpetualToolCall调用，明确绑定method/code49e4…，不改Python数学/发布/历史normalizer。 | null在冻结前失败、exact policy ref通过、held规则保留、其他method/code不套用、旧preparation.v1合法历史仍重放。Python发布仍最终权威。计划artifact d2-perpetual-live-publication-preflight/PLAN.md；未实施/未部署。后续method更改须带跨runtime一致性fixture，不能只靠prompt。 |
| REVIEW-ROLE-DRIFT | C当前账本绑定完整链真实通过结算/no-save-v3/factory/完整materials+完整promptDTO后，被Host perpetual-synthesis-protocol.ts的server-material role白名单拒绝input_binding。这是Python新原件生产者与Host消费者不同步；PC3P或DTO能序列化不能证明端到端成功。C已授权第13path显式兼容该角色，保留唯一role/bodyhash/ref/sourcekind/Run/context和384KB界限。 | 必须继续真实Host prepare/review/finalize/publicmapper/accounting/fullwriter/advice/manual/report，而不是只改白名单后称完成；旧catalog/history保持。下一次新增材料角色必须覆盖生产者→catalog→Host鉴真→完整writer→历史读完整链。将角色版本合同归并canonical并正规生成可列后续独立改造，本轮不扩框架。 |

C现有两次门分开记录：attempt1 existing fill WALL14.921s>原5s，尚未到新增链；串行attempt2真实入/退4.3457/4.5644s越过该点后，暴露上述Host角色错误。未放松金融或报价时限；后续门针对新确定错误，不重复已通过PC/全量测评。

2026-10-20T15:33:25.539290+00:00

## HOST-TS-42：完整Host类型检查未通过（不等于标准打包失败）

- 发现：live-preflight独立全TS检查exit1：reviewer-model-view.ts:56 compaction/prune不在SessionEventMap；client.spec.ts:370测试surface缺researchComposerSubmitIntent；research-managed-lifetime.spec.ts:58 ScriptedCompaction参数签名；retained-context-baseline.spec.ts:103 UserMessage与Record不兼容、:116 MessageId品牌类型。
- 归因未证：没有before全TS对照，不能因路径未改便称既有错误。A/C generated contracts/barrel不在这五条报错中。
- 标准实际脚本：PC build含tsc-b；Host build为身份生成+Vite+client build，不运行Host完整tsc。故下一标准构建通过只能证明实际产物打包及PC类型门，不能消除这五条完整Host类型债。
- 主线处理：保留显式未通过记录，继续42真实PC核心验收；如出现对应运行问题按实际栈修，不为全类型清理扩展本次金融/自动架构范围。原5P仅当时工具输出+manifest操作摘要，无独立stdout附件，不补写伪日志。

## 2026-10-21：BTC18成熟经验已生成，due进度未闭合（P2，待修）

- 用户影响：真实4h到期结果已存在，但旧due观察仍显示collecting；不能据此宣称结果未生成。下游selector读experience原件，因此不阻当前研究/经验候选选择。
- 一次有界RO证据：artifacts/decision-product-core-20261018/D2/AUTO-45/btc18-mature-result-readonly.json，SHA a126930d5dd22cb2e646e78c14be01f522fba0bc2f358323dbaeaff986c56f6a；exact BTC18 Run，无HTTP/writer/全表扫描，0.047s。
- 真实目标17:06Z→21:06Z，hash32c3552b6f78aed42e86cc6747b211bb3b870765c0264d5ce24a538b94084e62；真实experience perp_experience_46597f904520428ab2283f8a8698c117ac279fbbd38cf65d6ea6db139a786eac，category neutral，known21:35:03/recorded21:36:05。原件与目标canonical hash已验，未重放raw archive，不冒称全原件重验/预测资格。
- 原因范围：perpetual_forecast_runtime.py:245–247先写experience、273–278后写terminal due；109–114发现experience后跳过该Run。旧due6条最后21:34:25、attempt1/collecting/target_mature_source_capture_pending早于结果。
- 最小后续：恢复时识别“已有真实experience且latest due仍collecting”并追加可审计terminal观察；不重采行情、不重算标签、不覆盖experience。不在本次源冻结/活跃研究期间插修。


## 发布工具与磁盘规划：46真实失败（交付后治理，不在当前构建中改源）

- **已证故障**：46在retention materialization写入ENOSPC，stage本身未能持久化；UI/Host次数未知。Root按前版logical产物1.5x+1GiB估算不足，已保留失败原件，46a恢复6GiB最低预留并在实际7.39GiB下启动。额外allocation详细原因未认证，不编造性能结论。
- **可复用教训**：logical文件长度不能单独作为发布新增磁盘的可信上界；规划须包括构建输出、仓库内版本物化、临时写入和实际可用空间。后续用真实build阶段增量建立物理空间预算；一次失败后不得只降低门继续。
- **工具债**：4个launcher/observer与seal手动按编号复制，出现静态6GiB常数与record容量不一致、observer仍标45、准备脚本缺PYTHONPATH等控制器错误。应收敛一个参数化发布入口，由同一sealed record派生候选号、容量与状态，start成功后才挂唯一observer。当前46a已修正必要路径，不在构建中重构框架。
- **保全原则**：本次只迁移4份本目标旧/失败产物到D，242149files逐SHA一致、原E路径保留；业务库与旧报告未删。后续定义有容量边界的内容寻址存储/保留策略，避免持续复制累计，但须单独设计迁移和读取兼容，不能借此重写历史原件。
- **证据**：D1/candidate-46-actual-build-failure.json，D1/candidate-46a-build-start.json，D:/CodexProjectStorage/decision-hub-core-20261021-release46各*-relocation/result.json。


## 2026-10-21T02:06:39.626271+00:00: 46b真实PC新增确认

| 编号 | 实际影响 | 当前处理与关闭条件 |
|---|---|---|
| DEADLINE-COUPLING-46B（主线） | 候选有效期从prepare消耗研究时间；auto进一步min下一整点。BTC18发布后仅185s，ETH02约732s，准入截止/运行预算/可入场窗口耦合，可能合法研究尚未结束就被源槽截断。 | ISO7生产路径+1直接门窄修已获Root及fresh设计审APPROVE。新admission.v3具名/hash绑定policy：trigger只约束新受理、runtime admitted+3600、candidate prepare+5400。旧版本旧期限不改，行情freshness与PIT守卫不动；须实际auto/同Run报告+剩余操作窗口通过。 |
| PC-LOADING-COPY-46B（后续UX） | 设置首次读取尚未完成时显示“模拟账户暂不可用”，身份恢复时旧失效提示可能与新身份并存。数据回来后账户2500与v1设置正常，不能把加载态当能力失败。 | 后续将loading/invalid/unavailable明确分离，不靠重建账户或重复请求解决；截图来源AUTO-46与本轮初次AX。当前不扩deadline片。 |
| PC-ADVICE-DENSITY-46B（后续UX） | 建议页把设置、周期授权、免责声明、提醒、结果和PAPER纵向堆叠；结果卡仍偏原始列表，核心入口需要长滚动。实际截图已保存。 | 核心完整流程后按现有设计包重排主建议/操作/结果与按需详情；不删业务能力或原件，不在本次后台期限修复中夹改样式。 |

46b已真实通过BTC18到期结果与24h返回保持；UI/Host打包均1次成功、API200/3fresh/errornull。不据此关闭auto/24/72/PAPER尚缺的用户结果。

## 2026-10-21：四问复核补充（未关闭）

- RETENTION-LIFECYCLE：prepare-product-retention.py:903/963/1112保存整个prior.sources的original-view；build-product.ps1:75/109/190在各候选继承保全源码/组件引用。47回执65238来源与482冻结输入是不同范围，不能把前者全部称无用；但逐候选保存历史视图导致累加，迁D只缓解位置。后续共用不可变原件、按实际引用保留回滚依赖，清理无引用可重建中间件。当前未完成分配空间测量/安全删除清单，本次未删文件。
- STATUS-PROJECTION：47原始stage/wrapper已于04:01:05Z正常结束0，却在总控保留构建中及失联追查。现按原件更正，不重构建；merge28旧CURRENT已改为历史提示。执行优先级偏向发布/局部验证属实，不能靠新增一套门禁解决；已有通过证据复用，推进T03/T04实际事件情景概率/条件行动与自动闭环。

## 2026-10-21：47真实自动Run暴露的主线缺陷（未关闭）

| 编号 | 已证影响/根因 | 当前状态及关闭依据 |
|---|---|---|
| EXECUTION-PROMPT-CAP-47 | 自动Run ee574d在模型前失败；材料222740字节符合producer，但全文275167字符超内部262144，Host允许1048576；异常被掩成cancellation_projection_invalid | ISO按524288全文合同窄修及原prompt回归中；不截证据，不改金融方法/研究时限。须发布后真实同Run报告通过，不能用DTO回归冒充上线 |
| PC-PERIODIC-STATUS-47 | 服务端已生成Run，原panel无只读刷新仍留旧槽；queued判断遮蔽Run failed，页面误报排队 | ISO15秒只读刷新/显式刷新/终态优先/旧回包隔离已RED3→GREEN6；独立审及上线PC仍待，Main未改 |

实际原件：D2/AUTO-47/observation-current.json与binding-failure-provenance.json；根因回放：ISO artifacts/d2-auto47-binding-failure/prompt-validation.json。保留原失败，不重命名为成功，也不补做模型调用来证明同一个确定性异常。
