"""Complete synthetic native V4 proof; no provider call or financial permission."""

import hashlib
import json
from copy import deepcopy

import pytest
from test_d2_native_v2 import fixture

from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import _pairs, _review
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import _actual_return_payloads


def complete_proof(*, text_wire=False, negative=False, attack=None):
    raw, _request, notifications = fixture()
    values = [deepcopy(dict(item.payload)) for item in notifications]
    result = values[4]["event"]["data"]
    wrapper = result["value"]
    child = json.loads(wrapper["child_events_json"])
    if negative:
        output = wrapper["original_output"]["output"]
        packet = json.loads(output[0]["text"])
        # The legacy controlled transcript predates findings arrays. Build only
        # this synthetic negative case's complete current output explicitly.
        if isinstance(packet, dict):
            packet = [packet, {
                "schema_version": "perpetual-review-findings.v1",
                "review_request_ref": packet["review_request_ref"],
                "summary": "Synthetic negative review, no financial qualification.",
                "issues": [],
            }]
        assert isinstance(packet, list) and len(packet) == 2
        packet[0]["verdict"] = "needs_revision"
        packet[1]["issues"] = [{
            "issue_id": "synthetic_finding_1", "candidate_ids": [],
            "field_paths": ["/analysis/thesis"], "concern": "Synthetic unsupported claim.",
            "requested_change": "Retain explicit uncertainty.", "evidence_refs": [],
        }]
        output[0]["text"] = json.dumps(packet, ensure_ascii=False)
        child[-2]["data"]["message"]["content"] = deepcopy(output)
    child.insert(1, {"type": "assistant/chunk", "time": 0, "data": {
        "turn": 0, "step": 0,
        "chunk": {"type": "text-delta", "index": 0, "text": "x" * 1_050_000},
    }})
    if attack == "extra_user":
        child.insert(1, {"type": "user/message", "data": {
            "content": [{"type": "text", "text": "Unbound extra product input."}],
        }})
    for seq, event in enumerate(child, 1):
        event["seq"] = seq
    wrapper["child_events_json"] = json.dumps(child, ensure_ascii=False)
    wrapper["child_events_sha256"] = hashlib.sha256(wrapper["child_events_json"].encode()).hexdigest()
    if attack == "digest":
        wrapper["child_events_sha256"] = "0" * 64
    expected = deepcopy(wrapper)
    if text_wire:
        result.pop("value")
        result["content"] = [{"type": "text", "text": json.dumps(wrapper, ensure_ascii=False)}]
    notes = [DshSdkNotification("session.event", value) for value in values]
    return raw, notes, expected


def read_review(raw, notifications, wrapper):
    binding = raw["context"]["binding"]
    return _review(
        notifications, _pairs(notifications, binding["sessionId"]), "review", "freeze",
        json.loads(wrapper["candidate_json"]), json.loads(wrapper["review_request_json"]), binding,
    )


@pytest.mark.parametrize("text_wire", [False, True])
def test_complete_v4_review_proof_survives_without_arbitrary_embedded_size_cap(text_wire):
    raw, notes, wrapper = complete_proof(text_wire=text_wire)
    assert len(wrapper["child_events_json"].encode()) > 1_000_000
    authorization, restored, *_ = read_review(raw, notes, wrapper)
    assert authorization.verdict == "approved"
    assert restored == wrapper
    assert restored["child_events_sha256"] == hashlib.sha256(restored["child_events_json"].encode()).hexdigest()


def test_complete_negative_review_stays_negative_and_preserves_all_proof():
    raw, notes, wrapper = complete_proof(text_wire=True, negative=True)
    authorization, restored, *_ = read_review(raw, notes, wrapper)
    assert authorization.verdict == "needs_revision"
    assert restored == wrapper


@pytest.mark.parametrize("attack,reason", [("digest", "review_digest"), ("extra_user", "review_child_transcript")])
def test_large_proof_still_rejects_changed_digest_and_unbound_user(attack, reason):
    raw, notes, wrapper = complete_proof(text_wire=True, attack=attack)
    with pytest.raises(ValueError, match=reason):
        read_review(raw, notes, wrapper)


def test_generic_and_legacy_tool_text_keep_the_existing_one_megabyte_boundary():
    _raw, _notes, wrapper = complete_proof(text_wire=True)
    data = {"callId": "review", "content": [{"type": "text", "text": json.dumps(wrapper)}]}
    assert list(_actual_return_payloads(data, "review")) == []
    legacy = deepcopy(wrapper)
    legacy["schema_version"] = "decision-hub-frozen-review-result.v3"
    data["content"][0]["text"] = json.dumps(legacy)
    assert list(_actual_return_payloads(data, "review")) == []
    assert list(_actual_return_payloads(data, "review", allow_full_perpetual_review=True)) == []
