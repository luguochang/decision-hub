from __future__ import annotations

import asyncio
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_text_to_artifact_forecasts_and_gate(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'decision.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
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
    assert run.status.value == "completed"
    assert run.snapshot_id and run.artifact_id
    artifact = database.get_artifact_view(run.artifact_id)
    assert artifact is not None
    assert artifact.gate_status.value == "publish"
    assert len(artifact.forecasts) == 3
    assert artifact.counter_thesis
    assert artifact.citations
    with database.session() as session:
        outbox = session.execute(
            __import__("sqlalchemy").text("SELECT count(*) FROM outbox")
        ).scalar_one()
    assert outbox == 1


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
