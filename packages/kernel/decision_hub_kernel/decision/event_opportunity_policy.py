"""Pure CPI policy selection and the shared live/replay rule evaluator.

Thresholds are explicit, versioned research hypotheses, not fitted alpha. No
current price is frozen as a future announcement price. Publication permission
and native feature authentication remain the caller's independent obligations.
"""
from decimal import Decimal
from itertools import product
from types import SimpleNamespace

from ..ports.event_opportunity_policy import EventPolicyDefinition
from ..ports.market_path import canonical_hash, decimal_text
from .action_policy import evaluate_condition_expression, validate_condition_expression


_BANDS = ('cooler', 'near', 'hotter')
_FIELDS = ('flat_entry', 'flat_exit', 'flat_invalidation', 'long_entry', 'long_exit', 'long_invalidation')


def _leaf(scope, feature, cmp, threshold, unit):
    return dict(op='compare', feature=feature, scope_id=scope, unit=unit,
                cmp=cmp, threshold=decimal_text(Decimal(threshold)),
                max_age_seconds=3690 if feature == 'volume_ratio_1h' else 90)


def _selection(headline, core):
    # Distinguish direction disagreement from headline-only movement; core
    # pressure is not silently collapsed into headline pressure.
    if (headline, core) in {('cooler', 'hotter'), ('hotter', 'cooler')}:
        return ('headline_core_conflict', '.003', '1.4', '-.0035', 3,
                '总体与核心相反：不预设方向，要求更强价量持续确认，并较早保护已有多仓。')
    if headline == core == 'cooler':
        return ('joint_cooling', '0', '1.2', '-.005', 2,
                '总体与核心均较上月降温：降低通胀路径压力只是待证假设，正向价量确认后才考虑新增风险。')
    if core == 'hotter':
        return ('core_pressure', '.005', '1.5', '-.0025', 3,
                '核心较上月升温：新增多仓需更强反向价格确认；已有多仓采用更近的减险阈值。')
    if core == 'cooler':
        return ('core_cooling_headline_near', '.001', '1.25', '-.0045', 2,
                '核心降温而总体近于上月：支持性机制尚未确认，需价量确认且保留反向失效。')
    if headline == 'hotter':
        return ('headline_pressure_core_near', '.004', '1.45', '-.003', 3,
                '总体升温而核心近于上月：可能是成分噪声，要求反向价量确认，不能直接视为核心再通胀。')
    if headline == 'cooler':
        return ('headline_cooling_core_near', '.0025', '1.35', '-.004', 3,
                '只有总体降温：核心未同步，确认要求高于双降温情景。')
    return ('joint_near', '.002', '1.3', '-.004', 2,
            '总体与核心均近于上月：不作事件方向断言，由公布后价量与保护条件决定下一动作。')


def select_event_policy(scenario, macro_context, scope_id):
    """Select using both CPI bands and an already authenticated current context.

    The context is *current daily background*, not a survey, DXY, release-time
    surprise, or historical point-in-time input. A missing background has its
    own confirmation rule and gaps; it never supplies a bullish/bearish fact.
    """
    h, c = scenario.headline_band, scenario.core_band
    if h not in _BANDS or c not in _BANDS:
        raise ValueError('event_policy_scenario_band_invalid')
    state = macro_context.state
    if state not in {'unknown', 'headwind', 'tailwind', 'mixed'}:
        raise ValueError('event_policy_macro_state_invalid')
    if canonical_hash(macro_context.model_dump(mode='json', exclude={'payload_hash'})) != macro_context.payload_hash:
        raise ValueError('event_policy_macro_context_hash_invalid')
    variant, entry, volume, protect, confirmations, rationale = _selection(h, c)
    entry, volume, protect = Decimal(entry), Decimal(volume), Decimal(protect)
    if state == 'headwind':
        entry += Decimal('.002'); volume += Decimal('.2'); protect += Decimal('.001')
        confirmations = min(4, confirmations + 1)
        rationale += ' 当前认证的日频背景构成逆风，进一步提高新增风险的确认要求、收紧保护。'
    elif state in {'unknown', 'mixed'}:
        entry += Decimal('.001'); volume += Decimal('.1')
        confirmations = min(4, confirmations + 1)
        rationale += ' 日频宏观背景未知或分歧，不将其当方向信号；新增风险增加确认，保护分支仍有效。'
    else:
        rationale += ' 日频背景与风险偏好假设同向，但仍须公布后实际价量确认。'
    scope = scope_id
    positive = dict(op='all', args=[_leaf(scope, 'return_6h', 'gt', entry, 'fraction'),
                                    _leaf(scope, 'volume_ratio_1h', 'gte', volume, 'ratio')])
    negative = _leaf(scope, 'return_6h', 'lt', protect, 'fraction')
    rebound = _leaf(scope, 'return_6h', 'gt', entry, 'fraction')
    body = dict(policy_id=f'cpi-conditional-{variant}-{state}.v2', policy_hash='0' * 64,
                scenario_id=f'cpi:headline_{h}:core_{c}', headline_band=h, core_band=c,
                scope_id=scope, variant=variant, context_state=state,
                macro_context_hash=macro_context.input_hash,
                flat_entry=positive, flat_exit=negative, flat_invalidation=negative,
                long_entry=negative, long_exit=negative, long_invalidation=rebound,
                entry_confirmations=confirmations, rationale=rationale,
                counter_hypothesis='市场预期可能已计价，实际利率/广义美元的日频背景不能确定公告瞬时方向；头条成分、资金流与去杠杆可抵消或逆转上述机制。',
                gaps=tuple(macro_context.gaps) + (
                    '当前分格相对上月，不代表相对市场调查共识的惊喜。',
                    '阈值是待验证规则；历史路径回放不证明 alpha 或当前事件胜率。',
                    '采用合法滚动价量特征，未声称公告前固定区间或未来绝对入场价格。'))
    value = EventPolicyDefinition(**body)
    value = value.model_copy(update={'policy_hash': canonical_hash(value.model_dump(mode='json', exclude={'policy_hash'}))})
    verify_event_policy(value)
    return value


def event_policy_catalog(scope_id, macro_context):
    return tuple(select_event_policy(SimpleNamespace(headline_band=h, core_band=c), macro_context, scope_id)
                 for h, c in product(_BANDS, repeat=2))


def verify_event_policy(policy):
    if canonical_hash(policy.model_dump(mode='json', exclude={'policy_hash'})) != policy.policy_hash:
        raise ValueError('event_policy_hash_invalid')
    bindings = frozenset({('return_6h', policy.scope_id, 'fraction'), ('volume_ratio_1h', policy.scope_id, 'ratio')})
    for field in _FIELDS:
        validate_condition_expression(getattr(policy, field), allowed_features=bindings)
    return bindings


def evaluate_event_policy(policy, features, *, as_of, position='flat', phase='entry'):
    """Exactly the same bounded DSL evaluator is used by live callers and replay."""
    if position not in {'flat', 'long'} or phase not in {'entry', 'exit', 'invalidation'}:
        raise ValueError('event_policy_branch_invalid')
    bindings = verify_event_policy(policy)
    return evaluate_condition_expression(getattr(policy, f'{position}_{phase}'), features,
                                         as_of=as_of, allowed_features=bindings)
