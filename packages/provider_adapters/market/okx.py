from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.kernel.decision_hub_kernel.ports.sources import MarketQuote, PriceWindow

Fetcher = Callable[[str], Awaitable[dict[str, object]]]


async def _fetch(url: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("market response must be an object")
        return payload


class OKXPublicMarketAdapter:
    source_id = "okx-public"

    def __init__(
        self, *, fetcher: Fetcher | None = None, base_url: str = "https://www.okx.com"
    ) -> None:
        self.fetcher = fetcher or _fetch
        self.base_url = base_url.rstrip("/")

    async def quote(self, instrument: str, *, observed_at: datetime | None = None) -> MarketQuote:
        payload = await self.fetcher(f"{self.base_url}/api/v5/market/ticker?instId={instrument}")
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError("market_unavailable")
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ValueError("market response has no ticker")
        row = data[0]
        bid = _float(row.get("bidPx"))
        ask = _float(row.get("askPx"))
        last = _float(row.get("last"))
        if bid is not None and ask is not None:
            quality, benchmark = "observed", "first_executable"
        elif last is not None:
            quality, benchmark = "estimated", "last"
        else:
            quality, benchmark = "unavailable", "none"
        now = datetime.now(UTC)
        return MarketQuote(
            instrument=instrument,
            observed_at=observed_at or now,
            received_at=now,
            bid=bid,
            ask=ask,
            last=last,
            volume=_float(row.get("vol24h")),
            source_id=self.source_id,
            quality_status=quality,
            benchmark=benchmark,
        )

    async def window(
        self, instrument: str, emitted_at: datetime, expires_at: datetime
    ) -> PriceWindow | None:
        """Build an estimated two-candle window from the public candles endpoint.

        Public OKX candles are not a guaranteed historical execution record, so the returned
        window is explicitly marked estimated and is never treated as a high-confidence fill.
        """
        entry_payload = await self.fetcher(
            f"{self.base_url}/api/v5/market/history-candles?instId={instrument}"
            f"&bar=1m&after={int(emitted_at.timestamp() * 1000)}&limit=1"
        )
        exit_payload = await self.fetcher(
            f"{self.base_url}/api/v5/market/history-candles?instId={instrument}"
            f"&bar=1m&after={int(expires_at.timestamp() * 1000)}&limit=1"
        )
        entry_data = entry_payload.get("data")
        exit_data = exit_payload.get("data")
        if not isinstance(entry_data, list) or not isinstance(exit_data, list):
            return None
        entry_row = entry_data[0] if entry_data else None
        exit_row = exit_data[0] if exit_data else None
        if not isinstance(entry_row, list) or not isinstance(exit_row, list):
            return None
        now = datetime.now(UTC)
        entry_price = _float(entry_row[4]) if len(entry_row) >= 5 else None
        exit_price = _float(exit_row[4]) if len(exit_row) >= 5 else None
        if entry_price is None or exit_price is None:
            return None
        return PriceWindow(
            entry=MarketQuote(
                instrument=instrument,
                observed_at=emitted_at,
                received_at=now,
                last=entry_price,
                source_id=self.source_id,
                quality_status="estimated",
                benchmark="vwap_1m",
            ),
            exit=MarketQuote(
                instrument=instrument,
                observed_at=expires_at,
                received_at=now,
                last=exit_price,
                source_id=self.source_id,
                quality_status="estimated",
                benchmark="vwap_1m",
            ),
            quality_status="estimated",
        )


def _float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
