# 研究恢复与开发验证结果（2026-10-29）

本轮“恢复 BTC/ETH 4h 主流程、自测、核对需求、记录失败”目标已达到。完整产品仍未交付；P3/P5/P6、多事件闭环、其他周期及长期验收继续作为未完成需求。沿原系统修复，未重做前端、未做完整发布打包、未 git commit/push；R5j/R5k收尾阶段未新增付费研究，之前R5i真实研究与更早付费失败均保留。

## 当前可用与真实结果

开发前端 `http://localhost:5173`，API `127.0.0.1:18047`，DSH `127.0.0.1:18097`。start10 于05:17:45.6778799UTC启动，05:21:03.866178UTC首次观察全部7服务online、API/DSH ready；198.188299秒是采样上界，不是纯编译耗时。正式发布、金融资格、执行授权均false。

| 真实研究 | 原终态 | 概率：上涨 / 中性 / 下跌 | 本次正常owner接口 |
|---|---|---|---|
| BTC 4h `run_09fc0ef0a2fc4444a812b8df05167fef` | degraded，artifact `art_aac19b93f010489db1377121a2c90134` | 35.7407407407407407% /31.8055555555555556% /32.4537037037037037% | 成功，05:22:48.545745UTC证据 |
| ETH 4h `run_dff19351d7b84c6e8291199c96a0a377` | completed，artifact `art_12bfd8ce4a2a4ad1ac30026cfe84158a` | 34.8611111111111111% /33.7037037037037037% /31.4351851851851852% | 成功，05:24:13.785684UTC证据 |

两份报告在R5i阶段已真实研究并通过 VERIFICATION §4 四项技术检查；本轮修复后的接口再次读回，各13项身份/绑定/原文哈希检查通过。与原R5i证据比较，publication、source/artifact哈希、概率和资格字段相同。它们是原时点的历史研究，概率精确保留各自冻结90d prior，未证明模型预测增益；BTC旧degraded不改名成功，ETH仍有6个soft gaps。

证据根：`artifacts/incremental-delivery-20261023/d28-research-recovery/`。

- `live/{btc,eth}-r5k-final-authenticated-report.json`：本次正常owner登录和单次GET结果；此前两个409结果保留。
- `live/start10/{ready.json,startup-observation-summary.json,plugin-output-status.json}`：启动/就绪与两份编译log/stamp未变的有界证据，不冒全编译产物盘点。
- `coverage-semantics/r5k-historical-reader/final-integrity.json`：两接口与原件字段相等、96选定文件与模型字节未变、7项保护代码未变、六份既定正式保护文件完整SHA未变。仅覆盖所列文件，未盘点正式目录。
- 业务库存仍9 failed、1 cancelled、1 degraded、1 completed；本轮无新增研究。旧失败、负评、取消与首轮工具失败全部保留。

## 修了什么，如何验证

R5i修复了真实独审已APPROVED而Python消费者仍拒绝最终来源的问题；之后两次真实4h研究各约41分钟完成。生命周期记录把recorded与observed、PID出生身份分开，已实测官方启动/停止。旧实例有Windows重启事件证据，重启具体触发未知。

R5j补上真实root/child认证数据的最终传递：新v5载体经生产构造器/Host/双mapper/Source离线链验证，42 TS+127 Python运行项通过。派生BTC37facts/18evidence、ETH40/18、hard gaps为空。旧真实v4状态/概率/hash保持不变。新v5 live writer/lease、插入幂等和延迟inspect/cancel未重放，不写成真实新v5报告或金融验收。

R5k修复改Python后旧报告GET409：原v2 recipe依赖闭包与当前实现不同。保留当前执行门，开发模式明确注入只读作用域，复用保存时的精确原码消费者读取原件；没有伪装正式release/registry，没有删除校验。默认/正式模式无DEV权限，owner检查、原graph/native/report认证仍执行。

生产范围只有6文件：新增`development_history_snapshot.py`与`development_history_reader.py`；既有history publication单入口、SavedReportQuery、API routes/main接线。新模块289/272行、最长函数均48行。未改graph binding/perpetual graph/recipe/worker/PS、概率/质量/新鲜度、前端、正式构建或依赖。

- 新49用例：48 PASS、1 symlink平台SKIP、0FAIL/setup；140.0035517秒。source普通硬链接→独立快照、目标硬链接拒绝、>260长路径、绑定/只读/清理均通过。SKIP具体OS码未记录，不写成已通过。
- Ruff五新增源/测试文件0诊断；新helper strict实际2文件0错误/警告/信息。原legacy诊断及原测试失败库存未全解决，不宣称全仓strict/测试全绿。
- ACTUAL2使用真实生产factory与current Query，未猴补丁publication入口；精确旧509文件9,735,354B迁入固定DEV store，native路径下原builder自然重算ac519，两个旧selector通过。
- 两份actual-now完整Query成功，BTC57.422秒、ETH52.562秒；各7项原件对照、合计14项全部通过。6个非空事务以及额外selection快照均真实BEGIN/query_only、changes0、同实际DB；当前obsolete selector仍正确拒绝。该控制不是live writer/lease重放。

完整结果/成功命令/独立核对在 `coverage-semantics/r5k-historical-reader/actual2-evidence/`；独立审查 `review-final-offline.md`，最终正常接口另见live证据。实际所有49/169项并非同一全仓测试总账。

## 成功开发方式与性能边界

使用已有PowerShell7：

```powershell
& 'C:/Users/90603/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe' -NoProfile -File infra/dsh/run-dev.ps1 -DevelopmentRoot data/development/default
```

同一pwsh执行`infra/dsh/status-dev.ps1`检查、`infra/dsh/stop-dev.ps1`停止。已有实例先检查，不重复启动。完整步骤见DEVELOPMENT_WORKFLOW.md；当前start10正在运行，observer session25112、启动器PID5316仅作当时身份索引，下次必须现场核验。

前端用Vite开发入口；Python/插件/合同及参与recipe的资料变更在研究空闲时停实例、局部检查、源码重启。插件仅变化时局部编译；start09实际6.35秒，本次start10两份编译log/stamp全同，无新编译观察。真实研究等待、旧报告深度验证与编译是不同耗时：旧报告每次复核仍约一分钟，读取性能尚待优化。

DEV API启动按当前依赖身份保存/核验小闭包一次；当前451source+61schema=512文件，身份`4b9b9f5b95996c632d9d65444a74832e3dcd554770b46f10ff3eb5f7f7b19b68`。旧两报告身份`ac519bc186586fa81652f8b5b2dc54cf1108626535bc5378492f1cc76bcae183`已独立保存。固定位置`data/development/default/runtime/history-snapshots/<identity>/`，不扫描189归档或全仓；读请求不抓当前字节补旧版本。

该方案只支持官方源码重启后冻结源码的研究，不能热改Python后不重启就发新Run。存储/runtime/shared-code不匹配时仍失败，不授任意历史版本兼容或正式发布资格。

## 自己犯的错误与保留的现场

1. 曾把正式全仓存证用于日常开发，耗时约80分钟；已分离工作流。本轮源码开发没有调用完整发布构建。
2. R5f/g跨接口review漏掉Python最终消费者，付费研究虽审核成功仍落库失败；R5i才用真实整链补齐。R5j又以低层TrustedSource兼容控制代替公共publication Query，重启后才发现409；本轮用生产Query+正常API两层闭合。
3. 新快照漏Windows长路径和真实源metadata：D短目录仍Win206；clone丢掉真实源的普通硬链接属性，200个byte-exact源文件被误拒。已采用native IO保持逻辑manifest，用仅源leaf的精确规则复制独立快照，不拆改真实仓库链接。
4. 新测试首版507行、longpositive的fixture最长258未达>260、类型导入/行长遗漏、AST工具名ast.py自遮蔽，均属执行/夹具问题；保留原exit/log后修正，不归咎供应商或写成首跑全绿。
5. Root把纯格式lint也绑全局冻结，增加等待；已允许owned文件纯文本lint并行，实际源码身份/研究执行仍冻结。
6. E盘实际0空间，摘要写入Errno28。仅本轮失败writer隔离副本迁D，先另做verified backup再Move、三SHA同；原路径留.location.json。另两本轮readerfixture透明压缩前后逻辑bytes/SHA同；没有删除历史内容或迁改真库。记录`r5k-historical-reader/space-recovery/`与`D:/DH-r5k-space-20261029T043927Z/`。E仍只约450MB余量，后续测试临时文件放D短目录；本轮未做全盘清理。

逐步失败/裁决/命令与证据路径均追加到CODEX_LOG.md和PROBABILITY_BASE_RATE_PLAN.md；前几轮内容未重写。

## 尚未完成的需求

| 项 | 当前缺口 |
|---|---|
| P3 | ≤2pp无理由的base_rate_only确定性标记、>15pp无证据的确定性退回 |
| P4 | BTC幅度p50已证明；完整绝对价格带、numeric anchor与UI接线仍待 |
| P5/P6 | baseline log-loss/明确无对照、真实到期/四周评分；基准率与模型并列、偏离理由、仅基准率面板 |
| 多事件 | 专用预测producer/query/render主要CPI九格；PCE/就业/FOMC有部分链路，各族事前→独审→发布→结果回看未全验。其他事件仍是需求 |
| 其他质量门 | 其他周期/六组合、正式发布、连续14自然日≥90%、四周评分、≥10笔PAPER及费用/资金费、失败回放/retention等 |

已知技术遗留也保留：start04官方stop退出窗口竞态未修改；旧测试失败与legacy类型诊断仍需按新范围处理。正式发布存证的增量优化未实施，旧归档预算约195KB只是历史观察、本轮未复测；不得从开发快照成功推定正式发布已具备空间与回放资格。

A4原1030项跨版本最新库存944PASS/81FAIL/3ERROR/2SKIP，0未观察；核对完成，失败未全修，不是当前源码全绿。原250补段不再重跑。

后续顺序：P3审核与P6可解释展示→补齐P4/P5→逐族多事件闭环→其他周期/PAPER/长期证据→正式发布。以真实需求账本和新任务范围推进，不从T-000重启、重新换前端或反复付费碰运气。详见EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md与STATUS_RECONCILIATION.md。
