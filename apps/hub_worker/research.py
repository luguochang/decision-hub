from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.hub_worker.work_admission import HeavyWorkAdmission
from packages.contracts_py.decision_hub_contracts import (
    ContextExclusion,
    DomainPackManifest,
    ErrorProvenance,
    EvidenceCandidate,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
    RunStatus,
)
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchCompiledPolicyReceiptV1,
    ResearchRequestPackReceiptV2,
    ResearchTaskReceiptV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
)
from packages.kernel.decision_hub_kernel.application.action_research_integration import (
    ActionResearchIntegration,
    register_action_opportunities_in_session,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.component_consumption_binding import (
    component_consumption_reference_in_session,
)
from packages.kernel.decision_hub_kernel.application.event_watch import (
    EventWatchService,
    _sample_model,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    RECIPE_COMPONENT_REF,
    SpotResearchRecipeConsumptionInput,
    select_registered_spot_research_recipe,
)
from packages.kernel.decision_hub_kernel.application.public_opinion import (
    PublicOpinionResearchContext,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    BUNDLE_ROLES,
    FrozenResearchBundle,
    LegacyResearchBundle,
    PreparedResearchPolicyRefs,
    _has_execution,  # pyright: ignore[reportPrivateUsage]
    factory_event_identity,
    freeze_research_bundle_in_session,
    load_prepared_pack_definition,
    read_factory_source_in_session,
    read_research_bundle_in_session,
    task_scope,
)
from packages.kernel.decision_hub_kernel.application.research_context import (
    build_task_policy,
    project_excerpt,
    research_context_cutoff,
    select_context,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
    parse_graph_final_ref,
    prepare_graph_state_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    AgenticResearchState,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    freeze_graph_recipe_selection_in_session,
    graph_recipe_prepared_manifest_scope,
    graph_recipe_task_creation_scope,
    prepare_graph_recipe_manifest_v2,
)
from packages.kernel.decision_hub_kernel.application.research_harness_consumption import (
    HarnessFirstFreezeService,
    PreparedHarnessConsumption,
    read_harness_consumption_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_library_provenance import (
    is_known_test_hash,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_pack import (
    ResearchPackError,
    ResearchPackService,
)
from packages.kernel.decision_hub_kernel.application.research_policy import (
    load_research_policy,
    pack_file,
)
from packages.kernel.decision_hub_kernel.application.research_policy_compiler import compile_policy
from packages.kernel.decision_hub_kernel.application.research_policy_resources import canonical_hash
from packages.kernel.decision_hub_kernel.application.research_review import (
    ResearchReviewService,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import (
    ReviewScheduleService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalystClaimRecord,
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    EventWindowSampleRecord,
    ResearchDocumentBlockRecord,
    ResearchDocumentCorrectionRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import (
    is_sqlite_initial_begin_busy,
    is_sqlite_storage_busy,
)
from packages.kernel.decision_hub_kernel.ports.market_component_consumer import (
    MarketComponentConsumer,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    PERPETUAL_OUTPUT_SCHEMA_REFS,
    perpetual_output_schema_for_admission,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.provider_adapters.research import CryptoMacroFactPack


class ResearchWorkerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    run_id: str | None = None
    artifact_id: str | None = None
    error_code: str | None = None
    retryable: bool = False
    recorded_in_database: bool | None = None


if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.research_harness_frozen_run import (
        FrozenRunHarnessService,
    )


@dataclass(frozen=True)
class _PendingTerminalFailure:
    event_id: str
    run_id: str
    error: Exception
    error_code: str
    phase: str
    pending_candidate: bool
    pending_reason: str | None


type ContextInputsProvider = Callable[
    [Session, StudyRecord, datetime], tuple[ResearchInputEvidence, ...]
]


@runtime_checkable
class ModeAwareContextInputsProvider(Protocol):
    def for_research_mode(
        self,
        session: Session,
        study: StudyRecord,
        cutoff_at: datetime,
        *,
        research_mode: Literal["historical_review", "current_decision"],
    ) -> tuple[ResearchInputEvidence, ...]: ...


class ResearchRequestFactory:
    """Build typed requests from the immutable trigger snapshot and Domain Pack."""

    def __init__(
        self,
        database: Database,
        *,
        pack_root: Path,
        allowed_capabilities: Iterable[str] = ("replay.research",),
        execution_mode: Literal["live", "replay"] = "live",
        prepared_policy_refs: PreparedResearchPolicyRefs | None = None,
        spot_prepared_pack_root: Path | None = None,
        spot_allowed_capabilities: Iterable[str] | None = None,
        context_inputs_provider: ContextInputsProvider | None = None,
        component_consumer: MarketComponentConsumer | None = None,
        harness_first_freeze: HarnessFirstFreezeService | None = None,
        decision_output_enabled: bool = False,
        perpetual_binding_service: Any | None = None,
        opinion_context: PublicOpinionResearchContext | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.pack_root = pack_root
        self.allowed_capabilities = tuple(dict.fromkeys(allowed_capabilities))
        self.execution_mode = execution_mode
        if type(decision_output_enabled) is not bool:
            raise ValueError("research_decision_output_configuration_invalid")
        self.decision_output_enabled = decision_output_enabled
        if perpetual_binding_service is not None and perpetual_binding_service.database is not database:
            raise ValueError("perpetual_factory_database_mismatch")
        self.perpetual_binding_service = perpetual_binding_service
        self.opinion_context = opinion_context
        self.clock = clock
        self.context_inputs_provider = context_inputs_provider
        self.component_consumer = component_consumer
        self.packs = ResearchPackService(database, clock=clock)
        self.loaded_pack: dict[str, Any]
        self.prepared_pack = (
            load_prepared_pack_definition(
                pack_root,
                prepared_policy_refs,
                allowed_capabilities=self.allowed_capabilities,
                execution_mode=execution_mode,
            )
            if prepared_policy_refs is not None
            else None
        )
        self.harness_first_freeze = harness_first_freeze
        if harness_first_freeze is not None:
            if self.prepared_pack is None:
                raise ValueError("harness_prepared_factory_required")
            if harness_first_freeze.deployment.database is not database:
                raise ValueError("harness_factory_database_mismatch")
            harness_first_freeze.validate_factory_baseline(self.prepared_pack)
        if self.prepared_pack is not None:
            self.loaded_pack = self.prepared_pack.model_dump(mode="json")
            policy = self.loaded_pack["evidence_policy"]
        else:
            # Keep the original default v1 loading and rendering contract.
            manifest = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))
            parsed_manifest = DomainPackManifest.model_validate(manifest)
            policy = yaml.safe_load(
                pack_file(pack_root, parsed_manifest.evidence_policy_ref).read_text(
                    encoding="utf-8"
                )
            )
            self.loaded_pack = {
                "scope": "request_manifest_evidence_and_research_instructions",
                "manifest": parsed_manifest.model_dump(mode="json"),
                "evidence_policy": policy,
                "research_policy": load_research_policy(pack_root, parsed_manifest),
                "allowed_capabilities": list(self.allowed_capabilities),
                "execution_mode": execution_mode,
            }
        self.fact_pack = CryptoMacroFactPack.from_manifest(policy)
        self.event_watches = EventWatchService(database, clock=clock)
        self.spot_factory = (
            ResearchRequestFactory(
                database,
                pack_root=spot_prepared_pack_root,
                allowed_capabilities=tuple(spot_allowed_capabilities)
                if spot_allowed_capabilities is not None
                else self.allowed_capabilities,
                context_inputs_provider=context_inputs_provider,
                component_consumer=component_consumer,
                execution_mode=execution_mode,
                decision_output_enabled=decision_output_enabled,
                opinion_context=opinion_context,
                prepared_policy_refs=prepared_policy_refs,
                clock=clock,
            )
            if spot_prepared_pack_root is not None
            else None
        )

    def validate_scope(self, event_id: str, run_id: str) -> DomainPackManifest:
        receipt = self.packs.get(run_id)
        definition = receipt["definition"] if receipt else self.loaded_pack
        self._validate_execution_scope(definition)
        pack = DomainPackManifest.model_validate(definition["manifest"])
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.event_id != event_id:
                raise ValueError("research_run_event_mismatch")
            study = session.get(StudyRecord, run.study_id) if run.study_id else None
            if study is None:
                raise ValueError("research_study_scope_missing")
            require_run_research_scope_in_session(
                session, run, domain_pack_ref=f"{pack.pack_id}.v{pack.version.split('.')[0]}"
            )
        return pack

    def _validate_execution_scope(self, definition: dict[str, Any]) -> None:
        if not set(definition["allowed_capabilities"]).issubset(self.allowed_capabilities):
            raise ResearchPackError("research_pack_capability_revoked")
        if definition["execution_mode"] != self.execution_mode:
            raise ResearchPackError("research_pack_execution_mode_changed")

    def build(self, event_id: str, run_id: str) -> ResearchSessionRequest:
        if self.spot_factory is not None:
            with self.database.session() as session:
                if read_run_spot_admission_in_session(session, run_id) is not None:
                    return self.spot_factory.build(event_id, run_id)
        if self.prepared_pack is not None:
            prepared = self._build_prepared(event_id, run_id)
            if prepared is not None:
                return prepared
        return self._build_legacy(event_id, run_id)

    def _first_freeze_discovery(self, event_id: str, run_id: str) -> tuple[bool, dict[str, Any]]:
        # Own a physical read-only connection. No ORM Session/transaction crosses
        # the source/schema CPU preparation below or an await.
        with self.database.engine.connect() as connection:
            prior = int(connection.exec_driver_sql("PRAGMA query_only").scalar_one())
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                with Session(bind=connection) as discovery:
                    run = discovery.get(RunRecord, run_id, populate_existing=True)
                    if run is None or run.event_id != event_id:
                        raise ValueError("research_run_event_mismatch")
                    guard = {key: getattr(run, key) for key in (
                        "run_id", "event_id", "study_id", "strategy_version",
                        "status", "lease_owner",
                    )}
                    prior_bundle = discovery.scalar(
                        select(RunRoleOutputRecord.output_id)
                        .filter_by(run_id=run_id)
                        .where(RunRoleOutputRecord.role.in_(BUNDLE_ROLES)).limit(1)
                    )
                    exists = prior_bundle is not None or _has_execution(discovery, run_id)
            finally:
                connection.rollback()
                connection.exec_driver_sql(f"PRAGMA query_only={prior}")
        return exists, guard

    def _assert_first_freeze_run(self, session: Session, guard: dict[str, Any]) -> None:
        run = session.get(RunRecord, guard["run_id"], populate_existing=True)
        now = self.clock().astimezone(UTC)
        if (
            run is None
            or any(getattr(run, key) != value for key, value in guard.items()
                   if key != "deadline_at")
            or run.status not in {"admitted", "dispatched", "running"}
            or run.finished_at is not None or run.artifact_id is not None
            or run.error_code is not None
            or (run.lease_owner is not None and (
                as_utc(run.lease_expires_at) is None or as_utc(run.lease_expires_at) <= now
            ))
            or (guard.get("deadline_at") is not None and guard["deadline_at"] <= now)
        ):
            raise ValueError("research_factory_first_freeze_fence_changed")

    def _build_prepared(self, event_id: str, run_id: str) -> ResearchSessionRequest | None:
        """Freeze actual source and all receipts in one caller BEGIN IMMEDIATE."""
        from packages.kernel.decision_hub_kernel.application import (
            perpetual_forecast_scoring_inputs,
        )
        assert self.prepared_pack is not None
        prepared_harness: PreparedHarnessConsumption | None = None
        has_original_execution, first_freeze_guard = self._first_freeze_discovery(event_id, run_id)
        prepared_perpetual = (
            self.perpetual_binding_service.prepare_for_factory(event_id, run_id)
            if self.perpetual_binding_service is not None else None
        )
        prepared_experience = None
        if prepared_perpetual is not None and not has_original_execution:
            prepared_experience = (
                perpetual_forecast_scoring_inputs.prepare_perpetual_experience_inputs(
                    self.database, prepared_perpetual.value
                )
            )
        if self.perpetual_binding_service is None:
            from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
                read_perpetual_admission_identity_in_session,
            )
            with self.database.session() as discovery:
                if read_perpetual_admission_identity_in_session(discovery, run_id) is not None:
                    raise ValueError("perpetual_factory_binding_service_required")
        prepared_manifest = None
        if not has_original_execution:
            # Pure source/schema construction precedes BEGIN IMMEDIATE. The
            # native scope below rechecks all bytes/resolutions before COMMIT;
            # no DB original, permission or rule outcome is memoized here.
            prepared_manifest = prepare_graph_recipe_manifest_v2()
            from packages.contracts_py.decision_hub_contracts.execution_contract_base import (
                execution_schema_registry,
            )
            execution_schema_registry()
            if self.harness_first_freeze is not None:
                prepared_harness = self.harness_first_freeze.prepare()
        with self.database.session() as session, ExitStack() as prepared_sources:
            if session.bind is None or session.bind.dialect.name != "sqlite":
                raise ValueError("research_bundle_sqlite_required")
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None or run.event_id != event_id:
                raise ValueError("research_run_event_mismatch")
            saved = read_research_bundle_in_session(session, run_id)
            if isinstance(saved, FrozenResearchBundle):
                self._validate_execution_scope(saved.pack.definition.model_dump(mode="json"))
                if self.harness_first_freeze is not None:
                    read_harness_consumption_in_session(session, saved)
                if saved.request.output_schema_ref in PERPETUAL_OUTPUT_SCHEMA_REFS:
                    if prepared_perpetual is None:
                        raise ValueError("perpetual_factory_frozen_admission_missing")
                    prepared_perpetual.verify(session)
                return saved.request
            if isinstance(saved, LegacyResearchBundle):
                # Existing v1 records retain their original compatibility path.
                if (
                    self.packs.get_in_session(session, run_id) is None
                    and ResearchTaskService(self.database).get_in_session(session, run_id) is None
                ):
                    # Opt-in cannot backfill a v1 Pack with a new v2 definition.
                    raise ValueError("research_bundle_legacy_original_missing")
                return None
            if prepared_manifest is None:
                raise ValueError("research_factory_first_freeze_discovery_changed")
            self._assert_first_freeze_run(session, first_freeze_guard)
            if prepared_perpetual is not None:
                prepared_perpetual.verify(session)
            if prepared_experience is not None:
                prepared_experience.verify(session)
                prepared_sources.callback(prepared_experience.verify_after_writes, session)
            # Exit order: source bytes/resolution first, then current Run fence,
            # then the owning Database context's physical COMMIT.
            prepared_sources.callback(self._assert_first_freeze_run, session, first_freeze_guard)
            prepared_sources.enter_context(
                graph_recipe_prepared_manifest_scope(session, prepared_manifest)
            )
            if self.harness_first_freeze is not None:
                if prepared_harness is None:
                    raise ValueError("harness_factory_original_bundle_changed")
                self.harness_first_freeze.verify_new_choice_in_session(
                    session, prepared_harness, run_id
                )
                definition = prepared_harness.definition.model_dump(mode="json")
            else:
                definition = self.prepared_pack.model_dump(mode="json")
            self._validate_execution_scope(definition)
            pack = DomainPackManifest.model_validate(definition["manifest"])
            study = (
                session.get(StudyRecord, run.study_id, populate_existing=True)
                if run.study_id
                else None
            )
            if study is None:
                raise ValueError("research_study_scope_missing")
            require_run_research_scope_in_session(
                session, run, domain_pack_ref=f"{pack.pack_id}.v{pack.version.split('.')[0]}"
            )
            spot_binding = read_run_spot_admission_in_session(session, run_id)
            recipe_consumption_ref = None
            if spot_binding is not None:
                actual_pack_path = self.pack_root / "pack.yaml"
                expected_path = (
                    Path(__file__).resolve().parents[2] / spot_binding.effective.pack.ref
                )
                if (
                    actual_pack_path.resolve() != expected_path.resolve()
                    or hashlib.sha256(actual_pack_path.read_bytes()).hexdigest()
                    != spot_binding.effective.pack.sha256
                ):
                    raise ValueError("spot_factory_effective_pack_mismatch")
                if self.component_consumer is not None:
                    session.flush()
                    recipe_known_at = research_context_cutoff(self.clock())
                    recipe_input = SpotResearchRecipeConsumptionInput.model_validate(
                        {
                            "run_id": run_id,
                            "event_id": event_id,
                            "scope_id": spot_binding.scope.scope_id,
                            "admission_hash": spot_binding.admission_proof_hash,
                            "effective_binding_hash": spot_binding.effective_binding_hash,
                            "effective": spot_binding.effective.model_dump(mode="json"),
                            "definition": definition,
                            "definition_hash": canonical_hash(definition),
                        }
                    )
                    recipe_payload = recipe_input.model_dump(mode="json")
                    recipe_request_id = f"recipe:{run_id}"
                    value, receipt = self.component_consumer(
                        session,
                        kind="research_recipe",
                        component_ref=RECIPE_COMPONENT_REF,
                        scope_id=recipe_input.scope_id,
                        request_id=recipe_request_id,
                        known_at=recipe_known_at,
                        cutoff_at=recipe_known_at,
                        payload=recipe_payload,
                        fallback=select_registered_spot_research_recipe,
                    )
                    if canonical_hash(value) != recipe_input.definition_hash:
                        raise ValueError("spot_factory_component_configuration_changed")
                    recipe_consumption_ref = component_consumption_reference_in_session(
                        session,
                        receipt,
                        kind="research_recipe",
                        component_ref=RECIPE_COMPONENT_REF,
                        scope_id=recipe_input.scope_id,
                        request_id=recipe_request_id,
                        known_at=recipe_known_at,
                        payload=recipe_payload,
                        value=value,
                    )
            snapshot_service = SnapshotService(self.database, clock=self.clock)
            snapshot_id, _ = snapshot_service.freeze_for_run_in_session(session, run_id, event_id)
            rows = snapshot_service.read_evidence_in_session(session, snapshot_id)
            source = read_factory_source_in_session(session, run_id)
            input_evidence = [_input_evidence(row, max_chars=4000) for row in rows]
            library_candidates = _library_candidates_in_session(session, run_id)
            historical_review = any(
                marker in item.excerpt
                for item in input_evidence
                for marker in ("历史事件复盘", "历史复盘", "historical review")
            )
            family, _, _ = factory_event_identity(source)
            fact_pack = (
                CryptoMacroFactPack.from_manifest(
                    definition["evidence_policy"], instrument=study.instrument
                )
                if spot_binding is not None or prepared_perpetual is not None
                else CryptoMacroFactPack.from_manifest(definition["evidence_policy"])
            )
            requirements = list(fact_pack.contract_requirements_for_event(family))
            if prepared_perpetual is not None and not historical_review:
                requirements = [
                    item.model_copy(update={"description": (
                        f"Build a reproducible {fact_pack.asset} daily trend baseline "
                        "from at least 60 completed candles as historical context only. "
                        "Forward judgments must use this Run's exact frozen perpetual target and horizon."
                    )})
                    if item.requirement_id == "market_regime_context" else item
                    for item in requirements
                ]
            allowed_capabilities = tuple(
                capability for capability in definition["allowed_capabilities"]
            )
            # Keep Pack-declared fetch routes when search is not enabled.
            # A known approved URL still needs the frozen capability allowlist,
            # source registry and ordinary Gateway admission; this grants none.
            if historical_review:
                requirements = [
                    item
                    for item in requirements
                    if item.requirement_id
                    not in {
                        "expectation_pricing",
                        "crypto_spot_confirmation",
                        "derivatives_crowding",
                    }
                ]
            now = research_context_cutoff(self.clock())
            execution_budget = pack.execution_budget
            if prepared_perpetual is not None:
                from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
                    PERPETUAL_RESEARCH_RUNTIME_SECONDS,
                )
                execution_budget = execution_budget.model_copy(
                    update={"total_deadline_seconds": PERPETUAL_RESEARCH_RUNTIME_SECONDS}
                )
            deadline_at = now + timedelta(seconds=execution_budget.total_deadline_seconds)
            if prepared_perpetual is not None:
                admission = prepared_perpetual.value
                runtime_deadline = (
                    admission["runtime_deadline_at"]
                    if admission["schema_version"] == "perpetual-research-admission.v3"
                    else admission.get("runtime_deadline_at", admission["valid_until"])
                )
                deadline_at = min(deadline_at, datetime.fromisoformat(runtime_deadline))
                if deadline_at <= now:
                    raise ValueError("perpetual_research_admission_expired")
            pit_cutoff_at = (
                max(item.received_at for item in input_evidence)
                if self.execution_mode == "replay"
                else deadline_at
            )
            opinion_candidates = ()
            if (
                self.decision_output_enabled
                and spot_binding is not None
                and not historical_review
                and self.opinion_context is not None
            ):
                opinion_candidates = self.opinion_context.prepare_candidates_in_session(
                    session,
                    run_id=run_id,
                    research_family="crypto_macro",
                    instrument_scopes=(study.instrument,),
                    cutoff=min(now, pit_cutoff_at),
                )
                # The public context may have frozen the automatic version set.
                library_candidates = _library_candidates_in_session(session, run_id)
                by_claim = {item.evidence_id: item for item in opinion_candidates}
                library_candidates = tuple(
                    by_claim.get(item.evidence_id, item) for item in library_candidates
                )
                # Automatic library selection is part of the first Task's
                # immutable factory source, before policy and bundle hashing.
                source = read_factory_source_in_session(session, run_id)
            event_watch = (
                # Reuse the original DTO through the public request validator.
                source.watch.model_dump(mode="json") if source.watch is not None else None
            )
            samples = (
                [
                    _sample_model(row)
                    for row in session.scalars(
                        select(EventWindowSampleRecord)
                        .filter_by(watch_id=source.watch.watch_id)
                        .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.offset)
                        .execution_options(populate_existing=True)
                    ).all()
                ]
                if source.watch is not None
                else []
            )
            if historical_review and execution_budget.per_model_step_timeout_seconds < 240:
                execution_budget = execution_budget.model_copy(
                    update={"per_model_step_timeout_seconds": 240}
                )
            context_inputs: tuple[ResearchInputEvidence, ...] = ()
            protected_inputs: tuple[ResearchInputEvidence, ...] = ()
            if spot_binding is not None and self.context_inputs_provider is not None:
                session.flush()
                selection_cutoff = (
                    pit_cutoff_at if self.execution_mode == "replay" else min(now, pit_cutoff_at)
                )
                provider = self.context_inputs_provider
                provided = (
                    provider.for_research_mode(
                        session,
                        study,
                        selection_cutoff,
                        research_mode="historical_review"
                        if historical_review
                        else "current_decision",
                    )
                    if isinstance(provider, ModeAwareContextInputsProvider)
                    else provider(session, study, selection_cutoff)
                )
                context_inputs = tuple(
                    ResearchInputEvidence.model_validate_json(item.model_dump_json())
                    for item in provided
                )
                if len(context_inputs) > 5 or any(
                    item.kind != "document"
                    or item.authority != "unverified"
                    or item.source_id != "market_case_ledger.v1"
                    or item.observed_at > selection_cutoff
                    or item.received_at > selection_cutoff
                    for item in context_inputs
                ):
                    raise ValueError("spot_factory_historical_context_invalid")
                input_evidence = [*input_evidence, *context_inputs]
            from packages.kernel.decision_hub_kernel.application.action_review import (
                action_review_context_inputs_in_session,
            )

            from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import cpi_pre_event_context_inputs_in_session
            cpi_inputs = (
                cpi_pre_event_context_inputs_in_session(session, cutoff_at=min(now, pit_cutoff_at))
                if (spot_binding is not None or prepared_perpetual is not None) and not historical_review else ()
            )
            input_evidence = [*input_evidence, *cpi_inputs]
            from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import event_owned_scenario_context_inputs_in_session
            # Consume an already-published calendar-event original. Raw source
            # input clocks, original PIT and deadline are preserved separately.
            scenario_inputs = (
                event_owned_scenario_context_inputs_in_session(session, cutoff_at=now)
                if spot_binding is not None and not historical_review and self.execution_mode != "replay" else ()
            )
            if scenario_inputs:
                now = research_context_cutoff(self.clock())
            input_evidence = [*input_evidence, *scenario_inputs]
            from packages.kernel.decision_hub_kernel.application.event_opportunity_snapshot import event_opportunity_context_inputs_in_session
            opportunity_inputs = (
                event_opportunity_context_inputs_in_session(session, run_id, instrument=study.instrument, cutoff_at=now)
                if scenario_inputs else ()
            )
            if opportunity_inputs:
                now = research_context_cutoff(self.clock())
            input_evidence = [*input_evidence, *opportunity_inputs]
            review_inputs = action_review_context_inputs_in_session(session, run_id)
            input_evidence = [*input_evidence, *review_inputs]
            experience_inputs = prepared_experience.value if prepared_experience is not None else ()
            input_evidence = [*input_evidence, *experience_inputs]
            task_policy = build_task_policy(
                research_mode="historical_review" if historical_review else "current_decision",
                requirements=requirements,
                created_at=now,
                max_evidence_rounds=execution_budget.max_evidence_rounds,
                context_inputs=(*review_inputs, *cpi_inputs, *scenario_inputs, *opportunity_inputs),
            )
            if spot_binding is not None:
                from packages.kernel.decision_hub_kernel.application import (
                    research_context_selector_v3,
                )

                select_context_v3 = research_context_selector_v3.select_context_v3
                from packages.kernel.decision_hub_kernel.application.research_run_scope import (
                    spot_run_context_inputs_in_session,
                )

                protected_inputs = (
                    *spot_run_context_inputs_in_session(session, run_id),
                    *context_inputs,
                    *review_inputs,
                    *cpi_inputs,
                    *scenario_inputs,
                    *opportunity_inputs,
                )
                input_evidence = [
                    *input_evidence,
                    *spot_run_context_inputs_in_session(session, run_id),
                ]
                computed = select_context_v3(
                    input_evidence,
                    library_candidates,
                    requirements=requirements,
                    policy=task_policy,
                    prior_exclusions=_parent_library_exclusions_in_session(session, run_id),
                    manifest_prefix=f"context:{run_id}",
                    revision=1,
                    created_at=now,
                    allowed_capabilities=allowed_capabilities,
                    eligibility_cutoff_at=(
                        pit_cutoff_at
                        if self.execution_mode == "replay"
                        else min(now, pit_cutoff_at)
                    ),
                    protected_refs=tuple(item.evidence_id for item in protected_inputs),
                )
                input_evidence, context_manifest = list(computed.projected), computed.manifest
            elif prepared_perpetual is not None and cpi_inputs:
                from packages.kernel.decision_hub_kernel.application.research_context_selector_v3 import (
                    select_context_v3,
                )

                computed = select_context_v3(
                    input_evidence,
                    library_candidates,
                    requirements=requirements,
                    policy=task_policy,
                    prior_exclusions=_parent_library_exclusions_in_session(session, run_id),
                    manifest_prefix=f"context:{run_id}",
                    revision=1,
                    created_at=now,
                    allowed_capabilities=allowed_capabilities,
                    eligibility_cutoff_at=(
                        pit_cutoff_at if self.execution_mode == "replay" else min(now, pit_cutoff_at)
                    ),
                    protected_refs=tuple(item.evidence_id for item in cpi_inputs),
                )
                input_evidence, context_manifest = list(computed.projected), computed.manifest
            else:
                input_evidence, context_manifest = select_context(
                    input_evidence,
                    library_candidates,
                    requirements=requirements,
                    policy=task_policy,
                    prior_exclusions=_parent_library_exclusions_in_session(session, run_id),
                    manifest_prefix=f"context:{run_id}",
                    revision=1,
                    created_at=now,
                    allowed_capabilities=allowed_capabilities,
                    eligibility_cutoff_at=(
                        pit_cutoff_at
                        if self.execution_mode == "replay"
                        else min(now, pit_cutoff_at)
                    ),
                )
            if opinion_candidates and self.opinion_context is not None:
                self.opinion_context.freeze_selected_in_session(
                    session,
                    run_id=run_id,
                    selected_inputs=input_evidence,
                    cutoff=min(now, pit_cutoff_at),
                )
            by_id = {item.evidence_id: item for item in library_candidates}
            receipts = [
                {
                    "version_id": by_id[item.evidence_id].source_id.removeprefix(
                        "research-library:"
                    ),
                    "evidence_id": item.evidence_id,
                    "excerpt": item.excerpt,
                    "excerpt_hash": hashlib.sha256(item.excerpt.encode()).hexdigest(),
                    "original_file_hash": by_id[item.evidence_id].source_content_hash,
                    "locator_refs": list(
                        dict.fromkeys(re.findall(r"document://[^;\s\]]+", item.excerpt))
                    ),
                }
                for item in input_evidence
                if item.evidence_id in by_id
            ]
            run_scope = session.get(ResearchRunScopeRecord, run_id, populate_existing=True)
            if run_scope is not None:
                previous = json.loads(run_scope.input_receipts_json)
                if previous and previous != receipts:
                    raise ValueError("research_library_input_receipt_changed")
                run_scope.input_receipts_json = json.dumps(receipts, ensure_ascii=False)
                # Subsequent calibration reads require a clean physical read
                # snapshot inside this same already-open writer transaction.
                session.flush()
            request = ResearchSessionRequest.model_validate(
                {
                    "schema_version": "research-session-request.v1",
                    "request_id": f"research-request:{run_id}",
                    "run_id": run_id,
                    "event_id": event_id,
                    "trigger_snapshot_id": snapshot_id,
                    "domain_pack_ref": f"{pack.pack_id}.v{pack.version.split('.')[0]}",
                    "role_profile_ref": definition["research_policy"]["manager_ref"],
                    "execution_mode": self.execution_mode,
                    "research_mode": "historical_review"
                    if historical_review
                    else "current_decision",
                    "task_policy_ref": (
                        f"{task_policy.policy_id}.{task_policy.policy_version}:"
                        f"{task_policy.content_hash[:16]}"
                    ),
                    "context_manifest": context_manifest.model_dump(mode="json"),
                    "pit_cutoff_at": pit_cutoff_at,
                    "current_round": 1,
                    "evidence_refs": [item.evidence_id for item in input_evidence],
                    "input_evidence": [item.model_dump(mode="json") for item in input_evidence],
                    "evidence_requirements": [
                        item.model_dump(mode="json") for item in requirements
                    ],
                    "target_gaps": [],
                    "event_watch": event_watch,
                    "event_window_samples": [item.model_dump(mode="json") for item in samples],
                    "allowed_capabilities": list(allowed_capabilities),
                    "execution_budget": execution_budget.model_dump(mode="json"),
                    "deadline_at": deadline_at,
                    "output_schema_ref": (
                        perpetual_output_schema_for_admission(prepared_perpetual.value, as_of=now)
                        if prepared_perpetual is not None and not historical_review else
                        "research_signal.schema.yaml#/$defs/research_decision_synthesis_v1"
                        if (
                            self.decision_output_enabled
                            and spot_binding is not None
                            and not historical_review
                        )
                        else "agentic_research.schema.yaml#/$defs/research_session_result"
                    ),
                    "repair_instructions": None,
                }
            )
            first_freeze_guard["deadline_at"] = request.deadline_at
            scope = task_scope(request, source)
            compiled = compile_policy(
                ResearchPolicyResource.model_validate(definition["gate_policy_resource"]),
                ResearchPolicyResource.model_validate(definition["rubric_policy_resource"]),
                [
                    ResearchRoleRubricBinding.model_validate(item)
                    for item in definition["role_rubric_bindings"]
                ],
                scope=scope,
            )
            definition_hash = canonical_hash(definition)
            full_task_hash = canonical_hash(request.model_dump(mode="json"))
            pack_receipt = ResearchRequestPackReceiptV2.model_validate(
                {
                    "schema_version": "research-request-pack.v2",
                    "run_id": run_id,
                    "definition": definition,
                    "definition_hash": definition_hash,
                }
            )
            task_receipt = ResearchTaskReceiptV2.model_validate(
                {
                    "schema_version": "research-task-request.v2",
                    "run_id": run_id,
                    "request": request.model_dump(mode="json"),
                    "request_hash": full_task_hash,
                    "request_hash_kind": "complete_task_request.v1",
                    "factory_source": source.model_dump(mode="json"),
                    "scope": scope.model_dump(mode="json"),
                    "pack_definition_hash": definition_hash,
                    "compiled_policy_hash": compiled.compiled_hash,
                    "library_inputs": receipts,
                }
            )
            compiled_receipt = ResearchCompiledPolicyReceiptV1.model_validate(
                {
                    "schema_version": "research-compiled-policy-receipt.v1",
                    "run_id": run_id,
                    "event_id": event_id,
                    "full_task_hash": full_task_hash,
                    "pack_definition_hash": definition_hash,
                    "compiled": compiled.model_dump(mode="json"),
                }
            )
            from packages.kernel.decision_hub_kernel.application import research_probability_binding
            from packages.kernel.decision_hub_kernel.application.calibration import (
                resolve_probability_activation_in_session,
            )
            from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
                stable_compiled_policy_recipe_hash,
            )

            selection_from_activation = research_probability_binding.selection_from_activation

            activation = resolve_probability_activation_in_session(
                session,
                market=scope.market,
                instrument=scope.instrument,
                pack_definition_hash=definition_hash,
                compiled_policy_recipe_hash=stable_compiled_policy_recipe_hash(compiled),
                pit_cutoff_at=request.pit_cutoff_at,
            )
            probability_selection = (
                selection_from_activation(
                    activation,
                    run_id=run_id,
                    event_id=event_id,
                    task_request_hash=full_task_hash,
                    compiled_policy_hash=compiled.compiled_hash,
                    pack_definition_hash=definition_hash,
                    trigger_snapshot_id=snapshot_id,
                )
                if activation is not None
                else None
            )
            with graph_recipe_task_creation_scope(session, run_id):
                result = freeze_research_bundle_in_session(
                    session,
                    run_id,
                    pack_receipt,
                    task_receipt,
                    compiled_receipt,
                    now,
                    _probability_selection=probability_selection,
                )
                if prepared_perpetual is not None:
                    self.perpetual_binding_service.bind_task_in_session(session, result.request, prepared_perpetual)
                if prepared_experience is not None:
                    perpetual_forecast_scoring_inputs.freeze_experience_selection_in_session(
                        session, result.request, experience_inputs, recorded_at=now
                    )
                register_action_opportunities_in_session(
                    session,
                    request=result.request,
                    pack_definition_hash=definition_hash,
                    compiled_policy_recipe_hash=stable_compiled_policy_recipe_hash(compiled),
                    registered_at=now,
                )
                if spot_binding is None:
                    freeze_graph_recipe_selection_in_session(
                        session,
                        run_id=run_id,
                        recorded_at=now,
                    )
                else:
                    from packages.kernel.decision_hub_kernel.application import (
                        research_graph_recipe_v3,
                    )

                    freeze_graph_recipe_selection_v3_in_session = (
                        research_graph_recipe_v3.freeze_graph_recipe_selection_v3_in_session
                    )
                    from packages.kernel.decision_hub_kernel.application.research_run_scope import (
                        quality_context_for_spot_run_in_session,
                    )

                    freeze_graph_recipe_selection_v3_in_session(
                        session,
                        run_id=run_id,
                        recorded_at=now,
                        quality_context=quality_context_for_spot_run_in_session(
                            session, run_id
                        ).model_copy(
                            update={
                                "additional_required_context_refs": tuple(
                                    item.evidence_id for item in protected_inputs
                                )
                            }
                        ),
                    )
                if self.harness_first_freeze is not None:
                    assert prepared_harness is not None
                    self.harness_first_freeze.record_new_consumption_in_session(
                        session, prepared_harness, result
                    )
                if recipe_consumption_ref is not None:
                    assert spot_binding is not None
                    binding = {
                        "schema_version": "research-recipe-consumption-binding.v1",
                        "run_id": run_id,
                        "event_id": event_id,
                        "full_task_hash": full_task_hash,
                        "definition_hash": definition_hash,
                        "admission_hash": spot_binding.admission_proof_hash,
                        "effective_binding_hash": spot_binding.effective_binding_hash,
                        "consumption_ref": recipe_consumption_ref,
                    }
                    body = canonical_recipe(binding)
                    session.add(
                        RunRoleOutputRecord(
                            output_id="recipeconsume_" + canonical_hash(binding)[:40],
                            run_id=run_id,
                            role="research_recipe_consumption",
                            schema_version=binding["schema_version"],
                            payload_json=body,
                            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
                            created_at=now,
                        )
                    )
            return result.request

    def _build_legacy(self, event_id: str, run_id: str) -> ResearchSessionRequest:
        pack = self.validate_scope(event_id, run_id)
        original = ResearchTaskService(self.database).get(run_id)
        if original is not None:
            return original
        receipt = self.packs.freeze(run_id, self.loaded_pack)
        definition = receipt["definition"] if receipt else self.loaded_pack
        self._validate_execution_scope(definition)
        pack = DomainPackManifest.model_validate(definition["manifest"])
        fact_pack = CryptoMacroFactPack.from_manifest(definition["evidence_policy"])
        execution_mode = definition["execution_mode"]
        snapshot_id, _ = SnapshotService(self.database).freeze_for_run(run_id, event_id)
        rows = self.database.get_snapshot_evidence(snapshot_id)
        if not rows:
            raise ValueError("research_trigger_evidence_missing")
        input_evidence = [_input_evidence(row, max_chars=4000) for row in rows]
        library_candidates = _library_candidates(self.database, run_id)
        historical_review = any(
            marker in item.excerpt
            for item in input_evidence
            for marker in ("历史事件复盘", "历史复盘", "historical review")
        )
        event_watch = self.event_watches.get_watch_by_event_id(event_id)
        event_family = (
            event_watch.event_family
            if event_watch is not None
            else self.database.get_event_type(event_id)
        )
        requirements = list(fact_pack.contract_requirements_for_event(event_family))
        allowed_capabilities = tuple(
            capability for capability in definition["allowed_capabilities"]
        )
        # Search discovery and a fetch from an already approved locator are
        # independent routes. Retain the exact Pack declaration; the frozen
        # allowlist and Gateway still reject every disabled or unapproved call.
        if historical_review:
            requirements = [
                item
                for item in requirements
                if item.requirement_id
                not in {"expectation_pricing", "crypto_spot_confirmation", "derivatives_crowding"}
            ]
        now = research_context_cutoff(self.clock())
        deadline_at = now + timedelta(seconds=pack.execution_budget.total_deadline_seconds)
        # Replay must retain the historical information boundary. Using the
        # worker's deadline as PIT would make minute-level market evidence look
        # stale merely because the replay budget is longer than one minute.
        pit_cutoff_at = (
            max(item.received_at for item in input_evidence)
            if execution_mode == "replay"
            else deadline_at
        )
        event_window_samples = (
            list(self.event_watches.list_event_samples(event_id)) if event_watch is not None else []
        )
        execution_budget = pack.execution_budget
        if historical_review and execution_budget.per_model_step_timeout_seconds < 240:
            execution_budget = execution_budget.model_copy(
                update={"per_model_step_timeout_seconds": 240}
            )
        task_policy = build_task_policy(
            research_mode=("historical_review" if historical_review else "current_decision"),
            requirements=requirements,
            created_at=now,
            max_evidence_rounds=execution_budget.max_evidence_rounds,
        )
        input_evidence, context_manifest = select_context(
            input_evidence,
            library_candidates,
            requirements=requirements,
            policy=task_policy,
            prior_exclusions=_parent_library_exclusions(self.database, run_id),
            manifest_prefix=f"context:{run_id}",
            revision=1,
            created_at=now,
            allowed_capabilities=allowed_capabilities,
            eligibility_cutoff_at=(
                pit_cutoff_at if execution_mode == "replay" else min(now, pit_cutoff_at)
            ),
        )
        # Preserve the exact bounded text handed to the runtime separately from
        # acquired market evidence, so source context never bypasses coverage gates.
        library_by_id = {item.evidence_id: item for item in library_candidates}
        receipts = []
        for item in input_evidence:
            candidate = library_by_id.get(item.evidence_id)
            if candidate is None:
                continue
            receipts.append(
                {
                    "version_id": candidate.source_id.removeprefix("research-library:"),
                    "evidence_id": item.evidence_id,
                    "excerpt": item.excerpt,
                    "excerpt_hash": hashlib.sha256(item.excerpt.encode()).hexdigest(),
                    "original_file_hash": candidate.source_content_hash,
                    "locator_refs": list(
                        dict.fromkeys(re.findall(r"document://[^;\s\]]+", item.excerpt))
                    ),
                }
            )
        with self.database.session() as session:
            scope = session.get(ResearchRunScopeRecord, run_id)
            if scope is not None:
                previous = json.loads(scope.input_receipts_json)
                if previous and previous != receipts:
                    raise ValueError("research_library_input_receipt_changed")
                scope.input_receipts_json = json.dumps(receipts, ensure_ascii=False)
        return ResearchSessionRequest(
            schema_version="research-session-request.v1",
            request_id=f"research-request:{run_id}",
            run_id=run_id,
            event_id=event_id,
            trigger_snapshot_id=snapshot_id,
            domain_pack_ref=f"{pack.pack_id}.v{pack.version.split('.')[0]}",
            role_profile_ref=definition.get("research_policy", {}).get(
                "manager_ref", "crypto_macro.manager.v1"
            ),
            execution_mode=cast(Literal["live", "replay"], execution_mode),
            research_mode=("historical_review" if historical_review else "current_decision"),
            task_policy_ref=(
                f"{task_policy.policy_id}.{task_policy.policy_version}:"
                f"{task_policy.content_hash[:16]}"
            ),
            context_manifest=context_manifest,
            pit_cutoff_at=pit_cutoff_at,
            current_round=1,
            evidence_refs=[item.evidence_id for item in input_evidence],
            input_evidence=input_evidence,
            evidence_requirements=requirements,
            target_gaps=[],
            event_watch=event_watch,
            event_window_samples=event_window_samples,
            allowed_capabilities=list(allowed_capabilities),
            execution_budget=execution_budget,
            deadline_at=deadline_at,
            output_schema_ref="agentic_research.schema.yaml#/$defs/research_session_result",
            repair_instructions=None,
        )


def _parent_library_exclusions(database: Database, run_id: str) -> list[ContextExclusion]:
    with database.session() as session:
        return _parent_library_exclusions_in_session(session, run_id)


def _parent_library_exclusions_in_session(session: Session, run_id: str) -> list[ContextExclusion]:
    """Borrow the caller session; retain the original selection and consent checks."""
    run = session.get(RunRecord, run_id)
    if run is None or not run.parent_run_id:
        return []
    # A scheduled child has no scope of its own. Walk through those children
    # to the latest explicit selection; never inherit body text or consent.
    parent_id = run.parent_run_id
    seen = {run_id}
    parent = None
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = session.get(ResearchRunScopeRecord, parent_id)
        if parent is not None:
            break
        ancestor = session.get(RunRecord, parent_id)
        parent_id = ancestor.parent_run_id if ancestor else None
    if parent is None:
        return []
    current = session.get(ResearchRunScopeRecord, run_id)
    selected = set(json.loads(current.selected_version_ids_json)) if current else set()
    return [
        ContextExclusion(
            evidence_ref=f"library:{version}",
            reason="parent_library_not_selected_for_this_run",
        )
        for version in json.loads(parent.selected_version_ids_json)
        if version not in selected
    ]


class DurableResearchWorker:
    """Claim admitted Runs, execute the agentic graph, and project one ledger result."""

    def __init__(
        self,
        database: Database,
        runtime: ResearchHarnessRuntime,
        *,
        pack_root: Path,
        prepared_pack_root: Path | None = None,
        spot_prepared_pack_root: Path | None = None,
        spot_allowed_capabilities: Iterable[str] | None = None,
        context_inputs_provider: ContextInputsProvider | None = None,
        current_method_observer: Callable[[str], None] | None = None,
        component_consumer: MarketComponentConsumer | None = None,
        prepared_policy_refs: PreparedResearchPolicyRefs | None = None,
        harness_first_freeze: HarnessFirstFreezeService | None = None,
        frozen_run_harness: FrozenRunHarnessService | None = None,
        checkpoint_path: Path | None = None,
        worker_id: str = "research-worker",
        strategy_version: str = "research.v1",
        allowed_capabilities: Iterable[str] = ("replay.research",),
        execution_mode: Literal["live", "replay"] = "live",
        lease_seconds: int = 240,
        min_priority: int | None = None,
        terminal_projection_wait_seconds: float = 10.0,
        decision_output_enabled: bool = False,
        action_integration: ActionResearchIntegration | None = None,
        perpetual_binding_service: Any | None = None,
        opinion_context: PublicOpinionResearchContext | None = None,
        heavy_work_admission=None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if perpetual_binding_service is not None and prepared_pack_root is None:
            raise ValueError("perpetual_prepared_factory_required")
        self.perpetual_binding_service = perpetual_binding_service
        self.database = database
        self.runs = RunService(database, clock=clock)
        self.runtime = runtime
        self.worker_id = worker_id
        self.strategy_version = strategy_version
        self.frozen_run_harness = frozen_run_harness
        self.current_method_observer = current_method_observer
        self.action_integration = action_integration or ActionResearchIntegration(
            database, clock=clock
        )
        self.lease_seconds = lease_seconds
        self.min_priority = min_priority
        self.heavy_work_admission = heavy_work_admission
        if not 0 < terminal_projection_wait_seconds <= 15:
            raise ValueError("research_terminal_projection_wait_invalid")
        self.terminal_projection_wait_seconds = terminal_projection_wait_seconds
        self._pending_terminal_failure: _PendingTerminalFailure | None = None
        self.factory = ResearchRequestFactory(
            database,
            pack_root=pack_root,
            allowed_capabilities=allowed_capabilities,
            execution_mode=execution_mode,
            decision_output_enabled=decision_output_enabled,
            opinion_context=opinion_context,
            clock=clock,
        )
        if (prepared_pack_root is None) != (prepared_policy_refs is None):
            raise ValueError("research_prepared_composition_incomplete")
        if harness_first_freeze is not None and prepared_pack_root is None:
            raise ValueError("harness_prepared_factory_required")
        self.prepared_factory = (
            ResearchRequestFactory(
                database,
                pack_root=prepared_pack_root,
                prepared_policy_refs=prepared_policy_refs,
                harness_first_freeze=harness_first_freeze,
                perpetual_binding_service=perpetual_binding_service,
                allowed_capabilities=allowed_capabilities,
                execution_mode=execution_mode,
                decision_output_enabled=decision_output_enabled,
                opinion_context=opinion_context,
                clock=clock,
            )
            if prepared_pack_root is not None
            else None
        )
        self.spot_prepared_factory = (
            ResearchRequestFactory(
                database,
                pack_root=spot_prepared_pack_root,
                prepared_policy_refs=prepared_policy_refs,
                allowed_capabilities=tuple(spot_allowed_capabilities)
                if spot_allowed_capabilities is not None
                else allowed_capabilities,
                context_inputs_provider=context_inputs_provider,
                component_consumer=component_consumer,
                execution_mode=execution_mode,
                decision_output_enabled=decision_output_enabled,
                opinion_context=opinion_context,
                clock=clock,
            )
            if spot_prepared_pack_root is not None
            else None
        )
        self.executor = LangGraphResearchExecutor(
            database,
            runtime,
            checkpoint_path=checkpoint_path,
            clock=clock,
            expected_lease_owner=self.worker_id,
            heavy_work_admission=heavy_work_admission,
        )
        self.commit = CommitDecisionService(database, clock=clock)
        self.observability = ResearchObservabilityService(database, clock=clock)
        self.reviews = ResearchReviewService(database, clock=clock)
        self.review_schedule = ReviewScheduleService(database, clock=clock)
        self.value_evaluations = ResearchValueEvaluationService(
            database, pack_root=pack_root, clock=clock
        )
        self.clock = clock

    async def tick(self) -> ResearchWorkerReport | None:
        # A known execution failure must be projected before this process can
        # reclaim/checkpoint-resume any Run. This carries an error, not permission:
        # every attempted terminal CAS repeats current ownership/cancel checks.
        if self._pending_terminal_failure is not None:
            return await self._flush_terminal_failure()
        async with self._heavy_work_scope():
            # Scheduling can authenticate a long report history. Keep its owned
            # sessions off the event loop, but drain it before releasing the
            # shared admission or starting a claim after cancellation.
            scheduled = await HeavyWorkAdmission().run_thread(self._plan_due_reviews)
            claim = scheduled if scheduled is not None else self._claim_after_scheduler(
                skip_scheduler=True,
            )
        if claim is None or isinstance(claim, ResearchWorkerReport):
            if claim is None and self.min_priority is None:
                await self._heavy_thread(self._reconcile_actions_safely)
            return claim
        event_id, run_id = claim.event_id, claim.run_id
        owner_cancelled = asyncio.Event()
        operation = asyncio.create_task(
            self._execute_claim(event_id, run_id, recovered=claim.recovered),
            name=f"research-operation:{run_id}",
        )
        watcher = asyncio.create_task(
            self._watch_owner_cancellation(run_id, operation, owner_cancelled),
            name=f"research-cancel-watch:{run_id}",
        )
        try:
            return await operation
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if owner_cancelled.is_set() and task is not None and not task.cancelling():
                current = self.database.get_run_record(run_id)
                return ResearchWorkerReport(
                    status="cancelled",
                    run_id=run_id,
                    error_code=current.error_code if current else "cancelled_by_owner",
                )
            # External shutdown must remain cancellation control flow. It must
            # not be turned into a provider failure or an owner cancellation.
            raise
        finally:
            watcher.cancel()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(watcher, operation, return_exceptions=True)

    @asynccontextmanager
    async def _heavy_work_scope(self):
        if self.heavy_work_admission is None:
            yield
        else:
            async with self.heavy_work_admission.admitted():
                yield

    async def _heavy_thread(self, function, *args):
        # Never detach a live writer on cancellation. The enclosing claim
        # keeps renewing its lease until this admitted thread has exited.
        admission = self.heavy_work_admission or HeavyWorkAdmission()
        return await admission.run_thread(function, *args)

    def _plan_due_reviews(self) -> ResearchWorkerReport | None:
        # Only the general lane owns review scheduling. Every Session is
        # created and closed within this callable's native thread.
        if self.min_priority is not None:
            return None
        try:
            self.review_schedule.plan_due(now=self.clock())
        except OperationalError as error:
            if not is_sqlite_initial_begin_busy(error):
                raise
            return ResearchWorkerReport(
                status="maintenance_pending",
                error_code="research_scheduler_storage_busy",
                retryable=True,
            )
        return None

    def _claim_after_scheduler(self, *, skip_scheduler: bool = False):
        # Claim stays in the original synchronous control-flow segment. Moving
        # this CAS into a cancelled run_thread could discard a committed claim
        # before its execution/lease-renewal owner is installed.
        if not skip_scheduler:
            scheduled = self._plan_due_reviews()
            if scheduled is not None:
                return scheduled
        try:
            claim = self.runs.claim_next(
                strategy_version=self.strategy_version,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                min_priority=self.min_priority,
            )
        except OperationalError as error:
            # RunService owns and rolls back this entire failed CAS transaction.
            # Retry its fresh eligibility/lease checks on the next tick only if
            # the failed statement was that known UPDATE, before any Model call.
            # A COMMIT or an unrelated write has no such recovery authority here.
            from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import (
                is_sqlite_storage_busy,
            )

            if not (
                is_sqlite_storage_busy(error)
                and isinstance(error.statement, str)
                and re.match(r'^UPDATE\s+(?:runs|"runs")\s+SET\b', error.statement.strip(), re.I)
            ):
                raise
            return ResearchWorkerReport(
                status="maintenance_pending",
                error_code="research_claim_storage_busy",
                retryable=True,
            )
        if claim is None and self.frozen_run_harness is not None and self.min_priority is None:
            claim = self.frozen_run_harness.claim_next(
                worker_id=self.worker_id, lease_seconds=self.lease_seconds
            )
        return claim

    async def _watch_owner_cancellation(
        self,
        run_id: str,
        operation: asyncio.Task[ResearchWorkerReport],
        owner_cancelled: asyncio.Event,
    ) -> None:
        while not operation.done():
            try:
                current = await asyncio.to_thread(self.database.get_run_record, run_id)
            except Exception:
                # A transient DB read cannot invent cancellation authority.
                current = None
            if current is not None and current.status == RunStatus.cancelled.value:
                owner_cancelled.set()
                operation.cancel()
                return
            # Losing a lease is fenced at publication/failure. Cancelling a
            # shared deterministic DSH session could kill its replacement owner.
            await asyncio.sleep(1.0)

    def _factory_for_run(
        self, run_id: str
    ) -> tuple[ResearchRequestFactory | FrozenRunHarnessService, bool]:
        """Choose from original persisted schema, never from missing Method alone."""
        if self.frozen_run_harness is not None and self.frozen_run_harness.contains(run_id):
            return self.frozen_run_harness, True
        if self.prepared_factory is None:
            return self.factory, False
        from packages.kernel.decision_hub_kernel.application.research_observability import (
            requires_tool_admission_proof,
        )

        with self.database.session() as session:
            from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
                read_perpetual_admission_identity_in_session,
            )
            if read_perpetual_admission_identity_in_session(session, run_id) is not None:
                if self.perpetual_binding_service is None:
                    raise ValueError("perpetual_worker_binding_service_required")
                return self.prepared_factory, True
            if read_run_spot_admission_in_session(session, run_id) is not None:
                if self.spot_prepared_factory is None:
                    raise ValueError("spot_prepared_factory_not_configured")
                return self.spot_prepared_factory, True
            rows = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=run_id)
                .populate_existing()
                .all()
            )
            by_role = {row.role: row for row in rows}
            method = by_role.get("research_execution_method")
            if method is not None:
                if method.schema_version in {
                    "research-execution-method.v1",
                    "research-execution-method.v2",
                }:
                    return self.factory, False
                if method.schema_version == "research-execution-method.v3":
                    return self.prepared_factory, True
                raise ValueError("research_worker_method_schema_unknown")
            if any(by_role.get(role) is not None for role in BUNDLE_ROLES):
                from packages.kernel.decision_hub_kernel.application.research_binding import (
                    FrozenResearchBundle,
                    LegacyResearchBundle,
                    read_research_bundle_in_session,
                )

                bundle = read_research_bundle_in_session(session, run_id)
                if isinstance(bundle, FrozenResearchBundle):
                    return self.prepared_factory, True
                if isinstance(bundle, LegacyResearchBundle):
                    return self.factory, False
                raise ValueError("research_worker_original_bundle_invalid")
            link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
            prompt = session.query(DshSessionPromptRecord).filter_by(run_id=run_id).first()
            if (
                link is not None
                or prompt is not None
                or requires_tool_admission_proof(session, run_id)
                or any(row.role.startswith("context_select.") for row in rows)
            ):
                raise ValueError("research_worker_partial_history_not_fresh")
            return self.prepared_factory, True

    def _prepare_request_policy(
        self, factory: ResearchRequestFactory | FrozenRunHarnessService, event_id: str, run_id: str,
    ) -> ResearchSessionRequest:
        # Every writer/reader invoked here owns its Session in this same native
        # thread. This is the original preparation, not a cached authorization.
        request = factory.build(event_id, run_id)
        task_policy = build_task_policy(
            research_mode=request.research_mode or "current_decision",
            requirements=request.evidence_requirements,
            created_at=self.clock().astimezone(UTC),
            max_evidence_rounds=request.execution_budget.max_evidence_rounds,
            context_inputs=request.input_evidence,
        )
        policy_output_id = self.database.save_role_output(
            run_id, "task_policy", task_policy.model_dump(mode="json"), "research-task-policy.v1",
        )
        self.database.record_run_event(
            run_id, self._next_event_sequence(run_id), "research.task_policy.created",
            {"output_id": policy_output_id, "policy_ref": request.task_policy_ref,
             "content_hash": task_policy.content_hash},
        )
        return request

    async def _execute_claim(
        self, event_id: str, run_id: str, *, recovered: bool
    ) -> ResearchWorkerReport:
        phase = "prepare"
        lease_task = asyncio.create_task(
            self._renew_lease_while_running(run_id), name=f"research-lease:{run_id}"
        )
        try:
            phase = "execute"
            async with self._heavy_work_scope():
                # Preserve this admission until the real native preparation
                # exits, including repeated cancellation. Sessions never cross
                # awaits; service heartbeat and lease renewal remain schedulable.
                preparation = HeavyWorkAdmission()
                factory, selected = await preparation.run_thread(self._factory_for_run, run_id)
                resume_checkpoint = recovered and await self.executor.has_checkpoint(run_id)
                if resume_checkpoint:
                    await preparation.run_thread(factory.validate_scope, event_id, run_id)
                    request = None
                else:
                    request = await preparation.run_thread(
                        self._prepare_request_policy, factory, event_id, run_id,
                    )
            # DSH/model awaits and their lease/cancellation control never hold admission.
            if resume_checkpoint:
                state = (
                    await self.executor.resume(run_id, require_context_selection=selected)
                    if selected
                    else await self.executor.resume(run_id)
                )
            else:
                state = (
                    await self.executor.execute(request, require_context_selection=selected)
                    if selected
                    else await self.executor.execute(request)
                )
            final = ResearchSessionResult.model_validate(state["final_result"])
            final_locator = state.get("graph_final_projection_ref")
            # Only the original executor checkpoint locator is authority.
            # Never read latest outputs or fill an expected owner from a Run.
            final_ref = parse_graph_final_ref(final_locator) if final_locator is not None else None
            if self.frozen_run_harness is not None and self.frozen_run_harness.contains(run_id):
                phase = "commit"
                self.frozen_run_harness.complete(
                    run_id, event_id, final, final_ref, worker_id=self.worker_id
                )
                return ResearchWorkerReport(status="comparison_completed", run_id=run_id)
            # The original graph/Method transactions have ended before this
            # derived registry hook runs. The observer authenticates the frozen
            # originals itself; neither its success nor its failure is research
            # qualification, and a failure must not re-execute the model.
            if self.current_method_observer is not None:
                await self._heavy_thread(self._observe_current_method_safely, run_id)
            def complete_terminal() -> ResearchWorkerReport:
                # Sessions are opened and closed by these synchronous services
                # entirely in the same worker thread; no Session crosses awaits.
                nonlocal phase, final, state
                # V2 financial review remains the exact authenticated native sidecar.
                # The committer authenticates its original Graph locator; a V1
                # review cannot replace or qualify this financial boundary.
                if (final_ref is None or final_ref.row.schema_version
                        != "research-perpetual-graph-final.v1"):
                    phase = "review"
                    try:
                        review = (
                            self.reviews.review(run_id, final)
                            if final_ref is None
                            else self.reviews.review(
                                run_id,
                                final,
                                graph_final_ref=final_ref,
                                expected_lease_owner=self.worker_id,
                            )
                        )
                    except ValueError as exc:
                        # A native DSH review is advisory input. If its receipt hash or
                        # target is invalid, keep the run fail-closed and replace that
                        # receipt with the deterministic bounded review generated from
                        # the validated result. An invalid advisory receipt must not
                        # discard already-attested Evidence and horizons.
                        error_code = str(exc)
                        if not error_code.startswith("research_native_review_"):
                            raise
                        self.database.record_run_event(
                            run_id,
                            self._next_event_sequence(run_id),
                            "research.review.native_invalid",
                            {"error_code": error_code},
                        )
                        bounded = final.model_copy(update={"reviews": []})
                        review = (
                            self.reviews.review(run_id, bounded)
                            if final_ref is None
                            else self.reviews.review(
                                run_id,
                                bounded,
                                graph_final_ref=final_ref,
                                expected_lease_owner=self.worker_id,
                            )
                        )
                    final = final.model_copy(update={"reviews": [review]})
                    state = {**state, "final_result": final.model_dump(mode="json")}
                    self.database.record_run_event(
                        run_id,
                        self._next_event_sequence(run_id),
                        "research.review.created",
                        {
                            "review_id": review.review_id,
                            "result_hash": review.result_hash,
                            "resolution": review.resolution,
                        },
                    )
                phase = "commit"
                if final_ref is None:
                    artifact_id, gate = self.commit.commit_research_result(
                        run_id,
                        event_id,
                        final,
                        expected_lease_owner=self.worker_id,
                    )
                else:
                    artifact_id, gate = self.commit.commit_research_result(
                        run_id,
                        event_id,
                        final,
                        expected_lease_owner=self.worker_id,
                        graph_final_ref=final_ref,
                    )
                phase = "record"
                self._record_completed(run_id, state, gate.status.value, artifact_id)
                self._evaluate_terminal_safely(run_id)
                self._publish_actions_safely(run_id, event_id, artifact_id, final_ref)
                return ResearchWorkerReport(
                    status=gate.status.value,
                    run_id=run_id,
                    artifact_id=artifact_id,
                )

            return await self._heavy_thread(complete_terminal)
        except Exception as exc:
            error_code = getattr(exc, "error_code", "research_worker_failed")
            pending_reason: str | None = None
            pending_candidate = _is_preparation_pending(exc, phase)
            if pending_candidate:
                outcome, pending_reason = await self._retain_preparation_pending(
                    event_id, run_id, exc
                )
                if outcome == "pending":
                    return ResearchWorkerReport(
                        status="preparation_pending", run_id=run_id, error_code=error_code
                    )
                if outcome in {"cancelled", "lease_lost"}:
                    return ResearchWorkerReport(
                        status=outcome,
                        run_id=run_id,
                        error_code=pending_reason,
                    )
                if outcome == "storage_unavailable":
                    return ResearchWorkerReport(
                        status="preparation_storage_unavailable",
                        run_id=run_id,
                        error_code=pending_reason,
                    )
            self._pending_terminal_failure = _PendingTerminalFailure(
                event_id,
                run_id,
                exc,
                error_code,
                phase,
                pending_candidate,
                pending_reason,
            )
            return await self._flush_terminal_failure()
        finally:
            # Task cancellation is BaseException, so successful/Exception-only
            # cleanup would leave a renewal task alive in the same event loop.
            await self._stop_lease_renewal(lease_task)

    def _publish_actions_safely(
        self,
        run_id: str,
        event_id: str,
        artifact_id: str,
        reference: GraphFinalProjectionRef | None,
    ) -> None:
        if reference is None:
            return  # A legacy result has no native final identity to publish.
        try:
            self.action_integration.publish_committed(
                run_id=run_id, event_id=event_id, artifact_id=artifact_id, reference=reference
            )
        except Exception as error:
            code = getattr(error, "error_code", None) or type(error).__name__
            try:
                self.database.record_run_event(
                    run_id,
                    self._next_event_sequence(run_id),
                    "action.publication.hook_failed",
                    {
                        "artifact_id": artifact_id,
                        "error_code": code,
                        "recovery": "same_registered_opportunity_and_publish_key",
                    },
                )
            except Exception:
                logging.getLogger(__name__).error(
                    "action_hook_audit_storage_unavailable run=%s artifact=%s error=%s",
                    run_id,
                    artifact_id,
                    code,
                )

    def _reconcile_actions_safely(self) -> None:
        try:
            self.action_integration.reconcile_missed_hooks(max_candidates=3)
        except Exception as error:
            # This independent recovery cannot change a committed Report or
            # prevent the next ordinary research claim when its store is busy.
            logging.getLogger(__name__).error(
                "action_hook_reconcile_storage_unavailable error=%s", type(error).__name__
            )

    async def _flush_terminal_failure(self) -> ResearchWorkerReport:
        pending = self._pending_terminal_failure
        assert pending is not None
        run_id, event_id = pending.run_id, pending.event_id
        error_code = pending.error_code
        deadline = asyncio.get_running_loop().time() + self.terminal_projection_wait_seconds
        while True:
            current = self.database.get_run_record(run_id)
            if current is not None and current.status == RunStatus.cancelled.value:
                self._pending_terminal_failure = None
                return ResearchWorkerReport(
                    status="cancelled",
                    run_id=run_id,
                    error_code=current.error_code or "cancelled_by_owner",
                )
            try:
                failure_saved = (
                    await asyncio.to_thread(
                        self._fail_unrecoverable_preparation,
                        event_id,
                        run_id,
                        error_code,
                    )
                    if pending.pending_candidate
                    else await asyncio.to_thread(
                        self.runs.fail_if_owned,
                        run_id,
                        self.worker_id,
                        error_code=error_code,
                        require_current_lease=True,
                    )
                )
            except OperationalError as storage_error:
                # Only this owned, rolled-back initial BEGIN or terminal UPDATE
                # is known not to have saved a failure. Never retry a COMMIT or
                # an unrelated callback whose outcome is unconfirmed.
                terminal_update = (
                    is_sqlite_storage_busy(storage_error)
                    and isinstance(storage_error.statement, str)
                    and re.match(
                        r'^UPDATE\s+(?:runs|"runs")\s+SET\b',
                        storage_error.statement.strip(),
                        re.I,
                    )
                )
                if not (is_sqlite_initial_begin_busy(storage_error) or terminal_update):
                    self._pending_terminal_failure = None
                    raise
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    await asyncio.sleep(min(0.1, remaining))
                    continue
                report = ResearchWorkerReport(
                    status="terminal_storage_pending",
                    run_id=run_id,
                    error_code=error_code,
                    retryable=True,
                    recorded_in_database=False,
                )
                print(
                    json.dumps(
                        {
                            **report.model_dump(),
                            "phase": "terminal_projection",
                            "storage_error_code": "research_terminal_storage_busy",
                            "instance_id": self.worker_id,
                            "observed_at": datetime.now(UTC).isoformat(),
                        }
                    ),
                    flush=True,
                )
                return report
            except Exception:
                # An uncertain commit/contract failure has no replay authority.
                self._pending_terminal_failure = None
                raise
            self._pending_terminal_failure = None
            if not failure_saved:
                # Ownership may change between the preceding read and this
                # conditional update. Never project an old worker's failure
                # over a new owner or an already committed/cancelled outcome.
                current = self.database.get_run_record(run_id)
                cancelled = current is not None and current.status == RunStatus.cancelled.value
                return ResearchWorkerReport(
                    status="cancelled" if cancelled else "lease_lost",
                    run_id=run_id,
                    error_code=(
                        current.error_code
                        if cancelled and current
                        else "run_publication_lease_lost"
                    ),
                )
            try:
                await asyncio.to_thread(self._record_terminal_failure, pending)
            except OperationalError as storage_error:
                if not is_sqlite_storage_busy(storage_error):
                    raise
                # The terminal CAS was already confirmed. Audit storage failure
                # cannot erase it or authorize another research execution.
                print(
                    json.dumps(
                        {
                            "status": "terminal_audit_pending",
                            "run_id": run_id,
                            "error_code": error_code,
                            "recorded_in_database": False,
                            "terminal_recorded_in_database": True,
                            "storage_error_code": "research_terminal_audit_storage_busy",
                            "observed_at": datetime.now(UTC).isoformat(),
                        }
                    ),
                    flush=True,
                )
            self._evaluate_terminal_safely(run_id)
            return ResearchWorkerReport(
                status="failed",
                run_id=run_id,
                error_code=error_code,
                recorded_in_database=True,
            )

    def _record_terminal_failure(self, pending: _PendingTerminalFailure) -> None:
        run_id, exc, error_code = pending.run_id, pending.error, pending.error_code
        self.observability.append_trace(
            ResearchTraceEvent.model_validate(
                {
                    "schema_version": "research-trace-event.v1",
                    "run_id": run_id,
                    "research_session_id": f"product:{run_id}",
                    "sequence_no": 0,
                    "event_type": "session_stopped",
                    "occurred_at": self.clock(),
                    "stage": "done",
                    "summary": "Research execution stopped without a committable result.",
                    "reference_type": "run",
                    "reference_id": run_id,
                    "status": "failed",
                    "error_code": error_code,
                    "error": _error_provenance(exc),
                }
            )
        )
        self.database.record_run_event(
            run_id,
            self._next_event_sequence(run_id),
            "research.agentic.failed",
            {
                "error_code": error_code,
                "phase": pending.phase,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:2000],
                **(
                    {"pending_rejected_reason": pending.pending_reason}
                    if pending.pending_reason
                    else {}
                ),
            },
        )

    async def _retain_preparation_pending(
        self, event_id: str, run_id: str, error: BaseException
    ) -> tuple[str, str | None]:
        """Keep only the original, recoverable prepare wait unconfirmed.

        Read the official checkpoint without changing its channels. Native
        authentication and permission are repeated inside the actual write
        transaction; the checkpoint read itself grants no write permission.
        """
        if self.executor.checkpoint_path is None:
            return "ineligible", "research_preparation_checkpoint_unavailable"
        try:
            async with CheckpointStore(self.executor.checkpoint_path).open() as saver:
                saved = await saver.aget_tuple({"configurable": {"thread_id": run_id}})
                if saved is None:
                    return "ineligible", "research_preparation_checkpoint_missing"
                checkpoint_id = _preparation_checkpoint_id(saved.checkpoint["id"])
                if saved.config.get("configurable", {}).get("thread_id") != run_id:
                    return "ineligible", "research_preparation_checkpoint_identity_invalid"
                state = cast(
                    AgenticResearchState,
                    {
                        key: value
                        for key, value in saved.checkpoint["channel_values"].items()
                        if key in AgenticResearchState.__annotations__
                    },
                )
            # The Session is created and closed in this thread. Await real
            # completion: neither a timeout nor close kills a SQLite writer.
            return await asyncio.to_thread(
                self._record_preparation_pending,
                event_id,
                run_id,
                error,
                checkpoint_id,
                state,
            )
        except _PreparationPendingDenied as denied:
            return denied.outcome, str(denied)
        except OperationalError as unavailable:
            original = unavailable.orig
            if isinstance(original, sqlite3.OperationalError) and getattr(
                original, "sqlite_errorcode", 0
            ) & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                # The original prepare error and this storage contention are
                # separate facts. No pending receipt was confirmed or written.
                return "storage_unavailable", "research_preparation_storage_busy"
            raise
        except (ValueError, KeyError, TypeError, OSError) as denied:
            return "ineligible", str(denied)[:256]

    def _run_strategy(self, run_id: str) -> str:
        if self.frozen_run_harness is not None and self.frozen_run_harness.contains(run_id):
            return "evaluation.harness.v1"
        return self.strategy_version

    def _record_preparation_pending(
        self,
        event_id: str,
        run_id: str,
        error: BaseException,
        checkpoint_id: str,
        state: AgenticResearchState,
    ) -> tuple[str, str | None]:
        with self.database.session() as session:
            if session.get_bind().dialect.name != "sqlite":
                raise ValueError("research_preparation_pending_sqlite_required")
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = session.get(RunRecord, run_id, populate_existing=True)
            _pending_permission(run, event_id, self._run_strategy(run_id), self.worker_id, utcnow())
            native, original_state = prepare_graph_state_in_session(
                session, state, expected_lease_owner=self.worker_id
            )
            if not native:
                raise ValueError("research_preparation_pending_native_binding_missing")
            if _is_skill_preparation_observation(error):
                from packages.runtime_adapters.dsh_runtime.research_runtime_binding import read_runtime_task_anchor
                _require_skill_preparation_recovery_anchor(
                    error, read_runtime_task_anchor(session, run_id)
                )
            bundle = read_research_bundle_in_session(session, run_id)
            if not isinstance(bundle, FrozenResearchBundle):
                raise ValueError("research_preparation_pending_native_bundle_missing")
            task_row = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "research_task_request",
                )
                .execution_options(populate_existing=True)
            )
            if (
                task_row is None
                or task_row.payload_json != canonical_recipe(bundle.task.model_dump(mode="json"))
                or hashlib.sha256(task_row.payload_json.encode()).hexdigest()
                != task_row.payload_hash
            ):
                raise ValueError("research_preparation_pending_task_row_changed")
            deadline = bundle.task.scope.deadline_at
            # A late prepare may already have committed. Authenticate its
            # entire contiguous history; never adopt a partial/unknown Method.
            has_preparation = (
                session.get(DshSessionLinkRecord, run_id, populate_existing=True) is not None
                or session.scalar(
                    select(DshSessionPromptRecord.run_id).where(
                        DshSessionPromptRecord.run_id == run_id
                    )
                )
                is not None
                or session.scalar(
                    select(RunRoleOutputRecord.output_id).where(
                        RunRoleOutputRecord.run_id == run_id,
                        (RunRoleOutputRecord.role == "research_execution_method")
                        | RunRoleOutputRecord.role.like("dsh_execution_request:%")
                        | RunRoleOutputRecord.role.like("dsh_prompt_integrity:%"),
                    )
                )
                is not None
            )
            preparation: dict[str, object] | None = None
            if has_preparation:
                prepared = read_prepared_execution_in_session(session, run_id)
                deadline = min(deadline, prepared.method.definition.deadline_at)
                preparation = {
                    "method_hash": prepared.method.method_hash,
                    "method_recorded_at": prepared.method.recorded_at.isoformat(),
                    "public_request_hash": prepared.method.request_hash,
                    "public_request_hash_kind": prepared.method.request_hash_kind,
                    "generation": prepared.execution.generation,
                    "source_round": prepared.execution.request.current_round,
                    "turn_kind": prepared.execution.turn_kind,
                    "actual_full_request_hash": prepared.execution.request_hash,
                    "actual_full_request_hash_kind": prepared.execution.request_hash_kind,
                    "actual_reduced_request_hash": prepared.execution.execution_request_hash,
                    "actual_reduced_request_hash_kind": (
                        prepared.execution.execution_request_hash_kind
                    ),
                }
            now = utcnow()
            if now >= deadline:
                raise ValueError("research_preparation_pending_deadline_elapsed")
            assert run is not None
            _pending_permission(run, event_id, self._run_strategy(run_id), self.worker_id, now)
            task_recorded_at = as_utc(cast(datetime | None, task_row.created_at))
            lease_expires_at = as_utc(run.lease_expires_at)
            if task_recorded_at is None or lease_expires_at is None:
                raise ValueError("research_preparation_pending_original_time_missing")
            sequence = (
                session.scalar(
                    select(func.max(RunEventRecord.sequence_no)).where(
                        RunEventRecord.run_id == run_id
                    )
                )
                or 0
            ) + 1
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence_no=sequence,
                    event_type="research.execution.preparation_pending",
                    occurred_at=now,
                    payload_json=json.dumps(
                        {
                            "checkpoint_id": checkpoint_id,
                            "worker_id": self.worker_id,
                            "error": _error_provenance(error).model_dump(mode="json"),
                            "provider_id": getattr(error, "provider_id", None),
                            "message": str(error),
                            "deadline_at": deadline.isoformat(),
                            "phase": "execute",
                            "diagnostic": _pending_diagnostic(error),
                            "task": {
                                "output_id": task_row.output_id,
                                "role": task_row.role,
                                "schema_version": task_row.schema_version,
                                "payload_hash": task_row.payload_hash,
                                "payload_hash_kind": "sha256_stored_utf8_payload.v1",
                                "recorded_at": task_recorded_at.isoformat(),
                                "request_hash": bundle.task.request_hash,
                                "stored_request_hash_kind": bundle.task.request_hash_kind,
                                "request_recipe": "canonical_research_session_request.v1",
                                "scope_hash": bundle.task.scope.scope_hash,
                            },
                            "original_checkpoint": {
                                "run_id": run_id,
                                "logical_round": original_state["current_round"],
                                "graph_round_admission_ref": (
                                    original_state.get("graph_round_admission_ref")
                                ),
                                "graph_final_projection_ref": (
                                    original_state.get("graph_final_projection_ref")
                                ),
                            },
                            "original_lease": {
                                "owner": run.lease_owner,
                                "expires_at": lease_expires_at.isoformat(),
                                "strategy_version": run.strategy_version,
                                "event_id": run.event_id,
                            },
                            "preparation": preparation,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )
            self.observability.append_trace_in_session(
                session,
                ResearchTraceEvent.model_validate(
                    {
                        "schema_version": "research-trace-event.v1",
                        "run_id": run_id,
                        "research_session_id": f"product:{run_id}",
                        "sequence_no": 0,
                        "event_type": "session_stopped",
                        "occurred_at": now,
                        "stage": "planning",
                        "status": "running",
                        "summary": (
                            "执行准备尚未确认，原任务与材料保留；等待原租约到期后核对检查点恢复。"
                        ),
                        "reference_type": "run",
                        "reference_id": run_id,
                        "error_code": getattr(
                            error, "error_code", "research_method_binding_failed"
                        ),
                        "error": _error_provenance(error),
                    }
                ),
            )
            session.flush()
            # Recheck after actual event/trace SQL flush. Do not change the Run,
            # its lease, original owner identities, costs or terminal fields.
            final_now = utcnow()
            if final_now >= deadline:
                raise ValueError("research_preparation_pending_deadline_elapsed")
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.event_id == event_id,
                    RunRecord.strategy_version == self._run_strategy(run_id),
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_owner == self.worker_id,
                    RunRecord.lease_expires_at > final_now,
                    RunRecord.artifact_id.is_(None),
                    RunRecord.finished_at.is_(None),
                    RunRecord.error_code.is_(None),
                )
                .values(updated_at=run.updated_at)
                .execution_options(synchronize_session=False)
            )
            if int(getattr(changed, "rowcount", 0)) != 1:
                fresh = session.get(RunRecord, run_id, populate_existing=True)
                _pending_permission(
                    fresh, event_id, self._run_strategy(run_id), self.worker_id, final_now
                )
                raise _PreparationPendingDenied("lease_lost", "run_publication_lease_lost")
        return "pending", None

    def _fail_unrecoverable_preparation(self, event_id: str, run_id: str, error_code: str) -> bool:
        # A permanent checkpoint/proof rejection follows the ordinary failure
        # outcome only while this exact worker still has a live lease. The
        # generic legacy fail_if_owned does not fence an already expired lease.
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            now = utcnow()
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.event_id == event_id,
                    RunRecord.strategy_version == self._run_strategy(run_id),
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_owner == self.worker_id,
                    RunRecord.lease_expires_at > now,
                    RunRecord.artifact_id.is_(None),
                    RunRecord.finished_at.is_(None),
                    RunRecord.error_code.is_(None),
                )
                .values(
                    status=RunStatus.failed.value,
                    error_code=error_code,
                    updated_at=now,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            return int(getattr(changed, "rowcount", 0)) == 1

    async def _renew_lease_while_running(self, run_id: str) -> None:
        """Keep a long DSH session owned by its original worker.

        DSH can legitimately spend several minutes in subagents and model
        steps.  The old fixed lease expired during that work, allowing the
        second research lane to reclaim the same Run and append duplicate
        trace events.  Renew the durable Run lease independently of the
        service heartbeat so only one worker can process a live session.
        """

        interval = max(1.0, min(self.lease_seconds / 3, 30.0))
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await asyncio.to_thread(
                    self.runs.renew_lease,
                    run_id,
                    self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                # The terminal worker path remains authoritative. A transient
                # renewal failure must not cancel an otherwise valid DSH run.
                continue
            if not renewed:
                return

    @staticmethod
    async def _stop_lease_renewal(task: asyncio.Task[None]) -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _observe_current_method_safely(self, run_id: str) -> bool:
        observer = self.current_method_observer
        if observer is None:
            return True
        try:
            observer(run_id)
            return True
        except Exception as exc:
            try:
                self.database.record_run_event(
                    run_id,
                    self._next_event_sequence(run_id),
                    "research.current_method_observer.failed",
                    {
                        "error_code": "research_current_method_observer_failed",
                        "cause_code": str(getattr(exc, "error_code", type(exc).__name__)),
                        "retryable": True,
                    },
                )
            except Exception:
                # Storage diagnostics are also derived. Keep a minimal local
                # receipt without disclosing arbitrary exception/payload text.
                print("research_current_method_observer_storage_receipt_unavailable", flush=True)
            return False

    def _evaluate_terminal_safely(self, run_id: str) -> bool:
        """Keep derived evaluation failures outside the committed Run outcome.

        Research value evaluation is an append-only projection. It may be retried
        independently, but it must never turn an already committed Artifact or a
        correctly classified runtime failure into a different business outcome.
        """

        try:
            self.value_evaluations.evaluate_current(run_id)
            return True
        except Exception as exc:
            self.database.record_run_event(
                run_id,
                self._next_event_sequence(run_id),
                "research.value_evaluation.failed",
                {
                    "error_code": "research_value_evaluation_failed",
                    "cause_code": str(getattr(exc, "error_code", type(exc).__name__)),
                    "retryable": True,
                },
            )
            return False

    def _record_completed(
        self,
        run_id: str,
        state: dict[str, object],
        gate_status: str,
        artifact_id: str,
    ) -> None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            final = ResearchSessionResult.model_validate(state["final_result"])
            run.runtime_version = final.runtime_version
            run.latency_ms = round((final.finished_at - final.started_at).total_seconds() * 1000)
            run.cost_usd = final.estimated_cost_usd
            run.updated_at = self.clock()
        self.database.record_run_event(
            run_id,
            self._next_event_sequence(run_id),
            "research.agentic.completed",
            {
                "artifact_id": artifact_id,
                "gate_status": gate_status,
                "decision_snapshot_id": state.get("decision_snapshot_id"),
                "runtime_id": final.runtime_id,
                "runtime_version": final.runtime_version,
                "research_session_id": final.research_session_id,
                "rounds": len(final.rounds),
                "tool_calls": final.total_tool_calls,
                "subagents": final.total_subagents,
            },
        )

    def _next_event_sequence(self, run_id: str) -> int:
        timeline = self.database.get_timeline(run_id)
        return max((item.sequence_no for item in timeline), default=0) + 1


def _input_evidence(row: dict[str, Any], *, max_chars: int = 4000) -> ResearchInputEvidence:
    source_type = str(row.get("source_type", "document"))
    kind = cast(
        Literal["official", "market", "web", "transcript", "document"],
        {
            "transcript": "transcript",
            "official_feed": "official",
            "web": "web",
            "document": "document",
        }.get(source_type, "document"),
    )
    authority = "official" if kind == "official" else "unverified"
    # ResearchInputEvidence is a bounded prompt projection. The immutable
    # Observation/Snapshot retains the full source text and content hash.
    excerpt = project_excerpt(str(row.get("text", "")), (), max_chars)
    return ResearchInputEvidence(
        evidence_id=str(row["evidence_id"]),
        kind=kind,
        authority=authority,
        source_id=str(row["source_id"]),
        source_url=row.get("source_url"),
        published_at=_parse_optional_datetime(row.get("published_at")),
        observed_at=_parse_datetime(row["observed_at"]),
        received_at=_parse_datetime(row["received_at"]),
        content_hash=str(row["content_hash"]),
        excerpt=excerpt,
    )


def _library_candidates(database: Database, run_id: str) -> tuple[EvidenceCandidate, ...]:
    with database.session() as session:
        return _library_candidates_in_session(session, run_id)


def _library_candidates_in_session(session: Session, run_id: str) -> tuple[EvidenceCandidate, ...]:
    """Borrow the caller session; retain the original selection and consent checks."""
    scope = session.get(ResearchRunScopeRecord, run_id)
    if scope is None:
        return ()
    version_ids = json.loads(scope.selected_version_ids_json)
    if not version_ids:
        return ()
    versions = []
    for version_id in version_ids:
        version = session.get(ResearchDocumentVersionRecord, version_id)
        document = session.get(ResearchDocumentRecord, version.document_id) if version else None
        if version is None or document is None or version.status != "ready":
            raise ValueError("selected_document_version_not_ready")
        if is_known_test_hash(version.original_file_hash):
            raise ValueError("library_test_material_not_market_evidence")
        if scope.research_family not in json.loads(document.market_scopes_json):
            raise ValueError("document_market_scope_mismatch")
        instruments = json.loads(document.instrument_scopes_json)
        if instruments and not set(json.loads(scope.instrument_scope_json)).issubset(instruments):
            raise ValueError("document_instrument_scope_mismatch")
        if document.privacy_mode != "remote_allowed" and not (
            scope.library_context_mode == "selected" and scope.allow_remote_library_content
        ):
            raise ValueError("document_remote_consent_required")
        versions.append((version, document))
    result: list[EvidenceCandidate] = []
    for version, document in versions:
        blocks = (
            session.query(ResearchDocumentBlockRecord)
            .filter_by(version_id=version.version_id)
            .all()
        )
        blocks.sort(
            key=lambda block: (
                block.page_no,
                int(block.block_id.rsplit(":", 1)[-1])
                if block.block_id.rsplit(":", 1)[-1].isdigit()
                else 0,
                block.block_id,
            )
        )
        correction = (
            session.query(ResearchDocumentCorrectionRecord)
            .filter_by(version_id=version.version_id)
            .order_by(ResearchDocumentCorrectionRecord.created_at.desc())
            .first()
        )
        if correction is None or correction.accepted_at is None:
            raise ValueError("document_review_required")
        changes = {item["block_id"]: item for item in json.loads(correction.changes_json)}
        sections = []
        for block in blocks:
            change = changes.get(block.block_id)
            if change and change["original"] != block.content:
                raise ValueError("document_correction_source_mismatch")
            body = change["corrected"] if change else block.content
            # Each source window retains its page/block locator and text offset.
            for offset in range(0, len(body), 2800):
                window = body[offset : offset + 2800]
                sections.append(
                    f"[{block.locator}; chars {offset}:{offset + len(window)}]\n{window}"
                )
        chunks: list[str] = []
        current = ""
        for section in sections:
            if current and len(current) + len(section) + 2 > 3500:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{section}".strip()
        if current:
            chunks.append(current)
        if not chunks:
            raise ValueError("document_readable_content_missing")
        for index, body in enumerate(chunks, 1):
            excerpt = (
                f"Reviewed library source: {document.title[:200]}. Auxiliary material; "
                f"independently verify factual claims. Part {index}/{len(chunks)}.\n{body}"
            )
            digest = hashlib.sha256(excerpt.encode()).hexdigest()
            result.append(
                EvidenceCandidate(
                    evidence_id=f"library:{version.version_id}:part:{index}:{digest[:12]}",
                    requirement_id="market_narrative",
                    kind="document",
                    authority="unverified",
                    source_id=f"research-library:{version.version_id}",
                    source_url=None,
                    published_at=as_utc(version.published_at),
                    observed_at=cast(datetime, as_utc(version.observed_at)),
                    received_at=cast(datetime, as_utc(version.received_at)),
                    content_hash=digest,
                    excerpt=excerpt,
                    content_ref=f"research-library://{version.version_id}/part/{index}",
                    acquisition_receipt_id=None,
                    source_content_hash=version.original_file_hash,
                    parser_ref="research-library.reviewed-blocks.v1",
                    structured_payload_ref=None,
                    tool_call_id=None,
                    research_session_id=run_id,
                    round=1,
                    quality="accepted",
                    freshness_status="unknown",
                    conflict_group=None,
                )
            )
    rows = (
        session.query(AnalystClaimRecord)
        .filter(AnalystClaimRecord.version_id.in_(version_ids))
        .filter(AnalystClaimRecord.status.in_(["candidate", "accepted"]))
        .order_by(AnalystClaimRecord.created_at)
        .all()
    )
    version_by_id = {version.version_id: version for version, _document in versions}
    for row in rows:
        version = version_by_id[row.version_id]
        quote = row.supporting_quote or row.claim_text
        content_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        result.append(
            EvidenceCandidate(
                evidence_id=f"library:{row.claim_id}",
                requirement_id="market_narrative",
                kind="document",
                authority="unverified",
                source_id=f"research-library:{row.version_id}",
                source_url=None,
                published_at=as_utc(version.published_at),
                observed_at=cast(datetime, as_utc(version.observed_at)),
                received_at=cast(datetime, as_utc(version.received_at)),
                content_hash=content_hash,
                excerpt=row.supporting_quote[:4000] or row.claim_text[:4000],
                content_ref=f"research://claim/{row.claim_id}",
                acquisition_receipt_id=None,
                source_content_hash=version.original_file_hash,
                parser_ref="research-library.claim.v1",
                structured_payload_ref=None,
                tool_call_id=None,
                research_session_id=run_id,
                round=1,
                quality="accepted" if row.status == "accepted" else "candidate",
                freshness_status="unknown",
                conflict_group=None,
            )
        )
    return tuple(result)


class _PreparationPendingDenied(ValueError):
    def __init__(self, outcome: str, code: str) -> None:
        super().__init__(code)
        self.outcome = outcome


def _preparation_checkpoint_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("research_preparation_checkpoint_identity_invalid")
    return value


def _is_skill_preparation_observation(error: BaseException) -> bool:
    # This adapter-owned subtype exists only after actual ProjectionPending and
    # the original prepared Task anchor were checked. It grants no permission;
    # _record_preparation_pending re-reads full originals in its current writer.
    from packages.runtime_adapters.dsh_runtime.web_runtime import SkillPreparationPendingExecutionError
    return (type(error) is SkillPreparationPendingExecutionError
            and error.error_code == "research_skill_preparation_failed"
            and error.cause_code == "cancellation_projection_wait_elapsed"
            and str(error) == "Skill selection observation is pending before dispatch")


def _require_skill_preparation_recovery_anchor(error, actual_anchor) -> None:
    # Called only with the full normal original reader's fresh writer result.
    # Equal primitive metadata alone is never native Task execution permission.
    if not _is_skill_preparation_observation(error) or actual_anchor != error.anchor:
        raise ValueError("skill_preparation_pending_original_task_changed")
    observed = error.pending_observation.poll()
    if observed.state not in {"pending", "ready"} or (
            observed.state == "pending" and observed.limitation != "cancellation_projection_wait_elapsed"):
        raise ValueError(observed.limitation or "skill_preparation_pending_operation_invalid")



def _is_preparation_pending(error: BaseException, phase: str) -> bool:
    # Only the named Runtime prepare wait or selection initial-BEGIN wait is
    # eligible. Generic SQL contention cannot reopen an execution or commit.
    return (
        phase == "execute"
        and isinstance(error, AgentExecutionError)
        and error.origin == "orchestration"
        and error.retryable is True
        and error.provider_id == "dsh-web"
        and (
            _is_skill_preparation_observation(error)
            or (
                error.error_code == "research_method_binding_failed"
                and error.cause_code == "cancellation_projection_wait_elapsed"
                and str(error) == "Execution preparation was not confirmed"
            )
            or (
                error.error_code == "research_context_selection_pending"
                and error.cause_code == "sqlite_initial_begin_busy"
                and str(error) == "Context selection preparation was not confirmed"
            )
        )
    )


def _pending_diagnostic(error: BaseException) -> dict[str, object]:
    # Preserve the original exception attributes; ErrorProvenance alone has
    # no provider/model/message/attempt fields. No Provider body is exposed.
    return {
        "message": str(error),
        **{
            key: getattr(error, key, None)
            for key in (
                "error_code",
                "cause_code",
                "origin",
                "retryable",
                "attempt",
                "provider_id",
                "model",
                "capability_id",
                "tool_call_id",
                "deadline_ms",
            )
        },
    }


def _pending_permission(
    run: RunRecord | None,
    event_id: str,
    strategy_version: str,
    owner: str,
    now: datetime,
) -> None:
    if run is not None and run.status == RunStatus.cancelled.value:
        raise _PreparationPendingDenied("cancelled", run.error_code or "cancelled_by_owner")
    expiry = as_utc(run.lease_expires_at) if run is not None else None
    if (
        run is None
        or run.event_id != event_id
        or run.strategy_version != strategy_version
        or run.status != RunStatus.running.value
        or run.lease_owner != owner
        or expiry is None
        or expiry <= now
        or run.artifact_id is not None
        or run.finished_at is not None
        or run.error_code is not None
    ):
        raise _PreparationPendingDenied("lease_lost", "run_publication_lease_lost")


def _error_provenance(error: BaseException) -> ErrorProvenance:
    provenance = getattr(error, "provenance", None)
    if callable(provenance):
        value = provenance()
        if isinstance(value, ErrorProvenance):
            return value
    # This exact internal reason is safe to show. Never project arbitrary
    # exception messages (which can contain request bodies or credentials).
    safe_cause = (
        "original_preparation_deadline_elapsed"
        if isinstance(error, PermissionError)
        and str(error) == "original_preparation_deadline_elapsed"
        else type(error).__name__
    )
    return ErrorProvenance(
        error_code=str(getattr(error, "error_code", "research_worker_failed")),
        origin=str(getattr(error, "origin", "orchestration")),  # type: ignore[arg-type]
        # Preserve the Python exception class when a boundary raises a plain
        # ValueError/ValidationError.  A generic ``research_worker_failed``
        # without a phase or cause made a completed DSH session impossible to
        # diagnose from the product trace.
        cause_code=getattr(error, "cause_code", None) or safe_cause,
        capability_id=getattr(error, "capability_id", None),
        tool_call_id=getattr(error, "tool_call_id", None),
        retryable=bool(getattr(error, "retryable", False)),
        deadline_ms=getattr(error, "deadline_ms", None),
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise ValueError("research_timestamp_invalid")
    normalized = as_utc(cast(datetime | None, parsed))
    if normalized is None:
        raise ValueError("research_timestamp_invalid")
    return normalized


def _parse_optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _parse_datetime(value)
