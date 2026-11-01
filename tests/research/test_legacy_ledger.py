from datetime import UTC, datetime, timedelta

from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    DshSessionLinkRecord,
    RunRecord,
)
from packages.query_views.decision_desk.service import DecisionDeskQueryService
from tests.evolution.test_research_worker import _admitted_run


def test_ledger_timestamps_preserve_utc_in_public_list_and_detail(tmp_path):
    database, _, run_id = _admitted_run(tmp_path)
    moment = datetime(2026, 9, 30, 2, 40, 0, tzinfo=UTC)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        run.created_at = moment - timedelta(minutes=5)
        run.updated_at = moment
        run.finished_at = moment
    for view in [
        database.latest_runs(1)[0],
        database.get_run_view(run_id),
        database.get_run_inspector(run_id).run,
    ]:
        assert view.updated_at == moment
        assert view.finished_at == moment
        assert view.created_at == moment - timedelta(minutes=5)
        assert view.model_dump(mode="json")["updated_at"].endswith("Z")
    # No mutation of the stored SQLite timestamp is needed.
    with database.session() as session:
        assert session.get(RunRecord, run_id).updated_at == moment.replace(tzinfo=None)


def test_summary_recent_publication_count_excludes_old_and_research_only(tmp_path):
    database, event_id, run_id = _admitted_run(tmp_path)
    now = datetime.now(UTC)
    with database.session() as session:
        for identity, status, age in [
            ("recent", "publish", 1),
            ("limited", "degraded", 2),
            ("old", "publish", 31),
            ("research", "research_only", 1),
            ("rejected", "reject", 1),
        ]:
            session.add(
                ArtifactRecord(
                    artifact_id=identity,
                    run_id=run_id,
                    event_id=event_id,
                    gate_status=status,
                    headline=identity,
                    summary="test",
                    payload_json="{}",
                    created_at=now - timedelta(days=age),
                )
            )
    summary = DecisionDeskQueryService(database).summary()
    assert summary.published_count_30d == 2
    assert summary.inbox.pending_count == 1
    assert summary.inbox.running_count == 0


def test_inspector_uses_durable_dsh_session_identity(tmp_path):
    database, _, run_id = _admitted_run(tmp_path)
    assert database.get_run_inspector(run_id).orchestration.mode == "unknown"
    now = datetime.now(UTC)
    with database.session() as session:
        session.add(
            DshSessionLinkRecord(
                run_id=run_id,
                dsh_session_id="actual-session",
                request_hash="a" * 64,
                state="completed",
                upstream_identity_json="{}",
                plugin_build_hash="b" * 64,
                created_at=now,
                updated_at=now,
            )
        )
    inspector = database.get_run_inspector(run_id)
    assert inspector.orchestration.mode == "dsh_native"
    assert inspector.calls == []
