"""Finite real SQLite/thread scheduling; no provider, model or normal data."""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from apps.hub_worker.work_admission import HeavyWorkAdmission as LegacyAdmission
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.orchestration.langgraph import executor as execution
from packages.orchestration.langgraph.graphs import agentic_research_graph as graph_module
from packages.orchestration.owned_work import HeavyWorkAdmission
from tests.dsh_native.test_dsh_web_runtime import _request


@pytest.fixture
def storage(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'work.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("CREATE TABLE beats (phase TEXT NOT NULL)")
    yield engine
    engine.dispose()


def save_beat(engine, phase):
    with Session(engine) as session:
        session.execute(text("INSERT INTO beats (phase) VALUES (:phase)"), {"phase": phase})
        session.commit()


@pytest.mark.asyncio
async def test_native_session_spans_two_actual_heartbeat_commits(storage):
    assert LegacyAdmission is HeavyWorkAdmission
    admission = HeavyWorkAdmission()
    main_thread = threading.get_ident()
    context = ContextVar("owned_context", default="missing")
    context.set("caller")
    started, closed = threading.Event(), threading.Event()
    heartbeats = []

    def slow_read():
        assert threading.get_ident() != main_thread
        assert context.get() == "caller"
        context.set("native-only")
        with Session(storage) as session:
            session.execute(text("SELECT count(*) FROM beats")).scalar_one()
            started.set()
            time.sleep(0.14)
        closed.set()
        return "prepared"

    async def beat():
        while not closed.is_set():
            await asyncio.sleep(0.02)
            if started.is_set() and not closed.is_set():
                await asyncio.to_thread(save_beat, storage, "heartbeat")
                heartbeats.append(time.monotonic())

    heartbeat = asyncio.create_task(beat())
    try:
        assert await admission.run_thread(slow_read) == "prepared"
    finally:
        await heartbeat
    assert len(heartbeats) >= 2
    assert context.get() == "caller"
    with Session(storage) as session:
        assert session.execute(text("SELECT count(*) FROM beats")).scalar_one() >= 2


@pytest.mark.asyncio
async def test_cancel_drains_actual_session_before_releasing_admission(storage):
    admission = HeavyWorkAdmission()
    started, release, closed, next_started = [threading.Event() for _ in range(4)]

    def slow_read():
        with Session(storage) as session:
            session.execute(text("SELECT count(*) FROM beats")).scalar_one()
            started.set()
            assert release.wait(2)
        closed.set()

    def next_read():
        assert closed.is_set()
        next_started.set()
        with Session(storage) as session:
            return session.execute(text("SELECT count(*) FROM beats")).scalar_one()

    first = asyncio.create_task(admission.run_thread(slow_read))
    while not started.is_set():
        await asyncio.sleep(0.005)
    first.cancel()
    second = asyncio.create_task(admission.run_thread(next_read))
    try:
        # Repeated cancellation cannot detach the original native Session.
        await asyncio.sleep(0.025)
        first.cancel()
        await asyncio.sleep(0.025)
        assert not first.done() and not closed.is_set() and not next_started.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
    assert closed.is_set() and next_started.is_set()


@pytest.mark.asyncio
async def test_default_executor_and_graph_own_all_pre_runtime_sessions(storage, monkeypatch):
    """Scheduling seam only: original semantic verifiers are not tested here."""
    from packages.kernel.decision_hub_kernel.application import research_context_selection_v4

    RunRoleOutputRecord.__table__.create(storage)
    main_thread = threading.get_ident()
    phases = []

    @contextmanager
    def session():
        assert threading.get_ident() != main_thread
        with Session(storage) as value:
            yield value

    database = SimpleNamespace(session=session)

    def slow_phase(name):
        assert threading.get_ident() != main_thread
        with Session(storage) as value:
            value.execute(text("SELECT count(*) FROM beats")).scalar_one()
            time.sleep(0.03)
        phases.append(name)

    def prepare(_session, state, _owner):
        slow_phase("state")
        return True, state

    def recipe(_session, _run):
        slow_phase("recipe")

    def quality(_session, _run):
        slow_phase("quality")
        return True

    request = _request("run-owned-graph")

    class Selection:
        def __init__(self, *_args, **_kwargs):
            pass

        def select(self, **_kwargs):
            slow_phase("selection")
            return SimpleNamespace(request=request, reference="original-reference")

    class AtRuntime(Exception):
        pass

    class Runtime:
        async def execute_selected_observed(self, *args, **kwargs):
            assert threading.get_ident() == main_thread
            # A model await must not hold the native-work admission.
            await asyncio.wait_for(executor.owned_work.run_thread(lambda: None), timeout=0.5)
            raise AtRuntime

    monkeypatch.setattr(graph_module, "prepare_graph_state_in_session", prepare)
    monkeypatch.setattr(graph_module, "read_graph_recipe_selection_in_session", recipe)
    monkeypatch.setattr(graph_module, "is_quality_graph_in_session", quality)
    monkeypatch.setattr(research_context_selection_v4, "ContextSelectionService", Selection)
    executor = execution.LangGraphResearchExecutor(
        database, Runtime(), expected_lease_owner="original-owner",
    )
    executor.trace_sink = None
    monkeypatch.setattr(executor.tasks, "freeze", lambda _request: slow_phase("freeze"))
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.02)
            ticks += 1

    beating = asyncio.create_task(heartbeat())
    try:
        with pytest.raises(AtRuntime):
            await executor.execute(request, require_context_selection=True)
    finally:
        stop.set()
        await beating
    assert phases == ["freeze", "state", "recipe", "quality", "selection"]
    assert ticks >= 2
