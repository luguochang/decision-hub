"""Bounded OCC with real frozen Task/Selection/Method and independent SQLite writes.

One genuine bootstrap is cloned for the cases. No source verifier, original
reader or permission result is mocked. Injection only controls the commit
between original preparation and its fresh read fence. No Host/model is called.
"""
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ResearchTraceEvent
from packages.kernel.decision_hub_kernel.application import research_graph_recipe
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    OriginalPreparationReadsetChanged,
)
from packages.runtime_adapters.dsh_runtime import research_runtime_binding as binding
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from tests.orchestration.test_graph_observed_result_v2 import OWNER, _active, _prepared_inputs
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@pytest.fixture(scope="module")
def native_baseline(tmp_path_factory):
    directory = tmp_path_factory.mktemp("selected-occ-original")
    generator = _prepared_inputs(directory, max_rounds=1)
    inputs = next(generator)
    try:
        _active(inputs)
        selected = ContextSelectionService(inputs.database).select(
            run_id=inputs.request.run_id, graph_round=1, expected_lease_owner=OWNER,
        )
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            anchor = binding.read_runtime_task_anchor(session, inputs.request.run_id)
            binding.prepare_runtime_turn_in_session(
                session, inputs.database, anchor, selected.request, inputs.upstream,
                runtime_id="dsh",
                runtime_version=(
                    f"dsh-web-{inputs.upstream.source_version}+"
                    f"{inputs.upstream.plugin_build_hash[:12]}"
                ),
                profile_ref="decision-research.web.v1", prompt_policy_hash=PROMPT_POLICY_HASH,
                selection_reference=selected.reference, expected_lease_owner=OWNER,
            )
            # Keep the original request deadline; do not lengthen the method.
            session.get(RunRecord, inputs.request.run_id).lease_expires_at = (
                selected.request.deadline_at
            )
        with inputs.database.session() as session:
            anchor = binding.read_runtime_task_anchor(session, inputs.request.run_id)
        assert anchor.generation == 1 and anchor.requires_selection
        yield inputs, selected, anchor
    finally:
        generator.close()


def _clone(inputs, path):
    with sqlite3.connect(inputs.database.engine.url.database) as source:
        with sqlite3.connect(path) as destination:
            source.backup(destination)
    return Database(f"sqlite+pysqlite:///{path.as_posix()}")


def _observation(database, run_id, sequence):
    """Typed local baseline completion, not a claim of a real provider result."""
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        session_id = link.dsh_session_id
        # This unmasked reference can legitimately advance during authentication.
        link.trace_ref = f"hub://runs/{run_id}/baseline/controlled-{sequence}"
    ResearchObservabilityService(database).append_trace(ResearchTraceEvent(
        schema_version="research-trace-event.v1", run_id=run_id,
        research_session_id=session_id, sequence_no=0, event_type="tool_completed",
        occurred_at=datetime.now(UTC), stage="evidence",
        summary="Controlled baseline observation for concurrency verification",
        reference_type="tool_call", reference_id=f"controlled-baseline-{sequence}",
        status="succeeded", error_code=None,
    ))


@pytest.mark.parametrize("change", [
    "observation", "twice", "owner", "cancel", "lease", "selection",
    "method", "capability", "deadline", "source", "unselected_owner",
])
def test_selected_native_occ_keeps_frozen_authority(
    native_baseline, tmp_path, monkeypatch, caplog, change,
):
    inputs, frozen, anchor = native_baseline
    database = _clone(inputs, tmp_path / "case.sqlite3")
    selected = (frozen.reference, OWNER, json.dumps(
        frozen.request.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ))
    if change == "unselected_owner":
        selected = None
    original_prepare = binding.prepare_originals
    original_scope = binding.callback_source_lease_scope
    prepared_values, scopes, worker_threads = [], [], []
    expected_deadline = frozen.request.deadline_at
    original_clock = binding.datetime

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (expected_deadline + timedelta(microseconds=1)).astimezone(tz or UTC)

    @contextmanager
    def observed_scope(*args, **kwargs):
        assert not scopes or scopes[-1]["closed"], "old source scope leaked into retry"
        entry = {"closed": False, "thread": threading.get_ident()}
        scopes.append(entry)
        try:
            with original_scope(*args, **kwargs) as lease:
                yield lease
        finally:
            entry["closed"] = True
            # Mutate after the real old source handles and snapshots are closed.
            if len(scopes) == 1 and change == "deadline":
                monkeypatch.setattr(binding, "datetime", ExpiredClock)
            elif len(scopes) == 1 and change == "source":
                empty_source = tmp_path / "different-source-root"
                empty_source.mkdir()
                monkeypatch.setattr(research_graph_recipe, "_ROOT", empty_source)

    def prepare_then_commit(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        prepared = original_prepare(*args, **kwargs)
        prepared_values.append(prepared)
        if len(prepared_values) == 1 or change == "twice":
            _observation(database, anchor.run_id, len(prepared_values))
            with database.session() as session:
                run = session.get(RunRecord, anchor.run_id)
                if change in {"owner", "unselected_owner"}:
                    run.lease_owner = "different-owner-must-not-be-adopted"
                elif change == "cancel":
                    run.status = "cancelled"
                elif change == "lease":
                    run.lease_expires_at = original_clock.now(UTC) - timedelta(seconds=1)
                elif change in {"selection", "method", "capability"}:
                    role = frozen.reference.role if change == "selection" else (
                        "research_execution_method"
                    )
                    row = session.scalar(select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == anchor.run_id,
                        RunRoleOutputRecord.role == role,
                    ))
                    assert row is not None
                    if change == "capability":
                        body = json.loads(row.payload_json)
                        assert body["definition"]["allowed_capabilities"]
                        body["definition"]["allowed_capabilities"] = []
                        row.payload_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
                        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
                    else:
                        # Even semantically equivalent altered raw bytes must fail.
                        row.payload_json += " "
        return prepared

    monkeypatch.setattr(binding, "callback_source_lease_scope", observed_scope)
    monkeypatch.setattr(binding, "prepare_originals", prepare_then_commit)
    try:
        # Exactly one native Future owns both attempts; the function makes no RPC.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(binding.authenticate_runtime_binding,
                database, anchor, selected, deadline_at=expected_deadline)
            if change == "observation":
                assert future.result(timeout=240) is None
                assert len(prepared_values) == 2 and len(scopes) == 2
            else:
                with pytest.raises(ValueError) as raised:
                    future.result(timeout=240)
                if change in {"twice", "unselected_owner"}:
                    assert isinstance(raised.value, OriginalPreparationReadsetChanged)
                if change == "twice":
                    assert len(prepared_values) == len(scopes) == 2
                elif change == "unselected_owner":
                    assert len(prepared_values) == len(scopes) == 1
                elif change == "cancel":
                    assert str(raised.value) == "research_execution_run_not_active"
                elif change in {"owner", "lease"}:
                    assert "permission_lost" in str(raised.value)
                elif change == "deadline":
                    assert "deadline_elapsed" in str(raised.value)
                elif change == "source":
                    assert "source" in str(raised.value)
                else:
                    assert not isinstance(raised.value, OriginalPreparationReadsetChanged), (
                        "second attempt must perform full immutable authentication"
                    )
        assert len(set(worker_threads)) == 1
        assert all(item["closed"] for item in scopes)
        assert all(item["thread"] == worker_threads[0] for item in scopes)
        assert all(value is not other for index, value in enumerate(prepared_values)
                   for other in prepared_values[index + 1:])
        warnings = [record.getMessage() for record in caplog.records
                    if "research_binding_readset_conflict" in record.getMessage()]
        assert warnings and "query_fingerprint=" in warnings[0]
        assert "SELECT " not in "\n".join(warnings)
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 0
    finally:
        database.engine.dispose()
