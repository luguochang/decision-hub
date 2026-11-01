"""Pure first-publication projection of an already authenticated V2 research packet.

No SQL, files, network, current clock or permission. The unique publisher supplies
the authenticated snapshot and assigned anchor under its own fences/CAS. These
values grant no financial qualification, executable quantity or fill authority.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v1 import (
    DecisionActionPlanHeadV1,
)
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionCandidateV2,
    ActionComparisonV2,
    ActionPlanRevisionV2,
    DecisionMethodInputsV2,
    DecisionSnapshotV2,
    DecisionSourceRefV1,
    FrozenForecastV1,
)

from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_action_synthesis import (
    normalize_action_synthesis,
    validate_action_review_binding_v2,
)
from .action_comparison import compare_action_candidates
from .publication_anchor import BoundPublicationTarget, freeze_publication_anchor

if TYPE_CHECKING:
    from ..application.perpetual_cost_assumptions import PerpetualCostAssumptionsOriginal
    from ..application.perpetual_research_reader import TrustedPerpetualResearch


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _ref(prefix, body):
    digest = canonical_hash(body)
    return {"ref": prefix + digest, "hash": digest}


def _seal(model, body, field):
    parsed = model.model_validate({**body, field: "0" * 64})
    wire = parsed.model_dump(mode="json")
    wire[field] = canonical_hash({key: value for key, value in wire.items() if key != field})
    return model.model_validate(wire)


@dataclass(frozen=True)
class PerpetualPublicationOriginal:
    role: str
    schema_version: str
    reference: DecisionSourceRefV1
    body_json: str

    @property
    def ref(self) -> DecisionSourceRefV1:
        return self.reference


@dataclass(frozen=True)
class PerpetualPublicationBranch:
    position_branch: str
    comparison_inputs: DecisionMethodInputsV2
    comparison: ActionComparisonV2
    revision: ActionPlanRevisionV2 | None
    head: DecisionActionPlanHeadV1 | None
    sensitivity_cases: tuple[tuple[str, ActionComparisonV2], ...]


@dataclass(frozen=True)
class DerivedPerpetualPublication:
    bound: BoundPublicationTarget
    frozen_forecast: FrozenForecastV1
    snapshot: DecisionSnapshotV2
    branches: tuple[PerpetualPublicationBranch, ...]
    originals: tuple[PerpetualPublicationOriginal, ...]
    uncovered_branches: tuple[str, ...]
    gaps: tuple[str, ...]


def _branch_candidates(synthesis, branch, cost_ref):
    selected = {}
    unavailable = []

    def exclude(candidate, reference, reason):
        # Keep the reviewed intent and its exact reference in the derivation.
        # An unmodelled candidate has no economic value or ranking, including 0.
        unavailable.append(dict(candidate_id=candidate["candidate_id"],
                                reviewed_candidate_ref=reference, reason=reason))

    for candidate, reference in zip(
        synthesis["candidate_set"]["candidates"],
        synthesis["candidate_set"]["candidate_refs"],
        strict=True,
    ):
        if candidate["position_branch"] != branch:
            exclude(candidate, reference, "position_branch_not_compared")
            continue
        action, side = candidate["action"], candidate["target_side"]
        if action == "conditional":
            exclude(candidate, reference, "conditional_fill_distribution_unavailable")
            continue
        key = None
        if action == "no_change" and side == branch:
            key = "no_change"
        elif branch == "flat" and action == "open" and side in {"long", "short"}:
            key = "open_" + side
        elif branch in {"long", "short"} and action == "close" and side == "flat":
            key = "close"
        elif branch in {"long", "short"} and action == "reduce" and side == branch:
            before, after = candidate["quantity_before"], candidate["quantity_after"]
            # 'reduce' alone does not prove the existing calculator's half rule.
            if (
                before is not None
                and after is not None
                and Decimal(before) != 0
                and Decimal(after) * 2 == Decimal(before)
            ):
                key = "reduce_half"
        if (
            key is None
            or key in selected
            or candidate["size_mode"] != "unit_notional"
            or Decimal(candidate["unit_notional_usdt"]) != Decimal("100")
            or candidate["condition_refs"]
            or candidate["nested_action_ref"] is not None
        ):
            raise ValueError("perpetual_publication_reviewed_branch_unsupported")
        if candidate["cost_ref"] != cost_ref:
            raise ValueError("perpetual_publication_candidate_cost_original_missing")
        selected[key] = (candidate, reference)
    expected = (
        {"open_long", "open_short", "no_change"}
        if branch == "flat"
        else {"no_change", "reduce_half", "close"}
    )
    if set(selected) != expected:
        raise ValueError("perpetual_publication_reviewed_branch_incomplete")
    return selected, unavailable


def derive_perpetual_publication(
    *,
    trusted: TrustedPerpetualResearch,
    bound: BoundPublicationTarget,
    cost_original: PerpetualCostAssumptionsOriginal,
    forecast_version: int = 1,
) -> DerivedPerpetualPublication:
    """One primary reviewed branch, one snapshot and one final target, paused.

    Mechanical target-reference rebinding creates server-derived probability/path
    VIEWS while preserving the entire original reviewed forecast and candidate
    set. Weights, amplitudes, preferences and selected candidate bodies stay exact.
    """
    if type(forecast_version) is not int or forecast_version < 1:
        raise ValueError("perpetual_publication_forecast_version_invalid")
    synthesis, packet, authorization, materials = (
        json.loads(text)
        for text in (
            trusted.synthesis_json,
            trusted.review_request_json,
            trusted.authorization_json,
            trusted.materials_json,
        )
    )
    typed = normalize_action_synthesis(synthesis)
    review = validate_action_review_binding_v2(packet, authorization)
    if (
        packet["synthesis"] != synthesis
        or review.verdict != "approved"
        or synthesis["opportunity_id"] != trusted.opportunity_id
        or synthesis["candidate_set"]["context"] != materials["context"]
        or materials["context"]["owner_id"] != trusted.owner_id
        or synthesis["method_ref"] != materials["method_ref"]
        or synthesis["method_code_hash"] != materials["method_code_hash"]
    ):
        raise ValueError("perpetual_publication_trusted_packet_scope_mismatch")
    expected_bound = freeze_publication_anchor(
        candidate_target=synthesis["candidate_target"],
        anchor_rule=synthesis["anchor_rule"],
        candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
        anchor_rule_ref=synthesis["candidate_set"]["anchor_rule_ref"],
        assigned_at=bound.receipt.assigned_at,
        opportunity_id=trusted.opportunity_id,
        commit_id=bound.receipt.commit_id,
    )
    if (
        bound != expected_bound
        or not aware_utc(trusted.known_at) <= bound.receipt.assigned_at < review.valid_until
    ):
        raise ValueError("perpetual_publication_anchor_or_clock_mismatch")
    costs = json.loads(cost_original.body_json)
    cost_ref = {"ref": costs["policy_ref"], "hash": canonical_hash(costs)}
    base_cost, base_cost_ref = costs["base_cost_policy"], costs["base_cost_ref"]
    if (
        costs["purpose"] != "research_unit_only"
        or costs["unit_notional_usdt"] != "100"
        or costs != materials["cost_assumptions"]
        or cost_ref != materials["cost_assumptions_ref"]
        or base_cost != materials["cost_policy"]
        or base_cost_ref != materials["cost_policy_ref"]
        or base_cost_ref["hash"] != canonical_hash(base_cost)
        or cost_ref not in synthesis["economic_input_refs"]
    ):
        raise ValueError("perpetual_publication_cost_original_missing")
    context = deepcopy(materials["context"])
    state = materials["position_context"]["state"]
    if state not in {"flat", "long", "short", "unknown"}:
        raise ValueError("perpetual_publication_position_state_invalid")
    branch = "flat" if state == "unknown" else state
    candidates, unavailable = _branch_candidates(synthesis, branch, base_cost_ref)
    at = bound.receipt.assigned_at
    if any(
        ActionCandidateV2.model_validate(row).valid_until <= at for row, _ in candidates.values()
    ):
        raise ValueError("perpetual_publication_candidate_expired")
    originals = {}

    def add(role, schema, body, reference=None):
        reference = reference or _ref("perp_" + role + "_", body)
        if canonical_hash(body) != reference["hash"]:
            raise ValueError("perpetual_publication_original_hash_mismatch")
        item = PerpetualPublicationOriginal(
            role,
            schema,
            DecisionSourceRefV1.model_validate(reference),
            _json(body),
        )
        old = originals.get(reference["ref"])
        if old is not None and (
            old.reference.hash != item.reference.hash or json.loads(old.body_json) != body
        ):
            raise ValueError("perpetual_publication_original_identity_conflict")
        originals.setdefault(reference["ref"], item)
        return reference

    native_ref = add(
        "native_synthesis",
        synthesis["schema_version"],
        synthesis,
        packet["synthesis_ref"],
    )
    add(
        "review_request",
        packet["schema_version"],
        packet,
        authorization["review_request_ref"],
    )
    review_ref = add(
        "review_authorization",
        authorization["schema_version"],
        authorization,
    )
    candidate_set_ref = add(
        "candidate_set",
        "action-candidate-set.v2",
        synthesis["candidate_set"],
        synthesis["candidate_set_ref"],
    )
    for row, reference in zip(
        synthesis["candidate_set"]["candidates"],
        synthesis["candidate_set"]["candidate_refs"],
        strict=True,
    ):
        add(
            "candidate_" + canonical_hash(row["candidate_id"])[:16],
            "action-candidate.v2",
            row,
            reference,
        )
    candidate_target_ref = add(
        "candidate_target",
        "pending-publication-target.v1",
        synthesis["candidate_target"],
        synthesis["candidate_set"]["candidate_target_ref"],
    )
    rule_ref = add(
        "anchor_rule",
        "publication-anchor-rule.v1",
        synthesis["anchor_rule"],
        synthesis["candidate_set"]["anchor_rule_ref"],
    )
    final_ref = add(
        "final_target",
        "publication-bound-target.v1",
        bound.target.model_dump(mode="json"),
        bound.target_ref.model_dump(mode="json"),
    )
    anchor_ref = add(
        "anchor_receipt",
        "publication-anchor-receipt.v1",
        bound.receipt.model_dump(mode="json"),
        bound.receipt_ref.model_dump(mode="json"),
    )
    event_ref = add(
        "event_probability",
        "event-probability.v1",
        synthesis["event_probability"],
        synthesis["event_probability_ref"],
    )
    original_market_ref = add(
        "native_market_distribution",
        "conditional-market-distribution.v1",
        synthesis["market_distribution"],
        synthesis["market_distribution_ref"],
    )
    original_paths_ref = add(
        "native_paths",
        "assumed-unit-path-distribution.v1",
        synthesis["path_distribution"],
        synthesis["market_distribution"]["path_distribution_ref"],
    )
    original_probability_ref = add(
        "native_probability",
        "probability-estimate.v1",
        synthesis["market_distribution"]["direction_distribution"],
    )
    add("cost_assumptions", costs["schema_version"], costs, cost_ref)
    add("base_cost", "comparison-cost-policy.v1", base_cost, base_cost_ref)
    allowed_ref = add(
        "allowed_domain",
        "action-comparison-allowed-domain.v1",
        packet["allowed_domain"],
        packet["allowed_domain_ref"],
    )
    selection = json.loads(trusted.native_selection_json)
    selection_ref = add("native_selection", "research-native-selection.v1", selection)
    wire_originals = dict(schema_version="perpetual-native-wire-originals.v1")
    for key, text in (
        ("synthesis", trusted.synthesis_json),
        ("review_request", trusted.review_request_json),
        ("authorization", trusted.authorization_json),
    ):
        wire_originals[key + "_json"] = text
        wire_originals[key + "_utf8_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    wire_ref = add("native_wire_originals", "perpetual-native-wire-originals.v1", wire_originals)
    materials_ref = add("research_materials", "perpetual-research-materials.v1", materials)
    # Comparison DTOs normalize legal timestamp spellings. Their body refs must
    # identify those derived wire views, never replace raw forecast originals.
    event = typed.event_probability.model_dump(mode="json")
    comparison_event_ref = add("comparison_event_probability", "event-probability.v1", event)
    paths = typed.path_distribution.model_dump(mode="json")
    paths["market_target_ref"] = final_ref
    paths_ref = add("bound_paths", "assumed-unit-path-distribution.v1", paths)
    market = typed.market_distribution.model_dump(mode="json")
    market["market_target_ref"] = final_ref
    market["direction_distribution"]["target_ref"] = final_ref
    market["path_distribution_ref"] = paths_ref
    market["event_scenario_ref"] = comparison_event_ref
    market_ref = add("bound_market_distribution", "conditional-market-distribution.v1", market)
    frozen = FrozenForecastV1.model_validate(
        dict(
            schema_version="frozen-forecast.v1",
            forecast_id="perp_forecast_"
            + canonical_hash([trusted.opportunity_id, native_ref])[:40],
            opportunity_id=trusted.opportunity_id,
            candidate_target_ref=candidate_target_ref,
            anchor_rule_ref=rule_ref,
            anchor_receipt_ref=anchor_ref,
            final_target_ref=final_ref,
            event_probability_ref=event_ref,
            market_distribution_ref=original_market_ref,
            candidate_probability_ref=original_probability_ref,
            forecast_as_of=typed.as_of,
            binding_kind="publication_bound_commit",
        )
    )
    frozen_ref = add("frozen_forecast", "frozen-forecast.v1", frozen.model_dump(mode="json"))
    probability_commit_ref = add(
        "probability_commit",
        "perpetual-probability-commit.v1",
        dict(
            schema_version="perpetual-probability-commit.v1",
            frozen_forecast_ref=frozen_ref,
            final_target_ref=final_ref,
            original_event_probability_ref=event_ref,
            comparison_event_probability_ref=comparison_event_ref,
            original_market_distribution_ref=original_market_ref,
            bound_market_distribution_ref=market_ref,
            original_paths_ref=original_paths_ref,
            bound_paths_ref=paths_ref,
            transformation="target_refs_only_probabilities_and_amplitudes_unchanged",
        ),
    )
    action_targets = {
        key: add(
            "outcome_target_" + key,
            "perpetual-action-outcome-target.v1",
            dict(
                schema_version="perpetual-action-outcome-target.v1",
                reviewed_candidate_ref=reference,
                final_target_ref=final_ref,
                cost_policy_ref=base_cost_ref,
                paths_ref=paths_ref,
                unit_notional_usdt="100",
                outcomes=["net_usdt_positive", "net_usdt_zero", "net_usdt_negative"],
                basis="assumed_fills_preserve_reviewed_path_weights",
            ),
        )
        for key, (_, reference) in candidates.items()
    }
    price_basis = add(
        "price_basis",
        "perpetual-unchanged-path-price-basis.v1",
        dict(
            schema_version="perpetual-unchanged-path-price-basis.v1",
            original_paths_ref=original_paths_ref,
            original_forecast_as_of=synthesis["as_of"],
            meaning="same_reviewed_relative_gross_price_path_no_market_repricing",
        ),
    )
    inputs = dict(
        schema_version="decision-method-inputs.v2",
        opportunity_id=trusted.opportunity_id,
        forecast_version=forecast_version,
        instrument_id=context["instrument_id"],
        scope_id=context["scope_id"],
        target=bound.target.model_dump(mode="json"),
        event_probability=event,
        market_distribution=market,
        path_distribution=paths,
        position_branch=branch,
        unit_notional_usdt="100",
        bindings=dict(
            forecast=frozen_ref,
            settings=context["settings_ref"],
            instrument=context["instrument_ref"],
            utility=context["utility_ref"],
            cost=base_cost_ref,
            target=final_ref,
            event_probability=comparison_event_ref,
            market_distribution=market_ref,
            paths=paths_ref,
            method=synthesis["method_ref"],
            account=context["account_ref"],
            position=context["position_ref"],
        ),
        action_target_refs=action_targets,
        cost_policy=base_cost,
        forecast_as_of=synthesis["as_of"],
        comparison_as_of=at,
        forecast_known_at_max=synthesis["known_at_max"],
        comparison_known_at_max=trusted.known_at,
        utility_policy=deepcopy(materials["utility_policy"]),
        account_snapshot=deepcopy(materials["account_snapshot"]),
        candidate_leverage=materials.get("execution_profile", {}).get(
            "initial_notional_to_margin_ratio"
        ),
        price_bridge=dict(
            forecast_price_ref=price_basis,
            comparison_price_ref=price_basis,
            comparison_to_forecast_price_ratio="1",
            known_at=typed.as_of,
        ),
    )
    method_inputs = DecisionMethodInputsV2.model_validate(inputs)
    wire_inputs = method_inputs.model_dump(mode="json")
    inputs_ref = add("comparison_inputs", "decision-method-inputs.v2", wire_inputs)
    comparison = ActionComparisonV2.model_validate(compare_action_candidates(wire_inputs))
    comparison_ref = add("comparison", "action-comparison.v2", comparison.model_dump(mode="json"))
    sensitivity = []
    for case in costs["sensitivity_cases"]:
        case_inputs = deepcopy(wire_inputs)
        case_inputs["cost_policy"] = case["cost_policy"]
        case_inputs["bindings"]["cost"] = (
            base_cost_ref
            if case["case_id"] == "base"
            else add(
                "sensitivity_cost_" + case["case_id"],
                "comparison-cost-policy.v1",
                case["cost_policy"],
            )
        )
        case_result = ActionComparisonV2.model_validate(compare_action_candidates(case_inputs))
        add(
            "sensitivity_" + case["case_id"],
            "action-comparison.v2",
            case_result.model_dump(mode="json"),
        )
        sensitivity.append((case["case_id"], case_result))
    gaps = [
        *synthesis["gaps"],
        "assumed_costs_actual_fee_tier_unknown",
        "future_funding_unknown_explicit_zero_base",
        "fill_and_account_feasibility_unknown",
        "paper_execution_not_authorized",
    ]
    if state == "unknown":
        gaps.append("assumed_flat_not_account_operation")
    if unavailable:
        gaps.append("reviewed_candidates_unavailable_and_unranked")
    gaps = list(dict.fromkeys(gaps))
    if len(gaps) > 128:
        raise ValueError("perpetual_publication_gap_capacity_exceeded")
    uncovered = tuple(key for key in ("flat", "long", "short") if key != branch)
    derivation_ref = add(
        "derivation",
        "perpetual-publication-derivation.v1",
        dict(
            schema_version="perpetual-publication-derivation.v1",
            native_synthesis_ref=native_ref,
            review_authorization_ref=review_ref,
            admission_ref=json.loads(trusted.admission_ref_json),
            task_binding_ref=json.loads(trusted.task_binding_ref_json),
            mapped_original=trusted.mapped_ref.model_dump(mode="json"),
            provider_original=trusted.provider_emission_ref.model_dump(mode="json"),
            transport_original=trusted.transport_ref.model_dump(mode="json"),
            producer_generation=trusted.producer_generation,
            native_wire_originals_ref=wire_ref,
            research_materials_ref=materials_ref,
            native_paths_ref=original_paths_ref,
            bound_paths_ref=paths_ref,
            comparison_inputs_ref=inputs_ref,
            comparison_ref=comparison_ref,
            final_target_ref=final_ref,
            anchor_receipt_ref=anchor_ref,
            candidate_id_mapping={key: row["candidate_id"] for key, (row, _) in candidates.items()},
            unavailable_candidates=unavailable,
            changes="one_assigned_target_rebinding_and_typed_datetime_wire_projection_only",
            original_raw_forecast_retained=True,
            probabilities_and_amplitudes_changed=False,
            unit_basis="100_USDT_comparison_not_balance_size_or_fill",
            primary_position_state=state,
            uncovered_branches=list(uncovered),
            gaps=gaps,
        ),
    )
    bindings = dict(
        candidate_set=candidate_set_ref,
        comparison=comparison_ref,
        candidate_target=candidate_target_ref,
        final_target=final_ref,
        anchor_rule=rule_ref,
        anchor_receipt=anchor_ref,
        frozen_forecast=frozen_ref,
        probability_commit=probability_commit_ref,
        event_probability=comparison_event_ref,
        market_distribution=market_ref,
        instrument=context["instrument_ref"],
        settings=context["settings_ref"],
        utility=context["utility_ref"],
        review_authorization=review_ref,
        native_synthesis=native_ref,
        native_selection=selection_ref,
        method=synthesis["method_ref"],
        allowed_domain=allowed_ref,
        method_code_hash=synthesis["method_code_hash"],
        account=context["account_ref"],
        position=context["position_ref"],
        derivation=derivation_ref,
    )
    decision_id = (
        "perp_decision_"
        + canonical_hash([trusted.run_id, trusted.opportunity_id, bound.receipt.commit_id])[:40]
    )
    snapshot = _seal(
        DecisionSnapshotV2,
        dict(
            schema_version="decision-snapshot.v2",
            id=decision_id,
            opportunity_id=trusted.opportunity_id,
            run_id=trusted.run_id,
            source_result_ref=trusted.mapped_ref.output_id,
            source_result_hash=trusted.mapped_ref.payload_hash,
            decision_cutoff_at=typed.as_of,
            known_at=at,
            scope_id=context["scope_id"],
            horizon=context["horizon"],
            context=context,
            bindings=bindings,
            gaps=gaps,
        ),
        "snapshot_hash",
    )
    revision = head = None
    if comparison.selected_action_id is not None:
        candidate, candidate_ref = candidates[comparison.selected_action_id]
        plan_id = (
            "perp_plan_"
            + canonical_hash(
                [trusted.owner_id, context["account_id"], trusted.opportunity_id, branch]
            )[:40]
        )
        revision = _seal(
            ActionPlanRevisionV2,
            dict(
                schema_version="decision-action-plan-revision.v2",
                revision_id="perp_revision_"
                + canonical_hash([plan_id, decision_id, comparison_ref])[:40],
                plan_id=plan_id,
                decision_id=decision_id,
                opportunity_id=trusted.opportunity_id,
                revision_no=1,
                previous_id=None,
                scope_id=context["scope_id"],
                horizon=context["horizon"],
                as_of=typed.as_of,
                published_at=at,
                valid_until=min(
                    typed.candidate_set.valid_until,
                    review.valid_until,
                    ActionCandidateV2.model_validate(candidate).valid_until,
                ),
                context=context,
                position_context=state,
                selected_candidate=candidate,
                selected_candidate_ref=candidate_ref,
                bindings=bindings,
                target_spec_ref=final_ref["ref"],
                target_spec_hash=final_ref["hash"],
                advice_qualification="research_trial",
                execution="paper_only",
                reason=("假设当前空仓，非账户操作。" if state == "unknown" else "")
                + "已审完整候选的100 USDT单位假设比较；费用及零资金费为显式假设，"
                "未知账户及成交可行性；初始监控暂停，未授权执行。",
            ),
            "payload_hash",
        )
        head = DecisionActionPlanHeadV1.model_validate(
            dict(
                schema_version="decision-action-plan-head.v1",
                plan_id=plan_id,
                current_revision_id=revision.revision_id,
                state_version=1,
                state="proposed",
                monitor_state="paused",
                next_due=None,
                valid_until=revision.valid_until,
                lease_owner=None,
                lease_generation=0,
                lease_expires_at=None,
                last_input_watermark=None,
                enter_streak=0,
            )
        )
    return DerivedPerpetualPublication(
        bound,
        frozen,
        snapshot,
        (
            PerpetualPublicationBranch(
                branch, method_inputs, comparison, revision, head, tuple(sensitivity)
            ),
        ),
        tuple(originals.values()),
        uncovered,
        tuple(gaps),
    )
