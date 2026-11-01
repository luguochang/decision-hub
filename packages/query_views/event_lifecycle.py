"""Read the event's preparation, actual activation, monitor and user receipts."""
from datetime import timedelta
from sqlalchemy import select
from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import _archive
from packages.kernel.decision_hub_kernel.application.event_coverage import EventCoverageService
from packages.kernel.decision_hub_kernel.application.event_realization import (
    read_latest_event_realization_for_forecast_in_session,
)
from packages.kernel.decision_hub_kernel.application.event_read_scope import native_event_read_scope
from packages.kernel.decision_hub_kernel.persistence.db import SourceStateRecord, as_utc, utcnow
from packages.kernel.decision_hub_kernel.persistence.event_scenario_records import (
    EventScenarioJobRecord as Job, EventScenarioForecastRecord as Forecast,
)

SOURCE_LABELS = {'cpi-publication-schedule': 'CPI官方日程', 'bls-public-api': 'CPI官方公布值',
    'cleveland-cpi-nowcast': '通胀即时预测', 'cleveland-cpi-nowcast-history': '即时预测历史'}


class EventLifecycleQueryService:
    def __init__(self, database, monitor, *, archive=None, clock=utcnow):
        self.database, self.monitor, self.clock = database, monitor, clock
        self.archive = archive or _archive()

    def sources(self):
        with self.database.session() as session:
            return [dict(source_id=source_id, label=label,
                state=row.status if row else 'missing',
                last_success_at=as_utc(row.last_success_at) if row else None,
                error_code=row.error_code if row else 'source_not_registered')
                for source_id, label in SOURCE_LABELS.items()
                for row in (session.get(SourceStateRecord, source_id),)]

    def detail(self, event_key, *, owner_id=None):
        at = self.clock()
        with self.database.session() as session, native_event_read_scope(session, archive=self.archive):
            forecast = session.scalar(select(Forecast).where(Forecast.event_key == event_key,
                Forecast.published_at <= at).order_by(Forecast.published_at.desc(), Forecast.forecast_id).limit(1))
            job = session.scalar(select(Job).where(Job.event_key == event_key, Job.job_kind == 'forecast',
                Job.created_at <= at).order_by(Job.updated_at.desc()).limit(1))
            if forecast is None and job is None:
                return None
            scheduled = as_utc(forecast.scheduled_at if forecast else job.scheduled_at)
            period = forecast.reference_period if forecast else job.reference_period
            realization = read_latest_event_realization_for_forecast_in_session(session,
                forecast.forecast_id, archive=self.archive, cutoff_at=at) if forecast else None
            activation = dict(state='realized' if realization else
                'awaiting_release' if at < scheduled else 'awaiting_actual',
                realization_id=realization.realization_id if realization else None,
                scenario_id=realization.scenario_id if realization else None,
                known_at=realization.known_at if realization else None,
                recorded_at=realization.recorded_at if realization else None,
                metrics=[dict(metric_id=m.metric_id, value=str(m.value) if m.value is not None else None, unit=m.unit)
                    for m in realization.metrics] if realization else [])
            outcome = None
            if forecast:
                from packages.kernel.decision_hub_kernel.application.event_outcome import (
                    read_latest_event_outcome_for_forecast_in_session, event_outcome_user_view,
                )
                value = read_latest_event_outcome_for_forecast_in_session(session,
                    forecast.forecast_id, archive=self.archive, cutoff_at=at)
                outcome = event_outcome_user_view(value, scheduled_at=scheduled, cutoff_at=at)
            forecast_id = forecast.forecast_id if forecast else None
        return dict(schema_version='event-lifecycle-view.v1', event_key=event_key, as_of=at,
            scheduled_at=scheduled, reference_period=period, forecast_id=forecast_id,
            coverage=EventCoverageService(self.database, clock=self.clock).for_event(event_key),
            realization=activation,
            assets=[dict(instrument=instrument,
                positions=self.monitor.get_status(forecast_id, instrument) if forecast_id else [])
                for instrument in ('BTC-USDT', 'ETH-USDT')],
            notices=self.monitor.list_notices(event_key=event_key, owner_id=owner_id),
            outcome=outcome,
            outcome_due_at=scheduled + timedelta(hours=24, seconds=60), sources=self.sources())
