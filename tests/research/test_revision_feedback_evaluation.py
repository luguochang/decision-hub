from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts_py.decision_hub_contracts import ObservationCreate, ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ForecastRecord,
    OutcomeRecord,
    ReportRevisionRecord,
    ResearchEvidenceRecord,
)

NOW = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
FEEDBACK_AT = NOW + timedelta(minutes=10)
ORIGINAL_TABLES = {
    "runs", "artifacts", "report_revisions", "research_results", "research_evidence",
    "research_facts", "forecasts", "outcomes", "evaluations", "run_role_outputs",
    "snapshots", "observations", "outbox", "product_read_receipts", "product_commands",
    "active_pointers", "candidate_versions", "experiences", "promotion_decisions",
    "experiments", "experiment_results",
}


@dataclass(frozen=True)
class Scenario:
    database: Database
    path: Path
    run_id: str
    revision_id: str
    original_value: dict[str, Any]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Capture every physical column, including original JSON and timestamps."""
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {
            table["name"]: sorted(
                (
                    dict(row)
                    for row in connection.execute(
                        'SELECT * FROM "' + table["name"].replace('"', '""') + '"'
                    )
                ),
                key=_canonical,
            )
            for table in tables
        }


def _original_rows(rows: dict[str, list[dict[str, Any]]]) -> dict[str, object]:
    return {table: rows[table] for table in sorted(ORIGINAL_TABLES)}


def _observe(case: str, **values: object) -> None:
    root = os.getenv("DSH_REVISION_FEEDBACK_EVIDENCE_DIR")
    if root:
        directory = Path(root)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{case}.json"
        with target.open("x", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, sort_keys=True, indent=2)


def _result(run_id: str) -> ResearchSessionResult:
    coverage = {
        "status": "insufficient", "covered_requirement_ids": [], "gaps": [],
        "conflicts": [], "hard_coverage_ratio": 0.0, "soft_coverage_ratio": 0.0,
        "assessed_at": NOW.isoformat(),
    }
    task = {
        "task_id": "fixture-task", "requirement_id": "fixture-context",
        "capability_id": "replay.research", "objective": "Retain the controlled report.",
        "question": "Was the controlled source retained?", "input_evidence_refs": [],
        "output_schema_ref": "research-session-result.v1", "depends_on": [],
        "success_condition": "The original report is preserved.", "priority": 1,
    }
    return ResearchSessionResult.model_validate_json(_canonical({
        "schema_version": "research-session-result.v1", "request_id": f"result:{run_id}",
        "research_session_id": f"fixture:{run_id}", "runtime_id": "offline-feedback-fixture",
        "runtime_version": "offline-feedback-fixture.v1", "profile_ref": "fixture-only.v1",
        "trace_ref": "fixture://revision-feedback/result",
        "trace_hash": hashlib.sha256(b"controlled original report trace").hexdigest(),
        "status": "degraded",
        "rounds": [{
            "round": 1,
            "plan": {
                "plan_id": "fixture-plan", "objective": "Bounded report fixture.",
                "tasks": [task], "required_capabilities": ["replay.research"],
                "created_at": NOW.isoformat(),
            },
            "tool_invocations": [], "tool_results": [], "new_evidence_refs": [],
            "coverage": coverage, "started_at": NOW.isoformat(),
            "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
        }],
        "evidence_candidates": [], "facts": [], "final_coverage": coverage,
        "causal_case": None, "horizons": [],
        "stop_reason": {
            "code": "critical_data_unavailable", "detail": "Controlled bounded report only.",
            "bounded": True, "remaining_hard_gaps": [],
        },
        "total_tool_calls": 0, "total_subagents": 0, "total_tokens": None,
        "estimated_cost_usd": None, "started_at": NOW.isoformat(),
        "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
    }))


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "import-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_SOURCES_ENABLED", "0")
    monkeypatch.delenv("DECISION_HUB_OPERATIONS_RESEARCH_RUNTIME", raising=False)
    original_connect = socket.socket.connect
    socketpair_code = getattr(getattr(socket, "_fallback_socketpair", None), "__code__", None)
    network_attempts: list[object] = []

    def deny_network(sock: socket.socket, address: Any) -> None:
        # Allow only Windows stdlib socketpair self-pipe, never an HTTP origin.
        caller = sys._getframe(1)  # pyright: ignore[reportPrivateUsage] -- exact stdlib IPC
        if socketpair_code is not None and caller.f_code is socketpair_code:
            return original_connect(sock, address)
        network_attempts.append(address)
        raise AssertionError("feedback evaluation fixture must not connect to a Provider")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    from packages.kernel.decision_hub_kernel.application import research_value, workbench

    # Equal real fixture clocks isolate identity changes to owner/content, not time.
    monkeypatch.setattr(workbench, "utcnow", lambda: FEEDBACK_AT)
    monkeypatch.setattr(research_value, "utcnow", lambda: FEEDBACK_AT)
    yield
    assert network_attempts == []


def _scenario(directory: Path) -> Scenario:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "feedback.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    event_id, _, created = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="Controlled original report for immutable revision feedback.",
            source_id="revision-feedback-fixture", observed_at=NOW,
        )
    )
    assert created
    run_id, created = RunService(database, clock=lambda: NOW).create(
        event_id, "feedback-report", strategy_version="research.v1",
    )
    assert created
    # Actual legacy Commit persists the typed result and report; no Graph, Host,
    # Provider, calibration, directional publication or future API is claimed.
    CommitDecisionService(database, clock=lambda: NOW + timedelta(seconds=2)).commit_research_result(
        run_id, event_id, _result(run_id),
    )
    with database.session() as session:
        revision = session.query(ReportRevisionRecord).filter_by(source_run_id=run_id).one()
        revision_id = revision.revision_id
        excerpt = "Controlled prior durable source, not a Provider response."
        session.add(ResearchEvidenceRecord(
            evidence_id="fixture-original-evidence", run_id=run_id,
            capability_id="replay.research", requirement_id="fixture-context", kind="transcript",
            authority="official", source_id="revision-feedback-fixture", source_url=None,
            published_at=None, observed_at=NOW, received_at=NOW,
            content_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(), excerpt=excerpt,
            research_session_id=f"fixture:{run_id}", round=1, quality="accepted",
            freshness_status="fresh", accepted_at=NOW,
        ))
        forecast = session.query(ForecastRecord).filter_by(artifact_id=revision.artifact_id).first()
        assert forecast is not None and forecast.direction == "no_trade"
        # This original no-trade record exercises fixed Outcome row retention;
        # it grants no directional label or promotion eligibility.
        session.add(OutcomeRecord(
            outcome_id="fixture-original-outcome", forecast_id=forecast.forecast_id,
            observed_at=NOW + timedelta(minutes=5), direction_correct=False,
            return_pct=0.0, fees=0.0, slippage=0.0, quality_status="unavailable",
        ))
    original = ResearchValueEvaluationService(
        database, pack_root=Path(__file__).resolve().parents[2] / "packs" / "crypto_macro",
        clock=lambda: NOW + timedelta(minutes=6),
    ).evaluate_current(run_id)
    assert original.usefulness == "unlabeled" and original.mode == "research_only"
    return Scenario(database, path, run_id, revision_id, original.model_dump(mode="json"))


def _client(scenario: Scenario) -> TestClient:
    # Import after the isolated directory and offline flags exist. Do not enter
    # lifespan: these in-process routes need no service heartbeat/background task.
    from apps.hub_api.main import create_app

    return TestClient(create_app(scenario.database, source_connectors=[], sources_enabled=False))


def _post(client: TestClient, scenario: Scenario, *, owner: str = "owner-a",
          request_id: str = "feedback-fixed", notes: str = "Missing event context.",
          verdict: str = "not_useful") -> dict[str, Any]:
    payload = {
        "request_id": request_id, "target_type": "report_revision",
        "target_id": scenario.revision_id, "created_by": owner,
        "verdict": verdict, "notes": notes,
    }
    response = client.post(
        "/v1/workbench/feedback", json=payload,
        headers={"Idempotency-Key": request_id, "X-Owner-Id": owner},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert {key: result[key] for key in payload} == payload
    assert result["feedback_id"] and result["created_at"]
    return result


def _value(client: TestClient, scenario: Scenario) -> dict[str, Any]:
    response = client.get(f"/v1/research/runs/{scenario.run_id}/value-evaluation")
    assert response.status_code == 200, response.text
    return response.json()


def test_revision_feedback_post_appends_value_without_mutating_originals(
    offline, tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "append")
    try:
        client = _client(scenario)
        before = _rows(scenario.path)
        original = _value(client, scenario)
        receipt = _post(client, scenario)
        actual = _value(client, scenario)
        after = _rows(scenario.path)
        _observe("append", before=before, after=after, original=original,
                 feedback_receipt=receipt, actual_value=actual)
        assert _original_rows(after) == _original_rows(before)
        assert before["research_value_evaluations"][0] in after["research_value_evaluations"]
        assert len(after["workbench_feedback"]) == 1
        assert actual["evaluation_id"] != original["evaluation_id"], (
            "POST saved the revision feedback but GET still returns the pre-feedback value"
        )
        assert actual["evaluation_version"] != original["evaluation_version"]
        assert actual["usefulness"] == "not_useful"
        assert len(after["research_value_evaluations"]) == 2
        # GET must not generate a third evaluation.
        assert _value(client, scenario) == actual
        assert _rows(scenario.path) == after
    finally:
        scenario.database.engine.dispose()


def test_same_feedback_request_replay_does_not_replace_later_value_or_append_again(
    offline, tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "replay")
    try:
        client = _client(scenario)
        before = _rows(scenario.path)
        first = _post(client, scenario)
        first_value = _value(client, scenario)
        first_rows = _rows(scenario.path)
        later = _post(client, scenario, request_id="feedback-later", verdict="useful",
                      notes="A later, separately confirmed report feedback.")
        later_value = _value(client, scenario)
        later_rows = _rows(scenario.path)
        replay = _post(client, scenario)
        replay_value = _value(client, scenario)
        replay_rows = _rows(scenario.path)
        _observe("replay", before=before, first_rows=first_rows, later_rows=later_rows,
                 replay_rows=replay_rows, first=first, later=later, replay=replay,
                 first_value=first_value, later_value=later_value, replay_value=replay_value)
        assert replay == first and replay_value == later_value
        assert replay_rows == later_rows
        assert _original_rows(first_rows) == _original_rows(before)
        assert _original_rows(replay_rows) == _original_rows(before)
        assert all(row in replay_rows["research_value_evaluations"]
                   for row in first_rows["research_value_evaluations"])
        assert len(first_rows["workbench_feedback"]) == 1
        assert len(replay_rows["workbench_feedback"]) == 2
        assert len(replay_rows["research_value_evaluations"]) == 3, (
            "each actual feedback needs one fixed value; replay must not append or replace it"
        )
        assert len(first_rows["research_value_evaluations"]) == 2
        assert first_value["evaluation_id"] != later_value["evaluation_id"]
        assert first_value["usefulness"] == "not_useful" and later_value["usefulness"] == "useful"
    finally:
        scenario.database.engine.dispose()


def test_same_count_and_time_different_owner_and_content_bind_different_value_versions(
    offline, tmp_path: Path,
) -> None:
    original = _scenario(tmp_path / "original")
    cloned_path = tmp_path / "cloned.sqlite3"
    # Copy the actual fixed fixture with SQLite backup, including random original
    # IDs. Do not reconstruct a different Run and mistake identity drift for proof.
    with sqlite3.connect(original.path) as source, sqlite3.connect(cloned_path) as target:
        source.backup(target)
    clone_database = Database(f"sqlite+pysqlite:///{cloned_path}")
    clone = Scenario(clone_database, cloned_path, original.run_id, original.revision_id,
                     original.original_value)
    try:
        first_client, second_client = _client(original), _client(clone)
        first_before, second_before = _rows(original.path), _rows(cloned_path)
        assert first_before == second_before
        first = _post(first_client, original, owner="owner-a", verdict="useful",
                      notes="Useful for the first owner's question.")
        second = _post(second_client, clone, owner="owner-b", verdict="useful",
                       notes="Useful for a different owner's purpose.")
        first_value, second_value = _value(first_client, original), _value(second_client, clone)
        first_after, second_after = _rows(original.path), _rows(cloned_path)
        _observe("binding", first_before=first_before, second_before=second_before,
                 first_after=first_after, second_after=second_after, first=first, second=second,
                 first_value=first_value, second_value=second_value)
        assert first["request_id"] == second["request_id"]
        assert first["feedback_id"] == second["feedback_id"]
        assert first["created_at"] == second["created_at"]
        assert first["created_by"] != second["created_by"] and first["notes"] != second["notes"]
        assert len(first_after["workbench_feedback"]) == len(second_after["workbench_feedback"]) == 1
        assert _original_rows(first_after) == _original_rows(first_before)
        assert _original_rows(second_after) == _original_rows(second_before)
        assert first_value["evaluation_version"] != second_value["evaluation_version"], (
            "equal row counts and times must not alias feedback owner/content identities"
        )
        assert first_value["usefulness"] == second_value["usefulness"] == "useful"
    finally:
        original.database.engine.dispose()
        clone_database.engine.dispose()
