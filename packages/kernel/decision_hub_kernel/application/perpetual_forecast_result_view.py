"""Owner-bound, read-only projection of an immutable perpetual forecast outcome."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    ProbabilityEstimateV1,
    PublicationBoundTargetV1,
)

from ..persistence.action_repository import ActionRepository
from ..persistence.db import RunRecord, RunRoleOutputRecord, utcnow
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_forecast_experience import ROLE as EXPERIENCE_ROLE
from ..ports.perpetual_forecast_experience import SCHEMA as EXPERIENCE_SCHEMA
from .perpetual_advice_query import (
    PUBLISHER,
    read_perpetual_published_originals_in_session,
)
from .perpetual_forecast_experience import read_forecast_experience_in_session
from .perpetual_research_binding import (
    ADMISSION_ROLE,
    read_perpetual_admission_identity_in_session,
)

SCHEMA = "perpetual-forecast-result-view.v1"


class ForecastResultError(ValueError):
    """Stable public failure code; the underlying exception is never returned."""


class ObservedForecastResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experience_ref: DecisionSourceRefV1
    snapshot_ref: DecisionSourceRefV1
    final_target_ref: DecisionSourceRefV1
    frozen_forecast_ref: DecisionSourceRefV1
    market_distribution_ref: DecisionSourceRefV1
    method_ref: DecisionSourceRefV1
    target_family_ref: DecisionSourceRefV1
    label_rule_ref: DecisionSourceRefV1
    direction_estimate: dict
    actual_category: Literal["up", "neutral", "down"]
    actual_return_fraction: str
    multiclass_brier: str | None
    known_at: datetime
    recorded_at: datetime
    gaps: list[str]
    calculation_method_hash: str
    qualification: Literal["research_trial"]
    financial_qualified: Literal[False]
    skill_consumed: Literal[False]
    trade_outcome: Literal[False]

    @field_validator("direction_estimate")
    @classmethod
    def valid_direction_estimate(cls, value):
        ProbabilityEstimateV1.model_validate(value)
        return value


class PerpetualForecastResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["perpetual-forecast-result-view.v1"] = SCHEMA
    status: Literal["not_due", "pending", "unsupported", "observed"]
    reason_code: Literal[
        "forecast_target_not_due",
        "forecast_result_not_recorded",
        "forecast_horizon_unsupported",
    ] | None
    owner_id: str
    run_id: str
    instrument_id: str
    horizon: Literal["4h", "24h", "72h"]
    read_at: datetime
    admission_ref: DecisionSourceRefV1
    target_ref: DecisionSourceRefV1
    target: dict
    result: ObservedForecastResult | None

    @field_validator("target")
    @classmethod
    def valid_target(cls, value):
        PublicationBoundTargetV1.model_validate(value)
        return value

    @model_validator(mode="after")
    def exact_state(self):
        reasons = {
            "not_due": "forecast_target_not_due",
            "pending": "forecast_result_not_recorded",
            "unsupported": "forecast_horizon_unsupported",
            "observed": None,
        }
        if self.reason_code != reasons[self.status] or (self.status == "observed") != (
            self.result is not None
        ):
            raise ValueError("perpetual_forecast_result_state_invalid")
        return self


def _wire(view: PerpetualForecastResultView) -> dict:
    return view.model_dump(mode="json")


def _error(code: str, cause: Exception | None = None) -> NoReturn:
    if cause is None:
        raise ForecastResultError(code)
    raise ForecastResultError(code) from cause


class PerpetualForecastResultQuery:
    def __init__(self, database, *, now=utcnow):
        self.database, self.now = database, now

    def applicable(self, run_id: str) -> bool:
        with self.database.session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == ADMISSION_ROLE,
                )
            )
            return count == 1

    def read(self, owner_id: str, run_id: str) -> dict:
        at = aware_utc(self.now())
        with self.database.session() as session:
            try:
                identity = read_perpetual_admission_identity_in_session(session, run_id)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                _error("perpetual_forecast_result_publication_unavailable", exc)
            if identity is None:
                _error("perpetual_forecast_result_not_applicable")
            admission, admission_ref = identity
            if admission["owner_id"] != owner_id:
                raise PermissionError("perpetual_forecast_result_owner_mismatch")
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None:
                _error("perpetual_forecast_result_not_applicable")
            command_id = "publish_" + canonical_hash(
                [run_id, admission["opportunity_id"]]
            )
            published = ActionRepository(session).get_command(PUBLISHER, command_id)
            if published is None:
                _error("perpetual_forecast_result_publication_unavailable")
            try:
                publication = read_perpetual_published_originals_in_session(
                    session,
                    run=run,
                    admission=admission,
                    admission_ref=admission_ref,
                    published=published,
                    at=at,
                )
            except (ValueError, TypeError, KeyError, OSError) as exc:
                _error("perpetual_forecast_result_publication_unavailable", exc)

            target_body = publication["target"]
            target = PublicationBoundTargetV1.model_validate(target_body)
            bindings = publication["snapshot"]["bindings"]
            final_target_ref = DecisionSourceRefV1.model_validate(bindings["final_target"])
            if canonical_hash(target_body) != final_target_ref.hash:
                _error("perpetual_forecast_result_binding_mismatch")
            base = dict(
                schema_version=SCHEMA,
                owner_id=owner_id,
                run_id=run_id,
                instrument_id=admission["partition"]["instrument_id"],
                horizon=admission["context"]["horizon"],
                read_at=at,
                admission_ref=DecisionSourceRefV1.model_validate(admission_ref),
                target_ref=final_target_ref,
                target=target_body,
            )
            from ..ports.perpetual_forecast_scoring import ROLE as SCORING_ROLE
            scoring_rows = session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_((EXPERIENCE_ROLE, SCORING_ROLE)),
            )).all()
            if len(scoring_rows) > 1:
                _error("perpetual_forecast_result_original_unavailable")
            scoring_row = next((item for item in scoring_rows if item.role == SCORING_ROLE), None)
            v6 = session.scalar(select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "research_publication_source",
                RunRoleOutputRecord.schema_version == "research-publication-source.v6",
            )) is not None
            scoring_claim = session.scalar(select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.schema_version == "perpetual-forecast-due-observation.v2",
                RunRoleOutputRecord.role.startswith("perpetual_forecast_due_observation_v2:"),
            ).limit(1)) is not None
            if scoring_row is not None or (v6 or scoring_claim) and not scoring_rows:
                from .perpetual_forecast_scoring_view import read_scoring_view
                try:
                    return read_scoring_view(
                        session, base=base, publication=publication, row=scoring_row, at=at
                    )
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    _error("perpetual_forecast_result_original_unavailable", exc)
            if target.horizon != "4h":
                return _wire(
                    PerpetualForecastResultView(
                        **base,
                        status="unsupported",
                        reason_code="forecast_horizon_unsupported",
                        result=None,
                    )
                )
            if at < aware_utc(target.window_end):
                return _wire(
                    PerpetualForecastResultView(
                        **base,
                        status="not_due",
                        reason_code="forecast_target_not_due",
                        result=None,
                    )
                )

            row = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == EXPERIENCE_ROLE,
                )
            )
            if row is None:
                return _wire(
                    PerpetualForecastResultView(
                        **base,
                        status="pending",
                        reason_code="forecast_result_not_recorded",
                        result=None,
                    )
                )
            if row.schema_version != EXPERIENCE_SCHEMA:
                _error("perpetual_forecast_result_original_unavailable")
            reference = DecisionSourceRefV1(ref=row.output_id, hash=row.payload_hash)
            try:
                original = read_forecast_experience_in_session(
                    session, reference, cutoff_at=at
                )
            except (ValueError, TypeError, KeyError, OSError) as exc:
                _error("perpetual_forecast_result_original_unavailable", exc)
            body = original.body
            expected_snapshot = {
                "ref": publication["snapshot"]["id"],
                "hash": publication["snapshot"]["snapshot_hash"],
            }
            if (
                body["owner_id"] != owner_id
                or body["run_id"] != run_id
                or body["instrument_id"] != base["instrument_id"]
                or body["horizon"] != "4h"
                or body["target"] != target_body
                or body["snapshot_ref"] != expected_snapshot
                or body["final_target_ref"] != final_target_ref.model_dump(mode="json")
                or body["frozen_forecast_ref"] != bindings["frozen_forecast"]
                or body["market_distribution_ref"]
                != publication["frozen_forecast"]["market_distribution_ref"]
                or body["method_ref"] != publication["target"]["method_ref"]
                or body["target_family_ref"] != publication["target"]["target_family_ref"]
                or body["label_rule_ref"] != publication["target"]["label_rule_ref"]
                or body["direction_estimate"]
                != publication["market_distribution"]["direction_distribution"]
            ):
                _error("perpetual_forecast_result_binding_mismatch")
            result = ObservedForecastResult(
                experience_ref=original.ref,
                snapshot_ref=body["snapshot_ref"],
                final_target_ref=body["final_target_ref"],
                frozen_forecast_ref=body["frozen_forecast_ref"],
                market_distribution_ref=body["market_distribution_ref"],
                method_ref=body["method_ref"],
                target_family_ref=body["target_family_ref"],
                label_rule_ref=body["label_rule_ref"],
                direction_estimate=body["direction_estimate"],
                actual_category=body["category"],
                actual_return_fraction=body["return_fraction"],
                multiclass_brier=body["multiclass_brier"],
                known_at=body["known_at"],
                recorded_at=body["recorded_at"],
                gaps=body["gaps"],
                calculation_method_hash=body["calculation_method_hash"],
                qualification=body["qualification"],
                financial_qualified=body["financial_qualified"],
                skill_consumed=body["skill_consumed"],
                trade_outcome=body["trade_outcome"],
            )
            return _wire(
                PerpetualForecastResultView(
                    **base, status="observed", reason_code=None, result=result
                )
            )
