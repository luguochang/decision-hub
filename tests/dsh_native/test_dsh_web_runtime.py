from __future__ import annotations

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
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
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
    def status_value(
        payload: DshSessionSubmit, state: str, sequence: int
    ) -> DshSessionStatus:
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
    assert len(host.submit_calls) == 2
    assert host.submit_calls[0].request_hash == host.submit_calls[1].request_hash
    assert (
        host.submit_calls[0].deterministic_session_id
        == host.submit_calls[1].deterministic_session_id
    )
    assert (
        host.submit_calls[0].deterministic_request_id
        == host.submit_calls[1].deterministic_request_id
    )
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
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db), host, poll_interval_seconds=0.001
    )

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

    host.status = never_terminal  # type: ignore[method-assign]
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db),
        host,
        poll_interval_seconds=0.001,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id, deadline_seconds=0.01))

    assert raised.value.error_code == "provider_timeout"
    assert host.cancelled == [run_id]
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state in {"running", "cancelled"}


async def test_web_runtime_recovers_when_host_exits_after_acceptance(tmp_path: Path) -> None:
    """A durable admitted link is enough to resume after a Host process exit."""
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
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db), host, poll_interval_seconds=0.001
    )
    with pytest.raises(AgentExecutionError, match="process exit"):
        await runtime.execute(_request(run_id))

    admitted = DshSessionLinkService(db).get(run_id)
    assert admitted is not None and admitted.state == "admitted"

    # A replacement runtime reuses the persisted deterministic session link.
    recovered = DshWebResearchRuntime(
        DshSessionLinkService(db), host, poll_interval_seconds=0.001
    )
    result = await recovered.execute(_request(run_id))
    assert result.research_session_id == host.submit_calls[0].deterministic_session_id
    link = DshSessionLinkService(db).get(run_id)
    assert link is not None and link.state == "completed"
    assert len({item.deterministic_session_id for item in host.submit_calls}) == 1


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
    repaired = DshWebResearchRuntime(
        DshSessionLinkService(db), host, poll_interval_seconds=0.001
    )
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
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(db), host, poll_interval_seconds=0.001
    )
    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(_request(run_id))

    assert raised.value.error_code == "host_version_incompatible"
    assert DshSessionLinkService(db).get(run_id) is None
    assert host.submit_calls == []
