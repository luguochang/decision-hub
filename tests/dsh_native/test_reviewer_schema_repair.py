"""Pure constructed native wrappers. No database, Provider or financial claim."""

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSynthesisCandidate
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
    extract_attested_review_receipts,
    extract_structured_review_receipts,
)
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons


def encoded(body):
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def wrapper():
    candidate = ResearchSynthesisCandidate.model_validate(
        {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": "request-controlled",
            "causal_case": _causal_case(
                thesis="仅工程控制", unresolved_questions=[], evidence_id="ev-controlled"
            ),
            "horizons": _horizons("ev-controlled"),
        }
    )
    candidate_json = encoded(candidate.model_dump(mode="json"))
    control = {
        "schema_version": "decision-hub-frozen-review-input.v1",
        "candidate_sha256": sha(candidate_json),
        "expected_target_claim": candidate.causal_case.thesis,
        "frozen_candidate": candidate.model_dump(mode="json"),
    }
    prompt = (
        "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n" + encoded(control) + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
    )
    receipt = {
        "schema_version": "independent-review-receipt.v2",
        "role": "independent_reviewer",
        "target_claim": candidate.causal_case.thesis,
        "evidence_refs": ["ev-controlled"],
        "challenge": "需要保留未知",
        "resolution": "bounded",
        "limitations": ["不是金融资格"],
        "reviewer_version": "controlled.v2",
        "issues": [
            {
                "issue_id": "control",
                "severity": "important",
                "blocking_scope": ["direction"],
                "evidence_refs": ["ev-controlled"],
                "status": "open",
                "disposition": "unresolved",
                "resolution_evidence_refs": [],
                "effect_on_direction": "unknown",
                "effect_on_tail_risk": "unknown",
                "effect_on_monitor": "unknown",
                "effect_on_report": "unknown",
                "reason": "没有足够证据",
            }
        ],
    }
    invalid = copy.deepcopy(receipt)
    invalid["issues"][0]["blocking_scope"].append("monitor")
    output = {
        "kind": "foreground",
        "runId": "child-controlled",
        "output": [{"type": "text", "text": encoded(receipt)}],
    }
    before = {**output, "output": [{"type": "text", "text": encoded(invalid)}]}
    feedback = (
        "issues.0.blocking_scope.1:invalid_enum_value:expected=direction|path_risk|execution|report"
    )
    events = [
        {"seq": 10, "type": "turn/start", "data": {"turn": 1}},
        {
            "seq": 11,
            "type": "user/message",
            "data": {
                "role": "user",
                "id": "controlled-feedback",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": feedback}],
            },
        },
        {
            "seq": 12,
            "type": "assistant/message",
            "data": {"message": {"content": output["output"]}},
        },
        {"seq": 13, "type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]
    before_json, events_json = encoded(before), encoded(events)
    return {
        "schema_version": "decision-hub-frozen-review-result.v2",
        "binding": {
            "runId": "run-controlled",
            "requestHash": "a" * 64,
            "sessionId": "root-controlled",
            "requestId": "request-controlled",
            "generation": 1,
        },
        "freeze_call_id": "freeze",
        "freeze_seq": 2,
        "candidate_json": candidate_json,
        "candidate_sha256": sha(candidate_json),
        "expected_target_claim": candidate.causal_case.thesis,
        "actual_prompt": prompt,
        "actual_prompt_sha256": sha(prompt),
        "child_session_id": "child-controlled",
        "original_output": output,
        "schema_repair": {
            "schema_version": "decision-hub-review-schema-repair.v1",
            "attempt": 1,
            "child_parent_session_id": "root-controlled",
            "invalid_output_json": before_json,
            "invalid_output_sha256": sha(before_json),
            "validation_errors": [feedback],
            "repair_prompt": feedback,
            "repair_prompt_sha256": sha(feedback),
            "event_start_seq": 10,
            "event_end_seq": 13,
            "events_json": events_json,
            "events_sha256": sha(events_json),
        },
    }


def read(body):
    def notification(kind, data):
        return DshSdkNotification(
            "session.event",
            {"sessionId": "root-controlled", "event": {"type": kind, "time": 0, "data": data}},
        )

    return extract_structured_review_receipts(
        [
            notification("tool/call", {"callId": "review", "name": "subagent_reviewer"}),
            notification(
                "tool/result", {"callId": "review", "name": "subagent_reviewer", "result": body}
            ),
        ],
        expected_session_id="root-controlled",
        allowed_evidence_ids=frozenset({"ev-controlled"}),
        fallback_created_at=datetime.now(UTC),
    )


def test_reads_exact_same_child_repair_and_retains_full_first_output():
    body = wrapper()
    rows = read(body)
    assert len(rows) == 1 and rows[0].structured_receipt.issues[0].effect_on_monitor == "unknown"
    assert (
        json.loads(rows[0].wrapper_json)["schema_repair"]["invalid_output_json"]
        == body["schema_repair"]["invalid_output_json"]
    )
    old = copy.deepcopy(body)
    old["schema_version"] = "decision-hub-frozen-review-result.v1"
    del old["schema_repair"]
    assert len(read(old)) == 1
    new_first_valid = copy.deepcopy(body)
    new_first_valid["schema_repair"] = None
    assert len(read(new_first_valid)) == 1


def test_rejected_wrong_version_output_never_promotes_through_legacy_reader():
    body = wrapper()
    final = json.loads(body["original_output"]["output"][0]["text"])
    legacy = {
        key: value
        for key, value in final.items()
        if key not in {"issues", "reviewer_version"}
    }
    legacy["schema_version"] = "independent-review-receipt.v1"
    invalid = {
        **body["original_output"],
        "output": [{"type": "text", "text": encoded(legacy)}],
    }
    body["schema_repair"]["invalid_output_json"] = encoded(invalid)
    body["schema_repair"]["invalid_output_sha256"] = sha(encoded(invalid))
    assert len(read(body)) == 1

    def notification(kind, data):
        return DshSdkNotification(
            "session.event",
            {"sessionId": "root-controlled", "event": {"type": kind, "time": 0, "data": data}},
        )

    def legacy_read(result):
        return extract_attested_review_receipts(
            [
                notification("tool/call", {"callId": "review", "name": "subagent_reviewer"}),
                notification(
                    "tool/result",
                    {"callId": "review", "name": "subagent_reviewer", "result": result},
                ),
            ],
            expected_session_id="root-controlled",
            allowed_evidence_ids=frozenset({"ev-controlled"}),
            fallback_created_at=datetime.now(UTC),
        )

    combined = legacy_read(body)
    assert len(combined) == 1 and combined[0].structured_receipt is not None
    assert combined[0].structured_receipt.schema_version == "independent-review-receipt.v2"
    # Existing unwrapped V1 actual returns keep their old compatibility.
    assert len(legacy_read(invalid)) == 1
    body["schema_repair"]["invalid_output_sha256"] = "f" * 64
    assert read(body) == [] and legacy_read(body) == []


@pytest.mark.parametrize(
    "change",
    ["raw_hash", "parent", "seq", "feedback", "already_valid", "illegal_final", "malformed_nested"],
)
def test_rejects_broken_repair_proofs(change):
    body = wrapper()
    repair = body["schema_repair"]
    if change == "raw_hash":
        repair["invalid_output_sha256"] = "f" * 64
    elif change == "parent":
        repair["child_parent_session_id"] = "another-root"
    elif change == "seq":
        repair["event_end_seq"] = 14
    elif change == "feedback":
        repair["repair_prompt"] = "different feedback"
        repair["repair_prompt_sha256"] = sha(repair["repair_prompt"])
    elif change == "already_valid":
        repair["invalid_output_json"] = encoded(body["original_output"])
        repair["invalid_output_sha256"] = sha(repair["invalid_output_json"])
    elif change == "malformed_nested":
        events = json.loads(repair["events_json"])
        events[1]["data"]["source"] = []
        repair["events_json"] = encoded(events)
        repair["events_sha256"] = sha(repair["events_json"])
    else:
        body["original_output"] = json.loads(repair["invalid_output_json"])
    assert read(body) == []
