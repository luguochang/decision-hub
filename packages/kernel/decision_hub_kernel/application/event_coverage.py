"""Account for expected pre-event delivery, independently of numerical success.

These are operational coverage records, not an authentication of a numerical
forecast. Published originals still pass their own reader when opened. No
HTTP, model work or archive parsing is performed inside this short writer.
"""
from datetime import timedelta
from sqlalchemy import select, text
from ..persistence.db import as_utc, utcnow
from ..persistence.event_coverage_records import EventCoverageRecord as Coverage
from ..persistence.event_scenario_records import EventScenarioJobRecord as Job, EventScenarioForecastRecord as Forecast
from ..ports.market_path import aware_utc, canonical_hash

SLOTS = (('pre_24h', timedelta(hours=24)), ('pre_2h', timedelta(hours=2)), ('pre_30m', timedelta(minutes=30)))


def _writer(session):
    if session.bind.dialect.name == 'sqlite':
        session.execute(text('BEGIN IMMEDIATE'))


class EventCoverageService:
    def __init__(self, database, *, clock=utcnow):
        self.database = database
        self.clock = clock

    def tick(self):
        """Register known events even when their forecast job is still missing inputs."""
        at = aware_utc(self.clock())
        count = 0
        with self.database.session() as session:
            _writer(session)
            jobs = session.scalars(select(Job).where(
                Job.job_kind == 'forecast', Job.scope_key == 'global',
                Job.created_at <= at, Job.scheduled_at >= at - timedelta(days=30),
            ).order_by(Job.scheduled_at, Job.created_at.desc())).all()
            events = {}
            for job in jobs:
                events.setdefault(job.event_key, job)
            for key, job in events.items():
                scheduled = as_utc(job.scheduled_at)
                first = session.scalar(select(Forecast).where(
                    Forecast.event_key == key, Forecast.published_at <= at,
                    Forecast.published_at < scheduled,
                ).order_by(Forecast.published_at, Forecast.forecast_id).limit(1))
                for name, offset in SLOTS:
                    due = scheduled - offset
                    identity = 'event-cover:' + canonical_hash([key, name])
                    row = session.get(Coverage, identity)
                    if row is None:
                        row = Coverage(coverage_id=identity, event_key=key,
                            reference_period=job.reference_period, scheduled_at=scheduled,
                            slot_kind=name, due_at=due, state='pending',
                            first_observed_at=at, updated_at=at)
                        session.add(row)
                    previous_state = row.state
                    # Record genuine publication time, never backdate a late bootstrap.
                    if at < due:
                        state = 'pending'
                    elif first is not None:
                        state = 'ready' if as_utc(first.published_at) <= due else 'late'
                    else:
                        state = 'missing' if at < scheduled else 'missed'
                    row.state = state
                    row.forecast_id = first.forecast_id if first else None
                    row.forecast_published_at = as_utc(first.published_at) if first else None
                    row.reference_period = job.reference_period
                    row.reason_code = ('event_prevision_inputs_unavailable' if state in {'missing', 'missed'}
                        else 'event_prevision_published_after_slot' if state == 'late' else None)
                    row.updated_at = at
                    if (row.notice_id is None or previous_state != state) and state != 'pending' and at < scheduled:
                        from .event_opportunity_monitor import enqueue_event_notice_in_session
                        transition = 'coverage_missing' if state == 'missing' else 'pre_event_risk'
                        notice = enqueue_event_notice_in_session(session, database=self.database,
                            event_key=key, reference_period=job.reference_period,
                            transition=transition, subject='核心事件公布前提醒',
                            body=('核心数据即将公布，事前输入尚未齐备，请查看具体缺口；当前没有可核对的事件概率。'
                                if state == 'missing' else
                                '事前预案已保存。已有多仓请核对公布前风险预算及减险分支；空仓请查看条件和失效窗口。公布后确认不等于公布前已执行。'),
                            created_at=at, dedupe_key='event-coverage:' + canonical_hash([key, name, state]),
                            forecast_id=row.forecast_id, reason_codes=(row.reason_code,) if row.reason_code else ())
                        row.notice_id = notice.notice_id
                    count += 1
        return count

    def for_event(self, event_key):
        with self.database.session() as session:
            rows = session.scalars(select(Coverage).where(Coverage.event_key == event_key).order_by(Coverage.due_at)).all()
            return [dict(coverage_id=r.coverage_id, event_key=r.event_key,
                reference_period=r.reference_period, scheduled_at=as_utc(r.scheduled_at),
                slot_kind=r.slot_kind, due_at=as_utc(r.due_at), state=r.state,
                forecast_id=r.forecast_id, forecast_published_at=as_utc(r.forecast_published_at),
                first_observed_at=as_utc(r.first_observed_at), updated_at=as_utc(r.updated_at),
                notice_id=r.notice_id, reason_code=r.reason_code) for r in rows]
