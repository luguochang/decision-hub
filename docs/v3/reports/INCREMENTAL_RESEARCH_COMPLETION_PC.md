# 原PC完整研究版本读取候选

2026-10-26，D隔离候选；未采用Main、未部署、未新增真实研究。实现沿用原报告组件和JSON/HTML导出入口，没有更换前端。

## 改动与原件

生产范围仅 `apps/decision-desk/src/api/perpetualSavedReport.ts` 和新增同目录 `perpetualReportDelivery.ts`。前者按source.v5/packet.v1及source.v6/packet.v2显式配对；后者校验新delivery的身份、提交时间、六份输入hash、原件目录、独审内容及引用，金融和执行权限仍为false。旧V5分支及原概率、事件、Action验证继续使用。

完整状态须有approved鉴真引用、非空wrapper hash、显式findings.v2四项passed及原件、三份内容去重关键证据和一份反证；incomplete也不能删除已有授权的wrapper hash。仅内容完整状态不表示真实模型语义、实际PC/重启或最终六条件全部合格。

新增两个定向测试文件和两个fixture。fixture来自Task B原native完整链在私有SQLite中保存、再由原SavedReportQuery读回的对象，字节复制，未编辑状态或hash：

| fixture | 字节 | SHA256 | 原文件 |
| --- | ---: | --- | --- |
| saved-report-delivery-native-incomplete.json | 495706 | bed2ba2c911dae48e2a3dd115da8483e57c362e2445fcef5d184ae553f00dc6d | temp/research-completion-task-b/native-v6-final01/test_original_native_assessmen0/saved-report.v6.json |
| saved-report-delivery-native-complete.json | 516009 | 38d20d2ee0d0d271d4f4bd75acd32e363f535300360ffd1790b771754544e641 | temp/research-completion-task-b/native-v6-final01/test_original_native_assessmen1/saved-report.v6.json |

这些native原件使用合成child输出，没有HTTP、实际模型或真实研究资格。

## 实际验证

- 初次RED：旧PC版本literal拒绝V6，1FAIL/13PASS；Root terminal84079 exit1。随后新decoder绿色，保存日志。
- wrapper hash移除负例实际RED：1FAIL/18skipped；原错误解析返回成功。修复后仍拒绝自重封外层hash的该篡改；RED原日志 `temp/research-completion-pc-wrapper-red.log` 保留。
- 最终Root terminal8463 exit0：原报告23项、原Action35项、新delivery19项，合计3files/77PASS、Vitest 12.82秒，日志 `temp/research-completion-pc-related-final.log`。原旧报告用例React act warning保留。
- 最终Root terminal99308 exit0：在原PC目录运行 `node --max-old-space-size=8192 node_modules/typescript/bin/tsc -p tsconfig.json --noEmit --pretty false`，0诊断；原tsconfig未降低。该工具调用终态已实际观察，空输出没有生成Tee日志，不补造原始stdout或时长。
- 测试使用受控fetch/jsdom，实际解析两份原服务fixture；原组件显示正文和原按钮；JSON/HTML保存完整source/artifact，HTML内隐藏JSON与阅读快照一致、活动外部资源0。未启动浏览器/API、未执行真实点击下载或重启，不将其表述为端到端验收。

## 采用前条件

本切片依赖Task A新typed合同和Task B新V6提交/查询。六文件scope及测试/fixture来源在 `temp/research-completion-pc/scope.json`，独立审查后才可精确提交、采用及新normal认证。

另外，Main+982当前对真实旧07的原查询已实测失败，原因是7b7d历史实现未归档。这个兼容性问题必须独立修复；保留旧V5 decoder、当前合成旧报告回归通过，都不能覆盖该真实失败。既有07原件不改，仍不计合格研究。

## 非作者复核

`/root/master_status_audit/content_review` 已核原scope `4c5af4d21aa984819345e0dba8f9aaf3f9a95b395ad0181c8ec6ebdcb98b373c` 的7pins、fixture原件、旧分支及自重封负例，结论C0/I0/M0。复核未重跑suite、未访问数据库/API/网络。PC核wire一致性；native wrapper来源鉴真仍由原server完成，非空hash自身不构成独立来源证明。新增本段后仅报告hash变化，最终清单另存同目录scope-final.json，原scope保留。
