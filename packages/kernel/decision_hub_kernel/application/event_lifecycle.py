"""Advance saved event preparations on the existing realtime worker lane."""
from dataclasses import dataclass
from datetime import timedelta
import re

from sqlalchemy import func, select

from ..persistence.db import ObservationRecord, SourcePollAttemptRecord, as_utc, utcnow
from ..persistence.event_scenario_records import EventScenarioForecastRecord as Forecast
from ..ports.bls_cpi import BLS_SOURCE_ID
from ..ports.market_path import aware_utc, canonical_hash
from .cpi_pre_event_context import _archive
from .event_coverage import EventCoverageService
from .event_opportunity_monitor import EventOpportunityMonitorService
from .event_outcome import EventOutcomeService
from .event_read_scope import native_event_read_scope
from .event_realization import (
    commit_prepared_event_realization_in_session,
    prepare_event_realization_in_session,
)
from .market_path_store import MarketPathStore


@dataclass(frozen=True)
class EventLifecycleReport:
    coverage_slots: int = 0
    realizations_written: int = 0
    evaluations_written: int = 0
    outcomes_written: int = 0
    errors: tuple[str, ...] = ()


def _error(stage, exc):
    code = getattr(exc, 'error_code', None) or str(exc).splitlines()[0]
    if not re.fullmatch(r'[a-zA-Z0-9_:.-]{1,128}', code or ''):
        code = type(exc).__name__.lower()
    return f'{stage}:{code}'


class EventLifecycleProcessor:
    def __init__(self, database, *, feature_root, archive=None, clock=utcnow):
        self.database, self.clock = database, clock
        self.archive = archive or _archive()
        self.coverage = EventCoverageService(database, clock=clock)
        self.monitor = EventOpportunityMonitorService(database,
            MarketPathStore(database, self.archive, clock=clock),
            feature_root=feature_root, archive=self.archive, clock=clock)
        self.outcomes = EventOutcomeService(database, archive=self.archive, clock=clock)
        # Only completed authentication is memoized. On restart the native
        # reader runs again; a new official observation retries immediately.
        self._realized_inputs = {}

    def _realize_due(self, *, max_events=4):
        at = aware_utc(self.clock())
        with self.database.session() as session:
            rows = session.execute(select(Forecast.forecast_id, Forecast.event_key,
                Forecast.reference_period, Forecast.scheduled_at).where(
                    Forecast.published_at <= at, Forecast.published_at < Forecast.scheduled_at,
                    Forecast.scheduled_at <= at,
                    Forecast.scheduled_at >= at - timedelta(days=30),
                ).order_by(Forecast.scheduled_at.desc(), Forecast.published_at.desc(),
                    Forecast.forecast_id)).all()
            latest = {}
            for row in rows:
                latest.setdefault(row.event_key, row)
            targets = []
            for row in list(latest.values())[:max_events]:
                originals = session.execute(select(ObservationRecord.observation_id,
                    ObservationRecord.content_hash, ObservationRecord.text,
                    SourcePollAttemptRecord.completed_at).join(SourcePollAttemptRecord,
                        SourcePollAttemptRecord.attempt_id == func.json_extract(ObservationRecord.text, '$.attempt_id'))
                    .where(ObservationRecord.source_id == BLS_SOURCE_ID,
                        ObservationRecord.received_at <= at,
                        SourcePollAttemptRecord.status == 'succeeded',
                        SourcePollAttemptRecord.completed_at <= at,
                        SourcePollAttemptRecord.completed_at >= row.scheduled_at,
                        func.json_extract(ObservationRecord.text, '$.reference_period') == row.reference_period)
                    .order_by(ObservationRecord.observation_id)).all()
                if not originals:
                    continue
                signature = canonical_hash([[r[0], r[1], r[2], as_utc(r[3]).isoformat()] for r in originals])
                if self._realized_inputs.get(row.forecast_id) != signature:
                    targets.append((row.forecast_id, signature))
        count, errors = 0, []
        for forecast_id, signature in targets:
            try:
                with self.database.session() as session, native_event_read_scope(session, archive=self.archive):
                    prepared = prepare_event_realization_in_session(session, forecast_id,
                        archive=self.archive, cutoff_at=at, recorded_at=at)
                if prepared is None:
                    continue
                with self.database.session() as session:
                    result = commit_prepared_event_realization_in_session(session, prepared, archive=self.archive)
                if result:
                    self._realized_inputs[forecast_id] = signature
                    count += int(not prepared.existing)
            except Exception as exc:
                errors.append(_error('realization', exc))
        return count, errors

    def tick(self):
        errors = []
        coverage = realizations = evaluations = outcomes = 0
        try:
            coverage = self.coverage.tick()
        except Exception as exc:
            errors.append(_error('coverage', exc))
        try:
            realizations, actual_errors = self._realize_due()
            errors.extend(actual_errors)
        except Exception as exc:
            errors.append(_error('realization', exc))
        try:
            evaluations = self.monitor.process_due(max_events=4)
        except Exception as exc:
            errors.append(_error('monitor', exc))
        try:
            outcomes = self.outcomes.process_due(max_events=4)
        except Exception as exc:
            errors.append(_error('outcome', exc))
        return EventLifecycleReport(coverage, realizations, evaluations, outcomes, tuple(errors))
