from __future__ import annotations

import csv
import hashlib
import io
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class PortaraMacroPoint:
    event_offset: str
    observed_at: datetime
    level: str
    event_return: str


@dataclass(frozen=True)
class PortaraMacroSeries:
    metric_family: str
    instrument: str
    venue: str
    source_id: str
    source_url: str
    source_hash: str
    level_unit: str
    event_return_unit: str
    points: tuple[PortaraMacroPoint, ...]


@dataclass(frozen=True)
class PortaraMacroEventWindow:
    event_at: datetime
    source_timezone: str
    series: tuple[PortaraMacroSeries, ...]


@dataclass(frozen=True)
class PortaraRateExpectationPoint:
    event_offset: str
    observed_at: datetime
    level: str
    delta: str


@dataclass(frozen=True)
class PortaraRateExpectationWindow:
    event_at: datetime
    source_timezone: str
    instrument: str
    venue: str
    source_id: str
    source_url: str
    source_hash: str
    points: tuple[PortaraRateExpectationPoint, ...]


@dataclass(frozen=True)
class PortaraCrossAssetPoint:
    event_offset: str
    observed_at: datetime
    level: str
    event_return: str


@dataclass(frozen=True)
class PortaraCrossAssetEventWindow:
    event_at: datetime
    source_timezone: str
    metric_family: str
    instrument: str
    venue: str
    source_id: str
    source_url: str
    source_hash: str
    points: tuple[PortaraCrossAssetPoint, ...]


@dataclass(frozen=True)
class _Bar:
    observed_at: datetime
    close: Decimal


class PortaraMacroEventWindowLoader:
    """Build rates/USD event windows from licensed Portara ASCII files.

    Portara delivers USY02Y from Tullett Prebon Sovereign Debt and DX from
    ICE Futures US.  The delivery vendor is shared, but the market origins are
    kept as separate source and independence identifiers.  Files are read only
    from owner-configured paths; a model cannot choose or alter those paths.
    """

    offsets = {"t-5m": timedelta(minutes=-5), "t+1m": timedelta(minutes=1)}
    rate_source_url = "https://portaracqg.com/fixed-income/int/usy02y"
    usd_source_url = "https://portaracqg.com/futures/int/dxa"

    def __init__(
        self,
        *,
        us2y_path: Path,
        dxy_path: Path,
        source_timezone: str = "America/Chicago",
        maximum_bar_lag: timedelta = timedelta(seconds=90),
        maximum_baseline_lag: timedelta = timedelta(days=3),
    ) -> None:
        self.us2y_path = us2y_path
        self.dxy_path = dxy_path
        try:
            self.source_timezone = ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("portara_source_timezone_invalid") from exc
        if maximum_bar_lag < timedelta(0):
            raise ValueError("portara_maximum_bar_lag_invalid")
        if maximum_baseline_lag < timedelta(0):
            raise ValueError("portara_maximum_baseline_lag_invalid")
        self.maximum_bar_lag = maximum_bar_lag
        self.maximum_baseline_lag = maximum_baseline_lag
        self.source_timezone_name = source_timezone
        self._rate_bytes = _read_file(self.us2y_path)
        self._usd_bytes = _read_file(self.dxy_path)
        self._rate_bars = _parse_bars(self._rate_bytes, self.source_timezone)
        self._usd_bars = _parse_bars(self._usd_bytes, self.source_timezone)
        self._rate_hash = hashlib.sha256(self._rate_bytes).hexdigest()
        self._usd_hash = hashlib.sha256(self._usd_bytes).hexdigest()

    def load(self, event_at: datetime) -> PortaraMacroEventWindow:
        event = _aware(event_at)
        return PortaraMacroEventWindow(
            event_at=event,
            source_timezone=self.source_timezone_name,
            series=(
                self._series(
                    bars=self._rate_bars,
                    event_at=event,
                    metric_family="macro.rates",
                    instrument="US2Y",
                    venue="tullett-prebon",
                    source_id="tullett-prebon-usy02y-via-portara",
                    source_url=self.rate_source_url,
                    source_hash=self._rate_hash,
                    level_unit="yield_percent",
                    event_return_unit="bps",
                ),
                self._series(
                    bars=self._usd_bars,
                    event_at=event,
                    metric_family="macro.usd",
                    instrument="DXY",
                    venue="iceus",
                    source_id="iceus-dx-via-portara",
                    source_url=self.usd_source_url,
                    source_hash=self._usd_hash,
                    level_unit="index",
                    event_return_unit="percent",
                ),
            ),
        )

    def load_rate_expectation(self, event_at: datetime) -> PortaraRateExpectationWindow:
        """Build a pre-event US2Y repricing baseline when no prediction contract exists."""

        event = _aware(event_at)
        baseline = _bar_at_or_before(
            self._rate_bars,
            event - timedelta(hours=24),
            maximum_lag=self.maximum_baseline_lag,
        )
        latest = _bar_at_or_before(
            self._rate_bars,
            event - timedelta(minutes=5),
            maximum_lag=self.maximum_bar_lag,
        )
        delta = (latest.close - baseline.close) * Decimal(100)
        return PortaraRateExpectationWindow(
            event_at=event,
            source_timezone=self.source_timezone_name,
            instrument="US2Y",
            venue="tullett-prebon",
            source_id="tullett-prebon-usy02y-via-portara",
            source_url=self.rate_source_url,
            source_hash=self._rate_hash,
            points=(
                PortaraRateExpectationPoint(
                    event_offset="baseline",
                    observed_at=baseline.observed_at,
                    level=_decimal_text(baseline.close),
                    delta="0",
                ),
                PortaraRateExpectationPoint(
                    event_offset="t-5m",
                    observed_at=latest.observed_at,
                    level=_decimal_text(latest.close),
                    delta=_calculated_text(delta),
                ),
            ),
        )

    def coverage(self, event_at: datetime) -> dict[str, object]:
        """Describe every requested slot without substituting a nearby value."""

        event = _aware(event_at)
        series_specs = (
            ("macro.rates", "US2Y", self._rate_bars, self._rate_hash),
            ("macro.usd", "DXY", self._usd_bars, self._usd_hash),
        )
        series: list[dict[str, object]] = []
        for metric_family, instrument, bars, source_hash in series_specs:
            slots: list[dict[str, object]] = []
            for event_offset, delta in self.offsets.items():
                target = event + delta
                slots.append(
                    _coverage_slot(
                        bars,
                        event_offset=event_offset,
                        target=target,
                        maximum_lag=self.maximum_bar_lag,
                    )
                )
            series.append(
                {
                    "metric_family": metric_family,
                    "instrument": instrument,
                    "source_hash": source_hash,
                    "slots": slots,
                }
            )
        expectation_slots = [
            _coverage_slot(
                self._rate_bars,
                event_offset="baseline",
                target=event - timedelta(hours=24),
                maximum_lag=self.maximum_baseline_lag,
            ),
            _coverage_slot(
                self._rate_bars,
                event_offset="t-5m",
                target=event - timedelta(minutes=5),
                maximum_lag=self.maximum_bar_lag,
            ),
        ]
        return {
            "event_at": event.isoformat(),
            "series": series,
            "expectation_pricing": {
                "instrument": "US2Y",
                "source_hash": self._rate_hash,
                "slots": expectation_slots,
            },
        }

    def _series(
        self,
        *,
        bars: tuple[_Bar, ...],
        event_at: datetime,
        metric_family: str,
        instrument: str,
        venue: str,
        source_id: str,
        source_url: str,
        source_hash: str,
        level_unit: str,
        event_return_unit: str,
    ) -> PortaraMacroSeries:
        selected = {
            offset: _bar_at_or_before(
                bars,
                event_at + delta,
                maximum_lag=self.maximum_bar_lag,
            )
            for offset, delta in self.offsets.items()
        }
        baseline = selected["t-5m"].close
        post = selected["t+1m"].close
        delta = (
            (post - baseline) * Decimal(100)
            if metric_family == "macro.rates"
            else (post / baseline - Decimal(1)) * Decimal(100)
        )
        points = tuple(
            PortaraMacroPoint(
                event_offset=offset,
                observed_at=selected[offset].observed_at,
                level=_decimal_text(selected[offset].close),
                event_return="0" if offset == "t-5m" else _calculated_text(delta),
            )
            for offset in self.offsets
        )
        return PortaraMacroSeries(
            metric_family=metric_family,
            instrument=instrument,
            venue=venue,
            source_id=source_id,
            source_url=source_url,
            source_hash=source_hash,
            level_unit=level_unit,
            event_return_unit=event_return_unit,
            points=points,
        )


class PortaraCrossAssetEventWindowLoader:
    """Build an independent ES risk-asset confirmation from licensed minute bars."""

    offsets = {"t-5m": timedelta(minutes=-5), "t+1m": timedelta(minutes=1)}
    source_url = "https://portaracqg.com/futures/int/ep"

    def __init__(
        self,
        *,
        es_path: Path,
        source_timezone: str = "America/Chicago",
        maximum_bar_lag: timedelta = timedelta(seconds=90),
    ) -> None:
        self.es_path = es_path
        try:
            self.source_timezone = ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("portara_source_timezone_invalid") from exc
        if maximum_bar_lag < timedelta(0):
            raise ValueError("portara_maximum_bar_lag_invalid")
        self.maximum_bar_lag = maximum_bar_lag
        self.source_timezone_name = source_timezone
        self._source_bytes = _read_file(es_path)
        self._bars = _parse_bars(self._source_bytes, self.source_timezone)
        self._source_hash = hashlib.sha256(self._source_bytes).hexdigest()

    def load(self, event_at: datetime) -> PortaraCrossAssetEventWindow:
        event = _aware(event_at)
        selected = {
            offset: _bar_at_or_before(
                self._bars,
                event + delta,
                maximum_lag=self.maximum_bar_lag,
            )
            for offset, delta in self.offsets.items()
        }
        baseline = selected["t-5m"].close
        event_return = (selected["t+1m"].close / baseline - Decimal(1)) * Decimal(100)
        return PortaraCrossAssetEventWindow(
            event_at=event,
            source_timezone=self.source_timezone_name,
            metric_family="cross_asset.equity",
            instrument="ES",
            venue="cme",
            source_id="cme-emini-sp500-es-via-portara",
            source_url=self.source_url,
            source_hash=self._source_hash,
            points=tuple(
                PortaraCrossAssetPoint(
                    event_offset=offset,
                    observed_at=selected[offset].observed_at,
                    level=_decimal_text(selected[offset].close),
                    event_return=("0" if offset == "t-5m" else _calculated_text(event_return)),
                )
                for offset in self.offsets
            ),
        )

    def coverage(self, event_at: datetime) -> dict[str, object]:
        event = _aware(event_at)
        return {
            "event_at": event.isoformat(),
            "instrument": "ES",
            "source_hash": self._source_hash,
            "slots": [
                _coverage_slot(
                    self._bars,
                    event_offset=event_offset,
                    target=event + delta,
                    maximum_lag=self.maximum_bar_lag,
                )
                for event_offset, delta in self.offsets.items()
            ],
        }


def _read_file(path: Path) -> bytes:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("portara_source_file_unavailable") from exc
    if not payload:
        raise ValueError("portara_source_file_empty")
    return payload


def _parse_bars(payload: bytes, timezone: ZoneInfo) -> tuple[_Bar, ...]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("portara_source_encoding_invalid") from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {"Date", "Time", "Close"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise ValueError("portara_source_columns_invalid")
    by_timestamp: dict[datetime, _Bar] = {}
    for row in reader:
        try:
            date = str(row["Date"]).strip()
            time = str(row["Time"]).strip().zfill(4)
            pattern = "%Y%m%d%H%M%S" if len(time) == 6 else "%Y%m%d%H%M"
            local = datetime.strptime(f"{date}{time}", pattern).replace(tzinfo=timezone)
            observed_at = local.astimezone(UTC)
            close = Decimal(str(row["Close"]).strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("portara_source_row_invalid") from exc
        if not close.is_finite() or close <= 0:
            raise ValueError("portara_source_price_invalid")
        if observed_at in by_timestamp:
            raise ValueError("portara_source_timestamp_duplicate")
        by_timestamp[observed_at] = _Bar(observed_at=observed_at, close=close)
    if not by_timestamp:
        raise ValueError("portara_source_rows_missing")
    return tuple(by_timestamp[key] for key in sorted(by_timestamp))


def _bar_at_or_before(bars: tuple[_Bar, ...], target: datetime, *, maximum_lag: timedelta) -> _Bar:
    index = bisect_right(bars, target, key=lambda bar: bar.observed_at) - 1
    if index < 0:
        raise ValueError("portara_event_window_missing")
    selected = bars[index]
    if target - selected.observed_at > maximum_lag:
        raise ValueError("portara_event_window_stale")
    return selected


def _coverage_slot(
    bars: tuple[_Bar, ...],
    *,
    event_offset: str,
    target: datetime,
    maximum_lag: timedelta,
) -> dict[str, object]:
    try:
        selected = _bar_at_or_before(bars, target, maximum_lag=maximum_lag)
    except ValueError as exc:
        return {
            "event_offset": event_offset,
            "target_at": target.isoformat(),
            "status": "gap",
            "reason": str(exc),
        }
    return {
        "event_offset": event_offset,
        "target_at": target.isoformat(),
        "status": "available",
        "observed_at": selected.observed_at.isoformat(),
        "lag_seconds": int((target - selected.observed_at).total_seconds()),
    }


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _calculated_text(value: Decimal) -> str:
    return _decimal_text(value.quantize(Decimal("0.00000001")))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("portara_event_timestamp_must_be_aware")
    return value.astimezone(UTC)
