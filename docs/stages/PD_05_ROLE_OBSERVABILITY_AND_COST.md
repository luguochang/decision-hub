# PD-05 Role、事实准备度、成本与可观测性

状态：`completed / engineering and runtime evidence verified`  
日期：2026-09-05（Asia/Shanghai）  
上级方案：[`PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md`](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)

## 1. 阶段目标

让 owner 不看日志、不读大段 JSON，即可回答五个问题：

1. 本次运行用了哪个 Domain Pack、交易员 Role、DSH Runtime、Capability 和 Gate 版本？
2. 每个 requirement 已满足、仍缺、被拒绝、过期还是没有 baseline？
3. primary/fallback 分别尝试了什么，成功或失败的原因是什么？
4. 模型、Search、typed provider 与数据订阅花费多少，哪些成本仍 unknown？
5. DSH trajectory、Hub ledger 与 LoongSuite trace 如何从同一个 Run 追溯？

## 2. 非目标与硬边界

- 不复制 DSH 的 Chat、Session JSONL 或完整 Trajectory 到 Hub。
- 不让 LoongSuite 成为业务账本、Gate 或业务状态真源。
- 不自研 tracing SDK、计费系统或日志平台。
- 不把 unknown/partial 成本显示成 `$0`，也不根据字符串长度猜 token。
- 不默认向 telemetry exporter 发送 Prompt、正文、密钥或完整 Evidence。
- 不新增 Provider DTO；继续消费 canonical capability result、attempt、RunCall 和 DSH link。

## 3. 现有复用点

| 能力 | 现有资产 | 本阶段处理 |
|---|---|---|
| Pack/Role | `ResearchPlan`、profile YAML | 投影版本/hash，不复制定义 |
| Runtime/Provider | Run、RunCall、ResearchResult、DSH session link | 聚合 lineage |
| Requirement coverage | `CoverageAssessment`、Evidence/FactStore | 转换为人类可读 readiness |
| Capability attempts | Research rounds/tool reservations/provider attempts | 聚合尝试与稳定错误分类 |
| 模型 usage | RunCall/DSH result usage | 汇总 token 与 known/partial/unknown |
| Tool/provider cost | capability result/attempt | 分项归集，禁止假 0 |
| 技术 trace | `@loongsuite/dsh-plugin`、`DshSessionLink.trace_ref` | 只存/展示 TelemetryRef |
| 页面 | DSH extension + Decision Desk Research | 分工展示，不新增前端 |

## 4. Canonical 产品投影

在 `research_product_view.schema.yaml` 增加：

- `ResearchVersionLineage`：pack、role、runtime、capability manifest、gate 与 source registry 版本；
- `RequirementReadinessItem`：requirement、hard/soft、状态、已满足字段、缺失字段、窗口、原因与下一能力；
- `SourceAttemptView`：source/provider/capability/route role、状态、latency、error、cost；
- `ResearchCostBreakdown`：model/search/provider/subscription/total、currency、status、unknown components；
- `ResearchObservabilityView`：以上内容及 `trajectory_ref/telemetry_ref/ledger_ref`。

成本状态固定为：

```text
known    所有纳入项有可审计金额；total 可显示
partial  至少一项金额已知且至少一项 unknown；显示已知小计 + unknown 列表
unknown  没有可审计金额；不显示 total=0
```

免费 Provider 必须通过版本化 cost policy 明确 `known_free`，才能记为 0；`null` 不是 0。

## 5. 代码落点

```text
contracts/schemas/research_product_view.schema.yaml
packages/query_views/research/service.py
packages/kernel/.../research_observability.py
packages/runtime_adapters/dsh_runtime/result_mapper.py
packages/runtime_adapters/dsh_runtime/tool_result_attestation.py
apps/hub_api/main.py
apps/research_mcp/main.py
extensions/dsh/decision-hub/src/client/index.js
apps/decision-desk/src/research/ResearchPage.tsx
```

Query View 可以从现有 normalized records 和 research result JSON 聚合。不得为了 UI 方便新增
重复业务表；若 DSH token usage 尚未进入 canonical RunCall，优先在 result mapper 的现有写入边界
补齐，而不是前端解析 DSH JSONL。

## 6. 页面职责

### DSH Web

- 展示“交易员角色”而不是内部 profile 文件名；同时提供版本引用；
- 报告上方显示事实准备度摘要、关键缺口、下一复查和成本状态；
- Source attempts 默认按 requirement 折叠，错误用人类可读标签；
- 可跳转 DSH trajectory 和 LoongSuite trace；不展示 Hub 表结构或 raw payload。

### Decision Desk

- 跨 Run 比较来源健康、Provider fallback、成本、Outcome/Evaluation；
- 允许展开稳定 IDs/hash 供审计；
- 不复制 DSH 会话、输入框、完整 Agent trajectory。

## 7. 错误与真实性语义

- `stale`、`semantic_mismatch`、`baseline_unavailable`、`provider_timeout`、`rate_limited`、
  `domain_denied`、`budget_exhausted` 必须独立，不合并为“证据不足”。
- Search locator、approved body、typed market fact 使用不同 kind 与标签。
- cost/usage 缺失是 `unknown`，不能被 Python/TypeScript 默认值转成 0。
- telemetry exporter 不可用不影响业务 Run，页面显示 trace unavailable；不得把业务标失败。
- LoongSuite ref 不可访问时保留 Hub/DSH canonical lineage。

## 8. BDD

```gherkin
Scenario: 成本只有部分已知
  Given DSH 有 token usage 和模型价格
  And data subscription allocation unknown
  When 页面显示 Run cost
  Then status 是 partial
  And 显示已知小计与 unknown component
  And 不显示 total 为 0

Scenario: 免费 Provider 有明确政策
  Given provider route 的 cost policy 为 versioned known_free
  When adapter 返回 cost_usd=0
  Then 该项状态为 known
  And 保留 cost policy ref

Scenario: requirement 被语义 Gate 拒绝
  Given Search locator 提到美债收益率
  But 没有 intraday rates FactEnvelope
  When owner 查看 readiness
  Then macro_transmission 显示 metric/window mismatch
  And 页面指向下一 typed capability
  And 不显示为已满足

Scenario: Telemetry exporter 不可用
  Given LoongSuite/OTLP 无法连接
  When DSH Run 完成
  Then Artifact、Gate 和 Inbox 不受影响
  And telemetry status 为 unavailable
```

## 9. TDD 与验收

- Contract：完整/partial/unknown 成本、readiness 状态、版本字段兼容。
- Query：混合成功/拒绝/stale attempts、known/unknown cost、trace ref 有无。
- Runtime：DSH usage 和 capability attempt 不重复计费，secret/body 不进 telemetry。
- DSH Plugin：Role、readiness、成本、trace 链接的人类可读渲染。
- Desk：跨 Run 管理投影，raw JSON 默认隐藏，长文本/移动端无溢出。
- E2E：同一 Run 的 DSH、Hub API、Desk 三处关键状态一致。

## 10. 退出门

- [x] 每个 Run 可见 Pack/Role/Runtime/Capability/Gate/Registry 版本；
- [x] requirement readiness 可读且语义拒绝原因准确；
- [x] source/provider attempts、fallback、错误和延迟可追溯；
- [x] model/search/provider/subscription 成本为 known/partial/unknown；
- [x] unknown 不显示为 0；
- [x] LoongSuite 只作 TelemetryRef，失败不影响业务；
- [x] DSH 与 Desk 分工清晰，无重复 Chat/Trajectory；
- [x] 全量测试、静态、codegen、构建和浏览器验收通过。

完成证据统一见 [PD 实施与自测执行日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
页面验收显示 Role/Pack/Runtime/Gate、事实准备度、Provider 尝试、明确错误和成本状态；缺失订阅
或上游金额时为 `partial/unknown`，不会显示伪 `$0`。LoongSuite 仍是可选技术 Telemetry，Hub
账本和 DSH Trajectory 保持各自唯一所有权。

通过本卡只证明运行可解释与成本可审计，不证明研究有预测价值。下一阶段为 PD-06。
