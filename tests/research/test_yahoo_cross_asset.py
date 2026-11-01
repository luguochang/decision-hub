from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    ResearchFactStore,
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.provider_adapters.macro_market import YahooCrossAssetResearchAdapter
from packages.provider_adapters.macro_market import yahoo as yahoo_module
from packages.provider_adapters.research import CryptoMacroFactPack

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)


def _saved_vix_payload() -> dict[str, Any]:
    """Bounded transcription of the second saved 2026-10-02 chart, not GSPC."""
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": "^VIX",
                        "dataGranularity": "1d",
                        "regularMarketTime": 1790956951,
                        "regularMarketPrice": 15.54,
                        "currentTradingPeriod": {
                            "regular": {"start": 1790924400, "end": 1790972100},
                        },
                    },
                    "timestamp": [
                        1790146800, 1790233200, 1790319600, 1790578800,
                        1790665200, 1790751600, 1790838000, 1790924400,
                    ],
                    "indicators": {"quote": [{"close": [
                        15.180000305175781, 15.670000076293945, 14.869999885559082,
                        16.06999969482422, 16.040000915527344, 16.34000015258789,
                        16.389999389648438, 15.539999961853027,
                    ]}]},
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_saved_vix_open_daily_bar_is_not_a_completed_daily_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received = datetime(2026, 10, 2, 16, 17, 35, 985723, tzinfo=UTC)

    class ReceivedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return received

    monkeypatch.setattr(yahoo_module, "datetime", ReceivedDatetime)

    async def fetch(url: str) -> Any:
        assert "%5EVIX" in url
        return _saved_vix_payload()

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch).execute(
        _query("cross_asset_confirmation", ["VIX"], ["level", "return_5d"]).model_copy(
            update={"observed_at": received, "cutoff_at": received}
        )
    )
    level = next(fact for fact in result.facts or [] if fact.field == "level")
    assert level.value == "16.389999389648438"
    assert level.published_at is None
    assert result.evidence_candidates[0].published_at is None
    assert level.observed_at == received
    assert level.received_at == received


def _completion_pair(
    *,
    start: datetime,
    completion: datetime,
    received: datetime,
    basis: str = "provider_current_regular_end",
) -> tuple[EvidenceCandidate, FactEnvelope]:
    coordinates = {
        "completion_contract": "yahoo-provider-declared-daily.v2",
        "daily_bar_start_at": start.isoformat(),
        "daily_completion_not_after_at": completion.isoformat(),
        "daily_completion_basis": basis,
        "daily_snapshot_requested_at": received.isoformat(),
    }
    excerpt = json.dumps(
        {"provider": "yahoo-finance-public", "instrument": "VIX", **coordinates},
        sort_keys=True,
        separators=(",", ":"),
    )
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
    content_hash = research_evidence_content_hash(
        requirement_id="cross_asset_confirmation",
        kind="market",
        authority="audited_aggregator",
        source_id="yahoo-finance-public",
        source_url=url,
        published_at=None,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    candidate = EvidenceCandidate(
        evidence_id=research_evidence_instance_id(
            content_hash=content_hash, research_session_id="daily-proof-test"
        ),
        requirement_id="cross_asset_confirmation",
        kind="market",
        authority="audited_aggregator",
        source_id="yahoo-finance-public",
        source_url=AnyUrl(url),
        published_at=None,
        observed_at=received,
        received_at=received,
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id="daily-proof-call",
        research_session_id="daily-proof-test",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    attributes = {
        "provider_symbol": "^VIX",
        "window_sessions": 5.0,
        "completed_daily_only": True,
        "authority_limit": "audited_aggregator_not_official_release",
        **coordinates,
    }
    semantics = {
        "requirement_id": candidate.requirement_id,
        "metric_family": "cross_asset.volatility",
        "field": "level",
        "instrument": "VIX",
        "venue": None,
        "value": "16.389999389648438",
        "unit": "index",
        "window_start_at": None,
        "window_end_at": None,
        "event_offset": None,
        "published_at": None,
        "source_id": candidate.source_id,
        "independence_group": "yahoo-market:VIX",
        "delay_class": "delayed",
        "payload_schema_ref": "yahoo.chart.daily.v8",
        "attributes": attributes,
    }
    fact = FactEnvelope.model_validate({
        "schema_version": "fact-envelope.v1",
        "fact_id": "unbound-fixture-fact",
        "evidence_id": candidate.evidence_id,
        "observed_at": received,
        "received_at": received,
        "quality": "candidate",
        "payload_hash": "0" * 64,
        **semantics,
    })
    return candidate, _rehash_fact(fact)


def _rehash_fact(fact: FactEnvelope) -> FactEnvelope:
    payload_hash = research_fact_payload_hash(
        requirement_id=fact.requirement_id, metric_family=fact.metric_family,
        field=fact.field, instrument=fact.instrument, venue=fact.venue,
        value=fact.value, unit=fact.unit, window_start_at=fact.window_start_at,
        window_end_at=fact.window_end_at, event_offset=fact.event_offset,
        published_at=fact.published_at, source_id=fact.source_id,
        independence_group=fact.independence_group, delay_class=fact.delay_class,
        payload_schema_ref=fact.payload_schema_ref, attributes=fact.attributes,
    )
    return fact.model_copy(update={
        "payload_hash": payload_hash,
        "fact_id": research_fact_instance_id(
            evidence_id=fact.evidence_id, payload_hash=payload_hash
        ),
    })


def _admit_daily(
    tmp_path: Path,
    candidate: EvidenceCandidate,
    facts: Iterable[FactEnvelope],
    *,
    cutoff: datetime,
    freshness_seconds: int = 259200,
    required_baseline: bool = False,
) -> EvidenceCandidate:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'daily.sqlite3'}")
    database.create_all()
    try:
        with database.session() as session:
            session.add(RunRecord(
                run_id="run-daily",
                event_id="event-daily",
                status="running",
                strategy_version="research.v1",
                runtime_version="dsh.v1",
                created_at=cutoff,
                updated_at=cutoff,
            ))
        requirement = EvidenceRequirement(
            requirement_id="cross_asset_confirmation",
            description="Completed daily equity and volatility context.",
            importance="soft",
            source_priority=["audited_aggregator"],
            authority_floor="audited_aggregator",
            preferred_capabilities=["market.cross_asset"],
            freshness_seconds=freshness_seconds,
            minimum_independent_sources=1,
            allowed_fallbacks=[],
            confidence_cap=0.65,
            required_event_offsets=["baseline"] if required_baseline else [],
        )
        return ResearchEvidenceService(database).accept_candidates(
            run_id="run-daily",
            capability_id="market.cross_asset",
            candidates=[candidate],
            requirements={requirement.requirement_id: requirement},
            cutoff_at=cutoff,
            facts=facts,
        )[0]
    finally:
        database.engine.dispose()


def test_daily_unknown_publication_does_not_refresh_old_completion(tmp_path: Path) -> None:
    received = datetime(2026, 10, 2, 16, 17, 35, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(days=5, hours=6),
        completion=received - timedelta(days=5),
        received=received,
    )
    admitted = _admit_daily(tmp_path, candidate, [fact], cutoff=received)
    assert admitted.freshness_status == "stale"
    assert admitted.quality == "stale"


@pytest.mark.parametrize(
    ("received", "cutoff", "expected"),
    [
        ("2026-10-02T20:14:59+00:00", "2026-10-02T23:00:00+00:00", "16.389999389648438"),
        ("2026-10-02T20:15:00+00:00", "2026-10-02T20:15:00+00:00", "15.539999961853027"),
        ("2026-10-02T20:16:00+00:00", "2026-10-02T23:00:00+00:00", "15.539999961853027"),
        ("2026-10-02T23:00:00+00:00", "2026-10-02T20:14:59+00:00", "16.389999389648438"),
    ],
)
@pytest.mark.asyncio
async def test_daily_completion_uses_declared_end_and_both_clock_bounds(
    received: str, cutoff: str, expected: str,
) -> None:
    instant = datetime.fromisoformat(received)

    async def fetch(url: str) -> Any:
        return _saved_vix_payload()

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch, clock=lambda: instant).execute(
        _query("cross_asset_confirmation", ["VIX"], ["level"]).model_copy(
            update={"cutoff_at": datetime.fromisoformat(cutoff)}
        )
    )
    fact = (result.facts or [])[0]
    assert fact.value == expected
    assert fact.published_at is None
    assert fact.attributes["daily_snapshot_requested_at"] == instant.isoformat()
    assert datetime.fromisoformat(str(fact.attributes["daily_completion_not_after_at"])) <= min(
        instant, datetime.fromisoformat(cutoff)
    )


@pytest.mark.parametrize("two_responses", [False, True])
@pytest.mark.asyncio
async def test_response_or_sequential_fetch_crossing_close_keeps_first_bar_uncompleted(
    two_responses: bool,
) -> None:
    before_close = datetime(2026, 10, 2, 20, 14, 59, tzinfo=UTC)
    after_close = before_close + timedelta(seconds=2)
    clock_at = before_close
    calls = 0

    async def fetch(url: str) -> Any:
        nonlocal clock_at, calls
        calls += 1
        payload = _saved_vix_payload()
        if "%5EGSPC" in url:
            payload["chart"]["result"][0]["meta"]["symbol"] = "^GSPC"
        clock_at = after_close
        return payload

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch, clock=lambda: clock_at).execute(
        _query("cross_asset_confirmation", ["VIX", "SPX"] if two_responses else ["VIX"],
               ["level"]).model_copy(update={"cutoff_at": after_close})
    )
    vix = next(fact for fact in result.facts or [] if fact.instrument == "VIX")
    assert vix.value == "16.389999389648438"
    assert vix.attributes["daily_snapshot_requested_at"] == before_close.isoformat()
    assert vix.received_at == after_close
    assert calls == (2 if two_responses else 1)
    if two_responses:
        spx = next(fact for fact in result.facts or [] if fact.instrument == "SPX")
        assert spx.value == "15.539999961853027"
        assert spx.attributes["daily_snapshot_requested_at"] == after_close.isoformat()


@pytest.mark.parametrize("bad_end", [None, "1790972100", True, 1790924400])
@pytest.mark.asyncio
async def test_unknown_current_completion_uses_only_proven_prior_bars(bad_end: object) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)

    async def fetch(url: str) -> Any:
        payload = _saved_vix_payload()
        payload["chart"]["result"][0]["meta"]["currentTradingPeriod"]["regular"]["end"] = bad_end
        return payload

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch, clock=lambda: received).execute(
        _query("cross_asset_confirmation", ["VIX"], ["level"]).model_copy(
            update={"cutoff_at": received}
        )
    )
    assert (result.facts or [])[0].value == "16.389999389648438"
    assert (result.facts or [])[0].attributes["daily_completion_basis"] == "next_daily_bar_start"


@pytest.mark.parametrize("invalid", ["symbol", "granularity", "order", "length", "nan", "too_few"])
@pytest.mark.asyncio
async def test_invalid_daily_shape_or_insufficient_completion_refuses(invalid: str) -> None:
    received = datetime(2026, 10, 2, 16, 17, 35, tzinfo=UTC)

    async def fetch(url: str) -> Any:
        payload = _saved_vix_payload()
        result = payload["chart"]["result"][0]
        if invalid == "symbol":
            result["meta"]["symbol"] = "^GSPC"
        elif invalid == "granularity":
            result["meta"]["dataGranularity"] = "1m"
        elif invalid == "order":
            result["timestamp"][1] = result["timestamp"][0]
        elif invalid == "length":
            result["timestamp"].pop()
        elif invalid == "nan":
            result["indicators"]["quote"][0]["close"][0] = float("nan")
        else:
            result["timestamp"] = result["timestamp"][-6:]
            quote = result["indicators"]["quote"][0]
            quote["close"] = quote["close"][-6:]
        return payload

    with pytest.raises(ResearchCapabilityError) as raised:
        await YahooCrossAssetResearchAdapter(fetcher=fetch, clock=lambda: received).execute(
            _query("cross_asset_confirmation", ["VIX"], ["level"]).model_copy(
                update={"cutoff_at": received}
            )
        )
    assert raised.value.error_code == "research_market_output_invalid"


@pytest.mark.parametrize(
    ("basis", "start_age", "completion_age", "expected"),
    [
        ("provider_current_regular_end", 4 * 86400, 2 * 86400, "fresh"),
        ("provider_current_regular_end", 5 * 86400, 4 * 86400, "stale"),
        ("provider_current_regular_end", 100, 0, "fresh"),
        ("next_daily_bar_start", 2 * 86400, 86400, "fresh"),
        ("next_daily_bar_start", 5 * 86400, 4 * 86400, "stale"),
        ("next_daily_bar_start", 5 * 86400, 86400, "unknown"),
    ],
)
def test_kernel_completion_age_interval_preserves_threshold(
    tmp_path: Path, basis: str, start_age: int, completion_age: int, expected: str,
) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(seconds=start_age),
        completion=received - timedelta(seconds=completion_age), received=received, basis=basis,
    )
    admitted = _admit_daily(tmp_path, candidate, [fact], cutoff=received)
    assert admitted.freshness_status == expected
    assert admitted.quality == ("accepted" if expected == "fresh" else
                                "candidate" if expected == "unknown" else "stale")


@pytest.mark.parametrize("conflict", [
    "evidence_id", "requirement", "source", "payload_hash", "fact_id", "coordinates",
    "publication", "missing_fact", "candidate_hash", "basis", "timezone",
])
def test_v2_completion_identity_conflicts_cannot_be_admitted(tmp_path: Path, conflict: str) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(hours=24), completion=received - timedelta(hours=8),
        received=received,
    )
    if conflict == "evidence_id":
        fact = fact.model_copy(update={"evidence_id": "foreign-evidence"})
    elif conflict == "requirement":
        fact = fact.model_copy(update={"requirement_id": "foreign-requirement"})
    elif conflict == "source":
        fact = fact.model_copy(update={"source_id": "foreign-source"})
    elif conflict == "payload_hash":
        fact = fact.model_copy(update={"payload_hash": "0" * 64})
    elif conflict == "fact_id":
        fact = fact.model_copy(update={"fact_id": "foreign-fact"})
    elif conflict == "coordinates":
        fact = _rehash_fact(fact.model_copy(update={"attributes": {
            **fact.attributes, "daily_completion_not_after_at": received.isoformat(),
        }}))
    elif conflict == "publication":
        fact = fact.model_copy(update={"published_at": received})
    elif conflict == "candidate_hash":
        candidate = candidate.model_copy(update={"content_hash": "0" * 64})
    elif conflict in {"basis", "timezone"}:
        body = json.loads(candidate.excerpt)
        body["daily_completion_basis" if conflict == "basis" else "daily_bar_start_at"] = (
            "unverified_calendar" if conflict == "basis" else "2026-10-01T23:00:00"
        )
        candidate = candidate.model_copy(update={"excerpt": json.dumps(body)})
    with pytest.raises(ValueError, match="research_daily_completion"):
        _admit_daily(
            tmp_path, candidate, [] if conflict == "missing_fact" else [fact], cutoff=received
        )


@pytest.mark.parametrize("contract", [None, "yahoo-old.v1", "yahoo-unknown.v9"])
def test_legacy_or_unknown_daily_metadata_cannot_borrow_baseline_or_receive_freshness(
    tmp_path: Path, contract: str | None,
) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(hours=24), completion=received - timedelta(hours=8),
        received=received,
    )
    body = json.loads(candidate.excerpt)
    body["completion_contract"] = contract
    candidate = candidate.model_copy(update={"excerpt": json.dumps(body)})
    fact = fact.model_copy(update={"attributes": {
        **fact.attributes, "completion_contract": contract,
    }, "event_offset": "baseline"})
    admitted = _admit_daily(tmp_path, candidate, [fact], cutoff=received, required_baseline=True)
    assert admitted.freshness_status == "unknown"
    assert admitted.quality == "candidate"


def test_non_yahoo_market_receive_freshness_is_unchanged(tmp_path: Path) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, _ = _completion_pair(
        start=received - timedelta(days=5), completion=received - timedelta(days=4),
        received=received,
    )
    candidate = candidate.model_copy(update={"source_id": "ordinary-market-fixture"})
    admitted = _admit_daily(tmp_path, candidate, [], cutoff=received)
    assert admitted.freshness_status == "fresh"
    assert admitted.quality == "accepted"


def test_daily_fact_generator_is_consumed_once(tmp_path: Path) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(hours=24), completion=received - timedelta(hours=8),
        received=received,
    )
    admitted = _admit_daily(tmp_path, candidate, (item for item in [fact]), cutoff=received)
    assert admitted.quality == "accepted"


@pytest.mark.parametrize(
    ("age_days", "basis", "expected"),
    [(5, "provider_current_regular_end", "stale"),
     (2, "next_daily_bar_start", "candidate"),
     (1, "provider_current_regular_end", "accepted")],
)
def test_daily_fact_store_preserves_evidence_quality_without_false_credit(
    tmp_path: Path, age_days: int, basis: str, expected: str,
) -> None:
    received = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    candidate, fact = _completion_pair(
        start=received - timedelta(days=6),
        completion=received - timedelta(days=age_days),
        received=received,
        basis=basis,
    )
    admitted = _admit_daily(tmp_path, candidate, [fact], cutoff=received)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'daily.sqlite3'}")
    try:
        stored = ResearchFactStore(database).accept_facts(
            run_id="run-daily", capability_id="market.cross_asset", facts=[fact],
            cutoff_at=received,
        )
        assert admitted.quality == expected
        assert stored[0].quality == expected
        assert ResearchFactStore(database).list_run_facts("run-daily") == stored
        assert stored[0].published_at is None
        assert stored[0].attributes == fact.attributes
        requirement = EvidenceRequirement(
            requirement_id="cross_asset_confirmation", description="Daily volatility context",
            importance="soft", source_priority=["audited_aggregator"],
            authority_floor="audited_aggregator", preferred_capabilities=["market.cross_asset"],
            freshness_seconds=259200, minimum_independent_sources=1,
            allowed_fallbacks=[], confidence_cap=0.65,
        )
        coverage = assess_evidence_sufficiency(
            [requirement], [admitted], cutoff_at=received, facts=stored,
        )
        assert bool(coverage.covered_requirement_ids) == (expected == "accepted")
        (tmp_path / "fact-credit-observation.json").write_text(json.dumps({
            "evidence": admitted.model_dump(mode="json"),
            "stored_facts": [item.model_dump(mode="json") for item in stored],
            "coverage": coverage.model_dump(mode="json"),
        }, indent=2), encoding="utf-8")
    finally:
        database.engine.dispose()


def _payload(values: list[float], symbol: str = "^VIX") -> dict[str, Any]:
    start = NOW - timedelta(days=len(values))
    latest = start + timedelta(days=len(values) - 1)
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "dataGranularity": "1d",
                        "currentTradingPeriod": {"regular": {
                            "start": int(latest.timestamp()),
                            "end": int((latest + timedelta(hours=8)).timestamp()),
                        }},
                    },
                    "timestamp": [
                        int((start + timedelta(days=index)).timestamp())
                        for index in range(len(values))
                    ],
                    "indicators": {"quote": [{"close": values}]},
                }
            ],
        }
    }


def _query(requirement_id: str, symbols: list[str], fields: list[str]):
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": f"yahoo-{requirement_id}",
            "capability_id": "market.cross_asset",
            "requirement_id": requirement_id,
            "query": "completed daily market context",
            "target_url": None,
            "symbols": symbols,
            "fields": fields,
            "allowed_domains": ["query1.finance.yahoo.com"],
            "max_results": 10,
            "max_cost_usd": 0.0,
            "research_session_id": "yahoo-test",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=1),
        }
    )


@pytest.mark.asyncio
async def test_yahoo_adapter_emits_rates_and_usd_five_session_change() -> None:
    async def fetch(url: str) -> Any:
        if "%5ETNX" in url:
            return _payload([4.80, 4.82, 4.85, 4.86, 4.88, 4.90], "^TNX")
        return _payload([100.0, 100.2, 100.1, 100.4, 100.5, 101.0], "DX-Y.NYB")

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch).execute(
        _query("macro_transmission", ["US10Y", "DXY"], ["level", "change_5d"])
    )

    assert result.provider == "yahoo-finance-public"
    assert {item.authority for item in result.evidence_candidates} == {"audited_aggregator"}
    keyed = {(fact.instrument, fact.field): fact for fact in result.facts or []}
    assert keyed[("US10Y", "level")].unit == "yield_percent"
    assert float(keyed[("US10Y", "change_5d")].value) == pytest.approx(10.0)
    assert keyed[("US10Y", "change_5d")].unit == "bps"
    assert float(keyed[("DXY", "change_5d")].value) == pytest.approx(1.0)
    for fact in result.facts or []:
        assert fact.payload_hash == research_fact_payload_hash(
            requirement_id=fact.requirement_id,
            metric_family=fact.metric_family,
            field=fact.field,
            instrument=fact.instrument,
            venue=fact.venue,
            value=fact.value,
            unit=fact.unit,
            window_start_at=fact.window_start_at,
            window_end_at=fact.window_end_at,
            event_offset=fact.event_offset,
            published_at=fact.published_at,
            source_id=fact.source_id,
            independence_group=fact.independence_group,
            delay_class=fact.delay_class,
            payload_schema_ref=fact.payload_schema_ref,
            attributes=fact.attributes,
        )


@pytest.mark.asyncio
async def test_yahoo_adapter_emits_equity_and_volatility_returns() -> None:
    async def fetch(url: str) -> Any:
        if "%5EGSPC" in url:
            return _payload([7500, 7520, 7540, 7560, 7580, 7650], "^GSPC")
        return _payload([16.0, 15.8, 15.5, 15.2, 15.0, 14.8])

    result = await YahooCrossAssetResearchAdapter(fetcher=fetch).execute(
        _query("cross_asset_confirmation", ["SPX", "VIX"], ["level", "return_5d"])
    )

    keyed = {(fact.instrument, fact.field): fact for fact in result.facts or []}
    assert keyed[("SPX", "level")].metric_family == "cross_asset.equity"
    assert keyed[("VIX", "level")].metric_family == "cross_asset.volatility"
    assert float(keyed[("SPX", "return_5d")].value) == pytest.approx(2.0)
    assert float(keyed[("VIX", "return_5d")].value) == pytest.approx(-7.5)


def test_current_snapshot_projects_daily_macro_and_cross_asset_semantics() -> None:
    requirements = {
        item.requirement_id: item
        for item in CryptoMacroFactPack.from_pack(
            Path("packs/crypto_macro")
        ).contract_requirements_for_event("crypto_market_snapshot")
    }

    macro = requirements["macro_transmission"]
    assert macro.required_fields == ["level", "change_5d"]
    assert macro.required_event_offsets == []
    assert macro.minimum_independence_groups == 2
    assert "delayed" in macro.allowed_delay_classes

    cross = requirements["cross_asset_confirmation"]
    assert cross.required_metric_families == [
        "cross_asset.equity",
        "cross_asset.volatility",
    ]
    assert cross.required_fields == ["level", "return_5d"]
    assert cross.required_event_offsets == []


@pytest.mark.asyncio
async def test_typed_daily_results_cover_snapshot_macro_and_cross_asset_requirements() -> None:
    async def fetch(url: str) -> Any:
        if "%5ETNX" in url:
            return _payload([4.80, 4.82, 4.85, 4.86, 4.88, 4.90], "^TNX")
        if "DX-Y.NYB" in url:
            return _payload([100.0, 100.2, 100.1, 100.4, 100.5, 101.0], "DX-Y.NYB")
        if "%5EGSPC" in url:
            return _payload([7500, 7520, 7540, 7560, 7580, 7650], "^GSPC")
        return _payload([16.0, 15.8, 15.5, 15.2, 15.0, 14.8])

    adapter = YahooCrossAssetResearchAdapter(fetcher=fetch)
    macro_result = await adapter.execute(
        _query("macro_transmission", ["US10Y", "DXY"], ["level", "change_5d"])
    )
    cross_result = await adapter.execute(
        _query("cross_asset_confirmation", ["SPX", "VIX"], ["level", "return_5d"])
    )
    requirements = [
        item
        for item in CryptoMacroFactPack.from_pack(
            Path("packs/crypto_macro")
        ).contract_requirements_for_event("crypto_market_snapshot")
        if item.requirement_id in {"macro_transmission", "cross_asset_confirmation"}
    ]
    evidence = [
        item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
        for item in [*macro_result.evidence_candidates, *cross_result.evidence_candidates]
    ]
    facts = [
        item.model_copy(update={"quality": "accepted"})
        for item in [*(macro_result.facts or []), *(cross_result.facts or [])]
    ]

    coverage = assess_evidence_sufficiency(
        requirements,
        evidence,
        facts=facts,
        cutoff_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert coverage.gaps == []
    assert set(coverage.covered_requirement_ids) == {
        "macro_transmission",
        "cross_asset_confirmation",
    }
