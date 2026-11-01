"""New native V5 consumer behavior; real BTC/ETH Source hashes are checked by actual replay."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
    review_originals_from_prompt,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
    preparation_for_capture,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run
from tests.decision_product.finalized_coverage_support import (
    CHILD,
    EVIDENCE_ID,
    FACT_ID,
    NATIVE_CALL,
    PROVIDER_REFERENCE,
    captures,
    make_case,
    mapped,
    set_path,
    sha,
    wire,
)


@pytest.mark.parametrize("event_plan", [False, True], ids=["synthesis-v2", "synthesis-v3"])
def test_v5_native_result_delivers_child_data_through_both_mappers(event_plan):
    case = make_case(event_plan=event_plan)
    expected = deepcopy(case.delivery)
    capture = captures(case)[0]
    assert capture.authorization.verdict == "approved"
    assert capture.call_seq == 7 and capture.result_seq == 8
    result = mapped(case)
    assert [row.evidence_id for row in result.result.evidence_candidates] == [EVIDENCE_ID]
    assert [row.fact_id for row in result.result.facts] == [FACT_ID]
    fact = result.result.facts[0]
    assert fact.attributes == expected["facts"][0]["attributes"]
    assert fact.value == "42.5" and fact.quality == "accepted"
    evidence = result.result.evidence_candidates[0]
    assert evidence.tool_call_id == PROVIDER_REFERENCE != NATIVE_CALL
    assert evidence.freshness_status == "fresh"
    assert evidence.research_session_id == case.binding["sessionId"]
    assert result.perpetual_synthesis.authorization.verdict == "approved"
    assert result.result.total_tool_calls == 3
    assert result.perpetual_synthesis.selection.call_event_index == 6
    assert result.perpetual_synthesis.selection.result_event_index == 7


@pytest.mark.parametrize("event_plan", [False, True], ids=["synthesis-v2", "synthesis-v3"])
def test_v5_preparation_reader_keeps_actual_prepare_arguments_and_history(event_plan):
    case = make_case(event_plan=event_plan)
    review = next(row["data"]["value"] for row in case.events
                  if row["type"] == "tool/result" and row["data"].get("callId") == "review")
    proof = preparation_for_capture(
        _sdk_run(case.payload()).notifications, expected_session_id=case.binding["sessionId"],
        call_id=case.final_call["data"]["callId"],
        catalog=review_originals_from_prompt(review["actual_prompt"]), request=case.request,
    )
    assert proof["preparation"] == case.wrapper["preparation"]
    assert proof["finalize_arguments"] == case.final_call["data"]["arguments"]
    assert proof["prepare_arguments"]["action"] == "prepare"
    assert [attempt["authorization"].verdict for attempt in proof["attempts"]] == ["approved"]


def test_v5_evidence_without_facts_still_enters_coverage():
    result = mapped(make_case(facts=False)).result
    assert [row.evidence_id for row in result.evidence_candidates] == [EVIDENCE_ID]
    assert result.facts == []
    assert not [gap for gap in result.final_coverage.gaps if gap.requirement_id == "event_identity"]


def test_identical_root_and_child_payloads_merge_once_without_extra_invocations():
    result = mapped(make_case(root_duplicate=True)).result
    assert [row.evidence_id for row in result.evidence_candidates] == [EVIDENCE_ID]
    assert [row.fact_id for row in result.facts] == [FACT_ID]
    assert result.total_tool_calls == 4
    assert len(result.rounds[0].tool_invocations) == 4


@pytest.mark.parametrize("kind", ["evidence", "fact"])
def test_root_child_same_id_with_different_content_is_rejected(kind):
    case = make_case(root_duplicate=True)
    assert len(mapped(case).result.evidence_candidates) == 1
    if kind == "evidence":
        case.delivery["evidence_candidates"][0]["excerpt"] += " Conflicting content."
    else:
        case.delivery["facts"][0]["value"] = "43.5"
    with pytest.raises(AgentExecutionError):
        mapped(case)


@pytest.mark.parametrize("event_plan", [False, True], ids=["synthesis-v2", "synthesis-v3"])
def test_old_v4_protected_child_text_does_not_change_old_evidence_or_capture_identity(event_plan):
    case = make_case(version="v4", event_plan=event_plan)
    payload = case.payload()
    before_bytes = payload.events_json
    result = mapped(case)
    assert result.result.status == "degraded"
    assert result.result.evidence_candidates == [] and result.result.facts == []
    assert "event_identity" in result.result.stop_reason.remaining_hard_gaps
    selection = result.perpetual_synthesis.selection
    assert (selection.call_seq, selection.result_seq) == (7, 8)
    assert (selection.call_event_index, selection.result_event_index) == (6, 7)
    original = json.loads(before_bytes)
    assert selection.call_event_hash == sha(wire(original[6]))
    assert selection.result_event_hash == sha(wire(original[7]))
    assert result.perpetual_synthesis.raw_candidate_hash == sha(wire(case.wrapper["candidate"]))
    assert case.payload().events_json == before_bytes
    assert payload.result_hash == sha("\0" + before_bytes)


@pytest.mark.parametrize("path,bad", [
    (("schema_version",), "decision-hub-finalized-coverage.v0"),
    (("unexpected",), True),
    (("binding", "runId"), "another-run"),
    (("binding", "requestHash"), "0" * 64),
    (("binding", "requestId"), "req_another"),
    (("binding", "sessionId"), "another-root"),
    (("binding", "generation"), 2),
    (("binding", "generation"), True),
    (("finalize_call_id",), "another-finalize"),
    (("finalize_call_seq",), 6),
    (("finalize_call_seq",), True),
    (("finalize_call_seq",), 2 ** 53),
    (("root_cutoff", "before_seq"), 8),
    (("root_cutoff", "events_sha256"), "invalid"),
    (("source_snapshot", "message_id"), "another-snapshot"),
    (("source_snapshot", "message_seq"), 7),
    (("source_snapshot", "body_sha256"), "0" * 64),
    (("child_prefixes", 0, "eventsHash"), "A" * 64),
    (("child_prefixes", 0, "firstLiveSeq"), 7),
    (("child_prefixes", 0, "lastSeq"), -2),
    (("evidence_sources", 0, "execution_session_id"), "not-a-delivered-child"),
    (("evidence_sources", 0, "native_call_id"), PROVIDER_REFERENCE),
    (("evidence_sources", 0, "capability_id"), "not.allowed"),
    (("evidence_sources", 0, "evidence_id"), "not-a-carrier-evidence"),
    (("evidence_sources",), []),
    (("evidence_candidates",), []),
    (("evidence_candidates", 0, "quality"), "candidate"),
    (("evidence_candidates", 0, "quality"), "rejected"),
    (("evidence_candidates", 0, "research_session_id"), CHILD),
    (("evidence_candidates", 0, "requirement_id"), "not-a-frozen-requirement"),
    (("facts", 0, "quality"), "candidate"),
    (("facts", 0, "evidence_id"), "missing-evidence"),
    (("facts", 0, "requirement_id"), "not-the-evidence-requirement"),
])
def test_v5_invalid_carrier_never_falls_back_to_legacy_mapping(path, bad):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    set_path(case.delivery, path, bad)
    with pytest.raises(AgentExecutionError):
        mapped(case)


@pytest.mark.parametrize("metadata,bad", [
    ("run_id", "other-run"), ("root_session_id", "other-root"),
    ("request_hash", "0" * 64), ("generation", 2),
    ("command_request_id", "req_other"), ("request_id", "other-product-request"),
    ("snapshot_id", "other-message"), ("schema_version", "decision-hub-protected-context.v4"),
])
def test_source_snapshot_metadata_must_match_even_with_recomputed_text_hash(metadata, bad):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    case.update_snapshot(**{metadata: bad})
    with pytest.raises(AgentExecutionError):
        mapped(case)


@pytest.mark.parametrize("attack", [
    "plugin", "duplicate", "post_call", "replacement", "body_bytes",
])
def test_source_snapshot_requires_one_actual_pre_call_original_message(attack):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    if attack == "plugin":
        case.snapshot["data"]["source"]["plugin"] = "another-plugin"
    elif attack == "duplicate":
        case.events.insert(5, deepcopy(case.snapshot))
    elif attack == "post_call":
        case.snapshot["seq"] = case.final_call["seq"] + 2
        case.delivery["source_snapshot"]["message_seq"] = case.snapshot["seq"]
    elif attack == "replacement":
        case.snapshot["surfaceOp"] = {"start": case.snapshot["seq"], "deleteCount": 1}
    else:
        case.snapshot["data"]["content"][1]["text"] += " "
    with pytest.raises(AgentExecutionError):
        mapped(case)


@pytest.mark.parametrize("attack", ["root", "self_parent", "duplicate", "cycle", "undeclared_tail"])
def test_child_prefix_relationships_and_declaration_remain_bound(attack):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    prefix = case.delivery["child_prefixes"][0]
    if attack == "root":
        prefix["sessionId"] = case.binding["sessionId"]
    elif attack == "self_parent":
        prefix["parentSessionId"] = CHILD
    elif attack == "duplicate":
        case.delivery["child_prefixes"].append(deepcopy(prefix))
    elif attack == "cycle":
        prefix["parentSessionId"] = "second-child"
        other = deepcopy(prefix)
        other.update(sessionId="second-child", parentSessionId=CHILD)
        case.delivery["child_prefixes"].append(other)
    else:
        prefix["lastSeq"] += 1
    if attack != "undeclared_tail":
        case.update_snapshot(declared_prefixes=True)
    with pytest.raises(AgentExecutionError):
        mapped(case)


def test_unrepresented_empty_ancestor_is_not_a_new_consumer_rejection():
    case = make_case()
    case.delivery["child_prefixes"][0]["parentSessionId"] = "empty-owned-ancestor"
    case.update_snapshot(declared_prefixes=True)
    assert [row.evidence_id for row in mapped(case).result.evidence_candidates] == [EVIDENCE_ID]


def test_preserves_source_times_after_call_without_adding_a_new_time_gate():
    case = make_case()
    at = (datetime.fromtimestamp(case.final_call["time"] / 1000, tz=UTC)
          + timedelta(milliseconds=1)).isoformat()
    for row in [*case.delivery["evidence_candidates"], *case.delivery["facts"]]:
        row["observed_at"] = row["received_at"] = at
    result = mapped(case).result
    assert result.evidence_candidates[0].received_at.isoformat() == at
    assert result.facts[0].observed_at.isoformat() == at


def test_v5_without_request_cannot_authorize_capability_or_request_scope():
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    with pytest.raises(AgentExecutionError):
        extract_attested_perpetual_synthesis_captures(
            _sdk_run(case.payload()).notifications,
            expected_session_id=case.binding["sessionId"], request=None,
        )


@pytest.mark.parametrize("attack", [
    "missing", "extra_wrapper_key", "model_argument", "old_prepare", "unreviewed",
])
def test_v5_inherits_complete_prepared_finalize_authorization(attack):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    if attack == "missing":
        case.wrapper.pop("coverage_delivery")
    elif attack == "extra_wrapper_key":
        case.wrapper["unexpected"] = True
    elif attack == "model_argument":
        case.final_call["data"]["arguments"]["coverage_delivery"] = deepcopy(case.delivery)
    elif attack == "old_prepare":
        case.final_call["data"]["arguments"]["prepared_call_id"] = "other-prepare"
    else:
        case.wrapper["review_gate"].update(status="unreviewed", reason="missing_review",
                                         review_call_id=None, review_receipt_hash=None)
    with pytest.raises(AgentExecutionError):
        mapped(case)


@pytest.mark.parametrize("where", ["model", "other_tool"])
def test_model_or_other_tool_copy_does_not_give_v4_child_coverage(where):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    copied = deepcopy(case.wrapper)
    case.wrapper["schema_version"] = "decision-hub-synthesis-tool-result.v4"
    case.wrapper.pop("coverage_delivery")
    if where == "model":
        case.extra_notifications.append({"method": "model.output", "payload": {"value": copied}})
    else:
        case.events.extend([
            {"seq": 9, "type": "tool/call", "data": {
                "callId": "unrelated", "name": "unrelated_tool", "arguments": {},
            }},
            {"seq": 10, "type": "tool/result", "data": {"callId": "unrelated", "value": copied}},
        ])
    result = mapped(case).result
    assert result.evidence_candidates == [] and result.facts == []
    assert result.status == "degraded"


def test_old_v4_content_hash_stays_identical_when_v6_body_contains_more_child_facts():
    case = make_case(version="v4")
    before = mapped(case).result
    body = json.loads(case.snapshot["data"]["content"][1]["text"])
    attributes = {"exact_execution": "synthetic complete receipt " + "x" * 1100}
    digest = sha(wire(attributes))
    projected = deepcopy(body["facts"][0])
    projected.pop("attributes")
    projected["attributes_ref"] = {"body_hash": digest}
    second = deepcopy(projected)
    second["fact_id"] = "synthetic-second-child-fact"
    body.update(schema_version="decision-hub-protected-context.v6",
                facts=[projected, second],
                shared_fact_attributes=[{"body_hash": digest, "body": attributes}])
    case.snapshot["data"]["content"][1]["text"] = wire(body)
    after = mapped(case).result
    assert before.evidence_candidates == after.evidence_candidates == []
    assert before.facts == after.facts == []
    assert before.status == after.status == "degraded"
    assert research_result_content_hash(before) == research_result_content_hash(after)


def test_multiple_real_source_references_for_one_child_evidence_are_allowed():
    case = make_case()
    case.delivery["child_prefixes"][0]["providerCalls"].append({
        "callId": "synthetic-second-native-call", "receiptHash": "a" * 64,
    })
    second = deepcopy(case.delivery["evidence_sources"][0])
    second["native_call_id"] = "synthetic-second-native-call"
    case.delivery["evidence_sources"].append(second)
    case.update_snapshot(declared_prefixes=True)
    assert [row.evidence_id for row in mapped(case).result.evidence_candidates] == [EVIDENCE_ID]


@pytest.mark.parametrize("key", ["evidence_candidates", "facts", "evidence_sources"])
def test_duplicate_rows_in_the_carrier_are_rejected_before_merge(key):
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    case.delivery[key].append(deepcopy(case.delivery[key][0]))
    with pytest.raises(AgentExecutionError):
        mapped(case)


def test_failed_native_finalization_cannot_supply_child_coverage():
    case = make_case()
    assert captures(case)[0].authorization.verdict == "approved"
    case.events[-1]["data"]["isError"] = True
    with pytest.raises(AgentExecutionError):
        mapped(case)


def test_incomplete_transport_keeps_root_evidence_without_merging_finalized_child_data():
    case = make_case(root_duplicate=True)
    root = next(row["data"]["value"] for row in case.events
                if row["type"] == "tool/result"
                and row["data"].get("callId") == "synthetic-root-call")
    root["evidence_candidates"][0]["evidence_id"] = "synthetic-root-only-evidence"
    root["facts"][0].update(fact_id="synthetic-root-only-fact",
                            evidence_id="synthetic-root-only-evidence")
    assert [row.evidence_id for row in mapped(case).result.evidence_candidates] == [
        "synthetic-root-only-evidence", EVIDENCE_ID,
    ]
    result = mapped(case, finish_reason="error")
    assert [row.evidence_id for row in result.result.evidence_candidates] == [
        "synthetic-root-only-evidence",
    ]
    assert [row.fact_id for row in result.result.facts] == ["synthetic-root-only-fact"]
    assert result.perpetual_synthesis is None and result.result.status == "degraded"
