"""Replay the current conditional CPI rule on authenticated historical prices.

This is a retrospective what-if, not a historical point-in-time backtest. The
current macro context chooses the rule; it is never relabelled as historically
known. Price feature clocks below are explicitly simulated close clocks. Native
price provenance/known-at authentication is performed by the reader before this
pure calculation, and remains in each sample's path hashes and capture list.
"""
from datetime import timedelta
from decimal import Decimal, localcontext
from statistics import median
from types import SimpleNamespace

from ..ports.market_path import canonical_hash, path_hashes
from ..ports.event_response_path import EventResponseSampleV2
from .action_policy import ActionFeatureValue, confirmed_bar_streak
from .event_response_model import scenario_band, estimate as estimate_v1
from .event_opportunity_policy import select_event_policy, evaluate_event_policy, verify_event_policy

STRATEGY_TARGET = 'cpi_scenario_policy_net_profit_event_24h.v2'


def replay_feature_frames(bars, scope_id):
    """Same 361 close / 21 complete UTC hours / 18 decimal feature definitions.

    Build one index and cache complete-hour volumes; future rows are never used
    by a frame. This avoids recomputing the entire path index for every minute.
    Frames are what-if inputs and must never be persisted as native features.
    """
    ordered = tuple(sorted(bars, key=lambda b: b.open_at))
    by = {b.open_at: b for b in ordered}
    hours = {}
    for bar in ordered:
        hour = bar.open_at.replace(minute=0, second=0, microsecond=0)
        hours.setdefault(hour, []).append(bar)
    volumes = {hour: sum((b.volume_base for b in rows), Decimal(0))
               for hour, rows in hours.items()
               if len(rows) == 60 and all(b.volume_base is not None for b in rows)
               and all(b.open_at == hour + timedelta(minutes=i) for i, b in enumerate(rows))}
    for bar in ordered:
        boundary = bar.close_at.replace(minute=0, second=0, microsecond=0)
        hour_values = [volumes.get(boundary - timedelta(hours=i + 1)) for i in range(21)]
        prior = by.get(bar.open_at - timedelta(hours=6))
        ret = ratio = None
        # Full path continuity is independently required by response_sample_v2.
        with localcontext() as ctx:
            ctx.prec = 50
            if prior is not None:
                ret = (bar.close / prior.close - 1).quantize(Decimal('0.000000000000000001'))
            if all(v is not None for v in hour_values):
                base = median(hour_values[1:])
                if base > 0:
                    ratio = (hour_values[0] / base).quantize(Decimal('0.000000000000000001'))
        result = {}
        for name, unit, value, observed in (
            ('return_6h', 'fraction', ret, bar.close_at),
            ('volume_ratio_1h', 'ratio', ratio, boundary),
        ):
            digest = canonical_hash({'recipe': 'event-policy-what-if-feature.v2', 'scope': scope_id,
                                     'feature': name, 'value': str(value), 'observed': observed.isoformat()})
            result[(name, scope_id)] = ActionFeatureValue(
                feature=name, scope_id=scope_id, unit=unit, value=value,
                observed_at=observed, known_at=observed,
                quality='accepted' if value is not None else 'unknown',
                source_ref='what-if-feature:' + digest, source_hash=digest)
        yield bar, result


def replay_policy_path(policy, bars, *, scheduled_at):
    """One conditional long policy, including protection priority and delayed fill.

    Entry is half-open [T0+60s, T0+6h). Both entry and protective exit use
    confirming closed bars followed by a 60 second reaction delay. Exit checks
    cannot use a bar that closed before the pending entry had actually filled.
    """
    verify_event_policy(policy)
    ordered = tuple(sorted(bars, key=lambda b: b.open_at))
    by = {b.open_at: b for b in ordered}
    start = scheduled_at + timedelta(seconds=policy.entry_start_seconds)
    entry_end = scheduled_at + timedelta(seconds=policy.entry_end_seconds)
    end = scheduled_at + timedelta(seconds=policy.exit_end_seconds)
    if len(by) != len(ordered):
        raise ValueError('event_policy_replay_duplicate_bar')
    if not ordered or ordered[-1].close_at != end:
        raise ValueError('event_policy_replay_end_missing')
    first = scheduled_at.replace(minute=0, second=0, microsecond=0) - timedelta(hours=21)
    if (ordered[0].open_at != first or len(ordered) != int((end - first).total_seconds() / 60)
            or any(b.open_at != first + timedelta(minutes=i) for i, b in enumerate(ordered))):
        raise ValueError('event_policy_replay_path_gap')
    if any(b.scope_id != policy.scope_id for b in ordered):
        raise ValueError('event_policy_replay_scope_mismatch')
    entry = None; exit_at = None; exit_price = None
    streak = 0; watermark = None
    for bar, features in replay_feature_frames(ordered, policy.scope_id):
        at = bar.close_at
        if at < start:
            continue
        if entry is None and at >= entry_end:
            break
        if entry is not None and at <= entry.open_at:
            continue
        if any(f.value is None for f in features.values()):
            raise ValueError('event_policy_replay_required_feature_missing')
        protection = evaluate_event_policy(policy, features, as_of=at, phase='exit')
        invalid = evaluate_event_policy(policy, features, as_of=at, phase='invalidation')
        if protection.status != 'ok' or invalid.status != 'ok':
            raise ValueError('event_policy_replay_protection_invalid')
        if entry is None:
            condition = evaluate_event_policy(policy, features, as_of=at, phase='entry')
            if condition.status != 'ok':
                raise ValueError('event_policy_replay_entry_invalid')
            truth = condition.truth if protection.truth == invalid.truth == 'false' else 'false'
            streak, confirmed = confirmed_bar_streak(streak, watermark, at, truth,
                                                     required=policy.entry_confirmations)
            watermark = at
            if confirmed:
                fill_at = at + timedelta(seconds=policy.reaction_delay_seconds)
                if fill_at >= entry_end:
                    break
                entry = by.get(fill_at)
                if entry is None:
                    raise ValueError('event_policy_replay_entry_fill_missing')
        elif protection.truth == 'true' or invalid.truth == 'true':
            fill_at = at + timedelta(seconds=policy.reaction_delay_seconds)
            if fill_at >= end:
                break
            fill = by.get(fill_at)
            if fill is None:
                raise ValueError('event_policy_replay_exit_fill_missing')
            exit_at, exit_price = fill.open_at, fill.open
            break
    net = Decimal(0)
    if entry is not None:
        if exit_at is None:
            exit_at, exit_price = end, ordered[-1].close
        cost = Decimal(policy.fee_fraction_each_side) + Decimal(policy.slippage_fraction_each_side)
        net = (exit_price * (1 - cost)) / (entry.open * (1 + cost)) - 1
    return dict(strategy_entered=entry is not None,
                strategy_entry_at=entry.open_at if entry else None,
                strategy_exit_at=exit_at, strategy_net_return=net)


def response_sample_v2(release, *, release_hash, bls, bls_hash, rates, scope, bars, captures, macro_context):
    t0 = release.payload.scheduled_at
    start, end = t0 + timedelta(seconds=60), t0 + timedelta(hours=24, seconds=60)
    ordered = tuple(sorted(bars, key=lambda b: b.open_at))
    first = t0.replace(minute=0, second=0, microsecond=0) - timedelta(hours=21)
    if (not ordered or ordered[0].open_at != first or ordered[-1].close_at != end
            or len(ordered) != int((end - first).total_seconds() / 60)
            or any(b.open_at != first + timedelta(minutes=i) for i, b in enumerate(ordered))):
        raise ValueError('event_response_native_path_gap')
    if any(b.scope_id != scope.scope_id for b in ordered):
        raise ValueError('event_policy_replay_scope_mismatch')
    h, c = rates['headline_mom'], rates['core_mom']
    if any(v is None for v in (h.actual_value, h.prior_value, c.actual_value, c.prior_value)):
        raise ValueError('event_response_cpi_rates_missing')
    scenario = SimpleNamespace(headline_band=scenario_band(h.actual_value, h.prior_value),
                               core_band=scenario_band(c.actual_value, c.prior_value))
    policy = select_event_policy(scenario, macro_context, scope.scope_id)
    semantic, provenance = path_hashes(scope, ordered)
    replay = replay_policy_path(policy, ordered, scheduled_at=t0)
    by = {b.open_at: b for b in ordered}
    return EventResponseSampleV2(
        event_key=release.payload.event_key, reference_period=release.payload.reference_period,
        scheduled_at=t0, release_observation_id=release.observation_id,
        release_content_hash=release_hash, release_known_at=release.known_at,
        bls_observation_id=bls.observation_id, bls_content_hash=bls_hash, bls_known_at=bls.known_at,
        headline_actual=h.actual_value, headline_prior=h.prior_value,
        core_actual=c.actual_value, core_prior=c.prior_value,
        scenario_id=policy.scenario_id, reaction_start_at=start, reaction_end_at=end,
        reaction_return=ordered[-1].close / by[start].open - 1,
        path_semantic_hash=semantic, path_provenance_hash=provenance,
        capture_originals=tuple(sorted(set(captures))),
        policy_id=policy.policy_id, policy_hash=policy.policy_hash, **replay)


def estimate_v2(model, *, target, scenario_id=None, entered_only=False):
    if model.model_version != 'cpi-scenario-policy-empirical.v2':
        raise ValueError('event_policy_estimate_requires_v2_replay')
    if target not in {'asset_positive_event_24h', STRATEGY_TARGET}:
        raise ValueError('event_policy_estimate_target_invalid')
    result = estimate_v1(model, target=target, scenario_id=scenario_id, entered_only=entered_only)
    return result.model_copy(update={'assumptions': result.assumptions + (
        'V2 policy selected using the current authenticated daily macro background; retrospective what-if, not historical macro PIT.',
        'Net return recomputed by the same V2 scenario DSL/confirmation/delay/cost rule as the current preplan; not the legacy strategy probability.',
        'Event-day aggregate follows the actual CPI prior-month band in each historical event; the current event has no asserted winning probability.',
    )})
