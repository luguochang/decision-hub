# Changelog

## Unreleased

- 2026-09-01 `PRODUCT-CLOSEOUT-01 / E2-L` 通过真实官方 DSH Web 产品验收：Run
  `run_04dc1a46fd1e4c3b988750e18b0e9581` 在受管 Session 内完成 2 轮、12/12 durable Tool
  Call、15 条 Evidence、66.7% hard coverage，并由代码 Gate 收敛为 `research_only / tool_budget`；
  DSH 与 Decision Desk 终态一致。后台 scheduler/worker 自动创建 child recheck Run
  `run_7df306876d114e09b8e83507d06f3ef0`。两次 Search timeout、FRED stale 和 unknown cost
  如实保留，不发布方向性交易结论。
- 2026-09-01 最终工程门通过：Python `390 passed`、DSH Plugin `53 passed + build`、Decision
  Desk `10 passed + build`、Ruff、Pyright、codegen、module docs、Compose、migration、
  recovery、三类 replay、callback/Web restart、rollback、桌面/窄屏和截图 hash 均通过。
  当前状态为 `pilot_ready=true / pilot_usable=research_only / Fixed active / DSH
  candidate-shadow`，下一阶段只做 E3 前瞻价值观察；不代表预测准确、盈利、自动交易或 Promotion。

- 2026-09-01 DSH Client Plugin 通过官方 `conversation.view` 新增独立“研究报告”页签，并将完整报告从 composer dock 移入该页；不覆盖上游 Chat/Trajectory、不修改 JSONL。上游 `0.1.2-alpha.2` 没有第三方 default-view seam，首次打开仍为 Chat，该限制已写入控制书与模块 README，禁止 DOM/CSS hack。新增 TDD 后插件 `43 passed`。
- 2026-09-01 Owner 接受的最小只读 capability canary 全通过：Fed official 1 条、FRED cross-asset 3 条、CoinEx spot/derivatives 各 1 条，成本均为 0；FRED 最大年龄约 4.42 天，继续由 freshness Gate 标 stale。新隔离实例在 MCP/worker 两侧加载一致 allowlist；完整离线、三类官方 replay、跨进程恢复、callback/Web restart 和 locked rollback 通过，新的浏览器 live Run/截图仍 pending。

- 2026-09-01 修复官方 DSH Client 的业务状态标签：DSH 回合 `completed` 不再被误写成 Hub 业务 `Run completed`，真实失败报告卡显示“研究失败”；新增回归后 DSH Plugin `42 passed` 且 production build 通过。浏览器复验桌面/窄屏无横向溢出、无 console error；因主动重启留下的 connection retry warning 如实保留。
- 产品交付控制书已校准为 E2L-A/B/C/D 完成、E2L-E 已执行但被 live capability acceptance 阻塞；Owner 接受最小只读 `official.macro`、`market.cross_asset`、`market.crypto_derivatives` manifest allowlist 复验，不授权放宽 Evidence/PIT/Gate、任意社区插件或 active Promotion。

- 2026-09-01 E2L-E 真实官方 DSH Web 复验：首次配置错误、第二次 `dsh_model_step_timeout` 和第三次正确 Provider 的有界补证 Run 均保留；第三次使用 `codexai-gpt55/gpt-5.5` 完成 16 次 DSH capability 调用，保留 20 条 Evidence/38 条 Trace，Search 4/8 timeout 后在未准入的 `market.crypto_derivatives` fail-closed。确认中转站 Chat 流式可用而 Responses 流式 idle timeout，本机 gitignored DSH route 改为官方 `openai-completions` + compat 声明。E2L-E/E2-L 仍 blocked，`pilot_ready=false`、`pilot_usable=false`，Fixed active、DSH candidate/shadow。

- E2L-03 Query/View 根因收口：无最终 `ResearchSessionResult` 的失败 Run 现在从 durable Trace/Evidence 重建可审计 round、tool invocation/result、runtime、coverage 和真实 hard gaps；synthesis attestation 失败显示为 `critical_data_unavailable` 并保留 `dsh_evidence_unattested/synthesis_attestation` provenance，causal/horizon/decision snapshot 保持为空。新增回归后全量离线 Python `373 passed`；真实 Web/Search/value 门仍 pending。

- E2L-03 offline closeout：持久化 DSH `deadline_at`（migration `0026`）并将 live effective cutoff 与 freshness/decision cutoff 分离；模型不能延长 live 时间窗口，历史缺失 deadline 的 link fail-closed。
- E2L-03 offline closeout：DSH Web synthesis attestation/structured-output 失败在存在可信 Evidence 时返回 evidence-only `degraded` 结果，保留 Evidence、Tool Trace、coverage 和失败 provenance，丢弃所有 causal/horizon 语义，不增加 repair turn 或占用下一 generation；无可信 Evidence 仍直接失败。
- E2L-03 offline closeout：LangGraph 和 Query/View 防止 coverage 足够但 synthesis 无效时误标 `completed`；失败 Run 的 runtime、round、tool count、coverage 和逐 capability failure 可由 durable link/Trace/Evidence 投影。专项联合测试 `72 passed`；全量离线门为 Python `372 passed`、DSH plugin `41 passed`、Decision Desk `10 passed`，真实 DSH Web 新实例仍 pending，`pilot_ready=false / pilot_usable=false`。
- 新增 [产品交付控制书与最终验收包](docs/product/PRODUCT_DELIVERY_CONTROL_BOOK_2026-09-01.md)，作为当前交付周期唯一执行入口；同步产品/工程/上下文索引，锁定 E2-L/E3 停止门，不新增运行时或领域能力。

- `PRODUCT-CLOSEOUT-EXEC-02` 已通过 E1 与 E2-R：修复受管 DSH Session 未挂到官方 Workspace 的根因，Host 现在通过公开 `workspaceRegistry.resolveByPath()` 和 `workspaceId` 创建/补挂 Session；未注册或解析失败使用稳定错误码 fail-closed。
- DSH Client Plugin 现在复用 canonical `ResearchRunDetailView` 在官方 `conversation.input.dock` 投影人可读报告，显示 Gate、有效 Evidence、工具失败 provenance、主/反因果链、30m/24h/72h、Trigger/Invalidation 和复查时间；没有新增第二套 Chat、报告后端、账本或 Agent Loop。
- 官方 DSH success/partial_failure/insufficient_or_stale 三类 replay、同 Session generation 2、callback/Web/lease 恢复、fresh migration、backup/restore、版本 fail-closed 和 locked rollback 已通过；最新门为 Python `343 passed`、DSH plugin `34 passed`、Decision Desk `9 passed` 及全部静态/契约/build/Compose 检查。
- 当前仍为 `pilot_ready=false / pilot_usable=false`：真实 `web.search` 继续 `research_capability_timeout`，Fixed active、DSH candidate/shadow，E3/P4 价值观察未开始。当前 revision 只有桌面报告截图/hash，真实移动 viewport 与 console 导出保持 pending，不作完成声明。
- 新增 [产品收口总执行书](docs/product/PRODUCT_CLOSEOUT_MASTER_EXECUTION_2026-09-01.md)，将 PRODUCT-CLOSEOUT-01 的剩余 C3-C7 工作、最终页面视图、DSH/Hub/插件和两层 loop 边界、个人资产沉淀、SDD/BDD/TDD/ADR/上下文约束、逐卡任务模板、真实阻塞和最终验收 checklist 收敛为唯一执行任务卡。文档不改变既有 ADR、canonical schema 或历史执行记录，未验证项继续保持 pending。
- 保留 DSH intake 稳定幂等键；相同输入命中失败 Run 时，官方 Client Plugin 现在通过 canonical `ResearchRunCommand(retry)` 创建唯一 child Run 并继续跟踪，不再要求修改文本或随机制造重复事件。
- DSH business status 现在投影 Run 顶层 `ErrorProvenance`；即使没有 Evidence/Artifact，也能显示 runtime error code、origin、cause 和 retryability，Provider/编排失败不再退化成只有 `failed` 的黑盒状态。
- C1/C2：官方 DSH Web 插件新增 typed `建立研究任务` 入口、稳定幂等 intake、Hub durable Run 状态轮询和 `research-run-queued.v1` canonical 响应；保持 DSH 原生 Chat/Session/Trajectory 不变。
- 2026-09-01 live 产品实测保留真实 Provider 超时失败：`web.search` 无响应导致 `provider_timeout/dsh_web_deadline_elapsed`，未创建方向性 Artifact/Forecast；详见 `docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md`。

## 2026-09-01

- Owner 接受并授权按 `docs/product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md` 完成 `PRODUCT-CLOSEOUT-01` C1-C7 整体产品目标；补充 live/replay capability 准入语义，明确 live 产品不得默认使用 `replay.research`，首个已审计 live allowlist 为 `web.search`，Official/Market typed capability 逐项 canary 后才能晋级。
- 产品启动器默认改为 live `web.search`、拒绝混入 replay capability，并通过 Compose `--env-file` 把 gitignored Provider 配置传给 Research MCP；Docker 构建上下文排除全部本机 `data/`、`.cache/` 和任意层级 `.env`，防止 Session、数据库、上游缓存和凭据进入镜像；Node 基础镜像改用 Public ECR 的不可变 digest，消除当前 Docker Hub 阻塞。
- 修复新镜像运行时入口：镜像有意不安装项目 entry points，Compose worker 与 Research MCP 因此统一使用 `python -m apps...` 模块入口，避免静态配置通过但容器启动时报 `executable file not found`。

## Unreleased - 2026-08-31

- 新增 [产品执行总方案](docs/product/PRODUCT_EXECUTION_MASTER_PLAN.md)，将 DSH Web 唯一入口、Decision Hub 控制面、双层插件、两层循环、目标代码结构、SDD/BDD/TDD/ADR 和 C1-C7 工程/产品/价值验收门收敛为单一执行入口。
- `research-mcp` 增加宿主机 loopback 端口映射；`run-product.sh` 传递宿主 MCP URL，修复宿主 DSH 与 Compose capability 网关之间的可达性缺口。
- `run-product.sh` 现在在官方 DSH Web 发布认证 URL 后，通过官方 `workspace/create` 注册默认 `Crypto Macro Trader` 工作区；工作区名称由 DSH 原生 Workspace Controller 展示，未复制上游 UI。
- 新增 [PRODUCT-CLOSEOUT-EXEC-01 执行与自测记录](docs/evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-08-31.md)：328 Python tests、静态、契约和前端质量门通过；Compose build 因 Docker Hub registry 超时保持 blocked，未将旧实例作为新产品证据。

## [Unreleased] PRODUCT-CLOSEOUT-01 accepted implementation (2026-08-31)

- 新增 [最终产品交付与主线闭环方案](docs/product/FINAL_PRODUCT_DELIVERY_AND_MAINLINE_CLOSURE.md)，独立记录 DSH Web 唯一用户入口、Decision Hub 控制面、`crypto_macro` Domain Pack、Decision Desk 管理后台、后台主动研究、补证 loop、报告/通知、Outcome/资产沉淀、未完成缺口和有限的 C1-C7 交付顺序。
- Owner 已确认 `PRODUCT-CLOSEOUT-01 / DSH Native Trader Pilot`，新增 Stage Charter 固化 C1-C7、页面视图、代码边界、DSH/Hub 两层循环、插件准入、SDD/BDD/TDD checklist、真实 canary 和最终交付门。
- 明确当前状态仍是“可复用工程底座 + 离线/候选链已验收”，不是实时市场产品交付；进入 C1-C7 实施后，Fixed 仍 active，真实 capability 只允许隔离 canary，不自动切 active pointer。

## [Unreleased] DSH-NATIVE-CORE completion plan (2026-08-31)

- 持久化本机 live Provider 配置到 gitignored `data/dsh-live/.env`/`settings.yaml`，不再要求重复输入；真实网关兼容性已核验为 `/v1/responses`，`/v1/chat/completions` 不可用。
- 修复 live 自定义 route 将 `gpt-5.5` 默认推理错误声明为 `high` 的配置根因，改为未探测能力下的 `off` 并保持 fail-closed。限时 live canary 已在 DSH 原生 Web 中真实完成同一 Session 两轮 `LIVE_OK`/`LIVE_TURN_2`，Session 索引和压缩 JSONL 均落盘；未宣称 Search、主动补证、事实覆盖或产品价值通过。
- 具体 Provider/Session/JSONL/hash/失败分类和未证明项见 [DSH 交互运行态与插件生态审计](docs/evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md) 的 9.2 节。
- Decision Hub 既有 R0 纵向 canary 也使用该真实 Responses 网关完成：合成文本 `run_13b0bb8a71b44fbd9db8f6d36d449c31` 为 `completed/publish`，三个 Horizon 和结构化 Artifact 正常；结果仅作为 Provider/Graph 兼容性证据，未扩大为实时 Search 或产品价值结论。

- 修复 replay 页面被误当成 live 会话的问题：纠正 DSH `schemastery` 的 runtime 配置类型，将只读守卫挂到新建 Session 也会渲染的官方 `conversation.input.dock`，并以新隔离实例和浏览器自动化验证输入不会产生 `unrecorded session`。新增 `infra/dsh/run-live-web.sh`，无 Provider 凭据或带 replay 配置时在启动前 fail-closed；未修改官方 DSH 源码或 replay fixture。

- 新增 [DSH-NATIVE-CORE 完成实施方案](docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)，把当前产品事实、NATIVE-04/05 剩余任务、Host terminal 幂等、取消错误语义、authority floor、per-capability provenance 和三类 replay 退出门固化为可执行任务卡。
- 修正 DSH 代码地图和总装文档的阶段编号/插件状态投影；不改变 active pointer、历史账本或 live capability。
- 修复 `tools/research_recovery_smoke.py` 的直接脚本入口，使其与模块入口共享仓库根路径并通过恢复验收；`research_acceptance.py` 现为 `28 passed`。
- `NC-01..NC-07` 与 NATIVE-00..05 工程证据已闭环：全量 Python `327 passed`，Ruff/Pyright、契约/模块文档、Decision Hub plugin 18 tests/build、Decision Desk 9 tests/build、Compose 与官方上游 acceptance 均通过。
- 官方 DSH Web success/partial_failure/insufficient_or_stale、Web restart、callback recovery、版本 fail-closed、locked rollback 和桌面/移动浏览器资产已通过；阶段状态为 `engineering acceptance complete / product value pending`。
- 修复官方 DSH 嵌套 tool-result provenance 映射：不再把 `research_replay_fixture_missing / gateway` 粗化为 `dsh_tool_failed / mcp`；严格解析 canonical `provenance=` JSON，解析失败才 fail-closed。
- 该完成声明不包含 `product_ready`、实时市场能力、预测准确率、盈利或 DSH active Promotion；下一步须独立授权 NATIVE-06 live/value Gate。
- 在执行方案第 0 节登记本次唯一大目标、产品价值断言、允许/禁止路径、三个核心验收断言和停止条件；补充说明固定官方源码闭包内的 `@deepseek-ai/dsh-llm-replay` 仅可作为同版本 keyless acceptance transport，不是生产网络能力。
- 统一完成实施方案、实施状态和当前决策索引的时态：官方 Web 三场景已完成，`DSH-NATIVE-CORE` 不再登记为活动实施目标；旧 Search reliability 授权问题已被完成事实取代，当前唯一待决项是是否授权 NATIVE-06 live/value Gate。
- 新增 [DSH 交互运行态与插件生态审计](docs/evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md)：记录 `--serve` replay 与 live DSH 的边界、脚本耗尽输入错误的 JSONL 证据、官方 Host/Client plugin 现状、候选开源插件及 LIVE-00..03 有界计划；不修改 replay、active pointer 或 live 授权。
- 修正文档状态：DSH plugin README 标记为工程验收完成/产品价值待验，G1/G2 前端 smoke 标记为历史验收已闭合；不改变运行时行为。
- 统一旧代码地图中的 NATIVE-00..05 状态：标记为工程验收完成，并明确 NATIVE-06/LIVE-00 才是后续 live/value Gate；修正“官方 Host/Client bridge 尚未完成”等过时描述。

所有用户可见行为、阶段里程碑和重要兼容性结果都记录在这里。架构决定见 `docs/decisions/`，详细进度见 `docs/IMPLEMENTATION_STATUS.md`。

## [Unreleased]

- 真实 DSH Web 运行 `run_3c0c64d966aa431da7aecce642694fcf` 完成 2 轮、12 次工具调用、10 条有效 Evidence 和 Hub Gate 投影；补齐工具参数声明 `request_id` 与底层 call ID 的 attestation 映射，避免合法同 Session 结果被误报为 `dsh_evidence_unattested`。修复后全量 Python `392 passed`、Ruff、Pyright、契约、文档和 Decision Desk 检查通过。运行最终因实时宏观/事件窗口事实不足而 fail-closed 为 `research_only/degraded/no_trade`，详见 [`DSH Live Research Attestation 修复记录`](docs/evaluations/DSH_LIVE_RESEARCH_ATTESTATION_FIX_2026-09-02.md)；不代表预测准确、盈利或 DSH Promotion。

- 新增 [通用底座与首个产品最终实施章程](docs/product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)，统一 DSH-first 产品形态、Hub/LangGraph/官方插件/Domain Pack 所有权、后台双层循环、个人资产沉淀、P1-P6 有界路线、E1-E3 验收门和停止线；后续代码只按该章程执行，不提前扩展 ASR、第二领域或规模化基础设施。
- 澄清 2026-09-01 Search 首轮超时与后续 Official/Market 模块级 canary 通过的状态差异，保留历史失败证据，不把模块级 canary 误写为完整产品准入。

- 新增 [DSH 与 Decision Hub 边界和代码地图](docs/product/DSH_AND_HUB_BOUNDARY_GUIDE.md)，独立说明 DSH 主壳、Hub 外层、官方插件、LangGraph、两个前端、三份状态数据和当前/目标代码落点；不新增实现授权。
- DSH-NATIVE-CORE 早期隔离验收曾只完成单条 stale/research_only replay 和 contract recovery；该历史 partial 状态已由本节上方记录的官方三场景、恢复/回滚和浏览器证据取代，失败样本继续保留。
- `infra/dsh/acceptance.sh` 的固定上游 commit/version/hash、公开 Session/Web/plugin seams、官方 Web 构建和认证 boot smoke 已纳入最终 NATIVE-00..05 工程验收。

### DSH-first implementation blueprint (2026-08-30)

- 新增 [DSH-first 产品实现总方案](docs/product/DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)，基于当前真实代码审计一次性锁定产品主界面、DSH/LangGraph/Kernel 所有权、双层插件与 CapabilityManifest、后端目录职责、前端信息架构、Replay/Live 边界、可靠性/失败语义、个人资产沉淀、第二领域扩展和有界产品路线。
- 初稿曾推荐 Decision Desk 作为产品主界面；2026-08-31 经官方 Cordis/client plugin 代码结构复核后已由 ADR-0013 修订为 DSH Web 交互主壳，保留 Decision Desk 管理后台，不 clone 或长期 fork DSH 前端。
- 明确当前仍为 Fixed active、DSH candidate/shadow、Replay 诊断态；只新增文档和索引，未开始 `DSH-LIVE-VALUE-CLOSURE` 代码，等待 owner confirmation。
- 2026-08-31 基于 DSH `master` 的 Cordis/Web/Session/Trajectory/插件源码结构复核，修订前端方案并新增 [ADR-0013](docs/decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md)：DSH Web 改为交互主壳，Hub 以官方 `dsh.bundle`/`dsh.client` Host+Client plugin 接入，Decision Desk 收敛为运营/账本/评测后台；现有 Python SDK 适配器保留为 canary/replay/fallback。
- 明确上游同步采用固定 package/image 或可选 Git submodule + 独立 overlay，不复制/fork DSH 前端；升级必须通过 Web/Session/JSONL/Tool/Subagent/plugin contract、Hub replay/PIT/Gate 和 owner review，不能承诺 alpha 上游零适配替换。
- 收口 DSH-first 总方案中的旧部署和任务顺序：首期拓扑显式包含固定上游 DSH Web 与原生 Host/Client plugin；下一阶段先做 `LIVE-00` 上游 Web 契约基线、`LIVE-01` 最小原生插件和 `LIVE-02` durable Host bridge，再进入真实市场能力 canary，避免继续沿 SDK-only/Decision Desk 主界面实现。
- 新增 [DSH 与 Decision Hub 系统总装设计](docs/product/DSH_HUB_SYSTEM_ASSEMBLY.md)，独立说明为什么 DSH 外仍保留 Trigger/Durable Run、Trust Boundary、Ledger/Evaluation 和 Operations，锁定 DSH 内层 Agent Loop 与 Hub 外层产品生命周期、两个前端职责、当前/目标代码树、三类状态数据和迁移收敛条件。
- Owner 于 2026-08-31 接受 ADR-0012/0013 和系统总装设计；新增 [DSH Native Web Product Core Stage Charter](docs/stages/DSH_NATIVE_WEB_PRODUCT_CORE.md)，授权 NATIVE-00 至 NATIVE-05 实现和离线自测，锁定上游 commit `0a53fb55bea101816fa226bb964ae2bed71c343b` 与源码 tar SHA-256，明确 npm CLI rc2 与源码 alpha2 不得混装宣称兼容。

### Product failure retrospective and DSH-first rebaseline proposal (2026-08-30)

- 新增 [产品失败复盘与长期工程教训](docs/retrospectives/RETRO-2026-08-30-PRODUCT-FAILURE-AND-LESSONS.md)，记录当前产品不可用的事实、目标漂移根因，以及以后新产品必须执行的价值门、运行态、文档和止损规则。
- 新增 proposed [ADR-0012 DSH-first 产品重新收口](docs/decisions/ADR-0012-dsh-first-product-rebaseline.md)：DSH 作为唯一研究执行 Harness，Decision Hub 保留 Evidence/PIT/Gate/Ledger/Outcome，先以真实 DSH 基线证明价值，再进行薄集成；owner 确认前不开始代码、不切 active pointer。
- 明确区分 `implemented`、`candidate`、`active`、`live_verified`、`useful` 和 `product_ready`，不再用工程阶段 `done` 代替产品可用。

### G1/G2 frontend smoke closure (2026-08-30)

- 按“API 健康后再启动 worker”的顺序重启隔离实例 `8030/5175`，从 Decision Desk 提交研究文本并取得真实页面截图、API Run 详情和 12 条 SSE 轨迹；结果为 `research_only`、17% hard coverage、5 个 hard gap、`critical_data_unavailable`，没有方向性 Horizon。
- 最终离线质量门通过：Python `296 passed`、Research acceptance `26 passed`、Pilot `15 passed`、Live Observation `42 passed`、Ruff/Pyright、canonical contract、13 个模块文档、前端 `9 passed`/build、Compose config、Alembic head `0021` 和 `git diff --check` 全部通过。
- 记录首次并发迁移竞态：API 与 worker 同时初始化全新 SQLite 时 worker 可能因 `table events already exists` 退出；Compose 健康依赖可规避，手工启动必须先等待 `/health/ready`。跨进程 migration lock/一次性 migrate job 作为后续启动可靠性任务，未在本轮引入未经跨平台验收的补丁。
- 结果仍只证明本机离线/replay 产品链路，不证明实时网络检索、事实充分度、预测准确率、盈利或 DSH Promotion；G2-C live Search 继续等待 owner 授权。

### G1/G2 frontend runtime smoke and fail-closed correction (2026-08-30)

- 完成隔离 `API 8030 + research worker + Vite 5175` 的浏览器端到端 smoke：文本从页面入队，经 durable replay worker 完成，Query/View 与 SSE 收口一致；页面明确显示 `replay`，不把 fixture 当实时检索。
- 修复 Decision Desk API target/base URL 的统一配置，保留 Inbox 的 R0 `/v1/observations` 与 Research 的 `/v1/research/observations` 两条语义；Vite 支持 `VITE_API_PROXY_TARGET`，同源生产默认不变。
- 修复研究结果在 `final_coverage=insufficient` 或非 `sufficient` stop code 时仍可能发布方向性 Forecast 的根因：研究图和 ledger commit 双层 fail-closed，Artifact 只能 `research_only/reject`，Forecast 强制 `no_trade`。
- 修复 replay worker 的注入时钟未贯穿 Request Factory/Commit 的日期漂移问题；完整离线 pytest 恢复为 `294 passed`，并增加长任务 heartbeat 与未知 `/v1` 路由 JSON 404 回归。
- Compose 默认研究 runtime 改为离线 replay fixture；Docker 镜像安装已审计 DSH extra，但真实 DSH/Search 仍需显式 canary 和 owner gate。

### R2-R-07 Search reliability proposal (2026-08-30)

- 记录 R2-R-06E 隔离真实 Run 的工程失败：模型可填写可信 `observed_at`，PIT 拒绝被粗略归类为 `provider_timeout`，并行 MCP 首个失败会 abort 其他调用，失败 Run 的终态在前端投影不完整。
- 新增 proposed 阶段卡 [`R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md`](docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)，限定 server-owned PIT time、结构化 error provenance、并行 capability 隔离和失败状态展示；不新增 Agent Loop、搜索协议、数据源、ASR/PPT/第二领域或 active pointer 变更。
- 当时记录为 owner Stage Gate；后续 G1/G2-A/B 已在本 Unreleased 节下方完成离线实现，但不代表实时搜索已稳定或 DSH 已 Promotion。

### R2-R-07 G1/G2 offline implementation (2026-08-30)

- 完成 G1-A/B/C/D：服务端 PIT 时间、结构化 error provenance、并行 capability 部分成功保留，以及失败 Run 的 API/UI 投影；失败不会被统一压成 timeout，历史 Run 不改写。
- 完成 G2-A/B：`crypto_macro` 六类最低事实包的来源 manifest、优先级/鲜度/权限/回退和 success/stale/provider_failure replay fixture；普通 CI 不访问网络。
- 新增并接受 [ADR-0011](docs/decisions/ADR-0011-research-reliability-fact-boundary.md)，锁定 server-owned PIT、ErrorProvenance、并行部分成功和事实覆盖边界。
- 离线质量门在本轮修复前曾出现 `293 passed, 1 failed` 的日期依赖回归；修复注入时钟后恢复为 `294 passed`。G1/G2 专项测试、Ruff、Pyright、canonical codegen、module docs、前端 test/build、migration `0021` 和 diff check 需以本轮最终命令证据为准。
- G2-C 一次真实 Search canary 与 G2-D 事实充分度验收仍待 owner 明确确认；Fixed 保持 active，DSH 保持 candidate/shadow，不代表实时稳定、预测准确或盈利。

### Product completion plan (2026-08-30)

- 新增 [`docs/product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md`](docs/product/PRODUCT_COMPLETION_AND_FUTURE_PLAN.md)，把已知问题和后续工作收口为 G1-G6 六个有限 Gate，并明确个人可用试点、Promotion、ASR、第二领域和规模化部署的进入条件。
- 规定每个 Gate 结束必须产生 `promote / retain / stop` 结论；没有真实价值证据的功能不自动进入下一阶段。

### R2-R-06D/E real-event acceptance and baseline retention (2026-08-30)

- 收口项目宪章、Roadmap、Implementation Status、Platform/Domain README 和当前决策索引的状态投影：R2-R-00 至 R2-R-06E 标记为已完成，Fixed 保持 active，DSH 为 candidate/shadow，后续 Search reliability/error provenance 仍需新的 owner gate。
- Warsh Jackson Hole 真实事件建立 durable DSH Run `run_27acb7c425914bc7a69060637ea1feb3`；DSH `gpt-5.5` 主动识别证据缺口并调用 `web.search`，Search 在 20 秒 capability deadline 内失败，产品以 `critical_data_unavailable`/`reject` fail-closed，保留 Trace、双 Snapshot、Result 和 Artifact，未写入未认证 Evidence、未修改 active pointer。
- 生成 `docs/evaluations/R2-R-06E_RUNTIME_DECISION.json` 与中文 Markdown 决策包；基于 12-case 对照和真实事件结果报告 `retain_baseline / pending_owner_review`，DSH 继续 candidate/shadow。
- 修复 Research Request Factory 对超长官方原文的输入投影：送入模型的 `ResearchInputEvidence.excerpt` 限制为 4000 字符，完整 Observation/Trigger Snapshot 与 content hash 保持不变；新增长文本回归测试。
- Owner usefulness 仍待人工填写；下一候选仅允许在新的 owner gate 下聚焦 Search reliability/error provenance，不扩大到 ASR、PPT、第二领域或自动交易。

### R2-R-06C DSH synthesis boundary and canary hardening (2026-08-30)

- ADR-0010 将 DSH 模型输出收缩为 `research-synthesis-candidate.v1`；Session、Trace、Tool、Evidence、Coverage、时间戳和计数由 adapter 从可信运行事实组装，跨 Session、篡改或越权 Evidence 以 `dsh_evidence_unattested` fail-closed。
- 三次同 case 失败 Canary 全部保留；第三次已正确分类为 `provider_timeout`，并暴露 replay 参数越权、无用 todo 调用和高推理耗时，没有登记评测资产或修改 active pointer。
- 当前 06C candidate profile 锁定 `reasoningEffort=low`、canonical replay 参数和 synthesis-only 输出，hash 为 `decision-research.v1:faf1b3115f7d339c`；R2-R-01 历史 profile hash 保留不改写。
- 第四次同 case Canary 已在 56.5 秒内完成，取得 2 条 attested Evidence，PIT/unattested 为 0，三个 Horizon distinct；随后以 TDD 修正 replay PIT 时间锚点和 canonical Round 工具轨迹投影，放行 12-case。
- 完成 structured-repair 后的追加 12-case 对照 `r2-r-06c-20260830-repair-full-12case`：Fixed `12/12`，DSH `9/12`；DSH 失败为 `dsh_evidence_unattested`、`dsh_session_incomplete`、`provider_timeout`，PIT violations 为 `0` 但 unattested Evidence 为 `1`。失败样本、原始报告和 active pointer 未变均已保留；该结果只允许进入 R2-R-06D，不构成 Promotion。

### R2-R-05 research observability and command center completed (2026-08-30)

- 新增 migration `0020_research_observability`，把规范化 Research Result、连续 Trace 和 owner command 纳入业务账本；DSH raw JSON、Provider payload 和 LangGraph state 不进入产品事实。
- 新增 Research Query/View/API、`after`/`Last-Event-ID` 增量 SSE，以及幂等 cancel/retry/recheck/feedback；retry/recheck 创建 child Run，不改写历史父 Run，已取消 Run 不允许继续提交。
- Research Result 与 Artifact/Forecast/Outbox/recheck 在同一事务提交，故障注入证明不会留下半提交；research worker heartbeat 纳入 Operations。
- Decision Desk 默认进入 Research Command Center，显示 durable Run、Plan/Tool/Evidence/Sufficiency、主/反根因链、独立 30m/24h/72h、停止原因、规范化 Trace 和 owner command；scheduled child 保留但不遮蔽主报告。
- 退出证据：Python `251 passed`、research acceptance `23 passed`、前端 `8 passed` 与 production build、Ruff、Pyright、canonical codegen、module docs、跨进程 recovery、Compose config、`git diff --check` 全通过；375/768/1024/1440 无横向溢出且浏览器控制台无错误。当前进入 R2-R-06，仍不切 active pointer 或宣称 U2.1/盈利。

### R2-R-04 durable research boundary completed (2026-08-29)

- 完成真实 Python 子进程 checkpoint recovery：模拟 Graph 完成但 Artifact 尚未 commit 的崩溃点，lease 到期后新进程恢复且只写一份 Artifact/Evidence/Outbox，第二次 worker 返回 `no_run`。
- 新增显式 DSH/replay runtime 选择的 fail-closed 测试；修复人工文本缺少 `published_at` 时 Research Request Factory 无法启动的问题。
- 修正双时间边界：Trigger Snapshot 继续冻结触发事实，Decision cutoff 随可信 Research Runtime 完成时间推进，触发后取得的合法新证据不再被误判为 future leakage。
- 将 Evidence/Snapshot 实例身份按 research session/Run 隔离，同时保留全局内容 hash；baseline、research 和 recheck Run 可使用同一材料而不撞主键。
- 复用现有 Run 队列实现 `available_at + parent_run_id` durable recheck；研究 commit 在同一事务内最多创建一个幂等 scheduled child Run，到期前 worker 不可领取。
- R2-R-04 退出证据：Python `243 passed`、research acceptance `23 passed`、迁移 head `0019_scheduled_research_runs`；当前进入 R2-R-05，仍不切 active pointer 或宣称 U2.1/盈利。

### R2-R-03/04 research worker partial implementation (2026-08-29)

- 修正 `DurableResearchWorker` 的公开 Kernel 持久化边界：移除动态导入、裸 SQL 和重复状态写入；成功事件记录 runtime/session、轮次、工具调用、子 Agent、成本和 Decision Snapshot，失败事件记录稳定错误码。
- 新增 `hub-worker --role research` composition，按 `strategy_version=research.v1` 原子领取 admitted Run，执行 bounded evidence-round graph，将双 Snapshot、Evidence、Artifact、30m/24h/72h Forecast 和 Outbox 接入既有账本。
- 新增 worker TDD 场景：同一 session gap continuation、成功投影、Provider failure、终态幂等和 heartbeat；当前仍是 `partial`，正式研究主链切换、真实进程重启演练、Research UI 和 R2-R-06 live/value gate 未完成。
- 自动 discovery 已按高影响事件创建独立 `research.v1` Run；新增 `research-mcp` 与 `hub-research-worker` Compose 服务，研究 capability allowlist 由显式环境变量传入，默认仍为 `replay.research`。
- 修复 research adapter 的公开导出、静态检查和严格类型问题；当前质量门：Python `231 passed`、目标模块 Ruff/Pyright、canonical codegen、模块文档检查、前端 `7 passed/build` 和 `git diff --check` 通过；全仓 Compose 配置检查通过。
- 新增 `tools/research_acceptance.py` 作为 R2-R-04 的离线退出门；明确其不启动 DSH、不访问外网、不把 candidate capability 视为已授权。

### R2-R Research Agent Pilot authorized (2026-08-29)

- Owner 接受研究智能体主体产品规格、ADR-0008、ADR-0009、平台/领域边界和 R2-R Stage Charter，并授权完成 R2-R-00 至 R2-R-06 的完整 U2.1 大阶段。
- 当前唯一目标是完成真实 DSH Harness Agent Loop、主动补证、双 Snapshot、durable research worker、人可读前端和 replay/live 验收；从 R2-R-00 canonical contract、Warsh 失败 fixture 和失败测试开始。
- DSH 精确版本/profile 在 R2-R-01 canary 后锁定；真实插件/来源继续 deny-by-default；R2-R-06 前不切 active pointer，不宣称产品可用、预测优势或盈利。

### R2-R-02/03 evidence boundary and bounded graph (2026-08-29)

- R2-R-02 离线完成：Web/Official/Market capability gateway、EvidenceCandidate lineage、PIT/freshness/authority/hash/fallback 校验和 Trigger/Decision 双 Snapshot；Decision Snapshot 保留 parent Trigger evidence，外部能力仍 deny-by-default。
- R2-R-03 核心已实现：确定性 freshness/authority/independence/conflict Sufficiency Gate、LangGraph bounded evidence rounds、同一研究请求的 gap continuation、no-progress/budget stop、JSON-compatible checkpoint state 和 horizon distinctness fail-closed。
- 当前仍未完成 API 正式主链切换、自动 discovery、Research Command Center/SSE、recovery acceptance、PIT replay/live 对比和 owner acceptance；R2-R-06 前不宣称 U2.1 可用。

### R2-R-01 DSH Research Harness completed (2026-08-29)

- 固定 `deepseek-harness-sdk==0.1.1rc1`、bundled runtime server `0.0.1` 和 restricted `decision-research.v1:1d4ce1f40ab265e4` profile；默认禁用 shell、文件系统、sandbox 和任意插件安装。
- 新增独立 `ResearchHarnessRuntime` port 和真实 DSH adapter，覆盖 Session 生命周期、Tool/Subagent/Step trace 归一化、严格 `ResearchSessionResult`、可信运行时计数、deadline/timeout 和 fail-closed 错误边界；旧 callable seam 保持冻结。
- 本地 bundled runtime handshake 与真实 gpt-5.5 canary 通过：3 tool calls/results、1 subagent、2 turns、4 steps、Session 文件落盘且 `finish_reason=completed`；密钥未进入配置、日志、fixture 或文档。
- 退出质量门为 Python 187 passed、Ruff、Pyright、canonical codegen、13 个模块文档、前端 7 passed/build 和 `git diff --check` 全通过。当前进入 R2-R-02；此结果不证明主动补证、预测优势或盈利。

### R2-R-00 contracts and failure baseline completed (2026-08-29)

- 新增 `agentic_research.schema.yaml`，从单一真源生成 Python/TypeScript/Zod 的 Product Extension、Domain Pack、Role/Capability、Evidence round、CausalCase、HorizonDecision、Trace/StopReason 和 Research View/Command 契约。
- 新增真实 `packs/crypto_macro/`，锁定根因链、最低证据包、Manager/反方/Data Quality 角色、deny-by-default Tool binding、30m/24h/72h Gate、评测 rubric 和 3 round/12 tool/180 秒预算。
- 将真实 Warsh 运行固化为失败 fixture，自动证明 hard evidence 缺失后零工具调用、三个 horizon 完全复制和 52% 无校准来源，防止后续把固定问答工作流重新包装成智能体。
- R2-R-00 退出证据：Python 170 passed、Ruff、Pyright、canonical codegen、module docs、前端 7 passed/build 和 `git diff --check` 全通过；当前进入 R2-R-01，仍未宣称真实 DSH 已接入。

### 研究智能体主体产品规格 proposed (2026-08-29)

- 新增独立产品规格，明确主体是主动发现、持久任务、证据缺口驱动补证、确定性 Gate 和人可读报告组成的 Research Agent；聊天只作为人工提交、追问、补充和 owner command 入口。
- 锁定 Research Command Center、运行中研究详情、最终报告、来源/能力健康、SSE 进度和 durable recheck 的前后端产品形态，禁止用 raw JSON、固定工作流或聊天 Demo 宣称智能体完成。
- 明确 ASR 暂不实现，只通过未来 `AsrProviderPort -> TranscriptSourceAdapter -> TextEnvelope` 接入同一文本核心；主体 R2-R-06 通过后再立独立阶段。
- 澄清双层插件模型：Product Extension 拥有业务契约/历史/评测，DSH Native Plugin 提供 Harness 能力；Extension 可以附带薄 DSH bundle，但 DSH Session 不成为产品账本。
- 本条只有文档与边界收口，未开始 R2-R 代码、未安装 DSH、未修改 active pointer，仍等待 owner gate。

### 通用产品平台边界审计 proposed (2026-08-29)

- 基于现有代码、DSH 官方架构/Python SDK/安全说明和 `crypto-macro-decision` Skill 完成平台审计；确认现有代码可演进，但当前仍是金融决策产品底座，不应宣称已经完成 PPT 等跨产品隔离。
- 新增 proposed 平台基线、资产/扩展模型、Crypto Macro Domain Pack 设计和 ADR-0009，锁定 `Platform Core -> Product Extension -> Domain Pack -> Role Profile -> Capability Plugin`。
- 明确 DSH 是 R2-R 拟议的 Agent 执行主线而不是 Product Core；LangGraph 只保留产品外层生命周期，DSH Session/JSONL 与业务 Ledger 保持双真源。
- 新增 `docs/context/` 当前状态、当前决策和交接协议，避免继续向总架构末尾追加讨论，并为长上下文恢复提供固定入口。
- 本条只有文档和架构审计；未安装 DSH、未开始 R2-R 代码、未迁移金融表、未实现 PPT、未修改 active pointer，仍等待 owner gate。

### R2-R Agentic Research Runtime proposed (2026-08-29)

- 真实 Warsh 事件验收确认当前正式主链是固定 policy/counter/synthesis LLM workflow；发现数据不足后没有工具、真实搜索/行情 binding 或 continuation，三个周期输出完全重复。该结果不能作为研究智能体完成证据。
- 新增 proposed R2-R Stage Charter 与 ADR-0008：选择 DSH Python SDK + 基于完整 `sdk`/base 能力集的受限 `decision-research` profile 作为首个真实 ResearchHarnessRuntime candidate，LangGraph 管理产品生命周期/双 Snapshot/充分度/Gate，DSH 管理内层工具/子 Agent loop。
- 方案锁定 Trigger/Decision 双 Snapshot、Web/Official/Market 三类能力、durable research worker、分周期独立决策、人可读研究轨迹、Fixed vs DSH 评测和 R2-R-00 至 R2-R-06 退出门。
- 本条只记录架构纠偏和文档，不包含 R2-R 代码、DSH 插件安装、active pointer 变更或真实网络授权；等待 owner gate。

### R2-L Live Observation Pilot implementation (2026-08-29)

- 完成 durable Evolution Job/lease、trigger scanner、heartbeat 和 realtime/evolution 两个独立 worker role；API、两个 worker 共享 SQLite WAL，但职责和故障域分离。
- 完成受控 SearchCapabilityPort、manifest/权限/域/预算/PIT fail-closed 校验、fake transport 和 OpenAI-compatible canonical adapter seam；默认不联网。
- 完成 Operations/Evolution API 与 Decision Desk 人可读视图，移除 API 失败时的 demo fallback；本机 acceptance 覆盖重启、lease recovery、候选幂等、heartbeat 和 pending owner review。
- 离线退出门：Python 162 passed、前端 7 passed、contract/module docs/ruff/pyright/build 和 `tools/live_observation_acceptance.py` 通过；浏览器 375/768/1024/1440 无横向溢出。
- `docker compose config --quiet` 通过；`docker compose build` 因 Docker Hub/GHCR 外部 registry timeout 未取得真实镜像运行证据。真实 Provider/source/search/market/notification、预测准确率、盈利、生产 HA、自动 Promotion/交易仍未证明。

### R2-L Live Observation Pilot authorized (2026-08-28)

- 接受 R2-L Stage Charter 和 ADR-0007：使用 API、realtime worker、evolution worker 三个逻辑进程，SQLite durable Evolution Job/lease 和 heartbeat，把现有 R0/R1/R2 组成持续运行产品。
- 锁定边界：复用现有 RealtimeScheduler、LangGraph Supervisor、EvaluationRunner、EvolutionAssetService 和 Promotion Gate；开放搜索只经审计 Capability，候选只能进入 pending owner review，禁止自动 Promotion/交易/源码自改。
- 定义 R2-L-00 至 R2-L-06、BDD/TDD 故障矩阵、前端 Operations/Evolution 视图和本机三进程退出门；在代码实现前先通过文档一致性检查。

### R2 Decision Workbench v1 offline U2 complete (2026-08-28)

- R2-01 至 R2-05 已形成离线 U2 工程闭环：canonical codegen、官方 MCP stdio/streamable HTTP、Workbench/Capability 边界、Run Inspector、评测/经验资产、LangGraph Supervisor、候选 Runtime 和人工 Promotion/Rollback。
- Promotion 使用 owner-only command、确定性 Gate、generation/CAS 和原子事务；并发只有一个赢家，事务故障不会留下 pointer/candidate/audit 半状态，Agent/DSH/前端不能直接修改 active pointer。
- Decision Desk 新增 Run/Evidence/Experiment/Asset/Promotion 人可读视图和 Promote/Reject/Rollback 交互；375/768/1024/1440 四个视口无横向溢出。
- Alembic head 为 `0015_evolution_provenance`；当前离线质量门为 Python `128 passed`、前端 `5 passed`，Ruff/Pyright/contract/module docs/Core/Pilot acceptance/MCP 双 transport/前端 build 均通过。
- R2 当前进入观察期，不自动开始 R3；真实 DSH/Pi/Provider canary、长期 shadow、预测准确率和盈利能力仍未证明。

### Governance alignment (2026-08-27)

- 对齐产品架构总表与当前 R0 交付证据：明确 R0/R1/R2 边界，修正实际仓库路径，并将动态 Supervisor、六层 grader、Version Registry、Evolution 和 DSH/Pi Workbench 保留为后续阶段能力；本次仅修改文档，不改变运行时行为。
- 重新运行 R0 core acceptance，确认文档修正没有改变契约、迁移、回放、恢复、前端或安全门禁结果。

### Observation period

- 只运行、观测、记录 FailurePattern 和补充前瞻评测证据；真实插件、Live Pilot、生产 Promotion、R3 多领域或远程部署必须另立授权。
- DSH 继续作为可替换 Workbench/Harness 生态，通过 `ResearchWorkbenchPort`、`CapabilityManifest` 和 `DshCapabilityAdapter` 接入，不成为 Product Kernel 或业务账本。

### R2 in progress (2026-08-27)

- R2 Stage Gate 已接受，锁定 U2 Decision Workbench v1、R2-00 至 R2-05 和完成后观察期；ADR-0005/0006 已接受。
- R2-00 将 LangGraph graph、checkpoint 和配置组装移出 Kernel application，新增最小 `DecisionWorkflowExecutor` Port、LangGraph executor/composition root 和架构 import 回归门；R0/R1 行为不变。

### R1-L offline complete (2026-08-27)

- R1-L 单 owner 试运行就绪的代码、离线 acceptance 和文档已完成，包含脱敏 readiness 契约与 API、worker `--preflight`/`--pilot` 启动门，以及 local/email outbox 组合根。真实来源、SMTP、长期运行和业务效果仍需 Live Pilot Gate。

### Delivered (2026-08-27)

- R1 Realtime Event Engine：来源 registry、官方 feed/calendar、文本转写边界、行情基准、scheduler、到期 Outcome、local notification、健康 API 和 Decision Desk 摘要已完成离线验收。固定 fixture 覆盖 cursor/PIT、重复与修订、失败恢复、到期幂等、通知重试、迁移升级和来源到 Outcome/outbox 的端到端链路。
- R1 使用 canonical schema/codegen、Kernel-owned durable state 和 R0 的 LangGraph/Gate/账本；未新增第二个 Agent runtime、账本、队列或工作流引擎。真实网络稳定性、来源授权、预测准确率和盈利能力均不在本次验收结论内。

### Delivered (2026-08-26)

- R0 文本核心纵向链：文本 admission、PIT Snapshot、LangGraph research、确定性 Gate、Artifact、三档 Forecast、Outcome/Evaluation、事务 outbox 和 Decision Desk 最小页面。
- R0-B Provider Reliability Boundary：ProviderConfig、Responses/Chat、structured output、timeout、bounded retry、错误分类、usage/cost unknown/estimated、预算 fail-closed 和显式 canary。
- R0-C/R0-D：固定 PIT replay/holdout compare、Outcome/Brier/net return、Run/Step/Attempt/Call Inspector、checkpoint recovery/idempotent commit、Provider failure safety、SQLite backup/restore/integrity、Alembic upgrade path 和 ReleaseManifest。
- OpenAI-compatible `gpt-5.5` Responses live canary 已通过 Runtime/Graph/结构化解析/Artifact/Forecast 兼容性验证；合成输入的 Gate 结果不代表业务准确率或盈利能力。
- 全局 SDD + BDD + TDD + ADR 治理规范、Task Context Manifest 和模块文档检查已固化。
- Inspector 证据归一化：Facts/Citations 由对应 reviewer 的结构化字段提供，禁止把 synthesis 上下文 JSON 持久化或展示为用户事实。

## 记录规则

每个阶段完成后把 `[Unreleased]` 条目移动到带日期的版本节，并链接对应 ReleaseManifest、commit 和验证命令；未通过验收的能力只能写在 `Next` 或 `Known limitations`，不能写成 Delivered。
# 2026-08-31

- 新增 `docs/product/PRODUCT_IMPLEMENTATION_AND_ACCEPTANCE_PLAN.md`，固化首期产品形态、DSH/Decision Hub/插件边界、目标代码结构、C1-C7 任务卡、SDD/BDD/TDD/ADR 约束、上下文压缩规则和最终工程/产品/价值验收门。
- 修复 `infra/dsh/run-product.sh` 将宿主机绝对 replay fixture 路径注入 Compose 容器的问题；容器现在使用容器内默认 fixture，避免启动后出现错误路径。

# 2026-09-01

- 新增 `docs/product/PRODUCT_EXECUTION_AND_ACCEPTANCE_MASTER_2026-09-01.md`，将产品视图、DSH-first 两层循环、插件/领域边界、代码目录、E2-L/E3 阶段目标、SDD/BDD/TDD/ADR 约束、上下文压缩和最终验收清单汇总为独立执行入口；不新增运行时或改变历史事实。
- 新增 `docs/evaluations/FRONTEND_RUNTIME_ROOT_CAUSE_2026-09-01.md`，记录旧 `8010` v1 API 与当前 v2 前端契约冲突、全新 `8970` 隔离实例的页面到后台 replay 复验、截图和产品能力边界。
- 修复 `research-worker-replay.json` 的 `total_tool_calls` 与空 `tool_invocations/tool_results` 不一致；增加 replay 资产一致性测试，避免页面再次出现“有工具计数但没有调用轨迹”。
- 收口 E2L-02 durable progress：Research Query/View 在最终 Result 尚未生成时仍投影已保留 Evidence、工具调用、研究轮次和逐 capability ErrorProvenance；失败和取消保持 fail-closed，不伪装成空结果或 `no_trade`。
- DSH Host watchdog/terminal callback 增加并发互斥和失败后可重放语义；Decision Desk ToolActivity 展示 canonical error code、来源、cause 和 retryability。
- 验证：离线 Python `361 passed`、DSH plugin `39 passed`、Decision Desk `9 passed`，Ruff/Pyright/codegen/module docs、前端 test/build 和 `git diff --check` 通过。真实 Search timeout 和价值观察仍 pending。
