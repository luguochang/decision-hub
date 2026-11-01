"""Finite callback lifetime gates, private SQLite only; no global API app."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import importlib.util
import os
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace

import pytest

_data = tempfile.TemporaryDirectory(prefix="dsh-provider-deadline-")
os.environ["DECISION_HUB_DATA_DIR"] = _data.name

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord  # noqa: E402

_root = next(p for p in Path(__file__).resolve().parents if (p / "BASES.json").is_file())
_spec = importlib.util.spec_from_file_location("provider_deadline_candidate", _root / "proposed/apps/hub_api/provider_input_routes.py")
routes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routes)


def setup(tmp_path, task_seconds=180):
    engine = create_engine(f"sqlite:///{tmp_path / 'owned.sqlite3'}")
    DshSessionLinkRecord.__table__.create(engine)
    now = datetime.now(UTC)
    payload = SimpleNamespace(run_id="run_" + "a" * 32, dispatch_id="owned-dispatch",
        root_session_id="dsh_" + "b" * 64, generation=1, request_hash="c" * 64,
        plugin_build_hash="d" * 64)
    with Session(engine) as session:
        session.add(DshSessionLinkRecord(run_id=payload.run_id, dsh_session_id=payload.root_session_id,
            request_hash=payload.request_hash, plugin_build_hash=payload.plugin_build_hash,
            upstream_identity_json="{}", state="running", generation=1,
            deadline_at=now + timedelta(seconds=task_seconds), max_tool_calls=100,
            model_step_timeout_ms=600000, tool_calls_started=0, last_seq=0,
            created_at=now, updated_at=now))
        session.commit()

    class PrivateDatabase:
        @contextmanager
        def session(self):
            with Session(engine) as session:
                yield session
                session.rollback()

    class Request:
        headers = {"x-decision-hub-provider-input-budget-ms": "180000",
            "x-decision-hub-provider-input-deadline-at": (now + timedelta(seconds=180)).isoformat()}
        scope = {}

        async def is_disconnected(self):
            return False

    return PrivateDatabase(), payload, Request(), engine


@pytest.mark.parametrize("kind", ["input", "emission", "finish"])
def test_same_native_auth_can_complete_after_virtual_sixty_seconds(tmp_path, monkeypatch, kind):
    database, payload, request, engine = setup(tmp_path)
    import time
    native_clock = time.monotonic
    shift = [0.0]
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: native_clock() + shift[0]))
    calls, closed = [], Event()

    def authenticate(actual, *, request_check, diagnostics):
        calls.append(actual)
        try:
            with database.session() as session:
                assert session.get(DshSessionLinkRecord, actual.run_id).generation == 1
                request_check()
                # Real owned synchronous callback/SQLite Session, with only its
                # phase clock advanced. No 60-second wall sleep or provider call.
                shift[0] = 61.0
                request_check()
                return "authenticated"
        finally:
            closed.set()

    result = asyncio.run(routes._bounded_callback(request, authenticate, payload, kind, database=database))
    assert result == "authenticated" and calls == [payload] and closed.is_set()
    engine.dispose()


def test_original_task_ceiling_refuses_without_calling_operation(tmp_path):
    database, payload, request, engine = setup(tmp_path, task_seconds=-1)
    calls = []
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes._bounded_callback(request, lambda *a, **k: calls.append(a), payload, database=database))
    assert error.value.status_code == 409 and calls == []
    engine.dispose()


def test_repeated_cancel_waits_for_same_native_session_exit(tmp_path):
    database, payload, request, engine = setup(tmp_path)
    entered, release, exited = Event(), Event(), Event()
    calls = []

    def authenticate(actual, *, request_check, diagnostics):
        calls.append(actual)
        try:
            with database.session() as session:
                assert session.get(DshSessionLinkRecord, actual.run_id).generation == 1
                entered.set()
                assert release.wait(2)
                request_check()
        finally:
            exited.set()

    async def scenario():
        task = asyncio.create_task(routes._bounded_callback(request, authenticate, payload, database=database))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set() and calls == [payload]

    asyncio.run(scenario())
    engine.dispose()
