"""Action revisions use the shared outbox without fabricating report identity."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.application.outbox import (
    NotificationDispatcher,
    OutboxService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.ports.sources import make_notification_envelope
from packages.provider_adapters.notifications.local import LocalNotificationAdapter

AT = datetime(2026, 10, 13, 4, tzinfo=UTC)


def message(
    kind="action_revision", subject_id="revision-1", transition="publish", body="条件已更新",
):
    return make_notification_envelope(
        subject_kind=kind,
        subject_id=subject_id,
        channel="local",
        dedupe_key=f"{kind}:{subject_id}:{transition}:local",
        subject="当前行动方案",
        body=body,
        created_at=AT,
    )


def setup(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'actions.sqlite3'}")
    database.create_all()
    export = tmp_path / "notification.jsonl"
    return (
        database, OutboxService(database),
        NotificationDispatcher(database, {"local": LocalNotificationAdapter(export)}), export,
    )


def test_old_subjects_are_not_resent_when_action_revision_is_published(tmp_path: Path):
    database, outbox, dispatcher, export = setup(tmp_path)
    originals = [message("report", "report-1"), message("risk_alert", "risk-1")]
    old_ids = [outbox.enqueue_subject(item) for item in originals]
    assert asyncio.run(dispatcher.dispatch_once()) == 2
    old_states = [outbox.delivery_status(row_id) for row_id in old_ids]
    new_id = outbox.enqueue_subject(message())
    assert outbox.enqueue_subject(message()) == new_id
    assert [outbox.enqueue_subject(item) for item in originals] == old_ids
    assert asyncio.run(dispatcher.dispatch_once()) == 1
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert [outbox.delivery_status(row_id) for row_id in old_ids] == old_states
    documents = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    assert [row["subject_kind"] for row in documents] == ["report", "risk_alert", "action_revision"]
    action = documents[-1]
    assert action["subject_id"] == "revision-1"
    assert not {"artifact_id", "delivered_at", "read_at"} & action.keys()
    assert outbox.delivery_status(new_id)["delivered_at"] is None
    with database.session() as session:
        assert session.get(OutboxRecord, new_id).artifact_id is None


def test_revision_and_transition_identity_and_atomic_rollback(tmp_path: Path):
    database, outbox, _, _ = setup(tmp_path)
    original = outbox.enqueue_subject(message())
    triggered = outbox.enqueue_subject(message(transition="trigger"))
    revised = outbox.enqueue_subject(message(subject_id="revision-2"))
    assert len({original, triggered, revised}) == 3
    with pytest.raises(ValueError, match="notification_dedupe_payload_conflict"):
        outbox.enqueue_subject(message(body="同一命令不能偷偷更改动作"))
    with pytest.raises(RuntimeError, match="publication_failed"):
        with database.session() as session:
            outbox.enqueue_subject(message(subject_id="rollback"), session=session)
            raise RuntimeError("publication_failed")
    with database.session() as session:
        assert session.query(OutboxRecord).count() == 3


@pytest.mark.parametrize("damage,error", [
    ("binding", "notification_payload_binding_invalid"),
    ("hash", "notification_payload_hash_invalid"),
])
def test_bad_action_payload_is_failed_without_report_fallback(tmp_path: Path, damage, error):
    database, outbox, dispatcher, export = setup(tmp_path)
    row_id = outbox.enqueue_subject(message())
    with database.session() as session:
        row = session.get(OutboxRecord, row_id)
        if damage == "binding":
            row.subject_id = "different-revision"
        else:
            payload = json.loads(row.payload_json)
            payload["body"] = "未经授权修改"
            row.payload_json = json.dumps(payload)
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    status = outbox.delivery_status(row_id)
    assert status["status"] == "failed" and status["last_error_code"] == error
    assert status["provider_accepted_at"] is None
    assert not export.exists()


def test_missing_action_payload_is_rejected_at_storage_and_dispatch_boundaries(tmp_path: Path):
    database, outbox, dispatcher, export = setup(tmp_path)
    row_id = outbox.enqueue_subject(message())
    with pytest.raises(IntegrityError, match="ck_outbox_subject_identity"):
        with database.session() as session:
            session.get(OutboxRecord, row_id).payload_json = None
    claim = dispatcher.claim_one()
    assert claim is not None and claim.payload_json is not None
    with pytest.raises(ValueError, match="notification_payload_missing"):
        dispatcher.resolve(replace(claim, payload_json=None))
    assert not export.exists()
