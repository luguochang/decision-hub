from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts import FactEnvelope
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRecord,
    as_utc,
    utcnow,
)


class ResearchFactStore:
    """Persist canonical typed facts after evidence and lineage attestation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def accept_facts(
        self,
        *,
        run_id: str,
        capability_id: str,
        facts: Iterable[FactEnvelope],
        cutoff_at: datetime,
    ) -> list[FactEnvelope]:
        cutoff = _aware(cutoff_at)
        accepted: list[FactEnvelope] = []
        with self.database.session() as session:
            if session.get(RunRecord, run_id) is None:
                raise KeyError(run_id)
            for fact in facts:
                evidence = session.get(ResearchEvidenceRecord, fact.evidence_id)
                if evidence is None or evidence.run_id != run_id:
                    raise ValueError("research_fact_evidence_not_found")
                if (
                    evidence.requirement_id != fact.requirement_id
                    or evidence.source_id != fact.source_id
                ):
                    raise ValueError("research_fact_lineage_mismatch")
                _validate_fact_pit(fact, cutoff)
                normalized = fact.model_copy(
                    update={
                        "quality": (
                            "accepted" if evidence.quality == "accepted" else evidence.quality
                        )
                    }
                )
                existing = session.get(ResearchFactRecord, fact.fact_id)
                if existing is not None:
                    persisted = _fact_model(existing)
                    if not _same_semantic_fact(persisted, normalized):
                        raise ValueError("research_fact_identity_conflict")
                    accepted.append(persisted)
                    continue
                session.add(
                    ResearchFactRecord(
                        fact_id=normalized.fact_id,
                        run_id=run_id,
                        capability_id=capability_id,
                        evidence_id=normalized.evidence_id,
                        requirement_id=normalized.requirement_id,
                        metric_family=normalized.metric_family,
                        field=normalized.field,
                        instrument=normalized.instrument,
                        venue=normalized.venue,
                        value_json=json.dumps(
                            normalized.value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        unit=normalized.unit,
                        window_start_at=normalized.window_start_at,
                        window_end_at=normalized.window_end_at,
                        event_offset=normalized.event_offset,
                        observed_at=normalized.observed_at,
                        received_at=normalized.received_at,
                        published_at=normalized.published_at,
                        source_id=normalized.source_id,
                        independence_group=normalized.independence_group,
                        quality=normalized.quality,
                        delay_class=normalized.delay_class,
                        payload_schema_ref=normalized.payload_schema_ref,
                        payload_hash=normalized.payload_hash,
                        attributes_json=json.dumps(
                            normalized.attributes,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        accepted_at=utcnow(),
                    )
                )
                accepted.append(normalized)
        return accepted

    def list_run_facts(self, run_id: str) -> list[FactEnvelope]:
        with self.database.session() as session:
            rows = (
                session.query(ResearchFactRecord)
                .filter_by(run_id=run_id)
                .order_by(ResearchFactRecord.accepted_at, ResearchFactRecord.fact_id)
                .all()
            )
            return [_fact_model(row) for row in rows]


def research_fact_payload_hash(
    *,
    requirement_id: str,
    metric_family: str,
    field: str,
    instrument: str | None,
    venue: str | None,
    value: float | str | None,
    unit: str,
    window_start_at: datetime | None,
    window_end_at: datetime | None,
    event_offset: str | None,
    published_at: datetime | None,
    source_id: str,
    independence_group: str,
    delay_class: str,
    payload_schema_ref: str,
    attributes: dict[str, float | str | bool | None],
) -> str:
    """Hash provider-owned semantics without volatile gateway receive times."""

    semantic = {
        "attributes": attributes,
        "delay_class": delay_class,
        "event_offset": event_offset,
        "field": field,
        "independence_group": independence_group,
        "instrument": instrument,
        "metric_family": metric_family,
        "payload_schema_ref": payload_schema_ref,
        "published_at": published_at.isoformat() if published_at is not None else None,
        "requirement_id": requirement_id,
        "source_id": source_id,
        "unit": unit,
        "value": value,
        "venue": venue,
        "window_end_at": window_end_at.isoformat() if window_end_at is not None else None,
        "window_start_at": window_start_at.isoformat() if window_start_at is not None else None,
    }
    encoded = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def research_fact_instance_id(*, evidence_id: str, payload_hash: str) -> str:
    return f"fact_{hashlib.sha256(f'{evidence_id}:{payload_hash}'.encode()).hexdigest()[:32]}"


def _same_semantic_fact(persisted: FactEnvelope, candidate: FactEnvelope) -> bool:
    """Ignore only volatile receipt times for one content-addressed fact."""

    return persisted == candidate.model_copy(
        update={
            "observed_at": persisted.observed_at,
            "received_at": persisted.received_at,
        }
    )


def _validate_fact_pit(fact: FactEnvelope, cutoff: datetime) -> None:
    observed = _aware(fact.observed_at)
    received = _aware(fact.received_at)
    published = _aware(fact.published_at) if fact.published_at is not None else None
    start = _aware(fact.window_start_at) if fact.window_start_at is not None else None
    end = _aware(fact.window_end_at) if fact.window_end_at is not None else None
    if published is not None and published > observed:
        raise ValueError("research_fact_pit_violation")
    if observed > received or received > cutoff:
        raise ValueError("research_fact_pit_violation")
    if (start is None) != (end is None) or (
        start is not None and end is not None and (start > end or end > observed)
    ):
        raise ValueError("research_fact_window_invalid")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research_fact_timestamp_must_be_aware")
    return value.astimezone(UTC)


def _fact_model(row: ResearchFactRecord) -> FactEnvelope:
    observed_at = as_utc(row.observed_at)
    received_at = as_utc(row.received_at)
    if observed_at is None or received_at is None:  # pragma: no cover - non-null DB guard
        raise ValueError("research_fact_timestamp_missing")
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=row.fact_id,
        evidence_id=row.evidence_id,
        requirement_id=row.requirement_id,
        metric_family=row.metric_family,
        field=row.field,
        instrument=row.instrument,
        venue=row.venue,
        value=json.loads(row.value_json),
        unit=row.unit,
        window_start_at=as_utc(row.window_start_at),
        window_end_at=as_utc(row.window_end_at),
        event_offset=row.event_offset,
        observed_at=observed_at,
        received_at=received_at,
        published_at=as_utc(row.published_at),
        source_id=row.source_id,
        independence_group=row.independence_group,
        quality=row.quality,  # type: ignore[arg-type]
        delay_class=row.delay_class,  # type: ignore[arg-type]
        payload_schema_ref=row.payload_schema_ref,
        payload_hash=row.payload_hash,
        attributes=json.loads(row.attributes_json),
    )
