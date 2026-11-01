"""Report completeness after original native authentication, never financial authority."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..ports.market_path import aware_utc
from ..ports.research_action_synthesis import (
    normalize_action_synthesis,
    primary_perpetual_synthesis,
)
from .perpetual_review_materials import validate_analysis_originals
from .provider_input_receipts import strict_json
from .research_publication_binding import observation_json, observation_sha

CHECKS = (
    "frozen_information",
    "probability_completeness",
    "evidence_balance",
    "cost_protection_and_conditions",
)
SCHEMA = "perpetual-report-delivery.v1"


def findings_from_authenticated_wrapper(
    native: Any, review: dict[str, Any]
) -> dict[str, Any] | None:
    """Caller has replayed native proof; only project its exact dual-object output."""
    from .perpetual_review_envelope import read_frozen_review_envelope_v4

    wrapper = read_frozen_review_envelope_v4(native.review_wrapper_json)
    matches = []
    for block in wrapper["original_output"]["output"]:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            value = strict_json(block["text"])
        except (KeyError, ValueError):
            continue
        if isinstance(value, list) and len(value) == 2 and value[0] == review:
            matches.append(value[1])
    if not matches:
        return None  # Historical v1 authorization-only output stays incomplete.
    if len(matches) != 1 or not isinstance(matches[0], dict):
        raise ValueError("perpetual_report_findings_original_ambiguous")
    return matches[0]


def assumptions_from_authenticated_wrapper(native: Any) -> str | None:
    """Only after original native replay matched this wrapper to the frozen prepare."""
    from packages.kernel.decision_hub_kernel.ports.perpetual_assessment_refs import (
        model_assumptions_from_review_prompt,
    )

    from .perpetual_review_envelope import read_frozen_review_envelope_v4

    wrapper = read_frozen_review_envelope_v4(native.review_wrapper_json)
    if "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON" not in wrapper["actual_prompt"].split("\n"):
        return None
    return observation_json(model_assumptions_from_review_prompt(wrapper["actual_prompt"]))


def _instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("perpetual_report_information_time_missing")
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _evidence(
    links: list[dict[str, Any]], available: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for link in links:
        for reference in link["evidence_refs"]:
            row = available[observation_json(reference)]
            if row["source_kind"] not in {"frozen_input", "run_evidence"}:
                continue
            body = strict_json(row["body_json"])
            # source_ref/original_ref and repeated content are aliases, not new evidence.
            identity = body.get("content_hash")
            if not isinstance(identity, str) or len(identity) != 64:
                continue
            unique.setdefault(identity, row)
    return list(unique.values())


def _probability_reasons(synthesis: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    primary = primary_perpetual_synthesis(synthesis)
    # Original typed contracts already validate decimal mass and partitions.
    # Ordinary direction research does not claim that an event probability is known.
    if synthesis["schema_version"] != "research-decision-synthesis.v3":
        return (
            ["probability_distribution_incomplete"]
            if primary["market_distribution"]["direction_distribution"]["probabilities"] is None
            else []
        )
    from ..ports.event_scenario_manifest import unpack_event_scenario_manifest

    row = next(row for row in catalog["originals"] if row["role"] == "event_scenario_plan")
    plan = unpack_event_scenario_manifest(strict_json(row["body_json"])["manifest"])["event_plan"]
    p, outcomes = plan["occurrence"], plan["outcome_given_occurrence"]
    distributions = [
        p,
        outcomes,
        *[branch["market_distribution"]["direction_distribution"] for branch in plan["branches"]],
    ]
    scenarios = {item["outcome_id"] for item in outcomes["probabilities"] or []}
    predicates = {item["scenario_id"] for item in plan["scenario_predicates"]}
    branches = {item["scenario_id"] for item in plan["branches"]}
    if (
        any(item["probabilities"] is None for item in distributions)
        or not scenarios
        or scenarios != predicates
        or scenarios != branches
    ):
        return ["event_probability_or_branch_coverage_incomplete"]
    return []


def _source_times(row: dict[str, Any]) -> tuple[list[datetime], bool]:
    body = strict_json(row["body_json"])
    times = [
        _instant(body[name])
        for name in ("published_at", "observed_at", "received_at", "known_at")
        if body.get(name) is not None
    ]
    missing = body.get("received_at") is None or body.get("observed_at") is None
    return times, missing


def assess_report_delivery(
    *,
    synthesis: dict[str, Any],
    review_request: dict[str, Any],
    review: dict[str, Any] | None,
    findings: dict[str, Any] | None,
    catalog: dict[str, Any],
    binding: dict[str, Any],
    decision_at: datetime,
    assessment_assumptions_json: str | None = None,
) -> dict[str, Any]:
    """Derive immutable content facts from already authenticated originals only.

    This pure function neither authenticates a native child nor writes a row.
    The production caller authenticates all inputs before invoking it.
    """
    normalize_action_synthesis(synthesis)
    cited = validate_analysis_originals(synthesis, catalog)
    primary = primary_perpetual_synthesis(synthesis)
    at = aware_utc(decision_at)
    reasons: list[str] = []
    available = {
        observation_json(ref): row
        for row in catalog["originals"]
        for ref in (row["source_ref"], row["original_ref"])
        if ref is not None
    }
    main = _evidence(primary["analysis"]["main_chain"], available)
    counter = _evidence(primary["analysis"]["opposite_chain"], available)
    if len(main) < 3:
        reasons.append("three_distinct_key_evidence_missing")
    if not counter:
        reasons.append("counter_evidence_missing")
    times = [_instant(primary["known_at_max"])]
    if synthesis["schema_version"] == "research-decision-synthesis.v3":
        from ..ports.event_scenario_manifest import unpack_event_scenario_manifest

        event = next(row for row in catalog["originals"] if row["role"] == "event_scenario_plan")
        plan = unpack_event_scenario_manifest(strict_json(event["body_json"])["manifest"])[
            "event_plan"
        ]
        times.extend(_instant(plan[name]) for name in ("known_at_max", "as_of"))
    for key in cited:
        row = available[key]
        if row["source_kind"] not in {"frozen_input", "run_evidence"}:
            continue
        source_times, missing = _source_times(row)
        times.extend(source_times)
        if missing:
            reasons.append("evidence_availability_time_missing")
    target, context = primary["candidate_target"], primary["candidate_set"]["context"]
    duration = {"4h": 14400, "24h": 86400, "72h": 259200}.get(context["horizon"])
    if target["horizon"] != context["horizon"] or target["duration_seconds"] != duration:
        reasons.append("target_horizon_incomplete")
    if _instant(target["valid_until"]) <= at:
        reasons.append("candidate_deadline_elapsed")
    reasons.extend(_probability_reasons(synthesis, catalog))
    if review is None or review["verdict"] != "approved":
        reasons.append("independent_review_not_approved")
    if findings is None or findings["schema_version"] != "perpetual-review-findings.v2":
        reasons.append("report_assessment_v2_missing")
    else:
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            PerpetualReviewFindingsV2,
        )

        parsed = PerpetualReviewFindingsV2.model_validate(findings).model_dump(mode="json")
        if review is None or parsed["review_request_ref"] != review["review_request_ref"]:
            raise ValueError("perpetual_report_findings_review_binding_mismatch")
        from packages.kernel.decision_hub_kernel.ports.perpetual_assessment_refs import (
            proposal_assessment_refs,
        )

        assumptions: list[dict[str, Any]] = (
            [] if assessment_assumptions_json is None else strict_json(assessment_assumptions_json)
        )
        if (assessment_assumptions_json is not None
                and observation_json(assumptions) != assessment_assumptions_json):
            raise ValueError("perpetual_report_assessment_assumptions_changed")
        proposals = proposal_assessment_refs(synthesis, assumptions)
        for name in CHECKS:
            check = parsed["report_assessment"][name]
            if any(observation_json(ref) not in available
                   and observation_json(ref) not in proposals.get(name, set())
                   for ref in check["evidence_refs"]):
                raise ValueError("perpetual_report_findings_original_unavailable")
            for reference in check["evidence_refs"]:
                row = available.get(observation_json(reference))
                if row is not None and row["source_kind"] in {"frozen_input", "run_evidence"}:
                    source_times, missing = _source_times(row)
                    times.extend(source_times)
                    if missing:
                        reasons.append("evidence_availability_time_missing")
            if check["status"] != "passed":
                reasons.append("review_check_" + check["status"] + ":" + name)
            elif not check["evidence_refs"]:
                reasons.append("review_check_without_evidence:" + name)
    if any(value >= at for value in times) or _instant(primary["as_of"]) > at:
        reasons.append("information_not_strictly_before_decision")
    if any(value > _instant(primary["known_at_max"]) for value in times):
        reasons.append("declared_information_cutoff_incomplete")
    inputs = dict(
        synthesis=synthesis,
        review_request=review_request,
        authorization=review,
        findings=findings,
        catalog=catalog,
        source_binding=binding,
    )
    return dict(
        schema_version=SCHEMA,
        run_id=catalog["run_id"],
        decision_at=at.isoformat(),
        status="incomplete" if reasons else "complete",
        reasons=sorted(set(reasons)),
        input_hashes={key: observation_sha(value) for key, value in inputs.items()},
        source_refs=dict(
            mapped_ref=binding["mapped_ref"],
            provider_emission_ref=binding["provider_emission_ref"],
            synthesis_ref=binding["synthesis_ref"],
        ),
        key_evidence_refs=[row["original_ref"] or row["source_ref"] for row in main],
        counter_evidence_refs=[row["original_ref"] or row["source_ref"] for row in counter],
        financial_qualified=False,
        execution_authorized=False,
    )
