# Decision Hub 核心价值交付实施书（2026-09-14）

状态：`COMPLETE / LIMITED（仅研究）/ NO-GO（交易与扩展）`  
目标范围：单 owner、手动触发、当前 BTC/USDT 市场快照研究  
交付期限：当前开发目标内连续完成，不新开外围阶段  
价值门：真实结果可读、过程可核验、任何涉及资金的结论默认不授权交易

## 0. 本次到底交付什么

本次只交付一个纵向产品行为：

> 用户在 DSH 输入当前 BTC/USDT 研究问题后，系统取得真实证据，明确告诉用户“当前应继续研究/等待，还是不应行动”，展示可核验的分析步骤、证据、缺口、独立复核和停止原因；Decision Desk 对同一个 Run 提供简洁的用户结果与技术详情。数据不足、概率未校准或过程失败时，界面必须阻止用户把结果理解成交易许可。

本次完成后，验收人不需要阅读 README、运行日志或手册来理解一次研究结果。手册只说明如何启动，不承担解释产品的责任。

## 1. 冻结的验收输入

三次真实验收统一使用以下任务，不在失败后修改题目来取得更好结果：

```text
请对当前 BTC/USDT 做一次可审计的市场快照研究：取得现货价格和成交量、永续资金费率、未平仓量及其变化、基差、订单簿拥挤度、至少两个独立交易所的确认，以及 24h、7d、90d 小时历史；输出 30m、24h、72h 条件化判断。每个事实只引用实际 Evidence，明确宏观和跨资产数据缺口，并给出触发条件、失效条件和下一次复查时间。数据不足或概率未校准时，明确给出不交易或等待确认，不得把研究产物状态写成交易许可。
```

任务类型固定为 `crypto_market_snapshot`。本轮不再混入旧宏观事件分钟回放，也不通过改短题目隐藏 OI 变化和跨交易所要求。

## 2. 成品第一屏

### 2.1 完成态示例

第一屏必须按下面的顺序出现，内容来自本次 Run，示例文字不作为硬编码结果：

```text
操作结论
暂不交易，等待 OI 变化和第二交易所确认

使用边界：仅研究；不构成交易许可
判断可靠性：低（概率未校准，2 项关键数据缺失）

为什么
1. 已确认：现货、资金费率、当前 OI、基差、订单簿和小时历史
2. 相互矛盾：……
3. 尚未确认：OI 变化、第二交易所、实时宏观/跨资产

条件改变
- 满足……后重新评估 30m
- 价格跌破……或资金费率……时原判断失效

下一次复查：YYYY-MM-DD HH:mm:ss
```

### 2.2 运行态

运行中第一屏显示七个用户阶段，不显示原始模型 prompt 或整段工具 JSON：

1. 已接收问题；
2. 已形成计划；
3. 正在取得市场证据；
4. 正在检查数据充分度；
5. 正在独立复核；
6. 正在决定是否补证；
7. 正在形成报告。

每个阶段只有 `等待 / 运行中 / 完成 / 有限制 / 失败` 五种视觉状态。阶段详情最多显示目的、结果、Evidence 数量和错误分类。

### 2.3 失败态

失败时第一屏固定显示：

- `操作结论：无法形成判断，不应交易`；
- 哪一步失败；
- 已保留多少 Evidence；
- 是否可重试；
- 重试会不会产生新的模型或数据费用；
- 原始错误编号放在“技术详情”，不能替代中文说明。

## 3. 资金风险语义

### 3.1 五类状态必须分开

| 层次 | 含义 | 用户显示 |
| --- | --- | --- |
| Run 状态 | 程序是否排队、运行、完成或失败 | `研究完成 / 研究失败` |
| Evidence 状态 | 数据是否新鲜、匹配、独立、可追溯 | `已采用 / 过期 / 拒绝 / 冲突` |
| Artifact 状态 | 研究报告能否写入账本 | `报告已保存 / 报告受限` |
| 决策状态 | 当前证据是否支持进一步人工判断 | `不交易 / 等待确认 / 需要人工复核` |
| 交易权限 | 系统是否有权执行资金操作 | 本产品固定为 `未授权交易` |

后台枚举 `publish` 继续只表示“Artifact 可以写入研究账本”。所有用户界面把它显示为 `报告已保存`，不再显示“可发布”。

### 3.2 确定性行动摘要

新增 canonical `ResearchActionSummary`，由 Hub Query View 基于已落账数据确定性生成。模型不能直接写 `trade_authorized`。

字段：

```yaml
schema_version: research-action-summary.v1
recommendation: do_not_trade | wait_for_confirmation | manual_review_required | unavailable
headline: string
rationale: string
reliability: unavailable | low | limited | reviewable
trade_authorized: false
artifact_status: pending | saved | limited | rejected
data_status: insufficient | partial | sufficient
accepted_evidence_count: integer
stale_evidence_count: integer
rejected_evidence_count: integer
blocking_gap_ids: string[]
warning_codes: string[]
supporting_evidence_refs: string[]
next_review_at: datetime | null
```

推导顺序固定如下：

```text
1. Run/Artifact 不可用、synthesis 被拒或存在未恢复系统失败
   => unavailable；trade_authorized=false

2. hard coverage < 1、存在 hard gap/hard conflict、缺少独立 Review、
   reviewer=rejected/requires_new_evidence，或 Evidence attestation/PIT 失败
   => do_not_trade；reliability=unavailable/low

3. 任一 horizon 概率未校准、所有 horizon 均 neutral/no_trade、
   或存在本任务声明的关键补充缺口
   => wait_for_confirmation；reliability=low/limited

4. 证据和 Review 均通过且存在 calibrated 的方向情景
   => manual_review_required；reliability=reviewable

5. 所有分支 trade_authorized 始终为 false；本目标不实现下单、仓位或资金分配
```

`manual_review_required` 表示可以交给人继续判断，不表示系统建议开仓。

### 3.3 当前已确认的合同错误

`packages/provider_adapters/research/fact_pack.py` 当前对 `crypto_market_snapshot` 将 `derivatives_crowding.required_fields` 从包含 `open_interest_delta` 放宽为只要求当前 OI 点，同时 `minimum_venues` 仍为 1。冻结验收输入明确要求 OI 变化和跨交易所确认，因此当前 hard 100% 不能代表请求得到满足。

P0 必须修正：

- 快照衍生品要求恢复 `open_interest_delta`；
- 现货/衍生品跨交易所要求不得继续以一个 venue 表示完成；
- 数据暂时拿不到时保留真实 gap，行动摘要必须是 `do_not_trade` 或 `wait_for_confirmation`；
- 不为了得到绿色 Gate 降低 requirement importance、minimum venues、freshness、authority 或字段要求。

## 4. 用户可见的分析过程

用户要看到的是可审计过程，不是模型私有思维文本。新增 `ResearchProcessStep[]` Query View，仅从已有 `rounds`、`tool_invocations`、`trace`、`coverage` 和 `reviews` 投影，不增加新的 Agent Loop。

字段：

```yaml
step_id: string
order: integer
kind: intake | plan | acquire | assess | review | replan | synthesize | gate
label: string
status: waiting | running | completed | limited | failed
objective: string
outcome: string
evidence_refs: string[]
tool_call_refs: string[]
warning_codes: string[]
occurred_at: datetime | null
```

投影规则：

- `intake`：Trigger Snapshot/Run admission；
- `plan`：每轮 `ResearchPlan.objective` 和任务数量；
- `acquire`：工具调用按 capability 聚合，显示查什么、成功/失败、得到几条 Evidence；
- `assess`：hard/soft coverage、gap 和 conflict；
- `review`：真实 `ResearchReview` 的目标、质疑、resolution 和 Evidence 引用；
- `replan`：存在 `replan` trace 时显示新一轮及补证内容；不存在时明确显示 no-replan 原因；
- `synthesize`：是否形成 causal case 和三个 horizon；
- `gate`：Artifact 保存状态与行动摘要，不能把 Artifact `publish` 解释为交易许可。

no-replan 原因只允许以下确定性值：

- `all_declared_routes_exhausted`；
- `hard_requirements_satisfied_and_review_accepted`；
- `round_budget_reached`；
- `tool_budget_reached`；
- `deadline_reached`；
- `review_requested_new_evidence`（此值必须紧接真实下一轮）；
- `runtime_failed`。

## 5. DSH 页面改造

文件：`extensions/dsh/decision-hub/src/client/index.js`

### 5.1 对话页始终可见的结论卡

在 `conversation.input.dock` 增加 `decision-hub-action-summary` slot。它轮询现有 `/api/decision-hub/report`，完成后在聊天页输入框上方显示紧凑行动卡。用户不切换“研究报告”标签也能看到：

- 操作结论；
- `未授权交易`；
- 可靠性；
- accepted/stale/rejected 计数；
- 最多 3 个阻塞缺口；
- 下一次复查；
- “查看分析过程”提示。

### 5.2 研究报告重排

报告顺序改为：

1. 行动摘要；
2. 为什么；
3. 条件改变与失效；
4. 分周期研究情景；
5. 分析过程；
6. Evidence；
7. 独立 Review；
8. 技术详情。

`上下文清单`、完整工具错误、Run ID、版本和原始引用默认折叠到技术详情。Evidence 原文仍可展开，不能删除或伪造。

### 5.3 文案替换

| 当前文案 | 新文案 |
| --- | --- |
| 可发布 | 报告已保存 |
| 证据充分（100% hard） | 必需证据检查通过；仍需查看限制 |
| 做多研究 / 做空研究 | 做多情景 / 做空情景 |
| 概率未校准 | 概率未校准，不应用于仓位决策 |
| 研究完成 | 研究流程完成 |

## 6. Decision Desk 改造

文件：

- `apps/decision-desk/src/research/ResearchPage.tsx`
- `apps/decision-desk/src/styles.css`
- `apps/decision-desk/src/App.tsx`

### 6.1 页面结构

侧栏本目标只保留 `Research`。Operations 不删除后端和数据，但从默认产品导航移除；开发者通过直接 URL 或后续明确的 debug 入口查看。

研究详情顶部先显示 `ResearchActionSummary`，随后提供两个视图：

- `用户结果`：行动摘要、支持/反对理由、条件、Evidence 摘要、Review；
- `技术详情`：原始任务、tool calls、完整 trace、版本、成本和错误 provenance。

默认打开 `用户结果`。长 Trace 不再与行动结论处在同一纵向页面。

### 6.2 Run 列表

每个 Run 只显示：任务标题、行动结论、可靠性、更新时间、阻塞缺口数。`R1/3`、工具预算、runtime ID 移入技术详情。

### 6.3 错误分组

错误不能隐藏，按影响展示：

| 分类 | 判断规则 | 展示位置 |
| --- | --- | --- |
| 阻断错误 | 无 Artifact、Run failed/rejected、PIT/attestation/contract 失败 | 首屏红色，结论 unavailable/do_not_trade |
| 数据限制 | stale、missing、semantic mismatch、policy denied | 行动卡黄色限制区 |
| 已恢复错误 | 前次失败但后续同 capability 成功且 Artifact 完成 | 技术详情黄色，不污染首屏为“系统失败” |
| 诊断信息 | 版本、序列号、低层事件 | 技术详情 |

后台真实 5xx、未捕获异常或进程退出不能降级成数据限制。

## 7. 后端与合同修改点

### 7.1 不新增数据库和运行时

本次不做 migration、不加表、不加队列、不加 scheduler、不加 Agent、不加 Provider 平台。行动摘要和过程摘要全部由已存在的 durable Run 数据投影。

### 7.2 精确文件范围

| 文件 | 改动 | 为什么是核心 |
| --- | --- | --- |
| `contracts/schemas/agentic_research.schema.yaml` | 增加 `research_action_summary`、`research_process_step`，挂到 detail view | DSH 与 Desk 使用同一真实语义 |
| `packages/query_views/research/service.py` | 确定性生成行动摘要、过程步骤、错误分类和 no-replan 原因 | 避免两个前端各自解释资金风险 |
| `packages/provider_adapters/research/fact_pack.py` | 恢复 OI delta 与跨 venue 请求的真实约束 | 修复 hard 100% 掩盖请求缺口 |
| `packages/kernel/decision_hub_kernel/application/commit.py` | 未校准方向不进入可发布方向 forecast；保留研究情景并 fail-closed | 防止账本中的方向字段被误用 |
| `extensions/dsh/decision-hub/src/client/index.js` | 聊天页行动卡、报告重排、状态文案和过程摘要 | 用户第一眼得到结论与依据 |
| `apps/decision-desk/src/research/ResearchPage.tsx` | 用户结果/技术详情、行动摘要、过程 | 管理和技术人员均能核验 |
| `apps/decision-desk/src/App.tsx` | 默认导航只保留 Research | 移除当前无价值入口 |
| 对应测试文件 | 保护资金风险、状态语义和第一屏 | 防止再次通过文案掩盖缺口 |

代码生成只运行项目已有 `tools/contract_codegen`，不建立新生成系统。

## 8. 开发任务与顺序

### P0：恢复真实 Gate 语义

- [x] 写失败测试：请求 OI 变化时，只有当前 OI 点不能覆盖 `derivatives_crowding`。
- [x] 写失败测试：请求跨交易所确认时，单一 OKX 不能被标记为完成。
- [x] 恢复 `open_interest_delta` 和最少两个 venue/independence 的任务约束。
- [x] 写失败测试：任一方向 horizon 为 `uncalibrated` 时，行动摘要不能为可人工考虑的方向结果。
- [x] 写失败测试：缺 Review、Review rejected 或 requires_new_evidence 时不能显示流程可靠。
- [x] 实现 fail-closed；旧 Run 回读保持兼容，不能回写篡改历史记录。

退出证据：同一现有 Run 不再被界面解释成完整可交易证据；数据缺失真实出现于行动摘要。

### P1：canonical 行动摘要和过程摘要

- [x] 增加两个 schema 定义和 detail view 字段。
- [x] 生成 Python/TS 合同并通过 canonical manifest。
- [x] 在 Query View 实现确定性推导。
- [x] 对完成、证据不足、未校准、Review 补证、系统失败五种夹具做测试。
- [x] 确认 `trade_authorized` 永远是 false。

退出证据：`GET /v1/research/runs/{run_id}` 单个响应足以渲染两端，不需要前端猜测 Gate。

### P2：DSH 主入口第一屏

- [x] 聊天页增加 action summary dock。
- [x] 报告顶部重排为行动摘要。
- [x] 增加 8 阶段过程视图和 no-replan 原因。
- [x] 重命名 `publish` 和 `sufficient` 的用户文案。
- [x] 阻断错误、数据限制、已恢复错误分开展示。
- [x] 插件测试验证 DOM 文案、状态和未授权交易提示。

退出证据：新建任务完成后，无需点击报告标签即可看到“不交易/等待/人工复核”和原因。

### P3：Decision Desk 收口

- [x] 默认导航只保留 Research。
- [x] Run 列表改为行动结论摘要。
- [x] Detail 默认用户结果，技术详情按需展开。
- [x] 删除默认长页中的重复 Trace/Evidence 全展开。
- [x] 测试用户结果、技术详情、错误分组；交互使用原生 button/details 语义。

退出证据：一个 1440×900 截图内能看到任务、行动结论、可靠性、证据计数、缺口和下一步。

### P4：真实端到端验收

- [x] 用一键启动器 clean restart，确认没有旧 dist。
- [x] 固定验收输入运行 3 次，不修改问题、不删除失败样本。
- [x] 每次记录 Run ID、Artifact ID、耗时、模型/工具次数、accepted/stale/rejected、hard/soft、Review、轮次、no-replan/replan、行动摘要、后台 5xx/异常。
- [x] 三次从 DSH Web 同源 intake 进入真实主链；自动点击了结果、过程、Evidence、Review 和 Desk 用户视图。为遵守 3 次费用上限，未额外发起第 4 次仅用于补拍运行态。
- [x] 截图只作为交付证据，同时已校验 DOM 文案、API 数据和后台日志。
- [x] 比较三次结论和证据差异；Run 1 缺二次来源、Run 2 合成失败、Run 3 修复后仅研究的变化均有可定位的代码/证据原因。
- [x] 给出 `LIMITED（仅研究）/ NO-GO（交易与扩展）`，未以测试通过宣布 GO。

## 9. 自动化测试矩阵

### Python

- `tests/capabilities/test_fact_pack.py`
  - OI point != OI delta；
  - 单 venue != cross-venue；
  - stale/semantic mismatch 保持 gap；
  - 不为 snapshot 删除用户明确请求的字段。
- `tests/kernel/test_sufficiency.py`
  - hard/soft、venue、independence、conflict 的确定性判定。
- `tests/kernel/test_commit.py` 或现有提交测试
  - uncalibrated long/short fail-closed；
  - no_trade 不生成貌似可执行方向；
  - Artifact 保存与交易权限分离。
- `tests/research/test_research_api.py`
  - action summary/process steps/error groups 序列化；
  - no-replan 原因；
  - 旧 Run 兼容。

### DSH plugin

- `extensions/dsh/decision-hub/tests/client.spec.ts`
  - 聊天页完成态显示行动结论；
  - `publish` 显示为报告已保存；
  - 未校准显示“不应用于仓位决策”；
  - 缺口和错误分类；
  - no-replan/replan 过程。

### Decision Desk

- `apps/decision-desk/src/research/ResearchPage.test.tsx`
  - 用户结果默认可见；
  - 技术 Trace 默认不占据长页；
  - Evidence 计数和引用；
  - Review 缺失阻断；
  - 系统失败不被显示成普通数据限制。
- `apps/decision-desk/src/App.test.tsx` 或现有入口测试
  - 默认导航无 Operations/Evolution/Forecasts 等外围入口。

### 质量门

```powershell
uv run pytest <上述定向测试> -q
uv run ruff check <本次 Python 文件>
uv run pyright <本次 Python 文件>
pnpm --dir extensions/dsh/decision-hub test
pnpm --dir extensions/dsh/decision-hub build
pnpm --dir apps/decision-desk test
pnpm --dir apps/decision-desk build
uv run python -m tools.contract_codegen check
git diff --check
```

定向测试通过后只运行一次相关研究主链回归；没有新失败或新代码时不重复跑大套件刷数字。

## 10. 真实验收记录

每次 Run 在本文尾部追加一行，不另建评测平台：

| Run | 终态 | 行动摘要 | hard/soft | Evidence A/S/R | 轮次/工具/Review | 阻塞缺口 | 后台异常 | 用户是否能在首屏判断 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `run_54f16549c7d7426eaa52abeaf00b1b85` / `art_ba086eeaff5e4feb8fb84820ab3cc0d2` | `research_only` / 311s | `do_not_trade` / low | 0% / 33% | 28/3/0 | 3/17/1 bounded，2 次补证 | 宏观、跨资产、二次现货、二次衍生品 | 无后台 5xx；交易所来源不足 | 是：明确不交易和 4 缺口 | 失败样本保留；证明单一 OKX 不能通过 hard Gate |
| `run_256fd84bb1f347d0aff8f00098646272` / `art_89e9b238af35419aacc038db67924122` | `rejected` / 214s | `unavailable` | 100% / 33% | 15/3/0 | 1/9/1 bounded，无可验证续路 | 宏观、跨资产 | 无后台 5xx；首次 synthesis 提交存在重复引用/缺引用，修复重试退化为空结果 | 是：拒绝报告、无法判断 | 失败样本保留；促成有界表达修复 |
| `run_c892c199d6e448ab8db6c6b455e97b41` / `art_92a279192b1942ffa1af13130c674e25` | `research_only` / 176s | `do_not_trade` / low | 100% / 33% | 15/6/0 | 1/10/1 bounded，无可验证续路 | 实时宏观、实时跨资产 | 无后台 5xx；2 项 DSH 原生搜索未配置但已被 typed provider 替代 | 是：结论、证据数、缺口、3 周期和过程同屏可见 | 单一固定任务的首屏安全闭环通过；产品价值与运行稳定性未验证 |

失败 Run 原样记录。不得因为失败重跑后成功就从表中删除第一次失败。

## 11. 费用与外部调用约束

- 当前模型配置只从 gitignored `.env` 读取，密钥不进入日志、截图、文档或 Git。
- 自动测试全部使用夹具；P0–P3 不调用付费模型。
- P4 正式验收最多 3 次新模型 Run。
- 只有确定为启动/配置故障时允许额外 1 次技术复跑，原失败仍计入验收。
- Provider/模型成本未知时明确记录 `unknown`；不得把 unknown 当 0。
- 不购买数据、不开通订阅、不调用交易账户、不生成订单、不计算仓位。
- 外部服务返回错误、限流或数据缺失时立即保留失败；禁止无限重试到出现好结果。
- 不因为即将交付而降低 Gate、移除字段、改评测输入或隐藏红色错误。

## 12. 产品价值门

### GO

只有同时满足才可称为当前 research-only 产品可交付：

- 三次真实 Run 均从输入到首屏行动摘要完成；
- 严重事实/时间/来源错配为 0；
- 结论引用支持率不低于 95%；
- 用户无需 Trace 能在两分钟内回答结论、依据、缺口、停止原因和下一步；
- 后台未捕获异常/5xx 为 0；所有工具失败均有正确分类；
- 单轮停止有可信 no-replan 原因，需要补证时真实发生下一轮；
- 所有页面明确 `未授权交易`；
- 结果相较直接模型回答至少减少一次人工资料拼接或核验步骤。

### LIMITED

链路和界面通过，但实时宏观、实时跨资产或概率校准等关键条件仍不可达：产品只能命名为“证据研究/事件简报”，行动摘要只能 `do_not_trade` 或 `wait_for_confirmation`，不得宣传方向预测。

### NO-GO

满足任一条立即停止本目标，不继续修外围：

- 仍通过放宽字段/venue/鲜度取得 hard 100%；
- 用户仍需阅读 Trace 才知道结果；
- 三次运行出现严重事实错配、未引用数字或同样输入无依据地改变主结论；
- 连续两次真实 Run 因同一 Provider/系统错误无法形成报告；
- 独立 Review 只是文字存在，无法关联 Evidence 或补证；
- 后台错误被 UI 隐藏或改名成成功；
- 相比现成工具没有减少人工查证和返工。

NO-GO 后保留 Hub 账本、Evidence/PIT/Gate 和可用 Provider adapter，停止当前 DSH 产品 UI 扩展，另行选择现成研究工具作为入口。

## 13. 明确禁止的工作

本目标完成前禁止：

- M04/M05 调度；
- Evolution、自主演化、自动交易；
- PPT、ASR、第二领域；
- Docker Desktop 修复；
- Langfuse/LoongSuite 扩建；
- 新数据库、新消息队列、新 Agent 框架；
- 新通用评测平台；
- 为旧历史宏观案例购买或拼凑分钟数据；
- 重写整个 DSH 或 Decision Desk。

## 14. 进度记录规则

所有开发进度直接更新本文的 P0–P4 checkbox 和验收表，同时在 `CORE_CHAIN_DEV_LOG.md` 只追加一段简短事实。不得再创建同级计划。

每次汇报固定回答：

1. 用户第一屏具体改变了什么；
2. 这次结论由哪些 Evidence 和 Gate 支持；
3. 哪些真实错误仍存在；
4. 是否触发 LIMITED/NO-GO；
5. 下一项代码改动是什么。

## 15. 实际实现结果

### 15.1 证据 Gate

- `FactPack` 恢复了快照研究的真实 hard 约束：现货必须有两个独立 venue；衍生品必须同时包含 funding、OI、`open_interest_delta`、mark/index/basis 和两个 venue。
- 新增 Binance 公开市场 adapter，在不使用交易账户或 API key 的情况下提供现货 24h ticker、funding、当前 OI、5m OI 历史变化、mark/index 和 basis。OKX 与 Binance 使用不同 independence group。
- commit Gate 在任一方向 horizon 未校准，或独立 Review 未 `accepted` 时，只能保存 `research_only`；不会因 hard coverage 100% 就输出可交易方向。

### 15.2 一个真实的结果模型

- canonical detail view 现在同时返回 `ResearchActionSummary` 和 8 步 `ResearchProcessStep`。DSH 和 Decision Desk 不再各自猜 Gate 含义。
- `trade_authorized` 由 Hub 确定性投影为 `false`；模型无法把它改为 true。
- 第二次验收暴露了 synthesis 表达性缺陷：重复 Evidence ref 和已经被其他字段引用、但 `causal_case.evidence_refs` 缺失。工具现在只对这两种表达问题做确定性归一化；不生成新事实、新结论或新 Evidence ID，Hub 仍校验 ID 白名单。

### 15.3 用户结果与技术审计

- DSH 对话页输入框上方始终有紧凑行动卡；“研究报告”首屏先显示行动结论、未授权交易、3 周期条件和分析过程。
- DSH 顶部不再向普通用户堆英文 stop reason、capability ID 和原始错误码。这些信息没有被删除，统一放在默认折叠的“技术限制与错误”。
- Decision Desk 默认导航只保留 Research；当前 Run 和行动摘要在首屏，历史 Run 收起，用户结果/技术详情分离。

## 16. 公开数据依据与边界

本轮没有把“缺数据”等同于“必须购买所有分钟数据”。对当前 BTC 快照，现成免费公开来源足以补齐现货、衍生品和小时历史：

- [Binance Spot Market Data](https://developers.binance.com/en/docs/binance-spot-api-docs/rest-api/market-data-endpoints) 公开市场数据接口可返回 24h ticker。
- [Binance USDⓈ-M Futures Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) 公开接口提供 mark/index、funding、当前 OI 和 OI statistics；官方文档明确 OI statistics 只保留最近 30 天。
- [OKX V5 Market Data](https://app.okx.com/docs-v5/en/) 明确 Market Data 无需认证，提供 ticker、order book 和 history candles；文档也提醒多缓存服务会使后一次请求的时间戳早于前一次，所以接入中保留每条 observed/received time，不用请求顺序伪造时序。
- [OKX Historical Market Data](https://www.okx.com/en-us/historical-data) 公开下载页列出了成交、K 线、funding 和 L2 订单簿的可用历史范围。

这些公开市场端点不能证明实时宏观传导、独立跨资产反应，也不能提供样本外概率校准。历史分钟宏观数据只是旧事件回放的需求，不是当前手动 BTC 快照主链的阻塞条件。

## 17. 自测结果

| 检查 | 结果 |
| --- | --- |
| 核心 Python 回归（FactPack/Gate/API/Binance/Fact Store/DSH Web/Launcher） | `70 passed, 2 skipped` |
| DSH plugin | `87 passed`，生产 build 通过 |
| Decision Desk | `12 passed`，生产 build 通过；仅有 Vite 560.70 kB chunk 警告 |
| Python 静态检查 | Ruff 通过，Pyright `0 errors` |
| canonical contract | `canonical schemas: ok` |
| diff 格式 | `git diff --check` 通过 |
| 全仓回归 | `655 passed, 3 skipped, 4 failed` |

全仓 4 个失败没有被隐藏：

1. `tests/e2e/test_realtime_source_flow.py` 和 `tests/providers/test_market_and_notifications.py` 在 Windows 用默认 GBK 读取 UTF-8 通知文件，触发 `UnicodeDecodeError`；
2. `tests/evolution/test_evolution_executor.py` 两个断言因候选制品 `candidate_artifact_hash_mismatch` 失败。

这 4 个失败属于本目标明确冻结的通知/实时调度和 Evolution 外围链路，不经过手动 BTC/USDT 研究。本轮没有为了把全仓数字涂绿而违反范围禁令；它们仍是仓库级已知问题，如果未来重启对应模块，必须先修复。

## 18. 最终产品决定

**结论：`LIMITED（仅研究）`，同时对“交易决策产品”和新的外围扩展给出 `NO-GO`。**

已验证的能力是：用户提交一次问题后，系统能自动聚合两个交易所、衍生品、订单簿和小时历史，产生 Evidence-linked 报告，并把未校准、stale、gap、Review 保留意见和工具失败明示。它是否真的降低手工查证时间和返工，本轮没有 owner 人工时长或盲评基线，因此仍待测量，不当作已证明的商业价值。

不能给 GO 的证据是：

- 3 次中有 1 次 synthesis 失败，运行稳定性没有达到“三次均形成可用报告”；
- 最终 Run 的 soft coverage 仅 33%，实时宏观与跨资产仍 stale/缺失；
- 3 个 horizon 全部未校准，独立 Review 为 `bounded`，不支持仓位决策；
- 当前最快真实 Run 仍用时 176 秒，模型费用字段为 `unknown`；
- 未建立样本外预测校准，也没有证明结论引用支持率稳定达到 95%。
- 没有直接模型基线、owner 完成时长或盲评，所以不能声称已减少人工资料拼接或返工。

因此验收后停止 PPT、新角色、自动调度、Evolution 和交易接入。如果决定继续投资，唯一值得单独立项的下一步是：先确定有许可的实时宏观/跨资产数据成本和一个样本外校准方案，再以“是否降低人工核验时间”作为唯一投资门。

独立多 Agent 只读审计已核对三份 acceptance JSON 与本文表格一致，并要求删除“已证明减少人工拼接”的过度声称；上述结论已按该审计修正。

## 19. 交付证据

- [当前使用说明](../user-guides/DECISION_HUB_CORE_RESEARCH_GUIDE_2026-09-14.md)
- [DSH 研究报告首屏](../user-guides/images/core-value-dsh-report-run3-final.png)
- [DSH 独立复核与关键事实](../user-guides/images/core-value-dsh-audit-details.png)
- [Decision Desk 用户结果首屏](../user-guides/images/core-value-decision-desk-run3-accepted.png)
- [Decision Desk 技术详情](../user-guides/images/core-value-decision-desk-run3-technical.png)
- 三次脱敏 API 验收详情保存在 gitignored `data/product-local/acceptance-run_<run_id>.json`，不包含模型密钥。
