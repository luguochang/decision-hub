"""One normal source for the publisher-observed monthly/yearly chart URLs."""
import hashlib,json
from datetime import UTC,datetime,timedelta
from packages.contracts_py.decision_hub_contracts import TextEnvelope
from packages.kernel.decision_hub_kernel.ports.cpi_nowcast_history import SOURCE,VERSION,MONTH_URL,YEAR_URL,CpiHistoryPayload,parse_cpi_history
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult,SourcePollFailure
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE,safe_url
from .transport import public_source_client
from .diagnostics import response_snapshot

async def _get(url):
    batch=CURRENT_CAPTURE.get()
    if batch is None or not batch.context_id.startswith('source:') or batch.adapter_version!=VERSION:raise ValueError('cpi_history_native_source_capture_required')
    async with public_source_client(url) as client:response=await client.get(url,follow_redirects=False)
    snapshot=response_snapshot(response.status_code,response.content,VERSION)
    if response.status_code!=200:raise SourcePollFailure('source_cpi_history_http_status_invalid',snapshot)
    digest=hashlib.sha256(response.content).hexdigest()
    captures=[v for v in batch.captures if v['url']==safe_url(url) and v['body_hash']==digest]
    if len(captures)!=1:raise ValueError('cpi_history_native_capture_ambiguous')
    v=captures[0]
    return response.content,{'capture_id':v['capture_id'],'raw_body_hash':digest,'raw_body_ref':v['body_ref'],'url':url,'received_at':v['completed_at']}

class ClevelandCpiHistorySource:
    def __init__(self,manifest,*,clock=None):self.manifest=manifest;self.clock=clock or (lambda:datetime.now(UTC))
    async def poll(self,cursor=None):
        month,mref=await _get(MONTH_URL);year,yref=await _get(YEAR_URL);batch=CURRENT_CAPTURE.get()
        snapshot=response_snapshot(200,month,VERSION)
        try:monthly,yearly=parse_cpi_history(month,year)
        except (ValueError,KeyError,TypeError) as error:raise SourcePollFailure('source_cpi_history_payload_invalid',snapshot.model_copy(update={'parse_status':'invalid'})) from error
        payload=CpiHistoryPayload(month_capture=mref,year_capture=yref,attempt_id=batch.context_id.removeprefix('source:'),received_at=max(mref['received_at'],yref['received_at']),period_count=len(monthly))
        identity=hashlib.sha256(month+year).hexdigest();before=None
        if cursor:
            try:
                data=json.loads(cursor)
                if set(data)!={'source_id','raw_pair_hash','content_hash'} or data['source_id']!=SOURCE:raise ValueError
                before=data
            except (ValueError,KeyError,TypeError) as error:raise SourcePollFailure('source_cpi_history_cursor_invalid',snapshot) from error
        raw=json.dumps(payload.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(raw.encode()).hexdigest()
        changed=before is None or before['raw_pair_hash']!=identity
        envelopes=(TextEnvelope(source_id=SOURCE,source_type='official_feed',observed_at=payload.received_at,received_at=payload.received_at,published_at=None,raw_text=raw,language='en',event_hint='cpi_nowcast_history',source_url=MONTH_URL,content_hash=digest,revision_of='sha256:'+before['content_hash'] if before else None),) if changed else ()
        after=json.dumps({'source_id':SOURCE,'raw_pair_hash':identity,'content_hash':digest if changed else before['content_hash']},sort_keys=True,separators=(',',':'))
        snapshot=snapshot.model_copy(update={'parse_status':'valid','parsed_count':len(monthly)})
        return SourcePollResult(source_id=SOURCE,cursor_before=cursor,cursor_after=after,envelopes=envelopes,fetched_at=payload.received_at,next_poll_at=self.clock()+timedelta(hours=24),content_snapshot=snapshot)
