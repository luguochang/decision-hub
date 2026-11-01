from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.market_features import build_market_features
from packages.kernel.decision_hub_kernel.application.market_path_store import (
    archive_feature,
)
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.risk_monitor import canonical_json
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    MarketCandleRecord,
    MarketPathAcquisitionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    canonical_hash,
    path_hashes,
    spot_scope,
)
from packages.provider_adapters.market.okx_path import (
    CapturedMarketPage,
    MarketPathError,
    OKXMarketPathAdapter,
)
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE, RawBodyArchive

NOW = datetime(2026, 10, 8, 12, 0, 10, tzinfo=UTC)
SCOPE = spot_scope("BTC-USDT")
REPO = Path(__file__).resolve().parents[2]


def clock(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PublicFixture:
    """Explicit synthetic market response transport; it exercises real capture logic."""

    def __init__(self, now):
        self.now, self.calls, self.failure, self.bump = now, [], None, Decimal(0)
        self.omit_middle = False

    async def fetch(self, url):
        self.calls.append(url)
        if self.failure:
            raise MarketPathError(self.failure, retryable=True)
        after = int(parse_qs(urlparse(url).query)["after"][0])
        rows = []
        for offset in range(1, 301):
            if self.omit_middle and offset == 150:
                continue
            stamp = after - offset * 60_000
            price = Decimal("100") + self.bump
            rows.append(
                [
                    str(stamp),
                    str(price),
                    str(price + 1),
                    str(price - 1),
                    str(price),
                    "1",
                    "100",
                    "100",
                    "1",
                ]
            )
        body = json.dumps({"code": "0", "data": rows}).encode()
        batch = CURRENT_CAPTURE.get()
        assert batch is not None
        response = httpx.Response(200, content=body, request=httpx.Request("GET", url))
        batch.record(url=url, started_at=self.now[0], completed_at=self.now[0], response=response)
        capture = batch.captures[-1]
        return CapturedMarketPage(body, self.now[0], capture["body_ref"])


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'market.sqlite3'}")
    database.create_all()
    now = [NOW]
    archive = RawBodyArchive(tmp_path / "raw")
    transport = PublicFixture(now)
    service = MarketRiskDataService(
        database,
        archive,
        tmp_path / "features",
        adapter=OKXMarketPathAdapter(fetcher=transport.fetch),
        scopes=(SCOPE,),
        clock=lambda: now[0],
        enabled=True,
    )
    yield database, service, transport, now
    database.engine.dispose()


def refresh(service):
    return asyncio.run(service.refresh_due())


def snapshot(service, now):
    return asyncio.run(service.snapshot(SCOPE.scope_id, as_of=now))


def acquisitions(database):
    with database.session() as session:
        return (
            session.query(MarketPathAcquisitionRecord)
            .order_by(MarketPathAcquisitionRecord.slot_at)
            .all()
        )


def test_refresh_commits_capture_bars_feature_and_snapshot_is_read_only(runtime):
    database, service, transport, now = runtime
    assert refresh(service) == 1
    initial_calls = len(transport.calls)
    assert initial_calls == 5
    with database.session() as session:
        assert session.query(MarketCandleRecord).count() == 1440
        assert session.query(RawCaptureRecord).count() == 5
    sample = snapshot(service, now[0])
    assert sample.data_state == "fresh" and sample.close_return == 0
    assert sample.data_as_of == NOW.replace(second=0)
    assert Path(sample.input_ref).is_file()
    assert snapshot(service, now[0] + timedelta(minutes=4)).data_state == "stale"
    assert len(transport.calls) == initial_calls
    assert refresh(service) == 0


def test_component_source_and_feature_use_real_same_writer_receipts(runtime):
    from apps.hub_worker.market_component_bootstrap import build_market_components
    from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
        MarketComponentAuditRecord,
        MarketComponentPointerRecord,
        MarketComponentVersionRecord,
    )

    database, service, _, _ = runtime
    service.component_consumer = build_market_components(database)
    build_market_components(database)  # Independent worker startup is idempotent.
    assert refresh(service) == 1
    receipt = json.loads(acquisitions(database)[0].receipt_json)
    refs = receipt["component_consumptions"]
    assert len(refs) == 2
    with database.session() as session:
        assert session.query(MarketComponentVersionRecord).count() == 12
        assert session.query(MarketComponentPointerRecord).count() == 0
        for ref in refs:
            original = session.get(MarketComponentAuditRecord, ref["audit_id"])
            assert original.payload_hash == ref["payload_hash"]
            body = json.loads(original.payload_json)
            assert body["state"] == "baseline_not_registry_approved"
            assert body["probability_qualified"] is False
        assert session.query(MarketCandleRecord).count() == 1440


def test_component_unpersisted_receipt_cannot_commit_market_data(runtime):
    from packages.kernel.decision_hub_kernel.application.market_component_functions import (
        normalize_market_path_component,
    )

    database, service, _, _ = runtime

    def forged(_session, **kwargs):
        return normalize_market_path_component(kwargs["payload"]), {"audit_id": "forged"}

    service.component_consumer = forged
    assert refresh(service) == 0
    assert acquisitions(database)[0].error_code == "component_consumption_original_binding_invalid"
    with database.session() as session:
        assert session.query(MarketCandleRecord).count() == 0


def test_component_feature_cannot_relabel_actual_path_basis(runtime):
    from apps.hub_worker.market_component_bootstrap import build_market_components
    from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
        MarketComponentAuditRecord,
    )

    database, service, _, _ = runtime
    actual = build_market_components(database)

    def broken(session, **kwargs):
        value, receipt = actual(session, **kwargs)
        if kwargs["kind"] == "feature":
            value["manifest_hash"] = "f" * 64
        return value, receipt

    service.component_consumer = broken
    assert refresh(service) == 0
    row = acquisitions(database)[0]
    assert row.error_code == "market_component_feature_basis_invalid"
    assert row.feature_ref is None
    with database.session() as session:
        # Raw source ingest remains true; broken feature audit is rolled back.
        assert session.query(MarketCandleRecord).count() == 1440
        assert session.query(MarketComponentAuditRecord).count() == 1


def test_default_switch_is_off(runtime):
    _, service, transport, now = runtime
    service.enabled = False
    assert refresh(service) == 0
    assert snapshot(service, now[0]).reason == "risk_data_disabled"
    assert not transport.calls


def test_incremental_overlap_restart_and_no_full_redownload(runtime):
    database, service, transport, now = runtime
    assert refresh(service) == 1
    now[0] += timedelta(minutes=1)
    restarted = MarketRiskDataService(
        database,
        service.raw_archive,
        service.feature_root,
        adapter=service.adapter,
        scopes=(SCOPE,),
        clock=lambda: now[0],
        enabled=True,
    )
    assert refresh(restarted) == 1
    assert len(transport.calls) == 6
    records = acquisitions(database)
    assert (records[-1].window_end_at - records[-1].window_start_at).total_seconds() == 180
    with database.session() as session:
        assert session.query(MarketCandleRecord).count() == 1441
    assert snapshot(restarted, now[0]).data_state == "fresh"


def test_two_failures_degrade_success_restores_and_persists_receipts(runtime):
    database, service, transport, now = runtime
    assert refresh(service) == 1
    transport.failure = "market_path_http_429"
    now[0] += timedelta(minutes=1)
    assert refresh(service) == 0
    assert acquisitions(database)[-1].consecutive_errors == 1
    assert snapshot(service, now[0]).data_state == "fresh"
    now[0] += timedelta(minutes=1)
    assert refresh(service) == 0
    failure = acquisitions(database)[-1]
    assert failure.health_state == "degraded" and failure.consecutive_errors == 2
    assert snapshot(service, now[0]).data_state == "error"
    transport.failure = None
    now[0] += timedelta(minutes=1)
    assert refresh(service) == 1
    assert acquisitions(database)[-1].health_state == "active"
    assert acquisitions(database)[-1].consecutive_errors == 0
    assert len(acquisitions(database)) == 4
    assert snapshot(service, now[0]).data_state == "fresh"


def test_store_acceptance_does_not_backdate_original_receipt(runtime):
    database, service, _, now = runtime
    assert refresh(service) == 1
    assert snapshot(service, now[0] - timedelta(microseconds=1)).data_state == "unknown"
    end = NOW.replace(second=0)
    assert (
        service.store.load(
            SCOPE, start_at=end - timedelta(hours=24), end_at=end, as_of=NOW - timedelta(seconds=1)
        )
        == ()
    )
    with database.session() as session:
        original = session.query(MarketCandleRecord).first()
        assert clock(json.loads(original.candle_json)["received_at"]) == NOW
        assert clock(json.loads(original.candle_json)["accepted_at"]) == NOW


def test_store_selects_only_revisions_known_at_cutoff(runtime):
    _, service, transport, now = runtime
    assert refresh(service) == 1
    cutoff = now[0]
    previous_end = cutoff.replace(second=0)
    now[0] += timedelta(minutes=1)
    transport.bump = Decimal("2")
    assert refresh(service) == 1
    before = service.store.load(
        SCOPE, start_at=previous_end - timedelta(minutes=2), end_at=previous_end, as_of=cutoff
    )
    after = service.store.load(
        SCOPE, start_at=previous_end - timedelta(minutes=2), end_at=previous_end, as_of=now[0]
    )
    assert {bar.close for bar in before} == {Decimal(100)}
    assert {bar.close for bar in after} == {Decimal(102)}
    assert snapshot(service, cutoff).data_state == "fresh"


def test_corrupt_raw_is_rejected_on_store_load_and_snapshot(runtime):
    database, service, _, now = runtime
    assert refresh(service) == 1
    with database.session() as session:
        row = session.query(MarketCandleRecord).first()
    digest = row.capture_hash
    (service.raw_archive.root / digest[:2] / digest).write_bytes(b"corrupt")
    end = NOW.replace(second=0)
    with pytest.raises(ValueError, match="archive_corrupt"):
        service.store.load(SCOPE, start_at=end - timedelta(hours=24), end_at=end, as_of=now[0])
    assert snapshot(service, now[0]).data_state == "error"


@pytest.mark.parametrize("mutation", ["price", "row_index", "url", "received", "payload"])
def test_store_refuses_forged_row_url_clock_or_payload(runtime, mutation):
    database, service, _, now = runtime
    assert refresh(service) == 1
    end = NOW.replace(second=0)
    bars = service.store.load(SCOPE, start_at=end - timedelta(hours=24), end_at=end, as_of=now[0])
    if mutation in {"url", "payload"}:
        with database.session() as session:
            if mutation == "url":
                for item in session.query(RawCaptureRecord).all():
                    item.url = item.url.replace("BTC-USDT", "ETH-USDT")
            else:
                item = session.query(MarketCandleRecord).first()
                body = json.loads(item.candle_json)
                body["close"] = "100.5"
                item.candle_json = canonical_json(body)
        with pytest.raises(ValueError):
            service.store.load(SCOPE, start_at=end - timedelta(hours=24), end_at=end, as_of=now[0])
        return
    from packages.kernel.decision_hub_kernel.ports.market_path import MarketPricePath

    altered = bars[-1].model_copy(
        update={
            "price": {"close": Decimal("100.5")},
            "row_index": {"row_index": 9999},
            "received": {"received_at": now[0] - timedelta(seconds=1)},
        }[mutation]
    )
    semantic, manifest = path_hashes(SCOPE, (altered,))
    fake = MarketPricePath(
        scope=SCOPE,
        window_start_at=altered.open_at,
        window_end_at=altered.close_at,
        candles=(altered,),
        page_count=1,
        request_count=1,
        status="complete",
        semantic_hash=semantic,
        manifest_hash=manifest,
    )
    with pytest.raises(ValueError):
        service.store.ingest(fake)


def test_feature_archive_corruption_returns_error(runtime):
    database, service, _, now = runtime
    assert refresh(service) == 1
    row = acquisitions(database)[-1]
    Path(row.feature_ref).write_bytes(b"bad feature")
    assert snapshot(service, now[0]).reason == "market_feature_archive_corrupt"


def test_expired_acquisition_lease_is_preserved_and_restart_recovers(runtime):
    database, service, _, now = runtime
    claim = asyncio.run(service._claim(SCOPE))
    assert claim is not None
    assert refresh(service) == 0
    now[0] += timedelta(minutes=4)
    assert refresh(service) == 1
    rows = acquisitions(database)
    assert len(rows) == 2
    assert rows[0].status == "failed" and rows[0].error_code == "market_acquisition_lease_expired"
    assert rows[1].status == "succeeded"
    assert snapshot(service, now[0]).data_state == "fresh"


def test_cancelled_cycle_has_durable_failure_without_swallowing_cancellation(runtime):
    database, service, _, _ = runtime
    original = service.adapter

    class CancelledAdapter:
        adapter_version = "explicit-cancel-test.v1"

        async def path(self, **_kwargs):
            raise asyncio.CancelledError()

    service.adapter = CancelledAdapter()
    with pytest.raises(asyncio.CancelledError):
        refresh(service)
    row = acquisitions(database)[0]
    assert row.status == "failed" and row.error_code == "market_acquisition_cancelled"
    service.adapter = original


def test_incomplete_original_path_never_commits_a_healthy_snapshot(runtime):
    database, service, transport, now = runtime
    transport.omit_middle = True
    assert refresh(service) == 0
    row = acquisitions(database)[0]
    assert row.status == "failed" and row.error_code == "market_acquisition_path_incomplete"
    assert row.feature_ref is None
    assert snapshot(service, now[0]).data_state == "unknown"
    with database.session() as session:
        assert 0 < session.query(MarketCandleRecord).count() < 1440


def test_real_f1_originals_ingest_pit_and_archived_snapshot(runtime):
    database, service, _, now = runtime
    root = REPO / "artifacts/market-risk-implementation-20261007/B0/F1"
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    row = summary["instruments"][0]
    captures = {}
    lineage = FactLineageStore(database)
    for meta in row["raw_receipts"]:
        body = (root / row["instrument"] / meta["body_ref"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta["body_sha256"]
        digest, ref = service.raw_archive.put(body)
        lineage.record_capture(
            {
                "capture_id": "original_" + str(meta["page"]),
                "context_id": "isolated-real-f1-replay",
                "url": meta["url"],
                "status_code": meta["status"],
                "body_hash": digest,
                "body_ref": ref,
                "byte_count": len(body),
                "media_type": "application/json",
                "adapter_version": "okx-closed-spot-path.v1",
                "error_code": None,
                "started_at": clock(meta["started_at"]),
                "completed_at": clock(meta["completed_at"]),
                "recorded_at": now[0],
            }
        )
        captures[parse_qs(urlparse(meta["url"]).query)["after"][0]] = CapturedMarketPage(
            body, clock(meta["completed_at"]), ref
        )

    async def fetch(url):
        return captures[parse_qs(urlparse(url).query)["after"][0]]

    start, end = clock(row["window_start"]), clock(row["window_end"])
    path = asyncio.run(
        OKXMarketPathAdapter(fetcher=fetch).path(scope=SCOPE, start_at=start, end_at=end)
    )
    assert service.store.ingest(path) == 1440
    assert service.store.ingest(path) == 0
    assert (
        service.store.load(SCOPE, start_at=start, end_at=end, as_of=clock(summary["completed_at"]))
        == ()
    )
    bars = service.store.load(SCOPE, start_at=start, end_at=end, as_of=now[0])
    assert len(bars) == 1440 and bars[0].open == Decimal(row["anchor_price"])
    feature = build_market_features(SCOPE, bars, now[0])
    assert feature.status == "stale" and feature.known_at == now[0] and feature.data_as_of == end
    ref, digest = archive_feature(
        canonical_json(feature.model_dump(mode="json")), service.feature_root
    )
    receipt = {"schema_version": "market-acquisition.v1", "manifest_hash": feature.manifest_hash}
    with database.session() as session:
        session.add(
            MarketPathAcquisitionRecord(
                acquisition_id="real_f1",
                scope_id=SCOPE.scope_id,
                slot_at=now[0],
                window_start_at=start,
                window_end_at=end,
                started_at=now[0],
                lease_expires_at=now[0] + timedelta(minutes=3),
                completed_at=now[0],
                known_at=now[0],
                next_due_at=now[0] + timedelta(minutes=1),
                status="succeeded",
                consecutive_errors=0,
                health_state="active",
                feature_ref=ref,
                feature_hash=digest,
                receipt_json=canonical_json(receipt),
                receipt_hash=canonical_hash(receipt),
            )
        )
    result = snapshot(service, now[0])
    assert result.data_state == "stale" and result.close_return is None
    assert result.data_as_of == end
