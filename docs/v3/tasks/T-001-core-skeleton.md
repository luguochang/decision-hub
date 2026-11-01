# T-001: v3 代码骨架与模块划分

## 任务信息

- **任务 ID:** T-001
- **任务名:** v3 代码骨架与模块划分
- **所属阶段:** Phase 0
- **预计工时:** 3 小时
- **依赖任务:** T-000（Git 基线）
- **分配给:** 未分配

---

## 目标

创建 v3 的完整目录结构、空模块文件、`__init__.py`，确保 Python 能正确 import，为后续任务提供干净的代码骨架。

---

## 输入

- T-000 创建的 `v3/main` 分支
- `docs/v3/02_ARCHITECTURE.md` 中的模块划分设计

---

## 输出

- `packages/core_v3/` 完整目录结构与空模块
- `apps/api_v3/` FastAPI 应用骨架
- `apps/worker_v3/` Worker 应用骨架
- `apps/pc_v3/` PC 前端项目骨架（React + Vite）
- `tests/v3/` 测试目录结构
- `data/v3/` 数据目录结构
- `migrations_v3/` Alembic 迁移目录
- `pyproject.toml` 新增 v3 scripts 入口点

---

## 详细需求

### 1. Core 领域层目录结构

创建 `packages/core_v3/` 及其子模块：

```
packages/core_v3/
├── __init__.py
├── errors.py                    # 错误码与异常类（T-006 实现）
├── symbols.py                   # 资产符号映射（本任务创建空文件）
├── feature_store/
│   ├── __init__.py
│   ├── storage.py              # 表定义（T-002 实现）
│   ├── query.py                # 查询接口（T-002 实现）
│   ├── okx_adapter.py          # OKX 适配器（T-002 实现）
│   ├── macro_adapter.py        # 宏观数据适配器（Phase 4）
│   └── options_adapter.py      # 期权数据适配器（Phase 4）
├── forecast/
│   ├── __init__.py
│   ├── base_rate.py            # 历史基率（Phase 2）
│   ├── market_anchor.py        # 市场锚点（Phase 4）
│   ├── llm_adjuster.py         # LLM 调整（Phase 2）
│   ├── aggregator.py           # 多视角聚合（Phase 2）
│   ├── calibrator.py           # 校准器（Phase 7）
│   └── causal_graph.py         # 因果图（Phase 4）
├── research/
│   ├── __init__.py
│   ├── state_machine.py        # 状态机（T-005 实现）
│   ├── dsh_session.py          # DSH 会话管理（T-003 实现）
│   ├── stage_prepare.py        # PREPARE 阶段（Phase 1）
│   ├── stage_evidence.py       # EVIDENCE 阶段（Phase 1）
│   ├── stage_forecast.py       # FORECAST 阶段（Phase 2）
│   ├── stage_plan.py           # PLAN 阶段（Phase 2）
│   ├── stage_review.py         # REVIEW 阶段（Phase 2）
│   └── stage_publish.py        # PUBLISH 阶段（Phase 2）
├── paper/
│   ├── __init__.py
│   ├── account.py              # 账户逻辑（Phase 6）
│   ├── order.py                # 订单逻辑（Phase 6）
│   ├── position.py             # 持仓逻辑（Phase 6）
│   ├── protection.py           # 止损止盈（Phase 6）
│   └── settlement.py           # 结算逻辑（Phase 6）
├── evaluation/
│   ├── __init__.py
│   ├── metrics.py              # 评分指标（Phase 7）
│   ├── calibration_curve.py    # 校准曲线（Phase 7）
│   └── stratified.py           # 分层统计（Phase 7）
└── reporting/
    ├── __init__.py
    ├── snapshot.py             # 报告快照（Phase 1）
    ├── renderer.py             # HTML 渲染（Phase 2）
    └── storage.py              # 报告存储（Phase 2）
```

所有文件本任务只创建空文件（带文件级注释说明用途）或简单的 `pass` 占位类/函数。

### 2. API 应用骨架

创建 `apps/api_v3/`:

```
apps/api_v3/
├── __init__.py
├── main.py                     # FastAPI 应用入口（本任务实现基本结构）
├── config.py                   # 配置加载（Phase 1）
├── research_routes.py          # 研究 API（Phase 2）
├── report_routes.py            # 报告查询 API（Phase 2）
├── paper_routes.py             # PAPER API（Phase 6）
└── monitor_routes.py           # 监控 API（Phase 5）
```

`main.py` 实现：
```python
from fastapi import FastAPI

app = FastAPI(title="Decision Hub v3 API", version="0.1.0")

@app.get("/health")
async def health():
    return {"status": "ok"}

# 其它路由在后续 Phase 实现
```

### 3. Worker 应用骨架

创建 `apps/worker_v3/`:

```
apps/worker_v3/
├── __init__.py
├── main.py                     # Worker 主循环（Phase 1）
├── research_scheduler.py       # 研究调度（Phase 2）
├── paper_monitor.py            # PAPER 监控（Phase 6）
└── condition_monitor.py        # 条件监控（Phase 5）
```

`main.py` 占位：
```python
import asyncio

async def main():
    print("Worker v3 starting...")
    # 主循环在 Phase 1 实现
    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
```

### 4. PC 前端骨架

创建 `apps/pc_v3/`:

```
apps/pc_v3/
├── package.json                # React + Vite 项目配置
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx                # 入口
│   ├── App.tsx                 # 主组件
│   └── pages/
│       ├── ResearchPage.tsx    # 研究发起页（Phase 2）
│       ├── ReportPage.tsx      # 报告展示页（Phase 2）
│       ├── PaperPage.tsx       # PAPER 操作页（Phase 6）
│       └── ReviewPage.tsx      # 复盘中心（Phase 7）
└── public/
```

`package.json`:
```json
{
  "name": "decision-hub-pc-v3",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 8051",
    "build": "tsc && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0",
    "@tanstack/react-query": "^5.66.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.2",
    "vite": "^6.0.7",
    "vitest": "^2.1.8"
  }
}
```

`src/main.tsx`:
```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

`src/App.tsx`:
```tsx
export default function App() {
  return (
    <div>
      <h1>Decision Hub v3</h1>
      <p>PC v3 placeholder. Pages will be implemented in Phase 2+.</p>
    </div>
  )
}
```

### 5. 测试目录

创建 `tests/v3/`:

```
tests/v3/
├── __init__.py
├── conftest.py                 # pytest fixtures（T-007 实现）
├── feature_store/
│   └── __init__.py
├── forecast/
│   └── __init__.py
├── research/
│   └── __init__.py
├── paper/
│   └── __init__.py
├── evaluation/
│   └── __init__.py
└── reporting/
    └── __init__.py
```

### 6. 数据与迁移目录

创建：
- `data/v3/.gitkeep`
- `migrations_v3/` Alembic 初始化（运行 `alembic init migrations_v3`）

### 7. 更新 pyproject.toml

追加 v3 scripts:
```toml
[project.scripts]
api-v3 = "apps.api_v3.main:run"
worker-v3 = "apps.worker_v3.main:main"
```

---

## 禁止事项

- 禁止实现任何业务逻辑（概率计算、DSH 调用等），只创建空文件和占位代码
- 禁止修改旧代码（`packages/kernel/`、`apps/hub_*` 等）
- 禁止新增依赖，`package.json` 中的依赖都是已有技术栈
- 禁止在 `__init__.py` 中写 `__all__`，保持空或只有注释

---

## 必须的测试

### 1. 测试 Python 模块可 import

```python
# tests/v3/test_imports.py
def test_core_v3_imports():
    """所有 core_v3 子模块都可以 import"""
    from packages.core_v3 import errors, symbols
    from packages.core_v3.feature_store import storage, query
    from packages.core_v3.forecast import base_rate
    from packages.core_v3.research import state_machine
    from packages.core_v3.paper import account
    from packages.core_v3.evaluation import metrics
    from packages.core_v3.reporting import snapshot

def test_apps_v3_imports():
    """v3 应用可以 import"""
    from apps.api_v3 import main as api_main
    from apps.worker_v3 import main as worker_main
```

### 2. 测试 API 健康检查

```python
# tests/v3/test_api_health.py
from fastapi.testclient import TestClient
from apps.api_v3.main import app

def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

### 3. 测试 PC 前端能启动

（手动验收命令，见下方）

---

## 允许修改的路径

- `packages/core_v3/` (新建全部)
- `apps/api_v3/` (新建全部)
- `apps/worker_v3/` (新建全部)
- `apps/pc_v3/` (新建全部)
- `tests/v3/` (新建全部)
- `data/v3/` (新建目录)
- `migrations_v3/` (新建目录)
- `pyproject.toml` (追加 scripts 入口点)
- `.gitignore` (如需追加 PC 前端 node_modules、dist 排除规则)

---

## 验收命令

```bash
# 1. Lint 与类型检查
.venv\Scripts\python.exe tools\v3_check.py
# 预期: 零错误（T-004 创建 v3_check.py 后才能运行，本任务暂时用下面的手动检查）

# 手动检查（T-004 之前）:
.venv\Scripts\ruff.exe check packages/core_v3 apps/api_v3 apps/worker_v3 tests/v3
.venv\Scripts\pyright.exe packages/core_v3 apps/api_v3 apps/worker_v3 tests/v3
# 预期: 零错误

# 2. 测试 import
.venv\Scripts\pytest.exe tests/v3/test_imports.py -v
# 预期: 全部通过

# 3. 测试 API 健康检查
.venv\Scripts\pytest.exe tests/v3/test_api_health.py -v
# 预期: 通过

# 4. 手动启动 API
.venv\Scripts\python.exe -m uvicorn apps.api_v3.main:app --host 127.0.0.1 --port 8050
# 在另一个终端:
curl http://127.0.0.1:8050/health
# 预期: {"status":"ok"}
# Ctrl+C 停止

# 5. 手动启动 Worker
.venv\Scripts\python.exe apps/worker_v3/main.py
# 预期: 输出 "Worker v3 starting..." 然后退出

# 6. PC 前端安装依赖并启动
cd apps/pc_v3
pnpm install
pnpm dev
# 预期: 浏览器打开 http://127.0.0.1:8051，显示 "Decision Hub v3" 占位页面
# Ctrl+C 停止
cd ../..

# 7. 检查目录结构
ls -R packages/core_v3 apps/api_v3 apps/worker_v3 apps/pc_v3 tests/v3 data/v3 migrations_v3
# 预期: 所有目录和占位文件都存在

# 8. 检查 pyproject.toml 入口点
grep -A2 '\[project.scripts\]' pyproject.toml
# 预期: 包含 api-v3 和 worker-v3
```

---

## 验收标准

- Python 代码通过 ruff 与 pyright strict
- 所有 `packages/core_v3/` 子模块可 import
- API 健康检查返回 200
- Worker 能运行（即使只是占位）
- PC 前端能在 8051 端口显示占位页面
- 目录结构与本任务卡"详细需求"完全一致
- 所有空文件都有文件级注释说明用途
- `pyproject.toml` 入口点正确

---

## 参考资料

- `docs/v3/02_ARCHITECTURE.md` 第 2 节（模块职责）
- FastAPI 文档: https://fastapi.tiangolo.com/
- Vite + React 文档: https://vitejs.dev/guide/

---

## 备注

本任务创建的都是"骨架"，没有业务逻辑。目的是让后续任务有清晰的位置放代码，避免"该放哪里"的疑问。

所有占位文件都应该包含一个文件级注释，格式：
```python
"""
<模块名>

用途: <一句话说明>
实现阶段: Phase X, T-YYY
"""
```

例如 `packages/core_v3/forecast/calibrator.py`:
```python
"""
Forecast calibration module.

用途: 对 LLM 概率进行 Platt 或保序回归校准
实现阶段: Phase 7
"""

# 占位，Phase 7 实现
```
