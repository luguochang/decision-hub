"""Real terminal-writer contention without another Host/model submission."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from packages.contracts_py.decision_hub_contracts import DshSessionCompletion, DshSessionSubmit
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    FakeHostClient,
    _database,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_status_terminal_busy import Writer

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("original_statement_control", [True, False])
async def test_terminal_result_initial_begin_contention(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, original_statement_control: bool,
) -> None:
    database = _database(tmp_path / "completion.sqlite3")
    run_id, _ = RunService(database).create("event-original-completion")
    writer = Writer(database)
    busy_statements: list[str] = []
    ticks = 0

    def on_error(context: Any) -> None:
        if getattr(context.original_exception, "sqlite_errorcode", None) == 5:
            busy_statements.append(context.statement)

    event.listen(database.engine, "handle_error", on_error)

    class Host(FakeHostClient):
        result_reads = 0

        async def result(self, payload: DshSessionSubmit) -> Any:
            reply = await super().result(payload)
            self.result_reads += 1
            writer.start()
            return reply

    host = Host()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                   poll_interval_seconds=.01)

    async def original_complete(completion: DshSessionCompletion, submit: DshSessionSubmit) -> None:
        # Preserved synchronous terminal writer; only the failed initial BEGIN
        # is expected. This is a controlled regression, not actual AI success.
        runtime.links.complete(completion)

    if original_statement_control:
        monkeypatch.setattr(runtime, "_complete_original_result", original_complete)

    async def clock_ticks() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(.1)
            ticks += 1

    clock = asyncio.create_task(clock_ticks())
    try:
        if original_statement_control:
            with pytest.raises(OperationalError) as failure:
                await runtime.execute(_request(run_id))
            assert failure.value.statement == "BEGIN IMMEDIATE"
        else:
            result = await runtime.execute(_request(run_id))
            assert result.status == "degraded"
            assert ticks >= 40
            link = runtime.links.get(run_id)
            assert link is not None and link.state == "completed" and link.generation == 1
        assert len(host.submit_calls) == 1 and host.result_reads == 1 and host.cancelled == []
        assert busy_statements == ["BEGIN IMMEDIATE"]
    finally:
        writer.close()
        clock.cancel()
        await asyncio.gather(clock, return_exceptions=True)
        await runtime.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_terminal_commit_failure_is_not_replayed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    database = _database(tmp_path / "uncertain.sqlite3")
    run_id, _ = RunService(database).create("event-uncertain-completion")
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host)
    calls = 0

    def uncertain(completion: DshSessionCompletion) -> Any:
        nonlocal calls
        calls += 1
        original = sqlite3.OperationalError("database is locked")
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise OperationalError("COMMIT", (), original)

    monkeypatch.setattr(runtime.links, "complete", uncertain)
    try:
        with pytest.raises(OperationalError) as failure:
            await runtime.execute(_request(run_id))
        assert failure.value.statement == "COMMIT" and calls == 1
        assert len(host.submit_calls) == 1 and host.cancelled == []
    finally:
        await runtime.close()
        database.engine.dispose()
