"""Offline original V2 cancellation recovery, using actual prepared SQLite rows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_execution_preparation_v2 import (
    PreparedInputs,
    _advance_inputs,  # pyright: ignore[reportPrivateUsage]
)
from test_execution_preparation_v2 import (
    inputs as inputs,
)

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionStatus,
    ResearchRunCommand,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
    execution_request_role,
    prompt_integrity_role,
)
from packages.kernel.decision_hub_kernel.application.research_binding import BUNDLE_ROLES
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector
from packages.runtime_adapters.dsh_runtime.cancelled_recovery import (
    CancelledRunReconciler,
    _load_binding,  # pyright: ignore[reportPrivateUsage]
)
from packages.runtime_adapters.dsh_runtime.web_host_client import DshRecoveryBinding
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


class ControlledRecoveryHost:
    """Existing-session transport only; no submit, readiness or Provider exists."""

    def __init__(self, prepared: PreparedInputs, state: str = "running") -> None:
        self.prepared = prepared
        self.state = state
        self.calls: list[tuple[str, DshRecoveryBinding]] = []
        self.started = time.perf_counter()
        self.phase_elapsed: list[tuple[str, float]] = []

    def status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        return DshSessionStatus.model_validate({
            "schema_version": "dsh-session-status.v1", "run_id": binding.run_id,
            "dsh_session_id": binding.dsh_session_id, "generation": binding.generation,
            "state": self.state, "last_seq": 3,
            "observed_at": self.prepared.now + timedelta(seconds=5),
            "error_code": "controlled_host_failure" if self.state == "failed" else None,
        })

    async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        self.calls.append(("status", binding))
        self.phase_elapsed.append(("status", time.perf_counter() - self.started))
        return self.status(binding)

    async def recovery_cancel(
        self, binding: DshRecoveryBinding, reason: str,
    ) -> DshSessionStatus:
        assert reason == "product_run_cancelled"
        self.calls.append(("cancel", binding))
        self.phase_elapsed.append(("cancel", time.perf_counter() - self.started))
        self.state = "cancelled"
        return self.status(binding)

    async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
        self.calls.append(("result", binding))
        self.phase_elapsed.append(("result", time.perf_counter() - self.started))
        response, events = "Already completed original V2 transport", "[]"
        return DshSessionResult(
            schema_version="dsh-session-result.v1", run_id=binding.run_id,
            dsh_session_id=binding.dsh_session_id, generation=binding.generation,
            final_response=response, events_json=events, finish_reason="stop", last_seq=3,
            trace_ref=f"controlled://{binding.dsh_session_id}/trace",
            result_hash=hashlib.sha256((response + "\0" + events).encode()).hexdigest(),
            started_at=self.prepared.now,
            finished_at=self.prepared.now + timedelta(seconds=4),
        )


def accepted_cancel(prepared: PreparedInputs) -> None:
    command = ResearchRunCommand(
        schema_version="research-run-command.v1",
        request_id="cancel-v2-original", command="cancel", reason="Stop original research",
    )
    result = ResearchCommandService(
        prepared.database, clock=lambda: prepared.now + timedelta(seconds=2),
    ).apply(prepared.request.run_id, command, owner_id="controlled-owner")
    assert result.status == "accepted"


def original_material(prepared: PreparedInputs) -> dict[str, Any]:
    with prepared.database.session() as session:
        roles = session.query(RunRoleOutputRecord).filter_by(
            run_id=prepared.request.run_id,
        ).order_by(RunRoleOutputRecord.role).all()
        run = session.get(RunRecord, prepared.request.run_id)
        assert run is not None
        return {
            "roles": [
                (row.output_id, row.role, row.schema_version, row.payload_json,
                 row.payload_hash, str(as_utc(row.created_at))) for row in roles
            ],
            "cost_usd": run.cost_usd,
            "status": run.status,
        }


async def test_real_prepared_v2_cancelled_run_recovers_same_session(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id)
        assert run is not None
        run.cost_usd = .37  # Existing accounted cost must not become local-call cost zero.
    accepted_cancel(inputs)
    before = original_material(inputs)
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    host = ControlledRecoveryHost(inputs)
    try:
        result = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=5)
        final = links.get(inputs.request.run_id)
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": result, "calls": [name for name, _ in host.calls],
            "phase_elapsed": host.phase_elapsed,
            "reconcile_elapsed_seconds": time.perf_counter() - host.started,
            "final_link": final.model_dump(mode="json") if final else None,
            "before": before, "after": original_material(inputs),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        assert result["confirmed"] == 1 and result["limitations"] == {}
        assert [name for name, _ in host.calls] == ["status", "cancel"]
        assert final is not None and final.state == "cancelled"
        assert final.generation == prepared.link.generation == 1
        assert final.request_hash == prepared.method.request_hash
        for _, binding in host.calls:
            assert binding.request_hash == prepared.method.request_hash
            assert binding.task_request_hash == prepared.execution.task_request_hash
            assert binding.execution_request_hash == prepared.execution.request_hash
            assert binding.first_execution_request_hash == (
                prepared.method.definition.policy_binding.first_execution_request_hash
            )
        assert original_material(inputs) == before
    finally:
        await projector.close()


async def test_diagnostic_full_v2_reader_true_completion_after_bounded_wait(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    projector = DshCancellationProjector(DshSessionLinkService(inputs.database))
    done = threading.Event()
    observed: dict[str, Any] = {}

    def read(session: Session) -> DshRecoveryBinding:
        started = time.monotonic()
        try:
            binding = _load_binding(session, inputs.request.run_id)
            observed["valid_binding"] = binding.run_id == inputs.request.run_id
            return binding
        finally:
            observed["elapsed_seconds"] = time.monotonic() - started
            done.set()

    try:
        result = await projector.read(read)  # Actual unchanged 250ms caller wait.
        assert await asyncio.to_thread(done.wait, 10)
        observed["caller_state"] = result.state
        observed["caller_limitation"] = result.limitation
        (tmp_path / "reader-completion.json").write_text(
            json.dumps(observed, indent=2), encoding="utf-8",
        )
        assert observed["valid_binding"] is True
    finally:
        await projector.close()


@pytest.mark.parametrize("state", ["cancelled", "completed", "failed"])
async def test_v2_recovery_preserves_actual_terminal_transport_and_cost(
    inputs: PreparedInputs, state: str, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id)
        assert run is not None
        run.cost_usd = .37
    accepted_cancel(inputs)
    before = original_material(inputs)
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    host = ControlledRecoveryHost(inputs, state)
    try:
        counts = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=8)
        after = links.get(inputs.request.run_id)
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts, "calls": [name for name, _ in host.calls],
            "link": after.model_dump(mode="json") if after else None,
        }, indent=2), encoding="utf-8")
        assert counts["confirmed"] == 1 and counts["limitations"] == {}
        assert [name for name, _ in host.calls] == (
            ["status", "result"] if state == "completed" else ["status"]
        )
        assert after is not None and after.state == state and after.generation == 1
        assert after.request_hash == prepared.method.request_hash
        assert original_material(inputs) == before
        if state == "completed":
            assert after.result_ref == f"dsh-host://runs/{inputs.request.run_id}/result"
            assert after.result_hash == hashlib.sha256(
                b"Already completed original V2 transport\0[]"
            ).hexdigest()
            assert after.trace_ref == f"controlled://{after.dsh_session_id}/trace"
            assert after.terminal_at == inputs.now + timedelta(seconds=4)
        if state == "failed":
            assert after.error_code == "controlled_host_failure"
        assert (await CancelledRunReconciler(projector, host).tick())["scanned"] == 0
    finally:
        await projector.close()


def _damage(prepared: PreparedInputs, damage: str) -> None:
    with prepared.database.session() as session:
        if damage == "prompt":
            row = session.get(DshSessionPromptRecord, (prepared.request.run_id, 1))
            assert row is not None
            row.prompt += " changed original body"
        elif damage == "resigned_first_prompt":
            prompt = session.get(DshSessionPromptRecord, (prepared.request.run_id, 1))
            integrity = session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prepared.request.run_id,
                RunRoleOutputRecord.role == prompt_integrity_role(1),
            ))
            assert prompt is not None and integrity is not None
            prompt.prompt += " changed but structurally valid original body"
            body = json.loads(integrity.payload_json)
            body["prompt_hash"] = hashlib.sha256(prompt.prompt.encode("utf-8")).hexdigest()
            integrity.payload_json = canonical_recipe(body)
            integrity.payload_hash = recipe_hash(body)
        elif damage == "run_not_cancelled":
            run = session.get(RunRecord, prepared.request.run_id)
            assert run is not None
            run.status = "running"
        elif damage == "generation":
            link = session.get(DshSessionLinkRecord, prepared.request.run_id)
            assert link is not None
            link.generation += 1
        elif damage in {"unknown_method", "mixed_execution"}:
            role = METHOD_ROLE if damage == "unknown_method" else execution_request_role(1)
            row = session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prepared.request.run_id,
                RunRoleOutputRecord.role == role,
            ))
            assert row is not None
            row.schema_version = (
                "research-execution-method.v99" if damage == "unknown_method"
                else "dsh-execution-request.v1"
            )
        else:
            row = session.scalar(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prepared.request.run_id,
                RunRoleOutputRecord.role == damage,
            ))
            assert row is not None
            session.delete(row)


@pytest.mark.parametrize("damage", [
    *BUNDLE_ROLES, METHOD_ROLE, execution_request_role(1), prompt_integrity_role(1),
    "prompt", "unknown_method", "mixed_execution",
])
async def test_partial_unknown_mixed_v2_proofs_never_fallback_or_call_host(
    inputs: PreparedInputs, damage: str, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    _damage(inputs, damage)
    before = original_material(inputs)
    links = DshSessionLinkService(inputs.database)
    link_before = links.get(inputs.request.run_id)
    projector = DshCancellationProjector(links)
    host = ControlledRecoveryHost(inputs)
    try:
        counts = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=5)
        (tmp_path / "observation.json").write_text(json.dumps(counts), encoding="utf-8")
        assert host.calls == [] and counts["confirmed"] == 0 and counts["skipped"] == 1
        assert counts["limitations"] and counts["pending"] == 0
        assert original_material(inputs) == before
        assert links.get(inputs.request.run_id) == link_before
    finally:
        await projector.close()


@pytest.mark.parametrize("repair", [False, True])
async def test_v2_latest_generation_recovery_keeps_first_linked_hash_and_ceiling(
    inputs: PreparedInputs, repair: bool, tmp_path: Path,
) -> None:
    later = _advance_inputs(inputs, repair=repair)
    prepared = later.prepare(turn_kind="synthesis_repair" if repair else "research_round")
    accepted_cancel(later)
    before = original_material(later)
    links = DshSessionLinkService(later.database)
    projector = DshCancellationProjector(links)
    host = ControlledRecoveryHost(later)
    try:
        counts = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=8)
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts,
            "current_round": prepared.execution.request.current_round,
            "calls": [name for name, _ in host.calls],
        }), encoding="utf-8")
        assert counts["confirmed"] == 1 and counts["limitations"] == {}
        assert [name for name, _ in host.calls] == ["status", "cancel"]
        assert original_material(later) == before
        for _, binding in host.calls:
            assert binding.generation == 2
            assert binding.request_hash == prepared.method.request_hash
            assert binding.request_hash != prepared.execution.execution_request_hash
            assert binding.execution_request_hash == prepared.execution.request_hash
            assert binding.task_request_hash == prepared.execution.task_request_hash
            assert binding.max_tool_calls == inputs.submit.max_tool_calls
            assert binding.deadline_at == inputs.submit.deadline_at
        assert prepared.execution.request.current_round == (1 if repair else 2)
    finally:
        await projector.close()


@pytest.mark.parametrize("phase,damage", [
    ("status", "prompt"), ("cancel", "prompt"), ("result", "prompt"),
    ("status", "run_not_cancelled"), ("cancel", "generation"),
    ("status", "resigned_first_prompt"),
])
async def test_v2_held_rpc_whole_proof_or_owner_change_stops_followup_and_projection(
    inputs: PreparedInputs, phase: str, damage: str, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    entered, release = asyncio.Event(), asyncio.Event()
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    link_before = links.get(inputs.request.run_id)

    class HeldHost(ControlledRecoveryHost):
        async def held(self) -> None:
            entered.set()
            await release.wait()

        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            if phase == "status":
                await self.held()
            return await super().recovery_status(binding)

        async def recovery_cancel(
            self, binding: DshRecoveryBinding, reason: str,
        ) -> DshSessionStatus:
            if phase == "cancel":
                await self.held()
            return await super().recovery_cancel(binding, reason)

        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            if phase == "result":
                await self.held()
            return await super().recovery_result(binding)

    host = HeldHost(inputs, "completed" if phase == "result" else "running")
    task = asyncio.create_task(CancelledRunReconciler(projector, host).tick(time_budget_seconds=8))
    try:
        await asyncio.wait_for(entered.wait(), 6)
        _damage(inputs, damage)  # Real second DB connection while Host RPC is held.
        link_after_damage = links.get(inputs.request.run_id)
        release.set()
        counts = await task
        after_link = links.get(inputs.request.run_id)
        assert after_link is not None
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts, "calls": [name for name, _ in host.calls],
            "original_link": link_before.model_dump(mode="json") if link_before else None,
            "after_link": after_link.model_dump(mode="json"),
        }), encoding="utf-8")
        assert counts["confirmed"] == 0 and counts["pending"] == 1
        assert counts["limitations"] == {"recovery_binding_changed": 1}
        assert [name for name, _ in host.calls] == {
            "status": ["status"], "cancel": ["status", "cancel"],
            "result": ["status", "result"],
        }[phase]
        assert links.get(inputs.request.run_id) == link_after_damage
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await projector.close()


async def test_v2_final_complete_writer_rechecks_changed_but_valid_whole_prompt(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    assert projector.sessions is not None
    host = ControlledRecoveryHost(inputs, "completed")
    reached: list[str] = []
    link_before = links.get(inputs.request.run_id)

    def drift_at_actual_writer(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _many: bool,
    ) -> None:
        if statement == "BEGIN IMMEDIATE" and not reached:
            _damage(inputs, "resigned_first_prompt")
            reached.append(statement)

    event.listen(projector.sessions.engine, "before_cursor_execute", drift_at_actual_writer)
    try:
        counts = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=8)
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts, "writer_reached": reached,
            "calls": [name for name, _ in host.calls],
            "phase_elapsed": host.phase_elapsed,
        }), encoding="utf-8")
        assert reached == ["BEGIN IMMEDIATE"]
        assert [name for name, _ in host.calls] == ["status", "result"]
        assert counts["confirmed"] == 0 and counts["limitations"] == {"recovery_binding_changed": 1}
        assert links.get(inputs.request.run_id) == link_before
        # The response above was real controlled transport, but no rejected raw
        # archive is invented in this two-source slice. S3b owns that proof.
    finally:
        event.remove(projector.sessions.engine, "before_cursor_execute", drift_at_actual_writer)
        await projector.close()


async def test_v2_default_tick_pending_retains_real_thread_until_native_read_finishes(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    assert projector.sessions is not None
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    host = ControlledRecoveryHost(inputs)
    link_before = links.get(inputs.request.run_id)

    def hold_real_source_read(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _many: bool,
    ) -> None:
        if "FROM studies" in statement and not entered.is_set():
            entered.set()
            assert release.wait(8)

    # Observe real futures instead of treating elapsed async wait as a killed
    # DB thread. The Kernel validator and projector operation stay unmocked.
    futures: list[Any] = []
    original_submit = projector._executor.submit  # pyright: ignore[reportPrivateUsage]

    def observe_submit(*args: Any, **kwargs: Any) -> Any:
        future = original_submit(*args, **kwargs)
        futures.append(future)
        future.add_done_callback(lambda _future: finished.set() if entered.is_set() else None)
        return future

    projector._executor.submit = observe_submit  # pyright: ignore[reportPrivateUsage]
    event.listen(projector.sessions.engine, "after_cursor_execute", hold_real_source_read)
    try:
        counts = await CancelledRunReconciler(projector, host).tick()  # Original default 2s.
        assert entered.is_set() and counts["pending"] == 1
        assert counts["confirmed"] == 0 and counts["limitations"] == {
            "cancellation_projection_wait_elapsed": 1,
        }
        assert host.calls == [] and not futures[-1].done()
        release.set()
        # These are the original futures. Wait for actual completion, never
        # release a slot merely because the tick returned pending.
        resolved = await asyncio.wait_for(asyncio.wrap_future(futures[-1]), 8)
        assert resolved.state == "ready" and resolved.value.run_id == inputs.request.run_id
        assert finished.is_set() and links.get(inputs.request.run_id) == link_before
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts, "caller_default_seconds": 2,
            "real_thread_final_state": resolved.state, "host_calls": len(host.calls),
            "real_future_done": futures[-1].done(),
        }), encoding="utf-8")
    finally:
        release.set()
        event.remove(projector.sessions.engine, "after_cursor_execute", hold_real_source_read)
        await projector.close()


@pytest.mark.parametrize("invalid", ["status_identity", "result_hash", "result_sequence"])
async def test_v2_foreign_or_corrupt_transport_does_not_replace_original_link(
    inputs: PreparedInputs, invalid: str, tmp_path: Path,
) -> None:
    inputs.prepare()
    accepted_cancel(inputs)
    links = DshSessionLinkService(inputs.database)
    projector = DshCancellationProjector(links)
    before = links.get(inputs.request.run_id)

    class InvalidHost(ControlledRecoveryHost):
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            status = await super().recovery_status(binding)
            return status.model_copy(update={"run_id": "foreign-run"}) if (
                invalid == "status_identity"
            ) else status

        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            result = await super().recovery_result(binding)
            return result.model_copy(update={
                "result_hash": "f" * 64,
            } if invalid == "result_hash" else {"last_seq": 2})

    host = InvalidHost(inputs, "completed")
    try:
        counts = await CancelledRunReconciler(projector, host).tick(time_budget_seconds=8)
        (tmp_path / "observation.json").write_text(json.dumps({
            "counts": counts, "calls": [name for name, _ in host.calls],
            "phase_elapsed": host.phase_elapsed,
        }), encoding="utf-8")
        assert counts["confirmed"] == 0 and counts["pending"] == 1
        assert counts["limitations"] == {{
            "status_identity": "recovery_status_identity_mismatch",
            "result_hash": "recovery_result_identity_or_hash_mismatch",
            "result_sequence": "recovery_result_sequence_regression",
        }[invalid]: 1}
        assert links.get(inputs.request.run_id) == before
    finally:
        await projector.close()
