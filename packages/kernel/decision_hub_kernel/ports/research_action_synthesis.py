"""Pure codec for native decision synthesis; no source or publication authority."""

import hashlib
import json
from copy import deepcopy
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewAuthorizationV2,
    ActionReviewAuthorizationV3,
    ActionReviewRequestV2,
    ActionReviewRequestV3,
    ResearchDecisionSynthesisV2,
    ResearchDecisionSynthesisV3,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import (
    ResearchDecisionSynthesisV1,
)


def primary_perpetual_synthesis(value):
    if isinstance(value, dict):
        return (
            value["primary_v2"]
            if value.get("schema_version") == "research-decision-synthesis.v3"
            else value
        )
    return value.primary_v2 if isinstance(value, ResearchDecisionSynthesisV3) else value


def primary_perpetual_review_request(value):
    if isinstance(value, dict):
        return (
            value["primary_review_request"]
            if value.get("schema_version") == "action-review-request.v3"
            else value
        )
    return value.primary_review_request if isinstance(value, ActionReviewRequestV3) else value


def primary_perpetual_authorization(value):
    if isinstance(value, dict):
        return (
            value["primary_authorization"]
            if value.get("schema_version") == "action-review-authorization.v3"
            else value
        )
    return value.primary_authorization if isinstance(value, ActionReviewAuthorizationV3) else value


def parse_action_review_request(value):
    version = value.get("schema_version") if isinstance(value, dict) else value.schema_version
    return (
        ActionReviewRequestV3 if version == "action-review-request.v3" else ActionReviewRequestV2
    ).model_validate(value)


def _body_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _body_binding(reference: dict, body: dict) -> None:
    if reference["hash"] != _body_hash(body):
        raise ValueError("research_v2_included_body_hash_mismatch")


def _synthesis_body_bindings(value: dict) -> None:
    """Included JSON content identities only, never authentication of external originals."""
    if value.get("schema_version") == "research-decision-synthesis.v3":
        primary = value["primary_v2"]
        _synthesis_body_bindings(primary)
        _body_binding(value["primary_v2_ref"], primary)
        if any(
            value[key] != primary[key]
            for key in ("request_id", "opportunity_id", "as_of", "known_at_max")
        ):
            raise ValueError("research_v3_primary_projection_mismatch")
        return
    candidates = value["candidate_set"]
    for reference, body in (
        (value["candidate_set_ref"], candidates),
        (candidates["candidate_target_ref"], value["candidate_target"]),
        (candidates["anchor_rule_ref"], value["anchor_rule"]),
        (value["event_probability_ref"], value["event_probability"]),
        (value["market_distribution_ref"], value["market_distribution"]),
        (value["market_distribution"]["path_distribution_ref"], value["path_distribution"]),
        *zip(candidates["candidate_refs"], candidates["candidates"], strict=True),
    ):
        _body_binding(reference, body)
    if value["market_distribution"]["path_distribution_ref"] not in value["economic_input_refs"]:
        raise ValueError("research_v2_assumed_paths_economic_binding_missing")


def validate_action_review_binding_v2(
    request: object, authorization: object
) -> ActionReviewAuthorizationV2 | ActionReviewAuthorizationV3:
    """Validate exact frozen packet binding, not session independence or publish authority.

    Callers must independently resolve/authenticate the request, native child-session
    input/output and originals. Pass archived JSON; no V1 review is adapted.
    """
    if isinstance(request, dict) and request.get("schema_version") == "action-review-request.v3":
        return validate_action_review_binding_v3(request, authorization)
    if isinstance(request, ActionReviewRequestV3):
        return validate_action_review_binding_v3(request.model_dump(mode="json"), authorization)
    packet = ActionReviewRequestV2.model_validate(request)
    review = ActionReviewAuthorizationV2.model_validate(authorization)
    raw = request if isinstance(request, dict) else packet.model_dump(mode="json")
    synth = raw["synthesis"]
    _synthesis_body_bindings(synth)
    _body_binding(raw["synthesis_ref"], synth)
    _body_binding(raw["allowed_domain_ref"], raw["allowed_domain"])
    _body_binding(review.review_request_ref.model_dump(mode="json"), raw)
    expected = {
        "synthesis_ref": raw["synthesis_ref"],
        **{
            key: synth[key]
            for key in [
                "candidate_set_ref",
                "event_probability_ref",
                "market_distribution_ref",
                "method_ref",
                "method_code_hash",
                "allowed_domain_ref",
            ]
        },
        "candidate_target_ref": synth["candidate_set"]["candidate_target_ref"],
        "anchor_rule_ref": synth["candidate_set"]["anchor_rule_ref"],
        "invalidation_refs": raw["allowed_domain"]["invalidation_refs"],
    }
    checked = review.model_dump(mode="json")
    if any(checked[key] != value for key, value in expected.items()):
        raise ValueError("research_v2_review_packet_binding_mismatch")
    if review.reviewer_input_ref != review.review_request_ref:
        raise ValueError("research_v2_review_input_packet_mismatch")
    if (
        not packet.requested_at
        <= review.reviewed_at
        < review.valid_until
        <= packet.allowed_domain.valid_until
    ):
        raise ValueError("research_v2_review_packet_expiry")
    return review


def validate_action_review_binding_v3(
    request: dict, authorization: object
) -> ActionReviewAuthorizationV3:
    """Exact nested actual-review projections, with independent event expiry."""

    packet = ActionReviewRequestV3.model_validate(request)
    review = ActionReviewAuthorizationV3.model_validate(authorization)
    raw_review = (
        authorization if isinstance(authorization, dict) else review.model_dump(mode="json")
    )
    synth, domain = request["synthesis"], request["event_plan_domain"]
    _synthesis_body_bindings(synth)
    for reference, body in (
        (request["synthesis_ref"], synth),
        (request["primary_review_request_ref"], request["primary_review_request"]),
        (request["event_plan_domain_ref"], domain),
        (raw_review["review_request_ref"], request),
    ):
        _body_binding(reference, body)
    if request["primary_review_request"]["synthesis"] != synth["primary_v2"]:
        raise ValueError("research_v3_primary_request_projection_mismatch")
    scope = raw_review["event_plan_scope"]
    primary = validate_action_review_binding_v2(
        request["primary_review_request"], raw_review["primary_authorization"]
    )
    if (
        domain["plan_ref"] != synth["event_plan_ref"]
        or {
            key: item
            for key, item in scope.items()
            if key not in {"schema_version", "reviewed_at", "verdict"}
        }
        != domain
        or raw_review["synthesis_ref"] != request["synthesis_ref"]
        or raw_review["reviewer_input_ref"] != raw_review["review_request_ref"]
        or review.reviewed_at != primary.reviewed_at
        or review.event_plan_scope.reviewed_at != review.reviewed_at
        or review.verdict != primary.verdict
        or review.event_plan_scope.verdict != review.verdict
        or not packet.requested_at <= review.reviewed_at < review.event_plan_scope.freeze_before
    ):
        raise ValueError("research_v3_event_review_binding_mismatch")
    return review


def normalize_action_synthesis(
    value: object,
) -> ResearchDecisionSynthesisV1 | ResearchDecisionSynthesisV2 | ResearchDecisionSynthesisV3:
    """Validate the full wrapper after existing legacy representation repairs.

    Actions, thresholds and references are never inferred or repaired. Only the
    nested old research object receives the already installed Tool's lossless
    list/field-placement normalization. The input remains unchanged.
    """
    if isinstance(value, dict) and value.get("schema_version") in {
        "research-decision-synthesis.v2",
        "research-decision-synthesis.v3",
    }:
        model = (
            ResearchDecisionSynthesisV3
            if value["schema_version"] == "research-decision-synthesis.v3"
            else ResearchDecisionSynthesisV2
        )
        parsed = model.model_validate(value)
        _synthesis_body_bindings(value)
        return parsed
    if not isinstance(value, dict):
        return ResearchDecisionSynthesisV1.model_validate(value)
    result = deepcopy(value)
    candidate = result.get("research_candidate_v1")
    if isinstance(candidate, dict):
        case = candidate.get("causal_case")
        if (
            "horizons" not in candidate
            and isinstance(case, dict)
            and isinstance(case.get("horizons"), list)
        ):
            candidate["horizons"] = case.pop("horizons")

        def unique(refs: Any) -> Any:
            return (
                list(dict.fromkeys(refs))
                if isinstance(refs, list) and all(isinstance(item, str) for item in refs)
                else refs
            )

        if isinstance(candidate.get("horizons"), list):
            for horizon in candidate["horizons"]:
                if isinstance(horizon, dict) and "evidence_refs" in horizon:
                    horizon["evidence_refs"] = unique(horizon["evidence_refs"])
        if isinstance(case, dict):
            cited = []
            for name in ("main_chain", "opposite_chain"):
                if isinstance(case.get(name), list):
                    for link in case[name]:
                        if isinstance(link, dict) and "evidence_refs" in link:
                            link["evidence_refs"] = unique(link["evidence_refs"])
                            if isinstance(link["evidence_refs"], list):
                                cited.extend(
                                    item for item in link["evidence_refs"] if isinstance(item, str)
                                )
            case["evidence_refs"] = unique(case.get("evidence_refs", cited))
    return ResearchDecisionSynthesisV1.model_validate(result)
