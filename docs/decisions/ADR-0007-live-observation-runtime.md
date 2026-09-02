# ADR-0007 Live Observation 运行时、耐久化演进任务与人工晋级边界

日期：2026-08-28
状态：accepted

## 决策

R2-L 使用同一仓库、同一 Product Kernel、同一业务账本和三个逻辑进程：`hub-api`、realtime worker、evolution worker。实时链复用现有 `RealtimeScheduler`；演进链新增 Kernel-owned durable `EvolutionJob`、lease/CAS 和 heartbeat，并复用现有 LangGraph Supervisor、`EvaluationRunner`、`EvolutionAssetService` 和 Promotion Gate。

开放网络检索通过审计后的 `CapabilityManifest + SearchCapabilityPort` 接入，默认关闭。演进 Agent 只能生成版本化候选和待审查记录，不能修改源码、Gate、权限、账本事实或 active pointer；Promotion 永远由既有 owner-only command 完成。

首版使用 SQLite WAL 和进程管理器/Docker Compose 提供重启恢复，不引入独立队列或第二个 workflow engine。

## 背景

R0/R1/R2 已完成文本主链、实时来源调度、评测/演进资产、Supervisor 和人工 Promotion，但当前只启动 API 时，页面只会展示历史数据，realtime worker 未常驻，演进组件也没有自动调度 composition。继续增加角色或页面不能解决产品没有持续运行闭环的问题。

## 候选方案

1. **选择：三个逻辑进程 + SQLite durable job/lease。** 最小复用现有边界，单机可恢复，未来可把 Repository 换成 PostgreSQL。
2. 单一进程同时运行 API/实时/演进。部署简单，但任一长任务会扩大故障域，健康状态也无法独立判断。
3. Celery/Redis、Temporal/DBOS 或 Kafka。当前没有跨机器、高吞吐或复杂补偿证据，引入会增加第二套状态和运维面。
4. 完全交给 DSH/Pi harness 常驻调度。会让外部 session/plugin 状态成为事实源，破坏可替换边界和 owner Gate。
5. Agent 自动修改代码并自动 Promotion。无法可靠回放、审计和回滚，且违反已接受安全约束。

## 否决项

- 否决 API 内 background task 承担长期 worker；Web 重载和扩容会造成重复执行。
- 否决 Graph checkpoint 代替 Evolution Job 表；checkpoint 是编排恢复数据，不是产品任务账本。
- 否决日志/JSONL 作为 worker 状态或 candidate registry。
- 否决未审计插件联网和 broad web search 默认开启。
- 否决 evolution worker 直接调用 Promotion 或写 active pointer。

## 后果

- 增加 `evolution_jobs`、`service_heartbeats` 及其 canonical Query/View 契约和迁移。
- worker role 可以独立停止、升级和观察；数据库/Job lease 负责恢复，进程管理器负责拉起。
- 真实搜索、Provider、来源、行情和通知仍需分别 opt-in/canary；离线退出门不宣称业务效果。
- SQLite 仍限制在单机共享卷；未来跨主机迁移只替换 persistence adapter，不改变领域契约。

## 迁移/回滚

升级前使用现有 SQLite backup；迁移只新增表/索引。功能回滚优先停止 evolution worker、禁用 Search Capability，不删除追加资产。若需回退代码，历史 Job/heartbeat 表可保留；active pointer 不受影响。

## 受影响契约

- 新增 `evolution-job.v1` canonical schema。
- 扩展 Operations/Evolution Query View；禁止 API/前端双写 DTO。
- 既有 `workbench-assets.v1`、Promotion、R0/R1 contract 行为保持兼容。

## 受影响测试

- contract/codegen、0015 -> 0016/fresh migration。
- Job 幂等、lease/CAS、恢复、重试和并发。
- 三进程 heartbeat/readiness/process smoke。
- Search Capability 权限/PIT/预算 fail-closed。
- Operations/Evolution API 和前端响应式验收。
- R0/R1/R2 全量回归与 owner Promotion 不变量。
