"""Joint historical stage-residual model, not a strategy-win probability."""
from collections import Counter
from decimal import Decimal,localcontext
from datetime import datetime
from ..ports.market_path import aware_utc,canonical_hash
from ..ports.cpi_nowcast_history import METRICS,select_stage_pairs
from ..ports.event_scenario import EventDistributionSpec,EventScenarioOutcome,seal_cpi_scenario

BANDS=('cooler','near','hotter')
ASSUMPTIONS=(
    'historical_four_metric_nowcast_residuals_are_jointly_exchangeable_at_the_same_calendar_stage',
    'official_chart_history_was_received_now_and_is_not_authenticated_historical_PIT_vintage',
    'past_regimes_are_pooled_with_equal_event_weight_and_may_not_match_current_regime',
    'nowcast_is_a_model_estimate_not_market_survey_consensus',
    'bands_compare_with_prior_month_not_market_surprise_or_asset_direction',
    'dirichlet_half_count_is_explicit_joint_category_prior_not_observed_errors',
    'event_probability_does_not_identify_asset_reaction_or_strategy_net_profit_probability',
    'empirical_support_does_not_model_extremes_beyond_observed_joint_residuals',
    'continuous_chart_actual_and_BLS_index_derived_prior_are_not_rounded_bulletin_values',
    'current_HTML_nowcast_center_is_displayed_at_two_decimals_with_half_width_0.005pp',
)
def band(value,baseline,tolerance):
    delta=value-baseline
    return 'cooler' if delta < -tolerance else 'hotter' if delta > tolerance else 'near'

def compute_cpi_scenario_body(context,*,history_source,month_body,year_body,tolerance_pp=Decimal('0.05')):
    as_of=context.cutoff_at
    if context.scheduled_at is None or not as_of<context.scheduled_at or history_source.known_at>as_of:raise ValueError('cpi_scenario_missing_or_late_inputs')
    if len(context.nowcast)!=4 or len(context.prior)!=4:raise ValueError('cpi_scenario_four_nowcast_and_prior_required')
    current={v.metric_id:v for v in context.nowcast};prior={v.metric_id:v for v in context.prior}
    updates={p.source_update_label for p in context.nowcast}
    if len(updates)!=1 or None in updates:raise ValueError('cpi_scenario_information_stage_unavailable')
    update_month,stage_day=map(int,next(iter(updates)).split('/'))
    year,month=map(int,context.reference_period.split('-'))
    stage_offset=(update_month-month)%12
    if stage_offset not in {0,1}:raise ValueError('cpi_scenario_information_stage_unsupported')
    pairs,excluded=select_stage_pairs(month_body,year_body,target_period=context.reference_period,stage_month_offset=stage_offset,stage_day=stage_day)
    if not pairs:raise ValueError('cpi_scenario_joint_residual_history_unavailable')
    # Preserve four residuals from the SAME historical month as one draw.
    draws=tuple(tuple(current[metric].value+pair.actual[i]-pair.forecast[i] for i,metric in enumerate(METRICS)) for pair in pairs)
    h,c=prior['headline_mom'].value,prior['core_mom'].value
    counts=Counter((band(v[0],h,tolerance_pp),band(v[1],c,tolerance_pp)) for v in draws)
    identifiers=tuple(f'cpi:headline_{a}:core_{b}' for a in BANDS for b in BANDS)
    outcomes=[]
    with localcontext() as ctx:
        ctx.prec=28;denominator=Decimal(len(pairs))+Decimal('4.5');used=Decimal(0)
        for i,(a,b) in enumerate((a,b) for a in BANDS for b in BANDS):
            if i<8:probability=(Decimal(counts[(a,b)])+Decimal('0.5'))/denominator
            else:
                with localcontext() as exact:
                    exact.prec=128
                    probability=Decimal(1)-sum((o.event_result_probability for o in outcomes),Decimal(0))
            used+=probability
            outcomes.append(EventScenarioOutcome(scenario_id=identifiers[i],headline_band=a,core_band=b,event_result_probability=probability,matched_historical_events=counts[(a,b)]))
    # These are named marginal support summaries of the SAME joint draws;
    # they are never multiplied together to make event probabilities.
    support=[]
    for i,metric in enumerate(METRICS):
        values=sorted(v[i] for v in draws)
        def nearest(q):
            import math
            return values[max(0,math.ceil(len(values)*q)-1)]
        support.append((metric,nearest(.1),nearest(.5),nearest(.9)))
    body={'event_key':context.event_key,'scheduled_at':context.scheduled_at,'as_of':as_of,
        'spec':EventDistributionSpec(event_family='cpi',partition_kind='cpi_joint_3x3',scenario_ids=identifiers),'scenarios':tuple(outcomes),'assumptions':ASSUMPTIONS,
        'reference_period':context.reference_period,'input_context':context,'input_context_hash':context.content_hash,'history_source':history_source,
        'baseline_ref':context.content_hash,'headline_baseline':h,'core_baseline':c,'tolerance_pp':tolerance_pp,'stage_month_offset':stage_offset,'stage_day':stage_day,
        'sample_count':len(pairs),'sample_hash':canonical_hash([p.model_dump(mode='json') for p in pairs]),'excluded_periods':excluded,'marginal_support_quantiles':tuple(support)}
    return body

def build_cpi_event_scenario(context,*,run_id,history_source,month_body,year_body,published_at,tolerance_pp=Decimal('0.05')):
    body=compute_cpi_scenario_body(context,history_source=history_source,month_body=month_body,year_body=year_body,tolerance_pp=tolerance_pp)
    body.update(snapshot_id='event-scenario:'+canonical_hash([run_id,context.event_key]),run_id=run_id,published_at=aware_utc(published_at))
    return seal_cpi_scenario(body)

def score_joint_event(snapshot,*,realized_headline_mom,realized_core_mom,label_known_at,target_definition):
    """Evaluate a frozen event forecast only after its actual release.

    Costs, position, asset reaction and strategy PnL have different labels and
    must use their own evaluation protocol rather than these event scores.
    """
    known=aware_utc(label_known_at)
    if target_definition!=snapshot.target_definition:raise ValueError('cpi_scenario_evaluation_target_definition_mismatch')
    if known<snapshot.scheduled_at:raise ValueError('cpi_scenario_actual_label_before_release')
    h=Decimal(realized_headline_mom);c=Decimal(realized_core_mom)
    if not h.is_finite() or not c.is_finite():raise ValueError('cpi_scenario_actual_label_nonfinite')
    identity=f'cpi:headline_{band(h,snapshot.headline_baseline,snapshot.tolerance_pp)}:core_{band(c,snapshot.core_baseline,snapshot.tolerance_pp)}'
    from math import log
    probability=next(v.event_result_probability for v in snapshot.scenarios if v.scenario_id==identity)
    brier=sum(((v.event_result_probability-(1 if v.scenario_id==identity else 0))**2 for v in snapshot.scenarios),Decimal(0))
    return {'label':identity,'joint_multiclass_brier':str(brier),'joint_log_loss':-log(float(probability)),'target':'event_outcome','asset_or_strategy_win_score':None,'prediction_payload_hash':snapshot.payload_hash}
