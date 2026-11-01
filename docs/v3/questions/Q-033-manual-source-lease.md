# Q-033：单parent使用现成默认full-pin源码句柄

- 状态：DECIDED，2026-10-24；依据用户自主决策授权及原sameRun唯一新实测5.6261252s仍FAIL原5s门。不是缓存权限或降低时限。
- 只读依据：D:/DecisionHub-v3-development/temp/legacy-reuse/q031-source-lease-applicability.md（f0279142…497e5）及同名pins；Q031现在正常路径仅一个真实prepare_originals parent。现成callback_source_lease_scope需要显式传递，单套with无效；其默认full-pin完整字节HASH后持有Windows真实拒写/删HANDLE，每次仍检查原inventory/作用域及新鲜路径身份。
- 精确实施范围：D当前已验证的perpetual_manual_execution.py、perpetual_manual_preparation.py、perpetual_paper.py及必要新增tests/decision_product/test_perpetual_manual_source_lease.py。原callback_source_lease.py、original_preparation.py、DB.session/仓库SQL-CAS、旧测试断言、Advice、前端只读。新单测support仅为400/60限制必要时拆分；先定具体文件再改。
- 最小接线：normal execute_intent在原terminal/idempotent早退后持有一个同线程scope，显式manual→helper→paper→唯一prepare_originals source_lease；使用默认full-pin，不defer、不extend、不seal选择。原前/后鉴真、联合读集/业务hash、writer当前authority/account/CAS、clock/quote age不删不缓存。None/非Windows保原full-hash和原调用形状；entry_at不移到source capture后。
- 生命周期：scope在writer Database.session外层，跨实际COMMIT或rollback返回后再finally关闭；ACK unknown时保持到完整session退出，不自动重发。不能helper返回前close、跨intent共享、跨线程或伪造lease/PreparedOriginals。terminal旧no-fill parent保持原路径，不冒称全流程只有一个parent。
- 必要验证：先真实RED证明未传lease/提前close的接线错误，再GREEN；正常commit、rollback/取消、ACK unknown仍持锁、过后释放、重复intent与独立两intent、关闭后消费拒绝、None路径兼容和原quote/member/readset负例。真正Windows原生句柄只操作新D私有源/DB，不锁改正式库或生产文件做破坏测试。纯stub不能冒实际Windows生命周期认证。
- 先单测/必要静态/非作者review/精确提交，之后才按旧Main3SHA匹配/备份/受保护端口无监听接回；必要时新未profile控制包真实身份核验原sameRun一次。旧63pin因Q032测试集成已历史化，不能重写它或盲重跑。未实测不称5秒成功；本问题不授权改lease库或放宽完整来源鉴真。
- 重测试单队列；当前Q032已终态，Q033可以接续。真实长期目标及19项新增/变形静态提示仍保留，不自行ACCEPTED/宣告交付。


## 2026-10-24实际闭合：本地候选，性能未达

- 本地源码提交`e6ab691`，生产三源按旧SHA/备份精确接回Main本地工作副本，未部署服务/正式库。默认full-pin、唯一parent、跨物理COMMIT/rollback/ACK未知及None原2args接线均保持；原lease库、SQL/CAS、时钟、hash、原5秒门和旧断言未改。
- 局部RED9FAIL/3PASS→12PASS；新15＋旧38共53PASS42.59s，测试辅助拆分后仅新15再PASS6.80s；新test/support2文件strict0。真实Windows范围只是小D私有源/HANDLE/临时SQLite＋明示合成no-fill业务边界，不能外推完整Main/native/实际PAPER。源码非作者C0/I0，原件见[scope](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/scope-final.json)及[review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/runner-final-nonauthor-review.json)。
- Ruff结果按上下文披露：Main配置＋虚拟Main filename五文件0；根裸D检查新test/support有2个I001，生产三源0，[上下文收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/ruff-context-addendum.json)。Main真实同配置三源strict1129→1130；新增唯一旧checkpoint未知类型，已记录、暂不改冻结源；Q031旧19项亦未清，不报整体strict0。
- 新`D:/DH-q033-same-run-20261024T0727`控制包唯一实际执行exit1：6.0007603999983985s失败原5s门，pytest1failed73.69s。65pins前后同、453Main origins、1个D临时DB、5loopback（3bind/2connect）、denied/child/HTTP空；未到研究/native/报告，没有真实Host/Provider或金融资格。
- Main三源保留为已审本地候选，不把本次结果称性能采用/成功；与此前5.6261252s单样本不作因果差额。旧63pin包不改不跑，后续不得盲重试。详细原件和未清边界见[本轮报告](D:/DecisionHub-legacy-incremental/docs/v3/reports/INCREMENTAL_MANUAL_SOURCE_LEASE.md)及[实际独立review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/transport-runtime-review.json)。官方Host独立原链路执行器另行准备中，长期目标ACTIVE。本问题记录实施与实际失败，不自行ACCEPTED。
