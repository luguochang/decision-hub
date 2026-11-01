import json
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.fact_routes import fact_router
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import (
    FactVersionRecord,
    ResearchEvidenceRecord,
    RunInputRefRecord,
    RunRecord,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
    safe_url,
)
from tests.research.test_fact_store import NOW, _database, _fact


def test_late_revision_does_not_leak_into_past_or_change_old_run(tmp_path, monkeypatch):
    db = _database(tmp_path)
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: NOW
    )
    store, facts = FactLineageStore(db), ResearchFactStore(db)
    facts.accept_facts(
        run_id="run-fact", capability_id="market.crypto_derivatives", facts=[_fact()], cutoff_at=NOW
    )
    old = store.list_versions(as_of=NOW)["items"][0]
    with db.session() as session:
        original = session.get(ResearchEvidenceRecord, "evidence-fact")
        values = {
            column.name: getattr(original, column.name) for column in original.__table__.columns
        }
        values.update(
            evidence_id="evidence-correction",
            content_hash="2" * 64,
            received_at=NOW + timedelta(hours=2),
            accepted_at=NOW + timedelta(hours=2),
        )
        session.add(ResearchEvidenceRecord(**values))
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow",
        lambda: NOW + timedelta(hours=2),
    )
    facts.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[
            _fact(
                fact_id="correction",
                evidence_id="evidence-correction",
                value="79999",
                payload_hash="3" * 64,
                received_at=NOW + timedelta(hours=2),
            )
        ],
        cutoff_at=NOW + timedelta(hours=2),
    )
    assert store.list_versions(as_of=NOW + timedelta(hours=1))["total"] == 1
    assert store.get_version(old["fact_version_id"])["fact"]["value"] == "79043"
    assert store.list_versions(as_of=NOW + timedelta(hours=3))["total"] == 2
    first = store.backfill()
    assert first == store.backfill()
    assert first["refs"] == first["facts"] == 2
    assert old["archive_status"] == "unarchived"


def test_evidence_capture_route_checks_run_and_returns_original_receipt(tmp_path):
    db = _database(tmp_path)
    store = FactLineageStore(db)
    batch = CaptureBatch("historical:unit:1:daily", "test", 
                         RawBodyArchive(tmp_path / "raw-captures"), store.record_capture)
    batch.record(url="https://example.org/daily", started_at=NOW, completed_at=NOW,
                 response=httpx.Response(200, content=b"original candles"))
    with db.session() as session:
        store.bind_parsed(session, evidence_ids=["evidence-fact"], captures=batch.captures)
    app = FastAPI()
    app.include_router(fact_router(db, tmp_path))
    client = TestClient(app)
    path = "/v1/research/runs/run-fact/evidence/evidence-fact/captures"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()[0]["capture_id"] == batch.captures[0]["capture_id"]
    assert client.get(path.replace("run-fact", "other-run")).status_code == 404
    assert client.get(path.replace("evidence-fact", "unknown")).status_code == 404
    capture_id = response.json()[0]["capture_id"]
    original = client.get(f"/v1/raw-captures/{capture_id}/body")
    assert original.text == "original candles" and original.headers['content-type'].startswith(
        "text/plain")


@pytest.mark.asyncio
async def test_raw_archive_failure_status_hash_integrity_and_context_isolation(tmp_path):
    archive = RawBodyArchive(tmp_path / "raw")
    saved = []
    batch = CaptureBatch("request-1", "parser-1", archive, saved.append)
    with capture_scope(batch):
        async with CapturingAsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(403, content=b"Access denied"))
        ) as client:
            response = await client.get("https://x.test/raw?api_key=private&symbol=BTC")
    assert response.status_code == saved[0]["status_code"] == 403
    assert "private" not in saved[0]["url"]
    assert archive.read(saved[0]["body_ref"]) == b"Access denied"
    async with CapturingAsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"not in a scope"))
    ) as client:
        await client.get("https://x.test/raw")
    assert len(saved) == 1
    digest = saved[0]["body_hash"]
    (tmp_path / "raw" / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupt"):
        archive.read(saved[0]["body_ref"])
    with pytest.raises(ValueError, match="invalid"):
        archive.read("raw://sha256/../../secrets")
    assert (
        safe_url("https://user:pass@x.test/a?token=secret#secret")
        == "https://x.test/a?token=REDACTED"
    )


def test_fact_api_rejects_naive_asof_and_exposes_archive_limits(tmp_path, monkeypatch):
    db = _database(tmp_path)
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: NOW
    )
    ResearchFactStore(db).accept_facts(
        run_id="run-fact", capability_id="market.crypto_derivatives", facts=[_fact()], cutoff_at=NOW
    )
    app = FastAPI()
    app.include_router(fact_router(db, tmp_path))
    client = TestClient(app)
    assert client.get("/v1/facts/versions?as_of=2026-09-05T00:00:00").status_code == 422
    assert client.get("/v1/facts/versions?limit=101").status_code == 422
    response = client.get("/v1/facts/versions", params={"as_of": NOW.isoformat(), "limit": 1})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["archive_status"] == "unarchived"
    detail = client.get("/v1/facts/versions/" + item["fact_version_id"]).json()
    assert detail["run_inputs"][0]["run_id"] == "run-fact"
    assert client.get("/v1/facts/versions/absent").status_code == 404
    assert client.get("/v1/raw-captures/absent/body").status_code == 404


@pytest.mark.asyncio
async def test_cross_run_reuse_keeps_origin_and_gate_checks(tmp_path, monkeypatch):
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
    from packages.kernel.decision_hub_kernel.application.research_evidence import (
        ResearchCapabilityError,
        ResearchCapabilityGatewayService,
        ResearchEvidenceService,
    )
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        ResearchObservabilityService,
    )
    from packages.provider_adapters.market.okx_research import OKXDerivativesResearchAdapter
    from packages.provider_adapters.research.raw_capture import reused_observation_times
    from packages.workbench_adapters.durable_research_gateway import (
        DurableResearchCapabilityGateway,
    )
    from packages.workbench_adapters.research_reuse import ArchivedResearchAdapter
    from tests.research import test_durable_research_gateway as fixtures

    now = utcnow()
    monkeypatch.setattr(fixtures, "NOW", now)
    db, run1, session1 = fixtures._database(tmp_path)
    _, run2, session2 = fixtures._database(tmp_path)
    _, run3, session3 = fixtures._database(tmp_path)
    with db.session() as session:
        study = ensure_study(
            session,
            question_key="test",
            title="test",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            now=now,
        )
        for run in (run1, run2, run3):
            session.get(RunRecord, run).study_id = study
    count = [0]

    def response(request):
        count[0] += 1
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {"last": "80000", "instId": "BTC-USDT", "ts": str(int(now.timestamp() * 1000))}
                ],
            },
        )

    async def fetch(url):
        async with CapturingAsyncClient(transport=httpx.MockTransport(response)) as client:
            return (await client.get(url)).json()

    adapter = OKXDerivativesResearchAdapter(fetcher=fetch)
    archive = RawBodyArchive(tmp_path / "raw-captures")
    wrapped = ArchivedResearchAdapter(adapter, db, archive, "parser-test")
    manifest = fixtures._manifest().model_copy(
        update={"capability_id": adapter.capability_id, "allowed_domains": ["okx.com"]}
    )
    gate = ResearchCapabilityGatewayService(
        [manifest],
        [wrapped],
        enabled_capabilities=[adapter.capability_id],
        archived_observation_resolver=reused_observation_times,
    )
    requirement = fixtures._requirement().model_copy(
        update={
            "requirement_id": "crypto_spot_confirmation",
            "authority_floor": "exchange",
            "source_priority": ["exchange"],
            "preferred_capabilities": [adapter.capability_id],
        }
    )
    durable = DurableResearchCapabilityGateway(
        gate,
        DshSessionLinkService(db),
        ResearchEvidenceService(db),
        ResearchObservabilityService(db),
        requirements={requirement.requirement_id: requirement},
        raw_archive=archive,
        parser_fingerprint="parser-test",
    )

    def query(sid, call):
        return fixtures._query(sid, request_id=call).model_copy(
            update={
                "capability_id": adapter.capability_id,
                "requirement_id": requirement.requirement_id,
                "query": "BTC spot",
                "symbols": ["BTC-USDT"],
                "fields": ["spot_price"],
                "allowed_domains": ["okx.com"],
                "target_url": None,
            }
        )

    first = await durable.execute(query(session1, "call1"))
    second = await durable.execute(query(session2, "call2"))
    assert count == [1]
    assert second.provider.endswith(":reused")
    assert second.facts[0].observed_at == first.facts[0].observed_at
    assert second.facts[0].independence_group == first.facts[0].independence_group
    assert second.facts[0].fact_id != first.facts[0].fact_id
    with db.session() as session:
        refs = session.query(RunInputRefRecord).all()
        assert len({ref.fact_version_id for ref in refs}) == 1
        assert {ref.use_mode for ref in refs} == {"fresh", "reused"}
        version = session.query(FactVersionRecord).one()
    assert len(FactLineageStore(db).get_version(version.fact_version_id)["raw_captures"]) == 1
    # License changes are checked before looking in the archive.
    gate._manifests[adapter.capability_id] = manifest.model_copy(
        update={"license_status": "pending"}
    )
    with pytest.raises(ResearchCapabilityError, match="license"):
        await durable.execute(query(session3, "denied"))
    assert count == [1]
    # Exact keys include market and instrument; an unrelated Study never hits.
    gate._manifests[adapter.capability_id] = manifest
    with db.session() as session:
        gold = ensure_study(
            session, question_key="test", title="gold", market="gold", instrument="XAU-USD", now=now
        )
        session.get(RunRecord, run3).study_id = gold
    third = await durable.execute(query(session3, "call3"))
    assert not third.provider.endswith(":reused")
    assert count == [2]
    with db.session() as session:
        session.get(RunRecord, run3).study_id = study
    original_key = wrapped.key(query(session3, "x"))
    assert original_key != wrapped.key(
        query(session3, "x").model_copy(update={"fields": ["funding_rate"]})
    )
    assert wrapped.key(query(session3, "x").model_copy(update={"mode": "replay"})) is None
    # Corruption cannot be served. A newly fetched identical body repairs its
    # content address while preserving the damaged bytes for diagnosis.
    detail = FactLineageStore(db).get_version(version.fact_version_id)
    raw_hash = detail["raw_captures"][0]["body_hash"]
    (archive.root / raw_hash[:2] / raw_hash).write_bytes(b"broken")
    fourth = await durable.execute(query(session3, "call4"))
    assert not fourth.provider.endswith(":reused")
    assert count == [3]
    assert list((archive.root / raw_hash[:2]).glob("*.corrupt.*"))
    # Cache expiration never extends on reuse; the adapter must fetch again.
    wrapped.clock = lambda: utcnow() + timedelta(seconds=31)
    fifth = await durable.execute(query(session3, "call5"))
    assert not fifth.provider.endswith(":reused")
    assert count == [4]


@pytest.mark.asyncio
async def test_concurrent_capture_scopes_keep_different_call_inputs(tmp_path):
    import asyncio

    saved = []
    archive = RawBodyArchive(tmp_path / "raw")

    async def collect(name):
        batch = CaptureBatch(name, "test", archive, saved.append)
        with capture_scope(batch):
            async with CapturingAsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=name.encode())
                )
            ) as client:
                await asyncio.sleep(0)
                await client.get("https://x.test/" + name)
        return batch

    one, two = await asyncio.gather(collect("one"), collect("two"))
    assert one.captures[0]["context_id"] == "one"
    assert two.captures[0]["context_id"] == "two"
    assert archive.read(one.captures[0]["body_ref"]) == b"one"
    assert archive.read(two.captures[0]["body_ref"]) == b"two"


def test_input_archive_resolves_frozen_coverage_and_detects_corruption(tmp_path):
    from packages.contracts_py.decision_hub_contracts import CoverageManifest, DatasetCoverage
    from packages.provider_adapters.historical.archive import HistoricalDataArchive
    from packages.workbench_adapters.fact_assets import FactAssetResolver

    archive = HistoricalDataArchive(tmp_path / "historical")
    partition = archive.write_partition(
        dataset_id="crypto.btc_usdt.ohlcv_1d",
        partition_key="2026-09",
        source_id="gateio-public",
        rows=[{"timestamp": NOW.isoformat(), "close": "79043"}],
        unit="usdt",
        frequency="1d",
        instrument="BTC-USDT",
        venue="gateio",
        contract_type="spot",
        created_at=NOW,
    )
    dataset = DatasetCoverage(
        dataset_id="crypto.btc_usdt.ohlcv_1d",
        status="complete",
        source_id="gateio-public",
        requested_start_at=NOW,
        requested_end_at=NOW + timedelta(days=1),
        actual_start_at=NOW,
        actual_end_at=NOW,
        row_count=1,
        unit="usdt",
        frequency="1d",
        instrument="BTC-USDT",
        venue="gateio",
        contract_type="spot",
        publication_time_precision="date",
        revision_policy="immutable",
        partition_refs=[partition.payload_ref],
        gaps=[],
    )
    ref = archive.write_coverage(
        CoverageManifest(
            schema_version="coverage-manifest.v1",
            job_id="datajob_" + "a" * 32,
            requested_start_at=NOW,
            requested_end_at=NOW + timedelta(days=1),
            datasets=[dataset],
            generated_at=NOW,
        )
    )
    version = {"structured_payload_ref": ref}
    resolver = FactAssetResolver(tmp_path)
    assert resolver.read(version)["verified"] is True
    assert resolver.read(version, 0)["payload"]["rows"][0]["close"] == "79043"
    with pytest.raises(KeyError):
        resolver.read(version, 1)
    path = archive.partitions / (partition.payload_hash + ".json")
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        resolver.read(version)


def test_fact_dimensions_use_payload_identity_and_pit_before_paging(tmp_path, monkeypatch):
    db = _database(tmp_path)
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: NOW
    )
    ResearchFactStore(db).accept_facts(
        run_id="run-fact", capability_id="market.crypto_derivatives", facts=[_fact()], cutoff_at=NOW
    )
    with db.session() as session:
        original = session.query(FactVersionRecord).one()
        for suffix, family, source, known, observed in [
            ("derivative", "crypto.derivatives", "derivative-source", NOW, NOW),
            ("late", "crypto.spot", "late-source", NOW + timedelta(hours=2), NOW),
            ("future", "crypto.spot", "future-source", NOW, NOW + timedelta(hours=2)),
        ]:
            values = {c.name: getattr(original, c.name) for c in original.__table__.columns}
            payload = json.loads(original.payload_json)
            payload.update(metric_family=family, source_id=source, observed_at=observed.isoformat())
            values.update(
                fact_version_id=suffix, semantic_key=suffix, source_id=source,
                known_at=known, observed_at=observed, payload_json=json.dumps(payload),
            )
            session.add(FactVersionRecord(**values))
    app = FastAPI()
    app.include_router(fact_router(db, tmp_path))
    client = TestClient(app)
    options = client.get("/v1/facts/options", params={"as_of": NOW.isoformat()})
    assert options.status_code == 200
    assert options.json()["sources"] == ["coinex-public", "derivative-source"]
    assert options.json()["fields"] == ["price"]
    assert options.json()["instruments"] == [
        {"instrument": "BTCUSDT", "metric_family": "crypto.derivatives"},
        {"instrument": "BTCUSDT", "metric_family": "crypto.spot"},
    ]
    response = client.get("/v1/facts/versions", params={
        "as_of": NOW.isoformat(), "fact_instrument": "BTCUSDT",
        "metric_family": "crypto.derivatives", "source_id": "derivative-source",
        "field": "price", "offset": 999, "limit": 1,
    })
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1 and page["offset"] == 0
    assert page["items"][0]["fact_version_id"] == "derivative"
    empty = client.get("/v1/facts/versions", params={
        "as_of": NOW.isoformat(), "fact_instrument": "DXY", "offset": 999,
    }).json()
    assert empty["items"] == [] and empty["total"] == empty["offset"] == 0
    past = client.get("/v1/facts/options", params={
        "as_of": (NOW - timedelta(seconds=1)).isoformat(),
    }).json()
    assert past["fields"] == past["sources"] == past["instruments"] == []
    later = client.get("/v1/facts/options", params={
        "as_of": (NOW + timedelta(hours=3)).isoformat(),
    }).json()
    assert len(later["sources"]) == 4
    assert client.get("/v1/facts/options?as_of=2026-09-04T08:00:00").status_code == 422
    # Existing instrument continues to filter research scope, not fact payload.
    legacy = client.get("/v1/facts/versions", params={
        "as_of": NOW.isoformat(), "instrument": "BTCUSDT",
    }).json()
    assert legacy["total"] == 0
