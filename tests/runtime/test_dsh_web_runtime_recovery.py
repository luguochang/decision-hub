# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshRunSessionLinkView,
    DshSessionCompletion,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import (
    DshHostClient,
    DshWebResearchRuntime,
)
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    RuntimeTaskAnchor,
    read_runtime_task_anchor,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SESSION_ID, REQUEST_ID = DshSessionLinkService.deterministic_ids(
    "run-web-recovery", "a" * 64,
)


def _submit(*, deadline_at: datetime | None = None) -> DshSessionSubmit:
    return DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id="run-web-recovery",
        request_hash="a" * 64,
        deterministic_session_id=SESSION_ID,
        deterministic_request_id=REQUEST_ID,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref="hub://runs/run-web-recovery/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(seconds=2),
        model_step_timeout_ms=60_000,
        max_tool_calls=12,
        generation=1,
    )


def _status(state: str, *, error_code: str | None = None) -> DshSessionStatus:
    return DshSessionStatus(
        schema_version="dsh-session-status.v1",
        run_id="run-web-recovery",
        dsh_session_id=SESSION_ID,
        state=state,  # type: ignore[arg-type]
        generation=1,
        last_seq=3,
        observed_at=datetime.now(UTC),
        error_code=error_code,
    )


def _result() -> DshSessionResult:
    final_response = "{}"
    events_json = "[]"
    result_hash = hashlib.sha256(
        (final_response + "\0" + events_json).encode("utf-8")
    ).hexdigest()
    finished_at = datetime.now(UTC)
    return DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id="run-web-recovery",
        dsh_session_id=SESSION_ID,
        generation=1,
        last_seq=3,
        final_response=final_response,
        finish_reason="completed",
        events_json=events_json,
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
        trace_ref="dsh-host://runs/run-web-recovery/trace",
        result_hash=result_hash,
    )


def _retryable_transport_error() -> AgentExecutionError:
    return AgentExecutionError(
        "dsh_host_unavailable",
        "temporary Host transport failure",
        retryable=True,
        provider_id="dsh-web",
        origin="transport",
        cause_code="remoteprotocolerror",
    )


def _readiness(
    ready: bool, *, error_code: str | None = None
) -> DshHostReadiness:
    return DshHostReadiness(
        schema_version="dsh-host-readiness.v1",
        ready=ready,
        version_compatible=True,
        session_controller=True,
        client_plugin=True,
        hub_reachable=ready,
        upstream_identity=DshUpstreamIdentity(
            source_commit="0" * 40,
            source_version="test",
            package_versions={"dsh": "test"},
            plugin_build_hash="0" * 64,
        ),
        checked_at=datetime.now(UTC),
        error_code=error_code,
    )


class _HostSequence:
    def __init__(
        self,
        statuses: list[DshSessionStatus | AgentExecutionError],
        results: list[DshSessionResult | AgentExecutionError],
        *,
        readinesses: list[DshHostReadiness | AgentExecutionError] | None = None,
    ) -> None:
        self.statuses = list(statuses)
        self.results = list(results)
        self.readinesses = list(readinesses or [])
        self.readiness_calls = 0
        self.status_calls = 0
        self.result_calls = 0
        self.cancel_calls = 0
        self.closed = False

    async def readiness(self) -> DshHostReadiness:
        self.readiness_calls += 1
        if not self.readinesses:
            raise AssertionError("unexpected readiness call")
        item = self.readinesses.pop(0)
        if isinstance(item, AgentExecutionError):
            raise item
        return item

    async def submit(self, payload: DshSessionSubmit) -> object:
        raise AssertionError("submit is not part of _wait_for_result tests")

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        del payload
        self.status_calls += 1
        if not self.statuses:
            raise AssertionError("unexpected status call")
        item = self.statuses.pop(0)
        if isinstance(item, AgentExecutionError):
            raise item
        return item

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        del payload
        self.result_calls += 1
        if not self.results:
            raise AssertionError("unexpected result call")
        item = self.results.pop(0)
        if isinstance(item, AgentExecutionError):
            raise item
        return item

    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        del payload, reason
        self.cancel_calls += 1
        return _status("cancelled")

    async def close(self) -> None:
        self.closed = True


class _RecordingLinks(DshSessionLinkService):
    """Record successful completions while preserving the real SQLite boundary."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.completed: list[DshSessionCompletion] = []

    def complete(
        self, callback: DshSessionCompletion, *, require_cancelled: bool = False,
        expected_request_hash: str | None = None,
        binding_validator: Callable[[Session], None] | None = None,
    ) -> DshRunSessionLinkView:
        view = super().complete(
            callback, require_cancelled=require_cancelled,
            expected_request_hash=expected_request_hash, binding_validator=binding_validator,
        )
        self.completed.append(callback)
        return view


class _TransportBoundRuntime(DshWebResearchRuntime):
    """Private transport probes own a real SQL anchor in the calling coroutine."""

    active_anchor_contexts = 0
    transport_sql_anchor: RuntimeTaskAnchor
    binding_durations: list[float]

    async def _assert_task_binding(self, *, allow_cancelled: bool = False) -> None:
        started = time.perf_counter()
        try:
            await super()._assert_task_binding(allow_cancelled=allow_cancelled)
        finally:
            self.binding_durations.append(time.perf_counter() - started)

    @contextmanager
    def transport_anchor(self) -> Generator[None]:
        token = self._task_anchor.set(self.transport_sql_anchor)
        self.active_anchor_contexts += 1
        try:
            yield
        finally:
            self._task_anchor.reset(token)
            self.active_anchor_contexts -= 1

    async def _wait_for_result(
        self, request: ResearchSessionRequest, submit: DshSessionSubmit,
    ) -> DshSessionResult:
        with self.transport_anchor():
            return await super()._wait_for_result(request, submit)

    async def _wait_for_readiness(self, *, deadline_at: datetime) -> DshHostReadiness:
        with self.transport_anchor():
            return await super()._wait_for_readiness(deadline_at=deadline_at)


class _RuntimeFactory:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.runtimes: list[_TransportBoundRuntime] = []
        self.clients: list[_HostSequence] = []

    def create(
        self, client: _HostSequence, *, poll_interval_seconds: float = 0.001,
        readiness_grace_seconds: float = 10,
    ) -> tuple[DshWebResearchRuntime, _RecordingLinks]:
        links = _RecordingLinks(self.database)
        links.reserve(_submit(), _readiness(True).upstream_identity)
        runtime = _TransportBoundRuntime(
            links, cast(DshHostClient, client), poll_interval_seconds=poll_interval_seconds,
            readiness_grace_seconds=readiness_grace_seconds,
        )
        runtime.binding_durations = []
        # Establish ownership before a test starts its deliberately short
        # transport deadline. Every production RPC guard still re-reads SQL.
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            runtime.transport_sql_anchor = read_runtime_task_anchor(session, "run-web-recovery")
        self.runtimes.append(runtime)
        self.clients.append(client)
        return runtime, links


@pytest.fixture
async def runtime_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_RuntimeFactory]:
    # The helpers exercise Host transport only. Seed a real correlation boundary,
    # without submitting research, storing a Task, or declaring a business success.
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "runtime"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'transport.sqlite3'}")
    factory = _RuntimeFactory(database)
    live_connections: set[int] = set()

    @event.listens_for(database.engine, "connect")
    def record_connect(connection: object, _record: object) -> None:
        live_connections.add(id(connection))

    @event.listens_for(database.engine, "close")
    def record_close(connection: object, _record: object) -> None:
        live_connections.discard(id(connection))

    try:
        database.create_all()
        with database.session() as session:
            session.add(RunRecord(
                run_id="run-web-recovery", event_id="event-web-recovery", status="admitted",
                created_at=NOW, updated_at=NOW,
            ))
        yield factory
    finally:
        try:
            await asyncio.gather(*(runtime.close() for runtime in factory.runtimes))
        finally:
            database.engine.dispose()
        cleanup = {
            "runtimes_closed": len(factory.runtimes),
            "host_clients_closed": sum(client.closed for client in factory.clients),
            "sqlite_connections_remaining": len(live_connections),
            "anchor_contexts_remaining": sum(runtime.active_anchor_contexts
                                             for runtime in factory.runtimes),
            "actual_binding_read_seconds": [runtime.binding_durations
                                            for runtime in factory.runtimes],
        }
        (tmp_path / "cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
        assert all(client.closed for client in factory.clients)
        assert not live_connections
        assert all(runtime.active_anchor_contexts == 0 for runtime in factory.runtimes)


def _request() -> ResearchSessionRequest:
    # _wait_for_result only consults the budget for the pre-call deadline
    # error; the durable deadline comes from DshSessionSubmit.
    return cast(ResearchSessionRequest, object())


@pytest.mark.asyncio
async def test_status_transport_error_retries_until_host_completed(
    runtime_factory: _RuntimeFactory,
) -> None:
    client = _HostSequence([_retryable_transport_error(), _status("completed")], [_result()])
    runtime, links = runtime_factory.create(client)

    result = await runtime._wait_for_result(_request(), _submit())

    assert result.finish_reason == "completed"
    assert client.status_calls == 2
    assert client.result_calls == 1
    assert len(links.completed) == 1
    assert links.completed[0].terminal_status == "completed"
    view = links.get("run-web-recovery")
    assert view is not None and view.state == "completed"
    run = runtime_factory.database.get_run_record("run-web-recovery")
    assert run is not None and run.status == "admitted"


@pytest.mark.asyncio
async def test_readiness_retries_only_transient_hub_unreachable_state(
    runtime_factory: _RuntimeFactory,
) -> None:
    client = _HostSequence(
        [],
        [],
        readinesses=[
            _readiness(False, error_code="host_hub_unreachable"),
            _readiness(True),
        ],
    )
    runtime, _links = runtime_factory.create(
        client,
        readiness_grace_seconds=0.1,
    )

    report = await runtime._wait_for_readiness(
        deadline_at=datetime.now(UTC) + timedelta(seconds=1)
    )

    assert report.ready is True
    assert client.readiness_calls == 2


@pytest.mark.asyncio
async def test_readiness_does_not_retry_version_or_configuration_failure(
    runtime_factory: _RuntimeFactory,
) -> None:
    client = _HostSequence(
        [],
        [],
        readinesses=[_readiness(False, error_code="host_version_incompatible")],
    )
    runtime, _links = runtime_factory.create(client)

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_readiness(
            deadline_at=datetime.now(UTC) + timedelta(seconds=1)
        )

    assert raised.value.error_code == "host_version_incompatible"
    assert raised.value.retryable is False
    assert client.readiness_calls == 1


@pytest.mark.asyncio
async def test_result_transport_error_retries_after_host_completed(
    runtime_factory: _RuntimeFactory,
) -> None:
    client = _HostSequence(
        [_status("completed")], [_retryable_transport_error(), _result()]
    )
    runtime, links = runtime_factory.create(client)

    result = await runtime._wait_for_result(_request(), _submit())

    assert result.run_id == "run-web-recovery"
    assert client.status_calls == 1
    assert client.result_calls == 2
    assert links.completed[0].result_hash == result.result_hash


@pytest.mark.asyncio
async def test_non_retryable_host_error_is_not_retried(
    runtime_factory: _RuntimeFactory,
) -> None:
    error = AgentExecutionError(
        "dsh_host_auth_failed",
        "bridge authentication failed",
        provider_id="dsh-web",
        origin="configuration",
        cause_code="http_401",
    )
    client = _HostSequence([error], [])
    runtime, _links = runtime_factory.create(client)

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_result(_request(), _submit())

    assert raised.value.error_code == "dsh_host_auth_failed"
    assert raised.value.retryable is False
    assert client.status_calls == 1


@pytest.mark.asyncio
async def test_retry_deadline_preserves_last_transport_provenance(
    runtime_factory: _RuntimeFactory,
) -> None:
    client = _HostSequence([_retryable_transport_error()] * 10, [])
    runtime, _links = runtime_factory.create(
        client,
        poll_interval_seconds=0.020,
    )
    # The original 15ms fixture cannot include real SQL proof plus this
    # Windows loop's 15.625ms clock resolution. Fix the caller's initial
    # transport-test budget once; production never extends that deadline.
    submit = _submit(deadline_at=datetime.now(UTC) + timedelta(seconds=0.250))

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_result(_request(), submit)

    assert raised.value.error_code == "dsh_host_unavailable"
    assert raised.value.origin == "transport"
    assert raised.value.cause_code == "remoteprotocolerror"
    assert raised.value.retryable is True
    assert raised.value.attempt >= 2
    assert client.status_calls < 10


@pytest.mark.asyncio
async def test_cancelled_status_poll_propagates_without_retry(
    runtime_factory: _RuntimeFactory,
) -> None:
    async def cancelled(payload: DshSessionSubmit) -> DshSessionStatus:
        del payload
        raise asyncio.CancelledError

    client = _HostSequence([], [])
    client.status = cancelled  # type: ignore[method-assign]
    runtime, _links = runtime_factory.create(client)

    with pytest.raises(asyncio.CancelledError):
        await runtime._wait_for_result(_request(), _submit())


def test_large_native_result_preserves_complete_export_and_identity_guards() -> None:
    import json

    from pydantic import ValidationError
    from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run, _validate_result

    proof = "原始证据" * 2_100_000
    notifications = [{"method": "session.event", "payload": {
        "sessionId": _submit().deterministic_session_id, "event": {
            "seq": 3, "type": "tool/result", "data": {"proof": proof},
        },
    }}]
    events_json = json.dumps(notifications, ensure_ascii=False, separators=(",", ":"))
    assert len(events_json) > 8_388_608
    payload = _result().model_dump(mode="json")
    payload["events_json"] = events_json
    payload["result_hash"] = hashlib.sha256((str(payload["final_response"]) + "\0" + events_json).encode()).hexdigest()
    result = DshSessionResult.model_validate_json(json.dumps(payload, ensure_ascii=False))
    _validate_result(result, _submit())
    sdk = _sdk_run(result)
    assert result.events_json == events_json
    assert sdk.notifications[0].payload == notifications[0]["payload"]
    assert len(sdk.notifications[0].payload["event"]["data"]["proof"]) == len(proof)
    for invalid in (None, 1, [], "", "x"):
        with pytest.raises(ValidationError):
            DshSessionResult.model_validate({**payload, "events_json": invalid})
    with pytest.raises(ValidationError):
        DshSessionResult.model_validate({**payload, "run_id": ""})
    foreign = DshSessionResult.model_validate({**payload, "run_id": "foreign-run"})
    with pytest.raises(AgentExecutionError, match="identity"):
        _validate_result(foreign, _submit())
    changed = DshSessionResult.model_validate({**payload, "result_hash": "b" * 64})
    with pytest.raises(AgentExecutionError, match="hash"):
        _validate_result(changed, _submit())
