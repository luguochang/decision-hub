from __future__ import annotations

import asyncio
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.orchestration.langgraph import build_analyze_text_service
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_uncalibrated_legacy_candidate_is_saved_fail_closed(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'decision.sqlite3'}")
    database.create_all()
    service = build_analyze_text_service(database, FakeAgentRuntime())
    event_id, run_id, admitted = asyncio.run(
        service.submit_and_run(
            ObservationCreate(
                text=(
                    "Powell says rates may stay higher for longer while inflation remains elevated."
                ),
                source_id="fixture",
            )
        )
    )
    assert admitted is True
    run = database.get_run_view(run_id)
    assert run is not None
    assert run.event_id == event_id
    assert run.status.value == "degraded"
    assert run.snapshot_id and run.artifact_id
    artifact = database.get_artifact_view(run.artifact_id)
    assert artifact is not None
    assert artifact.gate_status.value == "research_only"
    assert len(artifact.forecasts) == 3
    assert {item.direction.value for item in artifact.forecasts} == {"no_trade"}
    assert {item.probability for item in artifact.forecasts} == {0.5}
    assert artifact.counter_thesis
    assert artifact.citations
    with database.session() as session:
        outbox = session.execute(
            __import__("sqlalchemy").text("SELECT count(*) FROM outbox")
        ).scalar_one()
    assert outbox == 0


def test_gate_fails_closed_without_evidence() -> None:
    result = evaluate_gate(
        {
            "facts": ["a claim"],
            "citations": [],
            "counter_thesis": "counter",
            "trigger": "trigger",
            "invalidation": "invalid",
            "direction": "long",
            "probability": 0.58,
        }
    )
    assert result.status.value == "reject"
    assert any(item["reason_code"] == "citations_missing" for item in result.decisions)


def test_gate_does_not_publish_direction_without_trusted_value_validation() -> None:
    candidate = {
        "facts": ["a claim"],
        "citations": ["evidence-1"],
        "counter_thesis": "counter",
        "trigger": "trigger",
        "invalidation": "invalid",
        "direction": "long",
        "probability": 0.58,
    }

    unvalidated = evaluate_gate(candidate)
    validated = evaluate_gate(candidate, directional_value_validated=True)

    assert unvalidated.status.value == "research_only"
    assert any(
        item["reason_code"] == "directional_value_unvalidated"
        for item in unvalidated.decisions
    )
    assert validated.status.value == "publish"


def test_gate_publishes_completed_no_trade_research_report() -> None:
    candidate = {
        "facts": ["spot and derivatives facts"],
        "citations": ["evidence-1"],
        "counter_thesis": "counter",
        "trigger": "review trigger",
        "invalidation": "invalid",
        "direction": "no_trade",
        "probability": 0.5,
    }
    assert evaluate_gate(candidate).status.value == "research_only"
    candidate["research_complete"] = True
    result = evaluate_gate(candidate)
    assert result.status.value == "publish"
    assert all(item["status"] == "pass" for item in result.decisions)
