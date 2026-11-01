"""Frozen current-paper inputs; shape alone grants no admission authority."""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSettingsValuesV1,
    DecisionSourceRefV1,
    PerpetualResearchCommandV3,
    PerpetualResearchPaperPartitionV3,
)

INPUT_POLICY = "saved_preferences_current_paper_ledger.v1"
type Sha = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


def _current_paper_partition(value):
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return PerpetualResearchPaperPartitionV3.model_validate(raw).model_dump(mode="json")


# The consent boundary uses the canonical strict paper contract. Existing
# trigger services receive their original base DTO, with identical field bytes.
type CurrentPaperPartition = Annotated[
    DecisionSettingsPartitionV1,
    BeforeValidator(_current_paper_partition),
    WithJsonSchema(PerpetualResearchPaperPartitionV3.model_json_schema()),
]


class _InputValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, regex_engine="python-re")


class PerpetualResearchDeadlinePolicyV1(_InputValue):
    schema_version: Literal["perpetual-research-deadline-policy.v1"]
    runtime_seconds: Annotated[int, Field(strict=True, ge=3600, le=3600)]
    operation_margin_seconds: Annotated[int, Field(strict=True, ge=1800, le=1800)]
    candidate_basis: Literal["entry_prepared_at"]
    runtime_basis: Literal["admitted_at"]
    trigger_scope: Literal["new_admission_only"]


class ResearchInputAuthorityWitnessV1(_InputValue):
    schema_version: Literal["perpetual-research-input-authority.v1"]
    origin: Literal["manual_v3"]
    owner_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    planned_run_id: Annotated[str, Field(strict=True, min_length=1)]
    request_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    command_schema_version: Literal["perpetual-research-command.v3"]
    command: PerpetualResearchCommandV3
    command_hash: Sha
    input_policy: Literal["saved_preferences_current_paper_ledger.v1"]


class ResearchAutomaticInputAuthorityWitnessV1(ResearchInputAuthorityWitnessV1):
    schema_version: Literal["perpetual-research-automatic-input-authority.v1"]
    origin: Literal["automatic_consent"]
    source_schema_version: Literal[
        "perpetual-research-periodic-trigger.v1",
        "perpetual-research-event-trigger.v1",
        "perpetual-plan-review-trigger.v1",
        "perpetual-plan-review-trigger.v2",
        "perpetual-plan-review-trigger.v3",
    ]
    source_proof_hash: Sha
    consent_schema_version: Literal[
        "perpetual-research-schedule-receipt.v2",
        "perpetual-plan-watch-receipt.v2",
        "perpetual-plan-watch-receipt.v3",
    ]
    consent_ref: DecisionSourceRefV1
    consent_command_hash: Sha
    trigger_ref: DecisionSourceRefV1
    claim_request_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    claim_generation: Annotated[int, Field(strict=True, ge=1)]
    claim_owner: Annotated[str, Field(strict=True, min_length=1)]


type ResearchInputAuthorityWitness = Annotated[
    ResearchInputAuthorityWitnessV1 | ResearchAutomaticInputAuthorityWitnessV1,
    Field(discriminator="origin"),
]


class ResearchEffectiveRefsV1(_InputValue):
    account_ref: DecisionSourceRefV1
    position_ref: DecisionSourceRefV1
    utility_ref: DecisionSourceRefV1


class ResearchInputDerivationV1(_InputValue):
    method_ref: DecisionSourceRefV1
    implementation_hash: Sha
    allowed_utility_fields: list[Literal["account_ref", "position_scenario", "hash"]]


class ResearchAccountBindingV1(_InputValue):
    schema_version: Literal["perpetual-research-account-binding.v1"]
    run_id: Annotated[str, Field(strict=True, min_length=1)]
    owner_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    partition: DecisionSettingsPartitionV1
    horizon: Literal["4h", "24h", "72h"]
    input_policy: Literal["saved_preferences_current_paper_ledger.v1"]
    authority: ResearchInputAuthorityWitness
    settings_revision: DecisionSourceRefV1
    settings_state_version: Annotated[int, Field(strict=True, ge=1)]
    base_utility_ref: DecisionSourceRefV1
    ledger_revision: DecisionSourceRefV1
    ledger_state_version: Annotated[int, Field(strict=True, ge=1)]
    observed_at: AwareDatetime
    prepared_at: AwareDatetime
    valid_until: AwareDatetime
    effective_values: DecisionSettingsValuesV1
    effective_refs: ResearchEffectiveRefsV1
    derivation: ResearchInputDerivationV1


class PreparedPerpetualAdmissionV2(_InputValue):
    account_binding: ResearchAccountBindingV1
    admission: dict[str, Any]


@dataclass(frozen=True)
class PreparedResearchAccountInputsV1:
    binding: ResearchAccountBindingV1
    binding_ref: DecisionSourceRefV1
    effective_values: DecisionSettingsValuesV1


@dataclass(frozen=True)
class AuthenticatedResearchInputs:
    mode: Literal["saved", "current_paper"]
    binding: ResearchAccountBindingV1 | None
    effective_values: DecisionSettingsValuesV1
