# TypeScript Contracts

这是 canonical schema 的 TypeScript/Zod 运行时镜像。Decision Desk、未来
DSH adapter 和其他 Web 入口只能依赖这个公开 package，不能复制同名 DTO
或从应用私有目录 deep import。`WorkbenchOverview` 是由 canonical
ResearchMemo、Feedback、Capability 实体组合出的只读 View，不拥有第二套
实体契约。

R2-R 的 ResearchSession、Evidence、CausalCase、HorizonDecision、Trace 和
ResearchRunView 均由 `agentic_research.schema.yaml` 生成，并由 `src/index.ts`
稳定导出。`src/generated/r2.ts` 禁止手改；修改契约后运行 codegen，再运行
Decision Desk test/build 验证消费端。
真实 Provider Experiment 的随机性使用 canonical `provider_default`，前端不得展示为
可复现的 deterministic/seeded 运行。
