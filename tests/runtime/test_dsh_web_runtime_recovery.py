# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
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
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import (
    DshHostClient,
    DshWebResearchRuntime,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _submit(*, deadline_at: datetime | None = None) -> DshSessionSubmit:
    return DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id="run-web-recovery",
        request_hash="a" * 64,
        deterministic_session_id="dsh-web-recovery",
        deterministic_request_id="request-web-recovery",
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
        dsh_session_id="dsh-web-recovery",
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
        dsh_session_id="dsh-web-recovery",
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
        return None


class _Links:
    def __init__(self) -> None:
        self.observed: list[DshSessionStatus] = []
        self.completed: list[DshSessionCompletion] = []

    def observe(self, status: DshSessionStatus) -> object:
        self.observed.append(status)
        return object()

    def complete(self, completion: DshSessionCompletion) -> object:
        self.completed.append(completion)
        return object()


def _request() -> ResearchSessionRequest:
    # _wait_for_result only consults the budget for the pre-call deadline
    # error; the durable deadline comes from DshSessionSubmit.
    return cast(ResearchSessionRequest, object())


@pytest.mark.asyncio
async def test_status_transport_error_retries_until_host_completed() -> None:
    client = _HostSequence([_retryable_transport_error(), _status("completed")], [_result()])
    links = _Links()
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, links),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
    )

    result = await runtime._wait_for_result(_request(), _submit())

    assert result.finish_reason == "completed"
    assert client.status_calls == 2
    assert client.result_calls == 1
    assert len(links.completed) == 1
    assert links.completed[0].terminal_status == "completed"


@pytest.mark.asyncio
async def test_readiness_retries_only_transient_hub_unreachable_state() -> None:
    client = _HostSequence(
        [],
        [],
        readinesses=[
            _readiness(False, error_code="host_hub_unreachable"),
            _readiness(True),
        ],
    )
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, _Links()),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
        readiness_grace_seconds=0.1,
    )

    report = await runtime._wait_for_readiness(
        deadline_at=datetime.now(UTC) + timedelta(seconds=1)
    )

    assert report.ready is True
    assert client.readiness_calls == 2


@pytest.mark.asyncio
async def test_readiness_does_not_retry_version_or_configuration_failure() -> None:
    client = _HostSequence(
        [],
        [],
        readinesses=[_readiness(False, error_code="host_version_incompatible")],
    )
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, _Links()),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_readiness(
            deadline_at=datetime.now(UTC) + timedelta(seconds=1)
        )

    assert raised.value.error_code == "host_version_incompatible"
    assert raised.value.retryable is False
    assert client.readiness_calls == 1


@pytest.mark.asyncio
async def test_result_transport_error_retries_after_host_completed() -> None:
    client = _HostSequence(
        [_status("completed")], [_retryable_transport_error(), _result()]
    )
    links = _Links()
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, links),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
    )

    result = await runtime._wait_for_result(_request(), _submit())

    assert result.run_id == "run-web-recovery"
    assert client.status_calls == 1
    assert client.result_calls == 2
    assert links.completed[0].result_hash == result.result_hash


@pytest.mark.asyncio
async def test_non_retryable_host_error_is_not_retried() -> None:
    error = AgentExecutionError(
        "dsh_host_auth_failed",
        "bridge authentication failed",
        provider_id="dsh-web",
        origin="configuration",
        cause_code="http_401",
    )
    client = _HostSequence([error], [])
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, _Links()),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_result(_request(), _submit())

    assert raised.value.error_code == "dsh_host_auth_failed"
    assert raised.value.retryable is False
    assert client.status_calls == 1


@pytest.mark.asyncio
async def test_retry_deadline_preserves_last_transport_provenance() -> None:
    client = _HostSequence([_retryable_transport_error()] * 10, [])
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, _Links()),
        cast(DshHostClient, client),
        poll_interval_seconds=0.002,
    )
    submit = _submit(deadline_at=datetime.now(UTC) + timedelta(seconds=0.015))

    with pytest.raises(AgentExecutionError) as raised:
        await runtime._wait_for_result(_request(), submit)

    assert raised.value.error_code == "dsh_host_unavailable"
    assert raised.value.origin == "transport"
    assert raised.value.cause_code == "remoteprotocolerror"
    assert raised.value.retryable is True
    assert raised.value.attempt >= 2
    assert client.status_calls < 10


@pytest.mark.asyncio
async def test_cancelled_status_poll_propagates_without_retry() -> None:
    async def cancelled(payload: DshSessionSubmit) -> DshSessionStatus:
        del payload
        raise asyncio.CancelledError

    client = _HostSequence([], [])
    client.status = cancelled  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(
        cast(DshSessionLinkService, _Links()),
        cast(DshHostClient, client),
        poll_interval_seconds=0.001,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime._wait_for_result(_request(), _submit())
