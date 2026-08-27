# Source Adapters

## 目的
把手工文本、未来 ASR/Meeting Copilot、官方 feed 统一为 `TextEnvelope`。

## 不负责
不做市场判断、PIT freeze、Agent 分析或通知。

## 公开契约
`SourceConnector`、`SourceManifest`、`SourcePollResult`、`SourceHealth`、`SourcePlugin`、`TranscriptSourcePlugin`。

`SourceRegistry` 只负责 capability registration，不持有 durable cursor、health 或业务事实。`Kernel SourceIngestionService + Database` 在 `TextEnvelope` admission 和 durable Run 建立后才推进 cursor；相同 content hash 由 Kernel 去重，revision 通过 `revision_of` 留存。

## 官方来源
`official_feeds` 提供 RSS/Atom/JSON parser 和 Fed/BLS/BEA 官方 feed 配置。默认普通测试不触网，运行时需显式启用 source worker；没有授权的网页或搜索摘要不能作为 canonical source。

## 失败语义
429、超时、解析错误和未知异常映射为有限 `source_*` 错误码并更新健康状态；失败不推进 cursor，不修改已存在的 Event/Run/Artifact。

## ASR 位置
`transcript_meeting_copilot` 只校验 transcript envelope，并提供 fragment -> envelope 的转换；音频捕获和模型选择仍在来源层，不进入 Core 账本。

## 最近验证
R1 已以 fixture 验证 registry、固定 feed parser、cursor/去重/revision/health 和 transcript 文本边界；真实网络稳定性、授权范围和 ASR 模型质量仍需显式 live canary，离线退出门不等于生产完成。
