# 自然24h恢复排队：技术结论与验收清单

更新：2026-10-13（本机日期；原证据UTC不改）。范围为原`run_da34ab255fd24741a6082f1795a78246`恢复排队。**83已正常发布并在线，本批原键恢复、保存与独立确认、预算、导出刷新与往返通过；整体核心与原33项仍未全部通过。**

## 83实际验收结果

同原Run/Freeze按保留的原请求键恢复，实际gen3/v13已确认，Saved1/独立ACK1精确匹配。计算116.484秒、保存16.062秒、总132.547秒，原120/120/270预算及完整退出通过，四次续租成功。完整JSON289534字节与已保存原件一致，阅读版全文和原缺口保持，刷新与父子报告往返通过。

本次POST实际HTTP200/61.8071秒，超过客户端45秒等待；页面保留未知并通过原键GET核对受理，没有新键/重复提交/重跑模型。该等待体验仍待整改。[合并实操证据](../../../artifacts/core-goal-20261011/pc83_01/pc-recovery-acceptance01.json) · [真实预算与退出](../../../artifacts/core-goal-20261011/pc83_01/natural24h-native-budget-proof03.json)。

## 82历史实证与原因（原失败保留）

| 已证明 | 结论边界 |
|---|---|
| 82状态GET 16.7649秒，旧读51.1382秒；原前端限30秒，独立诊断client限45秒 | 读取改善已实测，不改任何timeout，也不等于恢复完成 |
| 唯一恢复POST 409、29.0966秒；原gen1/v5未变，commands0、Saved0/ACK0，原键GET确认未受理 | POST具体首个错误无日志，仍UNKNOWN，不倒填后续诊断 |
| 原完整只读证明报`publication_retry_archive_ownership_bound_exceeded`；Source入口/出口一致、native/authority/timer退出，无写/无许可 | 独立证明当前排队前置合同被原归档累计预算阻断，不证明POST首因相同 |
| 元数据22条；排序前两份材料98,704,084 + 111,214,083 = 209,918,167字节 > 201,326,592字节（192MiB） | 各文件不是单独超限；全历史材料累计复核触界。实际材料读取字节未计量，保持UNKNOWN |

原`research_publication_jobs48.py:1181–1227`的`_retry_owned_archive_proof51`累计每个历史Saved的`material_bytes`（1204–1206），并调用原`_confirmed`复核完整材料。将“能否排队处理这一个失败Run”绑定到“复核全部已确认历史材料”，使无关归档增长也能阻断恢复，并重复付出完整材料读取/证明成本。不能将29.0966秒全部归于材料读；本轮未测阶段归因。

已有`_known_cancel_archive_ownership69`体现排队用途：检查原归档清单、快照和Saved/ACK所有权，不授其他材料服务许可。83已限定修改jobs48、公DTO、UI解析三源，**明确版本化V2排队用途**，保持完整审计和后续真实准备/保存核验；完整来源与构建读回通过，当前状态见[机器状态](current-core-execution.json)。

## 修复与验收清单

- [x] V2明确用途和版本；未知模式/字段拒绝，旧DTO/完整审计和known-cancel原路径保持，不能由caller布尔授许可。
- [x] 原Run/Freeze/归属/expected generation与version/CAS、原档案全清单、Saved/独立ACK/哈希/撤销/fence及入口出口fresh检查保持；孤儿、篡改、清单变化、已有保存、in-flight拒绝。非目标材料不因此获得读取许可。
- [x] 原16MiB bundle、192MiB单材料、32MiB manifest/native证明、4000计数、Source预算及原120/120/270不变；旧全历史审计累计预算和失败保留。Worker实际准备、回放、完整保存和独立确认仍核全部所需原件。
- [x] 正常导入候选测试：新增11项native控制及两项旧材料路径通过；旧known-cancel纯夹具绑定修正后单项通过，原13通过/1失败保留。旧新DTO/UI实际解析通过；不以测试替代生产许可。
- [x] 正式一次合批Source审查→正常捕获/晋升/构建/读回/Ready后，沿**原da34、原计划与父Run、原请求键**合法恢复；未知先GET回执，不重复POST、不另建手动Run、不重置库。实际受理/CAS→native退出→Saved1/独立ACK1及预算/原件→PC刷新/下载/父子返回通过。
- [ ] 保82两资产summary与timeline各50原门P、三报告重启原SHA不变；全G5 overlap仍UNKNOWN、PC剩余仍待。事件首renew失败、旧Source失败和原33项8/22/3不抹去。

证据：[原只读证明](../../../artifacts/core-goal-20261011/pc82_01/natural24h-original-proof01.json)（SHA`871168…5c03e`） · [22条元数据](../../../artifacts/core-goal-20261011/pc82_01/natural24h-original-metadata01.json) · [POST/GET时序](../../../artifacts/core-goal-20261011/pc82_01/natural24h-api-timing-result02.json) · [未受理原生状态](../../../artifacts/core-goal-20261011/pc82_01/natural24h-after-command-native01.json) · [独立阻断边界](../../../artifacts/core-goal-20261011/pc82_01/natural24h-recovery-blocker01.json)。
