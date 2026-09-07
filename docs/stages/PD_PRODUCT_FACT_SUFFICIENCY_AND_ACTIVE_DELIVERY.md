# PD 产品事实充分度与主动交付 Stage Charter

版本：`PD-STAGE-2026-09-04.v1`  
状态：`accepted / PD-00..03 completed / PD-04..06 runtime closeout completed / PD-07 observation pending`  
Owner Gate：2026-09-04 已确认按详细方案与既有架构治理实现和自测。

## 价值假设

把当前“DSH 会搜索和循环、但事实不足后只能安全停止”的工程试点，收敛成单 owner 可以日常使用的主动研究产品：系统自动发现高影响事件、提前采集可比较事实、在同一 DSH Session 内补证、主动交付人类可读报告，并在 30m/24h/72h 后形成 Outcome/Evaluation。成功标准是事实语义正确和用户价值可测，不是报告更长或 coverage 数字更高。

## 权威详细方案

[产品事实充分度与主动交付修复方案](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md) 是本阶段 SDD、代码结构、产品视图、BDD/TDD、`PD-00..07` checklist 和停止线的详细真源；本轮执行修复、Red/Green 证据和唯一目标见[主动研究交付修复方案](../product/PRODUCT_AGENTIC_FACT_SUFFICIENCY_REMEDIATION_2026-09-05.md)。本 Charter 只固定阶段授权、路径和退出门，不复制正文。

## 输入与输出

输入：官方日历/feed、新闻 locator、人工文本、未来 ASR `TextEnvelope`、typed macro/crypto facts。  
输出：显式 lineage 的 Research Plan、`FactEnvelope`、PIT Evidence、Requirement Readiness、DSH Report/Inbox/Trace、通知、30m/24h/72h Outcome/Evaluation。

## 架构所有权

- DSH 是唯一 Agent Harness，拥有 Supervisor/Agent Loop/Tool/Skill/Subagent/Session/Trajectory/JSONL。
- LangGraph 只拥有 durable Run 生命周期、checkpoint、预算、轮次、恢复和确定性路由。
- Hub 是 Event/Fact/Evidence/PIT/Artifact/Forecast/Outcome/Evaluation/Outbox 唯一可信账本和发布 Gate。
- `crypto_macro` Domain Pack 拥有金融 requirement、字段/单位/窗口真值表、来源与 Gate policy。
- Provider/Search/Fetch/Telemetry 都是可替换 adapter/plugin，不能向 Core 泄漏私有字段或获得发布权。

## 复用清单与自研边界

复用 Pydantic/Zod codegen、SQLAlchemy/Alembic、LangGraph checkpoint、DSH Agent Loop、现有 Capability Gateway、Outbox、Outcome/Evaluation、官方 DSH extension 和 LoongSuite telemetry。只自研产品特有的 `FactEnvelope`、声明式 semantic validator、事件窗口策略、Provider mapper、来源注册、Query/View 投影和验收资产；不自研第三套 Agent Loop、搜索引擎、队列、插件安装器或第二套账本。

## 有限任务顺序

1. `PD-00`：恢复可信绿线；显式 `research_task.requirement_id`；`FactEnvelope`；Domain Semantic Gate；历史兼容。
2. `PD-01`：事件 watch、窗口快照、迟到/突发事件策略。
3. `PD-02`：多 venue crypto、分钟级 macro、expectation pricing typed capabilities 与 provider bake-off。
4. `PD-03`：DSH Search -> Source Registry -> Fetch/typed attestation。
5. `PD-04`：自动事件 -> DSH Run -> Inbox/报告/通知/recheck。
6. `PD-05`：Role/Pack/Runtime/Gate、readiness、成本和 LoongSuite 引用的人类可读投影。
7. `PD-06`：Outcome/Evaluation/Experience candidate 的受控闭环。
8. `PD-07`：至少 14 天或 20 个前瞻事件，只能在 `PD-00..06` 全绿后启动。

当前进度：`PD-00` 已于 2026-09-04 通过；`PD-01` 已在既有 scheduler/database/worker
边界内完成 durable EventWatch、八个事件窗口、迟到事件和采样失败语义，并以跨服务重建、
重复 tick 幂等通过退出门。`PD-02A/B` 已完成 Provider route/attempt 契约、durable failure lineage
和 Pack 驱动的 OKX primary/CoinEx fallback composition。`PD-02C` 已完成 EventWatch 窗口
capture、内容寻址 archive、Run/EventWatch lineage、archive-only route、event return/OI delta
和 FactStore/Gate 回放闭环。`PD-02D` 已完成订单簿 crowding proxy、Pack route 和衍生品完整
窗口语义回放；`PD-02E` 已完成 provider-neutral intraday macro/expectation seam 和离线完整
语义回放，但真实分钟级供应商仍 blocked；`PD-02F` Pack/profile/composition closure、`PD-02G`
public adapter canary 和 `PD-03` Source Registry/官方 event.identity parser 已完成；`PD-04..06`
的运行收口、DSH/Inbox/Report/Trace/Decision Desk 浏览器验收和质量门已于 2026-09-05 完成。
当前下一唯一产品阶段是 `PD-07` 前瞻观察；在真实分钟级 Provider 和价值门通过前不能把
research-only 试点写成实时交易产品，也不能用免费 delayed proxy 冒充正式分钟级数据。
PD-07 的样本准入、cohort、运行入口、指标和停止线以
[PD-07 前瞻价值观察阶段卡](PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)为唯一执行真源。

## 允许修改路径

```text
contracts/ packages/contracts_*
packs/crypto_macro/
packages/kernel/ packages/orchestration/langgraph/
packages/provider_adapters/ packages/source_adapters/
packages/query_views/ packages/evals/
apps/hub_worker/ apps/hub_api/ apps/decision-desk/
extensions/dsh/decision-hub/
migrations/ tests/ tools/
docs/ CHANGELOG.md INDEX.md
```

## 禁止修改与非目标

- 不修改或 fork DSH 上游私有实现；不复制 DSH Web。
- 不删除/重写历史 migration、Run、Evidence、Artifact、Forecast、Outcome。
- 不自动交易、自动 Promotion、在线修改 Gate、引入多用户、ASR 或第二领域。
- 不为满足测试访问 live network；普通 CI 必须离线确定性。
- 不把 Search locator、当前快照或 LLM 推论伪装成事件窗口事实。
- 不因供应商选择修改 Core、Graph 或前端领域契约。

## 失败与恢复语义

提交前超时不得取消不存在的远端 Session；Host accepted 后超时必须 best-effort cancel 并保持可对账。Provider timeout/429/5xx、域名拒绝、stale、字段/单位/窗口不匹配分别记录，不合并成“证据不足”。无未尝试能力或预算耗尽后才 bounded stop；服务重启不得重复 Evidence、Artifact、通知或 Outcome。

## 阶段退出门

- `PD-00..06` 的 Contract/Pack/Provider/Gateway/Graph/Worker/DSH Plugin/UI/Replay/Browser E2E 全绿。
- 至少一个未来官方测试事件无需人工提问完成 watch -> facts -> DSH -> report -> notification -> recheck。
- 页面不看 raw JSON 即可解释来源、窗口、缺口、错误、成本、Role/Pack/Runtime/Gate 版本。
- 旧 payload 和历史账本仍可读，PIT/future leakage 与 semantic substitution 均为 0。
- `PD-07` 观察入口和指标已建立，但未满 14 天/20 事件前不得宣称产品价值或盈利通过。

执行证据统一追加到 [PD 执行日志](../evaluations/PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
