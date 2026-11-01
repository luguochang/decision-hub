# 原永续研究保护与观望条件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 新研究沿原字段给出可审查的保护、到期与观望换选条件，补 Requirements §4.4 的实际内容缺口。

**Architecture:** 原Python method prompt与原Host独审persona使用同一内容要求。沿用原assumptions/local_id/ref、prepare→review→finalize、原报告和发布路径；不增UI/API/schema，不改任何金融授权字段。独审依实际原件拒绝遗漏，验收统计另按六项证据判断。

**Tech Stack:** Python 3.12、现有Pydantic/pytest；原TypeScript/Vitest工具链，不新增依赖。

**Spec:** Main docs/v3/01_REQUIREMENTS.md §4、Task Master T303/T804；actual07 source_json 缺保护与明确hold换选条件的实证；当前 AGENTS §53。

## Global Constraints

- 14自然日≥90%、最终P50≤15分钟/P95≤30分钟、PAPER≥10笔及完整退出恢复、四周评分等最终范围全部保留。
- 07与其他历史原件只读，不新模型/HTTP/服务；Main/正式profile/8047/3097/data不修改。
- 实施只在D:/DecisionHub-legacy-incremental；当前profile逐字基线72eb40d，Main profile SHA6b687ed7…2462、reviewer SHA2825e4b7…5c02。
- 不伪造价格、资金费、校准、成交或source时间；止损/止盈是假设条件，不是已成交价格。
- 原单位比较仍是终点收益；新增保护说明不冒首次触达策略EV。保留hold选择，不为PAPER数量强选open。
- 原financial_qualified/execution_authorized/research_complete真义保留；不把V5固定degraded改名充合格。
- 旧profile436行是当前Main既存基线，本切片不重构无关代码；新增helper单一职责，必要静态检查只声称实际范围。

## Review Focus

1. 缺真实永续价格或tick：保持未知并说明缺口，不自动填默认幅度。
2. hold：有继续hold、考虑多/空与判断失效条件；条件触发的是重核/后继研究，不是自动下单。
3. 多/空保护公式：方向与取整语义正确，数值只能引用冻结价格、tick及显式有理由假设。
4. monitor-only条件：现有codec可读；不挂普通open候选condition_refs，避免触原publisher unsupported分支。
5. 原审核/报告：同包保护说明原样保留；旧未知资金费、未校准概率与不受保护的单位比较限制不丢失。

## Task 1 — 同包保护内容与独审

**Files:**
- Modify packages/runtime_adapters/dsh_runtime/perpetual_profile.py
- Modify extensions/dsh/decision-hub/src/reviewer-tool.ts
- Test: 复用/新增上述两组件最近的focused tests；必要小support/helper需先报告精确路径，不能扩大旧断言修改范围。

**Interfaces:** Python原prompt builders/TS reviewer persona签名不变；沿用assumptions role=exit_policy/entry_condition/basis，candidate.exit_ref/basis_refs，analysis.thesis与主反链confirmation/invalidation。

- [ ] Step 1: 写反例，证明现有method/reviewer没有要求完整价格来源、保护/到期计划及明确hold换选内容；用完整原模型草稿验证既有引用接缝，不能只测新字符串自身。
- [ ] Step 2: 跑focused测试并保存真实RED，必须是对应业务断言失败，非环境/导入失败。
- [ ] Step 3: 最小增强原prompt与独审要求；禁止示例行情数值或默认d；方案可用P_ref、tick、显式d_SL/d_TP与floor_tick/ceil_tick表达。完整说明条件适用域、入场偏离后的重核和unknown项。
- [ ] Step 4: 跑focused GREEN与原prepare双端引用/monitor codec必要回归；旧完整包和数值比较断言不改。
- [ ] Step 5: 对owned Python/TS做现有定向静态检查；结果按旧模块基线差异如实记录。非作者review后，由Root精确本地提交；不自行接Main或部署。

此切片只证明生产规则与引用流改善；真实新研究内容、重启后可读及最终合格率仍需后续实际验收。
