from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

BytesFetcher = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class BinanceVisionSource:
    source_url: str
    content_hash: str
    member_name: str


@dataclass(frozen=True)
class BinanceVisionEventPoint:
    event_offset: str
    observed_at: datetime
    values: Mapping[str, str]


@dataclass(frozen=True)
class BinanceVisionEventWindow:
    event_at: datetime
    points: tuple[BinanceVisionEventPoint, ...]
    sources: tuple[BinanceVisionSource, ...]


async def _fetch_bytes(url: str) -> bytes:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except (httpx.HTTPStatusError, httpx.RequestError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("binance_vision_unreachable")  # pragma: no cover


class BinanceVisionEventWindowLoader:
    """Read an immutable BTC event window from Binance's public archive.

    The daily/monthly files are exchange-owned historical source material.  The
    loader selects only observations available at the requested event offsets;
    it never substitutes a current quote or fills a missing field with zero.
    """

    source_id = "binance-vision"
    venue = "binance"
    instrument = "BTCUSDT"
    offsets = {"t-5m": timedelta(minutes=-5), "t+1m": timedelta(minutes=1)}

    def __init__(
        self,
        *,
        fetcher: BytesFetcher | None = None,
        base_url: str = "https://data.binance.vision/data",
    ) -> None:
        self.fetcher = fetcher or _fetch_bytes
        self.base_url = base_url.rstrip("/")

    async def load(self, event_at: datetime) -> BinanceVisionEventWindow:
        event = _aware(event_at)
        date = event.date().isoformat()
        month = event.strftime("%Y-%m")
        urls = {
            "spot": (f"{self.base_url}/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{date}.zip"),
            "metrics": (
                f"{self.base_url}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{date}.zip"
            ),
            "premium": (
                f"{self.base_url}/futures/um/daily/premiumIndexKlines/BTCUSDT/1m/"
                f"BTCUSDT-1m-{date}.zip"
            ),
            "funding": (
                f"{self.base_url}/futures/um/monthly/fundingRate/BTCUSDT/"
                f"BTCUSDT-fundingRate-{month}.zip"
            ),
        }
        payloads = await asyncio.gather(*(self.fetcher(url) for url in urls.values()))
        tables: dict[str, list[dict[str, str]]] = {}
        sources: list[BinanceVisionSource] = []
        for (kind, url), payload in zip(urls.items(), payloads, strict=True):
            member, rows = _read_csv_zip(payload, kind=kind)
            tables[kind] = rows
            sources.append(
                BinanceVisionSource(
                    source_url=url,
                    content_hash=hashlib.sha256(payload).hexdigest(),
                    member_name=member,
                )
            )

        points = tuple(
            self._point(offset, event + delta, tables) for offset, delta in self.offsets.items()
        )
        return BinanceVisionEventWindow(event_at=event, points=points, sources=tuple(sources))

    def _point(
        self,
        offset: str,
        target: datetime,
        tables: Mapping[str, list[dict[str, str]]],
    ) -> BinanceVisionEventPoint:
        spot = _latest_row(tables["spot"], target, "close_time", milliseconds=True)
        metrics = _latest_row(tables["metrics"], target, "create_time")
        premium = _latest_row(tables["premium"], target, "close_time", milliseconds=True)
        funding = _latest_row(tables["funding"], target, "calc_time", milliseconds=True)
        values = {
            "price": _required(spot, "close"),
            "volume": _required(spot, "quote_volume"),
            "funding_rate": _required(funding, "last_funding_rate"),
            "open_interest": _required(metrics, "sum_open_interest_value"),
            "basis": _required(premium, "close"),
            "crowding_signal": _required(metrics, "count_long_short_ratio"),
        }
        return BinanceVisionEventPoint(
            event_offset=offset,
            observed_at=target,
            values=values,
        )


_KLINE_FIELDS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def _read_csv_zip(payload: bytes, *, kind: str) -> tuple[str, list[dict[str, str]]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError("binance_vision_archive_member_invalid")
            text = archive.read(names[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError("binance_vision_archive_invalid") from exc
    first = text.partition("\n")[0]
    fields = _KLINE_FIELDS if kind in {"spot", "premium"} and first[:1].isdigit() else None
    rows = [dict(row) for row in csv.DictReader(io.StringIO(text), fieldnames=fields)]
    if not rows:
        raise ValueError("binance_vision_archive_empty")
    return names[0], rows


def _latest_row(
    rows: list[dict[str, str]],
    target: datetime,
    timestamp_field: str,
    *,
    milliseconds: bool = False,
) -> dict[str, str]:
    eligible: list[tuple[datetime, dict[str, str]]] = []
    for row in rows:
        raw = row.get(timestamp_field)
        if not raw:
            continue
        try:
            observed = _timestamp(raw, milliseconds=milliseconds)
        except (ValueError, OverflowError):
            continue
        if observed <= target:
            eligible.append((observed, row))
    if not eligible:
        raise ValueError(f"binance_vision_point_missing:{timestamp_field}")
    return max(eligible, key=lambda item: item[0])[1]


def _timestamp(value: str, *, milliseconds: bool) -> datetime:
    if milliseconds:
        raw = int(value)
        # Binance spot archives use microseconds for files from 2025 onward;
        # futures archives remain milliseconds.  Normalize by magnitude.
        if raw >= 100_000_000_000_000:
            raw //= 1000
        return datetime.fromtimestamp(raw / 1000, tz=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # Binance Vision's metrics CSV documents create_time as UTC but omits
        # the suffix.  Treating it as local time would move event windows.
        parsed = parsed.replace(tzinfo=UTC)
    return _aware(parsed)


def _required(row: Mapping[str, str], field: str) -> str:
    value = row.get(field)
    if value is None or not value.strip():
        raise ValueError(f"binance_vision_field_missing:{field}")
    return value.strip()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("binance_vision_timestamp_must_be_aware")
    return value.astimezone(UTC)
