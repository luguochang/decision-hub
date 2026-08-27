from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    OutboxRecord,
)
from packages.orchestration.langgraph import build_analyze_text_service
from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore, RecoveryWatchdog
from packages.orchestration.langgraph.graphs.decision_graph import build_decision_graph
from packages.orchestration.langgraph.state.decision import DecisionState
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_sqlite_checkpoint_survives_reopen(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "checkpoints.sqlite3"

        class CounterState(TypedDict):
            value: int

        async def increment(state: CounterState) -> dict[str, int]:
            return {"value": state.get("value", 0) + 1}

        graph = StateGraph(CounterState)
        graph.add_node("increment", increment)
        graph.add_edge(START, "increment")
        graph.add_edge("increment", END)
        async with CheckpointStore(path).open() as saver:
            app = graph.compile(checkpointer=saver)
            result = await app.ainvoke(
                {"value": 0}, config={"configurable": {"thread_id": "replay-1"}}
            )
            assert result["value"] == 1
        async with AsyncSqliteSaver.from_conn_string(str(path)) as reopened:
            await reopened.setup()
            checkpoint = await reopened.aget_tuple({"configurable": {"thread_id": "replay-1"}})
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["value"] == 1

    asyncio.run(run())


def test_recovery_watchdog_reads_non_terminal_runs(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'ledger.sqlite3'}")
    database.create_all()
    service = build_analyze_text_service(database, FakeAgentRuntime())
    event_id, _, admitted = service.admission.admit(ObservationCreate(text="running fixture"))
    assert admitted is True
    run_id, _ = service.runs.create(event_id)
    assert RecoveryWatchdog(database, tmp_path / "checkpoints.sqlite3").candidates() == [run_id]


def test_business_run_recovers_from_checkpoint_without_duplicate_commit(tmp_path: Path) -> None:
    async def run() -> None:
        database = Database(f"sqlite+pysqlite:///{tmp_path / 'ledger.sqlite3'}")
        database.create_all()
        checkpoint_path = tmp_path / "checkpoints.sqlite3"
        service = build_analyze_text_service(
            database, FakeAgentRuntime(), checkpoint_path=checkpoint_path
        )
        event_id, _envelope, admitted = service.admission.admit(
            ObservationCreate(text="Powell says rates may stay higher for longer.")
        )
        assert admitted is True
        run_id, _ = service.runs.create(event_id)
        from datetime import UTC, datetime, timedelta

        state: DecisionState = {
            "run_id": run_id,
            "event_id": event_id,
            "deadline_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        }
        async with CheckpointStore(checkpoint_path).open() as saver:
            graph = build_decision_graph(database, FakeAgentRuntime(), checkpointer=saver)
            await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": run_id}},
                interrupt_before=["gate_and_commit"],
            )
            checkpoint = await saver.aget_tuple(
                {"configurable": {"thread_id": run_id}}
            )
            assert checkpoint is not None
            channel_values = checkpoint.checkpoint["channel_values"]
            assert "text" not in channel_values
            assert "envelope" not in channel_values
            assert channel_values["snapshot_id"]
        await service.resume(run_id)
        await service.resume(run_id)
        run_view = database.get_run_view(run_id)
        assert run_view is not None
        assert run_view.status.value == "completed"
        with database.session() as session:
            assert session.query(ArtifactRecord).filter_by(run_id=run_id).count() == 1
            assert session.query(OutboxRecord).count() == 1

    asyncio.run(run())
