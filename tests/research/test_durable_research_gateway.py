from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionCompletion,
    DshSessionSubmit,
    DshUpstreamIdentity,
    EvidenceCandidate,
    EvidenceRequirement,
    ProviderAttempt,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway

NOW = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)


def _database(
    tmp_path: Path, *, max_tool_calls: int = 12
) -> tuple[Database, str, str]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite3'}")
    database.create_all()
    run_id, _ = RunService(database, clock=lambda: NOW).create("event-durable")
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
        adapter=_Adapter(session_id, published_at=None),
        requirement=requirement,
    ).execute(_query(session_id))

    assert result.completed_at == NOW + timedelta(seconds=3)
    persisted = ResearchEvidenceService(database).list_run_evidence(run_id)
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
async def test_unknown_or_terminal_session_and_round_fail_closed(tmp_path: Path) -> None:
    database, _run_id, session_id = _database(tmp_path)
    gateway = _gateway(database, session_id)
    with pytest.raises(ResearchCapabilityError) as missing:
        await gateway.execute(_query("unknown-session"))
    assert missing.value.error_code == "research_session_not_found"

    with pytest.raises(ResearchCapabilityError) as wrong_round:
        await gateway.execute(_query(session_id, round=2))
    assert wrong_round.value.error_code == "research_round_mismatch"

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
