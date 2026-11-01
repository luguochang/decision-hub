"""Retained-reader safety controls; the formal historical chain is a separate gate.

These controls never fabricate a passing Selection, Method or Publication.
The Root formal-read probe must still authenticate the five real old Selection
originals and twelve old AI windows from one read-only SQLite snapshot.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import (
    research_context_selection as current,
)
from packages.kernel.decision_hub_kernel.application import (
    research_context_selection_versions as versions,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding as graph
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import OWNER, MaterialHost
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_graph_recipe_selection_v2 import prepared_task as prepared_task

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


def test_retained_producer_and_graph_reader_are_exact_originals() -> None:
    originals = versions._verified_resources()  # pyright: ignore[reportPrivateUsage]
    producer = versions._load_modules(originals)  # pyright: ignore[reportPrivateUsage]
    # This calls the ORIGINAL function, whose __file__/root point to its intact
    # registered source tree. It does not inject a claimed old hash into a new
    # producer, rewrite the source or modify global package modules.
    assert producer._selector_identity() == versions._IDENTITY
    assert producer._selector_identity() != current._selector_identity()
    binding = versions._MODULES[versions._BINDING]
    assert producer.read_graph_round_in_session is binding.read_graph_round_in_session
    assert (
        binding._recipe_hash()
        == versions._FILES[
            "packages/kernel/decision_hub_kernel/application/research_graph_projection.py"
        ]
    )
    assert current.read_graph_round_in_session is not binding.read_graph_round_in_session


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("implementation_hash", "0" * 64),
        ("producer_source_hash", "0" * 64),
        ("context_policy_source_hash", "0" * 64),
        ("ranking_rules_ref", "research-context.relevance-authority-recency.v1"),
    ],
)
def test_unknown_or_mixed_identity_never_loads_or_queries(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = {**versions._IDENTITY, field: value}

    def forbid(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Unknown version reached a loader or database")

    monkeypatch.setattr(versions, "_verified_resources", forbid)
    assert (
        versions.replay_retained_context_selection_in_session(
            None,  # type: ignore[arg-type]  # No Session may be reached by this control.
            selector_identity=identity,
            run_id="unknown",
            reference_json="{}",
            clock=lambda: datetime.now(UTC),
        )
        is None
    )


def test_changed_retained_source_is_rejected_even_after_prior_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    originals = versions._verified_resources()
    versions._load_modules(originals)
    copied = tmp_path / "retained"
    shutil.copytree(versions._RESOURCE_ROOT, copied)
    target = copied / versions._BINDING
    target.write_bytes(target.read_bytes() + b"\n# controlled corruption\n")
    monkeypatch.setattr(versions, "_RESOURCE_ROOT", copied)
    with pytest.raises(ValueError, match="^context_selection_implementation_unavailable$"):
        versions._verified_resources()


def test_changed_reused_dependency_is_not_hidden_by_original_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "current"
    shutil.copytree(versions._RESOURCE_ROOT, copied)
    relative = "packages/kernel/decision_hub_kernel/application/research_context.py"
    target = copied / relative
    target.write_bytes(target.read_bytes() + b"\n# controlled dependency drift\n")
    monkeypatch.setattr(versions, "_ROOT", copied)
    with pytest.raises(ValueError, match="^context_selection_implementation_unavailable$"):
        versions._verified_resources()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO control VALUES (2)",
        "UPDATE control SET value = 2",
        "DELETE FROM control",
        "CREATE TABLE unauthorized (value INTEGER)",
    ],
)
def test_original_reader_cannot_write_and_listener_is_removed(statement: str) -> None:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE control (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO control VALUES (1)")
        with Session(engine) as session:
            with versions._read_only(session):
                assert session.connection().exec_driver_sql("SELECT value FROM control").all() == [
                    (1,)
                ]
                with pytest.raises(
                    ValueError, match="^context_selection_historical_write_forbidden$"
                ):
                    session.connection().exec_driver_sql(statement)
            assert session.connection().exec_driver_sql("SELECT value FROM control").all() == [(1,)]
            session.connection().exec_driver_sql("INSERT INTO control VALUES (3)")
            assert session.connection().exec_driver_sql(
                "SELECT value FROM control ORDER BY value"
            ).all() == [(1,), (3,)]
    finally:
        engine.dispose()


def test_current_writer_never_uses_historical_dispatch() -> None:
    # The actual writer branch is exercised by the existing producer and Graph
    # gates. This control locks the public ABI: no argument can ask the retained
    # implementation to select, execute, fence, or return a current permission.
    import inspect

    parameters = inspect.signature(versions.replay_retained_context_selection_in_session).parameters
    assert "require_current_permission" not in parameters
    assert "expected_lease_owner" not in parameters
    assert "active" not in parameters
    assert "database" not in parameters


@pytest.mark.asyncio
async def test_actual_current_selection_method3_graph2_and_unknown_identity(
    prepared_task: PreparedInputs,
) -> None:
    """Actual Factory/Runtime/Graph originals, then an explicit corrupt negative."""
    assert RunService(prepared_task.database).claim_for_execution(
        prepared_task.request.run_id,
        worker_id=OWNER,
        lease_seconds=900,
        strategy_version="research.v1",
    )
    selected = current.ContextSelectionService(prepared_task.database).select(
        run_id=prepared_task.request.run_id,
        graph_round=1,
        expected_lease_owner=OWNER,
    )
    assert (
        selected.receipt.selector_identity.model_dump(mode="json") == current._selector_identity()
    )
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(prepared_task.database),
        MaterialHost(prepared_task),
        poll_interval_seconds=0.001,
    )
    try:
        observed = await runtime.execute_selected_observed(
            selected.request,
            selected.reference,
            expected_lease_owner=OWNER,
        )
    finally:
        await runtime.close()
    with prepared_task.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        admitted = graph.admit_observed_round_in_session(
            session,
            run_id=prepared_task.request.run_id,
            expected_lease_owner=OWNER,
            observed=observed,
            previous=None,
            database=prepared_task.database,
        )
        assert isinstance(admitted.receipt, graph.ResearchGraphRoundAdmissionV2)
        final = graph.freeze_graph_final_in_session(
            session,
            run_id=prepared_task.request.run_id,
            expected_lease_owner=OWNER,
            last_admission=admitted.reference,
            database=prepared_task.database,
        )
        assert isinstance(final.receipt, graph.ResearchGraphFinalProjectionV2)
        original = graph.read_graph_final_in_session(
            session,
            run_id=prepared_task.request.run_id,
            reference=final.reference,
            expected_lease_owner=OWNER,
            require_current_permission=True,
        )
        assert original.receipt == final.receipt
        method = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prepared_task.request.run_id,
                RunRoleOutputRecord.role == "research_execution_method",
            )
        )
        assert method is not None and method.schema_version == "research-execution-method.v3"

        row = session.get(RunRoleOutputRecord, selected.reference.output_id)
        assert row is not None
        body = selected.receipt.model_dump(mode="json")
        body["selector_identity"]["implementation_hash"] = "0" * 64
        _false_receipt, row.payload_json = current._encode(body)
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
        session.flush()
        corrupted_ref = selected.reference.model_copy(update={"payload_hash": row.payload_hash})
        with pytest.raises(ValueError, match="^context_selection_implementation_unavailable$"):
            current.ContextSelectionService(prepared_task.database).read_in_session(
                session,
                run_id=prepared_task.request.run_id,
                reference=corrupted_ref,
                require_current_permission=False,
            )
        session.rollback()
