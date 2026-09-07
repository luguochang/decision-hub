# Decision Hub 单机研究试用版使用说明

版本：2026-09-03  
适用形态：单机、单 owner、只读 research-only 试用  
产品入口：官方 DeepSeek Harness（DSH）Web  
管理入口：Decision Desk（本机 Hub API）

## 1. 这是什么

Decision Hub 是一个事件驱动的研究系统。用户从 DSH Web 提交研究主题后，DSH 负责会话、模型 Agent Loop、工具调用、轨迹和 JSONL 持久化；Decision Hub 负责 durable Run、来源能力、Evidence、PIT 时间边界、Coverage、确定性 Gate、报告投影和复查调度。

当前首个领域是 `crypto_macro.v1`，默认研究角色为 `Crypto Macro Trader`。它可以把一段文字任务转换为有边界的研究 Run，主动调用已授权的官方宏观、跨资产和加密货币现货/衍生品能力，并在证据不足时继续有限补证。它不会因为模型给出一段看起来完整的话，就绕过 Gate 发布方向性结论。

## 2. 当前交付边界

当前可以交付为“单机 research-only 试用版”，用于验证研究链路、事实证据、报告结构和可观测性。

当前不能宣称：

- 实时行情和网页搜索永远可用；
- 预测准确、盈利或可自动交易；
- 证据不足时仍会给出可靠多空方向；
- DSH 已被替换、Fork 或成为业务账本；
- 语音转文本、PPT、第二领域和多用户平台已经交付。

当关键事实过期、来源不可用、Provider 失败、工具预算耗尽或结构化输出不合格时，系统应显示失败/仅研究/不交易，并保留失败来源和已取得的证据。

## 3. 启动

在仓库根目录执行：

```bash
env \
  DSH_PRODUCT_PORT=52780 \
  DECISION_HUB_API_PORT=8990 \
  DECISION_HUB_RESEARCH_MCP_PORT=8992 \
  DECISION_HUB_COMPOSE_PROJECT=decision-hub-user-acceptance \
  DSH_PRODUCT_KEEP_SERVICES=1 \
  ./infra/dsh/run-product.sh
```

启动器会：

1. 启动 Hub API、Research MCP、实时 worker、研究 worker 和 Evolution worker；
2. 使用 `data/dsh-live/.env` 中的本机 Provider 凭据，不要求每次重复输入；
3. 启动固定版本的官方 DSH Web；
4. 注册 `Crypto Macro Trader` 工作区；
5. 在终端打印唯一的带本地 token 的 DSH URL。

只打开启动器本次打印的 URL。URL 中的 token 只用于本机认证，不要复制到 Git、Markdown、截图或工单中。停止本次专用实例可以使用：

```bash
./infra/dsh/stop-product.sh \
  --project decision-hub-user-acceptance \
  --api-port 8990 \
  --mcp-port 8992 \
  --dsh-port 52780
```

不要用这个命令停止其他端口上的历史实例。

## 4. 从 DSH Web 发起研究

1. 打开启动器输出的 DSH URL。
2. 点击“新建会话”。
3. 选择工作区 `Crypto Macro Trader`。
4. 选择“决策研究”。
5. 在输入框写研究任务，或使用预填充的示例任务。
6. 点击“建立研究任务”。
7. 等待状态从“研究任务已排队”进入“研究中”，再进入终态。

建议的首个测试任务：

```text
请研究截至当前时间最近一次美联储主席公开讲话对 BTC 的影响。先确认事件身份、讲话原文与发布时间，再主动检索政策变化、美国利率与美元传导、跨资产、BTC 现货和衍生品事实；遇到证据缺口时继续尝试所有仍可用的已授权能力。完成主因果链和最强反方链，并按 30 分钟、24 小时、72 小时给出经过代码 Gate 的研究结论；禁止用记忆编造实时数据。
```

提交后，DSH 会创建一个稳定关联的 Hub `Run ID` 和 DSH `Session ID`。相同的提交不会无界重复创建 Run；“重新研究”会走幂等的 child Run。

## 5. 如何看懂 DSH 页面

### 对话

显示用户输入、系统注入的研究上下文、模型阶段消息和结构化结果。原始 JSON 只用于审计，不需要把它当成最终报告阅读。

### 轨迹

轨迹是 DSH 原生可观测入口，包含：

- 轮次和模型步骤；
- `decision_hub_research` 工具请求与结果；
- 每次 capability 的开始、完成和失败；
- Evidence 接受/拒绝及原因；
- Coverage、Replan、Synthesis 和 Session Stop。

截图：

![DSH 研究轨迹](/Users/chase/Desktop/codex/project/dsh-mult/docs/evaluations/assets/product-user-guide-trace-20260903.jpg)

### 研究报告

报告页只投影业务可读字段：

- 当前 Gate：`仅研究`、`可发布`、`拒绝`或失败；
- hard/soft coverage；
- 有效 Evidence 数量；
- 30m、24h、72h 的 action、概率、触发条件、失效条件和下次复查；
- 主因果链、反向假设和未解决问题；
- 停止原因和失败来源。

截图：

![DSH 研究报告](/Users/chase/Desktop/codex/project/dsh-mult/docs/evaluations/assets/product-user-guide-report-20260903.jpg)

## 6. 如何看懂报告状态

| 页面状态 | 含义 | 用户动作 |
| --- | --- | --- |
| 研究中 | Durable Run 仍在执行，可能正在补证或重规划 | 等待终态，不重复提交 |
| 可发布 | Evidence、PIT、结构化输出和 Gate 均满足该领域规则 | 仍只作为研究意见使用，不自动交易 |
| 仅研究 | 取得了部分事实，但仍有关键缺口、过期数据或预算边界 | 只阅读事实和缺口，等待复查或补充来源 |
| 研究拒绝 | 输入、权限、契约或关键事实不满足准入 | 修正输入/权限后重新研究 |
| 本轮运行失败 | Provider、DSH、MCP、网络或结构化输出失败 | 查看失败来源；只有 `retryable=true` 才重试 |
| 报告投影暂不可用 | Run 已终态，但详情接口暂时不可达 | 以状态栏和 Decision Desk 为准，不把它当成成功报告 |

空白会话（`run_id=null`）只显示“当前会话尚未关联正式研究任务”，不会伪装成“研究报告生成中”。
若历史 Run 显示 `dsh_host_unavailable` 且标记“可重试”，请使用“重新研究”创建 child Run；父 Run、失败来源
和已有证据会原样保留。实际验收中发现的问题与根因见[产品用户交付问题记录](../evaluations/PRODUCT_USER_DELIVERY_ISSUES_2026-09-03.md)。

`no_trade` 在当前版本是保护性动作，不代表市场一定下跌或上涨，而是代表当前证据不支持承担方向性风险。

## 7. 如何查看 Decision Desk

在浏览器打开：`http://127.0.0.1:8990/?run_id=<Run ID>`。其中 `<Run ID>` 替换为 DSH 报告中显示的实际 ID。

Decision Desk 是管理后台，不是第二套用户聊天入口。它提供：

- Research runs：所有自动触发、人工提交和 scheduled recheck；
- Evidence sufficiency：每个 hard/soft gap、鲜度和 authority；
- Failure provenance：错误码、来源层、capability、tool call 和 retryability；
- Research plan：每轮计划、任务和调用计数；
- Evidence：来源、时间戳、质量和原文链接；
- Research trace：归一化后的 Hub 业务轨迹；
- Operations / Evolution：运行服务和后续评测入口。

截图：

![Decision Desk 研究后台](/Users/chase/Desktop/codex/project/dsh-mult/docs/evaluations/assets/product-user-guide-desk-20260903.jpg)

## 8. 可评测与可观测性

本版本可以看到三层效果：

1. DSH 原生层：Session、Trajectory、Tool、模型步骤、耗时、Token、JSONL 和错误。
2. Hub 业务层：Run、Evidence、Coverage、PIT、Gate、停止原因、复查和父子 Run。
3. 工程 Trace 层：可选的 DSH observability plugin/OTLP 捕获；默认关闭，不把技术 Trace 冒充业务证据。

验收时应同时核对 DSH“轨迹”、DSH“研究报告”和 Decision Desk 的同一 `Run ID`。三处如果不一致，应记录为投影问题，不要手工改数据库。

## 9. 本次真实验收示例

真实 Run：`run_bb4a5161d3ca4b049baf4d7386fd478d`  
DSH Session：`dsh_1bb912016c18188e19fb3f70e04ba09a16ce5e749132d12a1dd75385547d65e5`  
运行模式：`live`  
结果：`research_only`  
轮次：2  
工具调用：12  
Evidence：15 条已保留，其中 6 条为当前报告接受的非 stale Evidence  
Trace：87 条标准化事件  
Hard coverage：67%  
停止原因：`tool_budget`  
失败来源：编排层将 stale 事实作为 `evidence_stale` 保留并安全降级  
复查：已排入 scheduled recheck

这次运行证明了“Provider -> DSH Agent Loop -> capability -> Evidence/PIT/Coverage -> Gate -> DSH/Hub 页面投影”的主链路；它没有证明 FRED 日频数据满足 30m/24h 的实时要求，也没有证明预测准确率或盈利。

## 10. 常见问题

### 页面显示“API 密钥无效”

先停止本次专用实例，再重新用 `run-product.sh` 启动。启动器已经隔离 `DEEPSEEK_*` 与宿主 `OPENAI_*` 命名空间；不要在宿主 shell 中手工把 OpenAI key 改名成 DeepSeek key。

### 报告显示“证据不足”

这是 Gate 的正常保护行为。先查看 Evidence sufficiency 的具体 gap，例如过期的利率/USD、缺少事件窗口或缺少独立来源。不能通过手工填写 `observed_at` 或复制旧数据绕过 PIT。

### 页面显示“仅研究完成”但没有多空方向

这是预期结果。当前硬性事实不满足时，系统只保留事实、因果候选、反向假设和 `no_trade`，不会发布未经 Gate 的方向性 Forecast。

### 看到很长的 JSON

在 DSH“对话”或“轨迹”中，JSON 是可审计原始记录；最终阅读应优先使用“研究报告”页和 Decision Desk 的 Evidence/Gap 区域。

## 11. 交付结论

截至 2026-09-03，本产品适合单 owner 本机 research-only 试用和工程验收。进入“可用于实时决策”的下一步，必须新增实时利率/美元/预期定价来源、事件窗口数据、独立交叉资产来源，并完成至少 14 天或 20 个高影响事件的前瞻价值观察；未通过价值 Gate 前，不切换 active pointer，不启用自动交易。
