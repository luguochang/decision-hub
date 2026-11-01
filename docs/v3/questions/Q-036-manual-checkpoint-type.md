# Q-036：补全原手工执行 checkpoint 类型合同

状态：DECIDED，2026-10-24；依据用户自主修正文档和保留原系统的授权。只清除 Q033 新接线暴露的 checkpoint 类型缺口，不扩成旧模块重写。

实证见 [既有单项诊断](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/main-strict-single-delta-readonly.json)及[本轮只读分类](D:/DecisionHub-v3-development/temp/legacy-reuse/q031-q033-typing-debt-readonly.md)。原 execute_intent 的 checkpoint 无注解，新传给已正确声明 Callable[[], None] | None 的 lease 接口时产生 Unknown 诊断。原 Q031 的结构性19项不是所有消息新增的完整统计口径，不能把本卡成功等同只剩19项或整体strict0。

## 精确范围与保持项

唯一生产路径为 `packages/kernel/decision_hub_kernel/application/perpetual_manual_execution.py`。D 中仅从 collections.abc 导入 Callable，给 execute_intent 的同一 checkpoint 参数补 `Callable[[], None] | None`；默认值、函数体、调用位置及异常传递保持。该模块已启用 postponed annotations，不增加运行时业务分支。

不改其他 checkpoint 接口、测试断言、quote/issuer/source guard、SQL/CAS、资金/clock/5s门，不引入 cast/Any/ignore 或改变 strict。Q031 其它类型边界另按实际合同处理。报告只写本卡实际范围和诊断差量。

## 验证与集成

先对当前 Main 的原 Q033 三源同配置执行只读 strict，保存 RED 消息与实际源SHA；D 不完整，禁止拿其实验依赖当真实 Main。候选检查 AST：除一个标准库导入和一个参数注解，完整运行时 AST相同；Ruff 使用实际 Main 配置/filename。此纯注解更改不编写镜像实现的运行测试，也不重跑无变化的整链/旧UI/native。

独立复核后精确本地提交一生产文件；按旧SHA、新SHA、备份及受保护服务检查才接Main本地源码。以相同 Main 三源配置执行 AFTER strict，逐完整消息多重集查差量；如新增非预期诊断则回退该单文件，不能用总数减少掩盖新错。未运行/旧诊断仍明确保留，目标ACTIVE，不新增ACCEPTED。


## 实施结果

单生产文件＋卡已提交9461308；09:10:11Z核1ac旧SHA/d83新SHA/备份后接Main。真实同cfg三Main源strict1130→1125、完整message新增0/移除5，checkpoint目标消失；exit1及旧债仍保留，不称strict0。仅postponed annotation/import，完整运行时AST不变，Ruff0/根非作者C0I0，未跑无变化业务。详见 [Q036报告](../reports/INCREMENTAL_MANUAL_CHECKPOINT_TYPE.md)。
