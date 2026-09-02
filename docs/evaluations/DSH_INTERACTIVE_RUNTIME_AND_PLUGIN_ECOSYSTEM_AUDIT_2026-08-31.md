# DSH 交互运行态与插件生态审计

日期：2026-08-31（Asia/Shanghai）
状态：`verified / replay root cause closed / live provider multi-turn verified / search and product value pending`
范围：DSH Web 输入报错、官方 Host/Client plugin 集成、当前插件生态和后续 live 入口。
本记录不授权实时 Search/Official/Market canary、active runtime 切换或新领域开发；本轮新增的 Provider 多轮 canary 仅限本机、只读和固定短文本。

## 1. 结论先行

本次在 DSH Web 中输入后报错，根因已经复现并闭环：用户打开的是一次性 `llm-replay` 离线验收实例，不是 live DSH 交互实例。首轮固定脚本已经消费完 9 次模型调用，继续输入会请求第 10 次调用，官方 replay transport 按设计抛出 `script exhausted`。随后在隔离 live 实例中发现第二个独立配置问题：自定义 `gpt-5.5` route 错误声明 `reasoning: high`，DSH 在网络 I/O 前以 `UNSUPPORTED_REASONING_EFFORT` fail-closed；将未被网关能力声明支持的默认推理改为 `off` 后，真实请求成功。

因此必须同时承认两件事：

1. DSH 官方 Web、Session、Trajectory、JSONL、Host/Client plugin 和 Decision Hub bridge 的工程验收是通过的。
2. 原 `65349` replay 页面不能被当作可以自由对话、实时联网检索或持续后台运行的产品入口；独立 `50220` live 页面已验证真实 Provider 多轮，但实时 Search 和常驻 research worker 尚未在该页面启动。

这不是通过增加 Prompt、修改 fixture 或再造一个 Agent Loop 可以解决的问题。正确修复是把 replay 验收入口和 live interactive 入口分开，并让页面明确显示运行模式。

## 2. 复现证据

### 2.1 启动命令和进程

当前页面由以下命令启动：

```text
./.venv/bin/python tools/dsh_native_acceptance.py --scenario success --serve
```

该脚本先执行一次完整场景，再保持 Web 进程存活供浏览器查看。`--serve` 不是常驻研究 worker，也不是 live Provider 启动器。当前实例的关键进程是：

```text
tools/dsh_native_acceptance.py --scenario success --serve
dsh CLI --profile web --patch infra/dsh/presets/decision-research/replay.patch.yml
```

本轮页面地址为 `http://127.0.0.1:65349/`，只用于当前验收证据浏览，不能作为正式交互入口。

### 2.2 Replay 配置

`tools/dsh_native_acceptance.py` 强制设置以下 replay 环境和 patch：

```text
DSH_REPLAY_PLUGIN
DSH_SNAPSHOT_FILE
DSH_SNAPSHOT_OVERRIDE
infra/dsh/presets/decision-research/replay.patch.yml
```

`replay.patch.yml` 禁用真实 `llm-deepseek`，加载同版本官方测试支持包 `@deepseek-ai/dsh-llm-replay`。该包是 keyless、固定脚本的验收 transport，不是生产 Provider。

### 2.3 第 10 次调用失败

success fixture 只包含 9 次模型调用：8 次 capability/tool 调用和 1 次 synthesis。首轮完成后在同一 Session 输入 `ces`，DSH 正常追加第二轮用户消息，但 replay adapter 因脚本游标耗尽返回：

```text
llm-replay: script exhausted — session requested model call #10
but its script has only 9; re-record the scenario
```

Session JSONL 已保留对应事实：用户消息、第二轮 `turn/end`、`reason=error` 和原始错误。Session 最终为 `running=false`、两轮，说明 DSH 收到了输入，只是在固定 replay transport 的边界失败。

官方实现位置：

- `tools/dsh_native_acceptance.py:149-185, 845-870`
- `infra/dsh/presets/decision-research/replay.patch.yml`
- `.cache/dsh-upstream/source/packages/test-support/llm-replay/src/index.ts:857-870`

如果在该 replay 页面新建 Session，也不能获得自由对话：新 Session 没有对应录制脚本，会进入 `unrecorded session` 失败路径。

### 2.4 为什么直接打开裸地址会报 401

DSH Web 的根路径不是匿名静态页面。官方 Web 启动器会为本次进程生成本地认证 token，并打印一个带 `?token=...` 的 canonical URL。当前进程日志中的形式是：

```text
dsh web: http://127.0.0.1:65349/?token=<本次进程生成的本地 token>
```

直接用 `curl http://127.0.0.1:65349/` 或在没有认证 cookie 的全新浏览器中打开裸 `/`，会得到：

```text
HTTP/1.1 401 Unauthorized
dsh web authentication required; reopen the URL printed by dsh web.
```

这不是 Hub、插件或 replay 的错误，而是 DSH 官方的浏览器信任围栏。通过启动器打印的完整 URL 首次打开后，浏览器会建立本地认证状态；之后在同一浏览器中访问 `/` 或带 `session_id` 的链接通常可以继续使用。不同浏览器、隐身窗口或清除 cookie 后，必须重新使用本次进程打印的完整 URL。`session_id` 只用于定位 Session，不能替代 Web token。

当前实例的 canonical URL 可从本地日志查看，token 不写入仓库、文档或聊天记录：

```bash
rg 'dsh web:' tmp/dsh-native-core/20260831T090632Z-success-11152f20/logs/dsh-web.log
```

启动入口必须保留这个行为，不应为了“裸地址可打开”而关闭认证或把 token 固定写进配置。LIVE-00 要求启动器明确打印 canonical URL、运行模式和有效期，并在 readiness 失败时给出“重新打开启动器 URL”的可操作提示。

### 2.5 新建 Session 的自动化复现

本轮使用浏览器自动化在同一个隔离 replay Web 中点击“新建会话”，填写普通文本 `测试新会话` 并发送。页面先正常追加：

```text
上下文注入
@deepseek-ai/dsh-system-prompt
```

随后显示：

```text
本轮运行失败
llm-replay: a model call arrived from an unrecorded session (#2);
the scenario recorded only 1 session(s) — re-record it
```

这条 `@deepseek-ai/dsh-system-prompt` 记录是 DSH 官方的运行上下文注入，不是失败原因。失败发生在 replay adapter 发现新建 Session 不在唯一的录制 Session 列表之后。自动化复现同时证明：

1. 新建 Session 按钮本身可用，文本能够提交到 DSH；
2. DSH 能持久化新 Session 的 JSONL 和错误终态；
3. replay 只允许 fixture 中声明的 Session，不能把新建 Session 当作 live 会话；
4. 该临时测试只写入 `tmp/dsh-native-core/.../dsh-home`，没有写入正式 Hub 数据库。

因此 replay 模式下的正确产品行为应是：允许查看预录 Session，但在新建/继续输入前显示“只读验收”或引导用户启动 live profile。不能捕获这个错误后伪造空回复，也不能把未录制 Session 动态加入 fixture 来掩盖问题。

## 3. 当前验收与产品能力矩阵

| 能力 | 当前事实 | 能否据此宣称产品可用 |
|---|---|---|
| 官方 DSH Web 启动 | 固定上游、隔离 `DSH_HOME`、readiness 通过 | 只能证明 Web 工程可启动 |
| DSH Session/Trajectory/JSONL | success/partial_failure/insufficient_or_stale 三场景均有证据 | 只能证明回放轨迹可审计 |
| Decision Hub Host/Client plugin | 官方 `dsh.bundle`/`dsh.client` seam 已加载，Host/Client 测试和构建通过 | 可以作为集成基线 |
| Hub durable bridge | `run_id <-> dsh_session_id`、callback、恢复、取消、版本 fail-closed 已验收 | 可以作为业务控制面基线 |
| 继续自由输入 | replay 脚本耗尽后失败 | 当前页面不可用 |
| 真实 Provider 多轮 | 隔离 live profile 使用 `codexai-gpt55/gpt-5.5` 已验证两轮真实 Responses 请求和同一 Session 续接 | 只能证明 Provider/Session 基础可用，不代表研究产品可用 |
| 实时网络 Search/Market/Official | replay capability，live 默认 deny-by-default | 不可宣称 |
| 常驻后台研究 | 当前 `--serve` 只保活 Web，不消费长期队列 | 不可宣称 |
| 预测准确率、盈利 | 没有 prospective 证据 | 不可宣称 |

“工程验收通过但交互产品不可用”不是矛盾，而是运行模式没有被正确区分。本记录以后禁止把 `replay`、`candidate` 或 `engineering acceptance complete` 写成 `live`、`active` 或 `product_ready`。

## 4. 插件化的真实边界

### 4.1 已完成的官方 DSH 插件

`extensions/dsh/decision-hub/` 是官方 DSH 插件，不是自创的 Python 插件层：

```text
package.json
  dsh.bundle.patch -> cordis.patch.yml
  dsh.client.platform=web
  dsh.client.inject -> 官方 DSH client UI modules

src/host/
  readiness、submit、status、result、cancel、callback、session correlation

src/client/
  DSH 原生槽位中的 Run、Evidence、Gate、Stop reason、Report 状态投影
```

Host plugin 只调用 DSH 官方公开的 WebServer、SessionController 和事件 seam；Client plugin 只通过官方 slot/conversation surface 注入业务摘要。它没有复制 DSH Chat、Session、Trajectory、Agent Loop、Tool Loop、Subagent 或 JSONL。

### 4.2 Hub CapabilityManifest 不是第二套插件系统

DSH 官方 plugin 解决“能力如何安装、加载和显示”；Hub `CapabilityManifest` 解决“插件结果能否进入正式 Evidence/Gate”。只有会影响正式业务结果的插件才需要 manifest 审计：

```text
DSH official plugin
  -> Tool / Skill / MCP / Subagent / UI / Hook
  -> capability binding
  -> CapabilityManifest（权限、来源、PIT、鲜度、预算、审计、回滚）
  -> Evidence Gateway
  -> deterministic Sufficiency/Gate
```

插件不能直接写 Hub Ledger、Gate、Forecast、Outcome 或 active pointer。临时 DSH-only Skill 可以只由 DSH 管理；正式事实必须经过 Hub canonical contract 和 runtime validation。

## 5. 当前开源生态调查（2026-08-31）

以下是通过 GitHub API 和官方仓库文档核对出的研究候选。星标和更新时间会变化，不代表质量认证；安装前必须再次审计许可证、依赖、权限、schema、错误语义和真实测试。

| 项目 | 许可证 | 可研究用途 | 当前处理 |
|---|---|---|---|
| [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | MIT，developer preview | 官方 Agent Harness、Cordis、Web、Session、MCP、Skills、plugin seam | 当前唯一主 Harness；固定版本，不 fork 上游 |
| [awesome-dsh-plugin/awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin) | CC0 | 社区插件发现入口 | 只做目录，不等于安全/质量背书 |
| [rogerdigital/dsh-searxng](https://github.com/rogerdigital/dsh-searxng) | MIT | 自托管 SearXNG 搜索，减少 Exa/Perplexity 依赖 | 最相关候选；先做独立 Search canary，不能绕过 Evidence/PIT/Gate |
| [liustack/modlens](https://github.com/liustack/modlens) | MIT | OCR、视觉、截图、图表和图片新闻结构化 | 可作为未来视觉 capability 候选；输出不能直接成为 canonical fact |
| [MemTensor/MemOS](https://github.com/MemTensor/MemOS) | Apache-2.0 | 持久记忆、混合检索、跨任务 Skill 复用 | 研究个人资产 adapter；不取代 Hub Ledger/Evaluation |
| [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | AGPL-3.0 | Agent Memory/RAG/Skills、自进化上下文 | 技术可研究，但商业闭源产品有授权风险，暂不引入 |
| [nexu-io/open-design](https://github.com/nexu-io/open-design) | Apache-2.0 | DSH/Codex 等 Harness 的设计、PPT、图像、视频能力 | 未来 PPT Product Extension 候选，不进入金融核心 |
| [omdsh-dev/DSH-better-sidebar](https://github.com/omdsh-dev/DSH-better-sidebar) | MIT | 侧边栏、文件、终端、Git、子代理等 UI 扩展 | 只研究 UI seam，不替换官方 DSH Web |
| [1035041186/dsh-llm-ollama](https://github.com/1035041186/dsh-llm-ollama) | MIT | 本地 Ollama provider，适合 4060 Ti 离线 fallback | 后续 Provider candidate，必须接统一 ProviderConfig |
| [qixin-ai-data/dsh-qixin-insight-mcp-oauth](https://github.com/qixin-ai-data/dsh-qixin-insight-mcp-oauth) | MIT | 企业数据 OAuth 2.1/MCP | 未来企业数据研究候选，先审计权限和合规 |

生态结论：可以接入开源插件，但不能按 stars 直接安装。接入必须先经过“候选目录 -> 许可证/依赖扫描 -> manifest/权限审计 -> canonical schema 映射 -> replay/error 测试 -> 隔离 canary -> owner enable”。插件是实现来源，不是业务事实来源。

## 6. 为什么当前页面没有自动补证 loop

本次页面使用的是 `success` 固定 replay。它只验证已录制的 8 个 capability 调用和 1 次 synthesis，不能根据新输入发现新缺口并访问实时网络。当前 DSH Agent Loop 本身支持多轮，但是否能继续取决于运行时：

```text
replay runtime  -> 只能消费固定脚本，额外调用必然失败
live DSH        -> 可继续多轮，但需要真实 Provider、MCP endpoint 和权限
Hub worker      -> 负责 durable trigger/lease/recovery，不替代 DSH Agent Loop
```

所以页面“证据不足后停止”在 replay 场景是验收预期，不是智能体已经完成实时补证。要验证主动补证，必须启动无 replay patch 的 live DSH，并给它一个经过 allowlist 的 Search/Official/Market capability；这属于 NATIVE-06/G2-C 的 owner gate，不应在普通离线测试中偷偷联网。

## 7. 有界后续计划

后续不再无限叠加功能，按以下四张有限任务卡推进。每张卡结束必须得到 `promote / retain / stop`，否则停在当前状态。

### LIVE-00：运行模式和启动前检查

目标：把 `replay acceptance`、`local fake/replay worker` 和 `live interactive DSH` 做成三个明确 profile。

必须产出：

- `infra/dsh/run-live-web.sh` 或等价入口，明确要求 Provider 配置；
- readiness 显示 `runtime_mode=replay|live`、provider、capability 权限和 worker 状态；
- 启动日志明确打印带本地认证 token 的 canonical Web URL；裸 `/` 的 401 说明和重新打开方式进入 runbook；
- replay 场景完成或 script exhausted 后，页面显示“只读验收”，不让用户误以为可继续对话；
- live 入口没有 key 时在启动前失败，不能启动后让用户看到模糊的 provider error；
- runbook 和模块 README 更新，保留本次错误复现命令。

不做：不修改官方 replay adapter，不扩 fixture 伪装 live，不重写 DSH Agent Loop。

### LIVE-01：单次 live Provider/多轮 canary

Provider/多轮子项已在本轮完成；若扩大到 Search 或事件研究，仍需重新授权固定 capability、数据范围和时限。

范围（未完成部分）：单个脱敏事件、限时、只读 Search、最多一轮补证，不通知、不交易、不切 active pointer。

验收：真实 Session 可进行至少两轮；每次 tool call 有 capability、来源、时间和错误 provenance；失败时 Hub 保留已完成证据并 fail-closed。

### LIVE-02：事实覆盖与 Search reliability

把 `crypto_macro` 六类最低事实逐项验证：事件原文/身份、政策预期、利率与美元传导、跨资产确认、BTC 现货、衍生品拥挤度。每类事实必须有允许的 authority、freshness、PIT 和独立来源；缺失时只能 `research_only/no_trade`，不能用模型补写。

候选插件优先顺序是 `dsh-searxng`（搜索）和受控官方/市场数据 adapter；MemOS、OpenViking、PPT 和 ASR 不进入这张卡。

### LIVE-03：个人单机 prospective 观察

在 4060 Ti 单机上持续运行 7-14 天，记录任务延迟、来源成功率、证据覆盖、人工查证耗时、Forecast/Outcome/Brier 和失败分类。只有当 owner 认为它稳定减少人工工作，且结果不劣于 Fixed baseline，才讨论 DSH promotion；否则 `retain_fixed` 或 `stop_product`。

## 8. 当前需要 owner 做什么

对于本次 replay 报错，owner 不需要修改数据或重新录制脚本；错误已由代码和 JSONL 证据确认。

进入更大范围 LIVE-01/G2-C（实时 Search/Official/Market）仍需要单独 owner 决策/前置：

1. 明确允许使用哪个 Provider/gateway（密钥不写入仓库或文档）；
2. 明确允许哪些只读 Search/Official/Market endpoint；
3. 同意一次临时目录、限时、单事件 canary；
4. 确认是否将该 canary 结果用于产品价值判断。

本轮 owner 已明确授权一次本地 live Provider canary；因此 `Fixed active / DSH candidate-shadow` 不变，live Web 仅作为限时验证入口。Search/Official/Market 仍不自动联网、不自动补证、不自动升级。

## 9. 本轮质量门

2026-08-31 实际执行结果：

```text
git diff --check                                  passed
tools/docs/check_module_docs.py                   13 modules ok
python -m tools.contract_codegen check             passed
pytest -m "not live" -q                            327 passed
ruff check .                                      passed
pyright                                            0 errors, 0 warnings
DSH plugin Vitest                                  18 passed
DSH plugin build                                   passed
Decision Desk Vitest                               9 passed
Decision Desk build                                passed
docker compose config --quiet                      passed
```

另外，官方 DSH Web 三场景 replay、Host callback recovery、Web restart、版本 fail-closed、locked rollback 和浏览器桌面/移动视口证据均已记录在 [DSH-NATIVE-CORE Replay 与恢复验收记录](DSH_NATIVE_CORE_REPLAY_MATRIX_2026-08-31.md)。

这些检查证明工程链路和失败安全，不证明实时事实、预测准确率、盈利或 DSH active Promotion。

## 9.1 本轮 replay 误用修复与浏览器复验（2026-08-31 19:30）

本轮没有扩大 replay fixture、修改官方 DSH 源码或伪造模型结果，只修复了产品入口对运行模式的表达和防误用边界：

1. 修正 DSH `schemastery` 配置声明，使用官方支持的 `z.union(['live', 'replay'])`，避免新 Web 实例在启动阶段因不存在的 `z.literal` API 直接退出。
2. 将 replay 交互守卫挂到官方 `conversation.input.dock`。该 slot 在新建空 Session 的 hero 状态也会渲染；此前挂在 `conversation.composer.dock` 时，新建 Session 没有挂载守卫，造成页面仍可输入。
3. 运行新的隔离验收实例（端口 `64619`，运行模式 `replay`）。浏览器自动化实际观察到：
   - 页面显示“当前是 replay 离线验收，只能查看已录制轨迹；请使用 live 启动入口后再新建或继续会话”；
   - 输入框为 disabled，发送按钮为 disabled；
   - 自动化尝试输入 `不应发送` 在事件拦截下未产生 User message，也没有新的 `unrecorded session` 错误；
   - 页面无红色启动失败状态，官方 DSH Web 壳和 Decision Hub Host readiness 正常。
4. 随后使用全新浏览器标签和另一组隔离实例（端口 `50212`）复验，避免旧标签残留状态影响判断：点击“新建会话”后输入框和发送按钮仍为 disabled，填入尝试被拦截，没有 User message、`unrecorded session` 或页面错误；该标签控制台无 error/warning。

截图验收资产（临时目录，不进入产品数据）:

```text
tmp/dsh-native-core/20260831T112835Z-success-accff9af/replay-readonly-browser.png
sha256: 2dd2b8027331a294fd50bfb46903c5a3ae334bdb682199a68a43323ed114a5b5
```

本轮重新执行的质量门：

```text
git diff --check                                  passed
tools/docs/check_module_docs.py                   13 modules ok
python -m tools.contract_codegen check             passed
pytest -m "not live" -q                            327 passed
ruff check .                                      passed
pyright                                            0 errors, 0 warnings
DSH plugin Vitest                                  18 passed
DSH plugin build                                   passed
Decision Desk Vitest                               9 passed
Decision Desk build                                passed
docker compose config --quiet                      passed
```

旧 `65349` 进程仍可存在，但它使用旧插件 bundle；验证新代码必须使用新实例启动日志中打印的本次 `BROWSER_URL`，不能复用旧 tab 的 URL。

## 9.2 真实 live Provider/多轮复验（2026-08-31 20:06-20:12）

本节记录一次限时、只读的本机 live canary。Provider 凭据来自本机 gitignored 文件 `data/dsh-live/.env`，文档、日志和仓库均不记录密钥或 Web token。

### 配置与根因修复

- 真实网关 `GET /v1/models` 返回 `gpt-5.5`；真实 `POST /v1/responses` 返回 HTTP 200 和固定探针 `PROBE_OK`。
- 网关的 `/v1/chat/completions` 返回 404，因此 DSH route 使用官方 `llm-pi-ai` 的 `api: openai-responses`，不能误配 Chat Completions。
- 首次 live 页面请求在网络 I/O 前失败：`UNSUPPORTED_REASONING_EFFORT`，因为 hand-declared route 的 `reasoning: high` 不在该模型声明的能力内。将 `data/dsh-live/settings.yaml` 的默认推理改为 `off`，不伪造未探测的 reasoning capability；重启 live profile 后复验通过。

### 浏览器与 Session 证据

- 隔离 live Web：`127.0.0.1:50220`，无 replay patch，运行模式 `live`；页面工作区为持久化 `Codex`。
- 新 Session：`session-da5b144a-7ac6-49a4-8ec9-ff4ec2eb9a48`。
- 第 1 轮用户文本为 `只回复：LIVE_OK`，页面收到 assistant `LIVE_OK`，终态 completed，约 7 秒。
- 同一 Session 第 2 轮用户文本为 `第二轮只回复：LIVE_TURN_2`，页面收到 assistant `LIVE_TURN_2`，终态 completed，约 5 秒；页面显示 `2 轮 · 2 步`，没有 `unrecorded session` 或 `script exhausted`。
- DSH 持久化索引和 JSONL 均生成：
  - `data/dsh-live/storages/session_projcache/sessions/session-da5b144a-7ac6-49a4-8ec9-ff4ec2eb9a48.json`
  - `data/dsh-live/sessions/--Users-chase-Documents-Codex--/session-da5b144a-7ac6-49a4-8ec9-ff4ec2eb9a48/session.jsonl.zstd`
  - JSONL SHA-256：`c4248fbe8171fc9ca4d9917602758e157922a076830b0d5bc8d9a3d51fcbf1ff`
  - Session 索引记录 `lastUsed.provider=codexai-gpt55`、`lastUsed.model=gpt-5.5`、`turns=2`、`steps=2`。
- 新建空标签页重新打开同一 live Web，页面正常显示已完成两轮，控制台没有 error/warning；首次启动瞬间的连接重试 warning 未在新鲜标签页复现，属于页面建立连接时的短暂启动抖动，需在后续长时运行门继续观察。
- 同一真实网关另经 Decision Hub 的 `tools.canary.run_live_text_canary` 完成一次合成文本纵向链：`admitted=true`、`status=completed`、`gate_status=publish`、三个 Horizon 均生成，`run_id=run_13b0bb8a71b44fbd9db8f6d36d449c31`、结果 hash 为 `3fee1d30ff543c6007e55e448d2fb42f39b6efba6cbcaaae43b756d8406c2600`。该结果只证明 R0 Provider/Graph/结构化输出/Artifact 链路兼容，不是实时事件事实、Search 补证或收益证明。

### 这次 canary 证明与未证明

已证明：真实 gateway Responses 协议兼容、DSH 原生 Web 会话、多轮续接、模型选择投影、Session 索引和压缩 JSONL 持久化。

仍未证明：DSH `web.search` 在该网关下可用、实时新闻/行情事实覆盖、主动补证 loop、后台常驻 worker、Research Evidence/Gate 进入正式产品主链、预测准确率、盈利或 DSH active Promotion。短回复 canary 不能代替这些验收。

## 10. 不可违反的结论

- 不把 `tools/dsh_native_acceptance.py --serve` 当成 live 入口。
- 不修改 replay fixture 来掩盖脚本耗尽。
- 不在 Hub/LangGraph 中重写第二套 DSH Agent Loop。
- 不安装未经许可证、权限、schema、错误和回放审计的社区插件。
- 不让任何插件直接写 Ledger、Gate、active pointer 或交易接口。
- 不把 DSH Session JSONL 作为唯一业务账本；它与 Hub Ledger、LangGraph checkpoint 分开保存并用 ID 关联。
- 不在 Search/Official/Market canary 和 LIVE-01/G2-C 价值门通过前宣称实时研究产品可用。
