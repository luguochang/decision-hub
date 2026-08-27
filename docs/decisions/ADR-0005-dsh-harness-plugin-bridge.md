# ADR-0005 DSH Harness 与插件生态桥接边界

日期：2026-08-27
状态：`accepted`（owner 于 2026-08-27 随 R2 Stage Gate 接受）
关联阶段：[R2 Decision Workbench 与自主进化](../stages/R2_DECISION_WORKBENCH_EVOLUTION.md)

## 决策

Decision Hub 不把 DeepSeek Harness（DSH）作为业务核心或唯一运行时，而是把它作为可替换的 Workbench/Harness 生态，通过公开适配边界复用其插件、Skill、MCP、Agent Loop、Subagent、Session 和 UI 能力：

- DSH 交互和插件能力通过 `ResearchWorkbenchPort`、Core MCP 和 `ResearchMemo/Feedback` DTO 接入。
- 有明确输入输出、权限和时间语义的 DSH 插件，通过 `CapabilityManifest` + `DshCapabilityAdapter` 转换为 Core Tool/Source/Provider Port。
- DSH Agent Loop 只有在能够实现统一 `AgentRuntime -> AgentResult` 契约时，才作为 `DshAgentRuntime` candidate 进入 replay/holdout/shadow。
- Product Kernel 继续独立拥有 Event、Evidence、Snapshot、Run、Artifact、Forecast、Outcome、Evaluation、Asset、Version、Promotion 和 Rollback。
- DSH Session、Cordis Context、插件内部状态、原始 Harness JSON 和 UI 状态不能成为业务账本、PIT 或发布依据。
- 插件默认 deny-by-default，必须经过来源/许可证/安全/权限/契约/回放审计并由 owner enable；禁止自动安装、自动晋级或自动修改 Gate/权限。

## 背景与证据

截至 2026-08-27 的公开资料：

1. [官方 DSH README](https://github.com/deepseek-ai/deepseek-harness) 将 DSH 定义为 DeepSeek AI 的开源 Agent Harness，采用 “everything is a plugin”，提供 `web`/`headless` 入口，并明确处于 `developer preview`、可能发生兼容性破坏。
2. 官方 `docs/architecture.md` 说明 Cordis 插件树覆盖模型、工具、Session、Agent Loop、Subagent、Jobs、Web、Sandbox、Approval、Telemetry、Workflow 和 UI 等 capability seam；插件通过 typed service、事件和可逆 effect 组合。
3. [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin) 的公开 README 说明插件以 `dsh.bundle` manifest 分发并可用 `dsh plugin add` 安装；其 count endpoint 在核对时返回 `2322`。
4. [dsh-market](https://github.com/dsh-market/dsh-market) 提供 DSH 内置可视化插件市场；生态规模证明了可复用机会，但不证明每个插件的安全、许可证、维护或业务效果。

## 候选方案

### A. 将 DSH 直接作为 Decision Hub Core

否决。它会让业务账本、PIT、Gate、资产和版本依赖 developer-preview 的内部 schema、Session 和插件生命周期；替换 DSH 时可能需要迁移产品事实。

### B. 完全忽略 DSH，用 LangGraph 重写所有 Harness 能力

否决。它会重复实现工具、Skill、会话、插件和交互能力，失去社区生态，并把 Decision Hub 变成另一个封闭 Harness。

### C. 采用本 ADR 的桥接方案

推荐。DSH 保留其擅长的交互和插件生态，Core 保留产品事实、评测和发布权；通过适配器和契约测试控制版本、权限和可迁移性。

## 复用边界

| DSH 能力 | Decision Hub 接入 | 约束 |
|---|---|---|
| Web/Workbench UI | 外部研究入口 | 只能调用 Core Query/Command，不直读 SQL |
| Skill/MCP/Tools | `DshCapabilityAdapter` | 输入输出 schema、权限、PIT、timeout、cost、失败码必须可审计 |
| Session/Agent events | telemetry/研究引用 | 不作为 Core 账本；只存必要的外部引用/hash |
| Agent Loop/Subagent | `DshAgentRuntime` candidate | 同一 AgentRuntime contract；只做 replay/holdout/shadow |
| DSH bundle/profile | 外部安装描述 | 不能直接替代 Core CapabilityManifest；需审核/转换 |
| DSH Marketplace | 插件发现来源 | 不能自动安装到正式链；需要 owner enable |

## 后果

正面后果：保留 DSH 的插件生态和 Harness 能力；不重复实现 Agent Loop；未来可接入 DSH、Pi、Codex 或自建 Web；核心资产不会因 Harness 替换而迁移。

负面后果：需要维护一个适配器、能力清单和契约测试；不能“一键把所有 DSH 插件变成正式产品能力”；部分 DSH-only 插件只能留在研究工作台。

## 迁移、回滚和验证

- DSH 版本升级先运行 adapter contract suite、capability canary 和固定 replay；失败时保留旧 adapter/active pointer。
- 插件卸载或禁用只撤销其 adapter effect，不删除 Event/Evidence/Snapshot/Run/Asset/Promotion 历史。
- `DshAgentRuntime` 候选未达到 PromotionGate 时，正式 active pointer 保持 LangGraph-native。
- 任何跨边界 schema 变化先改 canonical contract、生成镜像和必要 ADR；不能把 DSH 内部 JSON 直接写入 Core。

## 受影响契约与测试

- 提议新增：`ResearchWorkbenchPort`、`CapabilityManifest`、`DshCapabilityAdapter`、可选 `DshAgentRuntime` contract。
- 必须覆盖：插件准入、许可证/权限 deny、schema invalid、PIT leakage、timeout/429、tool denied、卸载/回滚、DSH 未启动时 Core 可用、candidate 与 baseline 公平 replay。
- 本 ADR 已被 owner 接受；R2-01 负责按本边界锁定并生成契约，仍不得因此自动安装社区插件或绕过准入审计。

## Owner 确认

owner 已随 R2 Stage Gate 接受本 ADR。实现必须从 deny-by-default 的 bridge 和 contract suite 开始；本决定不等于授权安装任意社区插件。
