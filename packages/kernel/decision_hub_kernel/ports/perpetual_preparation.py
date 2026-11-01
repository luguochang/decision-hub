"""Pure reference assembly. Callers authenticate the catalog and native sources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewRequestV2,
    DecisionReviewOriginalsV1,
    PerpetualSynthesisDraftV1,
)

from .market_path import aware_utc
from .research_action_synthesis import normalize_action_synthesis


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def body_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def derived_ref(prefix, body):
    digest = body_hash(body)
    return dict(ref=prefix + "_" + digest, hash=digest)


def prepare_perpetual_synthesis(
    draft: Mapping[str, Any], *, request_id: str, binding: dict[str, Any],
    review_originals: dict[str, Any], requested_at: str,
) -> dict[str, Any]:
    """Preserve judgments/numbers/text; derive internal references from exact bodies."""
    if isinstance(draft, dict) and draft.get("schema_version") == "perpetual-synthesis-draft.v2":
        from .event_scenario_preparation import prepare_perpetual_event_synthesis

        return prepare_perpetual_event_synthesis(
            draft,
            request_id=request_id,
            binding=binding,
            review_originals=review_originals,
            requested_at=requested_at,
        )
    from datetime import datetime

    PerpetualSynthesisDraftV1.model_validate(draft)
    DecisionReviewOriginalsV1.model_validate(review_originals)
    draft, catalog, binding = deepcopy(draft), deepcopy(review_originals), deepcopy(binding)
    seen = set()
    for row in catalog["originals"]:
        body, key = json.loads(row["body_json"]), canonical(row["source_ref"])
        if (
            key in seen
            or canonical(body) != row["body_json"]
            or body_hash(body) != row["source_ref"]["hash"]
        ):
            raise ValueError("decision_hub_preparation_server_body_mismatch")
        seen.add(key)

    def server(role):
        rows = [
            r
            for r in catalog["originals"]
            if r["source_kind"] == "server_material" and r["role"] == role
        ]
        if len(rows) != 1:
            raise ValueError("decision_hub_preparation_server_original_missing")
        return json.loads(rows[0]["body_json"])

    target, rule, policy = (
        server("candidate_target"),
        server("anchor_rule"),
        server("allowed_domain_policy"),
    )
    unit_costs = server("cost_assumptions")
    unit_cost_rows = [
        row
        for row in catalog["originals"]
        if row["source_kind"] == "server_material" and row["role"] == "cost_policy"
    ]
    if (
        body_hash(target) != binding["candidate_target_ref"]["hash"]
        or body_hash(rule) != binding["anchor_rule_ref"]["hash"]
        or policy["method_ref"] != binding["method_ref"]
        or policy["method_code_hash"] != binding["method_code_hash"]
    ):
        raise ValueError("decision_hub_preparation_server_binding_mismatch")
    def instant(text):
        return aware_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    if instant(draft["valid_until"]) > min(
        instant(target["valid_until"]), instant(policy["valid_until"])
    ):
        raise ValueError("decision_hub_preparation_expiry_outside_domain")
    available = {
        canonical(ref)
        for row in catalog["originals"]
        for ref in (row["source_ref"], row["original_ref"])
        if ref is not None
    }
    for ref in [
        binding["candidate_target_ref"],
        binding["anchor_rule_ref"],
        binding["method_ref"],
        *[v for k, v in binding["context"].items() if k.endswith("_ref")],
        target["conditioning_ref"],
    ]:
        if ref is not None:
            available.add(canonical(ref))

    def original(ref):
        if ref is not None and canonical(ref) not in available:
            raise ValueError("decision_hub_preparation_original_unavailable")
        return deepcopy(ref)

    assumptions = {}
    for row in draft["assumptions"]:
        if row["assumption_id"] in assumptions:
            raise ValueError("decision_hub_preparation_duplicate_assumption")
        for ref in row["evidence_refs"]:
            original(ref)
        assumptions[row["assumption_id"]] = row

    def resolve(ref, *roles):
        if ref is None:
            return None
        if "local_id" in ref:
            body = assumptions.get(ref["local_id"])
            if body is None:
                raise ValueError("decision_hub_preparation_assumption_unavailable")
            if roles and body["role"] not in roles:
                raise ValueError("decision_hub_preparation_assumption_role_mismatch")
            return derived_ref("decision_model_assumption", body)
        return original(ref)

    def estimate(value, target_role):
        return dict(
            value,
            target_ref=resolve(value["target_ref"], target_role),
            source_ref=resolve(value["source_ref"], "probability_source"),
            conditioning_ref=resolve(value["conditioning_ref"], "conditioning", "event_definition"),
            model_ref=original(value["model_ref"]),
            calibration_ref=original(value["calibration_ref"]),
        )

    by_id = {row["candidate_id"]: row for row in draft["candidates"]}
    if len(by_id) != len(draft["candidates"]):
        raise ValueError("decision_hub_preparation_duplicate_candidate")
    built: dict[str, dict[str, Any]]
    visiting: set[str]
    built, visiting = {}, set()

    def candidate_body(key: str) -> dict[str, Any]:
        if key in visiting:
            raise ValueError("decision_hub_preparation_candidate_cycle")
        if key in built:
            return built[key]
        row = by_id.get(key)
        if row is None:
            raise ValueError("decision_hub_preparation_candidate_child_unavailable")
        visiting.add(key)
        nested_id = row["nested_candidate_id"]
        body = {k: v for k, v in row.items() if k != "nested_candidate_id"}
        body.update(
            schema_version="action-candidate.v2",
            context=binding["context"],
            valid_until=draft["valid_until"],
            execution="paper_only",
            nested_action_ref=None
            if nested_id is None
            else derived_ref("action_candidate", candidate_body(nested_id)),
            condition_refs=[resolve(r, "entry_condition") for r in row["condition_refs"]],
            size_policy_ref=resolve(row["size_policy_ref"], "size_policy"),
            exit_ref=resolve(row["exit_ref"], "exit_policy"),
            target_refs=[resolve(r, "action_target") for r in row["target_refs"]],
            basis_refs=[resolve(r, "basis") for r in row["basis_refs"]],
        )
        for field in ("cost_ref", "funding_ref", "margin_ref", "fill_ref"):
            body[field] = original(row[field])
        if (
            unit_costs.get("schema_version") == "perpetual-cost-assumptions.v1"
            and unit_costs.get("purpose") == "research_unit_only"
            and body["cost_ref"] == unit_cost_rows[0]["original_ref"] == unit_costs["base_cost_ref"]
            and unit_costs["base_cost_policy"] == server("cost_policy")
            and body["size_mode"] == "unit_notional"
            and body["quantity_before"] is None
            and body["quantity_after"] is None
        ):
            # The declared cost original owns its research unit, never an
            # executable quantity. Keep the source draft/hash exact.
            body["unit_notional_usdt"] = unit_costs["unit_notional_usdt"]
        built[key] = body
        visiting.remove(key)
        return body

    candidates = [candidate_body(row["candidate_id"]) for row in draft["candidates"]]
    candidate_set = dict(
        schema_version="action-candidate-set.v2",
        candidate_set_id=draft["candidate_set_id"],
        context=binding["context"],
        as_of=draft["as_of"],
        valid_until=draft["valid_until"],
        candidate_target_ref=binding["candidate_target_ref"],
        anchor_rule_ref=binding["anchor_rule_ref"],
        candidates=candidates,
        candidate_refs=[derived_ref("action_candidate", c) for c in candidates],
    )
    event = dict(draft["event_probability"], schema_version="event-probability.v1")
    for key, role in (
        ("event_definition_ref", "event_definition"),
        ("occurrence_target_ref", "occurrence_target"),
        ("outcome_target_ref", "outcome_target"),
        ("joint_partition_ref", "joint_partition"),
        ("observation_rule_ref", "observation_rule"),
    ):
        event[key] = resolve(event[key], role)
    event["occurrence_distribution"] = estimate(
        event["occurrence_distribution"], "occurrence_target"
    )
    event["outcome_given_occurrence"] = estimate(
        event["outcome_given_occurrence"], "outcome_target"
    )
    outcome = event["outcome_given_occurrence"]
    if (
        outcome["kind"] == "unavailable"
        and outcome["probabilities"] is None
        and outcome["conditioning_ref"] is None
    ):
        # This identifies the conditional target; no probability is supplied.
        # The exact Provider draft remains unchanged and hash-bound below.
        outcome["conditioning_ref"] = deepcopy(event["event_definition_ref"])
    event_ref = derived_ref("event_probability", event)
    paths = dict(
        draft["path_distribution"],
        market_target_ref=binding["candidate_target_ref"],
        conditioning_ref=target["conditioning_ref"],
        fill_weight_assumption_ref=resolve(
            draft["path_distribution"]["fill_weight_assumption_ref"], "fill_weight_assumption"
        ),
    )
    paths_ref = derived_ref("assumed_unit_paths", paths)
    direction = dict(
        draft["direction_distribution"],
        target_ref=binding["candidate_target_ref"],
        conditioning_ref=target["conditioning_ref"],
        source_ref=resolve(draft["direction_distribution"]["source_ref"], "probability_source"),
        model_ref=original(draft["direction_distribution"]["model_ref"]),
        calibration_ref=original(draft["direction_distribution"]["calibration_ref"]),
    )
    market = dict(
        schema_version="conditional-market-distribution.v1",
        instrument_ref=binding["context"]["instrument_ref"],
        market_target_ref=binding["candidate_target_ref"],
        event_scenario_ref=event_ref if draft["market_event_conditioned"] else None,
        conditioning_ref=target["conditioning_ref"],
        path_distribution_ref=paths_ref,
        direction_distribution=direction,
    )
    set_ref, market_ref = (
        derived_ref("action_candidate_set", candidate_set),
        derived_ref("market_distribution", market),
    )
    domain = dict(
        schema_version="action-comparison-allowed-domain.v1",
        candidate_set_ref=set_ref,
        candidate_target_ref=binding["candidate_target_ref"],
        anchor_rule_ref=binding["anchor_rule_ref"],
        event_probability_ref=event_ref,
        market_distribution_ref=market_ref,
        **{
            key: policy[key]
            for key in (
                "method_ref",
                "method_code_hash",
                "variables",
                "invalidation_refs",
                "valid_until",
            )
        },
    )
    domain_ref = derived_ref("action_allowed_domain", domain)
    for ref in [*draft["economic_input_refs"], *draft["reasoning_refs"]]:
        original(ref)
    analysis = dict(
        draft["analysis"],
        schema_version="decision-causal-analysis.v1",
        candidate_target_ref=binding["candidate_target_ref"],
    )
    for link in analysis["main_chain"] + analysis["opposite_chain"]:
        for ref in link["evidence_refs"]:
            original(ref)
    candidate = dict(
        schema_version="research-decision-synthesis.v2",
        request_id=request_id,
        opportunity_id=binding["opportunity_id"],
        as_of=draft["as_of"],
        known_at_max=draft["known_at_max"],
        candidate_set=candidate_set,
        candidate_set_ref=set_ref,
        candidate_target=target,
        anchor_rule=rule,
        event_probability=event,
        event_probability_ref=event_ref,
        market_distribution=market,
        market_distribution_ref=market_ref,
        path_distribution=paths,
        economic_input_refs=[*draft["economic_input_refs"], paths_ref],
        method_ref=binding["method_ref"],
        method_code_hash=binding["method_code_hash"],
        allowed_domain_ref=domain_ref,
        comparison_ref=None,
        target_binding="pending_anchor",
        reasoning_refs=draft["reasoning_refs"],
        analysis=analysis,
        gaps=draft["gaps"],
    )
    normalize_action_synthesis(candidate)
    packet = dict(
        schema_version="action-review-request.v2",
        synthesis_ref=derived_ref("research_decision_synthesis", candidate),
        synthesis=candidate,
        allowed_domain_ref=domain_ref,
        allowed_domain=domain,
        requested_at=requested_at,
    )
    ActionReviewRequestV2.model_validate(packet)
    return dict(
        candidate=candidate,
        review_request=packet,
        assumptions=draft["assumptions"],
        source_draft_hash=body_hash(draft),
        normalized_synthesis_hash=body_hash(candidate),
        normalized_review_request_hash=body_hash(packet),
    )
