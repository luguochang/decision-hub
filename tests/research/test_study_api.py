from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.study_routes import study_router
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from tests.research.test_study_identity import NOW, setup


def test_api_recent_catchup_uses_research_policy_and_preserves_live_cursor(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from apps.hub_api.main import create_app
    from packages.contracts_py.decision_hub_contracts.models import SourceManifest
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, SourceStateRecord
    from packages.source_adapters.official_feeds import OfficialFeedSource

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    now = datetime.now(UTC)
    body = f"""<rss><channel><item><guid>release</guid><title>FOMC statement</title>
    <pubDate>{(now - timedelta(hours=2)).isoformat()}</pubDate></item>
    <item><guid>notice</guid><title>Routine bank notice</title>
    <pubDate>{(now - timedelta(hours=1)).isoformat()}</pubDate></item></channel></rss>"""

    async def fetch(_url):
        return 200, body

    source = OfficialFeedSource(
        SourceManifest(source_id="fed-press", source_type="official_feed", version="fixture.v1"),
        "https://example.test/feed",
        fetcher=fetch,
        bootstrap_latest=True,
    )
    db = setup(tmp_path)
    app = create_app(db, source_connectors=[source], sources_enabled=True)
    client = TestClient(app)
    first = client.post("/v1/sources/fed-press/poll").json()
    assert first["accepted_count"] == 0
    after = client.post("/v1/sources/fed-press/poll?mode=catchup").json()
    assert after["error_code"] is None and after["accepted_count"] == 1
    assert after["cursor"] == first["cursor"]
    with db.session() as session:
        research = session.query(RunRecord).filter_by(strategy_version="research.v1").one()
        assert research.status == "admitted" and research.admission_origin == "automatic"
        assert session.get(SourceStateRecord, "fed-press").cursor == first["cursor"]
    decisions = client.get("/v1/discovery/decisions?source_id=fed-press").json()
    assert any(
        d["details"]["trigger_type"] == "catchup" and d["run_id"] == research.run_id
        for d in decisions["items"]
    )
    repeated = client.post("/v1/sources/fed-press/poll?mode=catchup").json()
    assert repeated["accepted_count"] == 0
    with db.session() as session:
        assert session.query(RunRecord).filter_by(strategy_version="research.v1").count() == 1


def test_study_api_pagination_and_old_run_deep_link(tmp_path):
    db = setup(tmp_path)
    admission = AdmissionService(db, clock=lambda: NOW)
    event, _, _ = admission.admit(ObservationCreate(text="FOMC 数据审查", source_id="manual"))
    for n in range(3):
        run_id, _ = RunService(db, clock=lambda n=n: NOW + timedelta(minutes=n)).create(
            event, f"run-{n}"
        )
        CommitDecisionService(db, clock=lambda n=n: NOW + timedelta(minutes=n)).commit(
            run_id, event, {"summary": str(n)}, GateResult(GateStatus.research_only, [])
        )
    app = FastAPI()
    app.include_router(study_router(db))
    client = TestClient(app)
    study = client.get(f"/v1/runs/{run_id}/study").json()
    assert study["title"] == "FOMC 数据审查" and study["created_at"].endswith("Z")
    listing = client.get("/v1/studies?q=FOMC&limit=1").json()
    assert listing["total"] == 1
    first = client.get(f"/v1/studies/{study['study_id']}?limit=1").json()
    second = client.get(f"/v1/studies/{study['study_id']}?limit=1&offset=1").json()
    assert first["total"] == 3
    assert first["revisions"][0]["source_run_id"] == run_id
    assert first["revisions"][0]["previous_revision_id"] == second["revisions"][0]["revision_id"]
    assert client.get("/v1/studies?limit=0").status_code == 422
    assert client.get("/v1/studies/not-found").status_code == 404
    assert client.get("/v1/discovery/decisions").json()["total"] == 0


def test_study_activity_uses_own_research_tasks_and_actual_report_binding(tmp_path):
    from packages.kernel.decision_hub_kernel.persistence.db import (
        AdmissionDecisionRecord,
        ArtifactRecord,
        ReviewScheduleRecord,
        RunRecord,
    )

    db = setup(tmp_path)
    service = RunService(db, clock=lambda: NOW)
    event, _, _ = AdmissionService(db, clock=lambda: NOW).admit(
        ObservationCreate(text="Activity test", source_id="manual")
    )
    baseline, _ = service.create(event, "baseline")
    ids = [service.create(event, f"activity-{i}")[0] for i in range(8)]
    with db.session() as session:
        study_id = session.get(RunRecord, baseline).study_id
        for i, run_id in enumerate(ids):
            run = session.get(RunRecord, run_id)
            run.strategy_version = "research.v1"
            run.status = "running" if i == 0 else "failed"
            run.created_at = NOW + timedelta(minutes=i)
            run.error_code = "provider_timeout" if i else None
        # Latest task points to another run's artifact: never label this as its report.
        session.add(
            ArtifactRecord(
                artifact_id="foreign-report",
                run_id=baseline,
                event_id=event,
                gate_status="research_only",
                headline="Other",
                summary="Other",
                payload_json="{}",
                created_at=NOW,
            )
        )
        session.get(RunRecord, ids[-1]).artifact_id = "foreign-report"
        for i, (parent, status) in enumerate(
            [(ids[0], "planned"), (baseline, "planned"), (ids[1], "completed")]
        ):
            session.add(
                ReviewScheduleRecord(
                    review_plan_id=f"p{i}",
                    parent_run_id=parent,
                    horizon="24h",
                    trigger_type="scheduled",
                    trigger_ref="test",
                    trigger_hash=f"h{i}",
                    trigger_at=NOW + timedelta(days=i + 1),
                    status=status,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        for i, strategy in enumerate(["research.v1", "baseline.v1"]):
            session.add(
                AdmissionDecisionRecord(
                    decision_id=f"a{i}",
                    attempt_id=f"poll{i}",
                    event_id=event,
                    study_id=study_id,
                    source_id="fed-press",
                    strategy_version=strategy,
                    policy_version="test",
                    decision="ignored",
                    reason_code="no_high_impact_title_match",
                    details_json="{}",
                    decided_at=NOW + timedelta(minutes=i),
                )
            )
        session.commit()
    app = FastAPI()
    app.include_router(study_router(db))
    client = TestClient(app)
    response = client.get(f"/v1/studies/{study_id}?offset=999")
    assert response.status_code == 200
    result = response.json()
    activity = result["activity"]
    assert result["offset"] == 0
    assert activity["research_runs"] == 8 and activity["active_runs"] == 1
    assert len(activity["recent_runs"]) == 6
    assert activity["recent_runs"][0]["run_id"] == ids[-1]
    assert activity["recent_runs"][0]["report_available"] is False
    assert activity["pending_review_plans"] == 1
    assert activity["next_review_at"] == (NOW + timedelta(days=1)).isoformat().replace(
        "+00:00", "Z"
    )
    assert activity["latest_admission"]["decision_id"] == "a0"
    assert client.get("/v1/studies?q=absent&offset=999").json()["offset"] == 0
    assert client.get("/v1/studies?offset=999").json()["offset"] == 0
