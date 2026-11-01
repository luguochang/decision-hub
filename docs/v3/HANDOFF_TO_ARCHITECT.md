# 交接给架构师 / 产品负责人

本文档面向负责 Decision Hub v3 设计、验收、问题裁决的人类或高级智能体（架构师/PM）。

---

## 你的职责

1. **设计与文档：** 编写/维护需求、架构、数据契约、预测方法、阶段计划文档。
2. **任务分解：** 把每个阶段拆成具体、可验收的任务卡。
3. **验收：** 审查执行智能体提交的报告，决定 ACCEPTED / REJECTED。
4. **问题裁决：** 执行智能体遇到 BLOCKED 时，阅读 Question 文档，给出决策。
5. **阶段验收：** 每个 Phase 完成后，写阶段验收报告，决定是否进入下一阶段。
6. **与用户沟通：** 重大方向、优先级、数据源授权、上线批准需要用户拍板时，你负责整理信息并请求决策。

---

## 当前状态

**日期：** 2026-10-23

**已完成：**
- 诊断文档：`docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` 和 `OPTIMIZATION_AND_EXTENSIONS.md`
- v3 基础框架：
  - `AGENTS.md`：执行智能体规则
  - `docs/v3/README.md`：总览
  - `docs/v3/STATUS.md`：进度跟踪
  - `docs/v3/01_REQUIREMENTS.md`：产品需求
  - `docs/v3/02_ARCHITECTURE.md`：架构设计
  - `docs/v3/templates/`：任务卡、报告、问题模板
  - `docs/v3/tasks/T-000-git-baseline.md`：第一个任务卡

**Phase 0 状态：** PLANNING。T-000 已编写，T-001—T-007 待编写。

**待办事项（你需要完成的）：**
1. 编写 `docs/v3/03_DATA_CONTRACTS.md`（数据契约：表结构、Pydantic 模型）
2. 编写 `docs/v3/04_FORECAST_METHOD.md`（预测方法：因果图、概率三层、聚合、校准）
3. 编写 `docs/v3/05_PHASE_PLAN.md`（8 个阶段的详细目标、依赖、验收标准）
4. 完成 Phase 0 剩余任务卡（T-001—T-007）
5. 把 T-000 在 `STATUS.md` 中标为 `READY`，分配给执行智能体
6. 通知执行智能体开始工作

---

## 与执行智能体的协作流程

### 正常流程

1. 你写好一批 READY 任务后，通知执行智能体："T-000 到 T-003 已 READY，请按顺序执行"。
2. 执行智能体拿一个任务，在 STATUS 改为 IN_PROGRESS，开始执行。
3. 执行完成后，执行智能体提交报告到 `docs/v3/reports/<任务ID>.md`，STATUS 改为 REVIEW，结束会话。
4. 你阅读报告、检查验收命令、审查代码，决定：
   - **ACCEPTED:** 在 STATUS 改为 ACCEPTED，自己合并分支到 `v3/main`，删除任务分支，通知执行智能体可以开始下一个。
   - **REJECTED:** 在 STATUS 改为 REJECTED，写明原因（可以在报告文件末尾追加"## 验收意见"），通知执行智能体修复。
5. 执行智能体修复后重新提交报告，回到步骤 4。

### 遇到阻塞

1. 执行智能体遇到无法按任务卡完成的情况，写 Question 到 `docs/v3/questions/Q-<序号>-<短名>.md`，STATUS 改为 BLOCKED。
2. 你阅读 Question，在其中的"决策记录"一节填写裁决（APPROVED / MODIFIED / REJECTED / NEEDS_DISCUSSION）。
3. 若裁决涉及任务卡修改、新增模块、依赖变更，更新相关文档和任务卡。
4. 通知执行智能体裁决结果，执行智能体按裁决继续或重新规划。

---

## 当前需要你拍板的问题

来自 `docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` 第 10 节，用户尚未决策：

1. **首个交付切片：** 是否认可"BTC/ETH × 4h 普通市场，每天稳定出合格报告"作为 Phase 2 的交付目标？
2. **验收顺序：** 是否同意"先保证每天稳定出结果，审计完整性作为第二优先级"？
3. **不可放松的校验：** 资金、账户归属、事前时点这三类保持严格，其余可以降级为告警。是否有其它不可放松的校验？
4. **数据源授权与费用：** Phase 4 引入预测市场（Kalshi/Polymarket）、期权 API（Deribit），需要确认使用条款、API 密钥、费用预算。
5. **历史回放样本采集：** 是否授权只读查询旧正式库（`data/core-live-20260913/db/decision_hub.sqlite3`，`mode=ro`）用于提取历史失败场景？

**建议：** 先拍板 1-3，Phase 0 可以开工；4-5 可以等 Phase 2 完成后再定。

---

## 关键文档索引

| 文档 | 路径 | 用途 |
|---|---|---|
| 诊断报告 | `docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` | 问题分析、方案、阶段计划 |
| 优化建议 | `docs/diagnosis-2026-10-23/OPTIMIZATION_AND_EXTENSIONS.md` | 预测方法改进、数据缺口、业务拓展 |
| 执行规则 | `AGENTS.md` | 执行智能体必须遵守的规则 |
| v3 总览 | `docs/v3/README.md` | 项目结构、角色、流程 |
| 进度跟踪 | `docs/v3/STATUS.md` | 唯一真相来源，所有任务状态 |
| 产品需求 | `docs/v3/01_REQUIREMENTS.md` | 范围、场景、交付标准 |
| 架构设计 | `docs/v3/02_ARCHITECTURE.md` | 模块、数据流、接口、错误处理 |
| 任务卡模板 | `docs/v3/templates/TASK_CARD.md` | 编写新任务卡时复制 |
| 报告模板 | `docs/v3/templates/TASK_REPORT.md` | 执行智能体填写 |
| 问题模板 | `docs/v3/templates/QUESTION.md` | 遇到阻塞时填写 |
| 第一个任务 | `docs/v3/tasks/T-000-git-baseline.md` | Git 基线任务卡示例 |

---

## 下一步行动

1. 阅读 `docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` 和 `OPTIMIZATION_AND_EXTENSIONS.md`，理解问题全貌。
2. 阅读 `docs/v3/README.md`、`01_REQUIREMENTS.md`、`02_ARCHITECTURE.md`，熟悉 v3 设计。
3. 决定"当前需要你拍板的问题"中的 1-3 项。
4. 编写 `03_DATA_CONTRACTS.md`、`04_FORECAST_METHOD.md`、`05_PHASE_PLAN.md`。
5. 编写 Phase 0 剩余任务卡（T-001—T-007）。
6. 把 T-000 在 `STATUS.md` 中标为 READY，通知执行智能体。

**估计时间：** 编写剩余文档和任务卡约需 6—8 小时工作量。如果你是 LLM，可以一次性完成；如果是人类，可以分批进行。

---

## 联系与协作

- **用户（人类）：** 通过原会话或新会话与你沟通。重大决策、授权、上线批准找用户。
- **执行智能体 Codex：** 在新的 Codex 会话中工作。你通过更新 `STATUS.md` 和通知消息与它协作。Codex 只执行，不设计。
- **本文档维护：** 由架构师维护。执行智能体不修改本文件。

---

祝顺利！Decision Hub v3 的成功取决于清晰的设计、严格的验收、和诚实的沟通。
