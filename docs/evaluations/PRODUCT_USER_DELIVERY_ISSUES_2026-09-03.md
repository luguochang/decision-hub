# 产品用户交付问题记录（2026-09-03）

本记录只收录本轮真实页面验收中发现的异常或容易误判的状态。它与
[用户交付验收记录](PRODUCT_USER_DELIVERY_ACCEPTANCE_2026-09-03.md)分开保存，避免把失败样本
改写成成功证据。

## 1. 问题总览

| 编号 | 状态 | 现象 | 根因/判断 | 处理 |
| --- | --- | --- | --- | --- |
| UI-2026-09-03-01 | 已修复 | 插件生成的 `lib/client.js` 空行带空格，`git diff --check` 失败 | `build-client.mjs` 给每一行统一加四个空格，生成空行时产生 trailing whitespace | 修改生成器只给非空行加缩进；重新构建后 diff check 通过 |
| UI-2026-09-03-02 | 已修复 | 研究报告可能在 DSH 对话区域和 composer 同时重复显示 | 业务报告曾注入两个官方 slot | 保留官方 `conversation.view`，删除 composer dock 注入；插件测试覆盖单一报告区域 |
| UI-2026-09-03-03 | 已修复 | `run_id=null` 的空 Session 被误显示为“研究报告生成中” | 报告状态机没有区分 idle 与 loading | `reportStateOf` 将无 Run 映射为 idle；空 Session 只显示未关联任务 |
| UI-2026-09-03-04 | 已修复 | 终态 Run 的 report 详情暂不可用时，页面不能显示已有失败/coverage 信息 | 页面只等待 detail，不使用终态 status 投影 | 增加 `terminal_pending` / `terminal_error` 和 `terminalReportModelOf`，保留失败来源、Gate、coverage 和审计链接 |
| LIVE-2026-09-03-01 | 已记录，非本轮代码缺陷 | 旧 Session 显示 `dsh_host_unavailable`，状态为证据不足/可重试 | 该 Session 创建于当前专用实例完成启动和 Hub/DSH callback 对齐之前；不是本轮成功 Run 的证据 | 保留失败 provenance；不重写历史。用户可点击“重新研究”创建 child Run |

## 2. LIVE-2026-09-03-01 证据

页面验收时打开的旧 Session 公开显示：

```text
研究失败 · 证据不足 (50% hard)
Research execution stopped without a committable result.
research.runtime: dsh_host_unavailable (transport/remoteprotocolerror, 可重试)
```

它关联的是旧 Run，不能与本轮交付记录中的
`run_bb4a5161d3ca4b049baf4d7386fd478d` 混用。当前专用实例的 DSH Host readiness 仍返回
`ready=true`，Hub API `/health/ready` 返回 `{"status":"ready"}`，Compose 五个服务均为
healthy/up。因此本记录把它作为历史失败样本保存，而不是宣称当前链路持续成功。

## 3. 用户侧处理

1. 新研究必须从官方 DSH Web 的“新建会话”进入，不要在历史 Session 中继续输入当作新 Run。
2. 看到“可重试”时，先查看失败来源和 Decision Desk；点击“重新研究”会创建 child Run，父 Run 保留不变。
3. 看到“仅研究/不交易”或“证据不足”时，不要把它当成方向预测；查看 Evidence、缺口和下次复查时间。
4. 看到“报告投影暂不可用”时，以状态卡和 Decision Desk 为准；不要手工补时间戳或复制旧数据。

## 4. 修复后的质量证据

- `./.venv/bin/pytest -m 'not live' -q`：397 passed。
- `pnpm --dir extensions/dsh/decision-hub test -- --run`：57 passed。
- `pnpm --dir apps/decision-desk test -- --run`：10 passed。
- `pnpm --dir apps/decision-desk build`：通过（仅有既有 chunk size warning）。
- `./.venv/bin/ruff check .`：通过。
- `./.venv/bin/pyright`：0 errors / 0 warnings / 0 informations。
- `./.venv/bin/python -m tools.contract_codegen check`：通过。
- `./.venv/bin/python tools/docs/check_module_docs.py`：13 modules 通过。
- `git diff --check`：通过。

## 5. 交付含义

本轮修复证明的是页面状态和构建质量门收口，以及失败可见、可审计、可重试；不证明实时行情
永远可用、预测准确、盈利、自动交易或生产高可用。当前产品仍是单 owner、单机、只读
`research-only` 试用版。
