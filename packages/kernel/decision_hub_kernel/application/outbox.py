from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationMessage,
    NotificationPort,
)


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


class NotificationDispatcher:
    """Dispatches committed outbox rows through replaceable channel adapters."""

    def __init__(
        self,
        database: Database,
        adapters: dict[str, NotificationPort],
        *,
        max_attempts: int = 3,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.adapters = adapters
        self.max_attempts = max_attempts
        self.clock = clock

    async def dispatch_once(self, limit: int = 50) -> int:
        delivered = 0
        with self.database.session() as session:
            rows = (
                session.query(OutboxRecord)
                .filter(
                    OutboxRecord.sent_at.is_(None),
                    OutboxRecord.failed_at.is_(None),
                    OutboxRecord.attempts < self.max_attempts,
            (OutboxRecord.next_attempt_at.is_(None))
            | (OutboxRecord.next_attempt_at <= self.clock()),
                )
                .order_by(OutboxRecord.id.asc())
                .limit(limit)
                .all()
            )
            pending = [
                (row.id, row.artifact_id, row.channel, row.dedupe_key, row.attempts) for row in rows
            ]
        for row_id, artifact_id, channel, dedupe_key, _attempts in pending:
            adapter = self.adapters.get(channel)
            if adapter is None:
                self._mark_failed(row_id, "notification_adapter_missing")
                continue
            artifact = self.database.get_artifact_view(artifact_id)
            if artifact is None:
                self._mark_failed(row_id, "notification_artifact_missing")
                continue
            message = NotificationMessage(
                artifact_id=artifact_id,
                channel=channel,
                dedupe_key=dedupe_key,
                subject=artifact.headline,
                body=artifact.summary,
                created_at=artifact.created_at,
            )
            try:
                result = await adapter.deliver(message)
            except Exception:
                self._mark_retry(row_id, "notification_unavailable")
                continue
            if result.delivered:
                self._mark_delivered(row_id)
                delivered += 1
            else:
                if result.retryable:
                    self._mark_retry(row_id, result.error_code or "notification_unavailable")
                else:
                    self._mark_failed(row_id, result.error_code or "notification_rejected")
        return delivered

    def _mark_delivered(self, row_id: int) -> None:
        with self.database.session() as session:
            row = session.get(OutboxRecord, row_id)
            if row and row.sent_at is None:
                row.attempts += 1
                row.sent_at = self.clock()
                row.next_attempt_at = None
                row.last_error_code = None

    def _mark_retry(self, row_id: int, error_code: str) -> None:
        with self.database.session() as session:
            row = session.get(OutboxRecord, row_id)
            if row and row.sent_at is None:
                row.attempts += 1
                row.last_error_code = error_code
                if row.attempts >= self.max_attempts:
                    row.failed_at = self.clock()
                    row.next_attempt_at = None
                else:
                    row.next_attempt_at = self.clock() + timedelta(
                        seconds=min(30 * (2 ** (row.attempts - 1)), 900)
                    )

    def _mark_failed(self, row_id: int, error_code: str) -> None:
        with self.database.session() as session:
            row = session.get(OutboxRecord, row_id)
            if row and row.sent_at is None:
                row.attempts += 1
                row.last_error_code = error_code
                row.failed_at = self.clock()
                row.next_attempt_at = None
