"""A fully declared paper strategy; no model-generated probability."""
from datetime import timedelta
from decimal import Decimal
from math import sqrt
from statistics import median
from ..ports.market_path import path_hashes
from ..ports.event_response_path import EventResponseSample,EventProbabilityEstimate

def scenario_band(value,prior,tolerance=Decimal('.05')):
    return 'cooler' if value<prior-tolerance else 'hotter' if value>prior+tolerance else 'near'

def _features(bars,index):
    # Exact complete UTC hours, not rolling partial T0 hours.
    bar=bars[index];boundary=bar.close_at.replace(minute=0,second=0,microsecond=0)
    by={b.open_at:b for b in bars}
    volumes=[]
    for offset in range(21):
        start=boundary-timedelta(hours=offset+1)
        hour=[by.get(start+timedelta(minutes=m)) for m in range(60)]
        if any(b is None or b.volume_base is None for b in hour):return None
        v=sum((b.volume_base for b in hour),Decimal(0))
        if offset==0:current=v
        else:volumes.append(v)
    prior=by.get(bar.open_at-timedelta(hours=6))
    base=median(volumes)
    if prior is None or base<=0:return None
    return bar.close/prior.close-1,current/base

def response_sample(release,*,release_hash,bls,bls_hash,rates,scope,bars,captures):
    t0=release.payload.scheduled_at;start=t0+timedelta(seconds=60);end=start+timedelta(hours=24)
    ordered=tuple(sorted(bars,key=lambda b:b.open_at))
    first=t0.replace(minute=0,second=0,microsecond=0)-timedelta(hours=21)
    if not ordered or ordered[0].open_at!=first or ordered[-1].close_at!=end or len(ordered)!=int((end-first).total_seconds()/60) or any(b.open_at!=first+timedelta(minutes=i) for i,b in enumerate(ordered)):raise ValueError('event_response_native_path_gap')
    by={b.open_at:b for b in ordered};entry=None;exit_at=None;net=Decimal(0);consecutive=0
    for i,b in enumerate(ordered[:-2]):
        if b.close_at<start:continue
        f=_features(ordered,i)
        if f is None:raise ValueError('event_response_required_feature_missing')
        ret,volume=f
        if entry is None:
            if b.close_at>=t0+timedelta(hours=6):break
            consecutive=consecutive+1 if ret>0 and volume>=Decimal('1.2') else 0
            if consecutive>=2:
                # Match the existing 60s reaction delay after the confirming
                # close. No instantaneous fill at that same close boundary.
                proposed=ordered[i+2]
                if proposed.open_at>=t0+timedelta(hours=6):break
                entry=proposed
        elif ret<Decimal('-.005'):
            exit_at=ordered[i+2].open_at;exit_price=ordered[i+2].open;break
    if entry is not None:
        if exit_at is None:exit_at=end;exit_price=ordered[-1].close
        # Explicit fee+slippage .15% each side. Spread remains unverified.
        net=(exit_price*(1-Decimal('.0015')))/(entry.open*(1+Decimal('.0015')))-1
    h,c=rates['headline_mom'],rates['core_mom']
    if any(v is None for v in (h.actual_value,h.prior_value,c.actual_value,c.prior_value)):raise ValueError('event_response_cpi_rates_missing')
    semantic,provenance=path_hashes(scope,ordered)
    return EventResponseSample(event_key=release.payload.event_key,reference_period=release.payload.reference_period,scheduled_at=t0,
        release_observation_id=release.observation_id,release_content_hash=release_hash,release_known_at=release.known_at,
        bls_observation_id=bls.observation_id,bls_content_hash=bls_hash,bls_known_at=bls.known_at,
        headline_actual=h.actual_value,headline_prior=h.prior_value,core_actual=c.actual_value,core_prior=c.prior_value,
        scenario_id=f'cpi:headline_{scenario_band(h.actual_value,h.prior_value)}:core_{scenario_band(c.actual_value,c.prior_value)}',
        reaction_start_at=start,reaction_end_at=end,reaction_return=ordered[-1].close/by[start].open-1,
        strategy_entered=entry is not None,strategy_entry_at=entry.open_at if entry else None,strategy_exit_at=exit_at,strategy_net_return=net,
        path_semantic_hash=semantic,path_provenance_hash=provenance,capture_originals=tuple(sorted(set(captures))))

def estimate(model,*,target,scenario_id=None,entered_only=False):
    selected=[s for s in model.samples if scenario_id is None or s.scenario_id==scenario_id]
    entered=sum(s.strategy_entered for s in selected)
    if entered_only:selected=[s for s in selected if s.strategy_entered];entered=len(selected)
    n=len(selected);wins=sum((s.reaction_return if target=='asset_positive_event_24h' else s.strategy_net_return)>0 for s in selected)
    lo=hi=None
    if n:
        p=wins/n;z=1.959963984540054;denom=1+z*z/n;center=(p+z*z/(2*n))/denom;radius=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
        lo=Decimal(str(max(0.,center-radius)));hi=Decimal(str(min(1.,center+radius)))
    return EventProbabilityEstimate(target=target,probability_status='estimated_uncalibrated' if n else 'unavailable',basis='conditional_on_entered' if entered_only else 'same_prior_month_scenario' if scenario_id else 'event_day_climatology',scenario_conditioned=scenario_id is not None,
        value=(Decimal(wins)+Decimal('.5'))/(n+1) if n else None,empirical_fraction=Decimal(wins)/n if n else None,lower_95=lo,upper_95=hi,events=n,entered=entered,successes=wins,
        model_ref='event-response-model:'+model.payload_hash,model_hash=model.payload_hash,
        assumptions=('Jeffreys Beta(.5,.5) posterior mean; Wilson 95% empirical interval, not calibrated reliability.','Only registered complete historical CPI events; small sample and selection bias.','Prior-month bands are not market consensus surprises.','Current retrospective vintages; no historical pre-event backtest claim.','All event opportunities denominator; untriggered strategy has zero return and is not a win.','Closed-minute confirmation plus 60s delay then next-minute open paper fill; no live executability assertion.','Spread/account fees unverified; fee .1% and slippage .05% each side assumptions.'))
