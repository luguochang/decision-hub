"""Private immutable probability inputs; a typed value alone grants no authority."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from packages.evals.forecast_calibration import (
    ForecastCalibrationPolicy,
    ForecastCalibrationReport,
    ForecastCalibrationSample,
)

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Horizon = Literal["30m", "24h", "72h"]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_COHORT = 512


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calibration_clock_timezone_required")
    return value.astimezone(UTC)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AIProbabilityScope(_Closed):
    market: Identifier
    instrument: Identifier
    method_family: Literal["ai_synthesis"] = "ai_synthesis"
    horizon: Horizon
    stable_recipe_hash: Digest
    strategy_version: Identifier
    pack_definition_hash: Digest
    compiled_policy_recipe_hash: Digest
    method_recipe_hash: Digest
    graph_recipe_hash: Digest
    input_definition: Literal["authenticated_graph_subjective_probability.v2"] = "authenticated_graph_subjective_probability.v2"
    target: Literal["own_action_correct"] = "own_action_correct"


class CostPolicyReference(_Closed):
    """Explicit official captured cost model, never default zero or executed-PnL proof."""
    capture_id: Identifier
    body_hash: Digest
    body_ref: Identifier
    capture_row_hash: Digest
    approved_host: Identifier
    fee_json_pointer: Annotated[str, Field(min_length=1, max_length=512)]
    slippage_json_pointer: Annotated[str, Field(min_length=1, max_length=512)]
    percent_scale: Literal[0.0001, 0.01, 1.0, 100.0]
    approval_ref: Identifier


class CalibrationEvaluationPolicy(_Closed):
    version: Literal["own-action-retain-or-abstain.v2"] = "own-action-retain-or-abstain.v2"
    thresholds: ForecastCalibrationPolicy = Field(default_factory=ForecastCalibrationPolicy)
    retain_threshold: Probability = 0.5
    cost: CostPolicyReference | None = None
    regime_rule: Literal["authenticated-sma20-sma60.v1"] = "authenticated-sma20-sma60.v1"
    purpose: Literal["prospective_research_calibration", "observational_diagnostic"]

    @model_validator(mode="after")
    def prospective_gates(self) -> CalibrationEvaluationPolicy:
        p = self.thresholds
        if self.purpose == "prospective_research_calibration" and (
            p.min_samples < 100 or p.n_splits < 5 or p.gap_samples < 1
            or p.min_accuracy < 0.60 or p.min_accuracy_lower_bound < 0.50
            or p.min_coverage_ratio < 0.60 or p.min_net_return_pct < 0
            or p.max_drawdown_pct > 20 or p.max_regime_share > 0.70
            or self.retain_threshold != 0.5
        ):
            raise ValueError("calibration_prospective_thresholds_weakened")
        return self


class CalibrationPlan(_Closed):
    job_id: Identifier
    request_id: Identifier
    scope: AIProbabilityScope
    policy: CalibrationEvaluationPolicy
    registered_at: AwareDatetime
    start_at: AwareDatetime
    end_at: AwareDatetime
    approval_source_ref: Identifier

    @model_validator(mode="after")
    def interval(self) -> CalibrationPlan:
        if not self.start_at < self.end_at:
            raise ValueError("calibration_plan_interval_invalid")
        if self.policy.purpose == "prospective_research_calibration" and self.start_at < self.registered_at:
            raise ValueError("calibration_plan_not_preregistered")
        return self


class CohortOriginal(_Closed):
    outlook_id: Identifier
    row_hash: Digest
    row: dict[str, Any]
    selection: Literal["selected", "repeat", "unchecked", "unkeyed"]
    assessment_json: str
    assessment_hash: Digest
    basis_json: str
    basis_hash: Digest
    included: StrictBool
    reasons: tuple[str, ...]


class AuthenticatedCalibrationSample(_Closed):
    sample: ForecastCalibrationSample
    outlook_id: Identifier
    run_id: Identifier
    graph_ref: dict[str, Any]
    compiled_policy_hash: Digest
    observation_original: dict[str, Any]
    attempt_original: dict[str, Any]
    selected_probability: Probability | None
    source_refs: tuple[dict[str, Any], ...]
    assessment_hash: Digest
    basis_hash: Digest
    label_known_at: AwareDatetime
    label: dict[str, Any]
    frozen_input: dict[str, Any]
    cost_original: dict[str, Any]
    regime_original: dict[str, Any]

    @model_validator(mode="after")
    def sample_clock(self) -> AuthenticatedCalibrationSample:
        start = aware_utc(self.sample.as_of)
        if self.sample.outcome_at is None or aware_utc(self.sample.outcome_at) != self.label_known_at:
            raise ValueError("calibration_label_known_time_invalid")
        if start >= self.label_known_at:
            raise ValueError("calibration_label_before_prediction")
        if self.sample.sample_id != self.outlook_id:
            raise ValueError("calibration_sample_identity_invalid")
        if not all(math.isfinite(value) for value in (
            self.sample.raw_probability, self.sample.realized_return_pct, self.sample.trading_cost_pct,
        )):
            raise ValueError("calibration_sample_nonfinite")
        return self


class FrozenCalibrationInputs(_Closed):
    schema_version: Literal["authenticated-calibration-inputs.v2"] = "authenticated-calibration-inputs.v2"
    plan: CalibrationPlan
    captured_at: AwareDatetime
    cohort: Annotated[tuple[CohortOriginal, ...], Field(max_length=MAX_COHORT)]
    samples: Annotated[tuple[AuthenticatedCalibrationSample, ...], Field(max_length=MAX_COHORT)]
    reasons: tuple[str, ...]
    next_boundary_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def bounded(self) -> FrozenCalibrationInputs:
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("calibration_inputs_byte_limit")
        if len({item.outlook_id for item in self.cohort}) != len(self.cohort):
            raise ValueError("calibration_cohort_duplicate")
        originals = {item.outlook_id: item for item in self.cohort}
        if len({item.outlook_id for item in self.samples}) != len(self.samples):
            raise ValueError("calibration_samples_duplicate")
        for item in self.samples:
            original = originals.get(item.outlook_id)
            if (original is None or not original.included or original.selection != "selected"
                    or original.assessment_hash != item.assessment_hash or original.basis_hash != item.basis_hash
                    or item.label_known_at > self.captured_at):
                raise ValueError("calibration_sample_original_mismatch")
        return self


class CalibrationProducerIdentity(_Closed):
    schema_version: Literal["calibration-producer-identity.v2"] = "calibration-producer-identity.v2"
    producer_version: Literal["authenticated-kernel-calibration.v2"] = "authenticated-kernel-calibration.v2"
    math_version: Literal["own-action-retain-or-abstain.v2"] = "own-action-retain-or-abstain.v2"
    code_files: dict[str, Digest]
    dependencies: dict[str, str]
    loaded_abi: dict[str, Digest]
    assessment_manifest_hash: Digest


class FrozenCalibrationProduction(_Closed):
    schema_version: Literal["strategy-calibration-production.v2"] = "strategy-calibration-production.v2"
    production_id: Identifier
    job_id: Identifier
    scope: AIProbabilityScope
    captured_at: AwareDatetime
    server_known_at: AwareDatetime
    inputs: FrozenCalibrationInputs
    inputs_hash: Digest
    producer: CalibrationProducerIdentity
    producer_hash: Digest
    report: ForecastCalibrationReport | None
    report_hash: Digest | None
    state: Literal["passed", "failed", "waiting"]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def originals(self) -> FrozenCalibrationProduction:
        if (self.job_id != self.inputs.plan.job_id or self.scope != self.inputs.plan.scope
                or self.captured_at != self.inputs.captured_at or self.captured_at > self.server_known_at
                or self.inputs_hash != content_hash(self.inputs.model_dump(mode="json"))
                or self.producer_hash != content_hash(self.producer.model_dump(mode="json"))):
            raise ValueError("calibration_production_original_invalid")
        if self.report is None:
            if self.report_hash is not None or self.state != "waiting":
                raise ValueError("calibration_production_report_missing")
        elif (self.report_hash != content_hash(self.report.model_dump(mode="json"))
              or self.report.strategy_version != self.scope.strategy_version
              or self.report.horizon != self.scope.horizon
              or aware_utc(self.report.evaluated_at) > self.server_known_at
              or self.report.passed != (self.state == "passed")):
            raise ValueError("calibration_production_report_invalid")
        if self.state == "passed" and (self.reasons or self.inputs.reasons
                or self.inputs.plan.policy.purpose != "prospective_research_calibration"):
            raise ValueError("calibration_production_unqualified_pass")
        return self


class CalibrationProductionReference(_Closed):
    horizon: Horizon
    production_id: Identifier
    payload_hash: Digest
    scope_hash: Digest
    inputs_hash: Digest
    report_hash: Digest
    producer_hash: Digest


class ProbabilityActivation(_Closed):
    schema_version: Literal["probability-activation.v2"] = "probability-activation.v2"
    activation_id: Identifier
    request_id: Identifier
    scope_market: Identifier
    scope_instrument: Identifier
    strategy_version: Identifier
    stable_recipe_hash: Digest
    pack_definition_hash: Digest
    compiled_policy_recipe_hash: Digest
    references: Annotated[tuple[CalibrationProductionReference, ...], Field(min_length=1, max_length=3)]
    server_known_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    approval_source_ref: Identifier

    @model_validator(mode="after")
    def valid(self) -> ProbabilityActivation:
        if not self.server_known_at <= self.valid_from < self.valid_until:
            raise ValueError("probability_activation_interval_invalid")
        if len({item.horizon for item in self.references}) != len(self.references):
            raise ValueError("probability_activation_horizon_duplicate")
        return self


class KernelCalibratedValue(_Closed):
    schema_version: Literal["kernel-calibrated-value.v2"] = "kernel-calibrated-value.v2"
    production_id: Identifier
    production_payload_hash: Digest
    scope_hash: Digest
    report_hash: Digest
    math_version: Literal["own-action-retain-or-abstain.v2"]
    math_identity_hash: Digest
    input_definition: Literal["authenticated_graph_subjective_probability.v2"] = "authenticated_graph_subjective_probability.v2"
    raw_probability: Probability
    calibrated_probability: Probability
    retain_threshold: Probability
    target: Literal["own_action_correct"] = "own_action_correct"


def stable_compiled_policy_recipe(compiled: Any) -> dict[str, Any]:
    """Derive recipe content after an original reader authenticates the compiled row.

    The complete compiled digest is still checked here and retained per case.
    Only run-specific scope/requirements and their applicability outcomes are
    removed from the reusable recipe; definitions/resources/code remain exact.
    This pure function cannot authenticate a DB row or approve an activation.
    """
    from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
        ResearchCompiledPolicy,
    )
    value = compiled.model_dump(mode="json") if isinstance(compiled, BaseModel) else compiled
    parsed = ResearchCompiledPolicy.model_validate(value)
    body = parsed.model_dump(mode="json")
    digest = body.pop("compiled_hash")
    if content_hash(body) != digest:
        raise ValueError("calibration_compiled_original_hash_invalid")
    body.pop("scope_hash")
    body.pop("requirements_hash")
    body["compiled_rules"] = [
        {"definition": item["definition"], "declaration_source": item["declaration_source"]}
        for item in body["compiled_rules"]
    ]
    return {"schema_version": "stable-compiled-policy-recipe.v2", "policy": body}


def stable_compiled_policy_recipe_hash(compiled: Any) -> str:
    return content_hash(stable_compiled_policy_recipe(compiled))
