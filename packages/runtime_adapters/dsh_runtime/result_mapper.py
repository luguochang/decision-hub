from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchPlan,
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
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkRun
from .tool_result_attestation import (
    extract_attested_capability_results,
    extract_attested_synthesis_candidates,
)


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
    """Build the product result from model semantics and trusted runtime facts."""

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
    captures = extract_attested_synthesis_candidates(
        run.notifications,
        expected_session_id=run.session_id,
    )
    if captures:
        synthesis = captures[-1]
    else:
        try:
            synthesis = ResearchSynthesisCandidate.model_validate_json(run.final_response)
        except ValidationError as exc:
            if incomplete:
                synthesis = ResearchSynthesisCandidate(
                    schema_version="research-synthesis-candidate.v1",
                    request_id=request.request_id,
                    causal_case=None,
                    horizons=[],
                )
            else:
                raise AgentExecutionError(
                    "structured_output_invalid",
                    "DSH returned neither an attested synthesis Tool result nor a response "
                    "that satisfies research-synthesis-candidate.v1",
                ) from exc

    if synthesis.request_id != request.request_id:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "DSH synthesis request identity does not match the product request",
        )

    _validate_synthesis_evidence_refs(synthesis, request, evidence)
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
    return ResearchSessionResult(
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
        # The current SDK does not expose trusted aggregate token/cost usage.
        total_tokens=None,
        estimated_cost_usd=None,
        started_at=started_at,
        finished_at=finished_at,
    )


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
    return ResearchSessionResult(
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
        started_at=started_at,
        finished_at=finished_at,
    )


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


def _trusted_evidence(run: DshSdkRun, request: ResearchSessionRequest) -> list[EvidenceCandidate]:
    allowed = frozenset(request.allowed_capabilities)
    by_id: dict[str, EvidenceCandidate] = {}
    fingerprints: dict[str, str] = {}
    for attested in extract_attested_capability_results(run.notifications):
        result = attested.result
        if result.capability_id not in allowed:
            continue
        if not _result_belongs_to_request(
            result.request_id,
            request.request_id,
            attested.tool_call_id,
            attested.declared_request_id,
        ):
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
    for attested in extract_attested_capability_results(run.notifications):
        result = attested.result
        if result.capability_id not in allowed or not _result_belongs_to_request(
            result.request_id,
            request.request_id,
            attested.tool_call_id,
            attested.declared_request_id,
        ):
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
) -> None:
    errors = _unattested_synthesis_ref_errors(
        synthesis,
        set(_allowed_evidence_ids(request, evidence)),
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
) -> list[str]:
    """Return the exact Evidence IDs a synthesis may reference."""

    return _allowed_evidence_ids(request, _trusted_evidence(run, request))


def synthesis_evidence_ref_errors(
    run: DshSdkRun,
    request: ResearchSessionRequest,
) -> list[dict[str, object]]:
    """Describe only invalid synthesis references; never guess a replacement."""

    try:
        synthesis = ResearchSynthesisCandidate.model_validate_json(run.final_response)
    except ValidationError:
        return []
    return _unattested_synthesis_ref_errors(
        synthesis,
        set(trusted_evidence_ids(run, request)),
    )


def _allowed_evidence_ids(
    request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
) -> list[str]:
    refs = {item.evidence_id for item in request.input_evidence}
    refs.update(item.evidence_id for item in evidence)
    return sorted(refs)


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
            entries.append(
                (["causal_case", "evidence_refs", str(index)], evidence_ref)
            )
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
    tasks = [
        ResearchTask(
            task_id=f"round-{request.current_round}:{item.requirement_id}",
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
            depends_on=[],
            success_condition=f"Acquire attested Evidence for {item.requirement_id}.",
            priority=index,
        )
        for index, item in enumerate(requirements, start=1)
    ]
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
    completed = {
        item.reference_id: item
        for item in traces
        if item.reference_id is not None
        and item.event_type in {"tool_completed", "tool_failed"}
    }
    invocations: list[ToolInvocation] = []
    results: list[ToolResultSummary] = []
    attempts: dict[str, int] = {}
    for trace in traces:
        if trace.event_type != "tool_started":
            continue
        call_id = trace.reference_id or f"dsh-trace-{trace.sequence_no}"
        result = attested.get(call_id)
        tool_name = _tool_name_from_trace(trace)
        terminal = completed.get(call_id)
        capability_id = (
            result.capability_id
            if result is not None
            else terminal.error.capability_id
            if terminal is not None
            and terminal.error is not None
            and terminal.error.capability_id in request.allowed_capabilities
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
            [item.evidence_id for item in result.evidence_candidates]
            if result is not None
            else []
        )
        content_hash = (
            hashlib.sha256(
                result.model_dump_json().encode("utf-8")
            ).hexdigest()
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
    return (
        (tool_call_id is not None and result_request_id == tool_call_id)
        or (
            declared_request_id is not None
            and result_request_id == declared_request_id
        )
    )


def _capability_from_tool(
    tool_name: str, request: ResearchSessionRequest
) -> str:
    if tool_name.endswith(("decision_hub_research", "research_capability_execute")) and len(
        request.allowed_capabilities
    ) == 1:
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
