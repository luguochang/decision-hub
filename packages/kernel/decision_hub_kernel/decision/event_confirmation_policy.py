"""Announcement-anchored, deterministic confirmation shared by live and replay.

Native authentication is the caller's obligation. This pure protocol preserves
known-at cutoffs, fixed pre-event baselines and distinct closed-minute streaks.
It does not reuse the rolling V2 strategy's probability or execution authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from statistics import median
from typing import Literal

from ..ports.action_monitor import PerConditionResultV2
from ..ports.event_opportunity_monitor import EventConfirmationInputs
from ..ports.event_opportunity_policy import EventPolicyDefinition
from ..ports.market_path import MarketCandle, aware_utc, canonical_hash
from .action_policy import ActionConditionResult, confirmed_bar_streak
from .event_opportunity_policy import verify_event_policy

CONFIRMATION_PROTOCOL = "event-confirmation.v1"
STRATEGY_TARGET = "cpi_event_anchored_policy_net_profit_event_24h.v1"
PROBABILITY_GAP = "公告锚定规则尚无独立成熟样本和校准结果，不能沿用旧滚动价量策略的净盈利概率。"
PENDING_RULE_SUMMARY = (
    "以公告前最后完整分钟收盘价为价格锚，公告后完整分钟成交量与固定公告前20分钟中位量比较；"
    "具体阈值须绑定已认证实际情景。旧6小时收益和小时量仅作背景。"
)


def _number(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        value = value.quantize(Decimal("0.000000000000000001"))
    text = format(value, "f").rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


@dataclass(frozen=True)
class EventConfirmationSpec:
    source_policy_hash: str
    scope_id: str
    positive_threshold: str
    negative_threshold: str
    volume_threshold: str
    entry_confirmations: int
    protection_confirmations: int
    long_confirmations: int
    protocol: str = CONFIRMATION_PROTOCOL
    strategy_target: str = STRATEGY_TARGET
    anchor: str = "last_complete_minute_close_at_or_before_T0"
    volume_baseline: str = "fixed_20_contiguous_complete_minutes_before_T0_median"
    long_positive_behavior: str = "wait_and_keep_protection_until_window_end"
    entry_start_seconds: int = 60
    entry_end_seconds: int = 21600
    exit_end_seconds: int = 86460
    reaction_delay_seconds: int = 60

    @property
    def policy_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class EventConfirmationResult:
    protocol: str
    strategy_target: str
    policy_hash: str
    rule_summary: str
    per_condition: tuple[PerConditionResultV2, ...]
    entry: ActionConditionResult
    exit: ActionConditionResult
    invalidation: ActionConditionResult
    inputs: EventConfirmationInputs
    gaps: tuple[str, ...]


def _threshold(expression, *, scope, feature, unit, cmp):
    expected = {
        "op": "compare", "feature": feature, "scope_id": scope, "unit": unit,
        "cmp": cmp, "threshold": expression.get("threshold"),
        "max_age_seconds": 3690 if feature == "volume_ratio_1h" else 90,
    }
    if expression != expected:
        raise ValueError("event_confirmation_unsupported_frozen_expression")
    return Decimal(expression["threshold"])


def confirmation_spec(policy: EventPolicyDefinition) -> EventConfirmationSpec:
    """Explicitly convert only the frozen V2 rule shape, never arbitrary DSL."""
    verify_event_policy(policy)
    entry = policy.flat_entry
    if set(entry) != {"op", "args"} or entry["op"] != "all" or len(entry["args"]) != 2:
        raise ValueError("event_confirmation_unsupported_frozen_expression")
    positive = _threshold(entry["args"][0], scope=policy.scope_id, feature="return_6h", unit="fraction", cmp="gt")
    volume = _threshold(entry["args"][1], scope=policy.scope_id, feature="volume_ratio_1h", unit="ratio", cmp="gte")
    negative = _threshold(policy.flat_exit, scope=policy.scope_id, feature="return_6h", unit="fraction", cmp="lt")
    if (
        any(getattr(policy, name) != policy.flat_exit for name in ("flat_invalidation", "long_entry", "long_exit"))
        or policy.long_invalidation != entry["args"][0]
        or positive < 0 or negative >= 0 or volume <= 0
    ):
        raise ValueError("event_confirmation_frozen_branch_mismatch")
    return EventConfirmationSpec(
        source_policy_hash=policy.policy_hash, scope_id=policy.scope_id,
        positive_threshold=_number(positive), negative_threshold=_number(negative),
        volume_threshold=_number(volume), entry_confirmations=policy.entry_confirmations,
        protection_confirmations=policy.protection_confirmations,
        long_confirmations=policy.long_confirmations,
    )


def confirmation_rule_summary(spec: EventConfirmationSpec, position_context: str) -> str:
    positive = _number(Decimal(spec.positive_threshold) * 100)
    negative = _number(Decimal(spec.negative_threshold) * 100)
    basis = "价格锚=公告前最后完整分钟收盘；量基准=固定公告前20个连续完整分钟成交量中位数。"
    if position_context == "flat":
        rule = (f"公告后相对锚收益>{positive}%且该分钟量≥基准{spec.volume_threshold}倍，"
                f"连续{spec.entry_confirmations}根不同已收盘分钟才确认新增风险；"
                f"相对锚收益<{negative}%立即停止新增风险并提示保护。")
    elif position_context == "long":
        rule = (f"公告后相对锚收益<{negative}%，连续{spec.long_confirmations}根不同已收盘分钟确认减险；"
                f"相对锚收益>{positive}%时本次减险条件不成立，继续监控后续回落，不永久关闭保护。")
    else:
        raise ValueError("event_confirmation_position_invalid")
    return basis + rule + "新增风险确认窗为公告后60秒至6小时，纸面动作另延迟60秒；旧6小时收益/小时量仅作背景。"


def evaluate_event_confirmation(
    policy: EventPolicyDefinition, *, position_context: Literal["flat", "long"],
    scheduled_at: datetime, as_of: datetime, activation_known_at: datetime,
    candles: Sequence[MarketCandle], previous: Sequence[PerConditionResultV2] = (),
) -> EventConfirmationResult:
    spec = confirmation_spec(policy)
    summary = confirmation_rule_summary(spec, position_context)
    t0, cutoff, activated = map(aware_utc, (scheduled_at, as_of, activation_known_at))
    baseline_end = t0.replace(second=0, microsecond=0)
    baseline_start = baseline_end - timedelta(minutes=20)
    by_close = {}
    for bar in candles:
        if bar.scope_id != spec.scope_id:
            raise ValueError("event_confirmation_scope_mismatch")
        if bar.close_at <= cutoff and bar.known_at <= cutoff:
            if bar.close_at in by_close and by_close[bar.close_at] != bar:
                raise ValueError("event_confirmation_duplicate_minute")
            by_close[bar.close_at] = bar
    anchor = by_close.get(baseline_end)
    baseline = [by_close.get(baseline_start + timedelta(minutes=i)) for i in range(1, 21)]
    post = [bar for bar in by_close.values() if bar.open_at >= t0]
    current = max(post, key=lambda bar: bar.close_at) if post else None
    gaps = []
    if anchor is None:
        gaps.append("event_confirmation_anchor_missing")
    if any(bar is None for bar in baseline):
        gaps.append("event_confirmation_baseline_minutes_missing")
    elif any(bar.volume_base is None for bar in baseline):
        gaps.append("event_confirmation_baseline_volume_missing")
    base_volume = median([bar.volume_base for bar in baseline]) if not gaps else None
    if base_volume is not None and base_volume <= 0:
        gaps.append("event_confirmation_baseline_volume_zero")
    if current is None:
        gaps.append("event_confirmation_post_release_minute_missing")
    elif current.close_at < max(t0 + timedelta(seconds=60), activated) or activated > cutoff:
        gaps.append("event_confirmation_after_actual_known_minute_missing")
    elif (cutoff - current.close_at).total_seconds() > 90:
        gaps.append("event_confirmation_current_minute_stale")
    if current is not None and current.volume_base is None:
        gaps.append("event_confirmation_current_volume_missing")
    with localcontext() as context:
        context.prec = 50
        event_return = current.close / anchor.close - 1 if anchor is not None and current is not None else None
        ratio = current.volume_base / base_volume if (
            current is not None and current.volume_base is not None and base_volume is not None and base_volume > 0
        ) else None
    used = [*baseline, current]
    refs = tuple(dict.fromkeys(bar.capture_ref for bar in used if bar is not None and bar.capture_ref))
    inputs = EventConfirmationInputs(
        scheduled_at=t0, anchor_close_at=anchor.close_at if anchor else None,
        anchor_price=_number(anchor.close) if anchor else None,
        baseline_start_at=baseline_start, baseline_end_at=baseline_end,
        baseline_minute_count=sum(bar is not None for bar in baseline),
        baseline_volume_median=_number(base_volume) if base_volume is not None else None,
        closed_bar_at=current.close_at if current else None,
        current_close=_number(current.close) if current else None,
        current_minute_volume=_number(current.volume_base) if current and current.volume_base is not None else None,
        event_return=_number(event_return) if event_return is not None else None,
        minute_volume_ratio=_number(ratio) if ratio is not None else None, input_refs=refs,
    )
    positive = event_return is not None and event_return > Decimal(spec.positive_threshold)
    negative = event_return is not None and event_return < Decimal(spec.negative_threshold)
    entry = positive and ratio is not None and ratio >= Decimal(spec.volume_threshold) if position_context == "flat" else negative
    raw = {"entry": entry, "exit": negative, "invalidation": negative if position_context == "flat" else False}
    required = {"entry": spec.entry_confirmations if position_context == "flat" else spec.long_confirmations,
                "exit": spec.protection_confirmations, "invalidation": spec.protection_confirmations}
    prior = {item.id: item for item in previous}
    baseline_identity = canonical_hash({
        "policy": spec.policy_hash, "scheduled_at": t0.isoformat(),
        "baseline": [bar.model_dump(mode="json") if bar else None for bar in baseline],
    })
    results = []
    for phase in ("entry", "exit", "invalidation"):
        ident = f"event-confirmation:{baseline_identity[:32]}:{position_context}:{phase}"
        last = prior.get(ident)
        phase_gaps = list(gaps)
        if phase == "entry" and not t0 + timedelta(seconds=60) <= cutoff < t0 + timedelta(hours=6):
            phase_gaps.append("event_confirmation_entry_window_closed")
        if cutoff >= t0 + timedelta(seconds=spec.exit_end_seconds):
            phase_gaps.append("event_confirmation_exit_window_closed")
        if current and last and last.last_bar_at and current.close_at < last.last_bar_at:
            phase_gaps.append("event_confirmation_minute_time_regressed")
        truth = "unknown" if phase_gaps else "true" if raw[phase] else "false"
        count = 0
        last_at = last.last_bar_at if last else None
        if truth == "true" and current:
            count, _ = confirmed_bar_streak(last.confirmation_count if last else 0, last_at, current.close_at,
                                            truth, required=required[phase], interval_seconds=60)
        if current and (last_at is None or current.close_at > last_at):
            last_at = current.close_at
        confirmed = truth == "true" and count >= required[phase]
        results.append(PerConditionResultV2(
            id=ident, truth="true" if confirmed else "false" if truth == "true" else truth,
            raw_truth=truth, status="ok", reason=";".join(phase_gaps) or None,
            input_refs=list(refs), confirmation_count=count, last_bar_at=last_at, confirmed=confirmed,
        ))
    combined = [ActionConditionResult(item.truth, item.status, (item.reason,) if item.reason else (), tuple(item.input_refs)) for item in results]
    return EventConfirmationResult(CONFIRMATION_PROTOCOL, STRATEGY_TARGET, spec.policy_hash, summary,
                                   tuple(results), *combined, inputs, tuple(gaps))
