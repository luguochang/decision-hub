# 原PAPER单次读快照改造报告

## 任务信息

- 任务：Q-031，依据Q-027原系统渐进交付裁决，不冒认真实PAPER或T-803验收。
- 执行者：根智能体及具名实现/非作者审查者；日期2026-10-24。
- 状态：本地提交并受控集成，原5秒门仍失败；目标ACTIVE，未最终交付。
- 本地实现提交：`71225da`；前置逐字三源基线`73c544d`。未推送。

## 执行摘要

保留原前端、业务入口和原测试断言。把entry、account、observations及实际检查到的quote前缀放进现成prepare_originals的一个真实RO事务；Advice提取同session读取，仍按首个eligible停止，不预解码坏尾部。报价由真实reader签发并绑定parent、成员、业务值身份及完整hash，写前与写后均检查；不缓存owner/Run/权限/SQL授权，不修改仓库CAS或5秒时限。

新增单测38项通过，随后仅类型/格式修订定向4项通过；这组单测显式替换sourceguard，只能证明具名D临时SQLite/合成原件边界。真实Main旧12定义展开14项，12通过、2失败，失败在旧夹具构造先于Advice查询。新的原sameRun实测5.626125200011302秒，仍在原5秒门失败，未进入研究POST/native/报告。

## 交付物清单

- `perpetual_manual_execution.py`：1693行，SHA `bfa5a2d19703aaee2f9e06f6db1fc42b9b9bc5ae74f3216589a7ae59b00e52ef`。
- `perpetual_advice_query.py`：482行，SHA `d3a3faee20ae3f869489a906dfbd19cbdd3fc021bc015146741f7930cfd6dcb5`。
- `perpetual_paper.py`：683行，SHA `8ee70f0cb938c3ada5a41470675ec8261a626aaeebdeed30c8338009badd5144`。
- `perpetual_manual_preparation.py`：97行，SHA `02b366382d343c95320671d6a18d4d63e07426d04d2e9b12f8937b8ce99f8acd`。
- `test_perpetual_manual_preparation.py`：396行，SHA `4cff416bde3b9d460b866e149159f6a09b91f6c0c1f5193efda636e7b7337d23`。
- `manual_preparation_support.py`：162行，SHA `a55eb00536d25bbd58326e3a3e1ac8261fccc37a7b15179f648e3eded8d69940`。

完整冻结清单：`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/manual-single-ro-bundle-repair/scope-final.json`，SHA `7cade231a326885f8d27cb1e6592d6cc4ad7c7f0ce84c8fd06bfa13e90fce034`。新增测试support为守400行/60行而拆出的同一夹具，不扩大生产范围。

## 验收命令执行结果

```text
<Main python> -B <manual-single-ro-bundle-repair>/run_tests.py -q
38 passed in 46.55s
<same command> -q -k <affected annotation/format cases>
4 passed, 34 deselected in 5.53s

<Main python> -B -m ruff check --config <Main>/pyproject.toml <six exact owned paths>
All checks passed!

<Main python> -B -m pyright -p <pyright-main.json> --outputjson <three real Main paths>
baseline: 3 files / 1565 errors / 0 warnings / 2.286s / exit1
candidate: 3 files / 1385 errors / 0 warnings / 2.207s / exit1
<same command> <real Main perpetual_manual_preparation.py>
1 file / 0 errors / 0 warnings / 0.636s / exit0

<same-run-manual-snapshot-control>/run_regressions.py --execute <explicit roots/newD work>
2 failed, 12 passed in 260.55s (0:04:20)
actual exit1; before/after 63 pins equal; 448 origins all Main; denied=[]
2 failures: fixture persisted_publication -> lawful_packet_for -> packet_for
KeyError: 'review_originals' (test_perpetual_native_graph.py:70)

<same-run-manual-snapshot-control>/run.py --execute <explicit roots/another newD work>
MANUAL_EXECUTE_WALL elapsed_seconds=5.626125200011302 status=filled
assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
1 failed in 74.78s (0:01:14)
actual exit1; source_pins_unchanged=true; denied=[]; native children=[]; research HTTP=[]
```

真实原件：`evidence/legacy-same-run/manual-original-regressions-20261024T0627-root/`及`manual-snapshot-control-20261024T0633-root/`，均相对于D:/DecisionHub-v3-development。原12定义含一个三参数读集用例，故展开14项。前者有15个临时loopback事件和13个D SQLite目标，不可写成无网络；后者未到native的结论由原输出和观察者支持，不能仅凭post-test验证跳过flag推断。

## 技术决策与集成

Main旧三SHA匹配、新helper不存在、8047/3097无监听后，逐字备份三旧源，再只接四源；备份与逐文件before/after见`evidence/legacy-sdk-review/manual-single-ro-main-integration/receipt.json`。端口原件另存`manual-single-ro-bundle-repair/ports-before-main-integration.json`。未部署原服务、换原profile、改前端或读写正式库。当前Main四源与提交一致；binding仍2844facd…e94ca，recipe仍5ce02ead…0f9d16，D的Q029/Q030实验未重新采用。

新控制包`temp/legacy-reuse/same-run-manual-snapshot-control/`的5基础模块与旧timing-control逐字相同；63具名pin含真实Main新四源和选定旧回归依赖。full_readset_observed与directory_enumeration_observed仍false；不是全依赖闭包或OS强沙箱，没有D源码overlay/假__file__。全部旧失败封包不改写、不重跑。

## 测试覆盖说明

具名RED/GREEN覆盖首个不合格/第二个合格、坏尾不预读、未来cutoff、联合行/范围漂移、真假parent/选中member/hash替换、跨engine/read-session、reader退出与取消异常、inner业务值就地/换件、无quote分支、写后漂移回滚，以及entry_at在source capture前取得和preparer返回交界突变。owner/Run/settings的联合读集用例使用简单SQL夹具，不冒称真实完整权限链。真实旧用例补证单quote API、CAS/幂等、原件变化、余额不足回滚、clock bracket、签名与exact-entry cutoff等；两项Advice夹具仍失败。

## 已知问题 / 偏离

- 原5秒门尚未通过。5.626与此前21.527来自不同单次源码对照；可陈述实测差异，不作统计收益或单项因果承诺。合成业务clock每次+1ms，filled不证明真实墙钟5秒后仍可成交。
- 原三大模块不是strict0：1095/285/185变为950/256/179。独立对照仍保19项新增或变形静态提示（union/Optional/推断/private），未识别新增可达运行时P2，但不等于新增诊断0。仅新helper真实Main strict0；无全项目/v3本轮重验声明。
- 初次pyright include绝对路径的配置失败、D82条缺namespace/误解析Main旧API诊断、错误首次RED和SQL-only post counterfactual均保留，未伪称正确装配产品失败/历史首个TDD。最终正确命令为Main cwd+CLI具名路径、include空数组。
- 原38后的类型/import归一和两行最终layout分别有原字节/AST证据；未宣称最后字节又跑38。Ruff最后两行断行保完整AST精确等价，旧scope c797和peer收据另存未覆盖。
- Host仍仅staged-resolver/no-Host；没有真实Provider/PAPER、14天/四周认证。全部实际长期指标未达，目标保持ACTIVE。

## 下一步

Q-032仅修旧Advice夹具的真实Task/material catalog接线，保持原断言，不重跑整批14。原sameRun性能下一步先只读评估现有CallbackSourceLease在单一parent上的实际COMMIT生命周期与开销，禁止把TTL/stat或默认defer/seal当授权缓存，也不重跑本封包或抬5秒门。


## Q032后续局部完成

两个夹具失败已独立定向2PASS79.77s并本地提交4d9966e，仅测试接回Main；旧14项原失败结果不改写。当前整体5s结论仍FAIL5.6261252s，详见INCREMENTAL_ADVICE_FIXTURE.md及Q033最小后续scope。
