# 项目临时空间与 SQLITE_FULL

2026-10-10，49首次正常启动的真实失败：实时通道 `analytical_assessments` 在 UPDATE 执行阶段得到原生 `SQLITE_FULL`（13），12.300381秒，未到90秒动作预算。该异常不是 `SQLITE_BUSY`，不能用延长租约或降低校验门解决。

当时只读系统检查：C为NTFS、剩余0；E为NTFS、剩余127.495GiB。系统临时目录为C盘，项目数据库在E盘，约3.950GB，WAL约11MB。**这些事实没有捕获该次SQL的实际临时文件，也没有证明提交或回滚成功。** 原日志和首次Ready F均保留。

## 可复用启动方法

使用同一已通过的49构建，不重建、不迁移、不修改原授权配置，不删除用户文件。先按标准停机并核对在途任务、原claim和未知围栏；随后调用本项目的进程临时目录包装器：

```powershell
& E:/project/study/codex/dsh/decision-hub/artifacts/market-risk-implementation-20261007/B2/release49/launch49_tempE_02.ps1
```

包装器将本次启动进程的TMP和TEMP设为`data/product-local/tmp49`，核查目标为本项目内的普通目录，再委托原`start-local49.ps1`和受保护的标准`run-product.ps1`。不改系统或用户的永久环境变量；原六项私有配置、资格、协议、预算及到期时间保持。

标准启动路径继承这两个变量，没有清空或覆盖。SQLite Windows默认临时目录由GetTempPath选择；Windows优先TMP、其次TEMP。WAL/回滚日志仍在数据库旁。参照[SQLite临时文件说明](https://sqlite.org/tempfiles.html)、[Windows GetTempPathW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-gettemppathw)。

## 实际放行条件

原环境F不可覆盖。新启动需核三接口、七个新鲜服务心跳、通道实际错误状态、真实研究与报告写入，并在下一自然机会验持续监测。仅静审继承或设置环境变量不能标为修复成功。若仍SQLITE_FULL，保留新的原生码与阶段，定向核临时目录、配额/页数及实际空间，不清理未知文件或反复重启。

项目临时目录调整不解决C盘本身的满盘状态；C盘清理需另行核实文件归属。

## 本次实际结果（追加，原失败不覆盖）

2026-10-10 12:54（北京时间），新启动`20261010T044926813Z`的`ready49_tempE_02.json`通过：三接口HTTP200、七个新鲜服务心跳，实时通道没有当前错误，数据库仍0091，实际49构建指针保持。原`ready49_01.json`失败保留。旧39正常恢复CAS随后通过并排队；新BTC请求通过PC正常保存。两者尚未证明worker最终完成，后续实际结果见[当前交付状态](CURRENT_DELIVERY_STATUS.md)。
