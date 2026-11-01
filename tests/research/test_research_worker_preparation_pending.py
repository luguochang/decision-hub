"""Offline actual Worker, SQLite saver and late execution preparation controls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import pytest
import yaml
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.hub_worker.research import DurableResearchWorker, ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import DshSessionCompletion
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
# Register the complete publication schema before the isolated fixture calls create_all.
from packages.kernel.decision_hub_kernel.persistence import (
    research_publication_bundle_records,  # noqa: F401
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionPromptRecord,
    ResearchTraceRecord,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore
from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import RuntimePreparedTurn
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.orchestration.test_graph_observed_result_v2 import (
    _prepared_inputs,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
PACK = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


@pytest.fixture
def inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    # This prepares actual Factory rows while the Run remains admitted. Only
    # actual Worker.tick claims it; no test changes status, owner or expiry.
    yield from _prepared_inputs(tmp_path, max_rounds=1, max_repairs=0)


def _worker(
    fixture: PreparedInputs, runtime: DshWebResearchRuntime,
    tmp_path: Path, owner: str, *, lease_seconds: int = 20,
) -> DurableResearchWorker:
    worker = DurableResearchWorker(
        fixture.database, runtime, pack_root=PACK,
        checkpoint_path=tmp_path / "worker-checkpoint.sqlite",
        worker_id=owner, execution_mode="replay",
        allowed_capabilities=fixture.request.allowed_capabilities,
        lease_seconds=lease_seconds,
    )
    root = tmp_path / "prepared-pack"
    manifest = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
    # An actual explicit prepared Factory replaces the default constructor's
    # v1 choice only in this isolated fixture; no source/pointer is rewritten.
    worker.factory = ResearchRequestFactory(
        fixture.database, pack_root=root, execution_mode="replay",
        allowed_capabilities=fixture.request.allowed_capabilities,
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
    )
    return worker


def _sql(fixture: PreparedInputs) -> dict[str, Any]:
    with fixture.database.session() as session:
        run = session.get(RunRecord, fixture.request.run_id, populate_existing=True)
        assert run is not None
        return {
            "status": run.status, "owner": run.lease_owner,
            "lease_expires_at": str(as_utc(run.lease_expires_at)),
            "artifact_id": run.artifact_id, "error_code": run.error_code,
            "finished_at": str(run.finished_at), "cost_usd": run.cost_usd,
            "pending_events": [
                {"sequence": row.sequence_no, "type": row.event_type,
                 "at": str(row.occurred_at), "body": json.loads(row.payload_json)}
                for row in session.query(RunEventRecord).filter_by(
                    run_id=run.run_id, event_type="research.execution.preparation_pending"
                ).all()
            ],
            "traces": [
                {"id": row.id, "event_type": row.event_type, "stage": row.stage,
                 "status": row.status, "error_code": row.error_code,
                 "summary": row.summary, "hash": row.event_hash}
                for row in session.query(ResearchTraceRecord).filter_by(run_id=run.run_id).all()
            ],
            "roles": {
                row.role: {"id": row.output_id, "hash": row.payload_hash,
                           "schema": row.schema_version, "at": str(row.created_at)}
                for row in session.query(RunRoleOutputRecord).filter_by(run_id=run.run_id).all()
            },
        }


def _observe(tmp_path: Path, body: dict[str, Any]) -> None:
    encoded = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    target = tmp_path / "worker-pending-observation.json"
    target.write_text(encoded, encoding="utf-8")
    if destination := os.getenv("DECISION_HUB_WORKER_PENDING_EVIDENCE_DIR"):
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / (tmp_path.name + ".json")).write_text(encoded, encoding="utf-8")


def _full_sql_rows(fixture: PreparedInputs) -> dict[str, list[dict[str, Any]]]:
    # Fixed table/PK allowlist; retain original SQLite values and complete JSON
    # text rather than claiming a projection of selected columns is all rows.
    tables = (
        ("runs", "run_id"), ("run_events", "id"),
        ("research_trace_events", "id"), ("run_role_outputs", "output_id"),
    )
    with fixture.database.session() as session:
        return {
            table: [dict(row) for row in session.connection().exec_driver_sql(
                f"SELECT * FROM {table} WHERE run_id = ? ORDER BY {key}",
                (fixture.request.run_id,),
            ).mappings()]
            for table, key in tables
        }


@pytest.mark.asyncio
async def test_actual_worker_retains_pending_then_real_expired_lease_new_owner_resumes(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = inputs

    class CompletedPreparedHost(PreparedHost):
        async def result(self, payload):
            result = await super().result(payload)
            with fixture.database.session() as session:
                prepared = read_prepared_execution_in_session(
                    session, payload.run_id, payload.generation,
                )
                prompt = prepared.host_prompt()
                prompt_row = session.get(
                    DshSessionPromptRecord, (payload.run_id, payload.generation),
                )
                assert prompt_row is not None
                created = as_utc(prompt_row.created_at)
                assert created is not None
            assert (prompt.run_id, prompt.generation, prompt.dsh_session_id, prompt.request_id) == (
                payload.run_id, payload.generation, payload.deterministic_session_id,
                payload.deterministic_request_id,
            )
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            elapsed = created - epoch
            created_us = ((elapsed.days * 86400 + elapsed.seconds) * 1000000
                          + elapsed.microseconds)
            started_ms = (created_us + 999) // 1000
            now = datetime.now(UTC) - epoch
            finished_ms = ((now.days * 86400 + now.seconds) * 1000
                           + now.microseconds // 1000)
            assert started_ms <= finished_ms
            started_at = epoch + timedelta(milliseconds=started_ms)
            finished_at = epoch + timedelta(milliseconds=finished_ms)
            assert finished_at <= payload.deadline_at
            events = [
                {"method": "session.event", "payload": {
                    "sessionId": prompt.dsh_session_id,
                    "event": {"type": "user/message", "seq": 1, "time": started_ms,
                              "data": {"source": {"kind": "user", "rpcId": prompt.request_id}}},
                }},
                {"method": "session.event", "payload": {
                    "sessionId": prompt.dsh_session_id,
                    "event": {"type": "turn/end", "seq": 3, "time": finished_ms,
                              "data": {"reason": {"kind": "completed"}}},
                }},
            ]
            events_json = json.dumps(events, separators=(",", ":"))
            result = result.model_copy(update={
                "started_at": started_at, "finished_at": finished_at,
                "last_seq": 3, "events_json": events_json,
                "trace_ref": f"dsh://sessions/{prompt.dsh_session_id}?last_seq=3",
                "result_hash": hashlib.sha256(
                    (result.final_response + "\0" + events_json).encode(),
                ).hexdigest(),
            })
            # Simulate the authenticated Host callback through its normal writer;
            # Runtime then reads and validates this original before Review/Commit.
            DshSessionLinkService(fixture.database).complete(DshSessionCompletion(
                schema_version="dsh-session-completion.v1", run_id=payload.run_id,
                dsh_session_id=prompt.dsh_session_id, terminal_status="completed",
                generation=prompt.generation, last_seq=result.last_seq,
                trace_ref=result.trace_ref,
                result_ref=f"dsh-host://runs/{payload.run_id}/result",
                result_hash=result.result_hash, completed_at=finished_at, error=None,
            ), authenticated_host=True)
            return result

    host = CompletedPreparedHost(fixture)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(fixture.database), host, poll_interval_seconds=.001,
    )
    runtime.preparation_confirmation_wait_seconds = 5
    worker = _worker(fixture, runtime, tmp_path, "worker-pending-owner-a")
    release = threading.Event()
    entered = threading.Event()
    completed = threading.Event()
    futures: list[Future[Any]] = []
    outcomes: list[dict[str, Any]] = []
    original_submit = runtime.cancellation_projector._executor.submit  # pyright: ignore[reportPrivateUsage]
    original_write = runtime.cancellation_projector.write
    held_once = False
    renewal_lock = threading.Lock()
    renewals: list[dict[str, Any]] = []
    original_renew = worker.runs.renew_lease

    def observed_renew(run_id: str, worker_id: str, *, lease_seconds: int) -> bool:
        entry: dict[str, Any] = {
            "run_id": run_id, "worker_id": worker_id,
            "started_at": datetime.now(UTC).isoformat(), "finished_at": None,
        }
        with renewal_lock:
            renewals.append(entry)
        try:
            renewed = original_renew(run_id, worker_id, lease_seconds=lease_seconds)
            entry["renewed"] = renewed
            return renewed
        finally:
            entry["finished_at"] = datetime.now(UTC).isoformat()

    monkeypatch.setattr(worker.runs, "renew_lease", observed_renew)

    def observe_submit[T](operation: Callable[[], T]) -> Future[T]:
        future = original_submit(operation)
        futures.append(future)

        def done(actual: Future[T]) -> None:
            outcome: dict[str, Any] = {"done": actual.done()}
            try:
                value = actual.result()
                if isinstance(value, ProjectionRead):
                    outcome.update(state=value.state, limitation=value.limitation)
                    if isinstance(value.value, RuntimePreparedTurn):
                        outcome.update(generation=value.value.prepared.execution.generation,
                                       current_round=value.value.request.current_round)
                        completed.set()
            except BaseException as error:
                outcome["error"] = type(error).__name__
            outcomes.append(outcome)

        future.add_done_callback(done)
        return future

    async def held_write[T](
        operation: Callable[[Session], T], *, timeout_seconds: float = .25,
    ) -> ProjectionRead[T]:
        nonlocal held_once
        if held_once:
            return await original_write(operation, timeout_seconds=timeout_seconds)
        held_once = True

        def actual_operation(session: Session) -> T:
            entered.set()
            if not release.wait(8):
                raise RuntimeError("controlled_preparation_release_missing")
            return operation(session)

        # Hold only the first actual prepare operation. The real delegated
        # Future, Session, Kernel validation, transaction, two slots and configured
        # 5s preparation confirmation waiter all run. Release after pending, before Worker writer begins.
        result = await original_write(actual_operation, timeout_seconds=timeout_seconds)
        release.set()
        return result

    monkeypatch.setattr(runtime.cancellation_projector._executor, "submit", observe_submit)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(runtime.cancellation_projector, "write", held_write)
    proof: dict[str, Any] = {"before": _sql(fixture)}
    resumed_runtime: DshWebResearchRuntime | None = None
    try:
        report = await worker.tick()
        proof.update(first_report=report.model_dump(mode="json") if report else None,
                     after_first_tick=_sql(fixture), host_before_resume=len(host.submit_calls),
                     future_outcomes=outcomes, configured_preparation_confirmation_wait_seconds=runtime.preparation_confirmation_wait_seconds)
        _observe(tmp_path, proof)
        assert entered.is_set() and runtime.binding_wait_seconds == 5
        assert runtime.preparation_confirmation_wait_seconds == 5
        assert report is not None and report.status == "preparation_pending", (
            "unconfirmed preparation became failed instead of retaining the original Run"
        )
        assert report.error_code == "research_method_binding_failed"
        first = _sql(fixture)
        assert first["status"] == "running" and first["error_code"] is None
        assert first["owner"] == "worker-pending-owner-a" and first["artifact_id"] is None
        assert len(first["pending_events"]) == 1
        assert host.submit_calls == [] and host.result_calls == 0
        assert await asyncio.to_thread(completed.wait, 10), "original prepare future unresolved"
        assert all(future.done() for future in futures)
        assert any(outcome.get("generation") == 1 for outcome in outcomes)
        original_rows = _sql(fixture)["roles"]
        original_role_rows = {
            row["output_id"]: row for row in _full_sql_rows(fixture)["run_role_outputs"]
        }
        assert worker.executor.checkpoint_path is not None
        async with CheckpointStore(worker.executor.checkpoint_path).open() as saver:
            checkpoint = await saver.aget_tuple({
                "configurable": {"thread_id": fixture.request.run_id}
            })
            assert checkpoint is not None
            checkpoint_identity = checkpoint.checkpoint["id"]
            channels = checkpoint.checkpoint["channel_values"]
            assert channels["request"] == fixture.request.model_dump(mode="json")
        await runtime.close()
        # Cancelling the renewal coroutine cannot abort an already submitted
        # to_thread DB update. Re-read the actual persisted lease after every
        # wait and reject recovery until its true UTC expiry, within this bound.
        wait_deadline = time.monotonic() + 45
        lease_observations: list[dict[str, Any]] = []
        while True:
            original_run = fixture.database.get_run_record(fixture.request.run_id)
            assert original_run is not None
            expiry = as_utc(original_run.lease_expires_at)
            observed_at = datetime.now(UTC)
            assert expiry is not None
            assert original_run.lease_owner == "worker-pending-owner-a"
            assert original_run.status == "running" and original_run.artifact_id is None
            assert original_run.error_code is None
            with renewal_lock:
                renewal_active = any(row["finished_at"] is None for row in renewals)
            lease_observations.append({
                "observed_at": observed_at.isoformat(), "expiry": expiry.isoformat(),
                "owner": original_run.lease_owner, "status": original_run.status,
                "renewal_active": renewal_active,
            })
            if expiry <= observed_at and not renewal_active:
                break
            remaining_bound = wait_deadline - time.monotonic()
            assert remaining_bound > 0, "actual persisted lease did not expire within bound"
            remaining_lease = (expiry - observed_at).total_seconds()
            assert remaining_lease < 21
            await asyncio.sleep(min(max(0.03, remaining_lease + 0.03), remaining_bound))
        proof.update(lease_observations=lease_observations, actual_renewals=renewals)
        _observe(tmp_path, proof)
        resumed_runtime = DshWebResearchRuntime(
            DshSessionLinkService(fixture.database), host, poll_interval_seconds=.001,
        )
        # Only the first owner needs a short expiry for the recovery test.
        # The recovered claim uses the real Worker default for the complete
        # validation/publication chain; its original permission fence remains.
        next_worker = _worker(
            fixture, resumed_runtime, tmp_path, "worker-pending-owner-b", lease_seconds=240
        )
        claims: list[dict[str, Any]] = []
        original_claim = next_worker.runs.claim_next

        def observed_claim(**kwargs: Any):
            value = original_claim(**kwargs)
            if value is not None:
                claims.append(value.model_dump(mode="json"))
            return value

        monkeypatch.setattr(next_worker.runs, "claim_next", observed_claim)
        recovery_called_at = datetime.now(UTC)
        assert recovery_called_at >= expiry
        resumed = await next_worker.tick()
        final = _sql(fixture)
        proof.update(checkpoint_id=checkpoint_identity, real_expired_lease=str(expiry),
                     recovery_called_at=recovery_called_at.isoformat(),
                     claims=claims,
                     final_report=resumed.model_dump(mode="json") if resumed else None,
                     after_resume=final, host_generations=[s.generation for s in host.submit_calls],
                     future_outcomes=outcomes,
                     original_prepared_rows=original_rows)
        _observe(tmp_path, proof)
        assert len(claims) == 1 and claims[0]["recovered"] is True
        assert claims[0]["worker_id"] == "worker-pending-owner-b"
        assert resumed is not None and resumed.artifact_id is not None
        assert resumed.status == "reject", "controlled incomplete evidence must not publish"
        assert final["status"] in {"completed", "degraded"} and final["error_code"] is None
        assert [s.generation for s in host.submit_calls] == [1] and host.result_calls == 1
        for role in (
            "research_execution_method", "dsh_execution_request:1", "dsh_prompt_integrity:1"
        ):
            assert final["roles"][role] == original_rows[role]
        assert len(final["pending_events"]) == 1
        assert final["cost_usd"] is None
        assert "research_publication_source" in final["roles"]
        final_role_rows = {
            row["output_id"]: row for row in _full_sql_rows(fixture)["run_role_outputs"]
        }
        for output_id, original_row in original_role_rows.items():
            assert final_role_rows[output_id] == original_row
    except BaseException as error:
        proof.update(failure_type=type(error).__name__, failure_message=str(error),
                     failure_sql=_sql(fixture), future_outcomes=outcomes,
                     host_generations=[s.generation for s in host.submit_calls])
        _observe(tmp_path, proof)
        raise
    finally:
        release.set()
        await runtime.close()
        if resumed_runtime is not None:
            await resumed_runtime.close()


def _pending_error() -> AgentExecutionError:
    return AgentExecutionError(
        "research_method_binding_failed", "Execution preparation was not confirmed",
        retryable=True, provider_id="dsh-web", origin="orchestration",
        cause_code="cancellation_projection_wait_elapsed",
    )


async def _checkpoint_before_rpc(
    fixture: PreparedInputs, worker: DurableResearchWorker,
    runtime: DshWebResearchRuntime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = worker.runs.claim_next(
        strategy_version=worker.strategy_version, worker_id=worker.worker_id,
        lease_seconds=worker.lease_seconds,
    )
    assert claim is not None and claim.recovered is False

    async def controlled_prepare_failure(*_args: object, **_kwargs: object) -> NoReturn:
        # Negative storage/identity fixtures stop before RPC. They produce no
        # native observation and cannot certify a Runtime prepare succeeded.
        raise _pending_error()

    monkeypatch.setattr(runtime, "execute_observed", controlled_prepare_failure)
    with pytest.raises(AgentExecutionError) as raised:
        await worker.executor.execute(fixture.request)
    assert raised.value.cause_code == "cancellation_projection_wait_elapsed"
    assert worker.executor.checkpoint_path is not None
    async with CheckpointStore(worker.executor.checkpoint_path).open() as saver:
        saved = await saver.aget_tuple({"configurable": {"thread_id": fixture.request.run_id}})
        assert saved is not None
        assert saved.checkpoint["channel_values"]["request"] == fixture.request.model_dump(
            mode="json"
        )


@pytest.mark.asyncio
async def test_actual_sqlite_busy_retains_original_run_without_false_pending_receipt(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "busy-owner", lease_seconds=240)
    proof: dict[str, Any] = {}
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        before = _sql(inputs)
        full_before = _full_sql_rows(inputs)
        result: tuple[str, str | None] | None = None
        failure: BaseException | None = None
        with inputs.database.engine.connect() as other:
            other.exec_driver_sql("BEGIN IMMEDIATE")
            started = time.monotonic()
            try:
                result = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
                    inputs.request.event_id, inputs.request.run_id, _pending_error()
                )
            except Exception as error:
                failure = error
            finally:
                elapsed = time.monotonic() - started
                other.rollback()
        after = _sql(inputs)
        full_after = _full_sql_rows(inputs)
        proof.update(before=before, after=after, outcome=result, elapsed_seconds=elapsed,
                     exception_type=type(failure).__name__ if failure else None,
                     error=str(failure) if failure else None, host_submits=len(host.submit_calls),
                     full_before=full_before, full_after=full_after)
        _observe(tmp_path, proof)
        assert before == after, "busy wait must not write a pending event or terminal Run"
        assert full_before == full_after
        assert host.submit_calls == []
        assert elapsed >= 4.5, "real 5000ms SQLite busy_timeout was not reached"
        assert result == ("storage_unavailable", "research_preparation_storage_busy"), (
            "real SQLite contention escaped instead of reporting an unconfirmed storage wait"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_writer_freezes_original_witnesses_and_rolls_back_after_real_flush(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "metadata-owner", lease_seconds=240)
    proof: dict[str, Any] = {}
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        with inputs.database.session() as session:
            bundle = read_research_bundle_in_session(session, inputs.request.run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        # Genuine Kernel preparation (no Host), not hand-written receipt rows.
        prepared = replace(inputs, prompt=render_prepared_research_policy(
            bundle, inputs.request.allowed_capabilities
        ) + "\n\nControlled body; not an executed Host.").prepare()
        before = _sql(inputs)
        full_before = _full_sql_rows(inputs)
        error = AgentExecutionError(
            "research_method_binding_failed", "Execution preparation was not confirmed",
            retryable=True, attempt=3, provider_id="dsh-web", model="controlled-model",
            origin="orchestration", cause_code="cancellation_projection_wait_elapsed",
            capability_id="controlled-capability", tool_call_id="controlled-call", deadline_ms=123,
        )
        outcome = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, error
        )
        after = _sql(inputs)
        full_after = _full_sql_rows(inputs)
        assert outcome == ("pending", None)
        assert full_after["runs"] == full_before["runs"]
        assert full_after["run_role_outputs"] == full_before["run_role_outputs"]
        for table in ("run_events", "research_trace_events"):
            assert full_after[table][:-1] == full_before[table]
            assert len(full_after[table]) == len(full_before[table]) + 1
        body = after["pending_events"][0]["body"]
        task_row = before["roles"]["research_task_request"]
        assert body["task"]["output_id"] == task_row["id"]
        assert body["task"]["payload_hash"] == task_row["hash"]
        assert body["task"]["schema_version"] == task_row["schema"]
        assert body["task"]["request_hash"] == bundle.task.request_hash
        assert body["task"]["stored_request_hash_kind"] == bundle.task.request_hash_kind
        assert body["task"]["request_recipe"] == "canonical_research_session_request.v1"
        assert body["task"]["scope_hash"] == bundle.task.scope.scope_hash
        assert body["preparation"] == {
            "method_hash": prepared.method.method_hash,
            "method_recorded_at": prepared.method.recorded_at.isoformat(),
            "public_request_hash": prepared.method.request_hash,
            "public_request_hash_kind": "dsh_link_identity.v1",
            "generation": 1, "source_round": prepared.execution.request.current_round,
            "turn_kind": "research_round",
            "actual_full_request_hash": prepared.execution.request_hash,
            "actual_full_request_hash_kind": "canonical_research_session_request.v1",
            "actual_reduced_request_hash": prepared.execution.execution_request_hash,
            "actual_reduced_request_hash_kind": "dsh_link_identity.v1",
        }
        assert body["original_checkpoint"] == {
            "run_id": inputs.request.run_id, "logical_round": 1,
            "graph_round_admission_ref": None, "graph_final_projection_ref": None,
        }
        assert body["original_lease"]["owner"] == before["owner"]
        assert datetime.fromisoformat(body["original_lease"]["expires_at"]) == (
            datetime.fromisoformat(before["lease_expires_at"])
        )
        assert body["diagnostic"] == {
            "error_code": error.error_code, "message": str(error), "cause_code": error.cause_code,
            "origin": error.origin, "retryable": True, "attempt": 3,
            "provider_id": error.provider_id, "model": error.model,
            "capability_id": error.capability_id, "tool_call_id": error.tool_call_id,
            "deadline_ms": 123,
        }
        assert after["roles"] == before["roles"]
        assert {k: v for k, v in after.items() if k not in {"pending_events", "traces"}} == {
            k: v for k, v in before.items() if k not in {"pending_events", "traces"}
        }
        assert host.submit_calls == []
        original_append = worker.observability.append_trace_in_session
        reached: list[str] = []

        def flush_then_expire(session: Session, event: Any, *, once: bool = False):
            result = original_append(session, event, once=once)
            reached.append("expiry_after_trace_sql_flush")
            run = session.get(RunRecord, inputs.request.run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.flush()
            return result

        monkeypatch.setattr(worker.observability, "append_trace_in_session", flush_then_expire)
        refused = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, error
        )
        assert refused == ("lease_lost", "run_publication_lease_lost")
        assert _sql(inputs) == after, "all event/trace/expiry writes must roll back together"
        assert _full_sql_rows(inputs) == full_after

        for field, value, expected in (
            ("lease_owner", "foreign-owner", ("lease_lost", "run_publication_lease_lost")),
            ("status", "cancelled", ("cancelled", "cancelled_by_owner")),
        ):
            def flush_then_permission_change(
                session: Session, event: Any, *, once: bool = False,
                changed_field: str = field, changed_value: str = value,
            ):
                result = original_append(session, event, once=once)
                reached.append(changed_field + "_after_trace_sql_flush")
                run = session.get(RunRecord, inputs.request.run_id)
                assert run is not None
                setattr(run, changed_field, changed_value)
                session.flush()
                return result

            monkeypatch.setattr(
                worker.observability, "append_trace_in_session", flush_then_permission_change
            )
            outcome = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
                inputs.request.event_id, inputs.request.run_id, error
            )
            assert outcome == expected
            assert _sql(inputs) == after
            assert _full_sql_rows(inputs) == full_after

        def flush_then_fail(session: Session, event: Any, *, once: bool = False):
            original_append(session, event, once=once)
            reached.append("io_after_trace_sql_flush")
            raise OSError("controlled_after_real_pending_flush")

        monkeypatch.setattr(worker.observability, "append_trace_in_session", flush_then_fail)
        denied = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, error
        )
        assert denied == ("ineligible", "controlled_after_real_pending_flush")
        assert _sql(inputs) == after
        assert _full_sql_rows(inputs) == full_after

        def flush_then_nonbusy_sql(session: Session, event: Any, *, once: bool = False):
            original_append(session, event, once=once)
            reached.append("nonbusy_sql_after_trace_sql_flush")
            session.connection().exec_driver_sql("SELECT * FROM absent_pending_control_table")

        monkeypatch.setattr(worker.observability, "append_trace_in_session", flush_then_nonbusy_sql)
        with pytest.raises(OperationalError) as nonbusy:
            await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
                inputs.request.event_id, inputs.request.run_id, error
            )
        assert getattr(nonbusy.value.orig, "sqlite_errorcode", None) == 1
        assert _sql(inputs) == after
        assert _full_sql_rows(inputs) == full_after
        proof.update(before=before, after=after, payload=body, reached=reached,
                     expiry_denied=refused, io_denied=denied, nonbusy_code=1,
                     full_before=full_before, full_after=full_after,
                     full_after_all_rollbacks=_full_sql_rows(inputs),
                     host_generations=[s.generation for s in host.submit_calls])
        _observe(tmp_path, proof)
    except BaseException as error:
        proof.update(
            failure_type=type(error).__name__, failure_message=str(error), sql=_sql(inputs)
        )
        _observe(tmp_path, proof)
        raise
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_refuses_missing_corrupt_and_partial_original_native_proof(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "proof-owner", lease_seconds=240)
    cases: list[dict[str, Any]] = []
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        with inputs.database.session() as session:
            task = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role="research_task_request"
            ).one()
            original = {
                column.name: getattr(task, column.name) for column in task.__table__.columns
            }
        for variant in ("missing_task", "corrupt_task", "unknown_method"):
            with inputs.database.session() as session:
                task = session.get(RunRoleOutputRecord, original["output_id"])
                assert task is not None
                if variant == "missing_task":
                    session.delete(task)
                elif variant == "corrupt_task":
                    task.payload_json += " "
                else:
                    session.add(RunRoleOutputRecord(
                        output_id="unknown-method-control", run_id=inputs.request.run_id,
                        role="research_execution_method",
                        schema_version="research-execution-method.v999",
                        payload_json="{}", payload_hash=hashlib.sha256(b"{}").hexdigest(),
                        created_at=datetime.now(UTC),
                    ))
            before = _sql(inputs)
            outcome = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
                inputs.request.event_id, inputs.request.run_id, _pending_error()
            )
            after = _sql(inputs)
            cases.append({"variant": variant, "before": before, "after": after, "outcome": outcome})
            _observe(tmp_path, {"cases": cases})
            assert outcome[0] == "ineligible" and outcome[1]
            assert after == before and after["pending_events"] == []
            assert host.submit_calls == []
            with inputs.database.session() as session:
                if variant == "missing_task":
                    session.add(RunRoleOutputRecord(**original))
                elif variant == "corrupt_task":
                    restored = session.get(RunRoleOutputRecord, original["output_id"])
                    assert restored is not None
                    restored.payload_json = original["payload_json"]
                else:
                    inserted = session.get(RunRoleOutputRecord, "unknown-method-control")
                    assert inserted is not None
                    session.delete(inserted)
        # No original saver tuple: never build state from today's Run or Task.
        worker.executor.checkpoint_path = tmp_path / "empty-official-saver.sqlite"
        before = _sql(inputs)
        missing = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, _pending_error()
        )
        assert missing == ("ineligible", "research_preparation_checkpoint_missing")
        assert _sql(inputs) == before
        cases.append({"variant": "missing_checkpoint", "outcome": missing, "before": before})
        _observe(tmp_path, {"cases": cases})
    finally:
        await runtime.close()


@pytest.mark.parametrize("difference", ["schema_wait", "foreign_provider", "permanent"])
@pytest.mark.asyncio
async def test_other_binding_errors_keep_the_original_terminal_failure_path(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, difference: str,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "failure-owner")
    error = AgentExecutionError(
        "research_method_binding_failed",
        "Schema authentication was not confirmed" if difference == "schema_wait"
        else "Execution preparation was not confirmed",
        retryable=difference != "permanent", origin="orchestration",
        provider_id="foreign" if difference == "foreign_provider" else "dsh-web",
        cause_code="cancellation_projection_wait_elapsed",
    )

    async def other_failure(*_args: object, **_kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(runtime, "execute_observed", other_failure)
    try:
        report = await worker.tick()
        snapshot = _sql(inputs)
        _observe(tmp_path, {"difference": difference, "report": report.model_dump(mode="json")
                           if report else None, "sql": snapshot})
        assert report is not None and report.status == "failed"
        assert report.error_code == error.error_code
        assert snapshot["status"] == "failed" and snapshot["pending_events"] == []
        assert snapshot["artifact_id"] is None and host.submit_calls == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_external_shutdown_remains_cancelled_error_without_pending_or_terminal_write(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "shutdown-owner")
    entered = asyncio.Event()
    held = asyncio.Event()

    async def held_execution(*_args: object, **_kwargs: object) -> NoReturn:
        entered.set()
        await held.wait()
        raise AssertionError("controlled_held_execution_was_released")

    monkeypatch.setattr(runtime, "execute_observed", held_execution)
    operation = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        operation.cancel("controlled_external_shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation
        snapshot = _sql(inputs)
        _observe(tmp_path, {"sql": snapshot, "host_submits": len(host.submit_calls)})
        assert snapshot["status"] == "running" and snapshot["error_code"] is None
        assert snapshot["pending_events"] == [] and snapshot["artifact_id"] is None
        assert host.submit_calls == []
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_missing_checkpoint_does_not_overwrite_original_run_error(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "original-error-owner", lease_seconds=240)
    try:
        claim = worker.runs.claim_next(
            strategy_version=worker.strategy_version, worker_id=worker.worker_id,
            lease_seconds=worker.lease_seconds,
        )
        assert claim is not None
        with inputs.database.session() as session:
            run = session.get(RunRecord, inputs.request.run_id)
            assert run is not None
            run.error_code = "original_persisted_error"
        before = _full_sql_rows(inputs)
        outcome = await worker._retain_preparation_pending(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, _pending_error()
        )
        assert outcome == ("ineligible", "research_preparation_checkpoint_missing")
        changed = worker._fail_unrecoverable_preparation(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id, inputs.request.run_id, "research_method_binding_failed"
        )
        after = _full_sql_rows(inputs)
        _observe(tmp_path, {"before": before, "after": after, "outcome": outcome,
                           "changed": changed, "host_submits": len(host.submit_calls)})
        assert changed is False, "missing checkpoint must not overwrite an existing Run error"
        assert before == after and host.submit_calls == []
    finally:
        await runtime.close()
