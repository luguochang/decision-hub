# 日常开发与正式发布

> 2026-10-29 最新实证：本页源码开发流程已完成 BTC、ETH 各一次真实4h研究、独立复审、保存及正常owner报告认证读回，全程没有调用完整发布打包。BTC报告degraded（后查child装配缺口）、ETH completed；概率均保留历史先验，不据此授予模型增益/金融/正式资格。实例现场状态及正在修订的源码以 HANDOFF_RECOVERY_CURRENT_2026-10-29.md 为准，不能沿下方旧“保持运行”快照直接重复启动。

日期：2026-10-28。**开发入口已完成实际冷启动、自动初始化、停止与重启验证。** 默认实例 `data/development/default` 在本次交付时保持运行，前端 `http://localhost:5173`。服务当前状态仍以现场 readiness 为准。真实 BTC/ETH 研究与正式发布没有据此通过。

## 当前约定

用户已批准将日常开发与完整发布存证分开，实施必要的开发启动、预检、清单预算修复及文档变更。此前 `HANDOFF_CODEX.md` 的“只执行构建、启动、记录”是当时的任务范围，不再阻止本轮已授权的开发工具改造。旧全项目目标仍暂停，不从 T-000 重新执行。

本页是日常开发入口。完整发布仍使用 `build-product.ps1` / `run-product.ps1`，须有真实通过的回执；开发会话不生成、复用或冒充正式发布回执。历史归档、正式数据库和以前的失败产物保留。

## 按改动范围选择验证

| 改动 | 日常验证 | 需要重启/构建的部分 |
|---|---|---|
| Python 基准率、概率、业务逻辑 | 相关 pytest、Ruff、类型检查 | 重启开发会话加载新源码；未变的插件复用，不因此封存整个仓库 |
| Decision Desk 前端 | Vite dev、相关 Vitest/类型检查 | 浏览器热更新；无需日常完整发布构建 |
| DSH 插件、角色协议、共享 TS 合约 | 相关测试及插件局部构建 | 保持插件身份一致，重启开发 DSH |
| 跨服务研究链 | 独立开发会话内的 API/DSH/worker 和真实原件验证 | 同一开发会话，保留审核和来源校验 |
| 确定正式发布候选 | 先独立预检，再完整留存、构建、正式回执与验收 | 正式发布流程；开发通过不替代正式资格 |

不能用“文件存在”“旧页面能打开”或历史回执证明当前源码可用。Python 测试、组件构建、启动就绪、真实研究和正式发布是不同验收项，分别记录结果。

## 当前已知事实

- 上一轮 32 项基准率/评分测试在新短临时目录全部通过，384.70 秒；错误长路径为 278 字符，对照路径为 231 字符。
- 上一轮完整发布构建运行 5083.733 秒，约 84 分 44 秒，最终失败。候选清单到留存回执约 4788.528 秒；UI、Host 编译均 0 次，启动和 BTC/ETH 真实研究未执行。
- 根错误 `assessment_basis_immutable_binding_limit`：历史组件 `source-binding.json` 的认证运输内容累计触及 256 MiB。
- 本轮单遍认证汇总：142 份路径、138 份不同 SHA；每份索引 schema/SHA 均匹配。原始总量 268,479,412 字节；唯一内容 268,239,598 字节；最大单份 5,929,105 字节。
- 按唯一内容计算后，当前预算余量为 195,858 字节。**这不是无限容量方案。** 日常开发不再生成完整历史归档；后续正式发布仍需检查预算，不能靠删除历史或直接加大阈值处理。

证据目录：`artifacts/incremental-delivery-20261023/r28010837/`；本轮实现证据见验证记录。

## 清单预算的修复契约

保留每个路径的实际 SHA 在 manifest 内。即使两份文件声称同一 SHA，也必须分别读真实字节并认证各自索引；不能因已见过 SHA 就跳过文件验证。运输内容通过认证后，相同内容只占一次唯一内容预算。

256 MiB 在本轮明确为“已认证唯一运输内容量”预算，**不再声称约束重复路径的总 I/O 字节数**。仍保留文件数量、单文件大小、普通源码累计预算、读取分块和 checkpoint 边界。新增不同历史内容仍可能触发预算，篡改和资源超限必须继续失败。

此修复不改变历史归档字节、不解除来源或独立审核、不提升金融/执行资格。manifest/verifier 身份变化按原机制处理，不能改写旧回执制造匹配。

源扫描还提前排除原先就不计入 manifest 的历史 payload 和忽略目录；有效文件仍按相同顺序逐路径读取、哈希和认证。小夹具的路径/顺序/原始 SHA 一致，真实 kernel 的 1,393 个有效路径也完全一致。单次仅元数据对照为旧约 20 秒、剪枝后约 0.194 秒，主要节省被排除文件的逐项 stat/分类；这不是 API 倍率或完整冷启动基准。

## 快速 manifest 预检

以下命令在仓库根目录的 PowerShell 7 内执行；最终版本真实独立预检 exit 0、`passed=true`，17.529 秒，见本页末尾。

```powershell
$sha = (Get-FileHash packages/evals/assessment_basis.py -Algorithm SHA256).Hash.ToLowerInvariant()
& .venv/Scripts/python.exe -B infra/dsh/assessment-manifest-preflight.py --repository-root . --expected-source-sha256 $sha --phase standalone
```

预检只输出一份小 JSON：实际通过须 exit 0、`passed=true` 且有有效 manifest hash。失败保留真实错误码，不继续完整发布构建。它不生成产品回执、不启动服务、不写数据库或发布指针。

发布构建中执行两次检查：

1. 留存、生成身份、安装指针之前做 early 检查，尽早暴露已有清单/环境错误。
2. 构建自身可能新增归档、改变合法身份，因此封闭最终 SourceBefore 后仍做 final 检查；early 通过不能代替 final。

## 独立开发环境

实际联调已验证 API、DSH、7 个服务、Vite/HMR、owner/CSRF 与安全停止。这里提供开发反馈流程；BTC/ETH 当前真实研究及正式发布尚未通过。

本机已实测的 PowerShell 7 路径如下。先在终端执行一次；即使当前终端是 Windows PowerShell 5.1，也通过此路径运行脚本：

```powershell
Set-Location 'E:/project/study/codex/dsh/decision-hub'
$pwsh7 = 'C:/Users/90603/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
& $pwsh7 -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
& $pwsh7 -NoProfile -File infra/dsh/run-dev.ps1
```

默认实例为 `data/development/default`。首次或输入/产物已变化时仅局部构建 DSH 插件；已记录且逐哈希未变化的插件会复用。需要强制重编插件时增加 `-BuildPlugin`。这条入口不调用完整发布存证，也不写正式 installed-release 指针。

默认端口：Decision Desk `http://localhost:5173`、API `127.0.0.1:18047`、DSH `127.0.0.1:18097`、研究 MCP `18037`、采集 `18038`。开发前端用 localhost，与正式默认 127.0.0.1 的 owner cookie 分离；内部 RPC 保持 loopback。

使用启动日志给出的私有 `.url` 快捷方式进入 owner 会话；裸前端地址不代表已配对。默认文件为 `data/development/default/runtime/private-settings/Decision Hub.url`。不要把其中 pairing token 或 DSH 的认证 URL 复制到文档/聊天。

停止同一实例：

```powershell
& $pwsh7 -NoProfile -File infra/dsh/stop-dev.ps1
```

命名实例（启动和停止必须用相同根目录）：

```powershell
& $pwsh7 -NoProfile -File infra/dsh/run-dev.ps1 -DevelopmentRoot data/development/mywork
& $pwsh7 -NoProfile -File infra/dsh/stop-dev.ps1 -DevelopmentRoot data/development/mywork
```

实例名只允许有限字母/数字/下划线/连字符。可以显式选择各端口、`-CredentialFile` 和 `-ResearchModel`；不允许用开发参数指向正式目录或启用正式交易/学习功能。默认从 `data/dsh-live/.env` 只读取得已配置的模型路由/凭据，新的 DSH home/profile 与正式环境分开；不会写回正式 `.env`。

运行期间保持终端/supervisor 存活。修改 Python 后，在研究队列空闲时停止并重启开发会话；前端由 Vite 热更新。研究进行中应保持源码固定，避免破坏该 Run 的实际来源身份。当前入口尚未提供任意单角色热替换，不把整会话重启写成已实现的单角色重启。

同一源码工作区的插件产物是共享的：正式产品或同一开发实例仍运行时，开发启动拒绝重编共享产物；应先按对应环境的正式停止方式结束自己的会话，不误杀占用端口的其他程序。

隔离约束：

- 独立的开发数据、DSH home、workspace、日志、进程状态及端口；不覆盖正式 `core-live-20260913`、`dsh-live` 或 `product-local`。
- 从明确凭据来源只读加载必要模型配置，不复制正式 profile，不在文档或日志打印凭据、pairing token。
- 首次 DSH profile 使用官方插件装配方式，并通过当前开发 DSH 的官方 `workspace/create` 注册隔离工作区；插件改动仅局部构建，不进行完整历史存证。
- API/worker 运行当前 Python 源码；前端使用 Vite dev，并使 owner 会话 Origin/CSRF 与开发前端地址一致。
- 不继承正式 retained receipt、比较/历史私有策略或未请求的技能/交易功能；普通永续研究继续做真实原件冻结与审核。
- 停止只作用于该开发会话的实际 PID 和创建时间；无法证明归属时保留状态并拒绝误杀。

内部健康探测固定 `127.0.0.1` 且绕过系统代理；浏览器 owner 地址仍为 `localhost`。本机实测 .NET 的 localhost 探测曾因地址选择超时，不应通过增加超时或忽略 ready 处理。已有 owner-only ACL 幂等复用，不需要管理员 `SeSecurityPrivilege`。正式 supervisor 的旧 PID 被系统进程复用时，通过实际创建时间识别；不结束系统进程、不改正式状态文件。

全新目录还需避免重复设置 owner：目录属于当前用户，并不代表继承的 Modify 权限包含 `WRITE_OWNER`。开发 helper 先认证当前 owner，仅修改 DACL 并复核 owner-only；外来 owner 拒绝。登录私有目录及两个会话文件先按同样方式保护，再由原 `New-ProductSettingsSession` 生成真实随机 token/签名及快捷方式。正式会话 factory 保持原样，不复制或伪造凭据。

### 旧 V4 发布服务与当前永续报告

源码启动原先在 API 初始化时无条件构造旧 V4 frozen publication 服务，该服务需要正式 normal registry；这会让仅清空回执的开发入口启动失败。开发模式现在明确分离这项能力，不伪造 registry 或发布确认。

普通永续 V5/V6 已有自己的保存/读取机制：`PerpetualSavedReportQuery` 认证 owner、实际 source、artifact、graph-final 和独立审核，保留原 API `/v1/decision-settings/research/runs/{run_id}/report` 及其 `/source`。这些结果本就保持 `financial_qualified=false`、`execution_authorized=false`，开发模式不能将其提升。

开发模式不启动旧 V4 publication 队列，不提供它的正式 quality/确认/恢复路由；通用 confirmed reader 明确失败关闭。普通研究的一般 lane 仍保留真实用量采集与会话恢复。正式模式继续使用原 factory、回执与来源认证，不能用开发状态替代。

publication 路径只允许由实例名派生为源码外 `.decision-hub-development/<实例>/publication`；开发配置会检查越界/链接，但当前禁用旧 V4 服务时不创建或使用该存储。checkpoint、document bundle、source acquisition 与 DSH home 必须落在该实例的固定隔离路径，不能继承正式路径。

## 测试临时目录与日志

使用 `artifacts/incremental-delivery-20261023/` 下的新短目录，避免把长 SHA 文件名再嵌套到冗长的轮次目录。每次使用新目录，不让 pytest 清理旧记录。

pytest 使用 `-p no:cacheprovider`，必要时显式指定短 `--basetemp` 与日志路径。源码测试设置 `PYTHONDONTWRITEBYTECODE=1`；相关测试所需的 `PYTHONPATH` 以既有验证命令为准。不要为修复执行路径而改测试断言或产品代码。

最短反馈顺序：先跑改动对应的测试/类型检查；Python 改动重启此开发会话；前端通过 Vite 热更新；插件改动让启动器自动局部重编。只有确定正式候选时才运行完整发布构建。当前真实局部插件打包为 7.08 秒，Vite 自报启动 389 毫秒；这两个数不等于整个服务组冷启动时间。

最终新默认实例的局部插件打包为 6.41 秒，自动注册工作区后，API/DSH/7 服务及完整权限检查的观察验收在启动后 157.545 秒完成（约 2 分 38 秒，含检查与控制端调度时间）。这是一次现场记录，不是承诺的最低冷启动基准。随后官方 stop 成功，5 个端口、所记录进程均释放，正式 6 个保护文件字节未变。

同一实例重启的观察验收为 115.347 秒，官方 workspace 返回 `created=false`。插件构建日志与 stamp 的 SHA、修改时间均未变，证实本次复用而没有重新编译。重启后全部检查再通过，原前端实际显示“API 与研究进程 3/3”；正式 6 文件再次对照未变。使用私有快捷方式 `data/development/default/runtime/private-settings/Decision Hub.url` 配对设置页；原 `39142` 页面的旧 run 不属于本次开发验收。

在 PowerShell 7 中（当前为 5.1 时先执行 `& $pwsh7 -NoProfile`），可用以下只读端点检查当前默认端口；必须检查 JSON 的真实状态，不把 SPA fallback 的 HTML 200 当作通过：

```powershell
Invoke-RestMethod http://127.0.0.1:18047/health/ready -NoProxy
Invoke-RestMethod http://127.0.0.1:18097/decision-hub/v1/readiness -NoProxy
Invoke-RestMethod http://127.0.0.1:18047/v1/operations -NoProxy
Invoke-RestMethod http://127.0.0.1:18047/v1/development/status -NoProxy
```

前两项须为 `status=ready`、`ready=true`；operations 的 7 个服务应为 online；development status 须明确开发模式且正式发布/金融/执行资格为 false。新开发库不复制正式研究数据或设置。当前实测 `options` 返回可选合约 0，缺少可核验的交易所合约元数据；应先按产品原流程读取真实元数据，再在原设置页配置研究对象。该初始化尚未完成，不能把空设置、测试夹具或旧 run 当作 BTC/ETH 新研究验收。

## 正式发布注意事项

- 当前正式候选尚未通过，不能直接把之前失败构建的生成身份/安装指针当作发布版本。
- 修改启动脚本或评测实现后，旧回执不再匹配当前源码属于正常拒绝；不重钉旧回执来放行。
- 正式构建始终使用新的输出目录，保留旧产物；先预检，通过后再计划昂贵留存。
- `EnableSpotResearchV3` 正式启动需要构建时的 `InstallSpotResearchV3` evidence。开发普通永续研究与正式 Spot/Action 的启用范围须明确区分。
- 本轮不运行新的完整发布构建，也不据此声明全部项目或长期认证交付完成。

## 验证记录

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 唯一运输内容预算与逐路径认证 | 13 个新增 focused 用例及 4 个关键原用例通过；最终 Main 预检 exit 0、17.529 秒 | `artifacts/db01/summary.json`；`artifacts/incremental-delivery-20261023/d28030518/manifest-final-result.json` |
| 等价目录剪枝 | 17 PASS / 1 SKIP（含绑定回归）；真实 Junction 通过，文件 symlink 因权限跳过 | `artifacts/mp02/summary.json`；`artifacts/mp01/kernel-metadata-summary.json` |
| 独立预检与 early/final 行为 | 新增 15 项通过；本轮未运行完整发布构建 | `artifacts/incremental-delivery-20261023/mpf01/verification.json` |
| 启动器受控回归 | workspace 阶段完整批次 84 PASS / 3 SKIP；最后 DACL/会话增量另有 7 项定向通过及静态 0 新诊断，未重复全批 | `artifacts/dev3-1028a/final-launcher-eleventh.log`、`green-dacl-final.log` |
| 独立开发启动/停止及正式边界 | 实际 stop exit 0、5 端口和已记录进程无残留，正式 6 文件 SHA 均未变 | `artifacts/incremental-delivery-20261023/d28030518/start04/stop-isolation-proof.json` |
| 全新默认实例自动初始化 | 自动官方 workspace 注册 `created=true`；7/7 online，API/DSH/owner/Vite 全通过，随后安全停止 | `artifacts/incremental-delivery-20261023/d28030518/start06/ready.json`、`stop-isolation-proof.json` |
| 同实例重启与插件复用 | 自动 `created=false`，API/DSH/7 服务及权限再通过；插件 stamp/构建日志 SHA 与时间未变；正式 6 SHA 再次未变 | `artifacts/incremental-delivery-20261023/d28030518/start07/ready.json`、`plugin-reuse-proof.json`、`protected-runtime-final.json` |
| API、DSH、Vite 与 owner 会话 | 实测通过；匿名/无 CSRF/错 Origin 均拒绝，正确 CSRF 的坏请求为 422 | `artifacts/incremental-delivery-20261023/d28030518/start04/ready.json`、`owner-vite-http.json` |
| 普通永续 V5 原件保存/读取 | 真实 Graph→Commit→现有 reader 的隔离夹具 1 项通过；owner/改源拒绝；无付费模型调用 | `artifacts/dd01/summary.json`、`v5.txt`；开发分离测试 52 PASS / 3 SKIP |
| BTC/ETH 当前真实 4h 研究 | 未执行 | 不复用旧 run 作为本轮验收 |
| 新完整发布构建 | 本轮不执行 | 不宣称正式发布通过 |

静态检查按前像比较：本轮新增检查错误为 0；assessment 原有 Ruff 4 / strict 6、API/worker 切片原有 Ruff 19 / strict 10 均未增加，不能写成全仓零错误。旧回执相关切片仍有 9 个失败夹具（V1 预期与当前 V2 helper/资源不匹配），没有改旧断言制造通过。已记录的 SKIP、失败和未执行项目均不算通过。

额外 `git diff --check` 对 HEAD 返回 exit 2：worker 的 626 行 CRLF 被报告 trailing whitespace；任务前像原有 616 行同为 CRLF，前像/当前均没有行尾空格或 tab。没有为此重写运行源码或更改 Git 配置；该检查不计通过，见 `d28030518/git-diff-check-result.json`、`line-endings-proof.json`。

当前默认实例日志为 `data/development/default/runtime/logs/`；首次通过的另一实例 `data/development/workflow-20261028/` 已正常停止且保留。本轮观察证据为 `artifacts/incremental-delivery-20261023/d28030518/`，静态非作者审查见 `artifacts/incremental-delivery-20261023/independent-review.md`。正常 stop 会保留数据、日志和状态；观察器对被正常终止的控制进程记录原生 `-1` 与 `intentional_stop=true`，与 `stop-dev.ps1` 命令自身 exit 0 分别记录。

## 后续接手范围

日常功能开发从本页 `run-dev.ps1` 进入；已有默认实例运行时先检查状态，修改 Python/插件需按本页 stop 后重启，不另跑旧完整构建。不要从 T-000 或旧暂停目标重启，也不要为“完成构建”删除历史归档。

后续尚需独立完成：真实交易所合约元数据采集与研究设置初始化、BTC/ETH 各一次当前 4h 研究及 `VERIFICATION.md` 第 4 节验收；再确定正式候选并解决有限归档预算后的持续增长、跑新的完整发布构建和真实发布资格验证。当前开发工具改造不代表这些产品交付项已完成。

## 2026-10-29 研究恢复轮的运行事实（覆盖前文旧现场状态）

本节写入UTC：2026-10-28T16:48:35.5290763+00:00。本轮仍不运行完整发布构建。`run-dev.ps1` 的 start01 只局部编译插件9.61秒；start02插件stamp/构建日志字节与时间完全相同，证实复用而没有再编译。证据在 `artifacts/incremental-delivery-20261023/d28-research-recovery/live/start01/`、`start02/plugin-reuse-proof.json`。这些不是正式发布回执。

新增只读状态入口（PowerShell 7）：

```powershell
& $pwsh7 -NoProfile -File infra/dsh/status-dev.ps1 -DevelopmentRoot data/development/default
```

必须同时看recorded_state与observed_state；旧processes.json的running不代表在线。正常运行应running/running且ownership verified；外部stop确认全部本人进程和监听消失后才stopped/absent。unknown、孤儿、复用PID不能当作已停止，也不能按单个PID误杀。finally仅能证明子树清理/正在退出，不能自证supervisor已退出。

目前时间点的默认实例已由官方stop正常停止，stop02 exit0、status stopped/absent/listeners=[]；改Python的R5d窗口已打开。未来接手先现场status，不按本节快照直接启动或停止。

模型元数据：旧开发初始化硬编码128000，实际曾把214299输入的正常provider stop误判overflow。R5c已按同provider/API/baseURL/model的既有source declaration纠正初始化；default受控迁移只改变contextWindow为262144，maxTokens8192及其他字节不变。**262144只是来源声明，不是远端已验证最大值。** 迁移有精确前像、私有备份和锁，已经执行，不要用旧expectedSHA重复迁移或手工编辑设置。见 `capacity/report.md` 与 `live/capacity-migration.json`。

真实研究状态：两个旧4h run均failed；本轮新BTC run_ec03e5b9ff4f43b3b54cbd09a351dc47也在模型前因FOMC完整原文重复导致277174B>256000B而失败，0模型调用。新研究尚未验收通过。修复只允许无损展示共享，保留两FOMC阶段、四族coverage、原件/哈希/来源和大小门；详见RESEARCH_RECOVERY_PLAN的R5d及CODEX_LOG。没有通过删事件、抬容量或重钉旧身份制造成功。

日历默认all，与首页CPI专用事前预判面板是不同接线；PCE/就业/FOMC完整预测闭环尚未逐族真实验收。范围/具体缺口见 `EVENT_REQUIREMENTS_COVERAGE_2026-10-28.md`。不要据CPI页面或某次普通研究推定其他事件需求完成。
2026-10-28T20:16:09.2216262Z
## 2026-10-29 恢复轮实际开发启动记录

系统UTC见本节前缀。start05沿现有run-dev源码入口成功启动：compiler-reported Vite 6.34秒，全开发启动约123秒（官方start19:54:04.7247631Z→7项ready19:56:07.702618Z）；不是完整发布存证，也不能把6.34秒当整个启动时间。实据在artifacts/incremental-delivery-20261023/d28-research-recovery/live/start05/local-plugin-build-proof.json、ready.json、pre-research-verified.json。新BTC-r5f1正在运行，14生产源与模型settings冻结；不要据旧PID或本段running描述重复启动，先执行status-dev现场核对。

最新简明交接入口：HANDOFF_RECOVERY_CURRENT_2026-10-29.md。研究恢复修复、每次真实失败和未完P3/P6/其他事件/A4测试都单独记账，局部编译/测试通过不等最终报告或正式发布。

已知停止边界：start04首次官方stop已写stop.request并正常触发cleanup，但宽限结束重核owner恰撞退出窗口，命令exit1/identity_invalid；不能记第一次成功。此后现场全部absent/listeners0，Root仅一次官方终态分支重试exit0、stopped/absent，原件全保留（runtime/start04-stop-diagnosis.md）。竞态本身暂未改代码。遇同类情况先保留错误码/最后40行，检查当前身份和端口；不得按旧PID手杀、手改state或连续重复stop。正常开发仍用官方run-dev/status-dev/stop-dev与PowerShell7，不改正式目录。

### Python局部改动的缓存实例（2026-10-29实证）
R5h仅改历史审核材料单份配额，start07默认run-dev未给BuildPlugin。官方生成identity与原stamp/15产物全匹配，build log/stamp未更新，复用原插件缓存；没有执行新的插件编译，更没有build-product。开发从启动到首次ready观测98.153238秒，7服务online。证据recovery/live/start07/plugin-cache-proof-v2.json、pre-research-verified.json、ready.json。不要每次Python修改都强制BuildPlugin；由官方identity检查决定。检查脚本须规范SHA十六进制大小写，先前v1误报与v2更正均保留，不删原错误。

### Windows Python读取UTF8源的命令口径
R5i官方codegen check第一次在Windows默认GBK读取4个已有UTF8中文schema失败；同官方入口加进程参数后通过：`.venv/Scripts/python.exe -X utf8 -m tools.contract_codegen check`。后续Python pytest也明确-X utf8，并记录准确原命令。这个参数不改schema、校验规则或持久环境；旧失败日志保留。完整插件tsc/codegen检查属于受影响项目检查，不等于build-product的正式存证打包。

## 2026-10-29 开发反馈与真实研究耗时分开记录

本节证据根目录为 `artifacts/incremental-delivery-20261023/d28-research-recovery/`；下文 live 与 review 路径均相对此目录。

本轮 start08 的局部插件 Vite 编译为6.3秒，启动到首次7服务ready观测为154.045463秒（包含局部编译、初始化和就绪等待，不能把6.3秒写成整体启动时间）。此前Python局部改动start07复用已验插件，首次ready98.153238秒。无一次新的build-product存证打包，未产生正式发布通过资格。

同一start08源码候选连续完成BTC、ETH4h，两者之间未重编或重启。BTC请求00:23:24UTC，报告01:04:51UTC；ETH请求01:11:50UTC，报告01:52:58UTC，均约41分钟。这里的主要等待是模型研究与独立复审，不是编译。原负评和同Run修订保留；两份报告均正常owner接口认证读回。证据为 `live/{btc,eth}-r5i1-{terminal-proof,final-authenticated-report,final-verification}.json` 及 review 目录独审。

日常修改先选本地范围：新模块/回归用例，受影响项目的类型检查，必要时在研究队列空闲时源码重启。真实模型研究只在验证需要时执行；已存实际录制可先离线回放定位。单纯界面文字、类型/风格修正不自动触发新真实研究。真实研究时冻结源码和模型；结束后再改动，避免把source identity拒绝误判成模型故障。

R5j本地阶段已实际观察TS新11及相关旧31、Python新78及定向旧49运行PASS，完整plugin tsc约128秒复核通过；这是具名候选的局部检查，不等于全仓库全绿。新helper静态收尾、实际录制全链回放与新源码启动验收，以CODEX_LOG最新结果为准。失败日志、严格检查配置错误和后续修正均保留，不能拿计划命令或旧pass替代新版本结果。

复用命令继续使用上文已实测的PowerShell7完整路径及infra/dsh/run-dev.ps1/status-dev.ps1/stop-dev.ps1；由官方输入身份检查决定插件缓存，Python改动不默认强制-BuildPlugin。不要把日常验证重新指向build-product.ps1，也不要删除旧归档来换开发速度。

## 2026-10-29 最终可复用方式：源码重启与历史报告

记录时间：2026-10-29T05:32:08.279422+00:00。完整结果见RESEARCH_RECOVERY_RESULT_2026-10-29.md，现场入口见HANDOFF_RECOVERY_CURRENT_2026-10-29.md。

1. 先以现有pwsh7执行status-dev，已有start10不重复启动；需要改Python/插件/合同/参与recipe资料时，确认无研究→官方stop→局部测试/静态→run-dev源码重启。前端继续Vite。
2. 不把build-product完整存证放日常迭代。本次start10官方缓存复用，两compiler log/stamp未变；198.188秒首次观察7ready是采样上界。此前start09局部Vite6.35秒，真实BTC/ETH研究各约41分钟，旧报告深度验证约一分钟，分别记录不混成编译耗时。
3. validated DEV API bootstrap按实际manifest capture当前小闭包一次，已有身份只核验；固定runtime/history-snapshots下current512files和old509files均保留。读请求不创建旧版本、不扫189归档/全仓。只支持源码重启后冻结，不支持热改Python不重启新发Run。
4. 当前源码有普通hardlinks且SHA全部匹配。仅源regular leaf读入后复制独立snapshot，目标/index/祖先/DB检查仍strict；不得为存证unlink/重拷Main源。Windows长路径由native IO内部处理，不改全局系统设置、逻辑index或manifest。
5. 新测试用D盘短的全新basetemp/log目录，运行前确认不存在，保留失败现场；不重用u1–u5旧目录。E曾实际满，当前约450MB，不把新的大fixture拷回E。迁移后的失败writer副本位置见原路径.location.json/space-recovery receipts。
6. 新单测49=48PASS1平台SKIP，Ruff5files0/strict2helpers0、ACTUAL2和正常owner两报告读回均通过；原A4全仓失败库存/长期与正式发布未通过。不为复盘重发付费研究或重跑原1030/250库存。

成功命令与失败命令均保留在coverage-semantics/r5k-historical-reader/及D:/DH-r5k-actual2/SUCCESSFUL_COMMAND.md。原输出路径已存在，不可照抄覆盖；重复验证必须新标签/新目录并有具体原因。
