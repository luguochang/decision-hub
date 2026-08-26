from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore, RecoveryWatchdog


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


def test_recovery_watchdog_only_returns_explicit_running_ids() -> None:
    assert RecoveryWatchdog(["run-1", "run-2"]).candidates() == ["run-1", "run-2"]
