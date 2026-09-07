# 产品用户交付验收记录（2026-09-03）

## 1. 结论

本轮达到：`engineering delivery candidate` + `research-only pilot usable`。  
本轮未达到：`product-ready`、实时交易决策、预测准确率或盈利证明。

判断依据是一次新的隔离 live DSH Web 真实运行，而不是 replay fixture、模块 canary 或旧截图。

## 2. 运行配置

| 项目 | 值 |
| --- | --- |
| DSH Web | `127.0.0.1:52780` |
| Hub API | `127.0.0.1:8990` |
| Research MCP | `127.0.0.1:8992` |
| Compose project | `decision-hub-user-acceptance` |
| DSH 上游 | commit `0a53fb55bea101816fa226bb964ae2bed71c343b` / `0.1.2-alpha.2` |
| Provider | 官方 DeepSeek / `deepseek-v4-flash` |
| 模式 | `live` |
| 用户入口 | 官方 DSH Web |
| 管理入口 | Decision Desk |

Token、API key、原始 Provider payload 和完整会话敏感内容未写入本记录。

## 3. 根因修复

首次验收 Run `run_ec274a1ddb3f4e20890558638875c5a6` 在 DSH Provider 层返回 `AUTH` / HTTP 401。只读检查官方 DSH 凭据解析顺序后确认：进程环境优先于 `DSH_HOME/.env`；旧 `run-live-web.sh` 将宿主 `OPENAI_*` 主动映射为 `DEEPSEEK_*`，覆盖了本机 `data/dsh-live/.env` 的官方凭据。

修复内容：

- 删除 `OPENAI_API_KEY -> DEEPSEEK_API_KEY` 和 `OPENAI_BASE_URL -> DEEPSEEK_BASE_URL` 的隐式映射；
- 保留 OpenAI-compatible route 在 `settings.yaml` 中独立声明的能力；
- 新增启动器回归测试，锁定 Provider 命名空间隔离；
- 不改 Hub 账本、PIT、Gate、DSH 上游源码或历史 Run。

修复前测试先失败，修复后：`tests/dsh_native/test_product_launcher.py` 为 `4 passed`，shell syntax check 通过。

## 4. 真实 Run 证据

| 项目 | 结果 |
| --- | --- |
| Run ID | `run_bb4a5161d3ca4b049baf4d7386fd478d` |
| DSH Session | `dsh_1bb912016c18188e19fb3f70e04ba09a16ce5e749132d12a1dd75385547d65e5` |
| 终态 | `research_only` / `done` |
| 轮次 | 2 |
| 工具调用 | 12 / 12 |
| Evidence | 15 条保留；6 条当前报告接受为非 stale |
| Trace | 87 条标准化事件 |
| Hard coverage | 67% |
| Soft coverage | 0% |
| Gate | `仅研究`，三周期均为 `no_trade` |
| 停止原因 | `tool_budget` |
| 失败来源 | `evidence_stale` / `orchestration` |
| 复查 | `2026-09-03T01:56:07.849802Z` 已排入 |

主要工具调用均真实完成：`official.macro`、`market.cross_asset`、`market.crypto_derivatives`。DSH 第二轮根据剩余 gap 重新规划并继续调用，而不是第一次发现缺口就停止。

## 5. 页面验收

### DSH Web

- 新建会话成功；
- `Crypto Macro Trader` 工作区和“决策研究”入口可见；
- 研究任务成功入队并进入研究态；
- “轨迹”页展示模型步骤、2 轮、工具调用、Evidence 接受/拒绝、Coverage、Replan、Synthesis 和 Stop；
- “研究报告”页展示 Gate、67% hard coverage、Evidence 数、3 个 Horizon、缺失事实、触发/失效条件和复查时间；
- Provider 失败不会显示成成功报告；
- 同一报告未再通过 composer dock 重复注入。

### Decision Desk

- Research Command Center 能看到 durable Run、父子复查 Run 和服务状态；
- Failure provenance 展示 `evidence_stale`、来源 `orchestration`、fail-closed；
- Evidence 区域展示 source、authority、quality、observed/published 时间和原文链接；
- Research plan 展示 2 轮计划和 12 次工具上限；
- trace 区域展示 87 条业务归一化事件。

## 6. 截图资产

以下截图均为本次真实 Run 的脱敏页面截图，尺寸 `1280x720`，已排除 token 和 Provider payload：

| 页面 | 文件 | SHA-256 |
| --- | --- | --- |
| DSH 研究报告 | `docs/evaluations/assets/product-user-guide-report-20260903.jpg` | `93270ebaa0999ae245c104bb3feaabaab415970e29ef6ef338bf7f61a68235ee` |
| DSH 轨迹 | `docs/evaluations/assets/product-user-guide-trace-20260903.jpg` | `449f930bccf9fcc1ddcb12f3e36ae7c116beeea8e6fdafe28a3e7f507dd25f82` |
| Decision Desk | `docs/evaluations/assets/product-user-guide-desk-20260903.jpg` | `f381aae57baee77eef9b8e2d743728d843e5a877c4531629e44172b31717de41` |

## 7. 已知限制和后续 Gate

本 Run 的 `expectation_pricing` 与 `macro_transmission` 仍由 FRED 日频数据提供，观察日期为 2026-09-01/2026-08-28，不能满足 300-900 秒鲜度要求；Speech body 只取得官方页面导航性内容，事件窗口前后 BTC/利率/USD 对比也不完整。因此 Gate 正确阻止方向性发布。

下一阶段必须另立 owner Gate，优先增加审计过的实时利率、美元、预期定价和事件窗口来源，然后用真实前瞻事件观察验证价值。当前不切 active pointer，不自动交易，不把本轮 `research_only` 解释为交易建议。

## 8. 质量门

本轮与修复相关的检查：

- `uv run pytest -q tests/dsh_native/test_product_launcher.py`：`4 passed`；
- `uv run pytest -q tests/runtime/test_dsh_research_runtime.py -k 'web_research_prompt'`：`2 passed`；
- `bash -n infra/dsh/run-product.sh infra/dsh/run-live-web.sh infra/dsh/run-web.sh`：通过；
- `git diff --check`：通过；
- Compose 镜像重建：5/5 images built；
- Hub API `/health/ready`：`{"status":"ready"}`；
- DSH/Hub/Decision Desk 浏览器 DOM 断言和截图：通过；
- 其余全量 Python、DSH plugin、Decision Desk、Ruff、Pyright、contract codegen、module docs 质量门沿用现有基线，未因本轮功能变更而放宽。

本次最终工作树复跑（2026-09-03 10:14-10:18 CST）：

- `./.venv/bin/pytest -m 'not live' -q`：`397 passed`；
- `pnpm --dir extensions/dsh/decision-hub test -- --run`：`57 passed`；
- `pnpm --dir apps/decision-desk test -- --run`：`10 passed`；
- `pnpm --dir apps/decision-desk build`：通过（仅有既有 chunk size warning）；
- `./.venv/bin/ruff check .`、`./.venv/bin/pyright`、contract codegen、module docs 和 `git diff --check`：通过。

本次页面复核还打开了一个旧历史 Session；它显示 `dsh_host_unavailable` / 可重试，已作为失败样本
单独记录，不能与本记录中的真实成功启动和 `run_bb4a5161d3ca4b049baf4d7386fd478d` 混用。详见
[产品用户交付问题记录](PRODUCT_USER_DELIVERY_ISSUES_2026-09-03.md)。

## 9. 用户交付判断

用户现在可以启动本机产品、提交研究、查看 DSH 原生轨迹、阅读业务报告并在 Decision Desk 复盘同一 Run。交付名称应写为：

> Decision Hub 单机 research-only 研究试用版（DSH Web 主入口）。

不得写为“实时交易智能体”“自动化盈利系统”或“已完成产品价值验证”。
