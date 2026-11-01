"""Normal source capture/admission for a small explicitly observed release set."""
import hashlib,json
from datetime import UTC,datetime,timedelta
from packages.contracts_py.decision_hub_contracts.models import SourceType,TextEnvelope
from packages.kernel.decision_hub_kernel.ports.cpi_release_history import SOURCE,VERSION,DOCUMENTS,parse_cpi_release_history
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult,SourcePollFailure,SourceContentSnapshot
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE,safe_url
from .transport import public_source_client
from .diagnostics import response_snapshot
class CpiHistoricalReleaseSource:
    def __init__(self,manifest,*,clock=None):self.manifest=manifest;self.clock=clock or (lambda:datetime.now(UTC))
    async def poll(self,cursor=None):
        batch=CURRENT_CAPTURE.get()
        if batch is None or not batch.context_id.startswith('source:') or batch.adapter_version!=VERSION:raise ValueError('cpi_release_history_native_capture_required')
        seen={}
        if cursor:
            try:
                data=json.loads(cursor)
                if data['source_id']!=SOURCE or set(data)!={'source_id','seen'} or not isinstance(data['seen'],dict):raise ValueError
                seen=data['seen']
            except (ValueError,KeyError,TypeError) as e:raise SourcePollFailure('source_cpi_release_history_cursor_invalid',SourceContentSnapshot(parser_version=VERSION)) from e
        values=[];envelopes=[]
        for url in DOCUMENTS:
            async with public_source_client(url) as client:response=await client.get(url,follow_redirects=False)
            snapshot=response_snapshot(response.status_code,response.content,VERSION)
            digest=hashlib.sha256(response.content).hexdigest()
            found=[c for c in batch.captures if c['url']==safe_url(url) and c['body_hash']==digest and c['status_code']==200 and c['error_code'] is None]
            if len(found)!=1:raise SourcePollFailure('source_cpi_release_history_capture_invalid',snapshot)
            c=found[0]
            try:value=parse_cpi_release_history(response.content,provenance={'document_url':url,'capture_id':c['capture_id'],'attempt_id':batch.context_id.removeprefix('source:'),'received_at':c['completed_at']})
            except (ValueError,TypeError) as e:raise SourcePollFailure('source_cpi_release_history_payload_invalid',snapshot.model_copy(update={'parse_status':'invalid'})) from e
            values.append(value)
            if seen.get(value.event_key)==value.semantic_hash:continue
            raw=json.dumps(value.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
            envelopes.append(TextEnvelope(source_id=SOURCE,source_type=SourceType.official_feed,observed_at=value.received_at,received_at=value.received_at,published_at=None,raw_text=raw,language='en',event_hint='cpi_historical_release',source_url=url,content_hash=hashlib.sha256(raw.encode()).hexdigest()))
            seen[value.event_key]=value.semantic_hash
        # Historical material is intentionally not a macro-event trigger.
        snapshot=snapshot.model_copy(update={'parse_status':'valid','parsed_count':len(values)})
        return SourcePollResult(source_id=SOURCE,cursor_before=cursor,cursor_after=json.dumps({'source_id':SOURCE,'seen':seen},sort_keys=True,separators=(',',':')),envelopes=tuple(envelopes),fetched_at=max(v.received_at for v in values),next_poll_at=self.clock()+timedelta(days=7),content_snapshot=snapshot)
