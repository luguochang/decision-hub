"""Official retrospective chart data, not historical PIT-vintage authority."""
from __future__ import annotations
import json,re
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract
from .cpi_pdf_parse_cache import cached_cpi_parse

SOURCE='cleveland-cpi-nowcast-history';VERSION='cpi-nowcast-history.v1'
MONTH_URL='https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_month.json?sc_lang=en'
YEAR_URL='https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_year.json?sc_lang=en'
METRICS=('headline_mom','core_mom','headline_yoy','core_yoy')

class HistoryCapture(FrozenContract):
    capture_id:str=Field(min_length=1)
    raw_body_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    raw_body_ref:str
    url:str
    received_at:AwareDatetime
    @model_validator(mode='after')
    def valid_ref(self):
        if self.raw_body_ref!='raw://sha256/'+self.raw_body_hash:raise ValueError('cpi_history_capture_ref_invalid')
        return self

class CpiHistoryPayload(FrozenContract):
    schema_version:Literal['cpi-nowcast-history.v1']=VERSION
    source_id:Literal['cleveland-cpi-nowcast-history']=SOURCE
    role:Literal['historical_model_training']='historical_model_training'
    month_capture:HistoryCapture
    year_capture:HistoryCapture
    attempt_id:str=Field(min_length=1)
    received_at:AwareDatetime
    period_count:int=Field(ge=1)
    vintage_status:Literal['retrospective_chart_not_authenticated_PIT']='retrospective_chart_not_authenticated_PIT'
    @model_validator(mode='after')
    def valid_capture(self):
        if (self.month_capture.url!=MONTH_URL or self.year_capture.url!=YEAR_URL
            or self.received_at!=max(self.month_capture.received_at,self.year_capture.received_at)):
            raise ValueError('cpi_history_source_identity_invalid')
        return self

class CpiResidualPair(FrozenContract):
    reference_period:str
    forecast_date:date
    actual_date:date
    forecast:tuple[Decimal,...]=Field(min_length=4,max_length=4)
    actual:tuple[Decimal,...]=Field(min_length=4,max_length=4)
    month_record_index:int
    year_record_index:int
    forecast_row_index:int
    actual_row_index:int
    @model_validator(mode='after')
    def valid_pair(self):
        if self.forecast_date>=self.actual_date or any(not p.is_finite() for p in (*self.forecast,*self.actual)):
            raise ValueError('cpi_history_pair_invalid')
        return self

def _records(body,expected_axis):
    values=json.loads(body,parse_float=Decimal)
    if not isinstance(values,list) or not values:raise ValueError('cpi_history_chart_empty')
    result={}
    for index,record in enumerate(values):
        chart=record['chart']
        period=chart['subcaption']
        match=re.fullmatch(r'(20\d{2})-(\d{1,2})',period)
        if chart['caption']!='Inflation Nowcasting' or chart['yaxisname']!=expected_axis or not match:raise ValueError('cpi_history_chart_identity_invalid')
        year,month=map(int,match.groups());date(year,month,1);period=f'{year}-{month:02d}'
        if period in result:raise ValueError('cpi_history_period_duplicate')
        categories=record['categories']
        if len(categories)!=1:raise ValueError('cpi_history_categories_ambiguous')
        # Chart vertical release markers do not consume a data-array row.
        labels=[v['label'] for v in categories[0]['category'] if v.get('vline')!='true']
        dates=[]
        for label in labels:
            if not re.fullmatch(r'\d{2}/\d{2}',label):raise ValueError('cpi_history_category_date_invalid')
            m,d=map(int,label.split('/'));dates.append(date(year+(m<month),m,d))
        if dates!=sorted(set(dates)):raise ValueError('cpi_history_category_order_invalid')
        series={s['seriesname']:s['data'] for s in record['dataset']}
        if len(series)!=len(record['dataset']):raise ValueError('cpi_history_series_duplicate')
        names=('CPI Inflation','Core CPI Inflation','Actual CPI Inflation','Actual Core CPI Inflation')
        if any(name not in series or len(series[name])!=len(labels) for name in names):raise ValueError('cpi_history_series_shape_invalid')
        points={}
        for name in names:
            points[name]=[]
            for i,value in enumerate(series[name]):
                raw=str(value.get('value','')).strip()
                if not raw:points[name].append(None);continue
                number=Decimal(raw)
                aliases=(name,name.replace('Core CPI Inflation','Core CPI'))
                tooltip=value.get('tooltext','')
                if (not number.is_finite() or '{br}'+labels[i]+'{br}' not in tooltip
                    or not any(tooltip.startswith(alias+'{br}') for alias in aliases)):
                    raise ValueError('cpi_history_point_locator_invalid')
                points[name].append(number)
        result[period]=(index,dates,points)
    return result

def _parse_cpi_history(month_body,year_body):
    monthly=_records(month_body,'Month-over-month percent change')
    yearly=_records(year_body,'Year-over-year percent change')
    if set(monthly)!=set(yearly):raise ValueError('cpi_history_month_year_periods_crosswired')
    return tuple(tuple((period,index,tuple(dates),tuple((name,tuple(values)) for name,values in points.items()))
        for period,(index,dates,points) in records.items()) for records in (monthly,yearly))

def parse_cpi_history(month_body,year_body):
    frozen=cached_cpi_parse((month_body,year_body),_parse_cpi_history,parser_version=VERSION,
        dependencies=(_records,json.loads,json.JSONDecoder,json.JSONDecoder.__init__,
            json.JSONDecoder.decode,json.JSONDecoder.raw_decode,Decimal,date,re.fullmatch))
    # Public ABI remains independent mutable dictionaries/lists on every call.
    return tuple({period:(index,list(dates),{name:list(values) for name,values in points})
        for period,index,dates,points in records} for records in frozen)

def select_stage_pairs(month_body,year_body,*,target_period,stage_month_offset,stage_day):
    """One four-dimensional pair per historical event, at the same stage.

    The labels are publisher historical chart dates, not invented original
    received timestamps. The source's actual known clock is authenticated
    separately and must be before the future event for current use.
    """
    if stage_month_offset not in {0,1} or not 1<=stage_day<=31:raise ValueError('cpi_history_stage_unsupported')
    monthly,yearly=parse_cpi_history(month_body,year_body);pairs=[];excluded=[]
    for period,(mi,dates,points) in sorted(monthly.items()):
        if period>=target_period:excluded.append((period,'target_or_future_reference_period'));continue
        if period in {'2025-10','2025-11'}:excluded.append((period,'missing_cpi_and_model_special_methodology'));continue
        yi,ydates,y=yearly[period]
        if dates!=ydates:excluded.append((period,'month_year_date_grid_mismatch'));continue
        actual=[i for i in range(len(dates)) if all(v is not None for v in (points['Actual CPI Inflation'][i],points['Actual Core CPI Inflation'][i],y['Actual CPI Inflation'][i],y['Actual Core CPI Inflation'][i]))]
        if len(actual)!=1:excluded.append((period,'four_joint_actuals_unavailable'));continue
        ai=actual[0];year,month=map(int,period.split('-'));stage_month=month+stage_month_offset;stage_year=year+(stage_month>12);stage_month=(stage_month-1)%12+1
        # Invalid dates (e.g. stage31 in February) have a deliberate policy:
        # use the last valid day of that target calendar month.
        import calendar
        stage=date(stage_year,stage_month,min(stage_day,calendar.monthrange(stage_year,stage_month)[1]))
        if stage>=dates[ai]:excluded.append((period,'information_stage_not_pre_release_for_historical_event'));continue
        eligible=[i for i,d in enumerate(dates) if d<=stage and d<dates[ai] and all(v is not None for v in (points['CPI Inflation'][i],points['Core CPI Inflation'][i],y['CPI Inflation'][i],y['Core CPI Inflation'][i]))]
        if not eligible:excluded.append((period,'joint_pre_release_stage_nowcast_unavailable'));continue
        fi=eligible[-1]
        pairs.append(CpiResidualPair(reference_period=period,forecast_date=dates[fi],actual_date=dates[ai],forecast=(points['CPI Inflation'][fi],points['Core CPI Inflation'][fi],y['CPI Inflation'][fi],y['Core CPI Inflation'][fi]),actual=(points['Actual CPI Inflation'][ai],points['Actual Core CPI Inflation'][ai],y['Actual CPI Inflation'][ai],y['Actual Core CPI Inflation'][ai]),month_record_index=mi,year_record_index=yi,forecast_row_index=fi,actual_row_index=ai))
    return tuple(pairs),tuple(excluded)
