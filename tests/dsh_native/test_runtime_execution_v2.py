"""Offline Runtime transport and genuine SQLite execution preparation boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, TypedDict

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionResult,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    EvidenceRequirement,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    TASK_ROLE,
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ResearchExecutionPreparationService,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime as runtime_module
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector
from packages.runtime_adapters.dsh_runtime.profile import (
    PROMPT_POLICY_HASH,
    build_synthesis_tool_repair_prompt,
    build_trusted_web_research_prompt,
)
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    prepare_runtime_turn_in_session,
    read_runtime_task_anchor,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    FakeHostClient,
    _completed_result_with_repairable_synthesis,  # pyright: ignore[reportPrivateUsage]
    _database,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.dsh_native.test_execution_preparation_v2 import (
    PreparedInputs,
    _snapshot,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None and inputs is not None


def _observe(path: Path, body: dict[str, Any]) -> None:
    (path / "observation.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _sql_repairable_result(inputs: PreparedInputs, payload: DshSessionSubmit) -> DshSessionResult:
    """A controlled transcript backed by an actual offline Evidence admission."""
    result = _completed_result_with_repairable_synthesis(payload)
    events = json.loads(result.events_json)
    capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
        "structuredContent"]
    body = capability["evidence_candidates"][0]
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, payload.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    requirement = next(item for item in bundle.task.request.evidence_requirements
                       if item.requirement_id == body["requirement_id"])
    cutoff = inputs.request.pit_cutoff_at
    body.update(published_at=(cutoff - timedelta(seconds=2)).isoformat(),
                observed_at=(cutoff - timedelta(seconds=1)).isoformat(),
                received_at=cutoff.isoformat())
    candidate = EvidenceCandidate.model_validate(body)
    candidate.content_hash = research_evidence_content_hash(
        requirement_id=candidate.requirement_id, kind=candidate.kind,
        authority=candidate.authority, source_id=candidate.source_id,
        source_url=str(candidate.source_url), published_at=candidate.published_at,
        excerpt=candidate.excerpt, structured_payload_ref=candidate.structured_payload_ref,
    )
    admitted = ResearchEvidenceService(inputs.database).accept_candidates(
        run_id=payload.run_id, capability_id="replay.research", candidates=[candidate],
        requirements={requirement.requirement_id: EvidenceRequirement.model_validate(
            requirement.model_dump(mode="json"))}, cutoff_at=cutoff,
    )
    assert len(admitted) == 1 and admitted[0].quality == "accepted"
    capability["evidence_candidates"] = [admitted[0].model_dump(mode="json")]
    encoded = json.dumps(events)
    return result.model_copy(update={"events_json": encoded, "result_hash": hashlib.sha256(
        (result.final_response + "\0" + encoded).encode()).hexdigest()})


@pytest.mark.asyncio
@pytest.mark.parametrize("body_kind", [
    "arbitrary", "missing", "foreign", "late", "hash", "unaccepted", "legal",
])
async def test_v2_repair_replay_requires_known_template_and_original_evidence_scope(
    inputs: PreparedInputs, tmp_path: Path, body_kind: str,
):
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    service = ResearchExecutionPreparationService(inputs.database)
    prefix = render_prepared_research_policy(bundle, inputs.request.allowed_capabilities) + "\n\n"
    version = f"dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}"
    class PreparationIdentity(TypedDict):
        runtime_id: str
        runtime_version: str
        profile_ref: str
        prompt_policy_hash: str

    common: PreparationIdentity = {"runtime_id": "dsh", "runtime_version": version,
        "profile_ref": "decision-research.web.v1", "prompt_policy_hash": PROMPT_POLICY_HASH}
    service.prepare(inputs.request, inputs.submit, inputs.upstream,
                    prefix + build_trusted_web_research_prompt(inputs.request), **common)
    first_result = await PreparedHost(inputs).result(inputs.submit)
    DshSessionLinkService(inputs.database).complete(DshSessionCompletion(
        schema_version="dsh-session-completion.v1", run_id=inputs.request.run_id,
        dsh_session_id=inputs.submit.deterministic_session_id, terminal_status="completed",
        generation=1, last_seq=first_result.last_seq, trace_ref=first_result.trace_ref,
        result_ref=f"dsh-host://runs/{inputs.request.run_id}/result",
        result_hash=first_result.result_hash, completed_at=first_result.finished_at, error=None,
    ))
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        inputs.request.run_id, inputs.submit.request_hash, 2)
    submit = inputs.submit.model_copy(update={"generation": 2,
        "deterministic_session_id": session_id, "deterministic_request_id": request_id,
        "prompt_ref": f"hub://runs/{inputs.request.run_id}/prompts/2"})
    ids = [inputs.request.input_evidence[0].evidence_id]
    if body_kind == "missing":
        ids.append("controlled-missing-repair-evidence")
    if body_kind in {"foreign", "late", "hash", "unaccepted"}:
        controlled = _sql_repairable_result(inputs, inputs.submit)
        assert controlled.result_hash
        from packages.kernel.decision_hub_kernel.persistence.db import ResearchEvidenceRecord
        with inputs.database.session() as session:
            row = session.get(ResearchEvidenceRecord, "ev-partial-provider-failure")
            assert row is not None
            if body_kind == "foreign":
                row.run_id = "different-controlled-run"
            elif body_kind == "late":
                row.received_at = inputs.request.pit_cutoff_at + timedelta(seconds=1)
            elif body_kind == "hash":
                row.content_hash = "f" * 64
            else:
                row.quality = "candidate"
        ids.append("ev-partial-provider-failure")
    body = ("Controlled arbitrary repair body." if body_kind == "arbitrary" else
            build_synthesis_tool_repair_prompt(inputs.request, session_id,
                [{"loc": [], "type": "controlled", "msg": "declared fixture error"}], ids))
    saved = service.prepare(inputs.request, submit, inputs.upstream, prefix + body,
                            turn_kind="synthesis_repair", **common)
    before = _snapshot(inputs.database, inputs.request.run_id)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    error = None
    try:
        try:
            await runtime.execute(inputs.request)
        except AgentExecutionError as exc:
            error = exc.cause_code
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"body_kind": body_kind, "error": error, "before": before,
            "after": after, "submits": len(host.submit_calls),
            "legitimate_kernel_declaration": saved.execution.model_dump(mode="json")})
        if body_kind == "legal":
            assert error is None and [p.generation for p in host.submit_calls] == [2]
        else:
            expected = ("research_runtime_repair_prompt_invalid" if body_kind == "arbitrary"
                        else "research_runtime_repair_evidence_invalid")
            assert error == expected
            assert host.submit_calls == [] and before == after
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical_body", [False, True])
async def test_v2_declared_profile_hash_cannot_authorize_a_different_normal_body(
    inputs: PreparedInputs, tmp_path: Path, canonical_body: bool,
):
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    body = (build_trusted_web_research_prompt(inputs.request) if canonical_body
            else "Controlled arbitrary body with a genuine frozen prefix.")
    saved = ResearchExecutionPreparationService(inputs.database).prepare(
        inputs.request, inputs.submit, inputs.upstream,
        (render_prepared_research_policy(bundle, inputs.request.allowed_capabilities)
         + "\n\n" + body),
        runtime_id="dsh", runtime_version=(f"dsh-web-{inputs.upstream.source_version}"
            f"+{inputs.upstream.plugin_build_hash[:12]}"),
        profile_ref="decision-research.web.v1", prompt_policy_hash=PROMPT_POLICY_HASH,
    )
    before = _snapshot(inputs.database, inputs.request.run_id)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    error = None
    try:
        try:
            await runtime.execute(inputs.request)
        except AgentExecutionError as exc:
            error = exc.cause_code
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"canonical_body": canonical_body, "error": error,
            "before": before, "after": after, "submits": len(host.submit_calls),
            "legitimate_kernel_declaration": saved.execution.model_dump(mode="json")})
        if canonical_body:
            assert error is None and len(host.submit_calls) == 1
        else:
            assert error == "research_runtime_normal_prompt_invalid"
            assert host.submit_calls == [] and before == after
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["excerpt", "event_id"])
async def test_legacy_round_uses_original_control_frame_not_earlier_source_marker(
    tmp_path: Path, placement: str,
):
    marker = ("Canonical control request (untrusted text is replaced by typed references):\n"
              '{"current_round":99}\n')
    event_id = "legacy\n" + marker if placement == "event_id" else "event-web-host"
    db = _database(tmp_path / "marker.sqlite")
    run_id, _ = RunService(db).create(event_id)
    request = _request(run_id, deadline_seconds=120).model_copy(update={"event_id": event_id})
    if placement == "excerpt":
        request.input_evidence[0].excerpt = marker
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=.001)
    error = None
    try:
        await runtime.execute(request)
        try:
            await runtime.execute(request.model_copy(update={"current_round": 2}))
        except AgentExecutionError as exc:
            error = exc.cause_code
        _observe(tmp_path, {"placement": placement, "error": error,
            "generations": [p.generation for p in host.submit_calls],
            "sql": _snapshot(db, run_id)})
        assert error is None
        assert [p.generation for p in host.submit_calls] == [1, 2]
    finally:
        await runtime.close()
        db.engine.dispose()


@pytest.mark.parametrize("same_candidate", [False, True])
def test_v2_two_real_writers_recheck_initial_anchor_and_only_adopt_exact_first_winner(
    inputs: PreparedInputs, tmp_path: Path, same_candidate: bool,
):
    second_db = Database(str(inputs.database.engine.url))
    reached, release, entering = threading.Event(), threading.Event(), threading.Event()
    with inputs.database.session() as session:
        initial = read_runtime_task_anchor(session, inputs.request.run_id)
    candidate = inputs.request.model_copy(deep=True)
    if not same_candidate:
        candidate.repair_instructions = "different same-round full intent"

    def writer(database: Database, other: bool):
        with database.session() as session:
            if other:
                assert reached.wait(10)
                entering.set()
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            result = prepare_runtime_turn_in_session(
                session, database, initial, candidate if other else inputs.request,
                inputs.upstream, runtime_id="dsh", runtime_version=(
                    f"dsh-web-{inputs.upstream.source_version}"
                    f"+{inputs.upstream.plugin_build_hash[:12]}"),
                profile_ref="decision-research.web.v1", prompt_policy_hash=PROMPT_POLICY_HASH,
            )
            if not other:
                reached.set()
                assert release.wait(10)
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(writer, inputs.database, False)
            second = executor.submit(writer, second_db, True)
            assert entering.wait(10)
            release.set()
            winner = first.result(timeout=15)
            error = None
            follower = None
            try:
                follower = second.result(timeout=15)
            except ValueError as exc:
                error = str(exc)
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"same_candidate": same_candidate, "error": error,
            "winner": winner.prepared.execution.model_dump(mode="json"),
            "follower": follower.prepared.execution.model_dump(mode="json") if follower else None,
            "after": after, "two_distinct_engines": second_db.engine is not inputs.database.engine})
        assert len(after["prompts"]) == 1 and len(after["roles"]) == 6
        if same_candidate:
            assert follower is not None and follower.prepared == winner.prepared
        else:
            assert error == "research_execution_generation_conflict" and follower is None
    finally:
        release.set()
        second_db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_existing_repair_then_next_round_uses_next_generation(tmp_path: Path, repair: bool):
    db = _database(tmp_path / "legacy.sqlite")
    run, _ = RunService(db).create("event-web-host", "runtime-generation-control")
    request = _request(run, deadline_seconds=120)
    request.evidence_requirements[0].importance = "soft"
    request.execution_budget.max_structured_repairs = int(repair)

    class Host(FakeHostClient):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            if repair:
                return _completed_result_with_repairable_synthesis(payload)
            return await super().result(payload)

    host = Host()
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=.001)
    error = None
    results = []
    try:
        results.append(await runtime.execute(request))
        results.append(await runtime.execute(request.model_copy(update={"current_round": 2})))
    except Exception as exc:
        error = {"type": type(exc).__name__, "cause": getattr(exc, "cause_code", None)}
    finally:
        observation: dict[str, Any] = {"repair": repair, "submitted_generations": [
            p.generation for p in host.submit_calls], "error": error,
            "statuses": [r.status for r in results], "sql": _snapshot(db, run)}
        await runtime.close()
        db.engine.dispose()
        observation["cleanup"] = "runtime/projector closed; database engine disposed"
        _observe(tmp_path, observation)
    assert error is None, observation
    assert observation["submitted_generations"] == ([1, 2, 3] if repair else [1, 2])


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_existing_two_runs_keep_their_own_actual_host_version(
    tmp_path: Path, concurrent: bool,
):
    db = _database(tmp_path / "versions.sqlite")
    runs = [RunService(db).create("event-web-host", f"runtime-version-{i}")[0] for i in range(2)]
    first_submitted, second_submitted = asyncio.Event(), asyncio.Event()
    identities = [DshUpstreamIdentity(
        source_commit=letter * 40, source_version=f"0.1.{i}", package_versions={},
        plugin_build_hash=letter * 64,
    ) for i, letter in enumerate("ab")]

    class Host(FakeHostClient):
        async def readiness(self) -> DshHostReadiness:
            base = await super().readiness()
            task = asyncio.current_task()
            assert task is not None
            return base.model_copy(update={"upstream_identity": identities[
                1 if task.get_name() == "version-B" else 0]})

        async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
            accepted = await super().submit(payload)
            if payload.run_id == runs[0]:
                first_submitted.set()
                if concurrent:
                    await second_submitted.wait()
            else:
                second_submitted.set()
            return accepted

    host = Host()
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=.001)
    results = []
    try:
        first = asyncio.create_task(runtime.execute(_request(runs[0])), name="version-A")
        if concurrent:
            await first_submitted.wait()
            second = asyncio.create_task(
                runtime.execute(_request(runs[1])), name="version-B"
            )
            results = list(await asyncio.gather(first, second))
        else:
            results = [await first]
        methods = [ResearchMethodService(db).get(run) for run in runs[:len(results)]]
        versions = [result.runtime_version for result in results]
        expected = [f"dsh-web-{item.source_version}+{item.plugin_build_hash[:12]}"
                    for item in identities[:len(results)]]
        _observe(tmp_path, {"concurrent": concurrent, "returned_versions": versions,
            "expected_versions": expected, "methods": methods,
            "sql": [_snapshot(db, run) for run in runs[:len(results)]],
            "calls": [(p.run_id, p.generation) for p in host.submit_calls],
            "controlled_results": [result.status for result in results]})
        assert versions == expected
    finally:
        await runtime.close()
        db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("product_cancel", [False, True])
async def test_v2_async_cancellation_only_stops_authorized_active_submission(
    inputs: PreparedInputs, tmp_path: Path, product_cancel: bool,
):
    entered = asyncio.Event()

    class Host(PreparedHost):
        async def status(self, payload: DshSessionSubmit):
            entered.set()
            await asyncio.Event().wait()
            return self.status_value(payload, "running", 1)

    host = Host(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    task = asyncio.create_task(runtime.execute(inputs.request))
    try:
        await asyncio.wait_for(entered.wait(), timeout=20)
        before = _snapshot(inputs.database, inputs.request.run_id)
        if product_cancel:
            with inputs.database.session() as session:
                run = session.get(RunRecord, inputs.request.run_id)
                assert run is not None
                run.status = "cancelled"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"product_cancel": product_cancel, "before": before, "after": after,
            "actual_host_cancels": host.cancelled})
        assert host.cancelled == ([inputs.request.run_id] if product_cancel else [])
        assert before["roles"] == after["roles"] and before["prompts"] == after["prompts"]
        assert after["link"]["state"] == ("cancelled" if product_cancel else "admitted")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_v2_missing_current_sidecar_rejects_readiness_and_does_not_backfill(
    inputs: PreparedInputs, tmp_path: Path,
):
    inputs.prepare()
    with inputs.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role="dsh_prompt_integrity:1").one()
        session.delete(row)
    before = _snapshot(inputs.database, inputs.request.run_id)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(inputs.request)
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"before": before, "after": after, "error": error.value.cause_code,
            "readiness": host.readiness_calls, "submits": len(host.submit_calls)})
        assert host.readiness_calls == 0 and host.submit_calls == [] and before == after
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "completed", "failed"])
async def test_v2_terminal_run_never_prepares_or_calls_host(
    inputs: PreparedInputs, tmp_path: Path, status: str,
):
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id)
        assert run is not None
        run.status = status
    before = _snapshot(inputs.database, inputs.request.run_id)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(inputs.request)
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"status": status, "before": before, "after": after,
            "error": error.value.cause_code, "readiness": host.readiness_calls})
        assert error.value.cause_code == "research_execution_run_not_active"
        assert host.readiness_calls == 0 and host.submit_calls == [] and before == after
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_public_lane_real_threads_hold_two_slots_and_close_without_reuse(
    tmp_path: Path,
):
    db = _database(tmp_path / "lane.sqlite")
    lane = DshCancellationProjector(DshSessionLinkService(db))
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]
    thread_ids = []

    def held(session: Session, index: int):
        session.connection().exec_driver_sql("SELECT 1")
        thread_ids.append(threading.get_ident())
        entered[index].set()
        try:
            assert release[index].wait(5)
            return index
        finally:
            finished[index].set()

    try:
        pending = await asyncio.gather(*[lane.read(
            lambda session, index=index: held(session, index), timeout_seconds=.01,
        ) for index in range(2)])
        assert all(item.state == "pending" for item in pending)
        assert all(item.is_set() for item in entered)
        blocked = await lane.write(lambda session: session.connection().exec_driver_sql(
            "SELECT 1").scalar_one(), timeout_seconds=.1)
        assert blocked.limitation == "cancellation_projection_busy"
        release[0].set()
        while not finished[0].is_set():
            await asyncio.sleep(.001)
        await asyncio.sleep(.01)
        successful = await lane.write(lambda session: session.connection().exec_driver_sql(
            "SELECT 1").scalar_one(), timeout_seconds=.1)
        assert successful.state == "ready" and successful.value == 1
        await lane.close()
        closed = await lane.read(lambda _session: 1)
        assert closed.limitation == "cancellation_projection_closed"
        release[1].set()
        while not finished[1].is_set():
            await asyncio.sleep(.001)
        _observe(tmp_path, {"actual_thread_ids": thread_ids, "two_pending": [
            item.limitation for item in pending], "third_blocked": blocked.limitation,
            "after_real_done": successful.state, "closed": closed.limitation,
            "actual_finished": [item.is_set() for item in finished]})
        assert len(set(thread_ids)) == 2
    finally:
        for event in release:
            event.set()
        await lane.close()
        db.engine.dispose()


@pytest.mark.asyncio
async def test_v2_runtime_write_failure_after_real_five_flush_rolls_everything_back(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    original = runtime_module.prepare_runtime_turn_in_session
    observed: dict[str, Any] = {}

    def failed(*args: Any, **kwargs: Any):
        value = original(*args, **kwargs)
        observed["generation_after_all_five_flush"] = value.prepared.execution.generation
        raise ValueError("controlled_runtime_after_flush_failure")

    monkeypatch.setattr(runtime_module, "prepare_runtime_turn_in_session", failed)
    before = _snapshot(inputs.database, inputs.request.run_id)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    try:
        with pytest.raises(AgentExecutionError) as error:
            await runtime.execute(inputs.request)
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"before": before, "after": after, "actual_callback": observed,
            "error": error.value.cause_code, "host_submits": len(host.submit_calls)})
        assert observed == {"generation_after_all_five_flush": 1}
        assert error.value.cause_code == "controlled_runtime_after_flush_failure"
        assert before == after and host.submit_calls == []
    finally:
        await runtime.close()


class PreparedHost(FakeHostClient):
    def __init__(self, fixture: PreparedInputs) -> None:
        super().__init__()
        self.fixture = fixture
        self.readiness_calls = 0
        self.result_calls = 0

    async def readiness(self) -> DshHostReadiness:
        self.readiness_calls += 1
        base = await super().readiness()
        return base.model_copy(update={"upstream_identity": self.fixture.upstream})

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        self.result_calls += 1
        return await super().result(payload)


@pytest.mark.asyncio
async def test_v2_first_runtime_preparation_has_real_five_receipts_and_host_projection(
    inputs: PreparedInputs, tmp_path: Path,
):
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    before = _snapshot(inputs.database, inputs.request.run_id)
    error = None
    result = None
    prepared = None
    try:
        result = await runtime.execute(inputs.request)
        with inputs.database.session() as session:
            prepared = read_prepared_execution_in_session(session, inputs.request.run_id)
    except Exception as exc:
        error = {"type": type(exc).__name__, "cause": getattr(exc, "cause_code", None)}
    finally:
        _observe(tmp_path, {"before": before, "after": _snapshot(inputs.database,
            inputs.request.run_id), "error": error, "readiness": host.readiness_calls,
            "submits": [p.model_dump(mode="json") for p in host.submit_calls],
            "result_calls": host.result_calls, "status": result.status if result else None,
            "host_prompt": prepared.host_prompt().model_dump(mode="json") if prepared else None})
        await runtime.close()
    assert error is None
    assert result is not None and result.status == "degraded"
    assert prepared is not None and prepared.execution.generation == 1
    assert prepared.host_prompt().schema_version == "dsh-session-prompt.v1"
    assert len(host.submit_calls) == 1 and host.result_calls == 1


@pytest.mark.asyncio
async def test_v2_actual_complete_bundle_read_has_bounded_real_duration(
    inputs: PreparedInputs, tmp_path: Path,
):
    lane = DshCancellationProjector(DshSessionLinkService(inputs.database))
    started = time.monotonic()
    try:
        result = await lane.read(lambda session: read_runtime_task_anchor(
            session, inputs.request.run_id), timeout_seconds=2)
        _observe(tmp_path, {"state": result.state, "limitation": result.limitation,
            "elapsed_seconds": time.monotonic()-started,
            "before_after": _snapshot(inputs.database, inputs.request.run_id)})
        assert result.state == "ready" and result.value is not None
        assert result.value.generation == 0 and result.value.bundle_hash is not None
    finally:
        await lane.close()


def _damage_task(inputs: PreparedInputs) -> None:
    with inputs.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role=TASK_ROLE).one()
        body = json.loads(row.payload_json)
        body["request"]["evidence_requirements"][-1]["description"] += " changed"
        row.payload_json = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["readiness", "submit", "result"])
async def test_v2_drift_rejects_next_rpc_or_semantics_and_keeps_actual_transport(
    inputs: PreparedInputs, tmp_path: Path, phase: str,
):
    class Host(PreparedHost):
        async def readiness(self) -> DshHostReadiness:
            returned = await super().readiness()
            if phase == "readiness":
                _damage_task(inputs)
            return returned

        async def submit(self, payload: DshSessionSubmit) -> DshSessionAccepted:
            returned = await super().submit(payload)
            if phase == "submit":
                _damage_task(inputs)
            return returned

        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            returned = await super().result(payload)
            if phase == "result":
                _damage_task(inputs)
            return returned

    host = Host(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    error = None
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await runtime.execute(inputs.request)
        error = caught.value.cause_code
        saved = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"phase": phase, "error": error, "after": saved,
            "submits": len(host.submit_calls), "results": host.result_calls})
        if phase == "readiness":
            assert host.submit_calls == [] and saved["link"] is None
            assert len(saved["roles"]) == 3
        elif phase == "submit":
            assert len(host.submit_calls) == 1 and host.result_calls == 0
            assert saved["link"]["accepted_at"] != "None"
        else:
            assert host.result_calls == 1 and saved["link"]["state"] == "completed"
            assert saved["link"]["result_hash"] != "None"
            assert saved["link"]["result_ref"].startswith("dsh-host://")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_v2_same_round_restart_reuses_exact_prompt_full_request_and_time(
    inputs: PreparedInputs, tmp_path: Path,
):
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    try:
        await runtime.execute(inputs.request)
        before = _snapshot(inputs.database, inputs.request.run_id)
    finally:
        await runtime.close()
    retry_host = PreparedHost(inputs)
    retry = DshWebResearchRuntime(DshSessionLinkService(inputs.database), retry_host,
                                 poll_interval_seconds=.001)
    try:
        await retry.execute(inputs.request)
        after = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"before": before, "after": after,
            "replayed": [p.model_dump(mode="json") for p in retry_host.submit_calls]})
        assert before["roles"] == after["roles"]
        assert before["prompts"] == after["prompts"]
        assert [p.generation for p in retry_host.submit_calls] == [1]
        changed = inputs.request.model_copy(update={"repair_instructions": "new same-round body"})
        with pytest.raises(AgentExecutionError) as conflict:
            await retry.execute(changed)
        assert conflict.value.cause_code == "research_execution_generation_conflict"
        assert len(retry_host.submit_calls) == 1
    finally:
        await retry.close()


@pytest.mark.asyncio
async def test_v2_late_write_commit_sends_no_unconfirmed_rpc_then_exact_retry(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    original = runtime_module.prepare_runtime_turn_in_session
    reached, release, finished = threading.Event(), threading.Event(), threading.Event()
    measured: dict[str, Any] = {}

    def delayed(*args: Any, **kwargs: Any):
        started = time.monotonic()
        try:
            value = original(*args, **kwargs)
            measured["prepared_callback_seconds"] = time.monotonic() - started
            reached.set()
            assert release.wait(10)
            return value
        finally:
            finished.set()

    monkeypatch.setattr(runtime_module, "prepare_runtime_turn_in_session", delayed)

    class Host(PreparedHost):
        async def readiness(self) -> DshHostReadiness:
            returned = await super().readiness()
            runtime.binding_wait_seconds = .01
            runtime.preparation_confirmation_wait_seconds = .01
            return returned

    host = Host(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    try:
        with pytest.raises(AgentExecutionError) as pending:
            await runtime.execute(inputs.request)
        assert pending.value.cause_code == "cancellation_projection_wait_elapsed"
        assert host.submit_calls == []
        for _ in range(500):
            if reached.is_set():
                break
            await asyncio.sleep(.01)
        assert reached.is_set(), "real callback never reached five-piece flush"
        release.set()
        for _ in range(500):
            if finished.is_set():
                break
            await asyncio.sleep(.01)
        assert finished.is_set()
        # Thread callback completion precedes context commit by a few instructions.
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            late = read_prepared_execution_in_session(session, inputs.request.run_id)
        committed = _snapshot(inputs.database, inputs.request.run_id)
        monkeypatch.setattr(runtime_module, "prepare_runtime_turn_in_session", original)
        retry_host = PreparedHost(inputs)
        runtime.client = retry_host
        runtime.binding_wait_seconds = 5
        runtime.preparation_confirmation_wait_seconds = 30
        await runtime.execute(inputs.request)
        after = _snapshot(inputs.database, inputs.request.run_id)
        original_roles = {row["output_id"]: row for row in committed["roles"]}
        after_roles = {row["output_id"]: row for row in after["roles"]}
        added_audit = [row for key, row in after_roles.items() if key not in original_roles]
        _observe(tmp_path, {"pending": pending.value.cause_code, "measured": measured,
            "unconfirmed_submits": len(host.submit_calls), "late": committed, "after": after,
            "added_audit_rows": added_audit,
            "retry_generations": [p.generation for p in retry_host.submit_calls]})
        assert late.execution.generation == 1
        assert len(after_roles) == len(after["roles"])
        for output_id, original_row in original_roles.items():
            # Include whole payload, hash, schema and original creation time.
            assert after_roles[output_id] == original_row
        assert committed["prompts"] == after["prompts"]
        assert added_audit
        for row in added_audit:
            assert (
                row["role"].startswith("research_transport:")
                and row["schema"] == "research-transport-observation.v1"
            ) or (
                row["role"].startswith("research_observed:")
                and row["schema"] == "research-mapped-observation.v1"
            )
        assert [p.generation for p in retry_host.submit_calls] == [1]
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_v2_repair_round_one_then_round_two_is_generation_three(
    inputs: PreparedInputs, tmp_path: Path,
):
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    request = inputs.request.model_copy(deep=True)
    request.evidence_requirements = [next(item for item in
        [EvidenceRequirement.model_validate(x.model_dump(mode="json"))
         for x in bundle.task.request.evidence_requirements] if item.importance == "soft")]
    request.execution_budget.max_structured_repairs = 1

    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            self.result_calls += 1
            return _sql_repairable_result(inputs, payload)

    host = Host(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    try:
        first = await runtime.execute(request)
        second = await runtime.execute(request.model_copy(update={"current_round": 2}))
        _observe(tmp_path, {"generations": [p.generation for p in host.submit_calls],
            "statuses": [first.status, second.status],
            "sql": _snapshot(inputs.database, request.run_id)})
        assert [p.generation for p in host.submit_calls] == [1, 2, 3]
        with inputs.database.session() as session:
            turns = [read_prepared_execution_in_session(session, request.run_id, n)
                     for n in range(1, 4)]
        assert [p.execution.request.current_round for p in turns] == [1, 1, 2]
        assert [p.execution.turn_kind for p in turns] == [
            "research_round", "synthesis_repair", "research_round"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_v2_persisted_repair_quota_survives_restart_and_exact_retry(
    inputs: PreparedInputs, tmp_path: Path,
):
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    request = inputs.request.model_copy(deep=True)
    request.evidence_requirements = [EvidenceRequirement.model_validate(next(
        item for item in bundle.task.request.evidence_requirements if item.importance == "soft"
    ).model_dump(mode="json"))]
    request.execution_budget.max_structured_repairs = 1

    class Host(PreparedHost):
        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            self.result_calls += 1
            # An actual malformed controlled transcript on the new research turn;
            # accepted/result identity remains the real incoming generation.
            value = _sql_repairable_result(inputs, payload)
            if payload.generation == 3:
                events = json.loads(value.events_json)
                events[-2]["payload"]["event"]["seq"] = 5
                events[-1]["payload"]["event"]["seq"] = 6
                candidate = events[-1]["payload"]["event"]["data"]["message"]["content"][0][
                    "structuredContent"]
                candidate["horizons"][0]["evidence_refs"] = ["ev-wrong-attested-ref"]
                encoded = json.dumps(events)
                value = value.model_copy(update={"events_json": encoded, "last_seq": 6,
                    "result_hash": hashlib.sha256(("\0" + encoded).encode()).hexdigest()})
            return value

    first_host = Host(inputs)
    first = DshWebResearchRuntime(DshSessionLinkService(inputs.database), first_host,
                                 poll_interval_seconds=.001)
    try:
        await first.execute(request)
        before_restart = _snapshot(inputs.database, request.run_id)
        assert [p.generation for p in first_host.submit_calls] == [1, 2]
    finally:
        await first.close()
    host = Host(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host,
                                   poll_interval_seconds=.001)
    try:
        await runtime.execute(request)
        replayed = _snapshot(inputs.database, request.run_id)
        _observe(tmp_path, {"before_restart": before_restart, "replayed": replayed})
        original_roles = {row["output_id"]: row for row in before_restart["roles"]}
        replayed_roles = {row["output_id"]: row for row in replayed["roles"]}
        assert len(replayed_roles) == len(replayed["roles"])
        for output_id, original_row in original_roles.items():
            # Whole original payload/hash/schema/creation time must survive retry.
            assert replayed_roles[output_id] == original_row
        added = [row for key, row in replayed_roles.items() if key not in original_roles]
        original_contexts = [json.loads(row["payload"])["context"]
            for row in original_roles.values()
            if row["schema"] == "research-transport-observation.v1"
            and json.loads(row["payload"])["phase"] == "result"
            and json.loads(row["payload"])["context"]["generation"] == 2]
        assert len(original_contexts) == 1 and len(added) == 2
        assert [p.generation for p in host.submit_calls] == [2]
        expected_statuses = [host.status_value(host.submit_calls[0], state, seq).model_dump(
            mode="json") for state, seq in [("running", 2), ("completed", 3)]]
        actual_statuses: list[dict[str, Any]] = []
        for row in added:
            # A restarted controlled Host emits fresh status observations. Only
            # those exact correlated replies may be added; no core row is replaced.
            body = json.loads(row["payload"])
            assert row["role"].startswith("research_transport:")
            assert row["schema"] == "research-transport-observation.v1"
            assert row["hash"] == hashlib.sha256(row["payload"].encode()).hexdigest()
            assert body["phase"] == "status" and body["error"] is None
            assert body["context"] == original_contexts[0]
            actual_statuses.append(body["reply"])
        assert sorted(actual_statuses, key=lambda x: x["last_seq"]) == expected_statuses
        assert before_restart["link"] == replayed["link"]
        assert before_restart["prompts"] == replayed["prompts"]
        with pytest.raises(AgentExecutionError) as exhausted:
            await runtime.execute(request.model_copy(update={"current_round": 2}))
        after = _snapshot(inputs.database, request.run_id)
        _observe(tmp_path, {"before_restart": before_restart, "replayed": replayed,
            "after": after, "error": exhausted.value.cause_code,
            "submits_after_restart": [p.generation for p in host.submit_calls]})
        assert exhausted.value.cause_code == "research_runtime_repair_budget_exhausted"
        assert [p.generation for p in host.submit_calls] == [2, 3]
        assert after["link"]["generation"] == "3" and after["link"]["state"] == "completed"
        assert len(after["prompts"]) == 3
        assert after["link"]["result_hash"] != "None"
    finally:
        await runtime.close()
