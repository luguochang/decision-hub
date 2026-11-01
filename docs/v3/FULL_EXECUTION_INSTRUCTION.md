# 给执行智能体 Codex 的完整指令 — Decision Hub v3 重建

**日期:** 2026-10-23  
**发起人:** 架构师  
**目标:** 完成 Decision Hub v3 全部 8 个阶段、55 个任务，达到可交付状态

---

## 🎯 你的任务

Decision Hub v1/v2 无法交付（10 月 257 次研究 0 成功）。现在进行 v3 完整重建，你负责按设计文档编写所有代码、测试、验收、直到最终交付。

**总工作量:** 55 个任务，311 小时，约 11 周

---

## 📖 第一步：阅读核心文档（启动前必读）

**按顺序阅读，不要跳过：**

### 1. 执行规则（最重要，违反一次任务直接 REJECTED）
```bash
cat AGENTS.md
```
重点：
- 第 2 节：15 条"绝对禁止"
- 第 4 节：每个任务的固定流程
- 第 6 节：报告必须诚实

### 2. v3 总览
```bash
cat docs/v3/README.md
```

### 3. 任务总清单（55 个任务全貌）
```bash
cat docs/v3/TASK_MASTER_LIST.md
```
重点：
- 8 个 Phase 的所有任务、工时、依赖
- 每个 Phase 的验收标准
- 工时统计与时间线

### 4. 产品需求
```bash
cat docs/v3/01_REQUIREMENTS.md
```
理解：v3 要做什么、合格研究定义、最终交付标准

### 5. 架构设计
```bash
cat docs/v3/02_ARCHITECTURE.md
```
理解：模块划分、数据流、接口契约

### 6. 诊断报告（背景材料，理解"为什么"）
```bash
cat docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md
```

---

## 🚀 第二步：开始执行

### 执行流程（每个任务重复此流程）

1. **查看 STATUS**
   ```bash
   cat docs/v3/STATUS.md
   ```
   找到下一个 READY 状态的任务。

2. **读取任务信息**
   
   **方式 A: 如果任务卡已存在**
   ```bash
   cat docs/v3/tasks/T-XXX-<name>.md
   ```
   
   **方式 B: 如果任务卡未编写**  
   从 `TASK_MASTER_LIST.md` 获取任务的"交付物简述"，结合 `02_ARCHITECTURE.md` 中对应模块的设计来理解任务。
   
   如果仍有疑问（不确定接口、数据结构、边界条件），写 Question 到 `docs/v3/questions/Q-<序号>-<短名>.md`，标记任务为 BLOCKED，等待架构师裁决。

3. **切分支、更新 STATUS 为 IN_PROGRESS**
   ```bash
   git checkout v3/main
   git pull
   git checkout -b v3/T-XXX-<name>
   # 编辑 STATUS.md，T-XXX 改为 IN_PROGRESS，填开始时间
   git add docs/v3/STATUS.md
   git commit -m "T-XXX: Mark as IN_PROGRESS"
   ```

4. **执行任务**
   - 只修改任务卡"允许修改的路径"中列出的文件
   - 任务卡没写的功能不要加
   - 遵守 `AGENTS.md` 第 2 节的 15 条"绝对禁止"

5. **运行验收命令（任务卡中的"验收命令"一节）**
   - 逐条运行，保存命令和输出（至少最后 30 行）
   - 任何一条失败必须修复后才能提交报告

6. **写报告**
   ```bash
   cp docs/v3/templates/TASK_REPORT.md docs/v3/reports/T-XXX.md
   # 填写所有章节：执行摘要、交付物、验收命令结果、已知问题/偏离、Git 提交
   git add docs/v3/reports/T-XXX.md
   git commit -m "T-XXX: Submit task report"
   ```

7. **更新 STATUS 为 REVIEW，推送**
   ```bash
   # 编辑 STATUS.md，T-XXX 改为 REVIEW，填结束时间
   git add docs/v3/STATUS.md
   git commit -m "T-XXX: Mark as REVIEW"
   git push -u origin v3/T-XXX-<name>
   ```

8. **结束会话，等待架构师验收**

9. **验收通过后，开始下一个任务**  
   架构师会合并分支到 `v3/main`，通知你继续。

### 特殊情况处理

**遇到阻塞（任务卡有歧义、设计缺口、技术问题无法按卡完成）：**
1. 立即停止
2. 写 Question 文档：
   ```bash
   cp docs/v3/templates/QUESTION.md docs/v3/questions/Q-<序号>-<短名>.md
   # 填写：问题描述、现象/错误、根因分析、已尝试方案、建议方案
   git add docs/v3/questions/Q-<序号>-*.md
   git commit -m "Q-<序号>: <问题简述>"
   ```
3. 更新 STATUS: T-XXX 状态改为 BLOCKED
4. 推送并结束会话，等待架构师裁决

**验收被 REJECTED：**
1. 架构师会在报告末尾追加"验收意见"一节，说明问题
2. 在原任务分支上修复
3. 重新运行验收命令
4. 更新报告中的"验收命令执行结果"和"已知问题/偏离"
5. 提交、推送、更新 STATUS 为 REVIEW

---

## ⚠️ 关键约束（违反任何一条任务直接 REJECTED）

1. **只修改任务卡允许的路径**  
   任务卡"允许修改的路径"之外的文件一律不动。

2. **禁止 import 旧代码**  
   `packages/kernel/`、`apps/hub_*`、`packages/provider_adapters/` 全部禁止 import 或修改。

3. **任务卡没写的功能不加**  
   不要"我觉得更完整"而加功能、改接口、换技术栈。

4. **验收命令必须全部通过**  
   不通过就修复，不要"差不多就行"。

5. **报告必须诚实**  
   "已知问题/偏离"一节有问题就写，没有就写"无"。隐瞒比失败更严重。

6. **遇到问题写 Question，不要猜测**  
   不确定接口、数据结构、优先级时，写 Question 而不是自己决定。

7. **禁止的 Git 操作**  
   - 禁止 `git push --force`
   - 禁止 `git commit --amend` 已推送的提交
   - 禁止 `git reset --hard` 有未提交工作时
   - 禁止 `--no-verify`

8. **ruff + pyright strict 零错误**  
   所有 Python 代码必须通过 lint 和类型检查。

9. **禁止编造数字**  
   测试结果、性能数据、评分指标必须是真实运行的输出，不能编造。

10. **禁止为通过测试而放宽断言**  
    测试失败说明代码有问题，修代码而不是改测试。

---

## 📋 8 个 Phase 的执行顺序

### Phase 0: 基线与基础设施 (7 任务, 22h)
**目标:** Git 基线、代码骨架、Feature Store、回放框架、状态机、错误码、测试基础设施

**任务:** T-000 → T-001 → (T-002, T-003, T-004, T-005, T-006 可并行) → T-007

**验收标准:**
- Python 代码 ruff + pyright strict 零错误
- Feature Store 能存取按 known_at 查询的数据
- 回放框架能录制并回放 OKX API 调用
- 研究状态机能正确流转

**完成后:** 架构师写 `docs/v3/acceptance/phase-0.md`，批准后进入 Phase 1。

---

### Phase 1: 架构主干 (6 任务, 34h)
**目标:** 研究主循环、DSH 会话管理、PREPARE/EVIDENCE 阶段、不可变存储、会话日志校验、快照生成

**任务:** T-101 → (T-102, T-103 可并行) → (T-104, T-105, T-106 可并行)

**验收标准:**
- 研究能从 IDLE → PREPARE → EVIDENCE 流转
- DSH 会话能保留上下文并调用
- 原件按内容哈希存储
- 会话日志只能追加
- 快照可离线读取

---

### Phase 2: 普通市场 4h (10 任务, 56h)
**目标:** FORECAST/PLAN/REVIEW/PUBLISH 阶段、报告渲染、PC 前端、端到端测试

**任务:** T-201 → T-202 → T-203 → T-204 → T-205 → T-206 → T-207 → (T-208, T-209 可并行) → T-210

**验收标准:**
- BTC/ETH × 4h 能完成完整研究流程
- 报告包含方向、概率、多空方案、理由
- 概率来自：基率 + LLM 主观 + 多视角聚合
- PC 能发起研究并查看报告
- 端到端测试连续 3 次成功

---

### Phase 3: 24h / 72h 期限 (3 任务, 14h)
**目标:** 扩展到 24h/72h 期限、六组合稳定性测试

**任务:** T-301 → T-302 → T-303

**验收标准:**
- BTC/ETH × 4h/24h/72h 六个组合都能完成研究
- 稳定性测试合格率 ≥ 80%

---

### Phase 4: 事件研究 (7 任务, 47h)
**目标:** 经济数据共识、事件日历、情景判定、因果图、预测市场锚点、期权隐含波动

**任务:** T-401 → T-402 → T-403 → T-404 → (T-405, T-406 可并行) → T-407

**验收标准:**
- CPI、FOMC 等事件有共识预期和日历
- 数据公布后能自动判定情景并触发方案
- 因果图能逐节点展示传导路径
- 概率展示：基率 + 市场锚点 + LLM 调整

---

### Phase 5: 监控与提醒 (4 任务, 20h)
**目标:** 条件监控、Worker 循环、邮件/Webhook 通知、PC 监控管理页

**任务:** T-501 → T-502 → T-503 → T-504

**验收标准:**
- 能创建"BTC 跌破 XX 时提醒"条件监控
- 条件触发时能通过邮件/Webhook 通知

---

### Phase 6: PAPER 成交 (6 任务, 33h)
**目标:** 账户、订单、方案自动执行、保护机制、结算、PC PAPER 操作页

**任务:** T-601 → T-602 → T-603 → T-604 → T-605 → T-606

**验收标准:**
- 能创建 PAPER 账户
- 事件触发后能自动执行方案
- 止损/止盈/失效条件能正确触发平仓
- 至少完成 ≥ 10 笔成交

---

### Phase 7: 结果评分与学习 (7 任务, 38h)
**目标:** 评分指标、校准曲线、分层统计、校准器训练、历史回放评估、PC 复盘中心、周报

**任务:** T-701 → T-702 → T-703 → T-704 → (T-705, T-706 可并行) → T-707

**验收标准:**
- 所有研究都有 Brier、log loss、方向准确率评分
- 校准曲线展示预测概率 vs 实际频率
- 校准器能改善概率校准 (Brier 下降 ≥ 5%)
- 历史回放评估能积累 ≥ 30 个样本

---

### Phase 8: 最终交付 (5 任务, 47h)
**目标:** 用户手册、运维手册、性能优化、14 天稳定性验证、交接清单

**任务:** T-801 → T-802 → T-803 → T-804 → T-805

**验收标准（最终交付）:**
- BTC/ETH × 4h/24h/72h 连续 14 天合格率 ≥ 90%
- 单次研究 P50 ≤ 15 分钟、P95 ≤ 30 分钟
- ≥ 2 类事件族完成事前预案全流程
- PAPER ≥ 10 笔成交
- ruff + pyright strict 零错误
- ≥ 30 个历史回放用例全部通过
- 用户手册与运维手册完整

---

## 🎯 开始执行

现在按照"第二步：开始执行"的流程，从 T-000 开始，逐个完成所有 55 个任务。

### 当前第一个任务

```bash
# 读取 T-000 任务卡
cat docs/v3/tasks/T-000-git-baseline.md

# 切分支
git checkout main
git checkout -b v3/T-000-git-baseline

# 更新 STATUS
# 编辑 docs/v3/STATUS.md，T-000 改为 IN_PROGRESS
git add docs/v3/STATUS.md
git commit -m "T-000: Mark as IN_PROGRESS"

# 执行任务卡中的所有步骤 ...
```

### 预计时间线

- **Phase 0:** 约 4 天
- **Phase 1:** 约 6 天
- **Phase 2:** 约 10 天
- **Phase 3:** 约 2 天
- **Phase 4/5/6 并行:** 约 8 天
- **Phase 7:** 约 7 天
- **Phase 8:** 约 8 天 + 14 天真实等待

**总计:** 约 11 周（不含 14 天稳定性验证的真实等待）

---

## 📞 遇到问题怎么办

- **任务卡有歧义？** → 写 Question
- **不确定接口定义？** → 写 Question
- **技术问题无法按卡完成？** → 写 Question
- **需要新增依赖？** → 写 Question
- **验收命令失败？** → 修复代码，不要改测试
- **测试数据不够？** → 先用 mock 通过测试，报告中写明"已知问题"

**不要自行补全设计、不要猜测架构师的意图。有疑问就问。**

---

## ✅ 成功标准

v3 视为可交付，需同时满足（Phase 8 最终验收标准）：
1. BTC/ETH × 4h/24h/72h 连续 14 天合格率 ≥ 90%
2. 单次研究 P50 ≤ 15 分钟、P95 ≤ 30 分钟
3. ≥ 2 类事件族完成事前预案全流程
4. PAPER ≥ 10 笔成交
5. ruff + pyright strict 零错误
6. ≥ 30 个历史回放用例全部通过
7. 用户手册与运维手册完整
8. 交接清单包含所有交付物

---

**祝顺利！记住：诚实比完美更重要。**

---

**附：关键文档路径**
- 执行规则：`AGENTS.md`
- 任务总清单：`docs/v3/TASK_MASTER_LIST.md`
- 进度跟踪：`docs/v3/STATUS.md`（唯一真相来源）
- 产品需求：`docs/v3/01_REQUIREMENTS.md`
- 架构设计：`docs/v3/02_ARCHITECTURE.md`
- 诊断报告：`docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md`
- 任务卡目录：`docs/v3/tasks/`
- 报告模板：`docs/v3/templates/TASK_REPORT.md`
- 问题模板：`docs/v3/templates/QUESTION.md`
