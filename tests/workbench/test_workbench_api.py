from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import Database, SnapshotRecord


def test_workbench_command_requires_owner_and_existing_reference(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    client = TestClient(create_app(database))
    payload = {
        "request_id": "memo-api-1",
        "run_id": "missing-run",
        "snapshot_id": None,
        "domain_pack_ref": "crypto_macro.v1",
        "created_by": "owner",
        "claims": ["Claim"],
        "evidence_refs": ["evidence:1"],
        "counterpoints": [],
        "uncertainties": [],
        "follow_up_questions": [],
    }
    unauthorized = client.post(
        "/v1/workbench/memos",
        headers={"Idempotency-Key": "memo-api-1"},
        json=payload,
    )
    assert unauthorized.status_code == 403
    missing = client.post(
        "/v1/workbench/memos",
        headers={"Idempotency-Key": "memo-api-1", "X-Owner-Id": "owner"},
        json=payload,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "workbench_run_not_found"


def test_workbench_overview_is_typed_and_memo_reuse_is_conflict(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'overview.sqlite3'}")
    client = TestClient(create_app(database))
    observation = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "overview-observation"},
        json={"text": "Overview fixture", "source_id": "overview-fixture"},
    )
    assert observation.status_code == 202
    run_id = observation.json()["run_id"]
    snapshot_id = client.get(f"/v1/runs/{run_id}").json()["snapshot_id"]
    with database.session() as session:
        snapshot = session.get(SnapshotRecord, snapshot_id)
        assert snapshot is not None
        evidence_id = json.loads(snapshot.evidence_json)[0]["evidence_id"]
    memo_payload = {
        "request_id": "overview-memo",
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "domain_pack_ref": "crypto_macro.v1",
        "created_by": "owner",
        "claims": ["The fixture is observable."],
        "evidence_refs": [evidence_id],
        "counterpoints": [],
        "uncertainties": [],
        "follow_up_questions": [],
    }
    first = client.post(
        "/v1/workbench/memos",
        headers={"Idempotency-Key": "overview-memo", "X-Owner-Id": "owner"},
        json=memo_payload,
    )
    assert first.status_code == 200
    reused = client.post(
        "/v1/workbench/memos",
        headers={"Idempotency-Key": "overview-memo", "X-Owner-Id": "owner"},
        json={**memo_payload, "claims": ["Changed claim"]},
    )
    assert reused.status_code == 409
    assert reused.json()["detail"] == "workbench_request_reused"

    overview = client.get("/v1/workbench/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert len(body["memos"]) == 1
    assert body["feedback"] == []
    assert isinstance(body["capabilities"], list)


def test_feedback_command_is_owner_bound_and_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'feedback.sqlite3'}")
    client = TestClient(create_app(database))
    observation = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "feedback-observation"},
        json={"text": "Feedback fixture", "source_id": "feedback-fixture"},
    )
    assert observation.status_code == 202
    run_id = observation.json()["run_id"]
    payload = {
        "request_id": "feedback-request",
        "target_type": "run",
        "target_id": run_id,
        "created_by": "owner",
        "verdict": "needs_review",
        "notes": "Need a second source before trusting the conclusion.",
    }
    unauthorized = client.post(
        "/v1/workbench/feedback",
        headers={"Idempotency-Key": "feedback-request", "X-Owner-Id": "other"},
        json=payload,
    )
    assert unauthorized.status_code == 403
    first = client.post(
        "/v1/workbench/feedback",
        headers={"Idempotency-Key": "feedback-request", "X-Owner-Id": "owner"},
        json=payload,
    )
    assert first.status_code == 200
    second = client.post(
        "/v1/workbench/feedback",
        headers={"Idempotency-Key": "feedback-request", "X-Owner-Id": "owner"},
        json=payload,
    )
    assert second.status_code == 200
    assert second.json()["feedback_id"] == first.json()["feedback_id"]
    conflict = client.post(
        "/v1/workbench/feedback",
        headers={"Idempotency-Key": "feedback-request", "X-Owner-Id": "owner"},
        json={**payload, "verdict": "incorrect"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "workbench_request_reused"
