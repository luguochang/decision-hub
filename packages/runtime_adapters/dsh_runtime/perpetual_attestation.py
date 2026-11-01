"""Exact versioned native Tool pairs and independent-child receipts; no publish authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewAuthorizationV2,
    ActionReviewAuthorizationV3,
    ActionReviewRequestV2,
    ActionReviewRequestV3,
    ResearchDecisionSynthesisV2,
    ResearchDecisionSynthesisV3,
)
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshInstructionMessage,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.ports.event_scenario_manifest import (
    event_scenario_validation_scope,
)
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
    parse_action_review_request,
    primary_perpetual_review_request,
    primary_perpetual_synthesis,
    validate_action_review_binding_v2,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification
from .finalized_coverage import FinalizedCoverage, validate_finalized_coverage
from .reviewer_instruction_bindings import (
    expected_review_instructions,
    matches_review_instructions,
)
from .tool_result_attestation import (
    _actual_return_payloads,
    _event_seq,
    _pruned_review_result_projection,
    _session_event,
    _tool_result_failed,
    _typed_content_items,
    _typed_tool_identity,
    _typed_tool_result_call_id,
    _unique_pairs,
)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _hash(value):
    return _sha(_canonical(value))


def _json(value):
    return json.loads(value, object_pairs_hook=_unique_pairs) if isinstance(value, str) else value


_PureReplayMemo = dict[tuple[str, str, str, str, str], dict]


def _normalized_preparation(
    draft, *, request_id, binding, review_originals, requested_at,
    computation_memo: _PureReplayMemo | None = None,
):
    """Reuse only successful pure calculations within an explicit caller scope."""
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import (
        prepare_perpetual_synthesis,
    )

    key = None
    if computation_memo is not None:
        key = (_hash(draft), _hash(review_originals), _hash(binding), request_id, requested_at)
        if key in computation_memo:
            return computation_memo[key]
    normalized = prepare_perpetual_synthesis(
        draft, request_id=request_id, binding=binding,
        review_originals=review_originals, requested_at=requested_at,
    )
    if computation_memo is not None:
        computation_memo[key] = normalized
    return normalized


def _review_child_input_matches(
    events, expected_prompt, expected_instructions: DshInstructionMessage | None = None,
):
    """Authenticate the sole product input plus the SDK's recorded policy snapshot."""
    products, snapshots, instructions = [], [], []
    allowed_sections = {"sandbox:policy", "approval:policy", "subagent:delegation"}
    prefix = (
        "Current runtime context. This snapshot supersedes earlier runtime-context snapshots."
    )
    for index, event in enumerate(events):
        if event.get("type") != "user/message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            return False
        message = data.get("message", data)
        if not isinstance(message, dict):
            return False
        source = message.get("source", data.get("source"))
        if "source" in message and "source" in data and message["source"] != data["source"]:
            return False
        missing_source = "source" not in message and "source" not in data
        if missing_source or isinstance(source, dict) and source.get("kind") == "user":
            products.append((index, message.get("content")))
            continue
        if isinstance(source, dict) and source.get("kind") == "agent-instructions":
            if not matches_review_instructions(data, expected_instructions):
                return False
            instructions.append(index)
            continue
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "plugin", "form", "sections"}
            or source.get("kind") != "plugin"
            or source.get("plugin") != "@deepseek-ai/dsh-system-prompt"
            or source.get("form") != "snapshot"
            or set(data) != {"content", "source", "role", "id"}
            or data.get("role") != "user"
            or not isinstance(data.get("id"), str)
            or not data["id"].strip()
        ):
            return False
        sections = source["sections"]
        if not isinstance(sections, list) or not sections:
            return False
        names, texts = set(), []
        for section in sections:
            if (
                not isinstance(section, dict)
                or set(section) != {"name", "text"}
                or section.get("name") not in allowed_sections
                or section["name"] in names
                or not isinstance(section.get("text"), str)
                or len(section["text"]) == 0
            ):
                return False
            names.add(section["name"])
            texts.append(section["text"])
        if data["content"] != [{"type": "text", "text": prefix + "\n\n" + "\n\n".join(texts)}]:
            return False
        snapshots.append(index)
    if len(products) != 1 or products[0][1] != [{"type": "text", "text": expected_prompt}]:
        return False
    if len(snapshots) > 1 or len(instructions) > 1:
        return False
    first_answer = next(
        (index for index, event in enumerate(events) if event.get("type") == "assistant/message"),
        len(events),
    )
    return (
        (not snapshots or products[0][0] < snapshots[0] < first_answer)
        and (not instructions or products[0][0] < instructions[0] < first_answer
             and (not snapshots or instructions[0] < snapshots[0]))
    )


@dataclass(frozen=True)
class AttestedPerpetualSynthesisCapture:
    synthesis: ResearchDecisionSynthesisV2 | ResearchDecisionSynthesisV3
    review_request: ActionReviewRequestV2 | ActionReviewRequestV3
    authorization: ActionReviewAuthorizationV2 | ActionReviewAuthorizationV3 | None
    call_id: str
    call_event_index: int
    result_event_index: int
    payload_path: tuple
    raw_candidate_hash: str
    call_seq: int | None
    result_seq: int | None
    review_wrapper_json: str | None
    review_call_id: str | None
    review_call_event_index: int | None
    review_result_event_index: int | None
    payload_occurrence: int = 0
    coverage_delivery: FinalizedCoverage | None = None


def _arguments(data, call_id, name):
    rows = list(_typed_content_items(data, "tool-call")) or [data]
    rows = [
        row for row in rows if _typed_tool_identity(row, item_type="tool-call") == (call_id, name)
    ]
    if len(rows) != 1:
        raise ValueError("call_arguments_ambiguous")
    args = _json(rows[0].get("arguments"))
    if not isinstance(args, dict):
        raise ValueError("call_arguments_invalid")
    return args


def _pairs(notifications, session_id):
    calls, results, seqs = {}, {}, {}
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != session_id:
            continue
        kind, data, _ = event
        seq = _event_seq(notification)
        if seq is not None:
            seqs.setdefault(seq, []).append(index)
        identity = _typed_tool_identity(data, item_type="tool-call")
        if (
            kind == "tool/call"
            and identity
            and identity[1] in {"decision_hub_synthesis_submit", "subagent_reviewer"}
        ):
            calls.setdefault(identity[0], []).append((index, data, identity[1]))
        elif kind in {"tool/result", "tool/call-result"}:
            call_id = _typed_tool_result_call_id(data)
            if call_id:
                results.setdefault(call_id, []).append(index)
    # Include official replacements even if their reported call id is changed.
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != session_id:
            continue
        surface = notification.payload["event"].get("surfaceOp")
        if isinstance(surface, Mapping) and type(surface.get("start")) is int:
            for original in seqs.get(surface["start"], []):
                original_event = _session_event(notifications[original])
                target = _typed_tool_result_call_id(original_event[1]) if original_event else None
                if target in calls and index not in results.setdefault(target, []):
                    results[target].append(index)
    pairs = {}
    for call_id, starts in calls.items():
        if len(starts) != 1:
            raise ValueError("duplicate_call")
        start, data, name = starts[0]
        originals = []
        for index in results.get(call_id, []):
            surface = notifications[index].payload["event"].get("surfaceOp")
            if surface is None or surface == "append":
                originals.append(index)
            elif not _pruned_review_result_projection(
                notifications,
                call_index=start,
                result_index=index,
                expected_session_id=session_id,
                events_by_seq=seqs,
            ):
                raise ValueError("unproved_display_replacement")
        if len(originals) != 1 or originals[0] <= start:
            # A reviewer still in flight never authorizes submission.
            if not originals and name == "subagent_reviewer":
                continue
            raise ValueError("pair_ambiguous")
        pairs[call_id] = (start, originals[0], data, name)
    return pairs


def _binding(value, session_id, request, notifications, before):
    if not isinstance(value, dict) or set(value) != {
        "runId",
        "requestHash",
        "sessionId",
        "requestId",
        "generation",
    }:
        raise ValueError("binding_shape")
    if (
        value["sessionId"] != session_id
        or type(value["generation"]) is not int
        or value["generation"] < 1
    ):
        raise ValueError("binding_session")
    digest = _sha(f"dsh-host-bridge.v1\0{value['runId']}\0{value['requestHash']}")
    rpc = (
        digest
        if value["generation"] == 1
        else _sha(
            f"dsh-host-turn.v1\0{value['runId']}\0{value['requestHash']}\0{value['generation']}"
        )
    )
    if session_id != "dsh_" + digest or value["requestId"] != "req_" + rpc:
        raise ValueError("binding_identity")
    if request is not None and (
        request.run_id != value["runId"]
        or research_request_hash(request) != value["requestHash"]
        or request.output_schema_ref not in {
            "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2",
            "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3",
        }
    ):
        raise ValueError("binding_request")
    prompts = []
    for notification in notifications[:before]:
        event = _session_event(notification)
        if event and event[2] == session_id and event[0] == "user/message":
            source = event[1].get("source")
            if isinstance(source, dict) and source.get("kind") == "user" and source.get("rpcId"):
                prompts.append(source["rpcId"])
    if not prompts or prompts[-1] != value["requestId"]:
        raise ValueError("binding_prompt")


def _prepared_packet(
    notifications, pairs, freeze_id, synthesis_raw, packet_raw, binding, catalog, *,
    computation_memo: _PureReplayMemo | None = None,
):
    """Replay the actual prepare pair. This is native proof, not SQL source authority."""
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        PerpetualPreparationWitnessV1,
        PerpetualPreparationWitnessV2,
        PerpetualPrepareArgumentsV1,
        PerpetualPrepareArgumentsV2,
    )
    start, end, data, name = pairs[freeze_id]
    args = _arguments(data, freeze_id, name)
    event_plan = synthesis_raw.get("schema_version") == "research-decision-synthesis.v3"
    argument_model = PerpetualPrepareArgumentsV2 if event_plan else PerpetualPrepareArgumentsV1
    argument_model.model_validate(args)
    event = _session_event(notifications[end])
    if event is None or _tool_result_failed(event[1]):
        raise ValueError("preparation_original_failed")
    outputs = list(_actual_return_payloads(event[1], freeze_id))
    if len(outputs) != 1:
        raise ValueError("preparation_original_ambiguous")
    body = outputs[0][0]
    if (
        not isinstance(body, dict)
        or set(body) != {"schema_version", "candidate", "review_request", "preparation"}
        or body["schema_version"] != (
            "decision-hub-perpetual-preparation.v2" if event_plan
            else "decision-hub-perpetual-preparation.v1"
        )
        or body["candidate"] != synthesis_raw
        or body["review_request"] != packet_raw
    ):
        raise ValueError("preparation_original_changed")
    witness = body["preparation"]
    witness_model = PerpetualPreparationWitnessV2 if event_plan else PerpetualPreparationWitnessV1
    witness_model.model_validate(witness)
    milliseconds = notifications[start].payload["event"].get("time")
    if type(milliseconds) is not int:
        raise ValueError("preparation_clock_missing")
    if (
        datetime.fromisoformat(catalog["as_of"].replace("Z", "+00:00")).timestamp() * 1000
        > milliseconds
    ):
        raise ValueError("preparation_future_catalog")
    requested_at = (
        (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    if (
        witness["prepared_call_id"] != freeze_id
        or witness["prepared_seq"] != _event_seq(notifications[start])
        or witness["requested_at"] != requested_at
        or args["request_id"] != synthesis_raw["request_id"]
    ):
        raise ValueError("preparation_call_identity_mismatch")
    _binding(binding, binding["sessionId"], None, notifications, start)
    primary = primary_perpetual_synthesis(synthesis_raw)
    options_binding = {
        k: primary[k] for k in ("opportunity_id", "method_ref", "method_code_hash")
    }
    options_binding.update(
        {
            k: primary["candidate_set"][k]
            for k in ("context", "candidate_target_ref", "anchor_rule_ref")
        }
    )
    normalized = _normalized_preparation(
        args["draft"],
        request_id=args["request_id"],
        binding=options_binding,
        review_originals=catalog,
        requested_at=requested_at,
        computation_memo=computation_memo,
    )
    for key in ("candidate", "review_request"):
        if normalized[key] != body[key]:
            raise ValueError("preparation_deterministic_replay_mismatch")
    witness_keys = (
        "assumptions",
        "source_draft_hash",
        "normalized_synthesis_hash",
        "normalized_review_request_hash",
    )
    if event_plan:
        witness_keys += (
            "primary_v2_hash", "primary_review_request_hash", "event_plan_draft_hash",
            "event_plan_hash",
        )
    for key in witness_keys:
        if normalized[key] != witness[key]:
            raise ValueError("preparation_derived_identity_mismatch")
    return witness, args


@event_scenario_validation_scope()
def preparation_for_capture(notifications, *, expected_session_id, call_id, catalog, request=None):
    """Read exact native preparation behind one already attested final capture."""
    computation_memo: _PureReplayMemo = {}
    pairs = _pairs(notifications, expected_session_id)
    start, end, data, name = pairs[call_id]
    args = _arguments(data, call_id, name)
    if args.get("action") != "finalize":
        return None
    event = _session_event(notifications[end])
    if event is None or _tool_result_failed(event[1]):
        raise ValueError("finalize_original_failed")
    outputs = list(_actual_return_payloads(event[1], call_id))
    if len(outputs) != 1:
        raise ValueError("finalize_original_ambiguous")
    body = outputs[0][0]
    if (
        not isinstance(body, dict)
        or body.get("schema_version") not in {
            "decision-hub-synthesis-tool-result.v4", "decision-hub-synthesis-tool-result.v5",
        }
    ):
        raise ValueError("finalize_original_protocol_invalid")
    finalized_coverage = body["schema_version"] == "decision-hub-synthesis-tool-result.v5"
    if finalized_coverage and set(body) != {
        "schema_version", "candidate", "review_request", "preparation", "review_gate",
        "coverage_delivery",
    }:
        raise ValueError("finalize_original_protocol_invalid")
    witness, prepare_args = _prepared_packet(
        notifications,
        pairs,
        args["prepared_call_id"],
        body["candidate"],
        body["review_request"],
        body["review_gate"]["binding"],
        catalog,
        computation_memo=computation_memo,
    )
    if (
        witness != body["preparation"]
        or body["review_gate"]["freeze_call_id"] != witness["prepared_call_id"]
    ):
        raise ValueError("finalize_original_preparation_changed")
    attempts = _preparation_history(
        notifications, pairs, before=start, binding=body["review_gate"]["binding"], request=request,
        computation_memo=computation_memo,
    )
    if not attempts or attempts[-1]["call_id"] != args["prepared_call_id"]:
        raise ValueError("finalize_stale_preparation")
    if finalized_coverage:
        captures = extract_attested_perpetual_synthesis_captures(
            notifications, expected_session_id=expected_session_id, request=request,
        )
        if not captures or captures[-1].call_id != call_id:
            raise ValueError("finalize_original_protocol_invalid")
    return dict(
        preparation=witness,
        prepare_arguments=prepare_args,
        finalize_arguments=args,
        candidate=body["candidate"],
        review_request=body["review_request"],
        attempts=attempts,
    )


def _findings_schema_from_prompt(
    prompt: str,
) -> Literal["perpetual-review-findings.v1", "perpetual-review-findings.v2"]:
    lines = prompt.split("\n")
    marker = "FINDINGS_FIXED_FIELDS_JSON"
    if marker not in lines:
        return "perpetual-review-findings.v1"
    if lines.count(marker) != 1:
        raise ValueError("review_findings_prompt_version_invalid")
    index = lines.index(marker)
    fixed = _json(lines[index + 1]) if index + 1 < len(lines) else None
    version = fixed.get("schema_version") if isinstance(fixed, dict) else None
    if version == "perpetual-review-findings.v1":
        return "perpetual-review-findings.v1"
    if version == "perpetual-review-findings.v2":
        return "perpetual-review-findings.v2"
    raise ValueError("review_findings_prompt_version_invalid")


def _findings(
    packet: dict[str, Any], authorization: Any, output: Any, catalog: dict[str, Any], *,
    required: bool, expected_schema_version: str = "perpetual-review-findings.v1",
    assumptions: Sequence[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        PerpetualReviewFindingsV1,
        PerpetualReviewFindingsV2,
    )

    values = []
    for block in output:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            value = _json(block.get("text"))
        except (ValueError, TypeError):
            continue
        values.append(value)
    if not required:
        return None
    if len(values) != 1 or not isinstance(values[0], list):
        raise ValueError("review_exact_array_required")
    values = values[0]
    if (
        len(values) != 2
        or not all(isinstance(value, dict) for value in values)
        or [value.get("schema_version") for value in values]
        != [
            "action-review-authorization.v3"
            if packet.get("schema_version") == "action-review-request.v3"
            else "action-review-authorization.v2",
            expected_schema_version,
        ]
    ):
        raise ValueError("review_exact_findings_required")
    if expected_schema_version == "perpetual-review-findings.v1":
        parsed = PerpetualReviewFindingsV1.model_validate(values[1])
    elif expected_schema_version == "perpetual-review-findings.v2":
        parsed = PerpetualReviewFindingsV2.model_validate(values[1])
    else:
        raise ValueError("review_findings_prompt_version_invalid")
    refs = {
        _canonical(ref)
        for row in catalog["originals"]
        for ref in (row["source_ref"], row["original_ref"])
        if ref
    }
    primary: Any = primary_perpetual_synthesis(packet["synthesis"])
    ids = {row["candidate_id"] for row in primary["candidate_set"]["candidates"]}
    if packet.get("schema_version") == "action-review-request.v3":
        from packages.kernel.decision_hub_kernel.ports.event_scenario_manifest import (
            unpack_event_scenario_manifest,
        )

        rows = [
            row for row in catalog["originals"]
            if row["source_kind"] == "server_material" and row["role"] == "event_scenario_plan"
        ]
        if len(rows) != 1:
            raise ValueError("review_event_manifest_original_missing")
        material = _json(rows[0]["body_json"])
        if material.get("schema_version") != "event-scenario-prepared-original.v2":
            raise ValueError("review_event_manifest_original_version")
        expanded = unpack_event_scenario_manifest(material["manifest"])
        synth = packet["synthesis"]
        if (
            material["event_plan_manifest_ref"] != synth["event_plan_manifest_ref"]
            or synth["event_plan_manifest_ref"]["hash"] != _hash(material["manifest"])
            or synth["event_plan_ref"]["hash"] != _hash(expanded["event_plan"])
            or synth["event_plan_draft_ref"]["hash"]
            != _hash(expanded["normalized_event_plan_draft"])
        ):
            raise ValueError("review_event_manifest_original_changed")
        ids.update(
            row["candidate_id"]
            for branch in expanded["event_plan"]["branches"]
            for bundle in branch["comparisons_by_position"].values()
            for row in bundle["comparison"]["candidate_results"]
        )
    if (
        parsed.review_request_ref.model_dump(mode="json")
        != authorization.review_request_ref.model_dump(mode="json")
        or authorization.verdict != "approved"
        and not parsed.issues
        or len({row.issue_id for row in parsed.issues}) != len(parsed.issues)
        or any(
            any(ident not in ids for ident in row.candidate_ids)
            or any(_canonical(ref.model_dump(mode="json")) not in refs for ref in row.evidence_refs)
            for row in parsed.issues
        )
    ):
        raise ValueError("review_findings_binding_invalid")
    if isinstance(parsed, PerpetualReviewFindingsV2):
        from packages.kernel.decision_hub_kernel.ports.perpetual_assessment_refs import (
            proposal_assessment_refs,
        )

        proposals = proposal_assessment_refs(packet["synthesis"], assumptions)
        for name, check in parsed.report_assessment.model_dump(mode="json").items():
            if (check["status"] == "passed" and not check["evidence_refs"]
                    or any(_canonical(ref) not in refs
                           and _canonical(ref) not in proposals.get(name, set())
                           for ref in check["evidence_refs"])):
                raise ValueError("review_assessment_binding_invalid")
    return values[1]


def _preparation_history(
    notifications, pairs, *, before, binding, request=None,
    computation_memo: _PureReplayMemo | None = None,
):
    """Rebuild every successful prepare and actual review; never select an older approval."""
    attempts = []
    children = set()
    for call_id, (start, end, data, name) in sorted(pairs.items(), key=lambda row: row[1][0]):
        if start >= before or end >= before:
            continue
        args = _arguments(data, call_id, name)
        event = _session_event(notifications[end])
        if name == "subagent_reviewer" and attempts:
            if attempts[-1].get("review_attempted"):
                raise ValueError("prepare_already_reviewed")
            attempts[-1]["review_attempted"] = True
        if event is None or _tool_result_failed(event[1]):
            continue
        if name == "decision_hub_synthesis_submit" and args.get("action") == "prepare":
            values = list(_actual_return_payloads(event[1], call_id))
            if (
                len(values) != 1
                or not isinstance(values[0][0], dict)
                or values[0][0].get("schema_version") not in {
                    "decision-hub-perpetual-preparation.v1",
                    "decision-hub-perpetual-preparation.v2",
                }
            ):
                raise ValueError("preparation_history_original_invalid")
            body = values[0][0]
            previous = attempts[-1] if attempts else None
            if previous and (
                previous.get("authorization") is None
                or previous["authorization"].verdict == "approved"
                or not previous.get("findings")
                or previous["review_end"] >= start
                or previous["preparation"]["source_draft_hash"]
                == body["preparation"]["source_draft_hash"]
            ):
                raise ValueError("revision_requires_negative_review")
            attempts.append(
                dict(
                    call_id=call_id,
                    prepare_arguments=args,
                    preparation=body["preparation"],
                    candidate=body["candidate"],
                    review_request=body["review_request"],
                )
            )
        elif name == "subagent_reviewer" and attempts:
            active = attempts[-1]
            if active.get("authorization") is not None:
                raise ValueError("prepare_already_reviewed")
            authorization, wrapper, review_start, review_end, _digest = _review(
                notifications,
                pairs,
                call_id,
                active["call_id"],
                active["candidate"],
                active["review_request"],
                binding,
                computation_memo=computation_memo,
            )
            if wrapper["child_session_id"] in children:
                raise ValueError("review_child_reused")
            children.add(wrapper["child_session_id"])
            from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
                review_originals_from_prompt,
            )

            catalog = review_originals_from_prompt(wrapper["actual_prompt"])
            lines = wrapper["actual_prompt"].split("\n")
            findings = _findings(
                active["review_request"],
                authorization,
                wrapper["original_output"]["output"],
                catalog,
                required="FINDINGS_FIXED_FIELDS_JSON" in lines,
                expected_schema_version=_findings_schema_from_prompt(wrapper["actual_prompt"]),
                assumptions=active["preparation"]["assumptions"],
            )
            marker = "BEGIN_PREVIOUS_NEGATIVE_REVIEW_JSON"
            if len(attempts) > 1:
                previous = attempts[-2]
                raw_authorization = None
                for block in previous["wrapper"]["original_output"]["output"]:
                    if not isinstance(block, dict) or block.get("type") != "text":
                        continue
                    try:
                        raw = _json(block.get("text"))
                    except (ValueError, TypeError):
                        continue
                    for item in raw if isinstance(raw, list) else [raw]:
                        if (
                            isinstance(item, dict)
                            and item.get("schema_version") == (
                                "action-review-authorization.v3"
                                if previous["review_request"].get("schema_version")
                                == "action-review-request.v3"
                                else "action-review-authorization.v2"
                            )
                        ):
                            raw_authorization = item
                expected = dict(authorization=raw_authorization, findings=previous["findings"])
                if lines.count(marker) != 1:
                    raise ValueError("previous_review_missing")
                position = lines.index(marker)
                if (
                    lines[position + 1] != _canonical(expected)
                    or lines[position + 2] != "END_PREVIOUS_NEGATIVE_REVIEW_JSON"
                    or lines[position + 3] != "PREVIOUS_NEGATIVE_REVIEW_SHA256=" + _hash(expected)
                ):
                    raise ValueError("previous_review_changed")
            elif marker in lines:
                raise ValueError("previous_review_unavailable")
            if request is not None:
                calls = [_session_event(row) for row in notifications[: start + 1]]
                child_calls = sum(
                    1
                    for row in calls
                    if row
                    and row[2] == binding["sessionId"]
                    and row[0] == "tool/call"
                    and (_typed_tool_identity(row[1], item_type="tool-call") or ("", ""))[1]
                    in {"subagent", "dsh.internal.subagent", "subagent_reviewer"}
                )
                if child_calls > request.execution_budget.max_subagents:
                    raise ValueError("review_total_subagent_budget_exhausted")
                tool_calls = sum(
                    1
                    for row in calls
                    if row and row[2] == binding["sessionId"] and row[0] == "tool/call"
                )
                at = notifications[start].payload["event"].get("time")
                if (
                    tool_calls > request.execution_budget.max_tool_calls
                    or type(at) is not int
                    or at >= request.deadline_at.timestamp() * 1000
                ):
                    raise ValueError("review_original_task_budget_exhausted")
            active.update(
                authorization=authorization,
                findings=findings,
                wrapper=wrapper,
                catalog=catalog,
                review_call_id=call_id,
                review_start=review_start,
                review_end=review_end,
            )
        elif name == "decision_hub_synthesis_submit" and "candidate" in args and attempts:
            raise ValueError("preparation_cross_protocol_history")
    return attempts


def _review(
    notifications, pairs, call_id, freeze_id, synthesis_raw, packet_raw, binding, *,
    computation_memo: _PureReplayMemo | None = None,
):
    start, end, _args, name = pairs[call_id]
    if name != "subagent_reviewer":
        raise ValueError("review_tool")
    event = _session_event(notifications[end])
    if event is None or _tool_result_failed(event[1]):
        raise ValueError("review_failed")
    values = list(_actual_return_payloads(event[1], call_id, allow_full_perpetual_review=True))
    if len(values) != 1:
        raise ValueError("review_wrapper_ambiguous")
    wrapper = values[0][0]
    expected = {
        "schema_version",
        "binding",
        "freeze_call_id",
        "freeze_seq",
        "candidate_json",
        "candidate_sha256",
        "review_request_json",
        "review_request_sha256",
        "actual_prompt",
        "actual_prompt_sha256",
        "child_session_id",
        "child_parent_session_id",
        "child_events_json",
        "child_events_sha256",
        "original_output",
    }
    if (
        not isinstance(wrapper, dict)
        or set(wrapper) != expected
        or wrapper["schema_version"] != "decision-hub-frozen-review-result.v4"
    ):
        raise ValueError("review_protocol")
    for text, digest in [
        ("candidate_json", "candidate_sha256"),
        ("review_request_json", "review_request_sha256"),
        ("actual_prompt", "actual_prompt_sha256"),
        ("child_events_json", "child_events_sha256"),
    ]:
        if (
            not isinstance(wrapper[text], str)
            or (text != "child_events_json" and len(wrapper[text].encode()) > 1_000_000)
            or _sha(wrapper[text]) != wrapper[digest]
        ):
            raise ValueError("review_digest")
    frozen = pairs.get(freeze_id)
    if (
        not frozen
        or not frozen[0] < frozen[1] < start < end
        or wrapper["freeze_call_id"] != freeze_id
        or wrapper["freeze_seq"] != _event_seq(notifications[frozen[0]])
    ):
        raise ValueError("review_freeze_order")
    freeze_args = _arguments(frozen[2], freeze_id, "decision_hub_synthesis_submit")
    prepared = freeze_args.get("action") == "prepare"
    if (
        not prepared
        and freeze_args != {"candidate": synthesis_raw, "review_request": packet_raw}
        or _json(wrapper["candidate_json"]) != synthesis_raw
        or _json(wrapper["review_request_json"]) != packet_raw
    ):
        raise ValueError("review_frozen_packet")
    frozen_result = _session_event(notifications[frozen[1]])
    if frozen_result is None or (not prepared and not _tool_result_failed(frozen_result[1])):
        raise ValueError("review_qualified_freeze_required")
    freeze_rows = list(_typed_content_items(frozen_result[1], "tool-result")) or [frozen_result[1]]
    matching = [row for row in freeze_rows if _typed_tool_result_call_id(row) == freeze_id]
    if len(matching) != 1:
        raise ValueError("review_freeze_result_ambiguous")
    failure = matching[0]
    content = failure.get("content")
    exact_error = content == [
        {"type": "text", "text": "Error: decision_hub_final_review_required"}
    ] or content == [{"type": "text", "text": "decision_hub_final_review_required"}]
    exact_code = (
        isinstance(failure.get("error"), dict)
        and failure["error"].get("code") == "decision_hub_final_review_required"
    )
    if not prepared and not exact_error and not exact_code:
        raise ValueError("review_freeze_not_qualified")
    if (
        wrapper["binding"] != binding
        or wrapper["child_parent_session_id"] != binding["sessionId"]
        or wrapper["child_session_id"] == binding["sessionId"]
    ):
        raise ValueError("review_child_scope")
    output = wrapper["original_output"]
    if (
        not isinstance(output, dict)
        or set(output) != {"kind", "runId", "output"}
        or output["kind"] != "foreground"
        or output["runId"] != wrapper["child_session_id"]
    ):
        raise ValueError("review_child_output")
    lines = wrapper["actual_prompt"].split("\n")
    marker = "BEGIN_PERPETUAL_REVIEW_PACKET_JSON"
    if lines.count(marker) != 1 or lines.count("END_PERPETUAL_REVIEW_PACKET_JSON") != 1:
        raise ValueError("review_input_marker")
    marker_at = lines.index(marker)
    if (
        lines[marker_at + 2] != "END_PERPETUAL_REVIEW_PACKET_JSON"
        or lines[marker_at + 1] != wrapper["review_request_json"]
    ):
        raise ValueError("review_actual_input")
    from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
        review_originals_from_prompt,
        validate_analysis_originals,
    )

    catalog = review_originals_from_prompt(wrapper["actual_prompt"])
    validate_analysis_originals(synthesis_raw, catalog)
    if catalog["run_id"] != binding["runId"] or catalog["request_hash"] != binding["requestHash"]:
        raise ValueError("review_original_scope")
    assessment_assumptions: Sequence[dict[str, Any]] = ()
    if prepared:
        witness, _ = _prepared_packet(
            notifications, pairs, freeze_id, synthesis_raw, packet_raw, binding, catalog,
            computation_memo=computation_memo,
        )
        marker = "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON"
        if lines.count(marker) != 1:
            raise ValueError("review_assumptions_missing")
        position = lines.index(marker)
        if (
            lines[position + 1] != _canonical(witness["assumptions"])
            or lines[position + 2] != "END_DECLARED_MODEL_ASSUMPTIONS_JSON"
            or lines[position + 3] != "MODEL_ASSUMPTIONS_SHA256=" + _hash(witness["assumptions"])
        ):
            raise ValueError("review_assumptions_changed")
        assessment_assumptions = witness["assumptions"]
    child = _json(wrapper["child_events_json"])
    if (
        not isinstance(child, list)
        or not child
        or any(type(row.get("seq")) is not int for row in child)
        or any(right["seq"] <= left["seq"] for left, right in zip(child, child[1:], strict=False))
    ):
        raise ValueError("review_child_sequence")
    answers = [
        row
        for row in child
        if row.get("type") == "assistant/message"
        and row.get("data", {}).get("message", {}).get("content")
    ]
    ends = [row for row in child if row.get("type") == "turn/end"]
    has_instructions = any(
        row.get("type") == "user/message"
        and isinstance(row.get("data", {}).get("source"), dict)
        and row["data"]["source"].get("kind") == "agent-instructions"
        for row in child
    )
    expected_instructions = expected_review_instructions(
        notifications, call_id=call_id, call_seq=_event_seq(notifications[start]), binding=binding,
    ) if has_instructions else None

    def content(row):
        data = row.get("data", {})
        return data.get("message", data).get("content")

    if (
        not _review_child_input_matches(child, wrapper["actual_prompt"], expected_instructions)
        or not answers
        or content(answers[-1]) != output["output"]
        or len(ends) != 1
        or ends[0].get("data", {}).get("reason", {}).get("kind") != "completed"
    ):
        raise ValueError("review_child_transcript")
    authorizations = []
    for block in output["output"]:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            value = _json(block["text"])
            authorizations.extend(value if isinstance(value, list) else [value])
        except (ValueError, TypeError):
            continue
    authorizations = [
        value
        for value in authorizations
        if isinstance(value, dict)
        and value.get("schema_version") == (
            "action-review-authorization.v3"
            if packet_raw.get("schema_version") == "action-review-request.v3"
            else "action-review-authorization.v2"
        )
    ]
    if len(authorizations) != 1:
        raise ValueError("review_exact_authorization")
    authorization = validate_action_review_binding_v2(packet_raw, authorizations[0])
    _findings(
        packet_raw,
        authorization,
        output["output"],
        catalog,
        required="FINDINGS_FIXED_FIELDS_JSON" in lines,
        expected_schema_version=_findings_schema_from_prompt(wrapper["actual_prompt"]),
        assumptions=assessment_assumptions,
    )
    at = notifications[start].payload["event"].get("time")
    if type(at) is not int or int(authorization.reviewed_at.timestamp() * 1000) != at:
        raise ValueError("review_clock")
    if datetime.fromisoformat(catalog["as_of"].replace("Z", "+00:00")).timestamp() * 1000 > at:
        raise ValueError("review_future_catalog")
    return authorization, wrapper, start, end, _hash(authorizations[0])


@event_scenario_validation_scope()
def extract_attested_perpetual_synthesis_captures(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
    request: ResearchSessionRequest | None = None,
) -> list[AttestedPerpetualSynthesisCapture]:
    """Authenticate native transcript relationships; source/PIT/owner stay kernel gates."""
    try:
        computation_memo: _PureReplayMemo = {}
        pairs = _pairs(notifications, expected_session_id)
        captures = []
        for call_id, (start, end, data, name) in pairs.items():
            if name != "decision_hub_synthesis_submit":
                continue
            event = _session_event(notifications[end])
            assert event is not None
            if _tool_result_failed(event[1]):
                continue
            values = list(_actual_return_payloads(event[1], call_id))
            if len(values) != 1:
                raise ValueError("synthesis_return_ambiguous")
            wrapper, path = values[0]
            if (
                isinstance(wrapper, dict)
                and wrapper.get("schema_version") in {
                    "decision-hub-perpetual-preparation.v1",
                    "decision-hub-perpetual-preparation.v2",
                }
            ):
                # Preparation is a successful durable freeze, not a final output.
                continue
            finalized_coverage = (
                isinstance(wrapper, dict)
                and wrapper.get("schema_version") == "decision-hub-synthesis-tool-result.v5"
            )
            phase4 = (
                isinstance(wrapper, dict)
                and wrapper.get("schema_version") in {
                    "decision-hub-synthesis-tool-result.v4",
                    "decision-hub-synthesis-tool-result.v5",
                }
            )
            if (
                not isinstance(wrapper, dict)
                or set(wrapper)
                != (
                    ({"schema_version", "candidate", "review_request", "review_gate", "preparation"}
                     | ({"coverage_delivery"} if finalized_coverage else set()))
                    if phase4
                    else {"schema_version", "candidate", "review_request", "review_gate"}
                )
                or wrapper["schema_version"]
                not in {
                    "decision-hub-synthesis-tool-result.v3",
                    "decision-hub-synthesis-tool-result.v4",
                    "decision-hub-synthesis-tool-result.v5",
                }
            ):
                raise ValueError("synthesis_protocol")
            raw, packet_raw, gate = (
                wrapper["candidate"],
                wrapper["review_request"],
                wrapper["review_gate"],
            )
            synthesis = normalize_action_synthesis(raw)
            if not isinstance(
                synthesis, (ResearchDecisionSynthesisV2, ResearchDecisionSynthesisV3)
            ):
                raise ValueError("synthesis_version")
            if isinstance(synthesis, ResearchDecisionSynthesisV3) and not phase4:
                raise ValueError("event_synthesis_preparation_required")
            packet = parse_action_review_request(packet_raw)
            event_plan = isinstance(packet, ActionReviewRequestV3)
            domain_key = "event_plan_domain" if event_plan else "allowed_domain"
            domain_ref = packet.event_plan_domain_ref if event_plan else packet.allowed_domain_ref
            if (
                packet.synthesis != synthesis
                or packet.synthesis_ref.hash != _hash(raw)
                or domain_ref.hash != _hash(packet_raw[domain_key])
                or (
                    event_plan and packet.primary_review_request_ref.hash
                    != _hash(packet_raw["primary_review_request"])
                )
            ):
                raise ValueError("packet_binding")
            expected_args = (
                {
                    "action": "finalize",
                    "request_id": synthesis.request_id,
                    "prepared_call_id": wrapper["preparation"]["prepared_call_id"],
                }
                if phase4
                else {"candidate": raw, "review_request": packet_raw}
            )
            if _arguments(data, call_id, name) != expected_args:
                raise ValueError("synthesis_call_changed")
            expected = {
                "schema_version",
                "status",
                "reason",
                "binding",
                "candidate_json",
                "candidate_sha256",
                "review_request_json",
                "review_request_sha256",
                "freeze_call_id",
                "freeze_seq",
                "review_receipt_hash",
                "review_call_id",
            }
            if (
                not isinstance(gate, dict)
                or set(gate) != expected
                or gate["schema_version"] != "decision-hub-final-review-gate.v3"
            ):
                raise ValueError("gate_protocol")
            _binding(gate["binding"], expected_session_id, request, notifications, start)
            if synthesis.request_id != (
                request.request_id if request else "research-request:" + gate["binding"]["runId"]
            ):
                raise ValueError("synthesis_request")
            call_time = notifications[start].payload["event"].get("time")
            primary_packet = primary_perpetual_review_request(packet)
            if (
                type(call_time) is not int
                or not synthesis.as_of.timestamp() * 1000
                <= call_time
                < primary_packet.allowed_domain.valid_until.timestamp() * 1000
                or (
                    event_plan
                    and call_time >= packet.event_plan_domain.freeze_before.timestamp() * 1000
                )
            ):
                raise ValueError("synthesis_expired")
            if (
                _json(gate["candidate_json"]) != raw
                or _sha(gate["candidate_json"]) != gate["candidate_sha256"]
                or _json(gate["review_request_json"]) != packet_raw
                or _sha(gate["review_request_json"]) != gate["review_request_sha256"]
            ):
                raise ValueError("gate_bytes")
            authorization = review_json = review_id = review_start = review_end = None
            if gate["status"] == "matched":
                if gate["reason"] != "exact_target" or gate["review_call_id"] not in pairs:
                    raise ValueError("gate_review_missing")
                authorization, review, review_start, review_end, authorization_hash = _review(
                    notifications,
                    pairs,
                    gate["review_call_id"],
                    gate["freeze_call_id"],
                    raw,
                    packet_raw,
                    gate["binding"],
                    computation_memo=computation_memo,
                )
                if review_end >= start or gate["review_receipt_hash"] != authorization_hash:
                    raise ValueError("gate_authorization")
                review_json, review_id = _canonical(review), gate["review_call_id"]
                if phase4:
                    history = _preparation_history(
                        notifications, pairs, before=start, binding=gate["binding"],
                        request=request,
                        computation_memo=computation_memo,
                    )
                    if (
                        not history
                        or history[-1]["call_id"] != expected_args["prepared_call_id"]
                        or history[-1].get("authorization") is None
                        or history[-1]["review_call_id"] != gate["review_call_id"]
                        or history[-1].get("findings") is not None
                        and authorization.verdict != "approved"
                    ):
                        raise ValueError("finalize_latest_approved_required")
                    from packages.kernel.decision_hub_kernel.application import (
                        perpetual_review_materials,
                    )

                    witness, _ = _prepared_packet(
                        notifications,
                        pairs,
                        gate["freeze_call_id"],
                        raw,
                        packet_raw,
                        gate["binding"],
                        perpetual_review_materials.review_originals_from_prompt(
                            review["actual_prompt"]
                        ),
                        computation_memo=computation_memo,
                    )
                    if (
                        witness != wrapper["preparation"]
                        or gate["freeze_call_id"] != expected_args["prepared_call_id"]
                    ):
                        raise ValueError("finalize_preparation_changed")
            elif (
                gate["status"] != "unreviewed"
                or gate["reason"] == "exact_target"
                or gate["review_call_id"] is not None
                or gate["review_receipt_hash"] is not None
            ):
                raise ValueError("unreviewed_gate")
            if phase4 and gate["status"] != "matched":
                raise ValueError("finalize_review_required")
            coverage_delivery = None
            if finalized_coverage:
                if authorization is None or authorization.verdict != "approved":
                    raise ValueError("finalize_latest_approved_required")
                coverage_delivery = validate_finalized_coverage(
                    wrapper["coverage_delivery"], binding=gate["binding"], request=request,
                    notifications=notifications, call_id=call_id,
                    call_seq=_event_seq(notifications[start]),
                )
            captures.append(
                AttestedPerpetualSynthesisCapture(
                    synthesis,
                    packet,
                    authorization,
                    call_id,
                    start,
                    end,
                    ("payload", "event", "data", *path, "candidate"),
                    _hash(raw),
                    _event_seq(notifications[start]),
                    _event_seq(notifications[end]),
                    review_json,
                    review_id,
                    review_start,
                    review_end,
                    coverage_delivery=coverage_delivery,
                )
            )
        captures = sorted(captures, key=lambda value: value.result_event_index)
        if captures:
            final = captures[-1]
            pair = pairs[final.call_id]
            args = _arguments(pair[2], final.call_id, pair[3])
            if args.get("action") == "finalize":
                gate_body = list(
                    _actual_return_payloads(
                        _session_event(notifications[pair[1]])[1], final.call_id
                    )
                )[0][0]
                history = _preparation_history(
                    notifications,
                    pairs,
                    before=len(notifications),
                    binding=gate_body["review_gate"]["binding"],
                    request=request,
                    computation_memo=computation_memo,
                )
                if not history or history[-1]["call_id"] != args["prepared_call_id"]:
                    raise ValueError("latest_preparation_not_finalized")
        return captures
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "Perpetual native synthesis/review proof is invalid",
            cause_code="perpetual_synthesis_attestation_invalid",
        ) from exc
