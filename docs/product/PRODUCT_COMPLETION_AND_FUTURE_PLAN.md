# Decision Hub 产品收口与后续总计划

版本：`PRODUCT-PLAN-2026-08-30.v1`
状态：`accepted for G1/G2 execution / G3+ owner review pending`
适用范围：R2-R-07 之后的所有产品、工程、数据和领域扩展工作

> 本文的目的不是再增加一个无限路线图，而是把“什么必须完成、什么只需验证、什么有价值再做、什么明确不做”一次列清楚。后续新增问题必须归入本文已有 Gate；如果不能归入，先停在产品/架构评审，不直接开代码。

## 1. 产品结论

Decision Hub 的产品不是一个聊天页面，也不是一次 LLM 调用。首个产品是：

```text
持续发现事件
  -> 创建 durable research Run
  -> 在授权的工具/预算/时间内自主补证
  -> 证据充分度与 PIT 由代码裁决
  -> 输出分周期、可证伪、带来源和停止原因的报告
  -> 到期记录 Outcome/Evaluation
  -> 将失败样本、反馈和版本沉淀为个人资产
```

DSH 是可替换的 Harness 执行层，负责 model/tool/subagent/session loop；LangGraph 负责产品级生命周期、checkpoint、Evidence round 和恢复；Product Kernel 负责契约、PIT、账本、Gate、评测、资产和权限。三者所有权不能混淆。

当前正式结果仍是 Fixed baseline；DSH 是 candidate/shadow。只有通过本文规定的验证和 owner Gate，才允许改变 active runtime。任何阶段都不以“代码更多”“JSON 更多”或“报告更长”作为完成标准。

## 2. 当前事实与可用等级

| 等级 | 当前状态 | 可以做什么 | 不能宣称 |
|---|---|---|---|
| `U0` 文本核心 | `done` | 人工粘贴文本，得到固定链报告、Gate、Forecast、回放和评测 | 实时检索、智能体自主补证、盈利 |
| `U1` 单 owner 试运行 | `offline done / live gate pending` | 在明确授权的来源、行情和通知上运行 realtime worker | 所有来源可靠、无人值守、生产稳定 |
| `U2` Decision Workbench | `offline done / observation` | 查看 Run/Trace/Evidence、运行评测、管理候选和人工回滚 | DSH 默认更好、预测优势 |
| `U2.1-candidate` Research Agent | `R2-R-06E done / retain_baseline` | DSH candidate 主动规划缺口、调用受限 capability、失败可观察 | DSH 已 promotion、实时搜索稳定、预测准确或盈利 |
| `U2.1-pilot` 可用试点 | `未达成` | 经过可靠性修复和 prospective 观察后，作为个人研究辅助使用 | 自动交易、自动 Promotion、多领域产品 |
| `U3` 第二产品/远程部署 | `blocked` | 在真实需求和第一产品证据成立后扩展 | 预先建设泛化 SaaS 或微服务 |

“可用”在本项目中有两个含义，必须分开：

1. **工程可运行**：进程能启动、Run 能持久化、失败不产生伪结论。这一部分 R0/R1/R2/R2-L 和 R2-R candidate 已大体具备。
2. **值得依赖**：真实来源覆盖、延迟、证据完整性、预测校准和 owner 节省时间均有前瞻证据。这一部分尚未完成，不能用 replay 或一次成功 canary 代替。

## 3. 已知问题总表

以下是目前从代码、离线验收、真实 canary 和产品目标中识别出的完整问题集合。`Gate` 表示它属于哪个有限阶段；没有“临时顺手修”的类别。

| 编号 | 问题 | 影响 | 归属 Gate | 当前处理 |
|---|---|---|---|---|
| P-01 | 模型可填写可信 `observed_at` | PIT 边界可能误判 | G1 | 已修复；server-owned 时间和诊断投影已回归 |
| P-02 | PIT 拒绝被压成 `provider_timeout` | 无法定位失败来源 | G1 | 已修复；稳定错误码和 provenance 已回归 |
| P-03 | 并行 capability 一个失败导致其他结果 abort | 已完成证据可能丢失 | G1 | 已修复；部分成功/失败隔离已回归 |
| P-04 | 失败 Run 前端终态投影不完整 | Owner 误以为仍在运行 | G1 | 已修复；API/SSE/UI 失败投影已回归 |
| P-05 | Search/Official/Market 真实来源覆盖不足或被拒绝 | 关键事实无法补齐 | G2 | 六类事实 manifest/replay 已完成，真实 canary 待确认 |
| P-06 | 外部来源 403、404、超时、修订和限流 | 实时链路不稳定 | G2 | replay failure 变体已覆盖，真实 canary 待确认 |
| P-07 | Search 成本、延迟和结果质量未知 | 可能超预算或错过窗口 | G2 | 代码已记录 provenance/预算字段，真实 usage 待 canary |
| P-08 | DSH session/tool/subagent 真实长期稳定性不足 | Agent 可能半途失败 | G1/G3 | 已有 canary，需 prospective 观察 |
| P-09 | 证据 attestation、来源权限和跨 Session 污染 | 可能出现不可信事实 | G1/G2 | 代码已有 fail-closed，需回归 |
| P-10 | 30m/24h/72h 独立性和概率校准不足 | 报告看似完整但无增量价值 | G3 | 12-case 已暴露，需 Outcome |
| P-11 | owner usefulness 未填写 | 不知道是否真正节省查证时间 | G3 | 必须由 owner 体验后填写 |
| P-12 | Worker 重启、lease、checkpoint 和单次提交 | 长任务可能重复或丢失 | G1/G3 | 离线/本机已测，需长时间观察 |
| P-13 | Email/IM/桌面通知真实投递 | 结果可能无法到达 owner | G3 | local outbox 已有，外部 provider 未验证 |
| P-14 | 事件日历和新闻发现的授权、游标、重复/修订 | 可能漏事件或重复触发 | G2/G3 | fixture 已有，真实来源待授权 |
| P-15 | 进程健康、磁盘、备份、恢复和告警 | 长期无人值守风险 | G3 | Operations 骨架已有，SLO 未定义 |
| P-16 | Secret、网络域和插件权限治理 | 外部能力越权或泄漏 | G1/G2/G3 | deny-by-default，新增 capability 必须审计 |
| P-17 | schema、migration、runtime/profile 版本管理 | 升级后历史不可读 | 全部 | codegen/ADR/迁移纪律已锁定 |
| P-18 | 模型 Provider 协议、超时、重试和成本差异 | 中转站切换后行为漂移 | G1/G3 | Responses/Chat contract 已有，需按 provider canary |
| P-19 | 文档、状态和代码不同步 | 上下文长后目标漂移 | 全部 | SDD/BDD/TDD/ADR/Handoff 已固化 |
| P-20 | 中文/英文 ASR、partial/final/revision | 语音输入可能改变事实版本 | G4 | 暂不阻塞文本核心，复用 Meeting Copilot 边界 |
| P-21 | A 股、美股、PPT 等第二领域契约 | BTC 字段污染通用底座 | G5 | 只有真实第二调用方出现后启动 |
| P-22 | 多用户、权限、租户和公共插件市场 | 产品边界和安全面扩大 | G6 | 首期明确不做 |
| P-23 | 远程高可用、Postgres、队列和多机调度 | 单机能力不足时才需要 | G6 | 只有规模证据成立后评估 |

## 4. 有限 Gate 计划

后续只允许按以下六个 Gate 推进。每个 Gate 都有进入条件、任务、退出证据和停止条件；完成或失败后都要做一次产品决策，不自动开启下一 Gate。

### G1：研究链可靠性收口（离线实现完成）

对应阶段：[R2-R-07](../stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。

任务（已完成）：

- server-owned PIT timestamp 和旧请求兼容投影；
- Provider/Search/MCP/DSH/outer timeout 错误分层与 provenance；
- 并行 capability 单任务隔离、部分结果保留和 bounded cancellation；
- Research View/API/SSE/UI 的 failed/degraded/stop code/retryability 投影；
- fake/replay/失败注入和一次隔离真实事件回归。

退出门：错误码稳定、PIT 不可由模型绕过、已完成证据不丢、失败 Run 不产生方向性发布、前端显示真实终态。以上离线退出门已通过；G1 不改变 active pointer，不增加领域，不安装插件。

停止条件：若需要重写 DSH loop、扩大网络权限或引入第二套状态机，立即退回 ADR。

### G2：来源与能力覆盖验证（manifest/replay 完成，live acceptance pending）

目标不是“把所有网站都接上”，而是让一个明确的 `crypto_macro` 研究包拥有最小可用证据集，并知道缺失时如何安全停止。

任务（manifest/replay 已完成，live Search canary 已安全失败，E2-L pending）：

- 明确事件身份/政策变化/预期定价/宏观传导/BTC 现货/衍生品六类 requirement；
- 为每类 requirement 选择优先来源、回退来源、时间字段和权限域；
- 逐个验证 Fed 官方、FRED、BLS/BEA 可用替代来源、市场数据和受审计 Search capability；
- 记录 403/404/429/超时/修订、成本和延迟，不能把 Search 摘要伪装成官方事实；
- 为每个 capability 建立 replay fixture 和 manifest hash；live capability 仍显式启用。

退出门：每个高影响 requirement 至少有一个可验证来源或明确 `critical_data_unavailable`；真实来源失败时有 provenance 和 fail-closed；没有未来信息泄漏。六类 requirement 的 manifest 与回放已通过离线门；真实来源覆盖和 G2-D 充分度仍待一次受控 canary。G2 只验证数据链，不宣称预测优势。

停止条件：数据源需要违反授权、无法稳定提供 PIT 时间或成本不可控时，保留缺口并报告 `research_only/no_trade`，不无限寻找网站。

### G3：个人单机 Prospective Pilot 与价值决策

目标是验证这套产品是否真的比手工查证更有用，而不是继续添加基础设施。

任务：

- 单机运行 API、realtime worker、research worker、evolution worker 和受控 MCP；
- 选择固定观察窗口（建议至少 14 天或 20 个高影响事件，以先达到者为准）；
- 每个事件同时保留 Fixed baseline、DSH candidate、失败样本和 owner feedback；
- 记录启动到首个可用证据、最终停止、错误分类、工具调用、成本和通知延迟；
- 到期补记 30m/24h/72h Outcome，计算 Brier、方向、覆盖率、延迟和成本；
- owner 只填写“是否节省查证时间、是否理解停止原因、是否愿意继续使用”。

退出门：运行可恢复；没有伪证据/未来泄漏；失败可解释；owner 至少能判断是否节省时间；样本和失败都进入资产/评测账本。G3 结束必须做一次 `promote / retain / stop` 决策。

三种结果：

1. `promote candidate`：只在固定 Promotion 门通过时改变 runtime pointer；仍禁止自动交易。
2. `retain baseline`：DSH 没有足够价值，保持 shadow，停止新增功能。
3. `stop candidate`：成本、延迟、来源覆盖或用户价值不成立，删除/冻结候选运行时但保留历史证据。

### G4：语音输入扩展（可选，不阻塞核心）

只有 G3 证明文本研究链值得长期使用后才考虑。实现原则：

- 复用已验证的 Meeting Copilot 采集、VAD、`partial/final/revision` 和 EvidenceSpan 边界；不重写中文 ASR；
- 中文 FunASR 保持 provider，英文/多语 ASR 作为独立 `ASRProvider`/sidecar；
- 所有 ASR 输出先转 `TextEnvelope`，下游研究核心不知道输入来自音频还是人工文本；
- `partial` 只用于 UI/候选预热，正式 Evidence 等待 `final/revision`；
- 验收专名、数字、政策术语、否定句、时间、首字延迟、断流恢复、revision 撤销和本地显存/成本；
- ASR 失败不能覆盖已有文本事实，也不能直接触发自动交易或发布。

G4 的退出门是“接入文本核心且可回放”，不是“换了模型所以效果一定更好”。

### G5：第二领域/第二产品扩展（按需）

只有出现真实第二调用方时启动，例如 A 股、美股、供应链事件或 PPT。顺序固定：

1. 新建独立 Product Extension/Domain Pack 规格和 ADR；
2. 证明它只使用 Platform Core 的 Event/Run/Evidence/Artifact/Asset/Evaluation/Trace；
3. 为领域新增自己的结果契约、Gate、来源和评测，不把 BTC Forecast/Outcome 塞入 Core；
4. 用一个最小真实用例验证两个领域确实共享的接口；
5. 只有重复出现的共享需求才提取 Platform Core，不提前泛化。

PPT 不复用市场 Forecast 字段；它应拥有 `SlidePlan`、`RenderCheck`、素材和导出 Artifact。DSH 可以继续提供 Harness loop，但不成为领域账本。

### G6：规模化部署与多用户（条件触发）

只有出现以下任一真实证据才启动：跨主机并发写入、单机磁盘/CPU/内存不足、远程只读用户、需要高可用、或事件量无法由 SQLite WAL 承担。

任务可能包括 PostgreSQL、对象存储、队列、身份认证、租户隔离、限流、密钥服务、SLO/告警、灾备和多机 worker。每一项都必须单独 ADR 和迁移演练，不把“以后可能需要”当作当前代码任务。

## 5. 个人资产如何沉淀

每次运行都要沉淀以下可迁移资产，而不是只保存聊天记录：

- Domain Doctrine：根因链、证据要求、鲜度和 Gate 规则；
- Evidence/Source Pack：来源 manifest、PIT fixture、内容 hash、失败样本和回退关系；
- Role/Profile：Manager、反方、Data Quality、Specialist 的任务契约和权限；
- Runtime/Provider Profile：模型、协议、预算、超时、错误和版本；
- Research Trace：Plan、Tool、Evidence、Sufficiency、Stop Reason 的规范化轨迹；
- Evaluation Dataset/Experiment：Fixed、DSH、未来 Pi/OpenAI candidate 的同条件比较；
- FailurePattern/Experience：失败根因、修复结果、适用范围和是否值得推广；
- Product Artifact：最终报告、Forecast/Outcome、owner feedback 和 Promotion/Rollback 审计。

DSH JSONL/Session 只作为运行证据和诊断引用；它不能替代上述产品资产。未来更换 DSH、Pi、OpenAI Agents SDK 或模型时，以上资产不迁移、不重写，只替换 Runtime adapter。

## 6. “什么时候可以停止开发”

满足以下条件即可把第一产品定义为“个人可用试点”，停止继续堆功能：

1. G1 通过，真实失败能准确解释；
2. G2 对 `crypto_macro` 最小证据包有明确覆盖，缺失时安全停止；
3. G3 观察窗口完成，Run 可恢复、结果可追溯、成本和延迟可接受；
4. owner 能确认报告是否减少人工查证时间；
5. Fixed/DSH 的 promote/retain/stop 决策已记录；
6. 备份、恢复、通知和来源健康有可执行 runbook；
7. 文档、schema、模块 README、测试和变更记录同步。

达到该条件后，系统进入 `U2.1-pilot / observation`，后续只处理真实运行中出现的 P0/P1 问题，不再以新增页面、角色、插件或领域作为默认工作。

以下条件不属于“可用试点”的必要条件，不能用来无限延期：第二领域、多用户、PPT、英文 ASR、远程高可用、公共插件市场、自动交易、预测盈利证明。它们分别由 G4/G5/G6 或新的独立产品目标决定。

## 7. 新问题准入规则

以后发现任何新问题，先按以下顺序处理：

1. 判断它是否影响安全、PIT、账本、Gate、恢复或错误可解释性；影响则归入 G1/G2 的 P0/P1。
2. 判断它是否只能通过真实运行才能知道；只能加入 G3 observation，不先写复杂代码。
3. 判断它是否属于新领域/新输入/新部署；归入 G4/G5/G6，必须新建 Stage Charter。
4. 若只是 UI 偏好、Prompt 美化、日志字段或无真实调用方的抽象，暂不做。
5. 同一缺陷两次局部修复仍未解决，停止补丁，新增 ADR 做架构复盘。

没有通过对应 Gate，就不能把问题转成下一阶段的无限 backlog。每个 Gate 结束都必须产生一份 `promote / retain / stop` 结论。

## 8. 当前 owner 只需确认的事项

技术执行、离线测试、失败复现、文档更新和结果记录由 Agent 自动完成。当前只需要 owner 确认：

- 是否允许进行一次限时、只读、临时目录的真实 Search canary（G2-C）；
- G3 观察窗口达到后，是否愿意填写 owner usefulness，而不是由模型代填。

未确认前，系统保持 Fixed active、DSH candidate/shadow，继续允许离线/静态检查，不扩大网络权限，不进入 G3/G4/G5/G6。

## 9. 权威链接

- 当前状态：[CURRENT_STATE](../context/CURRENT_STATE.md)
- 当前决策：[CURRENT_DECISIONS](../context/CURRENT_DECISIONS.md)
- 当前候选阶段：[R2-R-07](../stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)
- 研究智能体规格：[RESEARCH_AGENT_PRODUCT_SPEC](RESEARCH_AGENT_PRODUCT_SPEC.md)
- 平台边界：[PLATFORM_BASELINE](../platform/PLATFORM_BASELINE.md)
- 全局治理：[DEVELOPMENT_GOVERNANCE](../engineering/DEVELOPMENT_GOVERNANCE.md)
