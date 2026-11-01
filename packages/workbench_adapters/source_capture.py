from pathlib import Path

from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)


def source_capture_context(database, data_root: Path):
    archive = RawBodyArchive(data_root / "raw-captures")
    store = FactLineageStore(database)

    def context(attempt_id, manifest):
        return capture_scope(
            CaptureBatch(
                context_id=f"source:{attempt_id}",
                adapter_version=manifest.version,
                archive=archive,
                persist=store.record_capture,
            )
        )

    return context


def observation_capture_context(database, data_root: Path):
    archive = RawBodyArchive(data_root / "raw-captures")
    store = FactLineageStore(database)

    def context(attempt_id):
        return capture_scope(
            CaptureBatch(
                context_id=f"analytical-observation:{attempt_id}",
                adapter_version="okx-close-1m.v1",
                archive=archive,
                persist=store.record_capture,
            )
        )

    return context
