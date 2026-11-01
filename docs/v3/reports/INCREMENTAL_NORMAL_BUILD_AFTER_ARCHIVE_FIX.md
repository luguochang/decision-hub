# Q039：归档声明修复后正常构建失败简报

2026-10-24，按 Q039 明确放行执行一次原 `infra/dsh/build-product.ps1` build-only，结果 **FAILED**；没有自动重试。
本次 Main recipe 身份为 `09c40117f5bc591a0137d1c513ea29c7e7b36dd61d2be24874e8c7ac7245a3c2`；后续注解候选不属于本次构建身份。

- 开始：`2026-10-24T09:59:45.920613Z`；结束：`2026-10-24T10:12:57.438922Z`。
- 原 PowerShell PID `27436`，创建 FILETIME `134373095859227805`；进程 exit **1**，总耗时 **791.5183887 秒**。
- 新输出仅为 `Main/artifacts/incremental-delivery-20261024/validated-build/`，Q038 失败 `normal-build/` 原件保留。

原 retention 准备回执实际 `passed=true`，175 项 registry registrations；其内容已按实际引用 SHA 核对。
新 registry SHA：`e729bbbd0d2853a43be638798f62cdbbef343ba2ef27cd2b6bcbe4c9554fbf80`。
原 assessment manifest preflight 实际 `passed=true`、exit 0。
上述阶段通过不表示最终正常 product guard 或完整构建通过。

UI 构建 attempt **1**、exit **134**，实际原命令为 `tsc -b && vite build`；Host attempt **0**，builds 0。
错误为 Node PID `10900` 的 `JavaScript heap out of memory`，约 4 GiB 默认堆附近发生；不是本轮 TypeScript 语义诊断。
GC 原日志在该 Node 进程约 `131.262s`、`132.309s` 时报告 4072–4078 MB 已用堆和分配失败；不等同于整个构建耗时。
PowerShell 随后在原 `build-product.ps1:296` 抛 `Decision Desk build failed.`。

[原始 stderr](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q039-normal-build/build.stderr.log) SHA `6538bed02cec41a32525f066f5d6392899bdb33ea831cf09fdeeaee3926420ad`，保留完整 V8 stack。
[原始 stdout](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q039-normal-build/build.stdout.log) SHA `2288c030ae69f96c78c4b10fb449bd49047974eb4ba0c8b29ad922cfc7da3062`。

原 stage 的回执预估 `245213` 字节、已知最小值 `239208` 字节，均小于 `2097152` 字节上限；**2 MiB 门不是本次失败原因**。
最终 `product-validated-builds.json` **不存在**；不得将 retention 回执作为新正常 validated receipt。

前后核验结果：525 项已备份旧输出共 `113753561` 字节，当前 SHA 全同、无缺失和新增输出文件。
71 项 native 构建输入、18 项已补齐 prior 原源码、producer/package/lock 以及 installed-release/V3 installed pointers 均前后不变。
71 输入指纹仍为 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`；输入指纹不是产物哈希。
18 旧源与合成 fixture 复用既有 exact SHA 原件，未重抄或假造新来源。

pnpm 原日志含 workspace/lockfile 检查及 `pnpm v11.25.0`；未显式执行 install 或改变原 pipeline。
node_modules 没有完整 before 清单，不能声称没有任何自动协调/依赖动作；offline 设置不能替代该证明。
package、lock 与已钉源码元数据前后相同，不扩展为未记录的文件系统结论。

[终态核验原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q039-normal-build/final-receipt.json)
SHA：`03513dcbcb6592393c94563df79029328f9d3299a7973eaa4afd4c5f7f58d742`。
[原 stage](E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261024/validated-build/build-execution.json) SHA `fd0b62a59ab37e91daf54b52d4946366a3e8775d3fceeea34b17fa440cde4521`。

未自动第二次构建、恢复/删除产物、部署 API/Host/Worker、调用 run-product、迁移数据库、提交模型或改写安装指针。
本简报只写 D 文档；不修改产品源码、不执行新检查、不 stage/commit，也不重复其他执行者的 UI 编译归因。
本次未取得正常构建候选；API/Host、sameRun 原 5 秒门、真实 PAPER 与长期验收均未据此通过，目标仍 ACTIVE。
