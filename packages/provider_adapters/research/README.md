# Research Capability Adapters

本目录只把受审计 Provider 输出转换为 canonical
`ResearchCapabilityResult -> EvidenceCandidate`。权限、域名、预算、PIT、hash、
Evidence 入账和双 Snapshot 由 Kernel 的 `ResearchCapabilityGatewayService` 与
`ResearchEvidenceService` 裁决；adapter 不拥有 Gate、Run、账本或 DSH Session。

- `web_search.py` 复用 R2-L 的 `SearchCapabilityPort`，搜索摘要固定标记为
  `search_derived`。DSH native 与 Tavily transport 都通过该适配器进入同一个 Gateway；
  不允许 provider 自行晋升 authority。
- `documents.py` 提供 allowlist 后的 Web/Official 文档抓取；默认只读 HTTPS，只接受显式文本媒体。
  未配置专用 parser 时 PDF/二进制响应稳定拒绝，不能把乱码保存为 Evidence。
- `replay.py` 只返回已归档 fixture，禁止在 replay 中回退到 live network。

任何新增 adapter 都必须通过同一 canonical contract、permission、PIT、timeout、
cost 和 replay 测试后，才能从 Pack 的 `candidate` 改为 `approved`。

G1/G2-A/B 已完成离线实现：server-owned PIT、provider/search error provenance、六类
事实 manifest 与 replay fixture 均已接通。G2-AF 已完成首批 gap continuation 与 DSH/Tavily
typed adapter；外部 capability 仍 deny-by-default，真实 canary 需只读 secret、PIT 和预算门。

## 来源与事实边界

`source_registry.py` 是 Pack-owned 的来源准入策略，不是 fetcher、crawler 或新的插件运行时。
DSH native Search、Tavily 等 transport 只返回 `search_derived` locator；只有 approved Fetch 或
typed provider 经现有 Gateway、PIT、hash、authority 和 Semantic Gate 后，才可产生可入账的
`EvidenceCandidate`/`FactEnvelope`。unknown、未审计、错误 requirement 或越权 redirect 一律
fail-closed。该目录不拥有账本、Run、Gate、Session 或通知权限，也不保存未授权正文。

`documents.py` 的 parser hook 仅做一次 Fetch 后的确定性解析，不能通过解析器放宽来源 policy 或
创建第二套事实 DTO。事件身份目前只允许 `event_actor/event_time/revision_status`，不能把单篇
正文伪装成 `policy.delta`；市场分钟级事实只能来自对应 typed provider/event-window archive。

Registry 从同一 `source_manifest.yaml` 读取 dotted Pack ID 到 canonical requirement ID 的映射；
`source_registry.yaml` 不双写 canonical ID，也不靠点号/下划线猜测。通用 `web.fetch` 和
`official.macro` 必须共用该 Registry；前者只有同时通过 fetch/evidence 许可后才能创建 Candidate，
并采用来源政策的 authority，capability 域名白名单本身不构成 Evidence 批准。
## G2 crypto-macro fact policy

`CryptoMacroFactPack` loads the Pack-owned `evidence/source_manifest.yaml` and
exposes the six minimum fact requirements plus their source/capability ladders.
It only evaluates typed Evidence with the existing deterministic Sufficiency
Gate; it never performs network I/O or chooses a market direction. The replay
fixture `g2_replay_manifest.json` covers success, stale and provider-failure
variants for every minimum fact.
