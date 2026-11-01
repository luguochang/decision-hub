"""Offline, actual Factory/SQLite/controlled Host observation boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionSubmit,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import TASK_ROLE
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs, inputs
from tests.dsh_native.test_runtime_execution_v2 import (
    PreparedHost,
    _sql_repairable_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


def _roles(fixture: PreparedInputs, prefix: str) -> list[dict[str, Any]]:
    with fixture.database.session() as session:
        rows = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == fixture.request.run_id,
                RunRoleOutputRecord.role.like(prefix + "%"),
            )
        ).all()
        return [
            {
                "id": r.output_id,
                "role": r.role,
                "hash": r.payload_hash,
                "body": json.loads(r.payload_json),
            }
            for r in rows
        ]


def _observe(path: Path, **body: Any) -> None:
    (path / "observation.json").write_text(
        json.dumps(body, default=str, indent=2), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_lawful_v2_runtime_has_durable_actual_observation_outlet(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        result = await runtime.execute(inputs.request)
        raw, mapped = _roles(inputs, "research_transport:"), _roles(inputs, "research_observed:")
        _observe(
            tmp_path,
            result_status=result.status,
            submits=len(host.submit_calls),
            actual_results=host.result_calls,
            raw=raw,
            mapped=mapped,
        )
        assert result.status == "degraded" and host.result_calls == 1
        assert len(raw) >= 1, "actual Host response has no immutable transport receipt"
        assert len(mapped) == 1, "lawful mapped result has no exact observation locator"
        assert callable(getattr(runtime, "execute_observed", None))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_returned_raw_survives_original_task_drift_without_mapped_admission(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            result = await super().result(payload)
            with inputs.database.session() as session:
                row = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == inputs.request.run_id,
                        RunRoleOutputRecord.role == TASK_ROLE,
                    )
                )
                assert row is not None
                body = json.loads(row.payload_json)
                body["request"]["evidence_requirements"][-1]["description"] += " changed"
                row.payload_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
                row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
            return result

    host = Host(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await runtime.execute(inputs.request)
        link = runtime.links.get(inputs.request.run_id)
        raw, mapped = _roles(inputs, "research_transport:"), _roles(inputs, "research_observed:")
        _observe(
            tmp_path,
            error=caught.value.cause_code,
            actual_results=host.result_calls,
            link=link.model_dump(mode="json") if link else None,
            raw=raw,
            mapped=mapped,
        )
        assert host.result_calls == 1 and link is not None and link.result_hash
        assert mapped == []
        assert any(item["body"]["phase"] == "result" for item in raw), (
            "real returned transcript was lost at the subsequent native binding rejection"
        )
    finally:
        await runtime.close()


def _result_with_events(result: DshSessionResult, events: list[dict[str, Any]]) -> DshSessionResult:
    text = json.dumps(events, ensure_ascii=False, indent=1)
    return result.model_copy(
        update={
            "events_json": text,
            "result_hash": hashlib.sha256(
                (result.final_response + "\0" + text).encode()
            ).hexdigest(),
        }
    )


def _capture_events(session_id: str, request_id: str) -> list[dict[str, Any]]:
    def event(kind: str, body: dict[str, Any], *, marker: str):
        return {
            "method": "session.event",
            "original_wrapper": marker,
            "payload": {"sessionId": session_id, "event": {"type": kind, "data": body}},
        }

    first = {
        "schema_version": "research-synthesis-candidate.v1",
        "request_id": "not-selected",
        "causal_case": None,
        "horizons": [],
    }
    chosen = {**first, "request_id": request_id}
    return [
        {"method": "ignored.original", "payload": {"preserved": "🧪"}},
        event("tool/result", {"toolCallId": "unmatched", "result": chosen}, marker="unmatched"),
        event(
            "tool/call",
            {"toolCallId": "first", "name": "decision_hub_synthesis_submit"},
            marker="first-call",
        ),
        event("tool/result", {"toolCallId": "first", "result": first}, marker="first-result"),
        event(
            "tool/call",
            {"toolCallId": "selected", "name": "decision_hub_synthesis_submit"},
            marker="selected-call",
        ),
        event(
            "tool/result",
            {"toolCallId": "selected", "result": {"repeated": [chosen, json.dumps(chosen)]}},
            marker="selected-result",
        ),
    ]


@pytest.mark.asyncio
async def test_actual_selected_pair_indices_payload_path_and_original_raw_bytes(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        observation_sha,
        read_mapped_observation,
        read_transport_observation,
        verify_selection_witness,
    )
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        extract_attested_synthesis_candidates,
        extract_attested_synthesis_captures,
    )
    from packages.runtime_adapters.dsh_runtime.web_runtime import (
        _sdk_run,  # pyright: ignore[reportPrivateUsage]
    )

    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit):
            original = await super().result(payload)
            return _result_with_events(
                original,
                _capture_events(payload.deterministic_session_id, inputs.request.request_id),
            )

    host = Host(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        observed = await runtime.execute_observed(inputs.request)
        with inputs.database.session() as session:
            mapped = read_mapped_observation(session, observed.observation_ref)
            raw = read_transport_observation(session, mapped.mapping_input_ref)
        assert isinstance(raw.reply, DshSessionResult)
        payload = raw.reply
        sdk = _sdk_run(payload)
        captures = extract_attested_synthesis_captures(
            sdk.notifications, expected_session_id=payload.dsh_session_id
        )
        candidates = extract_attested_synthesis_candidates(
            sdk.notifications, expected_session_id=payload.dsh_session_id
        )
        assert candidates == [item.candidate for item in captures]
        assert len(candidates) == 3 and captures[-1].payload_occurrence == 1
        assert len(sdk.notifications) == len(json.loads(payload.events_json)) == 6
        selection = mapped.selection
        assert (selection.call_id, selection.call_event_index, selection.result_event_index) == (
            "selected",
            4,
            5,
        )
        assert selection.call_seq is None and selection.result_seq is None
        assert selection.origin_generation is None and selection.presence_generation is None
        assert selection.call_event_hash == observation_sha(json.loads(payload.events_json)[4])
        assert raw.events_json_hash == hashlib.sha256(payload.events_json.encode()).hexdigest()
        assert mapped.mapping_input_generation == mapped.latest_transport_generation == 1
        assert mapped.result == observed.result and observed.result.total_tokens is None
        assert observed.result.estimated_cost_usd is None
        assert raw.total_tokens is None and raw.estimated_cost_usd is None
        verify_selection_witness(payload, selection)
        changed = ResearchSynthesisCandidate.model_validate(
            {
                "schema_version": "research-synthesis-candidate.v1",
                "request_id": "forged",
                "causal_case": None,
                "horizons": [],
            }
        )
        with pytest.raises(ValueError, match="event_witness_invalid"):
            verify_selection_witness(
                payload,
                selection.model_copy(
                    update={
                        "candidate": changed,
                        "candidate_payload_hash": observation_sha(changed.model_dump(mode="json")),
                    }
                ),
            )
        _observe(
            tmp_path,
            ref=observed.observation_ref.model_dump(mode="json"),
            mapped=mapped.model_dump(mode="json"),
            raw=raw.model_dump(mode="json"),
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["foreign", "hash"])
async def test_invalid_typed_physical_reply_is_unverified_raw_and_never_mapped(
    inputs: PreparedInputs,
    tmp_path: Path,
    damage: str,
):
    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit):
            original = await super().result(payload)
            return original.model_copy(
                update=(
                    {"run_id": "foreign-actual-run"}
                    if damage == "foreign"
                    else {"result_hash": "a" * 64}
                )
            )

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), Host(inputs), poll_interval_seconds=0.001
    )
    try:
        with pytest.raises(AgentExecutionError):
            await runtime.execute_observed(inputs.request)
        records = _roles(inputs, "research_transport:")
        returned = [item["body"] for item in records if item["body"]["phase"] == "result"]
        assert len(returned) == 1 and returned[0]["trust_level"] == "request_correlated_unverified"
        assert returned[0]["reply"]["run_id"] == (
            "foreign-actual-run" if damage == "foreign" else inputs.request.run_id
        )
        assert _roles(inputs, "research_observed:") == []
        link = runtime.links.get(inputs.request.run_id)
        assert link is not None and link.result_hash is None
        _observe(tmp_path, damage=damage, raw=records, original_link=link.model_dump(mode="json"))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_same_round_retry_returns_exact_observation_without_recharging_or_rewriting(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        read_mapped_observation,
    )

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), PreparedHost(inputs), poll_interval_seconds=0.001
    )
    try:
        first = await runtime.execute_observed(inputs.request)
        original = _roles(inputs, "research_observed:")
        second = await runtime.execute_observed(inputs.request)
        assert first == second and len(original) == 1
        assert original == _roles(inputs, "research_observed:")
        with inputs.database.session() as session:
            mapped = read_mapped_observation(session, first.observation_ref)
            row = session.get(RunRoleOutputRecord, first.observation_ref.output_id)
            assert row is not None
            row.payload_hash = "f" * 64
        with inputs.database.session() as session:
            with pytest.raises(ValueError, match="original_row_invalid"):
                read_mapped_observation(session, first.observation_ref)
        _observe(
            tmp_path,
            first=first.model_dump(mode="json"),
            second=second.model_dump(mode="json"),
            mapped=mapped.model_dump(mode="json"),
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_failed_partial_keeps_failure_and_has_evidence_only_observation(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        read_mapped_observation,
    )

    class Host(PreparedHost):
        async def status(self, payload: DshSessionSubmit):
            return self.status_value(payload, "failed", 4).model_copy(
                update={"error_code": "controlled_provider_failure"}
            )

        async def result(self, payload: DshSessionSubmit):
            self.result_calls += 1
            return _sql_repairable_result(inputs, payload).model_copy(
                update={"finish_reason": "error"}
            )

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), Host(inputs), poll_interval_seconds=0.001
    )
    try:
        observed = await runtime.execute_observed(inputs.request)
        with inputs.database.session() as session:
            mapped = read_mapped_observation(session, observed.observation_ref)
        raw = _roles(inputs, "research_transport:")
        failed = [r["body"] for r in raw if r["body"]["phase"] == "status"]
        assert len(failed) == 1 and failed[0]["reply"]["state"] == "failed"
        assert failed[0]["reply"]["error_code"] == "controlled_provider_failure"
        assert any(r["body"]["phase"] == "result" for r in raw)
        assert mapped.selection.kind == "evidence_only" and observed.result.evidence_candidates
        assert observed.result.causal_case is None and observed.result.horizons == []
        assert observed.result.total_tokens is None and observed.result.estimated_cost_usd is None
        link = runtime.links.get(inputs.request.run_id)
        assert link is not None and link.state == "failed"
        _observe(tmp_path, raw=raw, mapped=mapped.model_dump(mode="json"))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_final_writer_fresh_original_binding_rejects_drift_before_existing_receipt_readback(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        read_mapped_observation,
        store_mapped_observation,
    )
    from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
        read_runtime_task_anchor,
        verify_runtime_task_anchor,
    )

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), PreparedHost(inputs), poll_interval_seconds=0.001
    )
    try:
        observed = await runtime.execute_observed(inputs.request)
        with inputs.database.session() as session:
            anchor = read_runtime_task_anchor(session, inputs.request.run_id)
            receipt = read_mapped_observation(session, observed.observation_ref)
        before = _roles(inputs, "research_observed:")
        with pytest.raises(ValueError):
            with inputs.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                row = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == inputs.request.run_id,
                        RunRoleOutputRecord.role == TASK_ROLE,
                    )
                )
                assert row is not None
                # Damage actual SQL within the physical writer, not the guard function.
                session.delete(row)
                session.flush()
                store_mapped_observation(
                    session,
                    receipt,
                    binding_validator=lambda current: verify_runtime_task_anchor(current, anchor),
                )
        assert _roles(inputs, "research_observed:") == before
        with inputs.database.session() as session:
            assert read_runtime_task_anchor(session, inputs.request.run_id) == anchor
        _observe(
            tmp_path,
            original=observed.model_dump(mode="json"),
            original_mapped=before,
            after=_roles(inputs, "research_observed:"),
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_format_failure_returns_its_exact_persisted_failure_locator(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        read_transport_observation,
    )
    from packages.kernel.decision_hub_kernel.ports.research_observation import (
        ObservedAgentExecutionError,
        ResearchObservedError,
    )

    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit):
            original = await super().result(payload)
            text = "not a synthesis object"
            return original.model_copy(
                update={
                    "final_response": text,
                    "result_hash": hashlib.sha256(
                        (text + "\0" + original.events_json).encode()
                    ).hexdigest(),
                }
            )

    host = Host(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await runtime.execute_observed(inputs.request)
        failures = [
            r for r in _roles(inputs, "research_transport:") if r["body"]["phase"] == "error"
        ]
        assert host.result_calls == 1 and failures
        reference = getattr(caught.value, "observation_ref", None)
        _observe(
            tmp_path,
            actual_results=host.result_calls,
            code=caught.value.error_code,
            reference=reference.model_dump(mode="json") if reference else None,
            failures=failures,
            mapped=_roles(inputs, "research_observed:"),
        )
        assert reference is not None, (
            "failure was stored but its original exception lost the locator"
        )
        assert any(r["id"] == reference.output_id for r in failures)
        assert isinstance(caught.value, ObservedAgentExecutionError)
        original = caught.value.__cause__
        assert isinstance(original, AgentExecutionError)
        assert caught.value.__dict__ == {**original.__dict__, "observation_ref": reference}
        with inputs.database.session() as session:
            raw = read_transport_observation(session, reference)
        assert raw.error == ResearchObservedError.from_error(original)
        assert raw.error_code == (original.cause_code or original.error_code)
        assert _roles(inputs, "research_observed:") == []
    finally:
        await runtime.close()


def test_observed_error_preserves_original_full_error_fields_and_provider_cause():
    from datetime import UTC, datetime

    from packages.kernel.decision_hub_kernel.ports.research_observation import (
        ObservedAgentExecutionError,
        ObservedExecutionRef,
        ResearchObservedError,
    )

    original = AgentExecutionError(
        "controlled_error",
        "Exact original message",
        retryable=True,
        attempt=3,
        provider_id="controlled-provider",
        model="controlled-model",
        origin="gateway",
        cause_code="controlled_cause",
        capability_id="replay.research",
        tool_call_id="real-call",
        deadline_ms=1200,
    )
    cause = ValueError("controlled-provider-cause")
    original.__cause__ = cause
    reference = ObservedExecutionRef(
        output_id="actual",
        role="actual",
        schema_version="actual",
        payload_hash="a" * 64,
        recorded_at=datetime.now(UTC),
    )
    try:
        raise ObservedAgentExecutionError(original, reference) from original
    except ObservedAgentExecutionError as returned:
        assert returned.__cause__ is original and original.__cause__ is cause
        assert ResearchObservedError.from_error(returned) == ResearchObservedError.from_error(
            original
        )
        assert returned.provenance() == original.provenance()


@pytest.mark.asyncio
async def test_unconfirmed_actual_raw_writer_never_retries_the_host_rpc(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio
    import threading
    from collections.abc import Callable
    from concurrent.futures import Future

    from packages.kernel.decision_hub_kernel.ports.research_observation import (
        ObservedExecutionRef,
        ResearchTransportObservation,
    )
    from packages.runtime_adapters.dsh_runtime import web_runtime as module
    from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead

    entered = threading.Event()
    original = module.store_transport_observation
    first = True

    def delayed(session: Session, value: ResearchTransportObservation) -> ObservedExecutionRef:
        nonlocal first
        if first and value.phase == "accepted":
            first = False
            entered.set()
            # The original physical writer really remains alive after the waiter expires.
            threading.Event().wait(6.5)
        return original(session, value)

    monkeypatch.setattr(module, "store_transport_observation", delayed)

    class Host(PreparedHost):
        async def submit(self, payload: DshSessionSubmit):
            returned = await super().submit(payload)
            if len(self.submit_calls) > 1:
                retry_while_writer_live.append(any(not future.done() for future in futures))
            return returned

    host = Host(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    executor = runtime.cancellation_projector._executor  # pyright: ignore[reportPrivateUsage]
    actual_submit = executor.submit
    futures: list[Future[ProjectionRead[object]]] = []
    retry_while_writer_live: list[bool] = []

    def observe_future(
        operation: Callable[[], ProjectionRead[object]],
    ) -> Future[ProjectionRead[object]]:
        future = actual_submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(executor, "submit", observe_future)
    error = None
    try:
        try:
            await runtime.execute_observed(inputs.request)
        except AgentExecutionError as exc:
            error = exc
        assert entered.is_set()
        actual_calls = len(host.submit_calls)
        # No future is killed or slot released by caller timeout. Observe real completion.
        for _ in range(80):
            if all(future.done() for future in futures):
                break
            await asyncio.sleep(0.05)
        ready = [future.result() for future in futures if future.done()]
        originals = [
            item.value
            for item in ready
            if item.state == "ready" and isinstance(item.value, ObservedExecutionRef)
        ]
        _observe(
            tmp_path,
            submits=actual_calls,
            error=error.cause_code if error else None,
            retry_while_writer_live=retry_while_writer_live,
            all_threads_done=all(future.done() for future in futures),
            actual_committed_refs=[item.model_dump(mode="json") for item in originals],
            raw=_roles(inputs, "research_transport:"),
        )
        assert originals and all(future.done() for future in futures)
        assert actual_calls == 1, "unconfirmed raw write incorrectly re-issued the real Host RPC"
        assert error is not None and error.error_code == "research_observation_unavailable"
        assert host.result_calls == 0 and _roles(inputs, "research_observed:") == []
    finally:
        await runtime.close()
