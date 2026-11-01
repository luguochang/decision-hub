"""Completion facts through original models; no financial or native-origin claim."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from runpy import run_path
from typing import Any

# Candidate contracts must load before Main consumers cache model classes.
# ruff: noqa: E402

SUPPORT = run_path(str(Path(__file__).with_name("candidate_perpetual_review_support.py")))
SUPPORT["load_review_candidates"]()
LOAD = SUPPORT["load_candidate_module"]
REPORT = LOAD(
    "packages.kernel.decision_hub_kernel.application.perpetual_research_report",
    "packages/kernel/decision_hub_kernel/application/perpetual_research_report.py",
)
MAIN = SUPPORT["MAIN"]

from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
    SERVER_ROLES,
    validate_analysis_originals,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
)

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence

DECISION = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
CHECKS = (
    "frozen_information",
    "probability_completeness",
    "evidence_balance",
    "cost_protection_and_conditions",
)


def packet() -> dict[str, Any]:
    """Full original synthesis fixture plus four explicitly synthetic source bodies."""
    helpers = run_path(str(MAIN / "tests/contracts/test_d2_candidate_contract.py"))
    request = helpers["review_request"]()
    synthesis = request["synthesis"]
    math = run_path(str(MAIN / "tests/decision_product/test_action_comparison.py"))
    old = synthesis["event_probability"]["occurrence_distribution"]
    synthesis["event_probability"]["occurrence_distribution"] = math["estimate"](
        old["target_ref"], {"occurs": "0.6", "not_occurs": "0.4"}
    )
    synthesis["event_probability_ref"] = helpers["body_ref"](
        "event", synthesis["event_probability"]
    )
    synthesis["market_distribution"]["event_scenario_ref"] = synthesis["event_probability_ref"]
    synthesis["market_distribution_ref"] = helpers["body_ref"](
        "market", synthesis["market_distribution"]
    )
    request["allowed_domain"].update(
        event_probability_ref=synthesis["event_probability_ref"],
        market_distribution_ref=synthesis["market_distribution_ref"],
    )
    synthesis["allowed_domain_ref"] = helpers["body_ref"]("domain", request["allowed_domain"])
    request["allowed_domain_ref"] = synthesis["allowed_domain_ref"]
    rows = []
    for role in SERVER_ROLES:
        body = {"unit_fixture_role": role}
        rows.append(
            dict(
                source_kind="server_material",
                role=role,
                body_json=observation_json(body),
                source_ref=dict(ref="server:" + role, hash=observation_sha(body)),
                original_ref=None,
            )
        )
    refs = []
    for index in range(4):
        body = ResearchInputEvidence.model_validate(
            dict(
                evidence_id=f"unit-evidence-{index}",
                kind="official",
                authority="official",
                source_id=f"unit-source-{index}",
                source_url="https://example.com/unit",
                published_at="2025-12-31T23:58:00Z",
                observed_at="2025-12-31T23:59:00Z",
                received_at="2025-12-31T23:59:59Z",
                content_hash=observation_sha({"unit_content": index}),
                excerpt="Offline evidence fixture; no real market or native-origin claim.",
            )
        ).model_dump(mode="json")
        ref = dict(ref=body["evidence_id"], hash=body["content_hash"])
        rows.append(
            dict(
                source_kind="frozen_input",
                role=body["evidence_id"],
                body_json=observation_json(body),
                source_ref=dict(ref="review:unit:" + str(index), hash=observation_sha(body)),
                original_ref=ref,
            )
        )
        refs.append(ref)
    synthesis["known_at_max"] = "2025-12-31T23:59:59Z"
    synthesis["reasoning_refs"] = refs[:3]
    synthesis["analysis"]["main_chain"][0]["evidence_refs"] = refs[:3]
    synthesis["analysis"]["opposite_chain"][0]["evidence_refs"] = [refs[3]]
    request["synthesis_ref"] = helpers["body_ref"]("synth", synthesis)
    normalize_action_synthesis(synthesis)
    catalog = dict(
        schema_version="decision-review-originals.v1",
        run_id="unit-run",
        request_hash="b" * 64,
        as_of=DECISION.isoformat().replace("+00:00", "Z"),
        originals=rows,
    )
    validate_analysis_originals(synthesis, catalog)
    request_ref = dict(ref="unit-review-request", hash=observation_sha(request))
    review = dict(verdict="approved", review_request_ref=request_ref)
    findings = dict(
        schema_version="perpetual-review-findings.v2",
        review_request_ref=deepcopy(request_ref),
        summary="Explicit unit assessment, never an authenticated child receipt.",
        issues=[],
        report_assessment={
            name: dict(
                status="passed",
                reason="Retained unit evidence supports this check.",
                evidence_refs=refs,
            )
            for name in CHECKS
        },
    )
    binding = dict(
        schema_version="perpetual-report-binding.v1",
        mapped_ref={"unit": "mapped"},
        provider_emission_ref={"unit": "provider"},
        synthesis_ref=request["synthesis_ref"],
        native_review_verdict="approved",
        financial_qualification="unqualified",
    )
    return dict(
        synthesis=synthesis,
        review_request=request,
        review=review,
        findings=findings,
        catalog=catalog,
        binding=binding,
        decision_at=DECISION,
    )


def assess(value: dict[str, Any]) -> dict[str, Any]:
    helper = LOAD(
        "packages.kernel.decision_hub_kernel.application.perpetual_report_delivery",
        "packages/kernel/decision_hub_kernel/application/perpetual_report_delivery.py",
    )
    return helper.assess_report_delivery(**value)


def event_packet(change: str | None = None) -> dict[str, Any]:
    """Real original event-plan builder/manifest and full V3 contract, synthetic originals."""
    from packages.kernel.decision_hub_kernel.decision.event_scenario_action_plan import (
        build_event_scenario_action_plan,
    )
    from packages.kernel.decision_hub_kernel.ports.event_scenario_manifest import (
        pack_event_scenario_manifest,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import derived_ref
    from tests.decision_product.test_event_scenario_action_plan import fixture_plan

    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        ActionReviewRequestV3,
    )

    value = packet()
    draft, originals = fixture_plan()
    plan = build_event_scenario_action_plan(draft, originals)
    if change == "p":
        plan["occurrence"].update(probabilities=None, kind="unavailable", source_ref=None)
    elif change == "q":
        plan["branches"][1]["market_distribution"]["direction_distribution"].update(
            probabilities=None, kind="unavailable", source_ref=None
        )
    elif change == "coverage":
        plan["branches"] = plan["branches"][:1]
    manifest = pack_event_scenario_manifest(
        dict(event_plan=plan, normalized_event_plan_draft=draft, declared_originals=[])
    )
    manifest_ref = derived_ref("event_manifest", manifest)
    plan_ref = derived_ref("event_plan", plan)
    draft_ref = derived_ref("event_draft", draft)
    body = dict(
        schema_version="event-scenario-prepared-original.v2",
        manifest=manifest,
        event_plan_ref=plan_ref,
        event_plan_manifest_ref=manifest_ref,
        event_plan_draft_ref=draft_ref,
        source_draft_hash=observation_sha(draft),
        source_emission_ref=None,
        source_emission_context=None,
    )
    value["catalog"]["originals"].append(
        dict(
            source_kind="server_material",
            role="event_scenario_plan",
            source_ref=dict(ref="review:event-plan", hash=observation_sha(body)),
            original_ref=manifest_ref,
            body_json=observation_json(body),
        )
    )
    primary = value["synthesis"]
    primary["known_at_max"] = max(primary["known_at_max"], plan["known_at_max"])
    value["review_request"]["synthesis_ref"] = derived_ref("research_decision_synthesis", primary)
    synthesis = dict(
        schema_version="research-decision-synthesis.v3",
        **{key: primary[key] for key in ("request_id", "opportunity_id", "as_of", "known_at_max")},
        primary_v2=primary,
        primary_v2_ref=derived_ref("research_decision_synthesis", primary),
        event_plan_ref=plan_ref,
        event_plan_manifest_ref=manifest_ref,
        event_plan_draft_ref=draft_ref,
    )
    domain = dict(
        plan_ref=plan_ref,
        method_ref=plan["method_ref"],
        method_code_hash=plan["method_code_hash"],
        freeze_before=plan["freeze_before"],
        valid_until=max(row["valid_until"] for row in plan["branches"]),
        branches=[
            dict(
                branch_ref=row["branch_ref"],
                candidate_refs=[
                    ref
                    for position in ("flat", "long", "short")
                    for ref in row["reviewed_candidate_refs_by_position"][position]
                ],
                applicable_from=row["applicable_from"],
                valid_until=row["valid_until"],
            )
            for row in plan["branches"]
        ],
    )
    request = dict(
        schema_version="action-review-request.v3",
        synthesis=synthesis,
        synthesis_ref=derived_ref("research_decision_synthesis", synthesis),
        primary_review_request=value["review_request"],
        primary_review_request_ref=derived_ref("action_review_request", value["review_request"]),
        event_plan_domain=domain,
        event_plan_domain_ref=derived_ref("event_review_domain", domain),
        requested_at=value["review_request"]["requested_at"],
    )
    ActionReviewRequestV3.model_validate(request)
    value.update(synthesis=synthesis, review_request=request)
    value["binding"]["synthesis_ref"] = request["synthesis_ref"]
    value["review"]["review_request_ref"] = derived_ref("action_review_request", request)
    value["findings"]["review_request_ref"] = deepcopy(value["review"]["review_request_ref"])
    return value
