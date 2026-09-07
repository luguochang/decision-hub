# ADR-0024：语义事实与事件窗口边界

日期：2026-09-04  
状态：accepted

## 决策

1. 新增 canonical `FactEnvelope`，以指标族、instrument/venue、value/unit、事件窗口、三时间戳、来源独立组和 payload lineage 表达可用于确定性金融判断的结构化事实。
2. `research_task` 必须显式携带 `requirement_id`；Plan、Tool、Evidence、attempt 和 Gap 禁止再按数组位置推断关联。
3. Evidence 准入分为 Generic Evidence Gate、Domain Semantic Gate、Product Publish Gate。Kernel 保持通用；`crypto_macro` 以声明式 manifest 拥有字段、单位、指标族、事件 offset、venue 和迟到策略。
4. 可预知事件通过 durable watch 和 sampler 保存事件前后 Fact；迟到或突发事件没有基线时必须标记 `no_baseline/retrospective_only`，不能用当前值伪造历史窗口。
5. Search 继续只发现 locator；精确行情由 typed provider/已保存 Fact 提供。历史 Evidence/Run 不回写，旧 payload 通过兼容解析继续可读。

## 背景

真实 G2-AF Run 已证明 DSH Search 和三轮 Agent Loop 可运行，但当前通用 Gate 只检查 requirement id、authority、freshness、来源数和冲突，无法阻止 BTC funding/OI 被错误标为 Fed expectation pricing，也无法证明事实属于 `T-30m/T+30m`。继续增加 Prompt、搜索轮次或放宽 Gate 会产生看似更高、实际不可解释的 coverage。

## 候选方案与否决项

- 采用 canonical Fact + Domain manifest validator：供应商和领域隔离，能回放和 codegen。
- 否决把所有金融字段直接加到 `EvidenceCandidate`：会污染通用 Core，并使第二领域继承无关字段。
- 否决只解析 `excerpt` JSON：字符串不是稳定跨模块契约，无法做字段/单位/窗口 Gate。
- 否决让 LLM Judge 判断事实够不够：不可确定、不可回放、可能放行语义替代。
- 否决重写 DSH/LangGraph：问题位于事实契约和数据能力，不在 Harness Loop。

## 后果

- 跨模块 schema 和 migration 增加，但 Provider 可以在不改 Core/Graph/UI 契约的情况下替换。
- 旧 Run 的 coverage 保留为历史计算；新语义 coverage 必须带 policy/version，不能静默回写。
- 30m 价值需要提前采集或可验证历史分钟数据；零成本搜索不能替代该成本。
- Domain Semantic Gate 失败必须投影为具体 reason code，不能统一显示 `insufficient_sources`。

## 迁移与回滚

以新的 Alembic revision 增量建 Fact/Watch 表；不修改 0001..0027。回滚可停止新 sampler/validator 并保留新增表和历史记录，不删除数据。新字段通过 schema version/additive compatibility 引入；旧 archived payload 使用显式 legacy mapper。

## 受影响契约与测试

- `agentic_research.schema.yaml`、新 `research_fact.schema.yaml`、source/pack policy。
- Contract codegen、semantic-substitution、task reorder/parallel、PIT/late-event、migration upgrade、Query/View 和浏览器 BDD。
- 详细任务与退出门见 [PD Stage Charter](../stages/PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)。
