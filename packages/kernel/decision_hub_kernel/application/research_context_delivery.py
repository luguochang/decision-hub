"""Read exact selected root synthesis input and item bodies; no read-latest proof.

The retained proof concerns DSH GenerateOptions. It does not attest supplier
wire bytes, model understanding, causal correctness, usage completeness or fees.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.provider_input import (
    ContextBodyCoverage,
    ContextBodyCoverageV2,
    ResearchContextDelivery,
    ResearchContextDeliveryV2,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

from .provider_input_receipts import (
    borrowed_provider_read,
    read_provider_emission_in_session,
    read_provider_finish_in_session,
    read_provider_input_in_session,
    row_reference,
    sha,
    strict_input_json,
    strict_json,
    tool_arguments_json,
)
from .research_publication_binding import (
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
    verify_selection_witness,
)


def json_nodes(value: Any, locus: str = "$", depth: int = 0):
    """Bounded exact JSON/text loci, including actual text Tool JSON blocks."""
    if depth > 12:
        return
    yield locus, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from json_nodes(item, locus + "[" + json.dumps(key) + "]", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from json_nodes(item, locus + f"[{index}]", depth + 1)
    elif isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            decoded = strict_json(value)
        except ValueError:
            return
        yield from json_nodes(decoded, locus + "::json", depth + 1)


def _root_events(
    reply: DshSessionResult, root: str, before_index: int | None = None
) -> list[dict[str, Any]]:
    events = json.loads(reply.events_json)
    if not isinstance(events, list):
        raise ValueError("research_context_delivery_events_invalid")
    return [
        item
        for item in events[:before_index]
        if isinstance(item, dict)
        and item.get("method") == "session.event"
        and isinstance(item.get("payload"), dict)
        and item["payload"].get("sessionId") == root
    ]


def _identity(data: dict[str, Any]) -> tuple[str | None, str | None]:
    # Use the official attestation codec for both typed and public event forms.
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _tool_call_id,  # pyright: ignore[reportPrivateUsage]
        _tool_name,  # pyright: ignore[reportPrivateUsage]
    )

    return _tool_call_id(data), _tool_name(data)


def _tool_originals(
    reply: DshSessionResult, root: str, before_index: int | None
) -> dict[str, tuple[dict[str, Any], str]]:
    calls: dict[str, str] = {}
    result: dict[str, tuple[dict[str, Any], str]] = {}
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _tool_result_failed,  # pyright: ignore[reportPrivateUsage]
    )

    # Evidence observed after the selected synthesis cannot certify that
    # synthesis input. Keep the index in the original unfiltered event list.
    for item in _root_events(reply, root, before_index):
        event = item["payload"].get("event", {})
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        call_id, name = _identity(data)
        if event.get("type") == "tool/call" and call_id and name:
            calls[call_id] = name
        elif (
            event.get("type") in {"tool/result", "tool/call-result"}
            and call_id in calls
            and not _tool_result_failed(data)
        ):
            # Only actual root research tools can attest a dynamic Evidence
            # body. An independent child log or model quote cannot fill it.
            if not any(
                calls[call_id].endswith(name)
                for name in ("decision_hub_research", "research_capability_execute")
            ):
                continue
            for _, node in json_nodes(data):
                if (
                    isinstance(node, dict)
                    and isinstance(node.get("evidence_id"), str)
                    and isinstance(node.get("excerpt"), str)
                    and isinstance(node.get("content_hash"), str)
                ):
                    key = node["evidence_id"]
                    previous = result.get(key)
                    if previous is not None and previous[0] != node:
                        raise ValueError("research_context_delivery_evidence_ambiguous")
                    result[key] = node, observation_sha(item)
    return result


def _initial_inputs(session: Session, mapped_context: Any) -> tuple[dict[str, Any], ...]:
    execution, _ = _execution_original(session, mapped_context)
    # The selected generation can legitimately omit original Task inputs.
    # Only the exact server execution request defines initial body delivery.
    return tuple(item.model_dump(mode="json") for item in execution.request.input_evidence)


def _initial_prompt_sections_present(request: Any, prompt: str) -> bool:
    """Check the producer's complete control and untrusted-body codec together."""
    from packages.runtime_adapters.dsh_runtime.profile import (
        _prompt_request_sections,  # pyright: ignore[reportPrivateUsage]
    )

    canonical, bodies = _prompt_request_sections(request)
    expected = (
        "Canonical control request (untrusted text is replaced by typed references):\n"
        + canonical
        + "\nBEGIN_UNTRUSTED_RESEARCH_DATA\n"
        + bodies
        + "\nEND_UNTRUSTED_RESEARCH_DATA"
    )
    return expected in prompt


def _execution_original(session: Session, context: Any):
    from .research_execution_binding import parse_execution_receipt

    reference = next(
        (ref for ref in context.rows if ref.role == f"dsh_execution_request:{context.generation}"),
        None,
    )
    if reference is None:
        raise ValueError("research_context_delivery_execution_original_missing")
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or row.run_id != context.run_id or row_reference(row) != reference:
        raise ValueError("research_context_delivery_execution_original_changed")
    execution = parse_execution_receipt(row)
    if (
        execution.run_id != context.run_id
        or execution.generation != context.generation
        or execution.dsh_session_id != context.dsh_session_id
        or execution.request_hash != context.execution_request_hash
        or observation_sha(execution.request.model_dump(mode="json")) != execution.request_hash
    ):
        raise ValueError("research_context_delivery_execution_identity_invalid")
    return execution, reference


def _call_arguments(reply: DshSessionResult, index: int, call_id: str, root: str) -> Any:
    events = json.loads(reply.events_json)
    notification = events[index]
    payload = notification.get("payload", {})
    event = payload.get("event", {})
    data = event.get("data", {})
    if (
        notification.get("method") != "session.event"
        or payload.get("sessionId") != root
        or event.get("type") != "tool/call"
        or not isinstance(data, dict)
    ):
        raise ValueError("research_context_delivery_root_call_invalid")
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _typed_tool_identity,  # pyright: ignore[reportPrivateUsage]
    )

    if _typed_tool_identity(data, item_type="tool-call") != (
        call_id,
        "decision_hub_synthesis_submit",
    ):
        raise ValueError("research_context_delivery_root_call_invalid")
    values = []
    if "arguments" in data:
        values.append(data["arguments"])
    message = data.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for item in message["content"]:
            if (
                isinstance(item, dict)
                and item.get("type") == "tool-call"
                and item.get("id", item.get("callId")) == call_id
            ):
                if "arguments" in item:
                    values.append(item["arguments"])
                elif "args" in item:
                    values.append(item["args"])
    decoded = [strict_json(value) if isinstance(value, str) else value for value in values]
    if not decoded or any(value != decoded[0] for value in decoded):
        raise ValueError("research_context_delivery_root_call_arguments_missing")
    return decoded[0]


def _review_originals(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    mapped: Any,
    reply: DshSessionResult,
) -> dict[str, tuple[dict[str, Any], ObservedExecutionRef, dict[str, Any]]]:
    """Full root wrapper only after the actual immutable effect is reverified."""
    from packages.kernel.decision_hub_kernel.ports.research_review_effects import (
        ResearchReviewEffectOriginal,
    )
    from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
        _returned_review_wrappers,  # pyright: ignore[reportPrivateUsage]
    )

    from .research_review_effects import read_review_effects_in_session

    effects = read_review_effects_in_session(
        session, mapped_ref=mapped_ref, source_result=mapped.result
    )
    if effects is None or effects.review_effect_ref is None:
        return {}
    reference = effects.review_effect_ref
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or row_reference(row) != reference:
        raise ValueError("research_context_delivery_review_original_changed")
    original = ResearchReviewEffectOriginal.model_validate_json(row.payload_json)
    if (
        original.mapped_ref != mapped_ref
        or original.transport_ref != mapped.mapping_input_ref
        or original.run_id != mapped.context.run_id
        or original.generation != mapped.context.generation
        or original.root_session_id != mapped.context.dsh_session_id
        or original.selected_call_id != mapped.selection.call_id
        or original.selected_candidate_hash != mapped.selection.candidate_payload_hash
        or original.source_result_hash != effects.source_result_hash
        or effects.public_review_ids != (original.public_review_id,)
    ):
        raise ValueError("research_context_delivery_review_scope_invalid")
    events = json.loads(reply.events_json)
    call = events[original.reviewer_call_event_index]
    result = events[original.reviewer_result_event_index]
    if (
        observation_sha(call) != original.reviewer_call_event_hash
        or observation_sha(result) != original.reviewer_result_event_hash
        or result["payload"].get("sessionId") != mapped.context.dsh_session_id
        or mapped.selection.call_event_index is None
        or not original.reviewer_call_event_index
        < original.reviewer_result_event_index
        < mapped.selection.call_event_index
    ):
        raise ValueError("research_context_delivery_review_native_event_invalid")
    wrappers = list(
        _returned_review_wrappers(result["payload"]["event"]["data"], original.reviewer_call_id)
    )
    if len(wrappers) != 1:
        raise ValueError("research_context_delivery_review_wrapper_missing")
    wrapper = dict(wrappers[0])
    if (
        observation_sha(wrapper) != original.frozen_wrapper_hash
        or wrapper.get("candidate_sha256") != original.frozen_candidate_hash
        or wrapper.get("actual_prompt_sha256") != original.reviewer_prompt_hash
        or wrapper.get("child_session_id") != original.child_session_id
    ):
        raise ValueError("research_context_delivery_review_wrapper_changed")
    receipt = original.receipt.model_dump(mode="json")
    if observation_sha(receipt) != original.receipt_hash:
        raise ValueError("research_context_delivery_review_receipt_changed")
    projection = {
        "schema_version": "decision-hub-protected-review-body.v1",
        "run_id": original.run_id,
        "generation": original.generation,
        "root_session_id": original.root_session_id,
        "request_hash": mapped.context.first_linked_request_hash,
        "review_call_id": original.reviewer_call_id,
        "review_call_seq": call["payload"]["event"]["seq"],
        "review_result_seq": result["payload"]["event"]["seq"],
        "frozen_candidate_sha256": original.frozen_candidate_hash,
        "wrapper_sha256": original.frozen_wrapper_hash,
        "receipt_sha256": original.receipt_hash,
        "receipt": receipt,
    }
    return {"review:" + original.public_review_id: (wrapper, reference, projection)}


def _protected_native_prefix(
    *,
    census: Any,
    context: Any,
    plugin_build_hash: str,
    message: dict[str, Any],
    first: int,
    last: int,
    events: list[dict[str, Any]],
    selected_seq: Any,
) -> bool:
    """Prove only an omitted header prefix, never an arbitrary missing endpoint."""
    if (
        first != 0
        or (census.run_id, census.generation, census.root_session_id, census.request_hash)
        != (
            context.run_id,
            context.generation,
            context.dsh_session_id,
            context.first_linked_request_hash,
        )
        or census.plugin_build_hash != plugin_build_hash
        or not census.observer_installed_before_root_birth
        or set(census.missing_codes) - {"native_root_birth_missing", "native_session_not_completed"}
    ):
        return False
    root = next(
        (item for item in census.sessions if item.session_id == context.dsh_session_id), None
    )
    if (
        root is None
        or root.parent_session_id is not None
        or root.birth_status != "observed"
        or not root.settled
        or root.first_live_seq != first
        or root.native_first_seq != first
        or root.native_last_seq is None
        or root.events_json is None
        or root.events_sha256 is None
        or set(root.missing_codes) - {"native_session_not_completed"}
        or type(selected_seq) is not int
    ):
        return False
    anchors = [
        item["payload"]["event"]
        for item in events
        if item["payload"]["event"].get("type") == "user/message"
        and isinstance(item["payload"]["event"].get("data"), dict)
        and item["payload"]["event"]["data"].get("id") == message.get("id")
    ]
    return bool(
        len(anchors) == 1
        and anchors[0]["data"] == message
        and type(anchors[0].get("seq")) is int
        and first <= last < anchors[0]["seq"] < selected_seq <= root.native_last_seq
    )


def _protected_envelopes(
    session: Session,
    *,
    input_body: dict[str, Any],
    original: Any,
    execution: Any,
    reply: DshSessionResult,
    before_index: int | None,
    as_of: datetime,
    authenticated_baselines: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Recognize only the installed producer's complete plugin JSON block.

    This is still the authenticated GenerateOptions boundary. Every claimed
    dynamic DTO is compared with both root native and durable originals.
    """
    from .fact_store import _fact_model  # pyright: ignore[reportPrivateUsage]
    from .research_evidence import _evidence_model  # pyright: ignore[reportPrivateUsage]

    keys = {
        "schema_version",
        "snapshot_id",
        "run_id",
        "generation",
        "root_session_id",
        "request_hash",
        "plugin_build_hash",
        "request_id",
        "command_request_id",
        "native_first_seq",
        "native_last_seq",
        "frozen_input_evidence",
        "evidence_candidates",
        "facts",
        "independent_reviews",
        "proof_scope",
        "model_understanding_verified",
        "budget_qualified",
        "financial_qualified",
    }
    context = original.native_context
    expected_identity = {
        "schema_version": "decision-hub-protected-context.v1",
        "run_id": context.run_id,
        "generation": context.generation,
        "root_session_id": context.dsh_session_id,
        "request_hash": context.first_linked_request_hash,
        "plugin_build_hash": original.submit.plugin_build_hash,
        "request_id": execution.request.request_id,
        "command_request_id": original.prompt_request_id,
        "proof_scope": "dsh_llm_generate_options",
    }
    events = _root_events(reply, context.dsh_session_id, before_index)
    seqs = [item["payload"]["event"].get("seq") for item in events]
    census_read = False
    authenticated_census = None
    selected_native = None
    initial = [item.model_dump(mode="json") for item in execution.request.input_evidence]
    evidence = _tool_originals(reply, context.dsh_session_id, before_index)
    native_facts: dict[str, dict[str, Any]] = {}
    # Only successful, paired root research Tool results can furnish facts.
    calls: dict[str, str] = {}
    for notification in events:
        event = notification["payload"].get("event", {})
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        call_id, name = _identity(data)
        if event.get("type") == "tool/call" and call_id and name:
            calls[call_id] = name
        elif event.get("type") in {"tool/result", "tool/call-result"} and call_id in calls:
            from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
                _tool_result_failed,  # pyright: ignore[reportPrivateUsage]
            )

            if _tool_result_failed(data) or not any(
                calls[call_id].endswith(tool)
                for tool in (
                    "decision_hub_research",
                    "research_capability_execute",
                )
            ):
                continue
            for _, node in json_nodes(data):
                if isinstance(node, dict) and isinstance(node.get("fact_id"), str):
                    previous = native_facts.get(node["fact_id"])
                    if previous is not None and previous != node:
                        raise ValueError("research_context_delivery_fact_ambiguous")
                    native_facts[node["fact_id"]] = node
    accepted = []
    for index, message in enumerate(input_body["messages"]):
        if (
            not isinstance(message, dict)
            or message.get("role") != "user"
            or message.get("source")
            != {"kind": "plugin", "plugin": "decision-hub-protected-context"}
        ):
            continue
        blocks = message.get("content")
        if not isinstance(blocks, list) or len(blocks) != 2 or not isinstance(blocks[1], dict):
            continue
        text = blocks[1].get("text")
        if blocks[1].get("type") != "text" or not isinstance(text, str) or not text.startswith("{"):
            continue
        try:
            body = strict_json(text)
        except ValueError:
            continue
        expected_keys = keys
        if isinstance(body, dict):
            if body.get("schema_version") in {"decision-hub-protected-context.v2", "decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}:
                expected_keys = expected_keys | {"server_baseline"}
            if body.get("schema_version") == "decision-hub-protected-context.v4":
                expected_keys = expected_keys | {"shared_fact_attributes"}
        if (
            not isinstance(body, dict)
            or set(body) != expected_keys
            or any(body.get(key) != value for key, value in expected_identity.items() if key != "schema_version")
            or body.get("schema_version") not in {"decision-hub-protected-context.v1", "decision-hub-protected-context.v2", "decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}
            or type(body["generation"]) is not int
            or any(
                body[key] is not False
                for key in (
                    "model_understanding_verified",
                    "budget_qualified",
                    "financial_qualified",
                )
            )
            or not isinstance(body["snapshot_id"], str)
            or not body["snapshot_id"]
            or body["snapshot_id"] != message.get("id")
            or observation_sha(body["frozen_input_evidence"]) != observation_sha(initial)
            or any(
                not isinstance(body[key], list)
                for key in (
                    "evidence_candidates",
                    "facts",
                    "independent_reviews",
                )
            )
        ):
            continue
        first, last = body["native_first_seq"], body["native_last_seq"]
        if (
            (first is None) != (last is None)
            or first is not None
            and (
                type(first) is not int
                or type(last) is not int
                or last not in seqs
                or first < 0
                or first > last
            )
        ):
            continue
        if first is not None and first not in seqs:
            if first != 0 or before_index is None:
                continue
            # The Host uses complete official native events; transport omits
            # request/header. Its absence cannot establish a missing body.
            # Read the bound census in this same physical snapshot, once for
            # this invocation. All source/model originals and time stay gated.
            from .native_conversation_census import read_native_census_in_session

            if not census_read:
                authenticated_census = read_native_census_in_session(
                    session,
                    run_id=context.run_id,
                    generation=context.generation,
                    as_of=as_of,
                )
                selected_native = json.loads(reply.events_json)[before_index]
                census_read = True
            if (
                authenticated_census is None
                or authenticated_census[0].native_context != context
                or any(
                    ref.recorded_at > authenticated_census[0].known_at
                    for ref in (
                        authenticated_census[1],
                        *authenticated_census[0].source_refs,
                        *authenticated_census[0].model_binding_refs,
                    )
                )
                or not isinstance(selected_native, dict)
                or selected_native.get("method") != "session.event"
                or selected_native.get("payload", {}).get("sessionId") != context.dsh_session_id
                or selected_native["payload"].get("event", {}).get("type") != "tool/call"
                or not _protected_native_prefix(
                    census=authenticated_census[0].census,
                    context=context,
                    plugin_build_hash=original.submit.plugin_build_hash,
                    message=message,
                    first=first,
                    last=last,
                    events=events,
                    selected_seq=selected_native["payload"]["event"].get("seq"),
                )
            ):
                continue
        baseline_evidence, baseline_facts = {}, {}
        delivery = None
        is_projection = body["schema_version"] in {"decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}
        semantic_facts = body["facts"]
        if body["schema_version"] == "decision-hub-protected-context.v4":
            from .research_baseline_projection import restore_fact_attributes

            semantic_facts = restore_fact_attributes(body["facts"], body["shared_fact_attributes"])
        if body["schema_version"] in {"decision-hub-protected-context.v2", "decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}:
            from ..ports.research_baseline import BaselineRequest
            from .research_baseline import read_baseline_delivery_in_session
            delivery = read_baseline_delivery_in_session(session, request=BaselineRequest(
                schema_version="research-baseline-request.v1", run_id=context.run_id,
                research_session_id=context.dsh_session_id, generation=context.generation,
                request_hash=context.first_linked_request_hash), as_of=original.known_at)
            if is_projection:
                from .research_baseline_projection import project_baseline_delivery

                if delivery is None:
                    raise ValueError("research_context_baseline_original_mismatch")
                expected_projection = project_baseline_delivery(
                    delivery.model_dump(mode="json"), facts=semantic_facts,
                    evidence_candidates=body["evidence_candidates"],
                )
                if tool_arguments_json(expected_projection) != tool_arguments_json(body["server_baseline"]):
                    raise ValueError("research_context_baseline_original_mismatch")
            elif delivery is None or observation_sha(delivery.model_dump(mode="json")) != observation_sha(body["server_baseline"]):
                raise ValueError("research_context_baseline_original_mismatch")
            if sum(isinstance(other, dict) and other.get("id") == message.get("id")
                   for other in input_body["messages"]) != 1:
                raise ValueError("research_context_baseline_snapshot_ambiguous")
            # The batch is not a native Tool. Authenticate the actual plugin
            # event and the claimed current-generation prompt independently.
            # SDK order: pre-step snapshot, step/start, claimed prompt, plugin.
            anchors = [item["payload"]["event"] for item in events
                       if item["payload"]["event"].get("type") == "user/message"
                       and item["payload"]["event"].get("data", {}).get("id") == message.get("id")]
            if (len(anchors) != 1 or anchors[0].get("data") != message
                    or type(anchors[0].get("seq")) is not int
                    or (last is not None and anchors[0]["seq"] <= last)):
                raise ValueError("research_context_baseline_native_anchor_missing")
            prompt = session.get(DshSessionPromptRecord, (context.run_id, context.generation), populate_existing=True)
            def exact_prompt(other):
                return (isinstance(other, dict) and isinstance(other.get("source"), dict)
                    and other["source"].get("kind") == "user"
                    and other["source"].get("rpcId") == original.prompt_request_id
                    and isinstance(other.get("content"), list)
                    and any(isinstance(block, dict) and block.get("type") == "text"
                            and prompt is not None and block.get("text") == prompt.prompt
                            for block in other["content"]))
            native_prompts = [item["payload"]["event"] for item in events
                              if item["payload"]["event"].get("type") == "user/message"
                              and exact_prompt(item["payload"]["event"].get("data"))]
            if (len(native_prompts) != 1 or type(native_prompts[0].get("seq")) is not int
                    or native_prompts[0]["seq"] >= anchors[0]["seq"]):
                raise ValueError("research_context_baseline_native_prompt_missing")
            prompt_seq = native_prompts[0]["seq"]
            if first is None or prompt_seq > last:
                exact = [other for other in input_body["messages"] if exact_prompt(other)]
                steps = [item["payload"]["event"]["seq"] for item in events
                         if item["payload"]["event"].get("type") == "step/start"
                         and type(item["payload"]["event"].get("seq")) is int
                         and (last is None or item["payload"]["event"]["seq"] > last)
                         and item["payload"]["event"]["seq"] < prompt_seq]
                if len(exact) != 1 or len(steps) != 1 or body["independent_reviews"]:
                    raise ValueError("research_context_baseline_initial_prompt_missing")
            baseline_evidence = {item.evidence_id: item.model_dump(mode="json")
                for result in delivery.results for item in result.evidence_candidates}
            baseline_facts = {item.fact_id: item.model_dump(mode="json")
                for result in delivery.results for item in (result.facts or [])}
        identity = tool_arguments_json if is_projection else observation_sha
        valid = True
        ids = []
        for item in body["evidence_candidates"]:
            key = item.get("evidence_id") if isinstance(item, dict) else None
            if not isinstance(key, str) or not key:
                valid = False
                break
            row = session.get(ResearchEvidenceRecord, key, populate_existing=True) if key else None
            pair = evidence.get(key)
            baseline_item = baseline_evidence.get(key)
            if (
                row is None
                or row.run_id != context.run_id
                or row.quality != "accepted"
                or (
                    (pair is None and baseline_item is None)
                    or identity(pair[0] if pair is not None else baseline_item) != identity(item)
                    or tool_arguments_json(_evidence_model(row).model_dump(mode="json"))
                    != tool_arguments_json(item)
                )
            ):
                valid = False
                break
            ids.append(key)
        if len(set(ids)) != len(ids):
            valid = False
        fact_ids = []
        for item in semantic_facts:
            key = item.get("fact_id") if isinstance(item, dict) else None
            if not isinstance(key, str) or not key:
                valid = False
                break
            row = session.get(ResearchFactRecord, key, populate_existing=True) if key else None
            if (
                row is None
                or row.run_id != context.run_id
                or row.quality != "accepted"
                or (
                    identity(native_facts.get(key, baseline_facts.get(key))) != identity(item)
                    or tool_arguments_json(_fact_model(row).model_dump(mode="json"))
                    != tool_arguments_json(item)
                    or item.get("evidence_id") not in ids
                )
            ):
                valid = False
                break
            fact_ids.append(key)
        if len(set(fact_ids)) != len(fact_ids):
            valid = False
        if valid:
            if authenticated_baselines is not None and delivery is not None:
                authenticated_baselines[body["snapshot_id"]] = delivery
            accepted.append((f"$.messages[{index}].content[1].text::json", body))
    return accepted


@borrowed_provider_read
def read_context_delivery_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    additional_required_refs: tuple[str, ...] = (),
) -> ResearchContextDelivery:
    """Caller-owned physical snapshot, zero writes and no authority from caller refs.

    The mapped locator must already be chosen by the real Graph admission.
    Additional server refs can only enlarge the independently derived census.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("research_context_delivery_physical_snapshot_required")
    changes = native.total_changes
    with session.no_autoflush:
        mapped = read_mapped_observation(session, mapped_ref)
        raw = read_transport_observation(session, mapped.mapping_input_ref)
        if raw.context != mapped.context or not isinstance(raw.reply, DshSessionResult):
            raise ValueError("research_context_delivery_transport_identity_invalid")
        verify_selection_witness(raw.reply, mapped.selection)
        context, selected = mapped.context, mapped.selection
        chains = (
            []
            if mapped.result.causal_case is None
            else [
                *mapped.result.causal_case.main_chain,
                *mapped.result.causal_case.opposite_chain,
            ]
        )
        required = tuple(
            dict.fromkeys(
                [
                    *(ref for chain in chains for ref in chain.evidence_refs),
                    *(ref for horizon in mapped.result.horizons for ref in horizon.evidence_refs),
                    *(
                        ref
                        for review in mapped.result.reviews or []
                        for ref in review.evidence_refs
                    ),
                    *additional_required_refs,
                ]
            )
        )
        execution, _ = _execution_original(session, context)
        initial = {
            item.evidence_id: item.model_dump(mode="json")
            for item in execution.request.input_evidence
        }
        # Initial Task refs are retained separately from the dynamic census.
        required = tuple(dict.fromkeys([*initial, *required]))
        review_refs = tuple("review:" + review.review_id for review in mapped.result.reviews or [])
        if len(set(review_refs)) != len(review_refs) or set(review_refs) & set(initial):
            raise ValueError("research_context_delivery_review_reference_ambiguous")
        required = tuple(dict.fromkeys([*required, *review_refs]))
        errors: list[str] = []
        emission_ref = input_ref = finish_ref = None
        input_nodes: list[tuple[str, Any]] = []
        root_verified = False
        full_prompt = False
        initial_bodies_verified = False
        protected: list[tuple[str, dict[str, Any]]] = []
        authenticated_baselines: dict[str, Any] = {}
        if selected.kind != "tool_capture" or not selected.call_id:
            errors.append("provider_selected_root_tool_witness_missing")
        else:
            saved = read_provider_emission_in_session(
                session,
                run_id=context.run_id,
                generation=context.generation,
                root_session_id=context.dsh_session_id,
                call_id=selected.call_id,
                execution_session_id=context.dsh_session_id,
            )
            if saved is None:
                errors.append("provider_selected_root_emission_missing")
            else:
                emission, emission_ref = saved
                original, input_ref = read_provider_input_in_session(
                    session, emission.submit.input_ref
                )
                assert selected.call_event_index is not None
                actual_arguments = _call_arguments(
                    raw.reply, selected.call_event_index, selected.call_id, context.dsh_session_id
                )
                if (
                    original.native_context != context
                    or emission.candidate_hash != selected.candidate_payload_hash
                    or sha(tool_arguments_json(actual_arguments)) != emission.submit.arguments_hash
                    or emission.known_at > raw.recorded_at
                ):
                    raise ValueError("research_context_delivery_selected_candidate_mismatch")
                input_body = strict_input_json(original.submit.input_json)
                input_nodes = list(json_nodes(input_body))
                root_verified = True
                protected = _protected_envelopes(
                    session,
                    input_body=input_body,
                    original=original,
                    execution=execution,
                    reply=raw.reply,
                    before_index=selected.call_event_index,
                    as_of=raw.recorded_at,
                    authenticated_baselines=authenticated_baselines,
                )
                prompt = session.get(
                    DshSessionPromptRecord,
                    (context.run_id, context.generation),
                    populate_existing=True,
                )
                assert prompt is not None
                # Exact original prompt in an actual user text block. IDs or
                # fragments elsewhere never stand in for initial body delivery.
                full_prompt = any(
                    isinstance(message, dict)
                    and isinstance(message.get("source"), dict)
                    and message["source"].get("kind") == "user"
                    and isinstance(message.get("content"), list)
                    and any(
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and block.get("text") == prompt.prompt
                        for block in message["content"]
                    )
                    for message in input_body["messages"]
                )
                initial_bodies_verified = full_prompt and _initial_prompt_sections_present(
                    execution.request, prompt.prompt
                )
                if protected:
                    # Complete frozen DTOs replace no authority or control
                    # rules; they retain the same authenticated initial bodies.
                    full_prompt = initial_bodies_verified = True
                finish = read_provider_finish_in_session(session, original)
                if finish is None:
                    errors.append("provider_selected_dispatch_finish_missing")
                else:
                    finish_value, finish_ref = finish
                    if finish_value.known_at > raw.recorded_at:
                        raise ValueError("research_context_delivery_finish_clock_invalid")
                    if finish_value.submit.status != "completed":
                        errors.append("provider_selected_dispatch_not_completed")
        baseline_originals = {}
        for locus, body in protected:
            if body["schema_version"] in {"decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}:
                # Full original came from the successful reader above. Keep
                # actual input/body untouched; coverage searches its top DTOs.
                full_delivery = authenticated_baselines.get(body["snapshot_id"])
                if full_delivery is None:
                    raise ValueError("research_context_baseline_original_missing")
                for result in full_delivery.results:
                    for candidate in result.evidence_candidates:
                        baseline_originals[candidate.evidence_id] = (
                            candidate.model_dump(mode="json"), full_delivery.batch_ref, locus,
                        )
                continue
            if body["schema_version"] != "decision-hub-protected-context.v2":
                continue
            reference = ObservedExecutionRef.model_validate_json(json.dumps(body["server_baseline"]["batch_ref"]))
            for result in body["server_baseline"]["results"]:
                for candidate in result["evidence_candidates"]:
                    baseline_originals[candidate["evidence_id"]] = (candidate, reference, locus)
        coverage_type = ContextBodyCoverageV2 if baseline_originals else ContextBodyCoverage
        originals = _tool_originals(raw.reply, context.dsh_session_id, selected.call_event_index)
        review_originals = (
            _review_originals(session, mapped_ref=mapped_ref, mapped=mapped, reply=raw.reply)
            if review_refs
            else {}
        )
        # A missing mapped public review is not an accepted-empty V2 review.
        # Ordinary live synthesis needs its authenticated root wrapper proof.
        # Replay/Harness retains its prior optional-review semantics.
        if execution.request.execution_mode == "live" and not review_originals:
            errors.append("provider_authenticated_root_review_original_missing")
        items = []
        for evidence_ref in required:
            if evidence_ref in review_refs:
                review = review_originals.get(evidence_ref)
                wrapper = review[0] if review is not None else None
                loci = tuple(
                    locus
                    for locus, value in input_nodes
                    if wrapper is not None and value == wrapper
                )
                projection = review[2] if review is not None else None
                loci += tuple(
                    locus + "[independent_reviews][0]"
                    for locus, body in protected
                    if projection is not None
                    and observation_sha(body["independent_reviews"])
                    == observation_sha([projection])
                    and type(body["native_first_seq"]) is int
                    and type(body["native_last_seq"]) is int
                    and body["native_first_seq"]
                    <= projection["review_call_seq"]
                    < projection["review_result_seq"]
                    <= body["native_last_seq"]
                )
                covered = bool(root_verified and wrapper is not None and loci)
                items.append(
                    coverage_type(
                        evidence_ref=evidence_ref,
                        body_hash=observation_sha(wrapper)
                        if wrapper is not None
                        else sha("unavailable:" + evidence_ref),
                        source_kind="root_child_summary",
                        original_ref=review[1] if review is not None else None,
                        input_loci=loci,
                        covered=covered,
                        reason_code=None
                        if covered
                        else "provider_root_review_wrapper_body_missing"
                        if wrapper is not None
                        else "provider_authenticated_root_review_original_missing",
                    )
                )
                continue
            item = initial.get(evidence_ref)
            if item is not None:
                covered = root_verified and initial_bodies_verified
                items.append(
                    coverage_type(
                        evidence_ref=evidence_ref,
                        body_hash=observation_sha(item),
                        source_kind="initial_prompt",
                        original_ref=mapped_ref,
                        input_loci=tuple(
                            locus + "[frozen_input_evidence]" for locus, _ in protected
                        )
                        or ("$.messages[user].content[exact_original_prompt]",)
                        if covered
                        else (),
                        covered=covered,
                        reason_code=None if covered else "provider_initial_prompt_body_missing",
                    )
                )
                continue
            baseline = baseline_originals.get(evidence_ref)
            if baseline is not None:
                node, reference, locus = baseline
                loci = tuple(path for path, value in input_nodes if value == node)
                covered = bool(root_verified and loci)
                items.append(coverage_type(evidence_ref=evidence_ref,
                    body_hash=observation_sha(node), source_kind="server_baseline",
                    original_ref=reference, input_loci=loci, covered=covered,
                    reason_code=None if covered else "provider_server_baseline_body_missing"))
                continue
            original = originals.get(evidence_ref)
            row = session.scalar(
                select(ResearchEvidenceRecord)
                .filter_by(run_id=context.run_id, evidence_id=evidence_ref)
                .execution_options(populate_existing=True)
            )
            node = original[0] if original is not None else None
            lawful = (
                node is not None
                and row is not None
                and row.quality == "accepted"
                and (row.excerpt, row.content_hash, row.source_content_hash)
                == (node.get("excerpt"), node.get("content_hash"), node.get("source_content_hash"))
            )
            loci = tuple(locus for locus, value in input_nodes if lawful and value == node)
            covered = bool(root_verified and lawful and loci)
            items.append(
                coverage_type(
                    evidence_ref=evidence_ref,
                    body_hash=observation_sha(node)
                    if node is not None
                    else sha("unavailable:" + evidence_ref),
                    source_kind="root_tool_result" if lawful else "unavailable",
                    original_ref=mapped.mapping_input_ref if lawful else None,
                    input_loci=loci,
                    covered=covered,
                    reason_code=None
                    if covered
                    else (
                        "provider_dynamic_body_missing"
                        if lawful
                        else "provider_root_admitted_body_original_missing"
                    ),
                )
            )
        if any(not item.covered for item in items):
            errors.append("provider_required_context_body_incomplete")
        if not full_prompt:
            errors.append("provider_initial_prompt_body_missing")
        # The source is the authenticated server execution request. A model's
        # Result manifest cannot create selection authority or replace it.
        manifest = execution.request.context_manifest
        selection_manifest_hash = (
            observation_sha(manifest.model_dump(mode="json")) if manifest else None
        )
        if manifest is None:
            errors.append("provider_selection_manifest_missing")
        elif (
            mapped.result.context_manifest is not None
            and mapped.result.context_manifest.model_dump(mode="json")
            != manifest.model_dump(mode="json")
        ):
            errors.append("provider_result_selection_manifest_mismatch")
        result = (ResearchContextDeliveryV2 if baseline_originals else ResearchContextDelivery)(
            schema_version="research-context-delivery.v2" if baseline_originals else "research-context-delivery.v1",
            run_id=context.run_id,
            generation=context.generation,
            root_session_id=context.dsh_session_id,
            selected_call_id=selected.call_id,
            selected_candidate_hash=selected.candidate_payload_hash,
            mapped_ref=mapped_ref,
            transport_ref=mapped.mapping_input_ref,
            provider_input_ref=input_ref,
            provider_emission_ref=emission_ref,
            provider_finish_ref=finish_ref,
            selection_manifest_hash=selection_manifest_hash,
            items=tuple(items),
            required_refs=required,
            root_synthesis_input_verified=root_verified,
            context_fidelity_verified=bool(
                root_verified and items and all(item.covered for item in items) and not errors
            ),
            missing_codes=tuple(dict.fromkeys(errors)),
        )
        if native.total_changes != changes or not native.in_transaction:
            raise ValueError("research_context_delivery_snapshot_changed")
        return result


class ResearchContextDeliveryQuery:
    def __init__(self, database: Database) -> None:
        self.database = database

    def read(self, run_id: str) -> ResearchContextDelivery:
        # The actual persisted publication selects the exact Graph locator.
        # No current/last mapped output is selected as a substitute.
        from .commit import PUBLICATION_SOURCE_ROLE
        from .research_quality_view import (
            ResearchQualityViewV1,
            consume_published_research_originals_in_session,
        )

        def consume(
            quality: ResearchQualityViewV1, mapped_ref: ObservedExecutionRef | None
        ) -> ResearchContextDelivery:
            if quality.status != "available" or mapped_ref is None:
                raise KeyError(run_id)
            row = session.scalar(
                select(RunRoleOutputRecord)
                .filter_by(run_id=run_id, role=PUBLICATION_SOURCE_ROLE)
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise KeyError(run_id)
            if row_reference(row) != quality.publication_ref:
                raise ValueError("research_context_delivery_publication_changed")
            return read_context_delivery_in_session(session, mapped_ref=mapped_ref)

        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            # This actual reader checks the saved publication/Artifact/Gate,
            # not only a caller-rehashed row that points at some older final.
            return consume_published_research_originals_in_session(
                session,
                run_id,
                as_of=utcnow(),
                consume=consume,
            )
