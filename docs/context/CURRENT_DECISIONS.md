# 当前有效决策索引

更新时间：2026-09-01
用途：只列当前仍有效的结论和权威引用。决策正文只写在 ADR/架构/schema 中，不在这里重复推理历史。

## Accepted

| 结论 | 权威来源 |
|---|---|
| Core/Harness 隔离，Agent 不拥有 Gate/账本 | [产品架构基线](../../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md)、[ADR-0005](../decisions/ADR-0005-dsh-harness-plugin-bridge.md) |
| DSH 插件只有在结果要进入正式 Evidence/Gate 时才经 CapabilityManifest 审计；DSH-only UI/Skill/人工工具仍由 DSH 管理 | [ADR-0005](../decisions/ADR-0005-dsh-harness-plugin-bridge.md)、[ADR-0013](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md) |
| Kernel 不 import LangGraph/DSH/Pi/Provider | [ADR-0006](../decisions/ADR-0006-kernel-orchestration-boundary-alignment.md) |
| Live Observation 使用 durable job/lease/heartbeat，候选只进入 owner review | [ADR-0007](../decisions/ADR-0007-live-observation-runtime.md) |
| canonical schema/codegen、PIT、确定性 Gate、SDD/BDD/TDD/ADR | [治理规范](../engineering/DEVELOPMENT_GOVERNANCE.md) |
| DSH Python SDK + 受限 `decision-research` profile 是首个真实 Research Harness candidate；LangGraph 只保留产品外层生命周期和 Evidence round | [ADR-0008](../decisions/ADR-0008-agentic-research-runtime.md)、[R2-R](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md) |
| 通用底座采用 Platform Core/Product Extension/Domain Pack/Role Profile/Capability Plugin | [ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md) |
| 主体产品是主动发现、耐久任务、补证循环和人可读报告组成的 Research Agent；聊天只是补充入口 | [研究智能体主体产品规格](../product/RESEARCH_AGENT_PRODUCT_SPEC.md) |
| 插件采用 Product Extension 与 DSH Native Plugin 双层模型 | [资产与扩展模型](../platform/ASSET_AND_EXTENSION_MODEL.md)、[ADR-0009](../decisions/ADR-0009-product-platform-extension-boundary.md) |
| `crypto-macro-decision` 拆为 Doctrine/Evidence/Profile/Capability/Gate/Eval/Fixture | [Crypto Macro Domain Pack](../domains/crypto_macro/README.md) |
| R2-R DSH candidate 固定 `deepseek-harness-sdk==0.1.1rc1`、bundled runtime `0.0.1` 与 `decision-research.v1:1d4ce1f40ab265e4` restricted profile | [R2-R-01](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)、[DSH adapter README](../../packages/runtime_adapters/dsh_runtime/README.md) |
| DSH 模型只输出研究语义候选；Session/Tool/Evidence/Coverage/时间戳/计数由 adapter 与确定性代码组装 | [ADR-0010](../decisions/ADR-0010-model-semantics-runtime-ledger-boundary.md) |
| G1/G2 的 server-owned PIT、ErrorProvenance、并行部分成功和六类事实 manifest/replay 边界 | [ADR-0011](../decisions/ADR-0011-research-reliability-fact-boundary.md) |
| DSH 是唯一研究执行 Harness，Hub 保留触发、可信边界、业务账本、评测和管理后台 | [ADR-0012](../decisions/ADR-0012-dsh-first-product-rebaseline.md) |
| DSH Web 是 Agent 交互主壳；Hub 以官方 Host/Client plugin 接入，Decision Desk 收敛为管理后台 | [ADR-0013](../decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md) |
| DSH-NATIVE-CORE 工程验收已完成；固定官方上游闭包、原生插件、durable bridge 和 replay E2E 均已留证；该阶段不再新增任务，后续产品收口由 PRODUCT-CLOSEOUT-01 承担 | [DSH Native Web Product Core](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)、[完成实施方案](../stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)、[PRODUCT-CLOSEOUT-01](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md) |
| PRODUCT-CLOSEOUT-01 的 E1/E2-R/E2-L 已通过；当前可进入单 owner、单机、只读的 `research_only` 观察期，但 Fixed 仍 active、DSH 仍 candidate/shadow | [产品交付控制书](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)、[E2-L 真实产品验收](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md)、[PRODUCT-CLOSEOUT-01](../stages/PRODUCT_CLOSEOUT_01_DSH_NATIVE_TRADER_PILOT.md) |
| 产品执行与最终验收总表汇总当前产品视图、两层循环、插件边界、E2-L/E3 checklist 和停止门；不替代 schema、ADR、Stage Charter 或历史证据 | [产品执行与最终验收总表](../product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md) |
| 当前交付周期的唯一执行入口、E2-L checklist、实现状态和最终停止门 | [产品交付控制书与最终验收包](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md) |
| Pilot Ready v2 的 PR-00..08、Search attribution、attestation 安全降级、Tool 预算、受信 Session 身份和 accepted-before-prompt 已完成；该任务书只保留历史证据，不再是执行入口 | [Pilot Ready 最终执行与验收书 v2](../product/PRODUCT_PILOT_READY_FINAL_EXECUTION_PLAN_2026-09-01.md)、[ADR-0019](../decisions/ADR-0019-durable-tool-budget-and-session-readiness.md)、[ADR-0020](../decisions/ADR-0020-trusted-dsh-tool-session-context.md)、[E2-L 真实产品验收](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md) |
| Search `action.sources` 仅为发现元数据；只有 `url_citation` exact span 可生成 `search_derived` Evidence，同一 claim/URL alias/同 publisher 不虚增独立来源 | [ADR-0017](../decisions/ADR-0017-search-evidence-citation-attribution.md) |
| E2-L 后唯一下一阶段是至少 14 天或 20 个高影响事件的 E3 前瞻观察；窗口结束只能 `promote / retain_baseline / stop`，观察前不扩功能、不切 active pointer | [产品交付控制书](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)、[E2-L 真实产品验收](../evaluations/E2L_LIVE_PRODUCT_ACCEPTANCE_2026-09-01.md) |

## Proposed / Owner Gate Pending

| 结论 | 提案来源 | 接受前限制 |
|---|---|---|
| 本次真实事件是否减少 owner 的人工查证时间 | [R2-R-06E Owner usefulness](../evaluations/R2-R-06E_RUNTIME_DECISION.md) | 只能由 owner 根据实际页面体验填写，不能由 LLM/自动脚本代填 |
| 当前报告的证据、反方和停止原因是否足够可解释 | [R2-R-06E Owner usefulness](../evaluations/R2-R-06E_RUNTIME_DECISION.md) | 只有 owner 复核后才能作为产品价值证据 |
| E3 观察结束后 DSH candidate 是否 promotion 为 active runtime | [产品交付控制书](../product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)、[R2-R-06E 决策包](../evaluations/R2-R-06E_RUNTIME_DECISION.md) | 必须有前瞻覆盖、延迟、失败率、成本、人工时间、usefulness、Outcome/Brier/方向/净收益证据；当前继续 `retain_baseline` |

R2-R-06E 已完成对照和真实事件验收，当前报告结论为 `retain_baseline`；这不是将 DSH
promotion 改为 accepted，而是保留 Fixed active、等待 owner usefulness 和后续候选 gate。

## Supersession 规则

- 新 ADR 接受后，在被替代 ADR 顶部标记 `superseded by ADR-NNNN`；不删除历史。
- 本索引只保留当前结论；过期解释从本页移除并保留 ADR 历史链接。
- 聊天、研究文档和 `docs/context/` 不能单独把 proposed 改为 accepted。
