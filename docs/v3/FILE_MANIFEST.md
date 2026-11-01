# Decision Hub v3 文档清单

所有文档已写入仓库，当前未 commit。需要你决定如何处理（commit 到哪个分支、是否需要修改）。

---

## 一、核心指挥文档（必读）

| 文件 | 大小 | 读者 | 用途 |
|---|---|---|---|
| `AGENTS.md` | 5.5 KB | 执行智能体 Codex | **第一优先级必读**。执行规则：只能做什么、绝对禁止、代码规范、任务流程、失败处理 |
| `docs/v3/README.md` | 4.6 KB | 所有人 | v3 总览：背景、原则、结构、角色、流程 |
| `docs/v3/STATUS.md` | 3.2 KB | 所有人 | **唯一真相来源**。阶段与任务进度跟踪 |
| `docs/v3/01_REQUIREMENTS.md` | 8.0 KB | 架构师、执行智能体 | 产品需求：范围、用户场景、合格研究定义、交付标准 |
| `docs/v3/02_ARCHITECTURE.md` | 14 KB | 架构师、执行智能体 | 架构设计：模块、数据流、接口、错误处理 |
| `docs/v3/HANDOFF_TO_ARCHITECT.md` | 6.3 KB | 架构师/PM | 接手指南：职责、当前状态、协作流程、待拍板问题 |
| `docs/v3/SUMMARY.md` | 14 KB | 用户、架构师 | **本次交付总结**。成果、方法论、风险、下一步 |

**阅读顺序建议（第一次接触）：**
1. `SUMMARY.md`（了解全貌）
2. `README.md`（理解结构）
3. `01_REQUIREMENTS.md`（明确目标）
4. `02_ARCHITECTURE.md`（理解设计）
5. `AGENTS.md`（如果你是执行智能体）或 `HANDOFF_TO_ARCHITECT.md`（如果你是架构师）

---

## 二、诊断与方案（背景材料）

| 文件 | 大小 | 读者 | 用途 |
|---|---|---|---|
| `docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md` | 28 KB | 所有人 | 问题分析（架构/流程/业务）、原因对应方案、8 阶段计划 |
| `docs/diagnosis-2026-10-23/OPTIMIZATION_AND_EXTENSIONS.md` | 16 KB | 架构师、用户 | 预测方法优化、数据缺口、业务拓展方向 |

**用途：** 理解"为什么要重建"以及"优化方向"。架构师编写任务卡时参考。

---

## 三、模板（执行智能体使用）

| 文件 | 大小 | 用途 |
|---|---|---|
| `docs/v3/templates/TASK_CARD.md` | 4.2 KB | 架构师编写新任务卡时复制此模板 |
| `docs/v3/templates/TASK_REPORT.md` | 5.6 KB | 执行智能体完成任务后填写报告 |
| `docs/v3/templates/QUESTION.md` | 4.4 KB | 执行智能体遇到阻塞时填写问题文档 |

---

## 四、任务卡（Phase 0）

| 文件 | 大小 | 状态 | 用途 |
|---|---|---|---|
| `docs/v3/tasks/T-000-git-baseline.md` | 6.9 KB | PLANNING | 第一个任务：Git 基线与分支策略 |

**待编写：** T-001—T-007（需架构师完成 `03_DATA_CONTRACTS.md` 等文档后再写）

---

## 五、待创建目录（任务执行时填充）

```
docs/v3/reports/     ← 执行智能体提交的任务报告
docs/v3/questions/   ← 执行智能体提交的阻塞问题
docs/v3/acceptance/  ← 架构师编写的阶段验收报告
```

---

## 六、待编写文档（架构师职责）

| 文件 | 预计大小 | 优先级 | 内容 |
|---|---|---|---|
| `docs/v3/03_DATA_CONTRACTS.md` | 10 KB | HIGH | 表结构、Pydantic 模型、known_at 规则、迁移策略 |
| `docs/v3/04_FORECAST_METHOD.md` | 8 KB | HIGH | 因果图模板、概率三层、聚合、校准、基率计算 |
| `docs/v3/05_PHASE_PLAN.md` | 12 KB | MEDIUM | 8 阶段详细目标、依赖、验收标准、风险 |
| `docs/v3/tasks/T-001.md` 到 `T-007.md` | 各 5-7 KB | HIGH | Phase 0 剩余任务卡 |
| `docs/v3/GIT_WORKFLOW.md` | 3 KB | HIGH | Git 分支命名、提交、合并规则（T-000 创建） |

**总计待编写：** 约 70 KB，预计 12—15 小时工作量。

---

## 七、文件统计

```
已交付文档:  107 KB, 13 个文件
待编写文档:   70 KB,  9 个文件
总计:        177 KB, 22 个文件
```

---

## 八、下一步行动清单

### 用户（人类）需要决定的：

- [ ] 问题 1：首个交付切片是否认可"BTC/ETH 4h 普通市场"？
- [ ] 问题 2：验收顺序是否"稳定出结果优先，审计完整性第二"？
- [ ] 问题 3：不可放松的校验是否为"资金/归属/时点"三类？
- [ ] 谁担任架构师角色（自己 / 另一个 LLM / 人类同事）？
- [ ] 是否启动 Phase 0？

### 架构师需要完成的：

- [ ] 编写 `03_DATA_CONTRACTS.md`
- [ ] 编写 `04_FORECAST_METHOD.md`
- [ ] 编写 `05_PHASE_PLAN.md`
- [ ] 编写 Phase 0 任务卡 T-001—T-007
- [ ] 在 `STATUS.md` 中把 T-000 标为 READY
- [ ] 通知执行智能体 Codex 开始执行

### 执行智能体 Codex 需要做的：

- [ ] 阅读 `AGENTS.md`（第一优先级）
- [ ] 阅读 `docs/v3/README.md`、`STATUS.md`
- [ ] 阅读被分配任务的任务卡
- [ ] 执行任务、提交报告
- [ ] 遇到阻塞时写 Question

---

## 九、文档维护规则

- **`AGENTS.md`、`docs/v3/*.md`：** 由架构师维护。执行智能体只读，不修改（`STATUS.md` 中自己的任务状态除外）。
- **`docs/v3/reports/*.md`：** 由执行智能体创建，架构师可追加"验收意见"。
- **`docs/v3/questions/*.md`：** 由执行智能体创建，架构师填写"决策记录"。
- **`docs/v3/tasks/*.md`：** 由架构师创建，执行过程中发现问题可提 Question 要求修改，但不直接改任务卡。
- **诊断文档（`docs/diagnosis-2026-10-23/`）：** 历史材料，只读，不再修改。

---

## 十、相关命令

```bash
# 查看所有 v3 文档
find docs/v3 -type f -name '*.md' -exec ls -lh {} \; | awk '{print $9, $5}'

# 统计已写文档字数
wc -w docs/v3/*.md docs/v3/templates/*.md docs/v3/tasks/*.md AGENTS.md

# 检查 STATUS.md 中的任务状态
grep '| T-' docs/v3/STATUS.md

# 查看待拍板问题
sed -n '/## 当前需要你拍板/,/^##/p' docs/v3/HANDOFF_TO_ARCHITECT.md
```

---

**最后更新：** 2026-10-23 14:40  
**维护者：** 架构师（本会话）
