"""Actual SQLite originals/outbox/common admission; financial packet is synthetic.
No native/model/Graph is executed or claimed by this gate.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER, factory_for
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.decision_product.test_perpetual_research_entry import BASE, client_for


@pytest.mark.asyncio
async def test_exact_revision_expiry_signed_consent_outbox_common_queue_and_original(
    binding_case, tmp_path
):
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
        read_plan_review_source_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
        read_perpetual_research_materials_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
        PerpetualResearchAdmissionDrivers,
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord, RunRecord
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchRequestRecord,
        RiskResearchTriggerRecord,
    )
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )
    from tests.decision_product.test_perpetual_action_publication import lawful_packet_for
    from tests.decision_product.test_perpetual_advice_query import persisted_publication
    from tests.decision_product.test_perpetual_research_schedule import NextSlotCloses

    case = binding_case
    factory, _ = factory_for(case, tmp_path / "old")
    request = factory.build(case["event_id"], case["run_id"])
    with case["db"].session() as session:
        actual = read_perpetual_research_materials_in_session(session, request)

    def packet(req, materials):
        return lawful_packet_for(req, {**materials, "review_originals": actual["review_originals"]})

    with patch(
        "tests.decision_product.test_perpetual_action_publication.lawful_packet_for", packet
    ):
        derived = persisted_publication(case)
    revision = derived.branches[0].revision
    clock = [AT + timedelta(seconds=40)]
    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    collector = NextSlotCloses(case, clock)
    configured.entry.collect_window = collector
    service = PerpetualPlanFollowupService(case["db"], configured.entry, now=lambda: clock[0])
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
    command = dict(
        schema_version="perpetual-plan-watch-command.v1",
        request_id="watch-exact",
        partition=case["settings"].partition.model_dump(mode="json"),
        horizon="4h",
        plan_id=revision.plan_id,
        expected_revision_ref=dict(ref=revision.revision_id, hash=revision.payload_hash),
        expected_state_version=0,
        expected_settings_revision=dict(
            ref=saved.revision.revision_id, hash=saved.revision.payload_hash
        ),
        expected_settings_state_version=saved.state_version,
        operation="watch",
        mode="remind_and_research",
    )
    client, _, _ = client_for(case, clock=lambda: clock[0])
    path = BASE + "/plans/watches/commands"
    reply = client.post(path, json=command, headers={"Idempotency-Key": command["request_id"]})
    assert reply.status_code == 200, reply.text
    receipt = reply.json()
    assert receipt["plan_revision_ref"] == command["expected_revision_ref"]
    assert (
        client.post(path, json=command, headers={"Idempotency-Key": command["request_id"]}).json()
        == receipt
    )
    assert client.get(path + "/" + command["request_id"]).json() == receipt
    assert collector.calls == 0
    with pytest.raises(ValueError, match="settings_changed|revision_mismatch"):
        service.save("foreign-owner", {**command, "request_id": "foreign"})
    with pytest.raises(ValueError, match="state_conflict"):
        service.save(OWNER, {**command, "request_id": "stale"})
    csrf = client.headers.pop("X-Settings-CSRF")
    assert (
        client.post(
            path, json={**command, "request_id": "no-csrf"}, headers={"Idempotency-Key": "no-csrf"}
        ).status_code
        == 403
    )
    client.headers["X-Settings-CSRF"] = csrf
    original_revision = revision.model_dump_json()
    clock[0] = revision.valid_until
    driver = PerpetualResearchAdmissionDrivers(case["db"], configured.entry, now=lambda: clock[0])
    driver.discover()
    driver.discover()
    with case["db"].session() as session:
        assert session.scalar(select(func.count()).select_from(OutboxRecord).where(OutboxRecord.subject_kind == "action_revision", OutboxRecord.dedupe_key.startswith("perpetual-plan-watch:"))) == 1
        assert session.scalar(select(func.count()).select_from(RiskResearchRequestRecord)) == 1
        queued = session.scalar(select(RiskResearchRequestRecord))
        source = read_plan_review_source_in_session(
            session,
            queued.request_id,
            None,
            None,
            at=clock[0],
            require_active=False,
            original_only=True,
        )
        assert source["source"]["plan_revision_ref"] == command["expected_revision_ref"]
    assert service.current(OWNER, revision.plan_id, command["expected_revision_ref"])["expiry"][
        "outbox_id"
    ]
    legacy = RiskResearchAdmissionService(case["db"], enabled=True, clock=lambda: clock[0])
    assert legacy._claim() is None
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    assert engine._claim() is None, "not due before the full hour"
    clock[0] = __import__("datetime").datetime.fromisoformat(
        receipt["auto_review_slot"].replace("Z", "+00:00")
    ) + timedelta(seconds=10)
    assert await engine.process_due(limit=1) == 1
    with case["db"].session() as session:
        queued = session.get(RiskResearchRequestRecord, queued.request_id)
        assert queued.status == "queued"
        new_run = session.get(RunRecord, queued.run_id)
        assert new_run.admission_origin == "automatic"
        original, _ = read_perpetual_admission_identity_in_session(session, new_run.run_id)
        assert original["automatic_source"]["source"] == source["source"]
        assert original["valid_until"] == source["source"]["deadline_at"]
        assert original["runtime_deadline_at"] != original["valid_until"]
        from packages.kernel.decision_hub_kernel.persistence.action_repository import (
            ActionRepository,
        )

        assert (
            ActionRepository(session).get_revision(revision.revision_id).model_dump_json()
            == original_revision
        )
        assert session.scalar(select(func.count()).select_from(OutboxRecord).where(OutboxRecord.subject_kind == "action_revision", OutboxRecord.dedupe_key.startswith("perpetual-plan-watch:"))) == 1
        assert session.scalar(select(func.count()).select_from(RiskResearchTriggerRecord)) == 1
    assert collector.calls == 1
    assert await engine.process_due(limit=1) == 0
    newer = {**case, "run_id": new_run.run_id, "event_id": new_run.event_id}
    fresh, binding = factory_for(newer, tmp_path / "new")
    fresh.clock = lambda: clock[0]
    binding.now = lambda: clock[0]
    new_request = fresh.build(new_run.event_id, new_run.run_id)
    with case["db"].session() as session:
        materials = read_perpetual_research_materials_in_session(session, new_request)
    motive = materials["policies"]["plan_review_motive"]
    assert motive["source"]["source"]["plan_revision_ref"] == command["expected_revision_ref"]
    assert motive["fresh_market_evidence"] is motive["execution_authorized"] is False
    policies = next(
        row for row in materials["review_originals"]["originals"] if row["role"] == "policies"
    )
    assert json.loads(policies["body_json"])["plan_review_motive"] == motive
    pause = {
        **command,
        "request_id": "pause-watch",
        "operation": "pause",
        "expected_state_version": 1,
    }
    assert service.save(OWNER, pause).state == "paused"
    # Consent can be paused after queueing without changing frozen historical authority.
    with case["db"].session() as session:
        proof = read_plan_review_source_in_session(
            session,
            queued.request_id,
            None,
            None,
            at=clock[0],
            require_active=False,
            run_id=new_run.run_id,
        )
    assert proof == source

