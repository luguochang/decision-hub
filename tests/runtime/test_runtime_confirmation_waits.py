"""Purpose budgets and real single-Future SQL confirmation, never source proof.

Controlled preparation returns in this file test Runtime waiting only. The
genuine required Graph joint and existing late-commit Worker tests authenticate
the actual Kernel preparation and prove that unconfirmed work sends no RPC.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservation,
    ResearchNativeObservationContext,
    ResearchSynthesisSelectionWitness,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime as runtime_module
from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    RuntimePreparedTurn,
    RuntimeTaskAnchor,
)
from packages.runtime_adapters.dsh_runtime.web_host_client import DshHostClient
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.runtime.test_dsh_web_runtime_recovery import (
    _readiness,  # pyright: ignore[reportPrivateUsage] # Public protocol test input, no proof.
    _result,  # pyright: ignore[reportPrivateUsage]
    _submit,  # pyright: ignore[reportPrivateUsage]
)


class _Base(DeclarativeBase):
    pass


class _Confirmation(_Base):
    __tablename__ = "controlled_confirmation"
    identity: Mapped[int] = mapped_column(primary_key=True)


class _NoHost:
    calls: int = 0

    async def close(self) -> None:
        pass


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncGenerator[DshWebResearchRuntime]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'confirmation.sqlite3'}")
    _Base.metadata.create_all(database.engine)
    value = DshWebResearchRuntime(
        DshSessionLinkService(database),
        cast(DshHostClient, _NoHost()),
        poll_interval_seconds=0.001,
    )
    try:
        yield value
    finally:
        await value.close()
        database.engine.dispose()


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("proof_read_wait_seconds", 0),
        ("proof_read_wait_seconds", 15.1),
        ("preparation_confirmation_wait_seconds", 0),
        ("preparation_confirmation_wait_seconds", 30.1),
        ("mapping_confirmation_wait_seconds", 0),
        ("mapping_confirmation_wait_seconds", 30.1),
    ],
)
def test_purpose_budgets_have_hard_bounds(tmp_path: Path, keyword: str, value: float) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'bounds.sqlite3'}")
    try:
        with pytest.raises(ValueError, match="dsh_web_.*_wait_invalid"):
            DshWebResearchRuntime(
                DshSessionLinkService(database),
                cast(DshHostClient, _NoHost()),
                **{keyword: value},
            )
    finally:
        database.engine.dispose()


def _anchor(deadline: datetime) -> RuntimeTaskAnchor:
    return RuntimeTaskAnchor(
        run_id="run-web-recovery",
        event_id="controlled-event",
        snapshot_id=None,
        task_request_hash="1" * 64,
        bundle_hash="2" * 64,
        execution_fingerprint="3" * 64,
        generation=1,
        deadline_at=deadline,
        requires_selection=False,
    )


@pytest.mark.asyncio
async def test_complete_proof_budget_caps_operation_and_keeps_cancel_budget(
    runtime: DshWebResearchRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime._task_anchor.set(_anchor(datetime.now(UTC) + timedelta(seconds=20)))  # pyright: ignore[reportPrivateUsage]
    runtime._binding_deadline.set(datetime.now(UTC) + timedelta(seconds=20))  # pyright: ignore[reportPrivateUsage]
    operation = datetime.now(UTC) + timedelta(seconds=0.15)
    runtime._proof_operation_deadline.set(operation)  # pyright: ignore[reportPrivateUsage]
    budgets: list[float] = []

    async def pending(*_args: Any, timeout_seconds: float = 0.25) -> ProjectionRead[Any]:
        budgets.append(timeout_seconds)
        return ProjectionRead("pending", limitation="controlled_unconfirmed")

    monkeypatch.setattr(runtime.cancellation_projector, "read", pending)
    with pytest.raises(AgentExecutionError, match="Original research Task"):
        await runtime._assert_task_binding()  # pyright: ignore[reportPrivateUsage]
    assert 0 < budgets[-1] <= 0.15
    with pytest.raises(AgentExecutionError):
        await runtime._assert_task_binding(allow_cancelled=True)  # pyright: ignore[reportPrivateUsage]
    assert budgets[-1] == runtime.binding_wait_seconds == 5
    assert runtime.preparation_confirmation_wait_seconds == 30
    assert runtime.mapping_confirmation_wait_seconds == 30
    assert runtime.proof_read_wait_seconds == 15
    assert runtime.readiness_grace_seconds == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["ready", "pending", "cancelled", "task_deadline", "operation_deadline"]
)
async def test_preparation_awaits_one_real_future_without_rpc_or_replay(
    runtime: DshWebResearchRuntime,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    # Only the wait boundary reads run_id/deadline_at. This explicit controlled
    # input cannot authenticate a Task; the genuine joint covers that contract.
    request = cast(
        ResearchSessionRequest,
        SimpleNamespace(
            run_id="run-web-recovery",
            deadline_at=datetime.now(UTC) + timedelta(seconds=2),
            output_schema_ref="controlled.wait-boundary.only",
        ),
    )
    original_deadline = (
        datetime.now(UTC) + timedelta(seconds=0.15)
        if outcome == "task_deadline"
        else request.deadline_at
    )
    original_anchor = _anchor(original_deadline)
    runtime._task_anchor.set(original_anchor)  # pyright: ignore[reportPrivateUsage]
    runtime.binding_wait_seconds = 0.01
    runtime.preparation_confirmation_wait_seconds = 0.15 if outcome == "pending" else 0.8
    if outcome == "operation_deadline":
        runtime._proof_operation_deadline.set(datetime.now(UTC) + timedelta(seconds=0.15))  # pyright: ignore[reportPrivateUsage]
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls: list[int] = []

    def controlled(database: Database, *_args: Any, **_kwargs: Any) -> RuntimePreparedTurn:
        calls.append(1)
        entered.set()
        try:
            with database.session() as session:
                assert release.wait(2)
                session.add(_Confirmation(identity=1))
                session.flush()
                return RuntimePreparedTurn(
                    original_anchor,
                    cast(FrozenExecutionPreparation, object()),
                    request,
                    _submit(deadline_at=request.deadline_at),
                    0,
                    cast(Any, None),
                )
        finally:
            finished.set()

    monkeypatch.setattr(runtime_module, "prepare_runtime_turn", controlled)
    # This test covers the actual writer/Future lifetime, not method/profile
    # authentication. Its private database contains only the controlled row.
    monkeypatch.setattr(runtime, "_profile_for", lambda _request: "controlled-wait-only")
    pending = asyncio.create_task(
        runtime._prepare_v2_turn(  # pyright: ignore[reportPrivateUsage]
            request,
            _readiness(True),
            "controlled-wait-only",
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.02)
        assert not pending.done()  # The old .01 wait passed; same Future is still awaited.
        assert calls == [1]
        assert cast(_NoHost, runtime.client).calls == 0
        if outcome in {"ready", "pending"}:
            if outcome == "pending":
                await asyncio.sleep(runtime.preparation_confirmation_wait_seconds + 0.03)
                assert not pending.done() and not finished.is_set()
                assert calls == [1]  # The same accepted writer is still running.
            release.set()
            assert (await pending).anchor == original_anchor
        elif outcome == "cancelled":

            async def cancel_and_observe_exit() -> None:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending

            cancellation = asyncio.create_task(cancel_and_observe_exit())
            await asyncio.sleep(0.03)
            assert not pending.done() and not cancellation.done()
            assert not finished.is_set()
            release.set()
            await cancellation
        else:
            # Deadline withdraws execution permission, but the caller must
            # drain the already accepted native writer before returning.
            await asyncio.sleep(0.18)
            assert not pending.done() and not finished.is_set()
            release.set()
            with pytest.raises(AgentExecutionError) as error:
                await pending
            assert error.value.cause_code == "dsh_web_preparation_deadline_elapsed"
            assert runtime._task_anchor.get() == original_anchor  # pyright: ignore[reportPrivateUsage]
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        # BEGIN IMMEDIATE waits for the actual original commit; no callback replay.
        with runtime.links.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            assert session.scalars(select(_Confirmation.identity)).all() == [1]
        assert calls == [1]
        assert cast(_NoHost, runtime.client).calls == 0
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_rpc_proof_operation_scope_resets_on_cancellation(
    runtime: DshWebResearchRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    captured: list[datetime | None] = []

    async def interrupted(*, allow_cancelled: bool = False) -> None:
        assert not allow_cancelled
        captured.append(runtime._proof_operation_deadline.get())  # pyright: ignore[reportPrivateUsage]
        raise asyncio.CancelledError

    async def forbidden_rpc() -> None:
        raise AssertionError("unconfirmed proof must not call Host")

    monkeypatch.setattr(runtime, "_assert_task_binding", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await runtime._retry_host_call(  # pyright: ignore[reportPrivateUsage]
            forbidden_rpc,
            deadline_at=deadline,
            operation="controlled",
        )
    assert captured == [deadline]
    assert runtime._proof_operation_deadline.get() is None  # pyright: ignore[reportPrivateUsage]


def _controlled_mapping(runtime: DshWebResearchRuntime) -> ResearchSessionResult:
    """Typed wait-only material, never original Task/Graph/source evidence."""
    raw = _result()
    now = datetime.now(UTC)
    coverage = {
        "status": "insufficient", "covered_requirement_ids": [], "gaps": [],
        "conflicts": [], "hard_coverage_ratio": 0.0, "soft_coverage_ratio": 0.0,
        "assessed_at": now,
    }
    result = ResearchSessionResult.model_validate({
        "schema_version": "research-session-result.v1", "request_id": "controlled-wait",
        "research_session_id": raw.dsh_session_id, "runtime_id": "dsh",
        "runtime_version": "controlled-wait-only", "profile_ref": "controlled-wait-only",
        "trace_ref": "controlled-wait-only", "trace_hash": "1" * 64, "status": "degraded",
        "rounds": [{
            "round": 1, "plan": {
                "plan_id": "controlled-wait-only", "objective": "wait boundary only",
                "required_capabilities": ["controlled"], "created_at": now,
                "tasks": [{
                    "task_id": "controlled", "requirement_id": "controlled",
                    "capability_id": "controlled", "objective": "wait boundary only",
                    "question": "wait boundary only", "input_evidence_refs": [],
                    "output_schema_ref": "controlled", "depends_on": [],
                    "success_condition": "not an authentication verdict", "priority": 1,
                }],
            },
            "tool_invocations": [], "tool_results": [], "new_evidence_refs": [],
            "coverage": coverage, "started_at": now, "finished_at": now,
        }],
        "evidence_candidates": [], "final_coverage": coverage, "causal_case": None,
        "horizons": [], "stop_reason": {
            "code": "critical_data_unavailable", "detail": "controlled wait boundary only",
            "bounded": True, "remaining_hard_gaps": [],
        },
        "total_tool_calls": 0, "total_subagents": 0, "total_tokens": None,
        "estimated_cost_usd": None, "started_at": now, "finished_at": now,
    })
    reference = ObservedExecutionRef(
        output_id="controlled-raw", role="controlled-raw",
        schema_version="research-transport-observation.v1",
        payload_hash="1" * 64, recorded_at=now,
    )
    context = ResearchNativeObservationContext(
        run_id=raw.run_id, event_id="controlled", snapshot_id=None,
        request_id=result.request_id, dsh_session_id=raw.dsh_session_id, generation=1,
        original_task_request_hash="1" * 64, bundle_hash="2" * 64, method_hash="3" * 64,
        execution_request_hash="4" * 64, first_linked_request_hash="5" * 64,
        prompt_hash="6" * 64, rows=(),
    )
    selection = ResearchSynthesisSelectionWitness(
        kind="evidence_only", candidate_payload_hash=None, candidate=None,
        final_response_hash=None, call_id=None, call_event_index=None,
        result_event_index=None, payload_occurrence=None, payload_path=None,
        call_event_hash=None, result_event_hash=None, call_seq=None, result_seq=None,
    )
    observations = runtime_module._RuntimeObservations(  # pyright: ignore[reportPrivateUsage]
        context=context, latest=reference, latest_generation=1, mapping_payload=raw,
        selection=selection,
        result_refs={runtime_module.observation_sha(raw.model_dump(mode="json")): reference},
    )
    runtime._observations.set(observations)  # pyright: ignore[reportPrivateUsage]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["ready", "pending", "task_deadline", "request_deadline", "operation_deadline"]
)
async def test_mapped_confirmation_one_future_deadline_caps_and_no_extra_rpc(
    runtime: DshWebResearchRuntime, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    result = _controlled_mapping(runtime)
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=2)
    runtime._task_anchor.set(  # pyright: ignore[reportPrivateUsage]
        _anchor(now + timedelta(seconds=0.15) if outcome == "task_deadline" else deadline)
    )
    runtime._binding_deadline.set(  # pyright: ignore[reportPrivateUsage]
        now + timedelta(seconds=0.15) if outcome == "request_deadline" else deadline
    )
    if outcome == "operation_deadline":
        runtime._proof_operation_deadline.set(  # pyright: ignore[reportPrivateUsage]
            now + timedelta(seconds=0.15)
        )
    runtime.binding_wait_seconds = 0.01
    runtime.mapping_confirmation_wait_seconds = 0.15 if outcome == "pending" else 0.8
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls: list[int] = []

    def controlled(
        session: Session, value: ResearchMappedObservation, **_kwargs: Any,
    ) -> ObservedExecutionRef:
        calls.append(1)
        entered.set()
        try:
            assert release.wait(2)
            assert value.result == result
            session.add(_Confirmation(identity=1))
            session.flush()
            return ObservedExecutionRef(
                output_id="controlled-mapped", role="controlled-mapped",
                schema_version=value.schema_version, payload_hash="1" * 64,
                recorded_at=value.recorded_at,
            )
        finally:
            finished.set()

    # This callback verifies waiting/commit only. Genuine joint15 runs the
    # unchanged actual original reader and mapper; no source pass is mocked.
    monkeypatch.setattr(runtime_module, "store_mapped_observation", controlled)
    pending = asyncio.create_task(runtime._mapped_observation(result))  # pyright: ignore[reportPrivateUsage]
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.02)
        assert not pending.done() and calls == [1]
        assert cast(_NoHost, runtime.client).calls == 0
        if outcome == "ready":
            release.set()
            observed = await pending
            assert observed.observation_ref.output_id == "controlled-mapped"
        else:
            with pytest.raises(AgentExecutionError) as error:
                await pending
            assert error.value.cause_code == "cancellation_projection_wait_elapsed"
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        with runtime.links.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            assert session.scalars(select(_Confirmation.identity)).all() == [1]
        assert calls == [1] and cast(_NoHost, runtime.client).calls == 0
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
