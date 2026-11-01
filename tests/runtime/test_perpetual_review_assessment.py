"""Synthetic content protocol facts; no HTTP/model/DB or qualification claim."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from typing import Any

import pytest
from candidate_perpetual_review_support import CANDIDATE, MAIN, load_review_candidates
from jsonschema import Draft202012Validator

candidate = load_review_candidates()
CHECKS = (
    "frozen_information",
    "probability_completeness",
    "evidence_balance",
    "cost_protection_and_conditions",
)
REF = {"ref": "synthetic-original", "hash": "1" * 64}
PACKET_REF = {"ref": "synthetic-packet", "hash": "2" * 64}


def value() -> dict[str, Any]:
    return {
        "schema_version": "perpetual-review-findings.v2",
        "review_request_ref": PACKET_REF,
        "summary": "Synthetic content review; not a real research acceptance.",
        "issues": [],
        "report_assessment": {
            key: {
                "status": "passed",
                "reason": "Synthetic original checked.",
                "evidence_refs": [REF],
            }
            for key in CHECKS
        },
    }


def read_findings(
    findings: dict[str, Any], *, expected: str = "perpetual-review-findings.v2",
    verdict: str = "approved",
) -> Any:
    from types import SimpleNamespace

    class Reference:
        def model_dump(self, **_kwargs: Any) -> dict[str, str]:
            return PACKET_REF

    assert "expected_schema_version" in inspect.signature(candidate._findings).parameters
    return candidate._findings(
        {
            "schema_version": "action-review-request.v2",
            "synthesis": {
                "candidate_set": {"candidates": []},
            },
        },
        SimpleNamespace(review_request_ref=Reference(), verdict=verdict),
        [
            {
                "type": "text",
                "text": json.dumps(
                    [
                        {"schema_version": "action-review-authorization.v2"},
                        findings,
                    ]
                ),
            }
        ],
        {"originals": [{"source_ref": REF, "original_ref": None}]},
        required=True,
        expected_schema_version=expected,
    )


def test_canonical_v2_is_explicit_and_validates_all_four_named_checks() -> None:
    schema = json.loads(
        (CANDIDATE / "contracts/schemas/decision_product_v2.schema.yaml").read_bytes()
    )
    assert "perpetual_review_findings_v2" in schema["$defs"]
    validator = Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/perpetual_review_findings_v2"}
    )
    validator.validate(value())
    for key in CHECKS:
        broken = value()
        del broken["report_assessment"][key]
        assert list(validator.iter_errors(broken))


def test_approved_incomplete_remains_incomplete_without_changing_verdict() -> None:
    findings = value()
    findings["report_assessment"]["cost_protection_and_conditions"] = {
        "status": "incomplete",
        "reason": "Synthetic missing protective original.",
        "evidence_refs": [],
    }
    before = deepcopy(findings)
    assert read_findings(findings) == before
    assert findings == before


def test_legacy_v1_does_not_gain_default_assessments() -> None:
    old = value()
    old["schema_version"] = "perpetual-review-findings.v1"
    del old["report_assessment"]
    assert read_findings(old, expected="perpetual-review-findings.v1") == old


def test_new_prompt_cannot_be_downgraded_to_legacy_v1() -> None:
    old = value()
    old["schema_version"] = "perpetual-review-findings.v1"
    del old["report_assessment"]
    with pytest.raises(ValueError):
        read_findings(old)


@pytest.mark.parametrize(
    "attack",
    [
        "missing_check",
        "empty_reason",
        "bad_status",
        "foreign_ref",
        "passed_without_ref",
        "pc_claim",
    ],
)
def test_malformed_or_unbound_content_cannot_claim_pass(attack: str) -> None:
    findings = value()
    check = findings["report_assessment"]["frozen_information"]
    if attack == "missing_check":
        del findings["report_assessment"]["frozen_information"]
    if attack == "empty_reason":
        check["reason"] = " "
    if attack == "bad_status":
        check["status"] = "approved"
    if attack == "foreign_ref":
        check["evidence_refs"] = [{"ref": "foreign", "hash": "0" * 64}]
    if attack == "passed_without_ref":
        check["evidence_refs"] = []
    if attack == "pc_claim":
        findings["report_assessment"]["restart_readable"] = deepcopy(check)
    with pytest.raises(ValueError):
        read_findings(findings)


def replace_prompt(item: Any, before: str, after: str) -> None:
    if isinstance(item, dict):
        for key, current in item.items():
            if current == before:
                item[key] = after
            else:
                replace_prompt(current, before, after)
    elif isinstance(item, list):
        for current in item:
            replace_prompt(current, before, after)


def full_proof(
    *, downgrade: bool = False, tamper: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Adapt only synthetic protocol metadata in the complete original fixture."""
    import hashlib

    raw = json.loads((MAIN / "artifacts/d2-native-v2/native-transcript.json").read_bytes())
    events = raw["context"]["events"]
    wrapper = events[4]["data"]["value"]
    old_prompt = wrapper["actual_prompt"]
    catalog = candidate._json(
        old_prompt.split("BEGIN_AUTHENTICATED_REVIEW_ORIGINALS_JSON\n")[1].split("\n")[0]
    )
    authorization = candidate._json(wrapper["original_output"]["output"][0]["text"])
    if isinstance(authorization, list):
        authorization = authorization[0]
    findings = value()
    findings["review_request_ref"] = authorization["review_request_ref"]
    for check in findings["report_assessment"].values():
        check["evidence_refs"] = [catalog["originals"][0]["source_ref"]]
    findings["report_assessment"]["cost_protection_and_conditions"] = {
        "status": "incomplete",
        "reason": "Synthetic missing protection, despite action approval.",
        "evidence_refs": [],
    }
    fixed = {"schema_version": "perpetual-review-findings.v2"}
    lines = old_prompt.split("\n")
    if "FINDINGS_FIXED_FIELDS_JSON" in lines:
        lines[lines.index("FINDINGS_FIXED_FIELDS_JSON") + 1] = json.dumps(fixed)
    else:
        lines.extend(["FINDINGS_FIXED_FIELDS_JSON", json.dumps(fixed)])
    prompt = "\n".join(lines)
    if downgrade:
        findings["schema_version"] = "perpetual-review-findings.v1"
        del findings["report_assessment"]
    output = [{"type": "text", "text": json.dumps([authorization, findings])}]
    child = json.loads(wrapper["child_events_json"])

    replace_prompt(child, old_prompt, prompt)
    answers = [event for event in child if event["type"] == "assistant/message"]
    answers[-1]["data"]["message"]["content"] = deepcopy(output)
    wrapper["actual_prompt"] = prompt
    wrapper["actual_prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    wrapper["child_events_json"] = json.dumps(child)
    wrapper["child_events_sha256"] = hashlib.sha256(
        wrapper["child_events_json"].encode()
    ).hexdigest()
    wrapper["original_output"]["output"] = output
    if tamper:
        changed = json.loads(output[0]["text"])
        changed[1]["report_assessment"]["cost_protection_and_conditions"]["status"] = "passed"
        output[0]["text"] = json.dumps(changed)
    return raw, wrapper, findings


def read_complete_proof(raw: dict[str, Any], wrapper: dict[str, Any]) -> Any:
    from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification

    context = raw["context"]
    notes = [
        DshSdkNotification(
            "session.event",
            {
                "sessionId": context["binding"]["sessionId"],
                "event": event,
            },
        )
        for event in context["events"]
    ]
    return candidate._review(
        notes,
        candidate._pairs(notes, context["binding"]["sessionId"]),
        "review",
        "freeze",
        json.loads(wrapper["candidate_json"]),
        json.loads(wrapper["review_request_json"]),
        context["binding"],
    )


def test_actual_child_proof_preserves_approved_incomplete_v2_findings() -> None:
    raw, wrapper, findings = full_proof()
    authorization, restored, *_ = read_complete_proof(raw, wrapper)
    assert authorization.verdict == "approved"
    assert restored == wrapper
    assert json.loads(restored["original_output"]["output"][0]["text"])[1] == findings


def test_actual_child_new_prompt_rejects_v1_downgrade_even_with_matching_transcript() -> None:
    raw, wrapper, _ = full_proof(downgrade=True)
    with pytest.raises(ValueError, match="review_exact_findings_required"):
        read_complete_proof(raw, wrapper)


def test_findings_cannot_be_rewritten_without_the_independent_child_output() -> None:
    raw, wrapper, _ = full_proof(tamper=True)
    with pytest.raises(ValueError, match="review_child_transcript"):
        read_complete_proof(raw, wrapper)
