from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class CheckpointStore:
    """Owns the LangGraph checkpoint database; business truth remains in Kernel tables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def open(self) -> AsyncGenerator[AsyncSqliteSaver, None]:
        async with AsyncSqliteSaver.from_conn_string(str(self.path)) as saver:
            await saver.setup()
            yield saver


class RecoveryWatchdog:
    """Finds interrupted runs through Kernel state; resume policy is explicit and bounded."""

    def __init__(self, running_run_ids: list[str]) -> None:
        self.running_run_ids = running_run_ids

    def candidates(self) -> list[str]:
        return list(self.running_run_ids)
