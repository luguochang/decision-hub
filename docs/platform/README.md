# 产品平台文档

状态：`accepted`（owner 于 2026-08-29 随 R2-R Stage Gate 接受）

本目录只描述跨领域长期有效的平台边界，不记录单个阶段的实施细节，也不复制 ADR、canonical schema 或模块 README。

## 阅读顺序

1. [平台基线与代码审计](PLATFORM_BASELINE.md)：当前代码能复用什么、DSH 与 LangGraph 各自拥有哪一层、哪些代码保留或迁移。
2. [资产与扩展模型](ASSET_AND_EXTENSION_MODEL.md)：个人/企业资产如何沉淀，领域、角色和能力如何插拔。
3. [当前决策投影](../context/CURRENT_DECISIONS.md)：已接受与待确认结论的短索引。
4. [R2-R Stage Charter](../stages/R2_R_AGENTIC_RESEARCH_RUNTIME.md)：已完成 candidate path 的实施范围和退出证据。

## 文档边界

- 平台文档回答“长期归谁、怎样扩展”。
- Domain 文档回答“某个业务如何判断、需要什么证据、如何评测”。
- Stage Charter 回答“本阶段具体做什么、不做什么、怎样验收”。
- ADR 回答“为什么作出一项跨模块决定，以及如何回滚”。
- `docs/context/` 只做短上下文投影，不创造新事实。

若本文档与已接受 ADR 或 canonical schema 冲突，以 ADR/schema 为准并停止代码修改；先修订 proposed 文档或新增 ADR。
