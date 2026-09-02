# ADR-0017 Search Evidence 引用归因与独立来源边界

日期：2026-09-01
状态：accepted
决策：Responses `action.sources` 只作为发现元数据；只有明确绑定到 output text span 的
`url_citation` 才能生成 claim-bearing Search Evidence。独立来源数量由保守、确定性的
provenance identity 决定，不能由模型返回的 URL 数量决定。

## 背景

真实 Run `run_ef2b6ab98ae34aa48d42195d49319303` 暴露了来源血缘错误：旧 Search transport
收集所有 `web_search_call.action.sources`，再把同一份全局 `output_text` 复制到每个 URL。
因此一个关于 Warsh 2026-08-28 讲话的摘要同时被挂到实际讲话、旧 Powell 讲话和旧
Bowman PDF 等不同页面。`WebSearchResearchAdapter` 又把 hostname 写入 `source_id`，
Sufficiency 可能把这些无法证明支持摘要的 URL 计为独立来源。

这会让“证据数量很多”看似正常，但 `source_url`、`excerpt` 和实际 claim 没有可验证关系。
它属于 Evidence 可信含义错误，不是前端显示问题。

当前安装的 OpenAI Python SDK Responses 类型把 Web citation 定义在
`output[].content[].annotations[]`：`type=url_citation`，并包含 `url`、`title`、
`start_index` 和 `end_index`。中转若宣称兼容 Responses，必须保留该结构；缺失时不得
自行猜测绑定关系。

## 候选方案

1. **继续使用 action.sources + 全局摘要。** 实现最简单，但无法证明每个 URL 支持该摘要，否决。
2. **对每个 action.sources URL 再抓取全文并做模型匹配。** 会引入新的抓取权限、网络失败、
   版权/robots、正文解析和模型归因问题，且形成第二个隐式 Search loop，否决。
3. **用 Markdown 中的域名链接做正则匹配。** 显示文本可变，URL alias 多，不能替代协议
   annotation，否决。
4. **只接受结构化 url_citation exact binding。** 可确定性验证并 fail-closed，接受。

## 决策

1. `action.sources` 是本次工具调用发现过的 URL 集合，不直接产生 `SearchEvidence`。
2. 只解析 message `output_text` block 的 `url_citation` annotations。
3. citation URL canonicalize 后必须存在于同一响应的 `action.sources` 集合；否则
   `search_output_invalid`。
4. `start_index/end_index` 必须是非布尔整数，满足 `0 <= start < end <= len(text)`；
   Evidence `snippet` 只保存该 exact span，不扩展到整个段落。
5. URL canonicalization 至少处理 scheme/host 大小写、默认端口、fragment 和常见 tracking
   query；凭据 URL 和非法端口拒绝。
6. 同一 canonical URL 合并；同一 normalized claim span 若绑定多个 URL，只保留第一条，
   不能满足多个独立来源。
7. Search Evidence authority 固定为 `search_derived`，official-looking hostname 不提升等级。
8. `www.` 与 apex hostname 使用相同保守 `source_id`；同 hostname 的不同页面不能贡献多个
   publisher。更复杂的跨域品牌别名只有在未来引入经过审计的 Source Registry 后才处理。
9. 有 sources 但没有有效 citation 时返回 `search_no_attributed_sources`；不得用 top-level
   `output_text` 或模型记忆兜底。

## 后果

- 真实可归因的 Search Evidence 数量会显著减少，这是正确的保守行为。
- 不保留 citation annotations 的 OpenAI-compatible 中转会安全失败；系统应优先使用
  Official/Market capability 或解释性停止。
- Exact citation span 可能比整段摘要短，但它能证明 URL 与文本关系；生成摘要仍可保留在
  DSH 会话中，不能冒充每个来源的 Evidence excerpt。
- 本次不修改 canonical schema：现有 `SearchEvidence.snippet` 足以保存 attributed span。
  若未来同时需要 claim summary 和 source quote，必须另立 schema 版本、codegen 和 migration。
- 历史 Evidence 不改写；旧 Run 仍保留用于复盘，但不得作为修复后独立来源语义的验收样本。

## 迁移与回滚

- 新 Run 立即使用新的 transport 语义；无历史数据迁移。
- Provider 不兼容时保持 capability failed/insufficient，并记录稳定错误，不回退到旧摘要复制。
- 若实现回滚，`pilot_ready` 必须同时回到 false，且不能使用受影响 Search Evidence 做 Gate。

## 受影响契约

- canonical schema：无字段变化；Search Evidence 的可信语义收紧。
- error code：新增/固定 `search_no_attributed_sources`；非法 annotation 使用
  `search_output_invalid`。
- authority：继续为 `search_derived`。
- source independence：继续由 `EvidenceCandidate.source_id` 决定，但 adapter 使用保守
  publisher identity。

## 受影响实现

- `packages/provider_adapters/search/openai_responses.py`
- `packages/provider_adapters/research/web_search.py`
- `packages/provider_adapters/README.md`
- `tests/capabilities/test_search_capability.py`
- `tests/research/test_capability_gateway.py`
- `tests/kernel/test_sufficiency.py`

## 受影响测试

- 两个 sources + 一个 citation：只产生一个 Evidence；
- sources + global text + 无 annotations：稳定失败；
- 两个不同 citation span：逐源 Evidence；
- 同一 span 多 URL：不能虚增来源；
- tracking/fragment alias：canonical 去重；
- citation URL 不在 action.sources、offset 越界或布尔 offset：fail-closed；
- `.gov` Search citation 仍为 `search_derived`；
- `www.`/apex 同 publisher 时 Sufficiency 仍为 `insufficient_sources`。
