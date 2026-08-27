# Query Views

## 目的
从 Kernel 只读表组装 Decision Desk 所需的 Overview、Inbox、Artifact 和 Health DTO。

## 不负责
不保存第二份业务账本，不做 Gate、Outcome 计算或 Provider 调用。

## 公开契约
`DecisionDeskQueryService.summary()`。

## 最近验证
`DecisionDeskQueryService.summary()` 与 Kernel Inspector DTO 已通过 API/E2E 测试；Query View 不读取 Graph state、Provider response 或前端私有 DTO。
