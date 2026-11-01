# Decision Hub 产品启动恢复与主链路自测

日期：2026-09-15  
目标：只恢复产品核心启动链，并验证一次“输入 → 入队 → Worker → DSH → Evidence/Gate → 输出”的真实路径。不扩展外围功能，不以端口监听代替主链路验收。

## 结论

- 初始故障确实是 Docker Desktop backend 在 Inference manager 清理残留 `dockerInference` socket 时崩溃，表现为 Linux engine pipe 不可访问；已通过保留数据的运行时目录改名方式恢复。当前 `docker version` client/server 均为 `29.6.1`，Compose 路径已重新可用。
- 宿主机备用路径已恢复。当前 `infra/dsh/run-product.ps1` 可在 Windows PowerShell 下启动 Hub API、Research MCP、DSH Web、Research Worker、Historical Worker，并把受管根进程写入 `data/product-local/processes.json`。
- 当前核心端口和 readiness 已通过：Hub API `8047/health/ready=200`、DSH `3097/decision-hub/v1/readiness=200`、Research MCP `8037` TCP 可连接。
- Docker 修复后第一条复测 Run `run_849e90bc54b54b0faca1d817b20cbb76` 暴露了旧进程和事件分类问题；修复并重启后的 Run `run_bd5f6a601ece403c87842e7aa3928cd2` 已按 `crypto_market_snapshot` 契约完成真实主链，hard coverage 100%、15 条新鲜 Evidence、独立 Review accepted，Artifact 已保存。它仍然只输出等待确认的研究结论，`trade_authorized=false`，没有把报告发布误当成交易授权。

## 关键原因与修复后的复测（22:20—22:24）

这次异常的首因不是“免费数据拿不到”，而是手工输入没有携带 `event_hint`。旧逻辑把 BTC 当前快照归成 `macro_event`，ResearchRequestFactory 随即要求事件身份、政策变化、预期定价等宏观硬字段，已有交易所事实被错误地计为缺口。第二个问题是 Gate 把“完整研究得出的 no_trade”也降级成 `research_only`，导致报告看起来像失败。

已做的核心修复：

1. `packages/kernel/decision_hub_kernel/application/admission.py` 增加可解释的 BTC 快照识别；显式 `event_hint` 仍然优先，避免误分类。
2. `packages/kernel/decision_hub_kernel/decision/gate.py` 和 `application/commit.py` 分离“报告可发布”和“交易可授权”：只有确定性研究结果已完成、hard coverage=100%、独立 Review accepted 时，完整的 `no_trade` 报告才进入 `publish`；`trade_authorized` 永远为 `false`。
3. DSH 原生复核只允许引用触发快照和本轮结果 Evidence；不可信 receipt 被拒绝后继续走确定性的 bounded review，不再让 worker 直接失败。

最新真实 Run：`run_bd5f6a601ece403c87842e7aa3928cd2`  / Artifact `art_43904843edcb4c9782cde2556944ab25`。

实际结果：

- `status=completed`、`gate_status=publish`、`artifact_status=saved`、`failure=null`；
- hard coverage `1.0`，已覆盖双交易所现货、衍生品拥挤度和小时历史；
- 新鲜 Evidence `15` 条，包含 OKX/Binance 现货、funding、OI/OI history、basis、订单簿和 24h/7d/90d 小时窗口；
- `independent_reviewer=accepted`，形成主链、反方链和 30m/24h/72h 条件；
- 行动摘要为 `wait_for_confirmation`，原因是实时宏观传导与跨资产数据仍是软缺口且概率未校准；这两个缺口 `blocks_directional_output=false`，不会再把整份研究报告当成失败；
- `trade_authorized=false` 仍保持，当前产品输出是“已完成的可审计研究 + 等待确认”，不是自动交易信号。

## 初次复测记录（已定位为旧逻辑问题，21:24—21:27）

提交新的 BTC/USDT 可审计快照问题后，主链实际经过：

```text
202 Accepted → research worker claim → DSH live session
→ 9 次能力调用 → 18 条 Evidence → round 2 补证
→ Artifact → deterministic Gate → research_only / do_not_trade
```

Run：`run_849e90bc54b54b0faca1d817b20cbb76`  
Artifact：`art_4ec9077f041442dfa2bcdc78fb97a31d`

验证到的业务输出：

- `failure=null`，没有再次出现 `research_worker_failed`；
- `status=research_only`、`stage=done`、`latest_sequence_no=113`；
- `accepted_evidence=15`，`stale_evidence=3`；
- 前端可直接展示 headline、5 条事实、主/反向链、30m/24h/72h 三个 `no_trade` 情景和来源引用；
- `trade_authorized=false`，`recommendation=do_not_trade`；
- 仍缺少事件身份、事件窗口变化、实时宏观/跨资产和可比较的衍生品基线，因此系统没有越过交易 Gate。

这条记录保留用于解释为什么不能只看一次旧 Run 下结论：它在旧分类逻辑和旧进程状态下把 BTC 快照当成宏观事件，故障表现为证据不足。分类修复后的最新复测见上文，已取得 hard coverage 100% 并保存完整报告。

## 按顺序执行的工作

### 1. 启动依赖检查（初始故障记录）

检查结果：

```text
Docker Desktop process: running
Docker Server: timeout_or_unavailable（修复前）
```

没有执行 Docker factory reset，也没有修改 Docker 数据目录。初始验证阶段先按宿主机备用路径继续；随后按 `DOCKER_STARTUP_RECOVERY_2026-09-15.md` 完成 Docker runtime socket 修复并重新验证 daemon。

### 2. 修复 Windows 启动器

文件：`infra/dsh/run-product.ps1`

修复内容：

1. 不再依赖当前 PowerShell 是否支持 `Start-Process -Environment`。启动子进程前临时注入本次子进程环境，使用 `Start-Process` 的原生 stdout/stderr 重定向，启动后立即恢复父进程环境。
2. 每次启动前清空受管日志并删除旧的 `processes.json`，避免旧 DSH URL 让新启动误判成功。
3. 每个进程启动后检查是否立即退出；研究 Worker 和历史 Worker 都启动后再次检查存活。
4. 只有五个核心进程都成功启动后才写入新的进程状态文件。

### 3. 修复主链结果投影边界

文件：`apps/hub_worker/research.py`

真实运行发现 DSH 返回的原生独立复核 receipt 的 `result_hash` 与最终结果不一致。原代码把这个 advisory receipt 校验错误直接转换为 `research_worker_failed`，导致 Evidence 已经齐全、DSH 已经完成，却没有 Artifact。

现在的处理是：

- 原生复核仍然严格校验，hash、目标声明等不一致时不会被接受。
- 只拒绝这条不可信的原生复核，并基于已验证的研究结果生成确定性的 bounded review。
- 继续走 Commit/Gate；证据不足仍只能生成 `research_only/no_trade`，不会放行方向性交易结论。

这个修复没有放宽任何数据 Gate，只避免“复核 receipt 坏了”掩盖已经取得的 Evidence。

## 真实主链路证据

输入是一条固定的 BTC/USDT 可审计快照问题，要求现货、衍生品、订单簿、两个交易所和 24h/7d/90d 小时历史。

Run：`run_8f4cc4e62298402d9e37456f436b529b`  
Artifact：`art_5333800b3e52445a92d3f0389652bba3`

实际取得并持久化的证据包括：

- OKX 与 Binance 现货价格/成交量；
- OKX funding、open interest、基差和订单簿深度代理；
- FRED 利率/美元序列（但相对事件截止点过旧）；
- 本地小时历史归档，24h、7d、90d 窗口均有 coverage manifest；
- DSH 研究回合、工具失败原因、独立复核和 synthesis trace。

最终 Gate 结果：

```text
status             research_only
recommendation     do_not_trade
trade_authorized   false
accepted_evidence  13
hard coverage      50%
soft coverage      33%
```

仍然阻断方向性输出的缺口：

- Binance 永续 OI 变化和第二交易所的完整衍生品拥挤确认；
- 新鲜的宏观 rates/USD transmission 与 event return；
- 独立的跨资产确认。

因此当前结果是“有证据的安全停止”，不是“可交易结论”。

## 自测结果

启动后检查：

```text
GET http://127.0.0.1:8047/health/ready                       200
GET http://127.0.0.1:3097/decision-hub/v1/readiness          200
TCP 127.0.0.1:8037                                           connected
```

针对结果投影和原生复核边界：

```text
uv run pytest -q tests/evolution/test_research_worker.py
20 passed
```

本轮分类、Gate 和 Review 回归：

```text
uv run pytest -q tests/kernel/test_core_flow.py \
  tests/research/test_market_snapshot_admission.py \
  tests/research/test_context_and_review.py \
  tests/evolution/test_research_worker.py \
  tests/research/test_research_api.py
36 passed
```

并用最新 live Run `run_bd5f6a601ece403c87842e7aa3928cd2` 完成一次端到端复测，确认启动器、API、MCP、DSH、Worker、Evidence、Review、Commit 和前端投影使用的是同一份新代码。

启动器契约检查：

```text
uv run pytest -q tests/dsh_native/test_product_launcher.py \
  -k "product_launcher or requires_the_current_product_api_contract or research_worker_starts_only_after_dsh_host_readiness_barrier"
6 passed, 2 skipped
```

## 当前使用方式

启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\dsh\run-product.ps1
```

查看 DSH Web：从 `data/product-local/logs/dsh-web.stdout.log` 读取本次生成的带 token URL；不要把 token 写进工单或日志截图。

停止：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\dsh\stop-product.ps1
```

当前状态文件只管理本次启动的五个根进程，数据和日志会保留。

## 未完成与约束

- Docker engine 已恢复，`docker version` client/server 均为 `29.6.1`；本轮只验证了 daemon 与业务主链，尚未把 Decision Hub 五个宿主机进程切换成 compose 编排。切换 compose 需单独做一次等价的主链验收，不能把端口监听当成完成。
- 交易授权仍保持关闭。完整研究报告可以显示为 `publish` 并交付事实、推理和条件；没有概率校准或新鲜宏观/跨资产确认时，行动摘要必须保持 `wait_for_confirmation/do_not_trade`，不得自动产生仓位或交易指令。
- 本次真实 Run 在修复前触发了原生复核 hash 不一致，之后使用同一 immutable DSH 结果完成了确定性 re-projection；修复后的边界由 20 个 Worker 单测覆盖。下一次新 Run 应直接走 bounded review 分支，不再因为该校验错误丢弃 Artifact。

本文件是本轮执行记录。后续工作若不直接提高“启动可用性、证据完整性或结果可审计性”，不得加入本目标。
