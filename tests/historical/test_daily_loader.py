from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from packages.provider_adapters.historical.gate_daily import GateDailyDataLoader
from packages.provider_adapters.historical.loaders import OKXHistoricalDataLoader
from packages.provider_adapters.historical.research import _aggregate_hourly_to_daily


@pytest.mark.asyncio
async def test_okx_daily_loader_requests_completed_1d_candles() -> None:
    end = datetime(2026, 9, 18, tzinfo=UTC)
    start = end - timedelta(days=70)
    timestamps = [int((start + timedelta(days=index)).timestamp() * 1000) for index in range(70)]
    calls: list[str] = []

    async def fetcher(url: str) -> dict[str, object]:
        calls.append(url)
        return {
            "code": "0",
            "data": [
                [
                    str(timestamp),
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    "100",
                    "100",
                    "1",
                ]
                for timestamp in reversed(timestamps)
            ],
        }

    batch = await OKXHistoricalDataLoader(fetcher=fetcher, pause=lambda _: _no_wait()).load(
        "crypto.btc_usdt.ohlcv_1d", start, end
    )

    assert batch.frequency == "1d"
    assert len(batch.rows) == 70
    assert parse_qs(urlparse(calls[0]).query)["bar"] == ["1D"]


async def _no_wait() -> None:
    return None


def test_hourly_archive_fallback_aggregates_only_real_closed_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": (start + timedelta(hours=hour)).isoformat(),
            "open": str(100 + hour // 24),
            "high": str(101 + hour // 24),
            "low": str(99 + hour // 24),
            "close": str(100 + hour // 24),
            "volume_base": "2",
            "confirmed": True,
        }
        for hour in range(60 * 24)
        if hour // 24 != 17
    ]

    daily = _aggregate_hourly_to_daily(rows)

    assert len(daily) == 59
    assert daily[0]["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert daily[0]["open"] == "100"
    assert daily[0]["high"] == "101"
    assert daily[0]["low"] == "99"
    assert daily[0]["volume_base"] == "48"
    assert all(item["timestamp"] != "2026-01-18T00:00:00+00:00" for item in daily)


@pytest.mark.parametrize("hours", [list(range(15)), list(range(23)), [*range(23), 22]])
def test_partial_or_duplicate_hour_day_cannot_become_daily(hours: list[int]) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [{"timestamp": (start + timedelta(hours=h)).isoformat(), "open": "100",
             "high": "101", "low": "99", "close": "100", "volume_base": "1",
             "confirmed": True} for h in hours]
    assert _aggregate_hourly_to_daily(rows) == []


@pytest.mark.asyncio
async def test_gate_daily_filters_open_candle_and_preserves_volume_units() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async def fetcher(url: str) -> object:
        assert "interval=1d" in url and "limit=" not in url
        return [[str(int((start + timedelta(days=i)).timestamp())), "2000", "101", "102",
                 "99", "100", "20", closed] for i, closed in enumerate(["true", "false"])]
    batch = await GateDailyDataLoader(fetcher=fetcher).load(
        "crypto.btc_usdt.ohlcv_1d", start, start + timedelta(days=2)
    )
    assert batch.source_id == "gateio-public"
    assert len(batch.rows) == 1
    assert batch.rows[0]["close"] == "101"
    assert batch.rows[0]["volume_base"] == "20"
    assert batch.rows[0]["volume_quote"] == "2000"


def test_daily_fact_units_match_pack_semantics() -> None:
    from packages.provider_adapters.historical.research import _unit
    for field in ("return_20_pct", "realized_vol_20_pct"):
        assert _unit("crypto.btc_usdt.ohlcv_1d", field, "usdt_and_btc") == "percent"
    assert _unit("crypto.btc_usdt.ohlcv_1d", "volume_ratio_20", "usdt_and_btc") == "ratio"
