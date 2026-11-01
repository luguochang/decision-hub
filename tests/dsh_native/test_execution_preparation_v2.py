"""Actual offline SQLite V2 preparation; no Runtime/Host/provider execution."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application import (
    research_execution_binding as preparation,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context import (
    build_task_policy,
    select_context,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    ResearchExecutionPreparationService,
    TurnKind,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@dataclass(frozen=True)
class PreparedInputs:
    database: Database
    request: ResearchSessionRequest
    submit: DshSessionSubmit
    upstream: DshUpstreamIdentity
    prompt: str
    now: datetime

    def prepare(self, *, turn_kind: TurnKind = "research_round") -> FrozenExecutionPreparation:
        return ResearchExecutionPreparationService(self.database, clock=lambda: self.now).prepare(
            self.request,
            self.submit,
            self.upstream,
            self.prompt,
            runtime_id="dsh",
            runtime_version=f"dsh-web-{self.upstream.source_version}+{self.upstream.plugin_build_hash[:12]}",
            profile_ref="decision-research.web.v1",
            prompt_policy_hash="4" * 64,
            turn_kind=turn_kind,
        )


@pytest.fixture
def inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        factory = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode="replay",
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        full = factory.build(event_id, run_id)
        # Actual Graph-style first execution scope is a proper original subset.
        request = full.model_copy(deep=True)
        request.evidence_requirements = request.evidence_requirements[:1]
        digest = research_request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=digest,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at,
            max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        upstream = DshUpstreamIdentity(
            source_commit="1" * 40,
            source_version="0.1.0",
            package_versions={},
            plugin_build_hash="2" * 64,
        )
        with database.session() as session:
            bundle = read_research_bundle_in_session(session, run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        prompt = (
            render_prepared_research_policy(bundle, request.allowed_capabilities)
            + "\n\nControlled body; not an executed Host."
        )
        yield PreparedInputs(database, request, submit, upstream, prompt, datetime.now(UTC))
    finally:
        database.engine.dispose()


def _utc_iso(value: datetime) -> str:
    normalized = as_utc(value)
    assert normalized is not None
    return normalized.isoformat()


def _snapshot(database: Database, run_id: str) -> dict[str, Any]:
    with database.session() as session:
        roles = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=run_id)
            .order_by(RunRoleOutputRecord.role)
            .all()
        )
        prompts = (
            session.query(DshSessionPromptRecord)
            .filter_by(run_id=run_id)
            .order_by(DshSessionPromptRecord.generation)
            .all()
        )
        link = session.get(DshSessionLinkRecord, run_id)
        run = session.get(RunRecord, run_id)
        assert run is not None
        return {
            "runtime_version": run.runtime_version,
            "roles": [
                {
                    "output_id": row.output_id,
                    "role": row.role,
                    "schema": row.schema_version,
                    "payload": row.payload_json,
                    "hash": row.payload_hash,
                    "created_at": _utc_iso(row.created_at),
                }
                for row in roles
            ],
            "prompts": [
                {
                    "generation": row.generation,
                    "request_id": row.request_id,
                    "prompt": row.prompt,
                    "created_at": _utc_iso(row.created_at),
                }
                for row in prompts
            ],
            "link": {
                column.name: str(getattr(link, column.name)) for column in link.__table__.columns
            }
            if link is not None
            else None,
        }


def _observe(tmp_path: Path, **body: Any) -> None:
    (tmp_path / "observation.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_real_first_preparation_has_one_common_time_and_distinct_three_hashes(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    before = _snapshot(inputs.database, inputs.request.run_id)
    prepared = inputs.prepare()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path,
        before=before,
        after=after,
        method=prepared.method.model_dump(mode="json"),
        execution=prepared.execution.model_dump(mode="json"),
        integrity=prepared.integrity.model_dump(mode="json"),
        internal_prompt=prepared.prompt.model_dump(mode="json"),
        host_prompt=prepared.host_prompt().model_dump(mode="json"),
    )
    assert len(before["roles"]) == 3 and before["prompts"] == [] and before["link"] is None
    assert len(after["roles"]) == 6 and len(after["prompts"]) == 1
    assert prepared.method.recorded_at == prepared.link.created_at == prepared.prompt.recorded_at
    assert prepared.execution.recorded_at == prepared.integrity.recorded_at == inputs.now
    assert (
        len(
            {
                prepared.execution.task_request_hash,
                prepared.execution.request_hash,
                prepared.method.request_hash,
            }
        )
        == 3
    )
    assert (
        prepared.method.definition.policy_binding.first_execution_request_hash
        == prepared.execution.request_hash
    )
    assert prepared.host_prompt().schema_version == "dsh-session-prompt.v1"
    assert (
        DshSessionLinkService(inputs.database).get_prompt(inputs.request.run_id)
        == prepared.host_prompt()
    )
    method = ResearchMethodService(inputs.database).get(inputs.request.run_id)
    assert method is not None and method["method_hash"] == prepared.method.method_hash
    assert [
        row for row in after["roles"] if row["role"] in {r["role"] for r in before["roles"]}
    ] == before["roles"]


def test_actual_graph_budget_exclusion_keeps_known_collected_reference(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    request = inputs.request
    requirement = request.evidence_requirements[0]
    candidates = []
    for index in range(14):
        excerpt = f"Controlled original collected source {index}; no research completion."
        published = request.pit_cutoff_at - timedelta(seconds=2)
        candidates.append(
            EvidenceCandidate(
                evidence_id=f"controlled-budget-{index}",
                requirement_id=requirement.requirement_id,
                kind="official",
                authority="official",
                source_id="controlled-existing-source",
                source_url=None,
                published_at=published,
                observed_at=request.pit_cutoff_at - timedelta(seconds=1),
                received_at=request.pit_cutoff_at,
                content_hash=research_evidence_content_hash(
                    requirement_id=requirement.requirement_id,
                    kind="official",
                    authority="official",
                    source_id="controlled-existing-source",
                    source_url=None,
                    published_at=published,
                    excerpt=excerpt,
                    structured_payload_ref=None,
                ),
                excerpt=excerpt,
                structured_payload_ref=None,
                tool_call_id=None,
                research_session_id=f"controlled:{request.run_id}",
                round=1,
                quality="candidate",
                freshness_status="unknown",
                conflict_group=None,
            )
        )
    evidence = ResearchEvidenceService(inputs.database)
    recorded = evidence.accept_candidates(
        run_id=request.run_id,
        capability_id="replay.research",
        candidates=candidates,
        requirements={requirement.requirement_id: requirement},
        cutoff_at=request.pit_cutoff_at,
    )

    class CaptureOnly:
        runtime_id = "controlled-graph-capture"
        runtime_version = "controlled-graph-capture.v1"
        profile_ref = "controlled-graph-capture.v1"

        def __init__(self) -> None:
            self.requests: list[ResearchSessionRequest] = []

        async def execute(
            self,
            request: ResearchSessionRequest,
            trace_sink: ResearchTraceSink | None = None,
        ) -> ResearchSessionResult:
            self.requests.append(request)
            raise AgentExecutionError(
                "controlled_stop", "Capture only; no Host or successful result"
            )

        async def close(self) -> None:
            return None

    capture = CaptureOnly()
    graph = build_agentic_research_graph(capture, evidence)
    state = initial_research_state(request)
    state["evidence"] = [item.model_dump(mode="json") for item in recorded]
    with pytest.raises(AgentExecutionError, match="Capture only"):
        asyncio.run(graph.ainvoke(state))
    assert len(capture.requests) == 1
    actual = capture.requests[0]
    assert actual.context_manifest is not None
    excluded = {
        item.evidence_ref
        for item in actual.context_manifest.excluded_inputs
        if item.reason == "context_budget"
    }
    assert excluded and excluded <= set(actual.evidence_refs)
    assert not excluded & {item.evidence_id for item in actual.input_evidence}
    digest = research_request_hash(actual)
    session_id, request_id = DshSessionLinkService.deterministic_ids(actual.run_id, digest)
    submit = inputs.submit.model_copy(
        update={
            "request_hash": digest,
            "deterministic_session_id": session_id,
            "deterministic_request_id": request_id,
        }
    )
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, actual.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    prompt = (
        render_prepared_research_policy(bundle, actual.allowed_capabilities)
        + "\n\nCaptured Graph request; no Host."
    )
    before = _snapshot(inputs.database, actual.run_id)
    caught = None
    prepared = None
    try:
        prepared = replace(
            inputs, request=actual, submit=submit, prompt=prompt, now=datetime.now(UTC)
        ).prepare()
    except ValueError as error:
        caught = str(error)
    _observe(
        tmp_path,
        graph_actual=actual.model_dump(mode="json"),
        budget_excluded=sorted(excluded),
        error=caught,
        before=before,
        after=_snapshot(inputs.database, actual.run_id),
        returned_successful_graph_result=False,
    )
    assert caught is None
    assert prepared is not None and excluded <= set(prepared.execution.request.evidence_refs)


class ControlledFlushFailure(RuntimeError):
    pass


@pytest.mark.parametrize("stage", ["method", "link", "prompt", "execution", "integrity"])
def test_failure_after_each_actual_flush_rolls_back_all_five_and_run_version(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    before = _snapshot(inputs.database, inputs.request.run_id)
    actual_flush = preparation._flush_stage  # pyright: ignore[reportPrivateUsage]
    reached: list[dict[str, Any]] = []

    def after_flush(session: Session, actual_stage: str) -> None:
        actual_flush(session, actual_stage)
        if actual_stage == stage:
            run = session.get(RunRecord, inputs.request.run_id, populate_existing=True)
            assert run is not None
            reached.append(
                {
                    "stage": actual_stage,
                    "runtime_version": run.runtime_version,
                    "role_count": session.query(RunRoleOutputRecord)
                    .filter_by(run_id=run.run_id)
                    .count(),
                    "link_count": session.query(DshSessionLinkRecord)
                    .filter_by(run_id=run.run_id)
                    .count(),
                    "prompt_count": session.query(DshSessionPromptRecord)
                    .filter_by(run_id=run.run_id)
                    .count(),
                }
            )
            raise ControlledFlushFailure(stage)

    monkeypatch.setattr(preparation, "_flush_stage", after_flush)
    with pytest.raises(ControlledFlushFailure, match=stage):
        inputs.prepare()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path,
        stage=stage,
        actual_after_flush=reached,
        before=before,
        after=after,
        host_or_runtime_called=False,
    )
    assert reached and after == before
    expected = {
        "method": (4, 0, 0),
        "link": (4, 1, 0),
        "prompt": (4, 1, 1),
        "execution": (5, 1, 1),
        "integrity": (6, 1, 1),
    }[stage]
    assert (
        reached[0]["role_count"],
        reached[0]["link_count"],
        reached[0]["prompt_count"],
    ) == expected
    assert (reached[0]["runtime_version"] != before["runtime_version"]) == (stage != "method")


def _advance_inputs(inputs: PreparedInputs, *, repair: bool = False) -> PreparedInputs:
    prepared = inputs.prepare()
    DshSessionLinkService(inputs.database).complete(
        DshSessionCompletion(
            schema_version="dsh-session-completion.v1",
            run_id=inputs.request.run_id,
            dsh_session_id=prepared.link.dsh_session_id,
            terminal_status="completed",
            generation=1,
            last_seq=1,
            trace_ref=None,
            result_ref="controlled://no-host/first",
            result_hash="5" * 64,
            completed_at=inputs.now + timedelta(milliseconds=1),
            error=None,
        )
    )
    request = inputs.request.model_copy(deep=True)
    request.current_round = 1 if repair else 2
    request.allowed_capabilities = ["web.search"]
    request.execution_budget.max_tool_calls -= 1
    request.repair_instructions = "Controlled synthesis repair body." if repair else None
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        request.run_id, prepared.method.request_hash, 2
    )
    submit = inputs.submit.model_copy(
        update={
            "generation": 2,
            "deterministic_session_id": session_id,
            "deterministic_request_id": request_id,
            "prompt_ref": f"hub://runs/{request.run_id}/prompts/2",
        }
    )
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    prompt = (
        render_prepared_research_policy(bundle, request.allowed_capabilities)
        + "\n\nControlled next turn; no Host."
    )
    return replace(
        inputs, request=request, submit=submit, prompt=prompt, now=inputs.now + timedelta(seconds=1)
    )


@pytest.mark.parametrize("repair", [False, True])
def test_next_generation_and_retry_keep_original_method_ceiling_and_first_full_hash(
    inputs: PreparedInputs,
    tmp_path: Path,
    repair: bool,
) -> None:
    next_inputs = _advance_inputs(inputs, repair=repair)
    before = _snapshot(inputs.database, inputs.request.run_id)
    turn: TurnKind = "synthesis_repair" if repair else "research_round"
    prepared = next_inputs.prepare(turn_kind=turn)
    after = _snapshot(inputs.database, inputs.request.run_id)
    repeated = replace(next_inputs, now=next_inputs.now + timedelta(seconds=1)).prepare(
        turn_kind=turn
    )
    _observe(
        tmp_path,
        controlled_completion_not_host=True,
        before=before,
        after=after,
        current=prepared.execution.model_dump(mode="json"),
        repeated=repeated.execution.model_dump(mode="json"),
    )
    assert repeated == prepared and _snapshot(inputs.database, inputs.request.run_id) == after
    assert prepared.link.generation == 2 and prepared.execution.request.current_round == (
        1 if repair else 2
    )
    assert prepared.method.recorded_at == prepared.link.created_at == inputs.now
    assert prepared.link.max_tool_calls == inputs.submit.max_tool_calls
    assert (
        prepared.execution.request.execution_budget.max_tool_calls
        == inputs.submit.max_tool_calls - 1
    )
    assert (
        prepared.prompt.recorded_at
        == prepared.execution.recorded_at
        == prepared.integrity.recorded_at
        == next_inputs.now
    )
    assert prepared.execution.execution_request_hash != prepared.method.request_hash
    assert (
        prepared.execution.first_execution_request_full_hash
        == prepared.method.definition.policy_binding.first_execution_request_hash
    )
    old = {row["role"]: row for row in before["roles"]}
    assert all(row == old[row["role"]] for row in after["roles"] if row["role"] in old)
    assert len(after["roles"]) == 8 and len(after["prompts"]) == 2


@pytest.mark.parametrize("stage", ["link", "prompt", "execution", "integrity"])
def test_next_generation_real_flush_failure_keeps_original_completed_link(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    next_inputs = _advance_inputs(inputs)
    before = _snapshot(inputs.database, inputs.request.run_id)
    actual_flush = preparation._flush_stage  # pyright: ignore[reportPrivateUsage]
    reached = []

    def after_flush(session: Session, actual_stage: str) -> None:
        actual_flush(session, actual_stage)
        if stage == actual_stage:
            link = session.get(DshSessionLinkRecord, inputs.request.run_id, populate_existing=True)
            assert link is not None
            reached.append(
                {
                    "actual_stage": stage,
                    "generation": link.generation,
                    "state": link.state,
                    "roles": session.query(RunRoleOutputRecord)
                    .filter_by(run_id=link.run_id)
                    .count(),
                    "prompts": session.query(DshSessionPromptRecord)
                    .filter_by(run_id=link.run_id)
                    .count(),
                }
            )
            raise ControlledFlushFailure(stage)

    monkeypatch.setattr(preparation, "_flush_stage", after_flush)
    with pytest.raises(ControlledFlushFailure, match=stage):
        next_inputs.prepare()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path,
        before=before,
        after=after,
        actual_after_flush=reached,
        controlled_completion_not_host=True,
    )
    assert reached and reached[0]["generation"] == 2 and reached[0]["state"] == "admitted"
    assert after == before


@pytest.mark.parametrize("failure", ["unknown", "late", "content_hash"])
def test_additional_reference_needs_actual_same_run_pit_and_semantic_hash(
    inputs: PreparedInputs,
    tmp_path: Path,
    failure: str,
) -> None:
    extra = "controlled-invalid-reference"
    if failure != "unknown":
        first = inputs.request.input_evidence[0]
        requirement = inputs.request.evidence_requirements[0]
        candidate = EvidenceCandidate.model_validate(
            first.model_dump(mode="json")
            | {
                "evidence_id": extra,
                "requirement_id": requirement.requirement_id,
                "structured_payload_ref": None,
                "tool_call_id": None,
                "research_session_id": f"controlled:{inputs.request.run_id}",
                "round": 1,
                "quality": "candidate",
                "freshness_status": "unknown",
                "conflict_group": None,
                "received_at": inputs.request.pit_cutoff_at + timedelta(seconds=1)
                if failure == "late"
                else inputs.request.pit_cutoff_at,
            }
        )
        candidate.content_hash = research_evidence_content_hash(
            requirement_id=candidate.requirement_id,
            kind=candidate.kind,
            authority=candidate.authority,
            source_id=candidate.source_id,
            source_url=str(candidate.source_url) if candidate.source_url else None,
            published_at=candidate.published_at,
            excerpt=candidate.excerpt,
            structured_payload_ref=None,
        )
        ResearchEvidenceService(inputs.database).accept_candidates(
            run_id=inputs.request.run_id,
            capability_id="replay.research",
            candidates=[candidate],
            requirements={requirement.requirement_id: requirement},
            cutoff_at=inputs.request.pit_cutoff_at + timedelta(seconds=2),
        )
        if failure == "content_hash":
            with inputs.database.session() as session:
                row = session.get(ResearchEvidenceRecord, extra)
                assert row is not None
                row.content_hash = "0" * 64
    request = inputs.request.model_copy(
        update={"evidence_refs": [*inputs.request.evidence_refs, extra]}
    )
    before = _snapshot(inputs.database, request.run_id)
    with pytest.raises(ValueError, match="research_execution_evidence_reference_invalid"):
        replace(inputs, request=request).prepare()
    after = _snapshot(inputs.database, request.run_id)
    _observe(tmp_path, failure=failure, before=before, after=after)
    assert before == after


def test_same_generation_full_request_drift_cannot_claim_original_prompt(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    original = inputs.prepare()
    before = _snapshot(inputs.database, inputs.request.run_id)
    changed = inputs.request.model_copy(update={"repair_instructions": "Later unmatched text."})
    assert research_request_hash(changed) == original.method.request_hash
    with pytest.raises(ValueError, match="research_execution_generation_conflict"):
        replace(inputs, request=changed).prepare()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path,
        before=before,
        after=after,
        actual_full_request_drift=changed.model_dump(mode="json"),
    )
    assert before == after


@pytest.mark.parametrize("piece", ["method", "link", "prompt", "execution", "integrity"])
def test_missing_original_v2_piece_is_not_backfilled(
    inputs: PreparedInputs,
    tmp_path: Path,
    piece: str,
) -> None:
    inputs.prepare()
    with inputs.database.session() as session:
        if piece == "link":
            row = session.get(DshSessionLinkRecord, inputs.request.run_id)
        elif piece == "prompt":
            row = session.get(DshSessionPromptRecord, (inputs.request.run_id, 1))
        else:
            role = {
                "method": "research_execution_method",
                "execution": "dsh_execution_request:1",
                "integrity": "dsh_prompt_integrity:1",
            }[piece]
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=inputs.request.run_id, role=role)
                .one()
            )
        assert row is not None
        session.delete(row)
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError):
        inputs.prepare()
    with pytest.raises(ValueError):
        DshSessionLinkService(inputs.database).get_prompt(inputs.request.run_id)
    _observe(
        tmp_path,
        missing=piece,
        before=before,
        after=_snapshot(inputs.database, inputs.request.run_id),
    )
    assert before == _snapshot(inputs.database, inputs.request.run_id)


@pytest.mark.parametrize("different", [False, True])
def test_two_real_connections_have_one_first_writer_and_no_replacement(
    inputs: PreparedInputs,
    tmp_path: Path,
    different: bool,
) -> None:
    second = Database(inputs.database.engine.url.render_as_string(hide_password=False))
    barrier = threading.Barrier(2)
    alternative = replace(inputs, database=second)
    if different:
        alternative = replace(
            alternative,
            request=inputs.request.model_copy(
                update={
                    "repair_instructions": "Different actual first FULL request.",
                }
            ),
        )
    before = _snapshot(inputs.database, inputs.request.run_id)

    def contend(candidate: PreparedInputs) -> dict[str, Any]:
        barrier.wait(timeout=5)
        try:
            result = candidate.prepare()
            return {
                "success": True,
                "method": result.method.model_dump(mode="json"),
                "execution": result.execution.model_dump(mode="json"),
            }
        except ValueError as error:
            return {"success": False, "error": str(error)}

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contend, candidate) for candidate in (inputs, alternative)]
            results = [future.result(timeout=15) for future in futures]
    finally:
        second.engine.dispose()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path, different=different, before=before, after=after, actual_two_connections=results
    )
    successes = [result for result in results if result["success"]]
    assert len(successes) == (1 if different else 2)
    if different:
        assert [result["error"] for result in results if not result["success"]] == [
            "research_execution_generation_conflict"
        ]
    else:
        assert successes[0] == successes[1]
    assert len(after["roles"]) == 6 and len(after["prompts"]) == 1
    winner = ResearchMethodService(inputs.database).get(inputs.request.run_id)
    assert winner is not None and winner["method_hash"] == successes[0]["method"]["method_hash"]


@pytest.mark.parametrize("change", ["method_payload", "bundle_time"])
def test_reused_session_reads_actual_sql_and_rejects_changed_original_anchor(
    inputs: PreparedInputs,
    tmp_path: Path,
    change: str,
) -> None:
    inputs.prepare()
    other = Database(inputs.database.engine.url.render_as_string(hide_password=False))
    session = inputs.database.factory()
    try:
        saved = ResearchMethodService(inputs.database).get_in_session(
            session, inputs.request.run_id
        )
        assert saved is not None
        cached_rows = (
            session.query(RunRoleOutputRecord).filter_by(run_id=inputs.request.run_id).all()
        )
        assert all(row.payload_hash for row in cached_rows)
        with other.session() as writer:
            if change == "method_payload":
                row = (
                    writer.query(RunRoleOutputRecord)
                    .filter_by(run_id=inputs.request.run_id, role="research_execution_method")
                    .one()
                )
                row.payload_json += " "
            else:
                rows = (
                    writer.query(RunRoleOutputRecord)
                    .filter(
                        RunRoleOutputRecord.run_id == inputs.request.run_id,
                        RunRoleOutputRecord.role.in_(
                            (
                                "research_request_pack",
                                "research_task_request",
                                "research_compiled_policy",
                            )
                        ),
                    )
                    .all()
                )
                for row in rows:
                    row.created_at -= timedelta(seconds=1)
        before = _snapshot(inputs.database, inputs.request.run_id)
        if change == "bundle_time":
            # B1 still accepts these self-consistent original payloads; only
            # Method's frozen raw-row time anchor establishes the conflict.
            with other.session() as reader:
                assert read_research_bundle_in_session(reader, inputs.request.run_id) is not None
        with pytest.raises(ValueError):
            ResearchMethodService(inputs.database).get_in_session(session, inputs.request.run_id)
        _observe(
            tmp_path,
            change=change,
            original_method=saved,
            before=before,
            after=_snapshot(inputs.database, inputs.request.run_id),
        )
        assert before == _snapshot(inputs.database, inputs.request.run_id)
    finally:
        session.rollback()
        session.close()
        other.engine.dispose()


@pytest.mark.parametrize("cancel_first", [False, True])
def test_cancelled_run_denies_new_preparation_but_retains_prior_original_receipts(
    inputs: PreparedInputs,
    tmp_path: Path,
    cancel_first: bool,
) -> None:
    original = None if cancel_first else inputs.prepare()
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        run = session.get(RunRecord, inputs.request.run_id, populate_existing=True)
        assert run is not None
        # A controlled persisted cancellation state, not a real HTTP command.
        run.status = "cancelled"
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError, match="research_execution_run_not_active"):
        inputs.prepare()
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(
        tmp_path,
        controlled_cancel_state=True,
        cancel_first=cancel_first,
        before=before,
        after=after,
        host_or_model_called=False,
    )
    assert after == before
    if original is not None:
        assert (
            DshSessionLinkService(inputs.database).get_prompt(inputs.request.run_id)
            == original.host_prompt()
        )
        method = ResearchMethodService(inputs.database).get(inputs.request.run_id)
        assert method is not None and method["method_hash"] == original.method.method_hash
    else:
        assert len(after["roles"]) == 3 and after["link"] is None and after["prompts"] == []


def test_borrowed_session_does_not_commit_or_close_and_requires_physical_transaction(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    before = _snapshot(inputs.database, inputs.request.run_id)
    service = ResearchExecutionPreparationService(inputs.database)
    kwargs: dict[str, Any] = {
        "runtime_id": "dsh",
        "runtime_version": (
            f"dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}"
        ),
        "profile_ref": "decision-research.web.v1",
        "prompt_policy_hash": "4" * 64,
        "recorded_at": inputs.now,
    }
    session = inputs.database.factory()
    try:
        with pytest.raises(ValueError, match="research_execution_transaction_required"):
            service.prepare_in_session(
                session, inputs.request, inputs.submit, inputs.upstream, inputs.prompt, **kwargs
            )
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        prepared = service.prepare_in_session(
            session, inputs.request, inputs.submit, inputs.upstream, inputs.prompt, **kwargs
        )
        assert session.in_transaction()
        assert (
            session.query(RunRoleOutputRecord).filter_by(run_id=inputs.request.run_id).count() == 6
        )
        # A distinct real connection sees no uncommitted five-item bundle.
        assert _snapshot(inputs.database, inputs.request.run_id) == before
        session.rollback()
        _observe(
            tmp_path,
            before=before,
            after=_snapshot(inputs.database, inputs.request.run_id),
            inside_execution=prepared.execution.model_dump(mode="json"),
            own_commit=False,
        )
        assert _snapshot(inputs.database, inputs.request.run_id) == before
    finally:
        session.rollback()
        session.close()


def test_v1_method_and_prompt_remain_readable_and_are_never_upgraded(
    inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    methods = ResearchMethodService(inputs.database, clock=lambda: inputs.now)
    original = methods.freeze(
        inputs.request.run_id, {"legacy": "original"}, request_hash=inputs.submit.request_hash
    )
    links = DshSessionLinkService(inputs.database, clock=lambda: inputs.now)
    links.reserve(inputs.submit, inputs.upstream)
    from packages.contracts_py.decision_hub_contracts import DshSessionPrompt

    legacy_prompt = DshSessionPrompt(
        schema_version="dsh-session-prompt.v1",
        run_id=inputs.request.run_id,
        dsh_session_id=inputs.submit.deterministic_session_id,
        request_id=inputs.submit.deterministic_request_id,
        request_hash=inputs.submit.request_hash,
        generation=1,
        prompt="An original legacy prompt with no actual-execution certificate.",
    )
    links.store_prompt(legacy_prompt)
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ValueError, match="research_execution_legacy_or_partial"):
        inputs.prepare()
    _observe(
        tmp_path,
        before=before,
        after=_snapshot(inputs.database, inputs.request.run_id),
        v1_original=original,
        v1_still_readable=methods.get(inputs.request.run_id),
        existing_bundle_v2_does_not_certify_legacy_v1=True,
    )
    assert original == methods.get(inputs.request.run_id)
    assert links.get_prompt(inputs.request.run_id) == legacy_prompt
    assert before == _snapshot(inputs.database, inputs.request.run_id)


def _input_origin_basis(inputs: PreparedInputs, origin: str) -> PreparedInputs:
    if origin == "task":
        return inputs
    requirement = inputs.request.evidence_requirements[0]
    excerpt = "Controlled   source one.\n\nOriginal    material; no successful execution."
    published = inputs.request.pit_cutoff_at - timedelta(seconds=2)
    candidate = EvidenceCandidate(
        evidence_id="controlled-input-origin",
        requirement_id=requirement.requirement_id,
        kind="official",
        authority="official",
        source_id="controlled-input-origin-source",
        source_url=None,
        published_at=published,
        observed_at=inputs.request.pit_cutoff_at - timedelta(seconds=1),
        received_at=inputs.request.pit_cutoff_at,
        content_hash=research_evidence_content_hash(
            requirement_id=requirement.requirement_id,
            kind="official",
            authority="official",
            source_id="controlled-input-origin-source",
            source_url=None,
            published_at=published,
            excerpt=excerpt,
            structured_payload_ref=None,
        ),
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id=None,
        research_session_id=f"controlled:{inputs.request.run_id}",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    stored = ResearchEvidenceService(inputs.database).accept_candidates(
        run_id=inputs.request.run_id,
        capability_id="replay.research",
        candidates=[candidate],
        requirements={requirement.requirement_id: requirement},
        cutoff_at=inputs.request.pit_cutoff_at,
    )
    assert len(stored) == 1
    body = stored[0].model_dump(mode="json")
    item = ResearchInputEvidence.model_validate(
        {key: body[key] for key in ResearchInputEvidence.model_fields}
    )
    request = inputs.request.model_copy(
        update={
            "input_evidence": [*inputs.request.input_evidence, item],
            "evidence_refs": [*inputs.request.evidence_refs, item.evidence_id],
        }
    )
    assert research_request_hash(request) == inputs.submit.request_hash
    return replace(inputs, request=request)


@pytest.mark.parametrize("origin", ["task", "same_run"])
@pytest.mark.parametrize(
    "field",
    [
        "excerpt",
        "content_hash",
        "kind",
        "authority",
        "source_id",
        "source_url",
        "published_at",
        "observed_at",
        "received_at",
    ],
)
def test_input_origin_caller_drift_is_not_certified_by_valid_id_and_full_hash(
    inputs: PreparedInputs,
    tmp_path: Path,
    origin: str,
    field: str,
) -> None:
    basis = _input_origin_basis(inputs, origin)
    index = 0 if origin == "task" else len(basis.request.input_evidence) - 1
    original = basis.request.input_evidence[index]
    replacements: dict[str, Any] = {
        "excerpt": "Caller invented unrelated research prose.",
        "content_hash": "f" * 64,
        "kind": "web" if original.kind != "web" else "official",
        "authority": "unverified" if original.authority != "unverified" else "official",
        "source_id": "caller-replaced-origin",
        "source_url": "https://example.invalid/caller-replaced-origin",
        "published_at": original.observed_at - timedelta(seconds=5),
        "observed_at": original.observed_at - timedelta(microseconds=1),
        "received_at": original.received_at - timedelta(microseconds=1),
    }
    raw = original.model_dump(mode="python") | {field: replacements[field]}
    changed = ResearchInputEvidence.model_validate(raw)
    assert changed != original
    selected = list(basis.request.input_evidence)
    selected[index] = changed
    request = basis.request.model_copy(update={"input_evidence": selected})
    assert research_request_hash(request) == basis.submit.request_hash
    before = _snapshot(inputs.database, request.run_id)
    error = None
    prepared = None
    try:
        prepared = replace(basis, request=request).prepare()
    except ValueError as exc:
        error = str(exc)
    after = _snapshot(inputs.database, request.run_id)
    _observe(
        tmp_path,
        origin=origin,
        field=field,
        original=original.model_dump(mode="json"),
        caller=changed.model_dump(mode="json"),
        error=error,
        before=before,
        after=after,
        accepted_full_hash=prepared.execution.request_hash if prepared else None,
        reduced_hash_unchanged=True,
        no_host_or_model=True,
    )
    assert error == "research_execution_input_origin_invalid"
    assert before == after


@pytest.mark.parametrize("origin", ["task", "same_run"])
def test_input_origin_actual_selector_excerpt_projection_remains_legal(
    inputs: PreparedInputs,
    tmp_path: Path,
    origin: str,
) -> None:
    basis = _input_origin_basis(inputs, origin)
    policy = build_task_policy(
        research_mode=basis.request.research_mode or "current_decision",
        requirements=basis.request.evidence_requirements,
        created_at=basis.request.pit_cutoff_at,
        max_evidence_rounds=basis.request.execution_budget.max_evidence_rounds,
    )
    projected, manifest = select_context(
        basis.request.input_evidence,
        [],
        requirements=basis.request.evidence_requirements,
        policy=policy,
        manifest_prefix=f"context:{basis.request.run_id}",
        revision=1,
        created_at=basis.request.pit_cutoff_at,
        allowed_capabilities=basis.request.allowed_capabilities,
        eligibility_cutoff_at=basis.request.pit_cutoff_at,
    )
    if origin == "same_run":
        original = basis.request.input_evidence[-1]
        actual = next(item for item in projected if item.evidence_id == original.evidence_id)
        assert actual.excerpt != original.excerpt
        assert actual.model_dump(exclude={"excerpt"}) == original.model_dump(exclude={"excerpt"})
    request = basis.request.model_copy(
        update={"input_evidence": projected, "context_manifest": manifest}
    )
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(request.run_id, digest)
    submit = basis.submit.model_copy(
        update={
            "request_hash": digest,
            "deterministic_session_id": session_id,
            "deterministic_request_id": request_id,
        }
    )
    prepared = replace(basis, request=request, submit=submit).prepare()
    _observe(
        tmp_path,
        origin=origin,
        actual_selector_projection=request.model_dump(mode="json"),
        actual_execution=prepared.execution.model_dump(mode="json"),
        no_host_or_model=True,
    )
    assert prepared.execution.request.model_dump(mode="json") == request.model_dump(mode="json")
