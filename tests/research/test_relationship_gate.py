from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import FactEnvelope
from packages.kernel.decision_hub_kernel.decision.relationship_gate import (
    validate_baseline_after,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _fact(
    fact_id: str,
    offset: str,
    start: datetime,
    end: datetime,
    **attrs: float | str | bool | None,
) -> FactEnvelope:
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=fact_id,
        evidence_id=f"evidence-{fact_id}",
        requirement_id="cross_asset.confirmation",
        metric_family="market.return",
        field="event_return",
        instrument="BTC-USDT",
        venue="okx",
        value="1.0",
        unit="percent",
        window_start_at=start,
        window_end_at=end,
        event_offset=offset,
        observed_at=end,
        received_at=end,
        published_at=None,
        source_id="okx",
        independence_group="crypto-exchange",
        quality="accepted",
        delay_class="realtime",
        payload_schema_ref="market.v1",
        payload_hash="a" * 64,
        attributes={"event_id": "event-1", **attrs},
    )


def test_relationship_gate_accepts_same_observable_and_window() -> None:
    baseline = _fact("b", "baseline", NOW, NOW + timedelta(minutes=1))
    after = _fact("a", "t0", NOW + timedelta(minutes=5), NOW + timedelta(minutes=6))
    match = validate_baseline_after(baseline, after, event_id="event-1")
    assert match.unit == "percent"
    assert match.instrument == "BTC-USDT"
    assert match.event_id == "event-1"


def test_relationship_gate_rejects_mismatched_unit_or_event() -> None:
    baseline = _fact("b", "baseline", NOW, NOW + timedelta(minutes=1))
    wrong_unit = _fact("a", "t0", NOW + timedelta(minutes=5), NOW + timedelta(minutes=6))
    wrong_unit = wrong_unit.model_copy(update={"unit": "bps"})
    with pytest.raises(ValueError, match="relationship_unit_mismatch"):
        validate_baseline_after(baseline, wrong_unit, event_id="event-1")
    wrong_event = _fact(
        "a2", "t0", NOW + timedelta(minutes=5), NOW + timedelta(minutes=6), event_id="event-2"
    )
    with pytest.raises(ValueError, match="relationship_event_mismatch"):
        validate_baseline_after(baseline, wrong_event, event_id="event-1")


def test_relationship_gate_rejects_mismatched_event_without_expected_id() -> None:
    baseline = _fact("b", "baseline", NOW, NOW + timedelta(minutes=1), event_id="event-1")
    after = _fact(
        "a",
        "t0",
        NOW + timedelta(minutes=5),
        NOW + timedelta(minutes=6),
        event_id="event-2",
    )
    with pytest.raises(ValueError, match="relationship_event_mismatch"):
        validate_baseline_after(baseline, after)


def test_relationship_gate_returns_fact_event_without_expected_id() -> None:
    baseline = _fact("b", "baseline", NOW, NOW + timedelta(minutes=1))
    after = _fact("a", "t0", NOW + timedelta(minutes=5), NOW + timedelta(minutes=6))

    assert validate_baseline_after(baseline, after).event_id == "event-1"
