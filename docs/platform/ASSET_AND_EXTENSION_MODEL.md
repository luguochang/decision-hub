# 资产沉淀与可插拔扩展模型

日期：2026-08-29
状态：`accepted`（owner 于 2026-08-29 随 R2-R Stage Gate 接受）

## 1. 真正需要沉淀的资产

模型、Harness 和 UI 都会快速变化。能长期形成个人价值并转化为企业护城河的是以下资产：

| 资产 | 内容 | 为什么可迁移 |
|---|---|---|
| Domain Pack | Doctrine、证据要求、来源优先级、Gate、预算、评测 | 不依赖某个模型或 Harness |
| Capability | 输入/输出 schema、权限、成本、健康、实现映射 | 可以由 DSH plugin、MCP 或 Provider 替换实现 |
| Dataset | PIT replay、holdout、前瞻 shadow、失败样本 | 可以公平比较模型、Runtime 和策略 |
| Evidence | 来源、三时间戳、hash、revision、引用和冲突 | 是结论可追溯性的基础 |
| Artifact/Outcome | 真实输出及其后验结果 | 能证明是否有实际价值 |
| Evaluation | 校准、准确率、覆盖、延迟、成本、人工修改率 | 决定版本是否值得晋升 |
| Experience/FailurePattern | 经过结果与评测验证的经验和失败模式 | 不是聊天记忆或未经验证的“教训” |
| Version/Promotion | Pack、Profile、Skill、模型、工具和 active pointer 历史 | 支持升级、回滚和责任追踪 |

Prompt 和 DSH Session 有价值，但只有在绑定版本、证据和评测后才成为资产。单独保存大量聊天记录或 JSON 不是资产沉淀。

## 2. DSH JSONL 与产品账本双真源

```text
DSH Session JSONL
  = 模型看到了什么、调用了什么工具、Subagent/Step 如何运行

Decision Hub Ledger
  = 为什么启动任务、使用了哪些 PIT Evidence、产生何种业务结果、
    结果后来是否正确、哪个版本被晋升
```

产品账本只保存：

- `dsh_session_id`；
- trace path/object ref 与 content hash；
- 关键 `ResearchTraceEvent` 投影；
- 规范化 Evidence/Artifact/Outcome/Evaluation；
- Pack/Profile/Runtime/Capability 版本。

不把全部 DSH raw JSON 复制进第二张日志表；也不只依赖 DSH JSONL 查询 Brier、费用后结果、PIT 回放或版本晋升。

## 3. 双层插件与四级插拔模型

“插件”必须区分两个层级，不能再混用：

| 层级 | 含义 | 数据所有权 |
|---|---|---|
| Product Extension | 产品插件式模块，例如 `decision`、`presentation` | 拥有产品契约、业务历史、迁移和评测闭环 |
| DSH Native Plugin | Harness 内可装卸能力，例如 Tool、Skill、MCP、Subagent、Memory、Hook、UI | 只拥有执行期配置/状态，不拥有正式产品账本 |

Product Extension 可以附带一个薄 DSH bundle，把自己的 Role Profile、Skill、命令、查询工具和 UI 接入 DSH；该 bundle 被禁用或 DSH 被替换时，Extension 的 Run、Artifact、Forecast、Outcome、Evaluation 和历史迁移必须仍然完整。

DSH Plugin/MCP/Provider 通过 `DshCapabilityAdapter -> CapabilityManifest` 映射到下面的 Capability Plugin。不是所有 Product Extension 都要发布为 DSH npm 插件，也不能把 DSH Session 当成 Extension 数据库。

## 4. 四级产品插拔模型

### Product Extension

定义产品结果和闭环。示例：`decision`、`presentation`。

### Domain Pack

定义业务方法和规则。示例：`crypto_macro.v1`、`us_equity.v1`。

### Role Profile

定义一次 Agent 职责与允许调用的能力。Profile 是组合，不默认是代码插件。

### Capability Plugin

定义一个原子可执行能力。实现可以来自 DSH、MCP、Python adapter 或外部 API。

组合示例：

```text
crypto_macro trader profile
  -> policy/data delta
  -> expectation pricing
  -> macro transmission
  -> derivatives crowding
  -> counter thesis
  -> data quality
  -> web/official/market capabilities

presentation editor profile
  -> research/citation
  -> outline
  -> slide composition
  -> image generation/search
  -> render/overflow check
  -> file capabilities
```

两个 Profile 共享 DSH Harness、Task/Run/Artifact/Evaluation 和部分搜索/文件 Capability，但不共享业务输出 schema 或 Gate。

## 5. 推荐的声明式契约

以下只是字段所有权示例；正式实现必须先进入 canonical schema/codegen，不能复制 YAML/Python/TypeScript 三份真相。

```yaml
product_extension:
  id: presentation.v1
  task_schema_ref: presentation-task.v1
  artifact_schema_ref: deck-artifact.v1
  evaluation_policy_ref: deck-quality.v1

domain_pack:
  id: crypto_macro.v1
  product_extension_ref: decision.v1
  doctrine_ref: crypto-macro-doctrine.v1
  evidence_policy_ref: crypto-macro-evidence.v1
  gate_policy_ref: crypto-macro-gate.v1
  role_profile_refs: [macro-lead.v1, data-quality.v1, counter-thesis.v1]

role_profile:
  id: macro-lead.v1
  required_capabilities: [expectation_pricing, macro_transmission]
  allowed_tools: [web.search, web.fetch, official.fed, macro.market]
  output_schema_ref: specialist-result.v1
  evaluation_policy_ref: causal-evidence-rubric.v1

capability:
  id: macro.market.v1
  implementation: dsh-plugin-or-mcp-ref
  input_schema_ref: macro-market-query.v1
  output_schema_ref: macro-market-snapshot.v1
  permissions: [network.read]
  replay_policy: archive_required
  secret_policy: adapter_only
```

## 6. 插件准入生命周期

```text
discover
  -> license/security review
  -> canonical capability mapping
  -> contract test
  -> replay
  -> shadow
  -> owner enable
  -> health monitoring
  -> retire/rollback
```

DSH Marketplace 负责发现，不负责本产品的信任。一个插件如果不能回答 schema、权限、网络范围、PIT、成本、timeout、失败码、回放和卸载后果，只能作为 owner 的临时 DSH 工具，不能进入正式链。

## 7. 角色新增规则

新增角色按以下顺序判断：

1. 只是 persona、工具白名单、输出 schema 或评测不同：新增 Role Profile。
2. 需要新的业务证据或判断规则：修改/新增 Domain Pack，经 replay/holdout。
3. 需要新原子工具：新增 Capability adapter/plugin，经准入生命周期。
4. 需要新的产物和结果闭环：新增 Product Extension。
5. 需要改变 Run、Evidence、Asset、Evaluation 等跨产品语义：才修改 Platform Core，并先写 ADR。

禁止为每个角色复制 Graph、数据库表、DTO、Provider client 或前端应用。

## 8. 个人资产到企业资产

首期仍是单 owner，不引入用户系统。所有资产先通过稳定 ID、版本、hash、来源和 lineage 实现可迁移；未来成立企业时再增加 `organization_id/workspace_id/owner_scope`，不改变资产内容。

企业化前必须补的不是“更多角色”，而是：

- 数据与插件许可证台账；
- Secret 和网络权限隔离；
- 前瞻数据集与结果标签；
- 版本晋升、回滚和审计责任；
- 备份、恢复、保留和导出；
- 资产可见性和 owner/organization scope；
- 成本、SLA 和安全事件记录。

## 9. 自主进化边界

```text
Run + DSH trace + Outcome + Owner feedback
  -> candidate Experience / FailurePattern
  -> replay
  -> holdout
  -> prospective shadow
  -> owner review
  -> new Pack/Profile/Skill/Policy version
  -> promotion or rollback
```

Agent 可以提出候选，不能直接修改正式 Pack、Skill、Gate、权限或 active pointer。没有 Outcome/Evaluation lineage 的“经验”不能晋升为正式资产。
