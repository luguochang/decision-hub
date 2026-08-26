from __future__ import annotations

import hashlib
import json
import uuid

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.kernel.decision_hub_kernel.persistence.db import Database, SnapshotRecord


class SnapshotService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def freeze(self, event_id: str, envelope: TextEnvelope) -> tuple[str, list[str]]:
        cutoff = envelope.received_at
        evidence = [
            {
                "evidence_id": f"ev_{uuid.uuid4().hex}",
                "text": envelope.raw_text,
                "source_id": envelope.source_id,
                "source_type": envelope.source_type.value,
                "observed_at": envelope.observed_at.isoformat(),
                "cutoff_at": cutoff.isoformat(),
                "content_hash": envelope.content_hash,
            }
        ]
        snapshot_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        snapshot_id = f"snap_{uuid.uuid4().hex}"
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
        return snapshot_id, [evidence[0]["evidence_id"]]
