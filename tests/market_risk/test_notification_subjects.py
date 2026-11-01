from __future__ import annotations

import asyncio
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from packages.kernel.decision_hub_kernel.application.outbox import (
    NotificationDispatcher,
    OutboxService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationResult,
    make_notification_envelope,
    verify_notification_envelope,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter

AT = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def envelope(
    *,
    subject_id: str = "alert_a",
    body: str = "Price crossed the risk boundary.",
    created_at: datetime = AT,
    dedupe_key: str = "notify-alert-a",
):
    return make_notification_envelope(
        subject_kind="risk_alert",
        subject_id=subject_id,
        channel="local",
        dedupe_key=dedupe_key,
        subject="Market risk changed",
        body=body,
        created_at=created_at,
    )


def database_at(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'notifications.sqlite3'}")
    database.create_all()
    return database


def test_envelope_canonical_hash_normalizes_offset_and_rejects_tampering() -> None:
    utc = envelope()
    offset = envelope(created_at=AT.astimezone(timezone(timedelta(hours=8))))
    assert utc.payload_hash == offset.payload_hash
    assert verify_notification_envelope(offset).created_at == AT
    with pytest.raises(ValueError, match="notification_payload_hash_invalid"):
        verify_notification_envelope(utc.model_copy(update={"body": "Changed after signing"}))
    with pytest.raises(ValueError, match="notification_created_at_timezone_required"):
        envelope(created_at=AT.replace(tzinfo=None))


def test_enqueue_is_idempotent_and_subject_atomicity_uses_caller_transaction(
    tmp_path: Path,
) -> None:
    database = database_at(tmp_path)
    service = OutboxService(database)
    first = service.enqueue_subject(envelope())
    assert service.enqueue_subject(envelope()) == first
    with pytest.raises(ValueError, match="notification_dedupe_payload_conflict"):
        service.enqueue_subject(envelope(body="Changed risk"))
    with pytest.raises(RuntimeError, match="alert_rollback"):
        with database.session() as session:
            service.enqueue_subject(
                envelope(subject_id="rollback", dedupe_key="rollback"), session=session
            )
            raise RuntimeError("alert_rollback")
    with database.session() as session:
        row = session.query(OutboxRecord).one()
        assert row.id == first
        assert row.subject_kind == "risk_alert"
        assert row.artifact_id is None
        assert row.payload_hash == envelope().payload_hash
    assert service.pending() == []


def test_local_v2_records_acceptance_without_fabricating_artifact_or_user_receipt(
    tmp_path: Path,
) -> None:
    database = database_at(tmp_path)
    service = OutboxService(database)
    row_id = service.enqueue_subject(envelope())
    export = tmp_path / "export.jsonl"
    dispatcher = NotificationDispatcher(database, {"local": LocalNotificationAdapter(export)})
    assert asyncio.run(dispatcher.dispatch_once()) == 1
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    document = json.loads(export.read_text(encoding="utf-8"))
    assert document["subject_kind"] == "risk_alert"
    assert document["subject_id"] == "alert_a"
    assert "artifact_id" not in document
    assert "delivered_at" not in document and "read_at" not in document
    assert document["provider_accepted_at"]
    status = service.delivery_status(row_id)
    assert status["status"] == "provider_accepted"
    assert status["provider_message_id"] == "notify-alert-a"
    assert status["delivered_at"] is None and status["read_at"] is None


def test_concurrent_dispatchers_only_one_can_claim_each_subject(tmp_path: Path) -> None:
    database = database_at(tmp_path)
    OutboxService(database).enqueue_subject(envelope())
    barrier = Barrier(2)

    def claim(owner: str):
        # Separate engine connections emulate independent worker processes.
        other = Database(str(database.engine.url))
        dispatcher = NotificationDispatcher(other, {}, owner_id=owner, clock=lambda: AT)
        barrier.wait(timeout=5)
        result = dispatcher.claim_one()
        other.engine.dispose()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker_a", "worker_b"]))
    assert sum(item is not None for item in claims) == 1
    with database.session() as session:
        row = session.query(OutboxRecord).one()
        assert row.attempts == 1 and row.claim_generation == 1


def test_expired_owner_cannot_finish_even_without_successor_and_generation_fences(
    tmp_path: Path,
) -> None:
    database = database_at(tmp_path)
    row_id = OutboxService(database).enqueue_subject(envelope())
    clock = [AT]
    old = NotificationDispatcher(
        database, {}, owner_id="old", clock=lambda: clock[0], lease_seconds=5
    )
    fresh = NotificationDispatcher(
        database, {}, owner_id="new", clock=lambda: clock[0], lease_seconds=5
    )
    old_claim = old.claim_one()
    assert old_claim is not None and fresh.claim_one() is None
    clock[0] += timedelta(seconds=5)
    assert old.finish(old_claim, accepted=True) is False
    new_claim = fresh.claim_one()
    assert new_claim is not None and new_claim.generation == 2
    assert old.finish(old_claim, accepted=True) is False
    assert fresh.finish(new_claim, accepted=True, provider_message_id="new-accepted") is True
    assert old.finish(old_claim, error_code="late_failure") is False
    status = OutboxService(database).delivery_status(row_id)
    assert status["status"] == "provider_accepted" and status["attempts"] == 2
    assert status["provider_message_id"] == "new-accepted"


class WritingAdapter:
    channel = "local"

    def __init__(self, database: Database):
        self.database = database

    async def deliver(self, _message) -> NotificationResult:
        # A provider's independent writer would deadlock/fail if dispatch held
        # its writer transaction across the network await.
        OutboxService(self.database).enqueue_subject(
            envelope(subject_id="during-send", dedupe_key="during-send")
        )
        await asyncio.sleep(0)
        return NotificationResult(delivered=True, provider_message_id="accepted")


def test_provider_send_does_not_hold_database_transaction(tmp_path: Path) -> None:
    database = database_at(tmp_path)
    OutboxService(database).enqueue_subject(envelope())
    dispatcher = NotificationDispatcher(database, {"local": WritingAdapter(database)})
    assert asyncio.run(dispatcher.dispatch_once(limit=1)) == 1
    with database.session() as session:
        assert session.query(OutboxRecord).count() == 2


def test_failures_exhaustion_and_manual_recovery_remain_observable(tmp_path: Path) -> None:
    database = database_at(tmp_path)
    service = OutboxService(database)
    row_id = service.enqueue_subject(envelope())
    clock = [AT]
    dispatcher = NotificationDispatcher(database, {}, clock=lambda: clock[0], max_attempts=2)
    claim = dispatcher.claim_one()
    assert dispatcher.finish(claim, error_code="temporary", retryable=True)
    assert dispatcher.claim_one() is None
    assert service.delivery_status(row_id)["status"] == "retry_wait"
    clock[0] += timedelta(seconds=30)
    claim = dispatcher.claim_one()
    assert dispatcher.finish(claim, error_code="temporary", retryable=True)
    failed = service.delivery_status(row_id)
    assert failed["status"] == "failed" and failed["attempts"] == 2
    assert dispatcher.claim_one() is None
    assert service.retry_failed(row_id, request_id="retry-operation", now=clock[0])
    assert service.retry_failed(row_id, request_id="retry-operation", now=clock[0])
    restored = service.delivery_status(row_id)
    assert restored["status"] == "queued" and restored["attempts"] == 0
    assert sum(item["event"] == "manual_retry" for item in restored["attempt_history"]) == 1
    claim = dispatcher.claim_one()
    assert dispatcher.finish(claim, accepted=True)
    assert not service.retry_failed(row_id, request_id="cannot-resend-accepted", now=clock[0])


def test_crash_after_provider_acceptance_can_duplicate_but_never_duplicates_internal_subject(
    tmp_path: Path,
) -> None:
    database = database_at(tmp_path)
    service = OutboxService(database)
    row_id = service.enqueue_subject(envelope())
    export = tmp_path / "ambiguous.jsonl"
    adapter = LocalNotificationAdapter(export)
    clock = [AT]
    dispatcher = NotificationDispatcher(
        database, {"local": adapter}, clock=lambda: clock[0], lease_seconds=5
    )
    claim = dispatcher.claim_one()
    assert adapter.deliver_sync(dispatcher.resolve(claim)).delivered
    # Simulate process loss after provider acceptance, before durable finish.
    clock[0] += timedelta(seconds=5)
    assert asyncio.run(dispatcher.dispatch_once(limit=1)) == 1
    assert len(export.read_text(encoding="utf-8").splitlines()) == 2
    with database.session() as session:
        assert session.query(OutboxRecord).count() == 1
    assert service.delivery_status(row_id)["external_duplicate_window"] is True


def test_exhausted_crashed_claim_has_unknown_outcome_instead_of_false_acceptance(
    tmp_path: Path,
) -> None:
    database = database_at(tmp_path)
    service = OutboxService(database)
    row_id = service.enqueue_subject(envelope())
    clock = [AT]
    dispatcher = NotificationDispatcher(
        database, {}, clock=lambda: clock[0], lease_seconds=1, max_attempts=1
    )
    assert dispatcher.claim_one() is not None
    clock[0] += timedelta(seconds=1)
    assert dispatcher.claim_one() is None
    status = service.delivery_status(row_id)
    assert status["status"] == "failed"
    assert status["last_error_code"] == "notification_outcome_unknown"
    assert status["provider_accepted_at"] is None


def test_legacy_drain_cannot_bypass_active_claim_or_resend_finished_subject(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = database_at(tmp_path)
    service = OutboxService(database)
    service.enqueue_subject(envelope())
    dispatcher = NotificationDispatcher(database, {})
    claim = dispatcher.claim_one()
    assert service.drain_local() == 0
    assert dispatcher.finish(claim, accepted=True)
    assert service.drain_local() == 0
    assert not (tmp_path / "data" / "exports" / "notifications.jsonl").exists()


def test_invalid_payload_is_terminal_and_never_sent(tmp_path: Path) -> None:
    database = database_at(tmp_path)
    row_id = OutboxService(database).enqueue_subject(envelope())
    with database.session() as session:
        row = session.get(OutboxRecord, row_id)
        payload = json.loads(row.payload_json)
        payload["body"] = "tampered"
        row.payload_json = json.dumps(payload)
    export = tmp_path / "should-not-exist.jsonl"
    dispatcher = NotificationDispatcher(database, {"local": LocalNotificationAdapter(export)})
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    status = OutboxService(database).delivery_status(row_id)
    assert status["status"] == "failed"
    assert status["last_error_code"] == "notification_payload_hash_invalid"
    assert not export.exists()


def load_outbox_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0072_market_risk_outbox.py"
    )
    spec = importlib.util.spec_from_file_location("market_risk_outbox_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_outbox(connection) -> None:
    connection.execute(
        sa.text(
            "CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "artifact_id VARCHAR(128) NOT NULL, channel VARCHAR(64) NOT NULL, "
            "dedupe_key VARCHAR(256) NOT NULL UNIQUE, attempts INTEGER NOT NULL, "
            "sent_at DATETIME, next_attempt_at DATETIME, failed_at DATETIME, "
            "last_error_code VARCHAR(128))"
        )
    )
    connection.execute(sa.text("CREATE INDEX ix_outbox_artifact_id ON outbox (artifact_id)"))
    connection.execute(
        sa.text(
            "INSERT INTO outbox(id, artifact_id, channel, dedupe_key, attempts, sent_at) VALUES "
            "(10, 'art_old_sent', 'local', 'old-sent-key', 1, '2026-10-01 12:00:00'), "
            "(11, 'art_old_pending', 'local', 'old-pending-key', 0, NULL)"
        )
    )


def test_0072_isolated_migration_preserves_old_rows_and_guards_new_history(tmp_path: Path) -> None:
    # Directly rehearse the additive Outbox portion of 0071 -> 0072 on its
    # unchanged legacy table. The full schema chain is a separate release gate.
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    migration = load_outbox_migration()
    with engine.begin() as connection:
        legacy_outbox(connection)
        before = connection.execute(
            sa.text(
                "SELECT id,artifact_id,channel,dedupe_key,attempts,sent_at FROM outbox ORDER BY id"
            )
        ).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        after = connection.execute(
            sa.text(
                "SELECT id,artifact_id,channel,dedupe_key,attempts,sent_at FROM outbox ORDER BY id"
            )
        ).all()
        assert before == after
        rows = connection.execute(
            sa.text(
                "SELECT subject_id,dispatch_status,provider_accepted_at FROM outbox ORDER BY id"
            )
        ).all()
        assert rows == [("art_old_sent", "legacy_sent", None), ("art_old_pending", "queued", None)]
        message = envelope()
        connection.execute(
            sa.text(
                "INSERT INTO outbox(artifact_id,subject_kind,subject_id,channel,dedupe_key,"
                "attempts,payload_json,payload_hash) "
                "VALUES(NULL,'risk_alert',:subject_id,'local',:dedupe_key,0,:payload_json,:payload_hash)"
            ),
            dict(
                subject_id=message.subject_id,
                dedupe_key=message.dedupe_key,
                payload_json=message.model_dump_json(),
                payload_hash=message.payload_hash,
            ),
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(
                RuntimeError, match="notification_subject_history_downgrade_forbidden"
            ):
                migration.downgrade()
        assert connection.execute(sa.text("SELECT count(*) FROM outbox")).scalar_one() == 3
        connection.execute(sa.text("DELETE FROM outbox WHERE subject_kind='risk_alert'"))
        # New attempt history on a legacy report must not be silently discarded.
        connection.execute(
            sa.text('UPDATE outbox SET attempt_history_json=\'[{"event":"claimed"}]\' WHERE id=11')
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(
                RuntimeError, match="notification_subject_history_downgrade_forbidden"
            ):
                migration.downgrade()
        connection.execute(sa.text("UPDATE outbox SET attempt_history_json='[]' WHERE id=11"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        restored = connection.execute(
            sa.text(
                "SELECT id,artifact_id,channel,dedupe_key,attempts,sent_at FROM outbox ORDER BY id"
            )
        ).all()
        assert restored == before
        assert "subject_kind" not in {
            column["name"] for column in sa.inspect(connection).get_columns("outbox")
        }
    engine.dispose()


def test_migrated_sent_report_dedupe_keeps_original_identity_and_sent_state(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'legacy.sqlite3'}")
    migration = load_outbox_migration()
    with database.engine.begin() as connection:
        legacy_outbox(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    message = make_notification_envelope(
        subject_kind="report",
        subject_id="art_old_sent",
        channel="local",
        dedupe_key="old-sent-key",
        subject="Report",
        body="Report details",
        created_at=AT,
    )
    assert OutboxService(database).enqueue_subject(message) == 10
    with database.session() as session:
        row = session.get(OutboxRecord, 10)
        assert row.artifact_id == "art_old_sent" and row.subject_id == "art_old_sent"
        assert row.sent_at == datetime(2026, 10, 1, 12, 0)
        assert row.attempts == 1 and row.payload_json is None
        assert row.dispatch_status == "legacy_sent"
