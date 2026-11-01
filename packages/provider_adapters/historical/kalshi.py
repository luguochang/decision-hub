from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


@dataclass(frozen=True)
class KalshiExpectationPoint:
    event_offset: str
    observed_at: datetime
    probability: str


@dataclass(frozen=True)
class KalshiExpectationWindow:
    event_ticker: str
    market_ticker: str
    market_title: str
    points: tuple[KalshiExpectationPoint, ...]
    source_urls: tuple[str, ...]
    source_hashes: tuple[str, ...]


async def _fetch_json(url: str) -> Mapping[str, object]:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("kalshi_output_invalid")
            return payload
        except (httpx.HTTPStatusError, httpx.RequestError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("kalshi_unreachable")  # pragma: no cover


class KalshiHistoricalExpectationLoader:
    """Select a liquid pre-event probability series from Kalshi archives."""

    source_id = "kalshi-exchange"
    offsets = {"baseline": timedelta(hours=-24), "t-5m": timedelta(minutes=-5)}

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        maximum_bar_lag: timedelta = timedelta(seconds=90),
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")
        if maximum_bar_lag < timedelta(0):
            raise ValueError("kalshi_maximum_bar_lag_invalid")
        self.maximum_bar_lag = maximum_bar_lag

    async def load(self, *, event_ticker: str, event_at: datetime) -> KalshiExpectationWindow:
        event = _aware(event_at)
        markets_url = self._url(
            "/historical/markets", {"event_ticker": event_ticker, "limit": 1000}
        )
        markets_payload = await self.fetcher(markets_url)
        raw_markets = markets_payload.get("markets")
        if not isinstance(raw_markets, list) or not raw_markets:
            raise ValueError("kalshi_event_markets_missing")

        start = int((event - timedelta(hours=54)).timestamp())
        end = int((event - timedelta(minutes=4)).timestamp())
        market_rows: list[tuple[str, str, str]] = []
        for market in raw_markets:
            if not isinstance(market, Mapping):
                continue
            ticker = market.get("ticker")
            title = market.get("title")
            if not isinstance(ticker, str) or not isinstance(title, str):
                continue
            candles_url = self._url(
                f"/historical/markets/{ticker}/candlesticks",
                {"start_ts": start, "end_ts": end, "period_interval": 1},
            )
            market_rows.append((ticker, title, candles_url))
        payloads = await asyncio.gather(
            *(self.fetcher(item[2]) for item in market_rows), return_exceptions=True
        )
        candidates: list[
            tuple[
                Decimal,
                str,
                str,
                str,
                Mapping[str, object],
                tuple[KalshiExpectationPoint, ...],
            ]
        ] = []
        for (ticker, title, candles_url), candles_payload in zip(
            market_rows, payloads, strict=True
        ):
            if isinstance(candles_payload, BaseException):
                continue
            points = _select_points(
                candles_payload,
                event,
                self.offsets,
                maximum_bar_lag=self.maximum_bar_lag,
            )
            if len(points) != len(self.offsets):
                continue
            score = _volume_score(candles_payload)
            candidates.append((score, ticker, title, candles_url, candles_payload, points))
        if not candidates:
            raise ValueError("kalshi_expectation_window_missing")
        _, ticker, title, candles_url, candles_payload, points = max(
            candidates, key=lambda item: (item[0], item[1])
        )
        return KalshiExpectationWindow(
            event_ticker=event_ticker,
            market_ticker=ticker,
            market_title=title,
            points=points,
            source_urls=(markets_url, candles_url),
            source_hashes=(
                _payload_hash(markets_payload),
                _payload_hash(candles_payload),
            ),
        )

    def _url(self, path: str, params: Mapping[str, object]) -> str:
        return f"{self.base_url}{path}?{urlencode(params)}"


def _select_points(
    payload: Mapping[str, object],
    event_at: datetime,
    offsets: Mapping[str, timedelta],
    *,
    maximum_bar_lag: timedelta,
) -> tuple[KalshiExpectationPoint, ...]:
    raw = payload.get("candlesticks")
    if not isinstance(raw, list):
        return ()
    candles: list[tuple[datetime, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        timestamp = item.get("end_period_ts")
        price = item.get("price")
        close = price.get("close") or price.get("previous") if isinstance(price, Mapping) else None
        try:
            observed = datetime.fromtimestamp(int(str(timestamp)), tz=UTC)
            probability = str(Decimal(str(close)))
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            continue
        candles.append((observed, probability))
    output: list[KalshiExpectationPoint] = []
    for offset, delta in offsets.items():
        target = event_at + delta
        eligible = [item for item in candles if item[0] <= target]
        if offset == "baseline" and not eligible:
            # Sparse contracts may not trade at the exact prior-day anchor.
            # Use the earliest observation that is still clearly pre-event;
            # the emitted timestamp remains the actual exchange timestamp.
            eligible = [item for item in candles if item[0] <= event_at - timedelta(minutes=10)]
        if not eligible:
            continue
        observed, probability = (
            min(eligible, key=lambda item: item[0])
            if offset == "baseline" and all(item[0] > target for item in eligible)
            else max(eligible, key=lambda item: item[0])
        )
        if offset != "baseline" and target - observed > maximum_bar_lag:
            continue
        output.append(
            KalshiExpectationPoint(
                event_offset=offset,
                observed_at=observed,
                probability=probability,
            )
        )
    return tuple(output)


def _volume_score(payload: Mapping[str, object]) -> Decimal:
    raw = payload.get("candlesticks")
    if not isinstance(raw, list):
        return Decimal(0)
    score = Decimal(0)
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            score += Decimal(str(item.get("volume", "0")))
        except InvalidOperation:
            continue
    return score


def _payload_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("kalshi_timestamp_must_be_aware")
    return value.astimezone(UTC)
