"""Server current-method witnesses and historical, context-only case selection.

The Factory cannot guess a Method before Host preparation. A successful native
V3 execution may register an immutable witness. Subsequent Factory calls read
that witness in their own writer snapshot and authenticate the current sources.
This registry grants neither Fact authority nor product/model qualification.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
    ResearchTaskScope,
)
from packages.evals.analytical_assessment import stable_hub_recipe
from packages.kernel.decision_hub_kernel.application import research_context
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as graph_recipe
from packages.kernel.decision_hub_kernel.application import research_policy_render_v2 as renderer
from packages.kernel.decision_hub_kernel.application.market_case import (
    MarketCaseService,
    _read_boundary,  # pyright: ignore[reportPrivateUsage] # Same physical SELECT-only snapshot.
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    COMPILED_ROLE,
    PreparedResearchPolicyRefs,
    load_prepared_pack_definition,
)
from packages.kernel.decision_hub_kernel.application.research_context_selector_v3 import (
    RANKING_RULES_REF,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
    authenticated_graph_recipe_identity,
    read_graph_final_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_policy_checker import (
    citation_execution_identity,
)
from packages.kernel.decision_hub_kernel.application.research_policy_compiler import (
    code_identity,
    compile_policy,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.market_case import CaseScope
from packages.kernel.decision_hub_kernel.ports.market_path import FrozenContract, aware_utc
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.dsh_runtime import profile

SCHEMA = "market-case-current-method.v1"
ROLE = "market_case_current_method"
OUTPUT_SCHEMA = "agentic_research.schema.yaml#/$defs/research_session_result"
HASH = r"^[a-f0-9]{64}$"


class CurrentMethodWitness(FrozenContract):
    schema_version: Literal["market-case-current-method.v1"] = SCHEMA
    registry_id: str
    witness_run_id: str
    market: Literal["crypto"] = "crypto"
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    horizon: Literal["24h"] = "24h"
    configuration_hash: str = Field(pattern=HASH)
    configuration: dict[str, Any]
    stable_recipe_hash: str = Field(pattern=HASH)
    stable_recipe: dict[str, Any]
    method_ref: ObservedExecutionRef
    compiled_ref: ObservedExecutionRef
    graph_selection_ref: ObservedExecutionRef
    graph_final_ref: GraphFinalProjectionRef
    current_known_at: AwareDatetime


@dataclass(frozen=True)
class _CurrentConfiguration:
    identity: dict[str, Any]
    pack: Any
    installation: Any

    @property
    def digest(self) -> str:
        return recipe_hash(self.identity)


@dataclass(frozen=True)
class _AuthenticatedMethod:
    instrument: str
    stable_recipe: dict[str, Any]
    method_ref: ObservedExecutionRef
    compiled_ref: ObservedExecutionRef
    graph_selection_ref: ObservedExecutionRef
    graph_final_ref: GraphFinalProjectionRef
    original_known_at: datetime
    frozen_context_bound: bool = True


def _reference(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    clock = as_utc(row.created_at)
    if clock is None:
        raise ValueError("market_case_method_original_clock_invalid")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=clock,
    )


class MarketCaseContextProvider:
    """Trusted composition dependency; no network, model, registration on reads.

    One deterministic registry row per current configuration/asset records the
    first authenticated native witness. Repeated observations do not move its
    known time or replace its originals. A conflicting stable method is an error.
    """

    def __init__(
        self,
        database: Database,
        archive: RawBodyArchive,
        *,
        prepared_pack_root: Path,
        refs: PreparedResearchPolicyRefs,
        allowed_capabilities: tuple[str, ...],
        execution_mode: Literal["live", "replay"],
        runtime_id: str,
        profile_ref: str,
        memory_enabled: bool = True,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.archive = archive
        self.cases = MarketCaseService(database, archive)
        self.prepared_pack_root = prepared_pack_root
        self.refs = refs
        self.allowed_capabilities = tuple(allowed_capabilities)
        self.execution_mode = execution_mode
        self.runtime_id = runtime_id
        self.profile_ref = profile_ref
        self.memory_enabled = memory_enabled
        self.clock = clock

    def _current(self, instrument: str) -> _CurrentConfiguration:
        if self.execution_mode != "live" or self.runtime_id != "dsh":
            raise ValueError("market_case_current_runtime_unavailable")
        installation = graph_recipe._installation()  # pyright: ignore[reportPrivateUsage]
        pack = load_prepared_pack_definition(
            self.prepared_pack_root,
            self.refs,
            allowed_capabilities=self.allowed_capabilities,
            execution_mode=self.execution_mode,
        )
        source_hashes = {}
        for name, module, expected in (
            ("prompt_profile", profile, profile.PROMPT_POLICY_HASH),
            ("context_policy", research_context, research_context.CONTEXT_POLICY_HASH),
            ("policy_renderer", renderer, renderer.RENDERER_HASH),
        ):
            actual = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("market_case_current_loaded_source_changed")
            source_hashes[name] = actual
        identity = {
            "schema_version": "market-case-current-configuration.v1",
            "market": "crypto",
            "instrument": instrument,
            "horizon": "24h",
            "runtime_id": self.runtime_id,
            "profile_ref": self.profile_ref,
            "execution_mode": self.execution_mode,
            "research_mode": "current_decision",
            "pack_hash": recipe_hash(pack.model_dump(mode="json")),
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "compiler": code_identity(),
            "checker": citation_execution_identity(),
            "loaded_sources": source_hashes,
            "context_selection": {
                "mode": "required_before_rpc",
                "selection_schema": "research-context-selection.v4",
                "ranking_rules_ref": RANKING_RULES_REF,
                "time_rule_ref": "research-context.received-cutoff.v1",
            },
            "installation_receipt": installation.receipt.model_dump(mode="json"),
            "installed_pointer": installation.pointer.model_dump(mode="json"),
            "registration": installation.registration.model_dump(mode="json"),
            "plugin_build_hash": installation.plugin_build_hash,
            "graph_dependency_hash": installation.dependency_hash,
        }
        return _CurrentConfiguration(identity, pack, installation)

    @staticmethod
    def _registry_id(current: _CurrentConfiguration) -> str:
        return "current_method_" + current.digest

    def _authenticate(
        self,
        session: Session,
        run_id: str,
        current: _CurrentConfiguration,
    ) -> _AuthenticatedMethod:
        admitted = read_run_spot_admission_in_session(session, run_id)
        if admitted is None:
            raise ValueError("market_case_method_spot_original_missing")
        bundle, _ = read_bundle_identity_in_session(session, run_id)
        if bundle.request.research_mode != "current_decision":
            raise ValueError("market_case_current_method_mode_unavailable")
        prepared = read_prepared_execution_in_session(session, run_id)
        method = prepared.method
        definition = method.definition.model_dump(mode="json")
        if method.schema_version != "research-execution-method.v3":
            raise ValueError("market_case_method_v3_required")
        selection = graph_recipe.read_graph_recipe_selection_v3_in_session(session, run_id)
        if selection is None:
            raise ValueError("market_case_method_graph_missing")
        if (
            admitted.instrument != current.identity["instrument"]
            or admitted.effective.plugin_build_hash != current.installation.plugin_build_hash
            or bundle.pack.definition.model_dump(mode="json")
            != current.pack.model_dump(mode="json")
        ):
            raise ValueError("market_case_current_configuration_mismatch")
        expected_compiled = compile_policy(
            ResearchPolicyResource.model_validate(
                current.pack.gate_policy_resource.model_dump(mode="json")
            ),
            ResearchPolicyResource.model_validate(
                current.pack.rubric_policy_resource.model_dump(mode="json")
            ),
            [
                ResearchRoleRubricBinding.model_validate(item.model_dump(mode="json"))
                for item in current.pack.role_rubric_bindings
            ],
            scope=ResearchTaskScope.model_validate(bundle.task.scope.model_dump(mode="json")),
        )
        if expected_compiled.model_dump(mode="json") != bundle.compiled.compiled.model_dump(
            mode="json"
        ):
            raise ValueError("market_case_current_compiled_policy_changed")
        manifest = current.pack.manifest
        expected_definition = {
            "runtime_id": self.runtime_id,
            "profile_ref": self.profile_ref,
            "execution_mode": self.execution_mode,
            "research_mode": "current_decision",
            "prompt_policy_hash": profile.PROMPT_POLICY_HASH,
            "context_policy_hash": research_context.CONTEXT_POLICY_HASH,
            "research_policy_renderer_hash": renderer.RENDERER_HASH,
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "execution_budget": manifest.execution_budget.model_dump(mode="json"),
            "domain_pack_ref": f"{manifest.pack_id}.v{manifest.version.split('.')[0]}",
            "role_profile_ref": current.pack.research_policy.manager_ref,
            "output_schema_ref": OUTPUT_SCHEMA,
            "agent_preset": "decision-research",
            "permission_ref": "decision-hub://permissions/research-only",
        }
        if (
            any(definition[key] != value for key, value in expected_definition.items())
            or {
                key: definition["context_selection_binding"][key]
                for key in current.identity["context_selection"]
            }
            != current.identity["context_selection"]
            or definition["upstream_identity"]["plugin_build_hash"]
            != current.installation.plugin_build_hash
            or definition["runtime_version"]
            != (
                f"dsh-web-{definition['upstream_identity']['source_version']}"
                f"+{current.installation.plugin_build_hash[:12]}"
            )
        ):
            raise ValueError("market_case_current_method_mismatch")
        final_row = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "research_graph_final",
            )
            .execution_options(populate_existing=True)
        )
        if final_row is None or final_row.schema_version != "research-graph-final-projection.v3":
            raise ValueError("market_case_method_final_missing")
        final_ref = GraphFinalProjectionRef(row=_reference(final_row))
        frozen = read_graph_final_in_session(
            session,
            run_id=run_id,
            reference=final_ref,
            expected_lease_owner=None,
            require_current_permission=False,
        )
        graph_identity = authenticated_graph_recipe_identity(frozen.receipt)
        if graph_identity != {
            "recipe_version": "research-graph-projection.v3",
            "dependency_hash": current.installation.dependency_hash,
        }:
            raise ValueError("market_case_current_graph_identity_changed")
        stable = stable_hub_recipe(definition, bundle.compiled.compiled.model_dump(mode="json"))
        stable["graph_recipe"] = graph_identity
        context_bound = self._verify_frozen_context(bundle.request.input_evidence, stable)
        method_row = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == METHOD_ROLE,
            )
            .execution_options(populate_existing=True)
        )
        compiled_row = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == COMPILED_ROLE,
            )
            .execution_options(populate_existing=True)
        )
        if method_row is None or compiled_row is None:
            raise ValueError("market_case_method_original_missing")
        method_ref, compiled_ref = _reference(method_row), _reference(compiled_row)
        return _AuthenticatedMethod(
            admitted.instrument,
            stable,
            method_ref,
            compiled_ref,
            selection.reference,
            final_ref,
            max(
                method_ref.recorded_at,
                compiled_ref.recorded_at,
                selection.reference.recorded_at,
                final_ref.row.recorded_at,
            ),
            context_bound,
        )

    @staticmethod
    def _verify_frozen_context(inputs: list, stable_recipe: dict[str, Any]) -> bool:
        """An old Task cannot be enrolled later; selected context must match its true Method."""
        import json

        receipts = [
            item for item in inputs if item.evidence_id.startswith("market_case_context_selection_")
        ]
        if not receipts:
            return False
        if len(receipts) != 1:
            raise ValueError("market_case_frozen_selection_ambiguous")
        item = receipts[0]
        body = json.loads(item.excerpt)
        digest = hashlib.sha256(item.excerpt.encode()).hexdigest()
        if (
            item.kind != "document"
            or item.authority != "unverified"
            or item.source_id != "market_case_ledger.v1"
            or item.content_hash != digest
            or item.evidence_id != "market_case_context_selection_" + digest
            or body.get("schema_version") != "market-case-context-selection.v1"
            or body.get("context_only") is not True
            or body.get("current_fact_authority") is not False
        ):
            raise ValueError("market_case_frozen_selection_changed")
        if body.get("status") in {"selected", "no_eligible_cases"}:
            if body.get("stable_hub_recipe") != recipe_hash(stable_recipe):
                raise ValueError("market_case_frozen_current_method_mismatch")
        elif (
            body.get("status")
            not in {
                "current_stable_method_unavailable",
                "current_stable_method_not_yet_known",
                "current_stable_method_unverifiable",
                "market_case_memory_disabled",
                "market_case_exact_scope_unavailable",
                "current_stable_method_mode_unavailable",
            }
            or "stable_hub_recipe" in body
        ):
            raise ValueError("market_case_frozen_selection_status_invalid")
        return True

    @staticmethod
    def _decode(row: RunRoleOutputRecord, current: _CurrentConfiguration) -> CurrentMethodWitness:
        witness = CurrentMethodWitness.model_validate_json(row.payload_json)
        if (
            row.role != ROLE
            or row.schema_version != SCHEMA
            or row.payload_json != canonical_recipe(witness.model_dump(mode="json"))
            or row.payload_hash != recipe_hash(witness.model_dump(mode="json"))
            or row.output_id != witness.registry_id
            or witness.registry_id != MarketCaseContextProvider._registry_id(current)
            or row.run_id != witness.witness_run_id
            or as_utc(row.created_at) != witness.current_known_at
            or witness.configuration_hash != current.digest
            or witness.configuration != current.identity
            or recipe_hash(witness.stable_recipe) != witness.stable_recipe_hash
            or (witness.market, witness.instrument, witness.horizon)
            != ("crypto", current.identity["instrument"], "24h")
        ):
            raise ValueError("market_case_current_method_registry_changed")
        return witness

    @staticmethod
    def _assert_witness(witness: CurrentMethodWitness, proof: _AuthenticatedMethod) -> None:
        if (
            witness.instrument != proof.instrument
            or witness.stable_recipe != proof.stable_recipe
            or witness.method_ref != proof.method_ref
            or witness.compiled_ref != proof.compiled_ref
            or witness.graph_selection_ref != proof.graph_selection_ref
            or witness.graph_final_ref != proof.graph_final_ref
            or proof.original_known_at > witness.current_known_at
        ):
            raise ValueError("market_case_current_method_witness_changed")

    def observe(self, run_id: str) -> None:
        """Post-final hook only; old/unscoped Runs are a no-op, failures surface to Worker."""
        if not self.memory_enabled:
            return
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if read_run_spot_admission_in_session(session, run_id) is None:
                return
            run = session.get(RunRecord, run_id, populate_existing=True)
            study = session.get(StudyRecord, run.study_id, populate_existing=True) if run else None
            if study is None:
                raise ValueError("market_case_current_method_study_missing")
            current = self._current(study.instrument)
            try:
                proof = self._authenticate(session, run_id, current)
            except ValueError as error:
                if str(error) == "market_case_current_method_mode_unavailable":
                    return
                raise
            if not proof.frozen_context_bound:
                return
            now = aware_utc(self.clock())
            if proof.original_known_at > now:
                raise ValueError("market_case_current_method_original_from_future")
            identity = self._registry_id(current)
            existing = session.get(RunRoleOutputRecord, identity, populate_existing=True)
            if existing:
                saved = self._decode(existing, current)
                self._assert_witness(
                    saved,
                    self._authenticate(session, saved.witness_run_id, current),
                )
                if saved.stable_recipe != proof.stable_recipe:
                    raise ValueError("market_case_current_method_ambiguous")
                return
            value = CurrentMethodWitness(
                registry_id=identity,
                witness_run_id=run_id,
                instrument=proof.instrument,
                configuration_hash=current.digest,
                configuration=current.identity,
                stable_recipe_hash=recipe_hash(proof.stable_recipe),
                stable_recipe=proof.stable_recipe,
                method_ref=proof.method_ref,
                compiled_ref=proof.compiled_ref,
                graph_selection_ref=proof.graph_selection_ref,
                graph_final_ref=proof.graph_final_ref,
                current_known_at=now,
            )
            body = canonical_recipe(value.model_dump(mode="json"))
            session.add(
                RunRoleOutputRecord(
                    output_id=identity,
                    run_id=run_id,
                    role=ROLE,
                    schema_version=SCHEMA,
                    payload_json=body,
                    payload_hash=recipe_hash(value.model_dump(mode="json")),
                    created_at=now,
                )
            )

    @staticmethod
    def _diagnostic(cutoff: datetime, *, reason: str, **details: Any) -> ResearchInputEvidence:
        body = {
            "schema_version": "market-case-context-selection.v1",
            "status": reason,
            "cutoff_at": cutoff.isoformat(),
            "context_only": True,
            "current_fact_authority": False,
            "product_release_qualified": False,
            **details,
        }
        content = canonical_recipe(body)
        digest = hashlib.sha256(content.encode()).hexdigest()
        return ResearchInputEvidence(
            evidence_id="market_case_context_selection_" + digest,
            kind="document",
            authority="unverified",
            source_id="market_case_ledger.v1",
            source_url=None,
            published_at=None,
            observed_at=cutoff,
            received_at=cutoff,
            content_hash=digest,
            excerpt=content,
        )

    def __call__(
        self,
        session: Session,
        study: StudyRecord,
        cutoff: datetime,
    ) -> tuple[ResearchInputEvidence, ...]:
        cutoff = aware_utc(cutoff)
        if not self.memory_enabled:
            return (self._diagnostic(cutoff, reason="market_case_memory_disabled"),)
        if (
            study.market != "crypto"
            or study.instrument not in {"BTC-USDT", "ETH-USDT"}
            or "24h" not in {part.strip() for part in study.horizon_scope.split(",")}
        ):
            return (self._diagnostic(cutoff, reason="market_case_exact_scope_unavailable"),)
        with _read_boundary(session):
            try:
                current = self._current(study.instrument)
                row = session.get(
                    RunRoleOutputRecord,
                    self._registry_id(current),
                    populate_existing=True,
                )
                if row is None:
                    return (self._diagnostic(cutoff, reason="current_stable_method_unavailable"),)
                witness = self._decode(row, current)
                if witness.current_known_at > cutoff:
                    return (self._diagnostic(cutoff, reason="current_stable_method_not_yet_known"),)
                proof = self._authenticate(session, witness.witness_run_id, current)
                self._assert_witness(witness, proof)
                inputs, references, retrieval = self.cases.context_in_session(
                    session,
                    scope=CaseScope(
                        market="crypto",
                        instrument=study.instrument,
                        horizon="24h",
                        method_family="ai_synthesis",
                        stable_recipe_hash=witness.stable_recipe_hash,
                    ),
                    cutoff_at=cutoff,
                    max_items=4,
                )
            except (ValueError, OSError) as error:
                return (
                    self._diagnostic(
                        cutoff,
                        reason="current_stable_method_unverifiable",
                        reason_code=str(error)[:128],
                    ),
                )
            diagnostic = self._diagnostic(
                cutoff,
                reason="selected" if inputs else "no_eligible_cases",
                registry_id=witness.registry_id,
                registry_hash=row.payload_hash,
                current_known_at=witness.current_known_at.isoformat(),
                stable_hub_recipe=witness.stable_recipe_hash,
                scope={"market": "crypto", "instrument": study.instrument, "horizon": "24h"},
                selected_refs=[
                    item.model_dump(mode="json", exclude={"excerpt"}) for item in references
                ],
                excluded=list(retrieval.excluded[:5]),
                exclusion_count=len(retrieval.excluded),
                total_scanned=retrieval.total_scanned,
                truncated=retrieval.truncated or len(retrieval.excluded) > 5,
            )
            return (diagnostic, *inputs)

    def for_research_mode(
        self,
        session: Session,
        study: StudyRecord,
        cutoff: datetime,
        *,
        research_mode: str,
    ) -> tuple[ResearchInputEvidence, ...]:
        """Factory's explicit planned mode; old three-argument provider callers stay valid."""
        if research_mode != "current_decision":
            return (
                self._diagnostic(
                    aware_utc(cutoff),
                    reason="current_stable_method_mode_unavailable",
                    research_mode=research_mode,
                ),
            )
        return self(session, study, cutoff)
