"""Large synthetic ETH originals exercise real private capture/store/Run readers."""

import asyncio
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
    spot_run_context_inputs_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.persistence.run_spot_admission import (
    RunSpotAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPricePath,
    canonical_hash,
    path_hashes,
    spot_scope,
)
from packages.provider_adapters.market.okx_path import CapturedMarketPage, OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import CaptureBatch, capture_scope
from tests.market_risk.test_spot_scope_admission import prepared

assert prepared is not None


def _ingest_216_originals(db, store, now):
    scope = spot_scope("ETH-USDT")
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(days=1)

    def persist(capture):
        with db.session() as session:
            session.add(RawCaptureRecord(**capture))

    batch = CaptureBatch(
        "fixture-216-eth-captures", "synthetic-okx-response.v1", store.archive, persist
    )

    async def fetch(url):
        query = parse_qs(urlparse(url).query)
        assert query["instId"] == ["ETH-USDT"]
        after = int(query["after"][0])
        index = int((after / 1000 - start.timestamp()) // 60)
        # 216 distinct original responses cover exactly 1440 closed minutes.
        first = next(i * 1440 // 216 for i in range(216) if (i + 1) * 1440 // 216 == index)
        rows = [
            [str(after - offset * 60_000), "100", "101", "99", "100", "1", "100", "100", "1"]
            for offset in range(1, index - first + 1)
        ]
        body = json.dumps({"code": "0", "data": rows}).encode()
        response = httpx.Response(200, content=body, request=httpx.Request("GET", url))
        batch.record(url=url, started_at=now, completed_at=now, response=response)
        return CapturedMarketPage(body, now, batch.captures[-1]["body_ref"])

    async def collect():
        adapter = OKXMarketPathAdapter(fetcher=fetch)
        bars = []
        with capture_scope(batch):
            for i in range(216):
                page = await adapter.candles(
                    scope=scope,
                    start_at=start + timedelta(minutes=i * 1440 // 216),
                    end_at=start + timedelta(minutes=(i + 1) * 1440 // 216),
                )
                bars.extend(page.candles)
        return tuple(bars)

    bars = asyncio.run(collect())
    semantic, manifest = path_hashes(scope, bars)
    assert (
        store.ingest(
            MarketPricePath(
                scope=scope,
                window_start_at=start,
                window_end_at=end,
                candles=bars,
                page_count=216,
                request_count=216,
                status="complete",
                semantic_hash=semantic,
                manifest_hash=manifest,
            )
        )
        == 1440
    )


def test_large_eth_context_has_complete_bounded_summary_and_immutable_originals(prepared):
    db, now, _, _, runtime = prepared
    service, effective, host = runtime("ETH-USDT")
    _ingest_216_originals(db, service.path_store, now[0])
    token = service.approve_trial(request_id="large-eth", effective=effective, host=host)
    event_id, _, _ = AdmissionService(db, clock=lambda: now[0]).admit(
        ObservationCreate(
            text="Synthetic ETH spot 24h context-size control.",
            source_id="fixture-eth",
            source_type=SourceType.transcript,
            observed_at=now[0] - timedelta(seconds=1),
            language="en",
        )
    )
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="large-eth",
            title="ETH spot 24h",
            market="crypto",
            instrument="ETH-USDT",
            horizon_scope="24h",
            now=now[0],
        )

    def resolver(session, study, at):
        assert study.instrument == "ETH-USDT"
        return service.resolve_current_in_session(
            session, effective=effective, host=host, assessed_at=at
        )

    run_id, created = RunService(db, clock=lambda: now[0], spot_admission_resolver=resolver).create(
        event_id,
        "large-eth-run",
        strategy_version="research.v1",
        study_id=study_id,
    )
    assert created
    with db.session() as session:
        assert session.query(MarketCandleRecord).count() == 1440
        assert session.query(RawCaptureRecord).count() == 216
        original = session.get(SpotResearchAdmissionRecord, token.admission_id)
        row = session.get(RunSpotAdmissionRecord, run_id)
        assert original is not None and row is not None
        before = (original.proof_json, original.proof_hash, row.payload_json, row.payload_hash)
        frozen = read_run_spot_admission_in_session(session, run_id)
        assert frozen is not None and frozen.instrument == "ETH-USDT"
        hashes = list(frozen.path.capture_hashes)
        assert len(hashes) == len(set(hashes)) == 216
        assert len(json.dumps({"capture_hashes": hashes})) > 4000
        assert json.loads(original.proof_json)["path"]["capture_hashes"] == hashes
        assert json.loads(row.payload_json)["path"]["capture_hashes"] == hashes

        inputs = spot_run_context_inputs_in_session(session, run_id)
        summary = json.loads(inputs[1].excerpt)
        assert all(len(item.excerpt) <= 4000 for item in inputs)
        assert "capture_hashes" not in summary
        assert summary["capture_count"] == 216
        assert summary["capture_hashes_hash"] == canonical_hash(hashes)
        assert summary["scope"] == spot_scope("ETH-USDT").model_dump(mode="json")
        assert summary["qualification"] == "unknown"
        assert summary["semantic_hash"] == frozen.path.semantic_hash
        assert summary["manifest_hash"] == frozen.path.manifest_hash
        assert (
            inputs[1].evidence_id
            == inputs[1].source_id
            == summary["original_ref"]
            == (f"market-path-manifest:{frozen.scope.scope_id}:{frozen.path.manifest_hash}")
        )
        assert inputs[1].content_hash == hashlib.sha256(inputs[1].excerpt.encode()).hexdigest()
        assert read_run_spot_admission_in_session(session, run_id) == frozen
        session.refresh(original)
        session.refresh(row)
        assert before == (
            original.proof_json,
            original.proof_hash,
            row.payload_json,
            row.payload_hash,
        )
