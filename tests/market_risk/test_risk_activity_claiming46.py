"""Private SQLite controls for real claim transitions; never current Host authority."""
# pyright: reportPrivateUsage=false
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.pool import QueuePool

from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRecord,
    DshHostActivityScope,
    DshHostActivitySnapshot,
    activity_binding,
    activity_targets_in_session,
    freeze_activity_snapshot_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    EventRecord,
    RunRecord,
    ServiceRuntimeConfigRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchPlanRecord,
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from tests.market_risk.test_risk_activity_demand29 import (
    NOW,
    ROOT_RUN,
    _seed,
)
from tests.market_risk.test_risk_activity_demand29 import (
    database as _private_database_fixture,
)
from tests.market_risk.test_risk_research_admission import (
    prepared as _private_prepared_fixture,
    service as _prepared_research_service,
)


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    # pytest exposes the decorated fixture's original callable at runtime.
    yield from _private_database_fixture.__wrapped__(  # pyright: ignore[reportAttributeAccessIssue]
        tmp_path, monkeypatch,
    )


@pytest.fixture
def repeated_prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yield from _private_prepared_fixture.__wrapped__(tmp_path, monkeypatch)


def _repeat_pending_zero(database: Database) -> tuple[str, str]:
    request_id, study_id = _seed(database, attempts=1)
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None
        request.attempts = request.lease_generation = 0
        request.available_at = NOW
        request.error_code = None
        session.add(RiskResearchRequestRecord(
            request_id='riskresearch_previous_user_request', scope_id=request.scope_id,
            instrument=request.instrument, status='queued', run_id=ROOT_RUN,
            event_id='event:old-risk-activity', created_at=NOW - timedelta(days=2),
            updated_at=NOW - timedelta(days=1), available_at=NOW - timedelta(days=2),
            attempts=1, lease_generation=1,
        ))
    return request_id, study_id


@pytest.mark.asyncio
async def test_repeat_terminal_request_needs_fresh_host_proof_before_actual_run(repeated_prepared):
    import asyncio

    database, clock, *_ = repeated_prepared
    clock[0] = NOW
    service = await asyncio.to_thread(_prepared_research_service, repeated_prepared)
    request_id, _study_id = _repeat_pending_zero(database)
    await service.prepare_scope('BTC-USDT')
    claim = service._claim()
    assert claim is not None and claim[0] == request_id and claim[2] == 1
    with database.session() as session:
        target = activity_targets_in_session(session, now=clock[0])
        assert target is not None and [item.run_id for item in target.records] == [ROOT_RUN]
        run, link = session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        assert run is not None and link is not None
        old_run = tuple(session.execute(select(RunRecord.__table__).where(
            RunRecord.run_id == ROOT_RUN,
        )).one())
        old_link = tuple(session.execute(select(DshSessionLinkRecord.__table__).where(
            DshSessionLinkRecord.run_id == ROOT_RUN,
        )).one())
        binding = activity_binding(run, link)
    with pytest.raises(ValueError, match='study_host_activity_refresh_required'):
        service._fresh_preflight(claim[0], claim[2])
    with pytest.raises(ValueError, match='study_host_activity_refresh_required'):
        service._admit(claim[0], claim[2])
    with database.session() as session:
        assert [row.run_id for row in session.scalars(select(RunRecord))] == [ROOT_RUN]
        assert not session.scalars(select(EventRecord)).all()
    scope = DshHostActivityScope(
        host_instance_id=UUID('5f4d4ff3-e9e0-44ba-a858-24b684c5f0bf'), process_id=1234,
        started_at=NOW - timedelta(minutes=5), hub_base_url='http://127.0.0.1:8000',
        workspace_id='workspace:controlled', workspace_cwd_hash='a' * 64,
    )
    snapshot = DshHostActivitySnapshot(
        schema_version='dsh-host-activity-snapshot.v1', scope=scope, observed_at=clock[0],
        expires_at=clock[0] + timedelta(seconds=30), census_complete=True,
        records=[DshHostActivityRecord(
            **binding.model_dump(), state='current_scope_inactive', presence='absent',
            running=False, queue_count=0, active_job_count=0, descendant_ids=[],
            fence_id='fence:repeat-private', reason_code='host_activity_lineage_quiescent',
            terminal_state='unknown',
        )],
    )
    with database.session() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        assert freeze_activity_snapshot_in_session(
            session, snapshot, expected_scope=scope, transport_fingerprint='d' * 64,
            recorded_at=clock[0], raw_snapshot_json=snapshot.model_dump_json(),
        ) == 1
    assert service._fresh_preflight(claim[0], claim[2]) is True
    assert service._admit(claim[0], claim[2]) is True
    assert service._admit(claim[0], claim[2]) is False
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None and request.status == 'queued' and request.attempts == 1
        assert request.run_id and request.run_id != ROOT_RUN
        assert len(session.scalars(select(RunRecord)).all()) == 2
        assert tuple(session.execute(select(RunRecord.__table__).where(
            RunRecord.run_id == ROOT_RUN,
        )).one()) == old_run
        assert tuple(session.execute(select(DshSessionLinkRecord.__table__).where(
            DshSessionLinkRecord.run_id == ROOT_RUN,
        )).one()) == old_link


@pytest.mark.parametrize('status', ['admitted', 'running'])
def test_repeat_nonterminal_request_still_cannot_acquire_condition_lease(database: Database, status: str):
    request_id, _study_id = _repeat_pending_zero(database)
    with database.session() as session:
        run = session.get(RunRecord, ROOT_RUN)
        assert run is not None
        run.status = status
    service = RiskResearchAdmissionService(database, enabled=True, clock=lambda: NOW)
    assert service._claim() is None
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None and request.status == 'pending_admission'
        assert request.attempts == request.lease_generation == 0
        assert request.lease_owner is None and request.run_id is None and request.event_id is None


@pytest.mark.parametrize('fault', ['missing_run_study', 'foreign_run_study', 'scope', 'asset', 'identity'])
def test_repeat_unsettled_link_requires_exact_original_study_identity(database: Database, fault: str):
    request_id, study_id = _repeat_pending_zero(database)
    with database.session() as session:
        run, study = session.get(RunRecord, ROOT_RUN), session.get(StudyRecord, study_id)
        assert run is not None and study is not None
        if fault == 'missing_run_study':
            run.study_id = None
        elif fault == 'foreign_run_study':
            run.study_id = 'study:foreign-user-history'
        elif fault == 'scope':
            study.scope_key = 'f' * 64
        elif fault == 'asset':
            study.instrument = 'ETH-USDT'
        else:
            study.identity_basis = 'legacy_unknown'
    service = RiskResearchAdmissionService(database, enabled=True, clock=lambda: NOW)
    assert service._claim() is None
    assert service._admit(request_id, 1) is False
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None and request.status == 'pending_admission'
        assert request.attempts == request.lease_generation == 0
        assert [row.run_id for row in session.scalars(select(RunRecord))] == [ROOT_RUN]


def claimed(
    database: Database,
) -> tuple[RiskResearchAdmissionService, list[datetime], tuple[str, str, int], str]:
    request_id, study_id = _seed(database, attempts=1)
    clock = [NOW + timedelta(seconds=60)]
    service = RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    claim = service._claim()
    assert claim is not None and claim[0] == request_id and claim[2] == 2
    return service, clock, claim, study_id


def test_claim_remains_collection_demand_past_30s_and_requires_fresh_originals(database: Database):
    service, clock, claim, _study_id = claimed(database)
    clock[0] += timedelta(seconds=31)
    with database.session() as session:
        target = activity_targets_in_session(session, now=clock[0])
        assert target is not None and [row.run_id for row in target.records] == [ROOT_RUN]
        run, link = session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        assert run is not None and link is not None
        binding = activity_binding(run, link)
        assert not session.scalars(select(ServiceRuntimeConfigRecord)).all()
    with pytest.raises(ValueError, match='study_host_activity_refresh_required'):
        service._fresh_preflight(claim[0], claim[2])
    scope = DshHostActivityScope(
        host_instance_id=UUID('5f4d4ff3-e9e0-44ba-a858-24b684c5f0bf'), process_id=1234,
        started_at=NOW - timedelta(minutes=5), hub_base_url='http://127.0.0.1:8000',
        workspace_id='workspace:controlled', workspace_cwd_hash='a' * 64,
    )
    snapshot = DshHostActivitySnapshot(
        schema_version='dsh-host-activity-snapshot.v1', scope=scope, observed_at=clock[0],
        expires_at=clock[0] + timedelta(seconds=30), census_complete=True,
        records=[DshHostActivityRecord(
            **binding.model_dump(), state='current_scope_inactive', presence='absent',
            running=False, queue_count=0, active_job_count=0, descendant_ids=[],
            fence_id='fence:controlled', reason_code='host_activity_lineage_quiescent',
            terminal_state='unknown',
        )],
    )
    with database.session() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        assert freeze_activity_snapshot_in_session(
            session, snapshot, expected_scope=scope, transport_fingerprint='d' * 64,
            recorded_at=clock[0], raw_snapshot_json=snapshot.model_dump_json(),
        ) == 1
    assert service._fresh_preflight(claim[0], claim[2]) is True
    with database.session() as session:
        run, link = session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        assert run is not None and run.status == 'failed' and run.artifact_id is None
        assert link is not None and link.state == 'running' and link.terminal_at is None
        assert activity_binding(run, link) == binding
    clock[0] = NOW + timedelta(seconds=120)
    with database.session() as session:
        assert activity_targets_in_session(session, now=clock[0]) is None
    assert service._fresh_preflight(claim[0], claim[2]) is False


@pytest.mark.asyncio
async def test_process_due_keeps_demand_during_long_prepare_and_preflights_without_writer(
    database: Database, monkeypatch: pytest.MonkeyPatch,
):
    request_id, _study_id = _seed(database, attempts=1)
    clock = [NOW + timedelta(seconds=60)]
    prepared = []

    async def prepare(_instrument: str):
        assert cast(QueuePool, database.engine.pool).checkedout() == 0
        clock[0] += timedelta(seconds=31)
        with database.session() as session:
            row = session.get(RiskResearchRequestRecord, request_id)
            assert row is not None
            assert row.status == 'claiming' and row.lease_owner == service.owner_id
            target = activity_targets_in_session(session, now=clock[0])
            assert target is not None and target.records[0].run_id == ROOT_RUN
        prepared.append(True)

    service = RiskResearchAdmissionService(
        database, enabled=True, clock=lambda: clock[0], prepare_scope=prepare,
    )
    service.ensure_daily_plans()
    with database.session() as session:
        for plan in session.scalars(select(RiskResearchPlanRecord)):
            plan.state = 'paused'
    original = service._fresh_preflight
    in_preflight = [False]
    writer_in_preflight = []

    def observe_statement(
        _connection: object, _cursor: object, statement: str, _parameters: object,
        _context: object, _many: bool,
    ):
        if in_preflight[0] and statement.upper().startswith('BEGIN IMMEDIATE'):
            writer_in_preflight.append(statement)

    def observed_preflight(key: str, generation: int) -> bool:
        in_preflight[0] = True
        try:
            return original(key, generation)
        finally:
            in_preflight[0] = False

    event.listen(database.engine, 'before_cursor_execute', observe_statement)
    monkeypatch.setattr(service, '_fresh_preflight', observed_preflight)
    try:
        assert await service.process_due(limit=1) == 0
    finally:
        event.remove(database.engine, 'before_cursor_execute', observe_statement)
    assert prepared == [True] and writer_in_preflight == []
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row is not None
        assert row.status == 'pending_admission' and row.attempts == 2
        assert row.error_code == 'study_host_activity_refresh_required'
        assert row.run_id is None and row.event_id is None


def test_claim_lease_owner_generation_and_available_clock_fail_closed(database: Database):
    service, clock, claim, _study_id = claimed(database)
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None
        for name, value in (
            ('lease_owner', 'foreign-worker'), ('lease_owner', None),
            ('lease_expires_at', clock[0]), ('lease_expires_at', clock[0] + timedelta(seconds=61)),
            ('updated_at', clock[0] + timedelta(seconds=1)),
            ('available_at', clock[0] + timedelta(seconds=1)), ('lease_generation', 1),
            ('run_id', 'foreign-run'), ('event_id', 'foreign-event'), ('status', 'failed'),
        ):
            original = getattr(row, name)
            setattr(row, name, value)
            session.flush()
            assert activity_targets_in_session(session, now=clock[0]) is None, name
            setattr(row, name, original)
            session.flush()
        # Collection may observe another structurally managed owner. It does
        # not claim to be that owner and cannot consume or rewrite its lease.
        row.lease_owner = 'risk-research:' + 'f' * 32
    assert service._fresh_preflight(claim[0], claim[2]) is False
    assert service._admit(claim[0], claim[2]) is False
    service._failure(claim[0], claim[2], ValueError('foreign-owner-must-not-write'))
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None and row.status == 'claiming' and row.attempts == 2
        assert row.error_code == 'study_host_terminal_unknown'


def test_corrupt_trigger_is_neither_collection_demand_nor_preflight_authority(database: Database):
    service, clock, claim, _study_id = claimed(database)
    with database.session() as session:
        trigger = session.scalar(select(RiskResearchTriggerRecord).where(
            RiskResearchTriggerRecord.request_id == claim[0],
        ))
        assert trigger is not None
        trigger.payload_hash = '0' * 64
    with database.session() as session:
        assert activity_targets_in_session(session, now=clock[0]) is None
    with pytest.raises(ValueError, match='risk_research_trigger_corrupt'):
        service._fresh_preflight(claim[0], claim[2])


def test_actual_third_failure_remains_failed_without_fourth_claim(database: Database):
    service, clock, claim, _study_id = claimed(database)
    service._failure(claim[0], claim[2], ValueError('study_host_activity_refresh_required'))
    with database.session() as session:
        assert activity_targets_in_session(session, now=clock[0]) is not None
    clock[0] += timedelta(seconds=120)
    third = service._claim()
    assert third is not None and third[2] == 3
    with database.session() as session:
        assert activity_targets_in_session(session, now=clock[0]) is not None
    service._failure(third[0], third[2], ValueError('study_host_activity_refresh_required'))
    clock[0] += timedelta(days=1)
    assert service._claim() is None
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, third[0])
        assert row is not None and row.status == 'failed' and row.attempts == 3
        assert activity_targets_in_session(session, now=clock[0]) is None
        link = session.get(DshSessionLinkRecord, ROOT_RUN)
        assert link is not None and link.terminal_at is None


def test_new_owner_intent_is_idempotent_and_preserves_failed_three_original(database: Database):
    service, clock, claim, _study_id = claimed(database)
    with database.session() as session:
        old = session.get(RiskResearchRequestRecord, claim[0])
        assert old is not None
        old.status, old.attempts, old.lease_generation = 'failed', 3, 3
        old.lease_owner = old.lease_expires_at = None
        old.error_code = 'study_host_activity_refresh_required'
        original = tuple(getattr(old, column.name) for column in old.__table__.columns)
    new = service.stage(
        'BTC-USDT', request_id='new-authorized-intent',
        source_ref='owner-command:new-authorized-intent', source={'owner_id': 'controlled-owner'},
    )
    assert new['request_id'] != claim[0] and new['status'] == 'pending_admission'
    assert new['attempts'] == 0 and new['run_id'] is None
    repeated = service.stage(
        'BTC-USDT', request_id='new-authorized-intent',
        source_ref='owner-command:new-authorized-intent', source={'owner_id': 'controlled-owner'},
    )
    assert repeated['request_id'] == new['request_id']
    with database.session() as session:
        old = session.get(RiskResearchRequestRecord, claim[0])
        assert old is not None
        assert tuple(getattr(old, column.name) for column in old.__table__.columns) == original
    clock[0] += timedelta(seconds=1)


def test_daily_claim_requires_real_matching_active_plan(database: Database):
    _request_id, _study_id = _seed(database, attempts=1)
    service = RiskResearchAdmissionService(database, enabled=True, clock=lambda: NOW)
    service.ensure_daily_plans()
    with database.session() as session:
        eth_plan = session.get(RiskResearchPlanRecord, 'eth-usdt-spot-daily-research.v1')
        assert eth_plan is not None
        eth_plan.state = 'paused'
    service.discover()  # Adds the saved real daily trigger to the original pending intent.
    clock = NOW + timedelta(seconds=60)
    service.clock = lambda: clock
    claim = service._claim()
    assert claim is not None and claim[0] == _request_id
    with database.session() as session:
        assert activity_targets_in_session(session, now=clock) is not None
        plan = session.get(RiskResearchPlanRecord, 'btc-usdt-spot-daily-research.v1')
        assert plan is not None
        plan.state = 'paused'
        session.flush()
        assert activity_targets_in_session(session, now=clock) is None


def final_claim(database: Database):
    request_id, _study_id = _seed(database, attempts=2)
    clock = [NOW + timedelta(seconds=120)]
    service = RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    claim = service._claim()
    assert claim is not None and claim[0] == request_id and claim[2] == 3
    return service, clock, claim


def test_restart_retires_expired_third_lease_without_admission_or_fourth_attempt(
    database: Database,
):
    original, clock, claim = final_claim(database)
    replacement = RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    assert replacement.owner_id != original.owner_id
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None
        owner, expiry = row.lease_owner, row.lease_expires_at
    clock[0] += timedelta(seconds=59)
    assert replacement._claim() is None
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None and row.status == 'claiming'
    clock[0] += timedelta(seconds=1)
    assert replacement._claim() is None
    assert original._fresh_preflight(claim[0], claim[2]) is False
    assert original._admit(claim[0], claim[2]) is False
    original._failure(claim[0], claim[2], ValueError('late-owner'))
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None and row.status == 'failed' and row.attempts == 3
        assert row.lease_generation == 3 and row.run_id is None and row.event_id is None
        assert row.error_code == 'risk_research_claim_lease_expired'
        assert row.lease_owner == owner and row.lease_expires_at == expiry
        assert session.scalar(select(RunRecord).where(RunRecord.run_id != ROOT_RUN)) is None
        assert not session.scalars(select(ServiceRuntimeConfigRecord)).all()


@pytest.mark.asyncio
async def test_third_prepare_exhausting_lease_is_closed_by_following_managed_tick(
    database: Database,
):
    request_id, _study_id = _seed(database, attempts=2)
    clock = [NOW + timedelta(seconds=120)]

    async def prepare(_instrument: str):
        clock[0] += timedelta(seconds=60)

    service = RiskResearchAdmissionService(
        database, enabled=True, clock=lambda: clock[0], prepare_scope=prepare,
    )
    service.ensure_daily_plans()
    with database.session() as session:
        for plan in session.scalars(select(RiskResearchPlanRecord)):
            plan.state = 'paused'
    assert await service.process_due(limit=1) == 0
    assert await service.process_due(limit=1) == 0
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row is not None and row.status == 'failed' and row.attempts == 3
        assert row.error_code == 'risk_research_claim_lease_expired'
        assert row.run_id is None and row.event_id is None


def test_new_owner_intent_retires_expiry_before_active_request_coalescing(database: Database):
    original, clock, claim = final_claim(database)
    clock[0] += timedelta(seconds=60)
    restarted = RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    value = restarted.stage(
        'BTC-USDT', request_id='intent-after-expiry',
        source_ref='owner-command:intent-after-expiry', source={'owner_id': 'controlled-owner'},
    )
    assert value['request_id'] != claim[0] and value['attempts'] == 0
    assert value['status'] == 'pending_admission' and value['run_id'] is None
    assert original._admit(claim[0], claim[2]) is False
    with database.session() as session:
        old = session.get(RiskResearchRequestRecord, claim[0])
        assert old is not None and old.status == 'failed' and old.attempts == 3
        assert old.error_code == 'risk_research_claim_lease_expired'


@pytest.mark.parametrize('fault', ['generation', 'owner', 'duration', 'trigger', 'run', 'event'])
def test_corrupt_expired_final_claim_is_not_retired(database: Database, fault: str):
    original, clock, claim = final_claim(database)
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None
        if fault == 'generation':
            row.lease_generation = 2
        elif fault == 'owner':
            row.lease_owner = 'foreign-worker'
        elif fault == 'duration':
            assert row.lease_expires_at is not None
            row.lease_expires_at += timedelta(seconds=1)
        elif fault == 'run':
            row.run_id = 'foreign-run'
        elif fault == 'event':
            row.event_id = 'foreign-event'
        else:
            trigger = session.scalar(select(RiskResearchTriggerRecord).where(
                RiskResearchTriggerRecord.request_id == claim[0],
            ))
            assert trigger is not None
            trigger.payload_hash = '0' * 64
        session.flush()
        before = tuple(getattr(row, column.name) for column in row.__table__.columns)
    clock[0] += timedelta(seconds=61)
    restarted = RiskResearchAdmissionService(database, enabled=True, clock=lambda: clock[0])
    assert restarted._claim() is None
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, claim[0])
        assert row is not None
        assert tuple(getattr(row, column.name) for column in row.__table__.columns) == before
    assert original._fresh_preflight(claim[0], claim[2]) is False
