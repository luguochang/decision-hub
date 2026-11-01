# 事前概率报告最后一段修复记录

最新结果：一份真实ETH4h事前研究报告已在原版前端展示，JSON与离线HTML实际下载及内容独立核验通过，可研究试用。概率未经校准且报告窗口已结束，不具备当前操作资格；全部任务和长期验收尚未完成。详见本文最后的“实际14”记录。以下历史过程保留。

## 已定位的失败

实际12的第三原独审authorization为approved，概率完整性、证据平衡、成本保护三项passed，冻结信息incomplete。native终态已保存，20MB原transport和Graph round亦已保存；最终化开始持久时间11:41:27.533810Z，距原截止11:41:48.074051Z只余20.540241秒。原观察器到期关闭其Job时，本Run仍无Artifact或graph_final。原原件和关闭副本保持不变。

原历史读取的真实60日摘要存在，但审核器看不到90日原capture及覆盖审计。真实封存副本新读取链路已只读鉴真：79 SELECT＋1只读PRAGMA，零写，DB/WAL前后相同；原90行与所选60行分开标识，raw UTF8原字节及hash未改。

## Main采用

Root精确采用7源：Graph最终化3源、历史材料2源、两处prompt澄清。5个Main旧文件已完整备份，2个新helper沿现source inventory受控。D repair同步的是同7源，其自身旧前像实际只有3份，不能把D repair称为独立产品依赖闭包。Main没有Git操作。

Graph的完整原件鉴真在同一个同步callback的只读准备中执行，持有原source lease；短writer保留原readset、owner、lease、generation、取消、原件变化与deadline核对。没有全局授权缓存。原snapshot/final producer body保持相同语义。原SQLite写锁阻塞有实际RED；修复后的同入口及必要边界有GREEN。

普通V2的catalog可读视图通过原admission、raw、coverage及partition重放鉴真，摘要DTO/4000字符限制及原时间不改。旧catalog精确回读不升级hash；新view缺失或损坏不能降级成summary。fresh容量不足整份省略optional view，保原上限。既有upcoming事件/V3 fresh上下文保原summary，真实旧事件catalog/prompt字节兼容已验证；事件的完整raw view仍不在本轮交付范围。

prompt明确区分snapshot的可用时间边界与实际writer决策时间；明确每个opening exit body的仓位时间退出，分别保留proposal expiry和quote freshness。未自动改写模型结论、概率或保护水平，也未设未知目标anchor。

## 验证与实际限制

物理Main实际加载验证：67项Python（22最终化、20历史材料、25保护输入）、2项Host、2组原事件byte guard通过。首次保护测试启动目录错误导致4个collection errors，原日志保留；明确Main/importlib路径后同25例通过。Root source/import SHA前后核验一致。

新增helper类型错误为零；旧修改源诊断基线71→33，按file/rule/message多集合新增零。这不等于全Main ruff/strict零错误，亦不等于55（实际56）个总控任务完成。既有真实时间、预测评分、PAPER、事件、失败回放及部署交接门继续按实测验收。

## 当前构建

Root唯一session22904，outer13040/creation_filetime134374928702892300，supervisor22144；开始2026-10-26T12:54:30.2465288Z。原builder24196/creation_filetime134374928732997524。同Handle于2026-10-26T14:04:07.0340598Z实际退出0，outer耗时4176.7876549秒。有限postflight退出0、errors[]，未部署。CURRENT normal为6909bc704fe28b724743e5eae5d3fe727ef1c17419faad4e0af9215cf532932e，native为7d921119e08f4684b4dbc267bbc23dd4236ea685e38742d702ea786cd5e71db3；原raw sources1831/effective63359/artifacts48/full lib-dist649。UI与Host各一次0，process/migration/pointer/model零动作。

显式prior为6059af63013f78af55294847bcef09125946c0cacccb94ac1e2609cf0ed9978a；prepared4d9cc0360482a2d66a444b9d2e6ec846465fa623efa6a9895bff098bdc8d6629。原producer生成新的闭包及fingerprint，未提前预测或额外跑独立guard。133 controls、72 native inputs、5旧源码及651输出前像已固定；起始空闲内存15753134080字节，原build child8GiB设置保留。

真实研究报告仍须在新normal成功后，逐项确认保存、原PC组件展示与自包含JSON/HTML导出；本记录不把定向测试称为预测校准或实际操作资格。

证据：D:/DecisionHub-v3-development/evidence/legacy-sdk-review/probability-final-mile-main-integration/adoption-result.json（e3e1d5e7…3275）；D:/DH-final-mile-main01/summary.json（93117da4…f5f8）；D:/DecisionHub-v3-development/evidence/legacy-sdk-review/probability-final-mile-normal-build。
## 实际13：仍未交付，直接失败链已查实

原新normal上的唯一ETH4h研究实际失败，不能把前述源码/构建通过称为功能可用。Run为run_419adf77e7f44b35b7fa8785cc75e37c，一次POST受理；原deadline未改。Root同Handle实际14:22:58.7512605Z至15:10:17.6356724Z，2838.8845472秒/exit1。10个Owned Jobs均关闭，未保留失效预览。

两次真实独审均needs_revision。冻结信息、概率完整性、证据平衡连续passed；成本与条件从failed变incomplete，首次遗漏的hold/转向/作废条件已自行修正，最终仅余当前双源spot硬要求。审核器实际收到完整历史视图，90源行、86所选完成日线、4分区及8928B原capture；原时间/原hash不变。概率仍是主观未校准权重，不是已验证的市场准确率、成交概率或收益承诺。

现货调用失败的直接原因是模型填了未获该Task授权的crypto_spot，provider_attempts为空；实际冻结ID为crypto_spot_confirmation，preferred market.crypto_derivatives，hard/600s/两独立source、group、venue。完整canonical需求原本已传给模型，不能归因为数据库丢需求或交易所接口失败。永续prompt早返回跳过了普通研究原有的调用指引。最小提示修复正在隔离验证；工具schema enum需要新增可信catalog投影，未顺手扩大实现范围。

原native终态completed/error=null，最后答复blocked/needs_revision/finalized=false，明确没有调用finalize。映射记录perpetual_synthesis_capture_missing；Graph继续evidence_gap及第二轮重规划后，进入只允许首片的永续入口。关闭checkpoint唯一61B原错误为ValueError(research_perpetual_graph_first_slice_required)。这是已查实的流程异常；heartbeat storage busy只是另一事件，不是已证直接原因。修复必须保留真实负评、原预算和截止，不可直接删first-slice校验或伪造综合提交。

关闭业务库证明Run failed/research_worker_failed、Artifact NULL、lease清空。没有报告GET、render、GUI报告验收、JSON/HTML导出或交易授权，查看和导出均NOT_DELIVERED。39原文件一次关闭一致副本位于D:/DH-eth4h-replay13-source01，原SQLite未打开；副本9 SELECT/零mutation，前后原DB/WAL/SHM同，副本SHM变化另列。

证据：Ddev/evidence/legacy-sdk-review/eth4h-live-actual13/closed-investigation/closed-state.json（84cae955…cc65）；task-spot-requirement.original-projection.json（08f797ec…5ab6）；native-terminal.original-projection.json（42e069af…105d）；D:/DH-actual13-independent-acceptance/terminal-closed-addendum.json（48e8ece0…5271）。整体目标继续ACTIVE，所有自然时间与总控门未完成，不新增ACCEPTED。

## 两处后续修复已采用，标准构建运行中

两源已独立复核并精确采用物理Main：Graph增加完整admission之后的非重试失败收口，保留已鉴真原失败码和详情，禁止误入不支持的第二片；prompt要求从canonical evidence_requirements精确使用需求ID及允许路由，不猜别名，不改变缺口原importance。没有修改原前端或伪造负评报告。原两源码与真实6909 prior一致并已备份。

实际Main正常导入后18项通过，包含5个新边界、5个原相关回归和8个prompt用例；pytest92.82秒、outer98.701962秒、exit0。两个生产模块真实__file__/SHA及前后文件都相同。首轮私有CLI参数在collection前失败4/0cases，原件保留；修正启动器后通过，业务断言未改。实际guard有1次被拒bind、无错源，不能声称零socket尝试。原生产类型/Ruff均零新增，不是整体strict0。

Root唯一标准构建exec86365已于UTC2026-10-26T16:13:37.3753144Z启动，outer11540/FT134375048174195608、supervisor16292，原builder13124/FT134375048202279899。prepared SHA9badca4d0182b1ec73ad2d340ae1f838b8c94c7aaa36c0ebb67abe0ed062ddc7；新helper沿原producer，仅更新本轮两源绑定与prior6909。实际133controls、72native身份输入、2旧源码及651个当前输出前像已逐项核对；初始空闲21030764544字节。旧current31bf由同一次原retention保留，不额外执行独立guard/H1。

当前仅RUNNING，尚无新normal终态或报告交付。必须继续86365同handle，原实际退出后才做一次有限postflight；不能因空日志重启。正式服务、installed49c和pointer未切换；下一actual14的包、工作目录和Run尚未创建。旧13原失败/负评保持，预测准确率和全部长期门仍未验证。

证据：D:/DH-blocked-slice-integration01/adoption-result.json（53e3a82b…64ab0）、D:/DH-blocked-slice-main01/summary.json（bc8579e0…e4b51）、Ddev/evidence/legacy-sdk-review/blocked-slice-normal-build01。

## 新标准构建已通过，实际14待释放

同一normal86365已于UTC17:21:34.7630621Z真实退出0，4077.3878767秒；有限postflight实际退出0、errors为空。新normal SHA3ea7e378670646e72f0326de13be8619c0be89de83791607104804a9a8350eb8（250319B），native实际7d921119…1db3；raw sources1831、effective63847、artifacts48、full lib/dist649。原retention实际180→181并保留31bf；没有重复独立guard/H1。旧86365已闭，不再轮询或重启。

两源及公共回归已本地提交f6286d3961397965eeb84130991966e7b393371b，精确11文件/blob验证相同；Main无Git操作、无推送。公共5项在无强制OUT环境变量时通过，Ruff0，原业务断言与3份fixture保持，独立复核C0/I0/M0。性能索引优化仍为D候选，尚未采用或实测提速。

实际14只先完成未绑定离线模板及静态复核；最终normal/native/SDK/源清单绑定、真实运行、canonical报告、原前端及JSON/HTML仍待验收。正式服务与installed49c未切换。全项目与长期验收目标继续进行，没有新增ACCEPTED。构建证据Ddev/evidence/legacy-sdk-review/blocked-slice-normal-build01/final-receipt.json，SHA d1031bda…db4b。


## 实际14：原版报告与下载已验收，可研究试用

真实Run `run_be0d276c61d5446b9527f145c0f46969` 已保存canonical `art_fa190171cd604b01be7f61a89c1456cb`。原独审首次要求补齐冻结信息，模型自行修订后第二次approved，冻结信息、概率完整性、证据平衡、成本与条件四项均passed。原finalize成功、worker returncode=0；后续Host会话终止事件没有覆盖业务成功。没有修改历史负评或延长原deadline。

原报告GET实际200/31.830秒，物理Main原decoder与renderer实际0/11.651秒。Root在原版前端完成登录和同Run报告加载，页面真实显示“独立审核已通过”及40%/20%/40%方向分布，原“保存阅读快照JSON”和“保存离线HTML”按钮均实际产生Downloads文件。两个截图及Root记录保存在 `D:/DH-eth4h-live14/artifacts/gui-acceptance/`。

独立只读核验25/25：canonical、JSON、HTML内嵌原文一致，原authorization/findings与四项passed一致，SourceDefs与引用hash成立，四份source cut字节同，736/736 pinned文件重新hash零差异。浏览器实际下载补验10/10：JSON仅report.read_at变化，HTML仅同一读取时间的两处变化，其他内容相同。证据为 `D:/DH-actual14-independent-acceptance/acceptance.md` 和 `browser-download-addendum.md`。

报告原件SHA为5594bd727984ce79b036b96ff5cf5bba9e271083da350dc077de0978db527e1f。已保存阅读文件位于 `D:/DH-eth4h-live14/artifacts/report-readback/live14-report-r01/reading.json` 和 `reading.html`。完整audit bundle仍为unsupported_perpetual_version；本次验收的是自包含阅读导出，不回写原worker的report_export_accepted=false。

可用范围是研究参考：原方向分布subjective/research_trial，上涨40%、横盘20%、下跌40%，未经校准；事件发生、条件结果、实际成交与机会盈利概率未获认证。financial_qualified和execution_authorized均false，没有可执行数量或实际订单。最终assigned window为北京时间2026-10-27 02:27–06:27；proposal/review有效期为03:19:12.568623。验收时都已结束，当前只作历史研究。原模型pending文字按原件保留，实际action publication有最终窗口。当前单次成功不证明六组合稳定性或P50/P95目标。

预览由Root19788有意保持运行，outer25024/FT134375104106135867、WORK `D:/DH-eth4h-live14`，API与原UI39142。原Study自动复核政策保留，可能有后续模型调用；本次GUI没有创建研究/账户/订单或启用周期研究。页面“运行链路需检查/2/3”保持真实状态，不称整系统健康。交接说明及仅本预览身份匹配停止脚本在WORK/artifacts；停止脚本仅语法检查未执行。正式安装指针未切换。

当前一份真实事前概率报告的研究→保存→原前端→阅读下载链路已经闭合。全56任务/9阶段及原T01—T08、六组合14自然日、真实事件/提醒后继、10笔PAPER退出恢复、四周评分、30完整失败回放、最终strict/Ruff与部署交接仍未全部验收，目标继续ACTIVE，不新增ACCEPTED。
