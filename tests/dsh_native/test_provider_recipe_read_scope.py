"""Finite physical-RO recipe reuse and real private callback writer controls.

The recipe fixture explicitly substitutes the upstream authenticated bundle seam
and installation subset. Actual selection/install/source/schema readers execute;
it is not a real product guard or provider result. Writer controls use the existing
actual prepared V2 fixture. No HTTP, provider, model, formal DB or product release.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import provider_input_receipts as callbacks
from packages.kernel.decision_hub_kernel.application import research_graph_recipe as legacy
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe
from packages.kernel.decision_hub_kernel.application.research_context_execution import (
    generation_proof,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
)
from packages.kernel.decision_hub_kernel.application.research_recipe_read_scope import (
    current_recipe_read_scope,
    recipe_original_read_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import (
    forbid_external_connections as forbid_external_connections,
)
from tests.dsh_native.test_provider_input_receipts import inputs as inputs
from tests.market_risk.test_recipe_selection_v3 import (
    NOW,
    add_originals,
    baseline,
    configure,
)
from tests.market_risk.test_recipe_selection_v3 import installation_fixture as installation_fixture
from tests.market_risk.test_recipe_selection_v3 import manifest as manifest
from tests.market_risk.test_recipe_selection_v3 import writer_fixture as writer_fixture

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


@pytest.fixture
def selected(writer_fixture, installation_fixture):
    configure(installation_fixture)
    with writer_fixture.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with legacy.graph_recipe_task_creation_scope(session, "run-contract"):
            add_originals(session)
            original = recipe.freeze_graph_recipe_selection_v3_in_session(
                session, run_id="run-contract", recorded_at=NOW, quality_context=baseline()
            )
    return writer_fixture, original


def _begin(database):
    session = database.factory()
    session.connection().exec_driver_sql("BEGIN")
    return session


def test_actual_recipe_repeated_calls_are_reduced_and_clones_preserve_full_payload(
    selected, monkeypatch, tmp_path
):
    database, original = selected
    full_reader = recipe._read_graph_recipe_selection_v3_original
    full_installation = recipe._installation_original
    counts = {"selection": 0, "installation": 0}

    def read(*args, **kwargs):
        counts["selection"] += 1
        return full_reader(*args, **kwargs)

    def installation():
        counts["installation"] += 1
        return full_installation()

    monkeypatch.setattr(recipe, "_read_graph_recipe_selection_v3_original", read)
    monkeypatch.setattr(recipe, "_installation_original", installation)
    session = _begin(database)
    try:
        start = time.perf_counter()
        for _ in range(6):
            assert recipe.read_graph_recipe_selection_v3_in_session(
                session, "run-contract"
            ) == original
        baseline_seconds = time.perf_counter() - start
        assert counts == {"selection": 6, "installation": 6}
        counts.update(selection=0, installation=0)
        start = time.perf_counter()
        with recipe_original_read_scope(session):
            for _ in range(6):
                clone = recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
                assert clone == original and clone is not original
            clone.receipt.manifest.runtime_versions["injected"] = "mutable clone only"
            clean = recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
            assert clean == original and "injected" not in clean.receipt.manifest.runtime_versions
        scoped_seconds = time.perf_counter() - start
        assert counts == {"selection": 2, "installation": 2}
        (tmp_path / "bounded-performance.json").write_text(json.dumps({
            "baseline_calls": 6, "scoped_calls_including_fresh_exit": counts,
            "baseline_seconds": baseline_seconds, "scoped_seconds": scoped_seconds,
            "same_original": recipe._selection_json(clean) == recipe._selection_json(original),
            "scope": "selected fixture, no source_capture or actual provider; not native proof",
        }), encoding="utf-8")
    finally:
        session.rollback()
        session.close()


def test_current_source_and_schema_tamper_with_restored_mtime_rejected_at_exit(
    selected, monkeypatch, tmp_path
):
    database, _ = selected
    actual_read = recipe._read_resource
    for resource in (recipe.SOURCE_REF, recipe.CONTEXT_SCHEMA_REF):
        local = tmp_path / ("source.py" if resource == recipe.SOURCE_REF else "schema.json")
        original_bytes = actual_read(resource)
        local.write_bytes(original_bytes)
        stat = local.stat()
        monkeypatch.setattr(recipe, "_read_resource", lambda ref, local=local, resource=resource: (
            local.read_bytes() if ref == resource else actual_read(ref)
        ))
        session = _begin(database)
        native = session.connection().connection.driver_connection
        before_changes = native.total_changes
        try:
            with pytest.raises(ValueError, match="effective_implementation_mismatch"):
                with recipe_original_read_scope(session):
                    recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
                    local.write_bytes(original_bytes + b"\n ")
                    os.utime(local, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    assert recipe.read_graph_recipe_selection_v3_in_session(
                        session, "run-contract"
                    ) is not None
            assert native.total_changes == before_changes
        finally:
            session.rollback()
            session.close()
        monkeypatch.setattr(recipe, "_read_resource", actual_read)


def test_selection_dirty_flush_changed_native_transaction_and_other_session_do_not_reuse(selected):
    database, _ = selected
    for change in ("dirty", "flush", "write", "transaction"):
        session = _begin(database)
        try:
            with pytest.raises(ValueError, match="read_scope_changed"):
                with recipe_original_read_scope(session):
                    recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
                    if change in {"dirty", "flush"}:
                        row = session.get(RunRoleOutputRecord, legacy.TASK_ROLE)
                        row.payload_json += " "
                        if change == "flush":
                            session.flush()
                    elif change == "write":
                        session.connection().exec_driver_sql(
                            "UPDATE run_role_outputs SET payload_json=payload_json||' ' "
                            "WHERE output_id=?",
                            (legacy.TASK_ROLE,),
                        )
                    else:
                        session.rollback()
                        session.connection().exec_driver_sql("BEGIN")
                    recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
        finally:
            session.rollback()
            session.close()
    session, other = _begin(database), _begin(database)
    try:
        with recipe_original_read_scope(session):
            assert recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
            # Another Session gets the unchanged original full reader, not this memo.
            assert recipe.read_graph_recipe_selection_v3_in_session(other, "run-contract")
    finally:
        for value in (session, other):
            value.rollback()
            value.close()


def test_copied_contexts_and_different_proof_ancestries_cannot_share_entries(selected):
    database, _ = selected
    session = _begin(database)
    try:
        with recipe_original_read_scope(session):
            scope = current_recipe_read_scope()
            recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
            with generation_proof("run-contract", 2):
                recipe.read_graph_recipe_selection_v3_in_session(session, "run-contract")
            assert len([key for key in scope.values if key[0] == "selection"]) == 2
            context = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(context.run, current_recipe_read_scope).result() is None
    finally:
        session.rollback()
        session.close()

    async def check_tasks():
        session = _begin(database)
        try:
            with recipe_original_read_scope(session):
                async def child():
                    return current_recipe_read_scope()
                assert await asyncio.create_task(child()) is None
        finally:
            session.rollback()
            session.close()
    asyncio.run(check_tasks())


def test_real_callback_full_input_tokens_preserved_cancel_and_terminal_refuse_late_commit(
    active, monkeypatch
):
    prepared, submit = active
    service = callbacks.ProviderInputReceiptService(prepared.database)
    original_json = json.loads(submit.input_json)
    original_json["messages"].append({
        "source": {"kind": "user"},
        "content": [{"type": "text", "text": "保留完整输入与token上下文" * 512}],
    })
    raw = observation_json(original_json)
    submit = submit.model_copy(update={"input_json": raw, "input_hash": callbacks.sha(raw)})
    recorded = service.record_input(submit)
    with prepared.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        original, _ = callbacks.read_provider_input_in_session(session, recorded.reference)
        assert original.submit.input_json == raw and original.submit.max_tokens == submit.max_tokens
    for cause in ("cancel", "terminal", "changed_readset"):
        calls = 0
        actual_prepare = callbacks.prepare_originals

        def prepare(*args, _actual=actual_prepare, _cause=cause, **kwargs):
            proof = _actual(*args, **kwargs)
            if _cause != "cancel":
                with prepared.database.session() as session:
                    if _cause == "terminal":
                        session.get(RunRecord, submit.run_id).status = "failed"
                    else:
                        row = session.scalar(select(RunRoleOutputRecord).where(
                            RunRoleOutputRecord.run_id == submit.run_id,
                            RunRoleOutputRecord.role == legacy.TASK_ROLE,
                        ))
                        row.payload_json += " "
            return proof

        def cancel(_cause=cause):
            nonlocal calls
            calls += 1
            if _cause == "cancel" and calls == 5:
                raise ValueError("research_provider_callback_lifetime_elapsed")

        monkeypatch.setattr(callbacks, "prepare_originals", prepare)
        try:
            with pytest.raises((ValueError, PermissionError)):
                service.record_input(
                    submit.model_copy(update={"dispatch_id": cause}), request_check=cancel
                )
        finally:
            monkeypatch.setattr(callbacks, "prepare_originals", actual_prepare)
        with prepared.database.session() as session:
            assert len(session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.role.like("model_input:%")
            )).all()) == 1
            if cause == "terminal":
                session.get(RunRecord, submit.run_id).status = "running"
