from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.macro_event import (
    reconcile_macro_release_in_session,
    upsert_macro_event_in_session,
)
from packages.kernel.decision_hub_kernel.application.study import ensure_occurrence
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ObservationRecord,
    utcnow,
)


def _hash_text(text: str, *, request_namespace: str | None = None) -> str:
    """Hash the admitted observation and, when supplied, its request scope.

    Ordinary source observations remain content-deduplicated.  The fixed
    "latest BTC advice" entry is a *new market snapshot request* each time a
    caller supplies a new idempotency key; hashing that key prevents a stale
    observation/event from being silently reused while preserving retry
    idempotency for the same HTTP request.
    """

    payload = text if not request_namespace else f"{request_namespace}\n{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _infer_event_hint(request: ObservationCreate) -> str | None:
    """Classify an untyped manual crypto snapshot before building the Run.

    The UI accepts free-form research text and historically left ``event_hint``
    empty.  Admission then stored ``macro_event`` for every such request, so
    the crypto-market snapshot policy (which removes policy-event requirements
    and requires two venue snapshots) was never applied.  Keep explicit hints
    authoritative and infer only when the text contains an asset, a current
    snapshot cue, and a crypto-market metric cue.
    """

    if request.event_hint:
        return request.event_hint
    text = request.text.casefold()
    has_asset = any(
        marker in text for marker in ("btc", "bitcoin", "比特币", "btc/usdt", "btc-usdt", "btcusdt")
    )
    has_snapshot_cue = any(
        marker in text
        for marker in ("当前", "最新", "实时", "快照", "current", "latest", "snapshot")
    )
    has_market_metric = any(
        marker in text
        for marker in (
            "价格",
            "成交量",
            "现货",
            "永续",
            "资金费率",
            "未平仓",
            "订单簿",
            "基差",
            "price",
            "volume",
            "spot",
            "perpetual",
            "funding",
            "open interest",
            "order book",
            "basis",
        )
    )
    return (
        "crypto_market_snapshot" if has_asset and has_snapshot_cue and has_market_metric else None
    )


def _admission_utc(value: datetime, field: str) -> datetime:
    """Require a real input instant; do not interpret new naive values as UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"admission_timestamp_offset_required:{field}")
    return value.astimezone(UTC)


def _utc_text_envelope(envelope: TextEnvelope) -> TextEnvelope:
    """Project a valid UTC copy without changing source text, hashes or labels."""
    updates = {
        "observed_at": _admission_utc(envelope.observed_at, "observed_at"),
        "received_at": _admission_utc(envelope.received_at, "received_at"),
        "published_at": (_admission_utc(envelope.published_at, "published_at")
                         if envelope.published_at is not None else None),
        "scheduled_at": (_admission_utc(envelope.scheduled_at, "scheduled_at")
                         if envelope.scheduled_at is not None else None),
    }
    if envelope.macro_event is not None:
        payload = envelope.macro_event
        updates["macro_event"] = payload.model_copy(update={
            "scheduled_at": _admission_utc(payload.scheduled_at, "macro_event.scheduled_at"),
            "vintage_at": (_admission_utc(payload.vintage_at, "macro_event.vintage_at")
                           if payload.vintage_at is not None else None),
        })
    # scheduled_date/time_precision/source_timezone are metadata, not inferred
    # instants. Preserve them, and never mutate the caller's original DTO.
    return envelope.model_copy(update=updates)


class AdmissionService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def admit(
        self,
        request: ObservationCreate,
        *,
        idempotency_key: str | None = None,
        session: Session | None = None,
    ) -> tuple[str, TextEnvelope, bool]:
        now = self.clock()
        # Keep the original valid clock spelling in the content-hash namespace;
        # UTC conversion belongs to the immutable input's storage projection.
        _admission_utc(now, "clock")
        observed_at = request.observed_at or now
        event_hint = _infer_event_hint(request)
        # A repeated terse/latest-advice prompt is not the same observation:
        # it asks for a fresh snapshot.  Use the caller's idempotency key as a
        # namespace only for that fixed entry; all other observations retain
        # the original content-hash deduplication semantics.
        request_namespace = (
            f"latest-advice:{idempotency_key or now.isoformat()}"
            if request.source_id == "decision-desk-latest-advice"
            and event_hint == "crypto_market_snapshot"
            else None
        )
        content_hash = _hash_text(request.text, request_namespace=request_namespace)
        envelope = TextEnvelope(
            source_id=request.source_id,
            source_type=request.source_type,
            observed_at=observed_at,
            published_at=request.published_at,
            received_at=now,
            raw_text=request.text,
            language=request.language,
            event_hint=event_hint,
            source_url=request.source_url,
            content_hash=content_hash,
        )
        result = self.admit_envelopes((envelope,), session=session)
        return result[0]

    def admit_envelopes(
        self, envelopes: tuple[TextEnvelope, ...], *, session: Session | None = None
    ) -> list[tuple[str, TextEnvelope, bool]]:
        """Atomically admit a source batch while preserving PIT timestamps and revisions."""
        # Validate the whole batch before any Session access/autoflush. Invalid
        # source or nested timestamps must not partially touch a borrowed UOW.
        projected = tuple(_utc_text_envelope(envelope) for envelope in envelopes)
        if not projected:
            return []
        now = _admission_utc(self.clock(), "clock")
        results: list[tuple[str, TextEnvelope, bool]] = []
        with self.database.session() if session is None else nullcontext(session) as session:
            for envelope in projected:
                existing = (
                    session.query(ObservationRecord)
                    .filter_by(content_hash=envelope.content_hash)
                    .first()
                )
                if existing:
                    results.append((existing.event_id, envelope, False))
                    continue
                event_id = f"evt_{uuid.uuid4().hex}"
                observation_id = f"obs_{uuid.uuid4().hex}"
                session.add(
                    EventRecord(
                        event_id=event_id,
                        event_type=envelope.event_hint or "macro_event",
                        # A calendar envelope describes a planned occurrence;
                        # ordinary text retains observed_at as its event time.
                        occurred_at=envelope.scheduled_at or envelope.observed_at,
                        received_at=envelope.received_at,
                    )
                )
                session.add(
                    ObservationRecord(
                        observation_id=observation_id,
                        event_id=event_id,
                        source_id=envelope.source_id,
                        source_type=envelope.source_type.value,
                        observed_at=envelope.observed_at,
                        published_at=envelope.published_at,
                        received_at=envelope.received_at,
                        language=envelope.language,
                        text=envelope.raw_text,
                        content_hash=envelope.content_hash,
                        source_url=str(envelope.source_url) if envelope.source_url else None,
                        event_hint=envelope.event_hint,
                        scheduled_at=envelope.scheduled_at,
                        revision_of=envelope.revision_of,
                    )
                )
                upsert_macro_event_in_session(
                    session,
                    event_id=event_id,
                    envelope=envelope,
                    now=now,
                )
                reconcile_macro_release_in_session(
                    session,
                    event_id=event_id,
                    envelope=envelope,
                    now=now,
                )
                session.flush()
                ensure_occurrence(session, event_id, now)
                results.append((event_id, envelope, True))
        return results
