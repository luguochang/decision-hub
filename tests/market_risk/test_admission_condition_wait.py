"""Draft private-SQLite gates. Apply all eight source candidates before running."""

import asyncio
import json
import sqlite3
import threading
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import risk_research_admission as admission
from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRecord,
    DshHostActivityScope,
    DshHostActivitySnapshot,
    activity_binding,
    activity_targets_in_session,
    freeze_activity_snapshot_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_admission_wait import (
    KIND,
    AdmissionConditionWait,
    ContinuousAdmissionWriter,
    admission_accounting_valid,
    recognized_condition_waits,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    ProductCommandRecord,
    RunRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.market_risk.test_risk_activity_claiming46 import (
    NOW,
    ROOT_RUN,
    _prepared_research_service,
    _repeat_pending_zero,
    database,
    repeated_prepared,
)

assert database is not None and repeated_prepared is not None


def _missing_wait(service, clock):
    claim = service._claim()
    assert claim is not None
    with pytest.raises(AdmissionConditionWait) as raised:
        service._fresh_preflight(claim[0], claim[2])
    assert service._defer_condition(claim[0], claim[2], raised.value)
    assert not service._defer_condition(claim[0], claim[2], raised.value)
    with service.database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert admission_accounting_valid(session, row, now=clock[0])
        assert row.attempts == 0 and row.lease_generation == claim[2]
        assert activity_targets_in_session(session, now=clock[0]) is not None
    assert service._claim() is None
    clock[0] += timedelta(seconds=20)
    return claim


@pytest.mark.asyncio
async def test_four_waits_then_one_admission_across_continuous_writer_expiry(
    repeated_prepared,
    monkeypatch,
):
    db, clock, *_ = repeated_prepared
    clock[0] = NOW
    service = await asyncio.to_thread(_prepared_research_service, repeated_prepared)
    request_id, _ = _repeat_pending_zero(db)
    await service.prepare_scope("BTC-USDT")
    for generation in range(1, 5):
        assert _missing_wait(service, clock)[2] == generation
    claim = service._claim()
    assert claim[0] == request_id and claim[2] == 5
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.attempts == 1 and row.lease_generation == 5
        assert len(recognized_condition_waits(session, row, now=clock[0])) == 4
        binding = activity_binding(
            session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        )
    scope = DshHostActivityScope(
        host_instance_id=UUID("5f4d4ff3-e9e0-44ba-a858-24b684c5f0bf"),
        process_id=1234,
        started_at=NOW - timedelta(minutes=5),
        hub_base_url="http://127.0.0.1:8000",
        workspace_id="workspace:controlled",
        workspace_cwd_hash="a" * 64,
    )
    snapshot = DshHostActivitySnapshot(
        schema_version="dsh-host-activity-snapshot.v1",
        scope=scope,
        observed_at=clock[0],
        expires_at=clock[0] + timedelta(seconds=30),
        census_complete=True,
        records=[
            DshHostActivityRecord(
                **binding.model_dump(),
                state="current_scope_inactive",
                presence="absent",
                running=False,
                queue_count=0,
                active_job_count=0,
                descendant_ids=[],
                fence_id="fence:wait-private",
                reason_code="host_activity_lineage_quiescent",
                terminal_state="unknown",
            )
        ],
    )
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        freeze_activity_snapshot_in_session(
            session,
            snapshot,
            expected_scope=scope,
            transport_fingerprint="d" * 64,
            recorded_at=clock[0],
            raw_snapshot_json=snapshot.model_dump_json(),
        )
    assert service._fresh_preflight(*[claim[0], claim[2]])
    original = service.runs.create
    completed = []

    def create_then_cross_claim_deadline(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] += timedelta(seconds=61)
        # A genuine independent connection cannot take over while this writer owns it.
        other = sqlite3.connect(db.engine.url.database, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
        completed.append(clock[0])
        return value

    monkeypatch.setattr(service.runs, "create", create_then_cross_claim_deadline)
    assert service._admit(claim[0], claim[2])
    assert not service._admit(claim[0], claim[2])
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.status == "queued" and row.run_id is not None
        assert row.lease_generation == 5 and row.attempts == 1
        assert as_utc(row.updated_at) == completed[0]
        assert len(session.scalars(select(RunRecord)).all()) == 2


@pytest.mark.parametrize("expire_final", [False, True])
def test_credit_does_not_remove_real_failure_ceiling(database, expire_final):
    clock = [NOW]
    request_id, _ = _repeat_pending_zero(database)
    service = admission.RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    _missing_wait(service, clock)
    for number in range(1, 4):
        claim = service._claim()
        assert claim[2] == number + 1
        if number == 3 and expire_final:
            clock[0] += timedelta(seconds=60)
            assert service._claim() is None
        else:
            service._failure(request_id, claim[2], ValueError("real-source-failure"))
            clock[0] += timedelta(seconds=60 * 2 ** (number - 1))
    assert service._claim() is None
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.status == "failed" and row.attempts == 3 and row.lease_generation == 4
        assert row.run_id is None and row.event_id is None
        assert admission_accounting_valid(session, row, now=clock[0])


@pytest.mark.parametrize("damage", ["counter", "raw_hash", "generation", "trigger", "time"])
def test_forged_credit_cannot_create_collection_demand(database, damage):
    clock = [NOW]
    request_id, _ = _repeat_pending_zero(database)
    service = admission.RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    _missing_wait(service, clock)
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        record = session.scalar(
            select(ProductCommandRecord).where(
                ProductCommandRecord.target_id == request_id, ProductCommandRecord.kind == KIND
            )
        )
        if damage == "counter":
            row.lease_generation += 1
        elif damage == "raw_hash":
            record.result_json += " "
        else:
            body = json.loads(record.result_json)
            if damage == "generation":
                body["lease_generation"] += 1
            if damage == "trigger":
                body["initiating_trigger_hash"] = "f" * 64
            if damage == "time":
                body["available_at"] = body["recorded_at"]
            record.result_json = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            record.payload_hash = canonical_hash(body)
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert not admission_accounting_valid(session, row, now=clock[0])
        assert activity_targets_in_session(session, now=clock[0]) is None
    assert service._claim() is None


@pytest.mark.asyncio
async def test_same_claim_survives_long_prepare_without_spending_attempt(database, monkeypatch):
    clock = [NOW]
    request_id, _ = _repeat_pending_zero(database)
    prepared = []

    async def long_prepare(instrument):
        prepared.append(instrument)
        for _ in range(4):
            clock[0] += timedelta(seconds=25)
            await asyncio.sleep(0.04)

    service = admission.RiskResearchAdmissionService(
        database, enabled=True, clock=lambda: clock[0], prepare_scope=long_prepare
    )
    monkeypatch.setattr(service, "ensure_daily_plans", lambda: None)
    monkeypatch.setattr(service, "discover", lambda: None)
    # Speed up only scheduler cadence; use real renew writer/clock/ownership checks.
    monkeypatch.setattr(admission, "CLAIM_RENEW_INTERVAL_SECONDS", 0.01)
    assert await service.process_due(limit=1) == 0
    assert prepared == ["BTC-USDT"]
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.status == "pending_admission" and row.attempts == 0
        assert row.lease_generation == 1 and row.run_id is None
        assert as_utc(row.updated_at) == clock[0] and clock[0] > NOW + timedelta(seconds=60)


@pytest.mark.parametrize("lost", ["owner", "expired"])
def test_renewal_never_revives_old_permission(database, lost):
    clock = [NOW]
    request_id, _ = _repeat_pending_zero(database)
    service = admission.RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    claim = service._claim()
    if lost == "owner":
        with database.session() as session:
            session.get(RiskResearchRequestRecord, request_id).lease_owner = "different-owner"
    else:
        clock[0] += timedelta(seconds=60)
    assert service._renew_claim(request_id, claim[2]) is False
    assert (
        service._defer_condition(
            request_id, claim[2], AdmissionConditionWait("fresh_host_preflight")
        )
        is False
    )


@pytest.mark.asyncio
async def test_cancel_drains_exact_native_call_before_return():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def owned():
        calls.append(1)
        entered.set()
        assert release.wait(5)

    task = asyncio.create_task(
        admission.RiskResearchAdmissionService._observe_owned(asyncio.to_thread(owned))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [1]


def test_expired_claim_cannot_enter_writer(database):
    clock = [NOW]
    request_id, _ = _repeat_pending_zero(database)
    service = admission.RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    claim = service._claim()
    clock[0] += timedelta(seconds=60)
    assert service._admit(request_id, claim[2]) is False
    with database.session() as session:
        assert session.get(RiskResearchRequestRecord, request_id).run_id is None


@pytest.mark.parametrize("damage", ["transaction", "owner"])
def test_continuous_writer_rejects_released_boundary_or_changed_owner(database, damage):
    request_id, _ = _repeat_pending_zero(database)
    service = admission.RiskResearchAdmissionService(database, enabled=True, clock=lambda: NOW)
    claim = service._claim()
    with pytest.raises(ValueError, match="risk_admission_writer_continuity_lost"):
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(RiskResearchRequestRecord, request_id)
            with ContinuousAdmissionWriter(
                session, row, owner=service.owner_id, generation=claim[2], acquired_at=NOW
            ) as writer:
                if damage == "transaction":
                    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                        writer.native.execute("ROLLBACK")
                else:
                    row.lease_owner = "changed-owner"
                writer.complete(row, completed_at=NOW + timedelta(seconds=61))
    # Rollback succeeded after the guard restored native authorizer, no pool pollution.
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.lease_owner == service.owner_id and row.run_id is None
