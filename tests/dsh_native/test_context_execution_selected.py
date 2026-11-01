"""Prepared T2 consumer witnesses; run only after the assigned heavy window.

The initial stage deliberately calls the current public execute_observed entry.
It does not claim this entry already promises a persisted Selection barrier.
Factory, B1, T1, SQLite and the transport/result mapper are actual implementations;
only the Host transport is controlled, with no Provider, HTTP or model call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionStatus,
    DshSessionSubmit,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application import (
    research_execution_binding as preparation,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import BUNDLE_ROLES
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
    FrozenContextSelection,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedResearchResult
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.runtime_adapters.dsh_runtime import research_runtime_binding as runtime_binding
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    _active,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


class HeldSelectionHost(PreparedHost):
    """Hold the real readiness await; count all physical status calls."""

    def __init__(self, fixture: PreparedInputs) -> None:
        super().__init__(fixture)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.status_calls: list[DshSessionSubmit] = []
        self.closed = False

    async def readiness(self) -> DshHostReadiness:
        value = await super().readiness()
        self.entered.set()
        await self.release.wait()
        return value

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        self.status_calls.append(payload)
        return await super().status(payload)

    async def close(self) -> None:
        self.closed = True
        await super().close()


def _database_path(fixture: PreparedInputs) -> str:
    path = fixture.database.engine.url.database
    assert path is not None and path != ":memory:"
    return path


def _full_sql(fixture: PreparedInputs) -> dict[str, list[dict[str, Any]]]:
    """All original rows of this isolated file, not a projected subset."""
    return _full_database_sql(fixture.database)


def _full_database_sql(database: Database) -> dict[str, list[dict[str, Any]]]:
    path = database.engine.url.database
    assert path is not None and path != ":memory:"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    'SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid'
                )
            ]
            for table in tables
        }


def _damage_selection(
    fixture: PreparedInputs,
    frozen: FrozenContextSelection,
    damage: Literal["deleted", "resigned"],
) -> dict[str, Any]:
    """A different real SQLite connection commits while readiness is held.

    The re-signed case changes original recorded time by one integer microsecond,
    then uses the actual canonical recipe for both internal and raw hashes.
    Neither the expected original reference nor B1/Task is replaced.
    """
    with sqlite3.connect(_database_path(fixture)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM run_role_outputs WHERE output_id = ?",
            (frozen.reference.output_id,),
        ).fetchone()
        assert row is not None
        assert row["payload_json"] == frozen.payload_json
        original = dict(row)
        if damage == "deleted":
            affected = connection.execute(
                "DELETE FROM run_role_outputs WHERE output_id = ?",
                (frozen.reference.output_id,),
            ).rowcount
            changed = None
        else:
            body = json.loads(row["payload_json"])
            recorded = datetime.fromisoformat(body["recorded_at"].replace("Z", "+00:00"))
            assert recorded.utcoffset() is not None
            recorded = recorded.astimezone(UTC) + timedelta(microseconds=1)
            # Match the actual producer's typed UTC JSON normalization (Z),
            # then selfhash. Raw +00:00 hashing would not be a valid re-sign.
            body = frozen.receipt.model_copy(update={"recorded_at": recorded}).model_dump(
                mode="json"
            )
            body["selection_payload_hash"] = recipe_hash(
                {key: value for key, value in body.items() if key != "selection_payload_hash"}
            )
            payload = canonical_recipe(body)
            raw_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            affected = connection.execute(
                "UPDATE run_role_outputs SET payload_json = ?, payload_hash = ?, "
                "created_at = ? WHERE output_id = ?",
                (
                    payload,
                    raw_hash,
                    recorded.replace(tzinfo=None).isoformat(" "),
                    frozen.reference.output_id,
                ),
            ).rowcount
            changed = dict(
                connection.execute(
                    "SELECT * FROM run_role_outputs WHERE output_id = ?",
                    (frozen.reference.output_id,),
                ).fetchone()
            )
        assert affected == 1
        connection.commit()
    return {"kind": damage, "original": original, "changed": changed, "affected": affected}


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["legal", "deleted", "resigned"])
async def test_current_public_runtime_consumes_original_selection_before_submit(
    inputs: PreparedInputs,
    tmp_path: Path,
    damage: Literal["legal", "deleted", "resigned"],
) -> None:
    """First three gates: legal control plus two genuine consumer discontinuities."""
    _active(inputs)
    frozen = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id,
        graph_round=1,
        expected_lease_owner=OWNER,
    )
    before = _full_sql(inputs)
    host = HeldSelectionHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    returned: ObservedResearchResult | None = None
    error: BaseException | None = None
    mutation: dict[str, Any] | None = None
    task = asyncio.create_task(
        runtime.execute_selected_observed(
            frozen.request,
            frozen.reference,
            expected_lease_owner=OWNER,
        )
    )
    after: dict[str, list[dict[str, Any]]] | None = None
    try:
        # Harness wait only; the real caller deadline and DB proof budgets remain.
        entered = asyncio.create_task(host.entered.wait())
        completed, _pending = await asyncio.wait(
            (entered, task),
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in completed:
            await task
        if entered not in completed:
            entered.cancel()
            await asyncio.gather(entered, return_exceptions=True)
            raise TimeoutError("Host readiness was not reached")
        assert not host.submit_calls
        if damage != "legal":
            mutation = _damage_selection(inputs, frozen, damage)
        host.release.set()
        try:
            returned = await asyncio.wait_for(task, timeout=120)
        except Exception as exc:
            error = exc
    except BaseException as exc:
        error = exc
        raise
    finally:
        host.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        # close deliberately does not terminate a timed-out real worker thread.
        # Join the test-owned executor before disposing this fixture's Database.
        runtime.cancellation_projector._executor.shutdown(  # pyright: ignore[reportPrivateUsage]
            wait=True, cancel_futures=True
        )
        after = _full_sql(inputs)
        observation = {
            "phase": "explicit selected public entry, required original Selection",
            "damage": damage,
            "expected_original_selection": frozen.reference.model_dump(mode="json"),
            "original_selection_payload_json": frozen.payload_json,
            "selected_request": frozen.request.model_dump(mode="json"),
            "before": before,
            "mutation": mutation,
            "after": after,
            "error": None
            if error is None
            else {
                "type": type(error).__name__,
                "message": str(error),
                "error_code": getattr(error, "error_code", None),
                "cause_code": getattr(error, "cause_code", None),
                "cause": repr(error.__cause__),
                "notes": getattr(error, "__notes__", []),
            },
            "readiness_calls": host.readiness_calls,
            "submit_calls": [item.model_dump(mode="json") for item in host.submit_calls],
            "status_calls": [item.model_dump(mode="json") for item in host.status_calls],
            "result_calls": host.result_calls,
            "cancel_calls": host.cancelled,
            "returned": None if returned is None else returned.model_dump(mode="json"),
            "host_closed": host.closed,
            "remaining_test_lane_threads": [
                thread.name
                for thread in threading.enumerate()
                if thread.name.startswith("dsh-cancel-db")
            ],
        }
        (tmp_path / "t2-selection-consumer-observation.json").write_text(
            json.dumps(observation, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    assert after is not None and host.closed
    assert before["runs"][0]["run_id"] == inputs.request.run_id
    original_bundle = [row for row in before["run_role_outputs"] if row["role"] in BUNDLE_ROLES]
    current_bundle = [row for row in after["run_role_outputs"] if row["role"] in BUNDLE_ROLES]
    assert original_bundle == current_bundle
    if damage == "legal":
        assert error is None
        assert returned is not None and returned.result.status == "degraded"
        assert len(host.submit_calls) == 1 and host.result_calls == 1
        assert before["run_role_outputs"][0:] != after["run_role_outputs"][0:]
    else:
        # These assertions are the actual red witness on the current consumer.
        assert host.submit_calls == [], "Host submitted after original Selection drift"
        assert host.status_calls == [] and host.result_calls == 0
        assert returned is None and error is not None
        assert before["runs"] == after["runs"]
        assert before["dsh_session_links"] == after["dsh_session_links"]
        assert before["dsh_session_prompts"] == after["dsh_session_prompts"]


@pytest.mark.asyncio
async def test_actual_graph_commits_selection_then_selected_execution(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    """Actual Graph -> T1 original -> public selected Runtime -> native final ref."""
    _active(inputs)
    before = _full_sql(inputs)
    host = HeldSelectionHost(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database),
        host,
        poll_interval_seconds=0.001,
    )
    state = None
    error = None
    try:
        state = await LangGraphResearchExecutor(
            inputs.database,
            runtime,
            expected_lease_owner=OWNER,
        ).execute(inputs.request, require_context_selection=True)
    except BaseException as exc:
        error = exc
        raise
    finally:
        await runtime.close()
        runtime.cancellation_projector._executor.shutdown(  # pyright: ignore[reportPrivateUsage]
            wait=True,
            cancel_futures=True,
        )
        after = _full_sql(inputs)
        (tmp_path / "t2-graph-selected-observation.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": after,
                    "state": state,
                    "error": None
                    if error is None
                    else {
                        "type": type(error).__name__,
                        "message": str(error),
                        "cause_code": getattr(error, "cause_code", None),
                        "cause": repr(error.__cause__),
                    },
                    "submit": [item.model_dump(mode="json") for item in host.submit_calls],
                    "status": [item.model_dump(mode="json") for item in host.status_calls],
                    "result_calls": host.result_calls,
                },
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    assert state is not None and state.get("graph_final_projection_ref")
    assert len(host.submit_calls) == 1 and host.result_calls == 1
    rows = {row["role"]: row for row in after["run_role_outputs"]}
    assert rows["research_execution_method"]["schema_version"] == "research-execution-method.v3"
    assert rows["dsh_execution_request:1"]["schema_version"] == "dsh-execution-request.v3"
    assert (
        rows["context_execution_link:1"]["schema_version"]
        == "research-context-selection-execution-link.v2"
    )
    assert rows["context_select.r1.a1"]["schema_version"] == "research-context-selection.v3"
    final = ResearchSessionResult.model_validate_json(json.dumps(state["final_result"]))
    assert final.status == "degraded"


async def _close_lane(runtime: DshWebResearchRuntime) -> None:
    await runtime.close()
    runtime.cancellation_projector._executor.shutdown(  # pyright: ignore[reportPrivateUsage]
        wait=True, cancel_futures=True
    )


def _observe_case(path: Path, body: dict[str, Any]) -> None:
    (path / "t2-observation.json").write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seam", ["method", "link", "prompt", "execution", "integrity", "selection_link", "last_fence"]
)
async def test_selected_preparation_real_flush_rollback_and_last_owner_fence(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    _active(inputs)
    frozen = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id,
        graph_round=1,
        expected_lease_owner=OWNER,
    )
    before = _full_sql(inputs)
    reached: list[str] = []
    original_flush = preparation._flush_stage  # pyright: ignore[reportPrivateUsage]

    def flush(session: Session, stage: str) -> None:
        original_flush(session, stage)
        reached.append(stage)
        if stage == seam:
            raise OSError("controlled after actual SQL flush: " + seam)
        if seam == "last_fence" and stage == "selection_link":
            run = session.get(RunRecord, inputs.request.run_id, populate_existing=True)
            assert run is not None
            run.lease_owner = "controlled replacement owner"
            session.flush()

    monkeypatch.setattr(preparation, "_flush_stage", flush)
    host = HeldSelectionHost(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    error = None
    try:
        try:
            await runtime.execute_selected_observed(
                frozen.request, frozen.reference, expected_lease_owner=OWNER
            )
        except Exception as exc:
            error = exc
    finally:
        await _close_lane(runtime)
        after = _full_sql(inputs)
        _observe_case(
            tmp_path,
            {
                "seam": seam,
                "reached_after_flush": reached,
                "error": repr(error),
                "cause": getattr(error, "cause_code", None),
                "before": before,
                "after": after,
                "submit": [v.model_dump(mode="json") for v in host.submit_calls],
                "status_count": len(host.status_calls),
                "result_count": host.result_calls,
            },
        )
    assert error is not None and host.submit_calls == []
    assert (seam if seam != "last_fence" else "selection_link") in reached
    assert before == after, "Partial Method/Link/Prompt/Execution/Integrity/derived Link survived"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["submit", "status", "result"])
async def test_original_selection_is_rechecked_at_each_rpc_and_before_semantic_mapping(
    inputs: PreparedInputs,
    tmp_path: Path,
    phase: str,
) -> None:
    _active(inputs)
    frozen = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id,
        graph_round=1,
        expected_lease_owner=OWNER,
    )
    before = _full_sql(inputs)
    mutation = None

    class Host(HeldSelectionHost):
        async def submit(self, payload: DshSessionSubmit):
            nonlocal mutation
            value = await super().submit(payload)
            if phase == "submit":
                mutation = _damage_selection(inputs, frozen, "deleted")
            return value

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            nonlocal mutation
            value = await super().status(payload)
            if phase == "status":
                mutation = _damage_selection(inputs, frozen, "deleted")
            return value

        async def result(self, payload: DshSessionSubmit):
            nonlocal mutation
            value = await super().result(payload)
            if phase == "result":
                mutation = _damage_selection(inputs, frozen, "deleted")
            return value

    host = Host(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    returned = None
    error = None
    try:
        try:
            returned = await runtime.execute_selected_observed(
                frozen.request, frozen.reference, expected_lease_owner=OWNER
            )
        except Exception as exc:
            error = exc
    finally:
        await _close_lane(runtime)
        after = _full_sql(inputs)
        _observe_case(
            tmp_path,
            {
                "phase": phase,
                "before": before,
                "after": after,
                "mutation": mutation,
                "error": repr(error),
                "cause": getattr(error, "cause_code", None),
                "submit": [v.model_dump(mode="json") for v in host.submit_calls],
                "statuses": [v.model_dump(mode="json") for v in host.status_calls],
                "result_count": host.result_calls,
                "returned": returned.model_dump(mode="json") if returned else None,
            },
        )
    assert mutation is not None and error is not None and returned is None
    assert len(host.submit_calls) == 1
    assert len(host.status_calls) == {"submit": 0, "status": 1, "result": 2}[phase]
    assert host.result_calls == (1 if phase == "result" else 0)
    assert after["research_evidence"] == before["research_evidence"]
    assert after["research_facts"] == before["research_facts"]
    mapped = [row for row in after["run_role_outputs"] if row["role"].startswith("research_mapped")]
    assert mapped == []
    if phase == "result":
        raw = [
            json.loads(row["payload_json"])
            for row in after["run_role_outputs"]
            if row["role"].startswith("research_transport")
        ]
        result_observations = [body for body in raw if body["phase"] == "result"]
        assert len(result_observations) == 1 and result_observations[0]["reply"]["result_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["owner", "expiry"])
async def test_selected_writer_fences_after_last_native_context_read(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    _active(inputs)
    selected = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id,
        graph_round=1,
        expected_lease_owner=OWNER,
    )
    before = _full_sql(inputs)
    reached = []
    original = runtime_binding.native_observation_context_in_session

    def late_drift(session: Session, *args: Any, **kwargs: Any):
        value = original(session, *args, **kwargs)
        run = session.get(RunRecord, inputs.request.run_id, populate_existing=True)
        assert run is not None
        if drift == "owner":
            run.lease_owner = "replacement after full native proof"
        else:
            run.lease_expires_at = datetime.now(UTC) - timedelta(microseconds=1)
        session.flush()
        reached.append({"owner": run.lease_owner, "expiry": str(run.lease_expires_at)})
        return value

    monkeypatch.setattr(runtime_binding, "native_observation_context_in_session", late_drift)
    error = None
    returned = None
    try:
        # The public borrowed writer is synchronous and has one real caller
        # transaction. It isolates the last fence from transport retry policy.
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            anchor = runtime_binding.read_runtime_task_anchor(session, inputs.request.run_id)
            returned = runtime_binding.prepare_runtime_turn_in_session(
                session, inputs.database, anchor, selected.request, inputs.upstream,
                runtime_id="dsh", runtime_version=f"dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}",
                profile_ref="decision-research.web.v1", prompt_policy_hash=PROMPT_POLICY_HASH,
                selection_reference=selected.reference, expected_lease_owner=OWNER,
            )
    except Exception as exc:
        error = exc
    finally:
        after = _full_sql(inputs)
        _observe_case(
            tmp_path,
            {
                "drift": drift,
                "before": before,
                "after": after,
                "reached_after_actual_native_proof": reached,
                "error": repr(error),
                "cause": getattr(error, "cause_code", None),
                "boundary": "actual borrowed writer; no Host API called",
                "returned_generation": None if returned is None else returned.prepared.execution.generation,
            },
        )
    assert reached and error is not None and returned is None
    assert after == before, "Preparation committed after its last owner/expiry permission was lost"


@pytest.mark.asyncio
async def test_same_generation_requires_original_selection_row_not_equal_later_projection(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    _active(inputs)
    clock = datetime.now(UTC)
    selector = ContextSelectionService(inputs.database, clock=lambda: clock)
    first = selector.select(run_id=inputs.request.run_id, graph_round=1, expected_lease_owner=OWNER)
    host = HeldSelectionHost(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        await runtime.execute_selected_observed(
            first.request, first.reference, expected_lease_owner=OWNER
        )
    finally:
        await _close_lane(runtime)
    second = selector.select(
        run_id=inputs.request.run_id,
        graph_round=1,
        attempt_no=2,
        attempt_basis="explicit_reselection",
        parent_selection_ref=first.reference,
        expected_lease_owner=OWNER,
    )
    assert first.reference != second.reference and first.request == second.request
    before = _full_sql(inputs)
    error = None
    returned = None
    try:
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            anchor = runtime_binding.read_runtime_task_anchor(session, inputs.request.run_id)
            returned = runtime_binding.prepare_runtime_turn_in_session(
                session,
                inputs.database,
                anchor,
                second.request,
                inputs.upstream,
                runtime_id="dsh",
                runtime_version=f"dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}",
                profile_ref="decision-research.web.v1",
                prompt_policy_hash=PROMPT_POLICY_HASH,
                selection_reference=second.reference,
                expected_lease_owner=OWNER,
            )
    except Exception as exc:
        error = exc
    after = _full_sql(inputs)
    _observe_case(
        tmp_path,
        {
            "first": first.reference.model_dump(mode="json"),
            "second": second.reference.model_dump(mode="json"),
            "same_projected_request": first.request == second.request,
            "before": before,
            "after": after,
            "error": repr(error),
            "returned_execution": None
            if returned is None
            else returned.prepared.execution.model_dump(mode="json"),
        },
    )
    assert error is not None and returned is None and before == after


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["owner", "expiry"])
async def test_verified_selection_permission_loss_is_invalid_without_status_retry(
    inputs: PreparedInputs,
    tmp_path: Path,
    drift: str,
) -> None:
    _active(inputs)
    selected = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id, graph_round=1, expected_lease_owner=OWNER
    )
    mutation = None

    class PermissionHost(HeldSelectionHost):
        async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
            nonlocal mutation
            accepted = await super().submit(payload)
            with sqlite3.connect(_database_path(inputs)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                if drift == "owner":
                    connection.execute(
                        "UPDATE runs SET lease_owner=? WHERE run_id=?",
                        ("replacement after actual accepted reply", payload.run_id),
                    )
                else:
                    connection.execute(
                        "UPDATE runs SET lease_expires_at=? WHERE run_id=?",
                        (
                            (datetime.now(UTC) - timedelta(microseconds=1)).replace(tzinfo=None).isoformat(" "),
                            payload.run_id,
                        ),
                    )
                changed = connection.execute(
                    "SELECT lease_owner,lease_expires_at FROM runs WHERE run_id=?", (payload.run_id,)
                ).fetchone()
                assert changed is not None
                mutation = dict(changed)
            return accepted

    host = PermissionHost(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    before = _full_sql(inputs)
    error = None
    returned = None
    try:
        # The external guard bounds this test; it does not change the actual
        # original caller deadline or Runtime retry clock.
        returned = await asyncio.wait_for(
            runtime.execute_selected_observed(
                selected.request, selected.reference, expected_lease_owner=OWNER
            ),
            timeout=30,
        )
    except Exception as exc:
        error = exc
    finally:
        await _close_lane(runtime)
        after = _full_sql(inputs)
        _observe_case(tmp_path, {
            "drift": drift, "mutation": mutation, "before": before, "after": after,
            "error": repr(error), "cause": getattr(error, "cause_code", None),
            "retryable": getattr(error, "retryable", None),
            "returned": None if returned is None else returned.result.model_dump(mode="json"),
            "submit": [value.model_dump(mode="json") for value in host.submit_calls],
            "statuses": [value.model_dump(mode="json") for value in host.status_calls],
            "results": host.result_calls, "cancelled": host.cancelled,
        })
    assert mutation is not None and error is not None and returned is None
    assert getattr(error, "retryable", None) is False
    assert len(host.submit_calls) == 1 and host.status_calls == [] and host.result_calls == 0
    assert host.cancelled == [], "A replacement owner must not be cancelled"
    assert after["research_evidence"] == before["research_evidence"]
    assert after["research_facts"] == before["research_facts"]
    assert any(row["role"].startswith("research_transport:") for row in after["run_role_outputs"])


@pytest.mark.asyncio
async def test_normal_composition_fresh_run_uses_prepared_selection_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.hub_worker.composition import build_research_worker

    database, event_id, run_id = _admitted_run(tmp_path)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "worker-data"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_EXECUTION_MODE", "replay")
    monkeypatch.delenv("DECISION_HUB_RESEARCH_MIN_PRIORITY", raising=False)
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host, poll_interval_seconds=0.001)
    before = _full_database_sql(database)
    worker = None
    report = None
    error = None
    prepared = None
    try:
        worker = build_research_worker(database, runtime=runtime, allowed_capabilities=("replay.research",))
        report = await worker.tick()
        with database.session() as session:
            prepared = preparation.read_prepared_execution_in_session(session, run_id)
    except Exception as exc:
        error = exc
    finally:
        await _close_lane(runtime)
        after = _full_database_sql(database)
        _observe_case(tmp_path, {
            "before": before, "after": after, "event_id": event_id, "run_id": run_id,
            "error": repr(error), "report": None if report is None else report.model_dump(mode="json"),
            "method": None if prepared is None else prepared.method.model_dump(mode="json"),
            "execution": None if prepared is None else prepared.execution.model_dump(mode="json"),
            "submits": [value.model_dump(mode="json") for value in host.submit_calls],
            "worker_id": None if worker is None else worker.worker.worker_id,
            "route": "actual normal composition; no opt-in executor keyword from this test",
        })
        database.engine.dispose()
    assert error is None and report is not None and prepared is not None
    assert prepared.method.schema_version == "research-execution-method.v3"
    assert prepared.execution.schema_version == "dsh-execution-request.v3"
    assert host.submit_calls and all(value.run_id == run_id for value in host.submit_calls)
    by_role = {row["role"]: row for row in after["run_role_outputs"]}
    assert by_role["research_request_pack"]["schema_version"] == "research-request-pack.v2"
    assert json.loads(by_role["research_request_pack"]["payload_json"])["definition"]["manifest"]["version"] == "1.1.0"
    assert by_role["context_execution_link:1"]["schema_version"] == "research-context-selection-execution-link.v2"
    assert by_role["context_select.r1.a1"]["schema_version"] == "research-context-selection.v3"
    assert report.status == "reject", "Controlled empty synthesis is not publication approval"


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", ["research-context-selection-execution-link.v2", "unknown-link.v9"])
async def test_orphan_numeric_selection_link_requires_real_consumer_proof(
    inputs: PreparedInputs, tmp_path: Path, schema: str,
) -> None:
    from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        ResearchObservabilityService,
    )
    from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
    from tests.evolution.test_research_worker import (
        _result,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.research.test_gateway_execution_v2 import (
        ControlledAdapter,
        _gateway,  # pyright: ignore[reportPrivateUsage]
        _query,  # pyright: ignore[reportPrivateUsage]
    )

    _active(inputs)
    selected = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id, graph_round=1, expected_lease_owner=OWNER
    )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        anchor = runtime_binding.read_runtime_task_anchor(session, inputs.request.run_id)
        turn = runtime_binding.prepare_runtime_turn_in_session(
            session, inputs.database, anchor, selected.request, inputs.upstream,
            runtime_id="dsh",
            runtime_version=f"dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}",
            profile_ref="decision-research.web.v1", prompt_policy_hash=PROMPT_POLICY_HASH,
            selection_reference=selected.reference, expected_lease_owner=OWNER,
        )
    host = HeldSelectionHost(inputs)
    host.release.set()
    links = DshSessionLinkService(inputs.database)
    links.accepted(await host.submit(turn.submit))
    original = _full_sql(inputs)
    # A genuine prepared Link exists first. A different physical connection
    # removes its bundle/Method/proofs; the numeric new role is the only new
    # execution footprint left. Selection alone does not upgrade an old Run.
    with sqlite3.connect(_database_path(inputs)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM run_role_outputs WHERE run_id=? AND role NOT IN (?,?)",
            (inputs.request.run_id, selected.reference.role, "context_execution_link:1"),
        )
        connection.execute(
            "UPDATE run_role_outputs SET schema_version=? WHERE run_id=? AND role=?",
            (schema, inputs.request.run_id, "context_execution_link:1"),
        )
    before = _full_sql(inputs)
    observer = ResearchObservabilityService(inputs.database)
    reserved = observer.reserve_tool_call(
        run_id=inputs.request.run_id, request_id="orphan-selection-call",
        research_session_id=turn.submit.deterministic_session_id, generation=1,
        capability_id="replay.research", query_hash="a" * 64,
    )
    source = replace(inputs, request=selected.request, submit=turn.submit, now=datetime.now(UTC))
    adapter = ControlledAdapter(source)
    errors: dict[str, dict[str, str | None]] = {}
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    try:
        try:
            await _gateway(source, adapter).execute(_query(source))
        except Exception as exc:
            errors["gateway"] = {"code": getattr(exc, "error_code", None), "cause": getattr(exc, "cause_code", None)}
        try:
            await LangGraphResearchExecutor(
                inputs.database, runtime, expected_lease_owner=OWNER
            ).execute(selected.request)
        except ValueError as exc:
            errors["graph"] = {"code": str(exc), "cause": None}
        # These are untrusted caller declarations for denial checks, not a
        # claimed successful Graph projection or publication proof.
        candidate = _result(selected.request)
        try:
            ResearchReviewService(inputs.database).review(inputs.request.run_id, candidate)
        except ValueError as exc:
            errors["review"] = {"code": str(exc), "cause": None}
        try:
            CommitDecisionService(inputs.database).commit_research_result(
                inputs.request.run_id, inputs.request.event_id, candidate
            )
        except ValueError as exc:
            errors["commit"] = {"code": str(exc), "cause": None}
    finally:
        await _close_lane(runtime)
        after = _full_sql(inputs)
        _observe_case(tmp_path, {
            "original_prepared_rows": original, "schema": schema, "before": before, "after": after,
            "reservation_outcome": reserved.outcome,
            "reservation_error": None if reserved.error is None else reserved.error.model_dump(mode="json"),
            "errors": errors, "adapter_calls": len(adapter.calls),
            "host_submits": [value.model_dump(mode="json") for value in host.submit_calls],
            "host_statuses": [value.model_dump(mode="json") for value in host.status_calls],
        })
    assert reserved.outcome == "denied" and reserved.error is not None
    assert reserved.error.error_code == "research_tool_v2_admission_required"
    assert errors["gateway"]["code"] == "research_execution_binding_invalid"
    assert set(errors) == {"gateway", "graph", "review", "commit"}
    assert errors["graph"]["code"] == "research_task_request_missing_before_execution"
    assert all(
        errors[name]["code"] == "research_graph_native_bundle_missing"
        for name in ("review", "commit")
    )
    assert adapter.calls == [] and len(host.submit_calls) == 1 and host.status_calls == []
    for table in ("research_evidence", "research_facts", "research_results", "artifacts", "outbox", "run_role_outputs", "snapshots", "research_tool_call_reservations"):
        assert after[table] == before[table], table
    assert after["dsh_session_links"] == before["dsh_session_links"]


@pytest.mark.asyncio
async def test_method2_cannot_ignore_numeric_selected_link_but_keeps_unreserved_role_legacy(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    _active(inputs)
    host = HeldSelectionHost(inputs)
    host.release.set()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        await runtime.execute_observed(inputs.request)
    finally:
        await _close_lane(runtime)
    with inputs.database.session() as session:
        original = preparation.read_prepared_execution_in_session(session, inputs.request.run_id)
        assert original.method.schema_version == "research-execution-method.v2"
        session.add(
            RunRoleOutputRecord(
                output_id="unrelated_context_role",
                run_id=inputs.request.run_id,
                role="context_execution_link:unrelated",
                schema_version="not-a-reserved-schema",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=datetime.now(UTC),
            )
        )
    with inputs.database.session() as session:
        assert (
            preparation.read_prepared_execution_in_session(session, inputs.request.run_id)
            == original
        )
        session.add(
            RunRoleOutputRecord(
                output_id="unknown_numeric_context_role",
                run_id=inputs.request.run_id,
                role="context_execution_link:1",
                schema_version="unknown-link.v9",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=datetime.now(UTC),
            )
        )
    before = _full_sql(inputs)
    error = None
    returned = None
    try:
        with inputs.database.session() as session:
            returned = preparation.read_prepared_execution_in_session(
                session, inputs.request.run_id
            )
    except ValueError as exc:
        error = exc
    after = _full_sql(inputs)
    _observe_case(
        tmp_path,
        {
            "before": before,
            "after": after,
            "error": repr(error),
            "returned_method_schema": None if returned is None else returned.method.schema_version,
            "old_method_schema": original.method.schema_version,
            "host_submits": len(host.submit_calls),
        },
    )
    assert error is not None and returned is None and before == after
