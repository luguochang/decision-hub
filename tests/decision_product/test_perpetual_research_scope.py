"""Financial scope checks on a synthetic actual-native packet; not an admission proof."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
    validate_perpetual_packet_scope,
)
from tests.runtime.test_d2_native_v2 import extractor, fixture


def case():
    raw, request, notifications = fixture()
    capture = extractor()(
        notifications,
        expected_session_id=raw["context"]["binding"]["sessionId"],
        request=request,
    )[0]
    synthesis = capture.synthesis.model_dump(mode="json")
    domain = capture.review_request.allowed_domain.model_dump(mode="json")
    policy = {
        key: deepcopy(domain[key])
        for key in (
            "method_ref",
            "method_code_hash",
            "variables",
            "invalidation_refs",
            "valid_until",
        )
    }
    policy.update(
        mutation_scope="none_original_frozen_inputs_only", deterministic_recomparison_enabled=False
    )
    admitted = {
        "opportunity_id": synthesis["opportunity_id"],
        "context": deepcopy(synthesis["candidate_set"]["context"]),
        "candidate_target": deepcopy(synthesis["candidate_target"]),
        "anchor_rule": deepcopy(synthesis["anchor_rule"]),
        "candidate_target_ref": deepcopy(synthesis["candidate_set"]["candidate_target_ref"]),
        "anchor_rule_ref": deepcopy(synthesis["candidate_set"]["anchor_rule_ref"]),
        "method_ref": deepcopy(synthesis["method_ref"]),
        "method_code_hash": synthesis["method_code_hash"],
        "allowed_domain_policy": policy,
        "admitted_at": synthesis["as_of"],
        "valid_until": domain["valid_until"],
    }
    return request, capture, admitted


def test_reviewed_packet_matches_original_server_scope_without_inventing_authority():
    request, native, admission = case()
    validate_perpetual_packet_scope(
        admission,
        native,
        request_id=request.request_id,
        known_at=datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
    )


@pytest.mark.parametrize("changed", ["owner", "settings", "method", "clock", "mutation"])
def test_native_approval_cannot_override_server_owner_inputs_policy_or_clock(changed):
    request, native, admission = case()
    if changed == "owner":
        admission["context"]["owner_id"] = "different-owner"
    elif changed == "settings":
        admission["context"]["settings_ref"]["hash"] = "f" * 64
    elif changed == "method":
        admission["method_code_hash"] = "f" * 64
    elif changed == "clock":
        admission["valid_until"] = "2026-01-01T00:20:00Z"
    else:
        admission["allowed_domain_policy"]["deterministic_recomparison_enabled"] = True
    with pytest.raises(ValueError, match="perpetual_native_server_"):
        validate_perpetual_packet_scope(
            admission,
            native,
            request_id=request.request_id,
            known_at=datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
        )


def raw_packet(native):
    wrapper = json.loads(native.review_wrapper_json)
    return (
        wrapper,
        json.loads(wrapper["candidate_json"]),
        json.loads(wrapper["review_request_json"]),
    )


@pytest.mark.parametrize("authorization_count", [1, 2])
def test_review_original_extraction_matches_native_text_block_selection(authorization_count):
    """Pure extraction after attestation; prose is not a second authorization."""
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _review_originals,
    )

    _, native, _ = case()
    wrapper, _, _ = raw_packet(native)
    authorization = next(
        block for block in wrapper["original_output"]["output"]
        if block.get("type") == "text"
    )
    wrapper["original_output"]["output"] = [
        {"type": "text", "text": "审核完成，以下是授权结果。"},
        {"type": "text", "text": None},
        *[deepcopy(authorization) for _ in range(authorization_count)],
    ]
    observed = replace(native, review_wrapper_json=json.dumps(wrapper, ensure_ascii=False))
    if authorization_count == 1:
        assert _review_originals(observed).authorization_json == authorization["text"]
    else:
        with pytest.raises(ValueError, match="perpetual_native_review_original_authorization_missing"):
            _review_originals(observed)


def test_legal_timestamp_lexical_form_keeps_original_review_hash_and_output_bytes():
    """Lawful wire variation of real fixture; pure scope check, no new native/DB claim."""
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        ActionReviewAuthorizationV2,
        ActionReviewRequestV2,
    )
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        observation_sha,
    )
    from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
        validate_action_review_binding_v2,
    )

    request, native, admission = case()
    wrapper, candidate, packet = raw_packet(native)
    packet["requested_at"] = packet["requested_at"].replace("Z", ".000Z")
    authorization = native.authorization.model_dump(mode="json")
    authorization["review_request_ref"]["hash"] = observation_sha(packet)
    authorization["reviewer_input_ref"]["hash"] = observation_sha(packet)
    # First prove the raw packet itself remains valid, before typed normalization.
    validate_action_review_binding_v2(packet, authorization)
    packet_text = json.dumps(packet, ensure_ascii=False, indent=1)
    authorization_text = json.dumps(authorization, ensure_ascii=False, indent=2)
    wrapper["review_request_json"] = packet_text
    wrapper["review_request_sha256"] = hashlib.sha256(packet_text.encode()).hexdigest()
    wrapper["original_output"]["output"] = [{"type": "text", "text": authorization_text}]
    lexical = replace(
        native,
        review_request=ActionReviewRequestV2.model_validate(packet),
        authorization=ActionReviewAuthorizationV2.model_validate(authorization),
        review_wrapper_json=json.dumps(wrapper),
    )
    assert lexical.review_request.model_dump(mode="json")["requested_at"] != packet["requested_at"]
    validate_perpetual_packet_scope(
        admission,
        lexical,
        request_id=request.request_id,
        known_at=datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        validate_perpetual_provider_packet,
    )

    originals = validate_perpetual_provider_packet(
        lexical, {"candidate": candidate, "review_request": packet}
    )
    assert originals.review_request_json == packet_text
    assert originals.authorization_json == authorization_text
    assert originals.synthesis_json == wrapper["candidate_json"]


@pytest.mark.parametrize("changed", ["candidate", "review_request"])
def test_provider_join_rejects_different_raw_original_even_when_typed_times_equal(changed):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        validate_perpetual_provider_packet,
    )
    from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
        normalize_action_synthesis,
    )

    _, native, _ = case()
    _, candidate, packet = raw_packet(native)
    arguments = {"candidate": deepcopy(candidate), "review_request": deepcopy(packet)}
    validate_perpetual_provider_packet(native, arguments)
    if changed == "candidate":
        arguments["candidate"]["known_at_max"] = candidate["known_at_max"].replace("Z", ".000Z")
        assert normalize_action_synthesis(arguments["candidate"]) == native.synthesis
    else:
        arguments["review_request"]["requested_at"] = packet["requested_at"].replace("Z", ".000Z")
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            ActionReviewRequestV2,
        )

        assert (
            ActionReviewRequestV2.model_validate(arguments["review_request"])
            == native.review_request
        )
    with pytest.raises(ValueError, match="perpetual_provider_native_"):
        validate_perpetual_provider_packet(native, arguments)
