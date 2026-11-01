"""Private draft metadata and repository: no production Base registration/commit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from packages.kernel.decision_hub_kernel.ports.action_quote_clock import reconstruct_clock_receipt
from sqlalchemy import DDL, DateTime, Index, String, Table, Text, UniqueConstraint, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from packages.kernel.decision_hub_kernel.persistence.db import Base, RawCaptureRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.action_quote import (
    MAX_QUOTE_AGE,
    WAIT_FOR_QUOTE,
    CaptureOriginal,
    ParsedQuote,
    decode_json,
    parse_okx_quote,
    require_scope,
    text_at,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    canonical_hash,
)

QuoteBase = Base


class QuoteRecord(QuoteBase):
    __tablename__ = "action_quotes"
    __table_args__ = (
        UniqueConstraint("capture_id", name="uq_action_quote_capture"),
        Index("ix_action_quote_scope_observed", "scope_id", "observed_at"),
        Index("ix_action_quote_semantic", "semantic_hash"),
    )
    quote_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    semantic_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class AvailabilityRecord(QuoteBase):
    __tablename__ = "action_quote_availability"
    quote_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


for _table in (QuoteRecord.__table__, AvailabilityRecord.__table__):
    _table = cast(Table, _table)
    for _operation in ("UPDATE", "DELETE"):
        event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER {_table.name}_immutable_{_operation.lower()} "
                f"BEFORE {_operation} ON {_table.name} BEGIN "
                "SELECT RAISE(ABORT, 'action_quote_immutable'); END"
            ).execute_if(dialect="sqlite"),
        )

for _table, _identity in (
    (QuoteRecord.__table__, "quote_id = NEW.quote_id OR capture_id = NEW.capture_id"),
    (AvailabilityRecord.__table__, "quote_id = NEW.quote_id"),
):
    _table = cast(Table, _table)
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_table.name}_immutable_replace BEFORE INSERT ON {_table.name} "
            f"WHEN EXISTS (SELECT 1 FROM {_table.name} WHERE {_identity}) BEGIN "
            "SELECT RAISE(ABORT, 'action_quote_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )


class RawArchive(Protocol):
    def read(self, ref: str) -> bytes: ...


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _capture(row: RawCaptureRecord) -> CaptureOriginal:
    return CaptureOriginal(
        row.capture_id,
        row.context_id,
        row.url,
        row.status_code,
        row.body_ref,
        row.body_hash,
        row.byte_count,
        row.adapter_version,
        row.error_code,
        as_utc(row.started_at),
        as_utc(row.completed_at),
        as_utc(row.recorded_at),
    )


@dataclass(frozen=True)
class PreparedQuote:
    parsed: ParsedQuote


@dataclass(frozen=True)
class StoredQuote:
    quote_id: str
    parsed: ParsedQuote
    admitted_at: datetime
    payload_hash: str
    available_at: datetime | None = None
    availability_hash: str | None = None


@dataclass(frozen=True)
class QuoteExclusion:
    quote_id: str
    quote_hash: str
    reason: str
    observed_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class QuoteSelection:
    selected: StoredQuote | None
    exclusions: tuple[QuoteExclusion, ...]


class QuoteRepository:
    def __init__(self, archive: RawArchive):
        self.archive = archive

    def prepare(
        self,
        session: Session,
        *,
        scope: MarketPathScope,
        capture_id: str,
        clock_measurement_id: str | None = None,
    ) -> PreparedQuote:
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("action_quote_capture_missing")
        capture = _capture(row)
        raw = self.archive.read(row.body_ref)
        receipt = None
        if clock_measurement_id is not None:

            def original(identity: str) -> tuple[CaptureOriginal, bytes]:
                found = session.get(RawCaptureRecord, identity, populate_existing=True)
                if found is None or found.body_ref is None:
                    raise ValueError("action_quote_clock_capture_missing")
                return _capture(found), self.archive.read(found.body_ref)

            measurement, measured_raw = original(clock_measurement_id)
            measured = decode_json(measured_raw)
            captures = measured.get("captures")
            if not isinstance(captures, list) or len(captures) != 3:
                raise ValueError("action_quote_clock_measurement_shape")
            before, before_raw = original(captures[0]["capture_id"])
            after, after_raw = original(captures[2]["capture_id"])
            receipt = reconstruct_clock_receipt(
                quote=capture,
                quote_raw=raw,
                before=before,
                before_raw=before_raw,
                after=after,
                after_raw=after_raw,
                measurement=measurement,
                measurement_raw=measured_raw,
            )
        return PreparedQuote(parse_okx_quote(scope, capture, raw, clock_receipt_json=receipt))

    def admit(
        self, session: Session, prepared: PreparedQuote, *, admitted_at: datetime
    ) -> StoredQuote:
        parsed = prepared.parsed
        clock = parsed.clock_receipt
        current = self.prepare(
            session,
            scope=parsed.scope,
            capture_id=parsed.capture.capture_id,
            clock_measurement_id=(clock["measurement_capture_ref"] if clock else None),
        )
        if current != prepared:
            raise ValueError("action_quote_original_changed_after_prepare")
        identity = "aq_" + canonical_hash([parsed.scope.scope_id, parsed.capture.capture_id])
        existing = session.get(QuoteRecord, identity, populate_existing=True)
        if existing is not None:
            return self.read(session, quote_id=identity)
        latest = session.scalar(
            select(QuoteRecord.observed_at)
            .where(QuoteRecord.scope_id == parsed.scope.scope_id)
            .order_by(QuoteRecord.observed_at.desc())
            .limit(1)
        )
        if latest is not None and parsed.observed_at < as_utc(latest):
            raise ValueError("action_quote_provider_clock_regressed")
        admitted = aware_utc(admitted_at)
        payload = parsed.payload(admitted_at=admitted)
        digest = canonical_hash(payload)
        session.add(
            QuoteRecord(
                quote_id=identity,
                capture_id=parsed.capture.capture_id,
                scope_id=parsed.scope.scope_id,
                observed_at=parsed.observed_at,
                received_at=parsed.capture.completed_at,
                admitted_at=admitted,
                semantic_hash=parsed.semantic_hash,
                payload_json=_json(payload),
                payload_hash=digest,
            )
        )
        session.flush()
        return StoredQuote(identity, parsed, admitted, digest)

    def read(self, session: Session, *, quote_id: str) -> StoredQuote:
        row = session.get(QuoteRecord, quote_id, populate_existing=True)
        if row is None:
            raise ValueError("action_quote_missing")
        payload = decode_json(row.payload_json)
        scope = MarketPathScope.model_validate(payload.get("scope"))
        clock = payload.get("clock_receipt")
        if payload.get("schema_version") not in {
            "action-quote-original.v1",
            "action-quote-original.v2",
        }:
            raise ValueError("action_quote_version_unsupported")
        if (payload["schema_version"] == "action-quote-original.v2") != isinstance(clock, dict):
            raise ValueError("action_quote_clock_receipt_missing")
        parsed = self.prepare(
            session,
            scope=scope,
            capture_id=row.capture_id,
            clock_measurement_id=clock["measurement_capture_ref"] if clock else None,
        ).parsed
        admitted = as_utc(row.admitted_at)
        expected = parsed.payload(admitted_at=admitted)
        if (
            payload != expected
            or canonical_hash(payload) != row.payload_hash
            or row.quote_id != "aq_" + canonical_hash([scope.scope_id, row.capture_id])
            or row.scope_id != scope.scope_id
            or as_utc(row.observed_at) != parsed.observed_at
            or as_utc(row.received_at) != parsed.capture.completed_at
            or row.semantic_hash != parsed.semantic_hash
        ):
            raise ValueError("action_quote_stored_projection_mismatch")
        availability = session.get(AvailabilityRecord, quote_id, populate_existing=True)
        if availability is None:
            return StoredQuote(quote_id, parsed, admitted, row.payload_hash)
        available_at = as_utc(availability.available_at)
        receipt = self._availability_payload(quote_id, row.payload_hash, available_at)
        if (
            available_at < admitted
            or decode_json(availability.payload_json) != receipt
            or canonical_hash(receipt) != availability.payload_hash
        ):
            raise ValueError("action_quote_availability_mismatch")
        return StoredQuote(
            quote_id, parsed, admitted, row.payload_hash, available_at, availability.payload_hash
        )

    @staticmethod
    def _availability_payload(quote_id: str, digest: str, at: datetime) -> dict[str, Any]:
        return {
            "schema_version": "action-quote-availability.v1",
            "quote_id": quote_id,
            "quote_hash": digest,
            "available_at": text_at(at),
            "basis": "first_service_read_of_committed_quote",
        }

    def record_available(
        self, session: Session, *, quote_id: str, quote_hash: str, observed_available_at: datetime
    ) -> StoredQuote:
        # Internal repository method. Application must own a prior independent
        # committed read; this method alone does not confer that authentication.
        quote = self.read(session, quote_id=quote_id)
        if quote.payload_hash != quote_hash:
            raise ValueError("action_quote_available_source_changed")
        if quote.available_at is not None:
            return quote
        available = aware_utc(observed_available_at)
        if available < quote.admitted_at:
            raise ValueError("action_quote_availability_before_admission")
        payload = self._availability_payload(quote_id, quote_hash, available)
        session.add(
            AvailabilityRecord(
                quote_id=quote_id,
                available_at=available,
                payload_json=_json(payload),
                payload_hash=canonical_hash(payload),
            )
        )
        session.flush()
        return self.read(session, quote_id=quote_id)

    def first_eligible(
        self,
        session: Session,
        *,
        scope: MarketPathScope,
        eligible_at: datetime,
        as_of: datetime,
        clock_policy: str = "legacy",
    ) -> QuoteSelection:
        require_scope(scope)
        eligible, now = aware_utc(eligible_at), aware_utc(as_of)
        end = min(eligible + WAIT_FOR_QUOTE, now)
        rows = session.scalars(
            select(QuoteRecord)
            .join(AvailabilityRecord, AvailabilityRecord.quote_id == QuoteRecord.quote_id)
            .where(
                QuoteRecord.scope_id == scope.scope_id,
                *([QuoteRecord.observed_at >= eligible] if clock_policy == "legacy" else []),
                AvailabilityRecord.available_at >= eligible,
                AvailabilityRecord.available_at <= end,
            )
            .order_by(
                AvailabilityRecord.available_at, QuoteRecord.received_at, QuoteRecord.capture_id
            )
            .limit(129)
        ).all()
        if len(rows) > 128:
            raise ValueError("action_quote_selection_budget_exceeded")
        exclusions: list[QuoteExclusion] = []
        for row in rows:
            quote = self.read(session, quote_id=row.quote_id)
            if quote.parsed.scope != scope:
                raise ValueError("action_quote_selection_scope_mismatch")
            if quote.available_at is None:
                raise ValueError("action_quote_selection_availability_missing")
            # Fresh at HTTP receipt is insufficient after a long DB/consumer delay.
            # Use the original actionable clock, never the later scorer's as_of.
            reason = None
            try:
                lower, upper = quote.parsed.local_interval(clock_policy=clock_policy)
                if lower < eligible or (
                    clock_policy == "bracket_v1" and quote.parsed.capture.started_at < eligible
                ):
                    reason = "action_quote_before_eligible"
                elif upper > quote.available_at or quote.available_at - lower > MAX_QUOTE_AGE:
                    reason = "action_quote_stale_when_available"
            except ValueError as error:
                reason = str(error)
            if reason:
                exclusions.append(
                    QuoteExclusion(
                        quote.quote_id,
                        quote.payload_hash,
                        reason,
                        quote.parsed.observed_at,
                        quote.available_at,
                    )
                )
                continue
            return QuoteSelection(quote, tuple(exclusions))
        return QuoteSelection(None, tuple(exclusions))
