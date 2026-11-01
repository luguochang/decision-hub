"""Finite owned-thread/private-SQLite lifetime gates, not full-original proof fixtures."""

import ast
import asyncio
import contextlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from fastapi import Header, HTTPException, Query, Request

from apps.hub_api import model_binding_request as binding_module
from apps.hub_api.model_binding_request import bind_model_request
from apps.hub_api.research_proof_request import await_original_research_read
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)


def payload():
    return DshModelDispatchBinding.model_validate(
        dict(
            schema_version="dsh-model-dispatch-binding.v1",
            run_id="run_" + "a" * 32,
            dsh_session_id="dsh_" + "b" * 64,
            request_hash="c" * 64,
            generation=1,
            plugin_build_hash="d" * 64,
            purpose="conversation",
            execution_session_id=None,
            parent_session_id=None,
            provider="private-control",
            model="private-model",
            reasoning_effort=None,
            temperature=None,
            max_tokens=None,
            stop_sequences_hash=None,
        )
    )


def request(kind="binding", deadline=None, *, budget_ms=1000):
    headers = []
    if deadline is not None:
        prefix = "model-binding" if kind == "binding" else "research-proof"
        headers = [
            (f"x-decision-hub-{prefix}-deadline-at".encode(), deadline.isoformat().encode()),
            (f"x-decision-hub-{prefix}-budget-ms".encode(), str(budget_ms).encode()),
        ]

    async def receive():
        await asyncio.sleep(0)
        return dict(type="http.request", body=b"", more_body=False)

    return Request(dict(type="http", method="GET", path="/private", headers=headers), receive)


class PrivateDatabase:
    def __init__(self, tmp_path, *, remaining=1):
        self.path = tmp_path / "private.sqlite3"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE effects (value INTEGER)")
        p = payload()
        self.link = SimpleNamespace(
            **{
                key: getattr(p, key)
                for key in (
                    "run_id",
                    "dsh_session_id",
                    "generation",
                    "request_hash",
                    "plugin_build_hash",
                )
            },
            deadline_at=datetime.now(UTC) + timedelta(seconds=remaining),
        )
        self.live = 0
        self.closed = 0

    @contextlib.contextmanager
    def session(self):
        connection = sqlite3.connect(self.path)
        self.live += 1
        session = SimpleNamespace(
            no_autoflush=contextlib.nullcontext(),
            get=lambda *args, **kwargs: (connection.execute("SELECT 1").fetchone(), self.link)[1],
            connection=connection,
        )
        try:
            yield session
            connection.commit()
        finally:
            connection.close()
            self.live -= 1
            self.closed += 1

    def count(self):
        with sqlite3.connect(self.path) as connection:
            return connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


@pytest.mark.asyncio
async def test_slow_proof_crosses_writer_slice_then_single_real_commit(tmp_path, monkeypatch):
    database = PrivateDatabase(tmp_path)
    monkeypatch.setattr(binding_module, "MAX_MODEL_BINDING_BUDGET_MS", 20)
    calls = []
    stages = []

    def bind(value, *, request_check, diagnostics, writer_start, source_lease):
        calls.append(1)
        with database.session() as session:
            time.sleep(0.08)
            request_check()
            stages.append("proof_finished")
            writer_start()
            stages.append("writer_started")
            session.connection.execute("INSERT INTO effects VALUES (1)")
            request_check()
        return value

    result = await bind_model_request(
        request(deadline=datetime.now(UTC) + timedelta(seconds=1)),
        SimpleNamespace(database=database, bind=bind),
        payload(),
    )
    assert result == payload() and calls == [1]
    assert stages == ["proof_finished", "writer_started"]
    assert database.count() == 1 and database.live == 0 and database.closed == 2


@pytest.mark.asyncio
async def test_durable_earlier_task_deadline_cannot_be_extended_or_write(tmp_path):
    database = PrivateDatabase(tmp_path, remaining=0.03)
    calls = []

    def bind(value, *, request_check, diagnostics, writer_start, source_lease):
        calls.append(1)
        with database.session():
            time.sleep(0.07)
            request_check()
        pytest.fail("expired original must not start writer")

    with pytest.raises(ValueError, match="^dsh_model_binding_original_deadline_elapsed$"):
        await bind_model_request(
            request(deadline=datetime.now(UTC) + timedelta(seconds=1)),
            SimpleNamespace(database=database, bind=bind),
            payload(),
        )
    assert calls == [1] and database.count() == 0 and database.live == 0


@pytest.mark.asyncio
async def test_request_and_writer_budgets_have_distinct_stable_causes(tmp_path, monkeypatch):
    monkeypatch.setattr(binding_module, "MAX_MODEL_BINDING_BUDGET_MS", 20)

    for phase, expected in (
        ("request", "dsh_model_binding_budget_elapsed"),
        ("writer", "dsh_model_binding_writer_budget_elapsed"),
    ):
        case = tmp_path / phase
        case.mkdir()
        database = PrivateDatabase(case)
        calls = []

        def bind(
            value, *, request_check, diagnostics, writer_start, source_lease,
            _calls=calls, _database=database, _phase=phase,
        ):
            _calls.append(1)
            with _database.session() as session:
                if _phase == "writer":
                    writer_start()
                    session.connection.execute("INSERT INTO effects VALUES (1)")
                time.sleep(0.06)
                request_check()
            return value

        with pytest.raises(ValueError, match=f"^{expected}$"):
            await bind_model_request(
                request(
                    deadline=datetime.now(UTC) + timedelta(seconds=1),
                    budget_ms=20 if phase == "request" else 1000,
                ),
                SimpleNamespace(database=database, bind=bind),
                payload(),
            )
        assert calls == [1] and database.count() == 0 and database.live == 0


@pytest.mark.asyncio
async def test_invalid_identity_never_submits_a_callback_or_writer(tmp_path):
    database = PrivateDatabase(tmp_path)
    database.link.request_hash = "f" * 64

    def bind(*args, **kwargs):
        pytest.fail("identity mismatch cannot reach original writer")

    with pytest.raises(ValueError, match="dsh_model_dispatch_identity_mismatch"):
        await bind_model_request(
            request(deadline=datetime.now(UTC) + timedelta(seconds=1)),
            SimpleNamespace(database=database, bind=bind),
            payload(),
        )
    assert database.count() == 0 and database.live == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["binding", "research_detail"])
async def test_repeated_cancel_drains_same_native_session_without_effect(tmp_path, kind):
    database = PrivateDatabase(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def operation(check):
        calls.append(1)
        with database.session():
            entered.set()
            assert release.wait(1)
            check()
        return payload()

    deadline = datetime.now(UTC) + timedelta(seconds=1)
    if kind == "binding":

        def bind(value, *, request_check, **kwargs):
            return operation(request_check)

        pending = asyncio.create_task(
            bind_model_request(
                request(deadline=deadline), SimpleNamespace(database=database, bind=bind), payload()
            )
        )
    else:
        pending = asyncio.create_task(
            await_original_research_read(
                request(kind, deadline), database, payload().run_id, operation, kind=kind
            )
        )
    while not entered.is_set():
        await asyncio.sleep(0.001)
    pending.cancel()
    await asyncio.sleep(0.005)
    pending.cancel()
    await asyncio.sleep(0.005)
    assert not pending.done() and database.live == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert calls == [1] and database.live == 0 and database.count() == 0


@pytest.mark.asyncio
async def test_owned_ro_survives_short_observation_and_preserves_permission_failure(tmp_path):
    database = PrivateDatabase(tmp_path)

    def read(check):
        with database.session():
            time.sleep(0.07)
            check()
        return "actual-reader-value"

    value = await await_original_research_read(
        request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
        database,
        payload().run_id,
        read,
        kind="research_detail",
    )
    assert value == "actual-reader-value" and database.live == 0

    def denied(check):
        check()
        raise PermissionError("private-control-denied")

    with pytest.raises(PermissionError, match="private-control-denied"):
        await await_original_research_read(
            request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
            database,
            payload().run_id,
            denied,
            kind="research_detail",
        )


@pytest.mark.asyncio
async def test_actual_detail_route_native_auth_and_default_reader_contract(tmp_path):
    # Extract only this route. Never import main/global create_app or initialize a DB.
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "apps/hub_api/main.py").exists() and (parent / "pyproject.toml").exists()
    )
    source = root / "apps/hub_api/main.py"
    overlay = Path(__file__).resolve().parents[2] / "apps/hub_api/main.py"
    if overlay.exists():
        source = overlay
    tree = ast.parse(source.read_text(encoding="utf-8"))
    route = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "research_run_detail"
    )
    route.decorator_list = []
    database = PrivateDatabase(tmp_path)
    calls = []

    def auth(value):
        calls.append("auth")
        if value != "private-test":
            raise HTTPException(401, "private-test-auth-required")

    def read(run_id):
        calls.append("reader")
        return {"run_id": run_id}

    namespace = dict(
        Request=Request,
        Header=Header,
        ResearchRunDetailView=dict,
        HTTPException=HTTPException,
        research_query=SimpleNamespace(get=read),
        frozen_review_bootstrap=None,
        require_dsh_callback_secret=auth,
        db=database,
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[])),
            str(source),
            "exec",
        ),
        namespace,
    )
    endpoint = namespace["research_run_detail"]
    assert await endpoint(request("research_detail"), payload().run_id, None) == {
        "run_id": payload().run_id
    }
    assert calls == ["reader"]
    calls.clear()
    with pytest.raises(HTTPException) as error:
        await endpoint(
            request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
            payload().run_id,
            None,
        )
    assert error.value.status_code == 401 and calls == ["auth"]
    calls.clear()
    assert await endpoint(
        request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
        payload().run_id,
        "private-test",
    ) == {"run_id": payload().run_id}
    assert calls == ["auth", "reader"] and database.live == 0


def test_kernel_preparation_receives_original_checkpoint_before_any_writer(monkeypatch):
    from packages.kernel.decision_hub_kernel.application import model_dispatch as module

    service = object.__new__(module.ModelDispatchService)
    service.database = object()
    checkpoints, writers = [], []

    def check():
        checkpoints.append(1)

    def invalid_original(*args, checkpoint, **kwargs):
        assert checkpoint is check
        checkpoint()
        raise ValueError("private-original-invalid")

    monkeypatch.setattr(module, "prepare_originals", invalid_original)
    with pytest.raises(ValueError, match="private-original-invalid"):
        service.bind(payload(), request_check=check, writer_start=lambda: writers.append(1))
    assert len(checkpoints) >= 2 and writers == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["dsh_session_prompt", "dsh_business_status"])
async def test_actual_context_routes_require_native_auth_and_prompt_generation(tmp_path, name):
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").exists()
    )
    source = Path(__file__).resolve().parents[2] / "apps/hub_api/main.py"
    if not source.exists():
        source = root / "apps/hub_api/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    route = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    route.decorator_list = []
    database = PrivateDatabase(tmp_path)
    calls = []

    def auth(value):
        calls.append("auth")
        if value != "private-test":
            raise HTTPException(401, "private-test-auth-required")

    def read(*args, **kwargs):
        calls.append("reader")
        return {"actual": True}

    namespace = dict(
        Request=Request,
        Header=Header,
        Query=Query,
        Literal=Literal,
        DshBusinessStatus=dict,
        DshSessionPrompt=dict,
        HTTPException=HTTPException,
        research_query=SimpleNamespace(business_status=read),
        dsh_sessions=SimpleNamespace(get_prompt=read),
        frozen_review_bootstrap=None,
        require_dsh_callback_secret=auth,
        db=database,
    )
    compiled = compile(
        ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[])), str(source), "exec"
    )
    exec(compiled, namespace)
    endpoint = namespace[name]
    kwargs = {"generation": 1} if name == "dsh_session_prompt" else {"projection": "full"}
    with pytest.raises(HTTPException) as error:
        await endpoint(
            request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
            payload().run_id,
            bridge_key=None,
            **kwargs,
        )
    assert error.value.status_code == 401 and calls == ["auth"]
    calls.clear()
    assert await endpoint(
        request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
        payload().run_id,
        bridge_key="private-test",
        **kwargs,
    ) == {"actual": True}
    assert calls == ["auth", "reader"] and database.live == 0
    if name == "dsh_session_prompt":
        calls.clear()
        with pytest.raises(HTTPException) as mismatch:
            await endpoint(
                request("research_detail", datetime.now(UTC) + timedelta(seconds=1)),
                payload().run_id,
                bridge_key="private-test",
                generation=2,
            )
        assert mismatch.value.detail == "dsh_model_dispatch_identity_mismatch"
        assert calls == ["auth"]
