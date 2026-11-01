"""Cancelled-Run recovery uses real durable receipts and controlled Host RPCs."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select, text
from test_cancel_projection import NOW, completion, fixture, identity
from test_dsh_web_runtime import _request

from packages.contracts_py.decision_hub_contracts import (
    DshRunSessionLinkView,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
    execution_request_role,
    prompt_integrity_role,
    read_prompt_integrity,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    canonical_recipe,
    method_definition,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector
from packages.runtime_adapters.dsh_runtime.cancelled_recovery import CancelledRunReconciler
from packages.runtime_adapters.dsh_runtime.web_host_client import (
    DshRecoveryBinding,
    DshWebHostClient,
    DshWebHostConfig,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime


def frozen_run(
    db: Database, *, event_id: str = "event-web-host", execution_receipt: bool = True,
) -> tuple[DshSessionLinkService, DshSessionSubmit]:
    run_id, _ = RunService(db, clock=lambda: NOW).create(event_id)
    request = _request(run_id).model_copy(update={
        "event_id": event_id, "deadline_at": NOW + timedelta(seconds=30),
    })
    ResearchTaskService(db, clock=lambda: NOW).freeze(request)
    submit = DshWebResearchRuntime._submission(request)
    links = DshSessionLinkService(db, clock=lambda: NOW)
    definition = method_definition(
        request, submit, identity(), runtime_id="dsh", runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1", prompt_policy_hash="e" * 64,
    )
    ResearchMethodService(db, clock=lambda: NOW).freeze(
        run_id, definition, request_hash=submit.request_hash,
    )
    links.reserve(submit, identity())
    links.store_prompt(DshSessionPrompt(
        schema_version="dsh-session-prompt.v1", run_id=run_id,
        dsh_session_id=submit.deterministic_session_id,
        request_id=submit.deterministic_request_id, request_hash=submit.request_hash,
        generation=1, prompt="Frozen initial research prompt: 宏观事件与黄金。",
    ), execution_request=request if execution_receipt else None)
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "cancelled"
    return links, submit


def database(path: Path) -> Database:
    db = Database(f"sqlite+pysqlite:///{path}")
    db.create_all()
    return db


class RecoveryHost:
    def __init__(self, state: str = "running") -> None:
        self.state = state
        self.calls: list[tuple[str, DshRecoveryBinding]] = []

    def status_value(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        return DshSessionStatus.model_validate({
            "schema_version": "dsh-session-status.v1", "run_id": binding.run_id,
            "dsh_session_id": binding.dsh_session_id, "generation": binding.generation,
            "state": self.state, "last_seq": 3, "observed_at": NOW + timedelta(seconds=5),
            "error_code": "provider_failed" if self.state == "failed" else None,
        })

    async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        self.calls.append(("status", binding))
        return self.status_value(binding)

    async def recovery_cancel(
        self, binding: DshRecoveryBinding, reason: str,
    ) -> DshSessionStatus:
        assert reason == "product_run_cancelled"
        self.calls.append(("cancel", binding))
        self.state = "cancelled"
        return self.status_value(binding)

    async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
        self.calls.append(("result", binding))
        return DshSessionResult(
            schema_version="dsh-session-result.v1", run_id=binding.run_id,
            dsh_session_id=binding.dsh_session_id, generation=binding.generation,
            final_response="Already finished before owner cancellation", events_json="[]",
            finish_reason="stop", last_seq=3,
            trace_ref=f"dsh://{binding.dsh_session_id}/trace",
            result_hash=hashlib.sha256(
                b"Already finished before owner cancellation\0[]"
            ).hexdigest(), started_at=NOW, finished_at=NOW + timedelta(seconds=4),
        )

    async def submit(self, _payload: DshSessionSubmit) -> None:
        raise AssertionError("recovery must never submit or create a repair")

    async def close(self) -> None:
        pass


@pytest.mark.parametrize("state", ["running", "cancelled", "completed", "failed"])
async def test_restart_recovers_exact_original_binding_without_submission(
    tmp_path: Path, state: str,
) -> None:
    path = tmp_path / "restart.db"
    original = database(path)
    _links, submit = frozen_run(original)
    with original.session() as session:
        before_count = session.query(RunRecord).count()
        prompt = session.get(DshSessionPromptRecord, (submit.run_id, 1))
        assert prompt is not None
        expected_prompt_hash = read_prompt_integrity(session, prompt)
    original.engine.dispose()
    reopened = Database(f"sqlite+pysqlite:///{path}")  # No create_all or migration on recovery.
    links = DshSessionLinkService(reopened)
    host = RecoveryHost(state)
    runtime = DshWebResearchRuntime(links, host)  # type: ignore[arg-type]
    try:
        result = await runtime.reconcile_cancelled_runs()
        assert result["confirmed"] == 1 and result["limitations"] == {}
        expected = ["status", "cancel"] if state == "running" else ["status"]
        if state == "completed":
            expected.append("result")
        assert [name for name, _binding in host.calls] == expected
        for _name, binding in host.calls:
            assert binding.run_id == submit.run_id
            assert binding.dsh_session_id == submit.deterministic_session_id
            assert binding.generation == 1 and binding.request_hash == submit.request_hash
            assert binding.deadline_at == submit.deadline_at
            assert binding.max_tool_calls == submit.max_tool_calls
            assert binding.model_step_timeout_ms == submit.model_step_timeout_ms
            assert binding.prompt_hash == expected_prompt_hash
        final = links.get(submit.run_id)
        assert final is not None and final.state == ("cancelled" if state == "running" else state)
        with reopened.session() as session:
            assert session.query(RunRecord).count() == before_count
            assert session.get(RunRecord, submit.run_id).status == "cancelled"  # type: ignore
            assert session.query(DshSessionPromptRecord).count() == 1
        again = await runtime.reconcile_cancelled_runs()
        assert again["scanned"] == 0 and len(host.calls) == len(expected)
    finally:
        await runtime.close()
        reopened.engine.dispose()


@pytest.mark.parametrize("damage,expected", [
    ("prompt_receipt_missing", "prompt_integrity_missing"),
    ("prompt_text", "prompt_integrity_invalid"),
    ("prompt_receipt_hash", "prompt_integrity_invalid"),
    ("method_receipt_missing", "recovery_method_receipt_missing"),
    ("method_hash", "research_method_receipt_corrupt"),
    ("method_resigned_budget", "recovery_frozen_budget_mismatch"),
    ("old_null_budget", "recovery_frozen_budget_missing"),
    ("deadline", "recovery_frozen_budget_mismatch"),
    ("task_hash", "research_task_request_invalid"),
])
async def test_missing_or_tampered_original_proof_never_calls_host(
    tmp_path: Path, damage: str, expected: str,
) -> None:
    db = database(tmp_path / "tampered.db")
    links, submit = frozen_run(db)
    with db.session() as session:
        row = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == submit.run_id,
            RunRoleOutputRecord.role == (
                METHOD_ROLE if damage.startswith("method_")
                else "research_task_request" if damage == "task_hash"
                else prompt_integrity_role(1)
            ),
        ))
        assert row is not None
        if damage.endswith("receipt_missing"):
            session.delete(row)
        elif damage in {"method_hash", "prompt_receipt_hash", "task_hash"}:
            row.payload_hash = "0" * 64
        elif damage == "method_resigned_budget":
            body = json.loads(row.payload_json)
            body["definition"]["execution_budget"]["max_tool_calls"] += 1
            body["method_hash"] = recipe_hash(body["definition"])
            row.payload_json = canonical_recipe(body)
            row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
        elif damage == "prompt_text":
            prompt = session.get(DshSessionPromptRecord, (submit.run_id, 1))
            assert prompt is not None
            prompt.prompt += " unauthorized replacement"
        else:
            link = session.get(DshSessionLinkRecord, submit.run_id)
            assert link is not None
            if damage == "old_null_budget":
                link.model_step_timeout_ms = None
            elif damage == "deadline":
                link.deadline_at += timedelta(seconds=1)  # type: ignore[operator]
    host = RecoveryHost()
    projector = DshCancellationProjector(links)
    try:
        report = await CancelledRunReconciler(projector, host).tick()
        assert report["limitations"] == {expected: 1}
        assert report["skipped"] == 1 and report["attempted"] == 0
        assert host.calls == []
        assert links.get(submit.run_id).state == "admitted"  # type: ignore[union-attr]
    finally:
        await projector.close()
        db.engine.dispose()


async def test_old_host_protocol_is_unsupported_without_constructing_submit(tmp_path: Path) -> None:
    db, links, _submit = fixture(tmp_path / "old-host.db")
    projector = DshCancellationProjector(links)
    try:
        report = await CancelledRunReconciler(projector, object()).tick()
        assert report["limitations"] == {"recovery_host_unsupported": 1}
        assert report["attempted"] == 0
    finally:
        await projector.close()
        db.engine.dispose()


async def test_incomplete_first_page_does_not_starve_valid_later_runs(tmp_path: Path) -> None:
    db = database(tmp_path / "fair.db")
    runs = [frozen_run(db) for _ in range(5)]
    runs.sort(key=lambda pair: pair[1].run_id)
    with db.session() as session:
        for _links, submit in runs[:4]:
            row = session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == submit.run_id,
                RunRoleOutputRecord.role == prompt_integrity_role(1),
            ))
            assert row is not None
            session.delete(row)
    projector = DshCancellationProjector(runs[0][0])
    host = RecoveryHost()
    recovery = CancelledRunReconciler(projector, host)
    try:
        first = await recovery.tick(limit=4)
        second = await recovery.tick(limit=4)
        third = await recovery.tick(limit=4)
        assert first["skipped"] == 4 and first["attempted"] == 0
        assert second["confirmed"] == 1
        assert third["backoff"] == 4 and third["attempted"] == 0
        assert {binding.run_id for _method, binding in host.calls} == {runs[-1][1].run_id}
    finally:
        await projector.close()
        db.engine.dispose()


async def test_two_timed_out_real_threads_keep_slots_until_they_finish(tmp_path: Path) -> None:
    db, links, _submit = fixture(tmp_path / "threads.db")
    projector = DshCancellationProjector(links)
    assert projector.sessions is not None
    entered: list[int] = []
    release = threading.Event()

    def hold_sql(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if statement == "SELECT 7":
            entered.append(threading.get_ident())
            assert release.wait(3)

    event.listen(projector.sessions.engine, "after_cursor_execute", hold_sql)
    try:
        first = await projector.read(lambda session: session.scalar(text("SELECT 7")),
                                     timeout_seconds=.02)
        second = await projector.read(lambda session: session.scalar(text("SELECT 7")),
                                      timeout_seconds=.02)
        third = await projector.read(lambda session: session.scalar(text("SELECT 7")),
                                     timeout_seconds=.02)
        assert first.limitation == second.limitation == "cancellation_projection_wait_elapsed"
        assert third.limitation == "cancellation_projection_busy"
        assert len(set(entered)) == 2
        release.set()
        ready = None
        for _attempt in range(20):
            await asyncio.sleep(.01)
            ready = await projector.read(lambda session: session.scalar(text("SELECT 8")))
            if ready.state == "ready":
                break
        assert ready is not None and ready.state == "ready" and ready.value == 8
    finally:
        release.set()
        await projector.close()
        db.engine.dispose()


async def test_dedicated_projection_ignores_exhausted_shared_queue_pool(tmp_path: Path) -> None:
    db, links, submit = fixture(tmp_path / "pool.db")
    projector = DshCancellationProjector(links)
    main_thread = threading.get_ident()
    try:
        with ExitStack() as stack:
            # Installed SQLite QueuePool defaults 5 persistent + 10 overflow.
            for _index in range(15):
                stack.enter_context(db.engine.connect())
            policy = await projector.read(lambda session: (
                session.scalar(text("PRAGMA busy_timeout")), threading.get_ident(),
            ))
            assert policy.value is not None
            assert policy.value[0] == 50 and policy.value[1] != main_thread
            result = await projector.project(completion(submit), submit.request_hash)
            assert result.state == "confirmed"
        with db.engine.connect() as connection:
            assert connection.scalar(text("PRAGMA busy_timeout")) == 5000
    finally:
        await projector.close()
        db.engine.dispose()


@pytest.mark.parametrize("wrong", ["run", "session", "generation", "hash"])
async def test_wrong_host_response_identity_or_hash_cannot_project(
    tmp_path: Path, wrong: str,
) -> None:
    db = database(tmp_path / "wrong-response.db")
    links, submit = frozen_run(db)

    class WrongHost(RecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            original = await super().recovery_status(binding)
            if wrong == "hash":
                return original
            return original.model_copy(update={
                "run_id" if wrong == "run" else "dsh_session_id" if wrong == "session"
                else "generation": "other" if wrong != "generation" else binding.generation + 1,
            })

        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            return (await super().recovery_result(binding)).model_copy(
                update={"result_hash": "0" * 64},
            )

    projector = DshCancellationProjector(links)
    try:
        report = await CancelledRunReconciler(projector, WrongHost("completed")).tick()
        assert report["confirmed"] == 0 and report["pending"] == 1
        assert links.get(submit.run_id).state == "admitted"  # type: ignore[union-attr]
    finally:
        await projector.close()
        db.engine.dispose()


async def test_generation_switch_during_rpc_cannot_project_old_completion(tmp_path: Path) -> None:
    db = database(tmp_path / "late-generation.db")
    links, submit = frozen_run(db)
    original_prompt = links.get_prompt(submit.run_id)
    assert original_prompt is not None
    actual_request = ResearchTaskService(db).get(submit.run_id)
    assert actual_request is not None

    class LateHost(RecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            links.complete(completion(submit, "completed"))
            session_id, request_id = links.deterministic_ids(submit.run_id, submit.request_hash, 2)
            links.reserve(submit.model_copy(update={
                "generation": 2, "deterministic_session_id": session_id,
                "deterministic_request_id": request_id,
            }), identity())
            links.store_prompt(original_prompt.model_copy(update={
                "generation": 2, "request_id": request_id, "prompt": "Actual repair prompt",
            }), execution_request=actual_request)
            return await super().recovery_status(binding)

    projector = DshCancellationProjector(links)
    try:
        report = await CancelledRunReconciler(projector, LateHost("cancelled")).tick()
        assert report["confirmed"] == 0
        assert report["limitations"] == {"recovery_binding_changed": 1}
        final = links.get(submit.run_id)
        assert final is not None and (final.generation, final.state) == (2, "admitted")
    finally:
        await projector.close()
        db.engine.dispose()


@pytest.mark.parametrize("damage", [
    "method_receipt", "prompt_text", "budget", "deadline", "execution_context_resigned",
])
async def test_held_status_rechecks_whole_frozen_binding_before_cancel_rpc(
    tmp_path: Path, damage: str,
) -> None:
    db = database(tmp_path / "held-proof.db")
    links, submit = frozen_run(db)
    entered = asyncio.Event()
    release = asyncio.Event()

    class HeldStatus(RecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            entered.set()
            await release.wait()
            return await super().recovery_status(binding)

    projector = DshCancellationProjector(links)
    host = HeldStatus()
    try:
        tick = asyncio.create_task(CancelledRunReconciler(projector, host).tick())
        await asyncio.wait_for(entered.wait(), 1)
        with db.session() as session:
            if damage == "method_receipt":
                row = session.scalar(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == submit.run_id,
                    RunRoleOutputRecord.role == METHOD_ROLE,
                ))
                assert row is not None
                row.payload_hash = "0" * 64
            elif damage == "execution_context_resigned":
                execution = session.scalar(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == submit.run_id,
                    RunRoleOutputRecord.role == execution_request_role(1),
                ))
                anchor = session.scalar(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == submit.run_id,
                    RunRoleOutputRecord.role == prompt_integrity_role(1),
                ))
                assert execution is not None and anchor is not None
                body = json.loads(execution.payload_json)
                body["request"]["input_evidence"][0]["excerpt"] += " resigned drift"
                body["request_hash"] = recipe_hash(body["request"])
                execution.payload_json = canonical_recipe(body)
                execution.payload_hash = hashlib.sha256(execution.payload_json.encode()).hexdigest()
                anchor_body = json.loads(anchor.payload_json)
                anchor_body["execution_request_receipt_hash"] = execution.payload_hash
                anchor.payload_json = json.dumps(anchor_body, sort_keys=True, separators=(",", ":"))
                anchor.payload_hash = hashlib.sha256(anchor.payload_json.encode()).hexdigest()
            elif damage == "prompt_text":
                prompt = session.get(DshSessionPromptRecord, (submit.run_id, 1))
                assert prompt is not None
                prompt.prompt += " changed while Host status awaited"
            else:
                link = session.get(DshSessionLinkRecord, submit.run_id)
                assert link is not None
                if damage == "budget":
                    assert link.max_tool_calls is not None
                    link.max_tool_calls += 1
                else:
                    assert link.deadline_at is not None
                    link.deadline_at += timedelta(seconds=1)
        release.set()
        report = await tick
        assert report["confirmed"] == 0
        assert report["limitations"] == {
            "recovery_binding_changed" if damage == "execution_context_resigned"
            else "recovery_cancel_authorization_unavailable": 1,
        }
        assert [name for name, _binding in host.calls] == ["status"]
        assert links.get(submit.run_id).state == "admitted"  # type: ignore[union-attr]
    finally:
        release.set()
        await projector.close()
        db.engine.dispose()


async def test_completed_result_cannot_regress_host_terminal_sequence(tmp_path: Path) -> None:
    db = database(tmp_path / "result-seq.db")
    links, submit = frozen_run(db)

    class EarlierResult(RecoveryHost):
        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            return (await super().recovery_result(binding)).model_copy(update={"last_seq": 2})

    projector = DshCancellationProjector(links)
    try:
        report = await CancelledRunReconciler(projector, EarlierResult("completed")).tick()
        assert report["limitations"] == {"recovery_result_sequence_regression": 1}
        assert report["confirmed"] == 0
        assert links.get(submit.run_id).state == "admitted"  # type: ignore[union-attr]
    finally:
        await projector.close()
        db.engine.dispose()


@pytest.mark.parametrize("damage", [None, "initial_prompt", "over_budget"])
async def test_later_evidence_generation_uses_frozen_remaining_budget_and_first_prompt_anchor(
    tmp_path: Path, damage: str | None,
) -> None:
    db = database(tmp_path / "later-round.db")
    links, submit = frozen_run(db)
    original_request = ResearchTaskService(db).get(submit.run_id)
    original_prompt = links.get_prompt(submit.run_id)
    assert original_request is not None and original_prompt is not None
    links.complete(completion(submit, "completed"))
    session_id, request_id = links.deterministic_ids(submit.run_id, submit.request_hash, 2)
    links.reserve(submit.model_copy(update={
        "generation": 2, "deterministic_session_id": session_id,
        "deterministic_request_id": request_id,
    }), identity())
    allowance = submit.max_tool_calls + 1 if damage == "over_budget" else submit.max_tool_calls - 1
    active_request = original_request.model_copy(update={
        "current_round": 2, "execution_budget": original_request.execution_budget.model_copy(
            update={"max_tool_calls": allowance},
        ),
    })
    links.store_prompt(original_prompt.model_copy(update={
        "generation": 2, "request_id": request_id,
        "prompt": "Frozen round two with remaining tools",
    }), execution_request=active_request)
    if damage == "initial_prompt":
        with db.session() as session:
            first = session.get(DshSessionPromptRecord, (submit.run_id, 1))
            assert first is not None
            first.prompt += " corrupt first-generation text"
    projector = DshCancellationProjector(links)
    host = RecoveryHost("cancelled")
    try:
        report = await CancelledRunReconciler(projector, host).tick()
        if damage is None:
            assert report["confirmed"] == 1 and report["limitations"] == {}
            binding = host.calls[-1][1]
            assert binding.generation == 2 and binding.max_tool_calls == submit.max_tool_calls
            assert binding.execution_request_hash == recipe_hash(active_request.model_dump(
                mode="json",
            ))
            assert binding.first_execution_request_hash != binding.execution_request_hash
        else:
            assert report["confirmed"] == 0 and host.calls == []
            assert report["limitations"] == {
                "prompt_integrity_invalid" if damage == "initial_prompt"
                else "recovery_execution_task_budget_mismatch": 1,
            }
    finally:
        await projector.close()
        db.engine.dispose()


async def test_rpc_budget_and_external_shutdown_preserve_pending_link(tmp_path: Path) -> None:
    db = database(tmp_path / "budget.db")
    links, submit = frozen_run(db)
    cancelled = asyncio.Event()

    class HeldHost(RecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("never resolves")

    projector = DshCancellationProjector(links)
    recovery = CancelledRunReconciler(projector, HeldHost())
    try:
        report = await recovery.tick(time_budget_seconds=.05)
        assert report["limitations"] == {"recovery_rpc_budget_elapsed": 1}
        assert cancelled.is_set() and report["pending"] == 1
        assert links.get(submit.run_id).state == "admitted"  # type: ignore[union-attr]
        recovery = CancelledRunReconciler(projector, HeldHost())
        task = asyncio.create_task(recovery.tick())
        await asyncio.sleep(.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await projector.close()
        db.engine.dispose()


@pytest.mark.parametrize("state", ["running", "cancelled"])
async def test_held_later_status_rejects_resigned_first_prompt_before_cancel_or_projection(
    tmp_path: Path, state: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database(tmp_path / "held-first-prompt.db")
    links, submit = frozen_run(db)
    request = ResearchTaskService(db).get(submit.run_id)
    prompt = links.get_prompt(submit.run_id)
    assert request is not None and prompt is not None
    links.complete(completion(submit, "completed"))
    session_id, request_id = links.deterministic_ids(submit.run_id, submit.request_hash, 2)
    links.reserve(submit.model_copy(update={
        "generation": 2, "deterministic_session_id": session_id,
        "deterministic_request_id": request_id,
    }), identity())
    links.store_prompt(prompt.model_copy(update={
        "generation": 2, "request_id": request_id, "prompt": "Frozen generation two",
    }), execution_request=request)
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldHost(RecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            entered.set()
            await release.wait()
            return await super().recovery_status(binding)

    projector = DshCancellationProjector(links)
    host = HeldHost(state)
    projection_started = threading.Event()
    continue_projection = threading.Event()
    actual_errors: list[tuple[str, int]] = []
    actual_futures: list[Future[Any]] = []
    before_columns: dict[str, Any] = {}
    if state == "cancelled":
        with db.session() as session:
            original_link = session.get(DshSessionLinkRecord, submit.run_id)
            assert original_link is not None
            before_columns = {
                column.name: getattr(original_link, column.name)
                for column in DshSessionLinkRecord.__table__.columns
            }
        original_complete = projector._links.complete
        original_submit = projector._executor.submit

        def observed_complete(
            callback: DshSessionCompletion, **kwargs: Any,
        ) -> DshRunSessionLinkView:
            projection_started.set()
            # Deliberately let the unchanged production 250ms await expire.
            # The real writer/validator still runs afterwards; no authority or
            # pending result alone is sufficient for this test to pass.
            assert continue_projection.wait(5)
            try:
                return original_complete(callback, **kwargs)
            except ValueError as exc:
                actual_errors.append((str(exc), threading.get_ident()))
                raise

        def observed_submit(
            operation: Callable[..., Any], *args: Any, **kwargs: Any,
        ) -> Future[Any]:
            future = original_submit(operation, *args, **kwargs)
            actual_futures.append(future)
            return future

        monkeypatch.setattr(projector._links, "complete", observed_complete)
        monkeypatch.setattr(projector._executor, "submit", observed_submit)
    try:
        task = asyncio.create_task(CancelledRunReconciler(projector, host).tick())
        await asyncio.wait_for(entered.wait(), 1)
        with db.session() as session:
            first_prompt = session.get(DshSessionPromptRecord, (submit.run_id, 1))
            anchor = session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == submit.run_id,
                RunRoleOutputRecord.role == prompt_integrity_role(1),
            ))
            assert first_prompt is not None and anchor is not None
            first_prompt.prompt += " replaced and resigned while status held"
            body = json.loads(anchor.payload_json)
            body["prompt_hash"] = hashlib.sha256(first_prompt.prompt.encode()).hexdigest()
            anchor.payload_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
            anchor.payload_hash = hashlib.sha256(anchor.payload_json.encode()).hexdigest()
        release.set()
        report = await task
        if state == "cancelled":
            assert report["limitations"] == {"cancellation_projection_wait_elapsed": 1}
            assert report["pending"] == 1 and projection_started.is_set()
            actual_future = actual_futures[-1]
            assert not actual_future.done()
            continue_projection.set()
            actual_result = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(actual_future)), 2,
            )
            assert actual_future.done()
            assert actual_result.state == "invalid"
            assert actual_result.limitation == "recovery_binding_changed"
            assert [code for code, _thread in actual_errors] == ["recovery_binding_changed"]
            assert actual_errors[0][1] != threading.get_ident()
            with db.session() as session:
                final_link = session.get(DshSessionLinkRecord, submit.run_id)
                assert final_link is not None
                assert {
                    column.name: getattr(final_link, column.name)
                    for column in DshSessionLinkRecord.__table__.columns
                } == before_columns
        else:
            assert report["limitations"] == {"recovery_binding_changed": 1}
        assert report["confirmed"] == 0
        assert [name for name, _binding in host.calls] == ["status"]
        final = links.get(submit.run_id)
        assert final is not None and (final.generation, final.state) == (2, "admitted")
    finally:
        release.set()
        continue_projection.set()
        await projector.close()
        db.engine.dispose()


def test_prompt_and_integrity_receipt_rollback_together_and_old_missing_is_not_filled(
    tmp_path: Path,
) -> None:
    db, links, submit = fixture(tmp_path / "prompt-atomic.db")
    prompt = DshSessionPrompt(
        schema_version="dsh-session-prompt.v1", run_id=submit.run_id,
        dsh_session_id=submit.deterministic_session_id,
        request_id=submit.deterministic_request_id, request_hash=submit.request_hash,
        generation=1, prompt="Original prompt",
    )

    def fail_receipt(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO run_role_outputs"):
            raise ValueError("injected_receipt_write_failed")

    event.listen(db.engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(ValueError, match="injected_receipt_write_failed"):
            links.store_prompt(prompt)
        assert links.get_prompt(submit.run_id) is None
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_receipt)
    links.store_prompt(prompt)
    with db.session() as session:
        receipt = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == submit.run_id,
            RunRoleOutputRecord.role == prompt_integrity_role(1),
        ))
        assert receipt is not None
        session.delete(receipt)
    assert links.store_prompt(prompt) == prompt
    with db.session() as session:
        saved = session.get(DshSessionPromptRecord, (submit.run_id, 1))
        assert saved is not None
        with pytest.raises(ValueError, match="prompt_integrity_missing"):
            read_prompt_integrity(session, saved)
    db.engine.dispose()


async def test_legacy_prompt_does_not_gain_missing_execution_proof_on_repeat(
    tmp_path: Path,
) -> None:
    db = database(tmp_path / "legacy-execution.db")
    links, submit = frozen_run(db, execution_receipt=False)
    actual_request = ResearchTaskService(db).get(submit.run_id)
    prompt = links.get_prompt(submit.run_id)
    assert actual_request is not None and prompt is not None
    assert links.store_prompt(prompt, execution_request=actual_request) == prompt
    projector = DshCancellationProjector(links)
    host = RecoveryHost()
    try:
        report = await CancelledRunReconciler(projector, host).tick()
        assert report["limitations"] == {"recovery_execution_request_missing": 1}
        assert host.calls == [] and report["attempted"] == 0
        with db.session() as session:
            assert session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == submit.run_id,
                RunRoleOutputRecord.role == execution_request_role(1),
            )) is None
    finally:
        await projector.close()
        db.engine.dispose()


def test_prompt_execution_request_and_integrity_share_one_real_rollback(tmp_path: Path) -> None:
    db = database(tmp_path / "triple-atomic.db")
    links, submit = frozen_run(db)
    actual_request = ResearchTaskService(db).get(submit.run_id)
    prompt = links.get_prompt(submit.run_id)
    assert actual_request is not None and prompt is not None
    proof_roles = {execution_request_role(1), prompt_integrity_role(1)}
    with db.session() as session:
        saved_prompt = session.get(DshSessionPromptRecord, (submit.run_id, 1))
        assert saved_prompt is not None
        session.delete(saved_prompt)
        for row in session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == submit.run_id,
            RunRoleOutputRecord.role.in_(proof_roles),
        )):
            session.delete(row)

    def fail_after_proof_insert(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO run_role_outputs"):
            # Fail after SQLite executed the actual proof INSERT, then verify
            # rollback covers it and the prompt in that writer transaction.
            raise ValueError("injected_after_execution_proof_write")

    event.listen(db.engine, "after_cursor_execute", fail_after_proof_insert)
    try:
        with pytest.raises(ValueError, match="injected_after_execution_proof_write"):
            links.store_prompt(prompt, execution_request=actual_request)
        with db.session() as session:
            assert session.get(DshSessionPromptRecord, (submit.run_id, 1)) is None
            assert list(session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == submit.run_id,
                RunRoleOutputRecord.role.in_(proof_roles),
            ))) == []
            assert session.query(RunRoleOutputRecord).count() == 2  # original Task and Method
    finally:
        event.remove(db.engine, "after_cursor_execute", fail_after_proof_insert)
        db.engine.dispose()


async def test_real_http_recovery_methods_keep_exact_routes_body_and_never_put(
    tmp_path: Path,
) -> None:
    db = database(tmp_path / "http-binding.db")
    links, _submit = frozen_run(db)
    host = RecoveryHost()
    projector = DshCancellationProjector(links)
    seen: list[tuple[str, str, dict[str, Any] | None]] = []
    bindings: list[DshRecoveryBinding] = []

    def route(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Decision-Hub-Host-Key"] == "test-bridge-key"
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        binding = bindings[-1]
        if request.method == "POST":
            assert body == {"reason": "product_run_cancelled", "generation": binding.generation}
            host.state = "cancelled"
        else:
            assert request.method == "GET"
        return httpx.Response(200, json=host.status_value(binding).model_dump(mode="json"))

    class BoundHttpHost(DshWebHostClient):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            bindings.append(binding)
            return await super().recovery_status(binding)

    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    client = BoundHttpHost(DshWebHostConfig(
        base_url="http://controlled.invalid", bridge_key="test-bridge-key",
    ), client=http)
    try:
        report = await CancelledRunReconciler(projector, client).tick()
        assert report["confirmed"] == 1
        run_id = bindings[-1].run_id
        assert [(method, path) for method, path, _body in seen] == [
            ("GET", f"/decision-hub/v1/runs/{run_id}"),
            ("POST", f"/decision-hub/v1/runs/{run_id}/cancel"),
        ]
    finally:
        await projector.close()
        await http.aclose()
        db.engine.dispose()


async def test_real_worker_graph_filtered_first_round_can_recover_after_owner_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_dsh_web_runtime import FakeHostClient

    from apps.hub_worker.research import DurableResearchWorker
    from packages.contracts_py.decision_hub_contracts import ResearchRunCommand
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        ResearchCommandService,
    )
    from packages.kernel.decision_hub_kernel.application.research_request_identity import (
        research_request_hash,
    )
    from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
    from tests.evolution.test_research_worker import PACK_ROOT, _admitted_run

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    db, _snapshot, run_id = _admitted_run(tmp_path)
    entered = asyncio.Event()

    class HeldGraphHost(FakeHostClient):
        def __init__(self) -> None:
            super().__init__()
            self.recovery = RecoveryHost("cancelled")

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("held Host status")

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            raise AgentExecutionError("dsh_host_unavailable", "controlled unavailable Host")

        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            return await self.recovery.recovery_status(binding)

        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            return await self.recovery.recovery_result(binding)

        async def recovery_cancel(
            self, binding: DshRecoveryBinding, reason: str,
        ) -> DshSessionStatus:
            return await self.recovery.recovery_cancel(binding, reason)

    host = HeldGraphHost()
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host)
    worker = DurableResearchWorker(
        db, runtime, pack_root=PACK_ROOT, worker_id="real-graph-recovery-test",
    )
    task = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task_request = ResearchTaskService(db).get(run_id)
        assert task_request is not None
        link = links.get(run_id)
        assert link is not None
        assert research_request_hash(task_request) != link.request_hash
        result = ResearchCommandService(db).apply(run_id, ResearchRunCommand(
            schema_version="research-run-command.v1", request_id="real-graph-cancel",
            command="cancel", reason="Controlled owner stop",
        ), owner_id="owner-graph")
        assert result.status == "accepted"
        report = await asyncio.wait_for(task, 5)
        assert report is not None and report.status == "cancelled"
        assert links.get(run_id).state == "admitted"  # type: ignore[union-attr]
        recovery = await runtime.reconcile_cancelled_runs()
        assert recovery["confirmed"] == 1, recovery
        assert links.get(run_id).state == "cancelled"  # type: ignore[union-attr]
        assert len(host.submit_calls) == 1
        assert [name for name, _binding in host.recovery.calls] == ["status"]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        db.engine.dispose()
