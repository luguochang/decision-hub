# 原正常 build-only：Q038 唯一执行失败

2026-10-24 按 Q038 执行一次原 `infra/dsh/build-product.ps1`，结果 **FAILED**，未自动再次构建。主进程 PID 16260，创建 FILETIME `134373081519726981`；外层开始 `2026-10-24T09:35:51.970868Z`，结束 `2026-10-24T09:40:41.203289Z`，**exit 1，289.2326086 秒**。原 stage 开始 `09:35:52.4034537Z`，结束 `09:40:41.1013991Z`；在 retention 准备阶段失败。

[终态核验原件](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q038-normal-build/final-receipt.json) SHA `63ff6a7c58c0673949848e4ec9274adcc583746a63dba19d3fb5f2d1a8695b17`；[原 build-execution.json](E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261024/normal-build/build-execution.json) SHA `6b5a02f3d36703f700e20002b157a6c09f284178f1888f9b2918db8f0ec5975b`。

实际命令由 [prepared-command.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q038-normal-build/prepared-command.json)（SHA `877dc18d96ab84db1500f3612921fa9451981d2a2e860061f3138e8a7fd9fced`）及 [started.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q038-normal-build/started.json) 保存：

```powershell
pwsh -NoProfile -File E:/project/study/codex/dsh/decision-hub/infra/dsh/build-product.ps1 `
  -OutputDirectory artifacts/incremental-delivery-20261024/normal-build `
  -PriorValidatedBuildReceipt E:/project/study/codex/dsh/decision-hub/artifacts/decision-product-core-20261018/D1/candidate-49c/product-validated-builds.json `
  -PriorValidatedBuildReceiptSha256 2bc144fc4bdc3dcaa11766aea1927d71e9d8dfe32753baaad9a71d7138d567e6 `
  -OriginalSourceRoots E:/project/study/codex/dsh/decision-hub/artifacts/incremental-delivery-20261024/original-sources
```

执行前 8047/3097 无监听。使用原 Codex fallback pnpm；本地版本处理设 `pm_on_fail=ignore`，同时关闭 Corepack 网络并设 offline，未安装或更新依赖。未传 `InstallSpotResearchV3`、capture 或数据库参数；未调用 run-product。

原 stage 的 UI/Host attempts 均 **0**，builds 0，validated_receipt 为 null，原错误 `product_build_retention_prepare_failed`；migrations、产品 process starts/stops、pointer writes、model submissions 均 0。这里的产品计数不表示构建子进程没有启动。

执行前按实际 expected SHA，create-only 补齐 18 项 prior 原源码到新 `Main/artifacts/incremental-delivery-20261024/original-sources/`：12 项来自 49c `retention/original-view`，3 项来自 Q037 `original-source` 相对树，另 3 项分别来自具名旧 `PerpetualSavedReport`、advice、manual 修复备份。逐项源路径、expected SHA 和目标见 prepared-command 原件；来源选择依据 [availability-final.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/prior49c-source-availability/availability-final.json) SHA `82578b007317c7b48c40a43b9308c01bd70d10626455453772fe53e9860788d0`。该清单仅证明 raw receipt 的 1790 项可获，**未证明 63499 项历史来源或完整正常 guard 通过**。

执行前完整备份 lib/dist、已有 tsbuildinfo 和 generated identity 共 **525 文件、113,753,561 字节**，保相对路径及逐文件 SHA；[备份清单](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q038-normal-build/output-backup-manifest.json) SHA `ef6cfc0b354412f18c1729917c4bde6cb07bfaa116e683d0dba973bbfc631e6f`。失败后 525 项当前原件 SHA 全部相同，输出目录无新增文件；18 旧源、71 native 构建输入、已钉 producer/package/lock/pointer 均前后相同。71 输入指纹为 `a925ff980f20075b56d40910046775aab70530bf78bb5f1dcf3531c20701ec64`，不是产物哈希。另按卡补入 bce 合成 native-event fixture（702654 字节、SHA `bceccbcb05f3b40da1c8ad533ac0ffc099dd4febb3396ee0eb81b086a87fe667`），不计真实事件或研究资格。

失败原始 stderr 如下，完整原字节仍保留于 [build.stderr.log](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q038-normal-build/build.stderr.log)（SHA `8a4b37e8addf22170f920860837e24aacd84bbc70b86f76c1ee7eb725014d53b`）；末段替换字符是原件内容，未修写。stdout 为 0 字节。

```text
Traceback (most recent call last):
  File "E:\project\study\codex\dsh\decision-hub\packages\kernel\decision_hub_kernel\application\research_recipe_archive.py", line 192, in _recipe_initial_modules
    raise ValueError("assignment")
ValueError: assignment

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "E:\project\study\codex\dsh\decision-hub\infra\dsh\prepare-product-retention.py", line 1193, in <module>
    prepare(parser.parse_args())
  File "E:\project\study\codex\dsh\decision-hub\infra\dsh\prepare-product-retention.py", line 1003, in prepare
    prepared = producer.prepare_retained_recipe_archive(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "E:\project\study\codex\dsh\decision-hub\packages\kernel\decision_hub_kernel\application\research_recipe_archive.py", line 704, in prepare_retained_recipe_archive
    planned = _planned_manifest(root, overlay, runtime)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "E:\project\study\codex\dsh\decision-hub\packages\kernel\decision_hub_kernel\application\research_recipe_archive.py", line 226, in _planned_manifest
    initial = _recipe_initial_modules(payload(
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "E:\project\study\codex\dsh\decision-hub\packages\kernel\decision_hub_kernel\application\research_recipe_archive.py", line 201, in _recipe_initial_modules
    raise _fail("research_recipe_archive_initial_declaration_invalid") from error
ValueError: research_recipe_archive_initial_declaration_invalid
Exception: E:\project\study\codex\dsh\decision-hub\infra\dsh\build-product.ps1:102
Line |
 102 |  … ASTEXITCODE -ne 0) { throw 'product_build_retention_prepare_failed' }
     |                         ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | product_build_retention_prepare_failed
```

可定位的契约不兼容：本次 recipe SHA `5ce02ead1bf1531077383c45b3da33f9e6789368e7ad0175a1330323ad0f9d16` 在模块第 58 行定义 `_GRAPH_RECIPE_ROOTS`，builder 第 240 行直接遍历该全局量；archive SHA `b47d8050652b24ac7406146960658bae82e74cc508db6bcb4d657f640f3990e7` 的 `_recipe_initial_modules`（173–201 行）只接受 builder 内唯一 `initial = <literal tuple>`，故第 192 行抛 `assignment`。后续最小修复方向是恢复 builder 本地 literal `initial` 并与缓存 global 根保持一致；不改 archive parser 或阈值。该修复本报告未实施。

本轮保留失败 stage、retention 原件与回滚备份，没有清理或覆盖历史。未取得新正常 validated receipt，未启动 API/Host/Worker 或修改安装 pointer，未作新资格或性能通过声明；5 秒门及真实长期验收仍未达。
