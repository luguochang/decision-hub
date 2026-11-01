from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationEnvelope,
    NotificationMessage,
    NotificationPort,
    decode_notification_envelope,
    verify_notification_envelope,
)


def _utc(value: datetime) -> datetime:
    # SQLite does not preserve tzinfo; persisted timestamps are UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _writer(session: Session) -> None:
    # SQLite's short writer lock serializes selection and lease assignment.
    # Provider calls never occur in this transaction.
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _history(row: OutboxRecord, event: str, at: datetime, **fields: object) -> None:
    entries = json.loads(row.attempt_history_json or "[]")
    entries.append({"event": event, "at": _utc(at).isoformat(), **fields})
    row.attempt_history_json = json.dumps(entries, ensure_ascii=True, separators=(",", ":"))


@dataclass(frozen=True)
class NotificationClaim:
    row_id: int
    owner: str
    generation: int
    expires_at: datetime
    subject_kind: str
    subject_id: str
    artifact_id: str | None
    channel: str
    dedupe_key: str
    payload_json: str | None
    payload_hash: str | None


class OutboxService:
    """Durable notification subjects; provider acceptance is not a user receipt."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def pending(self, limit: int = 50) -> list[dict[str, object]]:
        # Preserve the report-only legacy DTO. Alerts have their own product view.
        with self.database.session() as session:
            rows = (
                session.query(OutboxRecord)
                .filter(OutboxRecord.sent_at.is_(None), OutboxRecord.subject_kind == "report")
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

    def enqueue_subject(
        self, message: NotificationEnvelope, *, session: Session | None = None
    ) -> int:
        checked = verify_notification_envelope(message)
        if len(checked.subject_id) > 128 or len(checked.dedupe_key) > 256:
            raise ValueError("notification_identity_too_long")
        if session is not None:
            return self._enqueue(session, checked)
        with self.database.session() as owned_session:
            _writer(owned_session)
            return self._enqueue(owned_session, checked)

    @staticmethod
    def _enqueue(session: Session, message: NotificationEnvelope) -> int:
        existing = (
            session.query(OutboxRecord)
            .filter(OutboxRecord.dedupe_key == message.dedupe_key)
            .first()
        )
        if existing is not None:
            original_id = existing.subject_id or existing.artifact_id
            if (
                existing.subject_kind != message.subject_kind
                or original_id != message.subject_id
                or existing.channel != message.channel
                or (
                    existing.payload_hash is not None
                    and existing.payload_hash != message.payload_hash
                )
            ):
                raise ValueError("notification_dedupe_payload_conflict")
            # A legacy report keeps its original identity and sent/failed state.
            return existing.id
        row = OutboxRecord(
            artifact_id=message.subject_id if message.subject_kind == "report" else None,
            subject_kind=message.subject_kind,
            subject_id=message.subject_id,
            channel=message.channel,
            dedupe_key=message.dedupe_key,
            payload_json=message.model_dump_json(),
            payload_hash=message.payload_hash,
        )
        session.add(row)
        session.flush()
        return row.id

    def delivery_status(self, row_id: int) -> dict[str, object] | None:
        with self.database.session() as session:
            row = session.get(OutboxRecord, row_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "status": row.dispatch_status,
                "attempts": row.attempts,
                "claim_generation": row.claim_generation,
                "provider_accepted_at": row.provider_accepted_at,
                "provider_message_id": row.provider_message_id,
                "failed_at": row.failed_at,
                "last_error_code": row.last_error_code,
                "next_attempt_at": row.next_attempt_at,
                # Product display / acknowledgement is recorded separately.
                "delivered_at": None,
                "read_at": None,
                "external_duplicate_window": True,
                "attempt_history": json.loads(row.attempt_history_json or "[]"),
            }

    def retry_failed(self, row_id: int, *, request_id: str, now: datetime | None = None) -> bool:
        if not request_id:
            raise ValueError("notification_retry_request_id_required")
        at = _utc(now or utcnow())
        with self.database.session() as session:
            _writer(session)
            row = session.get(OutboxRecord, row_id)
            if row is None:
                return False
            if any(
                item.get("event") == "manual_retry" and item.get("request_id") == request_id
                for item in json.loads(row.attempt_history_json or "[]")
            ):
                return True
            if row.sent_at is not None or row.failed_at is None:
                return False
            _history(
                row,
                "manual_retry",
                at,
                request_id=request_id,
                previous_error=row.last_error_code,
                previous_attempts=row.attempts,
            )
            row.failed_at = None
            row.next_attempt_at = at
            row.attempts = 0
            row.claim_owner = None
            row.claim_expires_at = None
            row.claim_generation += 1
            row.dispatch_status = "queued"
            return True

    def drain_local(self, limit: int = 50) -> int:
        # The synchronous legacy command shares the exact same fenced claims.
        from packages.provider_adapters.notifications.local import LocalNotificationAdapter

        export_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "exports"
        adapter = LocalNotificationAdapter(export_dir / "notifications.jsonl")
        dispatcher = NotificationDispatcher(self.database, {"local": adapter})
        accepted = 0
        for _ in range(limit):
            claim = dispatcher.claim_one(channel="local")
            if claim is None:
                break
            try:
                message = dispatcher.resolve(claim)
            except ValueError as exc:
                dispatcher.finish(claim, error_code=str(exc))
                continue
            try:
                result = adapter.deliver_sync(message)
            except Exception:
                dispatcher.finish(claim, error_code="notification_unavailable", retryable=True)
                continue
            committed = dispatcher.finish(
                claim,
                accepted=result.delivered,
                provider_message_id=result.provider_message_id,
                error_code=result.error_code,
                retryable=result.retryable,
            )
            if result.delivered and committed:
                accepted += 1
        return accepted


class NotificationDispatcher:
    """At-least-once sends with durable, expiring and generation-fenced leases.

    A provider can accept before the process crashes or the lease expires. A later
    retry can then send twice; no external exactly-once guarantee is implied.
    """

    def __init__(
        self,
        database: Database,
        adapters: dict[str, NotificationPort],
        *,
        max_attempts: int = 3,
        clock: Callable[[], datetime] = utcnow,
        owner_id: str | None = None,
        lease_seconds: int = 60,
    ) -> None:
        if max_attempts < 1 or lease_seconds < 1:
            raise ValueError("notification_dispatch_limits_invalid")
        self.database = database
        self.adapters = adapters
        self.max_attempts = max_attempts
        self.clock = clock
        self.owner_id = owner_id or f"notify-{uuid4().hex}"
        self.lease_seconds = lease_seconds

    def claim_one(self, *, channel: str | None = None) -> NotificationClaim | None:
        at = _utc(self.clock())
        with self.database.session() as session:
            _writer(session)
            filters = [
                OutboxRecord.sent_at.is_(None),
                OutboxRecord.failed_at.is_(None),
                or_(OutboxRecord.claim_expires_at.is_(None), OutboxRecord.claim_expires_at <= at),
            ]
            if channel is not None:
                filters.append(OutboxRecord.channel == channel)
            exhausted = (
                session.query(OutboxRecord)
                .filter(*filters, OutboxRecord.attempts >= self.max_attempts)
                .all()
            )
            for row in exhausted:
                error = (
                    "notification_outcome_unknown"
                    if row.claim_owner
                    else "notification_attempts_exhausted"
                )
                row.failed_at = at
                row.last_error_code = error
                row.dispatch_status = "failed"
                row.claim_owner = None
                row.claim_expires_at = None
                row.next_attempt_at = None
                _history(row, "exhausted", at, error_code=error)
            row = (
                session.query(OutboxRecord)
                .filter(
                    *filters,
                    OutboxRecord.attempts < self.max_attempts,
                    or_(OutboxRecord.next_attempt_at.is_(None), OutboxRecord.next_attempt_at <= at),
                )
                .order_by(OutboxRecord.id.asc())
                .first()
            )
            if row is None:
                return None
            row.claim_generation += 1
            row.claim_owner = self.owner_id
            row.claim_expires_at = at + timedelta(seconds=self.lease_seconds)
            row.attempts += 1
            row.dispatch_status = "claimed"
            _history(row, "claimed", at, generation=row.claim_generation, attempt=row.attempts)
            session.flush()
            return NotificationClaim(
                row_id=row.id,
                owner=self.owner_id,
                generation=row.claim_generation,
                expires_at=_utc(row.claim_expires_at),
                subject_kind=row.subject_kind,
                subject_id=row.subject_id or row.artifact_id or "",
                artifact_id=row.artifact_id,
                channel=row.channel,
                dedupe_key=row.dedupe_key,
                payload_json=row.payload_json,
                payload_hash=row.payload_hash,
            )

    def resolve(self, claim: NotificationClaim) -> NotificationMessage | NotificationEnvelope:
        if claim.payload_json is not None:
            try:
                parsed = decode_notification_envelope(claim.payload_json)
            except ValueError:
                raise ValueError("notification_payload_invalid") from None
            message = verify_notification_envelope(parsed)
            if (
                message.subject_kind != claim.subject_kind
                or message.subject_id != claim.subject_id
                or message.channel != claim.channel
                or message.dedupe_key != claim.dedupe_key
                or message.payload_hash != claim.payload_hash
            ):
                raise ValueError("notification_payload_binding_invalid")
            return message
        if claim.subject_kind != "report" or claim.artifact_id is None:
            raise ValueError("notification_payload_missing")
        artifact = self.database.get_artifact_view(claim.artifact_id)
        if artifact is None:
            raise ValueError("notification_artifact_missing")
        return NotificationMessage(
            artifact_id=claim.artifact_id,
            channel=claim.channel,
            dedupe_key=claim.dedupe_key,
            subject=artifact.headline,
            body=f"Gate: {artifact.gate_status.value}\n\n{artifact.summary}",
            created_at=artifact.created_at,
        )

    def finish(
        self,
        claim: NotificationClaim,
        *,
        accepted: bool = False,
        provider_message_id: str | None = None,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> bool:
        at = _utc(self.clock())
        with self.database.session() as session:
            _writer(session)
            row = session.get(OutboxRecord, claim.row_id)
            if (
                row is None
                or row.sent_at is not None
                or row.failed_at is not None
                or row.claim_owner != claim.owner
                or row.claim_generation != claim.generation
                or row.claim_expires_at is None
                or _utc(row.claim_expires_at) <= at
            ):
                return False
            if accepted:
                row.sent_at = at
                row.provider_accepted_at = at
                row.provider_message_id = provider_message_id
                row.next_attempt_at = None
                row.last_error_code = None
                row.dispatch_status = "provider_accepted"
                _history(
                    row,
                    "provider_accepted",
                    at,
                    generation=claim.generation,
                    provider_message_id=provider_message_id,
                )
            else:
                error = error_code or "notification_unavailable"
                row.last_error_code = error
                terminal = not retryable or row.attempts >= self.max_attempts
                row.failed_at = at if terminal else None
                row.next_attempt_at = (
                    None
                    if terminal
                    else at + timedelta(seconds=min(30 * (2 ** (row.attempts - 1)), 900))
                )
                row.dispatch_status = "failed" if terminal else "retry_wait"
                _history(
                    row,
                    "failed" if terminal else "retry_wait",
                    at,
                    generation=claim.generation,
                    error_code=error,
                )
            row.claim_owner = None
            row.claim_expires_at = None
            return True

    async def dispatch_once(self, limit: int = 50) -> int:
        accepted = 0
        # Acquire just before delivery, so a batch does not age waiting leases.
        for _ in range(limit):
            claim = self.claim_one()
            if claim is None:
                break
            adapter = self.adapters.get(claim.channel)
            if adapter is None:
                self.finish(claim, error_code="notification_adapter_missing")
                continue
            try:
                message = self.resolve(claim)
            except ValueError as exc:
                self.finish(claim, error_code=str(exc))
                continue
            try:
                # No SQLAlchemy session or DB transaction spans this await.
                result = await adapter.deliver(message)
            except Exception:
                self.finish(claim, error_code="notification_unavailable", retryable=True)
                continue
            committed = self.finish(
                claim,
                accepted=result.delivered,
                provider_message_id=result.provider_message_id,
                error_code=result.error_code,
                retryable=result.retryable,
            )
            if result.delivered and committed:
                accepted += 1
        return accepted
