# Decision Hub UI 全流程验收记录 · 2026-09-24

## 验收范围

- 生产导航：Research、宏观日历、Research Runs、资料研究。
- 设计包 V2 的生产页面与状态：首页、提交、排队、研究中、失败、任务账本、日历范围与详情、研究报告、Evidence、图谱、资料流水线、服务状态、DSH 上下文。
- 交互触发：输入、筛选、分页范围切换、抽屉关闭与 Escape、图谱搜索/筛选/列表/全屏、资料上传/修正/审核/关联 Run、Run 的反馈/重试/取消。
- 联调接口：健康、Operations、Research Runs、Inbox、事件日历、Run 详情、观测、价值评估、Forecast Audit、Review Plans、Decision Graph、资料库及原件。

## 发现与修复

| 问题 | 处理 |
| --- | --- |
| 顶部“核心服务在线”只判断 Operations 请求是否成功，Research Worker 离线时仍显示在线 | 改为根据 `hub-api` 与 `hub-research-worker` 两个核心服务的真实心跳显示在线、降级、读取中或未知。 |
| 首页 Worker 离线状态被折叠为“未知” | 单独显示“离线”，并使用危险状态样式。 |
| 资料列表、版本、解析块、修正、观点候选接口失败时容易落入空态 | 增加每个查询的错误提示，区分加载、空数据和接口不可用。 |
| 资料修正、审核、创建 Research Run 的失败没有反馈 | 增加 mutation 错误提示，并在观点审核请求期间锁定重复点击。 |
| 资料库 API 失败默认重试，延迟显示真实错误 | 资料库查询统一关闭无意义的自动重试。 |
| 日历事件详情顶部返回会丢失原来的范围/筛选上下文 | 返回链接优先使用事件打开时保存的 `back` 路径。 |
| 直接打开 DSH 且没有 Run 时没有下一步 | 显示真实空态并提供“发起研究”入口。 |

## 真实联调记录

- 前端代理 `POST /v1/research/observations` 返回 202，重复 Idempotency-Key 返回同一 Run。
- Run 详情、Observability、Value Evaluation、Forecast Audit、Review Plans、Decision Graph、Inspector 均返回 200。
- Owner command 已验证：feedback 首次 `accepted`，同请求重放 `already_applied`；失败 Run 的 retry 创建子 Run；排队 Run 的 cancel 返回 `accepted` 并进入 cancelled。
- 资料上传返回 202；版本、原件、解析块、修正、观点候选接口均可读。
- 资源检查：前端首页、logo、hero 图片、Vite 代理和后端 `/docs`、`/health/live`、`/health/ready` 均可访问。

## 2026-09-26 复验修正

本节原先把一次裸启动 Worker 的 `dsh_sdk_unavailable` 误判成本机不能运行正式产品；另把 Windows 临时 SQLite 文件锁造成的 Replay 脚本退出码 1 误判为 fixture 不匹配。这两项结论已由后续真实验收推翻。Windows 正式入口 `infra/dsh/run-product.ps1` 使用官方 `dsh-web` runtime，已提交可读取的研究 Artifact。并发复查计划触发的 Worker 崩溃和 Replay 清理问题也已修复。当前事实、Run ID 和复验命令见 [`Windows 本机真实产品验收记录`](WINDOWS_LIVE_PRODUCT_ACCEPTANCE_2026-09-26.md)。

## 验收命令

- `pnpm web:test`：6 个文件、25 个用例通过。
- `pnpm --dir apps/decision-desk exec tsc -b --pretty false`：通过。
- `pnpm web:build`：通过；仅保留既有的大 chunk 提示。
- `.venv\\Scripts\\python.exe -m pytest tests/e2e/test_api_flow.py -q`：7 passed。
- `git diff --check`：通过。
