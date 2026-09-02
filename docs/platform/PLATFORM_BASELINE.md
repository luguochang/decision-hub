# 通用智能体产品平台基线与代码审计

日期：2026-08-29
状态：`accepted`（owner 于 2026-08-29 随 R2-R Stage Gate 接受）
关联决策：[ADR-0005](../decisions/ADR-0005-dsh-harness-plugin-bridge.md)、[ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)、[ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md)

## 1. 结论先行

当前代码**可以演进成以 DSH 为首个正式 Agent Harness 的通用产品底座**，但必须准确区分两条主线：

```text
产品主线：Decision Hub Platform
  拥有 Task/Run/Evidence/Artifact/Asset/Evaluation/Version/Promotion

Agent 执行主线：DSH Research Harness（R2-R 首个 candidate）
  拥有 Session/Step/Tool/Subagent/Skill/Plugin/Compaction/Trace
```

因此最终选择不是“DSH 或自研平台二选一”，也不是“DSH UI 上装几个功能”。推荐结构是：

```text
Decision Desk / API / MCP
            |
Platform Core
            |
Product Extension
            |
Role Profile + Domain Pack
            |
ResearchHarnessRuntime Port
            |
DSH restricted decision-research profile
            |
audited Capability Plugins / MCP / typed providers
```

DSH 是 Agent 执行底座，不是业务数据库、PIT 真源、Gate、资产库或最终产品前端。LangGraph 只保留产品级生命周期和可恢复路由，不再扩张成第二套通用 Agent Loop。

## 2. 当前代码真实状态

| 能力 | 当前状态 | 判断 |
|---|---|---|
| 文本、来源、PIT、Run、Artifact、Forecast、Outcome | 已实现并有测试 | 可保留，是第一个决策产品的可靠底座 |
| Run/Step/Call、成本、错误、checkpoint、outbox | 已实现并有测试 | 可抽为跨产品运行资产 |
| Workbench/MCP/Capability 准入 | 已实现最小边界 | 可作为外部 Harness/插件的入口，不等于插件已集成 |
| Dataset/Candidate/Experiment/Experience/FailurePattern/Promotion | 已实现离线闭环 | 是个人和企业资产的核心雏形 |
| R2-L worker/heartbeat/search seam/Operations | 当前未提交工作树中已实现并自测 | 可保留；只是运行化骨架，不是研究 Agent |
| 固定 `policy_delta -> counter_thesis -> synthesis` | 已实现 | 冻结为 `fixed-baseline.v1`，只用于回放、对照和显式降级 |
| `DshAgentRuntime` | 只有 callable wrapper | 名称存在不代表 DSH SDK、Session、Tool、Subagent 已接入 |
| DSH Python SDK/受限 `decision-research` profile | `0.1.1rc1` 已锁定；restricted profile、adapter、local handshake 与真实 Tool/Subagent canary 已通过 | R2-R 的首个真实 Harness candidate；06E 后仍未 promotion |
| 主动补证与 Tool Loop | candidate path 已实现并通过离线/真实事件 fail-closed 验收 | DSH 保持 candidate/shadow；真实 Search reliability 和 owner usefulness 未达 Promotion 门 |
| 通用 PPT/文件产物领域 | 未实现 | 只能说有扩展方向，不能说当前已隔离完成 |

外部核对显示 DSH 当前提供 `web`、`headless`、`sdk`、`sdk-minimal`、`acp` profile；完整 `sdk` 继承 base 的工具、Session、Skill、Subagent、Web、遥测和持久化能力。`sdk-minimal` 明确缺少 Web、Subagent、Skill、compaction 和 telemetry，不能用于 R2-R 完整验收。默认 `sdk` 又是 coding agent 组合，能力面过宽，因此正式候选必须用 profile/patch 组装 `decision-research`：保留研究能力，默认禁用 shell、文件写入、任意插件安装和宿主凭据访问。

DSH 当前仍是 developer preview，且官方安全说明明确尚未经过安全审计。因此接入必须锁定精确版本、独立 `DSH_HOME`、隔离 workspace、最小权限、插件白名单和回放门，不能直接开放任意插件或宿主凭据。

本次核对的官方事实源（2026-08-29）：

- [DSH README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.zh.md)：Everything is a Plugin、developer preview、`web` 入口；
- [架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.zh.md)：Cordis 插件树、`web/headless/sdk/sdk-minimal/acp` profile、Session/Agent/Tool seam；
- [Python SDK 指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.zh.md)：`DeepSeekHarness` client、持久 `DSH_HOME`/Session JSONL、profile/patch/plugin 机制；
- [安全说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.zh.md)：未安全审计，sandbox/approval 不能保证隔离；
- 最新公开仓库 release 为 `dsh-v0.1.2-alpha.1`，但 R2-R-01 实测 Python SDK/PyPI 可用组合为 `deepseek-harness-sdk==0.1.1rc1` + bundled runtime server `0.0.1`；已固定该组合和 `decision-research.v1:1d4ce1f40ab265e4` profile，不能因“最新”自动升级。

## 3. 最终五层所有权

### 3.1 Platform Core

跨交易、PPT 和未来产品共享：

- Workspace/Owner scope（首期仍是单 owner）；
- Task、Run、Step、Call、TraceRef、Budget、Error；
- Evidence/Source/Artifact 的通用身份、时间、hash 和 lineage；
- Asset、Dataset、Evaluation、Candidate、Version、Promotion、Rollback；
- Scheduler/lease/heartbeat/outbox/notification；
- Capability registry、权限、成本、健康和审计。

Platform Core 不包含 BTC、方向、收益率、幻灯片页数、主题风格等领域字段。

### 3.2 Product Extension

产品扩展定义一种结果闭环及其领域契约：

| Extension | 自己拥有 | 复用 Platform Core |
|---|---|---|
| `decision` | Forecast、Outcome、Gate、时间周期、行动语义、Brier/收益评测 | Task/Run/Evidence/Artifact/Evaluation/Version |
| `presentation` | Brief、Deck、Slide、Citation、Render、Overflow、人工修改率 | Task/Run/Evidence/Artifact/Evaluation/Version |

PPT 不是 `crypto_macro` 下的另一个角色，也不应被迫生成 Forecast 或走交易 Gate。

### 3.3 Domain Pack

Domain Pack 把某一业务方法版本化，例如 `crypto_macro.v1`、未来的 `a_share.v1`。它拥有：

- Doctrine 和术语；
- 事件分类、证据要求、来源优先级和鲜度；
- 允许的 Role Profile 与 Capability；
- 确定性 Gate/置信度上限；
- 领域评测 rubric、fixture 和预算。

Domain Pack 只依赖 Product Extension 的公开契约，不 import DSH、LangGraph 或具体 Provider。

### 3.4 Role Profile

Role 不是一个随意新增的 Prompt 文件，也不是每个角色都写一个 Python 插件。它是声明式组合：

```text
persona
+ required capabilities
+ allowed tools
+ input/output contract
+ evidence policy
+ budget/deadline
+ evaluation policy
```

只有需要新算法、新外部协议、特殊运行时或独立 UI 时，才创建代码型 Extension/Plugin。

### 3.5 Capability Plugin

Capability 是原子、可审计、可组合的执行能力，例如：

- `web.search`、`web.fetch`；
- `official.fed.statement`、`macro.us_yield_curve`；
- `market.okx.derivatives`、`market.coinglass.crowding`；
- `file.read`、`ppt.render`、`ppt.layout_check`。

一个交易员 Role 会调用多个 Capability；一个 Capability 也可被多个 Role/Domain 复用。DSH plugin、MCP server 或独立 Provider 只是实现来源，进入正式链前都要转换成自己的 `CapabilityManifest` 并通过审计。

## 4. DSH、LangGraph 与 Kernel 的准确关系

| 组件 | 应负责 | 不应负责 |
|---|---|---|
| DSH | Agent Session、Step Loop、模型/工具循环、Subagent、Skill/MCP、上下文压缩、原始 JSONL 轨迹 | Product Task/Run 唯一事实、PIT、Gate、Forecast/Outcome、Promotion |
| LangGraph | Product Run 生命周期、Evidence round、双 Snapshot、checkpoint/recovery、充分度路由、确定性提交 | 再写一套通用 ReAct/Tool/Subagent/Session Harness |
| Platform Core | 业务与企业资产、权限、契约、账本、评测、版本、发布和回滚 | DSH 内部插件树、模型上下文或 UI state |
| Decision Desk | 正式产品查询、人工反馈、owner review、可观测视图 | 直接读取 DSH JSONL、SQL 或 LangGraph state |

DSH 自己也有 Jobs、Workflow 和 Web UI，但“它能做”不等于“产品所有权应交给它”。事件触发、业务幂等、PIT、Outcome、评测和晋升需要跨 Harness 保持稳定，所以仍由 Platform Core/LangGraph 外层拥有。

## 5. 代码处置矩阵

| 处置 | 当前对象 | 做法 |
|---|---|---|
| 保留 | canonical schema/codegen、PIT、账本、Gate、outbox、Run Inspector、eval/evolution、source/provider ports | 不迁数据、不重写行为，继续用现有测试保护 |
| 抽象 | Task/Run/Step/Call、Artifact identity、Evidence lineage、Asset/Evaluation/Version、Capability | 在真实第二调用方出现时提取通用协议；先加依赖规则和命名空间 |
| 迁移 | Forecast/Outcome/Brier、30m/24h/72h、`crypto_macro.v1` 默认值、固定金融 Graph/前端常量 | 渐进迁入 `decision` Extension 和 `crypto_macro` Pack；使用兼容 adapter/迁移，不大爆炸改名 |
| 冻结 | `fixed-baseline.v1`、历史 migration、历史 Run/Artifact、现有 replay fixture | 只修复确定性 bug，不继续向 fixed graph 加搜索和角色 |
| 删除 | 当前无立即删除项 | 只有替代实现通过 contract/replay 且无调用方后，另立任务删除；本轮不清理用户工作树 |

## 6. 当前最重要的结构缺口

1. `AgentResult.payload`、`CapabilityCall.input/output` 仍是 `dict/Mapping`，跨 Harness 的研究契约还不够严格。
2. `CapabilityManifest` 缺少数据类别、鲜度、replay policy、secret policy、健康 canary、来源包/hash 等正式字段。
3. `Forecast` 与 `crypto_macro.v1` 仍位于当前 Kernel/前端默认路径，尚无真正的 Product Extension registry。
4. 没有 `ResearchHarnessRuntime`、DSH SDK client、profile binding、Session/Trace ref 或 DSH readiness。
5. `crypto-macro-decision` 的 Doctrine、Evidence、Gate、Role、Eval 还没有拆成可执行 Pack 资产。
6. 没有第二产品的契约测试，因此“通用底座”目前只是架构目标，尚未被 PPT Extension 证明。

## 7. 渐进实施，不进行第二次重写

### 当前 R2-R 必做

- 先接受 ADR-0008/0009 与 Stage Charter；
- `R2-R-00` 锁 `ResearchHarnessRuntime`、Evidence、Role Profile、Capability 和 Pack contract；
- 接入 DSH Python SDK + 受限 `decision-research` profile，复用其完整 Agent Loop；
- 把 `crypto-macro-decision` 拆成 Pack 资产；
- 保持现有账本、Gate、评测和前端 Query/View；
- fixed baseline 与 DSH candidate 公平回放、shadow 和回滚。

### R2-R 不做

- 不先完成全仓目录重构；
- 不开发 PPT；
- 不 clone/fork DSH；
- 不把 DSH Web 变成正式产品前端；
- 不实现多用户、公共插件市场、自动晋升或自动交易。

### R2-R 通过后

用一个最小 `presentation` Extension 做第二调用方证明。只有第二调用方真正需要共享接口时，才提取 Platform Core；这能验证隔离又避免预建一套没有使用者的“大平台”。

## 8. 产品与企业停止条件

通用底座不是目录齐全就算完成。至少满足以下证据才可对外称为产品平台：

- `crypto_macro` 在真实前瞻事件中证明主动补证、可恢复、可解释；
- 第二 Product Extension 在不改 Platform Core 领域字段的情况下接入；
- DSH 升级或替换不会迁移业务账本；
- Capability 安装、禁用和回滚不删除历史资产；
- 每个候选都有 dataset、evaluation、版本和 owner promotion 证据；
- 前端展示人可读进度、证据和结果，不依赖 raw JSON。

在这些条件之前，准确名称是“单 owner 可审计智能体产品底座 + 第一个决策领域”，不是成熟多领域企业平台。
