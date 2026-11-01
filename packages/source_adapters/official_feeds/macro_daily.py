"""Automatic daily macro acquisition via the existing source capture pipeline."""
import hashlib, json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from packages.contracts_py.decision_hub_contracts.models import SourceType,TextEnvelope
from packages.kernel.decision_hub_kernel.ports.macro_daily import VERSION,source_id,source_url,capture_url,parse_macro_daily
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult,SourcePollFailure
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE
from .diagnostics import response_snapshot
from .transport import public_source_client

@dataclass(frozen=True)
class MacroDailyResponse:
    status:int
    body:bytes
    provenance:dict

async def _fetch(series):
    batch=CURRENT_CAPTURE.get();url=source_url(series)
    if batch is None or not batch.context_id.startswith('source:') or batch.adapter_version!=VERSION:
        raise ValueError('macro_daily_native_capture_required')
    async with public_source_client(url) as client:
        response=await client.get(url,follow_redirects=False)
    digest=hashlib.sha256(response.content).hexdigest()
    captures=[c for c in batch.captures if c.get('body_hash')==digest and c['url']==capture_url(series)]
    if len(captures)!=1:raise ValueError('macro_daily_capture_ambiguous')
    c=captures[0]
    return MacroDailyResponse(response.status_code,response.content,{'capture_id':c['capture_id'],'attempt_id':batch.context_id.removeprefix('source:'),'received_at':c['completed_at']})

class FredDailyBackgroundSource:
    def __init__(self,manifest,*,series_id,fetcher=None,clock=None):
        if manifest.source_id!=source_id(series_id) or manifest.version!=VERSION:raise ValueError('macro_daily_manifest_invalid')
        self.manifest=manifest;self.series_id=series_id;self.fetcher=fetcher or _fetch;self.clock=clock or (lambda:datetime.now(UTC))
    async def poll(self,cursor=None):
        result=await self.fetcher(self.series_id)
        snapshot=response_snapshot(result.status,result.body,VERSION)
        try:value=parse_macro_daily(result.body,series_id=self.series_id,provenance=result.provenance)
        except (ValueError,TypeError) as error:
            raise SourcePollFailure('source_macro_daily_payload_invalid',snapshot.model_copy(update={'parse_status':'invalid'})) from error
        old=None
        if cursor:
            try:
                old=json.loads(cursor)
                if set(old)!={'schema','semantic_hash','content_hash'} or old['schema']!=VERSION or any(len(old[k])!=64 or any(c not in '0123456789abcdef' for c in old[k]) for k in ('semantic_hash','content_hash')):raise ValueError
            except (ValueError,TypeError,KeyError) as error:raise SourcePollFailure('source_macro_daily_cursor_invalid',snapshot) from error
        raw=json.dumps(value.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(raw.encode()).hexdigest();envelopes=()
        if old is None or old['semantic_hash']!=value.semantic_hash:
            envelopes=(TextEnvelope(source_id=value.source_id,source_type=SourceType.official_feed,observed_at=value.received_at,received_at=value.received_at,published_at=None,raw_text=raw,language='en',event_hint='macro_daily_background',event_family=None,source_url=value.source_url,scheduled_at=None,macro_event=None,revision_of=('sha256:'+old['content_hash']) if old else None,content_hash=digest),)
            after=json.dumps({'schema':VERSION,'semantic_hash':value.semantic_hash,'content_hash':digest},sort_keys=True,separators=(',',':'))
        else:after=cursor
        return SourcePollResult(source_id=value.source_id,cursor_before=cursor,cursor_after=after,envelopes=envelopes,fetched_at=value.received_at,next_poll_at=self.clock()+timedelta(hours=6),content_snapshot=snapshot.model_copy(update={'parse_status':'valid','parsed_count':1}))
