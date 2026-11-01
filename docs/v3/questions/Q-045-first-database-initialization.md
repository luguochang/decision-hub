# Q-045：首次数据库初始化与服务就绪检查分开执行

状态：DECIDED，2026-10-24。用户已授权按实证自主纠正文档和执行安排；本裁决不修改金融门槛或产品代码。

## 实证与判断

正常构建已经通过，真实 receipt SHA `936c0aa80206cb42e9e379c143f1a5fcd549c7a36716fc1c3ce65eb38f965f7c`。原官方入口 q043nh01 在45秒 API readiness 失败，尚未进入 Host/研究。后续 q043api02 保原权限和45秒门，用 Python 线程栈取得四个完整样本：约10秒在 assessment manifest 文件检查、20秒在 pypdf 导入、30秒在实际迁移0014、40秒在迁移0035。进程在清理前仍存活，1931输入前后相同；不能把全部45秒归于扫描、迁移或死锁。

证据：`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/api-readiness-q043api02/diagnostic-proof.json`，SHA `8d7f8dc58242db5e7d3d73f957f8951fd9df43e259787450657691db2f8e06aa`。两份不完整 faulthandler/AV 原件保留，不再重复同类采样，也不认定其因果。

新的私有库需要执行全部103份真实迁移。把安装初始化与 API 启动都塞入一次45秒就绪窗口，不利于判断各步骤。先明确完成数据库初始化，再使用原45秒就绪检查，保留全部实际迁移、检查与失败。

## 精确允许范围

只在全新 `D:/DecisionHub-v3-development/temp/legacy-reuse/normal-receipt-host-initialized/` 准备和封存：原 Q041 run 的最小新副本、四份原字节辅助文件、README、新 `initialize_database.py` 及精确 source pins/scope/evidence；旧包、原件和 Main 源码只读。另允许三根目录的本卡和任务报告、AGENTS 接续登记。

新一次 Host attempt 为 `D:/DH-host-q043nh02`，不得恢复、复制或 stamp 旧半迁移库。clone 建好只读 Main migrations junction 后，用原 OwnedProcess、同 sterile env、原 childguard 启动一次初始化子进程：

1. 导入真实 Main 的 `Database`，断言 SQLite 目标严格是该 attempt 的 `hub-data/db/decision_hub.sqlite3`。
2. 调用原 `Database.initialize()`，实际执行原 Alembic `upgrade head`；不得裸 CLI 使用错误默认库、`create_all`、跳迁移或降级。
3. 从该私有库读取实际 current heads，与原 `Database.expected_migration_heads()` 非空集合一致，保存真实路径、UTC、耗时、退出和 heads；释放 engine。
4. 安装步骤采用独立有界 **300秒**预算。这是本裁决新选的安装预算，不声称原有预算；失败或超时停止、保留现场，不重试。进程/Job 在全部路径进入原清理。
5. 初始化完成后才启动原 API；API 自身的 `initialize()` 仍实际执行。原 API ready45秒、Host20秒、Worker240秒以及业务、身份、报价/金融限制和原 assert 全部保持。

新工具与来源必须精确冻结、非作者复核；绑定实际 normal receipt、Main17候选、当前13源和真实迁移目录。准备不等于执行；初始化、ready、Host、原场景 assert、前后来源与 Owned 清理分别按实物报告。不得由安装通过推定研究、Provider、PAPER、14天或四周验收。目标保持 ACTIVE。
