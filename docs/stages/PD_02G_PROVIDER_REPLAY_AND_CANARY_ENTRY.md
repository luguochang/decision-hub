# PD-02G Provider Replay / Canary Entry

版本：`PD-02G-2026-09-04.v1`  
状态：`completed / engineering exit passed / public adapter canary passed / macro live blocked`

## 1. 目标与边界

本卡收口 PD-02 的验收入口，不新增 Provider 或 Agent 框架：

```text
普通 CI
  -> adapter fixtures
  -> Router/Gateway/Durable Gateway
  -> Evidence/FactStore/Semantic Gate
  -> 全量质量门

显式 live canary
  -> 已批准 public adapter health
  -> typed facts / provenance / cost 摘要
  -> 脱敏 JSON
```

普通 CI 永不联网。live canary 必须设置
`DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY=1`，只访问 Pack 已批准域名，不写产品账本、不读取
模型或 Search key。它只证明当次 public endpoint 和 mapper 可用，不证明事件窗口、完整事实充分度、
预测准确率、收益或生产稳定性。

宏观 intraday/expectation 仍是 `candidate/review_required`。没有真实授权 endpoint 时，canary
入口必须显示 blocked，不允许通过临时修改生产 manifest 冒充通过。

## 2. 实现

- 复用 `tools/canary/run_research_fact_capabilities_canary.py`，不新增第二个 canary runner。
- public canary 增加 `market.crypto_crowding`，并要求每个 case 同时返回 Evidence 和 typed Facts。
- 脱敏输出只保留 provider、计数、metric families、delay classes、event offsets、source domains、
  latency、cost 和稳定 error code；不输出原始正文、payload、key 或请求 header。
- `cost_usd=null` 保持 unknown；只有 provider 明确上报免费才显示 `0.0`。
- public current snapshot 的 `event_offsets=[]` 必须原样展示，不能把 adapter health 写成
  event-window sufficient。
- macro/expectation 的离线完整语义回放继续以 PD-02E 测试为权威；真实 canary 等 owner 提供
  供应商授权、固定域名和 endpoint 后再执行。

## 3. BDD/TDD

```gherkin
Scenario: 普通测试不触网
  Given 未设置 live canary 开关
  When 运行普通 pytest
  Then canary 拒绝执行外部请求

Scenario: public adapter health 不能冒充研究充分
  Given public endpoint 返回 current typed facts
  When canary 成功
  Then 输出 metric family/delay class/event offsets
  And note 明确 current snapshot 不证明 event-window sufficiency

Scenario: 没有 typed facts 不能通过
  Given endpoint 只返回 Evidence 或空 facts
  When canary 校验结果
  Then case 为 failed

Scenario: candidate macro provider 保持 blocked
  Given production manifest 为 candidate/review_required
  When 未提供授权与审批
  Then 不执行该 provider
  And 文档与 readiness 保持 provider_blocked
```

## 4. 退出门

- [x] crypto、crowding、macro/expectation 离线语义全链测试存在并通过；
- [x] public canary 要求 typed Facts，而不只检查 Evidence；
- [x] canary 输出脱敏字段并显式区分 adapter health 与 semantic sufficiency；
- [x] 默认不开启网络，candidate provider 不被临时审批；
- [x] 全量 Python/前端/静态/契约/文档质量门通过；
- [x] public endpoint live canary 本轮实际执行并记录；
- [ ] licensed macro/expectation live canary（外部 Provider/授权阻断）。

### 本轮 public canary 结果（2026-09-05）

`tools/canary/run_research_fact_capabilities_canary.py` 在显式
`DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY=1` 下完成五个只读 public adapter case。结果为
`status=passed`：`official_feed` 返回 1 条 Evidence/3 条 `event.identity` Facts；
`cross_asset` 返回 FRED delayed 日频 proxy；`crypto_spot`、`crypto_derivatives` 和
`crypto_crowding` 返回实时 current snapshot typed Facts，crowding 记录了 primary timeout 后
CoinEx fallback 成功的两次 attempt。`event_offsets=[]` 的 current snapshot 仍未关闭事件窗口
requirement，整体 `semantic_scope=current_snapshot_adapter_health_only`。宏观 intraday 和
expectation pricing 仍是 `live_provider_blocked`，不得把 canary 通过写成 PD-02 产品退出或交易可用。
