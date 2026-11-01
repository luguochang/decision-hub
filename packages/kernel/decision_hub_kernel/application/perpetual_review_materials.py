"""SELECT-only review originals; source text never grants product permissions."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionReviewOriginalsV1,
)
from sqlalchemy import select

from ..persistence.db import ResearchEvidenceRecord, as_utc
from ..ports.market_path import aware_utc
from .research_publication_binding import observation_json, observation_sha
from .research_request_identity import research_request_hash

SERVER_ROLES = (
    "instrument_contract",
    "execution_profile",
    "comparison_method",
    "cost_assumptions",
    "cost_policy",
    "close_window",
    "candidate_target",
    "anchor_rule",
    "allowed_domain_policy",
    "invalidation_policy",
    "settings_revision",
    "account_snapshot",
    "position_context",
    "utility_policy",
    "policies",
)


def _event_source_rules(agenda: dict[str, Any]) -> dict[str, Any]:
    """Frozen parser rules from authenticated schedule content, never date guesses."""
    rules: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    rules, gaps = [], []
    for entry in agenda["entries"]:
        if entry["family"] == "pce" and entry["stage_id"] == "release":
            from ..ports.pce_source_rules import pce_source_rule

            rule = pce_source_rule(entry)
            if rule is None:
                gaps.append(dict(event_key=entry["event_key"],
                                 reason="pce_reference_period_not_authenticated"))
            else:
                rules.append(rule)
            continue
        if entry["family"] == "nfp" and entry["stage_id"] == "release":
            from ..ports.employment_source_rules import employment_source_rule

            rule = employment_source_rule(entry)
            if rule is not None:
                rules.append(rule)
            continue
        if (
            entry["family"] == "fomc"
            and entry["stage_id"] in {"policy_statement", "press_conference"}
        ):
            from ..ports.fomc_source_rules import fomc_source_rule

            fomc_rule = fomc_source_rule(entry)
            if fomc_rule is not None:
                rules.append(fomc_rule)
            else:
                reason = (
                    "fomc_press_conference_actual_original_not_bound"
                    if entry["stage_id"] == "press_conference"
                    else "fomc_statement_source_rule_not_authenticated"
                )
                gaps.append(dict(event_key=entry["event_key"], reason=reason))
            continue
        if entry["family"] != "cpi" or entry["stage_id"] != "release":
            continue
        original = json.loads(entry["original_json"])
        payload = original.get("parsed", {}).get("payload", {})
        period = payload.get("reference_period")
        if (
            entry["source_id"] != "cpi-publication-schedule"
            or payload.get("schema_version") != "cpi-publication-schedule.v1"
            or payload.get("reference_period_basis") != "dol_next_release_statement"
            or not isinstance(period, str)
            or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period)
        ):
            gaps.append(
                dict(event_key=entry["event_key"], reason="cpi_reference_period_not_authenticated")
            )
            continue
        rule = dict(
            schema_version="event-scenario-observation-rule.v1",
            adapter="bls_cpi_release_document.v1",
            source_id="bls-releases",
            adapter_version="official-feed.v2",
            source_url="https://www.bls.gov/news.release/cpi.htm",
            event_key=entry["event_key"],
            family="cpi",
            stage_id="release",
            reference_period=period,
            value_kind="official_published_sa_month_change",
            partition_family="cpi-published-value.v1",
            precision=dict(
                unit="percentage_point", decimal_places=1, rounding="publisher_reported"
            ),
            metrics=[
                dict(metric_id=key, unit="percent", reference_period=period, stage_id="release")
                for key in ("headline_mom", "core_mom")
            ],
        )
        rules.append(
            dict(
                schedule_ref=entry["source_ref"],
                observation_rule_ref=dict(
                    ref="event-source-rule:" + observation_sha(rule), hash=observation_sha(rule)
                ),
                rule=rule,
            )
        )
    return dict(schema_version="event-scenario-source-rules.v1", rules=rules, gaps=gaps)


event_source_rules_for_agenda = _event_source_rules


def _entry(kind, role, body, original=None):
    text = observation_json(body)
    return dict(
        source_ref=dict(ref=f"review-original:{kind}:{role}", hash=observation_sha(body)),
        original_ref=original,
        source_kind=kind,
        role=role,
        body_json=text,
    )


def build_review_originals(
    request, materials, *, as_of, evidence=(), historical_materials=None,
):
    """Caller supplies authenticated Task/materials and admitted evidence only."""
    at = aware_utc(as_of)
    ctx = materials["context"]
    refs = dict(
        instrument_contract=ctx["instrument_ref"],
        execution_profile=ctx["profile_ref"],
        comparison_method=materials["method_ref"],
        cost_assumptions=materials["cost_assumptions_ref"],
        cost_policy=materials["cost_policy_ref"],
        close_window=materials["close_window"].get("source_ref"),
        candidate_target=materials["candidate_target_ref"],
        anchor_rule=materials["anchor_rule_ref"],
        allowed_domain_policy=materials["allowed_domain_policy_ref"],
        invalidation_policy=materials["invalidation_policy_ref"],
        settings_revision=ctx["settings_ref"],
        account_snapshot=ctx["account_ref"],
        position_context=ctx["position_ref"],
        utility_policy=ctx["utility_ref"],
    )
    rows = [
        _entry("server_material", role, materials[role], refs.get(role)) for role in SERVER_ROLES
    ]
    if "input_binding" in materials:
        rows.append(
            _entry(
                "server_material",
                "input_binding",
                materials["input_binding"],
                materials["input_binding_ref"],
            )
        )
    if "direction_base_rate" in materials:
        rows.append(
            _entry(
                "server_material",
                "direction_base_rate",
                dict(
                    schema_version="perpetual-direction-base-rate-wrapper.v1",
                    parent_ref=materials["direction_base_rate_ref"],
                    child_path="/direction_base_rate",
                    base_rate=materials["direction_base_rate"],
                ),
                materials["direction_base_rate_ref"],
            )
        )
    if "event_agenda" in materials:
        rows.append(
            _entry(
                "server_material",
                "event_agenda",
                dict(
                    schema_version="perpetual-event-agenda-original.v1",
                    parent_ref=materials["event_agenda_ref"],
                    child_path="/event_agenda",
                    agenda=materials["event_agenda"],
                ),
                materials["event_agenda_ref"],
            )
        )
        source_rules = _event_source_rules(materials["event_agenda"])
        rows.append(
            _entry(
                "server_material",
                "event_scenario_source_rules",
                source_rules,
                dict(
                    ref="event-source-rules:" + observation_sha(source_rules),
                    hash=observation_sha(source_rules),
                ),
            )
        )
    for source in request.input_evidence:
        body = source.model_dump(mode="json")
        if (
            source.received_at > at
            or source.observed_at > at
            or source.published_at is not None
            and source.published_at > at
        ):
            raise ValueError("perpetual_review_future_original")
        rows.append(
            _entry(
                "frozen_input",
                source.evidence_id,
                body,
                dict(ref=source.evidence_id, hash=source.content_hash),
            )
        )
    for source in evidence:
        if (
            source.received_at > at
            or source.observed_at > at
            or source.published_at is not None
            and source.published_at > at
        ):
            raise ValueError("perpetual_review_future_original")
        body = source.model_dump(mode="json")
        if historical_materials and source.evidence_id in historical_materials:
            from .historical_review_material import KEY, validate_daily_review_material

            material = historical_materials[source.evidence_id]
            validate_daily_review_material(material, source, as_of=at)
            body[KEY] = material
        rows.append(
            _entry(
                "run_evidence",
                source.evidence_id,
                body,
                dict(ref=source.evidence_id, hash=source.content_hash),
            )
        )
    value = dict(
        schema_version="decision-review-originals.v1",
        run_id=request.run_id,
        request_hash=research_request_hash(request),
        as_of=at.isoformat().replace("+00:00", "Z"),
        originals=rows,
    )
    validate_catalog(value)
    return value


def validate_catalog(value):
    DecisionReviewOriginalsV1.model_validate(value)
    if len(observation_json(value).encode()) > 640_000:
        raise ValueError("perpetual_review_originals_oversized")
    keys = set()
    for row in value["originals"]:
        body = json.loads(row["body_json"])
        key = observation_json(row["source_ref"])
        if (
            key in keys
            or row["body_json"] != observation_json(body)
            or observation_sha(body) != row["source_ref"]["hash"]
        ):
            raise ValueError("perpetual_review_original_body_mismatch")
        keys.add(key)
        if row["source_kind"] == "server_material" and row["role"] == "input_binding":
            from ..ports.perpetual_research_inputs import ResearchAccountBindingV1

            binding = ResearchAccountBindingV1.model_validate(body)
            from .perpetual_research_inputs import _reference

            if binding.run_id != value["run_id"] or row["original_ref"] != _reference(
                binding
            ).model_dump(mode="json"):
                raise ValueError("perpetual_review_input_binding_mismatch")
        if row["source_kind"] == "server_material" and row["role"] == "event_agenda":
            from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
                EventAgendaSnapshotV1,
            )

            agenda = EventAgendaSnapshotV1.model_validate(body["agenda"])
            wire = agenda.model_dump(mode="json")
            if (
                set(body) != {"schema_version", "parent_ref", "child_path", "agenda"}
                or body["schema_version"] != "perpetual-event-agenda-original.v1"
                or body["child_path"] != "/event_agenda"
                or row["original_ref"] is None
                or body["parent_ref"] != row["original_ref"]
                or agenda.payload_hash
                != observation_sha(
                    {key: item for key, item in wire.items() if key != "payload_hash"}
                )
                or agenda.as_of > datetime.fromisoformat(value["as_of"].replace("Z", "+00:00"))
                or any(entry.known_at > agenda.as_of for entry in agenda.entries)
            ):
                raise ValueError("perpetual_agenda_original_mismatch")
    server = [row["role"] for row in value["originals"] if row["source_kind"] == "server_material"]
    if (
        len(server) != len(set(server))
        or not set(SERVER_ROLES) <= set(server)
        or set(server)
        - set(SERVER_ROLES)
        - {
            "event_agenda",
            "input_binding",
            "direction_base_rate",
            "event_scenario_source_rules",
            "event_scenario_plan",
            "event_scenario_method",
        }
    ):
        raise ValueError("perpetual_review_required_original_missing")


def validate_analysis_originals(synthesis, catalog):
    validate_catalog(catalog)
    value = synthesis.model_dump(mode="json") if hasattr(synthesis, "model_dump") else synthesis
    if value.get("schema_version") == "research-decision-synthesis.v3":
        event_rows = [
            row
            for row in catalog["originals"]
            if row["source_kind"] == "server_material" and row["role"] == "event_scenario_plan"
        ]
        if len(event_rows) != 1:
            raise ValueError("perpetual_event_plan_review_original_missing")
        event = json.loads(event_rows[0]["body_json"])
        from ..ports.event_scenario_manifest import unpack_event_scenario_manifest

        expanded = unpack_event_scenario_manifest(event["manifest"])
        if (
            observation_sha(expanded["event_plan"]) != value["event_plan_ref"]["hash"]
            or event["event_plan_ref"] != value["event_plan_ref"]
            or observation_sha(event["manifest"]) != value["event_plan_manifest_ref"]["hash"]
            or event["event_plan_manifest_ref"] != value["event_plan_manifest_ref"]
        ):
            raise ValueError("perpetual_event_plan_review_original_changed")
        value = value["primary_v2"]
    available = {
        observation_json(ref)
        for row in catalog["originals"]
        for ref in (row["source_ref"], row["original_ref"])
        if ref is not None
    }
    references = list(value["reasoning_refs"])
    for link in value["analysis"]["main_chain"] + value["analysis"]["opposite_chain"]:
        references.extend(link["evidence_refs"])
    if any(observation_json(ref) not in available for ref in references):
        raise ValueError("perpetual_analysis_original_unavailable")
    return {observation_json(ref) for ref in references}


def select_review_originals(synthesis, catalog):
    selected = validate_analysis_originals(synthesis, catalog)
    return dict(
        catalog,
        originals=[
            row
            for row in catalog["originals"]
            if row["source_kind"] == "server_material"
            or any(
                observation_json(ref) in selected
                for ref in (row["source_ref"], row["original_ref"])
                if ref is not None
            )
        ],
    )


def review_originals_from_prompt(prompt):
    lines = prompt.split("\n")
    marker = "BEGIN_AUTHENTICATED_REVIEW_ORIGINALS_JSON"
    if lines.count(marker) != 1:
        raise ValueError("perpetual_review_originals_missing")
    start = lines.index(marker)
    if lines[start + 2] != "END_AUTHENTICATED_REVIEW_ORIGINALS_JSON":
        raise ValueError("perpetual_review_originals_marker_invalid")
    value = json.loads(lines[start + 1])
    validate_catalog(value)
    if lines[start + 1] != observation_json(value) or lines[
        start + 3
    ] != "REVIEW_ORIGINALS_SHA256=" + observation_sha(value):
        raise ValueError("perpetual_review_originals_digest_mismatch")
    return value


def read_review_originals_in_session(session, request, materials, *, as_of, selected=None):
    """Historical selected-entry readback ignores unrelated later evidence."""
    from .research_evidence import _evidence_model, _validate_acquisition_receipt

    at = aware_utc(as_of)
    query = (
        select(ResearchEvidenceRecord)
        .where(
            ResearchEvidenceRecord.run_id == request.run_id,
            ResearchEvidenceRecord.accepted_at <= at,
            ResearchEvidenceRecord.quality == "accepted",
        )
        .order_by(ResearchEvidenceRecord.evidence_id)
    )
    if selected is not None:
        validate_catalog(selected)
        if (
            selected["run_id"] != request.run_id
            or selected["request_hash"] != research_request_hash(request)
            or aware_utc(datetime.fromisoformat(selected["as_of"].replace("Z", "+00:00"))) != at
        ):
            raise ValueError("perpetual_review_original_scope_mismatch")
        ids = [row["role"] for row in selected["originals"] if row["source_kind"] == "run_evidence"]
        query = query.where(ResearchEvidenceRecord.evidence_id.in_(ids))
    evidence = []
    for row in session.scalars(query).all():
        if as_utc(row.accepted_at) > at:
            raise ValueError("perpetual_review_future_original")
        item = _evidence_model(row)
        _validate_acquisition_receipt(session, item)
        evidence.append(item)
    if selected is None:
        from ..ports.research_synthesis_protocol import (
            V2_DECISION_OUTPUT_SCHEMA_REF,
            upcoming_perpetual_event,
        )

        # Limited rollout: match the existing profile's authenticated upcoming
        # event predicate at this exact catalog clock. Both root authoring and
        # prepare use this reader, so no new view ref is issued then stripped.
        # Explicit V3 and any other legacy protocol retain their original body.
        if (
            getattr(request, "output_schema_ref", None) != V2_DECISION_OUTPUT_SCHEMA_REF
            or upcoming_perpetual_event(
                {"context": materials["context"], "event_agenda": materials.get("event_agenda")},
                as_of=at,
            ) is not None
        ):
            return build_review_originals(request, materials, as_of=at, evidence=evidence)
    from .historical_review_material import KEY, read_daily_review_material_in_session

    # A historical selected entry opts into its exact old representation.
    # Never add a newly available view to an old body/hash during readback.
    wanted = None if selected is None else {
        row["role"] for row in selected["originals"]
        if row["source_kind"] == "run_evidence" and KEY in json.loads(row["body_json"])
    }
    historical_materials = {}
    # Preserve the old summary budget before attempting any optional full view.
    # A selected catalog must instead rebuild every view in full, without this
    # fresh-preparation fallback.
    actual = (
        build_review_originals(request, materials, as_of=at, evidence=evidence)
        if selected is None else None
    )
    for item in evidence:
        if wanted is None or item.evidence_id in wanted:
            material = read_daily_review_material_in_session(
                session, run_id=request.run_id, evidence=item, as_of=at,
            )
            if material is not None:
                trial = {**historical_materials, item.evidence_id: material}
                if selected is None:
                    try:
                        next_catalog = build_review_originals(
                            request, materials, as_of=at, evidence=evidence,
                            historical_materials=trial,
                        )
                    except ValueError as exc:
                        if str(exc) == "perpetual_review_originals_oversized":
                            continue
                        raise
                    actual = next_catalog
                historical_materials = trial
    if selected is not None:
        actual = build_review_originals(
            request, materials, as_of=at, evidence=evidence,
            historical_materials=historical_materials,
        )
    if selected is None:
        return actual
    event_rows = [
        row
        for row in selected["originals"]
        if row["source_kind"] == "server_material" and row["role"] == "event_scenario_plan"
    ]
    if event_rows:
        from ..ports.event_scenario_preparation import prepare_event_scenario_catalog
        from .provider_input_receipts import read_provider_emission_in_session, strict_json

        if len(event_rows) != 1:
            raise ValueError("perpetual_event_plan_source_ambiguous")
        event = strict_json(event_rows[0]["body_json"])
        identity = event["source_emission_context"]
        if identity is None or identity["run_id"] != request.run_id:
            raise ValueError("perpetual_event_plan_source_identity_missing")
        source = read_provider_emission_in_session(session, **identity)
        if source is None or source[1].model_dump(mode="json") != event["source_emission_ref"]:
            raise ValueError("perpetual_event_plan_source_original_missing")
        emission, reference = source
        arguments = strict_json(emission.submit.raw_arguments_json)
        if (
            emission.submit.tool_name != "decision_hub_synthesis_submit"
            or emission.submit.request_hash != research_request_hash(request)
            or arguments.get("action") != "prepare"
        ):
            raise ValueError("perpetual_event_plan_source_original_mismatch")
        actual = prepare_event_scenario_catalog(
            arguments["draft"],
            binding={
                key: materials[key]
                for key in (
                    "opportunity_id",
                    "context",
                    "candidate_target_ref",
                    "anchor_rule_ref",
                    "method_ref",
                    "method_code_hash",
                )
            },
            catalog=actual,
            source_known_at=emission.known_at.isoformat().replace("+00:00", "Z"),
            source_emission_ref=reference.model_dump(mode="json"),
            source_emission_context=identity,
        )
    if ("input_binding" in materials) != any(
        row["source_kind"] == "server_material" and row["role"] == "input_binding"
        for row in selected["originals"]
    ):
        raise ValueError("perpetual_review_required_original_missing")
    by_ref = {observation_json(row["source_ref"]): row for row in actual["originals"]}
    if any(by_ref.get(observation_json(row["source_ref"])) != row for row in selected["originals"]):
        raise ValueError("perpetual_review_original_changed_or_unavailable")
    return selected
