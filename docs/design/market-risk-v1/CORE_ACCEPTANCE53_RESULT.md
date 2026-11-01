# 53实际验收与54必要修复

53历史验收时点：2026-10-10 21:05（北京时间），发布在途补记为21:32。此页原失败、PC F及在途快照保留；当前正式54和后续真实结果见[54实际验收](CORE_ACCEPTANCE54_RESULT.md)。目标active，手机排除；原八层33项和指标保持。

## 1. 标准发布与启动：通过

原52标准STOP、原生selection核对与完整capture通过；12份封存材料覆盖停机后49条原生选择，0业务写入。53合并15源，原65守卫/配置/协议保持；一次标准UI/Host构建、完整三映射读回、原授权核对和正常START通过。当前schema0091，未安装0092异步audit。

[标准构建](../../../artifacts/mr-release-b2b4-53/product-validated-builds.json) · [完整读回](../../../artifacts/market-risk-implementation-20261007/B2/release53-postbuild01/postbuild53_02.json) · [Ready：3健康200/7新鲜心跳](../../../artifacts/market-risk-implementation-20261007/B2/release53/ready53_01.json)。启动通过不证明业务交付。

## 2. BTC同原报告：失败，修复进行中

Run `run_4061aaa1cd134013abfc60f7f6c698de` 的真实DSH执行与降级摘要保留。53一次正常inspect eligible后，按原request/gen9/version12执行唯一CAS进入queued/gen10/version13；自然Worker认领gen11后produce实际失败，原完整用量材料被 research_usage_transport_original_invalid 拒绝，异常类builtins.ValueError。Saved和独立ACK仍不存在，不开放完整成功声明。

已定位当前私有用量模块引用的DshSessionResult与normal原件Reader返回类不同；候选仅修outer CurrentLoader的闭合DTO导出边界，原七条transport认证、inner archive、Frozen IO、租约/CAS、预算均保持。独立审查同时核其余nominal边界，避免逐个类盲目重建。真实BTC完整材料尚未成功形成，有限机制控制不能冒充真实material replay或Saved/ACK。

[正常inspect](../../../artifacts/market-risk-implementation-20261007/B2/release50/btc-inspect53_01/receipt.private.json) · [唯一正常CAS](../../../artifacts/market-risk-implementation-20261007/B2/release50/btc-retry53_01/receipt.private.json)。私有诊断仅保存在原件中，对用户页不暴露命令、配置、令牌或堆栈。

## 3. 实际PC动作：具名范围通过及发现问题

| 动作 | 实际结果 | 证据/后续 |
|---|---|---|
| 复查子任务、问题详情并返回 | P：显示“研究运行身份核验未完成”，不再错误泛称准备；13证据/7取证保留，返回同一父BTC | [截图](../../../artifacts/mr-pc53/followup-runtime-copy53_01.png) / [详情DOM](../../../artifacts/mr-pc53/followup-details53_01.dom.txt)。实际复查执行仍F，首内部异常未知 |
| 打开研究列表并等待读取完成 | P：立即显示“正在读取报告与任务…”，读取完成显示已有8项任务并可进入同原BTC；加载提示不再空白 | [加载截图](../../../artifacts/mr-pc53/research-loading53_01.png) / [完成DOM](../../../artifacts/mr-pc53/research-loaded53_01.dom.txt) |
| 重载53首页，查看30分钟曲线 | P：29原分钟/1缺口，明确“存在分钟数据缺口”，展示本次时点与原件数量；不插值 | [截图](../../../artifacts/mr-pc53/timeline-gap53_01.png) / [DOM](../../../artifacts/mr-pc53/timeline-gap53_01.dom.txt) |
| 打开资料默认列表 | P：测试原件默认隐藏，已核验测试资料数量可见 | [DOM](../../../artifacts/mr-pc53/library53_01.dom.txt) |
| 显示全部、打开测试资料 | P：明确测试标识，研究使用区禁止新市场研究，保留原虚构文本回看 | [DOM](../../../artifacts/mr-pc53/library-fixture53_02.dom.txt) |
| 测试资料质量提示 | F：另一质量区仍写“可选择用于新研究”，与用途禁用矛盾 | 54 Panel候选按已核验用途分支；用途未知/失败不宣称可用 |
| 选中资料后的读取中状态 | F：已选中却显示“选择一份资料” | 54候选新增“正在读取原文与处理结果…”；实际加载复验待 |
| 正常FOMC原文搜索2027 | P：3个真实匹配；点击2027 FOMC Meetings定位原749段，页范围726–750/770 | [定位截图](../../../artifacts/mr-pc53/library-locate53_01.png) / [DOM](../../../artifacts/mr-pc53/library-locate53_01.dom.txt) |
| 定位结果可读性 | F：可见metadata暴露完整document URI/hash | 54候选可见页码/段号，完整URI留title；原版本/key/onLocate不变 |

一次CUA用组合innerText作为按钮accessibleName失败是验收定位错误；正确按钮名点击后真实定位P，不将工具失败归因产品。没有勾选新研究授权、提交研究或修改原资料。

## 4. 其他真实失败与未通过

另一当前Run `run_050ce46b6e1e442da6d549be0e3dbdee` 的原session_stopped记录为research_method_binding_failed/cancellation_projection_failed。旧Worker日志没有内部异常类或栈；provider_attempts空字段可能为默认值，不能据此声称没有调用Host。53收尾修复不等于已证这个未知首因消失。后续从原授权范围捕捉可信callback阶段/class，再修具体异常，保留原失败。

待完成：同原BTC正常Saved/独立ACK/三接口与PC下载一致；新合法研究和批准fetch；自然自动研究/复查；剩余PC按钮及用户手册；异步完整audit、用量和已证性能问题；原评价、来源与黄金准入。全部按[33项清单](IMPLEMENTATION_CHECKLIST.md)结项，不把候选和页面长度作为用户价值。

## 5. 54发布在途（21:32）

三必要源已完成控制和独审；标准STOP、停后49条原生选择全字段核验、原12份材料覆盖及3源晋升P，0业务写入。原件未新增，无需重新capture。唯一标准构建已开始，尚未产生54实际build/启动/Ready，不提前标P。后续沿同一BTC报告正常Saved/独立ACK/快读下载及PC复验。

## 6. 54后续实际（53原件保留）

54标准构建/读回/START/Ready与资料用途/加载/title URI/末页返回实际P历史保持；随后已正常STOP，55必要后继待合并/构建/启动，schema0091。停前同BTC inspect为queued/gen18/attempts16/version21、eligiblefalse、Saved/ACK0，无新CAS，不称当前仍在执行。单次无locals栈见FrozenReplay旧quality/context/policy重复全源核验，不能证明无限loop或完全fix；180仅lease，不是执行deadline。本页53 PC F/gen11失败和首次Ready/inspect F保留。详见[54结果](CORE_ACCEPTANCE54_RESULT.md)。
