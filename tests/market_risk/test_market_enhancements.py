"""Fixed features and explicit missing gates, never synthetic model promotion."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from packages.kernel.decision_hub_kernel.application.market_enhancements import (
    ArchivedEnhancementInputs,
    CrowdingInput,
    CrowdingPair,
    CrowdingPoint,
    EnhancementBinding,
    EnhancementOriginalReader,
    EventSurpriseInput,
    EventValue,
    MacroRelease,
    MacroStateInput,
    MarketEnhancements,
    SourcePoint,
    component_feature_callback,
    crowding,
    event_surprise,
    macro_release_from_fact,
    macro_state,
)
from packages.kernel.decision_hub_kernel.application.market_extension_registry import (
    MarketExtensionRegistry,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketExtensionRegistrationRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.provider_adapters.research.raw_capture import RawBodyArchive

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def source(identity, at, *, public=True):
    return SourcePoint(
        original_ref="fixture://" + identity,
        original_hash=canonical_hash({"fixture": identity}),
        source_id="fixture",
        observed_at=at,
        received_at=at,
        known_at=at,
        public_at=at if public else None,
        public_precision="instant" if public else "unknown",
    )


def values(result):
    return {f.name: f.value for f in result.features}


def test_macro_period_changes_revisions_future_known_and_date_precision():
    rows = tuple(
        MacroRelease(
            series_id="USD",
            observation_date=(NOW - timedelta(days=8 - i)).date(),
            value=i,
            unit="index",
            vintage_at=NOW - timedelta(days=7 - i),
            source=source(str(i), NOW - timedelta(days=7 - i)),
        )
        for i in range(6)
    )
    revision = rows[-1].model_copy(
        update={
            "value": 99,
            "vintage_at": NOW + timedelta(seconds=1),
            "source": source("future-revision", NOW + timedelta(seconds=1)),
        }
    )
    data = MacroStateInput(instrument="BTC-USDT", releases=rows + (revision,))
    actual = macro_state(data, cutoff_at=NOW)
    assert values(actual) == {
        "USD:fixture:level": 5,
        "USD:fixture:change_1_release": 1,
        "USD:fixture:change_5_release": 5,
    }
    assert actual.data_pit_verified and revision.source.original_ref not in actual.original_refs
    assert not actual.probability_qualified and actual.probability is None
    unknown = MacroStateInput(
        instrument="ETH-USDT",
        releases=tuple(
            row.model_copy(
                update={
                    "vintage_at": None,
                    "source": source(str(i), row.source.known_at, public=False),
                }
            )
            for i, row in enumerate(rows)
        ),
    )
    descriptive = macro_state(unknown, cutoff_at=NOW)
    assert values(descriptive)["USD:fixture:level"] == 5 and not descriptive.data_pit_verified
    assert (
        "macro_vintage_or_exact_public_time_unknown_descriptive_only" in descriptive.missing_reasons
    )


def pair(venue, *, instrument="BTC-USDT", contract="BTC-PERP", oi=120, funding=0.008, mark=101):
    current = CrowdingPoint(
        instrument=instrument,
        venue=venue,
        contract=contract,
        contract_kind="perpetual",
        open_interest=oi,
        open_interest_unit="contracts",
        funding_rate=funding,
        funding_period_seconds=28800,
        derivative_price=mark,
        spot_price=100,
        price_basis="mark",
        source=source(venue + ":current", NOW - timedelta(seconds=1)),
    )
    prior = current.model_copy(
        update={"open_interest": 100, "source": source(venue + ":prior", NOW - timedelta(hours=1))}
    )
    return CrowdingPair(current=current, prior=prior)


def test_crowding_same_contract_units_cycles_and_multi_venue_alignment():
    data = CrowdingInput(
        instrument="BTC-USDT", venues=(pair("one"), pair("two", funding=0.016, mark=102))
    )
    result = crowding(data, cutoff_at=NOW)
    actual = values(result)
    assert actual["one:BTC-PERP:mark:oi_change"] == pytest.approx(0.2)
    assert actual["one:BTC-PERP:mark:funding_per_hour"] == pytest.approx(0.001)
    assert actual["basis_dispersion"] == pytest.approx(0.01) and result.data_pit_verified
    changed_contract = pair("one").model_copy(update={"prior": pair("one", contract="OTHER").prior})
    unknown = crowding(
        CrowdingInput(instrument="BTC-USDT", venues=(changed_contract,)), cutoff_at=NOW
    )
    assert values(unknown)["one:BTC-PERP:mark:oi_change"] is None
    assert (
        values(unknown)["funding_dispersion"] is None
        and "crowding_multi_venue_or_alignment_unknown" in unknown.missing_reasons
    )
    with pytest.raises(ValueError, match="instrument_mismatch"):
        crowding(CrowdingInput(instrument="ETH-USDT", venues=data.venues), cutoff_at=NOW)


def test_event_consensus_is_prepublic_and_prior_is_not_a_replacement():
    release = NOW - timedelta(minutes=1)
    actual = EventValue(
        event_id="official-event",
        period="2026-09",
        unit="percent",
        value=3,
        source=source("actual", release),
    )
    consensus = actual.model_copy(
        update={"value": 2, "source": source("consensus", release - timedelta(hours=1))}
    )
    prior = actual.model_copy(
        update={"value": 1, "source": source("prior", release - timedelta(days=30))}
    )
    data = EventSurpriseInput(
        instrument="BTC-USDT", actual=actual, consensus=consensus, prior=prior
    )
    assert values(event_surprise(data, cutoff_at=NOW)) == {
        "actual_minus_consensus": 1,
        "actual_minus_prior": 2,
    }
    absent = event_surprise(data.model_copy(update={"consensus": None}), cutoff_at=NOW)
    assert (
        values(absent)["actual_minus_consensus"] is None
        and values(absent)["actual_minus_prior"] == 2
    )
    late = data.model_copy(
        update={"consensus": consensus.model_copy(update={"source": source("late", release)})}
    )
    assert values(event_surprise(late, cutoff_at=NOW))["actual_minus_consensus"] is None
    future = data.model_copy(
        update={
            "actual": actual.model_copy(
                update={"source": source("future", NOW + timedelta(microseconds=1))}
            )
        }
    )
    assert event_surprise(future, cutoff_at=NOW).status == "unavailable"


def test_existing_fred_payload_converter_preserves_unknown_publication_and_real_known():
    body = {
        "quality": "accepted",
        "source_id": "fred-public",
        "payload_schema_ref": "fred.series-observation.v2",
        "field": "level",
        "value": "4.2",
        "unit": "yield_percent",
        "observed_at": NOW.isoformat(),
        "received_at": NOW.isoformat(),
        "published_at": None,
        "attributes": {
            "series": "DGS10",
            "observation_date": "2026-10-07",
            "time_schema": "fred.observation-time.v2",
            "public_time_precision": "unknown",
            "revision_at": None,
        },
    }
    row = macro_release_from_fact(
        body, original_ref="fixture://fv-fred", original_hash=canonical_hash(body), known_at=NOW
    )
    result = macro_state(MacroStateInput(instrument="ETH-USDT", releases=(row,)), cutoff_at=NOW)
    assert values(result)["DGS10:fred-public:level"] == 4.2 and not result.data_pit_verified
    assert row.source.public_at is None and row.source.observed_at == NOW and row.vintage_at is None
    assert values(result)["DGS10:fred-public:change_1_release"] is None
    with pytest.raises(ValueError, match="original_invalid"):
        macro_release_from_fact(
            body, original_ref="fixture://fv-fred", original_hash="0" * 64, known_at=NOW
        )


def test_exact_archived_inputs_actual_three_registrations_and_closed_f5(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "bootstrap"))

    def private(_dialect, _record, args, _kwargs):
        assert (
            Path(str(args[0]).removeprefix("file:").split("?")[0])
            .resolve()
            .is_relative_to(tmp_path.resolve())
        )

    event.listen(Engine, "do_connect", private)
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'enhancements.sqlite3'}")
    db.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    data = MacroStateInput(
        instrument="BTC-USDT",
        releases=(
            MacroRelease(
                series_id="USD",
                observation_date=NOW.date(),
                value=100,
                unit="index",
                source=source("macro", NOW, public=False),
            ),
        ),
    )
    digest, ref = archive.put(data.model_dump_json().encode())
    binding = EnhancementBinding("MACRO_STATE_V1", "BTC-USDT", ref, digest, NOW)
    service = MarketEnhancements(ArchivedEnhancementInputs(archive))
    reader = EnhancementOriginalReader(service, (binding,))
    registry = MarketExtensionRegistry(db, original_reader=reader, clock=lambda: NOW)
    try:
        ids = service.register_candidates(registry, "BTC-USDT")
        assert len(ids) == 3 and service.register_candidates(registry, "BTC-USDT") == ids
        with db.session() as session:
            assert len(session.scalars(select(MarketExtensionRegistrationRecord)).all()) == 3
        admission = registry.assess(ids[0])
        assert admission.state == "not_admitted" and not admission.probability_calibrated
        assert "f5_independent_sample_qualification_missing" in admission.reasons
        result = service.evaluate(binding)
        assert (
            result.original_refs == (data.releases[0].source.original_ref,)
            and result.probability is None
        )
        output = component_feature_callback(
            {"input": data.model_dump(mode="json"), "cutoff_at": NOW.isoformat()},
            {"route": "MACRO_STATE_V1", "instrument": "BTC-USDT"},
        )
        assert output == result.model_dump(mode="json")
        with pytest.raises(ValueError, match="instrument_mismatch"):
            service.evaluate(EnhancementBinding("MACRO_STATE_V1", "ETH-USDT", ref, digest, NOW))
        with pytest.raises(ValueError, match="original_changed"):
            service.evaluate(EnhancementBinding("MACRO_STATE_V1", "BTC-USDT", ref, "0" * 64, NOW))
    finally:
        db.engine.dispose()
        event.remove(Engine, "do_connect", private)
