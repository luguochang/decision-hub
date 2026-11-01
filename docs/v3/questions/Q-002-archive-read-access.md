# Q-002：完整存档被 8 个旧产物目录的读取权限阻塞

## 问题信息

- **问题 ID:** Q-002
- **关联任务:** T-000
- **提出者:** Codex / 01a16796-0554-7ea1-9bda-c0a5409715b8
- **提出时间:** 2026-10-23 15:50（Asia/Shanghai）
- **优先级:** HIGH
- **状态:** DEFERRED / MAIN_FLOW_UNBLOCKED（按用户最新指令继续 v3；完整旧存档仍待权限恢复）

## 问题描述

用户已经批准 Q-001，允许 T-000 存档、隔离工作区初始化和本地交付。本问题不是重复请求这些授权，而是执行完整存档清点时遇到的实际文件系统错误。

T-000 要求一次提交全部未提交内容。`artifacts/` 没有被现有 `.gitignore` 整体忽略，但下列 8 个目录无法读取。Git 跳过这些目录后给出的列表不是完整列表，因此不能据此宣称完整存档。任务卡没有授权修改这些旧产物或目录权限；执行者没有擅自跳过它们或更改 ACL。

## 现象 / 错误信息

实际清点命令只临时启用长路径处理，没有写入 Git 配置：

```text
git -c core.longpaths=true ls-files --cached --others --exclude-standard --deduplicate -z
```

实际 stderr：

```text
warning: could not open directory 'artifacts/pc-process-ownership-20261001/pytest/': Permission denied
warning: could not open directory 'artifacts/pc-process-ownership-20261001-race/lint-repair-pytest-091b95af/': Permission denied
warning: could not open directory 'artifacts/pc-process-ownership-20261001-race/pytest/': Permission denied
warning: could not open directory 'artifacts/pc-redteam-20261001-1004/pytest/': Permission denied
warning: could not open directory 'artifacts/pc-redteam-20261001-1008/pytest/': Permission denied
warning: could not open directory 'artifacts/pytest-pc-document-quality-final/': Permission denied
warning: could not open directory 'artifacts/pytest-pc-document-quality-green/': Permission denied
warning: could not open directory 'artifacts/pytest-pc-document-quality-red/': Permission denied
```

独立使用 Python `os.lstat` 和 `os.scandir` 核查上述每个路径：全部为普通目录（非 symlink、非 junction），全部 `os.scandir` 返回 `WinError 5`，`can_list=false`。第一处路径的 PowerShell `Get-Acl` 也返回 `Attempted to perform an unauthorized operation.`。

停止前最后一条清点进度为：

```json
{"paths": 4128119, "logical_bytes": 119277863872}
```

该数字只是**已处理部分的下界，不是完整文件数量或总量**。逻辑字节也不是 Git 对象库的压缩后空间需求。未因此自行排除任何目录，也未开始暂存或创建 Git 对象。

## 根因分析

读取权限阻止完整枚举，而现有规则要求保全全部符合 Git 默认忽略规则的历史内容。目录名称像 pytest 产物，并不证明其中没有需要保全的文件；执行者无法读取其内容，也没有依据将其认定为空目录或可丢弃文件。

## 已尝试的方案

1. 用户批准 Q-001 后，已记录裁决，并将 T-000 追加为 IN_PROGRESS。
2. 原生工作区工具因聊天 cwd 不是 Git 仓库返回 `Not a git repository`；尚未创建隔离目录。此工具限制本身可用 Git worktree 解决，不另作阻塞。
3. 使用 `git -c core.longpaths=true` 做只读完整清点，长路径错误不再出现，但发现以上 8 处读取错误。
4. 用独立文件系统接口确认错误，未修改 ACL 或目录内容。
5. 中止本轮自己启动的清点进程，确认进程退出。保留 stderr 与部分统计摘要；删除了自己生成的未完成临时路径清单，未删除用户文件。

## 建议方案

优先恢复这 8 个目录的读取权限，保持文件内容和范围不变，再重新完整清点并存档。需由有权限的用户 / 管理员处理，或明确授权执行者仅为存档调整这些路径的读取权限；当前执行者甚至无法读取第一处 ACL，不能保证同一账户能完成修复。

如果这些目录确实不属于需存档的内容，也可以由用户 / 架构师明确批准将**仅这 8 个目录**排除于 Git 存档之外，同时原件留在磁盘，报告逐项注明偏离。未批准前不采用此方案，不扩大排除范围。

磁盘现有余量有限；目录恢复后仍需根据完整清点与实际 Git 写入核实能否存档，不能把逻辑字节直接当作对象库需求，也不预先断言磁盘必然不足。

## 阻塞范围

- 阻塞 T-000 的完整存档，因此尚未创建 archive 和 v3/main，未通过本任务验收。
- 不影响 Q-001 已批准的效力；读取问题解决后继续原方案，不再重复确认。
- 未修改旧服务代码、数据库、产物内容、ACL 或 Git 配置。
- 不绕过 T-000 / 阶段验收开始 T-001。

## 决策记录（由架构师 / 用户填写）

**决策时间:** 2026-10-23（本聊天用户回复）

**决策:** APPROVED：恢复读取权限后完整存档，不允许跳过目录。

**裁决内容:** 用户选择“由有权限账户恢复读取权限，再完整存档（推荐）”。执行者已复核 8 个目录仍返回 WinError 5。当前账户是 `DESKTOP-H2G1HA1\90603`，令牌为 Medium Mandatory Level，Administrators 为 deny-only，未获得管理员权限；无法自行切换到有权限账户。未再次要求批准同一存档方案。

**后续用户指令:** “继续往后执行不要阻塞主流程了”。据此将完整旧产物存档保留为待办，不再作为 v3 干净基线及 T-001 的前置阻塞；不代表用户批准删除、排除或伪称存档完成。先在独立工作区从已确认提交初始化 v3，继续任务卡，旧目录保持原样。

### 权限恢复准备（2026-10-23，用户裁决后）

- 可读取的上级目录所有者为 `DESKTOP-H2G1HA1\CodexSandboxOffline`；8 个子目录的 ACL 仍不可读，不能仅凭上级 ACL 推断它们的具体拒绝规则。
- 已准备外部操作脚本 `E:/project/study/codex/_shared/temp/decision-hub-t000-fpxrmjuv/restore-archive-read-access.ps1`，只对这 8 个已证实拒绝读取的目录本身，为当前账户 SID 追加 `RX`；不使用递归命令或新增继承位，不重置 ACL、不更改所有者、不删除显式拒绝规则、不改写文件内容。修改前检查目标及各级祖先无 reparse point。
- 在任何 ACL 改动前，将 8 个原 DACL 一并保存到脚本旁新建的 `acl-backup-*` 目录；失败即停止，不静默继续。若有权限账户本身也读不到原 ACL，脚本停止，不自行夺取所有权。现有拒绝 ACE 仍可能使新增 RX 无效，须以原账户实际读取为准。
- 脚本仅完成语法解析和 `-Preview` 验证，**尚未执行权限修改**。需要有权限账户在提升的 PowerShell 中运行：

```powershell
& 'E:\project\study\codex\_shared\temp\decision-hub-t000-fpxrmjuv\restore-archive-read-access.ps1'
```

- 脚本运行后，必须再用未提升的 Codex 进程验证 8 个根目录和完整 Git 清点，才能确认阻塞解除。
- 命令行为依据：[Microsoft icacls 文档](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)。使用 `/grant` 而非 `/grant:r`，并避免递归命令跨越链接边界。

## 相关链接

- [T-000 任务卡](../tasks/T-000-git-baseline.md)
- [已批准的 Q-001](Q-001-execution-rules.md)
- [任务报告](../reports/T-000.md)
- 诊断 stderr：`E:/project/study/codex/_shared/temp/decision-hub-t000-fpxrmjuv/inventory-stderr.txt`
- 部分统计：`E:/project/study/codex/_shared/temp/decision-hub-t000-fpxrmjuv/inventory-partial.json`
