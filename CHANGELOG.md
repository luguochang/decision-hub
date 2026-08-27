# Changelog

所有用户可见行为、阶段里程碑和重要兼容性结果都记录在这里。架构决定见 `docs/decisions/`，详细进度见 `docs/IMPLEMENTATION_STATUS.md`。

## [Unreleased]

### Governance alignment (2026-08-27)

- 对齐产品架构总表与当前 R0 交付证据：明确 R0/R1/R2 边界，修正实际仓库路径，并将动态 Supervisor、六层 grader、Version Registry、Evolution 和 DSH/Pi Workbench 保留为后续阶段能力；本次仅修改文档，不改变运行时行为。
- 重新运行 R0 core acceptance，确认文档修正没有改变契约、迁移、回放、恢复、前端或安全门禁结果。

### Next

- R1 Realtime Event Engine：在新的 Stage Charter 和 owner 授权后接入来源 registry、官方日历/新闻、行情基准、ASR 适配器、scheduler 和通知；不改变 R0 文本主链。
- R2 Decision Workbench 与自主进化：在 R1 真实数据和 R0 replay 证据稳定后，再接入 DSH/Pi ResearchMemo、实验、shadow 和人工晋级。

### Delivered (2026-08-26)

- R0 文本核心纵向链：文本 admission、PIT Snapshot、LangGraph research、确定性 Gate、Artifact、三档 Forecast、Outcome/Evaluation、事务 outbox 和 Decision Desk 最小页面。
- R0-B Provider Reliability Boundary：ProviderConfig、Responses/Chat、structured output、timeout、bounded retry、错误分类、usage/cost unknown/estimated、预算 fail-closed 和显式 canary。
- R0-C/R0-D：固定 PIT replay/holdout compare、Outcome/Brier/net return、Run/Step/Attempt/Call Inspector、checkpoint recovery/idempotent commit、Provider failure safety、SQLite backup/restore/integrity、Alembic upgrade path 和 ReleaseManifest。
- OpenAI-compatible `gpt-5.5` Responses live canary 已通过 Runtime/Graph/结构化解析/Artifact/Forecast 兼容性验证；合成输入的 Gate 结果不代表业务准确率或盈利能力。
- 全局 SDD + BDD + TDD + ADR 治理规范、Task Context Manifest 和模块文档检查已固化。
- Inspector 证据归一化：Facts/Citations 由对应 reviewer 的结构化字段提供，禁止把 synthesis 上下文 JSON 持久化或展示为用户事实。

## 记录规则

每个阶段完成后把 `[Unreleased]` 条目移动到带日期的版本节，并链接对应 ReleaseManifest、commit 和验证命令；未通过验收的能力只能写在 `Next` 或 `Known limitations`，不能写成 Delivered。
