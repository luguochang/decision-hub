# Windows 本机完整产品启动与研究验收

本手册用于复验 Decision Desk → Hub API → Research Worker → 官方 DSH Web → 只读工具 → Artifact 的真实链路。健康检查通过只说明服务可访问；验收必须产生可从 API 重新读取的报告，并保留证据与轨迹。

## 正式启动方式

在仓库根目录打开 PowerShell，确认 `node`、`pnpm`、`uv` 可执行；本机已有锁定的 `.cache/dsh-upstream/source` 与 `data/dsh-live/.env`。`.env` 至少要有可用的 `OPENAI_API_KEY` 和与 Provider 匹配的 `OPENAI_BASE_URL` / 模型配置。不要输出、提交或粘贴密钥及 DSH 启动器打印的带 token URL。

```powershell
.\infra\dsh\run-product.ps1
```

保持此终端运行。启动器会构建当前 Decision Desk 和 DSH 插件，启动同源 API、官方 DSH Web、MCP、来源服务、realtime/documents/research/priority/historical workers，然后等待 readiness。业务入口是 `http://127.0.0.1:8047/#/home`（今日研究）；DSH Web 是内部轨迹控制台，首次进入需使用启动器打印的完整认证 URL。本机这条 Windows 路径直接启动进程，不要求 Docker Desktop；Linux `run-product.sh` 使用 Compose，才要求 Docker daemon。

`hub-evolution-worker` 是 owner 发起策略演进时运行的独立进程，不属于日常研究常驻进程；未发起演进时 `/v1/operations` 显示它为 `offline` 是当前启动策略。日常验收核对 API、来源发现、资料处理、普通/优先研究 Worker、DSH 与 MCP；演进功能另以候选创建/评审流程验收，不能把“默认离线”写成已运行。

已有实例需要干净重启时，先在另一个 PowerShell 执行：

```powershell
.\infra\dsh\stop-product.ps1
.\infra\dsh\run-product.ps1
```

`stop-product.ps1` 只处理 `data/product-local/processes.json` 中校验过命令标记的进程，保留 SQLite、DSH Session、checkpoint 和日志。不要删除 `data/core-live-20260913` 来“修复”失败。

本次 UX V3 变更涉及 0042 来源轮询账本与 0043 浏览器回执时间线；API 启动时会将当前数据目录升级到 Alembic head。升级前应给 `data/core-live-20260913/db/decision_hub.sqlite3` 做 SQLite 一致性备份，升级后核对 `alembic_version` 为 `0043_source_acquisition_receipt_timeline`。2026-09-27 的升级前备份及实测见 [UX V3 实施与验收记录](../product/DECISION_HUB_UX_V3_IMPLEMENTATION_2026-09-27.md)。

## 完整验收

> 2026-09-30当前执行范围：用户要求先完成PC，手机验收暂停。下述旧总验收与双视口脚本包含移动用例，当前不要直接按旧全量入口执行；PC按唯一执行台账逐项验证。步骤及证据见 `docs/plan/ARCHITECTURE_REMEDIATION_EXECUTION_LOG.md`，运行恢复/用量归集见 `docs/operations/LOCAL_PRODUCT_RECOVERY.md`。

需要一次执行全项目验收时，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m tools.full_product_acceptance
```

它会复用健康实例；如果任一常驻服务、DSH 控制器、研究 MCP 或来源采集服务未就绪，会调用受管停止脚本并隐藏启动 Windows 正式启动器。随后执行前端和 DSH 插件构建、全部 Python/前端/插件测试、来源采集浏览器 smoke、隔离恢复回放、私有测试资料流水线、在线 API GET 合同矩阵、38 个生产页面和移动端浏览器交互矩阵，并等待关联资料的真实 DSH 研究 Run 提交 Artifact，最后检查虚构资料 Claim 未进入 Evidence。详细结果写到被 Git 忽略的 `data/product-local/full-acceptance-latest.json` 和 `data/product-local/acceptance-ui/latest.json`。重复运行复用测试资料与 Run 的幂等键。[2026-09-26 全产品验收记录](../hand-off/FULL_PRODUCT_AUTOMATED_ACCEPTANCE_2026-09-26.md)列出覆盖、跳过项和问题处理。

UX V3 的一级入口、结果跟踪、来源轮询与数据作业还有独立的双视口浏览器检查：

```powershell
node tools/ui_v3_browser_acceptance.mjs
```

它从正式 `8047` 入口读取真实 API，在 1280×800 和 375×812 检查 16 个页面状态、主导航、数据作业入口/返回、脚本错误与横向溢出；截图和结果写到 `data/product-local/acceptance-ui-v3/`。旧 38 状态和真实 DSH 链仍由总验收工具覆盖。

DSH Web 使用本地认证：裸 `http://127.0.0.1:3097/` 返回 401 是预期行为。首次打开需复制启动器打印的完整带 token URL；总验收工具会在本机读取该 URL 并验证认证页面可正常加载，报告中不包含 token。

在另一个 PowerShell 终端运行：

```powershell
.\.venv\Scripts\python.exe -m tools.local_product_acceptance --submit
```

脚本会检查 API readiness、当前 `research-inbox-view.v1` 契约、`dsh-web` runtime、普通和优先级 Worker 在线、业务首页可加载；随后提交一条真实中文 BTC/宏观研究，等待终态，并要求 Artifact、Evidence、规范化 Trace、行动摘要和 Run view 中的 Artifact ID 一致。`research_only` 是可以提交的受限研究报告，须阅读报告里的交易授权边界；`failed`、无 Artifact、Worker 离线或超时均会使脚本非零退出。首次输出中的 `run_id` 可用来复验同一账本，不重复提交请求：

```powershell
.\.venv\Scripts\python.exe -m tools.local_product_acceptance --run-id <run_id>
```

浏览器打开业务入口，核对“获取最新操作建议”、请求进入研究列表、Run 的状态与阶段、证据、轨迹、报告结论和重试入口。单独的 `local_product_acceptance` 验证 API 业务链路；一键总验收还使用 Playwright 点击主要导航、输入、筛选、日历、DSH 标签与报告入口，并保存桌面及移动截图。

隔离的恢复回放验收（不触网、不写正式账本）：

```powershell
.\.venv\Scripts\python.exe -m tools.research_recovery_smoke
```

它应输出 `status=passed`、一个 Artifact、一条 Evidence、一条 outbox，第二次 Worker 执行 `no_run`。Windows 上该脚本会在清理临时目录前释放 SQLAlchemy 连接，避免 SQLite 文件锁导致功能通过但进程退出码为 1。

## 故障定位

| 现象 | 检查与处理 |
| --- | --- |
| `dockerDesktopLinuxEngine` 不可用 | Windows 本机按本手册使用 `run-product.ps1`；只有 Compose 启动路径需要 Docker。 |
| `dsh_sdk_unavailable` | 先核对 Worker 的启动方式。裸 `python -m apps.hub_worker.main --role research --once` 默认走 Python SDK；正式 Windows 产品启动器显式选 `DECISION_HUB_RESEARCH_RUNTIME=dsh-web`，不依赖该 SDK。 |
| API 健康但 Worker 离线 | 查询 `http://127.0.0.1:8047/v1/operations`，查看 `data/product-local/logs/research-worker.stderr.log` 与 `research-priority-worker.stderr.log`。不要把 API readiness 当作完整验收。 |
| `UNIQUE constraint failed: review_schedules.trigger_hash` | 旧代码中两个 Worker 同时发现同一复查计划，先查询后插入发生竞态。当前代码用 SQLite `ON CONFLICT DO NOTHING` 原子插入，且只有普通研究 Worker 负责复查调度。执行 `pytest tests/research/test_review_schedule.py -q` 验证并发用例后重启。 |
| Run 长时间停在 `researching` | 先看 `v1/operations` 服务心跳和 Run 的 Evidence/Trace 是否增长，再看 DSH 与 research-worker 日志。不要用新的请求覆盖原 Run；进程恢复后用 `--run-id` 验证原账本。 |
| 旧 UI 或契约 | 正式启动器会重新构建当前前端与插件；`local_product_acceptance` 会检查 `research-inbox-view.v1`，避免旧服务只因 `/health/ready` 通过就被误认作当前产品。 |

日志位于 `data/product-local/logs/`，启动器的受管进程清单位于 `data/product-local/processes.json`。排障材料可以记录错误码、Run ID、Artifact ID、阶段与数量；不要记录密钥、认证 token、原始 Provider 响应。
