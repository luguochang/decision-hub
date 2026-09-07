# PD-07 前瞻价值观察执行日志

状态：`not started / no prospective sample recorded`  
阶段卡：[PD-07 前瞻价值观察与产品停止线](../stages/PD_07_PROSPECTIVE_VALUE_OBSERVATION.md)  
记录纪律：只追加，不覆盖历史结果；原始事实、时间戳和 payload hash 以 Hub 账本为准。

## 入口基线（2026-09-05）

- `PD-00..06 engineering complete`，全量质量门和隔离页面证据见
  [PD 实施与自测执行日志](PD_IMPLEMENTATION_EXECUTION_LOG_2026-09.md)。
- 当前交付边界：单 owner、单机、只读 `research_only` 试点。
- Fixed active；DSH candidate/shadow；自动 Promotion 和自动交易关闭。
- 真实 `macro.cross_asset_intraday`、`macro.expectation_pricing` licensed Provider 尚未证明；
  免费/延迟 proxy 不能冒充实时事实。
- 当前合格前瞻样本数：`0`。
- 当前观察起始时间：`null`。历史、replay、fixture 和事后粘贴事件不计入。
- 当前结论：`observation_not_started`，不得输出 `promote`。

## Cohort Manifest

首个未来 EventWatch 创建前填写；任何影响事实、规划、Gate 或 Provider route 的改动必须新建 cohort，
不得修改旧行。

| cohort | started_at | code identity | DSH/upstream | extension hash | Pack/Role | Gate/Registry | Provider routes | status |
|---|---|---|---|---|---|---|---|---|
| pending | null | pending | pending | pending | `crypto_macro` / trader | pending | pending | not_started |

## Event Ledger

每个合格未来事件追加一行。`research_only` 可以是可读报告终态，但 Forecast/Brier/收益字段必须为空。

| no. | cohort | family | event/watch/run/session/artifact refs | scheduled/received/finished | baseline | hard coverage | report/notify/recheck | cost | owner usefulness / verification min | 30m/24h/72h | notes |
|---:|---|---|---|---|---|---:|---|---|---|---|---|

## Daily Reliability Snapshot

每天追加一段：服务 heartbeat、source cursor、Watch/sample、Run 终态、Provider attempts、失败分类、
延迟、成本和未到期 Outcome。没有新事件时写“无合格事件”，不能复制上一日数字充数。

```text
date:
cohort:
eligible events cumulative:
service/runtime status:
watch/sample status:
report success/failure:
provider/search failures:
cost known/partial/unknown:
open hard gaps:
owner action:
```

## Incident And Cohort Changes

每次修复记录：incident、根因、受影响样本、Red test、修复、全量回归、是否改变 cohort、是否需要
重启观察时钟。不得只写“已修复”，不得删除失败 Run 或把旧结果重算成新版本。

## Final Decision

观察门未满足，保持空白。最终只能引用一份 owner 确认的 ADR：

```text
decision: promote | retain_baseline | stop
decision ADR:
eligible sample count:
calendar duration:
category coverage:
PIT leakage / semantic substitution:
event-to-report / baseline coverage:
provider reliability / latency / cost:
owner usefulness / verification time:
forecast metrics (only eligible directional forecasts):
unresolved risks:
```

