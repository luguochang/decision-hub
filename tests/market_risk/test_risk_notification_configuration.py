"""Configured notification intent routing; never send external mail."""

from datetime import UTC, datetime

import pytest

from apps.hub_worker.composition import build_realtime_worker
from packages.kernel.decision_hub_kernel.application.decision_settings import (
    DecisionSettingsService,
)
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
    PerpetualResearchAdmissionDrivers,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import RiskMonitorPlanRecord
from packages.kernel.decision_hub_kernel.ports.sources import NotificationResult
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.perpetual_research_entry import (
    configured_perpetual_research_services,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_plan_followup_continuous import published
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.market_risk.test_risk_monitor import SCOPE, commit, sample
from tests.market_risk.test_risk_monitor import runtime as runtime


@pytest.mark.asyncio
async def test_email_channel_is_persisted_in_risk_outbox(runtime):
    database, _, clock = runtime
    service = RiskMonitorService(database, clock=lambda: clock[0], channel="email")
    commit(service, clock)
    with database.session() as session:
        assert session.query(OutboxRecord).one().channel == "email"

    class AcceptingEmail:
        channel = "email"

        async def deliver(self, message):
            assert message.channel == "email"
            return NotificationResult(delivered=True, provider_message_id="fake-email-accepted")

    dispatcher = NotificationDispatcher(database, {"email": AcceptingEmail()})
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    with database.session() as session:
        message = session.query(OutboxRecord).one()
        assert message.provider_accepted_at is not None
        assert message.provider_message_id == "fake-email-accepted"


def test_normal_realtime_builder_routes_email_to_registered_channel(runtime, tmp_path, monkeypatch):
    database, _, _ = runtime
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))
    monkeypatch.setenv("DECISION_HUB_RISK_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    worker = build_realtime_worker(
        database,
        runtime=FakeAgentRuntime(),
        settings=PilotSettings(
            sources_enabled=False, market_enabled=False, notification_channel="email",
            smtp_host="invalid.test", smtp_sender="sender@example.test",
            smtp_recipient="recipient@example.test",
        ),
    )
    monitor = worker.scheduler.risk_monitor
    now = datetime.now(UTC)
    monitor.clock = lambda: now
    with database.session() as session:
        plan_id = session.query(RiskMonitorPlanRecord).filter_by(scope_id=SCOPE).one().plan_id
    claim = monitor.claim(plan_id)
    assert claim is not None
    assert monitor.complete(claim, sample(now))
    with database.session() as session:
        message = session.query(OutboxRecord).one()
        assert message.channel == "email"
        assert message.channel in worker.scheduler.notifications.adapters
        assert message.provider_accepted_at is None


def test_driver_configured_channel_reaches_plan_expiry_outbox(binding_case, tmp_path):
    case, clock = binding_case, [AT]
    revision = published(case, tmp_path / "publication", clock)
    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    driver = PerpetualResearchAdmissionDrivers(
        case["db"], configured.entry, now=lambda: clock[0], channel="email"
    )
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
    driver.plan.save(OWNER, dict(
        schema_version="perpetual-plan-watch-command.v1", request_id="email-watch",
        partition=case["settings"].partition.model_dump(mode="json"), horizon="4h",
        plan_id=revision.plan_id,
        expected_revision_ref=dict(ref=revision.revision_id, hash=revision.payload_hash),
        expected_state_version=0,
        expected_settings_revision=dict(
            ref=saved.revision.revision_id, hash=saved.revision.payload_hash
        ),
        expected_settings_state_version=saved.state_version,
        operation="watch", mode="remind_only",
    ))
    clock[0] = revision.valid_until
    driver.discover()
    driver.discover()
    with case["db"].session() as session:
        message = session.query(OutboxRecord).filter(
            OutboxRecord.dedupe_key.startswith("perpetual-plan-watch:")
        ).one()
        assert message.channel == "email"
        assert message.sent_at is None
    assert driver.plan.condition_monitor.channel == "email"
