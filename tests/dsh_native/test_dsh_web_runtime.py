from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    EvidenceRequirement,
    ExecutionBudget,
    ResearchCapabilityResult,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_host_client import (
    DshWebHostClient,
    DshWebHostConfig,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime

NOW = datetime(2026, 8, 31, 3, 0, tzinfo=UTC)


def _database(path: Path) -> Database:
    db = Database(f"sqlite+pysqlite:///{path}")
    db.create_all()
    return db


def _identity() -> DshUpstreamIdentity:
    return DshUpstreamIdentity(
        source_commit="c" * 40,
        source_version="0.1.2-alpha.2",
        package_versions={"@deepseek-ai/dsh": "0.1.2-alpha.2"},
        plugin_build_hash="b" * 64,
    )


def _request(run_id: str, *, deadline_seconds: float = 30) -> ResearchSessionRequest:
    now = datetime.now(UTC)
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id=f"research-request:{run_id}",
        run_id=run_id,
        event_id="event-web-host",
        trigger_snapshot_id="snapshot-web-host",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="replay",
        pit_cutoff_at=NOW,
        current_round=1,
        evidence_refs=["evidence-input"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="evidence-input",
                kind="transcript",
                authority="unverified",
                source_id="manual",
                source_url=None,
                published_at=NOW - timedelta(minutes=1),
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
                content_hash="0" * 64,
                excerpt="The official discussed inflation risks.",
            )
        ],
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="event_identity",
                description="Verify the event identity.",
                importance="hard",
                source_priority=["official"],
                authority_floor="official",
                preferred_capabilities=["replay.research"],
                freshness_seconds=3600,
                minimum_independent_sources=1,
                allowed_fallbacks=["replay.research"],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=2,
            max_tool_calls=6,
            max_subagents=2,
            total_deadline_seconds=30,
            per_tool_timeout_seconds=5,
            per_model_step_timeout_seconds=10,
            max_structured_repairs=0,
            max_estimated_cost_usd=0.1,
        ),
        deadline_at=now + timedelta(seconds=deadline_seconds),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


class FakeHostClient:
    def __init__(self) -> None:
        self.submit_calls: list[DshSessionSubmit] = []
        self.cancelled: list[str] = []
        self._polls = 0

    async def readiness(self) -> DshHostReadiness:
        return DshHostReadiness(
            schema_version="dsh-host-readiness.v1",
            ready=True,
            version_compatible=True,
            session_controller=True,
            client_plugin=True,
            hub_reachable=True,
            upstream_identity=_identity(),
            checked_at=NOW,
            error_code=None,
        )

    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
        self.submit_calls.append(payload)
        return DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            accepted_at=NOW,
            generation=payload.generation,
        )

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        self._polls += 1
        return self.status_value(
            payload, "completed" if self._polls > 1 else "running", self._polls + 1
        )

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        final_response = json.dumps(
            {
                "schema_version": "research-synthesis-candidate.v1",
                "request_id": f"research-request:{payload.run_id}",
                "causal_case": None,
                "horizons": [],
            }
        )
        events_json = "[]"
        result_hash = hashlib.sha256(
            (final_response + "\0" + events_json).encode("utf-8")
        ).hexdigest()
        return DshSessionResult(
            schema_version="dsh-session-result.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            generation=payload.generation,
            last_seq=3,
            final_response=final_response,
            finish_reason="completed",
            events_json=events_json,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
            trace_ref=f"dsh://sessions/{payload.deterministic_session_id}/trace",
            result_hash=result_hash,
        )

    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        del reason
        self.cancelled.append(payload.run_id)
        return self.status_value(payload, "cancelled", 4)

    async def close(self) -> None:
        return None

    @staticmethod
    def status_value(payload: DshSessionSubmit, state: str, sequence: int) -> DshSessionStatus:
        return DshSessionStatus.model_validate(
            {
                "schema_version": "dsh-session-status.v1",
                "run_id": payload.run_id,
                "dsh_session_id": payload.deterministic_session_id,
                "state": state,
                "generation": payload.generation,
                "last_seq": sequence,
                "observed_at": NOW + timedelta(seconds=sequence),
                "error_code": None,
            }
        )


class RecordingTraceSink:
    def __init__(self) -> None:
        self.events: list[ResearchTraceEvent] = []

    async def emit(self, event: ResearchTraceEvent) -> None:
        self.events.append(event)


def _record_product_cancellation(db: Database, run_id: str) -> None:
    """Adapter fixture authority; command/API cancellation has separate real tests."""
    with db.session() as session:
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.status = "cancelled"
        row.lease_owner = None
        row.lease_expires_at = None


@pytest.mark.parametrize(
    "phase", ["readiness", "submit", "status", "repair_submit", "repair_status", "trace"]
)
async def test_cancellation_covers_initial_and_repair_submission(
    tmp_path: Path, phase: str,
) -> None:
    """Real durable links with a controlled Host; no paid provider execution."""
    db = _database(tmp_path / "cancel.sqlite3")
    run_id, _ = RunService(db).create("event-cancel-host")
    entered = asyncio.Event()
    cancellations: list[DshSessionSubmit] = []

    class HeldHost(FakeHostClient):
        async def hold(self) -> None:
            entered.set()
            await asyncio.Event().wait()

        async def readiness(self) -> DshHostReadiness:
            if phase == "readiness":
                await self.hold()
            return await super().readiness()

        async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
            if phase == "submit" or (phase == "repair_submit" and payload.generation == 2):
                # The controller may have accepted it before the response is lost.
                self.submit_calls.append(payload)
                await self.hold()
            return await super().submit(payload)

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            if phase == "status" or (phase == "repair_status" and payload.generation == 2):
                await self.hold()
            return await super().status(payload)

        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            return _completed_result_with_repairable_synthesis(payload)

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            assert reason == "product_run_cancelled"
            cancellations.append(payload)
            return await super().cancel(payload, reason)

    host = HeldHost()

    class HeldTraceSink(RecordingTraceSink):
        async def emit(self, event: ResearchTraceEvent) -> None:
            if phase == "trace":
                await host.hold()
            await super().emit(event)

    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    request = _request(run_id)
    request = request.model_copy(update={
        "execution_budget": request.execution_budget.model_copy(
            update={"max_structured_repairs": 1}
        ),
        "evidence_requirements": [
            request.evidence_requirements[0].model_copy(update={"importance": "soft"})
        ],
    })
    operation = asyncio.create_task(runtime.execute(request, HeldTraceSink()))
    await asyncio.wait_for(entered.wait(), timeout=2)
    _record_product_cancellation(db, run_id)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    if phase == "readiness":
        assert cancellations == []
        assert links.get(run_id) is None
    else:
        assert len(cancellations) == 1
        assert cancellations[0] == host.submit_calls[-1]
        assert cancellations[0].generation == (
            2 if phase in {"repair_submit", "repair_status", "trace"} else 1
        )
        link = links.get(run_id)
        # Trace delivery follows a completed Host turn. Its historical terminal
        # record must not be rewritten by subsequent product cancellation.
        assert link is not None and link.state == ("completed" if phase == "trace" else "cancelled")


@pytest.mark.parametrize("host_cancel", ["unavailable", "hang"])
async def test_cancel_host_await_is_bounded_and_does_not_claim_host_success(
    tmp_path: Path, host_cancel: str,
) -> None:
    db = _database(tmp_path / "cancel-unconfirmed.sqlite3")
    run_id, _ = RunService(db).create("event-cancel-unconfirmed")
    entered = asyncio.Event()
    attempts: list[DshSessionSubmit] = []

    class UnconfirmedHost(FakeHostClient):
        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("held polling must be cancelled")

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            attempts.append(payload)
            if host_cancel == "unavailable":
                raise RuntimeError("controlled_host_cancel_unavailable")
            await asyncio.Event().wait()
            raise AssertionError("host cleanup must time out")

    host = UnconfirmedHost()
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    operation = asyncio.create_task(runtime.execute(_request(run_id)))
    await asyncio.wait_for(entered.wait(), timeout=2)
    _record_product_cancellation(db, run_id)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=6)
    assert len(attempts) == 1
    link = links.get(run_id)
    assert link is not None and link.state != "cancelled"


async def test_reclaimed_runtime_shutdown_does_not_cancel_new_owner_session(tmp_path: Path) -> None:
    db = _database(tmp_path / "reclaimed-shutdown.sqlite3")
    clock = [datetime.now(UTC)]
    runs = RunService(db, clock=lambda: clock[0])
    run_id, _ = runs.create("event-reclaimed-shutdown")
    assert runs.claim_for_execution(run_id, worker_id="old-owner", lease_seconds=1)
    entered = asyncio.Event()
    new_entered = asyncio.Event()
    release = asyncio.Event()

    class SharedHost(FakeHostClient):
        status_calls = 0

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            self.status_calls += 1
            if self.status_calls == 1:
                entered.set()
            else:
                new_entered.set()
            await release.wait()
            return self.status_value(payload, "completed", 3)

    host = SharedHost()
    links = DshSessionLinkService(db)
    old_runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    new_runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    request = _request(run_id)
    old_operation = asyncio.create_task(old_runtime.execute(request))
    new_operation: asyncio.Task[ResearchSessionResult] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        clock[0] += timedelta(seconds=2)
        assert runs.claim_for_execution(run_id, worker_id="new-owner", lease_seconds=30)
        new_operation = asyncio.create_task(new_runtime.execute(request))
        await asyncio.wait_for(new_entered.wait(), timeout=2)
        assert len(host.submit_calls) == 1
        old_operation.cancel()  # External shutdown, not an authorized product cancellation.
        with pytest.raises(asyncio.CancelledError):
            await old_operation
        assert host.cancelled == []
        current = db.get_run_record(run_id)
        assert current is not None and current.status == "running"
        assert current.lease_owner == "new-owner"
        release.set()
        result = await asyncio.wait_for(new_operation, timeout=2)
        # This Host fixture has no trusted evidence: it completes the session
        # but is correctly degraded at the research mapping boundary.
        assert result.research_session_id == host.submit_calls[0].deterministic_session_id
        link = links.get(run_id)
        assert link is not None and link.state == "completed"
    finally:
        for operation in (old_operation, new_operation):
            if operation is not None and not operation.done():
                operation.cancel()
        await asyncio.gather(
            *(item for item in (old_operation, new_operation) if item is not None),
            return_exceptions=True,
        )


@pytest.mark.parametrize("wrong_identity", ["run_id", "dsh_session_id", "generation"])
async def test_cancel_response_identity_mismatch_does_not_confirm_link(
    tmp_path: Path, wrong_identity: str,
) -> None:
    db = _database(tmp_path / "cancel-wrong-identity.sqlite3")
    run_id, _ = RunService(db).create("event-cancel-wrong-identity")
    entered = asyncio.Event()

    class WrongIdentityHost(FakeHostClient):
        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("held polling must be cancelled")

        async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
            matching = await super().cancel(payload, reason)
            values = matching.model_dump(mode="json")
            values[wrong_identity] = (
                payload.generation + 1 if wrong_identity == "generation" else "other"
            )
            return DshSessionStatus.model_validate(values)

    host = WrongIdentityHost()
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    operation = asyncio.create_task(runtime.execute(_request(run_id)))
    await asyncio.wait_for(entered.wait(), timeout=2)
    before = links.get(run_id)
    assert before is not None
    _record_product_cancellation(db, run_id)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert host.cancelled == [run_id]
    link = links.get(run_id)
    assert link == before


def _failed_result_with_attested_evidence(
    payload: DshSessionSubmit,
) -> DshSessionResult:
    evidence = EvidenceCandidate.model_validate(
        {
            "evidence_id": "ev-partial-provider-failure",
            "requirement_id": "event_identity",
            "kind": "official",
            "authority": "official",
            "source_id": "federalreserve.gov",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/example.htm",
            "published_at": (NOW - timedelta(minutes=2)).isoformat(),
            "observed_at": (NOW - timedelta(seconds=2)).isoformat(),
            "received_at": (NOW - timedelta(seconds=1)).isoformat(),
            "content_hash": "9" * 64,
            "excerpt": "Official event identity retained before provider failure.",
            "structured_payload_ref": None,
            "tool_call_id": "call-partial-1",
            "research_session_id": payload.deterministic_session_id,
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )
    capability = ResearchCapabilityResult(
        schema_version="research-capability-result.v1",
        request_id=f"research-request:{payload.run_id}",
        capability_id="replay.research",
        provider="fixture-replay",
        evidence_candidates=[evidence],
        cost_usd=0.0,
        completed_at=NOW,
    )
    notifications = [
        {
            "method": "session.event",
            "payload": {
                "sessionId": payload.deterministic_session_id,
                "event": {
                    "type": "tool/call",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-call",
                                    "toolCallId": "call-partial-1",
                                    "toolName": "decision_hub_research",
                                }
                            ]
                        }
                    },
                    "timestamp": NOW.isoformat(),
                },
            },
        },
        {
            "method": "session.event",
            "payload": {
                "sessionId": payload.deterministic_session_id,
                "event": {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-result",
                                    "toolCallId": "call-partial-1",
                                    "structuredContent": capability.model_dump(mode="json"),
                                    "content": [],
                                    "isError": False,
                                }
                            ]
                        }
                    },
                    "timestamp": NOW.isoformat(),
                },
            },
        },
    ]
    events_json = json.dumps(notifications)
    final_response = json.dumps(
        {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": f"research-request:{payload.run_id}",
            "causal_case": None,
            "horizons": [
                {
                    "horizon": "30m",
                    "action": "long",
                    "subjective_probability": 0.9,
                    "probability_status": "uncalibrated",
                    "evidence_refs": [evidence.evidence_id],
                    "trigger": "Interrupted output must not be published.",
                    "invalidation": "Provider failure.",
                    "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
                    "next_review_at": (NOW + timedelta(minutes=10)).isoformat(),
                    "missing_facts": [],
                    "confidence_cap_reason": None,
                }
            ],
        }
    )
    return DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id=payload.run_id,
        dsh_session_id=payload.deterministic_session_id,
        generation=payload.generation,
        last_seq=5,
        final_response=final_response,
        finish_reason="error",
        events_json=events_json,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        trace_ref=f"dsh://sessions/{payload.deterministic_session_id}/trace",
        result_hash=hashlib.sha256(
            (final_response + "\0" + events_json).encode("utf-8")
        ).hexdigest(),
    )


def _completed_result_with_repairable_synthesis(
    payload: DshSessionSubmit,
) -> DshSessionResult:
    base = _failed_result_with_attested_evidence(payload)
    notifications = json.loads(base.events_json)
    for sequence, notification in enumerate(notifications, start=1):
        notification["payload"]["event"]["seq"] = sequence
    evidence_ref = (
        "ev-partial-provider-failur" if payload.generation == 1 else "ev-partial-provider-failure"
    )
    synthesis = {
        "schema_version": "research-synthesis-candidate.v1",
        "request_id": f"research-request:{payload.run_id}",
        "causal_case": None,
        "horizons": [
            {
                "horizon": "30m",
                "action": "no_trade",
                "subjective_probability": 0.5,
                "probability_status": "uncalibrated",
                "evidence_refs": [evidence_ref],
                "trigger": "Wait for confirmation.",
                "invalidation": "New evidence invalidates the draft.",
                "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
                "next_review_at": (NOW + timedelta(minutes=10)).isoformat(),
                "missing_facts": [],
                "confidence_cap_reason": None,
            }
        ],
    }
    if payload.generation == 1:
        final_response = json.dumps(synthesis)
        last_seq = 2
    else:
        notifications.extend(
            [
                {
                    "method": "session.event",
                    "payload": {
                        "sessionId": payload.deterministic_session_id,
                        "event": {
                            "seq": 3,
                            "type": "tool/call",
                            "data": {
                                "message": {
                                    "content": [
                                        {
                                            "type": "tool-call",
                                            "toolCallId": "call-synthesis-repair",
                                            "toolName": "decision_hub_synthesis_submit",
                                        }
                                    ]
                                }
                            },
                            "timestamp": NOW.isoformat(),
                        },
                    },
                },
                {
                    "method": "session.event",
                    "payload": {
                        "sessionId": payload.deterministic_session_id,
                        "event": {
                            "seq": 4,
                            "type": "tool/result",
                            "data": {
                                "message": {
                                    "content": [
                                        {
                                            "type": "tool-result",
                                            "toolCallId": "call-synthesis-repair",
                                            "structuredContent": synthesis,
                                            "isError": False,
                                        }
                                    ]
                                }
                            },
                            "timestamp": NOW.isoformat(),
                        },
                    },
                },
            ]
        )
        final_response = ""
        last_seq = 4
    events_json = json.dumps(notifications)
    return base.model_copy(
        update={
            "generation": payload.generation,
            "last_seq": last_seq,
            "final_response": final_response,
            "finish_reason": "completed",
            "events_json": events_json,
            "result_hash": hashlib.sha256(
                (final_response + "\0" + events_json).encode("utf-8")
            ).hexdigest(),
        }
    )


async def test_http_client_uses_host_auth_and_never_leaks_callback_key() -> None:
    seen_headers: list[tuple[str | None, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(
            (
                request.headers.get("X-Decision-Hub-Host-Key"),
                request.headers.get("X-Decision-Hub-Bridge-Key"),
            )
        )
        return httpx.Response(
            200,
            json={
                "schema_version": "dsh-host-readiness.v1",
                "ready": True,
                "version_compatible": True,
                "session_controller": True,
                "client_plugin": True,
                "hub_reachable": True,
                "upstream_identity": _identity().model_dump(mode="json"),
                "checked_at": NOW.isoformat(),
                "error_code": None,
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = DshWebHostClient(
        DshWebHostConfig(base_url="http://dsh.local", bridge_key="host-secret"),
        client=http,
    )

    report = await client.readiness()
    await client.close()

    assert report.ready is True
    assert seen_headers == [("host-secret", None)]


async def test_http_client_session_operation_uses_host_inspection_envelope() -> None:
    seen_timeout: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_timeout.append(float(request.extensions["timeout"]["read"]))
        raise httpx.ReadTimeout("controlled session inspection timeout", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = DshWebHostConfig(
        base_url="http://dsh.local",
        bridge_key="host-secret",
        request_timeout_seconds=0.5,
        operation_timeout_seconds=35,
    )
    client = DshWebHostClient(config, client=http)
    payload = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id="run-timeout-envelope",
        request_hash="0" * 64,
        deterministic_session_id="dsh-timeout-envelope",
        deterministic_request_id="request-timeout-envelope",
        workspace_ref="decision-hub://workspace/default",
        prompt_ref="hub://runs/run-timeout-envelope/prompts/1",
        agent_preset=None,
        permission_ref="decision-hub://permissions/research",
        deadline_at=NOW + timedelta(minutes=10),
        model_step_timeout_ms=600_000,
        max_tool_calls=6,
        generation=1,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await client.status(payload)
    await client.close()

    assert seen_timeout == [35.0]
    assert raised.value.error_code == "provider_timeout"
    assert raised.value.cause_code == "dsh_host_timeout"
    assert raised.value.deadline_ms == 35_000


async def test_http_client_preserves_canonical_not_ready_payload_on_503() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "schema_version": "dsh-host-readiness.v1",
                "ready": False,
                "version_compatible": False,
                "session_controller": True,
                "client_plugin": True,
                "hub_reachable": True,
                "upstream_identity": _identity().model_dump(mode="json"),
                "checked_at": NOW.isoformat(),
                "error_code": "host_version_incompatible",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DshWebHostClient(
        DshWebHostConfig(base_url="http://dsh.local", bridge_key="host-secret"),
        client=http,
    )

    report = await client.readiness()
    await client.close()

    assert report.ready is False
    assert report.version_compatible is False
    assert report.error_code == "host_version_incompatible"


@pytest.mark.parametrize(
    ("status_code", "host_code"),
    [
        (404, "host_route_not_found"),
        (404, "host_run_not_found"),
        (410, "dsh_native_session_lost"),
    ],
)
async def test_http_client_preserves_exact_canonical_host_error(
    status_code: int, host_code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={
                "error": {
                    "code": host_code,
                    "message": host_code,
                    "retryable": False,
                }
            },
        )

    payload = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id="run-host-error",
        request_hash="0" * 64,
        deterministic_session_id="dsh-host-error",
        deterministic_request_id="request-host-error",
        workspace_ref="decision-hub://workspace/default",
        prompt_ref="hub://runs/run-host-error/prompts/1",
        agent_preset=None,
        permission_ref="decision-hub://permissions/research",
        deadline_at=NOW + timedelta(minutes=10),
        model_step_timeout_ms=600_000,
        max_tool_calls=6,
        generation=1,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DshWebHostClient(
        DshWebHostConfig(base_url="http://dsh.local", bridge_key="host-secret"),
        client=http,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await client.status(payload)
    await client.close()

    assert raised.value.error_code == host_code
    assert raised.value.cause_code == host_code
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    "stage_code",
    ["host_submit_link_fetch", "host_submit_original_prompt_fetch"],
)
async def test_http_client_preserves_new_submit_read_stage(
    stage_code: str,
) -> None:
    payload = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id="run-submit-read-error",
        request_hash="1" * 64,
        deterministic_session_id="dsh-submit-read-error",
        deterministic_request_id="request-submit-read-error",
        workspace_ref="decision-hub://workspace/default",
        prompt_ref="hub://runs/run-submit-read-error/prompts/1",
        agent_preset=None,
        permission_ref="decision-hub://permissions/research",
        deadline_at=NOW + timedelta(minutes=10),
        model_step_timeout_ms=600_000,
        max_tool_calls=6,
        generation=1,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "error": {
                    "code": "dsh_host_read_cancelled",
                    "message": "dsh_host_read_cancelled",
                    "retryable": False,
                    "submit": {
                        "schema_version": "dsh-submit-error.v1",
                        "stage_code": stage_code,
                        "run_id": payload.run_id,
                        "dsh_session_id": payload.deterministic_session_id,
                        "generation": payload.generation,
                        "request_hash": payload.request_hash,
                        "request_id": payload.deterministic_request_id,
                    },
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DshWebHostClient(
        DshWebHostConfig(base_url="http://dsh.local", bridge_key="host-secret"),
        client=http,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await client.submit(payload)
    await client.close()

    assert raised.value.error_code == "dsh_host_read_cancelled"
    assert raised.value.cause_code == "dsh_host_read_cancelled"
    assert raised.value.retryable is False
    assert f"stage={stage_code}" in str(raised.value)


async def test_web_runtime_reserves_one_session_reconciles_and_maps_result(tmp_path: Path) -> None:
    db = _database(tmp_path / "web-runtime.sqlite3")
    run_id, _ = RunService(db).create("event-web-host")
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
    )

    first = await runtime.execute(_request(run_id))
    second = await runtime.execute(_request(run_id))
    link = DshSessionLinkService(db).get(run_id)
    prompt = DshSessionLinkService(db).get_prompt(run_id)

    assert first.research_session_id == second.research_session_id
    # Once this exact tuple has durable acceptance, subsequent executions
    # reconcile it through GET status/result and never replay PUT submit.
    assert len(host.submit_calls) == 1
    assert link is not None and link.state == "completed"
    assert link.result_hash is not None
    assert prompt is not None and prompt.run_id == run_id
    assert prompt.prompt
    assert host.submit_calls[0].max_tool_calls == 6
    assert link.max_tool_calls == 6


async def test_web_runtime_uses_durable_execution_count_not_dsh_attempt_count(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-tool-ledger.sqlite3")
    run_id, _ = RunService(db).create("event-web-tool-ledger")

    class LedgerHost(FakeHostClient):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            with db.session() as session:
                link = session.get(DshSessionLinkRecord, payload.run_id)
                assert link is not None
                link.tool_calls_started = 2
            result = await super().result(payload)
            attempted_events = [
                {
                    "method": "session.event",
                    "payload": {
                        "sessionId": payload.deterministic_session_id,
                        "event": {
                            "type": "tool/call",
                            "data": {"toolCallId": f"attempt-{index}"},
                            "timestamp": NOW.isoformat(),
                        },
                    },
                }
                for index in range(5)
            ]
            events_json = json.dumps(attempted_events)
            return result.model_copy(
                update={
                    "events_json": events_json,
                    "result_hash": hashlib.sha256(
                        (result.final_response + "\0" + events_json).encode("utf-8")
                    ).hexdigest(),
                }
            )

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db), LedgerHost(), poll_interval_seconds=0.001
    )
    result = await runtime.execute(_request(run_id))

    assert result.total_tool_calls == 2


async def test_web_runtime_retains_attested_evidence_from_failed_host_turn(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-failed-partial.sqlite3")
    run_id, _ = RunService(db).create("event-web-failed-partial")
    host = FakeHostClient()

    async def failed_status(payload: DshSessionSubmit) -> DshSessionStatus:
        return host.status_value(payload, "failed", 5).model_copy(
            update={"error_code": "dsh_turn_error"}
        )

    async def partial_result(payload: DshSessionSubmit) -> DshSessionResult:
        with db.session() as session:
            link = session.get(DshSessionLinkRecord, payload.run_id)
            assert link is not None
            link.tool_calls_started = 1
        return _failed_result_with_attested_evidence(payload)

    host.status = failed_status  # type: ignore[method-assign]
    host.result = partial_result  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)

    result = await runtime.execute(_request(run_id))
    link = DshSessionLinkService(db).get(run_id)

    assert result.status == "degraded"
    assert [item.evidence_id for item in result.evidence_candidates] == [
        "ev-partial-provider-failure"
    ]
    assert result.causal_case is None
    assert result.horizons == []
    assert result.total_tool_calls == 1
    assert result.stop_reason.detail.startswith("DSH session was incomplete (error)")
    assert link is not None and link.state == "failed"
    assert link.error_code == "dsh_turn_error"
    assert link.result_ref == f"dsh-host://runs/{run_id}/result"
    assert link.result_hash is not None


async def test_web_runtime_keeps_failed_turn_without_partial_result_fail_closed(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-failed-empty.sqlite3")
    run_id, _ = RunService(db).create("event-web-failed-empty")
    host = FakeHostClient()

    async def failed_status(payload: DshSessionSubmit) -> DshSessionStatus:
        return host.status_value(payload, "failed", 2).model_copy(
            update={"error_code": "dsh_turn_error"}
        )

    async def no_result(_payload: DshSessionSubmit) -> DshSessionResult:
        raise AgentExecutionError(
            "host_terminal_result_unavailable",
            "DSH Host has no partial result",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="host_terminal_result_unavailable",
        )

    host.status = failed_status  # type: ignore[method-assign]
    host.result = no_result  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id))
    link = DshSessionLinkService(db).get(run_id)

    assert raised.value.error_code == "dsh_turn_error"
    assert link is not None and link.state == "failed"
    assert link.result_ref is None
    assert link.result_hash is None


async def test_web_runtime_keeps_trusted_evidence_when_synthesis_attestation_fails(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-evidence-only.sqlite3")
    run_id, _ = RunService(db).create("event-web-evidence-only")
    host = FakeHostClient()

    async def invalid_synthesis(payload: DshSessionSubmit) -> DshSessionResult:
        evidence = EvidenceCandidate.model_validate(
            {
                "evidence_id": "ev-web-attested",
                "requirement_id": "event_identity",
                "kind": "official",
                "authority": "official",
                "source_id": "federalreserve.gov",
                "source_url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
                "published_at": (NOW - timedelta(minutes=2)).isoformat(),
                "observed_at": (NOW - timedelta(seconds=2)).isoformat(),
                "received_at": (NOW - timedelta(seconds=1)).isoformat(),
                "content_hash": "1" * 64,
                "excerpt": "Official event identity.",
                "structured_payload_ref": None,
                "tool_call_id": "call-mcp-1",
                "research_session_id": payload.deterministic_session_id,
                "round": 1,
                "quality": "candidate",
                "freshness_status": "unknown",
                "conflict_group": None,
            }
        )
        capability = ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=f"research-request:{payload.run_id}",
            capability_id="replay.research",
            provider="fixture-replay",
            evidence_candidates=[evidence],
            cost_usd=0.0,
            completed_at=NOW,
        )
        tool_call = {
            "method": "session.event",
            "payload": {
                "sessionId": payload.deterministic_session_id,
                "event": {
                    "type": "tool/call",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-call",
                                    "toolCallId": "call-mcp-1",
                                    "toolName": (
                                        "mcp__decision_research__research_capability_execute"
                                    ),
                                }
                            ]
                        }
                    },
                    "timestamp": NOW.isoformat(),
                },
            },
        }
        tool_result = {
            "method": "session.event",
            "payload": {
                "sessionId": payload.deterministic_session_id,
                "event": {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-result",
                                    "toolCallId": "call-mcp-1",
                                    "structuredContent": capability.model_dump(mode="json"),
                                    "content": [],
                                }
                            ]
                        }
                    },
                    "timestamp": NOW.isoformat(),
                },
            },
        }
        final_response = json.dumps(
            {
                "schema_version": "research-synthesis-candidate.v1",
                "request_id": f"research-request:{payload.run_id}",
                "causal_case": None,
                "horizons": [
                    {
                        "horizon": "30m",
                        "action": "no_trade",
                        "subjective_probability": 0.5,
                        "probability_status": "uncalibrated",
                        "evidence_refs": ["ev-model-rewrote-this-id"],
                        "trigger": "Wait for confirmation.",
                        "invalidation": "New evidence invalidates the draft.",
                        "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
                        "next_review_at": (NOW + timedelta(minutes=10)).isoformat(),
                        "missing_facts": ["event_identity"],
                        "confidence_cap_reason": "Attestation must pass.",
                    }
                ],
            }
        )
        events_json = json.dumps([tool_call, tool_result])
        result_hash = hashlib.sha256(
            (final_response + "\0" + events_json).encode("utf-8")
        ).hexdigest()
        return DshSessionResult(
            schema_version="dsh-session-result.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            generation=payload.generation,
            last_seq=4,
            final_response=final_response,
            finish_reason="completed",
            events_json=events_json,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
            trace_ref=f"dsh://sessions/{payload.deterministic_session_id}/trace",
            result_hash=result_hash,
        )

    host.result = invalid_synthesis  # type: ignore[method-assign]
    sink = RecordingTraceSink()
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)

    result = await runtime.execute(_request(run_id), trace_sink=sink)

    assert result.status == "degraded"
    assert result.synthesis_failure_code == "dsh_evidence_unattested"
    assert result.evidence_candidates[0].evidence_id == "ev-web-attested"
    assert result.causal_case is None
    assert result.horizons == []
    assert len(host.submit_calls) == 1
    failure_events = [item for item in sink.events if item.event_type == "session_stopped"]
    assert len(failure_events) == 1
    assert failure_events[0].error_code == "dsh_evidence_unattested"
    assert failure_events[0].error is not None
    assert failure_events[0].error.cause_code == "synthesis_attestation"


async def test_web_runtime_repairs_synthesis_without_repeating_research(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-synthesis-repair.sqlite3")
    run_id, _ = RunService(db).create("event-web-synthesis-repair")
    host = FakeHostClient()

    async def repairable_result(payload: DshSessionSubmit) -> DshSessionResult:
        return _completed_result_with_repairable_synthesis(payload)

    host.result = repairable_result  # type: ignore[method-assign]
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    request = _request(run_id)
    request = request.model_copy(
        update={
            "execution_budget": request.execution_budget.model_copy(
                update={"max_structured_repairs": 1}
            ),
            "evidence_requirements": [
                request.evidence_requirements[0].model_copy(update={"importance": "soft"})
            ],
        }
    )

    result = await runtime.execute(request)

    assert result.status == "completed"
    assert result.synthesis_failure_code is None
    assert result.horizons[0].evidence_refs == ["ev-partial-provider-failure"]
    assert [item.generation for item in host.submit_calls] == [1, 2]
    assert len(result.evidence_candidates) == 1
    repair_prompt = links.get_prompt(run_id, 2)
    assert repair_prompt is not None
    assert '"ev-partial-provider-failure"' in repair_prompt.prompt
    assert "ev-partial-provider-failur is not an allowed Evidence ID" in repair_prompt.prompt
    assert "evidence_ref_unattested" in repair_prompt.prompt


async def test_web_runtime_keeps_session_identity_across_evidence_round_continuation(
    tmp_path: Path,
) -> None:
    """Outer LangGraph rounds must continue one DSH Session, not conflict."""
    db = _database(tmp_path / "web-runtime-continuation.sqlite3")
    run_id, _ = RunService(db).create("event-web-continuation")
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
    )

    first_request = _request(run_id)
    second_request = first_request.model_copy(
        update={
            "current_round": 2,
            "evidence_refs": ["evidence-input", "evidence-round-1"],
        }
    )
    await runtime.execute(first_request)
    await runtime.execute(second_request)

    assert len(host.submit_calls) == 2
    assert host.submit_calls[0].request_hash == host.submit_calls[1].request_hash
    assert (
        host.submit_calls[0].deterministic_session_id
        == host.submit_calls[1].deterministic_session_id
    )
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state == "completed"


async def test_web_runtime_timeout_cancels_and_fails_closed(tmp_path: Path) -> None:
    db = _database(tmp_path / "web-runtime-timeout.sqlite3")
    run_id, _ = RunService(db).create("event-web-timeout")
    host = FakeHostClient()

    async def never_terminal(payload: DshSessionSubmit) -> DshSessionStatus:
        return host.status_value(payload, "running", 1)

    async def no_partial_result(_payload: DshSessionSubmit) -> DshSessionResult:
        raise AgentExecutionError(
            "dsh_host_conflict",
            "DSH Host has no partial result",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="http_409",
        )

    host.status = never_terminal  # type: ignore[method-assign]
    host.result = no_partial_result  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
    )

    # Establish a caller budget that includes actual SQLite proof/commit and
    # lets the fake Host submit be accepted on this Windows machine. The old
    # 50ms cold samples may expire before acceptance. This scenario verifies cleanup;
    # submit-before-deadline exhaustion is covered separately below.
    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id, deadline_seconds=0.5))

    assert raised.value.error_code == "provider_timeout"
    assert host.cancelled == [run_id]
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state in {"running", "cancelled"}


async def test_web_runtime_deadline_retains_evidence_from_cancelled_turn(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-timeout-partial.sqlite3")
    run_id, _ = RunService(db).create("event-web-timeout-partial")
    host = FakeHostClient()

    async def never_terminal(payload: DshSessionSubmit) -> DshSessionStatus:
        return host.status_value(payload, "running", 1)

    async def cancelled_partial(payload: DshSessionSubmit) -> DshSessionResult:
        with db.session() as session:
            link = session.get(DshSessionLinkRecord, payload.run_id)
            assert link is not None
            link.tool_calls_started = 1
        return _failed_result_with_attested_evidence(payload).model_copy(
            update={"finish_reason": "aborted"}
        )

    host.status = never_terminal  # type: ignore[method-assign]
    host.result = cancelled_partial  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
        terminal_reconciliation_seconds=0.1,
    )

    # Exercise an accepted turn's actual partial reconciliation. As in the
    # no-partial case above, the initial caller budget includes real SQL proof.
    result = await runtime.execute(_request(run_id, deadline_seconds=0.5))
    link = DshSessionLinkService(db).get(run_id)

    assert host.cancelled == [run_id]
    assert result.status == "degraded"
    assert len(result.evidence_candidates) == 1
    assert result.horizons == []
    assert result.total_tool_calls == 1
    assert link is not None and link.state == "cancelled"
    assert link.result_ref == f"dsh-host://runs/{run_id}/result"
    assert link.result_hash is not None


async def test_web_runtime_readiness_timeout_before_acceptance_does_not_cancel_remote_run(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-submit-timeout.sqlite3")
    run_id, _ = RunService(db).create("event-web-submit-timeout")
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id, deadline_seconds=0.000001))

    assert raised.value.error_code == "provider_timeout"
    assert raised.value.cause_code == "dsh_web_readiness_deadline_elapsed"
    assert host.submit_calls == []
    assert host.cancelled == []


async def test_web_runtime_recovers_when_host_transiently_exits_after_acceptance(
    tmp_path: Path,
) -> None:
    """A transient Host exit must not fail a session that later completes."""
    db = _database(tmp_path / "web-runtime-host-restart.sqlite3")
    run_id, _ = RunService(db).create("event-web-host-restart")
    host = FakeHostClient()
    original_status = host.status
    crashed = True

    async def crash_once(payload: DshSessionSubmit) -> DshSessionStatus:
        nonlocal crashed
        if crashed:
            crashed = False
            raise AgentExecutionError(
                "dsh_host_unavailable",
                "simulated DSH Web process exit",
                retryable=True,
                provider_id="dsh-web",
                origin="transport",
                cause_code="host_process_exit",
            )
        return await original_status(payload)

    host.status = crash_once  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    result = await runtime.execute(_request(run_id))
    assert result.research_session_id == host.submit_calls[0].deterministic_session_id

    completed = DshSessionLinkService(db).get(run_id)
    assert completed is not None and completed.state == "completed"

    # A replacement runtime still reuses the persisted deterministic session link.
    recovered = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    recovered_result = await recovered.execute(_request(run_id))
    assert recovered_result.research_session_id == result.research_session_id
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state == "completed"
    assert len({item.deterministic_session_id for item in host.submit_calls}) == 1


async def test_web_runtime_recovers_durable_acceptance_when_submit_http_reply_is_lost(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-submit-reply-lost.sqlite3")
    run_id, _ = RunService(db).create("event-web-submit-reply-lost")
    host = FakeHostClient()
    links = DshSessionLinkService(db)
    submit_calls = 0

    async def accepted_then_timeout(payload: DshSessionSubmit) -> DshSessionAccepted:
        nonlocal submit_calls
        submit_calls += 1
        host.submit_calls.append(payload)
        accepted = DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            accepted_at=datetime.now(UTC),
            generation=payload.generation,
        )
        links.accepted(accepted)
        raise AgentExecutionError(
            "provider_timeout",
            "controlled lost HTTP 202 after durable acceptance",
            retryable=True,
            provider_id="dsh-web",
            origin="transport",
            cause_code="dsh_host_timeout",
            deadline_ms=15_000,
        )

    host.submit = accepted_then_timeout  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)

    result = await runtime.execute(_request(run_id))
    link = links.get(run_id)

    assert result.status in {"completed", "degraded"}
    assert submit_calls == 1
    assert len(host.submit_calls) == 1
    assert host.cancelled == []
    assert link is not None and link.state == "completed"


async def test_web_runtime_never_replays_unknown_submit_transport_result(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-submit-transport-unknown.sqlite3")
    run_id, _ = RunService(db).create("event-web-submit-transport-unknown")
    host = FakeHostClient()
    links = DshSessionLinkService(db)

    async def timeout_without_acceptance(payload: DshSessionSubmit) -> DshSessionAccepted:
        host.submit_calls.append(payload)
        raise AgentExecutionError(
            "provider_timeout",
            "controlled unknown PUT result before acceptance became visible",
            retryable=True,
            provider_id="dsh-web",
            origin="transport",
            cause_code="dsh_host_timeout",
            deadline_ms=35_000,
        )

    host.submit = timeout_without_acceptance  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id))

    assert raised.value.error_code == "provider_timeout"
    assert raised.value.cause_code == "dsh_host_timeout"
    assert len(host.submit_calls) == 1
    link = links.get(run_id)
    assert link is not None and link.accepted_at is None


async def test_web_runtime_does_not_hide_explicit_host_error_after_acceptance(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-submit-explicit-error.sqlite3")
    run_id, _ = RunService(db).create("event-web-submit-explicit-error")
    host = FakeHostClient()
    links = DshSessionLinkService(db)
    submit_calls = 0

    async def accepted_then_explicit_error(
        payload: DshSessionSubmit,
    ) -> DshSessionAccepted:
        nonlocal submit_calls
        submit_calls += 1
        host.submit_calls.append(payload)
        accepted = DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            accepted_at=datetime.now(UTC),
            generation=payload.generation,
        )
        if submit_calls == 1:
            links.accepted(accepted)
            raise AgentExecutionError(
                "provider_unavailable",
                "controlled explicit Host failure after acceptance",
                retryable=True,
                provider_id="dsh-web",
                origin="transport",
                cause_code="http_503",
            )
        return accepted

    host.submit = accepted_then_explicit_error  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id))
    link = links.get(run_id)

    assert raised.value.error_code == "provider_unavailable"
    assert submit_calls == 1
    assert len(host.submit_calls) == 1
    assert link is not None and link.state == "admitted"

    # A restarted Worker authenticates the exact durable tuple and continues
    # through GET status/result.  It must not replay PUT submit.
    recovered = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    result = await recovered.execute(_request(run_id))
    assert result.status in {"completed", "degraded"}
    assert submit_calls == 1
    assert len(host.submit_calls) == 1
    link = links.get(run_id)
    assert link is not None and link.state == "completed"
    stale = DshSessionAccepted(
        schema_version="dsh-session-accepted.v1",
        run_id=run_id,
        dsh_session_id=host.submit_calls[0].deterministic_session_id,
        accepted_at=link.accepted_at,
        generation=1,
    )
    assert runtime._recover_new_durable_acceptance(  # pyright: ignore[reportPrivateUsage]
        host.submit_calls[0],
        stale,
        AgentExecutionError(
            "provider_timeout",
            "controlled stale acceptance behind a later timeout",
            retryable=True,
            provider_id="dsh-web",
            origin="transport",
            cause_code="dsh_host_timeout",
        ),
    ) is None


async def test_web_runtime_native_session_loss_is_terminal_without_submit_replay(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "web-runtime-native-session-lost.sqlite3")
    run_id, _ = RunService(db).create("event-web-native-session-lost")
    host = FakeHostClient()

    async def native_session_lost(_payload: DshSessionSubmit) -> DshSessionStatus:
        raise AgentExecutionError(
            "dsh_native_session_lost",
            "The accepted native DSH Session is no longer materialized",
            retryable=False,
            provider_id="dsh-web",
            origin="dsh",
            cause_code="dsh_native_session_lost",
        )

    host.status = native_session_lost  # type: ignore[method-assign]
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)

    with pytest.raises(AgentExecutionError) as first:
        await runtime.execute(_request(run_id))
    assert first.value.error_code == "dsh_native_session_lost"
    assert len(host.submit_calls) == 1
    accepted = links.get(run_id)
    assert accepted is not None and accepted.accepted_at is not None

    recovered = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    with pytest.raises(AgentExecutionError) as second:
        await recovered.execute(_request(run_id))
    assert second.value.error_code == "dsh_native_session_lost"
    assert len(host.submit_calls) == 1


async def test_web_runtime_reconciles_a_lost_terminal_callback(tmp_path: Path) -> None:
    """A dropped Hub terminal write is repaired by a later status/result poll."""
    db = _database(tmp_path / "web-runtime-lost-callback.sqlite3")
    run_id, _ = RunService(db).create("event-web-lost-callback")
    host = FakeHostClient()
    links = DshSessionLinkService(db)
    original_complete = links.complete
    dropped = True

    def drop_first_completion(callback: DshSessionCompletion):
        nonlocal dropped
        if dropped:
            dropped = False
            raise RuntimeError("simulated callback delivery loss")
        return original_complete(callback)

    links.complete = drop_first_completion  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    with pytest.raises(RuntimeError, match="delivery loss"):
        await runtime.execute(_request(run_id))

    pending = DshSessionLinkService(db).get(run_id)
    assert pending is not None and pending.state == "running"
    repaired = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    await repaired.execute(_request(run_id))
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state == "completed"
    assert link.result_hash is not None


async def test_web_runtime_fails_closed_on_upstream_version_mismatch(tmp_path: Path) -> None:
    db = _database(tmp_path / "web-runtime-version-mismatch.sqlite3")
    run_id, _ = RunService(db).create("event-web-version-mismatch")
    host = FakeHostClient()

    async def incompatible_readiness() -> DshHostReadiness:
        report = await FakeHostClient().readiness()
        return report.model_copy(
            update={
                "ready": False,
                "version_compatible": False,
                "error_code": "host_version_incompatible",
            }
        )

    host.readiness = incompatible_readiness  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id))

    assert raised.value.error_code == "host_version_incompatible"
    assert DshSessionLinkService(db).get(run_id) is None
    assert host.submit_calls == []
