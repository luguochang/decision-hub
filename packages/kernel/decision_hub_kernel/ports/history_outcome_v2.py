"""Independent prospective hourly evaluation; never an existing trial or fit record."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .market_path import FrozenContract, aware_utc, canonical_hash, spot_scope

SCOPES = tuple(spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT"))
PUBLICATION_STATES = (
    "pending",
    "on_time",
    "late",
    "off_schedule",
    "blocked",
    "failed",
    "abstained",
    "missing",
    "unconfirmed",
)
LABEL_STATES = (
    "pending_maturity",
    "verified",
    "partial",
    "invalid",
    "missing_at_deadline",
    "late_diagnostic",
)
TARGETS = (("long", "0.02"), ("short", "0.02"), ("long", "0.05"), ("short", "0.05"))


class OutcomeContractV2(FrozenContract):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    financial_qualified: Literal[False] = False
    historical_pit_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False
    fixture_mode: bool

    @field_validator("*", mode="after")
    @classmethod
    def normalize_clock(cls, value):
        return aware_utc(value) if isinstance(value, datetime) else value


def seal_outcome[T: OutcomeContractV2](contract: type[T], **values) -> T:
    candidate = contract.model_validate({**values, "payload_hash": "0" * 64})
    return contract.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "payload_hash": canonical_hash(
                candidate.model_dump(mode="json", exclude={"payload_hash"})
            ),
        }
    )


def verify_outcome[T: OutcomeContractV2](value: T) -> T:
    checked = type(value).model_validate_json(value.model_dump_json())
    if (
        canonical_hash(checked.model_dump(mode="json", exclude={"payload_hash"}))
        != checked.payload_hash
    ):
        raise ValueError("history_outcome_payload_changed")
    return checked


class OutcomeOriginalRefV2(FrozenContract):
    identifier: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_ref: str = Field(min_length=1, max_length=4096)
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OutcomeModelGrantV2(FrozenContract):
    scope_id: str
    model: OutcomeOriginalRefV2
    dataset: OutcomeOriginalRefV2
    model_known_at: datetime

    @field_validator("model_known_at")
    @classmethod
    def clock(cls, value):
        return aware_utc(value)

    @model_validator(mode="after")
    def identity(self):
        if self.scope_id not in SCOPES or not self.model.identifier.startswith("hfit2_"):
            raise ValueError("history_outcome_model_grant_invalid")
        return self


class OutcomeSavedV2(OutcomeContractV2):
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    source_files: tuple[tuple[str, str], ...]
    source_original_ref: str = Field(min_length=1, max_length=4096)
    source_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    produced_at: datetime
    recorded_at: datetime

    @model_validator(mode="after")
    def source_clock(self):
        if (
            not self.source_files
            or len(dict(self.source_files)) != len(self.source_files)
            or any(
                not name or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                for name, digest in self.source_files
            )
            or self.produced_at > self.recorded_at
        ):
            raise ValueError("history_outcome_source_or_clock_invalid")
        return self


class HistoryOutcomeProtocolV2(OutcomeSavedV2):
    schema_version: Literal["history-outcome-protocol.v2"] = "history-outcome-protocol.v2"
    protocol_id: str = Field(pattern=r"^heval2_[a-f0-9]{48}$")
    registered_at: datetime
    first_hour: datetime
    last_hour: datetime
    model_grants: tuple[OutcomeModelGrantV2, ...] = Field(min_length=2, max_length=2)
    cadence_seconds: Literal[3600] = 3600
    opportunity_count: Literal[48] = 48
    dispatch_grace_seconds: Literal[300] = 300
    publication_grace_seconds: Literal[60] = 60
    horizon_seconds: Literal[86400] = 86400
    label_grace_seconds: Literal[86400] = 86400
    thresholds: tuple[Literal["0.02", "0.05"], ...] = ("0.02", "0.05")
    anchor_policy: Literal["scheduled_bar_open"] = "scheduled_bar_open"
    cutoff_policy: Literal["actual-first-dispatch-minute-within-5min.v1"] = (
        "actual-first-dispatch-minute-within-5min.v1"
    )
    primary_target: Literal["long:0.02"] = "long:0.02"
    score_precision: Literal[28] = 28
    score_rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    observation_offsets_seconds: tuple[int, ...] = (120, 900, 3600, 21600)
    maximum_observation_attempts: Literal[4] = 4
    automatic_forecast_retry: Literal[False] = False
    ci_status: Literal["not_registered_for_hourly_overlapping_windows"] = (
        "not_registered_for_hourly_overlapping_windows"
    )

    @model_validator(mode="after")
    def protocol(self):
        if (
            self.first_hour.timestamp() % 3600
            or self.last_hour != self.first_hour + timedelta(hours=23)
            or not self.registered_at <= self.produced_at <= self.recorded_at < self.first_hour
            or tuple(grant.scope_id for grant in self.model_grants) != SCOPES
            or any(grant.model_known_at > self.registered_at for grant in self.model_grants)
            or self.thresholds != ("0.02", "0.05")
            or self.observation_offsets_seconds != (120, 900, 3600, 21600)
        ):
            raise ValueError("history_outcome_frozen_protocol_invalid")
        return self


class HistoryOutcomeOpportunityV2(OutcomeSavedV2):
    schema_version: Literal["history-outcome-opportunity.v2"] = "history-outcome-opportunity.v2"
    opportunity_id: str = Field(pattern=r"^hopportunity2_[a-f0-9]{48}$")
    protocol: OutcomeOriginalRefV2
    scope_id: str
    model: OutcomeOriginalRefV2
    hour_at: datetime
    registered_at: datetime
    dispatch_deadline_at: datetime

    @model_validator(mode="after")
    def identity(self):
        if (
            self.scope_id not in SCOPES
            or self.hour_at.timestamp() % 3600
            or not self.registered_at <= self.produced_at <= self.recorded_at < self.hour_at
            or self.dispatch_deadline_at != self.hour_at + timedelta(minutes=5)
            or self.opportunity_id
            != "hopportunity2_"
            + canonical_hash([self.protocol.identifier, self.scope_id, self.hour_at.isoformat()])[
                :48
            ]
        ):
            raise ValueError("history_outcome_opportunity_invalid")
        return self


PublicationStateV2 = Literal[
    "pending",
    "on_time",
    "late",
    "off_schedule",
    "blocked",
    "failed",
    "abstained",
    "missing",
    "unconfirmed",
]
LabelStateV2 = Literal[
    "pending_maturity", "verified", "partial", "invalid", "missing_at_deadline", "late_diagnostic"
]


class HistoryOutcomePublicationV2(OutcomeSavedV2):
    schema_version: Literal["history-outcome-publication.v2"] = "history-outcome-publication.v2"
    publication_id: str = Field(pattern=r"^hpublication2_[a-f0-9]{48}$")
    protocol: OutcomeOriginalRefV2
    opportunity: OutcomeOriginalRefV2
    scope_id: str
    model: OutcomeOriginalRefV2
    hour_at: datetime
    registered_at: datetime
    classification: PublicationStateV2
    reason_code: str = Field(min_length=1, max_length=128)
    dispatch: OutcomeOriginalRefV2 | None = None
    forecast: OutcomeOriginalRefV2 | None = None
    actual_cutoff_at: datetime | None = None
    label_cutoff_at: datetime
    label_anchor_basis: Literal["actual_first_dispatch", "scheduled_counterfactual"]
    verified_readback_at: datetime | None = None
    diagnosed_at: datetime

    @model_validator(mode="after")
    def publication(self):
        actual = self.actual_cutoff_at
        if (
            self.scope_id not in SCOPES
            or self.hour_at.timestamp() % 3600
            or self.label_cutoff_at.timestamp() % 60
            or not self.registered_at < self.hour_at
            or not self.diagnosed_at <= self.produced_at <= self.recorded_at
            or self.classification == "pending"
        ):
            raise ValueError("history_outcome_terminal_publication_required")
        if self.label_anchor_basis == "actual_first_dispatch":
            if (
                actual is None
                or actual != self.label_cutoff_at
                or actual.timestamp() % 60
                or not self.hour_at <= actual <= self.hour_at + timedelta(minutes=5)
                or self.dispatch is None
            ):
                raise ValueError("history_outcome_first_dispatch_binding_invalid")
        elif (
            actual is not None
            or self.label_cutoff_at != self.hour_at
            or self.classification not in ("missing", "off_schedule")
        ):
            raise ValueError("history_outcome_counterfactual_binding_invalid")
        if self.classification in ("on_time", "late"):
            if (
                self.forecast is None
                or actual is None
                or self.verified_readback_at is None
                or not actual <= self.verified_readback_at <= self.diagnosed_at
            ):
                raise ValueError("history_outcome_readback_clock_required")
            on_time = self.verified_readback_at <= actual + timedelta(minutes=1)
            if (self.classification == "on_time") != on_time:
                raise ValueError("history_outcome_publication_timing_mismatch")
        elif self.verified_readback_at is not None:
            raise ValueError("history_outcome_unverified_readback_forbidden")
        if self.classification == "missing" and self.diagnosed_at < self.hour_at + timedelta(
            minutes=5
        ):
            raise ValueError("history_outcome_missing_before_deadline")
        return self


class HistoryOutcomeObservationV2(OutcomeSavedV2):
    schema_version: Literal["history-outcome-observation.v2"] = "history-outcome-observation.v2"
    observation_id: str = Field(pattern=r"^hobservation2_[a-f0-9]{48}$")
    protocol: OutcomeOriginalRefV2
    opportunity: OutcomeOriginalRefV2
    publication: OutcomeOriginalRefV2
    scope_id: str
    label_cutoff_at: datetime
    window_start_at: datetime
    window_end_at: datetime
    label_deadline_at: datetime
    observed_at: datetime
    accepted_at: datetime | None = None
    attempt: int = Field(ge=1, le=4)
    classification: LabelStateV2
    reason_code: str = Field(min_length=1, max_length=128)
    anchor_status: Literal["verified", "unknown"]
    anchor_price: Decimal | None = None
    observed_count: int = Field(ge=0, le=1440)
    expected_count: Literal[1440] = 1440
    pending_count: int = Field(ge=0, le=1440)
    window_status: Literal["pending", "complete", "partial", "unknown"]
    mae_long: Decimal | None = None
    mae_short: Decimal | None = None
    path_semantic_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    path_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_original_ref: str = Field(min_length=1, max_length=4096)
    observation_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def observation(self):
        if (
            self.scope_id not in SCOPES
            or self.label_cutoff_at.timestamp() % 60
            or self.window_start_at != self.label_cutoff_at + timedelta(minutes=1)
            or self.window_end_at != self.window_start_at + timedelta(days=1)
            or self.label_deadline_at != self.window_end_at + timedelta(days=1)
            or not self.observed_at <= self.produced_at <= self.recorded_at
            or any(
                value is not None and (not value.is_finite() or value < 0)
                for value in (self.anchor_price, self.mae_long, self.mae_short)
            )
        ):
            raise ValueError("history_outcome_label_window_or_clock_invalid")
        complete = (
            self.window_status == "complete"
            and self.anchor_status == "verified"
            and self.anchor_price is not None
            and self.anchor_price > 0
            and self.observed_count == 1440
            and self.pending_count == 0
            and self.mae_long is not None
            and self.mae_short is not None
            and self.observed_at >= self.window_end_at
        )
        if self.classification in ("verified", "late_diagnostic"):
            if (
                not complete
                or self.accepted_at is not None
            ):
                raise ValueError("history_outcome_complete_label_required")
            if (self.classification == "verified") != (self.observed_at <= self.label_deadline_at):
                raise ValueError("history_outcome_label_acceptance_deadline_mismatch")
        elif self.accepted_at is not None:
            raise ValueError("history_outcome_incomplete_acceptance_forbidden")
        if self.classification == "pending_maturity" and self.observed_at >= self.window_end_at:
            raise ValueError("history_outcome_mature_label_not_pending")
        if (
            self.classification == "missing_at_deadline"
            and self.observed_at < self.label_deadline_at
        ):
            raise ValueError("history_outcome_missing_label_before_deadline")
        return self


class HistoryOutcomeAcceptanceV2(OutcomeSavedV2):
    """Evidence issued after a different connection reads the stored subject.

    S proves the subject was already durably readable. The receipt's own COMMIT
    may occur later; recorded_at is never promoted to that independent S.
    """

    schema_version: Literal["history-outcome-acceptance.v2"] = "history-outcome-acceptance.v2"
    acceptance_id: str = Field(pattern=r"^hacceptance2_[a-f0-9]{48}$")
    protocol: OutcomeOriginalRefV2
    opportunity: OutcomeOriginalRefV2 | None = None
    accepted_kind: Literal["protocol", "publication", "observation"]
    subject: OutcomeOriginalRefV2
    subject_recorded_at: datetime
    independently_verified_at: datetime
    readback_evidence_ref: str = Field(min_length=1, max_length=4096)
    readback_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verification_mode: Literal["independent-physical-ro-snapshot.v1"] = (
        "independent-physical-ro-snapshot.v1"
    )

    @model_validator(mode="after")
    def confirmation(self):
        if (
            not self.subject_recorded_at <= self.independently_verified_at
            <= self.produced_at <= self.recorded_at
            or (self.accepted_kind == "protocol") != (self.opportunity is None)
            or self.acceptance_id != "hacceptance2_" + canonical_hash(
                [self.owner_claim, self.accepted_kind, self.subject.identifier]
            )[:48]
        ):
            raise ValueError("history_outcome_independent_confirmation_invalid")
        return self


class HistoryOutcomeTargetScoreV2(FrozenContract):
    direction: Literal["long", "short"]
    threshold: Literal["0.02", "0.05"]
    n_baseline: int = Field(ge=0, le=48)
    n_conditional: int = Field(ge=0, le=48)
    n_pair: int = Field(ge=0, le=48)
    baseline_brier: Decimal | None
    conditional_brier: Decimal | None
    paired_gain: Decimal | None
    ci: None = None
    ci_status: Literal["not_registered_for_hourly_overlapping_windows"] = (
        "not_registered_for_hourly_overlapping_windows"
    )

    @model_validator(mode="after")
    def scores(self):
        if self.n_baseline != self.n_pair or self.n_conditional != self.n_pair:
            raise ValueError("history_outcome_same_pair_set_required")
        values = (self.baseline_brier, self.conditional_brier, self.paired_gain)
        if (
            (self.n_pair == 0) != all(value is None for value in values)
            or self.n_pair > 0
            and any(value is None or not value.is_finite() for value in values)
            or any(value is not None and not 0 <= value <= 1 for value in values[:2])
            or self.paired_gain is not None
            and not -1 <= self.paired_gain <= 1
        ):
            raise ValueError("history_outcome_nullable_brier_invalid")
        return self


class HistoryOutcomeCaseResultV2(FrozenContract):
    opportunity_id: str
    scope_id: str
    hour_at: datetime
    publication_state: PublicationStateV2
    label_state: LabelStateV2
    label_cutoff_at: datetime
    publication_due: bool
    label_due: bool
    eligible_pair: bool
    exclusions: tuple[str, ...]
    opportunity: OutcomeOriginalRefV2
    publication: OutcomeOriginalRefV2 | None
    observation: OutcomeOriginalRefV2 | None
    forecast: OutcomeOriginalRefV2 | None
    acceptance: OutcomeOriginalRefV2 | None = None

    @field_validator("hour_at", "label_cutoff_at")
    @classmethod
    def clock(cls, value):
        return aware_utc(value)


class HistoryOutcomeScoreV2(OutcomeSavedV2):
    schema_version: Literal["history-outcome-score.v2"] = "history-outcome-score.v2"
    score_id: str = Field(pattern=r"^hscore2_[a-f0-9]{48}$")
    protocol: OutcomeOriginalRefV2
    actual_as_of: datetime
    expected_total: Literal[48] = 48
    publication_due_count: int = Field(ge=0, le=48)
    label_due_count: int = Field(ge=0, le=48)
    publication_counts: dict[PublicationStateV2, int]
    label_counts: dict[LabelStateV2, int]
    published_count: int = Field(ge=0, le=48)
    on_time_count: int = Field(ge=0, le=48)
    verified_label_count: int = Field(ge=0, le=48)
    paired_count: int = Field(ge=0, le=48)
    targets: tuple[HistoryOutcomeTargetScoreV2, ...] = Field(min_length=4, max_length=4)
    cases: tuple[HistoryOutcomeCaseResultV2, ...] = Field(min_length=48, max_length=48)
    primary_target: Literal["long:0.02"] = "long:0.02"
    ci: None = None
    ci_status: Literal["not_registered_for_hourly_overlapping_windows"] = (
        "not_registered_for_hourly_overlapping_windows"
    )

    @model_validator(mode="after")
    def census(self):
        if (
            set(self.publication_counts) != set(PUBLICATION_STATES)
            or set(self.label_counts) != set(LABEL_STATES)
            or any(
                type(n) is not int or not 0 <= n <= 48
                for n in (*self.publication_counts.values(), *self.label_counts.values())
            )
            or sum(self.publication_counts.values()) != 48
            or sum(self.label_counts.values()) != 48
            or len({case.opportunity_id for case in self.cases}) != 48
            or tuple((target.direction, target.threshold) for target in self.targets) != TARGETS
            or any(target.n_pair != self.paired_count for target in self.targets)
            or self.publication_due_count != sum(case.publication_due for case in self.cases)
            or self.label_due_count != sum(case.label_due for case in self.cases)
            or self.on_time_count != self.publication_counts["on_time"]
            or self.published_count
            != self.publication_counts["on_time"] + self.publication_counts["late"]
            or self.verified_label_count != self.label_counts["verified"]
            or self.paired_count != sum(case.eligible_pair for case in self.cases)
            or not self.paired_count <= self.verified_label_count <= self.label_due_count
            or not self.actual_as_of <= self.produced_at <= self.recorded_at
        ):
            raise ValueError("history_outcome_complete_score_census_invalid")
        for state in PUBLICATION_STATES:
            if self.publication_counts[state] != sum(
                case.publication_state == state for case in self.cases
            ):
                raise ValueError("history_outcome_publication_census_invalid")
        for state in LABEL_STATES:
            if self.label_counts[state] != sum(case.label_state == state for case in self.cases):
                raise ValueError("history_outcome_label_census_invalid")
        return self
