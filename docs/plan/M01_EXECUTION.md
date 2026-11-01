# M01 历史执行记录

> 本文保存 M01 的历史运行和故障证据。2026-09-09 起，唯一当前实施入口为 [Decision Hub 核心价值实施与验收记录](IMPLEMENTATION_CHECKLIST.md)；后续状态不再追加到本文。

## 目标与停止线

依照 V3.1 §5.1，交付一条 owner 手动研究 → 真实原文/基线与行情 → 缺口补查 → 干净上下文独立核验 → 有引用的主判断 → DSH 可见过程和结果的纵向链。必要 G0 修复随这条链完成。只修阻断当前案例的问题，不先建控制平台，不推进 M02–M05。

独立核验从 M01 就必须有；日常可用的 owner/G1 裁决属于 M03 后进入调度的条件，不是 M01→M02 的额外批准门。

## 当前状态

- 阶段：M01 / 开发中。目标已建立，未宣称通过。
- 源码基线：1f5a31da0da012c628737e5fa3bf8a3d04b344f1。
- 工作区已有用户提供的三个计划文档，保持原文。
- 首个案例：官方宏观讲话对 BTC 的研究；以明确官方原文 URL 和基线 URL 冻结事件，历史复盘不冒充当前事件交易窗口。实际案例与在线数据可达性在开工基线中落盘。
- 当前动作：核对已有入口/执行/研究/UI 接缝，先复现本链错误，再增量修改。

## 执行顺序与验收

| 步骤 | 行为交付 | 必须留下的证据 | 状态 |
|---|---|---|---|
| 1 | 固定案例与旧链路基线；核对运行条件 | 原问题、版本、真实来源、配置名、旧报告或准确启动失败原因 | 进行中 |
| 2 | 相关原文/基线进入研究，任务要求适配问题，能够真正补查 | 来源与时间匹配测试、长文引用、首查失败后二查成功 | 待做 |
| 3 | 最小独立核验与干净综合，生成有依据的主判断 | 真实 Session/Turn/receipt；核验处理与最终报告 | 待做 |
| 4 | 沿现有入口和执行链修 G0，DSH 同期展示任务/证据/报告 | 权限/幂等/续租/取消/终态测试；页面刷新与失败投影 | 待做 |
| 5 | 同一案例对照、故障测试、构建和必要回归 | 逐项实际结果与失败原因，明确离线与真实运行边界 | 待做 |

## 自测账本

仅记录实际执行，未执行不得勾选。历史基线：此前离线 Python 540 passed / 11 failed / 1 skipped；Desk 10 passed 和 build passed；DSH plugin 69 passed。这些不是本轮验收。

- [ ] 事件、人物、日期和 URL 不错配；无法确定时明确缺口
- [ ] 完整原文可回读，关键后半段内容进入相关片段
- [ ] 真实缺口补查，不因 capability 已使用就误认为所有目标穷尽
- [ ] 实际独立核验回执与干净综合
- [ ] 主判断、支持/反证、未知、改变判断的条件与引用
- [ ] owner、手动模式、一个活跃 root、重复提交和预算边界
- [ ] 租约/旧执行隔离、取消竞争、原子终态与派生失败
- [ ] DSH 当前任务/结果/失败可见，刷新恢复
- [ ] 当前改动相关测试、静态检查与构建
- [ ] 在线同题报告对照；不能运行则记录外部阻断，不标 M01 完成

## 改动与验证流水

### 2026-09-08 开工

- 重新读取 V3.1 §0–5，确认 M01 包含真实独立核验，而非推迟到 M02。
- 建立此记录，纠正早期 checklist 的阶段门和优先级表述。
- 尚无生产代码修改；后续每个切片在此追加实际改动、命令结果与下一步。

### 切片 1：来源匹配与正文（进行中）

- 红测试：官方事件新增 5 个失败用例，复现第一条错配、无法匹配和多条歧义；正文新增 2 个失败用例，复现后半段丢失和导航污染。
- 改动：RSS/Atom 必须唯一匹配明确文章 URL 或人物+日期；opaque event_id 不参与猜测。HTML 优先主体，保留段落；摘录按查询选择连续原文窗口。
- 绿测试：`pytest tests/research/test_document_adapter.py tests/research/test_official_event_facts.py -q` → 20 passed。
- 限定：全文持久回读、任务合同、真实复核和 UI 尚未交付；词面片段选择不等于语义引用支持，不能单凭这些测试标 M01 完成。
- 首个真实原文已取得：Waller 2026-09-03 官方讲话，HTTP 200；原始 HTML 位于 `tmp/m01/official-original.html`，SHA256 `ba27a2cd678239166587d9d60639bf7f2c89f78874683de863f9ccc038bd6c05`。这是来源基线，不是旧完整研究报告。
- owner 指定模型服务 codexai.club、gpt-5.5、OpenAI 原生 Responses；已按明确要求保存 gitignored `data/dsh-live/.env`，记录与测试不输出凭据。
- 原生 Responses + strict JSON Schema 最小在线调用 completed，返回 protocol_ok=true。结果 `tmp/m01/response-compatibility.json`；网关报告输入 4413、输出 14 tokens，包含其报告的额外 instructions 用量，成本未知。只能确认协议兼容，不证明模型来源或研究质量。
- 下一步：跑现有原生兼容 canary，启动固定 DSH；继续沿主链完成正文/基线与任务策略。

### 切片 1 的关联验证与实时联调

- 现有原生 Responses canary 真运行通过（合成输入）：生成 Artifact，gate=research_only，运行 degraded。修复 canary 的 Windows SQLite 连接释放，避免清理异常覆盖业务结果；不是业务预测验收。
- soft/hard 提交规则红→绿：软性缺口保留研究判断，硬缺口/硬冲突/不足覆盖继续阻断。
- DSH 报告不再把 no_trade 或未校准估计显示成 50%；显示“暂不支持交易判断”或“概率未校准”。
- 当前关联测试：研究 Python 175 passed；DSH plugin 70 passed，plugin build passed。完整静态/回归尚待最终切片。
- DSH 固定源码 SHA 校验通过、官方依赖安装与 build:official 通过；通过公开 profile plugin 安装当前插件，保存 owner 指定 Responses 路由，无上游私有补丁。
- Docker Desktop 启动后引擎仍不可用，联调改用现有 Python composition roots 与官方 DSH CLI，同机 loopback 隔离端口 8030/8032/3081；不新增执行平台，不启动 realtime/evolution/scheduler worker。
- 首次联调 API migration 与 MCP create_all 存在启动竞争。仅在新隔离 `data/m01-live` 中先 migration，再启动服务；旧 `data/m01` 保留诊断，不删除或修改用户业务库。
- 当前仍待完成：问题适配的任务合同、完整原文持久回读、真实补查/独立核验/干净综合、G0 权限/lease/epoch/取消与终态保护、实际任务详情和同题质量对照。没有把 175/70 测试通过当成这些内容已完成。

## 2026-09-08 continuation-format fix
- Real DSH case exposed a second-generation structured-output failure after the first generation had submitted an attested synthesis.
- Updated LangGraph continuation handling to preserve the prior trusted result for structured_output_invalid; identity/PIT/other non-retryable failures still hard-fail.
- Self-test: .venv\\Scripts\\python.exe -m pytest tests/orchestration -q => 23 passed.
- Remaining M01 blockers: genuine clean-context independent review receipt and end-to-end rerun proving readable degraded/final artifact plus DSH task visibility.

- Broader regression (tests/research tests/evolution tests/orchestration) reached 241 passed, 2 existing evolution failures on Windows recovery/hash paths; these are outside M01 touched modules and reproduce prior baseline class. M01 orchestration subset remains green (23 passed).

- Added canonical esearch_mode (historical_review / current_decision) to the research request contract. The factory detects explicit historical-review language in owner evidence and excludes current-only pricing/spot/derivatives requirements for that run, while retaining event/policy/macro/counter-evidence checks. Research tests: 173 passed.

- Hardened contract codegen for POSIX schema manifest paths and explicit UTF-8/LF manifest output; contract_codegen check reports canonical schemas ok. Generated Python tool still emits platform line endings through datamodel-codegen, recorded as tooling cleanup rather than M01 behavior.

- Contract and graph regression after esearch_mode and continuation preservation: 16 passed.

- DSH extension regression after task-state probability/error display changes: 70 passed (Vitest).

- Fixed contract generator newline determinism: generated Python artifacts are normalized to LF after datamodel-codegen/ruff. Generate + check and git diff --check now pass on Windows.

- Static typing verification for research factory, orchestration graph, and contract generator: pyright 0 errors / 0 warnings.

- Combined regression: 206 passed, 1 failed in an existing tiny-deadline DSH readiness test (expected readiness cause, observed submit cause); no M01 business assertion failed. This remains a timing-sensitive G0 test issue to normalize.

- Normalized DSH readiness timeout boundary: deadlines at or below one poll quantum now fail with dsh_web_readiness_deadline_elapsed before submit. Regression test passes.

- Rebuilt DSH extension after runtime/UI changes; Vite production build passed and refreshed build identity.


- DSH Web runtime focused suite after timeout-boundary fix: 11 passed; diff check clean.


- Audited compressed DSH session trajectories: native discovery/tool calls are present, but no subagent.started/independent reviewer receipt is present. This confirms the remaining M01 gap is real, not a UI-only reporting issue.

- Strengthened M01 DSH prompt contract: require exactly one bounded independent reviewer invocation before synthesis and a trace receipt containing role, evidence refs, challenge, and conclusion. Runtime regression: 67 passed, 1 skipped.

- DSH runtime contract suite after reviewer prompt requirement: 56 passed, 1 skipped.

- Rebuilt DSH production bundle after independent-review prompt contract update; build passed.

- Added a dedicated local DSH decision-reviewer preset derived from the audited research composition, with reviewer persona and same bounded tools. This turns reviewer selection into a concrete roster entry for the next live run.

- Verified data/dsh-live/.agent-presets/decision-reviewer exists alongside the manager preset; it is ignored runtime state and not committed as product code. Live rerun is still required to prove roster selection and receipt emission.

- New live run un_506deaf9c8ab43d0943e2beeac14d30c: API returned research_only artifact rt_985e3c1caca748cdb2e982e74fe85b1b; compressed DSH trajectory proves tool order included subagent before decision_hub_synthesis_submit. Added event mapping so native subagent tool call/result becomes subagent_started/subagent_completed with independent_review stage. Focused DSH runtime suite: 11 passed.

- Added historical typed-query retry instruction (Waller 2026-09-03 / Waller 2026-07-06) and rebuilt DSH. Second live rerun un_7a27ffe2f47f40a188b2f1185e1e416d failed with dsh_model_step_timeout before artifact; no false completion claim.

- Increased only historical-review model-step timeout to 240s (within the existing 480s total bounded budget); current decision timeout remains unchanged. Research suite: 173 passed.

- Final M01 live run un_521367d00da249d0a119f32a9f6ac026 produced artifact rt_2404f5dd97894fbebb9f8f0889995ba6; result is bounded degraded/research_only with 7 accepted evidence candidates, causal case with citations, and 	otal_subagents=1. Trajectory confirms reviewer call before synthesis. Remaining policy/macro gaps are explicitly surfaced as unknowns; no directional trading output is published.
