import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import packages.runtime_adapters.dsh_runtime.web_runtime as web_runtime_module
from apps.hub_api.main import create_app
from apps.hub_worker.research import DurableResearchWorker, ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionStatus,
    DshSessionSubmit,
    ObservationCreate,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_policy import (
    render_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from packages.runtime_adapters.dsh_runtime.web_runtime import (
    DshWebResearchRuntime,
    _terminal_failure_error,
)
from tests.dsh_native.test_dsh_web_runtime import _database, _identity

PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _status() -> DshSessionStatus:
    return DshSessionStatus(
        schema_version="dsh-session-status.v1",
        run_id="run-catalog-terminal",
        dsh_session_id="dsh-catalog-terminal",
        state="failed",
        generation=1,
        last_seq=2,
        observed_at=datetime(2026, 10, 17, tzinfo=UTC),
        error_code="research_catalog_transport_timeout",
    )


def _completion(message: str) -> DshSessionCompletion:
    return DshSessionCompletion(
        schema_version="dsh-session-completion.v1",
        run_id="run-catalog-terminal",
        dsh_session_id="dsh-catalog-terminal",
        terminal_status="failed",
        generation=1,
        last_seq=2,
        trace_ref="dsh://sessions/dsh-catalog-terminal?last_seq=2",
        result_ref=None,
        result_hash=None,
        completed_at=datetime(2026, 10, 17, tzinfo=UTC),
        error=DshBridgeError(
            code="research_catalog_transport_timeout",
            message=message,
            retryable=True,
        ),
    )


def test_authenticated_catalog_terminal_retains_bottom_provenance() -> None:
    error = _terminal_failure_error(
        _status(),
        _completion(
            "Research capability catalog failed "
            "(research_catalog_transport_timeout; phase=fetch; deadline_ms=30000)"
        ),
    )

    assert error.error_code == "research_catalog_transport_timeout"
    assert error.origin == "transport"
    assert error.cause_code == "research_catalog_transport_timeout"
    assert error.deadline_ms == 30_000
    assert error.retryable is True
    assert error.provenance().model_dump(mode="json") == {
        "error_code": "research_catalog_transport_timeout",
        "origin": "transport",
        "cause_code": "research_catalog_transport_timeout",
        "capability_id": None,
        "tool_call_id": None,
        "retryable": True,
        "deadline_ms": 30_000,
        "provider_attempts": [],
    }
    assert "phase=fetch" in str(error)


def test_unrecognized_terminal_text_stays_redacted() -> None:
    error = _terminal_failure_error(
        _status(),
        _completion(
            "Research capability catalog failed "
            "(research_catalog_transport_timeout; phase=fetch; deadline_ms=30000) "
            "Authorization=fixture-secret"
        ),
    )

    assert error.error_code == "research_catalog_transport_timeout"
    assert error.origin == "dsh"
    assert error.cause_code == "failed"
    assert error.deadline_ms is None
    assert "fixture-secret" not in str(error)


@pytest.mark.asyncio
async def test_catalog_failure_reaches_product_run_through_worker_and_authenticated_api(
    tmp_path,
) -> None:
    clock = [datetime.now(UTC) - timedelta(seconds=5)]
    database = _database(tmp_path / "catalog-terminal.sqlite3")
    event_id, _, admitted = AdmissionService(database, clock=lambda: clock[0]).admit(
        ObservationCreate(
            text="Official inflation release triggered the bounded research run.",
            source_id="release32-terminal-gate",
            source_type=SourceType.transcript,
            observed_at=clock[0] - timedelta(seconds=2),
            published_at=clock[0] - timedelta(seconds=3),
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted
    runs = RunService(database, clock=lambda: clock[0])
    run_id, created = runs.create(event_id, strategy_version="research.v1")
    assert created
    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=("replay.research",),
        execution_mode="replay",
        clock=lambda: clock[0],
    ).build(event_id, run_id)
    ResearchTaskService(database, clock=lambda: clock[0]).freeze(request)
    request_hash = research_request_hash(request)
    submit = DshWebResearchRuntime._submission(request)
    session_id, request_id = submit.deterministic_session_id, submit.deterministic_request_id
    links = DshSessionLinkService(database, clock=lambda: clock[0])
    links.reserve(submit, _identity())
    pack = ResearchPackService(database).get(run_id)
    assert pack is not None
    instruction = render_research_policy(
        pack["definition"].get("research_policy"), request.allowed_capabilities
    )
    prompt = build_trusted_web_research_prompt(request)
    if instruction:
        prompt = instruction + "\n\n" + prompt
    links.store_prompt(DshSessionPrompt(
        schema_version="dsh-session-prompt.v1",
        run_id=run_id,
        dsh_session_id=session_id,
        request_id=request_id,
        request_hash=request_hash,
        generation=1,
        prompt=prompt,
    ), execution_request=request)
    links.accepted(DshSessionAccepted(
        schema_version="dsh-session-accepted.v1",
        run_id=run_id,
        dsh_session_id=session_id,
        accepted_at=clock[0] + timedelta(seconds=1),
        generation=1,
    ))
    clock[0] += timedelta(seconds=2)
    completion = _completion(
        "Research capability catalog failed "
        "(research_catalog_transport_timeout; phase=fetch; deadline_ms=30000)"
    ).model_copy(update={
        "run_id": run_id,
        "dsh_session_id": session_id,
        "trace_ref": f"dsh://sessions/{session_id}?last_seq=2",
        "completed_at": clock[0],
    })

    class Host:
        def __init__(self) -> None:
            self.readiness_calls = 0
            self.submit_calls = 0
            self.status_calls = 0
            self.result_calls = 0
            self.cancel_calls = 0

        async def readiness(self) -> DshHostReadiness:
            self.readiness_calls += 1
            return DshHostReadiness(
                schema_version="dsh-host-readiness.v1",
                ready=True,
                version_compatible=True,
                session_controller=True,
                client_plugin=True,
                hub_reachable=True,
                upstream_identity=_identity(),
                checked_at=clock[0],
                error_code=None,
            )

        async def submit(self, _payload: DshSessionSubmit) -> DshSessionAccepted:
            self.submit_calls += 1
            raise AssertionError("authenticated acceptance must prevent resubmission")

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            self.status_calls += 1
            return DshSessionStatus(
                schema_version="dsh-session-status.v1",
                run_id=payload.run_id,
                dsh_session_id=payload.deterministic_session_id,
                state="failed",
                generation=payload.generation,
                last_seq=2,
                observed_at=clock[0],
                error_code="research_catalog_transport_timeout",
            )

        async def result(self, _payload: DshSessionSubmit) -> Any:
            self.result_calls += 1
            raise AgentExecutionError(
                "host_terminal_result_unavailable",
                "No model/tool result exists for this pre-model failure",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="host_terminal_result_unavailable",
            )

        async def cancel(self, _payload: DshSessionSubmit, _reason: str) -> DshSessionStatus:
            self.cancel_calls += 1
            raise AssertionError("a current running owner must not cancel the terminal original")

        async def close(self) -> None:
            return None

    class RecordingRuntime(DshWebResearchRuntime):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.terminal_reader_calls = 0

        async def _wait_for_result(self, request, submit):
            self.terminal_reader_calls += 1
            return await super()._wait_for_result(request, submit)

    host = Host()
    runtime = RecordingRuntime(links, host, poll_interval_seconds=0.001)
    worker = DurableResearchWorker(
        database,
        runtime,
        pack_root=PACK_ROOT,
        worker_id="release32-terminal-worker",
        allowed_capabilities=("replay.research",),
        execution_mode="replay",
        terminal_projection_wait_seconds=1,
        clock=lambda: clock[0],
    )
    assert runs.claim_for_execution(
        run_id, worker_id=worker.worker_id, lease_seconds=60
    )

    app = create_app(
        database,
        source_connectors=[],
        dsh_callback_secret="catalog-terminal-secret",
    )
    app.state.dsh_sessions.clock = lambda: clock[0]
    try:
        with TestClient(app) as client:
            unauthorized = client.put(
                f"/v1/dsh/sessions/{run_id}/terminal",
                json=completion.model_dump(mode="json"),
            )
            assert unauthorized.status_code == 401
            with database.session() as session:
                assert session.query(RunRoleOutputRecord).filter_by(
                    run_id=run_id,
                    role="dsh_authenticated_terminal:1",
                ).count() == 0

            started = time.monotonic()
            authenticated = client.put(
                f"/v1/dsh/sessions/{run_id}/terminal",
                json=completion.model_dump(mode="json"),
                headers={"X-Decision-Hub-Bridge-Key": "catalog-terminal-secret"},
            )
            report = await worker._execute_claim(event_id, run_id, recovered=False)
            public_run = client.get(f"/v1/runs/{run_id}")
            public_detail = client.get(f"/v1/research/runs/{run_id}")
            public_timeline = client.get(f"/v1/runs/{run_id}/timeline")
            public_link = client.get(f"/v1/dsh/sessions/{run_id}")
            elapsed = time.monotonic() - started

            assert elapsed < 15
            assert authenticated.status_code == 200, authenticated.text
            assert report.status == "failed"
            assert report.error_code == "research_catalog_transport_timeout"
            assert public_run.status_code == 200, public_run.text
            assert public_detail.status_code == 200, public_detail.text
            assert public_timeline.status_code == 200, public_timeline.text
            assert public_link.status_code == 200, public_link.text
            assert public_run.json()["status"] == "failed"
            assert public_run.json()["error_code"] == "research_catalog_transport_timeout"
            detail = public_detail.json()
            assert detail["run"]["status"] == "failed"
            assert detail["run"]["failure"] == {
                "error_code": "research_catalog_transport_timeout",
                "origin": "transport",
                "cause_code": "research_catalog_transport_timeout",
                "capability_id": None,
                "tool_call_id": None,
                "retryable": True,
                "deadline_ms": 30_000,
                "provider_attempts": [],
            }
            stopped = [
                item for item in detail["trace"] if item["event_type"] == "session_stopped"
            ]
            assert len(stopped) == 1
            assert stopped[0]["error"] == detail["run"]["failure"]
            failed_events = [
                item for item in public_timeline.json()["items"]
                if item["event_type"] == "research.agentic.failed"
            ]
            assert len(failed_events) == 1
            assert authenticated.json()["state"] == "failed"
            assert authenticated.json()["error_code"] == "research_catalog_transport_timeout"
            assert public_link.json()["state"] == "failed"
            assert public_link.json()["error_code"] == "research_catalog_transport_timeout"

            first_host_counts = (
                host.readiness_calls,
                host.submit_calls,
                host.status_calls,
                host.result_calls,
                host.cancel_calls,
                runtime.terminal_reader_calls,
            )
            assert first_host_counts == (1, 0, 1, 0, 0, 1)
            assert await worker.tick() is None
            assert (
                host.readiness_calls,
                host.submit_calls,
                host.status_calls,
                host.result_calls,
                host.cancel_calls,
                runtime.terminal_reader_calls,
            ) == first_host_counts

        with database.session() as session:
            rows = session.query(RunRoleOutputRecord).filter_by(
                run_id=run_id, role="dsh_authenticated_terminal:1",
            ).all()
            assert len(rows) == 1
            row = rows[0]
            roles = tuple(session.scalars(select(RunRoleOutputRecord.role).where(
                RunRoleOutputRecord.run_id == run_id,
            )))
            verified = read_authenticated_terminal_in_session(
                session, run_id, now=clock[0] + timedelta(seconds=1),
            )
            assert row.schema_version == "dsh-host-terminal-receipt.v1"
            assert verified is not None and verified.completion == completion
            assert verified.completion.error is not None
            assert verified.completion.error.code == "research_catalog_transport_timeout"
            assert "phase=fetch" in verified.completion.error.message
            assert "deadline_ms=30000" in verified.completion.error.message
            assert "native_conversation_census:1" not in roles
            assert "research_model_dispatch" not in roles
            assert not any(role.startswith("research_child_model:") for role in roles)
            assert not any(
                role.startswith(("model_input:", "model_emit:", "model_finish:"))
                for role in roles
            )
    finally:
        await runtime.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_failed_status_uses_authenticated_original_without_resubmission() -> None:
    status = _status()
    completion = _completion(
        "Research capability catalog failed "
        "(research_catalog_transport_timeout; phase=fetch; deadline_ms=30000)"
    )

    class Client:
        def __init__(self) -> None:
            self.status_calls = 0
            self.result_calls = 0
            self.submit_calls = 0

        async def status(self, _submit):
            self.status_calls += 1
            return status

        async def result(self, _submit):
            self.result_calls += 1
            raise AgentExecutionError(
                "host_terminal_result_unavailable",
                "No provider/tool result exists for this pre-model failure",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="host_terminal_result_unavailable",
            )

    client = Client()
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.client = client
    runtime.poll_interval_seconds = 0.001

    async def retry(call, **_kwargs):
        return await call()

    async def terminal_original(current, _submit):
        return current, completion

    runtime._retry_host_call = retry
    runtime._terminal_original = terminal_original
    submit = SimpleNamespace(
        run_id=status.run_id,
        deterministic_session_id=status.dsh_session_id,
        generation=status.generation,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    request = SimpleNamespace(
        execution_budget=SimpleNamespace(total_deadline_seconds=30),
    )

    with pytest.raises(AgentExecutionError) as caught:
        await runtime._wait_for_result(request, submit)

    assert caught.value.error_code == "research_catalog_transport_timeout"
    assert caught.value.origin == "transport"
    assert caught.value.cause_code == "research_catalog_transport_timeout"
    assert caught.value.deadline_ms == 30_000
    assert client.status_calls == 1
    assert client.result_calls == 0
    assert client.submit_calls == 0


@pytest.mark.asyncio
async def test_authenticated_model_timeout_without_result_skips_remote_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_at = datetime(2026, 10, 17, 3, 11, 51, 326000, tzinfo=UTC)
    completion = _completion("The model step exceeded its configured deadline").model_copy(
        update={
            "last_seq": 16,
            "trace_ref": "dsh://sessions/dsh-catalog-terminal?last_seq=16",
            "completed_at": completed_at,
            "error": DshBridgeError(
                code="dsh_model_step_timeout",
                message="The model step exceeded its configured deadline",
                retryable=True,
            ),
        }
    )
    transport_status = _status().model_copy(
        update={
            "last_seq": 17,
            "observed_at": completed_at + timedelta(minutes=9),
            "error_code": "host_terminal_result_binding_mismatch",
        }
    )

    class Client:
        def __init__(self) -> None:
            self.status_calls = 0
            self.result_calls = 0

        async def status(self, _submit):
            self.status_calls += 1
            return transport_status

        async def result(self, _submit):
            self.result_calls += 1
            raise AssertionError("a result-free authenticated failure must not read /result")

    client = Client()
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.client = client
    runtime.poll_interval_seconds = 0.001
    runtime.proof_read_wait_seconds = 1
    runtime.terminal_reconciliation_seconds = 1

    async def retry(call, **_kwargs):
        return await call()

    async def read_original(_operation, **_kwargs):
        return SimpleNamespace(state="ready", value=completion, limitation=None)

    captured: list[tuple[DshSessionStatus, DshSessionCompletion | None]] = []
    real_terminal_failure_error = web_runtime_module._terminal_failure_error

    def terminal_failure_error(status, original):
        captured.append((status, original))
        return real_terminal_failure_error(status, original)

    runtime._retry_host_call = retry
    runtime._read_binding_with_contention_wait = read_original
    monkeypatch.setattr(
        web_runtime_module,
        "_terminal_failure_error",
        terminal_failure_error,
    )
    submit = SimpleNamespace(
        run_id=completion.run_id,
        deterministic_session_id=completion.dsh_session_id,
        generation=completion.generation,
        request_hash="a" * 64,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    request = SimpleNamespace(
        execution_budget=SimpleNamespace(total_deadline_seconds=30),
    )

    with pytest.raises(AgentExecutionError) as caught:
        await runtime._wait_for_result(request, submit)

    assert caught.value.error_code == "dsh_model_step_timeout"
    assert caught.value.origin == "dsh"
    assert client.status_calls == 1
    assert client.result_calls == 0
    assert len(captured) == 1
    confirmed, authenticated_original = captured[0]
    assert authenticated_original == completion
    assert (
        confirmed.run_id,
        confirmed.dsh_session_id,
        confirmed.generation,
        confirmed.last_seq,
        confirmed.observed_at,
        confirmed.error_code,
    ) == (
        completion.run_id,
        completion.dsh_session_id,
        completion.generation,
        completion.last_seq,
        completion.completed_at,
        "dsh_model_step_timeout",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_ref", "result_hash"),
    [
        ("dsh-host://runs/run-catalog-terminal/result", None),
        (None, "a" * 64),
    ],
)
async def test_partial_authenticated_result_binding_still_uses_host_authority(
    result_ref: str | None,
    result_hash: str | None,
) -> None:
    status = _status().model_copy(update={"error_code": "dsh_model_step_timeout"})
    completion = _completion("The model step exceeded its configured deadline").model_copy(
        update={
            "result_ref": result_ref,
            "result_hash": result_hash,
            "error": DshBridgeError(
                code="dsh_model_step_timeout",
                message="The model step exceeded its configured deadline",
                retryable=True,
            ),
        }
    )

    class Client:
        def __init__(self) -> None:
            self.result_calls = 0

        async def status(self, _submit):
            return status

        async def result(self, _submit):
            self.result_calls += 1
            raise AgentExecutionError(
                "host_terminal_result_binding_mismatch",
                "The terminal result binding is incomplete",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="terminal_result_binding_mismatch",
            )

    client = Client()
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.client = client
    runtime.poll_interval_seconds = 0.001

    async def retry(call, **_kwargs):
        return await call()

    async def terminal_original(current, _submit):
        return current, completion

    runtime._retry_host_call = retry
    runtime._terminal_original = terminal_original
    submit = SimpleNamespace(
        run_id=status.run_id,
        deterministic_session_id=status.dsh_session_id,
        generation=status.generation,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    request = SimpleNamespace(
        execution_budget=SimpleNamespace(total_deadline_seconds=30),
    )

    with pytest.raises(AgentExecutionError) as caught:
        await runtime._wait_for_result(request, submit)

    assert caught.value.error_code == "host_terminal_result_binding_mismatch"
    assert caught.value.cause_code == "terminal_result_binding_mismatch"
    assert client.result_calls == 1


@pytest.mark.asyncio
async def test_missing_authenticated_original_keeps_remote_unavailable_fallback() -> None:
    status = _status().model_copy(update={"error_code": "dsh_model_step_timeout"})

    class Client:
        def __init__(self) -> None:
            self.result_calls = 0

        async def status(self, _submit):
            return status

        async def result(self, _submit):
            self.result_calls += 1
            raise AgentExecutionError(
                "host_terminal_result_unavailable",
                "No terminal result exists",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="host_terminal_result_unavailable",
            )

    client = Client()
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.client = client
    runtime.poll_interval_seconds = 0.001
    completed: list[DshSessionCompletion] = []

    async def retry(call, **_kwargs):
        return await call()

    async def terminal_original(current, _submit):
        return current, None

    async def complete_empty(current, _submit, completion):
        completed.append(completion)
        return current, None

    runtime._retry_host_call = retry
    runtime._terminal_original = terminal_original
    runtime._complete_empty_terminal = complete_empty
    submit = SimpleNamespace(
        run_id=status.run_id,
        deterministic_session_id=status.dsh_session_id,
        generation=status.generation,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    request = SimpleNamespace(
        execution_budget=SimpleNamespace(total_deadline_seconds=30),
    )

    with pytest.raises(AgentExecutionError) as caught:
        await runtime._wait_for_result(request, submit)

    assert caught.value.error_code == "dsh_model_step_timeout"
    assert client.result_calls == 1
    assert len(completed) == 1
    assert completed[0].result_ref is None
    assert completed[0].result_hash is None


@pytest.mark.asyncio
async def test_authenticated_cancelled_null_result_keeps_cancellation_provenance() -> None:
    observed_at = datetime(2026, 10, 17, 5, 30, tzinfo=UTC)
    status = _status().model_copy(
        update={
            "state": "cancelled",
            "last_seq": 17,
            "observed_at": observed_at,
            "error_code": "dsh_user_cancelled",
        }
    )
    original = _completion("ignored for a cancelled terminal").model_copy(
        update={
            "terminal_status": "cancelled",
            "last_seq": 17,
            "completed_at": observed_at,
            "error": None,
            "result_ref": None,
            "result_hash": None,
        }
    )

    class Client:
        def __init__(self) -> None:
            self.result_calls = 0

        async def status(self, _submit):
            return status

        async def result(self, _submit):
            self.result_calls += 1
            raise AgentExecutionError(
                "host_terminal_result_unavailable",
                "No terminal result exists",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="host_terminal_result_unavailable",
            )

    client = Client()
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.client = client
    runtime.poll_interval_seconds = 0.001
    runtime.terminal_reconciliation_seconds = 1.0

    async def retry(call, **_kwargs):
        return await call()

    async def terminal_original(current, _submit):
        return current, original

    async def task_binding(*_args, **_kwargs):
        return None

    runtime._retry_host_call = retry
    runtime._terminal_original = terminal_original
    runtime._assert_task_binding = task_binding
    submit = SimpleNamespace(
        run_id=status.run_id,
        deterministic_session_id=status.dsh_session_id,
        generation=status.generation,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    request = SimpleNamespace(
        execution_budget=SimpleNamespace(total_deadline_seconds=30),
    )

    with pytest.raises(AgentExecutionError) as caught:
        await runtime._wait_for_result(request, submit)

    assert caught.value.error_code == "dsh_user_cancelled"
    assert caught.value.cause_code == "cancelled"
    assert caught.value.retryable is False
    assert client.result_calls == 1
