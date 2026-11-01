"""Exact native reviewer effects; no model or read-latest qualification writer."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    ResearchSessionResult,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import ResearchChallengeV1
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservation,
    ResearchNativeObservationContext,
    ResearchSynthesisSelectionWitness,
)
from packages.kernel.decision_hub_kernel.ports.research_review_effects import (
    ResearchReviewEffectOriginal,
    ResearchReviewEffects,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchDecisionSynthesisV1
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import synthesis_protocol

from .provider_input_receipts import borrowed_provider_read, row_reference, sha, strict_json
from .research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
    verify_selection_witness,
)
from .research_review import research_result_content_hash


@dataclass(frozen=True)
class PerpetualReviewTransportEffect:
    """View of the immutable carrier; no V1 review or financial grant is produced."""
    mapped_ref: ObservedExecutionRef
    source_result_hash: str
    native_authorization_verdict: str | None
    financial_qualification: str = "unqualified"
    authorizes_publication: bool = False


def _perpetual_transport_effect(session, mapped_ref, source_result):
    from .research_publication_binding import read_mapped_perpetual_observation
    mapped = read_mapped_perpetual_observation(session, mapped_ref)
    if mapped.result != source_result or source_result.horizons or source_result.causal_case or source_result.reviews:
        raise ValueError("research_perpetual_review_transport_mismatch")
    authorization = mapped.perpetual_synthesis.authorization
    return PerpetualReviewTransportEffect(mapped_ref, research_result_content_hash(source_result),
                                         authorization.verdict if authorization else None)


def _address(mapped_ref: ObservedExecutionRef) -> tuple[str, str]:
    digest = sha(
        "research-review-effect.v1\0" + mapped_ref.output_id + "\0" + mapped_ref.payload_hash
    )[:40]
    return "review_effect_" + digest, "review_effect:" + digest


def _at(value: Any, path: tuple[str | int | None, ...]) -> Any:
    for step in path:
        value = strict_json(value) if step is None else value[step]
    return value


def _normalized_freeze_candidate(value: Any) -> ResearchSynthesisCandidate:
    """Same representation-only repairs as the actual synthesis Tool freeze.

    No claim/evidence ID is invented. The raw call and the full product-owned
    normalized candidate remain independently retained in the transport.
    """
    if not isinstance(value, dict):
        return ResearchSynthesisCandidate.model_validate(value)
    value = deepcopy(value)
    case = value.get("causal_case")
    if (
        "horizons" not in value
        and isinstance(case, dict)
        and isinstance(case.get("horizons"), list)
    ):
        value["horizons"] = case.pop("horizons")

    def unique(refs: Any) -> Any:
        return (
            list(dict.fromkeys(refs))
            if isinstance(refs, list) and all(isinstance(item, str) for item in refs)
            else refs
        )

    if isinstance(value.get("horizons"), list):
        for item in value["horizons"]:
            if isinstance(item, dict) and "evidence_refs" in item:
                item["evidence_refs"] = unique(item["evidence_refs"])
    if isinstance(case, dict):
        cited = []
        for name in ("main_chain", "opposite_chain"):
            if isinstance(case.get(name), list):
                for link in case[name]:
                    if isinstance(link, dict) and "evidence_refs" in link:
                        link["evidence_refs"] = unique(link["evidence_refs"])
                        if isinstance(link["evidence_refs"], list):
                            cited.extend(
                                item for item in link["evidence_refs"] if isinstance(item, str)
                            )
        case["evidence_refs"] = (
            unique(case["evidence_refs"]) if "evidence_refs" in case else unique(cited)
        )
    return ResearchSynthesisCandidate.model_validate(value)


def _selected_gate(
    events: list[dict[str, Any]],
    selection: ResearchSynthesisSelectionWitness,
    context: ResearchNativeObservationContext,
    *,
    decision: bool = False,
) -> dict[str, Any] | None:
    if (
        selection.kind != "tool_capture"
        or selection.payload_path is None
        or not selection.payload_path
    ):
        return None
    suffix = ("candidate", "research_candidate_v1") if decision else ("candidate",)
    if selection.payload_path[-len(suffix) :] != suffix:
        if decision or selection.payload_path[-1] == "research_candidate_v1":
            raise ValueError("research_review_selected_protocol_mismatch")
        return None
    if selection.result_event_index is None:
        raise ValueError("research_review_selected_event_missing")
    wrapper = _at(events[selection.result_event_index], selection.payload_path[: -len(suffix)])
    if (
        not isinstance(wrapper, dict)
        or set(wrapper) != {"schema_version", "candidate", "review_gate"}
        or wrapper["schema_version"]
        != (
            "decision-hub-synthesis-tool-result.v2"
            if decision
            else "decision-hub-synthesis-tool-result.v1"
        )
    ):
        raise ValueError("research_review_selected_wrapper_invalid")
    gate = wrapper["review_gate"]
    expected = {
        "schema_version",
        "status",
        "reason",
        "binding",
        "candidate_json",
        "candidate_sha256",
        "review_receipt_hash",
        "review_call_id",
    }
    if (
        not isinstance(gate, dict)
        or set(gate) != expected
        or gate["schema_version"]
        != (
            "decision-hub-final-review-gate.v2" if decision else "decision-hub-final-review-gate.v1"
        )
    ):
        raise ValueError("research_review_selected_gate_invalid")
    binding = gate["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding) != {"runId", "requestHash", "sessionId", "requestId", "generation"}
        or binding["runId"] != context.run_id
        or binding["requestHash"] != context.first_linked_request_hash
        or binding["sessionId"] != context.dsh_session_id
        or binding["generation"] != context.generation
    ):
        raise ValueError("research_review_gate_scope_invalid")
    # Session and rpc are deterministic at the authenticated Host boundary.
    digest = sha(f"dsh-host-bridge.v1\0{context.run_id}\0{context.first_linked_request_hash}")
    rpc_digest = (
        digest
        if context.generation == 1
        else sha(
            f"dsh-host-turn.v1\0{context.run_id}\0{context.first_linked_request_hash}\0{context.generation}"
        )
    )
    if binding["sessionId"] != "dsh_" + digest or binding["requestId"] != "req_" + rpc_digest:
        raise ValueError("research_review_gate_binding_invalid")
    text = gate["candidate_json"]
    candidate_model = ResearchDecisionSynthesisV1 if decision else ResearchSynthesisCandidate
    candidate = candidate_model.model_validate(strict_json(text)) if isinstance(text, str) else None
    projected = (
        candidate.research_candidate_v1
        if isinstance(candidate, ResearchDecisionSynthesisV1)
        else candidate
    )
    if (
        not isinstance(text, str)
        or sha(text) != gate["candidate_sha256"]
        or projected != selection.candidate
        or decision
        and candidate_model.model_validate(wrapper["candidate"]) != candidate
    ):
        raise ValueError("research_review_gate_candidate_invalid")
    if gate["status"] != "matched":
        return None
    if (
        gate["reason"] != "exact_target"
        or not isinstance(gate["review_call_id"], str)
        or not gate["review_call_id"]
        or not isinstance(gate["review_receipt_hash"], str)
    ):
        raise ValueError("research_review_matched_gate_invalid")
    return gate


def _evidence_sets(
    session: Session,
    mapped: ResearchMappedObservation,
) -> tuple[frozenset[str], frozenset[str]]:
    context, result = mapped.context, mapped.result
    allowed = {item.evidence_id for item in result.evidence_candidates}
    accepted = {
        item.evidence_id
        for item in result.evidence_candidates
        if item.quality == "accepted"
        and item.received_at <= result.finished_at
        and item.observed_at <= result.finished_at
    }
    for reference in context.rows:
        row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
        if row is None or row.run_id != context.run_id or row_reference(row) != reference:
            raise ValueError("research_review_prepared_original_changed")
        if reference.role == "research_task_request":
            request = json.loads(row.payload_json).get("request")
            if not isinstance(request, dict):
                raise ValueError("research_review_task_original_invalid")
            for item in request.get("input_evidence", []):
                if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str):
                    raise ValueError("research_review_input_evidence_invalid")
                allowed.add(item["evidence_id"])
                received = datetime.fromisoformat(item["received_at"].replace("Z", "+00:00"))
                observed = item.get("observed_at")
                observed = (
                    datetime.fromisoformat(observed.replace("Z", "+00:00")) if observed else None
                )
                if (
                    received.utcoffset() is not None
                    and received <= result.finished_at
                    and (
                        observed is None
                        or observed.utcoffset() is not None
                        and observed <= result.finished_at
                    )
                ):
                    accepted.add(item["evidence_id"])
    return frozenset(allowed), frozenset(accepted)


def _derive(
    session: Session,
    mapped_ref: ObservedExecutionRef,
    source_result: ResearchSessionResult,
    known_at: datetime,
    *,
    _freeze_indexes: list[int] | None = None,
) -> ResearchReviewEffectOriginal | None:
    from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _typed_tool_identity,  # pyright: ignore[reportPrivateUsage]
        extract_structured_review_receipts,
    )

    mapped = read_mapped_observation(session, mapped_ref)
    raw = read_transport_observation(session, mapped.mapping_input_ref)
    if (
        raw.context != mapped.context
        or not isinstance(raw.reply, DshSessionResult)
        or raw.phase != "result"
        or source_result != mapped.result
        or mapped.result_content_hash != research_result_content_hash(mapped.result)
    ):
        raise ValueError("research_review_mapped_source_invalid")
    verify_selection_witness(raw.reply, mapped.selection)
    if (
        raw.reply.run_id != mapped.context.run_id
        or raw.reply.generation != mapped.context.generation
        or raw.reply.dsh_session_id != mapped.context.dsh_session_id
    ):
        raise ValueError("research_review_transport_identity_invalid")
    if raw.events_json_hash != hashlib.sha256(
        raw.reply.events_json.encode()
    ).hexdigest() or raw.reply.result_hash != sha(
        raw.reply.final_response + "\0" + raw.reply.events_json
    ):
        raise ValueError("research_review_transport_events_invalid")
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _unique_pairs,  # pyright: ignore[reportPrivateUsage]
    )

    events = json.loads(
        raw.reply.events_json,
        object_pairs_hook=_unique_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("research_review_nonfinite_events")
        ),
    )
    if not isinstance(events, list):
        raise ValueError("research_review_events_invalid")
    task_refs = [ref for ref in mapped.context.rows if ref.role == "research_task_request"]
    decision = False
    if task_refs:
        if len(task_refs) != 1:
            raise ValueError("research_review_task_original_invalid")
        task = session.get(RunRoleOutputRecord, task_refs[0].output_id, populate_existing=True)
        if (
            task is None
            or task.run_id != mapped.context.run_id
            or row_reference(task) != task_refs[0]
            or sha(task.payload_json) != task.payload_hash
        ):
            raise ValueError("research_review_task_original_invalid")
        request = strict_json(task.payload_json).get("request")
        if not isinstance(request, dict):
            raise ValueError("research_review_task_original_invalid")
        decision = synthesis_protocol(request.get("output_schema_ref")) == "decision"
    gate = _selected_gate(events, mapped.selection, mapped.context, decision=decision)
    if gate is None:
        return None
    allowed, accepted = _evidence_sets(session, mapped)
    notifications = tuple(
        DshSdkNotification(method=item["method"], payload=item["payload"])
        for item in events
        if isinstance(item, dict)
        and isinstance(item.get("method"), str)
        and isinstance(item.get("payload"), dict)
    )
    # Indexes must be the original transport indexes, never a filtered transcript.
    if len(notifications) != len(events):
        raise ValueError("research_review_event_shape_invalid")
    receipts = extract_structured_review_receipts(
        notifications,
        expected_session_id=mapped.context.dsh_session_id,
        allowed_evidence_ids=allowed,
        fallback_created_at=mapped.recorded_at,
    )
    exact = [
        item
        for item in receipts
        if item.receipt_hash == gate["review_receipt_hash"]
        and item.tool_call_id == gate["review_call_id"]
    ]
    if not exact:
        return None  # A legacy V1 gate has no structured producer to backfill.
    if len(exact) != 1:
        raise ValueError("research_review_receipt_ambiguous")
    receipt = exact[0]
    structured = receipt.structured_receipt
    if (
        structured is None
        or receipt.wrapper_json is None
        or receipt.call_event_index is None
        or receipt.result_event_index is None
    ):
        raise ValueError("research_review_structured_original_missing")
    wrapper = strict_json(receipt.wrapper_json)
    candidate_model = ResearchDecisionSynthesisV1 if decision else ResearchSynthesisCandidate
    frozen_candidate = candidate_model.model_validate(strict_json(wrapper["candidate_json"]))
    projected_candidate = (
        frozen_candidate.research_candidate_v1
        if isinstance(frozen_candidate, ResearchDecisionSynthesisV1)
        else frozen_candidate
    )
    if (
        projected_candidate != mapped.selection.candidate
        or decision
        and (
            wrapper["schema_version"] != "decision-hub-frozen-review-result.v3"
            or frozen_candidate
            != candidate_model.model_validate(strict_json(gate["candidate_json"]))
            or wrapper["candidate_sha256"] != gate["candidate_sha256"]
        )
        or wrapper["binding"] != gate["binding"]
        or mapped.selection.call_event_index is None
        or not (
            receipt.call_event_index
            < receipt.result_event_index
            < mapped.selection.call_event_index
        )
    ):
        raise ValueError("research_review_exact_candidate_scope_invalid")
    freezes = [
        (index, item)
        for index, item in enumerate(events)
        if item["payload"].get("sessionId") == mapped.context.dsh_session_id
        and item["payload"].get("event", {}).get("seq") == wrapper["freeze_seq"]
        and item["payload"].get("event", {}).get("type") == "tool/call"
    ]
    if len(freezes) != 1 or freezes[0][0] >= receipt.call_event_index:
        raise ValueError("research_review_freeze_call_invalid")
    freeze = freezes[0][1]["payload"]["event"]["data"]
    if _typed_tool_identity(freeze, item_type="tool-call") != (
        wrapper["freeze_call_id"],
        "decision_hub_synthesis_submit",
    ):
        raise ValueError("research_review_freeze_call_invalid")
    args = freeze.get("arguments")
    if args is None:
        message = freeze.get("message", {})
        values = [
            block.get("arguments")
            for block in message.get("content", [])
            if isinstance(block, dict)
            and block.get("type") == "tool-call"
            and block.get("id", block.get("callId")) == wrapper["freeze_call_id"]
        ]
        if len(values) != 1:
            raise ValueError("research_review_freeze_arguments_invalid")
        args = values[0]
    args = strict_json(args) if isinstance(args, str) else args
    if (
        not isinstance(args, dict)
        or (
            normalize_action_synthesis(args.get("candidate"))
            if decision
            else _normalized_freeze_candidate(args.get("candidate"))
        )
        != frozen_candidate
    ):
        raise ValueError("research_review_freeze_candidate_invalid")
    if receipt.summary_hash is None:
        raise ValueError("research_review_summary_hash_missing")
    public_id = "review_" + receipt.summary_hash[:32]
    public = [review for review in source_result.reviews or [] if review.review_id == public_id]
    if (
        len(public) != 1
        or any(
            getattr(public[0], key) != getattr(receipt, key)
            for key in (
                "role",
                "target_claim",
                "evidence_refs",
                "challenge",
                "resolution",
                "limitations",
                "created_at",
            )
        )
        or public[0].result_hash != research_result_content_hash(source_result)
    ):
        raise ValueError("research_review_public_projection_invalid")
    if (
        not source_result.started_at <= receipt.created_at <= source_result.finished_at
        or known_at < receipt.created_at
    ):
        raise ValueError("research_review_receipt_time_invalid")
    source_hash = observation_sha(source_result.model_dump(mode="json"))
    challenges = []
    for issue in structured.issues:
        if not set(issue.resolution_evidence_refs) <= accepted:
            raise ValueError("research_review_resolution_evidence_unaccepted_or_future")
        resolved = issue.status in {"answered", "rejected"}
        if resolved and not issue.resolution_evidence_refs:
            raise ValueError("research_review_resolution_evidence_missing")
        challenges.append(
            ResearchChallengeV1(
                challenge_id="review-v2:" + receipt.receipt_hash[:24] + ":" + issue.issue_id,
                source_review_id=public_id,
                severity=issue.severity,
                blocking_scope=tuple(issue.blocking_scope),
                evidence_refs=tuple(issue.evidence_refs),
                status=issue.status,
                disposition=issue.disposition,
                resolution_evidence_refs=tuple(issue.resolution_evidence_refs),
                resolved_result_hash=source_hash if resolved else None,
                effect_on_direction=issue.effect_on_direction,
                effect_on_tail_risk=issue.effect_on_tail_risk,
                effect_on_monitor=issue.effect_on_monitor,
                effect_on_report=issue.effect_on_report,
                reason=issue.reason,
                reviewer_version=structured.reviewer_version,
            )
        )
    if (
        mapped.selection.call_id is None
        or mapped.selection.candidate_payload_hash is None
        or receipt.tool_call_id is None
    ):
        raise ValueError("research_review_actual_call_identity_missing")
    original = ResearchReviewEffectOriginal(
        run_id=mapped.context.run_id,
        generation=mapped.context.generation,
        root_session_id=mapped.context.dsh_session_id,
        mapped_ref=mapped_ref,
        transport_ref=mapped.mapping_input_ref,
        selected_call_id=mapped.selection.call_id,
        selected_candidate_hash=mapped.selection.candidate_payload_hash,
        source_result_hash=source_hash,
        public_review_id=public_id,
        receipt=structured,
        receipt_hash=receipt.receipt_hash,
        reviewer_call_id=receipt.tool_call_id,
        reviewer_call_event_index=receipt.call_event_index,
        reviewer_result_event_index=receipt.result_event_index,
        reviewer_call_event_hash=observation_sha(events[receipt.call_event_index]),
        reviewer_result_event_hash=observation_sha(events[receipt.result_event_index]),
        frozen_wrapper_hash=sha(receipt.wrapper_json),
        frozen_candidate_hash=wrapper["candidate_sha256"],
        reviewer_prompt_hash=wrapper["actual_prompt_sha256"],
        child_session_id=wrapper["child_session_id"],
        challenges=tuple(challenges),
        known_at=known_at,
    )
    # Private read result, populated only after the complete native original has
    # passed. It is not a model field or a persisted change to the V1 receipt.
    if _freeze_indexes is not None:
        _freeze_indexes.append(freezes[0][0])
    return original


def _view(
    original: ResearchReviewEffectOriginal,
    reference: ObservedExecutionRef,
) -> ResearchReviewEffects:
    return ResearchReviewEffects(
        source_result_hash=original.source_result_hash,
        selected_candidate_hash=original.selected_candidate_hash,
        mapped_ref=original.mapped_ref,
        transport_ref=original.transport_ref,
        review_effect_ref=reference,
        public_review_ids=(original.public_review_id,),
        challenges=original.challenges,
    )


@borrowed_provider_read
def read_review_effects_in_session(
    session: Session, *, mapped_ref: ObservedExecutionRef, source_result: ResearchSessionResult
) -> ResearchReviewEffects | PerpetualReviewTransportEffect | None:
    """SELECT-only; missing receipt cannot be backfilled by a quality GET."""
    if mapped_ref.schema_version == "research-mapped-observation.v2":
        return _perpetual_transport_effect(session, mapped_ref, source_result)
    output_id, role = _address(mapped_ref)
    row = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
    if row is None:
        return None
    ref = row_reference(row)
    original = ResearchReviewEffectOriginal.model_validate_json(row.payload_json)
    if (
        row.role != role
        or row.schema_version != original.schema_version
        or row.run_id != original.run_id
        or original.mapped_ref != mapped_ref
        or as_utc(row.created_at) != original.known_at
        or row.payload_json != observation_json(original.model_dump(mode="json"))
    ):
        raise ValueError("research_review_effect_original_invalid")
    current = _derive(session, mapped_ref, source_result, original.known_at)
    if current != original:
        raise ValueError("research_review_effect_source_changed")
    return _view(original, ref)


def record_review_effects_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    source_result: ResearchSessionResult,
    expected_lease_owner: str,
    known_at: datetime,
) -> ResearchReviewEffects | PerpetualReviewTransportEffect | None:
    """Graph writer only, after the Runtime's selected mapped observation exists."""
    from .research_graph_binding import _fence, _permission  # pyright: ignore[reportPrivateUsage]
    from .research_publication_binding import _require_writer  # pyright: ignore[reportPrivateUsage]

    _require_writer(session)
    if known_at.utcoffset() is None:
        raise ValueError("research_review_known_at_timezone_required")
    if mapped_ref.schema_version == "research-mapped-observation.v2":
        from .research_publication_binding import read_mapped_perpetual_observation
        mapped_v2 = read_mapped_perpetual_observation(session, mapped_ref)
        _permission(session, mapped_v2.context.run_id, expected_lease_owner)
        result = _perpetual_transport_effect(session, mapped_ref, source_result)
        _fence(session, mapped_v2.context.run_id, expected_lease_owner)
        return result
    mapped = read_mapped_observation(session, mapped_ref)
    _permission(session, mapped.context.run_id, expected_lease_owner)
    original = _derive(session, mapped_ref, source_result, known_at)
    if original is None:
        return None
    output_id, role = _address(mapped_ref)
    old = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            (RunRoleOutputRecord.output_id == output_id)
            | ((RunRoleOutputRecord.run_id == original.run_id) & (RunRoleOutputRecord.role == role))
        )
        .execution_options(populate_existing=True)
    )
    if old is not None:
        saved = read_review_effects_in_session(
            session, mapped_ref=mapped_ref, source_result=source_result
        )
        if saved is None:
            raise ValueError("research_review_effect_identity_conflict")
        _fence(session, original.run_id, expected_lease_owner)
        return saved
    _fence(session, original.run_id, expected_lease_owner)
    encoded = observation_json(original.model_dump(mode="json"))
    row = RunRoleOutputRecord(
        output_id=output_id,
        run_id=original.run_id,
        role=role,
        schema_version=original.schema_version,
        payload_json=encoded,
        payload_hash=sha(encoded),
        created_at=known_at,
    )
    session.add(row)
    session.flush()
    _fence(session, original.run_id, expected_lease_owner)
    return _view(original, row_reference(row))


def _lineage_original(session, mapped_ref, source_result):
    # Preserve the actual native original authentication, including _derive.
    view = read_review_effects_in_session(
        session, mapped_ref=mapped_ref, source_result=source_result
    )
    if view is None or view.review_effect_ref is None:
        return None
    row = session.get(RunRoleOutputRecord, view.review_effect_ref.output_id, populate_existing=True)
    if row is None or row_reference(row) != view.review_effect_ref:
        raise ValueError("research_review_lineage_original_changed")
    return ResearchReviewEffectOriginal.model_validate_json(row.payload_json), view.review_effect_ref


def _issue_identity(original, issue):
    # V2 has no immutable per-issue question field. The exact frozen candidate
    # and whole question text anchor this bounded association. UUID is excluded.
    return observation_sha({
        "schema_version": "research-review-issue-identity.v1",
        "selected_candidate_hash": original.selected_candidate_hash,
        "target_claim": original.receipt.target_claim,
        "question": original.receipt.challenge,
        "reviewer_version": original.receipt.reviewer_version,
        "severity": issue.severity,
        "blocking_scope": sorted(issue.blocking_scope),
        "evidence_refs": sorted(issue.evidence_refs),
    })


@borrowed_provider_read
def read_review_issue_lineage_in_session(
    session: Session, *, previous_mapped_ref: ObservedExecutionRef,
    mapped_ref: ObservedExecutionRef, source_result: ResearchSessionResult,
):
    """Server-only exact parent/current association; no model ID authority."""
    from ..ports.research_review_lineage import ResearchReviewIssueLineageV1

    if previous_mapped_ref == mapped_ref:
        return ()
    prior_mapping = read_mapped_observation(session, previous_mapped_ref)
    previous = _lineage_original(session, previous_mapped_ref, prior_mapping.result)
    current = _lineage_original(session, mapped_ref, source_result)
    if previous is None or current is None:
        return ()
    prior, prior_ref = previous
    now, now_ref = current
    if (
        prior.run_id != now.run_id
        or prior.root_session_id != now.root_session_id
        or prior.generation > now.generation
        or prior.known_at > now.known_at
        or prior_ref.recorded_at > now_ref.recorded_at
        or prior_mapping.context.original_task_request_hash
        != read_mapped_observation(session, mapped_ref).context.original_task_request_hash
    ):
        raise ValueError("research_review_lineage_parent_scope_invalid")
    groups = []
    for original in (prior, now):
        grouped = {}
        for issue, challenge in zip(original.receipt.issues, original.challenges, strict=True):
            key = _issue_identity(original, issue)
            grouped.setdefault(key, []).append((issue, challenge))
        groups.append(grouped)
    output = []
    for identity in sorted(groups[0].keys() & groups[1].keys()):
        # Multiple questions with this semantic silhouette are ambiguous, even
        # if model IDs happen to differ. Never guess by list position or UUID.
        if len(groups[0][identity]) != 1 or len(groups[1][identity]) != 1:
            continue
        prior_issue, prior_challenge = groups[0][identity][0]
        now_issue, now_challenge = groups[1][identity][0]
        if prior_issue.issue_id != now_issue.issue_id:
            continue  # Additional consistency, not the identity authority.
        anchor = observation_sha({
            "schema_version": "research-review-issue-lineage.v1",
            "run_id": prior.run_id,
            "root_session_id": prior.root_session_id,
            "original_task_request_hash": prior_mapping.context.original_task_request_hash,
            "issue_identity_hash": identity,
        })
        output.append(ResearchReviewIssueLineageV1(
            lineage_id="review-lineage:" + anchor,
            issue_identity_hash=identity,
            run_id=now.run_id, root_session_id=now.root_session_id,
            previous_generation=prior.generation, generation=now.generation,
            previous_effect_ref=prior_ref, effect_ref=now_ref,
            previous_mapped_ref=previous_mapped_ref, mapped_ref=mapped_ref,
            previous_transport_ref=prior.transport_ref, transport_ref=now.transport_ref,
            previous_receipt_hash=prior.receipt_hash, receipt_hash=now.receipt_hash,
            previous_result_hash=prior.source_result_hash, result_hash=now.source_result_hash,
            previous_review_id=prior.public_review_id, review_id=now.public_review_id,
            previous_challenge_id=prior_challenge.challenge_id,
            challenge_id=now_challenge.challenge_id,
        ))
    return tuple(output)
