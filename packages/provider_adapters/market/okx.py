from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.kernel.decision_hub_kernel.ports.sources import MarketQuote, PriceWindow
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

Fetcher = Callable[[str], Awaitable[dict[str, object]]]


async def _fetch(url: str) -> dict[str, object]:
    async with CapturingAsyncClient(timeout=10) as client:
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
        # A requested sample slot must never replace the provider's actual clock.
        ticker_ms = _float(row.get("ts"))
        ticker_at = datetime.fromtimestamp(ticker_ms / 1000, UTC) if ticker_ms else now
        return MarketQuote(
            instrument=instrument,
            observed_at=ticker_at,
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
        emitted_at = emitted_at.replace(tzinfo=UTC) if emitted_at.tzinfo is None else emitted_at
        expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
        if expires_at <= emitted_at:
            return None
        entry_payload = await self.fetcher(
            f"{self.base_url}/api/v5/market/history-candles?instId={instrument}"
            f"&bar=1m&after={int(emitted_at.timestamp() * 1000)}&limit=1"
        )
        exit_payload = await self.fetcher(
            f"{self.base_url}/api/v5/market/history-candles?instId={instrument}"
            f"&bar=1m&after={int(expires_at.timestamp() * 1000)}&limit=1"
        )
        now = datetime.now(UTC)
        entry = _closed_candle(entry_payload, instrument, emitted_at, now)
        exit_quote = _closed_candle(exit_payload, instrument, expires_at, now)
        if entry is None or exit_quote is None:
            return None
        return PriceWindow(
            entry=entry,
            exit=exit_quote,
            quality_status="estimated",
        )


def _closed_candle(payload, instrument, boundary, now) -> MarketQuote | None:
    if str(payload.get("code", "0")) != "0":
        return None
    rows = payload.get("data")
    row = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(row, list) or len(row) != 9 or str(row[8]) != "1":
        return None
    price, opened_ms = _float(row[4]), _float(row[0])
    if price is None or price <= 0 or opened_ms is None:
        return None
    try:
        closed_at = datetime.fromtimestamp(opened_ms / 1000, UTC) + timedelta(minutes=1)
    except (ValueError, OSError, OverflowError):
        return None
    # OKX `after` returns an older opening timestamp. The completed close
    # must be the first minute boundary at/after the requested instant.
    if not boundary <= closed_at < boundary + timedelta(minutes=1) or closed_at > now:
        return None
    return MarketQuote(
        instrument=instrument,
        observed_at=closed_at,
        received_at=now,
        last=price,
        source_id="okx-public",
        quality_status="estimated",
        benchmark="close_1m",
    )


def _float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        number = float(str(value))
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None
