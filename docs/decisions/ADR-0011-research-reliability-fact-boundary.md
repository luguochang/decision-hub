# ADR-0011 研究失败语义与事实覆盖边界

日期：2026-08-30
状态：accepted

## 决策

G1/G2 采用以下产品边界：

1. Research Capability Gateway 拥有网络 capability 的 `observed_at` 和 `received_at`；模型或 DSH 请求中的 `requested_observed_at` 只用于诊断，不能绕过 PIT。
2. Provider、transport、MCP、DSH、Gateway、PIT 和 orchestration 的失败必须投影为 canonical `ErrorProvenance`，保留最具体的稳定 `error_code`、来源、原因、capability、tool call、retryability 和 deadline。
3. capability 调用独立收集结果。一个调用失败不删除其他已完成结果；但只要 critical requirement 缺失、冲突或不可认证，Sufficiency/Gate 仍 fail-closed，只能 `research_only/no_trade`。
4. `crypto_macro.v1` 的六类最低事实需求由 Pack-owned `source_manifest.yaml` 定义，并以 success/stale/provider_failure replay fixture 作为离线事实源。Search 摘要不能替代 Official/Market 原生事实。
5. 普通 CI、replay 和模块测试永不访问网络。真实 Search 只能在显式 owner 确认后执行一次只读、allowlist、限时、临时目录的 canary；canary 结果不能自动 Promotion、交易、通知或修改 active pointer。

## 背景

R2-R-06E 后的隔离 Run 暴露了四个相互关联的问题：模型可以填写看似可信的时间；PIT 拒绝被压成 `provider_timeout`；并行 MCP 首个失败会影响其他能力；失败 Run 的终态在 API/UI 中不够可读。与此同时，真实研究需要事件身份、政策变化、预期定价、宏观传导、BTC 现货和衍生品六类事实，但不能用无边界搜索或摘要伪造覆盖。

## 候选方案

### A. 继续依赖模型时间和文本错误描述

否决。模型输出不是运行时事实，无法作为 PIT、重试或审计依据。

### B. 在每个 adapter 中各自定义错误 DTO、超时和事实列表

否决。会产生重复状态机和跨模块语义漂移，且无法保证 Query View、Trace 与 Worker 一致。

### C. Kernel 统一可信时间/错误/充分度，Pack 固化事实策略，Harness 只提供候选

选择。复用现有 DSH loop、官方 MCP transport、LangGraph 生命周期和 Pydantic/Zod codegen；Decision Hub 只增加必要的 Gateway、mapper、manifest 和 replay 薄层。

## 后果

正面：

- 失败可定位到具体 capability 和时间边界；部分成功可审计保留；失败 Run 不会伪装成运行中。
- 事实覆盖有稳定的来源优先级、鲜度、独立来源数量和回放证据，缺失时安全停止。
- Fixed baseline、DSH candidate/shadow 和未来其他 Harness 共享同一 canonical 结果与 Gate。

代价和限制：

- 增加 `ErrorProvenance` 字段和 `0021_research_error_provenance` 迁移；历史 Run 不改写。
- G1/G2 离线实现通过不等于真实 Search 稳定、预测准确或盈利；G2-C/G2-D 仍需独立证据。
- 新 capability 仍必须通过许可证、安全、schema、PIT、replay、预算和 owner enable 检查。

## 迁移与回滚

- 旧错误码、旧 Research Trace 和历史业务表保持可读；新 provenance 字段可空，Query View 对旧记录提供保守 fallback。
- 若真实 canary 失败，保留脱敏失败报告，Fixed 继续 active，DSH 继续 candidate/shadow；不放宽 PIT、authority 或 hash 规则。
- 不引入第二套 Agent Loop、搜索协议、账本、队列或基础设施。

## 受影响契约与模块

- `contracts/schemas/agentic_research.schema.yaml` 及其 codegen 镜像；
- Kernel Research Capability Gateway、Sufficiency 和 Research Observability；
- DSH/MCP/runtime/provider/query-view/worker adapter；
- `packs/crypto_macro/evidence/source_manifest.yaml` 与 `g2_replay_manifest.json`；
- migration `0021_research_error_provenance` 及 G1/G2 测试。

## 必须通过的验证

- server-owned PIT、错误 provenance、并行部分成功和失败 Run 投影的 BDD/TDD；
- 六类事实的 manifest/replay success、stale、provider_failure 覆盖；
- `pytest -m "not live"`、Ruff、Pyright、canonical codegen、模块文档、前端 test/build、迁移和 `git diff --check`；
- G2-C 真实 canary（需 owner 确认）和其后的 G2-D 事实充分度报告。
