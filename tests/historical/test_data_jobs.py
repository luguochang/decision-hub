from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.research_mcp.main import load_capability_manifests
from packages.contracts_py.decision_hub_contracts import DataJobCreate, ResearchCapabilityQuery
from packages.contracts_py.decision_hub_contracts.models import ResearchStrategyReceipt
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.historical import (
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    HistoricalDataResearchAdapter,
    HistoricalDatasetBatch,
)

NOW = datetime(2026, 2, 2, 4, 0, tzinfo=UTC)


class _Loader:
    def __init__(self, batch: HistoricalDatasetBatch) -> None:
        self.batch = batch
        self.calls = 0

    async def load(self, dataset_id: str, start: datetime, end: datetime) -> HistoricalDatasetBatch:
        del dataset_id, start, end
        self.calls += 1
        return self.batch


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'history.sqlite3'}")
    database.create_all()
    return database


def _request(*, key: str = "three-month-core-data") -> DataJobCreate:
    return DataJobCreate(
        idempotency_key=key,
        requested_by="owner",
        dataset_ids=[
            "crypto.btc_usdt.ohlcv_1h",
            "crypto.btc_usdt_swap.open_interest",
        ],
        window_start_at=NOW - timedelta(hours=48),
        window_end_at=NOW,
    )


def test_owner_job_is_idempotent_and_does_not_create_research_run(tmp_path: Path) -> None:
    database = _database(tmp_path)
    service = DataJobService(database, clock=lambda: NOW)

    first, created = service.create(_request())
    same, created_again = service.create(_request())

    assert created is True
    assert created_again is False
    assert same.job_id == first.job_id
    assert first.status == "queued"
    connection = sqlite3.connect(tmp_path / "history.sqlite3")
    assert connection.execute("select count(*) from runs").fetchone()[0] == 0
    connection.close()


def test_owner_job_rejects_same_key_with_different_payload(tmp_path: Path) -> None:
    service = DataJobService(_database(tmp_path), clock=lambda: NOW)
    service.create(_request())
    conflicting = _request().model_copy(update={"window_start_at": NOW - timedelta(hours=24)})

    with pytest.raises(ValueError, match="data_job_idempotency_conflict"):
        service.create(conflicting)


def test_expired_job_can_be_reclaimed_and_old_attempt_cannot_finish(tmp_path):
    now = [NOW]
    service = DataJobService(_database(tmp_path), clock=lambda: now[0])
    job, _ = service.create(_request())
    first = service.start(job.job_id)
    with pytest.raises(ValueError, match="already_running"):
        service.start(job.job_id)
    now[0] += timedelta(minutes=11)
    second = service.start(job.job_id)
    assert second.attempt == first.attempt + 1
    with pytest.raises(ValueError, match="lease_lost"):
        service.finish(
            job.job_id,
            status="completed",
            partition_refs=[],
            coverage_manifest_ref=None,
            errors=[],
            expected_attempt=first.attempt,
        )
    now[0] += timedelta(minutes=11)
    assert service.recover_expired() == 1
    assert service.recover_expired() == 0
    assert service.get(job.job_id).errors == ["data_job_interrupted"]
    assert service.get(job.job_id).status == "failed"


@pytest.mark.asyncio
async def test_history_deadline_finishes_job_instead_of_leaving_running(tmp_path):
    service = DataJobService(_database(tmp_path), clock=lambda: NOW)
    job, _ = service.create(_request())

    class HungLoader:
        async def load(self, *args):
            await asyncio.Event().wait()

    runner = HistoricalDataJobRunner(
        service,
        HistoricalDataArchive(tmp_path / "archive"),
        {"crypto.btc_usdt.ohlcv_1h": HungLoader()},
        timeout_seconds=0.02,
    )
    result = await runner.run(job.job_id)
    assert result.status == "failed"
    assert "data_job_deadline_exceeded" in result.errors


@pytest.mark.asyncio
async def test_runner_publishes_monthly_partitions_and_honest_partial_coverage(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = DataJobService(database, clock=lambda: NOW)
    job, _ = service.create(_request())
    rows: list[dict[str, object]] = [
        {
            "timestamp": (NOW - timedelta(hours=offset)).isoformat(),
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": "1.5",
            "volume_base": "10",
            "volume_currency": "10",
            "volume_quote": "15",
            "confirmed": True,
        }
        for offset in range(1, 49)
    ]
    price_loader = _Loader(
        HistoricalDatasetBatch(
            dataset_id="crypto.btc_usdt.ohlcv_1h",
            source_id="okx-public",
            rows=rows,
            unit="usdt_and_btc",
            frequency="1h",
            instrument="BTC-USDT",
            venue="okx",
            contract_type="spot",
            publication_time_precision="millisecond",
            revision_policy="immutable",
            gaps=[],
        )
    )
    oi_loader = _Loader(
        HistoricalDatasetBatch(
            dataset_id="crypto.btc_usdt_swap.open_interest",
            source_id="okx-public",
            rows=[],
            unit="btc",
            frequency="snapshot_only",
            instrument="BTC-USDT-SWAP",
            venue="okx",
            contract_type="linear_perpetual",
            publication_time_precision="millisecond",
            revision_policy="immutable",
            gaps=["okx_public_open_interest_has_no_historical_endpoint"],
        )
    )
    archive = HistoricalDataArchive(tmp_path / "assets")
    runner = HistoricalDataJobRunner(
        service,
        archive,
        {
            "crypto.btc_usdt.ohlcv_1h": price_loader,
            "crypto.btc_usdt_swap.open_interest": oi_loader,
        },
        clock=lambda: NOW,
    )

    completed = await runner.run(job.job_id)
    manifest = archive.load_coverage(completed.coverage_manifest_ref or "")

    assert completed.status == "partial"
    assert completed.attempt == 1
    assert len(completed.partition_refs) == 2  # January and February
    by_id = {item.dataset_id: item for item in manifest.datasets}
    assert by_id["crypto.btc_usdt.ohlcv_1h"].status == "complete"
    assert by_id["crypto.btc_usdt.ohlcv_1h"].row_count == 48
    assert by_id["crypto.btc_usdt_swap.open_interest"].status == "missing"
    assert by_id["crypto.btc_usdt_swap.open_interest"].row_count == 0
    assert completed.errors == [
        "crypto.btc_usdt_swap.open_interest:okx_public_open_interest_has_no_historical_endpoint"
    ]
    connection = sqlite3.connect(tmp_path / "history.sqlite3")
    assert connection.execute("select count(*) from runs").fetchone()[0] == 0
    connection.close()

    rerun = await runner.run(job.job_id)
    assert rerun.job_id == job.job_id
    assert rerun.attempt == 2
    assert rerun.partition_refs == completed.partition_refs
    assert rerun.coverage_manifest_ref == completed.coverage_manifest_ref


@pytest.mark.asyncio
async def test_historical_summary_is_read_through_existing_gateway_with_decimal_math(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = DataJobService(database, clock=lambda: NOW)
    job, _ = jobs.create(
        DataJobCreate(
            idempotency_key="gateway-history-job",
            requested_by="owner",
            dataset_ids=["crypto.btc_usdt.ohlcv_1h"],
            window_start_at=NOW - timedelta(hours=3),
            window_end_at=NOW,
        )
    )
    rows: list[dict[str, object]] = [
        {
            "timestamp": (NOW - timedelta(hours=offset)).isoformat(),
            "open": "100" if offset == 3 else "101",
            "high": "112",
            "low": "99",
            "close": "110" if offset == 1 else "100",
            "volume_base": "1.5",
            "volume_currency": "150",
            "volume_quote": "150",
            "confirmed": True,
        }
        for offset in (3, 2, 1)
    ]
    runner = HistoricalDataJobRunner(
        jobs,
        HistoricalDataArchive(tmp_path / "assets"),
        {
            "crypto.btc_usdt.ohlcv_1h": _Loader(
                HistoricalDatasetBatch(
                    dataset_id="crypto.btc_usdt.ohlcv_1h",
                    source_id="okx-public",
                    rows=rows,
                    unit="usdt_and_btc",
                    frequency="1h",
                    instrument="BTC-USDT",
                    venue="okx",
                    contract_type="spot",
                    publication_time_precision="millisecond",
                    revision_policy="immutable",
                    gaps=[],
                )
            )
        },
        clock=lambda: NOW,
    )
    await runner.run(job.job_id)
    adapter = HistoricalDataResearchAdapter(jobs, runner.archive, clock=lambda: NOW)
    manifest = next(
        item for item in load_capability_manifests() if item.capability_id == "data.historical"
    )
    query = ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="history-gateway-query",
        capability_id="data.historical",
        requirement_id="macro_transmission",
        query="BTC three hour baseline",
        target_url=None,
        symbols=["crypto.btc_usdt.ohlcv_1h"],
        fields=["period_open", "period_close", "return_pct", "row_count"],
        allowed_domains=[],
        max_results=1,
        max_cost_usd=0,
        research_session_id="history-gateway-session",
        round=1,
        mode="replay",
        observed_at=NOW,
        cutoff_at=NOW + timedelta(minutes=1),
        window_start_at=NOW - timedelta(hours=3),
        window_end_at=NOW,
    )
    result = await ResearchCapabilityGatewayService(
        [manifest],
        [adapter],
        enabled_capabilities=["data.historical"],
        clock=lambda: NOW,
    ).execute(query)

    summary = {item.field: item.value for item in result.facts or []}
    assert summary["period_open"] == "100"
    assert summary["period_close"] == "110"
    assert summary["return_pct"] == "10"
    assert summary["row_count"] == "3"

    missing_query = query.model_copy(
        update={
            "request_id": "history-missing-query",
            "symbols": ["crypto.btc_usdt_swap.open_interest"],
            "fields": ["row_count"],
        }
    )
    with pytest.raises(ResearchCapabilityError) as missing:
        await HistoricalDataResearchAdapter(jobs, runner.archive, clock=lambda: NOW).execute(
            missing_query
        )
    assert missing.value.error_code == "historical_dataset_coverage_missing"


@pytest.mark.asyncio
async def test_daily_history_projects_a_replayable_strategy_receipt(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = DataJobService(database, clock=lambda: NOW)
    job, _ = jobs.create(
        DataJobCreate(
            idempotency_key="daily-strategy-job",
            requested_by="owner",
            dataset_ids=["crypto.btc_usdt.ohlcv_1d"],
            window_start_at=NOW - timedelta(days=90),
            window_end_at=NOW,
        )
    )
    rows: list[dict[str, object]] = []
    for offset in range(90, 0, -1):
        close = 100.0 + (90 - offset) * 1.0
        timestamp = NOW - timedelta(days=offset)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": str(close - 0.5),
                "high": str(close + 1.0),
                "low": str(close - 1.0),
                "close": str(close),
                "volume_base": "100",
                "volume_currency": str(close * 100),
                "volume_quote": str(close * 100),
                "confirmed": True,
            }
        )
    archive = HistoricalDataArchive(tmp_path / "assets")
    runner = HistoricalDataJobRunner(
        jobs,
        archive,
        {
            "crypto.btc_usdt.ohlcv_1d": _Loader(
                HistoricalDatasetBatch(
                    dataset_id="crypto.btc_usdt.ohlcv_1d",
                    source_id="okx-public",
                    rows=rows,
                    unit="usdt_and_btc",
                    frequency="1d",
                    instrument="BTC-USDT",
                    venue="okx",
                    contract_type="spot",
                    publication_time_precision="millisecond",
                    revision_policy="immutable",
                    gaps=[],
                )
            )
        },
        clock=lambda: NOW,
    )
    completed = await runner.run(job.job_id)
    assert completed.status == "completed"

    query = ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="daily-strategy-query",
        capability_id="data.historical",
        requirement_id="market_regime_context",
        query="BTC daily trend",
        target_url=None,
        symbols=["crypto.btc_usdt.ohlcv_1d"],
        fields=["period_close", "sma_20", "sma_60", "return_20_pct", "row_count"],
        allowed_domains=[],
        max_results=1,
        max_cost_usd=0,
        research_session_id="daily-strategy-session",
        round=1,
        mode="replay",
        observed_at=NOW,
        cutoff_at=NOW,
        window_start_at=NOW - timedelta(days=90),
        window_end_at=NOW,
    )
    result = await HistoricalDataResearchAdapter(jobs, archive, clock=lambda: NOW).execute(query)

    receipt_payload = json.loads(result.facts[0].attributes["daily_strategy_receipt_json"])
    receipt = ResearchStrategyReceipt.model_validate(receipt_payload)
    assert receipt.outlook == "bullish"
    assert "持有倾向" in receipt.action_candidate
    assert receipt.probability is None
    assert receipt.probability_status == "unavailable"
    assert receipt.bar_count == 90
    assert receipt.evidence_refs == [result.evidence_candidates[0].evidence_id]


def test_partition_load_rejects_tampering(tmp_path: Path) -> None:
    archive = HistoricalDataArchive(tmp_path / "assets")
    manifest = archive.write_partition(
        dataset_id="macro.fred.dgs2",
        partition_key="2026-01",
        source_id="fred-public",
        rows=[{"timestamp": "2026-01-02T00:00:00+00:00", "value": "4.25"}],
        unit="yield_percent",
        frequency="native_business_day",
        instrument="DGS2",
        venue=None,
        contract_type=None,
        created_at=NOW,
    )
    digest = manifest.payload_ref.rsplit("/", 1)[-1]
    (archive.partitions / f"{digest}.json").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="historical_asset_hash_mismatch"):
        archive.load_partition(manifest.payload_ref)
