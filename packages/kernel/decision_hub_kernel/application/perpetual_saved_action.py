"""Read a report's immutable ActionPublication; never select today's advice."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, TypeAdapter
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionComparisonV2,
    ActionPlanRevisionV2,
    ActionReviewRequestV2,
    ActionReviewRequestV3,
    ComparisonCostPolicyV1,
    DecisionActionContextV2,
    DecisionSnapshotV2,
    DecisionSourceRefV1,
    PublicationBoundTargetV1,
    ResearchDecisionSynthesisV2,
    ResearchDecisionSynthesisV3,
)
from packages.kernel.decision_hub_kernel.application.perpetual_advice_query import (
    PUBLISHER,
    read_perpetual_published_originals_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import (
    ActionRepository,
    StoredCommandReceipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

JsonObject = dict[str, JsonValue]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", strict=True)]
_JSON_OBJECT = TypeAdapter(JsonObject)
_SYNTHESIS: TypeAdapter[ResearchDecisionSynthesisV2 | ResearchDecisionSynthesisV3] = TypeAdapter(
    ResearchDecisionSynthesisV2 | ResearchDecisionSynthesisV3,
    config=ConfigDict(regex_engine="python-re"),
)
_REQUEST: TypeAdapter[ActionReviewRequestV2 | ActionReviewRequestV3] = TypeAdapter(
    ActionReviewRequestV2 | ActionReviewRequestV3, config=ConfigDict(regex_engine="python-re")
)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class _Boundary(BaseModel):
    model_config = ConfigDict(regex_engine="python-re")


class _Model(_Boundary):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, regex_engine="python-re")


class SavedActionBinding(_Model):
    owner_id: StrictStr
    run_id: StrictStr
    artifact_id: StrictStr
    report_synthesis_ref: DecisionSourceRefV1
    admission_ref: DecisionSourceRefV1


class SavedActionUnavailable(_Model):
    schema_version: Literal["perpetual-saved-action.v1"] = "perpetual-saved-action.v1"
    status: Literal["unavailable"] = "unavailable"
    reason_code: Literal["action_publication_not_saved"] = "action_publication_not_saved"


class SavedActionAvailable(_Model):
    schema_version: Literal["perpetual-saved-action.v1"] = "perpetual-saved-action.v1"
    status: Literal["available"] = "available"
    binding: SavedActionBinding
    accepted_at: StrictStr
    command_receipt: JsonObject
    snapshot: DecisionSnapshotV2
    revision: ActionPlanRevisionV2
    comparison: ActionComparisonV2
    target: PublicationBoundTargetV1
    cost_assumptions: JsonObject
    cost_assumptions_ref: DecisionSourceRefV1
    derivation: JsonObject
    financial_qualified: Literal[False] = False
    execution_authorized: Literal[False] = False


class _Admission(_Boundary):
    owner_id: StrictStr
    opportunity_id: StrictStr
    context: DecisionActionContextV2
    settings_revision: JsonObject


class _ReportBinding(_Boundary):
    mapped_ref: ObservedExecutionRef
    provider_emission_ref: ObservedExecutionRef
    synthesis_ref: DecisionSourceRefV1


class _FixedOriginals(_Boundary):
    snapshot: DecisionSnapshotV2
    revision: ActionPlanRevisionV2
    comparison: ActionComparisonV2
    target: PublicationBoundTargetV1
    cost_assumptions: JsonObject
    cost_assumptions_ref: DecisionSourceRefV1
    derivation: JsonObject


def _verify_report(
    fixed: _FixedOriginals,
    *,
    run: RunRecord,
    admission: _Admission,
    admission_ref: DecisionSourceRefV1,
    report_binding: _ReportBinding,
    report_synthesis: object,
    report_review_request: object,
) -> None:
    raw = _JSON_OBJECT.validate_python(report_synthesis)
    typed = _SYNTHESIS.validate_json(_json(raw))
    request = _REQUEST.validate_json(_json(report_review_request))
    primary = typed.primary_v2 if isinstance(typed, ResearchDecisionSynthesisV3) else typed
    primary_raw = (
        _JSON_OBJECT.validate_python(raw["primary_v2"])
        if isinstance(typed, ResearchDecisionSynthesisV3)
        else raw
    )
    primary_request = (
        request.primary_review_request if isinstance(request, ActionReviewRequestV3) else request
    )
    derivation = fixed.derivation
    expected = {
        "admission_ref": admission_ref.model_dump(mode="json"),
        "native_synthesis_ref": primary_request.synthesis_ref.model_dump(mode="json"),
        "mapped_original": report_binding.mapped_ref.model_dump(mode="json"),
        "provider_original": report_binding.provider_emission_ref.model_dump(mode="json"),
    }
    if (
        admission.owner_id != fixed.snapshot.context.owner_id
        or run.run_id != fixed.snapshot.run_id
        or admission.context != fixed.snapshot.context
        or primary.candidate_set.context != admission.context
        or primary.opportunity_id != admission.opportunity_id
        or primary_request.synthesis != primary
        or request.synthesis_ref != report_binding.synthesis_ref
        or primary_request.synthesis_ref.hash != _hash(primary_raw)
        or report_binding.synthesis_ref.hash != _hash(raw)
        or any(derivation.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("perpetual_saved_action_report_binding_mismatch")
    ComparisonCostPolicyV1.model_validate(fixed.cost_assumptions["base_cost_policy"])
    if fixed.cost_assumptions_ref.hash != _hash(fixed.cost_assumptions):
        raise ValueError("perpetual_saved_action_cost_original_mismatch")


def read_saved_perpetual_action_in_session(
    session: Session,
    *,
    run: RunRecord,
    admission: object,
    admission_ref: object,
    report_binding: object,
    report_synthesis: object,
    report_review_request: object,
    report_published_at: datetime,
    read_at: datetime,
) -> SavedActionAvailable | SavedActionUnavailable:
    """The caller's authenticated report and transaction select the only command."""
    original_admission = _JSON_OBJECT.validate_python(admission)
    header = _Admission.model_validate_json(_json(original_admission))
    artifact_id = run.artifact_id
    if artifact_id is None:
        raise ValueError("perpetual_saved_action_report_not_saved")
    command_id = "publish_" + _hash([run.run_id, header.opportunity_id])
    published = ActionRepository(session).get_command(PUBLISHER, command_id)
    if published is None:
        return SavedActionUnavailable()
    if not aware_utc(report_published_at) <= published.accepted_at <= aware_utc(read_at):
        raise ValueError("perpetual_saved_action_publication_time_mismatch")
    raw_fixed: object = read_perpetual_published_originals_in_session(
        session,
        run=run,
        admission=original_admission,
        admission_ref=_JSON_OBJECT.validate_python(admission_ref),
        published=published,
        at=read_at,
        frozen_only=True,
    )
    fixed = _FixedOriginals.model_validate_json(_json(raw_fixed))
    reference = DecisionSourceRefV1.model_validate_json(_json(admission_ref))
    binding = _ReportBinding.model_validate_json(_json(report_binding))
    _verify_report(
        fixed,
        run=run,
        admission=header,
        admission_ref=reference,
        report_binding=binding,
        report_synthesis=report_synthesis,
        report_review_request=report_review_request,
    )
    return _available(
        fixed,
        published=published,
        header=header,
        run_id=run.run_id,
        artifact_id=artifact_id,
        binding=binding,
        reference=reference,
    )


def _available(
    fixed: _FixedOriginals,
    *,
    published: StoredCommandReceipt,
    header: _Admission,
    run_id: str,
    artifact_id: str,
    binding: _ReportBinding,
    reference: DecisionSourceRefV1,
) -> SavedActionAvailable:
    wire = _JSON_OBJECT.validate_python(published.wire())
    accepted_at = wire["accepted_at"]
    if not isinstance(accepted_at, str):
        raise ValueError("perpetual_saved_action_receipt_time_invalid")
    return SavedActionAvailable(
        binding=SavedActionBinding(
            owner_id=header.owner_id,
            run_id=run_id,
            artifact_id=artifact_id,
            report_synthesis_ref=binding.synthesis_ref,
            admission_ref=reference,
        ),
        accepted_at=accepted_at,
        command_receipt=wire,
        snapshot=fixed.snapshot,
        revision=fixed.revision,
        comparison=fixed.comparison,
        target=fixed.target,
        cost_assumptions=fixed.cost_assumptions,
        cost_assumptions_ref=fixed.cost_assumptions_ref,
        derivation=fixed.derivation,
    )
