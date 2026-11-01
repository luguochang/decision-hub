"""Isolated SQLite controls for current-scope activity; no Host or Provider is called."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    SERVICE_ID,
    DshHostActivityRecord,
    DshHostActivityRequest,
    DshHostActivityScope,
    DshHostActivitySnapshot,
    activity_binding,
    activity_link_fingerprint,
    activity_targets_in_session,
    freeze_activity_snapshot_in_session,
    host_activity_inactive_in_session,
    invalidate_activity_scope_in_session,
    register_verified_activity_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.runtime_configuration import (
    list_runtime_configurations,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    Database,
    DshSessionLinkRecord,
    ReviewScheduleRecord,
    RunRecord,
    RunRoleOutputRecord,
    ServiceHeartbeatRecord,
    ServiceRuntimeConfigRecord,
    StudyRecord,
)

# The existing full published-report fixture exercises the actual scheduler writer.
from tests.research.test_tracking_continuity import (
    _root as published_root,  # pyright: ignore[reportPrivateUsage]
)

NOW = datetime(2026, 10, 3, 14, 43, tzinfo=UTC)
STUDY = "study:activity"
SCOPE = DshHostActivityScope(
    host_instance_id=UUID("5f4d4ff3-e9e0-44ba-a858-24b684c5f0bf"),
    process_id=1234, started_at=NOW - timedelta(minutes=5),
    hub_base_url="http://127.0.0.1:8000", workspace_id="workspace:product",
    workspace_cwd_hash="a" * 64,
)


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'activity.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(StudyRecord(
            study_id=STUDY, scope_key="scope:activity", title="BTC 持续跟踪",
            market="crypto", instrument="BTC-USDT-SWAP", horizon_scope="30m,24h,72h",
            question_key="activity", identity_basis="controlled", status="active",
            created_at=NOW - timedelta(days=2), updated_at=NOW,
        ))
        session.add(RunRecord(
            run_id="run:old", event_id="event:old", study_id=STUDY, status="failed",
            strategy_version="research.v1", admission_origin="manual",
            created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=1),
            finished_at=NOW - timedelta(days=1),
        ))
        session.add(DshSessionLinkRecord(
            run_id="run:old", dsh_session_id="dsh:old", request_hash="b" * 64,
            generation=1, state="running", upstream_identity_json="{}",
            plugin_build_hash="c" * 64, last_seq=5,
            accepted_at=NOW - timedelta(days=2), terminal_at=None,
            created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=1),
        ))
    try:
        yield database
    finally:
        database.engine.dispose()


def _snapshot(
    database: Database, *, state: str = "current_scope_inactive", at: datetime = NOW,
    scope: DshHostActivityScope = SCOPE,
) -> DshHostActivitySnapshot:
    with database.session() as session:
        run = session.get(RunRecord, "run:old")
        link = session.get(DshSessionLinkRecord, "run:old")
        assert run is not None and link is not None
        binding = activity_binding(run, link)
    body = binding.model_dump()
    body.update({
        "state": state, "presence": "present", "running": state == "active",
        "queue_count": 0, "active_job_count": 0, "descendant_ids": ["dsh:old:child"],
        "fence_id": "fence:old", "reason_code": "host_activity_lineage_quiescent",
        "terminal_state": "unknown",
    })
    return DshHostActivitySnapshot(
        schema_version="dsh-host-activity-snapshot.v1", scope=scope,
        observed_at=at, expires_at=at + timedelta(seconds=30), census_complete=True,
        records=[DshHostActivityRecord.model_validate(body)],
    )


def _freeze(database: Database, snapshot: DshHostActivitySnapshot, *, at: datetime = NOW) -> int:
    with database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        return freeze_activity_snapshot_in_session(
            session, snapshot, expected_scope=snapshot.scope, transport_fingerprint="d" * 64,
            recorded_at=at, raw_snapshot_json=snapshot.model_dump_json(indent=2),
        )


def _inactive(database: Database, *, at: datetime = NOW) -> bool:
    with database.session() as session:
        run = session.get(RunRecord, "run:old")
        link = session.get(DshSessionLinkRecord, "run:old")
        assert run is not None and link is not None
        return host_activity_inactive_in_session(session, run=run, link=link, now=at)


@pytest.mark.parametrize("presence", ["present", "absent"])
def test_inactive_receipt_clears_mutex_without_changing_link_or_minting_terminal(
    database: Database, presence: str,
) -> None:
    body = _snapshot(database).model_dump()
    body["records"][0]["presence"] = presence
    snapshot = DshHostActivitySnapshot.model_validate(body)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, "run:old")
        assert link is not None
        original = activity_link_fingerprint(link)
        assert research_admission_guard_in_session(
            session, study_id=STUDY, now=NOW,
        ).reason_code == "study_host_terminal_unknown"
    assert _freeze(database, snapshot) == 1
    assert _freeze(database, snapshot) == 0
    assert _inactive(database)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, "run:old")
        assert link is not None
        assert activity_link_fingerprint(link) == original
        assert link.state == "running" and link.terminal_at is None
        assert research_admission_guard_in_session(
            session, study_id=STUDY, now=NOW,
        ).reason_code is None
        assert research_admission_guard_in_session(
            session, study_id=STUDY, now=NOW, max_daily_research=0,
        ).reason_code == "research_admission_budget_exhausted"
        assert session.query(ServiceHeartbeatRecord).count() == 0
        assert session.query(RunRoleOutputRecord).count() == 2
        assert session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.role.like("dsh_authenticated_terminal:%"),
        ).count() == 0
    configurations = list_runtime_configurations(database)
    assert configurations[0].service_id == SERVICE_ID
    assert configurations[0].settings["workspace_id"] == SCOPE.workspace_id


@pytest.mark.parametrize("state", ["active", "unknown"])
def test_active_and_unknown_observations_never_clear_mutex(database: Database, state: str) -> None:
    _freeze(database, _snapshot(database, state=state))
    assert not _inactive(database)


@pytest.mark.parametrize(
    "field,value",
    [
        ("running", "false"), ("running", 0), ("queue_count", True),
        ("queue_count", 1), ("active_job_count", 1), ("fence_id", None),
        ("presence", "unknown"), ("terminal_state", "failed"),
        ("generation", 9007199254740992),
    ],
)
def test_malformed_or_unsettled_inactive_record_is_rejected(
    database: Database, field: str, value: object,
) -> None:
    body = _snapshot(database).model_dump(mode="json")
    body["records"][0][field] = value
    with pytest.raises(ValueError):
        DshHostActivitySnapshot.model_validate(body)


@pytest.mark.parametrize("value", ["true", 1, False])
def test_census_boolean_is_strict_and_complete(database: Database, value: object) -> None:
    body = _snapshot(database).model_dump(mode="json")
    body["census_complete"] = value
    with pytest.raises(ValueError):
        DshHostActivitySnapshot.model_validate(body)


def test_duplicate_session_is_rejected(database: Database) -> None:
    snapshot = _snapshot(database)
    # Use actual six-field bindings; another Run cannot claim the same Session.
    first = {field: getattr(snapshot.records[0], field) for field in (
        "run_id", "study_id", "dsh_session_id", "generation", "request_hash", "link_fingerprint",
    )}
    second = dict(first, run_id="run:another")
    with pytest.raises(ValueError, match="duplicate_session"):
        DshHostActivityRequest.model_validate({"records": [first, second]})


@pytest.mark.parametrize("change", ["link", "lease", "active", "study"])
def test_change_between_probe_and_writer_is_rejected(database: Database, change: str) -> None:
    snapshot = _snapshot(database)
    with database.session() as session:
        run = session.get(RunRecord, "run:old")
        link = session.get(DshSessionLinkRecord, "run:old")
        assert run is not None and link is not None
        if change == "link":
            link.last_seq += 1
        elif change == "lease":
            run.lease_owner, run.lease_expires_at = "worker:new", NOW + timedelta(minutes=1)
        elif change == "active":
            run.status = "running"
        else:
            run.study_id = "study:another"
    with pytest.raises(ValueError, match="original_binding_invalid"):
        _freeze(database, snapshot)
    assert not _inactive(database)
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0
        assert session.query(ServiceRuntimeConfigRecord).count() == 0


def test_expired_or_wrong_current_scope_is_rejected(database: Database) -> None:
    snapshot = _snapshot(database)
    wrong = SCOPE.model_copy(update={"workspace_id": "workspace:other"})
    with database.session() as session, pytest.raises(ValueError, match="scope_mismatch"):
        freeze_activity_snapshot_in_session(
            session, snapshot, expected_scope=wrong, transport_fingerprint="d" * 64,
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="observation_expired"):
        _freeze(database, snapshot, at=NOW + timedelta(seconds=30))
    _freeze(database, snapshot)
    assert not _inactive(database, at=NOW + timedelta(seconds=30))


def test_new_boot_or_failed_probe_invalidates_old_scope_without_modifying_receipt(
    database: Database,
) -> None:
    snapshot = _snapshot(database)
    _freeze(database, snapshot)
    with database.session() as session:
        original_outputs = {
            row.output_id: row.payload_json for row in session.query(RunRoleOutputRecord)
        }
        session.execute(text("BEGIN IMMEDIATE"))
        invalidate_activity_scope_in_session(
            session, recorded_at=NOW + timedelta(seconds=1), reason_code="host_unreachable",
        )
    assert not _inactive(database, at=NOW + timedelta(seconds=1))
    fresh = SCOPE.model_copy(update={
        "host_instance_id": UUID("d82869da-5c17-4671-b8ed-b3e6cb25f643"),
        "started_at": NOW + timedelta(seconds=2), "process_id": 2345,
    })
    with database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        register_verified_activity_scope_in_session(
            session, expected_scope=fresh, transport_fingerprint="e" * 64,
            recorded_at=NOW + timedelta(seconds=3),
        )
    assert not _inactive(database, at=NOW + timedelta(seconds=3))
    with database.session() as session:
        assert original_outputs == {
            row.output_id: row.payload_json for row in session.query(RunRoleOutputRecord)
        }


def test_same_boot_refresh_preserves_valid_old_receipt_and_active_update_supersedes_it(
    database: Database,
) -> None:
    _freeze(database, _snapshot(database))
    with database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        register_verified_activity_scope_in_session(
            session, expected_scope=SCOPE, transport_fingerprint="d" * 64,
            recorded_at=NOW + timedelta(seconds=1),
        )
    assert _inactive(database, at=NOW + timedelta(seconds=1))
    at = NOW + timedelta(seconds=2)
    _freeze(database, _snapshot(database, state="active", at=at), at=at)
    assert not _inactive(database, at=at)


@pytest.mark.parametrize("target", ["snapshot", "config", "receipt", "link"])
def test_corrupt_original_or_changed_link_fails_closed(database: Database, target: str) -> None:
    _freeze(database, _snapshot(database))
    with database.session() as session:
        if target == "link":
            link = session.get(DshSessionLinkRecord, "run:old")
            assert link is not None
            link.generation += 1
        elif target == "config":
            config = session.query(ServiceRuntimeConfigRecord).one()
            config.settings_json = "{}"
        else:
            prefix = "dsh_activity_snapshot:%" if target == "snapshot" else "dsh_activity:%"
            output = session.query(RunRoleOutputRecord).filter(
                RunRoleOutputRecord.role.like(prefix),
            ).one()
            output.payload_json = "{}"
    assert not _inactive(database)


def test_targets_require_real_demand_and_include_fresh_proof_for_scope_refresh(
    database: Database,
) -> None:
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW) is None
        session.add(ReviewScheduleRecord(
            review_plan_id="plan:need", parent_run_id="run:old", horizon="24h",
            trigger_type="time", trigger_ref="controlled", trigger_hash="f" * 64,
            trigger_at=NOW + timedelta(seconds=1), status="planned",
            created_at=NOW, updated_at=NOW,
        ))
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW) is None
        needed = activity_targets_in_session(session, now=NOW + timedelta(seconds=1))
        assert needed is not None and [item.run_id for item in needed.records] == ["run:old"]
    _freeze(database, _snapshot(database))
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW + timedelta(seconds=1)) is not None
        plan = session.get(ReviewScheduleRecord, "plan:need")
        assert plan is not None
        plan.status = "admitted"
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW + timedelta(seconds=1)) is None
        session.add(AdmissionDecisionRecord(
            decision_id="decision:need", attempt_id="attempt:need", event_id="event:old",
            study_id=STUDY, source_id="source:controlled", strategy_version="research.v1",
            policy_version="policy:controlled", decision="blocked",
            reason_code="study_host_terminal_unknown", details_json=json.dumps({}),
            decided_at=NOW,
        ))
    with database.session() as session:
        assert activity_targets_in_session(session, now=NOW + timedelta(seconds=1)) is not None
        assert activity_targets_in_session(session, now=NOW + timedelta(seconds=61)) is None


def test_borrowed_reader_reloads_link_changed_by_another_sqlite_session(database: Database) -> None:
    _freeze(database, _snapshot(database))
    with database.session() as reader:
        run = reader.get(RunRecord, "run:old")
        link = reader.get(DshSessionLinkRecord, "run:old")
        assert run is not None and link is not None
        with database.session() as writer:
            writer.execute(text("BEGIN IMMEDIATE"))
            current = writer.get(DshSessionLinkRecord, "run:old")
            assert current is not None
            current.last_seq += 1
        assert not host_activity_inactive_in_session(reader, run=run, link=link, now=NOW)


def test_real_scheduler_admits_one_child_after_current_inactive_proof(database: Database) -> None:
    root, study_id = published_root(database, finished_at=NOW - timedelta(minutes=15))
    with database.session() as session:
        old = session.get(RunRecord, "run:old")
        assert old is not None
        old.study_id = study_id
    service = ReviewScheduleService(database, clock=lambda: NOW)
    assert service.plan_due() == ()
    with database.session() as session:
        before = activity_targets_in_session(session, now=NOW)
        assert before is not None and [item.run_id for item in before.records] == ["run:old"]
    _freeze(database, _snapshot(database))
    admitted = service.plan_due()
    assert len(admitted) == 1
    assert service.plan_due() == ()
    with database.session() as session:
        child = session.get(RunRecord, admitted[0].child_run_id)
        assert child is not None and child.parent_run_id == root and child.study_id == study_id
        assert child.admission_origin == "scheduled_recheck" and child.status == "admitted"
        old_link = session.get(DshSessionLinkRecord, "run:old")
        assert old_link is not None and old_link.state == "running" and old_link.terminal_at is None
