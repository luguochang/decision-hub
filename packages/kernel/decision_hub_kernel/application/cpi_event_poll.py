"""Read-only event-slot policy; ordinary manual polls receive no exemption.

This module does not issue HTTP or override the existing SourceIngestionService.
Composition must call it in that service's physical lease/quota transaction.
"""
from dataclasses import dataclass
from datetime import UTC,datetime,timedelta
from sqlalchemy import func,select
from ..persistence.db import ObservationRecord,SourcePollAttemptRecord,SourceStateRecord,as_utc
from ..ports.market_path import aware_utc
from ..ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID,CpiSchedulePayload
from ..ports.bls_cpi import BLS_SOURCE_ID,BlsCpiMetricPayload
from .cpi_schedule_original import read_cpi_schedule_original_in_session
from .bls_cpi_original import read_bls_cpi_original_in_session

SLOTS=(60,300,900,3600)
@dataclass(frozen=True)
class DueCpiReleaseSlot:
    event_key:str
    reference_period:str
    scheduled_at:datetime
    slot_seconds:int
    trigger_type:str
    lower_at:datetime
    upper_at:datetime
    schedule_observation_id:str
    schedule_payload_hash:str

def read_due_cpi_release_slot_in_session(session,*,archive,now,trigger_type):
    """A candidate scheduler decision, not a caller-provided window permission."""
    if trigger_type!='scheduled':return None,'manual_or_catchup_has_no_event_exemption'
    now=aware_utc(now);day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    count=session.scalar(select(func.count()).select_from(SourcePollAttemptRecord).where(SourcePollAttemptRecord.source_id==BLS_SOURCE_ID,SourcePollAttemptRecord.started_at>=day,SourcePollAttemptRecord.started_at<day+timedelta(days=1),SourcePollAttemptRecord.status!='skipped')) or 0
    if count>=24:return None,'source_bls_daily_quota_exhausted'
    state=session.get(SourceStateRecord,BLS_SOURCE_ID,populate_existing=True)
    if state is not None and state.error_code in {'source_access_denied','source_authentication_required'} and state.next_poll_at is not None and as_utc(state.next_poll_at)>now:
        return None,'source_access_denial_backoff_preserved'
    rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==CPI_SCHEDULE_SOURCE_ID,ObservationRecord.scheduled_at<=now,ObservationRecord.scheduled_at>now-timedelta(hours=2)).order_by(ObservationRecord.received_at.desc())).all()
    for row in rows:
        original=read_cpi_schedule_original_in_session(session,row.observation_id,archive=archive,cutoff_at=now)
        event=original.payload
        if event.reference_period is None:return None,'release_reference_period_unavailable'
        offset=(now-event.scheduled_at).total_seconds()
        eligible=[n for n in SLOTS if offset>=n]
        if not eligible:return None,'release_first_slot_not_due'
        slot=eligible[-1];upper=SLOTS[SLOTS.index(slot)+1] if slot!=SLOTS[-1] else 7200
        tag=f'scheduled_cpi:{event.scheduled_date}:{slot//60:02}'
        if session.scalar(select(func.count()).select_from(SourcePollAttemptRecord).where(SourcePollAttemptRecord.source_id==BLS_SOURCE_ID,SourcePollAttemptRecord.trigger_type==tag)):
            return None,'release_slot_already_recorded'
        metric_rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==BLS_SOURCE_ID,ObservationRecord.received_at<=now).order_by(ObservationRecord.received_at.desc()).execution_options(yield_per=128))
        valid=set()
        for metric in metric_rows:
            data=BlsCpiMetricPayload.model_validate_json(metric.text)
            if data.reference_period!=event.reference_period or data.actual_value is None or data.metric_id in valid:continue
            value=read_bls_cpi_original_in_session(session,metric.observation_id,archive=archive,cutoff_at=now)
            if value.known_at>=event.scheduled_at:valid.add(data.metric_id)
            if len(valid)==4:break
        if len(valid)==4:return None,'release_actuals_already_authenticated'
        return DueCpiReleaseSlot(event.event_key,event.reference_period,event.scheduled_at,slot,tag,event.scheduled_at+timedelta(seconds=slot),event.scheduled_at+timedelta(seconds=upper),row.observation_id,row.content_hash),'event_slot_due'
    return None,'no_authenticated_release_window'
