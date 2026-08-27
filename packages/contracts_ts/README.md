# TypeScript Contracts

这是 canonical schema 的 TypeScript/Zod 运行时镜像。Decision Desk、未来 DSH adapter 和其他 Web 入口只能依赖这个公开 package，不能复制同名 DTO 或从应用私有目录 deep import。`WorkbenchOverview` 是由 canonical ResearchMemo、Feedback、Capability 实体组合出的只读 View，不拥有第二套实体契约。
