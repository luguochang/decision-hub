# 问题 / 阻塞模板

**执行智能体遇到无法按任务卡完成的情况时，填写本模板，提交到 `docs/v3/questions/Q-<三位序号>-<短名>.md`，并在 `STATUS.md` 中将任务标记为 `BLOCKED`。**

---

## 问题信息

- **问题 ID:** Q-[三位数字]
- **关联任务:** T-[XXX]
- **提出者:** [执行智能体名称/会话标识]
- **提出时间:** YYYY-MM-DD HH:MM
- **优先级:** [HIGH / MEDIUM / LOW]
- **状态:** OPEN (等待架构师裁决)

---

## 问题描述

[清晰描述遇到什么问题、在什么场景下、卡在哪一步]

示例：
- 任务 T-002 要求实现 OKX K 线采集，但任务卡未明确应该采集现货（SPOT）还是永续合约（SWAP）的 K 线。
- OKX API 要求 `instType` 参数，不同类型的 `instId` 格式不同（现货 `BTC-USDT`，合约 `BTC-USDT-SWAP`）。
- `01_REQUIREMENTS.md` 第 3.1 节只写了 `BTC-USDT-PERP`，但 OKX 实际交易对名称是 `BTC-USDT-SWAP`。

---

## 现象 / 错误信息

[如适用，贴上错误输出、日志、截图]

示例：
```bash
$ python -m packages.core_v3.feature_store.okx_adapter --symbol BTC-USDT-PERP
OKX API Error 400: {"code":"51001","msg":"Instrument ID does not exist"}
```

---

## 根因分析

[你的初步判断：为什么会出现这个问题]

示例：
- `01_REQUIREMENTS.md` 使用了通用的 `-PERP` 后缀表示永续合约，但 OKX 实际使用 `-SWAP`。
- 任务卡 T-002 没有定义 `symbol` 到 OKX `instId` 的映射规则。
- 现有代码 `packages/provider_adapters/market/okx.py` 第 48 行硬编码了 `BTC-USDT-SWAP`，但它在禁止 import 的旧模块中。

---

## 已尝试的方案

[列出你尝试过的方法及结果]

示例：
1. **方案 A:** 直接用 `BTC-USDT-SWAP` 替换 `BTC-USDT-PERP`。
   - **结果:** OKX API 调用成功，但与需求文档术语不一致，可能导致后续任务混乱。
2. **方案 B:** 在 `okx_adapter.py` 中写一个 `symbol` 映射函数 `_to_okx_inst_id("BTC-USDT-PERP") -> "BTC-USDT-SWAP"`。
   - **结果:** 能工作,但不知道这个映射逻辑是属于 Feature Store 职责还是应该在更上层统一定义。

---

## 建议方案

[你认为应该怎么解决，及理由]

示例：
**建议在 `packages/core_v3/` 下创建 `symbol_mapping.py`，集中定义所有交易所的 symbol 标准化规则。**

理由：
- v3 需求中 `BTC-USDT-PERP`、`ETH-USDT-PERP` 是产品层面的统一标识。
- 不同交易所格式不同（OKX 用 `-SWAP`，Binance 用 `BTCUSDT`），映射逻辑不应散落在各 adapter 中。
- 后续 Phase 4 会引入 Deribit 期权（格式 `BTC-25OCT24-80000-C`），需要统一的符号体系。

具体实现：
```python
# packages/core_v3/symbol_mapping.py
def to_okx_inst_id(symbol: str, inst_type: str = "SWAP") -> str:
    """BTC-USDT-PERP -> BTC-USDT-SWAP"""
    if symbol.endswith("-PERP"):
        return symbol.replace("-PERP", "-SWAP")
    raise ValueError(f"Unknown symbol format: {symbol}")
```

需要架构师确认：
1. 这个模块应该放在 `core_v3/` 哪里？
2. 是否需要定义一个 `StandardSymbol` Pydantic 类？
3. Binance、Deribit 的映射是否也在这个模块？

---

## 阻塞范围

[说明这个问题阻塞了哪些任务]

示例：
- T-002 无法完成验收命令第 3 步（写入真实 K 线）
- T-003（资金费率）也会遇到同样的 symbol 映射问题
- 不影响 T-001（代码骨架）和 T-004（检查工具）

---

## 决策记录（由架构师填写）

**决策时间:** [留空，架构师填写]

**决策:** [APPROVED / MODIFIED / REJECTED / NEEDS_DISCUSSION]

**裁决内容:** [留空，架构师填写]

示例：
```
采纳建议方案，修改如下：
1. 创建 `packages/core_v3/symbols.py`（不叫 symbol_mapping，因为它还会包含资产元数据）
2. 定义 `class Asset(BaseModel): symbol: str, okx_inst_id: str, ...`
3. 在该模块维护 `ASSETS = [Asset(symbol="BTC-USDT-PERP", okx_inst_id="BTC-USDT-SWAP", ...), ...]`
4. T-002 修改为先完成 symbols.py，再继续 okx_adapter.py
5. 更新 T-002 任务卡，追加"允许修改路径: packages/core_v3/symbols.py"

Q-001 关闭，T-002 从 BLOCKED 改为 IN_PROGRESS。
```

---

## 相关链接

[可选，补充材料]

- OKX 公共数据文档: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-candlesticks
- 旧系统 OKX 映射逻辑: `packages/provider_adapters/market/okx.py` 第 48-52 行
