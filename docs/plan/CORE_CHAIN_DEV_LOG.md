# Decision Hub 核心链开发记录

## 当前目标

只验证并修复产品真实主链：用户输入 → 事件识别 → 证据需求 → 真实数据取证 → 证据/事实校验 → 因果综合 → 独立 Review → Gate → 用户可回读报告。

在主链形成稳定输出以前，不把 M04/M05、通用评测平台、调度器扩建、Docker、数据大盘或更多边界测试纳入当前工作。缺失的历史分钟数据继续作为严格事件回放的数据约束，不阻塞当前快照链开发。

当前执行目标为 **核心可交付（2026-09-19）**：固定“获取最新操作建议”入口，通过真实 DSH/模型与公共数据，稳定输出 BTC 未来 7 日方向、当前行动候选、根因链、正反证据、失效条件及数据缺口影响。衍生品必须真实进入报告，观点、可信度、执行权限分层。以连续真实 Run、页面回读及故障回归作为验收；不以工具成功数代替产品成功，不以放宽 Gate 或虚构胜率制造结论。旧 C0a 等记录保留为历史，当前状态以文末最新验收记录为准。

### 本次交付 checklist

- [x] 7d 原生方向合同、日线规则收据与执行权限分离。
- [x] 当前快照证据新鲜度与事件族一致；当前方向必须取得完整日线。
- [x] 可达 Gate/HTX 衍生品与双现货来源接入，网关负责双来源获取。
- [x] 新任务保持选中；报告 URL 与 run_id 正确绑定。
- [x] 工具调用身份由运行时生成；幂等缓存绑定完整请求内容，防止跨能力数据串用。
- [x] 连续真实简单输入取得日线、现货、宏观/跨资产、衍生品，并得到可回读报告。
- [x] 首屏能回答当前方向、当前行动、正反证据、失效条件、缺口具体影响。
- [x] 核对主链错误、实际复核回执、概率标注，保存最终 API 回执与截图。
- [x] 更新验收记录和使用引导；仅满足上述条件才完成目标。

## 2026-09-18：C0a 暂存进度与当前阻塞

本节是当前目标的阶段性快照，不能当作 C0a 完成证明。当前仍只处理一条纵向核心链，不启动角色、PPT、调度、社交/KOL、自动交易或通用评测扩建。

### 已完成并可复核

- 已确认模型服务本身可用：使用已配置的 OpenAI-compatible Responses endpoint 和 `gpt-5.5` 做最小文本请求，返回 `completed`；这排除了“模型服务整体不可用”这一假设。密钥不写入日志。
- `infra/dsh/run-product.ps1` 已支持显式 `-DshHome`，并在每次启动时同步 `decision-research` preset，避免恢复 DSH_HOME 缺 preset 导致 session submit 长时间不返回。
- `OpenAIResponsesWebSearchTransport` 增加显式 `DECISION_HUB_SEARCH_ALLOW_LOCATOR_ONLY=1` 开关。上游只返回 `action.sources` 而没有 `url_citation` 时，只生成标记为“必须 fetch”的 locator；默认仍严格拒绝无绑定引用，locator 不得直接进入 accepted factual Evidence。
- 搜索适配器定向测试：`33 passed`。既验证默认 fail-closed，也验证显式 locator-only 后仍保留 fetch-required 语义。
- 干净启动后 Hub API、Research MCP、DSH Web 和 worker readiness 均能启动；DSH `/decision-hub/v1/readiness` 返回 `ready=true`，说明启动层和版本/插件握手已恢复。

### 本阶段真实 Run 证据

- `run_6f1460fdd0694088a4aca5c4ecf982f8`：曾真实进入 DSH Agent Loop，收到 `accepted_at`，执行了多次 market/web.fetch 工具调用；其中 Federal Reserve feed 与 speech 页面成功形成 accepted Evidence。随后因恢复进程的 market capability 状态被判定为 terminal，第二轮在 `market.crypto_crowding` 处停止，hard coverage 仍为 `0%`，因此没有伪造方向结论。
- `run_174f6d637adc4e47b398c5d6adb769e0`：使用简单输入“现在怎么操作”走干净启动器，DSH session 已创建但首个模型 turn 立即返回 `dsh_turn_error`，没有产生任何 capability call。当前 Hub 状态为 `failed/done`，不能算主链通过。
- 该失败与模型服务健康不是同一问题：同一配置下的直接 Responses 文本探针已返回 `completed`。下一步必须抓住 DSH turn 的 provider/请求级错误，并修正 DSH 运行时到模型的配置或错误回传，不得再用“no_trade”掩盖零工具调用失败。

### 当前结论与退出条件

当前状态是 **C0a 未完成、主链仍不可交付**，原因已经从“环境整体 blocked”收窄为两个可执行问题：

1. DSH live turn 在新启动器上未把上游 provider 错误回传到 Hub，导致 `dsh_turn_error` 只有笼统的 `failed`；必须先让最小模型 turn 在 DSH 内成功或给出可诊断的真实错误。
2. 研究 MCP/worker 的 durable session 状态在进程重启后仍可能把后续能力请求视为 `research_run_terminal`；必须用同一干净进程组完成一次从输入到 market snapshot、search locator、web.fetch、Evidence、Review、报告回读的 Run。

C0a 的完成证明仍要求：一次真实简单输入 live Run 非 replay/fixture，至少两类独立上游信息，search→approved fetch→accepted Evidence，首屏能读到主机制/反方/动作条件/失效条件/限制，且记录真实 run_id、工具回执和 UI 回读。只有这条证据齐全后，才可从“研究助理抢救”进入下一阶段。

## 2026-09-13：BTC 当前快照真实主链

所有批次都从 DSH 页面使用的 `POST /api/decision-hub/research` 进入，经过 Hub、Research Worker、官方 DSH Web Host、Research MCP、OKX 公共接口和产品 Gate。没有直接调用内部 runtime 伪造成功。

| 批次 | Run | 结果 | 硬覆盖 | 实际发现 | 处理 |
|---|---|---:|---:|---|---|
| 007 | `run_a5d55837fbf3454bba2a99790d836101` | `research_only` | 0.50 | 输入是当前 BTC 快照，却沿用宏观事件合同；修正合同后又发现 OKX 现货事实被标成 `crypto.derivatives` | 新增 `crypto_market_snapshot` 事件族；当前快照只把现货确认和衍生品/拥挤度设为 hard；修正 OKX spot metric family |
| 008 | `run_a26bd65e01244d7e94e72b8f2ddccbe6` | `rejected` | 1.00 | 8 条数据和 hard Gate 已齐，但模型复制一个 Evidence ID 时少了一位，合成 attestation 正确拒绝 | 增加一次同 Session、下一 generation 的 synthesis-only 修复；只允许合成工具，使用精确 Evidence ID 白名单，禁止重复检索 |
| 009 | `run_48c6d101a9ad4730b134abb699459bce` | `research_only` | 1.00 | 证据、因果链、三周期和 Review 都成功；Host 为避免空尾响应，在合成提交后主动 cancel，`turn/end=aborted` 被上层误记为失败 | 只有当前 generation 存在已验真的 synthesis call/result 时，才把该受控 `aborted` 映射为成功；历史 generation 的 synthesis 不参与判断 |
| 010 | `run_6388e488453947f7a5b4489f5e5ad158` | `research_only` | 1.00 | 主链完成且没有运行错误；三个周期均为 `no_trade` | 验收通过；`research_only` 是 Gate 对有效 `no_trade` 报告的业务状态，不是执行失败 |

## 批次 010 的输入到输出证据

- Host：`completed`，`error_code=null`，generation 1，结果 hash 已持久化。
- 数据：8 条 OKX 公共 Evidence，覆盖现货 price/volume、funding、open interest、mark/index、basis 和 order-book crowding。
- Coverage：`sufficient`，hard coverage `1.0`，hard requirements 为 `crypto_spot_confirmation` 与 `derivatives_crowding`。
- 研究语义：存在 causal case、30m/24h/72h 三个 horizon 和 1 份独立 Review；所有引用都来自保留的 Evidence。
- Gate：所有确定性检查通过；三个 horizon 均选择 `no_trade`，所以产物保持 owner-visible `research_only`，Run 按既有状态模型显示 `degraded`。
- 用户输出：报告包含当前价/量、资金费率、OI、mark/index、basis、盘口不平衡、反论点、触发条件、失效条件和缺失信息。

汇总证据在 `data/decision-hub/evaluations/core-live-chain-20260913.json`，批次 007–010 的 detail/observability 和批次 010 的 Host 摘要位于同一目录。

## 本轮代码改动

- `extensions/dsh/decision-hub/src/host/hub-client.ts`：从当前 BTC 快照输入确定性选择 `crypto_market_snapshot`。
- `packs/crypto_macro/evidence/source_manifest.yaml`、`packages/provider_adapters/research/fact_pack.py`：定义快照专用 hard/soft 合同和 spot/derivatives 字段要求。
- `packages/provider_adapters/market/okx_research.py`：补齐 OKX 现货、衍生品和基差字段，并把现货事实归入 `crypto.spot`。
- `packages/runtime_adapters/dsh_runtime/web_runtime.py`、`profile.py`：增加一次受限的 synthesis-only 结构修复，不重新取证。
- `extensions/dsh/decision-hub/src/host/session-result.ts`、`bridge.ts`：保留跨 generation 的可信产品调用，并按当前 prompt 判断合成终态。
- `packages/runtime_adapters/dsh_runtime/event_mapper.py`：把合成成功后的受控结束映射为成功，不再污染产品 failure。

## 自测

- Python 定向主链：OKX、FactPack、Web runtime 共 `48 passed`。
- Runtime/DSH Web 回归：`77 passed, 1 skipped`。
- DSH Host/Client：`83 passed`。
- Pyright：本轮生产文件 `0 errors`。
- 真实批次 010：Host `completed`、产品 `error_code=null`、hard coverage `1.0`、8 Evidence、3 horizons、1 Review。

## 尚未解决的核心效果限制

当前快照主链已通，但一次截面只来自 OKX，缺少连续时间序列、跨交易所确认和宏观/跨资产背景；模型因此给出 `no_trade` 是合理的保守结论。批次 010 端到端约 90 秒，仍偏慢。下一步核心工作应增加可合法取得的近期小时序列和从现在开始的定时快照积累，再用新鲜任务检查结论信息量和人工返工是否改善；历史 US2Y/DXY 分钟授权继续只影响严格旧事件回放。


### 2026-09-13：主链历史小时序列接入与 011–015 真实复跑

这次只处理报告里已经确认的核心缺口：单次 OKX 截面无法回答“当前压力是短暂现象还是持续状态”。代码原本已有 `data.historical` 的读取适配器，但没有历史行情要求，也没有在 DSH playbook 中告诉模型如何调用；实时目录也没有归档分区，因此之前的报告只能正确地写“缺少历史小时摘要”。

- 新增 soft requirement `market_regime_context`（canonical `market_regime_context`），要求 `crypto.spot.history` 的 `period_open`、`period_close`、`return_pct`、`volume_sum`、`row_count`；它不改变当前快照的两项 hard requirement，也不放宽 PIT、来源、单位或 venue 校验。
- DSH `data.historical` playbook 固定使用免费 OKX `crypto.btc_usdt.ohlcv_1h`，按输入实际接收时间截断到最近完成整点，要求 24h、7d、90d 三个显式窗口。之前错误使用执行 deadline 会生成未来窗口，已改为输入时间锚点。
- 在隔离实时数据目录完成两次 OKX 公开历史任务，最终覆盖 2026-06-15 10:00 UTC 至 2026-09-13 10:00 UTC；Gateway 预检三窗口分别返回 24、168、2160 根小时记录的摘要，均为 accepted typed facts。
- 真实复跑暴露并修复两处主链问题：011/013 的归档整点滞后与严格窗口拒绝；012 的长期 MCP 进程未重载新 source manifest；014 的 synthesis repair 生成时间覆盖逻辑导致 `research_native_review_time_mismatch`。最后修复为保留第一代逻辑运行起点，再使用修复代次终点。
- 015 从页面同源 `POST /api/decision-hub/research` 完成到产品报告：Host `completed`、generation 1、`error_code=null`；6 次受审计 capability calls、1 个 reviewer、11 条 accepted Evidence（其中 3 条历史行情）、hard coverage `1.0`、3 个 horizons。报告明确引用 24h `-0.7188%`、7d `-3.8662%`、90d `+17.0756%`，解释短中长期分化，并把未平仓量变化、跨交易所确认和宏观背景继续列为缺口。
- 015 的 `research_only/degraded` 是现有 Gate 对 no_trade 结论的产品状态，不是执行失败；它说明历史信息已经进入因果链，但数据仍不足以支持无条件方向交易。完整脱敏汇总及 SHA-256 记录在 `data/decision-hub/evaluations/core-live-chain-20260913-followup.json`。

本轮自测：`74 passed, 1 skipped`（FactPack、runtime、historical）；`89 passed, 1 skipped`（DSH Web、agentic graph）；相关生产文件 Pyright `0 errors`。未启动 M04/M05、Docker 修复或外围评测扩建。

当前剩余核心约束是历史供给必须持续更新到每个已完成 UTC 小时，否则严格窗口会再次出现 `historical_dataset_coverage_missing`；还缺跨交易所、OI 变化历史和事件型宏观分钟数据。下一步应把小时任务做成已有 Worker 可调用的最小定时采集，并用连续新鲜任务观察报告是否稳定减少“缺历史”缺口；在没有质量/返工改善证据前，G1 仍不升级，M04/M05 仍停止。

### 2026-09-13：小时历史供给触发器

为关闭实时目录整点滞后的核心供给问题，在现有 `hub-worker` 入口增加了 `--role historical`。它每次只创建一项滚动 90 天的 OKX `crypto.btc_usdt.ohlcv_1h` DataJob：窗口结束点是当前已完成 UTC 小时的起点，幂等键绑定该小时，重复触发只读取已完成作业，不触发 Research Run、通知或任何新的研究任务。常驻运行时由外部进程按 `--interval 3600` 调用，不引入第二套 scheduler。

真实运行使用 `data/core-live-20260913` 隔离目录：首次触发创建 `datajob_212f8b549da64532a3a2ae3dabad91d5`，状态 `completed`，覆盖 `2026-06-15T10:00:00Z` 至 `2026-09-13T10:00:00Z`；同一小时再次触发返回 `already_complete`。这样下一次 DSH 研究可读取到完整的 24h/7d/90d 小时窗口，且不会因为重复 tick 产生重复研究或重复数据作业。

新增触发器测试覆盖已完成小时锚点、90 天滚动范围、同小时幂等和空返回失败可见性，结果 `7 passed`。这只是恢复数据供给的必要条件，还没有把 G1 判为通过；下一步必须用刷新后的供给执行新的 prospective 任务，观察关键缺口和人工返工是否实际减少。

### 2026-09-13：输入到输出主链 022–028 复核与修复

本轮只触发真实 DSH 页面同源入口，检查输入、路由、取证、模型终态、Review、Gate 和报告回读，不扩展外围测评或平台任务。

- 022 证明了 DSH Host → Worker → Research MCP → OKX → synthesis → Review → Artifact 的主链可以完成，但 MCP/历史目录和整点供给配置不一致时，报告会明确保留 `historical_dataset_coverage_missing`，而不是伪造历史事实。
- 023 暴露一个真正的终态缺陷：同一 DSH Session 的前一代原生 Review 回执可能被带入后一代 synthesis 结果，`created_at` 不在当前结果时间范围内，Worker 因 `research_native_review_time_mismatch` 丢弃整次结果。`packages/runtime_adapters/dsh_runtime/result_mapper.py` 现在只绑定当前结果时间范围内的 Review 回执，旧代回执被丢弃并交给当前 Worker 重新做确定性 Review；回归测试已加入 `tests/runtime/test_dsh_research_runtime.py`。
- 024 复跑确认旧代 Review 修复有效：报告已经落库，`research_only` 是硬缺口下的保守业务结果，不再是 Worker 失败。
- 025 发现运行配置虽然声明了 `market.crypto_crowding`，但 Worker 进程仍使用旧 capability allowlist；模型因此把 `crowding_signal` 错发给 `market.crypto_derivatives`，得到 `provider_route_unavailable`。本地 `data/dsh-live/.env` 和实际 Worker/MCP 启动 allowlist 已补上 `market.crypto_crowding`，并在 DSH playbook 中明确要求单独调用订单簿能力，禁止把拥挤度字段塞进衍生品快照请求。
- 026 证明订单簿能力能返回带 `proxy_kind=orderbook_imbalance` 的 typed fact，但当小时历史触发器只更新到上一整点时，当前请求仍会出现历史缺口。该问题促使下一次先刷新完整小时窗口再触发研究。
- 027 是一次可复现的环境重启故障：重启 Hub API 时漏带 DSH callback secret，Host 卡在 `admitted`，截止时间后才失败。该批不计入产品质量；启动检查已补齐回调密钥，避免把配置故障误写成模型失败。
- 028 在刷新 `crypto.btc_usdt.ohlcv_1h` 到 `2026-09-13T12:00:00Z` 后完成真实主链：Run `run_a31f0d7c10224335a99211cd44773a05`，Host/产品均完成，Artifact `art_46f836e4472c4b61b30c6e37aa28e6ba`，11 条 Evidence（2 条现货、5 条 OKX 衍生品、1 条 OKX 订单簿拥挤度、3 条小时历史），6 次受审计 capability calls、1 个独立 reviewer，hard coverage `1.0`，stop reason `sufficient`，产品 failure 为空。24h/7d/90d 窗口均返回完整 24/168/2160 根小时记录，订单簿 `crowding_signal` 成为正式可引用事实。
- 查询层同时修复了“已提交 Artifact 但早期工具失败仍显示运行失败”的误导：`packages/query_views/research/service.py` 现在只在无 Artifact 的终态或显式 Run error 时生成 run-level failure；单条 stale/semantic rejection 和已恢复的工具失败仍保留在 Trace/observability，不污染已完成报告状态。

本轮自测：`tests/runtime/test_dsh_research_runtime.py` `64 passed, 1 skipped`；研究 API + runtime 定向回归 `71 passed, 1 skipped`。真实 028 证明当前快照的输入到报告主链已可稳定落地，但报告仍会对缺少跨交易所、OI 变化历史、实时宏观和独立 cross-asset 的部分保持中性/保守，不把单一 OKX 代理扩写成全市场结论。G1 仍保持 `retain_baseline`，M04/M05 继续停止。

### 2026-09-13：prospective 029–031 与 Tool request_id 兜底修复

为验证 028 不是一次偶然成功，继续从 DSH 页面同源 intake 触发三条新鲜任务，仍只观察主链终态、证据绑定和报告信息量。

- 029-A `run_8c47d83f3ab74bb4a618d9c0fee8111d` 完成，Artifact `art_cf62b67f5c59443b931fe1bc78a885f9`；14 条 Evidence、7 次 capability calls、1 个 Review，hard coverage `1.0`，failure 为空。模型前两次 capability call 漏掉 `request_id`，DSH 工具层返回参数错误，随后自动重试成功；这增加了两条失败轨迹和一次额外调用，但没有污染最终 Artifact。
- 029-B `run_f957cbf7618c4463b74a173e84cc8484` 完成，Artifact `art_da88042b9fb5421597bfda19b5c7527d`；11 条 Evidence、6 次 capability calls、1 个 Review，hard coverage `1.0`，failure 为空。它对“无法区分新增空头/平多/现货卖压”的缺口做了保守降级，30m/24h 为 neutral、72h 为 no_trade。
- 初版修复尝试在 DSH 适配器中生成 `dsh-tool:<session>:<callId>`。030 `run_b117d6720016403495e05c4167e0ccbc` 证明该前缀破坏了结果与原始 Tool call 的逐字 attestation，虽然 11 条 Evidence 已进入临时账本，终态被安全地拒绝为 `dsh_evidence_unattested/synthesis_attestation`，没有生成 Artifact。该批是适配器回归，不计入产品质量。
- 修正为直接使用 DSH `exec.callId` 作为缺省 `request_id` 后，031 `run_9c12ede6b5a44b23a29544a5b97c7b43` 完成，Artifact `art_9cee1bb0d424425db2b056469c0c03a1`；14 条 Evidence、7 次 capability calls、1 个 Review，hard coverage `1.0`，failure 为空。复读其 Host 轨迹确认 7 个研究 Tool call 均未提供模型侧 `request_id`，但 `failed_tool_results=0`，所有结果均按原始 `callId` 通过 attestation。
- 代码改动集中在 `extensions/dsh/decision-hub/src/research-tool.ts`：公开参数允许模型省略 request_id，可信 wrapper 负责生成；Hub 侧 canonical query 仍严格要求非空 request_id。`tests/research-tool.spec.ts` 新增缺省 ID 回归，插件全套为 `85 passed`，生产 build hash 已更新并通过 DSH readiness。

这组三条 prospective 任务证明：主链在当前免费 OKX 小时数据、订单簿代理和单一模型配置下能够重复产出可回读 Artifact，且 request_id 漏填不再制造无谓失败；它没有证明跨交易所、OI 变化、宏观事件窗口或预测质量已经改善。29-A/29-B/31 的 soft coverage 均约 `0.142857`，G1 仍保持 `retain_baseline`，继续停止 M04/M05 和外围平台工作。

### 2026-09-13：核心链路 032–034 与真实问题修复

本轮继续只走真实 DSH 页面同源入口，验证输入到用户可回读报告的完整链路；不增加外围评测或 M04/M05。

- 032 在旧 Research Worker 进程上完成了 Artifact，但当时价值评估代码尚未重载，引用指标仍显示 0，不能用来验证指标修复。
- 033 暴露历史供给的真实时序问题：研究开始在 13:01 UTC，而常驻 historical worker 从任意分钟启动后按固定 3600 秒休眠，最新归档只到 12:00，`data.historical` 返回 `historical_dataset_coverage_missing`；系统按安全策略拒绝未经证明的 synthesis，没有伪造方向输出。手动补齐 13:00 小时后，034 重新完成。
- 034 `run_986b13c244454838a27f04d9810955f2` → Artifact `art_cbc3f6c8fe4b483ba26b34d4d19a533c`：Run `completed/done`，failure 为空，14 条 Evidence（11 accepted、3 stale）、8 次 capability calls、1 份独立 Review、hard coverage `1.0`、soft coverage `0.142857`、terminal reason `sufficient`。Host 结果含 36 条事件，`failed_tool_results=0`；报告可回读 30m/24h/72h 结论、因果链、反论点、触发/失效条件和明确缺口。
- 修复价值评估的引用匹配：Artifact 现在按 Evidence ID（同时支持 content/source ref）计算可追溯性，不再只匹配 URL/source ID。034 的真实评估从历史批次的假性 0 提升为 `citation_traceability=0.9091`；仍有 1 条 accepted Evidence 未被引用，保留为待观察信号，没有把它误报为满分。
- 修复历史 worker 的小时边界：`--interval 3600` 现在每次 tick 后等待下一个 UTC 整点并额外等待 5 秒，避免进程重启偏移导致下一整点缺失；非 3600 秒间隔仍保持原有本地验证节奏。新增边界测试并重启实际 historical worker。
- 029–031 暴露并修复 DSH Research Tool 漏填 `request_id` 的问题；030 的错误前缀曾触发 `dsh_evidence_unattested` 并被安全拒绝，031 用原始 callId 兜底后 7/7 研究调用无工具错误并成功生成 Artifact。该修复与本轮 034 的主链结果共同验证。

本轮自测：研究价值、研究 API、DSH runtime、历史 worker 定向回归 `78 passed、1 skipped`；新增历史边界测试所在文件单独 `4 passed`；生产代码 Pyright `0 errors`，Ruff 和 `git diff --check` 通过。当前 live Hub readiness 为 `ready`，历史 worker 已按整点对齐方式运行。

当前结论仍是 `G1=retain_baseline`：核心链路已经能稳定从输入到 Artifact/报告，但新任务 soft coverage 约 `0.142857`，citation 仍非满分，usefulness 尚未标注；跨交易所、OI 变化序列、实时 rates/USD 和可审计事件窗口仍是实际信息缺口。下一步只在有新鲜且合规的数据供给时继续验证质量/返工改善，外围工作保持停止。

### 2026-09-14：核心价值交付收口

本轮按 `CORE_VALUE_DELIVERY_EXECUTION_2026-09-14.md` 只处理手动 BTC/USDT 研究闭环。FactPack 恢复 OI delta 和两个独立 venue 的 hard 约束；新增 Binance 公开现货/永续市场 adapter，与 OKX 一起完成现货、funding、OI 及 5m 变化、mark/index/basis 的双 venue 取证。Hub 新增确定性行动摘要和 8 步过程投影，commit Gate 对未校准方向和非 accepted Review 保持 `research_only/trade_authorized=false`。DSH 和 Decision Desk 现在先显示不交易结论、3 周期条件、证据计数、缺口和分析过程，原始错误保留在默认折叠的技术详情。

三次同一固定输入的真实 Run 已完成并保留：`run_54f16549c7d7426eaa52abeaf00b1b85` 因单一 venue 为 hard 0%；`run_256fd84bb1f347d0aff8f00098646272` 取得 hard 100% 但 synthesis 因重复/缺失 Evidence ref 被拒绝；有界表达归一化后，`run_c892c199d6e448ab8db6c6b455e97b41` 在 176 秒内生成 15 accepted / 6 stale、hard 100% / soft 33%、3 个未校准 no_trade horizon 和 1 份 bounded Review。后台未出现未捕获异常或 5xx。

自测为核心 Python `70 passed, 2 skipped`、DSH plugin `87 passed + build`、Decision Desk `12 passed + build`、Ruff/Pyright/codegen/diff check 通过。全仓为 `655 passed, 3 skipped, 4 failed`，4 个失败属于冻结的 Windows 通知文件编码和 Evolution 制品哈希链路，已在实施书明示。最终决定为 `LIMITED（仅研究）/ NO-GO（交易与扩展）`；不启动 PPT、新角色、调度、Evolution 或交易接入。

### 2026-09-15：方向价值 outcome 审计

复读 `g1-c3-dev/holdout` 的 12 个冻结案例和两套真实 comparison，不调用模型；从 Binance Vision 下载并按官方 checksum 验证 48 个 BTCUSDT 1 分钟日档，生成 cutoff 后 30m/24h/72h 共 36 个 outcome。DSH 在 36 个判断中仅有 1 个 long，方向覆盖率 2.78%，其余为 neutral/no_trade；唯一 long 实现 +1.3797%，但样本量为 1 且 36/36 概率均未校准，无法计算有效 Brier 或证明优于 always-long/事件后动量。完整结果和复现命令见 `CORE_DECISION_VALUE_VALIDATION_2026-09-15.md`，裁决为 `STOP_PRODUCT_LAYER_INVESTMENT`。

### 2026-09-13：G1 结论与产品范围收缩决策

在不再重复调用模型的前提下，按方案的 G1 门完成最后一次证据审计。新鲜 dev/holdout 各 6 案例均已从真实 DSH Session 完成取证：dev `hard_coverage_mean=0.8055555555555555`、holdout `0.6944444444444445`，两边 `quality_success_count=0`、`manual_rework_count=6`，PIT 违规和未验真 Evidence 均为 0。候选没有带来质量或返工改善，G1 不通过。

可取得数据路径也已穷尽到当前授权边界：免费样本与 Yahoo/Twelve Data 不能覆盖冻结日期和 Pack 市场身份；Portara/CQG/ICE 的完整 USY02Y、DXA 分钟档案需要授权交付；Kalshi 对目标 `t-5m` 的最近成交实测滞后 300 秒，超过 90 秒门；USMPD 只有事件窗口汇总，不提供可独立回读的原始分钟 bar。模型路径已用原生 Responses canary 和真实 DSH 任务验证，当前唯一 owner 配置的 `codexai/gpt-5.5` 可完成主链，模型替换不能补齐缺失的 hard Evidence。

据此形成有运行证据的范围收缩，而不是伪造 G1 promotion：

- 保留：DSH 手动当前 BTC/USDT 市场研究，使用 OKX 现货、衍生品、订单簿拥挤度和滚动完整小时历史；报告可发布 Evidence-linked Artifact，但方向输出默认受限为 neutral/no_trade，并明确单一 venue 和缺口。
- 暂不发布/不自动化：冻结历史宏观事件的方向性研究，直到存在带 `portara-source-attestation.v1` 的 USY02Y/DXA 原始分钟文件，或从未来事件开始积累同等 PIT、身份、时效和独立来源保证的数据。
- 约束：不生成不完整的 core-v4，不放宽 `macro_transmission` hard gate，不启动 M04/M05 或自动交易/调度。

正式决策和输入证据 hash 已保存为 `data/decision-hub/evaluations/core-v3-product-scope-decision-20260913.json`，文件 SHA-256：`f153e663ee2f5d6de206e967fde42e7032b8bcc232885daac0aacc194d14496d`。该记录同时保存 dev/holdout 质量结果、034 当前快照主链证明、来源探针和重启条件；不包含任何密钥。

## 2026-09-14：真实产品逐功能走查与当前快照合同收敛

本轮先从空白 DSH 会话完成一次真实输入到输出，再逐一点击 DSH 与 Decision Desk 当前暴露的入口。走查确认主链可用，但也发现旧 Decision Desk 构建、占位导航和旧 Operations 状态会让用户误判产品不可用。

- 首次真实走查 Run run_deea031006eb481cafa49155995c2deb 完成，Artifact 为 art_11e70ad5bb7f493496fcd891aabd1eb6：14 条 Evidence、11 accepted、3 stale、1 Review、hard coverage 1.0、3 个 horizon。
- Decision Desk 最初显示“研究详情不可用”。API 返回正常，根因是 gitignored 的 apps/decision-desk/dist 仍是旧 contract bundle。重建后立即恢复；Windows 一键启动器现在固定先执行前端 build。
- 实际点击证明 Decision、Forecasts、Timeline、Evidence 都打开同一个空 Research workbench，Settings 与 Runbook 完全无响应。当前导航已经收敛为 Research 与 Operations，顶部无效搜索/通知也已移除；未来能力在真正实现前不再冒充可用功能。
- Operations 原先用旧 observation runtime 的 fake 和未启动的 realtime/evolution worker 表示整个 DSH 产品。现在只统计当前核心账本服务，并明确旧 runtime/source 状态不代表 DSH live 会话。
- 当前市场快照不再把 event_identity、policy_or_data_delta、expectation_pricing 和 counter_thesis 当作检索要求。前三项只属于外部事件研究，counter thesis 属于 synthesis/Review 义务。
- 最终真实回归 Run run_62ae59168aef4378a4188df91e7e86c7 完成，Artifact 为 art_1cf11f728aab4879a44a916f7080abe1：hard coverage 1.0、soft coverage 0.3333、14 条 Evidence（11 accepted、3 stale）、1 个 accepted Review、3 个未校准 neutral horizon。无关 gap 从 6 个降为 2 个，只保留 stale macro_transmission 和缺失 cross_asset_confirmation。
- 同一次运行暴露两次 research_macro_series_denied：FRED 三序列已经成功返回 stale 背景后，模型仍尝试额外未授权宏观序列。运行保持完成且拒绝结果没有进入 Evidence；profile 已增加“一次固定三序列、成功但 stale 后不要扩展重试”的明确约束，下一次新 Run 验证工具噪声是否下降。
- 一键启动脚本 infra/dsh/run-product.ps1 已真实启动并通过 API readiness、DSH version compatibility、Hub reachability、核心服务 2/2 和同一 Run 回读；stop-product.ps1 只停止启动器记录的进程并保留数据。

截图和用户操作路径记录在 docs/user-guides/DECISION_HUB_SCREENSHOT_WALKTHROUGH_2026-09-14.md。本轮没有启动 Evolution、自动交易、PPT 或通用评测扩建。
## 2026-09-15：网关核心链路可靠性目标与真实回归

方向价值审计完成后，停止继续堆产品外围。本轮只修复网关主链的四个 P0 风险，并把目标固定在 `docs/plan/GATEWAY_RELIABILITY_CORE_FLOW_2026-09-15.md`：

- `DurableResearchCapabilityGateway` 对已有 session 的终态、轮次和事件拒绝写入可查询的失败 trace；同一个 request 的不同拒绝按错误码分开，未知 session 不伪造不存在的 Run；
- Evidence 与 typed Fact 改为同一个数据库事务，Fact 校验或写入失败时不留下 Evidence 半提交；
- Provider router 接收 durable `deadline_at`，每个 fallback route 使用同一剩余预算，并拒绝 URL、端口、通配符等非法 domain 配置；
- `ResearchCapabilityQuery` 增加可选 durable deadline 字段，由网关覆盖为可信 Run deadline，模型输入不能延长执行上限。

回归结果：网关、Provider router、Fact Store 定向测试 `35 passed`；新增故障注入确认 Evidence/Fact 同时回滚。随后用隔离 SQLite 和真实 OKX 公共订单簿完成一次 Durable Gateway live smoke：Run `run_c845e7bea163481c9e338bd4f2bbab7b`，`tool_started -> tool_completed`，reservation `completed`，Provider `okx-orderbook-public`，1 条 accepted Evidence、2 条 accepted typed Facts、1 次成功 attempt、约 378ms、成本 0。该回归证明“真实 Provider → Gateway → Evidence/Fact → trace/reservation”可落地，不证明交易方向价值；价值结论仍保持 `STOP_PRODUCT_LAYER_INVESTMENT`。

同日的只读公共 Provider canary 为部分成功：FRED 与 OKX orderbook 可读，Federal Reserve feed/CoinEx 路径返回工具错误。该结果按失败保留，不能把“部分 Provider 可达”写成完整数据覆盖。Windows `run-product.ps1` 还暴露 `Start-Process -Environment` 不被当前 PowerShell 版本支持；这是启动器环境问题，未扩大本轮范围去做外围修复。

## 2026-09-17：R3-C0a 固定最新建议入口与搜索装配

本轮把范围锁定为一个纵向用例：用户点击“获取最新操作建议”，可以不输入任何专业字段，系统自动形成 BTC 当前快照研究，首屏先回答“现在采取什么动作、什么条件触发、什么条件使判断失效”。没有创建新的聚类表、概率训练、社交爬虫或外围评测任务。

已完成的代码：

- 新增 `research_intake.py`，把 Desk 固定入口或“现在怎么操作/给我最新操作建议”等短输入确定性展开为 `crypto_market_snapshot`。模板固定要求现货、衍生品、拥挤度、小时历史、最新公开信息、30m/24h/72h、主机制、反方、触发、失效和数据限制；用户文本只作为补充关注点；显式的非快照 `event_hint` 保持权威。
- `POST /v1/research/observations` 在排队前执行该规范化。前端允许补充框为空，提交稳定的 `decision-desk-latest-advice` 来源标识，不再要求用户自己写一份完整研究提示词。
- Research 首屏改为“最新操作建议”，优先投影 30m 情景：long/short 仍表达为“观望，满足条件后再评估做多/做空”，neutral/no_trade 显示等待或不交易，并紧邻展示触发和失效条件。soft gap 也作为“信息限制”直接显示；例如 search 未通过时明确显示“最新公开信息未核验”。原 Evidence 审计、三周期、因果链和技术轨迹仍可回读。
- `web.search` 改为 composition root 的单一 provider 选择，支持 `openai_responses`、`dsh_native`、`tavily`，未知 provider 直接拒绝。Windows 启动器安全读取 gitignored `.env` 的搜索配置；Compose 透传搜索 provider/base URL/key/model，但不打印密钥。
- launcher 只在 `DECISION_HUB_SEARCH_CANARY_STATUS=passed` 后把 `web.search` 加入 audited capability allowlist。当前本地状态为 `not_run`，因此不会把未验证的搜索配置冒充成已可用；typed 市场主链仍可独立运行。
- 当前 BTC 快照固定带 `market_narrative` soft requirement，而不是依赖模型临场决定要不要检索。canary 未通过时该 gap 仍保留在 Coverage 中，但没有可执行 route，也不会拿未知 URL 调 `web.fetch`；canary 通过后固定先走一次 `web.search`，限定 Fed/BLS/BEA 官方域。
- 搜索摘要保持 `search_derived/candidate`，只能作为 locator，不能覆盖 requirement 或进入结论引用；随后必须对 Registry 批准的官方 URL 执行 `web.fetch`，完成 publisher identity、原文 excerpt、hash、PIT 和 authority 校验后才成为 `accepted` Evidence。该 requirement 仅属于 `crypto_market_snapshot`，不会污染其他事件合同；fallback 轮不会重复搜索。
- Windows 启动器发现并修复了一个真实启动问题：`uv` 原先继承工作区外不可写的共享缓存，Hub API 会在启动初期退出；现在所有子进程使用 `data/product-local/uv-cache`。

自测证据：

- 后端固定入口、事件分类、搜索路由、provider 组合和 launcher：`25 passed, 3 skipped`（当前 Windows 跳过 POSIX launcher 用例）；新增 API 集成测试确认简单输入入库后是 `crypto_market_snapshot`，且固定模板包含现货和三周期要求。
- Decision Desk：`13 passed`；生产构建成功；其中一项直接检查空白表单提交的固定 source/event contract。
- 最新核心定向回归（fixed intake、FactPack、Registry、search provider、search→fetch→Evidence、DSH prompt、launcher）：`146 passed, 4 skipped`；其中新增的离线纵向测试明确证明搜索 locator 的 soft coverage 为 0，抓取官方原文后才变为 `accepted` 并覆盖 `market_narrative`。
- Ruff：通过；本轮生产代码和新增测试的 Pyright：`0 errors, 0 warnings`。把整个旧 `test_dsh_research_runtime.py` 一并纳入 Pyright 时仍有 18 个既存测试类型错误（私有符号引用与 optional review 断言），运行测试本身通过；本轮未把这些旧静态问题伪装成已修复。
- 扩大回归覆盖旧同步 API、Research API 和 Search adapter/canary 共 `53 passed, 1 failed`。唯一失败是旧 `baseline.v1` 用例仍断言 `completed/publish`，实际产品 Gate 返回带 Artifact 的 `degraded/research_only`；它不经过本轮 `/v1/research/observations` 固定入口，也没有被修改为假通过，后续应由旧基线所有者决定更新合同或恢复 publish 语义。
- Responses search canary 在沙箱内得到 Windows `10013` 网络权限错误；申请沙箱外执行被自动审批器以“审批模型容量不足”拒绝。该结果不是 provider 失败，也没有把 canary 标为 passed。
- 一键启动在修复 uv 缓存后可启动 Hub API 与 Research MCP，但 DSH Web 在沙箱内读取 `data/dsh-live/.credentials.yaml` 被明确 `EPERM`；沙箱外启动申请同样被自动审批器以容量原因拒绝。由于没有取得真实 DSH Run，本轮不得标记 C0a 完成。

剩余唯一退出步骤：在允许读取本地 DSH credential 和访问 provider 的进程中重新执行 `infra/dsh/run-product.ps1`，运行一次 Responses search canary，再从空白补充框触发一条 live Run，记录 run_id、Tool receipts、Artifact、首屏回读和后台错误。2026-09-17 本轮再次申请沙箱外 canary，自动审批器仍以“Selected model is at capacity”拒绝，未产生 provider 结果，也没有改写 `not_run`。通过前不进入 SourceLineage、聚类或概率训练。

### C0a 完成审计（2026-09-17）

| 目标要求 | 权威证据 | 结论 |
|---|---|---|
| 简单/空白输入固定补全 | API/normalizer/client 测试；入库事件为 `crypto_market_snapshot` | 已证明 |
| 单一 Responses search composition | composition、provider fail-closed、launcher gate 测试 | 代码与离线测试已证明 |
| search → official fetch → Evidence | `test_narrative_search_chain.py` 证明 locator coverage=0，官方原文 fetch 后 accepted | 离线链已证明；真实 provider 尚未证明 |
| 首屏只突出动作、触发、失效、关键依据和限制 | Decision Desk 组件测试与 production build；soft gap 显示“最新公开信息未核验” | 已证明 |
| 针对性自动测试 | 核心 `146 passed, 4 skipped`；前端 `13 passed`；Ruff/Pyright 定向通过 | 已证明 |
| 真实 Responses canary | `.env` 仍为 `DECISION_HUB_SEARCH_CANARY_STATUS=not_run` | 未完成 |
| 本次代码的真实主流程与 UI 回读 | 8047/8037/3097 均未监听；`data/product-local/processes.json` 不存在；没有新 run_id | 未完成 |

因此 C0a 不能标记完成。相同外部阻塞已连续三次复核：沙箱禁止 provider 网络和 DSH credential；沙箱外命令的自动审批又因审批模型容量不足被拒绝。继续改本地业务代码无法产生所缺的真实运行证据，且会偏离当前目标；等待外部执行授权恢复后，从 canary 原命令继续，不重做已通过的实现与测试。

## 2026-09-18：真实主链恢复与“误报失败”修复

本轮只处理真实主链的两个 P0 问题，没有扩展数据源、评测集或外围页面：

- 通过 DSH 恢复目录的凭据记录验证了 `codexai/gpt-5.5` 的真实 Responses 调用。此前 `settings.yaml` 声明 `apiKeyEnv` 后，DSH 凭据服务只查 `.credentials.yaml`，不会回退到子进程环境，导致首轮在模型调用前返回 `dsh_turn_error/TRANSPORT`。`infra/dsh/run-product.ps1` 现在从选定的 gitignored `.env` 安全同步 `llm-pi-ai/codexai` 的本地 api-key 记录；密钥不写入代码、日志或版本库。
- 修复 `should_continue_after_round` 的终止优先级。之前某个已失败但可重试的 Provider 会覆盖 `no_actionable_path`，把耗尽全部声明路线的 Run 推进到下一轮；此时工具预算已为零，图抛出 `research_tool_budget_exhausted_before_round`，Worker 再把已经形成的受限 no-trade 结果记成 `research_worker_failed`。现在 deterministic loop 关闭路线后直接进入 finalize，保留 DSH 已提交的 synthesis、缺口和失败轨迹。
- 新增回归测试，验证 `retryable_failures > 0` 不能重新打开 `loop_should_continue=false` 的闭环；图定向回归 `18 passed`，Research API/Worker 回归 `31 passed`，PowerShell 语法解析通过。

真实空白/短输入主链回归：`run_2a832a156ec842d9b5bae320c9fe4cc6` → Artifact `art_83625047c3d34efdb7f7dc31feede9f7`。DSH 已完成两轮真实模型步骤、独立复核和最终合成；Run 终态为 `research_only/done`，不是后台异常。首屏摘要为 `do_not_trade`、`trade_authorized=false`、`artifact_status=limited`、`data_status=insufficient`，并显示 30m/24h/72h 情景、缺口和“信息不足不应交易”的边界。该 Run 新增 `0` 条 accepted Evidence：OKX/Binance/FRED 的当前环境请求仍受 TLS/ConnectError 影响，历史归档缺失，搜索路由超时；系统保留这些错误和尝试记录，没有把模型措辞伪装成市场证据。

这次回归把“主链是否能交付”与“当前数据是否足以交易”分开验证：前者已从 `research_worker_failed` 修复为可回读的受限研究结果；后者仍未通过 C0a 的真实 Evidence 门槛。因此 C0a 仍不能标记完成，下一步只剩补齐可达且合规的至少两组实时市场 Evidence 后重跑同一入口；在此之前不放宽 hard gate、不输出方向概率，也不进入外围功能。

另外修正了 Operations 的运行态误导：本地 live 启动器会把真实 DSH research runtime 显示为 `dsh-web/provider/gpt-5.5/responses`，Hub API 与 Research Worker 两个心跳也均为 `provider/online`；不会再把未承载 DSH 研究的 native fallback 显示为 `fake`。该展示修复没有改变研究 Gate 或交易权限。

随后执行一次受控 Responses Search canary（`tools.canary.run_responses_web_search_canary`，未输出密钥）：结果为 `failed / research_capability_timeout`，约 20 秒未返回 `action.sources` 或 `url_citation`，没有生成 Evidence。已将恢复目录 `.env` 的 `DECISION_HUB_SEARCH_CANARY_STATUS` 记录为 `failed`，并关闭 `DECISION_HUB_SEARCH_ALLOW_LOCATOR_ONLY` 旁路；重启后的 live launcher 不再把未经验证的 `web.search` 加入能力白名单。该失败是可观测的搜索供给阻塞，不是主链异常，也不能被当作“搜索可用”。

## 2026-09-18：搜索预算验证、进度保存与价值复审

恢复点与核心改造顺序统一记录在 [CORE_PROGRESS_AND_VALUE_REVIEW_2026-09-18.md](CORE_PROGRESS_AND_VALUE_REVIEW_2026-09-18.md)，含架构图、代码映射、历史生死线、未完成 checklist。本轮遵循用户“先保存进度、复审价值和分层”的要求，没有启动新的大规模功能或付费研究 Run，C0a 保持未完成。

补记此前已落盘而尚未入日志的验证：

- 有界独立 Responses 探针约 36.5 秒返回，因此原 20 秒网关预算会提前截断，不能据此判定 provider 不支持搜索。`openai_responses.py` 显式要求工具调用；仅 `web.search` 改 60 秒；DSH Research Tool wrapper 改 65 秒；其他 HTTP 工具预算未整体放宽。
- `data/product-local/search-canary-60s.json` 为真实通过记录，返回 5 个 Fed URL，但兼容网关没有原生 citation spans。启用的是 locator-only 发现能力，搜索摘要仍是 candidate，不能直接支持事实。launcher 不再允许 locator 开关绕过 canary。
- `data/product-local/search-fetch-proof/receipt.json` 保存从搜索结果选择官方原文，经现有 Gateway/Registry/正文归档后形成 1 条 accepted/official Evidence 的隔离证明；run 为 `run_7f819ba342354022950a479de7fcfa0a`。`published_at=null`，只证实本次抓取和归档，不宣称发布时间校验完整，也不宣称 BTC 市场 hard Evidence 已充分。
- 前一批定向 Python 测试记录为 `63 passed`（search/canary、narrative chain、market snapshot、graph）；本次重新核对插件测试，用仓库 pnpm 脚本运行 `tests/research-tool.spec.ts` 为 `4 passed`。直接调用 bun 因 PATH 无命令失败，改用项目脚本后通过，不能把第一次失败当测试执行成功。
- 本次审查额外运行 `tests/kernel/test_core_flow.py` 和 `tests/research/test_research_observability.py`：`17 passed`；证明当前 Gate/投影行为，不证明金融预测质量。涉及搜索预算文件的 `git diff --check` 通过。

纠正上一节的过度结论：

1. `run_2a832...` 经数据库只读核对，事件 `evt_30c1c1132d324c8e962e3f310ac7f092` 的类型是 `macro_event`，API 有 9 项 requirement；不是 C0a 的固定 BTC 快照。其 Artifact 可读、failure 为空、accepted=0 仍成立，但不能作为固定入口验收。中文编码是待核实假设，未认定根因。
2. 循环修复证明 retryable failure 不应重开 deterministic loop 已关闭的路线；不能仅凭该回归断言此前所有 Worker 异常都是同一种预算异常。
3. 最新 13:59 +08:00 服务 readiness 为 ready，三个端口监听，外部授权不再是当前阻塞。市场数据与完整 UI 验收仍未完成，不能继续重复要求用户授权来解释停滞。
4. 独立架构审查指出 `commit.py` 会降级 Forecast，但 `query_views` 仍保留原始 horizons；真实问题是研究观点、信号资格、交易授权的语义混合，不能笼统说所有方向都被删除。下一改造方案明确分离这些语义，保留真实数据约束。

多 Agent 情况如实保留：architecture_core_audit 有完成意见；decision_usability_review 和 history_value_audit 再次返回平台容量不足。本轮方案不宣称三路独立审查全部通过。产品价值结论仍为“研究价值待验证，方向优势未证明”，没有将功能修复等同于可交付交易产品。

## 2026-09-18：C0a 固定入口真实主流程与首屏回读

在重启并构建当前 Decision Desk/DSH bundle 后，用 UTF-8 JSON 通过 `/v1/research/observations` 发送固定入口文本 `获取最新操作建议`，携带 `source_id=decision-desk-latest-advice` 与 `event_hint=crypto_market_snapshot`。Python 探针第一次返回 502，是探针继承本机代理导致的本地代理错误；改为 `trust_env=False` 直连后成功排队。产品 API 本身没有因此重启或修改业务结论。

真实主流程记录：

- `event_id=evt_08b84b4a4883471b81cf1e50f78b0b00`，数据库 `event_type=crypto_market_snapshot`；观察文本为完整固定 BTC 模板，中文 UTF-8 未被替换。
- `run_id=run_87a7390aa675402fae6b57ad59fb1abe`，`artifact_id=art_ada26a6ecaa34920bdad27a467cac5be`；`status=research_only`、`stage=done`、无 run-level failure。
- 真实 DSH 完成 2 轮，108 条 trace，25 次工具调用；其中 `web.search` 返回 5 个 Federal Reserve URL locator，随后 `web.fetch` 取得 1 条 `official/accepted` Evidence `ev_c12a172325ab6b982f467544abcd55c1`。5 条 locator 保持 `search_derived/candidate`，没有被误当作事实。
- accepted Evidence 支持 `market_narrative`，hard coverage `0%`、soft coverage `25%`。`data.historical` 为 `historical_dataset_coverage_missing`，`market.crypto_crowding` 为 `provider_transport_unavailable/connecterror`；剩余阻断缺口为 `crypto_spot_confirmation`、`derivatives_crowding`。这些失败均保留在 Trace 和报告中。
- 最终停止原因 `critical_data_unavailable`，因关键市场数据不可用而有界停止。行动摘要为“证据或复核未通过，不应据此交易”，`trade_authorized=false`；这不是交易预测通过，也不是把宏观背景扩写为 BTC 方向。
- Artifact 的模型 horizon 输出因不满足三窗口约束而为空。为满足 C0a 的首屏行动边界，Decision Desk 增加无 horizon 的安全回退展示：仍显示当前不交易、重新评估触发（补齐双交易所现货与衍生品/拥挤度且多来源一致）、失效条件（关键来源过期/冲突/不可用）和可点击的 accepted Evidence。它不生成伪造的 30m/24h/72h 预测。
- 浏览器实际回读 `http://127.0.0.1:8047/`：页面显示 `Research worker · online`、当前任务“仅研究”；首屏可见“证据或复核未通过，不应据此交易”“未授权交易”“重新评估条件”“关键依据”、1 条可用 Evidence、2 个阻断缺口和实时宏观/跨资产/小时历史限制；向下可见“分析过程”8 步，其中 25 次工具调用和每项失败原因均可读。截图已通过浏览器自动化观察。

本轮代码改动：`ResearchPage.tsx` 在没有合法 horizon 时不再只留一句 Gate 提示，而是显示安全的触发/失效边界和 accepted Evidence 引用；`ResearchPage.test.tsx` 新增无 horizon 回读回归。生产构建成功。前端 `pnpm test -- ResearchPage.test.tsx` 为 `14 passed`，Decision Desk `pnpm build` 成功；后端 search/canary/narrative/snapshot/graph 定向回归 `63 passed`。一次扩大 pytest 因 Windows pytest 临时目录被已有进程占用产生清理权限错误，随后同一测试集合不指定 basetemp 重跑为 `63 passed`；权限清理异常不计入产品测试失败。

## C0a 退出审计

| C0a 要求 | 当前证据 | 判定 |
|---|---|---|
| 简单/空白输入固定为 BTC 快照 | `evt_08b...` 的数据库 event_type 与完整 UTF-8 模板 | 已验证 |
| 单一 Responses search provider | search canary `search-canary-60s.json`，固定入口真实 trace | 已验证；兼容网关提供 locator，不提供原生 citation spans |
| search → official fetch → accepted Evidence | `ev_c12...`、`run_87a...`、`receipt.json` | 已验证 |
| 首屏动作、触发、失效、关键依据、限制 | 浏览器真实回读与新增无 horizon UI 回归 | 已验证 |
| 针对性测试与构建 | 后端 63 passed；前端 14 passed；生产 build | 已验证 |
| 一次真实 canary/主流程自测 | canary passed；`run_87a...` 完整主流程、Artifact、UI | 已验证 |
| 交易方向价值/数据充分 | hard coverage 0%，双交易所与衍生品证据不可用 | **未通过且不属于 C0a 的伪成功** |

C0a 的工程闭环已具备真实主流程、Evidence 链和 UI 回读；产品仍处于“仅研究”状态，不能宣称能交易或方向预测有效。后续若继续，只能进入原方案的 C0b/C1：先恢复实时市场数据并验证研究价值，不扩展外围功能。当前搜索兼容模式和市场缺口已明确记录，没有伪造概率或放宽交易 Gate。

### 2026-09-18：用户调整周期后的策略根因复核

- 用户明确取消小时数据前提，允许 3 天/7 天/30 天方向，要求真实信息权重、明确策略和杠杆清算风险。后续顺序以 `FINANCIAL_INTELLIGENCE_PROBABILITY_UPGRADE_2026-09-17.md` 第 34 节替代上段固定 C0b/C1 顺序，不继续外围扩展。
- 代码确认：小时历史是 soft requirement；graph/schema/commit 固定三个短周期；研究发布排除 long/short、非 sufficient 方向降级、UI 合并多种不交易原因。故仅补齐数据不能保证形成用户所需结论；此前仅归因缺数据的解释不完整。
- 当前 crowding adapter 是订单簿代理，不是清算/杠杆观测。最近真实报告还存在把 Fed Evidence 引用为 OKX 工具失败依据的问题，纳入 claim 与 receipt 相关性修复。
- 已检索交易所公开数据、作者动量研究、BIS carry 风险、清算图模型说明及概率校准方法，来源和适用限制列在主方案 §34.10。
- 实际探测共 9 条公开 URL：Alternative.me 返回 200；其余 8 条行情/归档 URL ConnectError。回执在 `data/product-local/daily-strategy-source-probe-20260918/`；这是本机访问结果，未证明底层原因，未作为产品行情 Evidence。
- 已保存 7 天日频策略的合同、数据最小集、规则/统计模型、权重来历、动作与权限分离、代码映射、两批核心 checklist 和自审。未实施这些代码修改、未训练模型、未生成新的 BTC 方向、未声称盈利或交付完成。

### 2026-09-18：D0-7D-DIRECTIONAL-CORE 开始执行

- 目标已创建：`D0-7D-DIRECTIONAL-CORE`。范围锁定为 7 天日频观点纵向链路；不把概率训练、衍生品增强或外围 UI 当作本目标出口。
- 已完成第一批实现：默认 BTC 输入模板改为已收盘日线 + 7 天主周期；新增 `packages/kernel/decision_hub_kernel/decision/daily_strategy.py`，实现 `daily-trend-v0` 纯函数规则，保存输入 hash、60 根最小历史、SMA20/SMA60、20 日收益、波动、成交量比、区间高低点、方向、行动候选和失效条件。
- Contract 已新增 `research-outlook.v1` 和 `strategy-receipt.v1`，并作为 `ResearchActionSummary.research_outlook` 可选投影接入现有报告；交易授权字段仍固定为 false。Query View 只接受 Fact attributes 中的 JSON receipt，不从缺失数据推造观点。
- 前端已增加 7 天主要方向展示分支；当前 live Run 尚未产生 daily receipt，因此旧报告不会被伪装成新观点。
- 定向验证：`uv run pytest -q tests/kernel/test_daily_strategy.py tests/kernel/test_core_flow.py tests/research/test_market_snapshot_admission.py --disable-warnings` 当前 17 passed；`pnpm test -- ResearchPage.test.tsx` 当前 14 passed；`pnpm build` 成功；`uv run python -m tools.contract_codegen check` canonical schemas ok。
- 尚未完成：日线 adapter/Pack 真实数据产出、receipt 写入 live Fact、研究循环接入、真实 D0 主链和四类 replay 退出审计。当前目标不能标记完成。


### 2026-09-18：D0-7D-DIRECTIONAL-CORE 完成审计

本轮完成了目标定义的核心纵向链路，没有进入概率训练、外围评测或自动交易：

- `source_manifest.yaml` 的 `market_regime_context` 已切换为已收盘 BTC 日线指标合同；旧小时数据仍可由历史 adapter 读取，但不再作为最新 7 日建议的默认输入。
- `OKXHistoricalDataLoader` 增加 `crypto.btc_usdt.ohlcv_1d` / `bar=1D`；`HistoricalDataResearchAdapter` 过滤未闭合 candle，要求至少 60 根，并将 `daily_strategy_receipt_json` 写入 Fact attributes。
- 历史 worker 增加独立 `tick_daily()`，生产 `historical` role 会按 UTC 日边界创建并幂等刷新 1D DataJob；原有 1h tick 和报告字段保持兼容。
- 策略回执新增触发条件和最强反方；Query View 投影到 `research_outlook`，前端首屏展示方向、行动、触发、反方、失效、证据和未校准状态。有 7 日观点时隐藏旧的 30m/24h/72h 卡片。
- 四类冻结 replay 已通过：上涨 `bullish`、下跌 `bearish`、区间 `range`、历史不足明确失败且不生成方向。详细结果见 `docs/plan/D0_7D_DIRECTIONAL_CORE_SELFTEST_2026-09-18.md`。
- 实时 OKX 1D 探针在当前环境以 `httpx.ConnectError` 失败；没有把 replay 冒充实时，也没有使用空值或模型文本补齐。恢复网络后沿现有 DataJob/Gateway 路径重跑。

验证结果：后端核心定向 `104 passed, 1 skipped`；契约生成 `canonical schemas: ok`；Ruff `All checks passed`；前端 `14 passed`；Vite production build 成功（仅既有 chunk size warning）。D0 的工程验收通过；实时数据可达性和策略价值/概率校准仍是下一目标前提。


### 2026-09-18：C1-CORE-DELIVERY 启动与真实归档 fallback

- 新目标已建立：`C1-CORE-DELIVERY`。验收要求从“工程链路可运行”提升为“真实数据、真实 Run、五案例价值对照和 UI 可回读”；自动下单和伪造概率仍禁止。
- 新增目标文档：`docs/plan/C1_CORE_DELIVERY_PLAN_2026-09-18.md`。
- C1-A 探针发现当前环境直接访问 OKX 1D 接口仍返回 `httpx.ConnectError`，但已有 `data/core-live-20260913/historical` 归档包含约 90 天真实 OKX 1h 数据。
- `HistoricalDataResearchAdapter` 现在可在 1D 分区缺失时，把已归档的闭合 1h candle 按 UTC 日聚合成日线；不填充缺失小时，并在 Fact attributes 标记 `derived_from_hourly=true`。日线窗口从 220 天收敛为历史 worker 的 90 天，满足 V0 的 60 根最小要求。
- 归档探针已实际产生可复算结果：`data/product-local/c1-existing-archive-20260918.json`。请求窗口为 2026-06-20 至 2026-09-18，得到 89 根日线，最新覆盖至 2026-09-16，V0 输出 `range`，20 日收益约 -5.66%，概率为 null。该结果是基于真实归档的 replay，不能当作当前 live 交易建议。
- 新增聚合缺口测试：缺少整天时只保留真实 59 天，不静默补齐。

当前 C1 仍未完成：还没有当前市场 spot/derivatives 的真实 Evidence，也没有新的真实 API Run/UI 回读。下一步只处理 C1-A 的当前市场数据可达性和历史归档接入主链，不扩展外围页面。
## 2026-09-19 C1 真实 Run 与 UI 自测

- 重启受管产品进程，重新构建 Decision Desk 和 DSH plugin；API、MCP、DSH Web、research worker、historical worker 均启动，worker heartbeat online。
- 触发 `run_e863345fcc254c28962f0baf7ac198d2`，固定入口为“获取最新操作建议”。新 profile 已生效：日线窗口 `90d`，窗口结束时间排除未闭合日线。
- 真实主链取得 `data.historical` 的 1 条 accepted Evidence、9 条 Fact；日线由真实归档小时线聚合，标记 `derived_from_hourly=true`，没有填充缺失天。
- 真实主链的实时交易所/搜索网关仍不可达，保留 `provider_transport_unavailable`、`research_market_unavailable`、`research_domain_denied`、`search_provider_failed`；最终 `research_only`，不是 replay 假装 live。
- UI 回读确认 7 日 `range`、`wait_for_range_break_or_trend_alignment`、`82285/74955.5` 触发、反方、失效、Evidence、数据限制和 `trade_authorized=false` 均可见。
- 自动检查：日线/历史/API 20 passed；策略 replay 4 passed；eval/runtime 7 passed；事件窗口/上下文 4 passed；前端 14 passed；build、Ruff、contract_codegen 均通过。
- 交付边界：核心研究链路可交付为有界研究建议；实时交易决策仍未交付。下一步只恢复一条当前市场公开数据路线，不扩展外围功能。

## 2026-09-19：公开现货 fallback 与 7d 合同误报修复

本轮只继续处理 C1 核心主链：

- 将 `api.alternative.me/fng/?limit=3` 登记为已批准的补充情绪来源，并在当前快照首轮优先尝试；真实 Run 接受 1 条带时间和内容 hash 的情绪 Evidence。
- 新增 `PublicSpotMarketResearchAdapter`，接入 Gate.io `api.gateio.ws` 与 Huobi `api.huobi.pro` 的公开 BTC/USDT 现货价格和成交量；两条路线都进入 source registry、bindings、MCP registry 和真实运行时路由，不能产出衍生品字段。
- 新增 `tests/research/test_spot_fallback.py`，覆盖 Gate 解析/Fact/Evidence 和 Huobi 解析；定向测试 `106 passed, 1 skipped`。
- 真实 Run `run_afd12c295ab841d7a6bf220b0479b00a` 接受 Gate、Huobi、Alternative.me、OKX 归档日线 4 条 Evidence，hard coverage 从之前的不足推进到 50%；唯一 hard 缺口是 `derivatives_crowding`，OKX/Binance 永续和 order-book 路线仍为 `provider_transport_unavailable`。
- 真实输出为 7d `range`，行动候选 `wait_for_range_break_or_trend_alignment`，触发 `82285/74955.5`，概率未校准，交易权限关闭。它是有边界的研究建议，不是交易信号。
- 修复 `packages/query_views/research/service.py` 的周期语义：已有可信 7d `research_outlook` 时，旧版 30m/24h/72h 缺失不再把“形成研究情景”标成 failed；UI/API 明确显示 7d 已形成、旧周期未发布。

当前 C1 仍未宣称交易可交付：需要衍生品/拥挤度至少一条可审计的公开路线，并需要样本外校准后才能显示概率；在此之前不放宽 Gate、不自动下单、不把当前 `range` 当成胜率。

## 2026-09-19：撤回 C1 交付结论，修正当前方向链

用户指出始终观望后重新核对代码，确认前次完成判定过早。完整范围见 CURRENT_DIRECTION_CORRECTION_2026-09-19.md。

- 修复小时聚合：不足24根、重复小时、非整点数据不再伪装成完整日线；旧v0归档报告显示明确更正提示。
- 接入 Gate 官方免费日线到既有 HistoricalRefreshWorker/DataJob/Archive；不是把探针或网页摘要冒充产品 Evidence。datajob_aecca9438e3a4201be63d1121cd3da0d 已完成90天日线。
- 修复收益率/波动率/量比单位，消除真实字段被标错单位导致 semantic_mismatch。
- v0.1 保持原三条件方向规则，增加1/2/7日变化，并把当前方向、现货持仓/空仓候选与后续复查分开。没有为了得出偏多去改投票阈值，没有伪造概率。
- 当前报告优先显示7d观点、数据截至、限制和现有复核状态；旧短周期不交易不再覆盖方向。宏观未纳入规则的边界明确展示。projection 要求 accepted Fact 与 accepted Evidence 绑定。
- 首轮复测 run_cab330d5b3734066ae721187426ad5d1 发现重复短输入复用旧观察时刻，日线窗口仍到09-18；主动取消并保留记录。
- 第二轮 run_5cd274d1e8504113b04309ec9168e9de 证明改用 context.created_at 仍不足：graph 同样用旧快照时间重建 context。该运行正常结束为 research_only，不能计修复成功。组合测试/取消命令曾被自动审批拒绝，未绕过取消限制，等待其完成。
- 最终 profile 固定按 live 请求 deadline 减总预算计算运行起点；历史replay仍使用原时点。新增“旧input + 旧context，隔天相同问题”的回归。
- 实盘探针和同源三天回算写入 data/product-local/core-direction-recheck-20260919/；09-18收盘规则已经偏多且低于前高，证明并非只能突破后才有观点。该回算不等于历史PIT回测或预测收益证明。
- 最新真实复测 run_fba7737392a2444f8ef0c77ad3ebeb0d 的持久化 DSH prompt 已核对为06-21至09-19日线窗口；已完成API和浏览器回读。新Gate原生日线截至09-18，90根，收盘80890.4，derived_from_hourly=false；7d价格规则偏多，已有现货持有倾向，当前条件已成立，非等待突破。
- 该Run两轮后仍是research_only/no_actionable_path，旧周期被全局抑制；Fed只取得日历，未取得决议正文。真实结果没有达到综合研究验收，不能把手工探针的宏观原文算为主链能力。详细未通过项及后续固定顺序已记录在整改文档。
- 回执保存为data/product-local/core-direction-recheck-20260919/live-run-result.json；Artifact art_5691ce7f1a1040cd925a643be2d13d83。页面保留复核限制、数据截至、宏观未纳入和方向未验证提示。

已通过后端定向121项/1 skipped、前端14项、Ruff、契约检查和生产构建。完整宏观+价格综合方向模型、原生7d合成合同和跨报告修订账本仍未完成；不能恢复“核心交付完成”的判断。

## 2026-09-19：P0-A+B vertical slice 完成与真实固定入口验收

本轮按“先修核心语义，再验证主链”的目标收口，没有新增 PPT、分钟级数据、全网爬虫或外围评测。完成内容如下：

- 原生合同 `contracts/schemas/agentic_research.schema.yaml` 增加 `research_market_view` 与 `research_execution_view`；前者表达 7 日方向、规则依据、反方、失效条件和未校准状态，后者表达执行权限、阻断原因、执行级缺口和 `trade_authorized`。
- `packages/query_views/research/service.py` 将有效日线方向投影到原生 `market_view`，并把衍生品拥挤、宏观传导、跨资产确认等缺口放到 `execution_view`/`missing_impact`，不再用执行缺口覆盖方向。
- `packs/crypto_macro/evidence/source_manifest.yaml` 将 `derivatives_crowding` 从方向硬门降为执行级软缺口；现货/日线足以形成 7 日方向基线，但不能因此授权杠杆或下单。
- `packages/orchestration/langgraph/graphs/agentic_research_graph.py` 增加“方向硬证据已足够时关闭方向视图”的策略：不再因执行级缺口或 bounded reviewer 保留意见继续空转到 `round_budget`；Forecast/Commit 仍独立 fail-closed。
- 前端 `apps/decision-desk/src/research/ResearchPage.tsx` 显示主方向、当前行动倾向、分析过程、执行权限和具体阻断原因；用户能区分“方向已形成”和“交易未授权”。

### 真实验收结果

- Run：`run_c0f9756a717746f58f0f41fc4db81659`；完整回执：`data/product-local/core-direction-recheck-20260919/p0-ab-final-live-run.json`。
- 运行状态：`research_only` / `done`；停止原因：`sufficient`，详情为 `Directional hard evidence passed; execution-only gaps remain separately gated.`；仅 1 个研究轮次，不再落到 `round_budget`。
- 方向：`7d bullish`；主标题为“7日价格规则倾向：偏多；交易权限未授权”。当前行动倾向为“已有现货：持有倾向；空仓：多头候选优先，入场风险需单独评估”。这不是要求先等突破，当前规则条件已经成立。
- 数据与证据：hard coverage `1.0`；接受 5 条方向相关 Evidence；日线收盘 80890.40，高于 SMA20 78164.08，SMA20 高于 SMA60 71221.30，近 1/2/7 日收益约 +5.85%/+6.15%/+4.74%。
- 执行：`execution_view.status=blocked`、`trade_authorized=false`；执行级缺口为 `derivatives_crowding`，并明确列出宏观传导、跨资产确认未核验和概率未校准。不能据此给出杠杆或自动下单授权。
- 概率：`probability_status=unavailable`。规则方向不是胜率，Commit 仍保持 `no_trade` fail-closed；没有用 0.5 或规则分数伪造统计概率。
- UI 回读：固定入口 `http://127.0.0.1:8047/?run_id=run_c0f9756a717746f58f0f41fc4db81659` 已看到方向、行动倾向、分析过程、证据、数据限制和执行阻断。

### 回归检查

- `uv run python -m tools.contract_codegen check`：通过。
- 后端定向核心回归（市场快照 admission、研究 API、日线 outlook、LangGraph、核心流、fact pack）：通过；新增“只剩执行级缺口时方向视图收口”测试通过。
- Decision Desk：`pnpm test -- --run` 通过（4 files / 14 tests）；`pnpm build` 通过，仅保留既有 chunk size warning。
- 修改文件 Ruff 检查通过。

### 根因结论与后续停止线

本次确认的根因不是单纯缺分钟数据，而是方向层、研究可信度、入场/持仓倾向、执行权限被旧合同压成一个 `no_trade` 状态；再叠加 bounded loop 在执行缺口上重复尝试，造成“永远不给结论”的表象。P0-A+B 已修正语义和循环策略，但 provider 运输失败仍是事实限制，不得隐藏：

1. **P0-C provider 可靠性**：为 Binance/OKX、宏观与跨资产适配器增加有限超时、重试/熔断、字段级失败和可审计 fallback；当前公开衍生品路线仍可能因 TLS/connect error 不可用。
2. **P0-D typed 宏观与跨资产证据**：接入带发布时间/有效期/修订版本的 FRED vintage、利率/美元代理、风险偏好和事件窗口；只有进入 Evidence/Fact 才能影响方向或执行门。
3. **P0-E 样本外校准**：按时间顺序留出 gap 做 replay、TimeSeriesSplit、Brier/log loss/可靠性与成本后回撤；没有独立 calibration receipt 就继续显示 `unavailable`，不显示胜率。

P0-A+B 可以交付为“有边界的方向研究结果 + 明确执行阻断”，不能称为完整自动交易产品。若 P0-C/D/E 在冻结数据和成本约束下不能优于简单基线，产品定位应收敛为证据化研究/风险提示，停止包装成高胜率交易系统。

## 2026-09-19：根因修复批次（方向观点与执行权限拆分）

用户要求停止继续堆外围方案后，启动代码路径与外部方法的并行审查。审查确认：问题是结果合同和 Gate 语义耦合，不是简单缺分钟数据。

- `packages/query_views/research/service.py` 先投影有效日线策略收据，再选择 action summary。存在 7d 方向时，主标题现在显示偏多/偏空/区间和“交易权限未授权”；缺口被转换成具体的 `missing_impact`，不会再用统一的“证据或复核未通过”覆盖方向。
- `packages/orchestration/langgraph/graphs/agentic_research_graph.py` bounded run 保留研究 horizon 的方向假设，概率降为未校准并附执行抑制原因；`CommitDecisionService` 的 Forecast 仍独立 fail-closed 为 `no_trade`，没有放松交易 Gate。
- 固定入口最新建议在 `AdmissionService` 中按新的幂等键建立哈希命名空间：相同 key 重试复用同一 run，不同 key 会建立新的 event/run/触发快照，避免重复短输入静默复用旧时点。
- 固定研究模板明确要求分别输出市场方向、研究可信度、当前入场/持仓倾向、执行权限。
- 新增整改计划：`docs/plan/CORE_ROOT_CAUSE_REPAIR_PLAN_2026-09-19.md`。这条是 P0-A+B 实施前的计划记录；后续收口记录显示该 vertical slice 已完成，未扩展 PPT、分钟数据或外围评测。

验证：`tests/research/test_market_snapshot_admission.py` 8 passed；`tests/research/test_research_api.py`、日线投影和核心流 17 passed；新增 bounded 方向保留测试通过。当前尚未宣称综合交易策略交付：宏观正文、衍生品可达性、时间外概率校准和原生 7d 合同仍是未完成项。

### 根因修复后的真实 Run

- 重载当前代码后，用新的幂等键 `root-cause-check-20260919-1028` 创建 `run_db8c5d68614c4f3dadf341a9a4a1ecb7`，没有复用历史 event；完整回执保存于 `data/product-local/core-direction-recheck-20260919/root-cause-live-run.json`。
- Run 最终为 `research_only`，不是运行失败；接受了 Gate/Huobi 现货、Gate 日线、Alternative.me 情绪和 Fed 页面等 5 条 Evidence。`market.crypto_derivatives`/`market.crypto_crowding` 仍因 provider transport/connecterror 不可用，宏观/跨资产为 semantic mismatch。
- API 主结论已从旧的 `do_not_trade / 证据或复核未通过` 变为 `manual_review_required / 7日价格规则倾向：偏多；交易权限未授权`。`research_outlook.outlook=bullish`，`missing_impact` 明确列出宏观、跨资产和衍生品分别限制什么。
- 旧周期研究假设也被保留：`30m=neutral`、`24h=long`、`72h=long`，全部标为 `uncalibrated`；执行权限仍为 false，Forecast Gate 未被放宽。
- 这次真实运行验证了“方向观点与执行门分离”已经贯通 API；它仍不能证明综合宏观模型或统计胜率已经完成。该运行是 P0-A+B 完成前的中间验收，最终结果以 `run_c0f9756a717746f58f0f41fc4db81659` 为准。

### 2026-09-19：根因修复后的最终复核

- 当前浏览器已回读 `run_db8c5d68614c4f3dadf341a9a4a1ecb7`；首屏显示“7日价格规则倾向：偏多”，同时单独显示“未授权交易”、当前行动候选、主反方、失效条件、证据和每个缺口的影响。
- 重新执行核心回归：后端 44 passed，Ruff targeted check passed，canonical contract check passed，Decision Desk 4 files / 14 tests passed。
- 本轮没有把 `trade_authorized=false` 改成 true，也没有把 `probability_status=unavailable` 改成概率数字；这两项仍是产品交付前的硬边界。
- 本次复核提出的 P0-A+B vertical slice 已在随后完成：7d 方向/执行分离已提升为原生合同，`daily_direction` / `execution_candidate` 证据门已拆开，并已接通 graph、commit 与 UI；当前后续顺序是 P0-C/P0-D/P0-E。

## 2026-09-19：P0-C provider transport 修复（最小核心补丁）

对真实 Run 中的 `provider_transport_unavailable` 做了可重复的根因核对：OKX、Binance、CoinEx 在当前运行环境的连接失败属于域名/网络可达性；Gate futures 与 HTX `hbdm` 公共接口实际可达，但此前只注册为现货 fallback，衍生品查询的字段过滤不会选中它们。因此“数据不存在”这个结论是错误的，实际是路由没有覆盖可达源。

- 新增 `packages/provider_adapters/market/gate_htx_research.py`，以现有 `FactEnvelope` 合同输出 Gate/HTX 的 funding、open interest、OI 变化、mark/index 和 basis；只读、无密钥、不改变方向或执行 Gate。
- `packages/provider_adapters/market/crowding.py` 增加 Gate/HTX 公共盘口路径，保留 `proxy_kind=orderbook_imbalance`，不会把盘口不平衡冒充清算或全市场杠杆数据。
- `apps/research_mcp/main.py`、`packs/crypto_macro/tools/bindings.yaml` 和 source registry 注册新路线；`packages/runtime_adapters/dsh_runtime/profile.py` 将 Gate/HTX 设为当前环境优先，OKX/Binance 作为 fallback，并要求独立 venue，不把单一交易所当全市场。
- 修复适配器的实际请求 bug：此前 `zip(urls, gather(fetcher(url) for url in urls))` 把字典键 `ticker/stats` 当成 URL；现在按 `urls.values()` 请求，避免所有真实/测试调用静默错配。

验证结果：Gate 与 HTX 真实 smoke 均返回完整 6 个衍生品字段；Gate/HTX 盘口 crowding 均返回；适配器、路由、source registry 和 capability gateway 定向测试通过。新路线加载需要重启 research-mcp，重启后的固定入口 Run 仍必须复核 Evidence、provider attempts、coverage 和执行门，不能把 smoke 当成策略有效性证明。

外部接口依据：Gate 官方文档公开列出 funding rate 与 contract stats（含 open interest/long-short 统计）接口，[Gate API futures](https://www.gate.com/docs/developers/apiv4/en/futures/)；HTX 官方 USDT 合约文档公开列出 funding rate 与 open interest 接口，[Huobi USDT swap API](https://huobiapi.github.io/docs/usdt_swap/v1/en/)。

## 2026-09-19：真实交付验收暴露的调用串用与页面选中故障

目标仍为核心可交付，尚未完成。修复内容：

1. `DurableResearchCapabilityGateway` 按实际事件族解析新鲜度，避免把快照日频宏观数据按事件窗口 10 分钟期限拒收。完整日线成为当前方向的硬需求。
2. `CapabilityProviderRouter` 对现货和衍生品分别收集两个成功来源，而非拿到首个结果即结束；保留部分成功、失败尝试、总 deadline，并处理 Windows 超时提前唤醒导致多启动一次 fallback 的问题。
3. `ResearchPage` 修复新 Run 尚未进入列表时自动跳回旧 Run，支持 `?run_id=` 初始化及同步 URL。真实 UI 集成测试覆盖列表暂时落后的场景。
4. 失败 UI Run `run_2926189ecfd447f382571521acdf68dd` 只有两条现货 Evidence，不能验收。原生工具轨迹证明：模型为所有能力复用了 `research-request:run_...`，网关按该 ID 返回第一次现货调用的缓存，后面的日线、宏观、衍生品请求因此没有实际执行。这是代码身份/幂等缺陷，不是金融数据缺失。
5. `research-tool.ts` 现在始终使用 DSH `exec.callId` 作为工具请求身份，验证响应身份/能力/requirement。网关 reservation 增加完整规范查询 `query_hash`，同 ID 不同参数拒绝复用，旧不可验证缓存也拒绝复用；迁移 `0033_research_tool_query_identity`。
6. 明确 `max_results` 是最多 20 条 Evidence，不是历史日线根数；保留 canonical 参数校验。提示独立 Reviewer 必须收到带 URL/时点/单位/原文的证据摘要，避免只发 ID 使复核失去信息。

真实运行记录：

- `run_c30973ac3b1449a0a0e703065d676609`：hard 100%、soft 75%，10 条 accepted；日线、Gate/Huobi 现货、Gate/HTX 衍生品、宏观及跨资产取得；盘口调用错误，仍不能当作清洁验收。
- `run_2926189ecfd447f382571521acdf68dd` 的原生回执位于 `data/product-local/core-direction-recheck-20260919/ui-native-result.json`。最终状态 hard 50%、没有日线方向，属于本轮失败样本。
- `run_f7d56512a0dc4af08734081536a71af4`：修复调用身份后从页面空输入触发，目前正在运行；已真实取得日线、宏观、跨资产和盘口，说明串用已解除。仍需检查最终结果，不能提前计完成。

验证：调用身份/网关/迁移/runtime 定向回归 97 passed、1 skipped；DSH 插件 90 passed；此前前端 15 passed + build；Ruff/Pyright 通过。最终连续 Run、首屏截图及交付引导仍待验收。

### 同日后续：真实调用遗漏被计划伪装成“路径耗尽”

- `run_f7d56512a0dc4af08734081536a71af4` 最终 11 条 accepted、hard 50%。Gate 现货一次临时连接失败，Huobi成功，双交易所现货尚缺；日线、双场所衍生品和宏观/跨资产已取得。已在双来源市场路由中增加最多两次/provider的有限重试，先尝试其他来源、只重试 retryable、复用总 deadline、不重复计入成功来源。
- `run_cb4c035f6b0349de892c1eda7b1e533e` 从空输入页面提交，最终10条accepted、hard100/soft75、无工具错误；但模型漏调独立 funding/OI/basis 请求，仍缺衍生品。这次不计完整交付通过。
- 根因：`agentic_research_graph.attempts_from_result` 遍历 `round.plan.tasks`，把未执行的计划也标为已尝试。修复为读取实际 `ToolInvocation`；增加可空向后兼容 `requirement_id` 合同，由 DSH 原生工具参数及 call/session 身份投影，失败/空结果调用仍可记录，而未调用的需求不耗尽路线。新增回归确认计划有两项、实际只调用一项时，另一项保留补证能力。
- 固定入口 acquisition checklist 明确分开现货、funding/OI/basis、盘口三种调用，复核前须完成已声明需求或说明真实预算遗漏；不是只靠加强提示词，遗漏后 deterministic loop 会继续补证。
- 补充金融字段核对：Gate `quote_volume` / Huobi `vol` 不能在 base-volume 缺失时冒充 BTC 成交量；保留零成交量，缺字段返回明确失败。
- 单元/集成回归：编排/runtime/API 103 passed、1 skipped；双来源路由/组装34 passed；现货字段/投影/API17 passed。Ruff/Pyright通过。新合同由codegen生成。最终版本连续运行待下节记录。

### 同日后续：合成引用修复回执读错输出位置

`run_07242199ba644601ad923e0e102114da` 已取齐全部6类需求，12条accepted、无采集失败。最终合成因某处Evidence ID末尾漏一个字符被拒绝；一次修复仍重复相同错误。原始回执保存为 `final-acceptance-b-native.json`，失败结果保留为 `final-acceptance-b-result.json`。本Run不算交付通过。

根因不是数据不足：`map_session_result` 验证原生 synthesis tool 结果，但 `synthesis_evidence_ref_errors` 仅解析空的 `run.final_response`，导致修复提示中的错误清单为空。已统一通过 `_submitted_synthesis` 读取最后一条经过工具身份验证的合成，精确输出错误字段路径与非法Evidence ID；仍使用既有白名单，不做模糊ID替换或放宽校验。新增原生工具合成、空正文的回归；runtime/recovery 79 passed、1 skipped。

至此整套核心定向回归为201 passed、1 skipped（在此引用修复前）；追加引用修复相关测试通过，前端15项和DSH插件90项通过，contract codegen一致。查询层额外修复历史产物没有结果时的空对象访问，并让固定入口标题显示用户可读名称，不再展示内部长提示模板。最终连续真实运行仍待记录。

## 2026-09-19：连续真实固定入口验收与首屏综合区

合成引用修复部署后，使用同一个真实 DSH/模型进程组从页面固定入口连续提交两次：一次留空补充说明，一次输入“BTC 现在怎么看？”。两次都不是 fixture 或 replay，均完成到 `research_only/done`，没有 Run failure，停止原因均为 `sufficient`。

- `run_c34141f197144ea1b903e220d1be800a`：12 条 accepted Evidence、0 stale、0 rejected；覆盖完整日线、Gate/Huobi 双现货、Gate/HTX 衍生品、Gate 盘口、Yahoo 日频宏观/跨资产和公开叙事抓取。生成 7d bullish 规则方向、24h/72h 条件性偏多、30m 中性、主链/反链、失效条件和证据缺口影响；原生独立 reviewer 给出 bounded 保留意见，指出日线滞后、衍生品窗口短、订单簿为单一 venue 代理、宏观官方最新声明缺失。
- `run_41028061f6f34a0b90a186d770b4c2ef`：同样 12 条 accepted、0 stale、0 rejected，覆盖相同六类需求；现货约 81,238–81,288 USDT，日线收盘 80,890.40，高于 SMA20 78,164.08，SMA20 高于 SMA60 71,221.30；宏观 US10Y/DXY 上行与 Gate 卖方盘口优势被写入综合反方链，结论为“价格规则偏多但当前不宜追价”。该次复核只有自动证据检查，没有可验证的独立 reviewer 回执，系统因此明确显示 `manual_review_required`/`reliability=limited`，未把它伪装成复核通过。
- 两次报告的交易权限均为 `trade_authorized=false`，概率均为 `unavailable/uncalibrated`；这证明研究方向、行动候选和执行权限已分层，但不证明样本外胜率或自动交易可用。
- 保存回执：`data/product-local/core-direction-recheck-20260919/final-acceptance-c-result.json`、`final-acceptance-d-result.json`。保存页面截图：`data/product-local/core-direction-recheck-20260919/final-delivery-screen.png`。截图 URL 为 `http://127.0.0.1:8047/?run_id=run_41028061f6f34a0b90a186d770b4c2ef`。

为解决“运行中首屏显示无法形成可靠判断、用户误以为已经失败”的交付问题，`ResearchActionSummary` 在 queued/researching/retry_wait 状态显示“正在取证与复核，结论生成中”，不渲染 pending 结果的失败标签；完成后在首屏增加“本次综合研究”区，直接显示因果主张、反向解释和复核限制，均来自已保存结果。新增前端回归后 `pnpm --dir apps/decision-desk test` 为 4 files/16 tests，`pnpm --dir apps/decision-desk build` 通过（仅既有 chunk size warning）。重启产品后浏览器回读无 console error。

这批验收说明本目标的“核心主链可交付”条件已满足：用户只需点击固定入口即可获得可回读的方向和当前行动候选，报告同时展示证据、根因链、反方、失效条件、限制与执行权限。交付边界仍然明确：它是证据化研究和人工复核候选，不是已校准的高胜率模型、自动下单系统或有授权的个人投资建议。后续 P0-D 的官方宏观正文/vintage 与 P0-E 的时间序列概率校准属于下一目标，不得在当前交付中伪称完成。

## 2026-09-21：分析链路全屏查看

用户验收发现 React Flow 分析链路固定在 430px 卡片中，节点文字难以阅读。`apps/decision-desk/src/research/ResearchPage.tsx` 新增“全屏查看分析链路”入口：打开带遮罩的全屏对话框，保留 React Flow 的缩放、平移、MiniMap 和 Fit View，支持 `Esc` 与“退出全屏”关闭；全屏布局增加节点宽度与纵向间距，避免长摘要重叠。`apps/decision-desk/src/styles.css` 增加全屏容器、标题栏、操作按钮和大节点样式。

验证：Decision Desk 生产构建通过；前端 4 个测试文件、16 个测试全部通过；浏览器真实 Run `run_edf327bc678b45d6b7caaa857f4e24b5` 已打开全屏流程图，确认可视化、缩放、退出按钮和 Esc 关闭均生效。

## 2026-09-21：分析链路从“只看结论”改为可复核证据投影

对 `run_edf327bc678b45d6b7caaa857f4e24b5` 的真实 API 回执做了核对：该 Run 实际完成 1 轮研究、9 次工具调用、12 条 Evidence、1 次独立复核，图中此前却只有 13 个语义节点。根因是 `packages/query_views/research/decision_graph.py` 的 causal 投影只输出主链/反链、综合判断、时间窗口和执行门，刻意没有输出 Evidence、Review、Coverage、工具调用和轮次摘要；因此“有记录但页面看不见”与“核心链路是否真的检索”混在了一起。

本次只增加可审计结构，不暴露模型隐性思维：

- causal graph 按“来源 × requirement”把 accepted/candidate Evidence 分组为节点，保留所有 `evidence_refs`，并把证据连到实际引用它的主链或反链；增加 Coverage 和 Review 节点，复核挑战与限制进入节点摘要。
- `DecisionGraphPanel` 增加研究轮次、工具调用、采纳证据、来源数、复核数审计条；说明文字明确这是结构化研究记录，不是逐字思维链；原始 Evidence、Research plan 和 Trace 仍在页面下方可展开复核。
- 复用项目已有 `elkjs`，将 React Flow 作为渲染器、ELK 作为自动布局器，按 JSON 图的分支关系进行 layered/orthogonal 排版；布局失败时降级为稳定网格，不影响数据投影。
- 证据节点保留到 Evidence 详情的跳转，用户可以从图中的来源节点定位到原始摘录、权威级别、时点和来源链接，不需要凭节点摘要猜测依据。

验证：`.venv\\Scripts\\python.exe -m pytest tests/research/test_decision_graph.py -q` 为 2 passed；`npm test -- --run` 为 4 files/16 tests passed；`npm run build` 通过（仅既有 chunk size warning）。这项改动解决的是“证据和过程没有被展示”的问题，不把 1 个研究轮次包装成 25/50 轮，也不把结构化 Evidence 伪装成模型内部思维过程；若产品要求每次至少二次独立取证，仍需单独修改编排关闭条件并新增真实回放验收。

## 2026-09-21：研究 Harness 复用与迁移方向冻结（历史草案，已取代）

> **历史草案，不能作为当前架构指令。** 本节记录的是一次中间评估，当时把
> Deep Agents/LangGraph 写成了研究 Harness 候选。随后对锁定的 DSH 源码、真实
> Session 轨迹和扩展点完成核对，已在本文后面的“Harness 架构纠正——DSH 保留为
> 唯一 Harness”一节正式取代本节。当前实现和后续开发只遵循后者及
> `RESEARCH_HARNESS_REUSE_AND_MIGRATION_2026-09-21.md` 的最终决定，不再引入第二套
> Agent Harness。

用户指出当前项目持续在流程图、adapter 和外围验收上打转，缺少可持续的深度研究执行底座。对代码现状与公开实现复核后，根因确认：Decision Hub 已有较完整的金融证据/事实/Review/Gate 和 MCP provider，但研究编排仍由自定义 `agentic_research_graph.py` + DSH bridge 负责；该闭环会在方向性 hard coverage 达标后结束，因此一次 Run 可能只有 1 个 evidence round。UI 的审计投影修复不能替代 Harness 能力。

已冻结新的迁移方向，详见 `docs/plan/RESEARCH_HARNESS_REUSE_AND_MIGRATION_2026-09-21.md`：

- 复用 Deep Agents/LangGraph 作为研究 Harness 候选，保留现有业务 Kernel、Evidence ledger、provider adapters、MCP 和 UI contracts。
- DSH 收敛为 live runtime adapter，不再拥有第二份研究状态机或金融账本。
- MCP 统一 `search -> fetch -> extract`，A2A 只在跨服务/跨主机长任务边界使用；本进程内角色使用 subagent/task。
- 固定 supervisor 状态机：intake → plan → parallel acquisition → coverage/gap replan → challenger → synthesis → calibration → publish；不能只用 hard coverage 100% 关闭。
- Phase 1 退出门是离线 fixture 至少两轮、独立 challenger、checkpoint 恢复、完整工具/证据/错误 trace；未通过时停止扩展外围功能。

本记录只冻结方向和停止线，不把现有 UI 改动或单轮真实 Run 宣称为深度研究交付。

## 2026-09-21：Harness 架构纠正——DSH 保留为唯一 Harness

对锁定 DSH 上游源码、当前 Profile 和既有 ADR 再核对后，纠正上一节“以 Deep Agents 替换/覆盖研究 Harness”的错误方向。DSH 本身已有 Agent Loop、Session/checkpoint、MCP、subagent、compaction、skills、todo/goal 和 trajectory；再次引入 Deep Agents 会形成第二套 Harness，并重复实现项目最初选择 DSH 要复用的能力。

稳定边界现已冻结：DSH 负责内部研究计划、subagent、工具循环、同 Session 续跑和轨迹；LangGraph 只负责外层产品 Run 生命周期、Evidence attestation、Gate、恢复和发布；Decision Hub 账本仍是金融事实和 Forecast/Outcome 的唯一权威。Deep Agents/OpenAI Agents SDK 只作为参考，不进入生产依赖。

当前效果差的直接原因不是已证明 DSH 能力不足，而是集成层主动限制了它：SDK Profile 禁用 Skills；Web/SDK Profile 只给 reviewer 一个无工具、深度 1 的 subagent；Prompt 禁止 todo；外层 graph 又允许在方向 hard coverage 通过后结束。因此真实运行没有使用 DSH 的原生多角色深度研究能力。

官方 DSH 当前 release 已为 v0.1.6-alpha.2，本仓库仍锁定 v0.1.2-alpha.2。下一目标先做隔离升级 Spike，再通过 preset/plugin/Skill/MCP 扩展 manager、market、macro、narrative、counter-thesis 和 reviewer；只有官方扩展点无法修复的核心缺陷才进入受控 fork。纠正后的唯一方案见 `docs/plan/RESEARCH_HARNESS_REUSE_AND_MIGRATION_2026-09-21.md`。

### 2026-09-21：当前实现收敛为 DSH Web Host + Decision Desk

对本机真实进程、启动脚本和运行时调用链复核后，最终边界固定如下：

- `DshWebResearchRuntime` 会向 3097 的官方 DSH Web Host 提交 Session；DSH Agent Loop 负责工具循环和合成，`decision_hub_synthesis_submit` 将结构化结果交回 Hub；Decision Desk 只负责固定入口、报告和审计投影。因此结论不是后台 Python 工作台凭空生成的。
- 8047 的 Decision Desk 是用户业务入口；3097 的 DSH Web UI 是原生对话/轨迹/恢复控制台。DSH Web 进程仍是核心运行时必需项，但它不是第二套业务前端。启动器现在同时输出 `DECISION_DESK_URL`（用户入口）和 `DSH_TRAJECTORY_URL`（调试入口）。
- 当前若看到 8047 Vite 代理到 8000、Operations 仍显示旧 heartbeat，说明运行的是手工/旧进程组，不是 `run-product.ps1` 的干净实例；必须先停旧进程再按统一启动器运行，不能把这个错配误判为 DSH 没被使用。
- `infra/dsh/presets/decision-research/agent.cordis.yml` 已挂载 DSH Skills/goal；research `subagent` 允许 research/web/skill/todo 工具并以 continuable 方式运行，单独 `subagent_reviewer` 保持无工具复核；SDK profile 同步启用 Skills/goal/report。DSH 的直接子 Agent 深度是 1；子 Agent 工具白名单不含任何 subagent 工具，因此允许直接并行补证但不会递归委派。
- `packs/crypto_macro/pack.yaml` 的预算提升到 10 rounds / 100 tool calls / 32 subagents / 1800s / 3 repairs；Evidence 身份、PIT、来源时点、预算、禁止 shell/FS/插件安装/自动下单仍然是必须保留的业务边界。

这条记录覆盖并纠正本文件较早的“引入另一套 Harness”草案；生产实现只保留 DSH 作为 Harness，LangGraph 只做外层生命周期和账本。

### 2026-09-21：错配进程清理与统一入口启动验收

停止了旧的 8047 Vite→8000 API、3097 DSH 和重复 worker 进程组，按 `infra/dsh/run-product.ps1` 重新启动。启动器新增驻留循环，保持 Windows 子进程组存活；此前脚本打印 URL 后立即退出，导致服务被运行环境清理，这是“启动后页面又不可用”的直接工程原因。

当前干净启动验收：

- `http://127.0.0.1:8047/health/ready` 返回 200，静态 Decision Desk 首页由 Hub API 提供；`http://127.0.0.1:3097/decision-hub/v1/readiness` 返回 `ready=true`、`version_compatible=true`、`client_plugin=true`、`hub_reachable=true`。
- `/v1/operations` 的 runtime 为 `dsh-web`，model 为 `gpt-5.5`、API mode 为 `responses`，research worker 为 online；这证明产品研究链的运行时不是 fake/fixed fallback。
- 干净启动复制的 preset 已包含 Skills custom dir、8 queries/20 results、120s search、60s fetch、research `subagent` 和独立 reviewer。
- Operations 中 BEA/BLS 等源当前有 `source_unavailable`，这是数据源健康告警，不能伪装成 healthy；Fed/ECB/BOJ 等官方源仍在线。研究报告必须继续写出缺失源和影响，DSH readiness 与源健康是两个独立状态。
- 2026-09-21 20:08（运行时阻塞复盘）：第二次真实请求停在 `admission` 的原因不是 DSH 未启动，而是历史自动复查生成的 255 个 `research-review-plan:*` admitted/running Run 占满单 research worker。已停止产品组、将这些未执行旧复查标记为 `cancelled/stale_review_queue_cleanup`，保留已完成历史产物和新的手动 Run；下一步重启后验证手动 Run 优先被 DSH Web runtime 接管。后续应在调度层隔离自动复查队列与手动请求，禁止未受控的复查计划批量入队。
- 2026-09-21 20:14（DSH 子 Agent 真实轨迹）：清理队列后的手动 Run 已由 DSH 接管，12 条证据、5 个研究子 Agent、1 个 reviewer 轨迹均出现；首轮子 Agent 因 allowlist 声明了当前 preset 未注册的 `web_search`，被 DSH `tools.restrict()` 拒绝。已移除该不存在的工具，只保留 `decision_hub_research`、`web_fetch`、`skill`、`todo_write`，并重启后复测。`web_search` 继续受 canary 约束，不能在工具未注册时写进 allowlist。

### 2026-09-21：DSH 结构提交修复与当前运行验证

本轮确认 DSH Web Host 仍是唯一研究 Harness：真实进程为 8047 Decision Desk + 3097 DSH Web Host，Operations runtime=`dsh-web`、model=`gpt-5.5`、API mode=`responses`。Decision Desk 负责用户入口和结果投影，DSH UI 只作为轨迹/调试入口，不再存在“后台工作台另算一份结论”的业务分叉。

- `extensions/dsh/decision-hub/src/synthesis-tool.ts` 增加表示层修复：当模型仅把 `horizons` 放在 `causal_case` 内时，把同一数组移到契约要求的顶层；不合并双写、不改变 Evidence/语义，若两处同时存在仍由严格 schema 拒绝。新增回归测试覆盖该真实失败形态。
- SDK/Web prompt 增加硬能力边界：只允许 canonical `allowed_capabilities` 和 profile 暴露的工具，禁止尝试不存在的 `evidence.lookup`、`data.quality` 等名称；Alternative.me 只能使用 `requirement_id=market_narrative`。合成前明确检查 `horizons` 必须与 `causal_case` 同级，schema 错误只做同会话结构修复，不重新检索。
- 预算改为 10 rounds / 100 tool calls / 32 subagents / 1800s / per-tool 120s / per-model-step 600s；Evidence 身份、来源时点、成本、禁止自动交易等边界不放开。
- TS 插件测试 `93 passed`，插件生产构建通过；Python 全量回归修正预算断言后需再次执行。真实手动 Run `run_49eafe6b3c524c5da524445e6c8ca08f` 已被新 DSH Web Host 接管，首轮已取得 13 条 Evidence、9 次受审计工具调用，进入 DSH 研究阶段；最终结论必须等 Host 完成 synthesis submit 后再发布。
- 重启时发现历史 scheduled recheck 会占住唯一 worker。已取消当前过期复查并重启干净进程；下一步把“手动高优先级请求与 scheduled recheck 隔离/抢占”固化到调度层，避免旧复查再次让用户请求长时间停在 queued。

### 2026-09-21：手动请求优先 lane 固化

将上述调度问题落实为两个 worker lane：`RunService.claim_next` 支持 `min_priority`，普通 worker 继续处理全部研究 Run，`run-product.ps1` 额外启动 `hub-research-priority-worker`，仅 claim `priority=100` 的手动 Run。两个 lane 共享同一个 Run/Evidence/Artifact 数据库和 DSH Web Host，不生成第二份结论；普通 worker 正在执行 scheduled recheck 时，手动请求仍可由优先 lane 领取。新增 priority lane 回归测试。

## 2026-09-21：核心交付缺口复核与来源信任面补齐

用户要求在继续编码前确认当前方案是否足够交付。本次复核结论是：核心链路可以交付为证据化研究产品，但不能宣称已完成样本外校准或高胜率交易系统。最新真实 Run `run_d75406268ea74b2ab5223438826a483f` 已验证 DSH Web Host → Hub Evidence/Gate → Decision Desk 报告链，包含 12 条 accepted Evidence、5 个研究子 Agent、1 个 Reviewer，hard/soft coverage 均为 100%，并修复了长任务 lease 过期造成的重复 trace 失败。

当前剩余的用户信任缺口不是重新换 Harness，而是来源和运行回执分散：用户结果页能看到证据摘录和流程图，却不能在一个区域快速判断来源数量、按 requirement 的覆盖、Evidence 新鲜度以及 DSH 实际执行规模。为此新增 `docs/plan/CORE_DELIVERY_GAP_REVIEW_2026-09-21.md`，把交付边界、缺口和验收标准写死，并在 `ResearchRunDetail` 增加“可信度与来源”区：显示 DSH runtime/profile、研究轮次、工具调用、子 Agent、Reviewer、accepted/fresh Evidence、来源身份和 requirement→source 矩阵；来源条目可跳到真实 Evidence 或外部 URL。该区同时明确“Evidence 覆盖通过不代表预测已经校准”。

本次不放宽 Evidence 身份、PIT、来源独立性、失败 provenance、交易授权或概率校准边界；“DSH 能力拉满”按当前合同上限执行（10 轮、100 次工具调用、32 个子 Agent、1800 秒），但不强制在证据已经充分后重复空转。样本外概率校准继续作为后续独立目标，不作为当前方向研究交付的伪完成项。

## 2026-09-21：失效 Run 链接导致黑盒空页的修复

用户当前打开的 `run_49eafe6b3c524c5da524445e6c8ca08f6` 已不在当前产品账本，8047 API 返回 `research_run_not_found`。因此页面没有 Evidence、流程图或分析过程可渲染，这不是 React Flow 没实现，也不是 DSH 没有返回轨迹，而是旧 Run 链接失效。

`ResearchPage` 现在在详情请求返回失效 Run 且当前列表已有可读终态时，自动选择最新带 Artifact 的终态 Run，并显示“原任务不在当前账本，已打开最新可读报告”的提示；URL 会同步到实际显示的 Run，避免用户面对无解释的空详情页。新增 stale URL 回归测试，前端回归达到 `18 passed`，生产构建通过。当前构建静态资源已确认包含来源矩阵、全屏分析链路和失效 Run 自动恢复逻辑。

## 2026-09-22：来源采集层与研究升级方案冻结

本轮先按代码实际情况和公开项目核对，没有先扩展角色或页面。新增冻结方案
`docs/plan/PREDICTIVE_RESEARCH_UPGRADE_PLAN_2026-09-21.md`，明确了从固定入口、Run
ledger、外层 LangGraph、DSH Web Host、MCP provider、Evidence/Gate 到预测/复盘的完整边界。
方案决定 DSH 继续作为唯一 Harness；LangGraph 只负责产品生命周期和恢复；A2A 只保留为
未来跨进程专家服务的协议边界；信息获取按 API/HTTP → Playwright/Crawlee → Screenshot/OCR
降级，任何浏览器结果仍需 Evidence Admission。

公开资料核对结果：DeepSeek Harness 官方定位为可插拔的模型、工具、技能、session、storage、
loop、scheduling 和 UI 运行时，当前集成边界可继续复用；AutoGen 官方仓库已进入 maintenance
mode，不能再引入第二套运行时；A2A 是独立 agent 间互操作协议，本进程内固定 DSH roles 不需要
引入；Playwright + Crawlee 适合作为确定性浏览器采集底座；FinRL 的 ingest → environment →
backtest → paper/live 分层可借鉴，但不能替代本项目 Evidence/PIT/calibration 门。

P1 最小闭环已落地：

- 新增 `SourceAcquisitionRecord`、迁移 `0038_source_acquisition_receipts` 和
  `SourceAcquisitionService`；Hub 新增 `POST/GET /v1/source-acquisitions`。
- Hub 会校验 source registry、允许域名、HTTP(S) URL、artifact 路径、成功状态必须有 artifact，
  并按 receipt id 和 canonical payload hash 幂等；冲突不会覆盖历史记录。
- `apps/source_acquisition_worker` 新增可选 `submitReceiptToHub`，canary 设置
  `SOURCE_HUB_URL` 后可提交 receipt 元数据；raw DOM/网络/截图/trace 仍留在采集工件目录，
  不会因为提交回执就自动成为 accepted Evidence。
- 新增来源服务单测和 API readback 测试，覆盖持久化、重复提交、冲突、域名限制、路径穿越。

自测结果：`uv run pytest -q tests/sources/test_source_acquisition_service.py` 通过 5 项；
`uv run pytest -q tests/research/test_research_api.py tests/operations/test_operations_api.py`
通过 15 项；source worker `typecheck` 和 `smoke` 通过（1 页面、4 工件、receipt readback）。
全量 Python 回归在新增迁移后有 4 项：3 项旧测试把 head 写死为 0037，已更新为 0038；1 项
极短 DSH transport deadline 测试在并行全量运行中出现时序抖动，单独重跑与迁移测试为 6 项通过，
需要后续单独收敛时间依赖，不能把一次全量绿灯伪造出来。

仍未宣称完成的内容：浏览器 receipt 尚未自动解析成 typed Evidence；真实策略没有通过的样本外
calibration receipt；因此方向可作为研究倾向，概率保持 unavailable/uncalibrated，交易授权继续
关闭。下一阶段只处理这两个核心断点和它们的验收，不新增外围页面或自动交易。

### 2026-09-22：采集回执进入 Evidence admission 的可验证边界

本轮继续沿用冻结方案，没有更换 DSH 或新增第二套 Harness。为避免“浏览器采集存在但无法证明
进入研究链”的断点，完成以下核心合同：

- `EvidenceCandidate` 新增可选 `acquisition_receipt_id`；迁移 `0039_evidence_acquisition_receipt`
  在 `research_evidence` 保存该关联，并由 `_validate_acquisition_receipt` 检查 receipt 存在、状态为
  `succeeded` 且 `source_id` 一致。缺失、失败或跨 source 的 receipt 一律拒绝 admission。
- Python/TypeScript 合同由 `tools.contract_codegen` 重新生成并通过 consistency check；既有候选保持
  向后兼容，未带回执的 typed provider 证据仍按原有 PIT/来源规则处理。
- 新增回归证明：真实 receipt 可被 Evidence admission 引用并在读回时保留；不存在的 receipt 不会
  被当作证据。这个测试是 admission contract，不冒充浏览器 worker 已经完成自动 normalizer。
- source worker 的真实 canary 使用 `SOURCE_HUB_URL=http://127.0.0.1:8047` 提交成功：
  `receipt_id=ddb7d101bf60fe8cc287ed18d9143be6`，Hub 返回 `created=true`，
  `receipt_hash=291a01581d1f90e87224af9ad42b0d1f49331fe58ea66f07cdb312f77bd4d3bd`；包含 DOM、network、
  screenshot、trace 四类工件，raw 工件仍在本地 content-addressed 目录，不自动冒充 accepted Evidence。
- 全量 Python 回归：`778 passed, 4 skipped`；`uv run python -m tools.contract_codegen check` 通过，
  相关 Ruff 检查通过。前端此前 `18 passed`、生产构建通过；source worker typecheck/smoke 通过且
  已关闭 Crawlee 可选 session/statistics 持久化警告。

本里程碑仍不宣称“信息采集已经自动生成研究事实”或“胜率已校准”：浏览器回执到 typed candidate
的 normalizer/字段语义映射仍是下一步，且没有样本外 calibration receipt 时，概率继续显示
`unavailable/uncalibrated`，交易授权继续关闭。

同日补齐一个运行可观测性问题：研究 worker 的 claim loop 仍保持 1 秒，但 heartbeat 不再复用这个
轮询间隔。`DECISION_HUB_HEARTBEAT_INTERVAL=10` 由统一启动器注入，避免同步存储或长 DSH 调用让
priority lane 被误报为 offline；健康状态仍来自 durable heartbeat，不会因为改阈值伪造在线。

### 2026-09-22：浏览器采集服务与 browser.readback capability 闭环

本轮把“Playwright 能采集”推进为可被 DSH 研究链使用的能力，仍只使用现有 DSH Harness；没有新增第二套 agent runtime。

- `apps/source_acquisition_worker/src/server.ts` 提供本地受控 `POST /v1/acquire` 与 `/health/ready`，串行执行浏览器任务，使用 request/source/url 生成稳定 receipt id，允许复用成功回执，并把 receipt 提交到 Hub。
- `worker.ts` 将 response 监听和 trace 启动移到首个导航前，避免漏掉初始页面的网络证据；服务端支持 key、域名白名单和 64KB 请求上限。
- `SourceAcquisitionService` 在配置 artifact root 时校验路径归属、文件存在和 SHA-256；因此浏览器回执引用的工件不能指向工作区外的任意文件。
- `BrowserReadbackResearchAdapter` 作为 `browser.readback` capability 读取成功回执，校验 source/url/cutoff/hash，提取 DOM 正文，并生成带 `acquisition_receipt_id` 的 `EvidenceCandidate`；目前覆盖注册的 Fed/ECB/BOJ 官方日历，full_text 策略才写入文档存储。
- crypto macro bindings 与统一启动器已声明并启动该 capability；`DECISION_HUB_SOURCE_ACQUISITION_ROOT/URL/KEY` 由启动器注入，研究 MCP 组合器只把它作为受控 provider 暴露给 DSH。
- 新增回归覆盖：成功回执进入 candidate、工件篡改被拒绝；source worker typecheck、合同检查和相关 Python 测试通过。

边界仍明确：HTTP 取数失败自动切换 browser 的路由尚未接入；当前需由 DSH 按 capability 调用。浏览器回执也不能绕过 Evidence admission，概率校准和交易授权仍未因此改变。

同日运行验证发现并修复一个本地产品环境问题：`browser.readback` 的 Python HTTP client 默认继承代理环境，导致访问本机 `127.0.0.1:8038` 时被代理返回空 502，PowerShell 直连却正常。已在 `_http_receipt_fetcher` 使用 `trust_env=False`，确保 loopback 请求不绕代理。

修复后完成真实链路：统一启动器重启成功，`/health/ready`、Hub API、source-acquisition-worker 均 ready；POST Fed FOMC 官方日历得到 `receipt_id=4fb0cc541e86c5199b42bece821ed0d5`、HTTP 200、DOM/network/screenshot/trace 四类工件，Hub `/v1/source-acquisitions` 可读回。随后真实 `BrowserReadbackResearchAdapter` 读取该 worker 回执，校验 DOM hash、写入 content-addressed document，生成 `EvidenceCandidate`（receipt link、content_ref、4000 字符 excerpt、`playwright-dom-main-text.v1+official-event.v1` parser）。这证明采集→回执→Hub→browser.readback→typed candidate 已在当前运行态闭环。

### 2026-09-22：web.fetch 官方动态日历失败回退

在确认 Python 进程代理问题后，进一步完成 P1 的 fallback 缺口：`BrowserFallbackWebFetchAdapter` 保持 DSH 对外的稳定 capability id 为 `web.fetch`，先执行 HTTP/HTML；只有 Fed/ECB/BOJ 注册日历 URL 且发生可重试、空正文、媒体类型或上游拒绝等失败时才调用 `browser.readback`。浏览器成功结果会转换回 `web.fetch` 结果，但候选保留 `acquisition_receipt_id`，因此路由、来源和 Evidence admission 仍可回溯；非官方日历和 HTTP 成功路径不会启动浏览器。

新增回归覆盖 fallback 仅在日历 HTTP 失败时触发；composition/provider-router 测试通过，web.fetch 的 ECB/BOJ 域名已纳入 manifest。同步将普通 HTTP document fetch 设为 `trust_env=False`，避免桌面代理误拦截公开研究源。

同日复核 P2 校准交付：`ForecastCalibrationReport` 已计算 base-rate baseline Brier、walk-forward accuracy/Wilson、Brier、log loss、净收益、回撤、reliability bins 和 failure_reasons；`StrategyCalibrationService` 只接受持久化 receipt，Decision Desk 已展示策略与 baseline Brier、成本后收益、最大回撤及每条失败原因。没有通过 receipt 时 `research_outlook` 保持 `uncalibrated/baseline_only`，执行权限仍为 false。本项由代码与现有前端测试共同验收，不把 evaluator 的结果当作真实胜率。

### 2026-09-22：P3 角色包、观点变化和 bounded fallback 投影

为了解决“图有节点但看不出各职责做了什么”的问题，新增 `role-packet.v1` 与 `claim-transition.v1` 合同，并在 `ResearchQueryService` 从 durable Evidence、Coverage、round/replan trace、CausalCase 和 Review 生成角色包：market structure、derivatives/crowding、macro/calendar、cross-asset、narrative/source critic、counter-thesis、data-quality、manager、synthesizer、reviewer。每个包只含 claims、Evidence refs、gap ids、职责状态和结论；没有把模型私有思维链伪装成可见内容。

每个 round/replan marker 生成 claim transition，关联该轮新增 Evidence 和开放缺口；若 trace 明确写出 thesis/direction change 才标记 `changed=true`，否则显示“未记录方向改变”，避免凭空推断。evidence-only 或 synthesis 失败时，角色包仍输出 blocked/partial 状态，`market_view` 也会保留 unresolved 的主边界、反方未复核说明、失效条件和缺口影响。

Decision Desk 技术详情新增“专家角色证据包”和“轮次观点变化记录”区域，继续区分结构化审计投影与模型私有思维链。合同生成、API/graph/角色包测试和前端 18 项测试均通过。
## 2026-09-22 · live core-chain canary and generation/round fix

- 发现真实 DSH 运行 `run_6a02a2ffc8eb44e2a2ff7ce313c4986c` 已取得 12 条 Evidence，但模型在同一 DSH generation 内发送 `round=2`，网关按 `generation=1` 拒绝后续调用，导致研究停在 `acquiring_evidence`。
- 修复 `packages/workbench_adapters/durable_research_gateway.py`：会话、事件、PIT、预算仍由 Hub 严格校验；仅将模型提供的内部 round 规范化为受 Hub 信任的 durable generation，避免同一 DSH 轮次因内部循环编号误差被截断。
- 更新 `tests/research/test_durable_research_gateway.py`，验证 round 规范化后 Evidence lineage 仍为 generation 1；全量 `uv run pytest -q`：`783 passed, 4 skipped`。
- 重启受管产品并跑 live canary：`run_957038f927b44f6ab42ed6350546bc45` 完成 `research_only`，12 条 accepted Evidence、1 轮、119 条规范化轨迹、9 个角色证据包、1 条观点变化记录、完整主因果链/反方链/分析图。
- 该运行形成 7d `bullish` 的 `daily-trend-v0.1` 基线展望；24h/72h 情景为 `long`，30m 为 `no_trade`。概率仍为 `unavailable/uncalibrated`，reviewer 为 `bounded`，`trade_authorized=false`，因此系统没有伪造胜率或交易授权。
- 运行时复核：Hub、realtime、research、priority research 在线；Fed/ECB/BOJ 官方源健康；BEA/BLS 源仍为 `degraded/source_unavailable`；evolution worker 为 owner-triggered、当前 offline。
- 页面核验发现默认选择策略会优先打开最新排队的 scheduled recheck，掩盖已有终态报告；`ResearchPage` 已改为无 URL 时优先选择最近有 artifact 的终态报告，其次选择研究中任务。前端 `18 passed`，生产 build 通过。
- 重启后的 Playwright/Crawlee 信息层 canary 成功：receipt `1eb0dbcae5d2c70d430653ac3072120a`，Fed 官方日历 `access_mode=playwright`，Hub 列表可回读 DOM/network/screenshot/trace 四类工件及 hash。

### 2026-09-22：客户操作手册与 research-only 交付验收

本次不新增外围功能，直接用真实浏览器验收当前核心链路，并将可交付边界固定成文档：

- 真实启动 `run-product.ps1` 后确认 8047 页面、Hub、research worker 在线；
- 通过“获取最新操作建议”打开固定研究模板，确认补充关注点可留空，不要求客户掌握触发关键词；
- 复核终态报告 `run_957038f927b44f6ab42ed6350546bc45`：12 条 accepted Evidence、8 个来源身份、1 轮、11 次工具调用、5 个子 Agent、1 次 bounded reviewer、119 条规范化轨迹、27 节点 causal graph；
- 真实点击全屏分析链路、技术详情和历史日历。历史日历返回 0 条时保留原始提示，没有用猜测填充历史事件；
- 新增 [`DECISION_HUB_CUSTOMER_USER_MANUAL_2026-09-22.md`](../user-guides/DECISION_HUB_CUSTOMER_USER_MANUAL_2026-09-22.md)，包含启动、触发、状态、结果、Evidence、分析链路、技术轨迹、日历、复查、故障处理和截图；
- 新增 [`DELIVERY_ACCEPTANCE_GOAL_2026-09-22.md`](./DELIVERY_ACCEPTANCE_GOAL_2026-09-22.md)，冻结本轮验收清单、证据、已知限制和停止规则。

验收结论：当前版本可以按 research-only 客户预览交付；样本外概率校准、完整历史宏观日历和交易授权均未通过，不能宣称自动交易或高胜率产品。只有出现核心链路复现失败、Evidence 与报告不一致、worker 无法启动或边界被绕过时，才创建下一轮整改目标。
