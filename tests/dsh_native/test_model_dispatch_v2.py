"""Native prepared SQLite controls; no Host/Provider parameter execution claim."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.kernel.decision_hub_kernel.application import model_dispatch
from packages.kernel.decision_hub_kernel.application.model_dispatch import (
    ROLE,
    ModelDispatchService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    SCHEMA as V1_METHOD_SCHEMA,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    RunRoleOutputRecord,
)
from tests.dsh_native.test_execution_preparation_v2 import (
    PreparedInputs,
    _advance_inputs,  # pyright: ignore[reportPrivateUsage]
    _snapshot,  # pyright: ignore[reportPrivateUsage]
)
from tests.dsh_native.test_execution_preparation_v2 import (
    inputs as inputs,
)
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


def _payload(inputs: PreparedInputs, *, generation: int = 1) -> DshModelDispatchBinding:
    return DshModelDispatchBinding(
        schema_version="dsh-model-dispatch-binding.v1",
        run_id=inputs.request.run_id,
        dsh_session_id=inputs.submit.deterministic_session_id,
        request_hash=inputs.submit.request_hash,
        generation=generation,
        plugin_build_hash=inputs.upstream.plugin_build_hash,
        purpose="conversation",
        provider="controlled-declaration-only",
        model="research-model",
        reasoning_effort="high",
        temperature=None,
        max_tokens=8192,
        stop_sequences_hash=None,
    )


def _rows(database: Database, run_id: str) -> list[dict[str, Any]]:
    with database.session() as session:
        return [
            {
                "output_id": row.output_id,
                "role": row.role,
                "schema": row.schema_version,
                "payload": row.payload_json,
                "hash": row.payload_hash,
                "created_at": row.created_at.isoformat(),
            }
            for row in session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id)
            .order_by(RunRoleOutputRecord.role)
            .all()
        ]


def _observe(tmp_path: Path, **values: Any) -> None:
    (tmp_path / "observation.json").write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_actual_v2_first_root_and_descendants_keep_frozen_five(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    before = _snapshot(inputs.database, inputs.request.run_id)
    root = _payload(inputs)
    child = root.model_copy(update={
        "execution_session_id": "controlled-child",
        "parent_session_id": root.dsh_session_id,
        "model": "child-model",
    })
    descendant = child.model_copy(update={
        "execution_session_id": "controlled-grandchild",
        "parent_session_id": child.execution_session_id,
        "model": "review-model",
    })
    service = ModelDispatchService(inputs.database, clock=lambda: inputs.now)
    assert service.bind(root) == root
    with pytest.raises(ValueError, match="lineage_invalid"):
        service.bind(descendant)
    assert service.bind(child) == child
    assert service.bind(descendant) == descendant
    after = _snapshot(inputs.database, inputs.request.run_id)
    repeated = ModelDispatchService(
        inputs.database, clock=lambda: inputs.now + timedelta(seconds=2)
    )
    assert repeated.bind(root) == root
    assert repeated.bind(child) == child
    assert repeated.bind(descendant) == descendant
    assert _snapshot(inputs.database, inputs.request.run_id) == after
    assert service.get(root.run_id) == root
    assert set(item.execution_session_id for item in service.children(root.run_id)) == {
        "controlled-child", "controlled-grandchild",
    }
    old = {row["role"]: row for row in before["roles"]}
    assert [row for row in after["roles"] if row["role"] in old] == before["roles"]
    assert after["prompts"] == before["prompts"] and after["link"] == before["link"]
    assert len(after["roles"]) == len(before["roles"]) + 3
    _observe(tmp_path, before=before, after=after,
             linked_hash=prepared.method.request_hash, provider_executed=False)


@pytest.mark.parametrize("repair", [False, True])
def test_v2_latest_generation_uses_first_public_hash_and_current_full_proof(
    inputs: PreparedInputs, tmp_path: Path, repair: bool,
) -> None:
    first = inputs.prepare()
    payload = _payload(inputs)
    service = ModelDispatchService(inputs.database, clock=lambda: inputs.now)
    service.bind(payload)
    first_controls = _rows(inputs.database, payload.run_id)
    later = _advance_inputs(inputs, repair=repair)
    current = later.prepare(turn_kind="synthesis_repair" if repair else "research_round")
    latest = payload.model_copy(update={"generation": 2})
    assert current.link.generation == 2 and current.execution.request.current_round == (
        1 if repair else 2
    )
    assert current.execution.execution_request_hash != payload.request_hash
    assert current.execution.link_request_hash == first.method.request_hash == payload.request_hash
    with pytest.raises(ValueError, match="identity_mismatch"):
        service.bind(payload)
    assert service.bind(latest) == latest
    assert service.get(payload.run_id) == payload
    with pytest.raises(ValueError, match="configuration_changed_during_run"):
        service.bind(latest.model_copy(update={"model": "changed-current-model"}))
    wrong_public = latest.model_copy(update={
        "request_hash": current.execution.execution_request_hash,
    })
    with pytest.raises(ValueError, match="identity_mismatch"):
        service.bind(wrong_public)
    before_child = _rows(inputs.database, payload.run_id)
    child = latest.model_copy(update={
        "execution_session_id": "second-generation-child",
        "parent_session_id": latest.dsh_session_id,
    })
    assert service.bind(child) == child
    after = _rows(inputs.database, payload.run_id)
    assert [row for row in after if row["role"] == ROLE] == [
        row for row in first_controls if row["role"] == ROLE
    ]
    assert len(after) == len(before_child) + 1
    _observe(tmp_path, first_controls=first_controls, before_child=before_child, after=after,
             current_execution=current.execution.model_dump(mode="json"),
             public_callback=latest.model_dump(mode="json"), provider_executed=False)


@pytest.mark.parametrize("piece", [
    "research_execution_method", "research_request_pack", "research_task_request",
    "research_compiled_policy", "dsh_execution_request:1", "dsh_prompt_integrity:1",
    "prompt",
])
def test_v2_missing_native_piece_never_creates_model_controls(
    inputs: PreparedInputs, tmp_path: Path, piece: str,
) -> None:
    inputs.prepare()
    with inputs.database.session() as session:
        if piece == "prompt":
            row = session.query(DshSessionPromptRecord).filter_by(
                run_id=inputs.request.run_id, generation=1,
            ).one()
        else:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role=piece,
            ).one()
        session.delete(row)
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError) as failure:
        ModelDispatchService(inputs.database).bind(_payload(inputs))
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, piece=piece, before=before, after=after, error=str(failure.value))
    assert after == before
    assert not any(row["role"] == ROLE for row in after["roles"])


@pytest.mark.parametrize("piece", [
    "research_request_pack", "research_task_request", "research_compiled_policy",
    "dsh_execution_request:1", "dsh_prompt_integrity:1", "prompt",
])
def test_v2_native_hash_or_prompt_drift_never_creates_model_controls(
    inputs: PreparedInputs, tmp_path: Path, piece: str,
) -> None:
    inputs.prepare()
    with inputs.database.session() as session:
        if piece == "prompt":
            prompt = session.query(DshSessionPromptRecord).filter_by(
                run_id=inputs.request.run_id, generation=1,
            ).one()
            prompt.prompt += "\nChanged after preparation."
        else:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role=piece,
            ).one()
            row.payload_hash = "0" * 64
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError) as failure:
        ModelDispatchService(inputs.database).bind(_payload(inputs))
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, piece=piece, before=before, after=after, error=str(failure.value))
    assert after == before


@pytest.mark.parametrize("schema", [V1_METHOD_SCHEMA, "research-execution-method.unknown"])
def test_prepared_footprint_cannot_downgrade_through_legacy_or_unknown_method(
    inputs: PreparedInputs, tmp_path: Path, schema: str,
) -> None:
    inputs.prepare()
    with inputs.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role="research_execution_method",
        ).one()
        body = {
            "schema_version": schema, "run_id": row.run_id,
            "request_hash": inputs.submit.request_hash,
            "definition": {
                "upstream_identity": inputs.upstream.model_dump(mode="json"),
            },
        }
        body["method_hash"] = hashlib.sha256(
            json.dumps(body["definition"], ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()
        ).hexdigest()
        row.schema_version = schema
        row.payload_json = json.dumps(body, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"))
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError) as failure:
        ModelDispatchService(inputs.database).bind(_payload(inputs))
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, schema=schema, before=before, after=after, error=str(failure.value))
    assert after == before


def test_fresh_same_write_session_does_not_certify_stale_method_identity_map(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    stale = inputs.database.factory()
    try:
        cached = stale.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role="research_execution_method",
        ).one()
        stale.commit()  # End read transaction; retain unexpired ORM object.
        with inputs.database.session() as other:
            row = other.query(RunRoleOutputRecord).filter_by(output_id=cached.output_id).one()
            row.payload_hash = "0" * 64
        assert cached.payload_hash != "0" * 64

        @contextmanager
        def reused_session() -> Generator[Session]:
            try:
                yield stale
                stale.commit()
            except BaseException:
                stale.rollback()
                raise

        before = _snapshot(inputs.database, inputs.request.run_id)
        with monkeypatch.context() as scoped:
            scoped.setattr(inputs.database, "session", reused_session)
            with pytest.raises(ValueError):
                ModelDispatchService(inputs.database).bind(_payload(inputs))
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, before=before, after=after, cached_before_stale=True)
        assert after == before
    finally:
        stale.close()


def test_complete_native_reader_and_model_insert_use_one_immediate_transaction(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    # The new seam is intentionally checked after the V2 functional first-red gate.
    reader = model_dispatch.read_prepared_execution_in_session
    connections: list[int] = []
    stages: list[str] = []

    class Lease:
        sealed = False

        def pin(self, source: Any) -> Any:
            stages.append("source_pinned")

            class Guard:
                def matches(_self) -> bool:
                    assert self.sealed
                    stages.append("sealed_source_match")
                    return True

            return Guard()

        def extend_authenticated_selection(self, session: Session, run_id: str) -> None:
            assert run_id == inputs.request.run_id
            assert session.in_transaction()
            stages.append("selection_extended")

        def seal_authenticated_selection(self, run_id: str) -> None:
            assert run_id == inputs.request.run_id
            assert stages[-1] == "selection_extended"
            self.sealed = True
            stages.append("selection_sealed")

    original_begin = model_dispatch.begin_immediate

    def watched_begin(session: Session) -> None:
        original_begin(session)
        stages.append("writer_acquired")

    original_after = preparation.PreparedOriginals.verify_after_writes

    def watched_after(
        self: Any, session: Session, *, diagnostics: Any = None, checkpoint: Any = None,
    ) -> None:
        assert checkpoint is request_check
        stages.append("after_write_checkpoint")
        original_after(self, session, diagnostics=diagnostics, checkpoint=checkpoint)

    def request_check() -> None:
        return None

    def writer_start() -> None:
        assert stages[-1] == "writer_acquired"
        stages.append("writer_budget_started")

    def watched(session: Session, run_id: str) -> FrozenExecutionPreparation:
        connection = session.connection().connection.driver_connection
        assert isinstance(connection, sqlite3.Connection) and connection.in_transaction
        assert session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=ROLE).count() == 0
        connections.append(id(connection))
        return reader(session, run_id)

    monkeypatch.setattr(model_dispatch, "read_prepared_execution_in_session", watched)
    monkeypatch.setattr(model_dispatch, "begin_immediate", watched_begin)
    monkeypatch.setattr(preparation.PreparedOriginals, "verify_after_writes", watched_after)
    assert ModelDispatchService(inputs.database).bind(
        _payload(inputs), request_check=request_check, writer_start=writer_start,
        source_lease=Lease(),
    ) == _payload(inputs)
    assert len(connections) == 1
    assert [stage for stage in stages if stage != "sealed_source_match"] == [
        "source_pinned", "selection_extended", "selection_sealed", "writer_acquired",
        "writer_budget_started", "after_write_checkpoint",
    ]
    assert stages.count("sealed_source_match") == 3
    assert stages.index("selection_sealed") < stages.index("sealed_source_match")
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, reader_connections=connections, after=after, immediate_transaction=True)


@pytest.mark.parametrize("fault", ["missing_current", "changed_current_hash"])
def test_latest_native_piece_is_rechecked_before_new_child_or_root_replay(
    inputs: PreparedInputs, tmp_path: Path, fault: str,
) -> None:
    inputs.prepare()
    payload = _payload(inputs)
    service = ModelDispatchService(inputs.database)
    service.bind(payload)
    later = _advance_inputs(inputs, repair=True)
    later.prepare(turn_kind="synthesis_repair")
    with inputs.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=payload.run_id, role="dsh_execution_request:2",
        ).one()
        if fault == "missing_current":
            session.delete(row)
        else:
            row.payload_hash = "0" * 64
    before = _snapshot(inputs.database, payload.run_id)
    latest = payload.model_copy(update={"generation": 2})
    child = latest.model_copy(update={
        "execution_session_id": "never-admitted-child",
        "parent_session_id": latest.dsh_session_id,
    })
    for candidate in (latest, child):
        with pytest.raises(ValueError):
            service.bind(candidate)
    after = _snapshot(inputs.database, payload.run_id)
    _observe(tmp_path, fault=fault, before=before, after=after, new_child_count=0)
    assert after == before and service.get(payload.run_id) == payload
