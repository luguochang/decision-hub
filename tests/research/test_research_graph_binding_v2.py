"""Real frozen inputs, controlled Runtime observations and isolated Graph SQL."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    AgenticResearchState,
    initial_research_state,
    request_for_state,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedResearchResult
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    _active,  # pyright: ignore[reportPrivateUsage]
    _sql,  # pyright: ignore[reportPrivateUsage]
    _write_observation,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


async def _observed(fixture: PreparedInputs, host: PreparedHost | None = None):
    full = _active(fixture)
    actual = request_for_state(initial_research_state(full), eligibility_cutoff=full.pit_cutoff_at)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(fixture.database),
        host or PreparedHost(fixture),
        poll_interval_seconds=0.001,
    )
    try:
        return await runtime.execute_observed(actual)
    finally:
        await runtime.close()


def _admit(session: Session, fixture: PreparedInputs, observed: ObservedResearchResult):
    return binding.admit_observed_round_in_session(
        session,
        run_id=fixture.request.run_id,
        expected_lease_owner=OWNER,
        database=fixture.database,
        observed=observed,
        previous=None,
    )


def _capture(path: Path, **body: object):
    _write_observation(path, "graph-binding-observation.json", body)


@pytest.mark.asyncio
async def test_real_round_and_final_replay_keep_original_refs_times_and_counters(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    observed = await _observed(inputs)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        first = _admit(session, inputs, observed)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        repeated = _admit(session, inputs, observed)
        final = binding.freeze_graph_final_in_session(
            session,
            run_id=inputs.request.run_id,
            expected_lease_owner=OWNER,
            database=inputs.database,
            last_admission=first.reference,
        )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        again = binding.freeze_graph_final_in_session(
            session,
            run_id=inputs.request.run_id,
            expected_lease_owner=OWNER,
            database=inputs.database,
            last_admission=first.reference,
        )
        binding.verify_declared_graph_result_in_session(
            session,
            run_id=inputs.request.run_id,
            reference=final.reference,
            declared_result=final.result,
            expected_lease_owner=OWNER,
        )
        changed = final.result.model_copy(
            update={"total_tool_calls": final.result.total_tool_calls + 1}
        )
        with pytest.raises(ValueError, match="declared_final_result_mismatch"):
            binding.verify_declared_graph_result_in_session(
                session,
                run_id=inputs.request.run_id,
                reference=final.reference,
                declared_result=changed,
                expected_lease_owner=OWNER,
            )
    assert first.reference == repeated.reference and first.state == repeated.state
    assert final.reference == again.reference and final.result == again.result
    cache: AgenticResearchState = {
        **first.state,
        "graph_round_admission_ref": first.reference.model_dump(mode="json"),
        "graph_final_projection_ref": final.reference.model_dump(mode="json"),
        "final_result": final.result.model_dump(mode="json"),
        "decision_snapshot_id": final.receipt.decision_snapshot_id,
        "stop_code": final.result.stop_reason.code,
        "stop_detail": final.result.stop_reason.detail,
    }
    rejected_caches = []
    with inputs.database.session() as session:
        assert binding.prepare_graph_state_in_session(session, cache, OWNER)[0]
        for damage in ("latest_result", "coverage", "final_result", "locator"):
            changed_cache = copy.deepcopy(cache)
            if damage == "locator":
                changed_ref = final.reference.model_copy(
                    update={
                        "row": final.reference.row.model_copy(
                            update={"output_id": final.reference.row.output_id + "-wrong"}
                        )
                    }
                )
                changed_cache["graph_final_projection_ref"] = changed_ref.model_dump(mode="json")
            elif damage == "coverage":
                changed_cache["coverage"]["hard_coverage_ratio"] = 1.0
            elif damage == "latest_result":
                latest = ResearchSessionResult.model_validate(changed_cache["latest_result"])
                changed_cache["latest_result"] = latest.model_copy(
                    update={"total_tool_calls": latest.total_tool_calls + 1}
                ).model_dump(mode="json")
            else:
                result = ResearchSessionResult.model_validate(changed_cache["final_result"])
                changed_cache["final_result"] = result.model_copy(
                    update={"total_tool_calls": result.total_tool_calls + 1}
                ).model_dump(mode="json")
            with pytest.raises(ValueError) as caught:
                binding.prepare_graph_state_in_session(session, changed_cache, OWNER)
            rejected_caches.append({"damage": damage, "error": str(caught.value)})
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id)
        assert run is not None
        run.status = "completed"
    with inputs.database.session() as session:
        historical = binding.read_graph_final_in_session(
            session,
            run_id=inputs.request.run_id,
            reference=final.reference,
            expected_lease_owner=None,
            require_current_permission=False,
        )
        assert historical.reference == final.reference and historical.result == final.result
        with pytest.raises(PermissionError):
            binding.verify_declared_graph_result_in_session(
                session,
                run_id=inputs.request.run_id,
                reference=final.reference,
                declared_result=final.result,
                expected_lease_owner=OWNER,
            )
    _capture(
        tmp_path,
        original_observation=observed.observation_ref.model_dump(mode="json"),
        first=first.reference.model_dump(mode="json"),
        repeated=repeated.reference.model_dump(mode="json"),
        final=final.reference.model_dump(mode="json"),
        rejected_caches=rejected_caches,
        sql=_sql(inputs),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["task", "method", "prompt"])
async def test_admission_refuses_actual_original_sql_drift_without_resigning(
    inputs: PreparedInputs,
    tmp_path: Path,
    damage: str,
):
    observed = await _observed(inputs)
    with inputs.database.session() as session:
        if damage == "prompt":
            from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord

            row = session.get(DshSessionPromptRecord, (inputs.request.run_id, 1))
            assert row is not None
            row.prompt += " drift"
        else:
            role = "research_task_request" if damage == "task" else "research_execution_method"
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=inputs.request.run_id, role=role)
                .one()
            )
            session.delete(row)
    before = _sql(inputs)
    with pytest.raises(ValueError):
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _admit(session, inputs, observed)
    after = _sql(inputs)
    assert before == after and after["decision_snapshot_id"] is None
    assert not any(role.startswith("research_graph_") for role in after["roles"])
    _capture(tmp_path, damage=damage, before=before, after=after)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["round", "snapshot"])
async def test_failure_after_actual_sql_flush_rolls_back_projection_and_snapshot(
    inputs: PreparedInputs,
    tmp_path: Path,
    boundary: str,
):
    observed = await _observed(inputs)
    first = None
    if boundary == "snapshot":
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            first = _admit(session, inputs, observed)
    before = _sql(inputs)
    fired = []

    def fail_after_flush(session: Session, _context: object):
        for row in session.new:
            if (
                boundary == "round"
                and isinstance(row, RunRoleOutputRecord)
                and row.role.startswith("research_graph_round:")
            ) or (
                boundary == "snapshot"
                and isinstance(row, SnapshotRecord)
                and row.snapshot_type == "decision"
            ):
                fired.append(type(row).__name__)
                raise RuntimeError("injected_after_real_sql_flush")

    event.listen(Session, "after_flush", fail_after_flush)
    try:
        with pytest.raises(RuntimeError, match="after_real_sql_flush"):
            with inputs.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                if boundary == "round":
                    _admit(session, inputs, observed)
                else:
                    assert first is not None
                    binding.freeze_graph_final_in_session(
                        session,
                        run_id=inputs.request.run_id,
                        expected_lease_owner=OWNER,
                        database=inputs.database,
                        last_admission=first.reference,
                    )
    finally:
        event.remove(Session, "after_flush", fail_after_flush)
    after = _sql(inputs)
    assert fired and before == after
    assert after["decision_snapshot_id"] is None
    _capture(tmp_path, boundary=boundary, actual_flushed=fired, before=before, after=after)


@pytest.mark.asyncio
async def test_real_expiry_after_sql_flush_uses_final_fence_and_rolls_back(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    observed = await _observed(inputs)
    before = _sql(inputs)
    fired = []

    def expire_after_flush(session: Session, _context: object):
        for row in session.new:
            if isinstance(row, RunRoleOutputRecord) and row.role.startswith(
                "research_graph_round:"
            ):
                fired.append(row.output_id)
                # Real SQL after receipt INSERT, without mocking the permission guard.
                session.connection().exec_driver_sql(
                    "UPDATE runs SET lease_expires_at = ? WHERE run_id = ?",
                    (
                        (datetime.now(UTC) - timedelta(seconds=1))
                        .replace(tzinfo=None)
                        .isoformat(" "),
                        inputs.request.run_id,
                    ),
                )

    event.listen(Session, "after_flush", expire_after_flush)
    try:
        with pytest.raises(PermissionError):
            with inputs.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                _admit(session, inputs, observed)
    finally:
        event.remove(Session, "after_flush", expire_after_flush)
    after = _sql(inputs)
    assert fired and before == after
    _capture(tmp_path, actual_flushed=fired, before=before, after=after)


def test_unknown_reserved_or_orphan_locator_cannot_downgrade_and_v1_adapter_is_not_v2(
    inputs: PreparedInputs,
):
    _active(inputs)
    with inputs.database.session() as session:
        # Actual original partial V2 footprint, not a fake caller flag.
        session.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role="research_compiled_policy"
        ).delete()
    with inputs.database.session() as session:
        with pytest.raises(ValueError):
            binding.graph_requires_native_binding_in_session(session, inputs.request.run_id)
    with inputs.database.session() as session:
        session.query(RunRoleOutputRecord).filter_by(run_id=inputs.request.run_id).delete()
        session.add(
            RunRoleOutputRecord(
                output_id="legacy-adapter",
                run_id=inputs.request.run_id,
                role="research_adapter_return:legacy",
                schema_version="research-adapter-return.v1",
                payload_json="{}",
                payload_hash="0" * 64,
                created_at=inputs.now,
            )
        )
    with inputs.database.session() as session:
        assert (
            binding.graph_requires_native_binding_in_session(session, inputs.request.run_id)
            is False
        )
        with pytest.raises(ValueError, match="native_bundle_missing"):
            binding.graph_requires_native_binding_in_session(
                session, inputs.request.run_id, has_checkpoint_locator=True
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_lineage", [False, True])
async def test_real_attested_fact_lineage_is_atomic_with_round_projection(
    inputs: PreparedInputs,
    tmp_path: Path,
    fail_after_lineage: bool,
):
    host = MaterialHost(inputs)
    observed = await _observed(inputs, host)
    assert len(observed.result.facts or []) == 1
    before = _sql(inputs)
    assert not before["materials"]["research_facts"]
    assert len(host.submit_calls) == 1
    flushed: list[str] = []

    def fail_after_flush(session: Session, _context: object) -> None:
        for row in session.new:
            if isinstance(row, RunInputRefRecord):
                flushed.append(row.fact_id)
                raise RuntimeError("injected_after_actual_fact_lineage_sql_flush")

    first = None
    if fail_after_lineage:
        event.listen(Session, "after_flush", fail_after_flush)
    try:
        if fail_after_lineage:
            with pytest.raises(RuntimeError, match="actual_fact_lineage_sql_flush"):
                with inputs.database.session() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    _admit(session, inputs, observed)
        else:
            with inputs.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                first = _admit(session, inputs, observed)
    finally:
        if fail_after_lineage:
            event.remove(Session, "after_flush", fail_after_flush)
    after = _sql(inputs)
    if fail_after_lineage:
        assert flushed and after == before
    else:
        assert first is not None
        assert len(first.receipt.facts) == 1 and first.receipt.facts[0].quality == "accepted"
        assert len(after["materials"]["research_facts"]) == 1
        assert len(after["materials"]["run_input_refs"]) == 1
        assert after["materials"]["parsed_versions"] and after["materials"]["fact_versions"]
        with inputs.database.session() as session:
            reread = binding.read_graph_round_in_session(
                session,
                run_id=inputs.request.run_id,
                reference=first.reference,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
        assert reread == first
    _capture(
        tmp_path,
        fail_after_lineage=fail_after_lineage,
        actual_flushed=flushed,
        original_runtime_ref=observed.observation_ref.model_dump(mode="json"),
        fact_payload=observed.result.facts[0].model_dump(mode="json")
        if observed.result.facts
        else None,
        admitted_ref=first.reference.model_dump(mode="json") if first else None,
        before=before,
        after=after,
    )
