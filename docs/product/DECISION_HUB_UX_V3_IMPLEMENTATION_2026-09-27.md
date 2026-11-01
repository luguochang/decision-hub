# Decision Hub UX V3 实施与自测记录

日期：2026-09-27  
目标依据：[全项目 PRD](DECISION_HUB_FULL_PRODUCT_PRD_2026-09-26.md)、[UX V3 页面 PRD](DECISION_HUB_UX_V3_PRD_2026-09-26.md)、[ADR-0025](../decisions/ADR-0025-decision-desk-daily-entry-and-dsh-audit.md)。

## 1. 本轮交付边界

本轮把单 owner、BTC / 宏观的日常研究入口从以手动输入为中心的 V2 工作台，改成以**真实来源状态、最近研究、官方事件和可追溯任务**为中心的 V3 入口。生产数据依旧由 Hub API/Kernel 和 DSH Web 持有；原型中的示例、黄金概念和虚构报告没有写入生产。原有报告、Run、资料、Operations、Evolution、Inbox 和 DSH 技术轨迹深链仍可访问。

| 用户入口 | 当前实现 | 仍需补齐 |
| --- | --- | --- |
| `#/home` 今日研究 | 显示真实来源检查、发现进程与研究进程、最近已保存研究、进行中任务和下一项官方发布；没有新任务用真实空态；临时研究为次级动作 | 稳定事件主题与报告版本归并；当前“最近研究”仍是 Run 投影，不能称作今天新发现 |
| `#/market` 市场与事件 | 只读 BTC / 宏观范围、近期官方事件、状态；完整日历有服务端分页/搜索，事件按 ID 深链 | 稳定 occurrence 与修订链的主题归并、未来黄金独立领域门 |
| `#/research` 研究 | 最近任务、报告、完整任务记录和 `#/results` 结果跟踪入口；Run 列表服务端分页/筛选。`#/report` 首先呈现结论、交易限制与可信度，来源矩阵、证据、研究过程和技术轨迹按需展开 | 真正的 Study/ReportRevision 和 owner usefulness 投影；结果页当前仅显示真实账本量及校准空态 |
| `#/library` 资料 | 资料上传、版本、解析、审核、观点和原件回读保留；默认标题用文件名，范围设置折叠；只允许匹配已审核 BTC 资料进入 BTC 研究 | 解析失败的正式重试、scope→Evidence 引用可视化、逐条修正审核 |
| `#/management` 管理与审计 | 来源/服务、DataJob、旧业务账本、演进审核、技术与设计入口集中可达；`#/sources` 有逐次轮询账本，`#/data-jobs` 显示真实失败作业 | 产品级健康聚合、备份/磁盘与能力授权投影；DataJob 当前接口最多返回 500 项 |

`#/Inbox`、`#/Operations`、`#/Evolution` 旧深链已经修复并有直接打开测试。`#/runs` 和 `#/calendar-*` 的历史查询在服务端分页，`#/event` 按事件 ID 读取，不再依赖首页 50 项。旧失败页调用原 Run 的正式 retry 命令产生关联子 Run；证据深链必须携带 Run ID。到期 Outcome 先筛方向 Forecast 再取批次，避免大量非方向记录占满队列。

## 2. 时间与真实状态

- 来源逐次轮询新增持久 `source_poll_attempts`：请求开始/结束、成功/空结果/失败、数量、游标与错误码；`GET /v1/sources/{source_id}/poll-attempts` 分页读取。未到计划时间的跳过不伪装成一次检查。进程在开始后崩溃会保留 `running`，超时归档尚未实施。
- 浏览器采集回执的失败/部分成功也会提交 Hub；0043 迁移分别保存请求开始、请求完成、内容观察和 Hub 接收时间。无内容的失败 `observed_at` 为空；Hub 独立赋 `received_at`。已有 v1 回执继续可读。
- `/health/ready` 只验证 API/DB；首页分别展示来源、发现和研究进程。进程在线不代表已形成可信报告。运行模式为在线配置也不等于每类工具已通过 canary。
- 方向价值停止裁决仍有效。`research_only` 是可阅读的受限结果，不代表可以交易；没有到期分母和校准时不显示收益或成功率。

## 3. 可重复的自测

在仓库根目录执行：

```powershell
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
uv run pytest tests/sources tests/research/test_history_list_api.py tests/scheduler/test_outcome_due.py -q
node tools/ui_v3_browser_acceptance.mjs
```

当前已验证：前端 46 项测试、生产构建、迁移/来源/Run 历史/Outcome 定向 Python 44 项测试通过。浏览器在正式入口 `http://127.0.0.1:8047/` 的桌面 1280×800 与手机 375×812 检查今日研究、市场与事件、研究、资料、管理与审计、数据更新记录、结果跟踪、来源审计共 16 个页面状态；每页标题正确、无横向溢出、无页面脚本异常或 API 错误，五个主导航和数据作业入口/返回可点击。浏览器记录及截图写入被 Git 忽略的 `data/product-local/acceptance-ui-v3/result.json` 与同目录 PNG。测试脚本断言当前可见交互，不等于每个历史按钮都已完成端到端写入验收。

正式后端迁移、重启、全部 Python/插件/来源/真实 DSH 报告链验收使用：

```powershell
.\infra\dsh\stop-product.ps1
.\infra\dsh\run-product.ps1
.\.venv\Scripts\python.exe -m tools.full_product_acceptance
```

2026-09-27 02:49（北京时间）正式验收结果为 **14/14 项通过**，详见本机 Git 忽略目录中的 [全产品机器记录](../../data/product-local/full-acceptance-latest.json)；复跑会复用同一资料与 Run 幂等键。结果如下：

| 验收点 | 结果与证据 |
| --- | --- |
| 启动与迁移 | Windows 受管启动器已在本轮重启正式实例；API、来源发现、资料、普通/优先研究 Worker、MCP 和 DSH Web 通过各自 readiness/心跳检查。正式库 `data/core-live-20260913/db/decision_hub.sqlite3` 已升至 `0043_source_acquisition_receipt_timeline`。升级前 SQLite 一致性备份为 `data/product-local/backups/decision_hub-before-0043-20260926T182122Z.sqlite3`，223,453,184 字节，`integrity_check=ok`。|
| 构建与自动测试 | Desk 与 DSH 插件生产构建通过；Python 全量 799 通过、4 跳过（2 条 SQLAlchemy 弃用提醒）；Desk 46/46、DSH 插件 93/93；采集服务类型检查、浏览器采集 smoke、隔离恢复回放、资料链和虚构 Claim 排除均通过。|
| 在线 API | OpenAPI 55 个 GET 路径中 52 个实测通过，包括来源轮询账本、Run 分页和事件 ID 读取；3 个明确跳过：当前无可用 `evaluation_id` 以及两个兜底路由。Run 列表实时总量和游标由响应头返回，不再被旧 500 条上限截断。|
| 浏览器 | 总验收时覆盖 38 个生产状态、87 项检查。之后修复“关键依据”证据深链缺少 Run ID 的问题，Desk 重新构建、46 项单测通过，[在线交互矩阵](../../data/product-local/acceptance-ui/latest.json)增至 89 项并通过真实点击证据抽屉与关闭。涵盖导航、搜索、日历、报告和资料版本深链、审计展开/收起、手机宽度；[V3 矩阵](../../data/product-local/acceptance-ui-v3/result.json)覆盖 16 个桌面/手机页面状态。均无脚本错误和横向溢出，截图在各自同目录 PNG。|
| 真实研究回读 | 关联资料的 DSH Run `run_94b800023ad2421983a64f8868674c2b` 为 `research_only`，Artifact `art_058a2ef25cd14ebba31dae3638554fd6`，13 条独立证据、96 条规范化轨迹；报告明确交易未授权。此次总验收复用已提交 Run 并核对 API、资料/证据排除和浏览器内容，**没有声称本次新触发了自动事件研究**。|
| 来源时间账本 | 0042 表在正式库有 68 次空结果和 6 次失败尝试；开始、结束、数量、错误码可逐次读取。BLS 两个来源目前仍降级，页面显示异常与最近错误时间，不把 API 在线解释成来源全部成功。|

`hub-evolution-worker` 按 owner 触发策略演进才启动，日常 Windows 产品启动器不常驻它；`/v1/operations` 中默认 `offline` 已在[启动手册](../runbooks/windows-product-live-acceptance.md)说明。Provider 已配置与 DSH 在线只证明运行条件，尚未形成未来自动事件的闭环 canary。

## 4. 尚未宣告完成的产品门槛

1. 自动来源触发一条**未来真实事件**，跨采集、准入、DSH、Evidence/Gate、报告和复核形成可回读结果；历史自动 Run 的失败不能被人工/fixture 报告替代。
2. CanonicalOccurrence → Study → Run → ReportRevision 尚需持久身份、受控匹配和跨事件/领域的独立复查。当前不能把运行列表当作完整主题报告版本。
3. 黄金要有独立 Pack、行情口径、证据门、Outcome 路由、回放及真实事件验收；生产不开放黄金研究按钮。
4. 前瞻样本、owner 有用性/查证成本、到期 Outcome 的合格分母、校准和简单基线仍不足以推翻方向价值停止线。

以上是明确的后续产品门，不以页面可点击或测试 fixture 通过冒充完成。
