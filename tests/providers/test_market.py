from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from packages.provider_adapters.market.okx import OKXPublicMarketAdapter


def test_okx_adapter_uses_executable_quote_and_last_price_fallback() -> None:
    responses = [
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "bidPx": "100",
                    "askPx": "101",
                    "last": "100.5",
                    "vol24h": "2",
                }
            ],
        },
        {
            "code": "0",
            "data": [
                {"instId": "BTC-USDT-SWAP", "bidPx": "", "askPx": "", "last": "99", "vol24h": "1"}
            ],
        },
    ]

    async def fetcher(_url: str) -> dict[str, object]:
        return cast(dict[str, object], responses.pop(0))

    adapter = OKXPublicMarketAdapter(fetcher=fetcher)
    first = asyncio.run(adapter.quote("BTC-USDT-SWAP"))
    second = asyncio.run(adapter.quote("BTC-USDT-SWAP"))

    assert first.quality_status == "observed"
    assert first.benchmark == "first_executable"
    assert first.bid == 100
    assert second.quality_status == "estimated"
    assert second.benchmark == "last"


@pytest.mark.parametrize("issue", [None, "unconfirmed", "stale", "nan", "error", "future"])
async def test_historical_window_uses_actual_confirmed_minute_close(issue):
    start = datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC)
    if issue == "future":
        start = datetime.now(UTC) + timedelta(days=1)

    async def fetch(url):
        stamp = int(parse_qs(urlsplit(url).query)["after"][0])
        opened = stamp // 60000 * 60000
        if issue == "stale":
            opened -= 60000
        return {
            "code": "500" if issue == "error" else "0",
            "data": [
                [
                    str(opened),
                    "1",
                    "1",
                    "1",
                    "NaN" if issue == "nan" else "100",
                    "1",
                    "1",
                    "1",
                    "0" if issue == "unconfirmed" else "1",
                ]
            ],
        }

    result = await OKXPublicMarketAdapter(fetcher=fetch).window(
        "BTC-USDT", start, start + timedelta(hours=1)
    )
    if issue:
        assert result is None
    else:
        assert result.entry.observed_at == start + timedelta(seconds=40)
        assert result.entry.observed_at != start
        assert result.entry.benchmark == "close_1m"
        assert result.quality_status == "estimated"


async def test_quote_does_not_relabel_requested_slot_as_actual_provider_time():
    actual = datetime(2026, 1, 1, tzinfo=UTC)

    async def fetch(url):
        return {"data": [{"ts": str(int(actual.timestamp() * 1000)), "last": "1"}]}

    quote = await OKXPublicMarketAdapter(fetcher=fetch).quote(
        "BTC-USDT", observed_at=actual - timedelta(days=1)
    )
    assert quote.observed_at == actual
