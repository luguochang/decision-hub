# G2-AF Tavily / DSH Search 核查与执行记录

日期：2026-09-03（Asia/Shanghai）  
阶段：`G2-AF` 主动事实获取与自主研究  
记录状态：`native DSH route probe passed / Tavily not used / Hub bridge pending`  
对应方案：[G2-AF 主动事实获取与自主研究阶段方案](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)

## 1. 本轮目的

本轮不是执行付费搜索，也不是修改 Search adapter。目的只有三个：

1. 核对 owner 提供 Tavily key 后，Tavily、官方 DSH 原生 Search、Hub Gateway 的真实边界；
2. 把“固定二十几个金融页面/端点、历史数据、来源权重、垃圾过滤、定向 Search 补充”固化成可执行设计；
3. 为以后每一轮执行保留不含 secret 的事实记录，避免上下文恢复时重新猜测架构。

## 2. Secret 与费用处理

- Tavily key：已从聊天收到，但本轮**未读取、未写入、未持久化、未发送到任何 API**。
- Key 不得进入 Markdown、源码、`.env.example`、SQLite、DSH JSONL、OTel Trace、截图、shell 输出、
  Git commit 或远程仓库。
- 由于 key 已出现在聊天内容，正式使用前建议 owner 在 Tavily 控制台轮换一次；新 key 只允许放在
  `data/dsh-live/.env`（权限 `600`）或操作系统 Secret Manager，并由 `.gitignore` 保护。
- 本轮没有产生 Tavily credit；DSH 原生 Search 探针确实执行了一次 DeepSeek Search route 请求，
  provider 返回了 usage 字段，但本轮没有把 usage 换算成费用，也没有产生其他供应商调用。不得把
  未执行的 Tavily 调用记录为成功、失败或零成本。

## 3. 已核查事实

### 3.1 官方 DSH 原生 Web Search

通过本仓库锁定的上游缓存 `.cache/dsh-upstream/source/` 和上游锁定提交核对：

- `packages/web/web-search-deepseek` 是官方 Search provider，注册 id 为 `deepseek-official`；
- `packages/bundle/base/cordis.patch.yml` 同时挂载 `dsh-web`、`dsh-web-search-deepseek` 和
  `dsh-tool-web`，官方标准/cordis/ptc preset 的 tool catalog 包含 `web_search`；
- 原生 Search 复用 `DEEPSEEK_API_KEY`，但默认 endpoint 是
  `https://api.deepseek.com/anthropic/v1/messages`，不是聊天 adapter 使用的
  `DEEPSEEK_BASE_URL`/Chat Completions endpoint；
- provider 只接受结构化 `web_search_tool_result`，不会从普通模型 prose 中猜 URL；无结构化结果时
  返回 provider error；
- 单次 Search 是一次完整的辅助 Messages model turn，存在独立的延迟和 token 成本；这不是一个
  免费、无延迟的静态搜索接口。

**结论：**“官方 DSH 有 web_search”是事实；“只要有 DeepSeek 聊天 key，本项目就已经能搜索”不是事实。
当前 `decision-research` preset 为了让每一条候选证据都经过 Hub 的 authority/PIT/hash/Gate，显式只
暴露 `decision-hub-research-tool`，没有把 `dsh-tool-web` 作为业务研究入口。因此此前页面没有继续
Search，根因是产品 profile/证据边界没有接入，不是 DSH 上游没有该能力。

### 3.2 Tavily

本轮使用公开官方文档核对（不带 key）：

- 官方 `tavily-mcp` 提供 Search/Extract/Map/Crawl 能力，适合作为 DSH MCP tool 或 Hub provider；
- 免费额度和按 credit 计费以官方控制台为准；文档当前列出免费 1,000 credits/月、basic Search 1
  credit、advanced Search 2 credits，按量价格和月包会变化；
- Tavily 返回的 Search 结果仍是 discovery 候选，不能单独满足金融 hard requirement；必须经过
  `web.fetch`、官方页面或 typed market provider 验证并记录三时间戳和内容 hash。

### 3.3 DSH 原生 Search route probe（一次性诊断）

在 owner 明确授权后，使用本机已配置的 DeepSeek 凭据完成一次最小、只读、单查询探针。
探针没有写入 DSH Session、Hub Ledger、Evidence、Trace 或长期运行数据，只保留以下脱敏摘要：

| 项目 | 结果 |
|---|---|
| endpoint | 官方 Anthropic-compatible `/anthropic/v1/messages` |
| model | `deepseek-v4-flash` |
| HTTP | `200` |
| response block | `thinking`、`server_tool_use`、`web_search_tool_result`、`text` |
| structured search result blocks | `1` |
| source count | `10`（仅计数，不保存正文） |
| source domains | `www.federalreserve.gov`、`files.stlouisfed.org`、`resources.newyorkfed.org`、`oig.federalreserve.gov`、`usgovernmentmanual.gov`、`www.investmentnews.com` |
| stop reason | `max_tokens`（探针 token 上限过低，不能作为完整研究运行） |
| usage | provider 返回 token/server-tool usage 字段；本轮不把它换算为费用 |

这证明当前 DeepSeek route 和官方 DSH 原生 Search provider 的基本协议可达，并确实返回结构化
Search block；不证明它已经能直接满足 `crypto_macro` 的 P0 金融 requirement。结果中同时出现
官方和非官方域名，仍必须经过 Source Registry、authority、独立性、freshness、PIT 和 Hub
attestation；探针也没有验证 DSH Web preset 的 tool catalog 到 Hub Ledger 的桥接。

本探针使用临时 TLS 诊断上下文，是由于本机信任库拒绝代理链自签名证书；该设置只用于本次
诊断请求，不能进入生产配置或 provider adapter。生产运行必须修复受信 CA/网络出口，而不是
长期关闭 TLS 校验。

### 3.4 本轮没有执行的动作

- 没有调用 Tavily Search/Extract/Map/Crawl；
- 没有把 `web.search` 加入 live allowlist；
- 没有修改 DSH preset、provider adapter、canonical schema 或数据库；
- 没有改变 Fixed active、DSH candidate/shadow、research-only 或 Gate 语义；
- 没有把任何页面摘要或模型记忆写入 Evidence Ledger。
- 没有调用 Tavily Search/Extract/Map/Crawl；因此没有消耗 Tavily credits。

## 4. 固定金融来源设计决定

固定来源不是“每轮搜索二十几次”，而是 `packs/crypto_macro/` 的受审计 Source Registry。首版拟
冻结 27 个 `source_ref`：Fed 官方发布/讲话/日历/FOMC 声明/纪要（5），Treasury 收益率/短票
（2），FRED DFF/DGS2/DGS10/美元代理（4），BLS CPI/日历（2），BEA PCE/日历（2），CME
FedWatch（1），OKX spot/funding/OI/liquidation（4），Deribit spot/funding/OI（3），Bybit
spot/funding/OI/liquidation（4），Reuters 宏观交叉解释（1）。完整字段、历史能力和限制见阶段方案
第 3.3 节。

Source Registry 必须记录：

- `source_ref`、canonical locator、publisher、authority、source class 和支持的 requirement；
- 具体字段映射、历史查询方式、允许最大 age 和三时间戳；
- 同 publisher/转载分组、robots/登录/付费限制、许可证、内容 hash 和固定失败码；
- P0/P1/P2 层级。P0 才能满足 hard fact；P1 用于独立解释；P2（Tavily/Brave/Exa/SearXNG）只做发现。

页面/API 不可达时必须记录失败 provenance，并尝试 registry 中已批准的 fallback；模型不得动态修改
registry 或把一般网页升级成 P0。

## 5. 本轮输出的 provider 决策

| 路径 | 角色 | 进入主链的前提 | 当前状态 |
|---|---|---|---|
| DSH 原生 `web_search` | 官方 Harness 的 discovery primary | 真实 Search route 可达，结构化来源能通过 DSH session/tool attestation 并映射为 `SearchResult.v1` | `primary candidate / route probe passed; attestation bridge not run` |
| Tavily API/MCP | Hub 可替换的外部 discovery/fetch capability | key 在 gitignored secret 中，Search -> Fetch -> Evidence attribution canary 通过 | `fallback candidate / free quota available; key not used` |
| OpenAI Responses Search | 现有 provider-neutral candidate | 中转站支持 Responses `web_search` 且在预算/超时内返回来源 | `candidate / previous timeout` |
| Brave/Exa/Serper/SearXNG | 后续 fallback 或灾备 | 首个 provider 有质量/可用性证据证明需要独立索引 | `not enabled` |

推荐不把 DSH 与 Tavily 做成两套 Agent Loop：二者都只返回候选，统一经过 Hub Gateway；当前按
owner 已接受的高性价比策略，DSH native 是 primary，Tavily 只在 native 失败/来源不足/需独立
交叉索引时调用。未来若要调整顺序，必须用隔离 canary 的覆盖、延迟、费用和引用完整度证据，并
通过 ADR，不在运行时动态切换。

## 6. 每轮执行记录模板

后续每个 canary、回放或真实 Run 都必须在本目录新增一份日期化记录，至少包含：

```text
Run/Session/Trace ref（可脱敏）
owner 目标与授权范围
provider、capability、adapter/profile version
PIT cutoff、开始/结束时间、round/tool call 数
请求 query 摘要（不得含 key/完整个人数据）
候选来源数、可归因来源数、accepted Evidence 数
P0/P1/P2 覆盖、hard gap、独立来源数、stale/conflict 数
耗时、provider reported cost/credit；未知时记录 unknown，不伪造 0
每个失败的 origin/cause/retryable/failure code
DSH JSONL/Hub Ledger/OTel trace 关联 ref（不复制正文）
前端状态、报告 Gate、通知和复查结果
测试命令及原始结果摘要
本轮结论：promote / retain / stop / blocked
下一轮唯一任务与 owner gate
```

每轮收尾必须同步 `CURRENT_STATE.md`、`CURRENT_DECISIONS.md`、`IMPLEMENTATION_STATUS.md`、
`ROADMAP.md` 和 `CHANGELOG.md`，但不把完整推理草稿粘贴进长期文档。

## 7. 后续任务与当前停止点

### 已完成（本轮）

- [x] 核对官方 DSH 原生 Search 存在、endpoint 与成本/结构化结果边界；
- [x] 解释当前 Decision Hub profile 为什么没有把原生 Search 暴露到业务主链；
- [x] 固化固定来源、历史数据、来源权重和垃圾过滤设计；
- [x] 登记 Tavily key 已收到但未使用，且未产生费用；建议正式执行前轮换聊天中暴露的旧 key。
- [x] DSH 原生 Search route 最小探针返回 HTTP 200 和结构化 `web_search_tool_result`；尚未进入 Hub Evidence 主链。

### 待 owner 确认后执行

- [ ] `G2-AF-01`：Capability Catalog + Source Registry contract Red tests；
- [ ] `G2-AF-02`：隔离 DSH 原生 Search attestation 和 Tavily Search -> Fetch -> Evidence fallback canary；
- [ ] `G2-AF-03`：同一 DSH Session 的 gap-driven continuation；
- [ ] `G2-AF-04`：calendar/feed admission -> durable Run -> 报告/通知/复查；
- [ ] `G2-AF-05`：14 天或 20 个高影响事件的 prospective observation，最终 `promote/retain/stop`。

当前停止点：DSH 原生 route probe 已通过，provider 顺序已按 DSH primary/Tavily fallback 收口；
但在 owner 对具体预算、live allowlist 和 canary 范围明确授权前，不运行 Tavily、不修改 DSH preset、
不把原生 Search 接入 Hub Ledger。
确认后的第一步仍是 `G2-AF-01`，不是直接写一套新的搜索或 Agent loop。

## 8. 本轮离线质量门

以下命令均在项目虚拟环境或对应前端 workspace 执行；除上文一次授权的 DSH 原生 Search 诊断探针
外，本轮没有执行 Tavily 或其他付费 Search 请求：

| 检查 | 结果 |
|---|---|
| `git diff --check` | 通过 |
| `python tools/docs/check_module_docs.py` | 通过，13 modules |
| `python tools/contract_codegen check` | 通过，canonical schemas ok |
| `.venv/bin/pytest -q` | 通过，397 passed |
| Pyright | 通过，0 errors / 0 warnings |
| `pnpm --dir extensions/dsh/decision-hub test -- --run` | 通过，57 passed |
| `pnpm --dir apps/decision-desk test -- --run` | 通过，10 passed |
| `pnpm --dir apps/decision-desk build` | 通过；Vite 仅报告既有 chunk size warning |

这些是文档/离线回归证据；原生 DSH Search 仅完成最小 route probe，不代表 attestation bridge、
Tavily、实时事件、历史数据覆盖或预测价值已经通过。真实 provider 主链仍需单独 canary 和 owner 授权。

## 9. 文档证据

- [G2-AF 阶段方案](../stages/G2_AF_ACTIVE_FACT_ACQUISITION_AND_AUTONOMOUS_RESEARCH.md)
- [官方 DSH 上游 Search provider README（本机锁定源码）](../../.cache/dsh-upstream/source/packages/web/web-search-deepseek/README.md)
- [官方 DSH 上游锁定提交](https://github.com/deepseek-ai/deepseek-harness/tree/0a53fb55bea101816fa226bb964ae2bed71c343b)
- [Tavily credits/pricing](https://docs.tavily.com/documentation/api-credits)
- [Tavily MCP](https://github.com/tavily-ai/tavily-mcp)
