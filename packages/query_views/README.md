# Query Views

## 目的
从 Kernel 只读表组装 Decision Desk 所需的 Overview、Inbox、Artifact 和 Health DTO。

## 不负责
不保存第二份业务账本，不做 Gate、Outcome 计算或 Provider 调用。

## 公开契约
`DecisionDeskQueryService.summary()`。

## 最近验证
R0 scaffold。
