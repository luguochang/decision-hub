# D2 官方 DeepSeek Live 主流程真实验收记录

版本：`DSH-DEEPSEEK-LIVE-2026-09-03.v1`  
日期：2026-09-03（Asia/Shanghai）  
关联阶段：[D2 官方 DeepSeek Live 主流程计划与验收清单](../stages/D2_OFFICIAL_DEEPSEEK_LIVE_FLOW_PLAN.md)  
状态：`engineering flow passed / synthesis failed safely / product direction pending`

## 1. 验收结论

本次使用官方 DeepSeek Provider 和官方 DSH Web，真实跑通了从用户任务到安全终态的主链：

```text
DSH Web
  -> DSH Agent Loop
  -> decision_hub_research
  -> Research MCP / capability gateway
  -> Evidence + PIT + coverage
  -> deterministic Gate
  -> Run + Artifact + DSH report projection
```

本次不是 replay，也不是静态 fixture。DSH 在 hard gap 存在时继续执行了第 2、3 轮补证，最后
由于结构化合成没有通过 `research-synthesis-candidate.v1`，系统按 evidence-only 规则安全降级：
保留已认证 Evidence、工具调用、Trace、Coverage 和失败 provenance；丢弃未认证的 causal case、
30m/24h/72h 方向语义，不发布方向性交易结论。

因此本次结论必须拆开写：

| 验收层 | 结果 | 说明 |
|---|---|---|
| Provider/模型探针 | `passed` | 官方 `/models` 与最小 `/chat/completions` 均为 HTTP 200 |
| DSH Web 主流程 | `passed to truthful terminal` | 官方 Web 建立受管 Session，Agent Loop 主动补证 |
| Hub Run/Session 关联 | `passed` | Run、DSH Session、Generation、Trace 引用一致 |
| Evidence/PIT/Coverage/Gate | `passed` | 12 条 Evidence 持久化，66.7% hard coverage，代码 Gate reject |
| 结构化 synthesis | `failed safely` | `structured_output_invalid`，evidence-only 降级 |
| 方向性产品输出 | `not passed` | causal/horizon 为空，不得声称预测成立 |
| 产品正式可用/盈利 | `not established` | 仍只能作为单 owner、只读、research-only 试用 |

## 2. Provider 探针

凭据只从 Git ignored 的 `data/dsh-live/.env` 读取；本记录不保存 key、响应正文、请求头或
完整 Provider payload。

```text
Provider: deepseek-official
Base URL: https://api.deepseek.com
Target model: deepseek-v4-flash

GET /models
  HTTP: 200
  models: deepseek-v4-flash, deepseek-v4-pro, deepseek-v4-flash-vision-exp
  target_present: true

POST /chat/completions (minimal text probe)
  HTTP: 200
  response model: deepseek-v4-flash
  choices: 1
  valid choice: true
  fallback: none
```

探针只验证 Provider，不创建业务 Run。官方模型可用性不能替代结构化业务结果验收。

## 3. 隔离运行环境

```text
Compose project: decision-hub-d2-deepseek
Hub API:        http://127.0.0.1:8000
Research MCP:   http://127.0.0.1:8002
DSH Web:        http://127.0.0.1:52580（实际使用启动器输出的带 token URL）
Runtime mode:   live
Workspace:      Crypto Macro Trader
DSH upstream:   0.1.2-alpha.2
```

服务状态：`hub-api healthy`、`hub-realtime-worker running`、`hub-evolution-worker running`、
`hub-research-worker running`、`research-mcp running`。MCP 当前以 TCP socket 作为启动检查；
直接访问 `GET http://127.0.0.1:8002/health/ready` 返回 `404`，但 Hub 到 capability endpoint
的真实业务调用成功。是否补充标准 readiness endpoint 另立小任务，不在本次绕过记录。

## 4. 真实 Run 证据

```text
Run ID:       run_6ea4b5b7c20140e2b4c0109d36b0179a
DSH Session:  dsh_10579e526e03c2c60383879c46930d05e190b339ad2a5f727c92cdc360962595
Artifact:     art_8047b286b13246bfa0479299e996745c
Run status:   degraded
Gate:         reject
Rounds:       3
Tool calls:   12
Evidence:     12 persisted rows
Hard coverage: 66.7%
Generation:   3
DSH link:     completed
```

通过的真实 capability 包括：

- `official.macro`：Federal Reserve speeches RSS 和官方 Warsh Jackson Hole 讲话页面；
- `market.cross_asset`：FRED DGS2、DGS10、DTWEXBGS；
- `market.crypto_derivatives`：CoinEx BTC spot、永续、funding、OI、basis、mark/index。

本次运行没有把未授权的搜索能力伪装成已成功能力。任何 Search 长期稳定性或更广泛事实覆盖，
必须由单独的 live canary 和事实充分度验收证明。

DSH Session link 的关键账本事实为：`max_tool_calls=12`、`tool_calls_started=12`、
`last_seq=38035`、`generation=3`。Hub trace 共保留 98 个事件，包含每次 capability 的
`tool_started`/`tool_completed` 和 DSH tool invocation/result；完整原始 JSONL 仍由 DSH 本地
Session 持有，不复制进 Git。

## 5. 真实失败与安全行为

最终 Result 的可验证摘要为：

```text
summary: Synthesis remained untrusted (structured_output_invalid); trusted Evidence was retained,
         but no directional semantics were published.
uncertainty:
  - expectation_pricing: stale
  - macro_transmission: stale
  - cross_asset_confirmation: insufficient_sources
  - counter_thesis: insufficient_sources
  - research_sufficiency:critical_data_unavailable
```

这不是“系统第一次发现缺口就停止”。DSH 已经在同一个受管 Session 内继续了三轮，直到允许的
capability ladder、工具预算和截止时间耗尽。终态拒绝的根因分两层保存：

1. 事实层：FRED 日频数据相对事件窗口 stale，缺少事件前后利率预期重定价和完整窗口对照；
2. 合成层：DeepSeek 返回内容未通过 canonical synthesis schema，系统不能信任其因果链和
   horizon 语义。

页面因此应显示 Gate 拒绝、证据覆盖、已完成 capability、缺口和 synthesis 失败，而不是显示
“正常 no_trade”或“报告仍在生成”。

## 6. 页面与交互观察

官方 DSH Web 页面成功显示受管会话、原生对话/轨迹和研究报告状态。研究报告投影使用 Hub
canonical View，不解析或直接展示完整 Provider JSON。终态卡片显示：Gate `拒绝`、证据不足、
工具调用数、保留 Evidence、缺口、失败原因和审计入口。

本次运行没有将带 token 的 URL 或原始 JSONL 复制进仓库。D2-F 收口时使用新插件 bundle
`1a1e18d643248959384734fb0d0331c49df9d7fe60aecafa1fc45b81763ab0e4` 在同一 DSH Session
完成桌面与移动浏览器取证，未使用旧 E2-L 截图冒充本次资产。

前端状态机已有两个重要保护：

- `run_id=null` 的空 Session 显示 idle，不显示“研究报告生成中”；
- Hub 已终态但报告详情暂不可用时显示终态 fallback、coverage、stop reason 和 failure，
  不继续伪装成 loading。

## 7. D2-F 浏览器资产与质量门收口

### 7.1 浏览器证据

```text
Bundle hash: 1a1e18d643248959384734fb0d0331c49df9d7fe60aecafa1fc45b81763ab0e4
Desktop viewport: 1280x720
Desktop screenshot: tmp/d2-dsh-report-new-desktop-20260903.png
Desktop SHA-256: 452e7d90bdf4b5223df1787ef926c2e1d02dc659b3cbb9aa3fff437fc4dd3adf
Mobile viewport: 375x812
Mobile screenshot: tmp/d2-dsh-report-new-mobile-20260903.png
Mobile SHA-256: bf98c8ab24bc09fd5438ab9da24b0831c67b153608083a33e6d1f88f47b261b7
```

同一 Run 的页面断言：

```text
Decision Hub 研究报告 regions: 1
conversation.composer.dock duplicate report: 0
Gate text: 拒绝
Coverage text: 67% hard
Failure text: structured_output_invalid / evidence_stale
Evidence count: 6 条有效证据
Tool count: 12 次工具调用
Mobile document.scrollWidth: 375
Mobile document.clientWidth: 375
JavaScript console errors: 0
```

浏览器取证后停止临时 `52680` 实例期间捕获到 6 条 `[connection] connection lost, retry #N`
warning。它们是实例被主动收口后的客户端重连噪声，不是页面 error，也不改变已完成 Run 的
终态；该告警不作“无 warning”声明，保留在本记录中供复盘。

### 7.2 质量门

本轮最终命令结果以收口时的命令输出为准，先记录已完成的真实探针和账本证据；若某项命令
受本机依赖或外部 registry 阻塞，必须保留原始失败，不改写为 passed。

```text
[x] `./.venv/bin/pytest -m "not live" -q` -> `395 passed in 20.18s`
[x] `./.venv/bin/ruff check .` -> `All checks passed!`
[x] `./.venv/bin/pyright` -> `0 errors, 0 warnings, 0 informations`
[x] `./.venv/bin/python -m tools.contract_codegen check` -> `canonical schemas: ok`
[x] `./.venv/bin/python tools/docs/check_module_docs.py` -> `module docs: ok (13 modules)`
[x] `pnpm --dir extensions/dsh/decision-hub test -- --run` -> `57 passed`；build passed
[x] `pnpm --dir apps/decision-desk test -- --run` -> `10 passed`；build passed（仅有 chunk size warning）
[x] `git diff --check` -> passed
[x] 本次 D2 桌面/移动浏览器截图、DOM/viewport 和 console 结果已归档（见 §7.1）
```

“测试通过”只代表工程行为；它不能证明实时数据长期可靠、预测准确、盈利或自动交易安全。

## 8. 后续边界

D2 完成后回到 E3 前瞻价值观察，不新增第二套 Agent Loop、Provider 协议、账本、搜索协议或
领域。若要改善 synthesis，必须先新增 TDD 失败样本和明确 Stage/ADR，沿同一 DSH Web 受管
Session 修复；禁止把普通 runtime 的 repair loop 直接复制到 Web 路径。

在 E3 观察窗口和 owner review 通过前，继续保持：

```text
Fixed baseline: active
DSH runtime:    candidate/shadow
Product mode:   research_only
Automatic trade: disabled
```
