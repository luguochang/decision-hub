"""Actual Selection3/Method3 cancellation recovery; only Host transport is controlled.

PreparedInputs is a data carrier for the real Factory fixture. Its Method2
prepare() helper is deliberately unused. Every observation contains all physical
SQLite tables, including internal tables, with typed cells and row hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshSessionAccepted,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    ResearchRunCommand,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
    ResearchExecutionMethodReceiptV3,
    ResearchExecutionRequestReceiptV3,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
    FrozenContextSelection,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, RunRoleOutputRecord
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector
from packages.runtime_adapters.dsh_runtime.cancelled_recovery import CancelledRunReconciler
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    prepare_runtime_turn_in_session,
    read_runtime_task_anchor,
)
from packages.runtime_adapters.dsh_runtime.web_host_client import DshRecoveryBinding
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import inputs as inputs
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
WORKER = "selected-cancellation-test-worker"
COMMAND_OWNER = "selected-cancellation-test-owner"
ACCOUNTED_COST = 0.37


class PhysicalTable(TypedDict):
    columns: list[str]
    rows: list[list[list[str]]]
    row_count: int
    rows_sha256: str


class PhysicalSnapshot(TypedDict):
    schema: list[list[list[str]]]
    tables: dict[str, PhysicalTable]
    sha256: str


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _cell(value: object) -> list[str]:
    if value is None:
        return ["null"]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    raise TypeError(f"unsupported SQLite physical value: {type(value).__name__}")


def _path(database: Database) -> str:
    path = database.engine.url.database
    assert path is not None and path != ":memory:"
    return path


def _physical_snapshot(database: Database) -> PhysicalSnapshot:
    """One physical read snapshot; retain all rows/types, including duplicates."""
    with sqlite3.connect(_path(database)) as connection:
        connection.execute("BEGIN")
        schema = [
            [_cell(value) for value in row]
            for row in connection.execute(
                "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_schema "
                "ORDER BY type, name"
            )
        ]
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
            )
        ]
        tables: dict[str, PhysicalTable] = {}
        for name in names:
            assert isinstance(name, str)
            cursor = connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            assert cursor.description is not None
            columns = [column[0] for column in cursor.description]
            rows = [[_cell(value) for value in row] for row in cursor]
            rows.sort(key=_canonical)
            tables[name] = {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "rows_sha256": _hash([columns, rows]),
            }
    return {"schema": schema, "tables": tables, "sha256": _hash([schema, tables])}


class SelectedCancellationHost:
    """Controlled accepted existing session; no Provider, HTTP, or new work."""

    def __init__(self) -> None:
        self.submit_calls: list[DshSessionSubmit] = []
        self.recovery_calls: list[tuple[str, DshRecoveryBinding]] = []
        self.state: Literal["running", "cancelled"] = "running"
        self.closed = False

    async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
        self.submit_calls.append(payload)
        assert len(self.submit_calls) == 1, "Recovery resubmitted the original session"
        return DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id,
            generation=payload.generation,
            accepted_at=datetime.now(UTC),
        )

    def _status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        assert len(self.submit_calls) == 1
        original = self.submit_calls[0]
        assert binding.run_id == original.run_id
        assert binding.dsh_session_id == original.deterministic_session_id
        assert binding.generation == original.generation
        assert binding.request_hash == original.request_hash
        assert binding.deadline_at == original.deadline_at
        assert binding.max_tool_calls == original.max_tool_calls
        assert binding.model_step_timeout_ms == original.model_step_timeout_ms
        return DshSessionStatus.model_validate(
            {
                "schema_version": "dsh-session-status.v1",
                "run_id": binding.run_id,
                "dsh_session_id": binding.dsh_session_id,
                "generation": binding.generation,
                "state": self.state,
                "last_seq": 4 if self.state == "cancelled" else 3,
                "observed_at": datetime.now(UTC),
                "error_code": None,
            }
        )

    async def recovery_status(self, binding: DshRecoveryBinding) -> DshSessionStatus:
        self.recovery_calls.append(("status", binding))
        return self._status(binding)

    async def recovery_cancel(
        self, binding: DshRecoveryBinding, reason: str,
    ) -> DshSessionStatus:
        self.recovery_calls.append(("cancel", binding))
        assert reason == "product_run_cancelled"
        self.state = "cancelled"
        return self._status(binding)

    async def recovery_result(self, binding: DshRecoveryBinding) -> DshSessionResult:
        self.recovery_calls.append(("result", binding))
        raise AssertionError("Cancelled original session must not request a result")

    async def close(self) -> None:
        self.closed = True


def _damage_selection(
    database: Database,
    selected: FrozenContextSelection,
    damage: Literal["deleted", "recorded_time"],
) -> dict[str, object]:
    """A distinct committed physical connection damages one real original row."""
    with sqlite3.connect(_path(database)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM run_role_outputs WHERE output_id=?", (selected.reference.output_id,)
        ).fetchone()
        assert row is not None and row["payload_json"] == selected.payload_json
        original = dict(row)
        if damage == "deleted":
            affected = connection.execute(
                "DELETE FROM run_role_outputs WHERE output_id=?", (selected.reference.output_id,)
            ).rowcount
            changed = None
        else:
            recorded = datetime.fromisoformat(row["created_at"])
            drifted = (recorded + timedelta(microseconds=1)).isoformat(" ")
            affected = connection.execute(
                "UPDATE run_role_outputs SET created_at=? WHERE output_id=?",
                (drifted, selected.reference.output_id),
            ).rowcount
            changed_row = connection.execute(
                "SELECT * FROM run_role_outputs WHERE output_id=?",
                (selected.reference.output_id,),
            ).fetchone()
            assert changed_row is not None
            changed = dict(changed_row)
            assert changed["payload_json"] == original["payload_json"]
            assert changed["payload_hash"] == original["payload_hash"]
        assert affected == 1
        connection.commit()
    return {"kind": damage, "original": original, "changed": changed, "rows_affected": affected}


def _assert_selection_damage_only(
    before: PhysicalSnapshot, after: PhysicalSnapshot, selection_id: str,
) -> None:
    assert before["schema"] == after["schema"]
    assert before["tables"].keys() == after["tables"].keys()
    for table in before["tables"]:
        if table != "run_role_outputs":
            assert before["tables"][table] == after["tables"][table], table
    previous, current = before["tables"]["run_role_outputs"], after["tables"]["run_role_outputs"]
    assert previous["columns"] == current["columns"]
    index = previous["columns"].index("output_id")
    original = [row for row in previous["rows"] if row[index] != ["text", selection_id]]
    retained = [row for row in current["rows"] if row[index] != ["text", selection_id]]
    assert original == retained


def _assert_only_transport_projection(before: PhysicalSnapshot, after: PhysicalSnapshot) -> None:
    assert before["schema"] == after["schema"]
    assert before["tables"].keys() == after["tables"].keys()
    for table in before["tables"]:
        if table != "dsh_session_links":
            assert before["tables"][table] == after["tables"][table], table
    previous, current = before["tables"]["dsh_session_links"], after["tables"]["dsh_session_links"]
    assert previous["columns"] == current["columns"]
    assert previous["row_count"] == current["row_count"] == 1
    mutable = {
        "state", "last_seq", "last_seen_at", "terminal_at", "trace_ref",
        "result_ref", "result_hash", "error_code", "updated_at",
    }
    for index, column in enumerate(previous["columns"]):
        if column not in mutable:
            assert previous["rows"][0][index] == current["rows"][0][index], column


async def _close_projector(projector: DshCancellationProjector) -> None:
    try:
        await projector.close()
    finally:
        # Join the actual test-owned executor even if a bounded read returned
        # pending. Public close stops admission but intentionally does not join.
        executor = getattr(projector, "_executor")
        assert isinstance(executor, ThreadPoolExecutor)
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["legal", "deleted", "recorded_time"])
async def test_selected_method3_cancel_recovers_only_original_complete_binding(
    inputs: PreparedInputs,
    tmp_path: Path,
    damage: Literal["legal", "deleted", "recorded_time"],
) -> None:
    database, run_id = inputs.database, inputs.request.run_id
    host = SelectedCancellationHost()
    links = DshSessionLinkService(database)
    projector = DshCancellationProjector(links)
    snapshots: dict[str, PhysicalSnapshot] = {"factory": _physical_snapshot(database)}
    observation: dict[str, object] = {
        "case": damage, "fixture_accounted_cost": ACCOUNTED_COST, "snapshots": snapshots,
        "producer": "ContextSelectionService -> prepare_runtime_turn_in_session with selected refs",
    }
    try:
        assert RunService(database).claim_for_execution(
            run_id, worker_id=WORKER, lease_seconds=300, strategy_version="research.v1"
        )
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None and run.status == "running" and run.lease_owner == WORKER
            run.cost_usd = ACCOUNTED_COST
        snapshots["claimed"] = _physical_snapshot(database)
        selected = ContextSelectionService(database).select(
            run_id=run_id, graph_round=1, expected_lease_owner=WORKER
        )
        assert selected.receipt.schema_version == "research-context-selection.v3"
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            anchor = read_runtime_task_anchor(session, run_id)
            turn = prepare_runtime_turn_in_session(
                session, database, anchor, selected.request, inputs.upstream,
                runtime_id="dsh",
                runtime_version=(
                    f"dsh-web-{inputs.upstream.source_version}"
                    f"+{inputs.upstream.plugin_build_hash[:12]}"
                ),
                profile_ref="decision-research.web.v1",
                prompt_policy_hash=PROMPT_POLICY_HASH,
                selection_reference=selected.reference,
                expected_lease_owner=WORKER,
            )
        prepared = turn.prepared
        assert isinstance(prepared.method, ResearchExecutionMethodReceiptV3)
        assert isinstance(prepared.execution, ResearchExecutionRequestReceiptV3)
        with database.session() as session:
            actual = read_prepared_execution_in_session(session, run_id)
            assert actual.method == prepared.method and actual.execution == prepared.execution
            roles = {
                row.role: row
                for row in session.query(RunRoleOutputRecord).filter_by(run_id=run_id).all()
            }
            assert roles["research_execution_method"].schema_version == "research-execution-method.v3"
            assert roles["dsh_execution_request:1"].schema_version == "dsh-execution-request.v3"
            assert roles["context_execution_link:1"].schema_version == (
                "research-context-selection-execution-link.v2"
            )
            assert roles[selected.reference.role].payload_json == selected.payload_json
        accepted = await host.submit(turn.submit)
        original_link = links.accepted(accepted)
        assert original_link.accepted_at == accepted.accepted_at
        assert original_link.state not in {"completed", "failed", "cancelled"}
        assert original_link.deadline_at == prepared.method.definition.deadline_at
        assert original_link.generation == 1
        snapshots["prepared_and_accepted"] = _physical_snapshot(database)
        observation["selected_reference"] = selected.reference.model_dump(mode="json")
        observation["original_selection_payload_json"] = selected.payload_json
        observation["method"] = prepared.method.model_dump(mode="json")
        observation["execution"] = prepared.execution.model_dump(mode="json")
        observation["accepted_link"] = original_link.model_dump(mode="json")
        if damage != "legal":
            observation["mutation"] = _damage_selection(database, selected, damage)
        snapshots["after_mutation"] = _physical_snapshot(database)
        _assert_selection_damage_only(
            snapshots["prepared_and_accepted"], snapshots["after_mutation"],
            selected.reference.output_id,
        )
        command = ResearchRunCommand(
            schema_version="research-run-command.v1", request_id=f"selected-cancel:{damage}",
            command="cancel", reason="Stop the original selected research session",
        )
        commands = ResearchCommandService(database)
        cancelled = commands.apply(run_id, command, owner_id=COMMAND_OWNER)
        assert cancelled.status == "accepted" and cancelled.target_run_id is None
        observation["cancel_result"] = cancelled.model_dump(mode="json")
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None and run.status == "cancelled"
            assert run.lease_owner is None and run.lease_expires_at is None
            assert run.cost_usd == ACCOUNTED_COST and run.artifact_id is None
            if damage == "legal":
                actual = read_prepared_execution_in_session(session, run_id)
                assert actual.method == prepared.method and actual.execution == prepared.execution
            else:
                with pytest.raises(ValueError, match="^context_selection_original_row_changed$"):
                    read_prepared_execution_in_session(session, run_id)
                observation["central_proof_error"] = "context_selection_original_row_changed"
        snapshots["cancelled_before_recovery"] = _physical_snapshot(database)
        before_recovery = snapshots["cancelled_before_recovery"]
        for table in ("run_role_outputs", "dsh_session_prompts", "dsh_session_links"):
            assert before_recovery["tables"][table] == snapshots["after_mutation"]["tables"][table]
        reconciler = CancelledRunReconciler(projector, host)
        counts = await reconciler.tick(time_budget_seconds=30)
        observation["recovery_counts"] = counts
        snapshots["after_recovery"] = _physical_snapshot(database)
        final_link = links.get(run_id)
        assert final_link is not None
        observation["final_link"] = final_link.model_dump(mode="json")
        assert len(host.submit_calls) == 1 and host.submit_calls[0] == turn.submit
        if damage == "legal":
            assert counts["scanned"] == counts["attempted"] == counts["confirmed"] == 1
            assert counts["pending"] == counts["skipped"] == 0 and counts["limitations"] == {}
            assert [name for name, _binding in host.recovery_calls] == ["status", "cancel"]
            assert final_link.state == "cancelled" and final_link.generation == 1
            assert final_link.dsh_session_id == accepted.dsh_session_id
            assert final_link.request_hash == prepared.method.request_hash
            assert final_link.deadline_at == original_link.deadline_at
            assert final_link.result_ref is None and final_link.result_hash is None
            _assert_only_transport_projection(before_recovery, snapshots["after_recovery"])
            for _name, binding in host.recovery_calls:
                assert binding.task_request_hash == prepared.execution.task_request_hash
                assert binding.method_hash == prepared.method.method_hash
                assert binding.prompt_hash == prepared.integrity.prompt_hash
                assert binding.first_prompt_hash == prepared.integrity.prompt_hash
                assert binding.execution_request_hash == prepared.execution.request_hash
                assert binding.first_execution_request_hash == (
                    prepared.method.definition.policy_binding.first_execution_request_hash
                )
            with database.session() as session:
                actual = read_prepared_execution_in_session(session, run_id)
                assert actual.method == prepared.method and actual.execution == prepared.execution
            replay = commands.apply(run_id, command, owner_id=COMMAND_OWNER)
            assert replay.status == "already_applied"
            observation["cancel_replay"] = replay.model_dump(mode="json")
            terminal_counts = await reconciler.tick(time_budget_seconds=30)
            observation["terminal_tick"] = terminal_counts
            assert terminal_counts["scanned"] == 0
            snapshots["after_replay_and_terminal_tick"] = _physical_snapshot(database)
            assert snapshots["after_replay_and_terminal_tick"] == snapshots["after_recovery"]
            assert len(host.submit_calls) == 1
            assert [name for name, _binding in host.recovery_calls] == ["status", "cancel"]
        else:
            assert counts["scanned"] == counts["skipped"] == 1
            assert counts["confirmed"] == counts["attempted"] == counts["pending"] == 0
            assert counts["limitations"] == {"context_selection_original_row_changed": 1}
            assert host.recovery_calls == []
            assert final_link == original_link
            assert snapshots["after_recovery"] == before_recovery
    except BaseException as exc:
        observation["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        try:
            await _close_projector(projector)
        finally:
            await host.close()
            snapshots["final_after_actual_executor_join"] = _physical_snapshot(database)
            observation["submit_calls"] = [value.model_dump(mode="json") for value in host.submit_calls]
            observation["recovery_calls"] = [
                {"phase": name, "binding": asdict(binding)} for name, binding in host.recovery_calls
            ]
            observation["host_closed"] = host.closed
            (tmp_path / "selected-cancel-observation.json").write_text(
                json.dumps(observation, ensure_ascii=False, sort_keys=True, indent=2, default=str)
                + "\n",
                encoding="utf-8",
            )
    assert snapshots["final_after_actual_executor_join"] == snapshots.get(
        "after_replay_and_terminal_tick", snapshots["after_recovery"]
    )
