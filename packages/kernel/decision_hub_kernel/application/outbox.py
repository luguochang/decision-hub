from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord


class OutboxService:
    """Read-only local outbox boundary; sending is a future replaceable adapter."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def pending(self, limit: int = 50) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = (
                session.query(OutboxRecord)
                .filter(OutboxRecord.sent_at.is_(None))
                .order_by(OutboxRecord.id.asc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "artifact_id": row.artifact_id,
                    "channel": row.channel,
                    "dedupe_key": row.dedupe_key,
                    "attempts": row.attempts,
                }
                for row in rows
            ]

    def drain_local(self, limit: int = 50) -> int:
        export_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / "notifications.jsonl"
        with self.database.session() as session:
            rows = (
                session.query(OutboxRecord)
                .filter(OutboxRecord.sent_at.is_(None), OutboxRecord.channel == "local")
                .order_by(OutboxRecord.id.asc())
                .limit(limit)
                .all()
            )
            with export_path.open("a", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(
                        json.dumps(
                            {
                                "artifact_id": row.artifact_id,
                                "channel": row.channel,
                                "dedupe_key": row.dedupe_key,
                                "delivered_at": datetime.now(UTC).isoformat(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    row.attempts += 1
                    row.sent_at = datetime.now(UTC)
            return len(rows)
