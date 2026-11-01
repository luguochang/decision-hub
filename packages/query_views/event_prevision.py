"""Read published event forecasts and opportunities; no acquisition or model calls."""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.kernel.decision_hub_kernel.ports.market_path import FrozenContract, aware_utc


class EventPrevisionItem(FrozenContract):
    event_key: str
    title: str
    reference_period: str | None
    scheduled_at: AwareDatetime
    state: Literal['waiting_inputs', 'pending', 'running', 'published', 'failed', 'missed', 'not_scheduled']
    forecast_id: str | None
    last_error_code: str | None
    next_attempt_at: AwareDatetime | None
    updated_at: AwareDatetime | None


class EventPrevisionList(FrozenContract):
    schema_version: Literal['event-prevision-list.v1'] = 'event-prevision-list.v1'
    as_of: AwareDatetime
    events: tuple[EventPrevisionItem, ...]
    uncovered_families: tuple[str, ...] = ('fomc',)


class ScenarioProbabilityView(FrozenContract):
    scenario_id: str
    headline_band: Literal['cooler', 'near', 'hotter']
    core_band: Literal['cooler', 'near', 'hotter']
    probability: Decimal = Field(ge=0, le=1)
    historical_matches: int = Field(ge=0)


class ProbabilityEstimateView(FrozenContract):
    target: str
    probability_status: Literal['estimated_uncalibrated', 'unavailable']
    basis: str
    scenario_conditioned: bool
    value: Decimal | None
    lower_95: Decimal | None
    upper_95: Decimal | None
    events: int
    entered: int
    successes: int
    assumptions: tuple[str, ...]


class CurrentConfirmationView(FrozenContract):
    confirmation_protocol: Literal['event-confirmation.v1']
    strategy_target: Literal['cpi_event_anchored_policy_net_profit_event_24h.v1']
    policy_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    flat_entry: str
    flat_exit: str
    flat_invalidation: str
    long_entry: str
    long_invalidation: str
    probability_status: Literal['unknown'] = 'unknown'
    probability_gap: str


class ConditionalPlanView(FrozenContract):
    scenario_id: str
    event_probability: Decimal
    main_chain: tuple[str, ...]
    counter_mechanism: str
    activation_required: tuple[str, ...]
    flat_entry: tuple[str, ...]
    flat_exit: tuple[str, ...]
    flat_invalidation: tuple[str, ...]
    held_long_entry: tuple[str, ...]
    held_long_invalidation: tuple[str, ...]
    asset_estimate: ProbabilityEstimateView
    net_profit_estimate: ProbabilityEstimateView
    armed: Literal[False] = False
    current_confirmation: CurrentConfirmationView | None = None


class EventMacroSeriesView(FrozenContract):
    series_id: Literal['DGS10', 'DFII10', 'DTWEXBGS']
    label: str
    unit: Literal['percent', 'index_jan_2006_100']
    value: Decimal
    observation_date: date
    known_at: AwareDatetime
    change_5_observations: Decimal
    source_url: str
    observation_ref: str
    content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def source_units(self):
        unit = 'index_jan_2006_100' if self.series_id == 'DTWEXBGS' else 'percent'
        if self.unit != unit or not self.value.is_finite() or not self.change_5_observations.is_finite() or self.observation_date > self.known_at.date() or self.source_url != 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=' + self.series_id:
            raise ValueError('event_prevision_macro_source_or_unit_invalid')
        return self


class EventMacroContextView(FrozenContract):
    schema_version: Literal['event-macro-context-view.v1'] = 'event-macro-context-view.v1'
    as_of: AwareDatetime
    state: Literal['unknown', 'headwind', 'tailwind', 'mixed']
    series: tuple[EventMacroSeriesView, ...]
    gaps: tuple[str, ...]

    @model_validator(mode='after')
    def clocks(self):
        if len({s.series_id for s in self.series}) != len(self.series) or any(s.known_at > self.as_of for s in self.series):
            raise ValueError('event_prevision_macro_duplicate_or_future_known')
        return self


class EventAssetOpportunityView(FrozenContract):
    instrument: Literal['BTC-USDT', 'ETH-USDT']
    state: Literal['available', 'missing', 'expired', 'outdated']
    as_of: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    refresh_required_at: AwareDatetime | None = None
    entry_valid_from: AwareDatetime | None = None
    entry_valid_until: AwareDatetime | None = None
    exit_valid_until: AwareDatetime | None = None
    asset_estimate: ProbabilityEstimateView | None = None
    net_profit_estimate: ProbabilityEstimateView | None = None
    net_profit_given_entry_estimate: ProbabilityEstimateView | None = None
    plans: tuple[ConditionalPlanView, ...] = ()
    gaps: tuple[str, ...] = ()
    snapshot_ref: str | None = None
    snapshot_hash: str | None = None
    monitoring_armed: Literal[False] = False
    macro_context: EventMacroContextView | None = None


class EventPrevisionDetail(FrozenContract):
    schema_version: Literal['event-prevision-detail.v1'] = 'event-prevision-detail.v1'
    as_of: AwareDatetime
    event: EventPrevisionItem
    snapshot_ref: str | None
    snapshot_hash: str | None
    estimate_as_of: AwareDatetime | None
    published_at: AwareDatetime | None
    probability_status: Literal['estimated_uncalibrated', 'missing']
    baseline_kind: Literal['prior_month'] = 'prior_month'
    headline_baseline: Decimal | None
    core_baseline: Decimal | None
    tolerance_pp: Decimal | None
    target_definition: str | None
    sample_count: int
    scenarios: tuple[ScenarioProbabilityView, ...]
    assumptions: tuple[str, ...]
    assets: tuple[EventAssetOpportunityView, ...]


def _event(value):
    status = value.status
    return EventPrevisionItem(
        event_key=value.event_key, title='美国 CPI', reference_period=value.reference_period,
        scheduled_at=value.scheduled_at, state=status.state if status else 'not_scheduled',
        forecast_id=value.forecast_id,
        last_error_code=status.last_error_code if status else None,
        next_attempt_at=status.next_attempt_at if status else None,
        updated_at=status.updated_at if status else None,
    )


def _estimate(value):
    return ProbabilityEstimateView.model_validate(value.model_dump(include={
        'target', 'probability_status', 'basis', 'scenario_conditioned', 'value',
        'lower_95', 'upper_95', 'events', 'entered', 'successes', 'assumptions',
    }))


def _current_confirmation(plan):
    """Current monitor presentation from the same frozen-policy conversion.

    This projects a new rule, not a rewrite of the original plan or its scores.
    A V1 plan has no convertible policy and retains its historical presentation.
    """
    policy = getattr(plan, 'policy', None)
    if policy is None:
        return None
    from packages.kernel.decision_hub_kernel.decision.event_confirmation_policy import (
        PROBABILITY_GAP, confirmation_rule_summary, confirmation_spec,
    )
    spec = confirmation_spec(policy)
    negative = format(Decimal(spec.negative_threshold) * 100, 'f').rstrip('0').rstrip('.')
    positive = format(Decimal(spec.positive_threshold) * 100, 'f').rstrip('0').rstrip('.') or '0'
    hours, rest = divmod(spec.exit_end_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    deadline = ''.join(f'{n}{label}' for n, label in ((hours, '小时'), (minutes, '分钟'), (seconds, '秒')) if n)
    return CurrentConfirmationView(
        confirmation_protocol=spec.protocol, strategy_target=spec.strategy_target,
        policy_hash=spec.policy_hash,
        flat_entry=confirmation_rule_summary(spec, 'flat'),
        flat_exit=(f'相对公告前价格锚收益<{negative}%，连续{spec.protection_confirmations}根不同已收盘分钟确认后，'
                   f'再延迟{spec.reaction_delay_seconds}秒进行纸面退出；最迟公告后{deadline}退出。'),
        flat_invalidation=(f'相对公告前价格锚收益<{negative}%时停止新增风险；公告前价格锚、固定20分钟基准量'
                           '或公告后分钟原件缺失时不新增风险；观察窗口不会延长。'),
        long_entry=confirmation_rule_summary(spec, 'long'),
        long_invalidation=(f'相对公告前价格锚收益>{positive}%只表示当次减险条件不成立，继续监控后续回落，'
                           f'不会永久关闭保护；到公告后{spec.entry_end_seconds // 3600}小时仍未确认则结束本轮观察，'
                           '预判被替代或原件修订时另行核对。'),
        probability_gap=PROBABILITY_GAP,
    )


def _opportunity(value, instrument, forecast, now):
    if value is None:
        return EventAssetOpportunityView(instrument=instrument, state='missing')
    if value.event_key != forecast.event_key:
        raise ValueError('event_prevision_opportunity_forecast_mismatch')
    if (value.scenario_snapshot_ref != forecast.snapshot_id
            or value.scenario_snapshot_hash != forecast.payload_hash):
        return EventAssetOpportunityView(instrument=instrument, state='outdated',
            gaps=('这份机会属于上一版事件预测，当前预测的机会尚未生成。',))
    plans = tuple(ConditionalPlanView(
        scenario_id=p.scenario_id, event_probability=p.event_result_probability,
        main_chain=tuple(n.description for n in p.causal_chain.nodes),
        counter_mechanism=p.alternative_mechanism, activation_required=p.activation_required,
        flat_entry=tuple(c.original_text for c in p.flat_proposal.entry),
        flat_exit=tuple(c.original_text for c in p.flat_proposal.exit),
        flat_invalidation=tuple(c.original_text for c in p.flat_proposal.invalidation),
        held_long_entry=tuple(c.original_text for c in p.long_proposal.entry),
        held_long_invalidation=tuple(c.original_text for c in p.long_proposal.invalidation),
        asset_estimate=_estimate(p.scenario_asset_reaction_estimate),
        net_profit_estimate=_estimate(p.scenario_strategy_net_profit_estimate),
        current_confirmation=_current_confirmation(p),
    ) for p in value.plans)
    from packages.kernel.decision_hub_kernel.application.event_opportunity_snapshot import event_macro_context_user_view
    macro = event_macro_context_user_view(value)
    return EventAssetOpportunityView(
        instrument=instrument, state='expired' if now >= value.model_valid_until else 'available',
        as_of=value.as_of, published_at=value.published_at,
        refresh_required_at=value.reestimate_required_at,
        entry_valid_from=value.entry_valid_from, entry_valid_until=value.entry_valid_until,
        exit_valid_until=value.exit_valid_until, asset_estimate=_estimate(value.asset_reaction_estimate),
        net_profit_estimate=_estimate(value.strategy_net_profit_estimate),
        net_profit_given_entry_estimate=_estimate(value.strategy_net_profit_given_entry_estimate),
        plans=plans, gaps=value.gaps, snapshot_ref=value.snapshot_id, snapshot_hash=value.payload_hash,
        macro_context=EventMacroContextView.model_validate(macro) if macro is not None else None,
    )


class EventPrevisionQueryService:
    def __init__(self, database, *, archive=None, clock=utcnow):
        self.database = database
        self.archive = archive
        self.clock = clock

    def listing(self):
        from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import read_event_scenario_events_in_session
        now = aware_utc(self.clock())
        with self.database.session() as session:
            events = read_event_scenario_events_in_session(session, cutoff_at=now, limit=12)
            return EventPrevisionList(as_of=now, events=tuple(_event(e) for e in events))

    def detail(self, event_key):
        from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import (
            read_event_scenario_event_in_session, read_latest_event_owned_scenario_for_event_in_session,
        )
        from packages.kernel.decision_hub_kernel.application.event_owned_opportunity import read_latest_event_owned_opportunity_for_event_in_session
        from packages.kernel.decision_hub_kernel.application.event_read_scope import native_event_read_scope
        from packages.kernel.decision_hub_kernel.application.cpi_pre_event_context import _archive
        now = aware_utc(self.clock())
        archive = self.archive or _archive()
        with self.database.session() as session, native_event_read_scope(session, archive=archive):
            event = read_event_scenario_event_in_session(session, event_key, cutoff_at=now)
            if event is None:
                return None
            forecast = read_latest_event_owned_scenario_for_event_in_session(
                session, event_key, archive=archive, cutoff_at=now,
            )
            if forecast is None:
                return EventPrevisionDetail(as_of=now, event=_event(event), snapshot_ref=None,
                    snapshot_hash=None, estimate_as_of=None, published_at=None,
                    probability_status='missing', headline_baseline=None, core_baseline=None,
                    tolerance_pp=None, target_definition=None, sample_count=0, scenarios=(),
                    assumptions=(), assets=tuple(EventAssetOpportunityView(instrument=i, state='missing')
                    for i in ('BTC-USDT', 'ETH-USDT')))
            assets = tuple(_opportunity(read_latest_event_owned_opportunity_for_event_in_session(
                session, event_key, instrument, archive=archive, cutoff_at=now,
            ), instrument, forecast, now) for instrument in ('BTC-USDT', 'ETH-USDT'))
            return EventPrevisionDetail(as_of=now, event=_event(event), snapshot_ref=forecast.snapshot_id,
                snapshot_hash=forecast.payload_hash, estimate_as_of=forecast.as_of,
                published_at=forecast.published_at, probability_status=forecast.probability_status,
                headline_baseline=forecast.headline_baseline, core_baseline=forecast.core_baseline,
                tolerance_pp=forecast.tolerance_pp, target_definition=forecast.target_definition,
                sample_count=forecast.sample_count, assumptions=forecast.assumptions, assets=assets,
                scenarios=tuple(ScenarioProbabilityView(scenario_id=s.scenario_id,
                    headline_band=s.headline_band, core_band=s.core_band,
                    probability=s.event_result_probability, historical_matches=s.matched_historical_events)
                    for s in forecast.scenarios))
