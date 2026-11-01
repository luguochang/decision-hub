# 后台研究的时限、进展、恢复与交付方案

## OP071 最新执行政策

用户明确有用后台研究可以超过60秒。页面等待/断连、callback、模型步骤和总期限分开；合法在途请求不因一分钟无新证据取消。旧07已保存报告但Action未过；32业务候选已封，正常08尚未安装。V2/900只属试行，持久callback/具证进展/跨Run续研未实现。具体实施字段、状态、源码接线与验收以[长研究实施规格](ASYNC_RESEARCH_IMPLEMENTATION.md)为最新入口；下文为保留历史，不据旧状态授信。

## OP070 候选实现与未过门

新budgetV2显式900收尾，1800/600/120及旧v1字节保持；候选21Python/13TS。V4无损baseline refs＋完整共享attributes，31/8/10可重建，两计量含输出低于实际128000；候选Host13/Kernel19＋真实私有读链。正式尚未安装，provider/最终综合/独立复核/Action未通过；持久callback/continuation仍未实施。gen2精确marker窄修及联合发布进行中，不能用纯helper绿替正常主线。

## OP069 最新真实观察（本节覆盖此前运行时态）

ETH07已于02:55:57 UTC以degraded结束并保存材料报告，租约释放；SDK/模型及压缩均报CONTEXT_WINDOW_EXCEEDED。实际窗口128000；唯一保护消息内baseline和顶层重复27Facts/7Evidence共230666字节。完整31Facts/8Evidence/10Frozen及原件保留，版本化无损引用投影待实施。最终PC显示8次收尾预留拒绝，原预算不改。长任务政策、未知回执持久恢复仍待实施；有用阶段进展可超过60秒，模型容量需通过实际预算和资料供给解决。





登记：2026-10-14。依据用户本轮补充及 core06 实际源码/运行证据，**先设计后实施**。本方案修正把单次传输等待视为业务成败的做法；总目标 W0–W9、事前行动、观点核验和结果驱动学习保持。





状态：预算/持久callback/continuation设计及源码评审已完成，相关协议尚未实施。core07正常发布7/7已证；但新ETH07在02:50:25以CONTEXT_WINDOW_EXCEEDED失败，第7输入仍认证完整。02:52 Hub Run仍running待终态同步，不称正常持续。旧06失败及新07原件均保留，具体压缩/上下文细因仍待限定诊断。





## 1. 这次核实了什么





| 层 | 当前实际边界 | 对产品的影响 |


|---|---|---|


| 旧06研究原边界 | 总期限 1800 秒；实际 Run `run_b391f2a4520c4ceda0d3a19e188c5055` 截止 01:35:54.751461 UTC | 研究可以持续超过一分钟；前台离开不等于用户取消 |


| 一次模型步骤 | 600 秒，真实 Host 有 step watchdog | 不能把模型尚在一个有效请求中推理说成“空转60秒” |


| 外部工具 | pack 声明120秒；实际 DSH research transport 默认65秒；内部能力另有预算 | 声明预算和真正能等待的时间不一致，可能在业务预算未耗尽时丢掉一次传输 |


| 技能原件/阶段回执 | Host固定60秒，Hub路由另有60秒 lifetime | 当前会影响模型下一次调度；并非整个研究只有60秒 |


| PC普通读取 | 默认30秒 | 已见旧05方法真实34.266秒才返回200；前台超时不代表后台计算或结果不存在 |


| 新取证窗口 | `deadline - 2 * model_step_ceiling` | 本次仅前600秒可新增取证，到01:15:54.751461关闭；提高单步容忍时间反而挤掉取证空间 |





代码位置与真实接线见 `artifacts/decision-product-v1/core-release-06/TIMEOUT_LAYERS.md`。本次实际总期限/单步值见 `w5w6-live-06/acquisition-20261014T010917613463/snapshot.json`；不是仅按配置推断。





W7 Future03 的62.989918秒发生于**纯本地身份、原件、阶段认证及回执准备**，当时没有模型推理或网络检索；函数已返回、loaded已保存，私有计时门随后判失败。A/B恢复诊断分别做1141/996个SELECT并重复准备同一trial。它证明重复认证和传输/回执语义有问题，不能证明研究只应一分钟，也不能把已保存回执当作模型已经收到、执行或采用。





### 本次真实上下文续接首因





上述ETH Run后续失败是另一条已定位缺陷：两个已保存ProviderInput均正常，第三次dispatch在SDK压缩旧prompt后，被旧protected快照的边界校验拒绝。`canReuseRetainedRootContextMessage`仅比较实质DTO，复用native_last_seq=5；原prompt seq7却已被真实compaction172/176移出当前surface。当前TS与Python校验均仍要求prompt7可见，故研究被中止。并非60秒到期、非JSON截断、也非两份成功输入身份串线。





近期修复先于预算升级：使用SDK公开`Session.append`的受证replace及sourceEventSeqs，从正常认证当前状态生成新快照，替换旧可见快照，保留原native事件审计。不能手抬旧bound或删root守卫。真实一份protected message为540453UTF8 bytes；简单添加第二份会超过现有1MB输入门。新输入保持唯一快照、完整27Facts/7Evidence/10 frozen inputs与baseline，测实际替换、体积和错误root拒绝。证据及单一轻复现计划见 `w5w6-live-06/native-context-failure-01/`；修复已正式安装core07，实装7个Host控制和唯一正常build通过，新07已保存第3实际输入，唯一保护快照和完整27Facts/7Evidence/10冻结输入仍在；真实压缩续接此段已过，技能消费及Action尚未通过。





### OP067 独立代码评审补充（预算/异步改造仍未实施）



- Host/API目前把客户端断连与60秒操作期限设置为同一取消位；未提交的长准备没有持久恢复任务，内存pending不等于跨重启。已提交原回执可按精确身份恢复，不能由此宣称未提交也可恢复。

- step watchdog从SDK step/start至step/end，包含的时间不是已证明纯模型推理。1800秒总期限下直接把model step改900会令取证窗口归零；须独立冻结closing reserve，本文没有实施或挑新数值。

- 有用进展包括实际原件/阶段增加与有效Provider请求在途；心跳/重复同hash/同错误不续命。可展示具证阶段、依据和研究结论，不以私有模型思维链作为用户交付。

- 根因问题目前须映射声明requirement才进入许可路径，自由问题不自动扩大能力；新具名inquiry/continuation另记未实施。延长等待或减少正文不能替代这个业务缺口。

- [独立评审](../../../../artifacts/decision-product-v1/background-research-review-01/REVIEW.md) SHA `d1fa9d13…d56af6`；其早期snapshot事实不覆盖后续live07观察。本文件预算/持久callback/continuation依旧未实施，A+B只属于已明确获准的候选去重片。



### OP068：新的实际模型上下文容量阻断

`w5w6-live-07/context-overflow-01/REPORT.md`已用native668与同dispatch ProviderFinish确定gpt-5.5的CONTEXT_WINDOW_EXCEEDED。第7输入671645UTF8字节小于产品1MB门，31Facts/8Evidence/10Frozen与唯一保护消息仍认证完整。之前的第3dispatch续接修复确实生效；本次并非60秒callback到期或该guard回归。

完整来源正文（按权利）、工具结果、结构化分析及不可变证据必须保留。模型输入同时必须处于实际可用上下文范围；DC-CONTEXT-01要求在具证诊断后设计认证ref/按需取原文及真实引用消费，不能用盲删关键证据、抬产品1MB或只加token/timeout掩盖溢出。当前策略**未实施**，provider usage字段不单独证明精确窗口上限。

02:52快照DSHfailed而Hub仍running待Worker同步，PC保存8证据/5来源/6Gateway及4收尾期拒绝。收尾拒绝与overflow分别记录，未证实因果不合并。根已分别安排单Run终态只读与native-only细因提取；没有新Run或预算修改。下一正常研究需先通过具体修复/保真/供给与预算门，不能无限重复原Run。

## 2. 产品行为





用户发起一次更新后获得稳定请求回执，可以离开页面，后续回来看到：





1. 正在获取哪些资料、检验哪条机制、还有什么缺口；这些是阶段成果摘要。


2. 已保存的依据、反方和暂定判断，明确资料时点与当前可用性。


3. 最终操作、触发、退出、失效和复核条件；必要时由后续研究修订。


4. 停滞、断源、结果提交未知、确实失败分别说明，并提供原请求恢复入口。





不能因为浏览器等不及而从头重做研究；不能让“正在思考”或心跳成为永久续命依据。研究成果可保留，但过期行情对应的操作不得在现在武装。





## 3. 时间政策分开冻结





新增政策以版本和hash随新Run冻结，历史Run沿原规则读取。实施时使用 additive 合同，不重写历史deadline或旧reader。





| 字段/类别（待实现） | 责任 | 约束 |


|---|---|---|


| `ui_wait_seconds` | 前台这次请求等多久 | 到期进入读取原回执/阶段状态，不能触发研究取消或重复POST |


| `operation_timeout_seconds`，按操作类型 | 本地认证、外部工具和模型各自最多运行多久 | Host、API、worker、实际provider使用同一冻结政策；不得相信模型/调用方自行声明更大权限 |


| `closing_reserve_seconds` | 取证结束后留给综合/复核/提交 | 与model最大等待时间独立；显式配置、边界校验和状态显示，不再必然等于2×model ceiling |


| `overall_deadline_at`及调用/费用预算 | 一次Run的总体消耗 | 进展不自动无限重置总期限；到期保留已有产物与未解决问题 |


| `stall_policy`，按阶段 | 判断停滞及恢复 | 等待外部有实际在途请求、等待DB、重复失败与无新证据分别处理 |


| `evidence_as_of`、`known_at`、Action有效期 | 防止事后信息与过期操作混用 | 计算完得晚不改变原信息时点；必要时发新的具名研究版本 |





近期先评估把本地/外部 transport 与已登记操作预算对齐（例如当前pack声明120秒），并完善原键恢复。这是修复口径不一致，不足以证明具备跨重启长任务。不能只加一个环境变量就宣布完成，也不以反复压到59秒作为永久产品目标。





收尾预算的实施初值需从已保存真实综合/复核/提交耗时冻结，登记所选样本、估计和失败处理；不在本文凭空把600换为另一个硬上限。验收必须证明改变模型step ceiling不会自动吞掉取证窗口，及收尾仍能正常提交。旧失败ETH06的取证窗口仍记录01:15:54，不后改来补通过；新07 Task须由正常Factory重新冻结，新预算政策尚未实施。





## 4. 什么算进展





分别记录工作进展和研究进展。每个进展绑定实际输入/产物引用与hash，不用长文本当成果。





- 工作进展：资料清单冻结、实际认证阶段完成、不同原件处理数增加、结果回执提交。


- 研究进展：新的独立来源结果、缺口被补齐或具证不可得、机制获得支持或被推翻、反方改变结论、独立审查完成。


- 不算进展：轮询、心跳、同hash内容重复、同错误无限重试、不断输出“正在研究”。





模型尚在已登记且未到期的真实provider请求中，即使暂未产生结论，也不能仅凭一分钟没有新证据强行结束。超过阶段预计耗时先显示等待原因；识别到重复无效行为才按停滞政策处理。总体预算仍受控。





结果摘要最少包含 `phase, completed_work, latest_findings, unresolved_gaps, last_meaningful_progress_at, expected_next_step`。默认页面用中文说明；具体IDs/hash在审计展开区，不能挤满主页面。





## 5. 最小实施顺序及现有代码复用





### B01：先让现有任务和回执可恢复（W2/W4/W8）





- 复用现有Run、ProviderInput、实时状态、原stage key和不可变阶段回执。


- `apps/hub_api/research_skill_routes.py`区分传输断开、真正用户取消和业务总期限；不把浏览器断线当用户取消。


- `extensions/dsh/decision-hub/src/host/hub-client.ts`、`skill-host.ts`保留原调用身份，在结果未知时按同key恢复，不重新调用模型或生成新loaded事件。


- PC `api/client.ts`和方法/进度投影显示“后台核验中/提交结果未知/已保存”；重开页面能回到原任务。已有34.266秒方法读取作为真实问题样本。


- 先在core07正常ready后，以首页ETH24h单次新请求验证现有业务链；旧core06失败保留，后台预算/异步协议升级尚未实施，不能成为所有核心功能的前置阻塞。





### B02：去掉本地重复，并拆开收尾预算（W5/W7/W2）





- 单快照方案只合并同一callback的身份/阶段检查与材料准备，保留最终writer全部原件验证，不把旧验证结果当永久权限。具体四个文件/分支已在 `trial-single-snapshot-plan-01/PLAN.md`写明。


- 私有Future03已存在loaded，只可在原精确方法/原source family内按原key完整认证恢复；A+B或core07 rebase改变compiler/Graph身份后必须新冻结与另行授权新链，旧loaded不能授权新family。历史失败和已提交回执保留，不能为省一次验证放宽身份。


- `packages/kernel/decision_hub_kernel/decision/research_loop.py`将closing reserve从model ceiling公式拆出；请求生成、冻结合同、profile与Host保持一致。新旧政策明确分支，旧Run不迁改。


- DC-W7-AB-01四P源候选现已封，20项mechanics窄控2.50秒、4AST/有限lint与独立静态审无新增阻断；使用有限source/material doubles，没有真实Trial/Future或实际60秒延迟通过。仍不正式安装、codegen、新family、Future/replay；后续完整门须另行登记和授权，不继续堆缓存或猜测全链重跑。





### B03：确需长批次/跨重启时实现持久callback（W7/W8）





这是待实现升级，现SDK不自动支持HTTP202续接。完整可行性/文件接线/租约风险见 `trial-single-snapshot-plan-01/BUDGET_REVIEW.md`。





首片限定内部loaded/consumed记录，不让模型收到假成功：





```


Host记录实际ProviderInput


  → 同原stage key提交callback


  → API短请求保存不可变binding，返回job_id


  → 现worker内有界分流：完整认证→原writer提交→不可变result


  → Host同key读取完成结果，真实完成后才继续dispatch


```





新增窄callback repository/service及additive binding/result/checkpoints；复用调度设施，不能把callback冒充成熟Outcome的learning job。不新增独立进程。





binding必须包含原body/hash、Run/generation、root/execution session、原occurrence、method/selection/compiled-plan、预算政策与绝对期限。writer/renew/fail要同时核owner、attempt、expiry、cancel version与generation；现普通Evolution `_owned`不够，不能直接照抄。





状态最少 `accepted / preparing / committing / completed / failed / cancelled / expired`，未知提交另记回执状态。status GET只读持久状态，不再同步重跑数十秒全链。进程重启重新认证可变权限，不序列化PreparedOriginals/SQLite snapshot当未来授权。





### B04：跨Run持续研究（W3/W4/W7）





在现有持久研究任务下登记continuation关系，保留未解决根因、已有资料/机制、后续入口和剩余成本政策。每次后续Run重新冻结as-of/时效输入和版本；不是修改旧预测或不断重跑同一问题。





触发条件须是可解释事件：新关键数据、机制反证、时效到期或具名未解决缺口。用户看到同一研究主题的修订历史；最终Action、Outcome及技能采用仍各自有原件。父任务机制与一次callback的后台化分别验收，不互相冒充。





## 6. 验收与效果指标





| 验收 | 可通过的实际结果 |


|---|---|


| 有进展且超过60秒 | 真实后台链完成多个可核阶段、墙钟超过60秒，任务和产物仍保留，最终调用方实际收到；不能用sleep或只返回pending冒充 |


| 前台离开/超时 | 原任务继续，重新打开看到同一回执和成果，无重复模型/POST |


| commit unknown | writer前/后断连分别验；同key恢复第一份真实回执，改body拒绝 |


| 重启/租约/取消 | 恢复已有产物，旧attempt/过期lease不能提交；显式取消有效，已提交结果仍可审计 |


| 晚到及数据时效 | 旧generation结果不能授权新generation；旧报价不改为now，过期Action不武装 |


| 真实无进展 | 仅心跳/重复同内容不能延长deadline；仍在合法provider请求中的等待不误判为空转 |


| 收尾窗口 | model ceiling与取证window独立；正常综合、反方复核和提交完成；未完成如实留产物 |


| 实际业务消费 | selected/loaded/执行/consumed/Action分别有本次原件；后台完成但没有进入模型或Action不能授通过 |





记录：阶段有效进展数、重复读取/重复请求数、任务恢复成功率、成果丢失数、实际模型收取数、取证/推理/认证/提交分段耗时、到期未完成原因。目标是减少重复和成果丢失、增加真实依据与可用操作；文章长度、运行时间或心跳次数不代表分析更好。





金融效果继续按固定事前Action/独立Outcome协议评价净收益、风险、误动作、漏预警、概率校准及相对基线。允许更长研究不自动意味着预测更准，不把工程通过写成70%胜率。





## 7. 当前完成与未完成





- 已完成：实际时限分层/恢复与异步可行性评审；core07正常发布7/7；Fed正常原文入库及首输入核验；第3dispatch续接真实通过。新ETH07后续到7份认证输入和selected+loaded，随后native因上下文容量失败，业务未过。W7仅四P源A+B候选窄控/静态审完成，非全链完成。


- 旧core06研究已失败：首输入包含真实双venue、闭合日线、观点和明确软缺口；两个输入均已认证，第三dispatch因压缩后错误复用旧保护快照被拒。原7条证据保留；最终技能消费、机制及Action未通过，失败不会自动重新调用模型。


- 尚未实施：本文的新预算政策、持久callback协议和跨Run continuation；W7真实非种子后续消费仍未通过。分层时限/单快照评审文件不算功能已完成。





每片开始前在tasks与EXECUTION_LOG登记范围、接口和验收；每片结束保存操作、原件、首败、改动hash与实际结论。当前总目标仍active，不另起一个只做性能的目标。


