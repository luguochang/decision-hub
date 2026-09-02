# E2L-03 前端真实链路、时间边界与证据血缘收口

版本：`E2L-03-2026-09-01.v1`
状态：`offline quality gates passed / live acceptance executed but capability-blocked`
目标：从官方 DSH Web 发起一次真实研究，并在 Decision Desk 中得到可信、可解释、可截图复核的终态；成功和失败都不能被伪装。
上级章程：[通用底座与首个产品最终实施章程](../product/PRODUCT_PLATFORM_FINAL_EXECUTION_CHARTER_2026-09-01.md)
关联阶段：[G1/G2 研究可靠性与事实覆盖实施方案](R2_R_G1_G2_EXECUTION_PLAN.md)
关联决策：[ADR-0011 研究失败语义与事实覆盖边界](../decisions/ADR-0011-research-reliability-fact-boundary.md)

> 本文是本轮实现与验收的唯一任务卡。它不新增 Agent Loop、Provider 协议、账本、
> 前端框架或网络权限；只修复正式 DSH -> MCP -> Hub -> Decision Desk 链路已经暴露的
> 边界错误，并把结果做成人可以判断的产品状态。

## 1. 用户价值与完成定义

用户不是来验证某个 API 返回了 JSON，而是要看系统能否主动补证、正确停止并说明：

1. 找到了哪些官方/市场事实；
2. 哪些事实新鲜、哪些只是背景或已经过期；
3. Agent 实际调用了哪些能力、成功或失败在哪里；
4. 根因链和 30m/24h/72h 为什么可以或不可以形成；
5. 失败时已有 Evidence 是否仍被保留，下一步是补事实、重试 transport，还是修协议；
6. 任何方向性结论是否确实通过 Evidence lineage 和代码 Gate。

本卡完成必须同时满足：

- 官方 DSH Web 能建立 durable Research Run 并显示真实终态；
- Decision Desk 能显示同一 Run 的运行轨迹、Evidence、coverage、failure provenance 和报告；
- 页面无 demo fallback、无 raw Provider/DSH JSON 倾倒、无“后台已失败但仍显示研究中”；
- 至少一张桌面截图和一张窄屏截图可直接用于复核；
- 未通过 attestation 或 Sufficiency 的结果不产生 Artifact/Forecast/方向性发布。

产品视图通过官方 `conversation.view` 增加独立“研究报告”页签，完整报告不再占用输入区；
官方 Chat/Trajectory 与 JSONL 保留为审计面。固定上游当前没有第三方默认视图 API，所以
首次打开仍落在 Chat；这是明确的上游产品限制，不通过替换 `chat` renderer 或 DOM hack
规避。

## 2. 2026-09-01 真实验收事实

隔离实例：Hub API `8041`、Research MCP `8042`、官方 DSH Web `51980`，Compose
项目 `dh-smoke-20260901`。认证 token、cookie 和 API key 不写入文档。

### 2.1 已修复的基础设施根因

首次正式链路中，`research-mcp` 未挂载 `decision-hub-data`，因此它看不到 Hub API
和 research worker 写入的 `dsh_session_links`。所有 capability 都错误失败为
`research_session_not_found`，上层再错误收敛为 `critical_data_unavailable`。

已在 `compose.yaml` 为 `research-mcp` 增加相同命名卷，并增加 Compose 静态回归测试。
修复后，正式 DSH Session 已完成 9 次 capability 调用并持久化 15 条 Evidence，证明
DSH、MCP、Official/Market adapter 和 durable ledger 已经真实连通。

### 2.2 当前阻塞：模型改写 Evidence ID

真实 Run：`run_76c6463688354ebfb419f73755d05761`。

MCP 返回并持久化的第三条 `expectation_pricing` Evidence ID 是：

```text
ev_db373fdf422d1fc7646418a5bec11a15
```

DSH 最终 synthesis 却引用了：

```text
ev_db373fdf422d1fc7641ab2b592e2e1fb29a9ed44fd0182e9c6740f8dd2dfe14
```

后者不是任何输入或成功 MCP Tool Result 中的 Evidence。`result_mapper` 因此返回
`dsh_evidence_unattested`。这是正确的 fail-closed，不得通过截断、模糊匹配、自动
替换或信任模型引用来让 Run 变绿。

### 2.3 当前阻塞：live PIT 上限被误用为新鲜度评估时刻

`ResearchRequestFactory` 当前在 live 模式把：

```text
pit_cutoff_at = worker_started_at + total_deadline_seconds
```

这本来是“本 Run 最晚允许接收事实的执行上限”，但同一个未来时间又被 MCP query 和
`ResearchEvidenceService.accept_candidates()` 当成 freshness cutoff。结果是刚从 CoinEx
收到的 BTC spot/derivatives Evidence，因为相对未来 deadline 已相差数分钟，被立即
标记为 `stale`。

正确边界是：

- `deadline_at`：Run/模型调用最晚结束时间；
- `query.cutoff_at`：本次 capability 调用的可信服务端接收时刻，且不得晚于 deadline；
- `decision_cutoff_at`：每轮最后一条已接收 Evidence/结果的可信时刻；
- freshness 必须相对本次可信 capability/decision cutoff 计算，不能相对未来 deadline；
- Replay 继续使用 fixture 固定 cutoff，不读取当前时间。

模型提交的 `observed_at` 和 `cutoff_at` 只用于关联/诊断，live Gateway 必须根据 linked
Run、当前服务端时间和 durable deadline 生成 effective cutoff，不能让模型延长窗口。

### 2.4 当前阻塞：失败 Run 投影与 durable 事实矛盾

同一 Run 在 Decision Desk 中当前显示：

```text
runtime=pending / Round 0 / 0 tools / coverage not assessed / no open gaps
```

但 durable ledger 已存在：

- DSH Session link；
- 21 条 normalized trace；
- 9 次成功 capability 调用；
- 15 条 Evidence；
- 初始 coverage 为 0% 且有 8 个 gap；
- terminal failure 为 `dsh_evidence_unattested`。

根因不是前端缺一个标签，而是 Query View 在没有最终 `ResearchSessionResult` 时没有从
`dsh_session_links`、`research_trace_events` 和 `research_evidence` 重建可信失败投影。
修复必须发生在 Query/View 层，前端不能直读 SQL 或 DSH JSONL。

### 2.5 当前页面可读性问题

- 官方 Fed HTML/XML excerpt 和完整 RSS 被直接铺在 Evidence 卡片中，无法扫描；
- 结构化 market excerpt 直接显示压缩 JSON，没有字段标签；
- `pending / Round 0 / 0 tools` 会让用户误判 Agent 没有工作；
- `coverage not assessed` 后又显示“没有未关闭 gap”，语义冲突；
- attestation 失败没有显示“15 条 Evidence 已保留、synthesis 未通过血缘认证”；
- 官方 DSH Web 会话选择与 Host API 单独创建的 Session 可能不同，页面不能用旧 Session
  的“研究中”证明新 Run 已完成。

## 3. 架构边界

```text
官方 DSH Web
  -> Decision Hub Client/Host Plugin（输入与业务状态槽）
  -> Hub durable Run / DSH Session link
  -> DSH 内层 model-tool loop
  -> Research MCP / Capability Gateway
  -> Official / Market / Search adapters
  -> durable Evidence + normalized Trace
  -> Result attestation + deterministic Sufficiency/Gate
  -> Query View
  -> DSH 报告槽 + Decision Desk 管理视图
```

所有权保持不变：

| 事实/能力 | Owner |
|---|---|
| 会话、模型/工具 loop、JSONL trajectory | DSH |
| Run、Evidence、PIT、Coverage、Gate、Artifact、Forecast | Hub |
| 外层 evidence round/checkpoint/recovery | LangGraph |
| 领域 requirement、freshness、来源阶梯 | `crypto_macro` Domain Pack |
| 人可读业务投影 | Query View + DSH Client Plugin / Decision Desk |

禁止：新增第二套 Agent Loop；让 DSH 写业务账本；让前端自行推导 Gate；放宽 Evidence ID
认证；自动交易；切换 active pointer；为本卡增加 ASR、PPT、第二领域或多用户。

## 4. 实现任务

### E2L-03-A：live effective cutoff

目标：把执行 deadline 与事实 cutoff/freshness 分离，同时保持 live 请求不能越过有界
Run deadline。

实现原则：

1. Gateway 从 linked Run/DSH prompt 的 durable request 读取允许的 `deadline_at`；
2. live query 的 effective cutoff 使用服务端当前时刻，且必须 `<= deadline_at`；
3. adapter 只看到 effective query；Evidence 验证和持久化使用相同 effective cutoff；
4. replay query 保持 fixture cutoff；
5. 不接受模型把 cutoff 向后延长，也不把 Provider 时间当服务端 received time。

实现状态：`done (offline)`。`dsh_session_links.deadline_at` 已由 migration
`0026_dsh_session_deadline` 持久化；历史缺失 deadline 的 link 不猜测时间，live Gateway
安全返回 `research_deadline_unavailable`。模型 cutoff 会被截断到 durable deadline，freshness
仍相对可信 capability completion/decision cutoff 计算。

BDD：在 8 分钟 Run deadline 下，1 秒前收到的 BTC tick 必须 fresh；超过 deadline 的调用
必须 `research_pit_violation`；FRED 三天前的日线仍应按 requirement freshness 判 stale。

### E2L-03-B：attestation 差异诊断

目标：保留 fail-closed，同时让错误说明具体是哪个 synthesis 引用无法认证。

实现原则：

- 错误不包含 raw Tool Result 或 secret；
- durable provenance 至少包含稳定 `cause_code`；
- 可增加有界、脱敏的 missing ref count/short id 业务投影，但不把模型错误引用写成 Evidence；
- 正确 MCP ID 必须精确匹配；unknown/conflicting ID 继续失败。

BDD：正确引用通过；未知引用失败；一个正确 ID 被模型改写时，15 条已持久化 Evidence 不
丢失，Run 明确显示失败发生于 `synthesis_attestation`。

实现状态：`done (offline)`。Web Runtime 在有可信 Evidence 时将
`dsh_evidence_unattested`/`structured_output_invalid` 映射为 `degraded` evidence-only
结果；整个不可信 causal case/horizons 被丢弃，Trace 写入 synthesis failure provenance，
不发送额外 repair turn，也不占用下一 generation。无可信 Evidence 仍 fail-closed 抛错。

### E2L-03-C：失败 Run durable projection

目标：没有最终 Result 时，Query View 仍从 Hub 自有事实生成一致的失败详情。

投影规则：

- runtime 优先从 Result；否则从 DSH Session link/trace 推断 `dsh`，不能显示 `pending`；
- current round 从 Evidence round、Session generation、trace round reference 的可信最大值取值；
- tool count 从 terminal capability trace 去重；不能因 synthesis 失败归零；
- evidence 继续读取 ledger；
- coverage 至少投影最后一次 durable assessment；若只有开始事件则保留“8 gaps / not
  completed”，不能显示“no gaps”；
- failure 明确 `origin=orchestration`、`cause=synthesis_attestation`；
- 不凭 trace 摘要重建一个可发布的 CausalCase/Horizon。

实现状态：`done (offline quality gate passed)`。失败投影现在保留已经落盘的 Evidence、tool
count、round、coverage 和逐 capability ErrorProvenance；没有 Result 时不得把 runtime 显示
为 pending，也不得生成方向性 Forecast。2026-09-01 对真实失败 Run 的只读数据库副本复验
结果为：`dsh / Round 1 / 10 tools / 47 Evidence / critical_data_unavailable`，剩余 hard
gaps 为 `expectation_pricing`、`macro_transmission`，failure 为
`dsh_evidence_unattested / synthesis_attestation`，causal/horizon/decision snapshot 均为空。

### E2L-03-D：前端可读性

目标：让失败和证据对 Owner 可操作，而不是倾倒数据。

- 在 failure 区显示“synthesis 未通过 Evidence 血缘认证”和“已保留 N 条 Evidence”；
- `round/tool/evidence` 统计使用 Query View canonical 字段；
- coverage 未完成时显示“评估未完成/最后已知 gap”，不显示成功空态；
- market JSON 使用少量键值摘要；官方 HTML/XML 清理导航噪声并截断，来源链接保留；
- 原始 ID 只在诊断细节中使用短形式；不显示 DSH raw JSONL；
- 桌面和窄屏均无横向溢出、卡片套卡片或文字覆盖。

实现状态：`done (offline; browser evidence pending for current revision)`。`run_id=null` 的
正常空态不再显示“研究生成中”；终态 detail 暂不可用时显示保守终止投影和失败 provenance。

### E2L-03-E：真实前端复验

从独立端口和独立 Compose project 启动，流程必须是：

```text
run-product.sh
  -> 打开其输出的官方 DSH Web URL
  -> 新建/选择当前 DSH Session
  -> 页面“建立研究任务”提交真实文本
  -> 页面轮询到 terminal
  -> 打开同一 Hub Run 的 Decision Desk
  -> 核对 Evidence / gaps / trace / failure or report
  -> 截取桌面与窄屏
```

不得使用旧端口、旧 bundle、旧 schema 或直接 curl 创建的另一 Session 代替本流程。

实现状态：`blocked / live evidence recorded`。新隔离实例已经从官方 DSH Web 完成真实
提交、正式重试和有界补证循环，证明 DSH 会继续调用 capability 并保留成功/失败事实；
但 `web.search` 仍有 4/8 次 timeout，当前 live allowlist 没有
`market.crypto_derivatives`，因此没有充分度、报告或方向性 Forecast。第二次模型步
timeout 和首次配置错误也作为独立失败 Run 保留。当前窄屏/桌面 DOM 验证无横向溢出，但
截图未作为长期文件保存，不能宣称完整 screenshot/hash 退出门。

真实 Run 与细节见
[产品收口执行记录第 13 节](../evaluations/PRODUCT_CLOSEOUT_EXECUTION_2026-09-01.md)。
E2L-E 仍未通过；只有在新的 capability/owner Gate 下取得稳定 Search 与市场事实后，
才可重跑同一官方 Web -> MCP -> Hub -> Desk 流程。

## 5. TDD 与质量门

### 5.1 后端 Red/Green

- live model-supplied future cutoff 不能使 fresh market Evidence 变 stale；
- Gateway effective cutoff 不得越过 durable deadline；
- replay cutoff 不变；
- synthesis 引用正确/未知/冲突 ID；
- failure 后 Evidence/Trace 保留；
- 无 Result 的 DSH failure 正确投影 runtime、round、tool count、evidence count 和 gap 状态；
- API list/detail schema 均为当前版本。

### 5.2 前端 Red/Green

- `dsh_evidence_unattested` 显示 synthesis attestation，而不是普通 Provider failure；
- 15 条 Evidence 已保留的状态可见；
- `pending / R0 / 0 tools / no gaps` 的矛盾 fixture 必须失败；
- JSON/XML Evidence 变为可扫描摘要；
- API 版本不匹配时继续显示不可用，不回退 demo。

### 5.3 完整命令

```text
git diff --check
python -m tools.docs.check_module_docs
python -m tools.contract_codegen check
pytest -m "not live"
ruff check .
pyright
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
pnpm --dir extensions/decision-hub-dsh-plugin test
pnpm --dir extensions/decision-hub-dsh-plugin build
docker compose config
```

真实 live 只运行一次有界验收；失败也是结果，必须保留日志、run id、错误和截图。

## 6. 完成门与停止门

工程完成门：上述专项和全量质量门通过。
产品完成门：官方 DSH Web 与 Decision Desk 都能展示同一真实 Run 的可信终态。
价值门：至少一个真实 live 事件形成可读 Evidence/缺口/根因链报告，并由 Owner 判断有用；
本卡不声称预测收益或 Promotion。

以下任一情况必须停止并回到架构/Owner，而不是继续打补丁：

- 需要放宽 Evidence exact-match；
- 需要新增第二账本或让前端/DSH 直接写 Gate；
- 需要扩大网络域、费用、插件权限或切 active pointer；
- 同一时间/血缘问题连续两次最小修复仍无法满足测试；
- 真实来源无法满足 requirement freshness，需要更换来源或调整领域需求定义。

## 7. 当前结论

系统已经证明“DSH 会主动调用多个能力并持久化 Evidence”，但尚未证明“正式产品链路
可正确完成并产生可用报告”。当前 `pilot_ready=false`、`pilot_usable=false`，Fixed
保持 active，DSH 保持 candidate/shadow。E2L-03 完成后仍需进入前瞻观察，而不是自动
进入 ASR/PPT/第二领域。
