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
R0 scaffold。
