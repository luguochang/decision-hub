"""Real small-pool reads of prepared and running research without saved results."""

from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

import pytest
import yaml
from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
)
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.query_basis_helpers import save_control_request
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_observability import (
    _trace,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ActiveCase:
    database: Database
    request: ResearchSessionRequest
    frozen_pack_root: Path
    query_pack_root: Path


def _case(tmp_path: Path, version: str, stage: str) -> ActiveCase:
    database, event_id, run_id = _admitted_run(tmp_path)
    root = PACK_ROOT
    refs = None
    if version == "v2":
        root = tmp_path / "prepared-pack"
        shutil.copytree(ROOT / "packs/crypto_macro_prepared/1.1.0", root)
        manifest = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
        refs = PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        )
    request = ResearchRequestFactory(
        database, pack_root=root, execution_mode="replay", clock=lambda: NOW,
        allowed_capabilities=("replay.research", "document.readback", "web.search"),
        prepared_policy_refs=refs,
    ).build(event_id, run_id)
    if version == "v1":
        ResearchTaskService(database, clock=lambda: NOW).freeze(request)
    if stage == "running":
        save_control_request(database, request, NOW, full_task=False)
        ResearchObservabilityService(database, clock=lambda: NOW).append_trace(
            _trace(run_id, event_type="model_step_started", summary="Research in progress")
        )
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "running" if stage == "running" else "admitted"
        run.updated_at = NOW
        assert session.get(ResearchResultRecord, run_id) is None
    url = database.engine.url
    database.engine.dispose()
    database.engine = create_engine(
        url, connect_args={"check_same_thread": False}, poolclass=QueuePool,
        pool_size=1, max_overflow=0, pool_timeout=1,
    )
    database.factory.configure(bind=database.engine)
    # Match the real API: query capabilities come from the traditional Pack,
    # while V2 request/Pack/task policy is read from its durable frozen bundle.
    # A prepared definition directory has no legacy tools/bindings.yaml.
    return ActiveCase(database, request, root, PACK_ROOT)


def _physical_rows(database: Database) -> dict[str, list[tuple[object, ...]]]:
    path = database.engine.url.database
    assert path is not None and path != ":memory:"
    with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as connection:
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return {
            name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in names
        }


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("stage", ["prepared", "running"])
def test_active_read_surfaces_use_one_connection_and_preserve_original_rows(
    tmp_path: Path, version: str, stage: str,
) -> None:
    case = _case(tmp_path, version, stage)
    database, request = case.database, case.request
    before = _physical_rows(database)
    query = ResearchQueryService(database, pack_root=case.query_pack_root)
    checkouts: list[int] = []
    task_reads: list[int] = []

    def checked_out(*_args: object) -> None:
        checkouts.append(1)

    def read_task(
        _connection: object, _cursor: object, statement: str,
        parameters: tuple[object, ...] | dict[str, object],
        _context: object, _many: bool,
    ) -> None:
        if (
            "FROM run_role_outputs" in statement
            and "run_role_outputs.role = ?" in statement
            and "research_task_request" in parameters
        ):
            task_reads.append(1)

    event.listen(database.engine, "checkout", checked_out)
    event.listen(database.engine, "before_cursor_execute", read_task)
    try:
        # Each public view acquires one connection. A helper's second owned
        # Session must time out with this real QueuePool; no Session is mocked.
        start = len(checkouts)
        detail = query.get(request.run_id)
        assert detail is not None and len(checkouts) == start + 1
        # Avoid spending the borrowed connection on the same complete-task
        # projection four times; verify actual SQL reads, not a mocked helper.
        assert len(task_reads) == 1
        assert detail.run.coverage_basis == "saved_task_request"
        assert detail.run.budget_basis == (
            "saved_request" if stage == "running" else "unavailable"
        )
        assert detail.reviews == []
        assert detail.horizons == [] and detail.causal_case is None
        assert detail.run.status == ("researching" if stage == "running" else "queued")

        start = len(checkouts)
        listed = query.list_page(limit=1)
        assert len(checkouts) == start + 1
        assert listed.total_count == 1 and listed.items[0] == detail.run

        start = len(checkouts)
        home = query.home()
        assert len(checkouts) == start + 1
        assert home.latest_run == detail.run and home.active_runs == [detail.run]
        assert home.latest_report is None and home.active_count == 1

        start = len(checkouts)
        inbox = query.inbox(limit=1)
        assert len(checkouts) == start + 1
        assert inbox.items[0].run_id == request.run_id
        assert not inbox.items[0].report_available

        start = len(checkouts)
        observe = query.observability_view(request.run_id)
        assert observe is not None and len(checkouts) == start + 1
        assert observe.coverage_basis == "saved_task_request"
        assert observe.versions.pack_basis == "saved_pack"
        assert observe.versions.domain_pack_version == ("1.1.0" if version == "v2" else "1.0.0")
        assert "gate_policy_ref" in (observe.versions.unverified_fields or [])

        start = len(checkouts)
        business = query.business_status(request.run_id)
        assert business is not None and len(checkouts) == start + 1
        assert _physical_rows(database) == before
        assert isinstance(database.engine.pool, QueuePool)
        assert database.engine.pool.checkedout() == 0
    finally:
        event.remove(database.engine, "checkout", checked_out)
        event.remove(database.engine, "before_cursor_execute", read_task)
        database.engine.dispose()


def test_mixed_active_reads_finish_with_concurrent_single_connection_pool(tmp_path: Path) -> None:
    case = _case(tmp_path, "v2", "running")
    database, request = case.database, case.request
    before = _physical_rows(database)
    query = ResearchQueryService(database, pack_root=case.query_pack_root)
    ready = Barrier(4)

    def read(surface: str) -> None:
        ready.wait(timeout=5)
        if surface == "detail":
            detail = query.get(request.run_id)
            assert detail is not None and detail.run.run_id == request.run_id
        elif surface == "list":
            assert query.list_page(limit=1).items[0].run_id == request.run_id
        elif surface == "home":
            assert query.home().active_runs[0].run_id == request.run_id
        else:
            assert query.inbox(limit=1).items[0].run_id == request.run_id

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(read, surface) for surface in (
                "detail", "list", "home", "inbox",
            )]
            for future in futures:
                future.result(timeout=10)
        assert _physical_rows(database) == before
        assert isinstance(database.engine.pool, QueuePool)
        assert database.engine.pool.checkedout() == 0
    finally:
        database.engine.dispose()


def test_detail_revalidates_original_bundle_on_every_public_read(tmp_path: Path) -> None:
    case = _case(tmp_path, "v2", "running")
    database, request = case.database, case.request
    query = ResearchQueryService(database, pack_root=case.query_pack_root)
    try:
        first = query.get(request.run_id)
        assert first is not None and first.run.coverage_basis == "saved_task_request"
        # Same service instance, new request: a previous valid projection must
        # not hide a now-corrupt original row behind an across-request cache.
        with database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=request.run_id, role="research_compiled_policy",
            ).one()
            row.payload_hash = "0" * 64
        before = _physical_rows(database)
        second = query.get(request.run_id)
        assert second is not None and second.run.coverage_basis == "invalid"
        assert second.run.coverage is None and second.run.budget_basis == "saved_request"
        assert second.reviews == [] and second.horizons == []
        assert _physical_rows(database) == before
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("version,damage", [
    ("v1", "task_hash"), ("v1", "pack_hash"),
    ("v2", "task_hash"), ("v2", "pack_hash"),
    ("v2", "missing_task"), ("v2", "missing_compiled"),
    ("v2", "trigger_changed"), ("v2", "trigger_json"),
    ("v2", "trigger_null"), ("v2", "trigger_manifest"),
])
def test_small_pool_rejects_corrupt_original_receipts_without_promoting_coverage(
    tmp_path: Path, version: str, damage: str,
) -> None:
    case = _case(tmp_path, version, "running")
    database, request = case.database, case.request
    with database.session() as session:
        if damage.startswith("trigger_"):
            run = session.get(RunRecord, request.run_id)
            assert run is not None and run.snapshot_id is not None
            snapshot = session.get(SnapshotRecord, run.snapshot_id)
            assert snapshot is not None
            if damage == "trigger_manifest":
                snapshot.generation = 0
            else:
                snapshot.evidence_json = {
                    "trigger_changed": json.dumps([]),
                    "trigger_json": "{",
                    "trigger_null": "null",
                }[damage]
        else:
            role = "research_request_pack" if damage == "pack_hash" else (
                "research_compiled_policy" if damage == "missing_compiled"
                else "research_task_request"
            )
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=request.run_id, role=role,
            ).one()
            if damage.startswith("missing_"):
                session.delete(row)
            else:
                row.payload_hash = "0" * 64
    before = _physical_rows(database)
    query = ResearchQueryService(database, pack_root=case.query_pack_root)
    try:
        detail = query.get(request.run_id)
        assert detail is not None and detail.run.budget_basis == "saved_request"
        if damage.startswith("trigger_"):
            assert detail.trigger_snapshot is None
        if damage != "pack_hash" or version == "v2":
            assert detail.run.coverage_basis == "invalid" and detail.run.coverage is None
            assert query.list_page(limit=1).items[0].coverage_basis == "invalid"
        observe = query.observability_view(request.run_id)
        assert observe is not None
        if version == "v2" or damage == "pack_hash":
            assert observe.versions.pack_basis == "invalid"
            assert observe.versions.domain_pack_version is None
        # A separately attested prompt retains its saved request budget even
        # when the complete task or prepared bundle cannot be revalidated.
        assert observe.versions.request_basis == "saved_request"
        assert detail.reviews == [] and detail.horizons == []
        assert _physical_rows(database) == before
    finally:
        database.engine.dispose()
