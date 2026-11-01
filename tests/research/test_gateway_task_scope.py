from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Generator, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import AnyUrl
from sqlalchemy.orm import Session

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionPrompt,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
    ObservationCreate,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchRunCommand,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import research_fact_payload_hash
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    canonical_recipe,
    method_definition,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import (
    ROLE as TASK_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_task import (
    ResearchTaskService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    CapabilityReuseRecord,
    Database,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    RunRoleOutputRecord,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway

PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
CAPABILITY = "market.cross_asset"


@pytest.fixture
def database(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    db.create_all()
    try:
        yield db
    finally:
        db.engine.dispose()


class RecordingEvidenceService(ResearchEvidenceService):
    """Observe the requirements handed to real SQLite admission, then delegate."""

    admitted_requirements: dict[str, EvidenceRequirement] | None = None

    def accept_candidates_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        capability_id: str,
        candidates: Iterable[EvidenceCandidate],
        requirements: Mapping[str, EvidenceRequirement],
        cutoff_at: datetime,
        facts: Iterable[FactEnvelope] = (),
    ) -> list[EvidenceCandidate]:
        self.admitted_requirements = dict(requirements)
        return super().accept_candidates_in_session(
            session,
            run_id=run_id,
            capability_id=capability_id,
            candidates=candidates,
            requirements=requirements,
            cutoff_at=cutoff_at,
            facts=facts,
        )


class ControlledCrossAssetAdapter:
    capability_id = CAPABILITY
    supported_modes = frozenset({"live"})

    def __init__(self, *, published_at: datetime = NOW - timedelta(seconds=1)) -> None:
        self.published_at = published_at
        self.calls: list[ResearchCapabilityQuery] = []

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.calls.append(query)
        excerpt = "Controlled archived source observation; no model or network call."
        source_url = "https://fred.stlouisfed.org/series/DGS10"
        candidate = EvidenceCandidate(
            evidence_id=f"evidence:{query.request_id}",
            requirement_id=query.requirement_id,
            kind="market",
            authority="audited_aggregator",
            source_id="controlled-cross-asset",
            source_url=AnyUrl(source_url),
            published_at=self.published_at,
            observed_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(seconds=1),
            content_hash=research_evidence_content_hash(
                requirement_id=query.requirement_id,
                kind="market",
                authority="audited_aggregator",
                source_id="controlled-cross-asset",
                source_url=source_url,
                published_at=self.published_at,
                excerpt=excerpt,
                structured_payload_ref=None,
            ),
            excerpt=excerpt,
            structured_payload_ref=None,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="controlled-local-adapter",
            evidence_candidates=[candidate],
            cost_usd=0.01,
            completed_at=NOW + timedelta(seconds=2),
        )


class BlockingCrossAssetAdapter(ControlledCrossAssetAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.entered.set()
        await self.release.wait()
        return await super().execute(query)


class BlockingFactCrossAssetAdapter(BlockingCrossAssetAdapter):
    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        result = await super().execute(query)
        candidate = result.evidence_candidates[0]
        fact = FactEnvelope(
            schema_version="fact-envelope.v1", fact_id="fact:scope-cancel",
            evidence_id=candidate.evidence_id, requirement_id=candidate.requirement_id,
            metric_family="macro.cross_asset", field="level", instrument=None, venue=None,
            value=4.25, unit="percent", window_start_at=None, window_end_at=None,
            event_offset=None, observed_at=candidate.observed_at, received_at=candidate.received_at,
            published_at=candidate.published_at, source_id=candidate.source_id,
            independence_group=candidate.source_id, quality="candidate", delay_class="realtime",
            payload_schema_ref="test.fact.v1", payload_hash="f" * 64, attributes={},
        )
        fact = fact.model_copy(update={"payload_hash": research_fact_payload_hash(
            **fact.model_dump(exclude={
                "schema_version", "fact_id", "evidence_id", "observed_at", "received_at",
                "quality", "payload_hash",
            }),
        )})
        return result.model_copy(update={"facts": [fact]})


def prepare_task(
    db: Database,
    *,
    watch_family: str | None = None,
    historical: bool = False,
) -> ResearchSessionRequest:
    text = (
        "历史事件复盘：请核验原始事件材料。"
        if historical
        else "Review the official event and its macro transmission."
    )
    event, _, admitted = AdmissionService(db, clock=lambda: NOW).admit(
        ObservationCreate(
            text=text,
            source_id="task-scope-input",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2),
            published_at=NOW - timedelta(seconds=3),
            language="zh" if historical else "en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted and db.get_event_type(event) == "central_bank_speech"
    run, created = RunService(db, clock=lambda: NOW).create(event, strategy_version="research.v1")
    assert created
    if watch_family:
        EventWatchService(db, clock=lambda: NOW).ensure_watch(
            event_id=event,
            source_id="task-scope-calendar",
            event_family=watch_family,
            scheduled_at=NOW - timedelta(days=1),
            window_offsets=("t-5m", "t+1m"),
        )
    factory = ResearchRequestFactory(
        db,
        pack_root=PACK_ROOT,
        allowed_capabilities=(CAPABILITY,),
        execution_mode="live",
        clock=lambda: NOW,
    )
    request = factory.build(event, run)
    assert ResearchTaskService(db, clock=lambda: NOW).freeze(request) == request
    assert ResearchTaskService(db).get(run) == request
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(run, digest, 1)
    identity = DshUpstreamIdentity(
        source_commit="c" * 40,
        source_version="control.1",
        package_versions={"dsh": "control.1"},
        plugin_build_hash="b" * 64,
    )
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/control",
        prompt_ref=f"hub://runs/{run}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        model_step_timeout_ms=150_000,
        max_tool_calls=request.execution_budget.max_tool_calls,
        generation=1,
    )
    definition = method_definition(
        request,
        submit,
        identity,
        runtime_id="dsh",
        runtime_version="control.1",
        profile_ref=request.role_profile_ref,
        prompt_policy_hash="d" * 64,
    )
    pack = factory.packs.get(run)
    assert pack is not None
    definition["request_pack_hash"] = pack["definition_hash"]
    ResearchMethodService(db, clock=lambda: NOW).freeze(run, definition, request_hash=digest)
    links = DshSessionLinkService(db, clock=lambda: NOW)
    links.reserve(submit, identity)
    links.store_prompt(
        DshSessionPrompt(
            schema_version="dsh-session-prompt.v1",
            run_id=run,
            dsh_session_id=session_id,
            request_id=request_id,
            request_hash=digest,
            generation=1,
            prompt="Control fixture: immutable Task and execution receipt; no Host submit.",
        ),
        execution_request=request,
    )
    return request


def gateway_and_query(
    db: Database,
    task: ResearchSessionRequest,
    adapter: ControlledCrossAssetAdapter,
    *,
    requirement_id: str = "macro_transmission",
) -> tuple[DurableResearchCapabilityGateway, RecordingEvidenceService, ResearchCapabilityQuery]:
    bindings = yaml.safe_load((PACK_ROOT / "tools/bindings.yaml").read_text(encoding="utf-8"))
    manifest = ResearchCapabilityManifest.model_validate(
        next(item for item in bindings["capabilities"] if item["capability_id"] == CAPABILITY)
    )

    def clock() -> datetime:
        return NOW + timedelta(seconds=3)

    inner = ResearchCapabilityGatewayService(
        [manifest],
        [adapter],
        enabled_capabilities=(CAPABILITY,),
        clock=clock,
    )
    evidence = RecordingEvidenceService(db)
    gateway = DurableResearchCapabilityGateway(
        inner,
        DshSessionLinkService(db, clock=clock),
        evidence,
        ResearchObservabilityService(db, clock=clock),
        requirements={},
        clock=clock,
    )
    link = DshSessionLinkService(db).get(task.run_id)
    assert link is not None
    query = ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="scope-call",
        capability_id=CAPABILITY,
        requirement_id=requirement_id,
        query="Read the controlled source observation.",
        target_url=AnyUrl("https://fred.stlouisfed.org/series/DGS10"),
        symbols=[],
        fields=["level", "change_5d"],
        allowed_domains=["fred.stlouisfed.org"],
        max_results=1,
        max_cost_usd=1,
        research_session_id=link.dsh_session_id,
        round=1,
        mode="live",
        observed_at=NOW,
        cutoff_at=task.pit_cutoff_at,
        deadline_at=task.deadline_at,
    )
    return gateway, evidence, query


def record_observation(
    label: str,
    adapter: ControlledCrossAssetAdapter,
    evidence: RecordingEvidenceService,
    *,
    result: ResearchCapabilityResult | None = None,
    error: ResearchCapabilityError | None = None,
) -> None:
    print(
        "TASK_SCOPE_OBSERVATION "
        + json.dumps(
            {
                "case": label,
                "adapter_calls": len(adapter.calls),
                "error_code": error.error_code if error else None,
                "admission_requirements": {
                    key: {
                        "importance": value.importance,
                        "freshness_seconds": value.freshness_seconds,
                    }
                    for key, value in (evidence.admitted_requirements or {}).items()
                },
                "result_quality": [item.quality for item in result.evidence_candidates]
                if result
                else None,
                "result_cost_usd": result.cost_usd if result else None,
            },
            sort_keys=True,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", ["event_watch", "historical_review", "ordinary_event"])
async def test_real_factory_task_is_the_gateway_admission_basis(database: Database, basis: str):
    task = prepare_task(
        database,
        watch_family="geopolitical_shock" if basis == "event_watch" else None,
        historical=basis == "historical_review",
    )
    expected = {item.requirement_id: item for item in task.evidence_requirements}
    if basis == "event_watch":
        assert (
            task.event_watch is not None and task.event_watch.event_family == "geopolitical_shock"
        )
        assert expected["macro_transmission"].importance == "soft"
    elif basis == "historical_review":
        assert task.research_mode == "historical_review"
        assert not {
            "expectation_pricing",
            "crypto_spot_confirmation",
            "derivatives_crowding",
        } & set(expected)
    adapter = ControlledCrossAssetAdapter()
    gateway, evidence, query = gateway_and_query(database, task, adapter)
    result = await gateway.execute(query)
    record_observation(basis, adapter, evidence, result=result)
    assert len(adapter.calls) == 1 and result.cost_usd == 0.01
    assert evidence.admitted_requirements == expected
    assert ResearchTaskService(database).get(task.run_id) == task


@pytest.mark.asyncio
async def test_snapshot_task_retains_completed_daily_freshness(database: Database):
    task = prepare_task(database, watch_family="crypto_market_snapshot")
    expected = {item.requirement_id: item for item in task.evidence_requirements}
    assert expected["macro_transmission"].freshness_seconds == 259200
    assert expected["macro_transmission"].required_event_offsets == []
    adapter = ControlledCrossAssetAdapter(published_at=NOW - timedelta(days=2))
    gateway, evidence, query = gateway_and_query(database, task, adapter)
    result = await gateway.execute(query)
    record_observation("current_snapshot_freshness", adapter, evidence, result=result)
    assert len(adapter.calls) == 1 and result.cost_usd == 0.01
    assert result.evidence_candidates[0].quality == "accepted"
    assert result.evidence_candidates[0].freshness_status == "fresh"
    assert evidence.admitted_requirements == expected


@pytest.mark.asyncio
async def test_requirement_removed_from_historical_task_does_not_start_adapter(database: Database):
    task = prepare_task(database, historical=True)
    assert "expectation_pricing" not in {item.requirement_id for item in task.evidence_requirements}
    adapter = ControlledCrossAssetAdapter()
    gateway, evidence, query = gateway_and_query(
        database,
        task,
        adapter,
        requirement_id="expectation_pricing",
    )
    error = None
    result = None
    try:
        result = await gateway.execute(query)
    except ResearchCapabilityError as caught:
        error = caught
    record_observation(
        "removed_historical_requirement", adapter, evidence, result=result, error=error
    )
    assert error is not None
    assert len(adapter.calls) == 0
    assert ResearchEvidenceService(database).list_run_evidence(task.run_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["payload_hash", "resigned_wrong_run"])
async def test_corrupt_present_task_cannot_fallback_to_current_event(
    database: Database,
    damage: str,
):
    task = prepare_task(database)
    with database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(run_id=task.run_id, role=TASK_ROLE).one()
        if damage == "payload_hash":
            row.payload_hash = "f" * 64
        else:
            body = json.loads(row.payload_json)
            body["request"]["run_id"] = "other-run"
            body["request_hash"] = hashlib.sha256(
                canonical_recipe(body["request"]).encode()
            ).hexdigest()
            row.payload_json = canonical_recipe(body)
            row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with pytest.raises(ValueError, match="research_task_request_invalid"):
        ResearchTaskService(database).get(task.run_id)
    adapter = ControlledCrossAssetAdapter()
    gateway, evidence, query = gateway_and_query(database, task, adapter)
    error = None
    result = None
    try:
        result = await gateway.execute(query)
    except ResearchCapabilityError as caught:
        error = caught
    record_observation(f"corrupt_task_{damage}", adapter, evidence, result=result, error=error)
    assert error is not None
    assert len(adapter.calls) == 0
    assert ResearchEvidenceService(database).list_run_evidence(task.run_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["changed", "missing", "corrupt"])
async def test_task_changed_during_adapter_retains_actual_result_and_cost(
    database: Database, drift: str,
):
    task = prepare_task(database)
    adapter = BlockingCrossAssetAdapter()
    gateway, evidence, query = gateway_and_query(database, task, adapter)
    running = asyncio.create_task(gateway.execute(query))
    await adapter.entered.wait()
    try:
        with database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=task.run_id, role=TASK_ROLE,
            ).one()
            if drift == "missing":
                session.delete(row)
            elif drift == "corrupt":
                row.payload_hash = "f" * 64
            else:
                body = json.loads(row.payload_json)
                requirement = next(
                    value for value in body["request"]["evidence_requirements"]
                    if value["requirement_id"] == query.requirement_id
                )
                requirement["freshness_seconds"] += 1
                body["request_hash"] = hashlib.sha256(
                    canonical_recipe(body["request"]).encode()
                ).hexdigest()
                row.payload_json = canonical_recipe(body)
                row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    finally:
        adapter.release.set()
    error = None
    result = None
    try:
        result = await running
    except ResearchCapabilityError as caught:
        error = caught
    record_observation(f"postcall_task_{drift}", adapter, evidence, result=result, error=error)
    assert error is not None
    assert len(adapter.calls) == 1
    assert evidence.admitted_requirements is None
    assert ResearchEvidenceService(database).list_run_evidence(task.run_id) == []
    with database.session() as session:
        reservation = session.get(
            ResearchToolCallReservationRecord, (task.run_id, query.request_id),
        )
        assert reservation is not None and reservation.status == "completed"
        assert reservation.error_json is None and reservation.result_json is not None
        actual = ResearchCapabilityResult.model_validate_json(reservation.result_json)
        assert actual.cost_usd == 0.01
        assert actual.evidence_candidates[0].requirement_id == query.requirement_id
        assert actual.evidence_candidates[0].quality == "candidate"
        print("TASK_SCOPE_DURABLE " + json.dumps({
            "case": f"postcall_task_{drift}", "adapter_calls": len(adapter.calls),
            "reservation_status": reservation.status, "actual_result_cost_usd": actual.cost_usd,
            "actual_candidate_quality": actual.evidence_candidates[0].quality,
            "task_scope_error_code": error.error_code,
        }, sort_keys=True))
    traces = ResearchObservabilityService(database).list_trace(task.run_id)
    completed = [trace for trace in traces if trace.event_type == "tool_completed"]
    assert len(completed) == 1 and completed[0].status == "succeeded"
    drift_audit = [trace for trace in traces if trace.reference_type == "task_scope"]
    assert len(drift_audit) == 1 and drift_audit[0].status == "denied"
    assert drift_audit[0].error_code == error.error_code
    assert not any(trace.event_type == "tool_failed" for trace in traces)
    assert ResearchObservabilityService(database).tool_calls_started(task.run_id) == 1


@pytest.mark.asyncio
async def test_scope_drift_with_original_cancel_never_audits_raw_material_as_accepted(
    database: Database, tmp_path: Path,
):
    task = prepare_task(database)
    adapter = BlockingFactCrossAssetAdapter()
    gateway, evidence, query = gateway_and_query(database, task, adapter)
    cancellation_database = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    cancel_at = NOW + timedelta(seconds=1)
    command = ResearchRunCommand(
        schema_version="research-run-command.v1", request_id="scope-original-cancel",
        command="cancel", reason="Owner cancelled the controlled in-flight call.",
    )
    running = asyncio.create_task(gateway.execute(query))
    try:
        await asyncio.wait_for(adapter.entered.wait(), timeout=3)
        cancellation = ResearchCommandService(cancellation_database, clock=lambda: cancel_at)
        assert cancellation.apply(task.run_id, command).status == "accepted"
        with cancellation_database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=task.run_id, role=TASK_ROLE,
            ).one()
            row.payload_hash = "f" * 64
        adapter.release.set()
        with pytest.raises(ResearchCapabilityError) as rejected:
            await running
        assert rejected.value.error_code == "research_task_request_invalid"
        assert len(adapter.calls) == 1
        assert evidence.admitted_requirements is None
        assert ResearchEvidenceService(database).list_run_evidence(task.run_id) == []
        with database.session() as session:
            assert session.query(ResearchFactRecord).count() == 0
            assert session.query(CapabilityReuseRecord).count() == 0
            reservation = session.get(
                ResearchToolCallReservationRecord, (task.run_id, query.request_id),
            )
            assert reservation is not None and reservation.status == "completed"
            assert reservation.result_json is not None and reservation.error_json is None
            actual = ResearchCapabilityResult.model_validate_json(reservation.result_json)
            assert actual.cost_usd == 0.01
            assert actual.evidence_candidates[0].quality == "candidate"
            assert actual.facts is not None and len(actual.facts) == 1
            assert actual.facts[0].quality == "candidate" and actual.facts[0].value == 4.25
        traces = ResearchObservabilityService(database).list_trace(task.run_id)
        print("TASK_SCOPE_CANCEL_DURABLE " + json.dumps({
            "adapter_calls": len(adapter.calls), "actual_result_cost_usd": actual.cost_usd,
            "actual_raw_facts": len(actual.facts), "reservation_status": reservation.status,
            "trace_events": [{"event_type": item.event_type, "status": item.status,
                              "reference_type": item.reference_type} for item in traces],
        }, sort_keys=True))
        assert not any(item.event_type == "evidence_accepted" for item in traces)
        completed = [item for item in traces if item.event_type == "tool_completed"]
        assert len(completed) == 1 and completed[0].status == "succeeded"
        assert completed[0].error is None
        audit = [item for item in traces if item.reference_type == "task_scope"]
        assert len(audit) == 1 and audit[0].status == "denied"
        assert command.request_id in audit[0].summary
        assert cancel_at.isoformat() in audit[0].summary
        assert "no new evidence/fact admission or reuse" in audit[0].summary
        assert not any(item.event_type == "tool_failed" for item in traces)
        assert ResearchObservabilityService(database).tool_calls_started(task.run_id) == 1
    finally:
        adapter.release.set()
        await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), timeout=3)
        cancellation_database.engine.dispose()
