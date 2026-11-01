"""Durable experimental forecast alarms, PC receipts, and original-based matching.

Dedicated roles in the existing immutable role-output ledger are namespaced
audit records, never invented research Runs or fact-change alerts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decision.forecast_alarm_matching import match_forecast_alarms
from ..decision.path_forecast_score import observation_complete, verify_observation
from ..persistence.db import Database, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.path_forecast_records import (
    RiskPathObservationRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from ..ports.forecast_alarm import (
    ALARM_POLICY,
    MINIMUM_FREQUENCY,
    AlarmOutcomeV1,
    ForecastAlarmListV1,
    ForecastAlarmReceiptV1,
    ForecastAlarmV1,
    ForecastAlarmViewV1,
)
from ..ports.market_path import aware_utc, canonical_hash, spot_scope
from ..ports.path_forecast import PathObservationReceipt, RiskPathForecast
from .market_path_store import MarketPathStore
from .path_forecast_history import MarketPathForecastInputs, seal_payload
from .risk_path_ledger import RiskPathLedger, checked_payload, encoded, writer

ALARM_ROLE = "forecast_alarm"
RECEIPT_ROLE = "forecast_alarm_receipt"
PUBLICATION_ROLE = "forecast_alarm_publication"
REVIEW_ROLE = "forecast_alarm_review"
EVENT_ROLE = "forecast_alarm_event"


def _sealed_dict(value: dict[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "payload_hash"}
    return {**body, "payload_hash": canonical_hash(body)}


class ForecastAlarmService:
    def __init__(
        self,
        database: Database,
        path_store: MarketPathStore | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database, self.clock = database, clock
        self.inputs = MarketPathForecastInputs(path_store, clock=clock) if path_store else None

    def now(self) -> datetime:
        return aware_utc(self.clock())

    @staticmethod
    def _read(row: RunRoleOutputRecord) -> dict[str, Any]:
        body = checked_payload(row.payload_json, row.payload_hash)
        if row.schema_version != body["schema_version"]:
            raise ValueError("forecast_alarm_audit_identity_changed")
        known = body.get("known_at", body.get("as_of"))
        if known is None or aware_utc(datetime.fromisoformat(known)) != as_utc(row.created_at):
            raise ValueError("forecast_alarm_audit_clock_changed")
        expected = "faudit_" + canonical_hash([row.role, row.run_id])[:48]
        if row.output_id != expected:
            raise ValueError("forecast_alarm_audit_key_changed")
        return body

    def _put(
        self, session: Session, role: str, key: str, body: dict[str, Any], *, create: bool = True
    ) -> dict[str, str]:
        identity = "faudit_" + canonical_hash([role, key])[:48]
        previous = session.get(RunRoleOutputRecord, identity)
        if previous is not None:
            if self._read(previous) != body:
                raise ValueError("forecast_alarm_immutable_request_conflict")
        else:
            if not create:
                raise ValueError("forecast_alarm_original_unavailable")
            known = body.get("known_at", body.get("as_of"))
            if not isinstance(known, str):
                raise ValueError("forecast_alarm_original_clock_missing")
            session.add(
                RunRoleOutputRecord(
                    output_id=identity,
                    run_id=key,
                    role=role,
                    schema_version=body["schema_version"],
                    payload_json=encoded(body),
                    payload_hash=body["payload_hash"],
                    created_at=aware_utc(datetime.fromisoformat(known)),
                )
            )
            session.flush()
        return {"original_ref": identity, "original_hash": body["payload_hash"]}

    def _alarm(self, session: Session, alarm_id: str, *, as_of: datetime) -> ForecastAlarmV1:
        key = "forecast-alarm:" + alarm_id
        identity = "faudit_" + canonical_hash([ALARM_ROLE, key])[:48]
        row = session.get(RunRoleOutputRecord, identity)
        if row is None:
            raise KeyError(alarm_id)
        alarm = ForecastAlarmV1.model_validate(self._read(row))
        if alarm.alarm_id != alarm_id or alarm.known_at > as_of:
            raise ValueError("forecast_alarm_not_yet_known")
        prediction = session.get(RiskPredictionRecord, alarm.prediction_id)
        if prediction is None or prediction.payload_hash != alarm.prediction_hash:
            raise ValueError("forecast_alarm_prediction_original_changed")
        forecast = RiskPathLedger.prediction_payload(prediction)
        frequency = next(
            x
            for x in forecast.frequencies
            if x.direction == alarm.direction and x.threshold == alarm.threshold
        )
        if (
            prediction.trial_id,
            forecast.scope.scope_id,
            forecast.model_version,
            forecast.cutoff_at,
            forecast.published_at,
            forecast.window_start_at,
            forecast.window_end_at,
            frequency.probability,
        ) != (
            alarm.trial_id,
            alarm.scope_id,
            alarm.method,
            alarm.cutoff_at,
            alarm.published_at,
            alarm.window_start_at,
            alarm.window_end_at,
            alarm.frequency,
        ):
            raise ValueError("forecast_alarm_prediction_identity_changed")
        return alarm

    def publish_pair_in_session(
        self,
        session: Session,
        *,
        request_id: str,
        prediction_refs: tuple[dict[str, Any], ...],
        known_at: datetime,
    ) -> tuple[dict[str, str], ...]:
        """Caller owns the publication physical writer; no model, HTTP or nested TX."""
        now = max(aware_utc(known_at), self.now())
        rows: list[tuple[RiskPredictionRecord, RiskPathForecast]] = []
        for ref in prediction_refs:
            row = session.get(RiskPredictionRecord, ref["prediction_id"])
            if row is None or row.payload_hash != ref["payload_hash"]:
                raise ValueError("forecast_alarm_publication_original_unavailable")
            rows.append((row, RiskPathLedger.prediction_payload(row)))
        if len(rows) != 2 or {x[1].model_version for x in rows} != {
            "BASE_EMPIRICAL_V1",
            "COND_VOL_V1",
        }:
            raise ValueError("forecast_alarm_pair_required")
        pair = {
            (
                x[0].trial_id,
                x[1].scope.scope_id,
                x[1].cutoff_at,
                x[1].window_start_at,
                x[1].window_end_at,
                x[1].feature_hash,
                x[1].model_hash,
                x[1].parameter_hash,
            )
            for x in rows
        }
        if len(pair) != 1:
            raise ValueError("forecast_alarm_pair_identity_mismatch")
        trial = session.get(RiskShadowTrialRecord, rows[0][0].trial_id)
        if trial is None or RiskPathLedger.protocol(trial).get("alarm_policy") != ALARM_POLICY:
            # Legacy trials retain their exact original no-dispatch policy.
            return ()
        namespace = "forecast-alarm-publication:" + canonical_hash(request_id)[:48]
        previous = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == namespace,
                RunRoleOutputRecord.role == PUBLICATION_ROLE,
            )
        )
        if previous is not None:
            body = self._read(previous)
            if body["request_id"] != request_id or body["prediction_refs"] != list(prediction_refs):
                raise ValueError("forecast_alarm_publication_request_conflict")
            return tuple(body["alarm_refs"])
        alarms, decisions = [], []
        for row, forecast in rows:
            for frequency in forecast.frequencies:
                reason = (
                    "prediction_late"
                    if forecast.publication_status != "on_time"
                    else "alarm_acceptance_late"
                    if now > forecast.window_start_at
                    else "frequency_unknown"
                    if frequency.probability is None
                    else "frequency_below_registered_gate"
                    if frequency.probability < MINIMUM_FREQUENCY
                    else None
                )
                decisions.append(
                    {
                        "prediction_id": row.prediction_id,
                        "direction": frequency.direction,
                        "threshold": str(frequency.threshold),
                        "reason": reason,
                    }
                )
                if reason is not None:
                    continue
                probability = frequency.probability
                if probability is None:
                    raise ValueError("forecast_alarm_frequency_unavailable")
                alarm_id = (
                    "falarm_"
                    + canonical_hash(
                        [
                            row.prediction_id,
                            row.payload_hash,
                            frequency.direction,
                            str(frequency.threshold),
                            ALARM_POLICY,
                        ]
                    )[:48]
                )
                alarm = seal_payload(
                    ForecastAlarmV1(
                        alarm_id=alarm_id,
                        trial_id=row.trial_id,
                        prediction_id=row.prediction_id,
                        prediction_hash=row.payload_hash,
                        method=forecast.model_version,
                        scope_id=forecast.scope.scope_id,
                        cutoff_at=forecast.cutoff_at,
                        published_at=forecast.published_at,
                        window_start_at=forecast.window_start_at,
                        window_end_at=forecast.window_end_at,
                        direction=frequency.direction,
                        threshold=frequency.threshold,
                        frequency=probability,
                        known_at=now,
                        payload_hash="0" * 64,
                    )
                )
                ref = self._put(
                    session, ALARM_ROLE, "forecast-alarm:" + alarm_id, alarm.model_dump(mode="json")
                )
                alarms.append({"alarm_id": alarm_id, **ref})
        self._put(
            session,
            PUBLICATION_ROLE,
            namespace,
            _sealed_dict(
                {
                    "schema_version": "forecast-alarm-publication.v1",
                    "request_id": request_id,
                    "trial_id": trial.trial_id,
                    "prediction_refs": list(prediction_refs),
                    "alarm_refs": alarms,
                    "decisions": decisions,
                    "known_at": now.isoformat(),
                    "probability_qualified": False,
                }
            ),
        )
        return tuple(alarms)

    def receipt(
        self,
        alarm_id: str,
        *,
        owner_id: str,
        request_id: str,
        receipt_kind: Literal["displayed", "visible", "acknowledged"],
        parent_receipt_id: str | None = None,
        visible_duration_ms: int | None = None,
    ) -> ForecastAlarmReceiptV1:
        now = self.now()
        with self.database.session() as session:
            writer(session)
            alarm = self._alarm(session, alarm_id, as_of=now)
            receipt_id = "fareceipt_" + canonical_hash([owner_id, request_id])[:48]
            key = "forecast-alarm-receipt:" + receipt_id
            previous = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == key, RunRoleOutputRecord.role == RECEIPT_ROLE
                )
            )
            if previous is not None:
                original = ForecastAlarmReceiptV1.model_validate(self._read(previous))
                if (
                    original.alarm_id,
                    original.owner_id,
                    original.request_id,
                    original.receipt_kind,
                    original.parent_receipt_id,
                    original.visible_duration_ms,
                ) != (
                    alarm_id,
                    owner_id,
                    request_id,
                    receipt_kind,
                    parent_receipt_id,
                    visible_duration_ms,
                ):
                    raise ValueError("forecast_alarm_receipt_request_conflict")
                return original
            parent_hash = None
            if receipt_kind != "displayed":
                parent = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id
                        == "forecast-alarm-receipt:" + str(parent_receipt_id),
                        RunRoleOutputRecord.role == RECEIPT_ROLE,
                    )
                )
                if parent is None:
                    raise ValueError("forecast_alarm_parent_receipt_unavailable")
                source = ForecastAlarmReceiptV1.model_validate(self._read(parent))
                parent_hash = source.payload_hash
                if (
                    source.alarm_id != alarm_id
                    or source.owner_id != owner_id
                    or source.known_at > now
                    or source.receipt_kind
                    != ("displayed" if receipt_kind == "visible" else "visible")
                ):
                    raise ValueError("forecast_alarm_parent_receipt_mismatch")
            body = seal_payload(
                ForecastAlarmReceiptV1(
                    receipt_id=receipt_id,
                    alarm_id=alarm_id,
                    alarm_hash=alarm.payload_hash,
                    owner_id=owner_id,
                    request_id=request_id,
                    receipt_kind=receipt_kind,
                    known_at=now,
                    parent_receipt_id=parent_receipt_id,
                    parent_receipt_hash=parent_hash,
                    visible_duration_ms=visible_duration_ms,
                    payload_hash="0" * 64,
                )
            )
            self._put(session, RECEIPT_ROLE, key, body.model_dump(mode="json"))
            return body

    def list(
        self, scope_id: str, *, as_of: datetime, owner_id: str | None = None, limit: int = 50
    ) -> ForecastAlarmListV1:
        if (
            scope_id not in {spot_scope(x).scope_id for x in ("BTC-USDT", "ETH-USDT")}
            or not 1 <= limit <= 100
        ):
            raise ValueError("forecast_alarm_scope_or_limit_invalid")
        clock = aware_utc(as_of)
        with self.database.session() as session:
            rows = session.scalars(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.role == ALARM_ROLE, RunRoleOutputRecord.created_at <= clock
                )
                .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id)
            ).all()
            alarms = [ForecastAlarmV1.model_validate(self._read(row)) for row in rows]
            selected = [
                self._alarm(session, x.alarm_id, as_of=clock)
                for x in alarms
                if x.scope_id == scope_id
            ][:limit]
            receipts = [
                ForecastAlarmReceiptV1.model_validate(self._read(row))
                for row in session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.role == RECEIPT_ROLE,
                        RunRoleOutputRecord.created_at <= clock,
                    )
                ).all()
            ]
            items = []
            for alarm in selected:
                own = [
                    x for x in receipts if x.alarm_id == alarm.alarm_id and x.owner_id == owner_id
                ]
                if any(x.alarm_hash != alarm.payload_hash for x in own):
                    raise ValueError("forecast_alarm_receipt_original_changed")
                items.append(
                    ForecastAlarmViewV1(
                        alarm=alarm,
                        display_status="displayed"
                        if any(x.receipt_kind == "displayed" for x in own)
                        else "not_recorded",
                        exposure_status="visible"
                        if any(x.receipt_kind == "visible" for x in own)
                        else "not_recorded",
                        acknowledged=any(x.receipt_kind == "acknowledged" for x in own),
                    )
                )
            return ForecastAlarmListV1(scope_id=scope_id, as_of=clock, items=tuple(items))

    def score_originals(self, *, trial_id: str, as_of: datetime) -> dict[str, Any]:
        with self.database.session() as session:
            writer(session)
            return self.score_in_session(session, trial_id=trial_id, as_of=as_of)

    def score_in_session(
        self,
        session: Session,
        *,
        trial_id: str,
        as_of: datetime,
        _record: bool = True,
        _accepted_at: datetime | None = None,
    ) -> dict[str, Any]:
        clock = aware_utc(as_of)
        if clock > self.now():
            raise ValueError("forecast_alarm_score_future_cutoff")
        accepted_at = aware_utc(_accepted_at) if _accepted_at is not None else self.now()
        if accepted_at < clock:
            raise ValueError("forecast_alarm_review_acceptance_backdated")
        trial = session.get(RiskShadowTrialRecord, trial_id)
        if trial is None:
            raise KeyError(trial_id)
        protocol = RiskPathLedger.protocol(trial)
        if protocol.get("alarm_policy") != ALARM_POLICY:
            raise ValueError("forecast_alarm_policy_not_registered")
        slots = tuple(
            (raw["scope_id"], as_utc(trial.first_cutoff_at) + timedelta(days=offset))
            for raw in protocol["scopes"]
            for offset in range(protocol["parameters"]["shadow_days"])
        )
        alarm_rows = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.role == ALARM_ROLE, RunRoleOutputRecord.created_at <= clock
            )
        ).all()
        alarms = tuple(
            self._alarm(session, x["alarm_id"], as_of=clock)
            for row in alarm_rows
            if (x := self._read(row))["trial_id"] == trial_id
        )
        alarm_ids = {x.alarm_id for x in alarms}
        receipts = tuple(
            ForecastAlarmReceiptV1.model_validate(body)
            for row in session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.role == RECEIPT_ROLE,
                    RunRoleOutputRecord.created_at <= clock,
                )
            ).all()
            if (body := self._read(row))["alarm_id"] in alarm_ids
        )
        latest: dict[tuple[str, datetime], RiskPathObservationRecord] = {}
        for row in session.scalars(
            select(RiskPathObservationRecord).where(
                RiskPathObservationRecord.label_known_at <= clock
            )
        ).all():
            key = row.scope_id, as_utc(row.cutoff_at)
            if key not in slots:
                continue
            prior = latest.get(key)
            if (
                prior is not None
                and as_utc(prior.label_known_at) == as_utc(row.label_known_at)
                and prior.payload_hash != row.payload_hash
            ):
                raise ValueError("forecast_alarm_conflicting_label_time")
            if prior is None or as_utc(row.label_known_at) > as_utc(prior.label_known_at):
                latest[key] = row
        outcomes, label_refs, unavailable = [], [], []
        for row in latest.values():
            label = PathObservationReceipt.model_validate(
                checked_payload(row.payload_json, row.payload_hash)
            )
            verify_observation(label)
            if (
                label.scope.scope_id,
                label.input_cutoff_at,
                label.label_known_at,
                label.path.window_end_at,
                label.path.manifest_hash,
                label.path.quality,
            ) != (
                row.scope_id,
                as_utc(row.cutoff_at),
                as_utc(row.label_known_at),
                as_utc(row.window_end_at),
                row.path_hash,
                row.quality,
            ):
                raise ValueError("forecast_alarm_label_original_identity_changed")
            if self.inputs is None:
                unavailable.append(
                    {
                        "original_ref": row.observation_id,
                        "reason": "label_original_reader_unavailable",
                    }
                )
                continue
            self.inputs.verify_observation_originals(label)
            complete = observation_complete(label)
            label_refs.append(
                {"original_ref": row.observation_id, "original_hash": label.payload_hash}
            )
            for crossing in label.path.crossings:
                outcomes.append(
                    AlarmOutcomeV1(
                        scope_id=label.scope.scope_id,
                        cutoff_at=label.input_cutoff_at,
                        window_start_at=label.path.window_start_at,
                        window_end_at=label.path.window_end_at,
                        direction=crossing.direction,
                        threshold=crossing.threshold,
                        event_presence=crossing.event_presence if complete else None,
                        firstness=crossing.firstness if complete else "unknown",
                        interval_start_at=crossing.interval_start_at,
                        interval_end_at=crossing.interval_end_at,
                        label_known_at=label.label_known_at,
                        original_ref=row.observation_id,
                        original_hash=label.payload_hash,
                    )
                )
        score = match_forecast_alarms(
            alarms, tuple(outcomes), receipts, expected_slots=slots, as_of=clock
        )
        event_blocks = []
        for cluster in score.pop("event_clusters"):
            body = _sealed_dict(
                {
                    "schema_version": "forecast-alarm-event.v1",
                    **cluster,
                    "known_at": accepted_at.isoformat(),
                    "label_known_at": cluster["known_at"],
                    "trial_id": trial_id,
                }
            )
            event_key = (
                "forecast-alarm-event:"
                + canonical_hash(
                    [trial_id, cluster["block_id"], clock.isoformat(), accepted_at.isoformat()]
                )[:48]
            )
            ref = self._put(session, EVENT_ROLE, event_key, body, create=_record)
            event_blocks.append(
                {
                    "block_id": cluster["block_id"],
                    "cutoff_at": cluster["cutoff_at"],
                    "known_at": accepted_at.isoformat(),
                    "scope_ids": sorted({member["scope_id"] for member in cluster["members"]}),
                    **ref,
                }
            )
        score.update(
            {
                "trial_id": trial_id,
                "protocol_hash": trial.protocol_hash,
                "alarm_policy": protocol.get("alarm_policy"),
                "matched_event_blocks": event_blocks,
                "label_original_refs": label_refs,
                "original_reader_exclusions": unavailable,
                "alarm_original_refs": [
                    {"alarm_id": x.alarm_id, "original_hash": x.payload_hash} for x in alarms
                ],
                "receipt_original_refs": [
                    {"receipt_id": x.receipt_id, "original_hash": x.payload_hash} for x in receipts
                ],
            }
        )
        score["known_at"] = accepted_at.isoformat()
        score["original_refs"] = [
            {"original_ref": x["original_ref"], "original_hash": x["original_hash"]}
            for x in event_blocks
        ]
        body = _sealed_dict(score)
        review_key = (
            "forecast-alarm-review:"
            + canonical_hash([trial_id, clock.isoformat(), accepted_at.isoformat()])[:48]
        )
        ref = self._put(session, REVIEW_ROLE, review_key, body, create=_record)
        return {**body, "score_original_ref": ref, "original_refs": [*score["original_refs"], ref]}

    def verify_score_original(self, original_ref: str, original_hash: str) -> dict[str, Any]:
        with self.database.session() as session:
            return self.read_score_in_session(
                session, original_ref=original_ref, original_hash=original_hash
            )

    def read_score_in_session(
        self, session: Session, *, original_ref: str, original_hash: str
    ) -> dict[str, Any]:
        """Only SELECT/recompute the frozen input cutoff; never create a new audit."""
        row = session.get(RunRoleOutputRecord, original_ref)
        if row is None or row.role != REVIEW_ROLE or row.payload_hash != original_hash:
            raise ValueError("forecast_alarm_review_original_unavailable")
        body = self._read(row)
        result = self.score_in_session(
            session,
            trial_id=body["trial_id"],
            as_of=aware_utc(datetime.fromisoformat(body["as_of"])),
            _record=False,
            _accepted_at=aware_utc(datetime.fromisoformat(body["known_at"])),
        )
        if result["payload_hash"] != original_hash:
            raise ValueError("forecast_alarm_review_recomputation_changed")
        return result
