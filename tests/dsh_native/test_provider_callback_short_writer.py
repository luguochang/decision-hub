"""Actual current ISO callback, physical SQLite contention and owner/attempt CAS.

The existing fixtures create their own SQLite database. No Main database,
network, reduced source guard, substituted original reader or permission result.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import research_graph_binding
from packages.kernel.decision_hub_kernel.application.provider_callback_diagnostics import (
    ProviderCallbackDiagnostics,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    CallbackSourceLease,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRoleOutputRecord,
    UsageCollectionRecord,
)
from packages.kernel.decision_hub_kernel.persistence.operation_phases import OperationPhases
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    _ConsumerSourceGuard,
)
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import inputs as inputs
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows immutable HANDLE source lease"),
    pytest.mark.usefixtures("forbid_external_connections"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_actual_current_callback_short_writer_and_competing_cas(active, monkeypatch):
    prepared_inputs, payload = active
    database = prepared_inputs.database
    evidence = {"scope": "actual-current-ISO-source", "root": str(ROOT), "phases": []}
    leases = []
    held = threading.Event()
    attempting_begin = threading.Event()
    done = threading.Event()
    writer_active = False
    unsealed_checks = []
    contender = {}
    seal_events = []
    original_pin = CallbackSourceLease.pin
    original_seal = CallbackSourceLease.seal_authenticated_selection
    original_matches = research_graph_binding._ReadSourceGuard.matches_identity

    def observe_pin(lease, source):
        value = original_pin(lease, source)
        leases.append(lease)
        return value

    def observe_seal(lease, run_id):
        assert not writer_active, "full final selection seal ran while holding writer"
        value = original_seal(lease, run_id)
        seal_events.append(run_id)
        return value

    def observe_matches(source):
        if writer_active:
            unsealed_checks.append(str(source.root))
        return original_matches(source)

    monkeypatch.setattr(CallbackSourceLease, "pin", observe_pin)
    monkeypatch.setattr(CallbackSourceLease, "seal_authenticated_selection", observe_seal)
    monkeypatch.setattr(
        research_graph_binding._ReadSourceGuard, "matches_identity", observe_matches
    )
    now = datetime.now(UTC)
    with database.session() as session:
        session.add(
            UsageCollectionRecord(
                job_id="callback-contention-cas",
                run_id=payload.run_id,
                session_id=payload.root_session_id,
                generation=1,
                status="running",
                next_check_at=now,
                lease_owner="cleanup-owner",
                attempts=7,
                lease_until=now + timedelta(minutes=1),
                created_at=now,
            )
        )

    def compete():
        if not held.wait(180):
            contender["error"] = "callback_never_acquired_writer"
            done.set()
            return
        started = perf_counter()
        try:
            with sqlite3.connect(database.engine.url.database, timeout=5) as connection:
                connection.execute("PRAGMA busy_timeout=5000")
                contender["attempted_begin_at"] = perf_counter()
                attempting_begin.set()
                connection.execute("BEGIN IMMEDIATE")
                contender["wait_ms"] = (perf_counter() - started) * 1000
                contender["stale_owner_rows"] = connection.execute(
                    "UPDATE usage_collection_jobs SET status='failed' "
                    "WHERE job_id=? AND status='running' AND lease_owner=? AND attempts=?",
                    ("callback-contention-cas", "superseded-owner", 7),
                ).rowcount
                contender["stale_attempt_rows"] = connection.execute(
                    "UPDATE usage_collection_jobs SET status='failed' "
                    "WHERE job_id=? AND status='running' AND lease_owner=? AND attempts=?",
                    ("callback-contention-cas", "cleanup-owner", 6),
                ).rowcount
                contender["pre_valid_state"] = connection.execute(
                    "SELECT status,lease_owner,attempts FROM usage_collection_jobs WHERE job_id=?",
                    ("callback-contention-cas",),
                ).fetchone()
                contender["valid_owner_attempt_rows"] = connection.execute(
                    "UPDATE usage_collection_jobs SET status='retry',lease_owner=NULL "
                    "WHERE job_id=? AND status='running' AND lease_owner=? AND attempts=?",
                    ("callback-contention-cas", "cleanup-owner", 7),
                ).rowcount
            contender["committed"] = True
        except sqlite3.Error as error:
            contender.update(error=type(error).__name__, sqlite_errorcode=error.sqlite_errorcode)
        finally:
            contender["elapsed_ms"] = (perf_counter() - started) * 1000
            done.set()

    def observe_phase(observation):
        nonlocal writer_active
        evidence["phases"].append(asdict(observation))
        if observation.phase == "writer_lifetime" and observation.state == "acquired":
            writer_active = True
            lease = leases[-1]
            evidence["at_physical_acquire"] = {
                "sealed": lease._sealed,
                "defer_unselected": lease.defer_unselected_recipe_archives,
                "authenticated_run": lease._extended_run_id,
                "source_root": str(lease._source.root),
                "graph_files": len(lease._source.graph.files),
                "consumer_files": len(lease._source.files),
                "pinned_files": len(lease._files),
                "deferred_unselected_files": len(lease._deferred_recipe_archives),
                "hashed_bytes": lease.hashed_bytes,
                "hash_observations": lease.hash_observations,
                "selected_paths": sorted(lease._expected),
            }
            # Request mutation access only; never write, remove or rename source.
            path = (
                ROOT / "packages/kernel/decision_hub_kernel/application/provider_input_receipts.py"
            )
            try:
                with path.open("r+b"):
                    evidence["consumed_write_access_refused"] = False
            except PermissionError:
                evidence["consumed_write_access_refused"] = True
            api = lease.native.api
            handle = api.CreateFileW(str(path), 0x10000, 7, None, 3, 0, None)
            refused = handle in (None, ctypes.c_void_p(-1).value)
            if not refused:
                lease.native.close(handle)
            evidence["consumed_delete_rename_access_refused"] = refused
            held.set()
            evidence["competitor_attempted_before_callback_work_resumed"] = attempting_begin.wait(5)
        elif observation.phase == "writer_lifetime" and observation.state == "released":
            evidence["physical_release_at"] = perf_counter()
            writer_active = False

    thread = threading.Thread(target=compete, name="actual-callback-competing-cas", daemon=True)
    thread.start()
    diagnostics = ProviderCallbackDiagnostics(payload, "input", sink=lambda _: None)
    diagnostics.operation_phases = OperationPhases(observe_phase)
    service = ProviderInputReceiptService(database)
    try:
        first = service.record_input(payload, diagnostics=diagnostics)
        assert done.wait(10), "competing real SQLite writer did not finish"
        thread.join(timeout=1)
        evidence["contender"] = contender
        evidence["unsealed_checks_inside_writer"] = unsealed_checks
        evidence["seal_events"] = seal_events
        evidence["all_scope_handles_closed"] = all(not lease.active for lease in leases)
        evidence["reference"] = first.reference.model_dump(mode="json")
        with database.session() as session:
            row = session.get(RunRoleOutputRecord, first.reference.output_id)
            assert row is not None and row.payload_hash == first.reference.payload_hash
            evidence["original_sha256"] = row.payload_hash
            assert session.scalar(
                select(UsageCollectionRecord.status).where(
                    UsageCollectionRecord.job_id == "callback-contention-cas"
                )
            ) in {"retry", "running"}
        # Assert only after retaining the real old-path measurements for RED.
        assert evidence["at_physical_acquire"]["sealed"], evidence
        assert evidence["at_physical_acquire"]["defer_unselected"]
        assert evidence["at_physical_acquire"]["source_root"] == str(ROOT)
        assert not unsealed_checks
        released = [
            p
            for p in evidence["phases"]
            if p["phase"] == "writer_lifetime" and p["state"] == "released"
        ]
        assert len(released) == 1 and released[0]["elapsed_ms"] < 5000, released
        assert contender.get("committed") is True, contender
        assert evidence["competitor_attempted_before_callback_work_resumed"]
        assert contender["attempted_begin_at"] < evidence["physical_release_at"]
        assert contender["valid_owner_attempt_rows"] == 1
        assert contender["pre_valid_state"] == ("running", "cleanup-owner", 7)
        assert contender["stale_owner_rows"] == contender["stale_attempt_rows"] == 0
        assert evidence["consumed_write_access_refused"]
        assert evidence["consumed_delete_rename_access_refused"]
        assert evidence["all_scope_handles_closed"]
        assert service.record_input(payload) == first
        evidence["idempotent"] = True
        with pytest.raises((ValueError, PermissionError)):
            service.record_input(payload.model_copy(update={"generation": 2}))
        evidence["wrong_generation_rejected"] = True
        with database.session() as session:
            rows = list(
                session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.output_id == first.reference.output_id
                    )
                )
            )
            assert len(rows) == 1 and rows[0].payload_hash == first.reference.payload_hash
        evidence["all_scope_handles_closed"] = all(not lease.active for lease in leases)
        assert evidence["all_scope_handles_closed"]
    finally:
        evidence["contender"] = contender
        evidence["unsealed_checks_inside_writer"] = unsealed_checks
        evidence["seal_events"] = seal_events
        destination = Path(
            os.environ.get(
                "DSH_CALLBACK_GATE_EVIDENCE",
                str(ROOT / "artifacts/d2-provider-callback-short-writer/gate-evidence.json"),
            )
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def test_unresolved_component_archive_cannot_seal_owned_canary(tmp_path):
    """Supplement only: no component resolver or source-authority substitution.

    Construct just the unresolved state at the seal boundary on owned bytes.
    The separate full-current-closure callback above exercises real selection.
    """
    root = tmp_path.resolve()
    relative = "packages/kernel/market_component_versions/owned/release.json"
    component = root / relative
    component.parent.mkdir(parents=True)
    component.write_bytes(b'{"owned_component":"not_recipe_selected"}')
    graph = SimpleNamespace(
        root=root,
        files=((relative, hashlib.sha256(component.read_bytes()).hexdigest()),),
        matches_identity=lambda: True,
    )
    source = _ConsumerSourceGuard.capture(graph, root)
    lease = CallbackSourceLease(defer_unselected_recipe_archives=True)
    try:
        lease.pin(source)
        # Test-only boundary arrangement: do not claim this is recipe promotion.
        lease._extended_run_id = "owned-unresolved-component"
        with pytest.raises(ValueError, match="original_preparation_source_changed"):
            lease.seal_authenticated_selection("owned-unresolved-component")
        assert not lease._sealed
    finally:
        lease.close()
    component.write_bytes(b"owned cleanup proves handles released")
