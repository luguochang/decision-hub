# Research Capability Adapters

本目录只把受审计 Provider 输出转换为 canonical
`ResearchCapabilityResult -> EvidenceCandidate`。权限、域名、预算、PIT、hash、
Evidence 入账和双 Snapshot 由 Kernel 的 `ResearchCapabilityGatewayService` 与
`ResearchEvidenceService` 裁决；adapter 不拥有 Gate、Run、账本或 DSH Session。

- `web_search.py` 复用 R2-L 的 `SearchCapabilityPort`，搜索摘要固定标记为
  `search_derived`。
- `documents.py` 提供 allowlist 后的 Web/Official 文档抓取；默认只读 HTTPS。
- `replay.py` 只返回已归档 fixture，禁止在 replay 中回退到 live network。

任何新增 adapter 都必须通过同一 canonical contract、permission、PIT、timeout、
cost 和 replay 测试后，才能从 Pack 的 `candidate` 改为 `approved`。

G1/G2-A/B 已完成离线实现：server-owned PIT、provider/search error provenance、六类
事实 manifest 与 replay fixture 均已接通；外部 capability 仍 deny-by-default。G2-C
真实 Search canary 需 owner 单独确认，详见 [阶段卡](../../../docs/stages/R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)。
## G2 crypto-macro fact policy

`CryptoMacroFactPack` loads the Pack-owned `evidence/source_manifest.yaml` and
exposes the six minimum fact requirements plus their source/capability ladders.
It only evaluates typed Evidence with the existing deterministic Sufficiency
Gate; it never performs network I/O or chooses a market direction. The replay
fixture `g2_replay_manifest.json` covers success, stale and provider-failure
variants for every minimum fact.
