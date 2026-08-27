from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from packages.contracts_py.decision_hub_contracts.models import RunStatus
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


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


class RecoveryTarget(Protocol):
    async def resume(self, run_id: str) -> None: ...


class RecoveryWatchdog:
    """Finds interrupted runs through Kernel state; resume policy is explicit and bounded."""

    def __init__(self, database: Database, checkpoint_path: Path) -> None:
        self.database = database
        self.checkpoint_path = checkpoint_path

    def candidates(self) -> list[str]:
        return self.database.recovery_candidates()

    async def recover(self, target: RecoveryTarget) -> dict[str, str]:
        results: dict[str, str] = {}
        runs = RunService(self.database)
        for run_id in self.candidates():
            try:
                async with CheckpointStore(self.checkpoint_path).open() as saver:
                    checkpoint = await saver.aget_tuple(
                        {"configurable": {"thread_id": run_id}}
                    )
                if checkpoint is None:
                    runs.set_status(
                        run_id, RunStatus.failed, error_code="recovery_checkpoint_missing"
                    )
                    results[run_id] = "recovery_checkpoint_missing"
                    continue
                await target.resume(run_id)
                results[run_id] = "recovered"
            except AgentExecutionError as exc:
                runs.set_status(run_id, RunStatus.failed, error_code=exc.error_code)
                results[run_id] = exc.error_code
            except Exception:
                runs.set_status(run_id, RunStatus.failed, error_code="recovery_failed")
                results[run_id] = "recovery_failed"
        return results
