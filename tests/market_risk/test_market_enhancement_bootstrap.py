"""Private SQLite + synthetic CSV controls; never claim real FRED/model/F5 qualification."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.market_enhancement_routes import market_enhancement_router
from apps.hub_worker.market_enhancement_bootstrap import MarketEnhancementTick, _authenticated_fred
from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    FactVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketExtensionRegistrationRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.macro_market.fred import FredSeriesResearchAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive

NOW = datetime(2026, 10, 8, 4, tzinfo=UTC)
BTC = spot_scope("BTC-USDT").scope_id
ETH = spot_scope("ETH-USDT").scope_id


@pytest.fixture
def setup(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'enhancements.sqlite3'}")
    database.create_all()
    clock = [NOW]
    tick = MarketEnhancementTick(database, tmp_path, clock=lambda: clock[0])
    app = FastAPI()
    app.include_router(market_enhancement_router(database, clock=lambda: clock[0]))
    yield database, tick, TestClient(app), tmp_path, clock
    database.engine.dispose()


def count(database, model):
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(model))


async def accepted_csv(database, tmp_path, monkeypatch):
    received, accepted = NOW - timedelta(minutes=2), NOW - timedelta(minutes=1)
    raw = b"observation_date,DGS2\n2026-10-06,4.20\n"

    async def fetch(_url):
        return raw.decode()

    query = ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "synthetic-fred",
            "capability_id": "market.cross_asset",
            "requirement_id": "cross_asset.rates",
            "query": "synthetic fixture current macro",
            "target_url": None,
            "symbols": ["DGS2"],
            "fields": ["level"],
            "allowed_domains": ["fred.stlouisfed.org"],
            "max_results": 1,
            "max_cost_usd": 0.05,
            "research_session_id": "synthetic-fred-session",
            "round": 1,
            "mode": "live",
            "observed_at": received,
            "cutoff_at": NOW,
        }
    )
    result = await FredSeriesResearchAdapter(fetcher=fetch, clock=lambda: received).execute(query)
    evidence = result.evidence_candidates[0]
    body_hash, body_ref = RawBodyArchive(tmp_path / "raw-captures").put(raw)
    capture = dict(
        capture_id="synthetic-capture",
        context_id="synthetic-fred",
        url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2",
        status_code=200,
        body_hash=body_hash,
        body_ref=body_ref,
        byte_count=len(raw),
        media_type="text/csv",
        adapter_version="synthetic-only",
        error_code=None,
        started_at=received,
        completed_at=received,
        recorded_at=received,
    )
    with database.session() as session:
        session.add(
            StudyRecord(
                study_id="synthetic-study",
                scope_key="synthetic-study",
                title="synthetic",
                market="crypto",
                instrument="BTC-USDT-SWAP",
                horizon_scope="30m,24h,72h",
                question_key="synthetic",
                identity_basis="explicit",
                status="active",
                created_at=received,
                updated_at=received,
            )
        )
        session.add(
            RunRecord(
                run_id="synthetic-run",
                event_id="synthetic-event",
                study_id="synthetic-study",
                status="running",
                strategy_version="synthetic",
                runtime_version="synthetic",
                created_at=received,
                updated_at=received,
            )
        )
        session.add(
            ResearchEvidenceRecord(
                **evidence.model_dump(exclude={"freshness_status", "quality", "source_url"}),
                source_url=str(evidence.source_url),
                run_id="synthetic-run",
                capability_id="market.cross_asset",
                quality="accepted",
                freshness_status="fresh",
                accepted_at=accepted,
            )
        )
        session.add(RawCaptureRecord(**capture))
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_lineage.utcnow", lambda: accepted
    )
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: accepted
    )
    with database.session() as session:
        FactLineageStore(database).bind_parsed(
            session,
            evidence_ids=[evidence.evidence_id],
            captures=[capture],
            parser_version="synthetic-fred-control",
        )
    ResearchFactStore(database).accept_facts(
        run_id="synthetic-run",
        capability_id="market.cross_asset",
        facts=result.facts,
        cutoff_at=NOW,
    )
    return accepted, body_ref


def test_readonly_startup_and_restart_preserve_six_original_candidates(setup):
    database, tick, client, tmp_path, _ = setup
    before = client.get(f"/v1/risk/scopes/{BTC}/enhancements")
    assert before.status_code == 200
    assert {x["data_status"] for x in before.json()["routes"]} == {"not_registered"}
    assert count(database, MarketExtensionRegistrationRecord) == 0
    assert tick.start() == 6 and tick.start() == 0
    assert {
        x["data_status"] for x in client.get(f"/v1/risk/scopes/{BTC}/enhancements").json()["routes"]
    } == {"awaiting_data"}
    assert tick.process() == 6
    before_rows = count(database, RunRoleOutputRecord)
    assert tick.process() == 0
    restarted = MarketEnhancementTick(database, tmp_path, clock=lambda: NOW)
    assert restarted.process() == 0
    assert count(database, MarketExtensionRegistrationRecord) == 6
    for scope in (BTC, ETH):
        value = client.get(f"/v1/risk/scopes/{scope}/enhancements").json()
        assert len(value["routes"]) == 3
        assert all(not x["promotion"] and not x["probability_qualified"] for x in value["routes"])
        assert all(x["admission_state"] == "not_admitted" for x in value["routes"])
        original = client.get("/v1/risk/enhancements/receipts/" + value["routes"][0]["receipt_id"])
        assert original.status_code == 200
    assert count(database, RunRoleOutputRecord) == before_rows
    assert client.get("/v1/risk/scopes/unknown/enhancements").status_code == 404


@pytest.mark.asyncio
async def test_native_accepted_fred_preserves_first_known_and_does_not_create_vintage(
    setup, monkeypatch
):
    database, tick, client, tmp_path, clock = setup
    assert tick.process() == 6
    accepted, _ = await accepted_csv(database, tmp_path, monkeypatch)
    clock[0] += timedelta(seconds=1)
    with database.session() as session:
        version = session.scalar(select(FactVersionRecord))
        _authenticated_fred(session, version, tick.archive, NOW)
    assert tick.process() == 2  # Unchanged crowding/event receipts are retained.
    for scope in (BTC, ETH):
        macro = client.get(f"/v1/risk/scopes/{scope}/enhancements").json()["routes"][0]
        assert macro["observation_date"] == "2026-10-06"
        assert datetime.fromisoformat(macro["source_known_at"].replace("Z", "+00:00")) == accepted
        assert macro["data_pit_verified"] is False and macro["probability"] is None
        assert macro["admission_state"] == "not_admitted"
        assert [f["value"] for f in macro["features"] if f["name"].endswith(":level")] == [4.2]
        saved = client.get("/v1/risk/enhancements/receipts/" + macro["receipt_id"]).json()
        assert saved["fred_bindings"][0]["source_instrument"] == "BTC-USDT-SWAP"
        assert saved["result"]["missing_reasons"]
    assert tick.process() == 0
    with database.session() as session:
        version = session.scalar(select(FactVersionRecord))
        assert as_utc(version.known_at) == accepted


@pytest.mark.asyncio
async def test_raw_damage_is_excluded_and_malformed_saved_original_is_409(setup, monkeypatch):
    database, tick, client, tmp_path, _ = setup
    _, body_ref = await accepted_csv(database, tmp_path, monkeypatch)
    original_read = tick.archive.read
    monkeypatch.setattr(
        tick.archive, "read", lambda ref: b"wrong bytes" if ref == body_ref else original_read(ref)
    )
    assert tick.process() == 6
    value = client.get(f"/v1/risk/scopes/{BTC}/enhancements").json()["routes"][0]
    assert value["excluded_source_count"] == 1 and value["original_count"] == 0
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, value["receipt_id"])
        row.payload_json = "{bad json"
    assert client.get(f"/v1/risk/scopes/{BTC}/enhancements").status_code == 409
    assert client.get("/v1/risk/enhancements/receipts/" + value["receipt_id"]).status_code == 409
    assert body_ref.startswith("raw://sha256/")


@pytest.mark.asyncio
async def test_cancel_waits_for_bounded_thread_completion(setup, monkeypatch):
    _, tick, _, _, _ = setup
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    def work():
        entered.set()
        assert release.wait(2)
        completed.set()
        return 0

    monkeypatch.setattr(tick, "process", work)
    task = asyncio.create_task(tick.tick())
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()
