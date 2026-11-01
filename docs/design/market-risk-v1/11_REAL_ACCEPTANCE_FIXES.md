# 11 正式验收发现与有限修复

2026-10-08；PC。11标准build/READY通过，功能仍分项验收，目标active。

## F11-ADMISSION：主动请求误计自动额度

ETH `riskresearch_eaf4f8edaaafe281465da82ee7463797177fe1fe`、BTC `riskresearch_4cc9c0c6d8726cc6caf5768d457df89970f7692d`各普通页面保存一次。两请求attempt3 failed、run_id=null，原码`research_admission_budget_exhausted`。真实trigger是manual，完整原payload/hash包含owner-command和owner；`risk_research_admission._admit`却硬传automatic，触全局24h自动次数12/12。Study小时额度各1/2并未满。

[实际只读原件](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh11/admission-budget-observation01.json) SHA `e2524d2399b2290e5e1ab6393fbeeaf3a4d122a5ef367f01c2fd1e22a5f6b6a0`。这是代码缺陷；本次没有创建Run，不能拿它证明DSH源码遍历修复已成功或失败。

修法：按已经核验的原trigger payload/hash/request/scope/instrument/source_ref和server写owner识别人工触发；共享guard默认仍automatic，仅人工不受自动次数门误挡。保主题并发、Host未settled、暂停、节流、租约和实际费用未知。现owner入口只校验header存在，不夸大为账号身份认证。

控制：合法人工正链；自动额度仍拒；错owner/source/hash/scope拒；并发、Host与暂停仍拒。保11失败原件及所有attempt；不重置历史或另加专用恢复旁路。修后标准新发行每资产一次普通新请求，记录与11失败的关系。

## F11-DOWNLOAD：稳定blob链接仍未确认下载

11同版旧ETH质量正常显示，中文缺口和结构化依据可读，原JSON/hash不变。原生DOM链接有稳定blob URL和正确filename；实际`download`事件与浏览器支持的下载API各等待20秒均超时。不能再认定立即释放URL是唯一原因。[页面](../../../artifacts/mr-pc11/old-eth-quality01.txt)、[首次事件](../../../artifacts/mr-pc11/download-event01.json)。

修法：`GET /v1/research/runs/{run_id}/report/download`复用现有质量原件读取，只返回available且绑定该Run的`full_report_json`原UTF-8字节。attachment安全文件名、application/json、quoted ETag原SHA；未发布/不存在404，原件损坏409。访问范围与原quality一致，不把owner凭据写URL，不重新序列化，不改旧资格。前端沿实际APIbase链接该endpoint。

原ETH字节295467、SHA `a75bcf8fbbae231d35e61c9d1f5b4dc021cf479adbe43d52d5ca2f12bfbfea93`。私控已有1次真实原04副本完整下载读19.672秒及5具名控；这不能代替PC实际下载。正式PC等待依据后端全门耗时采用45秒，核实际文件和SHA。

## F11-COPY：准入阻断原因隐藏

现`waitingUntil`已有pending状态guard，保留它；只修budget错误藏在details而用户只见泛化等待/失败的问题。pending显示额度阻断和最早再检查时点，failed显示已停止及原因；最早检查不等于预算届时恢复。不改变提交或配额，不给unknown错误编原因。

## 一次合批与验收边界

三组受影响源及负控冻结后，标准stop、保留11全部原件、精确晋升、一次build/start。11没有任何实际V3选择原件的只读观察另存[release12原件](../../../artifacts/market-risk-implementation-20261007/B2/release12/actual11-selection-observation01.json)，不制造选择记录；旧真实选择与读取原件仍保留。

新发行BTC/ETH各一次普通请求；存在Run且真实终态后才一次六GET核完整报告、ReviewV2、usage/未知USD、publication/case及受影响PC。原已读恢复已P，不重做未改功能。新报告与实际下载未过前，不授全产品通过；固定观察/统计/外部来源缺口独立保留。
