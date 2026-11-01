"""Action contracts reject malformed or misleading wire data, without live IO."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionOutcome,
    ActionPlanHead,
    ActionPlanRevision,
    Condition,
    ConditionEvaluation,
    DecisionSnapshot,
    Opportunity,
    Probability,
    probability_from_reference,
)

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"
HASH = "a" * 64
ASOF = "2026-10-13T00:00:00Z"
UNTIL = "2026-10-14T00:00:00Z"


def comparison():
    return {
        "op": "compare",
        "feature": "close",
        "scope_id": SCOPE,
        "unit": "USDT/BTC",
        "cmp": "gte",
        "threshold": "85000",
        "max_age_seconds": 120,
    }


def condition():
    return {
        "id": "entry-1",
        "purpose": "entry",
        "expression": comparison(),
        "min_fresh_samples": 2,
        "max_age_seconds": 120,
        "evaluation_seconds": 60,
        "calendar_ref": "crypto-24x7.v1",
        "valid_until": UNTIL,
        "original_text": "Wait for two confirmed observations",
        "input_requirements": [
            {"metric": "close", "scope": SCOPE, "source": "okx", "unit": "USDT/BTC"}
        ],
    }


def plan():
    return {
        "schema_version": "decision-action-plan-revision.v1",
        "revision_id": "revision-1",
        "plan_id": "plan-1",
        "decision_id": "decision-1",
        "opportunity_id": "opportunity-1",
        "revision_no": 1,
        "previous_id": None,
        "scope_id": SCOPE,
        "horizon": "24h",
        "as_of": ASOF,
        "published_at": "2026-10-13T00:01:00Z",
        "valid_until": UNTIL,
        "action": "enter_long",
        "position_context": "flat",
        "advice_qualification": "research_trial",
        "decision_status": "actionable",
        "entry": [condition()],
        "exit": [],
        "invalidation": [],
        "size_policy": {"kind": "fixed_scenario", "amount": "2500", "unit": "USDT"},
        "cost_policy_ref": "cost-v1",
        "cost_policy_hash": HASH,
        "target_spec_ref": "target-v1",
        "target_spec_hash": HASH,
        "basis_refs": [{"ref": "evidence-1", "hash": HASH}],
        "reason": "Conditional research trial with exit constraints",
        "payload_hash": HASH,
    }


def probability():
    return {
        "target_id": "target-return-positive-24h",
        "value": 0.7,
        "kind": "subjective",
        "provenance_ref": None,
    }


def test_valid_plan_is_closed_and_roundtrips():
    row = ActionPlanRevision.model_validate(plan())
    assert row.model_dump(mode="json")["action"] == "enter_long"
    assert row.as_of.tzinfo is UTC
    assert ActionPlanRevision.model_validate_json(row.model_dump_json()) == row
    with pytest.raises(ValidationError):
        row.action = "wait"


@pytest.mark.parametrize(
    "model,body",
    [
        (
            Opportunity,
            {
                "schema_version": "decision-opportunity.v1",
                "id": "opp-1",
                "key": "BTC:24h:2026-10-13T00",
                "scope_id": SCOPE,
                "horizon": "24h",
                "kind": "scheduled",
                "event_ref": None,
                "registered_at": ASOF,
                "time_start": ASOF,
                "time_end": UNTIL,
                "target_spec_hash": HASH,
                "recipe_hash": HASH,
                "cohort": "prospective",
                "status": "open",
                "reason": None,
            },
        ),
        (
            DecisionSnapshot,
            {
                "schema_version": "decision-snapshot.v1",
                "id": "d1",
                "opportunity_id": "opp-1",
                "run_id": "run-1",
                "source_result_ref": "raw-result",
                "source_result_hash": HASH,
                "decision_cutoff_at": ASOF,
                "known_at": ASOF,
                "scope_id": SCOPE,
                "horizon": "24h",
                "hypotheses": [
                    {
                        "direction": "range",
                        "evidence_refs": ["fact-1"],
                        "counter_refs": ["fact-2"],
                        "rationale": "Conditional range scenario",
                    }
                ],
                "factor_refs": [
                    {"ref": "fact-1", "hash": HASH, "unit": "USDT/BTC", "known_at": ASOF}
                ],
                "direction_status": "eligible",
                "path_status": "experimental",
                "probability": {
                    "target_id": "target",
                    "value": None,
                    "kind": "unavailable",
                    "provenance_ref": None,
                },
                "gaps": [],
                "skill_refs": [],
                "snapshot_hash": HASH,
            },
        ),
        (
            ConditionEvaluation,
            {
                "schema_version": "decision-condition-evaluation.v1",
                "id": "evaluation-1",
                "plan_id": "plan-1",
                "revision_id": "rev-1",
                "tick_at": ASOF,
                "as_of": ASOF,
                "evaluated_at": ASOF,
                "lease_generation": 1,
                "input_ref": "input-1",
                "input_hash": HASH,
                "truth": "unknown",
                "status": "ok",
                "per_condition": [
                    {
                        "id": "entry-1",
                        "truth": "unknown",
                        "status": "ok",
                        "reason": "missing",
                        "input_refs": [],
                    }
                ],
                "state_before": "armed",
                "state_after": "armed",
                "evaluation_hash": HASH,
            },
        ),
        (
            ActionOutcome,
            {
                "schema_version": "decision-action-outcome.v1",
                "id": "outcome-1",
                "opportunity_id": "opp-1",
                "plan_id": "plan-1",
                "revision_id": "rev-1",
                "mode": "paper",
                "status": "pending",
                "version": 1,
                "due_at": UNTIL,
                "protocol_ref": "protocol-1",
                "protocol_hash": HASH,
                "visible_at": None,
                "trigger_at": None,
                "entry_at": None,
                "exit_at": None,
                "price_basis": "bid_ask",
                "entry": None,
                "exit": None,
                "gross_pnl": None,
                "cost": None,
                "net_pnl": None,
                "mae": None,
                "coverage": "0",
                "price_refs": [],
                "baseline_outcome_ref": None,
                "exclusions": [],
                "settled_at": None,
                "payload_hash": HASH,
            },
        ),
        (
            ActionPlanHead,
            {
                "schema_version": "decision-action-plan-head.v1",
                "plan_id": "plan-1",
                "current_revision_id": "rev-1",
                "state_version": 1,
                "state": "armed",
                "monitor_state": "active",
                "next_due": ASOF,
                "valid_until": UNTIL,
                "lease_owner": None,
                "lease_generation": 0,
                "lease_expires_at": None,
                "last_input_watermark": None,
                "enter_streak": 0,
            },
        ),
    ],
)
def test_remaining_public_dtos_roundtrip_and_reject_additional_fields(model, body):
    row = model.model_validate(body)
    assert model.model_validate_json(row.model_dump_json()) == row
    with pytest.raises(ValidationError):
        model.model_validate({**body, "uncontracted_field": "must not disappear"})


@pytest.mark.parametrize("field", ["revision_id", "plan_id", "decision_id", "opportunity_id"])
def test_entity_ids_match_128_character_storage_and_outbox_boundary(field):
    raw = plan()
    raw[field] = "x" * 128
    ActionPlanRevision.model_validate(raw)
    raw[field] += "x"
    with pytest.raises(ValidationError):
        ActionPlanRevision.model_validate(raw)


def test_scope_length_matches_persistent_192_character_boundary():
    raw = plan()
    raw["scope_id"] = "x" * 193
    with pytest.raises(ValidationError):
        ActionPlanRevision.model_validate(raw)


def test_outcome_version_due_at_are_required_and_affect_serialized_payload():
    raw = {
        "schema_version": "decision-action-outcome.v1",
        "id": "o1",
        "opportunity_id": "opp",
        "plan_id": "plan",
        "revision_id": "rev",
        "mode": "paper",
        "status": "pending",
        "version": 1,
        "due_at": UNTIL,
        "protocol_ref": "protocol-1",
        "protocol_hash": HASH,
        "visible_at": None,
        "trigger_at": None,
        "entry_at": None,
        "exit_at": None,
        "price_basis": "bid_ask",
        "entry": None,
        "exit": None,
        "gross_pnl": None,
        "cost": None,
        "net_pnl": None,
        "mae": None,
        "coverage": "0",
        "price_refs": [],
        "baseline_outcome_ref": None,
        "exclusions": [],
        "settled_at": None,
        "payload_hash": HASH,
    }
    original = ActionOutcome.model_validate(raw).model_dump(mode="json")
    assert original["version"] == 1 and original["due_at"] == UNTIL
    for field in ("version", "due_at"):
        invalid = {key: value for key, value in raw.items() if key != field}
        with pytest.raises(ValidationError):
            ActionOutcome.model_validate(invalid)
    for changes in ({"version": 0}, {"version": True}, {"due_at": "2026-10-14T00:00:00"}):
        with pytest.raises(ValidationError):
            ActionOutcome.model_validate({**raw, **changes})
    revised = ActionOutcome.model_validate({**raw, "version": 2}).model_dump(mode="json")
    assert revised != original


def test_head_watermark_is_an_aware_instant_and_lease_is_a_pair():
    raw = {
        "schema_version": "decision-action-plan-head.v1",
        "plan_id": "plan",
        "current_revision_id": "rev",
        "state_version": 1,
        "state": "armed",
        "monitor_state": "active",
        "next_due": ASOF,
        "valid_until": UNTIL,
        "lease_owner": None,
        "lease_generation": 0,
        "lease_expires_at": None,
        "last_input_watermark": ASOF,
        "enter_streak": 0,
    }
    assert ActionPlanHead.model_validate(raw).last_input_watermark.tzinfo is UTC
    for changes in ({"last_input_watermark": "input-id"}, {"lease_owner": "worker-1"}):
        with pytest.raises(ValidationError):
            ActionPlanHead.model_validate({**raw, **changes})


@pytest.mark.parametrize(
    "field,value",
    [
        ("unexpected", True),
        ("position_context", "long"),
        ("revision_no", True),
        ("horizon", "7d"),
        ("as_of", "2026-10-13T00:00:00"),
        ("as_of", "2026-02-30T00:00:00Z"),
        ("previous_id", "rev0"),
        ("valid_until", ASOF),
        ("cost_policy_hash", "A" * 64),
    ],
)
def test_invalid_plan_shape_or_relation(field, value):
    raw = plan()
    raw[field] = value
    with pytest.raises(ValidationError):
        ActionPlanRevision.model_validate(raw)


@pytest.mark.parametrize(
    "action,position",
    [("enter_long", "unknown"), ("reduce_long", "flat"), ("exit_long", "unknown")],
)
def test_action_cannot_invent_position(action, position):
    raw = plan()
    raw.update(action=action, position_context=position)
    with pytest.raises(ValidationError):
        ActionPlanRevision.model_validate(raw)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), True, "0.7", -0.1, 1.1]
)
def test_probability_rejects_nonfinite_coerced_or_outside_range(value):
    raw = probability()
    raw["value"] = value
    with pytest.raises(ValidationError):
        Probability.model_validate(raw)


@pytest.mark.parametrize(
    "kind,value,reference",
    [
        ("unavailable", 0.5, None),
        ("subjective", None, None),
        ("subjective", 0.7, "invented"),
        ("calibrated", 0.7, None),
    ],
)
def test_probability_provenance_binding(kind, value, reference):
    with pytest.raises(ValidationError):
        Probability.model_validate(
            {"target_id": "target", "kind": kind, "value": value, "provenance_ref": reference}
        )


def test_probability_reference_adapter_is_explicit_and_preserves_unknown():
    unknown = probability_from_reference(
        {
            "kind": "unavailable",
            "target": "return_positive",
            "value": None,
            "calibration_ref": None,
        },
        target_id="registered-target-1",
    )
    assert unknown.value is None
    assert unknown.provenance_ref is None
    calibrated = probability_from_reference(
        {
            "kind": "calibrated",
            "target": "target_before_stop",
            "value": 0.7,
            "calibration_ref": "calibration-7",
        },
        target_id="registered-target-2",
    )
    assert calibrated.provenance_ref == "calibration-7"


@pytest.mark.parametrize("threshold", [1, "01", "1.0", "-0", "NaN", "1e3", "-Infinity"])
def test_decimal_wire_is_canonical_string(threshold):
    raw = condition()
    raw["expression"]["threshold"] = threshold
    with pytest.raises(ValidationError):
        Condition.model_validate(raw)


def test_ast_depth_eight_allowed_nine_rejected_and_group_bound_enforced():
    raw = condition()
    for _ in range(7):
        raw["expression"] = {"op": "all", "args": [raw["expression"]]}
    Condition.model_validate(raw)
    raw["expression"] = {"op": "any", "args": [raw["expression"]]}
    with pytest.raises(ValidationError):
        Condition.model_validate(raw)
    raw = condition()
    raw["expression"] = {"op": "all", "args": [comparison() for _ in range(17)]}
    with pytest.raises(ValidationError):
        Condition.model_validate(raw)


def test_aware_offsets_normalize_utc_and_naive_python_datetime_rejected():
    raw = plan()
    raw["as_of"] = "2026-10-13T08:00:00+08:00"
    assert ActionPlanRevision.model_validate(raw).as_of == datetime(2026, 10, 13, tzinfo=UTC)
    raw["as_of"] = datetime(2026, 10, 13)
    with pytest.raises(ValidationError):
        ActionPlanRevision.model_validate(raw)


def test_condition_binding_cannot_hide_wrong_purpose_or_duplicate_identity():
    raw = plan()
    raw["exit"] = [copy.deepcopy(raw["entry"][0])]
    with pytest.raises(ValidationError, match="decision_condition_duplicate_id"):
        ActionPlanRevision.model_validate(raw)
    raw["exit"][0]["id"] = "different-id"
    with pytest.raises(ValidationError, match="decision_condition_binding_invalid"):
        ActionPlanRevision.model_validate(raw)


def test_generated_zod_checks_probability_position_time_and_bounded_ast(tmp_path: Path):
    """Runs local generated validators; does not build or deploy the product."""
    node = shutil.which("node")
    assert node, "Existing node required; do not silently skip"
    corpus = tmp_path / "action-corpus.json"
    corpus.write_text(
        json.dumps({"plan": plan(), "probability": probability(), "condition": condition()}),
        encoding="utf-8",
    )
    runner = tmp_path / "action-conformance.ts"
    output = tmp_path / "action-conformance.cjs"
    generated = (ROOT / "packages/contracts_ts/src/generated/r2.ts").as_posix()
    runner.write_text(
        "import { readFileSync } from 'node:fs'\n"
        "import { decisionActionPlanRevisionV1Schema as planSchema, "
        "decisionProbabilityV1Schema as probabilitySchema, "
        f"decisionConditionV1Schema as conditionSchema }} from {json.dumps(generated)}\n"
        f"const corpus = JSON.parse(readFileSync({json.dumps(str(corpus))}, 'utf8'))\n"
        "let count = 0; const check = (schema, raw, expected) => { count++; "
        "if (schema.safeParse(raw).success !== expected) "
        "throw new Error('case '+count+' mismatch'); };\n"
        "check(planSchema, corpus.plan, true); "
        "check(probabilitySchema, corpus.probability, true);\n"
        "check(planSchema, {...corpus.plan, position_context:'long'}, false);\n"
        "check(planSchema, {...corpus.plan, as_of:'2026-10-13T00:00:00'}, false);\n"
        "check(planSchema, {...corpus.plan, as_of:'2026-02-30T00:00:00Z'}, false);\n"
        "check(planSchema, {...corpus.plan, extra:true}, false);\n"
        "check(planSchema, {...corpus.plan, revision_id:'x'.repeat(129)}, false);\n"
        "check(planSchema, {...corpus.plan, scope_id:'x'.repeat(193)}, false);\n"
        "for(const value of [NaN, Infinity, -Infinity, true, '0.7']) "
        "check(probabilitySchema, {...corpus.probability,value}, false);\n"
        "check(probabilitySchema, {...corpus.probability,kind:'unavailable',value:0.5}, false);\n"
        "check(probabilitySchema, {...corpus.probability,kind:'calibrated'}, false);\n"
        "check(probabilitySchema, {...corpus.probability,kind:'unavailable',value:null}, true);\n"
        "let expression=corpus.condition.expression; "
        "for(let i=0;i<7;i++) expression={op:'all',args:[expression]};\n"
        "check(conditionSchema, {...corpus.condition,expression}, true);\n"
        "check(conditionSchema, "
        "{...corpus.condition,expression:{op:'all',args:[expression]}}, false);\n"
        "console.log(JSON.stringify({count,passed:true}));\n",
        encoding="utf-8",
    )
    bootstrap = (
        "const path=require('node:path'); const [desk,entry,output]=process.argv.slice(1); "
        "const vite=require.resolve('vite/package.json',{paths:[desk]}); "
        "const esbuild=require(require.resolve('esbuild',{paths:[path.dirname(vite)]})); "
        "esbuild.buildSync({entryPoints:[entry],outfile:output,bundle:true,platform:'node',"
        "format:'cjs',target:'node18',logLevel:'error'}); require(output);"
    )
    result = subprocess.run(
        [node, "-e", bootstrap, str(ROOT / "apps/decision-desk"), str(runner), str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"count": 18, "passed": True}
