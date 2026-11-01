from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    Direction,
    Forecast,
    GateStatus,
    HorizonDecision,
    ResearchSessionResult,
    ResearchStrategyReceipt,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.analytical_outlook import freeze_outlooks
from packages.kernel.decision_hub_kernel.application.calibration import (
    StrategyCalibrationService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_report_summary import (
    render_research_report_summary,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    has_valid_accepted_independent_review,
    reject_legacy_native_publication_in_session,
    require_publication_writer,
    research_result_content_hash,
    run_input_evidence_ids_in_session,
    verify_graph_review_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.application.study import record_revision
from packages.kernel.decision_hub_kernel.decision.gate import GateResult, evaluate_gate
from packages.kernel.decision_hub_kernel.decision.horizon_contract import (
    HORIZON_CONTRACT_VERSION,
    validate_horizon_contract,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ForecastRecord,
    GateDecisionRecord,
    OutboxRecord,
    PublicationDecisionRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StrategyCalibrationRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    ExecutionBoundary,
    PreparedOriginals,
    begin_immediate,
    current_result_consumer_boundary,
    prepare_originals,
)

if TYPE_CHECKING:
    from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
        ResearchPolicyCheckReceipt,
    )

    from .research_graph_binding import FrozenGraphFinal, GraphFinalProjectionRef
    from .research_policy_checker import (
        _PreparedCommitPolicy,  # pyright: ignore[reportPrivateUsage]
    )
    from .research_probability_binding import FrozenProbabilityValues

PUBLICATION_SOURCE_ROLE = "research_publication_source"
PUBLICATION_SOURCE_SCHEMA = "research-publication-source.v1"
PUBLICATION_SOURCE_SCHEMA_V2 = "research-publication-source.v2"
PUBLICATION_SOURCE_SCHEMA_V3 = "research-publication-source.v3"
PUBLICATION_SOURCE_SCHEMA_V4 = "research-publication-source.v4"
PUBLICATION_SOURCE_SCHEMA_V5 = "research-publication-source.v5"
PUBLICATION_SOURCE_SCHEMA_V6 = "research-publication-source.v6"


@dataclass(frozen=True)
class _PreparedPublication:
    original: FrozenGraphFinal
    normalized: ResearchSessionResult
    review_binding: tuple[str, str, str, str] | None
    calibration_binding: tuple[tuple[str, ...], ...]
    canonical_candidate: dict[str, object]
    canonical_gate: GateResult
    final_gate: GateResult
    published: list[HorizonDecision]
    trigger_refs: set[str]
    native_binding: tuple[str, str, str, str] | None
    boundary: ExecutionBoundary
    kernel_values: FrozenProbabilityValues | None
    policy_inputs: _PreparedCommitPolicy | None
    perpetual_binding: dict[str, object] | None = None
    perpetual_assumptions_json: str | None = None


def _perpetual_content_at_decision(
    operation: Callable[..., Any],
    candidate: dict[str, Any],
    binding: dict[str, Any],
    *,
    decision_at: datetime,
    assumptions_json: str | None,
) -> Any:
    """Old retained namespaces must receive the exact old call without a new keyword."""
    if assumptions_json is None:
        return operation(candidate, binding, decision_at=decision_at)
    return operation(
        candidate, binding, decision_at=decision_at,
        assessment_assumptions_json=assumptions_json,
    )


class CommitDecisionService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock
        self.research_observability = ResearchObservabilityService(database, clock=clock)

    @contextmanager
    def _publication_originals_scope(
        self,
        run_id: str,
        prepare: Callable[[Session], _PreparedPublication | tuple[str, GateResult]],
    ) -> Iterator[PreparedOriginals[_PreparedPublication | tuple[str, GateResult]]]:
        # Own this call's complete byte proof through actual COMMIT/rollback.
        # Held immutable files authorize no Run, lease or publication by themselves.
        with callback_source_lease_scope() as source_lease:
            def authenticate(session: Session) -> _PreparedPublication | tuple[str, GateResult]:
                value = prepare(session)
                if source_lease is not None:
                    source_lease.extend_authenticated_selection(session, run_id)
                return value

            yield prepare_originals(self.database, authenticate, source_lease=source_lease)

    def _commit_v2(
        self,
        run_id: str,
        event_id: str,
        result: ResearchSessionResult | None,
        *,
        graph_final_ref: GraphFinalProjectionRef | None,
        expected_lease_owner: str | None,
        candidate: dict[str, object] | None = None,
        gate: GateResult | None = None,
        horizons: list[HorizonDecision] | None = None,
        raw_result: ResearchSessionResult | None = None,
        forecast_strategy_version: str | None = None,
        calibration_receipt_ids: dict[str, str] | None = None,
    ) -> tuple[str, GateResult]:
        from .research_graph_binding import (
            GraphFinalProjectionRef,
            _permission,  # pyright: ignore[reportPrivateUsage]
            read_graph_final_in_session,
        )
        from .research_policy_checker import (
            _commit_policy_values,  # pyright: ignore[reportPrivateUsage]
            _prepare_commit_policy_inputs_in_session,  # pyright: ignore[reportPrivateUsage]
            _store_commit_policy_values_in_session,  # pyright: ignore[reportPrivateUsage]
            _verify_commit_policy_values_in_session,  # pyright: ignore[reportPrivateUsage]
            policy_phase_reference,
        )

        if graph_final_ref is None:
            raise ValueError("research_graph_publication_reference_required")
        if not isinstance(graph_final_ref, GraphFinalProjectionRef):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("research_graph_publication_reference_invalid")
        graph_final_ref = GraphFinalProjectionRef.model_validate_json(
            graph_final_ref.model_dump_json(),
        )
        if result is None:
            raise ValueError("research_graph_publication_result_required")
        result = ResearchSessionResult.model_validate(result.model_dump(mode="json"))
        raw_result = (
            ResearchSessionResult.model_validate(raw_result.model_dump(mode="json"))
            if raw_result is not None
            else None
        )
        candidate, gate = deepcopy(candidate), deepcopy(gate)
        horizons, calibration_receipt_ids = deepcopy(horizons), deepcopy(calibration_receipt_ids)
        declared = raw_result if raw_result is not None else result

        def prepare(session: Session) -> _PreparedPublication | tuple[str, GateResult]:
            nonlocal forecast_strategy_version, calibration_receipt_ids
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None or run.event_id != event_id:
                raise ValueError("research_graph_publication_run_invalid")
            if run.artifact_id:
                # This is original-data replay, never permission to fill a
                # missing Result or mint a replacement publication source.
                return _read_publication_source_in_session(
                    session,
                    run=run,
                    reference=graph_final_ref,
                    declared=declared,
                    expected_lease_owner=expected_lease_owner,
                    candidate=candidate,
                    gate=gate,
                )
            if not expected_lease_owner:
                raise PermissionError("research_graph_publication_owner_required")
            _permission(session, run_id, expected_lease_owner)
            original = read_graph_final_in_session(
                session,
                run_id=run_id,
                reference=graph_final_ref,
                expected_lease_owner=expected_lease_owner,
                require_current_permission=False,
            )
            boundary = current_result_consumer_boundary(session, run_id, original=original)
            if (
                research_result_content_hash(declared)
                != original.receipt.projected_result_content_hash
            ):
                raise ValueError("research_graph_declared_final_result_mismatch")
            if original.receipt.schema_version == "research-perpetual-graph-final.v1":
                from .perpetual_research_report import prepare_content
                content = prepare_content(session, original, declared)
                if forecast_strategy_version is not None or calibration_receipt_ids not in (None, {}):
                    raise ValueError("perpetual_report_legacy_calibration_forbidden")
                if candidate is not None and candidate != content.candidate or gate is not None and gate != content.gate or horizons not in (None, []):
                    raise ValueError("perpetual_report_caller_projection_mismatch")
                return _PreparedPublication(original, declared, None, (), content.candidate, content.gate,
                    content.gate, [], run_input_evidence_ids_in_session(session, run_id), None,
                    boundary, None, None, content.binding,
                    getattr(content, "assessment_assumptions_json", None))
            review_binding = verify_graph_review_in_session(
                session,
                run_id=run_id,
                original=original.result,
                declared=declared,
            )

            # Only this Run's original first-freeze selection may be used.
            # Merely-passed calibration rows still lack authoritative sample
            # and mathematical value provenance; the new helper does not
            # expose them as calibrated authorization or accept a late map.
            from .research_probability_binding import (
                kernel_directional_values_attested,
                prepare_probability_publication_in_session,
                resolve_probability_commit_in_session,
            )

            forecast_strategy_version, calibration_receipt_ids = (
                resolve_probability_commit_in_session(
                    session,
                    run_id=run_id,
                    decision_at=original.receipt.decision_cutoff_at,
                    forecast_strategy_version=forecast_strategy_version,
                    calibration_receipt_ids=calibration_receipt_ids,
                )
            )

            kernel_values = prepare_probability_publication_in_session(
                session, run_id=run_id, original=original
            )

            def validates_calibration(
                receipt_id: str,
                *,
                strategy_version: str,
                horizon: str,
            ) -> bool:
                return _calibration_validates_in_session(
                    session,
                    receipt_id,
                    strategy_version=strategy_version,
                    horizon=horizon,
                )

            normalized, _ignored_attestation = _remove_unattested_calibration(
                declared,
                strategy_version=forecast_strategy_version,
                calibration_receipt_ids=calibration_receipt_ids,
                receipt_validates=validates_calibration,
            )
            if result != declared and result != normalized:
                raise ValueError("research_graph_publication_normalization_mismatch")
            if horizons is not None and horizons not in (
                list(declared.horizons),
                list(normalized.horizons),
            ):
                raise ValueError("research_graph_publication_horizons_mismatch")
            proof = _research_horizon_proof_in_session(
                session,
                run,
                event_id,
                normalized,
                horizons=list(normalized.horizons),
            )
            trigger_refs = run_input_evidence_ids_in_session(session, run_id)
            native_binding = _native_review_binding_in_session(session, run_id, normalized)
            calibration_binding, legacy_attested = _calibration_binding_in_session(
                session,
                normalized,
                forecast_strategy_version,
                calibration_receipt_ids,
            )
            # Actual Kernel values are independent of the model status flag.
            attested = (
                kernel_directional_values_attested(kernel_values)
                if kernel_values is not None
                else legacy_attested
            )
            publishable = _research_result_is_publishable(
                normalized,
                native_review_attested=native_binding is not None,
                additional_allowed_evidence_ids=trigger_refs,
                directional_calibration_attested=attested,
                reference_at=proof.reference_at if proof.valid else None,
            )
            canonical_candidate = _project_research_candidate(normalized, publishable)
            canonical_gate = evaluate_gate(
                canonical_candidate,
                directional_value_validated=attested,
            )
            if candidate is not None and candidate != canonical_candidate:
                raise ValueError("research_graph_publication_candidate_mismatch")
            if gate is not None and gate != canonical_gate:
                raise ValueError("research_graph_publication_gate_mismatch")
            final_gate = _research_publication_gate(
                canonical_candidate,
                canonical_gate,
                proof,
                publishable,
                result=normalized,
                directional_calibration_attested=attested,
            )
            published = (
                list(normalized.horizons)
                if publishable and final_gate.status == GateStatus.publish
                else [
                    _fail_closed_horizon(
                        h,
                        normalized.stop_reason.code
                        if not publishable
                        else f"publication_gate_{final_gate.status.value}",
                    )
                    for h in normalized.horizons
                ]
            )
            return _PreparedPublication(
                original,
                normalized,
                review_binding,
                calibration_binding,
                canonical_candidate,
                canonical_gate,
                final_gate,
                published,
                trigger_refs,
                native_binding,
                boundary,
                kernel_values,
                _prepare_commit_policy_inputs_in_session(
                    session, run_id=run_id, graph_ref=graph_final_ref,
                    expected_lease_owner=expected_lease_owner,
                ),
            )

        with self._publication_originals_scope(run_id, prepare) as prepared:
            if isinstance(prepared.value, tuple):
                # Completed original replay has no publication or execution permission.
                return prepared.value
            plan = prepared.value
            if not expected_lease_owner:
                raise PermissionError("research_graph_publication_owner_required")
            original, normalized = plan.original, plan.normalized
            review_binding, calibration_binding = plan.review_binding, plan.calibration_binding
            canonical_candidate, canonical_gate = plan.canonical_candidate, plan.canonical_gate
            final_gate, published = plan.final_gate, plan.published
            trigger_refs, native_binding = plan.trigger_refs, plan.native_binding
            perpetual_binding = plan.perpetual_binding
            with self.database.session() as session:
                begin_immediate(session)
                require_publication_writer(session)
                run = _permission(session, run_id, expected_lease_owner)
                plan.boundary.require_current_link(session, run_id)
                if run.event_id != event_id:
                    raise ValueError("research_graph_publication_run_invalid")
                prepared.verify(session)
                now = self.clock()
                if perpetual_binding is not None:
                    from .perpetual_research_report import content_at_decision

                    content = _perpetual_content_at_decision(
                        content_at_decision, canonical_candidate, perpetual_binding,
                        decision_at=now,
                        assumptions_json=getattr(plan, "perpetual_assumptions_json", None),
                    )
                    canonical_candidate, canonical_gate = content.candidate, content.gate
                    final_gate, perpetual_binding = content.gate, content.binding
                policy_values = _commit_policy_values(
                    plan.policy_inputs,
                    run_id=run_id,
                    graph_ref=graph_final_ref,
                    result=normalized,
                    assessed_at=now,
                ) if plan.policy_inputs is not None else None
                if policy_values is not None:
                    _store_commit_policy_values_in_session(session, policy_values)
                finalization_check = policy_values.finalization if policy_values is not None else None
                commit_check = policy_values.commit if policy_values is not None else None
                policy_checks = None
                if commit_check is not None:
                    if finalization_check is None:
                        raise ValueError("research_policy_finalization_receipt_invalid")
                    policy_checks = {
                        "finalization": policy_phase_reference(session, finalization_check),
                        "commit": policy_phase_reference(session, commit_check),
                    }
                    final_gate = _canonical_policy_gate(final_gate, commit_check)
                    if not _canonical_policy_passed(commit_check):
                        published = [
                            _fail_closed_horizon(h, "canonical_policy_not_available")
                            for h in normalized.horizons
                        ]
                kernel_references = None
                kernel_values = None
                if plan.kernel_values is not None:
                    from .research_probability_binding import (
                        probability_phase_reference,
                        publication_values_in_session,
                    )

                    kernel_references = {
                        phase: probability_phase_reference(session, run_id=run_id, phase=phase)
                        for phase in ("finalization", "commit")
                    }
                    kernel_values = publication_values_in_session(
                        session, run_id=run_id, references=kernel_references
                    )
                    if kernel_values.model_dump(
                        mode="json", exclude={"phase", "assessed_at"}
                    ) != plan.kernel_values.model_dump(mode="json", exclude={"phase", "assessed_at"}):
                        raise ValueError("research_probability_publication_plan_changed")
                artifact_id = f"art_{uuid.uuid4().hex}"
                audit = _forecast_audit_context_in_session(session, run_id, now)
                claimed = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.run_id == run_id,
                        RunRecord.status == "running",
                        RunRecord.artifact_id.is_(None),
                        RunRecord.lease_owner == expected_lease_owner,
                        RunRecord.lease_expires_at > utcnow(),
                    )
                    .values(artifact_id=artifact_id)
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(claimed, "rowcount", 0)) != 1:
                    raise PermissionError("run_publication_lease_lost")
                # Force SQL re-read before the existing immutable-result writer;
                # an identity-map object may predate another physical writer.
                _assert_result_row_in_session(session, run_id, normalized)
                self.research_observability.save_result_in_session(session, run_id, normalized)
                artifact = ArtifactRecord(
                    artifact_id=artifact_id,
                    run_id=run_id,
                    event_id=event_id,
                    gate_status=final_gate.status.value,
                    headline=str(canonical_candidate["headline"]),
                    summary=str(canonical_candidate["summary"]),
                    payload_json=json.dumps(
                        {
                            key: canonical_candidate[key]
                            for key in (
                                "facts",
                                "inferences",
                                "counter_thesis",
                                "uncertainty",
                                "transmission_chain",
                                "citations",
                            ) + (("perpetual_synthesis",) if plan.perpetual_binding is not None else ())
                        },
                        ensure_ascii=False,
                    ),
                    created_at=now,
                )
                session.add(artifact)
                record_revision(
                    session, run, artifact, input_fingerprint=audit.input_fingerprint, now=now
                )
                freeze_outlooks(
                    session,
                    run=run,
                    artifact=artifact,
                    result=normalized,
                    gate=final_gate,
                    published_horizons=published,
                    now=now,
                    raw_result=declared,
                    native_review_verified=native_binding is not None,
                    trigger_evidence_ids=trigger_refs,
                    retain_only=plan.perpetual_binding is not None,
                )
                if plan.perpetual_binding is None:
                    _add_publication_forecasts(
                        session,
                        artifact_id=artifact_id,
                        horizons=published,
                        candidate=canonical_candidate,
                        gate=final_gate,
                        now=now,
                        audit=audit,
                        strategy_version=forecast_strategy_version,
                        calibration_receipt_ids=calibration_receipt_ids,
                        kernel_values=kernel_values,
                    )
                for decision in final_gate.decisions:
                    session.add(GateDecisionRecord(run_id=run_id, artifact_id=artifact_id, **decision))
                session.add(
                    RunEventRecord(
                        run_id=run_id,
                        sequence_no=4,
                        event_type="decision.committed",
                        occurred_at=now,
                        payload_json=json.dumps({"artifact_id": artifact_id}),
                    )
                )
                session.add(
                    OutboxRecord(
                        artifact_id=artifact_id,
                        channel="local",
                        dedupe_key=f"artifact:{artifact_id}:local",
                    )
                )
                session.flush()
                _store_publication_source_in_session(
                    session,
                    run_id=run_id,
                    event_id=event_id,
                    reference=graph_final_ref,
                    original=original.result,
                    declared=declared,
                    normalized=normalized,
                    artifact=artifact,
                    gate=final_gate,
                    review_binding=review_binding,
                    calibration_binding=calibration_binding,
                    recorded_at=now,
                    candidate=canonical_candidate,
                    candidate_gate=canonical_gate,
                    publication_owner=expected_lease_owner,
                    policy_checks=policy_checks,
                    kernel_values=kernel_references,
                    perpetual_binding=perpetual_binding,
                )
                session.flush()
                if policy_values is not None:
                    _verify_commit_policy_values_in_session(session, policy_values)
                prepared.verify_after_writes(session)
                plan.boundary.require_current_link(session, run_id)
                # The final wall-clock fence follows all expensive validation and
                # semantic flushes. Any failure rolls Result/source/Artifact/Outbox
                # back together; an original-data read cannot authorize this write.
                finished = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.run_id == run_id,
                        RunRecord.status == "running",
                        RunRecord.artifact_id == artifact_id,
                        RunRecord.lease_owner == expected_lease_owner,
                        RunRecord.lease_expires_at > utcnow(),
                    )
                    .values(
                        status=(
                            RunStatus.completed.value
                            if final_gate.status == GateStatus.publish
                            or (perpetual_binding is not None
                                and perpetual_binding.get("schema_version")
                                == "perpetual-report-binding.v2"
                                and canonical_candidate.get("research_complete") is True)
                            else RunStatus.degraded.value
                        ),
                        updated_at=now,
                        finished_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(finished, "rowcount", 0)) != 1:
                    raise PermissionError("run_publication_lease_lost")
                # Native publication jobs share this final writer. Legacy Graph V1
                # publications have no frozen selection and retain their old path.
                selection_schema = session.scalar(
                    select(RunRoleOutputRecord.schema_version).where(
                        RunRoleOutputRecord.run_id == run_id,
                        RunRoleOutputRecord.role == "graph_recipe_selection",
                    )
                )
                if selection_schema in {
                    "research-graph-recipe-selection.v2",
                    "research-graph-recipe-selection.v3",
                }:
                    from .research_publication_jobs48 import enqueue_in_publication_transaction

                    enqueue_in_publication_transaction(session, run_id, now=now)
                return artifact_id, final_gate

    def commit(
        self,
        run_id: str,
        event_id: str,
        candidate: dict[str, object],
        gate: GateResult,
        *,
        horizons: list[HorizonDecision] | None = None,
        research_result: ResearchSessionResult | None = None,
        directional_calibration_attested: bool = False,
        forecast_strategy_version: str | None = None,
        calibration_receipt_ids: dict[str, str] | None = None,
        expected_lease_owner: str | None = None,
        raw_research_result: ResearchSessionResult | None = None,
        graph_final_ref: GraphFinalProjectionRef | None = None,
    ) -> str:
        from .research_graph_binding import graph_requires_native_binding_in_session

        with self.database.session() as session:
            native = graph_requires_native_binding_in_session(
                session,
                run_id,
                has_checkpoint_locator=graph_final_ref is not None,
            )
        if native:
            return self._commit_v2(
                run_id,
                event_id,
                research_result,
                graph_final_ref=graph_final_ref,
                expected_lease_owner=expected_lease_owner,
                candidate=candidate,
                gate=gate,
                horizons=horizons,
                raw_result=raw_research_result,
                forecast_strategy_version=forecast_strategy_version,
                calibration_receipt_ids=calibration_receipt_ids,
            )[0]
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reject_legacy_native_publication_in_session(session, run_id)
            existing_run = session.get(RunRecord, run_id, populate_existing=True)
            if not existing_run:
                raise KeyError(run_id)
            if existing_run.artifact_id:
                if expected_lease_owner is not None:
                    raise PermissionError("run_publication_lease_lost")
                if research_result is not None:
                    self.research_observability.save_result_in_session(
                        session, run_id, research_result
                    )
                return existing_run.artifact_id
        artifact_id = f"art_{uuid.uuid4().hex}"
        now = self.clock()
        requested_horizons = horizons
        if research_result is not None:
            original_result = research_result
            raw_research_result = raw_research_result or original_result
            research_result, _ignored_caller_attestation = _remove_unattested_calibration(
                research_result,
                strategy_version=forecast_strategy_version,
                calibration_receipt_ids=calibration_receipt_ids,
                receipt_validates=StrategyCalibrationService(self.database).validates,
            )
            if requested_horizons == list(original_result.horizons):
                requested_horizons = list(research_result.horizons)
            horizons = requested_horizons
        horizon_proof = _research_horizon_proof(
            self.database,
            run_id,
            event_id,
            research_result,
            horizons=horizons,
        )
        trigger_refs = _trigger_evidence_ids(self.database, run_id)
        with self.database.session() as session:
            native_review_binding = _native_review_binding_in_session(
                session,
                run_id,
                research_result,
            )
            calibration_binding, actual_calibration_attested = _calibration_binding_in_session(
                session,
                research_result,
                forecast_strategy_version,
                calibration_receipt_ids,
            )
        native_review_verified = native_review_binding is not None
        if research_result is not None:
            # The research caller's Boolean cannot mint Kernel calibration.
            directional_calibration_attested = actual_calibration_attested
        publishable_research = _research_result_is_publishable(
            research_result,
            native_review_attested=native_review_verified,
            additional_allowed_evidence_ids=trigger_refs,
            directional_calibration_attested=directional_calibration_attested,
            reference_at=horizon_proof.reference_at if horizon_proof.valid else None,
        )
        if research_result is not None:
            gate = _research_publication_gate(
                candidate,
                gate,
                horizon_proof,
                publishable_research,
                result=research_result,
                directional_calibration_attested=directional_calibration_attested,
            )
            horizons = list(research_result.horizons)
        effective_horizons = (
            [
                _fail_closed_horizon(
                    item,
                    research_result.stop_reason.code
                    if not publishable_research
                    else f"publication_gate_{gate.status.value}",
                )
                for item in horizons or []
            ]
            if research_result is not None
            and (not publishable_research or gate.status != GateStatus.publish)
            else list(horizons or [])
        )
        publish_direction = gate.status == GateStatus.publish
        forecasts: list[Forecast] = []
        horizon_by_name = {item.horizon: item for item in effective_horizons}
        for horizon in ("30m", "24h", "72h"):
            selected = horizon_by_name.get(horizon)
            forecasts.append(
                Forecast(
                    forecast_id=f"fc_{uuid.uuid4().hex}",
                    artifact_id=artifact_id,
                    instrument="BTC-USDT-SWAP",
                    horizon=horizon,
                    direction=Direction(
                        selected.action
                        if selected is not None
                        else (
                            str(candidate.get("direction", "no_trade"))
                            if publish_direction
                            else Direction.no_trade.value
                        )
                    ),
                    probability=(
                        selected.subjective_probability
                        if selected is not None
                        else (
                            float(str(candidate.get("probability", 0.5)))
                            if publish_direction
                            else 0.5
                        )
                    ),
                    trigger=(
                        selected.trigger
                        if selected is not None
                        else str(candidate.get("trigger", ""))
                    ),
                    invalidation=(
                        selected.invalidation
                        if selected is not None
                        else str(candidate.get("invalidation", ""))
                    ),
                    expires_at=(
                        selected.expires_at
                        if selected is not None
                        else now + timedelta(minutes={"30m": 30, "24h": 1440, "72h": 4320}[horizon])
                    ),
                )
            )
        payload = {
            "facts": candidate.get("facts", []),
            "inferences": candidate.get("inferences", []),
            "counter_thesis": candidate.get("counter_thesis", ""),
            "uncertainty": candidate.get("uncertainty", []),
            "transmission_chain": candidate.get("transmission_chain", []),
            "citations": candidate.get("citations", []),
        }
        audit = _forecast_audit_context(self.database, run_id, now)
        with self.database.session() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reject_legacy_native_publication_in_session(session, run_id)
            run = session.get(RunRecord, run_id, populate_existing=True)
            if not run:
                raise KeyError(run_id)
            if run.status == RunStatus.cancelled.value:
                raise PermissionError("research_run_cancelled")
            if run.artifact_id:
                if expected_lease_owner is not None:
                    raise PermissionError("run_publication_lease_lost")
                if research_result is not None:
                    self.research_observability.save_result_in_session(
                        session, run_id, research_result
                    )
                return run.artifact_id
            if (
                research_result is not None
                and _research_horizon_proof_in_session(
                    session,
                    run,
                    event_id,
                    research_result,
                    horizons=requested_horizons,
                )
                != horizon_proof
            ):
                raise PermissionError("research_horizon_binding_changed")
            if research_result is not None:
                if (
                    _native_review_binding_in_session(
                        session,
                        run_id,
                        research_result,
                    )
                    != native_review_binding
                ):
                    raise PermissionError("research_publication_review_binding_changed")
                if _calibration_binding_in_session(
                    session,
                    research_result,
                    forecast_strategy_version,
                    calibration_receipt_ids,
                ) != (calibration_binding, actual_calibration_attested):
                    raise PermissionError("research_publication_calibration_binding_changed")
            predicates = [
                RunRecord.run_id == run_id,
                RunRecord.artifact_id.is_(None),
                RunRecord.status != RunStatus.cancelled.value,
            ]
            if expected_lease_owner is not None:
                predicates.extend(
                    [
                        RunRecord.lease_owner == expected_lease_owner,
                        RunRecord.lease_expires_at > now,
                    ]
                )
            # Claim the single publication slot inside the artifact/outbox
            # transaction. A late owner cannot publish after a recovery claim.
            claimed = session.execute(
                update(RunRecord)
                .where(*predicates)
                .values(artifact_id=artifact_id)
                .execution_options(synchronize_session=False)
            )
            if int(getattr(claimed, "rowcount", 0)) != 1:
                session.refresh(run)
                if run.artifact_id:
                    if expected_lease_owner is not None:
                        raise PermissionError("run_publication_lease_lost")
                    return run.artifact_id
                raise PermissionError("run_publication_lease_lost")
            if research_result is not None:
                self.research_observability.save_result_in_session(session, run_id, research_result)
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                run_id=run_id,
                event_id=event_id,
                gate_status=gate.status.value,
                headline=str(candidate.get("headline", "Decision candidate")),
                summary=str(candidate.get("summary", "")),
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
            )
            session.add(artifact)
            record_revision(
                session, run, artifact, input_fingerprint=audit.input_fingerprint, now=now
            )
            freeze_outlooks(
                session,
                run=run,
                artifact=artifact,
                result=research_result,
                gate=gate,
                published_horizons=effective_horizons,
                now=now,
                raw_result=raw_research_result,
                native_review_verified=native_review_verified,
                trigger_evidence_ids=trigger_refs,
            )
            for forecast in forecasts:
                session.add(
                    ForecastRecord(
                        forecast_id=forecast.forecast_id,
                        artifact_id=artifact_id,
                        instrument=forecast.instrument,
                        horizon=forecast.horizon,
                        direction=forecast.direction.value,
                        probability=forecast.probability,
                        trigger=forecast.trigger,
                        invalidation=forecast.invalidation,
                        expires_at=forecast.expires_at,
                        forecasted_at=now,
                        as_of=audit.as_of,
                        strategy_version=forecast_strategy_version or audit.strategy_version,
                        input_snapshot_id=audit.input_snapshot_id,
                        input_fingerprint=audit.input_fingerprint,
                        feature_snapshot_json=audit.feature_snapshot_json,
                        evidence_cutoff_at=audit.evidence_cutoff_at,
                        calibration_receipt_id=(calibration_receipt_ids or {}).get(
                            forecast.horizon
                        ),
                    )
                )
            for decision in gate.decisions:
                session.add(GateDecisionRecord(run_id=run_id, artifact_id=artifact_id, **decision))
            run.artifact_id = artifact_id
            run.status = (
                RunStatus.completed.value
                if gate.status == GateStatus.publish
                else RunStatus.degraded.value
            )
            run.updated_at = now
            run.finished_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence_no=4,
                    event_type="decision.committed",
                    occurred_at=now,
                    payload_json=json.dumps({"artifact_id": artifact_id}),
                )
            )
            # Every committed research Artifact is owner-visible, including a
            # fail-closed research_only/reject report. Provider failures never
            # reach this branch because they have no Artifact to notify.
            if gate.status.value in {"publish", "degraded"} or research_result is not None:
                session.add(
                    OutboxRecord(
                        artifact_id=artifact_id,
                        channel="local",
                        dedupe_key=f"artifact:{artifact_id}:local",
                    )
                )
            # next_review_at is an owner-visible suggestion, never admission authority.
        return artifact_id

    def commit_research_result(
        self,
        run_id: str,
        event_id: str,
        result: ResearchSessionResult,
        *,
        forecast_strategy_version: str | None = None,
        calibration_receipt_ids: dict[str, str] | None = None,
        expected_lease_owner: str | None = None,
        graph_final_ref: GraphFinalProjectionRef | None = None,
    ) -> tuple[str, GateResult]:
        """Project a validated agentic result into the existing decision ledger."""

        from .research_graph_binding import graph_requires_native_binding_in_session

        with self.database.session() as session:
            native = graph_requires_native_binding_in_session(
                session,
                run_id,
                has_checkpoint_locator=graph_final_ref is not None,
            )
        if native:
            return self._commit_v2(
                run_id,
                event_id,
                result,
                graph_final_ref=graph_final_ref,
                expected_lease_owner=expected_lease_owner,
                forecast_strategy_version=forecast_strategy_version,
                calibration_receipt_ids=calibration_receipt_ids,
            )

        raw_result = result
        calibration_service = StrategyCalibrationService(self.database)
        result, directional_calibration_attested = _remove_unattested_calibration(
            result,
            strategy_version=forecast_strategy_version,
            calibration_receipt_ids=calibration_receipt_ids,
            receipt_validates=calibration_service.validates,
        )
        trigger_refs = _trigger_evidence_ids(self.database, run_id)
        horizon_proof = _research_horizon_proof(self.database, run_id, event_id, result)
        publishable_research = _research_result_is_publishable(
            result,
            native_review_attested=_durable_native_review_matches(self.database, run_id, result),
            additional_allowed_evidence_ids=trigger_refs,
            directional_calibration_attested=directional_calibration_attested,
            reference_at=horizon_proof.reference_at if horizon_proof.valid else None,
        )

        candidate = _project_research_candidate(result, publishable_research)
        gate = evaluate_gate(
            candidate,
            directional_value_validated=directional_calibration_attested,
        )
        artifact_id = self.commit(
            run_id,
            event_id,
            candidate,
            gate,
            horizons=list(result.horizons),
            research_result=result,
            directional_calibration_attested=directional_calibration_attested,
            forecast_strategy_version=forecast_strategy_version,
            calibration_receipt_ids=calibration_receipt_ids,
            raw_research_result=raw_result,
            expected_lease_owner=expected_lease_owner,
        )
        # Return the persisted final authorization, including an existing
        # Artifact's original decision. The supplied Gate is not authority.
        with self.database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            if artifact is None:
                raise KeyError(artifact_id)
            decisions = session.query(GateDecisionRecord).filter_by(artifact_id=artifact_id).all()
        return artifact_id, GateResult(
            status=GateStatus(artifact.gate_status),
            decisions=[
                {
                    "rule_id": row.rule_id,
                    "status": row.status,
                    "reason_code": row.reason_code,
                    "input_hash": row.input_hash,
                }
                for row in decisions
            ],
        )


def _calibration_validates_in_session(
    session: Session,
    receipt_id: str | None,
    *,
    strategy_version: str,
    horizon: str,
) -> bool:
    row = (
        session.get(StrategyCalibrationRecord, receipt_id, populate_existing=True)
        if receipt_id
        else None
    )
    return bool(
        row is not None
        and row.passed
        and row.strategy_version == strategy_version
        and row.horizon == horizon
        and hashlib.sha256(row.report_json.encode()).hexdigest() == row.report_hash
    )


def _assert_result_row_in_session(
    session: Session,
    run_id: str,
    expected: ResearchSessionResult,
) -> ResearchResultRecord | None:
    row = session.get(ResearchResultRecord, run_id, populate_existing=True)
    if row is not None and (
        row.schema_version != expected.schema_version
        or row.payload_hash != _canonical_hash(json.loads(row.payload_json))
        or ResearchSessionResult.model_validate_json(row.payload_json) != expected
    ):
        raise ValueError("research_graph_publication_result_conflict")
    return row


def _add_publication_forecasts(
    session: Session,
    *,
    artifact_id: str,
    horizons: list[HorizonDecision],
    candidate: dict[str, object],
    gate: GateResult,
    now: datetime,
    audit: ForecastAuditContext,
    strategy_version: str | None,
    calibration_receipt_ids: dict[str, str] | None,
    kernel_values: FrozenProbabilityValues | None = None,
) -> None:
    selected_by_name = {h.horizon: h for h in horizons}
    for name in ("30m", "24h", "72h"):
        selected = selected_by_name.get(name)
        kernel_direction, kernel_probability, kernel_production = (
            _kernel_forecast_scalar(kernel_values, name, gate)
            if kernel_values is not None
            else (None, None, None)
        )
        forecast = Forecast(
            forecast_id=f"fc_{uuid.uuid4().hex}",
            artifact_id=artifact_id,
            instrument=kernel_values.instrument if kernel_values is not None else "BTC-USDT-SWAP",
            horizon=name,
            direction=Direction(
                kernel_direction
                if kernel_direction is not None
                else (
                    selected.action
                    if selected is not None
                    else (
                        str(candidate.get("direction", "no_trade"))
                        if gate.status == GateStatus.publish
                        else "no_trade"
                    )
                )
            ),
            probability=(
                kernel_probability
                if kernel_probability is not None
                else (
                    selected.subjective_probability
                    if selected is not None
                    else (
                        float(str(candidate.get("probability", 0.5)))
                        if gate.status == GateStatus.publish
                        else 0.5
                    )
                )
            ),
            trigger=selected.trigger if selected is not None else str(candidate.get("trigger", "")),
            invalidation=(
                selected.invalidation
                if selected is not None
                else str(candidate.get("invalidation", ""))
            ),
            expires_at=(
                selected.expires_at
                if selected is not None
                else now
                + timedelta(
                    minutes={"30m": 30, "24h": 1440, "72h": 4320}[name],
                )
            ),
        )
        session.add(
            ForecastRecord(
                forecast_id=forecast.forecast_id,
                artifact_id=artifact_id,
                instrument=forecast.instrument,
                horizon=forecast.horizon,
                direction=forecast.direction.value,
                probability=forecast.probability,
                trigger=forecast.trigger,
                invalidation=forecast.invalidation,
                expires_at=forecast.expires_at,
                forecasted_at=now,
                as_of=audit.as_of,
                strategy_version=kernel_values.strategy_version
                if kernel_values is not None
                else strategy_version or audit.strategy_version,
                input_snapshot_id=audit.input_snapshot_id,
                input_fingerprint=audit.input_fingerprint,
                feature_snapshot_json=audit.feature_snapshot_json,
                evidence_cutoff_at=audit.evidence_cutoff_at,
                calibration_receipt_id=kernel_production
                if kernel_values is not None
                else (calibration_receipt_ids or {}).get(name),
            )
        )


def _kernel_forecast_scalar(
    values: FrozenProbabilityValues, horizon: str, gate: GateResult
) -> tuple[str, float, str | None]:
    slot = next((item for item in values.slots if item.horizon == horizon), None)
    if slot is None:
        raise ValueError("research_probability_forecast_horizon_missing")
    kernel = slot.kernel
    if (
        gate.status == GateStatus.publish
        and slot.graph_action in {"long", "short"}
        and slot.selected_probability_status == "uncalibrated"
        and kernel is not None
        and kernel.raw_probability == slot.graph_probability
        and kernel.calibrated_probability >= kernel.retain_threshold
    ):
        return slot.graph_action, kernel.calibrated_probability, kernel.production_id
    return "no_trade", 0.5, None


def _kernel_forecast_references(session: Session, artifact_id: str) -> list[dict[str, str]]:
    rows = session.scalars(
        select(ForecastRecord)
        .where(ForecastRecord.artifact_id == artifact_id)
        .order_by(ForecastRecord.horizon)
        .limit(4)
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) != 3 or {row.horizon for row in rows} != {"30m", "24h", "72h"}:
        raise ValueError("research_probability_forecasts_original_missing")
    from .research_probability_binding import _row_hash  # pyright: ignore[reportPrivateUsage]

    return [
        {"forecast_id": row.forecast_id, "horizon": row.horizon, "payload_hash": _row_hash(row)}
        for row in rows
    ]


def _verify_kernel_forecasts(
    session: Session,
    *,
    artifact: ArtifactRecord,
    result: ResearchSessionResult,
    gate: GateResult,
    values: FrozenProbabilityValues,
    references: object,
    candidate: dict[str, object],
) -> None:
    expected_references = _kernel_forecast_references(session, artifact.artifact_id)
    if references != expected_references:
        raise ValueError("research_probability_forecast_reference_invalid")
    raw = {horizon.horizon: horizon for horizon in result.horizons}
    for ref in expected_references:
        row = session.get(ForecastRecord, ref["forecast_id"], populate_existing=True)
        horizon = raw.get(ref["horizon"])
        direction, probability, production = _kernel_forecast_scalar(values, ref["horizon"], gate)
        expected_expiry = (
            horizon.expires_at
            if horizon is not None
            else as_utc(artifact.created_at)
            + timedelta(minutes={"30m": 30, "24h": 1440, "72h": 4320}[ref["horizon"]])
        )
        if (
            row is None
            or row.artifact_id != artifact.artifact_id
            or row.horizon != ref["horizon"]
            or row.instrument != values.instrument
            or row.strategy_version != values.strategy_version
            or row.direction != direction
            or row.probability != probability
            or row.calibration_receipt_id != production
            or row.trigger
            != (horizon.trigger if horizon is not None else str(candidate.get("trigger", "")))
            or row.invalidation
            != (
                horizon.invalidation
                if horizon is not None
                else str(candidate.get("invalidation", ""))
            )
            or as_utc(row.expires_at) != expected_expiry
            or as_utc(row.forecasted_at) != as_utc(artifact.created_at)
        ):
            raise ValueError("research_probability_forecast_value_invalid")


def _artifact_body(artifact: ArtifactRecord) -> dict[str, object]:
    created = as_utc(cast(datetime | None, artifact.created_at))
    if created is None:
        raise ValueError("research_publication_artifact_time_missing")
    return {
        "artifact_id": artifact.artifact_id,
        "run_id": artifact.run_id,
        "event_id": artifact.event_id,
        "gate_status": artifact.gate_status,
        "headline": artifact.headline,
        "summary": artifact.summary,
        "payload_json": artifact.payload_json,
        "created_at": created.isoformat(),
    }


def _quality_publication_binding(
    session: Session, *, run_id: str, reference: GraphFinalProjectionRef
) -> dict[str, str] | None:
    from ..persistence.run_spot_admission import RunSpotAdmissionRecord
    from .research_graph_binding import ResearchGraphFinalProjectionV3, read_graph_final_in_session
    from .research_graph_dispatch import read_graph_recipe_selection_for_run_in_session
    from .research_graph_recipe_v3 import FrozenGraphRecipeSelectionV3
    from .research_quality_sidecar import read_quality_sidecar_in_session
    from .research_run_scope import read_run_spot_admission_in_session

    if reference.row.schema_version != "research-graph-final-projection.v3":
        return None
    final = read_graph_final_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=None,
        require_current_permission=False,
    )
    selection = read_graph_recipe_selection_for_run_in_session(session, run_id)
    if not isinstance(final.receipt, ResearchGraphFinalProjectionV3) or not isinstance(
        selection, FrozenGraphRecipeSelectionV3
    ):
        raise ValueError("research_quality_publication_graph_mismatch")
    sidecar = read_quality_sidecar_in_session(
        session, final.receipt.quality_sidecar_id, selection=selection.receipt
    )
    binding = {
        "quality_sidecar_id": final.receipt.quality_sidecar_id,
        "quality_context_hash": sidecar.quality_context_hash,
        "quality_decision_hash": sidecar.quality_decision.decision_hash,
        "source_result_hash": sidecar.source_result_hash,
        "projected_result_hash": final.receipt.projected_result_hash,
        "publication_mode": sidecar.quality_decision.publication_mode,
    }
    frozen = read_run_spot_admission_in_session(session, run_id)
    if frozen is not None:
        row = session.get(RunSpotAdmissionRecord, run_id, populate_existing=True)
        assert row is not None
        binding.update(
            run_spot_admission_hash=row.payload_hash,
            admission_proof_hash=frozen.admission_proof_hash,
            path_manifest_hash=frozen.path.manifest_hash,
        )
    return binding


def _store_publication_source_in_session(
    session: Session,
    *,
    run_id: str,
    event_id: str,
    reference: GraphFinalProjectionRef,
    original: ResearchSessionResult,
    declared: ResearchSessionResult,
    normalized: ResearchSessionResult,
    artifact: ArtifactRecord,
    gate: GateResult,
    review_binding: tuple[str, str, str, str] | None,
    calibration_binding: tuple[tuple[str, ...], ...],
    recorded_at: datetime,
    candidate: dict[str, object],
    candidate_gate: GateResult,
    publication_owner: str,
    policy_checks: dict[str, dict[str, str]] | None = None,
    kernel_values: dict[str, dict[str, str]] | None = None,
    perpetual_binding: dict[str, object] | None = None,
) -> None:
    row = _assert_result_row_in_session(session, run_id, normalized)
    if row is None:
        raise ValueError("research_publication_result_missing")
    recorded = as_utc(recorded_at)
    assert recorded is not None
    body: dict[str, object] = {
        "schema_version": PUBLICATION_SOURCE_SCHEMA,
        "run_id": run_id,
        "event_id": event_id,
        "graph_final_ref": reference.model_dump(mode="json"),
        "graph_result_hash": _canonical_hash(original.model_dump(mode="json")),
        "declared_result_hash": _canonical_hash(declared.model_dump(mode="json")),
        "result_schema_version": row.schema_version,
        "result_payload_hash": row.payload_hash,
        "result_created_at": cast(
            datetime,
            as_utc(cast(datetime | None, row.created_at)),
        ).isoformat(),
        "artifact_id": artifact.artifact_id,
        "artifact_hash": _canonical_hash(_artifact_body(artifact)),
        "review_binding": list(review_binding) if review_binding is not None else None,
        "calibration_binding": [list(binding) for binding in calibration_binding],
        "gate": {"status": gate.status.value, "decisions": gate.decisions},
        "candidate": candidate,
        "candidate_gate": {
            "status": candidate_gate.status.value,
            "decisions": candidate_gate.decisions,
        },
        "publication_owner": publication_owner,
        "recorded_at": recorded.isoformat(),
    }
    if policy_checks is not None:
        body["schema_version"] = PUBLICATION_SOURCE_SCHEMA_V2
        body["policy_checks"] = policy_checks
    if kernel_values is not None:
        if policy_checks is None:
            raise ValueError("research_probability_publication_policy_missing")
        body["schema_version"] = PUBLICATION_SOURCE_SCHEMA_V3
        body["kernel_values"] = kernel_values
        body["kernel_forecasts"] = _kernel_forecast_references(session, artifact.artifact_id)
    quality = _quality_publication_binding(session, run_id=run_id, reference=reference)
    if quality is not None:
        body["base_schema_version"] = body["schema_version"]
        body["schema_version"] = PUBLICATION_SOURCE_SCHEMA_V4
        body["quality_binding"] = quality
    if perpetual_binding is not None:
        if reference.row.schema_version != "research-perpetual-graph-final.v1" or review_binding is not None or policy_checks is not None or kernel_values is not None or quality is not None:
            raise ValueError("perpetual_report_legacy_source_binding_forbidden")
        body["schema_version"] = PUBLICATION_SOURCE_SCHEMA_V5
        body["perpetual_binding"] = perpetual_binding
        if perpetual_binding.get("schema_version") == "perpetual-report-binding.v2":
            delivery = perpetual_binding["report_delivery"]
            if (not isinstance(delivery, dict)
                    or delivery.get("schema_version") != "perpetual-report-delivery.v1"):
                raise ValueError("perpetual_report_delivery_version_invalid")
            body["schema_version"] = PUBLICATION_SOURCE_SCHEMA_V6
            body["report_delivery"] = delivery
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    output_id = f"publication_{digest[:40]}"
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        # A publication slot and its source are born in the same writer. Do
        # not adopt a pre-existing partial source or silently return its ID.
        raise ValueError("research_publication_source_conflict")
    session.add(
        RunRoleOutputRecord(
            output_id=output_id,
            run_id=run_id,
            role=PUBLICATION_SOURCE_ROLE,
            schema_version=str(body["schema_version"]),
            payload_json=payload,
            payload_hash=digest,
            created_at=recorded_at,
        )
    )


def _read_publication_source_in_session(
    session: Session,
    *,
    run: RunRecord,
    reference: GraphFinalProjectionRef,
    declared: ResearchSessionResult,
    expected_lease_owner: str | None,
    candidate: dict[str, object] | None,
    gate: GateResult | None,
) -> tuple[str, GateResult]:
    perpetual = reference.row.schema_version == "research-perpetual-graph-final.v1"
    if perpetual:
        from .research_graph_binding import _perpetual_saved_terminal
        if not _perpetual_saved_terminal(session, run.run_id):
            raise PermissionError("research_publication_original_not_completed")
    from .research_graph_history_versions import replay_retained_publication_in_session
    from .research_policy_checker import (
        _publication_policy_scope,  # pyright: ignore[reportPrivateUsage]
    )

    retained = replay_retained_publication_in_session(
        session,
        run_id=run.run_id,
        reference_json=reference.model_dump_json(),
        declared_json=declared.model_dump_json(),
        expected_lease_owner=expected_lease_owner,
        candidate_json=json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if candidate is not None
        else None,
        gate_json=json.dumps(
            {"status": gate.status.value, "decisions": gate.decisions},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if gate is not None
        else None,
    )
    if retained is not None:
        saved_gate = json.loads(retained.gate_json)
        return retained.artifact_id, GateResult(
            status=GateStatus(saved_gate["status"]), decisions=saved_gate["decisions"]
        )

    if perpetual:
        from .perpetual_research_report import read_source
        return read_source(session, run=run, reference=reference, declared=declared,
                           expected_lease_owner=expected_lease_owner, candidate=candidate, gate=gate)

    if run.status not in {RunStatus.completed.value, RunStatus.degraded.value}:
        raise PermissionError("research_publication_original_not_completed")
    source = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run.run_id,
            RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
        )
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise ValueError("research_publication_source_missing")
    digest = hashlib.sha256(source.payload_json.encode("utf-8")).hexdigest()
    body = json.loads(source.payload_json)
    required = {
        "schema_version",
        "run_id",
        "event_id",
        "graph_final_ref",
        "graph_result_hash",
        "declared_result_hash",
        "result_schema_version",
        "result_payload_hash",
        "artifact_id",
        "artifact_hash",
        "review_binding",
        "calibration_binding",
        "gate",
        "recorded_at",
        "result_created_at",
        "candidate",
        "candidate_gate",
        "publication_owner",
    }
    schema = body.get("schema_version") if isinstance(body, dict) else None
    base_schema = (
        body.get("base_schema_version") if schema == PUBLICATION_SOURCE_SCHEMA_V4 else schema
    )
    if schema == PUBLICATION_SOURCE_SCHEMA_V4:
        required.update({"base_schema_version", "quality_binding"})
    if base_schema in {PUBLICATION_SOURCE_SCHEMA_V2, PUBLICATION_SOURCE_SCHEMA_V3}:
        required.add("policy_checks")
    if base_schema == PUBLICATION_SOURCE_SCHEMA_V3:
        required.update({"kernel_values", "kernel_forecasts"})
    if (
        not isinstance(body, dict)
        or set(body) != required
        or schema
        not in {
            PUBLICATION_SOURCE_SCHEMA,
            PUBLICATION_SOURCE_SCHEMA_V2,
            PUBLICATION_SOURCE_SCHEMA_V3,
            PUBLICATION_SOURCE_SCHEMA_V4,
        }
        or base_schema
        not in {
            PUBLICATION_SOURCE_SCHEMA,
            PUBLICATION_SOURCE_SCHEMA_V2,
            PUBLICATION_SOURCE_SCHEMA_V3,
        }
        or source.schema_version != schema
        or source.payload_hash != digest
        or source.output_id != f"publication_{digest[:40]}"
        or as_utc(cast(datetime | None, source.created_at)) is None
        or body["recorded_at"]
        != cast(
            datetime,
            as_utc(cast(datetime | None, source.created_at)),
        ).isoformat()
        or as_utc(run.finished_at) is None
        or body["recorded_at"] != cast(datetime, as_utc(run.finished_at)).isoformat()
        or body["run_id"] != run.run_id
        or body["event_id"] != run.event_id
        or body["artifact_id"] != run.artifact_id
        or body["graph_final_ref"] != reference.model_dump(mode="json")
        or body["declared_result_hash"] != _canonical_hash(declared.model_dump(mode="json"))
    ):
        raise ValueError("research_publication_source_invalid")
    expected_quality = _quality_publication_binding(session, run_id=run.run_id, reference=reference)
    if (expected_quality is not None) != (schema == PUBLICATION_SOURCE_SCHEMA_V4) or (
        expected_quality is not None and body["quality_binding"] != expected_quality
    ):
        raise ValueError("research_quality_publication_binding_changed")
    if expected_lease_owner is not None and expected_lease_owner != body["publication_owner"]:
        raise PermissionError("research_publication_original_owner_mismatch")
    if candidate is not None and body["candidate"] != candidate:
        raise ValueError("research_graph_publication_candidate_mismatch")
    if gate is not None and body["candidate_gate"] != {
        "status": gate.status.value,
        "decisions": gate.decisions,
    }:
        raise ValueError("research_graph_publication_gate_mismatch")
    with _publication_policy_scope(
        session,
        run_id=run.run_id,
        graph_ref=reference,
        expected_lease_owner=expected_lease_owner,
    ) as (original, authenticate_policy):
        if body["graph_result_hash"] != _canonical_hash(original.result.model_dump(mode="json")):
            raise ValueError("research_publication_source_original_mismatch")
        review = verify_graph_review_in_session(
            session,
            run_id=run.run_id,
            original=original.result,
            declared=declared,
        )
        row = session.get(ResearchResultRecord, run.run_id, populate_existing=True)
        artifact = session.get(ArtifactRecord, run.artifact_id, populate_existing=True)
        if (
            row is None
            or artifact is None
            or artifact.run_id != run.run_id
            or row.schema_version != body["result_schema_version"]
            or row.payload_hash != body["result_payload_hash"]
            or row.payload_hash != _canonical_hash(json.loads(row.payload_json))
            or as_utc(cast(datetime | None, row.created_at)) is None
            or body["result_created_at"]
            != cast(
                datetime,
                as_utc(cast(datetime | None, row.created_at)),
            ).isoformat()
            or _canonical_hash(_artifact_body(artifact)) != body["artifact_hash"]
            or list(review) != body["review_binding"]
        ):
            raise ValueError("research_publication_source_original_mismatch")
        persisted = ResearchSessionResult.model_validate_json(row.payload_json)
        candidate_body = body["candidate"]
        if (
            not isinstance(candidate_body, dict)
            or type(candidate_body.get("research_complete")) is not bool
        ):
            raise ValueError("research_publication_source_candidate_invalid")
        # This is original-body integrity, not today's calibration regrading or
        # renewed publication permission. Headline, summary, chains and citations
        # must come from the actual saved Result, even if source/Artifact were both
        # re-signed. The recorded completeness flag remains a historical declaration.
        projected = _project_research_candidate(persisted, candidate_body["research_complete"])
        artifact_payload = {
            key: projected[key]
            for key in (
                "facts",
                "inferences",
                "counter_thesis",
                "uncertainty",
                "transmission_chain",
                "citations",
            )
        }
        if (
            candidate_body != projected
            or artifact.headline != projected["headline"]
            or artifact.summary != projected["summary"]
            or json.loads(artifact.payload_json) != artifact_payload
        ):
            raise ValueError("research_publication_source_candidate_invalid")
        decisions = session.scalars(
            select(GateDecisionRecord)
            .where(
                GateDecisionRecord.artifact_id == artifact.artifact_id,
            )
            .order_by(GateDecisionRecord.id)
            .execution_options(populate_existing=True)
        ).all()
        gate = GateResult(
            status=GateStatus(artifact.gate_status),
            decisions=[
                {
                    "rule_id": d.rule_id,
                    "status": d.status,
                    "reason_code": d.reason_code,
                    "input_hash": d.input_hash,
                }
                for d in decisions
            ],
        )
        if body["gate"] != {"status": gate.status.value, "decisions": gate.decisions}:
            raise ValueError("research_publication_source_gate_invalid")
        publication = session.get(
            PublicationDecisionRecord,
            artifact.artifact_id,
            populate_existing=True,
        )
        if publication is None:
            raise ValueError("research_publication_decision_missing")
        original_decided_at = as_utc(cast(datetime | None, publication.decided_at))
        expected_terminal = (
            RunStatus.completed.value
            if gate.status == GateStatus.publish
            else RunStatus.degraded.value
        )
        if (
            publication.run_id != run.run_id
            or publication.artifact_id != artifact.artifact_id
            or publication.status != gate.status.value
            or publication.result_hash != research_result_content_hash(persisted)
            or json.loads(publication.decisions_json) != gate.decisions
            or original_decided_at is None
            or original_decided_at != as_utc(artifact.created_at)
            or original_decided_at != as_utc(source.created_at)
            or original_decided_at != as_utc(run.finished_at)
            or run.status != expected_terminal
        ):
            raise ValueError("research_publication_decision_original_mismatch")
        policy_receipts = authenticate_policy(
            result=persisted,
            references=body.get("policy_checks"),
        )
        if policy_receipts:
            _verify_canonical_policy_gate(gate, policy_receipts[-1])
            if as_utc(policy_receipts[-1].assessed_at) != as_utc(source.created_at):
                raise ValueError("research_policy_commit_time_mismatch")
        if base_schema == PUBLICATION_SOURCE_SCHEMA_V3:
            from .research_probability_binding import publication_values_in_session

            values = publication_values_in_session(
                session, run_id=run.run_id, references=body["kernel_values"]
            )
            if (
                values.graph_final_ref != reference.model_dump(mode="json")
                or values.graph_result_hash != body["graph_result_hash"]
            ):
                raise ValueError("research_probability_publication_graph_invalid")
            _verify_kernel_forecasts(
                session,
                artifact=artifact,
                result=persisted,
                gate=gate,
                values=values,
                references=body["kernel_forecasts"],
                candidate=body["candidate"],
            )
        # No normalization, semantic writes, new signature, or renewed permissions.
        return artifact.artifact_id, gate


def _policy_decisions(receipt: ResearchPolicyCheckReceipt) -> list[dict[str, str]]:
    return [
        {
            "rule_id": "canonical_policy:" + check.rule_id,
            "status": check.status,
            "reason_code": check.reason_code,
            "input_hash": check.input_hash,
        }
        for check in receipt.checks
    ]


def _canonical_policy_passed(receipt: ResearchPolicyCheckReceipt) -> bool:
    from ..decision.research_policy_registry import blocking_checks_passed
    from .research_policy_receipts import _encoded

    # Private Commit receives an executed original; revalidate shape and derive
    # the aggregate nevertheless. This helper grants no producer authority.
    checked, _ = _encoded(receipt)
    if checked.phase != "commit" or checked.assessment_state != "executed":
        raise ValueError("research_policy_commit_receipt_required")
    passed = blocking_checks_passed(checked.checks)
    if checked.all_applicable_blocking_rules_passed and not passed:
        raise ValueError("research_policy_receipt_pass_inconsistent")
    # Historical receipts with a conservative False remain fail-closed. New
    # executed receipts derive True in the checker, never from model claims.
    return checked.all_applicable_blocking_rules_passed and passed


def _canonical_policy_gate(gate: GateResult, receipt: ResearchPolicyCheckReceipt) -> GateResult:
    # Canonical success preserves the prior independent review/calibration/
    # permission gate. It cannot turn a prior reject/research_only into publish.
    passed = _canonical_policy_passed(receipt)
    status = GateStatus.research_only if gate.status == GateStatus.publish and not passed else gate.status
    return GateResult(status=status, decisions=[*gate.decisions, *_policy_decisions(receipt)])


def _verify_canonical_policy_gate(gate: GateResult, receipt: ResearchPolicyCheckReceipt) -> None:
    expected = _policy_decisions(receipt)
    if (
        (not _canonical_policy_passed(receipt) and gate.status == GateStatus.publish)
        or gate.decisions[-len(expected) :] != expected
        or any(
            d["rule_id"].startswith("canonical_policy:") for d in gate.decisions[: -len(expected)]
        )
    ):
        raise ValueError("research_policy_publication_gate_invalid")


def _project_research_candidate(
    result: ResearchSessionResult,
    publishable_research: bool,
) -> dict[str, object]:
    main_links = result.causal_case.main_chain if result.causal_case else []
    opposite_links = result.causal_case.opposite_chain if result.causal_case else []
    facts = [item.statement for item in main_links if item.claim_type == "fact"]
    inferences = [item.statement for item in main_links if item.claim_type != "fact"]
    citations = list(
        dict.fromkeys(ref for item in [*main_links, *opposite_links] for ref in item.evidence_refs)
    )
    first = next((item for item in result.horizons if item.horizon == "30m"), None)
    synthesis_unavailable = result.causal_case is None
    if synthesis_unavailable:
        # Evidence-only results remain useful for audit and follow-up. A
        # failed synthesis must not become a reject that hides all of the
        # accepted source material, but it also must never look like a
        # causal or directional conclusion.
        facts = [
            f"{item.evidence_id}: {item.excerpt[:500]}"
            for item in result.evidence_candidates
            if item.quality == "accepted"
        ]
        inferences = []
        citations = [
            item.evidence_id for item in result.evidence_candidates if item.quality == "accepted"
        ]
    candidate: dict[str, object] = {
        "headline": (
            result.causal_case.thesis
            if result.causal_case
            else "Evidence 已保留，但综合校验未通过，当前不能形成根因结论"
        ),
        "summary": render_research_report_summary(
            result, publishable_research=publishable_research
        ),
        "facts": facts,
        "inferences": inferences,
        "counter_thesis": (
            "；".join(item.statement for item in opposite_links)
            if opposite_links
            else "综合校验未通过，当前没有可发布的主因果链或反链。"
        ),
        "uncertainty": [
            f"{item.requirement_id}: {item.reason_code}" for item in result.final_coverage.gaps
        ],
        "transmission_chain": [item.statement for item in main_links],
        "citations": citations,
        "direction": first.action if first is not None else "no_trade",
        "probability": first.subjective_probability if first is not None else 0.5,
        "trigger": first.trigger if first is not None else "等待充分度与市场确认",
        "invalidation": (first.invalidation if first is not None else "研究证据不足或核心假设失效"),
        # This flag is produced only after deterministic research-result
        # validation (sufficient hard coverage, accepted review and no
        # unresolved hard conflicts). It publishes a complete report even
        # when its safe conclusion is no_trade; it never grants trading
        # authority.
        "research_complete": publishable_research,
    }
    if not publishable_research:
        candidate["direction"] = "no_trade"
        candidate["probability"] = 0.5
        candidate["uncertainty"] = [
            *cast(list[str], candidate["uncertainty"]),
            f"research_sufficiency:{result.stop_reason.code}",
        ]
    return candidate


@dataclass(frozen=True)
class ResearchHorizonProof:
    reference_at: datetime | None
    issue_codes: tuple[str, ...]
    input_hash: str

    @property
    def valid(self) -> bool:
        return self.reference_at is not None and not self.issue_codes


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _research_horizon_proof(
    database: Database,
    run_id: str,
    event_id: str,
    result: ResearchSessionResult | None,
    *,
    horizons: list[HorizonDecision] | None = None,
) -> ResearchHorizonProof:
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise KeyError(run_id)
        return _research_horizon_proof_in_session(session, run, event_id, result, horizons=horizons)


def _research_horizon_proof_in_session(
    session: Session,
    run: RunRecord,
    event_id: str,
    result: ResearchSessionResult | None,
    *,
    horizons: list[HorizonDecision] | None = None,
) -> ResearchHorizonProof:
    """Bind the checker to the saved task and Graph's business-time snapshot.

    There is no fallback to today's Pack, wall clock, or result finish time.
    This does not certify the complete HM04 method/policy contract.
    """
    identity: dict[str, object] = {
        "checker_version": HORIZON_CONTRACT_VERSION,
        "run_id": run.run_id,
        "event_id": event_id,
        "trigger_snapshot_id": run.snapshot_id,
        "decision_snapshot_id": run.decision_snapshot_id,
        "result_hash": research_result_content_hash(result) if result else None,
        "projected_horizons": (
            [item.model_dump(mode="json") for item in horizons] if horizons is not None else None
        ),
    }

    def proof(reference: datetime | None, *issues: str) -> ResearchHorizonProof:
        return ResearchHorizonProof(
            reference,
            tuple(issues),
            _canonical_hash(
                {
                    **identity,
                    "reference_at": reference.isoformat() if reference else None,
                    "issue_codes": list(issues),
                }
            ),
        )

    if result is None:
        return proof(None)
    if run.event_id != event_id:
        return proof(None, "research_publication_event_mismatch")
    if horizons is not None and horizons != list(result.horizons):
        return proof(None, "research_horizon_projection_mismatch")
    try:
        saved = read_task_request(session, run)
    except (ValueError, KeyError, TypeError):
        return proof(None, "research_task_request_invalid")
    if saved is None:
        return proof(None, "research_task_request_unavailable")
    task, task_hash, _source = saved
    identity["task_hash"] = task_hash
    identity["execution_mode"] = task.execution_mode
    if result.request_id != task.request_id:
        return proof(None, "research_publication_request_mismatch")
    trigger = (
        session.get(SnapshotRecord, run.snapshot_id, populate_existing=True)
        if run.snapshot_id
        else None
    )
    snapshot = (
        session.get(SnapshotRecord, run.decision_snapshot_id, populate_existing=True)
        if run.decision_snapshot_id
        else None
    )
    if trigger is None or snapshot is None:
        return proof(None, "research_decision_snapshot_unavailable")
    identity["trigger_snapshot_hash"] = trigger.snapshot_hash
    identity["decision_snapshot_hash"] = snapshot.snapshot_hash
    if (
        trigger.snapshot_type != "trigger"
        or trigger.generation != 1
        or trigger.run_id not in {None, run.run_id}
        or trigger.event_id != event_id
        or snapshot.snapshot_type != "decision"
        or snapshot.generation != 2
        or snapshot.run_id != run.run_id
        or snapshot.event_id != event_id
        or snapshot.parent_snapshot_id != trigger.snapshot_id
        or snapshot.pack_version != trigger.pack_version
    ):
        return proof(None, "research_decision_snapshot_identity_mismatch")
    cutoff = as_utc(cast(datetime | None, snapshot.cutoff_at))
    trigger_cutoff = as_utc(cast(datetime | None, trigger.cutoff_at))
    if cutoff is None or trigger_cutoff is None:
        return proof(None, "research_decision_cutoff_unavailable")
    try:
        trigger_evidence = json.loads(trigger.evidence_json)
        if not isinstance(trigger_evidence, list) or not trigger_evidence:
            raise ValueError("trigger_evidence_invalid")
        # SnapshotService.freeze[_for_run] uses json.dumps' default
        # separators/ensure_ascii here, rather than the decision recipe.
        trigger_digest = hashlib.sha256(
            json.dumps(trigger_evidence, sort_keys=True).encode(),
        ).hexdigest()
        expected_trigger_id = (
            "snap_" + hashlib.sha256(f"{run.run_id}:{trigger_digest}".encode()).hexdigest()[:32]
            if trigger.run_id is not None
            else f"snap_{trigger_digest[:32]}"
        )
        if trigger_digest != trigger.snapshot_hash or expected_trigger_id != trigger.snapshot_id:
            raise ValueError("trigger_hash_invalid")
        original_inputs = {item.evidence_id: item for item in task.input_evidence}
        for item in trigger_evidence:
            original = original_inputs[item["evidence_id"]]
            # SQLite DateTime drops tzinfo before freeze_for_run serializes
            # the Observation. Match the existing UTC projection, while
            # hashing the original JSON semantic recipe unchanged.
            item_cutoff = as_utc(cast(datetime | None, datetime.fromisoformat(item["cutoff_at"])))
            received = as_utc(cast(datetime | None, datetime.fromisoformat(item["received_at"])))
            observed = as_utc(cast(datetime | None, datetime.fromisoformat(item["observed_at"])))
            published = (
                as_utc(datetime.fromisoformat(item["published_at"]))
                if item.get("published_at")
                else None
            )
            if (
                item_cutoff is None
                or received is None
                or observed is None
                or item_cutoff != trigger_cutoff
                or received > trigger_cutoff
                or observed > received
                or (published is not None and published > received)
                or original.received_at != received
                or original.observed_at != observed
                or original.published_at != published
                or original.source_id != item["source_id"]
                or original.content_hash != item["content_hash"]
            ):
                raise ValueError("trigger_input_invalid")
        identity["trigger_cutoff_at"] = trigger_cutoff.isoformat()
    except (ValueError, KeyError, TypeError):
        return proof(None, "research_trigger_snapshot_invalid")
    try:
        evidence = json.loads(snapshot.evidence_json)
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(
                not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str)
                for item in evidence
            )
        ):
            raise ValueError("decision_snapshot_evidence_invalid")
        # Exact semantic recipe used by freeze_decision_snapshot; created_at,
        # pack_version and storage formatting are not its hash inputs.
        digest = _canonical_hash(
            {
                "cutoff_at": cutoff.isoformat(),
                "event_id": event_id,
                "evidence": evidence,
                "generation": 2,
                "parent_snapshot_id": trigger.snapshot_id,
                "run_id": run.run_id,
                "snapshot_type": "decision",
            }
        )
    except (ValueError, TypeError):
        return proof(None, "research_decision_snapshot_invalid")
    if digest != snapshot.snapshot_hash or snapshot.snapshot_id != f"snap_dec_{digest[:28]}":
        return proof(None, "research_decision_snapshot_hash_mismatch")
    if evidence[: len(trigger_evidence)] != trigger_evidence:
        return proof(None, "research_decision_snapshot_invalid")
    if cutoff < trigger_cutoff or cutoff > task.deadline_at or cutoff > task.pit_cutoff_at:
        return proof(None, "research_decision_cutoff_outside_task")
    if any(item.received_at > cutoff for item in task.input_evidence):
        return proof(None, "research_decision_cutoff_before_input")
    if task.execution_mode == "replay" and cutoff != task.pit_cutoff_at:
        return proof(None, "research_replay_cutoff_mismatch")
    checked = validate_horizon_contract(result.horizons, reference_at=cutoff)
    return proof(cutoff, *checked.issue_codes)


def _research_publication_gate(
    candidate: dict[str, object],
    gate: GateResult,
    proof: ResearchHorizonProof,
    publishable: bool,
    *,
    result: ResearchSessionResult,
    directional_calibration_attested: bool,
) -> GateResult:
    # This version's audit row must describe the proof checked here. Caller
    # decisions for unrelated rules retain their original audit information.
    decisions = [item for item in gate.decisions if item["rule_id"] != HORIZON_CONTRACT_VERSION]
    status = gate.status
    if status == GateStatus.publish:
        # Re-run the existing Kernel safeguards for an externally supplied
        # publish decision. Neither the caller flag nor the checker raises caps.
        selected = next((item for item in result.horizons if item.horizon == "30m"), None)
        checked = evaluate_gate(
            {
                **candidate,
                "research_complete": publishable,
                # These are the same typed fields used to create Forecasts.
                # The caller cannot hide a directional/high-probability result
                # behind a safer candidate projection. The probability cap is
                # also checked against every emitted window below.
                "direction": selected.action if selected else Direction.no_trade,
                "probability": selected.subjective_probability if selected else 0.5,
                "trigger": selected.trigger if selected else "",
                "invalidation": selected.invalidation if selected else "",
            },
            directional_value_validated=directional_calibration_attested,
        )
        status = checked.status
        checked_ids = {item["rule_id"] for item in checked.decisions}
        decisions = [item for item in decisions if item["rule_id"] not in checked_ids]
        decisions.extend(checked.decisions)
        if not publishable and status == GateStatus.publish:
            status = GateStatus.research_only
    if result.horizons:
        # Reuse the same Kernel rule for every window, including neutral and
        # no_trade conclusions. Calibration never raises this rule's ceiling.
        # Keep one existing audit rule ID, bound to all actual typed inputs.
        cap_checks = [
            next(
                decision
                for decision in evaluate_gate(
                    {**candidate, "probability": item.subjective_probability},
                    directional_value_validated=directional_calibration_attested,
                ).decisions
                if decision["rule_id"] == "probability_cap"
            )
            for item in result.horizons
        ]
        failed_cap = next((check for check in cap_checks if check["status"] == "fail"), None)
        decisions = [item for item in decisions if item["rule_id"] != "probability_cap"]
        decisions.append(
            {
                "rule_id": "probability_cap",
                "status": "fail" if failed_cap is not None else "pass",
                "reason_code": failed_cap["reason_code"] if failed_cap is not None else "ok",
                "input_hash": _canonical_hash(
                    {
                        "rule_id": "probability_cap",
                        "horizons": [
                            {
                                "horizon": item.horizon,
                                "subjective_probability": item.subjective_probability,
                                "probability_status": item.probability_status,
                            }
                            for item in sorted(result.horizons, key=lambda item: item.horizon)
                        ],
                    }
                ),
            }
        )
        if failed_cap is not None and status == GateStatus.publish:
            status = GateStatus.research_only
    decisions.append(
        {
            "rule_id": HORIZON_CONTRACT_VERSION,
            "status": "pass" if proof.valid else "fail",
            "reason_code": proof.issue_codes[0] if proof.issue_codes else "ok",
            "input_hash": proof.input_hash,
        }
    )
    return GateResult(status=status, decisions=decisions)


@dataclass(frozen=True)
class ForecastAuditContext:
    as_of: str | None
    strategy_version: str | None
    input_snapshot_id: str | None
    input_fingerprint: str | None
    feature_snapshot_json: str | None
    evidence_cutoff_at: datetime | None


def _forecast_audit_context(
    database: Database,
    run_id: str,
    forecasted_at: datetime,
) -> ForecastAuditContext:
    with database.session() as session:
        return _forecast_audit_context_in_session(session, run_id, forecasted_at)


def _forecast_audit_context_in_session(
    session: Session,
    run_id: str,
    forecasted_at: datetime,
) -> ForecastAuditContext:
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise KeyError(run_id)
    snapshot = (
        session.get(SnapshotRecord, run.snapshot_id, populate_existing=True)
        if run.snapshot_id
        else None
    )
    receipt: ResearchStrategyReceipt | None = None
    facts = (
        session.query(ResearchFactRecord)
        .populate_existing()
        .filter_by(run_id=run_id, quality="accepted")
        .order_by(ResearchFactRecord.accepted_at.asc(), ResearchFactRecord.fact_id.asc())
        .all()
    )
    for fact in facts:
        try:
            attributes = json.loads(fact.attributes_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(attributes, dict):
            continue
        raw = attributes.get("daily_strategy_receipt")
        if raw is None:
            raw = attributes.get("daily_strategy_receipt_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw, dict):
            continue
        try:
            receipt = ResearchStrategyReceipt.model_validate(raw)
        except ValueError:
            continue
        break

    strategy_version = receipt.strategy_version if receipt else run.strategy_version
    as_of = (
        receipt.as_of
        if receipt
        else (snapshot.cutoff_at.isoformat() if snapshot is not None else forecasted_at.isoformat())
    )
    features = receipt.features if receipt else {}
    feature_snapshot_json = json.dumps(
        features,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint_payload = {
        "snapshot_hash": snapshot.snapshot_hash if snapshot is not None else None,
        "receipt_data_hash": receipt.data_hash if receipt else None,
        "strategy_version": strategy_version,
        "as_of": as_of,
        "features": features,
    }
    input_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ForecastAuditContext(
        as_of=as_of,
        strategy_version=strategy_version,
        input_snapshot_id=run.snapshot_id,
        input_fingerprint=input_fingerprint,
        feature_snapshot_json=feature_snapshot_json,
        evidence_cutoff_at=snapshot.cutoff_at if snapshot is not None else forecasted_at,
    )


def _research_result_is_publishable(
    result: ResearchSessionResult | None,
    *,
    native_review_attested: bool = False,
    additional_allowed_evidence_ids: set[str] | None = None,
    directional_calibration_attested: bool = False,
    reference_at: datetime | None = None,
) -> bool:
    if result is None:
        return True
    return (
        result.status == "completed"
        and result.causal_case is not None
        and len(result.horizons) == 3
        and {item.horizon for item in result.horizons} == {"30m", "24h", "72h"}
        and reference_at is not None
        and validate_horizon_contract(result.horizons, reference_at=reference_at).valid
        and result.final_coverage.status == "sufficient"
        and result.stop_reason.code == "sufficient"
        and result.final_coverage.hard_coverage_ratio == 1.0
        and not any(
            gap.importance == "hard" or gap.blocks_directional_output
            for gap in result.final_coverage.gaps
        )
        and not any(item.severity == "hard" for item in result.final_coverage.conflicts)
        and not result.stop_reason.remaining_hard_gaps
        and native_review_attested
        and has_valid_accepted_independent_review(
            result,
            additional_allowed_evidence_ids=additional_allowed_evidence_ids,
            native_provenance_verified=native_review_attested,
        )
        # A model-authored ``probability_status=calibrated`` is not a trusted
        # calibration receipt. Until the signal kernel can attach a model/data
        # version and sample evidence, directional forecasts remain research
        # hypotheses and cannot become published Forecast records.
        and (
            all(item.action not in {"long", "short"} for item in result.horizons)
            or directional_calibration_attested
        )
    )


def _fail_closed_horizon(
    horizon: HorizonDecision,
    stop_code: str,
) -> HorizonDecision:
    return horizon.model_copy(
        update={
            "action": Direction.no_trade,
            "subjective_probability": 0.5,
            "probability_status": "uncalibrated",
            "confidence_cap_reason": (
                f"Directional output suppressed because research stopped at {stop_code}."
            ),
        }
    )


def _remove_unattested_calibration(
    result: ResearchSessionResult,
    *,
    strategy_version: str | None = None,
    calibration_receipt_ids: dict[str, str] | None = None,
    receipt_validates: Callable[..., bool] | None = None,
) -> tuple[ResearchSessionResult, bool]:
    """Downgrade model-authored calibration until a kernel receipt exists."""

    normalized: list[HorizonDecision] = []
    directional_attestations: list[bool] = []
    for horizon in result.horizons:
        directional = horizon.action in {"long", "short"}
        receipt_id = (calibration_receipt_ids or {}).get(horizon.horizon)
        attested = bool(
            directional
            and horizon.probability_status == "calibrated"
            and strategy_version
            and receipt_validates is not None
            and receipt_validates(
                receipt_id,
                strategy_version=strategy_version,
                horizon=horizon.horizon,
            )
        )
        if directional:
            directional_attestations.append(attested)
        if horizon.probability_status != "calibrated" or attested:
            normalized.append(horizon)
            continue
        existing = (horizon.confidence_cap_reason or "").strip()
        reason = "No kernel calibration receipt is attached to this horizon."
        normalized.append(
            horizon.model_copy(
                update={
                    "probability_status": "uncalibrated",
                    "confidence_cap_reason": f"{existing} {reason}".strip(),
                }
            )
        )
    return (
        result.model_copy(update={"horizons": normalized}),
        bool(directional_attestations) and all(directional_attestations),
    )


def _durable_native_review_matches(
    database: Database,
    run_id: str,
    result: ResearchSessionResult | None,
) -> bool:
    with database.session() as session:
        return _native_review_binding_in_session(session, run_id, result) is not None


def _native_review_binding_in_session(
    session: Session,
    run_id: str,
    result: ResearchSessionResult | None,
) -> tuple[str, str, str, str] | None:
    if result is None or result.reviews is None or len(result.reviews) != 1:
        return None
    row = (
        session.query(RunRoleOutputRecord)
        .populate_existing()
        .filter_by(
            run_id=run_id,
            role="independent_reviewer",
        )
        .one_or_none()
    )
    if row is None:
        return None
    try:
        payload = json.loads(row.payload_json)
        recorded_at = as_utc(cast(datetime | None, row.created_at))
        if (
            row.schema_version != "research-review.native-attested.v1"
            or payload != result.reviews[0].model_dump(mode="json")
            or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
            or recorded_at is None
        ):
            return None
    except (ValueError, TypeError):
        return None
    return row.output_id, row.schema_version, row.payload_hash, recorded_at.isoformat()


def _calibration_binding_in_session(
    session: Session,
    result: ResearchSessionResult | None,
    strategy_version: str | None,
    receipt_ids: dict[str, str] | None,
) -> tuple[tuple[tuple[str, ...], ...], bool]:
    bindings: list[tuple[str, ...]] = []
    validations: list[bool] = []
    for horizon in result.horizons if result else []:
        if horizon.action not in {"long", "short"}:
            continue
        receipt_id = (receipt_ids or {}).get(horizon.horizon)
        row = (
            session.get(StrategyCalibrationRecord, receipt_id, populate_existing=True)
            if receipt_id
            else None
        )
        if row is None:
            bindings.append((horizon.horizon, receipt_id or "", "missing"))
            validations.append(False)
            continue
        # Preserve the original passed/strategy/horizon predicate. The content
        # hash and record identity prove this is still the same immutable
        # Kernel receipt; no metrics are regraded and no thresholds change.
        digest = hashlib.sha256(row.report_json.encode()).hexdigest()
        bindings.append(
            (
                horizon.horizon,
                row.receipt_id,
                row.strategy_version,
                row.horizon,
                str(row.passed),
                row.dataset_hash,
                row.report_hash,
                digest,
            )
        )
        validations.append(
            bool(
                horizon.probability_status == "calibrated"
                and strategy_version
                and row.passed
                and row.strategy_version == strategy_version
                and row.horizon == horizon.horizon
                and digest == row.report_hash
            )
        )
    return tuple(bindings), bool(validations) and all(validations)


def _trigger_evidence_ids(database: Database, run_id: str) -> set[str]:
    run = database.get_run_record(run_id)
    if run is None or run.snapshot_id is None:
        return set()
    try:
        evidence = database.get_snapshot_evidence(run.snapshot_id)
    except (KeyError, ValueError):
        return set()
    return {
        str(item["evidence_id"]) for item in evidence if isinstance(item.get("evidence_id"), str)
    }
