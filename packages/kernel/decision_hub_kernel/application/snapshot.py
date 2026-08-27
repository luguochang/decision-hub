from __future__ import annotations

import hashlib
import json

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ObservationRecord,
    RunRecord,
    SnapshotRecord,
    utcnow,
)


class SnapshotService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def freeze(self, event_id: str, envelope: TextEnvelope) -> tuple[str, list[str]]:
        if envelope.observed_at > envelope.received_at or (
            envelope.published_at is not None and envelope.published_at > envelope.received_at
        ):
            raise ValueError("pit_future_information")
        cutoff = envelope.received_at
        evidence = [
            _evidence_item(
                text=envelope.raw_text,
                source_id=envelope.source_id,
                source_type=envelope.source_type.value,
                observed_at=envelope.observed_at.isoformat(),
                published_at=(
                    envelope.published_at.isoformat() if envelope.published_at else None
                ),
                received_at=envelope.received_at.isoformat(),
                cutoff_at=cutoff.isoformat(),
                content_hash=envelope.content_hash,
            )
        ]
        snapshot_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        snapshot_id = f"snap_{snapshot_hash[:32]}"
        with self.database.session() as session:
            session.add(
                SnapshotRecord(
                    snapshot_id=snapshot_id,
                    event_id=event_id,
                    cutoff_at=cutoff,
                    snapshot_hash=snapshot_hash,
                    evidence_json=json.dumps(evidence, ensure_ascii=False),
                )
            )
        return snapshot_id, [str(evidence[0]["evidence_id"])]

    def freeze_for_run(self, run_id: str, event_id: str) -> tuple[str, list[str]]:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            if run.snapshot_id:
                snapshot = session.get(SnapshotRecord, run.snapshot_id)
                if not snapshot:
                    raise KeyError(run.snapshot_id)
                evidence = json.loads(snapshot.evidence_json)
                return snapshot.snapshot_id, [str(item["evidence_id"]) for item in evidence]
            observation = (
                session.query(ObservationRecord)
                .filter_by(event_id=event_id)
                .order_by(ObservationRecord.received_at.asc())
                .first()
            )
            if not observation:
                raise KeyError(event_id)
            if observation.observed_at > observation.received_at or (
                observation.published_at is not None
                and observation.published_at > observation.received_at
            ):
                raise ValueError("pit_future_information")
            evidence = [
                _evidence_item(
                    text=observation.text,
                    source_id=observation.source_id,
                    source_type=observation.source_type,
                    observed_at=observation.observed_at.isoformat(),
                    published_at=(
                        observation.published_at.isoformat()
                        if observation.published_at
                        else None
                    ),
                    received_at=observation.received_at.isoformat(),
                    cutoff_at=observation.received_at.isoformat(),
                    content_hash=observation.content_hash,
                )
            ]
            snapshot_hash = hashlib.sha256(
                json.dumps(evidence, sort_keys=True).encode()
            ).hexdigest()
            snapshot_id = f"snap_{snapshot_hash[:32]}"
            session.add(
                SnapshotRecord(
                    snapshot_id=snapshot_id,
                    event_id=event_id,
                    cutoff_at=observation.received_at,
                    snapshot_hash=snapshot_hash,
                    evidence_json=json.dumps(evidence, ensure_ascii=False),
                )
            )
            run.snapshot_id = snapshot_id
            run.updated_at = utcnow()
            return snapshot_id, [str(evidence[0]["evidence_id"])]


def _evidence_item(
    *,
    text: str,
    source_id: str,
    source_type: str,
    observed_at: str,
    published_at: str | None,
    received_at: str,
    cutoff_at: str,
    content_hash: str,
) -> dict[str, str | None]:
    semantic = {
        "text": text,
        "source_id": source_id,
        "source_type": source_type,
        "observed_at": observed_at,
        "published_at": published_at,
        "received_at": received_at,
        "cutoff_at": cutoff_at,
        "content_hash": content_hash,
    }
    digest = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"evidence_id": f"ev_{digest[:32]}", **semantic}
