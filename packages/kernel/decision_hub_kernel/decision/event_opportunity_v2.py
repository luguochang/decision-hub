"""Scenario-dependent event plans from one authenticated policy/replay version."""
from datetime import timedelta
from decimal import Decimal

from ..ports.market_path import canonical_hash
from ..ports.event_opportunity import EventConditionalPlanV2, EventOwnedOpportunitySnapshotV2, EventCausalChain
from ..ports.research_signal import ResearchActionCandidate
from .action_research_policy import research_action_catalog
from .event_opportunity_policy import event_policy_catalog
from .event_response_model_v2 import estimate_v2, STRATEGY_TARGET


def _leaves(expression):
    if expression['op'] == 'compare':
        return [expression]
    return [leaf for part in expression['args'] for leaf in _leaves(part)]


def _condition(identity, purpose, expression, until, text, scope, confirmations):
    leaves = _leaves(expression)
    return dict(id=identity, purpose=purpose, expression=expression,
                min_fresh_samples=confirmations,
                max_age_seconds=max(x['max_age_seconds'] for x in leaves),
                evaluation_seconds=60, calendar_ref='crypto-24x7.v1', valid_until=until,
                original_text=text,
                input_requirements=[dict(metric=x['feature'], scope=scope, source='okx', unit=x['unit']) for x in leaves])


def _rule_text(expression):
    names = {'return_6h': '6小时收益', 'volume_ratio_1h': '最近完整UTC小时量/此前20小时中位量'}
    symbols = {'gt': '>', 'gte': '≥', 'lt': '<', 'lte': '≤'}
    def label(x):
        number = Decimal(x['threshold'])
        suffix = '%'
        if x['unit'] == 'fraction':
            number *= 100
        else:
            suffix = '倍'
        return f"{names[x['feature']]}{symbols[x['cmp']]}{format(number.normalize(), 'f')}{suffix}"
    return ' 且 '.join(label(x) for x in _leaves(expression))


def build_event_opportunity_v2(scenario, model, *, published_at, macro_context):
    if model.model_version != 'cpi-scenario-policy-empirical.v2':
        raise ValueError('event_opportunity_v2_requires_policy_replay')
    if model.as_of < scenario.as_of or model.as_of != macro_context.as_of:
        raise ValueError('event_opportunity_v2_context_clock_invalid')
    t0, asof, instrument, scope = scenario.scheduled_at, model.as_of, model.instrument, model.scope_id
    catalog = research_action_catalog(instrument)
    policies = event_policy_catalog(scope, macro_context)
    if any(model.fee_fraction_each_side != Decimal(p.fee_fraction_each_side)
           or model.slippage_fraction_each_side != Decimal(p.slippage_fraction_each_side) for p in policies):
        raise ValueError('event_opportunity_v2_policy_cost_mismatch')
    policy_catalog_hash = canonical_hash([p.model_dump(mode='json') for p in policies])
    if model.policy_catalog_hash != policy_catalog_hash or model.macro_context_hash != macro_context.input_hash:
        raise ValueError('event_opportunity_v2_replay_policy_catalog_mismatch')
    by = {p.scenario_id: p for p in policies}
    for sample in model.samples:
        p = by.get(sample.scenario_id)
        if p is None or sample.policy_id != p.policy_id or sample.policy_hash != p.policy_hash:
            raise ValueError('event_opportunity_v2_sample_policy_mismatch')
    expiry = t0 + timedelta(hours=24, seconds=60)
    entry_end = t0 + timedelta(hours=6)
    refresh = min(asof + timedelta(hours=6), t0 - timedelta(seconds=1))
    plans = []
    macro_refs = [item.observation_ref for item in macro_context.series]
    for s in scenario.scenarios:
        p = by[s.scenario_id]
        identity = 'event-plan:' + canonical_hash([scenario.snapshot_id, scope, s.scenario_id, p.policy_hash])[:32]
        review_id = identity + ':mechanism'
        mechanism = EventCausalChain(
            review_id=review_id, owner_forecast_id=scenario.snapshot_id, cutoff=asof, scope_id=scope, horizon='24h',
            nodes=[dict(id='event', kind='event', description=f'CPI联合情景 {s.scenario_id}：仅相对上月，不是相对市场共识。', refs=[scenario.snapshot_id]),
                   dict(id='background', kind='expectation', description=p.rationale, refs=macro_refs),
                   dict(id='price', kind='observable', description='公布后实际价量确认与失效保护；本规则无绝对未来入场价。', refs=['event-response-model:' + model.payload_hash]),
                   dict(id='asset', kind='asset_effect', description=instrument + ' 条件新增风险或持多仓减险', refs=[])],
            edges=[dict(id='event-background', from_node='event', to_node='background', hypothesis=p.rationale,
                        lag=dict(minimum_seconds=0, maximum_seconds=21600), support_refs=[scenario.snapshot_id],
                        counter_refs=macro_refs, testable_feature_refs=[], status='hypothesis'),
                   dict(id='background-price', from_node='background', to_node='price', hypothesis='当前规则：' + _rule_text(p.flat_entry) + f'，连续{p.entry_confirmations}根不同已收盘分钟；未确认前不入场。',
                        lag=dict(minimum_seconds=60, maximum_seconds=21600), support_refs=[], counter_refs=macro_refs,
                        testable_feature_refs=[dict(feature='return_6h', scope_id=scope, unit='fraction'), dict(feature='volume_ratio_1h', scope_id=scope, unit='ratio')], status='hypothesis'),
                   dict(id='price-asset', from_node='price', to_node='asset', hypothesis='同一版本条件决定纸面入场与保护；不把事件结果概率当策略胜率。',
                        lag=dict(minimum_seconds=60, maximum_seconds=86400), support_refs=[], counter_refs=[],
                        testable_feature_refs=[dict(feature='return_6h', scope_id=scope, unit='fraction')], status='hypothesis')],
            alternative_refs=[scenario.snapshot_id], priced_in_status='unknown', priced_in_refs=[],
            unresolved=list(p.gaps) + [p.counter_hypothesis], affected_plan_refs=[identity])
        flat_entry = _condition(identity + ':flat-entry', 'entry', p.flat_entry, entry_end,
            f'T0+60秒至T0+6小时：{_rule_text(p.flat_entry)}，连续{p.entry_confirmations}根不同已收盘分钟确认；再延迟60秒按合法分钟开盘纸面入场，须严格早于T0+6小时。', scope, p.entry_confirmations)
        flat_exit = _condition(identity + ':flat-exit', 'exit', p.flat_exit, expiry,
            _rule_text(p.flat_exit) + ' 确认后延迟60秒退出；最迟T0+24小时+60秒硬期限纸面退出。', scope, p.protection_confirmations)
        flat_invalid = _condition(identity + ':flat-invalid', 'invalidation', p.flat_invalidation, expiry,
            _rule_text(p.flat_invalidation) + ' 或原件缺失/过期时禁止新增风险；窗口不会延长。', scope, p.protection_confirmations)
        long_entry = _condition(identity + ':long-entry', 'entry', p.long_entry, entry_end,
            f'已有多仓：{_rule_text(p.long_entry)} 连续{p.long_confirmations}根已收盘分钟确认，建议按目录减仓；不假设实际账户持仓。', scope, p.long_confirmations)
        long_exit = _condition(identity + ':long-exit', 'exit', p.long_exit, expiry,
            _rule_text(p.long_exit) + ' 弱势延续，重新评估剩余多仓；不自动下单。', scope, p.protection_confirmations)
        long_invalid = _condition(identity + ':long-invalid', 'invalidation', p.long_invalidation, expiry,
            _rule_text(p.long_invalidation) + ' 减仓前提失效，重新评估，不自动追涨补仓。', scope, p.protection_confirmations)

        def proposal(position, action, entry, exit_condition, invalid):
            return ResearchActionCandidate(
                schema_version='research-action-candidate.v1', scope_id=scope, horizon='24h', as_of=asof,
                valid_until=expiry, next_review_at=refresh, position_context=position, action=action,
                probability=dict(target_id=catalog['targets']['24h']['ref'], value=None, kind='unavailable', provenance_ref=None),
                entry=[entry], exit=[exit_condition], invalidation=[invalid], size_policy_ref=catalog['size_policy']['ref'],
                cost_policy_ref=catalog['cost_policy']['ref'], target_spec_ref=catalog['targets']['24h']['ref'],
                evidence_refs=[scenario.snapshot_id, 'event-response-model:' + model.payload_hash] + macro_refs,
                claim_refs=[], mechanism_review_ref=review_id,
                reason=p.rationale + ' 这是尚未激活的事件条件研究预案。先达目标后止损的概率尚不可用；另列同规则的历史假设回放净利估计，不冒充当前操作胜率。')

        plans.append(EventConditionalPlanV2(
            scenario_id=s.scenario_id, event_result_probability=s.event_result_probability, policy=p,
            activation_required=('官方实际值须与同一期次绑定并按相同prior_month/.05pp分格；未连接自动激活。',
                                 '临近公布重新认证背景与价量；当前日频背景不是公告瞬时方向信号。',
                                 '实际价量确认、60秒反应延迟与入场半开窗口均满足后才有条件动作。'),
            causal_chain=mechanism, alternative_mechanism=p.counter_hypothesis,
            flat_proposal=proposal('flat', 'enter_long', flat_entry, flat_exit, flat_invalid),
            long_proposal=proposal('long', 'reduce_long', long_entry, long_exit, long_invalid),
            scenario_asset_reaction_estimate=estimate_v2(model, target='asset_positive_event_24h', scenario_id=s.scenario_id),
            scenario_strategy_net_profit_estimate=estimate_v2(model, target=STRATEGY_TARGET, scenario_id=s.scenario_id)))
    missing = tuple(p.scenario_id for p in plans if p.scenario_asset_reaction_estimate.events == 0)
    gaps = ('市场调查共识和公告前定价尚未绑定；相对上月变化不能当超出市场预期。',
            '按当前宏观背景选择的规则在历史价格路径做假设回放，没有使用历史当时已知的宏观背景。',
            '样本与确认阈值未经前瞻校准；没有当前操作胜率或70%保证。',
            '净利估计只对应空仓条件入场规则；已有多仓减险不共享该盈利概率。',
            '价差、账户手续费、杠杆与真实成交未认证；自动激活和监控未接通。') + tuple(macro_context.gaps) + tuple(model.excluded)
    value = EventOwnedOpportunitySnapshotV2(
        snapshot_id='event-opp-' + canonical_hash([scenario.snapshot_id, scope, scenario.payload_hash, model.payload_hash, policy_catalog_hash])[:48],
        forecast_id=scenario.snapshot_id, event_key=scenario.event_key, reference_period=scenario.reference_period,
        instrument=instrument, asset_scope=scope, scheduled_at=t0, event_probability_as_of=scenario.as_of,
        as_of=asof, published_at=published_at, scenario_snapshot_ref=scenario.snapshot_id,
        scenario_snapshot_hash=scenario.payload_hash, entry_valid_from=t0 + timedelta(seconds=60),
        entry_valid_until=entry_end, exit_valid_until=expiry, market_model_as_of=asof,
        model_valid_until=refresh, reestimate_required_at=refresh, model=model,
        macro_context=macro_context, policy_catalog_hash=policy_catalog_hash,
        asset_reaction_estimate=estimate_v2(model, target='asset_positive_event_24h'),
        strategy_net_profit_estimate=estimate_v2(model, target=STRATEGY_TARGET),
        strategy_net_profit_given_entry_estimate=estimate_v2(model, target=STRATEGY_TARGET, entered_only=True),
        plans=tuple(plans), price_estimation_status='no_authenticated_price_samples' if not model.samples else 'event_day_baseline_only' if missing else 'all_scenarios_supported_estimated_uncalibrated',
        missing_scenario_ids=missing, gaps=gaps, payload_hash='0' * 64)
    return value.model_copy(update={'payload_hash': canonical_hash(value.model_dump(mode='json', exclude={'payload_hash'}))})
