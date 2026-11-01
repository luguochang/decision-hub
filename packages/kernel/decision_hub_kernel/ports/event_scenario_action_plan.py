"""Canonical event-plan boundary. Parsing/hashing grants no review authority."""

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioActionPlanDraftV1,
    EventScenarioActionPlanV1,
    EventScenarioConditionEvaluationV1,
    EventScenarioRealizationV1,
    EventScenarioSelectionInputsV1,
    ScenarioBranchSelectionV1,
)

from ..decision.action_comparison import _ref, content_hash
from ..decision.event_scenario_action_plan import (
    METHOD_ID,
    build_event_scenario_action_plan,
    evaluate_event_scenario_action_plan,
)


def event_scenario_method_material(
    *,
    base_method_ref: dict,
    base_method_code_hash: str,
    implementation_dependencies: dict[str, str],
) -> tuple[dict, dict, str]:
    """Version the wrapper separately; caller supplies the retained source hashes.

    Register this material alongside the existing comparison method in Task2.
    This pure adapter never changes that method's historical identity.
    """
    import re

    _ref(base_method_ref)
    if (
        not re.fullmatch(r"[a-f0-9]{64}", base_method_code_hash)
        or not implementation_dependencies
        or any(
            not name or not re.fullmatch(r"[a-f0-9]{64}", digest)
            for name, digest in implementation_dependencies.items()
        )
    ):
        raise ValueError("event_plan_method_dependencies_invalid")
    code_hash = content_hash(
        dict(
            base_method_code_hash=base_method_code_hash,
            implementation_dependencies=implementation_dependencies,
        )
    )
    body = dict(
        schema_version="event-scenario-comparison-method.v1",
        method_id=METHOD_ID,
        base_method_ref=dict(base_method_ref),
        base_method_code_hash=base_method_code_hash,
        implementation_dependencies=dict(implementation_dependencies),
        qualification="research_trial",
        financial_qualified=False,
        method_code_hash=code_hash,
    )
    reference = dict(ref="decision-method:" + METHOD_ID, hash=content_hash(body))
    return body, reference, code_hash


def prepare_event_scenario_action_plan(
    draft: dict,
    authenticated_originals: dict[str, dict],
) -> dict:
    """Shape/local relations only; caller authenticates all supplied originals."""
    EventScenarioActionPlanDraftV1.model_validate(draft)
    result = build_event_scenario_action_plan(draft, authenticated_originals)
    EventScenarioActionPlanV1.model_validate(result)
    return result


def select_event_scenario_action_plan(
    plan: dict,
    realization: dict | None,
    condition_evaluations: list[dict],
    verified_market_account_cost_inputs: dict,
    *,
    as_of: str,
) -> dict:
    """Call only after authenticated native review and source readers in Task2."""
    # Datetime parsing may normalize '.000000Z' to 'Z'. Retain exact wire bodies
    # after validation because their external refs bind those lexical values.
    EventScenarioActionPlanV1.model_validate(plan)
    if realization is not None:
        EventScenarioRealizationV1.model_validate(realization)
    for row in condition_evaluations:
        EventScenarioConditionEvaluationV1.model_validate(row)
    EventScenarioSelectionInputsV1.model_validate(verified_market_account_cost_inputs)
    result = evaluate_event_scenario_action_plan(
        plan,
        realization,
        condition_evaluations,
        verified_market_account_cost_inputs,
        as_of=as_of,
    )
    ScenarioBranchSelectionV1.model_validate(result)
    return result
