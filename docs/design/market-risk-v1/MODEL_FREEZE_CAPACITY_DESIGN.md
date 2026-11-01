# 模型原件冻结：容量合同与46实际验收

更新：2026-10-10。归原MR02/12/23/26/28/32/33，接续原八层33项与B0–B4；46 V4已正式安装3文件/1生产、标准UI1/Host1 P。真实双模型一次资格156.687秒published_confirmed、两首次确认version1；同一完整包232543272字节，未重拟、未自动重试。原180秒与V3全部512门保持。Ready46_03三200/七fresh在线P，结果48机会登记与独立授权准备P；首UTC00:00双资产原生/90秒/独立ACK/按时2项已核P，后续23小时待；金融false。下文尺寸/失败段落明确为45初测原件，不从逻辑字节推断页容量首分支。

## 1. 真实首因与尺寸证据

45时点的历史首失败：45b已标准构建并Ready；实际两模型资格确认105.953秒后以`history_qualification_retention_bound_reached`失败，发生在native copy容量门，未生成可提交bundle。不能仅凭同一错误码断言首先触发了单行上限还是SQLite页上限，更不能从回滚后12288字节文件推断峰值。

| 已测对象 | 完整原生数据，按现copy45口径 |
|---|---:|
| 同一数据集完整行 | 154344110字节，其中原TEXT正文154343617字节 |
| 119条采集任务全部17字段 | 14602799字节，最大行124546字节 |
| 两模型、训练记录、补采尝试与作业 | 已完整量长；前一取证共489行190331641字节 |
| 252836条行情 | 262902781字节，最大行1049字节 |
| 1063条抓取原件 | 500897字节，最大行477字节 |
| 两条关联V2原件 | 16920173字节，最大行16912704字节 |
| 全部所选254390行、去重后 | 470655492字节，约449 MiB；TEXT/BLOB实值468596556字节 |

没有已测单行超过512 MiB，不能写“大任务单行超过512”或“任务重复数据集已证实”。以上是逻辑数据；数值/NULL按现代码每格8字节，SQLite记录编码、页、主键索引与空闲页另计，**45取证当时实际native文件体积UNKNOWN**；46完整包元数据实测native572928000字节，不能据此反写45首失败分支。

独立原件文件每模型既有实测208395540字节/1061文件（39真实阶段回执）；两个模型共享后须按完整ref/hash去重，不能机械相加，45取证当时未完成双模型完整freeze/forecast；46资格freeze及首UTC00:00双资产实际forecast已确认，后续23小时/成熟标签待。数据集147 MiB正文已包含在上表，不能重复计入逻辑原值。

[489行只读取证](../../../artifacts/market-risk-implementation-20261007/B2/release45/qualification-native-size-forensics45_01.json) SHA `b6e803583a6d054c11e099f8bab6423421f8f70bf79952671a7b3b291b5a952c`；[剩余全集聚合](../../../artifacts/market-risk-implementation-20261007/B2/release45/qualification-native-size-complement45_01.json) SHA `7e40b5747c1c0fd0dabec1a8a9d12e4818fd5637f00fe03d0ce143d570f5ebf5`。两次实际RO取证分别0.953/10.25秒、原数据库changes=0，无原payload输出或模型/math执行。主键集合digest、各字段类型/字节、源头前后不变证据保存在回执。

## 2. 明确版本与资源合同

| 资源 | 原V3 | 授权46候选V4 |
|---|---|---|
| 既有512 MiB规则 | 原全部分支逐字保持 | 新版本独立选择，不能覆盖原V3解释 |
| native SQLite快照 | 原512 MiB | 768 MiB（805306368字节） |
| 完整解包材料 | 原规则保持 | 1 GiB（1073741824字节），累计实字节 |
| 此次自有暂存 | 原规则保持 | 2 GiB（2147483648字节），仅this_prepare自有native image、SQLite sidecars与pack |
| 最终压缩包 | 原规则保持 | 512 MiB（536870912字节） |
| ZIP生产方式 | 旧格式兼容保持 | `ZIP_DEFLATED`，level1；46完整包232543272字节 |

snapshot unpack cache另有自有临时native文件768 MiB上限，与this_prepare暂存范围分开；2 GiB不约束全进程、整个data目录、所有并存cache或总RAM。

V4生产和读回按代码固定合同与精确源码SHA选择，不从环境参数、旧回执或调用者任意提高上限；V3原件继续按原512规则读取。native体积、完整材料解包总量、this_prepare暂存占用、snapshot cache与最终包分别核验，不能用压缩后较小尺寸证明解包安全。

**不降低的门**：180秒完整运行、300000行、16 MiB新增小批物化、1 MiB流式chunk；完整native字段（包括作业17字段）、所有原件/hash/schema/类型/manifest；原source出口与只读边界、独立确认及ACK/锁/自然小时门。16 MiB控制额外小批物化，不承诺整个Python/SQLite进程RSS只有16 MiB。模型不重拟合、不减少样本、截断正文或省略proof来凑容量。

## 3. 有限模块与实现顺序

只改一份生产模块`K/application/history_model_freeze_v3.py`：增加版本固定资源合同；native复制/归档打包/读回按同一版本逐项限额；V4 DEFLATE level1；导出准确native/unpack/staging/packed计量。现有严格transfer proof、source出口、snapshot和consumer资格逻辑保持，必要变化必须进入完整源码seal。

1. DEV完成有限边界控制：V3原门与旧格式读回不变；V4各容量越界拒绝；ZIP总量/重复材料/损坏或缺原件拒绝；大TEXT类型与完整17列同字节、跨snapshot/source改变失败。
2. 仅运行既有双模型publish/read节点一次，记录所选行数、共享原件集合、四项真实资源尺寸、压缩时长、完整elapsed、原预算及所有首失败；私测P不算正式资格。
3. 封候选→必要源码/合同审查→收口自动研究并标准停机→冻结实际版本集合与覆盖gate→一次标准构建/Ready→一次真实两模型资格运行及保存后独立确认。
4. 资格通过后才显式提供serving授权，随后原90秒自然小时/≤0.5秒锁/预测ACK、48机会协议及完整分母分别验收；不能预先写预测或金融可用。

## 4. 产品影响与自测指标

| 门 | 通过证据 | 当前 |
|---|---|---|
| 完整材料兼容 | 原V3仍拒绝原越界；既有原件、类型、17列、source和manifest不变 | 有限必要控制及46真实完整双模型资格发布/确认P；新预测原件未独立验收 |
| V4真实容量 | native≤768 MiB、解包≤1 GiB、this_prepare native/sidecars/pack≤2 GiB、另snapshot临时cache≤768 MiB、包≤512 MiB | 实测native572928000B、独有解包795373141B、包232543272B/1054entries；实际通过固定门，staging峰值/RSS未测，非总进程上限 |
| 时效 | 一次完整两模型运行≤180秒、不重拟合、原行数≤300000 | 实际156.687秒P；拟合0、自动重试0 |
| 资格闭环 | 真实发布committed证据及独立读回确认；未知提交不得假定回滚 | 两模型published_confirmed、独立两首次confirmed/version1 P；serving配置生成P |
| 用户结果 | 授权后实际预测/小时/ACK/PC；性能与原金融门独立 | 尚未通过 |

该次可靠冻结已在明确资源预算内确认两模型资格；用户结果继续核验自然小时、原生预测与成熟评价。报告兼容已恢复，不需要用户重写旧问题；这项工程不会直接证明分析准确或金融有效。

## 5. 首失败、停止与恢复

首个控制/真实运行失败立即保存phase/耗时/四资源计量/source/bundle身份，停止该次后续发布；不自动重试或逐次放大上限。未确认资格保持serving空，原48机会和旧28日窗口不续开或回填。

临时清理只针对本次this_prepare与独立snapshot cache分别创建并验证归属的对象，在finally关闭句柄后处理；不递归清理整个data目录，不清除旧失败stage、首候选或审计记录。出现未知提交先读取同一对象回执与原native状态；不能以删除暂存代替资格状态确认。更改容量合同须单列新版本与有限评估，不反写旧V3或历史成功。


[46实际完整包资源](../../../artifacts/market-risk-implementation-20261007/B2/release46/qualification-transport-metrics46_01.json)：完整ZIP hash核对、native572928000B超过旧512MiB，解包795373141B、压缩232543272B/1054项，own_prepare46残留空；staging峰值/RSS仍UNKNOWN。该次156.687秒资格闭环P，不意味着自然小时、报告性能或金融有效P。
