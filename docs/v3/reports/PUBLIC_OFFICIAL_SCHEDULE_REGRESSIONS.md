# 可提交的官方日程公共回归包

2026-10-27 Root接回记录：公共8文件已逐SHA接入D补丁镜像；Main业务19源保持冻结，Main公共tests尚未采用。独立复核C0/I0/M0，review SHA5254ee4ce887ec44edd1752884f8f6faf5fdacaf84d69f2cd1e0d0dc7db6d31c（D:/DH-event-paper-public-tests-peer01/review.json）；独立普通collect-only实际13 collected/exit0、7生产模块正常Main来源。业务13PASS/48.19s来自封包原实际终态，不冒独审重复执行业务套。

NFP本包只保证date/available coverage contract和当前实现immutable reopen，不独自证明历史旧NFP整份wire golden；既有PCE候选旧wire证据另列。包内真实公开body与synthetic NFP明确分开，DB/log/evidence及相同__init__不入patch。D仍是部分补丁工程；在完整原项目采用后按普通repo-root命令运行，不声称D独立产品。

以下为公共包封存时报告，保留其当时的“尚未接回”状态和首轮装配失败。
状态：仅新D patch mirror，未写Main/Drepair或旧包，未提交或采用；Root19源normal由Root独占，本任务没有启动构建。

新增仓库delta是3份Python文件及tests/fixtures/official_schedule下4份body＋manifest，共8文件。顶层README是包说明，不替换Main既存README；__init__.py只是与Main逐字相同的必要package mirror，不是delta。正常production import，无candidate源码、loader、reload、sys.path补丁、旧worktree/D固定路径或__file__伪装。路径仅由测试模块的repo_root定位fixtures。

实际通过命令从完整物理Main的cwd执行，使用默认pytest插件，无自定义OUT/环境变量、额外pythonpath或私有launcher：

```text
.venv/Scripts/python.exe -X utf8 -B -m pytest <patch-mirror>/tests/decision_product/test_official_schedule_public.py -p no:cacheprovider --basetemp=<private-D-temp> -q
```

cache/temp两个参数仅保证本任务不写Main；采用后README里的普通repo-root pytest命令即可运行。

最终业务终态是`13 passed in 48.19s`、actual exit0，原输出在`logs/pytest-second.log`。覆盖FOMC两expected阶段及双来源、无policy旧wire、旧完成policy不受新inflight遮挡、calendar running/晚完成旧cutoff保持及新cutoff修订生效、4类坏binding拒绝、parent.error_code空串/非空×running/晚完成、PCE精确日程与NFP旧date-only coverage。没有复制全部40项或PAPER horizon测试，没有镜像金融计算器来造断言。

新3文件沿Main原strict配置真实3 files/0 errors/0 warnings（`logs/pyright-second.json`），Main cwd Ruff `[]`/exit0（`logs/ruff-main-final.json`）。文件255/100/145行，函数最大19/27/21行。所有业务调用均原SourceIngestionService/CaptureBatch/Admission/原reader与agenda，私有SQLite/archive来自pytest tmp_path；selector record-only、run_targets要求为空。HTTPX所有请求交MockTransport，不请求外网；没改asyncio或生产模块身份。

首轮新fixture全socket guard误拦Windows asyncio的内部socketpair，实际26 warnings/26 setup或teardown errors/13.30s（`logs/pytest-first.log`），不是产品RED。移除该不适当的全socket拦截、继续用HTTPX MockTransport后才计13PASS。第一次静态2诊断分别是optional completed_at与冗余cast，随后收口0。首轮Ruff15条准备期诊断保留；另从外层cwd执行Ruff因first-party import placement出现3条I001（`logs/ruff-final.json`），正常Main cwd最终0，不冒D镜像为独立工程或任意cwd lint0。

3份真实公开body分别是Fed calendar167856B、Fed 2026 policy81588B、BEA ICS43220B；另246B NFP ICS明确synthetic。总body292910B。manifest保原SHA、实际HTTP/native capture来源标签与原收取时点；历史receipt只保SHA及安全元数据，不复制cookie、headers、代理配置或凭据。BEA native原件没有TLS/final_url证明，本包没有伪造。所有新capture行、内存revision及坏数据均标为受控离线单测，不冒历史PIT/新HTTP/真实事件验收。

`evidence/source-scope.json`实际核对Main已采用19源都等Root adoption receipt，并记录7个普通import的真实Main模块来源；该文件SHA为`071a8ab2cc4c2d9d43b054d503d0f10abc4d7bb9d2a3594ac2140b926c5983ce`。这不是新normal receipt或完整所有依赖闭包的认证。SourceGuard/Host/financial qualifications没有由本单元包升级。

所有已验Python代码和fixtures在最终13项开始前冻结，终态后只写README、报告和hash清单。期间没有采用Main tests、SQL live、HTTP/model/真实通知订单、服务或新build。临时DB和运行日志不在public patch delta，不携带13MB旧D2数据库；也不计30完整失败回放、两个真实事件族或十笔PAPER fills。
