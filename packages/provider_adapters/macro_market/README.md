# Macro Market Research Adapter

`FredSeriesResearchAdapter` 通过 FRED 公开 CSV 读取明确的宏观时间序列，并输出
canonical `EvidenceCandidate`。它用于收益率、美元指数等低频/背景事实，不冒充
实时 tick；`published_at` 保留观测日期，Pack freshness Gate 可以据此判定 stale。

新增 series 只通过 `series_allowlist`，不能让 Agent 构造任意 URL。网络权限、PIT、
预算和是否启用仍由 Kernel Research Capability Gateway 裁决。
