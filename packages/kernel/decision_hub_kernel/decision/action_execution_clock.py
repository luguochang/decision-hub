"""Pure execution timing, never authentication of caller-supplied timestamps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc


@dataclass(frozen=True)
class ExecutionWindow:
    base_at: datetime
    eligible_at: datetime
    quote_deadline: datetime
    channel: Literal["shadow_engine", "pc_visible"]
    delay_seconds: Literal[0, 60, 300]


def execution_window(
    *,
    publication_available_at: datetime,
    trigger_input_known_at: datetime,
    evaluation_available_at: datetime,
    admission_valid_at: datetime,
    channel: Literal["shadow_engine", "pc_visible"],
    delay_seconds: Literal[0, 60, 300] = 60,
    display_received_at: datetime | None = None,
) -> ExecutionWindow | None:
    """All clocks must come from separately authenticated committed originals."""
    if channel not in ("shadow_engine", "pc_visible"):
        raise ValueError("action_execution_channel_unsupported")
    if type(delay_seconds) is not int or delay_seconds not in (0, 60, 300):
        raise ValueError("action_execution_delay_not_registered")
    publication, known, available, admission = (
        aware_utc(value)
        for value in (
            publication_available_at,
            trigger_input_known_at,
            evaluation_available_at,
            admission_valid_at,
        )
    )
    if available < known:
        raise ValueError("action_execution_evaluation_before_input")
    base = max(publication, known, available, admission)
    if display_received_at is not None:
        display_received_at = aware_utc(display_received_at)
    if channel == "pc_visible":
        if display_received_at is None:
            return None
        # Publication's first service read may be later than a genuine UI receipt.
        # Exact revision/owner authentication belongs to the application reader.
        base = max(base, display_received_at)
    eligible = base + timedelta(seconds=delay_seconds)
    return ExecutionWindow(base, eligible, eligible + timedelta(seconds=60), channel, delay_seconds)


def exit_trigger_deadline(
    *,
    revision_valid_until: datetime,
    opportunity_end: datetime,
    entry_at: datetime,
    max_hold_seconds: int,
) -> datetime:
    if type(max_hold_seconds) is not int or not 0 < max_hold_seconds <= 86400:
        raise ValueError("action_execution_hold_limit_unsupported")
    valid, end, entry = (
        aware_utc(value) for value in (revision_valid_until, opportunity_end, entry_at)
    )
    if entry >= min(valid, end):
        raise ValueError("action_execution_entry_after_expiry")
    return min(valid, end, entry + timedelta(seconds=max_hold_seconds))


def validate_selected_quote(
    *,
    window: ExecutionWindow,
    observed_at: datetime,
    received_at: datetime,
    available_at: datetime,
    executed_at: datetime,
    entry_valid_until: datetime | None = None,
) -> None:
    """Validate the independently selected FIRST quote; do not search later prices."""
    observed, received, available, executed = (
        aware_utc(value) for value in (observed_at, received_at, available_at, executed_at)
    )
    if not observed <= received <= available <= executed:
        raise ValueError("action_execution_quote_clock_order")
    if observed < window.eligible_at:
        raise ValueError("action_execution_quote_before_eligible")
    if available > window.quote_deadline:
        raise ValueError("action_execution_quote_late")
    if executed > window.quote_deadline:
        raise ValueError("action_execution_missed_quote_window")
    if executed - observed > timedelta(seconds=10):
        raise ValueError("action_execution_quote_stale_at_execution")
    if entry_valid_until is not None and executed >= aware_utc(entry_valid_until):
        raise ValueError("action_execution_entry_after_expiry")


def validate_selected_interval(
    *,
    window: ExecutionWindow,
    observed_local_lower: datetime,
    observed_local_upper: datetime,
    capture_started_at: datetime,
    received_at: datetime,
    available_at: datetime,
    executed_at: datetime,
    entry_valid_until: datetime | None = None,
) -> None:
    lower, upper, started, received, available, executed = map(
        aware_utc,
        (
            observed_local_lower,
            observed_local_upper,
            capture_started_at,
            received_at,
            available_at,
            executed_at,
        ),
    )
    if not lower <= upper <= received <= available <= executed:
        raise ValueError("action_execution_quote_clock_order")
    if min(lower, started) < window.eligible_at:
        raise ValueError("action_execution_quote_before_eligible")
    if available > window.quote_deadline or executed > window.quote_deadline:
        raise ValueError("action_execution_missed_quote_window")
    if executed - lower > timedelta(seconds=10):
        raise ValueError("action_execution_quote_stale_at_execution")
    if entry_valid_until is not None and executed >= aware_utc(entry_valid_until):
        raise ValueError("action_execution_entry_after_expiry")
