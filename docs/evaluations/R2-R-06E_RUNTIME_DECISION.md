# R2-R-06E Runtime 决策包

版本：`r2-r-06e-20260830`
状态：`retain_baseline / pending_owner_review`
生成时间：2026-08-30

## 结论

DSH Research Runtime 暂不晋级为 `crypto_macro` active runtime，继续作为 candidate/shadow；Fixed baseline 保持 active。此次结论不是“DSH 永远没有价值”，而是当前证据没有达到 Promotion 门：12-case 追加对照仍有 1 个 `dsh_evidence_unattested`、1 个 `dsh_session_incomplete`、1 个 `provider_timeout`，候选 p95 为 `180177ms`；新真实事件的 `web.search` 在 capability 20 秒 deadline 内失败，最终 Run 以 `critical_data_unavailable`、`reject` 收口。

机器可读副本见同目录 [R2-R-06E_RUNTIME_DECISION.json](R2-R-06E_RUNTIME_DECISION.json)。该文件是验收报告，不是业务账本；真实 Run、Trace、Snapshot、Result 和 Artifact 仍以临时 SQLite 及产品数据库中的 canonical 记录为准。

## 证据

### 12-case PIT 对照

实验：`r2-r-06c-20260830-repair-full-12case`，数据集：`crypto_macro.r2r_pit.v1`。

| 指标 | Fixed baseline | DSH candidate | Promotion 门判断 |
|---|---:|---:|---|
| 样本完成数 | 12/12 | 9/12 | DSH 未通过 |
| hard coverage mean | 0 | 0.1667 | 有提升，但不足以抵消失败 |
| Evidence | 0 | 12 | 需全部可认证 |
| unattested Evidence | 0 | 1 | blocking fail |
| PIT violation | 0 | 0 | pass |
| Horizon distinct | 0/12 | 8/12 | DSH 未通过 |
| Tool calls | 0 | 43 | 需与失败和成本一起解释 |
| p95 latency | 121336ms | 180177ms | 超出 180 秒预算边界，未通过 |
| estimated cost | unknown | unknown | 不能宣称成本可控 |

失败样本不得删除或重跑覆盖。其价值是证明 fail-closed、证据认证和尾延迟边界仍在工作。

### 新真实事件

- 事件：Kevin Warsh 于 2026-08-28 在 Jackson Hole 发表 `In Our Time`。
- 官方来源：<https://www.federalreserve.gov/newsevents/speech/warsh20260828a.htm>
- Run：`run_27acb7c425914bc7a69060637ea1feb3`
- Runtime：DSH `0.1.1rc1`，profile `decision-research.v1:faf1b3115f7d339c`，model `gpt-5.5`。
- 可观察结果：Session 成功启动并尝试 1 次 `web.search`；MCP 日志记录该 capability 在 20 秒审计 deadline 内失败。产品 Trace、Decision Snapshot、Research Result 和 reject Artifact 均已落账。
- 安全结果：`evidence_count=0`、`stop_code=critical_data_unavailable`、Gate=`reject`、`active_pointer_changed=false`；没有用模型猜测补成方向，也没有发送发布通知。

这证明了“主动发现缺口 -> 调用工具 -> 工具失败 -> 代码充分度 Gate -> 可观察降级”的智能体生命周期骨架，但没有证明实时搜索稳定或研究结果具备交易价值。

## 未通过项与下一步

1. 保持 Fixed active，不修改 active pointer。
2. 将 Search provider timeout、MCP capability error 和 DSH tool error 的 provenance 继续细化，不能只显示泛化 `dsh_tool_failed`。
3. 在新的 owner Stage Gate 下，仅修复 Search reliability/error provenance 并重跑单一真实事件；不同时引入 ASR、PPT、第二领域、自动交易或社区插件。
4. 只有新的单事件 canary、失败分类和 owner usefulness 通过后，才考虑重跑 12-case；不得先扩大样本掩盖单事件失败。

## Owner usefulness 表单（待填写）

以下问题必须由 owner 根据页面实际使用回答，不能由 LLM Judge 代填：

- 相较手工查证，是否减少了查证时间？（是/否/无法判断）
- 报告是否能解释证据、反方和停止原因？（是/否/部分）
- 在当前失败和延迟下，是否值得继续作为 candidate？（是/否）
- 最值得保留或修正的一个能力是什么？

在表单完成前，`owner_usefulness_accepted=false`，不能报告 `promotion_recommended`。

## 权限边界

本决策包没有修改业务账本、历史结果、Provider 配置、Capability 审计状态或 active pointer。DSH 仍只拥有受限 Agent Loop；Core Gate 仍是唯一发布裁决者。
