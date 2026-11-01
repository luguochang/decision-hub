"""Gate BTC/ETH daily originals with an explicit pure closure clock."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from packages.kernel.decision_hub_kernel.decision.daily_strategy import DailyBar
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

from .loaders import HistoricalDatasetBatch


def normalize_gate_daily_payload(
    payload: object, *, start: datetime, end: datetime, closed_at: datetime
) -> list[dict[str, object]]:
    """Normalize the original response without network, storage or a clock read.

    Preserve the existing loader's exclusion of unconfirmed, out-of-range and
    not-yet-closed rows. The caller supplies the actual closure clock; a later
    reader must never substitute today's clock for the original selection.
    """
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, closed_at)):
        raise ValueError("gate_daily_clock_timezone_required")
    start, end, closed_at = (value.astimezone(UTC) for value in (start, end, closed_at))
    if not 0 < (end - start).days < 1000:
        raise ValueError("gate_daily_window_invalid")
    if not isinstance(payload, list):
        raise ValueError("gate_daily_payload_invalid")
    rows: dict[datetime, dict[str, object]] = {}
    for raw in payload:
        if not isinstance(raw, list) or len(raw) < 7:
            raise ValueError("gate_daily_row_invalid")
        timestamp = datetime.fromtimestamp(int(raw[0]), UTC)
        if len(raw) > 7 and str(raw[7]).lower() != "true":
            continue
        if timestamp < start or timestamp + timedelta(days=1) > min(end, closed_at):
            continue
        if timestamp.hour or timestamp.minute or timestamp.second:
            raise ValueError("gate_daily_not_utc_boundary")
        if timestamp in rows:
            raise ValueError("gate_daily_duplicate")
        DailyBar.from_mapping(
            {
                "date": timestamp.date().isoformat(),
                "open": raw[5],
                "high": raw[3],
                "low": raw[4],
                "close": raw[2],
                "volume": raw[6],
            }
        )
        rows[timestamp] = {
            "timestamp": timestamp.isoformat(),
            "open": str(raw[5]),
            "high": str(raw[3]),
            "low": str(raw[4]),
            "close": str(raw[2]),
            "volume_base": str(raw[6]),
            "volume_quote": str(raw[1]),
            "confirmed": True,
        }
    return [rows[timestamp] for timestamp in sorted(rows)]


async def _fetch(url: str) -> object:
    async with CapturingAsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class GateDailyDataLoader:
    def __init__(self, *, fetcher: Callable[[str], Awaitable[object]] = _fetch) -> None:
        self.fetcher = fetcher

    async def load(self, dataset_id: str, start: datetime, end: datetime) -> HistoricalDatasetBatch:
        instruments = {
            "crypto.btc_usdt.ohlcv_1d": ("BTC_USDT", "BTC-USDT", "usdt_and_btc"),
            "crypto.eth_usdt.ohlcv_1d": ("ETH_USDT", "ETH-USDT", "usdt_and_eth"),
        }
        if dataset_id not in instruments:
            raise ValueError("gate_daily_dataset_unsupported")
        pair, instrument, unit = instruments[dataset_id]
        if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).days < 1000:
            raise ValueError("gate_daily_window_invalid")
        payload = await self.fetcher(
            "https://api.gateio.ws/api/v4/spot/candlesticks?"
            + urlencode(
                {
                    "currency_pair": pair,
                    "interval": "1d",
                    "from": int(start.timestamp()),
                    "to": int(end.timestamp()) - 1,
                }
            )
        )
        rows = normalize_gate_daily_payload(
            payload, start=start, end=end, closed_at=datetime.now(UTC)
        )
        return HistoricalDatasetBatch(
            dataset_id=dataset_id,
            source_id="gateio-public",
            rows=rows,
            unit=unit,
            frequency="1d",
            instrument=instrument,
            venue="gateio",
            contract_type="spot",
            publication_time_precision="second",
            revision_policy="unknown",
            gaps=[],
        )
