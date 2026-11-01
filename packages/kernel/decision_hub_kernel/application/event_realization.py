"""Authenticate published forecasts and actuals, then append one scenario result.

Preparation performs native full reads and archive I/O in a read transaction.
The writer checks the issued preparation's original-row guards and parent CAS.
"""
from dataclasses import dataclass
from datetime import datetime
import json
from weakref import WeakKeyDictionary

from sqlalchemy import func, select

from ..decision.cpi_event_scenario import band
from ..persistence.db import (
    ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc, utcnow,
)
from ..persistence.event_realization_records import EventRealizationRecord as Record
from ..persistence.event_scenario_records import EventScenarioForecastRecord as Forecast
from ..persistence.event_scenario_records import EventScenarioJobRecord as Job
from ..ports.bls_cpi import BLS_SOURCE_ID, BlsCpiMetricPayload
from ..ports.cpi_pre_event import CpiContextSource
from ..ports.event_realization import (
    METRICS, EventRealizedMetric, EventRealizationSnapshot, seal_event_realization,
)
from ..ports.market_path import aware_utc, canonical_hash
from .bls_cpi_original import read_bls_cpi_original_in_session
from .cpi_pre_event_context import _archive
from .cpi_schedule_original import read_cpi_schedule_original_in_session
from .event_scenario_autoproduction import read_event_owned_scenario_in_session


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _semantic(payload):
    # A changed capture or an unused revised prior does not revise the label.
    return canonical_hash({key: value for key, value in payload.model_dump(mode="json").items()
        if key in {"metric_id", "series_id", "reference_period", "seasonal_adjustment", "unit",
            "formula_version", "current_index", "basis_index", "actual_value"}})


def _metric(session, original):
    payload = original.payload
    row = session.get(ObservationRecord, original.observation_id, populate_existing=True)
    return EventRealizedMetric(metric_id=payload.metric_id, reference_period=payload.reference_period,
        series_id=payload.series_id, seasonal_adjustment=payload.seasonal_adjustment,
        value=payload.actual_value, semantic_hash=_semantic(payload),
        missing_reasons=tuple(r for r in payload.missing_reasons if r.startswith("actual_")),
        source=CpiContextSource(observation_id=row.observation_id,
            content_hash=row.content_hash, known_at=original.known_at))


def _actuals(session, forecast, *, archive, cutoff_at):
    # Admission time determines revision order; received time alone can reorder
    # overlapping HTTP polls. Exact period and all four metric types are needed.
    query = (select(ObservationRecord)
        .join(SourcePollAttemptRecord,
            SourcePollAttemptRecord.attempt_id == func.json_extract(ObservationRecord.text, "$.attempt_id"))
        .where(ObservationRecord.source_id == BLS_SOURCE_ID,
            ObservationRecord.received_at <= cutoff_at,
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= cutoff_at,
            func.json_extract(ObservationRecord.text, "$.reference_period") == forecast.reference_period)
        .order_by(SourcePollAttemptRecord.completed_at.desc(),
            ObservationRecord.received_at.desc(), ObservationRecord.observation_id)
        .execution_options(yield_per=32))
    result = {}
    for row in session.scalars(query):
        payload = BlsCpiMetricPayload.model_validate_json(row.text)
        if payload.metric_id in result:
            continue
        original = read_bls_cpi_original_in_session(session, row.observation_id,
            archive=archive, cutoff_at=cutoff_at)
        if original.known_at < forecast.scheduled_at:
            raise ValueError("event_realization_actual_known_before_T0")
        # A newer incomplete/corrected original must not borrow an older value.
        if payload.actual_value is None and payload.metric_id in {"headline_mom", "core_mom"}:
            return None
        result[payload.metric_id] = _metric(session, original)
        if len(result) == 4:
            return tuple(result[k] for k in METRICS)
    return None


def _latest_row(session, forecast_id, *, cutoff_at=None):
    query = select(Record).where(Record.forecast_id == forecast_id)
    if cutoff_at is not None:
        query = query.where(Record.recorded_at <= cutoff_at, Record.known_at <= cutoff_at)
    return session.scalar(query.order_by(Record.recorded_at.desc(), Record.realization_id.desc()).limit(1)
        .execution_options(populate_existing=True))


def _same_actuals(left, right):
    return tuple((m.metric_id, m.semantic_hash) for m in left) == tuple((m.metric_id, m.semantic_hash) for m in right)


def _build(forecast, metrics, *, as_of, recorded_at, parent):
    headline = band(metrics[0].value, forecast.headline_baseline, forecast.tolerance_pp)
    core = band(metrics[1].value, forecast.core_baseline, forecast.tolerance_pp)
    identity = canonical_hash({"forecast_hash": forecast.payload_hash,
        "actuals": [(m.metric_id, m.semantic_hash) for m in metrics], "revision_parent_ref": parent})
    return seal_event_realization(dict(realization_id="event-realization:" + identity,
        event_key=forecast.event_key, forecast_id=forecast.forecast_id,
        owner_forecast_hash=forecast.payload_hash, reference_period=forecast.reference_period,
        scheduled_at=forecast.scheduled_at, as_of=as_of, known_at=max(m.source.known_at for m in metrics),
        recorded_at=recorded_at, schedule_source=forecast.input_context.schedule_source,
        headline_baseline=forecast.headline_baseline, core_baseline=forecast.core_baseline,
        tolerance_pp=forecast.tolerance_pp, metrics=metrics, headline_band=headline, core_band=core,
        scenario_id=f"cpi:headline_{headline}:core_{core}", input_hash=identity,
        revision_parent_ref=parent))


def _validate_native(row, value, cutoff):
    body = value.model_dump(mode="json")
    if (row.realization_id != value.realization_id or row.event_key != value.event_key
        or row.forecast_id != value.forecast_id or row.owner_forecast_hash != value.owner_forecast_hash
        or row.reference_period != value.reference_period or row.schema_version != value.schema_version
        or row.input_hash != value.input_hash or row.revision_parent_ref != value.revision_parent_ref
        or (as_utc(row.scheduled_at), as_utc(row.known_at), as_utc(row.recorded_at))
            != (value.scheduled_at, value.known_at, value.recorded_at)
        or row.payload_json != _json(body) or row.payload_hash != canonical_hash(body)
        or value.payload_hash != canonical_hash(value.model_dump(mode="json", exclude={"payload_hash"}))
        or value.recorded_at > cutoff or value.known_at > cutoff):
        raise ValueError("event_realization_native_original_invalid")


def read_event_realization_in_session(session, realization_id, *, cutoff_at, archive=None):
    archive = archive or _archive()
    cutoff = aware_utc(cutoff_at)
    row = session.get(Record, realization_id, populate_existing=True)
    if row is None:
        raise ValueError("event_realization_original_missing")
    value = EventRealizationSnapshot.model_validate_json(row.payload_json)
    _validate_native(row, value, cutoff)
    forecast = read_event_owned_scenario_in_session(session, value.forecast_id,
        archive=archive, cutoff_at=value.as_of)
    if forecast.payload_hash != value.owner_forecast_hash:
        raise ValueError("event_realization_forecast_original_changed")
    schedule = read_cpi_schedule_original_in_session(session, value.schedule_source.observation_id,
        archive=archive, cutoff_at=forecast.as_of)
    if ((schedule.payload.event_key, schedule.payload.reference_period, schedule.payload.scheduled_at)
        != (value.event_key, value.reference_period, value.scheduled_at)
        or schedule.known_at != value.schedule_source.known_at):
        raise ValueError("event_realization_schedule_original_crosswired")
    metrics = _actuals(session, forecast, archive=archive, cutoff_at=value.as_of)
    if metrics != value.metrics or _build(forecast, metrics, as_of=value.as_of,
        recorded_at=value.recorded_at, parent=value.revision_parent_ref) != value:
        raise ValueError("event_realization_actual_original_changed")
    if value.revision_parent_ref:
        parent = session.get(Record, value.revision_parent_ref, populate_existing=True)
        if parent is None:
            raise ValueError("event_realization_revision_parent_missing")
        prior = EventRealizationSnapshot.model_validate_json(parent.payload_json)
        _validate_native(parent, prior, value.recorded_at)
        if (prior.forecast_id != value.forecast_id or prior.event_key != value.event_key
            or prior.owner_forecast_hash != value.owner_forecast_hash
            or prior.recorded_at >= value.recorded_at or prior.known_at > value.known_at
            or _same_actuals(prior.metrics, value.metrics)):
            raise ValueError("event_realization_revision_lineage_invalid")
    return value


def read_latest_event_realization_for_forecast_in_session(session, forecast_id, *, cutoff_at, archive=None):
    cutoff = aware_utc(cutoff_at)
    row = _latest_row(session, forecast_id, cutoff_at=cutoff)
    return read_event_realization_in_session(session, row.realization_id,
        cutoff_at=cutoff, archive=archive) if row else None


def read_latest_event_realization_for_event_in_session(session, event_key, *, cutoff_at, archive=None):
    cutoff = aware_utc(cutoff_at)
    row = session.scalar(select(Record).where(Record.event_key == event_key,
        Record.recorded_at <= cutoff, Record.known_at <= cutoff)
        .order_by(Record.recorded_at.desc(), Record.realization_id.desc()).limit(1))
    return read_event_realization_in_session(session, row.realization_id,
        cutoff_at=cutoff, archive=archive) if row else None


_TABLES = {"observation": ObservationRecord, "source_attempt": SourcePollAttemptRecord,
    "raw_capture": RawCaptureRecord, "forecast": Forecast, "forecast_job": Job, "realization": Record}


def _row_hash(row):
    return canonical_hash({column.name: as_utc(value).isoformat() if isinstance(value, datetime) else value
        for column in row.__table__.columns for value in (getattr(row, column.name),)})


def _guard(session, forecast, metrics, parent):
    keys = {(table, key) for table, key, _ in forecast.source_guard}
    keys.update({("forecast", forecast.forecast_id), ("forecast_job", forecast.job_id)})
    if parent:
        keys.add(("realization", parent))
    for metric in metrics:
        row = session.get(ObservationRecord, metric.source.observation_id, populate_existing=True)
        payload = BlsCpiMetricPayload.model_validate_json(row.text)
        keys.update({("observation", row.observation_id), ("source_attempt", payload.attempt_id),
            ("raw_capture", payload.capture_id)})
    return tuple((table, key, _row_hash(session.get(_TABLES[table], key, populate_existing=True)))
        for table, key in sorted(keys))


@dataclass(frozen=True, eq=False)
class PreparedEventRealization:
    snapshot: EventRealizationSnapshot
    source_guard: tuple
    expected_parent_ref: str | None
    existing: bool = False


_ISSUED = WeakKeyDictionary()


def prepare_event_realization_in_session(session, forecast_id, *, cutoff_at, recorded_at=None, archive=None):
    archive = archive or _archive()
    cutoff = aware_utc(cutoff_at)
    recorded = aware_utc(recorded_at or utcnow())
    if recorded < cutoff:
        raise ValueError("event_realization_recorded_before_cutoff")
    # Pin source selection and its guards in the same physical read snapshot.
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    forecast = read_event_owned_scenario_in_session(session, forecast_id, archive=archive, cutoff_at=cutoff)
    if cutoff < forecast.scheduled_at:
        return None
    metrics = _actuals(session, forecast, archive=archive, cutoff_at=cutoff)
    if metrics is None:
        return None
    latest = _latest_row(session, forecast_id)
    prior = read_event_realization_in_session(session, latest.realization_id,
        cutoff_at=recorded, archive=archive) if latest else None
    if prior and _same_actuals(prior.metrics, metrics):
        prepared = PreparedEventRealization(prior, _guard(session, forecast, metrics, prior.realization_id),
            prior.realization_id, True)
    else:
        if prior and (recorded <= prior.recorded_at or max(m.source.known_at for m in metrics) < prior.known_at):
            raise ValueError("event_realization_revision_clock_invalid")
        value = _build(forecast, metrics, as_of=cutoff, recorded_at=recorded,
            parent=prior.realization_id if prior else None)
        prepared = PreparedEventRealization(value, _guard(session, forecast, metrics,
            prior.realization_id if prior else None), prior.realization_id if prior else None)
    _ISSUED[prepared] = canonical_hash([prepared.snapshot.payload_hash, prepared.source_guard,
        prepared.expected_parent_ref, prepared.existing])
    return prepared


def commit_prepared_event_realization_in_session(session, prepared, *, archive=None):
    if prepared is None:
        return None
    issued = _ISSUED.get(prepared)
    if issued != canonical_hash([prepared.snapshot.payload_hash, prepared.source_guard,
        prepared.expected_parent_ref, prepared.existing]):
        raise ValueError("event_realization_preparation_not_issued")
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # Serialize parent CAS even when called without the worker's explicit
        # writer transaction; legacy SQLite SELECT does not begin one itself.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    for table, key, digest in prepared.source_guard:
        row = session.get(_TABLES[table], key, populate_existing=True)
        if row is None or _row_hash(row) != digest:
            raise ValueError("event_realization_source_guard_changed")
    value = prepared.snapshot
    latest = _latest_row(session, value.forecast_id)
    if (latest.realization_id if latest else None) != prepared.expected_parent_ref:
        # A peer committed exactly this original while our read completed.
        if latest and latest.realization_id == value.realization_id:
            current = EventRealizationSnapshot.model_validate_json(latest.payload_json)
            _validate_native(latest, current, value.recorded_at)
            return current
        raise ValueError("event_realization_parent_changed")
    if prepared.existing:
        return value
    body = value.model_dump(mode="json")
    session.add(Record(realization_id=value.realization_id, event_key=value.event_key,
        forecast_id=value.forecast_id, owner_forecast_hash=value.owner_forecast_hash,
        reference_period=value.reference_period, scheduled_at=value.scheduled_at,
        known_at=value.known_at, recorded_at=value.recorded_at, input_hash=value.input_hash,
        revision_parent_ref=value.revision_parent_ref, schema_version=value.schema_version,
        payload_json=_json(body), payload_hash=canonical_hash(body)))
    session.flush()
    return value


def freeze_event_realization_in_session(session, forecast_id, *, cutoff_at, recorded_at=None, archive=None):
    prepared = prepare_event_realization_in_session(session, forecast_id, cutoff_at=cutoff_at,
        recorded_at=recorded_at, archive=archive)
    return commit_prepared_event_realization_in_session(session, prepared, archive=archive)


def event_realization_user_view(value, *, event_key=None, scheduled_at=None, cutoff_at=None):
    if value is None:
        cutoff = aware_utc(cutoff_at) if cutoff_at else utcnow()
        return {"state": "awaiting_release" if scheduled_at and cutoff < aware_utc(scheduled_at)
            else "awaiting_actual", "event_key": event_key, "realization_id": None,
            "scenario_id": None, "known_at": None}
    return {"state": "realized", **value.model_dump(mode="json")}
