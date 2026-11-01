"""Pure RO scheduling seams; no SQLite, HTTP, SDK, model or product start."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import logging
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

SOURCE = Path(__file__).resolve().parents[2]
ARTIFACT = SOURCE.parent if SOURCE.name == "candidate" else SOURCE / "artifacts/core-goal-20261011/runtime-binding-ro-wait59_03"
RUNTIME = "packages/runtime_adapters/dsh_runtime/web_runtime.py"
PROJECTOR = "packages/runtime_adapters/dsh_runtime/cancellation_projection.py"


def load(relative: str, suffix: str):
    name = f"packages.runtime_adapters.dsh_runtime._ro_wait59_03_{suffix}"
    spec = importlib.util.spec_from_file_location(name, SOURCE / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


web = load(RUNTIME, "web")
projection = load(PROJECTOR, "projection")


class Reads:
    def __init__(self, replies, *, invoke=False):
        self.replies = list(replies)
        self.timeouts = []
        self.invoke = invoke

    async def read(self, operation, *, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        if self.invoke:
            try:
                operation(SimpleNamespace())
            except ValueError as error:
                return web.ProjectionRead("invalid", limitation=str(error))
        assert self.replies, "unexpected resubmission"
        return self.replies.pop(0)


def runtime(reads):
    instance = web.DshWebResearchRuntime.__new__(web.DshWebResearchRuntime)
    instance.cancellation_projector = reads
    instance.proof_read_wait_seconds = 15.0
    instance.binding_wait_seconds = 5.0
    anchor = SimpleNamespace(bundle_hash="a" * 64)
    instance._task_anchor = ContextVar("ro_wait59_anchor", default=anchor)
    instance._selection = ContextVar("ro_wait59_selection", default=None)
    instance._binding_deadline = ContextVar("ro_wait59_deadline", default=datetime.now(UTC) + timedelta(seconds=1))
    instance._proof_operation_deadline = ContextVar("ro_wait59_proof_deadline", default=None)
    return instance


async def read_bounded(reads, *, timeout=0.15, deadline=None):
    return await runtime(reads)._read_binding_with_contention_wait(
        lambda _session: None, timeout_seconds=timeout,
        deadline_at=deadline or datetime.now(UTC) + timedelta(seconds=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["cancellation_projection_busy", "cancellation_projection_read_busy"])
async def test_finished_ro_contention_can_reenter_with_decreasing_budget(code):
    reads = Reads([web.ProjectionRead("pending", limitation=code), web.ProjectionRead("ready", "verified")])
    result = await read_bounded(reads)
    assert result.state == "ready" and result.value == "verified"
    assert len(reads.timeouts) == 2 and 0 < reads.timeouts[1] < reads.timeouts[0] <= 0.15


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["cancellation_projection_db_busy", "cancellation_projection_closed", "cancellation_projection_failed", "cancellation_projection_requires_file_sqlite"])
async def test_other_pending_states_never_resubmit(code):
    outcome = web.ProjectionRead("pending", limitation=code)
    reads = Reads([outcome])
    assert await read_bounded(reads) is outcome
    assert len(reads.timeouts) == 1


@pytest.mark.asyncio
async def test_same_submitted_future_is_polled_not_replayed():
    class Pending:
        polls = 0

        def poll(self):
            self.polls += 1
            return web.ProjectionRead("ready", "original_future")

    pending = Pending()
    reads = Reads([web.ProjectionRead("pending", limitation="cancellation_projection_wait_elapsed", pending=pending)])
    result = await read_bounded(reads)
    assert result.value == "original_future" and pending.polls == 1
    assert len(reads.timeouts) == 1


@pytest.mark.asyncio
async def test_full_budget_pending_preserves_one_future_and_fails_closed():
    class Pending:
        def poll(self):
            return web.ProjectionRead("pending", limitation="cancellation_projection_wait_elapsed", pending=self)

    pending = Pending()
    reads = Reads([web.ProjectionRead("pending", limitation="cancellation_projection_wait_elapsed", pending=pending)])
    result = await read_bounded(reads, timeout=0.04)
    assert result.state == "pending" and result.pending is pending and len(reads.timeouts) == 1


@pytest.mark.asyncio
async def test_absolute_deadline_does_not_get_a_fresh_wait_budget():
    reads = Reads([])
    result = await read_bounded(reads, deadline=datetime.now(UTC) - timedelta(seconds=1))
    assert result.state == "pending" and reads.timeouts == []


@pytest.mark.asyncio
async def test_cancelled_wait_propagates_without_resubmitting():
    class Cancelling(Reads):
        async def read(self, _operation, *, timeout_seconds):
            self.timeouts.append(timeout_seconds)
            raise asyncio.CancelledError()

    reads = Cancelling([])
    with pytest.raises(asyncio.CancelledError):
        await read_bounded(reads)
    assert len(reads.timeouts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ready", "invalid"])
async def test_late_delivery_cannot_exceed_original_monotonic_proof_budget(state):
    class Delayed(Reads):
        async def read(self, operation, *, timeout_seconds):
            self.timeouts.append(timeout_seconds)
            # Simulate a callback delivery that did not respect the async wait.
            await asyncio.sleep(0.035)
            return web.ProjectionRead(state, limitation="research_runtime_original_task_changed" if state == "invalid" else None)

    reads = Delayed([])
    result = await read_bounded(reads, timeout=0.01)
    assert result.state == "pending" and result.limitation == "cancellation_projection_wait_elapsed"
    assert len(reads.timeouts) == 1


def diagnostics(caplog):
    return [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records if record.getMessage().startswith("research_binding_read_wait ")]


@pytest.mark.asyncio
async def test_one_closed_diagnostic_after_slot_wait_without_payloads(caplog):
    reads = Reads([web.ProjectionRead("pending", limitation="cancellation_projection_busy"), web.ProjectionRead("ready")])
    with caplog.at_level(logging.INFO, logger=web.log.name):
        result = await read_bounded(reads)
    records = diagnostics(caplog)
    assert result.state == "ready" and len(records) == 1
    value = records[0]
    assert value["slot_rejections"] == 1 and value["accepted_ro_submissions"] == 1 and value["same_future_polls"] == 0
    assert value["final_state"] == "ready" and value["reason_code"] is None
    assert value["absolute_deadline_elapsed"] is False and value["proof_budget_elapsed"] is False
    assert set(value) == {"schema_version", "original_budget_seconds", "elapsed_ms", "slot_rejections", "same_future_polls", "accepted_ro_submissions", "final_state", "reason_code", "absolute_deadline_elapsed", "proof_budget_elapsed"}


@pytest.mark.asyncio
async def test_same_future_one_return_log_no_poll_spam(caplog):
    class Pending:
        def poll(self):
            return web.ProjectionRead("pending", limitation="cancellation_projection_wait_elapsed", pending=self)

    reads = Reads([web.ProjectionRead("pending", limitation="cancellation_projection_wait_elapsed", pending=Pending())])
    with caplog.at_level(logging.INFO, logger=web.log.name):
        result = await read_bounded(reads, timeout=0.06)
    records = diagnostics(caplog)
    assert result.state == "pending" and len(records) == 1
    assert records[0]["same_future_polls"] >= 1 and records[0]["accepted_ro_submissions"] == 1
    assert records[0]["slot_rejections"] == 0 and records[0]["proof_budget_elapsed"] is True
    assert len(reads.timeouts) == 1


@pytest.mark.asyncio
async def test_normal_uncontended_proof_does_not_log(caplog):
    with caplog.at_level(logging.INFO, logger=web.log.name):
        result = await read_bounded(Reads([web.ProjectionRead("ready")]))
    assert result.state == "ready" and diagnostics(caplog) == []


@pytest.mark.asyncio
async def test_diagnostic_failure_cannot_authorize_or_reject_proof(monkeypatch):
    def unavailable_log(*args, **kwargs):
        raise RuntimeError("private logger seam")

    monkeypatch.setattr(web.log, "info", unavailable_log)
    reads = Reads([web.ProjectionRead("pending", limitation="cancellation_projection_busy"), web.ProjectionRead("invalid", limitation="research_context_selection_permission_lost")])
    result = await read_bounded(reads)
    assert result.state == "invalid" and result.limitation == "research_context_selection_permission_lost"
    assert len(reads.timeouts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_code", ["research_execution_run_not_active", "research_runtime_original_task_changed", "research_context_selection_permission_lost"])
async def test_current_native_validator_rejection_never_authorizes_host(monkeypatch, closed_code):
    calls = []

    def reject(session, anchor, reference, owner, request_json, *, allow_cancelled):
        calls.append((reference, owner, request_json, allow_cancelled))
        raise ValueError(closed_code)

    monkeypatch.setattr(web, "verify_runtime_task_and_selection", reject)
    reads = Reads([], invoke=True)
    instance = runtime(reads)
    instance._selection.set(("original_reference", "original_owner", "original_whole_request"))
    with pytest.raises(AgentExecutionError) as error:
        await instance._assert_task_binding()
    assert error.value.cause_code == closed_code and error.value.retryable is False
    assert calls == [("original_reference", "original_owner", "original_whole_request", False)]
    assert len(reads.timeouts) == 1 and reads.timeouts[0] <= 15.0


def operational(code, *, native=True):
    original = sqlite3.OperationalError("private pure seam") if native else RuntimeError("private pure seam")
    if code is not None:
        original.sqlite_errorcode = code
    return OperationalError("SELECT private_seam", {}, original)


@pytest.mark.parametrize("code,native,expected", [(5, True, True), (6, True, True), (517, True, True), (262, True, True), (1, True, False), (10, True, False), (None, True, False), (True, True, False), (5, False, False), (-251, True, False)])
def test_only_native_numeric_sqlite_busy_locked_is_retryable(code, native, expected):
    assert projection._sqlite_read_contention(operational(code, native=native)) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only,code,expected", [(True, 5, "cancellation_projection_read_busy"), (True, 1, "cancellation_projection_db_busy"), (False, 5, "cancellation_projection_db_busy")])
async def test_write_unknown_outcome_keeps_original_nonretry_classification(read_only, code, expected):
    instance = projection.DshCancellationProjector.__new__(projection.DshCancellationProjector)
    instance._closed = False
    instance.unsupported = None
    instance._slots = threading.BoundedSemaphore(2)
    instance._executor = ThreadPoolExecutor(max_workers=2)

    def fail():
        raise operational(code)

    try:
        result = await instance._call(fail, 0.2, read_only=read_only)
        assert result.state == "pending" and result.limitation == expected and result.pending is None
    finally:
        instance._executor.shutdown(wait=True)


def functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_root58_preimage_and_every_other_function_are_exact():
    hashes = {RUNTIME: "c8ec33c72f1ee8b1a290f2a7b25b98de1e06137cff3f3320de2ab5203e9224a2", PROJECTOR: "bc9ae31bde50c190e27eb09aaa72a1f16238d234ad804f538f611efb71312fed"}
    allowed = {RUNTIME: {"_assert_task_binding"}, PROJECTOR: {"_call", "read", "execute"}}
    for relative, expected in hashes.items():
        before = ARTIFACT / "before" / relative
        assert hashlib.sha256(before.read_bytes()).hexdigest() == expected
        old, new = functions(before), functions(SOURCE / relative)
        for name, body in old.items():
            if name not in allowed[relative]:
                assert new[name] == body, name
        additions = set(new) - set(old)
        assert additions == ({"_read_binding_with_contention_wait", "finish"} if relative == RUNTIME else {"_sqlite_read_contention"})
    old_tree = ast.parse((ARTIFACT / "before" / RUNTIME).read_text(encoding="utf-8"))
    new_tree = ast.parse((SOURCE / RUNTIME).read_text(encoding="utf-8"))
    old_method = next(node for node in ast.walk(old_tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_assert_task_binding")
    new_method = next(node for node in ast.walk(new_tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_assert_task_binding")
    old_wait = next(node.value for node in ast.walk(old_method) if isinstance(node, ast.keyword) and node.arg == "timeout_seconds")
    new_wait = next(node.value for node in ast.walk(new_method) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "timeout_seconds" for target in node.targets))
    assert ast.dump(old_wait) == ast.dump(new_wait), "15s/original-deadline budget changed"
