# DSH 官方上游运行边界

本目录只负责固定、校验、构建和启动官方 DeepSeek Harness Web。业务代码不得复制官方 Web、Session、Trajectory 或插件安装逻辑，也不得直接修改缓存中的上游源码。

权威约束见：

- `docs/stages/DSH_NATIVE_WEB_PRODUCT_CORE.md`
- `docs/decisions/ADR-0013-dsh-web-native-plugin-upstream-integration.md`
- `infra/dsh/upstream.lock.json`

## 命令

```bash
./infra/dsh/fetch-upstream.sh
./infra/dsh/build-upstream.sh
./infra/dsh/run-web.sh --port 3080
./infra/dsh/run-live-web.sh --port 3080
./infra/dsh/run-product.sh
./infra/dsh/stop-product.sh
./infra/dsh/web-smoke.sh
./infra/dsh/acceptance.sh
```

`run-product.sh` 是产品启动入口：它先用 `--build --force-recreate` 从当前工作树构建并启动
Hub API、realtime/evolution worker 和 research MCP，然后在宿主机启动官方 DSH Web。只有
包含 `hub_reachable` 与 build identity 的 Host readiness 通过后，启动器才单独构建并启动
research worker。事件可以提前进入 durable 队列，但不会在 Agent Harness 尚未双向就绪时被消费。
启动器最后只输出一个用户入口。通用 `/health/ready` 通过后还必须验证 canonical
`research-inbox-view.v1`；旧镜像即使健康也不能冒充当前产品版本。
`stop-product.sh` 只停止同一 Compose 项目的服务，保留 SQLite、DSH Session 和
通知 outbox 数据卷。

启动器按 `DECISION_HUB_API_PORT` 生成稳定的 Compose 项目名
`decision-hub-product-<port>`，并在每次启动时用当前环境强制重建五个服务；research worker
位于 DSH readiness 消费屏障之后。这样不会把
旧的 DSH Host URL、旧 Provider 或旧 capability allowlist 留在容器里。启动日志中的
`DSH_URL` 是唯一用户入口；旧端口或不带 `?token=...` 的裸 URL 不属于当前产品实例。
需要并行隔离实例时显式设置 `DECISION_HUB_COMPOSE_PROJECT`，并同时设置独立的 API、MCP
和 DSH 端口，禁止复用另一个实例的项目名。

产品 live 启动默认启用已经通过独立 contract/replay/live canary 的只读 capability：
`official.macro`、`market.cross_asset`、`market.crypto_derivatives`、`web.fetch` 和
`web.search`（DSH native discovery）；明确拒绝 `replay.research`。`web.search.tavily`
仍只在显式加入 allowlist 且配置新 secret 后作为按需 fallback 启用。失败仍按稳定错误
进入 provenance，不能替代 typed source。回放只能通过隔离验收命令启动。若存在 gitignored 的
`data/dsh-live/.env`，启动器通过 Compose 官方 `--env-file` 参数把同一份 Provider
配置注入 Research MCP，不执行该文件、不打印密钥。任何新增 capability 仍须逐项通过
contract/replay/live canary 后才能加入 `DECISION_HUB_RESEARCH_CAPABILITIES`。

启动器会在 DSH Web 发布认证 URL 后，通过官方 `workspace/create` RPC 注册默认的
`data/dsh-live/Crypto Macro Trader` 工作区（可用 `DECISION_HUB_DSH_WORKSPACE_CWD`
覆盖）。因此用户进入 DSH Web 后看到的是产品工作区，而不是临时目录名；工作区和
Session 导航仍完全由 DSH 官方 Workspace/Session Controller 所有。

## 用户操作闭环

1. 执行 `./infra/dsh/run-product.sh`，保持该终端进程运行。
2. 打开日志打印的完整 `DSH_URL`（包含 `?token=...`）；不要打开旧端口或裸端口。
3. 进入 `Crypto Macro Trader` 后点击 DSH 原生的 `新建会话`。
4. 在新会话的输入框写研究目标；此时插件显示 `建立研究任务`，点击一次提交。
5. 等待通常 1--3 分钟，在同一页面查看 `对话`、`轨迹` 和 `研究报告`。

已关联正式 Run 的旧会话中，输入框是 DSH 普通续聊；它不会把每条聊天自动创建成新的 Hub
Run。要开始新的业务研究，回到 `新建会话`；要对失败 Run 进行幂等补跑，使用报告页的
`重新研究`。报告显示 `拒绝/证据不足/no_trade` 时表示代码 Gate 按事实缺口安全停止，
不是页面没有执行。

`research-mcp` 同时发布到宿主机 loopback 的 `${DECISION_HUB_RESEARCH_MCP_PORT:-8002}`。
启动器将宿主机 DSH 使用的 `DECISION_HUB_RESEARCH_MCP_URL` 设置为
`http://127.0.0.1:<port>/mcp`；Compose 内部 research worker 仍使用
`http://research-mcp:8002/mcp`。这样宿主机 DSH 和容器 worker 访问的是同一个受控
MCP，而不会误连各自容器内的 `127.0.0.1`。

若本机已有旧服务占用 8000，可用 `DECISION_HUB_API_PORT=8030
DSH_PRODUCT_PORT=3081 ./infra/dsh/run-product.sh` 启动隔离实例；启动器会把同一
API 地址传给 DSH Host callback 和 Decision Desk 链接，不会误连旧进程。

宿主机运行 DSH Web，research worker 运行在 Compose 容器。因此启动器同时设置宿主
机 loopback URL 和容器访问宿主机的 `host.docker.internal` URL。不要把容器里的
`DECISION_HUB_DSH_WEB_URL` 改成 `127.0.0.1`，否则 worker 会连接自身而非 DSH Host。

`run-web.sh` 会根据 `DECISION_HUB_DSH_RUNTIME_MODE` 或 replay 环境标记输出
`live`/`replay` 运行模式。`run-live-web.sh` 是可交互入口：它拒绝
`DSH_REPLAY_PLUGIN`、快照和 override，先检查 `DEEPSEEK_API_KEY`（或兼容网关的
`OPENAI_API_KEY`），再启动不带 replay patch 的官方 Web。凭据可通过当前进程环境
注入；本机个人试用也可保存在 gitignored 的 `data/dsh-live/.env`（权限 `600`），
启动器只检查其存在，DSH 在自己的 `DSH_HOME` 配置边界内读取。没有凭据时会以退出码
`78` 在启动前失败，而不是让用户进入一个必然失败的页面。该文件绝不能提交、复制到
日志或写入业务数据库；多人/生产部署应改用操作系统 Secret Manager。

验收脚本输出的完整 `BROWSER_URL` 必须用于首次打开。裸 `/` 没有 DSH 本地认证
cookie 时按官方设计返回 401；不要关闭这个信任围栏，也不要把 token 写进配置或文档。

`run-web.sh` also installs the repository-controlled `decision-research` user
preset into the isolated `DSH_HOME/.agent-presets` directory. The preset uses
the official `@deepseek-ai/dsh-agent-presets` roster and
`@deepseek-ai/dsh-mcp-client`; it does not replace the Web/base composition or
introduce a second Agent Loop. The MCP URL is configured with
`DECISION_HUB_RESEARCH_MCP_URL` and defaults to the local audited gateway.

When the Hub Host plugin dispatches Sessions from a worker, set
`DECISION_HUB_DSH_WORKSPACE_CWD` to an existing directory registered through
the official DSH `workspace/create` RPC. The launcher changes its process cwd
to the pinned upstream checkout, so leaving this unset would create bridge
Sessions in the source checkout and fail the Workspace ownership check.
The Hub API must use the same callback secret as the Web process:
`DECISION_HUB_DSH_CALLBACK_SECRET` on the API and
`DECISION_HUB_DSH_CALLBACK_KEY` for the Web plugin.

默认目录：

```text
.cache/dsh-upstream/archive.tar.gz   下载归档
.cache/dsh-upstream/source/          校验后的官方源码
data/dsh-web/                        隔离 DSH_HOME / Session JSONL
data/dsh-live/                       本机 live DSH_HOME、Provider 设置和 Session JSONL（Git ignored）
```

可通过 `DSH_UPSTREAM_CACHE` 和 `DSH_WEB_HOME` 覆盖本机目录。缓存和运行数据均被 Git 忽略；仓库只提交 lock、overlay、插件和验收代码。

## 边界

1. `fetch-upstream.sh` 必须先验证 SHA-256，才允许把归档发布为 source。
2. `verify-upstream.mjs` 验证版本、公开 Session Controller、WebServer route、`dsh.bundle` 和 `dsh.client` seam。
3. `build-upstream.sh` 使用上游锁定的 pnpm 版本和官方 `build:official`；不打私有补丁。
4. `run-web.sh` 只监听 loopback，不绕过官方对 `0.0.0.0` 的限制。
5. `acceptance.sh` 默认执行 source/build 和隔离认证 Web boot 验证；需要下载时显式设置 `DSH_ACCEPT_DOWNLOAD=1`，只做静态检查时设置 `DSH_ACCEPT_WEB=0`。
6. Host/Client plugin 只能通过 `--patch` overlay 装载；版本不兼容时 fail-closed。
7. Agent preset 只能通过官方 `DSH_HOME/.agent-presets` roster 发现；preset
   只拥有 Agent-plane rows，不复制 Host-plane 的 Session、Web、JSONL 或账本。

## 升级

升级必须在单独变更中更新 commit、version、tar URL 和 SHA-256，执行新旧版本 contract/replay smoke，并留下 ADR/CHANGELOG 记录。不能改成浮动 `master`。

## 2026-08-31 验收事实

固定上游 `0a53fb55bea101816fa226bb964ae2bed71c343b` 已通过 source/build/authenticated Web、
success/partial_failure/insufficient_or_stale 三场景、callback gap、Web restart、版本不兼容
fail-closed 和 locked rollback。浏览器 1280x720 与 375x812 无横向溢出；该结论只证明
官方 Web 工程闭环，不证明 live source、预测准确率、收益或 DSH Promotion。
