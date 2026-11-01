"""First HM04-B behavior red against the actual Factory and isolated SQLite."""

import json
import socket
from pathlib import Path

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.research_pack import ROLE as PACK_ROLE
from packages.kernel.decision_hub_kernel.application.research_task import ROLE as TASK_ROLE
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
)
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run

# Proposed HM04-B receipt role. No production writer for this role exists yet.
COMPILED_ROLE = "research_compiled_policy"
BUNDLE_ROLES = (PACK_ROLE, TASK_ROLE, COMPILED_ROLE)


@pytest.fixture(autouse=True)
def forbid_external_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("HM04-B01 must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


class InjectedSnapshotFreezeFault(RuntimeError):
    """Controlled exception after the original Pack freeze has returned."""


def _state(database: Database, run_id: str) -> dict[str, object]:
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        study = session.get(StudyRecord, run.study_id)
        assert study is not None
        rows = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id)
            .order_by(RunRoleOutputRecord.role)
            .all()
        )
        return {
            "run_id": run.run_id,
            "event_id": run.event_id,
            "study_horizons": study.horizon_scope,
            "snapshot_id": run.snapshot_id,
            "snapshot_count": session.query(SnapshotRecord).filter_by(run_id=run_id).count(),
            "bundle_counts": {role: sum(row.role == role for row in rows) for role in BUNDLE_ROLES},
            "rows": [
                {
                    "role": row.role,
                    "output_id": row.output_id,
                    "schema_version": row.schema_version,
                    "payload_hash": row.payload_hash,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
        }


def _observe(tmp_path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    (tmp_path / "observation.json").write_text(encoded, encoding="utf-8")
    print(f"HM04_B01_OBSERVATION {encoded}")


def test_factory_snapshot_fault_rolls_back_entire_frozen_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        initial = _state(database, run_id)
        assert initial["study_horizons"] == "30m,24h,72h"
        assert initial["bundle_counts"] == dict.fromkeys(BUNDLE_ROLES, 0)
        factory = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW)
        at_fault: dict[str, object] = {}

        def fail_after_pack(
            _service: SnapshotService, selected_run: str, selected_event: str
        ) -> tuple[str, list[str]]:
            assert (selected_run, selected_event) == (run_id, event_id)
            at_fault.update(_state(database, run_id))
            raise InjectedSnapshotFreezeFault("hm04_b01_injected_trigger_freeze_failure")

        monkeypatch.setattr(SnapshotService, "freeze_for_run", fail_after_pack)
        with pytest.raises(InjectedSnapshotFreezeFault) as caught:
            factory.build(event_id, run_id)
        after = _state(database, run_id)
        _observe(tmp_path, {
            "case": "snapshot_fault_after_original_pack_freeze",
            "exception_type": type(caught.value).__name__,
            "exception_message": str(caught.value),
            "initial": initial,
            "at_injected_fault": at_fault,
            "after_factory_failed": after,
            "expected_bundle_counts_after_failure": dict.fromkeys(BUNDLE_ROLES, 0),
        })
        assert at_fault, "The injected Factory boundary must actually be reached."
        assert after["bundle_counts"] == dict.fromkeys(BUNDLE_ROLES, 0), (
            "Failed Factory preparation must not leave a separately committed Pack, "
            "Task or compiled receipt; observed real SQLite rows are preserved above."
        )
    finally:
        database.engine.dispose()


def test_original_factory_returns_request_with_pack_but_without_task_freeze(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        initial = _state(database, run_id)
        assert initial["study_horizons"] == "30m,24h,72h"
        factory = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW)
        request = factory.build(event_id, run_id)
        after = _state(database, run_id)
        _observe(tmp_path, {
            "case": "original_factory_positive_control",
            "initial": initial,
            "after_factory_returned": after,
            "request": {
                "schema_version": request.schema_version,
                "run_id": request.run_id,
                "event_id": request.event_id,
                "trigger_snapshot_id": request.trigger_snapshot_id,
                "domain_pack_ref": request.domain_pack_ref,
                "requirement_ids": [item.requirement_id for item in request.evidence_requirements],
            },
            "executor_or_host_called": False,
        })
        assert (request.run_id, request.event_id) == (run_id, event_id)
        assert request.trigger_snapshot_id == after["snapshot_id"]
        assert after["snapshot_count"] == 1
        assert after["bundle_counts"] == {PACK_ROLE: 1, TASK_ROLE: 0, COMPILED_ROLE: 0}
        assert factory.packs.get(run_id) is not None
        assert ResearchTaskService(database).get(run_id) is None
    finally:
        database.engine.dispose()
