# PRODUCT-CLOSEOUT-01 执行记录：2026-09-01

状态：`E1 passed / E2-R replay passed / E2-L live provider blocked / E3 not started`

本记录只保存本轮真实启动、真实页面操作和失败证据。它不把一次成功的插件测试
或固定回放解释为实时研究、预测准确率或盈利证据。认证 URL 中的 token、API key
和 cookie 均不写入本文。

## 1. 本轮目标

按 [产品实现与最终验收方案](../product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md)
验证 C1/C2 的正式产品入口：官方 DSH Web -> Decision Hub Host/Client Plugin ->
durable Research Run -> 受管 DSH Session/Trajectory -> Hub 结果与错误投影。

## 2. 运行实例

使用全新 Compose 镜像和单独端口启动，避免引用旧端口或旧进程：

| 服务 | 地址/标识 | 结果 |
|---|---|---|
| Hub API | `127.0.0.1:8140` | `/health/ready` healthy |
| Research MCP | `127.0.0.1:8142/mcp` | Streamable HTTP started |
| 官方 DSH Web | `127.0.0.1:51890` | 页面标题 `DeepSeek Harness`，token 已省略 |
| 工作区 | `data/dsh-live/Crypto Macro Trader` | 官方 `workspace/create` 注册成功 |
| live allowlist | `web.search` | 显式注入，未混入 `replay.research` |
| Runtime | `dsh-web` | Hub research worker 使用官方 Web Host seam |

启动命令（凭据来自 gitignored `data/dsh-live/.env`，命令输出不包含 secret）：

```bash
DSH_PRODUCT_KEEP_SERVICES=1 \
DSH_PRODUCT_PORT=51890 \
DECISION_HUB_API_PORT=8140 \
DECISION_HUB_RESEARCH_MCP_PORT=8142 \
./infra/dsh/run-product.sh
```

启动器输出 `Product is ready`，并且官方页面能看到 `Crypto Macro Trader` 工作区、
DSH 原生 Session/Trajectory/模型/发送控件和 Decision Hub 的 `建立研究任务` 控件。

## 3. 页面真实操作

在官方 DSH Web 的新会话中输入中文研究目标并点击 `建立研究任务`。页面返回：

```text
研究任务已排队 · run_d224133b625d4632b5e7e90c24ce94e7
```

这证明普通 DSH Chat 与正式研究入口已经分开：普通发送仍由 DSH 自己处理，研究
按钮才向 Host Bridge 创建 Hub durable Run。相同文本/session 重复提交使用相同的
`dsh-intake-<sha256>` 幂等键，不重复建立事件身份。

## 4. C1/C2 关联证据

Hub 返回的 durable Run 初始状态为 `researching`，并实际创建并关联：

```text
run_id: run_d224133b625d4632b5e7e90c24ce94e7
dsh_session_id: dsh_3a2c0e6756c42f622d6d42e3c14c320910d39743bde00e600ad2947ee4f10f2e
state: cancelled (after bounded deadline)
last_seq: 84
accepted_at: present
trace_ref: dsh://sessions/.../trace?last_seq=84
```

`dsh_session_id` 只在本记录中作为运行关联证据使用；完整 JSONL 仍由 DSH 的本机
持久化目录拥有，Hub 只保存 link/result/trace reference 和产品状态。

页面插件新增了有限状态轮询：排队后通过同源 `run_id` 状态路由读取 Hub 的
`business-status`，显示研究中、失败、Gate 和 capability provenance。它不复制 DSH
Session，也不把 Hub raw JSON 倾倒到页面。

## 5. 真实 Provider/能力结果

DSH 确实进入 MCP 并多次调用了 `research_capability_execute`，不是首轮发现 gap
后直接停止。Research MCP 日志显示：

```text
capability_id=web.search
error_code=research_capability_timeout
origin=transport
retryable=true
deadline_ms=20000
```

之后产品总 deadline 到期，Hub 记录：

```text
run.status=failed
failure.error_code=provider_timeout
failure.origin=orchestration
failure.cause_code=dsh_web_deadline_elapsed
failure.retryable=true
total_tool_calls=0 (没有可信的 capability result 可以入账)
evidence=[]
artifact_id=null
active pointer unchanged
```

这次结果是安全失败，不是 `no_trade` 伪装：没有合法 Search Evidence 就没有
Research Result、Artifact 或方向性 Forecast。

### 5.1 协议探测

使用相同的本机 Provider 配置做了短时只读探测：

| 探测 | 结果 |
|---|---|
| `GET /v1/models` | `200`，列表包含 `gpt-5.5` |
| 简单 `POST /v1/responses` | 曾返回 `200` |
| 带 `web_search` 的 `POST /v1/responses` | 在 12 秒/40 秒上限内无响应 |
| 简单 `POST /v1/chat/completions` | 本轮容器探测超时；没有据此改写生产协议 |
| `web_search_preview` | 同样未在上限内返回 |

首轮探测时不能断言中转站已支持可用的 Responses Web Search。没有擅自切换模型、
协议、网络权限或把 Search 结果编造为事实；`web.search` 保持失败可见。后续
Official/Market 模块级 canary 虽已通过，但仍需完成完整 capability 审计后才能启用。

## 6. 自动化检查

本轮代码变更后的检查结果：

```text
contract_codegen check       passed
git diff --check             passed
DSH plugin Vitest            23 passed
DSH plugin production build passed
Hub API/contract smoke       20 passed
```

本轮插件测试新增并覆盖：

- DSH typed intake 只能 `POST`，replay 模式 fail-closed；
- Host 将同源文本转换为 canonical `ObservationCreate`；
- schema 校验、稳定幂等键和 `research-run-queued.v1` 响应；
- 根据 `run_id` 轮询时，在 DSH session 尚未知晓期间仍能显示 Hub business status；
- Client 从官方 composer 提取文本，构造 typed intake，不改变 DSH 原生 Chat。

## 7. 当前阶段结论

| 门 | 状态 | 解释 |
|---|---|---|
| C1 单一官方入口/工作区 | `通过（工程）` | 页面、工作区、插件入口和 durable Run 已真实贯通 |
| C2 DSH Web Runtime/关联 | `通过（失败可审计）` | 受管 Session、Trajectory、callback、JSONL link 已建立；Provider 失败不丢状态 |
| C3 主动补证 | `部分通过` | Agent 实际发起多次 Search 调用；本次因 Provider 无响应未形成 Evidence |
| C4 真实能力准入 | `未通过` | 首轮 C1/C2 运行时 `web.search` 超时，Official/Market 当时尚未完成 canary；后续模块级只读 canary 已通过，但完整 capability acceptance、DSH 同 Session 接入和 Search canary 仍未通过 |
| C5 报告/通知/观测 | `未完成` | 错误和状态可观察，成功报告/通知需先有可用能力 |
| C6 Outcome/个人资产 | `未完成` | 本次无 Forecast，不生成 Outcome；历史资产不受影响 |
| C7 单机恢复 | `未完成本轮产品门` | 既有离线 recovery 保留，需在最终 live revision 上再跑 |
| 产品可用 | `否` | 当前是可审计的 live 失败态，不是 `pilot_usable` |

## 8. 下一步边界

1. 保留本次失败 Run、DSH JSONL、MCP 日志和错误 provenance，不修改历史。
2. 先完成 Provider capability contract/canary：确认可用的 Search transport，或在
   不扩大权限的前提下启用已经通过独立审计的 Official/Market typed adapter。
3. 在能力准入通过后，用同一官方 DSH 页面重跑 success、partial failure、
   low-authority/insufficient 三场景，再收口 C5-C7。
4. 在工程和产品门通过后停止新增基础设施，进入至少 14 天或 20 个高影响事件的
   前瞻价值观察；观察期间不切 active pointer、不宣称盈利。

## 9. 本轮执行书落地与 2026-09-01 复测

本轮新增 [产品收口总执行书](../product/PRODUCT_CLOSEOUT_MASTER_EXECUTION_2026-09-01.md)，
将 C3-C7 的唯一任务顺序、DSH/Hub/插件所有权、两层 loop、前端视图、资产沉淀、
SDD/BDD/TDD/ADR、上下文压缩、停止门和可用性定义固化为单一执行任务卡。它不改变
已接受 ADR、canonical schema 或历史 Run。

离线工程门复测：

```text
pytest -m "not live"                 338 passed
ruff check                            passed
pyright                               0 errors
contract_codegen check                passed
module docs check                     passed (13 modules)
git diff --check                      passed
DSH plugin Vitest                     26 passed
DSH plugin build                      passed
Decision Desk Vitest                  9 passed
Decision Desk build                   passed
core_acceptance                       passed (338 tests)
research_acceptance                   passed (30 tests)
docker compose config --quiet         passed
```

真实只读事实能力 canary（通过模块方式启动）：

```text
official.macro              passed · official · www.federalreserve.gov · 1 evidence · $0
market.cross_asset          passed · official · fred.stlouisfed.org · 3 evidence · $0
market.crypto_derivatives   passed · exchange · api.coinex.com · 1 evidence · $0
```

`market.cross_asset` 的最大证据年龄约 333401 秒（约 3.85 天），因此它只能作为
历史/低频宏观参考；freshness Gate 必须将其标为 stale，不能伪装成实时市场确认。

真实 `web.search` canary 结果：

```json
{
  "status": "failed",
  "error_code": "research_capability_timeout",
  "origin": "transport",
  "retryable": true,
  "capability_id": "web.search",
  "deadline_ms": 20000,
  "latency_ms": 20241,
  "source_count": 0
}
```

canary 现在始终输出 `research-search-canary.v1` 脱敏 JSON；不会把 MCP/provider
traceback 当成结果。以脚本文件路径直接执行会因仓库包导入路径而失败，规范用法是：

```bash
DECISION_HUB_SEARCH_LIVE_CANARY=1 \
  ./.venv/bin/python -m tools.canary.run_responses_web_search_canary
```

本次事实能力通过不等于产品主线通过：Official/Market 适配器已经能返回带
provenance 的 Evidence，但 DSH Provider 尚未在同一 Session 中完成 synthesis，因此
C3 主动补证、C5 成功报告/通知、C6 Outcome/Experience 和 C7 最终 live 运维门继续
保持 pending。`pilot_ready` 与 `pilot_usable` 仍为 `否`。

## 10. PRODUCT-CLOSEOUT-EXEC-02 工程/replay 收口

本节是同日后续收口证据，取代第 7、8 节对 C3/C5/C7 工程状态的旧判断，但不
改写第 2 至 6 节的 live Search 失败事实。为了避免把 replay 写成实时可用，验收门
拆为：

```text
E1   工程/恢复门             passed
E2-R 官方 DSH replay 主线门  passed
E2-L live capability 主线门  pending / Search timeout
E3   14 天或 20 事件价值门    not started
```

因此当前仍为：

```text
pilot_ready=false
pilot_usable=false
active_runtime=Fixed
candidate_runtime=DSH shadow
```

### 10.0.1 E2L-01 新发现 blocker：误准入与任务饥饿

在当前 revision 的独立 Compose 项目和全新数据库上重建镜像、启动官方 DSH Web 后，
realtime source bootstrap 建立了大量 `research.v1` Run。错误样本
`run_f7dfd75ed788400fb1b888b16ee4a800` 的标题为 Fed 对银行前员工的执法行动，并非
高影响宏观事件。确认根因如下：

- discovery 使用完整网页正文，命中 Fed 页面的固定 `Federal Reserve` 文案；
- `bootstrap_latest=true` 会从每个来源自动取最近 10 条并建立历史 research；
- research worker 按 `created_at` FIFO，用户手动任务被 backlog 压住；
- 页面显示的运行 Session 来自自动来源，不能据此证明本轮手动 intake 已执行。

发现后已停止隔离实例，保留数据库、卷和日志，没有继续消耗模型费用，也没有删除
错误 Run。修复设计、BDD/TDD 和 live 重验清单见
[E2L-01](../stages/E2L_01_EVENT_ADMISSION_COST_PRIORITY.md)。在该任务完成并重新取得
全新实例证据前，`pilot_ready=false`、`pilot_usable=false` 保持不变。

### 10.1 根因修复

官方 DSH Session 此前虽已创建且 JSONL 存在，但没有出现在 `Crypto Macro Trader`
工作区树。根因是 Host 使用 `sessionController.create({sessionId, cwd})`，没有经过
官方 `workspaceRegistry.resolveByPath()` 和 `workspaceId` 关联。修复后：

- Host 只通过公开 `workspaceRegistry` 解析已注册工作区；
- 通过 `sessionController.create({sessionId, workspaceId})` 创建或补挂受管 Session；
- 未注册工作区返回 `host_workspace_not_registered`，解析异常返回
  `host_workspace_resolution_failed`；
- 重复 submit 不重复创建 Session 或重复发送 Prompt。

第二个产品缺口是官方页面虽有 Gate 摘要，但正文主要是系统 Prompt 和模型 JSON。
修复后 Host 从 Hub canonical `ResearchRunDetailView` 读取并以生成的 Zod schema
校验，Client Plugin 在官方 `conversation.input.dock` 投影人可读报告。没有新增第二套
报告后端、聊天界面、业务账本或 Agent Loop。

### 10.2 官方 DSH 三类 replay

| 场景 | Run / Session | 结果 | 关键证据 |
|---|---|---|---|
| success | `run_f3c0ee9883bf410b9ddefa52124fcbb6` / `dsh_a26eba5dde3c3bef14ef733760d06b18a5a0b043059b7371307d27f1df93b49d` | `completed / publish / sufficient` | hard coverage 100%，9 条有效 Evidence，8 次工具调用，30m/24h/72h 结论不同 |
| partial failure | `run_0aefdc052958443bb67d3020caf451d5` / `dsh_cf592a8e467ce1b344294fb900ea0e0525fd2c681b167dcf48bd1126c4973c0f` | `rejected / reject / insufficient` | hard coverage 16.67%，保留 1 条成功 Evidence 和 2 个 capability failure；同 Session generation 2 已验证 |
| insufficient/stale | `run_257dc9203a1c4195aebe8de83362aa35` / `dsh_4236d5805297911e3ddd85a134ac5f060c7fbd54a4dbbbc400ea851e40217777` | `rejected / reject / insufficient` | 账本保留 1 条 stale Evidence，但有效 Evidence 为 0；没有方向性 Horizon |

三个页面都在官方 DSH Web 中验证了 Workspace、Session、Trajectory、Tool、报告和
失败 provenance。Success 报告展开主/反因果链及 9 条关键事实后无横向溢出；
partial/stale 报告没有用 `no_trade` 掩盖证据不足，也没有输出方向性 Horizon。

### 10.3 恢复与耐久性

以下均已通过：

- fresh migration `0001 -> 0024`、backup/restore/integrity；
- research checkpoint、lease 和 active pointer unchanged；
- DSH callback gap、官方 Web restart、同 Session generation 2 continuation；
- version incompatibility fail-closed、locked rollback；
- recovery Run `run_a3816cac814f4c67b920d00b27be9761`，Session
  `dsh_c927cc741fa16d9cb3c646586ccda585a7eb37b772d5835a64437e0e7d800b1a`。

### 10.4 最新质量门

```text
pytest -m "not live"                 343 passed
ruff check                            passed
pyright                               0 errors / 0 warnings / 0 informations
contract_codegen check                passed
module docs check                     passed (13 modules)
git diff --check                      passed
DSH Host/Client Vitest                34 passed
DSH plugin production build           passed
DSH plugin build sha256               3cc016ce0c2f9d151d5afd3ac9d749ba519537623650ca0e3e6b934f8db0a48c
Decision Desk Vitest                  9 passed
Decision Desk production build        passed
docker compose config --quiet         passed
core/research/recovery acceptance     passed
```

Decision Desk build 仍有 Vite 500 KB chunk warning，但构建成功；该 warning 不在本轮
通过隐藏错误解决，也不授权无目标的前端拆包重构。

### 10.5 浏览器资产

| 资产 | SHA-256 |
|---|---|
| `tmp/product-closeout/20260901T0120Z/dsh-success-report-collapsed.png` | `a5de20ca891b2375c876a4fae397162b1b3096652cb88b7da00064dd1f50ed7d` |
| `tmp/product-closeout/20260901T0120Z/dsh-success-report-expanded.png` | `97655e92bd721e35673085f2d0453ae1291336d0bfd31c3831d39d64f30fdf70` |
| `tmp/product-closeout/20260901T0120Z/dsh-partial-failure-report.png` | `e85553fd3f149baa07fe488c349bb90db220ad97d9941ca2f61a8425f361ff61` |
| `tmp/product-closeout/20260901T0120Z/dsh-stale-report.png` | `700e36ce8a867d60ba2ffea76529f9881c7e6522417ddf9560586bd7bce16d2b` |

当前工具没有成功暴露可控移动 viewport，也没有导出本轮浏览器 console 资产。因此
只能声明桌面页面、DOM/API 主线和无横向溢出通过；不能声明当前 revision 的移动端
真实 viewport 或 console 零错误。它们继续保留为 E2-L 验收项。

### 10.6 唯一下一步和停止线

当前不再新增工作流、角色、页面、数据库或 Agent 框架。唯一允许的下一步是解决
E2-L：让 Search 或等价的已审计、新鲜 live capability 通过 contract/canary，并从
正式 DSH Session 完成一次 Evidence -> Sufficiency -> Gate -> 人可读报告闭环。

E2-L 通过后立即进入至少 14 天或 20 个高影响事件的 E3/P4 前瞻观察，不继续堆功能。
观察结束只允许形成 `promote / retain_baseline / stop` 决策；在此之前不得宣称实时
产品可用、预测准确率、盈利或 DSH active Promotion。

## 11. 本轮文档与质量门复核（2026-09-01）

本轮新增 [产品执行与最终验收总表](../product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md)，
将既有 accepted 产品、架构、插件和治理约束汇总为一个可执行 checklist。该文档没有新增
运行时、协议、数据库或权限，也没有改写历史验收数字；它只明确当前 E2-L live 门和
E3/P4 价值停止线。

同步内容：

- `INDEX.md`、`docs/product/README.md` 已指向总表；
- `CURRENT_STATE.md`、`HANDOFF.md` 已明确 Compose 配置检查通过但最终 live 仍 pending；
- `PRODUCT_CLOSEOUT_MASTER_EXECUTION_2026-09-01.md` 已将 E2L-01 标为已收口，并把当前唯一
  继续任务指向 E2L-02/E2-L live capability；
- 历史章节中的 `343 passed`/`34 passed` 仅保留为当时验收记录，本轮最新数字单独记录如下。

本轮复核基线：

```text
pytest -m 'not live'                  361 passed
DSH plugin Vitest                    39 passed
Decision Desk Vitest                  9 passed
Ruff                                  passed
Pyright                               0 errors / 0 warnings / 0 informations
canonical codegen                     passed
module docs                           passed (13 modules)
Decision Desk build                   passed
DSH plugin build                      passed
docker compose config --quiet         passed
git diff --check                      passed
```

这些是工程/replay 证据，不是 Search 实时可用、预测准确率或盈利证据。`pilot_ready=false`、
`pilot_usable=false`、Fixed active、DSH candidate/shadow 保持不变。

## 12. 总表落地后的复跑证据（2026-09-01）

为验证新增文档和状态同步没有改变运行行为，使用临时数据库和隔离输出目录重新执行
核心、研究和 DSH Native acceptance。以下 ID、路径和 hash 均为脱敏的本机验收证据：

| 场景 | 结果 | 证据 |
|---|---|---|
| Core acceptance | `passed` | Python `361 passed`、Pyright 0 error、canonical schema/module docs 通过 |
| Research acceptance | `passed` | `41 passed`、recovery smoke `status=passed`、Compose config 通过 |
| DSH success replay | `completed / publish / sufficient` | Run `run_b2e5eaabf80345ac8b3c6a8ea8ef8f1c`；9 条 Evidence；hard coverage `1.0`；Session log hash `c5566eb806919c65b4821a87257cc9b2c453332b7cc7ce0a0622ff255d1f6e78` |
| DSH insufficient/stale replay | `rejected / reject / insufficient` | Run `run_60bbfa7dd41e430a847c5b2ba9cb95fd`；1 条 stale Evidence；hard coverage `0.0`；stop `critical_data_unavailable`；Session log hash `2016b47bb1da2c4254691117ba1ff4955e1d6426466c5545904bf1c1fa129e89` |
| DSH recovery/restart/rollback | `passed` | Run `run_a6bd455ca6f34c01ac6812c5b111a032`；callback 503 重试、Web restart、版本 fail-closed 和 locked rollback 通过；恢复后 Session hash `12f7163bef8440f1cb2f4e4060a494379f7783a55d5a8645ccb0b473d7c14e1e` |

复跑没有触发真实网络，也没有切换 active pointer。真实 Search transport timeout、live
Official/Market/Search 主线、当前 revision 的移动 viewport/console 资产和 E3/P4 价值观察
仍是 pending；产品状态继续为 `pilot_ready=false`、`pilot_usable=false`、Fixed active、
DSH candidate/shadow。

## 13. E2L-E 真实 Web 复验追加记录（2026-09-01 09:02-09:33 UTC）

本节追加记录全新的隔离实例 `dh-e2l-reverify-20260901`，不改写第 2-6 节和第 12 节
历史证据。Hub API 使用 `8190`，Research MCP 使用 `8192`，官方 DSH Web 使用 `52120`；
认证 token、cookie、API key 和临时 DSH HOME 均不写入本文。

### 13.1 页面入口与首次失败

从官方 DSH Web 的 `Crypto Macro Trader` 工作区点击“建立研究任务”，没有使用直接
创建 Run 的 curl。页面建立并轮询了真实 Run：

```text
run_f202fb32e66c449aaf302bc8ec60e402
status=failed
failure=dsh_turn_error
evidence=0
tool_calls=0
```

DSH JSONL 的精确根因是：临时 HOME 只复制了 `.env`，没有复制持久化
`settings.yaml`，因此 DSH 回退到不受支持的 `deepseek-official/deepseek-v4-flash`。
该 Run 保留为配置错误的失败证据，没有重写或删除。

### 13.2 Provider/协议边界复验

同一中转站、同一 `gpt-5.5` 的只读探针结果：

```text
POST /v1/responses                 HTTP 200（非流式）
POST /v1/chat/completions          HTTP 200（非流式）
POST /v1/chat/completions stream   HTTP 200，约 9 秒完成
POST /v1/responses stream          未产生可消费事件，触发 120 秒 idle timeout
```

因此本机 DSH 路由已按官方 DSH `pi-ai` 配置契约改为 `api: openai-completions`，并声明：

```yaml
compat:
  supportsDeveloperRole: false
  maxTokensField: max_tokens
```

这是 DSH Provider adapter 的本地配置修正，不是 Hub 业务代码、协议栈或领域契约改造。
`data/dsh-live/settings.yaml` 仍为 gitignored 本机配置，密钥继续只从 `.env` 读取。

### 13.3 正确 Provider 下的第二次和第三次页面重试

第二次页面“重新研究”创建 child Run `run_37bcd4011a4098d2ffb65ef943dc3b63`。它已
确认使用 `codexai-gpt55/gpt-5.5`，但在 150 秒模型步预算内首 token idle timeout，
0 次工具调用，最终错误为 `dsh_model_step_timeout`。这证明模型步 watchdog 和失败
投影生效，不能解释为证据不足。

第三次页面正式重试创建 child Run `run_fb3b54cf46efe2a03b13e160f190673b`，并完成了
真正的 DSH 内层补证循环：

```text
provider/model       codexai-gpt55 / gpt-5.5
status                failed (fail-closed)
rounds                1
DSH capability calls 16（其中 dsh.research 8 次，web.search 8 次）
web.search calls      8（4 succeeded, 4 timed out）
retained Evidence     20
retained Trace        38 events
final failure         research_capability_unavailable
failure capability    market.crypto_derivatives
stop                  runtime_unavailable / 6 个 hard gaps 未闭合
causal_case           null
horizons              []
artifact/forecast     null
```

页面从“研究任务已排队”进入“研究进行中 · DSH 正在补证”，最终业务报告卡显示“研究失败”，
而不是无限 loading 或伪造 `no_trade`。Query/View 同时保留成功 Evidence、timeout、
round、tool count、coverage 和失败 provenance。当前隔离实例的窄屏（390x844）与桌面
（1440x900）均无横向溢出；控制台没有 error，但因本轮两次主动重启 DSH Web 留有
`connection lost, retry` warning，不能写成零 warning。截图未作为长期产品资产保存，
因此仍不把本节声明为完整截图/hash 退出门。状态标签修复后 DSH Plugin 为
`42 passed` 且 production build 通过；它不改变本次真实 Run 的失败事实。

### 13.4 当前结论

这次复验证明：

- 官方 DSH Web -> Host/Client -> durable Run/Session -> MCP -> Hub Ledger 的真实入口和
  DSH 主动循环已经连通；
- Provider 协议兼容性根因已被隔离并修正为官方 `openai-completions` 路由；
- DSH 会在 capability 成功/失败之间继续循环，而不是第一次缺口就停止；
- 失败、Evidence、Trace、coverage 和未闭合 hard gap 均被保留，未生成方向性 Forecast。

这次复验仍不能证明：

- `web.search` 已稳定（本次 8 次中 4 次 timeout）；
- `market.crypto_derivatives` 已获 live allowlist（当前为 deny-by-default）；
- 已达到充分度、产生可发布报告、预测准确率、盈利或 DSH active Promotion。

因此 E2L-E、E2-L 和产品状态继续为：

```text
E2L-E                 blocked by live capability acceptance
E2-L                  pending
E3/P4                 not started
pilot_ready           false
pilot_usable          false
active runtime        Fixed
DSH runtime           candidate/shadow
```

该次复验结束时，下一步只允许在新的 owner/Capability Gate 下处理 Search 稳定性和明确
的只读 `market.crypto_derivatives` canary；后续 Owner 授权与执行结果见第 14 节。上述
历史限制没有扩大成第二套 Agent Loop，也没有放宽 Evidence/PIT/Gate 或改写三次真实 Run。

## 14. E2L-E 最小 capability 与原生报告视图收口

Owner 接受控制书中的最小只读准入建议后，使用当前 revision 重新执行独立 capability
canary。全部结果来自公开只读端点，成本均为 `$0`：

```text
official.macro             passed · official · www.federalreserve.gov · 1 Evidence
market.cross_asset         passed · official · fred.stlouisfed.org · 3 Evidence
market.crypto spot         passed · exchange · api.coinex.com · 1 Evidence
market.crypto derivatives  passed · exchange · api.coinex.com · 1 Evidence
```

`market.cross_asset` 最大事实年龄约 `382187s`（约 4.42 天），因此能力健康不等于证据
新鲜；正式 Run 的 requirement freshness Gate 必须把它标记为 stale。新隔离实例
`dh-e2l-final-20260901` 使用 Hub API `8210`、Research MCP `8212`、官方 DSH Web `52220`，
并在 MCP/worker 两侧加载同一最小 allowlist：

```text
web.search,official.macro,market.cross_asset,market.crypto_derivatives
```

Host readiness 通过，固定上游 commit/version 与新 Plugin build hash 匹配。Client Plugin
已通过官方 `conversation.view` 新增 `decision-hub-report` 页签，完整报告从 composer dock
移入该页；没有覆盖 `chat`/`trajectory`，也没有修改上游源码或 JSONL。上游当前把默认
视图硬编码为 Chat 且无公开第三方切换 seam，因此“受管 Session 自动默认打开报告页”
仍是明确的 upstream limitation，不使用 DOM/CSS hack 掩盖。

本 revision 的完整工程证据：

```text
Python offline                     373 passed
DSH Plugin                         43 passed + production build
Decision Desk                      10 passed + production build
Ruff / Pyright                     passed / 0 errors
canonical schema / module docs     passed / 13 modules
official DSH replay matrix         success / partial / insufficient passed
cross-process recovery             passed
callback gap / Web restart         passed
locked version rollback            passed
Compose config / diff check        passed
```

当前仍未把 E2-L 标为通过：浏览器 live 页面需要完成一次新的官方 typed intake，并保存
桌面/窄屏截图、console 和最终 Run/Gate 证据；在此之前继续保持
`pilot_ready=false`、`pilot_usable=false`、Fixed active、DSH candidate/shadow。
