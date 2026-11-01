# Decision Hub v3 重建 — 交接检查清单

**日期：** 2026-10-23  
**架构师：** 本会话  
**状态：** ✅ 设计与文档阶段完成，待启动执行

---

## ✅ 已完成

### 一、诊断与分析
- [x] 分析 v1/v2 失败原因（架构、流程、业务）
- [x] 统计数据（257 次研究 0 成功、20 万行未 commit）
- [x] 编写诊断报告 `DIAGNOSIS_AND_SOLUTION.md` (28 KB)
- [x] 分析优化方向 `OPTIMIZATION_AND_EXTENSIONS.md` (16 KB)

### 二、v3 设计文档
- [x] 定义产品需求 `01_REQUIREMENTS.md` (8 KB)
- [x] 设计架构 `02_ARCHITECTURE.md` (14 KB)
- [x] 建立进度跟踪 `STATUS.md` (3.2 KB)
- [x] 编写 v3 总览 `README.md` (4.6 KB)

### 三、执行框架
- [x] 编写执行规则 `AGENTS.md` (5.5 KB, 15 条"绝对禁止")
- [x] 创建任务卡模板 `TASK_CARD.md` (4.2 KB)
- [x] 创建报告模板 `TASK_REPORT.md` (5.6 KB)
- [x] 创建问题模板 `QUESTION.md` (4.4 KB)
- [x] 编写第一个任务卡 `T-000-git-baseline.md` (6.9 KB)

### 四、交接文档
- [x] 编写架构师交接指南 `HANDOFF_TO_ARCHITECT.md` (6.3 KB)
- [x] 编写执行指令 `INSTRUCTION_FOR_CODEX.md` (9 KB)
- [x] 生成文件清单 `FILE_MANIFEST.md` (5.7 KB)
- [x] 编写总结 `SUMMARY.md` (13 KB)
- [x] 生成命令文本 `COMMAND_FOR_CODEX.txt` (1.5 KB)

**总计：** 15 个文档，125 KB，约 3.2 万字

---

## 🎯 给 Codex 的命令（复制下面整段）

```
你好，我需要你作为执行智能体完成 Decision Hub v3 重建项目的第一个任务。

这是一个严格的、有明确规则的项目。你必须先阅读所有指定文档，理解规则，然后按任务卡执行。不要跳过阅读步骤。

## 立即执行以下命令（按顺序）：

### 1. 读取执行指令（这是给你的完整说明）
cat docs/v3/INSTRUCTION_FOR_CODEX.md

完整阅读这份文档，它包含：
- 你需要读的所有核心文档清单
- T-000 任务的完整执行步骤
- 关键约束（15 条"绝对禁止"）
- 遇到问题怎么办
- 成功标准

### 2. 阅读核心文档（按 INSTRUCTION 中的顺序）
cat AGENTS.md
cat docs/v3/README.md
cat docs/v3/STATUS.md
cat docs/v3/01_REQUIREMENTS.md
cat docs/v3/02_ARCHITECTURE.md
cat docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md

### 3. 读取任务卡
cat docs/v3/tasks/T-000-git-baseline.md

### 4. 开始执行 T-000
按照 INSTRUCTION_FOR_CODEX.md 中"第二步：执行第一个任务 T-000"的流程执行。

## 重要提醒

1. 必须完整读完所有文档再开始工作
2. 只做任务卡允许的事（T-000 只是 git 操作和文档）
3. 验收命令必须全部通过
4. 报告必须诚实（有问题写"已知问题/偏离"）
5. 遇到任何疑问立即写 Question，不要猜测

预期时间：2 小时。开始吧！
```

---

## ⏭️ 待办事项（按优先级）

### P0 — 立即需要（启动 Phase 0）

- [ ] **用户决策：** 拍板 3 个问题
  - [ ] 首个交付切片：BTC/ETH × 4h 普通市场？
  - [ ] 验收顺序：稳定出结果优先？
  - [ ] 不可放松的校验：资金/归属/时点三类？

- [ ] **架构师任务：** 编写剩余设计文档
  - [ ] `docs/v3/03_DATA_CONTRACTS.md` (10 KB, 2h)
  - [ ] `docs/v3/04_FORECAST_METHOD.md` (8 KB, 3h)
  - [ ] `docs/v3/05_PHASE_PLAN.md` (12 KB, 2h)

- [ ] **架构师任务：** 编写 Phase 0 剩余任务卡
  - [ ] T-001: v3 代码骨架与模块划分
  - [ ] T-002: 特征仓库核心（按 known_at 索引）
  - [ ] T-003: 回放框架与 cassette 存储
  - [ ] T-004: v3 检查工具
  - [ ] T-005: 合格研究定义与状态机
  - [ ] T-006: 错误码体系与日志结构
  - [ ] T-007: 测试基础设施与 fixture

- [ ] **架构师任务：** 启动执行
  - [ ] 在 `STATUS.md` 中把 T-000 状态改为 READY
  - [ ] 把 `COMMAND_FOR_CODEX.txt` 内容发给新 Codex 会话

### P1 — Phase 0 执行中

- [ ] 监控 Codex 执行 T-000（预计 2h）
- [ ] 验收 T-000 报告（检查：验收命令通过、报告诚实、只修改允许路径）
- [ ] 决定 ACCEPTED / REJECTED
- [ ] 若 ACCEPTED：合并分支到 `v3/main`，通知 Codex 开始 T-001
- [ ] 若 REJECTED：在报告末尾写"验收意见"，通知 Codex 修复

### P2 — Phase 0 完成后

- [ ] 编写 Phase 0 阶段验收报告 `docs/v3/acceptance/phase-0.md`
- [ ] 根据实际工时复核 Phase 1—8 估算
- [ ] 编写 Phase 1 任务卡

---

## 📊 项目时间线预估

| 阶段 | 任务数 | 预计时间 | 依赖 |
|---|---|---|---|
| Phase 0: 基线与基础设施 | 8 个 | 3—4 天 | 架构师先完成待办 P0 |
| Phase 1: 架构主干 | ~10 个 | 2—3 周 | Phase 0 完成 |
| Phase 2: 普通市场 4h | ~8 个 | 2 周 | Phase 1 完成 |
| Phase 3—8 | TBD | 5—8 周 | Phase 2 完成 |

**总计：** 10—15 周（需 Phase 0 实测后复核）

---

## 🎯 成功标准（重申）

v3 视为可交付需同时满足：
1. BTC/ETH × 4h/24h/72h 连续 14 天合格率 ≥ 90%
2. 单次研究 P50 ≤ 15 分钟
3. ≥ 2 类事件族完成事前预案全流程
4. PAPER ≥ 10 笔成交
5. ruff + pyright strict 零错误
6. ≥ 30 个历史回放用例通过

---

## 📞 联系方式

- **执行问题（Codex 遇到阻塞）：** 查看 `docs/v3/questions/` 下的 Question 文档
- **架构师问题（设计疑问）：** 本会话或用户会话
- **用户决策（优先级、授权、上线）：** 用户会话

---

## ✅ 检查清单（启动前确认）

在把命令发给 Codex 之前，确认：
- [x] 所有文档已创建且内容完整
- [x] `AGENTS.md` 包含 15 条"绝对禁止"
- [x] `T-000-git-baseline.md` 任务卡详细、可执行
- [x] `INSTRUCTION_FOR_CODEX.md` 流程清晰
- [ ] 用户已拍板 3 个关键问题（或决定先试运行 T-000）
- [ ] 已决定谁担任架构师角色
- [ ] `STATUS.md` 中 T-000 状态为 READY（由架构师修改）

---

**当前状态：** 等待用户决策与架构师启动执行。

**建议：** 如果用户想先看效果，可以直接把上面的命令给 Codex，让它执行 T-000（只是 git 操作，风险低）。T-000 顺利后再决定是否继续。

