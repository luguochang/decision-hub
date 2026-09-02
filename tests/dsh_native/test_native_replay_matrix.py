from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    ObservationCreate,
    ResearchCapabilityResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.research.service import ResearchQueryService
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime

# The Web runtime's bounded wait uses wall clock for its transport deadline;
# keep the fixture timestamps deterministic relative to this test invocation.
NOW = datetime.now(UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
Scenario = Literal["success", "partial_failure", "insufficient_or_stale"]


class MatrixHostClient:
    """Contract-level DSH Host seam used when the official Web has no provider key."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario: Scenario = scenario
        self.submits: list[DshSessionSubmit] = []
        self._polls: dict[str, int] = {}

    async def readiness(self) -> DshHostReadiness:
        return DshHostReadiness(
            schema_version="dsh-host-readiness.v1",
            ready=True,
            version_compatible=True,
            session_controller=True,
            client_plugin=True,
            hub_reachable=True,
            upstream_identity=DshUpstreamIdentity(
                source_commit="0a53fb55bea101816fa226bb964ae2bed71c343b",
                source_version="0.1.2-alpha.2",
                package_versions={"@deepseek-ai/dsh": "0.1.2-alpha.2"},
                plugin_build_hash="b" * 64,
            ),
            checked_at=NOW,
            error_code=None,
        )

    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
        self.submits.append(payload)
        return DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            accepted_at=NOW,
            generation=payload.generation,
        )

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        count = self._polls.get(payload.run_id, 0) + 1
        self._polls[payload.run_id] = count
        state = "completed" if count > 1 else "running"
        return DshSessionStatus(
            schema_version="dsh-session-status.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            state=state,
            generation=payload.generation,
            last_seq=count + 1,
            observed_at=NOW + timedelta(seconds=count),
            error_code=None,
        )

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        session_id = payload.deterministic_session_id
        candidates = _scenario_candidates(self.scenario, session_id, payload.generation)
        notifications = _notifications(payload, candidates, self.scenario)
        evidence_refs = [str(item["evidence_id"]) for item in candidates]
        synthesis = {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": f"research-request:{payload.run_id}",
            "causal_case": _causal_case(evidence_refs) if evidence_refs else None,
            "horizons": _horizons(evidence_refs) if evidence_refs else [],
        }
        final_response = json.dumps(synthesis, sort_keys=True)
        events_json = json.dumps(
            [{"method": method, "payload": body} for method, body in notifications],
            sort_keys=True,
        )
        result_hash = hashlib.sha256(
            (final_response + "\0" + events_json).encode()
        ).hexdigest()
        return DshSessionResult(
            schema_version="dsh-session-result.v1",
            run_id=payload.run_id,
            dsh_session_id=session_id,
            generation=payload.generation,
            last_seq=len(notifications) + 2,
            final_response=final_response,
            finish_reason="completed",
            events_json=events_json,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=1),
            trace_ref=f"dsh://sessions/{session_id}/trace",
            result_hash=result_hash,
        )

    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        del reason
        return DshSessionStatus(
            schema_version="dsh-session-status.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            state="cancelled",
            generation=payload.generation,
            last_seq=1,
            observed_at=NOW,
            error_code=None,
        )

    async def close(self) -> None:
        return None


def _candidate(
    requirement_id: str,
    session_id: str,
    *,
    authority: str,
    source_id: str,
    observed_at: datetime,
    round_number: int,
) -> dict[str, object]:
    source_url = (
        "https://www.federalreserve.gov/"
        if authority == "official"
        else "https://exchange.example.test/market"
    )
    excerpt = f"Replay evidence for {requirement_id} from {source_id}."
    published_at = observed_at - timedelta(seconds=1)
    content_hash = research_evidence_content_hash(
        requirement_id=requirement_id,
        kind="official" if authority == "official" else "market",
        authority=authority,
        source_id=source_id,
        source_url=source_url,
        published_at=published_at,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return {
        "evidence_id": f"matrix-{requirement_id}-{source_id}",
        "requirement_id": requirement_id,
        "kind": "official" if authority == "official" else "market",
        "authority": authority,
        "source_id": source_id,
        "source_url": source_url,
        "published_at": published_at.isoformat(),
        "observed_at": observed_at.isoformat(),
        "received_at": observed_at.isoformat(),
        "content_hash": content_hash,
        "excerpt": excerpt,
        "structured_payload_ref": None,
        "tool_call_id": "matrix-call-success",
        "research_session_id": session_id,
        "round": round_number,
        "quality": "candidate",
        "freshness_status": "unknown",
        "conflict_group": None,
    }


def _scenario_candidates(
    scenario: Scenario, session_id: str, generation: int
) -> list[dict[str, object]]:
    if scenario == "insufficient_or_stale":
        observed_at = NOW - timedelta(days=30)
        return [
            _candidate(
                "event_identity",
                session_id,
                authority="official",
                source_id="official-stale",
                observed_at=observed_at,
                round_number=generation,
            )
        ]
    if scenario == "partial_failure":
        return [
            _candidate(
                "event_identity",
                session_id,
                authority="official",
                source_id="official-partial",
                observed_at=NOW,
                round_number=generation,
            ),
            _candidate(
                "policy_or_data_delta",
                session_id,
                authority="official",
                source_id="official-partial",
                observed_at=NOW,
                round_number=generation,
            ),
        ]
    result: list[dict[str, object]] = []
    authorities = {
        "event_identity": ("official", "official-event"),
        "policy_or_data_delta": ("official", "official-policy"),
        "expectation_pricing": ("exchange", "exchange-pricing"),
        "cross_asset_confirmation": ("verified_web", "web-cross-asset"),
        "crypto_spot_confirmation": ("exchange", "exchange-spot"),
        "derivatives_crowding": ("exchange", "exchange-derivatives"),
        "counter_thesis": ("verified_web", "web-counter-thesis"),
    }
    selected = list(authorities.items())
    if generation == 1:
        selected = selected[:2]
    else:
        selected = selected[2:]
    for requirement_id, (authority, source_id) in selected:
        result.append(
            _candidate(
                requirement_id,
                session_id,
                authority=authority,
                source_id=source_id,
                observed_at=NOW,
                round_number=generation,
            )
        )
    for source_id in (() if generation == 1 else ("exchange-yield", "exchange-dollar")):
        result.append(
            _candidate(
                "macro_transmission",
                session_id,
                authority="exchange",
                source_id=source_id,
                observed_at=NOW,
                round_number=generation,
            )
        )
    return result


def _notifications(
    payload: DshSessionSubmit, candidates: list[dict[str, object]], scenario: Scenario
) -> list[tuple[str, dict[str, object]]]:
    session_id = payload.deterministic_session_id
    method = "mcp__decision_research__research_capability_execute"
    capability = ResearchCapabilityResult(
        schema_version="research-capability-result.v1",
        request_id=f"research-request:{payload.run_id}",
        capability_id="replay.research",
        provider="matrix-replay",
        evidence_candidates=[EvidenceCandidate.model_validate(item) for item in candidates],
        cost_usd=0.0,
        completed_at=NOW,
    )
    result: list[tuple[str, dict[str, object]]] = [
        (
            "session.event",
            {
                "sessionId": session_id,
                "event": {
                    "type": "tool/call",
                    "data": {"toolCallId": "matrix-call-success", "toolName": method},
                },
            },
        ),
        (
            "session.event",
            {
                "sessionId": session_id,
                "event": {
                    "type": "tool/result",
                    "data": {
                        "toolCallId": "matrix-call-success",
                        "toolName": method,
                        "content": [capability.model_dump(mode="json")],
                    },
                },
            },
        ),
    ]
    if scenario == "partial_failure":
        result.extend(
            [
                (
                    "session.event",
                    {
                        "sessionId": session_id,
                        "event": {
                            "type": "tool/call",
                            "data": {"toolCallId": "matrix-call-failed", "toolName": method},
                        },
                    },
                ),
                (
                    "session.event",
                    {
                        "sessionId": session_id,
                        "event": {
                            "type": "tool/result",
                            "data": {
                                "toolCallId": "matrix-call-failed",
                                "toolName": method,
                                "isError": True,
                                "error": {
                                    "error_code": "research_capability_timeout",
                                    "origin": "transport",
                                    "cause_code": "matrix_timeout",
                                    "capability_id": "replay.research",
                                    "retryable": True,
                                    "deadline_ms": 20_000,
                                },
                            },
                        },
                    },
                ),
            ]
        )
    return result


def _causal_case(evidence_refs: list[str]) -> dict[str, object]:
    ref = evidence_refs[0]
    link = {
        "link_id": "matrix-main-link",
        "claim_type": "fact",
        "statement": "The replay event was identified and bounded.",
        "evidence_refs": [ref],
        "confirmation": "An independent replay source confirms the event.",
        "invalidation": "The source is retracted or contradicted.",
        "affected_horizons": ["30m", "24h", "72h"],
    }
    opposite = {
        **link,
        "link_id": "matrix-opposite-link",
        "claim_type": "scenario",
        "statement": "The event may already be priced in.",
    }
    return {
        "case_id": "matrix-case",
        "thesis": "The replay event supports a bounded research candidate.",
        "main_chain": [link],
        "opposite_chain": [opposite],
        "unresolved_questions": [],
        "evidence_refs": [ref],
    }


def _horizons(evidence_refs: list[str]) -> list[dict[str, object]]:
    ref = evidence_refs[0]
    values = ("30m", "24h", "72h")
    minutes = (30, 1440, 4320)
    reviews = (10, 360, 1440)
    return [
        {
            "horizon": horizon,
            "action": "long",
            "subjective_probability": 0.6,
            "probability_status": "uncalibrated",
            "evidence_refs": [ref],
            "trigger": f"{horizon} replay confirmation trigger",
            "invalidation": f"{horizon} replay invalidation",
            "expires_at": (NOW + timedelta(minutes=minutes[index])).isoformat(),
            "next_review_at": (NOW + timedelta(minutes=reviews[index])).isoformat(),
            "missing_facts": [],
            "confidence_cap_reason": None,
        }
        for index, horizon in enumerate(values)
    ]


def _admitted_run(
    tmp_path: Path, scenario: Scenario
) -> tuple[Database, str, str, MatrixHostClient]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / f'{scenario}.sqlite3'}")
    database.create_all()
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="Replay event for native DSH product acceptance.",
            source_id="matrix-input",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2),
            published_at=NOW - timedelta(seconds=3),
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    host = MatrixHostClient(scenario)
    return database, event_id, run_id, host


@pytest.mark.parametrize("scenario", ["success", "partial_failure", "insufficient_or_stale"])
def test_native_replay_matrix_projects_three_product_outcomes(
    tmp_path: Path, scenario: Scenario
) -> None:
    database, event_id, run_id, host = _admitted_run(tmp_path, scenario)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(database), host, poll_interval_seconds=0.001
    )
    worker = DurableResearchWorker(
        database,
        runtime,
        pack_root=PACK_ROOT,
        allowed_capabilities=("replay.research",),
        execution_mode="replay",
        clock=lambda: NOW,
    )

    report = asyncio.run(worker.tick())
    assert report is not None
    run = database.get_run_record(run_id)
    assert run is not None and run.artifact_id == report.artifact_id
    link = DshSessionLinkService(database).get(run_id)
    assert link is not None
    assert link.dsh_session_id == host.submits[0].deterministic_session_id
    assert link.state == "completed"
    assert link.result_hash is not None
    result = ResearchObservabilityService(database).get_result(run_id)
    assert result is not None

    if scenario == "success":
        assert len(host.submits) == 2
        assert host.submits[0].generation == 1
        assert host.submits[1].generation == 2
        assert (
            host.submits[0].deterministic_session_id
            == host.submits[1].deterministic_session_id
        )
        assert (
            host.submits[0].deterministic_request_id
            != host.submits[1].deterministic_request_id
        )
        assert report.status == "publish"
        assert run.status == "completed"
        assert result.final_coverage.status == "sufficient"
        assert result.stop_reason.code == "sufficient"
    else:
        assert report.status == "research_only"
        assert run.status == "degraded"
        assert result.final_coverage.status == "insufficient"
        assert result.stop_reason.code in {"critical_data_unavailable", "round_budget"}

    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    business = ResearchQueryService(database, pack_root=PACK_ROOT).business_status(run_id)
    assert detail is not None
    assert business is not None
    assert detail.run.run_id == run_id
    assert detail.run.artifact_id == report.artifact_id
    assert business.gate_status == report.status
    assert business.coverage_status == result.final_coverage.status
    assert business.hard_coverage_ratio == result.final_coverage.hard_coverage_ratio
    assert business.stop_reason_code == result.stop_reason.code
    if scenario == "partial_failure":
        failed = [
            item
            for round_item in detail.rounds
            for item in round_item.tool_results
            if item.status == "failed"
        ]
        assert failed and failed[0].error is not None
        assert failed[0].error.origin == "transport"
        assert failed[0].error.retryable is True
        assert business.failures
        assert business.failures[0].error_code == "research_capability_timeout"
        assert business.failures[0].origin == "transport"
