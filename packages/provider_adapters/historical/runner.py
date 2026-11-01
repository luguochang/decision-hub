from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from packages.contracts_py.decision_hub_contracts import (
    CoverageManifest,
    DataJobView,
    DatasetCoverage,
)
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)

from .archive import HistoricalDataArchive
from .loaders import HistoricalDatasetBatch


class HistoricalDataLoader(Protocol):
    async def load(
        self, dataset_id: str, start: datetime, end: datetime
    ) -> HistoricalDatasetBatch: ...


_METADATA = {
    "crypto.eth_usdt.ohlcv_1d": (
        "gateio-public", "usdt_and_eth", "1d", "ETH-USDT", "gateio", "spot",
        "second", "unknown",
    ),
    "crypto.btc_usdt.ohlcv_1h": (
        "okx-public",
        "usdt_and_btc",
        "1h",
        "BTC-USDT",
        "okx",
        "spot",
        "millisecond",
        "immutable",
    ),
    "crypto.btc_usdt.ohlcv_1d": (
        "okx-public",
        "usdt_and_btc",
        "1d",
        "BTC-USDT",
        "okx",
        "spot",
        "millisecond",
        "immutable",
    ),
    "crypto.btc_usdt_swap.funding_rate": (
        "okx-public",
        "rate",
        "scheduled_8h_variable",
        "BTC-USDT-SWAP",
        "okx",
        "linear_perpetual",
        "millisecond",
        "immutable",
    ),
    "crypto.btc_usdt_swap.open_interest": (
        "okx-public",
        "btc",
        "snapshot_only",
        "BTC-USDT-SWAP",
        "okx",
        "linear_perpetual",
        "millisecond",
        "immutable",
    ),
    "macro.fred.dgs2": (
        "fred-public",
        "yield_percent",
        "native_business_day",
        "DGS2",
        None,
        None,
        "date",
        "revisable",
    ),
    "macro.fred.dgs10": (
        "fred-public",
        "yield_percent",
        "native_business_day",
        "DGS10",
        None,
        None,
        "date",
        "revisable",
    ),
    "macro.fred.dtwexbgs": (
        "fred-public",
        "index",
        "native_business_day",
        "DTWEXBGS",
        None,
        None,
        "date",
        "revisable",
    ),
}


class HistoricalDataJobRunner:
    """Run one explicit DataJob and publish its immutable coverage evidence."""

    def __init__(
        self,
        jobs: DataJobService,
        archive: HistoricalDataArchive,
        loaders: Mapping[str, HistoricalDataLoader],
        *,
        clock: Callable[[], datetime] = utcnow,
        timeout_seconds: float = 300,
    ) -> None:
        self.jobs = jobs
        self.archive = archive
        self.loaders = dict(loaders)
        self.clock = clock
        if not 0 < timeout_seconds < 600:
            raise ValueError("historical_job_timeout_invalid")
        self.timeout_seconds = timeout_seconds

    async def run(self, job_id: str, *, queued_only: bool = False) -> DataJobView:
        job = self.jobs.start(job_id, queued_only=queued_only)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._run_started(job)
        except (TimeoutError, asyncio.CancelledError) as exc:
            code = (
                "data_job_deadline_exceeded"
                if isinstance(exc, TimeoutError)
                else "data_job_interrupted"
            )
            result = self.jobs.finish(
                job_id,
                status="failed",
                partition_refs=job.partition_refs,
                coverage_manifest_ref=job.coverage_manifest_ref,
                errors=[*job.errors, code],
                expected_attempt=job.attempt,
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return result

    async def _run_started(self, job: DataJobView) -> DataJobView:
        coverages: list[DatasetCoverage] = []
        partition_refs: list[str] = []
        errors: list[str] = []
        for dataset_id in job.dataset_ids:
            loader = self.loaders.get(dataset_id)
            if loader is None:
                error = "historical_dataset_loader_missing"
                coverages.append(self._missing(job, dataset_id, error))
                errors.append(f"{dataset_id}:{error}")
                continue
            try:
                capture = CaptureBatch(
                    context_id=f"historical:{job.job_id}:{job.attempt}:{dataset_id}",
                    adapter_version="historical-http.v1",
                    archive=RawBodyArchive(self.archive.root.parent / "raw-captures"),
                    persist=FactLineageStore(self.jobs.database).record_capture,
                )
                with capture_scope(capture):
                    batch = await loader.load(dataset_id, job.window_start_at, job.window_end_at)
                coverage = self._archive_batch(job, batch)
                # Original HTTP receipts, never reconstructed from normalized rows.
                coverage = coverage.model_copy(update={"raw_capture_ids": [
                    c["capture_id"] for c in capture.captures
                    if c["status_code"] is not None and 200 <= c["status_code"] < 300
                    and c["body_ref"] and not c["error_code"]
                ]})
            except Exception as exc:
                code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__.lower()
                coverage = self._missing(job, dataset_id, code)
            coverages.append(coverage)
            partition_refs.extend(coverage.partition_refs)
            errors.extend(f"{dataset_id}:{gap}" for gap in coverage.gaps)

        manifest = CoverageManifest(
            schema_version="coverage-manifest.v1",
            job_id=job.job_id,
            requested_start_at=job.window_start_at,
            requested_end_at=job.window_end_at,
            datasets=coverages,
            # The request creation instant makes a same-window rerun produce
            # the same manifest when the source rows are unchanged.
            generated_at=job.created_at,
        )
        manifest_ref = self.archive.write_coverage(manifest)
        if all(item.status == "complete" for item in coverages):
            status = "completed"
        elif any(item.row_count > 0 for item in coverages):
            status = "partial"
        else:
            status = "failed"
        return self.jobs.finish(
            job.job_id,
            status=status,
            partition_refs=partition_refs,
            coverage_manifest_ref=manifest_ref,
            errors=errors,
            expected_attempt=job.attempt,
        )

    def _archive_batch(self, job: DataJobView, batch: HistoricalDatasetBatch) -> DatasetCoverage:
        by_month: dict[str, list[dict[str, object]]] = {}
        for row in batch.rows:
            timestamp = _timestamp(row.get("timestamp"))
            if timestamp is None or not (job.window_start_at <= timestamp < job.window_end_at):
                continue
            by_month.setdefault(timestamp.strftime("%Y-%m"), []).append(row)
        manifests = [
            self.archive.write_partition(
                dataset_id=batch.dataset_id,
                partition_key=month,
                source_id=batch.source_id,
                rows=rows,
                unit=batch.unit,
                frequency=batch.frequency,
                instrument=batch.instrument,
                venue=batch.venue,
                contract_type=batch.contract_type,
                created_at=self.clock(),
            )
            for month, rows in sorted(by_month.items())
        ]
        actual_start = min((item.actual_start_at for item in manifests), default=None)
        actual_end = max((item.actual_end_at for item in manifests), default=None)
        row_count = sum(item.row_count for item in manifests)
        gaps = list(batch.gaps)
        status = _coverage_status(
            batch.dataset_id,
            job.window_start_at,
            job.window_end_at,
            actual_start,
            actual_end,
            row_count,
        )
        if status != "complete" and not gaps:
            gaps.append("actual_coverage_does_not_span_requested_window")
        return DatasetCoverage.model_validate(
            {
                "dataset_id": batch.dataset_id,
                "status": status,
                "source_id": batch.source_id,
                "requested_start_at": job.window_start_at,
                "requested_end_at": job.window_end_at,
                "actual_start_at": actual_start,
                "actual_end_at": actual_end,
                "row_count": row_count,
                "unit": batch.unit,
                "frequency": batch.frequency,
                "instrument": batch.instrument,
                "venue": batch.venue,
                "contract_type": batch.contract_type,
                "publication_time_precision": batch.publication_time_precision,
                "revision_policy": batch.revision_policy,
                "partition_refs": [item.payload_ref for item in manifests],
                "gaps": gaps,
            }
        )

    @staticmethod
    def _missing(job: DataJobView, dataset_id: str, error: str) -> DatasetCoverage:
        metadata = _METADATA[dataset_id]
        return DatasetCoverage.model_validate(
            {
                "dataset_id": dataset_id,
                "status": "missing",
                "source_id": metadata[0],
                "requested_start_at": job.window_start_at,
                "requested_end_at": job.window_end_at,
                "actual_start_at": None,
                "actual_end_at": None,
                "row_count": 0,
                "unit": metadata[1],
                "frequency": metadata[2],
                "instrument": metadata[3],
                "venue": metadata[4],
                "contract_type": metadata[5],
                "publication_time_precision": metadata[6],
                "revision_policy": metadata[7],
                "partition_refs": [],
                "gaps": [error],
            }
        )


def _coverage_status(
    dataset_id: str,
    start: datetime,
    end: datetime,
    actual_start: datetime | None,
    actual_end: datetime | None,
    row_count: int,
) -> str:
    if actual_start is None or actual_end is None or row_count == 0:
        return "missing"
    if dataset_id in {"crypto.btc_usdt.ohlcv_1h", "crypto.btc_usdt.ohlcv_1d",
                      "crypto.eth_usdt.ohlcv_1d"}:
        interval_seconds = 24 * 3600 if dataset_id.endswith("ohlcv_1d") else 3600
        tolerance = timedelta(days=2) if dataset_id.endswith("ohlcv_1d") else timedelta(hours=2)
        expected = max(1, int((end - start).total_seconds() // interval_seconds))
        complete = (
            row_count >= expected * 0.98
            and actual_start <= start + tolerance
            and actual_end >= end - tolerance
        )
    elif dataset_id == "crypto.btc_usdt_swap.funding_rate":
        expected = max(1, int((end - start).total_seconds() // (8 * 3600)))
        complete = (
            row_count >= expected * 0.85
            and actual_start <= start + timedelta(hours=12)
            and actual_end >= end - timedelta(hours=12)
        )
    elif dataset_id.startswith("macro.fred"):
        complete = actual_start <= start + timedelta(days=7) and actual_end >= end - timedelta(
            days=7
        )
    else:
        complete = False
    return "complete" if complete else "partial"


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
