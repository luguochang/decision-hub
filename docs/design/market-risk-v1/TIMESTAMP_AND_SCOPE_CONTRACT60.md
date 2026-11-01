# Root60 重试：时点、品种与研究意图合同核查

## 范围与结论

- 仅静态读取 Root60 已安装源码及既有公开响应原件；未访问数据库/API、执行模型、改产品或运行测试。
- Root 源码根：`E:/project/study/codex/dsh/decision-hub`。以下源码路径均相对此根。
- **旧 trigger cutoff 本身符合恢复合同**：子 Run 保留原事件，由该事件最早观察重新形成自己的触发快照；它不是本次实时行情截止，也不是复用父报告的证明。
- **未证实60恢复丢失品种/focus或转为永续研究**：58和59b响应已经显示同一 major profile 与空公开 focus；正式品种由 Study、现货准入、冻结 Task 与 Gateway 守卫绑定，不能靠 profile 后缀判断。
- **已确认公开说明缺口**：`research_focus` 只识别另一个手工来源；自动现货研究的真实意图虽进入观察文本，该字段仍为空。当前公开 DTO 也不能直接证明本 Run 的完整现货绑定与每个工具返回均正确。

## 已有响应证据

| 保存原件 | Run/关系 | profile / focus | 业务时点 |
| --- | --- | --- | --- |
| `artifacts/core-goal-20261011/eth-progress60_01/run.private.json` | `run_c382ba2fcab687814c16a533d921a3a4`，父 `run_14d1d0eddb86406f93a42f50e29bd88e` | `crypto_macro.manager.v1` / null | 创建21:56:00Z 10/10；trigger cutoff19:57:06Z 10/09；coverage cutoff22:02:34Z 10/10 |
| `artifacts/core-goal-20261011/btc-progress60_01/run.private.json` | `run_d7cb7d011a68b669f50f6a4ac9fb8305`，父 `run_3091c7d79ab27ea433ade65861f51d3e` | 同上 | 创建22:01:11Z 10/10；trigger cutoff05:05:54Z；coverage cutoff22:02:33Z |
| `artifacts/core-goal-20261011/eth-natural59b_01/run.private.json` | 上述ETH父 Run | 同上，原标题已是ETH现货未来24小时 | coverage cutoff20:54:12Z 10/10 |
| `artifacts/core-goal-20261011/btc-current59b_01/run.private.json` | 上述BTC父 Run | 同上，原标题已是BTC现货未来24小时 | coverage cutoff20:53:07Z 10/10 |
| `artifacts/core-goal-20261011/btc-linked58_01/run.private.json` | `run_dbce769c27973d121244b5ee4859535d`，BTC同事件祖先 | 同上，已是BTC现货标题 | 创建19:30:47Z，coverage cutoff19:32:31Z 10/10 |
| `artifacts/core-goal-20261011/eth-auto58_01/run.private.json` | `run_5085d5c2879340e793cf25672c66c1ba`，独立自动ETH事件，**不是60 ETH祖先** | 同上，已是ETH现货标题 | 创建19:35:45Z，coverage cutoff19:38:34Z 10/10 |

- `run_id/profile_ref/event_title/coverage_cutoff_at` 位于原件的 **`run` 子对象**；focus与快照位于外层。没有的 DTO 属性不能解释成数据库状态缺失。
- 两个60原件都在 researching，`decision_snapshot=null`、尚无完整报告；进行中的 coverage cutoff 不是已提交报告的最终分析时点。

## 正式恢复与资产合同

1. `packages/kernel/decision_hub_kernel/application/research_observability.py:600–699`：命令核对原拥有者/幂等回执后建立独立 child Run，保留原 event 与 Study；不复制父 Run 的 artifact、result、snapshot 或 DSH session。BTC/ETH先验证父 scope，再通过当前服务端 resolver，为 child 独立冻结新 spot admission。
2. `application/risk_research_admission.py:548–595`：自动研究将确切BTC/ETH现货未来24小时意图写入 `market-risk-automatic-research` Observation；建立 `exact_spot_monitor` Study，instrument精确BTC-USDT/ETH-USDT，horizon为30m/24h/72h。其 RunService 使用注入的正式 spot resolver；`application/run.py:123–178` 验证并冻结本 Run 准入。
3. `apps/hub_worker/research.py:1178–1192`：依据**本 Run 原生现货准入**选择 `spot_prepared_factory`，未配置即拒绝。`:375–421` 强校 scope及有效pack路径/字节，并对正式组件选择绑定 Run、scope、admission与definition hash；`:446–452` 依 Study instrument构造数据要求。
4. `apps/hub_worker/composition.py:747–774`：正常 spot factory使用 `packs/crypto_macro_prepared/1.2.0`，普通prepared路径另为1.1.0。`packs/.../1.2.0/profiles/manager.yaml` 真实版本1.2.0；`application/research_binding.py:237` **故意按major version生成** `crypto_macro.manager.v1`。Graph recipe v3是另一版本维度，此profile既不代表旧pack，也不独立授予spot资格。
5. `application/research_run_scope.py:245–323`：以本 Run 的验证原件构造受保护 admission/path上下文指针，含exact scope/instrument；它明确受控试用、产品/路径预测资格未通过，指针本身不冒市场事实或权限。
6. `packages/runtime_adapters/dsh_runtime/profile.py:543–576,665–676`：提示从本 Run admission读取exact spot asset，数据只用该现货、同资产永续上下文与对应日线；缺asset不静默换BTC。`packages/workbench_adapters/durable_research_gateway.py:1083–1155` 从原冻结 Task scope核对每次资产查询，其他资产拒绝；共同宏观因子仍可独立进入。
7. `application/market_scope.py:112–148`：常规spot开关未通过时，只有匹配Study的正式spot准入可走受控研究。市场页“待验证”与正在受控试用不矛盾；不能把这类执行宣称常规已交付。

## 四种时间口径

| 口径 | 真实来源与用途 |
| --- | --- |
| 原事件触发时间 | `application/snapshot.py:64–116` 取原event最早Observation.received_at；新快照ID含child run_id、created_at为本次冻结时间。原cutoff保留事件来源。 |
| 本次live执行起点/上限 | `apps/hub_worker/research.py:469–476,615–649` 重新冻结当前now与原30分钟deadline，live pit截止等于执行上限；replay才使用固定历史材料时间。上限不是未来事实已知时间。 |
| 实际采集与上下文可用时间 | `durable_research_gateway.py:171–201` live工具时钟由服务端当前clock、Link generation/deadline产生；`:495–498`记录真实服务端收到时间。`application/research_context_selection_v4.py:483+`每轮资格截止=min(live clock,原pit)，原触发时间不封死新数据采集。 |
| 分析/预测参考与最终决策时间 | Graph `agentic_research_graph.py:911,1110–1136` 初始取**全部请求输入**最大received_at，后续由真实round完成时间推进且不越原deadline/pit；最终决策快照绑定原Graph与材料。DSH `profile.py:358–393` live预测窗口以可信synthesis调用时点校验，不使用旧trigger或未来deadline。 |

- live历史窗口已有针对旧触发复用的修正：`dsh_runtime/profile.py:898–912` 当前决策用 `deadline - total_budget` 作为本次历史采集锚点，而不是原事件时间。
- `dsh_runtime/result_mapper.py:465–475` 最终coverage采用可信runtime完成时间及accepted evidence received_at；公开进行中coverage来自basis投影，不替代完整报告的最终快照。

## 尚待完整报告确认的产品验收

- 对两个60 Run 的已保存原件核对：实际Study/spot admission/effective pack、冻结Task instrument和request mode、context v3/Graph recipe exact binding、输入protected refs均属本child。代码守卫存在不等于此次原件已验收。
- 核对实际工具请求/原返回：BTC/ETH同资产、现货与永续用途分明、global macro独立、provider失败保持缺口；不得用标题或当前coverage时间推导所有行情都新鲜。
- 核对最终决策cutoff及30m/24h/72h reference/expires/review时间，证据发布时间/收到时间均在合法范围；旧事件背景与本次预测在报告中分开。
- 新报告必须有本Run独立result/Graph/决策快照及完整SavedACK，不能用父报告读取成功代替。本次静态核查不构成金融有效性或发布成功验收。

## 明确最小整改落点（本次不实现）

- **公开focus说明覆盖不足**：`packages/query_views/research/service.py:2413–2416` 仅识别 `decision-desk-latest-advice`。可为自动研究新增具名“研究任务/意图”投影，来自已验证Observation首行并标明自动来源；不要冒充用户手工关注点，也不要靠title授予scope。
- 页面同时显示“原事件时间”“本次研究起点/资料截止”“现货/资产/受控试用资格”，使用已验证正式绑定DTO；缺绑定显示未核验。当前无证据要求改恢复scope或模型截止规则。
