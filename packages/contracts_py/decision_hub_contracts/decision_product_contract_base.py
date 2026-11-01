"""Canonical action wire validation; no network, persistence, or policy authority."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    path = Path(__file__).resolve().parents[3] / "contracts/schemas/decision_product_v1.schema.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        definition["title"]: Draft202012Validator(
            {"$defs": document["$defs"], "$ref": f"#/$defs/{name}"},
            format_checker=FormatChecker(),
        )
        for name, definition in document["$defs"].items()
    }


def _wire(value: object) -> object:
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_timezone_required")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("decision_non_finite_number")
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


class DecisionProductContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value: object) -> object:
        validator = _validators().get(cls.__name__)
        if validator is None:
            raise ValueError("decision_canonical_definition_required")
        error = next(validator.iter_errors(_wire(value)), None)
        if error is not None:
            path = ".".join(str(item) for item in error.absolute_path)
            raise ValueError(f"decision_shape_invalid:{error.validator}:{path}")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def normalized_utc(cls, value: object) -> object:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("decision_timezone_required")
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def relations(self):
        kind = type(self).__name__
        if kind == "DecisionOpportunityV1" and self.time_start >= self.time_end:
            raise ValueError("decision_opportunity_window_invalid")
        if kind == "DecisionSnapshotV1":
            if self.known_at < self.decision_cutoff_at:
                raise ValueError("decision_snapshot_known_before_cutoff")
            if any(f.known_at > self.decision_cutoff_at for f in self.factor_refs):
                raise ValueError("decision_factor_after_cutoff")
        if kind == "DecisionSizePolicyV1":
            if self.kind == "unspecified":
                if self.amount is not None or self.unit is not None:
                    raise ValueError("decision_unspecified_size_must_be_null")
            elif self.amount is None or self.unit is None or Decimal(self.amount) <= 0:
                raise ValueError("decision_size_amount_unit_required")
        if kind == "DecisionActionPlanRevisionV1":
            if not self.as_of <= self.published_at < self.valid_until:
                raise ValueError("decision_plan_window_invalid")
            if (self.revision_no == 1) != (self.previous_id is None):
                raise ValueError("decision_plan_previous_revision_invalid")
            conditions = [*self.entry, *self.exit, *self.invalidation]
            if len({item.id for item in conditions}) != len(conditions):
                raise ValueError("decision_condition_duplicate_id")
            for items, purposes in (
                (self.entry, {"entry"}),
                (self.exit, {"exit", "risk_upgrade"}),
                (self.invalidation, {"invalidation"}),
            ):
                if any(
                    item.purpose not in purposes
                    or not self.as_of < item.valid_until <= self.valid_until
                    for item in items
                ):
                    raise ValueError("decision_condition_binding_invalid")
        if kind in {
            "DecisionConditionEvaluationV1",
            "DecisionPerConditionResultV1",
            "DecisionConditionEvaluationV2",
            "DecisionPerConditionResultV2",
        }:
            if self.status == "error" and self.truth != "unknown":
                raise ValueError("decision_evaluation_error_requires_unknown")
        if kind in {"DecisionConditionEvaluationV1", "DecisionConditionEvaluationV2"}:
            if self.as_of > self.evaluated_at or self.tick_at > self.evaluated_at:
                raise ValueError("decision_evaluation_time_invalid")
        if kind == "DecisionConditionEvaluationV2":
            if self.state_version_after != self.state_version_before + 1:
                raise ValueError("decision_evaluation_version_invalid")
            if len({item.id for item in self.per_condition}) != len(self.per_condition):
                raise ValueError("decision_evaluation_duplicate_condition")
            if any(
                item.last_bar_at is not None and item.last_bar_at > self.as_of
                for item in self.per_condition
            ):
                raise ValueError("decision_evaluation_future_bar")
        if kind == "DecisionPerConditionResultV2":
            if self.status == "error" and self.raw_truth != "unknown":
                raise ValueError("decision_evaluation_error_requires_unknown")
            if self.confirmation_count > 0 and self.last_bar_at is None:
                raise ValueError("decision_confirmation_watermark_required")
            if self.raw_truth != "true" and (self.confirmation_count or self.confirmed):
                raise ValueError("decision_confirmation_requires_true")
            if self.confirmed != (self.truth == "true"):
                raise ValueError("decision_confirmation_truth_mismatch")
            if self.confirmed and self.confirmation_count < 1:
                raise ValueError("decision_confirmation_count_required")
        if kind == "DecisionActionPlanHeadV1":
            if (self.lease_owner is None) != (self.lease_expires_at is None):
                raise ValueError("decision_head_lease_pair_invalid")
        if kind == "DecisionActionOutcomeV1":
            if not Decimal("0") <= Decimal(self.coverage) <= Decimal("1"):
                raise ValueError("decision_outcome_coverage_invalid")
            if any(value is not None and Decimal(value) <= 0 for value in (self.entry, self.exit)):
                raise ValueError("decision_outcome_price_invalid")
            if self.cost is not None and Decimal(self.cost) < 0:
                raise ValueError("decision_outcome_cost_invalid")
            if (self.status == "settled") != (self.settled_at is not None):
                raise ValueError("decision_outcome_settlement_time_invalid")
            if (
                self.entry_at is not None
                and self.exit_at is not None
                and self.exit_at < self.entry_at
            ):
                raise ValueError("decision_outcome_exit_before_entry")
        if kind == "DecisionUiBranchV2":
            revision, decision = self.revision, self.decision
            if (
                revision.decision_id != decision.id
                or revision.opportunity_id != decision.opportunity_id
                or (revision.scope_id, revision.horizon) != (decision.scope_id, decision.horizon)
                or self.head.plan_id != revision.plan_id
                or (
                    self.latest_evaluation is not None
                    and (
                        self.latest_evaluation.plan_id != revision.plan_id
                        or self.latest_evaluation.revision_id != revision.revision_id
                    )
                )
            ):
                raise ValueError("decision_ui_branch_basis_mismatch")
        if kind == "DecisionUiCurrentViewV2":
            pair = (self.scope_id, self.horizon)
            if (
                self.still_tracking_count < len(self.still_tracking)
                or len({item.plan_id for item in self.still_tracking}) != len(self.still_tracking)
                or (
                    self.latest_opportunity is not None
                    and (self.latest_opportunity.scope_id, self.latest_opportunity.horizon) != pair
                )
                or (
                    self.decision is not None
                    and (self.decision.scope_id, self.decision.horizon) != pair
                )
                or (self.branches and self.decision is None)
                or len({item.revision.position_context for item in self.branches})
                != len(self.branches)
            ):
                raise ValueError("decision_ui_current_identity_mismatch")
            for branch in self.branches:
                if (
                    (branch.decision.scope_id, branch.decision.horizon) != pair
                    or branch.decision.opportunity_id != self.decision.opportunity_id
                    or branch.decision.known_at > self.as_of
                    or branch.revision.published_at > self.as_of
                ):
                    raise ValueError("decision_ui_current_branch_mismatch")
        return self
