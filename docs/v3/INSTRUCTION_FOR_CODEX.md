# 给执行智能体 Codex 的指令

**日期：** 2026-10-23  
**发起人：** 架构师  
**优先级：** P0（最高）

---

## 🎯 任务概述

Decision Hub v1/v2 已无法交付（10 月 257 次研究 0 成功）。现在启动 v3 完整重建。你作为执行智能体，负责按设计文档和任务卡编写代码、测试、提交报告。

**关键原则：** 你只执行，不设计。任务卡没写的功能不要加。遇到设计缺口立即写 Question，不要猜。

---

## 📖 第一步：阅读核心文档（必须完整读完再开始工作）

**按以下顺序阅读，不要跳过任何一份：**

### 1. 执行规则（最重要）
```bash
# 在仓库根目录执行
cat AGENTS.md
```
**必读内容：** 
- 第 1 节：你只能做什么
- 第 2 节：绝对禁止（15 条铁律，违反一次任务直接 REJECTED）
- 第 3 节：代码规范
- 第 4 节：每个任务的固定流程
- 第 5 节：失败处理规则
- 第 6 节：报告必须诚实

### 2. v3 总览
```bash
cat docs/v3/README.md
```
**理解：** 背景、核心原则、角色分工、v3 与旧代码的边界

### 3. 进度跟踪
```bash
cat docs/v3/STATUS.md
```
**确认：** 当前阶段、你被分配的任务、任务状态

### 4. 产品需求
```bash
cat docs/v3/01_REQUIREMENTS.md
```
**理解：** v3 要做什么、合格研究定义、最终交付标准

### 5. 架构设计
```bash
cat docs/v3/02_ARCHITECTURE.md
```
**理解：** 模块划分、数据流、接口契约、错误处理

### 6. 诊断报告（背景材料）
```bash
cat docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md
```
**理解：** 为什么要重建、旧代码有什么问题（这些错误不要重复）

---

## 🚀 第二步：执行第一个任务 T-000

### 读取任务卡
```bash
cat docs/v3/tasks/T-000-git-baseline.md
```

**这个任务要求你做什么：**
1. 把当前所有未提交改动存档到分支 `archive/v2-final-state-2026-10-23`
2. 从 2026-09-07 的干净提交创建分支 `v3/main`
3. 把 v3 文档（`AGENTS.md`、`docs/v3/`）合入 `v3/main`
4. 编写 `docs/v3/GIT_WORKFLOW.md`（分支命名、提交规则）
5. 更新 `.gitignore`（排除 v3 运行时数据）

### 开始执行

**严格按照 `AGENTS.md` 第 4 节"每个任务的固定流程"：**

1. **切分支：**
   ```bash
   # 从当前 Main 切出任务分支
   git checkout main
   git checkout -b v3/T-000-git-baseline
   ```

2. **更新 STATUS：**
   ```bash
   # 编辑 docs/v3/STATUS.md，找到 T-000 那一行：
   # 把状态从 READY 改为 IN_PROGRESS
   # 填写开始时间（格式：2026-10-23 15:00）
   # 保存并提交
   git add docs/v3/STATUS.md
   git commit -m "T-000: Mark as IN_PROGRESS"
   ```

3. **执行任务卡中的所有步骤：**
   - 创建 `archive/v2-final-state-2026-10-23` 分支并提交所有改动
   - 找到 2026-09-07 的最后干净提交，创建 `v3/main` 分支
   - 把 `AGENTS.md` 和 `docs/v3/` 合入 `v3/main`
   - 编写 `docs/v3/GIT_WORKFLOW.md`（参考任务卡第 4 节）
   - 更新 `.gitignore`（追加 v3 相关条目）
   - 创建 `data/v3/.gitkeep` 空文件

4. **运行验收命令（任务卡第 "验收命令" 节）：**
   ```bash
   # 逐条运行任务卡中的所有验收命令
   # 把命令和输出原样保存（至少最后 30 行）
   # 任何一条失败都必须修复后才能提交报告
   ```

5. **写报告：**
   ```bash
   # 复制模板
   cp docs/v3/templates/TASK_REPORT.md docs/v3/reports/T-000.md
   
   # 按模板填写所有章节：
   # - 执行摘要（2-5 句话）
   # - 交付物清单（所有新建/修改文件及行数）
   # - 验收命令执行结果（逐条粘贴命令与输出）
   # - 已知问题/偏离（没有就写"无"，不要隐瞒）
   # - Git 提交信息
   ```

6. **提交报告：**
   ```bash
   git add docs/v3/reports/T-000.md
   git commit -m "T-000: Submit task report"
   ```

7. **更新 STATUS 为 REVIEW：**
   ```bash
   # 编辑 docs/v3/STATUS.md，T-000 状态改为 REVIEW
   # 填写结束时间
   git add docs/v3/STATUS.md
   git commit -m "T-000: Mark as REVIEW"
   ```

8. **推送分支：**
   ```bash
   git push -u origin v3/T-000-git-baseline
   ```

9. **结束会话，等待架构师验收。**

---

## ⚠️ 关键约束（违反任何一条任务直接 REJECTED）

1. **只修改任务卡"允许修改的路径"中列出的文件/目录**
   - T-000 允许：新建分支、新建/修改 `docs/v3/GIT_WORKFLOW.md`、修改 `.gitignore`、创建 `data/v3/.gitkeep`
   - 禁止：修改任何其它文件

2. **任务卡没写的功能，一律不加**
   - 例如：不要加 pre-commit hook、不要写自动化脚本、不要改 `pyproject.toml`

3. **遇到问题写 Question，不要猜测**
   - 如果发现 2026-09-07 的提交不是干净基线，写 Question 而不是自己选更早的提交
   - 如果 git 操作失败，写 Question 而不是换一种方式

4. **验收命令必须全部通过**
   - 任务卡中的每条验收命令都必须执行并输出正确结果
   - 不通过就修复，不要"差不多就行"

5. **报告必须诚实**
   - "已知问题/偏离"一节：有问题就写，没有就写"无"
   - 不要写"完全符合任务卡"然后隐瞒实际偏离的地方

6. **禁止的 Git 操作（`AGENTS.md` 第 2 节）**
   - 禁止 `git push --force`
   - 禁止 `git commit --amend` 已推送的提交
   - 禁止 `git reset --hard` 有未提交工作时
   - 禁止 `--no-verify`

---

## 📝 遇到阻塞怎么办

如果任务卡有歧义、发现设计缺口、遇到技术问题无法按卡完成：

1. **立即停止**，不要继续尝试
2. **写 Question 文档：**
   ```bash
   cp docs/v3/templates/QUESTION.md docs/v3/questions/Q-001-<短名>.md
   # 填写：问题描述、现象/错误、根因分析、已尝试方案、建议方案
   git add docs/v3/questions/Q-001-*.md
   git commit -m "Q-001: <问题简述>"
   ```
3. **更新 STATUS：** T-000 状态改为 BLOCKED
4. **推送并结束会话**，等待架构师裁决

---

## ✅ 成功标准

T-000 任务视为成功完成，需同时满足：

1. 两个分支清晰可见：`archive/v2-final-state-2026-10-23` 和 `v3/main`
2. Archive 分支包含所有当前未提交内容（一次提交）
3. `v3/main` 从 2026-09-07 干净基线切出，包含 v3 文档
4. `docs/v3/GIT_WORKFLOW.md` 完整、可操作
5. `.gitignore` 正确排除 `data/v3/*.sqlite3` 等
6. 任务卡中所有验收命令通过
7. 报告诚实、完整（没有隐瞒已知问题）
8. 只修改了任务卡允许的路径

---

## 🔄 后续任务

T-000 验收通过后，架构师会：
1. 合并你的分支到 `v3/main`
2. 删除任务分支 `v3/T-000-git-baseline`
3. 通知你开始下一个任务（T-001 或其它）

每个任务都是独立的会话。完成一个任务后结束会话，等待通知再开始下一个。

---

## 📞 联系方式

- **遇到技术问题（代码、git）：** 写 Question，标记任务为 BLOCKED
- **任务卡有歧义：** 写 Question
- **需要新增依赖：** 写 Question，等批准
- **其它疑问：** 写 Question

**不要自行补全设计、不要猜测架构师的意图。** 有疑问就问，这是规则。

---

## 🎯 开始执行

现在按照上面"第二步"的流程，开始执行 T-000。

**预计时间：** 2 小时  
**当前时间：** 2026-10-23 约 15:00

祝顺利！记住：**诚实比完美更重要。**

---

**附：关键文档路径**
- 执行规则：`AGENTS.md`
- v3 总览：`docs/v3/README.md`
- 进度跟踪：`docs/v3/STATUS.md`（唯一真相来源）
- 任务卡：`docs/v3/tasks/T-000-git-baseline.md`
- 报告模板：`docs/v3/templates/TASK_REPORT.md`
- 问题模板：`docs/v3/templates/QUESTION.md`
- 诊断报告：`docs/diagnosis-2026-10-23/DIAGNOSIS_AND_SOLUTION.md`
