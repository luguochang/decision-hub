# Decision Hub 执行路线图

版本：`ROADMAP-2026-08-26.v1`  
用途：把 [产品架构基线](../DECISION_HUB_PRODUCT_ARCHITECTURE_V1.md) 中的 R0-R3 规划转换为 GitHub 可逐项追踪的执行清单。本文是任务状态入口，不替代架构基线、契约和 ADR。

## 使用规则

- 每个任务先锁契约、失败边界和验收测试，再实现。
- 完成任务必须同步模块 README、测试、实现状态和必要的 ADR。
- `done` 只表示本地测试和文档证据已经存在；外部 Provider 兼容性、业务准确率和生产可靠性分别记录。
- 不为未来场景提前创建无调用方目录、空服务或第二套业务账本。

状态标记：`done` 已验证；`partial` 已有边界或 scaffold，但未达生产验收；`next` 下一批执行；`blocked` 需要外部授权或真实数据。

## R0：Owner Production Core

### 已完成

- [x] `R0-01` 仓库工程治理：canonical schema、codegen check、ADR、模块 README、TDD/SDD、`.gitignore`。
- [x] `R0-02` 文本纵向链：`ObservationCreate -> TextEnvelope -> Event/Observation -> Snapshot -> Run`。
- [x] `R0-03` LangGraph decision/research graph；policy delta 与 counter-thesis 并行。
- [x] `R0-04` AgentRuntime port：Fake、Replay、LangGraph-native Runtime。
- [x] `R0-05` 严格 `AgentPayload` 和 OpenAI-compatible Responses/Chat adapter 配置。
- [x] `R0-06` 确定性 Gate、Artifact、30m/24h/72h Forecast。
- [x] `R0-07` Outcome、Brier、net return、Evaluation Query View。
- [x] `R0-08` SQLite WAL、Alembic、业务账本与 LangGraph checkpoint 分离。
- [x] `R0-09` Idempotency-Key、Timeline、事务 outbox 和本地 worker。
- [x] `R0-10` Decision Desk 最小页面和文本提交入口。
- [x] `R0-11` 本地 TDD/E2E、Runtime adapter、契约、文档和前端构建检查。

### 当前 `partial`

- [ ] `R0-12` Provider contract：记录模型、协议、超时、成本、重试和结构化输出能力；不能只依赖一次 canary。
- [ ] `R0-13` Run/Step/Attempt/Lineage/Gate 的完整可观测 read model 和前端 Run Inspector。
- [ ] `R0-14` PIT fixture 集、时间切分 replay/holdout/shadow 和 baseline/candidate 对比。
- [ ] `R0-15` SQLite backup、restore、integrity check、retention 和 startup recovery watchdog 自动循环。
- [ ] `R0-16` R0 ReleaseManifest、完整 failure injection、安装/升级/回滚 runbook。

### 下一批 `next`

1. 补齐 `R0-12` Provider contract 和统一 timeout/retry/error 分类。
2. 形成第一批固定 PIT fixtures，并建立 Fake/Replay 与 GPT-5.5 结果的同契约对照。
3. 完善 Run Inspector 的阶段、耗时、模型调用、Gate 命中和降级原因展示，禁止直接显示无用原始 JSON。
4. 增加 SQLite 备份恢复 smoke 和进程中断恢复测试。
5. 生成 R0 ReleaseManifest，明确已验证、未验证和不能宣称的能力。

## R1：Realtime Event Engine

- [ ] `R1-01` SourcePlugin registry、cursor、重连、去重、revision 和 source health。
- [ ] `R1-02` 官方 Fed/BLS/BEA 日历、RSS/正文和授权范围内的事件来源。
- [ ] `R1-03` Meeting Copilot/ASR adapter：只接收转写文本，保留 fragment/revision/PIT 语义。
- [ ] `R1-04` OKX 公共行情和事件后执行基准；缺少授权的跨资产行情只能降级。
- [ ] `R1-05` scheduler、Outcome 到期标记、漂移/失败聚合和 outbox 通知 adapter。
- [ ] `R1-06` Email、桌面或 IM 推送；通知失败不能重新触发分析。

R1 开始真实直播监听或外部通知前，必须新增对应 ADR、Provider 授权说明、契约测试和回放样本。

## R2：Decision Workbench 与自主进化

- [ ] `R2-01` Core MCP 和 DSH ResearchMemo adapter；DSH 只能提交研究候选，不能写业务账本或默认发布版本。
- [ ] `R2-02` 完整 Run Inspector：证据血缘、步骤、调用、成本、Gate、版本和回放对比。
- [ ] `R2-03` Evaluation 数据集、失败样本、反馈和策略实验登记。
- [ ] `R2-04` Evolution Engine：只生成 candidate，经过 replay/holdout/shadow 后由 owner promotion。
- [ ] `R2-05` Asset Promotion、回滚、版本 registry 和候选/默认策略对比。

## R3：领域与部署扩展

- [ ] `R3-01` A 股 Domain Pack，复用 Kernel/Run/Evidence/Evaluation，不把 BTC 字段扩散到 Core。
- [ ] `R3-02` 美股/宏观 Domain Pack，单独定义市场时段、执行基准和来源契约。
- [ ] `R3-03` PPT 等非市场产品使用独立 Domain Extension，验证 Kernel 的跨产品复用。
- [ ] `R3-04` 只有出现跨机器高可用、并发写入、远程只读或长期大规模 tick 数据需求时，才评估 PostgreSQL/远程部署。

## 明确暂不做

- 不 clone DSH，不把 DSH session 当业务账本。
- 不把 Pi 作为首版必需运行时；先在 replay/holdout/shadow 中证明优势。
- 不自动交易、不让 Agent 修改 Gate 或自动晋级策略。
- 不在真实授权和数据价值尚未证明前引入 Redis、Kafka、Temporal、DBOS、Kubernetes 或微服务拆分。
- 不把 ASR 评测、新闻抓取或漂亮前端当成文本核心链路已验证的替代物。

## 里程碑验收

| 里程碑 | 必须具备 | 当前 |
|---|---|---|
| `R0-Core` | 文本到 Forecast/Outcome/Evaluation、Gate、账本、回放边界、TDD/SDD | `partial` |
| `R0-Release` | Provider contract、backup/recovery、可观测 Run Inspector、ReleaseManifest | `next` |
| `R1-Realtime` | 授权来源、事件调度、行情基准、Outcome 到期和通知 | `blocked/next` |
| `R2-Workbench` | DSH MCP、完整观测、实验、候选晋级和回滚 | `planned` |
| `R3-Domains` | 第二领域真实复用和按需远程部署 | `planned` |
