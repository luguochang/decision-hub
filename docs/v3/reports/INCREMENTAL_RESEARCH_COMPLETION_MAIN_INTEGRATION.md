# 完整研究与历史归档的Main精确采用

2026-10-26接续：Task A、Task B、原PC读取及受限completed-prior归档四个切片均经非作者C0/I0/M0，已精确采用Main工作副本；未部署、未新增真实研究。

本地候选提交：Task A `327b6eb`，Task B `1caa6b3`，原PC `c9bbe15`，归档 `66bad52`。Main实际采用33文件，19份现有文件先核旧SHA并逐字备份，14份新文件先确认不存在；正式8047/3097无监听，installed pointer、DB、profile均未由本次操作改写。采用收据在 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/research-completion-main-integration/adoption-receipt.json`，SHA `a704566f1f291f58946de003631b6b75fbe690cebb36d255945cff521c08ac3a`。没有Main Git操作。

## 实际Main验证

| 范围 | Root终态 | 结果 | 日志 |
| --- | --- | --- | --- |
| 新独审、完整性及归档三组Python | 99932 / exit0 | 70 PASS，25.25秒 | python-focused.log |
| 原PC报告、Action及新V6读取三组 | 65891 / exit0 | 77 PASS，11.95秒 | pc-focused.log |
| 原native相关十五组 | 71019 / exit0 | 97 PASS，53.57秒 | native-focused.log |

日志均在同一采用证据目录。最后逐SHA复核33份Main文件不变，`main-focused-verification.json` SHA `cb30ce59941c08f4887060f34742cf33f260174a06cc7934f5a332250edecdc3`。旧PC用例React act warning保留。native首次只在不含Vitest的repo根检查依赖失败，没有执行测试；随后使用原插件自己的已安装Vitest，未安装或升级依赖。上述为协议与受控回归，不是实际模型/业务资格。

## 历史回读及下一步

真正7b7d历史实现已由原producer在D私有staging生成，481份源/合同原字节全核，原175注册保留并通过现成union得到176。首次driver在原producer成功后因普通Windows长路径读取失败，原exit1保留；纯continuation只验证已有输出，未再次执行producer，实际exit0。

原物理7b7d视图已成功在新一致副本读取旧07：1113条SQL仅BEGIN/SELECT、changes0，17个共同原字段和source/artifact实际UTF8字节全等，仅read_at按新实际时间排除。原件 `D:/DecisionHub-v3-development/temp/research-completion-legacy-retention-01/original-view-query-r02-result.json` SHA `5b6d2f7f4affe48bba3fdf6ee609e34b3be5e1d8bf5b1bff0fea9e57f933c552`。这尚未证明当前Main经新normal/archive分支兼容。

后续真正standard normal明确以已完成6dd为prior、installed仍49c，显式CompletedMaintenancePrior并经过完整原auth链。所需542份原输入已在Main新的artifacts隔离目录准备，每份均匹配原6dd source或artifact map，48份原artifact齐全。未改原receipt maps或手写normal；当前982不能认证新33文件。新normal和当前Main/archive分支实际旧07回读通过后，才能释放新的实际研究。

默认完整strict及最终质量门仍未通过；沿旧Main配置的零新增类型诊断、这些回归、源代码采用或正常构建都不替代研究六条件、六组合14自然日、两真实事件族、完整提醒后继、10笔PAPER退出结算恢复、四周评分或30完整失败回放。目标ACTIVE。
