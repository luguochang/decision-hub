"""Official future CPI schedule with an explicitly observed DOL period document."""
from __future__ import annotations
import hashlib,json
from datetime import UTC,datetime,timedelta
from packages.contracts_py.decision_hub_contracts.models import MacroEventPayload,SourceType,TextEnvelope
from packages.kernel.decision_hub_kernel.ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID,CPI_SCHEDULE_VERSION,DOL_DOCUMENT_URL,fred_calendar_url,parse_cpi_publication_schedules
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollFailure,SourcePollResult
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE,safe_url
from .transport import public_source_client
from .diagnostics import response_snapshot

PREFIX='cpi-schedule-v1:'
async def _get(url):
    batch=CURRENT_CAPTURE.get()
    if batch is None or not batch.context_id.startswith('source:') or batch.adapter_version!=CPI_SCHEDULE_VERSION:raise ValueError('cpi_schedule_native_capture_required')
    async with public_source_client(url) as client:response=await client.get(url,follow_redirects=False)
    response_snapshot(response.status_code,response.content,CPI_SCHEDULE_VERSION)
    digest=hashlib.sha256(response.content).hexdigest()
    found=[c for c in batch.captures if c['body_hash']==digest and c['url']==safe_url(url)]
    if len(found)!=1:raise ValueError('cpi_schedule_capture_ambiguous')
    capture=found[0]
    return response.content,{'capture_id':capture['capture_id'],'raw_body_hash':digest,'raw_body_ref':capture['body_ref'],'url':url,'received_at':capture['completed_at']}

class CpiPublicationScheduleSource:
    def __init__(self,manifest,*,dol_release_url=DOL_DOCUMENT_URL,clock=None):
        self.manifest=manifest
        # A publisher-observed URL, never a guessed monthly filename. Once this
        # document expires, unmatched future FRED rows retain period=None.
        if dol_release_url!=DOL_DOCUMENT_URL:raise ValueError('cpi_schedule_document_url_not_configured')
        self.dol_release_url=dol_release_url
        self.clock=clock or (lambda:datetime.now(UTC))
    async def poll(self,cursor=None):
        now=self.clock()
        if now.tzinfo is None or now.utcoffset() is None:raise ValueError('cpi_schedule_clock_offset_required')
        now=now.astimezone(UTC);year=now.year
        fred,fred_ref=await _get(fred_calendar_url(year))
        dol,dol_ref=await _get(self.dol_release_url)
        batch=CURRENT_CAPTURE.get()
        snapshot=response_snapshot(200,fred,CPI_SCHEDULE_VERSION)
        try:values=parse_cpi_publication_schedules(fred,dol,year=year,fred_capture=fred_ref,dol_capture=dol_ref,attempt_id=batch.context_id.removeprefix('source:'))
        except (ValueError,TypeError) as error:raise SourcePollFailure('source_cpi_schedule_payload_invalid',snapshot.model_copy(update={'parse_status':'invalid'})) from error
        snapshot=snapshot.model_copy(update={'parse_status':'valid','parsed_count':len(values)})
        seen={}
        if cursor:
            try:
                if not cursor.startswith(PREFIX):raise ValueError
                data=json.loads(cursor.removeprefix(PREFIX));seen=data['seen']
                if data['source_id']!=CPI_SCHEDULE_SOURCE_ID or not isinstance(seen,dict):raise ValueError
                if any(not isinstance(v,dict) or set(v)!={'semantic_hash','content_hash'} for v in seen.values()):raise ValueError
            except (ValueError,TypeError,KeyError) as error:raise SourcePollFailure('source_cpi_schedule_cursor_invalid',snapshot) from error
        envelopes=[]
        upcoming=[p for p in values if p.scheduled_at>p.received_at]
        for payload in upcoming[:self.manifest.max_batch]:
            prior=seen.get(payload.event_key)
            if prior and prior['semantic_hash']==payload.semantic_hash:continue
            raw=json.dumps(payload.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
            digest=hashlib.sha256(raw.encode()).hexdigest()
            envelopes.append(TextEnvelope(source_id=CPI_SCHEDULE_SOURCE_ID,source_type=SourceType.official_feed,
                observed_at=payload.received_at,received_at=payload.received_at,published_at=None,raw_text=raw,language='en',
                event_hint=payload.event_key,event_family='cpi',scheduled_at=payload.scheduled_at,source_url=fred_calendar_url(year),
                macro_event=MacroEventPayload(external_event_id=payload.event_key,family='cpi',scheduled_at=payload.scheduled_at,scheduled_date=payload.scheduled_date,time_precision='exact',source_timezone='America/Chicago',source_authority='official',status='scheduled'),
                revision_of='sha256:'+prior['content_hash'] if prior else None,content_hash=digest))
            seen[payload.event_key]={'semantic_hash':payload.semantic_hash,'content_hash':digest}
        after=PREFIX+json.dumps({'source_id':CPI_SCHEDULE_SOURCE_ID,'seen':seen},sort_keys=True,separators=(',',':'))
        dates=[p.scheduled_date.isoformat() for p in upcoming]
        snapshot=snapshot.model_copy(update={'upcoming_count':len(upcoming),'first_upcoming_date':dates[0] if dates else None,'last_upcoming_date':dates[-1] if dates else None,'coverage_status':'available' if upcoming else 'no_upcoming'})
        return SourcePollResult(source_id=CPI_SCHEDULE_SOURCE_ID,cursor_before=cursor,cursor_after=after,envelopes=tuple(envelopes),fetched_at=max(fred_ref['received_at'],dol_ref['received_at']),next_poll_at=now+timedelta(hours=6),content_snapshot=snapshot)
