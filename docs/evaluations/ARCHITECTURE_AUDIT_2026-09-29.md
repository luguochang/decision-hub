# 全链路架构审查证据 · 2026-09-29

总方案：[全链路架构整改](../product/DECISION_HUB_ARCHITECTURE_REMEDIATION_PLAN_2026-09-29.md)。本记录说明实际执行了什么，不作为全部产品验收通过的声明。

## 运行与数据定位

- 仓库：`E:\project\study\codex\dsh\decision-hub`。
- 产品入口：`http://127.0.0.1:8047/`，DSH：3097；MCP：8037；采集服务：8038。
- 真实账本：`data/core-live-20260913/db/decision_hub.sqlite3`。该目录上层有数个 0 字节同名近似文件，不能用于验收。
- 检查开始入口不可达；使用既有 `tools.full_product_acceptance.ensure_product()` 启动，返回 `product startup launched and ready`。此前停止原因未确证。
- 本轮数据库取数使用只读连接和单一读事务；固定统计点 **20:41:15 +08:00**。后台恢复后仍正常产生新数据，因此不同时间截图数字可能不同。

## 自动化命令与结果

在仓库根目录运行：

```powershell
.venv/Scripts/python.exe tools/architecture_review_snapshot.py --output data/product-local/architecture-audit-2026-09-29 --network
.venv/Scripts/python.exe -m pytest tests/sources/test_official_feeds.py tests/research/test_discovery_policy.py tests/research/test_review_schedule.py tests/research/test_fact_store.py tests/scheduler/test_outcome_due.py tests/contracts/test_architecture_boundaries.py -q
```

| 检查 | 实际结果 | 解释 |
|---|---|---|
| 既有相关测试 | **39 passed in 3.51s** | 未覆盖现有生产 HTML 的语义问题，不能抵消以下失败探针 |
| 真实 Fed HTML 回放 | 200；只解析 1 条 2028-01-26 备注 | 2026 正常会议表未被读取 |
| 真实 BOJ HTML 回放 | 200；解析 16 个日期 | 混合会议、意见摘要/纪要日期；缩写和跨列语义不正确 |
| 月份与日期分离的 HTML | 期望 1，实际 0 | 纯内存复现，不入库 |
| 游标后插入/修订 | 第一次 1；第二次期望 2，实际 0 | 较早日期新安排及同日修订被游标过滤 |
| 本地只读 GET | 5 个接口均 200 | `/health/ready`、`/v1/health`、`/v1/sources`、`/v1/macro-events`、`/v1/source-acquisitions` |
| 来源健康语义 | health body 为 degraded；BLS 两源异常 | HTTP 200 只表示接口调用成功，不代表所有业务能力正常 |
| 日历 API 时间 | `2027-05-07T00:00:00` | 没有 timezone；不能作为精确发布时间 |
| Fact 引用审计 | 孤儿 Evidence 引用 0，跨 Run 错配 0 | 支持保留现有校验边界；不等于已验证所有字段的外部真实性 |
| 回执贯通 | 6 个采集回执；6,619 Evidence 中 acquisition_receipt_id 为非空的 0 | 当前显式浏览器采集血缘未贯通；不能说其他 provenance 全无 |

复现脚本仅输出报告及可选的公开官方 HTML，不修改账本；正常退出表示探针执行完成，缺陷结果在 JSON 内，不表示被测产品通过。再次运行会覆盖指定输出目录的快照；保留历史时请指定新的目录。

首次本地 HTTP 探针误用系统出站代理，返回非 JSON；已将 localhost 请求设为 `trust_env=False` 后重新取得 200 JSON。此探针配置问题不计为产品故障。SQL 字段名校正后重新采样，最终 `query_errors={}`。

## 固定样本统计与口径

机器可读摘要：[architecture-audit-2026-09-29.json](architecture-audit-2026-09-29.json)。

- Runs：1,115 = 自动 baseline 18 + 自动 research 1 + 人工 research 100 + scheduled_recheck 996。
- 自动 research 唯一记录：`run_359e3a068ddd448b85c5b3b5f1ed7814`，`provider_timeout`。不是整个系统当前运行成功率。
- 结果 528、Artifact 536、Evidence 6,619、Fact 19,566、Trace 47,529。
- 日历 10、Vintage 10；Watch 10、样本 80，均未来 pending，无到期失败结论。
- Forecast 1,608：no_trade 1,578、neutral 18、多空 12；Outcome 10，均 estimated；校准 0。
- DataJob 189：86 完成、99 失败、4 running。另查失败原因：98 个小时行情 connecterror、1 个日线 connecterror；浏览器可看到 9 月 29 日新任务完成。历史失败不能代表当前全部数据不可用。
- 机器质量评估 608；用户反馈、Memo、经验、实验、候选和晋级均为 0。
- outbox 530，均 local/sent，表示本地投递而非用户阅读。

## 浏览器检查

通过 Codex 浏览器自动化控制真实 localhost 页面，未注入模拟业务数据。

| 操作 | 结果 | 局限/发现 |
|---|---|---|
| 打开首页 | API 与研究进程 2/2；显示近期事件覆盖不足 | 日历最早事件在 2027；来源健康与覆盖需要分开 |
| 阅读结论与依据 | 打开真实 Run `run_601e9db97533491aa98d9e71cf208d67` | 报告有规则基线、综合解释及多层限制；不是本轮新执行的 Run |
| 技术详情 | 可访问图谱、证据、复核、角色、轨迹和复查 | 首屏/全文把大量技术记录堆在同一页面 |
| 分析链路全屏 | 弹出 27 节点分析图 | 图存在；节点过长，初始全图文字难读 |
| 管理→来源与服务 | 8 源状态及真实分页轮询表 | 接口、采集和内容质量不能合并成单一绿灯 |
| 管理→数据更新记录→更新失败 | 筛选生效，显示历史 connecterror 任务 | 已有作业视图，不是“完全没做数据可视化” |
| 当前浏览器 error 日志 | 检查时返回空列表 | 仅该页会话采集范围，不是全项目无前端错误证明 |

截图与 DOM：

- `data/product-local/architecture-audit-2026-09-29/report-current.png`
- `data/product-local/architecture-audit-2026-09-29/graph-current.png`
- `data/product-local/architecture-audit-2026-09-29/source-current.png`
- `data/product-local/architecture-audit-2026-09-29/data-jobs-current.png`
- `data/product-local/architecture-audit-2026-09-29/technical-dom.txt`

## 官方页面与哈希

| 页面 | UTF-8 存档 SHA-256 |
|---|---|
| [Fed FOMC](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | 以摘要 JSON 的 `parser.official_pages.fed-calendar.sha256` 为准 |
| [BOJ 日程与纪要](https://www.boj.or.jp/en/mopo/mpmsche_minu/) | 以摘要 JSON 的 `parser.official_pages.boj-calendar.sha256` 为准 |

原始存档位于同一审查输出目录。真实页面会更新；后续回归必须冻结本次文件及其哈希，另开在线探针验证新页面。禁止为了通过验收将原始 HTML 人工修成理想结构。

## 本轮交付状态

已完成：现状盘点、纯函数复现、相关回归、有限浏览器检查、数据库快照、联网依据、整改总方案与可点击架构总览。

文档交付检查：总方案 4 个 Mermaid 图；本轮新增 Markdown 链接均可解析到实际文件；Run/Forecast 分组求和与表计数一致。架构 HTML 的节点详情、数据资产页、整改计划页均已通过浏览器键盘操作切换，检查时无 error 日志；截图 `architecture-overview.png` 已人工查看。预览服务仅绑定 `127.0.0.1:8056`，服务目录为 `docs`，不是正式产品入口。

待实施：总方案 AR-01～12 的产品/后台整改。本轮不把文档交付当成这些问题已经修复；也不把之前手动真实 AI Run 成功外推为来源自动触发闭环已通过。
