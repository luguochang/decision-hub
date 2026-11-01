# Codex 任务单（2026-10-28，非代码收尾）

分工：Codex 只做排查、整理和文档，不改 `packages/`、`apps/`、`extensions/`、`infra/`、`tests/` 下任何文件，不 git commit/push，不删数据库和 artifacts。结果统一追加到 `CODEX_LOG.md` 的新一节"2026-10-28 收尾"，长输出落盘到 `artifacts/incremental-delivery-20261023/d28-closeout/`，日志里只放路径和摘录。遇错记录后跳到下一项，不要自己修代码。

## 1. 两个研究失败原因（只读）

- BTC `run_82740daa898548309bef03c4eada3c7c`：`dsh_host_read_transport_timeout`，07:58:05 UTC。
- ETH `run_e8c4952c3424404fa03a8823cfa80ba7`：`provider_timeout`，07:58:56 UTC。
- 日志目录：`data/development/default/runtime/logs/20261028T041544656Z/`（重点 `research-worker`、`dsh-web`、`hub-api`、`model-transport.channels.jsonl`、`provider-input.phases.jsonl`）。
- 要回答：超时发生在哪一步（模型调用、工具调用、host 读取）；配置的超时上限是多少、出自哪个文件哪一行；两个研究最后一次成功的模型输出时间；有没有任何一条输出里出现方向概率。结论标注"事实"或"推测"。

## 2. 开发实例为什么停了（只读）

18:40 复查时 supervisor PID 16740、hub-api 29300、Vite 30024 都不存在，18047/5173 无监听，但 `runtime/processes.json` 仍写 running（最后修改 18:15）。查各服务 stderr 末尾和 Windows 事件日志，判断是崩溃、被外部结束还是机器休眠/重启。不要重启实例，等 Claude 决定。

## 3. 合并状态文档

`docs/v3/STATUS.md` 与正式状态（`docs/design/decision-product-control-2026-10-17/00_MASTER_CONTROL.md` 中的任务表、`docs/IMPLEMENTATION_STATUS.md`）不一致。只做对照表：每项任务在各文档里的状态、矛盾点、建议保留哪个为准。写成 `docs/diagnosis-2026-10-23/STATUS_RECONCILIATION.md`，不要改原文档。

## 4. 全量基线失败清单

运行（可后台，输出落盘）：

```
PYTHONPATH=tests/decision_product .venv/Scripts/python.exe -m pytest tests/decision_product -q --continue-on-collection-errors -p no:cacheprovider
```

整理每个失败：测试名、错误类型、一行摘要、对应源文件的最后修改时间。以 `_shared/temp/claude/selftest/baseline_pytest.txt`（10-23 部分结果）为对照，标出哪些是 10-23 已存在的。只列清单，不分析根因。

## 5. PAPER 期限对齐落地核对

按 `docs/design/decision-product-control-2026-10-17/PAPER_HORIZON_ALIGNMENT_FIX.md`，grep `latest_entry_at` 和 512 代游标相关代码，逐条写"已落地/未落地/部分"，附文件:行号。结果写进第 3 项的对照文档末尾。

## 完成后

在 `CODEX_LOG.md` 列出新增/修改的文件清单（应只有本目录和 `artifacts/incremental-delivery-20261023/d28-closeout/`）。
