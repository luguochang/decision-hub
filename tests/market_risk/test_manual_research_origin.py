"""Private SQLite admissions only; no model, provider or formal database calls."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import (
    canonical_json,
    content_hash,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    EventRecord,
    RunRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from tests.market_risk.test_market_data import NOW
from tests.market_risk.test_risk_research_admission import prepared as prepared
from tests.market_risk.test_risk_research_admission import service


def _rows(session, model):
    return [
        tuple(getattr(row, col.name) for col in model.__table__.columns)
        for row in session.scalars(select(model).order_by(*model.__table__.primary_key.columns))
    ]


def _seed_budget(db, at, instrument="ETH-USDT"):
    with db.session() as session:
        study_id = ensure_study(
            session, question_key="spot-market-risk.v1", title="isolated quota fixture",
            market="crypto", instrument=instrument, horizon_scope="30m,24h,72h",
            identity_basis="exact_spot_monitor", now=at,
        )
        for i in range(12):
            stamp = at - timedelta(minutes=40, seconds=i)
            session.add(RunRecord(
                run_id=f"fixture-budget-{i}", event_id=f"fixture-event-{i}",
                study_id=study_id if i < 2 else None,
                strategy_version="research.v1", admission_origin="automatic",
                status="failed", error_code="fixture_previous_failure",
                runtime_version="fixture-no-runtime", created_at=stamp, updated_at=stamp,
                finished_at=stamp, cost_usd=None,
            ))
        return study_id


def _manual(bridge, instrument="ETH-USDT", key="owner-command1"):
    return bridge.stage(
        instrument, request_id=key, source_ref=f"owner-command:{key}",
        source={"owner_id": "owner"},
    )


@pytest.mark.asyncio
async def test_authenticated_manual_uses_actual_spot_admission_but_not_automatic_quota(
    prepared, monkeypatch,
):
    db, now, *_ = prepared
    from tests.market_risk.test_market_data import PublicFixture

    original_fetch = PublicFixture.fetch
    capture_errors = []

    async def diagnosed_fetch(transport, url):
        try:
            return await original_fetch(transport, url)
        except Exception as error:
            capture_errors.append(f"{type(error).__name__}:{error}")
            raise

    monkeypatch.setattr(PublicFixture, "fetch", diagnosed_fetch)
    try:
        bridge = await asyncio.to_thread(service, prepared)
    except AssertionError as error:
        raise AssertionError(f"synthetic capture errors: {capture_errors}") from error
    study_id = _seed_budget(db, now[0])
    scope = spot_scope("ETH-USDT")
    with db.session() as session:
        session.add(RiskResearchRequestRecord(
            request_id="fixture-prior-failed-owner-request", scope_id=scope.scope_id,
            instrument="ETH-USDT", status="failed", created_at=now[0], updated_at=now[0],
            available_at=now[0], attempts=3, lease_generation=3,
            error_code="research_admission_budget_exhausted",
        ))
    with db.session() as session:
        old_runs = _rows(session, RunRecord)
        old_failed = _rows(session, RiskResearchRequestRecord)
        guard = research_admission_guard_in_session(session, study_id=study_id, now=now[0])
        assert guard.reason_code == "research_admission_budget_exhausted"
        assert guard.details["daily_count"] == 12 and guard.details["study_hour_count"] == 2
    request = _manual(bridge)
    with db.session() as session:
        trigger_originals = _rows(session, RiskResearchTriggerRecord)
    claim = bridge._claim()
    assert claim[0] == request["request_id"]
    await bridge.prepare_scope("ETH-USDT")
    assert bridge._admit(claim[0], claim[2]) is True
    assert bridge._admit(claim[0], claim[2]) is False
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, request["request_id"])
        run = session.get(RunRecord, row.run_id)
        assert run.admission_origin == "manual" and run.priority == 100
        assert run.study_id == study_id and run.cost_usd is None
        assert session.get(StudyRecord, study_id).instrument == "ETH-USDT"
        from packages.kernel.decision_hub_kernel.application.research_run_scope import (
            read_run_spot_admission_in_session,
        )
        proof = read_run_spot_admission_in_session(session, run.run_id)
        assert proof.scope.instrument == "ETH-USDT"
        retained_runs = [r for r in _rows(session, RunRecord) if r[0].startswith("fixture-budget-")]
        assert retained_runs == old_runs
        failed = session.get(RiskResearchRequestRecord, "fixture-prior-failed-owner-request")
        assert tuple(getattr(failed, col.name) for col in failed.__table__.columns) == old_failed[0]
        assert _rows(session, RiskResearchTriggerRecord) == trigger_originals
    # Same-asset automatic/manual triggering still waits the existing 30 minute window.
    next_request = _manual(bridge, key="owner-command2")
    assert next_request["available_at"] > request["created_at"]
    assert bridge._claim() is None


@pytest.fixture
def private_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'origin.sqlite3'}")
    db.create_all()
    bridge = RiskResearchAdmissionService(db, enabled=True, clock=lambda: NOW)
    yield db, bridge
    db.engine.dispose()


@pytest.mark.parametrize("kind,key", [("daily", "daily1"), ("manual", "internal-no-owner")])
def test_automatic_and_unowned_manual_never_bypass_automatic_quota(private_bridge, kind, key):
    db, bridge = private_bridge
    _seed_budget(db, NOW)
    request = bridge.stage("ETH-USDT", request_id=key, kind=kind)
    claim = bridge._claim()
    with pytest.raises(ValueError, match="research_admission_budget_exhausted"):
        bridge._admit(claim[0], claim[2])
    bridge._failure(claim[0], claim[2], ValueError("research_admission_budget_exhausted"))
    with db.session() as session:
        assert session.get(RiskResearchRequestRecord, request["request_id"]).run_id is None
        assert session.query(EventRecord).count() == 0
        assert session.query(RunRecord).count() == 12


@pytest.mark.parametrize("damage", ["owner", "source_ref", "hash", "scope"])
def test_forged_or_mismatched_trigger_cannot_authorize_manual(private_bridge, damage):
    db, bridge = private_bridge
    request = _manual(bridge)
    with db.session() as session:
        trigger = session.scalars(select(RiskResearchTriggerRecord)).one()
        import json
        body = json.loads(trigger.payload_json)
        if damage == "owner":
            body["source"]["owner_id"] = False
            trigger.payload_json, trigger.payload_hash = canonical_json(body), content_hash(body)
        elif damage == "source_ref":
            trigger.source_ref = "owner-command:other-command"
        elif damage == "hash":
            body["source"]["owner_id"] = "changed-owner"
            trigger.payload_json = canonical_json(body)
        else:
            trigger.scope_id = spot_scope("BTC-USDT").scope_id
    claim = bridge._claim()
    reason = "risk_research_(manual_authority_invalid|trigger_corrupt)"
    with pytest.raises(ValueError, match=reason):
        bridge._admit(claim[0], claim[2])
    with db.session() as session:
        assert session.get(RiskResearchRequestRecord, request["request_id"]).run_id is None
        assert session.query(RunRecord).count() == session.query(EventRecord).count() == 0


def test_manual_keeps_study_concurrency_and_unsettled_host_gates(private_bridge):
    db, _bridge = private_bridge
    study_id = _seed_budget(db, NOW)
    with db.session() as session:
        run = session.get(RunRecord, "fixture-budget-0")
        run.status = "running"
    with db.session() as session:
        assert research_admission_guard_in_session(
            session, study_id=study_id, now=NOW, admission_origin="manual"
        ).reason_code == "study_research_active"
    with db.session() as session:
        session.get(RunRecord, "fixture-budget-0").status = "failed"
        session.add(DshSessionLinkRecord(
            run_id="fixture-budget-0", dsh_session_id="fixture-unsettled", request_hash="a" * 64,
            state="running", upstream_identity_json="{}", plugin_build_hash="b" * 64,
            generation=1, created_at=NOW, updated_at=NOW, deadline_at=NOW + timedelta(minutes=10),
        ))
    with db.session() as session:
        assert research_admission_guard_in_session(
            session, study_id=study_id, now=NOW, admission_origin="manual"
        ).reason_code == "study_host_terminal_unknown"


def test_manual_does_not_resume_paused_study(private_bridge):
    db, bridge = private_bridge
    study_id = _seed_budget(db, NOW)
    request = _manual(bridge)
    with db.session() as session:
        session.get(StudyRecord, study_id).status = "paused"
    claim = bridge._claim()
    with pytest.raises(ValueError, match="study_not_active"):
        bridge._admit(claim[0], claim[2])
    with db.session() as session:
        assert session.get(StudyRecord, study_id).status == "paused"
        assert session.get(RiskResearchRequestRecord, request["request_id"]).run_id is None
        assert session.query(EventRecord).count() == 0
