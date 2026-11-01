"""Actual SQLite/Run admission; synthetic captured paths are not model evidence."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import (
    canonical_json,
    content_hash,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import EventRecord, RunRecord, StudyRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import RiskAlertRecord
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from tests.market_risk.test_spot_scope_admission import prepared as _prepared_fixture


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    yield from _prepared_fixture.__wrapped__(tmp_path, monkeypatch)


def service(prepared):
    db, now, _resources, _resource, runtime = prepared
    installed = {x: runtime(x, capture=True) for x in ("BTC-USDT", "ETH-USDT")}
    ids = {}

    async def prepare_scope(instrument):
        gate, effective, host = installed[instrument]
        ids[instrument] = gate.approve_trial(
            request_id=f"isolated:{instrument}:{now[0].isoformat()}",
            effective=effective,
            host=host,
        ).admission_id

    def resolver(session, study, at):
        gate, effective, host = installed[study.instrument]
        return gate.resolve_current_in_session(
            session,
            admission_id=ids[study.instrument],
            effective=effective,
            host=host,
            assessed_at=at,
        )

    return RiskResearchAdmissionService(
        db,
        enabled=True,
        clock=lambda: now[0],
        prepare_scope=prepare_scope,
        spot_admission_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_both_exact_assets_admit_durable_runs_and_frozen_proofs(prepared):
    db, now, *_ = prepared
    bridge = await asyncio.to_thread(service, prepared)
    assert await bridge.process_due(limit=2) == 2
    assert await bridge.process_due(limit=2) == 0
    with db.session() as session:
        requests = session.scalars(select(RiskResearchRequestRecord)).all()
        assert len(requests) == 2
        for request in requests:
            run = session.get(RunRecord, request.run_id)
            study = session.get(StudyRecord, run.study_id)
            proof = read_run_spot_admission_in_session(session, run.run_id)
            assert request.status == "queued"
            assert study.instrument == request.instrument == proof.scope.instrument
            assert run.admission_origin == "automatic"
            assert len(bridge.latest(request.scope_id)["trigger_refs"]) == 1
    now[0] += timedelta(hours=1)
    with db.session() as session:
        assert read_run_spot_admission_in_session(session, requests[0].run_id) is not None


def test_concurrent_commands_merge_without_discarding_any_trigger_and_restart(prepared):
    db, now, *_ = prepared
    bridge = service(prepared)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda i: bridge.stage("ETH-USDT", request_id=f"command{i}"), range(5))
        )
    assert len({r["request_id"] for r in results}) == 1
    restarted = RiskResearchAdmissionService(db, enabled=True, clock=lambda: now[0])
    state = restarted.latest(spot_scope("ETH-USDT").scope_id)
    assert state["status"] == "pending_admission" and len(state["trigger_refs"]) == 5
    assert bridge.stage("ETH-USDT", request_id="command0")["request_id"] == state["request_id"]
    with pytest.raises(ValueError, match="identity_mismatch"):
        bridge.stage("BTC-USDT", request_id="command0")


@pytest.mark.asyncio
async def test_new_severe_trigger_during_run_is_preserved_for_next_run(prepared):
    db, now, *_ = prepared
    bridge = await asyncio.to_thread(service, prepared)
    assert await bridge.process_due(limit=2) == 2
    first = bridge.latest(spot_scope("BTC-USDT").scope_id)
    new = bridge.stage(
        "BTC-USDT",
        request_id="severe1",
        kind="risk_alert",
        source={"severity": "high"},
        source_ref="actual-test-alert",
    )
    assert new["request_id"] != first["request_id"]
    assert new["available_at"] > first["created_at"]
    now[0] += timedelta(minutes=31)
    assert bridge._claim() is None  # In-flight model work cannot duplicate a Run.
    with db.session() as session:
        for row in session.scalars(select(RunRecord)).all():
            row.status = "degraded"
    claim = bridge._claim()
    assert claim[0] == new["request_id"]
    with db.session() as session:
        triggers = session.scalars(
            select(RiskResearchTriggerRecord).where(
                RiskResearchTriggerRecord.request_id == new["request_id"],
            )
        ).all()
        assert len(triggers) == 1 and '"high"' in triggers[0].payload_json


@pytest.mark.asyncio
async def test_failed_admission_rolls_back_event_study_and_run_then_stops_retrying(prepared):
    db, now, *_ = prepared

    async def no_prepared_admission(_instrument):
        pass

    bridge = RiskResearchAdmissionService(
        db,
        enabled=True,
        clock=lambda: now[0],
        prepare_scope=no_prepared_admission,
    )
    command = bridge.stage("BTC-USDT", request_id="no-admission")
    # Exclude the independent ETH daily intent so this finite protocol measures BTC.
    bridge.ensure_daily_plans()
    with db.session() as session:
        from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
            RiskResearchPlanRecord,
        )

        for plan in session.scalars(select(RiskResearchPlanRecord)).all():
            plan.state = "paused"
    for delay in (0, 60, 120):
        now[0] += timedelta(seconds=delay)
        assert await bridge.process_due(limit=1) == 0
    view = bridge.latest(command["scope_id"])
    assert view["status"] == "failed" and view["attempts"] == 3
    now[0] += timedelta(days=1)
    assert await bridge.process_due(limit=1) == 0
    with db.session() as session:
        assert not session.scalars(select(RunRecord)).all()
        assert not session.scalars(select(EventRecord)).all()
        assert not session.scalars(select(StudyRecord)).all()


def test_expired_owner_cannot_admit_or_rewrite_failure_after_reclaim(prepared):
    db, now, *_ = prepared
    first = service(prepared)
    first.stage("BTC-USDT", request_id="lease")
    claim1 = first._claim()
    now[0] += timedelta(seconds=61)
    second = RiskResearchAdmissionService(db, enabled=True, clock=lambda: now[0])
    claim2 = second._claim()
    assert claim2[2] == claim1[2] + 1
    assert first._admit(claim1[0], claim1[2]) is False
    first._failure(claim1[0], claim1[2], ValueError("stale_owner"))
    assert second.latest(spot_scope("BTC-USDT").scope_id)["error_code"] is None


def test_fixture_batch_and_unsupported_scopes_do_not_starve_later_actual_alert(prepared):
    db, now, *_ = prepared
    bridge = RiskResearchAdmissionService(db, enabled=True, clock=lambda: now[0])
    with db.session() as session:
        for i in range(22):
            payload = {"input": {"input_ref": "fixture://isolated" if i < 20 else "actual-raw"}}
            scope = spot_scope("BTC-USDT").scope_id if i != 20 else "not-an-admitted-scope"
            stamp = now[0] - timedelta(minutes=22 - i)
            session.add(RiskAlertRecord(
                alert_id=f"alert{i:02}", episode_id=f"episode{i:02}", plan_id="test",
                evaluation_id=f"eval{i}", scope_id=scope, rule_version="test",
                alarm_kind="fact_change", severity="warning", observed_at=stamp,
                created_at=stamp, published_at=stamp, payload_json=canonical_json(payload),
                payload_hash=content_hash(payload),
            ))
    bridge.discover()
    bridge.discover()
    bridge.discover()
    with db.session() as session:
        triggers = session.scalars(select(RiskResearchTriggerRecord)).all()
        assert sum(row.kind == "excluded_fixture" for row in triggers) == 20
        assert sum(row.kind == "excluded_scope" for row in triggers) == 1
        actual = [row for row in triggers if row.kind == "risk_alert"]
        assert len(actual) == 1 and actual[0].source_ref == "alert21"
        assert not session.scalars(select(RunRecord)).all()
        assert not any(row.kind == "risk_alert" and row.source_ref != "alert21" for row in triggers)
