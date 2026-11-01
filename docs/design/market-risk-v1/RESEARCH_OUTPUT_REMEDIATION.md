# 研究输出与可用性：具体整改及验收

更新：2026-10-10。原MR15–19/MR32–33；46已正式安装3份源码/测试（1生产文件），migration0/restore0、schema0090；标准UI1/Host1构建P，validated SHA75e0be98…。两既有模型资格一次156.687秒published_confirmed，原180秒门内完成、未重拟/自动重试；独立原生只读2.515秒核对两资格及首次confirmed事件version1，明确serving配置已生成。结果协议一次18.016秒登记P，独立0.141秒精确核对protocol/acceptance/48机会并准备独立授权；窗口2026-10-10 UTC00:00–23:00，原28日不变。Ready46_03在2026-10-10 00:00:09.901155 UTC三200/七fresh在线P；首两次warming F保留，连续健康未证。实际PC首小时刷新2/48按时、46等待、unconfirmed0；首UTC00:00双资产14条原生、90秒、独立ACK及按时publication已核P，后续23小时/成熟标签待，不手动回填。45b旧ETH39正文/原字节浏览器附件恢复P仅为历史验收；quality30.641秒性能F。00:00首个自然双资产原生记录、90秒、独立ACK与按时publication已核P；≤0.5秒持锁/成熟评价未证，详见当前一页纸。自动研究恢复、报告R2完整材料闭包/正式确认、全PC及金融效果未过；全产品/全按钮/金融false。42结构新真实Run/完整用量/独立复核仍待；报告R2仅有限DEV控制，正式closed material未证。

## 1. 真实问题与已经核实的结果

本次不是 SDK 无法启动。39的真实 ETH 请求 `riskresearch_e76265cee05030faf339f57cf28c915f88079749` 从PC一次提交，Run `run_dbce68caf06e41f799c5a2b5a6d1c8ce`，已保存14证据、1独立复核和308477字节报告。请求完成、研究结果为 `research_only`，不是已校准预测。

| 项目 | 真实证据 | 结论 |
|---|---|---|
| 报告提交 | 三次候选JSON结构失败：causal_case命名错误；link必填字段缺失；invalidation缺失；随后进入复核并成功提交 | 功能最终成功，但多轮猜格式浪费时间 |
| 第四次“失败” | 原始DSH工具结果为 `decision_hub_final_review_required`，随后有真实review和成功submit | 正常复核检查点，不能误判成最终研究失败 |
| 内容质量 | 事实、上下文、报告complete；方向、路径风险、执行blocked；6项反方仍未解除 | 工程保存成功，金融资格未通过 |
| 使用体验 | 实际quality GET200 / 22.343s；download200 / 45.032s；PC附件与API和quality中的原报告逐字节一致 | 原件可核验，读取性能不合格 |
| 对话用量 | 已认证20调用：主16、协作4；native delegation/lifecycle census不完整，可信定价凭据缺失 | 总分母、总Token和费用unknown；不以已知子集冒充全部 |

原报告 SHA：`f3c1629fd171f4b21e03afcfb6829515325d91cb4c051dfdb1644bff6211c523`。原报告、每次公共回执、工具失败原件、PC下载和截图保存在 `B2/release39/eth-actual39_01`、`eth-terminal39_01` 与 `artifacts/mr-pc39`。

## 2. 42：模型看到实际嵌套合同，停止逐次猜字段

**根因**：`extensions/dsh/decision-hub/src/synthesis-tool.ts` 的模型可见参数原来只有通用 `candidate: json` 和几句描述。执行时才用生成的Zod合同检查完整结构。严格检查有必要，错误在于调用前未向模型提供相同的嵌套字段。

**具体实现（已安装44并标准构建，后续真实Run待验收）**：

1. 新增 `src/synthesis-tool-schema.ts`，从 `researchSynthesisCandidateSchema` 递归投影DSH支持的object/array/oneOf/null/enum/const和必填字段。结构随实际codegen合同变化，不维护另一份手写模型。
2. `synthesis-tool.ts` 对模型暴露完整结构，明确horizons位置、link的confirmation/invalidation、时间格式和缺失事实处理。
3. 原始参数仍原样送入Provider认证；原有仅表示层的去重/位置修复、严格Zod值校验、可信Evidence ID核验、冻结候选、独立复核和最终Gate全部保留。投影不能替模型填写因果链、编造证据或标记概率已校准。
4. 这是未来调用的改造。39原失败、原候选、原报告均保留，不能改写成没有失败。

**验收**：必要8控P：实际嵌套字段可见；缺失invalidation在任何Provider/来源调用前拒绝且不补值；新增字段/range/date仍拒绝；完整原候选及原normalization兼容。初次额外复核suite因现有DSH peer包不可解析F保留。whole Host tsc基线26诊断、候选修复后26诊断，无新增诊断；整体类型门仍F，不能用差分P冒充整体P。44一次标准Host构建已P；仍须在真实后续研究原件中核对模型实际收到新结构及提交结果，不能只凭构建或控制宣布完成。

候选封存：`DEV/artifacts/market-risk-implementation-20261007/B2/synthesis-schema42/candidate-source-seal42_01.json`，SHA `82c4f79e93c1d7dcc366dfeae6518138983c8abc3d4a31aab038407b41370a77`。

## 3. 43：报告读取按实际瓶颈整改

**可复用定位方法**：只读实际已发布Run；新物理连接、SQLite mode=ro/query_only/NullPool，不触发HTTP、模型或修改SQL。诊断先使用正常39受管构建的已验证receipt初始化历史注册和当前V3安装。最初两个诊断进程遗漏这两项安装绑定而F，原件保留；不是产品运行故障。

第三次实际profile完整核验成功，报告SHA与上述一致，3611条SQL、native changes `[0]`。cProfile下总查询51s（有测量开销，不能当作HTTP耗时）；重复JSON Schema形状检查占约17s/14628次，provider原件读144次、generation读11次。原始trace/profile留在 `B2/release39/quality-profile39_03`。

**已验证并暂缓的第一步**：在 `research_quality_view.py` 当前同一物理只读范围内启用既有 `binding_contract_base.shape_validation_scope`，只复用本次调用已通过的纯JSON形状与实际validator/schema指纹。所有数据库行、当前原件hash、来源文件、发布/终态/selection/sidecar/provider依赖和首尾验证继续执行。不能跨HTTP复用权限、旧读回执或错误结果；不能以TTL/mtime代替hash。被缓存的形状不能证明模型理解、费用完整或金融资格。

**实际结果与决策**：相同真实Run、同一完整只读核验，形状复用后的诊断查询49.375秒，原51秒；3611条SQL、native changes=0、原报告SHA一致。13项必要控制通过，但达不到≤2秒的工程目标。候选源码和首失败已保存于DEV的 `B2/research-quality43-held01`；质量读取生产源码已恢复为正式39同字节。本次组合发布不包含此性能候选，不再反复打磨同一种缓存。

**下一步技术方向**：质量与原报告应共用一次发布时保存、独立确认的材料包；按精确publication读取，在线核验材料包及当前访问许可，完整历史复核作为明确的审计操作。须先冻结依赖集合、撤销/未知提交、旧版本有限dispatch及实际首次读取合同，再做有限实现；原件发生变化必须阻断，不能简单删除核验或把旧报告当作最新判断。该方案尚未实现。 详细模块、数据合同、实现顺序和R1–R5门见[报告服务具体设计](REPORT_SERVING_REDESIGN.md)。

**验收**：成功结果与未优化原件的语义/原报告字节一致；JSON/schema/发布原件改变必须失败；回滚重开、替换连接、跨线程与消费者修改后不能返回成功；只读native changes=0。实际PC在首次读取、再次读取与浏览器附件各保存原始耗时和截图。工程目标质量和下载核心读取≤2秒，超过则保留F。这是新增可用性目标，不能替代原O04 p95≤0.5秒。

## 4. 输出层把使用边界放在结论旁边

真实报告主张“区间轻微偏多”，但关键方向反方未解除。首屏必须同时明确这是尚未通过资格的研究假说，再给出支持和反向解释；不能让用户滚动很远才发现blocked。完整主线、证据、审查、后续验证和下载入口均保留。

**UI已发布**：首屏资格说明和中文术语随44安装并保持于45b；原报告/URL/ID不改写。必要55控/类型P与旧测试首失败保留。45b同ETH39质量/正文及浏览器308477B原件恢复P，原安装校验未放宽；新Run/完整PC/3真人未过。

PC验收核对同一Run和原任务：初次加载未知时不冒充可用；research_only报告明确资格未通过；正文、展开反方、质量、证据链接、复查计划和附件可用。现有三次真实用户的可用性门仍待，代理自测不能替代。

## 5. 用量与数据缺口单独处理

真实用量缺口代码是 `provider_native_delegation_census_unavailable`、`provider_native_lifecycle_census_incomplete`、`provider_pricing_authority_unavailable`。下一步从该Run已保存的native census及Host生命周期采集链确认首因，补未来真实采集和终态确认；旧publication冻结用量不回填。没有可信价格时费用继续unknown，不根据模型名字猜价格。

来源缺口是产品能力问题：market_narrative无可执行审计获取路由；宏观只有水平/5日变化，缺共识、发布时点、修订与surprise；主要衍生品场所和持续流动性数据不足。对应原L1/L3/L4方案要逐个补真实provider、落库时间语义和来源准入。已采集很多数据不能证明有效信息充分，也不能靠放大Token预算补齐不存在的数据。

## 6. 当前执行顺序与停止条件

1. 已做：39报告/PC/附件与profile，42结构/43 UI必要控制；44安装/9表0090；45b8源码/测试、migration0/restore0、UI1Host1/Ready三200七fresh。8captures/838Runs/122native精确安装覆盖P；同ETH39正文与原字节浏览器下载恢复P。43缓存仍HOLD，不在发布内。
2. 46资格156.687秒在原180秒内发布确认，V3原门保持；首UTC00:00双资产14原生/90秒/独立ACK/按时2项P，后续23小时/持锁/成熟评价待，45容量首失败保留。
3. R1实际完整native材料采集P：一次60.313秒、3685查询/336去重、26表/374行/547源码、SQL未解析0/changes0，原报告与decision SHA不变；5981次Python文件读取中仍有python312.zip与6份dist-info METADATA未attest，完整闭包/受信重放/serving仍UNKNOWN。补最小runtime资料描述并受信重放，再执行R2正式确认/R3控制/R4真实≤2秒/R5共存；不重跑同条件采集。自动研究DEV候选已封，真实新Run/完整PC待。
4. 未验证的代码、自然24小时标签、完整用量、金融效果和真实用户门保持待验收。出现首个新失败就记录并修该首因；不无限重试、不重放过期时点、不新增同类模型反复拟合。

总目标保持active。工程实现、候选控制、正式部署、真实联调及金融有效性分别给结论，避免再次把“有产出”写成“已交付”。

## 7. 44升级后旧报告兼容首失败

旧ETH39质量读取409/2.484秒，独立只读首因为research_graph_recipe_v3_selection_changed（2.406秒、native_changes=[0]）；保存recipe b154…不在44 active registry且当前1d024…不同。精确身份原件证明saved/current recipe不同，不能沿用“同graph”的假说；不是SDK/网络故障。官方retainer遗漏原recipe的原因正在独立静态审查，45必须恢复既有受信原recipe注册与精确读取，不放宽旧原件、版本或source guard，也不重新生成旧报告。2.484秒返回失败不是质量性能通过。

[新Ready](../../../artifacts/market-risk-implementation-20261007/B2/release44/ready44_02.json) · [PC首失败](../../../artifacts/mr-pc44/eth39-compatibility-first-failure44_01.png) · [质量409回执](../../../artifacts/market-risk-implementation-20261007/B2/release44/old-quality44_01.json) · [独立只读首因](../../../artifacts/market-risk-implementation-20261007/B2/release44/old-quality-firstcause44_01.json) · [recipe精确身份](../../../artifacts/market-risk-implementation-20261007/B2/release44/recipe-identity44_01.json)


## 45b实际状态追加

45b实际PC从“管理与审计→历史数据与预测能力”进入#/history-status，既有令牌routine续期成功；BTC/ETH各87天、low44/high43。23:00UTC两个小时槽均显示首次条件不足、历史模型资格尚未确认或未授权；无概率/生产权限/正式前瞻批次。具体中文与历史首次状态语义P，未核验新预测或全按钮。错误#/history是操作路径错误，不登记产品路由缺陷。

自动研究只读诊断：BTC最新请求绑定维护取消Run，Run和DSH link均cancelled且真实terminal_at，取消并非未恢复回执缺陷；ETH最新自动请求在22:38:26UTC已3次failed/study_host_terminal_unknown、run_id=null，页面保留未确认。需完整Host inactive证据/可审计恢复路径及未来普通研究/自然触发验收，不改旧失败、不自动第四次重提。

报告原字节兼容P、quality30.641s性能F与新Run未验保持，不能因历史状态说明可用而宣称自动研究闭环完成。
