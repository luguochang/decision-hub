"""Actual owned mapping thread/SQLite commit; no API, Provider or global DB."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionResult
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchNativeObservationContext,
    ResearchSynthesisSelectionWitness,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime as web
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector


class CountingProjector(DshCancellationProjector):
    def __init__(self, links):
        super().__init__(links)
        self.write_calls = 0
        self.committed = threading.Event()

    async def write(self, operation, **kwargs):
        self.write_calls += 1
        original = kwargs.get("stage_observer")

        def stage(identity, name, code):
            if name == "commit_done":
                self.committed.set()
            if original is not None:
                original(identity, name, code)

        return await super().write(operation, **{**kwargs, "stage_observer": stage})


def typed_result(at):
    coverage = {
        "status": "insufficient",
        "covered_requirement_ids": [],
        "gaps": [],
        "conflicts": [],
        "hard_coverage_ratio": 0.0,
        "soft_coverage_ratio": 0.0,
        "assessed_at": at,
    }
    return ResearchSessionResult.model_validate(
        {
            "schema_version": "research-session-result.v1",
            "request_id": "request-mapping",
            "research_session_id": "session-mapping",
            "runtime_id": "controlled-native",
            "runtime_version": "test.v1",
            "profile_ref": "test.v1",
            "trace_ref": "test://trace",
            "trace_hash": "a" * 64,
            "status": "degraded",
            "rounds": [
                {
                    "round": 1,
                    "plan": {
                        "plan_id": "plan-mapping",
                        "objective": "Check originals",
                        "tasks": [
                            {
                                "task_id": "task-mapping",
                                "requirement_id": "event_identity",
                                "capability_id": "replay.research",
                                "objective": "Check originals",
                                "question": "Which original is available?",
                                "input_evidence_refs": [],
                                "output_schema_ref": "evidence-candidate.v1",
                                "depends_on": [],
                                "success_condition": "Known original",
                                "priority": 1,
                            }
                        ],
                        "required_capabilities": ["replay.research"],
                        "created_at": at,
                    },
                    "tool_invocations": [],
                    "tool_results": [],
                    "new_evidence_refs": [],
                    "coverage": coverage,
                    "started_at": at,
                    "finished_at": at,
                }
            ],
            "evidence_candidates": [],
            "facts": [],
            "final_coverage": coverage,
            "causal_case": None,
            "horizons": [],
            "stop_reason": {
                "code": "critical_data_unavailable",
                "detail": "Controlled lifecycle only",
                "bounded": True,
                "remaining_hard_gaps": [],
            },
            "total_tool_calls": 0,
            "total_subagents": 0,
            "total_tokens": None,
            "estimated_cost_usd": None,
            "started_at": at,
            "finished_at": at,
        }
    )


def setup_mapping(tmp_path, monkeypatch, *, hold=False, invalid=False, short_deadline=False):
    path = tmp_path / "mapping.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE mapping_receipts (id INTEGER PRIMARY KEY, body_hash TEXT)"
        )
    links = SimpleNamespace(
        database=SimpleNamespace(engine=engine), clock=lambda: datetime.now(UTC)
    )
    projector = CountingProjector(links)
    at = datetime.now(UTC)
    deadline = at + timedelta(seconds=0.09 if short_deadline else 3)
    anchor = web.RuntimeTaskAnchor(
        "run-mapping", "event-mapping", None, "a" * 64, bundle_hash="b" * 64, deadline_at=deadline
    )
    reference = ObservedExecutionRef(
        output_id="transport-test",
        role="research_transport:test",
        schema_version="research-transport-observation.v1",
        payload_hash="c" * 64,
        recorded_at=at,
    )
    context = ResearchNativeObservationContext(
        run_id="run-mapping",
        event_id="event-mapping",
        snapshot_id=None,
        request_id="request-mapping",
        dsh_session_id="session-mapping",
        generation=1,
        original_task_request_hash="a" * 64,
        bundle_hash="b" * 64,
        method_hash="c" * 64,
        execution_request_hash="d" * 64,
        first_linked_request_hash="e" * 64,
        prompt_hash="f" * 64,
        rows=(),
    )
    payload = DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id="run-mapping",
        dsh_session_id="session-mapping",
        generation=1,
        last_seq=2,
        final_response="{}",
        finish_reason="stop",
        events_json="[]",
        started_at=at,
        finished_at=at,
        trace_ref="test://trace",
        result_hash=hashlib.sha256(b"{}\0[]").hexdigest(),
    )
    selection = ResearchSynthesisSelectionWitness(
        kind="evidence_only",
        candidate_payload_hash=None,
        candidate=None,
        final_response_hash=None,
        call_id=None,
        call_event_index=None,
        result_event_index=None,
        payload_occurrence=None,
        payload_path=None,
        call_event_hash=None,
        result_event_hash=None,
        call_seq=None,
        result_seq=None,
    )
    observations = web._RuntimeObservations(
        context=context,
        latest=reference,
        latest_generation=1,
        mapping_payload=payload,
        selection=selection,
        result_refs={web.observation_sha(payload.model_dump(mode="json")): reference},
    )
    instance = web.DshWebResearchRuntime.__new__(web.DshWebResearchRuntime)
    instance.cancellation_projector = projector
    instance.mapping_confirmation_wait_seconds = 0.02
    instance._observations = ContextVar("mapping-observations", default=observations)
    instance._task_anchor = ContextVar("mapping-anchor", default=anchor)
    instance._binding_deadline = ContextVar("mapping-binding", default=deadline)
    instance._proof_operation_deadline = ContextVar("mapping-proof", default=None)
    entered, release = threading.Event(), threading.Event()
    calls, proof_calls = [], []

    def verify(session, actual):
        assert actual is anchor and session.in_transaction()
        proof_calls.append(True)

    def store(session, value, *, binding_validator):
        calls.append(threading.get_ident())
        assert value.context == context and value.mapping_input_ref == reference
        binding_validator(session)
        session.execute(
            text("INSERT INTO mapping_receipts VALUES (1, :hash)"),
            {"hash": value.result_content_hash},
        )
        entered.set()
        if hold:
            assert release.wait(2), "Finite test release did not arrive"
        else:
            time.sleep(0.11)  # Exceeds the first async observation, in the real writer.
        if invalid:
            raise ValueError("research_runtime_original_task_changed")
        return ObservedExecutionRef(
            output_id="mapping-test",
            role="research_observed:test",
            schema_version="research-mapped-observation.v1",
            payload_hash="d" * 64,
            recorded_at=datetime.now(UTC),
        )

    monkeypatch.setattr(web, "verify_runtime_task_anchor", verify)
    monkeypatch.setattr(web, "store_mapped_observation", store)
    return SimpleNamespace(
        instance=instance,
        projector=projector,
        engine=engine,
        result=typed_result(at),
        entered=entered,
        release=release,
        calls=calls,
        proofs=proof_calls,
        deadline=deadline,
    )


def row_count(state):
    with state.engine.connect() as connection:
        return connection.exec_driver_sql("SELECT count(*) FROM mapping_receipts").scalar_one()


async def close(state):
    state.release.set()
    state.projector._executor.shutdown(wait=True)
    await state.projector.close()
    state.engine.dispose()


@pytest.mark.asyncio
async def test_mapping_waits_for_real_commit_after_first_observation_without_replaying(
    tmp_path, monkeypatch
):
    state = setup_mapping(tmp_path, monkeypatch)
    try:
        observed = await state.instance._mapped_observation(state.result)
        assert (
            observed.result == state.result and observed.observation_ref.output_id == "mapping-test"
        )
        assert state.projector.committed.is_set() and row_count(state) == 1
        assert state.projector.write_calls == len(state.calls) == len(state.proofs) == 1
        assert state.calls[0] != threading.get_ident()
    finally:
        await close(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_observation", [False, True])
async def test_mapping_repeated_cancel_drains_same_real_writer(
    tmp_path, monkeypatch, after_observation
):
    state = setup_mapping(tmp_path, monkeypatch, hold=True)
    task = asyncio.create_task(state.instance._mapped_observation(state.result))
    try:
        assert await asyncio.to_thread(state.entered.wait, 1)
        if after_observation:
            await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        assert not task.done() and not state.projector.committed.is_set()
        state.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.projector.committed.is_set() and row_count(state) == 1
        assert state.projector.write_calls == len(state.calls) == 1
    finally:
        state.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(state)


@pytest.mark.asyncio
async def test_mapping_deadline_drains_but_never_admits_a_late_commit(tmp_path, monkeypatch):
    state = setup_mapping(tmp_path, monkeypatch, hold=True, short_deadline=True)
    task = asyncio.create_task(state.instance._mapped_observation(state.result))
    try:
        assert await asyncio.to_thread(state.entered.wait, 1)
        await asyncio.sleep(0.13)
        assert datetime.now(UTC) >= state.deadline
        assert not task.done() and not state.projector.committed.is_set()
        state.release.set()
        with pytest.raises(AgentExecutionError) as caught:
            await task
        assert caught.value.error_code == "provider_timeout"
        assert caught.value.cause_code == "dsh_web_mapping_deadline_elapsed"
        assert state.projector.committed.is_set() and row_count(state) == 1
        assert state.projector.write_calls == len(state.calls) == 1
    finally:
        state.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(state)


@pytest.mark.asyncio
async def test_mapping_actual_invalid_rolls_back_without_replaying(tmp_path, monkeypatch):
    state = setup_mapping(tmp_path, monkeypatch, invalid=True)
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await state.instance._mapped_observation(state.result)
        assert caught.value.cause_code == "research_runtime_original_task_changed"
        assert not caught.value.retryable
        assert not state.projector.committed.is_set() and row_count(state) == 0
        assert state.projector.write_calls == len(state.calls) == 1
    finally:
        await close(state)
