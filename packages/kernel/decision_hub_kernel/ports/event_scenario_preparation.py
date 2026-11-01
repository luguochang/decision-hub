"""Native event preparation from authenticated Provider arguments and catalog.

Local declarations are reviewed model output. They never become official facts.
The source-call reader authenticates ownership; this module derives identities,
explicit unit-account projections and comparisons without changing judgments.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewRequestV3,
    PerpetualSynthesisDraftV2,
    ResearchDecisionSynthesisV3,
)

from ..decision.action_comparison import _utc, content_hash
from .event_scenario_action_plan import (
    event_scenario_method_material,
    prepare_event_scenario_action_plan,
)
from .event_scenario_manifest import pack_event_scenario_manifest, unpack_event_scenario_manifest

LOCAL = "local:event:"
# Financial transformation closure. Provider normalization and lossless manifest
# transport are authenticated/replayed by the native chain, not a new calculator.
METHOD_FILES = ("decision/event_scenario_action_plan.py", "ports/event_scenario_action_plan.py")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def derived_ref(prefix, body):
    digest = content_hash(body)
    return dict(ref=prefix + "_" + digest, hash=digest)


def event_scenario_method_original(
    *, base_method_ref=None, base_method_code_hash=None, include_source=True
):
    if base_method_ref is None:
        from ..application.perpetual_research_binding import comparison_method_original

        _, base_method_ref, base_method_code_hash = comparison_method_original(include_source=True)
    root = Path(__file__).resolve().parents[1]
    sources = {name: (root / name).read_bytes() for name in METHOD_FILES}
    dependencies = {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()}
    body, _, code_hash = event_scenario_method_material(
        base_method_ref=base_method_ref,
        base_method_code_hash=base_method_code_hash,
        implementation_dependencies=dependencies,
    )
    if include_source:
        body["source_bundle"] = dict(
            schema_version="event-scenario-method-source-bundle.v1",
            content_kind="immutable_code_data",
            files={
                name: dict(sha256=dependencies[name], source_text=data.decode("utf8"))
                for name, data in sources.items()
            },
        )
    return (
        body,
        dict(
            ref="decision-method:event-scenario-assumed-unit-mean-net.v1", hash=content_hash(body)
        ),
        code_hash,
    )


def _catalog_maps(
    catalog: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    originals, roles = {}, {}
    for row in catalog["originals"]:
        body = json.loads(row["body_json"])
        if row["body_json"] != canonical(body) or row["source_ref"]["hash"] != content_hash(body):
            raise ValueError("event_native_catalog_original_mismatch")
        for reference in (row["source_ref"], row["original_ref"]):
            if reference is not None and reference["hash"] == content_hash(body):
                originals[reference["ref"]] = body
        if row["source_kind"] == "server_material":
            if row["role"] in roles:
                raise ValueError("event_native_catalog_role_duplicate")
            roles[row["role"]] = (row, body)
    return originals, roles


def _row(role, body, original_ref):
    return dict(
        source_ref=dict(ref="review-original:server_material:" + role, hash=content_hash(body)),
        original_ref=original_ref,
        source_kind="server_material",
        role=role,
        body_json=canonical(body),
    )


def prepare_event_scenario_catalog(
    draft,
    *,
    binding,
    catalog,
    source_known_at,
    source_emission_ref=None,
    source_emission_context=None,
):
    """The caller must authenticate the actual Provider source call first."""
    PerpetualSynthesisDraftV2.model_validate(draft)
    raw = deepcopy(draft["event_plan_draft"])
    if not _utc(raw["as_of"]) <= _utc(source_known_at) < _utc(raw["freeze_before"]):
        raise ValueError("event_native_source_not_before_freeze")
    if raw["as_of"] != draft["primary_draft"]["as_of"]:
        raise ValueError("event_native_primary_forecast_cutoff_mismatch")
    originals, roles = _catalog_maps(catalog)
    source_rules = roles.get("event_scenario_source_rules")
    if source_rules is not None:
        for item in source_rules[1]["rules"]:
            if item["observation_rule_ref"]["hash"] != content_hash(item["rule"]):
                raise ValueError("event_native_source_rule_hash_mismatch")
            originals[item["observation_rule_ref"]["ref"]] = item["rule"]
    agenda = roles.get("event_agenda")
    if (
        agenda is None
        or raw["event"]["schedule_ref"] != agenda[0]["source_ref"]
        or not any(
            entry["event_key"] == raw["event"]["event_key"]
            and entry["family"] == raw["event"]["family"]
            and entry["stage_id"] == raw["event"]["stage_id"]
            and entry["scheduled_at"] is not None
            and _utc(entry["scheduled_at"]) == _utc(raw["event"]["scheduled_at"])
            and _utc(entry["known_at"]) <= _utc(raw["as_of"])
            for entry in agenda[1]["agenda"]["entries"]
        )
    ):
        raise ValueError("event_native_authenticated_schedule_unavailable")
    method, method_ref, method_hash = event_scenario_method_original(
        base_method_ref=binding["method_ref"], base_method_code_hash=binding["method_code_hash"]
    )
    originals[method_ref["ref"]] = method
    references = {}

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"ref", "hash"} and value["ref"].startswith(LOCAL):
                if value["ref"] not in references:
                    raise KeyError(value["ref"])
                return deepcopy(references[value["ref"]])
            return {key: resolve(item) for key, item in cast(dict[str, Any], value).items()}
        return value

    declared: list[dict[str, Any]] = []
    pending = list(draft["event_plan_originals"])
    while pending:
        remaining = []
        for row in pending:
            if not row["source_ref"]["ref"].startswith(LOCAL):
                raise ValueError("event_native_declared_original_must_be_local")
            try:
                body = resolve(json.loads(row["body_json"]))
            except KeyError:
                remaining.append(row)
                continue
            if row["body_json"] != canonical(json.loads(row["body_json"])):
                raise ValueError("event_native_declared_original_not_canonical")
            reference = dict(ref=row["source_ref"]["ref"], hash=content_hash(body))
            if reference["ref"] in references and references[reference["ref"]] != reference:
                raise ValueError("event_native_declared_original_conflict")
            references[reference["ref"]] = reference
            originals[reference["ref"]] = body
            declared.append(dict(source_ref=reference, role=row["role"], body_json=canonical(body)))
        if len(remaining) == len(pending):
            # Target-start-price rules depend on target bodies, resolved below.
            if all(row["role"] == "forecast_price_rule" for row in remaining):
                break
            raise ValueError("event_native_local_original_cycle_or_missing")
        pending = remaining
    # Fixed targets and scenario definitions are declared explicitly; no ordinary
    # candidate conditioning or publication clock is substituted here.
    for branch in raw["branches"]:
        for inputs in branch["comparison_inputs_by_position"].values():
            target = resolve(inputs["target"])
            reference = inputs["bindings"]["target"]
            if reference["ref"].startswith(LOCAL):
                references[reference["ref"]] = dict(ref=reference["ref"], hash=content_hash(target))
                originals[reference["ref"]] = target
    for row in pending:
        body = resolve(json.loads(row["body_json"]))
        reference = dict(ref=row["source_ref"]["ref"], hash=content_hash(body))
        references[reference["ref"]] = reference
        originals[reference["ref"]] = body
        declared.append(dict(source_ref=reference, role=row["role"], body_json=canonical(body)))
    if raw["event"]["family"] == "cpi":
        reference = resolve(raw["event"]["observation_rule_ref"])
        rule = originals.get(reference["ref"])
        supported: list[dict[str, Any]] = [] if source_rules is None else source_rules[1]["rules"]
        if rule is None or not any(
            item["observation_rule_ref"] == reference and item["rule"] == rule for item in supported
        ):
            raise ValueError("event_native_cpi_reference_period_or_source_rule_pending")
        semantics = {key: rule[key] for key in ("value_kind", "precision", "partition_family")}
        for key in ("event_definition_ref", "partition_ref"):
            definition = originals.get(resolve(raw["event"][key])["ref"], {})
            if any(definition.get(name) != value for name, value in semantics.items()):
                raise ValueError("event_native_cpi_published_value_semantics_required")
        declared.append(
            dict(source_ref=reference, role="observation_rule", body_json=canonical(rule))
        )
    if (
        raw["event"]["family"] == "pce"
        and originals.get(resolve(raw["event"]["observation_rule_ref"])["ref"], {}).get("adapter")
        == "bea_pce_release_selector.v1"
    ):
        from .pce_source_rules import declared_pce_source_rule

        declared.append(declared_pce_source_rule(
            resolve(raw["event"]), [] if source_rules is None else source_rules[1]["rules"],
            agenda[1]["agenda"]["entries"],
        ))
    if raw["event"]["family"] == "nfp":
        from .employment_source_rules import declared_employment_source_rule

        reference = resolve(raw["event"]["observation_rule_ref"])
        if (
            isinstance(reference, dict)
            and originals.get(reference["ref"], {}).get("adapter")
            == "bls_employment_release_selector.v1"
        ):
            event = resolve(raw["event"])
            if not isinstance(event, dict):
                raise ValueError("event_native_employment_source_rule_pending")
            declared.append(declared_employment_source_rule(
                event, [] if source_rules is None else source_rules[1]["rules"],
                agenda[1]["agenda"]["entries"],
            ))
    if (
        raw["event"]["family"] == "fomc"
        and originals.get(resolve(raw["event"]["observation_rule_ref"])["ref"], {}).get("adapter")
        == "fed_fomc_statement_selector.v1"
    ):
        from .fomc_source_rules import declared_fomc_source_rule

        declared.append(declared_fomc_source_rule(
            resolve(raw["event"]), [] if source_rules is None else source_rules[1]["rules"],
            agenda[1]["agenda"]["entries"],
        ))
    # Marginal is a separate target, never a silent reconditioning of a branch.
    if raw["marginal_target"] is not None:
        target = resolve(raw["marginal_target"])
        reference = raw["marginal_target_ref"]
        if reference["ref"].startswith(LOCAL):
            references[reference["ref"]] = dict(ref=reference["ref"], hash=content_hash(target))
            originals[reference["ref"]] = target
    if raw["method_ref"] != method_ref and not raw["method_ref"]["ref"].startswith(LOCAL):
        raise ValueError("event_native_authored_method_original_mismatch")
    raw.update(method_ref=method_ref, method_code_hash=method_hash)
    # Register only the known intrinsic local bodies in their dependency order.
    # Server-owned economic references are never re-hashed from authored values.
    for branch in raw["branches"]:
        for position, inputs in branch["comparison_inputs_by_position"].items():
            for name, body_name in (
                ("event_probability", "event_probability"),
                ("paths", "path_distribution"),
                ("market_distribution", "market_distribution"),
            ):
                reference = inputs["bindings"][name]
                body = resolve(inputs[body_name])
                if reference["ref"].startswith(LOCAL):
                    references[reference["ref"]] = dict(
                        ref=reference["ref"], hash=content_hash(body)
                    )
                    originals[reference["ref"]] = body
            forecast = inputs["bindings"]["forecast"]
            if forecast["ref"].startswith(LOCAL):
                body = dict(
                    schema_version="event-scenario-forecast-cutoff.v1",
                    as_of=raw["as_of"],
                    event=resolve(raw["event"]),
                    occurrence=resolve(raw["occurrence"]),
                    outcome_given_occurrence=resolve(raw["outcome_given_occurrence"]),
                )
                references[forecast["ref"]] = dict(ref=forecast["ref"], hash=content_hash(body))
                originals[forecast["ref"]] = body
            for action, reference in inputs["action_target_refs"].items():
                if reference["ref"].startswith(LOCAL):
                    body = dict(
                        schema_version="event-scenario-action-target.v1",
                        position_branch=position,
                        action_id=action,
                        target_ref=resolve(inputs["bindings"]["target"]),
                        cost_ref=branch["cost_ref"],
                        fill_assumption_ref=resolve(branch["fill_assumption_ref"]),
                    )
                    references[reference["ref"]] = dict(
                        ref=reference["ref"], hash=content_hash(body)
                    )
                    originals[reference["ref"]] = body
    raw["context"] = deepcopy(binding["context"])
    # Unit utility/account refs are supplied below from authenticated server data.
    raw["context"].update(account_ref=None, position_ref=None)
    for branch in raw["branches"]:
        branch["account_ref"] = None
        for inputs in branch["comparison_inputs_by_position"].values():
            inputs["bindings"].update(
                account=None,
                position=None,
                utility=binding["context"]["utility_ref"],
                instrument=binding["context"]["instrument_ref"],
                settings=binding["context"]["settings_ref"],
                method=binding["method_ref"],
            )
            inputs["utility_policy"] = deepcopy(roles["utility_policy"][1])
    raw = resolve(raw)
    estimates = [raw["occurrence"], raw["outcome_given_occurrence"]]
    estimates.extend(
        inputs["market_distribution"]["direction_distribution"]
        for branch in raw["branches"]
        for inputs in branch["comparison_inputs_by_position"].values()
    )
    if any(estimate["kind"] not in {"subjective", "unavailable"} for estimate in estimates):
        raise ValueError("event_native_non_subjective_probability_qualification_unavailable")
    # Unit-only V1 comparison explicitly removes account feasibility. Preserve
    # every saved limit/side/leverage preference, with an exact projected policy.
    utility = deepcopy(roles["utility_policy"][1])
    if utility.get("schema_version") != "utility-policy.v2":
        raise ValueError("event_native_utility_original_required")
    utility.update(
        policy_ref="event-unit-utility:" + raw["plan_id"],
        account_ref=None,
        position_scenario="unknown",
    )
    utility["hash"] = content_hash({key: item for key, item in utility.items() if key != "hash"})
    unit_utility_ref = dict(ref=utility["policy_ref"], hash=utility["hash"])
    context = deepcopy(binding["context"])
    context.update(account_ref=None, position_ref=None, utility_ref=unit_utility_ref)
    raw.update(context=context, method_ref=method_ref, method_code_hash=method_hash)
    costs_row, costs = roles["cost_policy"]
    costs_ref = costs_row["original_ref"]
    if costs_ref is None or costs_ref["hash"] != content_hash(costs):
        raise ValueError("event_native_cost_original_required")
    for branch in raw["branches"]:
        branch.update(account_ref=None)
        if branch["cost_ref"] != costs_ref:
            raise ValueError("event_native_authored_cost_ref_mismatch")
        for position, inputs in branch["comparison_inputs_by_position"].items():
            if inputs["cost_policy"] != costs:
                raise ValueError("event_native_authored_cost_body_mismatch")
            inputs.update(
                instrument_id=context["instrument_id"],
                scope_id=context["scope_id"],
                opportunity_id=binding["opportunity_id"],
                utility_policy=deepcopy(utility),
                account_snapshot=None,
                position_branch=position,
            )
            inputs["bindings"].update(
                instrument=context["instrument_ref"],
                settings=context["settings_ref"],
                account=None,
                position=None,
                utility=unit_utility_ref,
                method=binding["method_ref"],
            )
            for name, body_name in (
                ("event_probability", "event_probability"),
                ("target", "target"),
                ("paths", "path_distribution"),
            ):
                reference = inputs["bindings"][name]
                body = inputs[body_name]
                if reference["ref"].startswith(LOCAL):
                    inputs["bindings"][name] = dict(ref=reference["ref"], hash=content_hash(body))
            paths_ref = inputs["bindings"]["paths"]
            inputs["market_distribution"]["path_distribution_ref"] = deepcopy(paths_ref)
            inputs["market_distribution"]["event_scenario_ref"] = deepcopy(
                inputs["bindings"]["event_probability"]
            )
            reference = inputs["bindings"]["market_distribution"]
            if reference["ref"].startswith(LOCAL):
                inputs["bindings"]["market_distribution"] = dict(
                    ref=reference["ref"], hash=content_hash(inputs["market_distribution"])
                )
    plan = prepare_event_scenario_action_plan(raw, originals)
    plan_ref = derived_ref("event_scenario_action_plan", plan)
    draft_ref = derived_ref("event_scenario_action_plan_draft", raw)
    manifest = pack_event_scenario_manifest(
        dict(event_plan=plan, normalized_event_plan_draft=raw, declared_originals=declared)
    )
    manifest_ref = derived_ref("event_scenario_preparation_manifest", manifest)
    body = dict(
        schema_version="event-scenario-prepared-original.v2",
        manifest=manifest,
        event_plan_ref=plan_ref,
        event_plan_manifest_ref=manifest_ref,
        event_plan_draft_ref=draft_ref,
        source_draft_hash=content_hash(draft),
        source_emission_ref=source_emission_ref,
        source_emission_context=source_emission_context,
    )
    result = deepcopy(catalog)
    result["originals"] = [
        row
        for row in result["originals"]
        if row["role"] not in {"event_scenario_plan", "event_scenario_method"}
    ]
    result["originals"].extend(
        [
            _row("event_scenario_method", method, method_ref),
            _row("event_scenario_plan", body, manifest_ref),
        ]
    )
    return result


def prepare_perpetual_event_synthesis(
    draft: dict[str, Any], *, request_id: str, binding: dict[str, Any],
    review_originals: dict[str, Any], requested_at: str,
) -> dict[str, Any]:
    from .perpetual_preparation import prepare_perpetual_synthesis

    PerpetualSynthesisDraftV2.model_validate(draft)
    _, roles = _catalog_maps(review_originals)
    material = roles.get("event_scenario_plan")
    if material is None or material[1]["source_draft_hash"] != content_hash(draft):
        raise ValueError("event_native_prepared_source_original_mismatch")
    prepared = material[1]
    expanded = unpack_event_scenario_manifest(prepared["manifest"])
    if prepared["event_plan_manifest_ref"]["hash"] != content_hash(prepared["manifest"]):
        raise ValueError("event_native_manifest_original_mismatch")
    if _utc(requested_at) >= _utc(expanded["event_plan"]["freeze_before"]):
        raise ValueError("event_native_prepare_after_freeze")
    # Rebuild from exact normalized recipe and retained originals during replay.
    originals, _ = _catalog_maps(review_originals)
    for row in expanded["declared_originals"]:
        originals[row["source_ref"]["ref"]] = json.loads(row["body_json"])
    recipe = expanded["normalized_event_plan_draft"]
    if recipe["marginal_target"] is not None:
        originals[recipe["marginal_target_ref"]["ref"]] = recipe["marginal_target"]
    plan = prepare_event_scenario_action_plan(recipe, originals)
    if plan != expanded["event_plan"] or prepared["event_plan_ref"]["hash"] != content_hash(plan):
        raise ValueError("event_native_plan_replay_mismatch")
    ordinary_catalog: dict[str, Any] = dict(
        review_originals,
        originals=[
            row
            for row in review_originals["originals"]
            if row["role"] not in {"event_scenario_plan", "event_scenario_method"}
        ],
    )
    primary = prepare_perpetual_synthesis(
        draft["primary_draft"],
        request_id=request_id,
        binding=binding,
        review_originals=ordinary_catalog,
        requested_at=requested_at,
    )
    first = primary["candidate"]
    candidate = dict(
        schema_version="research-decision-synthesis.v3",
        request_id=request_id,
        opportunity_id=first["opportunity_id"],
        as_of=first["as_of"],
        known_at_max=first["known_at_max"],
        primary_v2=first,
        primary_v2_ref=derived_ref("research_decision_synthesis", first),
        event_plan_manifest_ref=prepared["event_plan_manifest_ref"],
        event_plan_ref=prepared["event_plan_ref"],
        event_plan_draft_ref=prepared["event_plan_draft_ref"],
    )
    domain = dict(
        plan_ref=prepared["event_plan_ref"],
        method_ref=plan["method_ref"],
        method_code_hash=plan["method_code_hash"],
        freeze_before=plan["freeze_before"],
        valid_until=max((row["valid_until"] for row in plan["branches"]), key=_utc),
        branches=[
            dict(
                branch_ref=row["branch_ref"],
                candidate_refs=[
                    reference
                    for position in ("flat", "long", "short")
                    for reference in row["reviewed_candidate_refs_by_position"][position]
                ],
                applicable_from=row["applicable_from"],
                valid_until=row["valid_until"],
            )
            for row in plan["branches"]
        ],
    )
    packet = dict(
        schema_version="action-review-request.v3",
        synthesis=candidate,
        synthesis_ref=derived_ref("research_decision_synthesis", candidate),
        primary_review_request=primary["review_request"],
        primary_review_request_ref=derived_ref("action_review_request", primary["review_request"]),
        event_plan_domain=domain,
        event_plan_domain_ref=derived_ref("event_scenario_review_domain", domain),
        requested_at=requested_at,
    )
    ResearchDecisionSynthesisV3.model_validate(candidate)
    ActionReviewRequestV3.model_validate(packet)
    return dict(
        candidate=candidate,
        review_request=packet,
        assumptions=primary["assumptions"],
        source_draft_hash=content_hash(draft),
        normalized_synthesis_hash=content_hash(candidate),
        normalized_review_request_hash=content_hash(packet),
        primary_v2_hash=content_hash(first),
        primary_review_request_hash=content_hash(primary["review_request"]),
        event_plan_draft_hash=prepared["event_plan_draft_ref"]["hash"],
        event_plan_hash=content_hash(plan),
    )
