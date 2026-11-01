"""Actual SQLite/legacy runtime boundaries before v2 execution binding is wired."""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    ObservationCreate,
    ResearchSessionRequest,
    ResearchTraceEvent,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    NOW,
    FakeHostClient,
    _database,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)


class CountingHost(FakeHostClient):
    """Controlled transport; counts real product calls, never network or models."""

    def __init__(self) -> None:
        super().__init__()
        self.readiness_calls = 0
        self.status_calls = 0
        self.result_calls = 0

    async def readiness(self):
        self.readiness_calls += 1
        return await super().readiness()

    async def status(self, payload: DshSessionSubmit):
        self.status_calls += 1
        return await super().status(payload)

    async def result(self, payload: DshSessionSubmit):
        self.result_calls += 1
        return await super().result(payload)

    def counts(self) -> dict[str, int]:
        return {"readiness": self.readiness_calls, "submit": len(self.submit_calls),
                "status": self.status_calls, "result": self.result_calls,
                "cancel": len(self.cancelled)}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows asyncio creates its local waker through socketpair(). Permit only
    # the synchronous, same-thread construction of that internal socket pair.
    state = threading.local()
    original_pair = socket.socketpair
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def pair(*args: Any, **kwargs: Any) -> Any:
        state.local_pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            state.local_pair = False

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("B02 must not connect to network")

    def connect(sock: socket.socket, address: Any) -> Any:
        if getattr(state, "local_pair", False):
            return original_connect(sock, address)
        return denied()

    def connect_ex(sock: socket.socket, address: Any) -> Any:
        if getattr(state, "local_pair", False):
            return original_connect_ex(sock, address)
        return denied()

    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


def original_task(
    database: Database, *, case: str = "original",
) -> tuple[str, ResearchSessionRequest, ResearchSessionRequest]:
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text=f"The official event confirms elevated inflation risks. {case}",
            source_id=f"hm04-b02-input-{case}", source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2), published_at=NOW - timedelta(seconds=3),
            language="en", event_hint="central_bank_speech",
        )
    )
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version=f"research.v1.{case}")
    assert created
    snapshot_id, _ = SnapshotService(database).freeze_for_run(run_id, event_id)
    rows = database.get_snapshot_evidence(snapshot_id)
    assert len(rows) == 1
    raw = rows[0]
    base = _request(run_id)
    evidence = base.input_evidence[0].model_copy(update={
        "evidence_id": raw["evidence_id"], "source_id": raw["source_id"],
        "published_at": as_utc(datetime.fromisoformat(str(raw["published_at"]))),
        "observed_at": as_utc(datetime.fromisoformat(str(raw["observed_at"]))),
        "received_at": as_utc(datetime.fromisoformat(str(raw["received_at"]))),
        "content_hash": raw["content_hash"], "excerpt": raw["text"],
    })
    first = base.model_copy(update={
        "event_id": event_id, "trigger_snapshot_id": snapshot_id,
        "input_evidence": [evidence], "evidence_refs": [raw["evidence_id"]],
    })
    hidden = first.evidence_requirements[0].model_copy(update={
        "requirement_id": "policy_or_data_delta",
        "description": "Original full Task hidden requirement",
    })
    full = first.model_copy(update={
        "evidence_requirements": [*first.evidence_requirements, hidden],
    })
    full = ResearchSessionRequest.model_validate(full.model_dump(mode="json"))
    first = ResearchSessionRequest.model_validate(first.model_dump(mode="json"))
    ResearchTaskService(database).freeze(full)
    return run_id, full, first


def saved_prompt_hash(database: Database, run_id: str) -> str:
    with database.session() as session:
        row = session.get(DshSessionPromptRecord, (run_id, 1))
        assert row is not None
        return hashlib.sha256(row.prompt.encode("utf-8")).hexdigest()


def save_observation(directory: Path, value: dict[str, Any]) -> None:
    with (directory / "observation.json").open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
    print("B02_OBSERVATION=" + json.dumps(value, ensure_ascii=False, sort_keys=True))


async def test_legacy_filtered_first_request_has_a_real_full_task_positive_control(tmp_path: Path):
    database = _database(tmp_path / "positive.sqlite3")
    host = CountingHost()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(database), host, poll_interval_seconds=0.001,
    )
    try:
        run_id, full, first = original_task(database)
        await runtime.execute(first)
        first_method = ResearchMethodService(database).get(run_id)
        first_prompt = saved_prompt_hash(database, run_id)
        await runtime.execute(first)
        assert ResearchTaskService(database).get(run_id) == full
        assert len(host.submit_calls) == 2
        assert ResearchMethodService(database).get(run_id) == first_method
        assert saved_prompt_hash(database, run_id) == first_prompt
        save_observation(tmp_path, {
            "case": "legacy_full_task_filtered_positive", "run_id": run_id,
            "full_task_hash": recipe_hash(full.model_dump(mode="json")),
            "first_execution_full_hash": recipe_hash(first.model_dump(mode="json")),
            "link_request_hash": host.submit_calls[0].request_hash,
            "full_task_requirement_count": len(full.evidence_requirements),
            "first_requirement_count": len(first.evidence_requirements), "calls": host.counts(),
            "method_preserved": True, "prompt_preserved": True,
            "method_full_binding_verified": False,
        })
    finally:
        await runtime.close()
        database.engine.dispose()


def resign_hidden_requirement(database: Database, run_id: str) -> None:
    """Actual independent SQLite connection; original timestamps stay intact."""
    path = database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT output_id, payload_json FROM run_role_outputs WHERE run_id=? AND role=?",
            (run_id, "research_task_request"),
        ).fetchone()
        assert row is not None
        body = json.loads(row[1])
        body["request"]["evidence_requirements"][1]["description"] = "Changed during operation"
        body["request_hash"] = recipe_hash(body["request"])
        encoded = canonical_recipe(body)
        connection.execute(
            "UPDATE run_role_outputs SET payload_json=?, payload_hash=? WHERE output_id=?",
            (encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), row[0]),
        )


@pytest.mark.parametrize("phase", ["readiness", "submit", "status", "result"])
async def test_mid_execution_drift_stops_the_next_call_and_retains_real_remote_return(
    tmp_path: Path, phase: str,
):
    database = _database(tmp_path / "inflight.sqlite3")
    run_id, _full, first = original_task(database)

    class InflightHost(CountingHost):
        def damage(self, operation: str) -> None:
            if phase == operation:
                resign_hidden_requirement(database, run_id)

        async def readiness(self):
            result = await super().readiness()
            self.damage("readiness")
            return result

        async def submit(self, payload: DshSessionSubmit):
            result = await super().submit(payload)
            self.damage("submit")
            return result

        async def status(self, payload: DshSessionSubmit):
            result = await super().status(payload)
            self.damage("status")
            return result

        async def result(self, payload: DshSessionSubmit):
            result = await super().result(payload)
            self.damage("result")
            return result

    host = InflightHost()
    links = DshSessionLinkService(database)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(first)
        assert error.value.error_code == "research_method_binding_failed"
        expected = {
            "readiness": {"readiness": 1, "submit": 0, "status": 0, "result": 0, "cancel": 0},
            "submit": {"readiness": 1, "submit": 1, "status": 0, "result": 0, "cancel": 0},
            "status": {"readiness": 1, "submit": 1, "status": 1, "result": 0, "cancel": 0},
            "result": {"readiness": 1, "submit": 1, "status": 2, "result": 1, "cancel": 0},
        }
        assert host.counts() == expected[phase]
        linked = links.get(run_id)
        if phase == "result":
            # Remote completion is an actual observation, not a valid report.
            assert linked is not None and linked.state == "completed"
            assert linked.result_hash and linked.result_ref
        save_observation(tmp_path, {
            "case": f"inflight_{phase}", "calls": host.counts(),
            "error_code": error.value.error_code, "cause_code": error.value.cause_code,
            "semantic_research_result_returned": False,
            "raw_host_completion_retained": bool(linked and linked.result_hash),
        })
    finally:
        await runtime.close()
        database.engine.dispose()


async def test_task_changed_during_transient_retry_stops_the_second_submit(tmp_path: Path):
    database = _database(tmp_path / "retry.sqlite3")
    run_id, _full, first = original_task(database)

    class RetryHost(CountingHost):
        async def submit(self, payload: DshSessionSubmit):
            await super().submit(payload)
            resign_hidden_requirement(database, run_id)
            raise AgentExecutionError(
                "dsh_host_unavailable", "Controlled temporary transport failure",
                retryable=True, provider_id="dsh-web", origin="dsh", cause_code="http_503",
            )

    host = RetryHost()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                    poll_interval_seconds=0.001)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(first)
        assert error.value.error_code == "research_method_binding_failed"
        assert host.counts() == {
            "readiness": 1, "submit": 1, "status": 0, "result": 0, "cancel": 0,
        }
        save_observation(tmp_path, {
            "case": "transient_retry_drift", "calls": host.counts(),
            "error_code": error.value.error_code,
        })
    finally:
        await runtime.close()
        database.engine.dispose()


async def test_concurrent_runs_do_not_replace_each_others_original_task_anchor(tmp_path: Path):
    database = _database(tmp_path / "concurrent.sqlite3")
    changed_run, _, changed = original_task(database)
    intact_run, _, intact = original_task(database, case="intact")
    # Distinct observations and strategy identities create two real Runs.
    assert changed_run != intact_run

    class ConcurrentHost(CountingHost):
        def __init__(self):
            super().__init__()
            self.arrivals = 0
            self.both_ready = asyncio.Event()
            self.status_run_ids: list[str] = []

        async def submit(self, payload: DshSessionSubmit):
            accepted = await super().submit(payload)
            self.arrivals += 1
            if self.arrivals == 2:
                resign_hidden_requirement(database, changed_run)
                self.both_ready.set()
            await self.both_ready.wait()
            return accepted

        async def status(self, payload: DshSessionSubmit):
            self.status_run_ids.append(payload.run_id)
            return await super().status(payload)

    host = ConcurrentHost()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                    poll_interval_seconds=0.001)
    try:
        async with asyncio.timeout(5):
            damaged_result, intact_result = await asyncio.gather(
                runtime.execute(changed), runtime.execute(intact), return_exceptions=True,
            )
        assert isinstance(damaged_result, AgentExecutionError)
        assert damaged_result.error_code == "research_method_binding_failed"
        assert not isinstance(intact_result, BaseException)
        assert host.status_run_ids and set(host.status_run_ids) == {intact_run}
        save_observation(tmp_path, {
            "case": "concurrent_anchor_isolation", "calls": host.counts(),
            "changed_run_blocked": True, "intact_run_returned": True,
            "status_run_ids": host.status_run_ids,
        })
    finally:
        await runtime.close()
        database.engine.dispose()


async def test_task_drift_during_trace_await_cannot_return_a_product_report(tmp_path: Path):
    database = _database(tmp_path / "trace.sqlite3")
    run_id, _, first = original_task(database)

    class TraceHost(CountingHost):
        async def result(self, payload: DshSessionSubmit):
            result = await super().result(payload)
            events = json.dumps([{
                "method": "session.status",
                "payload": {"status": "idle", "sessionId": payload.deterministic_session_id},
            }])
            return result.model_copy(update={
                "events_json": events,
                "result_hash": hashlib.sha256(
                    (result.final_response + "\0" + events).encode("utf-8"),
                ).hexdigest(),
            })

    class DriftSink:
        calls = 0

        async def emit(self, event: ResearchTraceEvent) -> None:
            self.calls += 1
            resign_hidden_requirement(database, run_id)
            await asyncio.sleep(0)

    host, sink = TraceHost(), DriftSink()
    links = DshSessionLinkService(database)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(first, sink)
        assert error.value.error_code == "research_method_binding_failed"
        assert sink.calls > 0
        linked = links.get(run_id)
        assert linked is not None and linked.state == "completed" and linked.result_hash
        save_observation(tmp_path, {
            "case": "trace_await_drift", "calls": host.counts(),
            "trace_sink_calls": sink.calls, "raw_host_completion_retained": True,
            "semantic_research_result_returned": False, "error_code": error.value.error_code,
        })
    finally:
        await runtime.close()
        database.engine.dispose()


async def test_missing_configured_database_cannot_call_host_or_create_a_replacement(tmp_path: Path):
    path = tmp_path / "temporarily-unavailable.sqlite3"
    backup = tmp_path / "original-database-held.sqlite3"
    database = _database(path)
    _, _, first = original_task(database)
    host = CountingHost()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                    poll_interval_seconds=0.001)
    database.engine.dispose()
    assert path.resolve().is_relative_to(tmp_path.resolve()) and not backup.exists()
    path.rename(backup)
    original_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    try:
        async with asyncio.timeout(2):
            with pytest.raises(AgentExecutionError) as error:
                await runtime.execute(first)
        assert error.value.error_code == "research_method_binding_failed"
        assert error.value.retryable
        assert host.counts() == {"readiness": 0, "submit": 0, "status": 0, "result": 0, "cancel": 0}
        assert not path.exists()
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == original_hash
        save_observation(tmp_path, {
            "case": "configured_database_missing", "calls": host.counts(),
            "error_code": error.value.error_code, "cause_code": error.value.cause_code,
            "replacement_database_created": False, "original_bytes_preserved": True,
        })
    finally:
        await runtime.close()
        backup.rename(path)
        database.engine.dispose()


async def test_valid_binding_read_crossing_deadline_cannot_start_a_host_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path / "binding-deadline.sqlite3")
    _, _, first = original_task(database)
    # The caller may impose a shorter deadline; it never enlarges the Task.
    first = first.model_copy(update={"deadline_at": datetime.now(first.deadline_at.tzinfo)
                                   + timedelta(seconds=0.075)})
    host = CountingHost()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                    poll_interval_seconds=0.001)
    original_read = runtime.cancellation_projector.read
    reads = 0

    async def held_read(
        operation: Callable[[Session], Any], *, timeout_seconds: float = 0.25,
    ) -> Any:
        nonlocal reads
        reads += 1
        result = await original_read(operation, timeout_seconds=timeout_seconds)
        if reads == 2:
            # Actual valid SQL read; hold only its delivery across the deadline.
            delay = (first.deadline_at - datetime.now(first.deadline_at.tzinfo)).total_seconds()
            await asyncio.sleep(max(0, delay) + 0.02)
        return result

    monkeypatch.setattr(runtime.cancellation_projector, "read", held_read)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(first)
        save_observation(tmp_path, {
            "case": "valid_binding_read_crossed_deadline", "actual_sql_reads": reads,
            "calls": host.counts(), "error_code": error.value.error_code,
            "cause_code": error.value.cause_code, "held_delivery_fixture": True,
        })
        assert error.value.error_code == "provider_timeout"
        assert reads >= 2
        assert host.counts() == {"readiness": 0, "submit": 0, "status": 0, "result": 0, "cancel": 0}
    finally:
        await runtime.close()
        database.engine.dispose()


@pytest.mark.parametrize("damage", [
    "resigned_hidden_requirement", "deleted_task", "deleted_execution",
])
async def test_full_task_drift_must_stop_extra_host_calls_even_when_filtered_request_is_unchanged(
    tmp_path: Path, damage: str,
):
    database = _database(tmp_path / "negative.sqlite3")
    host = CountingHost()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(database), host, poll_interval_seconds=0.001,
    )
    try:
        run_id, full, first = original_task(database)
        await runtime.execute(first)
        before_calls = host.counts()
        before_method = ResearchMethodService(database).get(run_id)
        before_prompt = saved_prompt_hash(database, run_id)
        original_hash = recipe_hash(full.model_dump(mode="json"))
        changed_hash = None
        with database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=run_id, role=(
                    "dsh_execution_request:1" if damage == "deleted_execution"
                    else "research_task_request"
                ),
            ).one()
            if damage in {"deleted_task", "deleted_execution"}:
                session.delete(row)
            else:
                body = json.loads(row.payload_json)
                body["request"]["evidence_requirements"][1]["description"] = "Changed hidden item"
                changed_hash = recipe_hash(body["request"])
                body["request_hash"] = changed_hash
                row.payload_json = canonical_recipe(body)
                row.payload_hash = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
        error_code = None
        try:
            await runtime.execute(first)
        except AgentExecutionError as exc:
            error_code = exc.error_code
        after_calls = host.counts()
        extra = {key: after_calls[key] - before_calls[key] for key in before_calls}
        after_method = ResearchMethodService(database).get(run_id)
        after_prompt = saved_prompt_hash(database, run_id)
        save_observation(tmp_path, {
            "case": damage, "run_id": run_id, "original_full_task_hash": original_hash,
            "changed_full_task_hash": changed_hash, "filtered_request_unchanged": True,
            "before_calls": before_calls, "after_calls": after_calls, "extra_calls": extra,
            "error_code": error_code, "method_preserved": after_method == before_method,
            "prompt_preserved": after_prompt == before_prompt,
            "first_execution_retained": len(host.submit_calls) >= 1,
        })
        assert error_code is not None, f"full Task drift accepted; actual extra RPC={extra}"
        assert all(value == 0 for value in extra.values()), extra
        assert after_method == before_method and after_prompt == before_prompt
    finally:
        await runtime.close()
        database.engine.dispose()
