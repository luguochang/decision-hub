# Agentic 主动研究缺口复盘（2026-09-03）

版本：`AGENTIC-GAP-REVIEW-2026-09-03.v1`  
状态：`review complete / implementation not authorized`  
目的：记录为什么真实报告仍会出现信息不足、为什么用户感觉系统像问答助手，以及下一步应如何恢复产品价值。本文不授权新增 capability、插件、Provider 或代码实现。

## 1. 结论先行

这次问题不是 DSH 不支持 Agent Loop。DSH 官方 Web 已在真实 Run 中完成同一 Session 的多轮工具调用和补证；但本次产品配置没有把 `web.search` 放进 live allowlist，且当前可用的搜索适配器依赖 OpenAI Responses `web_search` 能力，不能由 DeepSeek 文本模型自动提供。

因此，系统实际执行的是：

```text
DSH Agent Loop
  -> 只看到 profile/MCP 已暴露的能力
  -> 调用 official.macro / market.cross_asset / market.crypto_derivatives
  -> 记录成功、stale 或失败
  -> 在有界预算内继续
  -> 关键 gap 未关闭时 fail-closed
```

而用户期待的是：

```text
发现 gap
  -> 自动发现可用搜索/抓取/数据能力
  -> 逐级尝试 fallback
  -> 用原文和精确行情验证搜索线索
  -> 直到充分、预算耗尽或明确不可得
  -> 主动生成报告并持续复查
```

后者还没有完整落地。当前报告对“证据审计和安全停止”有意义，但对“给出足够实时事实的交易研究”还不具备交付价值。不能把 `research_only/no_trade` 当成成功的市场判断。

## 2. 逐项事实核对

### 2.1 为什么没有继续搜索

当前 `infra/dsh/run-product.sh` 的默认 live allowlist 是：

```text
official.macro,market.cross_asset,market.crypto_derivatives
```

`web.search` 只有在 `DECISION_HUB_RESEARCH_CAPABILITIES` 显式包含时，`apps/research_mcp/main.py` 才会实例化 `WebSearchResearchAdapter`。DSH profile 只有一个受控的 `decision_hub_research` 工具；它不能访问未注册的能力，也没有“搜索 GitHub 并自动安装插件”的权限。

本次 D2/用户交付 Run 因此没有调用 `web.search`。它调用了已允许的官方和市场能力，并在 FRED 日频数据 stale、事件窗口不完整以及 synthesis 结构化输出失败后安全降级。真实 Run 完成了补证轮次，但没有获得足够的实时事实。

### 2.2 DSH 是否有 Loop

有两层 Loop，但边界必须说准确：

1. DSH 内层 Loop：DSH Manager/Supervisor 选择 profile 暴露的 Tool、Skill、Subagent，读取结果并继续会话。
2. Hub 外层 Loop：LangGraph `agentic_research_graph.py` 根据 deterministic coverage 决定是否开始下一轮、持久化 checkpoint、冻结 snapshot 并进入 Gate。

当前外层的继续条件是：仍有预算、仍未到最大轮次、且上一轮产生新的 accepted Evidence。若所有已允许能力都失败、stale 或被拒绝，没有新的 accepted Evidence，代码会进入 finalize；这不是无限搜索策略。

有界停止是安全要求，但当前产品没有把“能力目录和 fallback ladder”交给 DSH，因此缺口会很快变成“无可用能力”。这正是安全设计与产品价值之间尚未补齐的部分。

### 2.3 DeepSeek 是否自带 Web Search

DeepSeek 文本/聊天模型负责生成计划和结构化候选，不会自动联网。当前 `WebSearchResearchAdapter` 调用的是 OpenAI-compatible Responses `tools=[{"type":"web_search"}]`；这需要中转站或独立 Provider 真正实现该工具。

所以“使用 DeepSeek key”与“能搜索网络”是两个独立条件：

- DeepSeek Provider 探针通过，只说明文本模型可调用；
- Web Search 还需要兼容的 Responses web-search Provider，或一个自建/第三方 MCP 搜索服务；
- 没有搜索服务时，DSH 不能凭模型能力补出收益率、隐含概率、OI、funding 或清算数据。

### 2.4 交易员角色是否已实际使用

当前 `crypto_macro.manager.v1` 是 Research Request 的 `role_profile_ref`，`packs/crypto_macro` 中还有 manager、counter-thesis、data-quality 配置；Domain Pack 提供事实要求、根因链、能力绑定和 Gate 规则。

但它不是一个已安装、可在 DSH Web 中选择的“交易员插件”。当前 DSH profile 的 persona 是通用的 Decision Hub research manager，Prompt 传入的是 canonical request、evidence requirements、target gaps 和 capability playbook；代码没有把 `packs/crypto_macro/profiles/manager.yaml` 作为独立 DSH plugin 动态加载。

因此应准确描述为：交易员领域规则已经参与研究请求和 Gate，交易员角色的产品化展示和可替换 Role Plugin 还没有完成。不能把它宣传为已经在 DSH 中装载了完整交易员 Agent。

### 2.5 LoongSuite 是否已用于主流程

没有。`@loongsuite/dsh-plugin@0.1.2` 当前只在 `DSH_OBSERVABILITY_ENABLED=1` 的隔离 profile canary 中安装，验证 DSH 技术 Trace/Metric、父子 Span 和 exporter fail-open。默认产品启动器不安装它，D2 用户交付 Run 也没有把它作为业务研究能力。

LoongSuite 的职责是技术观测，不是 Web Search、金融数据、交易员角色或自进化。它不会解决事实不足；它只能回答“模型、工具和重试耗时在哪里”。当前 Decision Desk 还没有 OBS-02 的 TelemetryRef/Trace 链接视图，所以用户在业务页面看不到它是预期的，也是待补产品集成缺口。

### 2.6 调度和主动报告是否真正存在

调度骨架存在，但能力范围比产品描述窄：

- `hub-realtime-worker` 轮询已注册官方 RSS/Atom 来源；`DECISION_HUB_SOURCES_ENABLED=1` 才启用。
- BLS 日历需要 `DECISION_HUB_CALENDAR_DISCOVERY_ENABLED=1`，当前产品启动器没有默认打开。
- `CryptoMacroDiscoveryPolicy` 只对允许来源标题命中高影响词时创建 `research.v1` candidate；否则只创建 legacy `baseline.v1`。
- `hub-research-worker` 领取 `research.v1` Run，才能经 DSH Web research runtime 执行 Agent Loop。
- `hub-evolution-worker` 处理 FailurePattern、Outcome/Evaluation 和 candidate replay/holdout/shadow；它不会自动改 Prompt 或自动 Promotion。

所以系统不是完全手工，但也不是“全网实时监听、所有事件自动研究”。当前 DSH Web 中看到的用户 Run 主要是人工提交；自动来源只覆盖已注册 feed 和命中策略的事件，自动搜索和主动新闻发现尚未闭环。

## 3. 为什么这几版会持续偏离产品目标

### 根因 A：把安全失败当成产品完成

我们优先完成了 PIT、Gate、错误 provenance、回滚和 replay。这些工程门是必要条件，却被误解成“研究产品已可用”。结果是系统能诚实地说“证据不足”，却没有足够的已授权能力去解决证据不足。

### 根因 B：把 DSH Harness、Capability 和 Domain Role 混成不同层次的“插件”

- DSH Native Plugin：安装到 DSH profile 的 Tool/Skill/UI/Telemetry 单元；
- Capability Plugin：经 Gateway 返回结构化事实；
- Role Profile/Domain Pack：定义研究方法、硬事实和 Gate；
- LoongSuite：技术 Trace 插件。

它们不能互相替代。把 LoongSuite 接入不会增加搜索，把 Domain Pack 写进目录不会自动加载成 DSH persona，把 MCP 工具存在代码中也不会自动进入 live allowlist。

### 根因 C：配置允许的能力少于报告要求的事实

`crypto_macro` 要求六类事实，但当前 live 运行只给了三类 typed capability，且 FRED 的时间粒度不能满足短线窗口。报告因此必然产生 hard gap；这不是 Prompt 再长一点能解决的问题。

### 根因 D：自动调度与 DSH 研究主线没有被当成一个产品闭环验收

调度、研究 worker、DSH Web 和 Decision Desk 各自有工程测试，但没有以“事件自动发现 -> DSH 主动补证 -> 页面报告 -> 复查通知”作为唯一用户价值断言完成连续前瞻验收。

## 4. 最终处理建议

下一步不应继续增加页面或再写一个 Agent Loop，而应建立一个有界的“主动事实获取”产品门：

```text
Capability Catalog（只读）
  -> gap-to-capability fallback ladder
  -> DSH Supervisor 选择未尝试能力
  -> web.search 发现线索
  -> web.fetch/official/market 验证
  -> Gateway 写 Evidence/PIT/provenance
  -> 重新评估 gap
  -> sufficient 或 bounded failure
```

具体要求：

1. 为每个 hard requirement 声明 primary、fallback、验证方式、鲜度、authority、成本和失败码。
2. 增加只读 capability catalog 入口；DSH 只能从 catalog 选择，不能自动安装未知插件。
3. `web.search` 仅负责发现候选来源；必须通过 `web.fetch` 或 typed provider 验证后才能成为 claim-bearing Evidence。
4. 为 live profile 单独选择搜索实现：兼容的 Responses web-search Provider、经过审计的 MCP 搜索服务或本地 SearXNG。每个选择都要记录费用、授权和失败边界。
5. 把自动事件触发与 DSH research worker 串成一条真实前瞻链；日历是否开启、哪些 feed 触发研究、通知渠道和成本上限必须显式写入 Stage/ADR。
6. 将 `crypto_macro.manager` 作为真正的 Role Profile 资产加载到请求/DSH preset，并在页面显示“领域 Pack/Role/Runtime/能力目录”，但不复制 DSH UI。
7. OBS-01 保持技术 Trace；只有增加可查询外部 Trace 引用时才执行 OBS-02。不要把 LoongSuite Span 当金融 Evidence。
8. 自进化只在真实 Outcome/Evaluation 产生后生成 candidate，继续 replay/holdout/shadow 和 owner review；不要因为一次 `insufficient_sources` 自动修改生产规则。

## 5. 进入下一阶段前的三个硬问题

在未回答下列问题前，不应继续编码：

1. 搜索能力采用哪个明确 Provider/服务，预算和授权是什么？DeepSeek key 本身不回答这个问题。
2. 自动调度是否默认对高影响 Fed/BLS/BEA 事件启动 `research.v1`，以及 BLS 日历是否开启？
3. 本产品阶段目标是“实时事实补全的 research-only 试点”，还是继续只做工程/回放验收？两者的验收门不同。

## 6. 当前交付判断

当前可以交付：

- 官方 DSH Web 主入口；
- DSH Session/Trajectory/JSONL 与 Hub Run/Evidence/PIT/Gate 的可审计关联；
- 受限能力的多轮补证、失败 provenance、research-only/degraded 安全终态；
- 单机调度和进化骨架的工程/回放证据。

当前不能交付为：

- 自动发现全网事实的智能交易员；
- 在证据不足时自主找到并验证所有缺失数据；
- 实时行情/隐含概率/清算数据稳定覆盖；
- 已启用 LoongSuite 技术观测大盘；
- 已经被 Outcome/Brier 和 owner usefulness 证明的盈利或预测产品。

这次报告不是无意义：它证明了系统没有用不可信信息伪造结论，并暴露了真实产品缺口。但如果目标是给个人提供快速、可依赖的事件交易研究，它还没有达到交付线。下一步应围绕“主动事实获取 + 自动事件闭环”的一个明确 Gate 实施和验收，而不是继续堆抽象。

## 7. 依据

- [DSH 官方 DeepSeek Live 主流程真实验收](DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md)
- [产品用户交付验收](PRODUCT_USER_DELIVERY_ACCEPTANCE_2026-09-03.md)
- [DSH 可观测插件评估](DSH_OBSERVABILITY_PLUGIN_ASSESSMENT_2026-09-02.md)
- [产品收口与后续总计划](../product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)
- [DSH 与 Decision Hub 系统总装设计](../product/DSH_HUB_SYSTEM_ASSEMBLY.md)
