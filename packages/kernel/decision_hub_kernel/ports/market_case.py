"""Immutable market lessons. A user opinion or verified flag is not a market label."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash


class CaseScope(FrozenContract):
    market: str = Field(min_length=1, max_length=64)
    instrument: str = Field(min_length=1, max_length=128)
    horizon: str = Field(min_length=1, max_length=32)
    method_family: str = Field(min_length=1, max_length=32)
    stable_recipe_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CaseOriginal(FrozenContract):
    namespace: str
    original_id: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    first_known_at: AwareDatetime


class MarketCase(FrozenContract):
    schema_version: Literal["market-case.v1"] = "market-case.v1"
    case_id: str
    request_id: str
    revision_id: str
    study_id: str
    run_id: str
    artifact_id: str
    outlook_id: str
    scope: CaseScope
    original_cutoff_at: AwareDatetime | None
    window_start_at: AwareDatetime
    window_end_at: AwareDatetime
    created_at: AwareDatetime
    original_known_at: AwareDatetime
    label_known_at: AwareDatetime | None
    available_at: AwareDatetime
    classification: Literal[
        "verified_market_case",
        "product_feedback",
        "operational_failure",
        "model_error",
        "research_diagnostic",
    ]
    failure_category: str | None = None
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    action: Literal["long", "short", "neutral", "no_trade"]
    subjective_probability: float | None = Field(default=None, ge=0, le=1)
    mechanism: str | None = None
    precursor: tuple[str, ...] = ()
    counter_evidence: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    confirmation: tuple[str, ...] = ()
    invalidation: tuple[str, ...] = ()
    direction_correct: bool | None = None
    directional_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    label_capture_ids: tuple[str, ...] = ()
    originals: tuple[CaseOriginal, ...]
    assessment: dict
    assessment_basis: dict
    source_snapshot: dict
    # Feedback and report prose remain original wording, never a new AI summary.
    feedback: dict | None = None
    actual_alert_refs: tuple[str, ...] = ()
    normal_control_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "minute_close_not_execution_pnl",
        "not_calibration",
        "not_event_independence",
        "alert_and_normal_control_not_bound",
    )

    @model_validator(mode="after")
    def conservative_availability(self) -> MarketCase:
        times = [self.created_at, self.original_known_at]
        if self.label_known_at is not None:
            times.append(self.label_known_at)
        if self.available_at != max(times) or self.window_end_at <= self.window_start_at:
            raise ValueError("market_case_availability_invalid")
        if self.eligible and (
            self.classification != "verified_market_case"
            or self.label_known_at is None
            or self.direction_correct is None
            or self.exclusion_reasons
            or self.scope.stable_recipe_hash is None
        ):
            raise ValueError("market_case_qualification_invalid")
        return self

    @property
    def payload_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class CaseRelation(FrozenContract):
    relation_id: str
    request_id: str
    left_case_id: str
    right_case_id: str
    left_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    right_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["contradicts", "supersedes", "normal_control"]
    reason: str = Field(min_length=1, max_length=2000)
    created_at: AwareDatetime


class RetrievedCase(FrozenContract):
    case: MarketCase
    payload_hash: str
    similarities: tuple[str, ...]
    differences: tuple[str, ...]
    relations: tuple[CaseRelation, ...]


class CaseRetrieval(FrozenContract):
    schema_version: Literal["market-case-retrieval.v1"] = "market-case-retrieval.v1"
    scope: CaseScope
    cutoff_at: AwareDatetime
    items: tuple[RetrievedCase, ...]
    excluded: tuple[tuple[str, str], ...]
    memory_enabled: bool
    truncated: bool
    total_scanned: int


class CaseContextReference(FrozenContract):
    ref: str
    payload_hash: str
    available_at: AwareDatetime
    revision_id: str
    run_id: str
    # This is historical context, not a current market Fact or an official source.
    context_only: Literal[True] = True
    excerpt: str = Field(min_length=1, max_length=4000)
