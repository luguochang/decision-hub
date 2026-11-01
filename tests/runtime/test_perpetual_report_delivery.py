"""Content completeness negatives against full original packet/manifest models."""

from datetime import UTC, datetime
from pathlib import Path
from runpy import run_path

import pytest

SUPPORT = run_path(str(Path(__file__).with_name("candidate_perpetual_report_support.py")))
packet = SUPPORT["packet"]
assess = SUPPORT["assess"]
event_packet = SUPPORT["event_packet"]
REPORT = SUPPORT["REPORT"]
MAIN = SUPPORT["MAIN"]
observation_sha = SUPPORT["observation_sha"]
observation_json = SUPPORT["observation_json"]


def test_full_report_can_complete_without_financial_publish():
    value = packet()
    value["binding"] = {
        **value["binding"],
        "schema_version": "perpetual-report-binding.v2",
        "report_delivery": assess(value),
    }
    content = REPORT.render_perpetual_report_content(
        **{
            key: value[key]
            for key in ("synthesis", "review_request", "review", "catalog", "binding")
        }
    )
    assert content.candidate["research_complete"] is True
    assert content.gate.status.value == "research_only"
    assert content.candidate["perpetual_synthesis"]["financial_qualified"] is False


def test_v6_packet_retains_the_full_reviewed_findings_body_for_hash_readback():
    value = packet()
    value["binding"] = {
        **value["binding"],
        "schema_version": "perpetual-report-binding.v2",
        "report_delivery": assess(value),
    }
    content = REPORT.render_perpetual_report_content(
        **{
            key: value[key]
            for key in ("synthesis", "review_request", "review", "catalog", "binding")
        },
        findings=value["findings"],
    )
    assert content.candidate["perpetual_synthesis"]["review_findings"] == value["findings"]


def test_v5_material_packet_remains_incomplete():
    value = packet()
    content = REPORT.render_perpetual_report_content(
        **{
            key: value[key]
            for key in ("synthesis", "review_request", "review", "catalog", "binding")
        }
    )
    assert content.candidate["research_complete"] is False
    assert (
        content.candidate["perpetual_synthesis"]["schema_version"] == "perpetual-research-report.v1"
    )


def test_v5_entire_rendered_packet_matches_unchanged_physical_main():
    import importlib.util
    import sys

    name = "packages.kernel.decision_hub_kernel.application._task_b_original_report"
    path = MAIN / "packages/kernel/decision_hub_kernel/application/perpetual_research_report.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    original = importlib.util.module_from_spec(spec)
    sys.modules[name] = original
    spec.loader.exec_module(original)
    assert isinstance(original.__file__, str) and Path(original.__file__).resolve() == path
    value = packet()
    args = {
        key: value[key] for key in ("synthesis", "review_request", "review", "catalog", "binding")
    }
    old = original.render_perpetual_report_content(**args)
    new = REPORT.render_perpetual_report_content(**args)
    assert new.candidate == old.candidate
    assert new.gate == old.gate and new.binding == old.binding


def test_complete_assessment_retains_exact_sources_and_subjective_probabilities():
    value = packet()
    result = assess(value)
    assert result["status"] == "complete" and result["reasons"] == []
    assert result["run_id"] == "unit-run"
    assert result["input_hashes"]["findings"] == observation_sha(value["findings"])
    assert result["input_hashes"]["catalog"] == observation_sha(value["catalog"])
    assert len(result["key_evidence_refs"]) == 3 and len(result["counter_evidence_refs"]) == 1
    assert result["financial_qualified"] is result["execution_authorized"] is False


@pytest.mark.parametrize(
    "change,reason",
    [
        ("legacy_findings", "report_assessment_v2_missing"),
        ("no_review", "independent_review_not_approved"),
        ("negative_review", "independent_review_not_approved"),
        ("unknown_protection", "review_check_incomplete:cost_protection_and_conditions"),
        ("failed_balance", "review_check_failed:evidence_balance"),
        ("no_counter", "counter_evidence_missing"),
        ("alias_duplicate", "three_distinct_key_evidence_missing"),
        ("at_decision", "information_not_strictly_before_decision"),
        ("late_actual_source", "information_not_strictly_before_decision"),
        ("after_declared_cutoff", "declared_information_cutoff_incomplete"),
        ("expired", "candidate_deadline_elapsed"),
    ],
)
def test_approved_does_not_fill_missing_completion_evidence(change: str, reason: str):
    value = packet()
    if change == "legacy_findings":
        value["findings"]["schema_version"] = "perpetual-review-findings.v1"
        del value["findings"]["report_assessment"]
    elif change == "no_review":
        value["review"] = value["findings"] = None
    elif change == "negative_review":
        value["review"]["verdict"] = "rejected"
    elif change in {"unknown_protection", "failed_balance"}:
        name = (
            "cost_protection_and_conditions"
            if change == "unknown_protection"
            else "evidence_balance"
        )
        value["findings"]["report_assessment"][name]["status"] = (
            "incomplete" if change == "unknown_protection" else "failed"
        )
    elif change == "no_counter":
        value["synthesis"]["analysis"]["opposite_chain"][0]["evidence_refs"] = []
    elif change == "alias_duplicate":
        row = value["catalog"]["originals"][-3]
        value["synthesis"]["analysis"]["main_chain"][0]["evidence_refs"] = [
            row["source_ref"],
            row["original_ref"],
            value["catalog"]["originals"][-2]["original_ref"],
        ]
    elif change == "at_decision":
        value["decision_at"] = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    elif change in {"late_actual_source", "after_declared_cutoff"}:
        row = value["catalog"]["originals"][-1]
        body = __import__("json").loads(row["body_json"])
        body["received_at"] = (
            "2026-01-01T00:10:00Z" if change == "late_actual_source" else "2026-01-01T00:00:00Z"
        )
        row["body_json"] = observation_json(body)
        row["source_ref"]["hash"] = observation_sha(body)
    elif change == "expired":
        value["decision_at"] = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    result = assess(value)
    assert result["status"] == "incomplete" and reason in result["reasons"]
    assert result["financial_qualified"] is result["execution_authorized"] is False


@pytest.mark.parametrize(
    "change", ["cross_request", "foreign_ref", "missing_check", "catalog_tamper"]
)
def test_assessment_source_mismatch_is_rejected(change: str):
    value = packet()
    if change == "cross_request":
        value["findings"]["review_request_ref"]["hash"] = "e" * 64
    elif change == "foreign_ref":
        value["findings"]["report_assessment"]["frozen_information"]["evidence_refs"] = [
            dict(ref="foreign", hash="f" * 64)
        ]
    elif change == "missing_check":
        del value["findings"]["report_assessment"]["frozen_information"]
    else:
        value["catalog"]["originals"][-1]["body_json"] = "{}"
    with pytest.raises(ValueError):
        assess(value)


def test_ordinary_direction_report_does_not_require_event_occurrence_probability():
    value = packet()
    helpers = run_path(str(MAIN / "tests/contracts/test_d2_candidate_contract.py"))
    probability = value["synthesis"]["event_probability"]
    probability["occurrence_distribution"].update(
        probabilities=None, kind="unavailable", source_ref=None
    )
    value["synthesis"]["event_probability_ref"] = helpers["body_ref"]("event", probability)
    market = value["synthesis"]["market_distribution"]
    market["event_scenario_ref"] = value["synthesis"]["event_probability_ref"]
    value["synthesis"]["market_distribution_ref"] = helpers["body_ref"]("market", market)
    assert assess(value)["status"] == "complete"


def test_full_event_assessment_reads_all_frozen_manifest_branches():
    assert assess(event_packet())["status"] == "complete"


def test_event_manifest_cannot_hide_information_after_declared_cutoff():
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import derived_ref

    value = event_packet()
    synthesis = value["synthesis"]
    synthesis["known_at_max"] = synthesis["primary_v2"]["known_at_max"] = "2025-12-31T23:59:59Z"
    synthesis["primary_v2_ref"] = derived_ref(
        "research_decision_synthesis", synthesis["primary_v2"]
    )
    request = value["review_request"]
    request["primary_review_request"]["synthesis_ref"] = synthesis["primary_v2_ref"]
    request["primary_review_request_ref"] = derived_ref(
        "action_review_request", request["primary_review_request"]
    )
    request["synthesis_ref"] = derived_ref("research_decision_synthesis", synthesis)
    value["review"]["review_request_ref"] = derived_ref("action_review_request", request)
    value["findings"]["review_request_ref"] = dict(value["review"]["review_request_ref"])
    result = assess(value)
    assert result["status"] == "incomplete"
    assert "declared_information_cutoff_incomplete" in result["reasons"]


def test_independent_check_reference_cannot_hide_later_information():
    import json
    from copy import deepcopy

    value = packet()
    row = deepcopy(value["catalog"]["originals"][-1])
    body = json.loads(row["body_json"])
    body.update(
        evidence_id="check-only-late",
        received_at="2026-01-01T00:00:00Z",
        content_hash=observation_sha({"unit_check_only_late": True}),
    )
    row.update(
        role=body["evidence_id"],
        body_json=observation_json(body),
        source_ref=dict(ref="review:check-only-late", hash=observation_sha(body)),
        original_ref=dict(ref=body["evidence_id"], hash=body["content_hash"]),
    )
    value["catalog"]["originals"].append(row)
    value["findings"]["report_assessment"]["frozen_information"]["evidence_refs"].append(
        row["source_ref"]
    )
    result = assess(value)
    assert result["status"] == "incomplete"
    assert "declared_information_cutoff_incomplete" in result["reasons"]


@pytest.mark.parametrize("use_reference,expected", [(True, "incomplete"), (False, "complete")])
def test_reasoning_only_reference_cannot_hide_later_information(use_reference: bool, expected: str):
    import json
    from copy import deepcopy

    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import derived_ref

    value = packet()
    row = deepcopy(value["catalog"]["originals"][-1])
    body = json.loads(row["body_json"])
    body.update(
        evidence_id="reasoning-only-late",
        received_at="2026-01-01T00:00:00Z",
        content_hash=observation_sha({"unit_reasoning_only_late": True}),
    )
    row.update(
        role=body["evidence_id"],
        body_json=observation_json(body),
        source_ref=dict(ref="review:reasoning-only-late", hash=observation_sha(body)),
        original_ref=dict(ref=body["evidence_id"], hash=body["content_hash"]),
    )
    value["catalog"]["originals"].append(row)
    if use_reference:
        value["synthesis"]["reasoning_refs"].append(row["source_ref"])
    request = value["review_request"]
    request["synthesis_ref"] = derived_ref("research_decision_synthesis", value["synthesis"])
    value["binding"]["synthesis_ref"] = request["synthesis_ref"]
    value["review"]["review_request_ref"] = derived_ref("action_review_request", request)
    value["findings"]["review_request_ref"] = dict(value["review"]["review_request_ref"])
    result = assess(value)
    assert result["status"] == expected
    if use_reference:
        assert "declared_information_cutoff_incomplete" in result["reasons"]
    else:
        assert result["reasons"] == []


@pytest.mark.parametrize("change", ["p", "q", "coverage"])
def test_event_missing_p_or_any_q_or_branch_cannot_be_complete(change: str):
    result = assess(event_packet(change))
    assert result["status"] == "incomplete"
    assert "event_probability_or_branch_coverage_incomplete" in result["reasons"]
