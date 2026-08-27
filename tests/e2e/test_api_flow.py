from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ObservationRecord,
    SnapshotRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


def test_api_observation_run_and_outcome(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    app = create_app(database)
    client = TestClient(app)
    response = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "test-001"},
        json={
            "text": "Powell says rates may stay higher for longer.",
            "source_id": "api-fixture",
            "language": "en",
        },
    )
    assert response.status_code == 202
    body = response.json()
    run = client.get(body["status_url"])
    assert run.status_code == 200
    run_view = run.json()
    assert run_view["artifact_id"]
    detail = client.get(f"/v1/runs/{body['run_id']}/view").json()
    inspector = client.get(f"/v1/runs/{body['run_id']}/inspector")
    assert inspector.status_code == 200
    assert len(inspector.json()["calls"]) == 3
    assert [step["step_name"] for step in inspector.json()["steps"]] == [
        "mark_running",
        "freeze_snapshot",
        "research",
        "gate_and_commit",
    ]
    assert all(step["status"] == "succeeded" for step in inspector.json()["steps"])
    assert {call["role"] for call in inspector.json()["calls"]} == {
        "policy_delta",
        "counter_thesis",
        "decision_synthesis",
    }
    assert all(call["status"] == "succeeded" for call in inspector.json()["calls"])
    assert all(call["cost_status"] == "unknown" for call in inspector.json()["calls"])
    timeline = client.get(f"/v1/runs/{body['run_id']}/timeline")
    assert timeline.status_code == 200
    assert [item["event_type"] for item in timeline.json()["items"]] == [
        "run.started",
        "snapshot.frozen",
        "research.completed",
        "decision.committed",
    ]
    forecast_id = detail["artifact"]["forecasts"][0]["forecast_id"]
    outcome = client.post(
        "/v1/outcomes",
        json={
            "forecast_id": forecast_id,
            "return_pct": -0.8,
            "direction_correct": True,
            "fees": 0.1,
            "slippage": 0.1,
        },
    )
    assert outcome.status_code == 200
    assert outcome.json()["net_return_pct"] == -1.0
    evaluation = client.get(f"/v1/evaluations/{outcome.json()['evaluation_id']}")
    assert evaluation.status_code == 200


def test_api_requires_idempotency_key(tmp_path: Path) -> None:
    app = create_app(Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}"))
    response = TestClient(app).post("/v1/observations", json={"text": "test"})
    assert response.status_code == 400


def test_api_idempotency_key_returns_same_run(tmp_path: Path) -> None:
    app = create_app(Database(f"sqlite+pysqlite:///{tmp_path / 'idempotency.sqlite3'}"))
    client = TestClient(app)
    payload = {"text": "Powell says rates may stay higher for longer.", "source_id": "idempotency"}
    first = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "same-request"},
        json=payload,
    )
    second = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "same-request"},
        json=payload,
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]


def test_text_input_to_evaluation_output_contract(tmp_path: Path) -> None:
    text = "The committee says rates may stay higher for longer while inflation is still elevated."
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'full-chain.sqlite3'}")
    app = create_app(database)
    client = TestClient(app)

    accepted = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "full-chain-001"},
        json={"text": text, "source_id": "full-chain-fixture", "language": "en"},
    )

    assert accepted.status_code == 202
    accepted_body = accepted.json()
    run_id = accepted_body["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()
    detail = client.get(f"/v1/runs/{run_id}/view").json()
    artifact = detail["artifact"]

    assert run["status"] == "completed"
    assert run["event_id"] == accepted_body["event_id"]
    assert run["snapshot_id"]
    assert run["artifact_id"] == artifact["artifact_id"]
    assert artifact["gate_status"] == "publish"
    assert artifact["facts"]
    assert artifact["facts"] == [text]
    assert all("Policy analysis:" not in fact for fact in artifact["facts"])
    assert artifact["citations"]
    assert artifact["counter_thesis"]
    assert {forecast["horizon"] for forecast in artifact["forecasts"]} == {
        "30m",
        "24h",
        "72h",
    }
    assert all(
        forecast["trigger"] and forecast["invalidation"]
        for forecast in artifact["forecasts"]
    )

    with database.session() as session:
        event = session.get(EventRecord, accepted_body["event_id"])
        observations = session.query(ObservationRecord).all()
        snapshots = session.query(SnapshotRecord).all()
    assert event is not None
    assert len(observations) == 1
    assert observations[0].content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(snapshots) == 1
    assert snapshots[0].event_id == accepted_body["event_id"]
    assert snapshots[0].evidence_json

    forecast_id = artifact["forecasts"][0]["forecast_id"]
    outcome = client.post(
        "/v1/outcomes",
        json={
            "forecast_id": forecast_id,
            "return_pct": 2.4,
            "direction_correct": True,
            "fees": 0.2,
            "slippage": 0.1,
        },
    )
    assert outcome.status_code == 200
    evaluation = client.get(f"/v1/evaluations/{outcome.json()['evaluation_id']}")
    assert evaluation.status_code == 200
    evaluation_body = evaluation.json()
    assert evaluation_body["forecast_id"] == forecast_id
    assert evaluation_body["net_return_pct"] == pytest.approx(2.1)
    assert evaluation_body["brier_score"] == pytest.approx(
        (artifact["forecasts"][0]["probability"] - 1) ** 2
    )


def test_inspector_preserves_provider_failure_code(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'failure.sqlite3'}")
    app = create_app(database)
    class FailingGraph:
        async def ainvoke(self, _state: dict[str, object], **_kwargs: object) -> None:
            raise AgentExecutionError("provider_timeout", "fixture timeout", retryable=True)

    app.state.analyzer.graph = FailingGraph()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "provider-timeout"},
        json={"text": "fixture provider timeout", "source_id": "failure-fixture"},
    )
    assert response.status_code == 202
    run = client.get(response.json()["status_url"]).json()
    assert run["status"] == "failed"
    assert run["error_code"] == "provider_timeout"
