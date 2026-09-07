# Official Source Research Adapters

本目录提供高频权威来源的 typed capability。首个
`OfficialDocumentResearchAdapter` 复用只读 HTTP 文档抓取，但固定输出
`kind=official`、`authority=official`；实际 URL 仍必须同时通过 Pack manifest 和
Kernel Gateway 的域名白名单。

后续 Fed/BLS/BEA/Treasury 日历或 API 只有在出现真实字段/频率需求时才新增专用
adapter，不为每个网站复制 HTTP、timeout、PIT 或 Evidence 逻辑。

当前 Federal Reserve parser 只生成确定性 `event.identity` Facts，并由
`ResearchSourceRegistry` 校验请求 URL 和最终 redirect URL。Search locator、网页摘要和模型
推断不会被提升为 official Evidence；缺少可信 actor/time 时返回稳定错误并安全停止。该模块不
拥有 Agent Loop、账本、Gate 或发布权，新增字段必须先更新 canonical schema、Domain Pack 语义
真值表和对应 BDD/TDD 回放。

`official-event.v1` 只接受 Pack 映射后的 canonical `event_identity`。对
`policy_or_data_delta` 等其他 requirement 返回
`research_capability_requirement_unsupported`，不会改标签伪造 `current/baseline/delta`。
Fed speech feed 调用负责从可信发布日期生成 event identity；正文阅读交给共用 Registry 的
`web.fetch`，真正的政策变化 Fact 需要以后单独批准 baseline-aware parser/provider。
