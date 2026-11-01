# Windows 本机真实产品验收记录（2026-09-26）

## 结论

本机已按 `infra/dsh/run-product.ps1` 启动当前 Decision Desk、Hub API、官方 DSH Web、Research MCP 与双研究 Worker。`/v1/operations` 显示 `dsh-web`，API、普通研究 Worker、优先级 Worker 和 realtime 在线。Docker Desktop daemon 未启动，但 Windows 原生产品路径无需 Docker。

两条真实提交的 BTC/宏观 Run 均写入了可从 `/v1/runs/{run_id}/view` 取回的 Artifact：

| Run | 验收路径 | 终态 | Artifact | Evidence | Trace |
| --- | --- | --- | --- | ---: | ---: |
| `run_b111eaf7015f495a8375841a9fa41a66` | Worker 崩溃后重启恢复原 Run | `research_only` | `art_240c47fd5a13478b9585a21a6eb12fbf` | 13 | 102 |
| `run_edd8bfb7a0554ad484cd4c6b4675171f` | 修复后从新建请求完整运行 | `research_only` | `art_bc6e084c1a274a81bc29af8e25dd0ee8` | 13 | 100 |

`research_only` 是业务 Gate 提交的受限报告：关键证据覆盖通过，报告有方向观点与反方、来源、审计轨迹，但独立复核有保留、概率未校准，交易权限未授权。它不是 Worker 执行失败，也未被伪装成可以下单的结论。浏览器正式业务入口 `http://127.0.0.1:8047/` 已实际打开；首页显示核心链路 `2 / 2`、最新 Run 和结论，报告页显示 Evidence、来源矩阵、因果链与执行限制。

## 找到的问题与修复

1. 裸启动 `apps.hub_worker.main --role research --once` 默认用 Python DSH SDK，本机未安装它，因此报 `dsh_sdk_unavailable`。正式 Windows 启动器显式选 `dsh-web`，当前本机凭据与官方 Web 可以工作。之前把裸启动失败说成“本机无法验收”是不准确的。
2. 两个研究 Worker 同时扫描自动复查计划，旧的 `SELECT` 后 `INSERT` 触发 `review_schedules.trigger_hash` 唯一约束错误，普通 Worker 退出，启动器因受管服务退出而关闭整套产品。复查计划现改为 SQLite 原子冲突忽略插入，普通 Worker 独占复查调度；并发测试覆盖重复发现同一 trigger。
3. 隔离 Fake/Replay 恢复脚本本身可以提交 Artifact/Evidence/outbox，但 Windows 临时 SQLite 连接未释放导致退出码 1。脚本现在在临时目录清理前调用 `database.engine.dispose()`，复验退出码 0。
4. 真实报告的硬证据覆盖为 100% 时，`report-incomplete` 页面却提示“关键 Evidence 仍不足”。页面现在按 Run 的覆盖状态解释受限结论，正式前端重建并在浏览器复验。

## 复验命令

详细环境准备、启停与错误码定位见 [`Windows 本机完整产品启动与研究验收`](../runbooks/windows-product-live-acceptance.md)。本次通过：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\research\test_review_schedule.py -q
.\.venv\Scripts\python.exe -m pytest tests\evolution\test_research_worker.py -q
.\.venv\Scripts\python.exe -m pytest tests\dsh_native\test_product_launcher.py -q
.\.venv\Scripts\python.exe -m tools.research_recovery_smoke
pnpm --dir apps/decision-desk test -- --run
pnpm --dir apps/decision-desk build
.\.venv\Scripts\python.exe -m tools.local_product_acceptance --submit
```

结果分别为 5 passed、22 passed、8 passed / 3 skipped、`status=passed`、25 passed、build passed、真实研究验收 passed。最后检查 `git diff --check` 通过。
