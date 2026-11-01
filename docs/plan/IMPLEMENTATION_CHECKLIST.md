# Decision Hub 核心价值实施与验收记录

本文件是当前唯一实施入口。它依据三份方案文档和当前工作区代码维护，覆盖 M01 收口、M02 核心能力增强和 M03/G1 质量裁决。`M01_EXECUTION.md` 只保留历史过程，不再决定后续顺序。M04/M05 调度只有在 G1 通过后才开始。

## 最终目标

把 Decision Hub 做成一条可重复使用的研究主链：owner 在 DSH 提交真实问题，系统取得并保存可回读的原文、基线和三个月核心背景，按实际缺口最多并行两项独立取证，独立 reviewer 核验固定版本结果，最后生成引用可回读、事实与未知清楚、不会伪装成已校准预测的报告。用六个开发案例和独立留出案例证明它比当前链减少关键遗漏或人工返工，再收敛为唯一 DSH 工作台。

## 当前目标：G1-C3 新鲜任务效果闭环

G1-C2 的接入代码、事实关系 Gate、DSH Session 回读和离线自测已经完成。下一步回到产品核心：用当前代码重新运行代表任务，检查检索是否取得足够且相关的材料、缺口是否触发有效补查、独立 reviewer 是否真实执行、最终报告是否减少关键遗漏和人工返工。缺少某类外部数据时，任务必须显示具体未知并保守降级，但开发和其他可验证案例继续进行。

分钟数据的边界固定如下：它只在问题明确要求判断公告前后即时市场反应时，作为该事件窗口的 hard requirement；三个月背景、官方原文解读、一般研究和不适用的事件不以分钟文件为前置。冻结 dev/holdout 的 US2Y/DXY 文件只影响严格历史事件回放是否满覆盖，不再作为整个核心开发目标的阻塞。没有授权数据时不得用日频收益率或相关资产伪装成分钟事实。

- [x] BTC spot、funding、OI、basis、crowding 的官方历史窗口已接入并通过 dev/holdout 回放。
- [x] 可用 Kalshi 历史合约的 expectation baseline/t-5m/delta 已接入；没有对应预测合约时，授权 US2Y 文件可提供前一日基线到 t-5m 的利率重定价 fallback。
- [x] US2Y + DXY 的授权 ASCII 文件接入、事实构造、Replay lineage 和语义 Gate 已完成并自测。
- [x] 独立 cross-asset 使用 CME E-mini S&P（ES）分钟档案，产出 `cross_asset.equity` 的 `t-5m/t+1m` level/event return，并穿过同一 Replay/Pack Gate。
- [x] 修复所有事件共用同一组 hard requirements 的策略错误：`geopolitical_shock` 在美债、USD 和上市跨资产闭市时仍请求 expectation/macro 作为软证据，但只由 24/7 可观测的 BTC spot/derivatives 与事件本体/变化阻断；央行、通胀和就业事件保持原硬门。
- [x] DSH 在最终模型步骤失败或总时限取消时仍可回读此前已成功、已验真的 research tool Evidence/facts；终态保持 failed/cancelled，业务结果为 degraded，未完成的因果和方向结论一律丢弃。
- [x] DSH 原生 reviewer 回执已从真实 `subagent` call/result 投影为结构化 Review；只接收同 Session、成功结果、闭合 JSON 结构和已保留 Evidence ID。`review_id` 由回执内容 hash 验真，`result_hash` 单独绑定最终研究结果版本。质量评测不再把只有 `total_subagents`、没有可信 Review 的 DSH 结果判为成功。
- [x] 正式 Durable Worker 不再用本地模板 Review 覆盖 DSH 原生回执；原生回执会校验 Evidence refs、回执 ID、最终 result hash、target thesis 和时间边界，并将同一 Review 写入 `research_results` 与 `run_role_outputs`。无原生回执的运行仍使用保守 fallback。
- [x] DSH 原始 Session JSONL/Zstandard 归档已能按首条 header 绑定 Session 身份并复用在线证据/事实/Review 映射；多 Zstandard frame、header 冲突、读取上限、canonical request 尾随内容和失败 reviewer 状态均 fail-closed。
- [x] 核心数据集构建会生成带 hash 的逐案例 `data_coverage.json`，列出每个 hard/soft requirement 的实际 fixture、来源、事实数、观测时点、缺口原因和授权文件所需时间槽；最终候选可用 `--require-full-hard-data` 阻止部分覆盖版本发布。
- [~] 冻结案例的完整授权 US2Y/DXY 分钟文件尚未取得；取得后补做满覆盖的严格历史事件回放。该外部数据项不暂停当前核心开发。
- [x] 用当前代码新跑代表性 DSH 任务，逐项核对 Evidence/facts、缺口驱动补查、可信 Review、引用回读和最终报告；失败后直接修主链。
- [x] 将新鲜运行扩展到 dev/holdout，并对照 baseline 复核质量成功、关键遗漏和人工返工；两边均 6/6 完成取证但质量成功 0/6、人工返工 6/6，候选硬覆盖高于 baseline 且安全项不下降，尚未满足“质量或返工改善”条件，因此 G1 继续 `retain_baseline`。
- [x] 已用真实 DSH Web/Hub recovery 验证 callback 断连、同一 Run/Session 复读、Host 重启和锁定版本回滚；真实 DSH reviewer `requires_new_evidence` → 新 revision → query 已通过生产 `LangGraphResearchExecutor` 验证；浏览器刷新也能从 canonical Inbox 自动恢复同一受管 Session，Run snapshot 与 `latest_sequence_no=78` 前后一致。

状态口径：核心技术链、真实 reviewer 补证和浏览器恢复已完成验证，但产品效果尚未达到 G1 promotion 门槛；因此保持 `retain_baseline`，后续核心输入只剩授权数据或从现在开始积累的新事件窗口，继续停止 M04/M05 调度扩展。

## 当前代码事实（2026-09-13）

| 能力 | 当前证据 | 判定 |
|---|---|---|
| 真实 DSH 主链 | `core-v3-live-dsh-fomc-20241107-review-receipt` 从 482-event Zstandard Session 归档恢复 5 条 Evidence、17 个 facts、1 份 `bounded` 原生 Review，并完成 provenance/hash 回读 | 证据链可复现；因 `macro_transmission` hard gap，结果仍为 `degraded/research_only`，不产生方向性结论 |
| 正文读取 | Waller 正文和基线均有 content-addressed ref；同一 Gateway 支持 query 回读 | CV1 已完成，篡改/跨 Run/未知 ref 有拒绝测试 |
| 任务策略 | TaskPolicy 按研究模式和 requirement 生成并带 content hash；ContextManifest 记录选择 | CV3 已落地；原生 DSH Web candidate 已真实执行按 gap 分项取证 |
| 历史数据 | DataJob、分区、coverage manifest 和 Gateway 查询已落地；Binance Vision/Kalshi 核心回放已完成；授权 Portara/CQG US2Y+DXY 文件入口已穿过 Replay/Pack Gate；新版本构建同时产生逐案例覆盖报告并可启用 hard-data 发布门 | 冻结案例的完整 US2Y/DXY 分钟文件尚未取得，不能把接入能力计为数据覆盖 |
| 独立研究 | Review 固定最终结果 hash 并持久化为 role output，DSH 计划最多两项并行；原生 subagent 回执经 call/result、JSON schema、Evidence 白名单和 target thesis 投影，正式 Worker 保留原生回执；原始 Session 归档回读先验证 header/session identity | CV3 已落地；真实 482-event 取消 Session 可从权威 `.zstd` 恢复回执；另一次真实 DSH Web 运行由原生 `requires_new_evidence` 回执触发第 2 个 generation，Review 引用的 `replan`、修订提示词和后续 4 次缺口查询均可回读；正式 Worker 不再只统计 `total_subagents` |
| 上下文 | 每轮按 relevance/authority/recency 选材，保留排除原因，替换固定前缀投影 | CV3 已落地并有定向测试 |
| 质量验证 | 六例 dev + 六例 holdout manifest/hash 冻结；core-data-v3 已从原始 v1 重建并独立保存，加入按事件类型选择要求的 Pack policy；12 个真实 DSH Session 均已从原始归档按 manifest 身份恢复，并以当前代码重新 fresh comparison | fresh dev/holdout 的 DSH Web 均 6/6 完成，证据覆盖为 0.806/0.694，PIT/unattested 均为 0；当前 `independent_review_receipt` 门下两边质量成功均为 0/6、人工返工均为 6/6。授权 rates/USD 缺口和个别模型未返回可信结构化 Review，继续保留停止线 |
| 用户界面 | DSH 同页有输入、Inbox、报告、ContextManifest、Review、DataJob；用户流无 Desk 链接；刷新从 Inbox 的 canonical `dsh_session_id` 自动恢复受管 Session | CV5 页面、原生 Host、浏览器 snapshot+cursor 恢复均已真实运行验证 |
| G0 | 已有部分幂等、预算、取消、租约和提交代码/测试 | 未逐项重验；只随真实主链暴露的缺口修复，不另开平台项目 |

## 连续实施顺序

### CV1：证据资产与统一回读（M01 收口 + M02 起点）

- [x] 为 `EvidenceCandidate` 增加不可变 `content_ref`，Research Evidence 表持久保存；迁移兼容旧库。
- [x] HTTP 文档 adapter 将获准保留的完整正文写入内容寻址存储，Evidence 仍只把相关片段放进模型上下文。
- [x] 新增同一 Gateway 下的全文回读能力：按 evidence/content ref + query 读取新片段，不重新联网，不把本地路径暴露给模型。
- [x] 保留 published/observed/received、最终 URL、正文 hash、parser version；原文与基线都能回读。
- [x] 用 Waller 讲话后半段和基线做真实回读验收。

验收：引用能定位到持久正文；不同 query 可从同一正文取到不同相关段落；篡改、跨 Run、未知 ref 均拒绝；现有 Gateway/PIT/许可测试继续通过。

### CV2：三个月核心数据与手动 DataJob（M02）

- [x] 新增 canonical DataJob/Partition/CoverageManifest，沿现有 SQLAlchemy 数据库和 QueryService 落地，不新建第二套队列。
- [x] owner 手动创建一项 backfill；它不创建 Research Run、不触发通知或历史新事件。
- [x] 首批仅加载实际可取得的数据：BTC 小时 OHLCV、funding/OI 的实际历史覆盖、FRED DGS2/DGS10/DTWEXBGS 原频率数据。
- [x] 自然月窗口、指标预热窗口分开；分区不可变，manifest 原子发布，记录行数、单位、频率、合约类型、最早/最晚时间、发布时间精度、修订性和缺口。
- [x] 历史查询和实时刷新都经既有 ResearchCapabilityGateway；不能取得的 OI/预期/分钟宏观明确标缺失。
- [x] DSH 任务面板显示 DataJob queued/running/partial/failed/completed、覆盖和错误。

验收：一个真实三个月作业可重跑且幂等；分区/manifest hash 可校验；actual coverage 与真实返回一致；没有自动研究副作用。

### CV3：任务、上下文、两项取证和复核（M02）

- [x] 将关键词判断升级为版本化 TaskPolicy，按问题选择必要事实、任务和预算。
- [x] ContextManifest 记录 selected/effective 输入、排除原因、规则/工具/数据版本和指纹；替换机械 `[:4000]`/前 50 条。
- [x] 原文研究与市场/历史数据研究的计划依赖和提示最多允许 2 项并行；Task/Attempt/receipt 的现有轨迹和 result hash 已保留，原生 DSH Web 已真实运行验证。
- [x] reviewer 只读取固定结果版本并保存 challenge/resolution；真实 DSH Web 经生产 `LangGraphResearchExecutor` 收到 `requires_new_evidence` 后生成 Review 引用的可见 replan，把 challenge/limitations 写入第 2 个 generation 的修订提示词，并执行 4 次缺口查询；查询无 fixture 时保持结构化 missing，不生成伪证据。
- [x] 最后综合使用新的干净上下文；统一 sufficiency Gate 已接入 relationship Gate，按 metric family/instrument/venue/source 分组验证 baseline→after，同一组缺失或错配时拒绝覆盖；对带窗口元数据的 `event_return`/`open_interest_delta`/`delta` 派生事实保留合法配对路径。
- [x] Review 作为结构化资产保存 role、target claim、evidence refs、challenge、resolution、限制，不能只靠 `total_subagents=1`。

验收：首轮不足能够换新目标补证；一个来源失败不会取消另一个独立任务；错误基线/单位拼接不能过门；最终报告中的每个关键引用可回读。

### CV4：六个开发案例与独立留出（M02→M03）

- [x] 从现有 fixtures 固定六个开发案例，覆盖资料齐全、首轮不足、矛盾/旧信息混入三类，每类至少两个。
- [x] 保存当前 baseline 输出与人工缺陷，再跑 candidate；比较事实正确性、时间/数字、引用支持、关键遗漏、人工返工、耗时和已知成本。
- [x] 修正 comparison runner：`completed/degraded` 不等于质量成功；增加引用支持和 hard-gap/人工返工判定。
- [x] 固定至少一组未参与开发调优的 holdout，冻结 manifest/hash 后再运行。
- [x] 在线取证对照与固定证据分析对照已分开；Windows SDK wheel 缺失已由锁定的原生 DSH Web Host 路径绕过，完整 dev 与独立 holdout 均已真实运行并保存逐例结果。

验收：六个开发案例有逐项变化记录；holdout 无 PIT/引用违规；至少一个核心质量或返工指标改善，且安全正确性不下降。

### CV5：唯一工作台、必要诊断与 G1（M03）

- [x] 在 DSH 同一页面完成研究输入、当前任务、证据回读、Review、报告、失败/取消和 DataJob 覆盖查看。
- [x] 刷新从 canonical Inbox 的 `dsh_session_id` 自动选择受管 Session；真实 Edge 刷新前后 Run/Session、Trigger/Decision Snapshot hash、Evidence/Review 集合和 `latest_sequence_no=78` 完全一致。
- [x] 迁移仍有价值的 Desk 管理功能；用户流中的 Desk 链接和重复前端入口已删除。
- [x] 选定一条技术 Trace 路线并按 Run/Task/Attempt 关联；Trace 缺失不影响业务状态和报告。
- [~] 取消、迟到提交、恢复、版本固定和备份已有回归覆盖；仍需真实 DSH 运行验证迟到/恢复组合。
- [x] 形成 G1 裁决：`retain_baseline`。旧 v1 真实 DSH Web 平均硬覆盖约 0.278；core-data-v3 修复事实回放、事件策略和证据回读后，dev/holdout 分别为 0.806/0.694，12/12 案例均能从权威 Session 归档恢复 Evidence/facts，PIT 违规 0、unattested evidence 0；可信结构化 Review 的恢复路径已有一例真实证明。在当前 `independent_review_receipt` 门下 dev/holdout 质量成功均为 0/6、人工返工均为 6/6，因为冻结日期仍缺合规 rates/USD 分钟双源，部分 expectation 因 stale/无合约缺失，且旧 dev/holdout Session 大多没有可验证 Review；继续停止 M04/M05 调度投入。

验收：一个新用户只进入 DSH 即可完成全流程；开发集和 holdout 证据支持 G1 结论；之后才创建 M04 计划。

## G0 随主链验证矩阵

这些是每个相关切片的测试，不是独立基础设施阶段：owner/内部 callback scope；同 key 同 payload 复用、不同 payload 409；单活跃 root；lease renewal + epoch fence；取消后旧执行不能提交；结果/终态/完成事件原子；post-commit 派生失败不改终态；DB 原子事件序号；工具/模型/费用/时限有界且未知成本不记 0；远端状态未知时不盲目重派。

## 每次提交前的最小证据

1. 行为变化及关闭的真实缺口。
2. 精确改动文件和 schema/migration 影响。
3. 红测试、绿测试和真实命令输出；测试框架存在不等于行为完成。
4. 至少一个真实数据/DSH/界面产物，或准确记录外部失败。
5. 未验证部分和下一项核心工作。

## 明确不做

G1 前不做 M04/M05、日历、旧 timer 重启；不引入 Redis/Celery/Kafka、第二套 scheduler、数据库全面迁移、多租户、插件市场、自动交易、全市场数据平台、多个观测后端或拖拽编排器。

## 执行日志

### 2026-09-09：重新基线

- 三份方案重新核对后，废止“只执行 M01”和“用户确认才进入 M02”的旧 checklist 门槛。
- 当前真实 M01 输出能证明取证、subagent 和带引用综合可运行，但 `degraded/research_only`、全文不可回读、无三个月数据/质量对照，不能证明产品已经有效。
- 下一项代码工作固定为 CV1 证据资产与统一回读；完成后直接进入 CV2，不再因单个里程碑或局部测试结束整个改造。

### 2026-09-09：核心链连续实施与自测

- CV1 真实 clean canary 通过：Waller 讲话正文以 content-addressed ref 保存；同一 Gateway 用不同 query 取得原始片段和后半段片段，hash/篡改/跨 Run 拒绝均有测试。
- CV2 真实 DataJob `datajob_3c5d7fd971a74f38b3fcea6fea7cbc12`（2026-06-01 至 2026-09-01）产生 BTC 小时 OHLCV 2208 行、FRED DGS2/DGS10/DTWEXBGS 各 64 行；funding 从 2026-06-08 起为 partial，OI 无公开历史端点，均按真实缺口记录，没有伪造完整覆盖。
- CV3 新增 TaskPolicy/ContextManifest/Review 和 relationship gate；`tests/research` 180 项通过，context/review/relationship 定向测试通过。
- CV4 开发集与留出集均为六例冻结分区；`tests/evals/test_research_splits.py` 3 项通过，candidate 的 `completed` 与 `quality_success` 已分开，留出集同样纳入 denominator 且 PIT violations 为 0。比较结论保守落为 `retain_baseline`。
- CV5 DSH 页面已合并报告、上下文清单、独立复核和 DataJob 状态，前端/Host 72 项测试通过并完成生产构建；真实 DSH 评测探针返回 `dsh_sdk_unavailable`，原因是当前 Windows 环境没有 `win_amd64` 的锁定 runtime wheel。另行使用同一 `codexai.club/v1` + `gpt-5.5` 跑 OpenAI 兼容主链 canary 成功，但结果为 `degraded/research_only`，只证明 Provider/结构化链路可用，不证明研究质量。
- 随后修正了 DSH trusted plan 的依赖索引，确保第 3 项起依赖前两项之一，依赖感知执行器最多同时暴露两项；相关 runtime/research/eval 回归为 265 passed、1 skipped，前端最终为 72 passed。全仓 `pytest -q` 的核心代码仍通过，但 Windows POSIX launcher、默认代码页、MCP 进程启动和既有 evolution 临时文件测试共留下 10 个环境/既有隔离失败，未将其误报为 G1 证据。

### 2026-09-09：原生 DSH Web 真实 candidate 恢复

- Docker Desktop 的两个损坏 Unix socket 重解析项已隔离，避免 Inference/Secrets manager 启动即崩溃；Docker engine 仍不作为评测前置，Hub API 与锁定的 DSH Web 改用本机正式进程运行。
- 修复 `run-web.sh` 混用 pnpm 版本：上游 profile 继续使用锁定的 11.7.0，仓库插件按根 `packageManager` 使用 11.19.0；同时让 DSH home lock 在 MSYS2 与 Linux/WSL 都能生成进程启动签名。
- R2-R runner 新增 `dsh-web` candidate：每个 PIT case 启动固定端口的隔离 Research MCP，创建 Host bridge 所需的 durable evaluation Run，并注入本地 tool key；无需 Windows `deepseek-harness-runtime-bin` wheel。
- 修复 Fixed baseline 在 Windows 上成功后因 SQLite pool 未释放而被误报为 `fixed_baseline_failed`；单步模型时限由 60 秒调到 120 秒，仍受 case 总时限约束。
- 修复 Host 终态投影：`decision_hub_synthesis_submit` 已成功时，Provider 尾随 `EMPTY_RESPONSE` 不再覆盖已验证的结构化结果；普通 turn error 仍 fail-closed。Host/客户端测试 73 项通过。
- 真实 canary `cv4-native-canary-final`：Fixed baseline 0 条新 Evidence、硬覆盖 0；DSH Web 取得 2 条官方 Evidence、硬覆盖 0.333、引用支持 1.0、三周期区分通过、1 个 reviewer subagent，因 fixture 本身缺少其余硬事实而如实 `degraded/critical_data_unavailable`。

### 2026-09-09：真实 dev/holdout 对照与 G1 裁决

- 开发集 `cv4-native-dev-6`：Fixed 6/6 完成但新 Evidence 为 0、硬覆盖 0；DSH Web 5/6 完成，取得 10 条 Evidence、平均硬覆盖 0.278、引用支持均值 0.833、5 个 reviewer，1 例 `dsh_model_step_timeout`。两者质量成功均为 0/6，人工返工均为 6/6。
- 留出集第一次 `cv4-native-holdout-6` 恰逢 Provider transport 故障，Fixed 与 DSH 均全部失败，作为外部故障诊断保留，不纳入 G1。端点恢复后，简单 `gpt-5.5` Chat Completions 返回 200；错误映射新增 `provider_transport_error`，不再把连接问题归为 `unknown_runtime_error`。
- 修复评测路径关闭 durable progress 导致工具计数恒为 0 的问题；replay-only MCP 现在可由 DSH Web 评测显式接入既有 Hub 预算账本，SDK smoke 仍保持隔离。中转模型的长结构化步骤实测可能超过 120 秒，评测单步上限调整为 240 秒，总 case 上限仍为 420 秒。
- 最终留出集 `cv4-native-holdout-6-r3`：Fixed 5/6 完成、Evidence 0、硬覆盖 0、P95 89.203 秒；DSH Web 6/6 完成、12 条 Evidence、平均硬覆盖 0.278、35 次 durable tool calls、6 个 reviewer、P95 268.181 秒、PIT 违规 0。两者引用支持均值 0.833、质量成功均为 0/6、人工返工均为 6/6；PCE case 仍有 unsupported citation 与 horizon contract 缺陷。
- G1 判定为 `retain_baseline`。当前实现证明了原生 DSH 规划、隔离取证、预算账本、独立 reviewer、证据保存和保守停机链路可以工作，也证明了主要产品瓶颈是硬数据供给不足与长综合步骤可靠性，而不是继续增加调度、页面或平台组件。下一次核心改造只允许补齐 expectation/rates-USD/BTC event-window/derivatives 等冻结 hard requirements，并在同一 dev/holdout 上复跑；达到质量成功或减少人工返工前不启动 M04/M05。
- 最终自测：评测/运行时/Research MCP 定向 Python 回归 45 项通过，DSH Host/客户端 73 项通过并完成生产构建，Ruff 与 Pyright 无错误；修正 Git Bash 跨进程 home-lock 后相关 6 项通过。全仓为 572 passed、3 skipped、6 failed；剩余是两个 Windows 默认编码读取、两个既有 evolution 临时资产/恢复隔离、一个 15ms 时序敏感重试断言和一个 Windows MCP 10 秒启动测试，均未影响本轮真实 DSH/评测链路，也未伪装为通过。

### 2026-09-10：核心 hard data v2 与 dev/holdout 复测

- 根因确认：v1 冻结案例只有 `event_identity`/`policy_or_data_delta` 的 Evidence；`research_evaluation_capability_fixture` 不能承载 `FactEnvelope`；Replay adapter 丢弃 typed facts；comparison scorer 也没有把 facts 传入语义 Gate。因此数字窗口即使写入 fixture，也不可能通过 `minimum_fact` 检查。
- 合同与回放已修复：fixture 增加可选 `facts`；Replay adapter 保留并重绑定 Evidence/fact lineage；dataset loader 校验 fact ID、payload hash、PIT 时间和 lineage；comparison scorer 统一 freshness 后把 facts 传入 Gate。`reconstructed_fixture` 被加入 source mode，明确区分“现在下载的历史重建”与 prospective PIT 试验。
- 数据供给只补核心链：`BinanceVisionEventWindowLoader` 使用 Binance Vision 官方 spot 1m、futures metrics、premium index、funding archives，提供 BTC t-5m/t+1m 的价格、成交量、funding、OI、basis、crowding；`KalshiHistoricalExpectationLoader` 使用公开 Historical Markets/Candlesticks API，选择有实际成交的事件合约并提供 baseline、t-5m probability 和 delta。没有把缺失值填零或用代理替代。
- 离线核心审计：`tools/research_evaluation/audit_core_replay_dataset.py` 对 dev/holdout 各 6 案例逐案真实 Replay；hard coverage 分别为 0.750/0.694。BTC spot 与 derivatives 在 12/12 案例具备完整 typed facts；macro transmission 仍因没有合规分钟级 rates+USD 双源而保留 hard gap。FOMC Nov、PCE 和无对应市场合约的案例 expectation gap 也如实保留。
- DSH 执行协议已收敛：replay prompt 不再混入 live web search、历史讲话和 EventWatch 说明，要求按 requirement 清单逐项调用 `replay.research`，`max_results=20`，失败也继续下一项；synthesis 强制输出 30m/24h/72h 三个 horizon。单案例真实 canary 取得 5 个 Evidence、17 个 facts、8 次 capability 调用，hard coverage 0.833，三 horizon 和 1 个 reviewer 均通过。
- 真实 DSH Web 对照（`codexai.club/v1` + `gpt-5.5`，Host readiness 通过）：
  - dev `core-v2-native-dev`：Fixed 6/6 completed、Evidence 0、hard coverage 0；DSH 6/6 completed、Evidence 28、hard coverage 0.750、48 次 audited capability 调用、6 个 reviewer、PIT 违规 0、unattested evidence 0、三 horizon 6/6。
  - holdout `core-v2-native-holdout`：Fixed 6/6 completed、Evidence 0、hard coverage 0；DSH 6/6 completed、Evidence 28、hard coverage 0.694、48 次 audited capability 调用、6 个 reviewer、PIT 违规 0、unattested evidence 0、三 horizon 6/6。
  - 结果文件分别位于 `data/decision-hub/evaluations/core-v2-native-dev/` 和 `data/decision-hub/evaluations/core-v2-native-holdout/`；资产已登记，active pointer 未改变。
- 质量裁决：本轮证明“数据存在但 DSH 没有回读”已修复，证明 ResearchCapabilityGateway/Replay/Pack/事实 Gate 的核心链路可用；但质量成功仍为 0/6、人工返工仍为 6/6，所有案例至少有一个 hard gap，故不能宣称产品已达到可交易研究质量，也不能启动 M04/M05。
- 当前硬约束与下一步：必须接入已获许可且可验证的分钟级 `macro.rates` + `macro.usd` 双源，并保持 `t-5m/t+1m`、单位、independence group 和 source hash；Dukascopy 等候选端点本机不可达，FRED 日频和 EUR/USDT 等代理不能填充这个缺口。下一项只做该数据源的真实 canary、许可证/延迟核验和同一 dev/holdout 复跑；在此之前保持 `research_only/degraded` 停止线。
- 修复 expectation baseline 的事件语义：Gateway 只让实际 EventWatch offset 参与窗口槽校验，`baseline` 继续绑定同一 Run，但由 pricing adapter 的时间戳归档提供；证据接收层只对 typed fact 唯一标记为 `baseline` 且 requirement 明确要求 baseline 的 Evidence 免除普通快照 freshness，未标记旧快照仍 fail-closed。相关全链测试 9 项通过。
- 数据许可复核：可满足历史分钟级利率的官方方案是 CME DataMine/其 BrokerTec、GovPX 或 Treasury futures 数据，历史 DXY 则需要 ICE Data API/ICE Consolidated History 等授权；二者均要求购买/entitlement 和 API 身份。Trading Economics 官方 intraday API 支持 1 分钟与指定历史日期，但公开 `guest:guest` 对市场目录、US 2Y 和 DXY 探针均返回 HTTP 410，不能作为已可用数据。当前没有相应数据授权，不能用 Treasury/FRED 日频、Yahoo 短窗口或 EURUSD 代理伪装完成 hard requirement。
- 最终验证：核心 historical/eval/runtime/Gateway 回归 91 passed、1 skipped；canonical schema check、Ruff、Pyright 和 `git diff --check` 全部通过。全仓回归为 579 passed、3 skipped、5 failed；剩余失败是两个 Windows 默认 GBK 读取 UTF-8 JSONL、两个既有 evolution 候选资产 hash/恢复隔离，以及一个 Windows MCP 10 秒冷启动，均未落在本轮核心研究链。离线重建审计仍为 dev 0.750、holdout 0.694，且只作为 reconstructed fixture 证据，不冒充 prospective PIT。

### 2026-09-10：分钟级 rates/USD 授权文件入口

- 新增 `PortaraMacroEventWindowLoader`，只读取 owner 配置的本地授权文件；按源文件时区构造 `t-5m/t+1m`，超过 90 秒的旧 bar 直接拒绝。US2Y 保留 Tullett Prebon 市场来源，DXY 保留 ICE Futures US 市场来源，交付商相同不合并来源身份。
- US2Y 产出 `macro.rates` 的 `yield_percent` 与 bps event return；DXY 产出 `macro.usd` 的 index level 与 percent event return。每条事实记录原始文件 SHA-256、事件时间、单位、venue、delay class 和 independence group。
- 同一 US2Y 档案新增合规的 expectation fallback：只在案例没有 Kalshi 合约映射时，比较事件前一日最后可用值与 `t-5m`，产出 `macro.policy_expectation` 的 yield level 与 bps delta；基线最多容许三天市场闭市间隔，事件前 bar 仍受 90 秒限制。Kalshi 存在时保持优先，不重复写同一 requirement fixture。
- 数据集 builder 新增 `--portara-us2y`、`--portara-dxy`、`--portara-timezone`；必须成对配置。生成的单个 `macro_transmission` fixture 含两份 Evidence 和完整 typed facts，继续由现有 `replay.research` 路由执行，没有新增旁路或第二套研究系统。
- 周末或闭市事件若没有 90 秒内的真实 `t-5m/t+1m` bar，该案例保留 hard gap，builder 继续处理其他案例；文件缺失、格式、编码、重复时间戳等数据错误仍直接失败，避免用旧 bar 冒充事件窗口。
- 集成自测实际覆盖“文件读取 → 事件窗口 → fixture 合同校验 → Replay evidence/fact lineage 重绑定 → Pack 语义 Gate”，结果 `sufficient`；并验证闭市案例保留 gap。historical/eval 定向测试 9 passed，更宽核心回归 29 passed，Ruff、Pyright、`git diff --check` 通过。
- Portara/CQG 两个公开样例已于本机重新下载并核对为 `Date,Time,Open,High,Low,Close,Volume` 格式，与 loader 一致；样例分别覆盖不同日期，无法组成同一事件窗口，也不覆盖 2024/2025 冻结案例。因此本轮只证明核心接入链可用，G1 仍保持 `retain_baseline`，不得把合成测试数据计入 dev/holdout 质量结果。

### 2026-09-10：独立 cross-asset 事件窗口

- 按 Pack 的 soft requirement 补齐独立风险资产确认，没有用 BTC 或 DXY 重复充当 cross-asset。新增可选 `--portara-es`，读取 CME E-mini S&P（ES）1 分钟档案，形成 `cross_asset.equity`、`price/percent`、`t-5m/t+1m` typed facts。
- ES 与 rates/USD 一样固定原始文件 SHA-256、CME venue、独立 source ID、源文件时区和计算窗口；百分比计算固定到 8 位小数，避免循环小数污染报告。闭市和过旧 bar 保留 gap，源文件损坏仍 fail-closed。
- 真实公开 ES 样例 canary：源 hash `f335f07b306cbfe095b0a86bd45b8a2d22028e1a43b73062161ae4922ec5196f`，`2026-06-15T11:46Z` level 7531，`11:52Z` level 7533.25，事件收益 0.02987651%；继续穿过 fixture 合同、Replay lineage 和 Pack Gate，得到 1 条 Evidence、4 个 facts、soft coverage 1.0。该样例不进入冻结评测。
- dataset builder 不再删除同名冻结目标：目标存在直接拒绝，新结果先写隐藏 staging，manifest/hash 完整校验后才发布，防止真实取数中途失败破坏已有 dev/holdout。
- 新增 cross-asset、冻结目标保护和 Durable Gateway 整链自测。整链测试曾真实捕获 `t-5m` 零收益事实错误引用未来 `t+1m` 窗口的问题；现已让每条 event return 的 `window_end_at` 与自身 observation 对齐，Gateway 不需要放宽 PIT/窗口校验。historical/eval/Gateway 定向测试 19 passed，更宽核心回归 33 passed，Ruff、Pyright 和 `git diff --check` 通过。

### 2026-09-10：冻结案例剩余缺口复核

- 重新执行当前 immutable core-v2 审计：dev hard coverage 仍为 0.750，holdout 仍为 0.694。12/12 案例的共同 hard gap 是缺少分钟级 rates/USD 双源；讲话与周末突发事件另有 expectation semantic gap；FOMC 2024-11-07 和 PCE 2025-02-28 的 Kalshi 最后成交分别比目标 `t-5m` 早 9 分钟和 15 分钟，原 fixture 因此正确显示 stale。
- 修正 Kalshi loader 的上游标注问题：非 baseline bar 与目标 offset 的最大允许滞后从 30 分钟收紧到 90 秒，不能再把稀疏旧成交标成 `t-5m`。预测合约缺失或不够近时 builder 保留 gap；若配置了合规 US2Y 档案，则改用前一日基线到真实 `t-5m` 的 rates repricing，不产生重复 requirement fixture。
- holdout 的 `israel_strikes_iran_20241026` 还有 event identity / policy delta 低权威缺口。该缺口是在解冻后的审计中发现，当前不为单一留出案例硬编码来源，避免用 holdout 结果反向调优；应由后续通用官方事件源能力覆盖后再建立新版本留出集。
- 当前更宽核心回归 34 passed；Ruff、Pyright、`git diff --check` 全部通过。工作区与用户 Downloads 均未发现可用于冻结日期的 USY02Y/DXA/EP 文件，因此尚不能产生新的真实 dev/holdout 质量结果。
- 再次核对本机配置：Portara/CQG、CME DataMine、ICE、Trading Economics、EODHD、Alpha Vantage、Polygon、Databento 等数据凭据均不存在；只检查 presence，没有读取或记录任何密钥值。结合本地文件扫描结果，当前没有可合法执行冻结日期回填的输入。
- 全仓回归为 588 passed、3 skipped、6 failed。失败仍在目标外：两个 Windows 默认 GBK 读取 UTF-8 通知文件、两个 evolution 候选资产 hash/恢复、一个 15ms DSH Host 重试时序断言、一个 Windows MCP 10 秒冷启动；本轮新增核心链测试全部通过，未把这些目标外失败改写成成功。

### 2026-09-10：按事件类型选择硬证据与 core-v3 复测

- 找到此前“数据没到就永远停”的代码根因：`ResearchRequestFactory` 在读取事件类型前调用 `all_contract_requirements()`，导致每个事件都要求同一组硬证据。周末地缘冲击即使取得完整授权文件，rates/USD 与 ES 仍因市场闭市而不可能形成 `t-5m/t+1m`，产品会永久 `research_only`。
- `source_manifest.yaml` 升级为 evidence policy v3，并以 Pack-owned 数据规则定义 `geopolitical_shock` 的 `expectation_pricing`、`macro_transmission` 为 soft。事件身份、事件变化、BTC spot 和 derivatives 仍为 hard；其他已知事件族、未知事件族都保留默认硬门。只改变 importance，不放宽来源权威、PIT、字段、单位、窗口或语义 Gate。
- `ResearchRequestFactory` 先取 EventWatch；没有 Watch 时回读 admission 保存的 `EventRecord.event_type`，因此手动输入和计划事件都能使用同一策略。TaskPolicy hash、ContextManifest 与最终确定性 coverage 都接收这组实际要求。
- dataset builder 按每个案例的 `event_family` 写入 requirements 和 `expected_hard_requirement_ids`，并从目标名生成版本化 dataset ID；原 core-v2 不修改。新冻结目录为 `r2r_dev_core_v3`、`r2r_holdout_core_v3`。
- 离线 Replay/typed-fact Gate：dev 平均硬覆盖 0.806，周末 `iran_attack_israel_20240413` 为 1.0、无 hard gap；holdout 平均仍为 0.694，周末 `israel_strikes_iran_20241026` 为 0.5，剩余缺口是 `event_identity`/`policy_or_data_delta` 的 `low_authority`。留出集没有被规则虚假抬分，也没有针对单例添加来源。
- 真实 DSH Web dev `core-v3-native-dev`：4/6 有效完成、18 条 Evidence、32 次 audited tool calls、4 个 reviewer、PIT 违规 0、unattested evidence 0；2 例在模型第一步 `dsh_turn_error`，整批观测硬覆盖 0.500。地缘开发例又隔离重试两次，分别明确返回 Provider `overloaded` 和带 request ID 的通用服务处理错误，均为 0 tool call；OpenAI-compatible 文本 canary 同期成功，证明凭据/模型兼容而不能证明 DSH 服务稳定。
- 真实 DSH Web holdout `core-v3-native-holdout`：4/6 有效完成、18 条 Evidence、32 次 audited tool calls、5 个 reviewer、PIT 违规 0、unattested evidence 0，整批观测硬覆盖 0.472；周末地缘例真实经过 DSH/Gateway，取得 4 条 Evidence、8 次工具调用和 1 个 reviewer，最终只因两项低权威硬缺口保持 degraded。另 2 例为 `dsh_turn_error`。
- 本轮仍是 0/6 quality success、6/6 manual rework。结果证明不可满足的通用硬门已修复，也证明当前产品仍同时受授权 rates/USD 数据缺口和 DSH Provider 波动约束，不能通过 G1 或启动 M04/M05。
- 当前核心回归 46 passed；Ruff、Pyright 均通过。全仓回归为 595 passed、3 skipped、6 failed，失败仍是两个 Windows 默认 GBK 读取 UTF-8 通知、两个既有 evolution 资产 hash/恢复隔离、一个 15ms Host 重试时序断言和一个 Windows MCP 10 秒冷启动；本轮核心测试没有失败。离线审计产物位于 `data/decision-hub/evaluations/core-v3-offline-*-audit.json`，真实运行产物位于 `data/decision-hub/evaluations/core-v3-native-*`，active pointer 未改变。

### 2026-09-10：失败终态的可信证据回读与真实复测

- 定位到真实 `core-v3-geopolitical-retry-3` 的核心数据丢失：DSH 已完成 8 次受预算约束的工具执行，Hub 已保存 4 条 Evidence 和 14 个 typed facts，但最终模型步骤返回 Provider `PI_AI_ERROR` 后，Host 不生成结果引用，comparison 因而把该例记录为 0 Evidence。问题发生在证据回读层，不是数据源没有返回。
- Host 现在只在同一 turn 内存在成功的 `decision_hub_research`/`research_capability_execute` 调用及匹配成功结果时，为 failed/model-timeout/deadline-aborted 终态生成带 hash 的部分结果；原 terminal state 与错误码仍保留。无匹配 research result 或 research tool 本身失败时继续返回 409，不制造结果。
- Python Web runtime 在 failed/cancelled 状态下读取该部分结果并走原有 attestation、PIT、lineage 和 Pack coverage；未完成 turn 的 assistant 文本、causal case 和 horizons 不参与业务输出，只返回 `degraded` evidence-only 结果。180 秒到期会先停止模型和工具，只给 loopback Host 最多 3 秒投影已落库的收据；comparison 外层只为该清理留 5 秒，不延长研究预算。Hub durable link 允许同一 generation、terminal state、sequence、error 不变时从空 result 单向补充 result ref/hash，支持 Host 重启后恢复；任何改写或第二次 hash 仍报 terminal conflict。
- 对历史失败 Session 进行无新增模型调用的真实恢复：Host 返回 `finish_reason=error`、49,821 字节事件记录；标准 mapper 恢复 4 条 Evidence、14 个 facts、8 次工具调用，hard coverage 1.0、剩余 hard gap 0，causal case 未保留、horizon 数为 0。审计结果保存为 `data/decision-hub/evaluations/core-v3-geopolitical-readback/recovered-failed-session.json`。
- 使用新插件重新运行同一冻结 dev 地缘案例 `core-v3-geopolitical-readback`：DSH Web 完成，取得 4 条 Evidence、14 个 facts、8 次 audited tool calls、1 个 reviewer，hard coverage 1.0、PIT 违规 0、unattested evidence 0、quality success 1、manual rework 0；三个 horizon 均为 `no_trade`。评测 JSON 与 Hub 数据库计数一致，插件 build hash 为 `3e3001b79774598112bc3176d8df6144d8e33191fc68e5d78b28209ecff55909`，active pointer 未改变。
- 完整真实 dev `core-v3-native-dev-readback`：原始终态为 4/6 有业务结果、2 个 deadline cancel，已完成结果 18 条 Evidence；取消的两例实际已落库 9 条 Evidence/31 facts。恢复后 6/6 均有可读证据，共 27 条 Evidence/93 facts，平均 hard coverage 0.806；当时旧 comparison 仅凭 subagent 计数得到 quality success 1/6、manual rework 5/6，后续已由 `independent_review_receipt` 门和权威 Session 矩阵更正为 0/6、6/6。PIT/unattested 均为 0。
- 完整真实 holdout `core-v3-native-holdout-readback`：原始终态为 3/6 有业务结果、3 个 deadline cancel；取消三例实际已落库 14 条 Evidence/48 facts。恢复后 6/6 均有可读证据，共 26 条 Evidence/90 facts，平均 hard coverage 0.694、quality success 0/6、manual rework 6/6、PIT/unattested 均为 0。没有修改冻结 holdout 或按单例补来源。
- 五个取消 Session 的真实 Host 回放共恢复 23 条 Evidence、79 facts、40 次 durable tool calls；全部 `finish_reason=aborted`，causal case/horizon 均为 0。逐例结果保存在 `data/decision-hub/evaluations/core-v3-cancelled-readback-audit.json`，分区汇总保存在 `data/decision-hub/evaluations/core-v3-readback-adjusted-audit.json`；原 comparison 文件保持不可变，active pointer 未改变。
- Provider route 对 `PI_AI_ERROR` 启用了总计最多 2 次的有界重试，真实失败日志证明达到预算后停止；它用于吸收临时过载，不放宽总 deadline 或工具预算。最终更宽核心回归为 125 passed、1 skipped，DSH Host/客户端 76 passed；生产构建、Ruff、Pyright、canonical schema check 与 `git diff --check` 均通过。最终运行中的 Host readiness 为 true，代码 build hash 为 `78d1a69e09f4430bf2fc178f4ab768d1b1dc3d8a76911a20ed2855620701905f`。
- 这一结果关闭了“已取得证据却因最终生成失败或时限取消全部丢失”的核心缺陷；当时曾按旧 subagent 计数口径记录为减少 1 次人工返工，后续权威 Session 矩阵证明该案例没有可信结构化 Review，现行质量口径仍为 0/6、6/6。G1 仍不通过：央行、CPI、就业等事件的 `macro_transmission` 仍要求覆盖冻结日期的授权 US2Y/DXY 分钟双源，当前没有这些文件。

### 2026-09-11：授权数据实际覆盖报告与冻结发布门

- 继续按 G1-C2 核心事实闭环处理，没有启动调度、页面或通用平台工作。复核发现数据集 builder 会把 Portara 的事件窗口缺失/过期保留为 gap，但发布后只有质量分数下降，缺少逐案例的数据验收记录；这不满足方案中“数据集元数据列出实际覆盖”的要求，也会让不完整授权文件被误冻成下一候选版本。
- `build_core_replay_dataset.py` 现在为每个新版本写 `data_coverage.json`：逐案例记录 BTC spot、derivatives、expectation、rates/USD 和 cross-asset 的 hard/soft 级别、是否生成 fixture、Evidence/fact 数、source id/ref/hash、实际观测时点和 event offset；expectation 同时保留 Kalshi 与 US2Y fallback 的尝试及精确失败原因。
- 覆盖报告包含每个冻结案例所需的 US2Y/DXY/ES `t-5m`、`t+1m` UTC 目标时点、90 秒最大滞后，以及 US2Y expectation baseline 的前 24 小时目标和三天最大闭市容忍。报告本身带 SHA-256，`source_hashes.json` v2 记录其 ref/hash，不包含本地授权文件路径。
- Portara macro/cross-asset loader 新增只读 `coverage(event_at)` 诊断：逐 instrument 返回目标时点、实际观测时点、滞后秒数、source hash 和 gap reason；builder 将诊断原样写入每案覆盖报告，便于采购验收区分“文件存在但窗口过期”和“文件完全缺失”，不改变现有事实或 Pack 语义。
- 新增 `--require-full-hard-data` 发布门。启用时，构建仍先在隐藏 staging 中完成报告和全部合同校验；只要任何 hard 数据槽没有 fixture，就以 `core_replay_hard_data_coverage_incomplete:<covered>/<total>:<report>` 失败，且不会将 staging 重命名为不可变候选数据集。周末地缘案例的 expectation/macro 仍按 Pack v3 的 soft 规则处理，不会被错误阻断。
- 覆盖报告回读也纳入评测加载器：`source_hashes.json` 的 `coverage_report_hash` 校验报告规范化内容，`coverage_report_file_hash` 校验实际 UTF-8 文件字节；报告 dataset id、相对路径和双向引用不一致时 fail-closed。旧 v1 数据集没有覆盖引用时保持兼容。该校验曾被自测捕获并已修正“内容 hash 与文件 hash 混用”的问题。
- 新增纯离线 `tools/research_evaluation/plan_core_data_inputs.py`，从冻结 manifest 生成 dev/holdout 的输入计划，不触发网络、不修改冻结集；当前两份计划各列出 6 案例、22 个 hard requirement case slots，其中 expectation 5、macro transmission 5、BTC spot 6、derivatives 6，并为每案列出 US2Y/DXY/ES 的精确 UTC 目标时点和 Kalshi/US2Y fallback。
- 输入计划已写入 `data/decision-hub/evaluations/core-v3-data-input-plan.json` 与 `core-v3-holdout-data-input-plan.json`；同一 dev 计划连续生成两次 SHA-256 相同，确认采购契约不会因生成顺序或运行时间漂移。
- 输入计划同时锁定 source manifest hash、逐案例 fixture hash 和有效 IANA 时区；非法时区、路径逃逸和源数据集缺失均在联网前失败。计划扩展后的专项测试为 5 passed，重新生成两份计划成功。
- 端到端自测使用一个冻结 FOMC 案例、合成的 Binance 历史窗口和格式真实的 USY02Y/DXA/EP 文件，验证 Kalshi 缺口会正确切到 US2Y fallback、rates/USD/ES 事实进入新版本、逐 slot macro/cross-asset diagnostics 写入覆盖报告、hard coverage 达到 1.0、覆盖报告 hash 与 `source_hashes.json` 一致并通过严格发布门；缺少 US2Y/DXY 时则得到 2/4 hard 槽并拒绝发布，staging 报告仍可诊断。定向结果 19 passed；包含 DSH 证据回读、运行时、Fact Pack、Replay、comparison、macro 整链和全部 historical 测试的更宽回归为 127 passed、1 skipped；Ruff、Pyright、canonical schema check 和 `git diff --check` 均通过。
- 修正 hash 语义后重新执行同一回归：覆盖报告加载、篡改拒绝和旧数据集兼容均通过；加入输入计划工具、确定性测试和 Portara slot 诊断后的更宽核心回归为 135 passed、1 skipped。
- 按 T06 的完整范围补跑 `tests/research`、全部 eval、Fact Pack、DSH research runtime/session/web、historical 与 Gateway 相关测试：`320 passed、1 skipped`；未发现 Portara 诊断、Replay lineage、Provider Router 或 Pack Gate 回归。
- 修正 `_trusted_plan` 的并行依赖 off-by-one：第一对独立取证任务现在都无依赖，后续任务分别依赖前两位对应任务；Replay prompt 同时明确先发出无依赖的两项调用、最多保持两项在途，真实满足 `max_parallel_tasks=2`，避免把所有 Gateway 取证串成单链并放大 DSH deadline。新增计划依赖与 prompt 回归后同一 T06 范围为 `321 passed、1 skipped`；Ruff、Pyright 和 `git diff --check` 通过。
- 再纳入全部 `tests/dsh_native`（Native Replay matrix、Host 计划投影和取消/恢复约束）复跑，当前核心研究链回归为 `342 passed、3 skipped`；并行计划修复没有破坏 Native DSH 约束。
- 重新执行现有冻结 `r2r_dev_core_v3` / `r2r_holdout_core_v3` 的真实 Replay audit，结果仍分别为 hard coverage `0.8055555555555555` / `0.6944444444444445`，dev/holdout 各 6 案例，Evidence `27/26`，facts `93/90`，与上一版基线逐项一致；本轮覆盖报告完整性改动没有抬高或降低 G1 分数。
- 重新核对三份方案与 Pack：`cross_asset_confirmation` 明确是 soft requirement，原因是它用于独立风险确认和 72h 复核，但不应在闭市或不可得时阻断方向性安全门；`macro_transmission`、`expectation_pricing`、BTC spot 和 derivatives 才是当前方向性 hard 数据门。此次目标中的 cross-asset 仍已补齐 ES 接入和 typed-fact Gate，未擅自把 Pack 的 soft 规则改成 hard。
- 外部供给核查保持同一停止线：[Portara 公开历史表](https://portaracqg.com/historical-daily-futures-data/) 可确认 `USY02Y` 的历史品种，但公开下载是 daily sample，[Portara/CQG intraday](https://www.cqg.com/products/historical-data/portara) 需要按订单交付；[ICE Tick History](https://developer.ice.com/hc/en-us/articles/202848684-Tick-History) 支持 1 分钟聚合，却要求现有 ICE Data Services client account。因此当前没有把公开日频样本、代理资产或模型检索结果冒充冻结日期分钟事实。
- 2026-09-11 再做公开来源核查：[FirstRateData ZT 目录](https://firstratedata.com/i/futures/ZT) 声明有 1 分钟历史，但完整数据需购买、公开样本不覆盖冻结日期；[Dukascopy instrument catalogue](https://github.com/nova-land/dukascopy-tools) 的 `dollaridxusd` 是经纪商 CFD，不是 Pack 要求的 ICE DXA；[公开 CME ZT 仓库](https://github.com/axb0306/cme-futures-ohlc) 当前仅覆盖 2026 近期。它们分别缺少授权、市场身份或冻结日期覆盖，均不能进入 `macro.rates`/`macro.usd` hard facts。没有找到新的合规输入，因此不改写 G1 结论。
- 2026-09-11 直接探测 Kalshi 历史接口：`FED-24NOV` 的 `T5.50` 合约能返回精确 `t-5m` 时间点，现有 dev fixture 已覆盖；`KXPCECORE-25JAN` 在事件前最后成交为 `13:10Z`，距离 `t-5m=13:25Z` 超过 90 秒，判定 stale 正确，未放宽窗口或把零成交 carry-forward 冒充成交。
- 当前离线逐案审计再次确认缺口边界：dev 的 `macro_transmission` 六案全部缺失；`fomc_cut_25bp_20241218` 与 `powell_jackson_hole_20240823` 另缺 expectation，其他 dev expectation 已覆盖。holdout 的 `us_pce_20250228` expectation 同样因 stale 缺失，`israel_strikes_iran_20241026` 的事件本体保留原冻结低权威来源。没有修改冻结 holdout，也没有把这些 gap 改成 soft。
- 启动 Docker Desktop 后复核 Host 仍未进入 ready：日志确认后端因 `C:\Users\90603\AppData\Local\Docker\run\dockerInference` 自指向的 Windows reparse link 无法删除而退出，`docker version` 没有 Server。该问题发生在本机 Docker 运行态，不能由 Decision Hub 代码或 Replay 测试伪造解决；因此本轮不宣称真实 DSH 重跑完成。
- 已在确认目标仅为该单一 Docker 运行态链接后尝试 `fsutil reparsepoint delete` 与非递归 `rmdir`；Windows 均返回 `Error 1920 / The file cannot be accessed by the system`。没有继续使用可能清空 Docker 数据的 factory reset 或递归删除命令，环境修复仍停在 Docker Desktop 自身运行态。
- 进一步尝试不依赖 Docker 的官方 live CLI：现有 upstream source cache 能找到 CLI 和 profile，但缺少约 128 个锁定的 `@deepseek-ai/*` runtime 包；`run-web.sh` 的正常修复路径会要求删除并重装根 `node_modules`，而当前网络/包管理器无法安全完成该重装。直接启动因此以 `plugin tree failed to load / ERR_MODULE_NOT_FOUND` 终止，没有产生 Host readiness 或真实研究结果。
- 依赖复用核查已完成：`data/dsh-live/profiles/web/node_modules` 只有 profile/plugin 链接和包管理元数据，upstream source 也只有 `dsh-tool-session-query`、`dsh-web-fetch-http` 两个工具链接，用户目录的 pnpm store/corepack 缓存不存在完整 runtime 包；因此没有安全的“改模块路径即可启动”方案。没有删除根 `node_modules`、没有重置 Docker，也没有把离线 Replay 结果冒充 live 结果。
- 本轮最终自测：核心定向回归 `128 passed、3 skipped`；Ruff 通过；生产代码/工具类型门 `pyright packages apps tools/canary` 为 `0 errors、0 warnings、0 informations`；canonical schema check 与 `git diff --check` 通过。现有测试目录的 11 个 Pyright 报错来自历史测试私有 helper/既有工具类型，不由本轮核心改动引入。
- 下一条实际命令已固定：分别以原始 dev/holdout 冻结集为 source，使用新版本 target，传入 owner 授权的 `--portara-us2y`、`--portara-dxy`（如有则加 `--portara-es`）、正确 `--portara-timezone` 和 `--require-full-hard-data`；两份报告 hard-data 门通过后才运行真实 DSH 对照。当前本机仍没有覆盖 2024-04 至 2025-03 冻结事件的授权 US2Y/DXY 文件，因此不能生成真实 core-v4 或改写 G1 结论。

### 2026-09-11：真实 DSH 终态收口与独立 Review 回读

- Docker 仍受本机损坏的 `dockerInference` reparse link 阻断，但它不再阻断核心自测。上游 source cache 完成锁定依赖安装后，固定 DSH Host 已从官方源码直接启动；readiness 返回锁定 source commit/version 和仓库插件 build hash。手动联调曾暴露 Hub 进程缺 callback secret、PowerShell 按固定长度截取 `.env` 导致 API key 少一位，两项均按真实配置纠正，没有修改密钥或绕过身份校验。
- 真实 candidate `core-v3-live-dsh-fomc-20241107-patch5-candidate` 完成：Provider `codexai`、model `gpt-5.5`，Host build `66f016699880827f41d84f387233a6b8afbcf2286b17438904c7222c1899b86e`；8 次受预算约束的 ResearchCapabilityGateway 调用、1 个 reviewer、5 条 Evidence、17 个 typed facts、三个不同 horizon、引用支持率 1.0、PIT/unattested 均为 0。耗时 232.834 秒，hard coverage 0.833；`macro_transmission` 因冻结集无 US2Y/DXY fixture 保持 hard gap，因此结果正确为 `degraded/critical_data_unavailable`，没有虚报质量成功。
- 修复 Host 在成功 `decision_hub_synthesis_submit` 后仍等待无业务价值的尾随模型步骤：Host 在 durable synthesis Tool result 落盘后取消 Session，并将该受控 `aborted` 投影为 completed；没有 synthesis 的普通 cancel/error 仍 fail-closed。真实 candidate 的最后事件为 synthesis call/result、step end、受控 turn abort，Hub 权威终态为 completed。
- reviewer preset 现在使用无工具、`maxDepth=1` 的独立证据复核 persona，防止子任务继承经理研究工具后递归取证。进一步核对真实产物发现 Python mapper 曾将 `reviews` 固定为空，即使原生 subagent 已返回反驳；已新增同 Session call/result 配对、闭合 `independent-review-receipt.v1`、Evidence ID 白名单、回执内容 hash 和单 limitation 规范化。Prompt 要求先冻结拟提交 synthesis，把精确 thesis、全部引用和三个 horizon 交给 reviewer，复核后不得改写 thesis；最终 mapper 还要求 receipt target 与正式 causal thesis 精确一致。未知引用、失败结果、target 不一致或未匹配调用不会进入产品 Review。
- 真实复测 `core-v3-live-dsh-fomc-20241107-review-receipt` 在 360 秒边界前完成 5 条 Evidence 和两次 reviewer 尝试：第一次 Provider 子任务失败，第二次返回有效 JSON，主 synthesis 尚未提交即到期，权威终态保持 cancelled，horizon/因果结论均丢弃。无新增模型调用的可信回放现已直接读取权威 Session 归档，而不是依赖先前导出的 Host result：归档 SHA-256 为 `2f510bc360a2509c52422c6e50a1e808616cd711840d5e0ec8caf122216c152a`，共 482 条事件，恢复 37 条 canonical trace、5 条 Evidence、17 个 facts 和 1 份 `bounded` Review。回执 hash 为 `df945700d8cb3d826cc30b9db4cd31041cc9baaf1bb79d5a3a5616459b29d34e`，`review_id` 保持 `review_df945700d8cb3d826cc30b9db4cd3104`，Review 绑定的最终结果内容 hash 为 `dadb8462d335d684bdb29f37c7d0c6544ccaf2c4606cdf7e91bdec3a8935e39a`；case report result hash 为 `ad9a5fb2c2d8bb6255a8c33785b2706ed05211beb4aa99451201cfd85be2bd7c`。重建产物和 provenance sidecar 位于同一 `data/decision-hub/evaluations/core-v3-live-dsh-fomc-20241107-review-receipt/.../dsh-web-recovered-review.*.json` 目录。
- comparison 质量门新增 `independent_review_receipt`：DSH 即使数据齐全、三个 horizon 完整，只要没有可信结构化 Review，仍计为人工返工，避免用 `total_subagents=1` 冒充独立核验完成。
- 正式 Worker 原先会在运行时结果回读后无条件生成本地模板 Review，从而覆盖原生 DSH reviewer；现已让 `ResearchReviewService` 优先验证并保存原生回执。验证包括单一回执、Evidence 子集、canonical receipt ID、最终结果 hash、target thesis 和结果时间边界；LangGraph 每次确定性归一化后会重新绑定最终结果 hash，篡改或与已保存 role output 冲突时 fail-closed。正式 Worker 整链测试确认 `research_results` 与 `run_role_outputs` 保存同一个原生 Review；真实恢复 JSON 也已按新 hash 语义重算，没有改写回执正文。
- 新增 `session_archive.py` 和可重跑的 `recover_dsh_session_archive.py`：前者有界读取普通 JSONL 或多 frame Zstandard 归档，以首条 Session header 给每个原始事件补上可信 Session identity，并从 profile 最后的 canonical request 做严格合同恢复；后者同时校验冻结 dataset/case/cutoff/requirement/trigger 身份后才重建评测产物和 provenance。真实归档首次接入时捕获了“单次 Zstandard read 只得到首 frame”的缺陷，现已循环读到 EOF 且保留 64 MiB/100,000-event 上限。第一次失败 reviewer 的 trace 也从误标 `succeeded` 修正为 `failed/dsh_subagent_failed`，成功重试仍单独验真。
- 重新执行冻结 dev/holdout Replay audit：dev 6 例 hard coverage `0.8055555555555555`，holdout 6 例 `0.6944444444444445`；结果分别保存为 `core-v3-offline-dev-audit-final.json` 和 `core-v3-offline-holdout-audit-final.json`。BTC spot/derivatives 仍为 12/12 完整，现有 Kalshi expectation 按真实成交窗口保留，未放宽 stale 阈值；US2Y/DXY 缺口没有被 reviewer 或映射修复虚抬。
- Pack 契约复核确认 `macro_transmission` 约束的是 `macro.rates` + `macro.usd` 两个 metric family、两独立来源、`t-5m/t+1m`、实时延迟和单位，不把 US2Y/DXY instrument 写死；USY02Y/DXA 是当前已验证的 Portara 实现。没有第二份真实合规数据前不为假想供应商再建抽象层，未来替代源仍必须提供交易所/官方分钟档案和可验证来源身份。
- 最终核心回归 `375 passed、3 skipped`；DSH 插件 `77 passed`，TypeScript `tsc --noEmit`、生产构建、Ruff、Pyright（0 errors）、canonical schema、module docs 和 `git diff --check` 全部通过。全仓为 `615 passed、3 skipped、4 failed`；剩余是两个 Windows 默认 GBK 读取 UTF-8 通知文件和两个既有 evolution 候选资产 hash/恢复隔离失败，不在本轮研究主链范围。Windows MCP smoke 原失败的根因是 32 条 migration 日志填满未消费的 subprocess pipe；改写到临时日志文件后官方 client handshake 通过，单测 5.52 秒完成。

### 2026-09-12：权威 Session 归档回读闭环

- 本轮只修复证据、事实、Review 和 trace 的权威回读，没有新增页面、调度器或通用平台设施。`DshSessionArchive` 将原始日志首条 `session.id` 作为身份根，并拒绝 expected ID 不匹配、重复 header、非法/超限 JSONL、损坏 Zstandard 和 canonical request 冲突/尾随内容；`zstandard>=0.25,<1` 已从间接依赖提升为该正式适配器的直接依赖并同步 lockfile。
- 真实 482-event 归档重新映射后为 8 次 Research tool call、2 次 reviewer 尝试、5 条 Evidence、17 facts、hard coverage `0.8333333333333334`，唯一 hard gap 仍为 `macro_transmission`；最终状态保持 `degraded`，causal case/horizon 仍为空，quality success 仍为 false。Review 只来自第二个成功且 call ID 匹配的 tool result，第一个失败结果进入 trace 但不能形成回执。
- provenance 验证同时重算 archive、dataset manifest、trace、receipt、review/result binding、case report 和 provenance 自身 hash；`result_content_hash == review_result_hash`，所有 Review refs 均是本次 5 条 Evidence 的子集，时间位于 Session 起止边界内。同一恢复命令连续执行两次，report 与 sidecar 的文件 SHA-256 均完全一致。该结果替代先前从二次导出 JSON 手工重绑的 hash 记录，但不修改原始 Session 或历史 Hub 数据库行。
- 新增定向归档测试 5 项；包含 research/eval/DSH runtime/Worker/historical/Fact Pack/Gateway/Native DSH 的核心回归为 `383 passed、3 skipped`。Ruff、canonical schema、13 个 module docs、`git diff --check` 通过；Pyright 对 `packages apps tools/research_evaluation tools/canary` 为 `0 errors`。全仓为 `620 passed、3 skipped、4 failed`，仍是两个 Windows GBK 读取 UTF-8 通知 JSONL 和两个既有 evolution candidate artifact hash/恢复隔离失败，与上一轮相同且未落在本轮核心路径。
- G1 停止线不变：本次关闭的是“已有真实执行能否从权威原始归档重放”的证据链缺口，没有创造缺失的 rates/USD 数据。完整授权 US2Y/DXY 分钟文件到位前，不生成 core-v4、不把 `macro_transmission` 改软，也不启动 M04/M05。
- 当前只剩同一个外部输入条件：仓库和本机没有覆盖 2024-04 至 2025-03 指定 UTC slots 的已授权 USY02Y/DXA 分钟档案。[ICE Consolidated History](https://www.ice.com/fixed-income-data-services/access-and-delivery/connectivity-and-feeds/ice-consolidated-history) 官方接口确实支持最细 1 分钟，[CME DataMine](https://www.cmegroup.com/datamine.html) 也提供交易所/现金市场历史，但二者都要求账户、购买或数据许可；它们不是无需身份即可下载的公开接口。代码、严格发布门、逐 slot 输入计划、Gateway/Replay/Pack 回读和真实 DSH 路径均已验证；在文件到位前继续批量调用模型只能重复得到已知 `macro_transmission` hard gap，不能形成新的 G1 证据，因此不执行这类无效消耗，也不启动 M04/M05。
- 2026-09-12 对 Portara 官方样本再次做了只读格式探针：目录明确列出 DXA、TUAA(ZT)、EP 的 1 分钟历史，TUAA 页面同时注明完整 intraday 需要购买且 free-tier 当前不可用；公开样本分别是 `DXA2026M`、`TUAA2026M`、`EP2026M`，实际日期为 2026-05 至 2026-06，不能覆盖冻结的 2024-04 至 2025-03。样本已验证现有 ASCII parser、Chicago 时区转换和逐 slot stale/missing diagnostics，未写入评测集、未生成 hard fact；脱敏探针结果保存在 `data/decision-hub/evaluations/core-v3-portara-public-sample-probe.json`（文件 SHA-256 `7e6b5fe5fa7c4c9f2889459cd66103623c496b5f055cd141805704b106968f4f`）。
- 同日重新对 dev 案例 `fomc_cut_25bp_20241218` 的 Kalshi `FED-24DEC` 全事件合约做了只读探针：接口和合约均可回读，但所有候选在目标 `t-5m=2024-12-18T18:55:00Z` 前的最后有效成交最多到 `18:50:00Z`，滞后 `300s`，超过 `90s` 门；`KalshiHistoricalExpectationLoader` 因此稳定返回 `kalshi_expectation_window_missing`，没有把 stale 价格写入 fixture。脱敏结果和各候选 payload hash 保存在 `data/decision-hub/evaluations/core-v3-kalshi-expectation-probe.json`（文件 SHA-256 `5514042fbae3169aaf13c6c23205b767df357907e5cfde3c777700ca627a1a`）。
- 同日探测 CME FedWatch 历史/盘中接口作为 expectation 的官方候选：文档显示它有历史和 60 秒级概率，但生产 endpoint 在无 entitlement/OAuth API ID 时返回 HTTP 401；因此没有把文档样例或未授权响应写入事实，保留 Kalshi→授权 US2Y fallback 的原有路由。[CME FedWatch API](https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html)
- 同日补上 strict hard-data 的来源授权门：`--require-full-hard-data` 配合 USY02Y/DXA 文件时必须提供 `portara-source-attestation.v1`，由 owner 声明授权引用、source id/URL、时区和每个文件 SHA-256；builder 在生成 fixture 前验证字节 hash 与固定市场身份，覆盖报告和 `source_hashes.json` 只保存无本地路径的声明。新增 `PortaraSourceAttestation` 及 3 项拒绝测试，防止任意 CSV 被误标为 Portara/CQG licensed；既有 core fixture、Replay/Pack 路由不变。
- 同日完成来源授权门后的核心自测：研究链、评测、DSH runtime、Worker、历史数据、Fact Pack/Gateway、Native DSH 和关系 Gate 定向回归 `398 passed、3 skipped`；attestation 与 core builder 定向测试 `18 passed`，Ruff、生产代码/工具 Pyright（0 errors）、canonical schema、module docs 和 `git diff --check` 全部通过。该门只增加来源真实性校验，不改变现有离线 fixture，也没有把缺失的 US2Y/DXY 数据伪装成已覆盖。
- 同日收紧 attestation 与构建参数的绑定：声明了 `ES` 时必须同时传入 ES 文件，未传入则以 `portara_attestation_roles_mismatch` fail-closed；新增回归覆盖该未绑定可选来源场景，避免来源证明与实际使用文件不一致。
- 同日将 relationship Gate 接入统一 `assess_evidence_sufficiency`：对包含前后事件窗口的要求按 metric family、instrument、venue、source 和 independence group 分组，校验同一 observable 的 baseline→after；对有窗口元数据的派生 `event_return`、`open_interest_delta`、`delta` 保留合法关系。跨 instrument 错配与 event id 不一致均被拒绝；关系/语义/Native DSH 回归 `17 passed`，生产代码/工具 Pyright `0 errors`。
- 同日让真实事件窗口事实携带 `event_id`：宏观 intraday/expectation、BTC spot/derivatives event-window 及 core Replay fixture 都将事件身份纳入事实 payload hash，统一 Gate 可在存在身份时验证两端一致；相关 adapter、Replay、Gateway/Pack 回归 `36 passed`，核心回归保持 `402 passed、3 skipped`。
- 同日按 `python -m tools.research_evaluation.audit_core_replay_dataset` 重新审计当前冻结 dev/holdout：dev `hard_coverage_mean=0.8055555555555555`、holdout `0.6944444444444445`，12 个案例的 Replay/Pack/Gateway 回读稳定；未发现新的授权 USY02Y/DXA 输入，因此不生成 core-v4、不改写 G1 结论。
- 同日补充统一 Gate 的 event_id 负向回归：同一 instrument/source 但 baseline 与 after 属于不同事件时拒绝覆盖；关系/语义定向回归 `17 passed`，核心研究链回归 `402 passed、3 skipped`。本轮仍未引入任何外围平台任务。
- 同日收紧公开 `validate_baseline_after()`：即使调用方未传 expected event_id，只要两端事实显式携带不同事件身份也 fail-closed；直接关系 API 回归已覆盖该路径，核心研究链回归 `402 passed、3 skipped`。
- 同日只读探测旧金山联储 USMPD 公开研究数据：它确实含 UST2Y/DXY 字段和冻结 FOMC 日期，但输出是 30/70/100 分钟事件窗口的变化/收益，不提供可独立回读的原始 `t-5m/t+1m` 绝对分钟 bar；因此拒绝纳入 `macro_transmission` hard facts。脱敏探针保存在 `data/decision-hub/evaluations/core-v3-usmpd-probe.json`，文件 SHA-256 为 `743851f8d68eaa4013be23b26968fc28ae0197d8b7195741020a8ae0a25b1273`。[USMPD](https://www.frbsf.org/research-and-insights/data-and-indicators/us-monetary-policy-event-study-database/)
- 同日将 typed facts 正式接入 `CryptoMacroFactPack.assess()` 的 Pack 入口，防止只提交 Evidence 绕过字段/单位/窗口/关系 Gate；新增“无 facts 不覆盖、有完整 crypto spot facts 才覆盖”的回归，Pack/核心定向回归 `37 passed`，核心研究链回归 `402 passed、3 skipped`。
- 同日修复失败/取消 readback 的 round coverage fallback：当该轮没有预计算 Coverage 时，也按该轮持久 Evidence 过滤并传入 typed facts，保持与正常结果相同的语义/关系 Gate；readback/runtime 定向回归 `84 passed、1 skipped`，核心研究链回归 `402 passed、3 skipped`。
- 同日扩充冻结 dev/holdout audit 输出：逐案记录 `soft_coverage_ratio`、soft gaps 和事实 metric families，并锁定 cross-asset soft gap 的可见性；audit 定向回归 `15 passed`，核心研究链回归 `402 passed、3 skipped`。
- 同日按新 audit 结果复核 cross-asset：dev/holdout 的 `soft_coverage_mean` 均为 `0.0`，12/12 案例明确列出 `cross_asset_confirmation` soft gap，事实 metric family union 只有 `crypto.spot`、`crypto.derivatives` 和已覆盖案例中的 `macro.policy_expectation`；ES 接入代码和 Pack Gate 已有回归，但没有真实 ES 分钟输入前不把 soft gap 伪装成覆盖。
- 同日执行一次最小 OpenAI-compatible live canary：从 `data/dsh-live/.env` 注入现有 `https://codexai.club/v1` / `gpt-5.5` 配置，未把密钥写入日志或产物；Runtime、LangGraph、Gate 和 Artifact 链路成功返回 `admitted=true`、`forecast_horizons=[30m,24h,72h]`，但状态仍为 `degraded`、`gate_status=research_only`。该结果只证明接口兼容性，不改变 dev/holdout hard-data 缺口或 G1 停止线。
- 同日修正关系 Gate 的返回契约：调用方未显式传 `expected event_id` 时，`RelationshipMatch` 现在保留两端已验证的共同事件身份；新增回归覆盖该返回值和错配拒绝路径，关系 Gate 定向回归 `4 passed`。这只修复事件绑定回读，不放宽任何覆盖条件。
- 同日最终核心回归在关系 Gate 收口后为 `403 passed、3 skipped`；Ruff、Pyright（0 errors）、canonical schemas、13 个 module docs 和 `git diff --check` 仍通过。G1 仍只差冻结日期范围内的授权 US2Y/DXY 分钟输入，未生成 core-v4 或启动 M04/M05。
- 同日诊断截图中的 Docker 环境：CLI client `29.6.1` 可用，但 `desktop-linux` 初始 daemon pipe 不存在；启动 Docker Desktop 后虽出现 backend/pipe，Docker API 仍不响应且 `com.docker.service` 保持 stopped。该环境问题只影响再次启动原生 DSH live Web，不改变已经从真实 Session 归档完成的 DSH 证据回读，也不作为放宽 hard-data 门的理由。
- 同日修复 `ResearchValueEvaluationService` 的 durable fallback：失败/取消且没有 `research_results` 行时，现从同一 Run 的持久 Evidence/facts 重新评估 Coverage，不再用空列表覆盖已经成功取证的部分；新增回归验证 Evidence 和 typed facts 均传入语义 Gate。核心回归更新为 `404 passed、3 skipped`，Ruff 和 Pyright 继续通过。
- 同日将上述 fallback 查询限制在确实缺少 `research_results` 的失败/取消路径，已有结果的正常评估继续只读取已验真的 final Coverage；定向回归和完整核心回归保持 `404 passed、3 skipped`。
- 同日修复 value evaluation 的缓存身份：`evaluate_current()` 版本指纹现包含当前 Run 的 Evidence 数、fact 数、final result hash、Outcome 数和 Feedback 数；晚到的 durable Evidence/facts 或最终结果会生成新评估，不会命中终态前的旧空证据快照。指纹先 canonical hash 后形成固定 82 字符 `research-value.v2`，不突破数据库 128 字符上限；新增晚到 Evidence 回归，核心回归更新为 `405 passed、3 skipped`。
- 同日让 Query/View 与 value evaluation 的所有失败/取消 fallback 按 Run 的 `EventWatch.event_family`（无 watch 时按 Observation event hint）调用 `contract_requirements_for_event()`；地缘事件的 `expectation_pricing`/`macro_transmission` 继续是 soft，BTC spot/derivatives 仍为 hard。新增 fallback policy 回归，核心回归更新为 `406 passed、3 skipped`。
- 同日扩充 `audit_core_replay_dataset` 逐案输出 `event_family`，使 soft gap 能直接对应 Pack 的事件策略；dev/holdout audit 已重新生成，hard coverage 分别保持 `0.8055555555555555` 与 `0.6944444444444445`，没有改变任何覆盖判定。
- 同日将 `event_family` 纳入 value evaluation 的缓存指纹；事件被重新归类时，即使 Evidence/facts/result 数量不变也会重算评估，避免地缘事件 soft policy 继续命中旧 hard policy 快照。新增 policy-version 回归，核心定向回归 `2 passed`。
- 同日复跑完整核心集合确认 Query/View policy fallback、value cache 指纹和 audit event family 改动无回归：`406 passed、3 skipped`；Ruff、Pyright、canonical schemas、module docs 和 `git diff --check` 继续通过。
- 同日修正 `observability_view` readiness 的 policy 来源：readiness 现在和 Coverage 一样使用当前 Run 的 event-specific requirements，避免地缘事件在诊断列表中被重新显示为 hard；观测回归与完整核心集合保持 `406 passed、3 skipped`。
- 同日完成本轮目标的最终自测与记录收口：核心研究链回归仍为 `406 passed、3 skipped`；dev/holdout audit 重新生成且 hard coverage 保持 `0.8055555555555555` / `0.6944444444444445`，soft gaps 按事件类型可见；Ruff、生产代码/工具 Pyright（0 errors）、canonical schema、module docs 和 `git diff --check` 全部通过。实现侧已闭环，唯一未完成项仍是冻结日期范围内的授权 USY02Y/DXA 分钟档案；文件到位前不生成 core-v4、不放宽 hard gate、不启动 M04/M05。
- 同日重新从权威 `dsh_d422...` Zstandard 归档执行一次 FOMC 案例恢复，归档、Review、结果和 provenance hash 与已保存产物完全一致（archive `2f510bc3…c152a`、review `df945700…9d34e`、result `dadb8462…5e39a`、provenance `3cf13847…d915`）；确认回读路径没有依赖二次导出 JSON 或运行时重跑。
- 同日把同一恢复路径扩展到完整真实 dev/holdout Session 矩阵：12/12 原始归档均通过 frozen manifest、case、cutoff、requirement、trigger 和 Session identity 校验并生成逐案 report/provenance；dev 恢复 27 Evidence/93 facts/1 个可信 Review、hard coverage `0.8055555555555555`，holdout 恢复 26 Evidence/90 facts/0 Review、hard coverage `0.6944444444444445`，matrix hash 为 `6850c512bbca56d78c5ac9ea9355f68ebeb2ca006762ebe2c870f7d5a39b542b`。应用当前 `independent_review_receipt` 门后，dev/holdout 都是 quality success `0/6`、manual rework `6/6`；此前 dev `1/6` 是旧导出把 `total_subagents` 当作独立核验的历史结果，现以权威归档复核为准。矩阵保存在 `data/decision-hub/evaluations/core-v3-authoritative-session-readback/matrix.json`。

### 2026-09-12：解除分钟历史数据对核心开发的错误阻塞

- 重新核对 V3.1 原文：三个月市场背景优先使用小时级数据，只在具体事件反应分析中读取分钟窗口；取不到的预期、期权或分钟宏观数据应显示为缺口，不能以代理冒充，但原文没有要求因此暂停所有核心工作。
- 将 US2Y/DXY 授权分钟文件改为冻结事件回放的外部待办，不再作为 G1-C3 主链开发前置。适用事件仍保持严格 Gate，确保缺数据时诚实降级。
- 当前工作转入新鲜任务效果闭环：先验证材料取得、有效补查、可信 Review、引用回读和报告质量，再扩展到 dev/holdout；未证明减少遗漏或人工返工前，G1 仍为 `retain_baseline`，M04/M05 仍不启动。

### 数据取得决策（不作为开发阻塞）

| 路径 | 能直接支持的内容 | 成本/限制 | 当前处理 |
|---|---|---|---|
| FRED DGS2/DGS10/DTWEXBGS、美国财政部 XML/CSV | 日频利率、美元背景和三个月宏观背景 | 免费；不是事件前后分钟事实 | 立即可用，保留原频率和缺口 |
| Binance Vision | BTC spot、funding、OI、basis、crowding 的 1m/历史窗口 | 公开下载；只覆盖 Binance 资产 | 已接入并用于回放 |
| Kalshi | 有对应合约时的事前预期 | 合约存在性、历史访问和许可受限 | 有就用，没有就明确缺口 |
| Twelve Data 等免费账户 | 可能覆盖部分盘中资产 | 需要注册 key；免费额度和 `US2Y` 历史权限需逐符号验证，DXY 当前未确认 | 只做可用性探针，不直接进入 hard Gate |
| CME DataMine/Databento | ES、国债期货的历史盘中 bars | 需账户/授权；Databento 有按量历史方案，交易所许可仍需核对 | 仅在要做严格事件回放时申请 |
| ICE DXY 历史服务 | DXY 盘中/逐笔历史 | 商业授权服务 | 仅在 DXY 即时传导成为必需产品能力时申请 |
| 从现在开始的实时采集 | 后续新事件的 t-5m/t+1m 自有回放 | 只能覆盖采集之后；须遵守上游许可 | 作为低成本长期积累方案 |

当前冻结样本只有 12 个事件（dev 6 + holdout 6，2024-04-03 至 2025-03-07），严格回放真正需要的是这些事件附近的目标槽位和前一日 US2Y 基线，不是购买一整套多年分钟数据库。若暂时不做严格事件回放，任何免费日频/小时数据都只用于背景研究，不能把结果标成已验证的即时传导。

### 2026-09-13：G1-C3 新鲜 DSH 代表任务闭环

- 用隔离 Hub API、官方 DSH Web Host 和 `codexai.club/v1` 的 `gpt-5.5` 运行 `iran_attack_israel_20240413`，先后排除了两类启动配置错误：Host/MCP bridge key 不一致，以及 Research MCP 未指向同一隔离 `DECISION_HUB_DATA_DIR`。这两次结果均作为环境故障记录，没有计入质量结论。
- 修复 `result_mapper` 的 Review 白名单边界：原生 reviewer 回执可以合法引用 canonical input/trigger Evidence，但映射层只允许新返回 Evidence，导致真实回执被静默丢弃。现在 Review 与 synthesis 使用同一组 input + attested Evidence 白名单，并新增注释和回归验证。
- 修复后的真实结果保存在 `data/decision-hub/evaluations/g1-c3-geopolitical-20260912e/`：8 次研究调用全部完成，成功回读 4 条 Evidence、14 个 typed facts（BTC spot 与 derivatives 的 t-5m/t+1m），1 份 `requires_new_evidence` 原生 reviewer，三段 30m/24h/72h horizon，hard coverage `1.0`，PIT/引用一致性通过，质量成功 `1/1`、人工返工 `0/1`。缺失 expectation、rates/USD、cross-asset 和 counter-thesis fixture 被保留为明确 soft gaps，未被代理指标替代。
- 同一实验的 baseline 为 hard coverage `0.0`、quality success `0/1`；candidate 为 hard coverage `1.0`、quality success `1/1`、manual rework `0`，comparison 结论为 `promotion_recommended`。这只证明代表案例的主链改善，尚不足以改写六例 dev/六例 holdout 的 G1 裁决。
- 下一项仍只做核心质量工作：把同样的修复和统一环境扩展到 dev/holdout fresh comparison，检查质量/返工改善是否稳定；不启动 M04/M05，不把缺失宏观分钟数据改成可用。

### 2026-09-13：G1-C3 dev/holdout 新鲜对照完成

- 使用与代表任务相同的隔离 Hub API、官方 DSH Web Host、`codexai.club/v1` / `gpt-5.5`、Research MCP 和数据库配置，重新运行 `r2r_dev_core_v3` 与 `r2r_holdout_core_v3`，没有出现 bridge key、Session 不存在、DB 串线或 Host 不可用等环境失败。
- dev 结果保存在 `data/decision-hub/evaluations/g1-c3-dev-20260913/comparison.json`：baseline 6/6 完成、新 Evidence 0、hard coverage `0.0`；DSH Web 6/6 完成、49 次工具调用、6 个 reviewer subagent、27 条 Evidence、hard coverage `0.8055555555555555`、PIT 违规 0、unattested Evidence 0，P95 `205117ms`。
- holdout 结果保存在 `data/decision-hub/evaluations/g1-c3-holdout-20260913/comparison.json`：baseline 6/6 完成、新 Evidence 0、hard coverage `0.0`；DSH Web 6/6 完成、48 次工具调用、6 个 reviewer subagent、26 条 Evidence、hard coverage `0.6944444444444445`、PIT 违规 0、unattested Evidence 0，P95 `211085ms`。
- 两个集合的候选质量成功均为 `0/6`、人工返工均为 `6/6`，所以 comparison 结论均为 `retain_baseline`。逐案原因主要是数据包缺少 `macro_transmission`（rates/USD 分钟传导）导致有界降级；没有把日频或代理资产冒充事件分钟事实。dev 的伊朗案例还暴露出一次模型未返回独立 reviewer receipt，已被质量门正确标记为 `independent_review_receipt`。
- 这轮证明核心链路在 12 个真实 DSH 任务上可重复取得 Evidence/facts、执行 gap-driven research、生成三段 horizon 并把缺口写进 stop reason；同时也证明当前数据供应不足以让完整宏观事件达到可发布质量，不能据此升级 G1，也不启动 M04/M05。下一项核心输入仍是经授权且可校验的 US2Y/DXY 事件窗口数据，或改用后续实时采集覆盖新事件。
- 主链（`tests/research tests/evals tests/runtime tests/dsh_native tests/workbench tests/kernel tests/capabilities tests/migrations`）回归为 `453 passed、3 skipped`；Ruff、生产代码/工具 Pyright（0 errors）、canonical schema、module docs 和 `git diff --check` 通过。未纳入主链结论的 `tests/evolution` 仍有 2 个既有外围实验重启/候选哈希失败，未为此扩展本核心目标。
- 同日对公开历史分钟来源做了实际探针，结果固化在 `data/decision-hub/evaluations/core-v3-public-source-probe-20260913.json`：FirstRateData 的 ZT 免费样本可读但只有 `2026-08-26` 至 `2026-09-10`，不覆盖冻结的 `2024-04-03..2025-03-07`，且不是 USY02Y/DXA；Yahoo 的 2024 年 1 分钟请求返回 422/404，只有日频可回读；Twelve Data demo 对目标符号返回 401；Barchart、ICE、Portara 的分钟历史需要账户、API key 或商业数据许可；Dukascopy 的 DOLLARIDXUSD 是经纪商 CFD，不能冒充 Pack 的 ICE DXA，且没有 USY02Y 2 年期收益率。没有把这些探针结果写成 hard facts，因此不生成 core-v4、不放宽 Gate；免费链路仍限于有对应市场的 Binance Vision、存在合约时的 Kalshi，以及只用于背景的 FRED/Treasury 日频数据。

### 2026-09-13：原生 DSH recovery 验收与夹具修正

- 原生 acceptance 的 success replay 夹具改为调用统一 `research_fact_payload_hash()` 与 `research_fact_instance_id()`；此前使用占位 token hash，Gateway 按当前 typed-fact 语义校验时正确拒绝，导致验收夹具落后于 Pack Gate。partial/stale 场景仍保持 evidence-only 降级，没有合成事实覆盖。
- Windows acceptance runner 改用项目 `.venv/Scripts/python.exe`、Git Bash 的 `/c/...` 路径和短的 `C:\dsh-native-core` 运行根，避免 DSH Session 目录超过 Win32 路径限制；停止 Web 时按进程树清理 Git Bash/Node，防止 profile lock 残留阻断重启。Session 日志定位改为固定 `sessions/*/<session-id>/` 模式，避免递归扫描 DSH 缓存造成假性卡死。
- `tools/dsh_native_acceptance.py --recovery` 真实通过，结果保存为 `data/decision-hub/evaluations/core-v3-dsh-recovery-20260913.json`：callback 断连得到 `503 host_callback_unreachable` 后 Run 正常终止；同一 DSH Session 在 Web 重启后 `result_hash` 保持不变；不兼容 source version 被 `host_version_incompatible` 拒绝，恢复锁定版本后原结果 hash 不变；新 rollback Run 使用新的 Session 且 `publish`。success Run 为 `hard_coverage=1.0`、9 条 Evidence、0 工具失败。
- 同日三场景原生 acceptance 全部通过，汇总保存为 `data/decision-hub/evaluations/core-v3-dsh-native-acceptance-20260913.json`：success 为 `publish/sufficient`、hard coverage `1.0`；partial failure 为 `reject/insufficient` 且保留 1 条 Evidence、2 个工具失败；insufficient/stale 为 `reject/insufficient` 且保留 1 条 Evidence、1 个工具失败。三者均保留压缩 Session 归档，未把降级场景改写成成功。
- 三场景复跑中发现并修正了 `insufficient_or_stale` 场景的 Win32 路径长度边界：DSH 会把场景名编码进 Session 路径，临时根目录现使用单字符场景标记；success/partial/stale 复跑均通过，未改变业务 Gate。
- 验收夹具回归 `tests/dsh_native/test_dsh_native_acceptance_tool.py` 为 `8 passed`；reviewer replan + DSH runtime 定向回归为 `72 passed、1 skipped`；最终主链回归为 `453 passed、3 skipped`，Ruff、生产代码/工具 Pyright、canonical schema、module docs 和 `git diff --check` 均通过。该 recovery 证明 Host/Hub/Session 的真实恢复和版本固定链路，不等同于浏览器 UI snapshot+cursor 验收；CV5 浏览器级项继续保留未完成状态。

### 2026-09-13：真实 reviewer 补证修订闭环

- 先确认 `ResearchComparisonRunner` 为保持两候选直接对照而调用 `runtime.execute()`，不会经过产品的外层证据轮图；因此单次 comparison 中出现 `requires_new_evidence` 不能证明自动补证。正式 `DurableResearchWorker` 使用的是 `LangGraphResearchExecutor`，本次验收改从该生产入口执行同一冻结案例。
- 真实运行使用官方 DSH Web Host、隔离 Hub 数据库、`r2r_dev_core_v3` 的 `iran_attack_israel_20240413` 冻结 fixture 和当前 OpenAI-compatible 模型配置。Run `eval:core-v3-live-reviewer-replan-20260912T185211Z:iran_attack_israel_20240413:dsh-web-graph` 共完成 2 个 DSH generation、12 个受审计 Research tool call 和 2 个 reviewer subagent；第一代 8 次查询后返回 `requires_new_evidence`，产品图持久化以该 Review 为引用的 `replan`，第 2 代提示词包含固定 claim、challenge 和 limitations，并按剩余预算执行 4 次缺口查询。
- 第 2 代 4 次查询均针对 fixture 已声明缺失的 expectation、rates/USD、cross-asset 和 counter-thesis，结果为 `research_replay_fixture_missing`；系统保留明确未知，没有生成新 Evidence 或用代理数据补齐。已有 4 条 Evidence、14 个 typed facts 仍使该地缘事件的事件特定 hard Gate 达到 `1.0/sufficient`，最终状态为 `completed`，Decision Snapshot `snap_dec_2a4783b963d9c6b8ee6a9586f42b` 成功冻结。
- 脱敏验收产物保存在 `data/decision-hub/evaluations/core-v3-live-reviewer-replan-20260913.json`，文件 SHA-256 为 `fb281d34a64c72d776eb3d6a572b0c541522988d0b05d4147f767c6039f3c83e`。断言包括 Review replan 轨迹、两代提示词、修订指令传递、第二代真实查询、Run 级工具预算、MCP 鉴权、PIT、Evidence/Review attestation 和决策快照；Provider 密钥和 DSH 页面 token 未写入产物。
- 本次关闭 CV3 的真实 reviewer 自动补证缺口，不改变全量 dev/holdout 的 `retain_baseline`：授权 rates/USD 数据仍未到位，旧批次多数 Session 也没有可信 Review。下一项只剩 CV5 浏览器 snapshot+cursor 恢复证据；M04/M05 继续停止。

### 2026-09-13：浏览器 snapshot+cursor 恢复闭环

- 真实浏览器检查发现刷新后会回到 `source` workspace 的较新非产品 Session，虽然 canonical Hub API 仍能回读正确 Run。根因是客户端把角色标题 `Crypto Macro Trader` 当成 workspace 标识，而当前 DSH 的真实 workspace 标题/路径末段是 `decision-hub`；原单测用虚构的同名 workspace，遮住了生产命名差异。
- 客户端启动现在先读取 canonical Research Inbox，使用最新有效 `dsh_session_id` 经官方 Session Controller 执行 refresh/binding/open；Inbox 不可用或没有受管 Session 时才沿用原 workspace 入口。新增回归以真实 `source` + `decision-hub` workspace 结构证明会选择 Inbox 绑定的 Session，插件完整测试 `78 passed` 且生产 build hash 为 `2fee57442a6276418093be61c25766e1c7d931ac5e112714f2aa39fd7919b28a`。
- 重建并重启官方 DSH Web 后，以 Edge/Playwright 打开同一产品入口，不做人工 Session 选择。首屏与刷新后均自动显示目标 Run、`2 个子代理`、`仅研究完成 · 仅研究` 和 `证据充分 (100% hard)`，没有回到先前的 `3 个子代理` Session。
- 刷新前后签名完全相同：Run/DSH Session ID、`research_only/done`、round 2、`latest_sequence_no=78`、Trigger Snapshot ID/hash、Decision Snapshot ID/hash、4 个 Evidence ID、1 个 Review ID和 2 个 subagent 都未变化。上游模型回合的 `EMPTY_RESPONSE` 提示仍如实可见；Host 只因已验真的 synthesis tool result 将其投影为已完成，Hub 业务状态继续保留 `research_only` 和失败明细，没有改写成 publish。
- 脱敏产物为 `data/decision-hub/evaluations/core-v3-browser-snapshot-cursor-recovery-20260913.json`（SHA-256 `48e0b8436294d0af290336568aeb0c716937f5e25d8c9902a508089861997116`），对应刷新前后截图也记录在产物中；JSON 校验和密钥/token 扫描通过。本次关闭 CV5 浏览器恢复缺口，不改变 G1 `retain_baseline`，也不启动 M04/M05。
- 最终验证：DSH 插件 `78 passed`，研究主链 `454 passed、3 skipped`；Ruff、生产代码/工具 Pyright（0 errors）、canonical schema、module docs 和 `git diff --check` 均通过。包含测试目录的额外 Pyright 扫描仍报告 30 个测试类型问题，类别为 private helper 引用、pytest fixture 缺标注、可选值未先收窄和测试桩 `object` 过宽；运行测试均通过，本次不把整理测试类型扩成新的外围阶段。

### 2026-09-13：UI 输入到报告的实时主链修复

- 当前工作严格限定为输入 → 路由 → 实时取证 → synthesis → Review → Gate → 报告，没有启动 M04/M05、外围评测平台或 Docker 修复。持续记录见 `docs/plan/CORE_CHAIN_DEV_LOG.md`。
- 批次 007（`run_a5d55837fbf3454bba2a99790d836101`）从真实 UI 同源入口进入，取得 8 条 OKX Evidence，但 hard coverage 只有 `0.5`。根因是当前 BTC 快照错误套用宏观事件合同，且 OKX price/volume facts 被错误标为 `crypto.derivatives`。新增 `crypto_market_snapshot` 事件族和快照专用证据合同，并把 spot facts 修正为 `crypto.spot`；没有放宽字段、来源或引用校验。
- 批次 008（`run_a26bd65e01244d7e94e72b8f2ddccbe6`）修正后 hard coverage 达到 `1.0`，但模型将一个 35 字符 Evidence ID 写成 34 字符，synthesis attestation 正确拒绝。Web runtime 现允许一次同 Session 的 synthesis-only repair generation：提示词给出精确 ID 白名单，只能调用 `decision_hub_synthesis_submit`，任何 research/web/subagent/todo 调用都 fail-closed；回归证明修复不会重复取证。
- 批次 009（`run_48c6d101a9ad4730b134abb699459bce`）取得 8 Evidence、hard coverage `1.0`、causal case、3 horizons 和 1 Review，但 Host 在 synthesis tool 成功后主动结束空尾回合产生的 `turn/end=aborted` 被事件映射误判为 `dsh_finish_aborted`。映射层现在只在当前 generation 有严格 attested synthesis call/result 时将该受控结束记为成功，历史 generation 不能掩盖当前失败。
- 最终批次 010（`run_6388e488453947f7a5b4489f5e5ad158`）Host 为 `completed`、generation 1、`error_code=null`；产品 failure 为空，Coverage 为 `sufficient`、hard coverage `1.0`，保留 8 Evidence、causal case、30m/24h/72h 三段结论和 1 Review。三个 horizon 均为 `no_trade`，所以既有 Gate 正确生成 owner-visible `research_only/degraded` 报告；这次状态代表审慎业务结论，不是主链故障。
- 验证为 OKX/FactPack/Web runtime `48 passed`，runtime/DSH Web `77 passed、1 skipped`，DSH Host/Client `83 passed`，本轮生产文件 Pyright `0 errors`。脱敏汇总为 `data/decision-hub/evaluations/core-live-chain-20260913.json`（SHA-256 `9d9c5b60c8c0bb8de9db5156ca5b5ed191b97f60222e80d4514b50053e039adb`）。
- 当前主链已通；真实效果的剩余限制是单交易所截面、缺少连续时序/跨交易所/宏观背景，以及约 90 秒端到端时延。下一项仍只做核心信息供给：近期小时序列和从当前开始积累的快照，然后用新鲜任务验证信息量与返工；授权历史分钟数据继续作为严格旧事件回放约束，不再阻塞主链开发。


### 2026-09-13：输入到报告主链 011–015（历史小时证据）

- [x] 只从 DSH 页面入口触发真实主链：intake → Hub → Worker → DSH Host → Research MCP → Gateway → OKX → synthesis → Review → Gate → report。
- [x] 将现有免费 OKX 1h 数据装入隔离实时目录，并用 Gateway 预检 24h/7d/90d 三个窗口。
- [x] 把 `market_regime_context` 定义为 soft typed-fact requirement；硬门仍只检查当前现货和衍生品/拥挤度。
- [x] 修复历史窗口锚点、MCP source manifest 重载和 synthesis repair Review 时间合并三个实际阻塞点。
- [x] 真实 015 主链完成：Host completed、无产品 failure、11 条 Evidence（3 条 `crypto.spot.history`）、hard coverage 1.0、causal case、3 horizons、1 Review。
- [x] 将 011–015 的失败复现、修复和结果写入 `docs/plan/CORE_CHAIN_DEV_LOG.md` 与脱敏 JSON；不把 no_trade 业务状态误判为执行失败。
- [ ] 建立每个已完成 UTC 小时的最小历史采集触发，避免实时归档落后一整点；这属于历史供给可靠性，必须在下个核心任务完成。
- [ ] 继续补充跨交易所确认、OI 变化历史和有许可的事件型宏观分钟源；在真实质量/人工返工指标改善前不启动 M04/M05。

### 2026-09-13：小时历史供给触发器

- [x] 在现有 `hub-worker` 入口增加 `--role historical`，只触发滚动 90 天的 OKX `crypto.btc_usdt.ohlcv_1h` DataJob，不创建 Research Run、通知或第二套队列。
- [x] 触发窗口固定为最近一个已完成 UTC 小时；幂等键包含该小时，重复 tick 返回 `already_complete`，不会重复请求或重复写入。
- [x] 真实隔离目录运行一次：`datajob_212f8b549da64532a3a2ae3dabad91d5`，覆盖 `2026-06-15T10:00:00Z` 至 `2026-09-13T10:00:00Z`，状态 `completed`；第二次相同小时触发返回 `already_complete`。
- [x] 为触发器补充“完成小时、不读未来、幂等、空返回可见且无 Research Run 副作用”测试；定向结果 `7 passed`。
- [ ] 用刷新后的历史供给再跑新的 prospective 研究任务，逐例比较缺口、引用支持、Review 和人工返工；这一步才决定 G1 是否能从 `retain_baseline` 变更。

### 2026-09-13：核心输入到输出链路收口（022–028）

- [x] 真实触发 DSH 页面同源入口并验证 `intake → Hub → Research Worker → DSH Host/Session → MCP → typed Evidence/Facts → synthesis → Review → Gate → Artifact/report`，不以内部 runtime 直调代替。
- [x] 修复跨 generation 的旧 Review 回执时间错配：`result_mapper` 丢弃当前结果时间范围外的 native receipt，交由当前结果执行确定性 Review；补充回归测试。
- [x] 将现有 `market.crypto_crowding` 订单簿能力加入 live Worker/MCP allowlist，并在 DSH playbook 固定单独调用，防止模型把 `crowding_signal` 发给 `market.crypto_derivatives` 造成路由失败。
- [x] 修复 Query View 将已提交 Artifact 的早期工具失败/stale evidence rejection 误显示为 run-level failure；Trace/observability 仍保留逐项失败证据。
- [x] 刷新免费 OKX 1h 数据到最新完成整点后，真实 028 完成：11 Evidence、6 capability calls、1 reviewer、3 个历史窗口、hard coverage `1.0`、stop reason `sufficient`、Artifact 已回读且 failure 为空。
- [x] 记录并隔离一次 Hub API 重启漏带 callback secret 的环境故障（027）；后续启动必须同时注入 Hub callback secret、Host bridge secret、Research MCP key 和同一数据目录。
- [ ] 继续用新鲜任务观察质量/人工返工是否改善；当前 028 只证明链路和数据字段可用，不能单凭 hard coverage `1.0` 宣布 G1 promotion。
- [ ] 补齐跨交易所确认、OI 变化历史、实时 rates/USD 与可审计宏观事件窗口；缺少授权输入时保持明确 gap 和 no-trade/neutral，不用代理数据冒充。
- [ ] G1 未通过前继续停止 M04/M05、Docker 修复、通用评测扩建和其他外围平台工作。

### 2026-09-13：prospective 029–031 主链可靠性复核

- [x] 从 DSH 页面同源 intake 连续触发 029-A、029-B、031；三条成功任务均通过取证、synthesis、Review、Gate 和 Artifact 回读，hard coverage 均为 `1.0`。
- [x] 记录 029-A 模型漏填 `request_id` 的真实失败轨迹；确认重试后结果仍可提交，但额外失败调用会增加延迟和噪声。
- [x] 修复 DSH Research Tool 缺省 `request_id`：直接绑定原始 `exec.callId`，避免引入无法被结果 attestation 识别的本地前缀；031 真实复跑确认 7/7 研究调用无工具错误且 Artifact 已提交。
- [x] 记录错误前缀导致的 030 `dsh_evidence_unattested` 回归；它被安全拒绝且无 Artifact，不计入产品质量指标。
- [x] 插件测试 `85 passed`，生产构建和 DSH readiness 通过。
- [ ] 029-A、029-B、031 的 soft coverage 仍约 `0.142857`，尚未证明关键遗漏或人工返工改善；继续保留 `retain_baseline`。
- [ ] 继续补跨交易所、OI 变化历史、实时 rates/USD 与可审计宏观事件窗口；没有授权输入时保持 gap，不用代理冒充。

### 2026-09-13：核心链路 032–034 复核与供给修复

- [x] 重新触发真实 DSH 页面同源任务 034，完成 intake → 事件/需求 → OKX 取证 → Evidence/typed facts → synthesis → Review → Gate → Artifact/report 回读。
- [x] 记录 033 的真实历史供给滞后：固定 3600 秒休眠会因进程启动偏移错过完成 UTC 小时，触发 `historical_dataset_coverage_missing`；该次按 fail-closed 拒绝不可信 synthesis。
- [x] 修复 `--role historical --interval 3600` 的 UTC 整点对齐和 5 秒 settling delay，保留非小时本地验证间隔；重启常驻 historical worker。
- [x] 修复引用价值指标按 Evidence ID 计算的逻辑，并以 034 实际结果验证 `citation_traceability=0.9091`，不再把有效 Artifact 误报为 0。
- [x] 034 终态验证：failure 为空、Host `failed_tool_results=0`、hard coverage `1.0`、14 Evidence、1 Review、三段 horizon 均有报告输出。
- [x] 定向回归 `78 passed、1 skipped`，生产 Pyright 0 errors，Ruff 和 diff check 通过。
- [ ] G1 仍未通过：soft coverage 约 `0.142857`、usefulness 未标注，跨交易所/OI 变化/实时宏观数据仍缺；不启动 M04/M05 或外围评测扩建。
- [ ] 仅在取得合规新鲜数据后，继续观察 accepted Evidence 引用完整性、关键缺口和人工返工是否改善。

### 2026-09-13：G1 最终审计与范围收缩

- [x] 重新审计冻结 dev/holdout：两边各 6 案例真实 DSH 完成取证，quality success `0/6`、manual rework `6/6`，PIT/unattested 均为 0；G1 不满足质量或返工改善门。
- [x] 复核可取得数据路径：免费公开样本、Yahoo/Twelve Data、Portara/CQG/ICE 公共样本、Kalshi 和 USMPD 均不能同时满足冻结日期、USY02Y/DXA 身份、原始分钟 bar、`t-5m/t+1m`、时效和来源授权约束。
- [x] 复核模型路径：原生 Responses canary 与真实 DSH `gpt-5.5` 任务均可完成主链；当前瓶颈是缺失 hard Evidence，模型替换不能解决。
- [x] 形成产品范围收缩决策：保留手动当前 BTC/USDT 快照研究；冻结历史宏观事件方向研究暂不发布/不自动化，直到授权分钟数据或未来事件采集到位。
- [x] 将决策、运行指标、来源探针 hash 和重启条件写入 `data/decision-hub/evaluations/core-v3-product-scope-decision-20260913.json`；JSON 校验和敏感信息扫描通过。
- [x] 保持 `macro_transmission` hard gate、`--require-full-hard-data` 和 fail-closed；不生成 core-v4，不启动 M04/M05。
- [x] 当前主链代码修复回归保持 `78 passed、1 skipped`，生产 Pyright 0 errors，Ruff 与 diff check 通过。

这条范围收缩满足方案要求的“G1 未通过时形成有运行证据的产品 no-go/收缩结论”。后续只有满足重启条件才重新进入 dev/holdout 质量验证；在此之前不继续重复模型调用。

### 2026-09-14：真实产品逐功能走查与收口

- [x] 从空白 DSH 会话提交当前 BTC/USDT 问题，真实完成 intake → Worker → DSH → Research MCP → Evidence/Facts → synthesis → Review → Gate → Artifact/report。
- [x] 自动点击并截图 DSH 新会话、输入、提交、报告、主动研究、轨迹、Session 日志，以及 Desk Research、Evidence、gap、Review、因果链、horizon 和 Operations。
- [x] 修复 Decision Desk 旧 dist 导致“研究详情不可用”；一键启动器强制先 build 再启动 Hub。
- [x] 移除会打开同一空抽屉或无响应的占位入口，只保留当前真实可用的 Research 与 Operations。
- [x] 修正 Operations 状态边界，只统计 Hub API 与 Research Worker，并明确旧 observation runtime 不代表 DSH live。
- [x] 在 Research 详情中显示 accepted/flagged Evidence 计数与独立 Review 原始记录。
- [x] 从当前快照证据合同删除事件专属的三个 requirement 和被错误建模为取证项的 counter thesis。
- [x] 最终真实 Run run_62ae59168aef4378a4188df91e7e86c7 完成：hard 100%、soft 33%、14 Evidence、1 Review、3 个 neutral/uncalibrated horizon、2 个真实 soft gap。
- [x] 新增并真实运行 Windows run-product.ps1；重启后 API、DSH readiness、版本兼容、核心服务 2/2 和 Run 回读通过。
- [ ] 数据价值约束仍未关闭：跨交易所、OI 时序、实时宏观/跨资产确认、概率校准和人工 usefulness 标签。
- [ ] 下一次新鲜 Run 检查固定 FRED 三序列“一次调用”提示是否消除额外 research_macro_series_denied；不为此扩大到外围评测。
