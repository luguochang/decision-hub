# Q-046：Host验收观察窗与原任务deadline

状态：执行者已依据用户最新授权裁决；无需等待外部审批。此裁决只作用于新私有诊断控制器，不改产品或旧运行事实。

用户要求自主判断文档与实际情况，保留客观问题、继续主流程。原AGENTS的机械停问及不得改变任何测试操作窗口与本授权冲突时，按本次实际证据作有限裁决。

## 已有事实

- 新正常构建9fa3ff27…391f2b及final314c3741…8ed522实际PASS；无需再次构建。
- q043nh05原父进程worker.wait(timeout=240)于15:23:02超时，FAIL原样保留；它没有运行到collect/assert。
- 原session45records，4个tool result无业务error且对应4个step/end，第5步15:23:01.831开始。原Run/Link仍running，没有报告/终态；因此不能说已成功，也没有证据证明死锁。
- 该Run已冻结Task的scope.deadline_at=2026-10-24T15:49:34.097134Z，模型单步600000ms；Task30m与父harness240s不同。Worker lease240s也不是研究总期限。
- 最终P50<=15min/P95<=30min是实际统计门，不等同于每次验收脚本只能等4分钟；真实金融、14自然日、四周等门全部保留。

## 裁决和执行边界

1. 旧nh05仍FAIL，不更新原session/DB/时钟/租约/Task，不复用它来冒新成功，不自动重试付费运行。
2. 新fresh受控回放只改变私有父观察层。先沿原accepted60取得就绪条件，再从同run_id的唯一research_task_request/v2持久输出读取真实scope.deadline_at及身份。没有Task、链接不一致、截止过期或时钟异常即失败，不补默认deadline。
3. 提取时剩余必须为正且<=1800秒，固定对应monotonic端点；不在轮询时刷新、滑动延长。前置观察与后段分开记时，admit至最终结果的真实总时长完整保留。
4. 产品API45、安装300、模型step600000ms、lease240、Taskdeadline及原业务assert不改。没有新增重试、假资料、资格、source/SQL/CAS/auth例外。
5. 观察更久只为取得真实终态，不宣称性能改善。超过研究15/30分钟统计仍按实际计入；到原Task期限仍无结果即如实失败，不再扩大观察窗口。
6. 只新D私有控制器/新attempt/动态source pins；不修改Main、SDK、旧工具包、正式DB/profile/pointers。完整Host正例、恢复、原UI实际HTTP/浏览器及金融长期资格均须另有真实证据。

## 证据

- [正常构建final](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q043-runtime-baseline-normal-build/final-receipt.json) SHA314c3741ac9b4237d06a81163c80e54e818fd9d5db5ba3aa69f48275dc8ed522。
- [nh05失败](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh05/failure-proof.json) SHA1a1c01836276df7038dcafa7e82b645a162c095424ed5285544ffcae0838c2d9。
- [独立RO](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/official-host-q043nh05/session-readonly-review.json) SHAa83b41bffb6d8abea21171e92a5de7136dced45514ee7d64330d8a2b796ac4ac；DB和原session前后SHA同，Root原compressed跨frame解码45条/219495B/SHA b1cfba86187d211876e6dbdea5bd27b8f5f2420610e3ef4b0e80048aea41d754一致。


## 执行前细化：直接等冻结原件，不添加accepted60前置

Root代码审查结合原nh05 Link.accepted_at=15:20:00.751与Worker观察约15:19:02起点，发现accepted已近59秒；机械沿用60秒会另加过窄前提及每0.1秒HTTP轮询。此处只读提取Task截止，不授予任何产品权限，因此本节覆盖上文第2项的accepted60选择。

新私有observer仅在原240秒前置上限内每0.5秒mode=ro读同Run Task/Method/Exec/Link。仅精确“冻结原件尚缺”可继续，数据库、原hash/canonical/schema/identity/deadline异常、时钟回退或Worker已退出全部立即失败；不吞异常、不造默认值。原件齐备即使用其原deadline和提取时remaining<=1800固定mono端点，前后阶段及admit总耗时分别记录。产品accepted由原Worker/SDK继续执行，所有产品时限、模型预算、租约、金融门与原断言保持；这不是研究性能修复或原失败翻案。此细化在首次nh06执行前完成，无新增用户审批前置。
