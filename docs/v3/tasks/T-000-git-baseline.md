# T-000: Git 基线与分支策略

## 任务信息

- **任务 ID:** T-000
- **任务名:** Git 基线与分支策略
- **所属阶段:** Phase 0
- **预计工时:** 2 小时
- **依赖任务:** 无（Phase 0 第一个任务）
- **分配给:** 未分配

---

## 目标

建立 v3 重建的 git 基线：提交当前所有未跟踪改动到存档分支，创建 v3 干净起点分支，定义后续分支命名与合并规则。

---

## 输入

- 当前仓库状态：Main 分支，最后提交 2026-09-07，之后约 20 万行未提交改动（248 修改 + 1527 未跟踪文件）
- `docs/diagnosis-2026-10-23/` 下的诊断文档
- 本文件（T-000 任务卡）

---

## 输出

- **分支 `archive/v2-final-state-2026-10-23`:** 包含当前所有未提交内容的存档，一次提交。
- **分支 `v3/main`:** 从最后一次干净提交（2026-09-07）切出，作为 v3 的基准分支。
- **文件 `docs/v3/GIT_WORKFLOW.md`:** v3 分支命名、提交、合并规则。
- **验证:** `git log --oneline --graph --all` 显示清晰的分支结构。

---

## 详细需求

### 1. 存档当前状态

从 Main 当前状态切出分支 `archive/v2-final-state-2026-10-23`，提交所有改动（包括 untracked）:

```bash
git checkout -b archive/v2-final-state-2026-10-23
git add -A
git commit -m "archive: Final state of v1/v2 codebase as of 2026-10-23

This commit preserves all uncommitted work from 2026-09-07 to 2026-10-23,
including 248 modified files and 1527 untracked files.

Purpose: historical reference only. v3 rebuild starts from a clean baseline.

Includes:
- Diagnosis documents in docs/diagnosis-2026-10-23/
- Research recipe versions in packages/kernel/research_recipe_versions/
- All other uncommitted worker, API, and extension changes

Do NOT base new work on this branch."
```

### 2. 创建 v3 基准分支

找到 Main 上最后一次干净提交（预计是 2026-09-07 的某个 commit），从那里切出 `v3/main`:

```bash
# 找到最后干净提交的哈希
git log --oneline main | head -20
# 假设是 abc1234，替换下面的哈希

git checkout abc1234  # 替换为实际哈希
git checkout -b v3/main
```

此时 `v3/main` 分支是干净的 9-07 状态，不包含后续的 20 万行改动。

### 3. 把 v3 文档和 AGENTS.md 合入 `v3/main`

在 `v3/main` 分支上，cherry-pick 或手动复制以下文件（这些是架构师在 Main 上新建的，需要带到 v3/main）：

- `AGENTS.md`
- `docs/v3/` 目录下的所有文件（README.md, STATUS.md, 01_REQUIREMENTS.md, 02_ARCHITECTURE.md, templates/）

提交：
```bash
git add AGENTS.md docs/v3/
git commit -m "v3: Add project structure and agent instructions

- AGENTS.md: execution rules for Codex
- docs/v3/: requirements, architecture, templates, status tracking

This forms the baseline for Decision Hub v3 rebuild."
```

### 4. 编写 Git 工作流文档

创建 `docs/v3/GIT_WORKFLOW.md`，内容包括：

#### 分支命名规则

- **`v3/main`:** v3 的主干分支，只接受已验收任务的合并，受保护。
- **`v3/<任务ID>-<短名>`:** 任务分支，例如 `v3/T-001-feature-store`。
- **`archive/*`:** 存档分支，只读。

#### 任务分支工作流

1. 从 `v3/main` 切出任务分支：`git checkout v3/main && git pull && git checkout -b v3/T-XXX-name`
2. 在任务分支上开发、提交（可以多次提交）
3. 完成后推送：`git push -u origin v3/T-XXX-name`
4. 提交报告，STATUS 改为 REVIEW
5. 架构师验收：
   - ACCEPTED → 架构师合并到 `v3/main`，删除任务分支
   - REJECTED → 执行智能体在原分支修复，推送，重新 REVIEW

#### 提交信息格式

```
<任务ID>: <一句话描述>

[可选的详细说明]

[可选的 issue/ticket 引用]
```

示例：
```
T-002: Implement OKX kline fetching in Feature Store

- Added okx_adapter.py with fetch_klines method
- Created features table migration
- Added 3 unit tests
```

#### 禁止的 Git 操作

- 禁止 `git push --force` 到 `v3/main`
- 禁止 `git commit --amend` 已推送的提交
- 禁止 `git reset --hard` 有未提交工作时
- 禁止直接在 `v3/main` 上提交（必须通过任务分支合并）
- 禁止修改 `archive/*` 分支

### 5. 更新 .gitignore

确保 `.gitignore` 包含 v3 运行时数据：

```gitignore
# v3 runtime data
data/v3/*.sqlite3
data/v3/*.sqlite3-shm
data/v3/*.sqlite3-wal
data/v3/cassettes/
```

但保留 `data/v3/.gitkeep` 可提交。

---

## 禁止事项

- 禁止修改 Main 分支或其它已有分支
- 禁止删除 `archive/v2-final-state-2026-10-23` 分支
- 禁止在本任务中开始编写业务代码（Feature Store、API 等）

---

## 必须的测试

无（本任务是 git 操作，无代码逻辑）。

---

## 允许修改的路径

- 新建分支：`archive/v2-final-state-2026-10-23`, `v3/main`
- 在 `v3/main` 上新建/修改：
  - `docs/v3/GIT_WORKFLOW.md`
  - `.gitignore`（追加 v3 相关条目）
  - `data/v3/.gitkeep`（新建空文件）

---

## 验收命令

```bash
# 1. 检查分支存在
git branch -a | grep -E '(archive/v2-final-state-2026-10-23|v3/main)'
# 预期: 两个分支都存在

# 2. 检查 archive 分支有且仅有一次提交（相对于切出点）
git checkout archive/v2-final-state-2026-10-23
git log --oneline -1
# 预期: 提交信息包含 "archive: Final state"

# 3. 检查 v3/main 是干净的 9-07 基线 + v3 文档
git checkout v3/main
git log --oneline -5
# 预期: 最近一次提交是 "v3: Add project structure..."，之前是 9-07 的提交

# 4. 检查 v3/main 上有 v3 文档
ls docs/v3/
# 预期: README.md, STATUS.md, 01_REQUIREMENTS.md, 02_ARCHITECTURE.md, templates/, tasks/, reports/, questions/

# 5. 检查 AGENTS.md 存在
cat AGENTS.md | head -5
# 预期: 文件存在，首行是 "# AGENTS.md"

# 6. 检查 GIT_WORKFLOW.md 存在
cat docs/v3/GIT_WORKFLOW.md | head -10
# 预期: 文件存在，包含分支命名规则

# 7. 检查 .gitignore 包含 v3 条目
grep "data/v3/\*.sqlite3" .gitignore
# 预期: 有匹配行

# 8. 检查 git 图
git log --oneline --graph --all -20
# 预期: 能看到 v3/main 和 archive 分支的分叉点
```

---

## 验收标准

- `archive/v2-final-state-2026-10-23` 包含所有当前未提交内容，一次提交，不再修改
- `v3/main` 从 9-07 干净基线切出，包含 AGENTS.md、docs/v3/ 及 GIT_WORKFLOW.md
- 两个分支的分叉点清晰可见（`git log --graph`）
- `docs/v3/GIT_WORKFLOW.md` 完整、可操作，后续任务可直接遵循
- `.gitignore` 正确排除 v3 运行时数据

---

## 参考资料

- Git 分支最佳实践: https://git-scm.com/book/en/v2/Git-Branching-Branching-Workflows
- 本项目旧的 git 历史: `git log --oneline --all`

---

## 备注

**重要:** 本任务完成后，所有后续 v3 任务都从 `v3/main` 切分支。Main 分支和 `archive/*` 分支不再参与 v3 开发。

如果执行过程中发现 9-07 的提交不是最后干净的基线（例如有更早的干净点），可以选择更早的提交作为 `v3/main` 起点，并在报告中说明。
