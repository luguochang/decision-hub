from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from packages.kernel.decision_hub_kernel.persistence.db import ArtifactRecord, RunRecord
from tests.evolution.test_research_worker import NOW, _admitted_run


def test_every_execution_family_is_pageable_with_stable_order_and_creation_boundary(tmp_path):
    database, event_id, first_id = _admitted_run(tmp_path)
    with database.session() as session:
        first = session.get(RunRecord, first_id)
        first.created_at = NOW - timedelta(days=1)
        session.add_all(
            RunRecord(
                run_id=f"historic-{index:04d}",
                event_id=event_id,
                status="failed" if index % 2 else "completed",
                strategy_version="baseline.v1" if index < 205 else "research.v1",
                created_at=NOW,
                updated_at=NOW,
                error_code="literal_100%" if index == 5 else None,
            )
            for index in range(225)
        )
        session.add(
            RunRecord(
                run_id="different-version",
                event_id=event_id,
                status="cancelled",
                strategy_version="baseline.v2",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    app = FastAPI()
    app.include_router(asset_router(database, tmp_path, clock=lambda: NOW))
    with TestClient(app) as client:
        filters = {"limit": 100, "created_before": NOW.isoformat()}
        first = client.get("/v1/decision-desk/runs", params=filters)
        assert first.status_code == 200
        assert first.headers["X-Total-Count"] == "227"
        assert first.headers["X-Has-More"] == "true"
        # A newly arriving job must not shift historical pages.
        with database.session() as session:
            session.add(
                RunRecord(
                    run_id="new-after-cutoff",
                    event_id=event_id,
                    status="running",
                    strategy_version="baseline.v1",
                    created_at=NOW + timedelta(seconds=1),
                    updated_at=NOW + timedelta(seconds=1),
                )
            )
        second = client.get("/v1/decision-desk/runs", params={**filters, "offset": 100})
        third = client.get("/v1/decision-desk/runs", params={**filters, "offset": 200})
        ids = [item["run_id"] for response in [first, second, third] for item in response.json()]
        assert len(ids) == len(set(ids)) == 227
        assert first_id in ids
        assert "new-after-cutoff" not in ids
        assert third.headers["X-Has-More"] == "false"
        assert all(item["created_at"].endswith("Z") for item in first.json())
        baseline = client.get(
            "/v1/decision-desk/runs", params={**filters, "kind": "baseline", "status": "failed"}
        )
        assert baseline.headers["X-Total-Count"] == "102"
        assert all(
            item["status"] == "failed" and item["strategy_version"] == "baseline.v1"
            for item in baseline.json()
        )
        other = client.get("/v1/decision-desk/runs", params={**filters, "kind": "other"})
        assert [item["run_id"] for item in other.json()] == ["different-version"]
        literal = client.get("/v1/decision-desk/runs", params={**filters, "q": "100%"})
        assert [item["run_id"] for item in literal.json()] == ["historic-0005"]
        empty = client.get("/v1/decision-desk/runs", params={**filters, "offset": 999})
        assert empty.json() == []
        assert empty.headers["X-Total-Count"] == "227"
        for invalid in [
            {"limit": 101},
            {"offset": -1},
            {"status": "not_real"},
            {"kind": "not_real"},
            {"created_before": "2026-09-30T10:00:00"},
            {"q": "x" * 201},
        ]:
            assert (
                client.get("/v1/decision-desk/runs", params={**filters, **invalid}).status_code
                == 422
            )


def test_headline_search_keeps_run_identity_and_missing_artifacts(tmp_path):
    database, event_id, run_id = _admitted_run(tmp_path)
    with database.session() as session:
        row = session.get(RunRecord, run_id)
        row.artifact_id = "art-1"
        session.add(
            ArtifactRecord(
                artifact_id="art-1",
                run_id=run_id,
                event_id=event_id,
                gate_status="research_only",
                headline="美联储事件更新",
                summary="test",
                payload_json="{}",
                created_at=NOW,
            )
        )
    items, total = database.ledger_runs(q="美联储")
    assert total == 1
    assert items[0].run_id == run_id
    assert items[0].headline == "美联储事件更新"
    assert items[0].gate_status == "research_only"
