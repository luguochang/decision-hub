"""Official CPI publication schedule; FRED time and DOL period stay distinct."""
from __future__ import annotations
import hashlib,io,locale,re
from datetime import UTC,date,datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import AwareDatetime,Field,model_validator
from pypdf import PdfReader
from .market_path import FrozenContract,canonical_hash
from .cpi_nowcast import _Tables,MONTHS
from .cpi_pdf_parse_cache import cached_cpi_parse,cached_cpi_pdf_parse

CPI_SCHEDULE_SOURCE_ID='cpi-publication-schedule'
CPI_SCHEDULE_VERSION='cpi-publication-schedule.v1'
DOL_DOCUMENT_URL='https://www.dol.gov/newsroom/economicdata/cpi_10142026.pdf'
def fred_calendar_url(year:int)->str:
    if not 2000<=year<=2099:raise ValueError('cpi_calendar_year_invalid')
    return f'https://fred.stlouisfed.org/releases/calendar?rid=10&y={year}'

class CpiScheduleCapture(FrozenContract):
    capture_id:str=Field(min_length=1)
    raw_body_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    raw_body_ref:str
    url:str
    received_at:AwareDatetime
    @model_validator(mode='after')
    def valid_ref(self):
        if self.raw_body_ref!='raw://sha256/'+self.raw_body_hash:raise ValueError('cpi_schedule_raw_ref_invalid')
        return self

class CpiSchedulePayload(FrozenContract):
    schema_version:Literal['cpi-publication-schedule.v1']='cpi-publication-schedule.v1'
    source_id:Literal['cpi-publication-schedule']=CPI_SCHEDULE_SOURCE_ID
    role:Literal['schedule']='schedule'
    event_key:str
    event_family:Literal['cpi']='cpi'
    calendar_year:int=Field(ge=2000,le=2099)
    scheduled_date:date
    source_local_time:str=Field(pattern=r'^\d{2}:\d{2}$')
    source_timezone:Literal['America/Chicago']='America/Chicago'
    scheduled_at:AwareDatetime
    reference_period:str|None=Field(default=None,pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    reference_period_basis:Literal['dol_next_release_statement','unavailable']
    document_page_index:int|None=Field(default=None,ge=0)
    fred_capture:CpiScheduleCapture
    dol_capture:CpiScheduleCapture|None=None
    attempt_id:str=Field(min_length=1)
    received_at:AwareDatetime
    published_at:None=None
    @model_validator(mode='after')
    def valid_schedule(self):
        local=datetime.fromisoformat(str(self.scheduled_date)+'T'+self.source_local_time).replace(tzinfo=ZoneInfo(self.source_timezone))
        if (self.calendar_year!=self.scheduled_date.year or self.event_key!='fred-cpi:'+str(self.scheduled_date)
            or self.scheduled_at!=local.astimezone(UTC)
            or self.fred_capture.url!=fred_calendar_url(self.calendar_year)
            or (self.dol_capture is not None and self.dol_capture.url!=DOL_DOCUMENT_URL)
            or (self.reference_period is None)!=(self.reference_period_basis=='unavailable')
            or (self.reference_period is not None and (self.dol_capture is None or self.document_page_index is None))
            or self.received_at!=max(c.received_at for c in (self.fred_capture,self.dol_capture) if c is not None)):
            raise ValueError('cpi_schedule_identity_invalid')
        return self
    @property
    def semantic_hash(self):
        return canonical_hash(self.model_dump(mode='json',exclude={'fred_capture','dol_capture','attempt_id','received_at','document_page_index'}))

def _parse_fred_cpi_calendar(body:bytes,year:int)->tuple[tuple[date,str,datetime],...]:
    text=body.decode('utf-8');parser=_Tables();parser.feed(text);parser.close()
    plain=' '.join(' '.join(parser.text).split())
    if f'{year} Economic Release Calendar - Consumer Price Index' not in plain or 'All times are US Central Time.' not in plain:
        raise ValueError('cpi_calendar_identity_or_timezone_missing')
    candidates=[t for t in parser.tables if any('Consumer Price Index' in row for row in t['rows'])]
    if len(candidates)!=1:raise ValueError('cpi_calendar_table_ambiguous')
    current=None;out=[]
    for row in candidates[0]['rows']:
        label=row[0].removesuffix(' Updated') if row else ''
        if row and re.fullmatch(r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) [A-Za-z]+ \d{1,2}, \d{4}',label):
            parsed=datetime.strptime(label,'%A %B %d, %Y')
            if parsed.strftime('%A')!=label.split()[0] or parsed.year!=year:raise ValueError('cpi_calendar_date_invalid')
            current=parsed.date()
        elif len(row)>=2 and row[1]=='Consumer Price Index':
            if current is None:raise ValueError('cpi_calendar_missing_parent_date')
            value=datetime.strptime(row[0].strip(),'%I:%M %p')
            label=value.strftime('%H:%M')
            instant=datetime.combine(current,value.time(),tzinfo=ZoneInfo('America/Chicago')).astimezone(UTC)
            out.append((current,label,instant))
    if not out or len({v[0] for v in out})!=len(out):raise ValueError('cpi_calendar_empty_or_duplicate')
    return tuple(out)

def parse_fred_cpi_calendar(body:bytes,*,year:int)->tuple[tuple[date,str,datetime],...]:
    if len(body)>2*1024*1024:raise ValueError('cpi_calendar_body_too_large')
    if type(year) is not int:return _parse_fred_cpi_calendar(body,year)
    return cached_cpi_parse((body,),_parse_fred_cpi_calendar,parser_version=CPI_SCHEDULE_VERSION,
        parameters=(year,),dependencies=(
            _Tables,_Tables.__init__,_Tables.feed,_Tables.close,_Tables.goahead,
            _Tables.handle_starttag,_Tables.handle_endtag,_Tables.handle_data,
            _Tables.parse_starttag,_Tables.parse_endtag,_Tables.parse_comment,
            _Tables.parse_html_declaration,re.fullmatch,datetime,date,ZoneInfo,
            ZoneInfo('America/Chicago'),locale.setlocale(locale.LC_TIME)))

def _parse_dol_next_cpi_release(body:bytes)->tuple[str,datetime,int]:
    reader=PdfReader(io.BytesIO(body));matches=[]
    # The body is a release report, not a guessed date registry. Search every
    # page so a layout change cannot silently reinterpret a positional page.
    for index,page in enumerate(reader.pages):
        text=' '.join((page.extract_text() or '').split())
        pattern=r'The Consumer Price Index news release for ([A-Za-z]+) (20\d{2}) is scheduled to be published on (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([A-Za-z]+ \d{1,2}, 20\d{2}), at (\d{1,2}:\d{2}) ([ap])\.m\. \(ET\)\.'
        for m in re.finditer(pattern,text):
            period=f'{int(m[2])}-{MONTHS.index(m[1])+1:02d}'
            day=datetime.strptime(m[4],'%B %d, %Y')
            if day.strftime('%A')!=m[3]:raise ValueError('cpi_dol_weekday_mismatch')
            clock=datetime.strptime(m[5]+' '+m[6]+'m','%I:%M %p')
            instant=datetime.combine(day.date(),clock.time(),tzinfo=ZoneInfo('America/New_York')).astimezone(UTC)
            matches.append((period,instant,index))
    if len(matches)!=1:raise ValueError('cpi_dol_next_release_statement_ambiguous')
    return matches[0]

def parse_dol_next_cpi_release(body:bytes)->tuple[str,datetime,int]:
    if len(body)>5*1024*1024 or not body.startswith(b'%PDF-'):raise ValueError('cpi_dol_pdf_invalid')
    return cached_cpi_pdf_parse(body,_parse_dol_next_cpi_release,parser_version=CPI_SCHEDULE_VERSION,
        reader=PdfReader,dependencies=(MONTHS,re.finditer,datetime,ZoneInfo))

def parse_cpi_publication_schedules(fred_body:bytes,dol_body:bytes|None,*,year:int,fred_capture:dict,dol_capture:dict|None,attempt_id:str)->tuple[CpiSchedulePayload,...]:
    fred=CpiScheduleCapture.model_validate(fred_capture)
    dol=CpiScheduleCapture.model_validate(dol_capture) if dol_capture else None
    if hashlib.sha256(fred_body).hexdigest()!=fred.raw_body_hash or (dol is None)!=(dol_body is None):raise ValueError('cpi_schedule_provenance_invalid')
    if dol and hashlib.sha256(dol_body).hexdigest()!=dol.raw_body_hash:raise ValueError('cpi_dol_raw_hash_invalid')
    bound=parse_dol_next_cpi_release(dol_body) if dol_body is not None else None
    result=[]
    for day,clock,instant in parse_fred_cpi_calendar(fred_body,year=year):
        matching=bound is not None and bound[1]==instant
        result.append(CpiSchedulePayload(event_key='fred-cpi:'+str(day),calendar_year=year,scheduled_date=day,source_local_time=clock,scheduled_at=instant,
            reference_period=bound[0] if matching else None,reference_period_basis='dol_next_release_statement' if matching else 'unavailable',
            document_page_index=bound[2] if matching else None,fred_capture=fred,dol_capture=dol,attempt_id=attempt_id,
            received_at=max(c.received_at for c in (fred,dol) if c is not None)))
    return tuple(result)
