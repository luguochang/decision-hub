from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
    ResearchSessionRequest as SelectedRequest,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    V2_DECISION_OUTPUT_SCHEMA_REF,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification, DshSdkRun
from packages.runtime_adapters.dsh_runtime.result_mapper import (
    map_evidence_only_result,
    map_session_result_with_witness,
)

NOW = datetime(2026, 10, 20, tzinfo=UTC)
SESSION_ID = "dsh-test-fallback"


def _request() -> ResearchSessionRequest:
    return ResearchSessionRequest.model_validate(
        {
            "schema_version": "research-session-request.v1",
            "request_id": "request-fallback",
            "run_id": "run-fallback",
            "event_id": "event",
            "trigger_snapshot_id": "snapshot",
            "domain_pack_ref": "crypto_macro.v1",
            "role_profile_ref": "crypto_macro.manager.v1",
            "execution_mode": "replay",
            "context_manifest": {
                "manifest_id": "manifest-selected",
                "policy_ref": "policy",
                "revision": 1,
                "selected_inputs": ["input"],
                "effective_inputs": ["input"],
                "excluded_inputs": [],
                "rules_ref": "rules",
                "tool_versions": {},
                "data_versions": {},
                "input_fingerprint": "a" * 64,
                "created_at": NOW,
            },
            "pit_cutoff_at": NOW,
            "current_round": 1,
            "evidence_refs": ["input"],
            "input_evidence": [
                {
                    "evidence_id": "input",
                    "kind": "transcript",
                    "authority": "unverified",
                    "source_id": "manual",
                    "source_url": None,
                    "published_at": NOW,
                    "observed_at": NOW,
                    "received_at": NOW,
                    "content_hash": "0" * 64,
                    "excerpt": "Controlled input fixture.",
                }
            ],
            "evidence_requirements": [
                {
                    "requirement_id": "event_identity",
                    "description": "Verify event identity.",
                    "importance": "hard",
                    "source_priority": ["official"],
                    "authority_floor": "official",
                    "preferred_capabilities": ["replay.research"],
                    "freshness_seconds": 3600,
                    "minimum_independent_sources": 1,
                    "allowed_fallbacks": ["replay.research"],
                    "confidence_cap": 0.45,
                }
            ],
            "target_gaps": [],
            "allowed_capabilities": ["replay.research"],
            "execution_budget": {
                "max_evidence_rounds": 2,
                "max_tool_calls": 6,
                "max_subagents": 2,
                "total_deadline_seconds": 300,
                "per_tool_timeout_seconds": 5,
                "per_model_step_timeout_seconds": 10,
                "max_structured_repairs": 0,
                "max_estimated_cost_usd": 0.1,
            },
            "deadline_at": NOW + timedelta(minutes=5),
            "output_schema_ref": V2_DECISION_OUTPUT_SCHEMA_REF,
            "repair_instructions": None,
        }
    )


def _run() -> DshSdkRun:
    capability = {
        "schema_version": "research-capability-result.v1",
        "request_id": "request-fallback",
        "capability_id": "replay.research",
        "provider": "fixture-replay",
        "evidence_candidates": [
            {
                "evidence_id": "ev-attested",
                "requirement_id": "event_identity",
                "kind": "official",
                "authority": "official",
                "source_id": "fixture-official",
                "source_url": None,
                "published_at": NOW.isoformat(),
                "observed_at": NOW.isoformat(),
                "received_at": NOW.isoformat(),
                "content_hash": "1" * 64,
                "excerpt": "Controlled official fixture.",
                "structured_payload_ref": None,
                "tool_call_id": "call-research",
                "research_session_id": SESSION_ID,
                "round": 1,
                "quality": "candidate",
                "freshness_status": "unknown",
                "conflict_group": None,
            }
        ],
        "cost_usd": 0.0,
        "completed_at": NOW.isoformat(),
    }
    call = {"type": "tool-call", "toolCallId": "call-research", "toolName": "decision_hub_research"}
    result = {
        "type": "tool-result",
        "toolCallId": "call-research",
        "structuredContent": capability,
        "content": [],
    }
    notifications = tuple(
        DshSdkNotification(
            "session.event",
            {
                "sessionId": SESSION_ID,
                "event": {"type": kind, "data": {"message": {"content": [body]}}},
            },
        )
        for kind, body in [("tool/call", call), ("tool/result", result)]
    )
    return DshSdkRun(SESSION_ID, "No synthesis capture.", "completed", (), notifications, None)


def _fallback(request, *, run=None):
    return map_evidence_only_result(
        run or _run(),
        request,
        [],
        runtime_version="test",
        profile_ref="decision-research.web",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        failure_code="structured_output_invalid",
        failure_cause="perpetual_synthesis_capture_missing",
    )


@pytest.mark.parametrize("selected", [False, True], ids=["canonical", "selected-generated"])
def test_missing_synthesis_capture_retains_typed_degraded_fallback(selected):
    canonical = _request()
    request = (
        SelectedRequest.model_validate(canonical.model_dump(mode="json")) if selected else canonical
    )
    if selected:
        assert type(request.context_manifest) is not type(canonical.context_manifest)
        assert request.model_dump(mode="json") == canonical.model_dump(mode="json")
    with pytest.raises(AgentExecutionError) as missing:
        map_session_result_with_witness(
            _run(),
            request,
            [],
            runtime_version="test",
            profile_ref="decision-research.web",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        )
    assert missing.value.error_code == "structured_output_invalid"
    assert missing.value.cause_code == "perpetual_synthesis_capture_missing"

    result = _fallback(request)
    assert isinstance(result, ResearchSessionResult)
    assert type(result.context_manifest) is type(canonical.context_manifest)
    assert result.context_manifest.model_dump(mode="json") == canonical.context_manifest.model_dump(
        mode="json"
    )
    assert result.status == "degraded"
    assert result.synthesis_failure_code == "structured_output_invalid"
    assert result.causal_case is None and result.horizons == []
    assert result.stop_reason.code == "critical_data_unavailable"
    assert [row.evidence_id for row in result.evidence_candidates] == ["ev-attested"]
    assert result.reviews == []
    assert request.model_dump(mode="json") == canonical.model_dump(mode="json")


def test_fallback_without_trusted_evidence_preserves_original_failure():
    request = SelectedRequest.model_validate(_request().model_dump(mode="json"))
    empty_run = DshSdkRun(SESSION_ID, "No synthesis capture.", "completed", (), (), None)
    with pytest.raises(AgentExecutionError) as missing:
        _fallback(request, run=empty_run)
    assert missing.value.error_code == "structured_output_invalid"
    assert missing.value.cause_code == "perpetual_synthesis_capture_missing"


@pytest.mark.parametrize("field,value", [("revision", 0), ("unexpected", True)])
def test_fallback_rejects_invalid_or_unknown_manifest_wire(field, value):
    request = _request()
    manifest = request.context_manifest.model_dump(mode="json") | {field: value}
    # Simulate an invalid typed transport object without blessing its wire data.
    malformed = request.model_copy(update={"context_manifest": manifest})
    with pytest.raises(ValidationError):
        _fallback(malformed)
