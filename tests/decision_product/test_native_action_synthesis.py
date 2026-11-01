"""Native wire fixtures only: no model, production database or financial claim."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review_effects import _selected_gate
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
    extract_attested_decision_synthesis_captures,
    extract_structured_review_receipts,
)
from tests.decision_product.test_research_signal_contracts import synthesis
from tests.runtime.test_dsh_research_runtime import (
    NOW,
    _causal_payload,
    _horizon_payload,
    _request,
    _result_payload,
    _run,
    _synthesis_capture_notifications,
)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def wire_fixture(*, matched=True, request_override=None):
    request = _request().model_copy(update={"output_schema_ref": DECISION_OUTPUT_SCHEMA_REF})
    if request_override is not None:
        request = request.model_copy(update=request_override)
    digest = sha(f"dsh-host-bridge.v1\0{request.run_id}\0{research_request_hash(request)}")
    session_id = "dsh_" + digest
    binding = {
        "runId": request.run_id,
        "requestHash": research_request_hash(request),
        "sessionId": session_id,
        "requestId": "req_" + digest,
        "generation": 1,
    }
    candidate = synthesis()
    candidate["request_id"] = request.request_id
    candidate["research_candidate_v1"] = {
        **_result_payload(request_id=request.request_id),
        "causal_case": _causal_payload("evidence-transcript"),
        "horizons": [
            _horizon_payload("evidence-transcript", horizon=key) for key in ("30m", "24h", "72h")
        ],
    }
    candidate["action_intents"][0]["evidence_refs"] = ["evidence-transcript"]
    mechanism = candidate["mechanism_reviews"][0]
    mechanism["run_id"] = request.run_id
    mechanism["nodes"][0]["refs"] = ["evidence-transcript"]
    mechanism["edges"][0]["support_refs"] = ["evidence-transcript"]
    mechanism["edges"][0]["counter_refs"] = []
    mechanism["alternative_refs"] = []
    candidate = normalize_action_synthesis(candidate).model_dump(mode="json")
    text = canonical(candidate)
    thesis = candidate["research_candidate_v1"]["causal_case"]["thesis"]
    receipt = {
        "schema_version": "independent-review-receipt.v2",
        "role": "independent_reviewer",
        "target_claim": thesis,
        "evidence_refs": ["evidence-transcript"],
        "challenge": "完整对象工程验证",
        "resolution": "accepted",
        "limitations": ["未证明金融效果"],
        "reviewer_version": "fixture.v1",
        "issues": [],
    }
    control = {
        "schema_version": "decision-hub-frozen-review-input.v2",
        "candidate_sha256": sha(text),
        "expected_target_claim": thesis,
        "frozen_candidate": candidate,
    }
    prompt = (
        "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n"
        + canonical(control)
        + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
    )
    review = {
        "schema_version": "decision-hub-frozen-review-result.v3",
        "binding": binding,
        "freeze_call_id": "freeze",
        "freeze_seq": 1,
        "candidate_json": text,
        "candidate_sha256": sha(text),
        "expected_target_claim": thesis,
        "actual_prompt": prompt,
        "actual_prompt_sha256": sha(prompt),
        "child_session_id": "child",
        "schema_repair": None,
        "original_output": {
            "kind": "foreground",
            "runId": "child",
            "output": [{"type": "text", "text": canonical(receipt)}],
        },
    }
    gate = {
        "schema_version": "decision-hub-final-review-gate.v2",
        "status": "matched" if matched else "unreviewed",
        "reason": "exact_target" if matched else "review_attempt_used",
        "binding": binding,
        "candidate_json": text,
        "candidate_sha256": sha(text),
        "review_receipt_hash": sha(canonical(receipt)) if matched else None,
        "review_call_id": "review" if matched else None,
    }
    wrapper = {
        "schema_version": "decision-hub-synthesis-tool-result.v2",
        "candidate": candidate,
        "review_gate": gate,
    }

    def event(seq, kind, data):
        return DshSdkNotification(
            "session.event",
            {
                "sessionId": session_id,
                "event": {"seq": seq, "type": kind, "timestamp": NOW.isoformat(), "data": data},
            },
        )

    events = [
        event(
            1,
            "tool/call",
            {
                "callId": "freeze",
                "name": "decision_hub_synthesis_submit",
                "arguments": {"candidate": candidate},
            },
        ),
        event(
            2,
            "tool/result",
            {"callId": "freeze", "isError": True, "error": "decision_hub_final_review_required"},
        ),
        event(3, "tool/call", {"callId": "review", "name": "subagent_reviewer", "arguments": {}}),
        event(4, "tool/result", {"callId": "review", "result": review}),
        event(
            5,
            "tool/call",
            {
                "callId": "final",
                "name": "decision_hub_synthesis_submit",
                "arguments": {"candidate": candidate},
            },
        ),
        event(6, "tool/result", {"callId": "final", "result": wrapper}),
    ]
    return request, _run(session_id=session_id, notifications=tuple(events)), candidate


def mapped(request, run):
    return map_session_result_with_witness(
        run,
        request,
        [],
        runtime_version="fixture",
        profile_ref="decision-research.web.v1",
        started_at=NOW - timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=1),
        accepted_synthesis_only=True,
    )


def test_full_wrapper_and_legacy_projection_have_distinct_exact_witnesses():
    request, run, candidate = wire_fixture()
    value = mapped(request, run)
    action = value.action_synthesis
    assert action is not None
    assert action.synthesis.model_dump(mode="json") == candidate
    assert value.selection.payload_path == (*action.selection.payload_path, "research_candidate_v1")
    assert value.selection.candidate_payload_hash == action.projected_research_candidate_hash
    assert action.raw_candidate_hash == sha(canonical(candidate))
    assert action.raw_candidate_hash != action.projected_research_candidate_hash
    assert action.selection.call_id == value.selection.call_id == "final"
    assert value.result.horizons == action.synthesis.research_candidate_v1.horizons
    assert len(value.result.reviews) == 1


@pytest.mark.parametrize(
    "change",
    [
        "action_call",
        "action_gate",
        "control",
        "old_wrapper",
        "nested_quote",
        "request",
        "old_mode",
        "duplicate_call",
        "duplicate_result",
    ],
)
def test_new_protocol_rejects_mixed_or_unreviewed_action_originals(change):
    request, run, _ = wire_fixture()
    events = deepcopy(run.notifications)
    output = events[-1].payload["event"]["data"]["result"]
    if change == "action_call":
        events[-2].payload["event"]["data"]["arguments"]["candidate"] = deepcopy(
            output["candidate"]
        )
        events[-2].payload["event"]["data"]["arguments"]["candidate"]["action_intents"][0][
            "reason"
        ] = "changed"
    elif change == "action_gate":
        modified = deepcopy(output["candidate"])
        modified["action_intents"][0]["reason"] = "changed"
        output["review_gate"]["candidate_json"] = canonical(modified)
        output["review_gate"]["candidate_sha256"] = sha(canonical(modified))
    elif change == "control":
        review = events[3].payload["event"]["data"]["result"]
        review["actual_prompt"] = review["actual_prompt"].replace(
            '"frozen-review-input.v2"', '"wrong"'
        )
        # Change the full frozen body while retaining the unchanged gate candidate.
        modified = deepcopy(output["candidate"])
        modified["action_intents"][0]["reason"] = "changed"
        review["candidate_json"] = canonical(modified)
        review["candidate_sha256"] = sha(canonical(modified))
    elif change == "old_wrapper":
        output["schema_version"] = "decision-hub-synthesis-tool-result.v1"
    elif change == "nested_quote":
        events[-1].payload["event"]["data"]["result"] = {"quote": output}
    elif change == "request":
        request = request.model_copy(update={"request_id": "wrong"})
    elif change == "old_mode":
        request = request.model_copy(update={"output_schema_ref": "research-session-result.v1"})
    elif change == "duplicate_call":
        events = (*events, events[-2])
    elif change == "duplicate_result":
        events = (*events, events[-1])
    changed = _run(session_id=run.session_id, notifications=tuple(events))
    with pytest.raises((AgentExecutionError, ValueError)):
        mapped(request, changed)


def test_new_mode_rejects_bare_old_and_final_prose_candidates():
    request, run, candidate = wire_fixture()
    for events in (
        (),
        _synthesis_capture_notifications(
            candidate["research_candidate_v1"], session_id=run.session_id
        ),
    ):
        old = _run(
            session_id=run.session_id, notifications=events, final_response=canonical(candidate)
        )
        with pytest.raises(AgentExecutionError):
            mapped(request, old)


def test_review_v3_requires_full_wrapper_and_control_v2():
    _request_value, run, _ = wire_fixture()
    refs = dict(
        expected_session_id=run.session_id,
        allowed_evidence_ids=frozenset({"evidence-transcript"}),
        fallback_created_at=NOW,
    )
    assert len(extract_structured_review_receipts(run.notifications, **refs)) == 1
    events = deepcopy(run.notifications)
    events[3].payload["event"]["data"]["result"]["schema_version"] = (
        "decision-hub-frozen-review-result.v2"
    )
    assert extract_structured_review_receipts(events, **refs) == []


def test_valid_review_of_different_actions_cannot_approve_same_thesis():
    request, run, candidate = wire_fixture()
    events = deepcopy(run.notifications)
    review = events[3].payload["event"]["data"]["result"]
    changed = deepcopy(candidate)
    changed["action_intents"][0]["reason"] = "Other action with identical research thesis"
    text = canonical(changed)
    review["candidate_json"], review["candidate_sha256"] = text, sha(text)
    control = {
        "schema_version": "decision-hub-frozen-review-input.v2",
        "candidate_sha256": sha(text),
        "expected_target_claim": changed["research_candidate_v1"]["causal_case"]["thesis"],
        "frozen_candidate": changed,
    }
    prompt = (
        "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n"
        + canonical(control)
        + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
    )
    review["actual_prompt"], review["actual_prompt_sha256"] = prompt, sha(prompt)
    receipts = extract_structured_review_receipts(
        events,
        expected_session_id=run.session_id,
        allowed_evidence_ids=frozenset({"evidence-transcript"}),
        fallback_created_at=NOW,
    )
    assert len(receipts) == 1  # Self-consistent review, wrong complete candidate.
    with pytest.raises(AgentExecutionError) as caught:
        mapped(request, _run(session_id=run.session_id, notifications=events))
    assert caught.value.cause_code == "decision_synthesis_review_missing"


def test_normalizer_changes_only_existing_nested_representation():
    _request_value, _run_value, candidate = wire_fixture()
    before = deepcopy(candidate)
    candidate["research_candidate_v1"]["horizons"][0]["evidence_refs"] *= 2
    value = normalize_action_synthesis(candidate)
    assert value.model_dump(mode="json") == before
    assert len(candidate["research_candidate_v1"]["horizons"][0]["evidence_refs"]) == 2


def test_raw_candidate_hash_uses_actual_payload_without_dto_number_rewriting():
    _request_value, run, candidate = wire_fixture()
    raw = deepcopy(candidate)
    raw["action_intents"][0]["probability"].update(kind="subjective", value=1)
    events = deepcopy(run.notifications)
    events[-2].payload["event"]["data"]["arguments"]["candidate"] = raw
    events[-1].payload["event"]["data"]["result"]["candidate"] = raw
    capture = extract_attested_decision_synthesis_captures(
        events, expected_session_id=run.session_id
    )[0]
    assert capture.raw_candidate_hash == sha(canonical(raw))
    assert capture.synthesis.action_intents[0].probability.value == 1.0
    assert capture.raw_candidate_hash != sha(canonical(capture.synthesis.model_dump(mode="json")))


def test_new_action_evidence_is_not_promoted_by_old_research_evidence():
    request, run, candidate = wire_fixture()
    changed = deepcopy(candidate)
    changed["action_intents"][0]["evidence_refs"] = ["unattested-action-only"]
    events = deepcopy(run.notifications)
    events[-2].payload["event"]["data"]["arguments"]["candidate"] = changed
    output = events[-1].payload["event"]["data"]["result"]
    output["candidate"] = changed
    output["review_gate"]["candidate_json"] = canonical(changed)
    output["review_gate"]["candidate_sha256"] = sha(canonical(changed))
    with pytest.raises(AgentExecutionError) as caught:
        mapped(request, _run(session_id=run.session_id, notifications=events))
    assert caught.value.error_code == "dsh_evidence_unattested"


def test_legacy_projection_and_result_serialization_are_unchanged():
    request = _request()
    run = _run(notifications=_synthesis_capture_notifications(_result_payload()))
    first = mapped(request, run)
    assert first.action_synthesis is None
    # Recorded before this slice from the unchanged baseline mapper.
    assert first.selection.candidate_payload_hash == sha(canonical(_result_payload()))
    assert first.selection.payload_path[-1] == "structuredContent"
    assert first.result.schema_version == "research-session-result.v1"


def test_selected_kernel_gate_binds_entire_wrapper_and_version():
    request, run, _candidate = wire_fixture()
    value = mapped(request, run)
    context = SimpleNamespace(
        run_id=request.run_id,
        first_linked_request_hash=research_request_hash(request),
        dsh_session_id=run.session_id,
        generation=1,
    )
    events = [
        {"method": item.method, "payload": deepcopy(item.payload)} for item in run.notifications
    ]
    assert _selected_gate(events, value.selection, context, decision=True)["status"] == "matched"
    with pytest.raises(ValueError, match="protocol_mismatch"):
        _selected_gate(events, value.selection, context)
    gate = events[-1]["payload"]["event"]["data"]["result"]["review_gate"]
    changed = json.loads(gate["candidate_json"])
    changed["action_intents"][0]["reason"] = "action-only edit after review"
    gate["candidate_json"] = canonical(changed)
    gate["candidate_sha256"] = sha(canonical(changed))
    with pytest.raises(ValueError, match="candidate_invalid"):
        _selected_gate(events, value.selection, context, decision=True)


@pytest.mark.parametrize("typed", [True, False])
def test_actual_typed_or_rendered_return_retains_exact_path(typed):
    _request_value, run, _candidate = wire_fixture()
    events = deepcopy(run.notifications)
    data = events[-1].payload["event"]["data"]
    result = data.pop("result")
    data.clear()
    block = {
        "type": "tool-result",
        "toolCallId": "final",
        "content": [{"type": "text", "text": canonical(result)}],
    }
    if typed:
        block["structuredContent"] = result
    data["message"] = {"content": [block]}
    capture = extract_attested_decision_synthesis_captures(
        events, expected_session_id=run.session_id
    )[0]
    assert capture.payload_path[-2:] == (
        ("structuredContent", "candidate") if typed else (None, "candidate")
    )
    if typed:
        block["content"][0]["text"] = canonical({**result, "schema_version": "wrong"})
        with pytest.raises(AgentExecutionError):
            extract_attested_decision_synthesis_captures(events, expected_session_id=run.session_id)


def test_official_pruning_preserves_original_synthesis_and_rejects_fake_replacement():
    _request_value, run, _candidate = wire_fixture()
    events = list(deepcopy(run.notifications))
    event = events[-1].payload["event"]
    result = event["data"]["result"]
    event.update(surfaceOp="append", sourceEventSeqs=[5])
    event["data"] = {
        "turn": 1,
        "step": 2,
        "message": {
            "id": "message-final",
            "source": {"kind": "tool", "callId": "final"},
            "content": [
                {
                    "type": "tool-result",
                    "callId": "final",
                    "content": [{"type": "text", "text": canonical(result)}],
                }
            ],
        },
    }
    replacement = deepcopy(events[-1])
    replacement.payload["event"].update(
        seq=8, surfaceOp={"op": "replace", "start": 6, "end": 6}, sourceEventSeqs=[6]
    )
    replacement.payload["event"]["data"]["message"]["content"][0]["content"][0]["text"] = "pruned"
    prune = DshSdkNotification(
        "session.event",
        {
            "sessionId": run.session_id,
            "event": {
                "seq": 7,
                "type": "compaction/prune",
                "data": {
                    "shadowedRange": {"start": 6, "end": 6},
                    "shadowedSeqs": [6],
                },
            },
        },
    )
    events.extend([prune, replacement])
    capture = extract_attested_decision_synthesis_captures(
        events, expected_session_id=run.session_id
    )[0]
    assert capture.projection.result_event_index == 5
    replacement.payload["event"]["sourceEventSeqs"] = [999]
    with pytest.raises(AgentExecutionError):
        extract_attested_decision_synthesis_captures(events, expected_session_id=run.session_id)


@pytest.mark.parametrize("mode", ["tool", "response"])
def test_legacy_serialized_results_and_hashes_match_before_source(mode):
    # Checked-in synthetic golden captured from the retained before modules.
    # Reproduction needs no ignored artifact directory or production originals.
    golden = json.loads(
        (Path(__file__).parent / "fixtures/native_action_legacy_v1.json").read_text(
            encoding="utf-8"
        )
    )["cases"][mode]
    payload = _result_payload()
    payload["causal_case"] = _causal_payload("evidence-transcript")
    payload["horizons"] = [_horizon_payload("evidence-transcript")]
    run = _run(
        notifications=_synthesis_capture_notifications(payload) if mode == "tool" else (),
        final_response=canonical(payload),
    )
    request = _request()
    after = mapped(request, run)
    assert after.result.model_dump_json() == golden["result_json"]
    assert after.selection.model_dump_json() == golden["selection_json"]
    assert sha(after.result.model_dump_json()) == golden["result_sha256"]
