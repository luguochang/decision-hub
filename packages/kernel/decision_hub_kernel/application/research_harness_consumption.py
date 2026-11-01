"""Factory-only consumption of the trusted release's immutable policy bytes.

This receipt proves the Factory used an archived definition for first freeze.
It is not MethodV4, a native Host witness, an evaluation qualification, or an
approval. Deployed/fixture/shadow candidates remain unavailable in this slice.
The caller owns the original first-freeze transaction and its commit/rollback.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchPackDefinitionV2,
)
from packages.kernel.decision_hub_kernel.application.research_binding import FrozenResearchBundle
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    MAX_ARTIFACT_BYTES,
    HarnessArtifactIndexV1,
    PreparedHarnessManifestV1,
    ValidatedHarnessArtifact,
)
from packages.kernel.decision_hub_kernel.application.research_harness_deployment import (
    HarnessSelectionV1,
    PreparedHarnessChoice,
    ResearchHarnessDeploymentService,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_policy_resources import canonical_hash
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc

CONSUMPTION_ROLE = "research_harness_consumption"
CONSUMPTION_SCHEMA = "research-harness-bundle-consumption.v1"
Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Id = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class HarnessConsumptionError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


class HarnessBundleConsumptionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["research-harness-bundle-consumption.v1"]
    execution_scope: Literal["research_harness"]
    run_id: Id
    event_id: Id
    selection: HarnessSelectionV1
    artifact_json: Annotated[StrictStr, Field(min_length=1, max_length=MAX_ARTIFACT_BYTES)]
    pack_definition_hash: Hash
    full_task_hash: Hash
    compiled_policy_hash: Hash
    factory_definition_consumed: Literal[True]
    native_execution_proof: Literal["unverified"]
    eligible_for_deployment: Literal[False]
    evolution_pointer_consumed: Literal[False]
    recorded_at: AwareDatetime


def _hash(raw: str | bytes) -> str:
    return hashlib.sha256(raw.encode("utf-8") if isinstance(raw, str) else raw).hexdigest()


def _encoded(value: BaseModel) -> str:
    return canonical_recipe(value.model_dump(mode="json"))


def _output_id(run_id: str) -> str:
    return "harness-consumed_" + _hash(run_id)


@dataclass(frozen=True)
class PreparedHarnessConsumption:
    choice: PreparedHarnessChoice
    definition_json: bytes

    @property
    def definition(self) -> ResearchPackDefinitionV2:
        return ResearchPackDefinitionV2.model_validate_json(self.definition_json)


def _validate_original(
    receipt: HarnessBundleConsumptionV1, bundle: FrozenResearchBundle
) -> None:
    """Verify its frozen originals, independent of today's archive or pointer."""
    selection = receipt.selection
    raw = receipt.artifact_json.encode("utf-8")
    index = selection.candidate.artifact_index
    if len(raw) > MAX_ARTIFACT_BYTES or _hash(raw) != index.artifact_bytes_sha256:
        raise HarnessConsumptionError("harness_consumed_artifact_hash_invalid")
    manifest = PreparedHarnessManifestV1.model_validate_json(raw)
    definition = manifest.prepared_pack_definition
    if (
        selection.selection_kind != "release_baseline"
        or selection.expected_generation != 0
        or selection.pointer is not None
        or selection.domain_pack_ref != bundle.task.request.domain_pack_ref
        or manifest.candidate_id != selection.candidate.candidate_id
        or manifest.version != selection.candidate.version
        or manifest.domain_pack_ref != selection.domain_pack_ref
        or manifest.installed_runtime_binding != selection.candidate.installed_runtime_binding
        or manifest.installed_pack_definition_hash != index.definition_hash
        or canonical_hash(manifest.model_dump(mode="json")) != index.manifest_hash
        or canonical_hash(definition.model_dump(mode="json")) != index.definition_hash
        or definition != bundle.pack.definition
        or receipt.run_id != bundle.pack.run_id
        or receipt.run_id != bundle.task.run_id
        or receipt.run_id != bundle.compiled.run_id
        or receipt.event_id != bundle.compiled.event_id
        or receipt.pack_definition_hash != bundle.pack.definition_hash
        or receipt.pack_definition_hash != bundle.task.pack_definition_hash
        or receipt.pack_definition_hash != bundle.compiled.pack_definition_hash
        or receipt.full_task_hash != bundle.task.request_hash
        or receipt.full_task_hash != bundle.compiled.full_task_hash
        or receipt.compiled_policy_hash != bundle.task.compiled_policy_hash
        or receipt.compiled_policy_hash != bundle.compiled.compiled.compiled_hash
        or as_utc(receipt.recorded_at) != as_utc(bundle.recorded_at)
    ):
        raise HarnessConsumptionError("harness_consumed_originals_mismatch")


def read_harness_consumption_in_session(
    session: Session, bundle: FrozenResearchBundle
) -> HarnessBundleConsumptionV1 | None:
    row = session.scalar(
        select(RunRoleOutputRecord)
        .filter_by(run_id=bundle.pack.run_id, role=CONSUMPTION_ROLE)
        .execution_options(populate_existing=True)
    )
    if row is None:
        # An old frozen Run never receives a new selection/consumption receipt.
        return None
    if (
        row.output_id != _output_id(bundle.pack.run_id)
        or row.schema_version != CONSUMPTION_SCHEMA
        or row.payload_hash != _hash(row.payload_json)
    ):
        raise HarnessConsumptionError("harness_consumed_record_integrity_invalid")
    receipt = HarnessBundleConsumptionV1.model_validate_json(row.payload_json)
    _validate_original(receipt, bundle)
    if as_utc(row.created_at) != as_utc(receipt.recorded_at):
        raise HarnessConsumptionError("harness_consumed_timestamp_invalid")
    return receipt


class HarnessFirstFreezeService:
    """Internal optional dependency; no caller-selected artifact or pass flags."""

    def __init__(self, deployment: ResearchHarnessDeploymentService) -> None:
        # Never turn the isolated fixture approval service into Run permission.
        if deployment.isolated:
            raise PermissionError("harness_fixture_execution_not_authorized")
        self.deployment = deployment

    def validate_factory_baseline(self, definition: ResearchPackDefinitionV2) -> None:
        if definition != self.deployment.baseline.definition:
            raise HarnessConsumptionError("harness_factory_baseline_mismatch")

    def prepare(self) -> PreparedHarnessConsumption:
        choice = self.deployment.prepare_active_choice(self.deployment.release.domain_pack_ref)
        if (
            choice.selection.selection_kind != "release_baseline"
            or choice.selection.candidate != self.deployment.release
            or choice.pointer is not None
            or choice.decision_json is not None
            or choice.artifact.definition != self.deployment.baseline.definition
        ):
            raise PermissionError("harness_loaded_evaluation_assignment_unavailable")
        # Own immutable bytes/index; the later writer cannot consume a mutated DTO.
        index = HarnessArtifactIndexV1.model_validate_json(choice.artifact.index.model_dump_json())
        snapshot = PreparedHarnessChoice(
            bytes(choice.selection_json), None, ValidatedHarnessArtifact(choice.artifact.raw_bytes, index)
        )
        return PreparedHarnessConsumption(snapshot, _encoded(snapshot.artifact.definition).encode("utf-8"))

    def verify_new_choice_in_session(
        self, session: Session, prepared: PreparedHarnessConsumption, run_id: str
    ) -> None:
        self.deployment.verify_choice_in_first_freeze(session, prepared.choice)
        if (
            prepared.definition != prepared.choice.artifact.definition
            or prepared.definition != self.deployment.baseline.definition
        ):
            raise HarnessConsumptionError("harness_prepared_definition_changed")
        row = session.scalar(
            select(RunRoleOutputRecord.output_id).filter_by(run_id=run_id, role=CONSUMPTION_ROLE)
        )
        if row is not None:
            raise HarnessConsumptionError("harness_consumption_without_original_bundle")

    def record_new_consumption_in_session(
        self, session: Session, prepared: PreparedHarnessConsumption, bundle: FrozenResearchBundle
    ) -> HarnessBundleConsumptionV1:
        # Repeat the gen0/archive fence immediately before the receipt flush.
        self.verify_new_choice_in_session(session, prepared, bundle.pack.run_id)
        receipt = HarnessBundleConsumptionV1(
            schema_version=CONSUMPTION_SCHEMA,
            execution_scope="research_harness",
            run_id=bundle.pack.run_id,
            event_id=bundle.compiled.event_id,
            selection=prepared.choice.selection,
            artifact_json=prepared.choice.artifact.raw_bytes.decode("utf-8"),
            pack_definition_hash=bundle.pack.definition_hash,
            full_task_hash=bundle.task.request_hash,
            compiled_policy_hash=bundle.compiled.compiled.compiled_hash,
            factory_definition_consumed=True,
            native_execution_proof="unverified",
            eligible_for_deployment=False,
            evolution_pointer_consumed=False,
            recorded_at=bundle.recorded_at,
        )
        _validate_original(receipt, bundle)
        raw = _encoded(receipt)
        session.add(RunRoleOutputRecord(
            output_id=_output_id(receipt.run_id), run_id=receipt.run_id,
            role=CONSUMPTION_ROLE, payload_json=raw, payload_hash=_hash(raw),
            schema_version=CONSUMPTION_SCHEMA, created_at=bundle.recorded_at,
        ))
        session.flush()
        original = read_harness_consumption_in_session(session, bundle)
        if original != receipt:
            raise HarnessConsumptionError("harness_consumption_insert_incomplete")
        return receipt
