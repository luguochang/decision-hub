# 02 — 架构设计

本文档定义 v3 的模块划分、数据流、接口、技术选型、错误处理。需求在 `01_REQUIREMENTS.md`，数据契约在 `03_DATA_CONTRACTS.md`。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  PC 前端 (apps/pc_v3/, React + Vite)                        │
│  - 报告展示  - 研究发起  - PAPER 操作  - 复盘中心           │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP/JSON
┌───────────────────────────┴─────────────────────────────────┐
│  API 服务 (apps/api_v3/, FastAPI)                           │
│  - 研究提交  - 报告查询  - PAPER 命令  - 事件订阅           │
└───────────────────────────┬─────────────────────────────────┘
                            │ DB + 消息队列
┌───────────────────────────┴─────────────────────────────────┐
│  Worker (apps/worker_v3/)                                    │
│  - 研究调度  - DSH 会话管理  - 阶段状态机  - PAPER 执行     │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│  Core 领域层 (packages/core_v3/)                            │
│  ┌─────────────────┐  ┌──────────────────┐                 │
│  │ Feature Store   │  │ Forecast Engine  │                 │
│  │ - 按 known_at   │  │ - 因果图         │                 │
│  │ - OKX / 期权    │  │ - 概率三层       │                 │
│  │ - 宏观 / 新闻   │  │ - 聚合与校准     │                 │
│  └─────────────────┘  └──────────────────┘                 │
│  ┌─────────────────┐  ┌──────────────────┐                 │
│  │ Research Runner │  │ PAPER Engine     │                 │
│  │ - 阶段状态机    │  │ - 记账           │                 │
│  │ - DSH 适配      │  │ - 止损/止盈      │                 │
│  │ - 回放          │  │ - 结算           │                 │
│  └─────────────────┘  └──────────────────┘                 │
│  ┌─────────────────┐  ┌──────────────────┐                 │
│  │ Evaluation      │  │ Reporting        │                 │
│  │ - Brier / log   │  │ - 自包含快照     │                 │
│  │ - 校准曲线      │  │ - JSON + HTML    │                 │
│  └─────────────────┘  └──────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│  外部依赖                                                    │
│  - DSH Web Host (extensions/dsh/decision-hub/, 已有)        │
│  - OKX 公共 API                                             │
│  - 预测市场 / 期权 API (Phase 4 引入)                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 模块职责

### 2.1 Feature Store (`packages/core_v3/feature_store/`)

**职责：** 所有外部数据按 `known_at` 索引存储，提供统一的"截至某时点的信息集"查询接口。

**子模块：**
- `storage.py`: SQLite 表定义（`features` 表：`feature_type, symbol, known_at, data_json, source_id`）
- `okx_adapter.py`: OKX K线、资金费率、OI 采集与写入
- `macro_adapter.py`: 宏观日历、CPI 实际值/共识（Phase 4）
- `options_adapter.py`: Deribit 隐含波动、偏度（Phase 3/4）
- `query.py`: `get_features_as_of(asset, known_at, feature_types)` → dict

**关键约束：**
- 同一 `(feature_type, symbol, known_at, source_id)` 不可变，写入即冻结。
- 历史回放与实时研究从同一接口读取。

### 2.2 Forecast Engine (`packages/core_v3/forecast/`)

**职责：** 概率生成、因果图填充、聚合、校准。

**子模块：**
- `base_rate.py`: 历史同类情况频率分布
- `market_anchor.py`: 从预测市场/期权读取隐含概率（Phase 4引入，Phase 2 用 None）
- `llm_adjuster.py`: LLM 证据调整，输出偏离幅度与理由
- `aggregator.py`: 多视角聚合（中位数/对数几率平均）
- `calibrator.py`: Platt 或保序回归校准器（Phase 7 训练）
- `causal_graph.py`: 因果图模板定义与节点填充（Phase 4）

**Phase 2 简化：** 只实现 `base_rate` + `llm_adjuster`，锚点与校准器返回 None。

### 2.3 Research Runner (`packages/core_v3/research/`)

**职责：** 阶段状态机、DSH 会话管理、检查点续跑、回放。

**阶段定义：**
1. `PREPARE`: 冻结信息集、计算基率、读取锚点
2. `EVIDENCE`: DSH 会话采集证据
3. `FORECAST`: 概率生成
4. `PLAN`: 多空方案对比
5. `REVIEW`: 独立审核
6. `PUBLISH`: 生成自包含报告
7. `DONE` / `FAILED`

**子模块：**
- `state_machine.py`: 状态转移、检查点存储
- `dsh_session.py`: DSH API 调用、cassette 录制/回放
- `stage_*.py`: 每个阶段的具体逻辑

### 2.4 PAPER Engine (`packages/core_v3/paper/`)

**职责：** 纸面交易全生命周期。

**子模块：**
- `account.py`: 账户、权益、名义、杠杆计算
- `order.py`: 确认 → 排队 → 成交（用 OKX 真实 K 线回填价格）
- `position.py`: 持仓、未实现盈亏、资金费累计
- `protection.py`: 止损/止盈监控
- `settlement.py`: 平仓结算、流水归档

### 2.5 Evaluation (`packages/core_v3/evaluation/`)

**职责：** 预测评分、校准曲线、分层统计。

- `metrics.py`: Brier、log loss、ECE
- `calibration_curve.py`: 可靠性曲线绘制
- `stratified.py`: 按资产/期限/事件族分层

### 2.6 Reporting (`packages/core_v3/reporting/`)

**职责：** 自包含报告生成（JSON + HTML），历史迁移。

- `snapshot.py`: 报告快照结构（Pydantic）
- `renderer.py`: HTML 渲染（Jinja2 或内联）
- `storage.py`: 报告存储与查询

---

## 3. 数据流

### 3.1 普通市场研究（手动发起）

```
用户点击"发起研究" (PC)
  ↓ HTTP POST /api/v3/research/submit
API 写入 research_requests 表，返回 request_id
  ↓ Worker 轮询或收到消息
Worker: PREPARE 阶段
  → Feature Store: get_features_as_of(BTC, now(), [kline, funding, oi, macro])
  → Forecast Engine: base_rate(BTC, 4h, similar_vol_env)
  → 写 checkpoint
Worker: EVIDENCE 阶段
  → DSH session: 带信息集 + 工具调用
  → 录制 cassette
  → 写 checkpoint
Worker: FORECAST 阶段
  → Forecast Engine: llm_adjuster + aggregator
  → 写概率到 forecasts 表
Worker: PLAN 阶段
  → 计算 Taker 费用、资金费、止损/止盈
  → 写 plans 表
Worker: REVIEW 阶段
  → 独立 DSH session (反方视角)
  → 若退回 → 回到 EVIDENCE
Worker: PUBLISH 阶段
  → Reporting: 生成自包含 JSON + HTML
  → 写 reports 表，状态 = DONE
  ↓
PC 轮询 /api/v3/research/{request_id}/status
  → 完成后 GET /api/v3/reports/{report_id}
  → 展示报告
```

### 3.2 PAPER 成交与止损

```
用户点击"PAPER 确认" (PC)
  ↓ POST /api/v3/paper/orders
API 写 paper_orders (status=PENDING)
  ↓ Worker 轮询
Worker: 验证权益、杠杆
  → paper_orders.status = QUEUED
Worker: 下一 K 线收盘
  → 用 OKX K线 OHLC 回填成交价
  → paper_orders.status = FILLED
  → 写 paper_positions
  ↓ Worker 监控循环
每分钟: 读取 open positions
  → 用最新 K 线计算未实现盈亏
  → 检查止损/止盈
  → 若触发 → 平仓 → paper_fills → paper_positions.status = CLOSED
```

---

## 4. 技术选型

| 层 | 技术 | 理由 |
|---|---|---|
| 前端 | React 19 + Vite + TanStack Query | 已有技术栈，复用 `apps/decision-desk/` 设计 |
| API | FastAPI + Pydantic v2 | 已有，类型安全 |
| Worker | Python asyncio + APScheduler | 轻量，可单进程 |
| 数据库 | SQLite (WAL 模式) | 单用户，简化部署；Phase 8 可选迁移 PostgreSQL |
| DSH | 已有 `extensions/dsh/decision-hub/` | 复用，录制通过 bridge API |
| 模型 | gpt-6.1-sol (OpenAI-compatible) | 已配置 |
| 测试 | pytest + pytest-asyncio + httpx MockTransport | 已有 |
| Lint/Type | ruff + pyright strict | 已有 |

---

## 5. 接口契约

### 5.1 Feature Store

```python
@dataclass
class Feature:
    feature_type: str  # "kline_1m", "funding_rate", "oi", "macro_event"
    symbol: str        # "BTC-USDT-PERP"
    known_at: datetime # UTC, 带时区
    data: dict[str, Any]
    source_id: str

def get_features_as_of(
    symbol: str,
    as_of: datetime,
    feature_types: list[str],
) -> list[Feature]:
    """返回截至 as_of 时点已知的所有指定类型特征，按 known_at 升序"""
```

### 5.2 Forecast Engine

```python
@dataclass
class ForecastInput:
    asset: str
    horizon: Literal["4h", "24h", "72h"]
    as_of: datetime
    features: list[Feature]
    evidence: list[dict]  # DSH 会话输出的结构化证据

@dataclass
class ForecastOutput:
    probabilities: dict[str, float]  # {"up": 0.45, "down": 0.25, "neutral": 0.30}
    base_rate: dict[str, float] | None
    market_anchor: dict[str, float] | None
    adjustment_reason: str
    causal_graph_nodes: list[dict] | None  # Phase 4

def forecast(input: ForecastInput) -> ForecastOutput:
    ...
```

### 5.3 Research Runner

```python
class ResearchStage(str, Enum):
    PREPARE = "prepare"
    EVIDENCE = "evidence"
    FORECAST = "forecast"
    PLAN = "plan"
    REVIEW = "review"
    PUBLISH = "publish"
    DONE = "done"
    FAILED = "failed"

async def run_research(request_id: str) -> str:
    """返回 report_id 或抛异常"""
```

---

## 6. 错误处理

### 6.1 错误码体系

所有领域错误继承 `CoreError(Exception)`，携带 `code: ErrorCode`。

```python
# packages/core_v3/errors.py
class ErrorCode(str, Enum):
    # Feature Store
    FEATURE_NOT_FOUND = "feature_not_found"
    FEATURE_IMMUTABLE_VIOLATION = "feature_immutable_violation"
    
    # Forecast
    PROBABILITY_SUM_INVALID = "probability_sum_invalid"
    BASE_RATE_INSUFFICIENT = "base_rate_insufficient"
    
    # Research
    STAGE_TRANSITION_INVALID = "stage_transition_invalid"
    DSH_SESSION_TIMEOUT = "dsh_session_timeout"
    DSH_SESSION_CANCELLED = "dsh_session_cancelled"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    REVIEW_REJECTED = "review_rejected"
    
    # PAPER
    PAPER_INSUFFICIENT_EQUITY = "paper_insufficient_equity"
    PAPER_POSITION_NOT_FOUND = "paper_position_not_found"
    
    # System
    CASSETTE_NOT_FOUND = "cassette_not_found"
    REPLAY_MISMATCH = "replay_mismatch"

class CoreError(Exception):
    def __init__(self, code: ErrorCode, message: str, **context):
        self.code = code
        self.message = message
        self.context = context
        super().__init__(f"[{code.value}] {message}")
```

### 6.2 分层重试

- **瞬时错误（网络超时、API 限流）：** 阶段内重试，最多 3 次，指数退避。
- **业务错误（证据不足、审核拒绝）：** 不重试，写 checkpoint，人工介入或条件变化后续跑。
- **系统错误（数据库锁、OOM）：** 记录日志，研究状态 = FAILED，不自动重试。

### 6.3 日志结构

```python
import structlog
logger = structlog.get_logger()

logger.info(
    "research_stage_completed",
    request_id=request_id,
    stage="evidence",
    duration_ms=elapsed,
    evidence_count=len(evidence),
)
```

---

## 7. 部署架构（v3 单机模式）

```
Windows 10/11
├── Python 3.12 venv
├── Node.js 24 (前端构建)
├── SQLite DB (data/v3/decision_hub_v3.sqlite3)
├── Cassette 存储 (data/v3/cassettes/)
├── 进程:
│   ├── api_v3: uvicorn, 端口 8050
│   ├── worker_v3: Python 主循环
│   └── pc_v3: Vite dev (开发) 或 静态文件 (生产)
└── DSH Web Host (已有, 端口 3097)
```

**Phase 8 可选：** Docker Compose 封装、PostgreSQL 替换 SQLite。

---

## 8. 与旧系统边界

| 方面 | v3 | 旧系统 |
|---|---|---|
| 数据库 | `data/v3/decision_hub_v3.sqlite3` | `data/core-live-20260913/db/decision_hub.sqlite3` (只读) |
| 端口 | API 8050, PC 8051 | API 8047, PC 8047 (不冲突) |
| 代码 | `packages/core_v3/`, `apps/*_v3/` | `packages/kernel/` 等 (只读) |
| Import | 禁止 import 旧模块 | - |
| 运行 | 独立进程 | 保持运行(只读查询历史样本) |

---

本文档由架构师维护。接口变更需同步更新 `03_DATA_CONTRACTS.md`。
