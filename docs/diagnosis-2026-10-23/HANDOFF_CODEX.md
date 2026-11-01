# 交接给 Codex：构建、启动、真实验收

> **2026-10-28 全面交接：** 后续所有开发任务由 Codex 负责，入口见 [HANDOFF_TO_CODEX_2026-10-28.md](HANDOFF_TO_CODEX_2026-10-28.md)。本页"Codex 只执行、代码由 Claude 负责"的分工作废。

> **2026-10-28 当前入口更新：** 用户已批准落实 [日常开发工作流](DEVELOPMENT_WORKFLOW.md) 与 [实施计划](DEV_WORKFLOW_PLAN.md)。日常功能修改先走独立开发环境、局部测试和 manifest 快速预检，不能默认从本页步骤 1 开始耗时存证。本页旧命令与“仅执行记录”限制保留为前两轮历史；本轮仅在开发启动/预检/清单预算及必要测试文档范围内允许实现。完整发布回执、历史原件认证和正式验收要求仍保留，开发通过不等于发布通过。新入口实际可用项以工作流验证记录为准，未通过的不冒称可用。

> **本轮交付状态：** 默认开发实例 `data/development/default` 已实际冷启动、自动注册工作区、安全停止和同实例重启通过，交付时保持运行；原前端为 `http://localhost:5173`，私有设置入口见上述手册。先检查已有服务，不重复启动。真实 BTC/ETH 4h 研究未执行，新库缺少可核验合约元数据；不要据此宣布研究或正式发布通过。

日期：2026-10-23。分工：Codex 只做执行和记录；代码改动和判断由 Claude 负责。
背景：基准率改动（P1/P2/P5）已完成单测和类型检查，详见 `VERIFICATION.md`。还缺一次构建 + 启动 + BTC/ETH 真实研究。

## 硬性约束

- 不修改任何源码、测试、脚本、配置（`packages/`、`apps/`、`extensions/`、`infra/`、`tests/`、`docs/` 下的已有文件）。唯一可写的位置是 `artifacts/incremental-delivery-20261023/` 和本目录下的 `CODEX_LOG.md`。
- 遇到报错：记录完整错误码和最后 40 行输出，然后停止。不要自己修复，不要换参数反复重试（同一步最多重试 1 次，且只在明显是网络/超时的情况下）。
- 不删除任何已有 artifacts、数据库、备份。
- 不 git commit、不 push。
- 输出要省：长日志落盘，`CODEX_LOG.md` 里只放路径和关键摘录。

## 步骤

### 1. 构建（预计 70–80 分钟，正常现象，不要中断）

前置检查：空闲内存 ≥ 10 GB，`artifacts/incremental-delivery-20261023/base-rate-build-pwsh` 不存在。

上一次成功构建的回执：
- 路径：`artifacts/incremental-delivery-20261027/event-paper-integration-build/product-validated-builds.json`
- sha256：`1f1d2d3c1682ba6127115d8a955d9dda1f69da623e1c4997adfb1869c5023852`（执行前先自己重算一次确认）

```powershell
pwsh -NoProfile -File infra/dsh/build-product.ps1 `
  -OutputDirectory artifacts/incremental-delivery-20261023/base-rate-build-pwsh `
  -PriorValidatedBuildReceipt artifacts/incremental-delivery-20261027/event-paper-integration-build/product-validated-builds.json `
  -PriorValidatedBuildReceiptSha256 1f1d2d3c1682ba6127115d8a955d9dda1f69da623e1c4997adfb1869c5023852
```

上次构建用了哪些可选参数（`-InstallSpotResearchV3`、`-CompletedMaintenancePrior`、`-CapturedV3*` 等）没有记录。如果脚本因为缺参数报错，先只读排查（看脚本里的报错条件和上次产物），确定需要哪个参数再补上重跑一次；还不行就停止并记录。

完成标志：`base-rate-build-pwsh/build-execution.json` 里 `"passed": true`。记录每个阶段的开始/结束时间（看产物文件时间即可）。

### 2. 启动

```powershell
pwsh -NoProfile -File infra/dsh/run-product.ps1 `
  -ValidatedBuildReceipt artifacts/incremental-delivery-20261023/base-rate-build-pwsh/product-validated-builds.json `
  -EnableSpotResearchV3
```

如果永续研究入口需要别的开关才可见，参考 `docs/design/market-risk-v1/B2_B4_RELEASE_RUNBOOK.md` 和 `run-product.ps1` 的参数列表，只加开关，不改代码。记录 API/DSH 是否 ready、日志目录路径。

### 3. 真实验收

BTC、ETH 各发起一次 4h 永续研究（通过产品界面或现有 API，不要写新的绕过脚本）。按 `VERIFICATION.md` 第 4 节逐条判定，并填写那里列出的记录项。

## 交付

在本目录写 `CODEX_LOG.md`，结构固定：

```
## 结论
一句话：构建 通过/失败；启动 通过/失败；BTC 达标/未达标/阻塞；ETH 同上。

## 操作记录
| 时间 | 命令（精简） | 结果 | 日志路径 |

## 改动记录
新增或写入的文件列表（应只在 artifacts/incremental-delivery-20261023/ 和本文件）。若有任何其他改动，单独列出并说明原因。

## 验收明细
BTC / ETH 各一节，按 VERIFICATION.md 第 4 节的记录项填写。

## 未解决问题
错误码、最后 40 行输出路径、你的判断（标注"推测"）。
```

## 第二轮补充（2026-10-27）

第一轮失败原因已确认：用了 Windows PowerShell 5.1，脚本需要 PowerShell 7（`ConvertFrom-Json -AsHashtable`）。本轮全部命令用 `pwsh`。

- 前置：`pwsh -NoProfile -Command '$PSVersionTable.PSVersion'` 主版本必须 ≥ 7，否则停止。
- 允许官方脚本正常生成、清理构建产物和写运行时数据（沿用上一轮用户批准的例外）；仍禁止手工改代码和配置。
- 第一轮留下的 retention 归档（`research_recipe_versions/b7d95d61...`）和 `base-rate-build-retry-01` 保持原样，不要清理。
- 构建若再失败，不再重试，直接记录。
- `CODEX_LOG.md` 追加一节"第二轮"，不要改写第一轮内容。
