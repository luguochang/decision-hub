# 13真实失败后的14修复执行单

> 2026-10-08T09:13:32.935966+00:00当前15b：标准构建/Ready P，3health200/7online/schema0081；15b普通PC每资产各一次请求已终态：BTC run_4fddd3c278184b5fa5b44cb41b35026a failed/noartifact，原生首码dsh_provider_input_transport_timeout（09:06:41.575 UTC），worker 09:07:02 UTC的dsh_terminal_conflict为次因；ETH run_e664bc33a2614be19ca1cab6247e890e degraded/art_7ba93e84737c49d6862db618b34b7521，native failed首码dsh_provider_emission_transport_timeout（09:06:38.969 UTC）。ETH有降级artifact不等完整报告P，正式15b真实新完整报告门F；唯一六GET原件正由Core核对。 [15b的16工程门](../../../artifacts/market-risk-implementation-20261007/B2/release15b/ENGINEERING_ACCEPTANCE15B.md)。15源限额首F及14c两Run F保留，以下阶段历史不替当前终态。

以下13→14及14b段落保留各阶段历史事实；14c结果仅为历史失败；当前15b按顶部与CURRENT_FOCUS为准，原失败不覆盖。

## 原件与已排除的误判

- BTC `run_25235e5d22de42adabab61358efa3346`、ETH `run_ddffbffbe839480394f7ccbee11e4d55`均原生`dsh_provider_input_transport_timeout`、artifact null。请求入口确实manual/priority100，不是未启动DSH，也不是1800秒总期限耗尽。[唯一终态六GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh13/execution01/receipt.json)、[原input/emission/finish](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh13/failure-originals01.json)。
- ETH两个复核子任务从context开始到失败约30.008秒，绑定已保存但input没有原件；BTC下一root输入阶段也超时。actual Native序列证明失败发生在模型前的输入回执PUT阶段，具体writer等待/服务响应首因尚未证明。
- [有限纯source测量](../../../artifacts/market-risk-implementation-20261007/B2/provider-input-scan14/source-guard-measure01.json)：capture2.665s，3次matches约0.08s；[物理RO读取](../../../artifacts/market-risk-implementation-20261007/B2/provider-input-scan14/original-read-measure01.json)：prepared1.283–4.536s、200KB input读取0.013–0.031s。不能单凭旧13.020秒或文件数把本次首因归于scandir、SHA或“完整input递归”。这些有限独立读不是实际callback耗时。

## F14-01：模型前输入回执失败

| 项 | 精确方案 |
|---|---|
| 已知设计问题 | Host把link/bind/providerScope累计耗时从配置30s准备预算扣给input；input又受独立15s及冻结Run剩余限制。25s绑定+10s回执分别未超各自期限，却因合计35s被压成5s失败。ETH实际时点符合该耦合；BTC可能另有15s回执等待，不能承诺单项全解。 |
| 最小候选 | DEV `model-dispatch.ts`解除前置耗时扣input预算，保原configured上限、input min15s/frozen remaining、bind30s、emission/finish15s、Run1800及abort。所有原件/权限/绑定/source校验保持。 |
| 收尾风险 | `_bounded_callback`的is_disconnected观察器只有cancel+await，与已修model binding存在同类CancelScope吞cancel风险。DEV按既有stop_watcher/外部取消保留语义收尾；响应实际交付前仍需检查寿命。此静态风险不替实际首因。 |
| 必须补观测 | 下一真实调用必须保存server生成operation ID、callback阶段/耗时、writer等待、返回/响应及Host实际budget。当前uv wrapper日志0B不能推“没有callback”。只存安全身份/阶段/时间/状态，不记录body、SQL、凭据，不扩权限或延长寿命。 |
| 有意义控制 | 旧耦合25s+10s失败、新分阶段通过；bind>=30或input>=15分别abort/provider未调用；低配置仍生效；冻结期限不足拒；真实Starlette吞cancel收尾仍可结束并交付body，外部cancel/disconnect/超时不得成功或晚写。 |
| 正式验收 | 控制封签后保全部13原件、正常停止及一次标准合批构建/启动；每资产最多一次普通新请求，阶段观测与两个终态完整原件同Run核验。失败不重签或无条件重试。 |
| 报告通过条件 | 同资产90日/双venue/衍生及缺源、root-review完整正文、ReviewV2四影响、actual usage/未知USD裁定、publication/context/quality/case/consumer；Ready或input保存均不能代替。 |

## F14-02：有Run失败被首页误归准入

实际[ETH失败DOM](../../../artifacts/mr-pc13/eth-failed03.txt)和[截图](../../../artifacts/mr-pc13/eth-failed03.png)显示已有执行入口，却把`research_observation_unavailable`写成“准入核验尚未通过”。

- DEV只改`RiskResearch.tsx`：无Run才显示准入原因；有Run按实际状态显示执行/结果读取失败，读取不可用本身不推Run已failed；保审计原码及沿原执行记录恢复。
- 不自动新开请求；原pending时间、预算/并发/暂停、scope、幂等和按钮防重复保持。
- [16有限检查及类型/封签](../../../artifacts/market-risk-implementation-20261007/B2/research-failure-classification14/source-seal14.json)通过，正式源码已晋升，14标准构建预检失败，发布后仍须实际PC失败状态验收。

## 发布与记录要求

1. 四组源分别封签，Root只合已核diff；正式运行期间不热改源码。未改测试/数据及原已通过PC复用。
2. 保13标准receipt全部源/产物；如果实际选中V3则保其真实闭包，不能造capture或借别版本授权。
3. 标准发布一次，校验Ready、原下载与变化文案；真实两Run一次、终态六GET一次。原失败保留；实际新结果再同步当前一页纸、33清单、16门、手册与截图。
4. 修复失败有具体首错再限定下一动作；每个阶段须有界。28日/标签/统计/自然告警/黄金等独立等待沿原协议，不触发无限重构。

手机不纳入；金融效果未证明；U01三位非开发者仍未验证。

## 14有限验证与晋升原件

- [页面分类16项/类型检查封签](../../../artifacts/market-risk-implementation-20261007/B2/research-failure-classification14/source-seal14.json)。
- [Host9项/路由8项封签](../../../artifacts/market-risk-implementation-20261007/B2/provider-callback-lifetime14/source-seal14.json)。
- [安全记录及原件写入5项封签](../../../artifacts/market-risk-implementation-20261007/B2/provider-callback-diagnostics14/source-seal14.json)。
- [启动器路径/语法检查封签](../../../artifacts/market-risk-implementation-20261007/B2/launcher-provider-diagnostics14/source-seal14.json)。

[9文件晋升回执](../../../artifacts/market-risk-implementation-20261007/B2/release14/promotion14.json)。有限控制通过不替实际新Run报告验收；旧失败和控制中失败原件均保留。

## F14-03：发布回执把历史UI产物重复计入

14标准构建预检失败：回执估算2,139,517B超过固定2,097,152B，UI/Host打包0次、启动0、模型提交0。13已正常停止。旧前端hash文件累计3693JS/49CSS全部被回执重新计入；候选改为绑定本次Vite完整manifest产物、公有文件及全部Hostlib，旧文件和历史原件保留，最终2MiB原门保持。尚未发布或通过真实研究。

[失败阶段原件](../../../artifacts/mr-release-b2b4-14/build-execution.json)保留。仅修构建产物选择与预检估算语义；完整本次动态依赖/公有资产/Hostlib和最终SHA、2MiB门不得省略。必要负控包含缺失/篡改/路径逃逸/链接拒绝、历史文件原样保留及最终超限拒绝。源码封签后才能执行新的标准构建，不复用失败输出目录。

## 14b真实产物兼容失败（新原件）

14b完整assessment预检P，UI/Host各一次exit0；最终artifact reader拒绝Vite合法重复dynamicImports（index.html 15引用/12唯一，OperationsPage重复），故无validated receipt/Ready/模型调用。仅修字符串引用数组兼容，所有路径/引用存在/原SHA与最终2MiB门保持；真实14b输出先只读回放验证，再标准发布。
[标准执行原件](../../../artifacts/mr-release-b2b4-14b/build-execution.json)，[产物闭包修复封签](../../../artifacts/market-risk-implementation-20261007/B2/product-artifact-closure15/source-seal15.json)。前次14预检F和13报告F继续保留。

## 14c标准运行与本次实际验收

构建/启动P，当前产物41含ELK Worker，原2MiB/SHA门保持。[引用数组修复封签](../../../artifacts/market-risk-implementation-20261007/B2/product-artifact-reference16/source-seal16.json)。F14-02有Run分类实际P，[PC14](../../../artifacts/mr-pc14/actions01.json)七组/52原件；新完整报告仍F，不能补认。

### F14-04：BTC模型步骤前catalog边界

实际turn8.011s UNKNOWN timeout，无step/binding/provider。SDK assemble先于step/start，产品catalog有独立8s信号，强支持该分支但缺旧HTTP到达/耗时。候选必须匹配真实scope、守原目录/source/hash认证，有界处理请求并保存安全阶段，不空目录或自动重试兜底。

### F14-05：ETH原生完成后最终physical snapshot传递

native completed，worker run_event4100 phase execute ValueError `research_provider_physical_snapshot_required`。已有graph round及quality sidecar不能替最终publication。候选修真实物理快照传递，不能取消门、用伪快照或写者内普通读取替代。[worker/consumer诊断](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh14c/worker-consumer-diagnosis01.json)。

实际有一emission15s取消并rollback，但native后来completed，不将其认作ETH终态首因。两Run及[唯一六GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh14c/execution01/receipt.json)保存，不重试旧Run；两具体修复完成合批标准发布后再验。

## 15 晋升及新发布首错

2026-10-08T08:02:19.891992+00:00：目录有界寿命/取消 20 项控制 P；三处读取事务 5 项控制 P，[审核后6文件晋升](../../../artifacts/market-risk-implementation-20261007/B2/release15/promotion01.json)。标准停止完成后构建15，assessment预检拒绝 `assessment_basis_source_limit`，UI/Host打包各0次、未启动、模型0；[首F原件](../../../artifacts/mr-release-b2b4-15/build-execution.json)保留。定位闭包重复增长及精确限额后修复，既有8192/16MiB/256MiB和原件认证保持；新真实报告未获P。

## 15b：具体assessment维护修复后标准启动P

assessment仅一生产源晋升：原8794项→当前元数据621项/约29MB；历史selected完整SHA与cache守门保留，8192项/16MiB单文件/256MiB总量不变。4唯一控制P：首次2P+2 fixture F，适配后仅补2P，首轮原件保留。 [assessment封签](../../../artifacts/market-risk-implementation-20261007/B2/assessment-manifest-limit15/source-seal01.json)、[单源晋升](../../../artifacts/market-risk-implementation-20261007/B2/release15b/promotion01.json)。15首错assessment_basis_source_limit发生在打包前，不是SDK未启动；首次2P+2 fixture F原件及[15首F永久原件](../../../artifacts/mr-release-b2b4-15/build-execution.json)保留。

正式15b标准构建/启动P：9434源/41产物/1982928B，UI/Host各1exit0，3health200/7online/schema0081；真实新完整报告门F。[15b标准构建](../../../artifacts/mr-release-b2b4-15b/product-validated-builds.json)、[15b实际启动](../../../artifacts/market-risk-implementation-20261007/B2/formal-start15b/receipt01.json)。 实际assessment module-load预检P，loaded manifest hash ce48f003…ded26c7a，文件SHA9bde0874…7123794。历史selected原SHA/cache门不省略，不自动扩大限额。

15b普通PC每资产各一次请求已终态：BTC run_4fddd3c278184b5fa5b44cb41b35026a failed/noartifact，原生首码dsh_provider_input_transport_timeout（09:06:41.575 UTC），worker 09:07:02 UTC的dsh_terminal_conflict为次因；ETH run_e664bc33a2614be19ca1cab6247e890e degraded/art_7ba93e84737c49d6862db618b34b7521，native failed首码dsh_provider_emission_transport_timeout（09:06:38.969 UTC）。ETH有降级artifact不等完整报告P，正式15b真实新完整报告门F；唯一六GET原件正由Core核对。 下一步只等既有请求映射/终态，再核同Run完整质量/上下文/案例/consumer/PC。[15b的16工程门](../../../artifacts/market-risk-implementation-20261007/B2/release15b/ENGINEERING_ACCEPTANCE15B.md)G01新P，G03新报告门F；G05/G08/G11完整同Run链仍未通过；其他named历史范围复用，不授整产品/金融P。自动复查/真实Q05/U01及固定自然观察分门。


## 15b本轮实操补记

[唯一六 GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh15b/execution01/receipt.json)已一次执行，总体F、source稳定；BTC无报告、ETH仅degraded材料且context404。[PC15 五组/45 原件](../../../artifacts/mr-pc15/actions01.json)实际通过失败原因、保留证据/正文、原附件SHA、返回和事件类别筛选；综合因果论据与时间窗口输出缺失，完整报告仍F。

[15b实际验收与下一批核心修复](15B_REAL_ACCEPTANCE_AND_NEXT_FIX.md)给出四项具体候选、原件边界、正负控制和合批后同Run验收顺序。旧失败不重试，条件实际改变后再用普通按钮创建新研究。
