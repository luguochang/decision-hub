"""Strict public outcome relations, independent of mutable runtime services."""

import re
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

UTC_TEXT = re.compile(r"^(?!0000)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
SCOPES = ("crypto:spot:okx:BTC-USDT:trade:1m", "crypto:spot:okx:ETH-USDT:trade:1m")
CLOCKS = frozenset(
    (
        "prepared_registered_at",
        "independently_confirmed_registered_at",
        "first_hour",
        "last_hour",
        "actual_as_of",
        "hour_at",
        "actual_cutoff_at",
        "label_cutoff_at",
        "produced_at",
        "recorded_at",
        "window_start_at",
        "window_end_at",
        "label_deadline_at",
        "observed_at",
        "accepted_at",
    )
)
BOOLEANS = frozenset(
    (
        "owner_authenticated",
        "current_originals_verified",
        "financial_qualified",
        "historical_pit_verified",
        "promotion_allowed",
        "fixture_mode",
        "publication_due",
        "label_due",
        "eligible_pair",
    )
)
TARGETS = (("long", "0.02"), ("short", "0.02"), ("long", "0.05"), ("short", "0.05"))


class HistoryOutcomeContractBaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def exact_primitives(cls, value, info):
        if info.field_name == "source_files" and type(value) is list:
            # JSON arrays are wire tuples. A wildcard before-validator otherwise
            # removes Pydantic's JSON-mode tuple handling under strict=True.
            return [tuple(pair) if type(pair) is list else pair for pair in value]
        if info.field_name in BOOLEANS and type(value) is not bool:
            raise ValueError("history_outcome_exact_boolean_required")
        if info.field_name in CLOCKS and isinstance(value, str):
            if UTC_TEXT.fullmatch(value) is None:
                raise ValueError("history_outcome_exact_utc_timestamp_required")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("*", mode="after")
    @classmethod
    def utc_clock(cls, value, info):
        if info.field_name in CLOCKS and value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("history_outcome_utc_clock_required")
        return value

    @model_validator(mode="after")
    def relations(self):
        value = self.model_dump(mode="json")
        version = value.get("schema_version")
        if version == "history-outcome-status.v2":
            self._status()
        elif version == "history-outcome-score.v2":
            self._score(value)
        elif version == "history-outcome-observation.v2":
            if (
                self.label_cutoff_at.timestamp() % 60
                or self.window_start_at != self.label_cutoff_at + timedelta(minutes=1)
                or (
                    self.window_end_at != self.window_start_at + timedelta(days=1)
                    or self.label_deadline_at != self.window_end_at + timedelta(days=1)
                    or not self.observed_at <= self.produced_at <= self.recorded_at
                    or self.accepted_at is not None
                    or any(
                        number is not None and Decimal(number) < 0
                        for number in (self.anchor_price, self.mae_long, self.mae_short)
                    )
                )
            ):
                raise ValueError("history_outcome_observation_window_or_clock_invalid")
            complete = (
                self.window_status == "complete"
                and self.anchor_status == "verified"
                and (
                    self.anchor_price is not None
                    and Decimal(self.anchor_price) > 0
                    and self.observed_count == 1440
                    and self.pending_count == 0
                    and self.mae_long is not None
                    and self.mae_short is not None
                    and self.observed_at >= self.window_end_at
                )
            )
            if (
                self.classification in ("verified", "late_diagnostic")
                and (
                    not complete
                    or (self.classification == "verified")
                    != (self.observed_at <= self.label_deadline_at)
                )
                or self.classification == "pending_maturity"
                and self.observed_at >= self.window_end_at
                or (
                    self.classification == "missing_at_deadline"
                    and self.observed_at < self.label_deadline_at
                )
            ):
                raise ValueError("history_outcome_observation_classification_invalid")
        elif version in ("history-outcome-score-reply.v2", "history-outcome-observation-reply.v2"):
            subject = value.get("score", value.get("observation"))
            if subject["protocol"]["identifier"] != self.protocol_id:
                raise ValueError("history_outcome_reply_protocol_mismatch")
        elif "n_pair" in value:
            numbers = (value["baseline_brier"], value["conditional_brier"], value["paired_gain"])
            if value["n_baseline"] != value["n_pair"] or value["n_conditional"] != value["n_pair"]:
                raise ValueError("history_outcome_same_pair_set_required")
            if value["n_pair"] == 0:
                valid = all(number is None for number in numbers)
            else:
                valid = all(number is not None for number in numbers) and (
                    all(0 <= Decimal(number) <= 1 for number in numbers[:2])
                    and -1 <= Decimal(numbers[2]) <= 1
                )
            if not valid:
                raise ValueError("history_outcome_nullable_brier_invalid")
        elif "asset" in value:
            index = 0 if self.asset == "BTC" else 1
            if self.scope_id != SCOPES[index] or self.asset_label != ("比特币", "以太坊")[index]:
                raise ValueError("history_outcome_asset_scope_mismatch")
        if (
            "produced_at" in value
            and "recorded_at" in value
            and self.produced_at > self.recorded_at
        ):
            raise ValueError("history_outcome_original_clock_invalid")
        return self

    def _status(self):
        if (
            self.first_hour.timestamp() % 3600
            or self.last_hour != self.first_hour + timedelta(hours=23)
            or self.prepared_registered_at >= self.first_hour
            or self.prepared_registered_at > self.actual_as_of
            or sum(self.publication_counts.model_dump().values()) != 48
            or sum(self.label_counts.model_dump().values()) != 48
            or not self.paired_count <= self.label_counts.verified <= self.label_due_count
            or len(self.opportunities) != min(self.pagination.limit, 48 - self.pagination.offset)
        ):
            raise ValueError("history_outcome_status_census_invalid")
        confirmed = self.independently_confirmed_registered_at
        if (
            self.state == "prepared"
            and confirmed is not None
            or (self.state in ("active", "completed") and confirmed is None)
            or confirmed is not None
            and not (
                self.prepared_registered_at <= confirmed < self.first_hour
                and confirmed <= self.actual_as_of
            )
        ):
            raise ValueError("history_outcome_registration_confirmation_invalid")
        for index, item in enumerate(self.opportunities, self.pagination.offset):
            if (
                item.hour_at != self.first_hour + timedelta(hours=index // 2)
                or (item.scope_id != SCOPES[index % 2])
                or item.actual_cutoff_at is not None
                and (
                    item.actual_cutoff_at.timestamp() % 60
                    or not item.hour_at
                    <= item.actual_cutoff_at
                    <= item.hour_at + timedelta(minutes=5)
                )
            ):
                raise ValueError("history_outcome_opportunity_page_identity_invalid")

    def _score(self, value):
        cases = value["cases"]
        if (
            sum(value["publication_counts"].values()) != 48
            or sum(value["label_counts"].values()) != 48
            or len({case["opportunity_id"] for case in cases}) != 48
            or tuple((target["direction"], target["threshold"]) for target in value["targets"])
            != TARGETS
            or any(target["n_pair"] != self.paired_count for target in value["targets"])
            or self.publication_due_count != sum(case["publication_due"] for case in cases)
            or self.label_due_count != sum(case["label_due"] for case in cases)
            or self.paired_count != sum(case["eligible_pair"] for case in cases)
            or self.published_count
            != value["publication_counts"]["on_time"] + value["publication_counts"]["late"]
            or self.on_time_count != value["publication_counts"]["on_time"]
            or self.verified_label_count != value["label_counts"]["verified"]
            or not self.paired_count <= self.verified_label_count <= self.label_due_count
            or not self.actual_as_of <= self.produced_at <= self.recorded_at
        ):
            raise ValueError("history_outcome_score_census_invalid")
        for field, state in (
            ("publication_counts", "publication_state"),
            ("label_counts", "label_state"),
        ):
            if any(
                number != sum(case[state] == name for case in cases)
                for name, number in value[field].items()
            ):
                raise ValueError("history_outcome_score_state_census_invalid")
        if any(
            case["eligible_pair"]
            and (
                case["publication_state"] != "on_time"
                or case["label_state"] != "verified"
                or any(
                    case[name] is None
                    for name in ("publication", "observation", "forecast", "acceptance")
                )
                or not case["label_due"]
                or case["exclusions"]
                or self.fixture_mode
            )
            for case in cases
        ):
            raise ValueError("history_outcome_pair_eligibility_invalid")
