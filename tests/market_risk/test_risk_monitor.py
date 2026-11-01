from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.outbox import OutboxService
from packages.kernel.decision_hub_kernel.application.risk_monitor import (
    RiskMonitorService,
    content_hash,
    evaluate_condition,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    RiskAlertDeliveryReceiptRecord,
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorPlanRecord,
)
from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
    RiskComparisonV1,
    RiskConditionV1,
    RiskMetricV1,
    RiskMonitorInput,
)

SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"


def rule() -> RiskConditionV1:
    return RiskConditionV1(
        condition_id="btc-spot-move30.v1",
        scope_id=SCOPE,
        expression=RiskComparisonV1(left=RiskMetricV1(), right=Decimal("0.015")),
    )


def sample(now: datetime, value: str | None = "-0.016", **updates) -> RiskMonitorInput:
    fields = {
        "scope_id": SCOPE,
        "as_of": now,
        "data_as_of": now,
        "window_seconds": 1800,
        "close_return": Decimal(value) if value is not None else None,
        "data_state": "fresh" if value is not None else "unknown",
        "input_ref": "fixture://explicit-synthetic-input",
        "input_hash": content_hash([now.isoformat(), value]),
        "manifest_hash": "a" * 64,
    }
    return RiskMonitorInput.model_validate({**fields, **updates})


@pytest.fixture
def runtime(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'risk.sqlite3'}")
    database.create_all()
    clock = [datetime(2026, 10, 8, tzinfo=UTC)]
    service = RiskMonitorService(database, clock=lambda: clock[0])
    yield database, service, clock
    database.engine.dispose()


def commit(service, clock, value="-0.016", **updates):
    claim = service.claim(service.ensure_plan(rule()))
    assert claim is not None
    assert service.complete(claim, sample(clock[0], value, **updates))


def test_condition_does_not_use_unknown_stale_future_or_wrong_basis():
    now = datetime(2026, 10, 8, tzinfo=UTC)
    assert evaluate_condition(rule(), sample(now), now=now)[0] == "true"
    assert evaluate_condition(rule(), sample(now, "0.01"), now=now)[0] == "false"
    assert evaluate_condition(rule(), sample(now, None), now=now)[0] == "unknown"
    assert (
        evaluate_condition(rule(), sample(now, data_as_of=now - timedelta(seconds=181)), now=now)[0]
        == "unknown"
    )
    assert evaluate_condition(rule(), sample(now, scope_id="other"), now=now)[0] == "error"
    assert evaluate_condition(rule(), sample(now, window_seconds=900), now=now)[0] == "error"
    assert (
        evaluate_condition(rule(), sample(now, data_as_of=now + timedelta(seconds=1)), now=now)[0]
        == "error"
    )


def test_episode_is_atomic_persistent_and_recovery_needs_distinct_closed_bars(runtime):
    database, service, clock = runtime
    commit(service, clock)
    clock[0] += timedelta(seconds=60)
    commit(service, clock)
    clock[0] += timedelta(seconds=60)
    commit(service, clock, None)
    with database.session() as session:
        assert session.query(RiskAlertRecord).count() == session.query(OutboxRecord).count() == 1
        assert session.query(RiskMonitorPlanRecord).one().active_episode_id is not None
    clock[0] += timedelta(seconds=60)
    first_false_bar = clock[0]
    commit(service, clock, "0.001")
    clock[0] += timedelta(seconds=60)
    commit(service, clock, "0.001", data_as_of=first_false_bar)
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is None
    clock[0] += timedelta(seconds=60)
    commit(service, clock, "0.001")
    reopened = RiskMonitorService(database, clock=lambda: clock[0])
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is not None
        assert session.query(RiskMonitorPlanRecord).one().active_episode_id is None
        assert session.query(RiskConditionEvaluationRecord).count() == 6
    clock[0] += timedelta(seconds=60)
    commit(reopened, clock)
    with database.session() as session:
        assert session.query(RiskAlertRecord).count() == 2


def test_two_instances_claim_only_once_and_old_generation_cannot_submit(runtime):
    database, service, clock = runtime
    plan = service.ensure_plan(rule())
    barrier = threading.Barrier(2)

    def claim_one():
        other = RiskMonitorService(database, clock=lambda: clock[0])
        barrier.wait()
        return other.claim(plan)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_one(), range(2)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    old = winners[0]
    clock[0] += timedelta(seconds=90)
    assert not service.complete(old, sample(old.as_of))
    replacement = service.claim(plan)
    assert replacement is not None and replacement.generation > old.generation
    assert not service.complete(old, sample(old.as_of))
    assert service.complete(replacement, sample(clock[0]))
    assert not service.complete(replacement, sample(clock[0]))
    with database.session() as session:
        assert session.query(RiskConditionEvaluationRecord).count() == 1


def test_pause_invalidates_claim_and_command_is_idempotent(runtime):
    _, service, clock = runtime
    plan = service.ensure_plan(rule())
    claim = service.claim(plan)
    assert claim is not None
    first = service.command(plan, "pause", request_id="pause01")
    assert first == service.command(plan, "pause", request_id="pause01")
    assert service.claim(plan) is None
    assert not service.complete(claim, sample(clock[0]))
    with pytest.raises(ValueError, match="request_conflict"):
        service.command(plan, "resume", request_id="pause01")
    service.command(plan, "resume", request_id="resume01")
    assert service.claim(plan) is not None


def test_plan_expiry_during_work_fences_the_result(runtime):
    database, service, clock = runtime
    plan = service.ensure_plan(rule(), expires_at=clock[0] + timedelta(seconds=30))
    claim = service.claim(plan)
    assert claim is not None
    clock[0] += timedelta(seconds=30)
    assert not service.complete(claim, sample(clock[0]))
    assert service.expire_due() == 1
    assert service.expire_due() == 0
    with database.session() as session:
        assert session.query(RiskMonitorPlanRecord).one().state == "expired"
        assert session.query(RiskAlertRecord).count() == 0


def test_outbox_failure_rolls_back_alert_and_evaluation(runtime, monkeypatch):
    database, service, clock = runtime
    claim = service.claim(service.ensure_plan(rule()))
    assert claim is not None

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected_outbox_failure")

    with monkeypatch.context() as patch:
        patch.setattr(OutboxService, "enqueue_subject", fail)
        with pytest.raises(RuntimeError, match="injected_outbox_failure"):
            service.complete(claim, sample(clock[0]))
    with database.session() as session:
        assert session.query(RiskAlertRecord).count() == session.query(OutboxRecord).count() == 0
        assert session.query(RiskConditionEvaluationRecord).count() == 0
    assert service.complete(claim, sample(clock[0]))


def test_display_and_ack_use_server_time_and_independent_idempotent_receipts(runtime):
    database, service, clock = runtime
    commit(service, clock)
    with database.session() as session:
        alert_id = session.query(RiskAlertRecord).one().alert_id
    ancient_client = clock[0] - timedelta(days=30)
    display = service.record_receipt(
        alert_id, "display", request_id="seen", client_time=ancient_client
    )
    assert display["received_at"] == clock[0].isoformat()
    clock[0] += timedelta(seconds=5)
    assert display == service.record_receipt(alert_id, "display", request_id="seen")
    service.record_receipt(alert_id, "ack", request_id="seen")
    with database.session() as session:
        assert session.query(RiskAlertDeliveryReceiptRecord).count() == 2
        assert session.query(OutboxRecord).one().sent_at is None


def test_snapshot_failure_is_persisted_and_can_recover_without_erasing_active_risk(runtime):
    database, service, clock = runtime
    commit(service, clock)

    class Broken:
        async def snapshot(self, *_args, **_kwargs):
            raise RuntimeError("private_provider_error")

    worker = RiskMonitorService(database, Broken(), clock=lambda: clock[0])
    for _ in range(2):
        clock[0] += timedelta(seconds=60)
        assert asyncio.run(worker.process_due()) == 1
    with database.session() as session:
        plan = session.query(RiskMonitorPlanRecord).one()
        assert plan.state == "degraded" and plan.active_episode_id is not None
        assert "private_provider_error" not in plan.last_error_code
    clock[0] += timedelta(seconds=60)
    commit(worker, clock)
    with database.session() as session:
        assert session.query(RiskMonitorPlanRecord).one().state == "active"


def test_regressed_bar_remains_unknown_and_does_not_clear_alert(runtime):
    database, service, clock = runtime
    commit(service, clock)
    original = clock[0]
    clock[0] += timedelta(seconds=60)
    commit(service, clock, "0.001", data_as_of=original - timedelta(seconds=60))
    with database.session() as session:
        plan = session.query(RiskMonitorPlanRecord).one()
        assert plan.last_truth == "unknown" and plan.active_episode_id is not None
        assert plan.last_error_code == "risk_input_time_regressed"


def test_component_monitor_binding_matches_actual_evaluation_and_no_replay(runtime):
    import json

    from apps.hub_worker.market_component_bootstrap import build_market_components
    from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
        MarketComponentAuditRecord,
    )

    database, service, clock = runtime
    service.component_consumer = build_market_components(database)
    claim = service.claim(service.ensure_plan(rule()))
    assert claim is not None
    assert service.complete(claim, sample(clock[0]))
    assert not service.complete(claim, sample(clock[0]))
    with database.session() as session:
        evaluation = session.query(RiskConditionEvaluationRecord).one()
        audit = session.query(MarketComponentAuditRecord).one()
        assert audit.request_id == (
            f"component-consume:monitor:{evaluation.evaluation_id}:{claim.generation}"
        )
        assert json.loads(audit.payload_json)["output"]["truth"] == evaluation.truth_state == "true"
        assert session.query(OutboxRecord).count() == 1


def test_component_monitor_forged_audit_rolls_back_evaluation_and_notification(runtime):
    from packages.kernel.decision_hub_kernel.application.market_component_functions import (
        evaluate_market_rule_component,
    )

    database, service, clock = runtime

    def forged(_session, **kwargs):
        return evaluate_market_rule_component(kwargs["payload"]), {}

    service.component_consumer = forged
    claim = service.claim(service.ensure_plan(rule()))
    assert claim is not None
    with pytest.raises(ValueError, match="component_consumption_audit_reference_missing"):
        service.complete(claim, sample(clock[0]))
    with database.session() as session:
        assert session.query(RiskConditionEvaluationRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0
        assert session.query(RiskMonitorPlanRecord).one().last_evaluated_at is None
