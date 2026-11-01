# Q-043：在原历史报告中显示已发表方案原件

状态：DECIDED，2026-10-24。用户已要求复用原代码并自主裁决，执行者选择沿既有存储和界面补齐客观缺口。

设计依据：`saved-action-projection-plan.md`（Ddev/temp/legacy-reuse/original-host-next-step，SHA72790aa13239d23678981ab264a5d4c5a0c137b7624db331145b1ddb8dccd2b7）及来源清单fcc97aa2…041b。已发表Action的同run有真实持久比较/首选原件，原saved-report reader未投影；没有Action命令的run仍保持未知，不生成或重算方案。

## 允许范围（仅隔离D）

生产：原 `application/perpetual_saved_report.py`、`application/perpetual_advice_query.py`，新 `application/perpetual_saved_action.py`；上述application前缀均为packages/kernel/decision_hub_kernel。前端原 `apps/decision-desk/src/api/perpetualSavedReport.ts`、`src/product/PerpetualSavedReport.tsx`（同apps/decision-desk前缀），新同前缀 `src/api/perpetualSavedAction.ts`、`src/product/PerpetualSavedAction.tsx`。

新增测试：`tests/decision_product/test_perpetual_saved_action.py`、`apps/decision-desk/src/product/PerpetualSavedAction.test.tsx`；需要拆分传输负例时可新增 `apps/decision-desk/src/api/perpetualSavedAction.test.ts`，不因此删断言。允许私有D执行器/配置/证据，旧测试/导出helper/生成contracts/SQL-CAS/发行与交易writer只读。原saved_report Main3911a51d…e60e在D缺席，必须逐字基线并单独提交；其它旧源先核Main/D现有SHA，不混D旧实验或假造__file__。

## 实现和信任不变量

只同owner/run/artifact/admission/版本的确定publisher命令；已有repo/原件认证保留。原公开reader加默认False的`frozen_only`接缝，只在固定原件认证完成后、当前monitor/event读取前返回；默认Advice行为/响应不变。投影只取固定原件与真实command.accepted_at，不消费今天的status/gaps/event/updated_at，不倒填读取时钟。没有命令返回明确unavailable，损坏/跨线/不支持原件保持完整性拒绝，不能catch为missing或从当前Advice/设置补数。

研究published_at与较晚的动作accepted_at分别保留；同报告合成v2/v3 primary绑定和derivation/mapped/provider/admission/候选映射逐项校验。完整source_json/artifact_json与SHA不改，financial_qualified/execution_authorized保持false。新UI只在原section显示“发表时入选方案”、原精度概率/单位净收益/成本；旧缺字段与确实未发表两种未知区分。Q034 JSON/HTML沿原完整DTO/section导出，不另造renderer/auditbundle/导航，不加执行按钮。

新增函数≤60行、新生产文件≤300行；两个已超长旧reader仅允许这次小接缝/调用的明确例外，不借机重写。新代码保完整静态检查，不用Any/cast/ignore擦除金融类型；遗留动态边界需真实模型校验。按设计8组失败路径先RED再GREEN，必要原report/advice/export回归，所有未测或继承类型债诚实列明。不做无关完整556/98回归。

## 执行边界

Main的Q042构建正在运行，Main源码/产物/正式库/profile/pointers全部不写；新D候选完成测试、静态与非作者review后先本地提交。Root另行决定何时接Main、再取对应正常构建回执及真实Main/浏览器验收；当前8d79构建不能认证后来新源码。D是部分后端，不把单位用例/可信依赖替身当实际Main鉴真，更不得伪造来源路径；若源guard要求完整物理Main，隔离单位结果与将来真实Main集成结果必须分别报告，不能绕过后者。没有真实PAPER/付费模型/长期资格或项目完成声明。

## 2026-10-24 执行中按证据收窄及微扩展

- 允许新 `tests/decision_product/perpetual_saved_action_support.py` 仅拆现有 test arrange，使两个新文件各≤300行、新函数≤60；不增加金融宽松路径。
- 原 public `read_perpetual_published_originals_in_session` 可补真实 Session/RunRecord/StoredCommandReceipt/datetime/JSON 入参和 object 返回签名。原默认 `frozen_only=False` 不新增运行门、不改变旧行为；新 helper 自行核验 object 边界。D 静态装配落 Main 旧 API 的4诊断保留，须真实 Main 整合源验证，禁止伪造 __file__、改写导入的静态快照或假 stub 凑零。
- 原 UI legacy 缺字段与后端明确 unavailable 必须区别。旧 `PerpetualSavedReport.test.tsx:104` 的“本报告未保存动作盈利比较或首选原件”期望文案会把副本未附误称后台未发表，因此允许仅把这一条 expected literal 改为“此副本未附方案比较”。其余所有旧断言保持；金融未知、无 table、候选/导航/授权检查均不得删除或放宽。这是明确产品文案变更，不能通过保留矛盾句迎合旧测试。
- 允许一次必要原 native lawful 同 Run 纵向测试的私有装配：仅原1个 Vitest case，复用已审 Node/config guard，固定 fixture/cache 输出重定向至全新 D-owned 目录；Main 代码、固定 artifacts、SDK、原服务和正式库只读。原 issuer/auth/sourceguard 不改，实际失败留原件；不得拼另一 Run 的 Artifact 补全 view。
- 定向/strict/非作者核验后，由 Root 单独冻结准确 SHA、核旧 SHA/新文件 absence、备份并将本卡7生产源及必要具名 tests 与 Q044 codec 一并接回 Main 本地源码。prior49c 仍需的旧字节只 create-only 补入既有 original-sources；禁止覆盖旧原件。随后真实 Main strict/纵向读链、新 normal producer 与实际 verifier 各据实际结果判定，不因本节授权冒称已执行/部署/交付。
