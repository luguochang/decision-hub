"""Frozen V2 candidate/reviewer boundaries; synthetic references grant no authority."""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from runpy import run_path
import hashlib
import json

import pytest
from pydantic import ValidationError

DTO = "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2"


def ref(name):
    return {"ref": "fixture:" + name, "hash": "a" * 64}


def body_ref(name, body):
    return {"ref": "fixture:" + name, "hash": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()}


def context():
    return dict(owner_id="owner", account_kind="user_declared", account_id="unknown-account",
                instrument_id="okx:BTC-USDT-SWAP", scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                profile_ref=ref("paper-profile"), horizon="4h", instrument_ref=ref("instrument"),
                settings_ref=ref("settings"), account_ref=None, position_ref=None,
                utility_ref=ref("utility"))


def candidate(candidate_id="open_short", action="open", side="short", branch="flat"):
    return dict(schema_version="action-candidate.v2", candidate_id=candidate_id, context=context(),
                action=action, target_side=side, position_branch=branch,
                quantity_before=None, quantity_after=None, condition_refs=[], nested_action_ref=None,
                size_policy_ref=ref("unit-policy"), size_mode="unit_notional", unit_notional_usdt="100",
                exit_ref=ref("exit"), valid_until="2026-01-01T01:00:00Z", cost_ref=ref("cost"),
                funding_ref=None, margin_ref=None, fill_ref=None,
                target_refs=[ref("action-target-" + candidate_id)], basis_refs=[ref("assumptions")],
                unknown_reasons=["account_size_unknown", "fill_model_unavailable"], execution="paper_only")


def candidate_set():
    rows = [candidate("open_long", side="long"), candidate(),
            candidate("no_change", action="no_change", side="flat")]
    return dict(schema_version="action-candidate-set.v2", candidate_set_id="set-1", context=context(),
                as_of="2026-01-01T00:00:00Z", valid_until="2026-01-01T01:00:00Z",
                candidate_target_ref=ref("candidate-target"), anchor_rule_ref=ref("anchor-rule"),
                candidates=rows, candidate_refs=[ref(row["candidate_id"]) for row in rows])


def anchor_rule():
    return dict(schema_version="publication-anchor-rule.v1", anchor="publication_bound",
                candidate_target_ref=ref("candidate-target"), horizon="4h", duration_seconds=14400,
                grid_seconds=60, clock_rule="strict_next_utc_minute", price_basis="trade_1m_close",
                threshold_fraction="0.005", method_ref=ref("forecast-method"),
                clock_policy_ref=ref("clock-policy"), valid_until="2026-01-01T01:00:00Z",
                invalidation_refs=[ref("probability-domain")])


def test_complete_unit_candidate_set_preserves_unknown_account_and_action_separation():
    contracts = import_module(DTO)
    assert hasattr(contracts, "ActionCandidateSetV2"), "missing full V2 candidate set"
    parsed = contracts.ActionCandidateSetV2.model_validate(candidate_set())
    assert parsed.model_dump(mode="json") == candidate_set()
    assert parsed.candidates[1].action == "open" and parsed.candidates[1].target_side == "short"
    assert parsed.candidates[1].quantity_after is None


@pytest.mark.parametrize("change", ["scope", "settings", "reversal", "negative_size", "target", "conditional", "duplicate"])
def test_candidate_set_rejects_cross_scope_illegal_action_and_ambiguous_targets(change):
    contracts = import_module(DTO)
    assert hasattr(contracts, "ActionCandidateSetV2"), "missing full V2 candidate set"
    value = candidate_set()
    row = value["candidates"][1]
    if change == "scope": row["context"]["instrument_id"] = "okx:ETH-USDT-SWAP"
    if change == "settings": row["context"]["settings_ref"] = ref("other-settings")
    if change == "reversal": row.update(action="reduce", position_branch="long", quantity_before="2", quantity_after="-1")
    if change == "negative_size": row["unit_notional_usdt"] = "-1"
    if change == "target": row["target_refs"] = []
    if change == "conditional": row.update(action="conditional", condition_refs=[ref("condition")], nested_action_ref=ref("missing-child"))
    if change == "duplicate": value["candidates"][1]["candidate_id"] = "open_long"
    with pytest.raises(ValidationError): contracts.ActionCandidateSetV2.model_validate(value)


def test_known_quantity_action_legality_and_conditional_child_are_explicit():
    contracts = import_module(DTO)
    assert hasattr(contracts, "ActionCandidateV2"), "missing V2 action legality"
    for action, side, branch, before, after in [
        ("open", "short", "flat", "0", "-2"), ("add", "long", "long", "2", "3"),
        ("reduce", "short", "short", "-2", "-1"), ("close", "flat", "long", "2", "0"),
        ("no_change", "long", "long", "2", "2"),
    ]:
        value = candidate(action=action, side=side, branch=branch)
        value.update(quantity_before=before, quantity_after=after, size_mode="contracts", unit_notional_usdt=None)
        assert contracts.ActionCandidateV2.model_validate(value).quantity_after == after
        broken = deepcopy(value); broken["quantity_after"] = "-3" if branch == "long" else "3"
        with pytest.raises(ValidationError): contracts.ActionCandidateV2.model_validate(broken)
    value = candidate_set()
    child = deepcopy(value["candidates"][1]); child.update(candidate_id="conditional-short", action="conditional",
        condition_refs=[ref("event-trigger")], nested_action_ref=value["candidate_refs"][1])
    value["candidates"].append(child); value["candidate_refs"].append(ref("conditional-short"))
    assert contracts.ActionCandidateSetV2.model_validate(value).candidates[-1].action == "conditional"


def test_review_binds_full_set_rule_and_expiry_and_rejects_v1_authorization():
    contracts = import_module(DTO)
    assert hasattr(contracts, "ActionReviewAuthorizationV2"), "missing independent V2 review authorization"
    value = dict(schema_version="action-review-authorization.v2", review_request_ref=ref("request"),
                 synthesis_ref=ref("synthesis"), candidate_set_ref=ref("set"),
                 candidate_target_ref=ref("candidate-target"), anchor_rule_ref=ref("anchor-rule"),
                 event_probability_ref=ref("event-probability"), market_distribution_ref=ref("market"),
                 method_ref=ref("comparison-method"), method_code_hash="b" * 64,
                 allowed_domain_ref=ref("allowed-domain"), reviewed_at="2026-01-01T00:20:00Z",
                 valid_until="2026-01-01T01:00:00Z", invalidation_refs=[ref("domain-invalid")],
                 reviewer_input_ref=ref("actual-input"),
                 verdict="approved")
    assert contracts.ActionReviewAuthorizationV2.model_validate(value).candidate_set_ref == contracts.DecisionSourceRefV1.model_validate(ref("set"))
    for field, wrong in [("schema_version", "research-mechanism-review.v1"), ("candidate_set_ref", None),
                         ("anchor_rule_ref", None), ("valid_until", "2026-01-01T00:19:59Z")]:
        broken = deepcopy(value); broken[field] = wrong
        with pytest.raises(ValidationError): contracts.ActionReviewAuthorizationV2.model_validate(broken)


def test_anchor_rule_is_reviewable_before_assigned_time_and_receipt_keeps_strict_grid():
    contracts = import_module(DTO)
    assert hasattr(contracts, "PublicationAnchorRuleV1"), "missing reviewable publication rule"
    rule = contracts.PublicationAnchorRuleV1.model_validate(anchor_rule())
    assert "assigned_at" not in rule.model_dump(mode="json")
    value = dict(schema_version="publication-anchor-receipt.v1", opportunity_id="opportunity", commit_id="commit",
                 candidate_target_ref=ref("candidate-target"), anchor_rule_ref=ref("anchor-rule"),
                 clock_policy_ref=ref("clock-policy"), assigned_at="2026-01-01T00:30:00Z",
                 target_start="2026-01-01T00:31:00Z", target_end="2026-01-01T04:31:00Z",
                 duration_seconds=14400, final_target_ref=ref("final-target"))
    assert contracts.PublicationAnchorReceiptV1.model_validate(value).target_start.minute == 31
    for start in ["2026-01-01T00:30:00Z", "2026-01-01T00:31:01Z", "2026-01-01T00:32:00Z"]:
        broken = deepcopy(value); broken["target_start"] = start
        with pytest.raises(ValidationError): contracts.PublicationAnchorReceiptV1.model_validate(broken)


def test_actual_comparison_candidates_can_be_represented_without_fake_account_size():
    contracts = import_module(DTO)
    assert hasattr(contracts, "ActionCandidateSetV2"), "missing full candidates for existing math"
    examples = run_path(str(Path(__file__).resolve().parents[1] / "decision_product/test_action_comparison.py"))
    result = examples["compare"](examples["fixture"]())
    rows = candidate_set()
    # Carry the actual frozen unit comparison identities into full intents.
    # This is a fixed-window representation check, not publication authority.
    for name in ("instrument", "settings", "utility", "account", "position"):
        rows["context"][name + "_ref"] = result["bindings"][name]
    rows["candidate_target_ref"] = result["target_ref"]
    result_by_id = {row["candidate_id"]: row for row in result["candidate_results"]}
    for row in rows["candidates"]:
        row["context"] = deepcopy(rows["context"])
        row["target_refs"] = [result_by_id[row["candidate_id"]]["action_target_ref"]]
    rows["candidate_refs"] = [body_ref(row["candidate_id"], row) for row in rows["candidates"]]
    parsed = contracts.ActionCandidateSetV2.model_validate(rows)
    by_id = {row.candidate_id: row for row in parsed.candidates}
    for row in result["candidate_results"]:
        assert (by_id[row["candidate_id"]].action, by_id[row["candidate_id"]].target_side) == (row["action"], row["target_side"])
        assert by_id[row["candidate_id"]].target_refs[0].model_dump(mode="json") == row["action_target_ref"]
    assert result["selected_action_id"] == "open_short"
    assert result["executable_contracts"] is None


def test_v2_selector_routes_only_from_frozen_schema_reference():
    port = import_module("packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol")
    assert port.synthesis_protocol("decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2") == "decision_v2"
    assert port.synthesis_protocol(port.DECISION_OUTPUT_SCHEMA_REF) == "decision"
    with pytest.raises(ValueError): port.synthesis_protocol("decision-product.v3")


def causal_analysis_for(synthesis):
    return dict(schema_version="decision-causal-analysis.v1", candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
        thesis="主观情景：买盘可能延续，但现有证据不足以确认未来方向。",
        main_chain=[dict(link_id="main", claim_type="inference", statement="已观察价格是历史信息，需求延续仍是待验证机制。",
            evidence_refs=synthesis["reasoning_refs"], confirmation="目标窗口出现持续成交确认后复核。", invalidation="价格与机制背离时撤销解释。")],
        opposite_chain=[dict(link_id="opposite", claim_type="scenario", statement="反向机制：流动性消退可能令走势逆转。",
            evidence_refs=[], confirmation="需要新增可核验的流动性证据。", invalidation="持续成交确认前不视为事实。")],
        unresolved_questions=["缺少流动性与未来成交证据；反向机制仍是假设。"])


def review_request():
    examples = run_path(str(Path(__file__).resolve().parents[1] / "decision_product/test_action_comparison.py"))
    math = examples["fixture"]()
    rule = anchor_rule()
    rows = candidate_set()
    target = dict(schema_version="pending-publication-target.v1", target_id="ordinary-4h", instrument_ref=rows["context"]["instrument_ref"],
        horizon="4h", duration_seconds=14400, anchor="publication_bound", grid_seconds=60, clock_rule="strict_next_utc_minute",
        clock_policy_ref=rule["clock_policy_ref"], price_basis="trade_1m_close", threshold_fraction="0.005", method_ref=rule["method_ref"],
        target_family_ref=ref("family"), label_rule_ref=ref("labels"), protocol_ref=ref("protocol"), conditioning_ref=math["market_distribution"]["conditioning_ref"],
        valid_until=rule["valid_until"], invalidation_refs=rule["invalidation_refs"])
    rule["candidate_target_ref"] = body_ref("candidate-target", target)
    rows["candidate_target_ref"] = rule["candidate_target_ref"]
    rows["anchor_rule_ref"] = body_ref("anchor-rule", rule)
    rows["candidate_refs"] = [body_ref(row["candidate_id"], row) for row in rows["candidates"]]
    event, market = math["event_probability"], math["market_distribution"]
    event_ref = body_ref("event", event)
    market["market_target_ref"] = rows["candidate_target_ref"]
    market["direction_distribution"]["target_ref"] = rows["candidate_target_ref"]
    market["instrument_ref"] = rows["context"]["instrument_ref"]
    market["event_scenario_ref"] = event_ref
    paths = deepcopy(math["path_distribution"])
    paths["market_target_ref"] = rows["candidate_target_ref"]
    market["path_distribution_ref"] = body_ref("paths", paths)
    market_ref = body_ref("market", market)
    set_ref = body_ref("set", rows)
    domain = dict(schema_version="action-comparison-allowed-domain.v1", candidate_set_ref=set_ref,
        candidate_target_ref=rows["candidate_target_ref"], anchor_rule_ref=rows["anchor_rule_ref"],
        event_probability_ref=event_ref, market_distribution_ref=market_ref,
        method_ref=ref("comparison-method"), method_code_hash="b" * 64, variables=[],
        invalidation_refs=[ref("domain-invalid")], valid_until=rows["valid_until"])
    domain_ref = body_ref("domain", domain)
    synth = dict(schema_version="research-decision-synthesis.v2", request_id="research-request:native-v2-run", opportunity_id="opportunity", as_of=rows["as_of"],
        known_at_max=rows["as_of"], candidate_set=rows, candidate_set_ref=set_ref, candidate_target=target, anchor_rule=rule,
        event_probability=event, event_probability_ref=event_ref, market_distribution=market,
        market_distribution_ref=market_ref, path_distribution=paths, economic_input_refs=[ref("cost"), market["path_distribution_ref"]],
        method_ref=ref("comparison-method"), method_code_hash="b" * 64, allowed_domain_ref=domain_ref,
        comparison_ref=None, target_binding="pending_anchor", reasoning_refs=[ref("analysis")], gaps=["account_unknown"])
    synth["analysis"] = causal_analysis_for(synth)
    return dict(schema_version="action-review-request.v2", synthesis_ref=body_ref("synth", synth),
                synthesis=synth, allowed_domain_ref=domain_ref, allowed_domain=domain,
                requested_at="2026-01-01T00:10:00Z")


def test_native_synthesis_requires_complete_assumed_paths_not_direction_only():
    contracts = import_module(DTO)
    value = review_request()["synthesis"]
    value.pop("path_distribution", None)
    with pytest.raises(ValueError):
        contracts.ResearchDecisionSynthesisV2.model_validate(value)


@pytest.mark.parametrize("change", ["target", "conditioning", "direction", "hash", "economic_ref"])
def test_native_assumed_paths_bind_amplitudes_scope_and_probability_partition(change):
    port = import_module("packages.kernel.decision_hub_kernel.ports.research_action_synthesis")
    value = review_request()["synthesis"]
    if change == "target":
        value["path_distribution"]["market_target_ref"] = ref("other-target")
    elif change == "conditioning":
        value["path_distribution"]["conditioning_ref"] = None
    elif change == "direction":
        value["path_distribution"]["scenarios"][0]["terminal_return_fraction"] = "-0.02"
    elif change == "hash":
        value["path_distribution"]["scenarios"][0]["terminal_return_fraction"] = "0.03"
    else:
        value["economic_input_refs"] = [ref("cost")]
    with pytest.raises(ValueError):
        port.normalize_action_synthesis(value)


def test_native_v2_normalizer_and_review_binding_keep_complete_packet_and_reject_tampering():
    port = import_module("packages.kernel.decision_hub_kernel.ports.research_action_synthesis")
    assert hasattr(port, "validate_action_review_binding_v2"), "missing full review packet binding"
    request = review_request()
    synth = request["synthesis"]
    original = deepcopy(synth)
    assert port.normalize_action_synthesis(synth).model_dump(mode="json") == original
    assert synth == original
    value = dict(schema_version="action-review-authorization.v2", review_request_ref=body_ref("request", request),
        synthesis_ref=request["synthesis_ref"], candidate_set_ref=synth["candidate_set_ref"],
        candidate_target_ref=synth["candidate_set"]["candidate_target_ref"], anchor_rule_ref=synth["candidate_set"]["anchor_rule_ref"],
        event_probability_ref=synth["event_probability_ref"], market_distribution_ref=synth["market_distribution_ref"],
        method_ref=synth["method_ref"], method_code_hash=synth["method_code_hash"], allowed_domain_ref=synth["allowed_domain_ref"],
        reviewed_at="2026-01-01T00:20:00Z", valid_until="2026-01-01T01:00:00Z",
        invalidation_refs=request["allowed_domain"]["invalidation_refs"],
        reviewer_input_ref=body_ref("request", request), verdict="approved")
    parsed = port.validate_action_review_binding_v2(request, value)
    assert parsed.candidate_set_ref.hash == synth["candidate_set_ref"]["hash"]
    for field in ["candidate_set_ref", "anchor_rule_ref", "event_probability_ref", "market_distribution_ref", "allowed_domain_ref"]:
        bad = deepcopy(value); bad[field] = ref("forged")
        with pytest.raises(ValueError): port.validate_action_review_binding_v2(request, bad)
    bad = deepcopy(request); bad["synthesis"]["candidate_set"]["candidates"][0]["unit_notional_usdt"] = "200"
    with pytest.raises(ValueError): port.validate_action_review_binding_v2(bad, value)


def test_publication_bound_target_has_acyclic_frozen_identity():
    contracts = import_module(DTO)
    assert hasattr(contracts, "PublicationBoundTargetV1"), "missing canonical final target identity"
    value = dict(schema_version="publication-bound-target.v1", candidate_target_ref=ref("candidate-target"),
        anchor_rule_ref=ref("anchor-rule"),
        assigned_at="2026-01-01T00:30:00Z", window_start="2026-01-01T00:31:00Z",
        window_end="2026-01-01T04:31:00Z", clock_policy_ref=ref("clock-policy"),
        instrument_ref=ref("instrument"), horizon="4h", duration_seconds=14400,
        price_basis="trade_1m_close", threshold_fraction="0.005", method_ref=ref("forecast-method"),
        target_family_ref=ref("family"), label_rule_ref=ref("labels"), protocol_ref=ref("protocol"), conditioning_ref=None)
    assert contracts.PublicationBoundTargetV1.model_validate(value).model_dump(mode="json") == value
    assert "anchor_receipt_ref" not in value
    bad = deepcopy(value); bad["duration_seconds"] = 86400
    with pytest.raises(ValidationError): contracts.PublicationBoundTargetV1.model_validate(bad)


def publication_bindings():
    context_value = context()
    result = {key: ref(key) for key in ["candidate_set", "comparison", "candidate_target", "final_target", "anchor_rule",
        "anchor_receipt", "frozen_forecast", "probability_commit", "event_probability", "market_distribution", "instrument",
        "settings", "utility", "review_authorization", "native_synthesis", "native_selection", "method", "allowed_domain"]}
    result.update(method_code_hash="b" * 64, account=context_value["account_ref"], position=context_value["position_ref"], derivation=None)
    return result


def publication_snapshot():
    return dict(schema_version="decision-snapshot.v2", id="decision", opportunity_id="opportunity", run_id="run",
        source_result_ref="full-action-bundle", source_result_hash="a" * 64, decision_cutoff_at="2026-01-01T00:00:00Z",
        known_at="2026-01-01T00:30:00Z", scope_id=context()["scope_id"], horizon="4h", context=context(),
        bindings=publication_bindings(), gaps=["account_unknown"], snapshot_hash="c" * 64)


def publication_revision():
    return dict(schema_version="decision-action-plan-revision.v2", revision_id="revision", plan_id="plan", decision_id="decision",
        opportunity_id="opportunity", revision_no=1, previous_id=None, scope_id=context()["scope_id"], horizon="4h",
        as_of="2026-01-01T00:00:00Z", published_at="2026-01-01T00:30:00Z", valid_until="2026-01-01T01:00:00Z",
        context=context(), position_context="unknown", selected_candidate=candidate(), selected_candidate_ref=ref("open_short"),
        bindings=publication_bindings(), target_spec_ref="fixture:final_target", target_spec_hash="a" * 64,
        advice_qualification="research_trial", execution="paper_only", reason="Explicit synthetic unit comparison", payload_hash="c" * 64)


def test_publication_dto_version_routing_rejects_cross_partition_and_target_revision():
    port = import_module("packages.kernel.decision_hub_kernel.ports.action_plan")
    assert hasattr(port, "parse_action_plan_revision"), "missing explicit V1/V2 persisted version routing"
    snapshot = publication_snapshot()
    revision = publication_revision()
    assert port.parse_decision_snapshot(snapshot).schema_version == "decision-snapshot.v2"
    assert port.parse_action_plan_revision(revision).selected_candidate.target_side == "short"
    for path, wrong in [(('context', 'account_id'), 'other-account'), (('bindings', 'settings'), ref('new-settings')),
                        (('target_spec_hash',), 'd' * 64), (('previous_id',), 'wrong-parent')]:
        bad = deepcopy(revision); cursor = bad
        for key in path[:-1]: cursor = cursor[key]
        cursor[path[-1]] = wrong
        with pytest.raises(ValidationError): port.parse_action_plan_revision(bad)
    for function, body in [(port.parse_decision_snapshot, snapshot), (port.parse_action_plan_revision, revision)]:
        with pytest.raises(ValueError, match="unsupported"): function({**body, "schema_version": "unexpected.v99"})


def test_python_typescript_shared_wire_acceptance_corpus():
    contracts = import_module(DTO)
    path = Path(__file__).resolve().parents[2] / "artifacts/d2-candidate-contract/wire-corpus.json"
    for entry in json.loads(path.read_text()):
        model = getattr(import_module(entry["module"]) if entry.get("module") else contracts, entry["python"])
        if entry["valid"]:
            assert model.model_validate(entry["value"]).model_dump(mode="json") == entry["value"], entry["name"]
        else:
            with pytest.raises(ValidationError): model.model_validate(entry["value"])


def paper_account_command():
    return dict(schema_version="perpetual-paper-account-create.v1", request_id="create-1", account_id="paper-1",
        instrument_refs=[ref("btc")], profile=ref("profile"), initial_cash_usdt="1000",
        simulation_acknowledged=True, execution_mode="manual_after_visible")


def paper_account_summary():
    return dict(schema_version="perpetual-paper-account-summary.v1", owner_id="owner", account_id="paper-1",
        profile=ref("profile"), instrument_refs={"okx:BTC-USDT-SWAP": ref("btc")}, state_version=1,
        revision=ref("account-1"), execution_mode="manual_after_visible", execution_enabled=False,
        created_at="2026-01-01T00:00:00Z")


def test_explicit_paper_account_creation_requires_ack_and_strict_owned_summary():
    contracts = import_module(DTO)
    assert hasattr(contracts, "CreatePerpetualPaperAccountV1"), "missing explicit paper account transport"
    command, summary = paper_account_command(), paper_account_summary()
    assert contracts.CreatePerpetualPaperAccountV1.model_validate(command).initial_cash_usdt == "1000"
    assert contracts.PaperAccountSummaryV1.model_validate(summary).execution_enabled is False
    for key, value in [("simulation_acknowledged", False), ("initial_cash_usdt", "0"), ("initial_cash_usdt", 1000),
                       ("owner_id", "untrusted-owner"), ("account_id", "x" * 129)]:
        with pytest.raises(ValidationError): contracts.CreatePerpetualPaperAccountV1.model_validate({**command, key: value})
    for instruments in [{}, {"okx:BTC-USDT": ref("spot")}, {"okx:BTC-USDT-SWAP": ref("btc"), "okx:ETH-USDT-SWAP": ref("btc")}]:
        with pytest.raises(ValidationError): contracts.PaperAccountSummaryV1.model_validate({**summary, "instrument_refs": instruments})
    receipt = dict(schema_version="perpetual-paper-account-create-receipt.v1", owner_id="owner", request_id="create-1", command_hash="a" * 64, account=summary)
    assert contracts.PaperAccountCreationReceiptV1.model_validate(receipt).account.account_id == "paper-1"
    with pytest.raises(ValidationError): contracts.PaperAccountCreationReceiptV1.model_validate({**receipt, "owner_id": "other-owner"})
