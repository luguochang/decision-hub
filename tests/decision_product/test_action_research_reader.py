"""Actual offline native/Graph/publication fixtures, never live provider proof."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    DshSessionResult,
    DshSessionSubmit,
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.action_research_reader import (
    read_trusted_research_in_session,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
    validate_terminal_result_native_metadata,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.persistence import (
    research_publication_bundle_records,  # noqa: F401 -- register before fixture create_all
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    prepare_originals,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    _active,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_graph_observed_result_v2 import (
    continuation_inputs as continuation_inputs,
)
from tests.research import test_graph_citation_membership_v2 as citation_fixture
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)
from tests.research.test_graph_citation_membership_v2 import (
    LOCATOR_B,
    SPOT_A,
    SnapshotCitationHost,
    SucceedThenRejectCitationHost,
    _snapshot_final,  # pyright: ignore[reportPrivateUsage]
    snapshot_inputs,
)
from tests.research.test_graph_citation_publication_v2 import (
    Draft,
    _copy_draft,  # pyright: ignore[reportPrivateUsage]
    _originals,  # pyright: ignore[reportPrivateUsage]
    _publish,  # pyright: ignore[reportPrivateUsage]
    _renew_draft_lease,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert research_publication_bundle_records is not None


def _native_metadata(result: DshSessionResult, submit: DshSessionSubmit) -> DshSessionResult:
    """The existing authenticated-terminal fixture's native-event control.

Keep this small Host-only constructor here: importing its original test module
also imports API main, whose global app initializes a default database. It does
not replace any production validator or authenticated terminal receipt writer.
    """
    rows = json.loads(result.events_json)
    base = int(result.started_at.timestamp() * 1000)
    end = int(result.finished_at.timestamp() * 1000)
    events: list[dict[str, object]] = [{
        "type": "user/message", "seq": 1, "time": base,
        "data": {"source": {"kind": "user", "rpcId": submit.deterministic_request_id}},
    }]
    for sequence, row in enumerate(rows, 2):
        event = dict(row["payload"]["event"])
        event.update(seq=sequence, time=base)
        events.append(event)
    events.append({
        "type": "turn/end", "seq": len(events) + 1, "time": end,
        "data": {"reason": {"kind": result.finish_reason}},
    })
    body = json.dumps([{
        "method": "session.event", "payload": {
            "sessionId": submit.deterministic_session_id, "event": event,
        },
    } for event in events])
    return result.model_copy(update={
        "started_at": datetime.fromtimestamp(base / 1000, UTC),
        "finished_at": datetime.fromtimestamp(end / 1000, UTC), "events_json": body,
        "result_hash": hashlib.sha256((result.final_response + "\0" + body).encode()).hexdigest(),
    })


def _gateway_admitted_response(
    source: PreparedInputs, payload: DshSessionSubmit, response: DshSessionResult,
) -> DshSessionResult:
    """Update the old offline Host seam to return real Gateway admission quality.

The production native mapper now admits only accepted synthesis references.
Use the actual SQL evidence admission, never patch the mapper/Graph or relabel
candidate evidence by hand. Facts and raw synthesis stay as the Host emitted.
    """
    events = json.loads(response.events_json)
    capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
        "structuredContent"
    ]
    evidence_service = ResearchEvidenceService(source.database)
    accepted = evidence_service.accept_candidates(
        run_id=payload.run_id,
        capability_id=capability["capability_id"],
        candidates=[EvidenceCandidate.model_validate(item)
                    for item in capability["evidence_candidates"]],
        requirements={item.requirement_id: item for item in source.request.evidence_requirements},
        cutoff_at=source.request.pit_cutoff_at,
        facts=[FactEnvelope.model_validate(item) for item in capability.get("facts", [])],
    )
    persisted = {
        item.evidence_id: item for item in evidence_service.list_run_evidence(payload.run_id)
    }
    capability["evidence_candidates"] = [
        persisted[item.evidence_id].model_dump(mode="json") for item in accepted
    ]
    encoded = json.dumps(events)
    return response.model_copy(update={
        "events_json": encoded,
        "result_hash": hashlib.sha256(
            (response.final_response + "\0" + encoded).encode()
        ).hexdigest(),
    })


def _authenticated_host_response(
    source: PreparedInputs, payload: DshSessionSubmit, response: DshSessionResult,
) -> DshSessionResult:
    """Test Host emits coherent native events and uses the real terminal service.

Only this offline response's execution clock changes; all evidence PIT clocks
remain frozen. No terminal row, link state or authorization verdict is forged.
This exercises the authenticated service boundary, not HTTP key authentication.
    """
    now = datetime.now(UTC)
    # Native event timestamps are integral milliseconds. Sample after the real
    # preparation; never shift a receipt past its actual clock/deadline.
    completed_at = now.replace(microsecond=now.microsecond // 1000 * 1000)
    with source.database.session() as session:
        prompt = session.get(DshSessionPromptRecord, (payload.run_id, payload.generation))
        assert prompt is not None
        created_at = as_utc(prompt.created_at)
        assert created_at is not None and created_at <= completed_at < payload.deadline_at
    original = _native_metadata(response.model_copy(update={
        "started_at": completed_at, "finished_at": completed_at,
    }), payload)
    native_events = json.loads(original.events_json)
    last_seq = native_events[-1]["payload"]["event"]["seq"]
    original = original.model_copy(update={
        "last_seq": last_seq,
        "trace_ref": f"dsh://sessions/{payload.deterministic_session_id}?last_seq={last_seq}",
    })
    completion = DshSessionCompletion(
        schema_version="dsh-session-completion.v1",
        run_id=payload.run_id, dsh_session_id=payload.deterministic_session_id,
        generation=payload.generation, terminal_status="completed", last_seq=last_seq,
        trace_ref=original.trace_ref, result_ref=f"dsh-host://runs/{payload.run_id}/result",
        result_hash=original.result_hash, completed_at=original.finished_at, error=None,
    )
    validate_terminal_result_native_metadata(
        original, payload.deterministic_request_id, completion
    )
    DshSessionLinkService(source.database).complete(completion, authenticated_host=True)
    with source.database.session() as session:
        verified = read_authenticated_terminal_in_session(
            session, payload.run_id, now=datetime.now(UTC)
        )
        assert verified is not None and verified.completion == completion
    return original


class GatewaySnapshotHost(SnapshotCitationHost):
    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        admitted = _gateway_admitted_response(self.fixture, payload, await super().result(payload))
        return _authenticated_host_response(self.fixture, payload, admitted)


class GatewaySucceedThenRejectHost(SucceedThenRejectCitationHost):
    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        admitted = _gateway_admitted_response(self.fixture, payload, await super().result(payload))
        return _authenticated_host_response(self.fixture, payload, admitted)


@pytest.fixture(scope="module", autouse=True)
def isolated_reader_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    # Database(url=...) still creates its configured default data directories.
    # Keep those paths inside this test's temporary tree as well as each DB URL.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path_factory.mktemp("reader-data")))
        yield


async def _research_draft(
    tmp_path_factory: pytest.TempPathFactory, name: str,
) -> AsyncIterator[Draft]:
    # Construct only the actual seed consumed by these tests. The upstream
    # genuine_drafts also constructs an unused rejected seed whose historical
    # Graph-level rejection is now caught earlier by native attestation.
    directory = tmp_path_factory.mktemp(f"action-reader-{name}")
    patch = pytest.MonkeyPatch()
    cast(
        Callable[[pytest.MonkeyPatch], None],
        forbid_external_connections.__wrapped__,  # pyright: ignore[reportAttributeAccessIssue]
    )(patch)
    patch.setattr(citation_fixture, "SnapshotCitationHost", GatewaySnapshotHost)
    producer = cast(
        Callable[[Path], Generator[PreparedInputs]],
        snapshot_inputs.__wrapped__,  # pyright: ignore[reportAttributeAccessIssue]
    )(directory)
    try:
        source = next(producer)
        result = await _snapshot_final(source, directory, top_ref=SPOT_A)
        _renew_draft_lease(source)
        recorded = json.loads((directory / "snapshot-citation-original.json").read_text())
        reference = binding.parse_graph_final_ref(recorded["graph_final_ref"])
        with source.database.session() as session:
            final = binding.read_graph_final_in_session(
                session, run_id=source.request.run_id, reference=reference,
                expected_lease_owner=OWNER, require_current_permission=True,
            )
        assert final.result == result
        assert final.receipt.schema_version == "research-graph-final-projection.v2"
        review = ResearchReviewService(source.database).review(
            source.request.run_id, result, graph_final_ref=reference, expected_lease_owner=OWNER,
        )
        yield Draft(source, final, result.model_copy(update={"reviews": [review]}))
    finally:
        producer.close()
        patch.undo()


@pytest.fixture(scope="module")
async def accepted_draft(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Draft]:
    async for item in _research_draft(tmp_path_factory, "accepted"):
        yield item


@pytest.fixture(scope="module")
async def peer_draft(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Draft]:
    async for item in _research_draft(tmp_path_factory, "peer"):
        yield item


@pytest.fixture
def draft(accepted_draft: Draft, tmp_path: Path) -> Iterator[Draft]:
    copied = _copy_draft(accepted_draft, tmp_path)
    try:
        yield copied
    finally:
        copied.source.database.engine.dispose()


def _read(draft: Draft, published_artifact_id: str, **changes: Any):
    args: dict[str, Any] = dict(
        run_id=draft.source.request.run_id,
        event_id=draft.source.request.event_id,
        artifact_id=published_artifact_id,
        reference=draft.final.reference,
    )
    args.update(changes)
    with draft.source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        return read_trusted_research_in_session(session, **args)


def test_legacy_selected_native_horizons_are_readable_without_rewriting_hashes(draft: Draft):
    artifact_id = _publish(draft)
    before = _originals(draft.source)
    source_json = draft.final.result.model_dump_json()
    result = _read(draft, artifact_id)
    assert result.action_status == "unsupported"
    assert result.action_reason_code == "action_native_typed_synthesis_unavailable"
    assert result.quality_binding == "unavailable"
    assert result.selected_observation_ref == draft.final.receipt.selected_synthesis_observation_ref
    assert result.selected_observation_ref is not None
    with draft.source.database.session() as session:
        selected = read_mapped_observation(session, result.selected_observation_ref)
    assert selected.selection.candidate is not None
    assert result.selected_candidate_json == observation_json(
        selected.selection.candidate.model_dump(mode="json")
    )
    assert [(item.horizon, json.loads(item.candidate_json)) for item in result.horizons] == [
        (item.horizon, item.model_dump(mode="json"))
        for item in selected.selection.candidate.horizons
    ]
    assert result.selected_result_json == observation_json(selected.result.model_dump(mode="json"))
    assert result.graph_result_json == observation_json(draft.final.result.model_dump(mode="json"))
    assert draft.final.result.model_dump_json() == source_json
    assert _originals(draft.source) == before


def test_mixed_run_event_artifact_and_another_real_final_are_rejected(
    draft: Draft, peer_draft: Draft,
):
    artifact_id = _publish(draft)
    for change in (
        {"run_id": "other-run"}, {"event_id": "other-event"}, {"artifact_id": "other-artifact"},
    ):
        with pytest.raises(ValueError, match="action_research_run_identity_mismatch"):
            _read(draft, artifact_id, **change)
    with pytest.raises(ValueError):
        _read(draft, artifact_id, reference=peer_draft.final.reference)
    with pytest.raises(ValueError, match="action_research_final_reference_invalid"):
        _read(draft, artifact_id, reference=draft.final.reference.model_dump(mode="json"))


def test_selected_native_original_drift_rejected_even_when_report_still_exists(draft: Draft):
    artifact_id = _publish(draft)
    selected_ref = draft.final.receipt.selected_synthesis_observation_ref
    assert selected_ref is not None
    with draft.source.database.session() as session:
        row = session.get(RunRoleOutputRecord, selected_ref.output_id)
        assert row is not None
        row.payload_json += " "
    with pytest.raises(ValueError):
        _read(draft, artifact_id)


def test_dirty_or_missing_physical_snapshot_is_rejected_before_autoflush(draft: Draft):
    artifact_id = _publish(draft)
    args: dict[str, Any] = dict(
        run_id=draft.source.request.run_id, event_id=draft.source.request.event_id,
        artifact_id=artifact_id, reference=draft.final.reference,
    )
    with draft.source.database.session() as session:
        with pytest.raises(ValueError, match="action_research_clean_read_snapshot_required"):
            read_trusted_research_in_session(session, **args)
    with draft.source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        run = session.get(RunRecord, draft.source.request.run_id)
        assert run is not None
        run.status = "running"
        native = session.connection().connection.driver_connection
        assert isinstance(native, sqlite3.Connection)
        before = native.total_changes
        with pytest.raises(ValueError, match="action_research_clean_read_snapshot_required"):
            read_trusted_research_in_session(session, **args)
        assert native.total_changes == before
        session.rollback()


def test_prepared_read_rechecks_originals_in_writer_without_publication_permission(draft: Draft):
    artifact_id = _publish(draft)
    args: dict[str, Any] = dict(
        run_id=draft.source.request.run_id, event_id=draft.source.request.event_id,
        artifact_id=artifact_id, reference=draft.final.reference,
    )
    prepared = prepare_originals(
        draft.source.database, lambda session: read_trusted_research_in_session(session, **args)
    )
    with draft.source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prepared.verify(session)
    with draft.source.database.session() as session:
        row = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == draft.source.request.run_id,
            RunRoleOutputRecord.role == "independent_reviewer",
        ))
        assert row is not None
        row.payload_json += " "
    with draft.source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="original_preparation_readset_changed"):
            prepared.verify(session)


async def test_rejected_latest_raw_never_replaces_selected_same_horizon(
    continuation_inputs: PreparedInputs,
):
    source = continuation_inputs
    request = _active(source)
    host = GatewaySucceedThenRejectHost(source)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(source.database), host, poll_interval_seconds=0.001
    )
    try:
        graph = build_agentic_research_graph(
            runtime, ResearchEvidenceService(source.database), expected_lease_owner=OWNER
        )
        state = await graph.ainvoke(initial_research_state(request))  # pyright: ignore[reportArgumentType]
    finally:
        await runtime.close()
    reference = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
    result = ResearchSessionResult.model_validate(state["final_result"])
    review = ResearchReviewService(source.database).review(
        request.run_id, result, graph_final_ref=reference, expected_lease_owner=OWNER,
    )
    artifact_id, _ = CommitDecisionService(source.database).commit_research_result(
        request.run_id, request.event_id, result.model_copy(update={"reviews": [review]}),
        graph_final_ref=reference, expected_lease_owner=OWNER,
    )
    with source.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        trusted = read_trusted_research_in_session(
            session, run_id=request.run_id, event_id=request.event_id,
            artifact_id=artifact_id, reference=reference,
        )
        final = binding.read_graph_final_in_session(
            session, run_id=request.run_id, reference=reference,
            expected_lease_owner=None, require_current_permission=False,
        )
        last = binding.read_graph_round_in_session(
            session, run_id=request.run_id, reference=final.receipt.last_admission_ref,
            expected_lease_owner=None, require_current_permission=False,
        )
        rejected = read_mapped_observation(session, last.receipt.observation_ref)
        raw_rejected = read_transport_observation(session, rejected.mapping_input_ref)
    assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
    assert rejected.result.synthesis_failure_code == "dsh_evidence_unattested"
    assert trusted.selected_observation_ref != last.receipt.observation_ref
    assert trusted.selected_result_json is not None
    assert observation_sha(json.loads(trusted.selected_result_json)) != observation_sha(
        rejected.result.model_dump(mode="json")
    )
    assert rejected.result.causal_case is None
    assert isinstance(raw_rejected.reply, DshSessionResult)
    assert LOCATOR_B in raw_rejected.reply.events_json
    assert all(LOCATOR_B not in json.loads(item.candidate_json)["evidence_refs"]
               for item in trusted.horizons)
    assert {json.loads(item.candidate_json)["action"] for item in trusted.horizons} == {"long"}
    assert trusted.action_status == "unsupported"
