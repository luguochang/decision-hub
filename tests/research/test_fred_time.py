from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.macro_market.fred import FredSeriesResearchAdapter
from packages.provider_adapters.macro_market.fred_time import (
    fred_observation_date,
    qualify_fred_time,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def query(cutoff: datetime = NOW + timedelta(minutes=1)) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate({
        "schema_version": "research-capability-query.v1",
        "request_id": "fred-time-request",
        "capability_id": "market.cross_asset",
        "requirement_id": "cross_asset.rates",
        "query": "current FRED background",
        "target_url": None,
        "symbols": ["DGS2"],
        "fields": ["level"],
        "allowed_domains": ["fred.stlouisfed.org"],
        "max_results": 1,
        "max_cost_usd": 0.05,
        "research_session_id": "fred-time-session",
        "round": 1,
        "mode": "live",
        "observed_at": NOW,
        "cutoff_at": cutoff,
    })


@pytest.mark.asyncio
async def test_current_csv_is_current_background_not_a_dated_publication() -> None:
    async def fetch(_url: str) -> str:
        return "observation_date,DGS2\n2026-08-27,4.12\n2026-08-28,4.20\n2026-08-30,9.99\n"

    result = await FredSeriesResearchAdapter(fetcher=fetch, clock=lambda: NOW).execute(query())
    evidence, fact = result.evidence_candidates[0], result.facts[0]
    assert json.loads(evidence.excerpt)["observation_date"] == "2026-08-28"
    assert fact.value == "4.20"
    assert evidence.published_at is None and fact.published_at is None
    assert evidence.observed_at == evidence.received_at == result.completed_at == NOW
    assert fact.attributes["observation_time_precision"] == "date"
    assert fact.attributes["public_time_precision"] == "unknown"
    assert fact.attributes["provider_known_at"] == NOW.isoformat()
    assert fact.attributes["vintage_ref"] is None
    assert fact.attributes["minute_forward_eligible"] is False
    assert "publication_time_unavailable" in fact.attributes["time_exclusion_reasons"]
    assert "kernel_known_at_unavailable" in fact.attributes["time_exclusion_reasons"]
    assert fact.payload_schema_ref == "fred.series-observation.v2"


@pytest.mark.asyncio
async def test_revision_has_a_new_value_hash_and_receipt_without_backdating_original() -> None:
    bodies = iter(["DATE,DGS2\n2026-08-28,4.20\n", "DATE,DGS2\n2026-08-28,4.23\n"])
    clocks = iter([NOW, NOW + timedelta(minutes=1)])

    async def fetch(_url: str) -> str:
        return next(bodies)

    adapter = FredSeriesResearchAdapter(fetcher=fetch, clock=lambda: next(clocks))
    original = await adapter.execute(query(NOW + timedelta(minutes=2)))
    original_bytes = original.model_dump_json()
    revision = await adapter.execute(query(NOW + timedelta(minutes=2)))
    assert original.model_dump_json() == original_bytes
    old, new = original.facts[0], revision.facts[0]
    assert old.attributes["observation_date"] == new.attributes["observation_date"]
    assert old.payload_hash != new.payload_hash and old.fact_id != new.fact_id
    assert (
        original.evidence_candidates[0].content_hash != revision.evidence_candidates[0].content_hash
    )
    assert old.value == "4.20" and new.value == "4.23"
    assert old.published_at is None and new.published_at is None
    assert new.attributes["revision_at"] is None  # Current CSV supplies no revision clock.
    assert new.attributes["provider_known_at"] == (NOW + timedelta(minutes=1)).isoformat()
    assert new.attributes["minute_forward_eligible"] is False


def exact_times(**changes):
    # Explicit synthetic source originals, not evidence of FRED API vintage availability.
    body = {
        "observation_date": "2026-08-28",
        "received_at": NOW - timedelta(seconds=2),
        "known_at": NOW - timedelta(seconds=1),
        "cutoff_at": NOW,
        "published_at": NOW - timedelta(minutes=1),
        "public_time_precision": "microsecond",
        "publication_ref": "fixture://public-time-original",
        "version_ref": "fixture://exact-vintage-original",
    }
    return qualify_fred_time(**(body | changes))


def test_real_known_clock_is_independent_of_publication_and_preserves_one_microsecond() -> None:
    legal = exact_times(known_at=NOW)
    future = exact_times(known_at=NOW + timedelta(microseconds=1))
    assert legal.minute_forward_eligible
    assert future.historical_public_pit_eligible  # Does not substitute for system availability.
    assert not future.system_pit_eligible and not future.minute_forward_eligible
    assert future.known_at == NOW + timedelta(microseconds=1)
    assert "kernel_known_after_cutoff" in future.reasons
    missing = exact_times(known_at=None)
    assert not missing.minute_forward_eligible
    assert "kernel_known_at_unavailable" in missing.reasons


def test_revision_public_time_not_observation_period_controls_public_pit() -> None:
    before = exact_times(revision_at=NOW - timedelta(seconds=3))
    later = exact_times(
        received_at=NOW + timedelta(seconds=2),
        known_at=NOW + timedelta(seconds=3),
        revision_at=NOW + timedelta(microseconds=1),
    )
    assert before.historical_public_pit_eligible
    assert later.public_available_upper_bound == NOW + timedelta(microseconds=1)
    assert not later.historical_public_pit_eligible and not later.minute_forward_eligible
    assert "public_version_after_cutoff" in later.reasons


def test_day_precision_uses_source_day_upper_bound_and_never_minute_qualification() -> None:
    received = datetime(2026, 8, 28, 10, tzinfo=UTC)
    body = {
        "observation_date": "2026-08-27", "received_at": received, "known_at": received,
        "public_time_precision": "date", "public_date": date(2026, 8, 28),
        "public_day_timezone": UTC,
        "publication_ref": "fixture://dated-public-original", "version_ref": "fixture://vintage",
    }
    same_day = qualify_fred_time(**body, cutoff_at=received + timedelta(hours=2))
    bound = datetime(2026, 8, 29, tzinfo=UTC)
    before_bound = qualify_fred_time(**body, cutoff_at=bound - timedelta(microseconds=1))
    at_bound = qualify_fred_time(**body, cutoff_at=bound)
    assert same_day.public_available_upper_bound == bound
    assert not same_day.historical_public_pit_eligible
    assert not before_bound.historical_public_pit_eligible
    assert at_bound.historical_public_pit_eligible
    assert not at_bound.minute_forward_eligible
    assert "day_precision_not_minute_qualified" in at_bound.reasons
    no_zone = qualify_fred_time(**(body | {"public_day_timezone": None}), cutoff_at=bound)
    assert no_zone.public_available_upper_bound is None and not no_zone.minute_forward_eligible
    assert "public_day_timezone_unavailable" in no_zone.reasons


def test_day_bound_is_not_assumed_to_be_utc_midnight() -> None:
    received = datetime(2026, 8, 28, 10, tzinfo=UTC)
    result = qualify_fred_time(
        observation_date="2026-08-27", received_at=received, known_at=received,
        cutoff_at=datetime(2026, 8, 29, 0, tzinfo=UTC),
        public_time_precision="date", public_date=date(2026, 8, 28),
        public_day_timezone=timezone(timedelta(hours=-4)),
        publication_ref="fixture://explicit-source-day", version_ref="fixture://vintage",
    )
    assert result.public_available_upper_bound == datetime(2026, 8, 29, 4, tzinfo=UTC)
    assert not result.historical_public_pit_eligible


def test_public_clock_without_exact_originals_cannot_qualify() -> None:
    assert not exact_times(publication_ref=None).minute_forward_eligible
    assert not exact_times(version_ref=None).minute_forward_eligible
    assert not exact_times(public_time_precision="unknown").minute_forward_eligible
    assert not exact_times(known_at=NOW - timedelta(seconds=3)).minute_forward_eligible
    assert "kernel_known_before_receipt" in exact_times(known_at=NOW - timedelta(seconds=3)).reasons


def test_aware_offset_is_converted_not_relabelled_and_naive_clocks_are_rejected() -> None:
    shifted = NOW.astimezone(timezone(timedelta(hours=8)))
    result = exact_times(known_at=shifted)
    assert result.known_at == NOW and result.minute_forward_eligible
    with pytest.raises(ValueError, match="timezone_required"):
        exact_times(known_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone_required"):
        exact_times(published_at=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("text", ["2026-08-28T12:00:00Z", "2026-08-28+08:00", "2026-02-30"])
def test_observation_period_parser_never_truncates_a_timestamp_to_a_date(text: str) -> None:
    with pytest.raises(ValueError):
        fred_observation_date(text)


@pytest.mark.asyncio
async def test_provider_naive_receipt_clock_cannot_create_fact_time() -> None:
    async def fetch(_url: str) -> str:
        return "DATE,DGS2\n2026-08-28,4.20\n"

    with pytest.raises(ResearchCapabilityError) as raised:
        await FredSeriesResearchAdapter(
            fetcher=fetch, clock=lambda: NOW.replace(tzinfo=None)
        ).execute(query())
    assert raised.value.error_code == "research_macro_clock_invalid"
