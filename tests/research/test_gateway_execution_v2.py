"""Real offline V2 preparation and controlled capability admission/receipt boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy.orm import Session

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionPrompt,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    ResearchCapabilityInvocation,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchRunCommand,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CapabilityReuseRecord,
    Database,
    DshSessionLinkRecord,
    EventWatchRecord,
    EventWindowSampleRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRoleOutputRecord,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway
from tests.dsh_native.test_execution_preparation_v2 import (
    PreparedInputs,  # pyright: ignore[reportPrivateUsage]
    _advance_inputs,  # pyright: ignore[reportPrivateUsage]
    inputs,
)
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_durable_research_gateway import (
    NOW,
    _database,  # pyright: ignore[reportPrivateUsage]
    _manifest,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


class ControlledAdapter:
    capability_id = "replay.research"
    supported_modes = frozenset({"replay"})

    def __init__(self, source: PreparedInputs):
        self.source = source
        self.calls: list[ResearchCapabilityQuery] = []
        self.after_return: Callable[[], None] | None = None

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.calls.append(query)
        cutoff = (min(self.source.request.pit_cutoff_at, self.source.now)
                  if query.mode == "live" else self.source.request.pit_cutoff_at)
        excerpt = "Actual controlled offline capability material."
        published = cutoff - timedelta(seconds=2)
        candidate = EvidenceCandidate.model_validate({
            "evidence_id": "controlled-v2-evidence:" + query.request_id,
            "requirement_id": query.requirement_id,
            "kind": "official", "authority": "official", "source_id": "controlled-official",
            "source_url": "https://example.test/original",
            "published_at": published, "observed_at": cutoff - timedelta(seconds=1),
            "received_at": cutoff,
            "content_hash": research_evidence_content_hash(
                requirement_id=query.requirement_id, kind="official", authority="official",
                source_id="controlled-official", source_url="https://example.test/original",
                published_at=published, excerpt=excerpt, structured_payload_ref=None),
            "excerpt": excerpt, "structured_payload_ref": None,
            "tool_call_id": query.request_id, "research_session_id": query.research_session_id,
            "round": query.round, "quality": "candidate", "freshness_status": "unknown",
            "conflict_group": None,
        })
        if self.after_return is not None:
            self.after_return()
        return ResearchCapabilityResult(schema_version="research-capability-result.v1",
            request_id=query.request_id, capability_id=query.capability_id,
            provider="controlled-offline", evidence_candidates=[candidate], cost_usd=0.01,
            completed_at=cutoff)


class LiveAdapter(ControlledAdapter):
    capability_id = "web.search"
    supported_modes = frozenset({"live"})


def _live_inputs(tmp_path: Path, *, watch: bool = False):
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        if watch:
            EventWatchService(database).ensure_watch(event_id=event_id,
                source_id="controlled-calendar", event_family="inflation_release",
                scheduled_at=datetime.now(UTC) + timedelta(hours=1))
        full = ResearchRequestFactory(database, pack_root=root, execution_mode="live",
            allowed_capabilities=("document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]))).build(event_id, run_id)
        request = full.model_copy(deep=True)
        request.evidence_requirements = request.evidence_requirements[:1]
        digest = research_request_hash(request)
        sid, rid = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(schema_version="dsh-session-submit.v1", run_id=run_id,
            request_hash=digest, deterministic_session_id=sid, deterministic_request_id=rid,
            workspace_ref="decision-hub://workspace/default", prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research", permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at, max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1)
        upstream = DshUpstreamIdentity(source_commit="1" * 40, source_version="0.1.0",
            package_versions={}, plugin_build_hash="2" * 64)
        with database.session() as session:
            bundle = read_research_bundle_in_session(session, run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        yield PreparedInputs(database, request, submit, upstream,
            render_prepared_research_policy(bundle, request.allowed_capabilities)
            + "\n\nControlled real live-mode preparation; no network.", datetime.now(UTC))
    finally:
        database.engine.dispose()


@pytest.fixture
def live_inputs(tmp_path: Path):
    yield from _live_inputs(tmp_path)


@pytest.fixture
def watch_inputs(tmp_path: Path):
    yield from _live_inputs(tmp_path, watch=True)


def _query(source: PreparedInputs, *, request_id: str = "controlled-call-1"):
    return ResearchCapabilityQuery.model_validate({
        "schema_version": "research-capability-query.v1", "request_id": request_id,
        "capability_id": "replay.research",
        "requirement_id": source.request.evidence_requirements[0].requirement_id,
        "query": "Controlled original policy question", "allowed_domains": ["example.test"],
        "target_url": "https://example.test/original", "symbols": [], "fields": [],
        "research_session_id": source.submit.deterministic_session_id, "round": 1,
        "mode": "replay", "observed_at": source.request.pit_cutoff_at,
        "cutoff_at": source.request.pit_cutoff_at, "max_results": 2, "max_cost_usd": 0.1,
    })


def _gateway(source: PreparedInputs, adapter: ControlledAdapter,
             observer: ResearchObservabilityService | None = None):
    manifest = _manifest().model_copy(update={
        "capability_id": adapter.capability_id, "allowed_domains": ["example.test"],
    })
    return DurableResearchCapabilityGateway(
        ResearchCapabilityGatewayService([manifest], [adapter],
            enabled_capabilities=[adapter.capability_id], clock=lambda: source.now),
        DshSessionLinkService(source.database, clock=lambda: source.now),
        ResearchEvidenceService(source.database),
        observer or ResearchObservabilityService(source.database, clock=lambda: source.now),
        requirements={r.requirement_id: r for r in source.request.evidence_requirements},
        clock=lambda: source.now,
    )


def _snapshot(database: Database, run_id: str) -> dict[str, Any]:
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        assert link is not None
        return {
            "tool_calls_started": link.tool_calls_started, "link_state": link.state,
            "reservations": [{column.name: getattr(row, column.name)
                              for column in row.__table__.columns}
                for row in session.query(ResearchToolCallReservationRecord)
                .filter_by(run_id=run_id)],
            "evidence": [row.evidence_id for row in session.query(ResearchEvidenceRecord)
                         .filter_by(run_id=run_id)],
            "reuse_count": session.query(CapabilityReuseRecord).count(),
            "facts": [row.fact_id for row in session.query(ResearchFactRecord)
                      .filter_by(run_id=run_id)],
            "traces": [{"event": row.event_type, "status": row.status,
                        "summary": row.summary, "error": row.error_code}
                       for row in session.query(ResearchTraceRecord).filter_by(run_id=run_id)],
        }


def _observe(path: Path, body: dict[str, Any]) -> None:
    (path / "observation.json").write_text(
        json.dumps(body, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


async def test_genuine_v2_gateway_preserves_actual_cost_and_exact_repeat(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    prepared = inputs.prepare()
    adapter = ControlledAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    result = await gateway.execute(_query(inputs))
    repeated = await gateway.execute(_query(inputs))
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"prepared": prepared.execution.model_dump(mode="json"),
        "result": result.model_dump(mode="json"), "repeated": repeated.model_dump(mode="json"),
        "adapter_calls": len(adapter.calls), "sql": observed})
    assert result == repeated and result.cost_usd == 0.01 and len(adapter.calls) == 1
    assert observed["tool_calls_started"] == 1
    assert observed["reservations"][0]["status"] == "completed"


@pytest.mark.parametrize("role", ["research_request_pack", "research_execution_method",
                                  "dsh_prompt_integrity:1"])
async def test_second_connection_anchor_drift_before_reservation_never_calls_adapter(
    inputs: PreparedInputs, tmp_path: Path, role: str,
) -> None:
    inputs.prepare()
    other = Database(str(inputs.database.engine.url))
    adapter = ControlledAdapter(inputs)
    mutation: dict[str, Any] = {}

    class ChangedBeforeReserve(ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs: Any):
            with other.session() as session:
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=inputs.request.run_id, role=role).one()
                mutation["old_id"] = row.output_id
                row.output_id = "changed-raw-row-id:" + role
                mutation["new_id"] = row.output_id
            return super().reserve_tool_call(**kwargs)

    gateway = _gateway(inputs, adapter,
        ChangedBeforeReserve(inputs.database, clock=lambda: inputs.now))
    error = None
    try:
        try:
            await gateway.execute(_query(inputs))
        except ResearchCapabilityError as exc:
            error = {"code": exc.error_code, "cause": exc.cause_code}
        observed = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"mutation": mutation, "error": error,
            "adapter_calls": len(adapter.calls), "sql": observed,
            "connection_urls_equal": other.engine.url == inputs.database.engine.url})
        assert mutation["old_id"] != mutation["new_id"], "actual second-connection seam not reached"
        assert len(adapter.calls) == 0, "a different raw preparation was admitted after preflight"
        assert error is not None
        assert observed["tool_calls_started"] == 0 and observed["reservations"] == []
        denials = [row for row in observed["traces"]
                   if row["event"] == "tool_failed" and row["status"] == "denied"]
        assert len(denials) == 1, "actual before-reserve refusal lost its denial audit"
        assert denials[0]["error"] == error["code"]
    finally:
        other.engine.dispose()


@pytest.mark.parametrize("role", ["research_request_pack", "research_execution_method",
                                  "dsh_prompt_integrity:1"])
async def test_actual_result_survives_inflight_anchor_drift_without_semantic_admission(
    inputs: PreparedInputs, tmp_path: Path, role: str,
) -> None:
    inputs.prepare()
    other = Database(str(inputs.database.engine.url))
    adapter = ControlledAdapter(inputs)

    def drift() -> None:
        with other.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role=role).one()
            row.output_id = "inflight-original-row-changed:" + role

    adapter.after_return = drift
    error = None
    try:
        try:
            await _gateway(inputs, adapter).execute(_query(inputs))
        except ResearchCapabilityError as exc:
            error = {"code": exc.error_code, "cause": exc.cause_code}
        observed = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"phase": "actual_result_inflight_drift", "role": role,
            "error": error, "adapter_calls": len(adapter.calls), "sql": observed})
        assert len(adapter.calls) == 1 and observed["tool_calls_started"] == 1
        assert observed["reservations"][0]["status"] == "completed"
        raw = json.loads(observed["reservations"][0]["result_json"])
        assert raw["cost_usd"] == 0.01 and len(raw["evidence_candidates"]) == 1
        assert observed["evidence"] == [] and observed["facts"] == []
        assert observed["reuse_count"] == 0 and error is not None
    finally:
        other.engine.dispose()


@pytest.mark.parametrize("refusal", ["reader_drift", "deadline"])
async def test_admission_denial_audit_storage_failure_keeps_original_refusal_unknown(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    inputs.prepare()
    adapter = ControlledAdapter(inputs)
    class DriftBeforeWriter(ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs: Any):
            if refusal == "deadline":
                gateway.clock = lambda: inputs.request.deadline_at + timedelta(milliseconds=1)
            else:
                with inputs.database.session() as session:
                    row = session.query(RunRoleOutputRecord).filter_by(
                        run_id=inputs.request.run_id, role="research_execution_method").one()
                    row.output_id = "new-unadmitted-method-raw-id"
            return super().reserve_tool_call(**kwargs)
    observer = DriftBeforeWriter(inputs.database, clock=lambda: inputs.now)
    gateway = _gateway(inputs, adapter, observer)
    attempted: list[str] = []
    def unavailable(event: ResearchTraceEvent):
        assert event.error_code is not None
        attempted.append(event.error_code)
        raise OSError("controlled_denial_audit_storage_failure")
    monkeypatch.setattr(observer, "append_trace_once", unavailable)
    with pytest.raises(ResearchCapabilityError) as rejected:
        await gateway.execute(_query(inputs))
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"original_error": rejected.value.provenance().model_dump(mode="json"),
        "audit_attempts": attempted, "audit_notes": getattr(rejected.value, "__notes__", []),
        "original_cause": type(rejected.value.__cause__).__name__,
        "audit_context": type(rejected.value.__context__).__name__,
        "adapter_calls": len(adapter.calls),
        "sql": observed})
    assert rejected.value.error_code == "research_execution_binding_invalid"
    assert rejected.value.cause_code == (
        "research_execution_deadline_elapsed" if refusal == "deadline"
        else "research_execution_receipt_invalid")
    assert attempted == [rejected.value.error_code]
    if refusal == "deadline":
        assert rejected.value.__cause__ is None
    else:
        assert isinstance(rejected.value.__cause__, ValueError)
    assert isinstance(rejected.value.__context__, OSError)
    assert "research_denial_audit_unknown:OSError" in rejected.value.__notes__
    assert observed["tool_calls_started"] == 0 and observed["reservations"] == []
    assert not observed["traces"] and not adapter.calls


async def test_semantic_savepoint_rolls_back_evidence_but_keeps_actual_success(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    adapter = ControlledAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    flushed: list[int] = []

    def fail_after_evidence(session: Session, **_kwargs: object):
        flushed.append(session.query(ResearchEvidenceRecord)
            .filter_by(run_id=inputs.request.run_id).count())
        raise ValueError("controlled_actual_fact_admission_failure")

    monkeypatch.setattr(gateway.facts, "accept_facts_in_session", fail_after_evidence)
    error = None
    try:
        await gateway.execute(_query(inputs))
    except ResearchCapabilityError as exc:
        error = {"code": exc.error_code, "cause": exc.cause_code}
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"flushed_actual_evidence_count": flushed, "error": error,
        "adapter_calls": len(adapter.calls), "sql": observed})
    assert flushed == [1] and len(adapter.calls) == 1
    assert observed["evidence"] == [] and observed["facts"] == []
    assert observed["reservations"][0]["status"] == "completed"
    assert json.loads(observed["reservations"][0]["result_json"])["cost_usd"] == 0.01
    assert error is not None


async def test_semantic_denial_exact_repeat_keeps_cost_without_returning_raw_as_admitted(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    adapter = ControlledAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    reached: list[int] = []

    def denied(session: Session, **_kwargs: object):
        reached.append(session.query(ResearchEvidenceRecord).count())
        raise ValueError("controlled_semantic_denial")

    monkeypatch.setattr(gateway.facts, "accept_facts_in_session", denied)
    errors: list[str] = []
    returned: list[dict[str, Any]] = []
    sqls: list[dict[str, Any]] = []
    for _ in range(2):
        try:
            returned.append((await gateway.execute(_query(inputs))).model_dump(mode="json"))
        except ResearchCapabilityError as exc:
            errors.append(exc.error_code)
        sqls.append(_snapshot(inputs.database, inputs.request.run_id))
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"errors": errors, "returned": returned, "reached": reached, "sqls": sqls,
        "adapter_calls": len(adapter.calls), "sql": observed})
    assert reached == [1] and len(adapter.calls) == 1
    assert observed["evidence"] == [] and observed["facts"] == []
    assert json.loads(observed["reservations"][0]["result_json"])["cost_usd"] == 0.01
    assert len(errors) == 2 and returned == [], "raw cache silently bypassed semantic denial"
    assert sqls[0] == sqls[1], "exact denied replay changed original semantic/raw/audit rows"


@pytest.mark.parametrize("method_kind", ["missing", "unknown", "unknown_sidecars"])
async def test_orphan_v2_sidecars_never_downgrade_direct_reservation(
    inputs: PreparedInputs, tmp_path: Path, method_kind: str,
) -> None:
    inputs.prepare()
    with inputs.database.session() as session:
        session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == inputs.request.run_id,
            RunRoleOutputRecord.role.in_([
                "research_request_pack", "research_task_request", "research_compiled_policy",
            ])).delete(synchronize_session=False)
        method = session.query(RunRoleOutputRecord).filter_by(
            run_id=inputs.request.run_id, role="research_execution_method").one()
        if method_kind != "unknown":
            session.delete(method)
        else:
            method.schema_version = "unknown-method.v7"
        if method_kind == "unknown_sidecars":
            for role in ("dsh_execution_request:1", "dsh_prompt_integrity:1"):
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=inputs.request.run_id, role=role).one()
                row.schema_version = "unknown-receipt.v9"
    before = _snapshot(inputs.database, inputs.request.run_id)
    outcome = ResearchObservabilityService(
        inputs.database, clock=lambda: inputs.now).reserve_tool_call(
        run_id=inputs.request.run_id, request_id="orphan-direct-call",
        research_session_id=inputs.submit.deterministic_session_id, generation=1,
        capability_id="replay.research", query_hash="a" * 64,
    )
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"method_kind": method_kind, "before": before, "after": after,
        "outcome": outcome.outcome, "error": outcome.error.model_dump(mode="json")
            if outcome.error is not None else None})
    assert outcome.outcome == "denied"
    assert after == before


def test_genuine_v1_prompt_prefix_does_not_upgrade_reservation(tmp_path: Path) -> None:
    database, run_id, sid = _database(tmp_path)
    try:
        _, rid = DshSessionLinkService.deterministic_ids(run_id, "a" * 64)
        DshSessionLinkService(database, clock=lambda: NOW).store_prompt(DshSessionPrompt(
            schema_version="dsh-session-prompt.v1", run_id=run_id, dsh_session_id=sid,
            request_id=rid, request_hash="a" * 64, generation=1, prompt="Original legacy prompt."))
        with database.session() as session:
            integrity = session.query(RunRoleOutputRecord).filter_by(
                run_id=run_id, role="dsh_prompt_integrity:1").one()
            schema = integrity.schema_version
        result = ResearchObservabilityService(database, clock=lambda: NOW).reserve_tool_call(
            run_id=run_id, request_id="legacy-direct", research_session_id=sid, generation=1,
            capability_id="official.macro", query_hash="b" * 64)
        _observe(tmp_path, {"actual_legacy_integrity_schema": schema,
            "outcome": result.outcome, "sql": _snapshot(database, run_id)})
        assert schema == "dsh-prompt-integrity.v1" and result.outcome == "reserved"
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("old_status", ["completed", "running"])
async def test_old_query_only_v2_reservation_never_reexecutes_or_backfills(
    inputs: PreparedInputs, tmp_path: Path, old_status: str,
) -> None:
    inputs.prepare()
    query = _query(inputs)
    adapter = ControlledAdapter(inputs)
    old_result = await adapter.execute(query)
    adapter.calls.clear()
    with inputs.database.session() as session:
        session.add(ResearchToolCallReservationRecord(run_id=inputs.request.run_id,
            request_id=query.request_id, research_session_id=query.research_session_id,
            generation=1, capability_id=query.capability_id, status=old_status,
            query_hash=hashlib.sha256(json.dumps(query.model_dump(mode="json"),
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            result_json=old_result.model_dump_json() if old_status == "completed" else None,
            error_json=None, reserved_at=inputs.now,
            completed_at=inputs.now if old_status == "completed" else None))
        link = session.get(DshSessionLinkRecord, inputs.request.run_id)
        assert link is not None
        link.tool_calls_started = 1
    before = _snapshot(inputs.database, inputs.request.run_id)
    with pytest.raises(ResearchCapabilityError) as rejected:
        await _gateway(inputs, adapter).execute(query)
    after = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"before": before, "after": after,
        "error": rejected.value.error_code, "adapter_calls": len(adapter.calls)})
    assert rejected.value.error_code == "research_tool_request_conflict"
    assert after["reservations"] == before["reservations"] and not adapter.calls
    assert after["tool_calls_started"] == 1 and after["evidence"] == []


@pytest.mark.parametrize("changed_query", [False, True])
def test_two_connections_same_identity_reserve_only_one_actual_adapter(
    inputs: PreparedInputs, tmp_path: Path, changed_query: bool,
) -> None:
    inputs.prepare()
    other = Database(str(inputs.database.engine.url))
    barrier = Barrier(2)
    adapters = [ControlledAdapter(inputs), ControlledAdapter(inputs)]

    class RacingObserver(ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs: Any):
            barrier.wait(timeout=15)
            return super().reserve_tool_call(**kwargs)

    def invoke(index: int):
        db = inputs.database if index == 0 else other
        source = replace(inputs, database=db)
        query = _query(source)
        if index and changed_query:
            query = query.model_copy(update={"query": "Different original intent"})
        try:
            result = asyncio.run(_gateway(source, adapters[index], RacingObserver(
                db, clock=lambda: inputs.now)).execute(query))
            return {"result": result.model_dump(mode="json")}
        except ResearchCapabilityError as exc:
            return {"error": exc.error_code}

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(invoke, [0, 1]))
        observed = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"two_database_connections": True, "outcomes": outcomes,
            "adapter_calls": [len(adapter.calls) for adapter in adapters], "sql": observed})
        assert sum(len(adapter.calls) for adapter in adapters) == 1
        assert observed["tool_calls_started"] == 1 and len(observed["reservations"]) == 1
        if changed_query:
            assert any(outcome.get("error") == "research_tool_request_conflict"
                       for outcome in outcomes)
    finally:
        other.engine.dispose()


def _next(inputs: PreparedInputs) -> PreparedInputs:
    advanced = _advance_inputs(inputs)
    request = advanced.request.model_copy(deep=True)
    request.allowed_capabilities = ["replay.research"]
    request.execution_budget.max_tool_calls = 1
    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, request.run_id)
    assert isinstance(bundle, FrozenResearchBundle)
    return replace(advanced, request=request, prompt=render_prepared_research_policy(
        bundle, request.allowed_capabilities) + "\n\nControlled next actual generation.")


@pytest.mark.parametrize("disposition", ["completed", "failed", "cancelled"])
async def test_authenticated_generation_budget_counts_every_committed_reservation(
    inputs: PreparedInputs, tmp_path: Path, disposition: str,
) -> None:
    advanced = _next(inputs)
    current = advanced.prepare()
    adapter = ControlledAdapter(advanced)
    if disposition != "completed":
        def stopped():
            if disposition == "cancelled":
                raise asyncio.CancelledError()
            raise ValueError("controlled_actual_adapter_failure")
        adapter.after_return = stopped
    gateway = _gateway(advanced, adapter)
    try:
        await gateway.execute(_query(advanced, request_id="generation-slot-one"))
    except (ResearchCapabilityError, asyncio.CancelledError):
        pass
    with pytest.raises(ResearchCapabilityError) as exhausted:
        await gateway.execute(_query(advanced, request_id="generation-slot-two"))
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"current_budget": current.execution.request.execution_budget.max_tool_calls,
        "global_ceiling": current.link.max_tool_calls, "sql": observed,
        "adapter_calls": len(adapter.calls), "error": exhausted.value.error_code})
    assert current.link.max_tool_calls is not None
    assert current.link.max_tool_calls > 1 and len(adapter.calls) == 1
    assert observed["reservations"][0]["status"] == disposition
    assert exhausted.value.error_code == "research_tool_budget_exhausted"


def _intent(source: PreparedInputs) -> ResearchCapabilityInvocation:
    body = _query(source).model_dump(exclude={"schema_version", "round", "mode", "observed_at",
        "requested_observed_at", "cutoff_at", "deadline_at"})
    return ResearchCapabilityInvocation.model_validate({**body,
        "schema_version": "research-capability-invocation.v1", "capability_id": "web.search"})


async def test_live_timing_and_reservation_are_one_original_writer_and_exact_repeat(
    live_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    live_inputs.prepare()
    adapter = LiveAdapter(live_inputs)
    gateway = _gateway(live_inputs, adapter)
    first = await gateway.invoke_live(_intent(live_inputs))
    gateway.clock = lambda: live_inputs.now + timedelta(seconds=1)
    repeat = await gateway.invoke_live(_intent(live_inputs))
    with live_inputs.database.session() as session:
        timings = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.role.startswith("research_tool_time:")).all()
        assert len(timings) == 1
        timing = {"body": json.loads(timings[0].payload_json),
                  "created_at": str(timings[0].created_at)}
    _observe(tmp_path, {"result": first.model_dump(mode="json"), "timing": timing,
        "adapter_calls": len(adapter.calls),
        "sql": _snapshot(live_inputs.database, live_inputs.request.run_id)})
    assert first == repeat and first.cost_usd == 0.01 and len(adapter.calls) == 1


async def test_live_timing_and_reservation_flush_fault_roll_back_together(
    live_inputs: PreparedInputs, tmp_path: Path,
) -> None:
    live_inputs.prepare()
    reached: list[dict[str, int]] = []

    class FaultingObserver(ResearchObservabilityService):
        def reserve_tool_call_in_session(self, session: Session, **kwargs: Any):
            super().reserve_tool_call_in_session(session, **kwargs)
            reached.append({"timing": session.query(RunRoleOutputRecord).filter(
                RunRoleOutputRecord.role.startswith("research_tool_time:")).count(),
                "reservation": session.query(ResearchToolCallReservationRecord).count()})
            raise ValueError("controlled_after_actual_reservation_and_timing_flush")

    adapter = LiveAdapter(live_inputs)
    with pytest.raises(ResearchCapabilityError):
        await _gateway(live_inputs, adapter, FaultingObserver(live_inputs.database,
            clock=lambda: live_inputs.now)).invoke_live(_intent(live_inputs))
    with live_inputs.database.session() as session:
        remaining = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.role.startswith("research_tool_time:")).count()
    observed = _snapshot(live_inputs.database, live_inputs.request.run_id)
    _observe(tmp_path, {"reached_after_flush": reached, "remaining_timing": remaining,
        "adapter_calls": len(adapter.calls), "sql": observed})
    assert reached == [{"timing": 1, "reservation": 1}] and remaining == 0
    assert observed["tool_calls_started"] == 0 and observed["reservations"] == []
    assert not adapter.calls


@pytest.mark.parametrize("mutation", ["timing_identity", "deadline"])
async def test_live_final_timing_or_deadline_change_keeps_raw_cost_but_denies_semantics(
    live_inputs: PreparedInputs, tmp_path: Path, mutation: str,
) -> None:
    live_inputs.prepare()
    adapter = LiveAdapter(live_inputs)
    gateway = _gateway(live_inputs, adapter)
    def drift():
        if mutation == "deadline":
            gateway.clock = lambda: live_inputs.request.deadline_at + timedelta(milliseconds=1)
        else:
            with live_inputs.database.session() as session:
                row = session.query(RunRoleOutputRecord).filter(
                    RunRoleOutputRecord.role.startswith("research_tool_time:")).one()
                row.output_id = "other-physical-timing-row"
    adapter.after_return = drift
    with pytest.raises(ResearchCapabilityError):
        await gateway.invoke_live(_intent(live_inputs))
    observed = _snapshot(live_inputs.database, live_inputs.request.run_id)
    _observe(tmp_path, {"mutation": mutation, "sql": observed, "adapter_calls": len(adapter.calls)})
    assert len(adapter.calls) == 1 and observed["evidence"] == [] and observed["facts"] == []
    assert observed["reservations"][0]["status"] == "completed"
    assert json.loads(observed["reservations"][0]["result_json"])["cost_usd"] == 0.01


@pytest.mark.parametrize("drift", [False, True])
async def test_original_v2_watch_window_never_uses_today_watch(
    watch_inputs: PreparedInputs, tmp_path: Path, drift: bool,
) -> None:
    prepared = watch_inputs.prepare()
    original = prepared.execution.request
    assert original.event_watch is not None and original.event_window_samples
    old_slot = next(item for item in original.event_window_samples if item.offset == "t0")
    if drift:
        with watch_inputs.database.session() as session:
            row = session.query(EventWatchRecord).one()
            row.scheduled_at += timedelta(hours=1)
            for slot in session.query(EventWindowSampleRecord).all():
                slot.target_at += timedelta(hours=1)
    adapter = LiveAdapter(watch_inputs)
    gateway = _gateway(watch_inputs, adapter)
    gateway.event_watches = EventWatchService(watch_inputs.database)
    result = await gateway.invoke_live(_intent(watch_inputs).model_copy(update={
        "event_id": original.event_id, "requested_event_offsets": ["t0"]}))
    actual = adapter.calls[0]
    _observe(tmp_path, {"current_watch_drift": drift,
        "original_watch": original.event_watch.model_dump(mode="json"),
        "original_slot": old_slot.model_dump(mode="json"), "query": actual.model_dump(mode="json"),
        "cost": result.cost_usd, "adapter_calls": len(adapter.calls),
        "sql": _snapshot(watch_inputs.database, original.run_id)})
    assert actual.event_at == original.event_watch.scheduled_at
    assert actual.window_start_at == actual.window_end_at == old_slot.target_at
    assert len(adapter.calls) == 1 and result.cost_usd == 0.01


@pytest.mark.parametrize("anchor_drift", [False, True])
async def test_admitted_v2_inflight_cancel_keeps_actual_cost_and_original_cutoff(
    inputs: PreparedInputs, tmp_path: Path, anchor_drift: bool,
) -> None:
    inputs.prepare()
    other = Database(str(inputs.database.engine.url))
    adapter = ControlledAdapter(inputs)
    cutoff = inputs.now + timedelta(seconds=1)
    command = ResearchRunCommand(schema_version="research-run-command.v1",
        request_id="original-controlled-owner-cancel", command="cancel", reason="Controlled stop")
    receipt: list[dict[str, Any]] = []
    def cancel():
        receipt.append(ResearchCommandService(other, clock=lambda: cutoff).apply(
            inputs.request.run_id, command).model_dump(mode="json"))
        if anchor_drift:
            with other.session() as session:
                row = session.query(RunRoleOutputRecord).filter_by(run_id=inputs.request.run_id,
                    role="research_execution_method").one()
                row.output_id = "other-current-method"
    adapter.after_return = cancel
    error = None
    try:
        try:
            await _gateway(inputs, adapter).execute(_query(inputs))
        except ResearchCapabilityError as exc:
            error = exc.error_code
        observed = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"anchor_drift": anchor_drift, "cancel_receipt": receipt,
            "original_cancel_at": cutoff.isoformat(), "error": error,
            "adapter_calls": len(adapter.calls), "sql": observed})
        assert receipt[0]["status"] == "accepted" and len(adapter.calls) == 1
        assert observed["reservations"][0]["status"] == "completed"
        assert json.loads(observed["reservations"][0]["result_json"])["cost_usd"] == 0.01
        assert observed["reuse_count"] == 0
        if anchor_drift:
            assert observed["evidence"] == [] and observed["facts"] == [] and error
            assert not any(row["event"] == "evidence_accepted" for row in observed["traces"])
        else:
            assert len(observed["evidence"]) == 1 and error is None
        audit = next(row for row in observed["traces"] if command.request_id in row["summary"])
        assert cutoff.isoformat() in audit["summary"]
    finally:
        other.engine.dispose()


async def test_cancel_committed_between_preflight_and_reserve_never_calls_v2_adapter(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    other = Database(str(inputs.database.engine.url))
    adapter = ControlledAdapter(inputs)
    admitted: list[str] = []
    class CancelFirst(ResearchObservabilityService):
        def reserve_tool_call(self, **kwargs: Any):
            admitted.append(ResearchCommandService(other, clock=lambda: inputs.now).apply(
                inputs.request.run_id, ResearchRunCommand(schema_version="research-run-command.v1",
                    request_id="cancel-first-writer", command="cancel", reason="Controlled stop")
                ).status)
            return super().reserve_tool_call(**kwargs)
    try:
        with pytest.raises(ResearchCapabilityError) as rejected:
            await _gateway(inputs, adapter, CancelFirst(inputs.database,
                clock=lambda: inputs.now)).execute(_query(inputs))
        observed = _snapshot(inputs.database, inputs.request.run_id)
        _observe(tmp_path, {"cancel_first_result": admitted, "error": rejected.value.error_code,
            "adapter_calls": len(adapter.calls), "sql": observed})
        assert admitted == ["accepted"] and not adapter.calls
        assert observed["tool_calls_started"] == 0 and observed["reservations"] == []
    finally:
        other.engine.dispose()


async def test_valid_generation_advance_during_actual_call_never_relabels_old_output(
    inputs: PreparedInputs, tmp_path: Path,
) -> None:
    inputs.prepare()
    adapter = ControlledAdapter(inputs)
    actual_next: list[int] = []
    def advance():
        advanced = _next(inputs)
        actual_next.append(advanced.prepare().link.generation)
    adapter.after_return = advance
    with pytest.raises(ResearchCapabilityError):
        await _gateway(inputs, adapter).execute(_query(inputs))
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"actual_next_generation": actual_next,
        "adapter_calls": len(adapter.calls), "sql": observed})
    assert actual_next == [2] and len(adapter.calls) == 1
    assert observed["reservations"][0]["generation"] == 1
    assert observed["reservations"][0]["status"] == "completed"
    assert json.loads(observed["reservations"][0]["result_json"])["cost_usd"] == 0.01
    assert observed["evidence"] == [] and observed["facts"] == [] and observed["reuse_count"] == 0


async def test_actual_success_with_outer_storage_failure_stays_unknown_and_never_reexecutes(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs.prepare()
    adapter = ControlledAdapter(inputs)
    gateway = _gateway(inputs, adapter)
    reached: list[int] = []
    def unavailable(session: Session, **kwargs: object):
        reached.append(session.query(ResearchEvidenceRecord).count())
        raise OSError("controlled_actual_completion_storage_failure")
    monkeypatch.setattr(gateway.observability, "complete_tool_call_in_session", unavailable)
    errors: list[str] = []
    for _ in range(2):
        with pytest.raises(ResearchCapabilityError) as rejected:
            await gateway.execute(_query(inputs))
        errors.append(rejected.value.error_code)
    observed = _snapshot(inputs.database, inputs.request.run_id)
    _observe(tmp_path, {"after_real_evidence_flush": reached, "errors": errors,
        "actual_adapter_cost": 0.01, "adapter_calls": len(adapter.calls), "sql": observed})
    assert reached == [1] and len(adapter.calls) == 1
    assert observed["reservations"][0]["status"] == "running"
    assert observed["reservations"][0]["result_json"] is None
    assert observed["evidence"] == [] and observed["facts"] == []
    assert errors[-1] == "research_tool_call_in_progress"
