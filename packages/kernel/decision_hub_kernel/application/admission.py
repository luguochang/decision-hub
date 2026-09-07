from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ObservationRecord,
    utcnow,
)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AdmissionService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def admit(self, request: ObservationCreate) -> tuple[str, TextEnvelope, bool]:
        now = self.clock()
        observed_at = request.observed_at or now
        content_hash = _hash_text(request.text)
        envelope = TextEnvelope(
            source_id=request.source_id,
            source_type=request.source_type,
            observed_at=observed_at,
            published_at=request.published_at,
            received_at=now,
            raw_text=request.text,
            language=request.language,
            event_hint=request.event_hint,
            source_url=request.source_url,
            content_hash=content_hash,
        )
        result = self.admit_envelopes((envelope,))
        return result[0]

    def admit_envelopes(
        self, envelopes: tuple[TextEnvelope, ...]
    ) -> list[tuple[str, TextEnvelope, bool]]:
        """Atomically admit a source batch while preserving PIT timestamps and revisions."""
        results: list[tuple[str, TextEnvelope, bool]] = []
        with self.database.session() as session:
            for envelope in envelopes:
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
                results.append((event_id, envelope, True))
        return results
