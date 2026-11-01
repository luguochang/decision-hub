"""Real adapter, archived pages, Fact admission and minimal HTTP app; no model."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.hub_api.fact_routes import fact_router
from packages.contracts_py.decision_hub_contracts import EvidenceRequirement
from packages.kernel.decision_hub_kernel.application.fact_store import (
    ResearchFactStore,
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.market.spot_research import CAPABILITY, SpotMarketFactsAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import NOW, PublicFixture
from tests.market_risk.test_spot_research import manifest, query


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: NOW
    )
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'fixture.sqlite3'}")
    db.create_all()
    clock = [NOW]
    archive = RawBodyArchive(tmp_path / "raw-captures")
    transport = PublicFixture(clock)
    transport.bump = Decimal(2400)
    data = MarketRiskDataService(
        db,
        archive,
        tmp_path / "features",
        adapter=OKXMarketPathAdapter(fetcher=transport.fetch),
        scopes=(spot_scope("ETH-USDT"),),
        enabled=True,
        clock=lambda: clock[0],
    )
    assert asyncio.run(data.refresh_due()) == 1
    with db.session() as session:
        session.add(
            RunRecord(
                run_id="run-spot",
                event_id="event-spot",
                status="running",
                strategy_version="research.v1",
                runtime_version="dsh.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    adapter = SpotMarketFactsAdapter(data, clock=lambda: clock[0])
    gateway = ResearchCapabilityGatewayService(
        [manifest()],
        [adapter],
        enabled_capabilities=[CAPABILITY],
        clock=lambda: clock[0],
    )

    def admit():
        result = asyncio.run(gateway.execute(query("ETH-USDT", requirement_id="spot_a")))
        req = EvidenceRequirement(
            requirement_id="spot_a",
            description="Exact spot originals",
            importance="hard",
            source_priority=["exchange"],
            authority_floor="exchange",
            preferred_capabilities=[CAPABILITY],
            freshness_seconds=3600,
            minimum_independent_sources=1,
            allowed_fallbacks=[],
            confidence_cap=0.5,
        )
        ResearchEvidenceService(db).accept_candidates(
            run_id="run-spot",
            capability_id=CAPABILITY,
            candidates=result.evidence_candidates,
            requirements={"spot_a": req},
            cutoff_at=clock[0],
            facts=result.facts,
        )
        ResearchFactStore(db).accept_facts(
            run_id="run-spot",
            capability_id=CAPABILITY,
            facts=result.facts,
            cutoff_at=clock[0],
        )
        return result

    app = FastAPI()
    app.include_router(fact_router(db, tmp_path))
    client = TestClient(app)
    yield db, archive, data, transport, clock, admit, client
    db.engine.dispose()


def endpoint(result):
    return (
        f"/v1/research/runs/run-spot/evidence/{result.evidence_candidates[0].evidence_id}/captures"
    )


def test_normal_api_returns_every_window_page_and_original_bytes(case):
    db, archive, _data, transport, _clock, admit, client = case
    result = admit()
    before = len(transport.calls)
    response = client.get(endpoint(result))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 5  # full 1,440-minute window, not latest page only
    assert len({item["body_hash"] for item in response.json()}) == 5
    for capture in response.json():
        body = client.get(f"/v1/raw-captures/{capture['capture_id']}/body")
        assert body.status_code == 200
        assert body.content == archive.read(capture["body_ref"])
        assert hashlib.sha256(body.content).hexdigest() == capture["body_hash"]
    assert len(transport.calls) == before
    assert client.get(endpoint(result).replace("run-spot", "other-run")).status_code == 404
    assert client.get(endpoint(result) + "unknown").status_code == 404


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("fact_run", "fact_binding_mismatch"),
        ("fact_hash", "fact_binding_mismatch"),
        ("latest_ref", "fact_binding_mismatch"),
        ("scope", "fact_binding_mismatch"),
        ("time", "fact_binding_mismatch"),
        ("evidence", "evidence_identity_mismatch"),
        ("missing_bar", "window_manifest_mismatch"),
        ("missing_fact", "fact_missing"),
        ("raw_hash", "original_invalid"),
        ("raw_url", "original_invalid"),
        ("raw_time", "original_invalid"),
        ("raw_length", "receipt_length_mismatch"),
    ],
)
def test_wrong_binding_or_missing_original_is_named_conflict(case, mutation, expected):
    db, _archive, _data, _transport, _clock, admit, client = case
    result = admit()
    with db.session() as session:
        fact = session.scalars(select(ResearchFactRecord)).first()
        raw = session.scalars(select(RawCaptureRecord)).first()
        if mutation == "fact_run":
            fact.run_id = "other-run"
        elif mutation == "fact_hash":
            fact.payload_hash = "0" * 64
        elif mutation in {"latest_ref", "scope"}:
            attrs = json.loads(fact.attributes_json)
            attrs["latest_capture_ref" if mutation == "latest_ref" else "scope_id"] = "forged"
            fact.attributes_json = json.dumps(attrs)
        elif mutation == "time":
            fact.received_at += timedelta(seconds=1)
        elif mutation == "evidence":
            session.get(ResearchEvidenceRecord, fact.evidence_id).excerpt += " "
        elif mutation == "missing_bar":
            session.delete(session.scalars(select(MarketCandleRecord).offset(600)).first())
        elif mutation == "missing_fact":
            for row in session.scalars(select(ResearchFactRecord)).all():
                session.delete(row)
        elif mutation == "raw_hash":
            raw.body_hash = "0" * 64
        elif mutation == "raw_url":
            raw.url = raw.url.replace("ETH-USDT", "BTC-USDT")
        elif mutation == "raw_time":
            raw.completed_at += timedelta(seconds=1)
        elif mutation == "raw_length":
            raw.byte_count += 1
    response = client.get(endpoint(result))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "research_spot_capture_" + expected


def test_rehashed_forged_latest_receipt_cannot_replace_window(case):
    db, _archive, _data, _transport, _clock, admit, client = case
    result = admit()
    with db.session() as session:
        fact = session.scalars(select(ResearchFactRecord)).first()
        attrs = json.loads(fact.attributes_json)
        attrs["latest_capture_ref"] = "raw://sha256/" + "0" * 64
        fact.attributes_json = json.dumps(attrs)
        payload = {
            name: getattr(fact, name)
            for name in (
                "requirement_id",
                "metric_family",
                "field",
                "instrument",
                "venue",
                "unit",
                "event_offset",
                "source_id",
                "independence_group",
                "delay_class",
                "payload_schema_ref",
            )
        }
        payload.update(
            value=json.loads(fact.value_json),
            attributes=attrs,
            **{
                name: as_utc(getattr(fact, name))
                for name in (
                    "window_start_at",
                    "window_end_at",
                    "published_at",
                )
            },
        )
        fact.payload_hash = research_fact_payload_hash(**payload)
        fact.fact_id = research_fact_instance_id(
            evidence_id=fact.evidence_id, payload_hash=fact.payload_hash
        )
    response = client.get(endpoint(result))
    assert response.status_code == 409
    assert response.json()["detail"] == "research_spot_capture_latest_receipt_mismatch"


def test_later_corrections_never_rewrite_original_evidence(case):
    _db, _archive, data, transport, clock, admit, client = case
    result = admit()
    before = client.get(endpoint(result)).json()
    clock[0] += timedelta(minutes=1)
    transport.bump += 1
    assert asyncio.run(data.refresh_due()) == 1
    after = client.get(endpoint(result))
    assert after.status_code == 200 and after.json() == before


def test_partial_path_keeps_original_partial_status_and_all_retained_pages(case):
    db, _archive, _data, _transport, _clock, admit, client = case
    with db.session() as session:
        session.delete(session.scalars(select(MarketCandleRecord).offset(600)).first())
    result = admit()
    assert json.loads(result.evidence_candidates[0].excerpt)["path_status"] == "partial"
    assert all(fact.field != "path" for fact in result.facts)
    response = client.get(endpoint(result))
    assert response.status_code == 200 and len(response.json()) == 5


def test_missing_file_never_becomes_empty_capture_list(case):
    _db, archive, _data, _transport, _clock, admit, client = case
    result = admit()
    ref = next(fact for fact in result.facts if fact.field == "price").attributes[
        "latest_capture_ref"
    ]
    digest = ref.rsplit("/", 1)[-1]
    (archive.root / digest[:2] / digest).unlink()  # only disposable synthetic fixture
    response = client.get(endpoint(result))
    assert response.status_code == 409
    assert response.json()["detail"] == "research_spot_capture_original_unavailable"
