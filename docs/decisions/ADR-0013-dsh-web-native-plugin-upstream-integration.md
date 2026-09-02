# ADR-0013 DSH Web 原生插件与上游升级集成策略

日期：2026-08-31
状态：`accepted`（owner 于 2026-08-31 授权按方案建立阶段目标、实现并自测）
关联方案：[DSH-first 产品实现总方案](../product/DSH_FIRST_IMPLEMENTATION_BLUEPRINT.md)
修订关系：接受后修订 ADR-0005 的 Workbench/UI 定位和 ADR-0008 的 SDK-only candidate 运行方式；保留 Core/Harness 隔离、PIT、Gate、账本、插件 deny-by-default 和人工 Promotion 原则。

## 决策摘要

Decision Hub 直接复用固定版本的上游 DSH Web 作为 Agent 交互主壳，不复刻 Chat、Session、Trajectory、Tool、Subagent、Skill、插件清单和 JSONL 导出前端。

Decision Hub 的 DSH 集成以官方原生形式交付：

```text
@decision-hub/dsh-plugin
  Host plugin
    -> Hub task/session bridge
    -> Hub MCP/API tools and commands
    -> run_id <-> dsh_session_id correlation
    -> completion/failure/cancel callback

  Client plugin
    -> dsh.client / platform=web
    -> Decision Run/Evidence/Gate/Horizon/Report nodes
    -> Hub status/settings/admin links

@decision-hub/dsh-bundle
  dsh.bundle -> cordis.patch.yml
  -> 在固定 web profile 上挂载 Host/Client plugin
```

上游源码不复制进业务目录、不在 vendor 中静默改写。需要构建/调试上游时使用固定 commit 的 Git submodule 或独立 checkout；本产品变更只进入 `extensions/dsh/`、`overlays/dsh/`、`infra/dsh/` 和现有 Hub adapter。

现有 `deepseek-harness-sdk` Python 适配器保留为版本 handshake、隔离 canary、replay/holdout 和显式 fallback。正式候选运行方式从“worker 每次启动 SDK profile 子进程”演进为“Hub durable Run 调度常驻 DSH Web Host 中的普通 Session”。

## 背景与代码事实

当前 Hub 已经真实调用 DSH，但调用的是 SDK profile，不是 Web Host：

1. `packages/runtime_adapters/dsh_runtime/client.py` 固定 `deepseek-harness-sdk==0.1.1rc1`，导入 `DeepSeekHarness`。
2. adapter 传入 provider/model/workspace/session root/restricted Cordis profile/MCP URL，并按确定性 `session_id` 执行 `handle.run()`。
3. `DshResearchRuntime` 把 DSH notifications、Tool/Subagent 和最终输出映射为 canonical Result/Trace。
4. DSH Session JSONL 已写入本地 session root，但当前默认前端是 Decision Desk/replay，不是上游 DSH Web 实时会话页。

这解释了为什么“代码中已经接入 DSH”与“用户看不到 DSH 原生 UI”可以同时成立：复用的是 SDK Harness 执行能力，还没有复用 Web Host 和 client plugin surface。

## 官方上游核对

2026-08-31 对 DSH `master` 的只读核对确认：

- DSH 基于 Cordis，模型、工具、Session、Agent Loop、Subagent、Web 和 UI 都是插件。
- `web`、`headless`、`sdk`、`sdk-minimal`、`acp` 是 profile/组合入口；`web-app` 自身以 `dsh.bundle` 指向 `cordis.patch.yml`。
- Web 由大量 client plugins 组成，包括 Session、Trajectory、Plan、Tool、Subagent、Skill、Workflow、插件 Inventory 和设置卡。
- 外部包通过 `package.json` 的 `dsh.client` 声明浏览器插件，通过 `dsh.bundle` 声明组合 patch；官方设置卡指南明确无需改 Web App 源码即可动态接入。
- Session Controller 提供创建、恢复、历史、follow、prompt、skill 和实时 snapshot seam；Trajectory 从持久 Session event 投影。
- Webhook Runtime 可以把受信任外部 delivery 创建为普通 Workspace Session，但当前是 fire-and-forget，无内置去重、完成结果或 durable retry。
- 当前上游仍快速演进，仓库 package 为 `0.1.2-alpha.2`，本项目锁定 SDK 是 `0.1.1rc1`；不能假设内部 API 零破坏升级。

## 最终拓扑

```text
Browser
  -> DSH Web (upstream, pinned)
       -> Decision Hub client plugin
       -> upstream Session/Trajectory/Tool/Subagent/Skill UI

Hub API / realtime worker
  -> durable Event/Run/outbox
  -> authenticated DSH Host bridge plugin
       -> create/resume DSH Session
       -> DSH Agent Loop
       -> official DSH plugins + Hub research MCP
       -> session/agent events
  <- accepted/completed/failed callback
  -> Evidence Gateway / PIT / Gate / Ledger

Decision Desk admin
  -> Operations / Sources / Capability audit
  -> Ledger/PIT/Evaluation/Promotion/Rollback
```

DSH Web 负责“Agent 正在发生什么”；Decision Desk 负责“业务上发生了什么、是否可信、后来是否正确、哪个版本被晋级”。两者通过 `run_id`、`dsh_session_id`、trace ref 和 Query/View 链接，不复制完整会话或业务账本。

## 官方插件与产品 Capability 的关系

本项目不再发明第二套插件安装格式：

- DSH 插件由 `dsh plugin`、profile、`dsh.bundle` 和 `dsh.client` 安装/组装/卸载。
- Decision Hub `CapabilityManifest` 只是正式产品准入和审计记录，不是插件运行时或安装器。
- DSH-only UI/Skill/临时工具可以只存在于工作台，不需要 Hub 映射。
- 会影响正式 Evidence/Gate 的插件必须通过稳定 Tool/MCP/SDK seam 输出 schema，并由 Hub 增加 server-owned 时间、PIT、authority、hash、failure provenance 和预算。
- 插件不能直接写 Hub Event、Evidence、Forecast、Outcome、Gate 或 active pointer。

如果一个官方插件的输出已经符合 canonical capability schema，只增加通用 binding；只有领域数据格式不同才增加薄 result mapper，不复制插件逻辑或 Agent Loop。

## 上游源码和二次开发策略

推荐目录：

```text
vendor/deepseek-harness/             # 可选 submodule，只读固定 commit
extensions/dsh/decision-hub/         # Host + Client 官方格式插件
overlays/dsh/                         # profile/patch/config
infra/dsh/                            # 构建、容器、健康和升级脚本
packages/runtime_adapters/dsh_runtime # Python canary/fallback adapter
apps/decision-desk/                   # Hub 管理后台
```

规则：

1. 默认使用上游发布包/固定镜像 digest；只有缺少可消费构建物或需要调试时才引入 submodule。
2. 业务代码不 import `vendor` 内部源码，只依赖官方 package exports、SDK protocol、MCP 或经过 ADR 锁定的 seam。
3. 产品 patch 放在 overlay；不得在 submodule 内形成未记录修改。
4. 如果确实必须修 DSH 上游 bug，优先提交上游 PR；临时 patch 单独编号、测试、记录删除条件。
5. 不使用 Git subtree 或复制源码作为默认同步方式，因为它会把上游历史和业务 diff 混合。

## 常驻 Host Bridge 语义

官方 Webhook Runtime 不能直接满足 durable 产品要求，因此 Decision Hub Host plugin 至少拥有以下公开契约：

```text
submit(run_id, request_hash, prompt_ref, preset_ref, permission_ref)
  -> accepted(dsh_session_id, accepted_at)

status(run_id | dsh_session_id)
  -> admitted | running | idle | completed | failed | cancelled

cancel(run_id, reason, expected_generation)
  -> cancelled | already_terminal | conflict

completion callback
  -> run_id, session_id, terminal_status, last_seq, trace_ref, result_ref/hash
```

Hub outbox 负责重试提交；Host plugin 对 `run_id + request_hash` 幂等；DSH Agent/Session event 负责实时执行状态；Hub worker 收到完成回调后才执行 Evidence attestation、Decision Snapshot、Gate 和 commit。崩溃恢复时 Hub 从 durable Run/outbox 重新协调，不依赖 DSH 进程内 job 状态作为业务真相。

## 候选方案

### A. 继续完全自建 Decision Desk 前端

否决为日常 Agent 主界面。它会重复 DSH 已有的 Session、Trajectory、Tool、Subagent、Skill、插件和 JSONL 体验。Decision Desk 仅保留管理后台职责。

### B. Fork/复制 DSH 前端再二次开发

否决为默认方案。短期直观，长期会在每次上游 UI、Cordis、Session contract 和 client module 变化时产生大规模 merge。只有官方插件扩展点无法满足一个已确认核心需求时，才以独立 ADR 评估最小 fork。

### C. 上游 DSH Web + 原生 Host/Client Plugin + Hub 管理后台

选择。最大程度复用官方 UI/Session/插件生态，同时保留产品账本、PIT、Gate、Outcome 和版本资产。

### D. 仅使用 Python SDK 子进程并 deep-link DSH Web

保留为过渡/canary，不作为最终主线。共享 session root 是否支持跨 profile 实时 follow、并发持久化和 Web 自动发现必须单独验证；不能默认认为 SDK 子进程的 Session 会无缝出现在常驻 Web UI。

## 升级、兼容和回滚

不能承诺“DSH 更新后零修改直接替换”，因为官方仍处于 alpha/developer-preview。能承诺的是业务层不重写、升级受控：

```text
新 tag/commit/digest
  -> license/SBOM/hash
  -> web/profile/plugin discovery handshake
  -> Host bridge contract
  -> Client plugin build/load/HMR smoke
  -> Session create/resume/history/follow/JSONL export
  -> Tool/Skill/MCP/Subagent/Trajectory smoke
  -> Hub replay/holdout + PIT/Gate regression
  -> owner review
  -> promote or rollback pinned version
```

版本登记必须包含 upstream URL、commit/tag、package versions、image digest、Python SDK/runtime、profile hash、插件 manifest/build hash、contract suite 和回滚目标。升级失败继续运行旧 DSH 和旧 Hub active pointer，不能让 dependency bot 自动晋级研究 Runtime。

## 迁移步骤

本 ADR 接受后仍先写 Stage Charter，不直接重构：

1. 固定一个上游 DSH commit/package set，验证官方 Web 可独立构建启动和 Session/Trajectory/JSONL。
2. 创建最小 `@decision-hub/dsh-plugin`，使用官方 `dsh.bundle`/`dsh.client` 格式，只显示 Hub readiness 和一个只读 Run 节点。
3. 完成 Host bridge 的幂等 submit/status/cancel/completion contract；使用 Fake Hub/DSH 测试，不先接真实市场研究。
4. 将一个 replay Run 投影为 DSH Session，验证 Web 实时轨迹和 Hub ledger correlation。
5. 用一个真实但只读、限时事件做 shadow；与现有 Python SDK candidate 比较恢复、延迟、工具、Session 和页面体验。
6. owner 决定 `promote_web_host_bridge`、`retain_sdk_candidate` 或 `stop`；没有价值证据不迁移 active pointer。

历史 Run/Evidence/Artifact/Forecast/Outcome、现有 migration 和 Decision Desk admin 不重写。当前 Python adapter 在整个迁移期保留。

## BDD/TDD 验收

```text
Given Hub 已 durable admission 一个唯一 run_id
When Hub 重复提交相同 request_hash 到 DSH Host bridge
Then 只创建一个普通 DSH Session
And DSH Web 能实时显示该 Session 的 Plan/Tool/Subagent/Trajectory
And Hub 记录同一个 dsh_session_id
```

```text
Given DSH Host 在 Agent 执行中重启
When Hub 恢复未终态 Run
Then 系统恢复或明确重建 Session，不重复 commit
And Session JSONL/历史与 Hub Ledger 保持可追溯关联
```

```text
Given 一个 DSH 官方插件只安装在工作台
When 它没有通过 Hub CapabilityManifest 准入
Then Agent 可以在人工工作台按 DSH 权限使用它
But 它的结果不能直接成为正式 Evidence 或通过 Publish Gate
```

必须覆盖：插件安装/卸载、Client bundle 加载、Host/Client 版本不匹配、Session 创建/恢复/follow、JSONL export、Tool/Subagent、Hub API/MCP 超时、重复 delivery、完成回调丢失、取消冲突、权限拒绝、DSH 升级回滚和 Hub fail-closed。

## 后果

正面：直接获得 DSH 原生会话、轨迹、插件、Skill 和上游 UI 升级；Decision Hub 代码集中在真正的业务资产；未来 Role/领域能力可以按 DSH 官方插件生态组装。

负面：需要新增一个 TypeScript/Cordis Host+Client 插件和长驻 DSH 服务；外部 client bundle 当前要复刻官方构建格式；Host bridge durable completion 不能只靠官方 fire-and-forget Webhook，需要明确实现和测试；DSH alpha 更新仍可能要求适配。

## Owner Gate 结果

Owner 于 2026-08-31 接受本 ADR，并授权 [DSH Native Web Product Core](../stages/DSH_NATIVE_WEB_PRODUCT_CORE.md) 的离线核心实现。阶段 Gate 继续禁止：

- 不废弃 Decision Desk 或 Python SDK adapter；
- 不切 active pointer；
- 不安装社区插件；
- 不执行新的 live canary。
