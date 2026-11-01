from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode

import httpx

from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]
TextFetcher = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class HistoricalDatasetBatch:
    dataset_id: str
    source_id: str
    rows: list[dict[str, object]]
    unit: str
    frequency: str
    instrument: str
    venue: str | None
    contract_type: str | None
    publication_time_precision: Literal["millisecond", "second", "date", "unknown"]
    revision_policy: Literal["immutable", "revisable", "unknown"]
    gaps: list[str]


async def _fetch_json(url: str) -> Mapping[str, object]:
    payload: object = None
    for attempt in range(3):
        try:
            async with CapturingAsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            break
        except (httpx.HTTPStatusError, httpx.RequestError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    if not isinstance(payload, dict):
        raise ValueError("historical_provider_output_invalid")
    return payload


async def _fetch_text(url: str) -> str:
    for attempt in range(3):
        try:
            async with CapturingAsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
        except (httpx.HTTPStatusError, httpx.RequestError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("historical_provider_unreachable")  # pragma: no cover


class OKXHistoricalDataLoader:
    supported_dataset_ids = frozenset(
        {
            "crypto.btc_usdt.ohlcv_1h",
            "crypto.btc_usdt.ohlcv_1d",
            "crypto.btc_usdt_swap.funding_rate",
            "crypto.btc_usdt_swap.open_interest",
        }
    )

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://www.okx.com",
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")
        self.pause = pause

    async def load(
        self, dataset_id: str, start: datetime, end: datetime
    ) -> HistoricalDatasetBatch:
        if dataset_id not in self.supported_dataset_ids:
            raise ValueError("historical_dataset_unsupported")
        if dataset_id == "crypto.btc_usdt_swap.open_interest":
            # OKX's public API exposes a current OI snapshot but no historical
            # OI series. Recording zero history is more honest than presenting
            # the latest point as three-month coverage.
            return HistoricalDatasetBatch(
                dataset_id=dataset_id,
                source_id="okx-public",
                rows=[],
                unit="btc",
                frequency="snapshot_only",
                instrument="BTC-USDT-SWAP",
                venue="okx",
                contract_type="linear_perpetual",
                publication_time_precision="millisecond",
                revision_policy="immutable",
                gaps=["okx_public_open_interest_has_no_historical_endpoint"],
            )
        if dataset_id in {"crypto.btc_usdt.ohlcv_1h", "crypto.btc_usdt.ohlcv_1d"}:
            bar = "1D" if dataset_id.endswith("ohlcv_1d") else "1H"
            rows = await self._candles(start, end, bar=bar)
            return HistoricalDatasetBatch(
                dataset_id=dataset_id,
                source_id="okx-public",
                rows=rows,
                unit="usdt_and_btc",
                frequency="1d" if bar == "1D" else "1h",
                instrument="BTC-USDT",
                venue="okx",
                contract_type="spot",
                publication_time_precision="millisecond",
                revision_policy="immutable",
                gaps=[],
            )
        rows = await self._funding(start, end)
        return HistoricalDatasetBatch(
            dataset_id=dataset_id,
            source_id="okx-public",
            rows=rows,
            unit="rate",
            frequency="scheduled_8h_variable",
            instrument="BTC-USDT-SWAP",
            venue="okx",
            contract_type="linear_perpetual",
            publication_time_precision="millisecond",
            revision_policy="immutable",
            gaps=[],
        )

    async def _candles(
        self, start: datetime, end: datetime, *, bar: str = "1H"
    ) -> list[dict[str, object]]:
        cursor = int(end.timestamp() * 1000)
        earliest = int(start.timestamp() * 1000)
        rows: dict[int, dict[str, object]] = {}
        for page in range(20):
            url = f"{self.base_url}/api/v5/market/history-candles?" + urlencode(
                {"instId": "BTC-USDT", "bar": bar, "after": cursor, "limit": 300}
            )
            payload = await self.fetcher(url)
            data = _okx_data(payload)
            if not data:
                break
            page_timestamps: list[int] = []
            for raw in data:
                if not isinstance(raw, list) or len(raw) < 9:
                    continue
                timestamp = _int(raw[0])
                if timestamp is None:
                    continue
                page_timestamps.append(timestamp)
                if earliest <= timestamp < int(end.timestamp() * 1000):
                    rows[timestamp] = {
                        "timestamp": _iso_ms(timestamp),
                        "open": str(raw[1]),
                        "high": str(raw[2]),
                        "low": str(raw[3]),
                        "close": str(raw[4]),
                        "volume_base": str(raw[5]),
                        "volume_currency": str(raw[6]),
                        "volume_quote": str(raw[7]),
                        "confirmed": str(raw[8]) == "1",
                    }
            if not page_timestamps or min(page_timestamps) <= earliest:
                break
            next_cursor = min(page_timestamps)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
            if page < 19:
                await self.pause(0.11)
        return [rows[key] for key in sorted(rows)]

    async def _funding(self, start: datetime, end: datetime) -> list[dict[str, object]]:
        cursor = int(end.timestamp() * 1000)
        earliest = int(start.timestamp() * 1000)
        rows: dict[int, dict[str, object]] = {}
        for page in range(10):
            url = f"{self.base_url}/api/v5/public/funding-rate-history?" + urlencode(
                {"instId": "BTC-USDT-SWAP", "after": cursor, "limit": 100}
            )
            payload = await self.fetcher(url)
            data = _okx_data(payload)
            if not data:
                break
            page_timestamps: list[int] = []
            for raw in data:
                if not isinstance(raw, dict):
                    continue
                timestamp = _int(raw.get("fundingTime"))
                if timestamp is None:
                    continue
                page_timestamps.append(timestamp)
                if earliest <= timestamp < int(end.timestamp() * 1000):
                    rows[timestamp] = {
                        "timestamp": _iso_ms(timestamp),
                        "funding_rate": str(raw.get("fundingRate", "")),
                        "realized_rate": str(raw.get("realizedRate", "")),
                        "method": str(raw.get("method", "")),
                    }
            if not page_timestamps or min(page_timestamps) <= earliest:
                break
            next_cursor = min(page_timestamps)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
            if page < 9:
                await self.pause(0.11)
        return [rows[key] for key in sorted(rows)]


class FredHistoricalDataLoader:
    _series = {
        "macro.fred.dgs2": ("DGS2", "yield_percent"),
        "macro.fred.dgs10": ("DGS10", "yield_percent"),
        "macro.fred.dtwexbgs": ("DTWEXBGS", "index"),
    }
    supported_dataset_ids = frozenset(_series)

    def __init__(
        self,
        *,
        fetcher: TextFetcher | None = None,
        base_url: str = "https://fred.stlouisfed.org",
    ) -> None:
        self.fetcher = fetcher or _fetch_text
        self.base_url = base_url.rstrip("/")

    async def load(
        self, dataset_id: str, start: datetime, end: datetime
    ) -> HistoricalDatasetBatch:
        if dataset_id not in self._series:
            raise ValueError("historical_dataset_unsupported")
        series, unit = self._series[dataset_id]
        url = f"{self.base_url}/graph/fredgraph.csv?" + urlencode(
            {
                "id": series,
                "cosd": start.date().isoformat(),
                "coed": end.date().isoformat(),
            }
        )
        text = await self.fetcher(url)
        rows: list[dict[str, object]] = []
        for raw in csv.DictReader(io.StringIO(text)):
            date_text = raw.get("DATE") or raw.get("observation_date")
            value = raw.get(series)
            if not date_text or not value or value == ".":
                continue
            try:
                timestamp = datetime.fromisoformat(date_text).replace(tzinfo=UTC)
            except ValueError:
                continue
            if start <= timestamp < end:
                rows.append({"timestamp": timestamp.isoformat(), "value": value})
        return HistoricalDatasetBatch(
            dataset_id=dataset_id,
            source_id="fred-public",
            rows=rows,
            unit=unit,
            frequency="native_business_day",
            instrument=series,
            venue=None,
            contract_type=None,
            publication_time_precision="date",
            revision_policy="revisable",
            gaps=[],
        )


def _okx_data(payload: Mapping[str, object]) -> list[object]:
    if str(payload.get("code", "0")) != "0":
        raise ValueError("historical_provider_unsuccessful")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("historical_provider_output_invalid")
    return data


def _int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
