"""Read the original whole-window captures behind committed spot research.

This is provenance readback, not re-admission or a new market acquisition.
Collector receipts are shared market originals, not owned by a research Run;
the evidence and Fact binding establishes which Run actually used them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Never

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..persistence.db import RawCaptureRecord, ResearchEvidenceRecord, ResearchFactRecord, as_utc
from ..ports.market_path import aware_utc, path_hashes, spot_scope
from .fact_store import research_fact_instance_id, research_fact_payload_hash
from .market_path_store import MarketPathStore
from .provider_input_receipts import strict_json
from .research_evidence import research_evidence_content_hash, research_evidence_instance_id


def _reject(reason: str) -> Never:
    raise ValueError("research_spot_capture_" + reason)


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        _reject("clock_invalid")
    try:
        return aware_utc(datetime.fromisoformat(value))
    except (ValueError, TypeError) as exc:
        raise ValueError("research_spot_capture_clock_invalid") from exc


def spot_evidence_captures_in_session(
    session: Session,
    *,
    run_id: str,
    evidence: ResearchEvidenceRecord,
    store: MarketPathStore,
) -> list[RawCaptureRecord] | None:
    """None means another adapter; [] never masks a broken spot lineage.

    The caller checks the requested Run/evidence before entering this function.
    Selection uses the original source-known clock and must reproduce both
    frozen path hashes. Later corrections are never substituted.
    """
    if evidence.capability_id != "spot_market_facts":
        return None
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        native = connection.connection.driver_connection
        if native is None:
            _reject("snapshot_required")
        if not native.in_transaction:
            connection.exec_driver_sql("BEGIN")
        # Route may have read evidence before the physical snapshot began.
        session.refresh(evidence)
    if evidence.run_id != run_id:
        _reject("run_mismatch")
    expected_hash = research_evidence_content_hash(
        requirement_id=evidence.requirement_id,
        kind=evidence.kind,
        authority=evidence.authority,
        source_id=evidence.source_id,
        source_url=evidence.source_url,
        published_at=as_utc(evidence.published_at),
        excerpt=evidence.excerpt,
        structured_payload_ref=evidence.structured_payload_ref,
    )
    if (
        evidence.content_hash != expected_hash
        or evidence.evidence_id
        != research_evidence_instance_id(
            content_hash=expected_hash,
            research_session_id=evidence.research_session_id,
        )
        or evidence.source_id != "okx-public"
        or evidence.kind != "market"
        or evidence.authority != "exchange"
        or evidence.published_at is not None
        or any(
            (
                evidence.content_ref,
                evidence.acquisition_receipt_id,
                evidence.structured_payload_ref,
                evidence.source_content_hash,
                evidence.parser_ref,
            )
        )
    ):
        _reject("evidence_identity_mismatch")
    summary = strict_json(evidence.excerpt)
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != "okx.spot-ledger-research.v1"
    ):
        _reject("summary_invalid")
    instrument = summary.get("instrument")
    if instrument not in {"BTC-USDT", "ETH-USDT"}:
        _reject("scope_mismatch")
    scope = spot_scope(instrument)
    if (
        summary.get("scope_id") != scope.scope_id
        or summary.get("instrument_kind") != "spot"
        or summary.get("price_type") != "trade"
        or evidence.source_url
        != f"https://www.okx.com/api/v5/market/history-candles?instId={instrument}&bar=1m"
    ):
        _reject("scope_mismatch")
    window = summary.get("requested_window")
    if not isinstance(window, list) or len(window) != 2:
        _reject("window_invalid")
    start, end = _time(window[0]), _time(window[1])
    known, latest = _time(summary.get("source_known_at")), _time(summary.get("latest_close_at"))
    observed, received = as_utc(evidence.observed_at), as_utc(evidence.received_at)
    if (
        not start < latest <= end
        or end - start > timedelta(hours=24)
        or start.timestamp() % 60
        or end.timestamp() % 60
        or observed is None
        or received is None
        or not latest <= known <= received
        or not latest <= observed <= received
    ):
        _reject("clock_mismatch")
    facts = session.scalars(
        select(ResearchFactRecord).where(
            ResearchFactRecord.evidence_id == evidence.evidence_id,
        )
    ).all()
    if not facts:
        _reject("fact_missing")
    attrs_by_fact: list[dict[str, Any]] = []
    for fact in facts:
        attrs = strict_json(fact.attributes_json)
        value = strict_json(fact.value_json)
        if not isinstance(attrs, dict):
            _reject("fact_invalid")
        payload_hash = research_fact_payload_hash(
            requirement_id=fact.requirement_id,
            metric_family=fact.metric_family,
            field=fact.field,
            instrument=fact.instrument,
            venue=fact.venue,
            value=value,
            unit=fact.unit,
            window_start_at=as_utc(fact.window_start_at),
            window_end_at=as_utc(fact.window_end_at),
            event_offset=fact.event_offset,
            published_at=as_utc(fact.published_at),
            source_id=fact.source_id,
            independence_group=fact.independence_group,
            delay_class=fact.delay_class,
            payload_schema_ref=fact.payload_schema_ref,
            attributes=attrs,
        )
        if (
            fact.run_id != evidence.run_id
            or fact.capability_id != evidence.capability_id
            or fact.requirement_id != evidence.requirement_id
            or fact.source_id != evidence.source_id
            or fact.metric_family != "crypto.spot"
            or fact.venue != "okx"
            or fact.instrument != instrument
            or fact.payload_schema_ref != "okx.spot-ledger-fact.v1"
            or fact.payload_hash != payload_hash
            or fact.fact_id
            != research_fact_instance_id(
                evidence_id=evidence.evidence_id, payload_hash=payload_hash
            )
            or as_utc(fact.received_at) != received
            or as_utc(fact.observed_at) != observed
            or as_utc(fact.window_end_at) != latest
            or attrs.get("scope_id") != scope.scope_id
            or attrs.get("path_manifest_hash") != summary.get("manifest_hash")
            or attrs.get("path_semantic_hash") != summary.get("semantic_hash")
            or _time(attrs.get("source_known_at")) != known
            or attrs.get("window_status") != summary.get("path_status")
        ):
            _reject("fact_binding_mismatch")
        attrs_by_fact.append(attrs)
    try:
        source_bars = store.load_in_session(
            session,
            scope,
            start_at=start - timedelta(minutes=1),
            end_at=min(end, known.replace(second=0, microsecond=0)),
            as_of=known,
        )
    except OSError as exc:
        raise ValueError("research_spot_capture_original_unavailable") from exc
    except ValueError as exc:
        raise ValueError("research_spot_capture_original_invalid") from exc
    bars = tuple(bar for bar in source_bars if bar.open_at >= start)
    if not bars:
        _reject("window_unavailable")
    semantic, manifest = path_hashes(scope, bars)
    complete = len(bars) == int((end - start).total_seconds() // 60)
    if (
        (semantic, manifest) != (summary.get("semantic_hash"), summary.get("manifest_hash"))
        or type(summary.get("observed_bars")) is not int
        or len(bars) != summary["observed_bars"]
        or summary.get("expected_bars") != int((end - start).total_seconds() // 60)
        or summary.get("path_status") != ("complete" if complete else "partial")
        or bars[-1].close_at != latest
        or max(bar.known_at for bar in source_bars) != known
    ):
        _reject("window_manifest_mismatch")
    for attrs in attrs_by_fact:
        if (
            attrs.get("latest_capture_ref") != bars[-1].capture_ref
            or attrs.get("latest_capture_hash") != bars[-1].capture_hash
            or _time(attrs.get("source_received_at")) != max(bar.received_at for bar in source_bars)
        ):
            _reject("latest_receipt_mismatch")
    pairs = tuple(dict.fromkeys((bar.capture_ref, bar.capture_hash) for bar in bars))
    rows: list[RawCaptureRecord] = []
    for offset in range(0, len(pairs), 256):
        rows.extend(
            session.scalars(
                select(RawCaptureRecord).where(
                    tuple_(RawCaptureRecord.body_ref, RawCaptureRecord.body_hash).in_(
                        pairs[offset : offset + 256]
                    ),
                )
            ).all()
        )
    selected: dict[str, RawCaptureRecord] = {}
    for bar in bars:
        # Same strict URL/clock predicate as the existing store's byte verifier.
        matches = [
            row
            for row in rows
            if (
                row.body_ref == bar.capture_ref
                and row.body_hash == bar.capture_hash
                and row.status_code == 200
                and row.error_code is None
                and as_utc(row.completed_at) == bar.received_at
                and MarketPathStore._url_matches(row.url, scope, bar)  # pyright: ignore[reportPrivateUsage]
            )
        ]
        if not matches:
            _reject("receipt_missing")
        for row in matches:
            if row.started_at > row.completed_at:
                _reject("receipt_clock_invalid")
            selected[row.capture_id] = row
    # Each byte original was already verified by MarketPathStore. Check persisted
    # length metadata too; raw file hash failures never become an empty list.
    lengths: dict[str, int] = {}
    for row in selected.values():
        assert row.body_ref is not None
        if row.body_ref not in lengths:
            lengths[row.body_ref] = len(store.archive.read(row.body_ref))
        if row.byte_count != lengths[row.body_ref]:
            _reject("receipt_length_mismatch")
    def order(row: RawCaptureRecord) -> tuple[datetime, str, str]:
        return row.completed_at, row.url, row.capture_id

    return sorted(selected.values(), key=order)
