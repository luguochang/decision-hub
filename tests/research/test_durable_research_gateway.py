from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionCompletion,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
    ObservationCreate,
    ProviderAttempt,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchRunCommand,
    ResearchTraceEvent,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import (
    ResearchFactStore,
    research_fact_payload_hash,
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
    ToolCallReservation,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    CapabilityReuseRecord,
    Database,
    RawCaptureRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
)
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE, RawBodyArchive
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway

NOW = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)


def _database(
    tmp_path: Path,
    *,
    max_tool_calls: int = 12,
    event_hint: str | None = None,
    pack_definition: dict | None = None,
) -> tuple[Database, str, str]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    database.create_all()
    event_id = "event-durable"
    if event_hint is not None:
        event_id, _envelope, admitted = AdmissionService(
            database, clock=lambda: NOW
        ).admit(
            ObservationCreate(
                text="BTC current market snapshot",
                source_id="durable-gateway-test",
                source_type=SourceType.manual,
                event_hint=event_hint,
                language="en",
            )
        )
        assert admitted
    run_id, _ = RunService(database, clock=lambda: NOW).create(event_id)
    if pack_definition is not None:
        from packages.kernel.decision_hub_kernel.application.research_pack import (
            ResearchPackService,
        )

        ResearchPackService(database, clock=lambda: NOW).freeze(run_id, pack_definition)
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        run_id, "a" * 64, 1
    )
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash="a" * 64,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=NOW + timedelta(minutes=5),
        model_step_timeout_ms=150_000,
        max_tool_calls=max_tool_calls,
        generation=1,
    )
    DshSessionLinkService(database, clock=lambda: NOW).reserve(
        submit,
        DshUpstreamIdentity(
            source_commit="c" * 40,
            source_version="0.1.2",
            package_versions={"dsh": "0.1.2"},
            plugin_build_hash="b" * 64,
        ),
    )
    return database, run_id, session_id


def _manifest() -> ResearchCapabilityManifest:
    return ResearchCapabilityManifest.model_validate(
        {
            "schema_version": "research-capability-manifest.v1",
            "capability_id": "official.macro",
            "version": "1.0.0",
            "kind": "python_adapter",
            "implementation_ref": "adapter://official",
            "input_schema_ref": "research-capability-query.v1",
            "output_schema_ref": "research-capability-result.v1",
            "permissions": ["network:https"],
            "allowed_domains": ["federalreserve.gov"],
            "timeout_seconds": 20,
            "cost_policy_ref": "test.v1",
            "freshness_policy_ref": "test.v1",
            "license_status": "approved",
            "audit_status": "approved",
            "replay_policy": "archive_required",
            "secret_policy": "none",
        }
    )


def _requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="event_identity",
        description="Identify the official event.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["official.macro"],
        freshness_seconds=3600,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )


def _query(
    session_id: str, *, request_id: str = "call-1", round: int = 1
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": request_id,
            "capability_id": "official.macro",
            "requirement_id": "event_identity",
            "query": "official speech",
            "target_url": "https://www.federalreserve.gov/newsevents/speech/test.htm",
            "symbols": [],
            "fields": [],
            "allowed_domains": ["federalreserve.gov"],
            "max_results": 10,
            "max_cost_usd": 1,
            "research_session_id": session_id,
            "round": round,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=2),
        }
    )


def _candidate(
    session_id: str,
    *,
    evidence_id: str = "evidence-1",
    published_at: datetime | None = NOW - timedelta(minutes=1),
) -> EvidenceCandidate:
    excerpt = "The Committee remains attentive to inflation risks."
    source_url = "https://www.federalreserve.gov/newsevents/speech/test.htm"
    content_hash = research_evidence_content_hash(
        requirement_id="event_identity",
        kind="official",
        authority="official",
        source_id="federal-reserve",
        source_url=source_url,
        published_at=published_at,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate(
        evidence_id=evidence_id,
        requirement_id="event_identity",
        kind="official",
        authority="official",
        source_id="federal-reserve",
        source_url=AnyUrl(source_url),
        published_at=published_at,
        observed_at=NOW + timedelta(seconds=1),
        received_at=NOW + timedelta(seconds=1),
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id="call-1",
        research_session_id=session_id,
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )


class _Adapter:
    capability_id = "official.macro"
    supported_modes = frozenset({"live"})

    def __init__(
        self,
        session_id: str,
        *,
        fail: BaseException | None = None,
        published_at: datetime | None = NOW - timedelta(minutes=1),
    ) -> None:
        self.session_id = session_id
        self.fail = fail
        self.published_at = published_at
        self.call_count = 0

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.call_count += 1
        self.last_query = query
        if self.fail is not None:
            raise self.fail
        candidate = _candidate(self.session_id, published_at=self.published_at)
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="official-fixture",
            evidence_candidates=[candidate],
            cost_usd=0.01,
            completed_at=NOW + timedelta(seconds=2),
        )


class _BlockingAdapter(_Adapter):
    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.invocations = 0

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.invocations += 1
        if self.invocations == 1:
            self.entered.set()
            await self.release.wait()
        return await super().execute(query)


def _gateway(
    database: Database,
    session_id: str,
    *,
    adapter: _Adapter | None = None,
    requirement: EvidenceRequirement | None = None,
    event_watches: EventWatchService | None = None,
):
    inner = ResearchCapabilityGatewayService(
        [_manifest()],
        [adapter or _Adapter(session_id)],
        enabled_capabilities=["official.macro"],
        clock=lambda: NOW + timedelta(seconds=3),
    )
    return DurableResearchCapabilityGateway(
        inner,
        DshSessionLinkService(database, clock=lambda: NOW + timedelta(seconds=3)),
        ResearchEvidenceService(database),
        ResearchObservabilityService(database, clock=lambda: NOW + timedelta(seconds=3)),
        requirements={"event_identity": requirement or _requirement()},
        event_watches=event_watches,
        clock=lambda: NOW + timedelta(seconds=3),
    )


def _event_watch(database: Database) -> EventWatchService:
    service = EventWatchService(database, clock=lambda: NOW)
    service.ensure_watch(
        event_id="event-durable",
        source_id="calendar",
        event_family="central_bank_speech",
        scheduled_at=NOW + timedelta(minutes=30),
        window_offsets=("t-5m", "t+1m"),
    )
    return service


@pytest.mark.asyncio
async def test_frozen_run_scope_denies_globally_enabled_capability_before_adapter(tmp_path):
    from packages.kernel.decision_hub_kernel.application.research_method import (
        ResearchMethodService,
    )

    database, run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    ResearchMethodService(database).freeze(
        run_id, {"allowed_capabilities": ["web.fetch"]}, request_hash="a" * 64
    )
    with pytest.raises(ResearchCapabilityError) as denied:
        await gateway.execute(_query(session_id))
    assert denied.value.error_code == "research_capability_not_allowed_for_run"
    assert adapter.call_count == 0
    assert database.get_run_record(run_id).status != "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["mode", "request_hash", "missing_pack", "pack_hash", "corrupt_pack"]
)
async def test_run_scope_integrity_rejects_before_provider(tmp_path, case):
    from packages.kernel.decision_hub_kernel.application.research_method import (
        ResearchMethodService,
    )
    from packages.kernel.decision_hub_kernel.application.research_pack import (
        ResearchPackService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

    database, run_id, session_id = _database(
        tmp_path,
        pack_definition={"scope": "test"} if case in {"pack_hash", "corrupt_pack"} else None,
    )
    definition = {"allowed_capabilities": ["official.macro"], "execution_mode": "live"}
    if case in {"missing_pack", "pack_hash", "corrupt_pack"}:
        pack = ResearchPackService(database).get(run_id)
        definition["request_pack_hash"] = pack["definition_hash"] if pack else "f" * 64
        if case == "pack_hash":
            definition["request_pack_hash"] = "f" * 64
        elif case == "corrupt_pack":
            with database.session() as session:
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=run_id, role="research_request_pack"
                ).one()
                row.payload_json = "{}"
    ResearchMethodService(database).freeze(
        run_id, definition, request_hash=("b" if case == "request_hash" else "a") * 64
    )
    adapter = _Adapter(session_id)
    query = _query(session_id)
    if case == "mode":
        query = query.model_copy(update={"mode": "replay"})
    with pytest.raises(ResearchCapabilityError) as denied:
        await _gateway(database, session_id, adapter=adapter).execute(query)
    expected = (
        "research_capability_not_allowed_for_run" if case == "mode"
        else "research_method_receipt_invalid"
    )
    assert denied.value.error_code == expected
    assert adapter.call_count == 0
    trace = ResearchObservabilityService(database).list_trace(run_id)
    assert trace[-1].event_type == "tool_failed"
    assert trace[-1].error_code == expected


def test_valid_bound_pack_and_method_pass_session_validation(tmp_path):
    from packages.kernel.decision_hub_kernel.application.research_method import (
        ResearchMethodService,
    )
    from packages.kernel.decision_hub_kernel.application.research_pack import (
        ResearchPackService,
    )

    database, run_id, session_id = _database(tmp_path, pack_definition={"scope": "test"})
    pack = ResearchPackService(database).get(run_id)
    ResearchMethodService(database).freeze(
        run_id, {
            "allowed_capabilities": ["official.macro"], "execution_mode": "live",
            "request_pack_hash": pack["definition_hash"],
        }, request_hash="a" * 64,
    )
    validated, _, _ = _gateway(database, session_id)._validate_session(_query(session_id))
    assert validated == run_id


@pytest.mark.asyncio
async def test_valid_run_scope_allows_adapter_but_corrupt_receipt_does_not(tmp_path):
    from packages.kernel.decision_hub_kernel.application.research_method import (
        ResearchMethodService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

    database, run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    ResearchMethodService(database).freeze(
        run_id, {"allowed_capabilities": ["official.macro"]}, request_hash="a" * 64
    )
    await gateway.execute(_query(session_id))
    assert adapter.call_count == 1
    with database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=run_id, role="research_execution_method"
        ).one()
        row.payload_json = "{}"
    with pytest.raises(ResearchCapabilityError) as denied:
        await gateway.execute(_query(session_id).model_copy(update={"request_id": "another"}))
    assert denied.value.error_code == "research_method_receipt_invalid"
    assert adapter.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"capability_id": "data.historical"},
    {"requirement_id": "macro_transmission"},
    {"query": "a different source lookup"},
    {"symbols": ["BTC-USDT"]},
])
async def test_reused_call_id_with_changed_query_never_replays_cached_data(
    tmp_path: Path, changed: dict[str, object],
) -> None:
    database, run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    query = _query(session_id)
    first = await gateway.execute(query)
    with pytest.raises(ResearchCapabilityError) as caught:
        await gateway.execute(query.model_copy(update=changed))
    assert caught.value.error_code == "research_tool_request_conflict"
    assert await gateway.execute(query) == first
    assert adapter.call_count == 1
    assert ResearchObservabilityService(database).tool_calls_started(run_id) == 1


@pytest.mark.asyncio
async def test_success_is_persisted_before_result_returns(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)
    result = await _gateway(database, session_id).execute(_query(session_id))

    assert result.evidence_candidates[0].evidence_id == "evidence-1"
    assert len(ResearchEvidenceService(database).list_run_evidence(run_id)) == 1
    events = ResearchObservabilityService(database).list_trace(run_id)
    assert [item.event_type for item in events] == ["tool_started", "tool_completed"]
    assert all(item.reference_id == "call-1" for item in events)


@pytest.mark.asyncio
async def test_live_freshness_uses_trusted_completion_not_future_run_ceiling(
    tmp_path: Path,
) -> None:
    """A future bounded deadline must not age a newly received live fact."""

    database, run_id, session_id = _database(tmp_path)
    requirement = _requirement().model_copy(update={"freshness_seconds": 5})

    result = await _gateway(
        database,
        session_id,
        adapter=_Adapter(session_id, published_at=NOW),
        requirement=requirement,
    ).execute(_query(session_id))

    assert result.completed_at == NOW + timedelta(seconds=3)
    persisted = ResearchEvidenceService(database).list_run_evidence(run_id)
    assert len(persisted) == 1
    assert persisted[0].quality == "accepted"
    assert persisted[0].freshness_status == "fresh"


@pytest.mark.asyncio
async def test_persistence_resolves_event_family_freshness_policy(tmp_path: Path) -> None:
    """Daily snapshot evidence must not inherit the event-window 10-minute TTL."""

    database, run_id, session_id = _database(
        tmp_path, event_hint="crypto_market_snapshot"
    )
    static_requirement = _requirement().model_copy(update={"freshness_seconds": 600})
    snapshot_requirement = static_requirement.model_copy(
        update={"freshness_seconds": 3 * 24 * 60 * 60}
    )
    resolved_families: list[str | None] = []

    def resolve(event_family: str | None) -> dict[str, EvidenceRequirement]:
        resolved_families.append(event_family)
        return {"event_identity": snapshot_requirement}

    gateway = _gateway(
        database,
        session_id,
        adapter=_Adapter(session_id, published_at=NOW - timedelta(days=1)),
        requirement=static_requirement,
    )
    gateway.requirement_resolver = resolve

    await gateway.execute(_query(session_id))

    persisted = ResearchEvidenceService(database).list_run_evidence(run_id)
    assert resolved_families == ["crypto_market_snapshot"]
    assert len(persisted) == 1
    assert persisted[0].quality == "accepted"
    assert persisted[0].freshness_status == "fresh"


@pytest.mark.asyncio
async def test_live_cutoff_is_clamped_to_durable_dsh_deadline(tmp_path: Path) -> None:
    """A model-supplied future cutoff cannot extend the accepted Run window."""

    database, _run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id, published_at=None)
    requested_cutoff = NOW + timedelta(days=365)
    query = _query(session_id).model_copy(update={"cutoff_at": requested_cutoff})

    await _gateway(database, session_id, adapter=adapter).execute(query)

    assert adapter.last_query.cutoff_at == NOW + timedelta(minutes=5)
    assert adapter.last_query.deadline_at == NOW + timedelta(minutes=5)
    assert adapter.last_query.cutoff_at < requested_cutoff


@pytest.mark.asyncio
async def test_event_query_projects_server_owned_watch_lineage(tmp_path: Path) -> None:
    database, _run_id, session_id = _database(tmp_path)
    event_watches = _event_watch(database)
    adapter = _Adapter(session_id)
    watch = event_watches.get_watch_by_event_id("event-durable")
    assert watch is not None

    query = _query(session_id).model_copy(
        update={
            "event_id": "event-durable",
            "event_at": watch.scheduled_at,
            "window_start_at": NOW + timedelta(minutes=25),
            "window_end_at": NOW + timedelta(minutes=31),
            "requested_event_offsets": ["t-5m", "t+1m"],
        }
    )
    await _gateway(
        database, session_id, adapter=adapter, event_watches=event_watches
    ).execute(query)

    assert adapter.last_query.event_id == "event-durable"
    assert adapter.last_query.event_at == watch.scheduled_at
    assert adapter.last_query.window_start_at == NOW + timedelta(minutes=25)
    assert adapter.last_query.window_end_at == NOW + timedelta(minutes=31)


@pytest.mark.asyncio
async def test_event_id_mismatch_fails_before_provider_execution(tmp_path: Path) -> None:
    database, _run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)

    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway(database, session_id, adapter=adapter).execute(
            _query(session_id).model_copy(
                update={
                    "event_id": "another-event",
                    "requested_event_offsets": ["t-5m", "t+1m"],
                }
            )
        )

    assert raised.value.error_code == "research_event_lineage_mismatch"
    assert adapter.call_count == 0


@pytest.mark.asyncio
async def test_event_query_requires_durable_watch_and_valid_offsets(tmp_path: Path) -> None:
    database, _run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    query = _query(session_id).model_copy(
        update={
            "event_id": "event-durable",
            "requested_event_offsets": ["t-5m", "t+1m"],
        }
    )

    with pytest.raises(ResearchCapabilityError) as missing:
        await _gateway(database, session_id, adapter=adapter).execute(query)
    assert missing.value.error_code == "research_event_watch_not_found"
    assert adapter.call_count == 0

    event_watches = _event_watch(database)
    with pytest.raises(ResearchCapabilityError) as invalid:
        await _gateway(
            database, session_id, adapter=adapter, event_watches=event_watches
        ).execute(
            query.model_copy(update={"requested_event_offsets": ["t+72h"]})
        )
    assert invalid.value.error_code == "research_event_window_offset_invalid"
    assert adapter.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("event_at", NOW + timedelta(minutes=29)),
        ("window_start_at", NOW + timedelta(minutes=24)),
        ("window_end_at", NOW + timedelta(minutes=32)),
    ),
)
async def test_event_query_rejects_model_supplied_time_conflicts(
    tmp_path: Path, field: str, value: datetime
) -> None:
    database, _run_id, session_id = _database(tmp_path)
    event_watches = _event_watch(database)
    adapter = _Adapter(session_id)
    query = _query(session_id).model_copy(
        update={
            "event_id": "event-durable",
            "requested_event_offsets": ["t-5m", "t+1m"],
            field: value,
        }
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway(
            database, session_id, adapter=adapter, event_watches=event_watches
        ).execute(query)

    assert raised.value.error_code == "research_event_time_mismatch"
    assert adapter.call_count == 0


@pytest.mark.asyncio
async def test_failure_persists_provenance_and_is_retryable(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)
    attempt = ProviderAttempt.model_validate(
        {
            "provider_id": "official-fixture",
            "route_role": "primary",
            "service_tier": "replay",
            "status": "failed",
            "started_at": NOW,
            "finished_at": NOW + timedelta(seconds=1),
            "latency_ms": 1000,
            "cost_usd": 0.01,
            "error_code": "provider_timeout",
            "retryable": True,
        }
    )
    adapter = _Adapter(
        session_id,
        fail=ResearchCapabilityError(
            "research_capability_timeout",
            "timed out",
            retryable=True,
            origin="transport",
            cause_code="deadline",
            deadline_ms=20_000,
            provider_attempts=[attempt],
        ),
    )
    with pytest.raises(ResearchCapabilityError) as first_failure:
        await _gateway(database, session_id, adapter=adapter).execute(_query(session_id))
    assert first_failure.value.provenance().provider_attempts == [attempt]

    trace = ResearchObservabilityService(database).list_trace(run_id)
    assert trace[-1].event_type == "tool_failed"
    assert trace[-1].error is not None
    assert trace[-1].error.origin == "transport"
    assert trace[-1].error.retryable is True
    assert trace[-1].error.provider_attempts is not None
    assert trace[-1].error.provider_attempts[0].provider_id == "official-fixture"
    assert ResearchEvidenceService(database).list_run_evidence(run_id) == []

    # A retry after process recovery reads the durable error projection and
    # must retain the exact provider-attempt lineage rather than collapsing it
    # to a generic capability failure.
    with pytest.raises(ResearchCapabilityError) as recovered_failure:
        await _gateway(database, session_id, adapter=_Adapter(session_id)).execute(
            _query(session_id)
        )
    assert recovered_failure.value.error_code == "research_capability_timeout"
    assert recovered_failure.value.provider_attempts == (attempt,)


@pytest.mark.asyncio
async def test_evidence_and_facts_roll_back_as_one_unit(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)

    class _FactLineageFailureAdapter(_Adapter):
        async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
            result = await super().execute(query)
            candidate = result.evidence_candidates[0]
            fact = FactEnvelope(
                schema_version="fact-envelope.v1",
                fact_id="fact-invalid-lineage",
                evidence_id=candidate.evidence_id,
                requirement_id=candidate.requirement_id,
                metric_family="macro.event",
                field="signal",
                instrument=None,
                venue=None,
                value="candidate",
                unit="text",
                window_start_at=None,
                window_end_at=None,
                event_offset=None,
                observed_at=candidate.observed_at,
                received_at=candidate.received_at,
                published_at=candidate.published_at,
                source_id="wrong-source",
                independence_group="wrong-source",
                quality="candidate",
                delay_class="realtime",
                payload_schema_ref="test.fact.v1",
                payload_hash="f" * 64,
                attributes={},
            )
            return result.model_copy(update={"facts": [fact]})

    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway(
            database, session_id, adapter=_FactLineageFailureAdapter(session_id)
        ).execute(_query(session_id))

    assert raised.value.error_code == "research_fact_lineage_mismatch"
    assert ResearchEvidenceService(database).list_run_evidence(run_id) == []
    assert ResearchFactStore(database).list_run_facts(run_id) == []


@pytest.mark.asyncio
async def test_cancelled_error_is_control_flow_not_provider_failure(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await _gateway(
            database,
            session_id,
            adapter=_Adapter(session_id, fail=asyncio.CancelledError()),
        ).execute(_query(session_id))
    assert (
        ResearchObservabilityService(database).list_trace(run_id)[-1].event_type
        == "tool_started"
    )


@pytest.mark.asyncio
async def test_unknown_session_and_round_is_canonicalized_to_durable_generation(
    tmp_path: Path,
) -> None:
    database, _run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    with pytest.raises(ResearchCapabilityError) as missing:
        await gateway.execute(_query("unknown-session"))
    assert missing.value.error_code == "research_session_not_found"

    result = await gateway.execute(_query(session_id, round=2))
    assert result.evidence_candidates[0].round == 1
    assert adapter.last_query.round == 1

    link = DshSessionLinkService(database).get_by_session(session_id)
    assert link is not None
    DshSessionLinkService(database).complete(
        DshSessionCompletion(
            schema_version="dsh-session-completion.v1",
            run_id=link.run_id,
            dsh_session_id=session_id,
            terminal_status="failed",
            generation=1,
            last_seq=4,
            trace_ref=None,
            result_ref=None,
            result_hash=None,
            completed_at=NOW + timedelta(seconds=4),
            error=DshBridgeError(code="failed", message="failed", retryable=True),
        )
    )
    with pytest.raises(ResearchCapabilityError) as terminal:
        await gateway.execute(_query(session_id))
    assert terminal.value.error_code == "research_session_terminal"
    assert ResearchObservabilityService(database).list_trace(_run_id)[-1].error_code == (
        "research_session_terminal"
    )


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_content_conflict_fails_closed(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    query = _query(session_id)
    await gateway.execute(query)
    await gateway.execute(query)
    assert adapter.call_count == 1
    assert len(ResearchEvidenceService(database).list_run_evidence(run_id)) == 1
    assert len(ResearchObservabilityService(database).list_trace(run_id)) == 2

    class _ConflictAdapter(_Adapter):
        async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
            candidate = _candidate(self.session_id)
            candidate = candidate.model_copy(update={"excerpt": "different content"})
            return ResearchCapabilityResult(
                schema_version="research-capability-result.v1",
                request_id=query.request_id,
                capability_id=query.capability_id,
                provider="official-fixture",
                evidence_candidates=[candidate],
                cost_usd=0,
                completed_at=NOW + timedelta(seconds=2),
            )

    with pytest.raises(ResearchCapabilityError) as conflict:
        await _gateway(
            database, session_id, adapter=_ConflictAdapter(session_id)
        ).execute(_query(session_id, request_id="call-conflict"))
    assert conflict.value.error_code == "research_content_hash_mismatch"


@pytest.mark.asyncio
async def test_tool_budget_rejects_before_adapter_and_persists_provenance(
    tmp_path: Path,
) -> None:
    database, run_id, session_id = _database(tmp_path, max_tool_calls=1)
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)

    await gateway.execute(_query(session_id, request_id="call-1"))
    with pytest.raises(ResearchCapabilityError) as exhausted:
        await gateway.execute(_query(session_id, request_id="call-2"))

    assert exhausted.value.error_code == "research_tool_budget_exhausted"
    assert exhausted.value.origin == "gateway"
    assert adapter.call_count == 1
    trace = ResearchObservabilityService(database).list_trace(run_id)
    assert trace[-1].event_type == "tool_failed"
    assert trace[-1].error_code == "research_tool_budget_exhausted"
    assert trace[-1].error is not None
    assert trace[-1].error.tool_call_id == "call-2"


@pytest.mark.asyncio
async def test_concurrent_calls_cannot_share_the_last_tool_slot(tmp_path: Path) -> None:
    database, _run_id, session_id = _database(tmp_path, max_tool_calls=1)
    adapter = _BlockingAdapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    first = asyncio.create_task(
        gateway.execute(_query(session_id, request_id="call-first"))
    )
    await adapter.entered.wait()
    try:
        with pytest.raises(ResearchCapabilityError) as exhausted:
            await gateway.execute(_query(session_id, request_id="call-second"))
        assert exhausted.value.error_code == "research_tool_budget_exhausted"
    finally:
        adapter.release.set()
    await first
    assert adapter.call_count == 1


@pytest.mark.asyncio
async def test_separate_database_engines_cannot_share_the_last_tool_slot(
    tmp_path: Path,
) -> None:
    database, _run_id, session_id = _database(tmp_path, max_tool_calls=1)
    second_database = Database(
        f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}"
    )
    first_adapter = _BlockingAdapter(session_id)
    second_adapter = _Adapter(session_id)
    first_gateway = _gateway(database, session_id, adapter=first_adapter)
    second_gateway = _gateway(
        second_database, session_id, adapter=second_adapter
    )

    first = asyncio.create_task(
        first_gateway.execute(_query(session_id, request_id="call-engine-1"))
    )
    await first_adapter.entered.wait()
    try:
        with pytest.raises(ResearchCapabilityError) as exhausted:
            await second_gateway.execute(
                _query(session_id, request_id="call-engine-2")
            )
        assert exhausted.value.error_code == "research_tool_budget_exhausted"
    finally:
        first_adapter.release.set()
    await first
    assert first_adapter.call_count == 1
    assert second_adapter.call_count == 0


def _cancel_command(request_id: str = "owner-cancel-1") -> ResearchRunCommand:
    return ResearchRunCommand(
        schema_version="research-run-command.v1", request_id=request_id,
        command="cancel", reason="Owner stopped this research.",
    )


def test_cancel_commits_before_waiting_reservation_never_starts_adapter(tmp_path: Path) -> None:
    """Both engines use the real SQLite lock after the gateway's first state read."""
    database, run_id, session_id = _database(tmp_path)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.strategy_version = "research.v1"
    cancellation_database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    cancel_has_write_lock, release_cancel, reservation_requested = Event(), Event(), Event()
    adapter = _Adapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    reserve = gateway.observability.reserve_tool_call

    def cancellation_clock() -> datetime:
        cancel_has_write_lock.set()
        assert release_cancel.wait(4), "cancel transaction was not released"
        return NOW + timedelta(seconds=1)

    def waiting_reservation(
        *, run_id: str, request_id: str, research_session_id: str, generation: int,
        capability_id: str, query_hash: str,
    ) -> ToolCallReservation:
        reservation_requested.set()
        return reserve(
            run_id=run_id, request_id=request_id, research_session_id=research_session_id,
            generation=generation, capability_id=capability_id, query_hash=query_hash,
        )

    gateway.observability.reserve_tool_call = waiting_reservation
    cancellation = ResearchCommandService(cancellation_database, clock=cancellation_clock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        cancelling = workers.submit(cancellation.apply, run_id, _cancel_command())
        assert cancel_has_write_lock.wait(4)
        executing = workers.submit(asyncio.run, gateway.execute(_query(session_id)))
        try:
            assert reservation_requested.wait(4)
            assert adapter.call_count == 0
        finally:
            release_cancel.set()
        assert cancelling.result(timeout=4).status == "accepted"
        with pytest.raises(ResearchCapabilityError) as rejected:
            executing.result(timeout=4)
    assert rejected.value.error_code == "research_run_terminal"
    assert adapter.call_count == 0
    assert ResearchObservabilityService(database).tool_calls_started(run_id) == 0
    assert ResearchEvidenceService(database).list_run_evidence(run_id) == []
    with database.session() as session:
        assert session.query(ResearchToolCallReservationRecord).count() == 0
    assert ResearchObservabilityService(database).list_trace(run_id)[-1].error_code == (
        "research_run_terminal"
    )


class _ThreadControlledCaptureAdapter(_Adapter):
    """A locally controlled adapter exercises real durable capture and fact writes."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.entered, self.release = Event(), Event()
        self.raw_body = b"The Committee remains attentive to inflation risks."

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.entered.set()
        assert await asyncio.to_thread(self.release.wait, 4), "adapter was not released"
        result = await super().execute(query)
        batch = CURRENT_CAPTURE.get()
        assert batch is not None
        batch.reuse_key = "e" * 64
        batch.record(
            url=str(query.target_url), started_at=NOW,
            completed_at=NOW + timedelta(seconds=2),
            response=httpx.Response(
                200, content=self.raw_body, headers={"content-type": "text/plain"},
            ),
        )
        candidate = result.evidence_candidates[0]
        fact = FactEnvelope(
            schema_version="fact-envelope.v1", fact_id="fact-cancel-retained",
            evidence_id=candidate.evidence_id, requirement_id=candidate.requirement_id,
            metric_family="macro.event", field="signal", instrument=None, venue=None,
            value="inflation risks", unit="text", window_start_at=None, window_end_at=None,
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


@pytest.mark.parametrize("has_receipt", [True, False])
def test_reserved_inflight_result_retains_material_cost_and_cancellation_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_receipt: bool,
) -> None:
    database, run_id, session_id = _database(tmp_path)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.strategy_version = "research.v1"
    cancellation_database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    adapter = _ThreadControlledCaptureAdapter(session_id)
    gateway = _gateway(database, session_id, adapter=adapter)
    gateway.raw_archive = RawBodyArchive(tmp_path / "originals")
    monkeypatch.setattr(
        "packages.workbench_adapters.research_reuse.utcnow",
        lambda: NOW + timedelta(seconds=3),
    )
    cancel_at = NOW + timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=2) as workers:
        executing = workers.submit(asyncio.run, gateway.execute(_query(session_id)))
        try:
            assert adapter.entered.wait(4), "adapter never actually entered"
            with cancellation_database.session() as session:
                reservation = session.get(ResearchToolCallReservationRecord, (run_id, "call-1"))
                assert reservation is not None and reservation.status == "running"
            if has_receipt:
                cancellation = ResearchCommandService(
                    cancellation_database, clock=lambda: cancel_at,
                )
                assert workers.submit(
                    cancellation.apply, run_id, _cancel_command(),
                ).result(timeout=4).status == "accepted"
            else:
                with cancellation_database.session() as session:
                    run = session.get(RunRecord, run_id)
                    assert run is not None
                    run.status = "cancelled"
                    run.finished_at = cancel_at
            # An unrelated mutable Run update must never become the original cutoff.
            with cancellation_database.session() as session:
                run = session.get(RunRecord, run_id)
                assert run is not None
                run.updated_at = NOW + timedelta(hours=1)
        finally:
            adapter.release.set()
        result = executing.result(timeout=4)
    assert adapter.call_count == 1 and result.cost_usd == 0.01
    assert ResearchObservabilityService(database).tool_calls_started(run_id) == 1
    assert len(result.evidence_candidates) == len(result.facts or []) == 1
    assert len(ResearchEvidenceService(database).list_run_evidence(run_id)) == 1
    assert len(ResearchFactStore(database).list_run_facts(run_id)) == 1
    with database.session() as session:
        reservation = session.get(ResearchToolCallReservationRecord, (run_id, "call-1"))
        assert reservation is not None and reservation.result_json is not None
        assert reservation.status == "completed" and reservation.error_json is None
        assert ResearchCapabilityResult.model_validate_json(reservation.result_json) == result
        capture = session.query(RawCaptureRecord).one()
        assert capture.body_ref is not None
        assert gateway.raw_archive.read(capture.body_ref) == adapter.raw_body
        assert session.query(CapabilityReuseRecord).count() == 0
        run = session.get(RunRecord, run_id)
        assert run is not None and run.status == "cancelled"
    trace = ResearchObservabilityService(database).list_trace(run_id)
    completed = [item for item in trace if item.event_type == "tool_completed"]
    assert len(completed) == 1 and completed[0].status == "succeeded"
    assert completed[0].error is None and completed[0].error_code is None
    audit = [item for item in trace if item.reference_type == "cancellation_cutoff"]
    assert len(audit) == 1 and audit[0].status == "succeeded"
    assert "call-1" in audit[0].summary and "reusable" in audit[0].summary
    if has_receipt:
        assert "owner-cancel-1" in audit[0].summary
        assert cancel_at.isoformat() in audit[0].summary
        assert "unknown" not in audit[0].summary
    else:
        assert "unknown" in audit[0].summary
        assert cancel_at.isoformat() not in audit[0].summary


@pytest.mark.parametrize("changed", ["state", "session", "generation"])
def test_atomic_reservation_rechecks_durable_link_binding(tmp_path: Path, changed: str) -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord

    database, run_id, session_id = _database(tmp_path)
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        assert link is not None
        if changed == "state":
            link.state = "cancelled"
        elif changed == "session":
            link.dsh_session_id = "replacement-session"
        else:
            link.generation = 2
    service = ResearchObservabilityService(database)
    reservation = service.reserve_tool_call(
        run_id=run_id, request_id="call-1", research_session_id=session_id,
        generation=1, capability_id="official.macro", query_hash="f" * 64,
    )
    assert reservation.outcome == "denied" and reservation.error is not None
    assert reservation.error.error_code == (
        "research_session_terminal" if changed == "state" else "research_session_binding_mismatch"
    )
    assert service.tool_calls_started(run_id) == 0
    with database.session() as session:
        assert session.query(ResearchToolCallReservationRecord).count() == 0


@pytest.mark.asyncio
async def test_completed_reservation_keeps_original_result_after_run_cancellation(
    tmp_path: Path,
) -> None:
    database, run_id, session_id = _database(tmp_path)
    adapter = _Adapter(session_id)
    result = await _gateway(database, session_id, adapter=adapter).execute(_query(session_id))
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "cancelled"
        original = session.get(ResearchToolCallReservationRecord, (run_id, "call-1"))
        assert original is not None and original.query_hash is not None
        query_hash = original.query_hash
    reservation = ResearchObservabilityService(database).reserve_tool_call(
        run_id=run_id, request_id="call-1", research_session_id=session_id,
        generation=1, capability_id="official.macro", query_hash=query_hash,
    )
    assert reservation.outcome == "completed" and reservation.result == result
    assert adapter.call_count == 1


@pytest.mark.parametrize("corrupt_original", [False, True])
def test_cutoff_uses_original_verified_cancel_and_never_a_later_replay(
    tmp_path: Path, corrupt_original: bool,
) -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import ProductCommandRecord

    database, run_id, _session_id = _database(tmp_path)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.strategy_version = "research.v1"
    original_at = NOW + timedelta(seconds=1)
    for request_id, created_at in (
        ("original-cancel", original_at),
        ("later-cancel", NOW + timedelta(seconds=2)),
    ):
        service = ResearchCommandService(database, clock=lambda created_at=created_at: created_at)
        assert service.apply(run_id, _cancel_command(request_id)).status == "accepted"
    with database.session() as session:
        if corrupt_original:
            receipt = session.get(ProductCommandRecord, "original-cancel")
            assert receipt is not None
            receipt.result_json = "{}"
        cutoff = ResearchCommandService.cancellation_cutoff_in_session(session, run_id)
        assert cutoff is not None
        if corrupt_original:
            assert cutoff.request_id is None and cutoff.created_at is None
        else:
            assert cutoff.request_id == "original-cancel" and cutoff.created_at == original_at


def _controlled_trace(
    run_id: str, session_id: str, *, occurred_at: datetime,
    reference_id: str = "raced-call", reference_type: str | None = "capability_call",
) -> ResearchTraceEvent:
    return ResearchTraceEvent(
        schema_version="research-trace-event.v1", run_id=run_id,
        research_session_id=session_id, sequence_no=0, event_type="tool_started",
        occurred_at=occurred_at, stage="acquiring_evidence", summary="Controlled call started.",
        reference_type=reference_type, reference_id=reference_id, status="running",
        error_code=None, error=None,
    )


def test_trace_once_two_engines_deduplicate_inside_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, run_id, session_id = _database(tmp_path)
    second_database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    first_service, second_service = (
        ResearchObservabilityService(database), ResearchObservabilityService(second_database),
    )
    # Preserve legitimate existing hash/sequence and historical repeated references.
    for seconds in (0, 1):
        first_service.append_trace(_controlled_trace(
            run_id, session_id, occurred_at=NOW + timedelta(seconds=seconds),
            reference_id="historical-reference",
        ))
    with database.session() as session:
        originals = [
            {
                column.name: getattr(row, column.name)
                for column in ResearchTraceRecord.__table__.columns
            }
            for row in session.query(ResearchTraceRecord).order_by(ResearchTraceRecord.sequence_no)
        ]
    ready_to_write = Barrier(2)

    def install_boundary(service: ResearchObservabilityService) -> None:
        append = service._append_trace_locked  # pyright: ignore[reportPrivateUsage]

        def simultaneous_append(
            event: ResearchTraceEvent, *, once: bool = False,
        ) -> ResearchTraceEvent:
            ready_to_write.wait(timeout=4)
            # Both old readers have missed, or both new callers are about to lock.
            return append(event, once=True) if once else append(event)

        monkeypatch.setattr(service, "_append_trace_locked", simultaneous_append)

    install_boundary(first_service)
    install_boundary(second_service)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(service.append_trace_once, _controlled_trace(
                run_id, session_id, occurred_at=NOW + timedelta(seconds=seconds),
            ))
            for service, seconds in ((first_service, 2), (second_service, 3))
        ]
        returned = [future.result(timeout=4) for future in futures]
    assert returned[0] == returned[1]
    assert returned[0].sequence_no == 3
    historical_replay = ResearchObservabilityService(second_database).append_trace_once(
        _controlled_trace(
            run_id, session_id, occurred_at=NOW + timedelta(seconds=4),
            reference_id="historical-reference",
        ).model_copy(update={"summary": "A replay must preserve the original."}),
    )
    assert historical_replay.sequence_no == 1
    next_event = ResearchObservabilityService(second_database).append_trace(
        _controlled_trace(
            run_id, session_id, occurred_at=NOW + timedelta(seconds=5), reference_id="next-call",
        ),
    )
    assert next_event.sequence_no == 4
    with database.session() as session:
        rows = session.query(ResearchTraceRecord).order_by(ResearchTraceRecord.sequence_no).all()
        assert [
            {
                column.name: getattr(row, column.name)
                for column in ResearchTraceRecord.__table__.columns
            }
            for row in rows[:2]
        ] == originals
        assert len(rows) == 4
        assert rows[2].reference_id == "raced-call"
        assert [row.sequence_no for row in rows] == [1, 2, 3, 4]


@pytest.mark.parametrize("second_type", ["run", None])
def test_trace_once_keeps_reference_types_distinct(
    tmp_path: Path, second_type: str | None,
) -> None:
    database, run_id, session_id = _database(tmp_path)
    service = ResearchObservabilityService(database)
    first = service.append_trace_once(_controlled_trace(run_id, session_id, occurred_at=NOW))
    second = service.append_trace_once(_controlled_trace(
        run_id, session_id, occurred_at=NOW + timedelta(seconds=1), reference_type=second_type,
    ))
    assert first.sequence_no == 1 and second.sequence_no == 2
    for original in (first, second):
        replay = service.append_trace_once(original.model_copy(update={
            "occurred_at": NOW + timedelta(seconds=5), "summary": "Repeat the same namespace.",
        }))
        assert replay == original
    assert len(service.list_trace(run_id)) == 2


@pytest.mark.asyncio
async def test_duplicate_request_across_engines_still_starts_one_adapter(tmp_path: Path) -> None:
    database, run_id, session_id = _database(tmp_path)
    second_database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    first_adapter, second_adapter = _BlockingAdapter(session_id), _Adapter(session_id)
    first_gateway = _gateway(database, session_id, adapter=first_adapter)
    second_gateway = _gateway(second_database, session_id, adapter=second_adapter)
    query = _query(session_id)
    first = asyncio.create_task(first_gateway.execute(query))
    await first_adapter.entered.wait()
    try:
        with pytest.raises(ResearchCapabilityError) as duplicate:
            await second_gateway.execute(query)
        assert duplicate.value.error_code == "research_tool_call_in_progress"
    finally:
        first_adapter.release.set()
    result = await first
    assert await second_gateway.execute(query) == result
    assert first_adapter.call_count == 1 and second_adapter.call_count == 0
    assert ResearchObservabilityService(database).tool_calls_started(run_id) == 1
    trace = ResearchObservabilityService(database).list_trace(run_id)
    assert [row.event_type for row in trace] == ["tool_started", "tool_completed"]
