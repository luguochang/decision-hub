"""Durable finite trials, immutable paired predictions and authenticated path receipts."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..decision.path_forecast import (
    LEGACY_PARAMETERS,
    PARAMETERS,
    fit_path_forecast,
    predict_path_forecast,
    verify_path_forecast,
)
from ..decision.path_forecast_score import (
    observation_complete,
    score_path_forecasts,
    verify_observation,
)
from ..decision.path_score_inference import MatchedEventBlock
from ..persistence.db import Database, as_utc, utcnow
from ..persistence.path_forecast_records import (
    RiskEvaluationReportRecord,
    RiskPathJobRecord,
    RiskPathObservationRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from ..ports.market_component_consumer import MarketComponentConsumer
from ..ports.market_path import MarketPathScope, aware_utc, canonical_hash, spot_scope
from ..ports.path_forecast import (
    FrozenPathForecastModel,
    PathFeatureManifest,
    PathObservationReceipt,
    RiskPathForecast,
    parse_path_parameters,
)
from .component_consumption_binding import component_consumption_reference_in_session
from .market_component_research_functions import (
    PathForecastConsumptionInput,
    predict_registered_path_forecast,
    verify_consumed_path_forecast,
)
from .market_path_store import MarketPathStore
from .path_forecast_history import MarketPathForecastInputs, seal_payload

if TYPE_CHECKING:
    from .path_shadow_jobs import PathShadowClaim

TARGET = "long-short-mae-2pct-5pct.v1"
REPORT = "path-shadow-evaluation.v1"
REPORT_V2 = "path-shadow-evaluation.v2"


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def writer(session: Session) -> None:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def checked_payload(raw: str, expected_hash: str) -> dict:
    payload = json.loads(raw)
    if (
        canonical_hash({key: value for key, value in payload.items() if key != "payload_hash"})
        != (expected_hash)
        or payload.get("payload_hash") != expected_hash
    ):
        raise ValueError("risk_path_immutable_payload_changed")
    return payload


def scope_from_protocol(protocol: dict, identity: str) -> MarketPathScope:
    for raw in protocol["scopes"]:
        scope = MarketPathScope.model_validate(raw)
        if scope.scope_id == identity:
            return scope
    raise ValueError("path_shadow_scope_not_registered")


class RiskPathLedger:
    def __init__(
        self,
        database: Database,
        path_store: MarketPathStore,
        *,
        clock: Callable[[], datetime] = utcnow,
        component_consumer: MarketComponentConsumer | None = None,
        forecast_alarm_publisher: Callable[..., tuple[dict, ...]] | None = None,
        forecast_alarm_score_reader: Callable[..., dict] | None = None,
        forecast_alarm_original_reader: Callable[..., dict] | None = None,
    ) -> None:
        self.database, self.clock = database, clock
        self.inputs = MarketPathForecastInputs(path_store, clock=clock)
        self.component_consumer = component_consumer
        self.forecast_alarm_publisher = forecast_alarm_publisher
        self.forecast_alarm_score_reader = forecast_alarm_score_reader
        self.forecast_alarm_original_reader = forecast_alarm_original_reader

    def now(self) -> datetime:
        return aware_utc(self.clock())

    @staticmethod
    def protocol(row: RiskShadowTrialRecord) -> dict:
        protocol = json.loads(row.protocol_json)
        parameters = parse_path_parameters(protocol["parameters"])
        expected_schema = (
            "risk-path-shadow-trial.v1"
            if parameters.protocol_version == "path-shadow-24h.v1"
            else "risk-path-shadow-trial.v2"
        )
        if (
            canonical_hash(protocol) != row.protocol_hash
            or parameters not in (LEGACY_PARAMETERS, PARAMETERS)
            or protocol["parameters"] != parameters.model_dump(mode="json")
            or protocol.get("schema_version") != expected_schema
        ):
            raise ValueError("path_shadow_protocol_changed")
        first = aware_utc(datetime.fromisoformat(protocol["first_cutoff_at"]))
        last = first + timedelta(days=parameters.shadow_days - 1)
        end = last + timedelta(days=1, minutes=1)
        if (
            as_utc(row.first_cutoff_at),
            as_utc(row.last_cutoff_at),
            as_utc(row.last_window_end_at),
            as_utc(row.label_deadline_at),
        ) != (first, last, end, end + timedelta(seconds=parameters.label_grace_seconds)) or (
            as_utc(row.registered_at) >= first or row.registration_hash != row.protocol_hash
        ):
            raise ValueError("path_shadow_fixed_deadline_identity_changed")
        return protocol

    def register(
        self,
        *,
        request_id: str,
        first_cutoff_at: datetime,
        scopes: tuple[MarketPathScope, ...] = (spot_scope("BTC-USDT"), spot_scope("ETH-USDT")),
    ) -> str:
        first, now = aware_utc(first_cutoff_at), self.now()
        if not request_id or len(request_id) > 128:
            raise ValueError("path_shadow_request_id_invalid")
        if first != first.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError("path_shadow_daily_cutoff_required")
        scopes = tuple(sorted(scopes, key=lambda scope: scope.scope_id))
        if (
            not scopes
            or len({scope.scope_id for scope in scopes}) != len(scopes)
            or any(scope != spot_scope(scope.instrument) for scope in scopes)
        ):
            raise ValueError("path_shadow_scope_invalid")
        protocol = {
            "schema_version": "risk-path-shadow-trial.v2",
            "parameters": PARAMETERS.model_dump(mode="json"),
            "scopes": [scope.model_dump(mode="json") for scope in scopes],
            "first_cutoff_at": first.isoformat(),
            "target_version": TARGET,
            "feature_policy": PARAMETERS.feature_policy,
            "source_policy": "authenticated-market-path-store.v1",
            "alarm_policy": "shadow-forecast-ui-receipts.v1",
            "promotion_allowed": False,
        }
        registration_hash = canonical_hash(protocol)
        trial_id = "shadow_" + canonical_hash([request_id, registration_hash])[:48]
        last = first + timedelta(days=PARAMETERS.shadow_days - 1)
        end = last + timedelta(days=1, minutes=1)
        deadline = end + timedelta(seconds=PARAMETERS.label_grace_seconds)
        with self.database.session() as session:
            writer(session)
            now = self.now()
            prior = session.query(RiskShadowTrialRecord).filter_by(request_id=request_id).first()
            if prior is not None:
                original = self.protocol(prior)
                if (
                    aware_utc(datetime.fromisoformat(original["first_cutoff_at"])) != first
                    or original["scopes"] != protocol["scopes"]
                ):
                    raise ValueError("path_shadow_request_identity_conflict")
                return prior.trial_id
            if first <= now:
                raise ValueError("path_shadow_registration_must_precede_first_cutoff")
            for active in (
                session.query(RiskShadowTrialRecord)
                .filter(
                    RiskShadowTrialRecord.state.not_in(("completed", "failed")),
                    RiskShadowTrialRecord.first_cutoff_at <= last,
                    RiskShadowTrialRecord.last_cutoff_at >= first,
                )
                .all()
            ):
                if {raw["scope_id"] for raw in self.protocol(active)["scopes"]} & {
                    scope.scope_id for scope in scopes
                }:
                    raise ValueError("path_shadow_overlapping_scope_trial")
            session.add(
                RiskShadowTrialRecord(
                    trial_id=trial_id,
                    request_id=request_id,
                    registration_hash=registration_hash,
                    protocol_json=encoded(protocol),
                    protocol_hash=canonical_hash(protocol),
                    registered_at=now,
                    first_cutoff_at=first,
                    last_cutoff_at=last,
                    last_window_end_at=end,
                    label_deadline_at=deadline,
                    state="registered",
                )
            )
            for scope in scopes:
                self.add_job(session, trial_id, "fit", scope.scope_id, first, now, first)
                for offset in range(PARAMETERS.shadow_days):
                    cutoff = first + timedelta(days=offset)
                    self.add_job(
                        session,
                        trial_id,
                        "forecast",
                        scope.scope_id,
                        cutoff,
                        cutoff,
                        cutoff + timedelta(minutes=1),
                    )
                    self.add_job(
                        session,
                        trial_id,
                        "label",
                        scope.scope_id,
                        cutoff,
                        cutoff + timedelta(days=1, minutes=1),
                        deadline,
                    )
            self.add_job(session, trial_id, "finalize", "*", first, deadline, deadline)
        return trial_id

    @staticmethod
    def add_job(session, trial_id, kind, scope_id, cutoff, due, deadline):
        identity = (
            "pathjob_"
            + canonical_hash(
                [
                    trial_id,
                    kind,
                    scope_id,
                    cutoff.isoformat(),
                ]
            )[:48]
        )
        session.add(
            RiskPathJobRecord(
                job_id=identity,
                trial_id=trial_id,
                kind=kind,
                scope_id=scope_id,
                cutoff_at=cutoff,
                due_at=due,
                deadline_at=deadline,
                next_attempt_at=due,
                state="pending",
                attempts=0,
                lease_generation=0,
                checkpoint_json="{}",
            )
        )

    def trial(self, trial_id: str) -> dict:
        with self.database.session() as session:
            row = session.get(RiskShadowTrialRecord, trial_id)
            if row is None:
                raise KeyError(trial_id)
            jobs = session.query(RiskPathJobRecord).filter_by(trial_id=trial_id).all()
            return {
                "trial_id": trial_id,
                "state": row.state,
                "protocol": self.protocol(row),
                "protocol_hash": row.protocol_hash,
                "registered_at": as_utc(row.registered_at),
                "first_cutoff_at": as_utc(row.first_cutoff_at),
                "last_cutoff_at": as_utc(row.last_cutoff_at),
                "last_window_end_at": as_utc(row.last_window_end_at),
                "label_deadline_at": as_utc(row.label_deadline_at),
                "finished_at": as_utc(row.finished_at),
                "conclusion": row.conclusion,
                "jobs": [
                    {
                        "job_id": job.job_id,
                        "kind": job.kind,
                        "scope_id": job.scope_id,
                        "cutoff_at": as_utc(job.cutoff_at),
                        "state": job.state,
                        "attempts": job.attempts,
                        "error_code": job.error_code,
                    }
                    for job in jobs
                ],
            }

    @staticmethod
    def prediction_payload(row: RiskPredictionRecord) -> RiskPathForecast:
        payload = RiskPathForecast.model_validate(
            checked_payload(row.payload_json, row.payload_hash)
        )
        verify_path_forecast(payload)
        manifest = PathFeatureManifest.model_validate(
            checked_payload(
                row.feature_manifest_json,
                row.feature_manifest_hash,
            )
        )
        if (
            payload.scope.scope_id,
            payload.model_version,
            payload.cutoff_at,
            payload.published_at,
            payload.model_hash,
        ) != (
            row.scope_id,
            row.method,
            as_utc(row.cutoff_at),
            as_utc(row.published_at),
            row.model_hash,
        ) or payload.feature_hash != canonical_hash(manifest.features.model_dump(mode="json")):
            raise ValueError("risk_path_prediction_identity_changed")
        return payload

    def prediction(self, prediction_id: str) -> dict:
        with self.database.session() as session:
            row = session.get(RiskPredictionRecord, prediction_id)
            if row is None:
                raise KeyError(prediction_id)
            payload = self.prediction_payload(row)
            return {
                "prediction_id": row.prediction_id,
                "trial_id": row.trial_id,
                "forecast": payload.model_dump(mode="json"),
                "feature_manifest": json.loads(row.feature_manifest_json),
            }

    def predictions(self, scope_id: str, *, trial_id: str | None = None, limit: int = 100) -> tuple:
        if not 1 <= limit <= 200:
            raise ValueError("risk_path_prediction_limit_invalid")
        with self.database.session() as session:
            query = session.query(RiskPredictionRecord).filter_by(scope_id=scope_id)
            if trial_id is not None:
                query = query.filter_by(trial_id=trial_id)
            rows = query.order_by(RiskPredictionRecord.cutoff_at.desc()).limit(limit).all()
            return tuple(
                {
                    "prediction_id": row.prediction_id,
                    "trial_id": row.trial_id,
                    "forecast": self.prediction_payload(row).model_dump(mode="json"),
                }
                for row in rows
            )

    def verified_observation(self, observation_id: str) -> PathObservationReceipt:
        with self.database.session() as session:
            row = session.get(RiskPathObservationRecord, observation_id)
            if row is None:
                raise KeyError(observation_id)
            payload = PathObservationReceipt.model_validate(
                checked_payload(
                    row.payload_json,
                    row.payload_hash,
                )
            )
            if (payload.scope.scope_id, payload.input_cutoff_at, payload.label_known_at) != (
                row.scope_id,
                as_utc(row.cutoff_at),
                as_utc(row.label_known_at),
            ):
                raise ValueError("risk_path_observation_identity_changed")
        verify_observation(payload)
        self.inputs.verify_observation_originals(payload)
        if not observation_complete(payload):
            raise ValueError("risk_path_observation_not_complete")
        return payload

    def score(self, trial_id: str) -> dict | None:
        with self.database.session() as session:
            row = session.query(RiskEvaluationReportRecord).filter_by(trial_id=trial_id).first()
            return checked_payload(row.payload_json, row.payload_hash) if row else None

    def verified_report(self, report_id: str) -> dict:
        with self.database.session() as session:
            row = session.get(RiskEvaluationReportRecord, report_id)
            if row is None:
                raise KeyError(report_id)
            payload = checked_payload(row.payload_json, row.payload_hash)
            if (
                payload["report_id"],
                payload["trial_id"],
                payload["report_version"],
                payload["protocol_hash"],
                aware_utc(datetime.fromisoformat(payload["computed_at"])),
            ) != (
                row.report_id,
                row.trial_id,
                row.report_version,
                row.protocol_hash,
                as_utc(row.computed_at),
            ):
                raise ValueError("risk_path_report_identity_changed")
        for ref in payload["prediction_refs"]:
            prediction = self.prediction(ref["prediction_id"])
            if prediction["forecast"]["payload_hash"] != ref["payload_hash"]:
                raise ValueError("risk_path_report_prediction_changed")
        for ref in payload["observation_refs"]:
            if not ref["complete"]:
                continue
            observation = self.verified_observation(ref["observation_id"])
            if observation.payload_hash != ref["payload_hash"]:
                raise ValueError("risk_path_report_observation_changed")
        return payload

    def read_report_alarm_score(self, report: dict) -> dict | None:
        """Read one exact stored review in a private SELECT-only snapshot, never latest."""
        ref = report.get("alarm_score_original")
        if ref is None:
            if report["score"].get("alarm_originals"):
                raise ValueError("risk_path_alarm_review_reference_missing")
            return None
        if self.forecast_alarm_original_reader is None:
            raise ValueError("risk_path_alarm_original_reader_unavailable")
        with self.database.session() as session:
            connection = session.connection()
            prior = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
            connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                value = self.forecast_alarm_original_reader(
                    session,
                    original_ref=ref["original_ref"],
                    original_hash=ref["original_hash"],
                )
            finally:
                connection.exec_driver_sql(f"PRAGMA query_only={int(prior)}")
                session.rollback()
        if (
            value["schema_version"] != "forecast-alarm-score.v1"
            or value["trial_id"] != report["trial_id"]
            or value["protocol_hash"] != report["protocol_hash"]
            or value["score_original_ref"] != ref
            or value["original_refs"] != report["score"]["alarm_originals"]
            or aware_utc(datetime.fromisoformat(value["known_at"]))
            > aware_utc(datetime.fromisoformat(report["computed_at"]))
        ):
            raise ValueError("risk_path_alarm_original_report_binding_invalid")
        return value

    def fitted_model(self, trial_id: str, scope_id: str) -> FrozenPathForecastModel | None:
        with self.database.session() as session:
            row = (
                session.query(RiskPathJobRecord)
                .filter_by(
                    trial_id=trial_id,
                    kind="fit",
                    scope_id=scope_id,
                )
                .one()
            )
            if row.state != "succeeded":
                return None
            result = checked_payload(row.result_json, row.result_hash)
            return FrozenPathForecastModel.model_validate(result["model"])

    @staticmethod
    def owns(session: Session, claim, now: datetime) -> RiskPathJobRecord | None:
        job = session.get(RiskPathJobRecord, claim.job_id)
        trial = session.get(RiskShadowTrialRecord, claim.trial_id)
        if (
            job is None
            or trial is None
            or trial.state in {"completed", "failed"}
            or (trial.state == "paused" and claim.kind != "finalize")
            or (
                job.state != "running"
                or job.lease_owner != claim.owner
                or job.lease_generation != claim.generation
                or job.lease_expires_at is None
                or as_utc(job.lease_expires_at) <= now
                or (job.trial_id, job.kind, job.scope_id, as_utc(job.cutoff_at))
                != (
                    claim.trial_id,
                    claim.kind,
                    claim.scope_id,
                    claim.cutoff_at,
                )
            )
        ):
            return None
        return job

    @staticmethod
    def finish_job(job: RiskPathJobRecord, result: dict, now: datetime) -> None:
        body = {**result, "payload_hash": canonical_hash(result)}
        job.result_json, job.result_hash = encoded(body), body["payload_hash"]
        job.state, job.completed_at = "succeeded", now
        job.lease_owner = job.lease_expires_at = None
        job.error_code = None

    def save_fit(self, claim, model: FrozenPathForecastModel, *, read_as_of: datetime) -> bool:
        with self.database.session() as session:
            if self.owns(session, claim, self.now()) is None:
                return False
        read_at = aware_utc(read_as_of)
        self.inputs.verify_training_originals(model)
        rebuilt = fit_path_forecast(
            model.scope,
            model.accepted,
            training_cutoff_at=model.training_cutoff_at,
            parameters=model.parameters,
        )
        for name in (
            "baseline",
            "conditional_low",
            "conditional_high",
            "training_vol_median",
            "candidate_training_n",
            "independent_day_blocks",
            "history_start_at",
        ):
            if getattr(model, name) != getattr(rebuilt, name):
                raise ValueError("risk_path_fit_estimator_changed")
        with self.database.session() as session:
            writer(session)
            now = self.now()
            job = self.owns(session, claim, now)
            if job is None:
                return False
            if job.kind != "fit":
                raise ValueError("risk_path_job_kind_invalid")
            protocol = self.protocol(session.get(RiskShadowTrialRecord, job.trial_id))
            if model.parameters != parse_path_parameters(protocol["parameters"]):
                raise ValueError("risk_path_fit_protocol_identity_invalid")
            if now > as_utc(job.deadline_at):
                job.state, job.error_code, job.completed_at = "expired", "fit_deadline_elapsed", now
                job.lease_owner = job.lease_expires_at = None
                return False
            if (
                read_at > as_utc(job.cutoff_at)
                or read_at > now
                or (
                    model.scope.scope_id != job.scope_id
                    or model.training_cutoff_at != as_utc(job.cutoff_at)
                )
            ):
                raise ValueError("risk_path_fit_identity_invalid")
            predict_path_forecast(
                model,
                cutoff_at=model.training_cutoff_at,
                published_at=model.training_cutoff_at,
                features=None,
            )
            self.finish_job(
                job,
                {
                    "model": model.model_dump(mode="json"),
                    "read_as_of": read_at.isoformat(),
                },
                now,
            )
        return True

    def publish_pair(
        self,
        claim: PathShadowClaim,
        model: FrozenPathForecastModel,
        manifest: PathFeatureManifest,
    ) -> bool:
        with self.database.session() as session:
            if self.owns(session, claim, self.now()) is None:
                return False
        # Re-authenticate the fixed cutoff readset outside the write transaction.
        current = self.inputs.feature_manifest(model.scope, claim.cutoff_at)
        if (manifest.features, manifest.originals) != (current.features, current.originals):
            raise ValueError("risk_path_feature_manifest_changed")
        manifest = current
        with self.database.session() as session:
            writer(session)
            now = self.now()
            job = self.owns(session, claim, now)
            if job is None:
                return False
            if job.kind != "forecast" or model.scope.scope_id != job.scope_id:
                raise ValueError("risk_path_job_kind_or_scope_invalid")
            cutoff = as_utc(job.cutoff_at)
            if now > as_utc(job.deadline_at):
                job.state, job.error_code, job.completed_at = "missed", "forecast_slot_elapsed", now
                job.lease_owner = job.lease_expires_at = None
                return False
            frozen = (
                session.query(RiskPathJobRecord)
                .filter_by(
                    trial_id=job.trial_id,
                    kind="fit",
                    scope_id=job.scope_id,
                )
                .one()
            )
            if (
                frozen.state != "succeeded"
                or frozen.result_json is None
                or frozen.result_hash is None
                or checked_payload(
                    frozen.result_json,
                    frozen.result_hash,
                )["model"]["model_hash"]
                != model.model_hash
            ):
                raise ValueError("risk_path_model_not_frozen_for_trial")
            refs = []
            consumption_refs = []
            for method in (model.parameters.baseline, model.parameters.candidate):
                if self.component_consumer is None:
                    forecast = predict_path_forecast(
                        model,
                        cutoff_at=cutoff,
                        published_at=now,
                        features=manifest.features,
                        model_version=method,
                    )
                else:
                    session.flush()
                    inputs = PathForecastConsumptionInput(
                        trial_id=job.trial_id,
                        job_id=job.job_id,
                        scope_id=job.scope_id,
                        model_version=method,
                        model=model,
                        cutoff_at=cutoff,
                        published_at=now,
                        features=manifest.features,
                        feature_manifest_hash=manifest.payload_hash,
                    )
                    payload = inputs.model_dump(mode="json")
                    request_id = f"model:{job.job_id}:{method}"
                    value, receipt = self.component_consumer(
                        session,
                        kind="model",
                        component_ref=method,
                        scope_id=job.scope_id,
                        request_id=request_id,
                        known_at=now,
                        cutoff_at=cutoff,
                        payload=payload,
                        fallback=predict_registered_path_forecast,
                    )
                    forecast = verify_consumed_path_forecast(value, inputs)
                    consumption_refs.append(
                        component_consumption_reference_in_session(
                            session,
                            receipt,
                            kind="model",
                            component_ref=method,
                            scope_id=job.scope_id,
                            request_id=request_id,
                            known_at=now,
                            payload=payload,
                            value=forecast.model_dump(mode="json"),
                        )
                    )
                identity = (
                    "prediction_"
                    + canonical_hash(
                        [
                            job.trial_id,
                            job.scope_id,
                            method,
                            cutoff.isoformat(),
                            TARGET,
                        ]
                    )[:48]
                )
                prior = session.get(RiskPredictionRecord, identity)
                if prior is not None and prior.payload_hash != forecast.payload_hash:
                    raise ValueError("risk_path_prediction_conflict")
                if prior is None:
                    session.add(
                        RiskPredictionRecord(
                            prediction_id=identity,
                            trial_id=job.trial_id,
                            scope_id=job.scope_id,
                            method=method,
                            cutoff_at=cutoff,
                            published_at=now,
                            horizon_seconds=86400,
                            target_version=TARGET,
                            model_hash=model.model_hash,
                            feature_manifest_json=encoded(manifest.model_dump(mode="json")),
                            feature_manifest_hash=manifest.payload_hash,
                            payload_json=encoded(forecast.model_dump(mode="json")),
                            payload_hash=forecast.payload_hash,
                        )
                    )
                refs.append({"prediction_id": identity, "payload_hash": forecast.payload_hash})
            session.flush()
            if self.now() > as_utc(job.deadline_at):
                raise ValueError("risk_path_publication_deadline_elapsed")
            result = {"prediction_refs": refs}
            trial = session.get(RiskShadowTrialRecord, job.trial_id)
            protocol = self.protocol(trial)
            if (
                protocol["parameters"]["protocol_version"] == "path-shadow-24h.v2"
                and self.forecast_alarm_publisher is not None
            ):
                result["forecast_alarm_refs"] = list(
                    self.forecast_alarm_publisher(
                        session,
                        request_id=f"forecast-alarm:{job.job_id}",
                        prediction_refs=tuple(refs),
                        known_at=now,
                    )
                )
                if self.now() > as_utc(job.deadline_at):
                    raise ValueError("risk_path_publication_deadline_elapsed")
            if consumption_refs:
                result["component_consumption_refs"] = consumption_refs
            self.finish_job(job, result, now)
        return True

    def save_observation(self, claim, receipt: PathObservationReceipt) -> bool:
        with self.database.session() as session:
            if self.owns(session, claim, self.now()) is None:
                return False
        verify_observation(receipt)
        self.inputs.verify_observation_originals(receipt)
        with self.database.session() as session:
            writer(session)
            now = self.now()
            job = self.owns(session, claim, now)
            if job is None:
                return False
            if job.kind != "label":
                raise ValueError("risk_path_job_kind_invalid")
            if now >= as_utc(job.deadline_at):
                job.state, job.error_code, job.completed_at = (
                    "expired",
                    "label_deadline_elapsed",
                    now,
                )
                job.lease_owner = job.lease_expires_at = None
                return False
            if (
                (receipt.scope.scope_id, receipt.input_cutoff_at)
                != (
                    job.scope_id,
                    as_utc(job.cutoff_at),
                )
                or receipt.path.window_end_at > now
                or receipt.label_known_at > now
            ):
                raise ValueError("risk_path_observation_job_identity_invalid")
            # Acceptance follows actual verification. A caller cannot backdate a label.
            receipt = seal_payload(receipt.model_copy(update={"label_known_at": now}))
            identity = "pathobs_" + receipt.payload_hash[:48]
            existing = (
                session.query(RiskPathObservationRecord)
                .filter_by(
                    scope_id=job.scope_id,
                    cutoff_at=receipt.input_cutoff_at,
                    calculator_version=receipt.calculator_version,
                    path_hash=receipt.path.manifest_hash,
                )
                .order_by(RiskPathObservationRecord.label_known_at)
                .first()
            )
            if existing is not None:
                receipt = PathObservationReceipt.model_validate(
                    checked_payload(
                        existing.payload_json,
                        existing.payload_hash,
                    )
                )
                identity = existing.observation_id
            else:
                session.add(
                    RiskPathObservationRecord(
                        observation_id=identity,
                        scope_id=job.scope_id,
                        cutoff_at=receipt.input_cutoff_at,
                        window_end_at=receipt.path.window_end_at,
                        calculator_version=receipt.calculator_version,
                        path_hash=receipt.path.manifest_hash,
                        label_known_at=now,
                        quality=receipt.path.quality,
                        payload_json=encoded(receipt.model_dump(mode="json")),
                        payload_hash=receipt.payload_hash,
                    )
                )
            result = {"observation_id": identity, "observation_hash": receipt.payload_hash}
            if observation_complete(receipt):
                self.finish_job(job, result, now)
            else:
                body = {**result, "payload_hash": canonical_hash(result)}
                job.result_json, job.result_hash = encoded(body), body["payload_hash"]
                job.state, job.error_code = "pending", "label_incomplete"
                job.next_attempt_at = min(now + timedelta(minutes=5), as_utc(job.deadline_at))
                job.lease_owner = job.lease_expires_at = None
        return True

    def finalize(self, claim) -> bool:
        with self.database.session() as session:
            writer(session)
            now = self.now()
            job = self.owns(session, claim, now)
            if job is None:
                return False
            if job.kind != "finalize":
                raise ValueError("risk_path_job_kind_invalid")
            trial = session.get(RiskShadowTrialRecord, job.trial_id)
            if now < as_utc(trial.label_deadline_at):
                raise ValueError("risk_path_finalize_before_fixed_deadline")
            protocol = self.protocol(trial)
            parameters = parse_path_parameters(protocol["parameters"])
            slots = tuple(
                (raw["scope_id"], as_utc(trial.first_cutoff_at) + timedelta(days=offset))
                for raw in protocol["scopes"]
                for offset in range(parameters.shadow_days)
            )
            prediction_rows = (
                session.query(RiskPredictionRecord).filter_by(trial_id=trial.trial_id).all()
            )
            forecasts = tuple(self.prediction_payload(row) for row in prediction_rows)
            labels, observation_refs = [], []
            label_jobs = (
                session.query(RiskPathJobRecord)
                .filter_by(
                    trial_id=trial.trial_id,
                    kind="label",
                )
                .all()
            )
            for label_job in label_jobs:
                if label_job.result_json is None:
                    continue
                reference = checked_payload(label_job.result_json, label_job.result_hash)
                row = session.get(RiskPathObservationRecord, reference["observation_id"])
                if row is None or row.payload_hash != reference["observation_hash"]:
                    raise ValueError("risk_path_label_job_reference_changed")
                receipt = PathObservationReceipt.model_validate(
                    checked_payload(
                        row.payload_json,
                        row.payload_hash,
                    )
                )
                labels.append(receipt)
                observation_refs.append(
                    {
                        "observation_id": row.observation_id,
                        "payload_hash": row.payload_hash,
                        "complete": observation_complete(receipt),
                    }
                )
            alarm_score, event_blocks = None, None
            report_version = REPORT
            if parameters.protocol_version == "path-shadow-24h.v2":
                report_version = REPORT_V2
                if self.forecast_alarm_score_reader is not None:
                    alarm_score = self.forecast_alarm_score_reader(
                        session,
                        trial_id=trial.trial_id,
                        as_of=now,
                    )
                    if alarm_score.get("schema_version") != "forecast-alarm-score.v1":
                        raise ValueError("risk_path_alarm_score_schema_invalid")
                    event_blocks = tuple(
                        MatchedEventBlock(
                            block_id=raw["block_id"],
                            cutoff_at=aware_utc(datetime.fromisoformat(raw["cutoff_at"])),
                            known_at=aware_utc(datetime.fromisoformat(raw["known_at"])),
                            original_ref=raw["original_ref"],
                            original_hash=raw["original_hash"],
                            scope_ids=tuple(raw["scope_ids"]),
                        )
                        for raw in alarm_score["matched_event_blocks"]
                    )
            scored_at = self.now()
            score = score_path_forecasts(
                forecasts,
                tuple(labels),
                expected_slots=slots,
                as_of=scored_at,
                parameters=parameters,
                event_blocks=event_blocks,
                alarm_score=alarm_score,
            )
            identity = "riskeval_" + canonical_hash([trial.trial_id, report_version])[:48]
            body = {
                "report_id": identity,
                "trial_id": trial.trial_id,
                "report_version": report_version,
                "computed_at": scored_at.isoformat(),
                "protocol_hash": trial.protocol_hash,
                "score": score,
                "prediction_refs": [
                    {
                        "prediction_id": row.prediction_id,
                        "payload_hash": row.payload_hash,
                    }
                    for row in prediction_rows
                ],
                "observation_refs": observation_refs,
            }
            if report_version == REPORT_V2:
                body["alarm_score_original"] = (
                    alarm_score["score_original_ref"] if alarm_score is not None else None
                )
            payload = {**body, "payload_hash": canonical_hash(body)}
            session.add(
                RiskEvaluationReportRecord(
                    report_id=identity,
                    trial_id=trial.trial_id,
                    report_version=report_version,
                    protocol_hash=trial.protocol_hash,
                    computed_at=scored_at,
                    payload_json=encoded(payload),
                    payload_hash=payload["payload_hash"],
                )
            )
            for pending in (
                session.query(RiskPathJobRecord).filter_by(trial_id=trial.trial_id).all()
            ):
                if pending.job_id != job.job_id and pending.state in {"pending", "running"}:
                    pending.state, pending.error_code, pending.completed_at = (
                        "missed" if pending.kind == "forecast" else "expired",
                        "fixed_trial_deadline_elapsed",
                        now,
                    )
                    pending.lease_generation += 1
                    pending.lease_owner = pending.lease_expires_at = None
            self.finish_job(
                job, {"report_id": identity, "report_hash": payload["payload_hash"]}, now
            )
            trial.state, trial.finished_at, trial.conclusion = "completed", now, score["conclusion"]
        return True
