"""Original-only recipe conformance/ablation reader; never model-score assertions."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.commit import (
    PUBLICATION_SOURCE_ROLE,
    PUBLICATION_SOURCE_SCHEMA_V4,
)
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    RECIPE_COMPONENT_INPUT,
    RECIPE_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_REF,
    SpotResearchRecipeConsumptionInput,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_dispatch import (
    read_graph_recipe_selection_for_run_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    FrozenGraphRecipeSelectionV3,
)
from packages.kernel.decision_hub_kernel.application.research_quality_sidecar import (
    read_quality_sidecar_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_quality_view import (
    ResearchQualityViewV1,
    read_published_research_quality_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunCallRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.research_quality_records import (
    ResearchQualitySidecarRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_protocol import (
    ComponentOriginalRef,
    ComponentProtocolContext,
    ComponentProtocolResult,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import (
    ComponentManifest,
    ShadowTrial,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

RECIPE_PROTOCOL = {
    "schema_version": "component-research-recipe-originals-protocol.v1",
    "publication": "research-publication-source.v4",
    "recipe": "research-graph-recipe-selection.v3",
    "selection": "research-context-selection.v4",
    "quality": "research-quality-sidecar.v1",
    "ablation": "same-frozen-market-inputs-and-budget.v1",
    "minimum_paired_originals": 30,
    "minimum_independent_utc_days": 30,
    "cost": "same-run-completed-metered-provider-calls.v1",
    "scope": "report-conformance-and-cost-only-no-predictive-gain",
    "predictive_qualification": False,
    "trading_authority": False,
}
RECIPE_PROTOCOL_HASH = canonical_hash(RECIPE_PROTOCOL)


def _row_original(row: RunRoleOutputRecord, *, as_of: datetime) -> ComponentOriginalRef:
    raw = row.payload_json.encode()
    if hashlib.sha256(raw).hexdigest() != row.payload_hash or as_utc(row.created_at) > as_of:
        raise ValueError("component_recipe_original_changed_or_future")
    return ComponentOriginalRef(original_ref=row.output_id, original_hash=row.payload_hash)


def _material_request(bundle: FrozenResearchBundle) -> str:
    request = bundle.request.model_dump(mode="json")
    # Only execution identity varies; actual cutoff, evidence, context, routes,
    # deadlines, role configuration and budget remain material to an ablation.
    for key in ("request_id", "run_id", "event_id", "trigger_snapshot_id"):
        request.pop(key, None)
    return canonical_hash(request)


def _quality_metrics(view: ResearchQualityViewV1, required: set[str]) -> dict[str, Any]:
    decision = view.decision
    if decision is None or view.status != "available":
        raise ValueError("component_recipe_published_quality_required")
    retained = set(decision.retained_evidence_refs)
    return {
        "scope_id": decision.scope_id,
        "full_report_hash": decision.full_report_hash,
        "report_status": decision.report_status,
        "context_status": decision.context_status,
        "protected_required_count": len(required),
        "protected_missing_refs": sorted(required - retained),
        "critical_unresolved_count": sum(
            x.challenge_id in decision.unresolved_challenge_ids and x.severity == "critical"
            for x in decision.challenges
        ),
        "challenge_count": len(decision.challenges),
        "unresolved_challenge_ids": list(decision.unresolved_challenge_ids),
        "risk_count": len(decision.key_risks),
        "counter_evidence_refs": sorted(
            {ref for challenge in decision.challenges for ref in challenge.evidence_refs}
        ),
        "direction_status": decision.direction_status,
        "path_risk_status": decision.path_risk_status,
        "unknowns": list(decision.unknowns),
        "publication_mode": decision.publication_mode,
    }


class RecipeOriginalProtocol:
    kind: Literal["research_recipe"] = "research_recipe"
    protocol_id = "component-research-recipe-originals-protocol.v1"
    protocol_hash = RECIPE_PROTOCOL_HASH

    def evaluate(
        self,
        session: Session,
        *,
        manifest: ComponentManifest,
        trial: ShadowTrial,
        captures: tuple[dict[str, Any], ...],
        original_refs: tuple[ComponentOriginalRef, ...],
        as_of: datetime,
        context: ComponentProtocolContext,
    ) -> ComponentProtocolResult:
        at = aware_utc(as_of)
        connection = session.connection()
        native = connection.connection.driver_connection
        if (
            not isinstance(native, sqlite3.Connection)
            or not native.in_transaction
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_recipe_clean_physical_snapshot_required")
        before = native.total_changes
        if (
            manifest.kind != self.kind
            or manifest.component_ref != RECIPE_COMPONENT_REF
            or manifest.input_contract != RECIPE_COMPONENT_INPUT
            or manifest.output_contract != RECIPE_COMPONENT_OUTPUT
            or trial.protocol_hash != self.protocol_hash
            or trial.manifest_hash != manifest.payload_hash
        ):
            raise ValueError("component_recipe_protocol_identity_mismatch")
        pinned = {ref.original_ref: ref for ref in original_refs}
        if len(pinned) != len(original_refs):
            raise ValueError("component_recipe_duplicate_original")
        verified: dict[str, ComponentOriginalRef] = {}
        runs: dict[str, tuple[ResearchQualityViewV1, FrozenResearchBundle, dict[str, Any]]] = {}
        reasons, failures = [], []
        for capture in captures:
            intent = capture["intent"]
            raw = context.raw_archive.read(intent["input_ref"])
            if hashlib.sha256(raw).hexdigest() != intent["input_hash"]:
                raise ValueError("component_recipe_capture_raw_changed")
            inputs = SpotResearchRecipeConsumptionInput.model_validate_json(raw)
            cutoff = aware_utc(datetime.fromisoformat(intent["cutoff_at"]))
            known = aware_utc(datetime.fromisoformat(capture["known_at"]))
            if (
                inputs.scope_id != manifest.scope_id
                or not trial.start_at <= cutoff < trial.end_at
                or cutoff > known
                or known > at
                or capture["input_hash"] != canonical_hash(inputs.model_dump(mode="json"))
                or capture["output_hash"] != canonical_hash(capture["output"])
                or capture["output"] != inputs.definition.model_dump(mode="json")
            ):
                raise ValueError("component_recipe_capture_identity_changed")
            if inputs.run_id in runs:
                raise ValueError("component_recipe_duplicate_run_sample")
            view = read_published_research_quality_in_session(session, inputs.run_id, as_of=at)
            if view.status != "available":
                reasons.append("recipe_publication_original_unavailable")
                continue
            assert view.publication_ref is not None and view.quality_sidecar_id is not None
            publication = session.get(RunRoleOutputRecord, view.publication_ref.output_id)
            if (
                publication is None
                or publication.schema_version != PUBLICATION_SOURCE_SCHEMA_V4
                or publication.role != PUBLICATION_SOURCE_ROLE
            ):
                raise ValueError("component_recipe_publication_original_missing")
            reference = _row_original(publication, as_of=at)
            if pinned.get(reference.original_ref) != reference:
                raise ValueError("component_recipe_publication_not_pinned")
            verified[reference.original_ref] = reference
            selection = read_graph_recipe_selection_for_run_in_session(session, inputs.run_id)
            if not isinstance(selection, FrozenGraphRecipeSelectionV3):
                raise ValueError("component_recipe_selection_version_mismatch")
            bundle = read_research_bundle_in_session(session, inputs.run_id)
            scope = read_run_spot_admission_in_session(session, inputs.run_id)
            if (
                not isinstance(bundle, FrozenResearchBundle)
                or scope is None
                or scope.scope.scope_id != manifest.scope_id
                or scope.event_id != inputs.event_id
                or scope.admission_proof_hash != inputs.admission_hash
                or scope.effective_binding_hash != inputs.effective_binding_hash
                or scope.effective != inputs.effective
                or not scope.admitted_at <= cutoff <= bundle.request.pit_cutoff_at
                or bundle.pack.definition_hash != inputs.definition_hash
                or bundle.pack.definition.model_dump(mode="json") != capture["output"]
            ):
                raise ValueError("component_recipe_original_task_config_mismatch")
            sidecar = read_quality_sidecar_in_session(
                session, view.quality_sidecar_id, selection=selection.receipt
            )
            sidecar_row = session.get(ResearchQualitySidecarRecord, view.quality_sidecar_id)
            if sidecar_row is None or sidecar.recorded_at > at:
                raise ValueError("component_recipe_quality_original_future")
            verified[sidecar_row.sidecar_id] = ComponentOriginalRef(
                original_ref=sidecar_row.sidecar_id, original_hash=sidecar_row.payload_hash
            )
            verified[selection.reference.output_id] = ComponentOriginalRef(
                original_ref=selection.reference.output_id,
                original_hash=selection.reference.payload_hash,
            )
            metrics = _quality_metrics(
                view, set(selection.receipt.quality_context.additional_required_context_refs)
            )
            if metrics["scope_id"] != manifest.scope_id:
                raise ValueError("component_recipe_publication_scope_mismatch")
            runtime = json.loads(sidecar.prepared_runtime_json)
            metrics["prepared_budget"] = runtime["prepared_budget"]
            metrics["actual_context_fidelity_verified"] = (
                sidecar.quality_context.context_fidelity_verified
            )
            calls = list(
                session.scalars(select(RunCallRecord).where(RunCallRecord.run_id == inputs.run_id))
            )
            metered = bool(calls) and all(
                call.status in {"completed", "succeeded"}
                and call.finished_at is not None
                and as_utc(call.finished_at) <= at
                and call.total_tokens is not None
                and call.prompt_tokens is not None
                and call.completion_tokens is not None
                and call.total_tokens >= call.prompt_tokens + call.completion_tokens >= 0
                and call.provider_id is not None
                and call.model is not None
                and call.cost_status in {"measured", "actual"}
                and call.pricing_version is not None
                and call.cost_usd is not None
                and math.isfinite(call.cost_usd)
                and call.cost_usd >= 0
                for call in calls
            )
            for call in calls:
                call_body = {
                    column.name: (
                        getattr(call, column.name).isoformat()
                        if isinstance(getattr(call, column.name), datetime)
                        else getattr(call, column.name)
                    )
                    for column in RunCallRecord.__table__.columns
                }
                verified["run-call:" + call.call_id] = ComponentOriginalRef(
                    original_ref="run-call:" + call.call_id, original_hash=canonical_hash(call_body)
                )
            metrics["usage_call_count"] = len(calls)
            metrics["actual_usage_verified"] = metered
            metrics["actual_total_tokens"] = (
                sum(call.total_tokens or 0 for call in calls) if metered else None
            )
            actual_cost = (
                sum((Decimal(str(call.cost_usd)) for call in calls), Decimal(0))
                if metered
                else None
            )
            metrics["actual_cost_usd"] = str(actual_cost) if actual_cost is not None else None
            if actual_cost is not None and actual_cost > Decimal(
                str(runtime["prepared_budget"]["max_estimated_cost_usd"])
            ):
                failures.append("recipe_actual_cost_exceeds_frozen_budget")
            if (
                metrics["protected_missing_refs"]
                or metrics["critical_unresolved_count"]
                or metrics["report_status"] != "complete"
            ):
                failures.append("recipe_report_or_counterevidence_regression")
            if not metered:
                reasons.append("recipe_actual_metered_usage_unavailable")
            if not metrics["actual_context_fidelity_verified"]:
                reasons.append("recipe_actual_context_fidelity_unavailable")
            runs[inputs.run_id] = (view, bundle, metrics)
        pairs, blocks = [], set()
        for reference in original_refs:
            row = session.get(RunRoleOutputRecord, reference.original_ref)
            if row is None or row.role != "research_recipe_ablation":
                continue
            actual = _row_original(row, as_of=at)
            if actual != reference:
                raise ValueError("component_recipe_ablation_original_changed")
            body = json.loads(row.payload_json)
            if (
                row.schema_version != "research-recipe-ablation.v1"
                or body["trial_id"] != trial.trial_id
                or body["candidate_version_id"] != manifest.version_id
                or body["scope_id"] != manifest.scope_id
            ):
                raise ValueError("component_recipe_ablation_identity_mismatch")
            candidate = runs.get(body["candidate_run_id"])
            baseline = runs.get(body["baseline_run_id"])
            if (
                candidate is None
                or baseline is None
                or body["candidate_run_id"] == body["baseline_run_id"]
            ):
                reasons.append("recipe_ablation_publication_pair_unavailable")
                continue
            if _material_request(candidate[1]) != _material_request(baseline[1]):
                raise ValueError("component_recipe_ablation_frozen_inputs_differ")
            if candidate[2]["critical_unresolved_count"] > baseline[2][
                "critical_unresolved_count"
            ] or len(candidate[2]["protected_missing_refs"]) > len(
                baseline[2]["protected_missing_refs"]
            ):
                failures.append("recipe_same_input_ablation_quality_regression")
            pairs.append(reference.original_ref)
            blocks.add(candidate[1].request.pit_cutoff_at.date().isoformat())
            verified[reference.original_ref] = actual
        sample = len(pairs) >= 30 and len(blocks) >= 30
        cost = bool(runs) and all(value[2]["actual_usage_verified"] for value in runs.values())
        if not sample:
            reasons.append("fewer_than_30_same_input_pairs_and_independent_day_blocks")
        if not captures:
            reasons.append("recipe_shadow_execution_originals_unavailable")
        if (
            session.connection() is not connection
            or native.total_changes != before
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("component_recipe_protocol_wrote_or_snapshot_changed")
        reasons = sorted(set(reasons))
        status = "failed" if failures else "insufficient" if reasons else "passed"
        return ComponentProtocolResult(
            protocol_id=self.protocol_id,
            protocol_hash=self.protocol_hash,
            status=status,
            reasons=tuple(sorted(set([*failures, *reasons]))),
            original_refs=tuple(verified.values()),
            known_at=at,
            independent_blocks=len(blocks),
            sample_qualified=sample,
            cost_verified=cost,
            metrics={
                "published_original_count": len(runs),
                "same_input_ablation_pair_count": len(pairs),
                "independent_day_blocks": len(blocks),
                "runs": {key: value[2] for key, value in runs.items()},
                "predictive_gain": None,
                "probability_qualified": False,
                "trading_authority": False,
            },
        )
