"""Local V2 contract relations. Never authenticates hashes, sessions or owners."""

from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any


def decision_candidate_v2_issues(raw: Any) -> tuple[str, ...]:
    issues: list[str] = []

    def check(ok: bool, code: str) -> None:
        if not ok:
            issues.append("decision_v2_candidate_" + code)

    def stamp(value):
        return datetime.fromisoformat(value)

    def visit(value):
        if isinstance(value, list):
            for row in value:
                visit(row)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("schema_version")
        if kind == "event-scenario-preparation-manifest.v1":
            return
        if kind in {"event-scenario-action-plan-draft.v1", "event-scenario-action-plan.v1"}:
            as_of, freeze = stamp(value["as_of"]), stamp(value["freeze_before"])
            event, context = value["event"], value["context"]
            check(
                stamp(value["known_at_max"]) <= as_of < freeze < stamp(event["scheduled_at"]),
                "event_plan_freeze",
            )
            predicates = value["scenario_predicates"]
            scenarios = {row["scenario_id"]: row for row in predicates}
            check(len(scenarios) == len(predicates), "event_plan_scenarios")
            check(
                value["outcome_given_occurrence"]["conditioning_ref"]
                == event["event_definition_ref"],
                "event_plan_p_condition",
            )
            probabilities = value["outcome_given_occurrence"]["probabilities"]
            check(
                probabilities is not None
                and {row["outcome_id"] for row in probabilities} == set(scenarios),
                "event_plan_p_partition",
            )
            check(
                len({row["branch_id"] for row in value["branches"]}) == len(value["branches"]),
                "event_plan_branches",
            )
            for branch in value["branches"]:
                check(branch["scenario_id"] in scenarios, "event_plan_branch_scenario")
                if branch["scenario_id"] not in scenarios:
                    continue
                condition = scenarios[branch["scenario_id"]]["definition_ref"]
                inputs_by_position = branch["comparison_inputs_by_position"]
                first = inputs_by_position["flat"]
                target, domain = first["target"], branch["applicability_domain"]
                check(
                    target["anchor"] in {"scheduled_pre_release", "post_release_response"},
                    "event_plan_anchor",
                )
                offset = -60 if target["anchor"] == "scheduled_pre_release" else 60
                start = stamp(event["scheduled_at"]) + timedelta(seconds=offset)
                check(stamp(target["window_start"]) == start, "event_plan_start")
                check(
                    start
                    <= stamp(branch["applicable_from"])
                    < stamp(branch["valid_until"])
                    <= stamp(domain["valid_until"])
                    <= stamp(target["window_end"]),
                    "event_plan_expiry",
                )
                check(
                    Decimal(domain["price_ratio_min"]) <= Decimal(domain["price_ratio_max"])
                    and (not domain["maximum_elapsed_seconds"] or domain["recomparison_allowed"]),
                    "event_plan_bridge",
                )
                for position, inputs in inputs_by_position.items():
                    check(
                        inputs["target"] == target
                        and inputs["position_branch"] == position
                        and inputs["forecast_as_of"] == value["as_of"]
                        and inputs["comparison_as_of"] == value["as_of"]
                        and target["conditioning_ref"] == condition
                        and inputs["instrument_id"] == context["instrument_id"]
                        and inputs["scope_id"] == context["scope_id"]
                        and target["horizon"] == context["horizon"],
                        "event_plan_inputs",
                    )
                    market, paths = inputs["market_distribution"], inputs["path_distribution"]
                    check(
                        all(row["conditioning_ref"] == condition for row in [market, paths])
                        and market["market_target_ref"] == inputs["bindings"]["target"]
                        and paths["market_target_ref"] == inputs["bindings"]["target"],
                        "event_plan_condition",
                    )
                    if paths["supports_market_direction"]:
                        with localcontext() as decimal_context:
                            decimal_context.prec = 100
                            grouped = dict.fromkeys(("up", "neutral", "down"), Decimal(0))
                            threshold = Decimal(target["threshold_fraction"])
                            for atom in paths["scenarios"]:
                                terminal = Decimal(atom["terminal_return_fraction"])
                                label = (
                                    "up"
                                    if terminal > threshold
                                    else "down"
                                    if terminal < -threshold
                                    else "neutral"
                                )
                                grouped[label] += Decimal(atom["probability"])
                            q = market["direction_distribution"]["probabilities"]
                            check(
                                q is not None
                                and grouped
                                == {row["outcome_id"]: Decimal(row["value"]) for row in q},
                                "event_plan_q_grouping",
                            )
            if kind == "event-scenario-action-plan-draft.v1":
                check(
                    (value["marginal_target"] is None) == (value["marginal_target_ref"] is None),
                    "event_plan_marginal_pair",
                )
        if kind == "event-scenario-review-scope.v1":
            check(
                stamp(value["reviewed_at"]) < stamp(value["freeze_before"])
                and stamp(value["reviewed_at"]) < stamp(value["valid_until"]),
                "event_review_clock",
            )
        if kind == "event-scenario-realization.v1":
            check(
                stamp(value["released_at"])
                <= stamp(value["public_at"])
                <= stamp(value["received_at"])
                <= stamp(value["known_at"]),
                "event_realization_clock",
            )
        if kind == "scenario-branch-selection.v1":
            selected = value["status"] == "selected_preapproved_branch"
            check(
                selected
                == all(
                    value[key] is not None
                    for key in (
                        "branch_ref",
                        "selected_candidate_ref",
                        "selected_comparison_ref",
                        "selected_action_id",
                    )
                ),
                "event_selection_refs",
            )
            check(
                selected
                or all(
                    value[key] is None
                    for key in (
                        "selected_candidate_ref",
                        "selected_comparison_ref",
                        "selected_action_id",
                        "unit_expected_net_if_assumed_fills_usdt",
                        "recomparison",
                    )
                ),
                "event_selection_unknown",
            )
        if kind in {"perpetual-paper-account-create.v1", "perpetual-paper-account-summary.v1"}:
            references = value["instrument_refs"]
            if isinstance(references, dict):
                references = list(references.values())
            check(
                len({(r["ref"], r["hash"]) for r in references}) == len(references),
                "paper_instruments",
            )
        if kind == "perpetual-paper-account-create-receipt.v1":
            check(value["owner_id"] == value["account"]["owner_id"], "paper_receipt_owner")
        if kind == "perpetual-paper-account-list.v1":
            check(
                all(row["owner_id"] == value["owner_id"] for row in value["accounts"])
                and len({row["account_id"] for row in value["accounts"]}) == len(value["accounts"]),
                "paper_list_partition",
            )
        if {"owner_id", "scope_id", "settings_ref", "profile_ref"} <= value.keys():
            check(
                value["scope_id"] == "crypto:perp:" + value["instrument_id"] + ":trade:1m", "scope"
            )
        if kind == "action-candidate.v2":
            action, side, branch = value["action"], value["target_side"], value["position_branch"]
            before, after = value["quantity_before"], value["quantity_after"]
            check((before is None) == (after is None), "quantity_pair")
            if value["size_mode"] == "unit_notional":
                check(value["unit_notional_usdt"] is not None and before is None, "unit_size")
            else:
                check(value["unit_notional_usdt"] is None and before is not None, "contract_size")
            if action == "conditional":
                check(
                    value["nested_action_ref"] is not None and bool(value["condition_refs"]),
                    "conditional_child",
                )
            else:
                check(value["nested_action_ref"] is None, "nonconditional_child")
                check(
                    (action == "open" and branch == "flat" and side in {"long", "short"})
                    or (
                        action in {"add", "reduce"}
                        and branch in {"long", "short"}
                        and side == branch
                    )
                    or (action == "close" and branch in {"long", "short"} and side == "flat")
                    or (action == "no_change" and side == branch),
                    "action_side",
                )
            if before is not None and after is not None:
                before, after = Decimal(before), Decimal(after)
                branch_side = "flat" if before == 0 else "long" if before > 0 else "short"
                target_side = "flat" if after == 0 else "long" if after > 0 else "short"
                check(branch_side == branch and target_side == side, "signed_side")
                if action != "conditional":
                    check(
                        (action == "open" and before == 0 and after != 0)
                        or (action == "add" and before * after > 0 and abs(after) > abs(before))
                        or (action == "reduce" and before * after > 0 and abs(after) < abs(before))
                        or (action == "close" and before != 0 and after == 0)
                        or (action == "no_change" and before == after),
                        "quantity_transition",
                    )
            if any(
                value[key] is None for key in ("cost_ref", "funding_ref", "margin_ref", "fill_ref")
            ):
                check(bool(value["unknown_reasons"]), "unknown_inputs")
        if kind == "action-candidate-set.v2":
            rows, refs = value["candidates"], value["candidate_refs"]
            check(stamp(value["as_of"]) < stamp(value["valid_until"]), "set_expiry")
            check(
                len(rows) == len(refs)
                and len({row["candidate_id"] for row in rows}) == len(rows)
                and len({(r["ref"], r["hash"]) for r in refs}) == len(refs),
                "set_members",
            )
            for row in rows:
                check(row["context"] == value["context"], "set_context")
                check(
                    stamp(value["as_of"])
                    < stamp(row["valid_until"])
                    <= stamp(value["valid_until"]),
                    "member_expiry",
                )
                if row["action"] == "conditional":
                    children = [
                        child
                        for child, reference in zip(rows, refs, strict=False)
                        if reference == row["nested_action_ref"]
                    ]
                    check(
                        len(children) == 1
                        and children[0]["action"] != "conditional"
                        and children[0]["candidate_id"] != row["candidate_id"],
                        "set_conditional_child",
                    )
                    if len(children) == 1:
                        child = children[0]
                        check(
                            all(
                                row[key] == child[key]
                                for key in [
                                    "context",
                                    "target_side",
                                    "position_branch",
                                    "quantity_before",
                                    "quantity_after",
                                    "size_policy_ref",
                                    "size_mode",
                                    "unit_notional_usdt",
                                    "exit_ref",
                                    "cost_ref",
                                    "funding_ref",
                                    "margin_ref",
                                    "fill_ref",
                                    "target_refs",
                                ]
                            ),
                            "child_intent",
                        )
        if kind in {
            "publication-anchor-rule.v1",
            "pending-publication-target.v1",
            "publication-bound-target.v1",
        }:
            check(
                value["duration_seconds"]
                == {"4h": 14400, "24h": 86400, "72h": 259200}[value["horizon"]],
                "rule_duration",
            )
        if kind == "publication-bound-target.v1":
            assigned, start, end = (
                stamp(value[key]) for key in ("assigned_at", "window_start", "window_end")
            )
            check(
                start == assigned.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "target_strict_minute",
            )
            check(end - start == timedelta(seconds=value["duration_seconds"]), "target_duration")
        if kind == "publication-anchor-receipt.v1":
            assigned, start, end = (
                stamp(value[key]) for key in ("assigned_at", "target_start", "target_end")
            )
            check(
                start == assigned.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "strict_minute",
            )
            check(end - start == timedelta(seconds=value["duration_seconds"]), "receipt_duration")
        if kind == "action-review-authorization.v2":
            check(stamp(value["reviewed_at"]) < stamp(value["valid_until"]), "review_expiry")
        if kind == "action-comparison-allowed-domain.v1":
            rows = value["variables"]
            check(
                len({(row["category"], row["name"]) for row in rows}) == len(rows),
                "domain_variables",
            )
            for row in rows:
                check(stamp(row["valid_until"]) <= stamp(value["valid_until"]), "domain_expiry")
                check(
                    row["minimum"] is None
                    or row["maximum"] is None
                    or Decimal(row["minimum"]) <= Decimal(row["maximum"]),
                    "domain_bounds",
                )
        if kind == "decision-causal-analysis.v1":
            links = value["main_chain"] + value["opposite_chain"]
            check(len({row["link_id"] for row in links}) == len(links), "analysis_link_ids")
            check(
                all(row["evidence_refs"] for row in links) or bool(value["unresolved_questions"]),
                "analysis_unknown_explicit",
            )
        if kind == "research-decision-synthesis.v2":
            candidates, rule, market, target = (
                value["candidate_set"],
                value["anchor_rule"],
                value["market_distribution"],
                value["candidate_target"],
            )
            check(
                value["analysis"]["candidate_target_ref"] == candidates["candidate_target_ref"],
                "analysis_target",
            )
            check(
                value["as_of"] == candidates["as_of"]
                and stamp(value["known_at_max"]) <= stamp(value["as_of"]),
                "synthesis_cutoff",
            )
            check(
                candidates["candidate_target_ref"] == rule["candidate_target_ref"]
                and candidates["context"]["horizon"] == rule["horizon"]
                and stamp(candidates["valid_until"]) <= stamp(rule["valid_until"]),
                "synthesis_rule",
            )
            check(
                all(
                    target[key] == rule[key]
                    for key in [
                        "horizon",
                        "duration_seconds",
                        "grid_seconds",
                        "clock_rule",
                        "price_basis",
                        "threshold_fraction",
                        "method_ref",
                        "clock_policy_ref",
                        "valid_until",
                        "invalidation_refs",
                    ]
                )
                and target["instrument_ref"] == candidates["context"]["instrument_ref"]
                and target["conditioning_ref"] == market["conditioning_ref"],
                "synthesis_target",
            )
            check(
                market["market_target_ref"] == candidates["candidate_target_ref"]
                and market["instrument_ref"] == candidates["context"]["instrument_ref"]
                and (
                    market["event_scenario_ref"] is None
                    or market["event_scenario_ref"] == value["event_probability_ref"]
                ),
                "synthesis_probability",
            )
            check(
                all(
                    row["as_of"] == value["as_of"]
                    for row in [
                        market["direction_distribution"],
                        value["event_probability"]["occurrence_distribution"],
                        value["event_probability"]["outcome_given_occurrence"],
                    ]
                ),
                "synthesis_probability_cutoff",
            )
            paths = value["path_distribution"]
            check(
                paths["market_target_ref"] == candidates["candidate_target_ref"]
                and paths["conditioning_ref"] == target["conditioning_ref"],
                "synthesis_paths_scope",
            )
            if paths["supports_market_direction"]:
                grouped = dict.fromkeys(("up", "neutral", "down"), Decimal(0))
                threshold = Decimal(target["threshold_fraction"])
                for scenario in paths["scenarios"]:
                    terminal = Decimal(scenario["terminal_return_fraction"])
                    label = (
                        "up"
                        if terminal > threshold
                        else "down"
                        if terminal < -threshold
                        else "neutral"
                    )
                    grouped[label] += Decimal(scenario["probability"])
                probabilities = market["direction_distribution"]["probabilities"]
                check(
                    probabilities is not None
                    and grouped
                    == {row["outcome_id"]: Decimal(row["value"]) for row in probabilities},
                    "synthesis_paths_direction",
                )
        if kind == "action-review-request.v2":
            synth, domain = value["synthesis"], value["allowed_domain"]
            check(value["allowed_domain_ref"] == synth["allowed_domain_ref"], "request_domain")
            check(
                all(
                    domain[key] == synth[key]
                    for key in [
                        "candidate_set_ref",
                        "event_probability_ref",
                        "market_distribution_ref",
                        "method_ref",
                        "method_code_hash",
                    ]
                )
                and domain["candidate_target_ref"] == synth["candidate_set"]["candidate_target_ref"]
                and domain["anchor_rule_ref"] == synth["candidate_set"]["anchor_rule_ref"],
                "request_bindings",
            )
            check(
                stamp(synth["as_of"]) <= stamp(value["requested_at"]) < stamp(domain["valid_until"])
                and stamp(value["requested_at"]) < stamp(synth["candidate_set"]["valid_until"]),
                "request_expiry",
            )
        if kind in {"decision-snapshot.v2", "decision-action-plan-revision.v2"}:
            context, bindings = value["context"], value["bindings"]
            check(
                value["scope_id"] == context["scope_id"] and value["horizon"] == context["horizon"],
                "publication_scope",
            )
            check(
                all(
                    bindings[key] == context[key + "_ref"]
                    for key in ["instrument", "settings", "account", "position", "utility"]
                ),
                "publication_context",
            )
            if kind == "decision-snapshot.v2":
                check(
                    stamp(value["decision_cutoff_at"]) <= stamp(value["known_at"]), "snapshot_clock"
                )
            else:
                candidate = value["selected_candidate"]
                check(candidate["context"] == context, "revision_candidate")
                check(
                    value["position_context"] == "unknown"
                    or value["position_context"] == candidate["position_branch"],
                    "revision_position",
                )
                check(
                    (value["revision_no"] == 1) == (value["previous_id"] is None), "revision_parent"
                )
                check(
                    stamp(value["as_of"])
                    <= stamp(value["published_at"])
                    < stamp(value["valid_until"])
                    <= stamp(candidate["valid_until"]),
                    "revision_clock",
                )
                check(
                    bindings["final_target"]
                    == {"ref": value["target_spec_ref"], "hash": value["target_spec_hash"]},
                    "revision_target",
                )
        for child in value.values():
            visit(child)

    visit(raw)
    return tuple(issues)
