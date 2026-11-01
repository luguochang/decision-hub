"""Origin attestation must not promote a native V2 candidate into claim support."""
from datetime import timedelta
from typing import Literal

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.result_mapper import (
    map_session_result,
    map_session_result_with_witness,
    synthesis_evidence_ref_errors,
    trusted_evidence_ids,
)
from tests.runtime.test_dsh_research_runtime import (
    NOW,
    _attested_evidence,  # pyright: ignore[reportPrivateUsage]
    _capability_result,  # pyright: ignore[reportPrivateUsage]
    _causal_payload,  # pyright: ignore[reportPrivateUsage]
    _horizon_payload,  # pyright: ignore[reportPrivateUsage]
    _mcp_result_notification,  # pyright: ignore[reportPrivateUsage]
    _notification,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _result_payload,  # pyright: ignore[reportPrivateUsage]
    _review_capture_notifications,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
    _synthesis_capture_notifications,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.parametrize("quality", ["candidate", "stale", "rejected", "conflicted"])
def test_native_citation_excludes_unaccepted_attested_material(
    quality: Literal["candidate", "stale", "rejected", "conflicted"],
) -> None:
    evidence = _attested_evidence().model_copy(update={"quality": quality})
    payload = _result_payload()
    payload.update(causal_case=_causal_payload(evidence.evidence_id),
                   horizons=[_horizon_payload(evidence.evidence_id)])
    run = _run(notifications=(
        _notification("tool/call", {"toolCallId": "call-mcp-1",
            "toolName": "mcp__decision_research__research_capability_execute"}),
        _mcp_result_notification(_capability_result(evidence)),
        *_synthesis_capture_notifications(payload),
    ))
    with pytest.raises(AgentExecutionError) as caught:
        map_session_result_with_witness(run, _request(), [], runtime_version="test",
            profile_ref="decision-research.web.v1", started_at=NOW,
            finished_at=NOW + timedelta(seconds=2), accepted_synthesis_only=True)
    assert caught.value.error_code == "dsh_evidence_unattested"
    assert caught.value.cause_code == "synthesis_attestation"
    assert trusted_evidence_ids(run, _request(), accepted_only=True) == ["evidence-transcript"]
    errors = synthesis_evidence_ref_errors(run, _request(), accepted_only=True)
    assert len(errors) == 4  # summary, both causal chains, and horizon
    assert all(evidence.evidence_id in str(error["msg"]) for error in errors)
    # Origin evidence remains intact and legacy post-mapping admission remains unchanged.
    legacy = map_session_result(run, _request(), [], runtime_version="test",
        profile_ref="decision-research.v1", started_at=NOW,
        finished_at=NOW + timedelta(seconds=2))
    assert legacy.evidence_candidates == [evidence]
    assert legacy.horizons[0].evidence_refs == [evidence.evidence_id]


def test_accepted_claim_keeps_candidate_context_in_bounded_advisory_review() -> None:
    accepted = _attested_evidence().model_copy(update={"quality": "accepted"})
    candidate = accepted.model_copy(update={"evidence_id": "ev-background", "quality": "candidate"})
    capability = _capability_result(accepted).model_copy(
        update={"evidence_candidates": [accepted, candidate]})
    payload = _result_payload()
    payload["horizons"] = [_horizon_payload(accepted.evidence_id)]
    run = _run(notifications=(
        _notification("tool/call", {"toolCallId": "call-mcp-1",
            "toolName": "mcp__decision_research__research_capability_execute"}),
        _mcp_result_notification(capability),
        *_review_capture_notifications(candidate.evidence_id, resolution="bounded"),
        *_synthesis_capture_notifications(payload),
    ))
    result = map_session_result_with_witness(run, _request(), [], runtime_version="test",
        profile_ref="decision-research.web.v1", started_at=NOW,
        finished_at=NOW + timedelta(seconds=2), accepted_synthesis_only=True).result
    assert result.horizons[0].evidence_refs == [accepted.evidence_id]
    assert result.evidence_candidates == [accepted, candidate]
    assert result.reviews is not None
    assert len(result.reviews) == 1 and result.reviews[0].resolution == "bounded"
    assert result.reviews[0].evidence_refs == [candidate.evidence_id]
    assert trusted_evidence_ids(run, _request(), accepted_only=True) == [
        "ev-attested-1", "evidence-transcript"]
