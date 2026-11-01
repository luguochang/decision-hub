# 原manual单parent源码租约：本地接线与实际失败

- 日期：2026-10-24；本地源码提交 `e6ab691`。状态：已审本地候选，原sameRun **FAIL**；未部署、未作为性能成功采用，项目目标ACTIVE。
- 生产仅 `perpetual_manual_execution.py`、`perpetual_manual_preparation.py`、`perpetual_paper.py`。正常路径显式把默认full-pin lease传给唯一parent，scope持有到writer实际COMMIT/rollback退出；原terminal/idempotent早退、entry时钟、联合原件/SQL/CAS/业务hash及原5秒门保持。None仍按原两个位置参数调用preparer；没有defer/extend/seal或权限缓存。
- 三源已按旧SHA核验、备份后精确接回Main本地工作副本：manual `1ac620b1…3b77`，helper `954f40f5…d802`，paper `ac4cc3b7…515c`。这是本地源码集成，不是原profile/8047/3097服务或正式库部署；保留候选不等于性能采用。

## 局部验证及边界

| 原件 | 实际结果 | 口径 |
|---|---|---|
| [RED](D:/DH-q033-232f35ed8410/red.log) → [首轮GREEN](D:/DH-q033-232f35ed8410/green-first.log) | 9FAIL/3PASS5.91s → 12PASS5.75s | 缺传租约/未持锁等先失败，原件保留 |
| [新15＋原38](D:/DH-q033-232f35ed8410/owned-and-preparation-green.log) | 53PASS42.59s | 原quote/member/readset等38断言未改 |
| [测试辅助拆分后](D:/DH-q033-232f35ed8410/final-owned-green.log) | 15PASS6.80s | 未重复原38；最终五pin前后相同 |
| [新test/support严格检查](D:/DH-q033-232f35ed8410/strict-tests-final.json) | 2文件，0诊断 | 不外推整个旧模块strict0 |
| [Ruff上下文](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/ruff-context-addendum.json) | Main配置＋虚拟Main filename五文件0；裸D检查新两测试有2个I001 | 生产三源0；两种上下文分开披露，没有改源迁就不完整D分组 |
| [Main before](D:/DecisionHub-v3-development/temp/legacy-reuse/q033-main-before-strict.json) / [after](D:/DecisionHub-v3-development/temp/legacy-reuse/q033-main-after-strict.json) | 1129→1130；manual950→951、paper179不变、helper0不变 | 整体strict0未达 |

新增唯一诊断是manual:843 `reportUnknownArgumentType`：旧无类型`checkpoint`传入已标注的lease回调。根已决定本轮不改冻结源，后续集中补`Callable[[], None] | None`；[逐项只读收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/main-strict-single-delta-readonly.json)。Q031原先记录的19项新增/变形诊断也未清，不能把两轮合称总共只有20条，更不能声称新增0或whole strict0。

真正Windows句柄测试仅操作新D小私有源及临时SQLite，使用真实Consumer/HANDLE，验证写/删/父目录改名拒绝持续到物理commit、rollback和after_commit ACK未知之后，并覆盖释放、重复/独立intent、关闭后消费、取消、None兼容、SQL/inventory漂移及跨线程拒绝。业务身份/adapter为明示合成no-fill边界，不是完整Main来源准入、实际PAPER成交或DSH/Provider证明。D不是完整Python克隆，失配的相对包名检查不作Main结果。

冻结[scope](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/scope-final.json) SHA `962b8d56…6fb89`；非作者[源码review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/runner-final-nonauthor-review.json) C0/I0，SHA `04ed2f5f…712f`。局部GREEN没有改变以下实际失败。

## 唯一新sameRun结果

原用例为`tests/decision_product/test_perpetual_current_account_consumers.py::test_settled_v3_full_material_prompt_writer_advice_manual_and_frozen_history`。新65pin控制包一次执行：

```text
MANUAL_EXECUTE_WALL elapsed_seconds=6.0007603999983985 status=filled
assert elapsed < 5  # 原门槛不变
1 failed in 73.69s; actual exit_code=1
```

原件：[result](D:/DH-q033-same-run-20261024T0727/result.json)、[stdout](D:/DH-q033-same-run-20261024T0727/pytest.stdout.log)、[preflight](D:/DH-q033-same-run-20261024T0727/preflight.json)、[after](D:/DH-q033-same-run-20261024T0727/after.json)、[observations](D:/DH-q033-same-run-20261024T0727/observations.json)、[guard](D:/DH-q033-same-run-20261024T0727/python-read-set.json)。65个显式来源pin执行前后逐字相同；453个报告的模块origin均在Main；只观测到1个新D SQLite目标和5个临时loopback事件（3bind/2connect），`denied=[] / children=[] / HTTP=[]`。空完整读集并非零文件读取：本控制包的`full_readset_observed=false`及`directory_enumeration_observed=false`。

`filled`只是原合成fixture返回；原helper:254的5秒断言失败，调用测试在:51停止，未到后续研究、native验证或报告，未运行真实Host/Provider/实际金融成交。[独立实际结果review](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-source-lease-repair/transport-runtime-review.json) SHA `2cb8ed2c1dda882f1baaf1787482974e268dbedf853d584dee5f46b9e88c2e0c`。

此前Q0315.6261252秒、本次6.0007604秒均为失败的单样本；不能据差额作因果性能结论、回填成成功或报金融延迟。三源暂保已审本地候选，未部署；不重写旧63pin历史包、不盲重跑、不改时限。官方Host原链路临时执行器独立准备中，本报告没有Host成功结果。六组合/14天/四周/实际PAPER等长期验收未达，目标保持ACTIVE。
