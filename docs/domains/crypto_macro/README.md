# Crypto Macro Domain Pack 设计

日期：2026-08-29
状态：`accepted / R2-R-00..06E candidate path implemented / Fixed active`
产品扩展：`decision.v1`
首个 Pack：`crypto_macro.v1`

## 1. 目的

本文件把现有 `crypto-macro-decision` Skill 中已经验证有价值的方法，拆成可以测试、版本化、组合和替换的领域资产。它不是新的超长 Prompt，也不复制 Skill 全文。

## 2. Skill 拆分映射

| Skill 内容 | 应落到 | 不能只落到 |
|---|---|---|
| 事实/推论/情景隔离、根因链阶梯 | Doctrine + `CausalCase` schema + citation test | system prompt 文本 |
| 最低可交易事实包 | Evidence Requirement Policy | 模型自行判断“够不够” |
| 数据源优先级和 fallback | Capability bindings + source policy | Role 中硬编码网址 |
| 数据鲜度和置信度上限 | 确定性 Sufficiency/Gate policy | LLM Judge |
| funding/OI/mark/index/清算/基差 | typed Market Capability | 通用网页摘要 |
| DXY/收益率/VIX/油/FedWatch/OIS | typed Macro Capability + web fallback | owner 逐个手工补接口后才能运行 |
| 反方审查、拥挤度和 data quality | required Role capabilities | 固定五角色流水线 |
| 动作枚举、trigger/invalidation/RR | Decision Extension contract + Gate | 自由文本结论 |
| 30m/24h/72h | 独立 `HorizonDecision[]` 与鲜度策略 | 复制一段输出三次 |
| 评分和复盘 | Evaluation rubric + PIT fixtures | 对话中的主观打分 |

## 3. 必需研究能力

高影响 FOMC、Fed 官员、CPI/PCE/NFP 或重大地缘事件至少要求：

| Capability | 输出重点 |
|---|---|
| `event_identity` | 原始事件、时间、speaker/document、是否修订 |
| `policy_or_data_delta` | 实际措辞/数据相对前值与共识的变化 |
| `expectation_pricing` | FedWatch/OIS、收益率、已有仓位和是否 priced-in |
| `macro_transmission` | 利率/美元/流动性/风险偏好到 BTC/黄金的根因链 |
| `cross_asset_confirmation` | DXY、2Y/10Y、real yield、VIX、油、QQQ 等确认 |
| `derivatives_crowding` | mark/index、funding、OI、basis、liquidation、CVD/long-short |
| `counter_thesis` | 最强相反链、何时推翻主结论 |
| `data_quality` | 缺失、过期、冲突、来源等级和 confidence cap |

Supervisor 可以根据事件增加或拆分任务，但高影响事件不能删除 `counter_thesis` 和 `data_quality`。Role 数量不固定，required capability 固定由 Pack policy 决定。

## 4. 证据获取原则

```text
精确高频事实 -> typed official/market capability
未知新闻和长尾来源 -> DSH web.search/web.fetch
API 失败 -> Pack 声明的 fallback ladder
所有结果 -> EvidenceCandidate -> 三时间戳/PIT/来源校验 -> Evidence
```

关键数据优先级：

1. 官方或交易所原生数据。
2. 经审计的聚合 API。
3. 可验证网页正文。
4. 搜索摘要只用于发现或降低不确定性，不能替代精确执行数据。

缺失数据不是 `neutral`。在到达预算上限前，Manager 必须针对明确 Evidence Gap 继续同一个 DSH Session；达到上限仍不足时输出 `research_only` 和缺失列表，而不是生成伪精确概率。

## 5. 根因链契约

每个重要结论必须能投影为：

```text
observable fact
  -> prior expectation / positioning
  -> immediate cause
  -> deeper driver
  -> market transmission
  -> confirmation trigger
  -> trade implication
```

每一段至少包含：

- `claim_type`: fact / inference / scenario；
- `evidence_refs`；
- `freshness/status`；
- `trigger_probability`（无回测时明确 subjective）；
- `confirmation`；
- `invalidation`；
- `affected_horizons`。

如果链条没有可靠证据，只能标记为 `unconfirmed_scenario`，不能进入方向评分。

## 6. 三个周期必须独立

| 周期 | 主要证据 | 典型失效原因 |
|---|---|---|
| 30m | 讲话增量、即时收益率/DXY、BTC 价格/订单簿/衍生品 | 事件文本修订、第一反应反转、流动性噪声 |
| 24h | 定价持续性、现货/衍生品确认、跨资产收盘结构 | 市场已充分定价、后续官员修正、反向 flow |
| 72h | 后续数据/官员、收益率与美元趋势、流动性/资金流 | 新数据推翻政策链、风险 regime 改变 |

三个窗口可以同方向，但不能拥有完全相同的 Evidence、trigger、invalidation、expiry 和 next review。完全复制应触发 quality failure。

## 7. 确定性 Gate

Gate 顺序：

```text
PIT/source/freshness
  -> minimum fact pack
  -> hard blocks
  -> event compression
  -> primary signal coverage
  -> action enum and trigger/invalidation
  -> RR/EV execution gate
  -> confidence cap
```

Agent 只提交 `CausalCase + HorizonDecision[]` 候选。Gate 对缺失 mark/index、funding/OI、事件状态、未确认主催化剂、来源冲突和复制周期进行代码裁决。Agent、DSH、Judge 和前端都不能绕过 Gate。

## 8. 评测资产

首批评测至少包含：

- Evidence requirement coverage；
- citation precision/来源等级；
- PIT leakage；
- 根因链完整度和 fact/inference/scenario 隔离；
- counter-thesis 质量；
- horizon independence；
- data gap 发现后是否实际调用工具补证；
- stop reason 是否与预算/充分度一致；
- Brier、方向准确率、MFE/MAE、费用后结果；
- 延迟、token、搜索/Provider 成本和失败恢复。

历史 replay 只能证明可重复和无未来信息泄漏；是否带来真实收益必须用前瞻 shadow 与 Outcome 证明。

## 9. 已建立的资产目录

Owner 已授权 R2-R-00；以下目录已作为第一个真实 Domain Pack 创建：

```text
packs/crypto_macro/
  README.md
  pack.yaml
  doctrine/
  evidence/
  profiles/
  tools/
  gates/
  evaluations/
  fixtures/
```

`pack.yaml` 只引用 canonical 版本，不复制完整 Prompt；Skill 仍可作为 DSH 中的人机使用入口，但正式运行使用经过 codegen、测试和版本登记的 Pack 资产。

工具绑定当前均为 deny-by-default；除 deterministic replay 外仍是
`audit_status: candidate`。R2-R-01 已完成 DSH 版本锁定、权限审计和
readiness/tool/subagent canary；Web/Official/Market capability 仍需通过
R2-R-02 的来源、PIT、鲜度、authority 和 fallback 测试，不能作为已上线能力宣传。
