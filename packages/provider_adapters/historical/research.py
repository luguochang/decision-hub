from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    DatasetCoverage,
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
    LoadedDailyImplementation,
    finish_daily_computation,
    prepare_daily_computation_in_session,
)
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, as_utc, utcnow
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE

from .archive import HistoricalDataArchive
from .gate_daily import normalize_gate_daily_payload

_FIELDS = {
    "crypto.eth_usdt.ohlcv_1d": frozenset(
        {"period_close", "sma_20", "sma_60", "return_20_pct", "realized_vol_20_pct",
         "volume_ratio_20", "rolling_high_20", "rolling_low_20", "row_count"}
    ),
    "crypto.btc_usdt.ohlcv_1h": frozenset(
        {"period_open", "period_close", "return_pct", "volume_sum", "row_count"}
    ),
    "crypto.btc_usdt.ohlcv_1d": frozenset(
        {
            "period_close",
            "sma_20",
            "sma_60",
            "return_20_pct",
            "realized_vol_20_pct",
            "volume_ratio_20",
            "rolling_high_20",
            "rolling_low_20",
            "row_count",
        }
    ),
    "crypto.btc_usdt_swap.funding_rate": frozenset(
        {
            "mean_funding_rate",
            "latest_funding_rate",
            "min_funding_rate",
            "max_funding_rate",
            "row_count",
        }
    ),
    "crypto.btc_usdt_swap.open_interest": frozenset(
        {"period_start_value", "period_end_value", "change", "row_count"}
    ),
    "macro.fred.dgs2": frozenset({"period_start_value", "period_end_value", "change", "row_count"}),
    "macro.fred.dgs10": frozenset(
        {"period_start_value", "period_end_value", "change", "row_count"}
    ),
    "macro.fred.dtwexbgs": frozenset(
        {"period_start_value", "period_end_value", "change", "row_count"}
    ),
}


class HistoricalDataResearchAdapter:
    """Read immutable historical partitions through the canonical Gateway."""

    capability_id = "data.historical"
    supported_modes = frozenset({"live", "replay"})
    supported_symbols = frozenset(_FIELDS)
    symbol_fields = _FIELDS
    symbols_required = True
    max_symbols = 1
    usage_notes = (
        "symbols must contain one dataset id; require explicit "
        "window_start_at/window_end_at; fields must match that dataset. Catalog "
        "membership does not prove stored coverage."
    )

    def __init__(
        self,
        jobs: DataJobService,
        archive: HistoricalDataArchive,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.jobs = jobs
        self.archive = archive
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if len(query.symbols) != 1 or query.symbols[0] not in _FIELDS:
            raise ResearchCapabilityError(
                "historical_dataset_required",
                "historical capability requires one approved dataset id",
                retryable=False,
                origin="gateway",
            )
        if query.window_start_at is None or query.window_end_at is None:
            raise ResearchCapabilityError(
                "historical_window_required",
                "historical capability requires an explicit window",
                retryable=False,
                origin="gateway",
            )
        dataset_id = query.symbols[0]
        requested_fields = set(query.fields) or set(_FIELDS[dataset_id])
        if not requested_fields <= _FIELDS[dataset_id]:
            raise ResearchCapabilityError(
                "historical_field_invalid",
                "historical summary field is unsupported for this dataset",
                retryable=False,
                origin="gateway",
            )
        located = self._locate(dataset_id, query.window_start_at, query.window_end_at)
        derived_from_hourly = False
        if located is None and dataset_id == "crypto.btc_usdt.ohlcv_1d":
            located = self._locate(
                "crypto.btc_usdt.ohlcv_1h",
                query.window_start_at,
                query.window_end_at,
                allow_stale_days=3,
            )
            derived_from_hourly = located is not None
        if located is None:
            raise ResearchCapabilityError(
                "historical_dataset_coverage_missing",
                "no completed DataJob covers the requested dataset and window",
                retryable=False,
                origin="storage",
            )
        manifest_ref, coverage = located
        if dataset_id in {"crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"}:
            expected_instrument = (
                "ETH-USDT" if dataset_id == "crypto.eth_usdt.ohlcv_1d" else "BTC-USDT"
            )
            if coverage.instrument != expected_instrument:
                raise ResearchCapabilityError(
                    "historical_instrument_mismatch",
                    "stored daily coverage does not match the requested asset",
                    retryable=False, origin="storage",
                )
        if coverage.status == "missing" or not coverage.partition_refs:
            raise ResearchCapabilityError(
                "historical_dataset_coverage_missing",
                ";".join(coverage.gaps) or "historical dataset has no retained rows",
                retryable=False,
                origin="storage",
            )
        self._bind_archived_captures(manifest_ref, coverage, query)
        rows = self._rows(coverage, query.window_start_at, query.window_end_at)
        if derived_from_hourly:
            rows = _aggregate_hourly_to_daily(rows)
        if not rows:
            raise ResearchCapabilityError(
                "historical_window_empty",
                "retained partitions have no rows inside the requested window",
                retryable=False,
                origin="storage",
            )
        prepared_daily = None
        capture_batch = CURRENT_CAPTURE.get()
        if (dataset_id in {"crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"}
                and coverage.source_id == "gateio-public" and not derived_from_hourly
                and capture_batch is not None and coverage.raw_capture_ids):
            # Producer reads before Gateway opens its semantic writer.
            with self.jobs.database.session() as session:
                prepared_daily = prepare_daily_computation_in_session(
                    session, query=query, capture_ids=coverage.raw_capture_ids,
                    archive=capture_batch.archive, retained_rows=rows,
                    implementation=loaded_daily_implementation(),
                )
        if prepared_daily is None:
            summary = _summary(dataset_id, rows)
        else:
            rows, summary = prepared_daily.rows, prepared_daily.summary
        selected = {field: summary[field] for field in sorted(requested_fields)}
        now = self.clock()
        excerpt = json.dumps(
            {
                "dataset_id": dataset_id,
                "coverage_status": coverage.status,
                "requested_window": [
                    query.window_start_at.astimezone(UTC).isoformat(),
                    query.window_end_at.astimezone(UTC).isoformat(),
                ],
                "actual_coverage": [
                    coverage.actual_start_at.isoformat() if coverage.actual_start_at else None,
                    coverage.actual_end_at.isoformat() if coverage.actual_end_at else None,
                ],
                "summary": selected,
                "gaps": coverage.gaps,
                "derived_from_hourly": derived_from_hourly,
                "calculation_version": "historical-window-summary.v1",
                **({"daily_close_returns": _daily_close_return_originals(
                    rows, closed_before=min(now, query.window_end_at, query.cutoff_at))}
                   if dataset_id in {"crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"}
                   else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_url = (
            "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair="
            + coverage.instrument.replace("-", "_") + "&interval=1d"
            if coverage.source_id == "gateio-public"
            else _source_url(dataset_id)
        )
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange" if dataset_id.startswith("crypto.") else "official",
            source_id=coverage.source_id,
            source_url=source_url,
            published_at=None,
            excerpt=excerpt,
            structured_payload_ref=manifest_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange" if dataset_id.startswith("crypto.") else "official",
            source_id=coverage.source_id,
            source_url=AnyUrl(source_url),
            published_at=None,
            observed_at=now,
            received_at=now,
            content_hash=content_hash,
            excerpt=excerpt,
            structured_payload_ref=manifest_ref,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        daily_receipt_json = None
        daily_execution_json = None
        if dataset_id in {"crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"}:
            if prepared_daily is not None:
                executed_daily = finish_daily_computation(prepared_daily, candidate=candidate)
                daily_receipt_json = executed_daily.strategy_json
                daily_execution_json = executed_daily.receipt_json
            else:
                timestamp = rows[-1].get("timestamp")
                receipt = compute_daily_strategy(
                    _daily_strategy_rows(rows),
                    as_of=timestamp[:10] if isinstance(timestamp, str) else None,
                    evidence_refs=(candidate.evidence_id,),
                )
                daily_receipt_json = receipt.as_json()
        facts = [
            _fact(
                query,
                candidate,
                coverage,
                field,
                selected[field],
                now,
                daily_receipt_json=daily_receipt_json,
                daily_execution_json=daily_execution_json,
                dataset_id=dataset_id,
                derived_from_hourly=derived_from_hourly,
            )
            for field in sorted(selected)
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="local-historical-archive",
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=self.clock() if daily_execution_json is not None else now,
        )

    def _bind_archived_captures(
        self, manifest_ref: str, coverage: DatasetCoverage, query: ResearchCapabilityQuery,
    ) -> None:
        batch = CURRENT_CAPTURE.get()
        if batch is None:
            return
        try:
            if not coverage.raw_capture_ids:
                raise ValueError("historical_original_receipts_missing")
            manifest = self.archive.load_coverage(manifest_ref)
            receipts = []
            with self.jobs.database.session() as session:
                for identity in coverage.raw_capture_ids:
                    row = session.get(RawCaptureRecord, identity)
                    if (row is None or row.status_code is None
                            or not 200 <= row.status_code < 300 or not row.body_ref
                            or row.error_code
                            or not row.context_id.startswith(f"historical:{manifest.job_id}:")
                            or not row.context_id.endswith(f":{coverage.dataset_id}")
                            or as_utc(row.completed_at) > query.cutoff_at
                            or as_utc(row.recorded_at) > query.cutoff_at):
                        raise ValueError("historical_original_receipt_invalid_or_late")
                    # The archive verifies its content address; no network/refetch.
                    body = batch.archive.read(row.body_ref)
                    if hashlib.sha256(body).hexdigest() != row.body_hash:
                        raise ValueError("historical_original_receipt_hash_mismatch")
                    receipts.append({c.name: getattr(row, c.name) for c in row.__table__.columns})
            existing = {c["capture_id"] for c in batch.captures}
            batch.captures.extend(c for c in receipts if c["capture_id"] not in existing)
        except (OSError, ValueError) as exc:
            raise ResearchCapabilityError(
                "historical_original_archive_unavailable",
                "historical rows lack verifiable original collection receipts; refresh data first",
                retryable=False, origin="storage", cause_code=(
                    str(exc) if isinstance(exc, ValueError) else "historical_original_file_missing"
                ),
            ) from exc

    def _locate(
        self,
        dataset_id: str,
        start: datetime,
        end: datetime,
        *,
        allow_stale_days: int = 0,
    ) -> tuple[str, DatasetCoverage] | None:
        for job in self.jobs.list(limit=500):
            if job.status not in {"completed", "partial"} or not job.coverage_manifest_ref:
                continue
            manifest = self.archive.load_coverage(job.coverage_manifest_ref)
            if manifest.requested_start_at > start:
                continue
            coverage = next(
                (item for item in manifest.datasets if item.dataset_id == dataset_id), None
            )
            if coverage is None or coverage.status == "missing" or not coverage.partition_refs:
                continue
            if manifest.requested_end_at < end and (
                allow_stale_days <= 0
                or coverage.actual_end_at is None
                or coverage.actual_end_at < end - timedelta(days=allow_stale_days)
            ):
                continue
            return job.coverage_manifest_ref, coverage
        return None

    def _rows(
        self, coverage: DatasetCoverage, start: datetime, end: datetime
    ) -> list[dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        for partition_ref in coverage.partition_refs:
            _payload, partition_rows = self.archive.load_partition(partition_ref)
            for row in partition_rows:
                timestamp = _timestamp(row.get("timestamp"))
                if timestamp is not None and start <= timestamp < end:
                    rows[timestamp.isoformat()] = row
        return [rows[key] for key in sorted(rows)]


def _aggregate_hourly_to_daily(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Aggregate retained closed hourly rows into UTC daily candles."""

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        timestamp = _timestamp(row.get("timestamp"))
        if timestamp is None or row.get("confirmed", True) is False:
            continue
        grouped.setdefault(timestamp.date().isoformat(), []).append(row)
    daily: list[dict[str, object]] = []
    for date_text, items in sorted(grouped.items()):
        timestamps = [_timestamp(item.get("timestamp")) for item in items]
        day_start = datetime.fromisoformat(date_text).replace(tzinfo=UTC)
        expected = {day_start + timedelta(hours=hour) for hour in range(24)}
        if len(timestamps) != 24 or set(timestamps) != expected:
            # A partial day or duplicated hour is not a closed daily candle.
            continue
        ordered = sorted(
            items,
            key=lambda item: _timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=UTC),
        )
        daily.append(
            {
                "timestamp": f"{date_text}T00:00:00+00:00",
                "open": ordered[0].get("open"),
                "high": _format(max(_decimal(item.get("high")) for item in ordered)),
                "low": _format(min(_decimal(item.get("low")) for item in ordered)),
                "close": ordered[-1].get("close"),
                "volume_base": _format(
                    sum((_decimal(item.get("volume_base")) for item in ordered), Decimal(0))
                ),
                "confirmed": True,
            }
        )
    return daily


def _daily_close_return_originals(
    rows: Sequence[Mapping[str, object]], *, closed_before: datetime,
) -> dict[str, dict[str, str] | None]:
    """Publish short-return inputs alongside the summary the reviewer receives.

    These are close-to-close calendar-day returns, not intraday price moves or
    predictive probabilities. A missing UTC day leaves that horizon unknown.
    """
    closed = sorted(
        ((stamp, row) for row in rows
         if row.get("confirmed", True) is not False
         and (stamp := _timestamp(row.get("timestamp"))) is not None
         and stamp + timedelta(days=1) <= closed_before),
        key=lambda pair: pair[0],
    )
    result: dict[str, dict[str, str] | None] = {}
    for days in (1, 2, 7):
        name = f"return_{days}d_pct"
        result[name] = None
        window = closed[-days-1:]
        if len(window) != days + 1 or any(
            after[0] - before[0] != timedelta(days=1)
            for before, after in zip(window, window[1:], strict=False)
        ):
            continue
        start_at, start_row = window[0]
        end_at, end_row = window[-1]
        start, end = _decimal(start_row.get("close")), _decimal(end_row.get("close"))
        if start <= 0 or end <= 0:
            continue
        result[name] = {
            "start_at": (start_at + timedelta(days=1)).isoformat(),
            "end_at": (end_at + timedelta(days=1)).isoformat(),
            "start_value": _format(start), "end_value": _format(end),
            "value": _format((end / start - Decimal(1)) * Decimal(100)),
            "unit": "percent", "price_basis": "spot_daily_close",
            "formula": "(end_value/start_value-1)*100",
        }
    return result


def _summary(dataset_id: str, rows: list[dict[str, object]]) -> dict[str, str]:
    if dataset_id == "crypto.btc_usdt.ohlcv_1h":
        first = _decimal(rows[0].get("open"))
        last = _decimal(rows[-1].get("close"))
        volume = sum((_decimal(row.get("volume_base")) for row in rows), Decimal(0))
        return {
            "period_open": _format(first),
            "period_close": _format(last),
            "return_pct": _format((last / first - Decimal(1)) * Decimal(100)),
            "volume_sum": _format(volume),
            "row_count": str(len(rows)),
        }
    if dataset_id in {"crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"}:
        rows = [row for row in rows if row.get("confirmed", True) is not False]
        closes = [_decimal(row.get("close")) for row in rows]
        volumes = [_decimal(row.get("volume_base")) for row in rows]
        if len(closes) < 60:
            raise ResearchCapabilityError(
                "historical_daily_window_insufficient",
                "daily strategy requires at least 60 completed candles",
                retryable=False,
                origin="storage",
            )
        short = closes[-20:]
        long = closes[-60:]
        returns = [
            closes[index] / closes[index - 1] - Decimal(1)
            for index in range(len(closes) - 20, len(closes))
        ]
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns))
        volume_median = sorted(volumes[-20:])[10]
        return {
            "period_close": _format(closes[-1]),
            "sma_20": _format(sum(short, Decimal(0)) / Decimal(20)),
            "sma_60": _format(sum(long, Decimal(0)) / Decimal(60)),
            "return_20_pct": _format((closes[-1] / closes[-21] - Decimal(1)) * Decimal(100)),
            "realized_vol_20_pct": _format(variance.sqrt() * Decimal(365).sqrt() * Decimal(100)),
            "volume_ratio_20": _format(volumes[-1] / volume_median) if volume_median else "0",
            "rolling_high_20": _format(max(_decimal(row.get("high")) for row in rows[-20:])),
            "rolling_low_20": _format(min(_decimal(row.get("low")) for row in rows[-20:])),
            "row_count": str(len(rows)),
        }
    if dataset_id == "crypto.btc_usdt_swap.funding_rate":
        values = [_decimal(row.get("funding_rate")) for row in rows]
        return {
            "mean_funding_rate": _format(sum(values, Decimal(0)) / Decimal(len(values))),
            "latest_funding_rate": _format(values[-1]),
            "min_funding_rate": _format(min(values)),
            "max_funding_rate": _format(max(values)),
            "row_count": str(len(rows)),
        }
    values = [_decimal(row.get("value")) for row in rows]
    return {
        "period_start_value": _format(values[0]),
        "period_end_value": _format(values[-1]),
        "change": _format(values[-1] - values[0]),
        "row_count": str(len(rows)),
    }


def _fact(
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    coverage: DatasetCoverage,
    field: str,
    value: str,
    now: datetime,
    *,
    daily_receipt_json: str | None = None,
    daily_execution_json: str | None = None,
    dataset_id: str | None = None,
    derived_from_hourly: bool = False,
) -> FactEnvelope:
    effective_dataset_id = dataset_id or coverage.dataset_id
    unit = _unit(effective_dataset_id, field, coverage.unit)
    attributes: dict[str, float | str | bool | None] = {
        "dataset_id": effective_dataset_id,
        "coverage_status": coverage.status,
        "calculation_version": "historical-window-summary.v1",
        "derived_from_hourly": derived_from_hourly,
    }
    if daily_receipt_json is not None:
        attributes["daily_strategy_receipt_json"] = daily_receipt_json
    if daily_execution_json is not None:
        attributes["daily_execution_receipt_json"] = daily_execution_json
    metric_family = (
        "crypto.spot.history"
        if effective_dataset_id.endswith(("ohlcv_1h", "ohlcv_1d"))
        else "crypto.derivatives.history"
        if effective_dataset_id.startswith("crypto.")
        else "macro.history"
    )
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=coverage.instrument,
        venue=coverage.venue,
        value=value,
        unit=unit,
        window_start_at=query.window_start_at,
        window_end_at=query.window_end_at,
        event_offset=None,
        published_at=None,
        source_id=coverage.source_id,
        independence_group=f"{coverage.source_id}:{effective_dataset_id}",
        delay_class="historical",
        payload_schema_ref="historical-window-summary.v1",
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=coverage.instrument,
        venue=coverage.venue,
        value=value,
        unit=unit,
        window_start_at=query.window_start_at,
        window_end_at=query.window_end_at,
        event_offset=None,
        observed_at=now,
        received_at=now,
        published_at=None,
        source_id=coverage.source_id,
        independence_group=f"{coverage.source_id}:{effective_dataset_id}",
        quality="candidate",
        delay_class="historical",
        payload_schema_ref="historical-window-summary.v1",
        payload_hash=payload_hash,
        attributes=attributes,
    )


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise InvalidOperation
        return parsed
    except (InvalidOperation, ValueError) as exc:
        raise ResearchCapabilityError(
            "historical_numeric_value_invalid",
            "historical partition contains a non-finite numeric value",
            retryable=False,
            origin="storage",
        ) from exc


def _format(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _source_url(dataset_id: str) -> str:
    if dataset_id.startswith("crypto."):
        return "https://www.okx.com/docs-v5/en/"
    series = dataset_id.rsplit(".", 1)[-1].upper()
    return f"https://fred.stlouisfed.org/series/{series}"


def _unit(dataset_id: str, field: str, default: str) -> str:
    if field == "row_count":
        return "count"
    if field in {"return_pct", "return_20_pct", "realized_vol_20_pct"}:
        return "percent"
    if field == "volume_ratio_20":
        return "ratio"
    if "volume" in field:
        return "eth" if dataset_id.startswith("crypto.eth_") else "btc"
    if "funding_rate" in field:
        return "rate"
    if dataset_id.endswith(("ohlcv_1h", "ohlcv_1d")):
        return "usdt"
    return default


def _daily_strategy_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Extract the original strategy mapping as the actual loaded callable."""
    projected: list[dict[str, object]] = []
    for row in rows:
        timestamp = _timestamp(row.get("timestamp"))
        if timestamp is not None and row.get("confirmed", True) is not False:
            projected.append({
                "date": timestamp.date().isoformat(),
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
                "volume": row.get("volume_base"),
            })
    return projected


def loaded_daily_implementation() -> LoadedDailyImplementation:
    """Capture actual callables; this is not an execution credential."""
    return LoadedDailyImplementation(
        normalizer=normalize_gate_daily_payload, row_projector=_daily_strategy_rows,
        summary=_summary, numeric=_decimal, formatter=_format,
        timestamp=_timestamp, units=_unit,
    )
