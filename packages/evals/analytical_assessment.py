"""Read original research and archived labels without changing historical rows.

A caller physical transaction stays in place. Clean SQLite reads may use an
owned SELECT-only snapshot on the same actual Engine; all assessment fields
are read together. No execution, repair, publication, selection or calibration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Generator, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchReview, ResearchSessionResult
from packages.evals.daily_assessment import assess_daily_source_in_session
from packages.evals.input_receipts import verify_input_receipts
from packages.evals.prospective_observations import (
    _frozen_input,  # pyright: ignore[reportPrivateUsage] # Borrowed original lineage reader.
    verify_recorded_label,
)
from packages.kernel.decision_hub_kernel.application.analytical_outlook import (
    HORIZON_SECONDS,
    future_as_of_claim,
)
from packages.kernel.decision_hub_kernel.application.commit import (
    PUBLICATION_SOURCE_ROLE,
    _read_publication_source_in_session,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.fact_lineage import digest, run_scope
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    authenticated_graph_recipe_identity,
    parse_graph_final_projection_declaration,
    parse_graph_final_ref,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
    run_input_evidence_ids_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    Database,
    FactVersionRecord,
    ForecastRecord,
    PublicationDecisionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    StrategyCalibrationProductionRecord,
    StrategyProbabilityActivationRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import prepare_originals
from packages.provider_adapters.research.raw_capture import RawBodyArchive


class AssessmentSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: str
    output_id: str
    schema_version: str
    payload_hash: str


class AnalyticalAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["analytical-assessment.v1"] = "analytical-assessment.v1"
    assessed_at: AwareDatetime
    outlook_id: str
    run_id: str
    study_id: str | None
    source_status: Literal["verified", "unverifiable", "invalid"] = "unverifiable"
    source_reason: str | None = None
    method_status: Literal["verified", "unverifiable"] = "unverifiable"
    stable_recipe_hash: str | None = None
    method_scope: Literal[
        "hub_recipe_provider_unattested", "daily_rule_loaded_implementation"
    ] = "hub_recipe_provider_unattested"
    label_status: Literal[
        "not_due", "pending", "retry", "verified", "invalid", "unverifiable"
    ] = "unverifiable"
    label_reason: str | None = None
    observation_status: str | None = None
    benchmark: str | None = None
    selection_status: Literal["unchecked", "selected", "repeat", "unkeyed"] = "unchecked"
    directional_usable: bool = False
    verified_direction_correct: bool | None = None
    verified_directional_return_pct: float | None = None
    source_refs: list[AssessmentSourceRef] = Field(default_factory=list)
    label_capture_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: [
        "provider_configuration_unattested", "not_calibration",
        "study_identity_not_event_independence", "fixed_sample_selection_required",
        "minute_close_not_execution_pnl",
    ])


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _utc(value: datetime | None) -> datetime:
    result = as_utc(value)
    if result is None:
        raise ValueError("assessment_original_time_missing")
    return result


def _reason(error: Exception) -> str:
    text = str(error)
    return text if len(text) <= 100 and text.replace("_", "").isalnum() else (
        "assessment_original_invalid"
    )


def _label_capture_ids(value: object) -> list[str]:
    """Check the untyped existing label reader's receipt boundary."""
    if not isinstance(value, list):
        raise ValueError("assessment_label_capture_shape_invalid")
    identities: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("assessment_label_capture_shape_invalid")
        identity = item.get("capture_id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("assessment_label_capture_shape_invalid")
        identities.add(identity)
    return sorted(identities)


def stable_hub_recipe(definition: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
    """Derive a separate recipe from already authenticated V2/V3 definitions.

    This pure projection alone grants no provenance. In particular, per-Run
    Method hash, bundle/request hashes, prompts and absolute deadlines are absent.
    """
    keys = (
        "schema_version", "runtime_id", "runtime_version", "profile_ref", "prompt_policy_hash",
        "upstream_identity", "domain_pack_ref", "role_profile_ref", "execution_mode",
        "research_mode", "context_policy_hash", "output_schema_ref", "agent_preset",
        "permission_ref", "provider_configuration_attested", "evolution_pointer_consumed",
        "candidate_id", "research_policy_renderer_hash",
    )
    _require(definition.get("schema_version") in {
        "research-execution-method.v2", "research-execution-method.v3",
    }, "assessment_method_schema_unverifiable")
    policy_keys = (
        "gate_policy_hash", "rubric_hash", "compiler_version", "compiler_code_hash",
        "checker_version", "checker_code_hash", "checker_activation",
    )
    policy = definition["policy_binding"]
    resources = [compiled["gate_resource"], compiled["rubric_resource"]]
    resources += [item["profile_resource"] for item in compiled["role_rubric_bindings"]]
    resource_recipe = sorted(({
        key: item[key] for key in (
            "relative_ref", "source_schema_version", "raw_sha256", "canonical_definition_hash",
        )
    } for item in resources), key=lambda item: item["relative_ref"])
    recipe = {
        "schema_version": "analytical-hub-recipe.v1",
        "method": {key: definition[key] for key in keys},
        "allowed_capabilities": sorted(set(definition["allowed_capabilities"])),
        "execution_budget": definition["execution_budget"],
        "policy_implementation": {key: policy[key] for key in policy_keys},
        "policy_resources": resource_recipe,
    }
    if definition["schema_version"] == "research-execution-method.v3":
        selection = definition["context_selection_binding"]
        recipe["context_selection"] = {key: selection[key] for key in (
            "mode", "selection_schema", "ranking_rules_ref", "time_rule_ref",
        )}
    canonical_recipe(recipe)
    return recipe


def _verify_outlook(
    session: Session, outlook: AnalyticalOutlookRecord, run: RunRecord,
    persisted: ResearchSessionResult, declared: ResearchSessionResult,
) -> None:
    horizon = next((item for item in persisted.horizons if item.horizon == outlook.horizon), None)
    raw = next((item for item in declared.horizons if item.horizon == outlook.horizon), None)
    publication = session.get(
        PublicationDecisionRecord, outlook.artifact_id, populate_existing=True,
    )
    _require(horizon is not None and raw is not None and publication is not None,
             "assessment_outlook_horizon_missing")
    assert horizon is not None and raw is not None and publication is not None
    frozen_at = _utc(publication.decided_at)
    market, instrument = run_scope(session, run.run_id)
    method = f"ai-synthesis.v1:{persisted.profile_ref}:{persisted.runtime_version}"
    _require(
        outlook.outlook_id == "outlook_" + digest([run.run_id, method, outlook.horizon])[:40]
        and outlook.run_id == run.run_id and outlook.study_id == run.study_id
        and outlook.artifact_id == run.artifact_id
        and (outlook.market, outlook.instrument) == (market, instrument)
        and outlook.method_version == method
        and outlook.action == horizon.action
        and outlook.subjective_probability == horizon.subjective_probability
        and outlook.probability_status == horizon.probability_status
        and outlook.trigger == horizon.trigger and outlook.invalidation == horizon.invalidation
        and outlook.result_hash == research_result_content_hash(persisted)
        and outlook.research_quality == persisted.final_coverage.status
        and json.loads(outlook.evidence_refs_json) == list(horizon.evidence_refs)
        and json.loads(outlook.raw_payload_json) == {
            "horizon": raw.model_dump(mode="json"),
            "thesis": declared.causal_case.thesis if declared.causal_case else "",
            "model_finished_at": declared.finished_at.isoformat(),
        }
        and _utc(outlook.window_start_at) == frozen_at
        and _utc(outlook.recorded_at) == frozen_at
        and _utc(outlook.window_end_at) == frozen_at + timedelta(
            seconds=HORIZON_SECONDS[outlook.horizon],
        ), "assessment_outlook_original_mismatch",
    )
    # Recheck the producer's frozen input projection. Today's market enablement
    # cannot erase a recorded exclusion or certify an historical method.
    evidence = {row.evidence_id: row for row in session.scalars(
        select(ResearchEvidenceRecord).where(ResearchEvidenceRecord.run_id == run.run_id)
        .execution_options(populate_existing=True)
    )}
    facts = session.scalars(select(ResearchFactRecord).where(
        ResearchFactRecord.run_id == run.run_id,
    ).execution_options(populate_existing=True)).all()
    refs = {row.fact_id: row.fact_version_id for row in session.scalars(
        select(RunInputRefRecord).where(RunInputRefRecord.run_id == run.run_id)
        .execution_options(populate_existing=True)
    )}
    selected = [evidence[identity] for identity in horizon.evidence_refs if identity in evidence]
    versions = sorted({
        refs[fact.fact_id] for fact in facts
        if fact.evidence_id in horizon.evidence_refs and fact.quality == "accepted"
        and fact.fact_id in refs
    })
    reasons: set[str] = set()
    if market == "unknown" or instrument == "unknown":
        reasons.add("scope_unknown")
    if horizon.action == "no_trade":
        reasons.add("no_directional_hypothesis")
    if not any(row.quality == "accepted" for row in selected) or not versions:
        reasons.add("accepted_versioned_inputs_missing")
    if any(_utc(row.received_at) > frozen_at or _utc(row.observed_at) > frozen_at
           for row in selected):
        reasons.add("input_after_freeze")
    for identity in versions:
        version = session.get(FactVersionRecord, identity, populate_existing=True)
        if version is None:
            reasons.add("fact_version_missing")
        elif _utc(version.known_at) > frozen_at or _utc(version.observed_at) > frozen_at:
            reasons.add("input_after_freeze")
    trigger_ids = run_input_evidence_ids_in_session(session, run.run_id)
    if any(identity not in evidence and identity not in trigger_ids
           for identity in horizon.evidence_refs):
        reasons.add("unresolved_evidence_reference")
    if horizon.expires_at <= frozen_at:
        reasons.add("model_hypothesis_expired")
    if declared.finished_at > frozen_at:
        reasons.add("model_finished_after_freeze")
    thesis = declared.causal_case.thesis if declared.causal_case else ""
    if future_as_of_claim(thesis, frozen_at):
        reasons.add("future_as_of_claim")
    saved_reasons = json.loads(outlook.exclusion_reasons_json)
    _require(isinstance(saved_reasons, list) and all(isinstance(item, str)
             for item in saved_reasons), "assessment_outlook_exclusions_invalid")
    if "observation_market_not_enabled" in saved_reasons:
        reasons.add("observation_market_not_enabled")
    data_as_of = max((_utc(row.received_at) for row in selected), default=None)
    _require(
        json.loads(outlook.fact_version_refs_json) == versions
        and as_utc(outlook.data_as_of) == data_as_of
        and saved_reasons == sorted(reasons)
        and outlook.cohort == "prospective"
        and outlook.eligibility == ("excluded" if reasons else "eligible"),
        "assessment_outlook_inputs_mismatch",
    )


@dataclass(frozen=True)
class _SourceOriginals:
    rows: tuple[Any, ...]
    task: tuple[tuple[Any, str], str, str]
    preparation_type: Any
    preparation: tuple[tuple[str, Any, str], ...]
    projection: tuple[Any, str]
    declared: tuple[Any, str]
    persisted: tuple[Any, str]
    source_json: str

    @classmethod
    def capture(cls, run: Any, roles: dict[str, Any], task: Any, prepared: Any,
                projection: Any, declared: Any, saved: Any, persisted: Any,
                source_body: dict[str, Any]) -> _SourceOriginals:
        def row(value: Any) -> tuple[Any, tuple[Any, ...]]:
            return type(value), tuple((column.key, getattr(value, column.key))
                                      for column in value.__table__.columns)
        def model(value: Any) -> tuple[Any, str]:
            return type(value), value.model_dump_json()
        return cls((row(run), tuple((key, row(value)) for key, value in roles.items()), row(saved)),
                   (model(task[0]), task[1], task[2]), type(prepared), tuple((name, *model(getattr(prepared, name)))
                       for name in ('method', 'link', 'prompt', 'execution', 'integrity')),
                   model(projection), model(declared), model(persisted), json.dumps(source_body))

    def digest(self) -> tuple[str, int]:
        # Private data fingerprint, never an authority verdict. Type identities
        # also prevent swapping a same-named model inside the material object.
        run, roles, saved = self.rows
        def row(value):
            kind, values = value
            return [id(kind), [(name, value.isoformat() if isinstance(value, datetime) else value)
                              for name, value in values]]
        body = json.dumps([row(run), [(name, row(value)) for name, value in roles], row(saved),
            [id(self.task[0][0]), self.task[0][1], self.task[1], self.task[2]], id(self.preparation_type),
            [(name, id(kind), body) for name, kind, body in self.preparation],
            [id(self.projection[0]), self.projection[1]], [id(self.declared[0]), self.declared[1]],
            [id(self.persisted[0]), self.persisted[1]], self.source_json],
            sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest(), len(body)

    def fresh(self) -> tuple[Any, ...]:
        def row(value: tuple[Any, tuple[Any, ...]]) -> Any:
            kind, values = value
            return kind(**dict(values))
        def model(value: tuple[Any, str]) -> Any:
            kind, body = value
            return kind.model_validate_json(body)
        _require_source_original_methods()
        run, roles, saved = self.rows
        prepared = self.preparation_type(**{name: kind.model_validate_json(body)
                                            for name, kind, body in self.preparation})
        return (row(run), {name: row(value) for name, value in roles}, (model(self.task[0]), self.task[1], self.task[2]), prepared,
                model(self.projection), model(self.declared), row(saved), model(self.persisted),
                json.loads(self.source_json))


_SOURCE_ORIGINAL_METHODS = {
    'capture': (_SourceOriginals.capture.__func__, _SourceOriginals.capture.__func__.__code__),
    'fresh': (_SourceOriginals.fresh, _SourceOriginals.fresh.__code__),
    'digest': (_SourceOriginals.digest, _SourceOriginals.digest.__code__),
}


def _require_source_original_methods() -> None:
    for name, (function, code) in _SOURCE_ORIGINAL_METHODS.items():
        current = getattr(_SourceOriginals, name)
        current = getattr(current, '__func__', current)
        if current is not function or current.__code__ is not code:
            raise ValueError('calibration_registration_material_implementation_changed')


def _read_source_originals(
    session: Session, outlook: AnalyticalOutlookRecord, assessed_at: datetime,
    *, archive: RawBodyArchive | None = None,
) -> tuple[Any, ...]:
    run = session.get(RunRecord, outlook.run_id, populate_existing=True)
    _require(run is not None, "assessment_run_missing")
    assert run is not None
    roles = {row.role: row for row in session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run.run_id,
        RunRoleOutputRecord.role.in_((
            "research_request_pack", "research_task_request", "research_compiled_policy",
            "research_execution_method", "research_graph_final", PUBLICATION_SOURCE_ROLE,
            "independent_reviewer",
        )),
    ).execution_options(populate_existing=True))}
    required = {
        "research_request_pack", "research_task_request", "research_compiled_policy",
        "research_execution_method", "research_graph_final", PUBLICATION_SOURCE_ROLE,
        "independent_reviewer",
    }
    _require(required <= roles.keys(), "assessment_source_receipts_missing")
    _require(roles["research_execution_method"].schema_version in {
        "research-execution-method.v2", "research-execution-method.v3",
    }, "assessment_method_schema_unverifiable")
    for row in roles.values():
        _require(row.payload_hash == hashlib.sha256(row.payload_json.encode()).hexdigest()
                 and _utc(row.created_at) <= assessed_at, "assessment_source_receipt_invalid")
    task = read_task_request(session, run, assessed_at=assessed_at)
    _require(task is not None, "assessment_saved_task_missing")
    prepared = read_prepared_execution_in_session(session, run.run_id)
    source_body = json.loads(roles[PUBLICATION_SOURCE_ROLE].payload_json)
    reference = parse_graph_final_ref(source_body["graph_final_ref"])
    # Decode the saved declaration here; decoding never authenticates it.
    # The publication reader below independently reads and authenticates the
    # complete Graph chain and requires this exact projected result. Avoid
    # authenticating that same chain twice just to reconstruct the declaration.
    projection = parse_graph_final_projection_declaration(
        roles["research_graph_final"].payload_json,
        schema_version=roles["research_graph_final"].schema_version,
    )
    graph_row = roles["research_graph_final"]
    _require(
        projection.run_id == run.run_id
        and projection.schema_version == graph_row.schema_version
        and (reference.row.output_id, reference.row.role, reference.row.schema_version,
             reference.row.payload_hash, _utc(reference.row.recorded_at))
        == (graph_row.output_id, graph_row.role, graph_row.schema_version,
            graph_row.payload_hash, _utc(graph_row.created_at)),
        "assessment_graph_reference_mismatch",
    )
    review = ResearchReview.model_validate_json(roles["independent_reviewer"].payload_json)
    # Reconstruct only the lawful review addition, before saved calibration
    # normalization. The publication reader authenticates its producer/body.
    declared = projection.projected_result.model_copy(update={"reviews": [review]})
    # The existing caller-provided server archive is confined to this source
    # read. It supplies bytes, never a pass verdict or database permission.
    marker = object()
    previous_archive = session.info.get("kernel_calibration_archive", marker)
    if archive is not None:
        session.info["kernel_calibration_archive"] = archive
    try:
        _read_publication_source_in_session(
            session, run=run, reference=reference, declared=declared,
            expected_lease_owner=None, candidate=None, gate=None,
        )
    finally:
        if previous_archive is marker:
            session.info.pop("kernel_calibration_archive", None)
        else:
            session.info["kernel_calibration_archive"] = previous_archive
    saved = session.get(ResearchResultRecord, run.run_id, populate_existing=True)
    _require(saved is not None, "assessment_saved_result_missing")
    assert saved is not None
    persisted = ResearchSessionResult.model_validate_json(saved.payload_json)
    return run, roles, task, prepared, projection, declared, saved, persisted, source_body


def _source(
    session: Session, outlook: AnalyticalOutlookRecord, assessed_at: datetime,
    *, archive: RawBodyArchive | None = None,
) -> tuple[str, list[AssessmentSourceRef], bool]:
    # This private port exists only inside the clean registration transaction.
    # Outside it the complete existing source reader still runs every time.
    from packages.kernel.decision_hub_kernel.application.calibration import (
        _registration_materials, _registration_source_originals, _store_registration_source_originals,
    )
    originals = _registration_source_originals(session, outlook.run_id, assessed_at, archive)
    if originals is None:
        values = _read_source_originals(session, outlook, assessed_at, archive=archive)
        if _registration_materials(session) is not None:
            _store_registration_source_originals(session, outlook.run_id, assessed_at, archive, _SourceOriginals.capture(*values))
    else:
        values = originals.fresh()
    run, roles, task, prepared, projection, declared, saved, persisted, source_body = values
    _verify_outlook(session, outlook, run, persisted, declared)
    _require(_utc(run.finished_at) <= assessed_at, "assessment_publication_after_assessment")
    # These authenticated V2/V3 results retain the Runtime's original method
    # identity through Graph projection, review and calibration normalization.
    method_identity = (prepared.method.definition.profile_ref,
                       prepared.method.definition.runtime_version)
    _require((declared.profile_ref, declared.runtime_version) == method_identity,
             "assessment_declared_method_identity_mismatch")
    _require((persisted.profile_ref, persisted.runtime_version) == method_identity,
             "assessment_persisted_method_identity_mismatch")
    compiled = json.loads(roles["research_compiled_policy"].payload_json)["compiled"]
    definition = prepared.method.definition.model_dump(mode="json")
    stable_recipe = stable_hub_recipe(definition, compiled)
    # The publication reader has authenticated this exact saved projection
    # and its complete Graph chain. The identity helper only derives content;
    # decoding a projection alone cannot grant source or method authority.
    graph_recipe = authenticated_graph_recipe_identity(projection)
    if graph_recipe is not None:
        stable_recipe["graph_recipe"] = graph_recipe
    stable = recipe_hash(stable_recipe)
    refs = [AssessmentSourceRef(
        role=row.role, output_id=row.output_id, schema_version=row.schema_version,
        payload_hash=row.payload_hash,
    ) for row in sorted(roles.values(), key=lambda row: row.role)]
    if source_body["schema_version"] in {"research-publication-source.v2", "research-publication-source.v3"}:
        # The publication reader above independently re-executed both phases.
        # Include their exact immutable row identities, never a caller pass.
        for phase in ("finalization", "commit"):
            row = session.get(
                RunRoleOutputRecord, source_body["policy_checks"][phase]["receipt_id"],
                populate_existing=True,
            )
            if row is None:
                raise ValueError("assessment_policy_receipt_missing")
            refs.append(AssessmentSourceRef(
                role=row.role, output_id=row.output_id,
                schema_version=row.schema_version, payload_hash=row.payload_hash,
            ))
    if source_body["schema_version"] == "research-publication-source.v3":
        # The publication reader above has freshly authenticated both numeric
        # phases and their original production/activation, not saved flags.
        added = {(item.role, item.output_id) for item in refs}
        for phase in ("finalization", "commit"):
            reference_body = source_body["kernel_values"][phase]
            row = session.get(RunRoleOutputRecord, reference_body["receipt_id"], populate_existing=True)
            if (row is None or row.run_id != run.run_id or row.role != "research_probability_value_" + phase
                    or row.schema_version != "research-kernel-probability-values.v2"
                    or row.payload_hash != reference_body["payload_hash"]
                    or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash):
                raise ValueError("assessment_probability_receipt_missing_or_changed")
            refs.append(AssessmentSourceRef(role=row.role, output_id=row.output_id,
                                           schema_version=row.schema_version, payload_hash=row.payload_hash))
            body = json.loads(row.payload_json)
            first = session.get(RunRoleOutputRecord, body["first_binding_id"], populate_existing=True)
            if (first is None or first.run_id != run.run_id or first.role != "research_probability_binding"
                    or first.payload_hash != body["first_binding_payload_hash"]
                    or hashlib.sha256(first.payload_json.encode()).hexdigest() != first.payload_hash):
                raise ValueError("assessment_probability_first_binding_changed")
            if (first.role, first.output_id) not in added:
                refs.append(AssessmentSourceRef(role=first.role, output_id=first.output_id,
                                               schema_version=first.schema_version, payload_hash=first.payload_hash))
                added.add((first.role, first.output_id))
            if body["activation_id"] is not None:
                activation = session.get(StrategyProbabilityActivationRecord, body["activation_id"], populate_existing=True)
                if (activation is None or activation.payload_hash != body["activation_payload_hash"]
                        or hashlib.sha256(activation.payload_json.encode()).hexdigest() != activation.payload_hash):
                    raise ValueError("assessment_probability_activation_changed")
                if ("strategy_probability_activation", activation.activation_id) not in added:
                    refs.append(AssessmentSourceRef(role="strategy_probability_activation", output_id=activation.activation_id,
                                                   schema_version="probability-activation.v2", payload_hash=activation.payload_hash))
                    added.add(("strategy_probability_activation", activation.activation_id))
            for slot in body["slots"]:
                kernel = slot["kernel"]
                if kernel is None:
                    continue
                production = session.get(StrategyCalibrationProductionRecord, kernel["production_id"], populate_existing=True)
                if (production is None or production.payload_hash != kernel["production_payload_hash"]
                        or hashlib.sha256(production.payload_json.encode()).hexdigest() != production.payload_hash):
                    raise ValueError("assessment_probability_production_changed")
                if ("strategy_calibration_production", production.production_id) not in added:
                    refs.append(AssessmentSourceRef(role="strategy_calibration_production", output_id=production.production_id,
                                                   schema_version="strategy-calibration-production.v2", payload_hash=production.payload_hash))
                    added.add(("strategy_calibration_production", production.production_id))
        for reference_body in source_body["kernel_forecasts"]:
            forecast = session.get(ForecastRecord, reference_body["forecast_id"], populate_existing=True)
            if forecast is None:
                raise ValueError("assessment_kernel_forecast_missing")
            row_body = {column.key: (_utc(value).isoformat() if isinstance(value, datetime) else value)
                        for column in forecast.__table__.columns
                        for value in (getattr(forecast, column.key),)}
            if recipe_hash(row_body) != reference_body["payload_hash"]:
                raise ValueError("assessment_kernel_forecast_changed")
            # The V3 publication reader authenticated its artifact, horizon and
            # mathematical value; this reference adds provenance, not authority.
            refs.append(AssessmentSourceRef(role="kernel_forecast", output_id=forecast.forecast_id,
                                           schema_version="kernel-forecast-reference.v1",
                                           payload_hash=reference_body["payload_hash"]))
    refs.append(AssessmentSourceRef(
        role="research_result", output_id=run.run_id,
        schema_version=saved.schema_version, payload_hash=saved.payload_hash,
    ))
    forward = definition["execution_mode"] == "live" and (
        definition["research_mode"] == "current_decision"
    )
    return stable, refs, forward


def _assess_outlook_in_snapshot(
    session: Session, outlook_id: str, *, assessed_at: datetime,
    archive: RawBodyArchive | None = None,
) -> AnalyticalAssessment:
    """Retain ordinary rows, expose authenticated provenance and independently rechecked labels."""
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("assessment_time_timezone_required")
    now = _utc(assessed_at)
    with session.no_autoflush:
        outlook = session.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
        if outlook is None:
            raise KeyError(outlook_id)
        result = AnalyticalAssessment(
            assessed_at=now, outlook_id=outlook_id,
            run_id=outlook.run_id, study_id=outlook.study_id,
        )
        if outlook.method_family == "daily_rule":
            daily = assess_daily_source_in_session(
                session, outlook, assessed_at=now, archive=archive,
            )
            result = result.model_copy(update={
                "source_status": daily.source_status, "source_reason": daily.reason,
                "method_status": ("verified" if daily.method_status == "verified"
                                  else "unverifiable"),
                "method_scope": "daily_rule_loaded_implementation",
                "stable_recipe_hash": daily.stable_recipe_hash,
                "source_refs": [AssessmentSourceRef(
                    role=ref.role, output_id=ref.output_id, schema_version=ref.schema_version,
                    payload_hash=ref.payload_hash,
                ) for ref in daily.refs],
                "limitations": [item for item in result.limitations
                                if item != "provider_configuration_unattested"]
                                + list(daily.limitations),
            })
        elif outlook.method_family != "ai_synthesis":
            result = result.model_copy(update={
                "source_reason": "analytical_method_original_assessment_not_implemented",
            })
        else:
            try:
                stable, refs, forward = _source(session, outlook, now, archive=archive)
            except (
                ValueError, PermissionError, KeyError, TypeError,
                OSError, IndexError, AttributeError,
            ) as exc:
                reason = _reason(exc)
                unverifiable = reason in {
                    "assessment_source_receipts_missing", "assessment_saved_task_missing",
                    "assessment_method_schema_unverifiable", "assessment_run_missing",
                    "research_graph_recipe_current_implementation_unavailable",
                    "research_policy_checker_current_implementation_unavailable",
                }
                result = result.model_copy(update={
                    "source_status": "unverifiable" if unverifiable else "invalid",
                    "source_reason": (
                        "assessment_loaded_verifier_source_changed"
                        if reason in {"research_graph_recipe_current_implementation_unavailable",
                                      "research_policy_checker_current_implementation_unavailable"}
                        else reason
                    ),
                    "limitations": result.limitations + (
                        [reason]
                        if reason in {"research_graph_recipe_current_implementation_unavailable",
                                      "research_policy_checker_current_implementation_unavailable"}
                        else []
                    ),
                })
            else:
                result = result.model_copy(update={
                    "source_status": "verified", "method_status": "verified",
                    "stable_recipe_hash": stable, "source_refs": refs,
                    "limitations": result.limitations + ([] if forward else [
                        "non_live_research_source",
                    ]),
                })
        observation = session.get(AnalyticalObservationRecord, outlook_id, populate_existing=True)
        if observation is not None:
            result = result.model_copy(update={
                "observation_status": observation.status, "benchmark": observation.benchmark,
            })
        if _utc(outlook.window_end_at) > now:
            return result.model_copy(update={"label_status": "not_due"})
        if observation is None:
            return result.model_copy(update={"label_reason": "assessment_observation_missing"})
        if observation.status != "completed":
            return result.model_copy(update={
                "label_status": (observation.status if observation.status in {"pending", "retry"}
                                 else "unverifiable"),
                "label_reason": observation.error_code or (
                    f"assessment_observation_{observation.status}"
                    if observation.status not in {"pending", "retry"} else None
                ),
            })
        if archive is None:
            return result.model_copy(update={"label_reason": "assessment_raw_archive_unavailable"})
        try:
            label = verify_recorded_label(session, outlook, observation, archive, now)
            capture_ids = _label_capture_ids(label["captures"])
        except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as exc:
            return result.model_copy(update={
                "label_status": "invalid", "label_reason": _reason(exc),
            })
        input_verified = True
        try:
            frozen = _frozen_input(session, outlook)
            verify_input_receipts(session, {
                **frozen, "cutoff_at": frozen["recorded_at"], "facts": frozen["facts"],
            }, archive)
        except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as exc:
            input_verified = False
            result = result.model_copy(update={
                "label_reason": _reason(exc),
                "limitations": result.limitations + ["frozen_input_unverified"],
            })
        usable = (
            result.source_status == "verified" and result.method_status == "verified"
            and result.stable_recipe_hash is not None
            and input_verified
            and "non_live_research_source" not in result.limitations
            and outlook.cohort == "prospective" and outlook.eligibility == "eligible"
            and _utc(outlook.recorded_at) == _utc(outlook.window_start_at)
            and outlook.data_as_of is not None
            and _utc(outlook.data_as_of) <= _utc(outlook.window_start_at)
            and outlook.action in {"long", "short"}
        )
        return result.model_copy(update={
            "label_status": "verified", "directional_usable": usable,
            "verified_direction_correct": label["direction_correct"],
            "verified_directional_return_pct": label["directional_return_pct"],
            "label_capture_ids": capture_ids,
        })


class _SnapshotTransferUnavailable(Exception):
    """No optimization authority; retain the caller's original read semantics."""


class _AssessmentReadDatabase(Database):
    """A session provider on the actual Engine, with no database initialization.

    Only prepare_originals borrows it. Its authorizer/rollback/close belong to
    the distinct native connection, never the caller's connection or UOW.
    """

    def __init__(self, engine: Engine, caller_native: sqlite3.Connection) -> None:
        self.engine = engine
        self.caller_native = caller_native

    @contextmanager
    def session(self) -> Generator[Session]:
        with Session(self.engine, autoflush=False, expire_on_commit=False) as inner:
            connection = inner.connection()
            native = connection.connection.driver_connection
            # StaticPool and a bound Connection cannot transfer a snapshot.
            # This check precedes prepare_originals' BEGIN and authorizer.
            if (connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection)
                    or native is self.caller_native or native.in_transaction):
                raise _SnapshotTransferUnavailable()
            try:
                yield inner
            finally:
                inner.rollback()


def _outlook_columns(outlook: AnalyticalOutlookRecord, columns: tuple[str, ...]) -> tuple[object, ...]:
    state = inspect(outlook)
    if any(key not in state.dict for key in columns):
        raise ValueError("assessment_caller_outlook_scope_changed")
    # SQLite returns naive UTC while a retained producer object can be aware.
    # Compare exact UTC instants, with no tolerance or string normalization.
    return tuple(as_utc(value) if isinstance(value := state.dict[key], datetime) else value
                 for key in columns)


def assess_outlook_in_session(
    session: Session, outlook_id: str, *, assessed_at: datetime,
    archive: RawBodyArchive | None = None,
) -> AnalyticalAssessment:
    """Assess every source/label/input in one snapshot, without changing the caller.

    Pending writes, an existing physical transaction, a Connection bind, or a
    shared native connection use the original path. Loaded Outlook columns
    are only a consistency constraint, never provenance. A stale/partially
    loaded Outlook is rejected rather than silently mixing two scopes.
    """
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("assessment_time_timezone_required")
    if session.new or session.dirty or session.deleted:
        return _assess_outlook_in_snapshot(session, outlook_id, assessed_at=assessed_at, archive=archive)
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        return _assess_outlook_in_snapshot(session, outlook_id, assessed_at=assessed_at, archive=archive)
    with session.no_autoflush:
        caller_connection = session.connection()
        native = caller_connection.connection.driver_connection
        if (caller_connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection)
                or native.in_transaction):
            return _assess_outlook_in_snapshot(session, outlook_id, assessed_at=assessed_at, archive=archive)
        # Perform only the original scope lookup here. No source, observation,
        # label or input is read outside the owned assessment snapshot.
        outlook = session.get(AnalyticalOutlookRecord, outlook_id)
        if outlook is None:
            raise KeyError(outlook_id)
        state = inspect(outlook)
        columns = tuple(column.key for column in AnalyticalOutlookRecord.__table__.columns)
        if any(key not in state.dict for key in columns):
            raise ValueError("assessment_caller_outlook_scope_changed")
        caller_columns = _outlook_columns(outlook, columns)
        caller_transaction, caller_nested = session.get_transaction(), session.get_nested_transaction()
        caller_changes = native.total_changes

        def assessment(inner: Session) -> AnalyticalAssessment:
            fresh = inner.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
            if fresh is None or _outlook_columns(fresh, columns) != caller_columns:
                raise ValueError("assessment_caller_outlook_scope_changed")
            return _assess_outlook_in_snapshot(inner, outlook_id, assessed_at=assessed_at, archive=archive)

        try:
            result = prepare_originals(_AssessmentReadDatabase(bind, native), assessment).value
        except _SnapshotTransferUnavailable:
            return _assess_outlook_in_snapshot(session, outlook_id, assessed_at=assessed_at, archive=archive)
        if (session.connection() is not caller_connection or native.in_transaction
                or native.total_changes != caller_changes
                or session.get_transaction() is not caller_transaction
                or session.get_nested_transaction() is not caller_nested
                or session.new or session.dirty or session.deleted
                or _outlook_columns(outlook, columns) != caller_columns):
            raise ValueError("assessment_caller_snapshot_changed")
        return result
