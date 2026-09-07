# PD 实施与自测执行日志

状态：`PD-00..06 engineering/runtime closeout complete / PD-07 observation pending`  
阶段：[PD 产品事实充分度与主动交付](../stages/PD_PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY.md)  
详细方案：[产品事实充分度与主动交付修复方案](../product/PRODUCT_FACT_SUFFICIENCY_AND_ACTIVE_DELIVERY_PLAN_2026-09-04.md)

## 2026-09-05 本轮交付收口

独立执行方案：[Decision Hub 主动研究交付修复方案](../product/PRODUCT_AGENTIC_FACT_SUFFICIENCY_REMEDIATION_2026-09-05.md)。本轮目标是收口已有代码和运行链，不新增第二套 Agent Loop、Provider DTO、账本或前端入口。

### 根因与修复

- DSH Web 的标准 slot props 可能以非 enumerable 属性提供 `sessionId`；`{...props}` 会丢失该字段，导致报告页显示“当前会话尚未关联正式研究任务”。`conversationScopeProps()` 现在显式从官方 Session projection 读取并传给 Report/Inbox/Intake 包装器。
- replay/fake harness 只有聚合能力 `replay.research`，第一代和第二代 fixture 输出不同；supervisor 原逻辑只寻找未尝试 capability，因此把合法回放续轮过早收口。现在只有 `execution_mode=replay`、第一轮已有 accepted Evidence、能力集合严格为 `{"replay.research"}` 时允许一次下一 generation，仍受 `max_evidence_rounds/max_tool_calls/deadline` 限制；live 运行不获得该例外。
- 类型门的测试直接引用私有 helper，以及把通用 adapter Protocol 当具体 official adapter 写入 `fetcher`，已通过测试局部 `pyright` 豁免和显式 `cast` 修复；生产接口未扩大。

### Red / Green 证据

Red：

```text
tests/dsh_native/test_native_replay_matrix.py::... [success]  expected submits=2, actual=1
tests/evolution/test_research_worker.py::...                 expected requests=2, actual=1
pyright                                                        5 errors
```

Green：

```text
.venv/bin/pytest -q tests/orchestration/test_g2af_continuation.py \
  tests/dsh_native/test_native_replay_matrix.py tests/evolution/test_research_worker.py
27 passed

.venv/bin/pytest -q
551 passed

.venv/bin/pyright packages apps tests tools/canary
0 errors, 0 warnings

.venv/bin/ruff check packages apps migrations tests tools
All checks passed

.venv/bin/python -m tools.contract_codegen check
canonical schemas: ok

.venv/bin/python tools/docs/check_module_docs.py
module docs: ok (13 modules)

git diff --check
passed

pnpm --dir extensions/dsh/decision-hub test -- --run
69 passed

pnpm --dir extensions/dsh/decision-hub build
passed; build hash 3c0118e76c8c932f2a6f59a2269f2c0603c063e0d56b7ad30a9d01125e1b1889

pnpm --dir apps/decision-desk test -- --run
10 passed

pnpm --dir apps/decision-desk build
passed; Vite production build completed
```

### 真实页面证据

此前启动的隔离产品实例已完成 Host readiness 和同一真实 Run 的浏览器复验：

- DSH 报告：[pd-closeout-dsh-report-20260905.png](assets/pd-closeout-dsh-report-20260905.png)
- DSH 轨迹：[pd-closeout-dsh-trace-20260905.png](assets/pd-closeout-dsh-trace-20260905.png)
- Decision Desk：[pd-closeout-decision-desk-20260905.png](assets/pd-closeout-decision-desk-20260905.png)

页面可见报告、`research_only`、hard coverage、Evidence、tool calls、DSH 多轮、provider failure、未关闭 gap、scheduled recheck 和审计链接；Session/Run 关联已恢复。真实 Run 仍因事件后回溯缺少完整窗口、FRED 延迟、预期定价和部分 provider denied/timeout 而保持 `research_only`，这不是页面 bug，也不能解释为预测成功。

### 本轮结论

`PD-04..06 runtime closeout = completed in working tree and verified`。产品交付边界仍为单 owner、单机、只读 `research_only` 试点；真实分钟级宏观/预期定价 Provider、前瞻 Outcome/价值观察和盈利能力仍未证明。下一阶段只能进入 `PD-07` 前瞻观察或独立 Provider bake-off，不能继续无边界堆工程功能。

### OOM 根因、启动屏障与最终复验

- scheduled recheck `run_8886586f38a24fe4949a1da0e741c98c` 在第 0 轮以
  `host_hub_unreachable` 失败。Docker event 证明直接根因是 Hub API OOM、`exitCode=137`：约
  7.75 GiB 的 Docker VM 同时运行 68 个容器，其中 12 套旧 acceptance/test Compose 栈占用大量
  内存。DSH 在 Hub 恢复后无需重启即可重新 `hub_reachable=true`，排除 Cordis 地址解析和永久连接
  故障。
- 仅停止旧测试栈和 3 个旧 launcher；未删除 volume、数据库、Session、历史 Run、镜像或其他项目
  数据。运行容器降至 8 个，当前五个 Hub 服务约 606 MiB。
- `run-product.sh` 现在只构建当前工作树镜像一次，然后以 `--no-build` 启动控制面；通过 Hub、Inbox
  contract、MCP transport、DSH Host/build identity/Hub callback readiness 后，才以
  `--no-deps --no-build` 启动 research worker。该顺序同时关闭“DSH 未就绪即消费 Run”的独立竞态。
- 使用既有 owner `retry` 创建 child `run_2c06947df692424e326161d83f488368`；它建立 DSH Session
  `dsh_07e5a2012555a0ac614c8e0b1a749f08ab270a12afa8fd4884a4670fe235e2d1`，完成 3 次 generation、
  2 个提交轮次、16 次工具调用并生成 `art_a5a513d76cb145299cb412454f21fa0f`。终态为
  `research_only/provider_timeout`，保留最后一次 attested synthesis 和 durable Evidence，不发布方向性
  结果。
- 浏览器实测：官方 DSH 报告显示 8 条有效证据、3 轮轨迹、16 次调用和完整失败 provenance；主动
  研究页显示 scheduled recheck、通知、排队/失败/仅研究状态；Decision Desk 对同一 Run 显示 102 条
  归一化事件、Evidence 接受/拒绝、Gate、三周期输出和 owner commands。Session、Run、Artifact
  关联一致。

最终质量门：

```text
tests/dsh_native/test_product_launcher.py + runtime recovery    14 passed
.venv/bin/pytest -q                                             552 passed
.venv/bin/ruff check packages apps migrations tests tools       passed
.venv/bin/pyright packages apps tests tools/canary              0 errors / 0 warnings
.venv/bin/python -m tools.contract_codegen check                canonical schemas: ok
.venv/bin/python tools/docs/check_module_docs.py                module docs: ok (13 modules)
git diff --check                                                passed
DSH Extension                                                   69 passed + build
Decision Desk                                                   10 passed + build
```

该复验只关闭工程和运行可靠性问题，不启动或回填 PD-07；PD-07 仍是
`entry ready / observation not started / started_at=null / prospective sample count=0`。

## 执行纪律

每张任务卡记录 scope/非目标、Red、实现位置、测试命令、Run/样本、UI 证据、根因、未完成项和 `continue/retain/stop`。历史数字不覆盖最新质量门；live 结果和 replay 结果不混写。

## 基线审计（2026-09-04）

- 全量 Python：`445 passed, 1 failed`。
- 唯一失败：`test_web_runtime_timeout_cancels_and_fails_closed`；`10ms` 截止在 Host submit 前耗尽，`submit_calls=0`，旧测试却无条件要求 cancel。
- DSH Extension：`60 passed + build`；Decision Desk：`10 passed + build`。
- Ruff、Pyright、canonical codegen、module docs、`git diff --check` 通过。
- 结论：`continue PD-00`；先定义 accepted-before-cancel 行为并恢复绿色基线，不改生产超时掩盖。

## PD-00

状态：`completed / continue PD-01`

### Scope

恢复测试绿线，增加显式 requirement lineage、FactEnvelope、`crypto_macro` 真值表和 semantic-substitution Gate；保持历史 payload 可读。

### 非目标

本任务不启用新的 live Provider、不扩大域名、不改 DSH Agent Loop、不开始前瞻价值观察。

### 执行记录

#### Red

- `ResearchTask` 缺少显式 `requirement_id`，task attempt 只能按数组序号推断 gap。
- 旧 Gate 只检查 Evidence 的 freshness/authority/source count，Web 摘要、BTC 衍生品或当前
  snapshot 可能被错误计入不相干的金融 requirement。
- 新语义 Gate 接入后，官方 DSH `success` replay 因只携带 `EvidenceCandidate`、没有
  `FactEnvelope`，从旧预期的两轮变成三轮并使全量测试出现 1 个失败。这是夹具暴露了旧假成功，
  不是应放宽 Gate 的理由。
- `10ms` Web Runtime 用例把 submit 前 deadline 与 Host accepted 后 timeout 混为一种 cancel
  语义，形成 1 个旧断言失败。

#### Green / 实现

- canonical `agentic_research.schema.yaml` 增加显式 task lineage 和 requirement 语义声明；新增
  `research_fact.schema.yaml`，由 codegen 同步 Python/TypeScript/Zod，禁止手写镜像。
- migration `0028_research_fact_envelopes` 和 `ResearchFactStore` 持久化 typed facts，校验
  Evidence/Run/source lineage、payload hash、三时间戳 PIT、窗口顺序、幂等和身份冲突；历史
  Evidence/Run 不回写。
- `crypto_macro/evidence/source_manifest.yaml` 成为八类 requirement 的 metric family、字段、单位、
  event offset、venue、independence group 和 delay class 真值表；Kernel 只执行通用声明式 Gate。
- CoinEx、OKX、FRED adapter 同时返回 Evidence 与 typed Facts；当前 snapshot 仍会诚实得到
  `no_baseline/window_missing`，不会冒充事件窗口。
- Gateway 先接受 Evidence 再接受 Fact；DSH mapper 只映射 attested Facts；LangGraph checkpoint
  持有 facts、每轮从账本重读，并按显式 `requirement_id` 记录 attempted capability。
- success replay 已补齐字段/单位/事件偏移/独立来源/hash 的语义事实，恢复为同一 DSH Session
  两轮补证后 `publish/sufficient`；partial failure 与 stale 场景继续 fail-closed。
- submit 前 deadline 不 cancel 不存在的 Session；Host accepted 后 timeout 才 best-effort cancel。

#### 测试证据

2026-09-04 最终复跑：

```text
.venv/bin/pytest -q                                      454 passed
.venv/bin/pyright packages apps tests                    0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools            passed
.venv/bin/python -m tools.contract_codegen check          canonical schemas: ok
git diff --check                                          passed
```

本卡只证明契约、持久化、语义替代和 replay 行为正确；未证明分钟级真实 Provider、事件前基线、
正式交易判断或盈利。结论：`continue PD-01`。

## PD-01

状态：`completed / continue PD-02`

### Scope

在现有 source/calendar、Kernel database、`RealtimeScheduler` 和 worker composition 上增量建立
durable `EventWatch` 与事件窗口采样；不创建第二套 scheduler、账本或服务。

### 非目标

本卡不购买或选择正式分钟级 Provider，不伪造旧事件基线，不开始 PD-02/PD-07 live 价值观察。

### 首个 Red

未来日历事件必须保存规范化 `scheduled_at` 并创建幂等 watch；重复 tick/重启不能重复窗口，
迟到事件没有历史 snapshot 时必须返回 `baseline_unavailable/retrospective_only`。

### Green / 实现

- canonical `EventWatch`、`EventWindowSample`、`EventWindowCapture` 由 schema codegen 生成；
  migration `0029_event_watches` 只新增表，不改写历史 Event/Run/Evidence。
- 官方日历 adapter 显式输出 `scheduled_at`；admission 对日历事件使用该规范时间，
  `SourceIngestionService` 幂等创建 watch，并复用 Run 的 `available_at` 等待事件时间。
- `RealtimeScheduler` 在原有 tick 内推进 watch，通过 `EventWindowSamplerPort` 调用外部采样器；
  Kernel 只保存 provider、payload ref/hash 和 PIT 时间，不保存供应商私有结构。
- 未来窗口固定为 `T-30m/T-5m/T0/T+1m/T+5m/T+30m/T+24h/T+72h`；迟到事件不
  补造 baseline，采样错误和五分钟宽限过期分别保留为可审计状态。
- SQLite naive UTC 在公共模型边界统一归一化；身份/窗口冲突、future capture、PIT 逆序和
  重复 capture 均 fail-closed。

### 测试证据

2026-09-04 最终复跑：

```text
.venv/bin/pytest -q                                      463 passed
.venv/bin/pyright packages apps tests                    0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools            passed
.venv/bin/python -m tools.contract_codegen check          canonical schemas: ok
git diff --check                                          passed
```

其中完整生命周期 BDD 将时钟依次推进到八个窗口，中途重建 Database/Service 模拟进程重启，
每个窗口重复执行 tick；最终八个 sample 均只 capture 一次，watch 为 `completed`、baseline 为
`ready`。迟到事件、DST、采样失败、宽限过期、scheduler 自动推进和 migration upgrade 另有独立
用例。结论：`PD-01 completed / continue PD-02`；未伪造或证明真实金融 Provider 数据。

## PD-02

状态：`completed / PD-02A..G engineering; licensed live provider blocked`

### Scope

按 [PD-02 Typed Provider Pack 实施与验收方案](../stages/PD_02_TYPED_PROVIDER_PACK.md) 建立
stable capability、provider route、事件窗口 query、primary/fallback、attempt/cost/service-tier
审计和真实 composition；复用现有 Gateway、FactStore、semantic Gate 和 DSH 单一工具。

### 非目标

不新增 Agent Loop，不降低 Gate，不把 delayed/free proxy 冒充正式 realtime，不在普通测试触网，
不提前实现 PD-04 页面或 PD-06 进化。

### 首个 Red

当前 `apps/research_mcp/main.py` 通过环境变量在 CoinEx/OKX 间二选一，但 Pack manifest 永远声明
CoinEx implementation/domain；选择 OKX 后真实 Gateway 会拒绝 OKX URL。两个 adapter 还共用同一
capability ID，无法同时注册或自动 fallback；query/result 也无法表达事件窗口和多 route attempt。

### PD-02A：契约与失败审计（completed / continue PD-02B）

#### Red

- `ResearchCapabilityResult` 已可保存成功 route 的 attempt，但全部 provider route 失败后，
  durable reservation 只保存通用 `ErrorProvenance`；primary/fallback 的失败顺序、费用、服务等级、
  延迟和 retryability 会在进程恢复后丢失。
- HTTP timeout、429、5xx、4xx 与 transport failure 尚未由独立边界测试锁定；后续 router 容易把
  配置/契约错误误认为可回退故障。

#### Green / 实现

- canonical `ErrorProvenance` additive 增加 `provider_attempts`，codegen 同步 Python/TypeScript；
  旧 error payload 继续以空数组可读，不迁移、不回写历史 reservation。
- `ResearchCapabilityError.provenance()` 和 durable recovery `_error_from_provenance()` 双向投影
  `ProviderAttempt[]`；既有 `ResearchObservabilityService.fail_tool_call()` 继续持久化 canonical
  `error_json`，没有新建第二份 provider attempt ledger。
- `ProviderCapabilityRouter` 继续只对 timeout/429/5xx/transport 的 retryable 错误尝试 fallback；
  contract/PIT/字段/单位等 non-retryable 错误立即 fail-closed。新增 `http_errors.py` 的类别测试，
  明确不泄漏上游 payload 或密钥。

#### BDD/TDD 证据

- primary 成功：fallback 不调用；429 后 fallback 成功：两条 attempt 保留；contract 错误：不回退。
- primary/fallback 都失败：首次返回的 error 及进程重建后的 idempotent recovery 都保留相同的
  `ProviderAttempt[]`，不折叠为模糊的 `research_capability_failed`。
- HTTP 429、5xx、timeout、4xx、transport 与既有 `ResearchCapabilityError` 均有离线分类测试。

2026-09-04 本卡质量门：

```text
.venv/bin/pytest -q                                      473 passed
.venv/bin/pyright packages apps tests                    0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools           passed
.venv/bin/python -m tools.contract_codegen check         canonical schemas: ok
.venv/bin/python tools/docs/check_module_docs.py         module docs: ok (13 modules)
git diff --check                                         passed
```

结论：`continue PD-02B`。本卡只证明稳定契约、失败分类与 durable route lineage；没有完成
`apps/research_mcp` 的 stable router composition、没有新增真实 market/macro provider、没有进行
live canary，不能把它解释为事实充分或可交易。

### PD-02B：Stable Provider Router Composition（completed / continue PD-02C）

#### Red

- product composition 仍通过 `DECISION_HUB_CRYPTO_DERIVATIVES_PROVIDER` 在 OKX/CoinEx 间
  二选一，而 Pack 固定声明 CoinEx implementation/domain；配置、allowlist 和真实 adapter 脱节。
- 两个 vendor adapter 共用 stable capability ID，直接注册会冲突；OKX 又没有实现 DSH 当前请求的
  `spot_price/spot_volume/index_price/basis`，不能通过优先级假装具备字段能力。

#### Green / 实现

- `packs/crypto_macro/tools/bindings.yaml` 为 `market.crypto_derivatives` 声明合并 allowlist、OKX
  primary 和 CoinEx fallback；`apps/research_mcp` 从 manifest 装配唯一 stable Router，删除环境
  变量供应商选择。
- Router 按 approved/audited route、请求域和 `supported_fields` 保守过滤；未知 ref、重复
  provider ID、capability/mode mismatch 和无可用 route 都 fail-closed。
- 只有 retryable timeout/429/5xx/transport 错误继续 fallback；non-retryable 字段/契约/PIT/域
  错误不被换供应商掩盖。attempt 继续复用 canonical result/error 和既有 durable reservation。

#### BDD/TDD 与质量证据

- Pack composition、旧环境变量无效、primary success、Gateway 下 retryable fallback、按 domain
  只选指定 route、按字段跳过不兼容 route，以及所有配置错误均有离线测试。
- PD-02A 的 durable Gateway 测试继续证明 provider attempts 首次失败与重启恢复一致。

```text
.venv/bin/pytest -q                                      486 passed
.venv/bin/pyright packages apps tests                    0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools           passed
.venv/bin/python -m tools.contract_codegen check         canonical schemas: ok
```

结论：`PD-02B completed / continue PD-02C`。没有触网；当前 public adapters 仍是 current
snapshot，不能关闭事件窗口/OI delta 或正式宏观 requirement，产品状态保持 `research_only`。

## PD-02C：Crypto Event-Window Facts（completed / continue PD-02D）

### Scope

把 PD-01 的 durable EventWatch 窗口接入 DSH stable `market.crypto_derivatives` capability：由
RealtimeScheduler 捕获 `t-5m`/`t+1m`，写入不可变内容寻址 payload，再由 archive route 投影为
Run-scoped Evidence/Fact，最终经过既有 FactStore 和 semantic Gate。保持 DSH 为唯一 Agent
Harness，LangGraph 只负责 Run/checkpoint，Hub 只负责账本与裁决。

### 非目标

本卡不实现 `crowding_signal`、分钟级宏观/expectation pricing、第二套 Agent Loop、自动交易或
真实 Provider 长期价值观察；OKX/CoinEx live 可用性需独立 canary，不能用 fixture 代替。

### Red -> Green

- Red：EventWatch 已有窗口但 worker 没有 sampler composition；archive payload 没有完整性边界；
  DSH/MCP 工具没有暴露 event/window 参数；Gateway 没有把模型 event time 与 Run/EventWatch
  对账；Router 可能把事件查询回退为 current snapshot。
- Green：新增 canonical event-window payload/failure/observation 与 codegen 镜像；实现
  `CryptoEventWindowArchive`、multi-venue sampler、archive -> Evidence/Fact adapter；worker
  显式注入 sampler；DSH/MCP 透传参数；Durable Gateway 投影 server-owned 时间并 fail-closed；
  Router 对 event query 只选择 archive，对普通 query 排除 archive。
- 根因修正：Provider 私有 payload 仍留在 adapter/archive，Kernel 只保存 ref/hash；事件事实必须
  有真实 offset、PIT、Run lineage 和 Evidence 引用，缺失 baseline 不由 current snapshot 填补。

### BDD/TDD 证据

- archive canonical hash、atomic write、幂等、ref/hash/payload 篡改拒绝；sampler 部分失败保留
  failure，全部失败不提交 slot。
- Gateway 覆盖 event_id mismatch、无 EventWatch、非法 offset、event/window 时间冲突，以及
  合法请求的 server-owned projection；Provider 调用前均 fail-closed。
- Router 覆盖普通查询不调用 archive、事件查询只调用 archive、archive 非可重试失败不回退
  current snapshot。
- 端到端回放覆盖 `EventWatch -> archive -> Router -> Durable Gateway -> Evidence/FactStore ->
  semantic Gate`，并证明 `price`/`event_return` 事实可被语义 Gate 识别。

### 质量门（2026-09-04）

```text
.venv/bin/pytest -q                                      503 passed
pnpm --dir extensions/dsh/decision-hub test               60 passed
pnpm --dir extensions/dsh/decision-hub build              passed
.venv/bin/pyright packages apps tests                     0 errors / 0 warnings
.venv/bin/ruff check packages apps tests tools             passed
.venv/bin/python -m tools.contract_codegen check           canonical schemas: ok
.venv/bin/python tools/docs/check_module_docs.py           module docs: ok (13 modules)
git diff --check                                           passed
```

### 结论与保留项

PD-02C 工程退出门已通过，下一唯一任务为 PD-02D `crowding_signal` 与衍生品语义事实。当前
`crypto_spot_confirmation` 可在事件窗口有捕获时进入事实链，但 derivatives crowding、macro/
expectation 仍不足；产品继续保持 `research_only`，不代表事实充分、预测准确、盈利或正式交易
可用。所有测试使用 fake/replay 与临时目录，不触网、不读取真实密钥。

## PD-02D：Crypto Crowding Facts（completed / continue PD-02E）

### Scope

在既有 stable capability、Pack Router、EventWindow archive 和 FactStore/Gate 上增加
`market.crypto_crowding`。当前公开能力只计算 order-book imbalance，必须保留
`attributes.proxy_kind=orderbook_imbalance`，不把它解释为清算、杠杆、多空比或完整拥挤度。

### 非目标

不新增 Agent Loop/MCP 工具/数据库，不降低 derivatives semantic Gate，不在普通测试触网，不把
public endpoint 的离线 fixture 当作真实可用性或交易价值证据；intraday macro/expectation pricing
仍属于 PD-02E。

### Red -> Green

- Red：Pack 要求 `crowding_signal`，但只有 funding/OI/basis current/event-window facts；合法的
  `t-5m -> t+1m` 回放还因 60 秒 snapshot freshness 把基线错误标为 stale。
- Green：新增 OKX/CoinEx depth adapter、stable Router route、三个角色 allowlist 和 worker event
  sampler；事件 archive 可以投影 crowding facts。crypto spot/derivatives 的 event-window freshness
  改为 600 秒，恰好覆盖合法 `t-5m -> t+1m` 比较，不允许无 offset current snapshot 通过。
- 新增全链行为测试，真实执行 `EventWatch -> archive -> Router -> Gateway -> Evidence/FactStore ->
  Semantic Gate`；完整 funding/OI/OI delta/basis/crowding 为 sufficient，删除 crowding 后必须
  `semantic_mismatch`。
- 新增 adapter fixture 覆盖 OKX/CoinEx payload、空深度、单位、metric family 和 proxy 标记；
  composition 继续由 Pack route 驱动，不增加供应商环境变量分支。
- Decision Desk 的成本卡不再把 detail 缺失显示成 `$0.0000`，而是明确“未知”；只有选中 Run 的
  canonical detail 提供 estimate 时才显示金额，不伪造全局总成本。

### 代码与文档

- `packages/provider_adapters/market/crowding.py`
- `packages/provider_adapters/market/event_window.py`
- `apps/research_mcp/main.py`
- `apps/hub_worker/composition.py`
- `packs/crypto_macro/{pack.yaml,tools/bindings.yaml,evidence/source_manifest.yaml,profiles/*.yaml}`
- `tests/research/test_crypto_crowding.py`
- `tests/research/test_event_window_crowding_chain.py`
- `docs/stages/PD_02D_CRYPTO_CROWDING_FACTS.md`

### 保留项

- `crowding_signal` 当前是单 venue order-book proxy，不是独立 licensed crowding feed；真实 OKX/
  CoinEx canary 未由本卡执行。
- 本卡没有证明分钟级宏观、Fed expectation pricing、Search 长期稳定、预测准确或盈利。
- 产品状态保持 `research_only`，下一唯一入口是 PD-02E。

最终质量门在本轮全部命令完成后追加；离线工程通过、live canary 和产品价值必须分别记录。

## PD-02E：Intraday Macro / Expectation Pricing（engineering completed / live provider blocked）

### Red

- `macro.cross_asset_intraday` 在生产 Pack 中曾是 `approved`，但 capability/route 没有域名；
  真实 Gateway 会在 adapter 前报 `research_broad_permission_required`，与“provider 未配置”的
  产品语义不一致。
- Intraday adapter 只取 cutoff 前最新 point，事件查询丢失 `event_offset`，无法证明
  `t-5m/t+1m` 或 rates/USD 的独立 provenance。
- 两个宏观 requirement 的 300 秒 freshness 小于合法 `t-5m -> t+1m` 的 360 秒间隔，会把
  正确事件窗口错误判为 stale。
- 只有 adapter 单测，没有通过 Router、Gateway、FactStore 和 Domain Semantic Gate 的完整宏观
  与 expectation 行为链。

### Green

- 新增独立阶段卡 [PD-02E Intraday Macro / Expectation Pricing](../stages/PD_02E_INTRADAY_MACRO_EXPECTATION_PRICING.md)，
  先锁定 scope、非目标、BDD/TDD、live-provider 阻断和 `engineering_exit/live_provider_exit`
  双状态，避免把 fixture 通过写成生产事实充分。
- 生产 `macro.cross_asset_intraday` 与 `macro.expectation_pricing` 均改为
  `candidate/review_required`；宏观 route 声明 `requires_event_window=true`，未获授权在 adapter
  前 fail-closed，不用 `search:broad` 或假域名绕过 Gateway。
- Intraday adapter 现在保留事件 offset、provider source/independence group/source URL，并将
  event_id、event_at、requested offsets 和 cutoff 传入 provider-neutral endpoint；普通查询仍
  只取 latest PIT point。两个宏观 requirement 的 freshness 为 600 秒，仅覆盖合法事件信封。
- Expectation adapter 复用 canonical event query builder，保留 offsets；Provider cost 支持显式
  `*_ESTIMATED_COST_USD`，未配置时为 `null`，不伪装为 `$0.0`。
- 新增 `tests/research/test_macro_event_window_full_chain.py`，真实运行
  `adapter -> Router -> Gateway -> Durable Gateway -> Evidence/FactStore -> Semantic Gate`，覆盖：
  realtime sufficient、delayed research-only、缺 baseline、错误 metric family、未审批 route
  在 adapter 前拒绝。扩展 adapter offset/provenance 测试；修复 PD-02D 测试的 Optional 类型断言。

### 本轮真实质量门（2026-09-04）

```text
.venv/bin/pytest -q                                      518 passed in 23.29s
pytest 聚焦 macro/full-chain/contract                     24 passed
pytest 聚焦 crowding/event-window/macro                    15 passed
.venv/bin/ruff check packages apps tests tools             passed
.venv/bin/pyright packages apps tests                       0 errors / 0 warnings
.venv/bin/python -m tools.contract_codegen check             canonical schemas: ok
.venv/bin/python tools/docs/check_module_docs.py             module docs: ok (13 modules)
git diff --check                                           passed
pnpm --dir apps/decision-desk test                          10 passed
pnpm --dir apps/decision-desk build                         passed（chunk size warning）
pnpm --dir extensions/dsh/decision-hub test                  60 passed
pnpm --dir extensions/dsh/decision-hub build                 passed
```

### 结论

`PD-02E engineering_exit=pass`。本卡没有真实宏观/预期定价供应商、授权 endpoint 或联网 canary，
因此 `live_provider_exit=blocked`，产品仍是 `research_only/provider_blocked`，不能声称
30m/24h/72h 金融事实充分、预测准确或可交易。下一唯一工程入口是 `PD-02F`：只核验
PD-02C..02E 的 Pack/profile/composition/allowlist/readiness 投影，不新增第二套 loop、DTO 或账本。

## PD-02F：Pack/profile/composition closure（completed）

### Scope / 非目标

本卡只把 PD-02C..02E 的 capability、provider route、角色 profile、域名 allowlist、timeout 和
readiness 投影收敛为同一份 Pack 事实源。没有新增 Agent Loop、MCP 工具、Provider DTO、账本或
网络爬虫；真实宏观分钟级和政策定价供应商仍然需要单独授权。

### BDD/TDD 与根因

- Red：Pack route 的外层 timeout 小于 primary + fallback 最坏耗时，primary 超时会取消 Router，
  fallback 没有执行机会；CoinEx v2 depth URL 还缺少 `interval`，market symbol 和响应 shape
  与 fixture 假设不一致。
- Green：将 `market.crypto_crowding` timeout 调整为 18 秒，修正 CoinEx `BTCUSDT`、
  `interval=0.01` 和 `data.depth.bids/asks` 解析；新增 composition timeout invariant 和
  fallback 回放，确保只有 retryable transport 错误才切换 route。

### 质量证据

```text
tests/contracts/test_pd02f_pack_profile_composition.py 通过
tests/research/test_crypto_crowding.py 通过
Router/Gateway/Durable Gateway/FactStore/Semantic Gate 离线全链通过
```

结论：`engineering_exit=pass`。public adapter 的健康证明单独记录在 PD-02G，不把它提升为金融
语义充分或产品可交易结论。

## PD-02G：Provider replay / public canary（completed）

### 目标与安全边界

普通 pytest 保持离线；只在显式 `DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY=1` 下访问已批准
public endpoint。canary 不读取模型/Search secret，不写业务账本，不把 current snapshot 当作
事件窗口事实，也不替代 licensed macro/expectation bake-off。

### 首次失败与根因修复

第一次 public canary 失败并完整保留错误证据：

1. `market.crypto_crowding` 外层 10 秒 timeout 先于 fallback 完成；
2. CoinEx v2 depth 请求缺 `interval` 参数；
3. CoinEx market symbol 使用 `BTC-USDT-SWAP`，应为 `BTCUSDT`；
4. CoinEx 返回 `data.depth.bids/asks`，旧 mapper 按错误 shape 解析；
5. `official.macro` 尚无 typed Facts；FRED 为 delayed，current snapshot 没有 event offsets。

修复后通过 timeout composition 回归、CoinEx mapper 回归和 official event identity typed parser，
没有修改 Gate 去迎合结果。

### 2026-09-05 public canary 结果

命令：

```bash
DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY=1 \
.venv/bin/python -m tools.canary.run_research_fact_capabilities_canary
```

结果：`status=passed`。`official_feed` 返回 1 Evidence/3 Facts（`event.identity`,`t0`）；
`cross_asset` 返回 3 条 FRED delayed facts，最大年龄约 665682 秒；`crypto_spot` 返回 2 条
realtime current facts；`crypto_derivatives` 返回 5 条 realtime current facts；
`crypto_crowding` 返回 2 条 realtime order-book proxy facts，并保留 2 次 provider attempt
（primary timeout，CoinEx fallback 成功）。所有 current market case 的 `event_offsets=[]`。

因此 canary 的语义范围固定为：

```text
semantic_scope=current_snapshot_adapter_health_only
live_provider_blockers=macro.cross_asset_intraday, macro.expectation_pricing
```

结论：`PD-02G engineering_exit=pass`、`public_adapter_canary=pass`；PD-02 产品退出门、
PD-07 前瞻观察和交易可用性仍为 blocked/research_only。

## PD-03：Search source registry and official event facts（completed）

### Scope / 非目标

本卡只实现 DSH native Search locator -> Pack Source Registry -> approved Fetch -> deterministic
official parser -> Evidence/FactEnvelope 的可审计链。DSH 仍是唯一 Harness/Supervisor/Session/Trajectory，
LangGraph 仍只负责生命周期，Hub 仍是唯一账本与 Gate；不新增第二个 Agent Loop、搜索引擎、通用
爬虫、向量库或第二套事实 DTO。

### 实现与测试

- `agentic_research.schema.yaml` 新增 `research_source_policy` / `research_source_registry`，
  通过 canonical codegen 生成 Python/TypeScript 镜像；镜像未手改。
- `packs/crypto_macro/evidence/source_registry.yaml` 固定 P0..P4 来源、domain/path、
  requirement、search/fetch/evidence、license/audit、parser 与 redirect 策略。
- `ResearchSourceRegistry` 对 unknown、未批准、错误 requirement、重复/歧义 URL fail-closed；
  最终 redirect URL 必须再次匹配。
- `OfficialDocumentResearchAdapter` 解析 Federal Reserve RSS/Atom（含 UTF-8 BOM），返回同一
  Evidence 引用的 `event_actor`、`event_time`、`revision_status` 三项 `event.identity` Facts；
  actor/time 缺失不由模型补齐。
- 测试覆盖 registry 准入、未知/越权/redirect、BOM、缺时间 fail-closed、Facts lineage 和
  Source Registry composition；真实 Fed official feed canary 已通过。

### 本轮质量门和结论

本轮重新执行：

```text
.venv/bin/pytest -q                                      533 passed
```

PD-03 的工程和 official feed canary 退出门通过。由于 macro intraday/expectation provider 仍
没有授权 endpoint，产品仍明确为 `research_only/provider_blocked`；Search locator 和官方事件身份
Facts 不能关闭分钟级市场传导或政策预期定价 gap。下一唯一工程入口是 `PD-04`，目标为自动事件到
DSH Run、Inbox/报告、通知和 recheck 的整链，而不是再造一套 Agent Loop。
