# 原Advice发布夹具修复报告

## 任务信息与摘要

- Q-032，2026-10-24，本地提交4d9966e；逐字Main基线d880eaa。局部完成，不代表项目或金融验收。
- 只修persisted_publication夹具并新增28行publication_materials支持。重用同Run真实Task，没有才用原factory.build创建，通过生产reader取得规范材料和review_originals。未claim/prepare_execution/native或补假catalog。
- 原24条assert及全部其他函数AST逐项不变。两个原失败定向通过，原14项批次没有重跑。

## 文件与集成

- tests/decision_product/test_perpetual_advice_query.py：270行，SHA c1f68a0627a2abb65bdd50286778f1e681cb3c4d0810379219a2d255e1762885。
- tests/decision_product/perpetual_advice_fixture.py：28行，函数14行，SHA 367f69c7d3c52b7c57ffbbbd75f9690079639708a05ca6d7d22c5f25b0eb336a。
- 旧Main test SHA6abcf16f…55db6b匹配、新helper不存在并备份旧字节后，仅接回这两个测试文件。receipt：D:/DecisionHub-v3-development/evidence/legacy-sdk-review/advice-fixture-main-integration/receipt.json。未改生产四源、前端、profile、服务或正式库。没有声称集成后另跑Main两项；已测D物理文件与接回字节相同。

## 验收命令与真实输出

```text
<Main python> -B <temp/legacy-reuse/advice-fixture-repair/run.py>
<D physical test_perpetual_advice_query.py>::test_persisted_v2_publication_originals_available_expired_and_no_foreign_or_old_fallback PASSED
<D physical test_perpetual_advice_query.py>::test_missing_published_comparison_cannot_fall_back_to_snapshot_summary PASSED
2 passed in 79.77s
actual exit0; 11 pins before/after equal; denied=[]

pyright strict <actual D perpetual_advice_fixture.py, real Main dependencies>
1 file / 0 errors / 0 warnings / 1.524s
ruff/format new helper: 0
whole old test lint: baseline2 I001 -> candidate1 existing global I001
```

GREEN原件D:/DH-q032-5759c4bcd68c/，含stdout/result/before/after/python-read-set。生产及其余依赖来源Main，精确两处测试来源D，不能称全部origins为Main。原Isolation逐字复用，无sourceguard替身或假__file__。11具名pin不是全依赖闭包，目录与完整readset观察仍关闭；存在临时loopback，不称无网络。

## RED、边界与偏离

- 原失败记录仍为Q031旧批次12PASS2FAIL260.55s，两个KeyError review_originals均先于Advice current。这里的2PASS没有覆盖或取代整批14、真实native/Provider/PAPER。
- 首次长WORK在RawBodyArchive setup遇Windows路径长度错误，2errors且未进入新helper。原件保在advice-fixture-repair/attempt-27791afa07e64ac391db9c50d7294760，不当产品RED。第二次缩短WORK；两次之间也有Ruff imports/layout修订，两个D文件hash改变，其余9pins同。不能写成逐字只改WORK；最终GREEN本次11pins前后确实相同。
- 全局既存I001在夹具范围外保留；新helperstrict0不等于旧test或项目全strict0。生产Q031仍保19项新增/变形静态提示。
- 原sameRun唯一最新窗口仍5.626125200011302s失败原5s门，未到研究/native/报告。本次未重跑或降低门槛；14天/四周等真实长期目标未达且ACTIVE。

## 审查和下一步

源码冻结与原输出见evidence/legacy-sdk-review/advice-fixture-repair/scope-final.json；首次attempt口径修正单独保历史。非作者源码/24断言复核见q032-advice-fixture-final-nonauthor-review.json，运行来源复核另见对应transport收据，均位于D:/DecisionHub-v3-development/evidence/legacy-sdk-review/。

Q033按只读适用性证据开始默认full-pin现成源码句柄接线实验，保原SQL/CAS/owner/clock与原5秒门，不承诺性能。Main测试变化后旧same-run-manual-snapshot-control的63pin应历史性不匹配；禁止修改/重跑旧封包，未来按新源码另封包。Q031/Main生产四SHA仍bfa5/d3a3/8ee7/02b3，binding/recipe仍2844/5ce；D旧8352/1641实验未采用。
