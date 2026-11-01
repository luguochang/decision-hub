import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import (
    CapturedMarketPage,
    MarketPathError,
    OKXMarketPathAdapter,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = spot_scope("BTC-USDT")


def row(index):
    return [
        str(int((START + timedelta(minutes=index)).timestamp() * 1000)),
        "100",
        "102",
        "99",
        "101",
        "1",
        "100",
        "100",
        "1",
    ]


def captured(rows, code="0"):
    return CapturedMarketPage(
        json.dumps({"code": code, "data": rows}).encode(),
        START + timedelta(days=2),
        "fixture-original",
    )


def test_complete_24h_pagination_uses_older_cursor_and_preserves_originals():
    requests = []

    async def fetch(url):
        query = parse_qs(urlparse(url).query)
        requests.append(int(query["after"][0]))
        earlier = [row(i) for i in range(1440) if int(row(i)[0]) < requests[-1]]
        return captured(list(reversed(earlier))[:300])

    path = asyncio.run(
        OKXMarketPathAdapter(fetcher=fetch).path(
            scope=SCOPE, start_at=START, end_at=START + timedelta(days=1)
        )
    )
    assert path.status == "complete" and len(path.candles) == 1440
    assert path.page_count == path.request_count == 5
    assert all(left > right for left, right in zip(requests, requests[1:], strict=False))
    assert path.candles[0].open_at == START
    assert path.candles[-1].close_at == START + timedelta(days=1)
    assert all(item.capture_hash and item.capture_ref for item in path.candles)


def test_provider_repeated_page_fails_instead_of_infinite_loop():
    calls = []

    async def fetch(url):
        calls.append(url)
        return captured([row(i) for i in reversed(range(300, 600))])

    with pytest.raises(MarketPathError, match="cursor_not_progressing"):
        asyncio.run(
            OKXMarketPathAdapter(fetcher=fetch).path(
                scope=SCOPE, start_at=START, end_at=START + timedelta(minutes=600)
            )
        )
    assert len(calls) == 2


def test_missing_bar_returns_partial_and_does_not_interpolate():
    async def fetch(url):
        return captured([row(2), row(0)])

    path = asyncio.run(
        OKXMarketPathAdapter(fetcher=fetch).path(
            scope=SCOPE, start_at=START, end_at=START + timedelta(minutes=3)
        )
    )
    assert path.status == "partial" and len(path.candles) == 2
    assert path.missing_open_at == (START + timedelta(minutes=1),)


@pytest.mark.parametrize(
    "rows,code,error",
    [
        ([row(0)[:-1] + ["0"]], "0", "bar_unclosed"),
        ([row(0)], "50011", "provider_code_50011"),
        ([], "0", "page_empty"),
    ],
)
def test_non_success_is_explicit_and_not_a_complete_path(rows, code, error):
    async def fetch(url):
        return captured(rows, code)

    with pytest.raises(MarketPathError, match=error):
        asyncio.run(
            OKXMarketPathAdapter(fetcher=fetch).path(
                scope=SCOPE, start_at=START, end_at=START + timedelta(minutes=1)
            )
        )
