# Decision Hub 交接执行记录（2026-09-23）

## 目标

核清真实启动方式、前置条件和外部 Key；通过 localhost 浏览器自动化触发主要页面与交互；确认 DSH Web 和 Decision Desk 的边界；修复会阻断交付的返回值/调度异常；为 UI 部提供可直接落设计的状态、接口和验收清单。

## 里程碑

- [x] 读取并核对 `infra/dsh/run-product.ps1`、`run-product.sh`、`stop-product.ps1`、Compose、API/MCP/worker/source-acquisition README。
- [x] 脱敏检查 `data/dsh-live/.env`，只记录变量名和配置状态；没有把 Secret 写入日志或文档。
- [x] 实际启动 Windows 产品实例，确认 API 8047、MCP 8037、Source Acquisition 8038、DSH 3097 和受管 worker。
- [x] 浏览器自动化检查 Decision Desk 首屏、worker 在线状态、最新建议入口、日历全部/历史 tab、资料入口、已完成报告、用户结果/技术详情、全屏分析链路和 DSH Web。
- [x] 修复 scheduled recheck 递归扩张：最新 root per event type 才拥有复查计划，过时 queued child 可审计地取消。
- [x] 修复 BEA 旧 RSS 地址（`www.bea.gov/news/rss.xml` → `apps.bea.gov/rss/rss.xml`）。
- [x] 修复直接 PDF 下载被 Playwright 当作导航失败：receipt 增加 `download` artifact，并通过 source worker smoke/typecheck。
- [x] Research runs 查询排序保证 researching/queued/已有报告不会被批量 cancelled 审计记录遮挡。
- [x] 完成全量 pytest、前端测试、前端构建、合同检查、source worker smoke/typecheck、git diff check，并把最终输出附在本文末尾。

## 自动化观察

真实 Run `run_6c34b06c2d864d699085047ad353432b` 的页面验证结果：

- 7 日价格规则方向偏多；30m/24h `no_trade`；72h 保守 `long` 候选。
- 11 条采纳 Evidence、7 个来源、1 轮、10 次工具调用、10 个专家角色包、2 次观点记录。
- 证据含 Gate/HTX/Huobi 现货与衍生品、Yahoo SPX/DXY/US10Y/VIX、FOMC 官方页面；每条显示 authority、观察时间、requirement 和链接。
- 页面明确 `probability uncalibrated` 与 `交易权限未授权`；这是研究方向而非收益或下单承诺。
- 全屏图谱显示 28 个结构化节点和 Zoom In/Out/Fit View，不展示模型私有逐字思维链。

## 异常记录

### 已修复

1. 复查树递归造成大量 queued Run：`ReviewScheduleService` 现在按最新 terminal root/event type 归属计划，并对旧队列写入 `superseded_by_newer_root`。
2. 研究 Run 列表在 compaction 后被 cancelled 记录遮挡：Query View 改为 active → 有报告终态 → failure → cancelled 的稳定排序。
3. BEA RSS 404：改用官方 `apps.bea.gov/rss/rss.xml`。
4. 直接下载 PDF：source acquisition receipt 记录 `download` artifact 并以 succeeded 结束。

### 保留并向 UI 透明展示

1. BLS RSS/ICS 官方 URL 当前网络请求返回 403；官方页面和 URL 仍存在，属于访问策略/反爬限制，不是 XML 解析失败。Source 状态保持 degraded，后续应将 BLS 采集切换到 Playwright Source Acquisition。
2. 当前 `DECISION_HUB_SEARCH_CANARY_STATUS=not_run`，未放行 `web.search`；报告中的公开信息来自 `web.fetch`、typed official/market adapter，不能在 UI 宣称“已完成全网搜索”。
3. 旧数据中已有 81 个失败 Run；`/v1/health` 因此可能是 degraded。API/worker online 与数据源健康必须分栏展示。

4. 收尾真实运行 `run_8ca1ae61ae464a7fb3a174c891df165c` 完成了覆盖检查（hard 1.0、soft 0.75，只有衍生品拥挤度软缺口），但 DSH synthesis 没有产出 causal case，按契约以 `research_only`、`synthesis_incomplete` 有界结束；证据和报告工件仍保留。这证明页面能够展示“研究完成但结论不完整”的状态，UI 不得把它伪装成完整交易结论。已有完整根因链的 `run_6c34b06c2d864d699085047ad353432b` 仍可复核。

## 最终检查命令

```powershell
uv run pytest -q
pnpm web:test
pnpm web:build
pnpm --filter @decision-hub/source-acquisition-worker typecheck
pnpm --filter @decision-hub/source-acquisition-worker smoke
uv run python -m tools.contract_codegen check
git diff --check
```

## 最终输出（2026-09-23）

- `uv run pytest -q`: **790 passed, 4 skipped**。
- `pnpm web:test`: **4 files / 18 tests passed**。
- `pnpm web:build`: Vite production build succeeded；仅有现有的单 chunk 大于 500 kB 提示。
- Source Acquisition worker `typecheck`: passed；`smoke`: receipt succeeded，包含 4 个 artifacts 和 `download_receipt_id`。Crawlee 仍会打印原始 `page.goto: Download is starting` 诊断，但失败处理器已把该下载转成成功 receipt，这是框架导航语义，不是业务失败。
- `uv run python -m tools.contract_codegen check`: `canonical schemas: ok`。
- `git diff --check`: passed（无空白错误）。
- 实例复核：8047、8038、8037、3097 均监听；API、research、priority、documents、realtime 服务在线。全局 health 仍是 `degraded`，原因是 BLS 两个官方入口返回 403 和历史失败 Run，不影响 Hub/API 进程本身；BEA、Fed、ECB、BOJ 数据源健康。

## 页面级截图交接补充

针对 UI 部的页面设计需求，新增独立文档 `docs/hand-off/DECISION_HUB_UI_PAGE_INTERACTION_SCREENSHOT_HANDOFF_2026-09-23.md`，并生成 8 张真实界面截图：Research 首页、最新建议输入区、历史日历空态、用户结果、技术详情、全屏分析链路、资料研究入口和 DSH 轨迹控制台。截图脚本为 `apps/source_acquisition_worker/scripts/capture_ui_handoff.mjs`，已实际执行成功。

截图过程中发现无缓存浏览器首次加载 2.2 MB 前端 bundle 较慢，已用本地静态预览 + 同源 API 代理生成稳定截图；这不改变产品 API 或用户入口，设计稿仍必须覆盖首屏 loading/unknown 状态。
