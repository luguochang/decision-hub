# DSH Live Research Attestation 修复与真实运行记录

版本：`DSH-LIVE-ATTESTATION-2026-09-02.v1`
日期：2026-09-02（Asia/Shanghai）
状态：`engineering verified / research-only`

## 1. 结论

本次真实运行已经跑通以下完整链路：

```text
官方 DSH Web 会话
  -> DSH Agent Loop
  -> decision_hub_research 工具
  -> Research MCP / Official / Market / Web Search
  -> Evidence/PIT/Sufficiency
  -> Decision Hub Gate 与 Artifact
  -> DSH 原生报告与轨迹投影
```

运行不是静态演示。DSH 页面显示了 `2` 轮、`6` 步、`12` 次工具调用和 `10` 条有效证据；Hub
同一 `run_id` 显示 `rejected/degraded`、`66.7% hard coverage`、Evidence、失败来源和三个
horizon 的 `no_trade`。Gate 拒绝是预期的 fail-closed 结果，不是方向性研究成功，也不是盈利
或自动交易证明。

## 2. 原始问题与根因

DSH 工具底层 `call_id`（例如 `call_*`）与工具参数中声明的业务 `request_id`（例如
`dhreq_*`）不是同一个值。MCP 返回的 `ResearchCapabilityResult.request_id` 使用声明值，
旧映射器只接受产品级 request ID 或底层 call ID，导致合法同 Session 结果被误判为：

```text
dsh_evidence_unattested / orchestration / synthesis_attestation
```

这属于 attestation 归属映射缺陷，不是 Provider 没有返回证据。

## 3. 修复范围

- `packages/runtime_adapters/dsh_runtime/tool_result_attestation.py`
  - `AttestedCapabilityResult` 保留 `declared_request_id`；
  - 从结构化 tool-call arguments 读取声明的 request ID；
  - 将 call ID、声明 request ID 和结果绑定。
- `packages/runtime_adapters/dsh_runtime/result_mapper.py`
  - 只在同一 DSH Session、allowlist capability 和 evidence fingerprint 均匹配时信任结果；
  - 合法 request ID 归属包括产品级 ID、call ID 或同一次工具调用声明的 ID；
  - 其他 Session、未知 capability、错误 fingerprint 继续 fail-closed。
- `tests/runtime/test_dsh_research_runtime.py`
  - 覆盖 call ID、`dhreq_*` 声明 ID 和错误 lineage 三类回归。

## 4. 真实运行证据

运行实例：

```text
DSH Web             http://127.0.0.1:52420（必须使用启动器输出的带 token URL）
Hub API             http://127.0.0.1:8240
Research MCP        http://127.0.0.1:8242
DSH upstream        0.1.2-alpha.2
runtime             dsh-web
workspace           Crypto Macro Trader
```

真实 Run：

```text
run_id              run_3c0c64d966aa431da7aecce642694fcf
status              rejected / degraded
rounds              2
tool calls          12
valid Evidence      10（Hub 保留证据记录更多元数据）
hard coverage       66.7%
Gate                reject
```

调用过的能力包括：`official.macro`、`market.cross_asset`、`market.crypto_derivatives` 和
`web.search`。官方讲话、BTC spot、funding、OI、basis、FRED 和搜索结果均有真实调用记录。

## 5. 为什么最终是 no_trade

本次没有足够可靠的事件窗口数据，代码 Gate 因此拒绝方向性结论：

- FRED 2Y、10Y 和 DXY 主要是日频，不能满足 300 秒事件窗口；
- 没有获得 CME FedWatch/SOFR/Fed Funds Futures 的事件前后量化重定价；
- BTC 只有当前 spot/衍生品快照，缺少讲话前后价格和成交量比较窗口；
- 缺少完整 liquidation/flow 历史序列；
- `web.search` 出现 `research_capability_timeout`，该错误被记录为可重试 transport failure。

因此报告保留 Evidence、coverage、failure provenance 和审计入口，但不发布 thesis、causal
chain 或方向性 Forecast；三个 horizon 统一为研究性 `no_trade`。这正是系统应有的安全行为。

## 6. 回归检查

```text
pytest -q                                      392 passed
pytest tests/runtime/test_dsh_research_runtime.py -q  41 passed
ruff check .                                   passed
pyright                                        0 errors
module docs check                              passed（13 modules）
contract_codegen check                         passed
git diff --check                               passed
Decision Desk tests                            10 passed
Decision Desk build                            passed
```

## 7. 当前使用方式

1. 在仓库执行 `./infra/dsh/run-product.sh`。
2. 打开启动器打印的完整 `DSH_URL=http://127.0.0.1:<port>/?token=...`，不要手写裸端口。
3. 进入 `Crypto Macro Trader` 工作区，点击 `新建会话`。
4. 输入研究目标，明确要求主动检索和证据不足时继续补证。
5. 点击 `建立研究任务`，在同一 DSH 页面查看 `对话`、`轨迹`、`研究报告`。

注意：已经关联正式 Run 的旧会话中，普通输入是 DSH 续聊，不会自动创建新的 Hub Run；新
业务研究必须先点击 `新建会话`，再使用该会话出现的 `建立研究任务`。失败 Run 使用报告页
的 `重新研究`，不要通过普通聊天伪造新的业务入口。

一次真实研究通常需要约 1 至 3 分钟。当前入口是单 owner、单机、只读的
`research_only` 试用，不是自动交易系统。

## 8. 未完成项与停止线

- 实时宏观和事件窗口数据源仍不足，不能宣称预测准确率或盈利；
- Search 长期稳定性尚未通过 Promotion 门；
- 当前 DSH 仍是 candidate/shadow，Fixed baseline 仍 active；
- 不在本记录中新增 Provider、第二 Agent Loop、ASR、PPT、第二领域或自动交易；
- 下一步只能进入 E3 前瞻观察，按事实覆盖、延迟、失败率、成本、人工复核时间、Outcome/Brier
  和 usefulness 验收，不能无限堆功能。
