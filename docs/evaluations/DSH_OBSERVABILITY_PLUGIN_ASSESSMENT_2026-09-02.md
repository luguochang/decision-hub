# DSH 可观测插件接入评估与实施建议

版本：DSH-OBSERVABILITY-ASSESSMENT-2026-09-02.v1
状态：OBS-01 已获授权并通过本机 canary；OBS-02/OBS-03 仍为后续 owner gate
评估日期：2026-09-02（Asia/Shanghai）
适用产品：Decision Hub + 官方 DSH Web + Crypto Macro Trader

## 0. 决策摘要

用户提供的文章是[《AI 编码 Agent 真正跑起任务后，问题往往不再是“它有没有完成”》](https://mp.weixin.qq.com/s/b6_-8JB6QigS_uA-txPKLQ)。文章介绍了两条开源路线：

1. [loongsuite/dsh-plugin](https://github.com/loongsuite/dsh-plugin)：独立的 DSH 原生 OpenTelemetry GenAI 插件，直接在 DSH 进程内把 Session/Turn/Step/LLM/Tool 生命周期转成 OTLP Trace 和 Metrics。
2. [alibaba/loongsuite-pilot](https://github.com/alibaba/loongsuite-pilot)：本机多 Agent 采集器，为 DSH、Codex、Pi、Claude Code 等安装 Hook/Plugin 或读取本地日志，统一输出到 JSONL、HTTP、SLS 或 OTLP，并提供本地 Dashboard。

**最终建议：接入路线 A，暂不把 Pilot 放入 Decision Hub 核心。**

具体含义：

- 在官方 DSH Web 的独立 decision-research profile 中，以精确版本 pin 的方式安装 @loongsuite/dsh-plugin@0.1.2；
- 本地先使用 Jaeger 或 OpenTelemetry Collector 作为 Trace/Metric 查看和转发后端，不要求购买第三方 SaaS；
- DSH 原生 Session/Trajectory/JSONL 继续保留，Hub 的 Run/Evidence/PIT/Gate/Artifact/Outcome 继续是产品事实源；LoongSuite 只负责技术运行 Trace/Metric，不替代 Hub 业务账本；
- 用 gen_ai.session.id / dsh.session.id 与 Hub 的 dsh_session_links 做关联，不把 Hub 的业务字段、密钥或完整 Prompt 塞进 DSH Trace；
- LoongSuite Pilot 只有在以后确实同时运行多个 Agent，并且需要统一跨 Agent 本地大盘时，才作为独立可选采集层评估。Pilot 和独立插件不能同时作为同一 DSH Trace 的采集主责。

这不是“再造一套可观测性”，而是把已有的 DSH 原生事件补充成标准 Trace/Metric；Hub 已有的业务 Trace 保留，用来做证据、失败、Gate 和长期资产审计。

## 1. 文章内容核对结果

### 1.1 独立 DSH 插件的实际能力

截至 2026-09-02，通过 GitHub API 和 v0.1.2 源码核对到：

| 项目 | 核对结果 |
|---|---|
| 仓库 | https://github.com/loongsuite/dsh-plugin |
| 最新 release | v0.1.2，2026-08-25 发布 |
| 许可证 | Apache-2.0 |
| DSH 兼容声明 | >=0.1.0-rc.6 <0.2.0；完整验证版本包含 0.1.0-rc.6，不能据此自动推断当前 pin 的 0.1.2-alpha.2 已通过 |
| Node 要求 | >=22.19.0 |
| 接入方式 | DSH profile 的 Cordis patch，安装 @loongsuite/dsh-plugin |
| 输出 | OTLP/HTTP Protobuf Trace；可选 Metrics |
| 默认正文策略 | captureContent=false；提示词、回复、工具定义/参数/结果默认不进 Span |
| Span 树 | ENTRY -> AGENT -> STEP -> LLM / TOOL |
| 细节 | 每次真实 LLM attempt 独立 Span；工具按 DSH call ID 关联；错误、中止、不完整流和插件关闭会结束为错误状态；Subagent 有父子关系属性 |
| Metrics | gen_ai.client.operation.duration、gen_ai.client.token.usage 等标准指标 |
| 后端 | Jaeger、Tempo、SigNoz、Langfuse 或任意兼容 OTLP 的后端 |
| 失败隔离 | 插件拥有私有 TracerProvider/MeterProvider，导出失败不应改变模型/工具执行路径 |

v0.1.2 的 release notes 修复了同一 Session 多轮正文串线问题，并报告 46/46 测试通过。但是 README 完整验证表没有明确写当前仓库锁定的 DSH 0.1.2-alpha.2，所以必须在本项目的上游锁定版本上单独做兼容验收，不能只看 npm 安装成功。

### 1.2 LoongSuite Pilot 的实际能力

Pilot 仓库是 [alibaba/loongsuite-pilot](https://github.com/alibaba/loongsuite-pilot)，许可证为 Apache-2.0。它的 DSH 集成不是独立 OTLP 插件的同一条路径：

~~~text
Pilot installer
  -> 修改 DSH_HOME/cordis.patch.yml 的 Pilot-owned block
  -> DSH 插件把原生事件写入 Pilot 本地 dsh-<session>.jsonl
  -> Pilot daemon 归一化
  -> 本地 JSONL / HTTP / SLS / OTLP / 本地 Dashboard
~~~

它适合统一观察一台机器上的多个 Agent，能发现 DSH、Codex、Pi、Claude Code 等，并按 Agent、Session、Turn、Tool、Model、Token 和仓库活动提供汇总。代价是：增加一个常驻采集器、另一套本地数据目录和生命周期，且 DSH 源日志仍可能包含原始消息和工具数据。captureMessageContent=false 只控制归一化输出和 Trace，不等于 Pilot 本地源日志没有正文。

## 2. 为什么它正好补上当前缺口

当前 Decision Hub 已有三种不同层次的记录，不能混为一谈：

| 记录 | 当前作用 | 能回答的问题 |
|---|---|---|
| DSH Session JSONL/Trajectory | 官方 Agent 原生 append-only 事件和会话复盘 | 发生了哪些 Turn、Step、Tool、Message、Compaction |
| Hub ResearchTraceEvent | 规范化业务研究轨迹 | 哪个 Evidence round、Capability、PIT、Gate 或错误影响了业务 Run |
| OTel Trace/Metric（目前尚未接入） | 技术执行调用树和性能投影 | 时间花在哪里、哪个 LLM attempt 重试、工具失败在哪一步、TTFT/Token/成本趋势 |

现在 Hub 页面能够显示 round、coverage、capability 失败、Gate 和人可读报告，但不能像文章中的 Trace waterfall 那样直接看到：

- 一个 DSH Turn 的 ENTRY/AGENT/STEP/LLM/TOOL 父子关系；
- 同一 Step 下多次 LLM 重试的独立耗时；
- 模型首 token（TTFT）、输入/输出/cache/reasoning token；
- Subagent 的委派关系和独立成本；
- 技术失败发生在模型、工具、网络还是 exporter。

所以文章的判断成立：JSONL 是完整事件账本，Trace 是适合性能、错误和成本分析的结构化投影。两者不是二选一，也不应由 Hub 自己重新实现一套 Span 采集器。

## 3. 推荐的系统边界

### 3.1 三层数据所有权

~~~text
DSH 原生 Session/JSONL
  = Agent 工作过程事实源

LoongSuite OTel Plugin -> Jaeger/OTel Collector/Langfuse
  = 技术观测投影（耗时、Token、Trace、Metric）

Decision Hub SQLite/Ledger
  = 产品事实源（Event、Run、Evidence、PIT、Sufficiency、Gate、Artifact、Outcome）
~~~

硬规则：LoongSuite 插件不写 Hub Ledger，不拥有 Evidence/Gate/Forecast/Outcome，不修改 active pointer，不生成交易权限；Hub 也不复制 OTel 原始 Span 到 SQLite。

### 3.2 Trace 与 Hub Run 如何关联

独立插件源码会在 Span 属性中写入：

~~~text
gen_ai.session.id = DSH session id
dsh.session.id   = DSH session id
gen_ai.turn.id   = <session id>:<turn>
dsh.step         = <step>
~~~

Hub 已有 dsh_session_links，因此最小关联路径是：

~~~text
OTel Span.dsh.session.id
  -> Hub dsh_session_links.dsh_session_id
  -> Hub run_id / event_id / domain_pack_ref
~~~

这比把 run_id 注入 Prompt 或让插件直接访问 SQLite 更安全、更可替换。第一版只在 Decision Desk 提供“打开技术 Trace”链接或按 Session ID 查询；不为了展示一个链接而新建自定义 OTLP exporter。

如果后端不能按 dsh.session.id 查询，需要后续独立的 TelemetryRef 契约和只读 Query adapter，把 trace_id/外部 URL 写入 Hub 的引用表。这个 adapter 只存引用、backend、trace_id、observed_at 和版本，不存 Span 正文；需要新 ADR 和 owner gate，不能在本阶段偷偷扩展账本。

### 3.3 页面呈现

用户仍然从官方 DSH Web 进入：

~~~text
DSH Web
  -> 原生 Chat / Session / Trajectory / JSONL
  -> Decision Hub 研究报告页签
  -> “技术轨迹”按钮（打开 Jaeger/Tempo/Langfuse 对应 Trace）
~~~

Decision Desk 只显示少量人可读指标：

- Run 总耗时、LLM/Tool 分段耗时；
- LLM attempt、TTFT、输入/输出 token、估算成本；
- 工具成功率、错误率、重试次数；
- Subagent 数量和耗时；
- 外部 Trace backend 与 Trace ID。

详细瀑布图、Span 属性和原生 JSONL 仍由各自工具查看，不把数百行 Span JSON 倾倒在业务页面。正文默认关闭；需要调试正文时只在隔离本机短任务显式开启，且不能进入共享或远程后端。

## 4. 为什么首选独立插件，不先上 Pilot

### 4.1 独立插件的优点

- 已经实现 DSH 原生 Session/Turn/Step/LLM/Tool 生命周期监听；
- 已经处理 LLM retry、工具错误、TTFT、Token 和 Subagent 关系；
- 使用标准 OpenTelemetry，后端可替换，不绑定 LoongSuite SaaS；
- 本地 Jaeger/Collector 可以免费运行，不要求新增云服务；
- 不需要读取或复制 Hub 数据库，也不引入第二套业务账本；
- 代码量和运行组件都小，适合当前单机、单 owner、DSH-first 产品。

### 4.2 Pilot 的优点和后置条件

Pilot 的价值是统一多个 Agent：如果以后同时运行 DSH、Codex、Pi、Claude Code、PPT Agent 等，它能把不同本地事件归一化到同一套 Agent 视图，并提供本地 Dashboard。它不是当前 Crypto Macro Trader 的必要依赖。

Pilot 只有在满足以下条件后才进入评估：

~~~text
[ ] 至少两个真实 Agent 同时运行
[ ] owner 确认需要跨 Agent 的统一 Token/Session/仓库大盘
[ ] 明确原始本地日志的留存、加密、权限和清理策略
[ ] 选择 Pilot 或独立插件作为 DSH Trace 的唯一采集主责
[ ] Pilot 采集结果不会进入 Hub Evidence/Gate，除非通过 typed adapter 审计
~~~

当前不应同时安装独立插件和 Pilot 并将两份 DSH Trace 发往同一个后端，否则会形成重复链路；除非专门做对照实验，否则必须设置唯一 owner。

## 5. 费用、第三方组件和部署影响

### 5.1 不需要新增付费服务的本地方案

文章附带的 quickstart 已验证可以使用本地 Jaeger：

~~~text
DSH + @loongsuite/dsh-plugin@0.1.2
  -> localhost:4318 OTLP/HTTP
  -> Jaeger v2
  -> localhost:16686 查看 Trace
~~~

这条路径没有 SaaS 费用，但 Jaeger quickstart 的 Trace 存在内存中，容器停止后丢失，不能作为长期个人资产。长期保留可以选择：

- OpenTelemetry Collector + Jaeger/Tempo 持久化存储；
- 自建 Langfuse；
- Langfuse Cloud、阿里云云监控等托管服务（按其报价、流量和留存收费）。

当前没有要求采购任何服务。先用本地 Jaeger 完成兼容和页面验收，再根据 E3 的 Trace 量和留存需求决定是否增加持久化后端。

### 5.2 最小资源估算

独立插件本身只在 DSH Node 进程内维护 Span/Metric 队列；本地 Jaeger quickstart 是单容器，适合当前 4060 Ti/32 GB 主机。2 核 4 GB 云服务器不建议同时承载 DSH、Hub、数据库和长期 Trace 存储，除非只做转发或短期查看。真实资源应在 OBS canary 中按 Span 数、保留天数和导出批量实测，而不是照搬营销配置。

## 6. 安全和隐私边界

默认配置必须保持：

~~~yaml
captureContent: false
exportMetrics: true
~~~

必须注意：

1. 启用正文采集后，Prompt、模型回复、工具定义、参数、结果可能包含源码、凭据或个人数据；
2. OTel captureContent=false 不会改变 DSH 原生 JSONL 的本地保存；
3. Pilot 的 captureMessageContent=false 也不等于 Pilot 的本地 DSH source log 无正文；
4. Trace exporter 失败必须 fail-open，不得影响 Agent Loop、Hub Run 或 Gate；
5. OTLP Header/API key 只能从 gitignored 环境变量或本机 secret store 读取，禁止进入 profile 示例、日志和 Hub 账本；
6. Trace backend 的保留期、访问控制和备份必须单独记录，不把外部服务当作默认个人资产库。

## 7. 不重复造轮子的接入方案

### OBS-01：独立插件兼容性和本机 Trace canary（已执行）

目标：证明 @loongsuite/dsh-plugin@0.1.2 在本仓库锁定的 DSH 0.1.2-alpha.2 上能工作，且不改变现有产品主线。

实现边界：

1. 在 infra/dsh/observability/ 增加独立 profile/patch/compose overlay，不修改 DSH 上游源码，不把插件依赖写进 Kernel；
2. 锁定 npm 包版本、integrity、DSH upstream identity 和 Node/pnpm 版本；
3. 本地 Jaeger 或 OTel Collector 作为可替换 endpoint；
4. captureContent=false，仅导出结构、耗时、Token、TTFT 和错误状态；
5. 以 dsh.session.id 与 Hub dsh_session_links 进行只读关联；
6. Decision Desk 只增加技术 Trace 外链/摘要，不复制 Span 树到 Hub；
7. 为 exporter 断开、队列满、DSH 重启、HMR/adopt、重复 turn、模型 retry、tool failure、Subagent 和 shutdown flush 建立回归测试。

BDD 退出门：

~~~text
Given 固定 DSH Web profile + loongsuite plugin + local Jaeger
When 同一 Session 执行两轮、包含至少一次 Tool 和一次失败/重试
Then Jaeger 存在每轮一条 trace，且 ENTRY/AGENT/STEP/LLM/TOOL 父子关系正确
And session/turn/step 与 Hub dsh_session_links 可关联
And exporter 不可用时 DSH 与 Hub 仍正常完成，错误只出现在 telemetry diagnostics
And captureContent=false 时后端不存在 Prompt、回复、工具参数和结果正文
And Hub Gate、Evidence、Artifact、Outcome 与未接入前一致
~~~

TDD 重点：

- 插件 exact version 与 DSH exact version 的 bundle/handshake；
- 单轮和多轮 Span 数量、父子关系、Session/Turn/Step 属性；
- LLM retry 独立 Span、TTFT、input/output/cache/reasoning token；
- Tool call/result/error 状态；
- Subagent parent/delegation 属性；
- exporter timeout/drop 不改变 DSH 业务路径；
- captureContent 隔离和本地 secret 不外泄；
- Hub Run 与技术 Trace 的只读关联；
- replay/live 两种 profile 不混用，历史 JSONL 不重复导出。

### OBS-02：技术观测 Query/View

只有 OBS-01 通过后，才增加一个很薄的 TelemetryRef/Query adapter（如果实际后端需要）。它只提供 backend、trace_id、trace_url、session_id、observed_at、schema/version 和 health，不拥有 Span 内容，不进入 Evidence/Gate，也不把 OTel 事件复制到业务表。

### OBS-03：Pilot 多 Agent 评估（条件任务）

只有第二个真实 Agent 产生稳定需求后才评估。先做独立 Pilot profile 和本地 Dashboard，对照 DSH 独立插件的 Trace 数量、重复率、隐私和资源消耗；通过 owner review 后选择唯一 DSH Trace owner。OBS-03 不得阻塞 Crypto Macro Trader 的 E3。

## 8. 与现有模块的落点

~~~text
infra/dsh/observability/       OBS profile、lock、Jaeger/Collector overlay
extensions/dsh/decision-hub/   不复制 OTel 实现；只接官方 plugin seam 和外链/摘要
packages/runtime_adapters/    不新增第二套 Trace collector；只保留 session identity 关联
packages/kernel/               不存 OTel Span；如需引用，增加独立 TelemetryRef contract
packages/query_views/          只读展示技术观测摘要/外链
apps/decision-desk/            技术观测入口和少量摘要指标
docs/decisions/                OBS-01/02/03 若改变边界时新增 ADR
~~~

禁止落点：

- 不在 ResearchObservabilityService 中重写 OTel span coordinator；
- 不在 LangGraph node 中创建 ENTRY/AGENT/STEP/LLM/TOOL span；
- 不把 OTel raw payload、Prompt、工具参数或完整 JSONL 写入 Hub SQLite；
- 不为一个外链复制新的业务 Trace/Run/Session 表；
- 不把 Pilot 安装器改写成 Decision Hub 的插件市场或权限系统。

## 9. 当前状态、决策门和下一步

### 当前已确认

- 文章描述的独立 DSH OTel 插件真实存在，当前 release 为 v0.1.2；
- LoongSuite Pilot 是独立的多 Agent 本机采集器，不是独立 DSH 插件的必需依赖；
- 当前仓库已有 DSH JSONL、Hub ResearchTraceEvent、Query/View 和 Decision Desk，但没有 OTel Trace/Metric exporter；
- 独立插件正好补技术可观测性缺口，且不需要替换 Hub 业务账本；
- 当前 DSH 0.1.2-alpha.2 与插件 README 的“已完整验证版本”不完全相同，必须 exact-version canary；
- 本地 Jaeger 可作为零 SaaS 费用的第一验证后端，但不是长期持久资产库。

### OBS-01 执行证据（2026-09-02）

- 通过 `infra/dsh/observability/run-canary.sh --scenario=partial_failure` 在锁定的 DSH
  `0.1.2-alpha.2` 上按官方 profile seam 安装 `@loongsuite/dsh-plugin@0.1.2`；profile
  manifest 为 exact `0.1.2`，pnpm lock integrity 与 `plugin.lock.json` 一致。
- 同一业务回放 Run `run_7740a9271cde4275a59f25c25c168a29` 保持
  `rejected / insufficient / critical_data_unavailable`，并保留 1 条 Evidence、2 次 Tool
  failure；插件没有改变 Hub Gate 或业务结果。
- OTLP metadata receiver 捕获 2 个请求（`/v1/traces`、`/v1/metrics`）。解码 Trace payload
  得到 2 条 turn trace、17 个 Span，包含 `enter_ai_application_system`、
  `invoke_agent decision-research`、`react step`、`chat deepseek-v4-flash` 和
  `execute_tool decision_hub_research`；父 Span 均可解析，且存在 `dsh.session.id` 属性。
- payload 没有正文采集标记；插件配置保持 `captureContent=false`。测试接收器仅记录路径、
  字节数和结构摘要，原始 OTLP 不写入仓库。
- `--exporter-down --scenario=partial_failure` 将 OTLP endpoint 指向无监听端口；Run
  `run_ba919ad4cf4f43d995e98ef3e86b992d` 仍以 `rejected / insufficient` 完成，证明 exporter
  故障不阻断业务路径。
- 额外 success replay `run_bbe196418f5b4f6a8296b2c205c864d1` 以 9 条 Evidence、`completed /
  sufficient / publish` 完成，捕获 1 条 turn trace、28 个 Span；业务成功路径和失败路径都
  经过同一官方插件，而不是只测一个空会话。
- 第三种 insufficient/stale replay `run_1f8290069d4c45228123ef625cf08643` 以
  `rejected / insufficient / critical_data_unavailable` 完成并捕获 7 个 Span，证明最小
  事实不足路径也不会因 telemetry 接入而被误报为成功。
- 运行产物位于 `tmp/dsh-observability-canary/` 和 `tmp/dsh-native-core/`，均由 `.gitignore`
  忽略；本次未使用 Provider key、未启动 Pilot、未修改 active pointer 或 Hub schema。

### 未通过或尚未证明的项目

- 当前 canary 使用上游 `llm-replay` 和 metadata receiver，证明的是插件挂载、Span 结构和
  fail-open；还没有在真实 Provider 的 TTFT/Token/retry/subagent 组合上做产品价值结论。
- Docker 当前没有可用的 Jaeger 镜像缓存，`--jaeger` 路径只保留配置和启动入口，需在能拉取
  `jaegertracing/all-in-one:1.57.0` 的环境再做 UI 查询验收。
- `dsh.session.id -> dsh_session_links -> run_id` 的只读业务关联已有身份和表结构，但还没有
  新增 TelemetryRef/Trace URL 字段；该工作属于 OBS-02，不能在 OBS-01 中顺手扩表。

### OBS-01 后的 owner 决策门

1. OBS-02 是否在不复制 Span 的前提下增加 TelemetryRef/Query View 和 Trace URL；
2. 本机长期后端选择 Jaeger/Tempo/OTel Collector 及其保留期、访问控制和备份策略；
3. E3 观察期是否保留技术 Trace 多久（默认仍禁止正文采集）；
4. 以后出现第二个真实 Agent 时，是否再启动 OBS-03 Pilot 对照评估。

OBS-01 已通过但不授权修改 active runtime、Hub schema、DSH upstream、插件市场配置或生产网络
权限。后续只能在新的 owner gate 下开展 OBS-02/OBS-03；本阶段的唯一新增运行开关是显式的
`DSH_OBSERVABILITY_ENABLED=1`。

## 10. 复核来源

- 用户提供的文章：[微信原文](https://mp.weixin.qq.com/s/b6_-8JB6QigS_uA-txPKLQ)
- 独立插件仓库：[loongsuite/dsh-plugin](https://github.com/loongsuite/dsh-plugin)
- 独立插件 v0.1.2：[release](https://github.com/loongsuite/dsh-plugin/releases/tag/v0.1.2)
- 多 Agent 采集器：[alibaba/loongsuite-pilot](https://github.com/alibaba/loongsuite-pilot)
- OpenTelemetry GenAI 语义约定：[semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
