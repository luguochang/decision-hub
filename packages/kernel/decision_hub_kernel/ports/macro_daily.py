"""FRED current daily CSV retained with native capture and receipt provenance."""
import csv
import hashlib
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import AwareDatetime, Field, TypeAdapter, model_validator
from .market_path import FrozenContract, canonical_hash, aware_utc

VERSION='fred-daily-background.v1'
SERIES={
 'DGS10':('美国10年期国债名义收益率','percent',7),
 'DFII10':('美国10年期通胀保值国债实际收益率','percent',7),
 'DTWEXBGS':('广义贸易加权美元指数（非DXY）','index_jan_2006_100',14),
}
def source_id(series):
    if series not in SERIES:raise ValueError('macro_daily_series_denied')
    return 'fred-daily-'+series.lower()
def source_url(series):
    source_id(series)
    return 'https://fred.stlouisfed.org/graph/fredgraph.csv?id='+series
def capture_url(series):
    source_id(series)
    # The shared raw-capture redactor masks generic `id` query parameters.
    # Keep that protection. The exact CSV column authenticates the series.
    return 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=REDACTED'

class MacroDailyPoint(FrozenContract):
    observation_date: date
    value: Decimal
    @model_validator(mode='after')
    def finite(self):
        if not self.value.is_finite():raise ValueError('macro_daily_nonfinite_value')
        return self

class MacroDailyPayload(FrozenContract):
    schema_version: Literal['fred-daily-background.v1']=VERSION
    series_id: Literal['DGS10','DFII10','DTWEXBGS']
    source_id: str
    source_url: str
    label: str
    unit: Literal['percent','index_jan_2006_100']
    frequency: Literal['daily']='daily'
    public_at: None=None
    public_time_precision: Literal['unknown']='unknown'
    history_vintage: Literal['current_visible_not_historical_vintage']='current_visible_not_historical_vintage'
    points: tuple[MacroDailyPoint,...]
    received_at: AwareDatetime
    capture_id: str=Field(min_length=1)
    attempt_id: str=Field(min_length=1)
    raw_body_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
    raw_body_ref: str
    @model_validator(mode='after')
    def valid(self):
        if (self.source_id!=source_id(self.series_id) or self.source_url!=source_url(self.series_id)
            or (self.label,self.unit)!=SERIES[self.series_id][:2] or len(self.points)!=6
            or any(a.observation_date>=b.observation_date for a,b in zip(self.points,self.points[1:]))
            or (self.series_id=='DTWEXBGS' and any(p.value<=0 for p in self.points))
            or self.points[-1].observation_date>aware_utc(self.received_at).date()
            or self.raw_body_ref!='raw://sha256/'+self.raw_body_hash):
            raise ValueError('macro_daily_payload_identity_invalid')
        return self
    @property
    def semantic_hash(self):
        return canonical_hash(self.model_dump(mode='json',exclude={'received_at','capture_id','attempt_id','raw_body_hash','raw_body_ref'}))

def parse_macro_daily(body:bytes,*,series_id:str,provenance:dict)->MacroDailyPayload:
    if len(body)>2*1024*1024:raise ValueError('macro_daily_body_too_large')
    source_id(series_id)
    reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
    if reader.fieldnames not in [['observation_date',series_id],['DATE',series_id]]:
        raise ValueError('macro_daily_csv_header_invalid')
    points={};cutoff=aware_utc(TypeAdapter(AwareDatetime).validate_python(provenance['received_at'])).date()
    for row in reader:
        try:
            day=date.fromisoformat(row[reader.fieldnames[0]])
            if day>cutoff:raise ValueError('macro_daily_future_observation')
            if day in points:raise ValueError('macro_daily_duplicate_observation')
            raw=row[series_id]
            points[day]=None if raw in ('','.','NA') else MacroDailyPoint(observation_date=day,value=Decimal(raw))
        except (KeyError,TypeError,InvalidOperation) as exc:raise ValueError('macro_daily_csv_row_invalid') from exc
    selected=tuple(points[d] for d in sorted(points) if points[d] is not None)[-6:]
    digest=hashlib.sha256(body).hexdigest()
    return MacroDailyPayload(series_id=series_id,source_id=source_id(series_id),source_url=source_url(series_id),label=SERIES[series_id][0],unit=SERIES[series_id][1],points=selected,raw_body_hash=digest,raw_body_ref='raw://sha256/'+digest,**provenance)
