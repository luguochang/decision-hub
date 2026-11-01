# 原官方 Host 隔离装配与 API 启动诊断

2026-10-24。本轮沿用原 `tools/dsh_native_acceptance.py` 的 success 场景、资料生成、受理、单次 Worker、collect 与原断言，只替换环境、工作目录和进程启动接缝。实际运行尚未到 Host，不能据执行器存在或语法检查声称整链通过。原 manual 仍为 6.0007604 秒失败原 5 秒门，项目目标 ACTIVE。

## 已审装配

- 复用已封候选 39e 和 67ebed59 staging，复制 17 个原字节文件到全新 D home，创建 7 个同 SDK 链接并核 4 入口的 28 个解析；没有 install、rebuild 或替换原 profile。
- Python API/MCP/Worker 源仍是真实 Main；官方 CLI 指向锁定的本地 SDK。原 success 断言和 20s Host、5000ms callback、3 attempts、45s API readiness 时限保留。
- 清理只针对此次创建的真实 Popen/Windows Job 句柄，保存 PID、创建 FILETIME、退出与 Job 关闭记录。标准库防护不冒称完整 OS 沙箱，也没有独立全局后代进程快照。
- 首版 [scope](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/scope-final.json) SHA `79cf9b8f…7154` 与[非作者复核](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/original-host-execution-final-peer.json)保留。后续改动的旧 8 文件在 `migration-layout-repair/before/`，不改旧运行原件。

## 实际结果

| 具名运行 | 实际输出 | 已到达及未到达 |
| --- | --- | --- |
| [h1024a](D:/DH-host-h1024a/failure.json)，runner exit1 | 原 API 报 `alembic.util.exc.CommandError: Path doesn't exist: migrations`，随后 45s health timeout | 17 文件复制与 resolver 完成；API 初始化失败；没有 MCP/Host/Worker/admission |
| [迁移目录小验证](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/migration-layout-repair/final-receipt.json) | 目录缺失 RED；新 D `migrations` junction 指向真实 Main 后，`ScriptDirectory.get_heads()` 返回 `0103_perp_funding_reconcile` | 只目录发现，没有 DB initialize/upgrade；中间缺 Main sys.path 的 probe 装配错误单独保留 |
| [h1024b](D:/DH-host-h1024b/failure.json)，runner exit1 | 新 junction 和 resolver 成功；API 仍在原 45s 内未 ready，`hub-api.log` 为 0B | 没有首轮目录缺失异常；新的启动卡点未定位；没有 MCP/Host/Worker/admission |
| [ap1024a](D:/DH-host-ap1024a/diagnostic-observation.json)，一次 API-only 诊断 | 15s dump 只留下 `pathlib.with_segments:385` 不完整栈；子进程约 19.922s 以 `3221225477 / 0xC0000005` 退出 | 没有第二栈，不算 35s 双栈成功；未发 health HTTP、未启动其余业务链，不据此断言 Main、guard 或 faulthandler 是根因 |

首轮直接原因是隔离 cwd 与原 Alembic 相对路径不匹配，属于装配错误。修复只是在新私有 cwd 创建源迁移路径接缝并增加 ini/env/版本清单 SHA 检查，原迁移内容、生产 DB 和工作目录没有修改。新 [scope-with-migrations](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/scope-with-migrations.json) SHA `45413d78…43e6b`；根独立查看精确差异与 8 SHA，变更 clone 的 Node 语法检查 exit0，原 45s 不变。目录 GREEN 不证明完整 API 初始化或数据库升级通过。

首轮 clone PID5716 exit0、API PID8412 exit1；第二轮 clone PID27668 exit0、API PID6604由此次 Job 结束并观察 exit1；诊断 PID17660 观察到原生退出码。每项均有配对 owned/cleanup 记录，`root_exit_observed=true`、`job_closed=true`；两次完整装配的 cleanup-summary `errors=[]`。这些记录不扩大为独立全 OS 后代清点或产品业务成功。

诊断前后具名源 pins 相同，已冻 8 文件未修改。仅建立新的 D 产物、链接和临时 DB 范围；原 UI、Main profile、8047/3097 服务及正式库没有部署/改写。受保护端口检查与实际尝试日志在执行器目录，不借用原监听服务。

## 原件与下一步

- 首轮 [API log](D:/DH-host-h1024a/logs/hub-api.log)、[cleanup](D:/DH-host-h1024a/artifacts/cleanup-summary.json)、[独立只读收据](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/original-host-first-attempt-review.json)，收据 SHA `82ad3279…aebb`。
- 第二轮 [API log](D:/DH-host-h1024b/logs/hub-api.log)、[migration junction](D:/DH-host-h1024b/artifacts/migrations-junction.json)、[cleanup](D:/DH-host-h1024b/artifacts/cleanup-summary.json)。空日志没有提供业务根因。
- API-only [原始栈](D:/DH-host-ap1024a/logs/hub-api-only.log)、[completion](D:/DH-host-ap1024a/diagnostic-completion.json)、[收据](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/api-only-diagnostic/receipt.json)，收据 SHA `4afd5e4b…6956`。

当前先只读核对启动防护与路径解析，依据具体证据选择更小的下一诊断，不盲跑第三次完整 Host、不增加时限。独立的 Q034 在原报告页补阅读快照 JSON/离线 HTML，按自己的 TDD 和验收推进。实际 Host/live-root/Provider、实际 PAPER、六组合连续 14 天与四周评分仍未验收；没有新增 ACCEPTED。


## 2026-10-24 后续有限诊断（08:50Z 截止）

原防护的纯文件原语探针 `D:/DH-gp-c241` 0.156s/exit0，mkdir/write/readback/resolve/parents 合法私有路径通过、同前缀兄弟目录拒绝；整数深度峰值1。其[收据](D:/DecisionHub-v3-development/temp/legacy-reuse/original-host-execution/pure-guard-probe/receipt.json)只证明这些原语，不能解释或排除之前的原生异常。

随后两次是新增具名阶段诊断，不是第三次完整 Host：两者都只启动一个 API 子进程，保原 guard、45s上限、真实 Main 文件身份、私有 D cwd 和 owned Job；未发 health HTTP、未 MCP/Host/Worker/admission。

| 私有目录与新增观测 | 实际结果 | 限制 |
| --- | --- | --- |
| [bc1024a](D:/DH-host-bc1024a/diagnostic-observation.json)，模块/函数 call-return | 45.093s 仍运行；仅 API_MODULE CALL、DB_MODULE CALL/RETURN，没有 CREATE_APP | 未到 create_app、数据库 initialize 或 publication factory；不能据此认为数据目录已解释超时 |
| [bc1024b](D:/DH-host-bc1024b/diagnostic-observation.json)，仅 API `<module>` 8个固定一次性行标记 | 45.0s 仍运行；实际有 BEFORE_FASTAPI、BEFORE_ASSET_ROUTES、AFTER_ASSET_ROUTES、BEFORE_FACT_ROUTES，后续标记未达 | 仅将区间缩到原API line40 `fact_routes` 导入及依赖；不能断言该模块有缺陷，profile/trace时间不作性能验收 |

bc1024a PID23960/FILETIME134373050375605774，bc1024b PID16832/FILETIME134373054061062722；均由自身 Job 结束，子exit1、root_exit_observed/job_closed=true。外层 runner exit0只代表诊断收据完成，**不是 API ready**；各 source-before/after逐字相同。原件分别在两目录的 logs/hub-api-only.log、diagnostic-completion.json 与 logs/hub-api-only.cleanup.json。阶段scope为 `f1aa4d92…3147e`，有限行scope为 `718e1110…e1ffd`，原8文件/scope及旧失败日志保留。

Q034 原页导出已完成局部验证并接 Main 本地源码，见[阅读导出报告](INCREMENTAL_PERPETUAL_READING_EXPORT.md)。Q035 显式数据目录亦已提交并精确接 Main 本地源码，见[实例数据报告](INCREMENTAL_PUBLICATION_INSTANCE_DATA.md)；这改变旧执行器 pin中的 API/Worker 两项，**旧 source-pins 不可直接重跑**。后续只基于具名当前源码建立新输入身份，保留历史包，不放开正式写入或增大时限。下一步先 source-only 检查 fact_routes 的导入依赖及顶层副作用，尚未找到根因，不连续追加盲目启动。原 sameRun5s、完整Host、真实长期指标及整体strict0仍未通过；目标ACTIVE。


## 导入与正常启动前提更正

在[新的直接导入原件](D:/DH-fact-im1024a/logs/fact-import.log)中，原guard/sterileenv、无sysprofile/trace/faulthandler，仅官方-Ximporttime导入真Main fact_routes，实际6.1658053s/child0，226个apps/packages origins均为Main，结束后重hash相同。未create_app/DB/API/Host。此前有全函数/稀疏行诊断的45s区间不能据此称fact模块卡死，也不能把嵌套import时间相加或作因果性能差。

新的静态前置核查确认：clean env没有正常validated retention receipt pair，配置函数直接return而registry仍None；如果导入/迁移完成，发布factory将拒绝未注册Root。这是可证明的下一前置缺口，尚不能解释原45s或AV。真实49c locator/receipt及registry存在，但Main当前源与旧receipt已不同，不能直接拿它当当前guard通过；1790 raw sources已全部找到exact旧SHA材料，63499历史sources仍须原producer/guard真实核验。

Q036已清目标类型诊断而旧债非零；Q037已把审过的原UI/native差量接回Main源并由标准generator得到与D候选同指纹。Q038范围已单独记录，按原build-only接续，未据准备工作宣称normal receipt、API/Host或最终交付。最新事实及接点见 [启动诊断报告](INCREMENTAL_BOOTSTRAP_DIAGNOSIS.md)。旧执行器/控制包的pin保历史，不原位刷成成功。
