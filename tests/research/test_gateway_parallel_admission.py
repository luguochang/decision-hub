"""Two distinct real live admissions must not serialize full auth as writers."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Lock, get_ident

import pytest
from sqlalchemy import event

from packages.kernel.decision_hub_kernel.application import research_observability
from packages.contracts_py.decision_hub_contracts import ResearchRunCommand
from packages.kernel.decision_hub_kernel.application.research_evidence import ResearchCapabilityError
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    ResearchToolCallReservationRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import prepare_originals
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import CallbackSourceLease
from packages.workbench_adapters import durable_research_gateway as gateway_module
from tests.research.test_gateway_execution_v2 import (
    LiveAdapter,
    _gateway,
    _intent,
    forbid_external_connections,
    live_inputs,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert live_inputs is not None and forbid_external_connections is not None


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))


async def test_distinct_live_admissions_do_not_hold_writer_during_full_auth(
    live_inputs, tmp_path, monkeypatch,
):
    source = live_inputs
    source.prepare()
    loop = asyncio.get_running_loop()
    reached, release, once = asyncio.Event(), Event(), Lock()
    paused = False
    writer_threads = set()
    auth_observation = {}
    engine = source.database.engine

    def short_busy_timeout(connection, _record, _proxy):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=100")
        finally:
            cursor.close()

    def record_begin(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            writer_threads.add(get_ident())

    event.listen(engine, "checkout", short_busy_timeout)
    event.listen(engine, "before_cursor_execute", record_begin)
    original = research_observability.read_prepared_execution_in_session

    def held_actual_auth(session, *args, **kwargs):
        nonlocal paused
        with once:
            hold = not paused
            paused = True
        if hold:
            native = session.connection().connection.driver_connection
            auth_observation.update({
                "native_in_transaction": native.in_transaction,
                "begin_immediate_seen_on_owner": get_ident() in writer_threads,
            })
            loop.call_soon_threadsafe(reached.set)
            assert release.wait(15), "finite auth-owner watchdog expired"
        return original(session, *args, **kwargs)

    monkeypatch.setattr(
        research_observability, "read_prepared_execution_in_session", held_actual_auth,
    )
    adapter = LiveAdapter(source)
    gateway = _gateway(source, adapter)
    intent = _intent(source)
    first = asyncio.create_task(gateway.invoke_live(intent.model_copy(update={
        "request_id": "parallel-distinct-first",
    })))
    second = None
    second_outcome = None
    try:
        await asyncio.wait_for(reached.wait(), 10)
        second = asyncio.create_task(gateway.invoke_live(intent.model_copy(update={
            "request_id": "parallel-distinct-second",
        })))
        try:
            second_outcome = await asyncio.wait_for(asyncio.shield(second), 10)
        except Exception as error:
            second_outcome = error
    finally:
        release.set()
        tasks = [first] + ([second] if second is not None else [])
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        event.remove(engine, "checkout", short_busy_timeout)
        event.remove(engine, "before_cursor_execute", record_begin)

    errors = []
    for ordinal, value in enumerate(outcomes):
        if isinstance(value, BaseException):
            original_error = getattr(value, "orig", value)
            errors.append({
                "ordinal": ordinal,
                "type": type(value).__name__,
                "sqlite_errorcode": getattr(original_error, "sqlite_errorcode", None),
                "sqlite_errorname": getattr(original_error, "sqlite_errorname", None),
            })
    with source.database.session() as session:
        rows = session.query(ResearchToolCallReservationRecord).filter_by(
            run_id=source.request.run_id,
        ).all()
        timings = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == source.request.run_id,
            RunRoleOutputRecord.role.startswith("research_tool_time:"),
        ).count()
        observation = {
            "auth": auth_observation,
            "busy_timeout_ms": 100,
            "distinct_requests": True,
            "second_outcome_type": type(second_outcome).__name__,
            "errors": errors,
            "adapter_calls": len(adapter.calls),
            "reservations": sorted((row.request_id, row.status) for row in rows),
            "timing_count": timings,
        }
    (tmp_path / "parallel-admission-observation.json").write_text(
        json.dumps(observation, indent=2) + "\n", encoding="utf-8",
    )
    print("PARALLEL_ADMISSION_OBSERVATION=" + json.dumps(observation, sort_keys=True))
    problem = bool(errors)
    assert not problem, json.dumps(observation, sort_keys=True)
    assert len(adapter.calls) == 2 and len(rows) == 2 and timings == 2


async def test_only_exact_service_issued_admission_can_enter_writer(live_inputs, monkeypatch):
    source = live_inputs
    source.prepare()
    probes = []
    lease_events, leases = [], []
    extension_refused = True
    original_scope = gateway_module.callback_source_lease_scope
    original_extend = CallbackSourceLease.extend_authenticated_selection
    original_seal = CallbackSourceLease.seal_authenticated_selection

    @contextmanager
    def tracked_scope(checkpoint=None, **kwargs):
        assert kwargs["defer_unselected_recipe_archives"] is True
        lease_events.append("scope")
        try:
            with original_scope(checkpoint, **kwargs) as lease:
                leases.append(lease)
                yield lease
        finally:
            assert all(value is None or not value.active for value in leases)
            lease_events.append("closed")

    def tracked_extend(lease, session, run_id):
        native = session.connection().connection.driver_connection
        assert native.in_transaction
        assert lease._source is not None and not lease._sealed
        assert lease._extended_run_id is None
        lease_events.append("extend")
        if extension_refused:
            raise ValueError("controlled_selected_extension_refused")
        return original_extend(lease, session, run_id)

    def tracked_seal(lease, run_id):
        assert lease_events[-1] == "extend"
        assert lease._extended_run_id == run_id and not lease._sealed
        lease_events.append("seal")
        return original_seal(lease, run_id)

    monkeypatch.setattr(gateway_module, "callback_source_lease_scope", tracked_scope)
    monkeypatch.setattr(CallbackSourceLease, "extend_authenticated_selection", tracked_extend)
    monkeypatch.setattr(CallbackSourceLease, "seal_authenticated_selection", tracked_seal)

    class CheckedObserver(research_observability.ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs):
            issued = kwargs["admission_originals"]
            if os.name == "nt":
                assert lease_events[-1] == "seal" and leases[-1]._sealed
            # This is a genuine base issuer object, but it skipped this service's
            # mandatory prepared reader. It must not authenticate an admission.
            fabricated = prepare_originals(self.database, lambda _session: issued.value)
            alternate_service = research_observability.ResearchObservabilityService(
                self.database, clock=self.clock)
            cases = [
                ("caller_prepared_same_value", self, {"admission_originals": fabricated}),
                ("replaced_originals", self, {"admission_originals": replace(
                    issued, value=replace(issued.value))}),
                ("cross_run", self, {"run_id": kwargs["run_id"] + ":other"}),
                ("changed_query", self, {"query_hash": "f" * 64}),
                ("other_service", alternate_service, {}),
            ]
            for label, service, changes in cases:
                with pytest.raises(ValueError, match="(?:admission|preparation)_unissued"):
                    if service is self:
                        super().reserve_tool_call(**{**kwargs, **changes})
                    else:
                        service.reserve_tool_call(**{**kwargs, **changes})
                probes.append(label)
            link = issued.value.prepared.link
            original_hash = link.request_hash
            try:
                link.request_hash = "e" * 64
                with pytest.raises(ValueError, match="admission_unissued"):
                    super().reserve_tool_call(**kwargs)
                probes.append("mutated_nested_dto")
            finally:
                link.request_hash = original_hash
            return super().reserve_tool_call(**kwargs)

    observer = CheckedObserver(source.database, clock=lambda: source.now)
    adapter = LiveAdapter(source)
    gateway = _gateway(source, adapter, observer)
    if os.name == "nt":
        with pytest.raises(ResearchCapabilityError):
            await gateway.invoke_live(_intent(source).model_copy(update={
                "request_id": "selected-extension-denied",
            }))
        assert lease_events == ["scope", "extend", "closed"]
        assert not adapter.calls and not probes
    extension_refused = False
    await gateway.invoke_live(_intent(source))
    with source.database.session() as session:
        link = session.get(DshSessionLinkRecord, source.request.run_id)
        count = session.query(ResearchToolCallReservationRecord).filter_by(
            run_id=source.request.run_id).count()
    assert len(probes) == 6 and len(adapter.calls) == 1
    assert link.tool_calls_started == count == 1
    if os.name == "nt":
        assert lease_events == ["scope", "extend", "closed", "scope", "extend", "seal", "closed"]


@pytest.mark.parametrize("cut", ["accepted_before_writer", "cancel_before_commit"])
async def test_bound_cancellation_never_commits_new_admission(live_inputs, cut):
    source = live_inputs
    source.prepare()
    loop = asyncio.get_running_loop()
    reached, release = asyncio.Event(), Event()
    source_leases = []

    class CancelObserver(research_observability.ResearchObservabilityService):
        def prepare_tool_admission(self, **kwargs):
            source_leases.append(kwargs["source_lease"])
            return super().prepare_tool_admission(**kwargs)

        def reserve_tool_call(self, **kwargs):
            if cut == "accepted_before_writer":
                research_observability.ResearchCommandService(
                    self.database, clock=lambda: source.now,
                ).apply(source.request.run_id, ResearchRunCommand(
                    schema_version="research-run-command.v1", request_id="original-cancel",
                    command="cancel", reason="Controlled accepted cancellation before writer",
                ))
            return super().reserve_tool_call(**kwargs)

        def reserve_tool_call_in_session(self, session, **kwargs):
            result = super().reserve_tool_call_in_session(session, **kwargs)
            if cut == "cancel_before_commit":
                loop.call_soon_threadsafe(reached.set)
                assert release.wait(15), "finite writer-owner watchdog expired"
            return result

    observer = CancelObserver(source.database, clock=lambda: source.now)
    adapter = LiveAdapter(source)
    task = asyncio.create_task(_gateway(source, adapter, observer).invoke_live(_intent(source)))
    try:
        if cut == "cancel_before_commit":
            await asyncio.wait_for(reached.wait(), 30)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ResearchCapabilityError):
                await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    with source.database.session() as session:
        run = session.get(RunRecord, source.request.run_id)
        link = session.get(DshSessionLinkRecord, source.request.run_id)
        rows = session.query(ResearchToolCallReservationRecord).filter_by(
            run_id=source.request.run_id).count()
        timings = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == source.request.run_id,
            RunRoleOutputRecord.role.startswith("research_tool_time:"),
        ).count()
    assert not adapter.calls and rows == timings == link.tool_calls_started == 0
    if cut == "accepted_before_writer":
        assert run.status == "cancelled"
    assert source_leases and all(lease is None or not lease.active for lease in source_leases)
