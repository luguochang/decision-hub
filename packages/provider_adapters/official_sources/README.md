# Official Source Research Adapters

本目录提供高频权威来源的 typed capability。首个
`OfficialDocumentResearchAdapter` 复用只读 HTTP 文档抓取，但固定输出
`kind=official`、`authority=official`；实际 URL 仍必须同时通过 Pack manifest 和
Kernel Gateway 的域名白名单。

后续 Fed/BLS/BEA/Treasury 日历或 API 只有在出现真实字段/频率需求时才新增专用
adapter，不为每个网站复制 HTTP、timeout、PIT 或 Evidence 逻辑。
