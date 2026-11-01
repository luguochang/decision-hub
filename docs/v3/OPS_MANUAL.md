# Decision Hub 原产品运维手册（候选）

本稿依据原产品启动、停止、备份和报告读取实现整理。保留原界面与身份流程。2026-10-27 13:24 +0800 当前维护状态：17个事件后端源已实际接回Main，物理Main49例通过，新的唯一原normal正在session27841运行。旧预览已同句柄关闭，账户、设置、历史报告、评分和新采集日线均保留；当前网页暂停，构建成功及新七角色实际验收后恢复。详见[本轮接线实测](reports/INCREMENTAL_THREE_EVENT_FAMILY_MAIN_INTEGRATION.md)。

## 1. 启动前核对构建与数据身份

- 从实际交付仓库根目录操作，核对 Node、Python、锁文件、官方 SDK、前端与插件产物和本次正常构建回执。回执必须与当前源文件、实际产物、归档及注册信息匹配；源码提交号或单独的 `dist` 目录不足以证明身份。
- `infra/dsh/build-product.ps1` 要求 `OutputDirectory`、`PriorValidatedBuildReceipt` 及对应SHA-256。默认使用已安装基线；也支持显式 `-CompletedMaintenancePrior`，将真实完成的维护构建作为prior并完整鉴真其到已安装基线的历史链。该选项不把候选冒充已安装版本；须提供精确旧源 `-OriginalSourceRoots`，按原规则核备份、来源和产物。保留失败原件与旧输出，不手改校验器、回执或指针。
- `run-product.ps1` 接受显式 `-ValidatedBuildReceipt`，否则读取受校验的安装指针；无已安装回执时才可能进入原构建分支。无效指针或显式回执不匹配会失败，不应通过删除指针或换旧回执来获得启动成功。
- 使用已核对的原启动参数和功能开关；不为阅读研究报告开启执行开关，不把 PAPER 成本假设说成真实账户费率。私有测试工作区、临时预览端口和单测产物不是交付入口。
- 先确认数据根、历史 DSH Home、空闲空间、在途 Run、已有进程所有权与备份。迁移会写数据库；维护前应停机并完成一致备份。原 `Database.initialize()` 执行真实 Alembic `upgrade head`，不得用 `stamp`、`create_all` 或复制半迁移数据库代替。

构建调用模板如下，参数值来自本次受核验的维护记录，不能直接照抄占位符：

```powershell
pwsh -NoProfile -File .\infra\dsh\build-product.ps1 `
  -OutputDirectory <新的仓库内artifacts目录> `
  -PriorValidatedBuildReceipt <实际已安装基线回执> `
  -PriorValidatedBuildReceiptSha256 <该原件的SHA256>
```

## 2. 原产品入口、本机授权与报告

在仓库根目录使用原启动器，保持启动终端运行：

```powershell
pwsh -NoProfile -File .\infra\dsh\run-product.ps1
```

需要显式采用已经核验的构建时，沿同一入口使用 `-ValidatedBuildReceipt <本次回执路径>`；这不是免校验或自动部署授权。当前源码的默认 API/UI 为 `http://127.0.0.1:8047/`，DSH 为 3097，MCP 为 8037，采集服务为 8038。若启动参数不同，以该次启动输出和配置快照为准；不要拿临时私有实例的端口代替正式入口。

1. 打开启动器输出的 `DECISION_DESK_LOCAL_ENTRY` 本机快捷入口。原启动器创建受本机 owner ACL 保护的入口，PC 消费片段中的配对口令并在异步请求前移除该片段。
2. 需要恢复授权时，在原“仓位与风险设置”页展开“恢复本机授权”，使用密码输入和“验证本机身份”。核对页面显示的身份与有效期；不得注入 cookie、sessionStorage 或组件状态冒充授权。
3. 在同一标签页、同一产品 origin 打开 `#/report?run=<实际Run ID>`，核对 Run、Artifact、原文、证据、时间、限制及未知项。只看到 API/DSH 就绪或任务已入队，不代表报告完成。
4. 永续 V5 原页核对成功后，可使用“保存阅读快照 JSON”和“保存离线 HTML”。这是已核对正文、定义及附带字段的阅读副本；不是完整审计原件封包，不授予交易权限。通用报告与永续报告导出能力不同，不把通用 JSON/Markdown 当作 V5 HTML 已验收。

入口文件、配对口令、DSH 认证 URL、密钥和 CSRF 均不得进入截图、工单、报告或聊天。DSH 裸 URL 的未认证响应不应通过关闭认证处理。页面身份/完整性错误应保留真实错误，不改为成功或未经核验的旧版 fallback。

## 3. 日志与最小故障归因

当前原启动器默认使用：

| 材料 | 用途 |
| --- | --- |
| `data/product-local/supervisor.json` | 启动器身份、启动状态和日志目录 |
| `data/product-local/processes.json` | 角色 PID、创建时间、当前日志与恢复状态 |
| `data/product-local/logs/<launch_id>/` | 各角色每次启动的 stdout/stderr、构建与阶段诊断 |
| 原 `/health/ready`、`/v1/operations` | API 就绪与角色心跳、实际来源状态 |
| 原 `/v1/operations/runtime-configurations` | 启动配置快照和构建身份 |

这些查询不等于授予业务写权限；本稿没有实际访问接口。排障按“首次失败阶段 → 原异常码/退出码 → 同 Run 原件 → 来源身份”定位：

- **构建或身份失败**：先核实际 mismatch、回执来源、源文件和产物；UI/Host 尚未启动的失败不能写成研究质量失败。
- **API 未就绪**：区分导入、首次迁移、进程退出与健康检查响应。空日志不能推出根因。只在隔离环境做具名观测，保留原准备与清理证据。
- **Host/工具未就绪**：区分原认证、workspace 注册、SDK/插件身份、工具服务和采集来源；不能通过扩大进程/网络权限或关闭守门获取绿色结果。
- **Worker 失败或等待过久**：先核当前 Run、持久 Task、lease、checkpoint、DSH Session 和最后真实阶段。观察器超时、模型步骤预算、lease 与产品 Task deadline 分别记录；不把一个临时观察窗说成产品期限，也不提高真实期限来掩盖失败。
- **报告读回失败**：区分 403 身份、404 不适用/缺失、409 原件校验等真实响应；保留原状态。只凭 Artifact 存在不能认定报告可用，`research_only`、`degraded` 与未校准应按原结论呈现。
- **SQLite 错误**：`BUSY`/锁冲突、`FULL`/磁盘空间、缺表与 schema/fixture 装配问题分开归因。不得直接改状态、清 lease、补造 ACK、删 WAL 或降低 hash/权限门。

最小记录包含启动身份、Run/请求 ID、阶段、时间、固定错误码、HTTP/进程退出码、必要 SHA、清理结果；不要整包粘贴环境变量、响应正文或 Provider 原始输出。涉及实际交易/资金的异常保持原执行限制。

## 4. 停止与 Worker 恢复

```powershell
pwsh -NoProfile -File .\infra\dsh\stop-product.ps1
```

原停止脚本先写主动停止标记，核验启动器和角色身份，再停止受管子树；保留数据与日志。有未确认存活子进程或身份不符时会报错并保留状态。核对实际返回结果、PID 加创建时间及端口，不全局结束同名 Python/Node，也不删除受管状态文件来假装停机成功。

Worker 恢复优先沿原 durable claim/checkpoint/resume 路径核对同 Run。当前研究 Worker 在 recovered 且存在 checkpoint 时调用原 `resume`，不是默认重新创建研究。失败先保存原件、复现/定位具体原因，再决定是否需要新 Run；未知提交结果先按原请求 ID 查回执，不重复 POST。不得改原失败分母、回填时间、续期或人工改 SQL 来制造恢复成功。

## 5. SQLite 一致备份与恢复预演

使用已有 `tools.ops.product_assets`，先通过原停止流程核实产品及相关写入角色已停；工具的默认端口检查不能替代其他自定义实例的所有权核对。确认实际 Hub 数据根、全部曾使用的 DSH Home 和输出新目录后执行：

```powershell
.\.venv\Scripts\python.exe -m tools.ops.product_assets snapshot `
  --hub data/core-live-20260913 --dsh data/dsh-live `
  --output data/product-local/backups/<新的快照目录>
.\.venv\Scripts\python.exe -m tools.ops.product_assets restore `
  --snapshot data/product-local/backups/<已完成快照目录> `
  --output data/product-local/recovery/<新的预演目录>
```

若存在历史 Home，按实际情况追加原 `--extra-dsh-home`，不能把示例中的旧 Home 当作当前事实。工具对 SQLite 使用 backup API 和 integrity check，保存 Hub 原始资产及 DSH sessions/storages，逐文件校验并输出 manifest；源变化或外部链接会拒绝。不要在 WAL 活动时仅复制主 `.sqlite3` 文件冒充一致备份；失败目录不能当作完成的快照。

凭据、既有私有配置、仓库构建/presets 和原根路径是单独的恢复前提；此工具的 DSH 资产范围不包含 `.env`，不得把密钥写入公共备份清单。新目录恢复是完整性预演，数据库绝对引用仍可能指向原位置。真正恢复需协调数据库、checkpoint、原始文件与会话的同一版本、路径及构建身份；工具不会自动覆盖正式目录。保留恢复前原件，不用迁移降级或“重建空库”替代恢复。

只需单库副本时，已有 `tools.ops.database backup/integrity/restore` 可用，但源必须已存在、目标必须经核定，且该工具本身不代表全资产恢复。单库成功不能证明历史报告原件和 Session 已齐备。

## 6. 时钟与长期验收边界

研究需要有来源、时点和可验证误差界的 UTC/PIT 事实；系统授时状态、外部采样与应用时钟接入分别核对。历史已测偏差不是当前偏差，更不是可硬编码的补偿常数；单次 NTP 样本、系统显示时间或本机管理员权限都不能证明长期合格。异常时保留 unknown/失败，不回写旧 cohort 时间，不放宽 freshness。

Q024 的系统同步/偏差限属于操作政策，应与实际金融业务要求区分；任何替代授时都须事前证明误差与连续性并真实接入。不要自行改系统时间、发起提权或将一次校时追认为过去研究合格。

当前尚不能宣称全部交付完成：真实六组合连续 **14 自然日合格率 ≥90%**、条件后继研究合格率、P50/P95 **15/30 分钟**、至少两类真实事件全链、至少 **10 笔**覆盖完整退出和恢复的 PAPER、至少 **4 周**真实评分及恢复/历史读取等门仍需逐项原产品验收。缺失、正常 revise、失败、未成熟样本和封存批次均保留；合成测试、私有工具、离线回放或单例成功不替代这些门。

## 实现与既有说明

- 启停和身份：[run-product.ps1](../../infra/dsh/run-product.ps1)、[stop-product.ps1](../../infra/dsh/stop-product.ps1)、[decision-settings-session.ps1](../../infra/dsh/decision-settings-session.ps1)。
- 构建与迁移：[build-product.ps1](../../infra/dsh/build-product.ps1)、[Database](../../packages/kernel/decision_hub_kernel/persistence/db.py)。
- 备份：[product_assets.py](../../tools/ops/product_assets.py)、[database.py](../../tools/ops/database.py)、[本机产品恢复说明](../operations/LOCAL_PRODUCT_RECOVERY.md)。
- 原界面：[decisionSettings.ts](../../apps/decision-desk/src/api/decisionSettings.ts)、[PerpetualSavedReport.tsx](../../apps/decision-desk/src/product/PerpetualSavedReport.tsx)。
- 验收覆盖：[原产品增量交付需求覆盖账本](reports/INCREMENTAL_DELIVERY_COVERAGE.md)。旧 runbook 中端口、迁移 head、模型和执行状态的历史示例不能覆盖当前源码或本次原件。


## 维护恢复与到期采集（2026-10-27补充）

本次实际恢复：Root session86756，outer3108/creation FILETIME134375443675345354，UTC03:12:47.4966142Z启动。沿原WORK14、owner、数据库、账户和设置，workspace回读created=false；新named profile web-event-paper-20261027，原web及SDK会话字节保留。API/acquisition/MCP/Host/realtime/research六角色PID加创建时间均曾独立确认在线。realtime为Sources0/Market1/LLM0/风险和执行0，不把它当纯只读进程；原Study审核政策保留，后续研究可能使用模型。没有重跑initialize、bootstrap或研究POST。

当前精确停止入口为D:/DH-work14-actual-release-inputs01/Stop-PAPER-Preview-Resume.ps1，只核本次outer身份并向runtime/preview-resume01/stop发停止信号；已语法核对，尚未执行。不得执行旧预览停止脚本或按同名进程全局结束。当前原页面2/3已定位缺少独立hub-research-priority-worker，不是来源/history缺项或心跳过期。普通research可空闲接单，不能抢占长任务；后继新controller须真实增加owned优先角色、readiness与cleanup，不插心跳或改分母。六进程存在不能当全系统健康。报告GET本次200耗时50.430s，评分200耗时0.319s；慢读取不是新研究失败。详见上述恢复实测。

旧预览已按PID及创建时间匹配停止，所属进程清理完成。新构建须沿同一个controller等待真实终态，随后做一次有限postflight。空日志和观察超时不等于失败；历史链校验会读取多份旧注册表，不另开重复构建或跳过原完整性检查。

恢复时保留原数据库、owner、账户、设置、已保存报告和SDK会话原件。新插件使用独立named profile，原profile和依赖保留；不要原样重跑首次clone／initialize／owner bootstrap。新身份必须从实际成功构建绑定。先沿同Run原GET验证历史报告和评分，不能重新POST研究替代回读。具体脚本与停止入口以实际恢复回执为准。

API、acquisition、MCP、Host四服务不覆盖持续到期评分；原 `apps.hub_worker.main --role realtime` 同时处理成熟预测与已保存周期计划，具有真实业务副作用。完整研究另需research角色。按原功能开关分角色配置并核来源、市场、LLM和执行范围，不将realtime称为纯只读评分进程。PAPER执行另核其配置、许可、报价和回执，不以研究进程在线替代。

用户管理员授时后，有效同步事件和毫秒级NTP偏差已独立复测。当前时间服务Manual／Stopped，一次成功不证明持续时间健康；长期运行需持续保存真实偏差。历史known_at、原窗口、旧评分和封存试验不回填或倒签。
