# Source Adapters

## 目的
把手工文本、未来 ASR/Meeting Copilot、官方 feed 统一为 `TextEnvelope`。

## 不负责
不做市场判断、PIT freeze、Agent 分析或通知。

## 公开契约
`SourcePlugin`、`TranscriptSourcePlugin`、`ManualTextSource`。

## ASR 位置
`transcript_meeting_copilot` 只校验 transcript envelope；音频捕获和模型选择在未来扩展前单独 ADR。

## 最近验证
R0 只验证 TextEnvelope 输入边界；Transcript/ASR、新闻、日历和行情仍是 R1 SourcePlugin 工作包，不能在 R0 发布说明中宣称已接入。
