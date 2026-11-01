from __future__ import annotations

from dataclasses import dataclass

from packages.contracts_py.decision_hub_contracts import FactEnvelope


@dataclass(frozen=True)
class RelationshipMatch:
    baseline_fact_id: str
    after_fact_id: str
    event_id: str | None
    instrument: str | None
    venue: str | None
    unit: str


def validate_baseline_after(
    baseline: FactEnvelope,
    after: FactEnvelope,
    *,
    event_id: str | None = None,
) -> RelationshipMatch:
    """Allow a delta only when both facts describe the same observable.

    This is intentionally strict: source, event, instrument, venue, unit and
    non-overlapping windows must agree. It prevents a plausible looking delta
    assembled from different assets, venues or time periods.
    """

    if baseline.event_offset not in {"baseline", "t-5m", "t-1h"}:
        raise ValueError("relationship_baseline_offset_invalid")
    if after.event_offset not in {"t0", "after", "t+1m", "t+5m"}:
        raise ValueError("relationship_after_offset_invalid")
    for field in ("requirement_id", "metric_family", "field", "source_id", "unit"):
        if getattr(baseline, field) != getattr(after, field):
            raise ValueError(f"relationship_{field}_mismatch")
    if baseline.instrument != after.instrument:
        raise ValueError("relationship_instrument_mismatch")
    if baseline.venue != after.venue:
        raise ValueError("relationship_venue_mismatch")
    baseline_event = _event_id(baseline)
    after_event = _event_id(after)
    if baseline_event is not None or after_event is not None:
        if baseline_event is None or after_event is None or baseline_event != after_event:
            raise ValueError("relationship_event_mismatch")
    if event_id is not None and (baseline_event != event_id or after_event != event_id):
        raise ValueError("relationship_event_mismatch")
    if (
        baseline.window_end_at is not None
        and after.window_start_at is not None
        and baseline.window_end_at > after.window_start_at
    ):
        raise ValueError("relationship_window_overlap")
    return RelationshipMatch(
        baseline_fact_id=baseline.fact_id,
        after_fact_id=after.fact_id,
        event_id=event_id or baseline_event,
        instrument=baseline.instrument,
        venue=baseline.venue,
        unit=baseline.unit,
    )


def _event_id(fact: FactEnvelope) -> str | None:
    value = fact.attributes.get("event_id")
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("relationship_event_mismatch")
