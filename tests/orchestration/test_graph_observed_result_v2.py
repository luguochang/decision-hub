"""Offline genuine Factory/Runtime/Graph boundary cases; no provider or HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    FactEnvelope,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    request_for_state,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    read_mapped_observation,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import RuntimePreparedTurn
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import (
    PreparedHost,
    _sql_repairable_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_agentic_research_graph import (
    _causal_case,  # pyright: ignore[reportPrivateUsage]
    _horizons,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
OWNER = "graph-observed-test-worker"


class SqlState(TypedDict):
    run_status: str
    lease_owner: str | None
    decision_snapshot_id: str | None
    snapshots: int
    roles: list[str]
    materials: dict[str, list[str]]
    run_fields_hash: str


@pytest.fixture
def inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    yield from _prepared_inputs(tmp_path, max_rounds=1)


@pytest.fixture
def continuation_inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    yield from _prepared_inputs(tmp_path, max_rounds=2, max_repairs=1, repair_soft_scope=True)


@pytest.fixture
def evidence_only_inputs(tmp_path: Path) -> Generator[PreparedInputs]:
    yield from _prepared_inputs(tmp_path, max_rounds=2, max_repairs=0)


def _prepared_inputs(
    tmp_path: Path,
    *,
    max_rounds: int,
    max_repairs: int | None = None,
    repair_soft_scope: bool = False,
) -> Generator[PreparedInputs]:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        # Actual bounded source file before Factory freezes its original Task,
        # not a re-signed Task or a monkeypatched Gate after preparation.
        manifest["execution_budget"]["max_evidence_rounds"] = max_rounds
        if max_repairs is not None:
            manifest["execution_budget"]["max_structured_repairs"] = max_repairs
        if repair_soft_scope:
            # A real isolated Pack declares this research-only soft scope before
            # Factory freezes it. Runtime repair still requires sufficient hard
            # coverage; unresolved soft inquiries retain a legal next-round route.
            # Keep all original requirements/minimum facts and formal files intact.
            policy_path = root / manifest["evidence_policy_ref"]
            policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            policy["event_requirement_overrides"]["central_bank_speech"] = {
                item["canonical_requirement_id"]: "soft" for item in policy["requirements"]
            }
            policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
        (root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        request = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode="replay",
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        ).build(event_id, run_id)
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
        yield PreparedInputs(
            database,
            request,
            submit,
            upstream,
            "Unused; real Runtime builds Prompt",
            datetime.now(UTC),
        )
    finally:
        database.engine.dispose()


def _active(fixture: PreparedInputs) -> ResearchSessionRequest:
    with fixture.database.session() as session:
        run = session.get(RunRecord, fixture.request.run_id)
        assert run is not None
        run.status = "running"
        run.lease_owner = OWNER
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        bundle = read_research_bundle_in_session(session, run.run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        # B1's external-ref model is distinct from the public existing DTO.
        # Validate the actual saved value instead of casting its class identity.
        return ResearchSessionRequest.model_validate_json(bundle.task.request.model_dump_json())


def _sql(fixture: PreparedInputs) -> SqlState:
    with fixture.database.session() as session:
        run = session.get(RunRecord, fixture.request.run_id)
        assert run is not None
        materials = {}
        for model in (
            ResearchEvidenceRecord,
            ResearchFactRecord,
            EvidenceParsedLinkRecord,
            ParsedVersionRecord,
            FactVersionRecord,
            RunInputRefRecord,
            SnapshotRecord,
        ):
            # Fresh isolated fixture database contains only this original Run.
            # Save compact full-column fingerprints, not source excerpts/raw bodies.
            entries = []
            for row in session.query(model).all():
                body = {column.key: getattr(row, column.key) for column in model.__table__.columns}
                entries.append(
                    hashlib.sha256(
                        json.dumps(body, sort_keys=True, default=str, ensure_ascii=False).encode()
                    ).hexdigest()
                )
            materials[model.__tablename__] = sorted(entries)
        return {
            "run_status": run.status,
            "lease_owner": run.lease_owner,
            "decision_snapshot_id": run.decision_snapshot_id,
            "snapshots": session.query(SnapshotRecord).filter_by(run_id=run.run_id).count(),
            "roles": [
                r.role
                for r in session.query(RunRoleOutputRecord)
                .filter_by(run_id=run.run_id)
                .order_by(RunRoleOutputRecord.role)
            ],
            "materials": materials,
            "run_fields_hash": hashlib.sha256(
                json.dumps(
                    {
                        column.key: getattr(run, column.key)
                        for column in RunRecord.__table__.columns
                    },
                    sort_keys=True,
                    default=str,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
        }


def _write_observation(path: Path, name: str, body: dict[str, object]) -> None:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    (path / name).write_text(encoded, encoding="utf-8")
    if root := os.environ.get("DECISION_HUB_GRAPH_EVIDENCE_DIR"):
        target = Path(root) / (path.name + "-" + name)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(encoded)


class MaterialHost(PreparedHost):
    """Controlled paired MCP receipt and real Kernel-admitted replay Evidence.

    Facts are only in the typed original transcript until Graph admits them.
    This fixture is not a Provider call or a physical Gateway adapter execution.
    """

    repair_first = False
    fail_next = False
    fail_from_generation = 3

    async def result(self, payload: DshSessionSubmit):
        if self.fail_next and payload.generation >= self.fail_from_generation:
            self.result_calls += 1
            raise AgentExecutionError(
                "structured_output_invalid",
                "controlled next envelope",
                retryable=False,
                origin="dsh",
                cause_code="controlled_format",
            )
        self.result_calls += 1
        result = _sql_repairable_result(self.fixture, payload)
        events = json.loads(result.events_json)[:2]
        capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
            "structuredContent"
        ]
        evidence = capability["evidence_candidates"][0]
        # A cached content receipt can be returned in a new controlled tool
        # session. Keep the original SQL evidence untouched; correlate this
        # actual typed transport to the current Host invocation explicitly.
        evidence["research_session_id"] = payload.deterministic_session_id
        fact_body = {
            "schema_version": "fact-envelope.v1",
            "fact_id": "pending-controlled-fact-id",
            "evidence_id": evidence["evidence_id"],
            "requirement_id": evidence["requirement_id"],
            "metric_family": "macro.policy",
            "field": "stance",
            "instrument": None,
            "venue": None,
            "value": "restrictive",
            "unit": "text",
            "window_start_at": None,
            "window_end_at": None,
            "event_offset": None,
            "observed_at": evidence["observed_at"],
            "received_at": evidence["received_at"],
            "published_at": evidence["published_at"],
            "source_id": evidence["source_id"],
            "independence_group": evidence["source_id"],
            "quality": "candidate",
            "delay_class": "historical",
            "payload_schema_ref": "controlled-policy-stance.v1",
            "payload_hash": hashlib.sha256(b"controlled-policy-stance:restrictive").hexdigest(),
            "attributes": {"fixture": True},
        }
        fact = FactEnvelope.model_validate(fact_body)
        digest = research_fact_payload_hash(
            requirement_id=fact.requirement_id,
            metric_family=fact.metric_family,
            field=fact.field,
            instrument=fact.instrument,
            venue=fact.venue,
            value=fact.value,
            unit=fact.unit,
            window_start_at=fact.window_start_at,
            window_end_at=fact.window_end_at,
            event_offset=fact.event_offset,
            published_at=fact.published_at,
            source_id=fact.source_id,
            independence_group=fact.independence_group,
            delay_class=fact.delay_class,
            payload_schema_ref=fact.payload_schema_ref,
            attributes=fact.attributes,
        )
        fact = fact.model_copy(
            update={
                "payload_hash": digest,
                "fact_id": research_fact_instance_id(
                    evidence_id=fact.evidence_id, payload_hash=digest
                ),
            }
        )
        capability["facts"] = [fact.model_dump(mode="json")]
        eid = evidence["evidence_id"]
        full = self.fixture.request
        horizons = _horizons(eid)
        candidate = {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": full.request_id,
            "causal_case": _causal_case(
                thesis="Controlled official stance is restrictive.",
                unresolved_questions=[
                    r.requirement_id
                    for r in full.evidence_requirements
                    if r.requirement_id != evidence["requirement_id"]
                ],
                evidence_id=eid,
            ),
            "horizons": horizons,
        }
        for index, item in enumerate(horizons):
            item["next_review_at"] = (
                full.pit_cutoff_at + timedelta(minutes=10 + index)
            ).isoformat()
            item["expires_at"] = (full.pit_cutoff_at + timedelta(hours=4 + index)).isoformat()
        final = (
            result.final_response
            if self.repair_first and payload.generation == 1
            else json.dumps(candidate)
        )
        if payload.generation >= 2:
            for kind, data in (
                (
                    "tool/call",
                    {"toolCallId": "graph-synthesis", "name": "decision_hub_synthesis_submit"},
                ),
                ("tool/result", {"toolCallId": "graph-synthesis", "result": candidate}),
            ):
                events.append(
                    {
                        "method": "session.event",
                        "payload": {
                            "sessionId": payload.deterministic_session_id,
                            "event": {"type": kind, "data": data, "seq": len(events) + 1},
                        },
                    }
                )
            final = ""
        encoded = json.dumps(events)
        return result.model_copy(
            update={
                "events_json": encoded,
                "final_response": final,
                "last_seq": len(events),
                "result_hash": hashlib.sha256((final + "\0" + encoded).encode()).hexdigest(),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "cancel", "lease", "expired", "task", "prompt"])
async def test_true_graph_result_needs_native_final_ref_and_writer_permission(
    inputs: PreparedInputs,
    tmp_path: Path,
    change: str,
):
    request = _active(inputs)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )

    class HeldTrace:
        changed = False

        async def emit(self, event: ResearchTraceEvent) -> None:
            if (
                event.event_type == "plan_created"
                and event.research_session_id == f"product:{request.run_id}"
                and not self.changed
            ):
                self.changed = True
                # A distinct real SQLite transaction after Runtime returned.
                with inputs.database.session() as session:
                    run = session.get(RunRecord, request.run_id)
                    assert run is not None
                    if change == "cancel":
                        run.status = "cancelled"
                    elif change == "lease":
                        run.lease_owner = "new-worker"
                    elif change == "expired":
                        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    elif change == "task":
                        row = (
                            session.query(RunRoleOutputRecord)
                            .filter_by(run_id=request.run_id, role="research_task_request")
                            .one()
                        )
                        session.delete(row)
                    elif change == "prompt":
                        from packages.kernel.decision_hub_kernel.persistence.db import (
                            DshSessionPromptRecord,
                        )

                        row = session.get(DshSessionPromptRecord, (request.run_id, 1))
                        assert row is not None
                        row.prompt += " changed after actual observed return"

    trace = HeldTrace()
    # Historical preimplementation tests/copies retain the original optional
    # signature probe. This current test exercises the now implemented API.
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(inputs.database),
        trace_sink=trace,
        expected_lease_owner=OWNER,
    )
    error = None
    result = None
    before = _sql(inputs)
    try:
        result = await graph.ainvoke(initial_research_state(request))
    except (ValueError, PermissionError) as exc:
        error = str(exc)
    finally:
        after = _sql(inputs)
        _write_observation(
            tmp_path,
            "graph-observation.json",
            {
                "case": change,
                "before": before,
                "after": after,
                "error": error,
                "host_submit_count": len(host.submit_calls),
                "actual_result_status": result.get("final_result", {}).get("status")
                if result
                else None,
                "graph_final_projection_ref": result.get("graph_final_projection_ref")
                if result
                else None,
            },
        )
        await runtime.close()
    assert host.submit_calls, "fixture must reach actual controlled Host, not an API absence"
    if change == "none":
        assert error is None
        assert result is not None and result.get("graph_final_projection_ref") is not None
    else:
        assert error is not None, "semantic writer must recheck the original worker/cancel state"
        assert after["decision_snapshot_id"] is None
        assert not any(role.startswith("research_graph_") for role in after["roles"])
        assert before["materials"] == after["materials"]


@pytest.mark.asyncio
async def test_finished_checkpoint_resume_checks_original_projection_without_new_rpc(
    inputs: PreparedInputs,
    tmp_path: Path,
):
    from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore
    from packages.orchestration.langgraph.executor import LangGraphResearchExecutor

    request = _active(inputs)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    path = tmp_path / "checkpoint.sqlite"
    executor = LangGraphResearchExecutor(
        inputs.database, runtime, checkpoint_path=path, expected_lease_owner=OWNER
    )
    try:
        original = await executor.execute(request)
        calls = len(host.submit_calls)
        repeated = await executor.resume(request.run_id)
        assert repeated == original and len(host.submit_calls) == calls
        async with CheckpointStore(path).open() as saver:
            graph = build_agentic_research_graph(
                runtime,
                ResearchEvidenceService(inputs.database),
                checkpointer=saver,
                expected_lease_owner=OWNER,
            )
            original_result = ResearchSessionResult.model_validate(original["final_result"])
            changed = original_result.model_copy(
                update={"total_tool_calls": original_result.total_tool_calls + 1}
            ).model_dump(mode="json")
            await graph.aupdate_state(
                {"configurable": {"thread_id": request.run_id}},
                {"final_result": changed},
                as_node="finalize",
            )
        before = _sql(inputs)
        with pytest.raises(ValueError, match="checkpoint_projection_changed"):
            await executor.resume(request.run_id)
        after = _sql(inputs)
        assert before == after and len(host.submit_calls) == calls
        _write_observation(
            tmp_path,
            "checkpoint-observation.json",
            {
                "original_ref": original["graph_final_projection_ref"],
                "repeated_ref": repeated["graph_final_projection_ref"],
                "host_submits": calls,
                "before": before,
                "after": after,
            },
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_repair_generation_then_new_round_failure_keeps_exact_admitted_synthesis(
    continuation_inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore
    from packages.orchestration.langgraph.executor import LangGraphResearchExecutor

    fixture = continuation_inputs
    request = _active(fixture)
    host = MaterialHost(fixture)
    host.repair_first = True
    host.fail_next = True
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(fixture.database),
        host,
        poll_interval_seconds=0.001,
    )
    checkpoint_path = tmp_path / "generation-checkpoint.sqlite"
    executor = LangGraphResearchExecutor(
        fixture.database, runtime, checkpoint_path=checkpoint_path, expected_lease_owner=OWNER
    )
    pending: dict[str, object] | None = None
    checkpoint_proof: dict[str, object] | None = None
    original_prepared_rows: dict[str, dict[str, object]] | None = None
    captures: list[tuple[threading.Event, dict[str, object], Callable[[], bool]]] = []
    original_submit = runtime.cancellation_projector._executor.submit  # pyright: ignore[reportPrivateUsage]

    def observe_submit[T](operation: Callable[[], T]) -> Future[T]:
        # Delegate the original operation and Future. This does not replace
        # proof, budget, transaction, completion callback or the real two slots.
        future = original_submit(operation)
        finished = threading.Event()
        details: dict[str, object] = {"thread_done": False}

        def record_finished(done: Future[T]) -> None:
            try:
                outcome = done.result()
                if isinstance(outcome, ProjectionRead):
                    details.update(state=outcome.state, limitation=outcome.limitation)
                    if isinstance(outcome.value, RuntimePreparedTurn):
                        details.update(
                            generation=outcome.value.prepared.execution.generation,
                            current_round=outcome.value.request.current_round,
                        )
                else:
                    details["state"] = "unexpected_shape"
            except BaseException as error:
                details["exception_class"] = type(error).__name__
            finally:
                details["thread_done"] = True
                finished.set()

        captures.append((finished, details, future.done))
        future.add_done_callback(record_finished)
        return future

    monkeypatch.setattr(runtime.cancellation_projector._executor, "submit", observe_submit)  # pyright: ignore[reportPrivateUsage]
    try:
        try:
            state = await executor.execute(request)
        except AgentExecutionError as error:
            pending = {**error.provenance().model_dump(mode="json"),
                       "message": str(error), "attempt": error.attempt,
                       "provider_id": error.provider_id, "model": error.model}
            assert error.error_code == "research_method_binding_failed" and error.retryable
            assert str(error) == "Execution preparation was not confirmed"
            # Observe the original late Future's actual completion. close()
            # alone is wait=False and must never be substituted for this proof.
            finished, details, done = captures[-1]
            assert await asyncio.to_thread(finished.wait, 10), "late preparation thread unresolved"
            assert done() and details.get("state") == "ready"
            assert details.get("generation") == 3 and details.get("current_round") == 2
            async with CheckpointStore(checkpoint_path).open() as saver:
                saved = await saver.aget_tuple({"configurable": {"thread_id": request.run_id}})
                assert saved is not None
                channels = saved.checkpoint["channel_values"]
                assert channels["current_round"] == 2
                assert len(channels["rounds"]) == 1
                assert channels.get("graph_round_admission_ref") is not None
                checkpoint_proof = {
                    "checkpoint_id": saved.checkpoint["id"],
                    "current_round": channels["current_round"],
                    "original_ref": channels["graph_round_admission_ref"],
                    "original_future": details,
                    "host_generations_before_resume": [s.generation for s in host.submit_calls],
                }
            assert [s.generation for s in host.submit_calls] == [1, 2]
            # Metadata-only original row capture; no Kernel proof reader is
            # called to prewarm the new Runtime or to assemble another state.
            with fixture.database.session() as session:
                original_prepared_rows = {
                    row.role: {"id": row.output_id, "schema": row.schema_version,
                               "hash": row.payload_hash, "created_at": str(row.created_at)}
                    for row in session.query(RunRoleOutputRecord).filter(
                        RunRoleOutputRecord.run_id == request.run_id,
                        RunRoleOutputRecord.role.in_(("dsh_execution_request:3",
                                                      "dsh_prompt_integrity:3")),
                    ).all()
                }
            await runtime.close()
            runtime = DshWebResearchRuntime(
                DshSessionLinkService(fixture.database), host, poll_interval_seconds=0.001
            )
            executor = LangGraphResearchExecutor(
                fixture.database, runtime, checkpoint_path=checkpoint_path,
                expected_lease_owner=OWNER,
            )
            # One normal official-saver resume, never reset Run/current state,
            # fabricate a locator or increase the original default five seconds.
            state = await executor.resume(request.run_id)
        final_ref = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
        with fixture.database.session() as session:
            final = binding.read_graph_final_in_session(
                session,
                run_id=request.run_id,
                reference=final_ref,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
            last = binding.read_graph_round_in_session(
                session,
                run_id=request.run_id,
                reference=final.receipt.last_admission_ref,
                expected_lease_owner=OWNER,
                require_current_permission=True,
            )
            selected = read_mapped_observation(
                session,
                final.receipt.selected_synthesis_observation_ref,
            )
            latest = read_transport_observation(session, final.receipt.latest_observation_ref)
            current = read_prepared_execution_in_session(session, request.run_id)
            final_prepared_rows = {
                row.role: {"id": row.output_id, "schema": row.schema_version,
                           "hash": row.payload_hash, "created_at": str(row.created_at)}
                for row in session.query(RunRoleOutputRecord).filter(
                    RunRoleOutputRecord.run_id == request.run_id,
                    RunRoleOutputRecord.role.in_(("dsh_execution_request:3",
                                                  "dsh_prompt_integrity:3")),
                ).all()
            }
        if original_prepared_rows is not None:
            assert final_prepared_rows == original_prepared_rows
        assert [submit.generation for submit in host.submit_calls] == [1, 2, 3]
        assert current.execution.generation == 3 and current.execution.request.current_round == 2
        assert last.receipt.logical_round == 2 and last.receipt.observation_kind == "failure"
        assert last.receipt.previous_ref is not None
        assert selected.mapping_input_generation == 2
        assert selected.context.generation == 2
        assert selected.result.rounds[-1].round == 1
        assert final.receipt.native_context.generation == 3
        assert latest.phase == "error" and latest.context.generation == 3
        assert latest.error is not None and latest.error.error_code == "structured_output_invalid"
        assert final.result.causal_case == selected.result.causal_case
        # The original selected hypothesis is intact; deterministic bounded
        # finalization caps its publication probability separately.
        unchanged = {"subjective_probability", "probability_status", "confidence_cap_reason"}
        assert [item.model_dump(exclude=unchanged) for item in final.result.horizons] == [
            item.model_dump(exclude=unchanged) for item in selected.result.horizons
        ]
        assert all(
            item.subjective_probability == 0.5 and item.probability_status == "uncalibrated"
            for item in final.result.horizons
        )
        assert final.result.status == "degraded"
        assert final.result.total_tokens is None and final.result.estimated_cost_usd is None
        _write_observation(
            tmp_path,
            "generation-fallback-observation.json",
            {
                "host_generations": [submit.generation for submit in host.submit_calls],
                "first_pending": pending,
                "checkpoint_recovery": checkpoint_proof,
                "default_binding_wait_seconds": runtime.binding_wait_seconds,
                "original_prepared_rows": original_prepared_rows,
                "final_prepared_rows": final_prepared_rows,
                "final_ref": final_ref.model_dump(mode="json"),
                "last_round": last.receipt.model_dump(mode="json"),
                "selected_mapping_ref": final.receipt.selected_synthesis_observation_ref.model_dump(
                    mode="json"
                ),
                "selected_mapping_generation": selected.mapping_input_generation,
                "latest_failure_ref": final.receipt.latest_observation_ref.model_dump(mode="json"),
                "latest_failure": latest.model_dump(mode="json"),
                "sql": _sql(fixture),
            },
        )
    except Exception as failure:
        _write_observation(
            tmp_path,
            "generation-checkpoint-attempt-failure.json",
            {
                "first_pending": pending,
                "checkpoint_recovery": checkpoint_proof,
                "host_generations": [s.generation for s in host.submit_calls],
                "failure_class": type(failure).__name__,
                "failure_message": str(failure),
                "failure_provenance": failure.provenance().model_dump(mode="json")
                if isinstance(failure, AgentExecutionError) else None,
                "original_prepared_rows": original_prepared_rows,
                "default_binding_wait_seconds": runtime.binding_wait_seconds,
                "sql": _sql(fixture),
            },
        )
        raise
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_evidence_only_prior_round_does_not_grant_failure_fallback(
    evidence_only_inputs: PreparedInputs,
    tmp_path: Path,
):
    fixture = evidence_only_inputs
    request = _active(fixture)
    host = MaterialHost(fixture)
    host.repair_first = True
    host.fail_next = True
    host.fail_from_generation = 2
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(fixture.database),
        host,
        poll_interval_seconds=0.001,
    )
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(fixture.database),
        expected_lease_owner=OWNER,
    )
    try:
        with pytest.raises(ValueError, match="prior_synthesis_not_admissible"):
            await graph.ainvoke(initial_research_state(request))
        with fixture.database.session() as session:
            row = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=request.run_id,
                    role="research_graph_round:1",
                )
                .one()
            )
            first = binding.ResearchGraphRoundAdmission.model_validate_json(row.payload_json)
            original = read_mapped_observation(session, first.observation_ref)
        assert [submit.generation for submit in host.submit_calls] == [1, 2]
        assert original.selection.kind == "evidence_only"
        assert not original.result.causal_case and not original.result.horizons
        sql = _sql(fixture)
        assert sql["decision_snapshot_id"] is None
        assert (
            "research_graph_round:2" not in sql["roles"]
            and "research_graph_final" not in sql["roles"]
        )
        assert any(role.startswith("research_transport:") for role in sql["roles"])
        _write_observation(
            tmp_path,
            "evidence-only-refusal-observation.json",
            {
                "host_generations": [submit.generation for submit in host.submit_calls],
                "first_original_admission": first.model_dump(mode="json"),
                "selected_kind": original.selection.kind,
                "sql": sql,
            },
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_native_rows", [False, True])
async def test_legacy_dispatch_cannot_admit_after_actual_runtime_await_restores_native_rows(
    inputs: PreparedInputs,
    tmp_path: Path,
    restore_native_rows: bool,
):
    request = _active(inputs)
    actual = request_for_state(
        initial_research_state(request), eligibility_cutoff=request.pit_cutoff_at
    )
    digest = research_request_hash(actual)
    sid, rid = DshSessionLinkService.deterministic_ids(request.run_id, digest)
    predicted = inputs.submit.model_copy(
        update={
            "request_hash": digest,
            "deterministic_session_id": sid,
            "deterministic_request_id": rid,
        }
    )
    # Prepare a genuine controlled typed MCP reply using the original Factory
    # source. Only Evidence is durably admitted here. Fact admission is the
    # semantic writer under test. No Provider/RPC or classifier mock is used.
    response = await MaterialHost(inputs).result(predicted)
    with inputs.database.session() as session:
        originals = [
            {
                column.key: getattr(row, column.key)
                for column in RunRoleOutputRecord.__table__.columns
            }
            for row in session.query(RunRoleOutputRecord).filter_by(run_id=request.run_id)
        ]
        assert len(originals) == 3
        session.query(RunRoleOutputRecord).filter_by(run_id=request.run_id).delete()
    with inputs.database.session() as session:
        assert binding.graph_requires_native_binding_in_session(session, request.run_id) is False
    before = _sql(inputs)
    assert not before["materials"]["research_facts"]

    class CachedHost(PreparedHost):
        async def result(self, payload: DshSessionSubmit):
            self.result_calls += 1
            assert payload == predicted, "real Runtime must invoke the exact prepared test reply"
            return response

    host = CachedHost(inputs)
    actual_runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database),
        host,
        poll_interval_seconds=0.001,
    )

    class RestoringRuntime:
        runtime_id = actual_runtime.runtime_id
        runtime_version = actual_runtime.runtime_version
        profile_ref = actual_runtime.profile_ref

        async def execute(
            self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None
        ) -> ResearchSessionResult:
            result = await actual_runtime.execute(request, trace_sink=trace_sink)
            if restore_native_rows:
                # Real second SQLite transaction while Graph still awaits this
                # runtime call; restore byte-for-byte original Factory rows.
                with inputs.database.session() as session:
                    for body in originals:
                        session.add(RunRoleOutputRecord(**body))
            return result

        async def close(self) -> None:
            await actual_runtime.close()

    runtime = RestoringRuntime()
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(inputs.database),
        expected_lease_owner=OWNER,
    )
    error = None
    state = None
    try:
        try:
            state = await graph.ainvoke(initial_research_state(request))
        except ValueError as exc:
            error = str(exc)
        after = _sql(inputs)
        _write_observation(
            tmp_path,
            "legacy-dispatch-observation.json",
            {
                "restore_native_rows": restore_native_rows,
                "original_factory_row_ids": [body["output_id"] for body in originals],
                "actual_host_submits": [item.model_dump(mode="json") for item in host.submit_calls],
                "actual_host_result_hash": response.result_hash,
                "error": error,
                "before": before,
                "after": after,
            },
        )
        assert len(host.submit_calls) == 1 and host.result_calls == 1
        if restore_native_rows:
            assert error is not None
            assert after["materials"] == before["materials"], (
                "cached legacy dispatch must not commit new Fact/lineage "
                "under restored V2 footprints"
            )
            assert after["decision_snapshot_id"] is None
        else:
            assert error is None and state is not None
            assert len(after["materials"]["research_facts"]) == 1
            assert after["decision_snapshot_id"] is not None
            assert state.get("graph_final_projection_ref") is None
    finally:
        await runtime.close()
