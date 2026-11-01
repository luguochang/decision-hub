"""Publisher-observed historical release clocks; never historical first-known."""
from __future__ import annotations
import hashlib,io,re
from datetime import UTC,datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pypdf import PdfReader
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract,canonical_hash
from .cpi_pdf_parse_cache import cached_cpi_pdf_parse

SOURCE='cpi-release-history';VERSION='cpi-release-history.v1'
# Exact primary URLs discovered from official search, not filename guessing.
DOCUMENTS=('https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf','https://www.dol.gov/newsroom/economicdata/cpi_09112026.pdf')
MONTHS=('January','February','March','April','May','June','July','August','September','October','November','December')
class CpiReleaseHistoryPayload(FrozenContract):
    schema_version:Literal['cpi-release-history.v1']=VERSION
    event_family:Literal['cpi']='cpi'
    event_key:str
    reference_period:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    scheduled_at:AwareDatetime
    source_timezone:Literal['America/New_York']='America/New_York'
    document_url:str
    capture_id:str
    raw_body_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    raw_body_ref:str
    attempt_id:str
    received_at:AwareDatetime
    vintage_status:Literal['retrospective_release_document']='retrospective_release_document'
    @model_validator(mode='after')
    def identity(self):
        if self.document_url not in DOCUMENTS or self.event_key!='fred-cpi:'+self.scheduled_at.date().isoformat() or self.raw_body_ref!='raw://sha256/'+self.raw_body_hash or self.scheduled_at>=self.received_at:
            raise ValueError('cpi_release_history_identity_invalid')
        return self
    @property
    def semantic_hash(self):return canonical_hash(self.model_dump(mode='json',exclude={'capture_id','raw_body_hash','raw_body_ref','attempt_id','received_at'}))

def _parse_cpi_release_header(body:bytes)->tuple[datetime,str]:
    text=' '.join((PdfReader(io.BytesIO(body)).pages[0].extract_text() or '').split())
    clock=re.findall(r'(\d{1,2}:\d{2}) ([ap])\.m\. \(ET\) (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([A-Za-z]+ \d{1,2}, 20\d{2})',text)
    period=re.findall(r'CONSUMER PRICE INDEX [–-] ([A-Z]+) (20\d{2})',text)
    if len(clock)!=1 or len(period)!=1 or 'Transmission of material in this release is embargoed until' not in text:raise ValueError('cpi_history_header_ambiguous')
    minute,ampm,weekday,day=clock[0];parsed=datetime.strptime(day,'%B %d, %Y')
    if parsed.strftime('%A')!=weekday:raise ValueError('cpi_history_weekday_invalid')
    time=datetime.strptime(minute+' '+ampm+'m','%I:%M %p').time()
    t0=datetime.combine(parsed.date(),time,tzinfo=ZoneInfo('America/New_York')).astimezone(UTC)
    month,year=period[0];reference=f'{year}-{MONTHS.index(month.title())+1:02d}'
    return t0,reference

def parse_cpi_release_history(body:bytes,*,provenance:dict)->CpiReleaseHistoryPayload:
    if len(body)>5*1024*1024 or not body.startswith(b'%PDF-'):raise ValueError('cpi_history_release_pdf_invalid')
    t0,reference=cached_cpi_pdf_parse(body,_parse_cpi_release_header,parser_version=VERSION,
        reader=PdfReader,dependencies=(MONTHS,re.findall,datetime,ZoneInfo))
    # Only header parsing is reused. Every caller reconstructs and validates
    # its actual capture/attempt/received_at provenance and actual body hash.
    return CpiReleaseHistoryPayload(event_key='fred-cpi:'+t0.date().isoformat(),reference_period=reference,scheduled_at=t0,raw_body_hash=hashlib.sha256(body).hexdigest(),raw_body_ref='raw://sha256/'+hashlib.sha256(body).hexdigest(),**provenance)
