from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from packages.kernel.decision_hub_kernel.application.action_plan import ActionPlanService
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionPlanHeadRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate

from .action_fixtures import ASOF, make_opportunity, seed_plan


@pytest.fixture
def context(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'commands.sqlite3'}")
    database.create_all()
    with database.session() as session:
        begin_immediate(session)
        _, _, revision, head = seed_plan(session)
    clock = [ASOF + timedelta(minutes=2)]
    service = ActionPlanService(database, clock=lambda: clock[0])
    args = {
        "owner_id": "owner-a", "request_id": "request-a", "plan_id": head.plan_id,
        "expected_revision_id": revision.revision_id, "expected_state_version": 1,
        "operation": "pause",
    }
    yield database, service, args, clock
    database.engine.dispose()


def test_retry_returns_original_receipt_after_later_head_change_and_expiry(context):
    database, service, args, clock = context
    first = service.record_command(**args)
    assert first.response["head"]["monitor_state"] == "paused"
    assert first.response["head"]["state_version"] == 2
    second = service.record_command(**{
        **args, "request_id": "resume", "expected_state_version": 2, "operation": "resume",
    })
    assert second.response["head"]["state_version"] == 3
    clock[0] += timedelta(days=2)
    assert service.record_command(**args) == first
    assert service.get_command(owner_id="owner-a", request_id="request-a") == first
    with database.session() as session:
        assert ActionRepository(session).get_head(args["plan_id"]).state_version == 3
        assert session.query(ActionCommandReceiptRecord).count() == 2


def test_command_key_conflict_and_owner_scoped_readback(context):
    _, service, args, _ = context
    service.record_command(**args)
    with pytest.raises(ValueError, match="action_command_key_conflict"):
        service.record_command(**{**args, "operation": "resume"})
    assert service.get_command(owner_id="owner-b", request_id="request-a") is None
    other = service.record_command(**{
        **args, "owner_id": "owner-b", "expected_state_version": 2, "operation": "display",
    })
    assert other.owner_id == "owner-b"
    # No broker fill or paper economics is inferred from a UI event.
    assert set(other.response) == {"status", "operation", "server_time", "head"}


def test_display_and_ack_are_append_only_without_changing_head_or_first_visibility(context):
    database, service, args, clock = context
    first = service.record_command(**{**args, "operation": "display"})
    clock[0] += timedelta(minutes=1)
    second = service.record_command(**{
        **args, "request_id": "second-display", "operation": "display",
    })
    assert first.accepted_at < second.accepted_at
    assert service.get_command(owner_id=args["owner_id"], request_id=args["request_id"]) == first
    ack = service.record_command(**{**args, "request_id": "ack", "operation": "ack"})
    assert ack.response["head"]["state_version"] == 1
    with database.session() as session:
        assert ActionRepository(session).get_head(args["plan_id"]).state_version == 1
        assert session.query(ActionCommandReceiptRecord).count() == 3


@pytest.mark.parametrize("change,error", [
    ({"expected_revision_id": "old-revision"}, "action_revision_superseded"),
    ({"expected_state_version": 2}, "action_state_version_conflict"),
    ({"expected_state_version": True}, "action_command_version_invalid"),
    ({"owner_id": " "}, "action_command_identity_invalid"),
    ({"operation": "report_adoption"}, "action_command_operation_unsupported"),
    ({"payload": {"filled_at": "2026-10-13T00:00:00Z"}}, "action_command_payload_invalid"),
    ({"payload": []}, "action_command_payload_invalid"),
])
def test_invalid_command_has_no_partial_receipt_or_head_mutation(context, change, error):
    database, service, args, _ = context
    with pytest.raises(ValueError, match=error):
        service.record_command(**{**args, **change})
    with database.session() as session:
        assert session.query(ActionCommandReceiptRecord).count() == 0
        assert ActionRepository(session).get_head(args["plan_id"]).state_version == 1


def test_expired_plan_cannot_be_resumed(context):
    database, service, args, clock = context
    clock[0] = ASOF + timedelta(days=1)
    with pytest.raises(ValueError, match="action_plan_expired"):
        service.record_command(**{**args, "operation": "resume"})
    with database.session() as session:
        assert session.query(ActionCommandReceiptRecord).count() == 0


def test_receipt_failure_rolls_back_pause_and_lease_fence(context, monkeypatch):
    database, service, args, _ = context

    def fail(_self, _receipt):
        raise RuntimeError("injected_receipt_failure")

    monkeypatch.setattr(ActionRepository, "append_command_receipt", fail)
    with pytest.raises(RuntimeError, match="injected_receipt_failure"):
        service.record_command(**args)
    with database.session() as session:
        head = ActionRepository(session).get_head(args["plan_id"])
        assert head.monitor_state == "active" and head.state_version == 1
        assert head.lease_generation == 0
        assert session.query(ActionCommandReceiptRecord).count() == 0


def test_pause_fences_inflight_monitor_and_resets_confirmation_without_replaying_bar(context):
    database, service, args, clock = context
    watermark = ASOF + timedelta(minutes=1)
    with database.session() as session:
        row = session.get(ActionPlanHeadRecord, args["plan_id"])
        row.lease_owner = "old-monitor"
        row.lease_expires_at = clock[0] + timedelta(seconds=30)
        row.lease_generation = 7
        row.enter_streak = 1
        row.last_input_watermark = watermark
    receipt = service.record_command(**args)
    head = receipt.response["head"]
    assert head["lease_generation"] == 8 and head["lease_owner"] is None
    assert head["lease_expires_at"] is None and head["enter_streak"] == 0
    with database.session() as session:
        assert ActionRepository(session).get_head(args["plan_id"]).last_input_watermark == watermark


def test_resume_does_not_certify_restoration_of_unavailable_market_data(context):
    database, service, args, _ = context
    with database.session() as session:
        session.get(ActionPlanHeadRecord, args["plan_id"]).monitor_state = "degraded"
    receipt = service.record_command(**{**args, "operation": "resume"})
    assert receipt.response["head"]["monitor_state"] == "degraded"
    assert receipt.response["head"]["state_version"] == 1


def test_two_command_writers_cannot_both_use_same_expected_state(context):
    database, service, args, _ = context
    barrier = Barrier(2)

    def submit(request_id):
        barrier.wait(timeout=5)
        try:
            return service.record_command(**{**args, "request_id": request_id}).response["status"]
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["writer-a", "writer-b"]))
    assert sorted(results) == ["action_state_version_conflict", "recorded"]
    with database.session() as session:
        assert session.query(ActionCommandReceiptRecord).count() == 1


def test_opportunity_registration_retains_original_identity_and_disposition(context):
    database, service, _, _ = context
    opportunity = make_opportunity(asset="ETH", id="opportunity-eth")
    assert service.register_opportunity(opportunity) == opportunity
    with database.session() as session:
        begin_immediate(session)
        ActionRepository(session).set_opportunity_status(
            opportunity.id, expected_status="open", status="missed", reason="research_not_ready",
        )
    replay = service.register_opportunity(opportunity)
    assert replay.id == opportunity.id and replay.status == "missed"
    assert replay.registered_at == opportunity.registered_at
    assert replay.reason == "research_not_ready"
