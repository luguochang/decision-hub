# Canonical Schemas

这里的 YAML/JSON Schema 是跨 Python、TypeScript、API 和事件的唯一来源。生成目录不能手改。R0 的 codegen 工具负责 schema 语法校验、生成 manifest hash、Pydantic/Zod 镜像同步检查和 compatibility fixtures 检查。

```bash
./.venv/bin/python -m tools.contract_codegen generate
./.venv/bin/python -m tools.contract_codegen check
```

`contracts/generated-manifest.yaml` 是 schema hash 记录；修改 schema 后必须重新生成，并同步生成镜像、契约测试和受影响模块 README。

R2 的 `workbench_assets.schema.yaml` 一次锁定 ResearchMemo、Capability、Dataset、Experiment、Candidate、Promotion、ActivePointer 和 FailurePattern 的跨模块语义；各任务不得另写同名 DTO。

Workbench/Evolution 的列表接口可以组合这些实体形成 Query/View DTO（例如 `WorkbenchOverviewView`），但组合 View 不得复制或改变实体字段语义。

R2-R 的 `agentic_research.schema.yaml` 是研究智能体跨边界契约真源，覆盖
Product Extension、Domain Pack、Role Profile、Research Capability、Evidence
Gap/Round、CausalCase、独立 HorizonDecision、Trace/StopReason 和前端
Research View/Command。它不绑定 LangGraph 或某个 DSH SDK 版本；Harness
实现只通过 `implementation_ref` 和 adapter 接入。大文本、原始网页和原始
Provider/DSH payload 只保存内容引用与 hash，不进入跨模块状态。

R2-R-06 在同一 schema 中增加 `ResearchEvaluationCase`、归档 capability fixture 和
Outcome label availability 契约；数据集 manifest 继续复用
`EvaluationDatasetManifest`，不创建第二套 Dataset/Experiment 账本。
真实 Provider 对照实验必须记录 `randomness_policy=provider_default` 和
`random_seed=null`，不得把 Provider 不承诺复现的运行伪装成 deterministic。

`dsh_host_bridge.schema.yaml` 是官方 DSH Web Host 与 Hub 之间的唯一桥接契约，
覆盖 readiness、确定性 submit、accepted/terminal callback 和 Run/Session link。
它只保存关联、状态、上游 identity 和结果引用，不复制 DSH Session JSONL。
