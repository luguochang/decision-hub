"""Full offline Factory/Method/Graph/Commit, then bounded audit negatives.

The existing typed offline Host supplies controlled data. No Provider, HTTP,
live database or model is used. Audit rows come from the actual usage producer;
they are unavailable observations, never pricing or cancellation proof.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application import research_policy_checker as checker
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchCommandRecord,
    ResearchUsageSnapshotRecord,
    RunRoleOutputRecord,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from packages.workbench_adapters.usage_ledger import capture_usage
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    _active,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_graph_citation_membership_v2 import (
    SPOT_A,
    SnapshotCitationHost,
    snapshot_inputs,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None

# Private audit codecs are the exact production functions under test.
_Snapshot = checker._TrajectorySnapshot  # pyright: ignore[reportPrivateUsage]
_canonical = checker._trajectory_json  # pyright: ignore[reportPrivateUsage]
_encoded = checker._trajectory_encoded  # pyright: ignore[reportPrivateUsage]
_with_usage = checker._trajectory_with_usage_rows  # pyright: ignore[reportPrivateUsage]
_saved = checker._trajectory_read_saved  # pyright: ignore[reportPrivateUsage]
_store = checker._trajectory_store  # pyright: ignore[reportPrivateUsage]
_monotonic = checker._trajectory_monotonic  # pyright: ignore[reportPrivateUsage]
_materials = checker._policy_materials  # pyright: ignore[reportPrivateUsage]
_MAX_BYTES = checker._TRAJECTORY_MAX_BYTES  # pyright: ignore[reportPrivateUsage]


class _Producer(Protocol):
    __wrapped__: Callable[[Path], Generator[PreparedInputs]]


@dataclass(frozen=True)
class Joint:
    source: PreparedInputs
    reference: binding.GraphFinalProjectionRef
    trajectory: Any
    payload: str


def _seed_usage(source: PreparedInputs, count: int) -> None:
    for index in range(count):
        capture_usage(
            source.database,
            Path("unused-offline-usage-root"),
            source.request.run_id,
            f"trajectory-controlled-{index:03d}",
            prepared_view={
                "schema_version": "research-usage-view.v1",
                "run_id": source.request.run_id,
                "status": "unavailable",
                "sessions": [],
                "total_run_tokens": None,
                "estimated_cost_usd": None,
                "limitations": ["controlled_archive_unavailable"],
                "observed_at": datetime.now(UTC).isoformat(),
            },
        )


@pytest.fixture(scope="module")
async def joint(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Joint]:
    directory = tmp_path_factory.mktemp("trajectory-full-joint30")
    producer = cast(_Producer, snapshot_inputs).__wrapped__(directory)
    source = next(producer)
    runtime = None
    try:
        _seed_usage(source, 38)
        request = _active(source)
        runtime = DshWebResearchRuntime(
            DshSessionLinkService(source.database),
            SnapshotCitationHost(source, top_ref=SPOT_A),
            poll_interval_seconds=0.001,
        )
        graph = build_agentic_research_graph(
            runtime, ResearchEvidenceService(source.database), expected_lease_owner=OWNER
        )
        state = await graph.ainvoke(cast(Any, initial_research_state(request)))
        reference = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
        with source.database.session() as session:
            final = binding.read_graph_final_in_session(
                session,
                run_id=request.run_id,
                reference=reference,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
        result = final.result
        # Retain current policy's disposition. This older typed offline Host
        # does not produce Provider evidence attestation; no accepted claim.
        assert (
            cast(binding.ResearchGraphFinalProjectionV2, final.receipt).citation_disposition
            == "not_applicable"
        )
        assert result.status == "degraded"
        review = ResearchReviewService(source.database).review(
            source.request.run_id, result, graph_final_ref=reference, expected_lease_owner=OWNER
        )
        CommitDecisionService(source.database).commit_research_result(
            source.request.run_id,
            source.request.event_id,
            result.model_copy(update={"reviews": [review]}),
            graph_final_ref=reference,
            expected_lease_owner=OWNER,
        )
        with source.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=source.request.run_id,
                    schema_version="research-policy-trajectory-inputs.v1",
                )
                .filter(RunRoleOutputRecord.role.like("policy_trajectory:%"))
                .all()
            )
            assert len(row) == 2
            value = next(
                _Snapshot.model_validate_json(item.payload_json)
                for item in row
                if json.loads(item.payload_json)["phase"] == "commit"
            )
            payload = _saved(
                session,
                run_id=value.run_id,
                graph_ref_json=value.graph_ref_json,
                method_hash=value.method_hash,
                phase="commit",
            )
            assert payload is not None
            session.rollback()
        yield Joint(source, reference, value, payload)
    finally:
        if runtime is not None:
            await runtime.close()
        producer.close()


@pytest.fixture
def private_copy(joint: Joint, tmp_path: Path) -> Generator[Database]:
    path = joint.source.database.sqlite_path
    assert path is not None
    copied = tmp_path / "audit.sqlite3"
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as reader:
        with sqlite3.connect(copied) as writer:
            reader.backup(writer)
    database = Database(f"sqlite+pysqlite:///{copied.as_posix()}")
    try:
        yield database
    finally:
        database.engine.dispose()


def _fresh(session: Session, value: Any) -> Any:
    session.connection().exec_driver_sql("BEGIN")
    return _with_usage(session, value.model_copy(update={"usage_rows": ()}))


def test_actual_factory_graph_method_commit_reads_all_38_audit_originals(joint: Joint) -> None:
    value = joint.trajectory
    assert value.schema_version == "research-policy-trajectory-inputs.v1"
    assert len(value.usage_rows) == 38 and value.command_rows == ()
    assert value.verified_total_tokens is value.verified_estimated_cost_usd is None
    assert value.verified_cancel_drain is None
    assert _encoded(value) == joint.payload and len(joint.payload.encode()) < _MAX_BYTES
    with joint.source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        assert isinstance(native, sqlite3.Connection)
        before = native.total_changes
        rows = (
            session.query(ResearchUsageSnapshotRecord)
            .filter_by(run_id=value.run_id)
            .order_by(
                ResearchUsageSnapshotRecord.recorded_at, ResearchUsageSnapshotRecord.snapshot_id
            )
            .all()
        )
        assert [item.locator for item in value.usage_rows] == [item.snapshot_id for item in rows]
        assert [json.loads(item.payload_json) for item in value.usage_rows] == [
            binding._columns(item)  # pyright: ignore[reportPrivateUsage]
            for item in rows
        ]
        assert (
            _saved(
                session,
                run_id=value.run_id,
                graph_ref_json=value.graph_ref_json,
                method_hash=value.method_hash,
                phase="commit",
            )
            == joint.payload
        )
        assert native.total_changes == before
        session.rollback()


def test_usage_hash_byte_bound_monotonic_and_saved_v1_are_strict(
    joint: Joint,
    private_copy: Database,
) -> None:
    value = joint.trajectory
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(ResearchUsageSnapshotRecord, value.usage_rows[32].locator)
        assert row is not None
        original = row.payload_sha256
        row.payload_sha256 = "0" * 64
        session.flush()
        with pytest.raises(ValueError, match="^research_policy_trajectory_usage_original_invalid$"):
            _with_usage(session, value.model_copy(update={"usage_rows": ()}))
        row.payload_sha256 = original
        session.flush()
        assert (
            _encoded(_with_usage(session, value.model_copy(update={"usage_rows": ()})))
            == joint.payload
        )
        session.rollback()
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(ResearchUsageSnapshotRecord, value.usage_rows[0].locator)
        assert row is not None
        session.delete(row)
        session.flush()
        with pytest.raises(ValueError, match="^research_policy_trajectory_audit_original_changed$"):
            _monotonic(value, _with_usage(session, value.model_copy(update={"usage_rows": ()})))
        session.rollback()
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(ResearchUsageSnapshotRecord, value.usage_rows[0].locator)
        assert row is not None
        row.payload_json = _canonical({"audit": "x" * _MAX_BYTES})
        row.payload_sha256 = hashlib.sha256(row.payload_json.encode()).hexdigest()
        session.flush()
        with pytest.raises(ValueError, match="^research_policy_trajectory_bytes_exceeded$"):
            _with_usage(session, value.model_copy(update={"usage_rows": ()}))
        session.rollback()
    # The prior <=32 v1 codec is identical, with no new field or stamp.
    old = value.model_copy(update={"usage_rows": value.usage_rows[:32], "phase": "finalization"})
    old_payload = _canonical(old.model_dump(mode="json"))
    assert _encoded(_Snapshot.model_validate_json(old_payload)) == old_payload
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        # Remove only the isolated fixture's existing phase original, then store
        # the unchanged old-v1 bytes through the real immutable writer.
        session.query(RunRoleOutputRecord).filter_by(
            run_id=old.run_id, schema_version="research-policy-trajectory-inputs.v1"
        ).filter(RunRoleOutputRecord.role.like("policy_trajectory:%")).delete()
        session.query(RunRoleOutputRecord).filter_by(run_id=old.run_id).filter(
            RunRoleOutputRecord.role.like("policy_check:finalization:%")
        ).delete()
        _store(session, old)
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        assert (
            _saved(
                session,
                run_id=old.run_id,
                graph_ref_json=old.graph_ref_json,
                method_hash=old.method_hash,
                phase="finalization",
            )
            == old_payload
        )
        session.rollback()
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="^research_policy_trajectory_original_conflict$"):
            _store(session, old.model_copy(update={"usage_rows": value.usage_rows}))
        session.rollback()
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=old.run_id, schema_version="research-policy-trajectory-inputs.v1")
            .one()
        )
        row.payload_json += " "
        session.flush()
        with pytest.raises(ValueError, match="^research_policy_trajectory_saved_invalid$"):
            _saved(
                session,
                run_id=old.run_id,
                graph_ref_json=old.graph_ref_json,
                method_hash=old.method_hash,
                phase="finalization",
            )
        session.rollback()


def test_commands_33_still_fail_in_actual_original_materials(
    joint: Joint, private_copy: Database
) -> None:
    value = joint.trajectory
    with private_copy.session() as session:
        for index in range(33):
            session.add(
                ResearchCommandRecord(
                    request_id=f"controlled-command-{index:03d}",
                    run_id=value.run_id,
                    command="cancel",
                    reason="controlled audit only",
                    target_run_id=None,
                    status="accepted",
                    created_at=value.assessed_at + timedelta(microseconds=index),
                )
            )
    with private_copy.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with pytest.raises(ValueError, match="^research_policy_trajectory_audit_limit$"):
            with _materials(session, run_id=value.run_id, graph_ref=joint.reference):
                pytest.fail("command audit cap must reject before assessment")
        session.rollback()
