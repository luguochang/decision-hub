"""Direct scheduling/cancellation fences; real Gateway and temporary SQLite."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event, Thread, get_ident

import pytest
from sqlalchemy import event

from packages.kernel.decision_hub_kernel.application.research_evidence import ResearchCapabilityError
from packages.kernel.decision_hub_kernel.application.research_observability import ResearchObservabilityService
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord, ResearchToolCallReservationRecord, ResearchTraceRecord,
)
from tests.research.test_durable_research_gateway import _Adapter, _database, _gateway, _query
from tests.research.test_gateway_execution_v2 import (
    LiveAdapter, _gateway as bound_gateway, _intent, live_inputs, forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert live_inputs is not None and forbid_external_connections is not None


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))


def state(database, run_id):
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        rows = session.query(ResearchToolCallReservationRecord).filter_by(run_id=run_id).all()
        traces = session.query(ResearchTraceRecord).filter_by(run_id=run_id).all()
        return link.tool_calls_started, [row.status for row in rows], [row.reference_id for row in traces]


def latch_auth(gateway, monkeypatch):
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    native_entered = Event()
    original = gateway._validate_session
    threads = []

    def blocked(query):
        threads.append(get_ident())
        native_entered.set()
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2), "finite auth watchdog failed"
        return original(query)

    # Releases the old synchronous-loop implementation too, so RED is a
    # semantic assertion, never a deadlock or timeout test failure.
    def watchdog():
        if native_entered.wait(2):
            release.wait(0.3)
        release.set()

    watcher = Thread(target=watchdog)
    watcher.start()
    monkeypatch.setattr(gateway, "_validate_session", blocked)
    return entered, release, watcher, threads


async def test_cancellation_during_auth_never_reserves_or_enters_provider(tmp_path, monkeypatch):
    database, run_id, sid = _database(tmp_path)
    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    entered, release, watcher, _ = latch_auth(gateway, monkeypatch)
    task = asyncio.create_task(gateway.execute(_query(sid)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # a second cancellation cannot detach the owner
        release.set()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        assert cancelled and adapter.call_count == 0
        assert state(database, run_id)[:2] == (0, [])
    finally:
        release.set()
        watcher.join(1)
        database.engine.dispose()


async def test_timer_runs_before_sync_auth_exit_and_adapter_keeps_loop(tmp_path, monkeypatch):
    database, _, sid = _database(tmp_path)
    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    loop_thread = get_ident()
    entered, release, watcher, threads = latch_auth(gateway, monkeypatch)
    original_adapter = adapter.execute
    adapter_threads = []
    adapter_contexts = []

    async def record_adapter(query):
        from packages.kernel.decision_hub_kernel.application.daily_execution_binding import _CURRENT
        from packages.kernel.decision_hub_kernel.application.research_evidence import _ADAPTER_RETURN_COLLECTOR
        adapter_threads.append(get_ident())
        adapter_contexts.append((_CURRENT.get() is not None, _ADAPTER_RETURN_COLLECTOR.get() is not None))
        return await original_adapter(query)

    monkeypatch.setattr(adapter, "execute", record_adapter)
    task = asyncio.create_task(gateway.execute(_query(sid)))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)  # independent async scheduling while auth is held
        assert not release.is_set(), "sync authentication blocked the event loop"
        release.set()
        await task
        assert threads and all(thread != loop_thread for thread in threads)
        assert adapter_threads == [loop_thread]
        assert adapter_contexts == [(True, True)]
    finally:
        release.set()
        watcher.join(1)
        database.engine.dispose()


async def test_queued_cancel_does_not_open_authentication(tmp_path, monkeypatch):
    database, run_id, sid = _database(tmp_path)
    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    release, occupied = Event(), Event()
    original_executor = loop.run_in_executor

    def occupy():
        occupied.set()
        release.wait(2)

    blocker = original_executor(executor, occupy)
    assert occupied.wait(1)
    monkeypatch.setattr(loop, "run_in_executor", lambda _executor, fn, *args: original_executor(executor, fn, *args))
    auth_calls = []
    original_auth = gateway._validate_session

    def auth(query):
        auth_calls.append(True)
        return original_auth(query)

    monkeypatch.setattr(gateway, "_validate_session", auth)
    task = asyncio.create_task(gateway.execute(_query(sid)))
    try:
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await blocker
        assert not auth_calls and not adapter.call_count
        assert state(database, run_id)[:2] == (0, [])
    finally:
        release.set()
        executor.shutdown(wait=True)
        database.engine.dispose()


@pytest.mark.parametrize("cut", ["flushed", "committed", "audit_failure"])
async def test_cancel_at_writer_cut_is_rolled_back_or_exactly_audited(tmp_path, cut):
    database, run_id, sid = _database(tmp_path)
    loop = asyncio.get_running_loop()
    reached, release = asyncio.Event(), Event()

    class CutObserver(ResearchObservabilityService):
        def append_trace_in_session(self, session, trace, **kwargs):
            if cut == "audit_failure" and trace.reference_id == "call-1:admission-stopped":
                raise OSError("controlled cancellation audit storage failure")
            return super().append_trace_in_session(session, trace, **kwargs)

        def reserve_tool_call_in_session(self, session, **kwargs):
            value = super().reserve_tool_call_in_session(session, **kwargs)
            def pause(*_args):
                loop.call_soon_threadsafe(reached.set)
                assert release.wait(2)
            if cut == "flushed":
                pause()
            else:
                event.listen(session, "after_commit", pause, once=True)
            return value

    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    gateway.observability = CutObserver(database, clock=gateway.clock)
    task = asyncio.create_task(gateway.execute(_query(sid)))
    try:
        await asyncio.wait_for(reached.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
        count, statuses, refs = state(database, run_id)
        assert adapter.call_count == 0
        if cut == "flushed":
            assert (count, statuses) == (0, [])
        elif cut == "committed":
            assert (count, statuses) == (1, ["cancelled"])
            assert "call-1:admission-stopped" in refs
        else:
            assert (count, statuses) == (1, ["running"])
            assert "call-1:admission-stopped" not in refs
            assert any("cleanup_unconfirmed" in note for note in cancelled.value.__notes__)
    finally:
        release.set()
        database.engine.dispose()


async def test_v1_exact_repeat_and_cancelled_duplicate_do_not_cancel_old_row(tmp_path, monkeypatch):
    database, run_id, sid = _database(tmp_path)
    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    try:
        first = await gateway.execute(_query(sid))
        assert await gateway.execute(_query(sid)) == first
        assert adapter.call_count == 1
        entered, release, watcher, _ = latch_auth(gateway, monkeypatch)
        task = asyncio.create_task(gateway.execute(_query(sid)))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        watcher.join(1)
        assert state(database, run_id)[:2] == (1, ["completed"])
    finally:
        database.engine.dispose()


async def test_started_trace_error_after_commit_cleans_only_own_reservation(tmp_path):
    database, run_id, sid = _database(tmp_path)
    original_error = OSError("controlled tool_started storage failure")

    class TraceFailure(ResearchObservabilityService):
        def append_trace_in_session(self, session, trace, **kwargs):
            if trace.event_type == "tool_started":
                raise original_error
            return super().append_trace_in_session(session, trace, **kwargs)

    adapter = _Adapter(sid)
    gateway = _gateway(database, sid, adapter=adapter)
    gateway.observability = TraceFailure(database, clock=gateway.clock)
    try:
        with pytest.raises(OSError) as raised:
            await gateway.execute(_query(sid))
        assert raised.value is original_error
        assert adapter.call_count == 0
        count, statuses, refs = state(database, run_id)
        assert (count, statuses) == (1, ["cancelled"])
        assert "call-1:admission-stopped" in refs
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("mutation", ["scope", "generation", "deadline"])
async def test_bound_live_original_changes_before_reserve_still_deny(live_inputs, mutation):
    source = live_inputs
    source.prepare()
    adapter = LiveAdapter(source)

    class Mutated(ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs):
            if mutation == "deadline":
                gateway.clock = lambda: source.request.deadline_at + timedelta(seconds=1)
                self.clock = gateway.clock
            else:
                with source.database.session() as session:
                    if mutation == "generation":
                        session.get(DshSessionLinkRecord, source.request.run_id).generation += 1
                    else:
                        from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
                        row = session.query(RunRoleOutputRecord).filter_by(
                            run_id=source.request.run_id, role="research_request_pack").one()
                        row.output_id += ":changed"
            return super().reserve_tool_call(**kwargs)

    gateway = bound_gateway(source, adapter, Mutated(source.database, clock=lambda: source.now))
    with pytest.raises(ResearchCapabilityError):
        await gateway.invoke_live(_intent(source))
    assert not adapter.calls
    assert state(source.database, source.request.run_id)[:2] == (0, [])


async def test_cleanup_requires_exact_committed_identity(tmp_path):
    database, run_id, sid = _database(tmp_path)
    gateway = _gateway(database, sid)
    identities = []
    try:
        # Exercise real reserve/commit then exact cleanup, without a provider.
        gateway.observability.reserve_tool_call(run_id=run_id, request_id="owned",
            research_session_id=sid, generation=1, capability_id="official.macro",
            query_hash="1" * 64, on_new_reservation=identities.append)
        identity = identities[0]
        repeated = gateway.observability.reserve_tool_call(run_id=run_id, request_id="owned",
            research_session_id=sid, generation=1, capability_id="official.macro",
            query_hash="1" * 64, on_new_reservation=identities.append)
        assert repeated.outcome == "running" and len(identities) == 1
        with pytest.raises(ValueError, match="original_missing"):
            gateway.observability.cancel_owned_tool_call(replace(identity, request_id="missing"))
        for bad in (replace(identity, query_hash="2" * 64), replace(identity, generation=2)):
            with pytest.raises(ValueError, match="identity"):
                gateway.observability.cancel_owned_tool_call(bad)
        assert state(database, run_id)[:2] == (1, ["running"])
        gateway.observability.cancel_owned_tool_call(identity)
        gateway.observability.cancel_owned_tool_call(identity)
        assert state(database, run_id)[:2] == (1, ["cancelled"])
    finally:
        database.engine.dispose()
