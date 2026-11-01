# 全产品自动化验收记录（2026-09-26）

## 本次结论

Windows 本机正式产品链路已启动并通过自动化验收。业务入口为 `http://127.0.0.1:8047/`；前端由 Hub API 提供，不能用旧的 Vite 开发地址 `http://127.0.0.1:5173/` 判断正式产品是否在线。DSH Web 在 `127.0.0.1:3097` 运行，裸 `/` 返回 401 是本地认证要求；启动器日志中的完整带 token URL 已在本机浏览器测试中返回 200，token 不写入验收记录。

修复移动样式和资料链接后，正式产品完成受管重启；一键验收总报告状态为 `passed`，14 个门槛全部通过。

`/v1/operations` 显示 `dsh-web`、provider 模式，Hub API、Realtime Worker、普通及优先级研究 Worker、Documents Worker 在线。来源采集服务 readiness、研究 MCP 端口、DSH Host readiness 的 `ready/version_compatible/session_controller/client_plugin/hub_reachable` 均通过。Evolution Worker 是 owner 命令触发的按需进程，常驻状态 `offline` 是当前启动设计。

## 覆盖与结果

| 验收层 | 本次结果 | 证据 |
| --- | --- | --- |
| Windows 启动门槛 | API、DSH、MCP、采集服务和 5 个常驻服务就绪 | `tools/full_product_acceptance.py` 的 `product_ready` |
| 构建 | Decision Desk、DSH 插件通过 | 总验收 JSON 的 `web-build`、`dsh-plugin-build` |
| Python 单元与集成 | 791 passed，4 skipped | `python-all` |
| 前端与插件测试 | 26 + 93 passed | `web-tests`、`dsh-plugin-tests` |
| 来源采集 | TypeScript 类型检查、网页与 PDF 下载 smoke 通过 | `acquisition-typecheck`、`acquisition-browser-smoke` |
| 隔离恢复 | 重放后仅提交一个 Artifact/Evidence/outbox，二次 Worker 不重复领取 | `research-recovery` |
| 资料写入链路 | 上传私有测试资料、原文回读、解析、修正、接受、Claim 拒绝、关联研究提交 | `document-flow` |
| 在线 API | 53 个 GET 合同中 50 个通过；3 个有明确跳过原因 | `live-api-matrix` |
| 浏览器 | 38 个设计状态可加载；83 项导航、输入、筛选、日历、DSH、报告、资料链接、移动宽度与运行时检查通过 | `tools/product_ui_smoke.mjs`，`acceptance-ui/latest.json` |
| 真实 DSH 研究 | 关联资料的 Run 写入 Artifact、13 条 Evidence、96 条 Trace | `live-library-research` |
| 测试资料隔离 | 1 条虚构 Claim 保持 rejected；13 条最终 Evidence 均无测试资料内容 | `tools/product_fixture_exclusion.py` |

关联 Run `run_94b800023ad2421983a64f8868674c2b`，Artifact `art_058a2ef25cd14ebba31dae3638554fd6`，终态 `research_only`。这是 Gate 提交的受限研究报告，有结论和审计证据，但不授予交易执行权限；不是 DSH 启动失败。最终 JSON 保存在 Git 忽略目录 `data/product-local/full-acceptance-latest.json`，浏览器逐项结果与按钮快照保存在 `data/product-local/acceptance-ui/latest.json`。等待页面数据稳定后，按钮快照共记录了 38 个状态中超过 1100 个可见按钮实例；交互测试覆盖主要工作流，快照不等同于逐一点击全部按钮。桌面首页、桌面报告与移动首页截图保存在同一目录。

在线 API 的 3 个跳过项：`/v1/evaluations/{evaluation_id}` 因当前账本没有真实 outcome evaluation ID；`/v1/{path}` 与 `/{path}` 为回退路由，不是可枚举业务 GET。完整 Python 测试覆盖相应模型与服务逻辑；本次没有为了填充验收数字伪造 outcome。

## 发现的问题与处理

1. **“DSH 没启动”的表象**：旧页面停在 `5173`，但正式 Windows 启动器将当前生产前端挂在 `8047`。另外 DSH Web 裸根路径需要本地认证，401 是预期保护。验收脚本现在同时检查 DSH Host readiness、官方认证页面 200、运行时身份和真实研究 Artifact，任何一个失败都会非零退出。
2. **此前 `dsh_sdk_unavailable`**：裸 Worker 命令走 Python SDK，本机没有此 SDK；正式启动器显式设置 `DECISION_HUB_RESEARCH_RUNTIME=dsh-web`。真实 DSH Web/Provider 的新 Run 已提交 Artifact，说明本机正式运行路径可用。详细修复过程见 [Windows 本机真实产品验收记录](WINDOWS_LIVE_PRODUCT_ACCEPTANCE_2026-09-26.md)。
3. **两个研究 Worker 的复查计划竞态**：已改为 SQLite 原子冲突忽略插入，并由普通研究 Worker 执行复查调度；并发回归测试通过。
4. **隔离回放在 Windows 上退出码 1**：已在清理临时 SQLite 目录前释放连接；恢复 smoke 与总验收均通过。
5. **报告“Evidence 不足”误导文案**：硬要求覆盖已通过时，页面改为解释受限报告的复核、校准和交易权限原因；前端测试和真实浏览器报告页已复验。
6. **自动化工具漏掉运行时故障**：总验收入口现在先确认整套常驻服务和 DSH 控制器就绪，再运行 API/浏览器/真实研究门槛；不会仅凭 `/health/ready` 宣告成功。重复执行复用私有测试资料和 Run 的幂等键。
7. **移动首页卡片空白过大**：截图发现三张首页卡片在 375px 宽度仍继承桌面端 520px 最小高度。移动断点下已取消该下限，重新构建并复拍后卡片随内容收起。
8. **报告资料链接指向错误 ID**：前端曾把研究 `context_manifest.selected_inputs` 中的 Evidence ID 当成资料版本 ID，点击会打开不存在的 `docver` 路径。后端新增只读 `GET /v1/research-library/context/{run_id}`，返回持久化的真实 `selected_version_ids`；前端按该契约生成链接。受管重启后，真实 Run 的报告链接、跳转和资料详情加载三项浏览器检查均通过。

构建工具仍输出一个前端 chunk 大小警告；来源采集 PDF smoke 在 Crawlee 内部会记录一次预期的浏览器下载导航错误，随后成功保存下载并形成成功回执。这两项不影响本次功能结果，日志保留以便后续处理。

## 下次一键复验

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m tools.full_product_acceptance
```

脚本在已有健康产品上复用实例；若任一常驻服务或 DSH readiness 不符合条件，会先按受管进程清单停止并重新启动正式 Windows 产品，再执行完整门槛。手动启停、认证 URL 及故障定位见 [Windows 本机完整产品启动与研究验收](../runbooks/windows-product-live-acceptance.md)。测试资料明确标为 `local_only/private` 且其虚构 Claim 被拒绝；不要将其当成行情或真实研究材料。
