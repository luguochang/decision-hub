"""The owned Host field is the sole route for exact reviewer baseline authority."""
import hashlib
import json
from copy import deepcopy

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run
from tests.decision_product.reviewer_instruction_binding_support import (
    RESERVED,
    change_child,
    result_payload,
    synthetic_case,
)


def captures(payload, projection):
    result = result_payload(payload, projection)
    _, _, request = synthetic_case()
    return extract_attested_perpetual_synthesis_captures(
        _sdk_run(result).notifications, expected_session_id=result.dsh_session_id, request=request,
    )


def test_source_bound_baseline_survives_both_mapper_paths_and_original_capture_indices():
    from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness

    payload, projection, request = synthetic_case()
    result = result_payload(payload, projection)
    run = _sdk_run(result)
    original = json.loads(payload["events_json"])
    assert [(n.method, dict(n.payload)) for n in run.notifications[:-1]] == [
        (n["method"], n["payload"]) for n in original
    ]
    assert run.notifications[-1].method == RESERVED
    assert "event" not in run.notifications[-1].payload
    traces = map_notifications(
        run.notifications, run_id=request.run_id,
        research_session_id=run.session_id, request=request,
    )
    mapped = map_session_result_with_witness(
        run, request, traces, runtime_version="synthetic-baseline",
        profile_ref="decision-research.web.v2", started_at=result.started_at,
        finished_at=result.finished_at, accepted_synthesis_only=True,
    )
    assert mapped.perpetual_synthesis.authorization.verdict == "approved"
    selection = mapped.perpetual_synthesis.selection
    assert selection.call_event_index == 5 and selection.result_event_index == 6


def test_child_baseline_without_owned_host_field_still_fails_closed():
    payload, _, _ = synthetic_case()
    with pytest.raises(AgentExecutionError):
        captures(payload, None)


@pytest.mark.parametrize("where", ["model", "tool", "native"])
def test_identical_model_tool_or_native_json_cannot_promote_baseline_authority(where):
    payload, projection, _ = synthetic_case()
    notes = json.loads(payload["events_json"])
    if where == "model":
        notes.append({"method": "model.output", "payload": projection})
    elif where == "tool":
        notes.append({"method": "tool.output", "payload": {"value": projection}})
    else:
        notes[0]["payload"]["event"]["data"]["reviewer_instruction_bindings"] = projection
    payload["events_json"] = json.dumps(notes)
    assert all(n.method != RESERVED for n in _sdk_run(result_payload(payload)).notifications)
    with pytest.raises(AgentExecutionError):
        captures(payload, None)


def test_reserved_notification_in_raw_host_events_is_rejected():
    payload, projection, _ = synthetic_case()
    notes = json.loads(payload["events_json"])
    notes.append({"method": RESERVED, "payload": projection})
    payload["events_json"] = json.dumps(notes)
    with pytest.raises(AgentExecutionError, match="reserved"):
        _sdk_run(result_payload(payload))


@pytest.mark.parametrize("field,value", [
    ("run_id", "wrong-run"), ("dsh_session_id", "wrong-session"), ("generation", 2),
    ("request_id", "wrong-request"), ("request_hash", "c" * 64),
])
def test_projection_cannot_cross_native_request_scope(field, value):
    payload, projection, _ = synthetic_case()
    projection[field] = value
    with pytest.raises(AgentExecutionError):
        captures(payload, projection)


@pytest.mark.parametrize("attack", [
    "callid", "callseq", "future", "cutoff", "duplicate", "content", "source",
    "origin_generation", "origin_request",
])
def test_exact_call_cutoff_and_single_source_binding_are_required(attack):
    payload, projection, _ = synthetic_case()
    row = projection["bindings"][0]
    if attack == "callid":
        row["reviewer_call_id"] = "unrelated-review"
    elif attack == "callseq":
        row["reviewer_call_seq"] = 6
        row["parent_prefix_last_seq"] = 5
    elif attack == "future":
        row["baseline_event"]["seq"] = 5
    elif attack == "cutoff":
        row["parent_prefix_last_seq"] = 4
    elif attack == "duplicate":
        projection["bindings"].append(deepcopy(row))
    elif attack == "content":
        row["baseline_event"]["data"]["content"][0]["text"] += " changed"
    elif attack == "origin_generation":
        row["reviewer_generation"] = 2
    elif attack == "origin_request":
        row["reviewer_request_id"] = "req_wrong"
    else:
        row["baseline_event"]["data"]["source"]["baselineIdentity"] += " changed"
    with pytest.raises(AgentExecutionError):
        captures(payload, projection)


@pytest.mark.parametrize("attack", [
    "duplicate", "changed_content", "shape", "late", "product", "before_product",
])
def test_child_baseline_count_shape_content_and_order_remain_exact(attack):
    payload, projection, _ = synthetic_case()

    def mutate(child):
        if attack == "duplicate":
            child.insert(2, deepcopy(child[1]))
        elif attack == "changed_content":
            child[1]["data"]["content"][0]["text"] += " forged"
        elif attack == "shape":
            child[1]["data"]["unbound"] = True
        elif attack == "late":
            child.insert(-1, child.pop(1))
        elif attack == "before_product":
            child.insert(0, child.pop(1))
        else:
            child.insert(1, deepcopy(child[0]))

    change_child(payload, mutate)
    with pytest.raises(AgentExecutionError):
        captures(payload, projection)


def test_old_transcript_without_baseline_keeps_existing_behavior():
    payload, _, _ = synthetic_case(baseline=False)
    assert captures(payload, None)[0].authorization.verdict == "approved"


def test_old_result_full_model_roundtrip_does_not_turn_default_none_into_authority():
    from packages.contracts_py.decision_hub_contracts import DshSessionResult

    payload, _, _ = synthetic_case(baseline=False)
    old = result_payload(payload)
    reread = DshSessionResult.model_validate_json(old.model_dump_json())
    assert all(n.method != RESERVED for n in _sdk_run(reread).notifications)


def test_historical_review_uses_its_own_native_rpc_generation_under_current_transport():
    payload, projection, _ = synthetic_case()
    projection["generation"] = payload["generation"] = 2
    projection["request_id"] = "req_" + hashlib.sha256(
        f"dsh-host-turn.v1\0{payload['run_id']}\0{projection['request_hash']}\0{2}".encode()
    ).hexdigest()
    # Only the transport envelope advances; this retained call remains generation 1.
    assert captures(payload, projection)[0].authorization.verdict == "approved"
    row = projection["bindings"][0]
    row["reviewer_generation"] = 2
    row["reviewer_request_id"] = projection["request_id"]
    with pytest.raises(AgentExecutionError):
        captures(payload, projection)


def test_duplicate_converted_metadata_cannot_become_a_second_authority():
    payload, projection, request = synthetic_case()
    result = result_payload(payload, projection)
    original = _sdk_run(result).notifications
    with pytest.raises(AgentExecutionError):
        extract_attested_perpetual_synthesis_captures(
            (*original, original[-1]), expected_session_id=result.dsh_session_id, request=request,
        )


def test_retained_historical_call_without_old_rpc_marker_uses_host_origin_before_current_rpc():
    payload, projection, _ = synthetic_case()
    projection["generation"] = payload["generation"] = 2
    projection["request_id"] = "req_" + hashlib.sha256(
        f"dsh-host-turn.v1\0{payload['run_id']}\0{projection['request_hash']}\0{2}".encode()
    ).hexdigest()
    notes = json.loads(payload["events_json"])[1:]
    notes.append({"method": "session.event", "payload": {
        "sessionId": payload["dsh_session_id"], "event": {"seq": 8, "type": "user/message",
        "data": {"source": {"kind": "user", "rpcId": projection["request_id"]},
                 "content": [{"type": "text", "text": "Current generation product input."}]}}
    }})
    payload["events_json"] = json.dumps(notes)
    run = _sdk_run(result_payload(payload, projection))
    row = run.notifications[-1].payload["bindings"][0]
    assert row["reviewer_generation"] == 1 and row["reviewer_call_seq"] == 4
    old_rpc = projection["bindings"][0]["reviewer_request_id"]
    # A current-generation claim cannot borrow this historical missing-marker exception.
    projection["bindings"][0]["reviewer_generation"] = 2
    projection["bindings"][0]["reviewer_request_id"] = projection["request_id"]
    with pytest.raises(AgentExecutionError):
        _sdk_run(result_payload(payload, projection))
    projection["bindings"][0]["reviewer_generation"] = 1
    projection["bindings"][0]["reviewer_request_id"] = old_rpc
    notes[-1]["payload"]["event"]["seq"] = 3
    payload["events_json"] = json.dumps(notes)
    with pytest.raises(AgentExecutionError):
        _sdk_run(result_payload(payload, projection))
    payload["events_json"] = json.dumps(notes[:-1])
    with pytest.raises(AgentExecutionError):
        _sdk_run(result_payload(payload, projection))


def test_current_call_without_its_visible_rpc_marker_still_fails_closed():
    payload, projection, _ = synthetic_case()
    payload["events_json"] = json.dumps(json.loads(payload["events_json"])[1:])
    with pytest.raises(AgentExecutionError):
        _sdk_run(result_payload(payload, projection))
