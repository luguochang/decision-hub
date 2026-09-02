# E2-L 官方 DSH Web 真实产品验收记录

版本：`E2L-LIVE-ACCEPTANCE-2026-09-01.v1`
状态：`passed / research-only pilot entry`
验收日期：2026-09-01（Asia/Shanghai）
执行入口：[产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)

## 1. 验收结论

E2-L 已通过。当前 revision 已从官方 DSH Web 的 `Crypto Macro Trader` 工作区建立正式
研究任务，在同一受管 DSH Session 中自主完成两轮 Evidence Round，并把真实
Official、Market、Search 结果经 Research Gateway、PIT、Sufficiency 和代码 Gate 写入
Decision Hub 账本。官方 DSH 报告、原生 Trajectory 和 Decision Desk 对同一 Run 的
round、tool、Evidence、coverage、failure 和 Gate 一致。

本结论只允许产品进入单 owner、单机、只读的 `research_only` 观察期。它不表示 DSH
已 Promotion 为 active runtime，不表示预测准确率、盈利、Search 长期稳定性、自动交易、
多用户、ASR、PPT 或第二领域已经证明。

```text
pilot_ready          true（E2-L research-only 试用入口）
pilot_usable         research_only
automatic_trading    false
active runtime       Fixed
DSH runtime          candidate/shadow
next stage           E3 prospective observation
```

## 2. 固定架构没有改变

```text
官方 DSH Web  = 唯一用户入口和内层 Agent/Tool/Session Loop
Decision Hub  = Run/Evidence/PIT/Gate/Artifact/Forecast/Outcome 账本
LangGraph     = 外层 Evidence Round/checkpoint/lease/recovery
Research MCP  = capability 准入、事实校验和持久化边界
Domain Pack   = crypto_macro.v1 领域事实和 Gate
代码 Gate      = 唯一发布裁决者
```

没有 fork 或修改 DSH 上游，没有新增第二 Agent Loop、第二账本、第二 DTO、Provider 协议栈
或前端框架；历史 Run、Evidence、Artifact、Forecast、Outcome 和 DSH JSONL 均未改写。

## 3. 真实运行证据

运行环境：

```text
Compose project       dh-e2l-final-20260901
Hub API / Desk        http://127.0.0.1:8210
Research MCP          http://127.0.0.1:8212/mcp
Official DSH Web      http://127.0.0.1:50220
DSH upstream          0a53fb55bea101816fa226bb964ae2bed71c343b / 0.1.2-alpha.2
Plugin build hash     8dd3531365cbd96634b986babdc64fcf0236ea0e634bf444be7f795a4b4b41bf
Provider model        gpt-5.5（本机 secret 配置；未记录密钥）
Domain/Profile        crypto_macro.v1 / crypto_macro.manager.v1
```

正式 Run：

```text
run_id                run_04dc1a46fd1e4c3b988750e18b0e9581
dsh_session_id        dsh_c73364bc0fd7221f8102fc5181e8c4b17ab3736afc282379e311c67534d4c778
status                research_only / done
Hub run status        degraded
rounds                2
durable tool calls    12/12
retained Evidence     15（报告显示 9 条有效证据）
hard coverage         66.7%
Gate                  research_only
stop reason           tool_budget
Artifact              art_815a237cee2c4aa6bee513c79c64741d
estimated cost        unknown（账本为 null，不编造）
```

DSH 在工具预算内调用了 `official.macro`、`market.cross_asset`、
`market.crypto_derivatives` 和 `web.search`。两次 Search 在 Gateway 的 20 秒 deadline 内
超时，结构化 provenance 保留为：

```text
error_code   research_capability_timeout
origin       transport
capability   web.search
retryable    true
deadline_ms  20000
```

部分 Search 失败没有删除已成功的 Official/Market Evidence，也没有被包装成无信息的
`ToolTimeoutError` 或重复的 `dsh_tool_failed`。FRED 日频数据被 Gate 如实标为 stale；由于
`expectation_pricing` 和 `macro_transmission` 仍有 hard gap，代码 Gate 没有发布方向性交易
结论。

## 4. Session、工具和账本一致性

- DSH JSONL 只包含模型可见业务工具 `decision_hub_research`，共 12 次调用；原始 MCP
  capability tool 未暴露给 Agent。
- Tool 参数不包含 `research_session_id`；Session 身份只由官方
  `ToolDefinition.execute(args, exec)` 的 `exec.agent.id` 注入。
- Hub 中 15 条 Evidence 的 `research_session_id` 均等于完整 DSH Session ID。
- 运行中 Query/View 工具数从 4、7、8、10、11 增至 12，始终未超过 durable `12/12`；
  不再把 Gateway `capability_call` 与 Native `dsh_tool_call` 重复计数成 17/19/20。
- 官方 DSH 报告显示 `2` 轮、`12` 次工具调用、`67% hard`、`research_only` 和相同
  stop reason；Decision Desk 显示同一 Run 的 `Round 2/3`、`12/12 tools`、15 条 Evidence、
  `67%` 和 `tool_budget`。
- 报告只展示一次真实 `web.search` timeout 和一次 `evidence_stale`，原始包装事件仍保留
  在 Trace/JSONL 供审计，不通过 UI 删除事实。

## 5. 后台持续运行证据

父 Run 到达复查时间后，scheduler/worker 无需 owner 再次输入，自动创建 child Run：

```text
child run             run_7df306876d114e09b8e83507d06f3ef0
parent run            run_04dc1a46fd1e4c3b988750e18b0e9581
status                research_only / done
rounds                2
tool calls            12/12
Evidence              7
hard coverage         66.7%
stop reason           tool_budget
Artifact              art_5f09cf049d4b469490676195961caa49
```

这证明当前产品不只支持人工问答：durable scheduler、research worker、DSH Session、外层
Evidence Round 和账本能够在后台继续运行。该证据仍不等于长期稳定性，E3 必须继续观察。

## 6. 页面验收和截图

官方 DSH Web 与 Decision Desk 均检查了桌面和窄屏；DSH 额外检查
`375/768/1024/1440`，所有视口的 `scrollWidth == clientWidth`，没有横向溢出。正式 Run
期间两个页面的 console 均无 error。DSH Web 重建前的 connection retry warning 属于镜像
切换历史，不属于本 Run 期间错误。

截图位于 Git ignored 的临时验收目录：

```text
tmp/evaluations/e2l-live-20260901-run_04dc1a46/
```

| 资产 | SHA-256 |
|---|---|
| `dsh-report-1440x1000.png` | `168cb2d47d2a6f3490d150c0bcd86d05eb02e462c1fd3323f009198f18d93f07` |
| `dsh-report-375x812.png` | `965da57f0e9707137ef80fe07bb57643ba9b5010d55e87f293ab1c22f4cad358` |
| `decision-desk-run-1440x1000.png` | `7d8e84930320672db5dc4ed55ecc888a3dfe3a941cc1ca4ed3380d428b3b6191` |
| `decision-desk-run-375x812.png` | `5970dd9c5d46de88ef6b6d2765df577dd503e7bf1fcab5af123a61ca9266ecb8` |

## 7. 完整工程门

```text
pytest -m "not live"                 390 passed
Ruff                                  passed
Pyright                               0 errors / 0 warnings / 0 informations
canonical codegen                     passed
module docs                           passed (13 modules)
Decision Desk                         10 passed + production build
DSH Plugin                            53 passed + production build
docker compose config --quiet         passed
fresh migration 0001 -> 0027          passed
cross-process checkpoint recovery     passed
Core acceptance                       passed (390 Python tests + durability/replay)
Research acceptance                   passed (41 targeted tests + recovery)
Live observation acceptance           passed (55 targeted tests)
official DSH replay matrix            success / partial / insufficient passed
callback gap / Web restart            passed
locked-version rollback               passed
git diff --check                      passed
```

## 8. 已知限制和停止线

- Search 在本 Run 中仍有 2 次 timeout，不能宣称长期稳定；成功 Search canary 只证明
  当前 allowlist/协议可工作。
- FRED 日频数据不能满足分钟级 fresh requirement；Gate 正确保留 hard gap。需要更高频
  事实来源时必须先走 Capability/成本/许可 Gate，不能在 Prompt 中假装已有。
- 当前三个 horizon 是 `no_trade / 50% / uncalibrated` 的研究性停止结果，不是收益预测；
  E3 到期前不能填写 Outcome、Brier、方向准确率或净收益。
- 固定上游没有第三方 per-workspace default-view seam，受管 Session 首次仍进入官方 Chat；
  用户可切换“研究报告”。不通过 DOM/CSS hack 或 fork DSH 强行改变。
- 旧 `/v1/pilot/readiness` 是 R1-L Fixed 管线的 legacy readiness，不是本次 DSH-first E2-L
  判定；它在未配置 Fixed external provider/market 时仍可返回 `not_ready`。本轮不把它
  伪装成 DSH 产品 readiness，也不为收口新增第二套状态契约。

E2-L 通过后停止功能扩张。下一步只进入至少 14 天或 20 个高影响事件的 E3 前瞻观察，
记录事实覆盖、延迟、失败率、成本、人工复核时间、usefulness、Outcome/Brier/方向和净收益；
最终只允许 `promote / retain_baseline / stop`。ASR、PPT、第二领域、多用户、自动交易和
公共插件市场继续等待新的真实调用方与独立 Stage Gate。
