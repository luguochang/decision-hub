# Canonical Schemas

这里的 YAML/JSON Schema 是跨 Python、TypeScript、API 和事件的唯一来源。生成目录不能手改。R0 的 codegen 工具负责 schema 语法校验、生成 manifest hash、Pydantic/Zod 镜像同步检查和 compatibility fixtures 检查。

```bash
./.venv/bin/python -m tools.contract_codegen generate
./.venv/bin/python -m tools.contract_codegen check
```

`contracts/generated-manifest.yaml` 是 schema hash 记录；修改 schema 后必须重新生成，并同步生成镜像、契约测试和受影响模块 README。

R2 的 `workbench_assets.schema.yaml` 一次锁定 ResearchMemo、Capability、Dataset、Experiment、Candidate、Promotion、ActivePointer 和 FailurePattern 的跨模块语义；各任务不得另写同名 DTO。

Workbench/Evolution 的列表接口可以组合这些实体形成 Query/View DTO（例如 `WorkbenchOverviewView`），但组合 View 不得复制或改变实体字段语义。
