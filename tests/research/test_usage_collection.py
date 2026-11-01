from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from apps.hub_worker.usage_collection import UsageCollector
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchUsageSnapshotRecord,
    RunRecord,
    UsageCollectionRecord,
    as_utc,
)
from tests.evolution.test_research_worker import NOW
from tests.research.test_usage_view import archive, setup


def collector_setup(tmp_path, monkeypatch):
    home, _, run_id = setup(tmp_path, monkeypatch)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'worker.sqlite3'}")
    now = [NOW]
    collector = UsageCollector(database, home / "sessions", clock=lambda: now[0])
    return home, run_id, database, now, collector


def test_late_archives_and_unchanged_checks(tmp_path, monkeypatch):
    home, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    assert collector.tick() == 1
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert job.status == "waiting_archive"
        assert job.last_snapshot_id is not None
        assert session.query(ResearchUsageSnapshotRecord).count() == 1
    archive(home, "main-session")
    archive(home, "child", "main-session", 40)
    now[0] += timedelta(hours=2)
    assert collector.tick() == 1
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert job.status == "captured" and job.error_code is None
        assert session.query(ResearchUsageSnapshotRecord).count() == 2
        assert session.get(RunRecord, run_id).status == "admitted"
    now[0] += timedelta(hours=2)
    recovered = UsageCollector(db, home / "sessions", clock=lambda: now[0])
    assert recovered.tick() == 1
    with db.session() as session:
        assert session.query(ResearchUsageSnapshotRecord).count() == 2
        assert session.query(UsageCollectionRecord).one().attempts == 3

    app = FastAPI()
    app.include_router(asset_router(db, tmp_path))
    client = TestClient(app)
    response = client.get(f"/v1/research/runs/{run_id}/usage-collection")
    assert response.status_code == 200
    item = response.json()[0]
    assert item["status"] == "captured" and item["attempts"] == 3
    assert item["checked_at"].endswith("Z") and item["next_check_at"].endswith("Z")
    assert "request_id" not in item and "lease_owner" not in item
    assert client.get("/v1/research/runs/absent/usage-collection").status_code == 404


def test_expired_lease_reuses_pending_request(tmp_path, monkeypatch):
    home, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    collector.discover()
    job = collector.claim()
    competitor = UsageCollector(db, home / "sessions", clock=lambda: now[0])
    assert competitor.claim() is None
    from packages.query_views.research_usage import usage_for_session
    from packages.workbench_adapters.usage_ledger import capture_usage
    # Simulate a crash after receipt commit but before queue acknowledgement.
    saved = capture_usage(
        db, home / "sessions", run_id, job["request_id"], binding=("main-session", 1),
        prepared_view=usage_for_session(home / "sessions", run_id, "main-session"),
    )
    now[0] += timedelta(minutes=6)
    reclaimed = competitor.claim()
    assert reclaimed["request_id"] == job["request_id"]
    competitor.collect(reclaimed)
    with db.session() as session:
        assert session.query(ResearchUsageSnapshotRecord).count() == 1
        assert session.query(UsageCollectionRecord).one().last_snapshot_id == saved.snapshot_id
        assert session.get(RunRecord, run_id).status == "admitted"


def test_collection_failure_is_durable_retryable_and_does_not_fail_research(tmp_path, monkeypatch):
    home, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    def broken(*args):
        raise OSError("PRIVATE PATH AND PROVIDER DATA")
    with monkeypatch.context() as patch:
        patch.setattr("apps.hub_worker.usage_collection.usage_for_session", broken)
        assert collector.tick() == 1
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert job.status == "retry_wait" and job.error_code == "usage_OSError"
        assert job.lease_owner is None and job.request_id
        assert session.get(RunRecord, run_id).status == "admitted"
    now[0] += timedelta(hours=2)
    collector.tick()
    with db.session() as session:
        assert session.query(UsageCollectionRecord).one().status == "captured"


def test_historical_generation_keeps_its_original_session_binding(tmp_path, monkeypatch):
    home, run_id, db, _, collector = collector_setup(tmp_path, monkeypatch)
    archive(home, "main-session", tokens=20)
    archive(home, "new-session", tokens=90)
    with db.session() as session:
        session.add(DshSessionPromptRecord(
            run_id=run_id, generation=1, dsh_session_id="main-session", request_id="first",
            request_hash="a" * 64, prompt="PRIVATE PROMPT", created_at=NOW,
        ))
        link = session.get(DshSessionLinkRecord, run_id)
        link.generation = 2
        link.dsh_session_id = "new-session"
    assert collector.tick() == 2
    from packages.workbench_adapters.usage_ledger import usage_history
    receipts = usage_history(db, run_id)["items"]
    by_generation = {item.session_generation: item.view.sessions[0].recorded_tokens
                     for item in receipts}
    assert by_generation == {1: 20, 2: 90}
    assert all("PRIVATE PROMPT" not in item.model_dump_json() for item in receipts)


@pytest.mark.parametrize("missing", [False, True])
def test_unchanged_terminal_archives_back_off_without_hiding_gaps(tmp_path, monkeypatch, missing):
    home, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    now[0] = datetime.now(UTC)
    archive(home, "main-session", missing=missing)
    with db.session() as session:
        session.get(RunRecord, run_id).status = "completed"
    collector.tick()
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        receipt = session.get(ResearchUsageSnapshotRecord, job.last_snapshot_id)
        original = (receipt.snapshot_id, receipt.payload_json, receipt.payload_sha256)
        now[0] = as_utc(receipt.recorded_at) + timedelta(hours=12)
    restarted = UsageCollector(db, home / "sessions", clock=lambda: now[0])
    assert restarted.tick() == 1
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert job.status == ("partial" if missing else "captured")
        assert as_utc(job.next_check_at) - now[0] == timedelta(hours=6)
        assert session.query(ResearchUsageSnapshotRecord).count() == 1
        receipt = session.get(ResearchUsageSnapshotRecord, job.last_snapshot_id)
        assert (receipt.snapshot_id, receipt.payload_json, receipt.payload_sha256) == original
    # A later archive update is collected and resets the delay, without changing history.
    archive(home, "main-session", tokens=90)
    now[0] += timedelta(hours=6)
    assert restarted.tick() == 1
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert as_utc(job.next_check_at) - now[0] == timedelta(seconds=120)
        assert session.query(ResearchUsageSnapshotRecord).count() == 2
        assert session.get(ResearchUsageSnapshotRecord, original[0]).payload_json == original[1]


def test_live_sessions_keep_short_checks_and_new_jobs_bypass_old_backlog(tmp_path, monkeypatch):
    home, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    archive(home, "main-session")
    collector.tick()
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        assert as_utc(job.next_check_at) - now[0] == timedelta(seconds=30)
        job.next_check_at = now[0] - timedelta(days=2)
        session.get(RunRecord, run_id).status = "completed"
        session.add(UsageCollectionRecord(
            job_id="fresh", run_id=run_id, session_id="new-session", generation=2,
            status="pending", next_check_at=now[0], created_at=now[0], attempts=0,
        ))
    assert collector.claim()["job_id"] == "fresh"
    # Every fourth tick slot ignores priority to guarantee old work can progress.
    assert collector.claim(prioritize=False)["job_id"] != "fresh"


def test_batch_is_bounded_and_reserves_oldest_due_slots(tmp_path, monkeypatch):
    _, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    with db.session() as session:
        session.get(RunRecord, run_id).status = "completed"
        for index in range(24):
            historical = index < 4
            session.add(UsageCollectionRecord(
                job_id=f"job-{index:02}", run_id=run_id, session_id=f"session-{index}",
                generation=index + 1, status="partial" if historical else "pending",
                next_check_at=now[0] - timedelta(hours=1) if historical else now[0],
                checked_at=now[0] - timedelta(hours=2) if historical else None,
                created_at=now[0], attempts=0,
            ))
    completed = []
    monkeypatch.setattr(collector, "discover", lambda: None)
    def record_check(job):
        completed.append(job["job_id"])
        with db.session() as session:
            row = session.get(UsageCollectionRecord, job["job_id"])
            row.checked_at = now[0]
            row.next_check_at = now[0] + timedelta(hours=1)
            row.lease_owner = row.lease_until = None
    monkeypatch.setattr(collector, "collect", record_check)
    assert collector.tick() == 16
    assert completed[0] == "job-04"  # Fresh work starts before older history.
    assert set(f"job-{i:02}" for i in range(4)).issubset(completed)
    assert len(set(completed)) == 16
    assert collector.tick(time_budget_seconds=0) == 0
    assert len(completed) == 16


def test_global_queue_overview_is_read_only_and_keeps_lateness_separate_from_gaps(
    tmp_path, monkeypatch,
):
    _, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: now[0]))
    client = TestClient(app)
    assert client.get("/v1/operations/usage-collection").json()["session_versions"] == 0
    collector.discover()
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        job.status = "partial"
        job.checked_at = now[0] - timedelta(minutes=20)
        job.next_check_at = now[0] - timedelta(minutes=11)
        job.lease_until = now[0] + timedelta(minutes=1)
        job.lease_owner = "PRIVATE OWNER"
        session.add(UsageCollectionRecord(
            job_id="future-check", run_id=run_id, session_id="PRIVATE SESSION", generation=2,
            status="waiting_archive", next_check_at=now[0] + timedelta(hours=1),
            created_at=now[0], attempts=0,
        ))
    response = client.get("/v1/operations/usage-collection")
    assert response.status_code == 200
    view = response.json()
    assert view["session_versions"] == 2 and view["unique_runs"] == 1
    assert view["statuses"] == {"partial": 1, "waiting_archive": 1}
    assert view["due"] == view["overdue_10m"] == view["checking"] == 1
    assert view["oldest_due_at"].endswith("Z") and view["observed_at"].endswith("Z")
    assert "PRIVATE" not in response.text
    with db.session() as session:
        assert session.query(ResearchUsageSnapshotRecord).count() == 0
    from tools.ops.audit_usage_queue import audit
    inventory = audit(tmp_path / "worker.sqlite3", now=now[0])
    assert inventory["states"] == view["statuses"]
    assert inventory["overdue_10m"] == view["overdue_10m"]
    assert inventory["verified_receipts"] == 0


def test_queue_navigation_filters_paginates_and_never_collects(tmp_path, monkeypatch):
    _, run_id, db, now, collector = collector_setup(tmp_path, monkeypatch)
    collector.discover()
    with db.session() as session:
        job = session.query(UsageCollectionRecord).one()
        job.status = "partial"
        job.next_check_at = now[0] - timedelta(minutes=10)  # Boundary is not overdue.
        for index in range(3):
            session.add(UsageCollectionRecord(
                job_id=f"missing-{index}", run_id=run_id if index < 2 else "missing-run",
                session_id="DO NOT EXPOSE SESSION", generation=index + 2,
                status="waiting_archive", error_code="usage_archive_unavailable",
                next_check_at=now[0] - timedelta(minutes=11), created_at=now[0],
                attempts=3, lease_owner="DO NOT EXPOSE OWNER",
                lease_until=now[0] + timedelta(minutes=1) if index == 0 else now[0],
            ))
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: now[0]))
    client = TestClient(app)
    url = "/v1/operations/usage-collection/jobs"
    first = client.get(url, params={"filter": "waiting_archive", "limit": 2})
    assert first.status_code == 200
    page = first.json()
    assert page["total"] == 3 and page["has_more"]
    assert [item["job_id"] for item in page["items"]] == ["missing-0", "missing-1"]
    assert page["items"][0]["checking"] is True
    assert page["items"][1]["checking"] is False
    assert "DO NOT EXPOSE" not in first.text
    assert page["items"][0]["run_created_at"].endswith("Z")
    last = client.get(url, params={"filter": "waiting_archive", "limit": 2, "offset": 999}).json()
    assert last["offset"] == 2 and not last["has_more"]
    assert last["items"][0]["run_available"] is False
    assert last["items"][0]["run_created_at"] is None
    assert client.get(url, params={"filter": "overdue"}).json()["total"] == 3
    assert client.get(url, params={"filter": "checking"}).json()["total"] == 1
    assert client.get(url, params={"filter": "partial"}).json()["total"] == 1
    empty = client.get(url, params={"filter": "captured", "offset": 99}).json()
    assert empty["total"] == empty["offset"] == 0 and empty["items"] == []
    for params in ({"filter": "invalid"}, {"offset": -1}, {"limit": 101}, {"limit": 0}):
        assert client.get(url, params=params).status_code == 422
    with db.session() as session:
        assert session.query(ResearchUsageSnapshotRecord).count() == 0
        assert session.query(UsageCollectionRecord).count() == 4
