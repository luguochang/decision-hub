"""Exact native Action source controls; component proof, not a live publication."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest

from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.kernel.decision_hub_kernel.application.action_research_reader import (
    _native_action_from_originals,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservation,
    ResearchNativeObservationContext,
    ResearchTransportObservation,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchNativeSelection
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from tests.decision_product.test_native_action_synthesis import sha, wire_fixture
from tests.runtime.test_dsh_research_runtime import NOW


def test_frozen_decision_protocol_survives_real_event_mapper_then_result_mapper():
    request, run, _candidate = wire_fixture()
    traces = map_notifications(
        run.notifications,
        run_id=request.run_id,
        research_session_id=run.session_id,
        received_at=NOW + timedelta(seconds=1),
        request=request,
    )
    mapped = map_session_result_with_witness(
        run,
        request,
        traces,
        runtime_version="fixture",
        profile_ref="decision-research.web.v1",
        started_at=NOW - timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=1),
        accepted_synthesis_only=True,
    )
    assert traces and mapped.action_synthesis is not None
    assert mapped.selection.candidate == mapped.action_synthesis.synthesis.research_candidate_v1


def test_wrapper_cannot_switch_event_mapper_without_frozen_new_protocol():
    request, run, _candidate = wire_fixture()
    for frozen in (
        None,
        request.model_copy(update={"output_schema_ref": "research-session-result.v1"}),
    ):
        with pytest.raises(AgentExecutionError, match="frozen output protocol"):
            map_notifications(
                run.notifications,
                run_id=request.run_id,
                research_session_id=run.session_id,
                received_at=NOW,
                request=frozen,
            )


def _with_candidate(run, candidate):
    """Freeze the same entire candidate at every real native proof location."""
    events = deepcopy(run.notifications)
    text = observation_json(candidate)
    for index in (0, 4):
        events[index].payload["event"]["data"]["arguments"]["candidate"] = deepcopy(candidate)
    wrapper = events[-1].payload["event"]["data"]["result"]
    wrapper["candidate"] = deepcopy(candidate)
    gate = wrapper["review_gate"]
    gate["candidate_json"], gate["candidate_sha256"] = text, sha(text)
    review = events[3].payload["event"]["data"]["result"]
    review["candidate_json"], review["candidate_sha256"] = text, sha(text)
    control = {
        "schema_version": "decision-hub-frozen-review-input.v2",
        "candidate_sha256": sha(text),
        "expected_target_claim": review["expected_target_claim"],
        "frozen_candidate": candidate,
    }
    prompt = (
        "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n"
        + observation_json(control)
        + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
    )
    review["actual_prompt"], review["actual_prompt_sha256"] = prompt, sha(prompt)
    return replace(run, notifications=tuple(events))


def originals(*, matched=True, extra_envelope=False, run=None, request=None, align_cutoff=True):
    """Actual native mapper components, no fabricated committed publication."""
    if run is None:
        request, run, _ = wire_fixture(matched=matched)
    if align_cutoff:
        candidate = deepcopy(run.notifications[-1].payload["event"]["data"]["result"]["candidate"])
        for item in candidate["action_intents"]:
            item["as_of"] = NOW.isoformat()
            item["valid_until"] = (NOW + timedelta(hours=24)).isoformat()
            item["next_review_at"] = (NOW + timedelta(hours=1)).isoformat()
            for key in ("entry", "exit", "invalidation"):
                for condition in item[key]:
                    condition["valid_until"] = item["valid_until"]
        for item in candidate["mechanism_reviews"]:
            item["cutoff"] = NOW.isoformat()
        run = _with_candidate(run, candidate)
    events = [{"method": item.method, "payload": item.payload} for item in run.notifications]
    if extra_envelope:
        events[-1]["host_envelope_metadata"] = "retained"
        events[-2]["host_envelope_metadata"] = "retained"
    events_json = observation_json(events)
    finish = NOW + timedelta(seconds=1)
    payload = DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id=request.run_id,
        dsh_session_id=run.session_id,
        generation=1,
        last_seq=6,
        final_response=run.final_response,
        finish_reason=run.finish_reason,
        events_json=events_json,
        started_at=NOW - timedelta(seconds=1),
        finished_at=finish,
        trace_ref="fixture://native",
        result_hash=sha(run.final_response + "\0" + events_json),
    )
    context = ResearchNativeObservationContext(
        run_id=request.run_id,
        event_id=request.event_id,
        snapshot_id=request.trigger_snapshot_id,
        request_id=request.request_id,
        dsh_session_id=run.session_id,
        generation=1,
        original_task_request_hash=research_request_hash(request),
        bundle_hash="b" * 64,
        method_hash="c" * 64,
        # The producer binds the whole frozen execution request, including this
        # round's evidence/deadline, separately from the stable link identity.
        execution_request_hash=observation_sha(request.model_dump(mode="json")),
        first_linked_request_hash=research_request_hash(request),
        prompt_hash="d" * 64,
        rows=(),
    )
    raw = ResearchTransportObservation(
        schema_version="research-transport-observation.v1",
        context=context,
        phase="result",
        reply=payload,
        error_code=None,
        events_json_hash=sha(events_json),
        final_response_hash=sha(run.final_response),
        recorded_at=finish,
    )
    reference = ObservedExecutionRef(
        output_id="raw-fixture",
        role="raw-fixture",
        schema_version=raw.schema_version,
        payload_hash=observation_sha(raw.model_dump(mode="json")),
        recorded_at=finish,
    )
    traces = map_notifications(
        run.notifications,
        run_id=request.run_id,
        research_session_id=run.session_id,
        received_at=finish,
        request=request,
    )
    mapped = map_session_result_with_witness(
        run,
        request,
        traces,
        runtime_version="fixture",
        profile_ref="decision-research.web.v1",
        started_at=payload.started_at,
        finished_at=finish,
        accepted_synthesis_only=True,
    )
    selection = mapped.selection.model_copy(
        update={
            "call_event_hash": observation_sha(events[mapped.selection.call_event_index]),
            "result_event_hash": observation_sha(events[mapped.selection.result_event_index]),
        }
    )
    selected = ResearchMappedObservation(
        schema_version="research-mapped-observation.v1",
        context=context,
        mapping_input_ref=reference,
        latest_transport_ref=reference,
        mapping_input_generation=1,
        latest_transport_generation=1,
        selection=selection,
        result=mapped.result,
        result_content_hash=research_result_content_hash(mapped.result),
        recorded_at=finish,
    )
    return request, raw, selected


@pytest.mark.parametrize("matched", [False, True])
def test_complete_execution_request_hash_matches_real_native_context_semantics(matched):
    request, raw, selected = originals(matched=matched)
    assert raw.context.execution_request_hash == observation_sha(request.model_dump(mode="json"))
    assert raw.context.execution_request_hash != research_request_hash(request)
    native = _native_action_from_originals(
        selected=selected,
        raw=raw,
        request=request,
        task_created_at=NOW,
        decision_cutoff_at=request.pit_cutoff_at,
    )
    assert native.review_gate_status == ("matched" if matched else "unreviewed")
    assert native.execution_request_hash == raw.context.execution_request_hash


@pytest.mark.parametrize(
    "field", ["deadline_at", "current_round", "evidence_refs", "input_evidence"]
)
def test_same_link_identity_cannot_replace_complete_execution_request(field):
    request, raw, selected = originals()
    changes = {
        "deadline_at": request.deadline_at + timedelta(seconds=1),
        "current_round": request.current_round + 1,
        "evidence_refs": [*request.evidence_refs, "other-evidence"],
        "input_evidence": [],
    }
    changed = request.model_copy(update={field: changes[field]})
    assert changed.model_dump(mode="json") != request.model_dump(mode="json")
    assert research_request_hash(changed) == research_request_hash(request)
    with pytest.raises(ValueError, match="action_native_original_identity_mismatch"):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            request=changed,
            task_created_at=NOW,
            decision_cutoff_at=request.pit_cutoff_at,
        )


@pytest.mark.parametrize("extra_envelope", [False, True])
def test_exact_native_original_replays_full_wrapper_and_raw_envelope(extra_envelope):
    request, raw, selected = originals(extra_envelope=extra_envelope)
    old_hash = observation_sha(selected.result.model_dump(mode="json"))
    value = _native_action_from_originals(
        selected=selected,
        raw=raw,
        task_created_at=NOW,
        request=request,
        decision_cutoff_at=request.pit_cutoff_at + timedelta(hours=1),
    )
    witness = ResearchNativeSelection.model_validate_json(value.selection_witness_json)
    assert value.raw_candidate_hash == sha(value.raw_candidate_json)
    assert value.producer_cutoff_at == request.pit_cutoff_at
    assert value.execution_mode == request.execution_mode
    assert value.task_request_hash == raw.context.original_task_request_hash
    assert value.execution_request_hash == raw.context.execution_request_hash
    assert value.producer_generation == 1
    assert value.mapping_input_ref == selected.mapping_input_ref
    assert witness.presence_ref == selected.mapping_input_ref
    assert witness.presence_generation == 1 and witness.origin_generation is None
    assert witness.call_event_hash == selected.selection.call_event_hash
    assert witness.result_event_hash == selected.selection.result_event_hash
    assert tuple(witness.payload_path) == selected.selection.payload_path[:-1]
    assert value.projected_research_candidate_hash == selected.selection.candidate_payload_hash
    assert value.raw_context_hash == observation_sha(raw.context.model_dump(mode="json"))
    assert json.loads(value.producer_input_evidence_json) == [
        item.model_dump(mode="json") for item in request.input_evidence
    ]
    assert value.review_gate_status == "matched" and value.review_effect_ref is None
    assert observation_sha(selected.result.model_dump(mode="json")) == old_hash


@pytest.mark.parametrize(
    "field,value",
    [
        ("call_id", "other-call"),
        ("call_seq", 99),
        ("result_seq", 99),
        ("payload_occurrence", 1),
        ("presence_generation", 2),
        ("final_response_hash", "f" * 64),
    ],
)
def test_same_candidate_does_not_hide_other_witness_mutation(field, value):
    request, raw, selected = originals()
    selected = selected.model_copy(
        update={
            "selection": selected.selection.model_copy(update={field: value}),
        }
    )
    with pytest.raises(ValueError, match="action_native_full_selection_mismatch"):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            task_created_at=NOW,
            request=request,
            decision_cutoff_at=request.pit_cutoff_at,
        )


@pytest.mark.parametrize("mutation", ["protocol", "cutoff", "request", "generation", "raw_hash"])
def test_native_reader_rejects_wrong_frozen_scope_and_cutoff(mutation):
    request, raw, selected = originals()
    cutoff = request.pit_cutoff_at
    if mutation == "protocol":
        request = request.model_copy(update={"output_schema_ref": "research-session-result.v1"})
    elif mutation == "cutoff":
        cutoff -= timedelta(microseconds=1)
    elif mutation == "request":
        request = request.model_copy(update={"request_id": "another-request"})
    elif mutation == "generation":
        selected = selected.model_copy(update={"mapping_input_generation": 2})
    else:
        raw = raw.model_copy(update={"events_json_hash": "a" * 64})
    with pytest.raises(ValueError, match="action_native_"):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            task_created_at=NOW,
            request=request,
            decision_cutoff_at=cutoff,
        )


def test_unreviewed_native_stays_explicitly_unreviewed():
    request, raw, selected = originals(matched=False)
    value = _native_action_from_originals(
        selected=selected,
        raw=raw,
        task_created_at=NOW,
        request=request,
        decision_cutoff_at=request.pit_cutoff_at,
    )
    assert value.review_gate_status == "unreviewed" and value.review_effect_ref is None


def test_latest_transport_and_tool_accounting_do_not_replace_selected_candidate():
    request, raw, selected = originals()
    later_ref = selected.mapping_input_ref.model_copy(update={"output_id": "later-rejected"})
    changed_result = selected.result.model_copy(update={"total_tool_calls": 20})
    # Production normalizes accounting after mapping. Candidate/witness identity
    # remains exact; a latest raw ref must not be consumed in place of map input.
    selected = selected.model_copy(
        update={
            "latest_transport_ref": later_ref,
            "latest_transport_generation": 3,
            "result": changed_result,
            "result_content_hash": research_result_content_hash(changed_result),
        }
    )
    value = _native_action_from_originals(
        selected=selected,
        raw=raw,
        task_created_at=NOW,
        request=request,
        decision_cutoff_at=request.pit_cutoff_at,
    )
    assert value.mapping_input_ref != later_ref and value.producer_generation == 1


def test_raw_integer_is_not_rehashed_from_normalized_float_model():
    request, run, _ = wire_fixture()
    events = deepcopy(run.notifications)
    candidate = events[-1].payload["event"]["data"]["result"]["candidate"]
    candidate["action_intents"][0]["probability"].update(kind="subjective", value=1)
    # This mutation must be reflected in all four frozen references; no proof is
    # bypassed. The integer object is distinct from typed model serialization.
    text = observation_json(candidate)
    for index in (0, 4):
        events[index].payload["event"]["data"]["arguments"]["candidate"] = deepcopy(candidate)
    gate = events[-1].payload["event"]["data"]["result"]["review_gate"]
    gate["candidate_json"], gate["candidate_sha256"] = text, sha(text)
    review = events[3].payload["event"]["data"]["result"]
    review["candidate_json"], review["candidate_sha256"] = text, sha(text)
    control = {
        "schema_version": "decision-hub-frozen-review-input.v2",
        "candidate_sha256": sha(text),
        "expected_target_claim": review["expected_target_claim"],
        "frozen_candidate": candidate,
    }
    prompt = (
        "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n"
        + observation_json(control)
        + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
    )
    review["actual_prompt"], review["actual_prompt_sha256"] = prompt, sha(prompt)
    request, raw, selected = originals(
        request=request, run=replace(run, notifications=tuple(events))
    )
    value = _native_action_from_originals(
        selected=selected,
        raw=raw,
        task_created_at=NOW,
        request=request,
        decision_cutoff_at=request.pit_cutoff_at,
    )
    assert value.raw_candidate_hash == sha(value.raw_candidate_json)
    assert value.raw_candidate_hash != sha(observation_json(json.loads(value.synthesis_json)))


def test_complete_attestation_for_another_cutoff_is_rejected():
    request, raw, selected = originals(align_cutoff=False)
    with pytest.raises(ValueError, match="action_native_candidate_cutoff_mismatch"):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            task_created_at=NOW,
            request=request,
            decision_cutoff_at=request.pit_cutoff_at,
        )


def test_future_live_pit_deadline_is_not_used_as_producer_time():
    from tests.decision_product import test_native_action_synthesis as fixture_module

    request_factory = fixture_module._request
    # Change only the test fixture factory; all request hashes/session IDs and
    # native freeze/review binding are then produced by its real normal path.
    with patch.object(
        fixture_module,
        "_request",
        lambda: request_factory().model_copy(
            update={
                "pit_cutoff_at": NOW + timedelta(minutes=30),
            }
        ),
    ):
        request, raw, selected = originals()
    value = _native_action_from_originals(
        selected=selected,
        raw=raw,
        request=request,
        task_created_at=NOW - timedelta(seconds=1),
        decision_cutoff_at=NOW + timedelta(seconds=2),
    )
    assert value.producer_cutoff_at == NOW < request.pit_cutoff_at


@pytest.mark.parametrize("boundary", ["original_task", "finished_at", "final_cutoff"])
def test_declared_information_time_is_bounded_by_actual_original_times(boundary):
    request, raw, selected = originals()
    task_time, final_time = NOW, NOW
    if boundary == "original_task":
        task_time += timedelta(microseconds=1)
    elif boundary == "final_cutoff":
        final_time -= timedelta(microseconds=1)
    else:
        finish = NOW - timedelta(microseconds=1)
        raw = raw.model_copy(update={"reply": raw.reply.model_copy(update={"finished_at": finish})})
        selected = selected.model_copy(
            update={
                "result": selected.result.model_copy(update={"finished_at": finish}),
            }
        )
    with pytest.raises(ValueError, match="action_native_candidate_cutoff_mismatch"):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            request=request,
            task_created_at=task_time,
            decision_cutoff_at=final_time,
        )


@pytest.mark.parametrize(
    "change", ["action_outside_old_projection", "review_control", "gate_scope"]
)
def test_rehashed_old_witness_is_insufficient_without_complete_native_attestation(change):
    request, raw, selected = originals()
    events = json.loads(raw.reply.events_json)
    wrapper = events[-1]["payload"]["event"]["data"]["result"]
    if change == "action_outside_old_projection":
        wrapper["candidate"]["action_intents"][0]["reason"] = "unreviewed changed action"
    elif change == "review_control":
        review = events[3]["payload"]["event"]["data"]["result"]
        review["actual_prompt"] = "unrelated reviewer task"
        review["actual_prompt_sha256"] = sha(review["actual_prompt"])
    else:
        wrapper["review_gate"]["binding"]["generation"] = 2
    events_json = observation_json(events)
    reply = raw.reply.model_copy(
        update={
            "events_json": events_json,
            "result_hash": sha(raw.reply.final_response + "\0" + events_json),
        }
    )
    raw = raw.model_copy(update={"reply": reply, "events_json_hash": sha(events_json)})
    selected = selected.model_copy(
        update={
            "selection": selected.selection.model_copy(
                update={
                    "call_event_hash": observation_sha(events[selected.selection.call_event_index]),
                    "result_event_hash": observation_sha(
                        events[selected.selection.result_event_index]
                    ),
                }
            )
        }
    )
    with pytest.raises((AgentExecutionError, ValueError)):
        _native_action_from_originals(
            selected=selected,
            raw=raw,
            task_created_at=NOW,
            request=request,
            decision_cutoff_at=request.pit_cutoff_at,
        )
