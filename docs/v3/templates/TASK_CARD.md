# 任务卡模板

**复制本模板创建新任务卡。所有 `[占位符]` 必须替换为具体内容。**

---

## 任务信息

- **任务 ID:** T-[三位数字]
- **任务名:** [20 字以内简短名称]
- **所属阶段:** Phase [N]
- **预计工时:** [X 小时 或 X 天]
- **依赖任务:** [T-XXX, T-YYY] 或 无
- **分配给:** [执行智能体名称] 或 未分配

---

## 目标

[1-3 句话描述本任务要实现什么，为什么需要它]

---

## 输入

[列出本任务依赖的前置产物、配置、数据]

示例：
- T-001 创建的 `packages/core_v3/` 目录结构
- `docs/v3/03_DATA_CONTRACTS.md` 中的 `Feature` 数据类定义
- OKX 公共 API 文档（无需认证的 K 线接口）

---

## 输出

[列出本任务完成后交付的具体文件、功能、数据]

示例：
- `packages/core_v3/feature_store/storage.py`: `features` 表定义与迁移
- `packages/core_v3/feature_store/okx_adapter.py`: `OKXAdapter.fetch_klines()` 方法
- `tests/v3/feature_store/test_okx_adapter.py`: 单元测试，≥ 3 个用例
- `data/v3/decision_hub_v3.sqlite3`: 表已创建，可写入一条 K 线记录

---

## 详细需求

### 1. [子需求 A]

[具体要求，包括：函数签名、字段、约束、边界条件]

示例：
- `fetch_klines(symbol: str, start: datetime, end: datetime) -> list[Feature]`
- 每条 K 线转为一个 `Feature`，`feature_type = "kline_1m"`
- `known_at` 设为该 K 线的收盘时间 + 1 秒（模拟数据可知时点）
- 若 OKX API 返回错误（HTTP 429/500），抛 `CoreError(FEATURE_FETCH_FAILED, ...)`
- 单次请求不超过 100 根 K 线

### 2. [子需求 B]

...

---

## 禁止事项

[明确列出本任务**不应该**做的事]

示例：
- 禁止实现 K 线之外的其它特征类型（资金费率、OI），那是 T-003 的范围
- 禁止添加缓存层，Phase 0 只关注核心读写
- 禁止修改 `packages/kernel/` 下的旧代码

---

## 必须的测试

[列出必须编写并通过的测试用例]

示例：
1. `test_fetch_klines_success`: Mock OKX 返回正常数据，验证写入 `features` 表，`known_at` 正确
2. `test_fetch_klines_http_error`: Mock OKX 返回 500，验证抛出 `CoreError` 且错误码正确
3. `test_immutability`: 写入同一 `(feature_type, symbol, known_at)`，第二次写入抛错

---

## 允许修改的路径

[明确列出本任务可以新建/修改的文件/目录，其它路径只读]

示例：
- `packages/core_v3/feature_store/` (新建目录与所有文件)
- `tests/v3/feature_store/` (新建目录与所有文件)
- `migrations_v3/` (新建目录，Alembic 迁移脚本)
- `data/v3/` (新建目录，数据库文件)
- `pyproject.toml` (仅追加 `alembic` 依赖，需在报告中说明)

---

## 验收命令

[列出验收时必须运行的命令，及其预期输出]

示例：

```bash
# 1. Lint 与类型检查
.venv\Scripts\python.exe tools\v3_check.py
# 预期: 零错误

# 2. 运行测试
.venv\Scripts\pytest.exe tests/v3/feature_store/ -v
# 预期: 全部通过

# 3. 写入一条真实 K 线并查询
.venv\Scripts\python.exe -m packages.core_v3.feature_store.okx_adapter \
    --symbol BTC-USDT-PERP --start "2026-10-20T00:00:00Z" --end "2026-10-20T00:01:00Z"
# 预期: 输出 "Written 1 feature(s)"

sqlite3 data/v3/decision_hub_v3.sqlite3 "SELECT COUNT(*) FROM features WHERE feature_type='kline_1m'"
# 预期: 1

# 4. 检查 known_at 时区
sqlite3 data/v3/decision_hub_v3.sqlite3 "SELECT known_at FROM features LIMIT 1"
# 预期: 格式为 "2026-10-20 00:01:01+00:00" (带时区)
```

---

## 验收标准

[定性标准，补充验收命令无法覆盖的]

- 代码遵循 `AGENTS.md` §3 规范（行数、类型标注、注释）
- `Feature` 数据类与 `03_DATA_CONTRACTS.md` 定义完全一致
- 错误信息清晰，包含 `symbol`、`start`、`end` 上下文
- 无 TODO、无注释掉的代码、无调试 print

---

## 参考资料

[可选，列出有助于完成任务的文档/链接]

- OKX 公共 K 线 API: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-candlesticks
- Alembic 文档: https://alembic.sqlalchemy.org/
- 旧系统 OKX 适配器（仅参考，不要 import）: `packages/provider_adapters/market/okx.py`

---

## 备注

[可选，其它说明]
