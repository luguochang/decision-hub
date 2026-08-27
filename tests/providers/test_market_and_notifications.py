from __future__ import annotations

import asyncio
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from pydantic import SecretStr

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationMessage,
    NotificationResult,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.notifications.smtp import SMTPConfig, SMTPNotificationAdapter
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_notification_dispatch_is_deduplicated_and_failure_does_not_change_artifact(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'notify.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text="Powell says higher for longer.")))
    export = tmp_path / "notifications.jsonl"
    dispatcher = NotificationDispatcher(database, {"local": LocalNotificationAdapter(export)})

    assert asyncio.run(dispatcher.dispatch_once()) == 1
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert export.read_text().count("artifact_id") == 1


class RetryableNotificationAdapter:
    channel = "local"

    async def deliver(self, message: NotificationMessage) -> NotificationResult:
        assert message.channel == self.channel
        return NotificationResult(
            delivered=False,
            retryable=True,
            error_code="notification_unavailable",
        )


def test_notification_failure_is_observable_and_does_not_reanalyze(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'notify-failure.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text="Powell says higher for longer.")))
    dispatcher = NotificationDispatcher(database, {"local": RetryableNotificationAdapter()})

    assert asyncio.run(dispatcher.dispatch_once()) == 0
    with database.session() as session:
        outbox = session.query(OutboxRecord).one()
        assert outbox.attempts == 1
        assert outbox.sent_at is None
        assert outbox.next_attempt_at is not None
        assert outbox.last_error_code == "notification_unavailable"
    assert len(database.latest_runs()) == 1


class PermanentNotificationAdapter:
    channel = "local"

    async def deliver(self, message: NotificationMessage) -> NotificationResult:
        assert message.channel == self.channel
        return NotificationResult(
            delivered=False,
            retryable=False,
            error_code="notification_rejected",
        )


def test_notification_permanent_failure_is_terminal_and_not_due_again(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'notify-terminal.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text="Powell says higher for longer.")))
    now = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    dispatcher = NotificationDispatcher(
        database,
        {"local": PermanentNotificationAdapter()},
        clock=lambda: now,
    )

    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    with database.session() as session:
        outbox = session.query(OutboxRecord).one()
        assert outbox.attempts == 1
        assert outbox.failed_at == now.replace(tzinfo=None)
        assert outbox.next_attempt_at is None
        assert outbox.last_error_code == "notification_rejected"


def test_retryable_notification_waits_until_due_then_reaches_terminal_failure(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'notify-retry-limit.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text="Powell says higher for longer.")))
    clock = [datetime(2026, 8, 27, 1, 0, tzinfo=UTC)]
    dispatcher = NotificationDispatcher(
        database,
        {"local": RetryableNotificationAdapter()},
        max_attempts=2,
        clock=lambda: clock[0],
    )

    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    clock[0] += timedelta(seconds=30)
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    with database.session() as session:
        outbox = session.query(OutboxRecord).one()
        assert outbox.attempts == 2
        assert outbox.failed_at == clock[0].replace(tzinfo=None)
        assert outbox.next_attempt_at is None


def test_smtp_adapter_maps_temporary_failure_without_exposing_password() -> None:
    def sender(config: SMTPConfig, _message: EmailMessage) -> str | None:
        assert repr(config.password) == "SecretStr('**********')"
        raise smtplib.SMTPResponseException(451, b"temporary")

    adapter = SMTPNotificationAdapter(
        SMTPConfig(
            host="smtp.example.test",
            sender="hub@example.test",
            recipient="owner@example.test",
            username="hub",
            password=SecretStr("secret-fixture"),
        ),
        sender=sender,
    )
    result = asyncio.run(
        adapter.deliver(
            NotificationMessage(
                artifact_id="art_fixture",
                channel="email",
                dedupe_key="fixture-email",
                subject="Decision Hub fixture",
                body="Fixture body",
                created_at=datetime.now(UTC),
            )
        )
    )
    assert result.delivered is False
    assert result.retryable is True
    assert result.error_code == "notification_rate_limited"
