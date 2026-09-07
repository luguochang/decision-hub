# DSH 插件底座、现有插件资产与平台解耦优化方案

日期：2026-09-06  
状态：`proposed / architecture remediation pending owner confirmation`  
目的：基于当前代码而非目标口号，说明 DSH 插件、Decision Hub 自研插件、LoongSuite、Domain Pack 和后端业务模块的真实关系，并给出后续去金融耦合的可执行方案。

## 1. 结论先行

1. 当前 Decision Hub **确实以 DSH 原生插件形式接入**。`Extension / Host / Client` 是 DSH 提供的插件扩展接口，不是插件之外的另一种集成方式。
2. 自研交付物是一个可安装的 DSH 插件包 `@decision-hub/dsh-plugin`，它包含 Host Bridge、Client UI、Research Tool、Synthesis Tool 四个扩展面，并通过官方 `dsh plugin --profile web add` 安装；没有修改 DSH 上游源码。
3. 当前还组合了 DSH 官方 Web Search、Subagent、Todo、Compaction 等插件。其中 Subagent 只是已装载能力，尚未形成真实多 Agent 角色协作；Todo 在受限无人值守研究 Prompt 中被禁止调用，不能算当前金融主链成果。
4. `@loongsuite/dsh-plugin@0.1.2` 已完成精确版本锁定和隔离 canary，但默认产品启动不启用，业务前端也尚无 Trace 链接。它目前解决技术 Trace/Metric 验证，不解决事实不足、金融分析或业务资产沉淀。
5. `packs/crypto_macro` 是 Decision Hub 的领域包，不是自动可安装的 DSH 插件。当前由 Python Worker 直接读取，其中部分金融常量已泄漏到 Platform Kernel、组合根、查询层和 DSH Client 插件，平台化尚未完成。
6. 后续不重写 DSH，不另造 Harness。优化重点是把现有插件拆成“通用平台桥接插件 + 金融产品插件”，并把金融规则从通用后端移回 `crypto_macro` 产品扩展。

## 2. 名词和边界

| 名称 | 在当前项目中的含义 | 是否 DSH 原生插件 |
|---|---|---|
| DSH Plugin | 通过 profile/Cordis、`dsh.bundle`、`dsh.client` 安装和组合的运行单元 | 是 |
| Host Extension | 插件在 DSH 服务端使用 Session Controller、Web Server、Agent/Session 事件等公开 seam | 是，属于插件服务端扩展面 |
| Client Extension | 插件向 DSH Web 的 slot/view/settings 注入界面 | 是，属于插件浏览器扩展面 |
| Agent Preset | 为某类 Agent 组合 Persona、Tool、Subagent、Compaction 等插件 | 是插件组合配置，不是独立业务账本 |
| Decision Hub Capability | Hub 对 Tool/Provider/MCP 的统一输入输出、权限、预算和审计声明 | 不一定；实现可以来自 DSH Plugin、MCP、HTTP Provider 或 Python Adapter |
| Domain Pack | 领域 doctrine、事实要求、角色策略、Gate、来源和评测配置 | 当前不是 DSH 安装包，由 Hub 后端加载 |
| Product Extension | 同一领域的一组 Domain Pack、后端组合、DSH Preset/Client View 和评测 | 目标平台扩展单元，可包含一个或多个 DSH Plugin |

“DSH 一切皆插件”描述的是 Harness 内部的能力组合方式。Hub 的 Evidence、Gate、Run、Artifact、Outcome 等业务状态不能因此塞进 DSH Session JSONL，也不能让任意插件直接写业务账本。插件负责执行和交互，Hub 负责可信业务事实；二者通过稳定契约连接。

## 3. 当前插件清单与真实状态

### 3.1 自研 DSH 插件包

| 包/导出 | 当前状态 | 解决的问题 | 当前缺口 |
|---|---|---|---|
| `@decision-hub/dsh-plugin` Host | 主流程启用 | 将 Hub durable Run 提交到 DSH Session，处理 readiness、幂等 submit、status、cancel、terminal callback、重启恢复和版本 fail-closed | Host 路由命名和部分业务接口仍以 research 为中心，尚未抽成多产品 registry |
| `@decision-hub/dsh-plugin/client` | 主流程启用 | 在官方 DSH Web 内增加主动研究、业务状态、报告和 Decision Desk 跳转，不复制 DSH Chat/Session/Trajectory 页面 | 写死 `Crypto Macro Trader` 和金融报告结构，通用平台 UI 与金融产品 UI 混在同一文件 |
| `@decision-hub/dsh-plugin/research-tool` | `decision-research` preset 启用 | 把 Hub 受审计 Capability Gateway 暴露为 DSH Tool；Session ID 由运行时注入，模型不能伪造；Tool 返回结构化 EvidenceCandidate/Failure | 语义属于“可信研究”扩展，不应继续与通用 Host Bridge 无边界地放在一个入口中 |
| `@decision-hub/dsh-plugin/synthesis-tool` | `decision-research` preset 启用 | 在 DSH Tool 边界用 codegen Zod Schema 校验最终候选，避免从自由文本或助手 JSON 中猜测业务结论 | 当前输出 schema 固定为金融 research synthesis，应迁入金融/研究产品扩展 |
| `cordis.patch.yml` | 主流程启用 | 使用 `dsh.bundle` 将 Host 插件注入固定版本 DSH Web profile | 当前只注册一个 Decision Hub 插件，尚无产品插件清单和版本兼容 registry |

这不是四个互不相关的 Marketplace 插件，而是**一个自研 DSH 插件包的四个公开扩展面**。代码入口由 `package.json` 的 `dsh.bundle`、`dsh.client` 和 `exports` 声明，启动器使用官方 CLI 安装。

### 3.2 当前 Agent Preset 组合的 DSH 官方插件

| DSH 插件 | 主流程状态 | 用途 | 能否算本项目自研 |
|---|---|---|---|
| `@deepseek-ai/dsh-persona` | 启用 | 注入 Decision Hub Research Manager 身份和安全边界 | 否，仅配置 Persona |
| `@deepseek-ai/dsh-agent-instructions` | 启用 | 向 Session 注入受限研究指令 | 否 |
| `@deepseek-ai/dsh-tool-web` | 启用 | 提供 DSH 原生 `web_search` / `web_fetch`，用于发现和阅读来源 | 否；本项目贡献是 Evidence attestation 和来源准入，不是搜索引擎本身 |
| `@deepseek-ai/dsh-tool-subagent-control` / `list-agents` / `dsh-tool-subagent` | 已装载 | 提供 DSH 原生委派、查询和受限子 Agent 执行 | 否；当前没有把三个 Role Profile 映射为真实 Subagent，不能宣称已落地多 Agent |
| `@deepseek-ai/dsh-tool-todo` | 已装载但研究 Prompt 禁止调用 | 通用任务清单能力 | 否；对当前无人值守金融主链没有实际贡献，后续应决定移除或只在交互型 Agent 启用 |
| `@deepseek-ai/dsh-compaction-basic` / `dsh-command-compact` / `dsh-compaction-tool-result-pruner` | 启用 | 长会话上下文压缩和大 Tool Result 裁剪 | 否；本项目只负责选择参数和验证兼容性 |
| DSH Web 基础 Session/Trajectory/Tool UI 与 JSONL persistence | 上游基础 profile 启用 | 会话、运行轨迹、工具节点和持久化 | 否，直接复用上游能力 |

### 3.3 LoongSuite 和旧 SDK/MCP 路径

| 能力 | 当前状态 | 价值 | 不能解决的问题 |
|---|---|---|---|
| `@loongsuite/dsh-plugin@0.1.2` | `DSH_OBSERVABILITY_ENABLED=1` 时安装；隔离 canary 已通过；默认产品关闭 | 将 Session/Agent/Step/LLM/Tool 生命周期输出为 OpenTelemetry Trace/Metric，可接本地 Jaeger 或其他 OTLP 后端；Exporter 失败不影响主链 | 不提供 Web Search、金融数据、Gate、自进化或业务 Evidence；当前用户页面看不到是因为 OBS-02 TelemetryRef/View 尚未实现 |
| LoongSuite Pilot | 未集成 | 未来多个不同 Agent Runtime 并存时，可统一采集 DSH/Codex/Pi 等轨迹 | 当前只有一个主 Harness，不应与 DSH OTel 插件重复采集 |
| DSH Python SDK + `dsh-mcp-client` profile | 保留为 candidate/canary/fallback，不是当前 DSH Web 主路径 | 隔离验证 SDK、MCP、Session/Trace 映射和回放 | 不应与 Web Host 主路径同时被描述成两个正式 Runtime，也不能成为第二套产品入口 |

LoongSuite 当前不是无用代码，但属于“已验证、未产品化”的可选平台插件。下一步只有两种诚实状态：完成 OBS-02 后作为可见的技术可观测能力启用，或继续默认关闭并在简历中只写“完成兼容性验证”，不能写成已经交付可观测平台。

## 4. 当前运行链路

```text
官方 DSH Web（固定上游版本）
  + @decision-hub/dsh-plugin
      - Host Bridge
      - Client UI
  + decision-research Agent Preset
      - DSH Persona / Instructions
      - DSH Web Search / Fetch
      - DSH Subagent controls（可用但当前未实际拆角色）
      - DSH Compaction
      - Decision Hub Research Tool
      - Decision Hub Synthesis Tool
  + @loongsuite/dsh-plugin（仅显式 opt-in）

Hub Worker
  -> 外层 LangGraph evidence-round lifecycle
  -> DSH Host 创建/恢复同一 Session
  -> DSH Agent Loop 自主调用官方 Search 与自研 Research Tool
  -> Capability Gateway 验证来源/PIT/鲜度/预算并写 Evidence
  -> 自研 Synthesis Tool 校验候选
  -> Hub 确定性 Gate
  -> Artifact / Outcome / Evaluation
  -> DSH Client 报告 + Decision Desk 管理视图
```

LangGraph 不是第二个 Harness，也不应调度 DSH 内部 Subagent。它只管理跨 DSH generation 的 durable Run、证据轮次、Checkpoint 和停止条件。DSH 自己管理单轮内部的模型、Tool、Subagent、Session 和轨迹。

## 5. 已确认的代码架构问题

### P0：通用层混入金融领域常量

- `apps/hub_worker/composition.py` 写死 `DOMAIN_PACK = "crypto_macro.v1"`，直接创建 OKX/CoinEx/Crypto Event Window Provider。
- `apps/hub_worker/research.py` 直接依赖 `CryptoMacroFactPack`，并写死 `crypto_macro.manager.v1`。
- `packages/kernel/application/commit.py` 写死 `BTC-USDT-SWAP` 和 `30m/24h/72h`。
- `packages/kernel/persistence/db.py` 给通用 Snapshot 默认写入 `crypto_macro.v1`。
- `packages/orchestration/langgraph/graphs/agentic_research_graph.py` 在通用 orchestration 中校验固定金融 Horizon。
- `packages/query_views` 和 `apps/decision-desk` 存在 `crypto_macro` 默认值与金融 View 假设。

影响：新增 PPT 或其他领域时必须修改 Kernel、Worker、Query 和前端，不符合插件化目标。

### P0：自研 DSH 插件同时承担平台桥接和金融产品界面

- Host Bridge 的通用 Session/Run 关联是平台能力。
- Research/Synthesis Tool 是研究类产品能力。
- Client 中的主动研究、金融报告、Horizon 和 `Crypto Macro Trader` 是金融产品能力。
- 四者目前位于同一个包和大文件，升级与回滚无法按产品独立进行。

影响：以后新增 PPT 插件时，容易继续向同一个 `@decision-hub/dsh-plugin` 堆页面、Tool 和路由，最终形成新的单体插件。

### P1：Domain Pack 只有配置边界，没有统一运行时注册边界

`pack.yaml` 已声明 profile、capability、gate 和 evaluation，但组合根仍直接 import 金融实现。Domain Pack 不是安装后即可被 Platform 发现、校验和加载的 Product Extension。

影响：所谓“新增领域只加插件”目前尚不成立。

### P1：已装载插件与已产生产品价值没有区分

- Subagent 已启用，但 Role Profile 没有形成真实 Subagent 运行。
- Todo 已启用，但主 Prompt 明确禁止使用。
- LoongSuite canary 已通过，但默认关闭且前端无 Trace 引用。
- 旧 SDK/MCP 路径仍保留，容易被误解为正式双 Runtime。

影响：文档、简历和产品页面容易把“可用能力”“已验证能力”“主流程能力”混写。

## 6. 目标架构

```text
DSH Upstream（只读、固定版本、可升级回滚）
  |
  +-- @decision-hub/dsh-platform-plugin
  |     Host Bridge / Session-Run Link / readiness / callback / generic shell
  |
  +-- @decision-hub/dsh-crypto-macro-plugin
  |     crypto Agent Preset / Research Tool / Synthesis Tool / Inbox / Report View
  |
  +-- DSH official plugins
  |     Web Search / Subagent / Compaction / Session / Trajectory
  |
  +-- @loongsuite/dsh-plugin（可选平台可观测插件）
        OTLP Trace；只通过 TelemetryRef 与 Run 关联

Decision Hub Platform Core
  Event / Observation / Run / Extension Registry / Capability / Artifact / TelemetryRef
  不认识 BTC、金融 Horizon、FRED、OKX、CoinEx、crypto_macro

Crypto Macro Product Extension
  manifest / doctrine / role / evidence policy / provider binding / gate / forecasts
  scheduler policy / query projection / DSH contribution / evaluations

未来 PPT Product Extension
  独立 manifest / skills / tools / artifact schema / DSH contribution / evaluations
  不 import crypto_macro
```

### 6.1 插件边界决策

1. `@decision-hub/dsh-platform-plugin` 是所有领域共享的 DSH 插座，只处理运行时、状态关联和通用扩展注册。
2. `@decision-hub/dsh-crypto-macro-plugin` 是首个产品插件，负责金融 Agent Preset、研究工具绑定和金融页面贡献。
3. 后端 `ProductExtensionManifest` 是平台发现领域能力的唯一入口；它不是第二套插件系统，而是把 DSH 插件、Python 组合、Schema 和评测绑定为一个可审计产品扩展。
4. DSH 官方插件保持原样组合。能够直接满足需求的能力不重新实现，只增加必要的权限、结果校验和业务引用。
5. LoongSuite 保持第三方可选插件，不复制其 OTel 实现；Hub 只保存 `telemetry_ref` 和摘要。

## 7. 目标代码结构

```text
extensions/
  dsh/
    platform/                         # @decision-hub/dsh-platform-plugin
      src/host/                       # generic Session/Run bridge
      src/client/                     # generic workspace/status/extension slots
      tests/
    products/
      crypto-macro/                   # @decision-hub/dsh-crypto-macro-plugin
        src/tools/research-tool.ts
        src/tools/synthesis-tool.ts
        src/client/
        preset/agent.cordis.yml
        tests/

products/
  crypto_macro/
    extension.yaml                    # 产品扩展唯一 manifest
    contracts/                        # 金融 schema canonical source
    domain/                           # doctrine/requirements/gates/horizons
    composition/                      # source/provider/capability bindings
    projections/                      # 金融报告 Query/View
    evaluations/
    README.md

packages/
  kernel/                             # 通用 Event/Run/Artifact/Extension/Capability
  product_extensions/                 # manifest loader + registry + lifecycle port
  orchestration/langgraph/            # 通用 durable lifecycle，不含金融 horizon
  runtime_adapters/dsh_runtime/        # DSH Web/SDK adapter，不含金融 prompt

infra/dsh/
  upstream.lock.json
  observability/                      # LoongSuite lock/canary
  profiles/                           # 平台 profile；产品 preset 来自产品插件
```

迁移期保留旧目录兼容导出，不一次性移动历史 migration、Run、Artifact、Forecast、Outcome 或 Evaluation 数据。兼容层必须有删除条件和截止阶段，禁止永久双写。

## 8. 强制依赖规则

```text
1. DSH upstream 不 import Decision Hub；只通过官方 plugin/profile seam 组装。
2. platform plugin 不出现 crypto_macro、BTC、FRED、OKX、CoinEx、30m/24h/72h。
3. Platform Kernel 不 import products/* 或 packs/*。
4. Product Extension 可以依赖 Platform Port/Contract，Platform 不反向依赖产品实现。
5. LangGraph 外层不实现模型 Tool Loop 或 Subagent Supervisor。
6. 多 Agent 只通过 DSH 原生 Subagent；Role Profile 未映射且未产生轨迹前不得写“已实现”。
7. 第三方插件输出不能直接写 Hub Ledger；必须通过 Capability/Evidence Gateway。
8. LoongSuite Trace 不复制到业务库；只保存稳定 TelemetryRef、运行摘要和后端链接。
9. 每个插件标记 installed / enabled / exercised / product-visible / value-validated 五级状态。
10. 架构测试扫描通用模块中的领域常量和反向 import，违反即阻断合并。
```

## 9. 实施任务与顺序

### M0：事实冻结与回归基线

- [ ] 固定当前 DSH 上游、插件 build hash、数据库迁移和一条 replay/一条 live 失败样本。
- [ ] 生成当前插件 inventory，记录五级状态和对应测试证据。
- [ ] 将旧 SDK 路径明确标为 fallback/canary，主产品只保留 DSH Web Host 入口。

### M1：先增加架构测试

- [ ] 新增 forbidden dependency 测试，阻止 Kernel/Platform 引用金融常量和 provider。
- [ ] 新增插件 manifest 测试，验证 bundle/client/tool exports、版本、权限和回滚信息。
- [ ] 新增 Domain Pack 加载契约测试，先用 `crypto_macro` 和测试 fixture 验证 registry，不创建伪 PPT 产品。

### M2：拆分自研 DSH 插件

- [ ] 从现有包提取 generic Host Bridge 与 generic Client shell，形成 platform plugin。
- [ ] 将 Research/Synthesis Tool、金融 Inbox/Report 和 Agent Preset 迁入 crypto-macro plugin。
- [ ] 保留旧包名的短期兼容 meta-package，启动日志明确实际加载的两个插件，迁移结束后删除。

### M3：后端领域回迁

- [ ] 建立 `ProductExtensionManifest` 和 registry，Worker 通过 manifest 选择 request factory、capability bindings、gate 和 projections。
- [ ] 将 `CryptoMacroFactPack`、OKX/CoinEx/FRED、事件窗口和金融调度策略迁入 `products/crypto_macro`。
- [ ] 将 BTC instrument、30m/24h/72h Forecast/Outcome 生成迁出通用 Commit service。
- [ ] 数据库历史表和数据不改写；新增通用 extension/artifact metadata，并由金融扩展读取旧字段兼容视图。

### M4：LoongSuite 产品化或明确后置

- [ ] 保持 opt-in 和 `captureContent=false`。
- [ ] 实现 `TelemetryRef` Query/View，让 DSH Session、Hub Run 和 OTel Trace 可以跳转关联。
- [ ] 在 DSH/Decision Desk 仅显示耗时、LLM/Tool/Retry/Subagent Span 摘要和 Trace 链接，不展示无用 raw JSON。
- [ ] 如果没有稳定 OTLP 后端或用户价值，继续默认关闭，只保留 canary，不把它写成已交付能力。

### M5：产品和升级验收

- [ ] 官方 DSH Web 的 Chat/Session/Trajectory/JSONL 行为不回归。
- [ ] 金融文本输入仍可完成 Search -> Evidence -> Gate -> Report 主链，失败时保留真实原因。
- [ ] 自动事件 Run、通知、复查和 Outcome 不因插件拆分丢失。
- [ ] 新增测试 Product Extension 只新增 manifest/实现/DSH contribution，不修改 Platform Kernel；真实 PPT 在需求出现后单独立项。
- [ ] DSH 上游升级只替换 lock/build/profile，两个自研插件分别执行兼容测试和回滚。

## 10. 验收门与停止线

### 工程验收

- Platform forbidden-reference 扫描为 0。
- `@decision-hub/dsh-platform-plugin` 不包含金融 UI/schema。
- `@decision-hub/dsh-crypto-macro-plugin` 可单独启停；关闭后 DSH 通用工作台仍可启动。
- 现有 migration 和历史账本升级测试通过，无数据重写和双写漂移。
- DSH plugin、Python、前端、contract codegen、pyright、build、replay E2E 全部通过。

### 产品验收

- 用户从 DSH 唯一入口选择金融产品，能够看到金融 Persona、实际 Tool 调用、证据报告和业务终态。
- 页面明确区分 DSH 原生轨迹、LoongSuite 技术 Trace 和 Hub 业务 Evidence，不展示大段 raw JSON。
- 插件已安装但未使用时显示为 available，而不是伪装成“已参与本次运行”。

### 停止线

- 不因“平台化”重写 DSH Agent Loop、Session、Trajectory、Web Search、Subagent 或 OTel SDK。
- 不在本阶段开发 PPT 业务、ASR、自动交易或插件市场。
- 如果拆分要求修改 DSH 上游私有 API，先停止并评估官方 seam/上游 PR，不建立长期 fork。

## 11. 简历与对外表述口径

允许表述：

- 基于 DSH 原生插件体系开发 Decision Hub 插件，完成 Host/Client/Tool/Synthesis 四类扩展面。
- 组合 DSH 官方 Web Search 和 Compaction 能力，并以 Domain Pack/Capability Gateway 接入首个金融研究产品；Subagent 插件当前仅装载，不能表述为已运行多 Agent。
- 对 LoongSuite DSH OTel 插件完成精确版本接入与故障隔离验证。

禁止表述：

- 自研了 DSH Agent Loop、Session、Trajectory、Web Search 或 Subagent。
- 当前已经运行宏观/市场/反方三个独立 Agent。
- LoongSuite 已在默认产品页面完整交付。
- 当前已经实现 PPT 等多个领域即插即用。

## 12. 本文确认后的唯一第一步

先执行 `M0 + M1`：固化插件 inventory 和架构依赖测试，再拆插件或移动代码。没有测试先锁住边界之前，不开始目录迁移；否则会再次形成“文档正确、代码继续耦合”的状态。
