"""Deterministic daily BTC direction baseline.

This module intentionally has no model or network dependency.  It turns a
closed daily OHLCV sequence into an auditable research outlook.  A receipt is
returned with the input hash and every value used by the rule so a report can
be recomputed without an LLM.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Any


class DailyStrategyInputError(ValueError):
    """Raised when daily bars cannot support a deterministic calculation."""


@dataclass(frozen=True)
class DailyBar:
    """One completed daily candle in UTC order."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> DailyBar:
        required = ("date", "open", "high", "low", "close", "volume")
        missing = [key for key in required if key not in raw]
        if missing:
            raise DailyStrategyInputError(f"daily_bar_missing:{','.join(missing)}")
        try:
            bar = cls(
                date=str(raw["date"]),
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=float(raw["volume"]),
            )
        except (TypeError, ValueError) as exc:
            raise DailyStrategyInputError("daily_bar_numeric_invalid") from exc
        if not bar.date or any(not math.isfinite(value) for value in bar.numeric_values):
            raise DailyStrategyInputError("daily_bar_non_finite")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            raise DailyStrategyInputError("daily_bar_non_positive")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise DailyStrategyInputError("daily_bar_ohlc_inconsistent")
        return bar

    @property
    def numeric_values(self) -> tuple[float, ...]:
        return (self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True)
class DailyStrategyReceipt:
    strategy_version: str
    data_hash: str
    bar_count: int
    as_of: str
    parameters: Mapping[str, int]
    features: Mapping[str, float]
    outlook: str
    action_candidate: str
    trigger: str
    counter_case: str
    invalidation: str
    rationale: str
    evidence_refs: tuple[str, ...]
    probability: None = None
    probability_status: str = "unavailable"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "strategy-receipt.v1",
            "strategy_version": self.strategy_version,
            "data_hash": self.data_hash,
            "bar_count": self.bar_count,
            "as_of": self.as_of,
            "parameters": dict(self.parameters),
            "features": dict(self.features),
            "outlook": self.outlook,
            "action_candidate": self.action_candidate,
            "trigger": self.trigger,
            "counter_case": self.counter_case,
            "invalidation": self.invalidation,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "probability": self.probability,
            "probability_status": self.probability_status,
        }

    def as_json(self) -> str:
        """Return a canonical scalar payload suitable for Fact attributes."""

        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_daily_strategy(
    raw_bars: Sequence[Mapping[str, Any] | DailyBar],
    *,
    as_of: str | None = None,
    evidence_refs: Sequence[str] = (),
    short_window: int = 20,
    long_window: int = 60,
) -> DailyStrategyReceipt:
    """Compute the V0 daily trend outlook from completed candles.

    The exact parameters are deliberately explicit and versioned.  This is a
    baseline rule, not a calibrated probability model.  It reports a direction
    when the three trend conditions agree and otherwise reports a range.
    """

    if short_window < 2 or long_window <= short_window:
        raise DailyStrategyInputError("daily_strategy_window_invalid")
    bars = tuple(
        item if isinstance(item, DailyBar) else DailyBar.from_mapping(item) for item in raw_bars
    )
    if len(bars) < long_window:
        raise DailyStrategyInputError(f"daily_strategy_need_{long_window}_closed_bars")
    dates = [item.date for item in bars]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise DailyStrategyInputError("daily_strategy_dates_not_strictly_increasing")
    last = bars[-1]
    closes = [item.close for item in bars]
    volumes = [item.volume for item in bars]
    sma_short = fmean(closes[-short_window:])
    sma_long = fmean(closes[-long_window:])
    return_short = (last.close / closes[-short_window - 1] - 1.0) * 100.0
    recent_returns = {
        days: (last.close / closes[-days - 1] - 1.0) * 100.0 for days in (1, 2, 7)
    }
    returns = [
        (closes[index] / closes[index - 1] - 1.0)
        for index in range(len(closes) - short_window, len(closes))
    ]
    realized_vol = pstdev(returns) * math.sqrt(365.0) * 100.0
    volume_median = sorted(volumes[-short_window:])[short_window // 2]
    volume_ratio = last.volume / volume_median if volume_median else 0.0
    highest = max(item.high for item in bars[-short_window:])
    lowest = min(item.low for item in bars[-short_window:])

    above_short = last.close > sma_short
    short_above_long = sma_short > sma_long
    positive_return = return_short > 0
    below_short = last.close < sma_short
    short_below_long = sma_short < sma_long
    negative_return = return_short < 0
    if above_short and short_above_long and positive_return:
        outlook = "bullish"
        action = "已有现货：持有倾向；空仓：多头候选优先，入场风险需单独评估"
        trigger = f"当前偏多条件已成立；后续每个 UTC 日收盘复查 SMA{short_window} 与近期收益"
        counter_case = f"若跌破 SMA{long_window}，偏多趋势假设失效"
        invalidation = f"日线收盘跌破 SMA{long_window} ({sma_long:.2f})"
        rationale = (
            f"收盘 {last.close:.2f} 高于 SMA{short_window} {sma_short:.2f}，"
            f"SMA{short_window} 高于 SMA{long_window}，{short_window} 日收益为正。"
        )
    elif below_short and short_below_long and negative_return:
        outlook = "bearish"
        action = "已有现货：降低多头暴露倾向；空仓：不新增多头，不据此开杠杆空单"
        trigger = f"当前偏空条件已成立；后续每个 UTC 日收盘复查 SMA{short_window} 与近期收益"
        counter_case = f"若重新站上 SMA{long_window}，偏空趋势假设失效"
        invalidation = f"日线收盘重新站上 SMA{long_window} ({sma_long:.2f})"
        rationale = (
            f"收盘 {last.close:.2f} 低于 SMA{short_window} {sma_short:.2f}，"
            f"SMA{short_window} 低于 SMA{long_window}，{short_window} 日收益为负。"
        )
    else:
        outlook = "range"
        action = "当前趋势分歧，暂不作单边配置；已有仓位与空仓的操作不能用同一句买卖替代"
        trigger = "每个 UTC 日收盘重新判断近期动量和长趋势，不要求先突破区间才分析"
        counter_case = "趋势指标冲突不等于横盘已确认；近期反转可能早于长期均线交叉"
        invalidation = f"收盘有效突破 {highest:.2f} 或跌破 {lowest:.2f} 后重新计算"
        rationale = (
            f"收盘 {last.close:.2f}、均线排列和 {short_window} 日收益未形成同向趋势，"
            "当前属于趋势分歧，不能据此断言未来价格必在区间内。"
        )

    rationale += (
        f" 最近 1/2/7 日收益分别为 {recent_returns[1]:+.2f}% / "
        f"{recent_returns[2]:+.2f}% / {recent_returns[7]:+.2f}%。"
        "这是价格规则倾向，未融合宏观预期差、资金流或杠杆结构，不能称综合预测。"
    )

    canonical = [
        {"date": item.date, "open": item.open, "high": item.high, "low": item.low,
         "close": item.close, "volume": item.volume}
        for item in bars
    ]
    data_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    features = {
        "close": round(last.close, 10),
        "sma_short": round(sma_short, 10),
        "sma_long": round(sma_long, 10),
        "return_short_pct": round(return_short, 10),
        **{f"return_{days}d_pct": round(value, 10) for days, value in recent_returns.items()},
        "realized_vol_annualized_pct": round(realized_vol, 10),
        "volume_ratio": round(volume_ratio, 10),
        "rolling_high": round(highest, 10),
        "rolling_low": round(lowest, 10),
    }
    return DailyStrategyReceipt(
        strategy_version="daily-trend-v0.1",
        data_hash=data_hash,
        bar_count=len(bars),
        as_of=as_of or last.date,
        parameters={"short_window": short_window, "long_window": long_window},
        features=features,
        outlook=outlook,
        action_candidate=action,
        trigger=trigger,
        counter_case=counter_case,
        invalidation=invalidation,
        rationale=rationale,
        evidence_refs=tuple(dict.fromkeys(ref for ref in evidence_refs if ref)),
    )
