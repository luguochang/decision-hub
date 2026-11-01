# Q-032：旧Advice发布夹具补齐真实材料目录

- 状态：DECIDED，2026-10-24；依据用户自主处理不合理文档及原系统渐进修复授权，不等待重复确认。
- 实证：原12定义展开14项，12PASS/2FAIL260.55s。两个旧Advice用例在persisted_publication:115→lawful_packet_for→packet_for:70缺review_originals，先于current；未据此宣称Advice回归或全回归通过。原件保留于D:/DecisionHub-v3-development/evidence/legacy-same-run/manual-original-regressions-20261024T0627-root/。
- 精确范围：先把Main当前tests/decision_product/test_perpetual_advice_query.py逐字基线保全到D并独立提交；仅修其中persisted_publication夹具材料构造。必要可新增tests/decision_product/perpetual_advice_fixture.py承载有界支持，不改已有业务断言。生产源、packet_for严格索引、时限、材料身份/概率/权限/比较缺失拒绝只读。
- 最小方案：公开ResearchTaskService.get_in_session重用同Run真实Task；确实没有才用现成factory_for/factory.build创建同Run原件。通过生产read_perpetual_research_materials_in_session取得规范materials及真实review_originals，再喂原lawful_packet_for。不得claim/prepare_execution/native/伪catalog/空close_window/default或另一Run请求；原已存在manual夹具提供参考，但避免重复claim/DSHlink。
- 先保原两失败RED，再仅跑这两个受影响用例；available/expired/foreign/newRun不回退及缺comparison仍拒的原断言全部保留。受控新D工作目录/DB/日志/产物，真实Main依赖；不为此重跑整批14、原同Run或未变UI/native。D单测物理路径/装配边界须明报。
- 本地提交、必要lint/类型和非作者review后才允许精确备份/旧SHA匹配接回Main这个测试fixture；原5秒业务源不因夹具修改被宣称通过。本问题不批准源码句柄租约的新实现；那需要先按实际结果收口最小scope。
- 详细只读归因及8源pins：D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q031-advice-fixture-diagnosis/。


## 局部完成

基线d880eaa，修复4d9966e；2PASS79.77s、24原assert不变、helper strict0。旧Main6abcf16匹配和新helper absence/备份后仅两tests接回Main，未声称另跑Main用例或14全过。首次WORK setup及两attempt间Ruff修订均保实录；详见INCREMENTAL_ADVICE_FIXTURE.md。原5s仍失败；接续Q033，不重写旧63pin封包。
