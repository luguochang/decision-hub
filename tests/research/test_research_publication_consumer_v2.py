"""Genuine offline Factory/Runtime/Graph assets at review/publication writers."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import ResearchReview, ResearchSessionResult
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application import commit as commit_module
from packages.kernel.decision_hub_kernel.application import research_graph_binding as graph_binding
from packages.kernel.decision_hub_kernel.application.commit import (
    CommitDecisionService,
    _project_research_candidate,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
    read_graph_final_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.decision.gate import GateResult, evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ExecutionPermissionRecord,
    ForecastRecord,
    GateDecisionRecord,
    OutboxRecord,
    PublicationDecisionRecord,
    ReportRevisionRecord,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    _active,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


@dataclass(frozen=True)
class ActualGraphFinal:
    reference: GraphFinalProjectionRef
    result: ResearchSessionResult
    host_submits: int
    state: dict[str, Any]


async def _actual_final(source: PreparedInputs) -> ActualGraphFinal:
    request = _active(source)
    host = PreparedHost(source)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(source.database),
        host,
        poll_interval_seconds=0.001,
    )
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(source.database),
        expected_lease_owner=OWNER,
    )
    try:
        state = await graph.ainvoke(initial_research_state(request))
        reference = GraphFinalProjectionRef.model_validate_json(
            json.dumps(state["graph_final_projection_ref"]),
        )
        result = ResearchSessionResult.model_validate(state["final_result"])
        with source.database.session() as session:
            original = read_graph_final_in_session(
                session,
                run_id=source.request.run_id,
                reference=reference,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
        assert original.result == result and host.submit_calls
        return ActualGraphFinal(reference, result, len(host.submit_calls), dict(state))
    finally:
        await runtime.close()


def _sql(source: PreparedInputs) -> dict[str, Any]:
    models = [
        ArtifactRecord,
        ResearchResultRecord,
        AnalyticalOutlookRecord,
        ForecastRecord,
        GateDecisionRecord,
        OutboxRecord,
        RunRoleOutputRecord,
        PublicationDecisionRecord,
        ExecutionPermissionRecord,
        ReportRevisionRecord,
    ]
    with source.database.session() as session:
        run = session.get(RunRecord, source.request.run_id, populate_existing=True)
        assert run is not None
        return {
            "run": {column.key: getattr(run, column.key) for column in run.__table__.columns},
            "tables": {
                model.__tablename__: [
                    {column.key: getattr(row, column.key) for column in model.__table__.columns}
                    for row in session.query(model).order_by(*model.__mapper__.primary_key).all()
                ]
                for model in models
            },
        }


def _observe(path: Path, **body: Any) -> None:
    (path / "publication-observation.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


async def test_actual_graph_final_is_readable_before_any_publication_consumer(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    final = await _actual_final(inputs)
    observed = _sql(inputs)
    _observe(
        tmp_path,
        final_ref=final.reference.model_dump(mode="json"),
        result=final.result.model_dump(mode="json"),
        host_submits=final.host_submits,
        sql=observed,
    )
    assert observed["tables"]["research_results"] == []
    assert observed["tables"]["artifacts"] == []
    assert any(
        row["role"] == "research_graph_final" for row in observed["tables"]["run_role_outputs"]
    )


@pytest.mark.parametrize("consumer", ["review", "commit"])
async def test_v2_missing_original_final_ref_cannot_write_any_publication_state(
    inputs: PreparedInputs,
    tmp_path: Path,
    consumer: str,
) -> None:
    final = await _actual_final(inputs)
    before = _sql(inputs)
    error = None
    returned = None
    try:
        # Use the already-existing public entry without future keywords. The
        # actual Graph result is lawful; the omitted source locator is the gap.
        if consumer == "review":
            returned = (
                ResearchReviewService(inputs.database)
                .review(
                    inputs.request.run_id,
                    final.result,
                )
                .model_dump(mode="json")
            )
        else:
            returned = CommitDecisionService(inputs.database).commit_research_result(
                inputs.request.run_id,
                inputs.request.event_id,
                final.result,
                expected_lease_owner=OWNER,
            )[0]
    except (ValueError, PermissionError) as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    after = _sql(inputs)
    _observe(
        tmp_path,
        consumer=consumer,
        final_ref=final.reference.model_dump(mode="json"),
        original_result=final.result.model_dump(mode="json"),
        error=error,
        returned=returned,
        host_submits=final.host_submits,
        before=before,
        after=after,
    )
    assert error is not None, "V2 consumer wrote without the original Graph final locator"
    assert error["message"] == "research_graph_publication_reference_required"
    assert before == after


async def test_v2_review_and_publication_refuse_caller_artifact_and_gate_then_replay_readonly(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    final = await _actual_final(inputs)
    reviews = ResearchReviewService(inputs.database)
    service = CommitDecisionService(inputs.database)
    original = _sql(inputs)
    with pytest.raises(PermissionError, match="research_graph_publication_owner_required"):
        reviews.review(inputs.request.run_id, final.result, graph_final_ref=final.reference)
    assert _sql(inputs) == original
    with pytest.raises(ValueError, match="research_graph_publication_review_required"):
        service.commit_research_result(
            inputs.request.run_id,
            inputs.request.event_id,
            final.result,
            graph_final_ref=final.reference,
            expected_lease_owner=OWNER,
        )
    assert _sql(inputs) == original
    review = reviews.review(
        inputs.request.run_id,
        final.result,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    assert review.resolution == "bounded"
    reviewed = final.result.model_copy(update={"reviews": [review]})
    # The genuine controlled Graph result is incomplete. The exact legacy
    # projection is shared with the writer; caller text/status cannot override it.
    candidate = _project_research_candidate(reviewed, False)
    gate = evaluate_gate(candidate)
    after_review = _sql(inputs)
    rejected: list[dict[str, Any]] = []
    for case, changed_candidate, changed_gate, code in [
        (
            "candidate",
            {**candidate, "headline": "Controlled unbound caller Artifact"},
            gate,
            "research_graph_publication_candidate_mismatch",
        ),
        (
            "gate",
            candidate,
            GateResult(status=GateStatus.publish, decisions=gate.decisions),
            "research_graph_publication_gate_mismatch",
        ),
    ]:
        with pytest.raises(ValueError, match=code):
            service.commit(
                inputs.request.run_id,
                inputs.request.event_id,
                changed_candidate,
                changed_gate,
                research_result=reviewed,
                horizons=list(reviewed.horizons),
                graph_final_ref=final.reference,
                expected_lease_owner=OWNER,
            )
        after = _sql(inputs)
        rejected.append({"case": case, "sql": after})
        assert after == after_review
    artifact_id, actual_gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    published = _sql(inputs)
    source = [
        row
        for row in published["tables"]["run_role_outputs"]
        if row["role"] == "research_publication_source"
    ]
    assert len(source) == 1 and actual_gate.status != GateStatus.publish
    repeated_id, repeated_gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    after_replay = _sql(inputs)
    _observe(
        tmp_path,
        original_graph_ref=final.reference.model_dump(mode="json"),
        original=original,
        after_review=after_review,
        rejected=rejected,
        published=published,
        after_replay=after_replay,
        artifact_id=artifact_id,
        gate=actual_gate.status.value,
        host_submits=final.host_submits,
    )
    assert repeated_id == artifact_id and repeated_gate == actual_gate
    assert after_replay == published


async def test_v2_borrowed_review_and_publication_flushes_rollback_before_final_fence(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = await _actual_final(inputs)
    reviews = ResearchReviewService(inputs.database)
    before = _sql(inputs)
    stages: list[dict[str, Any]] = []
    for consumer in ("review", "commit"):
        with pytest.raises(ValueError, match="research_graph_publication_reference_required"):
            if consumer == "review":
                reviews.review(inputs.request.run_id, final.result)
            else:
                CommitDecisionService(inputs.database).commit_research_result(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    final.result,
                    expected_lease_owner=OWNER,
                )
        assert _sql(inputs) == before
        stages.append({"seam": f"missing_ref_{consumer}", "sql_unchanged": True})
    with pytest.raises(OSError, match="controlled review after real flush"):
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reviews.review_in_session(
                session,
                inputs.request.run_id,
                final.result,
                graph_final_ref=final.reference,
                expected_lease_owner=OWNER,
            )
            assert (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=inputs.request.run_id,
                    role="independent_reviewer",
                )
                .count()
                == 1
            )
            stages.append({"seam": "review_flushed", "count": 1})
            raise OSError("controlled review after real flush")
    assert _sql(inputs) == before
    review = reviews.review(
        inputs.request.run_id,
        final.result,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    reviewed = final.result.model_copy(update={"reviews": [review]})
    after_review = _sql(inputs)
    original_store = commit_module._store_publication_source_in_session  # pyright: ignore[reportPrivateUsage]
    for failure in ("io", "expiry", "owner", "cancel"):

        def reject_after_real_source(
            session: Session,
            *,
            _failure: str = failure,
            **kwargs: Any,
        ) -> None:
            original_store(session, **kwargs)
            session.flush()
            counts = {
                "result": session.query(ResearchResultRecord).count(),
                "artifact": session.query(ArtifactRecord).count(),
                "outbox": session.query(OutboxRecord).count(),
                "source": session.query(RunRoleOutputRecord)
                .filter_by(role="research_publication_source")
                .count(),
            }
            assert counts == {"result": 1, "artifact": 1, "outbox": 1, "source": 1}
            stages.append({"seam": f"source_flushed_{_failure}", "actual_counts": counts})
            if _failure == "io":
                raise OSError("controlled publication after real source flush")
            values: dict[str, Any] = (
                {"lease_expires_at": utcnow() - timedelta(seconds=1)}
                if _failure == "expiry"
                else {"lease_owner": "controlled-next-owner"}
                if _failure == "owner"
                else {"status": "cancelled"}
            )
            session.execute(
                update(RunRecord)
                .where(RunRecord.run_id == inputs.request.run_id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )

        with monkeypatch.context() as patch:
            patch.setattr(
                commit_module, "_store_publication_source_in_session", reject_after_real_source
            )
            with pytest.raises((OSError, PermissionError)):
                CommitDecisionService(inputs.database).commit_research_result(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    reviewed,
                    graph_final_ref=final.reference,
                    expected_lease_owner=OWNER,
                )
        assert _sql(inputs) == after_review
    _observe(
        tmp_path,
        original_graph_ref=final.reference.model_dump(mode="json"),
        before=before,
        after_review=after_review,
        after=_sql(inputs),
        stages=stages,
        host_submits=final.host_submits,
    )


async def test_v2_completed_original_artifact_never_backfills_missing_or_changed_sources(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    final = await _actual_final(inputs)
    review = ResearchReviewService(inputs.database).review(
        inputs.request.run_id,
        final.result,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    reviewed = final.result.model_copy(update={"reviews": [review]})
    service = CommitDecisionService(inputs.database)
    artifact_id, _gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    published = _sql(inputs)
    rejected: list[dict[str, Any]] = []
    # Both public Result writer entrances must remain read-only on a terminal
    # original Artifact. No pure historical Graph read grants missing-row writes.
    for entrance in ("project", "direct"):
        with inputs.database.session() as session:
            row = session.get(ResearchResultRecord, inputs.request.run_id)
            assert row is not None
            saved = {column.key: getattr(row, column.key) for column in row.__table__.columns}
            session.delete(row)
        missing = _sql(inputs)
        with pytest.raises(ValueError, match="research_publication_source_original_mismatch"):
            if entrance == "project":
                service.commit_research_result(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    reviewed,
                    graph_final_ref=final.reference,
                )
            else:
                candidate = _project_research_candidate(reviewed, False)
                service.commit(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    candidate,
                    evaluate_gate(candidate),
                    research_result=reviewed,
                    graph_final_ref=final.reference,
                )
        assert _sql(inputs) == missing
        rejected.append({"case": f"missing_result_{entrance}", "sql": missing})
        with inputs.database.session() as session:
            session.add(ResearchResultRecord(**saved))
    with inputs.database.session() as session:
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=inputs.request.run_id, role="research_publication_source")
            .one()
        )
        source_body = {
            column.key: getattr(source, column.key) for column in source.__table__.columns
        }
        session.delete(source)
    missing_source = _sql(inputs)
    with pytest.raises(ValueError, match="research_publication_source_missing"):
        service.commit_research_result(
            inputs.request.run_id,
            inputs.request.event_id,
            reviewed,
            graph_final_ref=final.reference,
        )
    assert _sql(inputs) == missing_source
    rejected.append({"case": "missing_source", "sql": missing_source})
    with inputs.database.session() as session:
        session.add(RunRoleOutputRecord(**source_body))
        artifact = session.get(ArtifactRecord, artifact_id)
        assert artifact is not None
        saved_summary = artifact.summary
        artifact.summary = "Controlled caller text replacing the original Artifact body"
    changed = _sql(inputs)
    with pytest.raises(ValueError, match="research_publication_source_original_mismatch"):
        service.commit_research_result(
            inputs.request.run_id,
            inputs.request.event_id,
            reviewed,
            graph_final_ref=final.reference,
        )
    assert _sql(inputs) == changed
    rejected.append({"case": "changed_artifact_body", "sql": changed})
    with inputs.database.session() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        assert artifact is not None
        artifact.summary = saved_summary
    assert _sql(inputs) == published
    _observe(
        tmp_path,
        original_graph_ref=final.reference.model_dump(mode="json"),
        published=published,
        rejected=rejected,
        restored=_sql(inputs),
        host_submits=final.host_submits,
    )


@pytest.mark.parametrize("consumer", ["review", "direct", "project"])
async def test_v2_actual_rows_inserted_after_legacy_dispatch_cannot_cross_legacy_writer(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
) -> None:
    final = await _actual_final(inputs)
    original = _sql(inputs)
    with inputs.database.session() as session:
        rows = session.query(RunRoleOutputRecord).filter_by(run_id=inputs.request.run_id).all()
        frozen = [
            {column.key: getattr(row, column.key) for column in row.__table__.columns}
            for row in rows
        ]
        for row in rows:
            session.delete(row)
    # These are actual producer rows, retaining every original raw byte/hash/id/time.
    # No caller-created success receipt or mocked classification is inserted.
    assert frozen and any(row["role"] == "research_graph_final" for row in frozen)
    absent = _sql(inputs)
    other = Database(inputs.database.engine.url.render_as_string(hide_password=False))
    reached = threading.Event()
    restored = threading.Event()
    observations: list[dict[str, Any]] = []
    original_dispatch = graph_binding.graph_requires_native_binding_in_session
    false_count = 0

    def restore_actual_rows() -> None:
        assert reached.wait(15), "controlled dispatch boundary was not reached"
        try:
            with other.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                observations.append(
                    {
                        "second_writer_driver": id(
                            session.connection().connection.driver_connection
                        ),
                        "restored_rows": frozen,
                    }
                )
                for body in frozen:
                    session.add(RunRoleOutputRecord(**body))
                session.flush()
        finally:
            restored.set()

    def controlled_dispatch(
        session: Session,
        run_id: str,
        *,
        has_checkpoint_locator: bool = False,
    ) -> bool:
        nonlocal false_count
        value = original_dispatch(session, run_id, has_checkpoint_locator=has_checkpoint_locator)
        observations.append(
            {
                "first_reader_driver": id(session.connection().connection.driver_connection),
                "actual_dispatch": value,
            }
        )
        if not value:
            false_count += 1
            # Project has its own true legacy dispatch before calling commit's
            # dispatch; insert at the latter boundary to expose the old writer.
            if false_count == (2 if consumer == "project" else 1):
                reached.set()
                assert restored.wait(15), "actual second writer did not complete"
        return value

    error = None
    returned = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(restore_actual_rows)
            with monkeypatch.context() as patch:
                patch.setattr(
                    graph_binding, "graph_requires_native_binding_in_session", controlled_dispatch
                )
                try:
                    if consumer == "review":
                        returned = (
                            ResearchReviewService(inputs.database)
                            .review(
                                inputs.request.run_id,
                                final.result,
                            )
                            .model_dump(mode="json")
                        )
                    elif consumer == "direct":
                        candidate = _project_research_candidate(final.result, False)
                        returned = CommitDecisionService(inputs.database).commit(
                            inputs.request.run_id,
                            inputs.request.event_id,
                            candidate,
                            evaluate_gate(candidate),
                            research_result=final.result,
                            expected_lease_owner=OWNER,
                        )
                    else:
                        returned = CommitDecisionService(inputs.database).commit_research_result(
                            inputs.request.run_id,
                            inputs.request.event_id,
                            final.result,
                            expected_lease_owner=OWNER,
                        )[0]
                except (ValueError, PermissionError) as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)}
            future.result(timeout=15)
        after = _sql(inputs)
        _observe(
            tmp_path,
            consumer=consumer,
            original_graph=original,
            rows_absent=absent,
            original_ref=final.reference.model_dump(mode="json"),
            returned=returned,
            error=error,
            observations=observations,
            after=after,
        )
        assert error is not None, "actual V2 rows crossed the legacy publication writer"
        assert error["message"] == "research_graph_publication_reference_required"
        assert after == original
        writers = [
            row["second_writer_driver"] for row in observations if "second_writer_driver" in row
        ]
        readers = [
            row["first_reader_driver"] for row in observations if "first_reader_driver" in row
        ]
        assert writers and readers and writers[0] != readers[0]
    finally:
        other.engine.dispose()


async def test_original_result_blocks_self_consistent_artifact_and_source_resigning(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    final = await _actual_final(inputs)
    review = ResearchReviewService(inputs.database).review(
        inputs.request.run_id,
        final.result,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    reviewed = final.result.model_copy(update={"reviews": [review]})
    service = CommitDecisionService(inputs.database)
    artifact_id, _gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    original = _sql(inputs)
    with inputs.database.session() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=inputs.request.run_id, role="research_publication_source")
            .one()
        )
        assert artifact is not None
        artifact_before = {
            column.key: getattr(artifact, column.key) for column in artifact.__table__.columns
        }
        source_before = {
            column.key: getattr(source, column.key) for column in source.__table__.columns
        }
        body = json.loads(source.payload_json)
        artifact.headline = "Controlled source-only headline absent from original Result"
        artifact.summary = "Controlled source-only summary absent from original Result"
        body["candidate"]["headline"] = artifact.headline
        body["candidate"]["summary"] = artifact.summary
        body["artifact_hash"] = commit_module._canonical_hash(  # pyright: ignore[reportPrivateUsage]
            commit_module._artifact_body(artifact)  # pyright: ignore[reportPrivateUsage]
        )  # pyright: ignore[reportPrivateUsage]
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        source.payload_json, source.payload_hash = encoded, digest
        source.output_id = f"publication_{digest[:40]}"
    changed = _sql(inputs)
    errors: list[dict[str, Any]] = []
    for entrance in ("project", "direct"):
        error = None
        returned = None
        try:
            if entrance == "project":
                returned = service.commit_research_result(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    reviewed,
                    graph_final_ref=final.reference,
                )[0]
            else:
                candidate = body["candidate"]
                returned = service.commit(
                    inputs.request.run_id,
                    inputs.request.event_id,
                    candidate,
                    evaluate_gate(candidate),
                    research_result=reviewed,
                    graph_final_ref=final.reference,
                )
        except (ValueError, PermissionError) as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
        errors.append(
            {"entrance": entrance, "error": error, "returned": returned, "sql": _sql(inputs)}
        )
    with inputs.database.session() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=inputs.request.run_id, role="research_publication_source")
            .one()
        )
        assert artifact is not None
        for key, value in artifact_before.items():
            setattr(artifact, key, value)
        for key, value in source_before.items():
            setattr(source, key, value)
    restored = _sql(inputs)
    _observe(
        tmp_path,
        original=original,
        changed=changed,
        errors=errors,
        restored=restored,
        original_ref=final.reference.model_dump(mode="json"),
    )
    assert all(item["error"] is not None for item in errors), (
        "Artifact/source resigning concealed a body absent from the original Result"
    )
    assert all(item["sql"] == changed for item in errors)
    assert restored == original


@pytest.mark.parametrize("checkpoint_ref", ["original", "missing"])
async def test_worker_passes_original_graph_ref_and_actual_owner_without_latest_inference(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_ref: str,
) -> None:
    final = await _actual_final(inputs)
    state = dict(final.state)
    if checkpoint_ref == "missing":
        state.pop("graph_final_projection_ref")
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database),
        host,
        poll_interval_seconds=0.001,
    )
    worker = DurableResearchWorker(
        inputs.database,
        runtime,
        pack_root=Path(__file__).resolve().parents[2] / "packs" / "crypto_macro",
        worker_id=OWNER,
        execution_mode="replay",
        allowed_capabilities=inputs.request.allowed_capabilities,
    )

    # Delivery from the executor is controlled here. Its state and locator
    # were produced by the actual Graph/Runtime above; this test does not
    # certify LangGraph checkpoint persistence or a second remote execution.
    async def has_checkpoint(run_id: str) -> bool:
        assert run_id == inputs.request.run_id
        return True

    async def resume(run_id: str) -> dict[str, Any]:
        assert run_id == inputs.request.run_id
        return state

    actual_review = worker.reviews.review
    actual_commit = worker.commit.commit_research_result
    forwarded: list[dict[str, Any]] = []

    def review(
        run_id: str,
        result: ResearchSessionResult,
        *,
        graph_final_ref: GraphFinalProjectionRef | None = None,
        expected_lease_owner: str | None = None,
    ) -> ResearchReview:
        forwarded.append(
            {
                "consumer": "review",
                "owner": expected_lease_owner,
                "ref": graph_final_ref.model_dump(mode="json")
                if graph_final_ref is not None
                else None,
            }
        )
        return actual_review(
            run_id,
            result,
            graph_final_ref=graph_final_ref,
            expected_lease_owner=expected_lease_owner,
        )

    def commit(
        run_id: str,
        event_id: str,
        result: ResearchSessionResult,
        *,
        graph_final_ref: GraphFinalProjectionRef | None = None,
        expected_lease_owner: str | None = None,
    ) -> tuple[str, GateResult]:
        forwarded.append(
            {
                "consumer": "commit",
                "owner": expected_lease_owner,
                "ref": graph_final_ref.model_dump(mode="json")
                if graph_final_ref is not None
                else None,
            }
        )
        return actual_commit(
            run_id,
            event_id,
            result,
            graph_final_ref=graph_final_ref,
            expected_lease_owner=expected_lease_owner,
        )

    monkeypatch.setattr(worker.executor, "has_checkpoint", has_checkpoint)
    monkeypatch.setattr(worker.executor, "resume", resume)
    monkeypatch.setattr(worker.reviews, "review", review)
    monkeypatch.setattr(worker.commit, "commit_research_result", commit)
    before = _sql(inputs)
    try:
        report = await worker._execute_claim(  # pyright: ignore[reportPrivateUsage]
            inputs.request.event_id,
            inputs.request.run_id,
            recovered=True,
        )
    finally:
        await runtime.close()
    after = _sql(inputs)
    _observe(
        tmp_path,
        original_ref=final.reference.model_dump(mode="json"),
        checkpoint_ref=checkpoint_ref,
        forwarded=forwarded,
        report=report.model_dump(mode="json"),
        original=before,
        after=after,
        original_host_submits=final.host_submits,
        additional_host_submits=len(host.submit_calls),
    )
    assert host.submit_calls == []
    if checkpoint_ref == "missing":
        assert report.status == "failed"
        assert forwarded == [{"consumer": "review", "owner": None, "ref": None}]
        assert after["tables"]["artifacts"] == []
        assert after["tables"]["research_results"] == []
        assert after["tables"]["outbox"] == []
        assert not any(
            row["role"] == "research_publication_source"
            for row in after["tables"]["run_role_outputs"]
        )
    else:
        assert report.status == "reject" and report.artifact_id
        assert forwarded == [
            {"consumer": consumer, "owner": OWNER, "ref": final.reference.model_dump(mode="json")}
            for consumer in ("review", "commit")
        ]
        assert len(after["tables"]["artifacts"]) == 1
        assert after["tables"]["artifacts"][0]["gate_status"] == report.status
        assert len(after["tables"]["research_results"]) == 1
        assert len(after["tables"]["outbox"]) == 1
        sources = [
            row
            for row in after["tables"]["run_role_outputs"]
            if row["role"] == "research_publication_source"
        ]
        assert len(sources) == 1
        source_body = json.loads(sources[0]["payload_json"])
        assert source_body["graph_final_ref"] == final.reference.model_dump(mode="json")
        assert source_body["publication_owner"] == OWNER
        assert source_body["gate"]["status"] == report.status


async def test_original_publication_decision_blocks_status_resigning_and_missing_original(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    final = await _actual_final(inputs)
    review = ResearchReviewService(inputs.database).review(
        inputs.request.run_id,
        final.result,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    reviewed = final.result.model_copy(update={"reviews": [review]})
    service = CommitDecisionService(inputs.database)
    artifact_id, original_gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
        expected_lease_owner=OWNER,
    )
    assert original_gate.status == GateStatus.reject
    original = _sql(inputs)
    with inputs.database.session() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(
                run_id=inputs.request.run_id,
                role="research_publication_source",
            )
            .one()
        )
        decision = session.get(PublicationDecisionRecord, artifact_id)
        assert artifact is not None and decision is not None
        artifact_before = {
            column.key: getattr(artifact, column.key) for column in artifact.__table__.columns
        }
        source_before = {
            column.key: getattr(source, column.key) for column in source.__table__.columns
        }
        decision_before = {
            column.key: getattr(decision, column.key) for column in decision.__table__.columns
        }
        body = json.loads(source.payload_json)
        artifact.gate_status = "publish"
        body["gate"]["status"] = "publish"
        body["artifact_hash"] = commit_module._canonical_hash(  # pyright: ignore[reportPrivateUsage]
            commit_module._artifact_body(artifact)  # pyright: ignore[reportPrivateUsage]
        )
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        source.payload_json, source.payload_hash = encoded, digest
        source.output_id = f"publication_{digest[:40]}"
    changed = _sql(inputs)
    observations: list[dict[str, Any]] = []

    def replay(phase: str) -> None:
        for entrance in ("project", "direct"):
            error = None
            returned = None
            status = None
            try:
                if entrance == "project":
                    returned, gate = service.commit_research_result(
                        inputs.request.run_id,
                        inputs.request.event_id,
                        reviewed,
                        graph_final_ref=final.reference,
                    )
                    status = gate.status.value
                else:
                    candidate = body["candidate"]
                    returned = service.commit(
                        inputs.request.run_id,
                        inputs.request.event_id,
                        candidate,
                        evaluate_gate(candidate),
                        research_result=reviewed,
                        graph_final_ref=final.reference,
                    )
            except (ValueError, PermissionError) as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
            observations.append(
                {
                    "phase": phase,
                    "entrance": entrance,
                    "error": error,
                    "returned": returned,
                    "status": status,
                    "sql": _sql(inputs),
                }
            )

    replay("status_resigned")
    with inputs.database.session() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        source = (
            session.query(RunRoleOutputRecord)
            .filter_by(
                run_id=inputs.request.run_id,
                role="research_publication_source",
            )
            .one()
        )
        decision = session.get(PublicationDecisionRecord, artifact_id)
        assert artifact is not None and decision is not None
        for key, value in artifact_before.items():
            setattr(artifact, key, value)
        for key, value in source_before.items():
            setattr(source, key, value)
        session.delete(decision)
    missing = _sql(inputs)
    replay("decision_missing")
    with inputs.database.session() as session:
        session.add(PublicationDecisionRecord(**decision_before))
    restored = _sql(inputs)
    repeated_id, repeated_gate = service.commit_research_result(
        inputs.request.run_id,
        inputs.request.event_id,
        reviewed,
        graph_final_ref=final.reference,
    )
    after_legal = _sql(inputs)
    _observe(
        tmp_path,
        original=original,
        changed=changed,
        missing=missing,
        observations=observations,
        restored=restored,
        after_legal=after_legal,
        legal_replay={"artifact_id": repeated_id, "gate": repeated_gate.status.value},
        original_ref=final.reference.model_dump(mode="json"),
    )
    assert all(observation["error"] is not None for observation in observations), (
        "Historical status/missing Decision was accepted without its original Decision anchor"
    )
    assert all(
        item["sql"] == (changed if item["phase"] == "status_resigned" else missing)
        for item in observations
    )
    assert restored == after_legal == original
    assert repeated_id == artifact_id and repeated_gate == original_gate
