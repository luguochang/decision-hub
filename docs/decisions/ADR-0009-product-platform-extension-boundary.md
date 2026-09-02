# ADR-0009 通用产品平台、领域扩展与角色/能力插件边界

日期：2026-08-29
状态：`accepted`（owner 于 2026-08-29 授权 R2-R）
关联文档：[平台基线](../platform/PLATFORM_BASELINE.md)、[资产与扩展模型](../platform/ASSET_AND_EXTENSION_MODEL.md)、[R2-R Stage Charter](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)

## 决策

Decision Hub 采用以下长期所有权模型：

```text
Platform Core
  -> Product Extension
      -> Domain Pack
          -> Role Profile
              -> Capability Plugin

ResearchHarnessRuntime 是横向执行端口；R2-R 首个 candidate 为 DSH Python SDK + 受限 `decision-research` profile。
```

- Platform Core 只拥有跨产品的 Task/Run/Evidence/Artifact/Asset/Evaluation/Version/Promotion 和运维语义。
- `decision` Product Extension 拥有 Forecast、Outcome、Gate、方向、周期和市场评测。
- 未来 `presentation` Extension 拥有 Deck/Slide/Render/Citation 和展示质量评测，不继承金融 Forecast/Gate。
- Domain Pack 拥有领域 doctrine、evidence requirements、source priority、Gate policy、budget 和 evaluation rubric。
- Role Profile 是 persona、required capabilities、tool allowlist、结构化输出和评测策略的声明式组合；不是默认代码插件。
- Capability Plugin 是原子执行能力。DSH plugin/MCP/Provider 必须映射到自有 canonical `CapabilityManifest` 后才能进入正式链。
- “插件”采用双层定义：Product Extension 是可独立于 DSH 存在的产品插件式模块；DSH Native Plugin 是 Harness 内可装卸的 Tool/Skill/MCP/Subagent/Memory/Hook/UI/Provider。前者可以附带薄 DSH bundle，后者不能成为前者的业务账本。
- DSH 拥有 Agent Session/Step/Tool/Subagent/Skill/Plugin/JSONL；不能拥有 Platform 账本、PIT、Gate 或 Promotion。
- 不进行一次性全仓重写。现有金融对象保持兼容并逐步归入 `decision/crypto_macro` 边界，第二 Product Extension 出现时再提取被证明共享的 Core。

## 背景

当前代码已经形成可靠的事件决策纵向链和评测/演进资产，但 `crypto_macro.v1`、Forecast、Outcome、Brier 与 30m/24h/72h 仍位于当前 Kernel 和前端默认路径。它是可演进的第一个领域产品，不是已经完成的通用平台。

同时 DSH 已提供完整 Agent Loop、Session、Skill、插件、Web、Subagent、Jobs 和 Python SDK。若完全用 LangGraph 重建，会重复造 Harness；若把所有产品事实交给 DSH，则会绑定 developer-preview 内部 schema，难以升级、替换和做企业资产治理。

## 候选方案

### A. DSH 直接成为产品 Core

否决。Session/插件树不是 Forecast/Outcome/Evaluation 业务事实；DSH 当前 developer preview 且官方声明未完成安全审计。

### B. LangGraph 自建完整 Harness 和插件平台

否决。会重复实现 DSH 已有的模型/工具循环、Session、Subagent、Skill、插件和上下文能力。

### C. 继续把所有未来业务塞进 `crypto_macro` Kernel

否决。PPT 等产品会被迫依赖方向、周期和 Brier，造成字段污染和后续重写。

### D. 先建设抽象齐全的大平台再做业务

否决。没有第二调用方的抽象无法验证，会延迟 R2-R 的真实效果并制造空目录/接口。

### E. 分层所有权 + 渐进提取

选择。先在 R2-R 中让 `crypto_macro` 按新边界使用 DSH Harness；稳定后用最小 `presentation` Extension 验证通用层，只提取两个真实调用方共同需要的能力。

## 后果

正面：

- 能复用 DSH 生态而不失去自己的产品和企业资产；
- 交易员、A 股、美股和 PPT 可以共享运行/资产能力但隔离业务契约；
- 新角色多数只增加 Profile/Pack，不复制 Graph、数据库或 API；
- 换 DSH、Pi、Codex 或模型时不迁移业务历史；
- 第二领域成为架构测试，而不是再重写一套系统。

负面：

- 需要维护 DSH adapter、Capability 准入和两层 trace/ledger 引用；
- 当前金融代码需要渐进归位，短期会存在明确标记的 legacy 路径；
- 只有第二 Extension 通过后才能证明平台抽象正确；
- DSH 版本、安全和插件兼容需要持续 contract/replay canary。

## 迁移与回滚

1. 历史 migration、Run、Artifact、Forecast、Outcome 不改写。
2. fixed graph 冻结为 baseline；DSH 先 candidate/shadow，不自动切 pointer。
3. R2-R-00 只锁 canonical contract、依赖规则和失败测试，不搬迁全仓。
4. 新增金融行为只能进入 `crypto_macro` Pack/decision Extension 的目标边界，不能继续扩散到 Platform-common。
5. DSH adapter 失败时回滚 pointer/worker/profile，不删除 Ledger 或 Session trace ref。
6. 第二 Product Extension 前不引入多用户、微服务或公共插件市场。

## 受影响契约与测试

- R2-R 提议新增 `ResearchHarnessRuntime`、`ProductExtensionManifest`、`DomainPackManifest`、`RoleProfile`、增强 `CapabilityManifest`。
- Product Extension 附带 DSH bundle 时，contract suite 必须证明卸载 bundle 或替换 Harness 后，产品历史与 Query API 仍完整。
- 架构测试必须拒绝 Platform Core import DSH/LangGraph/具体 Domain Pack。
- contract suite 必须证明一个 Capability 可由 fake/replay/DSH/MCP 实现而不改变调用契约。
- `crypto_macro` 必须证明 Skill 被拆为 Doctrine/Evidence/Gate/Profile/Eval，而不是单个 Prompt。
- 后续 `presentation` contract spike 必须在不修改 Forecast/Gate schema 的情况下创建 Deck Artifact 和领域 Evaluation。

## Owner Gate

Owner 已于 2026-08-29 接受本 ADR。授权仅覆盖按 R2-R Stage Charter 从 `R2-R-00` 开始的渐进实现，不授权一次性目录迁移、PPT 实现、任意社区插件安装或 active pointer 变更。
