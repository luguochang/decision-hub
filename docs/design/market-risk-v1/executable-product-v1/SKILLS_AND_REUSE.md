# 可执行技能、经验桥与现有能力复用规格

日期：2026-10-13。状态：开发设计；本轮只读产品代码，新增本文及 `spec/` 的三个离线参考文件，未安装依赖、修改生产代码、迁移数据库或启动模型。代码审查排除 `**/*_versions/**`。本文的接口、表和包合同均明确标作拟建，不构成实现或金融效果验收。

## 1. 交付范围与运行责任

必须同时通过[VALUE_ALIGNMENT.md](VALUE_ALIGNMENT.md)的I03–I06/I08/I11：结果自动触发ReflectionDelta、新候选差异、合法试用和后续真实消费，不能只演示手写种子。全球观点先进入ClaimSignal，经核验影响机制/行动；帖子内容与外部方法包不能直接获得执行权限。

首个切片交付：一个版本固定的种子技能、一条真实 Run 的选择→加载→执行→消费回执，以及至少一条有真实血缘的经验/案例进入候选技能评估。不能只交付 SKILL.md 或“工具调用成功”的摘要。

- DSH 保持唯一 agent loop：选技能、发工具调用、读新增证据、修订假说和综合。
- Hub 复用持久任务、冻结输入、Gateway、组件登记、评价、CAS 激活与回滚；新增的是合同、选择器和回执接线。
- 声明式 workflow 编译为已允许工具的节点及依赖检查。Hub 不新增自治调度器、技能任务队列、技能安装器或另一个模型执行循环。
- 技能内容不能修改运行权限、数据源许可、资产准入、发布确认、期限或评价门槛。新增资产必须先有该资产的事实/报价/日历与评价合同；技能无法令黄金或指数自动获得 BTC 的准入资格。

## 2. 已核可复用工具与领域模块

下表是当前文件能力；“需补”一列为开发内容。路径相对于仓库根目录。

| 层 | 当前文件/入口 | 可直接复用的机制 | 需补内容/边界 |
|---|---|---|---|
| 来源与查询 | `packages/provider_adapters/research/source_registry.py:195`；`packages/workbench_adapters/research_catalog.py:8` | 来源 license/audit、域名/路径、requirement 校验；catalog 对实际 adapter 的 fields/symbols/modes 和 hash 投影 | 技能依赖必须匹配冻结 Run 的 catalog，未知字段不是任意字段。外部技能说明不授予网络能力 |
| 时间 | `packages/provider_adapters/research/publication_time.py:10`；`packages/provider_adapters/macro_market/fred_time.py:42` | 发布/修订/收到/Kernel known 时间；FRED observation date 与可用时点分离 | 包、经验、案例及技能输出都有 first_known_at；未来修订和未来案例禁止进入较早 Run |
| 单位与报价 | `packages/provider_adapters/research/fact_pack.py:22`；`packages/kernel/decision_hub_kernel/ports/market_path.py:48` | `field_units`、`unit_policy`、报价币种/price type、Decimal、UTC、闭合分钟 | 当前 `spot_scope` 只启用 BTC/ETH；黄金美元/盎司、指数点位和交易日历必须用资产扩展合同，不从 BTC 单位猜测 |
| 工具事实/回执 | `packages/workbench_adapters/durable_research_gateway.py:126`；`packages/kernel/decision_hub_kernel/application/research_evidence.py`、`fact_store.py`、`fact_lineage.py` | Run/session 血缘、调用 reservation、PIT、幂等、adapter 原返回、原始 body、Fact 与 lineage | 技能执行复用这些边界；方法加载不生产市场 Fact，不把“0 条 attested facts”认作加载失败 |
| 原字节存储 | `packages/provider_adapters/research/raw_capture.py:78` | `RawBodyArchive.put/read`，`raw://sha256/<hash>`，读时重验字节 | 同一 archive 保存技能 manifest、SKILL、workflow、schema 的资源原件，不另造下载/安装服务 |
| 案例 | `packages/kernel/decision_hub_kernel/application/market_case.py:538`；`ports/market_case.py` | case scope、原件 hash、available_at、资格原件复核、排除项；案例是 context_only | 当前自动接线 `apps/hub_worker/market_case_context.py` 限 BTC/ETH、24h、精确 stable method；不能宣称跨资产学习已实现 |
| 冻结上下文 | `research_binding.py`、`research_context_execution.py`、`research_context_delivery.py` | Pack/Task/Compiled 和 provider 上下文的不可变绑定 | 新增方法资源绑定；技能不是官方新闻，不能混入当前市场 Fact 的 authority |
| 组件 | `ports/component_registry.py:14`；`application/component_registry.py:60`；`apps/hub_worker/market_components.py:193` | register→shadow→原件 qualification→review→CAS activate/rollback；真实消费 input/output hash、同 writer audit 原件重读 | SkillVersion 是 `research_recipe` 的具类型扩展；不得复用纯本地执行资格来认证整场金融研究 |
| 监测 | `ports/risk_monitor.py`；`application/risk_monitor.py`；`application/forecast_alarm.py` | 有类型风险条件、fresh/stale 状态、确认根数、冷却与实际 monitor 消费 | workflow 只能提交 monitor proposal，现有服务负责准入和执行；生成文本不直接安装告警 |
| 评价/结算 | `application/research_value.py`、`revision_feedback_evaluation.py`、`risk_path_ledger.py`、`evolution.py` | 结果与原件关联、路径预测/观察、实验与晋级原件 | 一份 feedback attachment 或 verified 状态不是金融标签；候选技能需方法质量与金融效用分别评价 |
| 原生模型输入 | `extensions/dsh/decision-hub/src/host/provider-input.ts`、`retained-context.ts`、`session-result.ts` | canonical JSON、真实 model dispatch 输入/工具输出、session event seq 和原件绑定 | 补确切技能版本与实际输入文字核验；generic `dsh.internal.skill` 成功不能自动算哪个包已加载 |

已核两个具体断点：`EvolutionContextService._resolve` 当前没有 `experience:` resolver；`CandidateConfigurationRuntime.execute` 只是把四项 `RULES` 附加到 instructions。四项为 cite_frozen_evidence、separate_missing_evidence、examine_counter_evidence、abstain_on_conflicting_evidence。它们不等于搜索/计算/案例检索技能编译器。

## 3. 实现切片与文件归属（拟建）

| 切片 | 新增文件/合同 | 现有改接文件 | 完成证据 |
|---|---|---|---|
| S1 包与版本 | `contracts/schemas/research_skill.schema.yaml`；`packages/kernel/decision_hub_kernel/ports/research_skill.py`；`application/research_skill_package.py` | `component_registry.py`、`market_component_bootstrap.py`、`market_component_versions.py` | 一个包完整原字节、resource/code/dependency hash，唯一版本身份；篡改拒绝 |
| S2 选择与冻结 | `application/research_skill_selection.py` | `research_binding.py`、`research_context_execution.py`、`apps/hub_worker/composition.py` | 同一 Run 冻结选中版本、排除原因、pointer generation、scope 和 catalog hash |
| S3 DSH 回执 | `extensions/dsh/decision-hub/src/skill-tool.ts`；`src/host/skill-binding.ts`；`application/research_skill_use.py`；`apps/hub_api/research_skill_routes.py` | `host/hub-client.ts`、`host/provider-input.ts`、`host/session-result.ts`、现有插件注册入口 | native tool result 与真实冻结模型输入证明 loaded；节点原返回证明 executed；最终引用证明 consumed |
| S4 经验桥 | `application/experience_case_bridge.py` | `evolution_context.py`、`evolution.py`、`market_case.py`、现有 MarketCaseContextProvider | experience→实际原件→case/候选包映射，缺血缘明确排除；旧经验/案例不重写 |
| S5 workflow | `application/research_skill_compiler.py`、`application/research_skill_execution.py` | `candidate_execution.py`（旧规则保留兼容）、DSH 研究工具/Gateway、组件纯函数消费者 | DSL 合法节点能触发已有能力并留逐步回执，不只是渲染四句 instructions |
| S6 资格与替换 | `apps/hub_worker/skill_component_protocol.py` 与对应 tests | 既有 ComponentRegistry shadow/review/activate/rollback，既有实验评价入口 | 先注册评估协议、完整分母、对照及 shadow；真实激活与回滚由既有 CAS 留痕 |

Python/TS DTO 由 schema 同步生成，禁止直接手改 generated 文件。只加一条正常迁移接入既有 ORM：新增技能事件和经验桥关系。审查时现有最大迁移号为 0092；正式序号由实现批次分配，不在本文预占。

## 4. SKILL 包目录与不可变 manifest

作者目录沿用 `packs/crypto_macro/skills/<skill-id>/`：

```text
macro-transmission-review/
  SKILL.md                  # 方法说明、触发/排除条件；沿用 DSH skill frontmatter
  workflow.json             # 声明式节点与 typed bindings
  schemas/input.schema.json
  schemas/output.schema.json
  evaluation-protocol.json  # 预先登记指标、对照、样本划分、失败口径
  references/method.md       # 方法出处；不是即时市场事实
```

发布使用现有构建/组件资源捕获：逐文件 `RawBodyArchive.put`，生成排序文件 manifest，再保存 canonical manifest。作者目录变化不影响已冻结 Run。DSH 不在运行期间执行 pip/npm、拉 GitHub 或写 SKILL 目录。

拟建 `research-skill-version.v1` 示例（`<sha256:...>` 是设计占位，不能提交运行/激活）：

```json
{
  "schema_version": "research-skill-version.v1",
  "skill_id": "macro-transmission-review",
  "version_id": "skill_macro_transmission_1_0_0",
  "parent_version_id": null,
  "kind": "method_workflow",
  "domain_pack_ref": "crypto_macro.v1",
  "scope": {"market": "crypto", "instruments": ["BTC-USDT", "ETH-USDT"], "horizons": ["24h", "72h"]},
  "input_contract": "macro-review-input.v1",
  "output_contract": "macro-review-result.v1",
  "files": [
    {"path": "SKILL.md", "body_ref": "raw://sha256/<sha256:skill-md>", "body_hash": "<sha256:skill-md>"},
    {"path": "workflow.json", "body_ref": "raw://sha256/<sha256:workflow>", "body_hash": "<sha256:workflow>"}
  ],
  "trigger": {"event_families": ["macro_release", "liquidity_change", "cross_asset_dislocation"]},
  "exclude": {"research_modes": ["legacy_unbound"]},
  "dependencies": {"capability_catalog_hash": "<sha256:catalog>", "component_versions": [], "input_schema_hash": "<sha256:input-schema>", "output_schema_hash": "<sha256:output-schema>"},
  "workflow_hash": "<sha256:workflow>",
  "compiler_hash": "<sha256:installed-compiler>",
  "evaluation_protocol_hash": "<sha256:protocol>",
  "origin_refs": [],
  "first_known_at": "2026-10-13T08:00:00Z"
}
```

完整包的 files 必须包含目录中全部运行相关文件；上例缩写不能作为完整发布输入。禁止绝对路径、`..`、符号链接外跳、任意 executable entrypoint 和自定义 Python/JS import。

组件映射：`ComponentManifest.kind=research_recipe`，`component_ref=skill:<skill_id>`，`scope_id` 仍为已准入资产范围；`code_hash` 认证已安装 compiler/executor/adapter 源码，`resource_hash` 为包 manifest 的 canonical hash，`parameters.skill` 由新 SkillVersion 类型严格验证。注册不激活，不把它塞入旧 directional ActivePointer。包内容 hash 不包含自身 hash，避免循环哈希。

新 seed 是明确登记的基线资源，回执为 baseline；自动生成候选只有原件 protocol 通过后才走组件激活。基线加载可用不等于预测有效。

现有 `select_registered_spot_research_recipe` 只接受认证的原 pack 配置并拒绝任意参数，不能直接承担新技能执行。拟建 `select_registered_skill_workflow` 作为已安装纯函数，严格读 SkillVersion/编译产物并返回具 hash 的节点合同；在 `declare_market_components` 登记具名 callback 和完整源码依赖。新 `SkillRecipeOriginalProtocol` 核技能包、输入/加载/消费/评价原件，不能把旧 RecipeOriginalProtocol 的 spot-pack 资格复制给技能。

## 5. DSH actual-loaded 接口与四阶段回执

选定实现：新增薄的 `decision_hub_skill` 工具，沿用当前 `defineTool`、session 血缘解析和 HubClient。它只访问既有 Run 已冻结技能/节点，提供 list、load、execute_step；不是安装器或 AgentRuntime。既有原生 skill 插件继续兼容历史静态技能，但无精确身份的调用标记 `legacy_skill_unversioned`，不补造新回执。

拟建内部端点沿用现有 Host bridge 认证：

| 接口 | 责任 |
|---|---|
| `GET /internal/research/sessions/{trusted_session_id}/skills` | 读取该 Run 已冻结目录；返回包版本/hash、触发说明和排除原因，不跟随新 pointer |
| `GET /internal/research/sessions/{trusted_session_id}/skills/{version_id}/package` | 重验 archive manifest/各文件、schema、scope、catalog 后返回确切方法资源 |
| `POST /internal/research/sessions/{trusted_session_id}/skill-use-events` | 接受 Host/服务原件的阶段事件；运行身份、callId、native seq 不取自模型文本 |
| `POST /internal/research/sessions/{trusted_session_id}/skills/{version_id}/steps/{node_id}` | 校验冻结节点/依赖/输入并保存节点绑定；纯计算调用已有组件消费者，搜证节点只返回应调用的现有研究工具合同，不代发隐藏网络调用 |

模型仅可给冻结目录里的 `version_id/node_id` 和合同允许的业务输入。run_id、generation、root/child session、call_id、request_hash、权限、canonical scope 由 runtime/Hub 推导。

搜证节点继续由 DSH 真实调用 `decision_hub_research`。Host skill-binding 将该 native call/result、实际 canonical query、ProviderToolEmission 与预先冻结的节点合同绑定，再重读 Gateway 的原件完成 executed 阶段。当前调用名/参数证明是具名的，不能在 `decision_hub_skill` 内部嵌套调用 Gateway 却声称产生了原研究工具的 native 调用；也不能仅放宽 accepted-tool 名称表来绕过证明。需要扩展关联 DTO 时新增版本，原 query 的来源/时间/权限检查仍完整执行。

拟建 `research-skill-use-receipt.v1`：

```text
use_id, run_id, generation, root_session_id, execution_session_id
skill_version_id, package_hash, workflow_hash, method_binding_hash
stage: selected | loaded | executed | consumed | failed
stage_request_id, parent_event_ref, original_ref, original_hash, first_known_at
selected: selection_ref/hash, pointer_generation, reason, excluded_candidates
loaded: native_tool_call_id, native_result_seq, returned_package_hash,
        loaded_body_hash, provider_input_ref/hash, exact_method_block_hash
executed: node_id, input_refs/hashes, gateway_call_ref或component_audit_ref,
          output_ref/hash, actual_status, error_code
consumed: consumer_ref/hash, output_refs, hypothesis/action/monitor-proposal引用位置
```

成立条件：

1. **selected**：Hub 已保存且重读冻结选择原件；不是 DSH 说“我选择了”。
2. **loaded**：Host 取到该工具真实返回的包与方法文字；在正常 DSH 下一次 official model dispatch 冻结 options 中核对同一文字/hash及 native tool call/result；Hub 重读独立 provider-input 原件。返回目录、HTTP 200 或文件存在只能证明 delivered，不能标 loaded。
3. **executed**：节点真实调用的 Gateway/组件原件与 input/output hash 相符。只读方法技能可以 loaded 后直接被消费，不要求捏造执行步骤。
4. **consumed**：综合/假说/行动或 monitor proposal 的结构化引用绑定该输出与用途；原 synthesis 读取器验证。文字自述“参考某技能”不够。引用可证明使用位置，不能证明模型理解或该技能带来因果收益。

每阶段 append-only，失败亦保存。拟新增`research_skill_use_events`保存上述小型身份字段、payload/original ref/hash；`node_id`为NOT NULL，非节点阶段使用空字符串，避免SQLite NULL唯一键允许重复。SQL唯一键 `(use_id, stage, node_id, stage_request_id)`；同请求同hash返回原件，异hash拒绝。聚合状态由GET投影，旧事件不更新成成功。SkillVersion正文仍在既有组件archive；Reflection仍用不可变role原件。

技能方法块放在冻结 method-resources 段，事实仍在 evidence/facts 段。`retained-context.ts` 现有“材料都是 evidence 不是 instructions”的块不直接塞方法指令；补独立具身份的方法块与输入认证，不改变 authority/权限语义。

## 6. Experience→MarketCase/Skill 的桥

复用 `ExperienceRecord` 与 `EvolutionAssetService.create_experience`。当前 writer 已校验 Evidence、Outcome、Evaluation/ExperimentResult 关联；`verified` 可手动设置，故 status 只是展示/治理状态，不可作为市场资格。

拟建 `ExperienceCaseBridgeService.resolve_in_session(reference, cutoff, scope)`：

1. 读取 `experience:<id>`，重建 payload hash，验证 status、领域和适用条件。可用时点取 `max(experience.created_at, experience.available_at, 原件 known_at)`；不按引用较早就把后来写的经验回填历史。
2. 从 Outcome→Forecast→Artifact/Run/实际 Outlook/Revision 解析血缘；找不到 Outlook/Revision 原件则记录 `experience_case_lineage_unavailable`，不制造市场案例。
3. 使用 MarketCaseService 的真实 assessment basis/标签/时间资格读取器。原件合格时捕获/复用具名 MarketCase；否则保留 operational_failure/product_feedback/model_error 等诊断分类。
4. 新增轻量关系 `experience_case_skill_links`：experience_id/hash、case_id/hash、candidate_skill_version_id/hash、bridge_protocol_hash、available_at、disposition、reason。保存关系，不另造一套经验摘要库。
5. `EvolutionContextService._resolve` 增加 `experience:` 与 `market_case:` 分支；冻结 included/excluded 全部引用与原件身份。方法候选可消费诊断经验；预测/策略评价只能使用满足其协议的市场案例。
6. 普通研究接线保留现有 context_inputs_provider，通过具类型组合器增加技能与经验桥结果；历史案例作为 context_only、技能作为 method_resources。不能靠模糊查询或 source_id 伪装当前官方事实。

已有合格 MarketCase 可直接成为候选包的 `origin_refs`，不为了增加 Experience 数量伪造旧 OutcomeRecord。经典 ExperienceCreate 仅认旧 Outcome/Evaluation 路径；若未来需要由新路径/Outlook 主动创建经验，必须新增 V2 original-ref union 及严格 reader，旧 writer 保持兼容，此项不能隐含称已解决。

## 7. 四条固定 rule 之外的声明式 workflow

本目录已提供 `skill-workflow.v1` 的唯一离线设计参考：[workflow.schema.json](spec/workflow.schema.json)、[全合成示例与 golden cases](spec/workflow.example.json)、[workflow_reference.py](spec/workflow_reference.py)。它们与行动条件 DSL 分开：条件 DSL 检查事实是否触发行动；workflow DSL 检查技能节点、能力边界和数据依赖。下列七项 op 名现已固定在 schema 与参考模块的 OP_CATALOG 中：

| op | 执行归属（待接入） | 离线合同检查 |
|---|---|---|
| `fact.select` | Hub 既有 FactStore/lineage 的只读选择 | scope、输入/输出 schema、绑定字段 |
| `evidence.acquire` | DSH 发原 research 工具，经既有 Gateway | 非空 capability_id、冻结 catalog、调用准入与 capability 合同 |
| `component.compute` | 既有已安装纯函数组件消费者 | 已安装 component_ref、组件输入/输出合同 |
| `case.retrieve` | 既有 MarketCaseService/context | 案例请求 schema；真实 PIT/资格由运行 reader 复核 |
| `hypothesis.check` | DSH 当前推理循环 | 前序事实/反证/案例输入绑定和假说输出 schema |
| `decision.compose` | DSH 当前综合与审核 | 假说输入和决策输出合同；不直接交易 |
| `monitor.propose` | 现有监测 proposal 准入 | 决策输入和提案输出 schema；不直接安装监测 |

`validate_workflow(workflow, admission)` 校验 Draft 2020-12 schema、固定 op、scope/catalog、外部传入的允许 capability、已安装组件、重复节点、缺依赖、DAG 环、绑定所引用的祖先节点及声明字段、显式基本类型兼容、literal 类型和必需输入/exports，返回拓扑校验顺序。`admission` 必须由正式 reader 产生；示例的 admission 是合成测试数据，workflow 声明 required_capabilities 不能自行授权。

参考代码不是运行器：它不执行节点、不生成技能、不调用网络/模型、不开应用数据库；排序仅为验证元数据。`run_checks()` 返回 `[{id, passed, expected, actual}]`，供本目录 `verify.py` 集成。当前只支持显式类型和对象属性路径，拒绝 catalog 的 `$ref/$dynamicRef/$recursiveRef`，防止触发远端 schema 读取；它不证明单位、实际输出值、PIT、原件、身份、预算、model 理解、金融效用或生产权限正确。这些仍按 S1–S6 接入真实边界。

离线自测：使用项目现有 `.venv/Scripts/python.exe -X utf8 -B .../spec/workflow_reference.py`，实际退出码 0、17/17 golden 通过（两条正控及未知 op/能力越权/缺依赖/环/前向绑定/未知输出字段/缺输入/重复节点/类型/catalog/scope/组件/schema 等负控）。本轮没有运行生产技能验收。

- 节点只引用上述固定 catalog。新增 op 必须新增合同、安装 adapter、冻结 catalog 和正负验收；没有任意代码/任意工具的兜底 op。
- 编译时验证 node 唯一、DAG 依赖、input/output schema、单位、scope、时间约束、引用字段和依赖资源完整。条件是 typed expression，不执行 Python/JS、`eval`、shell 或包路径。
- 编译结果为不可变节点清单与合同 hash。DSH 当前 loop 选择 ready node 并调用；Hub 验证父节点输出及冻结方法，禁止替换 op、target/source/scope。Node 状态只是该 Run 的回执投影，不是新队列。
- 外部 IO 节点由 DSH 发原 `decision_hub_research` 工具调用进入既有 `DurableResearchCapabilityGateway.invoke_live`，沿用已批准源、调用预算、取消与期限；纯函数交给现有 component consumer。模型推理节点仍由 DSH 执行，无 Hub 内部“再启动一轮”模型。
- 网络/模型执行期间不持 SQLite writer。用既有 reservation→外部执行→短 writer 保存/独立回读模式；分支未执行、依赖失败和部分成功全部记录。
- 旧 `CandidateConfigurationRuntime` 保留原四 rule 解析。新 workflow 是独立 `research_recipe` 方法版本，不能仅给旧 candidate_type 增加自由文本或放宽 RULES。

第一条种子链建议：时点/单位核对→宏观传导假说与反证搜证→历史案例差异→路径风险/概率基线结果→行动触发/失效条件→监测 proposal。只对当前合法能力编译；缺源明确返回 gap，并保存其影响及继续条件。

## 8. 依赖、缓存、并发与索引

| 对象 | 缓存键与规则 |
|---|---|
| 包原字节 | `resource_hash`；archive.read 重验字节。禁止以 skill_id/latest 或目录 mtime 代替身份 |
| 编译结果 | `(workflow_hash, compiler_hash, input_schema_hash, output_schema_hash, op_catalog_hash, dependency_manifest_hash, canonical_scope_hash)`；代码/schema/catalog改变即另一个结果 |
| 选择 | `(run_id, generation, request_hash, frozen_selection_hash)`；只复用该 Run 原件，不把当前 pointer 静态缓存为永久资格 |
| 节点纯计算 | `(skill_version_id, node_id, dependency_manifest_hash, input_hash)`；跨 Run 复用纯结果仍为新 Run 写真实消费回执，并检查该 Run 的 cutoff/许可 |
| 网络事实 | 复用既有 research_reuse/FactStore/Capture/PIT；不能根据 query 相同直接复用已过期事实 |
| 案例/经验 | `(canonical_scope_hash, cutoff_at, bridge_protocol_hash, selected_original_hashes)`；每次资格来自真实原件，不缓存 verified=True |

并发：同加载请求可合并不可变字节读取，但 root/child 每份 input dispatch 与回执独立；同 step idempotency 只执行一次并保留 in_progress/unknown，未知提交先查原回执，不换 key 重提。不同 DSH session 不能依赖全局 `DECISION_HUB_SKILL_DIR` 热切换，同一个已冻结包不能被另一 Run 的最新目录替换。

索引：复用 component version/scope、pointer CAS、market_case PIT 索引；新增 `ix_skill_use_run(run_id,generation,first_known_at)`、`ix_skill_use_version(skill_version_id,first_known_at)`、`uq_skill_use_request(use_id,stage,node_id,stage_request_id)`；桥关系 unique 对齐 experience hash+case hash+candidate hash，按 available_at 和 scope 查询。阶段引用必须指向唯一原件。

## 9. 拟建错误码与恢复语义

| 错误码 | 含义/恢复 |
|---|---|
| `skill_package_hash_mismatch` / `skill_resource_missing` | 不能加载；保留坏原件或缺件，不改 hash 为新文件 |
| `skill_scope_not_admitted` / `skill_dependency_unavailable` | 资产/工具/组件尚未准入；返回缺口，不继承 BTC 权限 |
| `skill_workflow_invalid` / `skill_op_not_installed` | DSL/类型/依赖环/单位或 op 错误；候选拒绝编译 |
| `skill_selection_binding_changed` | request/generation/包/原选择身份变化；停止当前加载，不切 latest |
| `skill_loaded_original_missing` / `skill_provider_input_mismatch` | 已送达但无实际 model input 证据，或字节不符；只能记 delivered/failed |
| `skill_step_dependency_not_ready` / `skill_step_input_mismatch` | 不执行节点，重读原依赖回执；不能以模型自述补齐 |
| `skill_use_request_reused` / `skill_consumption_original_missing` | 幂等 key 异 payload 或消费没有独立原件；拒绝写成功 |
| `experience_after_cutoff` / `experience_case_lineage_unavailable` / `experience_case_ineligible` | 排除该经验/案例并说明原因；诊断仍可展示，不升级为市场标签 |

工具/Provider/native 超时或取消继续使用既有 provenance/error code；不全部映射成“技能失败”。无新无限重试规则。

## 10. 六条正负验收（必须有实际证据）

1. **版本冻结**：真实 Run 选旧版本，指针随后切新，原 Run load/execute仍使用旧 hash；修改一个包文件、缺文件、跨目录路径均拒绝。核 Run/Method/包 hash，不能只比目录名。
2. **加载原件**：正常工具返回后，在真实 DSH 下一次 model dispatch 中读取精确方法块，写 loaded 原件；仅 list、HTTP 200、generic skill成功、缺 native result 或删改 provider input 均不能得到 loaded。
3. **节点执行与权限**：一条 workflow 实际使用已有 Gateway+pure component且逐步输入输出能重读；未知 op、shell、跨资产源、未来Fact/修订、错单位、跳过父节点均拒绝；DSH loop只有现有一套。
4. **消费与反馈**：最终综合真实绑定技能输出并产生独立 consumed 回执；只在正文提技能名不能通过。结算记录和无技能对照要能重读；报告保存成功不能替代技能金融价值。
5. **经验桥/PIT**：现存经验血缘可核时映射真实case/候选；无真实Outlook、owner verified但无标签、跨资产、未来available_at保持排除。关闭记忆的对照、原case与experience hash均保持，不回填旧Run。
6. **并发/故障/升级**：同 key 两调用只执行一次，跨root/child/run不串包；取消/重启/未知提交能查原回执。候选有失败样本仍计全分母；未过protocol不得activate，已激活退化能走原CAS回滚并保留旧版。

新增测试建议：`tests/research/test_skill_package.py`、`test_skill_workflow.py`、`test_skill_use_receipts.py`、`test_experience_case_bridge.py`，以及 `extensions/dsh/decision-hub/tests/skill-binding.spec.ts`。纯合同控制通过后，仅执行一条受控真实种子链证明 transport→actual input→实际节点→综合引用；此前未启动该验收。

## 11. 开源方法的采纳边界

- [Reflexion](https://github.com/noahshinn/reflexion)：MIT；借 execution-feedback→episodic-memory，研究任务成绩不代表金融效果。
- [Voyager SkillManager](https://github.com/MineDojo/Voyager/blob/main/voyager/agents/skill.py)：MIT；借 code/description 分离和任务检索，补本项目不可变版本与独立结算，旧Minecraft/Chroma/模型客户端不整体接入。
- [ACE](https://github.com/ace-agent/ace)：Apache-2.0；借条目ID与增量变更。helpful/harmful是Reflector标签，不是交易收益；当前删除/合并等操作仍有TODO。[原论文金融任务及局限](https://arxiv.org/html/2510.04618v3)
- [GEPA Adapter](https://github.com/gepa-ai/gepa/blob/main/src/gepa/core/adapter.py)：MIT；在评价器可信后用逐样本outputs/scores/trajectories评估候选。第一切片不引入其模型客户端或生产loop；优化必须保留失败和所有候选的完整分母。

本轮采纳这些机制的合同设计，未拷贝源代码。未来复制小模块需锁上游提交、保留许可证/归属、检查依赖和本地合同测试；软件许可证不授予数据源、模型或交易权限。
