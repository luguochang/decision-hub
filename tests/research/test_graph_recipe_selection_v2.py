"""Isolated SQLite/controlled Runtime recipe identity and writer gates.

No Provider, physical Gateway, formal database, or HTTP is executed. The v1
producer seam exists only while creating its original legacy Task; no existing
sidecar is deleted and no saved Task or Graph receipt is upgraded.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections.abc import Generator
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from apps.hub_worker import research as factory_module
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application import (
    research_graph_projection as v1_projection,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    SELECTION_ROLE,
    freeze_graph_recipe_selection_in_session,
    graph_recipe_manifest_v2,
    graph_recipe_task_creation_scope,
    read_graph_recipe_selection_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    _prepared_inputs,  # pyright: ignore[reportPrivateUsage]
    _sql,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_graph_binding_v2 import (
    _admit,  # pyright: ignore[reportPrivateUsage]
    _observed,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


def _legacy_creation_scope(*_: object, **__: object) -> AbstractContextManager[None]:
    return nullcontext()


def _legacy_freeze(*_: object, **__: object) -> None:
    return None


@pytest.fixture
def prepared_task(tmp_path: Path) -> Generator[PreparedInputs]:
    yield from _prepared_inputs(tmp_path, max_rounds=1, max_repairs=0)


def test_real_factory_freezes_original_task_pack_and_dependency_identity(
    prepared_task: PreparedInputs,
) -> None:
    with prepared_task.database.session() as session:
        frozen = read_graph_recipe_selection_in_session(session, prepared_task.request.run_id)
        assert frozen is not None
        assert frozen.receipt.recipe_version == "research-graph-projection.v2"
        assert frozen.receipt.original_task_ref.role == "research_task_request"
        assert frozen.receipt.original_pack_ref.role == "research_request_pack"
        assert frozen.receipt.dependency_hash == observation_sha(
            frozen.receipt.manifest.model_dump(mode="json")
        )
        paths = {item.path for item in frozen.receipt.manifest.source_files}
        assert any(path.endswith("citation_membership_v2.py") for path in paths)
        assert any(path.endswith("research_loop.py") for path in paths)
        assert any(path.endswith("sufficiency.py") for path in paths)
        assert any(path.endswith("research_context.py") for path in paths)
        assert any(path.endswith("research_graph_binding.py") for path in paths)
        assert frozen.receipt.manifest.schema_files
        assert frozen.receipt.manifest.runtime_versions == _actual_runtime_versions()


def _actual_runtime_versions() -> dict[str, str]:
    return {
        **{
            name: version(name)
            for name in (
                "pydantic",
                "pydantic-core",
                "jsonschema",
                "referencing",
                "SQLAlchemy",
                "PyYAML",
            )
        },
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
    }


def test_actual_runtime_identity_changes_dependency_hash() -> None:
    manifest = graph_recipe_manifest_v2()
    assert manifest.runtime_versions == _actual_runtime_versions()
    original_body = manifest.model_dump(mode="json")
    original_hash = observation_sha(original_body)
    changed_hashes: set[str] = set()
    for name in ("python", "sqlite", "pydantic-core", "PyYAML"):
        changed_body = manifest.model_dump(mode="json")
        changed_body["runtime_versions"][name] += "-controlled-version-drift"
        changed_hash = observation_sha(changed_body)
        assert changed_hash != original_hash
        changed_hashes.add(changed_hash)
    assert len(changed_hashes) == 4


def test_existing_v1_task_cannot_gain_selection_even_with_its_original_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = _prepared_inputs(tmp_path, max_rounds=1, max_repairs=0)
    try:
        # A real old producer never wrote the new sidecar. Keep every original
        # Task byte and subsequent reader/runtime route; do not delete a receipt.
        with monkeypatch.context() as legacy_producer:
            legacy_producer.setattr(
                factory_module, "graph_recipe_task_creation_scope", _legacy_creation_scope
            )
            legacy_producer.setattr(
                factory_module, "freeze_graph_recipe_selection_in_session", _legacy_freeze
            )
            old = next(producer)
        with old.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            assert read_graph_recipe_selection_in_session(session, old.request.run_id) is None
            task = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == old.request.run_id,
                    RunRoleOutputRecord.role == "research_task_request",
                )
            )
            assert task is not None
            original_clock = as_utc(task.created_at)
            assert original_clock is not None
            with pytest.raises(ValueError, match="new_task_scope_required"):
                freeze_graph_recipe_selection_in_session(
                    session,
                    run_id=old.request.run_id,
                    recorded_at=original_clock,
                )
            with pytest.raises(ValueError, match="existing_task_not_upgradeable"):
                with graph_recipe_task_creation_scope(session, old.request.run_id):
                    pytest.fail("An existing Task cannot receive a Factory creation permit")
    finally:
        producer.close()


@pytest.mark.parametrize("damage", ["manifest", "runtime_version", "task", "schema"])
def test_canonical_rehashed_sidecar_damage_does_not_authenticate(
    prepared_task: PreparedInputs,
    damage: str,
) -> None:
    with prepared_task.database.session() as session:
        row = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == prepared_task.request.run_id,
                RunRoleOutputRecord.role == SELECTION_ROLE,
            )
        )
        assert row is not None
        body = json.loads(row.payload_json)
        if damage == "manifest":
            body["manifest"]["source_files"][0]["payload_hash"] = "0" * 64
            body["dependency_hash"] = observation_sha(body["manifest"])
        elif damage == "runtime_version":
            assert body["manifest"]["runtime_versions"] == _actual_runtime_versions()
            body["manifest"]["runtime_versions"]["PyYAML"] += "-controlled-version-drift"
            body["dependency_hash"] = observation_sha(body["manifest"])
        elif damage == "task":
            body["original_task_ref"]["payload_hash"] = "0" * 64
        else:
            row.schema_version = "research-graph-recipe-selection.v999"
        row.payload_json = observation_json(body)
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
        row.output_id = "graph_recipe_selection_" + observation_sha(body)
        session.flush()
        with pytest.raises(ValueError, match="recipe_(selection_changed|schema_unsupported)"):
            read_graph_recipe_selection_in_session(session, prepared_task.request.run_id)


def test_unknown_graph_final_schema_is_not_v1_fallback() -> None:
    with pytest.raises(ValueError, match="final_schema_unsupported"):
        binding.parse_graph_final_projection_declaration(
            "{}", schema_version="research-graph-final-projection.v999"
        )


@pytest.mark.asyncio
async def test_v2_round_and_final_are_idempotent_and_cannot_be_relabelled_v1(
    prepared_task: PreparedInputs,
) -> None:
    observed = await _observed(prepared_task, MaterialHost(prepared_task))
    with prepared_task.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        first = _admit(session, prepared_task, observed)
        repeated = _admit(session, prepared_task, observed)
        assert first.reference == repeated.reference and first.state == repeated.state
        assert isinstance(first.receipt, binding.ResearchGraphRoundAdmissionV2)
        final = binding.freeze_graph_final_in_session(
            session,
            run_id=prepared_task.request.run_id,
            expected_lease_owner=OWNER,
            last_admission=first.reference,
            database=prepared_task.database,
        )
        assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
        again = binding.freeze_graph_final_in_session(
            session,
            run_id=prepared_task.request.run_id,
            expected_lease_owner=OWNER,
            last_admission=first.reference,
            database=prepared_task.database,
        )
        assert final.reference == again.reference
        with pytest.raises(PermissionError, match="permission_lost"):
            binding.verify_graph_citation_membership_in_session(
                session,
                run_id=prepared_task.request.run_id,
                reference=final.reference,
                expected_lease_owner="another-writer",
            )
        wrong_version = first.reference.model_copy(
            update={
                "row": first.reference.row.model_copy(
                    update={"schema_version": binding.ROUND_SCHEMA}
                )
            }
        )
        with pytest.raises(ValueError, match="locator_invalid"):
            binding.read_graph_round_in_session(
                session,
                run_id=prepared_task.request.run_id,
                reference=wrong_version,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )


def _rewrite_graph_row(
    session: Session, row: RunRoleOutputRecord, body: dict[str, Any], *, prefix: str
) -> ObservedExecutionRef:
    """Controlled stored corruption, including correct canonical envelope SHA.

    This never turns the body into an authenticated pass: the real reader must
    reject it against the unchanged, genuinely frozen Task/recipe/material.
    """
    row.payload_json = observation_json(body)
    row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    row.schema_version = body["schema_version"]
    row.output_id = (
        prefix
        + "_"
        + observation_sha({key: value for key, value in body.items() if key != "recorded_at"})
    )
    session.flush()
    recorded_at = as_utc(row.created_at)
    assert recorded_at is not None
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=recorded_at,
    )


@pytest.mark.asyncio
async def test_stored_v2_clock_generation_and_true_final_to_v1_round_mix_reject(
    prepared_task: PreparedInputs,
) -> None:
    observed = await _observed(prepared_task, MaterialHost(prepared_task))
    with prepared_task.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        first = _admit(session, prepared_task, observed)
        final = binding.freeze_graph_final_in_session(
            session,
            run_id=prepared_task.request.run_id,
            expected_lease_owner=OWNER,
            last_admission=first.reference,
            database=prepared_task.database,
        )
        assert isinstance(first.receipt, binding.ResearchGraphRoundAdmissionV2)
        assert first.receipt.selected_synthesis_observation_ref is not None
        assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
        # The same stored, same-version pair is genuinely authenticated first.
        assert (
            binding.read_graph_final_in_session(
                session,
                run_id=prepared_task.request.run_id,
                reference=final.reference,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            ).result
            == final.result
        )
    for damage in ("clock", "expired_admission_lease", "future_ref", "generation"):
        with prepared_task.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(RunRoleOutputRecord, first.reference.row.output_id)
            assert row is not None
            body = json.loads(row.payload_json)
            clock = datetime.fromisoformat(body["writer_clock_at"])
            if damage == "clock":
                body["writer_clock_at"] = (clock + timedelta(seconds=1)).isoformat()
            elif damage == "expired_admission_lease":
                body["lease_expires_at"] = body["writer_clock_at"]
            elif damage == "future_ref":
                body["observation_ref"]["recorded_at"] = (clock + timedelta(seconds=1)).isoformat()
            else:
                body["latest_transport_generation"] += 1
            ref = _rewrite_graph_row(session, row, body, prefix="research_graph_round")
            with pytest.raises(
                ValueError,
                match="research_graph_(round_clock_or_identity|projection_generations)_invalid",
            ):
                binding.read_graph_round_in_session(
                    session,
                    run_id=prepared_task.request.run_id,
                    reference=binding.GraphRoundAdmissionRef(row=ref),
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                )
            session.rollback()
    with prepared_task.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        round_row = session.get(RunRoleOutputRecord, first.reference.row.output_id)
        final_row = session.get(RunRoleOutputRecord, final.reference.row.output_id)
        assert round_row is not None and final_row is not None
        body = json.loads(round_row.payload_json)
        for field in ("recipe_selection_ref", "citation_check", "citation_disposition"):
            body.pop(field)
        body["schema_version"] = binding.ROUND_SCHEMA
        body["recipe_version"] = v1_projection.RECIPE_VERSION
        body["recipe_hash"] = hashlib.sha256(Path(v1_projection.__file__).read_bytes()).hexdigest()
        # Real stored v1 DTO + canonical row/id/hash, not a mismatched locator.
        v1_round = binding.ResearchGraphRoundAdmission.model_validate_json(observation_json(body))
        v1_ref = _rewrite_graph_row(
            session, round_row, v1_round.model_dump(mode="json"), prefix="research_graph_round"
        )
        final_body = json.loads(final_row.payload_json)
        final_body["last_admission_ref"] = binding.GraphRoundAdmissionRef(row=v1_ref).model_dump(
            mode="json"
        )
        mixed_final_ref = _rewrite_graph_row(
            session, final_row, final_body, prefix="research_graph_final"
        )
        assert final_row.schema_version == binding.FINAL_SCHEMA_V2
        assert round_row.schema_version == binding.ROUND_SCHEMA
        with pytest.raises(ValueError, match="research_graph_saved_receipt_invalid"):
            binding.read_graph_final_in_session(
                session,
                run_id=prepared_task.request.run_id,
                reference=binding.GraphFinalProjectionRef(row=mixed_final_ref),
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
        session.rollback()


@pytest.mark.asyncio
async def test_real_legacy_producer_round_and_final_keep_v1_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = _prepared_inputs(tmp_path, max_rounds=1, max_repairs=0)
    try:
        with monkeypatch.context() as legacy_producer:
            legacy_producer.setattr(
                factory_module, "graph_recipe_task_creation_scope", _legacy_creation_scope
            )
            legacy_producer.setattr(
                factory_module, "freeze_graph_recipe_selection_in_session", _legacy_freeze
            )
            old = next(producer)
        observed = await _observed(old, MaterialHost(old))
        with old.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            first = _admit(session, old, observed)
            final = binding.freeze_graph_final_in_session(
                session,
                run_id=old.request.run_id,
                expected_lease_owner=OWNER,
                last_admission=first.reference,
                database=old.database,
            )
            assert isinstance(first.receipt, binding.ResearchGraphRoundAdmission)
            assert isinstance(final.receipt, binding.ResearchGraphFinalProjection)
            assert read_graph_recipe_selection_in_session(session, old.request.run_id) is None
            assert binding.authenticated_graph_recipe_identity(final.receipt) is None
            assert (
                binding.read_graph_final_in_session(
                    session,
                    run_id=old.request.run_id,
                    reference=final.reference,
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                ).result
                == final.result
            )
    finally:
        producer.close()


@pytest.mark.asyncio
async def test_actual_v2_receipt_flush_then_cancel_rolls_back_before_final_fence(
    prepared_task: PreparedInputs,
) -> None:
    observed = await _observed(prepared_task, MaterialHost(prepared_task))
    before = _sql(prepared_task)
    flushed: list[str] = []

    def cancel_after_flush(session: Session, _context: object) -> None:
        for row in session.new:
            if (
                isinstance(row, RunRoleOutputRecord)
                and row.run_id == prepared_task.request.run_id
                and row.schema_version == binding.ROUND_SCHEMA_V2
            ):
                flushed.append(row.output_id)
                session.connection().exec_driver_sql(
                    "UPDATE runs SET status = 'cancelled' WHERE run_id = ?",
                    (prepared_task.request.run_id,),
                )

    event.listen(Session, "after_flush", cancel_after_flush)
    try:
        with pytest.raises(ValueError, match="^research_observation_run_not_active$"):
            with prepared_task.database.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                _admit(session, prepared_task, observed)
    finally:
        event.remove(Session, "after_flush", cancel_after_flush)
    assert flushed, "The cancellation must follow the actual v2 INSERT flush"
    assert _sql(prepared_task) == before
