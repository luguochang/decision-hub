"""Read-only projection of the frozen finite path experiment. GET never creates work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy import func

from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import (
    RiskPathLedger,
    checked_payload,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, as_utc, utcnow
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPathJobRecord,
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.path_forecast import (
    FrozenPathForecastModel,
    PathFeatureManifest,
    PathForecastParameters,
    PathForecastParametersV1,
    RiskPathForecast,
)

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0)]
Identity = Annotated[str, Field(min_length=1)]
TrialState = Literal["registered", "running", "paused", "draining", "completed", "failed"]


class View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrialProtocol(View):
    schema_version: Literal["risk-path-shadow-trial.v1", "risk-path-shadow-trial.v2"]
    parameters: PathForecastParameters | PathForecastParametersV1
    scopes: tuple[MarketPathScope, ...]
    first_cutoff_at: AwareDatetime
    target_version: Literal["long-short-mae-2pct-5pct.v1"]
    feature_policy: Literal["known-closed-31-delay180.v1"]
    source_policy: Literal["authenticated-market-path-store.v1"]
    alarm_policy: Literal["not_registered_no_user_dispatch", "shadow-forecast-ui-receipts.v1"]
    promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def frozen_version(self):
        legacy = self.schema_version.endswith(".v1")
        if legacy != self.parameters.protocol_version.endswith(".v1") or (
            self.alarm_policy
            != ("not_registered_no_user_dispatch" if legacy else "shadow-forecast-ui-receipts.v1")
        ):
            raise ValueError("risk_path_trial_protocol_version_mismatch")
        return self


class TrialJob(View):
    job_id: Identity
    kind: Literal["fit", "forecast", "label", "finalize"]
    scope_id: str
    cutoff_at: AwareDatetime
    state: Literal["pending", "running", "succeeded", "missed", "failed", "expired"]
    attempts: Count
    error_code: str | None


class TrialDetail(View):
    trial_id: Identity
    state: TrialState
    protocol: TrialProtocol
    protocol_hash: Hash
    registered_at: AwareDatetime
    first_cutoff_at: AwareDatetime
    last_cutoff_at: AwareDatetime
    last_window_end_at: AwareDatetime
    label_deadline_at: AwareDatetime
    finished_at: AwareDatetime | None
    conclusion: Literal["insufficient_evidence"] | None
    jobs: list[TrialJob]


class TrainingReadiness(View):
    schema_version: Literal["path-training-readiness.v1"] = "path-training-readiness.v1"
    scope_id: str
    as_of: AwareDatetime
    status: Literal[
        "training_pending", "training_unavailable", "training_partial", "training_sufficient"
    ]
    fit_job_state: Literal["pending", "running", "succeeded", "missed", "failed", "expired"]
    frozen_fit: bool
    read_as_of: AwareDatetime | None
    training_cutoff_at: AwareDatetime
    requested_history_start_at: AwareDatetime | None
    accepted_windows: Count | None
    excluded_windows: Count | None
    baseline_n: Count | None
    candidate_training_n: Count | None
    low_group_n: Count | None
    high_group_n: Count | None
    minimum_training_windows: Literal[60] = 60
    minimum_group_windows: Literal[30] = 30
    baseline_sample_ready: bool
    candidate_sample_ready: bool
    available_history_start_at: AwareDatetime | None
    available_history_end_at: AwareDatetime | None
    available_candle_minutes: Count | None
    history_basis: Literal["native_ledger_metadata_at_fit_read_as_of"] = (
        "native_ledger_metadata_at_fit_read_as_of"
    )
    accepted_history_start_at: AwareDatetime | None
    accepted_history_end_at: AwareDatetime | None
    exclusion_counts: dict[str, Count]
    reasons: list[str]
    fit_result_hash: Hash | None
    model_hash: Hash | None
    probability_qualified: Literal[False] = False
    promotion_allowed: Literal[False] = False


class TrialSummary(View):
    trial_id: Identity
    state: TrialState
    scope_ids: list[Identity]
    protocol_version: Literal["path-shadow-24h.v1", "path-shadow-24h.v2"]
    shadow_days: Literal[28]
    registered_at: AwareDatetime
    first_cutoff_at: AwareDatetime
    last_cutoff_at: AwareDatetime
    last_window_end_at: AwareDatetime
    label_deadline_at: AwareDatetime
    finished_at: AwareDatetime | None
    conclusion: Literal["insufficient_evidence"] | None
    expected_slot_count: Count
    expected_prediction_count: Count
    published_prediction_count: Count
    unpublished_prediction_count: Count
    future_prediction_count: Count
    missed_prediction_count: Count
    complete_label_count: Count
    pending_label_count: Count
    unavailable_mature_label_count: Count
    job_error_count: Count
    deadline_elapsed: bool
    report_available: bool
    promotion_allowed: Literal[False]
    training_readiness: list[TrainingReadiness]


class TrialsView(View):
    schema_version: Literal["risk-path-trials.v1"] = "risk-path-trials.v1"
    as_of: AwareDatetime
    trials: list[TrialSummary]


class CurrentTrialView(View):
    schema_version: Literal["risk-path-current-trial.v1"] = "risk-path-current-trial.v1"
    scope_id: Identity
    as_of: AwareDatetime
    trial: TrialSummary | None


class TrialDetailView(View):
    schema_version: Literal["risk-path-trial-detail.v1"] = "risk-path-trial-detail.v1"
    as_of: AwareDatetime
    trial: TrialDetail
    summary: TrialSummary


class Prediction(View):
    prediction_id: Identity
    trial_id: Identity
    forecast: RiskPathForecast


class PredictionDetail(Prediction):
    feature_manifest: PathFeatureManifest


class PredictionsView(View):
    schema_version: Literal["risk-path-predictions.v1"] = "risk-path-predictions.v1"
    scope_id: Identity
    trial_id: Identity | None
    as_of: AwareDatetime
    predictions: list[Prediction]


class PredictionDetailView(View):
    schema_version: Literal["risk-path-prediction-detail.v1"] = "risk-path-prediction-detail.v1"
    as_of: AwareDatetime
    prediction: PredictionDetail


class ScoreConfidenceInterval(View):
    lower: str
    upper: str
    point_estimate: str
    confidence: Literal["0.95"]
    protocol: Literal["utc-day-paired-percentile-bootstrap.v2"]
    protocol_hash: Hash
    seed: Literal[271828]
    replicates: Literal[2000]
    independent_day_blocks: Annotated[int, Field(ge=100)]
    event_blocks: Annotated[int, Field(ge=30)]
    unit: Literal["UTC-day"]
    estimator: Literal["equal_day_mean_baseline_minus_candidate_brier"]

    @field_validator("lower", "upper", "point_estimate")
    @classmethod
    def finite_decimal(cls, value: str) -> str:
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("risk_path_ci_decimal_invalid") from error
        if not number.is_finite() or not -1 <= number <= 1:
            raise ValueError("risk_path_ci_decimal_invalid")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if Decimal(self.lower) > Decimal(self.upper):
            raise ValueError("risk_path_ci_interval_reversed")
        return self


class ScoreTarget(View):
    scope_id: Identity
    direction: Literal["long", "short"]
    threshold: Literal["0.02", "0.05"]
    primary: bool
    expected_slots: Count
    baseline_scored: Count
    candidate_scored: Count
    paired_scored: Count
    baseline_brier: str | None
    candidate_brier: str | None
    paired_baseline_brier: str | None
    paired_candidate_brier: str | None
    paired_delta: str | None
    independent_day_blocks: Count
    ci: ScoreConfidenceInterval | None
    ci_status: Literal["not_registered", "insufficient", "available"]

    @model_validator(mode="after")
    def confidence_status(self):
        if (self.ci is not None) != (self.ci_status == "available"):
            raise ValueError("risk_path_ci_status_mismatch")
        return self

    @field_validator(
        "baseline_brier",
        "candidate_brier",
        "paired_baseline_brier",
        "paired_candidate_brier",
        "paired_delta",
    )
    @classmethod
    def finite_score(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None:
            try:
                number = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("risk_path_score_decimal_invalid") from error
            minimum = -1 if info.field_name == "paired_delta" else 0
            if not number.is_finite() or not minimum <= number <= 1:
                raise ValueError("risk_path_score_decimal_invalid")
        return value


class ScoreTargetV2(ScoreTarget):
    ci_reasons: list[Identity]


class ScoreInference(View):
    status: Literal["available", "insufficient"]
    ci: ScoreConfidenceInterval | None
    protocol: Literal["utc-day-paired-percentile-bootstrap.v2"]
    protocol_hash: Hash
    confidence: Literal["0.95"]
    seed: Literal[271828]
    replicates: Literal[2000]
    unit: Literal["UTC-day"]
    estimator: Literal["equal_day_mean_baseline_minus_candidate_brier"]
    expected_pairs: Count
    complete_pairs: Count
    independent_day_blocks: Count
    event_blocks: Count | None
    reasons: list[Identity]
    promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def inference_status(self):
        if (self.ci is not None) != (self.status == "available") or (
            self.complete_pairs > self.expected_pairs
        ):
            raise ValueError("risk_path_inference_status_mismatch")
        if self.ci and (
            self.ci.protocol_hash != self.protocol_hash
            or self.ci.independent_day_blocks != self.independent_day_blocks
            or self.ci.event_blocks != self.event_blocks
            or self.complete_pairs != self.expected_pairs
            or self.reasons
        ):
            raise ValueError("risk_path_inference_originals_mismatch")
        return self


class ScoreExclusion(View):
    scope_id: Identity
    cutoff_at: AwareDatetime
    direction: Literal["long", "short"]
    threshold: Literal["0.02", "0.05"]
    reasons: list[Identity]


class AlarmLeadTime(View):
    matched_count: Annotated[int, Field(ge=1)]
    minimum_seconds: str
    median_seconds: str
    resolution_seconds: Literal[60]
    conservative: Literal[True]

    @field_validator("minimum_seconds", "median_seconds")
    @classmethod
    def finite_seconds(cls, value: str) -> str:
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("risk_path_alarm_lead_decimal_invalid") from error
        if not number.is_finite() or number < 0:
            raise ValueError("risk_path_alarm_lead_decimal_invalid")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if Decimal(self.minimum_seconds) > Decimal(self.median_seconds):
            raise ValueError("risk_path_alarm_lead_reversed")
        return self


class AlarmMetrics(View):
    precision: str | None
    recall: str | None
    false_episodes_per_asset_day: str | None
    lead_time: AlarmLeadTime | None
    status: Literal["evaluated", "partial", "unknown"]
    reason: Literal[
        "forecast_alarm_and_authenticated_delivery_exposure_not_registered",
        "matched_forecast_alarm_originals",
        "mature_alarm_or_label_unavailable",
    ]

    @field_validator("precision", "recall", "false_episodes_per_asset_day")
    @classmethod
    def finite_metric(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None:
            try:
                number = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("risk_path_alarm_metric_invalid") from error
            if (
                not number.is_finite()
                or number < 0
                or (info.field_name != "false_episodes_per_asset_day" and number > 1)
            ):
                raise ValueError("risk_path_alarm_metric_invalid")
        return value


class PathScoreV1(View):
    schema_version: Literal["path-shadow-score.v1"]
    expected_slot_count: Count
    expected_prediction_count: Count
    published_prediction_count: Count
    unpublished_prediction_count: Count
    on_time_prediction_count: Count
    late_prediction_count: Count
    complete_label_count: Count
    partial_label_count: Count
    unavailable_label_count: Count
    independent_paired_day_blocks: Count
    observed_primary_event_day_blocks: Count
    independent_event_blocks: Count | None
    minimum_time_blocks: Literal[100]
    minimum_event_blocks: Literal[30]
    targets: list[ScoreTarget]
    exclusion_count_basis: Literal["target_evaluations"]
    exclusions: list[ScoreExclusion]
    exclusion_counts: dict[str, Count]
    alarm_metrics: AlarmMetrics
    ci: ScoreConfidenceInterval | None
    ci_status: Literal["not_registered", "insufficient", "available"]
    promotion_allowed: Literal[False]
    conclusion: Literal["insufficient_evidence"]
    reasons: list[Identity]

    @model_validator(mode="after")
    def frozen_version(self):
        legacy = self.schema_version == "path-shadow-score.v1"
        statuses = [self.ci_status, *(target.ci_status for target in self.targets)]
        if (
            (self.ci is not None) != (self.ci_status == "available")
            or (
                legacy
                and (
                    any(status != "not_registered" for status in statuses)
                    or self.independent_event_blocks is not None
                )
            )
            or (not legacy and "not_registered" in statuses)
        ):
            raise ValueError("risk_path_score_version_ci_mismatch")
        return self


class AlarmOriginalReference(View):
    original_ref: Identity
    original_hash: Hash


class PathScoreV2(PathScoreV1):
    schema_version: Literal["path-shadow-score.v2"]
    targets: list[ScoreTargetV2]
    inference: ScoreInference
    alarm_originals: list[AlarmOriginalReference]

    @model_validator(mode="after")
    def inference_binding(self):
        if self.ci != self.inference.ci or self.ci_status != self.inference.status:
            raise ValueError("risk_path_score_inference_binding_changed")
        return self


PathScore = Annotated[PathScoreV1 | PathScoreV2, Field(discriminator="schema_version")]


class PredictionReference(View):
    prediction_id: Identity
    payload_hash: Hash


class ObservationReference(View):
    observation_id: Identity
    payload_hash: Hash
    complete: bool


class ScoreReportV1(View):
    report_id: Identity
    trial_id: Identity
    report_version: Literal["path-shadow-evaluation.v1"]
    computed_at: AwareDatetime
    protocol_hash: Hash
    score: PathScoreV1
    prediction_refs: list[PredictionReference]
    observation_refs: list[ObservationReference]
    payload_hash: Hash

    @model_validator(mode="after")
    def same_version(self):
        if self.report_version.rsplit(".", 1)[-1] != self.score.schema_version.rsplit(".", 1)[-1]:
            raise ValueError("risk_path_report_score_version_mismatch")
        return self


class ScoreReportV2(ScoreReportV1):
    report_version: Literal["path-shadow-evaluation.v2"]
    score: PathScoreV2
    alarm_score_original: AlarmOriginalReference | None


ScoreReport = Annotated[ScoreReportV1 | ScoreReportV2, Field(discriminator="report_version")]
SCORE_REPORT_ADAPTER = TypeAdapter(ScoreReport)


class ScoreView(View):
    schema_version: Literal["risk-path-score-view.v1"] = "risk-path-score-view.v1"
    as_of: AwareDatetime
    trial: TrialSummary
    report: ScoreReport | None
    receipt_verification: Literal["stored_payload_hash"] = "stored_payload_hash"


def risk_paths_router(
    database: Database,
    path_store: MarketPathStore,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> APIRouter:
    """Use the already-registered ledger tables; no external source/model calls."""
    # The score reader can authenticate one stored review. GET never invokes the
    # write-side score producer; its presence is the normal ledger boundary.
    from packages.kernel.decision_hub_kernel.application.forecast_alarm import ForecastAlarmService

    router = APIRouter()
    alarms = ForecastAlarmService(database, path_store, clock=clock)
    ledger = RiskPathLedger(
        database,
        path_store,
        clock=clock,
        forecast_alarm_score_reader=alarms.score_in_session,
        forecast_alarm_original_reader=alarms.read_score_in_session,
    )
    allowed = {spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT")}

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, "risk_path_object_not_found") from error
        except ValueError as error:
            raise HTTPException(409, "risk_path_receipt_unverified") from error

    def scope(identity: str) -> str:
        if identity not in allowed:
            raise HTTPException(404, "risk_path_scope_not_found")
        return identity

    def training(detail: TrialDetail, now: datetime) -> list[TrainingReadiness]:
        """Read immutable fit receipts; candle extent is metadata, not PIT qualification."""
        values = []
        with database.session() as session:
            jobs = session.query(RiskPathJobRecord).filter_by(
                trial_id=detail.trial_id, kind="fit"
            ).all()
            by_scope = {job.scope_id: job for job in jobs}
            if len(by_scope) != len(jobs):
                raise ValueError("risk_path_fit_identity_invalid")
            for registered_scope in detail.protocol.scopes:
                job = by_scope.get(registered_scope.scope_id)
                if job is None or as_utc(job.cutoff_at) != detail.first_cutoff_at:
                    raise ValueError("risk_path_fit_identity_invalid")
                common: dict[str, object] = {
                    "scope_id": registered_scope.scope_id, "as_of": now,
                    "fit_job_state": job.state, "training_cutoff_at": detail.first_cutoff_at,
                    "frozen_fit": False, "read_as_of": None,
                    "requested_history_start_at": None, "accepted_windows": None,
                    "excluded_windows": None, "baseline_n": None,
                    "candidate_training_n": None, "low_group_n": None, "high_group_n": None,
                    "baseline_sample_ready": False, "candidate_sample_ready": False,
                    "available_history_start_at": None, "available_history_end_at": None,
                    "available_candle_minutes": None, "accepted_history_start_at": None,
                    "accepted_history_end_at": None, "exclusion_counts": {},
                    "fit_result_hash": None, "model_hash": None,
                }
                if job.state != "succeeded":
                    values.append(TrainingReadiness.model_validate({
                        **common,
                        "status": ("training_pending" if job.state in {"pending", "running"}
                                   else "training_unavailable"),
                        "reasons": ["frozen_fit_receipt_unavailable"],
                    }))
                    continue
                if job.result_json is None or job.result_hash is None:
                    raise ValueError("risk_path_fit_receipt_missing")
                result = checked_payload(job.result_json, job.result_hash)
                model = FrozenPathForecastModel.model_validate(result["model"])
                read_at = aware_utc(datetime.fromisoformat(result["read_as_of"]))
                if (
                    read_at > now or job.completed_at is None
                    or read_at > as_utc(job.completed_at)
                    or read_at > model.training_cutoff_at
                    or model.training_cutoff_at != as_utc(job.cutoff_at)
                    or model.scope != registered_scope
                    or model.parameters != detail.protocol.parameters
                    or canonical_hash(model.model_dump(mode="json", exclude={"model_hash"}))
                    != model.model_hash
                ):
                    raise ValueError("risk_path_fit_receipt_binding_invalid")
                # Never count candles acquired after the saved fit read, even if their
                # market timestamps predate it. Extent does not imply continuous bars.
                start, end, minutes = session.query(
                    func.min(MarketCandleRecord.open_at), func.max(MarketCandleRecord.close_at),
                    func.count(func.distinct(MarketCandleRecord.open_at)),
                ).filter(
                    MarketCandleRecord.scope_id == registered_scope.scope_id,
                    MarketCandleRecord.open_at >= model.history_start_at,
                    MarketCandleRecord.close_at <= read_at,
                    MarketCandleRecord.known_at <= read_at,
                    MarketCandleRecord.received_at <= read_at,
                ).one()
                baseline_ready = model.baseline.n >= 60
                candidate_ready = (
                    model.candidate_training_n >= 60 and model.conditional_low.n >= 30
                    and model.conditional_high.n >= 30 and model.training_vol_median is not None
                )
                reasons = []
                if not baseline_ready:
                    reasons.append("baseline_training_windows_insufficient")
                if model.candidate_training_n < 60:
                    reasons.append("candidate_training_windows_insufficient")
                if model.conditional_low.n < 30:
                    reasons.append("low_volatility_group_insufficient")
                if model.conditional_high.n < 30:
                    reasons.append("high_volatility_group_insufficient")
                exclusion_counts: dict[str, int] = {}
                for _, exclusions in model.excluded:
                    for reason in exclusions:
                        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                common.update(
                    frozen_fit=True, read_as_of=read_at,
                    requested_history_start_at=model.history_start_at,
                    accepted_windows=len(model.accepted), excluded_windows=len(model.excluded),
                    baseline_n=model.baseline.n, candidate_training_n=model.candidate_training_n,
                    low_group_n=model.conditional_low.n, high_group_n=model.conditional_high.n,
                    baseline_sample_ready=baseline_ready, candidate_sample_ready=candidate_ready,
                    available_history_start_at=as_utc(start), available_history_end_at=as_utc(end),
                    available_candle_minutes=minutes,
                    accepted_history_start_at=min(
                        (item.path.window_start_at for item in model.accepted), default=None
                    ),
                    accepted_history_end_at=max(
                        (item.path.window_end_at for item in model.accepted), default=None
                    ),
                    exclusion_counts=exclusion_counts,
                    fit_result_hash=job.result_hash, model_hash=model.model_hash,
                )
                values.append(TrainingReadiness.model_validate({
                    **common,
                    "status": (
                        "training_sufficient" if baseline_ready and candidate_ready
                        else "training_partial" if baseline_ready else "training_unavailable"
                    ),
                    "reasons": reasons,
                }))
        return values

    def summary(detail: TrialDetail, now: datetime) -> TrialSummary:
        forecasts = [job for job in detail.jobs if job.kind == "forecast"]
        labels = [job for job in detail.jobs if job.kind == "label"]
        published = [
            Prediction.model_validate(item)
            for identity in (item.scope_id for item in detail.protocol.scopes)
            for item in ledger.predictions(identity, trial_id=detail.trial_id, limit=200)
        ]
        by_slot: dict[tuple[str, datetime], int] = {}
        for item in published:
            key = item.forecast.scope.scope_id, item.forecast.cutoff_at
            by_slot[key] = by_slot.get(key, 0) + 1
        future = sum(
            2 - by_slot.get((job.scope_id, job.cutoff_at), 0)
            for job in forecasts
            if now <= job.cutoff_at + timedelta(minutes=1)
        )
        expected = 2 * len(forecasts)
        report = ledger.score(detail.trial_id)
        return TrialSummary(
            trial_id=detail.trial_id,
            state=detail.state,
            scope_ids=[item.scope_id for item in detail.protocol.scopes],
            protocol_version=detail.protocol.parameters.protocol_version,
            shadow_days=28,
            registered_at=detail.registered_at,
            first_cutoff_at=detail.first_cutoff_at,
            last_cutoff_at=detail.last_cutoff_at,
            last_window_end_at=detail.last_window_end_at,
            label_deadline_at=detail.label_deadline_at,
            finished_at=detail.finished_at,
            conclusion=detail.conclusion,
            expected_slot_count=len(forecasts),
            expected_prediction_count=expected,
            published_prediction_count=len(published),
            unpublished_prediction_count=expected - len(published),
            future_prediction_count=future,
            missed_prediction_count=expected - len(published) - future,
            complete_label_count=sum(job.state == "succeeded" for job in labels),
            pending_label_count=sum(
                now < job.cutoff_at + timedelta(days=1, minutes=1) for job in labels
            ),
            unavailable_mature_label_count=sum(
                now >= job.cutoff_at + timedelta(days=1, minutes=1) and job.state != "succeeded"
                for job in labels
            ),
            job_error_count=sum(bool(job.error_code) for job in detail.jobs),
            deadline_elapsed=now >= detail.label_deadline_at,
            report_available=report is not None,
            promotion_allowed=False,
            training_readiness=training(detail, now),
        )

    def detail(identity: str) -> TrialDetail:
        return TrialDetail.model_validate(ledger.trial(identity))

    def trials(limit: int, identity: str | None, *, active_only: bool = False) -> list[TrialDetail]:
        with database.session() as session:
            query = session.query(RiskShadowTrialRecord)
            if identity is not None:
                # Producer writes canonical JSON; scope identity is an exact quoted value.
                query = query.filter(RiskShadowTrialRecord.protocol_json.contains(f'"{identity}"'))
            if active_only:
                query = query.filter(RiskShadowTrialRecord.state.notin_(("completed", "failed")))
            ids = [
                row.trial_id
                for row in query.order_by(
                    RiskShadowTrialRecord.registered_at.desc(),
                    RiskShadowTrialRecord.trial_id,
                )
                .limit(limit)
                .all()
            ]
        values = []
        for trial_id in ids:
            value = detail(trial_id)
            if identity is None or identity in {item.scope_id for item in value.protocol.scopes}:
                values.append(value)
                if len(values) >= limit:
                    break
        return values

    @router.get("/v1/risk/path-trials", response_model=TrialsView)
    def trial_list(limit: int = Query(default=50, ge=1, le=100), scope_id: str | None = None):
        identity = scope(scope_id) if scope_id else None
        now = ledger.now()
        return call(
            lambda: TrialsView(
                as_of=now,
                trials=[summary(item, now) for item in trials(limit, identity)],
            )
        )

    @router.get("/v1/risk/scopes/{scope_id}/path-trial", response_model=CurrentTrialView)
    def current_trial(scope_id: str):
        scope(scope_id)
        now = ledger.now()

        def read():
            values = trials(1, scope_id, active_only=True) or trials(1, scope_id)
            current = values[0] if values else None
            return CurrentTrialView(
                scope_id=scope_id,
                as_of=now,
                trial=summary(current, now) if current is not None else None,
            )

        return call(read)

    @router.get("/v1/risk/path-trials/{trial_id}", response_model=TrialDetailView)
    def trial_detail(trial_id: str):
        now = ledger.now()

        def read():
            item = detail(trial_id)
            return TrialDetailView(as_of=now, trial=item, summary=summary(item, now))

        return call(read)

    @router.get("/v1/risk/scopes/{scope_id}/predictions", response_model=PredictionsView)
    def predictions(
        scope_id: str,
        trial_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        scope(scope_id)

        def read():
            if trial_id is not None and scope_id not in {
                item.scope_id for item in detail(trial_id).protocol.scopes
            }:
                raise KeyError(trial_id)
            return PredictionsView(
                scope_id=scope_id,
                trial_id=trial_id,
                as_of=ledger.now(),
                predictions=list(ledger.predictions(scope_id, trial_id=trial_id, limit=limit)),
            )

        return call(read)

    @router.get("/v1/risk/predictions/{prediction_id}", response_model=PredictionDetailView)
    def prediction_detail(prediction_id: str):
        return call(
            lambda: PredictionDetailView(
                as_of=ledger.now(),
                prediction=ledger.prediction(prediction_id),
            )
        )

    @router.get("/v1/risk/path-trials/{trial_id}/score", response_model=ScoreView)
    def score(trial_id: str):
        now = ledger.now()

        def read():
            trial = detail(trial_id)
            raw = ledger.score(trial_id)
            report = SCORE_REPORT_ADAPTER.validate_python(raw) if raw is not None else None
            if raw is not None and raw["report_version"] == "path-shadow-evaluation.v2":
                ledger.read_report_alarm_score(raw)
            if report is not None and (
                report.trial_id != trial_id
                or report.protocol_hash != trial.protocol_hash
                or report.report_version.rsplit(".", 1)[-1]
                != trial.protocol.parameters.protocol_version.rsplit(".", 1)[-1]
                or report.computed_at > now
                or trial.state != "completed"
                or report.computed_at < trial.label_deadline_at
            ):
                raise ValueError("risk_path_score_identity_changed")
            return ScoreView(as_of=now, trial=summary(trial, now), report=report)

        return call(read)

    return router
