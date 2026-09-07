# Decision Hub DSH Plugin

本包是 Decision Hub 在固定 DeepSeek Harness Web 中的官方扩展边界。Host 端只调用公开 `webServer` 和 `sessionController`，不实现第二套 Session、Agent Loop、队列或业务账本。

## Host 职责

- `GET /decision-hub/v1/readiness`：版本、Host/Hub、Client plugin 和产品工作区 readiness；工作区
  尚未通过官方 `workspace/create` 注册时返回 `503/host_workspace_not_registered`，不得误报 ready。
- `PUT /decision-hub/v1/runs/{run_id}`：校验 canonical submit，确定性创建 Session、获取 Hub-owned prompt 并提示一次。
- `GET /decision-hub/v1/runs/{run_id}`：从公开 Session inspection 投影状态。
- `GET /decision-hub/v1/runs/{run_id}/result`：只有确定性 request 后同时存在 `assistant/message` 与 `turn/end` 才返回结果。
- `POST /decision-hub/v1/runs/{run_id}/cancel`：取消当前 DSH turn；不改 Hub Ledger。
- accepted/terminal 通过固定 Hub callback 路由回传；失败可由重复 submit/status 协调重放。
- `/api/decision-hub/research` 使用稳定幂等键创建正式研究 Run；失败 Run 通过 `/api/decision-hub/research/retry` 转发 canonical `ResearchRunCommand(retry)`，创建唯一 child Run，而不是用随机键制造重复事件。
- `/api/decision-hub/report?run_id=...` 同源代理 canonical `ResearchRunDetailView`，并用生成的 Zod schema 校验；Client Plugin 通过官方 `conversation.view` 注册独立“研究报告”页签，不复制业务模型，也不覆盖官方 Chat/Trajectory renderer。
- 受管 Session 必须通过公开 `workspaceRegistry.resolveByPath()` 取得 `workspaceId` 后创建或补挂，因此会出现在官方 Workspace 树；未注册工作区和解析失败分别返回稳定错误码。
- `decision_hub_synthesis_submit` 只接收 codegen Zod schema 校验通过的候选对象；Hub 只接受同
  Session、同 Tool Call 的成功轨迹配对，模型最终自然语言不能绕过结构化边界。
- 产品工作区中尚未关联 Hub Run 的 live Session 会在捕获阶段把官方发送按钮和 Enter 统一转成
  现有 `/api/decision-hub/research` intake；Shift+Enter 仍换行。已关联的受管 Session 恢复官方
  发送语义供报告追问。显式“建立研究任务”按钮是同一 intake 的可见回退，不是第二条业务主线。

受管 Run 写路由要求 `X-Decision-Hub-Host-Key`；官方同源 Client 的 intake/retry 只进入受限 Hub admission/owner command 边界。Host -> Hub 使用独立的 `X-Decision-Hub-Bridge-Key`。日志不记录密钥、prompt、HTTP body 或原始 provider response。

事件窗口研究额外透传 `event_id`、`event_at`、`window_start_at`、`window_end_at` 和
`requested_event_offsets`。Gateway 会将时间边界投影为 EventWatch 的 server-owned 值；模型无法
跨 Run 读取其他事件，也不能用 current snapshot 代替缺失窗口。

## Fail-closed 边界

- 只接受 `decision-hub://workspace/default`；实际 cwd 由 Host 配置提供。
- 只接受精确 `hub://runs/{run_id}/prompt`，Host 不抓取任意 URL。
- permission ref 必须在部署 allowlist 中。
- `idle` 只表示当前 Agent 不运行；没有可证明的 `turn/end` 和 assistant result 时绝不回调 `completed`。
- 不把 Session JSONL 复制到 Hub SQLite；认证 result route 只提供一次规范化交付投影。

## 本地验证

```bash
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
```

DSH 上游版本与公开 seam 由 `infra/dsh/upstream.lock.json` 和 `infra/dsh/verify-upstream.mjs` 固定。插件通过 `cordis.patch.yml` 加载，不修改上游源码。

官方当前版本在 `ConversationSession` 内把未选择视图时的 fallback 固定为 `chat`，没有向
第三方 Client Plugin 暴露默认视图选择 API。因此插件只增加 `decision-hub-report` 独立
页签，并把完整报告从 composer dock 移入该页签；Chat/Trajectory 和 JSONL 继续用于审计。
禁止通过复用 `id=chat`、DOM/CSS hack 或修改上游源码强制替换默认页。若未来 DSH 发布
合法的 per-workspace default-view seam，再通过上游升级任务接入。

## 当前阶段边界

`DSH-NATIVE-CORE` 和 `PRODUCT-CLOSEOUT-01` E1/E2-R/E2-L 已完成工程、replay 和真实官方
Web 验收。G2-AF 隔离真实 Run 已在受管 Session 内完成 3 轮、20/24 个受审计 capability call、
DSH 原生 `web_search`、结构化 synthesis、13 条保留 Evidence、Artifact、通知和复查链；Native
Tool 只从 `exec.agent.id` 注入 Session 身份，原始 MCP capability tool 不暴露给模型。Plugin
build identity 由构建生成文件和 Host readiness 共同提供，测试必须核对二者一致；文档不手写
会随构建变化的 hash。
这只支持 `pilot_technical_path=true / research_only`，不代表事实已充分、预测准确或自动交易；
长期产品价值仍等待 G2-AF-05/E3。完整证据见
[`G2-AF 实施执行记录`](../../../docs/evaluations/G2_AF_IMPLEMENTATION_EXECUTION_LOG_2026-09-04.md)。

这里不实现第二套 Agent Loop、队列、账本或 JSONL 存储，也不把 replay 解释为实时市场能力。输入在 replay 场景中继续发送会因固定脚本耗尽而失败，这是验收 transport 的预期边界。交互 live 入口必须使用无 replay patch 的独立 profile，并在启动前完成 Provider readiness。阶段执行卡见 [`docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md`](../../../docs/stages/DSH_NATIVE_CORE_COMPLETION_PLAN.md)，交互运行态审计见 [`DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md`](../../../docs/evaluations/DSH_INTERACTIVE_RUNTIME_AND_PLUGIN_ECOSYSTEM_AUDIT_2026-08-31.md)。

### 运行入口

```bash
# 固定脚本的离线验收：页面只读，不能新建或继续发送消息
./.venv/bin/python tools/dsh_native_acceptance.py --scenario success --serve

# 真实交互：不带 replay patch；启动前必须有 Provider 凭据
./infra/dsh/run-live-web.sh --port 3080
```

验收脚本输出的 `BROWSER_URL` 是本次进程唯一的 canonical 地址，首次打开后由 DSH 建立本地浏览器认证。直接打开裸 `/`、旧进程端口或没有本次 token 的新浏览器会得到官方 `401 dsh web authentication required`，应重新使用启动日志中的完整地址。replay 页面现在会明确显示只读提示并拦截输入；这不会改变官方 Session/Trajectory/JSONL，也不会把回放脚本改成 live。
