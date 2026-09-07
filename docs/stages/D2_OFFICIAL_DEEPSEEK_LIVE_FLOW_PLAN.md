# D2 官方 DeepSeek Live 主流程跑通计划与验收清单

版本：`STAGE-D2-DEEPSEEK-LIVE-2026-09-02.v0.1`  
状态：`engineering flow passed / synthesis failed safely / product direction pending`  
前置：[DSH Native Web Product Core](DSH_NATIVE_WEB_PRODUCT_CORE.md)、[R2-R-07 Search Reliability](R2_R_07_SEARCH_RELIABILITY_ERROR_PROVENANCE.md)、[DSH 与 Hub 边界指南](../product/DSH_AND_HUB_BOUNDARY_GUIDE.md)、[TDD/SDD 自测规范](../engineering/TDD_SDD_SELF_TEST_STANDARD.md)

## 1. 目标

在单机隔离环境使用官方 DeepSeek API 配置，让官方 DSH Web 通过现有 Decision Hub 插件和已批准的研究能力完成一次真实文本研究闭环，并把可复核证据投影到 DSH 页面：

```text
DSH Web Session
  -> official DeepSeek model call
  -> DSH Agent Loop / research tool
  -> Research Capability Gateway
  -> typed Evidence / PIT / Sufficiency
  -> code Gate
  -> Run / Artifact / report projection
  -> DSH conversation + research report
```

本任务只验证主流程是否真正可用，不声称预测准确率、盈利、实时 Search 全覆盖或自动交易能力。

## 2. 约束与不做

1. DeepSeek Key 只能保存到 Git ignored 的 `data/dsh-live/.env`，权限 `600`；官方 DSH 禁止把网络路由变量放进该文件，因此 Base URL 只在 Git ignored 的 `data/dsh-live/settings.yaml` 中配置。密钥禁止进入源码、Markdown、JSONL、SQLite、截图、日志、提交和远程仓库。
2. 官方 Base URL 默认是 `https://api.deepseek.com`。Provider 适配器保留 OpenAI-compatible 接口，后续 GPT 只新增配置，不改 Core 契约、Agent Loop、Gate 或账本。
3. DSH 仍是唯一 Agent Runtime 和 Session/Trajectory 所有者；Decision Hub 不新建第二套 loop，不复制 DSH Web 源码。
4. Domain/Crypto Macro、Evidence、PIT、Sufficiency、Gate、Run 和 Artifact 仍由 Hub/Core 所有；DSH 只提交候选并调用已挂载 capability。
5. 研究只读、单 owner、单机；不启用自动交易、扣费、修改 active pointer、外部通知或第二领域。
6. `deepseek-v4-flash` 必须先通过官方账户/DSH Provider 实际模型探针。若官方账户不接受该 ID，只允许记录明确失败并在同一 Key 下选择官方 `/models` 返回的可用模型；禁止把 `404` 改写成数据不足或成功。
7. 真实 Search、Official、Market 能力逐项保留其错误、PIT 新鲜度和授权边界；一个能力失败不能抹掉其他已完成结果。

## 3. 实施阶段

### D2-A 文档与环境准备

- [x] 确认本计划与 `CURRENT_STATE`、`CURRENT_DECISIONS`、Roadmap、Changelog 的阶段状态一致；模块 README 在实现/验收收口时同步。
- [x] 检查 `data/dsh-live/.env` 被 `.gitignore` 覆盖，权限为 `600`；只记录变量名，不输出变量值。
- [x] 写入测试凭据：`DEEPSEEK_API_KEY`；`DEEPSEEK_BASE_URL` 不放入 `.env`，官方 URL 在 ignored DSH `settings.yaml` 中固定为 `https://api.deepseek.com`。
- [x] 保留现有 `codexai-gpt55` 环境和 settings 备份作为未来显式 Provider，不删除、不改写历史 Session。
- [x] 记录本次隔离端口、Compose project、DSH Home 和 runtime mode；原始运行数据保留在
  gitignored 本机目录，脱敏结论见 [D2 真实验收记录](../evaluations/DSH_DEEPSEEK_LIVE_FLOW_ACCEPTANCE_2026-09-03.md)。

### D2-B Provider/模型探针

先 Red 后 Green，探针不得创建业务 Run：

- [x] 官方 `/models` 请求成功：HTTP `200`，返回 3 个模型，目标模型明确在目录中；只记录模型 ID，不记录凭据/响应正文。
- [x] 对 `deepseek-v4-flash` 发起最小文本请求：HTTP `200`，响应模型一致且存在 choice；不创建业务 Run。
- [x] 目标模型可用，无需 fallback；旧中转 `404` 保留为配置污染失败样本，不改业务协议。
- [x] Provider 错误分类契约已有 `provider_config_error`、`provider_auth_failed`、
  `provider_model_unavailable`、`provider_timeout` 和 `provider_rate_limited`；本次目标模型
  没有触发 fallback，未把 Provider 失败改写成 `research_only`。
- [x] live 启动器在凭据缺失或 live/replay 配置冲突时 fail-closed；模型目录探针和最小请求
  在 DSH Web 启动前完成，凭据存在但模型不可用时不得报告可研究。

### D2-C DSH 真实主流程

- [x] 启动 Hub API、research MCP/worker 和官方 DSH Web；Hub readiness 通过，MCP 业务调用成功。
- [x] 页面使用官方 DSH Web 唯一入口并进入 `Crypto Macro Trader` 工作区；运行模式为 `live`。
- [x] 从“建立研究任务”提交了包含事件身份、政策变化、宏观传导、BTC 现货、衍生品和反方链的文本目标。
- [x] 观察到 `accepted -> queued -> researching -> terminal` 的耐久链路，而非瞬时完成。
- [x] DSH Session/JSONL 与 Hub Run 关联一致；本次 link generation 为 3，last sequence 为 38035。
- [x] DSH Agent Loop 在 hard gap 存在且预算允许时继续完成第 2、3 轮补证。
- [x] capability 的成功结果、工具调用和失败 provenance 独立持久化；没有把工具失败泛化成成功结论。

### D2-D 业务事实和页面投影

- [x] Run、Evidence、PIT cutoff/observed/received 三时间戳、Coverage、Gate、Artifact 通过
  canonical schema 和 deterministic Gate；本次 hard coverage 为 66.7%。
- [x] 只有经过 attestation 和代码 Gate 的证据进入有效 Evidence；未认证 synthesis 被降级为 evidence-only。
- [x] 降级结果保留事件、Evidence 数、Coverage、Gate、失败原因和审计入口；不发布方向性 horizon。
- [x] 失败/终态页面状态包含真实业务状态、失败能力和可重试性；不把 Provider 或 synthesis 失败显示为成功。
- [x] `run_id=null` 的空 Session 不显示研究报告生成卡。
- [x] `/report` 详情暂不可用但 status 已终态时，页面使用 status fallback；不制造方向性报告。

### D2-E 三类终态回归

- [x] Provider/model failure 的 replay 回归显示 Provider 失败，而不是证据不足。
- [x] Partial/insufficient 保留已完成 Evidence 和每项失败，Gate 为 `research_only/reject`，不发布方向结论。
- [x] Success/degraded replay 与本次 degraded Run 的 Run/Session/Tool/Evidence 数量一致，Gate 由代码裁决。
- [x] 重复提交同一 `Idempotency-Key` 不产生第二个 Run；retry 只创建 child Run，不改写父 Run。
- [x] DSH/worker 重启恢复回归已通过；本次已提交业务事实保留在持久账本。

### D2-F 质量门和记录

- [x] `pytest -m "not live"` 与受影响 Python 测试通过：`395 passed`。
- [x] DSH plugin `pnpm test -- --run`、build 通过：`57 passed`，Vite build passed。
- [x] Decision Desk frontend test/build 通过：`10 passed`，Vite build passed。
- [x] Ruff、Pyright、contract codegen/check、module docs check、`git diff --check` 通过：Ruff/pyright 无错误，canonical schemas/module docs/diff check passed。
- [x] 本次 D2 浏览器桌面/移动截图与 console 资产已归档；新 bundle DOM 只有一个报告区域，移动视口无横向溢出，console 无 error。停止临时实例后产生的 connection retry warning 已作为环境收口告警单独记录。
- [x] 验收记录保存命令、端口、runtime mode、Provider 名称/模型、脱敏 Run/Session ID、状态、Tool/Evidence/Coverage/Gate 摘要和失败根因。
- [x] 已更新状态、上下文和 CHANGELOG；本次没有新增架构决策或改变 active pointer。

## 4. BDD 退出门

```text
Feature: 官方 DeepSeek 主流程
Scenario: 模型可用且研究链路完成
Given 官方 DeepSeek 凭据和模型探针通过
And DSH live Web 加载 Decision Hub preset
When owner 从页面提交一条带证据要求的研究目标
Then DSH Agent Loop 至少完成一次模型步骤和一次已授权 research capability
And Hub 创建可查询 Run、Session link、Tool、Evidence、Coverage 和 Gate
And 页面显示与业务账本一致的可读终态
```

```text
Feature: Provider 失败不被掩盖
Scenario: 模型或 transport 不可用
Given Provider 探针或首个 model call 返回配置、认证、模型不存在或 timeout
When 研究任务结束
Then Run 显示 provider provenance 和 retryability
And Tool 调用、Evidence、Gate 事实保持诚实
And 页面不得把 Provider failure 显示成 evidence insufficient 或成功 no_trade
```

```text
Feature: 证据不足仍主动尝试
Scenario: 首个 capability 没有结果
Given 一个 hard gap 和至少一个未尝试的 fallback capability
When 首个 capability 失败且 deadline/预算仍足够
Then DSH loop 继续调用下一项允许 capability
And 已完成结果和失败 provenance 均保留
And 只有预算、deadline 或无可用梯子时才进入 bounded fail-closed terminal
```

## 5. TDD 断言

1. `test_provider_probe_does_not_create_run`：探针只验证 Provider，不写 Hub 账本。
2. `test_model_unavailable_is_provider_failure`：模型 404 保留稳定错误码和 retryability。
3. `test_live_intake_reaches_tool_and_terminal_projection`：前端提交可观察到真实 Tool/Run/Report 状态。
4. `test_failed_terminal_report_uses_status_fallback`：终态详情暂不可用时显示事实，不显示生成中。
5. `test_null_run_is_idle`：`run_id: null` 空状态不渲染研究报告。
6. `test_retry_creates_child_without_mutating_parent`：重试幂等、父 Run 不变。

## 6. 停止/回滚

满足任一条件立即停止，不靠局部补丁掩盖：

- 官方账户不支持目标模型且没有 owner 授权 fallback；
- 需要修改 DSH 上游源码、复制第二套 loop、放宽 Gate 或修改历史账本；
- 真实工具能力需要未授权域名、Key、收费服务或不明数据授权；
- 页面只能给出“证据不足”而无法展示 Provider/Tool/Failure provenance；
- 同一失败两次局部修复仍不能得到一致的 Run/Session/Report 事实。

失败时保留隔离实例和脱敏验收证据，停止本地 live 服务；不得删除历史 `data/` 或改写已有 Run。生产切换必须另立 owner Stage Gate，不能由本次测试自动 Promotion。

## 7. 完成定义

本任务只有在以下证据全部具备时才标记 `completed`：

```text
[x] Provider model probe passed (or explicit provider_model_unavailable recorded)
[x] One real DSH live Run reached a truthful terminal state
[x] At least one Agent Loop research Tool call observed
[x] Hub Run/DSH Session correlation verified
[x] Evidence/PIT/Coverage/Gate projection verified
[x] Success/partial/provider-failure states do not conflate
[x] Browser desktop/mobile screenshot and console result saved for this exact D2 run
[x] Unit/integration/frontend/static checks passed for this final working tree
[x] Status/context/changelog updated; no secret or runtime data tracked

完成定义的含义必须保持分层：上面已勾选项证明“真实 DSH 主流程到达诚实终态”；未勾选项表示
本次 D2 仍不能宣称结构化方向分析或产品正式可用。结构化 synthesis 失败属于安全失败，
不是允许通过的方向性结果。
```

在这些条件满足前，产品状态只能写 `live self-test pending` 或 `provider blocked`，不能写“主流程已交付”。
