from __future__ import annotations

import asyncio
from typing import cast

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
