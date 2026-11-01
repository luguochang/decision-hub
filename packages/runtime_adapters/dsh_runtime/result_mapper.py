from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchPlan,
    ResearchReview,
    ResearchRound,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchSynthesisCandidate,
    ResearchTask,
    ResearchTraceEvent,
    ToolInvocation,
    ToolResultSummary,
)
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewAuthorizationV2,
    ActionReviewRequestV2,
    ResearchDecisionSynthesisV2,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ResearchSynthesisSelectionWitness,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import (
    ResearchDecisionSynthesisV1,
    ResearchNativeSelection,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import synthesis_protocol
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkRun
from .finalized_coverage import merge_finalized_coverage
from .tool_result_attestation import (
    AttestedReviewReceipt,
    extract_attested_capability_results,
    extract_attested_decision_synthesis_captures,
    extract_attested_perpetual_synthesis_captures,
    extract_attested_review_receipts,
    extract_attested_synthesis_captures,
    extract_capability_call_targets,
    extract_server_baseline_results,
    synthesis_tool_result_wrappers,
)


@dataclass(frozen=True)
class MappedDecisionSynthesis:
    synthesis: ResearchDecisionSynthesisV1
    selection: ResearchNativeSelection
    raw_candidate_hash: str
    projected_research_candidate_hash: str


@dataclass(frozen=True)
class MappedPerpetualSynthesis:
    synthesis: ResearchDecisionSynthesisV2
    review_request: ActionReviewRequestV2
    authorization: ActionReviewAuthorizationV2 | None
    selection: ResearchNativeSelection
    raw_candidate_hash: str
    review_wrapper_json: str | None
    review_call_id: str | None
    review_call_event_index: int | None
    review_result_event_index: int | None


@dataclass(frozen=True)
class MappedSessionResult:
    result: ResearchSessionResult
    selection: ResearchSynthesisSelectionWitness
    action_synthesis: MappedDecisionSynthesis | None = None
    perpetual_synthesis: MappedPerpetualSynthesis | None = None


def map_session_result(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    traces: list[ResearchTraceEvent],
    *,
    runtime_version: str,
    profile_ref: str,
    started_at: datetime,
    finished_at: datetime,
) -> ResearchSessionResult:
    return map_session_result_with_witness(run, request, traces,
        runtime_version=runtime_version, profile_ref=profile_ref,
        started_at=started_at, finished_at=finished_at).result


def evidence_only_selection() -> ResearchSynthesisSelectionWitness:
    return ResearchSynthesisSelectionWitness(kind="evidence_only", candidate=None,
        candidate_payload_hash=None, final_response_hash=None, call_id=None,
        call_event_index=None, result_event_index=None, payload_occurrence=None,
        payload_path=None,
        call_event_hash=None, result_event_hash=None, call_seq=None, result_seq=None)


def map_session_result_with_witness(
    run: DshSdkRun,
    request: BaseModel,
    traces: list[ResearchTraceEvent],
    *,
    runtime_version: str,
    profile_ref: str,
    started_at: datetime,
    finished_at: datetime,
    accepted_synthesis_only: bool = False,
) -> MappedSessionResult:
    """Build the product result from model semantics and trusted runtime facts."""

    # Frozen receipts embed distinct generated DTO classes; cross their wire
    # boundary before constructing the public result and its nested models.
    request = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
    protocol = synthesis_protocol(request.output_schema_ref)
    if protocol == "decision_v2":
        return _map_perpetual_session_result(run, request, traces, runtime_version=runtime_version,
            profile_ref=profile_ref, started_at=started_at, finished_at=finished_at)
    action_synthesis = None
    incomplete = run.finish_reason != "completed"
    evidence = _trusted_evidence(run, request)
    facts = _trusted_facts(run, request, evidence)
    if incomplete and not evidence:
        raise AgentExecutionError(
            "dsh_session_incomplete",
            f"DSH session stopped with {run.finish_reason or 'no finish reason'}",
            retryable=run.finish_reason in {None, "error"},
            origin="dsh",
            cause_code=run.finish_reason or "missing_finish_reason",
        )
    if incomplete:
        # A terminal provider/model failure makes any in-flight narrative
        # semantically incomplete. Retain attested capability evidence, but do
        # not publish causal or directional output from the interrupted turn.
        synthesis = ResearchSynthesisCandidate(
            schema_version="research-synthesis-candidate.v1",
            request_id=request.request_id,
            causal_case=None,
            horizons=[],
        )
        selection = evidence_only_selection()
    else:
        if protocol == "decision":
            selection, action_synthesis = _submitted_decision_synthesis_with_witness(run)
        else:
            selection = _submitted_synthesis_with_witness(run)
        synthesis = selection.candidate
        assert synthesis is not None

    if synthesis.request_id != request.request_id:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "DSH synthesis request identity does not match the product request",
        )

    _validate_synthesis_evidence_refs(
        synthesis, request, evidence, accepted_only=accepted_synthesis_only,
    )
    if action_synthesis is not None:
        _validate_decision_synthesis(
            action_synthesis.synthesis,
            request,
            set(_synthesis_evidence_ids(request, evidence, accepted_only=accepted_synthesis_only)),
        )
    coverage = assess_evidence_sufficiency(
        request.evidence_requirements,
        evidence,
        cutoff_at=_coverage_cutoff(request, evidence, finished_at),
        facts=facts,
    )
    hard_gaps = [gap.requirement_id for gap in coverage.gaps if gap.importance == "hard"]
    trace_hash = _trace_hash(traces)
    tool_calls = sum(event.event_type == "tool_started" for event in traces)
    subagents = sum(event.event_type == "subagent_started" for event in traces)
    tool_invocations, tool_results = _trusted_tool_projection(run, request, traces)
    review_receipts = extract_attested_review_receipts(
        run.notifications,
        expected_session_id=run.session_id,
        # A reviewer may cite the canonical trigger/input evidence together
        # with newly acquired capability evidence.  The synthesis validator
        # already treats both sets as allowed; keep review attestation on the
        # same boundary so a valid receipt is not silently discarded.
        allowed_evidence_ids=frozenset(_allowed_evidence_ids(request, evidence)),
        fallback_created_at=finished_at,
    )

    research_round = ResearchRound(
        round=request.current_round,
        plan=_trusted_plan(
            request,
            started_at,
            allow_native_discovery=profile_ref.startswith("decision-research.web"),
        ),
        tool_invocations=tool_invocations,
        tool_results=tool_results,
        new_evidence_refs=[item.evidence_id for item in evidence],
        coverage=coverage,
        started_at=started_at,
        finished_at=finished_at,
    )
    stop_reason = ResearchStopReason(
        code="critical_data_unavailable" if hard_gaps else "sufficient",
        detail=(
            "The DSH round returned candidate Evidence for deterministic product validation."
            if evidence
            else "The DSH round returned no attested Evidence for the open requirements."
        ),
        bounded=bool(hard_gaps),
        remaining_hard_gaps=hard_gaps,
    )
    result = ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id=request.request_id,
        research_session_id=run.session_id,
        runtime_id="dsh",
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        trace_ref=f"dsh://session/{run.session_id}/trace/{trace_hash}",
        trace_hash=trace_hash,
        status="degraded" if hard_gaps or incomplete else "completed",
        rounds=[research_round],
        evidence_candidates=evidence,
        facts=facts,
        final_coverage=coverage,
        causal_case=synthesis.causal_case,
        horizons=synthesis.horizons,
        stop_reason=(
            stop_reason.model_copy(
                update={
                    "detail": (
                        f"DSH session was incomplete ({run.finish_reason or 'unknown'}) "
                        "but attested capability results were retained."
                    )
                }
            )
            if incomplete
            else stop_reason
        ),
        synthesis_failure_code=None,
        total_tool_calls=tool_calls,
        total_subagents=subagents,
        # Raw DSH usage exists, but this boundary has no reconciled run-wide
        # accounting across generations, retries, and subagents.
        total_tokens=None,
        estimated_cost_usd=None,
        context_manifest=request.context_manifest,
        reviews=[],
        started_at=started_at,
        finished_at=finished_at,
    )
    accepted_hash = _final_review_gate(run, request, selection, action_synthesis=action_synthesis)
    if action_synthesis is not None and accepted_hash:
        matched = [
            receipt
            for receipt in review_receipts
            if receipt.receipt_hash == accepted_hash and receipt.structured_receipt is not None
        ]
        try:
            if len(matched) != 1 or matched[0].wrapper_json is None:
                raise ValueError("review_missing_or_ambiguous")
            original = json.loads(matched[0].wrapper_json)
            if (
                original["schema_version"] != "decision-hub-frozen-review-result.v3"
                or ResearchDecisionSynthesisV1.model_validate_json(original["candidate_json"])
                != action_synthesis.synthesis
            ):
                raise ValueError("review_candidate_changed")
        except (ValueError, KeyError, TypeError) as exc:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "Decision synthesis matched review original differs",
                cause_code="decision_synthesis_review_missing",
            ) from exc
    if accepted_hash is not None:
        # A changed frozen candidate may keep bounded/rejected advisory history,
        # but no thesis-only accepted receipt can approve changed horizon fields.
        review_receipts = [receipt for receipt in review_receipts
            if (receipt.structured_receipt is None and receipt.resolution != "accepted")
            or receipt.receipt_hash == accepted_hash]
    else:
        # Structured V2 effects require the product's exact full-receipt gate;
        # old unwrapped native V1 review behavior remains available.
        review_receipts = [
            receipt for receipt in review_receipts if receipt.structured_receipt is None
        ]
    return MappedSessionResult(
        _attach_attested_review(result, review_receipts), selection, action_synthesis
    )


def _map_perpetual_session_result(
    run, request, traces, *, runtime_version, profile_ref, started_at, finished_at
):
    """V2 has its own native selection; legacy fields carry an evidence summary only."""
    # Execution receipts carry generated duplicate classes. Authenticate their
    # wire body, then use this port's canonical model for nested result fields.
    request = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
    evidence = _trusted_evidence(run, request)
    facts = _trusted_facts(run, request, evidence)
    incomplete = run.finish_reason != "completed"
    if incomplete and not evidence:
        raise AgentExecutionError(
            "dsh_session_incomplete",
            "Perpetual session has no completed native output or retained evidence",
        )
    perpetual = None
    if not incomplete:
        captures = extract_attested_perpetual_synthesis_captures(
            run.notifications, expected_session_id=run.session_id, request=request
        )
        if not captures:
            raise AgentExecutionError(
                "structured_output_invalid",
                "Perpetual synthesis requires its own successful attested native Tool result",
                cause_code="perpetual_synthesis_capture_missing",
            )
        capture = captures[-1]
        if capture.coverage_delivery is not None:
            evidence, facts = merge_finalized_coverage(evidence, facts, capture.coverage_delivery)

        def event_hash(index):
            event = run.notifications[index]
            return hashlib.sha256(
                json.dumps(
                    {"method": event.method, "payload": event.payload},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()

        native = ResearchNativeSelection.model_validate(
            dict(
                kind="tool_capture",
                final_response_hash=None,
                call_id=capture.call_id,
                call_event_index=capture.call_event_index,
                result_event_index=capture.result_event_index,
                payload_occurrence=capture.payload_occurrence,
                payload_path=list(capture.payload_path),
                call_event_hash=event_hash(capture.call_event_index),
                result_event_hash=event_hash(capture.result_event_index),
                call_seq=capture.call_seq,
                result_seq=capture.result_seq,
                origin_generation=None,
                presence_generation=None,
                presence_ref=None,
            )
        )
        perpetual = MappedPerpetualSynthesis(
            capture.synthesis,
            capture.review_request,
            capture.authorization,
            native,
            capture.raw_candidate_hash,
            capture.review_wrapper_json,
            capture.review_call_id,
            capture.review_call_event_index,
            capture.review_result_event_index,
        )
    coverage = assess_evidence_sufficiency(
        request.evidence_requirements,
        evidence,
        cutoff_at=_coverage_cutoff(request, evidence, finished_at),
        facts=facts,
    )
    hard_gaps = [gap.requirement_id for gap in coverage.gaps if gap.importance == "hard"]
    invocations, tool_results = _trusted_tool_projection(run, request, traces)
    trace_hash = _trace_hash(traces)
    summary = ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id=request.request_id,
        research_session_id=run.session_id,
        runtime_id="dsh",
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        trace_ref=f"dsh://session/{run.session_id}/trace/{trace_hash}",
        trace_hash=trace_hash,
        status="degraded" if hard_gaps or incomplete else "completed",
        rounds=[
            ResearchRound(
                round=request.current_round,
                plan=_trusted_plan(
                    request,
                    started_at,
                    allow_native_discovery=profile_ref.startswith("decision-research.web"),
                ),
                tool_invocations=invocations,
                tool_results=tool_results,
                new_evidence_refs=[row.evidence_id for row in evidence],
                coverage=coverage,
                started_at=started_at,
                finished_at=finished_at,
            )
        ],
        evidence_candidates=evidence,
        facts=facts,
        final_coverage=coverage,
        causal_case=None,
        horizons=[],
        reviews=[],
        stop_reason=ResearchStopReason(
            code="critical_data_unavailable" if hard_gaps or incomplete else "sufficient",
            detail="Transport evidence summary only; V2 candidate, probability and independent review remain in perpetual_synthesis.",
            bounded=bool(hard_gaps or incomplete),
            remaining_hard_gaps=hard_gaps,
        ),
        synthesis_failure_code=None,
        total_tool_calls=sum(row.event_type == "tool_started" for row in traces),
        total_subagents=sum(row.event_type == "subagent_started" for row in traces),
        total_tokens=None,
        estimated_cost_usd=None,
        context_manifest=request.context_manifest,
        started_at=started_at,
        finished_at=finished_at,
    )
    return MappedSessionResult(summary, evidence_only_selection(), perpetual_synthesis=perpetual)


def map_evidence_only_result(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    traces: list[ResearchTraceEvent],
    *,
    runtime_version: str,
    profile_ref: str,
    started_at: datetime,
    finished_at: datetime,
    failure_code: str,
    failure_cause: str,
) -> ResearchSessionResult:
    """Return a conservative result after semantic synthesis is rejected.

    The model's causal and horizon payload is deliberately discarded. Only
    capability results that passed the normal exact lineage/content checks are
    retained, so a malformed synthesis cannot erase useful facts or become a
    directional decision.
    """

    # Selected execution receipts carry generated duplicate nested DTO classes.
    # Revalidate their wire body at the same canonical boundary as the V2 mapper.
    request = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
    evidence = _trusted_evidence(run, request)
    facts = _trusted_facts(run, request, evidence)
    if not evidence:
        raise AgentExecutionError(
            failure_code,
            "Cannot retain an evidence-only result without trusted capability evidence",
            origin="orchestration",
            cause_code=failure_cause,
        )
    coverage = assess_evidence_sufficiency(
        request.evidence_requirements,
        evidence,
        cutoff_at=_coverage_cutoff(request, evidence, finished_at),
        facts=facts,
    )
    tool_invocations, tool_results = _trusted_tool_projection(run, request, traces)
    review_receipts = extract_attested_review_receipts(
        run.notifications,
        expected_session_id=run.session_id,
        allowed_evidence_ids=frozenset(_allowed_evidence_ids(request, evidence)),
        fallback_created_at=finished_at,
    )
    round_record = ResearchRound(
        round=request.current_round,
        plan=_trusted_plan(
            request,
            started_at,
            allow_native_discovery=profile_ref.startswith("decision-research.web"),
        ),
        tool_invocations=tool_invocations,
        tool_results=tool_results,
        new_evidence_refs=[item.evidence_id for item in evidence],
        coverage=coverage,
        started_at=started_at,
        finished_at=finished_at,
    )
    hard_gaps = [item.requirement_id for item in coverage.gaps if item.importance == "hard"]
    result = ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id=request.request_id,
        research_session_id=run.session_id,
        runtime_id="dsh",
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        trace_ref=f"dsh://session/{run.session_id}/trace/{_trace_hash(traces)}",
        trace_hash=_trace_hash(traces),
        status="degraded",
        rounds=[round_record],
        evidence_candidates=evidence,
        facts=facts,
        final_coverage=coverage,
        causal_case=None,
        horizons=[],
        stop_reason=ResearchStopReason(
            code="critical_data_unavailable",
            detail=(
                "Synthesis was rejected at evidence attestation "
                f"({failure_code}/{failure_cause}); {len(evidence)} trusted evidence item(s) "
                "were retained and all model semantics were discarded."
            ),
            bounded=True,
            remaining_hard_gaps=hard_gaps,
        ),
        synthesis_failure_code=failure_code,
        total_tool_calls=sum(item.event_type == "tool_started" for item in traces),
        total_subagents=sum(item.event_type == "subagent_started" for item in traces),
        total_tokens=None,
        estimated_cost_usd=None,
        context_manifest=request.context_manifest,
        reviews=[],
        started_at=started_at,
        finished_at=finished_at,
    )
    return _attach_attested_review(result, review_receipts)



class _FinalReviewBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    runId: str
    requestHash: str
    sessionId: str
    requestId: str
    generation: int = Field(ge=1)


class _FinalReviewGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[
        "decision-hub-final-review-gate.v1", "decision-hub-final-review-gate.v2"
    ]
    status: Literal["matched", "unreviewed"]
    reason: Literal["exact_target", "review_attempt_used", "subagent_budget_exhausted",
        "review_instruction_exhausted", "frozen_candidate_changed",
        "continuation_candidate_changed", "no_causal_candidate"]
    binding: _FinalReviewBinding
    candidate_json: str = Field(max_length=1_000_000)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_receipt_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    review_call_id: str | None


def _log_verified_final_gate_metadata(
    gate: _FinalReviewGate, selection: ResearchSynthesisSelectionWitness,
) -> None:
    """Log bounded verified metadata, without granting review qualification."""
    try:
        identifiers = (
            gate.binding.runId, gate.binding.sessionId,
            selection.call_id, gate.review_call_id,
        )
        if any(value is not None and len(value) > 256 for value in identifiers):
            return
        fields = {
            "status": gate.status, "reason": gate.reason,
            "run_id": gate.binding.runId, "session_id": gate.binding.sessionId,
            "generation": gate.binding.generation,
            "candidate_sha256": gate.candidate_sha256,
            "receipt_sha256": gate.review_receipt_hash,
            "selected_call_id": selection.call_id,
            "review_call_id": gate.review_call_id,
        }
        encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        if len(encoded) > 2048:
            return
        # Worker logs may have no INFO handler; WARNING retains this diagnostic.
        logging.getLogger(__name__).warning("research_final_review_gate_metadata %s", encoded)
    except Exception:
        # Observation must not change validation, adoption or result delivery.
        pass


def _final_review_gate(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    selection: ResearchSynthesisSelectionWitness,
    *,
    action_synthesis: MappedDecisionSynthesis | None = None,
) -> str | None:
    """Read only the adjacent, successful product Tool's closed review proof.

    None retains the old legacy boundary. Empty string prohibits accepted
    receipts, while a hash selects the exact attested final receipt. This
    coordinates review; Kernel still verifies native ownership/time/refs/hash.
    """
    index = selection.result_event_index
    if selection.kind != "tool_capture" or index is None:
        return None
    notification = run.notifications[index]
    event = notification.payload.get("event")
    data = event.get("data") if isinstance(event, Mapping) else None
    decision = action_synthesis is not None
    wrappers = synthesis_tool_result_wrappers(data, decision=decision)
    if not wrappers:
        if decision:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "Decision synthesis review gate is missing",
                cause_code="final_review_gate_invalid",
            )
        return None
    try:
        path = (
            tuple(action_synthesis.selection.payload_path or ())
            if action_synthesis is not None
            else selection.payload_path
        )
        adjacent = [(row, wrapper_path) for row, wrapper_path in wrappers
            if path == ("payload", "event", "data", *wrapper_path, "candidate")]
        if len(adjacent) != 1:
            raise ValueError("non_adjacent_gate")
        wrapper, _ = adjacent[0]
        if set(wrapper) != {"schema_version", "candidate", "review_gate"}:
            raise ValueError("wrapper_fields")
        gate = _FinalReviewGate.model_validate(wrapper["review_gate"])
        if gate.schema_version != (
            "decision-hub-final-review-gate.v2" if decision else "decision-hub-final-review-gate.v1"
        ):
            raise ValueError("gate_protocol")
        model = ResearchDecisionSynthesisV1 if decision else ResearchSynthesisCandidate
        candidate = model.model_validate_json(gate.candidate_json)
        if (
            action_synthesis is not None
            and hashlib.sha256(
                json.dumps(
                    json.loads(gate.candidate_json),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            != action_synthesis.raw_candidate_hash
        ):
            raise ValueError("candidate_raw_payload")
        if (
            candidate
            != (action_synthesis.synthesis if action_synthesis is not None else selection.candidate)
            or model.model_validate(wrapper["candidate"]) != candidate
            or hashlib.sha256(gate.candidate_json.encode("utf8")).hexdigest()
            != gate.candidate_sha256
        ):
            raise ValueError("candidate_bytes")
        binding = gate.binding
        digest = hashlib.sha256(
            f"dsh-host-bridge.v1\0{request.run_id}\0{binding.requestHash}".encode()
        ).hexdigest()
        rpc_digest = digest if binding.generation == 1 else hashlib.sha256(
            f"dsh-host-turn.v1\0{request.run_id}\0{binding.requestHash}\0{binding.generation}".encode()
        ).hexdigest()
        if (binding.runId != request.run_id or binding.requestHash != research_request_hash(request)
                or binding.sessionId != run.session_id or binding.sessionId != f"dsh_{digest}"
                or binding.requestId != f"req_{rpc_digest}"):
            raise ValueError("binding_scope")
        research = (
            candidate.research_candidate_v1
            if isinstance(candidate, ResearchDecisionSynthesisV1)
            else candidate
        )
        if gate.reason == "no_causal_candidate" and research.causal_case is not None:
            raise ValueError("causal_candidate_present")
        if gate.status == "unreviewed":
            if (gate.reason == "exact_target" or gate.review_receipt_hash is not None
                    or gate.review_call_id is not None):
                raise ValueError("unreviewed_receipt")
            _log_verified_final_gate_metadata(gate, selection)
            return ""
        if (gate.reason != "exact_target" or gate.review_receipt_hash is None
                or not gate.review_call_id):
            raise ValueError("matched_receipt")
        # The matching native receipt is still extracted and checked through
        # the unchanged strict pair/refs/time boundary; this hash grants nothing.
        _log_verified_final_gate_metadata(gate, selection)
        return gate.review_receipt_hash
    except (ValueError, TypeError, ValidationError) as exc:
        raise AgentExecutionError(
            "dsh_protocol_invalid", "Final review Tool proof is invalid",
            cause_code="final_review_gate_invalid",
        ) from exc


def _attach_attested_review(
    result: ResearchSessionResult,
    receipts: list[AttestedReviewReceipt],
) -> ResearchSessionResult:
    """Bind the latest attested receipt to the immutable mapped result version."""

    # A bounded same-session repair/continuation can retain the previous
    # generation's reviewer events in the DSH transcript.  That receipt is
    # valid evidence about the earlier result, but it is outside this mapped
    # result's immutable time interval.  Passing it through makes the worker
    # reject an otherwise usable result as ``research_native_review_time_mismatch``.
    # Leave it for the deterministic worker review instead of relabelling it
    # as a review of the new result version.
    receipts = [
        receipt
        for receipt in receipts
        if result.started_at <= receipt.created_at <= result.finished_at
    ]
    if result.causal_case is not None:
        exact = [
            receipt for receipt in receipts if receipt.target_claim == result.causal_case.thesis
        ]
        if exact:
            receipts = exact
        else:
            # A bounded/rejected receipt may legitimately target synthesis Vn
            # and cause the manager to produce Vn+1. Preserve that real
            # challenge as advisory revision history. An accepted receipt for
            # Vn can never approve a changed Vn+1 thesis.
            receipts = [receipt for receipt in receipts if receipt.resolution != "accepted"]
    if not receipts:
        return result
    receipt = receipts[-1]
    review = ResearchReview(
        review_id=f"review_{(receipt.summary_hash or receipt.receipt_hash)[:32]}",
        role=receipt.role,
        target_claim=receipt.target_claim,
        evidence_refs=receipt.evidence_refs,
        result_hash=research_result_content_hash(result),
        challenge=receipt.challenge,
        resolution=receipt.resolution,
        limitations=receipt.limitations,
        created_at=receipt.created_at,
    )
    return result.model_copy(update={"reviews": [review]})


def _coverage_cutoff(
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
    finished_at: datetime,
) -> datetime:
    if request.execution_mode == "replay":
        return request.pit_cutoff_at
    # Live evidence is assessed at the trusted runtime completion instant, not
    # at the future Run deadline or a model-supplied timestamp.
    return max((finished_at, *(item.received_at for item in evidence)))


def _trusted_result_sources(run: DshSdkRun, request: ResearchSessionRequest):
    for attested in extract_attested_capability_results(run.notifications):
        yield attested.result, _result_belongs_to_request(
            attested.result.request_id, request.request_id,
            attested.tool_call_id, attested.declared_request_id)
    for result in extract_server_baseline_results(run.notifications, request,
                                                   expected_session_id=run.session_id):
        yield result, True


def _trusted_evidence(run: DshSdkRun, request: ResearchSessionRequest) -> list[EvidenceCandidate]:
    allowed = frozenset(request.allowed_capabilities)
    by_id: dict[str, EvidenceCandidate] = {}
    fingerprints: dict[str, str] = {}
    for result, belongs in _trusted_result_sources(run, request):
        if result.capability_id not in allowed or not belongs:
            continue
        for evidence in result.evidence_candidates:
            if evidence.research_session_id != run.session_id:
                continue
            fingerprint = _evidence_fingerprint(evidence)
            previous = fingerprints.get(evidence.evidence_id)
            if previous is not None and previous != fingerprint:
                raise AgentExecutionError(
                    "dsh_evidence_unattested",
                    "allowed MCP results returned conflicting payloads for one evidence id",
                    cause_code="capability_evidence_conflict",
                )
            fingerprints[evidence.evidence_id] = fingerprint
            by_id[evidence.evidence_id] = evidence
    return list(by_id.values())


def _trusted_facts(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
) -> list[FactEnvelope]:
    allowed = frozenset(request.allowed_capabilities)
    evidence_ids = {item.evidence_id for item in evidence}
    by_id: dict[str, FactEnvelope] = {}
    fingerprints: dict[str, str] = {}
    for result, belongs in _trusted_result_sources(run, request):
        if result.capability_id not in allowed or not belongs:
            continue
        for fact in result.facts or []:
            if fact.evidence_id not in evidence_ids:
                raise AgentExecutionError(
                    "dsh_fact_unattested",
                    "allowed MCP result returned a fact without retained evidence",
                    cause_code="fact_evidence_missing",
                )
            fingerprint = _fact_fingerprint(fact)
            previous = fingerprints.get(fact.fact_id)
            if previous is not None and previous != fingerprint:
                raise AgentExecutionError(
                    "dsh_fact_unattested",
                    "allowed MCP results returned conflicting payloads for one fact id",
                    cause_code="capability_fact_conflict",
                )
            fingerprints[fact.fact_id] = fingerprint
            by_id[fact.fact_id] = fact
    return list(by_id.values())


def _fact_fingerprint(fact: FactEnvelope) -> str:
    return json.dumps(
        fact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_synthesis_evidence_refs(
    synthesis: ResearchSynthesisCandidate,
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
    *,
    accepted_only: bool = False,
) -> None:
    errors = _unattested_synthesis_ref_errors(
        synthesis,
        set(_synthesis_evidence_ids(request, evidence, accepted_only=accepted_only)),
    )
    if errors:
        raise AgentExecutionError(
            "dsh_evidence_unattested",
            "DSH synthesis references Evidence absent from the input or allowed MCP results",
            cause_code="synthesis_attestation",
        )


def trusted_evidence_ids(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    *,
    accepted_only: bool = False,
) -> list[str]:
    """Return the exact Evidence IDs a synthesis may reference."""

    return _synthesis_evidence_ids(
        request, _trusted_evidence(run, request), accepted_only=accepted_only,
    )


def synthesis_evidence_ref_errors(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    *,
    accepted_only: bool = False,
) -> list[dict[str, object]]:
    """Describe only invalid synthesis references; never guess a replacement."""

    try:
        if synthesis_protocol(request.output_schema_ref) == "decision":
            _selection, decision = _submitted_decision_synthesis_with_witness(run)
            return _decision_ref_errors(
                decision.synthesis,
                set(trusted_evidence_ids(run, request, accepted_only=accepted_only)),
            )
        synthesis = _submitted_synthesis(run)
    except AgentExecutionError:
        return []
    return _unattested_synthesis_ref_errors(
        synthesis,
        set(trusted_evidence_ids(run, request, accepted_only=accepted_only)),
    )


def _submitted_decision_synthesis_with_witness(
    run: DshSdkRun,
) -> tuple[ResearchSynthesisSelectionWitness, MappedDecisionSynthesis]:
    captures = extract_attested_decision_synthesis_captures(
        run.notifications, expected_session_id=run.session_id
    )
    if not captures:
        raise AgentExecutionError(
            "structured_output_invalid",
            "Decision synthesis requires its attested native Tool result",
            cause_code="decision_synthesis_capture_missing",
        )
    capture = captures[-1]
    projected = capture.projection

    def event_hash(index: int) -> str:
        event = run.notifications[index]
        return hashlib.sha256(
            json.dumps(
                {"method": event.method, "payload": event.payload},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()

    shared = dict(
        kind="tool_capture",
        final_response_hash=None,
        call_id=projected.call_id,
        call_event_index=projected.call_event_index,
        result_event_index=projected.result_event_index,
        payload_occurrence=projected.payload_occurrence,
        call_event_hash=event_hash(projected.call_event_index),
        result_event_hash=event_hash(projected.result_event_index),
        call_seq=projected.call_seq,
        result_seq=projected.result_seq,
        origin_generation=None,
        presence_generation=None,
        presence_ref=None,
    )
    old = ResearchSynthesisSelectionWitness.model_validate(
        {
            **shared,
            "candidate": projected.candidate,
            "candidate_payload_hash": projected.candidate_payload_hash,
            "payload_path": projected.payload_path,
        }
    )
    native = ResearchNativeSelection.model_validate(
        {**shared, "payload_path": list(capture.payload_path)}
    )
    return old, MappedDecisionSynthesis(
        capture.synthesis, native, capture.raw_candidate_hash, projected.candidate_payload_hash
    )


def _decision_ref_errors(
    synthesis: ResearchDecisionSynthesisV1, allowed: set[str]
) -> list[dict[str, object]]:
    errors = _unattested_synthesis_ref_errors(synthesis.research_candidate_v1, allowed)
    entries = []
    for index, intent in enumerate(synthesis.action_intents):
        entries.extend(
            (["action_intents", str(index), "evidence_refs", str(j)], ref)
            for j, ref in enumerate(intent.evidence_refs)
        )
    # Claim references are declarations checked against the frozen Claim store
    # by the Action reader. This parser does not promote them into Evidence.
    declared_claims = set(synthesis.claim_signal_refs)
    for index, mechanism in enumerate(synthesis.mechanism_reviews):
        prefix = ["mechanism_reviews", str(index)]
        for j, node in enumerate(mechanism.nodes):
            entries.extend(
                ([*prefix, "nodes", str(j), "refs", str(k)], ref)
                for k, ref in enumerate(node.refs)
                if ref not in declared_claims
            )
        for j, edge in enumerate(mechanism.edges):
            for name in ("support_refs", "counter_refs"):
                entries.extend(
                    ([*prefix, "edges", str(j), name, str(k)], ref)
                    for k, ref in enumerate(getattr(edge, name))
                    if ref not in declared_claims
                )
        entries.extend(
            ([*prefix, "priced_in_refs", str(k)], ref)
            for k, ref in enumerate(mechanism.priced_in_refs)
            if ref not in declared_claims
        )
    errors.extend(
        {
            "loc": path,
            "type": "evidence_ref_unattested",
            "msg": f"{ref} is not an allowed Evidence ID",
            "evidence_ref": ref,
        }
        for path, ref in entries
        if ref not in allowed
    )
    return errors


def _validate_decision_synthesis(
    synthesis: ResearchDecisionSynthesisV1, request: ResearchSessionRequest, allowed: set[str]
) -> None:
    if synthesis.request_id != request.request_id or any(
        item.run_id != request.run_id for item in synthesis.mechanism_reviews
    ):
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "Decision synthesis request identity differs",
            cause_code="decision_synthesis_request_mismatch",
        )
    if _decision_ref_errors(synthesis, allowed):
        raise AgentExecutionError(
            "dsh_evidence_unattested",
            "Decision synthesis references unattested evidence",
            cause_code="synthesis_attestation",
        )


def _submitted_synthesis(run: DshSdkRun) -> ResearchSynthesisCandidate:
    selection = _submitted_synthesis_with_witness(run)
    assert selection.candidate is not None
    return selection.candidate


def _submitted_synthesis_with_witness(run: DshSdkRun) -> ResearchSynthesisSelectionWitness:
    # Web runtime submits through a tool and deliberately has no final prose.
    # Validation and repair must inspect the same latest attested candidate;
    # otherwise a rejected tool citation produces an empty repair-error list.
    captures = extract_attested_synthesis_captures(
        run.notifications, expected_session_id=run.session_id,
    )
    if captures:
        capture = captures[-1]

        def event_hash(index: int) -> str:
            notification = run.notifications[index]
            encoded = json.dumps({"method": notification.method, "payload": notification.payload},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            return hashlib.sha256(encoded.encode()).hexdigest()

        return ResearchSynthesisSelectionWitness(kind="tool_capture", candidate=capture.candidate,
            candidate_payload_hash=capture.candidate_payload_hash, final_response_hash=None,
            call_id=capture.call_id, call_event_index=capture.call_event_index,
            result_event_index=capture.result_event_index,
            payload_occurrence=capture.payload_occurrence,
            payload_path=capture.payload_path,
            call_event_hash=event_hash(capture.call_event_index),
            result_event_hash=event_hash(capture.result_event_index),
            call_seq=capture.call_seq, result_seq=capture.result_seq)
    try:
        candidate = ResearchSynthesisCandidate.model_validate_json(run.final_response)
        encoded = json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return ResearchSynthesisSelectionWitness(kind="structured_response", candidate=candidate,
            candidate_payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            final_response_hash=hashlib.sha256(run.final_response.encode()).hexdigest(),
            call_id=None, call_event_index=None, result_event_index=None, payload_occurrence=None,
            payload_path=None,
            call_event_hash=None, result_event_hash=None, call_seq=None, result_seq=None)
    except ValidationError as exc:
        raise AgentExecutionError(
            "structured_output_invalid",
            "DSH returned neither an attested synthesis Tool result nor a response "
            "that satisfies research-synthesis-candidate.v1",
        ) from exc


def _allowed_evidence_ids(
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
) -> list[str]:
    refs = {item.evidence_id for item in request.input_evidence}
    refs.update(item.evidence_id for item in evidence)
    return sorted(refs)


def _synthesis_evidence_ids(
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
    *,
    accepted_only: bool,
) -> list[str]:
    # Native V2 Gateway outputs carry their actual admission disposition.
    # A successful tool/call pair attests a candidate's origin; it does not
    # make candidate/stale/rejected material valid support for a claim.
    # Legacy adapters retain their original post-mapping admission boundary.
    # This whitelist is only an early repair hint: Graph still checks the
    # original database Evidence and frozen inputs before publication.
    retained = (
        [item for item in evidence if item.quality == "accepted"] if accepted_only else evidence
    )
    return _allowed_evidence_ids(request, retained)


def _unattested_synthesis_ref_errors(
    synthesis: ResearchSynthesisCandidate,
    allowed_refs: set[str],
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for path, evidence_ref in _synthesis_evidence_ref_entries(synthesis):
        if evidence_ref not in allowed_refs:
            errors.append(
                {
                    "loc": path,
                    "type": "evidence_ref_unattested",
                    "msg": f"{evidence_ref} is not an allowed Evidence ID",
                }
            )
    return errors


def _synthesis_evidence_ref_entries(
    synthesis: ResearchSynthesisCandidate,
) -> list[tuple[list[str], str]]:
    entries: list[tuple[list[str], str]] = []
    if synthesis.causal_case is not None:
        for index, evidence_ref in enumerate(synthesis.causal_case.evidence_refs):
            entries.append((["causal_case", "evidence_refs", str(index)], evidence_ref))
        for chain_name, chain in (
            ("main_chain", synthesis.causal_case.main_chain),
            ("opposite_chain", synthesis.causal_case.opposite_chain),
        ):
            for link_index, link in enumerate(chain):
                for ref_index, evidence_ref in enumerate(link.evidence_refs):
                    entries.append(
                        (
                            [
                                "causal_case",
                                chain_name,
                                str(link_index),
                                "evidence_refs",
                                str(ref_index),
                            ],
                            evidence_ref,
                        )
                    )
    for horizon_index, horizon in enumerate(synthesis.horizons):
        for ref_index, evidence_ref in enumerate(horizon.evidence_refs):
            entries.append(
                (
                    [
                        "horizons",
                        str(horizon_index),
                        "evidence_refs",
                        str(ref_index),
                    ],
                    evidence_ref,
                )
            )
    return entries


def _trusted_plan(
    request: ResearchSessionRequest,
    created_at: datetime,
    *,
    allow_native_discovery: bool = False,
) -> ResearchPlan:
    requirement_by_id = {item.requirement_id: item for item in request.evidence_requirements}
    target_ids = [gap.requirement_id for gap in request.target_gaps]
    requirements = [
        requirement_by_id[item] for item in target_ids if item in requirement_by_id
    ] or list(request.evidence_requirements)
    allowed = tuple(request.allowed_capabilities)
    tasks: list[ResearchTask] = []
    for index, item in enumerate(requirements, start=1):
        task_id = f"round-{request.current_round}:{item.requirement_id}"
        # The policy permits at most two independent acquisition tasks. The
        # first pair is independent; each later task depends on the task two
        # positions before it, so a dependency-aware executor can have at
        # most two ready tasks.
        depends_on = [tasks[index - 3].task_id] if index >= 3 else []
        tasks.append(
            ResearchTask(
                task_id=task_id,
                requirement_id=item.requirement_id,
                capability_id=_preferred_capability(
                    item.preferred_capabilities,
                    allowed,
                    execution_mode=request.execution_mode,
                    allow_native_discovery=allow_native_discovery,
                ),
                objective=item.description,
                question=item.description,
                input_evidence_refs=list(request.evidence_refs),
                output_schema_ref="research-capability-result.v1",
                depends_on=depends_on,
                success_condition=f"Acquire attested Evidence for {item.requirement_id}.",
                priority=index,
            )
        )
    return ResearchPlan(
        plan_id=f"dsh-plan:{request.request_id}:round:{request.current_round}",
        objective=f"Resolve the bounded evidence gaps for round {request.current_round}.",
        tasks=tasks,
        required_capabilities=list(
            dict.fromkeys(
                _preferred_capability(
                    item.preferred_capabilities,
                    allowed,
                    execution_mode=request.execution_mode,
                    allow_native_discovery=allow_native_discovery,
                )
                for item in requirements
            )
        ),
        created_at=created_at,
    )


def _preferred_capability(
    preferred: list[str],
    allowed: tuple[str, ...],
    *,
    execution_mode: Literal["live", "replay"],
    allow_native_discovery: bool = False,
) -> str:
    for capability_id in preferred:
        if capability_id in allowed:
            return capability_id
    # Replay is a transport-level fixture adapter, not a domain source. It is
    # allowed to service any requirement only when the request is explicitly
    # replay-scoped; live requests must resolve through their declared ladder.
    if execution_mode == "replay" and "replay.research" in allowed:
        return "replay.research"
    # Official DSH Web can use its native search tool as a discovery-only
    # capability. It is intentionally not part of the Hub allowlist and can
    # never create business Evidence; a later Hub Fetch/typed provider call is
    # still required for attestation.
    if allow_native_discovery and "web.search" in preferred:
        return "dsh.native.web_search"
    raise AgentExecutionError(
        "research_capability_unavailable",
        "no allowed capability satisfies the requirement capability ladder",
        capability_id=preferred[0] if preferred else None,
    )


def _trusted_tool_projection(
    run: DshSdkRun,
    request: ResearchSessionRequest,
    traces: list[ResearchTraceEvent],
) -> tuple[list[ToolInvocation], list[ToolResultSummary]]:
    call_targets = extract_capability_call_targets(
        run.notifications, expected_session_id=run.session_id,
    )
    known_requirements = {item.requirement_id for item in request.evidence_requirements}
    attested = {
        item.tool_call_id: item.result
        for item in extract_attested_capability_results(run.notifications)
        if _result_belongs_to_request(
            item.result.request_id,
            request.request_id,
            item.tool_call_id,
            item.declared_request_id,
        )
        and item.result.capability_id in request.allowed_capabilities
    }
    # Surface replacements retain the call identity but are not another execution.
    completed: dict[tuple[str | None, str], ResearchTraceEvent] = {}
    for item in traces:
        if item.reference_id is not None and item.event_type in {"tool_completed", "tool_failed"}:
            completed.setdefault((item.reference_type, item.reference_id), item)
    invocations: list[ToolInvocation] = []
    results: list[ToolResultSummary] = []
    attempts: dict[str, int] = {}
    for trace in traces:
        if trace.event_type != "tool_started":
            continue
        call_id = trace.reference_id or f"dsh-trace-{trace.sequence_no}"
        result = attested.get(call_id)
        target = call_targets.get(call_id)
        tool_name = _tool_name_from_trace(trace)
        terminal = completed.get((trace.reference_type, call_id))
        capability_id = (
            result.capability_id
            if result is not None
            else terminal.error.capability_id
            if terminal is not None
            and terminal.error is not None
            and terminal.error.capability_id in request.allowed_capabilities
            else target[0]
            if target is not None and target[0] in request.allowed_capabilities
            else _capability_from_tool(tool_name, request)
        )
        attempts[capability_id] = attempts.get(capability_id, 0) + 1
        status = (
            "running"
            if terminal is None
            else "succeeded"
            if terminal.event_type == "tool_completed"
            else "failed"
        )
        finished_at = terminal.occurred_at if terminal is not None else None
        latency_ms = (
            max(0, round((finished_at - trace.occurred_at).total_seconds() * 1000))
            if finished_at is not None
            else None
        )
        invocations.append(
            ToolInvocation(
                tool_call_id=call_id,
                capability_id=capability_id,
                requirement_id=(
                    target[1] if target is not None and target[1] in known_requirements else None
                ),
                tool_name=tool_name,
                query_summary=f"Execute the bounded {capability_id} capability.",
                started_at=trace.occurred_at,
                finished_at=finished_at,
                status=status,
                attempt=attempts[capability_id],
                latency_ms=latency_ms,
                cost_usd=result.cost_usd if result is not None else None,
                error_code=terminal.error_code if terminal is not None else None,
                error=terminal.error if terminal is not None else None,
            )
        )
        if terminal is None:
            continue
        result_status = "succeeded" if terminal.event_type == "tool_completed" else "failed"
        evidence_refs = (
            [item.evidence_id for item in result.evidence_candidates] if result is not None else []
        )
        content_hash = (
            hashlib.sha256(result.model_dump_json().encode("utf-8")).hexdigest()
            if result is not None
            else None
        )
        results.append(
            ToolResultSummary(
                tool_call_id=call_id,
                status=result_status,
                summary=(
                    f"The {capability_id} capability returned {len(evidence_refs)} "
                    "attested evidence item(s)."
                    if result_status == "succeeded"
                    else f"The {capability_id} capability failed closed."
                ),
                evidence_refs=evidence_refs,
                content_ref=None,
                content_hash=content_hash,
                received_at=terminal.occurred_at,
                error_code=terminal.error_code,
                error=terminal.error,
            )
        )
    return invocations, results


def _tool_name_from_trace(trace: ResearchTraceEvent) -> str:
    prefix = "The research session called "
    suffix = "."
    if trace.summary.startswith(prefix) and trace.summary.endswith(suffix):
        return trace.summary[len(prefix) : -len(suffix)]
    return "dsh_tool"


def _result_belongs_to_request(
    result_request_id: str,
    product_request_id: str,
    tool_call_id: str | None,
    declared_request_id: str | None,
) -> bool:
    """Accept product- or tool-call-scoped IDs for this exact DSH run.

    The canonical query contract intentionally uses a unique request ID for
    each capability attempt. DSH commonly returns that call ID (for example
    ``dh-001-event-feed``) in ``ResearchCapabilityResult.request_id`` rather
    than repeating the outer product request ID. The attested tool-call ID is
    the binding that proves the result came from this session; it is safe to
    accept only an exact match here, while preserving the existing product
    request and product-request-prefixed forms.
    """
    if result_request_id == product_request_id or result_request_id.startswith(
        f"{product_request_id}:"
    ):
        return True
    return (tool_call_id is not None and result_request_id == tool_call_id) or (
        declared_request_id is not None and result_request_id == declared_request_id
    )


def _capability_from_tool(tool_name: str, request: ResearchSessionRequest) -> str:
    if (
        tool_name.endswith(("decision_hub_research", "research_capability_execute"))
        and len(request.allowed_capabilities) == 1
    ):
        return request.allowed_capabilities[0]
    if tool_name == "web_search":
        return "dsh.native.web_search"
    if tool_name == "web_fetch":
        return "dsh.native.web_fetch"
    normalized = "".join(
        character if character.isalnum() or character in {".", "_", "-"} else "_"
        for character in tool_name
    )
    return f"dsh.internal.{normalized}"


def _trace_hash(traces: list[ResearchTraceEvent]) -> str:
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in traces],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_fingerprint(candidate: EvidenceCandidate) -> str:
    return json.dumps(
        candidate.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
