# Decision Hub 当前运行与使用引导

版本：2026-09-13 当前核心链路
适用范围：单机、单 owner、手动、research-only

完整的实际页面截图和 2026-09-14 最新回归结果见
[当前可用功能与截图引导](DECISION_HUB_SCREENSHOT_WALKTHROUGH_2026-09-14.md)。

## 当前已经启动的服务

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| DSH Web（主入口） | `http://127.0.0.1:3097/` | 输入研究问题、查看对话、轨迹和报告。根路径需要本次启动时生成的一次性 token；当前页面已在 Codex 浏览器面板打开。 |
| Hub API / Decision Desk | `http://127.0.0.1:8047/` | 查看 Run、Evidence、Coverage、Review、失败原因和服务状态。 |
| Hub readiness | `http://127.0.0.1:8047/health/ready` | 应返回 `{"status":"ready"}`。 |
| Research MCP | `http://127.0.0.1:8037/` | DSH 与 Hub 之间的内部能力入口，不直接在浏览器使用。 |

当前真实服务使用隔离数据目录 `data/core-live-20260913`。本次已停止旧的 8000 端口 Hub 实例，避免前端连到旧数据库。
Hub API 的 `/v1/operations` 可能把自身 runtime 显示为 `fake`：本次模型调用由 DSH Web 的 live runtime 承担，Hub API 负责队列、账本和查询；这不是 DSH 页面切回 Fake Runtime。
当前 DSH Web 使用 OpenAI-compatible 的 `gpt-5.5`，服务地址和密钥只保存在本机 gitignored 的 `data/dsh-live/.env`，不会写入本指南。

## 页面操作

1. 打开当前已经打开的 DSH Web 页面。若页面返回 401，说明一次性 token 过期或 URL 不完整，需要重新启动 DSH Web，并只使用终端新打印的 URL。
2. 在工作区选择 `Crypto Macro Trader`，进入“决策研究”。
3. 在输入框提交一个明确的当前 BTC/USDT 研究问题，例如：

   ```text
   请研究当前 BTC/USDT 的短线和 24h/72h 风险。请取得现货、永续资金费率、未平仓量、基差、订单簿拥挤度和 24h/7d/90d 小时历史；每个事实只引用实际 Evidence，明确 OI 变化、跨交易所和宏观数据缺口，并给出触发条件、失效条件和下一次复查时间。
   ```

4. 点击“建立研究任务”，提交后不要重复点击。系统会显示一个 Run ID，并依次进入“排队”“研究中”和终态。
5. 先看“研究报告”：这里是最终可读结果，包含 hard/soft coverage、Evidence、主因果链、反方链、30m/24h/72h 结论、缺口和复查条件。
6. 再看“轨迹”：核对实际调用了哪些能力、哪些 Evidence 被接受或拒绝、是否发生补查、Review 是否在 synthesis 前完成。
7. 需要更细审计时打开 `http://127.0.0.1:8047/`，按同一个 Run ID 查看 Evidence、Coverage、Review、Trace 和失败来源。

一次研究通常需要约 1–3 分钟。页面可能显示 `neutral` 或 `no_trade`，这表示当前证据不足以承担方向性风险，是保护性业务结论，不是执行失败。

## 当前能验证的内容

- OKX 现货、永续衍生品、资金费率、OI 单点、基差、订单簿拥挤度。
- 免费 OKX 已完成小时的 24h/7d/90d 历史摘要。
- Evidence 的来源、时间、质量、PIT 和引用关系。
- DSH 原生模型轨迹、补查、独立 Review、Gate 和最终 Artifact。
- 历史小时 worker 会在每个完成的 UTC 整点后刷新滚动 90 天窗口。

当前不会承诺跨交易所 OI、冻结历史宏观事件的 USY02Y/DXA 分钟传导、校准预测、自动交易或自动调度。缺少这些事实时，系统必须把缺口写出来并降级。

## Windows 本机重启方式

当前 Docker Desktop 仍不可用，所以本机使用已经验证过的官方 DSH Web 加手动 Hub 进程。正常使用时不需要重启；只有服务退出或 DSH URL 失效时才重启。

先在 PowerShell 进入仓库：

```powershell
Set-Location 'E:\project\study\codex\dsh\decision-hub'
```

推荐直接使用已经验证的一键启动器；它会先重建 Decision Desk，避免旧
`dist` 与当前 API contract 不一致：

```powershell
.\infra\dsh\run-product.ps1
```

以下多终端命令只用于逐进程排障。

在第一个终端启动 Hub API（当前端口 8047）：

```powershell
$env:DECISION_HUB_DATA_DIR = 'E:\project\study\codex\dsh\decision-hub\data\core-live-20260913'
$env:HUB_API_HOST = '127.0.0.1'
$env:HUB_API_PORT = '8047'
$env:DECISION_HUB_DSH_CALLBACK_SECRET = 'core-live-callback'
$env:DECISION_HUB_SOURCES_ENABLED = '1'
$env:DECISION_HUB_MARKET_ENABLED = '1'
uv run python -m apps.hub_api.main
```

在第二个终端启动 Research MCP：

```powershell
$env:DECISION_HUB_DATA_DIR = 'E:\project\study\codex\dsh\decision-hub\data\core-live-20260913'
$env:DECISION_HUB_RESEARCH_MCP_PORT = '8037'
$env:DECISION_HUB_DSH_RESEARCH_TOOL_KEY = 'decision-hub-local-research-tool-key'
$env:DECISION_HUB_RESEARCH_CAPABILITIES = 'official.macro,market.cross_asset,market.crypto_derivatives,market.crypto_crowding,web.fetch,document.readback,data.historical'
$env:DECISION_HUB_RESEARCH_DURABLE_PROGRESS = '1'
uv run python -m apps.research_mcp.main
```

在第三个终端启动 Research Worker：

```powershell
$env:DECISION_HUB_DATA_DIR = 'E:\project\study\codex\dsh\decision-hub\data\core-live-20260913'
$env:DECISION_HUB_RESEARCH_RUNTIME = 'dsh-web'
$env:DECISION_HUB_RESEARCH_EXECUTION_MODE = 'live'
$env:DECISION_HUB_RESEARCH_CAPABILITIES = 'official.macro,market.cross_asset,market.crypto_derivatives,market.crypto_crowding,web.fetch,document.readback,data.historical'
$env:DECISION_HUB_DSH_WEB_URL = 'http://127.0.0.1:3097'
$env:DECISION_HUB_DSH_HOST_SECRET = 'decision-hub-local-host-key'
$env:DECISION_HUB_RESEARCH_POLL_INTERVAL_SECONDS = '1'
$env:DECISION_HUB_RESEARCH_WORKER_ID = 'core-chain-worker-032'
$env:DECISION_HUB_INSTANCE_ID = 'core-chain-worker-032'
uv run python -m apps.hub_worker.main --role research --interval 1
```

在第四个终端启动历史小时采集：

```powershell
$env:DECISION_HUB_DATA_DIR = 'E:\project\study\codex\dsh\decision-hub\data\core-live-20260913'
$env:DECISION_HUB_HISTORICAL_DATASET_ID = 'crypto.btc_usdt.ohlcv_1h'
$env:DECISION_HUB_HISTORICAL_WINDOW_DAYS = '90'
$env:DECISION_HUB_HISTORICAL_SOURCE = 'okx-public'
uv run python -m apps.hub_worker.main --role historical --interval 3600
```

最后在第五个终端启动 DSH Web。模型服务配置仍从 gitignored 的 `data/dsh-live/.env` 读取；不要把 key 复制到命令、文档或截图中：

```powershell
$env:DSH_HOME = 'E:\project\study\codex\dsh\decision-hub\data\dsh-live'
$env:DECISION_HUB_API_URL = 'http://127.0.0.1:8047'
$env:DECISION_HUB_DESK_URL = 'http://127.0.0.1:8047'
$env:DECISION_HUB_DSH_HOST_KEY = 'decision-hub-local-host-key'
$env:DECISION_HUB_DSH_CALLBACK_KEY = 'core-live-callback'
$env:DECISION_HUB_DSH_RESEARCH_TOOL_KEY = 'decision-hub-local-research-tool-key'
$env:DECISION_HUB_RESEARCH_TOOL_URL = 'http://127.0.0.1:8037/decision-hub/v1/research-capabilities/execute'
$env:DECISION_HUB_OWNER_ID = 'dsh-web-owner'
$env:DECISION_HUB_DSH_WORKSPACE_CWD = 'E:\project\study\codex\dsh\decision-hub\data\dsh-live\Crypto Macro Trader'
$env:DECISION_HUB_DSH_RUNTIME_MODE = 'live'
$env:DECISION_HUB_DSH_SOURCE_VERSION = '0.1.2-alpha.2'
$env:DECISION_HUB_DSH_PLUGIN_BUILD_HASH = 'b104cc32604739bfb876218bc981d1f5b621fd4dcb2ae3e905490e35935f5d89'
node .cache/dsh-upstream/source/apps/cli/lib/bin.js --profile web --patch extensions/dsh/decision-hub/cordis.patch.yml --host 127.0.0.1 --port 3097 --no-open
```

启动成功后终端会打印 `dsh web: http://127.0.0.1:3097/?token=...`。只使用这一次启动打印的完整地址；不要把 token 写入文档或提交到仓库。

## 常见状态

| 状态 | 解释 |
| --- | --- |
| `研究中` | Worker 正在取证、补查或等待 DSH 结果。 |
| `可发布` | Evidence、PIT、Review 和 Gate 满足当前领域规则；仍只是研究意见。 |
| `仅研究` | 证据或数据不完整，报告保留事实和缺口，不给出未经证明的方向。 |
| `neutral` / `no_trade` | 风险控制结论，不代表市场确定上涨或下跌。 |
| `研究拒绝` | 输入、权限、来源或契约不满足准入。 |
| `本轮运行失败` | Provider、DSH、MCP、网络或结构化输出失败；先看失败来源和 `retryable`。 |

不要在 DSH、Hub API 和 Decision Desk 之间混用 Run ID；三处必须使用同一个 Run ID。不要手工修改数据库，也不要用旧端口 8000 的页面判断当前运行结果。
