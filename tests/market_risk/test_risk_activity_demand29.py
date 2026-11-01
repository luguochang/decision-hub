"""Prepared isolated controls; no live Host, API, production DB, or model calls."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRecord,
    DshHostActivityScope,
    DshHostActivitySnapshot,
    activity_binding,
    activity_targets_in_session,
    freeze_activity_snapshot_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import content_hash
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    ServiceRuntimeConfigRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope

NOW = datetime(2026, 10, 9, 2, 30, tzinfo=UTC)
ROOT_RUN = "run:old-risk-activity"


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'risk-demand.sqlite3'}")
    database.create_all()
    try:
        yield database
    finally:
        database.engine.dispose()


def _seed(
    database: Database, *, instrument: str = "BTC-USDT", attempts: int = 2,
) -> tuple[str, str]:
    command = f"control-{instrument}"
    service = RiskResearchAdmissionService(
        database, enabled=True, clock=lambda: NOW - timedelta(seconds=10),
    )
    service.stage(
        instrument, request_id=command, source_ref=f"owner-command:{command}",
        source={"owner_id": "controlled-owner"},
    )
    request_id = "riskresearch_" + canonical_hash([
        spot_scope(instrument).scope_id, f"manual:{command}",
    ])[:40]
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None
        request.status, request.error_code = "pending_admission", "study_host_terminal_unknown"
        request.attempts = request.lease_generation = attempts
        request.updated_at = NOW
        request.available_at = NOW + timedelta(seconds=60 * 2 ** (attempts - 1))
        study_id = ensure_study(
            session, question_key="spot-market-risk.v1", title="controlled",
            market="crypto", instrument=instrument, horizon_scope="30m,24h,72h",
            identity_basis="exact_spot_monitor", now=NOW - timedelta(days=2),
        )
        session.add(RunRecord(
            run_id=ROOT_RUN, event_id="event:old-risk-activity", study_id=study_id,
            status="failed", strategy_version="research.v1", admission_origin="manual",
            created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=1),
            finished_at=NOW - timedelta(days=1),
        ))
        session.add(DshSessionLinkRecord(
            run_id=ROOT_RUN, dsh_session_id="dsh:old-risk-activity", request_hash="b" * 64,
            generation=1, state="running", upstream_identity_json="{}",
            plugin_build_hash="c" * 64, last_seq=5,
            accepted_at=NOW - timedelta(days=2), terminal_at=None,
            created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=1),
        ))
    return request_id, study_id


def _originals(session: Session) -> tuple[tuple[tuple[object, ...], ...], ...]:
    return tuple(
        tuple(tuple(row) for row in session.execute(select(model.__table__).order_by(
            *model.__table__.primary_key.columns,  # pyright: ignore[reportAttributeAccessIssue]
        )))
        for model in (
            RiskResearchRequestRecord, RiskResearchTriggerRecord, StudyRecord, RunRecord,
            DshSessionLinkRecord, RunRoleOutputRecord, ServiceRuntimeConfigRecord,
        )
    )


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
@pytest.mark.parametrize("attempts", [1, 2])
def test_original_blocked_request_maps_exact_study_during_saved_backoff(
    database: Database, instrument: str, attempts: int,
) -> None:
    _request_id, study_id = _seed(database, instrument=instrument, attempts=attempts)
    backoff = 60 * 2 ** (attempts - 1)
    with database.session() as session:
        before = _originals(session)
        for seconds in (0, backoff - 1, backoff, backoff + 60):
            target = activity_targets_in_session(session, now=NOW + timedelta(seconds=seconds))
            assert target is not None
            assert [(item.run_id, item.study_id) for item in target.records] == [
                (ROOT_RUN, study_id),
            ]
        assert activity_targets_in_session(
            session, now=NOW + timedelta(seconds=backoff + 61),
        ) is None
        assert _originals(session) == before


def test_request_status_identity_time_and_lease_fail_closed(database: Database) -> None:
    request_id, _study_id = _seed(database)
    mutations: tuple[tuple[str, object], ...] = (
        ("status", "claiming"), ("status", "queued"), ("status", "failed"),
        ("status", "completed"), ("error_code", "study_research_active"),
        ("run_id", "run:foreign"), ("event_id", "event:foreign"),
        ("scope_id", "crypto:spot:other:BTC-USDT:trade:1m"),
        ("instrument", "BTC-USDT-SWAP"), ("instrument", "ETH-USDT"),
        ("attempts", 0), ("attempts", 3), ("lease_generation", 1),
        ("lease_owner", "other-worker"), ("lease_expires_at", NOW + timedelta(seconds=1)),
        ("lease_expires_at", NOW - timedelta(seconds=1)),
        ("created_at", NOW + timedelta(seconds=1)), ("updated_at", NOW + timedelta(seconds=1)),
        ("available_at", NOW + timedelta(seconds=121)),
        ("available_at", NOW - timedelta(seconds=61)),
    )
    with database.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request is not None
        for name, value in mutations:
            original = getattr(request, name)
            setattr(request, name, value)
            session.flush()
            assert activity_targets_in_session(session, now=NOW) is None, name
            setattr(request, name, original)
            session.flush()
        assert activity_targets_in_session(session, now=NOW) is not None


def test_trigger_original_hash_binding_and_owner_command_fail_closed(database: Database) -> None:
    request_id, _study_id = _seed(database)
    with database.session() as session:
        trigger = session.scalar(select(RiskResearchTriggerRecord).where(
            RiskResearchTriggerRecord.request_id == request_id,
        ))
        assert trigger is not None
        payload = json.loads(trigger.payload_json)
        originals = (trigger.payload_json, trigger.payload_hash)
        for changed in (
            {**payload, "instrument": "ETH-USDT"},
            {**payload, "scope_id": "foreign"},
            {**payload, "source_ref": "owner-command:other"},
            {**payload, "source": {"owner_id": ""}},
            {**payload, "source": {"owner_id": "controlled-owner", "extra": "bad"}},
        ):
            trigger.payload_json = json.dumps(changed)
            trigger.payload_hash = content_hash(changed)
            session.flush()
            assert activity_targets_in_session(session, now=NOW) is None
        trigger.payload_json, trigger.payload_hash = originals
        for name, value in (
            ("payload_hash", "0" * 64), ("payload_json", "not-json"),
            ("scope_id", "foreign"), ("kind", "unrecognized"),
            ("source_ref", "manual:untrusted"), ("trigger_key", "manual:other"),
            ("recorded_at", NOW + timedelta(seconds=1)), ("request_id", "riskresearch_foreign"),
        ):
            original = getattr(trigger, name)
            setattr(trigger, name, value)
            session.flush()
            assert activity_targets_in_session(session, now=NOW) is None, name
            setattr(trigger, name, original)
            session.flush()
        # Even a self-consistent hash cannot upgrade an untrusted manual source.
        changed = {**payload, "source_ref": "manual:untrusted"}
        trigger.source_ref = "manual:untrusted"
        trigger.payload_json = json.dumps(changed)
        trigger.payload_hash = content_hash(changed)
        session.flush()
        assert activity_targets_in_session(session, now=NOW) is None


def test_existing_study_and_terminal_unleased_original_are_required(database: Database) -> None:
    _request_id, study_id = _seed(database)
    with database.session() as session:
        study = session.get(StudyRecord, study_id)
        run = session.get(RunRecord, ROOT_RUN)
        link = session.get(DshSessionLinkRecord, ROOT_RUN)
        assert study is not None and run is not None and link is not None
        for record, name, value in (
            (study, "status", "paused"), (study, "scope_key", "foreign"),
            (study, "market", "foreign"), (study, "instrument", "ETH-USDT"),
            (study, "horizon_scope", "24h"), (study, "question_key", "other"),
            (study, "identity_basis", "legacy_projection"),
            (run, "study_id", "study:foreign"), (run, "status", "running"),
            (run, "finished_at", None), (run, "finished_at", NOW + timedelta(seconds=1)),
            (run, "lease_owner", "live-worker"),
            (run, "lease_expires_at", NOW + timedelta(seconds=1)),
            (link, "request_hash", "invalid"),
        ):
            original = getattr(record, name)
            setattr(record, name, value)
            session.flush()
            assert activity_targets_in_session(session, now=NOW) is None, name
            setattr(record, name, original)
            session.flush()
        link.state, link.terminal_at = "failed", NOW - timedelta(days=1)
        session.flush()
        assert activity_targets_in_session(session, now=NOW) is None


def test_collection_demand_requires_real_inactive_receipt_and_preserves_old_link(
    database: Database,
) -> None:
    request_id, study_id = _seed(database)
    scope = DshHostActivityScope(
        host_instance_id=UUID("5f4d4ff3-e9e0-44ba-a858-24b684c5f0bf"), process_id=1234,
        started_at=NOW - timedelta(minutes=5), hub_base_url="http://127.0.0.1:8000",
        workspace_id="workspace:controlled", workspace_cwd_hash="a" * 64,
    )
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW) is not None
        assert research_admission_guard_in_session(
            session, study_id=study_id, now=NOW, admission_origin="manual",
        ).reason_code == "study_host_terminal_unknown"
        run, link = session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        assert run is not None and link is not None
        binding = activity_binding(run, link)
    snapshot = DshHostActivitySnapshot(
        schema_version="dsh-host-activity-snapshot.v1", scope=scope, observed_at=NOW,
        expires_at=NOW + timedelta(seconds=30), census_complete=True,
        records=[DshHostActivityRecord(
            **binding.model_dump(), state="current_scope_inactive", presence="absent",
            running=False, queue_count=0, active_job_count=0, descendant_ids=[],
            fence_id="fence:controlled", reason_code="host_activity_lineage_quiescent",
            terminal_state="unknown",
        )],
    )
    with database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        assert freeze_activity_snapshot_in_session(
            session, snapshot, expected_scope=scope, transport_fingerprint="d" * 64,
            recorded_at=NOW, raw_snapshot_json=snapshot.model_dump_json(),
        ) == 1
    with database.session() as session:
        assert research_admission_guard_in_session(
            session, study_id=study_id, now=NOW, admission_origin="manual",
        ).reason_code is None
        assert research_admission_guard_in_session(
            session, study_id=study_id, now=NOW + timedelta(seconds=31), admission_origin="manual",
        ).reason_code == "study_host_terminal_unknown"
        # Demand keeps the authenticated collector refreshing throughout retry 2.
        assert activity_targets_in_session(session, now=NOW + timedelta(seconds=90)) is not None
        request = session.get(RiskResearchRequestRecord, request_id)
        run, link = session.get(RunRecord, ROOT_RUN), session.get(DshSessionLinkRecord, ROOT_RUN)
        assert (
            request is not None and request.status == "pending_admission" and request.run_id is None
        )
        assert run is not None and run.status == "failed" and run.artifact_id is None
        assert link is not None and link.state == "running" and link.terminal_at is None
        assert activity_binding(run, link) == binding
        assert session.scalar(select(RunRoleOutputRecord.output_id).where(
            RunRoleOutputRecord.role.like("dsh_authenticated_terminal:%"),
        )) is None
