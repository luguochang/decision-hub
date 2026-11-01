# pyright: reportPrivateUsage=false
"""Actual worker maintenance defaults against natural full V2 proof cost."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.hub_worker import main
from apps.hub_worker.cancelled_sessions import (
    DEFAULT_RECOVERY_TIME_BUDGET_SECONDS,
    cancelled_session_recovery,
    cancelled_session_recovery_time_budget,
    recover_cancelled_sessions_once,
)
from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    ObservationCreate,
    ResearchRunCommand,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database, RunRecord
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.dsh_runtime import cancelled_recovery
from packages.runtime_adapters.dsh_runtime.web_host_client import DshRecoveryBinding
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from packages.source_adapters.registry import SourceRegistry
from tests.dsh_native.test_cancellation_recovery_v2 import ControlledRecoveryHost
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_execution_preparation_v2 import inputs as inputs
from tests.evolution.test_research_worker import NOW
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,
)
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    return value


def all_sql_rows(prepared: PreparedInputs) -> dict[str, list[dict[str, Any]]]:
    """All columns, not selected fields; only this isolated fixture database."""
    with prepared.database.session() as session:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in Base.metadata.sorted_tables:
            query = select(table)
            if table.primary_key.columns:
                query = query.order_by(*table.primary_key.columns)
            rows = session.execute(query).mappings().all()
            result[table.name] = [
                {key: json_value(value) for key, value in row.items()} for row in rows
            ]
        return result


@pytest.mark.parametrize(
    "budget", [None, 8.0], ids=["actual_worker_default", "independent_8s_control"]
)
async def test_actual_worker_maintenance_entry_converges_natural_v2_completed_host(
    inputs: PreparedInputs,
    budget: float | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id)
        assert run is not None
        run.cost_usd = 0.37
    command = ResearchRunCommand(
        schema_version="research-run-command.v1",
        request_id="maintenance-budget-original-cancel",
        command="cancel",
        reason="Stop this original research",
    )
    accepted = ResearchCommandService(
        inputs.database,
        clock=lambda: inputs.now + timedelta(seconds=2),
    ).apply(inputs.request.run_id, command, owner_id="controlled-maintenance-owner")
    assert accepted.status == "accepted"
    host = ControlledRecoveryHost(inputs, state="completed")
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database),
        host,  # pyright: ignore[reportArgumentType]
    )
    recovery = cancelled_session_recovery(runtime)
    assert recovery is not None and recovery is runtime
    projector = runtime.cancellation_projector
    before = all_sql_rows(inputs)
    before_link = runtime.links.get(inputs.request.run_id)
    assert before_link is not None
    started = time.perf_counter()
    proof_calls: list[dict[str, Any]] = []
    original_proof = cancelled_recovery._load_binding

    def timed_actual_proof(session: Session, run_id: str) -> DshRecoveryBinding:
        phase_start = time.perf_counter()
        try:
            return original_proof(session, run_id)
        finally:
            proof_calls.append(
                {
                    "started_after_seconds": phase_start - started,
                    "finished_after_seconds": time.perf_counter() - started,
                    "elapsed_seconds": time.perf_counter() - phase_start,
                    "thread_name": threading.current_thread().name,
                    "session_id": id(session),
                }
            )

    monkeypatch.setattr(cancelled_recovery, "_load_binding", timed_actual_proof)
    futures: list[Future[Any]] = []
    original_submit = projector._executor.submit

    def capture_actual_future(*args: Any, **kwargs: Any) -> Future[Any]:
        future = original_submit(*args, **kwargs)
        futures.append(future)
        return future

    monkeypatch.setattr(projector._executor, "submit", capture_actual_future)
    heartbeats: list[float] = []
    heartbeat_stop = asyncio.Event()

    async def heartbeat() -> None:
        while not heartbeat_stop.is_set():
            heartbeats.append(time.perf_counter() - started)
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(heartbeat())
    observed: dict[str, Any] = {
        "requested_budget": budget,
        "default_override_used": budget is not None,
        "shared_default_seconds": DEFAULT_RECOVERY_TIME_BUDGET_SECONDS,
    }
    counts = None
    try:
        # This is the exact once helper called by main.py; no tick/clock/proof
        # replacement, warm-up, artificial proof delay or external Host/Provider.
        if budget is None:
            counts = await recover_cancelled_sessions_once(recovery)
        else:
            counts = await recover_cancelled_sessions_once(recovery, time_budget_seconds=budget)
        observed["caller_elapsed_seconds"] = time.perf_counter() - started
        observed["counts"] = counts
        observed["unfinished_futures_at_caller_return"] = sum(
            not future.done() for future in futures
        )
        observed["slots_at_caller_return"] = projector._slots._value
        return_link = runtime.links.get(inputs.request.run_id)
        assert return_link is not None
        observed["link_at_caller_return"] = return_link.model_dump(mode="json")
        # Await the original real futures before closing; pending is not a kill
        # or a rollback claim. This is lifecycle observation, not a retry tick.
        async with asyncio.timeout(5):
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures), return_exceptions=True
            )
        observed["all_original_futures_done"] = all(future.done() for future in futures)
        observed["slots_after_original_futures"] = projector._slots._value
        after_link = runtime.links.get(inputs.request.run_id)
        assert after_link is not None
        after = all_sql_rows(inputs)
        observed["before_all_sql_columns"] = before
        observed["after_all_sql_columns"] = after
        observed["link_after_real_future_completion"] = after_link.model_dump(mode="json")
        observed["rpc_calls"] = [name for name, _ in host.calls]
        observed["rpc_phase_elapsed"] = host.phase_elapsed
        observed["native_proof_calls"] = proof_calls
        observed["event_loop_heartbeat_count"] = len(heartbeats)
        observed["event_loop_max_gap_seconds"] = max(
            (right - left for left, right in zip(heartbeats, heartbeats[1:], strict=False)),
            default=0,
        )
        assert (
            observed["all_original_futures_done"] and observed["slots_after_original_futures"] == 2
        )
        assert len(heartbeats) > 1
        assert before_link.deadline_at == after_link.deadline_at
        assert before_link.max_tool_calls == after_link.max_tool_calls
        assert before_link.model_step_timeout_ms == after_link.model_step_timeout_ms
        assert before_link.generation == after_link.generation == prepared.link.generation
        assert before_link.dsh_session_id == after_link.dsh_session_id
        for table, rows in before.items():
            if table != "dsh_session_links":
                assert rows == after[table], table
        run = inputs.database.get_run_record(inputs.request.run_id)
        assert run is not None and run.cost_usd == 0.37 and run.status == "cancelled"
        assert isinstance(counts, dict) and counts["confirmed"] == 1
        assert after_link.state == "completed"
        assert [name for name, _ in host.calls] == ["status", "result"]
    except BaseException as exc:
        observed["assertion_or_exception"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        heartbeat_stop.set()
        await heartbeat_task
        async with asyncio.timeout(5):
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures), return_exceptions=True
            )
        await projector.close()
        await asyncio.sleep(0.02)
        observed["future_count"] = len(futures)
        observed["all_futures_done_finally"] = all(future.done() for future in futures)
        observed["executor_threads_alive_after_close"] = [
            thread.name for thread in projector._executor._threads if thread.is_alive()
        ]
        (tmp_path / "maintenance-budget-observation.json").write_text(
            json.dumps(observed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def second_actual_preparation(inputs: PreparedInputs, tmp_path: Path) -> PreparedInputs:
    """A second real Admission/Run/Factory/preparation in the original database."""
    event_id, _, admitted = AdmissionService(inputs.database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="A second official event confirms distinct inflation risks.",
            source_id="maintenance-second-input",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2),
            published_at=NOW - timedelta(seconds=3),
            language="en",
            event_hint="central_bank_speech",
        ),
    )
    assert admitted
    run_id, created = RunService(inputs.database).create(event_id, strategy_version="research.v1")
    assert created and run_id != inputs.request.run_id
    root, manifest = _prepared_pack(tmp_path / "second")
    request = (
        ResearchRequestFactory(
            inputs.database,
            pack_root=root,
            execution_mode="replay",
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        .build(event_id, run_id)
        .model_copy(deep=True)
    )
    request.evidence_requirements = request.evidence_requirements[:1]
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        max_tool_calls=request.execution_budget.max_tool_calls,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        generation=1,
    )
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, run_id)
        assert isinstance(bundle, FrozenResearchBundle)
    prepared = PreparedInputs(
        inputs.database,
        request,
        submit,
        inputs.upstream,
        render_prepared_research_policy(bundle, request.allowed_capabilities)
        + "\n\nControlled second body; not an executed Host.",
        datetime.now(UTC),
    )
    prepared.prepare()
    return prepared


async def test_two_real_runs_share_default_budget_and_retire_cursor_without_rewriting_originals(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    second = second_actual_preparation(inputs, tmp_path)
    actuals = (inputs, second)
    for index, item in enumerate(actuals):
        with inputs.database.session() as session:
            run = session.get(RunRecord, item.request.run_id)
            assert run is not None
            run.cost_usd = 0.37 + index
        accepted = ResearchCommandService(
            inputs.database,
            clock=lambda item=item: item.now + timedelta(seconds=2),
        ).apply(
            item.request.run_id,
            ResearchRunCommand(
                schema_version="research-run-command.v1",
                request_id=f"maintenance-budget-original-{index}",
                command="cancel",
                reason="Stop this original research",
            ),
            owner_id="controlled-maintenance-owner",
        )
        assert accepted.status == "accepted"
    hosts = {
        item.request.run_id: ControlledRecoveryHost(item, state="completed") for item in actuals
    }

    class MultiOriginalHost:
        async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
            return await hosts[binding.run_id].recovery_status(binding)

        async def recovery_cancel(
            self,
            binding: DshRecoveryBinding,
            reason: str,
        ) -> DshSessionStatus:
            return await hosts[binding.run_id].recovery_cancel(binding, reason)

        async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
            return await hosts[binding.run_id].recovery_result(binding)

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database),
        MultiOriginalHost(),  # pyright: ignore[reportArgumentType]
    )
    before = all_sql_rows(inputs)
    before_links = {item.request.run_id: runtime.links.get(item.request.run_id) for item in actuals}
    started = time.perf_counter()
    phases: list[dict[str, Any]] = []
    original_proof = cancelled_recovery._load_binding

    def actual_timed_proof(session: Session, run_id: str) -> DshRecoveryBinding:
        tick = time.perf_counter()
        try:
            return original_proof(session, run_id)
        finally:
            phases.append({"run_id": run_id, "elapsed_seconds": time.perf_counter() - tick})

    monkeypatch.setattr(cancelled_recovery, "_load_binding", actual_timed_proof)
    observed: dict[str, Any] = {"shared_default_seconds": DEFAULT_RECOVERY_TIME_BUDGET_SECONDS}
    try:
        report = await recover_cancelled_sessions_once(runtime)
        observed["caller_elapsed_seconds"] = time.perf_counter() - started
        observed["counts"] = report
        observed["cursor_after_first_tick"] = runtime.cancelled_reconciler._cursor
        observed["backoff_after_first_tick"] = dict(runtime.cancelled_reconciler._backoff)
        following_counts = await recover_cancelled_sessions_once(runtime)
        observed["following_tick_counts"] = following_counts
        observed["cursor_after_following_tick"] = runtime.cancelled_reconciler._cursor
        after = all_sql_rows(inputs)
        observed["before_all_sql_columns"] = before
        observed["after_all_sql_columns"] = after
        observed["rpc_calls_by_run"] = {
            key: [name for name, _ in host.calls] for key, host in hosts.items()
        }
        observed["native_proof_calls"] = phases
        assert isinstance(report, dict) and report["scanned"] == report["confirmed"] == 2
        assert report["pending"] == 0
        assert isinstance(following_counts, dict) and following_counts["scanned"] == 0
        assert observed["cursor_after_first_tick"] == max(hosts)
        assert observed["cursor_after_following_tick"] == observed["cursor_after_first_tick"]
        assert observed["backoff_after_first_tick"] == {}
        assert runtime.cancellation_projector._slots._value == 2
        for index, item in enumerate(actuals):
            before_link, after_link = (
                before_links[item.request.run_id],
                runtime.links.get(item.request.run_id),
            )
            assert before_link is not None and after_link is not None
            assert after_link.state == "completed"
            for field in (
                "dsh_session_id",
                "generation",
                "request_hash",
                "deadline_at",
                "max_tool_calls",
                "model_step_timeout_ms",
            ):
                assert getattr(after_link, field) == getattr(before_link, field)
            run = inputs.database.get_run_record(item.request.run_id)
            assert run is not None and run.status == "cancelled" and run.cost_usd == 0.37 + index
            assert observed["rpc_calls_by_run"][item.request.run_id] == ["status", "result"]
        for table, rows in before.items():
            if table != "dsh_session_links":
                assert rows == after[table], table
    except BaseException as exc:
        observed["assertion_or_exception"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        await runtime.cancellation_projector.close()
        await asyncio.sleep(0.02)
        observed["slots_after_close"] = runtime.cancellation_projector._slots._value
        observed["executor_threads_alive_after_close"] = [
            thread.name
            for thread in runtime.cancellation_projector._executor._threads
            if thread.is_alive()
        ]
        (tmp_path / "maintenance-budget-observation.json").write_text(
            json.dumps(observed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@pytest.mark.parametrize("configured", [None, "", "2", "30"])
def test_budget_configuration_default_and_explicit_values(
    configured: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "DECISION_HUB_CANCELLED_SESSION_RECOVERY_TIME_BUDGET_SECONDS"
    if configured is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, configured)
    assert cancelled_session_recovery_time_budget() == (float(configured) if configured else 8.0)


@pytest.mark.parametrize("configured", ["nan", "inf", "0", "-1", "30.1", "bad"])
async def test_invalid_budget_is_rejected_before_building_worker(
    configured: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DECISION_HUB_CANCELLED_SESSION_RECOVERY_TIME_BUDGET_SECONDS", configured)

    def forbidden_build(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Invalid configuration must precede worker construction")

    monkeypatch.setattr(main, "build_research_worker", forbidden_build)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'invalid-budget.sqlite3'}")
    try:
        with pytest.raises(ValueError, match="^cancelled_session_recovery_budget_invalid$"):
            await main._run_loop(
                database=database,
                registry=SourceRegistry(),
                settings=PilotSettings(),
                role="research",
                interval=1,
                once=False,
            )
        assert not any(
            task.get_name() == "research-cancel-recovery" for task in asyncio.all_tasks()
        )
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("configured", [None, "2"], ids=["default8", "explicit2"])
@pytest.mark.parametrize("once", [True, False], ids=["once", "background"])
async def test_actual_main_once_and_background_pass_same_parsed_budget(
    configured: str | None,
    once: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "DECISION_HUB_CANCELLED_SESSION_RECOVERY_TIME_BUDGET_SECONDS"
    if configured is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, configured)
    budget = 8.0 if configured is None else 2.0
    seen: list[tuple[int, float]] = []
    entered = asyncio.Event()
    closed = asyncio.Event()
    usage_closed = asyncio.Event()

    class ConfigurationRuntime:
        async def reconcile_cancelled_runs(
            self,
            *,
            limit: int = 4,
            time_budget_seconds: float = 2,
        ) -> dict[str, int]:
            seen.append((limit, time_budget_seconds))
            entered.set()
            return {"scanned": 0}

    async def claim_tick() -> None:
        if not once:
            await asyncio.Event().wait()
        return None

    async def close_owner() -> None:
        closed.set()

    async def held_usage() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            usage_closed.set()

    worker = SimpleNamespace(
        worker=SimpleNamespace(runtime=ConfigurationRuntime(), min_priority=None),
        tick=claim_tick,
        close=close_owner,
    )

    def selected_worker(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return worker

    def selected_usage(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(run_forever=held_usage)

    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    monkeypatch.setattr(main, "UsageCollector", selected_usage)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'main-budget.sqlite3'}")
    database.create_all()
    try:
        if once:
            result = await main._run_loop(
                database=database,
                registry=SourceRegistry(),
                settings=PilotSettings(),
                role="research",
                interval=1,
                once=True,
            )
            assert result == 0
        else:
            task = asyncio.create_task(
                main._run_loop(
                    database=database,
                    registry=SourceRegistry(),
                    settings=PilotSettings(),
                    role="research",
                    interval=1,
                    once=False,
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                assert not task.done()
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert usage_closed.is_set()
        assert seen == [(4, budget)] and closed.is_set()
        assert not any(
            task.get_name() == "research-cancel-recovery" for task in asyncio.all_tasks()
        )
    finally:
        database.engine.dispose()
