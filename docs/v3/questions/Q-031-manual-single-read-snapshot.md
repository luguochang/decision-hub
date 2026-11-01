# Q-031：原PAPER一次真实读快照聚合

- 状态：DECIDED，2026-10-24，依据用户自主判断和原代码渐进修复授权。目标仍是原业务可用，不降低报价5秒或长期真实验收。
- 依据：Q029/Q030组合实测原5秒门失败27.2320451秒（pytest1failed94.43s），不足以支持采用。已将Main两文件逐字恢复为binding2844facd…e94ca、recipe5ce02ead…0f9d16；两项独立提交和全部失败材料保留在D，不再追加文件系统微调或盲重跑。并未据单样本宣称各补丁的因果收益/回退幅度。
- 实施范围：D中当前Main逐字基线的`perpetual_manual_execution.py`、`perpetual_advice_query.py`、`perpetual_paper.py`，必要时新增`perpetual_manual_preparation.py`承载有界聚合；对应新增`tests/decision_product/test_perpetual_manual_preparation.py`。原`persistence/original_preparation.py`和`perpetual_paper_repository.py`、原测试断言、前端、sourceguard/SQL/CAS只读。若证据表明确需扩scope，先记录具体理由，自主形成最小修订，不擅改金融门槛。
- 决策：使用现有prepare_originals的一次真实RO事务收齐entry、account、observations及原顺序实际检查到的quote前缀，首个eligible后停止；不把五个现成prepare再套进去另开session。Advice需提取current_in_session；报价读取是已存档原件解码，网络采集在独立collect方法，不得为合并引入新联网路径。
- 保真边界：原eligible/entry/cutoff/deadline与各quote clock bracket保持原取得顺序，不能统一取更晚的now。writer重新检查当前owner/Run/设置/账户版本/capacity/权限/时效及CAS；全部联合查询/范围/不存在/ORM行由原prepare_originals收集。选中quote必须绑定真issued parent、实际selected member及完整quote hash；保仓库require_prepared_quote和前/后验证，禁止裸quote、伪造子PreparedOriginals、扩大volatile masks或缓存授权。
- 必要RED/GREEN：首个quote不合格/第二个合格、尾部坏quote不预读、未来/陈旧clock、cutoff/deadline边界、无eligible提前返回；联合读集任意owner/Run/设置/账户/quote原件或范围漂移在writer拒绝；parent/member/hash换件拒绝；幂等、未知ACK、取消与异常原样回滚。原已有5秒业务测试完整保留，最终仍以真实无profile受控用例终态为准，不用调用次数或合成测试冒充实际PAPER。
- 装配：D并非完整当前后端克隆，D的Q029/Q030仍是未采用实验代码。单测明确装载具名D候选并只读当前Main依赖，禁止伪造`__file__`；依赖必须是Main已恢复2844/5ce，不能无意混入D实验binding/recipe。单元验证不冒称真实源码认证。Main接回须具名验证、非作者复核、旧SHA前置匹配和字节备份后进行，8047/3097/正式库不部署不改写。
- 详细只读建议：`D:/DecisionHub-v3-development/temp/legacy-reuse/manual-single-ro-bundle/decision.md`及同目录6源pin。新增代码有界、无新依赖/默认假数据/静默异常，报告既存静态诊断，不用新抑制掩盖。


## 实际结果（2026-10-24）

范围仅为守新增模块400/函数60拆出tests/decision_product/manual_preparation_support.py，未扩大生产四源。提交71225da并受控接回Main；38单元GREEN、最终类型/格式定向4GREEN，六源Ruff0，新helper真实Main strict0；旧三源仍1385诊断且保19项新增/变形，不报全0。真实旧14items12PASS/2夹具FAIL；原sameRun5.6261252s仍FAIL原5s门。未ACCEPTED或金融交付；日志/备份/peer及下一步见INCREMENTAL_MANUAL_READ_SNAPSHOT.md，夹具单独按Q032处理。
