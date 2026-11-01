"""Deterministic hypotheses and legal existing Action preplans, not permissions."""
from datetime import timedelta
from ..ports.market_path import canonical_hash
from ..ports.event_opportunity import EventConditionalPlan,EventOpportunitySnapshot,EventOwnedOpportunitySnapshot,EventCausalChain
from ..ports.research_signal import ResearchActionCandidate,MechanismReview
from .event_response_model import estimate
from .action_research_policy import research_action_catalog

def _condition(identity,purpose,expression,until,text,scope):
    leaves=expression.get('args',[expression])
    return dict(id=identity,purpose=purpose,expression=expression,min_fresh_samples=2 if purpose=='entry' else 1,max_age_seconds=max(x['max_age_seconds'] for x in leaves),evaluation_seconds=60,calendar_ref='crypto-24x7.v1',valid_until=until,original_text=text,input_requirements=[dict(metric=x['feature'],scope=scope,source='okx',unit=x['unit']) for x in leaves])
def _leaf(scope,feature,cmp,threshold,unit):return dict(op='compare',feature=feature,scope_id=scope,unit=unit,cmp=cmp,threshold=threshold,max_age_seconds=3690 if feature=='volume_ratio_1h' else 90)

def build_event_opportunity(scenario,model,*,published_at,run_id=None):
    if model.as_of<scenario.as_of:raise ValueError('event_opportunity_model_before_scenario')
    run_id=run_id if run_id is not None else getattr(scenario,'run_id',None)
    t0=scenario.scheduled_at;asof=model.as_of;instrument=model.instrument;scope=model.scope_id;cat=research_action_catalog(instrument)
    expiry=t0+timedelta(hours=24,seconds=60);entry_end=t0+timedelta(hours=6);refresh=min(asof+timedelta(hours=6),t0-timedelta(seconds=1))
    plans=[]
    for s in scenario.scenarios:
        identity='event-plan:'+canonical_hash([scenario.snapshot_id,scope,s.scenario_id])[:32]
        review_id=identity+':mechanism'
        cooler=s.core_band=='cooler';hotter=s.core_band=='hotter'
        direction='通胀相对上月降温可能降低利率路径压力' if cooler else '通胀相对上月升温可能抬高利率路径压力' if hotter else '核心通胀近于上月，标题与结构可能主导分歧'
        alternative='市场共识与定价可能已经计入本结果；实际利率、美元或去杠杆反向变化可抵消甚至逆转资产反应。'
        mechanism=EventCausalChain(review_id=review_id,owner_forecast_id=scenario.snapshot_id,cutoff=asof,scope_id=scope,horizon='24h',
            nodes=[dict(id='event',kind='event',description=f'CPI联合情景 {s.scenario_id}：仅相对上月，不是相对市场共识。',refs=[scenario.snapshot_id]),dict(id='rates',kind='expectation',description=direction,refs=[]),dict(id='price',kind='observable',description='公布后实际分钟价量确认；未确认前无资产方向断言。',refs=['event-response-model:'+model.payload_hash]),dict(id='asset',kind='asset_effect',description=instrument+'资金风险偏好与价格响应',refs=[])],
            edges=[dict(id='event-rates',from_node='event',to_node='rates',hypothesis=direction,lag=dict(minimum_seconds=0,maximum_seconds=21600),support_refs=[scenario.snapshot_id],counter_refs=[],testable_feature_refs=[],status='hypothesis'),dict(id='rates-price',from_node='rates',to_node='price',hypothesis='只有实际价格/成交量与假设一致时才形成条件入场；传导方向尚待观测。',lag=dict(minimum_seconds=60,maximum_seconds=21600),support_refs=[],counter_refs=['event-response-model:'+model.payload_hash],testable_feature_refs=[dict(feature='return_6h',scope_id=scope,unit='fraction'),dict(feature='volume_ratio_1h',scope_id=scope,unit='ratio')],status='hypothesis'),dict(id='price-asset',from_node='price',to_node='asset',hypothesis='持续价量确认可能支持短期延续，失败即退出。',lag=dict(minimum_seconds=60,maximum_seconds=86400),support_refs=[],counter_refs=[],testable_feature_refs=[dict(feature='return_6h',scope_id=scope,unit='fraction')],status='hypothesis')],
            alternative_refs=[scenario.snapshot_id],priced_in_status='unknown',priced_in_refs=[],unresolved=['无认证调查共识与公告前定价，不能把prior_month当surprise。','实际利率/美元/资金流的事件窗口原件尚未绑定。',alternative],affected_plan_refs=[identity])
        positive=dict(op='all',args=[_leaf(scope,'return_6h','gt','0','fraction'),_leaf(scope,'volume_ratio_1h','gte','1.2','ratio')])
        negative=_leaf(scope,'return_6h','lt','-0.005','fraction')
        rebound=_leaf(scope,'return_6h','gt','0','fraction')
        flat_entry=_condition(identity+':flat-entry','entry',positive,entry_end,'T0+60秒至T0+6小时：6小时收益>0且最近完整UTC小时量/此前20小时中位量>=1.2，连续两根不同已收盘分钟确认；确认后再延迟60秒，以首根合法分钟开盘纸面入场，入场时间须严格早于T0+6h。',scope)
        flat_exit=_condition(identity+':flat-exit','exit',negative,expiry,'6小时收益<-0.5%确认后延迟60秒纸面退出；最迟T0+24小时+60秒硬期限退出，不是实际可成交承诺。',scope)
        flat_invalid=_condition(identity+':flat-invalid','invalidation',negative,expiry,'同退出阈值，或价量原件缺失/过期/事件情景未认证时禁止入场；不能延长事件窗口。',scope)
        long_entry=_condition(identity+':long-entry','entry',negative,entry_end,'用户已持多仓时，公布后6小时收益<-0.5%且两根已收盘分钟确认，建议减半；不假设真实持仓。',scope)
        long_exit=_condition(identity+':long-exit','exit',negative,expiry,'若减仓后弱势延续，重新研究剩余仓位；此处不是实际账户指令。',scope)
        long_invalid=_condition(identity+':long-invalid','invalidation',rebound,expiry,'6小时收益恢复>0时减仓前提失效，重新评估；不自动追涨补仓。',scope)
        def proposal(position,action,entry,exit,invalid):
            return ResearchActionCandidate(schema_version='research-action-candidate.v1',scope_id=scope,horizon='24h',as_of=asof,valid_until=expiry,next_review_at=refresh,position_context=position,action=action,
                probability=dict(target_id=cat['targets']['24h']['ref'],value=None,kind='unavailable',provenance_ref=None),entry=[entry],exit=[exit],invalidation=[invalid],
                size_policy_ref=cat['size_policy']['ref'],cost_policy_ref=cat['cost_policy']['ref'],target_spec_ref=cat['targets']['24h']['ref'],evidence_refs=[scenario.snapshot_id,'event-response-model:'+model.payload_hash],claim_refs=[],mechanism_review_ref=review_id,
                reason='事件相对T0的条件研究预案；不是今天的24小时现价指令。公布情景/临近价量/仓位需再验，未武装且未授交易权限。当前Action target_before_stop概率仍不可用；另列固定规则净盈利估计不冒充该目标。')
        plans.append(EventConditionalPlan(scenario_id=s.scenario_id,event_result_probability=s.event_result_probability,activation_required=('官方实际值与同一reference_period绑定，continuous MoM按snapshot同一prior_month/.05pp分格。','不是市场共识惊喜；必须另核价量确认与当前真实报价。','当前模型每6小时重估且事件临近时刷新；不能直接使用今日绝对价。'),causal_chain=mechanism,alternative_mechanism=alternative,
            flat_proposal=proposal('flat','enter_long',flat_entry,flat_exit,flat_invalid),long_proposal=proposal('long','reduce_long',long_entry,long_exit,long_invalid),
            scenario_asset_reaction_estimate=estimate(model,target='asset_positive_event_24h',scenario_id=s.scenario_id),scenario_strategy_net_profit_estimate=estimate(model,target='long_confirmation_net_profit_event_24h',scenario_id=s.scenario_id)))
    gaps=('市场共识/期货定价原件未绑定；prior_month情景不代表超预期。','小样本历史事件日估计，未校准；未覆盖情景不填50%或复制事件概率。','净盈利目标仅上述正向确认纸面策略；减仓是风险控制，不能称该盈利概率。','价差/账户手续费未认证；不含杠杆、实际订单或账户持仓。','尚未连接自动公告情景激活/实时监控，全部预案未武装。')+tuple(model.excluded)
    snapshot_id='event-opp-'+canonical_hash([run_id or scenario.snapshot_id,scope,scenario.payload_hash,model.payload_hash])[:48]
    common=dict(snapshot_id=snapshot_id,event_key=scenario.event_key,reference_period=scenario.reference_period,instrument=instrument,asset_scope=scope,scheduled_at=t0,event_probability_as_of=scenario.as_of,as_of=asof,published_at=published_at,scenario_snapshot_ref=scenario.snapshot_id,scenario_snapshot_hash=scenario.payload_hash,
        entry_valid_from=t0+timedelta(seconds=60),entry_valid_until=entry_end,exit_valid_until=expiry,market_model_as_of=asof,model_valid_until=refresh,reestimate_required_at=refresh,model=model,
        asset_reaction_estimate=estimate(model,target='asset_positive_event_24h'),strategy_net_profit_estimate=estimate(model,target='long_confirmation_net_profit_event_24h'),strategy_net_profit_given_entry_estimate=estimate(model,target='long_confirmation_net_profit_event_24h',entered_only=True),plans=tuple(plans),
        price_estimation_status='no_authenticated_price_samples' if not model.samples else 'event_day_baseline_only' if any(p.scenario_asset_reaction_estimate.events==0 for p in plans) else 'all_scenarios_supported_estimated_uncalibrated',missing_scenario_ids=tuple(p.scenario_id for p in plans if p.scenario_asset_reaction_estimate.events==0),gaps=gaps,payload_hash='0'*64)
    if run_id is not None:
        reviews=tuple(MechanismReview(schema_version='research-mechanism-review.v1',run_id=run_id,**p.causal_chain.model_dump(mode='python',exclude={'owner_forecast_id'})) for p in plans)
        value=EventOpportunitySnapshot(run_id=run_id,task_mechanism_reviews=reviews,**common)
    else:value=EventOwnedOpportunitySnapshot(forecast_id=scenario.snapshot_id,**common)
    return value.model_copy(update={'payload_hash':canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'}))})
