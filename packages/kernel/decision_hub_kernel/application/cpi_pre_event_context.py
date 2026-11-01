"""Authenticate official CPI originals and expose them before normal Task freeze."""
import json,os
from pathlib import Path
from sqlalchemy import select
from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from ..persistence.db import ObservationRecord,SourcePollAttemptRecord,as_utc
from ..ports.cpi_pre_event import CpiContextSource,CpiContextMetric,CpiPreEventContextBinding
from ..ports.market_path import aware_utc
from ..ports.bls_cpi import BLS_SOURCE_ID,BlsCpiMetricPayload,period_offset
from ..ports.cpi_nowcast import NOWCAST_SOURCE_ID,CpiNowcastMetricPayload
from ..ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID,CpiSchedulePayload
from .bls_cpi_original import read_bls_cpi_original_in_session
from .cpi_nowcast_original import read_cpi_nowcast_original_in_session
from .cpi_schedule_original import read_cpi_schedule_original_in_session

PREFIX='cpi-pre-event-context:'
def _archive():
    from packages.provider_adapters.research.raw_capture import RawBodyArchive
    return RawBodyArchive(Path(os.getenv('DECISION_HUB_DATA_DIR','data/decision-hub'))/'raw-captures')
def _source(session,original):
    row=session.get(ObservationRecord,original.observation_id,populate_existing=True)
    return CpiContextSource(observation_id=row.observation_id,content_hash=row.content_hash,known_at=original.known_at)
def _rows(session,source_id,cutoff):
    query=select(ObservationRecord).where(ObservationRecord.source_id==source_id,ObservationRecord.received_at<=cutoff)
    if source_id==CPI_SCHEDULE_SOURCE_ID:query=query.where(ObservationRecord.scheduled_at>cutoff)
    # Stream the retained source history; an arbitrary recent-row ceiling can
    # hide a valid prior or a month-start nowcast. No history is discarded.
    return session.scalars(query.order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id).execution_options(yield_per=128))
def _admitted(session,data,cutoff):
    attempt=session.get(SourcePollAttemptRecord,data.attempt_id,populate_existing=True)
    return attempt is not None and attempt.status=='succeeded' and as_utc(attempt.completed_at) is not None and as_utc(attempt.completed_at)<=cutoff

def collect_cpi_pre_event_context_in_session(session,*,cutoff_at,archive,event_key=None):
    cutoff=aware_utc(cutoff_at);schedules=[];reasons=[]
    for row in _rows(session,CPI_SCHEDULE_SOURCE_ID,cutoff):
        data=CpiSchedulePayload.model_validate_json(row.text)
        if not _admitted(session,data,cutoff):continue
        if data.scheduled_at<=cutoff or (event_key is not None and data.event_key!=event_key):continue
        original=read_cpi_schedule_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff)
        schedules.append(original)
    if not schedules:
        return CpiPreEventContextBinding(cutoff_at=cutoff,schedule_source=None,event_key=None,scheduled_at=None,reference_period=None,incomplete_reasons=('no_authenticated_upcoming_cpi_schedule','market_survey_consensus_unavailable'))
    schedules.sort(key=lambda o:(o.payload.scheduled_at,-o.known_at.timestamp(),o.observation_id))
    schedule=schedules[0];period=schedule.payload.reference_period
    if period is None:
        return CpiPreEventContextBinding(cutoff_at=cutoff,schedule_source=_source(session,schedule),event_key=schedule.payload.event_key,scheduled_at=schedule.payload.scheduled_at,reference_period=None,incomplete_reasons=('release_reference_period_unavailable','market_survey_consensus_unavailable'))
    nowcasts={};priors={}
    for row in _rows(session,NOWCAST_SOURCE_ID,cutoff):
        data=CpiNowcastMetricPayload.model_validate_json(row.text)
        if not _admitted(session,data,cutoff):continue
        if data.reference_period!=period or data.metric_id in nowcasts:continue
        original=read_cpi_nowcast_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff)
        nowcasts[data.metric_id]=CpiContextMetric(metric_id=data.metric_id,role='nowcast',reference_period=period,value=data.value,
            seasonal_adjustment=data.seasonal_adjustment,source=_source(session,original),source_update_label=data.source_update_label,methodology_notices=data.source_methodology_notices)
        if len(nowcasts)==4:break
    for row in _rows(session,BLS_SOURCE_ID,cutoff):
        data=BlsCpiMetricPayload.model_validate_json(row.text)
        if not _admitted(session,data,cutoff):continue
        if data.reference_period!=period_offset(period,-1) or data.metric_id in priors or data.actual_value is None:continue
        original=read_bls_cpi_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff)
        # A previously published month is the future event's prior. The upcoming
        # event's own actual value and any surprise never enter this projection.
        priors[data.metric_id]=CpiContextMetric(metric_id=data.metric_id,role='prior',reference_period=data.reference_period,value=data.actual_value,
            seasonal_adjustment=data.seasonal_adjustment,source=_source(session,original))
        if len(priors)==4:break
    if len(nowcasts)!=4:reasons.append('four_authenticated_nowcast_metrics_unavailable');nowcasts={}
    if len(priors)!=4:reasons.append('four_authenticated_prior_metrics_unavailable');priors={}
    reasons.append('market_survey_consensus_unavailable')
    order=('headline_mom','core_mom','headline_yoy','core_yoy')
    return CpiPreEventContextBinding(cutoff_at=cutoff,schedule_source=_source(session,schedule),event_key=schedule.payload.event_key,scheduled_at=schedule.payload.scheduled_at,reference_period=period,
        nowcast=tuple(nowcasts[k] for k in order if k in nowcasts),prior=tuple(priors[k] for k in order if k in priors),incomplete_reasons=tuple(reasons))

def cpi_pre_event_context_inputs_in_session(session,*,cutoff_at,archive=None):
    payload=collect_cpi_pre_event_context_in_session(session,cutoff_at=cutoff_at,archive=archive or _archive())
    full=payload.model_dump(mode='json')
    parts={'schedule':{k:v for k,v in full.items() if k not in {'nowcast','prior'}},'nowcast':{'nowcast':full['nowcast']},'prior':{'prior':full['prior']}}
    # This is research context derived from authenticated sources, not an
    # accepted official Fact or a probabilistic/transaction qualification.
    result=[]
    from ..ports.market_path import canonical_hash
    for name,part in parts.items():
        body={'schema_version':'cpi-pre-event-context-part.v1','binding_hash':payload.content_hash,'part':name,'payload':part}
        raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        identity=PREFIX+payload.content_hash+':'+name
        result.append(ResearchInputEvidence(evidence_id=identity,source_id=identity,kind='document',authority='unverified',source_url=None,published_at=None,
            observed_at=payload.cutoff_at,received_at=payload.cutoff_at,content_hash=canonical_hash(body),excerpt=raw))
    return tuple(result)

def validate_cpi_pre_event_context_in_session(session,inputs,*,archive=None):
    actual=[item for item in inputs if item.source_id.startswith(PREFIX)]
    if not actual:return
    if len(actual)!=3:raise ValueError('cpi_frozen_context_parts_incomplete')
    from ..ports.market_path import canonical_hash
    bodies=[json.loads(item.excerpt) for item in actual]
    if {p.get('part') for p in bodies}!={'schedule','nowcast','prior'} or len({p.get('binding_hash') for p in bodies})!=1:raise ValueError('cpi_frozen_context_parts_crosswired')
    combined={}
    for body in bodies:
        if set(body)!={'schema_version','binding_hash','part','payload'} or body['schema_version']!='cpi-pre-event-context-part.v1' or not isinstance(body['payload'],dict) or set(combined)&set(body['payload']):raise ValueError('cpi_frozen_context_part_shape_invalid')
        combined.update(body['payload'])
    payload=CpiPreEventContextBinding.model_validate_json(json.dumps(combined))
    for item,body in zip(actual,bodies,strict=True):
        raw=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if (body['binding_hash']!=payload.content_hash or item.excerpt!=raw or item.content_hash!=canonical_hash(body) or item.evidence_id!=PREFIX+payload.content_hash+':'+body['part']
            or item.source_id!=item.evidence_id or item.kind!='document' or item.authority!='unverified'
            or item.published_at is not None or item.source_url is not None
            or item.observed_at!=payload.cutoff_at or item.received_at!=payload.cutoff_at):raise ValueError('cpi_frozen_context_identity_invalid')
    archive=archive or _archive()
    expected=collect_cpi_pre_event_context_in_session(session,cutoff_at=payload.cutoff_at,archive=archive)
    if expected!=payload:raise ValueError('cpi_frozen_context_selection_changed')
    if payload.schedule_source:
        source=payload.schedule_source;original=read_cpi_schedule_original_in_session(session,source.observation_id,archive=archive,cutoff_at=payload.cutoff_at)
        if _source(session,original)!=source or (original.payload.event_key,original.payload.scheduled_at,original.payload.reference_period)!=(payload.event_key,payload.scheduled_at,payload.reference_period):raise ValueError('cpi_frozen_schedule_changed')
    for point in (*payload.nowcast,*payload.prior):
        original=(read_cpi_nowcast_original_in_session if point.role=='nowcast' else read_bls_cpi_original_in_session)(session,point.source.observation_id,archive=archive,cutoff_at=payload.cutoff_at)
        data=original.payload;value=data.value if point.role=='nowcast' else data.actual_value
        if _source(session,original)!=point.source or (data.metric_id,data.reference_period,data.seasonal_adjustment,value)!=(point.metric_id,point.reference_period,point.seasonal_adjustment,point.value):raise ValueError('cpi_frozen_metric_changed')
        if point.role=='nowcast' and (point.source_update_label!=data.source_update_label or point.methodology_notices!=data.source_methodology_notices):raise ValueError('cpi_frozen_nowcast_notice_changed')
